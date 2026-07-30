"""Pre-flight bias and validation checks. Wrap a strategy + harness BEFORE
running the production backtest; if any check fails, the run aborts and the
report tells you why."""
from __future__ import annotations
from typing import Callable
import pandas as pd
import numpy as np

from ..utils.leakage_tests import (
    LeakageReport, check_signal_uses_only_past, check_no_future_normalization,
)


def preflight(strategy, data: pd.DataFrame, signal_extractor: Callable[..., pd.Series] | None = None) -> list[LeakageReport]:
    """Run leakage checks against a strategy's signal function.

    strategy must expose a `.signal(data: pd.DataFrame) -> pd.Series` method.
    """
    reports = []
    if signal_extractor is None:
        signal_extractor = lambda d: strategy.signal(d)
    reports.append(check_signal_uses_only_past(signal_extractor, data))
    reports.append(check_no_future_normalization(data))
    return reports


def assert_preflight_passes(reports: list[LeakageReport]) -> None:
    failed = [r for r in reports if not r.passed]
    if failed:
        msg = "; ".join(f"{r.name}: {r.detail}" for r in failed)
        raise RuntimeError(f"Pre-flight leakage check failed: {msg}")


def purged_walk_forward_splits(index: pd.DatetimeIndex,
                                train_min_days: int = 252 * 3,
                                test_window_days: int = 63,
                                embargo_days: int = 5):
    """Generator: (train_idx, test_idx) tuples.

    Train uses an expanding window; test is a fixed-width forward block;
    `embargo_days` of rows between train and test are dropped to prevent
    feature/label leakage at the seam (López de Prado).
    """
    idx = pd.DatetimeIndex(index).sort_values()
    n = len(idx)
    start = train_min_days
    while start + embargo_days + test_window_days <= n:
        train = idx[:start]
        test = idx[start + embargo_days: start + embargo_days + test_window_days]
        yield train, test
        start += test_window_days
