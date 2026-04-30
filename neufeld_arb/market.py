"""Market dataclasses and sample construction (paper Section 3.1.1).

A `Market` corresponds to one (K, π) realisation: a basket of d
underlying stocks each with `n_per_asset` traded vanilla calls (the
underlying itself is encoded as a strike-0 call). The paper draws
50 000 such markets by sampling 5 random S&P 500 constituents per
sample on a single observation day.

We provide:

    OptionsBundle  - per-stock chain: spot, strikes, bid, ask
    Market         - flattened (K, π, asset_idx) tuple ready for the NN
    sample_market  - draw `d` bundles from a pool, take top-`n_per_asset`
                     liquid options each, scale by spot
    scale_to_unit_spot - normalise an existing OptionsBundle so spot=1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


@dataclass
class OptionsBundle:
    """One stock's snapshot: spot price + a chain of vanilla calls.

    All arrays are 1-D and aligned by index. The `volume` field is used
    to pick the most-liquid options when the chain is larger than the
    target `n_per_asset`.
    """

    ticker: str
    spot: float
    strikes: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    volume: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def __post_init__(self):
        # Defensive: drop non-positive bid or ask, NaNs, zero strikes after warning.
        keep = np.isfinite(self.bid) & np.isfinite(self.ask) & (self.ask > 0)
        if not keep.all():
            self.strikes = self.strikes[keep]
            self.bid = self.bid[keep]
            self.ask = self.ask[keep]
            if len(self.volume):
                self.volume = self.volume[keep]
        if len(self.volume) == 0:
            self.volume = np.zeros_like(self.strikes)
        if not np.all(self.bid <= self.ask):
            # Crossed quotes happen on stale or thin chains; clamp bid to ask.
            self.bid = np.minimum(self.bid, self.ask)


@dataclass
class Market:
    """Flattened multi-asset market ready for the NN.

    `K` and `pi_ask`/`pi_bid` are length-N arrays; `asset_idx[k]` says
    which underlying option k is written on (in 0..d-1). `spot` is the
    per-asset reference value used for unscaling later.
    """

    tickers: tuple[str, ...]
    spot: np.ndarray            # [d]
    K: np.ndarray               # [N] strikes (already in unit-spot terms)
    pi_ask: np.ndarray          # [N]
    pi_bid: np.ndarray          # [N]
    asset_idx: np.ndarray       # [N], values in 0..d-1
    n_per_asset: int

    @property
    def d(self) -> int:
        return int(self.spot.shape[0])

    @property
    def N(self) -> int:
        return int(self.K.shape[0])

    def feature_vector(self) -> np.ndarray:
        """Concatenated input expected by the NN: [K | π+ | π-] of size 3N."""
        return np.concatenate([self.K, self.pi_ask, self.pi_bid]).astype(np.float32)


def scale_to_unit_spot(b: OptionsBundle) -> OptionsBundle:
    """Return a copy with strikes/bid/ask divided by spot so the new spot is 1."""
    s = b.spot
    return OptionsBundle(
        ticker=b.ticker,
        spot=1.0,
        strikes=b.strikes / s,
        bid=b.bid / s,
        ask=b.ask / s,
        volume=b.volume,
    )


def _top_liquid(b: OptionsBundle, n: int) -> OptionsBundle:
    """Keep the top-n strikes by volume, falling back to closest-to-spot if no volume info."""
    if b.strikes.size <= n:
        return b
    if b.volume.sum() > 0:
        order = np.argsort(-b.volume)[:n]
    else:
        # Closest-to-spot in moneyness.
        order = np.argsort(np.abs(b.strikes - b.spot))[:n]
    order = np.sort(order)  # preserve strike ordering
    return OptionsBundle(
        ticker=b.ticker,
        spot=b.spot,
        strikes=b.strikes[order],
        bid=b.bid[order],
        ask=b.ask[order],
        volume=b.volume[order],
    )


def sample_market(
    bundles: Sequence[OptionsBundle],
    d: int = 5,
    n_per_asset: int = 11,
    rng: np.random.Generator | None = None,
    include_underlying: bool = True,
) -> Market:
    """Pick `d` random bundles, take the top-`n_per_asset` calls each, build a Market.

    The underlying itself is included as a call with strike 0 when
    `include_underlying=True` (paper Section 3.1.1: "ni = 11 ... call
    options plus the underlying assets which can be considered as a
    call option with strike 0"). After this, `n_per_asset = 1 + (n - 1)`
    = the count requested, so we need `n_per_asset - 1` real calls.
    """
    if rng is None:
        rng = np.random.default_rng()
    if len(bundles) < d:
        raise ValueError(f"need at least {d} bundles, got {len(bundles)}")
    n_calls = n_per_asset - 1 if include_underlying else n_per_asset
    chosen = rng.choice(len(bundles), size=d, replace=False)

    K_parts, ask_parts, bid_parts, idx_parts = [], [], [], []
    spots = np.zeros(d, dtype=np.float64)
    tickers = []
    for new_i, raw in enumerate(chosen):
        b = bundles[raw]
        spots[new_i] = b.spot
        tickers.append(b.ticker)
        # Scale and pick top-liquid CALLS first.
        bs = scale_to_unit_spot(b)
        bs = _top_liquid(bs, n_calls)
        if bs.strikes.size < n_calls:
            # Pad by repeating the last quote so dimensionality is consistent.
            pad = n_calls - bs.strikes.size
            bs = OptionsBundle(
                ticker=bs.ticker, spot=bs.spot,
                strikes=np.concatenate([bs.strikes, np.full(pad, bs.strikes[-1] if bs.strikes.size else 1.0)]),
                bid=np.concatenate([bs.bid, np.full(pad, bs.bid[-1] if bs.bid.size else 0.0)]),
                ask=np.concatenate([bs.ask, np.full(pad, bs.ask[-1] if bs.ask.size else 0.0)]),
                volume=np.concatenate([bs.volume, np.zeros(pad)]),
            )

        if include_underlying:
            # Strike 0 call on a unit-spot underlying: bid=ask=spot=1.
            K_parts.append(np.concatenate([[0.0], bs.strikes]))
            ask_parts.append(np.concatenate([[1.0], bs.ask]))
            bid_parts.append(np.concatenate([[1.0], bs.bid]))
            idx_parts.append(np.full(n_per_asset, new_i, dtype=np.int64))
        else:
            K_parts.append(bs.strikes)
            ask_parts.append(bs.ask)
            bid_parts.append(bs.bid)
            idx_parts.append(np.full(n_per_asset, new_i, dtype=np.int64))

    return Market(
        tickers=tuple(tickers),
        spot=spots,
        K=np.concatenate(K_parts).astype(np.float64),
        pi_ask=np.concatenate(ask_parts).astype(np.float64),
        pi_bid=np.concatenate(bid_parts).astype(np.float64),
        asset_idx=np.concatenate(idx_parts).astype(np.int64),
        n_per_asset=n_per_asset,
    )
