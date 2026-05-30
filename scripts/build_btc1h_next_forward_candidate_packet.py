#!/usr/bin/env python3
"""Freeze the next BTC1H forward-evidence candidate packet.

This packet is not a deployment approval. It records the active BTC1H research
candidate, exact policy identity, promotion gates, and basis-watch fields that
future clean evidence-clock rows must satisfy after an explicitly authorized
paper-shadow restart.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_next_forward_candidate_packet_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"
WRAPPER = "scripts\\btc_1hr_high_conf80_entry70_no_chase_shadow.py"
ENGINE = "scripts\\btc_1hr_research_live.py"
MODEL_TTL_POLICY = "scan_time_close_minus_now_v1"
MODEL_POLICY_VERSION = "btc1h_live_model_20260522_scan_ttl_v1"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--multi-holdout-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_multi_holdout_research_latest_codex" / "btc1h_candidate_gate_summary.csv",
    )
    p.add_argument(
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_clean_evidence_clock_gate_latest_codex" / "btc1h_clean_evidence_clock_summary.csv",
    )
    p.add_argument(
        "--clean-clock-collection-preflight-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_clock_collection_preflight_latest_codex"
        / "btc1h_clean_clock_collection_preflight_summary.csv",
    )
    p.add_argument(
        "--basis-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_official_basis_mismatch_audit_latest_codex" / "btc1h_basis_mismatch_summary.csv",
    )
    p.add_argument(
        "--restart-auth-run-info",
        type=Path,
        default=BACKTEST_ROOT / "btc_restart_authorization_packet_latest_codex" / "run_info.json",
    )
    p.add_argument(
        "--forward-status-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex" / "shadow_status.csv",
    )
    p.add_argument(
        "--research-priority-matrix",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_research_priority_matrix_latest_codex" / "btc1h_research_priority_matrix.csv",
    )
    p.add_argument(
        "--research-priority-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_research_priority_matrix_latest_codex" / "btc1h_research_priority_summary.csv",
    )
    p.add_argument(
        "--replay-root-cause-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_root_cause_summary.csv",
    )
    p.add_argument(
        "--replay-mode-comparison",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_mode_comparison.csv",
    )
    p.add_argument(
        "--replay-repair-attempt-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempt_summary.csv",
    )
    p.add_argument("--min-clean-official-rows", type=int, default=50)
    p.add_argument("--max-proxy-official-mismatch-rate", type=float, default=0.02)
    p.add_argument("--basis-watch-boundary-usd", type=float, default=50.0)
    return p.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def first_row(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(key, "")).strip() == expected for key, expected in filters.items()):
            return row
    return {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def source_mtime(path: str) -> str:
    full = PROJECT_ROOT / path
    if not full.exists():
        return ""
    return datetime.fromtimestamp(full.stat().st_mtime, tz=timezone.utc).isoformat()


def decision_for_priority_row(row: dict[str, str]) -> dict[str, Any]:
    variant = str(row.get("variant", "")).strip()
    if variant == VARIANT:
        decision = "keep_as_only_frozen_clean_clock_control"
        research_lane = "forward_control"
        restart_candidate = True
    elif variant == "high_conf_80_entry59_70_no_chase":
        decision = "defer_shadow_until_independent_causal_rows"
        research_lane = "causal_replay_runner_up"
        restart_candidate = False
    elif variant == "high_conf_80_no_chase":
        decision = "basis_watchlist_only_no_restart"
        research_lane = "basis_stress_watchlist"
        restart_candidate = False
    else:
        decision = "deprioritize"
        research_lane = "low_priority_research"
        restart_candidate = False

    return {
        "variant": variant,
        "classification": row.get("classification", ""),
        "decision": decision,
        "research_lane": research_lane,
        "active_forward_control": row.get("active_forward_control", ""),
        "eligible_for_controlled_restart": restart_candidate,
        "deployable_now": False,
        "forward_control_rank": row.get("forward_control_rank", ""),
        "research_replay_rank": row.get("research_replay_rank", ""),
        "historical_holdouts": row.get("historical_holdouts", ""),
        "historical_positive_holdouts": row.get("historical_positive_holdouts", ""),
        "ws_cadences": row.get("ws_cadences", ""),
        "ws_positive_cadences": row.get("ws_positive_cadences", ""),
        "forward_official_rows": row.get("forward_official_rows", ""),
        "forward_official_pnl": row.get("forward_official_pnl", ""),
        "forward_proxy_mismatch_rate": row.get("forward_proxy_mismatch_rate", ""),
        "basis_p95_unique_pnl_positive": row.get("basis_p95_unique_pnl_positive", ""),
        "entry59_replay_overlap_status": row.get("replay_overlap_status", ""),
        "entry59_independent_replay_rows_vs_active": row.get("independent_replay_rows_vs_active", ""),
        "no_chase_extra_row_status": row.get("no_chase_extra_row_status", ""),
        "no_chase_total_extra_pnl": row.get("no_chase_total_extra_pnl", ""),
        "no_chase_fullscan_extra_pnl": row.get("no_chase_fullscan_extra_pnl", ""),
        "deployment_blockers": row.get("deployment_blockers", ""),
        "replay_repair_residual_target_count": row.get("replay_repair_residual_target_count", ""),
        "replay_repair_diagnostic_patch_applied_target_count": row.get(
            "replay_repair_diagnostic_patch_applied_target_count",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_target_count": row.get(
            "replay_repair_unresolved_future_exact_input_target_count",
            "",
        ),
        "replay_repair_diagnostic_patch_applied_targets": row.get(
            "replay_repair_diagnostic_patch_applied_targets",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_targets": row.get(
            "replay_repair_unresolved_future_exact_input_targets",
            "",
        ),
        "replay_repair_targets_repaired_to_promotion_usable_count": row.get(
            "replay_repair_targets_repaired_to_promotion_usable_count",
            "",
        ),
        "replay_repair_all_field_ready_repairs_remain_diagnostic_only": row.get(
            "replay_repair_all_field_ready_repairs_remain_diagnostic_only",
            "",
        ),
        "recommended_next_action": row.get("recommended_next_action", ""),
    }


def build_packet(
    args: argparse.Namespace,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    now = datetime.now(timezone.utc)
    multi = first_row(read_csv(args.multi_holdout_summary), variant=VARIANT)
    clean_rows = read_csv(args.clean_clock_summary)
    clean = first_row(clean_rows, variant=VARIANT) or (clean_rows[0] if clean_rows else {})
    preflight_rows = read_csv(args.clean_clock_collection_preflight_summary)
    preflight = first_row(preflight_rows, variant=VARIANT) or (preflight_rows[0] if preflight_rows else {})
    basis_rows_in = read_csv(args.basis_summary)
    basis = first_row(basis_rows_in, variant=VARIANT) or (basis_rows_in[0] if basis_rows_in else {})
    status = first_row(read_csv(args.forward_status_summary), name=LEDGER)
    auth = read_json(args.restart_auth_run_info)
    priority_rows = read_csv(args.research_priority_matrix)
    priority_summary_rows = read_csv(args.research_priority_summary)
    priority_summary = priority_summary_rows[0] if priority_summary_rows else {}
    replay_root_cause_rows = read_csv(args.replay_root_cause_summary)
    replay_mode_rows = read_csv(args.replay_mode_comparison)
    replay_repair_rows = read_csv(args.replay_repair_attempt_summary)
    replay_root_cause = replay_root_cause_rows[0] if replay_root_cause_rows else {}
    replay_repair = replay_repair_rows[0] if replay_repair_rows else {}
    replay_mode_count = len(replay_mode_rows)
    replay_mode_promotion_usable_count = sum(1 for row in replay_mode_rows if to_bool(row.get("promotion_usable_replay", "")))
    replay_repair_promotion_usable = to_bool(
        replay_repair.get("current_snapshot_repairs_make_replay_promotion_usable", "")
    )
    restart_authorization_status = str(preflight.get("restart_authorization_status", "")).strip()
    restart_authorization_packet_ready = to_bool(
        preflight.get(
            "restart_authorization_packet_ready",
            auth.get("all_ready_for_clean_restart_authorization", False),
        )
    )
    restart_authorization_ready = (
        to_bool(auth.get("all_ready_for_clean_restart_authorization", False))
        or restart_authorization_packet_ready
    )
    preflight_ready_for_authorization = to_bool(preflight.get("ready_for_authorization", ""))
    preflight_collection_evidence_ready = to_bool(preflight.get("collection_evidence_ready", ""))
    preflight_process_control_authorized = to_bool(preflight.get("process_control_authorized", ""))
    authorization_ready_but_collection_evidence_false = (
        preflight_ready_for_authorization and not preflight_collection_evidence_ready
    )
    expected_process_state = str(preflight.get("expected_process_state", "")).strip()
    observed_process_action = str(preflight.get("observed_process_action", "")).strip()
    will_restart_process = to_bool(preflight.get("will_restart_process", ""))
    will_start_new_process = to_bool(preflight.get("will_start_new_process", ""))
    no_process_action_taken = to_bool(preflight.get("no_process_action_taken", ""))
    promotion_readiness = str(multi.get("promotion_readiness_status", "")).strip()
    if not promotion_readiness:
        promotion_readiness = (
            "near_deployable_research_only"
            if to_bool(multi.get("near_deployable_candidate", False))
            else "not_near_deployable_research_only"
        )

    current_evidence = {
        "created_at_utc": now.isoformat(),
        "variant": VARIANT,
        "ledger": LEDGER,
        "research_status": multi.get("research_status", ""),
        "research_promising": multi.get("research_promising", ""),
        "near_deployable_candidate": multi.get("near_deployable_candidate", ""),
        "promotion_readiness_status": promotion_readiness,
        "near_deployable_disqualifying_blockers": multi.get("near_deployable_disqualifying_blockers", ""),
        "deployable_now": False,
        "deployability_state": promotion_readiness,
        "all_positive_holdouts": multi.get("all_positive_holdouts", ""),
        "all_holdouts": multi.get("all_holdouts", ""),
        "ws_positive_cadences": multi.get("ws_positive_cadences", ""),
        "ws_cadences": multi.get("ws_cadences", ""),
        "historical_trades": multi.get("historical_trades", ""),
        "historical_pnl_sum": multi.get("historical_pnl_sum", ""),
        "forward_official_rows": multi.get("forward_official_rows", clean.get("official_rows", "")),
        "forward_official_pnl": multi.get("forward_official_pnl", clean.get("official_pnl", "")),
        "forward_official_mismatch_rate": multi.get("forward_official_mismatch_rate", clean.get("official_proxy_mismatch_rate", "")),
        "replay_ledger_promotion_usable": multi.get("replay_ledger_promotion_usable", clean.get("replay_ledger_promotion_usable", "")),
        "replay_ledger_exact_matches": multi.get("replay_ledger_exact_matches", ""),
        "replay_ledger_actual_rows": multi.get("replay_ledger_actual_rows", ""),
        "replay_root_cause_counts": replay_root_cause.get("root_cause_counts", ""),
        "replay_dominant_root_cause": replay_root_cause.get("dominant_root_cause", ""),
        "replay_requires_order_decision_reprice_model": replay_root_cause.get("requires_order_decision_reprice_model", ""),
        "replay_requires_blocked_dedupe_filter": replay_root_cause.get("requires_blocked_dedupe_filter", ""),
        "replay_requires_exact_model_inputs": replay_root_cause.get("requires_exact_model_inputs", ""),
        "replay_promotion_usable_modes": replay_mode_promotion_usable_count,
        "replay_modes_compared": replay_mode_count,
        "replay_repair_best_current_snapshot_attempt": replay_repair.get("best_current_snapshot_attempt", ""),
        "replay_repair_strict_selected_scan_attempt_verdict": replay_repair.get(
            "strict_selected_scan_attempt_verdict",
            "",
        ),
        "replay_repair_current_snapshot_repairs_promotion_usable": replay_repair_promotion_usable,
        "replay_repair_remaining_blockers_after_best_attempt": replay_repair.get(
            "remaining_blockers_after_best_attempt",
            "",
        ),
        "replay_repair_residual_target_count": replay_repair.get("residual_target_count", ""),
        "replay_repair_diagnostic_patch_applied_target_count": replay_repair.get(
            "diagnostic_patch_applied_target_count",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_target_count": replay_repair.get(
            "unresolved_future_exact_input_target_count",
            "",
        ),
        "replay_repair_diagnostic_patch_applied_targets": replay_repair.get(
            "diagnostic_patch_applied_targets",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_targets": replay_repair.get(
            "unresolved_future_exact_input_targets",
            "",
        ),
        "replay_repair_targets_repaired_to_promotion_usable_count": replay_repair.get(
            "targets_repaired_to_promotion_usable_count",
            "",
        ),
        "replay_repair_all_field_ready_repairs_remain_diagnostic_only": to_bool(
            replay_repair.get("all_field_ready_repairs_remain_diagnostic_only", "")
        ),
        "clean_clock_status": clean.get("gate_status", ""),
        "clean_clock_ready": clean.get("clean_evidence_clock_ready", ""),
        "clean_clock_collection_preflight_status": preflight.get("preflight_status", ""),
        "clean_clock_collection_ready_for_authorization": preflight_ready_for_authorization,
        "clean_clock_collection_evidence_ready": preflight_collection_evidence_ready,
        "clean_clock_collection_process_control_authorized": preflight_process_control_authorized,
        "clean_clock_collection_preflight_blockers": preflight.get("blockers", ""),
        "restart_authorization_status": restart_authorization_status,
        "restart_authorization_packet_ready": restart_authorization_packet_ready,
        "expected_process_state": expected_process_state,
        "observed_process_action": observed_process_action,
        "will_restart_process": will_restart_process,
        "will_start_new_process": will_start_new_process,
        "no_process_action_taken": no_process_action_taken,
        "authorization_ready_but_collection_evidence_false": authorization_ready_but_collection_evidence_false,
        "basis_audit_status": basis.get("audit_status", ""),
        "basis_official_minus_proxy_pnl": basis.get("official_minus_proxy_pnl", ""),
        "basis_proxy_win_official_loss_flips": basis.get("proxy_win_official_loss_flips", ""),
        "shadow_running": status.get("running", ""),
        "shadow_source_freshness_status": status.get("source_freshness_status", ""),
        "restart_authorization_ready": restart_authorization_ready,
    }

    freeze_spec = {
        "created_at_utc": now.isoformat(),
        "variant": VARIANT,
        "ledger": LEDGER,
        "wrapper_script": WRAPPER,
        "engine_script": ENGINE,
        "wrapper_source_mtime_utc": source_mtime(WRAPPER),
        "engine_source_mtime_utc": source_mtime(ENGINE),
        "mode": "paper_only_forward_validation",
        "signal_strategy": VARIANT,
        "sizing_policy": "flat_max",
        "max_contracts_per_trade": 1,
        "min_ttl_min": 5,
        "max_ttl_min": 20,
        "model_ttl_policy": MODEL_TTL_POLICY,
        "model_policy_version": MODEL_POLICY_VERSION,
        "min_edge_cents": 12.0,
        "max_spread_cents": 2.0,
        "min_entry": 0.25,
        "max_entry": 0.70,
        "min_yes_probability_for_yes": 0.80,
        "max_yes_probability_for_no": 0.20,
        "no_chase_10m_usd": 150.0,
        "fees_required": True,
        "top_visible_qty_required": True,
        "fok_no_fill_required": True,
        "one_trade_per_event": True,
        "official_settlement_required": True,
        "count_rows_before_controlled_restart": False,
        "deployable_now": False,
        "status": "FROZEN_FOR_NEXT_CLEAN_EVIDENCE_CLOCK",
    }

    promotion_spec = {
        "variant": VARIANT,
        "ledger": LEDGER,
        "min_clean_post_restart_official_rows": args.min_clean_official_rows,
        "official_pnl_required": ">0_after_fees",
        "max_proxy_official_mismatch_rate": args.max_proxy_official_mismatch_rate,
        "max_proxy_win_official_loss_flips": 0,
        "replay_ledger_promotion_usable_required": True,
        "row_for_row_replay_match_required": True,
        "execution_realism_fields_required": True,
        "expected_model_policy_rows_required": True,
        "blank_policy_rows_allowed": 0,
        "captured_ttl_complete_required": True,
        "replay_root_cause_audit_required": True,
        "replay_mode_comparison_promotion_usable_modes_required": ">=1_promotion_usable_mode_before_replay_counts",
        "replay_repair_attempt_audit_required": True,
        "replay_repair_attempts_promotion_usable_required": True,
        "source_freshness_required": "RUNNING_SOURCE_CURRENT_after_controlled_restart",
        "post_restart_collection_gate_required": True,
        "basis_watch_must_be_reported": True,
        "guard_can_be_fit_from_current_rows": False,
        "deployable_now": False,
    }

    basis_watch_spec = {
        "variant": VARIANT,
        "ledger": LEDGER,
        "watch_boundary_usd": args.basis_watch_boundary_usd,
        "watch_fields": "side;market_ticker;ticker_strike;entry_btc_spot;proxy_close_spot;expiration_value;official_minus_proxy_spot;proxy_side_margin_usd;official_side_margin_usd;quote_age_ms;top_visible_qty;entry_price",
        "current_official_rows": basis.get("official_rows", ""),
        "current_official_proxy_mismatches": basis.get("official_proxy_mismatches", ""),
        "current_proxy_win_official_loss_flips": basis.get("proxy_win_official_loss_flips", ""),
        "current_near_proxy_boundary_rows": basis.get("near_proxy_boundary_rows", ""),
        "current_near_official_boundary_rows": basis.get("near_official_boundary_rows", ""),
        "current_max_abs_basis_usd": basis.get("max_abs_official_minus_proxy_spot", ""),
        "current_p95_abs_basis_usd": basis.get("p95_abs_official_minus_proxy_spot", ""),
        "prospective_only": True,
        "do_not_filter_current_rows": True,
        "deployable_guard_now": False,
        "note": "Watch near-boundary basis flips after clean restart; do not fit a guard from the stale 11-row sample.",
    }

    if priority_rows:
        decision_rows = [decision_for_priority_row(row) for row in priority_rows]
    else:
        decision_rows = [
            {
                "variant": VARIANT,
                "classification": "active_clean_forward_control",
                "decision": "keep_as_only_frozen_clean_clock_control",
                "research_lane": "forward_control",
                "active_forward_control": True,
                "eligible_for_controlled_restart": True,
                "deployable_now": False,
                "forward_control_rank": 1,
                "research_replay_rank": "",
                "historical_holdouts": multi.get("all_holdouts", ""),
                "historical_positive_holdouts": multi.get("all_positive_holdouts", ""),
                "ws_cadences": multi.get("ws_cadences", ""),
                "ws_positive_cadences": multi.get("ws_positive_cadences", ""),
                "forward_official_rows": multi.get("forward_official_rows", clean.get("official_rows", "")),
                "forward_official_pnl": multi.get("forward_official_pnl", clean.get("official_pnl", "")),
                "forward_proxy_mismatch_rate": multi.get(
                    "forward_official_mismatch_rate",
                    clean.get("official_proxy_mismatch_rate", ""),
                ),
                "basis_p95_unique_pnl_positive": "",
                "entry59_replay_overlap_status": "",
                "entry59_independent_replay_rows_vs_active": "",
                "no_chase_extra_row_status": "",
                "no_chase_total_extra_pnl": "",
                "no_chase_fullscan_extra_pnl": "",
                "deployment_blockers": "",
                "recommended_next_action": "Keep as the frozen forward control; require explicit restart authorization before collecting promotion rows.",
            }
        ]

    if preflight_collection_evidence_ready:
        packet_status = "READY_FOR_PROMOTION_COLLECTION_REVIEW"
    elif preflight_ready_for_authorization:
        packet_status = "READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE"
    elif restart_authorization_ready:
        packet_status = "READY_FOR_EXPLICIT_RESTART_AUTHORIZATION"
    else:
        packet_status = "BLOCKED_RESTART_AUTHORIZATION_NOT_READY"

    status = {
        "created_at_utc": now.isoformat(),
        "packet_status": packet_status,
        "deployable_now": False,
        "next_action": "explicit_user_authorized_controlled_paper_shadow_restart_then_collect_clean_official_rows",
        "current_rows_count_for_promotion": False,
        "restart_authorization_ready": bool(restart_authorization_ready),
        "clean_clock_collection_preflight_status": preflight.get("preflight_status", ""),
        "clean_clock_collection_ready_for_authorization": preflight_ready_for_authorization,
        "clean_clock_collection_evidence_ready": preflight_collection_evidence_ready,
        "clean_clock_collection_process_control_authorized": preflight_process_control_authorized,
        "clean_clock_collection_preflight_blockers": preflight.get("blockers", ""),
        "restart_authorization_status": restart_authorization_status,
        "restart_authorization_packet_ready": bool(restart_authorization_packet_ready),
        "expected_process_state": expected_process_state,
        "observed_process_action": observed_process_action,
        "will_restart_process": bool(will_restart_process),
        "will_start_new_process": bool(will_start_new_process),
        "no_process_action_taken": bool(no_process_action_taken),
        "authorization_ready_but_collection_evidence_false": bool(
            authorization_ready_but_collection_evidence_false
        ),
        "clean_clock_ready": bool(to_bool(clean.get("clean_evidence_clock_ready", False))),
        "basis_guard_deployable_now": bool(to_bool(basis.get("deployable_guard_now", False))),
        "candidate_decision_count": len(decision_rows),
        "recommended_forward_policy_change": priority_summary.get("recommended_forward_policy_change", "none"),
        "top_causal_replay_runner_up": priority_summary.get("top_causal_replay_runner_up", ""),
        "top_basis_stress_variant": priority_summary.get("top_basis_stress_variant", ""),
        "replay_root_cause_counts": replay_root_cause.get("root_cause_counts", ""),
        "replay_promotion_usable_modes": replay_mode_promotion_usable_count,
        "replay_modes_compared": replay_mode_count,
        "replay_repair_best_current_snapshot_attempt": replay_repair.get("best_current_snapshot_attempt", ""),
        "replay_repair_strict_selected_scan_attempt_verdict": replay_repair.get(
            "strict_selected_scan_attempt_verdict",
            "",
        ),
        "replay_repair_current_snapshot_repairs_promotion_usable": replay_repair_promotion_usable,
        "replay_repair_remaining_blockers_after_best_attempt": replay_repair.get(
            "remaining_blockers_after_best_attempt",
            "",
        ),
        "replay_repair_residual_target_count": replay_repair.get("residual_target_count", ""),
        "replay_repair_diagnostic_patch_applied_target_count": replay_repair.get(
            "diagnostic_patch_applied_target_count",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_target_count": replay_repair.get(
            "unresolved_future_exact_input_target_count",
            "",
        ),
        "replay_repair_diagnostic_patch_applied_targets": replay_repair.get(
            "diagnostic_patch_applied_targets",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_targets": replay_repair.get(
            "unresolved_future_exact_input_targets",
            "",
        ),
        "replay_repair_targets_repaired_to_promotion_usable_count": replay_repair.get(
            "targets_repaired_to_promotion_usable_count",
            "",
        ),
        "replay_repair_all_field_ready_repairs_remain_diagnostic_only": to_bool(
            replay_repair.get("all_field_ready_repairs_remain_diagnostic_only", "")
        ),
    }
    return [freeze_spec], [promotion_spec], [basis_watch_spec], [current_evidence], decision_rows, status


def build_report(
    freeze: dict[str, Any],
    promotion: dict[str, Any],
    basis: dict[str, Any],
    evidence: dict[str, Any],
    decisions: list[dict[str, Any]],
    status: dict[str, Any],
) -> str:
    lines = [
        "# BTC1H Next Forward Candidate Packet",
        "",
        f"Created UTC: `{status['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Packet status: `{status['packet_status']}`",
        f"- Deployable now: `{status['deployable_now']}`",
        f"- Current rows count for promotion: `{status['current_rows_count_for_promotion']}`",
        f"- Next action: `{status['next_action']}`",
        f"- Recommended forward policy change: `{status['recommended_forward_policy_change']}`",
        "",
        "## Frozen Candidate",
        "",
        f"- Variant: `{freeze['variant']}`",
        f"- Wrapper: `{freeze['wrapper_script']}`",
        f"- Policy: `{freeze['model_policy_version']}` / `{freeze['model_ttl_policy']}`",
        f"- TTL: `{freeze['min_ttl_min']}`-`{freeze['max_ttl_min']}` minutes",
        f"- Entry: `{freeze['min_entry']}`-`{freeze['max_entry']}`",
        f"- Probability: YES >= `{freeze['min_yes_probability_for_yes']}`, NO p_yes <= `{freeze['max_yes_probability_for_no']}`",
        f"- Min edge / max spread: `{freeze['min_edge_cents']}c` / `{freeze['max_spread_cents']}c`",
        f"- No-chase 10m move: `${freeze['no_chase_10m_usd']}`",
        "",
        "## Current Evidence",
        "",
        f"- Holdouts: `{evidence['all_positive_holdouts']} / {evidence['all_holdouts']}`",
        f"- WS cadences: `{evidence['ws_positive_cadences']} / {evidence['ws_cadences']}`",
        f"- Historical trades / PnL: `{evidence['historical_trades']}` / `{evidence['historical_pnl_sum']}`",
        f"- Forward official rows / PnL: `{evidence['forward_official_rows']}` / `{evidence['forward_official_pnl']}`",
        f"- Forward official mismatch rate: `{evidence['forward_official_mismatch_rate']}`",
        f"- Promotion readiness: `{evidence['promotion_readiness_status']}`",
        f"- Near-deployable disqualifying blockers: `{evidence['near_deployable_disqualifying_blockers']}`",
        f"- Replay root causes: `{evidence['replay_root_cause_counts']}`",
        f"- Promotion-usable replay modes: `{evidence['replay_promotion_usable_modes']} / {evidence['replay_modes_compared']}`",
        f"- Replay repair attempts usable: `{evidence['replay_repair_current_snapshot_repairs_promotion_usable']}`",
        f"- Replay residual targets: `{evidence['replay_repair_residual_target_count']}`",
        f"- Diagnostic repair targets: `{evidence['replay_repair_diagnostic_patch_applied_targets']}`",
        f"- Unresolved future-input targets: `{evidence['replay_repair_unresolved_future_exact_input_targets']}`",
        f"- Targets repaired to promotion usability: `{evidence['replay_repair_targets_repaired_to_promotion_usable_count']}`",
        f"- Replay repair blockers: `{evidence['replay_repair_remaining_blockers_after_best_attempt']}`",
        f"- Clean clock: `{evidence['clean_clock_status']}`",
        f"- Clean-clock collection preflight: `{evidence['clean_clock_collection_preflight_status']}`",
        f"- Ready for authorization review: `{evidence['clean_clock_collection_ready_for_authorization']}`",
        f"- Collection evidence ready: `{evidence['clean_clock_collection_evidence_ready']}`",
        f"- Restart authorization status: `{evidence['restart_authorization_status']}`",
        f"- Restart authorization packet ready: `{evidence['restart_authorization_packet_ready']}`",
        f"- Expected process state / observed action: `{evidence['expected_process_state']}` / `{evidence['observed_process_action']}`",
        f"- Will start new / restart after authorization: `{evidence['will_start_new_process']}` / `{evidence['will_restart_process']}`",
        f"- No process action taken by this workflow: `{evidence['no_process_action_taken']}`",
        f"- Authorization-ready but collection evidence false: `{evidence['authorization_ready_but_collection_evidence_false']}`",
        f"- Basis audit: `{evidence['basis_audit_status']}`",
        "",
        "## Promotion Gates",
        "",
        f"- Min clean official rows: `{promotion['min_clean_post_restart_official_rows']}`",
        f"- Max proxy/official mismatch rate: `{promotion['max_proxy_official_mismatch_rate']}`",
        f"- Max proxy-win/official-loss flips: `{promotion['max_proxy_win_official_loss_flips']}`",
        f"- Row-for-row replay required: `{promotion['row_for_row_replay_match_required']}`",
        f"- Replay root-cause audit required: `{promotion['replay_root_cause_audit_required']}`",
        f"- Replay repair attempts promotion-usable required: `{promotion['replay_repair_attempts_promotion_usable_required']}`",
        f"- Execution realism required: `{promotion['execution_realism_fields_required']}`",
        "",
        "## Basis Watch",
        "",
        f"- Watch boundary: `${basis['watch_boundary_usd']}`",
        f"- Current mismatches / flips: `{basis['current_official_proxy_mismatches']}` / `{basis['current_proxy_win_official_loss_flips']}`",
        f"- Current near-boundary rows proxy/official: `{basis['current_near_proxy_boundary_rows']}` / `{basis['current_near_official_boundary_rows']}`",
        "- This is prospective monitoring, not a fitted guard.",
        "",
        "## Candidate Decisions",
        "",
    ]
    for row in decisions:
        lines.append(
            f"- `{row['variant']}`: `{row['decision']}`; deployable now `{row['deployable_now']}`. "
            f"{row['recommended_next_action']}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    freeze_rows, promotion_rows, basis_rows, evidence_rows, decision_rows, status = build_packet(args)
    write_csv(args.out_dir / "btc1h_candidate_freeze_specs.csv", freeze_rows)
    write_csv(args.out_dir / "btc1h_promotion_gate_specs.csv", promotion_rows)
    write_csv(args.out_dir / "btc1h_basis_watch_specs.csv", basis_rows)
    write_csv(args.out_dir / "btc1h_current_evidence_snapshot.csv", evidence_rows)
    write_csv(args.out_dir / "btc1h_candidate_decision_packet.csv", decision_rows)
    (args.out_dir / "run_info.json").write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(
        build_report(freeze_rows[0], promotion_rows[0], basis_rows[0], evidence_rows[0], decision_rows, status),
        encoding="utf-8",
    )
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
