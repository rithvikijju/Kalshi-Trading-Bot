#!/usr/bin/env python3
"""Regression tests for BTC15M live-replay realism checks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.backtest_btc15m_f2_live_ws_holdout import select_f2_trades
from scripts.build_btc_forward_consistency_audit import replay_metrics


def replay_row(event_ticker: str, received_at_ns: int, btc_spot_age_sec: float) -> dict[str, object]:
    return {
        "received_at_ns": received_at_ns,
        "received_at_utc": pd.Timestamp("2026-05-18T08:48:10Z"),
        "event_ticker": event_ticker,
        "market_ticker": f"{event_ticker}-00",
        "seq": 1,
        "yes_ask": 0.55,
        "yes_ask_qty": 800.0,
        "no_ask": 0.46,
        "no_ask_qty": 800.0,
        "spread_cents": 1.0,
        "ttl_min": 11.0,
        "btc_spot_model": 76850.0,
        "btc_spot_age_sec": btc_spot_age_sec,
        "rv_60m": 0.25,
        "quote_speed_cents": 0.0,
        "floor_strike": 76900.0,
        "close_time": pd.Timestamp("2026-05-18T09:00:00Z"),
        "official_result": "",
        "close_btc_spot": 76980.0,
        "close_btc_time": pd.Timestamp("2026-05-18T09:00:00Z"),
        "proxy_result": "yes",
        "proxy_distance_usd": 80.0,
        "near_strike_proxy": False,
        "lognormal_p_yes": 0.20,
    }


class BtcForwardReplayRealismTests(unittest.TestCase):
    def test_select_f2_trades_excludes_stale_btc_spot_by_default(self) -> None:
        rows = [
            replay_row("KXBTC15M-STALE", 1, 20.0),
            replay_row("KXBTC15M-FRESH", 2, 5.0),
        ]
        trades, info = select_f2_trades(
            pd.DataFrame(rows),
            fair_p_min=0.60,
            edge_cents_min=12.0,
            ttl_min=10.0,
            ttl_max=12.0,
            spread_max_cents=2.0,
            entry_min=0.02,
            entry_max=0.50,
            visible_qty_min=250.0,
            side="both",
            first_signal_visible_qty_min=500.0,
        )

        self.assertEqual(info["raw_hits"], 1)
        self.assertEqual(info["first_signals"], 1)
        self.assertEqual(trades["event_ticker"].tolist(), ["KXBTC15M-FRESH"])

    def test_replay_metrics_prefers_rest_official_summary_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "run_info.json").write_text(
                json.dumps(
                    {
                        "capture_end_utc": "2026-05-18T09:10:00Z",
                        "quote_rows_after_meta": 100,
                        "f2_raw_hits": 6,
                        "f2_first_signals": 1,
                        "f2_signals_closed_with_proxy": 1,
                    }
                ),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {"result_mode": "official_2c_subset", "trades": 0, "pnl": 0.0},
                    {"result_mode": "proxy_2c", "trades": 1, "pnl": -0.5},
                ]
            ).to_csv(path / "f2_live_ws_summary.csv", index=False)
            pd.DataFrame(
                [{"candidate": "q250", "official_trades": 1, "official_pnl": -0.5}]
            ).to_csv(path / "summary.csv", index=False)

            metrics = replay_metrics(path)

        self.assertEqual(metrics["postfreeze_replay_official_source"], "rest_fill")
        self.assertEqual(metrics["postfreeze_replay_official_trades"], 1)
        self.assertEqual(metrics["postfreeze_replay_official_pnl"], -0.5)


if __name__ == "__main__":
    unittest.main()
