"""
Fair Value Gaps (FVG) — 3-candle imbalance.

Bullish FVG: bar[i-2].high < bar[i].low  → an untraded gap (low of i above high of i-2).
Bearish FVG: bar[i-2].low  > bar[i].high → gap to the downside.

The gap marks a price zone the market moved through too fast to fill; price often
returns ("mitigates") to it. We track each gap and whether it's been filled, so the
engine can look for entries on a retrace INTO an unmitigated gap aligned with structure.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..broker.base import Bar


@dataclass
class FVG:
    direction: int        # +1 bullish, -1 bearish
    top: float            # upper edge of the gap
    bottom: float         # lower edge of the gap
    idx: int              # index of the 3rd (middle-after) bar that created it
    ts: float
    mitigated: bool = False
    mitigated_idx: int | None = None

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


def find_fvgs(bars: list[Bar], min_size: float = 0.0) -> list[FVG]:
    """All FVGs in the window, newest last. min_size filters trivial gaps (in price units)."""
    out: list[FVG] = []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        if a.h < c.l and (c.l - a.h) > min_size:               # bullish gap
            out.append(FVG(+1, c.l, a.h, i, c.ts))
        elif a.l > c.h and (a.l - c.h) > min_size:             # bearish gap
            out.append(FVG(-1, a.l, c.h, i, c.ts))
    return out


def update_mitigation(fvgs: list[FVG], bars: list[Bar]) -> None:
    """Mark gaps mitigated once a later bar trades back into the zone."""
    for g in fvgs:
        if g.mitigated:
            continue
        for j in range(g.idx + 1, len(bars)):
            b = bars[j]
            if b.l <= g.top and b.h >= g.bottom:               # price re-entered the gap
                g.mitigated = True
                g.mitigated_idx = j
                break


def active_fvgs(bars: list[Bar], min_size: float = 0.0) -> list[FVG]:
    """Unmitigated FVGs only — the ones still likely to attract price."""
    g = find_fvgs(bars, min_size)
    update_mitigation(g, bars)
    return [x for x in g if not x.mitigated]
