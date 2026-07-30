"""K6 + K14 signal detection — pure logic.

K6: when BTC spot is $50-$500 past a strike with ≤15 minutes to close, the
favored side is statistically underpriced. Trade with side-aware vol filter:
  - YES side (spot above strike) only when rv_15m is mid+high (≥ k14_yes_min_rv)
  - NO side  (spot below strike) only when rv_15m is low+mid  (< k14_no_max_rv)

Input: a snapshot of {market_ticker → market info} + current spot + rv estimate.
Output: list of K6Signal candidates ranked by expected edge.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from config import CFG, kalshi_fee


@dataclass
class K6Signal:
    market_ticker: str
    event_ticker:  str
    strike:        float
    side:          str          # 'yes' or 'no'
    bucket:        str          # e.g. 'A1' (0-5m, d+50..+200)
    price:         float        # ask we'd pay
    qty:           int
    spot:          float
    spot_dist:     float        # spot − strike
    secs_to_close: float
    rv_15m_annual: float
    expected_edge: float        # cents per contract after fees
    book_depth:    int          # ask qty at signal time
    detected_at:   datetime


# ─ K6 bucket definitions ───────────────────────────────────────────
# Each bucket: (time_lo_sec, time_hi_sec, dist_lo_$, dist_hi_$, side, label, edge¢)
BUCKETS = [
    (0,   300,    50,  200, 'yes', 'A1',  5.1),
    (0,   300,   200,  500, 'yes', 'A2',  2.8),
    (0,   300,   -200,  -50, 'no',  'A1n', 0.3),
    (0,   300,   -500, -200, 'no',  'A2n', 1.5),
    (300, 900,    50,  200, 'yes', 'B1',  3.5),
    (300, 900,   200,  500, 'yes', 'B2',  2.5),
    (300, 900,   -200,  -50, 'no',  'B1n', 1.1),
    (300, 900,   -500, -200, 'no',  'B2n', 1.2),
]


def _book_passes(market: Dict[str, Any], side: str, max_spread: float = 0.05,
                  min_depth: int = 5) -> Optional[Dict[str, Any]]:
    """Return (ask_price, ask_qty) if book is healthy on the given side."""
    if side == 'yes':
        ask = market.get('yes_ask')
        ask_qty = market.get('yes_ask_qty') or 0
        bid = market.get('yes_bid')
    else:
        ask = market.get('no_ask')
        ask_qty = market.get('no_ask_qty') or 0
        bid = market.get('no_bid')
    if ask is None or bid is None or ask <= 0 or ask >= 1:
        return None
    if ask_qty < min_depth:
        return None
    if (ask - bid) > max_spread:
        return None
    return {'price': float(ask), 'qty': int(ask_qty), 'bid': float(bid)}


def _kelly_qty(win_prob: float, ask: float, bankroll_avail: float,
                max_qty: int, max_dollars: float, fraction: float) -> int:
    """Quarter-Kelly position sizing. Returns at least 1, at most max_qty."""
    fee = kalshi_fee(ask)
    win = 1.0 - ask - fee
    loss = ask + fee
    if win <= 0 or loss <= 0:
        return 0
    b = win / loss
    p = win_prob
    q = 1.0 - p
    f_full = (p * b - q) / b
    if f_full <= 0:
        return 0
    f = f_full * fraction
    dollar_size = min(bankroll_avail, max_dollars) * f
    if ask <= 0:
        return 0
    qty = int(dollar_size / ask)
    return max(1, min(qty, max_qty))


def scan_k6(markets: Dict[str, Dict[str, Any]],
             spot: float,
             rv_15m_annual: float,
             bankroll_avail: float,
             now: Optional[datetime] = None,
             open_market_tickers: Optional[set] = None) -> List[K6Signal]:
    """Return ranked list of K6 signals.

    markets:    {ticker → {strike, close_time, yes_bid, yes_ask, yes_ask_qty,
                            no_bid, no_ask, no_ask_qty, event_ticker, status}}
    spot:       current BTC mid
    rv_15m:     annualized realized σ over last 15min (for K14 filter)
    bankroll:   available capital for sizing
    open_tickers: positions we already have — skip these
    """
    if spot is None or spot <= 0 or rv_15m_annual is None:
        return []
    now = now or datetime.now(timezone.utc)
    open_market_tickers = open_market_tickers or set()
    signals: List[K6Signal] = []

    for ticker, m in markets.items():
        if ticker in open_market_tickers:
            continue
        if m.get('status') and m['status'] != 'active':
            continue
        strike = m.get('strike') or m.get('floor_strike')
        if strike is None: continue
        close_time = m.get('close_time')
        if close_time is None: continue
        secs = (close_time - now).total_seconds()
        if secs <= 0: continue
        spot_dist = spot - float(strike)

        for tlo, thi, dlo, dhi, side, label, _hist_edge in BUCKETS:
            if not (tlo <= secs <= thi):
                continue
            if not (dlo <= spot_dist <= dhi):
                continue

            # K14 vol-regime filter
            if side == 'yes' and rv_15m_annual < CFG['k14_yes_min_rv']:
                continue
            if side == 'no'  and rv_15m_annual >= CFG['k14_no_max_rv']:
                continue

            book = _book_passes(m, side)
            if book is None: continue
            ask = book['price']

            fee = kalshi_fee(ask)
            # Estimate expected edge from win prob × payoff math
            p_win = CFG['expected_win_prob']
            exp_edge = p_win * (1.0 - ask - fee) + (1 - p_win) * (-(ask + fee))
            if exp_edge < 0.005:  # require ≥ 0.5¢ expected after fees
                continue

            qty = _kelly_qty(p_win, ask, bankroll_avail,
                              CFG['max_qty_per_signal'],
                              CFG['max_dollars_per_signal'],
                              CFG['kelly_fraction'])
            qty = min(qty, book['qty'])
            if qty < 1: continue

            signals.append(K6Signal(
                market_ticker=ticker,
                event_ticker=m.get('event_ticker') or '',
                strike=float(strike), side=side, bucket=label,
                price=ask, qty=qty,
                spot=spot, spot_dist=spot_dist,
                secs_to_close=secs,
                rv_15m_annual=rv_15m_annual,
                expected_edge=exp_edge,
                book_depth=book['qty'],
                detected_at=now,
            ))
            break  # one bucket per market

    # rank by expected edge × qty (dollar-weighted)
    signals.sort(key=lambda s: -s.expected_edge * s.qty)
    return signals
