#!/usr/bin/env python3
"""Regression tests for BTC forward consistency process-hygiene blockers."""

from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from scripts.build_btc_forward_consistency_audit import (
    CandidateSpec,
    agreement_status,
    build_row,
    shadow_metrics,
    shadow_rows,
)


class BtcForwardConsistencyAuditTests(unittest.TestCase):
    def test_duplicate_target_processes_block_agreement_before_stale_source_logic(self) -> None:
        spec = CandidateSpec(
            family="BTC15M",
            candidate="q250_firstskip_qty500",
            shadow_name="btc15m_q250_qty500_firstskip_shadow",
            readiness_candidate="q250_firstskip_qty500",
            readiness_source="latest_live_replay_rest_official",
            basis_candidate="q250_firstskip_qty500",
            basis_side=None,
            min_official_rows=100,
        )
        shadows = pd.DataFrame(
            [
                {
                    "name": "btc15m_q250_qty500_firstskip_shadow",
                    "running": True,
                    "process_count": 2,
                    "duplicate_process_count": 1,
                    "process_hygiene_status": "DUPLICATE_TARGET_PROCESSES",
                    "source_freshness_status": "RUNNING_SOURCE_STALE_RESTART_REQUIRED",
                    "process_predates_latest_source": True,
                }
            ]
        )

        row = shadow_metrics(shadows, spec)

        self.assertEqual(row["shadow_duplicate_process_count"], 1)
        self.assertEqual(row["shadow_process_hygiene_status"], "DUPLICATE_TARGET_PROCESSES")
        self.assertEqual(agreement_status(row, spec), "duplicate_target_processes_running")

    def test_shadow_rows_merges_process_hygiene_fields_from_shadow_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            forward_dir = root / "forward"
            status_dir = root / "status"
            forward_dir.mkdir()
            status_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "name": "btc15m_q250_qty500_firstskip_shadow",
                        "running": True,
                        "source_freshness_status": "RUNNING_SOURCE_STALE_RESTART_REQUIRED",
                    }
                ]
            ).to_csv(forward_dir / "forward_shadow_summary.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "name": "btc15m_q250_qty500_firstskip_shadow",
                        "process_count": 2,
                        "duplicate_process_count": 1,
                        "process_hygiene_status": "DUPLICATE_TARGET_PROCESSES",
                    }
                ]
            ).to_csv(status_dir / "shadow_status.csv", index=False)

            merged = shadow_rows(Namespace(forward_report_dir=forward_dir, shadow_status_dir=status_dir))

            self.assertEqual(int(merged.iloc[0]["duplicate_process_count"]), 1)
            self.assertEqual(merged.iloc[0]["process_hygiene_status"], "DUPLICATE_TARGET_PROCESSES")

    def test_btc1h_remote_provenance_survives_local_not_running_status(self) -> None:
        spec = CandidateSpec(
            family="BTC1H",
            candidate="btc1h_high_conf80_entry70_no_chase",
            shadow_name="btc1h_high_conf80_entry70_no_chase_shadow",
            readiness_candidate="btc1h_high_conf80_entry70_no_chase_shadow:since",
            readiness_source="shadow_official_ledger",
            basis_candidate="btc1h_high_conf80_entry70_no_chase_shadow",
            basis_side="no",
            min_official_rows=50,
        )
        args = Namespace(freeze_utc="2026-05-18T04:17:44Z")
        shadows = pd.DataFrame(
            [
                {
                    "name": "btc1h_high_conf80_entry70_no_chase_shadow",
                    "running": False,
                    "process_count": 0,
                    "duplicate_process_count": 0,
                    "process_hygiene_status": "NOT_RUNNING",
                    "source_freshness_status": "NOT_RUNNING_OR_PROCESS_TIME_MISSING",
                }
            ]
        )
        readiness = pd.DataFrame(
            [
                {
                    "candidate": "btc1h_high_conf80_entry70_no_chase_shadow:since",
                    "source": "shadow_official_ledger",
                    "production_ready": False,
                    "failure_reasons": "btc1h_clean_evidence_clock_not_ready",
                    "live_official_trades": 11,
                    "live_official_pnl": 0.5,
                    "live_official_win_rate": 0.7273,
                    "live_proxy_trades": 11,
                    "live_proxy_pnl": 1.5,
                }
            ]
        )
        provenance = pd.DataFrame(
            [
                {
                    "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                    "provenance_verdict": "REMOTE_STATUS_STALE_OR_UNAVAILABLE",
                    "deployable_now": False,
                    "local_shadow_running": False,
                    "remote_status_age_minutes": 240.0,
                    "remote_status_fresh": False,
                    "remote_official_rows": 11,
                    "remote_official_pnl": 0.5,
                    "remote_proxy_pnl": 1.5,
                    "remote_official_minus_proxy_pnl": -1.0,
                    "remote_proxy_official_mismatches": 1,
                    "clean_clock_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                    "clean_clock_ready": False,
                    "clean_clock_blocker_count": 9,
                    "clean_clock_blank_policy_official_rows": 11,
                }
            ]
        )

        row = build_row(
            spec=spec,
            args=args,
            readiness=readiness,
            shadows=shadows,
            official_summary=pd.DataFrame(),
            basis_gates=pd.DataFrame(),
            health=pd.DataFrame(),
            kill_continue=pd.DataFrame(),
            reconciliation=pd.DataFrame(),
            btc1h_remote_provenance=provenance,
        )

        blockers = set(row["blocking_reasons"].split(";"))
        self.assertFalse(row["ready_running"])
        self.assertEqual(row["btc1h_remote_provenance_verdict"], "REMOTE_STATUS_STALE_OR_UNAVAILABLE")
        self.assertEqual(row["btc1h_remote_official_rows"], 11)
        self.assertEqual(row["btc1h_remote_official_pnl"], 0.5)
        self.assertEqual(row["agreement_status"], "btc1h_remote_status_stale_or_unavailable")
        self.assertIn("not_running_or_status_missing", blockers)
        self.assertIn("btc1h_remote_status_stale_or_unavailable", blockers)
        self.assertIn("btc1h_clean_evidence_clock_not_ready", blockers)
        self.assertIn("btc1h_remote_proxy_official_mismatch", blockers)
        self.assertIn("btc1h_too_few_remote_official_rows", blockers)


if __name__ == "__main__":
    unittest.main()
