#!/usr/bin/env python3
"""Tests for conservative BTC strategy triage helpers."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.audit_btc_strategy_triage import CANDIDATES, blocker_categories, overlap_context


class BtcStrategyTriageTests(unittest.TestCase):
    def test_overlap_context_marks_q1000_as_not_independent_when_identical_to_q250_yes(self) -> None:
        q1000 = next(spec for spec in CANDIDATES if spec.candidate == "q1000_yes")
        pairwise = pd.DataFrame(
            [
                {
                    "source": "full_causal",
                    "left_candidate": "q1000_yes",
                    "right_candidate": "q250_firstskip_qty500_yes",
                    "shared_official_events": 7,
                    "official_event_overlap_rate": 1.0,
                    "overlap_status": "identical_official_event_set",
                }
            ]
        )

        context = overlap_context(pairwise, q1000)

        self.assertEqual(context["overlap_reference_candidate"], "q250_firstskip_qty500_yes")
        self.assertEqual(context["full_causal_official_overlap_status"], "identical_official_event_set")
        self.assertEqual(context["overlap_blocker"], "not_independent_from_q250_yes_current_replay_sample")

    def test_not_independent_blocker_gets_category(self) -> None:
        cats = blocker_categories(["not_independent_from_q250_yes_current_replay_sample"])

        self.assertIn("not_independent_evidence", cats)


if __name__ == "__main__":
    unittest.main()
