"""Phase 2: cross-market features.
Phase 3: cross-market event/domino model.
Phase 4 (bonus): leakage stress test — intentionally inject a future feature
                  to show the framework catches it.
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

from run_phase1 import (run_phase, get_spy_only_features, get_all_features,
                         make_models, detailed_stats, run_baselines)
from evaluation import (split_data, walk_forward_splits, trade_pnl,
                         stats, deflated_sharpe)
from features import build_features


def get_event_features(df: pd.DataFrame) -> list:
    """For phase 3: cross-market event indicators + lagged returns of events.
    Events: |return| > 2 * rolling 21d std → flagged.
    """
    # we'll generate these on the fly in main(); just return marker
    return [c for c in df.columns
            if c.startswith("evt_") or c.startswith("spy_") or c in ("vix_level", "dow")]


def add_event_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'event' flags & magnitude for each cross-market asset.

    For each asset, event = sign(ret) * (|ret| > 2*sigma_21d). Carry over lag1.
    """
    cross = ["vix", "tlt", "ief", "uup", "gld", "uso", "efa", "eem", "hyg", "btc"]
    for a in cross:
        rcol = f"{a}_ret_1"
        vcol = f"{a}_vol_21"
        if rcol in df.columns and vcol in df.columns:
            z = df[rcol] / df[vcol].replace(0, np.nan)
            evt = z.where(z.abs() > 2.0, 0.0)
            df[f"evt_{a}"] = evt.fillna(0)
            df[f"evt_{a}_lag1"] = df[f"evt_{a}"].shift(1)
            df[f"evt_{a}_lag2"] = df[f"evt_{a}"].shift(2)
            # rolling count of events in last 21d
            df[f"evt_{a}_cnt_21"] = (df[f"evt_{a}"] != 0).astype(int).rolling(21).sum()
    return df


def run_phase2(df: pd.DataFrame):
    """All features (SPY + cross-market) vs Phase 1."""
    return run_phase("PHASE 2: SPY + cross-market features", df, get_all_features)


def run_phase3(df: pd.DataFrame):
    """Add event/domino features and re-run."""
    df = add_event_features(df.copy())
    # save the feature names actually present
    feat_fn = get_event_features
    return run_phase("PHASE 3: event/domino features + SPY base", df, feat_fn)


def run_phase4_leakage(df: pd.DataFrame):
    """Inject a literal future feature; the framework should output unrealistic
    Sharpe — demonstrating that an honest framework would catch this if a
    real-world bug introduced it.
    """
    df = df.copy()
    df["LEAK_target_ret_visible"] = df["target_ret"]  # the answer itself
    # add to feature columns
    def feats_with_leak(d):
        cols = get_spy_only_features(d) + ["LEAK_target_ret_visible"]
        return cols
    return run_phase("PHASE 4 (LEAKAGE DEMO): SPY-only + 1 future feature",
                      df, feats_with_leak)


if __name__ == "__main__":
    print("Rebuilding features with expanded test period...")
    df = build_features()
    df.to_parquet("_features.parquet")
    df = pd.read_parquet("_features.parquet")

    res1 = run_phase("PHASE 1: SPY-only features", df, get_spy_only_features)
    res2 = run_phase2(df)
    res3 = run_phase3(df)
    res4 = run_phase4_leakage(df)

    # save
    pd.DataFrame(res1).T.to_csv("_phase1_results.csv")
    pd.DataFrame(res2).T.to_csv("_phase2_results.csv")
    pd.DataFrame(res3).T.to_csv("_phase3_results.csv")
    pd.DataFrame(res4).T.to_csv("_phase4_leakage_results.csv")
    print("\nAll phases done.")
