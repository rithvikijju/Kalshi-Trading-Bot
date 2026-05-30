#!/usr/bin/env python3
"""Tests for BTC1H entry59 floor-filter audit."""

from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from pathlib import Path

from scripts.build_btc1h_entry59_floor_filter_audit import build_floor_audit


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
        base_variant="base",
        challenger_variant="entry59",
        direct_trades=tmp / "direct.csv",
        robustness_trades=tmp / "robustness.csv",
        base_live_replay=tmp / "base_live.csv",
        challenger_live_replay=tmp / "entry59_live.csv",
    )


class Btc1hEntry59FloorFilterAuditTests(unittest.TestCase):
    def test_reports_subset_filter_without_independent_market_side_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            write_csv(
                tmp / "direct.csv",
                [
                    {
                        "variant": "base",
                        "split": "holdout",
                        "event_ticker": "E1",
                        "market_ticker": "M1",
                        "side": "yes",
                        "entry_time": "2026-05-01T00:10:00Z",
                        "entry_price": "0.55",
                        "pnl": "0.43",
                    },
                    {
                        "variant": "base",
                        "split": "holdout",
                        "event_ticker": "E2",
                        "market_ticker": "M2",
                        "side": "no",
                        "entry_time": "2026-05-01T01:10:00Z",
                        "entry_price": "0.65",
                        "pnl": "-0.67",
                    },
                    {
                        "variant": "entry59",
                        "split": "holdout",
                        "event_ticker": "E2",
                        "market_ticker": "M2",
                        "side": "no",
                        "entry_time": "2026-05-01T01:10:00Z",
                        "entry_price": "0.65",
                        "pnl": "-0.67",
                    },
                ],
            )
            write_csv(tmp / "robustness.csv", [])
            write_csv(tmp / "base_live.csv", [])
            write_csv(tmp / "entry59_live.csv", [])

            _details, source_rows, summary = build_floor_audit(args(tmp))

            by_label = {row["evidence_label"]: row for row in source_rows}
            self.assertEqual(by_label["direct_holdout"]["status"], "ENTRY59_STRICT_SUBSET_FILTER")
            self.assertEqual(by_label["direct_holdout"]["challenger_market_side_only_rows"], 0)
            self.assertEqual(by_label["direct_holdout"]["base_market_side_only_rows"], 1)
            self.assertEqual(by_label["direct_holdout"]["deleted_low_entry_base_rows"], 1)
            self.assertEqual(summary["status"], "ENTRY59_STRICT_SUBSET_FILTER")
            self.assertEqual(summary["total_challenger_market_side_only_rows"], 0)
            self.assertEqual(summary["total_deleted_low_entry_base_rows"], 1)

    def test_same_market_side_different_execution_is_not_independent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            write_csv(
                tmp / "direct.csv",
                [
                    {
                        "variant": "base",
                        "split": "holdout",
                        "event_ticker": "E1",
                        "market_ticker": "M1",
                        "side": "yes",
                        "entry_time": "2026-05-01T00:10:00Z",
                        "entry_price": "0.55",
                        "pnl": "0.43",
                    },
                    {
                        "variant": "entry59",
                        "split": "holdout",
                        "event_ticker": "E1",
                        "market_ticker": "M1",
                        "side": "yes",
                        "entry_time": "2026-05-01T00:15:00Z",
                        "entry_price": "0.61",
                        "pnl": "0.37",
                    },
                ],
            )
            write_csv(tmp / "robustness.csv", [])
            write_csv(tmp / "base_live.csv", [])
            write_csv(tmp / "entry59_live.csv", [])

            _details, source_rows, summary = build_floor_audit(args(tmp))

            by_label = {row["evidence_label"]: row for row in source_rows}
            self.assertEqual(by_label["direct_holdout"]["status"], "SAME_MARKET_SIDE_DIFFERENT_EXECUTION_ROWS")
            self.assertEqual(by_label["direct_holdout"]["challenger_market_side_only_rows"], 0)
            self.assertEqual(by_label["direct_holdout"]["challenger_exact_only_rows"], 1)
            self.assertEqual(summary["status"], "SAME_MARKET_SIDE_DIFFERENT_EXECUTION_ROWS")
            self.assertEqual(summary["total_challenger_market_side_only_rows"], 0)


if __name__ == "__main__":
    unittest.main()
