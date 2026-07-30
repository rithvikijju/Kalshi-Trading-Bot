"""
Trading sessions, killzones, and session liquidity levels (all in US/Eastern).

ICT leans heavily on time: liquidity built in one session (e.g. the Asian range) is often
taken in the next (London / NY). Killzones are the windows where the high-probability
moves cluster, so we only ENTER inside a killzone (configurable) and use prior-session
highs/lows as liquidity targets/draws.

Windows (ET) — standard ICT definitions:
  Asian range   20:00 → 00:00   (builds the range; its H/L are liquidity for London)
  London KZ     02:00 → 05:00
  NY AM KZ      08:30 → 11:00   (around the 09:30 cash open — the prime window)
  NY PM KZ      13:30 → 16:00
Lunch 12:00→13:00 is intentionally excluded (chop).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from ..broker.base import Bar

ET = ZoneInfo("America/New_York")

KILLZONES = {
    "london": (time(2, 0), time(5, 0)),
    "ny_am": (time(8, 30), time(11, 0)),
    "ny_pm": (time(13, 30), time(16, 0)),
}
ASIAN_RANGE = (time(20, 0), time(0, 0))   # wraps midnight


def et_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ET)


def current_killzone(ts: float) -> str | None:
    """Return the killzone name active at ts, or None."""
    t = et_dt(ts).time()
    for name, (start, end) in KILLZONES.items():
        if start <= t < end:
            return name
    return None


def in_killzone(ts: float) -> bool:
    return current_killzone(ts) is not None


def minutes_to_session_close(ts: float, close: time = time(16, 0)) -> float:
    """Minutes until the RTH equity-index close (16:00 ET). Negative after close.
    Used to flatten before close so we never carry overnight against TopStep rules."""
    dt = et_dt(ts)
    close_dt = dt.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
    return (close_dt - dt).total_seconds() / 60.0


@dataclass
class SessionLevels:
    asian_high: float = 0.0
    asian_low: float = 0.0
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    midnight_open: float = 0.0     # 00:00 ET open — ICT's daily bias pivot


def _in_window(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t < end
    return t >= start or t < end   # wraps midnight (Asian range)


def session_levels(bars: list[Bar]) -> SessionLevels:
    """Compute liquidity levels from the bar window: Asian range H/L, prior-day H/L,
    and the midnight (00:00 ET) open. These are the draws-on-liquidity the engine targets."""
    sl = SessionLevels()
    if not bars:
        return sl
    now = et_dt(bars[-1].ts)
    today = now.date()

    asian_hi, asian_lo = float("-inf"), float("inf")
    pd_hi, pd_lo = float("-inf"), float("inf")
    for b in bars:
        dt = et_dt(b.ts)
        t = dt.time()
        # Asian range belonging to the current ET day's overnight (prev 20:00 → today 00:00)
        if _in_window(t, *ASIAN_RANGE):
            asian_hi = max(asian_hi, b.h)
            asian_lo = min(asian_lo, b.l)
        # previous calendar day's full range
        if dt.date() < today:
            pd_hi = max(pd_hi, b.h)
            pd_lo = min(pd_lo, b.l)
        # midnight open: first bar at/after 00:00 ET today
        if dt.date() == today and t >= time(0, 0) and sl.midnight_open == 0.0:
            sl.midnight_open = b.o

    if asian_hi > float("-inf"):
        sl.asian_high, sl.asian_low = asian_hi, asian_lo
    if pd_hi > float("-inf"):
        sl.prev_day_high, sl.prev_day_low = pd_hi, pd_lo
    return sl
