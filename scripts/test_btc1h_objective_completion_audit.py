#!/usr/bin/env python3
"""Tests for BTC1H objective completion audit."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from scripts.build_btc1h_objective_completion_audit import build_audit


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
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def args(tmp: Path) -> argparse.Namespace:
    return argparse.Namespace(
        out_dir=tmp / "out",
        multi_holdout_summary=tmp / "multi.csv",
        research_priority_matrix=tmp / "priority.csv",
        research_priority_summary=tmp / "priority_summary.csv",
        promotion_gap_matrix=tmp / "gap_matrix.csv",
        promotion_gap_summary=tmp / "gap_summary.csv",
        candidate_packet_run_info=tmp / "packet.json",
        candidate_current_evidence=tmp / "evidence.csv",
        snapshot_execution_summary=tmp / "snapshot_execution.csv",
        execution_filter_summary=tmp / "execution_filter.csv",
        execution_filter_basis_summary=tmp / "execution_filter_basis.csv",
        official_pnl_path_summary=tmp / "official_pnl_path.csv",
        holdout_provenance_summary=tmp / "holdout_provenance_summary.csv",
        replay_coverage_summary=tmp / "replay_coverage_summary.csv",
        faithful_replay_data_contract_summary=tmp / "data_contract_summary.csv",
        replay_source_contract_summary=tmp / "source_contract_summary.csv",
        replay_repair_prerequisite_summary=tmp / "replay_repair_prerequisite_summary.csv",
        replay_repair_target_summary=tmp / "replay_repair_target_summary.csv",
        replay_repair_attempt_summary=tmp / "replay_repair_attempt_summary.csv",
        min_clean_official_rows=50,
        max_proxy_official_mismatch_rate=0.02,
    )


def requirement(rows: list[dict[str, str]], requirement_id: str) -> dict[str, str]:
    matches = [row for row in rows if row["requirement_id"] == requirement_id]
    assert len(matches) == 1
    return matches[0]


def test_objective_audit_marks_research_progress_but_not_completion(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "multi.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "research_status": "active_forward_candidate",
                "research_promising": "True",
                "near_deployable_candidate": "False",
                "promotion_readiness_status": "promising_but_blocked_by_official_or_fidelity_gates",
                "deployable_now": "False",
                "all_positive_holdouts": 14,
                "all_holdouts": 14,
                "negative_holdouts": "",
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "forward_official_rows": 11,
                "forward_official_pnl": 0.5,
                "forward_official_mismatch_rate": 0.0909,
                "replay_ledger_promotion_usable": "False",
                "replay_ledger_exact_match_rate": 0.818182,
                "deploy_blockers": "official_proxy_mismatch_gate_failed;counterfactual_replay_not_row_faithful",
                "near_deployable_disqualifying_blockers": (
                    "official_proxy_mismatch_gate_failed;counterfactual_replay_not_row_faithful"
                ),
            },
            {
                "variant": "high_conf_80_entry59_70_no_chase",
                "research_status": "historical_only_promising",
                "research_promising": "True",
                "near_deployable_candidate": "False",
                "promotion_readiness_status": "historical_promising_needs_forward_official_evidence",
                "deployable_now": "False",
                "all_positive_holdouts": 12,
                "all_holdouts": 13,
                "negative_holdouts": "H2b_direct_apr23_may01_holdout",
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "forward_official_rows": 0,
                "forward_official_pnl": 0,
            },
            {
                "variant": "high_conf_80_no_chase",
                "research_status": "research_watch_or_reject",
                "research_promising": "False",
                "near_deployable_candidate": "False",
                "promotion_readiness_status": "research_watch_or_reject",
                "deployable_now": "False",
                "all_positive_holdouts": 13,
                "all_holdouts": 14,
                "negative_holdouts": "H4_live_ws_may06_12_stride1s",
                "ws_positive_cadences": 5,
                "ws_cadences": 6,
                "forward_official_rows": 0,
            },
            {
                "variant": "high_conf_80",
                "research_status": "historical_only_promising",
                "research_promising": "True",
                "near_deployable_candidate": "False",
                "promotion_readiness_status": "historical_promising_needs_forward_official_evidence",
                "deployable_now": "False",
                "all_positive_holdouts": 8,
                "all_holdouts": 9,
                "negative_holdouts": "H3_predexon_may03_06_external",
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "forward_official_rows": 0,
            },
        ],
    )
    write_csv(
        tmp_path / "priority.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "classification": "active_clean_forward_control",
                "deployable_now": "False",
                "active_forward_control": "True",
                "historical_positive_holdouts": 14,
                "historical_holdouts": 14,
                "historical_all_holdouts_pass": "True",
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "ws_all_cadences_pass": "True",
                "forward_official_rows": 11,
                "forward_official_pnl": 0.5,
                "forward_proxy_mismatch_rate": 0.0909,
                "replay_ledger_promotion_usable": "False",
                "replay_ledger_exact_match_rate": 0.818182,
                "deployment_blockers": (
                    "too_few_clean_forward_official_rows;row_for_row_replay_not_promotion_usable;"
                    "execution_realism_fields_missing;basis_stress_unique_pnl_nonpositive_at_p95_or_max;"
                    "active_historical_independence_weak_or_concentrated"
                ),
                "recommended_next_action": "Collect clean rows.",
            },
            {
                "variant": "high_conf_80_entry59_70_no_chase",
                "classification": "top_replay_runner_up_but_not_independent_on_snapshot",
                "deployable_now": "False",
                "active_forward_control": "False",
                "historical_positive_holdouts": 12,
                "historical_holdouts": 13,
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "forward_official_rows": 0,
                "replay_ledger_promotion_usable": "False",
            },
            {
                "variant": "high_conf_80_no_chase",
                "classification": "basis_robust_watchlist",
                "deployable_now": "False",
                "active_forward_control": "False",
                "historical_positive_holdouts": 13,
                "historical_holdouts": 14,
                "ws_positive_cadences": 5,
                "ws_cadences": 6,
                "forward_official_rows": 0,
                "replay_ledger_promotion_usable": "False",
            },
            {
                "variant": "high_conf_80",
                "classification": "low_priority_or_reject",
                "deployable_now": "False",
                "active_forward_control": "False",
                "historical_positive_holdouts": 8,
                "historical_holdouts": 9,
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "forward_official_rows": 0,
                "replay_ledger_promotion_usable": "False",
            },
        ],
    )
    write_csv(
        tmp_path / "priority_summary.csv",
        [
            {
                "active_forward_control": "high_conf_80_entry70_no_chase",
                "top_causal_replay_runner_up": "high_conf_80_entry59_70_no_chase",
                "top_basis_stress_variant": "high_conf_80_no_chase",
            }
        ],
    )
    write_csv(
        tmp_path / "gap_matrix.csv",
        [
            {
                "gate_id": "official_proxy_agreement",
                "status": "BLOCKED",
                "current_evidence": "mismatch rate 0.0909; proxy-win/official-loss flips 1",
                "blocker": "proxy_official_mismatch_or_flip",
                "next_action": "Collect clean rows.",
            },
            {
                "gate_id": "clean_forward_sample_size",
                "status": "BLOCKED",
                "current_evidence": "current stale official rows 11; clean post-restart official rows 0",
                "blocker": "too_few_clean_post_restart_official_rows",
            },
            {
                "gate_id": "official_pnl",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "stale official PnL 0.5",
                "blocker": "positive_only_on_stale_rows",
            },
            {
                "gate_id": "execution_realism_fields",
                "status": "BLOCKED",
                "current_evidence": "field complete rate 0",
                "blocker": "execution_realism_fields_missing",
            },
            {
                "gate_id": "row_for_row_replay",
                "status": "BLOCKED",
                "current_evidence": "promotion usable False; exact match rate 0.818182",
                "blocker": "replay_not_row_faithful",
            },
            {
                "gate_id": "replay_repair_attempts",
                "status": "BLOCKED",
                "current_evidence": "strict selected-scan verdict REGRESSES_ROW_FIDELITY",
                "blocker": "current_snapshot_repairs_not_promotion_usable",
            },
            {
                "gate_id": "clean_evidence_clock",
                "status": "BLOCKED",
                "current_evidence": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "blocker": "controlled_restart_required_or_model_inputs_missing",
            },
            {
                "gate_id": "holdout_independence",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "duplicate market/side rows",
                "blocker": "duplicate_market_side_rows",
            },
            {
                "gate_id": "statistical_confidence",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "unique p025 -1.28",
                "blocker": "unique_market_side_or_stale_official_stat_weak",
            },
            {
                "gate_id": "basis_stress",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "p95 unique PnL -0.22",
                "blocker": "proxy_label_basis_stress_fragility",
            },
        ],
    )
    write_csv(tmp_path / "gap_summary.csv", [{"blocked_gate_count": 9}])
    write_csv(
        tmp_path / "evidence.csv",
        [{"replay_repair_current_snapshot_repairs_promotion_usable": "False"}],
    )
    write_csv(
        tmp_path / "snapshot_execution.csv",
        [
            {
                "audit_status": "DIAGNOSTIC_OLD_SNAPSHOT_EXECUTION_FIELDS_NOT_PROMOTION_USABLE",
                "required_field_complete_rate": 1.0,
                "quote_age_le_limit_rate": 0.909091,
                "stale_quote_rows": 1,
                "blockers": "quote_age_above_limit;old_snapshot_not_clean_clock_promotion_evidence",
            }
        ],
    )
    write_csv(
        tmp_path / "execution_filter.csv",
        [
            {
                "audit_status": "DIAGNOSTIC_EXECUTION_FILTERED_OLD_SNAPSHOT_NOT_PROMOTION_USABLE",
                "strict_official_rows": 10,
                "strict_official_pnl": 0.2,
                "strict_official_proxy_mismatches": 1,
                "strict_removed_markets": "KXBTCD-26MAY2106-T77699.99",
                "blockers": "too_few_execution_filtered_official_rows;official_proxy_mismatch_remaining",
            }
        ],
    )
    write_csv(
        tmp_path / "execution_filter_basis.csv",
        [
            {
                "audit_status": "DIAGNOSTIC_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH_REMAINS",
                "strict_official_proxy_mismatches": 1,
                "strict_mismatch_markets": "KXBTCD-26MAY2008-T77299.99",
                "mismatch_removed_by_execution_filter": "False",
                "strict_mismatch_official_minus_proxy_spot": 42.15,
                "strict_mismatch_proxy_close_minus_strike": -8.37,
                "strict_mismatch_official_expiration_minus_strike": 33.78,
                "blockers": "single_mismatch_after_execution_filter;too_few_strict_official_rows;guard_not_fit_from_current_rows",
            }
        ],
    )
    write_csv(
        tmp_path / "official_pnl_path.csv",
        [
            {
                "path_audit_status": "DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE",
                "official_rows": 11,
                "official_pnl": 0.5,
                "proxy_pnl": 1.5,
                "official_minus_proxy_pnl": -1.0,
                "max_drawdown": -1.76,
                "drawdown_to_pnl_ratio": 3.52,
                "proxy_official_mismatches": 1,
                "quote_age_gt_90ms_rows": 3,
                "current_rows_count_for_promotion": "False",
                "strict_execution_path_rows": 10,
                "strict_execution_path_official_pnl": 0.2,
                "strict_execution_path_proxy_pnl": 1.2,
                "strict_execution_path_official_minus_proxy_pnl": -1.0,
                "strict_execution_path_max_drawdown": -1.76,
                "strict_execution_path_proxy_official_mismatches": 1,
                "strict_execution_path_current_rows_count_for_promotion": "False",
                "strict_execution_path_removed_markets": "KXBTCD-26MAY2106-T77699.99",
                "blockers": (
                    "too_few_clean_official_rows;official_proxy_mismatch_present;"
                    "path_from_pre_clean_clock_rows;clean_clock_not_ready"
                ),
            }
        ],
    )
    write_csv(
        tmp_path / "holdout_provenance_summary.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "holdout_rows": 16,
                "research_countable_rows": 16,
                "official_forward_diagnostic_rows": 1,
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "current_uses": (
                    "historical_proxy_research_only;official_forward_diagnostic_only;"
                    "live_ws_stability_research_only"
                ),
                "holdout_evidence_status": "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY",
            },
            {
                "variant": "high_conf_80",
                "holdout_rows": 11,
                "research_countable_rows": 11,
                "official_forward_diagnostic_rows": 0,
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "current_uses": "historical_proxy_research_only;live_ws_stability_research_only",
                "holdout_evidence_status": "RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE",
            },
            {
                "variant": "high_conf_80_entry59_70_no_chase",
                "holdout_rows": 15,
                "research_countable_rows": 15,
                "official_forward_diagnostic_rows": 0,
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "current_uses": "historical_proxy_research_only;live_ws_stability_research_only",
                "holdout_evidence_status": "RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE",
            },
            {
                "variant": "high_conf_80_no_chase",
                "holdout_rows": 18,
                "research_countable_rows": 18,
                "official_forward_diagnostic_rows": 0,
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "current_uses": "historical_proxy_research_only;live_ws_stability_research_only",
                "holdout_evidence_status": "RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE",
            },
        ],
    )
    write_csv(
        tmp_path / "replay_coverage_summary.csv",
        [
            {
                "scope": "all",
                "paper_rows": 0,
                "replayable_rows": 0,
                "coverage_gate_pass": "False",
            }
        ],
    )
    write_csv(
        tmp_path / "data_contract_summary.csv",
        [
            {
                "contract_status": "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS",
                "missing_required_field_count": 17,
                "missing_required_fields": (
                    "signal_scan.signal_strategy;signal_scan.model_ttl_policy;"
                    "order_decision.model_policy_version"
                ),
                "current_artifacts_can_support_faithful_replay": "False",
                "root_cause_field_gap_rows": 4,
                "root_cause_future_exact_input_target_count": 2,
                "root_cause_target_missing_required_field_count": 16,
                "root_cause_target_missing_required_field_occurrence_count": 16,
                "root_cause_target_unique_missing_required_field_count": 8,
                "root_cause_target_missing_required_fields": (
                    "signal_scan.model_ttl_policy;signal_scan.ttl_min;signal_scan.btc_rv60"
                ),
                "root_cause_target_repairs_can_make_promotion_usable_now": "False",
                "blockers": (
                    "missing_required_sidecar_fields;future_exact_model_input_capture_required;"
                    "current_replay_not_promotion_usable"
                ),
            }
        ],
    )
    write_csv(
        tmp_path / "source_contract_summary.csv",
        [
            {
                "source_contract_status": "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED",
                "current_source_contract_ready": "True",
            }
        ],
    )
    write_csv(
        tmp_path / "replay_repair_prerequisite_summary.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "repair_target_count": 4,
                "existing_snapshot_true_target_count": 2,
                "existing_snapshot_partial_target_count": 1,
                "existing_snapshot_false_target_count": 1,
                "existing_snapshot_can_repair_targets": (
                    "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
                ),
                "existing_snapshot_partial_targets": "same_event_market_selection",
                "existing_snapshot_false_targets": "skip_then_fill_sequence",
                "matched_drift_repair_events": "KXBTCD-26MAY2107;KXBTCD-26MAY2110",
                "future_exact_input_blocked_targets": (
                    "same_event_market_selection;skip_then_fill_sequence"
                ),
                "all_targets_repairable_from_existing_snapshot": "False",
                "current_snapshot_independent_repair_complete": "False",
                "near_deployable_after_current_replay_repairs": "False",
                "deployable_now": "False",
                "no_deploy_reason": (
                    "Two matched-drift rows can test replay engineering, but same-event selection "
                    "and skip-then-fill still need exact model-input/TTL capture or future clean-clock evidence."
                ),
            }
        ],
    )
    write_csv(
        tmp_path / "replay_repair_target_summary.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "current_independent_row_fidelity_exact": "False",
                "current_independent_promotion_usable": "False",
                "captured_order_decision_baseline_row_fidelity_exact": "True",
                "captured_order_decision_baseline_promotion_usable": "False",
                "current_exact_market_side_matches": 9,
                "current_actual_rows": 11,
                "current_replay_rows": 10,
                "current_replay_minus_actual_pnl": 0.61,
                "repair_target_count": 4,
                "next_repair_targets": (
                    "skip_then_fill_sequence;same_event_market_selection;"
                    "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
                ),
                "future_rows_required_items": (
                    "exact_model_input_capture;official_proxy_basis_gate;sample_size_gate;"
                    "clean_policy_identity"
                ),
                "deployable_now": "False",
            }
        ],
    )
    write_csv(
        tmp_path / "replay_repair_attempt_summary.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "attempt_count": 2,
                "repairable_targets_from_prereq": (
                    "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
                ),
                "matched_drift_patch_rows": 2,
                "matched_drift_patch_removes_entry_drift": "True",
                "strict_selected_scan_attempt_verdict": "REGRESSES_ROW_FIDELITY",
                "best_current_snapshot_attempt": "matched_drift_decision_fill_price_patch_simulation",
                "remaining_blockers_after_best_attempt": (
                    "missing_actual_rows;replay_row_count_differs;extra_replay_rows;"
                    "event_level_market_replacements;pnl_not_row_for_row_equal"
                ),
                "current_snapshot_repairs_make_replay_promotion_usable": "False",
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
                "near_deployable_after_current_replay_repairs": "False",
                "deployable_now": "False",
            }
        ],
    )
    (tmp_path / "packet.json").write_text(
        json.dumps(
            {
                "packet_status": "READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE",
                "deployable_now": False,
                "restart_authorization_status": "READY_FOR_USER_AUTHORIZATION_TO_START",
                "restart_authorization_packet_ready": True,
                "clean_clock_collection_ready_for_authorization": True,
                "clean_clock_collection_evidence_ready": False,
                "clean_clock_collection_process_control_authorized": False,
                "expected_process_state": "start_or_restart_allowed",
                "observed_process_action": "start_absent_target",
                "will_restart_process": False,
                "will_start_new_process": True,
                "no_process_action_taken": True,
                "authorization_ready_but_collection_evidence_false": True,
                "replay_promotion_usable_modes": 0,
                "replay_modes_compared": 6,
                "replay_repair_current_snapshot_repairs_promotion_usable": False,
            }
        ),
        encoding="utf-8",
    )

    candidates, requirements, summary = build_audit(args(tmp_path))
    by_variant = {row["variant"]: row for row in candidates}

    assert summary["objective_complete"] is False
    assert summary["deployable_candidates"] == 0
    assert summary["near_deployable_candidates"] == 0
    assert summary["promising_research_candidates"] == 3
    assert summary["priority_research_candidates"] == 3
    assert summary["multi_holdout_promising_candidates"] == 3
    assert (
        summary["priority_research_candidate_variants"]
        == "high_conf_80_entry70_no_chase;high_conf_80_entry59_70_no_chase;high_conf_80_no_chase"
    )
    assert (
        summary["multi_holdout_promising_variants"]
        == "high_conf_80_entry70_no_chase;high_conf_80_entry59_70_no_chase;high_conf_80"
    )
    assert summary["multi_holdout_promising_but_low_priority_variants"] == "high_conf_80"
    assert summary["priority_watchlist_not_multi_holdout_promising_variants"] == "high_conf_80_no_chase"
    assert summary["promotion_gap_blocked_gate_count"] == "9"
    assert summary["packet_status"] == "READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE"
    assert summary["restart_authorization_status"] == "READY_FOR_USER_AUTHORIZATION_TO_START"
    assert summary["restart_authorization_packet_ready"] is True
    assert summary["clean_clock_collection_ready_for_authorization"] is True
    assert summary["clean_clock_collection_evidence_ready"] is False
    assert summary["clean_clock_collection_process_control_authorized"] is False
    assert summary["expected_process_state"] == "start_or_restart_allowed"
    assert summary["observed_process_action"] == "start_absent_target"
    assert summary["will_restart_process"] is False
    assert summary["will_start_new_process"] is True
    assert summary["no_process_action_taken"] is True
    assert summary["authorization_ready_but_collection_evidence_false"] is True
    assert summary["snapshot_execution_audit_status"] == "DIAGNOSTIC_OLD_SNAPSHOT_EXECUTION_FIELDS_NOT_PROMOTION_USABLE"
    assert summary["snapshot_execution_stale_quote_rows"] == "1"
    assert summary["execution_filter_audit_status"] == "DIAGNOSTIC_EXECUTION_FILTERED_OLD_SNAPSHOT_NOT_PROMOTION_USABLE"
    assert summary["execution_filter_strict_official_rows"] == "10"
    assert summary["execution_filter_strict_official_pnl"] == "0.2"
    assert (
        summary["execution_filtered_basis_mismatch_audit_status"]
        == "DIAGNOSTIC_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH_REMAINS"
    )
    assert summary["execution_filtered_basis_mismatch_rows"] == "1"
    assert summary["execution_filtered_basis_mismatch_market"] == "KXBTCD-26MAY2008-T77299.99"
    assert summary["execution_filtered_basis_mismatch_removed_by_execution_filter"] == "False"
    assert summary["execution_filtered_basis_mismatch_official_minus_proxy_spot"] == "42.15"
    assert (
        summary["official_pnl_path_audit_status"]
        == "DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE"
    )
    assert summary["official_pnl_path_rows"] == "11"
    assert summary["official_pnl_path_official_pnl"] == "0.5"
    assert summary["official_pnl_path_max_drawdown"] == "-1.76"
    assert summary["official_pnl_path_proxy_official_mismatches"] == "1"
    assert summary["official_pnl_path_current_rows_count_for_promotion"] == "False"
    assert summary["official_pnl_path_strict_execution_rows"] == "10"
    assert summary["official_pnl_path_strict_execution_official_pnl"] == "0.2"
    assert summary["official_pnl_path_strict_execution_max_drawdown"] == "-1.76"
    assert summary["official_pnl_path_strict_execution_proxy_official_mismatches"] == "1"
    assert summary["official_pnl_path_strict_execution_current_rows_count_for_promotion"] == "False"
    assert summary["official_pnl_path_strict_execution_removed_markets"] == "KXBTCD-26MAY2106-T77699.99"
    assert summary["known_available_data_can_make_near_deployable"] is False
    assert summary["known_available_data_can_make_deployable"] is False
    assert summary["no_known_available_data_class_can_make_near_deployable"] is True
    assert summary["known_available_data_near_deployable_countable_rows"] == 0
    assert summary["known_available_data_deployable_countable_rows"] == 0
    assert summary["promotion_countable_available_data_classes"] == ""
    assert "historical_proxy_research_only" in summary["known_available_data_classes"]
    assert "official_forward_diagnostic_only" in summary["known_available_data_classes"]
    assert "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY" in summary["known_available_data_statuses"]
    assert summary["faithful_replay_data_contract_status"] == "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS"
    assert summary["faithful_replay_missing_required_field_count"] == "17"
    assert "signal_scan.signal_strategy" in summary["faithful_replay_missing_required_fields"]
    assert summary["faithful_replay_current_artifacts_can_support"] == "False"
    assert summary["faithful_replay_root_cause_field_gap_rows"] == "4"
    assert summary["faithful_replay_root_cause_future_exact_input_target_count"] == "2"
    assert summary["faithful_replay_root_cause_target_missing_required_field_count"] == "16"
    assert summary["faithful_replay_root_cause_target_missing_required_field_occurrence_count"] == "16"
    assert summary["faithful_replay_root_cause_target_unique_missing_required_field_count"] == "8"
    assert "signal_scan.ttl_min" in summary["faithful_replay_root_cause_target_missing_required_fields"]
    assert summary["faithful_replay_root_cause_target_repairs_can_make_promotion_usable_now"] == "False"
    assert summary["replay_source_contract_status"] == "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED"
    assert summary["replay_source_contract_ready"] == "True"
    assert summary["replay_repair_blocker_status"] == "BLOCKED_FUTURE_EXACT_INPUT_REQUIRED"
    assert summary["replay_repair_target_count"] == 4
    assert summary["replay_repair_existing_snapshot_true_target_count"] == 2
    assert summary["replay_repair_existing_snapshot_partial_target_count"] == 1
    assert summary["replay_repair_existing_snapshot_false_target_count"] == 1
    assert (
        summary["replay_repair_existing_snapshot_can_repair_targets"]
        == "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
    )
    assert summary["replay_repair_existing_snapshot_partial_targets"] == "same_event_market_selection"
    assert summary["replay_repair_existing_snapshot_false_targets"] == "skip_then_fill_sequence"
    assert (
        summary["replay_repair_future_exact_input_blocked_targets"]
        == "same_event_market_selection;skip_then_fill_sequence"
    )
    assert summary["replay_repair_all_targets_repairable_from_existing_snapshot"] is False
    assert summary["replay_repair_current_snapshot_independent_repair_complete"] is False
    assert "same-event selection" in summary["replay_repair_no_deploy_reason"]
    assert "skip_then_fill_sequence" in summary["replay_repair_target_matrix_next_targets"]
    assert "exact_model_input_capture" in summary["replay_repair_target_matrix_future_rows_required_items"]
    assert summary["replay_repair_current_exact_market_side_matches"] == 9
    assert summary["replay_repair_current_actual_rows"] == 11
    assert summary["replay_repair_current_replay_rows"] == 10
    assert summary["replay_repair_current_replay_minus_actual_pnl"] == "0.61"
    assert summary["replay_repair_current_independent_row_fidelity_exact"] is False
    assert summary["replay_repair_current_independent_promotion_usable"] is False
    assert summary["replay_repair_order_decision_baseline_row_fidelity_exact"] is True
    assert summary["replay_repair_attempt_count"] == 2
    assert (
        summary["replay_repair_attempt_best_current_snapshot_attempt"]
        == "matched_drift_decision_fill_price_patch_simulation"
    )
    assert "event_level_market_replacements" in summary["replay_repair_attempt_remaining_blockers"]
    assert summary["replay_repair_attempt_current_snapshot_repairs_promotion_usable"] is False
    assert summary["replay_repair_residual_target_count"] == 4
    assert summary["replay_repair_diagnostic_patch_applied_target_count"] == 2
    assert summary["replay_repair_unresolved_future_exact_input_target_count"] == 2
    assert (
        summary["replay_repair_diagnostic_patch_applied_targets"]
        == "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
    )
    assert (
        summary["replay_repair_unresolved_future_exact_input_targets"]
        == "skip_then_fill_sequence;same_event_market_selection"
    )
    assert summary["replay_repair_targets_repaired_to_promotion_usable_count"] == 0
    assert summary["replay_repair_all_field_ready_repairs_remain_diagnostic_only"] is True
    assert "faithful_live_replay_gate" in summary["critical_blocked_requirements"]
    assert by_variant["high_conf_80_entry70_no_chase"]["objective_candidate_status"] == "promising_research_control_blocked"
    assert by_variant["high_conf_80_entry70_no_chase"]["candidate_research_class"] == "active_forward_control_research_only"
    assert by_variant["high_conf_80_entry70_no_chase"]["candidate_is_research_only"] is True
    assert (
        by_variant["high_conf_80_entry59_70_no_chase"]["objective_candidate_status"]
        == "promising_research_runner_up_not_independent"
    )
    assert (
        by_variant["high_conf_80_entry59_70_no_chase"]["candidate_research_class"]
        == "causal_replay_runner_up_research_only"
    )
    assert by_variant["high_conf_80_no_chase"]["objective_candidate_status"] == "basis_watchlist_not_forward_validated"
    assert by_variant["high_conf_80_no_chase"]["candidate_research_class"] == "basis_robust_watchlist_research_only"
    assert by_variant["high_conf_80"]["objective_candidate_status"] == "low_priority_or_rejected"
    assert by_variant["high_conf_80"]["candidate_research_class"] == "multi_holdout_historical_promising_research_only"
    assert by_variant["high_conf_80"]["priority_research_candidate"] is False
    assert by_variant["high_conf_80"]["multi_holdout_research_promising"] is True
    active = by_variant["high_conf_80_entry70_no_chase"]
    assert active["promotion_readiness_status"] == "promising_but_blocked_by_official_or_fidelity_gates"
    assert active["negative_holdouts"] == ""
    assert active["available_data_status"] == "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY"
    assert active["available_data_uses"] == (
        "historical_proxy_research_only;official_forward_diagnostic_only;"
        "live_ws_stability_research_only"
    )
    assert active["current_available_data_class"] == "research_plus_official_diagnostic_only"
    assert active["promotion_countable_available_data"] is False
    assert active["candidate_promotion_evidence_status"] == "NO_PROMOTION_COUNTABLE_AVAILABLE_DATA"
    assert active["official_settlement_gate_blocked"] is True
    assert active["execution_realism_gate_blocked"] is True
    assert active["faithful_replay_gate_blocked"] is True
    assert active["clean_evidence_clock_gate_blocked"] is True
    assert active["statistical_or_basis_caveat_active"] is True
    assert active["promotion_gate_failures"] == (
        "official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;"
        "clean_evidence_clock_gate;statistical_or_basis_caveat"
    )
    assert "forward_official_rows=11" in active["promotion_blocker_evidence_snapshot"]
    assert "near_deployable_countable_rows=0" in active["promotion_blocker_evidence_snapshot"]
    assert "replay_ledger_exact_match_rate=0.818182" in active["promotion_blocker_evidence_snapshot"]
    assert "gap_matrix.csv" in active["promotion_blocker_evidence_sources"]
    assert "official_pnl_path.csv" in active["promotion_blocker_evidence_sources"]
    assert "data_contract_summary.csv" in active["promotion_blocker_evidence_sources"]
    assert "packet.json" in active["promotion_blocker_evidence_sources"]
    assert "no promotion-countable available data" in active["why_not_near_deployable"]
    assert "faithful row-for-row replay gate blocked" in active["why_not_near_deployable"]
    assert active["next_evidence_to_reconsider"].startswith("Explicitly authorized clean evidence clock")
    assert "official_proxy_mismatch_gate_failed" in active["near_deployable_disqualifying_blockers"]
    runner_up = by_variant["high_conf_80_entry59_70_no_chase"]
    assert runner_up["promotion_readiness_status"] == "historical_promising_needs_forward_official_evidence"
    assert runner_up["negative_holdouts"] == "H2b_direct_apr23_may01_holdout"
    assert runner_up["current_available_data_class"] == "research_only_no_official_forward_evidence"
    assert "forward_official_rows=0" in runner_up["promotion_blocker_evidence_snapshot"]
    assert "holdout_provenance_summary.csv" in runner_up["promotion_blocker_evidence_sources"]
    assert "not independent from the active snapshot" in runner_up["why_not_near_deployable"]
    assert "differs from entry70" in runner_up["next_evidence_to_reconsider"]
    watchlist = by_variant["high_conf_80_no_chase"]
    assert watchlist["current_available_data_class"] == "research_only_no_official_forward_evidence"
    assert "research_countable_rows=18" in watchlist["promotion_blocker_evidence_snapshot"]
    assert "basis watchlist lacks clean forward official validation" in watchlist["why_not_near_deployable"]
    assert "fresh causal replay" in watchlist["next_evidence_to_reconsider"]
    broad = by_variant["high_conf_80"]
    assert broad["promotion_readiness_status"] == "historical_promising_needs_forward_official_evidence"
    assert broad["negative_holdouts"] == "H3_predexon_may03_06_external"
    assert broad["available_data_status"] == "RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE"
    assert broad["official_forward_diagnostic_rows"] == "0"
    assert broad["promotion_countable_available_data"] is False
    assert broad["candidate_promotion_evidence_status"] == "NO_PROMOTION_COUNTABLE_AVAILABLE_DATA"
    assert broad["current_available_data_class"] == "research_only_no_official_forward_evidence"
    assert "low priority/rejected despite historical positives" in broad["why_not_near_deployable"]
    assert requirement(requirements, "candidate_universe_and_holdouts")["status"] == "PASS_RESEARCH_EVIDENCE"
    assert requirement(requirements, "candidate_ranking_and_identification")["status"] == "PASS_IDENTIFIED"
    assert (
        requirement(requirements, "available_data_scope_and_countability")["status"]
        == "PASS_SCOPED_NO_PROMOTION_USABLE_AVAILABLE_DATA"
    )
    assert "near-deployable-countable rows 0" in requirement(
        requirements,
        "available_data_scope_and_countability",
    )["evidence"]
    assert "no_available_data_class_countable_for_promotion" in requirement(
        requirements,
        "available_data_scope_and_countability",
    )["blocker"]
    assert requirement(requirements, "deployment_or_near_deployment_verdict")[
        "status"
    ] == "PASS_NO_DEPLOYABLE_OR_NEAR_DEPLOYABLE_FOUND"
    assert requirement(requirements, "official_settlement_gate")["status"] == "BLOCKED"
    assert "strict filtered basis mismatch rows 1" in requirement(requirements, "official_settlement_gate")[
        "evidence"
    ]
    assert "official PnL path DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE" in requirement(
        requirements, "official_settlement_gate"
    )["evidence"]
    assert "strict execution path rows 10" in requirement(requirements, "official_settlement_gate")["evidence"]
    assert "strict execution official PnL 0.2" in requirement(requirements, "official_settlement_gate")[
        "evidence"
    ]
    assert "single_mismatch_after_execution_filter" in requirement(requirements, "official_settlement_gate")[
        "blocker"
    ]
    assert "path_from_pre_clean_clock_rows" in requirement(requirements, "official_settlement_gate")["blocker"]
    assert requirement(requirements, "execution_realism_gate")["status"] == "BLOCKED"
    assert "old snapshot diagnostic" in requirement(requirements, "execution_realism_gate")["evidence"]
    assert "strict execution-filter rows 10" in requirement(requirements, "execution_realism_gate")["evidence"]
    assert requirement(requirements, "faithful_live_replay_gate")["status"] == "BLOCKED"
    assert "repair target matrix exact market/side matches 9/11" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "snapshot-repairable 2" in requirement(requirements, "faithful_live_replay_gate")["evidence"]
    assert "future exact-input blocked targets same_event_market_selection;skip_then_fill_sequence" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "diagnostic patch targets order_decision_reprice_fill_price;blocked_dedupe_scan_clock" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "targets repaired to promotion usable 0" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "root-cause field-gap rows 4" in requirement(requirements, "faithful_live_replay_gate")["evidence"]
    assert "overall missing required fields 17" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "target missing-field occurrences 16" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "target unique missing fields 8" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "target missing required field ids signal_scan.model_ttl_policy;signal_scan.ttl_min;signal_scan.btc_rv60" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["evidence"]
    assert "missing_required_sidecar_fields" in requirement(requirements, "faithful_live_replay_gate")["blocker"]
    assert "future_exact_input_required_for:same_event_market_selection;skip_then_fill_sequence" in requirement(
        requirements,
        "faithful_live_replay_gate",
    )["blocker"]
    assert "event_level_market_replacements" in requirement(requirements, "faithful_live_replay_gate")["blocker"]
    clean_clock_requirement = requirement(requirements, "clean_evidence_clock_gate")
    assert clean_clock_requirement["status"] == "BLOCKED"
    assert "restart authorization READY_FOR_USER_AUTHORIZATION_TO_START" in clean_clock_requirement["evidence"]
    assert "expected process state start_or_restart_allowed" in clean_clock_requirement["evidence"]
    assert "observed process action start_absent_target" in clean_clock_requirement["evidence"]
    assert "will start new True" in clean_clock_requirement["evidence"]
    assert "will restart False" in clean_clock_requirement["evidence"]
