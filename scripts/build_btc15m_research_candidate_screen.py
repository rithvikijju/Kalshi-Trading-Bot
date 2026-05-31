#!/usr/bin/env python3
"""Consolidate BTC15M research artifacts into a next-action screen.

The screen is intentionally conservative. It does not tune thresholds, start
processes, or let proxy-only rows become promotion evidence. Its purpose is to
turn the current branch/replay/ML/regime/forward artifacts into a repeatable
decision table for whether a candidate is deployable, should keep collecting,
should be tracked as a diagnostic sidecar only, or should be rejected.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_research_candidate_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

SAMPLE_BLOCKERS = {
    "selected_rows_below_min",
    "settled_rows_below_min",
    "order_rows_below_min",
    "paper_settled_rows_below_min",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BTC15M consolidated research candidate screen.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-official-rows", type=int, default=50)
    parser.add_argument("--backtest-root", type=Path, default=BACKTEST_ROOT)
    return parser.parse_args()


def latest_dir(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def all_dirs(root: Path, pattern: str) -> list[Path]:
    return sorted([p for p in root.glob(pattern) if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def blocker_set(text: Any) -> set[str]:
    return {part for part in clean_text(text).split(";") if part}


def row(
    candidate_id: str,
    family: str,
    evidence_source: str,
    artifact: Path | None,
    status: str,
    forward_action: str,
    deployable_now: bool = False,
    can_start_new_forward: bool = False,
    trades: int = 0,
    official_rows: int = 0,
    pnl: float = 0.0,
    premium: float = 0.0,
    max_dd: float = 0.0,
    bootstrap_p05: float | None = None,
    win_rate: float | None = None,
    blockers: str = "",
    notes: str = "",
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "family": family,
        "evidence_source": evidence_source,
        "artifact": str(artifact.relative_to(PROJECT_ROOT)) if artifact and artifact.exists() else "",
        "status": status,
        "forward_action": forward_action,
        "deployable_now": deployable_now,
        "can_start_new_forward": can_start_new_forward,
        "trades": trades,
        "official_rows": official_rows,
        "pnl": round(float(pnl), 6),
        "premium": round(float(premium), 6),
        "max_drawdown": round(float(max_dd), 6),
        "bootstrap_p05": "" if bootstrap_p05 is None else round(float(bootstrap_p05), 6),
        "win_rate": "" if win_rate is None else round(float(win_rate), 6),
        "blockers": blockers,
        "notes": notes,
    }


def screen_branch_inventory(root: Path) -> list[dict[str, Any]]:
    out_dir = latest_dir(root, "btc_branch_replayability_*")
    table = read_csv(out_dir / "branch_replayability.csv" if out_dir else None)
    if table.empty:
        return [
            row(
                "branch_inventory",
                "branches",
                "branch_replayability",
                out_dir,
                "missing_evidence",
                "rerun_branch_replayability_audit",
                blockers="branch_replayability_missing",
            )
        ]
    counts = table["replayability"].astype(str).value_counts().to_dict()
    direct = int(counts.get("directly_replayable_current_btc15m", 0))
    adapter = int(counts.get("not_directly_replayable_needs_adapter", 0))
    harness = int(counts.get("not_directly_replayable_needs_harness", 0))
    non_btc = len(table) - direct - adapter - harness
    notes = f"direct={direct};needs_adapter={adapter};needs_harness={harness};other={non_btc}"
    return [
        row(
            "branch_inventory",
            "branches",
            "branch_replayability",
            out_dir,
            "screened_no_direct_new_deployable_branch",
            "keep_using_current_replay_tools;adapter_required_for_v2_family",
            trades=len(table),
            blockers="" if direct else "no_directly_replayable_current_branch",
            notes=notes,
        )
    ]


def screen_lowdd(root: Path, min_official_rows: int) -> list[dict[str, Any]]:
    out_dir = latest_dir(root, "btc15m_lowdd_forward_promotion_gate_remote*")
    summary = read_json(out_dir / "lowdd_forward_promotion_gate_summary.json" if out_dir else None)
    if not summary:
        return [
            row(
                "btc15m_lowdd_current_wrapper",
                "lowdd_forward",
                "live_paper_official_gate",
                out_dir,
                "missing_evidence",
                "rerun_remote_lowdd_refresh",
                blockers="lowdd_gate_missing",
            )
        ]

    blockers = blocker_set(summary.get("blockers", ""))
    sample_only = bool(blockers) and blockers.issubset(SAMPLE_BLOCKERS)
    production_ready = as_bool(summary.get("production_ready"))
    status = str(summary.get("research_status") or "")
    if production_ready:
        final_status = "production_ready_requires_manual_predeploy_review"
        action = "do_not_auto_deploy;run_full_predeploy_review"
    elif "promising" in status and sample_only:
        final_status = "active_forward_research_insufficient_sample"
        action = f"keep_raw_and_lowdd_running_until_{min_official_rows}_official_parity_rows"
    else:
        final_status = "blocked_or_reject"
        action = "inspect_lowdd_gate_blockers_before_continuing"

    return [
        row(
            "btc15m_lowdd_current_wrapper",
            "lowdd_forward",
            "live_paper_official_gate",
            out_dir,
            final_status,
            action,
            deployable_now=production_ready,
            trades=as_int(summary.get("paper_rows")),
            official_rows=as_int(summary.get("paper_settled_rows")),
            pnl=as_float(summary.get("paper_official_pnl")),
            premium=as_float(summary.get("paper_official_premium")),
            max_dd=as_float(summary.get("order_max_drawdown")),
            win_rate=None,
            blockers=str(summary.get("blockers") or ""),
            notes=(
                f"parity={summary.get('live_sidecar_parity_pass_rows')}/{summary.get('paper_rows')};"
                f"price_mismatches={summary.get('live_sidecar_price_mismatch_rows')};"
                f"signal_one_contract_pnl={summary.get('signal_one_contract_pnl')}"
            ),
        )
    ]


def screen_full_raw_robustness(root: Path) -> list[dict[str, Any]]:
    out_dir = latest_dir(root, "btc15m_full_raw_replay_robustness_*")
    table = read_csv(out_dir / "candidate_robustness_summary.csv" if out_dir else None)
    rows: list[dict[str, Any]] = []
    if table.empty:
        return rows
    for _, src in table.iterrows():
        verdict = clean_text(src.get("verdict"))
        strategy = clean_text(src.get("strategy"))
        p05 = as_float(src.get("bootstrap_pnl_p05"))
        blockers = clean_text(src.get("row_quality_blockers"))
        if strategy == "current_lowdd_no_rv":
            status = "supports_lowdd_but_bootstrap_fragile"
            action = "keep_as_active_forward_collection_not_deployment"
        elif "excluded" in verdict:
            status = "excluded_by_prior_bias_audit"
            action = "do_not_start"
        elif "reject" in verdict:
            status = "reject"
            action = "do_not_start"
        elif p05 <= 0:
            status = "research_promising_but_not_robust"
            action = "do_not_start_without_new_holdout"
        else:
            status = "research_promising_needs_forward_gate"
            action = "freeze_sidecar_metric_before_any_forward_use"
        rows.append(
            row(
                f"full_raw_{strategy}",
                "full_raw_live_replay",
                "full_raw_robustness",
                out_dir,
                status,
                action,
                trades=as_int(src.get("trades")),
                official_rows=as_int(src.get("official_settled_rows")),
                pnl=as_float(src.get("pnl")),
                premium=as_float(src.get("premium")),
                max_dd=as_float(src.get("max_dd")),
                bootstrap_p05=p05,
                win_rate=as_float(src.get("win_rate")),
                blockers=blockers,
                notes=verdict,
            )
        )
    return rows


def screen_walkforward(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for out_dir in all_dirs(root, "btc15m_live_replay_walkforward_filters_20260522_0530_1300*"):
        table = read_csv(out_dir / "aggregate_summary.csv")
        if table.empty:
            continue
        label = out_dir.name.replace("btc15m_live_replay_walkforward_filters_20260522_0530_1300", "walkforward")
        for _, src in table.iterrows():
            mode = clean_text(src.get("mode"))
            key = (label, mode)
            if key in seen:
                continue
            seen.add(key)
            p05 = as_float(src.get("event_bootstrap_p05"))
            pnl = as_float(src.get("pnl"))
            quality_blockers = clean_text(src.get("row_quality_blockers"))
            if mode == "baseline_oos":
                status = "baseline_oos_support_only"
                action = "do_not_treat_baseline_as_new_filter"
            elif p05 > 0 and pnl > 0 and not quality_blockers:
                status = "candidate_filter_needs_prereg_forward"
                action = "freeze_as_diagnostic_sidecar_before_trading"
            else:
                status = "reject_filter_for_new_forward"
                action = "do_not_start"
            rows.append(
                row(
                    f"{label}_{mode}",
                    "walkforward_filter",
                    "chronological_oos_filter_audit",
                    out_dir,
                    status,
                    action,
                    trades=as_int(src.get("rows")),
                    official_rows=as_int(src.get("events")),
                    pnl=pnl,
                    premium=as_float(src.get("premium")),
                    max_dd=as_float(src.get("max_dd")),
                    bootstrap_p05=p05,
                    win_rate=as_float(src.get("win_rate")),
                    blockers=quality_blockers if quality_blockers else ("" if p05 > 0 else "bootstrap_p05_not_positive"),
                    notes=f"folds_with_rows={src.get('folds_with_rows')};prob_profit={src.get('event_bootstrap_prob_profit')}",
                )
            )
    return rows


def screen_regime(root: Path, min_official_rows: int) -> list[dict[str, Any]]:
    out_dir = latest_dir(root, "btc15m_regime_live_ws_full_raw_*")
    table = read_csv(out_dir / "regime_live_ws_summary.csv" if out_dir else None)
    rows: list[dict[str, Any]] = []
    if table.empty:
        return rows
    for rule, group in table.groupby("rule", dropna=False):
        proxy = group[group["result_mode"].astype(str).eq("proxy_2c")]
        official = group[group["result_mode"].astype(str).eq("official_2c_subset")]
        proxy_row = proxy.iloc[0] if not proxy.empty else pd.Series(dtype=object)
        official_row = official.iloc[0] if not official.empty else pd.Series(dtype=object)
        proxy_pnl = as_float(proxy_row.get("pnl"))
        official_pnl = as_float(official_row.get("pnl"))
        official_rows = as_int(official_row.get("trades"))
        if proxy_pnl <= 0 or official_pnl <= 0:
            status = "reject"
            action = "do_not_start"
            blockers = "nonpositive_proxy_or_official_subset"
        elif official_rows < min_official_rows:
            status = "diagnostic_only_tiny_official_subset"
            action = "do_not_start;may_monitor_as_sidecar_metric_after_freeze"
            blockers = "official_rows_below_min"
        else:
            status = "research_promising_needs_forward_parity"
            action = "freeze_sidecar_metric_before_any_forward_use"
            blockers = ""
        rows.append(
            row(
                f"regime_{rule}",
                "regime_rule",
                "full_raw_regime_replay",
                out_dir,
                status,
                action,
                trades=as_int(proxy_row.get("trades")),
                official_rows=official_rows,
                pnl=proxy_pnl,
                premium=as_float(proxy_row.get("premium")),
                max_dd=as_float(proxy_row.get("max_dd")),
                win_rate=as_float(proxy_row.get("win_rate")),
                blockers=blockers,
                notes=f"official_subset_pnl={official_pnl};proxy_pnl={proxy_pnl}",
            )
        )
    return rows


def screen_ml(root: Path, min_official_rows: int) -> list[dict[str, Any]]:
    out_dir = latest_dir(root, "btc15m_ml_live_ws_full_raw_window_grid_*")
    table = read_csv(out_dir / "ml_window_grid_official_proxy_summary.csv" if out_dir else None)
    rows: list[dict[str, Any]] = []
    if table.empty:
        return rows
    proxy_rows = table[table["subset"].astype(str).eq("proxy_all")]
    for _, src in proxy_rows.iterrows():
        model = clean_text(src.get("model"))
        proxy_pnl = as_float(src.get("proxy_pnl_2c"))
        official_pnl = as_float(src.get("official_pnl_2c"))
        official_rows = as_int(src.get("official_rows"))
        quality_blockers = clean_text(src.get("row_quality_blockers"))
        if quality_blockers:
            status = "reject_row_quality"
            action = "do_not_start"
            blockers = quality_blockers
        elif proxy_pnl <= 0:
            status = "reject_proxy_negative"
            action = "do_not_start"
            blockers = "proxy_pnl_not_positive"
        elif official_pnl <= 0:
            status = "reject_official_subset_negative"
            action = "do_not_start"
            blockers = "official_subset_pnl_not_positive"
        elif official_rows < min_official_rows:
            status = "diagnostic_sidecar_candidate_under_sampled"
            action = "freeze_non_trading_sidecar_metric_only"
            blockers = "official_rows_below_min"
        else:
            status = "research_promising_needs_forward_parity"
            action = "freeze_sidecar_metric_before_any_forward_use"
            blockers = ""
        rows.append(
            row(
                f"ml_{model}",
                "ml_model",
                "full_raw_ml_window_grid",
                out_dir,
                status,
                action,
                trades=as_int(src.get("rows")),
                official_rows=official_rows,
                pnl=proxy_pnl,
                premium=as_float(src.get("premium")),
                max_dd=as_float(src.get("max_dd_proxy_2c")),
                win_rate=as_float(src.get("proxy_win_rate")),
                blockers=blockers,
                notes=f"official_pnl_2c={official_pnl};official_win_rate={src.get('official_win_rate')}",
            )
        )
    return rows


def screen_ml_remote_sidecar(root: Path) -> list[dict[str, Any]]:
    out_dir = latest_dir(root, "btc15m_ml_sidecar_remote_raw_score_*")
    summary = read_json(out_dir / "ml_sidecar_remote_score_summary.json" if out_dir else None)
    if not summary:
        return []

    blockers = clean_text(summary.get("blockers"))
    proxy_rows = as_int(summary.get("proxy_rows"))
    official_rows = as_int(summary.get("official_rows"))
    proxy_pnl = as_float(summary.get("proxy_pnl_2c"))
    official_pnl = as_float(summary.get("official_pnl_2c"))
    if proxy_rows == 0:
        status = "forward_raw_metric_no_selection"
        action = "score_later_raw_capture_snapshot;do_not_start_ordering_shadow"
    elif blockers:
        status = "forward_raw_metric_collect_more"
        action = "score_later_raw_capture_snapshot;do_not_start_ordering_shadow"
    else:
        status = "forward_raw_metric_ready_for_review_not_deployment"
        action = "run_manual_review_before_any_paper_ordering"

    return [
        row(
            "btc15m_lightgbm_tabular_nontrading_sidecar",
            "ml_sidecar_forward",
            "remote_raw_ml_sidecar_score",
            out_dir,
            status,
            action,
            deployable_now=False,
            can_start_new_forward=False,
            trades=proxy_rows,
            official_rows=official_rows,
            pnl=proxy_pnl,
            premium=as_float(summary.get("proxy_premium")),
            max_dd=as_float(summary.get("proxy_max_drawdown")),
            win_rate=as_float(summary.get("proxy_win_rate_pct")) / 100.0,
            blockers=blockers,
            notes=(
                f"candidate_rows={summary.get('candidate_rows')};"
                f"candidate_events={summary.get('candidate_events')};"
                f"official_pnl_2c={official_pnl};"
                f"window={summary.get('start_utc')}..{summary.get('end_utc')};"
                f"advisories={summary.get('advisories')}"
            ),
        )
    ]


def screen_v2(root: Path, min_official_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    robustness_dirs = all_dirs(root, "btc15m_v2_binary_adapter_robustness_*")
    for out_dir in robustness_dirs[:3]:
        table = read_csv(out_dir / "candidate_robustness_summary.csv")
        if table.empty:
            continue
        for _, src in table.iterrows():
            strategy = clean_text(src.get("strategy"))
            p05 = as_float(src.get("bootstrap_pnl_p05"))
            pnl = as_float(src.get("pnl"))
            official_rows = as_int(src.get("official_settled_rows"))
            verdict = clean_text(src.get("verdict"))
            if pnl <= 0:
                status = "reject_nonpositive_pnl"
                action = "do_not_start"
                blockers = "official_pnl_not_positive"
            elif official_rows < min_official_rows:
                status = "diagnostic_only_branch_adapter_sample_small"
                action = "do_not_start_trading;needs_full_raw_adapter_or_frozen_metric"
                blockers = "official_rows_below_min"
                if p05 <= 0:
                    blockers += ";bootstrap_p05_not_positive"
            else:
                status = "research_promising_needs_forward_parity"
                action = "freeze_sidecar_metric_before_any_forward_use"
                blockers = ""
            rows.append(
                row(
                    f"{out_dir.name}_{strategy}",
                    "kalshi_v2_adapter",
                    "v2_binary_adapter_robustness",
                    out_dir,
                    status,
                    action,
                    trades=as_int(src.get("trades")),
                    official_rows=official_rows,
                    pnl=pnl,
                    premium=as_float(src.get("premium")),
                    max_dd=as_float(src.get("max_dd")),
                    bootstrap_p05=p05,
                    win_rate=as_float(src.get("win_rate")),
                    blockers=blockers,
                    notes=verdict,
                )
            )
    latest_adapter = latest_dir(root, "btc15m_v2_binary_adapter_*")
    if latest_adapter and not (latest_adapter / "candidate_robustness_summary.csv").exists():
        table = read_csv(latest_adapter / "summary.csv")
        for _, src in table.iterrows():
            strategy = clean_text(src.get("strategy"))
            rows.append(
                row(
                    f"{latest_adapter.name}_{strategy}",
                    "kalshi_v2_adapter",
                    "latest_v2_binary_adapter_slice",
                    latest_adapter,
                    "diagnostic_only_latest_slice_sample_small",
                    "do_not_start_trading;needs_larger_faithful_replay",
                    trades=as_int(src.get("trades")),
                    official_rows=as_int(src.get("events")),
                    pnl=as_float(src.get("pnl")),
                    premium=as_float(src.get("premium")),
                    max_dd=as_float(src.get("max_dd")),
                    win_rate=as_float(src.get("win_rate")),
                    blockers="official_rows_below_min",
                    notes=clean_text(src.get("research_status")),
                )
            )
    return rows


def markdown_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    work = df[cols].copy()
    if limit is not None:
        work = work.head(limit)
    for col in work.columns:
        work[col] = work[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), int(work[col].map(len).max())) for col in work.columns}
    lines = ["| " + " | ".join(col.ljust(widths[col]) for col in work.columns) + " |"]
    lines.append("| " + " | ".join("-" * widths[col] for col in work.columns) + " |")
    for _, src in work.iterrows():
        lines.append("| " + " | ".join(str(src[col]).ljust(widths[col]) for col in work.columns) + " |")
    return "\n".join(lines)


def ml_sidecar_report_line(screen: pd.DataFrame) -> str:
    sidecar = screen[screen["candidate_id"].astype(str) == "btc15m_lightgbm_tabular_nontrading_sidecar"]
    if sidecar.empty:
        return "- `lightgbm_tabular` is frozen only as a non-trading diagnostic metric; no remote raw score is available in this screen."
    src = sidecar.iloc[0]
    proxy_rows = as_int(src.get("trades"))
    official_rows = as_int(src.get("official_rows"))
    status = clean_text(src.get("status"))
    if proxy_rows == 0:
        selection = "selected zero proxy rows"
    else:
        selection = f"selected {proxy_rows} proxy rows with {official_rows} official rows"
    return (
        "- `lightgbm_tabular` is frozen only as a non-trading diagnostic metric; "
        f"the latest remote raw score {selection} and is `{status}`, so it is not a paper-ordering candidate."
    )


def build_report(screen: pd.DataFrame, info: dict[str, Any]) -> str:
    deployable = int(screen["deployable_now"].astype(bool).sum()) if "deployable_now" in screen else 0
    keep_collecting = screen[screen["forward_action"].astype(str).str.contains("keep_", na=False)]
    metric_only = screen[screen["forward_action"].astype(str).str.contains("sidecar", na=False)]
    rejects = screen[screen["forward_action"].astype(str).str.contains("do_not_start", na=False)]
    priority = screen[
        screen["candidate_id"].astype(str).isin(
            [
                "btc15m_lowdd_current_wrapper",
                "ml_lightgbm_tabular",
                "btc15m_lightgbm_tabular_nontrading_sidecar",
                "ml_xgboost_tabular",
                "branch_inventory",
            ]
        )
    ]
    lines = [
        "# BTC15M Research Candidate Screen",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Deployable candidates now: `{deployable}`.",
        "- Keep the raw collector and lowdd forward shadow running; lowdd is positive but still below the official/parity sample gate.",
        "- Do not start a new trading or paper-ordering shadow from the current branch/regime/ML/v2 evidence.",
        ml_sidecar_report_line(screen),
        "",
        "## Priority Rows",
        "",
        markdown_table(
            priority,
            ["candidate_id", "status", "forward_action", "trades", "official_rows", "pnl", "bootstrap_p05", "blockers"],
        ),
        "",
        "## Keep Collecting",
        "",
        markdown_table(
            keep_collecting,
            ["candidate_id", "status", "trades", "official_rows", "pnl", "premium", "blockers"],
        ),
        "",
        "## Diagnostic Sidecar Only",
        "",
        markdown_table(
            metric_only,
            ["candidate_id", "family", "status", "trades", "official_rows", "pnl", "blockers"],
            limit=20,
        ),
        "",
        "## Rejections / Do Not Start",
        "",
        markdown_table(
            rejects,
            ["candidate_id", "family", "status", "trades", "official_rows", "pnl", "blockers"],
            limit=30,
        ),
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.backtest_root
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    rows.extend(screen_branch_inventory(root))
    rows.extend(screen_lowdd(root, args.min_official_rows))
    rows.extend(screen_full_raw_robustness(root))
    rows.extend(screen_walkforward(root))
    rows.extend(screen_regime(root, args.min_official_rows))
    rows.extend(screen_ml(root, args.min_official_rows))
    rows.extend(screen_ml_remote_sidecar(root))
    rows.extend(screen_v2(root, args.min_official_rows))
    screen = pd.DataFrame(rows)
    if not screen.empty:
        screen = screen.sort_values(
            by=["deployable_now", "can_start_new_forward", "family", "candidate_id"],
            ascending=[False, False, True, True],
        ).reset_index(drop=True)

    out_csv = args.out_dir / "candidate_screen.csv"
    screen.to_csv(out_csv, index=False)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "backtest_root": str(root),
        "min_official_rows": args.min_official_rows,
        "row_count": int(len(screen)),
        "deployable_now_count": int(screen["deployable_now"].astype(bool).sum()) if not screen.empty else 0,
        "started_or_restarted_processes": False,
        "deployed_live": False,
        "out_csv": str(out_csv),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(screen, info), encoding="utf-8")

    compact_cols = ["candidate_id", "family", "status", "forward_action", "trades", "official_rows", "pnl", "blockers"]
    print(screen[compact_cols].to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
