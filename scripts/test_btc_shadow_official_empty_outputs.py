import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from scripts.build_btc_post_restart_collection_gate import LEDGER_SPECS, gate_ledger, read_csv
from scripts.build_btc_post_restart_collection_gate import read_json as read_gate_json
from scripts.check_btc_shadow_official_settlement import (
    SHADOW_OFFICIAL_TRADE_COLUMNS,
    summarize_by_policy,
    write_csv,
)


class BtcShadowOfficialEmptyOutputTests(unittest.TestCase):
    def test_shadow_official_trade_csv_keeps_headers_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_official_trades.csv"

            write_csv(path, [], SHADOW_OFFICIAL_TRADE_COLUMNS)

            df = pd.read_csv(path)
            self.assertEqual(list(df.columns), SHADOW_OFFICIAL_TRADE_COLUMNS)
            self.assertTrue(df.empty)

    def test_post_restart_gate_reader_accepts_zero_byte_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            path.write_text("", encoding="utf-8")

            df = read_csv(path)

            self.assertTrue(df.empty)

    def test_post_restart_gate_json_accepts_powershell_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "restart_result.json"
            path.write_text('\ufeff{"completed_at": "2026-05-18T22:54:17Z"}', encoding="utf-8")

            self.assertEqual(read_gate_json(path), {"completed_at": "2026-05-18T22:54:17Z"})

    def test_post_restart_gate_handles_empty_headered_trades_after_restart(self) -> None:
        trades = pd.DataFrame(columns=SHADOW_OFFICIAL_TRADE_COLUMNS)
        args = SimpleNamespace(
            max_quote_age_ms=250.0,
            min_btc15m_official_rows=100,
            min_btc1h_official_rows=50,
        )

        row = gate_ledger(
            trades,
            LEDGER_SPECS[0],
            restart_executed=True,
            restart_utc="2026-05-18T22:54:17Z",
            restart_source="restart_result",
            args=args,
        )

        self.assertEqual(row["gate_status"], "NO_POST_RESTART_ROWS")
        self.assertEqual(row["failure_reasons"], "no_post_restart_paper_rows;too_few_post_restart_official_rows")

    def test_policy_summary_accepts_null_policy_fields(self) -> None:
        rows = [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "signal_strategy": None,
                "model_ttl_policy": None,
                "model_policy_version": None,
                "status": "paper_filled",
                "official_result": "yes",
                "official_pnl": 0.24,
                "official_premium": 0.76,
                "official_win": True,
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "signal_strategy": "high_conf_80_entry70_no_chase",
                "model_ttl_policy": "scan_time_close_minus_now_v1",
                "model_policy_version": "btc1h_live_model_20260522_scan_ttl_v1",
                "status": "paper_filled",
                "official_result": "no",
                "official_pnl": -0.61,
                "official_premium": 0.61,
                "official_win": False,
            },
        ]

        summary = summarize_by_policy(rows, since_utc="")

        self.assertEqual(len(summary), 4)
        blank_policy = [
            row
            for row in summary
            if row["scope"] == "all"
            and row["signal_strategy"] == ""
            and row["model_ttl_policy"] == ""
            and row["model_policy_version"] == ""
        ][0]
        self.assertEqual(blank_policy["official_filled_rows"], 1)


if __name__ == "__main__":
    unittest.main()
