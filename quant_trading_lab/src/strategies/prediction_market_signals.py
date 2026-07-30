"""Prediction-market → TradFi lead-lag signal layer.

We do NOT trade prediction markets directly here. We compute event-implied
probabilities from Kalshi/Polymarket and align them to TradFi instruments
(SOFR futures proxies, TLT, SPY, VIX proxies, DXY) to look for:
  - Does PM probability move BEFORE the TradFi instrument?
  - What is the lead time?
  - Survives OOS?

If the lead is < 15 minutes or not statistically significant, the strategy
returns flat weights and writes a "not tradeable" event to risk_events.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from .base import Strategy


@dataclass
class LeadLagConfig:
    pm_symbol: str = "FED-RATE-DEC"           # ticker used in pm_snapshots
    tradfi_symbol: str = "TLT"
    max_lag_minutes: int = 240                # search up to 4h leads
    min_significant_lead_min: int = 15
    significance_threshold: float = 0.30      # correlation threshold


class LeadLagResearchStrategy(Strategy):
    """Research-only strategy. Produces weight = 0 if the signal isn't
    significant at sufficient lead; otherwise it outputs a small directional
    bet proportional to the PM probability change."""

    name = "pm_leadlag"

    def __init__(self, cfg: LeadLagConfig = LeadLagConfig()):
        self.cfg = cfg
        self.last_analysis: dict = {}

    def analyze_leadlag(self, pm_series: pd.Series, tradfi_series: pd.Series) -> dict:
        """Returns the lag at which PM->TradFi correlation peaks.
        Negative lag = PM leads TradFi."""
        pm = pm_series.pct_change().fillna(0)
        tr = tradfi_series.pct_change().fillna(0)
        # align
        df = pd.concat([pm.rename("pm"), tr.rename("tr")], axis=1).dropna()
        if len(df) < 100:
            return dict(lead_min=None, peak_corr=None, significant=False, n=len(df))
        max_lag = self.cfg.max_lag_minutes
        idx_freq_min = (df.index[1] - df.index[0]).total_seconds() / 60 if len(df) > 1 else 1
        max_shift = max(1, int(max_lag / max(idx_freq_min, 1)))
        corrs = []
        for shift in range(-max_shift, max_shift + 1):
            c = df["pm"].shift(shift).corr(df["tr"])
            corrs.append((shift, c))
        best = max(corrs, key=lambda x: abs(x[1]) if x[1] is not None and not np.isnan(x[1]) else 0)
        lead_min = best[0] * idx_freq_min       # negative = PM leads
        significant = (best[1] is not None and abs(best[1]) >= self.cfg.significance_threshold
                       and abs(lead_min) >= self.cfg.min_significant_lead_min and lead_min < 0)
        return dict(lead_min=lead_min, peak_corr=best[1], significant=significant,
                    n=len(df), all_lags=corrs)

    def signal(self, data: pd.DataFrame) -> pd.DataFrame:
        """data must contain columns [pm_symbol, tradfi_symbol] as time-aligned
        prices. Returns a single-column DataFrame for tradfi_symbol."""
        if self.cfg.pm_symbol not in data.columns or self.cfg.tradfi_symbol not in data.columns:
            return pd.DataFrame({self.cfg.tradfi_symbol: 0.0}, index=data.index)
        pm = data[self.cfg.pm_symbol]
        tr = data[self.cfg.tradfi_symbol]
        analysis = self.analyze_leadlag(pm, tr)
        self.last_analysis = analysis
        if not analysis["significant"]:
            return pd.DataFrame({self.cfg.tradfi_symbol: 0.0}, index=data.index)
        # tradeable: small weight in the direction of PM change
        delta_pm = pm.pct_change().rolling(int(abs(analysis["lead_min"]))).mean().fillna(0)
        w = np.sign(delta_pm) * 0.05    # 5% per signal, very conservative
        return pd.DataFrame({self.cfg.tradfi_symbol: w}, index=data.index)
