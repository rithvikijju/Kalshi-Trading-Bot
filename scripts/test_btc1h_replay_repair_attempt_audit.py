from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_btc1h_replay_repair_attempt_audit import build_attempts


def test_repair_attempt_audit_keeps_patch_simulation_out_of_promotion() -> None:
    current_summary = {
        "actual_rows": 11,
        "replay_rows": 10,
        "exact_market_side_matches": 9,
        "ledger_only_rows": 2,
        "replay_only_rows": 1,
        "event_replacement_rows": 2,
        "entry_price_drift_rows": 2,
        "pnl_drift_rows": 2,
        "actual_official_pnl": 0.50,
        "replay_pnl": 1.11,
        "replay_minus_actual_pnl": 0.61,
    }
    detail = pd.DataFrame(
        [
            {
                "status": "exact_market_side_match",
                "event_ticker": "KXBTCD-26MAY2107",
                "ledger_entry_price": 0.69,
                "replay_entry_price": 0.70,
                "entry_price_abs_diff": 0.01,
                "ledger_pnl": 0.29,
                "replay_pnl": 0.28,
                "pnl_diff_replay_minus_ledger": -0.01,
            },
            {
                "status": "exact_market_side_match",
                "event_ticker": "KXBTCD-26MAY2110",
                "ledger_entry_price": 0.66,
                "replay_entry_price": 0.65,
                "entry_price_abs_diff": 0.01,
                "ledger_pnl": 0.32,
                "replay_pnl": 0.33,
                "pnl_diff_replay_minus_ledger": 0.01,
            },
            {
                "status": "ledger_only_missing_from_replay_event_replacement",
                "event_ticker": "KXBTCD-26MAY2010",
                "entry_price_abs_diff": "",
                "pnl_diff_replay_minus_ledger": "",
            },
            {
                "status": "replay_only_extra_vs_ledger_event_replacement",
                "event_ticker": "KXBTCD-26MAY2010",
                "entry_price_abs_diff": "",
                "pnl_diff_replay_minus_ledger": "",
            },
        ]
    )
    selected_summary = {
        "actual_rows": 11,
        "replay_rows": 6,
        "exact_market_side_matches": 6,
        "ledger_only_rows": 5,
        "replay_only_rows": 0,
        "event_replacement_rows": 0,
        "entry_price_drift_rows": 1,
        "pnl_drift_rows": 1,
        "actual_official_pnl": 0.50,
        "replay_pnl": -0.14,
        "replay_minus_actual_pnl": -0.64,
    }
    prereq = pd.DataFrame(
        [
            {
                "repair_target": "order_decision_reprice_fill_price",
                "affected_events": "KXBTCD-26MAY2107",
                "existing_snapshot_can_repair_independent_replay": "True",
            },
            {
                "repair_target": "blocked_dedupe_scan_clock",
                "affected_events": "KXBTCD-26MAY2110",
                "existing_snapshot_can_repair_independent_replay": "True",
            },
                {
                    "repair_target": "skip_then_fill_sequence",
                    "affected_events": "KXBTCD-26MAY1911",
                    "existing_snapshot_can_repair_independent_replay": "False",
                    "missing_prerequisites": "exact_model_input_capture_or_order_decision_sequence_model",
                },
        ]
    )

    attempts, residual_rows, summary = build_attempts(
        current_summary,
        detail,
        selected_summary,
        prereq,
        Path("current"),
        Path("selected"),
    )

    patch = next(row for row in attempts if row["attempt"] == "matched_drift_decision_fill_price_patch_simulation")
    selected = next(row for row in attempts if row["attempt"] == "strict_selected_scan_selected_market_replay")
    residual_by_target = {row["repair_target"]: row for row in residual_rows}

    assert patch["patched_or_attempted_rows"] == 2
    assert patch["entry_price_drift_rows"] == 0
    assert patch["pnl_drift_rows"] == 0
    assert patch["promotion_usable_replay"] is False
    assert "missing_actual_rows" in patch["blockers"]
    assert "extra_replay_rows" in patch["blockers"]
    assert selected["repair_attempt_verdict"] == "REGRESSES_ROW_FIDELITY"
    assert selected["replay_rows"] == 6
    assert summary["matched_drift_patch_removes_entry_drift"] is True
    assert summary["current_snapshot_repairs_make_replay_promotion_usable"] is False
    assert summary["deployable_now"] is False
    assert summary["residual_target_count"] == 3
    assert summary["diagnostic_patch_applied_target_count"] == 2
    assert summary["unresolved_future_exact_input_target_count"] == 1
    assert (
        summary["diagnostic_patch_applied_targets"]
        == "order_decision_reprice_fill_price;blocked_dedupe_scan_clock"
    )
    assert summary["unresolved_future_exact_input_targets"] == "skip_then_fill_sequence"
    assert summary["targets_repaired_to_promotion_usable_count"] == 0
    assert summary["all_field_ready_repairs_remain_diagnostic_only"] is True
    assert (
        residual_by_target["order_decision_reprice_fill_price"]["residual_target_status"]
        == "DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE"
    )
    assert residual_by_target["order_decision_reprice_fill_price"]["current_snapshot_repair_attempted"] is True
    assert (
        residual_by_target["blocked_dedupe_scan_clock"]["residual_target_status"]
        == "DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE"
    )
    assert "diagnostic_patch_not_independent_replay_evidence" in residual_by_target[
        "blocked_dedupe_scan_clock"
    ]["residual_blockers"]
    assert (
        residual_by_target["skip_then_fill_sequence"]["residual_target_status"]
        == "UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED"
    )
    assert residual_by_target["skip_then_fill_sequence"]["future_exact_input_required_for_target"] is True
    assert "exact_model_input_capture" in residual_by_target["skip_then_fill_sequence"]["residual_blockers"]
