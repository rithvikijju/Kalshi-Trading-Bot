#!/usr/bin/env python3
"""Iterative hypothesis research over collected BTC 1h strategy data.

This script is deliberately read-only. It combines:

* historical corrected DuckDB replay output from may8examine.py
* deployed sizing research output
* official-result live/shadow ledgers

It does not touch the running live bot.
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "iterative_hypothesis_research_20260510"
DEFAULT_HISTORICAL_TRADES = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "live_shadow_accuracy_20260510"
    / "historical_may8_variants"
    / "may8examine_trades.csv"
)
DEFAULT_OFFICIAL_AUDIT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "live_shadow_accuracy_20260510"
    / "official_live_shadow_trade_audit.csv"
)


@dataclass(frozen=True)
class Hypothesis:
    round_no: int
    hypothesis_id: str
    description: str
    variant: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def strike_from_ticker(ticker: str) -> float:
    match = re.search(r"-T(?P<strike>\d+(?:\.\d+)?)", str(ticker).upper())
    if not match:
        return float("nan")
    # KXBTCD T80799.99 encodes "$80,800 or above".
    return float(match.group("strike")) + 0.01


def utc_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def add_common_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "entry_time" not in out and "created_at" in out:
        out["entry_time"] = out["created_at"]
    out["entry_time"] = utc_series(out["entry_time"])
    out["close_time"] = utc_series(out["close_time"])
    out["ttl_min"] = (out["close_time"] - out["entry_time"]).dt.total_seconds() / 60.0
    out["entry_hour_utc"] = out["entry_time"].dt.hour
    out["entry_minute"] = out["entry_time"].dt.minute
    out["side"] = out["side"].astype(str).str.lower()
    out["strike"] = pd.to_numeric(out.get("strike", pd.Series(np.nan, index=out.index)), errors="coerce").astype(float)
    missing_strike = out["strike"].isna()
    if missing_strike.any():
        out.loc[missing_strike, "strike"] = (
            out.loc[missing_strike, "market_ticker"].map(strike_from_ticker).astype(float)
        )
    spot_col = "entry_spot" if "entry_spot" in out else "btc_spot"
    out["entry_spot"] = pd.to_numeric(out.get(spot_col, np.nan), errors="coerce")
    out["side_distance_usd"] = np.where(
        out["side"] == "yes",
        out["entry_spot"] - out["strike"],
        out["strike"] - out["entry_spot"],
    )
    out["abs_distance_usd"] = (out["entry_spot"] - out["strike"]).abs()
    out["moneyness_bps"] = 10000.0 * (out["entry_spot"] - out["strike"]) / out["entry_spot"].clip(lower=1.0)
    out["side_moneyness_bps"] = np.where(out["side"] == "yes", out["moneyness_bps"], -out["moneyness_bps"])
    out["entry_bucket"] = pd.cut(
        pd.to_numeric(out["entry_price"], errors="coerce"),
        bins=[0.0, 0.45, 0.55, 0.65, 0.75, 1.01],
        labels=["<=45", "45-55", "55-65", "65-75", ">75"],
        right=True,
        include_lowest=True,
    )
    out["edge_bucket"] = pd.cut(
        pd.to_numeric(out["net_edge_cents"], errors="coerce"),
        bins=[-999, 14, 16, 18, 999],
        labels=["<14", "14-16", "16-18", ">=18"],
        right=False,
    )
    out["pnl"] = pd.to_numeric(out["pnl"], errors="coerce")
    out["premium"] = pd.to_numeric(out.get("premium", np.nan), errors="coerce")
    if out["premium"].isna().all():
        fee_col = "entry_fee" if "entry_fee" in out else "actual_fee_paid"
        if fee_col not in out:
            fee_col = "entry_fee_estimate"
        out["premium"] = pd.to_numeric(out["entry_price"], errors="coerce") * pd.to_numeric(
            out.get("contracts", 1), errors="coerce"
        ).fillna(1) + pd.to_numeric(out.get(fee_col, 0.0), errors="coerce").fillna(0.0)
    out["contracts"] = pd.to_numeric(out.get("contracts", 1), errors="coerce").fillna(1).astype(int)
    return out


def summarize(frame: pd.DataFrame, *, source: str, hypothesis: Hypothesis, split: str) -> dict:
    part = frame.dropna(subset=["pnl"]).copy()
    premium = float(part["premium"].sum()) if not part.empty else 0.0
    pnl = float(part["pnl"].sum()) if not part.empty else 0.0
    ordered = part.sort_values(["close_time", "entry_time", "market_ticker"]) if not part.empty else part
    equity = ordered["pnl"].cumsum() if not ordered.empty else pd.Series(dtype=float)
    drawdown = float((equity - equity.cummax()).min()) if len(equity) else 0.0
    return {
        "round": hypothesis.round_no,
        "hypothesis_id": hypothesis.hypothesis_id,
        "description": hypothesis.description,
        "source": source,
        "variant": hypothesis.variant,
        "split": split,
        "trades": int(len(part)),
        "contracts": int(part["contracts"].sum()) if not part.empty else 0,
        "premium": premium,
        "pnl": pnl,
        "return_on_premium": pnl / premium if premium else 0.0,
        "win_rate": float((part["pnl"] > 0).mean()) if not part.empty else 0.0,
        "max_drawdown": drawdown,
        "yes_trades": int((part["side"] == "yes").sum()) if not part.empty else 0,
        "no_trades": int((part["side"] == "no").sum()) if not part.empty else 0,
        "avg_entry": float(part["entry_price"].mean()) if not part.empty else 0.0,
        "avg_edge": float(part["net_edge_cents"].mean()) if not part.empty else 0.0,
        "avg_side_distance": float(part["side_distance_usd"].mean()) if not part.empty else 0.0,
    }


def evaluate_hypotheses(historical: pd.DataFrame, forward: pd.DataFrame, hypotheses: list[Hypothesis]) -> pd.DataFrame:
    rows: list[dict] = []

    def predicate_mask(hyp: Hypothesis, frame: pd.DataFrame) -> pd.Series:
        raw = hyp.predicate(frame)
        if isinstance(raw, (bool, np.bool_)):
            return pd.Series(bool(raw), index=frame.index)
        mask = pd.Series(raw, index=frame.index) if not isinstance(raw, pd.Series) else raw.reindex(frame.index)
        return mask.fillna(False).astype(bool)

    for hyp in hypotheses:
        hist_base = historical[historical["variant"] == hyp.variant].copy()
        if hist_base.empty:
            continue
        hist_mask = predicate_mask(hyp, hist_base)
        hist_selected = hist_base[hist_mask].copy()
        for split in ["train", "validation", "test", "all"]:
            part = hist_selected if split == "all" else hist_selected[hist_selected["split"] == split]
            rows.append(summarize(part, source="historical_duckdb", hypothesis=hyp, split=split))

        if hyp.variant == "baseline_current":
            fwd_sources = ["live_research", "shadow_research"]
        elif hyp.variant == "market_shrink_no_cautious":
            fwd_sources = ["shadow_market_shrink_no_cautious"]
        elif hyp.variant == "market_shrink_no_cautious_shape_adjacent":
            fwd_sources = ["shadow_market_shrink_no_cautious_shape_adjacent"]
        else:
            fwd_sources = []
        for source in fwd_sources:
            fwd_base = forward[forward["ledger"] == source].copy()
            fwd_base["variant"] = hyp.variant
            if fwd_base.empty:
                continue
            fwd_mask = predicate_mask(hyp, fwd_base)
            rows.append(summarize(fwd_base[fwd_mask], source="official_forward", hypothesis=hyp, split=source))
    return pd.DataFrame(rows)


def make_hypotheses() -> list[Hypothesis]:
    hyps: list[Hypothesis] = []

    def add(round_no: int, hypothesis_id: str, description: str, variant: str, pred: Callable[[pd.DataFrame], pd.Series]) -> None:
        hyps.append(Hypothesis(round_no, hypothesis_id, description, variant, pred))

    base_variants = ["baseline_current", "market_shrink_no_cautious", "market_shrink_no_cautious_shape_adjacent"]

    # Round 1: first 20 broad hypotheses.
    for variant in base_variants:
        add(1, f"{variant}:all", "Baseline for this variant", variant, lambda d: pd.Series(True, index=d.index))
        add(1, f"{variant}:no_only", "NO side only", variant, lambda d: d["side"] == "no")
        add(1, f"{variant}:yes_only", "YES side only", variant, lambda d: d["side"] == "yes")
        add(1, f"{variant}:edge_lt16", "Net edge below 16c", variant, lambda d: d["net_edge_cents"] < 16)
        add(1, f"{variant}:edge_gte16", "Net edge at least 16c", variant, lambda d: d["net_edge_cents"] >= 16)
        add(1, f"{variant}:entry_le65", "Entry price <= 65c", variant, lambda d: d["entry_price"] <= 0.65)
        add(1, f"{variant}:entry_55_70", "Entry price 55c-70c", variant, lambda d: (d["entry_price"] >= 0.55) & (d["entry_price"] <= 0.70))

    # Round 2: side/edge/price interactions.
    for variant in base_variants:
        add(2, f"{variant}:no_lt16", "NO with net edge <16c", variant, lambda d: (d["side"] == "no") & (d["net_edge_cents"] < 16))
        add(2, f"{variant}:no_14_16", "NO with net edge 14-16c", variant, lambda d: (d["side"] == "no") & (d["net_edge_cents"] >= 14) & (d["net_edge_cents"] < 16))
        add(2, f"{variant}:no_gte16_entry_le60", "NO high edge but entry <=60c", variant, lambda d: (d["side"] == "no") & (d["net_edge_cents"] >= 16) & (d["entry_price"] <= 0.60))
        add(2, f"{variant}:no_gte16_entry_gt60", "NO high edge and entry >60c", variant, lambda d: (d["side"] == "no") & (d["net_edge_cents"] >= 16) & (d["entry_price"] > 0.60))
        add(2, f"{variant}:yes_entry_le65", "YES entry <=65c", variant, lambda d: (d["side"] == "yes") & (d["entry_price"] <= 0.65))
        add(2, f"{variant}:yes_entry_gt65", "YES entry >65c", variant, lambda d: (d["side"] == "yes") & (d["entry_price"] > 0.65))
        add(2, f"{variant}:favorite_gt65", "Favorite-priced entries >65c", variant, lambda d: d["entry_price"] > 0.65)

    # Round 3: distance/TTL hypotheses.
    for variant in base_variants:
        add(3, f"{variant}:side_distance_pos", "Side already in the money at entry", variant, lambda d: d["side_distance_usd"] > 0)
        add(3, f"{variant}:side_distance_gt50", "Side distance >$50", variant, lambda d: d["side_distance_usd"] > 50)
        add(3, f"{variant}:side_distance_gt100", "Side distance >$100", variant, lambda d: d["side_distance_usd"] > 100)
        add(3, f"{variant}:side_distance_neg", "Side out of the money at entry", variant, lambda d: d["side_distance_usd"] <= 0)
        add(3, f"{variant}:ttl_5_20", "Late entry: 5-20 minutes to close", variant, lambda d: (d["ttl_min"] >= 5) & (d["ttl_min"] <= 20))
        add(3, f"{variant}:ttl_20_45", "Mid entry: 20-45 minutes to close", variant, lambda d: (d["ttl_min"] > 20) & (d["ttl_min"] <= 45))
        add(3, f"{variant}:ttl_45_65", "Early entry: 45-65 minutes to close", variant, lambda d: (d["ttl_min"] > 45) & (d["ttl_min"] <= 65))

    # Round 4: momentum/shape proxies available in historical output.
    for variant in base_variants:
        add(4, f"{variant}:side_ret10_pos", "Recent 10m side-aligned move positive", variant, lambda d: pd.to_numeric(d.get("side_ret_10m", 0), errors="coerce") > 0)
        add(4, f"{variant}:side_ret10_gt75", "Recent 10m side-aligned move >$75", variant, lambda d: pd.to_numeric(d.get("side_ret_10m", 0), errors="coerce") > 75)
        add(4, f"{variant}:side_ret10_neg", "Recent 10m side-aligned move negative", variant, lambda d: pd.to_numeric(d.get("side_ret_10m", 0), errors="coerce") <= 0)
        add(4, f"{variant}:distance_sigma_gt075", "Distance >0.75 expected sigma", variant, lambda d: pd.to_numeric(d.get("distance_sigma", np.nan), errors="coerce") > 0.75)
        add(4, f"{variant}:distance_sigma_lt075", "Distance <=0.75 expected sigma", variant, lambda d: pd.to_numeric(d.get("distance_sigma", np.nan), errors="coerce") <= 0.75)
        add(4, f"{variant}:spread_1c", "Spread <=1c", variant, lambda d: d["spread_cents"] <= 1.01)
        add(4, f"{variant}:spread_2c", "Spread 1-2c", variant, lambda d: (d["spread_cents"] > 1.01) & (d["spread_cents"] <= 2.01))

    # Round 5: combined deployable hypotheses informed by previous buckets.
    add(
        5,
        "deploy:market_shrink_core",
        "Market-shrink core: no cautious + entry <=70 + side distance positive",
        "market_shrink_no_cautious",
        lambda d: (d["entry_price"] <= 0.70) & (d["side_distance_usd"] > 0),
    )
    add(
        5,
        "deploy:market_shrink_no_late",
        "Market-shrink NO only late/mid with entry <=70",
        "market_shrink_no_cautious",
        lambda d: (d["side"] == "no") & (d["entry_price"] <= 0.70) & (d["ttl_min"] <= 45),
    )
    add(
        5,
        "deploy:market_shrink_yes_quality",
        "Market-shrink YES with side distance >50 and entry <=70",
        "market_shrink_no_cautious",
        lambda d: (d["side"] == "yes") & (d["side_distance_usd"] > 50) & (d["entry_price"] <= 0.70),
    )
    add(
        5,
        "deploy:shape_adjacent_all",
        "Shape-adjacent as-is",
        "market_shrink_no_cautious_shape_adjacent",
        lambda d: pd.Series(True, index=d.index),
    )
    add(
        5,
        "deploy:shape_adjacent_entry_le72",
        "Shape-adjacent but avoid >72c favorites",
        "market_shrink_no_cautious_shape_adjacent",
        lambda d: d["entry_price"] <= 0.72,
    )
    add(
        5,
        "deploy:baseline_no_lt16_no_scale",
        "Baseline research NO <16c only, no scale",
        "baseline_current",
        lambda d: (d["side"] == "no") & (d["net_edge_cents"] < 16),
    )
    add(
        5,
        "deploy:baseline_no_14_16_entry_55_65",
        "Baseline NO 14-16c, entry 55-65c",
        "baseline_current",
        lambda d: (d["side"] == "no") & (d["net_edge_cents"] >= 14) & (d["net_edge_cents"] < 16) & (d["entry_price"].between(0.55, 0.65)),
    )
    add(
        5,
        "deploy:baseline_avoid_high_edge_no",
        "Baseline excluding high-edge NO >=16c",
        "baseline_current",
        lambda d: ~((d["side"] == "no") & (d["net_edge_cents"] >= 16)),
    )
    add(
        5,
        "deploy:baseline_entry_le65_distance_pos",
        "Baseline entry <=65c and side distance positive",
        "baseline_current",
        lambda d: (d["entry_price"] <= 0.65) & (d["side_distance_usd"] > 0),
    )
    add(
        5,
        "deploy:market_shrink_low_spread",
        "Market-shrink spread <=1c",
        "market_shrink_no_cautious",
        lambda d: d["spread_cents"] <= 1.01,
    )
    add(
        5,
        "deploy:market_shrink_no_only",
        "Market-shrink NO side only",
        "market_shrink_no_cautious",
        lambda d: d["side"] == "no",
    )
    add(
        5,
        "deploy:market_shrink_no_low_edge",
        "Market-shrink NO with net edge <16c",
        "market_shrink_no_cautious",
        lambda d: (d["side"] == "no") & (d["net_edge_cents"] < 16),
    )
    add(
        5,
        "deploy:market_shrink_favorite",
        "Market-shrink favorite-priced entries",
        "market_shrink_no_cautious",
        lambda d: d["entry_price"] > 0.65,
    )
    add(
        5,
        "deploy:market_shrink_late",
        "Market-shrink 5-20 minute entries",
        "market_shrink_no_cautious",
        lambda d: (d["ttl_min"] >= 5) & (d["ttl_min"] <= 20),
    )
    add(
        5,
        "deploy:market_shrink_no_low_spread_favorite",
        "Market-shrink NO, spread <=1c, entry >55c",
        "market_shrink_no_cautious",
        lambda d: (d["side"] == "no") & (d["spread_cents"] <= 1.01) & (d["entry_price"] > 0.55),
    )
    add(
        5,
        "deploy:shape_adjacent_favorite",
        "Shape-adjacent favorite-priced entries",
        "market_shrink_no_cautious_shape_adjacent",
        lambda d: d["entry_price"] > 0.65,
    )
    add(
        5,
        "deploy:shape_adjacent_distance_sigma",
        "Shape-adjacent and distance >0.75 expected sigma",
        "market_shrink_no_cautious_shape_adjacent",
        lambda d: pd.to_numeric(d.get("distance_sigma", np.nan), errors="coerce") > 0.75,
    )
    add(
        5,
        "deploy:shape_adjacent_yes_favorite",
        "Shape-adjacent YES favorite-priced entries",
        "market_shrink_no_cautious_shape_adjacent",
        lambda d: (d["side"] == "yes") & (d["entry_price"] > 0.65),
    )
    add(
        5,
        "deploy:baseline_late_only",
        "Baseline 5-20 minute entries only",
        "baseline_current",
        lambda d: (d["ttl_min"] >= 5) & (d["ttl_min"] <= 20),
    )
    add(
        5,
        "deploy:baseline_yes_entry_le65",
        "Baseline YES with entry <=65c",
        "baseline_current",
        lambda d: (d["side"] == "yes") & (d["entry_price"] <= 0.65),
    )
    add(
        5,
        "deploy:baseline_no_high_edge_entry_gt60",
        "Baseline NO high edge with entry >60c",
        "baseline_current",
        lambda d: (d["side"] == "no") & (d["net_edge_cents"] >= 16) & (d["entry_price"] > 0.60),
    )

    # Round 6: refinement around the only baseline candidate that survived both
    # historical splits and forward official-result ledgers: 5-20 minute TTL.
    add(6, "refine:late_5_20_yes", "Late-only YES", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side"] == "yes"))
    add(6, "refine:late_5_20_no", "Late-only NO", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side"] == "no"))
    add(6, "refine:late_5_20_entry_le60", "Late-only entry <=60c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["entry_price"] <= 0.60))
    add(6, "refine:late_5_20_entry_le65", "Late-only entry <=65c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["entry_price"] <= 0.65))
    add(6, "refine:late_5_20_entry_gt65", "Late-only entry >65c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["entry_price"] > 0.65))
    add(6, "refine:late_5_20_edge_lt16", "Late-only net edge <16c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["net_edge_cents"] < 16))
    add(6, "refine:late_5_20_edge_gte16", "Late-only net edge >=16c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["net_edge_cents"] >= 16))
    add(6, "refine:late_5_20_no_lt16", "Late-only NO with edge <16c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side"] == "no") & (d["net_edge_cents"] < 16))
    add(6, "refine:late_5_20_no_gte16", "Late-only NO with edge >=16c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side"] == "no") & (d["net_edge_cents"] >= 16))
    add(6, "refine:late_5_20_yes_entry_le65", "Late-only YES entry <=65c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side"] == "yes") & (d["entry_price"] <= 0.65))
    add(6, "refine:late_5_20_no_entry_55_70", "Late-only NO entry 55-70c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side"] == "no") & d["entry_price"].between(0.55, 0.70))
    add(6, "refine:late_5_20_distance_gt50", "Late-only side distance >$50", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side_distance_usd"] > 50))
    add(6, "refine:late_5_20_distance_gt100", "Late-only side distance >$100", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["side_distance_usd"] > 100))
    add(6, "refine:late_5_20_distance_sigma_gt075", "Late-only distance >0.75 sigma", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (pd.to_numeric(d.get("distance_sigma", np.nan), errors="coerce") > 0.75))
    add(6, "refine:late_5_20_spread_1c", "Late-only spread <=1c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["spread_cents"] <= 1.01))
    add(6, "refine:late_5_20_spread_2c", "Late-only spread 1-2c", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (d["spread_cents"] > 1.01) & (d["spread_cents"] <= 2.01))
    add(6, "refine:late_5_20_side_ret10_pos", "Late-only side-aligned 10m move positive", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (pd.to_numeric(d.get("side_ret_10m", 0), errors="coerce") > 0))
    add(6, "refine:late_5_20_side_ret10_neg", "Late-only side-aligned 10m move non-positive", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & (pd.to_numeric(d.get("side_ret_10m", 0), errors="coerce") <= 0))
    add(6, "refine:late_5_12", "Very late 5-12 minute entries", "baseline_current", lambda d: d["ttl_min"].between(5, 12))
    add(6, "refine:late_12_20", "Late 12-20 minute entries", "baseline_current", lambda d: (d["ttl_min"] > 12) & (d["ttl_min"] <= 20))
    add(6, "refine:late_5_20_avoid_high_edge_no", "Late-only excluding high-edge NO", "baseline_current", lambda d: d["ttl_min"].between(5, 20) & ~((d["side"] == "no") & (d["net_edge_cents"] >= 16)))
    return hyps


def rank_candidates(results: pd.DataFrame) -> pd.DataFrame:
    hist = results[(results["source"] == "historical_duckdb") & (results["split"].isin(["train", "validation", "test"]))].copy()
    grouped = []
    for key, part in hist.groupby(["round", "hypothesis_id", "description", "variant"], sort=False):
        splits = {row["split"]: row for _, row in part.iterrows()}
        if not {"train", "validation", "test"} <= set(splits):
            continue
        train = splits["train"]
        val = splits["validation"]
        test = splits["test"]
        all_pnl = float(part["pnl"].sum())
        all_premium = float(part["premium"].sum())
        grouped.append(
            {
                "round": key[0],
                "hypothesis_id": key[1],
                "description": key[2],
                "variant": key[3],
                "train_trades": int(train["trades"]),
                "val_trades": int(val["trades"]),
                "test_trades": int(test["trades"]),
                "train_pnl": float(train["pnl"]),
                "val_pnl": float(val["pnl"]),
                "test_pnl": float(test["pnl"]),
                "all_pnl": all_pnl,
                "all_premium": all_premium,
                "all_rop": all_pnl / all_premium if all_premium else 0.0,
                "worst_split_pnl": min(float(train["pnl"]), float(val["pnl"]), float(test["pnl"])),
                "max_drawdown_worst": min(float(train["max_drawdown"]), float(val["max_drawdown"]), float(test["max_drawdown"])),
                "total_trades": int(train["trades"] + val["trades"] + test["trades"]),
            }
        )
    ranked = pd.DataFrame(grouped)
    if ranked.empty:
        return ranked
    ranked["split_consistent"] = (
        (ranked["train_pnl"] > 0)
        & (ranked["val_pnl"] > 0)
        & (ranked["test_pnl"] > 0)
        & (ranked["val_trades"] >= 3)
        & (ranked["test_trades"] >= 3)
    )
    ranked["score"] = (
        ranked["all_pnl"]
        + 2.0 * ranked["worst_split_pnl"]
        + 0.25 * ranked["all_rop"] * 100.0
        + 0.05 * ranked["total_trades"]
        + ranked["max_drawdown_worst"]
    )
    return ranked.sort_values(["split_consistent", "score", "all_pnl"], ascending=[False, False, False])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-trades", type=Path, default=DEFAULT_HISTORICAL_TRADES)
    parser.add_argument("--official-audit", type=Path, default=DEFAULT_OFFICIAL_AUDIT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    historical = add_common_features(pd.read_csv(args.historical_trades))
    forward = add_common_features(pd.read_csv(args.official_audit))
    forward = forward[forward["ledger"] != "shadow_js_guarded"].copy()
    forward = forward[forward["pnl"].notna() & forward["premium"].notna()].copy()
    hypotheses = make_hypotheses()
    results = evaluate_hypotheses(historical, forward, hypotheses)
    ranked = rank_candidates(results)

    results.to_csv(args.output_dir / "hypothesis_results_long.csv", index=False)
    ranked.to_csv(args.output_dir / "hypothesis_ranked.csv", index=False)
    pd.DataFrame(
        [
            {
                "round": h.round_no,
                "hypothesis_id": h.hypothesis_id,
                "description": h.description,
                "variant": h.variant,
            }
            for h in hypotheses
        ]
    ).to_csv(args.output_dir / "hypothesis_manifest.csv", index=False)

    print(f"hypotheses={len(hypotheses)}")
    print(f"output_dir={args.output_dir}")
    if not ranked.empty:
        print("\nTOP SPLIT-CONSISTENT HISTORICAL CANDIDATES")
        cols = [
            "round",
            "hypothesis_id",
            "variant",
            "total_trades",
            "train_pnl",
            "val_pnl",
            "test_pnl",
            "all_pnl",
            "all_rop",
            "worst_split_pnl",
            "max_drawdown_worst",
            "score",
        ]
        print(ranked[ranked["split_consistent"]].head(20)[cols].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
        print("\nTOP OVERALL")
        print(ranked.head(20)[cols + ["split_consistent"]].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
