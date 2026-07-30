"""Strategy-internal data models (dataclasses — light, no pydantic overhead).

Everything is expressed in the HELD SIDE's price space. Both signal types reduce
to the same shape: "buy a side we think is underpriced, expect its price to rise
back toward the baseline."
  - BUY_DIP   : YES dipped on a fast down-move  -> buy YES,  expect YES price up.
  - FADE_SPIKE: YES spiked on a fast up-move    -> buy NO,   expect NO  price up
                (NO = 1 - YES, so a YES reversion down is a NO reversion up).
"""
from __future__ import annotations
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from data.models import Side  # reuse the shared YES/NO enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SignalType(str, Enum):
    BUY_DIP = "buy_dip"        # oversold YES
    FADE_SPIKE = "fade_spike"  # overbought YES -> buy NO


@dataclass
class Quote:
    """Unified top-of-book snapshot for one market."""
    market_id: str
    ts: datetime
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    last: Optional[float] = None         # last trade YES price
    source: str = "orderbook"            # orderbook | trade

    @property
    def yes_mid(self) -> Optional[float]:
        if self.yes_bid is not None and self.yes_ask is not None:
            return (self.yes_bid + self.yes_ask) / 2
        return self.last                  # fall back to last trade when book is empty

    def side_ask(self, side: Side) -> Optional[float]:
        """Price we'd PAY to buy `side` (cross the spread)."""
        if side == Side.YES:
            if self.yes_ask is not None:
                return self.yes_ask
            return self.last
        # NO ask = 1 - YES bid
        if self.yes_bid is not None:
            return 1.0 - self.yes_bid
        return (1.0 - self.last) if self.last is not None else None

    def side_bid(self, side: Side) -> Optional[float]:
        """Price we'd RECEIVE to sell `side` (exit)."""
        if side == Side.YES:
            if self.yes_bid is not None:
                return self.yes_bid
            return self.last
        if self.yes_ask is not None:
            return 1.0 - self.yes_ask
        return (1.0 - self.last) if self.last is not None else None

    def side_mid(self, side: Side) -> Optional[float]:
        m = self.yes_mid
        if m is None:
            return None
        return m if side == Side.YES else 1.0 - m


@dataclass
class MarketMeta:
    """A market the strategy is watching, plus what the fair-value model needs."""
    market_id: str
    title: str = ""
    sport: Optional[str] = None          # "nba" | None (generic price-only)
    espn_event_id: Optional[str] = None
    team_abbr: Optional[str] = None       # the team this YES market resolves on
    is_home: Optional[bool] = None
    resolution_date: Optional[datetime] = None


@dataclass
class Signal:
    market_id: str
    type: SignalType
    side: Side                  # the side we BUY (YES for dip, NO for spike)
    entry_price: float          # held-side ask we'd pay
    target_price: float         # held-side take-profit (> entry)
    stop_price: float           # held-side stop (< entry)
    z: float
    spike_move: float
    expected_edge_cents: float  # net of round-trip fees + slippage
    fair_yes_prob: Optional[float] = None
    reason: str = ""
    ts: datetime = field(default_factory=_utcnow)


class PosStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class MRPosition:
    market_id: str
    side: Side
    size: float
    entry_price: float          # held-side fill price
    target_price: float
    stop_price: float
    signal_type: SignalType
    opened_at: datetime = field(default_factory=_utcnow)
    pos_id: Optional[int] = None
    status: PosStatus = PosStatus.OPEN
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    fair_yes_prob: Optional[float] = None
