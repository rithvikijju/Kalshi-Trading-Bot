"""Mispricing scanner / signal generator.

Walks the in-memory BOOKS (populated by the WebSocket listener), computes
fair value for each strike, and emits trade signals where the post-fee
edge clears the threshold AND survives the robust filter.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import CFG
from .data import BOOKS, LOCK, TRACKED, causal_sigma_from_spot
from .model import fair_value, detect_market_type
from .robust import robust_filter, build_ambiguity_set, SIZER
from .paper_db import log_robust_decision


# ════════════════════════════════════════════════════════════════════════
#  Single-cycle signal scan
# ════════════════════════════════════════════════════════════════════════
def kalshi_fee(price: float) -> float:
    """Per-contract round-trip fee on Kalshi.
    Approximation: 7c × p × (1-p) / 0.25, capped at 7c."""
    p = max(0.0, min(1.0, price))
    return min(CFG["kalshi_fee_cap"], CFG["kalshi_fee_cap"] * p * (1 - p) / 0.25)


def scan_signals(empirical_bank: Optional[dict] = None,
                  ambiguity_set: Optional[List[dict]] = None,
                  spot_override: Optional[float] = None,
                  sigma_override: Optional[float] = None) -> pd.DataFrame:
    """Return a DataFrame of tradeable signals, sorted by net edge descending.

    Pipeline:
      1. Pull current spot + σ + tracked event from in-memory state
      2. For each book in BOOKS scoped to the tracked event:
         - compute fair P(YES) via empirical bank (preferred) or lognormal
         - compute YES-side and NO-side edges, pick the bigger
         - apply CFG thresholds (min_edge, spread, entry band)
      3. For each surviving raw signal, run robust_filter() across the
         ambiguity set. Drop signals that don't pass.
      4. Apply max_concurrent_signals cap (top-N by edge).
    """
    with LOCK:
        spot     = spot_override or (None)
        if spot is None:
            from .data import SPOT
            spot = SPOT.get("price")
        event    = TRACKED.get("event")
        close_t  = TRACKED.get("close_time")
        books    = {tk: dict(b) for tk, b in BOOKS.items()}
    if spot is None or event is None or close_t is None:
        return pd.DataFrame()

    sigma = sigma_override or causal_sigma_from_spot()
    if sigma is None or sigma <= 0:
        return pd.DataFrame()

    now = datetime.now(timezone.utc)
    ttl_min = (close_t - now).total_seconds() / 60
    if ttl_min < CFG["scan_min_ttl_min"] or ttl_min > CFG["scan_max_ttl_hours"]*60:
        return pd.DataFrame()

    raw_rows = []
    for tk, b in books.items():
        if not tk.startswith(event): continue
        yb, ya = b.get("yes_bid"), b.get("yes_ask")
        if yb is None or ya is None: continue
        if (ya - yb) > CFG["max_spread_cents"] / 100: continue
        floor = b.get("floor"); cap = b.get("cap")
        if floor is None: continue

        try:
            floor_f = float(floor)
        except (ValueError, TypeError):
            continue

        p_yes = fair_value(tk, spot, floor_f, cap, ttl_min, sigma,
                            kurt=0.0, empirical_bank=empirical_bank)
        if p_yes is None: continue

        fee = kalshi_fee(0.5)    # midpoint-fee approximation; recomputed below
        # YES-side: buy YES at ya, win if p_yes > ya + fee
        edge_yes = (p_yes - ya) * 100 - kalshi_fee(ya) * 100
        # NO-side: buy NO at (1-yb), win if (1-p_yes) > (1-yb) + fee  →  yb - p_yes
        edge_no  = (yb - p_yes) * 100 - kalshi_fee(1 - yb) * 100

        if edge_yes >= edge_no:
            side, edge_c, entry = "yes", edge_yes, ya
        else:
            side, edge_c, entry = "no",  edge_no,  1 - yb

        if edge_c < CFG["min_edge_cents"]: continue
        if entry < CFG["min_entry_price"]: continue
        if entry > CFG["max_entry_price"]: continue

        raw_rows.append({
            "ticker":       tk,
            "side":         side,
            "entry_price":  float(entry),
            "model_p_yes":  float(p_yes),
            "market_yes_mid": (yb + ya) / 2,
            "yes_bid":      yb,
            "yes_ask":      ya,
            "spread_c":     (ya - yb) * 100,
            "floor":        floor_f,
            "cap":          float(cap) if cap is not None else None,
            "ttl_min":      ttl_min,
            "vol":          float(sigma),
            "kurt":         0.0,
            "edge_c":       float(edge_c),
            "fee_c":        float(kalshi_fee(entry) * 100),
        })

    if not raw_rows:
        return pd.DataFrame()

    df = pd.DataFrame(raw_rows).sort_values("edge_c", ascending=False).reset_index(drop=True)

    # Robust filter (HRDNN-inspired)
    if CFG["robust_enabled"] and ambiguity_set:
        keep = []
        for _, r in df.iterrows():
            sig = r.to_dict()
            passes, diag = robust_filter(
                sig, ambiguity_set, spot,
                fee_per_contract=kalshi_fee(sig["entry_price"]),
                min_pass_rate=CFG["robust_min_pass_rate"],
                min_mean_edge_c=CFG["robust_min_mean_edge_c"])
            log_robust_decision(sig["ticker"], sig["side"], sig["entry_price"],
                                 diag, passes, "")
            if passes:
                sig["robust_pass_rate"]   = diag.get("pass_rate")
                sig["robust_mean_edge_c"] = diag.get("mean_edge_c")
                keep.append(sig)
        df = pd.DataFrame(keep) if keep else pd.DataFrame()

    if len(df) == 0: return df
    df = df.head(CFG["max_concurrent_signals"]).reset_index(drop=True)
    return df
