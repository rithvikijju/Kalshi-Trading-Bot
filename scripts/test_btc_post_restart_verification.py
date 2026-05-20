import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_btc_post_restart_verification import (
    capture_sidecar_gate,
    process_identity_gate,
    read_json,
    resolve_restart_dir,
    started_pid_by_name,
)


class BtcPostRestartVerificationTests(unittest.TestCase):
    def test_read_json_accepts_powershell_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "restart_plan.json"
            path.write_text("\ufeff" + json.dumps({"execute": False}), encoding="utf-8")

            self.assertEqual(read_json(path), {"execute": False})

    def test_resolve_restart_dir_prefers_authorization_packet_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_plan = Path(tmp) / "authoritative_restart_plan"
            fallback = Path(tmp) / "newer_but_not_authoritative"
            auth_plan.mkdir()
            fallback.mkdir()

            self.assertEqual(
                resolve_restart_dir(
                    None,
                    {"latest_restart_plan_dir": str(auth_plan)},
                ),
                auth_plan,
            )

    def test_resolve_restart_dir_explicit_arg_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "explicit"
            auth_plan = Path(tmp) / "auth"
            explicit.mkdir()
            auth_plan.mkdir()

            self.assertEqual(
                resolve_restart_dir(
                    explicit,
                    {"latest_restart_plan_dir": str(auth_plan)},
                ),
                explicit,
            )

    def test_capture_sidecar_gate_requires_restart_freshness_and_rows(self) -> None:
        row = {
            "capture_sidecar_updated_at_utc": "2026-05-18T10:01:00+00:00",
            "capture_health_rows": 1,
            "ws_orderbook_top_rows": 1,
            "signal_scan_rows": 1,
            "capture_sidecar_error": "",
        }

        self.assertEqual(
            capture_sidecar_gate(
                row,
                restart_executed=True,
                restart_utc="2026-05-18T10:00:00+00:00",
                required=True,
            ),
            (True, "ready"),
        )
        self.assertEqual(
            capture_sidecar_gate(
                row,
                restart_executed=True,
                restart_utc="2026-05-18T10:02:00+00:00",
                required=True,
            ),
            (False, "capture_sidecar_stale_before_restart"),
        )

    def test_capture_sidecar_gate_requires_basic_sidecar_rows(self) -> None:
        row = {
            "capture_sidecar_updated_at_utc": "2026-05-18T10:01:00+00:00",
            "capture_health_rows": 1,
            "ws_orderbook_top_rows": 0,
            "signal_scan_rows": 1,
            "capture_sidecar_error": "",
        }

        self.assertEqual(
            capture_sidecar_gate(
                row,
                restart_executed=True,
                restart_utc="2026-05-18T10:00:00+00:00",
                required=True,
            ),
            (False, "capture_sidecar_missing_rows:ws_orderbook_top"),
        )

    def test_process_identity_gate_matches_restart_result_pid(self) -> None:
        result = {"started": [{"name": "target_a", "process_id": 1234}]}
        row = {
            "pids": "1234",
            "process_created_at_utc": "2026-05-18T10:00:03+00:00",
        }

        self.assertEqual(started_pid_by_name(result), {"target_a": "1234"})
        self.assertEqual(
            process_identity_gate(
                row,
                target_name="target_a",
                restart_executed=True,
                expected_started_pids=started_pid_by_name(result),
                plan_created_at="2026-05-18T10:00:00+00:00",
            ),
            (True, "ready", "1234", "2026-05-18T10:00:03+00:00"),
        )

    def test_process_identity_gate_rejects_stale_or_wrong_pid(self) -> None:
        result = {"started": [{"name": "target_a", "process_id": 1234}]}
        expected = started_pid_by_name(result)

        self.assertEqual(
            process_identity_gate(
                {"pids": "9999", "process_created_at_utc": "2026-05-18T10:00:03+00:00"},
                target_name="target_a",
                restart_executed=True,
                expected_started_pids=expected,
                plan_created_at="2026-05-18T10:00:00+00:00",
            )[1],
            "current_pid_does_not_match_restart_result",
        )
        self.assertEqual(
            process_identity_gate(
                {"pids": "1234", "process_created_at_utc": "2026-05-18T09:59:59+00:00"},
                target_name="target_a",
                restart_executed=True,
                expected_started_pids=expected,
                plan_created_at="2026-05-18T10:00:00+00:00",
            )[1],
            "process_created_before_restart_plan",
        )


if __name__ == "__main__":
    unittest.main()
