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


def live_portfolio_metrics(kalshi_md=None, kalshi_live=None):
    from .risk import get_live_balance
    bal = get_live_balance()
    if bal is None or bal <= 0:
        if kalshi_live is not None:
            try:
                b = kalshi_live.get_balance()
                bal = float(b.get("balance", 0)) / 100.0
                from .risk import set_live_balance
                set_live_balance(bal)
            except Exception:
                bal = None
    if bal is None or bal <= 0:
        print("=" * 72)
        print("  LIVE / SHADOW PORTFOLIO — balance unavailable, skipping")
        print("=" * 72)
        return {}
    return _portfolio_view("LIVE / SHADOW PORTFOLIO", LIVE_OR_SHADOW,
                            bal, "Kalshi cash balance", kalshi_md,
                            kalshi_live=kalshi_live)


def portfolio_metrics(kalshi_md=None, kalshi_live=None):
    paper_portfolio_metrics(kalshi_md)
    live_portfolio_metrics(kalshi_md, kalshi_live)
