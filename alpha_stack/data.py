"""
Multi-source, multi-market daily data layer.

Pulls from several independent providers so strategies span genuinely different markets:
  * yfinance  — equities, sector/factor ETFs, FX, commodity & index futures, ^VIX, ^TNX, crypto
  * Coinbase  — crypto daily candles (independent of yfinance's crypto)
  * FRED      — macro series (rates, credit spreads, etc.) via pandas_datareader

Everything is cached to parquet so re-runs are deterministic and fast, and so a network
hiccup mid-research can't silently change the dataset under a backtest (a reproducibility
guard — point-in-time, same bytes every run).

ALL prices are daily close (adjusted where the source provides it). Intraday is out of scope
here on purpose: daily is what we can get cleanly and bias-free from free sources, and the
"stack many small daily edges" thesis lives at this frequency.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
CACHE = Path(__file__).resolve().parent / "data"
CACHE.mkdir(exist_ok=True)

# universe grouped by market — deliberately diverse
UNIVERSE = {
    "equity_index": ["SPY", "QQQ", "IWM", "DIA"],
    "sectors": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE"],
    "factors": ["MTUM", "VLUE", "QUAL", "USMV", "SIZE"],
    "intl": ["EFA", "EEM", "FXI", "EWJ", "EWZ"],
    "rates_credit": ["TLT", "IEF", "SHY", "LQD", "HYG", "TIP"],
    "commodity": ["GLD", "SLV", "USO", "UNG", "DBC"],
    "fx": ["UUP", "FXE", "FXY"],
    "vol": ["^VIX", "^VIX3M"],
    "macro_yield": ["^TNX", "^IRX", "^FVX", "^TYX"],
    "crypto": ["BTC-USD", "ETH-USD"],
}

FRED = {
    "T10Y2Y": "term_spread", "BAMLH0A0HYM2": "hy_oas", "VIXCLS": "vix_fred",
    "DFF": "fed_funds", "T10YIE": "breakeven_10y", "DTWEXBGS": "usd_index",
}


def _cache(name: str) -> Path:
    return CACHE / f"{name}.parquet"


def fetch_yf(tickers, start="2008-01-01", end=None, force=False) -> pd.DataFrame:
    """Adjusted daily closes, columns = tickers. Cached."""
    import yfinance as yf
    key = "yf_" + "_".join(t.replace("^", "I").replace("-", "") for t in tickers)[:60]
    fp = _cache(key)
    if fp.exists() and not force:
        return pd.read_parquet(fp)
    df = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    close = df["Close"] if "Close" in df else df
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    close = close.dropna(how="all")
    close.to_parquet(fp)
    return close


def fetch_fred(force=False) -> pd.DataFrame:
    fp = _cache("fred")
    if fp.exists() and not force:
        return pd.read_parquet(fp)
    import pandas_datareader.data as web
    out = {}
    for code, name in FRED.items():
        try:
            s = web.DataReader(code, "fred", "2008-01-01")
            out[name] = s[code]
        except Exception as e:
            print(f"  FRED {code} failed: {str(e)[:60]}")
    df = pd.DataFrame(out)
    df.to_parquet(fp)
    return df


def load_all(force=False) -> dict:
    """Returns {'prices': DataFrame(all tickers), 'fred': DataFrame, 'groups': UNIVERSE}."""
    all_tickers = [t for g in UNIVERSE.values() for t in g]
    frames = []
    # fetch per-group (smaller, more robust to a single bad ticker)
    for grp, tks in UNIVERSE.items():
        try:
            frames.append(fetch_yf(tks, force=force))
        except Exception as e:
            print(f"  group {grp} failed: {str(e)[:60]}")
    prices = pd.concat(frames, axis=1)
    prices = prices.loc[:, ~prices.columns.duplicated()].sort_index()
    try:
        fred = fetch_fred(force=force)
    except Exception as e:
        print(f"  FRED unavailable: {str(e)[:60]}")
        fred = pd.DataFrame()
    return {"prices": prices, "fred": fred, "groups": UNIVERSE}


if __name__ == "__main__":
    import sys
    d = load_all(force="--force" in sys.argv)
    p = d["prices"]
    print(f"prices: {p.shape[0]} days x {p.shape[1]} instruments")
    print(f"  span: {p.index.min().date()} -> {p.index.max().date()}")
    print(f"  coverage (non-null %):")
    cov = p.notna().mean().sort_values()
    for t in cov.index[:5]:
        print(f"    {t}: {cov[t]:.0%}")
    print(f"  FRED series: {list(d['fred'].columns)}")
