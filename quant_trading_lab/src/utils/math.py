"""Numerical helpers used across strategies and metrics."""
from __future__ import annotations
import numpy as np
import pandas as pd


TRADING_DAYS = 252


def realized_vol(returns: pd.Series, window: int = 21, ann_factor: int = TRADING_DAYS) -> pd.Series:
    """Trailing realized vol. Returns must already exclude lookahead."""
    return returns.rolling(window=window, min_periods=window).std() * np.sqrt(ann_factor)


def ewm_vol(returns: pd.Series, halflife: float = 21, ann_factor: int = TRADING_DAYS) -> pd.Series:
    return returns.ewm(halflife=halflife, adjust=False).std() * np.sqrt(ann_factor)


def trailing_return(prices: pd.Series, lookback_days: int) -> pd.Series:
    """price_t / price_{t-lookback} - 1 (using only past data)."""
    return prices.pct_change(lookback_days)


def safe_divide(a, b, default=0.0):
    out = np.where(np.abs(b) < 1e-12, default, a / np.where(np.abs(b) < 1e-12, 1, b))
    return out


def winsorize(x: pd.Series, p: float = 0.01) -> pd.Series:
    lo = x.quantile(p)
    hi = x.quantile(1 - p)
    return x.clip(lo, hi)


def zscore(x: pd.Series, window: int) -> pd.Series:
    """Rolling z-score using only data up to t."""
    m = x.rolling(window).mean()
    s = x.rolling(window).std()
    return (x - m) / s
