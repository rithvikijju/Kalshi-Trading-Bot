#!/usr/bin/env python3
"""Tests for BTC15M candidate overlap audit."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.audit_btc15m_candidate_overlap import candidate_summaries, pairwise_summaries


def frame(candidate: str, events: list[str], pnls: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "candidate": [candidate] * len(events),
            "event_ticker": events,
            "market_ticker": [f"{event}-T" for event in events],
            "side": ["yes"] * len(events),
            "official_result_filled": ["yes"] * len(events),
            "proxy_result": ["yes"] * len(events),
            "pnl_official_rest_2c": pnls,
            "win_pnl_official_rest_2c": [1.0 if pnl > 0 else 0.0 for pnl in pnls],
        }
    )


class Btc15mCandidateOverlapTests(unittest.TestCase):
    def test_pairwise_overlap_detects_identical_official_event_sets(self) -> None:
        loaded = {
            "full_causal": {
                "q250_firstskip_qty500_yes": frame("q250_firstskip_qty500_yes", ["A", "B"], [0.5, -0.5]),
                "q1000_yes": frame("q1000_yes", ["A", "B"], [0.5, -0.5]),
            }
        }

        pairwise = pairwise_summaries(loaded)
        row = pairwise[
            pairwise["left_candidate"].eq("q250_firstskip_qty500_yes")
            & pairwise["right_candidate"].eq("q1000_yes")
        ].iloc[0]

        self.assertEqual(row["overlap_status"], "identical_official_event_set")
        self.assertEqual(int(row["shared_official_events"]), 2)
        self.assertEqual(float(row["official_event_overlap_rate"]), 1.0)

    def test_candidate_summary_counts_duplicate_events_and_mismatches(self) -> None:
        loaded = {
            "postfreeze": {
                "q250": pd.DataFrame(
                    {
                        "event_ticker": ["A", "A", "B"],
                        "side": ["yes", "yes", "no"],
                        "official_result_filled": ["yes", "yes", "no"],
                        "proxy_result": ["yes", "no", "yes"],
                        "pnl_official_rest_2c": [0.5, -0.5, 0.5],
                        "win_pnl_official_rest_2c": [1.0, 0.0, 1.0],
                    }
                )
            }
        }

        summary = candidate_summaries(loaded).iloc[0]

        self.assertEqual(int(summary["rows"]), 3)
        self.assertEqual(int(summary["unique_events"]), 2)
        self.assertEqual(int(summary["duplicate_event_rows"]), 1)
        self.assertEqual(int(summary["proxy_official_mismatches"]), 2)


if __name__ == "__main__":
    unittest.main()
