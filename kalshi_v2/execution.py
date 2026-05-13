"""Order execution + position management.

Live orders go through kalshi_live.place_order with:
  - Limit type (Kalshi rejects raw 'market'); aggressive price (best ask + buffer)
  - 30 s expiration so unfilled orders auto-cancel
  - Pre-fetch the live orderbook from kalshi_md and price relative to current quotes

Position management runs every cycle: stop-loss, take-profit, time-exit,
max-age. Resolution closes are deferred to check_settlements (single source
of truth for who-won-the-bet).
"""
from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from dateutil import parser as dtparser

from .config import CFG, LIVE_OR_SHADOW
from .client import KalshiClient
from .state import BOOKS, LOCK
from .paper_db import _conn, settle_trade, open_trades, record_trade


TERMINAL_STATUSES = {"settled", "finalized", "determined", "resolved", "closed"}


# ════════════════════════════════════════════════════════════════════════
#  Smart limit-order placement
# ════════════════════════════════════════════════════════════════════════
def place_smart_limit(kalshi_live: KalshiClient, kalshi_md: KalshiClient,
                       ticker: str, side: str, action: str, count: int,
                       expiration_sec: int = None) -> dict:
    """Place an aggressive limit order via kalshi_live.

    Quote resolution order (fastest → freshest fallback):
      1. In-memory BOOKS (WS-driven, sub-second). Skipped if stale > 5s.
      2. kalshi_md.get_market(ticker) parsed via parse_market_fields
         (handles BOTH legacy 'yes_bid'/'yes_ask' cent ints AND the
         2026 'yes_bid_dollars'/'yes_ask_dollars' string fields that
         the single-market endpoint returns).
      3. Raise with full Kalshi response body on 4xx.

    Then price = current_ask + buffer (for buys) / current_bid - buffer (sells),
    with expiration_ts = now + 30s.
    """
    from .client import parse_market_fields
    if kalshi_live is None:
        raise RuntimeError("kalshi_live not initialized")
    expiration_sec = expiration_sec or CFG["order_expiration_sec"]

    # 1. Try in-memory WS state (freshest).
    yb = ya = None
    with LOCK:
        b = BOOKS.get(ticker)
    if b and b.get("ts"):
        age = (datetime.now(timezone.utc) - b["ts"]).total_seconds()
        if age < 5 and b.get("yes_bid") is not None and b.get("yes_ask") is not None:
            yb, ya = float(b["yes_bid"]), float(b["yes_ask"])

    # 2. Fall back to REST single-market lookup, parsed for both field formats.
    if yb is None or ya is None:
        try:
            m  = kalshi_md.get_market(ticker).get("market", {})
            pf = parse_market_fields(m)
            yb = pf.get("yes_bid"); ya = pf.get("yes_ask")
        except Exception as e:
            raise RuntimeError(f"REST market fetch failed for {ticker}: {e}")

    if yb is None or ya is None:
        raise RuntimeError(f"no live quotes on {ticker}")
    yb, ya = float(yb), float(ya)

    buffer = CFG["order_buffer_cents"] / 100
    if action == "buy":
        if side == "yes": price = min(0.99, ya + buffer)
        else:             price = min(0.99, (1 - yb) + buffer)
    else:
        if side == "yes": price = max(0.01, yb - buffer)
        else:             price = max(0.01, (1 - ya) - buffer)

    expiration_ts = int(time.time()) + int(expiration_sec)
    if side == "yes":
        resp = kalshi_live.place_order(ticker, "yes", action, count,
                                         type_="limit", yes_price=price,
                                         expiration_ts=expiration_ts)
    else:
        resp = kalshi_live.place_order(ticker, "no", action, count,
                                         type_="limit", no_price=price,
                                         expiration_ts=expiration_ts)
    print(f"  ✓ live order: {ticker} {side} {action} x{count} @ ${price:.2f} "
          f"(expires {expiration_sec}s)")
    # Attach the actual limit price we set so callers can record it as
    # the realized fill price (Kalshi fills at this or better, but since
    # we set buffer-aggressive limits the fill is typically AT the limit).
    if isinstance(resp, dict):
        resp["_v2_limit_price"] = float(price)
    return resp


