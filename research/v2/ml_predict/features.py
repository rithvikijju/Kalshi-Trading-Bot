"""Feature pipeline for SPX prediction with cross-market data.

Asset universe (daily, 2000-2026):
- SPY    (S&P 500) — target asset
- ^VIX   (volatility index)
- TLT    (20y treasuries)
- IEF    (7-10y treasuries)
- UUP    (DXY proxy — dollar index ETF)
- GLD    (gold)
- USO    (oil)
- EFA    (developed ex-US equities)
- EEM    (emerging markets)
- HYG    (high-yield credit)
- BTC-USD (bitcoin, when available)

All features computed strictly with t-1 information. Target is t+1 log return
of SPY.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/Users/rithvikijju/edge-bot/research/v2")
import numpy as np
import pandas as pd
from _data import load_many

ASSETS = ["SPY", "^VIX", "TLT", "IEF", "UUP", "GLD", "USO",
          "EFA", "EEM", "HYG", "BTC-USD"]


def rsi(x: pd.Series, n: int = 14) -> pd.Series:
    d = x.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(start: str = "2000-01-01", end: str = "2026-06-01",
                   include_leakage: bool = False) -> pd.DataFrame:
    """Return a wide DataFrame of features indexed by date.

    Columns: target (next-day SPY log return), features (all t-1 or earlier).
    include_leakage=True intentionally adds a FUTURE feature for the leakage demo.
    """
    print(f"Loading {len(ASSETS)} assets...")
    px = load_many(ASSETS, start=start, end=end)
    px.columns = [c.replace("^", "").replace("-USD", "") for c in px.columns]

    # CRITICAL FIX: restrict index to US trading days (SPY's index)
    # otherwise BTC weekend rows create fake zero-return SPY days
    spy_dates = px["SPY"].dropna().index
    px = px.loc[spy_dates]
    # forward-fill ONLY for cross-market gaps within US trading days (e.g.
    # foreign holidays that fall on US trading days)
    px = px.ffill(limit=3)

    # log returns (on US trading-day grid only)
    ret = np.log(px / px.shift(1))

    feats = pd.DataFrame(index=px.index)

    # === target: next-day log return of SPY, computed at close ===
    feats["target_ret"] = ret["SPY"].shift(-1)
    feats["target_up"] = (feats["target_ret"] > 0).astype(int)

    # === SPY-only features (all t-1 or earlier) ===
    feats["spy_ret_1"] = ret["SPY"]
    feats["spy_ret_5"] = ret["SPY"].rolling(5).sum()
    feats["spy_ret_21"] = ret["SPY"].rolling(21).sum()
    feats["spy_ret_63"] = ret["SPY"].rolling(63).sum()
    feats["spy_vol_5"] = ret["SPY"].rolling(5).std()
    feats["spy_vol_21"] = ret["SPY"].rolling(21).std()
    feats["spy_vol_63"] = ret["SPY"].rolling(63).std()
    feats["spy_rsi_14"] = rsi(px["SPY"], 14)
    feats["spy_mom_3"] = ret["SPY"].rolling(3).sum()
    feats["spy_skew_21"] = ret["SPY"].rolling(21).skew()
    feats["spy_kurt_21"] = ret["SPY"].rolling(21).kurt()
    feats["spy_above_sma50"] = (px["SPY"] > px["SPY"].rolling(50).mean()).astype(int)
    feats["spy_above_sma200"] = (px["SPY"] > px["SPY"].rolling(200).mean()).astype(int)
    feats["spy_dist_sma50"] = (px["SPY"] / px["SPY"].rolling(50).mean() - 1)
    feats["spy_dist_sma200"] = (px["SPY"] / px["SPY"].rolling(200).mean() - 1)

    # === Cross-market features (1d, 5d returns + vol) for each asset ===
    cross_assets = ["VIX", "TLT", "IEF", "UUP", "GLD", "USO",
                    "EFA", "EEM", "HYG", "BTC"]
    for a in cross_assets:
        if a not in ret.columns or ret[a].notna().sum() < 200:
            continue
        feats[f"{a.lower()}_ret_1"] = ret[a]
        feats[f"{a.lower()}_ret_5"] = ret[a].rolling(5).sum()
        feats[f"{a.lower()}_ret_21"] = ret[a].rolling(21).sum()
        feats[f"{a.lower()}_vol_21"] = ret[a].rolling(21).std()
        # cross-correlation with SPY (rolling)
        feats[f"{a.lower()}_corr_spy_21"] = ret[a].rolling(21).corr(ret["SPY"])

    # VIX level itself (not just return)
    if "VIX" in px.columns:
        feats["vix_level"] = px["VIX"]
        feats["vix_dist_sma21"] = px["VIX"] / px["VIX"].rolling(21).mean() - 1

    # Day-of-week / calendar
    feats["dow"] = feats.index.dayofweek
    feats["month"] = feats.index.month
    feats["dom"] = feats.index.day

    # === LEAKAGE DEMO: intentionally add a future feature ===
    if include_leakage:
        feats["LEAK_target_ret"] = feats["target_ret"]   # literally the answer
        feats["LEAK_next_5d_vol"] = ret["SPY"].shift(-5).rolling(5).std()

    # Drop early rows with NaN from rolling, and the last row whose target is unknown
    feats = feats.iloc[200:-1].copy()
    feats = feats.dropna()
    print(f"  Built {feats.shape[1]} features × {feats.shape[0]} days "
          f"({feats.index[0].date()} → {feats.index[-1].date()})")
    return feats


if __name__ == "__main__":
    f = build_features()
    print("\nFeature head:")
    print(f.head(2).T)
    print("\nTarget balance:")
    print(f["target_up"].value_counts(normalize=True).round(3))
    # save for re-use
    f.to_parquet("/Users/rithvikijju/edge-bot/research/v2/ml_predict/_features.parquet")
    print("\nSaved features.")
