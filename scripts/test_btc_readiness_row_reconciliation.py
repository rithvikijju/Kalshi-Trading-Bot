#!/usr/bin/env python3
"""Regression tests for row-reconciliation readiness blockers."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.check_btc_deployment_readiness import apply_execution_and_schema_gates, apply_settlement_basis_gates


class BtcReadinessRowReconciliationTests(unittest.TestCase):
    def test_running_shadow_predating_source_blocks_readiness(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "family": "BTC15M",
                    "candidate": "q1000_yes",
                    "source": "latest_live_replay_rest_official",
                    "production_ready": True,
                    "failure_reasons": "",
                }
            ]
        )
        execution = pd.DataFrame(
            [
                {
                    "source": "q1000_yes_live_replay",
                    "profile": "live_ws_replay",
                    "audit_status": "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION",
                    "rows": 120,
                    "official_rows": 120,
                    "required_field_complete_rate": 1.0,
                    "missing_or_empty_fields": "",
                    "blockers": "",
                },
                {
                    "source": "btc15m_q1000_yes_shadow",
                    "profile": "paper_ledger",
                    "audit_status": "PASS_LEDGER_EXECUTION_FIELDS",
                    "rows": 120,
                    "official_rows": 120,
                    "required_field_complete_rate": 1.0,
                    "missing_or_empty_fields": "",
                    "blockers": "",
                },
            ]
        )
        schema = pd.DataFrame(
            [
                {
                    "ledger": "btc15m_q1000_yes_shadow",
                    "preflight_status": "PASS",
                    "schema_column_count": 37,
                    "realism_columns_present": 12,
                    "realism_columns_missing": 0,
                    "restart_required_for_deployable_ledger": False,
                }
            ]
        )
        post_restart = pd.DataFrame(
            [
                {
                    "ledger": "btc15m_q1000_yes_shadow",
                    "candidate": "q1000_yes",
                    "gate_status": "PASS_POST_RESTART_COLLECTION_GATE_NOT_DEPLOYMENT",
                    "promotion_collection_ready": True,
                    "post_restart_official_rows": 120,
                    "min_post_restart_official_rows": 100,
                    "official_pnl": 4.0,
                    "proxy_official_mismatches": 0,
                    "realism_complete_rows": 120,
                    "failure_reasons": "",
                }
            ]
        )
        row_reconciliation = pd.DataFrame(
            [
                {
                    "candidate": "q1000_yes",
                    "row_reconciliation_pass": True,
                    "promotion_usable": True,
                    "matched_rows": 120,
                    "paper_rows": 120,
                    "replay_rows": 120,
                    "official_rows": 120,
                    "reconciliation_status": "PASS_PROMOTION_USABLE",
                    "blocking_reasons": "",
                }
            ]
        )
        frozen_policy = pd.DataFrame(
            [
                {
                    "ledger": "btc15m_q1000_yes_shadow",
                    "candidate": "q1000_yes",
                    "policy_parity_status": "PASS_FROZEN_POLICY_PARITY",
                    "policy_parity_pass": True,
                    "policy_parity_blockers": "",
                }
            ]
        )
        source_status = pd.DataFrame(
            [
                {
                    "name": "btc15m_q1000_yes_shadow",
                    "running": True,
                    "source_freshness_status": "RUNNING_SOURCE_STALE_RESTART_REQUIRED",
                    "source_latest_path": "scripts\\btc15m_f2_q1000_yes_shadow.py",
                    "source_latest_mtime_utc": "2026-05-18T12:38:45+00:00",
                    "process_created_at_utc": "2026-05-18T02:41:59Z",
                    "process_predates_latest_source": True,
                    "process_count": 2,
                    "duplicate_process_count": 1,
                    "process_hygiene_status": "DUPLICATE_TARGET_PROCESSES",
                }
            ]
        )
        verification_checklist = pd.DataFrame(
            [
                {"check": "target_process_identity", "passes_for_evidence_clock": True},
                {"check": "required_capture_sidecars_ready", "passes_for_evidence_clock": True},
                {"check": "all_target_shadows_running", "passes_for_evidence_clock": True},
                {"check": "active_ledger_schemas_ready", "passes_for_evidence_clock": True},
            ]
        )
        verification_targets = pd.DataFrame(
            [
                {
                    "candidate": "q1000_yes",
                    "ledger": "btc15m_q1000_yes_shadow",
                    "evidence_clock_status": "EVIDENCE_CLOCK_STARTED_WAIT_FOR_OFFICIAL_ROWS",
                    "running": True,
                    "process_identity_ready": True,
                    "process_identity_status": "PASS",
                    "capture_sidecar_ready": True,
                    "capture_sidecar_status": "PASS",
                    "active_schema_status": "PASS",
                }
            ]
        )

        out = apply_execution_and_schema_gates(
            summary,
            execution_df=execution,
            schema_df=schema,
            post_restart_df=post_restart,
            row_reconciliation_df=row_reconciliation,
            shadow_status_df=source_status,
            frozen_policy_df=frozen_policy,
            post_restart_verification_info={
                "restart_executed": True,
                "evidence_clock_ready": True,
                "collection_gate_ready": True,
            },
            post_restart_verification_checklist_df=verification_checklist,
            post_restart_target_df=verification_targets,
            shadow_status_info={
                "duplicate_target_process_count": 1,
                "unmanaged_matching_process_count": 1,
                "duplicate_target_names": "btc15m_q1000_yes_shadow",
            },
        )

        row = out.iloc[0]
        self.assertFalse(bool(row["production_ready"]))
        self.assertEqual(row["shadow_source_freshness_status"], "RUNNING_SOURCE_STALE_RESTART_REQUIRED")
        self.assertTrue(bool(row["shadow_process_predates_latest_source"]))
        self.assertEqual(row["shadow_duplicate_process_count"], 1)
        self.assertEqual(row["shadow_process_hygiene_status"], "DUPLICATE_TARGET_PROCESSES")
        self.assertEqual(row["process_hygiene_unmanaged_matching_process_count"], 1)
        self.assertIn("running_source_stale_restart_required", row["failure_reasons"])
        self.assertIn("current_process_predates_latest_source", row["failure_reasons"])
        self.assertIn("target_duplicate_processes_running", row["failure_reasons"])
        self.assertIn("process_hygiene_unmanaged_matching_processes", row["failure_reasons"])

    def test_focused_candidate_is_blocked_when_row_reconciliation_not_promotion_usable(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "family": "BTC15M",
                    "candidate": "q250_firstskip_qty500",
                    "source": "latest_live_replay_rest_official",
                    "production_ready": True,
                    "failure_reasons": "",
                }
            ]
        )
        execution = pd.DataFrame(
            [
                {
                    "source": "q250_firstskip_qty500_live_replay",
                    "profile": "live_ws_replay",
                    "audit_status": "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION",
                    "rows": 2,
                    "official_rows": 2,
                    "required_field_complete_rate": 1.0,
                    "missing_or_empty_fields": "",
                    "blockers": "",
                }
            ]
        )
        row_reconciliation = pd.DataFrame(
            [
                {
                    "candidate": "q250_firstskip_qty500",
                    "row_reconciliation_pass": True,
                    "promotion_usable": False,
                    "matched_rows": 2,
                    "paper_rows": 2,
                    "replay_rows": 2,
                    "official_rows": 2,
                    "reconciliation_status": "MATCHED_BUT_NOT_PROMOTION_USABLE",
                    "blocking_reasons": "paper_ledger_execution_fields_missing;too_few_official_rows_for_promotion",
                }
            ]
        )

        out = apply_execution_and_schema_gates(
            summary,
            execution_df=execution,
            schema_df=pd.DataFrame(),
            post_restart_df=pd.DataFrame(),
            row_reconciliation_df=row_reconciliation,
        )

        row = out.iloc[0]
        self.assertFalse(bool(row["production_ready"]))
        self.assertIn("paper_replay_row_reconciliation_not_promotion_usable", row["failure_reasons"])
        self.assertEqual(row["row_reconciliation_status"], "MATCHED_BUT_NOT_PROMOTION_USABLE")
        self.assertEqual(row["row_reconciliation_matched_rows"], 2)

    def test_post_restart_verifier_blocks_when_evidence_clock_not_ready(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "family": "BTC15M",
                    "candidate": "q250_firstskip_qty500_yes",
                    "source": "shadow_official_ledger",
                    "production_ready": True,
                    "failure_reasons": "",
                }
            ]
        )
        execution = pd.DataFrame(
            [
                {
                    "source": "btc15m_q250_qty500_firstskip_yes_shadow",
                    "profile": "paper_ledger",
                    "audit_status": "PASS_LEDGER_EXECUTION_FIELDS",
                    "rows": 150,
                    "official_rows": 120,
                    "required_field_complete_rate": 1.0,
                    "missing_or_empty_fields": "",
                    "blockers": "",
                }
            ]
        )
        schema = pd.DataFrame(
            [
                {
                    "ledger": "btc15m_q250_qty500_firstskip_yes_shadow",
                    "preflight_status": "PASS",
                    "schema_column_count": 37,
                    "realism_columns_present": 12,
                    "realism_columns_missing": 0,
                    "restart_required_for_deployable_ledger": False,
                }
            ]
        )
        post_restart = pd.DataFrame(
            [
                {
                    "ledger": "btc15m_q250_qty500_firstskip_yes_shadow",
                    "candidate": "q250_firstskip_qty500_yes",
                    "gate_status": "PASS_POST_RESTART_COLLECTION_GATE_NOT_DEPLOYMENT",
                    "promotion_collection_ready": True,
                    "post_restart_official_rows": 120,
                    "min_post_restart_official_rows": 100,
                    "official_pnl": 3.0,
                    "proxy_official_mismatches": 0,
                    "realism_complete_rows": 120,
                    "failure_reasons": "",
                }
            ]
        )
        verification_checklist = pd.DataFrame(
            [
                {"check": "target_process_identity", "passes_for_evidence_clock": False},
                {"check": "required_capture_sidecars_ready", "passes_for_evidence_clock": False},
                {"check": "all_target_shadows_running", "passes_for_evidence_clock": True},
                {"check": "active_ledger_schemas_ready", "passes_for_evidence_clock": True},
            ]
        )
        verification_targets = pd.DataFrame(
            [
                {
                    "candidate": "q250_firstskip_qty500_yes",
                    "ledger": "btc15m_q250_qty500_firstskip_yes_shadow",
                    "evidence_clock_status": "FAIL_PROCESS_IDENTITY",
                    "running": True,
                    "process_identity_ready": False,
                    "process_identity_status": "current_pid_does_not_match_restart_result",
                    "capture_sidecar_ready": False,
                    "capture_sidecar_status": "capture_sidecar_stale_before_restart",
                    "active_schema_status": "PASS",
                }
            ]
        )

        out = apply_execution_and_schema_gates(
            summary,
            execution_df=execution,
            schema_df=schema,
            post_restart_df=post_restart,
            row_reconciliation_df=pd.DataFrame(),
            post_restart_verification_info={
                "restart_executed": True,
                "evidence_clock_ready": False,
                "collection_gate_ready": True,
            },
            post_restart_verification_checklist_df=verification_checklist,
            post_restart_target_df=verification_targets,
        )

        row = out.iloc[0]
        self.assertFalse(bool(row["production_ready"]))
        self.assertIn("post_restart_evidence_clock_not_ready", row["failure_reasons"])
        self.assertIn("post_restart_process_identity_not_ready", row["failure_reasons"])
        self.assertIn("post_restart_capture_sidecars_not_ready", row["failure_reasons"])
        self.assertIn("post_restart_target_process_identity_not_ready", row["failure_reasons"])
        self.assertEqual(row["post_restart_target_evidence_clock_status"], "FAIL_PROCESS_IDENTITY")

    def test_q250_yes_focused_replay_is_blocked_by_missing_paper_shadow_match(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "family": "BTC15M",
                    "candidate": "q250_firstskip_qty500_yes",
                    "source": "latest_live_replay_rest_official",
                    "production_ready": True,
                    "failure_reasons": "",
                }
            ]
        )
        execution = pd.DataFrame(
            [
                {
                    "source": "q250_firstskip_qty500_yes_live_replay",
                    "profile": "live_ws_replay",
                    "audit_status": "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION",
                    "rows": 1,
                    "official_rows": 1,
                    "required_field_complete_rate": 1.0,
                    "missing_or_empty_fields": "",
                    "blockers": "",
                }
            ]
        )
        row_reconciliation = pd.DataFrame(
            [
                {
                    "candidate": "q250_firstskip_qty500_yes",
                    "row_reconciliation_pass": False,
                    "promotion_usable": False,
                    "matched_rows": 0,
                    "paper_rows": 0,
                    "replay_rows": 1,
                    "official_rows": 1,
                    "reconciliation_status": "REPLAY_ROWS_WITHOUT_PAPER_SHADOW",
                    "blocking_reasons": "no_paper_shadow_rows_since_freeze;replay_rows_without_paper_match;too_few_official_rows_for_promotion",
                }
            ]
        )

        out = apply_execution_and_schema_gates(
            summary,
            execution_df=execution,
            schema_df=pd.DataFrame(),
            post_restart_df=pd.DataFrame(),
            row_reconciliation_df=row_reconciliation,
        )

        row = out.iloc[0]
        self.assertFalse(bool(row["production_ready"]))
        self.assertEqual(row["replay_execution_status"], "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION")
        self.assertEqual(row["row_reconciliation_status"], "REPLAY_ROWS_WITHOUT_PAPER_SHADOW")
        self.assertIn("paper_replay_row_reconciliation_not_passing", row["failure_reasons"])
        self.assertIn("row_reconciliation_replay_rows_without_paper_match", row["failure_reasons"])

    def test_settlement_basis_gate_blocks_q250_no_side_basis_risk(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "family": "BTC15M",
                    "candidate": "q250_firstskip_qty500",
                    "source": "latest_live_replay_rest_official",
                    "production_ready": True,
                    "failure_reasons": "",
                }
            ]
        )
        basis_gates = pd.DataFrame(
            [
                {
                    "family": "BTC15M",
                    "candidate": "q250_firstskip_qty500",
                    "side": "no",
                    "basis_gate_pass": False,
                    "basis_gate_reasons": "proxy_official_mismatch_rate_high;official_minus_proxy_pnl_delta_bad",
                    "promotion_min_official_rows": 100,
                    "official_rows": 16,
                    "mismatch_rate": 0.1875,
                    "adverse_mismatches": 3,
                    "pnl_delta_per_both_trade_2c": -0.1875,
                    "abs_basis_p95_usd": 24.545,
                    "max_abs_basis_usd": 31.16,
                },
                {
                    "family": "BTC15M",
                    "candidate": "q250_firstskip_qty500",
                    "side": "yes",
                    "basis_gate_pass": False,
                    "basis_gate_reasons": "too_few_official_rows",
                    "promotion_min_official_rows": 100,
                    "official_rows": 8,
                    "mismatch_rate": 0.0,
                    "adverse_mismatches": 0,
                    "pnl_delta_per_both_trade_2c": 0.0,
                    "abs_basis_p95_usd": 17.6685,
                    "max_abs_basis_usd": 18.89,
                },
            ]
        )

        out = apply_settlement_basis_gates(summary, basis_gates)

        row = out.iloc[0]
        self.assertFalse(bool(row["production_ready"]))
        self.assertEqual(row["settlement_basis_gate_status"], "FAIL")
        self.assertEqual(row["settlement_basis_official_rows"], 24.0)
        self.assertIn("settlement_basis_gate_failed", row["failure_reasons"])
        self.assertIn("settlement_basis_proxy_official_mismatch_rate_high", row["failure_reasons"])
        self.assertIn("settlement_basis_too_few_official_rows", row["failure_reasons"])


if __name__ == "__main__":
    unittest.main()