# ════════════════════════════════════════════════════════════════════════
#  Position management — runs every cycle
# ════════════════════════════════════════════════════════════════════════
def _market_quote(kalshi_md: KalshiClient, ticker: str) -> Optional[dict]:
    # Prefer in-memory WS state if fresh (< 30 s)
    with LOCK:
        b = BOOKS.get(ticker)
    if b and b.get("ts"):
        age = (datetime.now(timezone.utc) - b["ts"]).total_seconds()
        if age < 30 and b.get("yes_bid") is not None and b.get("yes_ask") is not None:
            return {
                "yes_bid": b["yes_bid"], "yes_ask": b["yes_ask"],
                "mid":     (b["yes_bid"] + b["yes_ask"]) / 2,
                "status":  b.get("status") or "active",
                "close_time": (dtparser.isoparse(b["close_time"])
                                if isinstance(b.get("close_time"), str) else b.get("close_time")),
            }
    # Fallback: REST fetch, parsed via parse_market_fields so it handles
    # both legacy 'yes_bid' int-cents AND 2026 'yes_bid_dollars' strings.
    try:
        from .client import parse_market_fields
        m  = kalshi_md.get_market(ticker).get("market", {})
        pf = parse_market_fields(m)
        yb, ya = pf.get("yes_bid"), pf.get("yes_ask")
        if yb is None or ya is None: return None
        ct = dtparser.isoparse(pf["close_time"]) if pf.get("close_time") else None
        return {"yes_bid": yb, "yes_ask": ya, "mid": (yb+ya)/2,
                "status": (pf.get("status") or "").lower(), "close_time": ct}
    except Exception:
        return None


def _decide_exit(trade: dict, quote: Optional[dict]) -> Optional[str]:
    side  = trade["side"]
    entry = float(trade["entry_price"])

    # max_age (works without a quote)
    try:
        ts = pd.to_datetime(trade["timestamp_utc"], utc=True)
        age_min = (datetime.now(timezone.utc) - ts.to_pydatetime()).total_seconds()/60
    except Exception:
        age_min = 0
    if age_min > CFG.get("max_position_age_min", 1e9):
        return f"max_age (age={age_min:.0f}m)"

    # Defer terminal-status / no-quote resolutions to check_settlements
    if quote is None:
        return None
    if quote.get("status") in TERMINAL_STATUSES:
        return None

    cur_mid = quote["mid"] if side == "yes" else 1.0 - quote["mid"]
    pnl_c = (cur_mid - entry) * 100

    if cur_mid <= entry * (1.0 - CFG["stop_loss_pct"]):
        return f"stop_loss ({pnl_c:+.1f}c)"
    if pnl_c >= CFG["take_profit_cents"]:
        return f"take_profit ({pnl_c:+.1f}c)"
    if quote["close_time"]:
        ttl_min = (quote["close_time"] - datetime.now(timezone.utc)).total_seconds() / 60
        if ttl_min <= CFG["time_exit_min_ttl_m"]:
            return f"time_exit (ttl={ttl_min:.1f}m)"
    return None


def _close_paper(trade: dict, quote: Optional[dict], reason: str,
                   override_close_price: Optional[float] = None):
    """Settle a trade in the local DB.

    Close price preference order:
      1. override_close_price (passed by _close_live = actual sell limit)
      2. mid-of-book mark from `quote`
      3. 0.50 (last-resort fallback)

    PnL is gross of fees; fees are computed and stored separately
    via a synthetic exit_reason suffix so reports can recompute
    net-of-fee PnL without reaching back into the order log.
    """
    side = trade["side"]
    if override_close_price is not None:
        mark = float(override_close_price)
    elif quote is not None:
        mark = quote["mid"] if side == "yes" else 1.0 - quote["mid"]
    else:
        mark = 0.5

    entry    = float(trade["entry_price"])
    contracts = int(trade["contracts"])
    gross_pnl = (mark - entry) * contracts

    # Kalshi round-trip taker fee per contract (approximation):
    # 0.07 * p * (1-p), ceil'd to nearest cent.
    import math as _math
    def _fee(p):
        p = max(0.0, min(1.0, p))
        return _math.ceil(0.07 * p * (1 - p) * 100) / 100.0
    fee_total = (_fee(entry) + _fee(mark)) * contracts

    pnl_net = gross_pnl - fee_total
    settle_trade(int(trade["id"]), mark, pnl_net, reason)
    print(f"    paper-closed #{trade['id']} {trade['market_ticker']} {side:>3s}  "
          f"reason={reason}  entry=${entry:.3f}  close=${mark:.3f}  "
          f"gross=${gross_pnl:+.2f}  fee=${fee_total:.2f}  net=${pnl_net:+.2f}")


