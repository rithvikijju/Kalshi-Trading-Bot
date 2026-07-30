"""
SimBroker — paper trading against the LIVE feed (or a replay).

Routes market data from a read-only `data_source` broker (the live ProjectX market hub)
to the trader, but simulates all fills locally so no real orders are ever sent. This is
how we "deploy and validate in parallel" with zero account risk:

  * market/stop orders fill at the touch (+ optional 1-tick slippage)
  * attached brackets (protective stop + target) are monitored every quote and auto-close
    the position when touched — exactly like the live bracket would
  * round-turn cost (commission + spread) is charged on the closing fill

Promotion to live = swap SimBroker for ProjectXBroker in run.py. The trader code is identical.
"""
from __future__ import annotations

import time

from ..config import INSTRUMENTS
from .base import (Broker, ContractMeta, Fill, Order, OrderType, Position, Quote, Side,
                   Trade)


class SimBroker(Broker):
    def __init__(self, data_source: Broker | None = None, slippage_ticks: float = 1.0):
        super().__init__()
        self.data_source = data_source
        self.slippage_ticks = slippage_ticks
        self._last_quote: dict[str, Quote] = {}
        self._brackets: dict[str, dict] = {}     # instrument -> {stop, target, setup_id}
        self._entry_ts: dict[str, float] = {}
        self._next_id = 1

    async def connect(self):
        if self.data_source is not None:
            # pipe the live feed through to our listeners + our fill simulator
            self.data_source.on_quote(self._on_source_quote)
            self.data_source.on_trade(self._emit_trade)
            await self.data_source.connect()
            self.contracts = self.data_source.contracts
        # fallbacks so sizing works even before contract metadata resolves
        for k, inst in INSTRUMENTS.items():
            self.contracts.setdefault(
                k, ContractMeta(k, k, inst.tick_size, inst.tick_value, inst.key))

    async def disconnect(self):
        if self.data_source is not None:
            await self.data_source.disconnect()

    async def subscribe(self, instruments: list[str]):
        if self.data_source is not None:
            await self.data_source.subscribe(instruments)

    # ---- feed handling + bracket monitoring ----
    async def _on_source_quote(self, q: Quote):
        self._last_quote[q.instrument] = q
        await self._check_brackets(q)
        await self._emit_quote(q)

    async def _check_brackets(self, q: Quote):
        br = self._brackets.get(q.instrument)
        pos = self.positions.get(q.instrument)
        if not br or not pos or pos.flat:
            return
        hit = None
        if pos.side is Side.BUY:
            if q.bid <= br["stop"]:
                hit = br["stop"]
            elif q.ask >= br["target"]:
                hit = br["target"]
        else:
            if q.ask >= br["stop"]:
                hit = br["stop"]
            elif q.bid <= br["target"]:
                hit = br["target"]
        if hit is not None:
            await self._close_at(q.instrument, hit, tag=br.get("tag", "bracket"))

    # ---- order entry (simulated) ----
    async def place(self, order: Order) -> Order:
        q = self._last_quote.get(order.instrument)
        if q is None:
            order.status = "rejected"
            return order
        meta = self.contracts.get(order.instrument)
        slip = self.slippage_ticks * (meta.tick_size if meta else 0.0)
        if order.type in (OrderType.MARKET, OrderType.STOP):
            px = (q.ask + slip) if order.side is Side.BUY else (q.bid - slip)
        else:  # LIMIT — assume fill at the limit (optimistic; flagged in README)
            px = order.limit_price if order.limit_price else q.mid
        order.order_id = self._next_id
        self._next_id += 1
        order.avg_fill = px
        order.status = "filled"
        await self._apply_fill(order.instrument, order.side, order.size, px, order.tag)
        if order.bracket_stop is not None and order.bracket_target is not None:
            self._brackets[order.instrument] = dict(
                stop=order.bracket_stop, target=order.bracket_target, tag=order.tag)
            self._entry_ts[order.instrument] = time.time()
        return order

    async def _apply_fill(self, instrument: str, side: Side, size: int, price: float, tag: str):
        pos = self.position(instrument)
        meta = self.contracts.get(instrument)
        dollars_per_pt = (meta.tick_value / meta.tick_size) if meta and meta.tick_size else 1.0
        signed = side.sign * size
        pnl = 0.0
        fees = 0.0
        if pos.size == 0 or (pos.size > 0) == (signed > 0):
            # opening or adding
            new_size = pos.size + signed
            pos.avg_price = ((abs(pos.size) * pos.avg_price) + (size * price)) / max(abs(new_size), 1)
            pos.size = new_size
        else:
            # reducing/closing (possibly flipping)
            closing = min(abs(signed), abs(pos.size))
            pnl = (price - pos.avg_price) * (1 if pos.size > 0 else -1) * closing * dollars_per_pt
            inst = INSTRUMENTS.get(instrument)
            fees = (inst.rt_cost if inst else 0.0) * closing
            pos.realized += pnl - fees
            pos.size += signed
            if pos.size == 0:
                pos.avg_price = 0.0
                self._brackets.pop(instrument, None)
            elif (pos.size > 0) != (pos.size - signed > 0):
                pos.avg_price = price  # flipped: remainder opens at fill price
        await self._emit_fill(Fill(instrument, side, size, price, pnl, fees, tag=tag))
        await self._emit_position(pos)

    async def _close_at(self, instrument: str, price: float, tag: str):
        pos = self.positions.get(instrument)
        if not pos or pos.flat:
            return
        close_side = pos.side.opposite
        await self._apply_fill(instrument, close_side, abs(pos.size), price, tag)

    async def flatten(self, instrument: str):
        q = self._last_quote.get(instrument)
        pos = self.positions.get(instrument)
        if not pos or pos.flat or q is None:
            return
        px = q.bid if pos.side is Side.BUY else q.ask
        await self._close_at(instrument, px, tag="flatten")

    async def cancel(self, order_id: int):
        return  # simulated orders fill immediately; nothing resting to cancel
