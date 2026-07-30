"""Alpaca paper / live broker. Default: paper. Live requires
SAFETY.can_trade_live AND the constructor flag live=True."""
from __future__ import annotations
from typing import Optional
import requests

from .broker_base import Broker, Order, Position
from ..config import KEYS, SAFETY


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, live: bool = False):
        if live and not SAFETY.can_trade_live:
            raise RuntimeError("Live trading refused — set LIVE_TRADING_ENABLED + I_UNDERSTAND_REAL_MONEY_RISK")
        if KEYS.alpaca_id is None or KEYS.alpaca_secret is None:
            raise RuntimeError("Alpaca keys missing")
        self.paper = not live
        self.base = KEYS.alpaca_paper_url if self.paper else "https://api.alpaca.markets"

    def _h(self):
        return {"APCA-API-KEY-ID": KEYS.alpaca_id, "APCA-API-SECRET-KEY": KEYS.alpaca_secret}

    def account(self) -> dict:
        r = requests.get(f"{self.base}/v2/account", headers=self._h(), timeout=15)
        r.raise_for_status()
        return r.json()

    def positions(self) -> list[Position]:
        r = requests.get(f"{self.base}/v2/positions", headers=self._h(), timeout=15)
        r.raise_for_status()
        return [Position(symbol=p["symbol"], qty=float(p["qty"]),
                          avg_px=float(p["avg_entry_price"])) for p in r.json()]

    def submit(self, order: Order) -> dict:
        body = dict(symbol=order.symbol, qty=str(order.qty), side=order.side,
                     type=order.order_type, time_in_force="day")
        if order.limit_price is not None:
            body["limit_price"] = str(order.limit_price)
        r = requests.post(f"{self.base}/v2/orders", json=body, headers=self._h(), timeout=15)
        r.raise_for_status()
        return r.json()

    def last_price(self, symbol: str) -> float:
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
                          headers=self._h(), timeout=10)
        r.raise_for_status()
        return float(r.json()["trade"]["p"])
