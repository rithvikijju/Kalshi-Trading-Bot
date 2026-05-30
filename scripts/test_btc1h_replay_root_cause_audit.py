#!/usr/bin/env python3
"""Regression tests for BTC1H replay root-cause audit."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.build_btc1h_replay_root_cause_audit import build_rows, build_summary, classify_root_cause


class Btc1hReplayRootCauseAuditTests(unittest.TestCase):
    def test_classifies_skip_then_fill_sequence(self) -> None:
        row = pd.Series(
            {
                "diagnosis": "captured_live_fill_missing_after_skip_then_fill",
                "scan_ttl_no_signal_rows": 1,
                "scan_ttl_recomputed_signal_rows": 0,
            }
        )

        self.assertEqual(classify_root_cause(row), "reprice_skip_then_fill_sequence_missing")

    def test_classifies_reprice_entry_drift_before_clock_drift(self) -> None:
        row = pd.Series(
            {
                "diagnosis": "matched_market_with_entry_or_pnl_drift",
                "max_decision_entry_abs_diff": 0.01,
                "replay_minus_selected_sec": 2.0,
            }
        )

        self.assertEqual(classify_root_cause(row), "order_decision_reprice_fill_price_missing")

    def test_build_rows_joins_parity_and_decision_chain(self) -> None:
        diagnosis = pd.DataFrame(
            [
                {
                    "diagnosis": "captured_live_fill_missing_from_replay",
                    "status": "ledger_only_missing_from_replay",
                    "event_ticker": "KXBTCD-26MAY2210",
                    "ledger_join_key": "KXBTCD-26MAY2210-T70000|no",
                    "replay_join_key": "",
                    "ledger_entry_price": 0.62,
                }
            ]
        )
        parity = pd.DataFrame(
            [
                {
                    "market_ticker": "KXBTCD-26MAY2210-T70000",
                    "side": "no",
                    "parity_status": "recomputed_no_signal",
                    "implied_ttl_status": "pass",
                    "selected_received_at_ns": 100,
                }
            ]
        )
        chain = pd.DataFrame(
            [
                {
                    "market_ticker": "KXBTCD-26MAY2210-T70000",
                    "side": "no",
                    "decision_action": "paper_fill",
                    "decision_detail": "filled",
                    "decision_minus_selected_sec": 0.5,
                    "entry_price_diff": 0.0,
                }
            ]
        )

        rows = build_rows(diagnosis, parity, chain)
        summary = build_summary(rows).iloc[0]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["root_cause"], "scan_time_model_does_not_recreate_captured_signal")
        self.assertEqual(int(summary["root_cause_rows"]), 1)
        self.assertFalse(bool(summary["promotion_usable_from_root_cause_audit"]))


if __name__ == "__main__":
    unittest.main()
