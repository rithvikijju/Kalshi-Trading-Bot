"""
Broker abstraction + the shared market/order data contracts.

Both the live ProjectX adapter and the paper Sim broker implement `Broker`, so the
trader loop is identical in sim and live — only the wiring at startup differs.

All callbacks are async and registered via `on_quote` / `on_trade` / `on_fill` /
`on_position` so the trader can react event-driven.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Awaitable, Callable, Optional


# ----------------------------------------------------------------- enums (match ProjectX)
class OrderType(IntEnum):
    LIMIT = 1
    MARKET = 2
    STOP = 4
    TRAILING_STOP = 5
    JOIN_BID = 6
    JOIN_ASK = 7


class Side(IntEnum):
    BUY = 0    # ProjectX: 0 = Bid
    SELL = 1   # ProjectX: 1 = Ask

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


# ----------------------------------------------------------------- market data
@dataclass
class Quote:
    instrument: str
    bid: float
    ask: float
    last: float
    volume: float
    ts: float                      # epoch seconds

    @property
    def mid(self) -> float:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread(self) -> float:
        return (self.ask - self.bid) if (self.bid and self.ask) else 0.0


@dataclass
class Depth:
    """A DOM snapshot: top levels of resting size on each side (price→size), best first.
    This is the order-book data real microstructure edges live in."""
    instrument: str
    bids: list                     # [(price, size), ...] best→worse
    asks: list                     # [(price, size), ...] best→worse
    ts: float

    def imbalance(self, levels: int = 5) -> float:
        """(bid_size - ask_size) / (bid_size + ask_size) over top `levels`. +1 = bid-heavy."""
        b = sum(s for _, s in self.bids[:levels])
        a = sum(s for _, s in self.asks[:levels])
        return (b - a) / (b + a) if (b + a) > 0 else 0.0

    def micro_price(self) -> float:
        """Size-weighted mid: leans toward the heavier side (a fairer 'true' price)."""
        if not self.bids or not self.asks:
            return 0.0
        bp, bs = self.bids[0]; ap, as_ = self.asks[0]
        tot = bs + as_
        return (bp * as_ + ap * bs) / tot if tot > 0 else (bp + ap) / 2.0


@dataclass
class Trade:
    instrument: str
    price: float
    volume: float
    aggressor: int                 # +1 buy-side, -1 sell-side, 0 unknown
    ts: float


@dataclass
class Bar:
    instrument: str
    ts: float                      # epoch seconds of the bar OPEN
    o: float
    h: float
    l: float
    c: float
    v: float
    closed: bool = True

    @property
    def range(self) -> float:
        return self.h - self.l

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def bull(self) -> bool:
        return self.c >= self.o


# ----------------------------------------------------------------- order/position
@dataclass
class Order:
    instrument: str
    side: Side
    size: int
    type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    # bracket: protective stop + target attached at entry (ProjectX supports natively)
    bracket_stop: Optional[float] = None
    bracket_target: Optional[float] = None
    tag: str = ""
    # filled by broker
    order_id: Optional[int] = None
    status: str = "pending"        # pending|working|filled|cancelled|rejected
    avg_fill: Optional[float] = None


@dataclass
class Position:
    instrument: str
    size: int = 0                  # signed: + long, - short, 0 flat
    avg_price: float = 0.0
    realized: float = 0.0          # realized $ PnL this position lifetime

    @property
    def flat(self) -> bool:
        return self.size == 0

    @property
    def side(self) -> Optional[Side]:
        if self.size > 0:
            return Side.BUY
        if self.size < 0:
            return Side.SELL
        return None


@dataclass
class Fill:
    instrument: str
    side: Side
    size: int
    price: float
    pnl: float                     # realized $ on this fill (0 for opens)
    fees: float
    ts: float = field(default_factory=lambda: time.time())
    tag: str = ""


# ----------------------------------------------------------------- contract metadata
@dataclass
class ContractMeta:
    instrument: str
    contract_id: str               # ProjectX contractId (string)
    tick_size: float
    tick_value: float
    name: str = ""


# Callback type aliases
QuoteCb = Callable[[Quote], Awaitable[None]]
TradeCb = Callable[[Trade], Awaitable[None]]
DepthCb = Callable[[Depth], Awaitable[None]]
FillCb = Callable[[Fill], Awaitable[None]]
PositionCb = Callable[[Position], Awaitable[None]]


class Broker:
    """Abstract broker. Implementations: ProjectXBroker (live), SimBroker (paper)."""

    def __init__(self):
        self._quote_cbs: list[QuoteCb] = []
        self._trade_cbs: list[TradeCb] = []
        self._depth_cbs: list[DepthCb] = []
        self._fill_cbs: list[FillCb] = []
        self._position_cbs: list[PositionCb] = []
        self.contracts: dict[str, ContractMeta] = {}     # instrument key -> meta
        self.positions: dict[str, Position] = {}

    # ---- subscription of internal listeners (trader registers here) ----
    def on_quote(self, cb: QuoteCb):
        self._quote_cbs.append(cb)

    def on_trade(self, cb: TradeCb):
        self._trade_cbs.append(cb)

    def on_depth(self, cb: DepthCb):
        self._depth_cbs.append(cb)

    def on_fill(self, cb: FillCb):
        self._fill_cbs.append(cb)

    def on_position(self, cb: PositionCb):
        self._position_cbs.append(cb)

    async def _emit_quote(self, q: Quote):
        for cb in self._quote_cbs:
            await cb(q)

    async def _emit_trade(self, t: Trade):
        for cb in self._trade_cbs:
            await cb(t)

    async def _emit_depth(self, d: Depth):
        for cb in self._depth_cbs:
            await cb(d)

    async def _emit_fill(self, f: Fill):
        for cb in self._fill_cbs:
            await cb(f)

    async def _emit_position(self, p: Position):
        for cb in self._position_cbs:
            await cb(p)

    # ---- lifecycle (override) ----
    async def connect(self):
        raise NotImplementedError

    async def disconnect(self):
        raise NotImplementedError

    async def subscribe(self, instruments: list[str]):
        raise NotImplementedError

    # ---- trading (override) ----
    async def place(self, order: Order) -> Order:
        raise NotImplementedError

    async def cancel(self, order_id: int):
        raise NotImplementedError

    async def flatten(self, instrument: str):
        """Market-close any open position in `instrument`."""
        raise NotImplementedError

    async def flatten_all(self):
        for inst in list(self.positions.keys()):
            await self.flatten(inst)

    # ---- helpers ----
    def position(self, instrument: str) -> Position:
        return self.positions.setdefault(instrument, Position(instrument))

    def meta(self, instrument: str) -> Optional[ContractMeta]:
        return self.contracts.get(instrument)
