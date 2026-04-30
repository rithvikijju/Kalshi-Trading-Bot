"""Live market-data adapter: pulls top-of-book quotes from a ccxt exchange and
emits FXSnapshots compatible with the rest of the pipeline.

For each ordered pair of assets (i, j) we look for a tradeable symbol on the
venue. Two cases:
    * "buy"  side: the venue lists `j/i` (e.g. BTC/USDT). Going from i -> j
      means buying j; we cross the ASK and receive 1/ask_book units of j per
      unit of i.
    * "sell" side: the venue lists `i/j` (e.g. ETH/BTC when i=ETH, j=BTC).
      Going i -> j means selling i; we cross the BID and receive bid_book
      units of j per unit of i.

`rates_bid[i, j]` always stores the executable rate (best side for the
trader). `rates_ask[i, j]` stores the worse side, used only for the
spread feature in the GNN's edge attributes. Pairs that aren't directly
listed are skipped — triangles passing through them are unreachable, and
the cycle ranker will avoid them because the rate stays at 1.0 by
default which makes the cycle product trivially unprofitable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from gnn_arbitrage.data import FXSnapshot


@dataclass
class PairInfo:
    symbol: str       # e.g. "BTC/USDT"
    side: str         # "buy" (we use base=i to buy quote=j) or "sell"
    taker_fee: float  # fractional, e.g. 0.0026 for 26 bps


class CCXTFeed:
    def __init__(
        self,
        exchange: str = "kraken",
        currencies: tuple[str, ...] = ("USDT", "BTC", "ETH", "SOL", "XRP"),
        poll_seconds: float = 2.0,
        rate_limit: bool = True,
    ):
        import ccxt  # local import so the module is optional
        if not hasattr(ccxt, exchange):
            raise ValueError(f"ccxt has no exchange named {exchange!r}")
        self.ex = getattr(ccxt, exchange)({"enableRateLimit": rate_limit})
        self.exchange_name = exchange
        self.currencies = tuple(currencies)
        self.poll = poll_seconds
        self.t = 0
        self._build_pair_map()

    # Internals -----------------------------------------------------------

    def _build_pair_map(self) -> None:
        self.ex.load_markets()
        self.pair_map: dict[tuple[int, int], PairInfo] = {}
        symbols = set(self.ex.symbols or [])
        markets = self.ex.markets

        def fee_for(symbol: str) -> float:
            m = markets.get(symbol, {}) or {}
            return float(m.get("taker") or 0.001)

        for i, ci in enumerate(self.currencies):
            for j, cj in enumerate(self.currencies):
                if i == j:
                    continue
                # Prefer the j/i listing: i used to BUY j at ask.
                buy_sym = f"{cj}/{ci}"
                sell_sym = f"{ci}/{cj}"
                if buy_sym in symbols:
                    self.pair_map[(i, j)] = PairInfo(buy_sym, "buy", fee_for(buy_sym))
                elif sell_sym in symbols:
                    self.pair_map[(i, j)] = PairInfo(sell_sym, "sell", fee_for(sell_sym))

        missing = [
            (self.currencies[i], self.currencies[j])
            for i in range(len(self.currencies))
            for j in range(len(self.currencies))
            if i != j and (i, j) not in self.pair_map
        ]
        if missing:
            print(f"[CCXTFeed] no direct listing for: {missing}")

    # Public API ----------------------------------------------------------

    def fees(self) -> dict[tuple[int, int], float]:
        return {k: v.taker_fee for k, v in self.pair_map.items()}

    def symbols(self) -> list[str]:
        return sorted({pi.symbol for pi in self.pair_map.values()})

    def snapshot(self) -> FXSnapshot:
        symbols = self.symbols()
        try:
            tickers = self.ex.fetch_tickers(symbols)
        except Exception as e:  # graceful degradation
            print(f"[CCXTFeed] fetch_tickers failed: {e!r}; retrying once")
            time.sleep(1.0)
            tickers = self.ex.fetch_tickers(symbols)

        n = len(self.currencies)
        bid = np.ones((n, n), dtype=np.float64)
        ask = np.ones((n, n), dtype=np.float64)

        for (i, j), info in self.pair_map.items():
            t = tickers.get(info.symbol)
            if not t:
                continue
            tb = t.get("bid")
            ta = t.get("ask")
            if tb is None or ta is None or tb <= 0 or ta <= 0:
                continue
            if info.side == "buy":
                # i -> j by buying j at the ask of j/i: receive 1/ask units of j.
                bid[i, j] = 1.0 / ta
                ask[i, j] = 1.0 / tb
            else:
                # i -> j by selling i at the bid of i/j: receive bid units of j.
                bid[i, j] = tb
                ask[i, j] = ta

        np.fill_diagonal(bid, 1.0)
        np.fill_diagonal(ask, 1.0)
        snap = FXSnapshot(t=self.t, currencies=self.currencies, rates_bid=bid, rates_ask=ask)
        self.t += 1
        return snap

    def stream(self, max_iters: int | None = None) -> Iterator[FXSnapshot]:
        i = 0
        while max_iters is None or i < max_iters:
            yield self.snapshot()
            time.sleep(self.poll)
            i += 1
