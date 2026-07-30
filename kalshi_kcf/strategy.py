"""KCF (Kalshi Capitulation Fade) — signal detection.

Detects sell_yes_taker trade events by diffing successive orderbook snapshots.
A trade is inferred when:
  - top-of-book qty on a side DROPS between consecutive polls
  - the corresponding PRICE level is unchanged (= someone consumed liquidity at that price)
  - the qty drop exceeds the market's rolling p85 of historical drops (= "strong" trade)

When a STRONG sell_yes_taker is detected:
  - Calibration says these are anti-informed (P(YES wins | sell_yes_taker) ≈ 68.6%)
  - Action: BUY YES at current ask, hold to settlement

When a STRONG buy_no_taker is detected:
  - Calibration says these are anti-informed (P(YES wins | buy_no_taker) ≈ 26.8%)
  - Action: BUY NO at current ask (less common — fewer such events), hold to settlement
"""
from __future__ import annotations
import math
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from config import CFG, kalshi_fee


# Per-trade-type effective informativeness (from
# INFORMED_VS_UNINFORMED_RESEARCH.md). Anti-informed types get inverted sign.
TRADE_CALIBRATION = {
    'sell_yes_taker':  {'p_yes_wins': 0.686, 'eff_sign': +1, 'alpha': 0.372},
    'sell_no_taker':   {'p_yes_wins': 0.637, 'eff_sign': +1, 'alpha': 0.273},
    'buy_yes_taker':   {'p_yes_wins': 0.220, 'eff_sign': -1, 'alpha': 0.561},
    'buy_no_taker':    {'p_yes_wins': 0.268, 'eff_sign': -1, 'alpha': 0.464},
}


@dataclass
class KCFSignal:
    market_ticker:  str
    event_ticker:   str
    strike:         float
    side:           str          # 'yes' or 'no' — what WE'd buy
    trigger_type:   str          # which trade type triggered
    trade_qty:      int          # detected trade size
    rolling_p85:    int          # market's rolling p85 threshold
    price:          float        # what we'd pay
    qty:            int          # our position size
    expected_winp:  float        # P(YES wins | trigger)
    expected_edge:  float        # vs current ask, post-fee
    detected_at:    datetime
    secs_to_close:  float


