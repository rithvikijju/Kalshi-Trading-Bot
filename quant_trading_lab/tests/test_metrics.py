import numpy as np
import pandas as pd

from src.backtest.metrics import (
    sharpe, sortino, max_drawdown, cagr, annualized_vol, summary,
)


def _ramp_equity(n=252, daily=0.0008):
    idx = pd.bdate_range("2020-01-01", periods=n)
    rets = pd.Series(daily, index=idx)
    eq = (1 + rets).cumprod() * 100_000
    return rets, eq


def test_sharpe_positive_for_uptrend():
    rets, eq = _ramp_equity()
    assert sharpe(rets) > 1.0


def test_max_drawdown_zero_on_monotonic_uptrend():
    _, eq = _ramp_equity()
    assert max_drawdown(eq) >= -1e-9    # monotonic up → tiny float noise OK


def test_max_drawdown_known_value():
    eq = pd.Series([100, 110, 90, 95, 130], index=pd.bdate_range("2020-01-01", periods=5))
    assert abs(max_drawdown(eq) - (-0.1818)) < 1e-3


def test_summary_includes_all_keys():
    rets, eq = _ramp_equity()
    s = summary(rets, eq)
    for k in ("sharpe", "sortino", "cagr", "ann_vol", "max_drawdown", "calmar"):
        assert k in s
