#!/usr/bin/env python3
"""Regression tests for BTC1H replay-vs-ledger reconciliation."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.build_btc1h_replay_vs_ledger_reconciliation import (
    build_detail,
    build_mismatch_diagnosis,
    build_summary,
    normalize_official,
    normalize_replay,
)


LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"


def official_row(
    event_ticker: str,
    market_ticker: str,
    *,
    entry_price: float = 0.60,
    official_pnl: float = 0.40,
    official_win: bool = True,
) -> dict[str, object]:
    return {
        "ledger": LEDGER,
        "created_at": "2026-05-22T12:00:00Z",
        "status": "paper_filled",
        "event_ticker": event_ticker,
        "market_ticker": market_ticker,
        "side": "no",
        "contracts": 1,
        "entry_price": entry_price,
        "official_result": "no" if official_win else "yes",
        "official_win": official_win,
        "official_pnl": official_pnl,
        "proxy_result": "no" if official_win else "yes",
        "official_proxy_result_mismatch": False,
    }


def replay_row(
    event_ticker: str,
    market_ticker: str,
    *,
    entry_price: float = 0.60,
    pnl: float = 0.40,
    win: bool = True,
) -> dict[str, object]:
    return {
        "event_ticker": event_ticker,
        "market_ticker": market_ticker,
        "side": "no",
        "entry_time": "2026-05-22T12:00:01Z",
        "entry_price": entry_price,
        "pnl": pnl,
        "win": win,
        "official_result": "no" if win else "yes",
        "result_source": "test",
    }


class Btc1hReplayVsLedgerReconciliationTests(unittest.TestCase):
    def test_promotion_usable_requires_price_and_pnl_parity(self) -> None:
        ledger = normalize_official(
            pd.DataFrame([official_row("KXBTCD-26MAY2210", "KXBTCD-26MAY2210-T70000")]),
            LEDGER,
        )
        replay = normalize_replay(
            pd.DataFrame(
                [replay_row("KXBTCD-26MAY2210", "KXBTCD-26MAY2210-T70000", entry_price=0.61, pnl=0.39)]
            )
        )

        detail = build_detail(ledger, replay)
        summary = build_summary(ledger, replay, detail).iloc[0]

        self.assertEqual(int(summary["exact_market_side_matches"]), 1)
        self.assertFalse(bool(summary["promotion_usable_replay"]))
        self.assertIn("entry_price_not_row_for_row_equal", str(summary["blockers"]))
        self.assertIn("pnl_not_row_for_row_equal", str(summary["blockers"]))

    def test_captured_decision_log_baseline_can_be_exact_without_promotion_usability(self) -> None:
        ledger = normalize_official(
            pd.DataFrame([official_row("KXBTCD-26MAY2210", "KXBTCD-26MAY2210-T70000")]),
            LEDGER,
        )
        replay = normalize_replay(pd.DataFrame([replay_row("KXBTCD-26MAY2210", "KXBTCD-26MAY2210-T70000")]))

        detail = build_detail(ledger, replay)
        summary = build_summary(ledger, replay, detail, "captured_order_decision_log").iloc[0]

        self.assertTrue(bool(summary["row_fidelity_exact"]))
        self.assertFalse(bool(summary["promotion_usable_replay"]))
        self.assertEqual(summary["row_fidelity_blockers"], "")
        self.assertIn("diagnostic_replay_not_independent_counterfactual", str(summary["blockers"]))

    def test_diagnosis_links_missing_live_fill_to_selected_decision_chain(self) -> None:
        ledger = normalize_official(
            pd.DataFrame([official_row("KXBTCD-26MAY2211", "KXBTCD-26MAY2211-T70100", official_pnl=-0.62)]),
            LEDGER,
        )
        replay = normalize_replay(pd.DataFrame(columns=["event_ticker", "market_ticker", "side"]))
        detail = build_detail(ledger, replay)
        selected_chain = pd.DataFrame(
            [
                {
                    "selected_received_at_utc": "2026-05-22T12:00:00Z",
                    "event_ticker": "KXBTCD-26MAY2211",
                    "market_ticker": "KXBTCD-26MAY2211-T70100",
                    "side": "no",
                    "selected_entry_price": 0.62,
                    "selected_action": "selected",
                    "decision_action": "skip",
                    "decision_detail": "failed_ws_reprice_filter",
                },
                {
                    "selected_received_at_utc": "2026-05-22T12:00:01Z",
                    "event_ticker": "KXBTCD-26MAY2211",
                    "market_ticker": "KXBTCD-26MAY2211-T70100",
                    "side": "no",
                    "selected_entry_price": 0.62,
                    "selected_action": "selected",
                    "decision_action": "paper_fill",
                    "decision_detail": "filled",
                },
            ]
        )
        fill_chain = pd.DataFrame(
            [
                {
                    "decision_received_at_utc": "2026-05-22T12:00:01Z",
                    "decision_action": "paper_fill",
                    "market_ticker": "KXBTCD-26MAY2211-T70100",
                    "side": "no",
                    "decision_entry_price": 0.62,
                    "decision_detail": "filled",
                    "quote_age_ms": 10,
                    "top_visible_qty": 5,
                }
            ]
        )

        diagnosis = build_mismatch_diagnosis(detail, ledger, replay, selected_chain, fill_chain)

        self.assertEqual(len(diagnosis), 1)
        row = diagnosis.iloc[0]
        self.assertEqual(row["diagnosis"], "captured_live_fill_missing_after_skip_then_fill")
        self.assertEqual(int(row["captured_selected_scan_rows"]), 2)
        self.assertEqual(int(row["captured_selected_skip_rows"]), 1)
        self.assertEqual(int(row["captured_fill_decision_rows"]), 1)
        self.assertIn("paper_fill", row["captured_decision_actions"])

    def test_diagnosis_flags_event_level_market_replacement(self) -> None:
        ledger = normalize_official(
            pd.DataFrame([official_row("KXBTCD-26MAY2212", "KXBTCD-26MAY2212-T70100")]),
            LEDGER,
        )
        replay = normalize_replay(pd.DataFrame([replay_row("KXBTCD-26MAY2212", "KXBTCD-26MAY2212-T70200")]))
        detail = build_detail(ledger, replay)
        selected_chain = pd.DataFrame(
            [
                {
                    "event_ticker": "KXBTCD-26MAY2212",
                    "market_ticker": "KXBTCD-26MAY2212-T70100",
                    "side": "no",
                    "decision_action": "paper_fill",
                }
            ]
        )
        fill_chain = selected_chain.copy()

        diagnosis = build_mismatch_diagnosis(detail, ledger, replay, selected_chain, fill_chain)

        self.assertEqual(set(diagnosis["diagnosis"]), {
            "captured_live_fill_replaced_by_replay_market",
            "replay_extra_market_replacing_captured_live_fill",
        })


if __name__ == "__main__":
    unittest.main()
