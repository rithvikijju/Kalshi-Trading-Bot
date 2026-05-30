#!/usr/bin/env python3
"""Tests for BTC1H faithful-replay data contract."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from scripts.build_btc1h_faithful_replay_data_contract import FIELD_CONTRACTS, build_contract


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
        status_json=tmp / "status.json",
        sidecar_schema_json=tmp / "schema.json",
        clean_clock_summary=tmp / "clean_clock.csv",
        replay_reconciliation_summary=tmp / "replay_reconciliation.csv",
        repair_target_summary=tmp / "repair_target.csv",
        repair_prerequisite_summary=tmp / "repair_prereq.csv",
        repair_prerequisite_audit=tmp / "repair_prereq_audit.csv",
        repair_attempt_summary=tmp / "repair_attempt.csv",
    )


def write_common_csvs(tmp: Path, *, clean_ready: bool = False, replay_usable: bool = False) -> None:
    write_csv(
        tmp / "clean_clock.csv",
        [
            {
                "clean_evidence_clock_ready": str(clean_ready),
                "expected_policy_official_rows": 12 if clean_ready else 0,
                "blank_policy_official_rows": 0 if clean_ready else 11,
                "sidecar_signal_missing_fields": ""
                if clean_ready
                else "signal_strategy;model_ttl_policy;model_policy_version;quote_age_ms",
                "sidecar_order_decision_missing_fields": ""
                if clean_ready
                else "signal_strategy;model_ttl_policy;model_policy_version",
            }
        ],
    )
    write_csv(
        tmp / "replay_reconciliation.csv",
        [{"promotion_usable_replay": str(replay_usable)}],
    )
    write_csv(
        tmp / "repair_target.csv",
        [{"future_rows_required_items": "exact_model_input_capture;clean_policy_identity"}],
    )
    write_csv(
        tmp / "repair_prereq.csv",
        [{"future_exact_input_blocked_targets": "" if clean_ready else "same_event_market_selection"}],
    )
    write_csv(
        tmp / "repair_prereq_audit.csv",
        [
            {
                "repair_target": "skip_then_fill_sequence",
                "root_cause": "reprice_skip_then_fill_sequence_missing",
                "existing_snapshot_can_repair_independent_replay": "True" if clean_ready else "False",
                "missing_prerequisites": "" if clean_ready else "exact_model_input_capture_or_order_decision_sequence_model",
                "why": "skip/fill sequence needs exact model inputs when old scans cannot recompute the signal",
                "next_validation_step": "capture exact model inputs",
            },
            {
                "repair_target": "same_event_market_selection",
                "root_cause": "same_event_market_selection_not_row_faithful",
                "existing_snapshot_can_repair_independent_replay": "True" if clean_ready else "Partial",
                "missing_prerequisites": "" if clean_ready else "exact_model_input_or_selected_market_policy_capture;model_value_parity",
                "why": "same-event market selection needs model and selected-market policy parity",
                "next_validation_step": "capture exact selected-market inputs",
            },
            {
                "repair_target": "order_decision_reprice_fill_price",
                "root_cause": "order_decision_reprice_fill_price_missing",
                "existing_snapshot_can_repair_independent_replay": "True",
                "missing_prerequisites": "",
                "why": "decision fill price exists",
                "next_validation_step": "use order decision fill price",
            },
            {
                "repair_target": "blocked_dedupe_scan_clock",
                "root_cause": "blocked_dedupe_or_post_selected_scan_used_as_replay_clock",
                "existing_snapshot_can_repair_independent_replay": "True",
                "missing_prerequisites": "",
                "why": "selected-chain timing exists",
                "next_validation_step": "filter blocked clocks",
            },
        ],
    )
    write_csv(
        tmp / "repair_attempt.csv",
        [
            {
                "current_snapshot_repairs_make_replay_promotion_usable": str(replay_usable),
                "remaining_blockers_after_best_attempt": "" if replay_usable else "missing_actual_rows",
            }
        ],
    )


def complete_schema() -> dict[str, list[str]]:
    by_table: dict[str, list[str]] = {}
    for table, field, _role, _why in FIELD_CONTRACTS:
        by_table.setdefault(table, [])
        if field not in by_table[table]:
            by_table[table].append(field)
    return by_table


def test_contract_blocks_current_schema_missing_clean_clock_fields(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "replay_sidecar_rows_by_table": {
                    "signal_scan": 100,
                    "order_decision": 5,
                    "ws_orderbook_top": 1000,
                    "ws_lifecycle": 50,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "schema.json").write_text(
        json.dumps(
            {
                "signal_scan": [
                    "received_at_ns",
                    "action",
                    "event_ticker",
                    "market_ticker",
                    "selected_market",
                    "selected_side",
                    "blocked_events",
                ],
                "order_decision": [
                    "received_at_ns",
                    "action",
                    "event_ticker",
                    "market_ticker",
                    "side",
                    "entry_price",
                    "yes_limit_price",
                    "contracts",
                ],
                "ws_orderbook_top": complete_schema()["ws_orderbook_top"],
                "ws_lifecycle": complete_schema()["ws_lifecycle"],
            }
        ),
        encoding="utf-8",
    )
    write_common_csvs(tmp_path, clean_ready=False, replay_usable=False)

    rows, target_gap_rows, summary = build_contract(args(tmp_path))
    gaps = {row["repair_target"]: row for row in target_gap_rows}

    assert summary["contract_status"] == "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS"
    assert summary["missing_required_field_count"] > 0
    assert "signal_scan.signal_strategy" in summary["missing_required_fields"]
    assert "order_decision.model_policy_version" in summary["missing_required_fields"]
    assert summary["current_artifacts_can_support_faithful_replay"] is False
    assert summary["requires_explicit_authorization"] is True
    assert summary["requires_process_control"] is True
    assert summary["no_process_action_taken"] is True
    assert any(row["field_id"] == "signal_scan.quote_age_ms" for row in rows)
    assert summary["root_cause_field_gap_rows"] == 4
    assert summary["root_cause_future_exact_input_target_count"] == 2
    assert summary["root_cause_target_field_contract_ready_count"] == 2
    assert summary["root_cause_target_missing_required_field_count"] == 19
    assert summary["root_cause_target_missing_required_field_occurrence_count"] == 19
    assert summary["root_cause_target_unique_missing_required_field_count"] == 11
    assert "signal_scan.model_ttl_policy" in summary["root_cause_target_missing_required_fields"]
    assert gaps["skip_then_fill_sequence"]["future_exact_input_required_for_target"] is True
    assert "signal_scan.ttl_min" in gaps["skip_then_fill_sequence"]["target_missing_required_fields"]
    assert gaps["same_event_market_selection"]["existing_snapshot_can_repair_independent_replay"] == "Partial"
    assert "model_input_output" in gaps["same_event_market_selection"]["target_missing_roles"]
    assert gaps["order_decision_reprice_fill_price"]["target_field_contract_ready"] is True
    assert gaps["order_decision_reprice_fill_price"]["target_repair_can_make_promotion_usable_now"] is False


def test_contract_can_be_field_ready_without_claiming_deployment(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "replay_sidecar_rows_by_table": {
                    "signal_scan": 100,
                    "order_decision": 5,
                    "ws_orderbook_top": 1000,
                    "ws_lifecycle": 50,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "schema.json").write_text(json.dumps(complete_schema()), encoding="utf-8")
    write_common_csvs(tmp_path, clean_ready=True, replay_usable=False)

    _rows, target_gap_rows, summary = build_contract(args(tmp_path))

    assert summary["missing_required_field_count"] == 0
    assert summary["current_field_contract_ready"] is True
    assert summary["clean_policy_identity_ready"] is True
    assert summary["current_artifacts_can_support_faithful_replay"] is True
    assert summary["current_replay_promotion_usable"] is False
    assert summary["deployable_now"] is False
    assert summary["near_deployable_now"] is False
    assert summary["root_cause_target_missing_required_field_count"] == 0
    assert summary["root_cause_target_missing_required_field_occurrence_count"] == 0
    assert summary["root_cause_target_unique_missing_required_field_count"] == 0
    assert summary["root_cause_future_exact_input_target_count"] == 0
    assert summary["root_cause_target_field_contract_ready_count"] == 4
    assert all(row["target_repair_can_make_promotion_usable_now"] is False for row in target_gap_rows)
