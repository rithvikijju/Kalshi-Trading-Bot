"""Unified price feed: top-of-book when available, last-trade fallback otherwise.

The public Kalshi mirror returns a null orderbook for many live sports markets
(confirmed empirically — game-winner books come back empty while trades stream),
so we degrade gracefully to the last trade price. Paper/backtest only need a mid;
genuine LIVE execution needs a real book, which this flags via Quote.source.
"""
from __future__ import annotations
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clients.kalshi_client import KalshiClient
from strategies.mean_reversion.models import Quote
from utils.logger import setup_logger

log = setup_logger("mr.feed")


class PriceFeed:
    def __init__(self, kalshi: KalshiClient):
        self.k = kalshi

    async def get_quote(self, market_id: str) -> Quote | None:
        now = datetime.now(timezone.utc)
        # 1. Try the order book (gives real bid/ask + the side we'd transact at).
        try:
            px = await self.k.get_price(market_id)
            if px.yes_bid is not None or px.yes_ask is not None:
                return Quote(market_id=market_id, ts=now,
                             yes_bid=px.yes_bid, yes_ask=px.yes_ask,
                             source="orderbook")
        except Exception as e:
            log.debug(f"orderbook {market_id}: {e}")
        # 2. Fall back to last trade.
        last = await self.k.get_last_trade_price(market_id)
        if last is None:
            return None
        return Quote(market_id=market_id, ts=now, last=last, source="trade")

    async def get_quotes(self, market_ids: list[str]) -> dict[str, Quote]:
        results = await asyncio.gather(*(self.get_quote(m) for m in market_ids),
                                       return_exceptions=True)
        out: dict[str, Quote] = {}
        for mid, r in zip(market_ids, results):
            if isinstance(r, Quote):
                out[mid] = r
        return out
