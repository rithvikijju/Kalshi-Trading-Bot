#!/usr/bin/env python3
"""Build a non-disruptive BTC paper-shadow restart authorization packet.

This script does not stop, start, migrate, or archive anything. It gathers the
current process state plus restart/gate artifacts so the next collection phase
can be approved and audited without ad hoc process management.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_restart_authorization_packet_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

TARGETS = [
    {
        "ledger": "btc15m_q250_qty500_firstskip_shadow",
        "candidate": "q250_firstskip_qty500",
        "family": "BTC15M",
        "script": r"scripts\btc15m_f2_q250_qty500_firstskip_shadow.py",
        "min_post_restart_official_rows": 100,
        "requires_existing_process": True,
    },
    {
        "ledger": "btc15m_q250_qty500_firstskip_yes_shadow",
        "candidate": "q250_firstskip_qty500_yes",
        "family": "BTC15M",
        "script": r"scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        "min_post_restart_official_rows": 100,
        "requires_existing_process": False,
    },
    {
        "ledger": "btc15m_q1000_yes_shadow",
        "candidate": "q1000_yes",
        "family": "BTC15M",
        "script": r"scripts\btc15m_f2_q1000_yes_shadow.py",
        "min_post_restart_official_rows": 100,
        "requires_existing_process": True,
    },
    {
        "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
        "candidate": "btc1h_high_conf80_entry70_no_chase",
        "family": "BTC1H",
        "script": r"scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py",
        "min_post_restart_official_rows": 50,
        "requires_existing_process": True,
    },
]

CAPTURE_SCRIPT = r"scripts\btc15m_live_capture.py"
DRY_RUN_COMMAND = "powershell -ExecutionPolicy Bypass -File scripts\\restart_btc_paper_shadows.ps1"
EXECUTE_COMMAND = (
    "powershell -ExecutionPolicy Bypass -File scripts\\restart_btc_paper_shadows.ps1 "
    "-Execute -IUnderstandThisRestartsPaperShadows"
)
EXECUTE_COMMAND_AFTER_UNMANAGED_DECISION = (
    "powershell -ExecutionPolicy Bypass -File scripts\\restart_btc_paper_shadows.ps1 "
    "-Execute -IUnderstandThisRestartsPaperShadows -IUnderstandUnmanagedBtcProcessesRemain"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC paper-shadow restart authorization packet.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--restart-plan-dir", type=Path, default=None)
    p.add_argument("--restart-preflight-dir", type=Path, default=BACKTEST_ROOT / "btc_shadow_restart_preflight_latest_codex")
    p.add_argument("--ledger-schema-dir", type=Path, default=BACKTEST_ROOT / "btc_ledger_schema_preflight_latest_codex")
    p.add_argument("--post-restart-gate-dir", type=Path, default=BACKTEST_ROOT / "btc_post_restart_collection_gate_latest_codex")
    p.add_argument("--kill-continue-dir", type=Path, default=BACKTEST_ROOT / "btc_kill_continue_latest_codex")
    p.add_argument("--forward-status-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex")
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def latest_restart_plan_dir() -> Path | None:
    matches = [
        path
        for path in BACKTEST_ROOT.glob("btc_paper_shadow_controlled_restart_*")
        if path.is_dir() and (path / "restart_plan.json").exists()
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: (path / "restart_plan.json").stat().st_mtime)


def load_restart_plan(plan_dir: Path | None) -> tuple[Path | None, dict[str, Any]]:
    resolved = plan_dir if plan_dir is not None else latest_restart_plan_dir()
    if resolved is None:
        return None, {}
    return resolved, read_json(resolved / "restart_plan.json")


def first_row(df: pd.DataFrame, **filters: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    mask = pd.Series(True, index=df.index)
    for col, value in filters.items():
        if col not in df.columns:
            return pd.DataFrame()
        mask &= df[col].astype(str).eq(str(value))
    return df.loc[mask].head(1).copy()


def scalar(df: pd.DataFrame, col: str, default: Any = "") -> Any:
    if df.empty or col not in df.columns:
        return default
    value = df.iloc[0].get(col, default)
    if pd.isna(value):
        return default
    return value


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def intish(value: Any, default: int = 0) -> int:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return default
    return out


def split_pids(value: Any) -> list[int]:
    raw = "" if value is None else str(value)
    if not raw or raw.lower() in {"nan", "none", "<na>"}:
        return []
    pids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pids.append(int(part))
        except ValueError:
            continue
    return sorted(pids)


def process_snapshot() -> list[dict[str, Any]]:
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*Kalshi-Trading-Bot*' -or "
        "$_.CommandLine -like '*btc15m*' -or $_.CommandLine -like '*btc_1hr*' -or "
        "$_.CommandLine -like '*predexon*' } | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Depth 3"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return []
    text = proc.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "process_id": row.get("ProcessId"),
                "command_line": row.get("CommandLine", ""),
            }
        )
    return rows


def pids_for(processes: list[dict[str, Any]], script: str) -> list[int]:
    needle = script.replace("/", "\\").lower()
    pids: list[int] = []
    for proc in processes:
        cmd = str(proc.get("command_line") or "").replace("/", "\\").lower()
        if needle in cmd:
            try:
                pids.append(int(proc["process_id"]))
            except Exception:
                continue
    return sorted(pids)


def load_forward_status(status_dir: Path) -> pd.DataFrame:
    current = read_csv(status_dir / "shadow_status.csv")
    if not current.empty:
        return current
    return read_csv(status_dir / "forward_shadow_summary.csv")


def build_rows(
    args: argparse.Namespace,
    processes: list[dict[str, Any]],
    restart_plan: dict[str, Any],
    forward_info: dict[str, Any],
) -> pd.DataFrame:
    preflight = read_csv(args.restart_preflight_dir / "shadow_restart_preflight_summary.csv")
    schema = read_csv(args.ledger_schema_dir / "ledger_schema_preflight_summary.csv")
    gate = read_csv(args.post_restart_gate_dir / "post_restart_collection_gate_summary.csv")
    kill = read_csv(args.kill_continue_dir / "kill_continue_summary.csv")
    forward = load_forward_status(args.forward_status_dir)
    unmanaged_count = intish(forward_info.get("unmanaged_matching_process_count", 0))
    duplicate_target_names = str(forward_info.get("duplicate_target_names", "") or "")

    rows: list[dict[str, Any]] = []
    plan_targets = {
        str(row.get("name", "")): row
        for row in restart_plan.get("targets", [])
        if isinstance(row, dict)
    }
    for target in TARGETS:
        ledger = target["ledger"]
        candidate = target["candidate"]
        pre = first_row(preflight, ledger=ledger)
        sch = first_row(schema, ledger=ledger)
        gat = first_row(gate, ledger=ledger)
        kil = first_row(kill, candidate=candidate)
        fwd = first_row(forward, name=ledger)
        fwd_pids = split_pids(scalar(fwd, "pids", ""))
        snapshot_pids = pids_for(processes, str(target["script"]))
        current_pids = fwd_pids or snapshot_pids
        process_count = intish(scalar(fwd, "process_count", len(current_pids)), len(current_pids))
        duplicate_process_count = intish(
            scalar(fwd, "duplicate_process_count", max(0, process_count - 1)),
            max(0, process_count - 1),
        )
        process_hygiene_status = str(
            scalar(
                fwd,
                "process_hygiene_status",
                "NOT_RUNNING"
                if process_count == 0
                else "DUPLICATE_TARGET_PROCESSES"
                if duplicate_process_count
                else "ONE_TARGET_PROCESS",
            )
        )
        plan_target = plan_targets.get(ledger, {})
        plan_safety_pass = boolish(plan_target.get("script_safety_pass", False))
        requires_existing_process = bool(target.get("requires_existing_process", True))
        script_path = PROJECT_ROOT / str(target["script"])
        active_schema_status = str(scalar(sch, "preflight_status", ""))
        active_schema_ok_for_authorization = boolish(
            scalar(sch, "restart_required_for_deployable_ledger", False)
        ) or active_schema_status == "DB_MISSING"
        checks = {
            "target_process_state_expected": process_count > 0 if requires_existing_process else process_count == 0,
            "target_script_exists": script_path.exists(),
            "restart_path_ready": scalar(pre, "restart_path_status") == "PASS_RESTART_PATH_READY",
            "fresh_schema_ready": scalar(pre, "fresh_schema_status") == "PASS_SCHEMA_READY",
            "fresh_insert_ready": scalar(pre, "fresh_insert_status") == "PASS_INSERT_REALISM_FIELDS",
            "fresh_capture_sidecar_ready": scalar(pre, "fresh_capture_sidecar_status") == "PASS_CAPTURE_SIDECAR",
            "active_schema_requires_restart_or_new_db": active_schema_ok_for_authorization,
            "post_restart_gate_waiting": scalar(gat, "gate_status") == "PENDING_CONTROLLED_RESTART",
            "no_post_restart_rows_counted": int(float(scalar(gat, "post_restart_official_rows", 0) or 0)) == 0,
            "not_production_ready": not boolish(scalar(kil, "production_ready", False)),
            "latest_dry_run_target_safety_passed": plan_safety_pass,
        }
        blockers = [name for name, passed in checks.items() if not passed]
        warnings: list[str] = []
        if duplicate_process_count:
            warnings.append("duplicate_target_processes_would_be_stopped_by_restart")
        if unmanaged_count:
            blockers.append("unmanaged_matching_processes_require_explicit_decision")
            warnings.append("unmanaged_matching_processes_not_touched_by_restart")

        if all(checks.values()) and unmanaged_count:
            status = "BLOCKED_UNMANAGED_PROCESS_DECISION_REQUIRED"
        elif all(checks.values()) and duplicate_process_count:
            status = "READY_FOR_USER_AUTHORIZATION_WITH_DUPLICATE_CLEANUP"
        elif all(checks.values()):
            status = "READY_FOR_USER_AUTHORIZATION" if requires_existing_process else "READY_FOR_USER_AUTHORIZATION_TO_START"
        else:
            status = "NEEDS_REVIEW_BEFORE_START_RESTART"
        rows.append(
            {
                "family": target["family"],
                "candidate": candidate,
                "ledger": ledger,
                "target_script": target["script"],
                "current_pids": ";".join(str(pid) for pid in current_pids),
                "process_count": process_count,
                "duplicate_process_count": duplicate_process_count,
                "process_hygiene_status": process_hygiene_status,
                "duplicate_target_process_cleanup_required": bool(duplicate_process_count),
                "unmanaged_matching_process_count": unmanaged_count,
                "duplicate_target_names": duplicate_target_names,
                "authorization_packet_status": status,
                "pre_authorization_blockers": ";".join(blockers),
                "process_cleanup_warnings": ";".join(warnings),
                "user_permission_required": True,
                "latest_restart_plan_script_safety_pass": plan_target.get("script_safety_pass", ""),
                "latest_restart_plan_script_safety_reasons": plan_target.get("script_safety_reasons", ""),
                "will_restart_process": requires_existing_process,
                "will_start_new_process": not requires_existing_process,
                "will_archive_trade_db_by_default": True,
                "live_capture_untouched": True,
                "restart_path_status": scalar(pre, "restart_path_status", ""),
                "fresh_schema_status": scalar(pre, "fresh_schema_status", ""),
                "fresh_insert_status": scalar(pre, "fresh_insert_status", ""),
                "fresh_capture_sidecar_status": scalar(pre, "fresh_capture_sidecar_status", ""),
                "fresh_capture_sidecar_error": scalar(pre, "fresh_capture_sidecar_error", ""),
                "copy_after_schema_status": scalar(pre, "copy_after_schema_status", ""),
                "copy_insert_status": scalar(pre, "copy_insert_status", ""),
                "active_ledger_schema_status": scalar(sch, "preflight_status", ""),
                "active_schema_columns": scalar(sch, "schema_column_count", ""),
                "active_realism_columns_present": scalar(sch, "realism_columns_present", ""),
                "active_realism_columns_missing": scalar(sch, "realism_columns_missing", ""),
                "active_paper_filled_rows": scalar(sch, "paper_filled_rows", ""),
                "post_restart_gate_status": scalar(gat, "gate_status", ""),
                "post_restart_official_rows": scalar(gat, "post_restart_official_rows", ""),
                "min_post_restart_official_rows": scalar(
                    gat,
                    "min_post_restart_official_rows",
                    target["min_post_restart_official_rows"],
                ),
                "post_restart_failure_reasons": scalar(gat, "failure_reasons", ""),
                "shadow_running_in_forward_status": scalar(fwd, "running", ""),
                "paper_filled_rows_since": scalar(fwd, "paper_filled_rows_since", ""),
                "official_filled_since": scalar(fwd, "official_filled_since", ""),
                "kill_continue_action": scalar(kil, "action", ""),
                "kill_continue_next_step": scalar(kil, "next_step", ""),
            }
        )
    return pd.DataFrame(rows)


def checklist_rows(
    summary: pd.DataFrame,
    capture_pids: list[int],
    restart_plan_dir: Path | None,
    restart_plan: dict[str, Any],
    forward_info: dict[str, Any],
) -> pd.DataFrame:
    plan_targets = restart_plan.get("targets", []) if isinstance(restart_plan.get("targets", []), list) else []
    plan_execute = boolish(restart_plan.get("execute", False)) if restart_plan else False
    plan_target_safety_pass = bool(plan_targets) and all(boolish(row.get("script_safety_pass", False)) for row in plan_targets if isinstance(row, dict))
    duplicate_process_count = int(summary["duplicate_process_count"].fillna(0).astype(int).sum()) if "duplicate_process_count" in summary.columns else 0
    duplicate_target_names = str(forward_info.get("duplicate_target_names", "") or "")
    unmanaged_count = intish(forward_info.get("unmanaged_matching_process_count", 0))
    checks = [
        {
            "check": "capture_process_untouched_by_workflow",
            "pass": len(capture_pids) > 0,
            "evidence": ";".join(str(pid) for pid in capture_pids),
            "required_before_execute": True,
        },
        {
            "check": "forward_process_hygiene_snapshot_loaded",
            "pass": bool(forward_info),
            "evidence": f"created_at_utc={forward_info.get('created_at_utc', '')}",
            "required_before_execute": True,
        },
        {
            "check": "no_unmanaged_matching_processes",
            "pass": unmanaged_count == 0,
            "evidence": f"unmanaged_matching_process_count={unmanaged_count}",
            "required_before_execute": True,
        },
        {
            "check": "duplicate_target_processes_disclosed",
            "pass": True,
            "evidence": f"duplicate_process_count={duplicate_process_count}; duplicate_target_names={duplicate_target_names}",
            "required_before_execute": False,
        },
        {
            "check": "all_target_restart_paths_ready",
            "pass": bool(summary["restart_path_status"].astype(str).eq("PASS_RESTART_PATH_READY").all()),
            "evidence": ";".join(summary["restart_path_status"].astype(str).tolist()),
            "required_before_execute": True,
        },
        {
            "check": "all_active_ledgers_need_restart_or_new_db_for_deployable_evidence",
            "pass": bool(
                summary["active_ledger_schema_status"].astype(str).isin(
                    ["FAIL_REALISM_SCHEMA_RESTART_REQUIRED", "DB_MISSING"]
                ).all()
            ),
            "evidence": ";".join(summary["active_ledger_schema_status"].astype(str).tolist()),
            "required_before_execute": True,
        },
        {
            "check": "post_restart_gate_waits_for_controlled_restart",
            "pass": bool(summary["post_restart_gate_status"].astype(str).eq("PENDING_CONTROLLED_RESTART").all()),
            "evidence": ";".join(summary["post_restart_gate_status"].astype(str).tolist()),
            "required_before_execute": True,
        },
        {
            "check": "latest_restart_dry_run_plan_exists",
            "pass": restart_plan_dir is not None and bool(restart_plan),
            "evidence": str(restart_plan_dir or ""),
            "required_before_execute": True,
        },
        {
            "check": "latest_restart_dry_run_did_not_execute",
            "pass": bool(restart_plan) and not plan_execute,
            "evidence": f"execute={plan_execute}",
            "required_before_execute": True,
        },
        {
            "check": "latest_restart_dry_run_script_safety_passed",
            "pass": plan_target_safety_pass,
            "evidence": ";".join(
                f"{row.get('name', '')}:{row.get('script_safety_pass', '')}:{row.get('script_safety_reasons', '')}"
                for row in plan_targets
                if isinstance(row, dict)
            ),
            "required_before_execute": True,
        },
        {
            "check": "explicit_user_authorization_still_required",
            "pass": True,
            "evidence": "Do not execute restart unless user explicitly approves the execute command.",
            "required_before_execute": True,
        },
    ]
    return pd.DataFrame(checks)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    processes = process_snapshot()
    restart_plan_dir, restart_plan = load_restart_plan(args.restart_plan_dir)
    forward_info = read_json(args.forward_status_dir / "run_info.json")
    unmanaged = read_csv(args.forward_status_dir / "unmanaged_processes.csv")
    forward = load_forward_status(args.forward_status_dir)
    summary = build_rows(args, processes, restart_plan, forward_info)
    capture_row = first_row(forward, name="btc15m_live_capture")
    capture_pids = split_pids(scalar(capture_row, "pids", "")) or pids_for(processes, CAPTURE_SCRIPT)
    checklist = checklist_rows(summary, capture_pids, restart_plan_dir, restart_plan, forward_info)
    ready_statuses = {
        "READY_FOR_USER_AUTHORIZATION",
        "READY_FOR_USER_AUTHORIZATION_TO_START",
        "READY_FOR_USER_AUTHORIZATION_WITH_DUPLICATE_CLEANUP",
    }
    ready_count = int(summary["authorization_packet_status"].astype(str).isin(ready_statuses).sum())
    all_ready = bool(ready_count == len(summary) and checklist["pass"].astype(bool).all())
    duplicate_process_count = int(summary["duplicate_process_count"].fillna(0).astype(int).sum())
    unmanaged_count = intish(forward_info.get("unmanaged_matching_process_count", 0))

    summary.to_csv(args.out_dir / "restart_authorization_summary.csv", index=False)
    checklist.to_csv(args.out_dir / "restart_authorization_checklist.csv", index=False)
    pd.DataFrame(processes).to_csv(args.out_dir / "process_snapshot.csv", index=False)
    unmanaged.to_csv(args.out_dir / "unmanaged_processes.csv", index=False)

    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "targets": len(TARGETS),
        "ready_for_user_authorization_count": ready_count,
        "all_ready_for_user_authorization": all_ready,
        "all_ready_for_clean_restart_authorization": all_ready and duplicate_process_count == 0 and unmanaged_count == 0,
        "process_hygiene_requires_cleanup_authorization": duplicate_process_count > 0,
        "duplicate_target_process_count": duplicate_process_count,
        "duplicate_target_names": str(forward_info.get("duplicate_target_names", "") or ""),
        "unmanaged_process_decision_required": unmanaged_count > 0,
        "unmanaged_matching_process_count": unmanaged_count,
        "dry_run_command": DRY_RUN_COMMAND,
        "latest_restart_plan_dir": str(restart_plan_dir or ""),
        "latest_restart_plan_execute": boolish(restart_plan.get("execute", False)) if restart_plan else "",
        "execute_command_requires_explicit_user_authorization": EXECUTE_COMMAND,
        "execute_command_after_explicit_unmanaged_decision": EXECUTE_COMMAND_AFTER_UNMANAGED_DECISION,
        "note": "Non-disruptive authorization packet only. This script did not stop, start, archive, migrate, or deploy anything. The q250 YES candidate is a new paper-only start target.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "family",
        "candidate",
        "authorization_packet_status",
        "current_pids",
        "process_count",
        "duplicate_process_count",
        "process_hygiene_status",
        "restart_path_status",
        "fresh_capture_sidecar_status",
        "active_ledger_schema_status",
        "post_restart_gate_status",
        "post_restart_official_rows",
        "min_post_restart_official_rows",
        "latest_restart_plan_script_safety_pass",
        "kill_continue_action",
        "process_cleanup_warnings",
    ]
    compact = summary[[col for col in compact_cols if col in summary.columns]].copy()
    report = [
        "# BTC Paper-Shadow Restart Authorization Packet",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        (
            "The paper-shadow restart path is blocked until unmanaged matching BTC/Predictive process handling is explicitly decided."
            if unmanaged_count
            else "The paper-shadow restart path is ready for explicit user authorization, with duplicate target cleanup disclosed."
            if all_ready and duplicate_process_count
            else
            "The paper-shadow restart path is ready for explicit user authorization, but this packet did not execute it."
            if all_ready
            else "At least one pre-authorization check needs review before a controlled restart."
        ),
        "",
        "No live deployment is authorized. The workflow starts/restarts paper shadows only and intentionally leaves BTC15M capture running.",
        f"Duplicate target processes currently disclosed: `{duplicate_process_count}`.",
        f"Unmanaged matching processes currently disclosed: `{unmanaged_count}`.",
        "",
        "## Target Summary",
        "",
        compact.fillna("").to_string(index=False),
        "",
        "## Checklist",
        "",
        checklist.fillna("").to_string(index=False),
        "",
        "## Unmanaged Matching Processes",
        "",
        unmanaged.fillna("").to_string(index=False) if not unmanaged.empty else "(none)",
        "",
        "## Commands",
        "",
        "Dry run:",
        "",
        "```powershell",
        DRY_RUN_COMMAND,
        "```",
        "",
        (
            "Current execute command is not ready while unmanaged matching processes remain; the guarded script is expected to refuse execution until that is explicitly addressed."
            if unmanaged_count
            else "Execute only after explicit user authorization:"
        ),
        "",
        "```powershell",
        EXECUTE_COMMAND,
        "```",
        "",
        "## Post-Restart Evidence Clock",
        "",
        "- Only rows written after the controlled restart completion timestamp can enter the post-restart collection gate.",
        "- BTC15M candidates need 100 official-settled post-restart rows with complete execution-realism fields.",
        "- BTC1H candidates need 50 official-settled post-restart rows with complete execution-realism fields.",
        "- Passing the post-restart collection gate would still not deploy anything; readiness, settlement-basis, live replay, and live/paper consistency gates still apply.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(run_info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
