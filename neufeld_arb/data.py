"""Real options-chain fetcher via yfinance.

Fetches a snapshot of vanilla call options (bid/ask/volume) for a given
list of tickers and a target expiration date. Returns a list of
`OptionsBundle`s ready to be fed into `sample_market`.

The fetch is deliberately synchronous and verbose: yfinance is the
single source of truth here, and we never fall back to synthetic data —
if a ticker fails it is dropped with a printed warning. The user must
have functioning internet and yfinance access (Yahoo's anti-bot
measures vary by region; if all tickers fail, the user should try a
different network or upgrade yfinance).
"""

from __future__ import annotations

import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from neufeld_arb.market import OptionsBundle


# Default basket: top S&P 500 names by trading volume / market cap.
SP500_TOP = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO",
    "JPM", "V", "JNJ", "WMT", "MA", "PG", "UNH", "HD", "BAC", "DIS",
    "NFLX", "ADBE", "CRM", "INTC", "CMCSA", "PEP", "KO", "ABT", "T",
    "PFE", "CSCO", "ABBV", "MRK", "TMO", "ORCL", "AMD", "QCOM",
)


def _try_fetch_one(ticker: str, expiry: str) -> OptionsBundle | None:
    """Fetch one ticker's call chain for the given expiry. Returns None on failure."""
    import yfinance as yf
    try:
        t = yf.Ticker(ticker)
        spot = None
        # Try fast_info first; fall back to history.
        try:
            spot = float(t.fast_info["last_price"])
        except Exception:
            try:
                spot = float(t.history(period="1d")["Close"].iloc[-1])
            except Exception:
                pass
        if spot is None or not np.isfinite(spot) or spot <= 0:
            print(f"[data] {ticker}: no spot, skipping")
            return None

        chain = t.option_chain(expiry)
        calls = chain.calls
        if calls is None or len(calls) == 0:
            print(f"[data] {ticker}: empty call chain at {expiry}")
            return None
        df = calls[["strike", "bid", "ask", "volume"]].copy()
        df = df.dropna(subset=["strike", "bid", "ask"])
        df = df[(df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"])]
        df["volume"] = df["volume"].fillna(0.0)
        if len(df) == 0:
            print(f"[data] {ticker}: no valid bid/ask rows after cleaning")
            return None
        return OptionsBundle(
            ticker=ticker,
            spot=spot,
            strikes=df["strike"].to_numpy(dtype=np.float64),
            bid=df["bid"].to_numpy(dtype=np.float64),
            ask=df["ask"].to_numpy(dtype=np.float64),
            volume=df["volume"].to_numpy(dtype=np.float64),
        )
    except Exception as e:
        print(f"[data] {ticker}: {type(e).__name__}: {e}")
        return None


def pick_expiry(tickers: Iterable[str], min_days: int = 14, max_days: int = 90) -> str | None:
    """Find the earliest expiration date present in all tickers, between
    `min_days` and `max_days` from today. Returns YYYY-MM-DD or None.
    """
    import yfinance as yf
    today = datetime.utcnow().date()
    candidates: dict[str, set[str]] = {}
    for tkr in tickers:
        try:
            opts = list(yf.Ticker(tkr).options or [])
            candidates[tkr] = set(opts)
        except Exception as e:
            print(f"[data] expiry probe failed for {tkr}: {e}")
    if not candidates:
        return None
    # Intersection across tickers.
    common = set.intersection(*candidates.values()) if candidates else set()
    eligible = []
    for d in common:
        try:
            day = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        delta = (day - today).days
        if min_days <= delta <= max_days:
            eligible.append(d)
    eligible.sort()
    return eligible[0] if eligible else None


def fetch_bundles(
    tickers: Iterable[str] = SP500_TOP,
    expiry: str | None = None,
    sleep_between: float = 0.4,
) -> list[OptionsBundle]:
    """Fetch one OptionsBundle per ticker.

    If `expiry` is None, picks the earliest common expiry within 14-90
    days for the given tickers. Failed tickers are dropped with a logged
    warning. The list of successful bundles is returned.
    """
    tickers = list(tickers)
    if expiry is None:
        expiry = pick_expiry(tickers)
        if expiry is None:
            raise RuntimeError(
                "could not determine a common expiry. Pass expiry='YYYY-MM-DD' explicitly, "
                "or check that yfinance is reachable from your machine."
            )
        print(f"[data] using expiry {expiry}")

    bundles: list[OptionsBundle] = []
    for tkr in tickers:
        b = _try_fetch_one(tkr, expiry)
        if b is not None and b.strikes.size >= 5:
            bundles.append(b)
        time.sleep(sleep_between)

    print(f"[data] fetched {len(bundles)}/{len(tickers)} bundles for expiry {expiry}")
    if not bundles:
        raise RuntimeError(
            "yfinance returned no usable option chains. Check connectivity, region "
            "(Yahoo Finance has geo restrictions), and that the tickers have listed options."
        )
    return bundles


def save_bundles(bundles: list[OptionsBundle], path: str | Path, expiry: str) -> None:
    """Write bundles to a single Parquet so they can be reloaded without re-fetching."""
    rows = []
    for b in bundles:
        for k, bd, ak, v in zip(b.strikes, b.bid, b.ask, b.volume):
            rows.append((b.ticker, b.spot, float(k), float(bd), float(ak), float(v), expiry))
    df = pd.DataFrame(rows, columns=["ticker", "spot", "strike", "bid", "ask", "volume", "expiry"])
    df.to_parquet(path)
    print(f"[data] wrote {len(rows)} rows for {df['ticker'].nunique()} tickers -> {path}")


def load_bundles(path: str | Path) -> tuple[list[OptionsBundle], str]:
    df = pd.read_parquet(path)
    expiry = df["expiry"].iloc[0]
    out: list[OptionsBundle] = []
    for tkr, g in df.groupby("ticker", sort=False):
        out.append(OptionsBundle(
            ticker=str(tkr),
            spot=float(g["spot"].iloc[0]),
            strikes=g["strike"].to_numpy(),
            bid=g["bid"].to_numpy(),
            ask=g["ask"].to_numpy(),
            volume=g["volume"].to_numpy(),
        ))
    return out, str(expiry)
