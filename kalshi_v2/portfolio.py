"""Portfolio metrics — separate paper and live/shadow views.

Reads from the shared trades table. Filters by trade_type to keep paper
and live accounting independent. Marks open positions to market using
in-memory BOOKS first, REST fallback second.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .config import CFG, PAPER_TAGS, LIVE_OR_SHADOW
from .state import BOOKS, LOCK


def _portfolio_view(label: str, tags, bankroll: float, account_label: str,
                     kalshi_md=None, kalshi_live=None) -> dict:
    """Print + return a single-portfolio view.

    For LIVE / SHADOW (kalshi_live provided): bankroll = Kalshi's
    `/portfolio/balance` cash, which is already net of position costs and
    settled PnL. So:
        equity = bankroll + position_market_value
                = bankroll + Σ(entry × contracts) + unrealized
    For PAPER (kalshi_live = None): bankroll = CFG['bankroll'] fixed
    starting balance, so cash and equity are derived from realized PnL.

    Returns dict with: equity, cash, realized, unrealized, n_open, n_settled.
    """
    real = tuple(t for t in tags if t is not None)
    null_clause = " OR trade_type IS NULL" if None in tags else ""
    ph = ",".join("?" * len(real))
    where = f"({'trade_type IN (' + ph + ')' if real else '1=0'}{null_clause})"

    from .paper_db import _conn
    conn = _conn()
    df = pd.read_sql_query(
        f"SELECT * FROM trades WHERE {where} ORDER BY timestamp_utc",
        conn, params=real)
    conn.close()

    is_live_view = kalshi_live is not None

    # Refresh live balance if possible — the cached value may be stale.
    if is_live_view:
        try:
            b = kalshi_live.get_balance()
            fresh = float(b.get("balance", 0)) / 100.0
            if fresh > 0:
                bankroll = fresh
        except Exception:
            pass

    print("=" * 72)
    print(f"  {label}")
    print(f"  cash:     ${bankroll:>14,.2f}  ({account_label})")
    print(f"  time:     {datetime.now(timezone.utc).isoformat()}")
    print("=" * 72)
    if len(df) == 0:
        print("  no trades.\n")
        return {"equity": bankroll, "cash": bankroll,
                "n_open": 0, "n_settled": 0}

    settled = df[df["settled"] == 1].copy()
    open_   = df[df["settled"] == 0].copy()

    # Mark-to-market open positions
    open_["current"] = float("nan")
    open_["unrealized"] = float("nan")
    open_["pnl_pct"] = float("nan")
    for idx, t in open_.iterrows():
        mark = _mark_to_market(t["market_ticker"], t["side"], kalshi_md)
        if mark is not None:
            open_.at[idx, "current"]    = mark
            open_.at[idx, "unrealized"] = (mark - float(t["entry_price"])) * int(t["contracts"])
            open_.at[idx, "pnl_pct"]    = (mark / float(t["entry_price"]) - 1) * 100

    realized  = float(settled["pnl_dollars"].sum()) if len(settled) else 0.0
    open_cost = float((open_["entry_price"] * open_["contracts"]).sum()) if len(open_) else 0.0
    unreal    = float(open_["unrealized"].sum(skipna=True)) if len(open_) else 0.0
    open_mark = open_cost + unreal       # current market value of open positions

    if is_live_view:
        # Kalshi balance IS cash. Don't double-count realized / open_cost.
        cash   = bankroll
        equity = cash + open_mark
    else:
        # Paper: bankroll is the fixed starting balance.
        cash   = bankroll + realized - open_cost
        equity = cash + open_mark

    print(f"\n  Cash:                ${cash:>14,.2f}")
    print(f"  Open positions @ mark: ${open_mark:>14,.2f}  "
          f"(cost ${open_cost:,.2f} + unreal ${unreal:+,.2f})")
    print(f"  Realized PnL:        ${realized:>+14,.2f}")
    print(f"  TOTAL EQUITY:        ${equity:>14,.2f}")
    if not is_live_view:
        print(f"    vs starting:       {(equity/bankroll-1)*100:+.2f}%")

    if len(settled):
        wins = (settled["pnl_dollars"] > 0).sum()
        print(f"\n  Settled: n={len(settled)}  wins={wins} ({wins/len(settled)*100:.1f}%)")

    if len(open_):
        print(f"\n  Open positions: {len(open_)}")
        cols = ["id","market_ticker","side","contracts","entry_price",
                 "current","unrealized","pnl_pct","trade_type"]
        cols = [c for c in cols if c in open_.columns]
        disp = open_[cols].copy()
        for c in ("entry_price","current"):
            if c in disp.columns:
                disp[c] = disp[c].apply(lambda x: f"${x:.3f}" if pd.notna(x) else "—")
        if "unrealized" in disp.columns:
            disp["unrealized"] = disp["unrealized"].apply(
                lambda x: f"${x:+.2f}" if pd.notna(x) else "—")
        if "pnl_pct" in disp.columns:
            disp["pnl_pct"] = disp["pnl_pct"].apply(
                lambda x: f"{x:+.1f}%" if pd.notna(x) else "—")
        print(disp.to_string(index=False))
    print()
    return {"equity": equity, "cash": cash, "realized": realized,
             "unrealized": unreal, "n_open": len(open_), "n_settled": len(settled)}


def _mark_to_market(ticker: str, side: str, kalshi_md=None) -> Optional[float]:
    # Prefer in-memory WS state
    with LOCK:
        b = BOOKS.get(ticker)
    if b and b.get("yes_bid") is not None and b.get("yes_ask") is not None:
        mid = (b["yes_bid"] + b["yes_ask"]) / 2
        return mid if side == "yes" else 1.0 - mid
    # REST fallback — use parse_market_fields so 2026 *_dollars fields work.
    if kalshi_md is not None:
        try:
            from .client import parse_market_fields
            m  = kalshi_md.get_market(ticker).get("market", {})
            pf = parse_market_fields(m)
            yb, ya = pf.get("yes_bid"), pf.get("yes_ask")
            if yb is not None and ya is not None:
                mid = (float(yb) + float(ya)) / 2
                return mid if side == "yes" else 1.0 - mid
            last = pf.get("last_price")
            if last is not None:
                return float(last) if side == "yes" else 1.0 - float(last)
        except Exception:
            return None
    return None


def paper_portfolio_metrics(kalshi_md=None):
    return _portfolio_view("PAPER PORTFOLIO", PAPER_TAGS,
                            CFG.get("bankroll", 100_000.0),
                            "CFG['bankroll']", kalshi_md)


def _kalshi_open_positions(kalshi_live, kalshi_md=None) -> list:
    """Pull live open positions from Kalshi (source of truth), not from
    the bot's DB. Marks each to current bid/ask mid via _mark_to_market.

    Returns list of dicts:
      { ticker, side (yes/no), contracts, avg_entry, current_mark, value }
    where value = contracts * current_mark.
    """
    if kalshi_live is None:
        return []
    try:
        resp = kalshi_live.get_positions()
    except Exception as e:
        print(f"  positions fetch failed: {e}")
        return []
    rows = []
    for p in resp.get("market_positions", []) or resp.get("positions", []) or []:
        ticker = p.get("ticker") or p.get("market_ticker")
        # Kalshi reports a single signed `position` count and an average price
        pos = int(p.get("position", 0))
        if pos == 0:
            continue
        side = "yes" if pos > 0 else "no"
        contracts = abs(pos)
        # Average entry — Kalshi exposes either market_exposure (in cents) or
        # average_price_cents; fall back to 0 if neither present.
        avg_entry = None
        for k in ("average_yes_price", "average_price", "market_exposure"):
            if p.get(k) is not None:
                v = float(p[k])
                # cents-int vs dollar-float heuristic
                avg_entry = v / 100.0 if v > 1.0 else v
                break
        if avg_entry is None:
            avg_entry = 0.5
        mark = _mark_to_market(ticker, side, kalshi_md=kalshi_md)
        if mark is None:
            mark = avg_entry
        rows.append({
            "ticker":       ticker,
            "side":         side,
            "contracts":    contracts,
            "avg_entry":    avg_entry,
            "current_mark": mark,
            "value":        contracts * mark,
            "unrealized":   (mark - avg_entry) * contracts,
        })
    return rows


def live_portfolio_metrics(kalshi_md=None, kalshi_live=None):
    """Live portfolio view sourced from Kalshi directly:
      - cash      = /portfolio/balance  (the spendable cash field)
      - positions = /portfolio/positions, marked to current mid
      - equity    = cash + Σ(position contracts × current_mark)
    Ignores the bot's DB for open positions (DB can drift from Kalshi
    truth if the user closes manually or the bot misses a fill).
    """
    if kalshi_live is None:
        print("=" * 72)
        print("  LIVE PORTFOLIO — no auth client, skipping")
        print("=" * 72)
        return {}

    # Cash from API
    try:
        b   = kalshi_live.get_balance()
        cash = float(b.get("balance", 0)) / 100.0
        payout = float(b.get("payout", 0)) / 100.0 if b.get("payout") else 0.0
    except Exception as e:
        print(f"  balance fetch failed: {e}")
        return {}

    positions = _kalshi_open_positions(kalshi_live, kalshi_md=kalshi_md)
    open_cost = sum(p["contracts"] * p["avg_entry"] for p in positions)
    open_mark = sum(p["value"] for p in positions)
    unreal    = sum(p["unrealized"] for p in positions)
    equity    = cash + open_mark + payout

    print("=" * 72)
    print(f"  LIVE PORTFOLIO  (source: Kalshi API)")
    print(f"  time:     {datetime.now(timezone.utc).isoformat()}")
    print("=" * 72)
    print(f"\n  Cash (spendable):      ${cash:>14,.2f}")
    if payout > 0:
        print(f"  Pending payout:        ${payout:>14,.2f}")
    print(f"  Open positions @ mark: ${open_mark:>14,.2f}  "
          f"(cost ${open_cost:,.2f} + unreal ${unreal:+,.2f})")
    print(f"  TOTAL EQUITY:          ${equity:>14,.2f}")

    if positions:
        print(f"\n  Open positions on Kalshi: {len(positions)}")
        for p in positions:
            pnl_pct = (p["current_mark"] / p["avg_entry"] - 1) * 100 if p["avg_entry"] > 0 else 0
            print(f"    {p['ticker']:35s} {p['side']:>3s}  x{p['contracts']:>4d}  "
                  f"avg ${p['avg_entry']:.3f}  mark ${p['current_mark']:.3f}  "
                  f"unreal ${p['unrealized']:+.2f} ({pnl_pct:+.1f}%)")
    print()
    return {"cash": cash, "payout": payout, "open_mark": open_mark,
             "unrealized": unreal, "equity": equity,
             "n_open": len(positions)}


def portfolio_metrics(kalshi_md=None, kalshi_live=None):
    paper_portfolio_metrics(kalshi_md)
    live_portfolio_metrics(kalshi_md, kalshi_live)