def _close_live(kalshi_live: KalshiClient, kalshi_md: KalshiClient,
                  trade: dict, quote: Optional[dict], reason: str):
    try:
        resp = place_smart_limit(kalshi_live, kalshi_md, trade["market_ticker"],
                                    trade["side"], "sell", int(trade["contracts"]))
    except Exception as e:
        print(f"    live close FAILED #{trade['id']}: {e}")
        return
    # Use the actual sell-side limit price we set as the close price,
    # not the WS mid (which is systematically more favorable to the bot
    # than what a sell order actually fills at).
    sell_price = None
    if isinstance(resp, dict):
        sell_price = resp.get("_v2_limit_price")
    _close_paper(trade, quote, f"live:{reason}",
                   override_close_price=sell_price)


def manage_open_positions(kalshi_md: KalshiClient,
                            kalshi_live: Optional[KalshiClient] = None,
                            dry_run: bool = False) -> int:
    """Iterate every open trade and apply exit rules. Returns the number
    of positions closed this cycle."""
    open_ = open_trades()    # all tags
    if len(open_) == 0:
        return 0
    n_closed = 0
    mode = CFG.get("mode", "paper")
    for _, t in open_.iterrows():
        q = _market_quote(kalshi_md, t["market_ticker"])
        reason = _decide_exit(t.to_dict(), q)
        if reason is None: continue
        if dry_run:
            print(f"    DRY would close #{t['id']} {t['market_ticker']} {t['side']:>3s} "
                  f"reason={reason}")
            continue
        is_live_tag = (t.get("trade_type") in LIVE_OR_SHADOW)
        if mode == "live" and is_live_tag and t.get("trade_type", "").endswith("_live"):
            _close_live(kalshi_live, kalshi_md, t.to_dict(), q, reason)
        else:
            _close_paper(t.to_dict(), q, reason)
        n_closed += 1
    return n_closed


# ════════════════════════════════════════════════════════════════════════
#  Settlement checker — terminal markets only, reads Kalshi result field
# ════════════════════════════════════════════════════════════════════════
def check_settlements(kalshi_md: KalshiClient) -> int:
    """For each unsettled trade, fetch the market and book PnL if Kalshi
    has marked it terminal AND populated `result`. Closes that aren't yet
    finalized are skipped (try again next cycle)."""
    df = open_trades()
    if len(df) == 0: return 0
    n_settled = 0
    for _, t in df.iterrows():
        try:
            m = kalshi_md.get_market(t["market_ticker"]).get("market", {})
        except Exception:
            continue
        status = (m.get("status") or "").lower()
        if status not in TERMINAL_STATUSES:
            continue
        # Read result field. Kalshi format: 'yes' / 'no' / 'void'
        result = (m.get("result") or "").lower()
        if not result or result == "void":
            continue
        # Determine settle price for our side
        if result == "yes":
            settle_price = 1.0 if t["side"] == "yes" else 0.0
        elif result == "no":
            settle_price = 0.0 if t["side"] == "yes" else 1.0
        else:
            continue
        pnl = (settle_price - float(t["entry_price"])) * int(t["contracts"])
        settle_trade(int(t["id"]), settle_price, pnl,
                       f"resolution:result={result}")
        print(f"  ✓ settled #{t['id']} {t['market_ticker']}: "
              f"bet {t['side']}, result {result}, P&L ${pnl:+.2f}")
        n_settled += 1
    return n_settled
