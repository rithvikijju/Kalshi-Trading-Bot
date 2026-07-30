"""Portfolio book-keeping. Tracks cash, positions, and equity over time.

The contract for engines/strategies:
  - decide target_weights at signal time t (no future data).
  - on the NEXT bar (t+1), apply turnover at open price (configurable).
  - mark-to-market at close.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_px: float = 0.0
    asset_class: str = "equity"        # equity|crypto_spot|crypto_perp


@dataclass
class Portfolio:
    starting_cash: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.starting_cash

    def market_value(self, prices: dict[str, float]) -> float:
        v = 0.0
        for s, p in self.positions.items():
            px = prices.get(s)
            if px is None:
                continue
            v += p.qty * px
        return v

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return sum(abs(p.qty * prices.get(s, 0.0)) for s, p in self.positions.items())

    def net_exposure(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices.get(s, 0.0) for s, p in self.positions.items())

    def apply_fill(self, symbol: str, qty: float, price: float, fee: float,
                   asset_class: str = "equity"):
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol, asset_class=asset_class)
        p = self.positions[symbol]
        new_qty = p.qty + qty
        if abs(new_qty) < 1e-9:
            self.cash -= qty * price + fee
            p.qty = 0.0
            p.avg_px = 0.0
            return
        if p.qty * qty >= 0 and abs(p.qty) > 1e-9:
            p.avg_px = (p.qty * p.avg_px + qty * price) / new_qty
        elif p.qty * qty < 0 and abs(qty) <= abs(p.qty):
            pass  # partial close, avg_px unchanged
        else:
            p.avg_px = price
        p.qty = new_qty
        self.cash -= qty * price + fee

    def snapshot(self, ts, prices: dict[str, float]) -> dict:
        eq = self.equity(prices)
        gross = self.gross_exposure(prices)
        net = self.net_exposure(prices)
        snap = dict(
            ts=ts, cash=self.cash, equity=eq,
            gross_exposure=gross, net_exposure=net,
            leverage=gross / eq if eq > 0 else 0,
            positions={s: dict(qty=p.qty, avg_px=p.avg_px, asset=p.asset_class)
                       for s, p in self.positions.items()},
        )
        self.history.append(snap)
        return snap
