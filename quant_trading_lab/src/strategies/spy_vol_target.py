"""SPY vol-target (and any ETF).

Idea: hold SPY at a notional that keeps annualized portfolio vol at `target_vol`.
Position size = target_vol / forecast_vol, clipped to [0, max_leverage].

Baseline forecast: 21-day rolling realized vol on log returns. All inputs at
time t use only data with timestamp <= t.

ML variant lives in a sibling module; baseline must work first.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from .base import Strategy
from ..utils.math import realized_vol


@dataclass
class VolTargetConfig:
    symbol: str = "SPY"
    target_vol: float = 0.10        # 10% annualized
    rv_window: int = 21
    max_leverage: float = 1.0
    allow_short: bool = False


class SPYVolTargetStrategy(Strategy):
    name = "spy_vol_target"

    def __init__(self, cfg: VolTargetConfig = VolTargetConfig()):
        self.cfg = cfg

    def signal(self, data: pd.DataFrame) -> pd.DataFrame:
        """data must include the symbol column (close prices). Returns a
        DataFrame with one column per symbol whose value is the target weight."""
        if self.cfg.symbol not in data.columns:
            raise KeyError(f"data missing '{self.cfg.symbol}' column")
        px = data[self.cfg.symbol]
        rets = np.log(px / px.shift(1))
        rv = realized_vol(rets, window=self.cfg.rv_window)
        weight = self.cfg.target_vol / rv
        weight = weight.clip(lower=-self.cfg.max_leverage if self.cfg.allow_short else 0,
                              upper=self.cfg.max_leverage)
        weight = weight.where(rv.notna(), 0.0)
        out = pd.DataFrame({self.cfg.symbol: weight}, index=data.index)
        return out
