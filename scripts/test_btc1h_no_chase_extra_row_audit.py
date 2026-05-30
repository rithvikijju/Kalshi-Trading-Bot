#!/usr/bin/env python3
"""Tests for BTC1H broad no-chase extra-row audit."""

from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from pathlib import Path

from scripts.build_btc1h_no_chase_extra_row_audit import build_audit


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def args(tmp: Path) -> argparse.Namespace:
    return argparse.Namespace(
        out_dir=tmp / "out",
        no_chase_variant="no_chase",
        entry70_variant="entry70",
        stress_cents=2.0,
        direct_trades=tmp / "direct.csv",
        robustness_trades=tmp / "robustness.csv",
        live_fullscan_trades=tmp / "live.csv",
        live_fullscan_no_chase_trades=None,
        live_fullscan_entry70_trades=None,
    )


class Btc1hNoChaseExtraRowAuditTests(unittest.TestCase):
    def test_negative_gt70_live_extra_rows_drive_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            write_csv(tmp / "direct.csv", [])
            write_csv(tmp / "live.csv", [])
            write_csv(
                tmp / "robustness.csv",
                [
                    {
                        "variant": "entry70",
                        "source": "websocket",
                        "cadence_sec": 1,
                        "event_ticker": "E1",
                        "market_ticker": "M1",
                        "side": "yes",
                        "entry_time": "2026-05-01T00:10:00Z",
                        "entry_price": "0.65",
                        "win": "true",
                    },
                    {
                        "variant": "no_chase",
                        "source": "websocket",
                        "cadence_sec": 1,
                        "event_ticker": "E1",
                        "market_ticker": "M1",
                        "side": "yes",
                        "entry_time": "2026-05-01T00:10:00Z",
                        "entry_price": "0.65",
                        "win": "true",
                    },
                    {
                        "variant": "no_chase",
                        "source": "websocket",
                        "cadence_sec": 1,
                        "event_ticker": "E2",
                        "market_ticker": "M2",
                        "side": "no",
                        "entry_time": "2026-05-01T01:10:00Z",
                        "entry_price": "0.75",
                        "win": "false",
                    },
                ],
            )

            _details, source_rows, summary = build_audit(args(tmp))

            by_label = {row["evidence_label"]: row for row in source_rows}
            self.assertEqual(by_label["live_ws_stride1s"]["status"], "EXTRA_GT70_ROWS_NEGATIVE")
            self.assertEqual(by_label["live_ws_stride1s"]["extra_gt70_rows"], 1)
            self.assertLess(float(by_label["live_ws_stride1s"]["extra_gt70_pnl"]), 0.0)
            self.assertEqual(summary["status"], "NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING")
            self.assertEqual(summary["live_ws_negative_extra_label_count"], 1)
            self.assertEqual(summary["live_ws_stride1_extra_gt70_rows"], 1)

    def test_absent_variant_source_is_skipped_not_counted_as_deleted_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            write_csv(tmp / "direct.csv", [])
            write_csv(tmp / "live.csv", [])
            write_csv(
                tmp / "robustness.csv",
                [
                    {
                        "variant": "entry70",
                        "source": "websocket",
                        "cadence_sec": 1,
                        "event_ticker": "E1",
                        "market_ticker": "M1",
                        "side": "yes",
                        "entry_time": "2026-05-01T00:10:00Z",
                        "entry_price": "0.65",
                        "win": "true",
                    }
                ],
            )

            _details, source_rows, summary = build_audit(args(tmp))

            self.assertEqual(source_rows, [])
            self.assertEqual(summary["status"], "MISSING_OR_EMPTY_INPUTS")
            self.assertEqual(summary["compared_evidence_labels"], 0)
            self.assertIn("variant_not_present:no_chase", summary["missing_sources"])


if __name__ == "__main__":
    unittest.main()
