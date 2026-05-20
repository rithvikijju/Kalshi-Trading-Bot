#!/usr/bin/env python3
"""Regression tests for BTC execution-realism fee gates."""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from scripts.build_btc_execution_realism_audit import audit_ledger, audit_replay
from scripts.check_btc_deployment_readiness import execution_reasons


def replay_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "received_at_ns": 1,
        "received_at_utc": "2026-05-18T09:19:48.604537Z",
        "event_ticker": "KXBTC15M-26MAY180530",
        "market_ticker": "KXBTC15M-26MAY180530-30",
        "side": "yes",
        "entry_price": 0.46,
        "entry_fee": 0.02,
        "visible_qty": 1135.0,
        "yes_ask": 0.46,
        "yes_ask_qty": 1135.0,
        "no_ask": 0.55,
        "no_ask_qty": 900.0,
        "spread_cents": 1.0,
        "btc_spot_model": 76850.0,
        "btc_spot_age_sec": 3.0,
        "close_time": "2026-05-18T09:30:00Z",
        "official_result_filled": "yes",
        "pnl_official_rest_0c": 0.52,
        "pnl_official_rest_2c": 0.50,
    }
    row.update(overrides)
    return row


def ledger_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ledger": "btc15m_q1000_yes_shadow",
        "created_at": "2026-05-18T09:19:48.650000+00:00",
        "event_ticker": "KXBTC15M-26MAY180530",
        "market_ticker": "KXBTC15M-26MAY180530-30",
        "side": "yes",
        "contracts": 1.0,
        "entry_price": 0.46,
        "fee": 0.02,
        "spread_cents": 1.0,
        "entry_btc_spot": 76850.0,
        "quote_age_ms": 20.0,
        "top_visible_qty": 1135.0,
        "quote_received_at_ns": 1,
        "signal_received_at_ns": 2,
        "yes_bid": 0.45,
        "yes_ask": 0.46,
        "no_bid": 0.54,
        "no_ask": 0.55,
        "official_result": "yes",
        "official_pnl": 0.52,
    }
    row.update(overrides)
    return row


class BtcExecutionRealismAuditTests(unittest.TestCase):
    def test_replay_summary_exposes_fee_and_stressed_pnl(self) -> None:
        summary, _, _ = audit_replay(
            pd.DataFrame([replay_row()]),
            candidate="q1000_yes_live_replay",
            path=Path("replay.parquet"),
            min_visible_qty=1000.0,
            max_btc_spot_age_sec=120.0,
        )

        self.assertEqual(summary["audit_status"], "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION")
        self.assertEqual(summary["fee_present_rate"], 1.0)
        self.assertEqual(summary["fee_nonnegative_rate"], 1.0)
        self.assertAlmostEqual(summary["fee_mean"], 0.02)
        self.assertAlmostEqual(summary["actual_fee_official_pnl"], 0.52)
        self.assertAlmostEqual(summary["stressed_2c_official_pnl"], 0.50)

    def test_negative_ledger_fee_blocks_even_when_other_fields_are_present(self) -> None:
        summary, _, _ = audit_ledger(
            pd.DataFrame([ledger_row(fee=-0.01)]),
            ledger="btc15m_q1000_yes_shadow",
            path=Path("shadow.csv"),
            max_quote_age_ms=250.0,
        )

        self.assertEqual(summary["audit_status"], "FAIL_LEDGER_EXECUTION_FIELDS")
        self.assertEqual(summary["fee_present_rate"], 1.0)
        self.assertEqual(summary["fee_nonnegative_rate"], 0.0)
        self.assertIn("fee_missing_or_negative", summary["blockers"])

    def test_readiness_names_fee_realism_separately_from_missing_fields(self) -> None:
        row = pd.Series(
            {
                "audit_status": "FAIL_LEDGER_EXECUTION_FIELDS",
                "rows": 1,
                "official_rows": 1,
                "missing_or_empty_fields": "",
                "blockers": "fee_missing_or_negative",
                "fee_present_rate": 1.0,
                "fee_nonnegative_rate": 0.0,
            }
        )

        reasons, values = execution_reasons(row, "shadow_ledger")

        self.assertIn("shadow_ledger_fee_realism_not_passing", reasons)
        self.assertNotIn("shadow_ledger_execution_fields_missing", reasons)
        self.assertEqual(values["shadow_ledger_execution_fee_nonnegative_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
