#!/usr/bin/env python3
"""Safety checks for BTC settlement-basis model feasibility diagnostics."""

from __future__ import annotations

import unittest
from argparse import Namespace

import pandas as pd

from scripts import build_btc_settlement_basis_model_feasibility as mod


class SettlementBasisModelFeasibilityTests(unittest.TestCase):
    def test_decision_feature_safety_blocks_leaky_feature(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            mod.validate_decision_features(["entry_price", "basis_usd"])
        self.assertIn("basis_usd", str(ctx.exception))

    def test_feature_safety_audit_marks_only_decision_time_features_allowed(self) -> None:
        audit = mod.feature_safety_audit(["entry_price", "side_yes"])
        roles = audit.set_index("feature")["feature_role"].to_dict()
        allowed = audit.set_index("feature")["live_feature_allowed"].to_dict()
        self.assertEqual(roles["entry_price"], "decision_time_model_feature")
        self.assertTrue(bool(allowed["entry_price"]))
        self.assertEqual(roles["basis_usd"], "post_event_diagnostic_only")
        self.assertFalse(bool(allowed["basis_usd"]))

    def test_guard_verdict_blocks_tiny_weak_diagnostic_sample(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "target": "adverse_proxy_official_mismatch",
                    "samples": 129,
                    "positives": 15,
                    "positive_rate": 0.1163,
                    "model_status": "diagnostic_only",
                    "oof_roc_auc": 0.4856,
                    "oof_average_precision": 0.1401,
                    "oof_average_precision_lift": 1.2046,
                }
            ]
        )
        args = Namespace(
            min_guard_samples=250,
            min_guard_positives=30,
            min_guard_roc_auc=0.60,
            min_guard_ap_lift=2.0,
        )
        verdict = mod.guard_verdicts(summary, args, mod.feature_safety_audit())
        self.assertFalse(bool(verdict.loc[0, "statistical_precheck_pass"]))
        self.assertFalse(bool(verdict.loc[0, "deployable_guard_now"]))
        blockers = str(verdict.loc[0, "guard_blockers"])
        self.assertIn("too_few_guard_samples<250", blockers)
        self.assertIn("too_few_guard_positives<30", blockers)
        self.assertIn("weak_oof_roc_auc<0.6", blockers)
        self.assertIn("weak_oof_ap_lift<2.0", blockers)
        self.assertIn("no_preregistered_fresh_forward_guard_evaluation", blockers)


if __name__ == "__main__":
    unittest.main()
