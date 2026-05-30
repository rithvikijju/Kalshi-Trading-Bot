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
            "btc1h_replay_root_cause_audit",
            "btc1h_order_decision_replay_baseline",
            "btc1h_order_decision_replay_baseline_reconciliation",
            "btc1h_replay_repair_feasibility",
            "btc1h_replay_repair_target_matrix",
            "btc1h_replay_repair_prerequisite_audit",
            "btc1h_replay_repair_attempt_audit",
            "btc1h_faithful_replay_data_contract",
            "btc1h_replay_source_contract_readiness",
            "btc1h_clean_clock_collection_preflight",
            "btc1h_clean_evidence_clock_gate",
            "btc1h_basis_stress_audit",
            "btc1h_variant_basis_stress_ranking",
            "btc1h_decision_distance_guard_audit",
            "btc1h_side_entry_profile_audit",
            "btc1h_holdout_independence_audit",
            "btc1h_statistical_confidence_audit",
            "btc1h_replay_variant_overlap_audit",
            "btc1h_entry59_floor_filter_audit",
            "btc1h_no_chase_extra_row_audit",
            "btc1h_research_priority_matrix",
            "deployment_readiness",
            "btc1h_promotion_gap_matrix",
            "btc1h_snapshot_execution_realism_audit",
            "btc1h_execution_filter_impact_audit",
            "btc1h_execution_filtered_basis_mismatch_audit",
            "btc1h_official_pnl_path_audit",
            "btc1h_objective_completion_audit",
            "btc1h_candidate_promotion_deficit_audit",
            "btc1h_remaining_evidence_manifest",
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
        self.assertLess(names.index("btc1h_multi_holdout_research"), names.index("btc1h_clean_evidence_clock_gate"))
        self.assertLess(names.index("btc1h_clean_evidence_clock_gate"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_clean_evidence_clock_gate"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_clean_evidence_clock_gate"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_clean_evidence_clock_gate"), names.index("btc1h_replay_root_cause_audit"))
        self.assertLess(names.index("restart_authorization_packet"), names.index("btc1h_clean_clock_collection_preflight"))
        self.assertLess(names.index("post_restart_collection_gate"), names.index("btc1h_clean_clock_collection_preflight"))
        self.assertLess(names.index("btc1h_clean_evidence_clock_gate"), names.index("btc1h_clean_clock_collection_preflight"))
        self.assertLess(names.index("btc1h_replay_root_cause_audit"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_replay_root_cause_audit"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_replay_root_cause_audit"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_replay_root_cause_audit"), names.index("btc1h_order_decision_replay_baseline"))
        self.assertLess(
            names.index("btc1h_order_decision_replay_baseline"),
            names.index("btc1h_order_decision_replay_baseline_reconciliation"),
        )
        self.assertLess(
            names.index("btc1h_order_decision_replay_baseline_reconciliation"),
            names.index("btc1h_replay_repair_feasibility"),
        )
        self.assertLess(names.index("btc1h_replay_root_cause_audit"), names.index("btc1h_replay_repair_feasibility"))
        self.assertLess(names.index("btc1h_clean_evidence_clock_gate"), names.index("btc1h_replay_repair_feasibility"))
        self.assertLess(names.index("btc1h_replay_repair_feasibility"), names.index("btc1h_replay_repair_target_matrix"))
        self.assertLess(
            names.index("btc1h_replay_repair_target_matrix"),
            names.index("btc1h_replay_repair_prerequisite_audit"),
        )
        self.assertLess(
            names.index("btc1h_replay_repair_prerequisite_audit"),
            names.index("btc1h_replay_repair_attempt_audit"),
        )
        self.assertLess(
            names.index("btc1h_replay_repair_attempt_audit"),
            names.index("btc1h_faithful_replay_data_contract"),
        )
        self.assertLess(
            names.index("btc1h_clean_evidence_clock_gate"),
            names.index("btc1h_faithful_replay_data_contract"),
        )
        self.assertLess(
            names.index("btc1h_replay_repair_prerequisite_audit"),
            names.index("btc1h_faithful_replay_data_contract"),
        )
        self.assertLess(
            names.index("btc1h_faithful_replay_data_contract"),
            names.index("btc1h_replay_source_contract_readiness"),
        )
        self.assertLess(
            names.index("btc1h_replay_source_contract_readiness"),
            names.index("btc1h_clean_clock_collection_preflight"),
        )
        self.assertLess(
            names.index("btc1h_clean_clock_collection_preflight"),
            names.index("btc1h_official_basis_mismatch_audit"),
        )
        self.assertLess(
            names.index("btc1h_replay_source_contract_readiness"),
            names.index("btc1h_remaining_evidence_manifest"),
        )
        self.assertLess(
            names.index("btc1h_clean_clock_collection_preflight"),
            names.index("btc1h_remaining_evidence_manifest"),
        )
        self.assertLess(
            names.index("btc1h_replay_repair_attempt_audit"),
            names.index("btc1h_next_forward_candidate_packet"),
        )
        self.assertLess(names.index("btc1h_replay_repair_attempt_audit"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_replay_repair_attempt_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_replay_repair_attempt_audit"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_replay_repair_prerequisite_audit"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_replay_repair_prerequisite_audit"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_replay_repair_prerequisite_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_replay_repair_prerequisite_audit"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_replay_repair_target_matrix"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_replay_repair_target_matrix"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_replay_repair_target_matrix"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_replay_repair_target_matrix"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_replay_repair_feasibility"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_replay_repair_feasibility"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_replay_repair_feasibility"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_replay_repair_feasibility"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_multi_holdout_research"), names.index("btc1h_decision_distance_guard_audit"))
        self.assertLess(names.index("btc1h_official_basis_mismatch_audit"), names.index("btc1h_decision_distance_guard_audit"))
        self.assertLess(names.index("btc1h_official_basis_mismatch_audit"), names.index("btc1h_basis_stress_audit"))
        self.assertLess(names.index("btc1h_basis_stress_audit"), names.index("btc1h_variant_basis_stress_ranking"))
        self.assertLess(names.index("btc1h_variant_basis_stress_ranking"), names.index("btc1h_decision_distance_guard_audit"))
        self.assertLess(names.index("btc1h_variant_basis_stress_ranking"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_basis_stress_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_decision_distance_guard_audit"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_decision_distance_guard_audit"), names.index("btc1h_side_entry_profile_audit"))
        self.assertLess(names.index("btc1h_side_entry_profile_audit"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_side_entry_profile_audit"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_side_entry_profile_audit"), names.index("btc1h_holdout_independence_audit"))
        self.assertLess(names.index("btc1h_holdout_independence_audit"), names.index("btc1h_statistical_confidence_audit"))
        self.assertLess(names.index("btc1h_holdout_independence_audit"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_holdout_independence_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_holdout_independence_audit"), names.index("btc1h_research_priority_matrix"))
        self.assertLess(names.index("btc1h_side_entry_profile_audit"), names.index("btc1h_statistical_confidence_audit"))
        self.assertLess(names.index("btc1h_variant_basis_stress_ranking"), names.index("btc1h_research_priority_matrix"))
        self.assertLess(names.index("btc1h_statistical_confidence_audit"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_statistical_confidence_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_statistical_confidence_audit"), names.index("btc1h_replay_variant_overlap_audit"))
        self.assertLess(names.index("btc1h_replay_variant_overlap_audit"), names.index("btc1h_research_priority_matrix"))
        self.assertLess(names.index("btc1h_replay_variant_overlap_audit"), names.index("btc1h_entry59_floor_filter_audit"))
        self.assertLess(names.index("btc1h_entry59_floor_filter_audit"), names.index("btc1h_research_priority_matrix"))
        self.assertLess(names.index("btc1h_entry59_floor_filter_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_entry59_floor_filter_audit"), names.index("btc1h_no_chase_extra_row_audit"))
        self.assertLess(names.index("btc1h_no_chase_extra_row_audit"), names.index("btc1h_research_priority_matrix"))
        self.assertLess(names.index("btc1h_no_chase_extra_row_audit"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_statistical_confidence_audit"), names.index("btc1h_research_priority_matrix"))
        self.assertLess(names.index("btc1h_research_priority_matrix"), names.index("btc1h_next_forward_candidate_packet"))
        self.assertLess(names.index("btc1h_research_priority_matrix"), names.index("deployment_readiness"))
        self.assertLess(names.index("btc1h_research_priority_matrix"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_decision_distance_guard_audit"), names.index("deployment_readiness"))
        self.assertLess(names.index("deployment_readiness"), names.index("btc1h_promotion_gap_matrix"))
        self.assertLess(names.index("btc1h_next_forward_candidate_packet"), names.index("btc1h_objective_completion_audit"))
        self.assertLess(names.index("btc1h_research_priority_matrix"), names.index("btc1h_objective_completion_audit"))
        self.assertLess(names.index("btc1h_promotion_gap_matrix"), names.index("forward_evidence_report"))
        self.assertLess(names.index("btc1h_promotion_gap_matrix"), names.index("btc1h_snapshot_execution_realism_audit"))
        self.assertLess(
            names.index("btc1h_snapshot_execution_realism_audit"),
            names.index("btc1h_execution_filter_impact_audit"),
        )
        self.assertLess(
            names.index("btc1h_execution_filter_impact_audit"),
            names.index("btc1h_execution_filtered_basis_mismatch_audit"),
        )
        self.assertLess(
            names.index("btc1h_execution_filtered_basis_mismatch_audit"),
            names.index("btc1h_official_pnl_path_audit"),
        )
        self.assertLess(
            names.index("btc1h_official_pnl_path_audit"),
            names.index("btc1h_objective_completion_audit"),
        )
        self.assertLess(names.index("btc1h_official_pnl_path_audit"), names.index("btc1h_remaining_evidence_manifest"))
        self.assertLess(names.index("btc1h_official_pnl_path_audit"), names.index("forward_evidence_report"))
        self.assertLess(names.index("btc1h_official_pnl_path_audit"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_promotion_gap_matrix"), names.index("btc1h_objective_completion_audit"))
        self.assertLess(
            names.index("btc1h_objective_completion_audit"),
            names.index("btc1h_candidate_promotion_deficit_audit"),
        )
        self.assertLess(
            names.index("btc1h_candidate_promotion_deficit_audit"),
            names.index("btc1h_remaining_evidence_manifest"),
        )
        self.assertLess(
            names.index("btc1h_promotion_gap_matrix"),
            names.index("btc1h_candidate_promotion_deficit_audit"),
        )
        self.assertLess(
            names.index("btc1h_candidate_promotion_deficit_audit"),
            names.index("forward_evidence_report"),
        )
        self.assertLess(
            names.index("btc1h_candidate_promotion_deficit_audit"),
            names.index("gpt_pro_action_status"),
        )
        self.assertLess(names.index("btc1h_promotion_gap_matrix"), names.index("btc1h_remaining_evidence_manifest"))
        self.assertLess(names.index("btc1h_remaining_evidence_manifest"), names.index("forward_evidence_report"))
        self.assertLess(names.index("btc1h_remaining_evidence_manifest"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_objective_completion_audit"), names.index("forward_evidence_report"))
        self.assertLess(names.index("btc1h_objective_completion_audit"), names.index("gpt_pro_action_status"))
        self.assertLess(names.index("btc1h_promotion_gap_matrix"), names.index("gpt_pro_action_status"))

    def test_readiness_exit_one_is_expected_but_other_steps_are_strict(self) -> None:
        steps = planned_steps(args(skip_packet=True))
        readiness = next(step for step in steps if step.name == "deployment_readiness")
        clean_clock = next(step for step in steps if step.name == "btc1h_clean_evidence_clock_gate")
        self.assertEqual(readiness.expected_codes, (0, 1))
        self.assertEqual(clean_clock.expected_codes, (0, 1))
        strict_steps = [
            step
            for step in steps
            if step.name not in {"deployment_readiness", "btc1h_clean_evidence_clock_gate"}
        ]
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
