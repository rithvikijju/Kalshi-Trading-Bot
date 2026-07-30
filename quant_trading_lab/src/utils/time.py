"""Time helpers. All timestamps in the system are UTC, tz-aware.

Provides MONTH_END_ALIAS that works across pandas 1.x (uses 'M'), 2.2+
(prefers 'ME'), and 4.x (only 'ME' works). Use this everywhere a monthly
resample/date_range frequency is needed."""
from __future__ import annotations
import pandas as pd
from datetime import datetime, timezone


def _detect_month_end_alias() -> str:
    for alias in ("ME", "M"):
        try:
            pd.date_range("2020-01-01", periods=1, freq=alias)
            return alias
        except (ValueError, TypeError):
            continue
    return "ME"


MONTH_END_ALIAS = _detect_month_end_alias()


def utcnow() -> pd.Timestamp:
    return pd.Timestamp.utcnow().tz_convert("UTC") if pd.Timestamp.utcnow().tz else pd.Timestamp.now(tz="UTC")


def to_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def biz_days(start, end) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, end=end)


def month_ends(start, end) -> pd.DatetimeIndex:
    return pd.date_range(start=start, end=end, freq=MONTH_END_ALIAS)
