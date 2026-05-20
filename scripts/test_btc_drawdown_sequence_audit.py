#!/usr/bin/env python3
"""Regression tests for BTC drawdown sequence audit."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.build_btc_drawdown_sequence_audit import make_sequence, summarize_sequence


def trade_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ledger": "btc15m_q1000_yes_shadow",
        "created_at": "2026-05-18T09:00:00+00:00",
        "created_at_ts": pd.Timestamp("2026-05-18T09:00:00Z"),
        "event_ticker": "KXBTC15M-26MAY180500",
        "market_ticker": "KXBTC15M-26MAY180500-00",
        "side": "yes",
        "official_result": "yes",
        "proxy_result": "yes",
        "official_proxy_result_mismatch": False,
        "official_pnl": 0.50,
    }
    row.update(overrides)
    return row


class BtcDrawdownSequenceAuditTests(unittest.TestCase):
    def test_sequence_computes_cumulative_peak_and_drawdown_in_time_order(self) -> None:
        rows = pd.DataFrame(
            [
                trade_row(
                    created_at="2026-05-18T09:10:00+00:00",
                    created_at_ts=pd.Timestamp("2026-05-18T09:10:00Z"),
                    market_ticker="later",
                    official_pnl=-0.70,
                ),
                trade_row(
                    created_at="2026-05-18T09:00:00+00:00",
                    created_at_ts=pd.Timestamp("2026-05-18T09:00:00Z"),
                    market_ticker="first",
                    official_pnl=0.50,
                ),
                trade_row(
                    created_at="2026-05-18T09:20:00+00:00",
                    created_at_ts=pd.Timestamp("2026-05-18T09:20:00Z"),
                    market_ticker="last",
                    official_pnl=0.40,
                ),
            ]
        )

        seq = make_sequence(
            rows,
            family="BTC15M",
            candidate="q1000_yes",
            ledger="btc15m_q1000_yes_shadow",
            scope="post_restart_promotion_window",
            promotion_window=True,
        )

        self.assertEqual(seq["market_ticker"].tolist(), ["first", "later", "last"])
        self.assertEqual(seq["sequence_index"].tolist(), [1, 2, 3])
        self.assertEqual(seq["cumulative_official_pnl"].tolist(), [0.5, -0.2, 0.2])
        self.assertEqual(seq["running_peak_official_pnl"].tolist(), [0.5, 0.5, 0.5])
        self.assertEqual(seq["drawdown"].tolist(), [0.0, -0.7, -0.3])

    def test_promotion_window_summary_blocks_without_controlled_restart(self) -> None:
        seq = make_sequence(
            pd.DataFrame([trade_row()]),
            family="BTC15M",
            candidate="q1000_yes",
            ledger="btc15m_q1000_yes_shadow",
            scope="post_restart_promotion_window",
            promotion_window=True,
        )

        summary = summarize_sequence(
            seq,
            family="BTC15M",
            candidate="q1000_yes",
            ledger="btc15m_q1000_yes_shadow",
            scope="post_restart_promotion_window",
            min_official_rows=100,
            restart_executed=False,
            max_drawdown_one_contract=10.0,
            max_dd_to_pnl_ratio=0.5,
            promotion_window=True,
        )

        self.assertEqual(summary["drawdown_gate_status"], "PENDING_CONTROLLED_RESTART")
        self.assertFalse(summary["drawdown_gate_pass"])
        self.assertIn("controlled_restart_not_executed", summary["failure_reasons"])
        self.assertIn("too_few_official_rows_for_drawdown_gate", summary["failure_reasons"])


if __name__ == "__main__":
    unittest.main()
