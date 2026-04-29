"""End-to-end runner for the Sezer 2017 GA + Deep MLP strategy.

Trains and evaluates on a single ticker (default AAPL) using the same
1997-2006 / 2007-2016 split as the paper. Compares to Buy & Hold.

Usage:
    python scripts/run_ga_dmlp.py [TICKER]

If yfinance can't reach the network (sandboxed environments) the script
falls back to a synthetic geometric-Brownian-motion price series so the
pipeline still demonstrates end-to-end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ga_dmlp.backtester import buy_and_hold, financial_evaluation
from ga_dmlp.ga import GAConfig, run_ga
from ga_dmlp.indicators import adjust_ohlc, all_rsi_columns, trend_direction
from ga_dmlp.mlp import MLPConfig, build_training_data, predict_with_voting, train_mlp


def fetch_prices(ticker: str, start: str, end: str) -> pd.DataFrame:
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            raise RuntimeError("empty download")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"[warn] yfinance unavailable ({e!r}); using synthetic GBM series")
        return _synthetic_prices(start, end)


def _synthetic_prices(start: str, end: str) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.bdate_range(start, end)
    n = len(idx)
    rets = rng.normal(0.0004, 0.015, size=n)
    close = 50.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame(
        {
            "Open": close * (1 + rng.normal(0, 0.002, n)),
            "High": close * (1 + np.abs(rng.normal(0, 0.005, n))),
            "Low": close * (1 - np.abs(rng.normal(0, 0.005, n))),
            "Close": close,
            "Adj Close": close,
            "Volume": rng.integers(1_000_000, 10_000_000, n),
        },
        index=idx,
    )
    return df


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    train_start, train_end = "1997-01-01", "2006-12-31"
    test_start, test_end = "2007-01-01", "2017-01-01"

    print(f"=== {ticker}: train {train_start}..{train_end}  test {test_start}..{test_end} ===")
    df_full = fetch_prices(ticker, train_start, test_end)
    if df_full.empty:
        print("no data; aborting")
        return

    # Phase 0
    df_full = adjust_ohlc(df_full)

    # Phase 1
    rsi = all_rsi_columns(df_full["Close"])
    trend = trend_direction(df_full["Close"], 50, 200)
    df_full = pd.concat([df_full, rsi], axis=1)
    df_full["trend"] = trend

    # Drop warm-up rows where SMA-200 / RSI are NaN.
    valid_mask = df_full["Close"].notna() & ~df_full[[f"rsi_{i}" for i in range(1, 21)]].isna().any(axis=1)
    df_full = df_full[valid_mask].copy()

    train = df_full.loc[train_start:train_end]
    test = df_full.loc[test_start:test_end]
    if len(train) < 252 or len(test) < 50:
        print(f"insufficient data after warmup: train={len(train)} test={len(test)}")
        return

    rsi_train = train[[f"rsi_{i}" for i in range(1, 21)]]
    rsi_test = test[[f"rsi_{i}" for i in range(1, 21)]]

    # Phase GA
    print("\n[Phase GA] running...")
    ga_cfg = GAConfig(population_size=30, generations=15, seed=1)
    best, best_fit, history = run_ga(
        train["Close"], rsi_train, train["trend"], ga_cfg, verbose=True
    )
    print(f"best chromosome: {best.as_tuple()}  train terminal capital: {best_fit:.2f}")

    # Phase 2
    X_train, y_train = build_training_data(best, rsi_train, train["trend"])
    print(f"\n[Phase 2] MLP training set: {X_train.shape[0]} rows  class counts: "
          f"hold={(y_train == 0).sum()}, buy={(y_train == 1).sum()}, sell={(y_train == 2).sum()}")

    if X_train.shape[0] == 0:
        print("no training rows for MLP; falling back to GA-only signals")
        signals = _ga_only_signals(best, rsi_test, test["trend"])
    else:
        # Phase 3
        cfg = MLPConfig(epochs=100)
        model = train_mlp(X_train, y_train, cfg, verbose=True)
        # Phase 4
        signals = predict_with_voting(model, rsi_test, test["trend"])

    # Phase 5
    print("\n[Phase 5] financial evaluation:")
    stats = financial_evaluation(test["Close"], signals)
    for k, v in stats.as_dict().items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    bah = buy_and_hold(test["Close"])
    print("\n[Buy & Hold baseline]")
    for k, v in bah.as_dict().items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")


def _ga_only_signals(chrom, rsi_columns, trend) -> np.ndarray:
    """Fallback when MLP has no training data: just use the chromosome rule."""
    n = len(trend)
    sig = np.zeros(n, dtype=np.int64)
    rsi_buy_dn = rsi_columns[f"rsi_{chrom.int_buy_dn}"].to_numpy()
    rsi_sell_dn = rsi_columns[f"rsi_{chrom.int_sell_dn}"].to_numpy()
    rsi_buy_up = rsi_columns[f"rsi_{chrom.int_buy_up}"].to_numpy()
    rsi_sell_up = rsi_columns[f"rsi_{chrom.int_sell_up}"].to_numpy()
    trends = trend.to_numpy()
    for i in range(n):
        if trends[i] > 0.5:
            if rsi_buy_up[i] < chrom.rsi_buy_up:
                sig[i] = 1
            elif rsi_sell_up[i] > chrom.rsi_sell_up:
                sig[i] = 2
        else:
            if rsi_buy_dn[i] < chrom.rsi_buy_dn:
                sig[i] = 1
            elif rsi_sell_dn[i] > chrom.rsi_sell_dn:
                sig[i] = 2
    return sig


if __name__ == "__main__":
    main()
