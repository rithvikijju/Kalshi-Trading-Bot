#!/usr/bin/env python3
"""Rank BTC1H strategy research priorities without changing live policy.

This diagnostic consumes fixed BTC1H evidence artifacts that were already
generated elsewhere.  It does not search thresholds, start/restart processes, or
promote a policy.  Its purpose is to keep three questions separate:

- Which policy is the current forward-evidence control?
- Which historical runner-up deserves causal replay next?
- Which apparent historical wins are still blocked from deployment?
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_research_priority_matrix_latest_codex"
ACTIVE_VARIANT = "high_conf_80_entry70_no_chase"
PRIMARY_VARIANTS = [
    ACTIVE_VARIANT,
    "high_conf_80_entry59_70_no_chase",
    "high_conf_80_no_chase",
    "high_conf_80",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--multi-holdout-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_multi_holdout_research_latest_codex" / "btc1h_candidate_gate_summary.csv",
    )
    parser.add_argument(
        "--variant-basis-stress-ranking",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_variant_basis_stress_ranking_latest_codex"
        / "btc1h_variant_basis_stress_ranking.csv",
    )
    parser.add_argument(
        "--promotion-gap-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_promotion_gap_matrix_latest_codex" / "btc1h_promotion_gap_summary.csv",
    )
    parser.add_argument(
        "--promotion-gap-matrix",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_promotion_gap_matrix_latest_codex" / "btc1h_promotion_gap_matrix.csv",
    )
    parser.add_argument(
        "--replay-repair-attempt-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempt_summary.csv",
    )
    parser.add_argument(
        "--replay-overlap-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_variant_overlap_latest_codex"
        / "btc1h_replay_variant_overlap_summary.csv",
    )
    parser.add_argument(
        "--entry59-floor-filter-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_entry59_floor_filter_audit_latest_codex"
        / "btc1h_entry59_floor_filter_summary.csv",
    )
    parser.add_argument(
        "--no-chase-extra-row-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_no_chase_extra_row_audit_latest_codex"
        / "btc1h_no_chase_extra_row_summary.csv",
    )
    parser.add_argument(
        "--holdout-independence-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_holdout_independence_audit_latest_codex"
        / "btc1h_holdout_independence_summary.csv",
    )
    parser.add_argument("--min-forward-official-rows", type=int, default=50)
    parser.add_argument("--max-proxy-official-mismatch-rate", type=float, default=0.02)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def by_key(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        value = str(row.get(key, "")).strip()
        if value:
            out[value] = row
    return out


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def semicolon_join(parts: list[str]) -> str:
    clean = [part for part in parts if part]
    return ";".join(dict.fromkeys(clean))


def historical_gate_pass(row: dict[str, str]) -> bool:
    holdouts = to_float(row.get("all_holdouts", ""))
    positive = to_float(row.get("all_positive_holdouts", ""))
    ws = to_float(row.get("ws_cadences", ""))
    ws_positive = to_float(row.get("ws_positive_cadences", ""))
    return holdouts > 0 and positive == holdouts and ws > 0 and ws_positive == ws


def basis_research_score(row: dict[str, str]) -> float:
    return (
        to_float(row.get("max_basis_shock_with_positive_unique_pnl", ""))
        + 5.0 * to_float(row.get("unique_pnl_at_p95_or_max", ""))
        + 2.0
        * ratio(
            to_float(row.get("positive_holdouts_at_p95_or_max", "")),
            to_float(row.get("holdouts_at_p95_or_max", "")),
        )
    )


def research_replay_score(candidate: dict[str, Any]) -> float:
    if candidate["active_forward_control"]:
        return -1000.0
    score = 0.0
    if candidate["historical_all_holdouts_pass"]:
        score += 35.0
    else:
        score += 20.0 * candidate["historical_positive_holdout_rate"]
    if candidate["ws_all_cadences_pass"]:
        score += 25.0
    else:
        score += 12.0 * candidate["ws_positive_cadence_rate"]
    score += min(25.0, max(0.0, candidate["historical_pnl"]) / 25.0 * 25.0)
    score += min(20.0, max(0.0, candidate["unique_pnl_at_p95_or_max"]) * 4.0)
    score += min(10.0, max(0.0, candidate["max_basis_shock_with_positive_unique_pnl"]) / 10.0)
    if candidate["historical_only"]:
        score -= 5.0
    if candidate["basis_p95_unique_pnl_positive"]:
        score += 8.0
    else:
        score -= 12.0
    return round(score, 6)


def forward_control_score(candidate: dict[str, Any]) -> float:
    score = 0.0
    if candidate["active_forward_control"]:
        score += 100.0
    score += min(25.0, candidate["forward_official_rows"])
    if candidate["forward_official_pnl"] > 0:
        score += min(15.0, candidate["forward_official_pnl"] * 10.0)
    if candidate["forward_proxy_mismatch_rate"] <= 0.02 and candidate["forward_official_rows"] > 0:
        score += 10.0
    if candidate["replay_ledger_promotion_usable"]:
        score += 20.0
    return round(score, 6)


def deployment_blockers(
    candidate: dict[str, Any],
    args: argparse.Namespace,
    active_gap_blockers: str,
    active_replay_repair_blockers: str,
) -> str:
    blockers: list[str] = []
    existing = str(candidate.get("deploy_blockers", "")).strip()
    if existing:
        blockers.extend(part.strip() for part in existing.split(";") if part.strip())
    if candidate["forward_official_rows"] < args.min_forward_official_rows:
        blockers.append("too_few_clean_forward_official_rows")
    if candidate["forward_official_rows"] == 0:
        blockers.append("no_forward_official_rows")
    if candidate["forward_proxy_mismatch_rate"] > args.max_proxy_official_mismatch_rate:
        blockers.append("official_proxy_mismatch_rate_too_high")
    if not candidate["historical_all_holdouts_pass"]:
        blockers.append("not_all_fixed_holdouts_positive")
    if not candidate["ws_all_cadences_pass"]:
        blockers.append("not_all_live_ws_cadences_positive")
    if candidate["unique_pnl_at_p95_or_max"] <= 0:
        blockers.append("basis_stress_unique_pnl_nonpositive_at_p95_or_max")
    if not candidate["replay_ledger_promotion_usable"]:
        blockers.append("row_for_row_replay_not_promotion_usable")
    if candidate["historical_only"]:
        blockers.append("historical_only_no_clean_forward_shadow")
    if candidate.get("replay_exact_row_set_match_vs_active"):
        blockers.append("no_independent_live_ws_replay_rows_vs_active_on_snapshot")
    if (
        candidate["variant"] == "high_conf_80_entry59_70_no_chase"
        and candidate.get("entry59_floor_filter_status")
        and candidate.get("entry59_total_challenger_market_side_only_rows") == 0
    ):
        blockers.append("entry_floor_filter_no_independent_market_side_rows_in_compared_artifacts")
    if (
        candidate["variant"] == "high_conf_80_no_chase"
        and str(candidate.get("no_chase_extra_row_status", "")).startswith("NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING")
    ):
        blockers.append("broad_no_chase_extra_rows_damage_live_ws_cadence")
    if candidate["active_forward_control"] and active_gap_blockers:
        blockers.extend(part.strip() for part in active_gap_blockers.split(";") if part.strip())
    if candidate["active_forward_control"] and active_replay_repair_blockers:
        blockers.extend(part.strip() for part in active_replay_repair_blockers.split(";") if part.strip())
    if candidate["active_forward_control"] and candidate.get("historical_duplicate_market_side_rows", 0):
        blockers.append("active_historical_holdouts_have_duplicate_market_side_rows")
    if (
        candidate["active_forward_control"]
        and str(candidate.get("holdout_independence_status", "")).strip() == "WEAK_OR_CONCENTRATED_RESEARCH"
    ):
        blockers.append("active_historical_independence_weak_or_concentrated")
    return semicolon_join(blockers)


def next_action(candidate: dict[str, Any]) -> str:
    variant = candidate["variant"]
    if variant == ACTIVE_VARIANT:
        return (
            "Keep as the frozen forward control; only after explicit authorization, restart cleanly and collect "
            "at least 50 official-settled rows with full execution/replay fields."
        )
    if variant == "high_conf_80_entry59_70_no_chase":
        if candidate.get("entry59_total_challenger_market_side_only_rows") == 0:
            return (
                "Do not start a separate shadow yet: across compared direct/robustness/live-replay artifacts the "
                "entry59 floor adds no independent market-side events versus entry70. Treat it as a diagnostic "
                "filter unless future clean rows diverge."
            )
        if candidate.get("replay_overlap_status") == "EXACT_ROW_SET_MATCH":
            independent = candidate.get("entry59_total_challenger_market_side_only_rows", "")
            return (
                "Do not start a separate shadow yet: the paused live-WS snapshot is an exact row-set match with "
                f"entry70, and the broader floor audit has only {independent} diagnostic historical independent "
                "market-side row(s). Broaden causal replay or wait for future clean rows that actually differ."
            )
        return (
            "Run causal live-WS replay/model-input parity for this derived entry band; consider a paper shadow only "
            "if row-for-row replay and official-basis diagnostics survive."
        )
    if variant == "high_conf_80_no_chase":
        if candidate.get("no_chase_extra_row_status"):
            return (
                "Keep as a basis-robust watchlist policy only. The extra-row audit shows "
                f"{candidate.get('no_chase_total_extra_rows')} broad no-chase exact extra rows with total "
                f"PnL {candidate.get('no_chase_total_extra_pnl')}, and the 1s live-WS extra >70c rows "
                f"were {candidate.get('no_chase_stride1_extra_gt70_pnl')}; do not restart or promote "
                "without fresh causal replay and official forward rows."
            )
        return (
            "Treat as a basis-robust watchlist policy, but explain the failed 1s live-WS cadence and extra-row damage "
            "before any forward shadow."
        )
    return "Deprioritize unless new pre-registered evidence changes the fixed holdout, WS cadence, and basis-stress picture."


def classification(candidate: dict[str, Any]) -> str:
    variant = candidate["variant"]
    if candidate["active_forward_control"]:
        return "active_clean_forward_control"
    if variant == "high_conf_80_entry59_70_no_chase":
        if candidate.get("replay_overlap_status") == "EXACT_ROW_SET_MATCH":
            return "top_replay_runner_up_but_not_independent_on_snapshot"
        return "top_causal_replay_runner_up"
    if variant == "high_conf_80_no_chase":
        return "basis_robust_watchlist"
    return "low_priority_or_reject"


def first_blocker_from_gap_matrix(rows: list[dict[str, str]]) -> str:
    blockers: list[str] = []
    for row in rows:
        if str(row.get("status", "")).strip() == "BLOCKED":
            blocker = str(row.get("blocker", "")).strip()
            if blocker:
                blockers.append(blocker)
    return semicolon_join(blockers)


def by_panel(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return by_key(rows, "panel")


def replay_repair_blockers(summary: dict[str, str]) -> str:
    blockers: list[str] = []
    if summary and not to_bool(summary.get("current_snapshot_repairs_make_replay_promotion_usable", "")):
        blockers.append("current_snapshot_repairs_not_promotion_usable")
    if str(summary.get("strict_selected_scan_attempt_verdict", "")).strip() == "REGRESSES_ROW_FIDELITY":
        blockers.append("selected_scan_attempt_regresses_row_fidelity")
    if to_int(summary.get("unresolved_future_exact_input_target_count", "")) > 0:
        blockers.append("unresolved_future_exact_input_replay_targets")
    if to_int(summary.get("diagnostic_patch_applied_target_count", "")) > 0 and to_bool(
        summary.get("all_field_ready_repairs_remain_diagnostic_only", "")
    ):
        blockers.append("field_ready_replay_repairs_diagnostic_only")
    if to_int(summary.get("targets_repaired_to_promotion_usable_count", "")) == 0:
        blockers.append("zero_replay_targets_repaired_to_promotion_usable")
    return semicolon_join(blockers)


def build_matrix(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    multi_by_variant = by_key(read_csv(args.multi_holdout_summary), "variant")
    basis_by_variant = by_key(read_csv(args.variant_basis_stress_ranking), "variant")
    overlap_rows = read_csv(args.replay_overlap_summary)
    overlap = overlap_rows[0] if overlap_rows else {}
    floor_filter_rows = read_csv(args.entry59_floor_filter_summary)
    floor_filter = floor_filter_rows[0] if floor_filter_rows else {}
    no_chase_rows = read_csv(args.no_chase_extra_row_summary)
    no_chase_extra = no_chase_rows[0] if no_chase_rows else {}
    independence_by_panel = by_panel(read_csv(args.holdout_independence_summary))
    active_historical_independence = independence_by_panel.get("historical_all_rows", {})
    active_unique_independence = independence_by_panel.get("historical_unique_market_side_rows", {})
    active_forward_independence = independence_by_panel.get("forward_official_stale_rows", {})
    gap_summary_rows = read_csv(args.promotion_gap_summary)
    gap_matrix_rows = read_csv(args.promotion_gap_matrix)
    active_gap_blockers = first_blocker_from_gap_matrix(gap_matrix_rows)
    repair_summary_rows = read_csv(args.replay_repair_attempt_summary)
    repair_summary = repair_summary_rows[0] if repair_summary_rows else {}
    active_repair_blockers = replay_repair_blockers(repair_summary)
    replay_repair_promotion_usable = to_bool(
        repair_summary.get("current_snapshot_repairs_make_replay_promotion_usable", "")
    )

    rows: list[dict[str, Any]] = []
    for variant in PRIMARY_VARIANTS:
        multi = multi_by_variant.get(variant, {})
        basis = basis_by_variant.get(variant, {})
        holdouts = to_float(multi.get("all_holdouts", ""))
        positive_holdouts = to_float(multi.get("all_positive_holdouts", ""))
        ws = to_float(multi.get("ws_cadences", ""))
        ws_positive = to_float(multi.get("ws_positive_cadences", ""))
        row: dict[str, Any] = {
            "variant": variant,
            "classification": "",
            "deployable_now": False,
            "active_forward_control": variant == ACTIVE_VARIANT,
            "historical_only": variant != ACTIVE_VARIANT,
            "research_status": multi.get("research_status", ""),
            "historical_all_holdouts_pass": historical_gate_pass(multi),
            "historical_positive_holdouts": to_int(positive_holdouts),
            "historical_holdouts": to_int(holdouts),
            "historical_positive_holdout_rate": round(ratio(positive_holdouts, holdouts), 6),
            "negative_holdouts": multi.get("negative_holdouts", ""),
            "ws_all_cadences_pass": ws > 0 and ws_positive == ws,
            "ws_positive_cadences": to_int(ws_positive),
            "ws_cadences": to_int(ws),
            "ws_positive_cadence_rate": round(ratio(ws_positive, ws), 6),
            "historical_trades": to_int(multi.get("historical_trades", "")),
            "historical_pnl": round(to_float(multi.get("historical_pnl_sum", "")), 6),
            "forward_official_rows": to_int(multi.get("forward_official_rows", "")),
            "forward_official_pnl": round(to_float(multi.get("forward_official_pnl", "")), 6),
            "forward_proxy_mismatch_rate": round(to_float(multi.get("forward_official_mismatch_rate", "")), 6),
            "replay_ledger_promotion_usable": to_bool(multi.get("replay_ledger_promotion_usable", "")),
            "replay_ledger_exact_match_rate": round(to_float(multi.get("replay_ledger_exact_match_rate", "")), 6),
            "max_basis_shock_with_positive_unique_pnl": round(
                to_float(basis.get("max_basis_shock_with_positive_unique_pnl", "")), 6
            ),
            "pnl_at_50": round(to_float(basis.get("pnl_at_50", "")), 6),
            "unique_pnl_at_50": round(to_float(basis.get("unique_pnl_at_50", "")), 6),
            "p95_or_max_basis_shock": round(to_float(basis.get("p95_or_max_basis_shock", "")), 6),
            "pnl_at_p95_or_max": round(to_float(basis.get("pnl_at_p95_or_max", "")), 6),
            "unique_pnl_at_p95_or_max": round(to_float(basis.get("unique_pnl_at_p95_or_max", "")), 6),
            "basis_p95_unique_pnl_positive": to_float(basis.get("unique_pnl_at_p95_or_max", "")) > 0,
            "basis_positive_holdouts_at_p95_or_max": to_int(basis.get("positive_holdouts_at_p95_or_max", "")),
            "basis_holdouts_at_p95_or_max": to_int(basis.get("holdouts_at_p95_or_max", "")),
            "basis_research_score": round(basis_research_score(basis), 6),
            "deploy_blockers": multi.get("deploy_blockers", ""),
            "replay_repair_best_current_snapshot_attempt": repair_summary.get("best_current_snapshot_attempt", "")
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_strict_selected_scan_attempt_verdict": repair_summary.get(
                "strict_selected_scan_attempt_verdict",
                "",
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_current_snapshot_repairs_promotion_usable": replay_repair_promotion_usable
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_residual_target_count": to_int(repair_summary.get("residual_target_count", ""))
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_diagnostic_patch_applied_target_count": to_int(
                repair_summary.get("diagnostic_patch_applied_target_count", "")
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_unresolved_future_exact_input_target_count": to_int(
                repair_summary.get("unresolved_future_exact_input_target_count", "")
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_diagnostic_patch_applied_targets": repair_summary.get(
                "diagnostic_patch_applied_targets",
                "",
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_unresolved_future_exact_input_targets": repair_summary.get(
                "unresolved_future_exact_input_targets",
                "",
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_targets_repaired_to_promotion_usable_count": to_int(
                repair_summary.get("targets_repaired_to_promotion_usable_count", "")
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_all_field_ready_repairs_remain_diagnostic_only": to_bool(
                repair_summary.get("all_field_ready_repairs_remain_diagnostic_only", "")
            )
            if variant == ACTIVE_VARIANT
            else "",
            "replay_repair_remaining_blockers_after_best_attempt": repair_summary.get(
                "remaining_blockers_after_best_attempt",
                "",
            )
            if variant == ACTIVE_VARIANT
            else "",
            "holdout_independence_status": active_historical_independence.get("status", "")
            if variant == ACTIVE_VARIANT
            else "",
            "historical_unique_market_side_rows": to_int(
                active_historical_independence.get("unique_market_side_rows", "")
            )
            if variant == ACTIVE_VARIANT
            else "",
            "historical_duplicate_market_side_rows": to_int(
                active_historical_independence.get("duplicate_market_side_rows", "")
            )
            if variant == ACTIVE_VARIANT
            else "",
            "historical_unique_market_side_pnl": round(
                to_float(active_historical_independence.get("unique_market_side_pnl", "")),
                6,
            )
            if variant == ACTIVE_VARIANT
            else "",
            "historical_leave_one_event_min_pnl": round(
                to_float(active_historical_independence.get("leave_one_event_min_pnl", "")),
                6,
            )
            if variant == ACTIVE_VARIANT
            else "",
            "historical_independence_blockers": active_historical_independence.get("blockers", "")
            if variant == ACTIVE_VARIANT
            else "",
            "forward_official_leave_one_event_min_pnl": round(
                to_float(active_forward_independence.get("leave_one_event_min_pnl", "")),
                6,
            )
            if variant == ACTIVE_VARIANT
            else "",
            "unique_market_side_panel_status": active_unique_independence.get("status", "")
            if variant == ACTIVE_VARIANT
            else "",
        }
        if variant == str(overlap.get("challenger_variant", "")):
            row["replay_overlap_status"] = overlap.get("status", "")
            row["replay_exact_row_set_match_vs_active"] = to_bool(overlap.get("exact_row_set_match", ""))
            row["independent_replay_rows_vs_active"] = to_int(overlap.get("independent_challenger_rows", ""))
        else:
            row["replay_overlap_status"] = ""
            row["replay_exact_row_set_match_vs_active"] = False
            row["independent_replay_rows_vs_active"] = ""
        if variant == str(floor_filter.get("challenger_variant", "")):
            row["entry59_floor_filter_status"] = floor_filter.get("status", "")
            row["entry59_total_challenger_market_side_only_rows"] = to_int(
                floor_filter.get("total_challenger_market_side_only_rows", "")
            )
            row["entry59_total_challenger_exact_only_rows"] = to_int(
                floor_filter.get("total_challenger_exact_only_rows", "")
            )
            row["entry59_total_base_exact_only_rows"] = to_int(floor_filter.get("total_base_exact_only_rows", ""))
            row["entry59_total_deleted_low_entry_base_rows"] = to_int(
                floor_filter.get("total_deleted_low_entry_base_rows", "")
            )
            row["entry59_total_deleted_low_entry_base_pnl"] = round(
                to_float(floor_filter.get("total_deleted_low_entry_base_pnl", "")),
                6,
            )
        else:
            row["entry59_floor_filter_status"] = ""
            row["entry59_total_challenger_market_side_only_rows"] = ""
            row["entry59_total_challenger_exact_only_rows"] = ""
            row["entry59_total_base_exact_only_rows"] = ""
            row["entry59_total_deleted_low_entry_base_rows"] = ""
            row["entry59_total_deleted_low_entry_base_pnl"] = ""
        if variant == str(no_chase_extra.get("no_chase_variant", "")):
            row["no_chase_extra_row_status"] = no_chase_extra.get("status", "")
            row["no_chase_total_extra_rows"] = to_int(no_chase_extra.get("total_extra_no_chase_exact_rows", ""))
            row["no_chase_total_extra_market_side_rows"] = to_int(
                no_chase_extra.get("total_extra_no_chase_market_side_rows", "")
            )
            row["no_chase_total_extra_pnl"] = round(to_float(no_chase_extra.get("total_extra_no_chase_pnl", "")), 6)
            row["no_chase_total_extra_gt70_rows"] = to_int(no_chase_extra.get("total_extra_gt70_rows", ""))
            row["no_chase_total_extra_gt70_pnl"] = round(to_float(no_chase_extra.get("total_extra_gt70_pnl", "")), 6)
            row["no_chase_live_ws_negative_extra_label_count"] = to_int(
                no_chase_extra.get("live_ws_negative_extra_label_count", "")
            )
            row["no_chase_stride1_extra_gt70_rows"] = to_int(no_chase_extra.get("live_ws_stride1_extra_gt70_rows", ""))
            row["no_chase_stride1_extra_gt70_pnl"] = round(
                to_float(no_chase_extra.get("live_ws_stride1_extra_gt70_pnl", "")),
                6,
            )
            row["no_chase_fullscan_status"] = no_chase_extra.get("fullscan_status", "")
            row["no_chase_fullscan_extra_rows"] = to_int(no_chase_extra.get("fullscan_extra_no_chase_rows", ""))
            row["no_chase_fullscan_extra_market_side_rows"] = to_int(
                no_chase_extra.get("fullscan_extra_no_chase_market_side_rows", "")
            )
            row["no_chase_fullscan_extra_pnl"] = round(
                to_float(no_chase_extra.get("fullscan_extra_no_chase_pnl", "")),
                6,
            )
            row["no_chase_fullscan_pnl_diff"] = round(
                to_float(no_chase_extra.get("fullscan_pnl_diff_no_chase_minus_entry70", "")),
                6,
            )
        else:
            row["no_chase_extra_row_status"] = ""
            row["no_chase_total_extra_rows"] = ""
            row["no_chase_total_extra_market_side_rows"] = ""
            row["no_chase_total_extra_pnl"] = ""
            row["no_chase_total_extra_gt70_rows"] = ""
            row["no_chase_total_extra_gt70_pnl"] = ""
            row["no_chase_live_ws_negative_extra_label_count"] = ""
            row["no_chase_stride1_extra_gt70_rows"] = ""
            row["no_chase_stride1_extra_gt70_pnl"] = ""
            row["no_chase_fullscan_status"] = ""
            row["no_chase_fullscan_extra_rows"] = ""
            row["no_chase_fullscan_extra_market_side_rows"] = ""
            row["no_chase_fullscan_extra_pnl"] = ""
            row["no_chase_fullscan_pnl_diff"] = ""
        row["classification"] = classification(row)
        row["research_replay_score"] = research_replay_score(row)
        row["forward_control_score"] = forward_control_score(row)
        row["deployment_blockers"] = deployment_blockers(row, args, active_gap_blockers, active_repair_blockers)
        row["recommended_next_action"] = next_action(row)
        rows.append(row)

    replay_candidates = sorted(
        [row for row in rows if not row["active_forward_control"]],
        key=lambda r: (-float(r["research_replay_score"]), str(r["variant"])),
    )
    for rank, row in enumerate(replay_candidates, start=1):
        row["research_replay_rank"] = rank
    active_rows = [row for row in rows if row["active_forward_control"]]
    for row in active_rows:
        row["research_replay_rank"] = ""

    forward_order = sorted(
        [row for row in rows if row["active_forward_control"] or row["forward_official_rows"] > 0],
        key=lambda r: (-float(r["forward_control_score"]), str(r["variant"])),
    )
    for rank, row in enumerate(forward_order, start=1):
        row["forward_control_rank"] = rank
    for row in rows:
        if "forward_control_rank" not in row:
            row["forward_control_rank"] = ""

    rows.sort(
        key=lambda r: (
            0 if r["active_forward_control"] else 1,
            r.get("research_replay_rank") or 99,
            str(r["variant"]),
        )
    )

    top_replay = replay_candidates[0] if replay_candidates else {}
    gap_summary = gap_summary_rows[0] if gap_summary_rows else {}
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "deployable_now": False,
        "production_ready_count": 0,
        "active_forward_control": ACTIVE_VARIANT,
        "active_forward_control_rank": next(
            (row["forward_control_rank"] for row in rows if row["variant"] == ACTIVE_VARIANT),
            "",
        ),
        "top_causal_replay_runner_up": top_replay.get("variant", ""),
        "top_causal_replay_runner_up_rank": top_replay.get("research_replay_rank", ""),
        "top_basis_stress_variant": max(rows, key=lambda r: float(r["basis_research_score"]))["variant"] if rows else "",
        "entry59_replay_overlap_status": overlap.get("status", ""),
        "entry59_independent_replay_rows_vs_active": overlap.get("independent_challenger_rows", ""),
        "entry59_floor_filter_status": floor_filter.get("status", ""),
        "entry59_independent_market_side_rows_vs_active": floor_filter.get(
            "total_challenger_market_side_only_rows",
            "",
        ),
        "entry59_deleted_low_entry_base_rows": floor_filter.get("total_deleted_low_entry_base_rows", ""),
        "entry59_deleted_low_entry_base_pnl": floor_filter.get("total_deleted_low_entry_base_pnl", ""),
        "no_chase_extra_row_status": no_chase_extra.get("status", ""),
        "no_chase_total_extra_rows": no_chase_extra.get("total_extra_no_chase_exact_rows", ""),
        "no_chase_total_extra_pnl": no_chase_extra.get("total_extra_no_chase_pnl", ""),
        "no_chase_live_ws_negative_extra_label_count": no_chase_extra.get("live_ws_negative_extra_label_count", ""),
        "no_chase_stride1_extra_gt70_pnl": no_chase_extra.get("live_ws_stride1_extra_gt70_pnl", ""),
        "no_chase_fullscan_status": no_chase_extra.get("fullscan_status", ""),
        "no_chase_fullscan_extra_rows": no_chase_extra.get("fullscan_extra_no_chase_rows", ""),
        "no_chase_fullscan_extra_pnl": no_chase_extra.get("fullscan_extra_no_chase_pnl", ""),
        "no_chase_fullscan_pnl_diff": no_chase_extra.get("fullscan_pnl_diff_no_chase_minus_entry70", ""),
        "active_holdout_independence_status": active_historical_independence.get("status", ""),
        "active_historical_rows": active_historical_independence.get("rows", ""),
        "active_historical_unique_market_side_rows": active_historical_independence.get(
            "unique_market_side_rows",
            "",
        ),
        "active_historical_duplicate_market_side_rows": active_historical_independence.get(
            "duplicate_market_side_rows",
            "",
        ),
        "active_historical_unique_market_side_pnl": active_historical_independence.get(
            "unique_market_side_pnl",
            "",
        ),
        "active_historical_leave_one_event_min_pnl": active_historical_independence.get(
            "leave_one_event_min_pnl",
            "",
        ),
        "active_forward_official_leave_one_event_min_pnl": active_forward_independence.get(
            "leave_one_event_min_pnl",
            "",
        ),
        "active_holdout_independence_blockers": active_historical_independence.get("blockers", ""),
        "active_unique_market_side_panel_status": active_unique_independence.get("status", ""),
        "recommended_forward_policy_change": "none",
        "recommended_next_forward_action": "keep_active_policy_for_clean_forward_control_if_user_authorizes_restart",
        "recommended_next_research_action": top_replay.get("recommended_next_action", ""),
        "promotion_gap_deployable_now": gap_summary.get("deployable_now", ""),
        "promotion_gap_blocked_gate_count": gap_summary.get("blocked_gate_count", ""),
        "replay_repair_best_current_snapshot_attempt": repair_summary.get("best_current_snapshot_attempt", ""),
        "replay_repair_strict_selected_scan_attempt_verdict": repair_summary.get(
            "strict_selected_scan_attempt_verdict",
            "",
        ),
        "replay_repair_current_snapshot_repairs_promotion_usable": replay_repair_promotion_usable,
        "replay_repair_residual_target_count": repair_summary.get("residual_target_count", ""),
        "replay_repair_diagnostic_patch_applied_target_count": repair_summary.get(
            "diagnostic_patch_applied_target_count",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_target_count": repair_summary.get(
            "unresolved_future_exact_input_target_count",
            "",
        ),
        "replay_repair_diagnostic_patch_applied_targets": repair_summary.get(
            "diagnostic_patch_applied_targets",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_targets": repair_summary.get(
            "unresolved_future_exact_input_targets",
            "",
        ),
        "replay_repair_targets_repaired_to_promotion_usable_count": repair_summary.get(
            "targets_repaired_to_promotion_usable_count",
            "",
        ),
        "replay_repair_all_field_ready_repairs_remain_diagnostic_only": to_bool(
            repair_summary.get("all_field_ready_repairs_remain_diagnostic_only", "")
        ),
        "replay_repair_remaining_blockers_after_best_attempt": repair_summary.get(
            "remaining_blockers_after_best_attempt",
            "",
        ),
        "replay_repair_active_blockers": active_repair_blockers,
        "note": (
            "All rows are diagnostic. Historical/proxy and basis-stress wins are not deployment evidence without "
            "causal replay, official settlement, execution realism, and clean forward rows."
        ),
    }
    return rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    compact_cols = [
        "variant",
        "classification",
        "forward_control_rank",
        "research_replay_rank",
        "historical_positive_holdouts",
        "historical_holdouts",
        "ws_positive_cadences",
        "ws_cadences",
        "forward_official_rows",
        "forward_official_pnl",
        "historical_unique_market_side_rows",
        "historical_duplicate_market_side_rows",
        "historical_unique_market_side_pnl",
        "max_basis_shock_with_positive_unique_pnl",
        "unique_pnl_at_p95_or_max",
    ]
    lines = [
        "# BTC1H Research Priority Matrix",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Deployable now: `{summary['deployable_now']}`",
        f"Active forward control: `{summary['active_forward_control']}`",
        f"Top causal replay runner-up: `{summary['top_causal_replay_runner_up']}`",
        f"Top basis-stress variant: `{summary['top_basis_stress_variant']}`",
        "",
        "## Ranking",
        "",
        markdown_table(rows, compact_cols),
        "",
        "## Recommendations",
        "",
    ]
    for row in rows:
        lines.append(f"- `{row['variant']}`: {row['recommended_next_action']}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(summary, indent=2, sort_keys=True),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = build_matrix(args)
    write_csv(args.out_dir / "btc1h_research_priority_matrix.csv", rows)
    write_csv(args.out_dir / "btc1h_research_priority_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, summary), encoding="utf-8")
    print(f"Wrote BTC1H research priority matrix to {args.out_dir}")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
