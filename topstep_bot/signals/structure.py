"""
Market structure: swing points, BOS / CHoCH, and displacement.

ICT vocabulary, defined operationally so it's testable (not discretionary):
  * Swing high/low  : fractal pivot — extreme of a (2k+1)-bar window.
  * BOS (Break of Structure)        : close beyond the most recent swing IN trend
                                       direction → trend continuation.
  * CHoCH (Change of Character)      : close beyond the most recent swing AGAINST the
                                       prevailing trend → first sign of reversal.
  * Displacement                     : an impulsive bar/run whose body dwarfs recent
                                       ATR — the institutional footprint that creates
                                       fair-value gaps and validates order blocks.

These are the structural primitives the higher-level detectors (FVG, order blocks,
liquidity) and the setup engine consume.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..broker.base import Bar


@dataclass
class Swing:
    idx: int          # index into the bar window
    ts: float
    price: float
    kind: str         # "high" | "low"


def atr(bars: list[Bar], n: int = 14) -> float:
    """Average true range over the last n bars (Wilder-ish, simple mean)."""
    if len(bars) < 2:
        return bars[-1].range if bars else 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].h, bars[i].l, bars[i - 1].c
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-n:]
    return sum(window) / len(window) if window else 0.0


def swings(bars: list[Bar], k: int = 2) -> list[Swing]:
    """Fractal swing points. A swing high at i requires bar[i].h to be the strict max
    of the [i-k, i+k] window (mirror for lows). k=2 → classic 5-bar fractal."""
    out: list[Swing] = []
    n = len(bars)
    for i in range(k, n - k):
        win = bars[i - k:i + k + 1]
        hi, lo = bars[i].h, bars[i].l
        if hi == max(b.h for b in win) and sum(1 for b in win if b.h == hi) == 1:
            out.append(Swing(i, bars[i].ts, hi, "high"))
        if lo == min(b.l for b in win) and sum(1 for b in win if b.l == lo) == 1:
            out.append(Swing(i, bars[i].ts, lo, "low"))
    return out


def last_swing(swings_list: list[Swing], kind: str) -> Swing | None:
    for s in reversed(swings_list):
        if s.kind == kind:
            return s
    return None


@dataclass
class StructureState:
    trend: str               # "up" | "down" | "none"
    bos: bool                # latest close broke structure in trend direction
    choch: bool              # latest close broke structure against trend (reversal hint)
    broken_level: float      # the swing level that was broken (0 if none)
    last_high: float
    last_low: float


def structure_state(bars: list[Bar], k: int = 2) -> StructureState:
    """Infer trend from the sequence of swings and flag BOS / CHoCH on the latest close."""
    sw = swings(bars, k)
    highs = [s for s in sw if s.kind == "high"]
    lows = [s for s in sw if s.kind == "low"]
    last_h = highs[-1].price if highs else (bars[-1].h if bars else 0.0)
    last_l = lows[-1].price if lows else (bars[-1].l if bars else 0.0)

    # trend from the last two highs and last two lows (HH+HL = up, LH+LL = down)
    trend = "none"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            trend = "up"
        elif lh and ll:
            trend = "down"

    close = bars[-1].c if bars else 0.0
    bos = choch = False
    broken = 0.0
    # BOS: continuation. CHoCH: reversal (close beyond the swing opposite to trend).
    if trend == "up":
        if highs and close > highs[-1].price:
            bos, broken = True, highs[-1].price
        elif lows and close < lows[-1].price:
            choch, broken = True, lows[-1].price
    elif trend == "down":
        if lows and close < lows[-1].price:
            bos, broken = True, lows[-1].price
        elif highs and close > highs[-1].price:
            choch, broken = True, highs[-1].price
    else:
        # no established trend: a clean break of either side is a CHoCH (new character)
        if highs and close > highs[-1].price:
            choch, broken = True, highs[-1].price
        elif lows and close < lows[-1].price:
            choch, broken = True, lows[-1].price

    return StructureState(trend, bos, choch, broken, last_h, last_l)


@dataclass
class Displacement:
    idx: int
    direction: int       # +1 up, -1 down
    magnitude: float     # body / ATR
    start: float
    end: float


def displacement(bars: list[Bar], n_atr: int = 14, thresh: float = 1.8) -> Displacement | None:
    """Detect an impulsive last bar: body >= thresh * ATR. This is the move that leaves
    behind FVGs and validates order blocks."""
    if len(bars) < 3:
        return None
    a = atr(bars[:-1], n_atr)
    if a <= 0:
        return None
    last = bars[-1]
    mag = last.body / a
    if mag >= thresh:
        return Displacement(len(bars) - 1, 1 if last.bull else -1, mag, last.o, last.c)
    return None
