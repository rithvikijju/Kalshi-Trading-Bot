#!/usr/bin/env python3
"""Tests for BTC15M first-signal side semantics audit."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.audit_btc15m_first_signal_side_semantics import compare


class Btc15mFirstSignalSideSemanticsTests(unittest.TestCase):
    def test_side_filtered_subset_passes_when_same_yes_event(self) -> None:
        global_df = pd.DataFrame(
            [
                {"event_ticker": "E1", "received_at_ns": 1, "side": "yes", "pnl_official_rest_2c": 0.5},
                {"event_ticker": "E2", "received_at_ns": 2, "side": "no", "pnl_official_rest_2c": -0.5},
            ]
        )
        side_df = pd.DataFrame(
            [{"event_ticker": "E1", "received_at_ns": 1, "side": "yes", "pnl_official_rest_2c": 0.5}]
        )

        details, summary = compare(global_df, side_df, "yes")

        self.assertEqual(summary["audit_status"], "SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST")
        self.assertEqual(summary["side_events_agree_with_global_first"], 1)
        self.assertEqual(summary["side_first_extra_events"], 0)
        self.assertIn("global_first_only", set(details["semantics_class"]))

    def test_side_filtered_after_opposite_global_first_is_drift(self) -> None:
        global_df = pd.DataFrame(
            [{"event_ticker": "E1", "received_at_ns": 1, "side": "no", "pnl_official_rest_2c": -0.5}]
        )
        side_df = pd.DataFrame(
            [{"event_ticker": "E1", "received_at_ns": 2, "side": "yes", "pnl_official_rest_2c": 0.5}]
        )

        details, summary = compare(global_df, side_df, "yes")

        self.assertEqual(summary["audit_status"], "SIDE_FILTER_DRIFTS_FROM_GLOBAL_FIRST")
        self.assertEqual(summary["side_first_extra_events"], 1)
        self.assertEqual(details.loc[0, "semantics_class"], "side_filtered_after_opposite_global_first")

    def test_side_filtered_new_event_is_drift(self) -> None:
        global_df = pd.DataFrame(
            [{"event_ticker": "E1", "received_at_ns": 1, "side": "yes", "pnl_official_rest_2c": 0.5}]
        )
        side_df = pd.DataFrame(
            [
                {"event_ticker": "E1", "received_at_ns": 1, "side": "yes", "pnl_official_rest_2c": 0.5},
                {"event_ticker": "E2", "received_at_ns": 2, "side": "yes", "pnl_official_rest_2c": 0.5},
            ]
        )

        details, summary = compare(global_df, side_df, "yes")

        self.assertEqual(summary["audit_status"], "SIDE_FILTER_DRIFTS_FROM_GLOBAL_FIRST")
        self.assertEqual(summary["side_first_extra_events"], 1)
        self.assertIn("side_filtered_extra_event", set(details["semantics_class"]))

    def test_missing_global_target_side_row_is_counted_but_not_drift(self) -> None:
        global_df = pd.DataFrame(
            [
                {"event_ticker": "E1", "received_at_ns": 1, "side": "yes", "pnl_official_rest_2c": 0.5},
                {"event_ticker": "E2", "received_at_ns": 2, "side": "yes", "pnl_official_rest_2c": -0.5},
            ]
        )
        side_df = pd.DataFrame(
            [{"event_ticker": "E1", "received_at_ns": 1, "side": "yes", "pnl_official_rest_2c": 0.5}]
        )

        details, summary = compare(global_df, side_df, "yes")

        self.assertEqual(summary["audit_status"], "SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST")
        self.assertEqual(summary["side_first_extra_events"], 0)
        self.assertEqual(summary["global_target_side_missing_from_side_replay"], 1)
        self.assertIn("global_target_side_missing_from_side_replay", set(details["semantics_class"]))


if __name__ == "__main__":
    unittest.main()
