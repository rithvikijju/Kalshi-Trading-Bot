"""T1 pair-based portfolio tracker.

A T1 position is a PAIR — two legs in different markets that settle together.
  - leg_lo: BUY YES at strike K_lo (cost: yes_ask_lo + fee)
  - leg_hi: BUY NO at strike K_hi  (cost: (1 − yes_bid_hi) + fee)

At settlement:
  BTC ≤ K_lo:        leg_lo loses $0; leg_hi wins $1
  K_lo < BTC ≤ K_hi: leg_lo wins $1; leg_hi wins $1   (both win)
  BTC > K_hi:        leg_lo wins $1; leg_hi loses $0

In all cases, total payout ≥ $1 per pair × qty. Risk-free.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from config import CFG, kalshi_fee


@dataclass
class T1Position:
    position_id:   str
    pair_id:       str
    event_ticker:  str
    mkt_lo:        str
    mkt_hi:        str
    strike_lo:     float
    strike_hi:     float
    qty:           int
    ask_lo:        float
    bid_hi:        float
    leg_lo_cost:   float       # qty × (ask_lo + fee_lo)
    leg_hi_cost:   float       # qty × ((1 − bid_hi) + fee_hi)
    total_cost:    float       # leg_lo_cost + leg_hi_cost
    net_edge:      float       # captured edge after fees, per pair × qty
    opened_at:     str         # ISO
    close_time:    str         # ISO market settlement
    # Filled at settle:
    settle_btc:    Optional[float] = None
    realized_pnl:  Optional[float] = None
    settled_at:    Optional[str]   = None


class T1Portfolio:
    def __init__(self):
        self.starting_bankroll = CFG['starting_bankroll']
        self.cash = CFG['starting_bankroll']
        self.realized_pnl = 0.0
        self.cum_fees = 0.0
        self.open_positions: Dict[str, T1Position] = {}
        self.closed_count = 0
        self.daily_pnl = 0.0
        self.daily_pnl_date = datetime.now(timezone.utc).date().isoformat()
        self.halted = False
        self.started_at = datetime.now(timezone.utc).isoformat()

    def _rollover_daily(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.daily_pnl_date:
            self.daily_pnl_date = today
            self.daily_pnl = 0.0

    def check_halt(self):
        self._rollover_daily()
        if self.daily_pnl <= CFG['daily_loss_limit']:
            self.halted = True

    def open_exposure(self) -> float:
        return sum(p.total_cost for p in self.open_positions.values())

    def can_open(self, total_cost: float) -> bool:
        if self.halted: return False
        if total_cost > self.cash: return False
        if self.open_exposure() + total_cost > CFG['max_total_exposure']: return False
        if len(self.open_positions) >= CFG['max_concurrent_positions']: return False
        return True

    def open_position(self, signal_dict: Dict[str, Any]) -> Optional[T1Position]:
        self._rollover_daily()
        qty = int(signal_dict['qty'])
        ask_lo = float(signal_dict['ask_lo'])
        bid_hi = float(signal_dict['bid_hi'])
        no_price_hi = 1.0 - bid_hi
        fee_lo = kalshi_fee(ask_lo)
        fee_hi = kalshi_fee(no_price_hi)
        leg_lo_cost = qty * (ask_lo + fee_lo)
        leg_hi_cost = qty * (no_price_hi + fee_hi)
        total_cost = leg_lo_cost + leg_hi_cost
        if not self.can_open(total_cost):
            return None
        pid = f"t1-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        pos = T1Position(
            position_id=pid,
            pair_id=signal_dict['pair_id'],
            event_ticker=signal_dict.get('event_ticker', ''),
            mkt_lo=signal_dict['mkt_lo'],
            mkt_hi=signal_dict['mkt_hi'],
            strike_lo=float(signal_dict['strike_lo']),
            strike_hi=float(signal_dict['strike_hi']),
            qty=qty, ask_lo=ask_lo, bid_hi=bid_hi,
            leg_lo_cost=leg_lo_cost, leg_hi_cost=leg_hi_cost,
            total_cost=total_cost,
            net_edge=float(signal_dict['net_edge']) * qty,
            opened_at=datetime.now(timezone.utc).isoformat(),
            close_time=signal_dict.get('close_time', ''),
        )
        self.open_positions[pid] = pos
        self.cash -= total_cost
        self.cum_fees += qty * (fee_lo + fee_hi)
        return pos

    def settle_position(self, position_id: str, btc_at_close: float) -> Optional[T1Position]:
        pos = self.open_positions.pop(position_id, None)
        if pos is None: return None
        # leg_lo wins if BTC > strike_lo
        leg_lo_settles = 1.0 if btc_at_close > pos.strike_lo else 0.0
        # leg_hi wins if BTC ≤ strike_hi
        leg_hi_settles = 1.0 if btc_at_close <= pos.strike_hi else 0.0
        # Payout = qty × (leg_lo_settles + leg_hi_settles)
        payout = pos.qty * (leg_lo_settles + leg_hi_settles)
        realized = payout - pos.total_cost
        self.cash += payout
        self.realized_pnl += realized
        self.daily_pnl += realized
        self.closed_count += 1
        pos.settle_btc = btc_at_close
        pos.realized_pnl = realized
        pos.settled_at = datetime.now(timezone.utc).isoformat()
        self.check_halt()
        return pos

    def to_state(self) -> dict:
        return {
            'mode': 'paper',
            'strategy': 't1_monotonicity_arb',
            'started_at': self.started_at,
            'starting_bankroll': self.starting_bankroll,
            'cash': round(self.cash, 4),
            'realized_pnl': round(self.realized_pnl, 4),
            'cum_fees': round(self.cum_fees, 4),
            'open_positions_count': len(self.open_positions),
            'open_positions': [asdict(p) for p in self.open_positions.values()],
            'closed_trades_count': self.closed_count,
            'daily_pnl': round(self.daily_pnl, 4),
            'daily_pnl_date': self.daily_pnl_date,
            'halted': self.halted,
            'open_exposure': round(self.open_exposure(), 4),
            'nav': round(self.cash + self.open_exposure(), 4),
        }

    def save(self, path: Path):
        tmp = path.with_suffix(path.suffix + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(self.to_state(), f, indent=2, default=str)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        with open(path) as f:
            s = json.load(f)
        p = cls()
        p.starting_bankroll = s.get('starting_bankroll', CFG['starting_bankroll'])
        p.cash = s['cash']
        p.realized_pnl = s.get('realized_pnl', 0.0)
        p.cum_fees = s.get('cum_fees', 0.0)
        p.closed_count = s.get('closed_trades_count', 0)
        p.daily_pnl = s.get('daily_pnl', 0.0)
        p.daily_pnl_date = s.get('daily_pnl_date',
                                  datetime.now(timezone.utc).date().isoformat())
        p.halted = s.get('halted', False)
        p.started_at = s.get('started_at', p.started_at)
        for d in s.get('open_positions', []):
            p.open_positions[d['position_id']] = T1Position(**d)
        return p
