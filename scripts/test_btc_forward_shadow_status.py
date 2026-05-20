import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_btc_forward_shadow_status import (
    load_capture_status_sidecar,
    parse_iso_utc,
    process_hygiene,
    source_freshness,
    summarize_duckdb,
    unmanaged_processes,
)


class BtcForwardShadowStatusTests(unittest.TestCase):
    def test_capture_sidecar_populates_status_when_db_unreadable_or_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            capture_db = Path(tmp) / "locked_capture.duckdb"
            sidecar = capture_db.with_name(capture_db.name + ".status.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "updated_at_utc": "2026-05-18T10:00:00+00:00",
                        "rows_by_table": {
                            "capture_health": 3,
                            "ws_orderbook_top": 7,
                            "signal_scan": 5,
                            "order_decision": 2,
                        },
                        "latest_utc_by_table": {
                            "capture_health": "2026-05-18T09:59:50+00:00",
                            "ws_orderbook_top": "2026-05-18T09:59:55+00:00",
                            "signal_scan": "2026-05-18T09:59:57+00:00",
                        },
                        "signal_scan_nonzero_candidate_rows": 1,
                        "signal_scan_action_counts": {"none": 4, "selected": 1},
                        "signal_scan_latest_action": "none",
                        "signal_scan_latest_detail": "h02_ttl_outside",
                    }
                ),
                encoding="utf-8",
            )

            sidecar_data, sidecar_error = load_capture_status_sidecar(capture_db)
            self.assertEqual(sidecar_error, "")
            self.assertEqual(sidecar_data["capture_read_source"], "sidecar_after_live_lock")
            self.assertEqual(sidecar_data["ws_orderbook_top_rows"], 7)
            self.assertEqual(sidecar_data["signal_scan_nonzero_candidate_rows"], 1)
            self.assertEqual(sidecar_data["signal_scan_latest_detail"], "h02_ttl_outside")

            summary = summarize_duckdb(capture_db, retries=1, sleep_s=0.0)
            self.assertFalse(summary["capture_db_exists"])
            self.assertEqual(summary["capture_read_source"], "sidecar_after_live_lock")
            self.assertEqual(summary["order_decision_rows"], 2)

    def test_source_freshness_detects_process_started_before_engine_update(self) -> None:
        status = source_freshness(
            "btc_1hr_high_conf80_entry70_no_chase_shadow.py",
            "btc_1hr_research_live.py",
            ["2000-01-01T00:00:00Z"],
        )

        self.assertEqual(status["source_freshness_status"], "RUNNING_SOURCE_STALE_RESTART_REQUIRED")
        self.assertTrue(status["process_predates_latest_source"])

    def test_parse_iso_utc_handles_z_suffix(self) -> None:
        parsed = parse_iso_utc("2026-05-18T02:41:59.1501390Z")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo.utcoffset(parsed).total_seconds(), 0)

    def test_process_hygiene_flags_duplicate_target_pids(self) -> None:
        status = process_hygiene(
            [
                {"pid": "111", "created_at_utc": "2026-05-18T01:00:00Z", "command_line": "script.py"},
                {"pid": "222", "created_at_utc": "2026-05-18T02:00:00Z", "command_line": "script.py"},
            ]
        )

        self.assertEqual(status["process_count"], 2)
        self.assertEqual(status["duplicate_process_count"], 1)
        self.assertEqual(status["process_hygiene_status"], "DUPLICATE_TARGET_PROCESSES")

    def test_unmanaged_processes_excludes_known_targets_and_keeps_extra_btc_processes(self) -> None:
        processes = [
            {"pid": "111", "command_line": "python scripts\\btc15m_live_capture.py"},
            {"pid": "222", "command_line": "python scripts\\btc_1hr_high_conf80_no_chase_shadow.py"},
        ]

        unmanaged = unmanaged_processes(processes, {"btc15m_live_capture.py"})

        self.assertEqual([row["pid"] for row in unmanaged], ["222"])


if __name__ == "__main__":
    unittest.main()
