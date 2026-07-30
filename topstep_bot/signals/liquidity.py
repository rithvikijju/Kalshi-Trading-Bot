"""
Liquidity pools and stop sweeps.

ICT premise: resting stop orders cluster just beyond obvious swing highs/lows and at
"equal highs/lows". Price is drawn to those pools, sweeps them (a quick spike that takes
out the level then rejects), and reverses — a "liquidity grab" / stop run. The sweep +
rejection + return inside the prior range is one of the higher-quality reversal triggers.

  * Liquidity pool : a swing extreme or a cluster of near-equal highs/lows (stops above
                     highs = "buy-side liquidity"; below lows = "sell-side liquidity").
  * Sweep          : a bar whose wick pierces a pool but whose CLOSE returns back inside
                     → the level was taken and rejected.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..broker.base import Bar
from .structure import atr, swings


@dataclass
class LiquidityPool:
    side: str             # "buy" (above highs) | "sell" (below lows)
    price: float
    strength: int         # how many swings cluster here (equal highs/lows = stronger)


@dataclass
class Sweep:
    direction: int        # +1 swept sell-side lows then reversed UP; -1 swept highs, reversed DOWN
    level: float          # the pool level taken out
    idx: int              # bar that did the sweep
    ts: float
    rejection: float      # wick beyond the level (price units) — bigger = cleaner grab


def liquidity_pools(bars: list[Bar], k: int = 2, tol_frac: float = 0.10) -> list[LiquidityPool]:
    """Cluster swing highs/lows into pools. `tol_frac` of ATR defines 'equal' levels."""
    sw = swings(bars, k)
    tol = max(atr(bars) * tol_frac, 1e-9)
    pools: list[LiquidityPool] = []

    def cluster(levels: list[float], side: str):
        used = [False] * len(levels)
        for i, lv in enumerate(levels):
            if used[i]:
                continue
            group = [lv]
            used[i] = True
            for j in range(i + 1, len(levels)):
                if not used[j] and abs(levels[j] - lv) <= tol:
                    group.append(levels[j])
                    used[j] = True
            pools.append(LiquidityPool(side, sum(group) / len(group), len(group)))

    cluster([s.price for s in sw if s.kind == "high"], "buy")
    cluster([s.price for s in sw if s.kind == "low"], "sell")
    return pools


def detect_sweep(bars: list[Bar], pools: list[LiquidityPool], k: int = 2) -> Sweep | None:
    """Did the latest bar sweep a pool and reject (close back inside)?"""
    if len(bars) < 3:
        return None
    last = bars[-1]
    a = atr(bars[:-1])
    best: Sweep | None = None
    for p in pools:
        if p.side == "buy":            # stops above highs: sweep = wick above, close below
            if last.h > p.price and last.c < p.price:
                rej = last.h - p.price
                if rej > 0 and (best is None or rej > best.rejection):
                    best = Sweep(-1, p.price, len(bars) - 1, last.ts, rej)
        else:                           # sell-side: wick below low, close back above
            if last.l < p.price and last.c > p.price:
                rej = p.price - last.l
                if rej > 0 and (best is None or rej > best.rejection):
                    best = Sweep(+1, p.price, len(bars) - 1, last.ts, rej)
    # only count it if the rejection is meaningful relative to volatility
    if best and a > 0 and best.rejection >= 0.10 * a:
        return best
    return None
