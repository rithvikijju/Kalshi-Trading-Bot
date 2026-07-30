"""Phase 5: predict realized vol instead of direction.

Volatility is FAR more predictable than direction (GARCH/HAR literature).
Trading rule: vol-target strategy. When predicted vol is low → leverage long SPY.
When predicted vol is high → de-lever or short VXX.

Target: log realized vol over next 5 trading days, annualized.
Models: same lineup as direction prediction.

Compare to two strong baselines:
- Naive: rolling 21d realized vol as the vol forecast (HAR-style)
- GARCH(1,1): the classical volatility model
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import time
import torch, torch.nn as nn

from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb

from evaluation import split_data, walk_forward_splits, stats, deflated_sharpe
from features import build_features


def add_vol_target(df: pd.DataFrame) -> pd.DataFrame:
    """Target = log realized vol over next 5 trading days, annualized."""
    df = df.copy()
    # we have spy_ret_1 already; reconstruct forward vol from it
    fwd = pd.concat([df["spy_ret_1"].shift(-i) for i in range(1, 6)], axis=1)
    df["target_vol"] = np.log(fwd.std(axis=1) * np.sqrt(252) + 1e-6)
    return df


def get_vol_features(df: pd.DataFrame) -> list:
    """Features for vol prediction — emphasize lagged vol/return-magnitude."""
    cols = [c for c in df.columns
            if (c.startswith("spy_vol_") or c.startswith("spy_ret_")
                or c.startswith("vix_") or c.startswith("vxx_")
                or c == "vix_level")]
    return cols


def evaluate_vol_model(pred_vol: np.ndarray, actual_vol: np.ndarray, actual_ret: np.ndarray,
                       baseline_vol: float = 0.18, cost_bp: float = 1.0):
    """Vol-target trading rule:
       target weight in SPY = baseline_vol / pred_vol_simple,
       clipped to [0, 2.0] (max 2x leverage, no shorts).
       PnL = weight × actual SPY return - turnover cost.
    """
    pred_simple = np.exp(pred_vol)
    weight = np.clip(baseline_vol / np.maximum(pred_simple, 0.05), 0, 2.0)
    pnl = weight * actual_ret
    turn = np.abs(np.diff(np.concatenate([[1.0], weight])))
    cost = turn * (cost_bp / 10000)
    pnl_net = pnl - cost
    return pd.Series(pnl_net), weight


def naive_vol_baseline(df_test, lookback=21):
    """Use trailing 21d realized vol of SPY as the vol forecast."""
    ret = df_test["spy_ret_1"]
    naive = (ret.rolling(lookback).std() * np.sqrt(252)).fillna(0.18)
    return np.log(naive.values + 1e-6)


def garch11_forecast(returns: pd.Series, lookback=500):
    """Rolling GARCH(1,1) one-step-ahead vol forecast."""
    try:
        from arch import arch_model
    except ImportError:
        return None
    fc = np.full(len(returns), np.nan)
    for t in range(lookback, len(returns)):
        try:
            am = arch_model(returns.iloc[t-lookback:t] * 100, p=1, q=1, rescale=False)
            res = am.fit(disp="off")
            forecast = res.forecast(horizon=1, reindex=False)
            fc[t] = np.sqrt(forecast.variance.values[-1, 0]) / 100 * np.sqrt(252)
        except Exception:
            pass
    return np.log(fc + 1e-6)


def main():
    df = pd.read_parquet("_features.parquet")
    df = add_vol_target(df)
    df = df.dropna(subset=["target_vol"])

    splits = split_data(df)
    test = splits["test"]
    feat_cols = get_vol_features(df)
    actual_vol = np.exp(test["target_vol"].values)
    actual_ret = test["target_ret"].values
    print(f"Train: {len(splits['train'])}  Test: {len(test)}  Features: {len(feat_cols)}")

    # Baselines
    print("\n=== Vol prediction MAE + trading-rule Sharpe ===\n")
    naive_pred = naive_vol_baseline(test)
    naive_simple = np.exp(naive_pred)
    mae_naive = float(np.mean(np.abs(naive_simple - actual_vol)))
    pnl_naive, _ = evaluate_vol_model(naive_pred, actual_vol, actual_ret)
    s = stats(pnl_naive)
    print(f"  Naive21D       : MAE={mae_naive:.4f}  Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")

    # Buy-hold control
    pnl_bh = pd.Series(actual_ret)
    s = stats(pnl_bh)
    print(f"  BuyHold        : MAE=n/a    Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")

    blocks = walk_forward_splits(test.index)

    models = {
        "Ridge":        Ridge(alpha=10.0, random_state=42),
        "RandomForest": RandomForestRegressor(n_estimators=200, max_depth=4,
                          min_samples_leaf=20, random_state=42, n_jobs=-1),
        "GradientBoost": GradientBoostingRegressor(n_estimators=200, max_depth=3,
                          learning_rate=0.05, random_state=42),
        "LightGBM":     lgb.LGBMRegressor(n_estimators=300, max_depth=4,
                          learning_rate=0.05, num_leaves=15, subsample=0.7,
                          colsample_bytree=0.7, random_state=42, n_jobs=1, verbosity=-1),
        "MLP":          MLPRegressor(hidden_layer_sizes=(32, 16), max_iter=300,
                          learning_rate_init=0.001, random_state=42),
    }

    results = {"Naive21D": dict(mae=mae_naive, **stats(pnl_naive)),
               "BuyHold": dict(mae=np.nan, **stats(pnl_bh))}

    for name, mdl_template in models.items():
        t0 = time.time()
        all_pred = np.full(len(test), np.nan)
        for (s_, e_) in blocks:
            cutoff = test.index[s_]
            tr = df.loc[df.index < cutoff].dropna(subset=feat_cols + ["target_vol"])
            if len(tr) < 200: continue
            X_tr = tr[feat_cols].values
            y_tr = tr["target_vol"].values
            X_te = test[feat_cols].iloc[s_:e_].fillna(0).values
            try:
                cls = mdl_template.__class__
                mdl = cls(**mdl_template.get_params())
                scaler = StandardScaler()
                X_tr_s = scaler.fit_transform(X_tr)
                X_te_s = scaler.transform(X_te)
                mdl.fit(X_tr_s, y_tr)
                p = mdl.predict(X_te_s)
                all_pred[s_:e_] = p
            except Exception as ex:
                print(f"  {name} block {s_}:{e_}: {ex}")
        # fill NaN with naive
        all_pred = np.where(np.isnan(all_pred), naive_pred, all_pred)
        pred_simple = np.exp(all_pred)
        mae = float(np.mean(np.abs(pred_simple - actual_vol)))
        pnl, _ = evaluate_vol_model(all_pred, actual_vol, actual_ret)
        st = stats(pnl)
        st["mae"] = mae
        st["secs"] = round(time.time() - t0, 1)
        results[name] = st
        print(f"  {name:14s}: MAE={mae:.4f}  Sharpe={st['sharpe']:+.2f}  Ret={st['ann_ret']*100:+.1f}%  MDD={st['mdd']*100:+.1f}%  ({st['secs']}s)")

    print("\nDeflated Sharpe (n_trials=5):")
    for name, st in results.items():
        if st["n"] > 0:
            ds = deflated_sharpe(st["sharpe"], n_trials=5, T=st["n"])
            print(f"  {name:14s}: raw={st['sharpe']:+.2f}  deflated={ds:+.2f}")

    pd.DataFrame(results).T.to_csv("_phase5_vol_results.csv")
    print("\nSaved Phase 5 vol results.")


if __name__ == "__main__":
    main()
