"""Shared data loader. Caches yfinance pulls to research/v2/data/*.parquet.

Daily adjusted close. Single source of truth for V2 backtests.
"""
from __future__ import annotations
import os, time
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def _path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("^", "_")
    return DATA_DIR / f"{safe}.parquet"


def load(ticker: str, start: str = "2005-01-01", end: str | None = None,
         force: bool = False) -> pd.Series:
    """Return adj-close series for ticker, cached locally."""
    p = _path(ticker)
    if p.exists() and not force:
        s = pd.read_parquet(p)["close"]
        return s
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"no data for {ticker}")
    # yfinance can return MultiIndex columns when given a list — flatten if so
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    s = df["Close"].rename("close")
    s.to_frame().to_parquet(p)
    time.sleep(0.2)
    return s


def load_many(tickers: list[str], start: str = "2005-01-01",
              end: str | None = None) -> pd.DataFrame:
    """Return aligned wide-format DataFrame of adj closes for tickers."""
    cols = {}
    for t in tickers:
        try:
            cols[t] = load(t, start=start, end=end)
        except Exception as e:
            print(f"  skip {t}: {e}")
    df = pd.DataFrame(cols).dropna(how="all")
    return df
