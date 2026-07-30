import numpy as np
import pandas as pd

from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.strategies.tsmom import TSMOMStrategy, TSMOMConfig


def test_voltgt_weight_inverse_to_vol():
    """During a high-vol regime weight should be smaller than during low-vol."""
    idx = pd.bdate_range("2020-01-01", periods=600)
    rng = np.random.default_rng(0)
    # 300 low-vol + 300 high-vol
    rets = np.concatenate([rng.normal(0, 0.005, 300), rng.normal(0, 0.025, 300)])
    px = 100 * np.exp(np.cumsum(rets))
    data = pd.DataFrame({"SPY": px}, index=idx)
    sig = SPYVolTargetStrategy(VolTargetConfig(symbol="SPY", target_vol=0.10, rv_window=21)).signal(data)
    low_w = sig["SPY"].iloc[100:280].mean()
    high_w = sig["SPY"].iloc[350:580].mean()
    assert low_w > high_w


def test_tsmom_signs_match_trend():
    idx = pd.bdate_range("2018-01-01", periods=800)
    # uptrend SPY, downtrend TLT
    spy = 100 + np.arange(800) * 0.1
    tlt = 100 - np.arange(800) * 0.05
    data = pd.DataFrame({"SPY": spy, "TLT": tlt}, index=idx)
    sig = TSMOMStrategy(TSMOMConfig(universe=["SPY", "TLT"],
                                      lookback_days=252, rv_window=63,
                                      allow_short=True)).signal(data)
    last = sig.iloc[-1]
    assert last["SPY"] > 0
    assert last["TLT"] < 0
