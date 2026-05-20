#!/usr/bin/env python3
"""Verify whether a controlled BTC paper-shadow restart actually started evidence collection.

This is a read-only post-restart control artifact. It does not stop, start,
archive, migrate, tune, or deploy anything. It answers a narrower question than
the collection gate: after a user-authorized restart/start, did the evidence
machine itself come up in a state where future rows can eventually count?
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_post_restart_verification_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

TARGETS = [
    {
        "name": "btc15m_q250_qty500_firstskip_shadow",
        "candidate": "q250_firstskip_qty500",
        "family": "BTC15M",
        "min_rows": 100,
        "capture_sidecar_required": True,
    },
    {
        "name": "btc15m_q250_qty500_firstskip_yes_shadow",
        "candidate": "q250_firstskip_qty500_yes",
        "family": "BTC15M",
        "min_rows": 100,
        "capture_sidecar_required": True,
    },
    {
        "name": "btc15m_q1000_yes_shadow",
        "candidate": "q1000_yes",
        "family": "BTC15M",
        "min_rows": 100,
        "capture_sidecar_required": True,
    },
    {
        "name": "btc1h_high_conf80_entry70_no_chase_shadow",
        "candidate": "btc1h_high_conf80_entry70_no_chase",
        "family": "BTC1H",
        "min_rows": 50,
        "capture_sidecar_required": True,
    },
]

CAPTURE_SIDECAR_MIN_ROW_FIELDS = {
    "capture_health_rows": "capture_health",
    "ws_orderbook_top_rows": "ws_orderbook_top",
    "signal_scan_rows": "signal_scan",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC post-restart verification artifact.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--restart-dir", type=Path, default=None)
    p.add_argument("--shadow-status-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex")
    p.add_argument("--ledger-schema-dir", type=Path, default=BACKTEST_ROOT / "btc_ledger_schema_preflight_latest_codex")
    p.add_argument("--restart-auth-dir", type=Path, default=BACKTEST_ROOT / "btc_restart_authorization_packet_latest_codex")
    p.add_argument("--post-restart-gate-dir", type=Path, default=BACKTEST_ROOT / "btc_post_restart_collection_gate_latest_codex")
    p.add_argument("--action-status-dir", type=Path, default=BACKTEST_ROOT / "btc_gpt_pro_action_status_latest_codex")
    return p.parse_args()


def latest_dir(prefix: str) -> Path | None:
    matches = [p for p in BACKTEST_ROOT.glob(f"{prefix}*") if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    out = str(value)
    if out.lower() in {"nan", "none", "<na>", "nat"}:
        return default
    return out


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return text(value).strip().lower() in {"true", "1", "yes", "y"}


def num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(out):
        return default
    return out


def parse_utc(value: Any) -> pd.Timestamp | None:
    raw = text(value)
    if not raw:
        return None
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def split_csv_text(value: Any) -> list[str]:
    return [part.strip() for part in text(value).split(",") if part.strip()]


def started_pid_by_name(result: dict[str, Any]) -> dict[str, str]:
    started = result.get("started", [])
    if not isinstance(started, list):
        return {}
    out: dict[str, str] = {}
    for row in started:
        if not isinstance(row, dict):
            continue
        name = text(row.get("name", ""))
        pid = text(row.get("process_id", ""))
        if name and pid:
            out[name] = pid
    return out


def process_identity_gate(
    status_row: dict[str, Any],
    *,
    target_name: str,
    restart_executed: bool,
    expected_started_pids: dict[str, str],
    plan_created_at: str,
) -> tuple[bool, str, str, str]:
    current_pids = split_csv_text(status_row.get("pids", ""))
    current_created = split_csv_text(status_row.get("process_created_at_utc", ""))
    expected_pid = expected_started_pids.get(target_name, "")
    if not restart_executed:
        return False, "pending_controlled_restart", expected_pid, ";".join(current_created)
    if not expected_pid:
        return False, "missing_restart_started_pid", expected_pid, ";".join(current_created)
    if expected_pid not in current_pids:
        return False, "current_pid_does_not_match_restart_result", expected_pid, ";".join(current_created)
    plan_ts = parse_utc(plan_created_at)
    if plan_ts is None:
        return False, "missing_or_unparseable_restart_plan_created_at", expected_pid, ";".join(current_created)
    idx = current_pids.index(expected_pid)
    created_ts = parse_utc(current_created[idx] if idx < len(current_created) else "")
    if created_ts is None:
        return False, "missing_or_unparseable_process_created_at", expected_pid, ";".join(current_created)
    if created_ts < plan_ts:
        return False, "process_created_before_restart_plan", expected_pid, ";".join(current_created)
    return True, "ready", expected_pid, ";".join(current_created)


def capture_sidecar_gate(
    status_row: dict[str, Any],
    *,
    restart_executed: bool,
    restart_utc: str,
    required: bool,
) -> tuple[bool, str]:
    if not required:
        return True, "not_required"
    if not restart_executed:
        return False, "pending_controlled_restart"
    updated = parse_utc(status_row.get("capture_sidecar_updated_at_utc", ""))
    if updated is None:
        return False, "missing_capture_sidecar_updated_at"
    restart_ts = parse_utc(restart_utc)
    if restart_ts is None:
        return False, "missing_or_unparseable_restart_utc"
    if updated < restart_ts:
        return False, "capture_sidecar_stale_before_restart"
    missing_tables: list[str] = []
    for field, table in CAPTURE_SIDECAR_MIN_ROW_FIELDS.items():
        if int(num(status_row.get(field, 0), 0)) <= 0:
            missing_tables.append(table)
    if missing_tables:
        return False, "capture_sidecar_missing_rows:" + ",".join(missing_tables)
    if text(status_row.get("capture_sidecar_error", "")):
        return False, "capture_sidecar_error_present"
    return True, "ready"


def first_row(df: pd.DataFrame, **filters: str) -> dict[str, Any]:
    if df.empty:
        return {}
    work = df.copy()
    for col, value in filters.items():
        if col not in work.columns:
            return {}
        work = work[work[col].astype(str).eq(str(value))]
    if work.empty:
        return {}
    return work.iloc[0].to_dict()


def resolve_restart_dir(arg: Path | None, restart_auth_info: dict[str, Any]) -> Path | None:
    if arg is not None:
        return arg
    auth_plan = text(restart_auth_info.get("latest_restart_plan_dir", ""))
    if auth_plan:
        return Path(auth_plan)
    return latest_dir("btc_paper_shadow_controlled_restart_")


def checklist_row(
    check: str,
    status: str,
    evidence_clock_ready: bool,
    deployment_ready: bool,
    evidence: str,
    next_action: str,
) -> dict[str, Any]:
    return {
        "check": check,
        "status": status,
        "passes_for_evidence_clock": bool(evidence_clock_ready),
        "passes_for_deployment": bool(deployment_ready),
        "evidence": evidence,
        "next_action": next_action,
    }


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    restart_auth_info = read_json(args.restart_auth_dir / "run_info.json")
    restart_dir = resolve_restart_dir(args.restart_dir, restart_auth_info)
    plan = read_json(restart_dir / "restart_plan.json") if restart_dir else {}
    result = read_json(restart_dir / "restart_result.json") if restart_dir else {}
    restart_executed = bool(result.get("completed_at"))
    restart_utc = text(result.get("completed_at", ""))
    plan_created_at = text(plan.get("created_at", ""))
    expected_started_pids = started_pid_by_name(result)
    plan_execute = boolish(plan.get("execute", False))
    plan_targets = plan.get("targets", []) if isinstance(plan.get("targets", []), list) else []
    script_safety_pass = bool(plan_targets) and all(boolish(row.get("script_safety_pass", False)) for row in plan_targets)
    untouched_processes = [text(x) for x in plan.get("untouched_processes", []) if text(x)]

    shadow = read_csv(args.shadow_status_dir / "shadow_status.csv")
    schema = read_csv(args.ledger_schema_dir / "ledger_schema_preflight_summary.csv")
    restart_auth = read_csv(args.restart_auth_dir / "restart_authorization_summary.csv")
    post_gate = read_csv(args.post_restart_gate_dir / "post_restart_collection_gate_summary.csv")
    action_status = read_csv(args.action_status_dir / "gpt_pro_candidate_status.csv")

    capture = first_row(shadow, name="btc15m_live_capture")
    capture_running = boolish(capture.get("running", False))
    capture_pids = text(capture.get("pids", ""))
    capture_untouched_declared = any("btc15m_live_capture.py" in item for item in untouched_processes)

    target_rows: list[dict[str, Any]] = []
    for target in TARGETS:
        name = target["name"]
        candidate = target["candidate"]
        status_row = first_row(shadow, name=name)
        schema_row = first_row(schema, ledger=name)
        auth_row = first_row(restart_auth, candidate=candidate)
        gate_row = first_row(post_gate, candidate=candidate)
        action_row = first_row(action_status, candidate=candidate)

        running = boolish(status_row.get("running", False))
        pids = text(status_row.get("pids", auth_row.get("current_pids", "")))
        process_identity_ready, process_identity_status, expected_started_pid, process_created_at_utc = process_identity_gate(
            status_row,
            target_name=name,
            restart_executed=restart_executed,
            expected_started_pids=expected_started_pids,
            plan_created_at=plan_created_at,
        )
        capture_sidecar_required = boolish(target.get("capture_sidecar_required", False))
        capture_read_source = text(status_row.get("capture_read_source", ""))
        capture_sidecar_updated_at_utc = text(status_row.get("capture_sidecar_updated_at_utc", ""))
        capture_sidecar_error = text(status_row.get("capture_sidecar_error", ""))
        capture_sidecar_ready, capture_sidecar_status = capture_sidecar_gate(
            status_row,
            restart_executed=restart_executed,
            restart_utc=restart_utc,
            required=capture_sidecar_required,
        )
        schema_status = text(schema_row.get("preflight_status", auth_row.get("active_ledger_schema_status", "")))
        restart_required = boolish(schema_row.get("restart_required_for_deployable_ledger", True))
        realism_present = int(num(schema_row.get("realism_columns_present", 0), 0))
        realism_missing = int(num(schema_row.get("realism_columns_missing", 0), 0))
        post_rows = int(num(gate_row.get("post_restart_official_rows", 0), 0))
        min_rows = int(num(gate_row.get("min_post_restart_official_rows", target["min_rows"]), target["min_rows"]))

        if not restart_executed:
            evidence_clock_status = "PENDING_CONTROLLED_RESTART"
        elif not running:
            evidence_clock_status = "FAIL_TARGET_NOT_RUNNING"
        elif not process_identity_ready:
            evidence_clock_status = "FAIL_PROCESS_IDENTITY"
        elif not capture_sidecar_ready:
            evidence_clock_status = "FAIL_CAPTURE_SIDECAR_MISSING"
        elif restart_required or realism_missing > 0:
            evidence_clock_status = "FAIL_ACTIVE_LEDGER_SCHEMA"
        else:
            evidence_clock_status = "EVIDENCE_CLOCK_STARTED_WAIT_FOR_OFFICIAL_ROWS"

        target_rows.append(
            {
                "family": target["family"],
                "candidate": candidate,
                "ledger": name,
                "restart_executed": restart_executed,
                "evidence_clock_status": evidence_clock_status,
                "running": running,
                "pids": pids,
                "expected_started_pid": expected_started_pid,
                "process_created_at_utc": process_created_at_utc,
                "process_identity_ready": process_identity_ready,
                "process_identity_status": process_identity_status,
                "capture_read_source": capture_read_source,
                "capture_sidecar_required": capture_sidecar_required,
                "capture_sidecar_ready": capture_sidecar_ready,
                "capture_sidecar_status": capture_sidecar_status,
                "capture_sidecar_updated_at_utc": capture_sidecar_updated_at_utc,
                "capture_sidecar_error": capture_sidecar_error,
                "capture_health_rows": int(num(status_row.get("capture_health_rows", 0), 0)),
                "ws_orderbook_top_rows": int(num(status_row.get("ws_orderbook_top_rows", 0), 0)),
                "signal_scan_rows": int(num(status_row.get("signal_scan_rows", 0), 0)),
                "signal_scan_nonzero_candidate_rows": int(num(status_row.get("signal_scan_nonzero_candidate_rows", 0), 0)),
                "authorization_packet_status": text(auth_row.get("authorization_packet_status", "")),
                "restart_path_status": text(auth_row.get("restart_path_status", "")),
                "active_schema_status": schema_status,
                "active_restart_required_for_deployable_ledger": restart_required,
                "realism_columns_present": realism_present,
                "realism_columns_missing": realism_missing,
                "post_restart_gate_status": text(gate_row.get("gate_status", "")),
                "post_restart_official_rows": post_rows,
                "min_post_restart_official_rows": min_rows,
                "post_restart_collection_ready": boolish(gate_row.get("promotion_collection_ready", False)),
                "gpt_pro_current_status": text(action_row.get("current_status", "")),
                "gpt_pro_rank": text(action_row.get("pro_rank", "")),
                "deployable_now": False,
            }
        )

    target_df = pd.DataFrame(target_rows)
    all_targets_running = bool(target_rows) and all(bool(row["running"]) for row in target_rows)
    all_process_identities_ready = bool(target_rows) and all(bool(row["process_identity_ready"]) for row in target_rows)
    all_required_sidecars_ready = bool(target_rows) and all(bool(row["capture_sidecar_ready"]) for row in target_rows)
    all_schemas_ready = bool(target_rows) and all(
        (not bool(row["active_restart_required_for_deployable_ledger"])) and int(row["realism_columns_missing"]) == 0
        for row in target_rows
    )
    any_collection_ready = bool(target_rows) and any(bool(row["post_restart_collection_ready"]) for row in target_rows)

    checks = [
        checklist_row(
            "restart_result_exists",
            "PASS" if restart_executed else "PENDING_CONTROLLED_RESTART",
            restart_executed,
            False,
            f"restart_dir={restart_dir}; restart_utc={restart_utc}",
            "Execute only after explicit user authorization; current goal continuation is not authorization.",
        ),
        checklist_row(
            "restart_plan_was_safety_checked",
            "PASS" if script_safety_pass else "FAIL_OR_MISSING",
            script_safety_pass and restart_executed,
            False,
            f"script_safety_pass={script_safety_pass}; plan_execute={plan_execute}",
            "Keep using the guarded restart workflow; do not manage processes ad hoc.",
        ),
        checklist_row(
            "btc15m_capture_untouched_and_running",
            "PASS" if capture_untouched_declared and capture_running else "FAIL",
            restart_executed and capture_untouched_declared and capture_running,
            False,
            f"capture_untouched_declared={capture_untouched_declared}; capture_running={capture_running}; pids={capture_pids}",
            "BTC15M capture must stay alive across any paper-shadow restart.",
        ),
        checklist_row(
            "all_target_shadows_running",
            "PASS" if restart_executed and all_targets_running else "PENDING_OR_FAIL",
            restart_executed and all_targets_running,
            False,
            ";".join(f"{row['ledger']}:{row['running']}:{row['pids']}" for row in target_rows),
            "After authorized restart, all four paper shadows should be running, including q250 YES-only.",
        ),
        checklist_row(
            "target_process_identity",
            "PASS" if restart_executed and all_process_identities_ready else "PENDING_OR_FAIL",
            restart_executed and all_process_identities_ready,
            False,
            ";".join(
                (
                    f"{row['ledger']}:expected_pid={row['expected_started_pid']}"
                    f":current_pids={row['pids']}"
                    f":created={row['process_created_at_utc']}"
                    f":status={row['process_identity_status']}"
                )
                for row in target_rows
            ),
            "After authorized restart, current target PIDs must match restart_result.json and be created after the restart plan.",
        ),
        checklist_row(
            "required_capture_sidecars_ready",
            "PASS" if restart_executed and all_required_sidecars_ready else "PENDING_OR_FAIL",
            restart_executed and all_required_sidecars_ready,
            False,
            ";".join(
                (
                    f"{row['ledger']}:required={row['capture_sidecar_required']}"
                    f":ready={row['capture_sidecar_ready']}"
                    f":status={row['capture_sidecar_status']}"
                    f":updated={row['capture_sidecar_updated_at_utc']}"
                    f":rows={row['capture_health_rows']}/{row['ws_orderbook_top_rows']}/{row['signal_scan_rows']}"
                    f":error={row['capture_sidecar_error']}"
                )
                for row in target_rows
            ),
            "Every restarted paper shadow must expose a fresh lock-free status sidecar after restart so audits do not depend on opening locked DuckDB files.",
        ),
        checklist_row(
            "active_ledger_schemas_ready",
            "PASS" if restart_executed and all_schemas_ready else "PENDING_OR_FAIL",
            restart_executed and all_schemas_ready,
            False,
            ";".join(
                f"{row['ledger']}:{row['active_schema_status']}:{row['realism_columns_present']}/{row['realism_columns_missing']}"
                for row in target_rows
            ),
            "Future fills cannot count unless active ledger schemas include execution-realism columns.",
        ),
        checklist_row(
            "post_restart_collection_gate",
            "PASS_FOR_COLLECTION_REVIEW" if any_collection_ready else "NOT_READY",
            any_collection_ready,
            any_collection_ready,
            ";".join(
                f"{row['candidate']}:{row['post_restart_gate_status']}:{row['post_restart_official_rows']}/{row['min_post_restart_official_rows']}"
                for row in target_rows
            ),
            "Promotion review still requires enough official-settled post-restart rows with execution realism.",
        ),
    ]
    checks_df = pd.DataFrame(checks)

    evidence_clock_ready = bool(
        restart_executed
        and capture_running
        and all_targets_running
        and all_process_identities_ready
        and all_required_sidecars_ready
        and all_schemas_ready
    )
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Read-only verifier. Does not restart, deploy, tune, archive, or migrate anything.",
        "restart_dir": str(restart_dir) if restart_dir else "",
        "restart_executed": restart_executed,
        "restart_utc": restart_utc,
        "evidence_clock_ready": evidence_clock_ready,
        "collection_gate_ready": any_collection_ready,
    }

    checks_df.to_csv(args.out_dir / "post_restart_verification_checklist.csv", index=False)
    target_df.to_csv(args.out_dir / "post_restart_target_status.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Post-Restart Verification",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"Restart executed: `{restart_executed}`",
        f"Evidence clock ready: `{evidence_clock_ready}`",
        "",
        "## Verdict",
        "",
        (
            "Evidence clock is ready for collection review."
            if evidence_clock_ready
            else "PENDING: no authorized controlled restart/start has produced a valid evidence clock yet."
        ),
        "",
        "## Checklist",
        "",
        checks_df.to_string(index=False),
        "",
        "## Target Status",
        "",
        target_df.to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- This verifier is about evidence plumbing, not deployment approval.",
        "- Even a ready evidence clock only starts future collection; promotion still needs official-settled rows and the collection gate.",
        "- If q250 YES-only is not running after an authorized restart, the top GPT Pro path has not actually begun.",
        "- If current PIDs do not match the guarded restart result, stale survivor processes cannot start the evidence clock.",
        "- If any target sidecar is missing or stale after restart, that shadow's capture health remains hard to audit under live DuckDB locks.",
        "- If active schemas still require restart, future fills remain diagnostic rather than promotion evidence.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
