#!/usr/bin/env python3
"""Tests for BTC1H replay variant overlap audit."""

from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from pathlib import Path

from scripts.build_btc1h_replay_variant_overlap_audit import build_overlap


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
        base_trades=tmp / "base.csv",
        challenger_trades=tmp / "challenger.csv",
        base_variant="base",
        challenger_variant="challenger",
    )


class Btc1hReplayVariantOverlapAuditTests(unittest.TestCase):
    def test_exact_match_reports_no_independent_challenger_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            base_rows = [
                {
                    "variant": "base",
                    "event_ticker": "KXBTCD-26MAY2106",
                    "market_ticker": "KXBTCD-26MAY2106-T77699.99",
                    "side": "no",
                    "entry_received_at_ns": "100",
                    "pnl": "0.29",
                }
            ]
            challenger_rows = [{**base_rows[0], "variant": "challenger"}]
            write_csv(tmp / "base.csv", base_rows)
            write_csv(tmp / "challenger.csv", challenger_rows)

            _details, summary = build_overlap(args(tmp))

            self.assertEqual(summary["status"], "EXACT_ROW_SET_MATCH")
            self.assertTrue(summary["exact_row_set_match"])
            self.assertEqual(summary["independent_challenger_rows"], 0)
            self.assertEqual(summary["pnl_diff"], 0.0)

    def test_challenger_only_rows_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            write_csv(
                tmp / "base.csv",
                [
                    {
                        "variant": "base",
                        "event_ticker": "KXBTCD-26MAY2106",
                        "market_ticker": "KXBTCD-26MAY2106-T77699.99",
                        "side": "no",
                        "entry_received_at_ns": "100",
                        "pnl": "0.29",
                    }
                ],
            )
            write_csv(
                tmp / "challenger.csv",
                [
                    {
                        "variant": "challenger",
                        "event_ticker": "KXBTCD-26MAY2106",
                        "market_ticker": "KXBTCD-26MAY2106-T77699.99",
                        "side": "no",
                        "entry_received_at_ns": "100",
                        "pnl": "0.29",
                    },
                    {
                        "variant": "challenger",
                        "event_ticker": "KXBTCD-26MAY2107",
                        "market_ticker": "KXBTCD-26MAY2107-T77299.99",
                        "side": "no",
                        "entry_received_at_ns": "200",
                        "pnl": "-0.72",
                    },
                ],
            )

            _details, summary = build_overlap(args(tmp))

            self.assertEqual(summary["status"], "HAS_INCREMENTAL_ROWS")
            self.assertFalse(summary["exact_row_set_match"])
            self.assertEqual(summary["independent_challenger_rows"], 1)
            self.assertEqual(summary["challenger_only_rows"], 1)


if __name__ == "__main__":
    unittest.main()
