#!/usr/bin/env python3
"""Build a BTC1H clean-clock collection preflight.

This is the bridge between source readiness and future evidence collection. It
does not authorize or perform a restart. It states whether the current code and
guarded restart packet are ready to begin a new BTC1H clean evidence clock if
the user later gives explicit permission, and it keeps old rows out of the
promotion pool.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_clean_collection_preflight_latest_codex"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"
CANDIDATE = "btc1h_high_conf80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--restart-authorization-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc_restart_authorization_packet_latest_codex" / "restart_authorization_summary.csv",
    )
    parser.add_argument(
        "--post-restart-gate-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc_post_restart_collection_gate_latest_codex" / "post_restart_collection_gate_summary.csv",
    )
    parser.add_argument(
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_evidence_clock_gate_latest_codex"
        / "btc1h_clean_evidence_clock_summary.csv",
    )
    parser.add_argument(
        "--faithful-replay-data-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_faithful_replay_data_contract_latest_codex"
        / "btc1h_faithful_replay_data_contract_summary.csv",
    )
    parser.add_argument(
        "--replay-source-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_source_contract_readiness_latest_codex"
        / "btc1h_replay_source_contract_summary.csv",
    )
    parser.add_argument(
        "--objective-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_objective_completion_audit_latest_codex" / "btc1h_objective_summary.csv",
    )
    parser.add_argument(
        "--remaining-evidence-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_remaining_evidence_manifest_latest_codex"
        / "btc1h_remaining_evidence_summary.csv",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def first(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(key, "")).strip() == value for key, value in filters.items()):
            return row
    return {}


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return ";".join(out)


def status_pass(value: Any) -> bool:
    text = str(value or "").strip().upper()
    return text == "PASS" or text.startswith("PASS_")


def build_preflight(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    auth_rows = read_csv(args.restart_authorization_summary)
    gate_rows = read_csv(args.post_restart_gate_summary)
    clean_rows = read_csv(args.clean_clock_summary)
    data_contract_rows = read_csv(args.faithful_replay_data_contract_summary)
    source_rows = read_csv(args.replay_source_contract_summary)
    objective_rows = read_csv(args.objective_summary)
    remaining_rows = read_csv(args.remaining_evidence_summary)

    auth = first(auth_rows, ledger=LEDGER) or first(auth_rows, family="BTC1H")
    gate = first(gate_rows, ledger=LEDGER) or first(gate_rows, family="BTC1H")
    clean = clean_rows[0] if clean_rows else {}
    data_contract = data_contract_rows[0] if data_contract_rows else {}
    source = source_rows[0] if source_rows else {}
    objective = objective_rows[0] if objective_rows else {}
    remaining = remaining_rows[0] if remaining_rows else {}

    source_ready = to_bool(source.get("current_source_contract_ready", ""))
    source_missing = to_int(source.get("source_missing_field_count", ""))
    auth_ready = (
        str(auth.get("authorization_packet_status", "")) == "NEEDS_REVIEW_BEFORE_START_RESTART"
        and to_bool(auth.get("user_permission_required", ""))
        and status_pass(auth.get("restart_path_status", ""))
        and status_pass(auth.get("fresh_schema_status", ""))
        and status_pass(auth.get("fresh_insert_status", ""))
        and status_pass(auth.get("fresh_capture_replay_schema_status", ""))
        and not str(auth.get("fresh_capture_replay_signal_missing_fields", "")).strip()
        and not str(auth.get("fresh_capture_replay_order_missing_fields", "")).strip()
    )
    restart_pending = not to_bool(gate.get("restart_executed", ""))
    current_rows_blocked = (
        not to_bool(data_contract.get("current_artifacts_can_support_faithful_replay", ""))
        or not to_bool(clean.get("clean_evidence_clock_ready", ""))
        or to_int(clean.get("expected_policy_official_rows", "")) <= 0
    )
    post_restart_rows = to_int(gate.get("post_restart_official_rows", ""))
    min_rows = to_int(gate.get("min_post_restart_official_rows", "50"), 50)
    clean_collection_ready_now = (
        source_ready
        and auth_ready
        and not restart_pending
        and not current_rows_blocked
        and post_restart_rows >= min_rows
        and to_bool(gate.get("promotion_collection_ready", ""))
    )

    checklist = [
        {
            "check_id": "source_contract_ready",
            "status": "PASS" if source_ready and source_missing == 0 else "BLOCKED",
            "current_value": f"ready={source_ready}; missing={source_missing}",
            "required_value": "current_source_contract_ready=True; source_missing_field_count=0",
            "artifact": str(args.replay_source_contract_summary),
        },
        {
            "check_id": "guarded_restart_packet_ready",
            "status": "PASS" if auth_ready else "BLOCKED",
            "current_value": (
                f"auth={auth.get('authorization_packet_status', '')}; "
                f"user_permission_required={auth.get('user_permission_required', '')}; "
                f"restart_path={auth.get('restart_path_status', '')}; "
                f"fresh_replay_schema={auth.get('fresh_capture_replay_schema_status', '')}"
            ),
            "required_value": "packet reviewed; user permission required; restart path/schema/replay schema ready",
            "artifact": str(args.restart_authorization_summary),
        },
        {
            "check_id": "explicit_authorization_not_granted",
            "status": "PENDING_PERMISSION" if restart_pending else "PASS",
            "current_value": f"restart_executed={gate.get('restart_executed', '')}; restart_source={gate.get('restart_source', '')}",
            "required_value": "Explicit user authorization plus guarded restart result before new rows count",
            "artifact": str(args.post_restart_gate_summary),
        },
        {
            "check_id": "current_rows_excluded",
            "status": "PASS" if current_rows_blocked else "REVIEW",
            "current_value": (
                f"clean_clock_ready={clean.get('clean_evidence_clock_ready', '')}; "
                f"expected_policy_rows={clean.get('expected_policy_official_rows', '')}; "
                f"current_artifacts_support_replay={data_contract.get('current_artifacts_can_support_faithful_replay', '')}"
            ),
            "required_value": "Current old rows must remain excluded until clean-clock fields are populated",
            "artifact": str(args.clean_clock_summary),
        },
        {
            "check_id": "future_official_sample_gate",
            "status": "PASS" if post_restart_rows >= min_rows and to_bool(gate.get("promotion_collection_ready", "")) else "PENDING_COLLECTION",
            "current_value": f"post_restart_official_rows={post_restart_rows}; min={min_rows}; gate={gate.get('gate_status', '')}",
            "required_value": ">=50 BTC1H post-restart official rows with execution realism and official settlement",
            "artifact": str(args.post_restart_gate_summary),
        },
        {
            "check_id": "no_deployment_verdict_preserved",
            "status": "PASS" if to_int(objective.get("deployable_candidates", "")) == 0 and to_int(objective.get("near_deployable_candidates", "")) == 0 else "REVIEW",
            "current_value": (
                f"deployable={objective.get('deployable_candidates', '')}; "
                f"near_deployable={objective.get('near_deployable_candidates', '')}; "
                f"manifest={remaining.get('manifest_status', '')}"
            ),
            "required_value": "No BTC1H deployable/near-deployable candidate from current artifacts",
            "artifact": str(args.objective_summary),
        },
    ]

    blockers = []
    if not source_ready:
        blockers.append("source_contract_not_ready")
    if not auth_ready:
        blockers.append("guarded_restart_packet_not_ready")
    if restart_pending:
        blockers.append("explicit_authorization_and_restart_required")
    if current_rows_blocked:
        blockers.append("current_rows_not_clean_clock_evidence")
    if post_restart_rows < min_rows:
        blockers.append("too_few_post_restart_official_rows")

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "ledger": LEDGER,
        "candidate": CANDIDATE,
        "preflight_status": (
            "READY_TO_COLLECT_AFTER_EXPLICIT_AUTHORIZATION"
            if source_ready and auth_ready and restart_pending and current_rows_blocked
            else "COLLECTION_GATE_PASS"
            if clean_collection_ready_now
            else "BLOCKED_PRE_AUTH_REVIEW_REQUIRED"
        ),
        "current_source_contract_ready": source_ready,
        "source_missing_field_count": source_missing,
        "restart_authorization_packet_ready": auth_ready,
        "user_permission_required": to_bool(auth.get("user_permission_required", "")),
        "explicit_authorization_observed": False,
        "restart_executed": to_bool(gate.get("restart_executed", "")),
        "current_rows_blocked_from_promotion": current_rows_blocked,
        "clean_evidence_clock_ready": to_bool(clean.get("clean_evidence_clock_ready", "")),
        "expected_policy_official_rows": clean.get("expected_policy_official_rows", ""),
        "current_artifacts_can_support_faithful_replay": to_bool(
            data_contract.get("current_artifacts_can_support_faithful_replay", "")
        ),
        "post_restart_gate_status": gate.get("gate_status", ""),
        "post_restart_official_rows": post_restart_rows,
        "min_post_restart_official_rows": min_rows,
        "promotion_collection_ready": to_bool(gate.get("promotion_collection_ready", "")),
        "deployable_now": False,
        "near_deployable_now": False,
        "no_process_action_taken": True,
        "blockers": unique_join(blockers),
        "next_action": (
            "If the user explicitly authorizes a guarded BTC1H paper-shadow restart later, count only rows after "
            "that restart result and rerun clean-clock, official-settlement, execution, and replay parity gates."
        ),
    }
    return checklist, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    columns = ["check_id", "status", "current_value", "required_value"]
    return "\n".join(
        [
            "# BTC1H Clean Collection Preflight",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Preflight status: `{summary['preflight_status']}`",
            f"Source contract ready: `{summary['current_source_contract_ready']}`",
            f"Restart authorization packet ready: `{summary['restart_authorization_packet_ready']}`",
            f"Restart executed: `{summary['restart_executed']}`",
            f"Current rows blocked from promotion: `{summary['current_rows_blocked_from_promotion']}`",
            f"No process action taken: `{summary['no_process_action_taken']}`",
            "",
            "## Checklist",
            "",
            markdown_table(rows, columns),
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(summary, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = build_preflight(args)
    write_csv(args.out_dir / "btc1h_clean_collection_preflight_checklist.csv", rows)
    write_csv(args.out_dir / "btc1h_clean_collection_preflight_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
