#!/usr/bin/env python3
"""Safety checks for the guarded BTC paper-shadow restart controller."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import pandas as pd

from scripts import build_btc_restart_authorization_packet as restart_auth
from scripts.build_btc_shadow_restart_preflight import check_fresh_capture_sidecar


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESTART_SCRIPT = PROJECT_ROOT / "scripts" / "restart_btc_paper_shadows.ps1"


class BtcPaperRestartSafetyTests(unittest.TestCase):
    def test_restart_controller_requires_execute_and_ack_before_destructive_actions(self) -> None:
        source = RESTART_SCRIPT.read_text(encoding="utf-8")
        dry_run_guard = source.index("if (-not $Execute)")
        ack_guard = source.index("if (-not $IUnderstandThisRestartsPaperShadows)")
        safety_guard = source.index("$unsafeTargets = @(")
        unmanaged_guard = source.index("if ($unmanagedRows.Count -ne 0 -and (-not $IUnderstandUnmanagedBtcProcessesRemain))")
        first_stop = source.index("Stop-Process")
        first_move = source.index("Move-Item")
        first_start = source.index("Start-Process")
        self.assertLess(dry_run_guard, first_stop)
        self.assertLess(ack_guard, first_stop)
        self.assertLess(safety_guard, first_stop)
        self.assertLess(unmanaged_guard, first_stop)
        self.assertLess(safety_guard, first_move)
        self.assertLess(safety_guard, first_start)

    def test_restart_controller_targets_only_shadow_wrappers(self) -> None:
        source = RESTART_SCRIPT.read_text(encoding="utf-8")
        targets = re.findall(r'Script = "([^"]+)"', source)
        self.assertGreaterEqual(len(targets), 4)
        self.assertNotIn(r"scripts\btc15m_live_capture.py", targets)
        for target in targets:
            self.assertTrue(target.endswith("_shadow.py"), target)
            self.assertNotIn("_live.py", target)

    def test_restart_controller_has_script_safety_check_and_hidden_start(self) -> None:
        source = RESTART_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("function Test-TargetScriptSafety", source)
        self.assertIn("paper_mode_not_locked_in_wrapper", source)
        self.assertIn("wrapper_contains_live_mode_argv", source)
        self.assertIn("Get-UnmanagedMatchingProcesses", source)
        self.assertIn("Get-TargetProcessFromStatusSidecar", source)
        self.assertIn("IUnderstandUnmanagedBtcProcessesRemain", source)
        self.assertIn("-WindowStyle Hidden", source)
        self.assertNotIn("cmd /c", source.lower())

    def test_dry_run_records_safety_fields_without_executing(self) -> None:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(RESTART_SCRIPT),
                "-StartupWaitSec",
                "1",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        self.assertIn("Dry run only. No processes were stopped or started.", completed.stdout)
        plan_match = re.search(r"Plan written to (.+restart_plan\.json)", completed.stdout)
        self.assertIsNotNone(plan_match, completed.stdout)
        plan_path = Path(plan_match.group(1).strip())
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        self.assertFalse(bool(plan["execute"]))
        self.assertIn(r"scripts\btc15m_live_capture.py", plan["untouched_processes"])
        self.assertIn("unmanaged_matching_process_count", plan)
        self.assertIn("unmanaged_matching_processes", plan)
        self.assertIn("process_inspection_warnings", plan)
        for target in plan["targets"]:
            self.assertTrue(bool(target["script_safety_pass"]), target)
            self.assertEqual(str(target["script_safety_reasons"]), "")
            self.assertIn("process_count", target)
            self.assertIn("duplicate_process_count", target)
            self.assertIn("process_hygiene_status", target)

    def test_restart_preflight_proves_capture_status_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = check_fresh_capture_sidecar(
                "btc1h_high_conf80_entry70_no_chase_shadow",
                Path(tmp) / "btc1h_capture.duckdb",
            )

        self.assertEqual(result["fresh_capture_sidecar_status"], "PASS_CAPTURE_SIDECAR")
        self.assertEqual(int(result["fresh_capture_health_rows"]), 1)
        self.assertEqual(int(result["fresh_capture_top_rows"]), 1)
        self.assertEqual(int(result["fresh_capture_signal_rows"]), 1)
        self.assertEqual(int(result["fresh_capture_signal_nonzero_rows"]), 1)
        self.assertEqual(result["fresh_capture_replay_schema_status"], "PASS_REPLAY_SIDECAR_SCHEMA")
        self.assertEqual(result["fresh_capture_replay_signal_missing_fields"], "")
        self.assertEqual(result["fresh_capture_replay_order_missing_fields"], "")

    def _write_authorization_fixture(self, root: Path, *, unmanaged_count: int) -> Namespace:
        preflight_dir = root / "preflight"
        schema_dir = root / "schema"
        gate_dir = root / "gate"
        kill_dir = root / "kill"
        forward_dir = root / "forward"
        for path in [preflight_dir, schema_dir, gate_dir, kill_dir, forward_dir]:
            path.mkdir()

        ledgers = [target["ledger"] for target in restart_auth.TARGETS]
        candidates = [target["candidate"] for target in restart_auth.TARGETS]
        pd.DataFrame(
            {
                "ledger": ledgers,
                "restart_path_status": ["PASS_RESTART_PATH_READY"] * len(ledgers),
                "fresh_schema_status": ["PASS_SCHEMA_READY"] * len(ledgers),
                "fresh_insert_status": ["PASS_INSERT_REALISM_FIELDS"] * len(ledgers),
                "fresh_capture_sidecar_status": ["PASS_CAPTURE_SIDECAR"] * len(ledgers),
                "fresh_capture_replay_schema_status": ["PASS_REPLAY_SIDECAR_SCHEMA"] * len(ledgers),
                "copy_after_schema_status": ["PASS_SCHEMA_READY"] * len(ledgers),
                "copy_insert_status": ["PASS_INSERT_REALISM_FIELDS"] * len(ledgers),
            }
        ).to_csv(preflight_dir / "shadow_restart_preflight_summary.csv", index=False)
        pd.DataFrame(
            {
                "ledger": ledgers,
                "preflight_status": [
                    "FAIL_REALISM_SCHEMA_RESTART_REQUIRED",
                    "DB_MISSING",
                    "FAIL_REALISM_SCHEMA_RESTART_REQUIRED",
                    "FAIL_REALISM_SCHEMA_RESTART_REQUIRED",
                ],
                "restart_required_for_deployable_ledger": [True, False, True, True],
                "schema_column_count": [25, 0, 25, 25],
                "realism_columns_present": [0, 0, 0, 0],
                "realism_columns_missing": [12, 12, 12, 12],
                "paper_filled_rows": [1, 0, 1, 1],
            }
        ).to_csv(schema_dir / "ledger_schema_preflight_summary.csv", index=False)
        pd.DataFrame(
            {
                "ledger": ledgers,
                "gate_status": ["PENDING_CONTROLLED_RESTART"] * len(ledgers),
                "post_restart_official_rows": [0] * len(ledgers),
                "min_post_restart_official_rows": [100, 100, 100, 50],
                "failure_reasons": [""] * len(ledgers),
            }
        ).to_csv(gate_dir / "post_restart_collection_gate_summary.csv", index=False)
        pd.DataFrame(
            {
                "candidate": candidates,
                "production_ready": [False] * len(candidates),
                "action": ["continue_shadow"] * len(candidates),
                "next_step": ["collect"] * len(candidates),
            }
        ).to_csv(kill_dir / "kill_continue_summary.csv", index=False)
        pd.DataFrame(
            {
                "name": [
                    "btc15m_live_capture",
                    "btc15m_q250_qty500_firstskip_shadow",
                    "btc15m_q250_qty500_firstskip_yes_shadow",
                    "btc15m_q1000_yes_shadow",
                    "btc1h_high_conf80_entry70_no_chase_shadow",
                ],
                "pids": ["999", "111,222", "223", "333", "444"],
                "process_count": [1, 2, 1, 1, 1],
                "duplicate_process_count": [0, 1, 0, 0, 0],
                "process_hygiene_status": [
                    "ONE_TARGET_PROCESS",
                    "DUPLICATE_TARGET_PROCESSES",
                    "ONE_TARGET_PROCESS",
                    "ONE_TARGET_PROCESS",
                    "ONE_TARGET_PROCESS",
                ],
                "running": [True, True, True, True, True],
                "paper_filled_rows_since": [0, 1, 0, 1, 1],
                "official_filled_since": [0, 1, 0, 1, 1],
            }
        ).to_csv(forward_dir / "shadow_status.csv", index=False)
        (forward_dir / "run_info.json").write_text(
            json.dumps(
                {
                    "created_at_utc": "2026-05-18T00:00:00+00:00",
                    "duplicate_target_process_count": 1,
                    "duplicate_target_names": "btc15m_q250_qty500_firstskip_shadow",
                    "unmanaged_matching_process_count": unmanaged_count,
                }
            ),
            encoding="utf-8",
        )
        pd.DataFrame(
            [{"pid": 555, "command_line": "python -u scripts\\btc_1hr_high_conf80_no_chase_shadow.py"}]
            if unmanaged_count
            else []
        ).to_csv(forward_dir / "unmanaged_processes.csv", index=False)
        return Namespace(
            restart_preflight_dir=preflight_dir,
            ledger_schema_dir=schema_dir,
            post_restart_gate_dir=gate_dir,
            kill_continue_dir=kill_dir,
            forward_status_dir=forward_dir,
            max_forward_status_age_minutes=10**9,
        )

    def _restart_plan(self) -> dict[str, object]:
        return {
            "targets": [
                {"name": target["ledger"], "script_safety_pass": True, "script_safety_reasons": ""}
                for target in restart_auth.TARGETS
            ]
        }

    def test_authorization_packet_uses_shadow_status_process_hygiene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._write_authorization_fixture(Path(tmp), unmanaged_count=0)
            info = restart_auth.read_json(args.forward_status_dir / "run_info.json")
            rows = restart_auth.build_rows(args, [], self._restart_plan(), info)

        q250 = rows[rows["ledger"].eq("btc15m_q250_qty500_firstskip_shadow")].iloc[0]
        self.assertEqual(q250["current_pids"], "111;222")
        self.assertEqual(int(q250["duplicate_process_count"]), 1)
        self.assertEqual(q250["process_hygiene_status"], "DUPLICATE_TARGET_PROCESSES")
        self.assertEqual(q250["authorization_packet_status"], "READY_FOR_USER_AUTHORIZATION_WITH_DUPLICATE_CLEANUP")
        self.assertTrue(bool(q250["will_stop_existing_processes"]))
        self.assertTrue(bool(q250["will_start_process"]))
        self.assertTrue(bool(q250["will_restart_process"]))
        self.assertFalse(bool(q250["will_start_new_process"]))
        self.assertEqual(q250["observed_process_action"], "restart_with_duplicate_cleanup")

    def test_authorization_packet_reports_start_action_for_absent_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._write_authorization_fixture(Path(tmp), unmanaged_count=0)
            forward_path = args.forward_status_dir / "shadow_status.csv"
            forward = pd.read_csv(forward_path)
            mask = forward["name"].eq("btc1h_high_conf80_entry70_no_chase_shadow")
            forward.loc[mask, "pids"] = ""
            forward.loc[mask, "process_count"] = 0
            forward.loc[mask, "duplicate_process_count"] = 0
            forward.loc[mask, "process_hygiene_status"] = "NOT_RUNNING"
            forward.loc[mask, "running"] = False
            forward.to_csv(forward_path, index=False)
            info = restart_auth.read_json(args.forward_status_dir / "run_info.json")
            rows = restart_auth.build_rows(args, [], self._restart_plan(), info)

        btc1h = rows[rows["ledger"].eq("btc1h_high_conf80_entry70_no_chase_shadow")].iloc[0]
        self.assertEqual(btc1h["authorization_packet_status"], "READY_FOR_USER_AUTHORIZATION_TO_START")
        self.assertNotIn("target_process_state_expected", str(btc1h["pre_authorization_blockers"]))
        self.assertEqual(btc1h["expected_process_state"], "start_or_restart_allowed")
        self.assertEqual(btc1h["observed_process_action"], "start_absent_target")
        self.assertFalse(bool(btc1h["will_stop_existing_processes"]))
        self.assertTrue(bool(btc1h["will_start_process"]))
        self.assertFalse(bool(btc1h["will_restart_process"]))
        self.assertTrue(bool(btc1h["will_start_new_process"]))

    def test_authorization_checklist_does_not_require_capture_process_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._write_authorization_fixture(Path(tmp), unmanaged_count=0)
            info = restart_auth.read_json(args.forward_status_dir / "run_info.json")
            rows = restart_auth.build_rows(args, [], self._restart_plan(), info)
            checklist = restart_auth.checklist_rows(args, rows, [], Path(tmp), self._restart_plan(), info)

        capture_check = checklist[checklist["check"].eq("capture_process_untouched_by_workflow")].iloc[0]
        self.assertTrue(bool(capture_check["pass"]))
        self.assertIn("targeted=False", str(capture_check["evidence"]))
        self.assertIn("running_pids=", str(capture_check["evidence"]))

    def test_authorization_packet_blocks_unmanaged_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._write_authorization_fixture(Path(tmp), unmanaged_count=1)
            info = restart_auth.read_json(args.forward_status_dir / "run_info.json")
            rows = restart_auth.build_rows(args, [], self._restart_plan(), info)
            checklist = restart_auth.checklist_rows(args, rows, [999], Path(tmp), self._restart_plan(), info)

        self.assertTrue(
            rows["authorization_packet_status"].astype(str).eq("BLOCKED_UNMANAGED_PROCESS_DECISION_REQUIRED").all()
        )
        no_unmanaged = checklist[checklist["check"].eq("no_unmanaged_matching_processes")].iloc[0]
        self.assertFalse(bool(no_unmanaged["pass"]))

    def test_authorization_packet_accepts_schema_ready_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._write_authorization_fixture(Path(tmp), unmanaged_count=0)
            schema_path = args.ledger_schema_dir / "ledger_schema_preflight_summary.csv"
            schema = pd.read_csv(schema_path)
            schema["preflight_status"] = "PASS_SCHEMA_READY"
            schema["restart_required_for_deployable_ledger"] = False
            schema.to_csv(schema_path, index=False)
            info = restart_auth.read_json(args.forward_status_dir / "run_info.json")
            rows = restart_auth.build_rows(args, [], self._restart_plan(), info)
            checklist = restart_auth.checklist_rows(args, rows, [999], Path(tmp), self._restart_plan(), info)

        check = checklist[checklist["check"].eq("active_ledger_schema_state_acceptable_for_restart")].iloc[0]
        self.assertTrue(bool(check["pass"]))


if __name__ == "__main__":
    unittest.main()
