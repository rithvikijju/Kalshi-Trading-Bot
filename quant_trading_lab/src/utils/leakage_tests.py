"""Programmatic leakage detectors.

These are invariant checks, not just unit tests — strategies must satisfy them
or they will be flagged before being run in the backtest engine.

Three checks:
1. signal_uses_only_past — for every t, the signal value depends only on
   data with timestamp <= t. Verified by perturbing future data and confirming
   the signal at t doesn't change.
2. rebalance_after_signal — fills/orders happen on bar t+1 (or later) using
   prices from t+1, never t.
3. future_feature_destroys_oos — if you intentionally leak a future feature
   into the signal, OOS Sharpe should explode. This is a sanity test of the
   evaluation harness, not the strategy.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import numpy as np
import pandas as pd


@dataclass
class LeakageReport:
    name: str
    passed: bool
    detail: str


def check_signal_uses_only_past(
    signal_fn: Callable[[pd.DataFrame], pd.Series],
    data: pd.DataFrame,
    perturb_horizon: int = 5,
    n_probes: int = 30,
    rng_seed: int = 0,
) -> LeakageReport:
    """For random t, perturb data at t+1..t+H and verify signal at t is unchanged."""
    rng = np.random.default_rng(rng_seed)
    base = signal_fn(data).copy()
    valid_idx = base.dropna().index
    if len(valid_idx) < perturb_horizon + 10:
        return LeakageReport("signal_uses_only_past", False,
                             f"Not enough valid signal points ({len(valid_idx)})")
    probe_pts = rng.choice(len(valid_idx) - perturb_horizon - 1, size=min(n_probes, len(valid_idx) - perturb_horizon - 1), replace=False)
    failures = []
    for i in probe_pts:
        t = valid_idx[i]
        d2 = data.copy()
        future_slice = d2.loc[d2.index > t].head(perturb_horizon)
        if future_slice.empty:
            continue
        num_cols = d2.select_dtypes(include=[np.number]).columns
        d2.loc[future_slice.index, num_cols] = d2.loc[future_slice.index, num_cols] * rng.uniform(0.5, 1.5)
        sig2 = signal_fn(d2)
        if abs(sig2.loc[t] - base.loc[t]) > 1e-9:
            failures.append((t, base.loc[t], sig2.loc[t]))
    if failures:
        return LeakageReport("signal_uses_only_past", False,
                             f"{len(failures)}/{len(probe_pts)} probes leaked. First: {failures[0]}")
    return LeakageReport("signal_uses_only_past", True,
                         f"{len(probe_pts)} probes, no leak detected")


def check_no_future_normalization(
    df: pd.DataFrame, columns: list[str] | None = None,
) -> LeakageReport:
    """Heuristic: full-sample z-score/standardize columns produce features with
    near-zero mean and unit std across the WHOLE series. Detect by checking if
    the early window already has ~unit std vs late window — a sign that scaling
    used the whole sample."""
    cols = columns or list(df.select_dtypes(include=[np.number]).columns)
    suspicious = []
    n = len(df)
    if n < 200:
        return LeakageReport("no_future_normalization", True, "Series too short to test")
    head, tail = df[cols].iloc[: n // 4], df[cols].iloc[3 * n // 4 :]
    for c in cols:
        h_std, t_std = head[c].std(), tail[c].std()
        if h_std == 0 or t_std == 0:
            continue
        ratio = max(h_std, t_std) / min(h_std, t_std)
        if abs(head[c].mean()) < 0.1 and abs(tail[c].mean()) < 0.1 and ratio < 1.2 and 0.9 < h_std < 1.1:
            suspicious.append(c)
    if suspicious:
        return LeakageReport("no_future_normalization", False,
                             f"Columns look like they were standardized on full sample: {suspicious[:5]}")
    return LeakageReport("no_future_normalization", True, "No obvious full-sample normalization")


def stress_test_with_oracle(
    backtest_fn: Callable[[pd.DataFrame], dict],
    data: pd.DataFrame,
    target_col: str,
) -> LeakageReport:
    """Inject the target (next-day return) AS A FEATURE; the backtest harness
    that uses it should produce an unrealistic Sharpe. If it doesn't, the
    harness itself has a bug — e.g. it's already enforcing shifts elsewhere
    that swallow the oracle.
    """
    d2 = data.copy()
    d2["__oracle"] = d2[target_col].shift(-1)
    res = backtest_fn(d2)
    sharpe = res.get("sharpe", 0)
    if sharpe < 5:
        return LeakageReport("oracle_stress", False,
                             f"Oracle injection gave Sharpe {sharpe:.2f} — harness should expose it. Look for double-shifts.")
    return LeakageReport("oracle_stress", True,
                         f"Harness correctly amplifies oracle to Sharpe {sharpe:.2f}")
