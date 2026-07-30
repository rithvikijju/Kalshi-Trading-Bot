"""Crypto spot-perp funding arbitrage.

Setup: long spot, short perp (1:1 by notional). PnL ≈ Σ funding payments
minus basis MTM minus financing. Position is delta-neutral, so spot price
moves do not enter PnL except through basis convergence.

Backtest takes daily spot prices + funding rate history and simulates
holding the carry. Unwind rules:
  - if funding < `unwind_funding_threshold` for `unwind_periods` in a row
  - if absolute basis > `unwind_basis_bps` (deviation suggests stress)
  - if explicit kill flag (e.g. exchange health alert) set externally

This is a sleeve, not a per-symbol signal — the engine wires it as a
custom sub-system rather than running through the generic backtest engine
(funding cashflows + basis MTM are not weight-based).
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import numpy as np


@dataclass
class FundingArbConfig:
    notional: float = 100_000.0
    funding_per_year: int = 365 * 3       # 8h funding × 365 days for binance
    unwind_funding_threshold: float = 0.0
    unwind_periods: int = 6               # 6 consecutive negative prints (~2 days at 8h)
    unwind_basis_bps: float = 50.0
    spot_taker_bps: float = 10.0
    perp_taker_bps: float = 4.0


def simulate_funding_arb(
    funding: pd.Series,                  # rate per period, indexed by funding_ts (UTC)
    spot: pd.Series,                     # daily close, indexed by date
    perp: pd.Series | None = None,       # daily perp mark; if None, assume = spot
    cfg: FundingArbConfig = FundingArbConfig(),
) -> dict:
    """Returns equity curve + diagnostics for the carry sleeve."""
    funding = funding.sort_index()
    spot = spot.sort_index()
    perp = (perp if perp is not None else spot).sort_index()

    entry_cost = cfg.notional * (cfg.spot_taker_bps + cfg.perp_taker_bps) / 10_000
    eq = pd.Series(index=spot.index, dtype=float)
    eq.iloc[0] = cfg.notional - entry_cost
    cum_funding = 0.0
    cum_basis = 0.0
    flat = False
    neg_streak = 0
    events = []

    # daily basis bp = (perp - spot) / spot * 10_000
    basis_bp = (perp - spot) / spot * 10_000
    # group funding into daily buckets
    daily_funding = funding.resample("D").sum().reindex(spot.index, fill_value=0.0)
    # daily count of negative funding periods
    neg_per_day = (funding < cfg.unwind_funding_threshold).resample("D").sum().reindex(spot.index, fill_value=0)

    for i, d in enumerate(spot.index[1:], start=1):
        prev = spot.index[i - 1]
        if not flat:
            cum_funding += cfg.notional * daily_funding.loc[d]
            cum_basis = (basis_bp.loc[d] - basis_bp.loc[spot.index[0]]) / 10_000 * cfg.notional
            eq.loc[d] = cfg.notional + cum_funding - cum_basis - entry_cost
            neg_streak = neg_streak + 1 if neg_per_day.loc[d] > 0 else 0
            if neg_streak >= cfg.unwind_periods:
                events.append((d, "unwind: persistent negative funding"))
                flat = True
                eq.loc[d] -= cfg.notional * (cfg.spot_taker_bps + cfg.perp_taker_bps) / 10_000
            elif abs(basis_bp.loc[d]) > cfg.unwind_basis_bps:
                events.append((d, f"unwind: basis {basis_bp.loc[d]:.1f}bp"))
                flat = True
                eq.loc[d] -= cfg.notional * (cfg.spot_taker_bps + cfg.perp_taker_bps) / 10_000
        else:
            eq.loc[d] = eq.loc[prev]

    rets = eq.pct_change().fillna(0)
    ann_ret = float((1 + rets.mean()) ** 252 - 1)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    mdd = float((eq / eq.cummax() - 1).min())
    return dict(equity=eq, returns=rets, basis_bp=basis_bp,
                daily_funding=daily_funding, events=events,
                metrics=dict(ann_ret=ann_ret, sharpe=sharpe, mdd=mdd,
                              total_funding=cum_funding,
                              final_equity=float(eq.iloc[-1])))
