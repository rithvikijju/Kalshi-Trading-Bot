"""
Order Blocks (OB) — the last opposing candle before an impulsive displacement.

Bullish OB: the last DOWN candle immediately before an up-displacement that breaks
structure. Institutions are presumed to have absorbed orders there; price often returns
to the block and reacts. Bearish OB is the mirror (last up candle before down-displacement).

We require the displacement that follows the block to (a) be impulsive (body >> ATR) and
(b) take out the block's range, which filters the endless "every candle is an OB" noise.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..broker.base import Bar
from .structure import atr


@dataclass
class OrderBlock:
    direction: int        # +1 bullish (demand), -1 bearish (supply)
    top: float
    bottom: float
    idx: int              # index of the order-block candle
    ts: float
    mitigated: bool = False

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


def find_order_blocks(bars: list[Bar], n_atr: int = 14, disp_thresh: float = 1.5,
                      lookahead: int = 3) -> list[OrderBlock]:
    out: list[OrderBlock] = []
    if len(bars) < n_atr + lookahead + 2:
        return out
    for i in range(1, len(bars) - lookahead):
        a = atr(bars[max(0, i - n_atr):i], n_atr)
        if a <= 0:
            continue
        ob = bars[i]
        # bullish OB: a down candle followed within `lookahead` by an up-displacement
        if not ob.bull:
            for j in range(i + 1, i + 1 + lookahead):
                d = bars[j]
                if d.bull and d.body >= disp_thresh * a and d.c > ob.h:
                    out.append(OrderBlock(+1, ob.h, ob.l, i, ob.ts))
                    break
        # bearish OB: an up candle followed by a down-displacement
        if ob.bull:
            for j in range(i + 1, i + 1 + lookahead):
                d = bars[j]
                if not d.bull and d.body >= disp_thresh * a and d.c < ob.l:
                    out.append(OrderBlock(-1, ob.h, ob.l, i, ob.ts))
                    break
    return out


def update_mitigation(obs: list[OrderBlock], bars: list[Bar]) -> None:
    for ob in obs:
        if ob.mitigated:
            continue
        for j in range(ob.idx + 2, len(bars)):
            b = bars[j]
            if b.l <= ob.top and b.h >= ob.bottom:
                ob.mitigated = True
                break


def active_order_blocks(bars: list[Bar], **kw) -> list[OrderBlock]:
    obs = find_order_blocks(bars, **kw)
    update_mitigation(obs, bars)
    return [o for o in obs if not o.mitigated]
