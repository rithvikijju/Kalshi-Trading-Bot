#!/usr/bin/env python3
"""Tests for BTC1H clean-clock collection preflight."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_clean_clock_collection_preflight import build_preflight


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
        replay_source_contract_summary=tmp / "source_contract.csv",
        faithful_replay_data_contract_summary=tmp / "data_contract.csv",
        clean_clock_summary=tmp / "clean_clock.csv",
        restart_authorization_summary=tmp / "restart_authorization.csv",
        shadow_restart_preflight_summary=tmp / "shadow_restart_preflight.csv",
        post_restart_collection_summary=tmp / "post_restart_collection.csv",
        remaining_evidence_summary=tmp / "remaining_evidence.csv",
        objective_summary=tmp / "objective_summary.csv",
    )


def row_by_id(rows: list[dict[str, object]], checklist_id: str) -> dict[str, object]:
    matches = [row for row in rows if row["checklist_id"] == checklist_id]
    assert len(matches) == 1
    return matches[0]


def seed_inputs(tmp: Path) -> None:
    write_csv(
        tmp / "source_contract.csv",
        [
            {
                "source_contract_status": "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED",
                "current_source_contract_ready": "True",
                "source_missing_field_count": 0,
                "source_missing_fields": "",
                "next_action": "Count future rows only after explicit authorization.",
            }
        ],
    )
    write_csv(
        tmp / "data_contract.csv",
        [
            {
                "contract_status": "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS",
                "missing_required_field_count": 17,
                "current_artifacts_can_support_faithful_replay": "False",
                "clean_policy_identity_ready": "False",
                "blockers": "missing_required_sidecar_fields;clean_policy_identity_not_ready",
                "next_action": "Collect future clean-clock rows.",
            }
        ],
    )
    write_csv(
        tmp / "clean_clock.csv",
        [
            {
                "gate_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "clean_evidence_clock_ready": "False",
                "expected_policy_official_rows": 0,
                "blank_policy_official_rows": 11,
                "sidecar_signal_missing_fields": "quote_age_ms;model_policy_version",
                "sidecar_order_decision_missing_fields": "signal_strategy",
                "next_action": "controlled_btc1h_shadow_restart_to_start_clean_evidence_clock",
            }
        ],
    )
    write_csv(
        tmp / "restart_authorization.csv",
        [
            {
                "family": "BTC1H",
                "candidate": "btc1h_high_conf80_entry70_no_chase",
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "authorization_packet_status": "READY_FOR_USER_AUTHORIZATION",
                "pre_authorization_blockers": "",
                "process_count": 1,
                "process_hygiene_status": "ONE_TARGET_PROCESS",
                "shadow_running_in_forward_status": "True",
                "expected_process_state": "start_or_restart_allowed",
                "observed_process_action": "restart_single_target",
                "forward_status_age_minutes": 10,
                "forward_status_fresh": "True",
                "user_permission_required": "True",
                "latest_restart_plan_script_safety_pass": "True",
                "will_restart_process": "True",
                "will_start_new_process": "False",
                "will_archive_trade_db_by_default": "True",
                "live_capture_untouched": "True",
                "restart_path_status": "PASS_RESTART_PATH_READY",
                "fresh_schema_status": "PASS_SCHEMA_READY",
                "fresh_insert_status": "PASS_INSERT_REALISM_FIELDS",
                "fresh_capture_sidecar_status": "PASS_CAPTURE_SIDECAR",
                "fresh_capture_replay_schema_status": "PASS_REPLAY_SIDECAR_SCHEMA",
                "fresh_capture_replay_signal_missing_fields": "",
                "fresh_capture_replay_order_missing_fields": "",
                "post_restart_gate_status": "PENDING_CONTROLLED_RESTART",
                "post_restart_official_rows": 0,
                "min_post_restart_official_rows": 50,
                "post_restart_failure_reasons": (
                    "controlled_restart_not_executed;no_post_restart_paper_rows;"
                    "too_few_post_restart_official_rows"
                ),
                "kill_continue_next_step": "clean official-settlement restart needs explicit permission.",
            }
        ],
    )
    write_csv(
        tmp / "shadow_restart_preflight.csv",
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "restart_path_status": "PASS_RESTART_PATH_READY",
                "fresh_schema_status": "PASS_SCHEMA_READY",
                "fresh_insert_status": "PASS_INSERT_REALISM_FIELDS",
                "fresh_capture_sidecar_status": "PASS_CAPTURE_SIDECAR",
                "fresh_capture_replay_schema_status": "PASS_REPLAY_SIDECAR_SCHEMA",
                "fresh_capture_replay_signal_missing_fields": "",
                "fresh_capture_replay_order_missing_fields": "",
                "interpretation": "Current code can create/migrate a deployable ledger schema.",
            }
        ],
    )
    write_csv(
        tmp / "post_restart_collection.csv",
        [
            {
                "family": "BTC1H",
                "candidate": "btc1h_high_conf80_entry70_no_chase",
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "restart_executed": "False",
                "post_restart_official_rows": 0,
                "min_post_restart_official_rows": 50,
                "official_pnl": 0.0,
                "proxy_official_mismatches": 0,
                "gate_status": "PENDING_CONTROLLED_RESTART",
                "promotion_collection_ready": "False",
                "failure_reasons": (
                    "controlled_restart_not_executed;no_post_restart_paper_rows;"
                    "too_few_post_restart_official_rows"
                ),
            }
        ],
    )
    write_csv(
        tmp / "remaining_evidence.csv",
        [
            {
                "critical_missing_evidence_ids": (
                    "clean_evidence_clock_start;official_settled_clean_sample;"
                    "execution_realism_clean_rows;faithful_row_for_row_replay_parity"
                )
            }
        ],
    )
    write_csv(
        tmp / "objective_summary.csv",
        [
            {
                "objective_complete": "False",
                "deployable_candidates": 0,
                "near_deployable_candidates": 0,
                "critical_blocked_requirements": (
                    "official_settlement_gate;execution_realism_gate;"
                    "faithful_live_replay_gate;clean_evidence_clock_gate"
                ),
            }
        ],
    )


def test_preflight_is_ready_for_authorization_but_not_collection_evidence(tmp_path: Path) -> None:
    seed_inputs(tmp_path)

    rows, summary = build_preflight(args(tmp_path))

    assert summary["preflight_status"] == "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE"
    assert summary["ready_for_authorization"] is True
    assert summary["collection_evidence_ready"] is False
    assert summary["source_contract_ready"] is True
    assert summary["current_artifacts_can_support_faithful_replay"] is False
    assert summary["clean_evidence_clock_ready"] is False
    assert summary["restart_authorization_status"] == "READY_FOR_USER_AUTHORIZATION"
    assert summary["restart_authorization_packet_ready"] is True
    assert summary["target_process_running"] is True
    assert summary["expected_process_state"] == "start_or_restart_allowed"
    assert summary["observed_process_action"] == "restart_single_target"
    assert summary["user_permission_required"] is True
    assert summary["fresh_capture_replay_schema_status"] == "PASS_REPLAY_SIDECAR_SCHEMA"
    assert summary["post_restart_gate_status"] == "PENDING_CONTROLLED_RESTART"
    assert summary["post_restart_official_rows"] == 0
    assert summary["min_post_restart_official_rows"] == 50
    assert summary["promotion_collection_ready"] is False
    assert summary["deployable_now"] is False
    assert summary["near_deployable_now"] is False
    assert summary["process_control_authorized"] is False
    assert summary["no_process_action_taken"] is True
    assert summary["requires_explicit_authorization"] is True
    assert "explicit_user_authorization_required" in summary["blockers"]

    assert row_by_id(rows, "source_contract_ready")["status"] == "PASS_SOURCE_READY"
    assert row_by_id(rows, "current_rows_faithful_replay_contract")["status"] == "BLOCKED_CURRENT_ROWS_NOT_FAITHFUL"
    assert row_by_id(rows, "clean_evidence_clock_state")["status"] == "BLOCKED_CLEAN_CLOCK_NOT_READY"
    assert row_by_id(rows, "restart_path_preflight")["status"] == "PASS_PREP_READY"
    assert row_by_id(rows, "target_process_state")["status"] == "PASS_TARGET_PROCESS_STATE_READY"
    assert row_by_id(rows, "explicit_authorization")["status"] == "BLOCKED_AUTHORIZATION_REQUIRED"
    assert row_by_id(rows, "post_restart_official_sample")["status"] == "BLOCKED_NO_POST_RESTART_ROWS"
    assert row_by_id(rows, "deployability_verdict")["status"] == "BLOCKED_NO_DEPLOY"


def test_missing_source_contract_blocks_authorization_readiness(tmp_path: Path) -> None:
    seed_inputs(tmp_path)
    write_csv(
        tmp_path / "source_contract.csv",
        [
            {
                "source_contract_status": "SOURCE_MISSING_FAITHFUL_REPLAY_CONTRACT_FIELDS",
                "current_source_contract_ready": "False",
                "source_missing_field_count": 2,
                "source_missing_fields": "signal_scan.quote_age_ms;order_decision.signal_strategy",
            }
        ],
    )

    rows, summary = build_preflight(args(tmp_path))

    assert summary["preflight_status"] == "BLOCKED_PREPARATION_NOT_READY"
    assert summary["ready_for_authorization"] is False
    assert summary["source_contract_ready"] is False
    assert "source_contract_not_ready" in summary["blockers"]
    assert row_by_id(rows, "source_contract_ready")["status"] == "BLOCKED_SOURCE_MISSING_FIELDS"


def test_restart_authorization_packet_must_be_ready_for_authorization(tmp_path: Path) -> None:
    seed_inputs(tmp_path)
    rows = list(csv.DictReader((tmp_path / "restart_authorization.csv").open(newline="", encoding="utf-8")))
    rows[0].update(
        {
            "authorization_packet_status": "NEEDS_REVIEW_BEFORE_START_RESTART",
            "pre_authorization_blockers": "target_process_state_expected",
            "process_count": 0,
            "process_hygiene_status": "NOT_RUNNING",
            "shadow_running_in_forward_status": "False",
            "expected_process_state": "start_or_restart_allowed",
            "observed_process_action": "start_absent_target",
            "forward_status_age_minutes": 22.1,
            "forward_status_fresh": "True",
        }
    )
    write_csv(tmp_path / "restart_authorization.csv", rows)

    checklist, summary = build_preflight(args(tmp_path))

    assert summary["preflight_status"] == "BLOCKED_PREPARATION_NOT_READY"
    assert summary["ready_for_authorization"] is False
    assert summary["restart_authorization_packet_ready"] is False
    assert summary["restart_authorization_status"] == "NEEDS_REVIEW_BEFORE_START_RESTART"
    assert summary["target_process_running"] is False
    assert summary["target_process_hygiene_status"] == "NOT_RUNNING"
    assert summary["expected_process_state"] == "start_or_restart_allowed"
    assert summary["observed_process_action"] == "start_absent_target"
    assert "restart_authorization_packet_not_ready" in summary["blockers"]
    assert "target_process_state_expected" in summary["blockers"]
    assert row_by_id(checklist, "restart_path_preflight")["status"] == "BLOCKED_PREP_NOT_READY"
    assert row_by_id(checklist, "target_process_state")["status"] == "BLOCKED_TARGET_PROCESS_STATE"