class TradeDetector:
    """Detects trades by diffing successive snapshots of top-of-book.
    Maintains per-market rolling history of trade sizes to compute p85.
    """
    def __init__(self, rolling_window: int = 100):
        # market_ticker -> previous snapshot dict
        self._prev: Dict[str, Dict[str, Any]] = {}
        # market_ticker -> deque of trade sizes
        self._sizes: Dict[str, deque] = defaultdict(lambda: deque(maxlen=rolling_window))

    def detect(self, market_ticker: str, snap: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return list of inferred trade events since last snapshot."""
        events = []
        prev = self._prev.get(market_ticker)
        self._prev[market_ticker] = dict(snap)
        if prev is None:
            return events

        # YES side
        if (snap.get('yes_bid') == prev.get('yes_bid')
                and snap.get('yes_bid_qty') is not None
                and prev.get('yes_bid_qty') is not None):
            drop = prev['yes_bid_qty'] - snap['yes_bid_qty']
            if drop > 0:
                events.append({
                    'classify': 'sell_yes_taker',
                    'price': snap['yes_bid'],
                    'qty': int(drop),
                })
                self._sizes[market_ticker].append(drop)
        if (snap.get('yes_ask') == prev.get('yes_ask')
                and snap.get('yes_ask_qty') is not None
                and prev.get('yes_ask_qty') is not None):
            drop = prev['yes_ask_qty'] - snap['yes_ask_qty']
            if drop > 0:
                events.append({
                    'classify': 'buy_yes_taker',
                    'price': snap['yes_ask'],
                    'qty': int(drop),
                })
                self._sizes[market_ticker].append(drop)
        # NO side (symmetric)
        if (snap.get('no_bid') == prev.get('no_bid')
                and snap.get('no_bid_qty') is not None
                and prev.get('no_bid_qty') is not None):
            drop = prev['no_bid_qty'] - snap['no_bid_qty']
            if drop > 0:
                events.append({
                    'classify': 'sell_no_taker',
                    'price': snap['no_bid'],
                    'qty': int(drop),
                })
                self._sizes[market_ticker].append(drop)
        if (snap.get('no_ask') == prev.get('no_ask')
                and snap.get('no_ask_qty') is not None
                and prev.get('no_ask_qty') is not None):
            drop = prev['no_ask_qty'] - snap['no_ask_qty']
            if drop > 0:
                events.append({
                    'classify': 'buy_no_taker',
                    'price': snap['no_ask'],
                    'qty': int(drop),
                })
                self._sizes[market_ticker].append(drop)
        return events

    def rolling_p85(self, market_ticker: str) -> Optional[float]:
        s = self._sizes.get(market_ticker)
        if not s or len(s) < 20: return None
        sorted_s = sorted(s)
        return sorted_s[int(0.85 * len(sorted_s))]


def scan_kcf(detector: TradeDetector,
             markets: Dict[str, Dict[str, Any]],
             bankroll_avail: float,
             open_market_tickers: Optional[set] = None,
             now: Optional[datetime] = None) -> List[KCFSignal]:
    """Run detector against fresh market snapshots, emit KCF signals on
    strong anti-informed events."""
    now = now or datetime.now(timezone.utc)
    open_market_tickers = open_market_tickers or set()
    signals: List[KCFSignal] = []

    for ticker, m in markets.items():
        events = detector.detect(ticker, m)
        if not events: continue
        if ticker in open_market_tickers: continue

        threshold = detector.rolling_p85(ticker)
        if threshold is None or threshold < 3: continue  # warmup

        # Each market emits at most ONE signal per cycle (most-informative event)
        best_event = None; best_edge = -1
        for ev in events:
            cal = TRADE_CALIBRATION.get(ev['classify'])
            if not cal: continue
            if ev['qty'] < threshold: continue

            # Decide our side: follow the eff_sign of the trigger
            if cal['eff_sign'] == +1:
                # buy YES at current ask
                entry = m.get('yes_ask')
                if entry is None or entry >= 0.99: continue
                p_win = cal['p_yes_wins']
            else:
                # buy NO at current ask
                entry = m.get('no_ask')
                if entry is None or entry >= 0.99: continue
                p_win = 1 - cal['p_yes_wins']

            fee = kalshi_fee(entry)
            edge = p_win - entry - fee
            if edge < CFG.get('kcf_min_edge', 0.02): continue
            if edge > best_edge:
                best_edge = edge
                best_event = ev
                best_event['entry'] = entry
                best_event['p_win'] = p_win
                best_event['edge'] = edge
                best_event['eff_sign'] = cal['eff_sign']

        if not best_event: continue

        # Position size — quarter-Kelly
        p = best_event['p_win']; ask = best_event['entry']
        fee = kalshi_fee(ask)
        win = 1 - ask - fee; loss = ask + fee
        if win <= 0: continue
        b = win / loss
        f_star = (p * b - (1 - p)) / b
        if f_star <= 0: continue
        f = f_star * CFG.get('kelly_fraction', 0.25)
        dollar_size = min(bankroll_avail, CFG.get('max_dollars_per_signal', 15.0)) * f
        qty = max(1, min(int(dollar_size / ask),
                          CFG.get('max_qty_per_signal', 20),
                          best_event['qty']))   # don't exceed detected trade size

        signals.append(KCFSignal(
            market_ticker=ticker,
            event_ticker=m.get('event_ticker') or '',
            strike=float(m.get('strike') or 0),
            side='yes' if best_event['eff_sign'] == +1 else 'no',
            trigger_type=best_event['classify'],
            trade_qty=best_event['qty'],
            rolling_p85=int(threshold),
            price=ask,
            qty=qty,
            expected_winp=p,
            expected_edge=best_event['edge'],
            detected_at=now,
            secs_to_close=(m.get('close_time') - now).total_seconds()
                if m.get('close_time') else 1800,
        ))

    signals.sort(key=lambda s: -s.expected_edge * s.qty)
    return signals
