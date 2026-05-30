from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_next_forward_candidate_packet.py"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_one(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_btc1h_next_forward_packet_freezes_candidate_without_promoting(tmp_path: Path) -> None:
    multi = tmp_path / "multi.csv"
    clean = tmp_path / "clean.csv"
    preflight = tmp_path / "preflight.csv"
    basis = tmp_path / "basis.csv"
    auth = tmp_path / "auth.json"
    status = tmp_path / "status.csv"
    priority = tmp_path / "priority.csv"
    priority_summary = tmp_path / "priority_summary.csv"
    replay_root = tmp_path / "replay_root.csv"
    replay_modes = tmp_path / "replay_modes.csv"
    repair_summary = tmp_path / "repair_summary.csv"
    write_csv(
        multi,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "research_status": "active_forward_candidate",
                "research_promising": "True",
                "near_deployable_candidate": "True",
                "promotion_readiness_status": "near_deployable_pending_sample_and_final_execution_gate",
                "near_deployable_disqualifying_blockers": "",
                "all_positive_holdouts": 14,
                "all_holdouts": 14,
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "historical_trades": 159,
                "historical_pnl_sum": 21.39,
                "forward_official_rows": 11,
                "forward_official_pnl": 0.5,
                "forward_official_mismatch_rate": 0.0909,
                "replay_ledger_promotion_usable": "False",
            }
        ],
    )
    write_csv(
        clean,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "gate_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "clean_evidence_clock_ready": "False",
                "official_rows": 11,
                "official_pnl": 0.5,
            }
        ],
    )
    write_csv(
        preflight,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "preflight_status": "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE",
                "ready_for_authorization": "True",
                "collection_evidence_ready": "False",
                "process_control_authorized": "False",
                "restart_authorization_status": "READY_FOR_USER_AUTHORIZATION_TO_START",
                "restart_authorization_packet_ready": "True",
                "expected_process_state": "start_or_restart_allowed",
                "observed_process_action": "start_absent_target",
                "will_restart_process": "False",
                "will_start_new_process": "True",
                "no_process_action_taken": "True",
                "blockers": (
                    "current_rows_not_faithful_replay_capable;clean_evidence_clock_not_ready;"
                    "explicit_user_authorization_required"
                ),
            }
        ],
    )
    write_csv(
        basis,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "audit_status": "TOO_FEW_OFFICIAL_ROWS;OBSERVED_PROXY_OFFICIAL_MISMATCH",
                "deployable_guard_now": "False",
                "official_rows": 11,
                "official_minus_proxy_pnl": -1.0,
                "official_proxy_mismatches": 1,
                "proxy_win_official_loss_flips": 1,
                "near_proxy_boundary_rows": 4,
                "near_official_boundary_rows": 4,
                "max_abs_official_minus_proxy_spot": 87.42,
                "p95_abs_official_minus_proxy_spot": 78.915,
            }
        ],
    )
    auth.write_text(
        json.dumps(
            {
                "all_ready_for_clean_restart_authorization": True,
                "all_ready_for_user_authorization": True,
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        status,
        [
            {
                "name": "btc1h_high_conf80_entry70_no_chase_shadow",
                "running": "True",
                "source_freshness_status": "RUNNING_SOURCE_STALE_RESTART_REQUIRED",
            }
        ],
    )
    write_csv(
        priority,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "classification": "active_clean_forward_control",
                "active_forward_control": "True",
                "forward_control_rank": 1,
                "research_replay_rank": "",
                "historical_holdouts": 14,
                "historical_positive_holdouts": 14,
                "ws_cadences": 6,
                "ws_positive_cadences": 6,
                "forward_official_rows": 11,
                "forward_official_pnl": 0.5,
                "forward_proxy_mismatch_rate": 0.0909,
                "basis_p95_unique_pnl_positive": "False",
                "deployment_blockers": "too_few_clean_forward_official_rows",
                "replay_repair_residual_target_count": 4,
                "replay_repair_diagnostic_patch_applied_target_count": 2,
                "replay_repair_unresolved_future_exact_input_target_count": 2,
                "replay_repair_diagnostic_patch_applied_targets": (
                    "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
                ),
                "replay_repair_unresolved_future_exact_input_targets": (
                    "skip_then_fill_sequence;same_event_market_selection"
                ),
                "replay_repair_targets_repaired_to_promotion_usable_count": 0,
                "replay_repair_all_field_ready_repairs_remain_diagnostic_only": "True",
                "recommended_next_action": "Keep as the frozen forward control.",
            },
            {
                "variant": "high_conf_80_entry59_70_no_chase",
                "classification": "top_replay_runner_up_but_not_independent_on_snapshot",
                "active_forward_control": "False",
                "forward_control_rank": "",
                "research_replay_rank": 1,
                "historical_holdouts": 13,
                "historical_positive_holdouts": 12,
                "ws_cadences": 6,
                "ws_positive_cadences": 6,
                "forward_official_rows": 0,
                "forward_official_pnl": 0,
                "forward_proxy_mismatch_rate": 0,
                "basis_p95_unique_pnl_positive": "True",
                "replay_overlap_status": "EXACT_ROW_SET_MATCH",
                "independent_replay_rows_vs_active": 0,
                "deployment_blockers": "no_independent_live_ws_replay_rows_vs_active_on_snapshot",
                "recommended_next_action": "Do not start a separate shadow yet.",
            },
            {
                "variant": "high_conf_80_no_chase",
                "classification": "basis_robust_watchlist",
                "active_forward_control": "False",
                "forward_control_rank": "",
                "research_replay_rank": 2,
                "historical_holdouts": 14,
                "historical_positive_holdouts": 13,
                "ws_cadences": 6,
                "ws_positive_cadences": 5,
                "forward_official_rows": 0,
                "forward_official_pnl": 0,
                "forward_proxy_mismatch_rate": 0,
                "basis_p95_unique_pnl_positive": "True",
                "no_chase_extra_row_status": "NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING",
                "no_chase_total_extra_pnl": -2.24,
                "no_chase_fullscan_extra_pnl": -0.84,
                "deployment_blockers": "broad_no_chase_extra_rows_damage_live_ws_cadence",
                "recommended_next_action": "Keep as a basis-robust watchlist policy only.",
            },
        ],
    )
    write_csv(
        priority_summary,
        [
            {
                "recommended_forward_policy_change": "none",
                "top_causal_replay_runner_up": "high_conf_80_entry59_70_no_chase",
                "top_basis_stress_variant": "high_conf_80_no_chase",
                "replay_repair_strict_selected_scan_attempt_verdict": "REGRESSES_ROW_FIDELITY",
                "replay_repair_current_snapshot_repairs_promotion_usable": "False",
            }
        ],
    )
    write_csv(
        replay_root,
        [
            {
                "root_cause_rows": 5,
                "unique_root_causes": 4,
                "dominant_root_cause": "same_event_market_selection_not_row_faithful",
                "root_cause_counts": "same_event_market_selection_not_row_faithful=2;reprice_skip_then_fill_sequence_missing=1",
                "requires_order_decision_reprice_model": "True",
                "requires_blocked_dedupe_filter": "True",
                "requires_exact_model_inputs": "False",
            }
        ],
    )
    write_csv(
        replay_modes,
        [
            {
                "mode": "candidate_scan",
                "promotion_usable_replay": "False",
            },
            {
                "mode": "selected_scan",
                "promotion_usable_replay": "False",
            },
        ],
    )
    write_csv(
        repair_summary,
        [
            {
                "best_current_snapshot_attempt": "matched_drift_decision_fill_price_patch_simulation",
                "strict_selected_scan_attempt_verdict": "REGRESSES_ROW_FIDELITY",
                "current_snapshot_repairs_make_replay_promotion_usable": "False",
                "remaining_blockers_after_best_attempt": "missing_actual_rows;replay_row_count_differs",
                "residual_target_count": 4,
                "diagnostic_patch_applied_target_count": 2,
                "unresolved_future_exact_input_target_count": 2,
                "diagnostic_patch_applied_targets": (
                    "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
                ),
                "unresolved_future_exact_input_targets": (
                    "skip_then_fill_sequence;same_event_market_selection"
                ),
                "targets_repaired_to_promotion_usable_count": 0,
                "all_field_ready_repairs_remain_diagnostic_only": "True",
            }
        ],
    )

    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--multi-holdout-summary",
            str(multi),
            "--clean-clock-summary",
            str(clean),
            "--clean-clock-collection-preflight-summary",
            str(preflight),
            "--basis-summary",
            str(basis),
            "--restart-auth-run-info",
            str(auth),
            "--forward-status-summary",
            str(status),
            "--research-priority-matrix",
            str(priority),
            "--research-priority-summary",
            str(priority_summary),
            "--replay-root-cause-summary",
            str(replay_root),
            "--replay-mode-comparison",
            str(replay_modes),
            "--replay-repair-attempt-summary",
            str(repair_summary),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    freeze = read_one(out_dir / "btc1h_candidate_freeze_specs.csv")
    promotion = read_one(out_dir / "btc1h_promotion_gate_specs.csv")
    basis_watch = read_one(out_dir / "btc1h_basis_watch_specs.csv")
    evidence = read_one(out_dir / "btc1h_current_evidence_snapshot.csv")
    decisions = {row["variant"]: row for row in read_rows(out_dir / "btc1h_candidate_decision_packet.csv")}
    run_info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))
    assert freeze["signal_strategy"] == "high_conf_80_entry70_no_chase"
    assert freeze["model_policy_version"] == "btc1h_live_model_20260522_scan_ttl_v1"
    assert freeze["count_rows_before_controlled_restart"] == "False"
    assert promotion["min_clean_post_restart_official_rows"] == "50"
    assert promotion["guard_can_be_fit_from_current_rows"] == "False"
    assert basis_watch["deployable_guard_now"] == "False"
    assert evidence["promotion_readiness_status"] == "near_deployable_pending_sample_and_final_execution_gate"
    assert evidence["deployability_state"] == "near_deployable_pending_sample_and_final_execution_gate"
    assert evidence["replay_dominant_root_cause"] == "same_event_market_selection_not_row_faithful"
    assert evidence["replay_promotion_usable_modes"] == "0"
    assert evidence["replay_modes_compared"] == "2"
    assert evidence["replay_repair_strict_selected_scan_attempt_verdict"] == "REGRESSES_ROW_FIDELITY"
    assert evidence["replay_repair_current_snapshot_repairs_promotion_usable"] == "False"
    assert "missing_actual_rows" in evidence["replay_repair_remaining_blockers_after_best_attempt"]
    assert evidence["replay_repair_residual_target_count"] == "4"
    assert evidence["replay_repair_diagnostic_patch_applied_target_count"] == "2"
    assert evidence["replay_repair_unresolved_future_exact_input_target_count"] == "2"
    assert (
        evidence["replay_repair_diagnostic_patch_applied_targets"]
        == "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
    )
    assert (
        evidence["replay_repair_unresolved_future_exact_input_targets"]
        == "skip_then_fill_sequence;same_event_market_selection"
    )
    assert evidence["replay_repair_targets_repaired_to_promotion_usable_count"] == "0"
    assert evidence["replay_repair_all_field_ready_repairs_remain_diagnostic_only"] == "True"
    assert evidence["clean_clock_collection_preflight_status"] == "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE"
    assert evidence["clean_clock_collection_ready_for_authorization"] == "True"
    assert evidence["clean_clock_collection_evidence_ready"] == "False"
    assert evidence["clean_clock_collection_process_control_authorized"] == "False"
    assert "explicit_user_authorization_required" in evidence["clean_clock_collection_preflight_blockers"]
    assert evidence["restart_authorization_status"] == "READY_FOR_USER_AUTHORIZATION_TO_START"
    assert evidence["restart_authorization_packet_ready"] == "True"
    assert evidence["expected_process_state"] == "start_or_restart_allowed"
    assert evidence["observed_process_action"] == "start_absent_target"
    assert evidence["will_restart_process"] == "False"
    assert evidence["will_start_new_process"] == "True"
    assert evidence["no_process_action_taken"] == "True"
    assert evidence["authorization_ready_but_collection_evidence_false"] == "True"
    assert promotion["replay_root_cause_audit_required"] == "True"
    assert promotion["replay_repair_attempt_audit_required"] == "True"
    assert promotion["replay_repair_attempts_promotion_usable_required"] == "True"
    assert decisions["high_conf_80_entry70_no_chase"]["eligible_for_controlled_restart"] == "True"
    assert decisions["high_conf_80_entry70_no_chase"]["replay_repair_residual_target_count"] == "4"
    assert (
        decisions["high_conf_80_entry70_no_chase"]["replay_repair_diagnostic_patch_applied_targets"]
        == "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
    )
    assert (
        decisions["high_conf_80_entry70_no_chase"]["replay_repair_unresolved_future_exact_input_targets"]
        == "skip_then_fill_sequence;same_event_market_selection"
    )
    assert (
        decisions["high_conf_80_entry70_no_chase"]["replay_repair_targets_repaired_to_promotion_usable_count"]
        == "0"
    )
    assert (
        decisions["high_conf_80_entry70_no_chase"][
            "replay_repair_all_field_ready_repairs_remain_diagnostic_only"
        ]
        == "True"
    )
    assert decisions["high_conf_80_entry59_70_no_chase"]["decision"] == "defer_shadow_until_independent_causal_rows"
    assert decisions["high_conf_80_entry59_70_no_chase"]["eligible_for_controlled_restart"] == "False"
    assert decisions["high_conf_80_no_chase"]["decision"] == "basis_watchlist_only_no_restart"
    assert decisions["high_conf_80_no_chase"]["no_chase_fullscan_extra_pnl"] == "-0.84"
    assert run_info["deployable_now"] is False
    assert run_info["packet_status"] == "READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE"
    assert run_info["candidate_decision_count"] == 3
    assert run_info["recommended_forward_policy_change"] == "none"
    assert run_info["clean_clock_collection_preflight_status"] == "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE"
    assert run_info["clean_clock_collection_ready_for_authorization"] is True
    assert run_info["clean_clock_collection_evidence_ready"] is False
    assert run_info["clean_clock_collection_process_control_authorized"] is False
    assert run_info["restart_authorization_status"] == "READY_FOR_USER_AUTHORIZATION_TO_START"
    assert run_info["restart_authorization_packet_ready"] is True
    assert run_info["expected_process_state"] == "start_or_restart_allowed"
    assert run_info["observed_process_action"] == "start_absent_target"
    assert run_info["will_restart_process"] is False
    assert run_info["will_start_new_process"] is True
    assert run_info["no_process_action_taken"] is True
    assert run_info["authorization_ready_but_collection_evidence_false"] is True
    assert run_info["replay_promotion_usable_modes"] == 0
    assert run_info["replay_modes_compared"] == 2
    assert run_info["replay_repair_strict_selected_scan_attempt_verdict"] == "REGRESSES_ROW_FIDELITY"
    assert run_info["replay_repair_current_snapshot_repairs_promotion_usable"] is False
    assert run_info["replay_repair_residual_target_count"] == "4"
    assert run_info["replay_repair_diagnostic_patch_applied_target_count"] == "2"
    assert run_info["replay_repair_unresolved_future_exact_input_target_count"] == "2"
    assert (
        run_info["replay_repair_diagnostic_patch_applied_targets"]
        == "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
    )
    assert (
        run_info["replay_repair_unresolved_future_exact_input_targets"]
        == "skip_then_fill_sequence;same_event_market_selection"
    )
    assert run_info["replay_repair_targets_repaired_to_promotion_usable_count"] == "0"
    assert run_info["replay_repair_all_field_ready_repairs_remain_diagnostic_only"] is True
