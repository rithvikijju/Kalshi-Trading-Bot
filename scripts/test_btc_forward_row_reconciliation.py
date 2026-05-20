#!/usr/bin/env python3
"""Regression tests for BTC paper-vs-replay row reconciliation."""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from scripts.build_btc_forward_row_reconciliation import CandidateSpec, reconcile_candidate


FREEZE = "2026-05-18T04:17:44Z"


def paper_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ledger": "btc15m_q1000_yes_shadow",
        "created_at": "2026-05-18T09:19:48.650000+00:00",
        "status": "paper_filled",
        "event_ticker": "KXBTC15M-26MAY180530",
        "market_ticker": "KXBTC15M-26MAY180530-30",
        "side": "yes",
        "contracts": 1.0,
        "entry_price": 0.46,
        "fee": 0.02,
        "official_result": "yes",
        "official_pnl": 0.52,
        "proxy_result": "yes",
        "proxy_pnl": 0.52,
        "expiration_value": 76925.94,
        "quote_age_ms": 20.0,
        "top_visible_qty": 1135.0,
        "quote_received_at_ns": 1,
        "signal_received_at_ns": 2,
        "yes_ask": 0.46,
        "no_ask": 0.55,
    }
    row.update(overrides)
    return row


def replay_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "received_at_utc": pd.Timestamp("2026-05-18T09:19:48.604537Z"),
        "received_at_ns": 3,
        "event_ticker": "KXBTC15M-26MAY180530",
        "market_ticker": "KXBTC15M-26MAY180530-30",
        "side": "yes",
        "entry_price": 0.46,
        "entry_fee": 0.02,
        "premium": 0.48,
        "official_result_filled": "yes",
        "pnl_official_rest_0c": 0.52,
        "pnl_official_rest_2c": 0.50,
        "proxy_result": "yes",
        "pnl_proxy_2c": 0.50,
        "visible_qty": 1135.0,
        "yes_ask": 0.46,
        "no_ask": 0.55,
    }
    row.update(overrides)
    return row


class BtcForwardRowReconciliationTests(unittest.TestCase):
    def test_matching_rows_reconcile_on_actual_fee_pnl_and_track_stress_delta(self) -> None:
        spec = CandidateSpec(
            family="BTC15M",
            candidate="q1000_yes",
            ledger="btc15m_q1000_yes_shadow",
            replay_path=Path("replay.parquet"),
            min_official_rows=1,
        )

        summary, details = reconcile_candidate(
            shadow=pd.DataFrame([paper_row()]),
            replay_raw=pd.DataFrame([replay_row()]),
            spec=spec,
            freeze_utc=FREEZE,
            entry_tolerance=1e-9,
            pnl_tolerance=1e-9,
            time_tolerance_ms=1000.0,
        )

        self.assertTrue(summary["row_reconciliation_pass"])
        self.assertEqual(summary["matched_rows"], 1)
        self.assertEqual(summary["actual_fee_pnl_mismatch_rows"], 0)
        self.assertAlmostEqual(summary["paper_minus_replay_actual_fee_pnl"], 0.0)
        self.assertAlmostEqual(summary["paper_minus_replay_stressed_2c_pnl"], 0.02)
        self.assertEqual(details["reconciliation_status"].tolist(), ["matched"])

    def test_replay_only_rows_block_fresh_paper_shadow_candidate(self) -> None:
        spec = CandidateSpec(
            family="BTC15M",
            candidate="q250_firstskip_qty500_yes",
            ledger="btc15m_q250_qty500_firstskip_yes_shadow",
            replay_path=Path("replay.parquet"),
            min_official_rows=1,
        )

        summary, details = reconcile_candidate(
            shadow=pd.DataFrame([paper_row(ledger="btc15m_q1000_yes_shadow")]),
            replay_raw=pd.DataFrame([replay_row()]),
            spec=spec,
            freeze_utc=FREEZE,
            entry_tolerance=1e-9,
            pnl_tolerance=1e-9,
            time_tolerance_ms=1000.0,
        )

        self.assertFalse(summary["row_reconciliation_pass"])
        self.assertEqual(summary["paper_rows"], 0)
        self.assertEqual(summary["replay_without_paper_rows"], 1)
        self.assertIn("no_paper_shadow_rows_since_freeze", summary["blocking_reasons"])
        self.assertEqual(details["reconciliation_status"].tolist(), ["replay_without_paper"])


if __name__ == "__main__":
    unittest.main()
