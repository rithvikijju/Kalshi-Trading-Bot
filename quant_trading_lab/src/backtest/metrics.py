"""Performance metrics. Inputs are daily returns (decimal, e.g. 0.01 = 1%).

All metrics are computed from the equity curve, not from individual fills,
so they reflect what an LP would actually see on a statement.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from ..utils.math import TRADING_DAYS
from ..utils.time import MONTH_END_ALIAS


def total_return(rets: pd.Series) -> float:
    return float((1 + rets).prod() - 1)


def cagr(rets: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    n = len(rets)
    if n == 0:
        return 0.0
    yrs = n / periods_per_year
    return float((1 + total_return(rets)) ** (1 / yrs) - 1) if yrs > 0 else 0.0


def annualized_vol(rets: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    return float(rets.std() * np.sqrt(periods_per_year))


def sharpe(rets: pd.Series, rf: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float:
    excess = rets - rf / periods_per_year
    s = excess.std()
    return float(excess.mean() / s * np.sqrt(periods_per_year)) if s > 0 else 0.0


def sortino(rets: pd.Series, rf: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float:
    excess = rets - rf / periods_per_year
    downside = excess[excess < 0].std()
    return float(excess.mean() / downside * np.sqrt(periods_per_year)) if downside and downside > 0 else 0.0


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def calmar(rets: pd.Series, equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    mdd = abs(max_drawdown(equity))
    c = cagr(rets, periods_per_year)
    return float(c / mdd) if mdd > 0 else 0.0


def hit_rate(rets: pd.Series) -> float:
    nz = rets[rets != 0]
    if len(nz) == 0:
        return 0.0
    return float((nz > 0).mean())


def turnover(weights: pd.DataFrame) -> float:
    """Average daily |Δw| summed across symbols. 1.0 = 100% of book turning each day."""
    if weights.empty:
        return 0.0
    return float(weights.diff().abs().sum(axis=1).mean())


def avg_holding_period(weights: pd.DataFrame) -> float:
    """Crude estimate: 1 / turnover_per_symbol."""
    t = weights.diff().abs().mean()
    t = t[t > 0]
    if t.empty:
        return float("inf")
    return float(1 / t.mean())


def rolling_sharpe(rets: pd.Series, window: int = 63) -> pd.Series:
    return rets.rolling(window).mean() / rets.rolling(window).std() * np.sqrt(TRADING_DAYS)


def rolling_drawdown(equity: pd.Series, window: int = 252) -> pd.Series:
    roll_max = equity.rolling(window, min_periods=1).max()
    return equity / roll_max - 1


def monthly_returns(equity: pd.Series) -> pd.Series:
    if equity.empty:
        return pd.Series(dtype=float)
    rets = equity.pct_change().fillna(0)
    return (1 + rets).resample(MONTH_END_ALIAS).prod() - 1


def summary(rets: pd.Series, equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> dict:
    return dict(
        total_return=total_return(rets),
        cagr=cagr(rets, periods_per_year),
        ann_vol=annualized_vol(rets, periods_per_year),
        sharpe=sharpe(rets, periods_per_year=periods_per_year),
        sortino=sortino(rets, periods_per_year=periods_per_year),
        max_drawdown=max_drawdown(equity),
        calmar=calmar(rets, equity, periods_per_year),
        hit_rate=hit_rate(rets),
        n_periods=len(rets),
    )
