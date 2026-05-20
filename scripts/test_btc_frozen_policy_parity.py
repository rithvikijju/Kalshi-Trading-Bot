#!/usr/bin/env python3
"""Tests for frozen BTC paper-shadow policy parity audit."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.audit_btc_frozen_policy_parity import POLICIES, PolicySpec, audit_policy


class BtcFrozenPolicyParityTests(unittest.TestCase):
    def test_current_frozen_wrappers_match_expected_policy(self) -> None:
        rows = [audit_policy(spec) for spec in POLICIES]

        self.assertTrue(all(row["policy_parity_pass"] for row in rows), rows)
        self.assertEqual({row["policy_parity_status"] for row in rows}, {"PASS_FROZEN_POLICY_PARITY"})

    def test_mismatched_threshold_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "wrapper.py"
            script.write_text(
                "\n".join(
                    [
                        "import os, sys",
                        "os.environ['BTC15M_SIGNAL_STRATEGY'] = 'h02'",
                        "os.environ['BTC15M_H02_EDGE_THRESHOLD_CENTS'] = '11.0'",
                        "sys.argv = ['btc15m_lowdd_live.py', '--mode', 'paper']",
                    ]
                ),
                encoding="utf-8",
            )
            spec = PolicySpec(
                policy_id="test",
                family="BTC15M",
                candidate="test_candidate",
                ledger="test_ledger",
                wrapper_script="wrapper.py",
                role="test",
                expected_env={
                    "BTC15M_SIGNAL_STRATEGY": "h02",
                    "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
                },
                env_defaults={},
                required_argv_flags={"--mode": "paper"},
            )

            row = audit_policy(spec, project_root=root)

        self.assertFalse(row["policy_parity_pass"])
        self.assertIn("env_mismatch:BTC15M_H02_EDGE_THRESHOLD_CENTS", row["policy_parity_blockers"])


if __name__ == "__main__":
    unittest.main()
