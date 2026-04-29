"""Synthetic FX rate feed with realistic spreads and occasional arbitrage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np


@dataclass
class FXSnapshot:
    """One timestamped cross-section of bid/ask rates between currencies.

    rates_bid[i, j] is the bid price of currency j quoted in currency i (the
    rate at which a holder of i can sell j). rates_ask[i, j] is the ask. A
    trader converting i -> j receives rates_bid[i, j] units of j per unit of i
    when crossing the spread (we use the convention that 1 unit of i buys
    rates_bid[i, j] units of j on a market sell of j vs i; rates_ask is the
    reverse direction). Diagonal entries are 1.0.
    """

    t: int
    currencies: tuple[str, ...]
    rates_bid: np.ndarray
    rates_ask: np.ndarray

    @property
    def n(self) -> int:
        return len(self.currencies)

    def executable_rate(self, i: int, j: int) -> float:
        """Rate received converting one unit of currency i into currency j."""
        if i == j:
            return 1.0
        return float(self.rates_bid[i, j])


class SyntheticFXFeed:
    """Generates a stream of FXSnapshots.

    Mid prices follow correlated geometric Brownian motion. Inverse symmetry
    is enforced on mid prices so that no arbitrage exists in the mid book.
    Bid/ask spreads are added on top, and with a small probability we inject a
    transient mispricing on a triangle to seed exploitable arbitrage.
    """

    def __init__(
        self,
        currencies: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD"),
        seed: int = 0,
        vol: float = 0.0008,
        spread_bps: float = 2.0,
        arb_prob: float = 0.05,
        arb_bps: float = 25.0,
    ):
        self.currencies = currencies
        self.n = len(currencies)
        self.vol = vol
        self.spread = spread_bps * 1e-4
        self.arb_prob = arb_prob
        self.arb_bps = arb_bps * 1e-4
        self.rng = np.random.default_rng(seed)

        # Anchor mid prices to USD (currency 0). Other prices are random
        # positive levels; cross rates derive from these via the no-arb
        # identity rate(i, j) = level[j] / level[i].
        self._levels = np.exp(self.rng.normal(0.0, 0.5, size=self.n))
        self._levels[0] = 1.0
        self.t = 0

    def _mid_matrix(self) -> np.ndarray:
        # rate(i, j) = level[j] / level[i]: 1 unit of i buys this many units of j
        return self._levels[None, :] / self._levels[:, None]

    def _step_levels(self) -> None:
        shocks = self.rng.normal(0.0, self.vol, size=self.n)
        shocks[0] = 0.0  # USD numeraire
        self._levels = self._levels * np.exp(shocks)

    def _maybe_inject_arbitrage(self, mid: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
        """Multiplicatively perturb a single edge so a triangle has a free lunch.

        Returns the perturbed matrix and the list of triangles where the
        perturbation creates strict positive log-cycle profit.
        """
        if self.rng.random() > self.arb_prob or self.n < 3:
            return mid, []

        i, j = self.rng.choice(self.n, size=2, replace=False)
        # Bump rate(i, j) up by arb_bps; this breaks symmetry on that edge so
        # cycles through (i, j) become profitable in one direction.
        bump = 1.0 + self.arb_bps
        mid = mid.copy()
        mid[i, j] *= bump
        mid[j, i] = 1.0 / mid[i, j]

        triangles: list[tuple[int, int, int]] = []
        for k in range(self.n):
            if k in (i, j):
                continue
            triangles.append((int(i), int(j), int(k)))
        return mid, triangles

    def snapshot(self) -> FXSnapshot:
        mid = self._mid_matrix()
        mid, _ = self._maybe_inject_arbitrage(mid)
        # Symmetric half-spread on every off-diagonal entry.
        bid = mid * (1.0 - self.spread / 2.0)
        ask = mid * (1.0 + self.spread / 2.0)
        np.fill_diagonal(bid, 1.0)
        np.fill_diagonal(ask, 1.0)
        snap = FXSnapshot(t=self.t, currencies=self.currencies, rates_bid=bid, rates_ask=ask)
        self._step_levels()
        self.t += 1
        return snap

    def stream(self, n: int) -> Iterator[FXSnapshot]:
        for _ in range(n):
            yield self.snapshot()
