#!/usr/bin/env python3
"""Tests for BTC1H clean collection preflight."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_clean_collection_preflight import build_preflight


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
        restart_authorization_summary=tmp / "auth.csv",
        post_restart_gate_summary=tmp / "post_gate.csv",
        clean_clock_summary=tmp / "clean_clock.csv",
        faithful_replay_data_contract_summary=tmp / "data_contract.csv",
        replay_source_contract_summary=tmp / "source_contract.csv",
        objective_summary=tmp / "objective.csv",
        remaining_evidence_summary=tmp / "remaining.csv",
    )


def seed_inputs(tmp: Path, *, source_ready: bool = True, restart_executed: bool = False, post_rows: int = 0) -> None:
    write_csv(
        tmp / "auth.csv",
        [
            {
                "family": "BTC1H",
                "candidate": "btc1h_high_conf80_entry70_no_chase",
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "authorization_packet_status": "NEEDS_REVIEW_BEFORE_START_RESTART",
                "user_permission_required": "True",
                "restart_path_status": "PASS_RESTART_PATH_READY",
                "fresh_schema_status": "PASS_SCHEMA_READY",
                "fresh_insert_status": "PASS_INSERT_REALISM_FIELDS",
                "fresh_capture_replay_schema_status": "PASS_REPLAY_SIDECAR_SCHEMA",
                "fresh_capture_replay_signal_missing_fields": "",
                "fresh_capture_replay_order_missing_fields": "",
            }
        ],
    )
    write_csv(
        tmp / "post_gate.csv",
        [
            {
                "family": "BTC1H",
                "candidate": "btc1h_high_conf80_entry70_no_chase",
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "restart_executed": str(restart_executed),
                "restart_source": "restart_result" if restart_executed else "restart_plan_not_executed",
                "post_restart_official_rows": post_rows,
                "min_post_restart_official_rows": 50,
                "promotion_collection_ready": str(restart_executed and post_rows >= 50),
                "gate_status": "PASS" if restart_executed and post_rows >= 50 else "PENDING_CONTROLLED_RESTART",
            }
        ],
    )
    write_csv(
        tmp / "clean_clock.csv",
        [
            {
                "clean_evidence_clock_ready": str(restart_executed and post_rows > 0),
                "expected_policy_official_rows": post_rows if restart_executed else 0,
            }
        ],
    )
    write_csv(
        tmp / "data_contract.csv",
        [
            {
                "current_artifacts_can_support_faithful_replay": str(restart_executed and post_rows > 0),
            }
        ],
    )
    write_csv(
        tmp / "source_contract.csv",
        [
            {
                "current_source_contract_ready": str(source_ready),
                "source_missing_field_count": 0 if source_ready else 3,
            }
        ],
    )
    write_csv(tmp / "objective.csv", [{"deployable_candidates": 0, "near_deployable_candidates": 0}])
    write_csv(tmp / "remaining.csv", [{"manifest_status": "BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE"}])


def test_preflight_ready_after_authorization_but_current_rows_blocked(tmp_path: Path) -> None:
    seed_inputs(tmp_path)

    rows, summary = build_preflight(args(tmp_path))

    assert summary["preflight_status"] == "READY_TO_COLLECT_AFTER_EXPLICIT_AUTHORIZATION"
    assert summary["current_source_contract_ready"] is True
    assert summary["restart_authorization_packet_ready"] is True
    assert summary["restart_executed"] is False
    assert summary["current_rows_blocked_from_promotion"] is True
    assert summary["deployable_now"] is False
    assert summary["no_process_action_taken"] is True
    check_status = {row["check_id"]: row["status"] for row in rows}
    assert check_status["explicit_authorization_not_granted"] == "PENDING_PERMISSION"
    assert check_status["current_rows_excluded"] == "PASS"


def test_preflight_blocks_when_source_contract_missing(tmp_path: Path) -> None:
    seed_inputs(tmp_path, source_ready=False)

    _rows, summary = build_preflight(args(tmp_path))

    assert summary["preflight_status"] == "BLOCKED_PRE_AUTH_REVIEW_REQUIRED"
    assert "source_contract_not_ready" in summary["blockers"]
    assert summary["source_missing_field_count"] == 3


def test_preflight_collection_gate_pass_after_clean_rows(tmp_path: Path) -> None:
    seed_inputs(tmp_path, restart_executed=True, post_rows=50)

    _rows, summary = build_preflight(args(tmp_path))

    assert summary["preflight_status"] == "COLLECTION_GATE_PASS"
    assert summary["restart_executed"] is True
    assert summary["post_restart_official_rows"] == 50
    assert summary["promotion_collection_ready"] is True
