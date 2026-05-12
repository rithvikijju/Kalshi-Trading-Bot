#!/usr/bin/env python3
"""Validate core p(YES) variants on settled captured websocket decisions.

This is a held-out decision audit, not a full counterfactual websocket replay.
It recomputes finalist model probabilities at the captured decision timestamps
and scores only opportunities with official settlement labels.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.core_model_research import (
    MAX_ENTRY,
    MAX_NO_P,
    MAX_SPREAD_CENTS,
    MIN_EDGE_CENTS,
    MIN_ENTRY,
    MIN_YES_P,
    ModelVariant,
    add_btc_features,
    build_event_cache,
    edge_uncertainty_cents,
    model_probabilities,
    stats_for_trades,
)


DEFAULT_HOLDOUT = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "holdout_late_only_features.csv"
DEFAULT_BTC = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "core_model_research_20260510_calibrated"

STRIKE_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Captured websocket decision holdout for core model finalists.")
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--btc", type=Path, default=DEFAULT_BTC)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def strike_from_ticker(ticker: str) -> float:
    match = STRIKE_RE.search(str(ticker))
    if not match:
        return float("nan")
    return float(match.group("strike")) + 0.01


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    values = btc["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def load_btc(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "time" not in df and "available_at" in df:
        df = df.rename(columns={"available_at": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return add_btc_features(df)


def finalist_variants() -> list[ModelVariant]:
    return [
        ModelVariant("baseline_emp70_logn_rv60", 1, "Current research fair value."),
        ModelVariant("rv_down60_blend", 2, "Downside semivariance volatility.", vol_col="rv_down_60m"),
        ModelVariant("brti_065", 1, "More aggressive BTC dampening.", brti_dampening=0.65),
        ModelVariant("student_t3_rv60", 2, "Very fat-tailed Student-t distribution.", emp_weight=0.5, lognormal_weight=0.0, student_weight=0.5, student_df=3),
        ModelVariant("blend_85_15_rv60", 1, "Empirical-heavy blend.", emp_weight=0.85, lognormal_weight=0.15),
        ModelVariant("vol_scale_085", 1, "Lower realized volatility scale.", vol_scale=0.85),
        ModelVariant("rv_up60_blend", 2, "Upside semivariance volatility.", vol_col="rv_up_60m"),
        ModelVariant("lower_uncertainty", 2, "Lower sampling uncertainty surcharge.", uncertainty_mult=0.50),
    ]


def score_variant_on_rows(variant: ModelVariant, rows: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    out_rows: list[dict] = []
    cache: dict[tuple[str, int], dict] = {}
    for _, row in rows.iterrows():
        entry_time = pd.Timestamp(row["entry_time"])
        close_time = pd.Timestamp(row["close_time"])
        event_open = close_time - pd.Timedelta(hours=1)
        cache_key = (str(row["event_ticker"]), variant.train_days)
        emp_cache = cache.get(cache_key)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, variant.train_days)
            if emp_cache is None:
                continue
            cache[cache_key] = emp_cache
        spot = float(row["btc_spot"]) if math.isfinite(float(row["btc_spot"])) else None
        _, btc_idx = btc_at_or_before(btc, entry_time)
        if spot is None or btc_idx is None:
            continue
        strike = strike_from_ticker(row["market_ticker"])
        if not math.isfinite(strike):
            continue
        q = pd.DataFrame(
            [
                {
                    "event_ticker": row["event_ticker"],
                    "available_at": entry_time,
                    "floor_strike": strike,
                    "yes_bid_close": np.nan,
                    "yes_ask_close": np.nan,
                    "yes_ask_exe": np.nan,
                    "no_ask_exe": np.nan,
                }
            ]
        )
        ttl_min = float(row["ttl_min"])
        p_yes = float(model_probabilities(variant, q, btc, btc_idx, spot, ttl_min, emp_cache, {})[0])
        side = str(row["side"]).lower()
        entry = float(row["entry_price"])
        fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
        if side == "yes":
            gross_edge = (p_yes - entry) * 100.0
            strong = p_yes >= variant.min_yes_p
            p_side = p_yes
        else:
            gross_edge = ((1.0 - p_yes) - entry) * 100.0
            strong = p_yes <= variant.max_no_p
            p_side = 1.0 - p_yes
        net_edge = gross_edge - fee * 100.0
        threshold = variant.min_edge_cents + float(edge_uncertainty_cents(np.asarray([p_yes]), emp_cache, variant.uncertainty_mult)[0])
        passed = (
            strong
            and net_edge >= threshold
            and MIN_ENTRY <= entry <= MAX_ENTRY
            and float(row.get("spread_cents", 1.0) if pd.notna(row.get("spread_cents", np.nan)) else 1.0) <= MAX_SPREAD_CENTS
        )
        won = side == str(row["official_result"]).lower()
        pnl = (1.0 if won else 0.0) - entry - fee
        out_rows.append(
            {
                **row.to_dict(),
                "variant": variant.name,
                "variant_p_yes": p_yes,
                "variant_p_side": p_side,
                "variant_net_edge_cents": net_edge,
                "variant_threshold_cents": threshold,
                "variant_pass": bool(passed),
                "pnl": pnl,
                "entry_fee": fee,
                "settle_time": close_time,
            }
        )
    return pd.DataFrame(out_rows)


def summarize(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if scored.empty:
        return pd.DataFrame()
    for variant, vg in scored.groupby("variant", sort=True):
        for label, mask in {
            "combined_decisions": pd.Series(True, index=vg.index),
            "live_only": vg["capture"].eq("research_live_capture"),
            "passed_combined": vg["variant_pass"],
            "passed_live_only": vg["variant_pass"] & vg["capture"].eq("research_live_capture"),
        }.items():
            g = vg[mask].copy()
            if g.empty:
                stats = {"trades": 0, "pnl": 0.0, "premium": 0.0, "rop": 0.0, "win_rate": 0.0, "max_drawdown": 0.0}
            else:
                g["entry_fee"] = g["entry_fee"].astype(float)
                g["pnl"] = g["pnl"].astype(float)
                stats = stats_for_trades(g)
            rows.append({"variant": variant, "slice": label, **stats})
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    holdout = pd.read_csv(args.holdout)
    holdout["entry_time"] = pd.to_datetime(holdout.get("entry_time", holdout["received_at_utc"]), utc=True)
    holdout["close_time"] = pd.to_datetime(holdout["close_time"], utc=True)
    holdout["side"] = holdout["side"].astype(str).str.lower()
    holdout = holdout[(holdout["settled"].astype(str).str.lower().isin(["true", "1"])) & (holdout["ttl_min"].between(5, 20))].copy()
    btc = load_btc(args.btc)
    frames = [score_variant_on_rows(v, holdout, btc) for v in finalist_variants()]
    scored = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    summary = summarize(scored)
    scored.to_csv(args.output_dir / "core_model_ws_decision_holdout_rows.csv", index=False)
    summary.to_csv(args.output_dir / "core_model_ws_decision_holdout_summary.csv", index=False)
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
