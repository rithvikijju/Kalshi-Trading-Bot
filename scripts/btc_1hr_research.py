#!/usr/bin/env python3
"""
Research-grade BTC 1-hour Kalshi strategy definition.

This module is intentionally paper/research only. It defines the signal logic
used by scripts/backtest_1hr_collected_data.py for the third strategy:
  - buy at executable ask-style prices, not midpoint
  - apply official Kalshi taker fees
  - blend empirical BTC returns with a lognormal sanity model
  - require stronger probability and edge filters than the older scripts
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR
from scripts import btc_1hr_neohardened as base


CFG.update({
    "min_edge_cents": 12.0,
    "max_spread_cents": 2,
    "min_entry_price": 0.25,
    "max_entry_price": 0.75,
    "max_concurrent_signals": 2,
})


def ceil_to_cent(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 0.0
    return math.ceil((value - 1e-12) * 100.0) / 100.0


def kalshi_taker_fee(price: float, contracts: int = 1) -> float:
    p = min(1.0, max(0.0, float(price)))
    return ceil_to_cent(0.07 * contracts * p * (1.0 - p))


def lognormal_p_above(spot: float, strike: float, ttl_min: float, annual_vol: float | None) -> float:
    if spot <= 0 or not annual_vol or not math.isfinite(annual_vol) or annual_vol <= 0:
        return float("nan")
    variance = annual_vol * annual_vol * ttl_min / MINUTES_PER_YEAR
    if variance <= 0:
        return 1.0 if spot >= strike else 0.0
    sigma = math.sqrt(variance)
    z = (math.log(strike / spot) + 0.5 * variance) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def blended_p_above(spot: float, strike: float, ttl_min: float, samples: dict, current_vol: float | None) -> float:
    p_emp = base.emp_p_above(
        spot,
        strike,
        ttl_min,
        samples,
        current_vol=current_vol,
        brti_dampening=0.80,
    )
    p_norm = lognormal_p_above(spot, strike, ttl_min, current_vol)
    if math.isfinite(p_norm):
        return 0.70 * p_emp + 0.30 * p_norm
    return p_emp


def compute_edges(event, btc_1m, emp_cache):
    spot = base.get_btc_spot()
    df = pd.DataFrame([base.parse_market(m) for m in event["markets"]])
    quoted = df[df["yes_bid"].notna() & df["yes_ask"].notna()].copy()
    if quoted.empty:
        return pd.DataFrame()

    ttl_min = event["ttl_hours"] * 60
    cur_v = float(btc_1m["rv_60m"].dropna().iloc[-1]) if "rv_60m" in btc_1m.columns else None
    horizons = sorted(emp_cache.keys())
    h = min(horizons, key=lambda x: abs(x - ttl_min)) if horizons else 60
    samples = emp_cache.get(h)
    if samples is None:
        return pd.DataFrame()

    probs = []
    for _, r in quoted.iterrows():
        tk = r.get("ticker", "")
        fl = r.get("floor")
        cap = r.get("cap")
        if "-T" in tk and fl is not None:
            p = blended_p_above(spot, fl, ttl_min, samples, cur_v)
        elif "-B" in tk and fl is not None:
            cap_v = cap if cap and cap > fl else (fl + 100)
            p = base.emp_p_bucket(spot, fl, cap_v, ttl_min, samples, cur_v)
        else:
            p = np.nan
        probs.append(p if np.isfinite(p) else np.nan)

    quoted["model_p_yes"] = probs
    quoted = quoted.dropna(subset=["model_p_yes"])
    if quoted.empty:
        return pd.DataFrame()

    quoted["edge_buy_yes"] = quoted["model_p_yes"] - quoted["yes_ask"]
    quoted["edge_buy_no"] = (1.0 - quoted["model_p_yes"]) - quoted["no_ask"]
    quoted["side"] = np.where(quoted["edge_buy_yes"] > quoted["edge_buy_no"], "yes", "no")
    quoted["entry_price"] = np.where(quoted["side"] == "yes", quoted["yes_ask"], quoted["no_ask"])
    quoted["entry_fee"] = quoted["entry_price"].apply(kalshi_taker_fee)
    quoted["edge_gross_cents"] = np.where(
        quoted["side"] == "yes",
        quoted["edge_buy_yes"] * 100,
        quoted["edge_buy_no"] * 100,
    )
    quoted["net_edge_cents"] = quoted["edge_gross_cents"] - quoted["entry_fee"] * 100
    quoted["spread_cents"] = np.where(
        quoted["side"] == "yes",
        (quoted["yes_ask"] - quoted["yes_bid"]) * 100,
        (quoted["no_ask"] - quoted["no_bid"]) * 100,
    )
    quoted["moneyness"] = quoted["floor"] / spot
    return quoted


def tradeable_signals(edges):
    if edges.empty:
        return edges

    strong_prob = np.where(
        edges["side"] == "yes",
        edges["model_p_yes"] >= 0.65,
        edges["model_p_yes"] <= 0.35,
    )
    mask = (
        (edges["net_edge_cents"] >= CFG["min_edge_cents"])
        & strong_prob
        & (edges["spread_cents"] <= CFG["max_spread_cents"])
        & (edges["entry_price"] >= CFG["min_entry_price"])
        & (edges["entry_price"] <= CFG["max_entry_price"])
    )
    return edges[mask].sort_values("net_edge_cents", ascending=False)


if __name__ == "__main__":
    print("This is a strategy module. Backtest it with scripts/backtest_1hr_collected_data.py --strategies research")
