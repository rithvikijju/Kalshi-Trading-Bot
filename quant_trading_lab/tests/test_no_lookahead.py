"""Verifies the SPY vol-target strategy does not peek at future data.

Strategy: perturb data at t+1..t+5 by a random factor and confirm the
signal value at t is unchanged.
"""
import numpy as np
import pandas as pd

from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.strategies.tsmom import TSMOMStrategy, TSMOMConfig
from src.utils.leakage_tests import check_signal_uses_only_past


def _synthetic_prices(symbols, n=600, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    rets = rng.normal(0.0005, 0.012, size=(n, len(symbols)))
    px = 100 * np.exp(np.cumsum(rets, axis=0))
    return pd.DataFrame(px, index=idx, columns=symbols)


def test_spy_vol_target_no_lookahead():
    data = _synthetic_prices(["SPY"])
    strat = SPYVolTargetStrategy(VolTargetConfig(symbol="SPY"))
    rep = check_signal_uses_only_past(lambda d: strat.signal(d)["SPY"], data)
    assert rep.passed, rep.detail


def test_tsmom_no_lookahead():
    universe = ["SPY", "QQQ", "TLT", "GLD"]
    data = _synthetic_prices(universe, n=900, seed=1)
    strat = TSMOMStrategy(TSMOMConfig(universe=universe, lookback_days=252, rv_window=63))
    # check on each symbol independently
    for s in universe:
        rep = check_signal_uses_only_past(lambda d, sym=s: strat.signal(d)[sym], data)
        assert rep.passed, f"{s}: {rep.detail}"
