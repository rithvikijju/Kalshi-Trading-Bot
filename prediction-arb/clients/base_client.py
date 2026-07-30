"""Unified async interface that both Kalshi and Polymarket clients implement."""
from __future__ import annotations
from abc import ABC, abstractmethod
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import (NormalizedMarket, NormalizedPrice, OrderBook,
                          OrderRequest, OrderResponse, Position, Platform, Side)


class PredictionMarketClient(ABC):
    """Async base interface. All trading + read operations are async for
    concurrency + low latency."""

    platform: Platform

    @abstractmethod
    async def close(self): ...

    # ─── Read-only ──────────────────────────────────────────────────
    @abstractmethod
    async def get_markets(self, status: str = "open", limit: int = 100,
                          category: str | None = None) -> list[NormalizedMarket]:
        ...

    @abstractmethod
    async def get_market(self, market_id: str) -> NormalizedMarket: ...

    @abstractmethod
    async def get_price(self, market_id: str,
                        token_id: str | None = None) -> NormalizedPrice: ...

    @abstractmethod
    async def get_orderbook(self, market_id: str,
                            side: Side = Side.YES,
                            depth: int = 10) -> OrderBook: ...

    # ─── Trading (raise NotImplementedError if no auth) ─────────────
    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResponse: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_balance_usd(self) -> float: ...
