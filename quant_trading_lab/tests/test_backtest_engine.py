import numpy as np
import pandas as pd

from src.backtest.engine import run_backtest, EngineConfig
from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig


def _make_prices(n=400, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    rets = rng.normal(0.0004, 0.012, n)
    spy = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame({"SPY": spy}, index=idx)


def test_backtest_runs_and_produces_metrics():
    prices = _make_prices()
    strat = SPYVolTargetStrategy(VolTargetConfig(symbol="SPY", target_vol=0.10, rv_window=21))
    cfg = EngineConfig(starting_cash=100_000, rebalance_freq="D",
                       benchmark="SPY", strategy_name="t_voltgt", run_preflight=False)
    res = run_backtest(strat, prices, cfg)
    assert "metrics" in res
    assert res["equity"].iloc[-1] > 0
    assert len(res["equity"]) == len(prices)


def test_engine_applies_signal_with_one_day_shift():
    """Crystal-ball test: a strategy that uses the realized return at t to
    weight at t should NOT produce a super-high Sharpe — the engine shifts
    the weight by 1, breaking the cheat."""
    prices = _make_prices()

    class Cheater:
        name = "cheater"
        def signal(self, data):
            rets = data["SPY"].pct_change()
            return pd.DataFrame({"SPY": np.sign(rets) * 0.5}, index=data.index)

    cfg = EngineConfig(starting_cash=100_000, rebalance_freq="D",
                       strategy_name="cheater", run_preflight=False, benchmark=None)
    res = run_backtest(Cheater(), prices, cfg)
    # With the 1-day shift the cheater is signing today's move using yesterday's
    # return — should NOT produce an unrealistic Sharpe.
    assert abs(res["metrics"]["sharpe"]) < 5
