#!/usr/bin/env python3
"""Regression tests for GPT Pro action-status deployment checklist helpers."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.build_btc_gpt_pro_action_status import (
    btc1h_candidate_promotion_deficit_gate_summary,
    btc1h_remote_provenance_gate_summary,
    candidate_row,
    execution_realism_gate_summary,
    process_hygiene_gate_summary,
    same_path_text,
    side_semantics_gate_summary,
)


def replay_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "profile": "live_ws_replay",
        "audit_status": "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION",
        "rows": 3,
        "fee_present_rate": 1.0,
        "fee_nonnegative_rate": 1.0,
        "entry_matches_side_ask_rate": 1.0,
        "visible_qty_matches_side_ask_qty_rate": 1.0,
        "visible_qty_ge_candidate_min_rate": 1.0,
        "one_trade_per_event": True,
        "blockers": "",
    }
    row.update(overrides)
    return row


def ledger_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "profile": "paper_shadow_ledger",
        "audit_status": "PASS_LEDGER_EXECUTION_FIELDS_NOT_PROMOTION",
        "rows": 2,
        "fee_present_rate": 1.0,
        "fee_nonnegative_rate": 1.0,
        "entry_matches_side_ask_rate": 1.0,
        "quote_age_le_limit_rate": 1.0,
        "top_visible_qty_ge_contracts_rate": 1.0,
        "one_trade_per_event": True,
        "blockers": "",
    }
    row.update(overrides)
    return row


class BtcGptProActionStatusTests(unittest.TestCase):
    def test_same_path_text_normalizes_equivalent_restart_paths(self) -> None:
        self.assertTrue(same_path_text(r"C:\tmp\restart", r"C:\tmp\restart\\"))
        self.assertFalse(same_path_text(r"C:\tmp\restart_a", r"C:\tmp\restart_b"))

    def test_execution_realism_gate_passes_only_when_replay_and_ledger_are_clean(self) -> None:
        summary = execution_realism_gate_summary(pd.DataFrame([replay_row(), ledger_row()]))

        self.assertTrue(summary["passes"])
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        self.assertIn("replay_ready=1/1", summary["evidence"])
        self.assertIn("ledger_ready=1/1", summary["evidence"])

    def test_execution_realism_gate_blocks_missing_ledger_top_book_fields(self) -> None:
        summary = execution_realism_gate_summary(
            pd.DataFrame(
                [
                    replay_row(),
                    ledger_row(
                        audit_status="FAIL_LEDGER_EXECUTION_FIELDS",
                        top_visible_qty_ge_contracts_rate=0.0,
                        blockers="top_visible_qty_missing_or_below_contracts",
                    ),
                ]
            )
        )

        self.assertFalse(summary["passes"])
        self.assertEqual(summary["status"], "BLOCKS_DEPLOYMENT")
        self.assertIn("ledger_ready=0/1", summary["evidence"])
        self.assertEqual(summary["top_book_or_fok_issue_rows"], 1)

    def test_execution_realism_gate_does_not_call_empty_ledger_rows_fee_failures(self) -> None:
        summary = execution_realism_gate_summary(
            pd.DataFrame(
                [
                    replay_row(),
                    ledger_row(
                        audit_status="NO_FILLED_LEDGER_ROWS",
                        rows=0,
                        fee_present_rate=0.0,
                        fee_nonnegative_rate=0.0,
                        blockers="no_filled_ledger_rows",
                    ),
                ]
            )
        )

        self.assertFalse(summary["passes"])
        self.assertEqual(summary["fee_issue_rows"], 0)
        self.assertEqual(summary["no_filled_ledger_rows"], 1)

    def test_side_semantics_gate_passes_clean_yes_subset(self) -> None:
        summary = side_semantics_gate_summary(
            pd.DataFrame(
                [
                    {
                        "target_side": "yes",
                        "comparison": "full_old_replay",
                        "audit_status": "SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST",
                        "config_comparable": True,
                        "side_first_extra_events": 0,
                        "global_target_side_missing_from_side_replay": 0,
                        "global_first_opposite_side_events": 18,
                    },
                    {
                        "target_side": "yes",
                        "comparison": "postfreeze_replay",
                        "audit_status": "SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST",
                        "config_comparable": True,
                        "side_first_extra_events": 0,
                        "global_target_side_missing_from_side_replay": 0,
                        "global_first_opposite_side_events": 1,
                    },
                ]
            )
        )

        self.assertTrue(summary["passes"])
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        self.assertIn("side_first_extra_events=0", summary["evidence"])

    def test_side_semantics_gate_blocks_extra_side_filtered_events(self) -> None:
        summary = side_semantics_gate_summary(
            pd.DataFrame(
                [
                    {
                        "target_side": "yes",
                        "comparison": "full_old_replay",
                        "audit_status": "SIDE_FILTER_WAITS_PAST_GLOBAL_FIRST",
                        "config_comparable": True,
                        "side_first_extra_events": 2,
                        "global_target_side_missing_from_side_replay": 0,
                        "global_first_opposite_side_events": 18,
                    },
                    {
                        "target_side": "yes",
                        "comparison": "postfreeze_replay",
                        "audit_status": "SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST",
                        "config_comparable": True,
                        "side_first_extra_events": 0,
                        "global_target_side_missing_from_side_replay": 0,
                        "global_first_opposite_side_events": 1,
                    },
                ]
            )
        )

        self.assertFalse(summary["passes"])
        self.assertEqual(summary["status"], "BLOCKS_Q250_YES_PROMOTION")
        self.assertEqual(summary["extra_events"], 2)

    def test_process_hygiene_gate_blocks_duplicate_or_unmanaged_processes(self) -> None:
        summary = process_hygiene_gate_summary(
            {
                "duplicate_target_process_count": 2,
                "unmanaged_matching_process_count": 1,
                "duplicate_target_names": "btc15m_q250_qty500_firstskip_shadow",
                "created_at_utc": "2026-05-18T21:17:45+00:00",
            }
        )

        self.assertFalse(summary["passes"])
        self.assertEqual(summary["status"], "BLOCKS_DEPLOYMENT")
        self.assertIn("duplicate_target_processes=2", summary["evidence"])
        self.assertIn("unmanaged_matching_processes=1", summary["evidence"])

    def test_process_hygiene_gate_passes_clean_process_set(self) -> None:
        summary = process_hygiene_gate_summary(
            {"duplicate_target_process_count": 0, "unmanaged_matching_process_count": 0}
        )

        self.assertTrue(summary["passes"])
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")

    def test_btc1h_remote_provenance_gate_blocks_stale_remote_status(self) -> None:
        summary = btc1h_remote_provenance_gate_summary(
            {
                "btc1h_remote_provenance_verdict": "REMOTE_STATUS_STALE_OR_UNAVAILABLE",
                "btc1h_remote_status_age_minutes": 289.4,
                "btc1h_remote_status_fresh": False,
                "btc1h_remote_official_rows": 11,
                "btc1h_remote_official_pnl": 0.5,
                "btc1h_remote_proxy_official_mismatches": 1,
                "btc1h_clean_clock_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "btc1h_clean_clock_ready": False,
                "blocking_reasons": "btc1h_remote_status_stale_or_unavailable;btc1h_clean_evidence_clock_not_ready",
            }
        )

        self.assertFalse(summary["passes"])
        self.assertEqual(summary["status"], "BLOCKS_BTC1H_PROMOTION")
        self.assertEqual(summary["verdict"], "REMOTE_STATUS_STALE_OR_UNAVAILABLE")
        self.assertEqual(summary["official_rows"], 11)
        self.assertIn("remote_proxy_official_mismatches=1", summary["evidence"])
        self.assertIn("clean_clock_ready=False", summary["evidence"])

    def test_btc1h_candidate_promotion_deficit_blocks_current_artifacts(self) -> None:
        summary = btc1h_candidate_promotion_deficit_gate_summary(
            {
                "objective_complete": "False",
                "candidates_current_artifacts_can_make_near_deployable": 0,
                "candidates_with_no_promotion_countable_data": 4,
                "clean_official_row_deficit": 50,
                "active_proxy_official_mismatch_rate_excess": 0.0709,
                "active_replay_exact_match_rate_deficit": 0.181818,
                "active_execution_field_complete_rate_deficit": 1.0,
                "faithful_replay_missing_required_field_count": 17,
                "faithful_replay_current_artifacts_can_support": "False",
            }
        )

        self.assertFalse(summary["passes"])
        self.assertEqual(summary["status"], "BLOCKS_BTC1H_PROMOTION")
        self.assertEqual(summary["clean_row_deficit"], 50)
        self.assertIn("active_mismatch_excess=0.0709", summary["evidence"])
        self.assertIn("faithful_missing_fields=17", summary["evidence"])

    def test_candidate_row_prefers_current_shadow_pids_over_stale_restart_packet(self) -> None:
        row = candidate_row(
            candidate="q250_firstskip_qty500",
            pro_rank="2",
            pro_instruction="control",
            restart_row={
                "current_pids": "7052",
                "authorization_packet_status": "READY_FOR_USER_AUTHORIZATION",
                "post_restart_official_rows": 0,
                "min_post_restart_official_rows": 100,
            },
            kill_row={},
            shadow_row={
                "running": True,
                "pids": "7052,10524",
                "process_count": 2,
                "duplicate_process_count": 1,
                "process_hygiene_status": "DUPLICATE_TARGET_PROCESSES",
                "source_freshness_status": "RUNNING_SOURCE_CURRENT",
            },
            official_since={},
            freeze_row={},
            starvation_row={},
            reconciliation_row={},
            policy_row={"policy_parity_pass": True},
            evidence_clock_ready=False,
        )

        self.assertEqual(row["current_pids"], "7052,10524")
        self.assertEqual(row["duplicate_process_count"], 1)

    def test_candidate_row_surfaces_btc1h_remote_provenance_status(self) -> None:
        row = candidate_row(
            candidate="btc1h_high_conf80_entry70_no_chase",
            pro_rank="observe_only",
            pro_instruction="observe",
            restart_row={},
            kill_row={},
            shadow_row={
                "running": False,
                "process_count": 0,
                "source_freshness_status": "NOT_RUNNING_OR_PROCESS_TIME_MISSING",
            },
            official_since={},
            freeze_row={},
            starvation_row={},
            reconciliation_row={},
            policy_row={"policy_parity_pass": True},
            evidence_clock_ready=False,
            consistency_row={
                "agreement_status": "btc1h_remote_status_stale_or_unavailable",
                "blocking_reasons": "btc1h_remote_status_stale_or_unavailable",
                "btc1h_remote_provenance_verdict": "REMOTE_STATUS_STALE_OR_UNAVAILABLE",
                "btc1h_remote_status_age_minutes": 289.4,
                "btc1h_remote_status_fresh": False,
                "btc1h_remote_official_rows": 11,
                "btc1h_remote_official_pnl": 0.5,
                "btc1h_remote_proxy_official_mismatches": 1,
                "btc1h_clean_clock_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "btc1h_clean_clock_ready": False,
            },
        )

        self.assertEqual(row["current_status"], "BTC1H_REMOTE_STATUS_STALE_OR_UNAVAILABLE")
        self.assertEqual(row["forward_consistency_status"], "btc1h_remote_status_stale_or_unavailable")
        self.assertEqual(row["btc1h_remote_official_rows"], 11)
        self.assertEqual(row["btc1h_remote_official_pnl"], 0.5)
        self.assertFalse(row["btc1h_clean_clock_ready"])


if __name__ == "__main__":
    unittest.main()
