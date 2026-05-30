#!/usr/bin/env python3
"""Regression tests for BTC1H signal-scan replay pruning."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from scripts.core_model_research import ModelVariant
from scripts.build_btc1h_selected_signal_model_parity import load_selected_signals
from scripts.replay_btc1h_core_ws_counterfactual import (
    QuoteState,
    load_signal_scans,
    parse_args,
    quote_state_matches_static_book,
    static_book_candidate_mask,
)


class Btc1hReplaySignalScanPruningTests(unittest.TestCase):
    def test_model_ttl_override_is_explicit_diagnostic_option(self) -> None:
        with patch.object(sys, "argv", ["replay", "--model-ttl-override-min", "15"]):
            args = parse_args()

        self.assertEqual(args.model_ttl_override_min, 15.0)

    def test_window_and_candidate_pruning_happens_in_sql(self) -> None:
        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE signal_scan (
                received_at_ns BIGINT,
                received_at_utc VARCHAR,
                reason VARCHAR,
                event_ticker VARCHAR,
                changed_markets BIGINT,
                evaluated_markets BIGINT,
                candidate_count BIGINT,
                selected_market VARCHAR,
                action VARCHAR,
                detail VARCHAR
            )
            """
        )
        con.execute(
            """
            INSERT INTO signal_scan VALUES
                (100, '2026-05-22T00:00:00Z', 'orderbook_delta', 'KXBTCD-26MAY2210', 1, 1, 0, '', 'none', ''),
                (150, '2026-05-22T00:00:01Z', 'orderbook_delta', 'KXBTCD-26MAY2210', 1, 1, 1, 'KXBTCD-26MAY2210-T70000', 'candidate', ''),
                (250, '2026-05-22T00:00:02Z', 'orderbook_delta', 'KXBTCD-26MAY2210', 1, 1, 1, 'KXBTCD-26MAY2210-T70100', 'selected', ''),
                (175, '2026-05-22T00:00:03Z', 'orderbook_delta', 'KXBTCD-26MAY2211', 1, 1, 1, 'KXBTCD-26MAY2211-T70000', 'selected', '')
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE replay_windows (
                event_ticker VARCHAR,
                seed_floor_ns BIGINT,
                replay_start_ns BIGINT,
                replay_end_ns BIGINT
            )
            """
        )
        con.execute("INSERT INTO replay_windows VALUES ('KXBTCD-26MAY2210', 90, 120, 240)")

        candidate = load_signal_scans(
            con,
            "signal_scan",
            None,
            None,
            use_replay_windows=True,
            candidate_scan_only=True,
        )
        selected = load_signal_scans(
            con,
            "signal_scan",
            None,
            None,
            use_replay_windows=True,
            selected_scan_only=True,
        )

        self.assertEqual(candidate["received_at_ns"].tolist(), [150])
        self.assertTrue(selected.empty)
        con.close()

    def test_static_book_candidate_mask_respects_entry_spread_and_qty(self) -> None:
        q = pd.DataFrame(
            [
                {
                    "yes_ask_exe": 0.60,
                    "yes_ask_qty": 10,
                    "yes_spread_cents": 1.0,
                    "no_ask_exe": 0.42,
                    "no_ask_qty": 0,
                    "no_spread_cents": 1.0,
                },
                {
                    "yes_ask_exe": 0.80,
                    "yes_ask_qty": 10,
                    "yes_spread_cents": 1.0,
                    "no_ask_exe": 0.80,
                    "no_ask_qty": 10,
                    "no_spread_cents": 1.0,
                },
                {
                    "yes_ask_exe": 0.60,
                    "yes_ask_qty": 10,
                    "yes_spread_cents": 3.0,
                    "no_ask_exe": 0.40,
                    "no_ask_qty": 10,
                    "no_spread_cents": 3.0,
                },
            ]
        )
        variants = [
            ModelVariant(
                "entry70",
                1,
                "test",
                min_entry=0.25,
                max_entry=0.70,
                max_spread_cents=2.0,
            )
        ]

        self.assertEqual(static_book_candidate_mask(q, variants).tolist(), [True, False, False])

    def test_quote_state_static_candidate_guard_matches_dataframe_gate(self) -> None:
        variant = ModelVariant(
            "entry70",
            1,
            "test",
            min_entry=0.25,
            max_entry=0.70,
            max_spread_cents=2.0,
        )
        quote_state = QuoteState(
            received_at_ns=1,
            received_at_utc=pd.Timestamp("2026-05-22T00:00:00Z"),
            market_ticker="KXBTCD-26MAY2210-T70000",
            event_ticker="KXBTCD-26MAY2210",
            seq=1,
            yes_bid=0.59,
            yes_bid_qty=10,
            yes_ask=0.60,
            yes_ask_qty=10,
            no_bid=0.39,
            no_bid_qty=10,
            no_ask=0.40,
            no_ask_qty=10,
            btc_spot=70000.0,
            source="test",
            floor_strike=70000.0,
            close_time=pd.Timestamp("2026-05-22T10:00:00Z"),
        )
        wide_quote_state = QuoteState(
            **{
                **quote_state.__dict__,
                "yes_bid": 0.55,
                "no_bid": 0.35,
            }
        )

        self.assertTrue(quote_state_matches_static_book(quote_state, [variant]))
        self.assertFalse(quote_state_matches_static_book(wide_quote_state, [variant]))

    def test_selected_signal_loader_preserves_optional_model_inputs(self) -> None:
        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE signal_scan (
                received_at_ns BIGINT,
                received_at_utc VARCHAR,
                reason VARCHAR,
                event_ticker VARCHAR,
                changed_markets BIGINT,
                evaluated_markets BIGINT,
                candidate_count BIGINT,
                selected_market VARCHAR,
                selected_side VARCHAR,
                signal_strategy VARCHAR,
                model_ttl_policy VARCHAR,
                model_policy_version VARCHAR,
                entry_price DOUBLE,
                net_edge_cents DOUBLE,
                model_p_yes DOUBLE,
                edge_threshold_cents DOUBLE,
                spread_cents DOUBLE,
                top_visible_qty DOUBLE,
                quote_received_at_ns BIGINT,
                quote_age_ms DOUBLE,
                ttl_min DOUBLE,
                close_time VARCHAR,
                btc_spot DOUBLE,
                btc_candle_time VARCHAR,
                btc_candle_age_sec DOUBLE,
                btc_rv60 DOUBLE,
                btc_ret_10m_usd DOUBLE,
                latency_ms DOUBLE,
                blocked_events BIGINT,
                action VARCHAR,
                detail VARCHAR
            )
            """
        )
        con.execute(
            """
            INSERT INTO signal_scan VALUES (
                100,
                '2026-05-22T00:00:00Z',
                'test',
                'KXBTCD-26MAY2210',
                1,
                1,
                1,
                'KXBTCD-26MAY2210-T70000',
                'no',
                'high_conf_80_entry70_no_chase',
                'scan_time_close_minus_now_v1',
                'btc1h_live_model_20260522_scan_ttl_v1',
                0.66,
                14.2,
                0.18,
                12.4,
                1.0,
                200,
                90,
                10.0,
                23.5,
                '2026-05-22T10:00:00Z',
                70000.0,
                '2026-05-22T00:00:00Z',
                5.0,
                0.42,
                -12.0,
                2.0,
                0,
                'selected',
                'ok'
            )
            """
        )

        selected = load_selected_signals(con, "", "")
        con.close()

        self.assertEqual(len(selected), 1)
        row = selected.iloc[0]
        self.assertEqual(row["selected_model_policy_version"], "btc1h_live_model_20260522_scan_ttl_v1")
        self.assertAlmostEqual(float(row["selected_ttl_min"]), 23.5)
        self.assertAlmostEqual(float(row["selected_btc_rv60"]), 0.42)


if __name__ == "__main__":
    unittest.main()
