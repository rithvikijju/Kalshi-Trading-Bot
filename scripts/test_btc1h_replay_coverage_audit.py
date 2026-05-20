#!/usr/bin/env python3
"""Regression tests for BTC1H replay coverage diagnostics."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

from scripts import build_btc1h_replay_coverage_audit as audit
from scripts.build_btc1h_replay_coverage_audit import CaptureSource, build_audit, summarize


class Btc1hReplayCoverageAuditTests(unittest.TestCase):
    def test_row_is_replayable_when_readable_capture_has_top_and_signal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "capture.duckdb"
            con = duckdb.connect(str(db_path))
            con.execute(
                "create table ws_orderbook_top(event_ticker varchar, received_at_ns bigint, received_at_utc varchar)"
            )
            con.execute(
                "create table signal_scan(event_ticker varchar, received_at_ns bigint, received_at_utc varchar)"
            )
            event = "KXBTCD-26MAY1801"
            # Event closes at 2026-05-18T05:00Z, comfortably inside the default coverage window.
            ts = pd.Timestamp("2026-05-18T04:41:25Z").value
            con.execute("insert into ws_orderbook_top values (?, ?, ?)", [event, ts, "2026-05-18T04:41:25Z"])
            con.execute("insert into signal_scan values (?, ?, ?)", [event, ts, "2026-05-18T04:41:25Z"])
            con.close()

            trades = pd.DataFrame(
                [
                    {
                        "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                        "created_at": "2026-05-18T04:41:25.952242+00:00",
                        "event_ticker": event,
                        "market_ticker": "KXBTCD-26MAY1801-T76999.99",
                        "side": "no",
                        "entry_price": 0.68,
                        "official_result": "yes",
                        "official_pnl": -0.70,
                    }
                ]
            )

            rows, source_rows = build_audit(
                trades,
                [CaptureSource("test_capture", db_path)],
                since_utc="2026-05-18T04:17:44Z",
                pre_close_min=80.0,
                post_close_min=8.0,
            )
            summary = summarize(rows)

        self.assertEqual(rows["coverage_status"].tolist(), ["REPLAYABLE_FROM_READABLE_CAPTURE"])
        self.assertEqual(rows["best_capture_label"].tolist(), ["test_capture"])
        self.assertEqual(rows["best_capture_read_source"].tolist(), ["live_readonly"])
        self.assertEqual(int(source_rows["top_rows"].iloc[0]), 1)
        self.assertTrue(bool(summary.loc[summary["scope"].eq("since"), "coverage_gate_pass"].iloc[0]))

    def test_locked_capture_can_use_snapshot_copy_for_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "locked_capture.duckdb"
            con = duckdb.connect(str(db_path))
            con.execute(
                "create table ws_orderbook_top(event_ticker varchar, received_at_ns bigint, received_at_utc varchar)"
            )
            con.execute(
                "create table signal_scan(event_ticker varchar, received_at_ns bigint, received_at_utc varchar)"
            )
            event = "KXBTCD-26MAY1801"
            ts = pd.Timestamp("2026-05-18T04:41:25Z").value
            con.execute("insert into ws_orderbook_top values (?, ?, ?)", [event, ts, "2026-05-18T04:41:25Z"])
            con.execute("insert into signal_scan values (?, ?, ?)", [event, ts, "2026-05-18T04:41:25Z"])
            con.close()

            trades = pd.DataFrame(
                [
                    {
                        "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                        "created_at": "2026-05-18T04:41:25.952242+00:00",
                        "event_ticker": event,
                        "market_ticker": "KXBTCD-26MAY1801-T76999.99",
                        "side": "no",
                        "entry_price": 0.68,
                        "official_result": "yes",
                        "official_pnl": -0.70,
                    }
                ]
            )

            real_connect = duckdb.connect

            def flaky_connect(path: str, *args, **kwargs):
                if Path(path) == db_path:
                    raise RuntimeError("database is being used by another process")
                return real_connect(path, *args, **kwargs)

            with patch.object(audit.duckdb, "connect", side_effect=flaky_connect):
                rows, source_rows = build_audit(
                    trades,
                    [CaptureSource("locked_capture", db_path)],
                    since_utc="2026-05-18T04:17:44Z",
                    pre_close_min=80.0,
                    post_close_min=8.0,
                )

        self.assertEqual(rows["coverage_status"].tolist(), ["REPLAYABLE_FROM_READABLE_CAPTURE"])
        self.assertEqual(rows["best_capture_read_source"].tolist(), ["snapshot_copy_after_live_read_failure"])
        self.assertIn("snapshot_succeeded", str(source_rows["read_error"].iloc[0]))
        self.assertEqual(int(source_rows["signal_scan_rows"].iloc[0]), 1)

    def test_locked_capture_can_use_replay_sidecar_when_snapshot_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "locked_capture.duckdb"
            db_path.write_bytes(b"locked-placeholder")
            sidecar = db_path.with_name(db_path.name + ".replay.jsonl")
            event = "KXBTCD-26MAY1801"
            ts = pd.Timestamp("2026-05-18T04:41:25Z").value
            sidecar.write_text(
                "\n".join(
                    [
                        json.dumps({"table": "ws_orderbook_top", "event_ticker": event, "received_at_ns": ts}),
                        json.dumps({"table": "signal_scan", "event_ticker": event, "received_at_ns": ts}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            trades = pd.DataFrame(
                [
                    {
                        "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                        "created_at": "2026-05-18T04:41:25.952242+00:00",
                        "event_ticker": event,
                        "market_ticker": "KXBTCD-26MAY1801-T76999.99",
                        "side": "no",
                        "entry_price": 0.68,
                        "official_result": "yes",
                        "official_pnl": -0.70,
                    }
                ]
            )

            with patch.object(audit.duckdb, "connect", side_effect=RuntimeError("database is being used by another process")):
                rows, source_rows = build_audit(
                    trades,
                    [CaptureSource("locked_capture", db_path)],
                    since_utc="2026-05-18T04:17:44Z",
                    pre_close_min=80.0,
                    post_close_min=8.0,
                )

        self.assertEqual(rows["coverage_status"].tolist(), ["REPLAYABLE_FROM_READABLE_CAPTURE"])
        self.assertEqual(rows["best_capture_read_source"].tolist(), ["replay_sidecar_after_live_read_failure"])
        self.assertEqual(int(source_rows["top_rows"].iloc[0]), 1)
        self.assertEqual(int(source_rows["signal_scan_rows"].iloc[0]), 1)
        self.assertEqual(str(source_rows["replay_sidecar_exists"].iloc[0]), "True")

    def test_missing_capture_is_not_replayable(self) -> None:
        trades = pd.DataFrame(
            [
                {
                    "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                    "created_at": "2026-05-18T04:41:25.952242+00:00",
                    "event_ticker": "KXBTCD-26MAY1801",
                    "market_ticker": "KXBTCD-26MAY1801-T76999.99",
                    "side": "no",
                    "entry_price": 0.68,
                    "official_result": "yes",
                    "official_pnl": -0.70,
                }
            ]
        )

        rows, _ = build_audit(
            trades,
            [CaptureSource("missing", Path("does-not-exist.duckdb"))],
            since_utc="2026-05-18T04:17:44Z",
            pre_close_min=80.0,
            post_close_min=8.0,
        )

        self.assertEqual(rows["coverage_status"].tolist(), ["NO_READABLE_CAPTURE_COVERAGE"])


if __name__ == "__main__":
    unittest.main()
