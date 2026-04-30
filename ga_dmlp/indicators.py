"""Technical indicators per Sezer 2017 Phases 0 and 1."""

from __future__ import annotations

import numpy as np
import pandas as pd


def adjust_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Phase 0: scale OHLC by adjustRatio = close / adjustedClose.

    yfinance's `auto_adjust=False` returns both Close and Adj Close. Volume is
    left untouched.
    """
    out = df.copy()
    if "Adj Close" not in out.columns or "Close" not in out.columns:
        raise ValueError("DataFrame must have 'Close' and 'Adj Close' columns.")
    ratio = out["Close"] / out["Adj Close"]
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col] / ratio
    return out


def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI for the given period (in days).

    RSI = 100 - 100 / (1 + RS), where RS = avg_gain / avg_loss using Wilder's
    exponential smoothing (alpha = 1/period).
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder's smoothing.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.fillna(50.0)
    return rsi


def compute_sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def trend_direction(close: pd.Series, fast: int = 50, slow: int = 200) -> pd.Series:
    """1 when fast SMA >= slow SMA (uptrend), else 0 (downtrend)."""
    sma_f = compute_sma(close, fast)
    sma_s = compute_sma(close, slow)
    trend = (sma_f >= sma_s).astype(float)
    trend[sma_s.isna()] = 0.0
    return trend


def all_rsi_columns(close: pd.Series, intervals: range = range(1, 21)) -> pd.DataFrame:
    """Return a DataFrame with one RSI column per interval in `intervals`."""
    return pd.DataFrame({f"rsi_{p}": compute_rsi(close, p) for p in intervals})
