#!/usr/bin/env python3
"""Audit BTC1H active-candidate holdout independence and concentration.

This is research-only.  The multi-holdout report says whether each bucket is
positive, but it can hide duplicated market/side rows and event concentration.
This audit keeps the frozen active BTC1H policy fixed and asks how much of the
edge survives de-duplicating market/side rows, removing one holdout at a time,
and removing the largest event cluster inside each holdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_btc1h_decision_distance_guard_audit import (  # noqa: E402
    ACTIVE_LEDGER,
    ACTIVE_VARIANT,
    DEFAULT_OUT as DISTANCE_DEFAULT_OUT,
    as_bool,
    default_paths,
    markdown_table,
    max_drawdown,
    prepare_forward,
    prepare_historical,
    read_csv,
    sharpe,
)


DEFAULT_OUT = DISTANCE_DEFAULT_OUT.parent / "btc1h_holdout_independence_audit_latest_codex"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--shadow-official-trades", type=Path, default=None)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    return parser.parse_args()


def event_from_market(market_ticker: Any) -> str:
    text = str(market_ticker or "").upper()
    if "-T" in text:
        return text.split("-T", 1)[0]
    return text


def add_identity_columns(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    for col in ["event_ticker", "market_ticker", "side", "holdout", "evidence_source"]:
        if col not in work.columns:
            work[col] = ""
    work["market_ticker"] = work["market_ticker"].astype(str).str.upper()
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    missing_event = work["event_ticker"].isin(["", "NAN", "NONE"])
    work.loc[missing_event, "event_ticker"] = work.loc[missing_event, "market_ticker"].map(event_from_market)
    work["side"] = work["side"].astype(str).str.lower()
    work["market_side_key"] = work["market_ticker"] + "|" + work["side"]
    work["holdout_key"] = work["evidence_source"].astype(str) + "|" + work["holdout"].astype(str)
    return work


def unique_market_side_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    sort_cols = [col for col in ["entry_time", "event_ticker", "market_ticker", "side"] if col in df.columns]
    return df.sort_values(sort_cols).drop_duplicates("market_side_key", keep="first").reset_index(drop=True)


def historical_rows(args: argparse.Namespace) -> pd.DataFrame:
    paths = default_paths(args)
    robustness = prepare_historical(read_csv(paths["robustness_trades"]), "robustness_trade_logs", args.stress_cents)
    direct = prepare_historical(read_csv(paths["direct_trades"]), "direct_predexon_trade_logs", args.stress_cents)
    frames = [df for df in [robustness, direct] if not df.empty]
    if not frames:
        return pd.DataFrame()
    work = pd.concat(frames, ignore_index=True, sort=False)
    work = add_identity_columns(work)
    work["pnl_for_audit"] = pd.to_numeric(work["research_pnl_stressed"], errors="coerce")
    work["premium_for_audit"] = pd.to_numeric(work["research_premium_stressed"], errors="coerce")
    work["win_for_audit"] = work["research_win"].astype(bool)
    work["settlement_label"] = "proxy_or_captured_result_plus_adverse_entry_stress"
    return work.dropna(subset=["pnl_for_audit", "premium_for_audit"]).reset_index(drop=True)


def forward_rows(args: argparse.Namespace) -> pd.DataFrame:
    paths = default_paths(args)
    forward = prepare_forward(read_csv(paths["shadow_official_trades"]))
    if forward.empty:
        return pd.DataFrame()
    work = add_identity_columns(forward)
    work["pnl_for_audit"] = pd.to_numeric(work["official_pnl"], errors="coerce")
    work["premium_for_audit"] = pd.to_numeric(work["official_premium"], errors="coerce")
    work["win_for_audit"] = as_bool(work["official_win_bool"])
    work["settlement_label"] = "kalshi_rest_official_current_stale_diagnostic"
    return work.dropna(subset=["pnl_for_audit", "premium_for_audit"]).reset_index(drop=True)


def summarize_rows(df: pd.DataFrame, panel: str) -> dict[str, Any]:
    if df.empty:
        return {
            "panel": panel,
            "rows": 0,
            "status": "NO_ROWS",
        }
    pnl = pd.to_numeric(df["pnl_for_audit"], errors="coerce").fillna(0.0)
    premium = pd.to_numeric(df["premium_for_audit"], errors="coerce").fillna(0.0)
    wins = df["win_for_audit"].astype(bool)
    event_pnl = df.groupby("event_ticker", dropna=False)["pnl_for_audit"].sum().sort_values()
    unique = unique_market_side_rows(df)
    unique_pnl = pd.to_numeric(unique["pnl_for_audit"], errors="coerce").fillna(0.0)
    duplicate_rows = int(df.duplicated("market_side_key").sum())
    largest_positive_event = float(event_pnl.max()) if len(event_pnl) else 0.0
    largest_negative_event = float(event_pnl.min()) if len(event_pnl) else 0.0
    total = float(pnl.sum())
    leave_one_event_min = float((total - event_pnl).min()) if len(event_pnl) else total
    status = "PASS_RESEARCH_INDEPENDENCE"
    blockers: list[str] = []
    if duplicate_rows > 0:
        blockers.append("duplicate_market_side_rows")
    if len(event_pnl) < 5 and panel.startswith("historical"):
        blockers.append("few_event_clusters")
    if float(unique_pnl.sum()) <= 0.0:
        blockers.append("unique_market_side_pnl_not_positive")
    if leave_one_event_min <= 0.0:
        blockers.append("single_event_removal_can_flip_pnl")
    if panel.startswith("forward"):
        status = "DIAGNOSTIC_ONLY_STALE_OFFICIAL"
    elif blockers:
        status = "WEAK_OR_CONCENTRATED_RESEARCH"
    return {
        "panel": panel,
        "status": status,
        "rows": int(len(df)),
        "unique_market_side_rows": int(len(unique)),
        "duplicate_market_side_rows": duplicate_rows,
        "event_clusters": int(len(event_pnl)),
        "holdouts": int(df["holdout_key"].nunique()) if "holdout_key" in df.columns else 0,
        "pnl": round(total, 6),
        "unique_market_side_pnl": round(float(unique_pnl.sum()), 6),
        "win_rate": round(float(wins.mean()), 6),
        "premium": round(float(premium.sum()), 6),
        "max_drawdown": round(max_drawdown(pnl), 6),
        "sharpe": round(sharpe(pnl), 6),
        "largest_positive_event_pnl": round(largest_positive_event, 6),
        "largest_negative_event_pnl": round(largest_negative_event, 6),
        "leave_one_event_min_pnl": round(leave_one_event_min, 6),
        "blockers": ";".join(blockers),
        "deployable_now": False,
    }


def build_holdout_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (source, holdout), group in df.groupby(["evidence_source", "holdout"], dropna=False, sort=True):
        row = summarize_rows(group, f"{source}|{holdout}")
        row.update({"evidence_source": source, "holdout": holdout})
        rows.append(row)
    return pd.DataFrame(rows)


def build_leave_one_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total = float(pd.to_numeric(df["pnl_for_audit"], errors="coerce").fillna(0.0).sum()) if not df.empty else 0.0
    for holdout_key, group in df.groupby("holdout_key", dropna=False, sort=True):
        remaining = df[df["holdout_key"] != holdout_key]
        unique_remaining = remaining.drop_duplicates("market_side_key", keep="first")
        rows.append(
            {
                "removed_holdout_key": holdout_key,
                "removed_rows": int(len(group)),
                "removed_pnl": round(float(pd.to_numeric(group["pnl_for_audit"], errors="coerce").fillna(0.0).sum()), 6),
                "remaining_rows": int(len(remaining)),
                "remaining_event_clusters": int(remaining["event_ticker"].nunique()) if not remaining.empty else 0,
                "remaining_pnl": round(float(pd.to_numeric(remaining["pnl_for_audit"], errors="coerce").fillna(0.0).sum()), 6),
                "remaining_unique_market_side_pnl": round(
                    float(pd.to_numeric(unique_remaining["pnl_for_audit"], errors="coerce").fillna(0.0).sum()), 6
                )
                if not unique_remaining.empty
                else 0.0,
                "remaining_pnl_fraction_of_total": round(
                    float(pd.to_numeric(remaining["pnl_for_audit"], errors="coerce").fillna(0.0).sum()) / total, 6
                )
                if abs(total) > 1e-12
                else 0.0,
                "flips_remaining_pnl_nonpositive": bool(
                    float(pd.to_numeric(remaining["pnl_for_audit"], errors="coerce").fillna(0.0).sum()) <= 0.0
                ),
            }
        )
    return pd.DataFrame(rows)


def build_top_cluster_rows(df: pd.DataFrame, limit: int = 25) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    grouped = (
        df.groupby(["event_ticker", "evidence_source", "holdout"], dropna=False)
        .agg(
            rows=("pnl_for_audit", "size"),
            market_side_rows=("market_side_key", "nunique"),
            pnl=("pnl_for_audit", "sum"),
            premium=("premium_for_audit", "sum"),
            first_entry=("entry_time", "min"),
            last_entry=("entry_time", "max"),
        )
        .reset_index()
    )
    grouped["abs_pnl"] = grouped["pnl"].abs()
    return grouped.sort_values(["abs_pnl", "rows"], ascending=[False, False]).head(limit)


def write_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    paths = default_paths(args)

    hist = historical_rows(args)
    fwd = forward_rows(args)
    if hist.empty and fwd.empty:
        raise SystemExit("No active BTC1H candidate rows found for holdout independence audit.")

    hist_unique = unique_market_side_rows(hist) if not hist.empty else pd.DataFrame()
    panels = [
        summarize_rows(hist, "historical_all_rows"),
        summarize_rows(hist_unique, "historical_unique_market_side_rows"),
    ]
    if not fwd.empty:
        panels.append(summarize_rows(fwd, "forward_official_stale_rows"))
    summary = pd.DataFrame(panels)
    holdouts = build_holdout_rows(hist) if not hist.empty else pd.DataFrame()
    leave_one = build_leave_one_rows(hist) if not hist.empty else pd.DataFrame()
    top_clusters = build_top_cluster_rows(hist) if not hist.empty else pd.DataFrame()

    input_cols = [
        "evidence_source",
        "holdout",
        "event_ticker",
        "market_ticker",
        "side",
        "market_side_key",
        "entry_time",
        "entry_price",
        "pnl_for_audit",
        "premium_for_audit",
        "win_for_audit",
        "settlement_label",
    ]
    write_csv(args.out_dir / "btc1h_holdout_independence_summary.csv", summary)
    write_csv(args.out_dir / "btc1h_holdout_independence_by_holdout.csv", holdouts)
    write_csv(args.out_dir / "btc1h_holdout_independence_leave_one_holdout.csv", leave_one)
    write_csv(args.out_dir / "btc1h_holdout_independence_top_clusters.csv", top_clusters)
    write_csv(args.out_dir / "btc1h_holdout_independence_input_rows.csv", hist[[c for c in input_cols if c in hist.columns]])

    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": ACTIVE_VARIANT,
        "active_ledger": ACTIVE_LEDGER,
        "stress_cents": args.stress_cents,
        "deployable_now": False,
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
        "note": "Research-only independence audit. Historical rows are proxy/captured; forward official rows are stale diagnostics until clean evidence clock starts.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    report_cols = [
        "panel",
        "status",
        "rows",
        "unique_market_side_rows",
        "duplicate_market_side_rows",
        "event_clusters",
        "holdouts",
        "pnl",
        "unique_market_side_pnl",
        "leave_one_event_min_pnl",
        "blockers",
    ]
    holdout_cols = [
        "evidence_source",
        "holdout",
        "status",
        "rows",
        "unique_market_side_rows",
        "event_clusters",
        "pnl",
        "unique_market_side_pnl",
        "leave_one_event_min_pnl",
        "blockers",
    ]
    report = [
        "# BTC1H Holdout Independence Audit",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Variant: `{ACTIVE_VARIANT}`",
        f"Historical/proxy adverse entry stress: `+{args.stress_cents:.1f}c`.",
        "",
        "## Verdict",
        "",
        "- Research-only diagnostic. This cannot promote the strategy.",
        "- A positive pooled result is weaker when duplicated market/side rows or one event cluster drive the edge.",
        "- The forward official panel is stale until a clean scan-time evidence clock exists.",
        "",
        "## Summary",
        "",
        markdown_table(summary[[c for c in report_cols if c in summary.columns]]),
        "",
        "## Holdout Concentration",
        "",
        markdown_table(holdouts[[c for c in holdout_cols if c in holdouts.columns]] if not holdouts.empty else holdouts),
        "",
        "## Leave-One-Holdout",
        "",
        markdown_table(leave_one if not leave_one.empty else leave_one),
        "",
        "## Largest Event Clusters",
        "",
        markdown_table(top_clusters if not top_clusters.empty else top_clusters),
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
