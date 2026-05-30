#!/usr/bin/env python3
"""Regression tests for BTC replay sidecar materialization."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BtcReplaySidecarMaterializationTests(unittest.TestCase):
    def test_materializer_preserves_btc1h_signal_policy_and_model_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sidecar = tmp_path / "capture.replay.jsonl"
            out_db = tmp_path / "capture.duckdb"
            rows = [
                {
                    "table": "signal_scan",
                    "received_at_ns": 100,
                    "received_at_utc": "2026-05-22T00:00:00Z",
                    "reason": "test",
                    "mode": "paper",
                    "event_ticker": "KXBTCD-26MAY2210",
                    "changed_markets": 1,
                    "evaluated_markets": 1,
                    "candidate_count": 1,
                    "selected_market": "KXBTCD-26MAY2210-T70000",
                    "selected_side": "no",
                    "signal_strategy": "high_conf_80_entry70_no_chase",
                    "model_ttl_policy": "scan_time_close_minus_now_v1",
                    "model_policy_version": "btc1h_live_model_20260522_scan_ttl_v1",
                    "entry_price": 0.66,
                    "net_edge_cents": 14.2,
                    "model_p_yes": 0.18,
                    "edge_threshold_cents": 12.4,
                    "spread_cents": 1.0,
                    "top_visible_qty": 200,
                    "quote_received_at_ns": 90,
                    "quote_age_ms": 10.0,
                    "ttl_min": 23.5,
                    "close_time": "2026-05-22T10:00:00Z",
                    "btc_spot": 70000.0,
                    "btc_candle_time": "2026-05-22T00:00:00Z",
                    "btc_candle_age_sec": 5.0,
                    "btc_rv60": 0.42,
                    "btc_ret_10m_usd": -12.0,
                    "latency_ms": 2.0,
                    "blocked_events": 0,
                    "action": "selected",
                    "detail": "ok",
                },
                {
                    "table": "order_decision",
                    "received_at_ns": 110,
                    "received_at_utc": "2026-05-22T00:00:00.000000110Z",
                    "mode": "paper",
                    "action": "paper_fill",
                    "signal_strategy": "high_conf_80_entry70_no_chase",
                    "model_ttl_policy": "scan_time_close_minus_now_v1",
                    "model_policy_version": "btc1h_live_model_20260522_scan_ttl_v1",
                    "event_ticker": "KXBTCD-26MAY2210",
                    "market_ticker": "KXBTCD-26MAY2210-T70000",
                    "side": "no",
                    "contracts": 1,
                    "entry_price": 0.66,
                    "yes_limit_price": 0.34,
                    "net_edge_cents": 14.2,
                    "btc_spot": 70000.0,
                    "estimated_cost": 0.68,
                    "portfolio_available": 100.0,
                    "portfolio_value": 100.0,
                    "client_order_id": "test",
                    "detail": "filled",
                },
                {
                    "table": "ws_lifecycle",
                    "received_at_ns": 80,
                    "received_at_utc": "2026-05-22T00:00:00Z",
                    "message_type": "market_lifecycle",
                    "event_type": "open",
                    "event_ticker": "KXBTCD-26MAY2210",
                    "market_ticker": "KXBTCD-26MAY2210-T70000",
                    "open_ts": 1000,
                    "close_ts": 2000,
                    "payload_json": "{}",
                },
            ]
            sidecar.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "materialize_btc_replay_sidecar.py"),
                    "--sidecar",
                    str(sidecar),
                    "--out-db",
                    str(out_db),
                    "--overwrite",
                ],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            con = duckdb.connect(str(out_db), read_only=True)
            try:
                signal = con.execute("select * from signal_scan").fetchdf().iloc[0].to_dict()
                decision = con.execute("select * from order_decision").fetchdf().iloc[0].to_dict()
                lifecycle = con.execute("select * from ws_lifecycle").fetchdf().iloc[0].to_dict()
            finally:
                con.close()

            self.assertEqual(signal["model_policy_version"], "btc1h_live_model_20260522_scan_ttl_v1")
            self.assertAlmostEqual(float(signal["ttl_min"]), 23.5)
            self.assertAlmostEqual(float(signal["btc_rv60"]), 0.42)
            self.assertEqual(decision["model_ttl_policy"], "scan_time_close_minus_now_v1")
            self.assertEqual(decision["side"], "no")
            self.assertEqual(int(lifecycle["open_ts"]), 1000)
            self.assertEqual(int(lifecycle["close_ts"]), 2000)

    def test_materializer_filters_time_window_and_preserves_raw_coinbase_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sidecar = tmp_path / "capture.replay.jsonl"
            out_db = tmp_path / "capture.duckdb"
            rows = [
                {
                    "table": "ws_orderbook_top",
                    "received_at_ns": 100,
                    "received_at_utc": "2026-05-29T00:00:00Z",
                    "market_ticker": "KXBTC15M-26MAY290000-100",
                    "event_ticker": "KXBTC15M-26MAY290000",
                    "btc_spot": 100.0,
                },
                {
                    "table": "ws_orderbook_top",
                    "received_at_ns": 200,
                    "received_at_utc": "2026-05-29T00:10:00Z",
                    "market_ticker": "KXBTC15M-26MAY290015-100",
                    "event_ticker": "KXBTC15M-26MAY290015",
                    "yes_ask": 0.42,
                    "no_ask": 0.59,
                    "btc_spot": 101.0,
                },
                {
                    "table": "signal_scan",
                    "received_at_ns": 210,
                    "received_at_utc": "2026-05-29T00:10:01Z",
                    "event_ticker": "KXBTC15M-26MAY290015",
                    "candidate_count": 1,
                    "selected_market": "KXBTC15M-26MAY290015-100",
                    "action": "selected",
                },
                {
                    "table": "coinbase_ticker",
                    "received_at_ns": 220,
                    "received_at_utc": "2026-05-29T00:10:02Z",
                    "product_id": "BTC-USD",
                    "price": 101.25,
                    "best_bid": 101.20,
                    "best_ask": 101.30,
                    "sequence": 7,
                    "exchange_time": "2026-05-29T00:10:02Z",
                },
                {
                    "table": "ws_orderbook_top",
                    "received_at_ns": 300,
                    "received_at_utc": "2026-05-29T00:20:00Z",
                    "market_ticker": "KXBTC15M-26MAY290030-100",
                    "event_ticker": "KXBTC15M-26MAY290030",
                    "btc_spot": 102.0,
                },
            ]
            sidecar.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "materialize_btc_replay_sidecar.py"),
                    "--sidecar",
                    str(sidecar),
                    "--out-db",
                    str(out_db),
                    "--overwrite",
                    "--start-utc",
                    "2026-05-29T00:05:00Z",
                    "--end-utc",
                    "2026-05-29T00:20:00Z",
                ],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            manifest = json.loads(out_db.with_suffix(out_db.suffix + ".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["raw_sidecar_rows_loaded"], 3)
            self.assertEqual(manifest["counts"]["ws_orderbook_top"], 1)
            self.assertEqual(manifest["counts"]["signal_scan"], 1)
            self.assertEqual(manifest["counts"]["coinbase_ticker"], 1)
            self.assertEqual(manifest["coinbase_ticker_source"], "raw_sidecar")
            self.assertEqual(manifest["filters"]["start_utc_inclusive"], "2026-05-29T00:05:00Z")
            self.assertEqual(manifest["filters"]["end_utc_exclusive"], "2026-05-29T00:20:00Z")

            con = duckdb.connect(str(out_db), read_only=True)
            try:
                top = con.execute("select market_ticker, btc_spot from ws_orderbook_top").fetchall()
                btc = con.execute("select product_id, price, best_bid, best_ask, sequence from coinbase_ticker").fetchall()
            finally:
                con.close()

            self.assertEqual(top, [("KXBTC15M-26MAY290015-100", 101.0)])
            self.assertEqual(btc, [("BTC-USD", 101.25, 101.2, 101.3, 7)])

    def test_materializer_table_prefix_filter_and_synthetic_coinbase_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sidecar = tmp_path / "capture.replay.jsonl"
            out_db = tmp_path / "capture.duckdb"
            rows = [
                {
                    "table": "ws_orderbook_top",
                    "received_at_ns": 100,
                    "received_at_utc": "2026-05-29T00:10:00Z",
                    "market_ticker": "KXBTC15M-26MAY290015-100",
                    "event_ticker": "KXBTC15M-26MAY290015",
                    "btc_spot": 101.0,
                },
                {
                    "table": "ws_orderbook_top",
                    "received_at_ns": 100,
                    "received_at_utc": "2026-05-29T00:10:00Z",
                    "market_ticker": "KXBTC15M-26MAY290015-101",
                    "event_ticker": "KXBTC15M-26MAY290015",
                    "btc_spot": 101.0,
                },
                {
                    "table": "ws_orderbook_top",
                    "received_at_ns": 110,
                    "received_at_utc": "2026-05-29T00:10:01Z",
                    "market_ticker": "KXBTCD-26MAY2910-100",
                    "event_ticker": "KXBTCD-26MAY2910",
                    "btc_spot": 102.0,
                },
                {
                    "table": "signal_scan",
                    "received_at_ns": 120,
                    "received_at_utc": "2026-05-29T00:10:02Z",
                    "event_ticker": "KXBTC15M-26MAY290015",
                    "candidate_count": 1,
                },
            ]
            sidecar.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "materialize_btc_replay_sidecar.py"),
                    "--sidecar",
                    str(sidecar),
                    "--out-db",
                    str(out_db),
                    "--overwrite",
                    "--table",
                    "ws_orderbook_top",
                    "--table",
                    "coinbase_ticker",
                    "--event-prefix",
                    "KXBTC15M",
                    "--synthetic-coinbase-from-top",
                ],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            manifest = json.loads(out_db.with_suffix(out_db.suffix + ".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["raw_sidecar_rows_loaded"], 2)
            self.assertEqual(manifest["counts"]["ws_orderbook_top"], 2)
            self.assertEqual(manifest["counts"]["coinbase_ticker"], 1)
            self.assertEqual(manifest["coinbase_ticker_source"], "synthetic_from_ws_orderbook_top_btc_spot")
            self.assertEqual(manifest["filters"]["tables"], ["ws_orderbook_top", "coinbase_ticker"])
            self.assertEqual(manifest["filters"]["event_prefixes"], ["KXBTC15M"])

            con = duckdb.connect(str(out_db), read_only=True)
            try:
                tables = {
                    row[0]
                    for row in con.execute(
                        "select table_name from information_schema.tables where table_schema = 'main'"
                    ).fetchall()
                }
                synthetic = con.execute("select received_at_ns, price from coinbase_ticker").fetchall()
            finally:
                con.close()

            self.assertIn("ws_orderbook_top", tables)
            self.assertIn("coinbase_ticker", tables)
            self.assertNotIn("signal_scan", tables)
            self.assertEqual(synthetic, [(100, 101.0)])


if __name__ == "__main__":
    unittest.main()
