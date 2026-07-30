"""Portfolio + position tracking for K6 paper/live runners.

A position is a single open contract bundle on one (market_ticker, side).
Closed positions become trades. PnL is realized at Kalshi market settlement.

Saved state schema (paper_state.json / live_state.json):
{
  "mode": "paper" | "live",
  "started_at": ISO ts,
  "starting_bankroll": 100.0,
  "cash": 73.50,
  "realized_pnl": 4.30,
  "cum_fees": 0.75,
  "open_positions": [Position, ...],
  "closed_trades_count": 47,
  "daily_pnl": -1.20,
  "halted": false
}
"""
from __future__ import annotations
import json, math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import CFG, kalshi_fee


@dataclass
class Position:
    position_id:   str
    market_ticker: str
    event_ticker:  str
    strike:        float
    side:          str
    bucket:        str
    qty:           int
    entry_price:   float
    entry_fee:     float
    entry_ts:      str           # ISO
    close_time:    str           # ISO, market settlement time
    spot_at_entry: float
    spot_dist_at_entry: float
    rv_at_entry:   float
    # Filled in at settlement:
    settle_value:  Optional[float] = None     # 0 or 1
    realized_pnl:  Optional[float] = None
    settled_at:    Optional[str]   = None


class Portfolio:
    def __init__(self, mode: str = 'paper'):
        self.mode = mode
        self.starting_bankroll = CFG['starting_bankroll']
        self.cash = CFG['starting_bankroll']
        self.realized_pnl = 0.0
        self.cum_fees = 0.0
        self.open_positions: Dict[str, Position] = {}
        self.closed_count = 0
        self.daily_pnl = 0.0
        self.daily_pnl_date = datetime.now(timezone.utc).date().isoformat()
        self.halted = False
        self.started_at = datetime.now(timezone.utc).isoformat()

    # ──────────────────────────────────────────────────────────────
    # Capital math
    # ──────────────────────────────────────────────────────────────
    def open_exposure(self) -> float:
        return sum(p.qty * p.entry_price for p in self.open_positions.values())

    def available_bankroll(self) -> float:
        return max(0.0, self.cash - self.open_exposure() * 0)  # cash already debited at open

    def can_open(self, qty: int, ask: float) -> bool:
        if self.halted: return False
        cost = qty * ask + qty * kalshi_fee(ask)
        if cost > self.cash: return False
        if self.open_exposure() + qty * ask > CFG['max_total_exposure']: return False
        if len(self.open_positions) >= CFG['max_concurrent_positions']: return False
        return True

    # ──────────────────────────────────────────────────────────────
    # Daily halt logic
    # ──────────────────────────────────────────────────────────────
    def _rollover_daily(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.daily_pnl_date:
            self.daily_pnl_date = today
            self.daily_pnl = 0.0
            # Don't auto-unhalt — operator must clear it.

    def check_daily_halt(self):
        self._rollover_daily()
        if self.daily_pnl <= CFG['daily_loss_limit']:
            self.halted = True

    # ──────────────────────────────────────────────────────────────
    # Open / settle
    # ──────────────────────────────────────────────────────────────
    def open_position(self, signal_dict: Dict[str, Any], fill_price: float,
                       fill_qty: int) -> Optional[Position]:
        """Record a new position. Debits cash. Returns the Position or None
        if it can't be opened (insufficient bankroll, halted, etc.)."""
        self._rollover_daily()
        if not self.can_open(fill_qty, fill_price):
            return None
        fee = kalshi_fee(fill_price)
        cost = fill_qty * fill_price + fill_qty * fee
        pid = f"k6-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{signal_dict['market_ticker']}"
        pos = Position(
            position_id=pid,
            market_ticker=signal_dict['market_ticker'],
            event_ticker=signal_dict.get('event_ticker', ''),
            strike=float(signal_dict['strike']),
            side=signal_dict['side'],
            bucket=signal_dict.get('bucket', ''),
            qty=int(fill_qty),
            entry_price=float(fill_price),
            entry_fee=float(fee),
            entry_ts=datetime.now(timezone.utc).isoformat(),
            close_time=signal_dict.get('close_time', ''),
            spot_at_entry=float(signal_dict.get('spot', 0)),
            spot_dist_at_entry=float(signal_dict.get('spot_dist', 0)),
            rv_at_entry=float(signal_dict.get('rv_15m_annual', 0)),
        )
        self.open_positions[pid] = pos
        self.cash -= cost
        self.cum_fees += fill_qty * fee
        return pos

    def settle_position(self, position_id: str, btc_at_close: float) -> Optional[Position]:
        pos = self.open_positions.pop(position_id, None)
        if pos is None: return None
        # YES wins if BTC > strike at close
        yes_wins = 1.0 if btc_at_close > pos.strike else 0.0
        settle = yes_wins if pos.side == 'yes' else (1.0 - yes_wins)
        # Each contract pays $1 if it settles in our favor
        payout = pos.qty * settle
        # We already paid cost (entry_price + fee) at open; receive payout now
        cost_total = pos.qty * pos.entry_price + pos.qty * pos.entry_fee
        realized = payout - cost_total
        self.cash += payout
        self.realized_pnl += realized
        self.daily_pnl += realized
        self.closed_count += 1
        pos.settle_value = settle
        pos.realized_pnl = realized
        pos.settled_at = datetime.now(timezone.utc).isoformat()
        self.check_daily_halt()
        return pos

    # ──────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────
    def to_state(self) -> dict:
        return {
            'mode': self.mode,
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
    def load(cls, path: Path, mode: str = 'paper') -> 'Portfolio':
        if not path.exists():
            return cls(mode=mode)
        with open(path) as f:
            s = json.load(f)
        p = cls(mode=mode)
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
            pos = Position(**d)
            p.open_positions[pos.position_id] = pos
        return p
