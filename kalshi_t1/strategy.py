"""T1 monotonicity arbitrage — pure logic.

For any two strikes K_lo < K_hi on the same event:
  P(BTC > K_lo) ≥ P(BTC > K_hi)  by definition of monotone probability

Equivalently:
  yes_ask(K_lo) ≥ yes_bid(K_hi)    must hold (no arb)

When VIOLATED — i.e., yes_bid(K_hi) > yes_ask(K_lo) — there's a risk-free arb:
  BUY YES at K_lo  (paying yes_ask_lo)
  BUY NO  at K_hi  (paying 1 − yes_bid_hi)

  Min payout = $1 minus (yes_ask_lo + (1 − yes_bid_hi)) minus fees
  Risk-free if bid_hi − ask_lo > fees.

Calm-regime filter: only fire when |spot_move_30s| ≤ $30 (V5 backtest finding —
T1 arbs evaporate in <500ms during volatile regimes, faster than 200-400ms RTT).
"""
from __future__ import annotations
import math, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from config import CFG, kalshi_fee


@dataclass
class T1Signal:
    pair_id:        str
    event_ticker:   str
    mkt_lo:         str
    mkt_hi:         str
    strike_lo:      float
    strike_hi:      float
    ask_lo:         float       # yes_ask at lower strike
    bid_hi:         float       # yes_bid at higher strike
    qty:            int
    gross_edge:     float       # bid_hi − ask_lo (before fees)
    net_edge:       float       # after both legs' fees
    pair_cost:      float       # ask_lo + (1 − bid_hi)  — cost of one pair
    secs_to_close:  float
    detected_at:    datetime


def scan_t1(markets: Dict[str, Dict[str, Any]],
            spot: Optional[float],
            spot_move_30s: Optional[float],
            bankroll_avail: float,
            now: Optional[datetime] = None,
            open_market_tickers: Optional[set] = None) -> List[T1Signal]:
    """Scan for monotonicity arb pairs.

    markets:        {ticker → {strike, close_time, yes_bid, yes_ask, yes_ask_qty,
                                yes_bid_qty, event_ticker, status}}
    spot:           current BTC mid (for diagnostics — not required for the arb)
    spot_move_30s:  |spot(now) − spot(now − 30s)| (calm filter)
    """
    now = now or datetime.now(timezone.utc)
    open_market_tickers = open_market_tickers or set()

    # Calm-regime filter
    if CFG.get('t1_calm_filter_enabled', True):
        if spot_move_30s is None:
            return []
        if spot_move_30s > CFG['t1_calm_max_spot_move_30s']:
            return []

    # Build sorted list of (ticker, strike, ya, yb, qtys) for active markets
    rows = []
    for ticker, m in markets.items():
        if ticker in open_market_tickers:
            continue
        if m.get('status') and m['status'] != 'active':
            continue
        strike = m.get('strike') or m.get('floor_strike')
        ya = m.get('yes_ask'); yb = m.get('yes_bid')
        if strike is None or ya is None or yb is None: continue
        if ya <= 0.01 or yb >= 0.99: continue
        ya_qty = int(m.get('yes_ask_qty') or 0)
        yb_qty = int(m.get('yes_bid_qty') or 0)
        rows.append({
            'ticker': ticker, 'strike': float(strike),
            'ya': float(ya), 'yb': float(yb),
            'ya_qty': ya_qty, 'yb_qty': yb_qty,
            'event_ticker': m.get('event_ticker', ''),
            'close_time': m.get('close_time'),
        })
    rows.sort(key=lambda r: r['strike'])
    if len(rows) < 2:
        return []

    opps = []
    for i, lo in enumerate(rows):
        # Only need to consider strictly higher strikes
        for hi in rows[i + 1:]:
            # Arb requires bid at higher strike > ask at lower strike
            if hi['yb'] <= lo['ya']:
                continue
            gross = hi['yb'] - lo['ya']
            fees = kalshi_fee(lo['ya']) + kalshi_fee(1.0 - hi['yb'])
            net = gross - fees
            if net < CFG['t1_min_net_edge_cents'] / 100:
                continue
            # qty limited by both book sides + config cap + bankroll
            qty_book = min(lo['ya_qty'] or 1, hi['yb_qty'] or 1)
            qty = min(qty_book, CFG['t1_max_qty_per_leg'])
            pair_cost_one = lo['ya'] + (1.0 - hi['yb'])
            if pair_cost_one <= 0:
                continue
            qty_budget = max(1, int(CFG['t1_max_dollars_per_pair'] / pair_cost_one))
            qty = min(qty, qty_budget)
            # Total cost incl. fees for this size
            total_cost = qty * pair_cost_one + qty * fees
            if total_cost > bankroll_avail:
                qty = max(1, int(bankroll_avail / (pair_cost_one + fees)))
                if qty < 1: continue
                total_cost = qty * pair_cost_one + qty * fees
                if total_cost > bankroll_avail: continue
            close_time = lo['close_time'] or hi['close_time']
            secs_to_close = ((close_time - now).total_seconds()
                              if close_time else 1800)
            opps.append(T1Signal(
                pair_id=f't1-{uuid.uuid4().hex[:10]}',
                event_ticker=lo['event_ticker'],
                mkt_lo=lo['ticker'], mkt_hi=hi['ticker'],
                strike_lo=lo['strike'], strike_hi=hi['strike'],
                ask_lo=lo['ya'], bid_hi=hi['yb'],
                qty=int(qty), gross_edge=gross, net_edge=net,
                pair_cost=pair_cost_one,
                secs_to_close=secs_to_close, detected_at=now,
            ))

    # Sort by net edge × qty (best $-edge first)
    opps.sort(key=lambda s: -s.net_edge * s.qty)
    return opps
