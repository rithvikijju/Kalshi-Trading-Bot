#!/usr/bin/env python3
"""Tests for the sequential BTC evidence stack refresh plan."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.refresh_btc_evidence_stack import cleanup_previous_refresh_artifacts, planned_steps, selected_steps


def args(**overrides: object) -> argparse.Namespace:
    base = {
        "out_dir": Path("unused"),
        "since_utc": "2026-05-18T02:42:00+00:00",
        "freeze_utc": "2026-05-18T04:17:44Z",
        "skip_packet": False,
        "start_at": None,
        "stop_after": None,
        "dry_run": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class BtcEvidenceStackRefreshTests(unittest.TestCase):
    def test_official_settlement_runs_before_dependent_artifacts(self) -> None:
        names = [step.name for step in planned_steps(args())]

        official_idx = names.index("shadow_official_settlement")
        for dependent in [
            "btc15m_postfreeze_replay_refresh",
            "execution_realism_audit",
            "post_restart_collection_gate",
            "drawdown_sequence_audit",
            "forward_row_reconciliation",
            "official_settlement_feature_table",
            "btc15m_next_forward_candidate_packet",
            "frozen_policy_parity",
            "btc1h_replay_coverage",
            "deployment_readiness",
            "forward_evidence_report",
            "gpt_pro_action_status",
        ]:
            self.assertLess(official_idx, names.index(dependent), dependent)

    def test_collection_diagnostics_run_before_summary_and_packet_steps(self) -> None:
        names = [step.name for step in planned_steps(args())]

        self.assertLess(names.index("btc15m_postfreeze_replay_refresh"), names.index("execution_realism_audit"))
        self.assertLess(names.index("btc15m_full_causal_replay_refresh"), names.index("btc15m_candidate_overlap"))
        self.assertLess(names.index("btc15m_candidate_overlap"), names.index("btc15m_shadow_replay_config_audit"))
        self.assertLess(names.index("btc15m_shadow_signal_health"), names.index("btc15m_signal_starvation"))
        self.assertLess(names.index("btc15m_postfreeze_replay_refresh"), names.index("btc15m_frozen_opportunity_rate"))
        self.assertLess(names.index("btc15m_signal_starvation"), names.index("btc15m_frozen_opportunity_rate"))
        self.assertLess(names.index("btc15m_frozen_opportunity_rate"), names.index("btc15m_first_signal_side_semantics"))
        self.assertLess(names.index("btc15m_first_signal_side_semantics"), names.index("forward_row_reconciliation"))
        self.assertLess(names.index("official_settlement_feature_table"), names.index("btc15m_next_forward_candidate_packet"))
        self.assertLess(names.index("btc15m_next_forward_candidate_packet"), names.index("frozen_policy_parity"))
        self.assertLess(names.index("frozen_policy_parity"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_replay_coverage"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("post_restart_verification"), names.index("deployment_readiness"))
        self.assertLess(names.index("post_restart_verification"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("post_restart_verification"), names.index("gpt_pro_strategy_packet"))

    def test_readiness_exit_one_is_expected_but_other_steps_are_strict(self) -> None:
        steps = planned_steps(args(skip_packet=True))
        readiness = next(step for step in steps if step.name == "deployment_readiness")
        self.assertEqual(readiness.expected_codes, (0, 1))
        strict_steps = [step for step in steps if step.name != "deployment_readiness"]
        self.assertTrue(all(step.expected_codes == (0,) for step in strict_steps))

    def test_uses_current_python_and_can_skip_packet(self) -> None:
        with_packet = planned_steps(args(skip_packet=False))
        without_packet = planned_steps(args(skip_packet=True))

        self.assertEqual(with_packet[0].argv[0], sys.executable)
        self.assertEqual(with_packet[-1].name, "gpt_pro_strategy_packet")
        self.assertNotIn("gpt_pro_strategy_packet", [step.name for step in without_packet])

    def test_reused_output_dir_cleans_only_refresh_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            stale_log = out_dir / "09_old_step.log"
            stale_log.write_text("old", encoding="utf-8")
            run_info = out_dir / "run_info.json"
            run_info.write_text("{}", encoding="utf-8")
            unrelated = out_dir / "candidate_evidence.csv"
            unrelated.write_text("keep", encoding="utf-8")

            cleanup_previous_refresh_artifacts(out_dir)

            self.assertFalse(stale_log.exists())
            self.assertFalse(run_info.exists())
            self.assertTrue(unrelated.exists())

    def test_resume_range_keeps_original_indexes_and_names(self) -> None:
        step_plan = selected_steps(
            args(start_at="btc15m_latest_live_replay_diagnostics", stop_after="execution_realism_audit"),
            planned_steps(args()),
        )

        self.assertEqual([idx for idx, _step in step_plan], [7, 8, 9])
        self.assertEqual(
            [step.name for _idx, step in step_plan],
            [
                "btc15m_latest_live_replay_diagnostics",
                "btc15m_settlement_basis_watch",
                "execution_realism_audit",
            ],
        )

    def test_resume_cleanup_only_removes_selected_step_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            keep_log = out_dir / "05_btc15m_shadow_replay_config_audit.log"
            keep_log.write_text("keep", encoding="utf-8")
            stale_selected = out_dir / "06_btc15m_latest_live_replay_diagnostics.log"
            stale_selected.write_text("old", encoding="utf-8")
            run_info = out_dir / "run_info.json"
            run_info.write_text("{}", encoding="utf-8")

            cleanup_previous_refresh_artifacts(out_dir, {6})

            self.assertTrue(keep_log.exists())
            self.assertFalse(stale_selected.exists())
            self.assertFalse(run_info.exists())


if __name__ == "__main__":
    unittest.main()
