"""Cross-asset time-series momentum (TSMOM / trend following).

Each month-end:
  - compute 12-month trailing return for each asset (ex-most-recent-month
    optional to follow Moskowitz-Ooi-Pedersen convention)
  - long if positive trend, cash/short if negative
  - vol-target each leg to `per_asset_vol` annualized
  - cap gross exposure to `max_gross_leverage`
  - hold for the next month.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from .base import Strategy
from ..utils.math import realized_vol
from ..utils.time import MONTH_END_ALIAS


@dataclass
class TSMOMConfig:
    universe: list[str] = field(default_factory=lambda: [
        "SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "SLV", "USO",
        "DBA", "VNQ", "HYG", "LQD", "UUP", "FXE", "FXY", "XLE", "XLK",
    ])
    lookback_days: int = 252
    rv_window: int = 63
    per_asset_vol: float = 0.10        # 10% annualized per leg
    max_gross_leverage: float = 1.0
    allow_short: bool = True
    rebalance: str = "M"               # monthly


class TSMOMStrategy(Strategy):
    name = "tsmom_cross_asset"

    def __init__(self, cfg: TSMOMConfig = TSMOMConfig()):
        self.cfg = cfg

    def signal(self, data: pd.DataFrame) -> pd.DataFrame:
        cols = [c for c in self.cfg.universe if c in data.columns]
        px = data[cols].copy()
        rets = np.log(px / px.shift(1))
        # 1) sign of 252-day trailing return
        trend = np.sign(px.pct_change(self.cfg.lookback_days))
        if not self.cfg.allow_short:
            trend = trend.clip(lower=0)
        # 2) vol scaling per leg
        rv = rets.rolling(self.cfg.rv_window, min_periods=self.cfg.rv_window).std() * np.sqrt(252)
        leg_size = (self.cfg.per_asset_vol / rv).where(rv > 0, 0.0)
        leg_size = leg_size.clip(upper=2.0)
        w = trend * leg_size
        # 3) only rebalance at month-end (forward-fill targets)
        if self.cfg.rebalance == "M":
            mask = pd.Series(False, index=w.index)
            month_ends = pd.Series(1, index=w.index).resample(MONTH_END_ALIAS).last().dropna().index
            mask.loc[mask.index.isin(month_ends)] = True
            w = w.where(mask, np.nan).ffill().fillna(0)
        # 4) gross leverage cap
        gross = w.abs().sum(axis=1)
        scale = np.where(gross > self.cfg.max_gross_leverage,
                          self.cfg.max_gross_leverage / gross.replace(0, np.nan), 1.0)
        w = w.mul(pd.Series(scale, index=w.index), axis=0).fillna(0)
        return w
