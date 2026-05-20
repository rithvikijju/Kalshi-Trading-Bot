#!/usr/bin/env python3
"""Tests for BTC15M post-freeze replay refresh orchestration."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

import pandas as pd

from scripts.refresh_btc15m_postfreeze_replays import replay_command, replay_specs, rest_command
from scripts.fill_btc15m_live_ws_official_results import summarize


def args(**overrides: object) -> argparse.Namespace:
    base = {
        "capture_db": Path("capture.duckdb"),
        "freeze_utc": "2026-05-18T04:17:44Z",
        "sleep": 0.05,
        "dry_run": False,
        "out_dir": Path("unused"),
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class Btc15mPostfreezeReplayRefreshTests(unittest.TestCase):
    def test_specs_cover_frozen_btc15m_forward_paths(self) -> None:
        names = [spec.candidate_name for spec in replay_specs()]
        self.assertEqual(
            names,
            ["q250_firstskip_qty500", "q250_firstskip_qty500_yes", "q1000_yes"],
        )

    def test_q250_yes_replay_command_is_frozen_and_side_specific(self) -> None:
        spec = next(item for item in replay_specs() if item.candidate_name == "q250_firstskip_qty500_yes")
        argv = replay_command(spec, args())
        text = " ".join(argv)

        self.assertEqual(argv[0], sys.executable)
        self.assertIn("--start 2026-05-18T04:17:44Z", text)
        self.assertIn("--side yes", text)
        self.assertIn("--visible-qty-min 250", text)
        self.assertIn("--first-signal-visible-qty-min 500", text)
        self.assertIn("--max-btc-spot-age-sec 10", text)

    def test_q1000_yes_does_not_use_first_signal_qty_filter(self) -> None:
        spec = next(item for item in replay_specs() if item.candidate_name == "q1000_yes")
        argv = replay_command(spec, args())

        self.assertIn("--visible-qty-min", argv)
        self.assertEqual(argv[argv.index("--visible-qty-min") + 1], "1000")
        self.assertNotIn("--first-signal-visible-qty-min", argv)

    def test_rest_command_uses_replay_output_trades_and_candidate_name(self) -> None:
        spec = replay_specs()[0]
        argv = rest_command(spec, args(sleep=0.1))
        text = " ".join(argv)

        self.assertIn("fill_btc15m_live_ws_official_results.py", text)
        self.assertIn("f2_live_ws_trades.parquet", text)
        self.assertIn("--candidate-name q250_firstskip_qty500", text)
        self.assertIn("--sleep 0.1", text)

    def test_empty_rest_summary_keeps_candidate_row(self) -> None:
        summary = summarize(pd.DataFrame(), "q250_firstskip_qty500_yes")

        self.assertEqual(len(summary), 1)
        row = summary.iloc[0].to_dict()
        self.assertEqual(row["candidate"], "q250_firstskip_qty500_yes")
        self.assertEqual(row["rows"], 0)
        self.assertEqual(row["official_filled"], 0)


if __name__ == "__main__":
    unittest.main()
