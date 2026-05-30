#!/usr/bin/env python3
"""Tests for BTC1H candidate promotion deficit audit."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_candidate_promotion_deficit_audit import build_candidate_rows


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
        candidate_status=tmp / "candidate_status.csv",
        objective_summary=tmp / "objective_summary.csv",
        promotion_gap_matrix=tmp / "promotion_gap_matrix.csv",
        snapshot_execution_summary=tmp / "snapshot_execution.csv",
        faithful_replay_data_contract_summary=tmp / "faithful_contract.csv",
        min_clean_official_rows=50,
        max_proxy_official_mismatch_rate=0.02,
    )


def test_candidate_deficit_audit_quantifies_missing_promotion_evidence(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "candidate_status.csv",
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "objective_candidate_status": "promising_research_control_blocked",
                "candidate_research_class": "active_forward_control_research_only",
                "promotion_countable_available_data": "False",
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "forward_official_rows": 11,
                "forward_official_pnl": 0.5,
                "forward_proxy_mismatch_rate": 0.0909,
                "replay_ledger_promotion_usable": "False",
                "replay_ledger_exact_match_rate": 0.818182,
                "next_evidence_to_reconsider": "Collect clean rows.",
            },
            {
                "variant": "high_conf_80_entry59_70_no_chase",
                "objective_candidate_status": "promising_research_runner_up_not_independent",
                "candidate_research_class": "causal_replay_runner_up_research_only",
                "promotion_countable_available_data": "False",
                "near_deployable_countable_rows": 0,
                "deployable_countable_rows": 0,
                "forward_official_rows": 0,
                "forward_official_pnl": 0.0,
                "forward_proxy_mismatch_rate": 0.0,
                "replay_ledger_promotion_usable": "False",
                "replay_ledger_exact_match_rate": 0.0,
            },
        ],
    )
    write_csv(
        tmp_path / "objective_summary.csv",
        [
            {
                "objective_complete": "False",
                "current_verdict": "objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate",
                "deployable_candidates": 0,
                "near_deployable_candidates": 0,
                "promising_research_candidates": 3,
                "replay_promotion_usable_modes": 0,
                "replay_modes_compared": 6,
                "critical_blocked_requirements": (
                    "official_settlement_gate;execution_realism_gate;"
                    "faithful_live_replay_gate;clean_evidence_clock_gate"
                ),
            }
        ],
    )
    write_csv(
        tmp_path / "promotion_gap_matrix.csv",
        [
            {
                "gate_id": "clean_forward_sample_size",
                "current_evidence": "current stale official rows 11; clean post-restart official rows 0",
            },
            {
                "gate_id": "execution_realism_fields",
                "current_evidence": "field complete rate 0; missing",
            },
            {
                "gate_id": "policy_identity_fields",
                "current_evidence": "blank policy rows 11; expected-policy official rows 0",
            },
            {
                "gate_id": "row_for_row_replay",
                "current_evidence": "promotion usable False; exact match rate 0.818182; promotion-usable replay modes 0/6",
            },
        ],
    )
    write_csv(
        tmp_path / "snapshot_execution.csv",
        [
            {
                "audit_status": "DIAGNOSTIC_OLD_SNAPSHOT_EXECUTION_FIELDS_NOT_PROMOTION_USABLE",
                "required_field_complete_rate": 1.0,
            }
        ],
    )
    write_csv(
        tmp_path / "faithful_contract.csv",
        [
            {
                "missing_required_field_count": 17,
                "root_cause_target_unique_missing_required_field_count": 8,
                "current_artifacts_can_support_faithful_replay": "False",
            }
        ],
    )

    rows, summary = build_candidate_rows(args(tmp_path))
    by_variant = {row["variant"]: row for row in rows}
    active = by_variant["high_conf_80_entry70_no_chase"]
    runner_up = by_variant["high_conf_80_entry59_70_no_chase"]

    assert summary["candidate_count"] == 2
    assert summary["candidates_current_artifacts_can_make_near_deployable"] == 0
    assert summary["candidates_with_no_promotion_countable_data"] == 2
    assert summary["clean_official_row_deficit"] == 50
    assert summary["active_proxy_official_mismatch_rate_excess"] == 0.0709
    assert summary["active_replay_exact_match_rate_deficit"] == 0.181818
    assert summary["faithful_replay_missing_required_field_count"] == 17
    assert active["can_current_artifacts_make_near_deployable"] is False
    assert active["clean_official_row_deficit"] == 50
    assert active["near_deployable_countable_row_deficit"] == 50
    assert active["proxy_official_mismatch_rate_excess"] == 0.0709
    assert active["execution_field_complete_rate_deficit"] == 1.0
    assert active["replay_exact_match_rate_deficit"] == 0.181818
    assert active["faithful_replay_target_unique_missing_required_field_count"] == 8
    assert active["clean_policy_identity_row_deficit"] == 50
    assert "official_proxy_mismatch_rate_above_limit" in active["promotion_deficit_blockers"]
    assert "faithful_replay_capture_fields_missing" in active["promotion_deficit_blockers"]
    assert runner_up["proxy_official_mismatch_status"] == "BLOCKED_NO_CLEAN_OFFICIAL_ROWS"
    assert "no_forward_official_rows" in runner_up["promotion_deficit_blockers"]
