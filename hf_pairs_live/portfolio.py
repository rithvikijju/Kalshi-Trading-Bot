"""Portfolio tracker for HF pairs trading."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from config import CFG


@dataclass
class PairPosition:
    position_id:   str
    direction:     str          # 'short_btc_long_eth' or 'long_btc_short_eth'
    entry_ts:      str
    entry_btc:     float
    entry_eth:     float
    entry_spread:  float
    entry_z:       float
    btc_qty:       float        # signed: + for long BTC, − for short BTC
    eth_qty:       float        # signed: + for long ETH, − for short ETH
    notional:      float        # per leg
    entry_fee:     float        # total entry cost (round-trip * notional)
    # Filled at close:
    exit_ts:       Optional[str] = None
    exit_btc:      Optional[float] = None
    exit_eth:      Optional[float] = None
    exit_z:        Optional[float] = None
    exit_reason:   Optional[str] = None
    realized_pnl:  Optional[float] = None


class Portfolio:
    def __init__(self):
        self.starting_bankroll = CFG['starting_bankroll']
        self.cash = CFG['starting_bankroll']
        self.realized_pnl = 0.0
        self.cum_fees = 0.0
        self.open_positions: Dict[str, PairPosition] = {}
        self.closed_trades_count = 0
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

    def can_open(self) -> bool:
        return (not self.halted
                and len(self.open_positions) < CFG['max_concurrent_positions']
                and self.cash > 2 * CFG['notional_per_leg'])

    def open_position(self, direction: str, btc_price: float, eth_price: float,
                       spread: float, z: float) -> Optional[PairPosition]:
        if not self.can_open():
            return None
        self._rollover_daily()
        notional = CFG['notional_per_leg']
        if direction == 'short_btc_long_eth':
            btc_qty = -notional / btc_price
            eth_qty = +notional / eth_price
        else:
            btc_qty = +notional / btc_price
            eth_qty = -notional / eth_price
        # Round-trip cost: 2 legs at entry (we book entry cost only — exit charged on close)
        entry_cost = 2 * notional * (CFG['taker_fee'] + CFG['slippage_bps'] / 10000)
        pid = f"hfp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        pos = PairPosition(
            position_id=pid, direction=direction,
            entry_ts=datetime.now(timezone.utc).isoformat(),
            entry_btc=btc_price, entry_eth=eth_price,
            entry_spread=spread, entry_z=z,
            btc_qty=btc_qty, eth_qty=eth_qty,
            notional=notional, entry_fee=entry_cost,
        )
        self.open_positions[pid] = pos
        self.cash -= entry_cost   # debit only the entry fees (notional sits as MTM)
        self.cum_fees += entry_cost
        return pos

    def close_position(self, position_id: str, btc_price: float, eth_price: float,
                        z: float, reason: str) -> Optional[PairPosition]:
        pos = self.open_positions.pop(position_id, None)
        if pos is None:
            return None
        # MTM PnL
        btc_pnl = (btc_price - pos.entry_btc) * pos.btc_qty
        eth_pnl = (eth_price - pos.entry_eth) * pos.eth_qty
        gross = btc_pnl + eth_pnl
        # Exit fees
        exit_fee = 2 * pos.notional * (CFG['taker_fee'] + CFG['slippage_bps'] / 10000)
        net_pnl = gross - exit_fee
        self.cash += net_pnl
        self.realized_pnl += net_pnl
        self.daily_pnl += net_pnl
        self.cum_fees += exit_fee
        self.closed_trades_count += 1
        pos.exit_ts = datetime.now(timezone.utc).isoformat()
        pos.exit_btc = btc_price
        pos.exit_eth = eth_price
        pos.exit_z = z
        pos.exit_reason = reason
        pos.realized_pnl = net_pnl
        self.check_halt()
        return pos

    def to_state(self) -> dict:
        return {
            'started_at': self.started_at,
            'starting_bankroll': self.starting_bankroll,
            'cash': round(self.cash, 4),
            'realized_pnl': round(self.realized_pnl, 4),
            'cum_fees': round(self.cum_fees, 4),
            'open_positions_count': len(self.open_positions),
            'open_positions': [asdict(p) for p in self.open_positions.values()],
            'closed_trades_count': self.closed_trades_count,
            'daily_pnl': round(self.daily_pnl, 4),
            'daily_pnl_date': self.daily_pnl_date,
            'halted': self.halted,
            'nav': round(self.cash, 4),
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
        p.closed_trades_count = s.get('closed_trades_count', 0)
        p.daily_pnl = s.get('daily_pnl', 0.0)
        p.daily_pnl_date = s.get('daily_pnl_date',
                                  datetime.now(timezone.utc).date().isoformat())
        p.halted = s.get('halted', False)
        p.started_at = s.get('started_at', p.started_at)
        for d in s.get('open_positions', []):
            p.open_positions[d['position_id']] = PairPosition(**d)
        return p
