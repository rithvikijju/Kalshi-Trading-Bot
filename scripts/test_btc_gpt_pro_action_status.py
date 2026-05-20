#!/usr/bin/env python3
"""Regression tests for GPT Pro action-status deployment checklist helpers."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.build_btc_gpt_pro_action_status import (
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


if __name__ == "__main__":
    unittest.main()
