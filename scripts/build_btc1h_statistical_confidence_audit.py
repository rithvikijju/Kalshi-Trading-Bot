#!/usr/bin/env python3
"""Statistical confidence audit for the active BTC1H candidate.

This is research-only.  It estimates how fragile the current BTC1H edge looks
under row bootstrap, event-cluster bootstrap, and a simple per-trade
breakeven-null simulation.  It does not promote, retune, deploy, or start
processes.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_btc1h_decision_distance_guard_audit import (  # noqa: E402
    ACTIVE_LEDGER,
    ACTIVE_VARIANT,
    DEFAULT_OUT as DISTANCE_DEFAULT_OUT,
    default_paths,
    markdown_table,
    prepare_forward,
    prepare_historical,
    read_csv,
)


DEFAULT_OUT = DISTANCE_DEFAULT_OUT.parent / "btc1h_statistical_confidence_audit_latest_codex"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--shadow-official-trades", type=Path, default=None)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--bootstrap-sims", type=int, default=20000)
    parser.add_argument("--null-sims", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20260522)
    return parser.parse_args()


def max_drawdown(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0
    equity = np.cumsum(arr)
    return float(np.min(equity - np.maximum.accumulate(equity)))


def sharpe(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12 or not math.isfinite(std):
        return 0.0
    return float(np.mean(arr) / std * math.sqrt(arr.size))


def add_key_columns(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    for col in ["event_ticker", "market_ticker", "side"]:
        if col not in work.columns:
            work[col] = ""
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    work["market_ticker"] = work["market_ticker"].astype(str).str.upper()
    work["side"] = work["side"].astype(str).str.lower()
    work["market_side_key"] = work["market_ticker"] + "|" + work["side"]
    return work


def panel_from_historical(df: pd.DataFrame, panel: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = add_key_columns(df)
    work["panel"] = panel
    work["pnl_for_stats"] = pd.to_numeric(work["research_pnl_stressed"], errors="coerce")
    work["premium_for_stats"] = pd.to_numeric(work["research_premium_stressed"], errors="coerce")
    work["win_for_stats"] = work["research_win"].astype(bool)
    work["settlement_label"] = "proxy_or_captured_result_plus_adverse_entry_stress"
    return work.dropna(subset=["pnl_for_stats", "premium_for_stats"]).reset_index(drop=True)


def panel_from_forward(df: pd.DataFrame, panel: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = add_key_columns(df)
    work["panel"] = panel
    work["pnl_for_stats"] = pd.to_numeric(work["official_pnl"], errors="coerce")
    work["premium_for_stats"] = pd.to_numeric(work["official_premium"], errors="coerce")
    work["win_for_stats"] = work["official_win_bool"].astype(bool)
    work["settlement_label"] = "kalshi_rest_official_current_stale_diagnostic"
    return work.dropna(subset=["pnl_for_stats", "premium_for_stats"]).reset_index(drop=True)


def unique_market_panel(df: pd.DataFrame, panel: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    sort_cols = [col for col in ["entry_time", "event_ticker", "market_ticker", "side"] if col in df.columns]
    work = df.sort_values(sort_cols).drop_duplicates("market_side_key", keep="first").copy()
    work["panel"] = panel
    return work.reset_index(drop=True)


def bootstrap_total_pnl(pnl: np.ndarray, sims: int, rng: np.random.Generator) -> tuple[float, float, float, float]:
    if pnl.size == 0:
        return 0.0, 0.0, 0.0, 0.0
    draws = rng.integers(0, pnl.size, size=(sims, pnl.size))
    totals = pnl[draws].sum(axis=1)
    return (
        float(np.quantile(totals, 0.025)),
        float(np.quantile(totals, 0.5)),
        float(np.quantile(totals, 0.975)),
        float(np.mean(totals <= 0.0)),
    )


def cluster_bootstrap_total_pnl(
    df: pd.DataFrame, sims: int, rng: np.random.Generator
) -> tuple[float, float, float, float, int]:
    if df.empty:
        return 0.0, 0.0, 0.0, 0.0, 0
    clusters = (
        df.groupby("event_ticker", dropna=False)["pnl_for_stats"]
        .sum()
        .astype(float)
        .to_numpy(dtype=float)
    )
    if clusters.size == 0:
        return 0.0, 0.0, 0.0, 0.0, 0
    draws = rng.integers(0, clusters.size, size=(sims, clusters.size))
    totals = clusters[draws].sum(axis=1)
    return (
        float(np.quantile(totals, 0.025)),
        float(np.quantile(totals, 0.5)),
        float(np.quantile(totals, 0.975)),
        float(np.mean(totals <= 0.0)),
        int(clusters.size),
    )


def breakeven_null_pvalue(
    premium: np.ndarray, observed_pnl: float, sims: int, rng: np.random.Generator
) -> float:
    if premium.size == 0:
        return 1.0
    probs = np.clip(premium.astype(float), 0.0, 1.0)
    wins = rng.random((sims, premium.size)) < probs
    simulated = wins.sum(axis=1).astype(float) - premium.sum()
    return float((np.sum(simulated >= observed_pnl) + 1.0) / (sims + 1.0))


def summarize_panel(df: pd.DataFrame, panel: str, args: argparse.Namespace, rng: np.random.Generator) -> dict[str, object]:
    if df.empty:
        return {
            "panel": panel,
            "rows": 0,
            "status": "NO_ROWS",
        }
    pnl = pd.to_numeric(df["pnl_for_stats"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    premium = pd.to_numeric(df["premium_for_stats"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    wins = df["win_for_stats"].astype(bool)
    boot_low, boot_mid, boot_high, boot_prob_negative = bootstrap_total_pnl(pnl, args.bootstrap_sims, rng)
    cl_low, cl_mid, cl_high, cl_prob_negative, cluster_count = cluster_bootstrap_total_pnl(df, args.bootstrap_sims, rng)
    null_p = breakeven_null_pvalue(premium, float(pnl.sum()), args.null_sims, rng)
    duplicate_keys = int(df.duplicated("market_side_key").sum()) if "market_side_key" in df else 0
    status = "PASS_RESEARCH_STAT"
    if panel.startswith("forward_official"):
        status = "DIAGNOSTIC_ONLY_STALE_OFFICIAL"
    elif boot_low <= 0.0 or cl_low <= 0.0 or null_p > 0.05:
        status = "WEAK_OR_FRAGILE_RESEARCH_STAT"
    return {
        "panel": panel,
        "status": status,
        "rows": int(len(df)),
        "event_clusters": cluster_count,
        "duplicate_market_side_rows": duplicate_keys,
        "pnl": round(float(pnl.sum()), 6),
        "mean_pnl": round(float(np.mean(pnl)), 6),
        "win_rate": round(float(wins.mean()), 6),
        "premium": round(float(premium.sum()), 6),
        "max_drawdown": round(max_drawdown(pnl), 6),
        "sharpe": round(sharpe(pnl), 6),
        "row_bootstrap_total_pnl_p025": round(boot_low, 6),
        "row_bootstrap_total_pnl_p50": round(boot_mid, 6),
        "row_bootstrap_total_pnl_p975": round(boot_high, 6),
        "row_bootstrap_prob_total_pnl_lte_0": round(boot_prob_negative, 6),
        "event_cluster_bootstrap_total_pnl_p025": round(cl_low, 6),
        "event_cluster_bootstrap_total_pnl_p50": round(cl_mid, 6),
        "event_cluster_bootstrap_total_pnl_p975": round(cl_high, 6),
        "event_cluster_bootstrap_prob_total_pnl_lte_0": round(cl_prob_negative, 6),
        "breakeven_null_pvalue": round(null_p, 6),
        "settlement_label": str(df["settlement_label"].iloc[0]),
        "deployable_now": False,
    }


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.bootstrap_sims < 100 or args.null_sims < 100:
        raise SystemExit("Use at least 100 simulations for stable diagnostics.")
    rng = np.random.default_rng(args.seed)
    paths = default_paths(args)
    robustness = panel_from_historical(
        prepare_historical(read_csv(paths["robustness_trades"]), "robustness_trade_logs", args.stress_cents),
        "historical_robustness_rows",
    )
    direct = panel_from_historical(
        prepare_historical(read_csv(paths["direct_trades"]), "direct_predexon_trade_logs", args.stress_cents),
        "historical_direct_rows",
    )
    historical_naive = pd.concat([robustness, direct], ignore_index=True, sort=False)
    historical_naive["panel"] = "historical_naive_all_rows"
    historical_unique = unique_market_panel(historical_naive, "historical_unique_market_side")
    forward = panel_from_forward(prepare_forward(read_csv(paths["shadow_official_trades"])), "forward_official_stale_rows")

    panels = [historical_naive, historical_unique, robustness, direct, forward]
    all_rows = pd.concat([df for df in panels if not df.empty], ignore_index=True, sort=False) if panels else pd.DataFrame()
    cols = [
        "panel",
        "evidence_source",
        "event_ticker",
        "market_ticker",
        "side",
        "market_side_key",
        "entry_time",
        "entry_price",
        "pnl_for_stats",
        "premium_for_stats",
        "win_for_stats",
        "settlement_label",
    ]
    all_rows[[c for c in cols if c in all_rows.columns]].to_csv(args.out_dir / "btc1h_statistical_input_rows.csv", index=False)
    summary = pd.DataFrame([summarize_panel(df, str(df["panel"].iloc[0]) if not df.empty else "empty", args, rng) for df in panels if not df.empty])
    summary.to_csv(args.out_dir / "btc1h_statistical_confidence_summary.csv", index=False)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": ACTIVE_VARIANT,
        "active_ledger": ACTIVE_LEDGER,
        "bootstrap_sims": args.bootstrap_sims,
        "null_sims": args.null_sims,
        "seed": args.seed,
        "stress_cents": args.stress_cents,
        "deployable_now": False,
        "note": "Research-only statistical confidence audit. Historical labels are proxy/captured; forward official rows are stale diagnostics until clean evidence clock starts.",
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    compact_cols = [
        "panel",
        "status",
        "rows",
        "event_clusters",
        "duplicate_market_side_rows",
        "pnl",
        "win_rate",
        "max_drawdown",
        "sharpe",
        "row_bootstrap_total_pnl_p025",
        "event_cluster_bootstrap_total_pnl_p025",
        "breakeven_null_pvalue",
    ]
    report = [
        "# BTC1H Statistical Confidence Audit",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Variant: `{ACTIVE_VARIANT}`",
        f"Bootstrap sims: `{args.bootstrap_sims}`; breakeven-null sims: `{args.null_sims}`; seed: `{args.seed}`.",
        "",
        "## Verdict",
        "",
        "- Research-only diagnostic. No statistical result here can bypass official-settlement, clean-clock, execution, or row-replay gates.",
        "- Use the unique-market-side and event-cluster columns to avoid over-reading duplicated/cadence-overlapping historical rows.",
        "- Forward official rows remain stale diagnostics until the clean scan-time evidence clock exists.",
        "",
        "## Summary",
        "",
        markdown_table(summary[compact_cols] if not summary.empty else summary),
        "",
        "## Interpretation",
        "",
        "- A positive historical bootstrap lower bound supports continued research, not deployment.",
        "- A weak or negative cluster lower bound means the apparent edge is sensitive to event grouping.",
        "- A stale official panel with a wide/negative lower bound reinforces the need for clean forward evidence before promotion.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
