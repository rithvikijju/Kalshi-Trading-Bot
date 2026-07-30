"""Broker interface. Same shape for paper/live. The risk monitor wraps any
broker before execution. Live brokers refuse orders unless SAFETY.can_trade_live."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    symbol: str
    side: str                 # "buy" | "sell"
    qty: float
    order_type: str = "market"
    limit_price: Optional[float] = None
    reason: str = ""

    def signed_qty(self) -> float:
        return self.qty if self.side == "buy" else -self.qty


@dataclass
class Position:
    symbol: str
    qty: float
    avg_px: float


class Broker(ABC):
    name: str
    paper: bool = True

    @abstractmethod
    def account(self) -> dict: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def submit(self, order: Order) -> dict: ...

    @abstractmethod
    def last_price(self, symbol: str) -> float: ...
