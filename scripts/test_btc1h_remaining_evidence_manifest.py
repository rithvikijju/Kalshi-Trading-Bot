#!/usr/bin/env python3
"""Tests for BTC1H remaining-evidence manifest."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from scripts.build_btc1h_remaining_evidence_manifest import build_manifest


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
        objective_summary=tmp / "objective_summary.csv",
        objective_requirements=tmp / "objective_requirements.csv",
        promotion_gap_matrix=tmp / "promotion_gap_matrix.csv",
        promotion_gap_summary=tmp / "promotion_gap_summary.csv",
        clean_clock_summary=tmp / "clean_clock_summary.csv",
        snapshot_execution_summary=tmp / "snapshot_execution_summary.csv",
        execution_filter_summary=tmp / "execution_filter_summary.csv",
        execution_filter_basis_summary=tmp / "execution_filter_basis_summary.csv",
        replay_root_cause_summary=tmp / "replay_root_cause_summary.csv",
        replay_repair_attempt_summary=tmp / "replay_repair_attempt_summary.csv",
        faithful_replay_data_contract_summary=tmp / "data_contract_summary.csv",
        replay_source_contract_summary=tmp / "source_contract_summary.csv",
        clean_clock_collection_preflight_summary=tmp / "collection_preflight_summary.csv",
        holdout_provenance_summary=tmp / "holdout_provenance_summary.csv",
        replay_coverage_summary=tmp / "replay_coverage_summary.csv",
        candidate_packet_run_info=tmp / "packet.json",
        min_clean_official_rows=50,
        max_proxy_official_mismatch_rate=0.02,
    )


def row_by_id(rows: list[dict[str, object]], evidence_id: str) -> dict[str, object]:
    matches = [row for row in rows if row["evidence_id"] == evidence_id]
    assert len(matches) == 1
    return matches[0]


def seed_blocked_inputs(tmp: Path) -> None:
    write_csv(
        tmp / "objective_summary.csv",
        [
            {
                "objective_complete": "False",
                "deployable_candidates": 0,
                "near_deployable_candidates": 0,
                "replay_promotion_usable_modes": 0,
                "replay_modes_compared": 6,
                "packet_status": "BLOCKED_RESTART_AUTHORIZATION_NOT_READY",
                "packet_deployable_now": "False",
                "critical_blocked_requirements": (
                    "official_settlement_gate;execution_realism_gate;"
                    "faithful_live_replay_gate;clean_evidence_clock_gate"
                ),
                "next_required_evidence": "clean official rows plus replay parity",
            }
        ],
    )
    write_csv(
        tmp / "objective_requirements.csv",
        [
            {
                "requirement_id": "official_settlement_gate",
                "status": "BLOCKED",
                "blocker": "too_few_clean_rows;proxy_official_mismatch",
                "next_action": "Collect clean official rows.",
            },
            {
                "requirement_id": "execution_realism_gate",
                "status": "BLOCKED",
                "blocker": "execution_realism_fields_missing",
                "next_action": "Collect complete execution fields.",
            },
            {
                "requirement_id": "faithful_live_replay_gate",
                "status": "BLOCKED",
                "blocker": "replay_not_row_faithful",
                "next_action": "Capture exact model inputs.",
            },
            {
                "requirement_id": "clean_evidence_clock_gate",
                "status": "BLOCKED",
                "blocker": "controlled_restart_required_or_model_inputs_missing",
                "next_action": "Start a clean evidence clock only with explicit authorization.",
            },
            {
                "requirement_id": "statistical_independence_and_basis_caveats",
                "status": "DIAGNOSTIC_ONLY",
                "blocker": "sample_not_independent_enough",
                "next_action": "Collect independent clean forward evidence.",
            },
            {
                "requirement_id": "deployment_or_near_deployment_verdict",
                "status": "PASS_NO_DEPLOYABLE_OR_NEAR_DEPLOYABLE_FOUND",
                "blocker": "",
                "next_action": "Do not deploy.",
            },
        ],
    )
    write_csv(
        tmp / "promotion_gap_matrix.csv",
        [
            {
                "gate_id": "official_proxy_agreement",
                "status": "BLOCKED",
                "current_evidence": "mismatch rate 0.0909",
                "blocker": "proxy_official_mismatch_or_flip",
                "next_action": "Collect clean rows.",
            },
            {
                "gate_id": "clean_forward_sample_size",
                "status": "BLOCKED",
                "current_evidence": "clean rows 0; stale rows 11",
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
                "current_evidence": "old snapshot only",
                "blocker": "old_snapshot_not_clean_clock_promotion_evidence",
                "next_action": "Collect clean execution fields.",
            },
            {
                "gate_id": "row_for_row_replay",
                "status": "BLOCKED",
                "current_evidence": "promotion usable 0/6",
                "blocker": "replay_not_row_faithful",
            },
            {
                "gate_id": "replay_repair_attempts",
                "status": "BLOCKED",
                "current_evidence": "strict selected-scan regresses row fidelity",
                "blocker": "current_snapshot_repairs_not_promotion_usable",
            },
            {
                "gate_id": "clean_evidence_clock",
                "status": "BLOCKED",
                "current_evidence": "expected-policy official rows 0",
                "blocker": "controlled_restart_required",
                "next_action": "Controlled restart required.",
            },
            {
                "gate_id": "holdout_independence",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "duplicate market sides",
                "blocker": "duplicate_market_side_rows",
            },
            {
                "gate_id": "statistical_confidence",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "unique p025 weak",
                "blocker": "statistical_confidence_weak",
            },
            {
                "gate_id": "basis_stress",
                "status": "DIAGNOSTIC_ONLY",
                "current_evidence": "basis stress fragile",
                "blocker": "basis_stress_fragility",
            },
        ],
    )
    write_csv(tmp / "promotion_gap_summary.csv", [{"blocked_gate_count": 9}])
    write_csv(
        tmp / "clean_clock_summary.csv",
        [
            {
                "gate_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "clean_evidence_clock_ready": "False",
                "expected_policy_official_rows": 0,
                "blank_policy_official_rows": 11,
                "status_age_minutes": 314.4,
                "sidecar_signal_missing_fields": "quote_age_ms;model_policy_version",
                "sidecar_order_decision_missing_fields": "signal_strategy",
                "next_action": "controlled_btc1h_shadow_restart_to_start_clean_evidence_clock",
            }
        ],
    )
    write_csv(
        tmp / "snapshot_execution_summary.csv",
        [
            {
                "audit_status": "DIAGNOSTIC_OLD_SNAPSHOT_EXECUTION_FIELDS_NOT_PROMOTION_USABLE",
                "required_field_complete_rate": 1.0,
                "quote_age_le_limit_rate": 0.909091,
                "stale_quote_rows": 1,
                "blockers": "old_snapshot_not_clean_clock_promotion_evidence",
            }
        ],
    )
    write_csv(
        tmp / "execution_filter_summary.csv",
        [
            {
                "strict_official_rows": 10,
                "strict_official_pnl": 0.2,
                "strict_removed_markets": "KXREMOVED",
                "blockers": "old_snapshot_not_clean_clock_promotion_evidence",
            }
        ],
    )
    write_csv(
        tmp / "execution_filter_basis_summary.csv",
        [
            {
                "audit_status": "DIAGNOSTIC_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH_REMAINS",
                "strict_official_proxy_mismatches": 1,
                "mismatch_removed_by_execution_filter": "False",
                "strict_mismatch_markets": "KXMISMATCH",
                "strict_mismatch_official_minus_proxy_spot": 42.15,
                "strict_mismatch_proxy_close_minus_strike": -8.37,
                "strict_mismatch_official_expiration_minus_strike": 33.78,
                "min_proxy_side_abs_margin_usd": 8.37,
                "blockers": "single_mismatch_after_execution_filter;guard_not_fit_from_current_rows",
                "next_action": "Track prospectively.",
            }
        ],
    )
    write_csv(
        tmp / "replay_root_cause_summary.csv",
        [
            {
                "root_cause_counts": "same_event_market_selection_not_row_faithful=2",
            }
        ],
    )
    write_csv(
        tmp / "replay_repair_attempt_summary.csv",
        [
            {
                "best_current_snapshot_attempt": "matched_drift_decision_fill_price_patch_simulation",
                "current_snapshot_repairs_make_replay_promotion_usable": "False",
                "remaining_blockers_after_best_attempt": "missing_actual_rows;extra_replay_rows",
                "next_required_evidence": "exact model-input/TTL capture",
            }
        ],
    )
    write_csv(
        tmp / "data_contract_summary.csv",
        [
            {
                "contract_status": "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS",
                "missing_required_field_count": 17,
                "missing_required_fields": (
                    "signal_scan.signal_strategy;signal_scan.model_ttl_policy;"
                    "order_decision.model_policy_version"
                ),
                "root_cause_target_missing_required_field_count": 16,
                "root_cause_target_missing_required_field_occurrence_count": 16,
                "root_cause_target_unique_missing_required_field_count": 8,
                "root_cause_target_missing_required_fields": (
                    "signal_scan.model_ttl_policy;signal_scan.model_policy_version;"
                    "signal_scan.ttl_min;signal_scan.close_time;signal_scan.btc_candle_time;"
                    "signal_scan.btc_candle_age_sec;signal_scan.btc_rv60;signal_scan.btc_ret_10m_usd"
                ),
                "current_artifacts_can_support_faithful_replay": "False",
                "blockers": "missing_required_sidecar_fields;clean_policy_identity_not_ready",
            }
        ],
    )
    write_csv(
        tmp / "source_contract_summary.csv",
        [
            {
                "source_contract_status": "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED",
                "current_source_contract_ready": "True",
                "source_missing_field_count": 0,
            }
        ],
    )
    write_csv(
        tmp / "holdout_provenance_summary.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "holdout_rows": 16,
                "research_countable_rows": 16,
                "live_ws_stability_rows": 6,
                "official_forward_diagnostic_rows": 1,
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "official_forward_trades": 11,
                "official_proxy_mismatch_rows": 1,
                "current_uses": (
                    "historical_proxy_research_only;official_forward_diagnostic_only;"
                    "live_ws_stability_research_only"
                ),
                "holdout_evidence_status": "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY",
            },
            {
                "variant": "high_conf_80_no_chase",
                "holdout_rows": 18,
                "research_countable_rows": 18,
                "live_ws_stability_rows": 6,
                "official_forward_diagnostic_rows": 0,
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "official_forward_trades": 0,
                "official_proxy_mismatch_rows": 0,
                "current_uses": "historical_proxy_research_only;live_ws_stability_research_only",
                "holdout_evidence_status": "RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE",
            },
        ],
    )
    write_csv(
        tmp / "replay_coverage_summary.csv",
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
        tmp / "collection_preflight_summary.csv",
        [
            {
                "preflight_status": "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE",
                "ready_for_authorization": "True",
                "collection_evidence_ready": "False",
                "process_control_authorized": "False",
                "post_restart_official_rows": 0,
                "blockers": (
                    "current_rows_not_faithful_replay_capable;clean_evidence_clock_not_ready;"
                    "explicit_user_authorization_required;controlled_restart_not_executed"
                ),
            }
        ],
    )
    (tmp / "packet.json").write_text(
        json.dumps({"packet_status": "BLOCKED_RESTART_AUTHORIZATION_NOT_READY", "deployable_now": False}),
        encoding="utf-8",
    )


def test_manifest_blocks_current_rows_and_preserves_authorization_gate(tmp_path: Path) -> None:
    seed_blocked_inputs(tmp_path)

    rows, available_data_rows, summary = build_manifest(args(tmp_path))

    assert summary["manifest_status"] == "BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE"
    assert summary["deployable_now"] is False
    assert summary["near_deployable_now"] is False
    assert summary["process_control_authorized"] is False
    assert summary["no_process_action_taken"] is True
    assert summary["requirements_current_artifacts_can_satisfy"] == 0
    assert summary["requires_explicit_authorization_count"] == 6
    assert summary["requires_process_control_count"] == 6
    assert "clean_evidence_clock_start" in summary["critical_missing_evidence_ids"]
    assert "faithful_row_for_row_replay_parity" in summary["critical_missing_evidence_ids"]
    assert summary["faithful_replay_data_contract_status"] == "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS"
    assert summary["faithful_replay_missing_required_field_count"] == "17"
    assert "signal_scan.signal_strategy" in summary["faithful_replay_missing_required_fields"]
    assert summary["faithful_replay_root_cause_target_missing_required_field_count"] == "16"
    assert summary["faithful_replay_root_cause_target_missing_required_field_occurrence_count"] == "16"
    assert summary["faithful_replay_root_cause_target_unique_missing_required_field_count"] == "8"
    assert "signal_scan.ttl_min" in summary["faithful_replay_root_cause_target_missing_required_fields"]
    assert summary["replay_source_contract_status"] == "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED"
    assert summary["replay_source_contract_ready"] == "True"
    assert summary["clean_clock_collection_preflight_status"] == "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE"
    assert summary["clean_clock_collection_preflight_ready_for_authorization"] == "True"
    assert summary["clean_clock_collection_preflight_collection_evidence_ready"] == "False"
    assert summary["clean_clock_collection_preflight_process_control_authorized"] == "False"
    assert "explicit_user_authorization_required" in summary["clean_clock_collection_preflight_blockers"]
    assert summary["known_available_data_can_make_near_deployable"] is False
    assert summary["known_available_data_can_make_deployable"] is False
    assert summary["no_known_available_data_class_can_make_near_deployable"] is True
    assert summary["known_available_data_near_deployable_countable_rows"] == 0
    assert summary["known_available_data_deployable_countable_rows"] == 0
    assert summary["promotion_countable_available_data_classes"] == ""
    assert "historical_proxy_research_only" in summary["known_available_data_classes"]
    assert "official_forward_diagnostic_only" in summary["known_available_data_classes"]
    assert "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY" in summary["known_available_data_statuses"]
    assert any(row["coverage_id"] == "holdout_provenance_high_conf_80_entry70_no_chase" for row in available_data_rows)
    assert any(row["coverage_id"] == "faithful_replay_current_artifacts" for row in available_data_rows)
    coverage_by_id = {row["coverage_id"]: row for row in available_data_rows}
    assert "research-only" in str(
        coverage_by_id["holdout_provenance_high_conf_80_entry70_no_chase"]["blockers"]
    )
    assert "official-settlement" in str(
        coverage_by_id["holdout_provenance_high_conf_80_entry70_no_chase"]["blockers"]
    )
    assert "row-for-row faithful replay" in str(coverage_by_id["replay_coverage_all"]["blockers"])
    assert "missing_required_sidecar_fields" in str(coverage_by_id["faithful_replay_current_artifacts"]["blockers"])
    assert "not current collection evidence" in str(coverage_by_id["source_contract_future_collection"]["blockers"])

    replay = row_by_id(rows, "faithful_row_for_row_replay_parity")
    assert "overall missing replay contract fields 17" in str(replay["current_evidence"])
    assert "root-cause target missing-field occurrences 16" in str(replay["current_evidence"])
    assert "root-cause target unique missing fields 8" in str(replay["current_evidence"])

    clean_clock = row_by_id(rows, "clean_evidence_clock_start")
    assert clean_clock["requires_explicit_authorization"] is True
    assert clean_clock["requires_process_control"] is True
    assert clean_clock["can_current_artifacts_satisfy"] is False
    assert "no_expected_policy_official_rows" in str(clean_clock["current_blockers"])
    assert "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE" in str(clean_clock["current_evidence"])

    verdict = row_by_id(rows, "deployment_or_near_deployment_verdict")
    assert verdict["requires_explicit_authorization"] is False
    assert verdict["requires_process_control"] is False
    assert verdict["can_current_artifacts_satisfy"] is False
    assert verdict["safe_now_action"] == "Do not deploy current BTC1H variants."


def test_manifest_records_basis_mismatch_as_prospective_watch_only(tmp_path: Path) -> None:
    seed_blocked_inputs(tmp_path)

    rows, _available_data_rows, _summary = build_manifest(args(tmp_path))

    basis = row_by_id(rows, "basis_mismatch_prospective_watch")
    assert basis["preregistration_required"] is True
    assert basis["can_current_artifacts_satisfy"] is False
    assert "guard_not_fit_from_current_rows" in str(basis["current_blockers"])
    assert "KXMISMATCH" in str(basis["current_evidence"])
    assert "not as a deployment guard" in str(basis["safe_now_action"])
