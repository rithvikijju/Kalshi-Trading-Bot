"""In-memory paper broker. Default mode for all live runs.

Simulates fills at last_price plus slippage; persists positions and account
state to SQLite so a notebook restart doesn't lose the book.
"""
from __future__ import annotations
import json
import uuid
from typing import Callable, Optional
import pandas as pd

from .broker_base import Broker, Order, Position
from ..backtest.costs import CostModel
from ..data.storage import insert_many, query


class PaperBroker(Broker):
    name = "paper"
    paper = True

    def __init__(self, run_id: str, starting_cash: float = 100_000.0,
                 price_fn: Optional[Callable[[str], float]] = None,
                 costs: CostModel = CostModel()):
        self.run_id = run_id
        self.cash = starting_cash
        self.book: dict[str, Position] = {}
        self.price_fn = price_fn
        self.costs = costs

    def last_price(self, symbol: str) -> float:
        if self.price_fn is None:
            raise RuntimeError("paper broker has no price_fn configured")
        return float(self.price_fn(symbol))

    def account(self) -> dict:
        px = {s: self.last_price(s) for s in self.book}
        mv = sum(p.qty * px[s] for s, p in self.book.items())
        return dict(cash=self.cash, market_value=mv, equity=self.cash + mv,
                    positions=len(self.book))

    def positions(self) -> list[Position]:
        return list(self.book.values())

    def submit(self, order: Order) -> dict:
        px = self.last_price(order.symbol)
        signed_qty = order.signed_qty()
        notional = signed_qty * px
        fee = self.costs.equity_trade_cost(notional)
        if order.symbol not in self.book:
            self.book[order.symbol] = Position(symbol=order.symbol, qty=0.0, avg_px=0.0)
        p = self.book[order.symbol]
        new_qty = p.qty + signed_qty
        if abs(new_qty) < 1e-9:
            p.qty = 0.0; p.avg_px = 0.0
        else:
            if p.qty * signed_qty >= 0:
                p.avg_px = (p.qty * p.avg_px + signed_qty * px) / new_qty
            p.qty = new_qty
        self.cash -= signed_qty * px + fee
        ts = pd.Timestamp.utcnow().isoformat()
        order_id = uuid.uuid4().hex[:12]
        fill_id = uuid.uuid4().hex[:12]
        insert_many("orders", [dict(run_id=self.run_id, order_id=order_id, ts=ts,
                                      symbol=order.symbol, side=order.side, qty=order.qty,
                                      order_type=order.order_type, limit_price=order.limit_price,
                                      status="filled", reason=order.reason)])
        insert_many("fills", [dict(run_id=self.run_id, fill_id=fill_id, order_id=order_id,
                                     ts=ts, symbol=order.symbol, qty=signed_qty, price=px,
                                     fee=fee, slippage_bps=self.costs.slippage_bps)])
        return dict(order_id=order_id, status="filled", price=px, fee=fee, fill_qty=signed_qty)
