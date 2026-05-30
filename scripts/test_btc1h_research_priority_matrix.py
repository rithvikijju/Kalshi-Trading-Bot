#!/usr/bin/env python3
"""Tests for BTC1H research priority matrix."""

from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from pathlib import Path

from scripts.build_btc1h_research_priority_matrix import build_matrix


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def args(tmp: Path) -> argparse.Namespace:
    return argparse.Namespace(
        out_dir=tmp / "out",
        multi_holdout_summary=tmp / "multi.csv",
        variant_basis_stress_ranking=tmp / "basis.csv",
        promotion_gap_summary=tmp / "gap_summary.csv",
        promotion_gap_matrix=tmp / "gap_matrix.csv",
        replay_repair_attempt_summary=tmp / "repair_summary.csv",
        replay_overlap_summary=tmp / "overlap.csv",
        entry59_floor_filter_summary=tmp / "floor.csv",
        no_chase_extra_row_summary=tmp / "no_chase_extra.csv",
        holdout_independence_summary=tmp / "independence.csv",
        min_forward_official_rows=50,
        max_proxy_official_mismatch_rate=0.02,
    )


class Btc1hResearchPriorityMatrixTests(unittest.TestCase):
    def test_separates_forward_control_from_causal_replay_runner_up(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            write_csv(
                tmp / "multi.csv",
                [
                    {
                        "variant": "high_conf_80_entry70_no_chase",
                        "research_status": "active_forward_candidate",
                        "all_positive_holdouts": 14,
                        "all_holdouts": 14,
                        "ws_positive_cadences": 6,
                        "ws_cadences": 6,
                        "forward_official_rows": 11,
                        "forward_official_pnl": 0.5,
                        "forward_official_mismatch_rate": 0.0909,
                        "replay_ledger_promotion_usable": "False",
                        "historical_pnl_sum": 21.39,
                        "historical_trades": 159,
                        "deploy_blockers": "too_few_forward_official_rows",
                    },
                    {
                        "variant": "high_conf_80_entry59_70_no_chase",
                        "research_status": "historical_only_promising",
                        "all_positive_holdouts": 12,
                        "all_holdouts": 13,
                        "negative_holdouts": "H2b",
                        "ws_positive_cadences": 6,
                        "ws_cadences": 6,
                        "forward_official_rows": 0,
                        "forward_official_pnl": 0,
                        "replay_ledger_promotion_usable": "False",
                        "historical_pnl_sum": 19.51,
                        "historical_trades": 141,
                    },
                    {
                        "variant": "high_conf_80_no_chase",
                        "research_status": "research_watch_or_reject",
                        "all_positive_holdouts": 13,
                        "all_holdouts": 14,
                        "negative_holdouts": "H4_stride1",
                        "ws_positive_cadences": 5,
                        "ws_cadences": 6,
                        "forward_official_rows": 0,
                        "forward_official_pnl": 0,
                        "replay_ledger_promotion_usable": "False",
                        "historical_pnl_sum": 20.89,
                        "historical_trades": 191,
                    },
                    {
                        "variant": "high_conf_80",
                        "research_status": "historical_only_promising",
                        "all_positive_holdouts": 8,
                        "all_holdouts": 9,
                        "ws_positive_cadences": 6,
                        "ws_cadences": 6,
                        "forward_official_rows": 0,
                        "forward_official_pnl": 0,
                        "replay_ledger_promotion_usable": "False",
                        "historical_pnl_sum": 14.94,
                        "historical_trades": 174,
                    },
                ],
            )
            write_csv(
                tmp / "basis.csv",
                [
                    {
                        "variant": "high_conf_80_no_chase",
                        "max_basis_shock_with_positive_unique_pnl": 87.42,
                        "unique_pnl_at_p95_or_max": 1.6,
                        "positive_holdouts_at_p95_or_max": 8,
                        "holdouts_at_p95_or_max": 12,
                    },
                    {
                        "variant": "high_conf_80_entry59_70_no_chase",
                        "max_basis_shock_with_positive_unique_pnl": 87.42,
                        "unique_pnl_at_p95_or_max": 1.41,
                        "positive_holdouts_at_p95_or_max": 8,
                        "holdouts_at_p95_or_max": 9,
                    },
                    {
                        "variant": "high_conf_80_entry70_no_chase",
                        "max_basis_shock_with_positive_unique_pnl": 75.0,
                        "unique_pnl_at_p95_or_max": -0.22,
                        "positive_holdouts_at_p95_or_max": 4,
                        "holdouts_at_p95_or_max": 9,
                    },
                    {
                        "variant": "high_conf_80",
                        "max_basis_shock_with_positive_unique_pnl": 50.0,
                        "unique_pnl_at_p95_or_max": -2.11,
                        "positive_holdouts_at_p95_or_max": 2,
                        "holdouts_at_p95_or_max": 5,
                    },
                ],
            )
            write_csv(tmp / "gap_summary.csv", [{"deployable_now": "False", "blocked_gate_count": 9}])
            write_csv(
                tmp / "gap_matrix.csv",
                [{"gate_id": "clean_forward_sample_size", "status": "BLOCKED", "blocker": "too_few_clean_post_restart_official_rows"}],
            )
            write_csv(
                tmp / "repair_summary.csv",
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
            write_csv(
                tmp / "overlap.csv",
                [
                    {
                        "challenger_variant": "high_conf_80_entry59_70_no_chase",
                        "status": "EXACT_ROW_SET_MATCH",
                        "exact_row_set_match": "True",
                        "independent_challenger_rows": 0,
                    }
                ],
            )
            write_csv(
                tmp / "floor.csv",
                [
                    {
                        "challenger_variant": "high_conf_80_entry59_70_no_chase",
                        "status": "ENTRY59_STRICT_SUBSET_FILTER",
                        "total_challenger_market_side_only_rows": 0,
                        "total_challenger_exact_only_rows": 0,
                        "total_base_exact_only_rows": 18,
                        "total_deleted_low_entry_base_rows": 7,
                        "total_deleted_low_entry_base_pnl": -0.42,
                    }
                ],
            )
            write_csv(
                tmp / "no_chase_extra.csv",
                [
                    {
                        "no_chase_variant": "high_conf_80_no_chase",
                        "entry70_variant": "high_conf_80_entry70_no_chase",
                        "status": "NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING",
                        "total_extra_no_chase_exact_rows": 50,
                        "total_extra_no_chase_market_side_rows": 38,
                        "total_extra_no_chase_pnl": -1.4,
                        "total_extra_gt70_rows": 50,
                        "total_extra_gt70_pnl": -1.4,
                        "live_ws_negative_extra_label_count": 4,
                        "live_ws_stride1_extra_gt70_rows": 11,
                        "live_ws_stride1_extra_gt70_pnl": -1.41,
                    }
                ],
            )
            write_csv(
                tmp / "independence.csv",
                [
                    {
                        "panel": "historical_all_rows",
                        "status": "WEAK_OR_CONCENTRATED_RESEARCH",
                        "rows": 159,
                        "unique_market_side_rows": 69,
                        "duplicate_market_side_rows": 90,
                        "unique_market_side_pnl": 5.82,
                        "leave_one_event_min_pnl": 19.41,
                        "blockers": "duplicate_market_side_rows",
                    },
                    {
                        "panel": "historical_unique_market_side_rows",
                        "status": "PASS_RESEARCH_INDEPENDENCE",
                        "rows": 69,
                        "unique_market_side_rows": 69,
                        "duplicate_market_side_rows": 0,
                        "unique_market_side_pnl": 5.82,
                    },
                    {
                        "panel": "forward_official_stale_rows",
                        "status": "DIAGNOSTIC_ONLY_STALE_OFFICIAL",
                        "rows": 11,
                        "unique_market_side_rows": 11,
                        "leave_one_event_min_pnl": 0.09,
                    },
                ],
            )

            rows, summary = build_matrix(args(tmp))
            by_variant = {row["variant"]: row for row in rows}

            self.assertEqual(summary["active_forward_control"], "high_conf_80_entry70_no_chase")
            self.assertEqual(summary["top_causal_replay_runner_up"], "high_conf_80_entry59_70_no_chase")
            self.assertEqual(summary["top_basis_stress_variant"], "high_conf_80_no_chase")
            self.assertEqual(summary["entry59_replay_overlap_status"], "EXACT_ROW_SET_MATCH")
            self.assertEqual(summary["entry59_floor_filter_status"], "ENTRY59_STRICT_SUBSET_FILTER")
            self.assertEqual(summary["entry59_independent_market_side_rows_vs_active"], "0")
            self.assertEqual(summary["no_chase_extra_row_status"], "NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING")
            self.assertEqual(summary["no_chase_stride1_extra_gt70_pnl"], "-1.41")
            self.assertEqual(summary["active_holdout_independence_status"], "WEAK_OR_CONCENTRATED_RESEARCH")
            self.assertEqual(summary["active_historical_unique_market_side_rows"], "69")
            self.assertEqual(summary["active_historical_duplicate_market_side_rows"], "90")
            self.assertEqual(summary["active_historical_unique_market_side_pnl"], "5.82")
            self.assertEqual(summary["active_forward_official_leave_one_event_min_pnl"], "0.09")
            self.assertEqual(summary["promotion_gap_blocked_gate_count"], "9")
            self.assertEqual(
                summary["replay_repair_best_current_snapshot_attempt"],
                "matched_drift_decision_fill_price_patch_simulation",
            )
            self.assertEqual(
                summary["replay_repair_strict_selected_scan_attempt_verdict"],
                "REGRESSES_ROW_FIDELITY",
            )
            self.assertEqual(summary["replay_repair_current_snapshot_repairs_promotion_usable"], False)
            self.assertEqual(summary["replay_repair_residual_target_count"], "4")
            self.assertEqual(summary["replay_repair_diagnostic_patch_applied_target_count"], "2")
            self.assertEqual(summary["replay_repair_unresolved_future_exact_input_target_count"], "2")
            self.assertEqual(
                summary["replay_repair_diagnostic_patch_applied_targets"],
                "order_decision_reprice_fill_price;blocked_dedupe_scan_clock",
            )
            self.assertEqual(
                summary["replay_repair_unresolved_future_exact_input_targets"],
                "skip_then_fill_sequence;same_event_market_selection",
            )
            self.assertEqual(summary["replay_repair_targets_repaired_to_promotion_usable_count"], "0")
            self.assertEqual(summary["replay_repair_all_field_ready_repairs_remain_diagnostic_only"], True)
            self.assertIn(
                "current_snapshot_repairs_not_promotion_usable",
                summary["replay_repair_active_blockers"],
            )
            self.assertIn(
                "selected_scan_attempt_regresses_row_fidelity",
                summary["replay_repair_active_blockers"],
            )
            self.assertIn(
                "unresolved_future_exact_input_replay_targets",
                summary["replay_repair_active_blockers"],
            )
            self.assertIn(
                "field_ready_replay_repairs_diagnostic_only",
                summary["replay_repair_active_blockers"],
            )
            self.assertIn(
                "zero_replay_targets_repaired_to_promotion_usable",
                summary["replay_repair_active_blockers"],
            )
            self.assertEqual(by_variant["high_conf_80_entry70_no_chase"]["forward_control_rank"], 1)
            self.assertEqual(
                by_variant["high_conf_80_entry70_no_chase"]["holdout_independence_status"],
                "WEAK_OR_CONCENTRATED_RESEARCH",
            )
            self.assertEqual(by_variant["high_conf_80_entry70_no_chase"]["historical_duplicate_market_side_rows"], 90)
            self.assertEqual(by_variant["high_conf_80_entry70_no_chase"]["historical_unique_market_side_pnl"], 5.82)
            self.assertEqual(
                by_variant["high_conf_80_entry70_no_chase"]["replay_repair_strict_selected_scan_attempt_verdict"],
                "REGRESSES_ROW_FIDELITY",
            )
            self.assertEqual(by_variant["high_conf_80_entry70_no_chase"]["replay_repair_residual_target_count"], 4)
            self.assertEqual(
                by_variant["high_conf_80_entry70_no_chase"][
                    "replay_repair_diagnostic_patch_applied_targets"
                ],
                "order_decision_reprice_fill_price;blocked_dedupe_scan_clock",
            )
            self.assertEqual(
                by_variant["high_conf_80_entry70_no_chase"][
                    "replay_repair_unresolved_future_exact_input_targets"
                ],
                "skip_then_fill_sequence;same_event_market_selection",
            )
            self.assertEqual(
                by_variant["high_conf_80_entry70_no_chase"][
                    "replay_repair_targets_repaired_to_promotion_usable_count"
                ],
                0,
            )
            self.assertEqual(
                by_variant["high_conf_80_entry70_no_chase"][
                    "replay_repair_all_field_ready_repairs_remain_diagnostic_only"
                ],
                True,
            )
            self.assertIn(
                "current_snapshot_repairs_not_promotion_usable",
                by_variant["high_conf_80_entry70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "unresolved_future_exact_input_replay_targets",
                by_variant["high_conf_80_entry70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "field_ready_replay_repairs_diagnostic_only",
                by_variant["high_conf_80_entry70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "zero_replay_targets_repaired_to_promotion_usable",
                by_variant["high_conf_80_entry70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "active_historical_holdouts_have_duplicate_market_side_rows",
                by_variant["high_conf_80_entry70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "active_historical_independence_weak_or_concentrated",
                by_variant["high_conf_80_entry70_no_chase"]["deployment_blockers"],
            )
            self.assertEqual(by_variant["high_conf_80_entry59_70_no_chase"]["research_replay_rank"], 1)
            self.assertEqual(
                by_variant["high_conf_80_entry59_70_no_chase"]["classification"],
                "top_replay_runner_up_but_not_independent_on_snapshot",
            )
            self.assertEqual(
                by_variant["high_conf_80_entry59_70_no_chase"]["entry59_total_challenger_market_side_only_rows"],
                0,
            )
            self.assertEqual(by_variant["high_conf_80_no_chase"]["research_replay_rank"], 2)
            self.assertEqual(by_variant["high_conf_80_no_chase"]["no_chase_total_extra_rows"], 50)
            self.assertIn(
                "broad_no_chase_extra_rows_damage_live_ws_cadence",
                by_variant["high_conf_80_no_chase"]["deployment_blockers"],
            )
            self.assertFalse(by_variant["high_conf_80_entry59_70_no_chase"]["deployable_now"])
            self.assertIn(
                "historical_only_no_clean_forward_shadow",
                by_variant["high_conf_80_entry59_70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "no_independent_live_ws_replay_rows_vs_active_on_snapshot",
                by_variant["high_conf_80_entry59_70_no_chase"]["deployment_blockers"],
            )
            self.assertIn(
                "entry_floor_filter_no_independent_market_side_rows_in_compared_artifacts",
                by_variant["high_conf_80_entry59_70_no_chase"]["deployment_blockers"],
            )


if __name__ == "__main__":
    unittest.main()
