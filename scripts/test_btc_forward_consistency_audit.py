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


if __name__ == "__main__":
    unittest.main()
