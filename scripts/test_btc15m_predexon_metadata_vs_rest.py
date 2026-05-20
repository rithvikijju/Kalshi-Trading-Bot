#!/usr/bin/env python3
"""Tests for Predexon metadata-vs-REST source fidelity audit."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.audit_btc15m_predexon_metadata_vs_rest import Candidate, summarize_candidate


class PredexonMetadataVsRestTests(unittest.TestCase):
    def test_clean_overlap_is_research_label_not_promotion_label(self) -> None:
        trades = pd.DataFrame(
            {
                "strategy": ["s"] * 3,
                "side": ["yes", "no", "yes"],
                "side_fair_p": [0.7, 0.7, 0.7],
                "fair_edge_cents": [20.0, 20.0, 20.0],
                "entry_price": [0.4, 0.4, 0.4],
                "visible_qty": [1000, 1000, 1000],
                "spread_cents": [1.0, 1.0, 1.0],
                "ttl_min": [11.0, 11.0, 11.0],
                "predexon_metadata_result": ["yes", "no", "yes"],
                "official_result_rest": ["yes", "no", ""],
                "result": ["yes", "no", "yes"],
                "pnl_official_rest_2c": [0.59, 0.59, None],
                "pnl_predexon_metadata_2c": [0.59, 0.59, 0.59],
                "pnl_stress": [0.59, 0.59, 0.59],
            }
        )

        row = summarize_candidate(
            trades,
            Candidate("toy", "s", "both", 1.0),
            max_official_mismatch_rate=0.0,
            min_rest_overlap_rows=2,
        )

        self.assertEqual(row["rest_metadata_mismatches"], 0)
        self.assertEqual(row["metadata_research_label_status"], "PASS_RESEARCH_LABEL_OVERLAP")
        self.assertTrue(row["metadata_usable_as_historical_research_label"])
        self.assertFalse(row["metadata_usable_as_promotion_label"])
        self.assertFalse(row["deployment_usable"])
        self.assertEqual(row["source_identity_note"], "metadata_matches_existing_result_column")

    def test_rest_overlap_mismatch_blocks_research_label(self) -> None:
        trades = pd.DataFrame(
            {
                "strategy": ["s"] * 2,
                "side": ["yes", "no"],
                "side_fair_p": [0.7, 0.7],
                "fair_edge_cents": [20.0, 20.0],
                "entry_price": [0.4, 0.4],
                "visible_qty": [1000, 1000],
                "spread_cents": [1.0, 1.0],
                "ttl_min": [11.0, 11.0],
                "predexon_metadata_result": ["yes", "no"],
                "official_result_rest": ["no", "no"],
                "result": ["yes", "no"],
                "pnl_official_rest_2c": [-0.41, 0.59],
                "pnl_predexon_metadata_2c": [0.59, 0.59],
                "pnl_stress": [0.59, 0.59],
            }
        )

        row = summarize_candidate(
            trades,
            Candidate("toy", "s", "both", 1.0),
            max_official_mismatch_rate=0.0,
            min_rest_overlap_rows=2,
        )

        self.assertEqual(row["rest_metadata_mismatches"], 1)
        self.assertEqual(row["metadata_research_label_status"], "FAIL_RESEARCH_LABEL_OVERLAP")
        self.assertFalse(row["metadata_usable_as_historical_research_label"])
        self.assertIn("metadata_rest_mismatch_rate_nonzero", row["blockers"])


if __name__ == "__main__":
    unittest.main()
