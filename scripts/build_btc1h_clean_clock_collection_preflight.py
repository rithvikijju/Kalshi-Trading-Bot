#!/usr/bin/env python3
"""Build a BTC1H clean-clock collection preflight.

The BTC1H source/materializer path can now be ready for future faithful replay
capture while the current evidence rows remain unusable for promotion.  This
read-only preflight separates those states: preparation readiness, explicit
authorization requirements, and the missing post-restart official sample.
It does not start, stop, restart, migrate, deploy, or tune anything.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_clean_clock_collection_preflight_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
CANDIDATE = "btc1h_high_conf80_entry70_no_chase"
FAMILY = "BTC1H"
AUTHORIZATION_READY_STATUSES = {
    "READY_FOR_USER_AUTHORIZATION",
    "READY_FOR_USER_AUTHORIZATION_WITH_DUPLICATE_CLEANUP",
    "READY_FOR_USER_AUTHORIZATION_TO_START",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--replay-source-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_source_contract_readiness_latest_codex"
        / "btc1h_replay_source_contract_summary.csv",
    )
    parser.add_argument(
        "--faithful-replay-data-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_faithful_replay_data_contract_latest_codex"
        / "btc1h_faithful_replay_data_contract_summary.csv",
    )
    parser.add_argument(
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_evidence_clock_gate_latest_codex"
        / "btc1h_clean_evidence_clock_summary.csv",
    )
    parser.add_argument(
        "--restart-authorization-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc_restart_authorization_packet_latest_codex" / "restart_authorization_summary.csv",
    )
    parser.add_argument(
        "--shadow-restart-preflight-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc_shadow_restart_preflight_latest_codex" / "shadow_restart_preflight_summary.csv",
    )
    parser.add_argument(
        "--post-restart-collection-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc_post_restart_collection_gate_latest_codex"
        / "post_restart_collection_gate_summary.csv",
    )
    parser.add_argument(
        "--remaining-evidence-summary",
        type=Path,
        default=None,
        help="Optional previous/current remaining-evidence summary for ad hoc reports; omitted during stack refresh.",
    )
    parser.add_argument(
        "--objective-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_objective_completion_audit_latest_codex"
        / "btc1h_objective_summary.csv",
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


def first(rows: list[dict[str, str]]) -> dict[str, str]:
    return rows[0] if rows else {}


def first_match(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
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


def status_pass(value: Any) -> bool:
    text = str(value or "").strip().upper()
    return text == "PASS" or text.startswith("PASS_")


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        for part in str(value or "").split(";"):
            text = part.strip()
            if text and text not in out:
                out.append(text)
    return ";".join(out)


def path_text(path: Path) -> str:
    return str(path.resolve())


def checklist_row(
    *,
    checklist_id: str,
    category: str,
    status: str,
    current_evidence: str,
    blockers: list[Any],
    current_artifacts: list[Path],
    ready_for_authorization: bool,
    promotion_collection_ready: bool,
    requires_explicit_authorization: bool,
    requires_process_control: bool,
    safe_now_action: str,
    next_action: str,
) -> dict[str, Any]:
    return {
        "checklist_id": checklist_id,
        "variant": VARIANT,
        "ledger": LEDGER,
        "category": category,
        "status": status,
        "current_evidence": current_evidence,
        "blockers": unique_join(blockers),
        "current_artifacts": unique_join([path_text(path) for path in current_artifacts]),
        "ready_for_authorization": ready_for_authorization,
        "promotion_collection_ready": promotion_collection_ready,
        "requires_explicit_authorization": requires_explicit_authorization,
        "requires_process_control": requires_process_control,
        "safe_now_action": safe_now_action,
        "next_action": next_action,
    }


def build_preflight(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = first(read_csv(args.replay_source_contract_summary))
    data_contract = first(read_csv(args.faithful_replay_data_contract_summary))
    clean_clock = first(read_csv(args.clean_clock_summary))
    authorization = first_match(
        read_csv(args.restart_authorization_summary),
        family=FAMILY,
        candidate=CANDIDATE,
    )
    restart_preflight = first_match(read_csv(args.shadow_restart_preflight_summary), ledger=LEDGER)
    post_restart = first_match(
        read_csv(args.post_restart_collection_summary),
        family=FAMILY,
        candidate=CANDIDATE,
    )
    remaining_path = getattr(args, "remaining_evidence_summary", None)
    remaining = first(read_csv(remaining_path)) if remaining_path else {}
    objective = first(read_csv(args.objective_summary))

    source_contract_ready = (
        to_bool(source.get("current_source_contract_ready", ""))
        and to_int(source.get("source_missing_field_count", "")) == 0
    )
    current_artifacts_support_replay = to_bool(
        data_contract.get("current_artifacts_can_support_faithful_replay", "")
    )
    clean_clock_ready = to_bool(clean_clock.get("clean_evidence_clock_ready", ""))
    expected_policy_rows = to_int(clean_clock.get("expected_policy_official_rows", ""))
    blank_policy_rows = to_int(clean_clock.get("blank_policy_official_rows", ""))
    restart_path_ready = status_pass(
        authorization.get("restart_path_status", restart_preflight.get("restart_path_status", ""))
    )
    fresh_schema_ready = status_pass(
        authorization.get("fresh_schema_status", restart_preflight.get("fresh_schema_status", ""))
    )
    fresh_insert_ready = status_pass(
        authorization.get("fresh_insert_status", restart_preflight.get("fresh_insert_status", ""))
    )
    fresh_capture_ready = status_pass(
        authorization.get("fresh_capture_sidecar_status", restart_preflight.get("fresh_capture_sidecar_status", ""))
    )
    fresh_replay_schema_ready = status_pass(
        authorization.get(
            "fresh_capture_replay_schema_status",
            restart_preflight.get("fresh_capture_replay_schema_status", ""),
        )
    )
    fresh_replay_missing_fields = unique_join(
        [
            authorization.get("fresh_capture_replay_signal_missing_fields", ""),
            authorization.get("fresh_capture_replay_order_missing_fields", ""),
            restart_preflight.get("fresh_capture_replay_signal_missing_fields", ""),
            restart_preflight.get("fresh_capture_replay_order_missing_fields", ""),
        ]
    )
    restart_plan_safety_pass = to_bool(authorization.get("latest_restart_plan_script_safety_pass", ""))
    authorization_status = authorization.get("authorization_packet_status", "")
    authorization_packet_ready = authorization_status in AUTHORIZATION_READY_STATUSES
    pre_authorization_blockers = authorization.get("pre_authorization_blockers", "")
    target_process_count = to_int(authorization.get("process_count", ""))
    target_process_running = to_bool(authorization.get("shadow_running_in_forward_status", ""))
    target_process_hygiene_status = authorization.get("process_hygiene_status", "")
    observed_process_action = authorization.get("observed_process_action", "")
    expected_process_state = authorization.get("expected_process_state", "")
    forward_status_age_minutes = authorization.get("forward_status_age_minutes", "")
    forward_status_fresh = to_bool(authorization.get("forward_status_fresh", ""))
    live_capture_untouched = to_bool(authorization.get("live_capture_untouched", ""))
    prep_ready = all(
        [
            source_contract_ready,
            authorization_packet_ready,
            restart_path_ready,
            fresh_schema_ready,
            fresh_insert_ready,
            fresh_capture_ready,
            fresh_replay_schema_ready,
            not fresh_replay_missing_fields,
            restart_plan_safety_pass,
            live_capture_untouched,
        ]
    )
    user_permission_required = to_bool(authorization.get("user_permission_required", "True"))
    restart_executed = to_bool(post_restart.get("restart_executed", ""))
    post_restart_official_rows = to_int(post_restart.get("post_restart_official_rows", ""))
    min_post_restart_official_rows = to_int(
        post_restart.get(
            "min_post_restart_official_rows",
            authorization.get("min_post_restart_official_rows", "50"),
        ),
        default=50,
    )
    promotion_collection_ready = to_bool(post_restart.get("promotion_collection_ready", ""))
    deployable_now = to_int(objective.get("deployable_candidates", "")) > 0
    near_deployable_now = to_int(objective.get("near_deployable_candidates", "")) > 0

    ready_for_authorization = prep_ready and user_permission_required and not promotion_collection_ready
    current_rows_blocked = not current_artifacts_support_replay or not clean_clock_ready or expected_policy_rows <= 0
    sample_blocked = post_restart_official_rows < min_post_restart_official_rows

    blockers = [
        "" if source_contract_ready else "source_contract_not_ready",
        "" if authorization_packet_ready else "restart_authorization_packet_not_ready",
        pre_authorization_blockers,
        "" if prep_ready else "pre_restart_preflight_not_ready",
        "" if current_artifacts_support_replay else "current_rows_not_faithful_replay_capable",
        "" if clean_clock_ready else "clean_evidence_clock_not_ready",
        "no_expected_policy_official_rows" if expected_policy_rows <= 0 else "",
        "explicit_user_authorization_required" if user_permission_required else "",
        "controlled_restart_not_executed" if not restart_executed else "",
        "no_post_restart_official_rows" if post_restart_official_rows == 0 else "",
        "too_few_post_restart_official_rows" if sample_blocked else "",
        post_restart.get("failure_reasons", ""),
        remaining.get("critical_missing_evidence_ids", objective.get("critical_blocked_requirements", "")),
    ]
    blocker_text = unique_join(blockers)

    if promotion_collection_ready and clean_clock_ready and current_artifacts_support_replay:
        preflight_status = "READY_COLLECTION_EVIDENCE_AVAILABLE"
    elif ready_for_authorization:
        preflight_status = "READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE"
    elif not prep_ready:
        preflight_status = "BLOCKED_PREPARATION_NOT_READY"
    else:
        preflight_status = "BLOCKED_CLEAN_CLOCK_COLLECTION_NOT_READY"

    checklist = [
        checklist_row(
            checklist_id="source_contract_ready",
            category="pre_restart_preparation",
            status="PASS_SOURCE_READY" if source_contract_ready else "BLOCKED_SOURCE_MISSING_FIELDS",
            current_evidence=(
                f"source status {source.get('source_contract_status', '')}; "
                f"source missing fields {source.get('source_missing_field_count', '')}"
            ),
            blockers=["" if source_contract_ready else source.get("source_missing_fields", "source_contract_not_ready")],
            current_artifacts=[args.replay_source_contract_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=False,
            requires_process_control=False,
            safe_now_action="Keep this as source readiness only; it is not evidence from clean future rows.",
            next_action=source.get("next_action", ""),
        ),
        checklist_row(
            checklist_id="current_rows_faithful_replay_contract",
            category="current_row_evidence",
            status=(
                "PASS_CURRENT_ROWS_SUPPORT_FAITHFUL_REPLAY"
                if current_artifacts_support_replay
                else "BLOCKED_CURRENT_ROWS_NOT_FAITHFUL"
            ),
            current_evidence=(
                f"data contract status {data_contract.get('contract_status', '')}; "
                f"missing required fields {data_contract.get('missing_required_field_count', '')}; "
                f"clean policy identity ready {data_contract.get('clean_policy_identity_ready', '')}"
            ),
            blockers=[
                data_contract.get("blockers", ""),
                "" if current_artifacts_support_replay else "current_rows_not_faithful_replay_capable",
            ],
            current_artifacts=[args.faithful_replay_data_contract_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=not current_artifacts_support_replay,
            requires_process_control=not current_artifacts_support_replay,
            safe_now_action="Treat current rows as blocker diagnostics only.",
            next_action=data_contract.get("next_action", ""),
        ),
        checklist_row(
            checklist_id="clean_evidence_clock_state",
            category="clean_clock",
            status="PASS_CLEAN_CLOCK_READY" if clean_clock_ready else "BLOCKED_CLEAN_CLOCK_NOT_READY",
            current_evidence=(
                f"gate status {clean_clock.get('gate_status', '')}; "
                f"expected-policy official rows {expected_policy_rows}; blank-policy official rows {blank_policy_rows}; "
                f"missing signal fields {clean_clock.get('sidecar_signal_missing_fields', '')}; "
                f"missing order fields {clean_clock.get('sidecar_order_decision_missing_fields', '')}"
            ),
            blockers=[
                clean_clock.get("gate_status", ""),
                "no_expected_policy_official_rows" if expected_policy_rows <= 0 else "",
            ],
            current_artifacts=[args.clean_clock_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=not clean_clock_ready,
            requires_process_control=not clean_clock_ready,
            safe_now_action="Do not count blank-policy rows as clean-clock evidence.",
            next_action=clean_clock.get("next_action", ""),
        ),
        checklist_row(
            checklist_id="restart_path_preflight",
            category="pre_restart_preparation",
            status="PASS_PREP_READY" if prep_ready else "BLOCKED_PREP_NOT_READY",
            current_evidence=(
                f"authorization status {authorization_status}; "
                f"restart path {authorization.get('restart_path_status', restart_preflight.get('restart_path_status', ''))}; "
                f"fresh schema {authorization.get('fresh_schema_status', restart_preflight.get('fresh_schema_status', ''))}; "
                f"fresh insert {authorization.get('fresh_insert_status', restart_preflight.get('fresh_insert_status', ''))}; "
                f"fresh replay schema {authorization.get('fresh_capture_replay_schema_status', restart_preflight.get('fresh_capture_replay_schema_status', ''))}"
            ),
            blockers=[
                "" if authorization_packet_ready else "restart_authorization_packet_not_ready",
                pre_authorization_blockers,
                "" if prep_ready else "pre_restart_preflight_not_ready",
                fresh_replay_missing_fields,
            ],
            current_artifacts=[args.restart_authorization_summary, args.shadow_restart_preflight_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=False,
            requires_process_control=False,
            safe_now_action="Review the guarded restart packet; no process action has been taken.",
            next_action=authorization.get("kill_continue_next_step", restart_preflight.get("interpretation", "")),
        ),
        checklist_row(
            checklist_id="target_process_state",
            category="authorization",
            status="PASS_TARGET_PROCESS_STATE_READY" if authorization_packet_ready else "BLOCKED_TARGET_PROCESS_STATE",
            current_evidence=(
                f"process count {target_process_count}; running {target_process_running}; "
                f"hygiene {target_process_hygiene_status}; forward status fresh {forward_status_fresh}; "
                f"expected process state {expected_process_state}; observed action {observed_process_action}; "
                f"forward status age minutes {forward_status_age_minutes}; "
                f"pre-authorization blockers {pre_authorization_blockers}"
            ),
            blockers=[
                "" if authorization_packet_ready else "restart_authorization_packet_not_ready",
                pre_authorization_blockers,
            ],
            current_artifacts=[args.restart_authorization_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=True,
            requires_process_control=True,
            safe_now_action="Treat the current process state as a blocker until the guarded authorization packet is ready.",
            next_action=authorization.get("kill_continue_next_step", ""),
        ),
        checklist_row(
            checklist_id="explicit_authorization",
            category="authorization",
            status="BLOCKED_AUTHORIZATION_REQUIRED" if user_permission_required else "PASS_AUTHORIZATION_NOT_REQUIRED",
            current_evidence=(
                f"authorization status {authorization.get('authorization_packet_status', '')}; "
                f"pre-authorization blockers {authorization.get('pre_authorization_blockers', '')}; "
                f"will restart process {authorization.get('will_restart_process', '')}; "
                f"will start new process {authorization.get('will_start_new_process', '')}; "
                f"will archive trade db by default {authorization.get('will_archive_trade_db_by_default', '')}; "
                f"live capture untouched {authorization.get('live_capture_untouched', '')}"
            ),
            blockers=[
                authorization.get("pre_authorization_blockers", ""),
                "explicit_user_authorization_required" if user_permission_required else "",
            ],
            current_artifacts=[args.restart_authorization_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=user_permission_required,
            requires_process_control=user_permission_required,
            safe_now_action="Do not run the guarded restart unless the user explicitly authorizes it.",
            next_action=authorization.get("kill_continue_next_step", ""),
        ),
        checklist_row(
            checklist_id="post_restart_official_sample",
            category="post_restart_collection",
            status=(
                "PASS_POST_RESTART_SAMPLE_READY"
                if promotion_collection_ready
                else "BLOCKED_NO_POST_RESTART_ROWS"
                if post_restart_official_rows == 0
                else "BLOCKED_TOO_FEW_POST_RESTART_ROWS"
            ),
            current_evidence=(
                f"restart executed {restart_executed}; post-restart official rows {post_restart_official_rows}; "
                f"minimum post-restart official rows {min_post_restart_official_rows}; "
                f"gate status {post_restart.get('gate_status', '')}; "
                f"official PnL {post_restart.get('official_pnl', '')}; "
                f"proxy/official mismatches {post_restart.get('proxy_official_mismatches', '')}"
            ),
            blockers=[
                post_restart.get("failure_reasons", ""),
                "controlled_restart_not_executed" if not restart_executed else "",
                "too_few_post_restart_official_rows" if sample_blocked else "",
            ],
            current_artifacts=[args.post_restart_collection_summary],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=not promotion_collection_ready,
            requires_process_control=not promotion_collection_ready,
            safe_now_action="No BTC1H promotion sample exists yet.",
            next_action="Collect official-settled post-restart rows only after explicit paper-shadow authorization.",
        ),
        checklist_row(
            checklist_id="deployability_verdict",
            category="final_verdict",
            status="PASS_DEPLOYABLE_FOUND" if deployable_now else "BLOCKED_NO_DEPLOY",
            current_evidence=(
                f"objective complete {objective.get('objective_complete', '')}; "
                f"deployable candidates {objective.get('deployable_candidates', '')}; "
                f"near-deployable candidates {objective.get('near_deployable_candidates', '')}; "
                "remaining critical missing "
                f"{remaining.get('critical_missing_evidence_ids', objective.get('critical_blocked_requirements', ''))}"
            ),
            blockers=["no_deployable_candidates" if not deployable_now else "", blocker_text],
            current_artifacts=[
                args.objective_summary,
                *([remaining_path] if remaining_path else []),
            ],
            ready_for_authorization=ready_for_authorization,
            promotion_collection_ready=promotion_collection_ready,
            requires_explicit_authorization=False,
            requires_process_control=False,
            safe_now_action="Do not deploy or call BTC1H near-deployable from current evidence.",
            next_action="Use this preflight as the read-only handoff before any user-authorized clean-clock collection.",
        ),
    ]

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "ledger": LEDGER,
        "candidate": CANDIDATE,
        "preflight_status": preflight_status,
        "ready_for_authorization": ready_for_authorization,
        "collection_evidence_ready": promotion_collection_ready
        and clean_clock_ready
        and current_artifacts_support_replay,
        "source_contract_ready": source_contract_ready,
        "source_contract_status": source.get("source_contract_status", ""),
        "source_missing_field_count": source.get("source_missing_field_count", ""),
        "current_artifacts_can_support_faithful_replay": current_artifacts_support_replay,
        "faithful_replay_data_contract_status": data_contract.get("contract_status", ""),
        "faithful_replay_missing_required_field_count": data_contract.get("missing_required_field_count", ""),
        "clean_evidence_clock_ready": clean_clock_ready,
        "clean_evidence_clock_gate_status": clean_clock.get("gate_status", ""),
        "expected_policy_official_rows": expected_policy_rows,
        "blank_policy_official_rows": blank_policy_rows,
        "restart_authorization_status": authorization_status,
        "restart_authorization_packet_ready": authorization_packet_ready,
        "pre_authorization_blockers": pre_authorization_blockers,
        "target_process_count": target_process_count,
        "target_process_running": target_process_running,
        "target_process_hygiene_status": target_process_hygiene_status,
        "expected_process_state": expected_process_state,
        "observed_process_action": observed_process_action,
        "forward_status_age_minutes": forward_status_age_minutes,
        "forward_status_fresh": forward_status_fresh,
        "user_permission_required": user_permission_required,
        "restart_path_status": authorization.get("restart_path_status", restart_preflight.get("restart_path_status", "")),
        "fresh_schema_status": authorization.get("fresh_schema_status", restart_preflight.get("fresh_schema_status", "")),
        "fresh_insert_status": authorization.get("fresh_insert_status", restart_preflight.get("fresh_insert_status", "")),
        "fresh_capture_sidecar_status": authorization.get(
            "fresh_capture_sidecar_status",
            restart_preflight.get("fresh_capture_sidecar_status", ""),
        ),
        "fresh_capture_replay_schema_status": authorization.get(
            "fresh_capture_replay_schema_status",
            restart_preflight.get("fresh_capture_replay_schema_status", ""),
        ),
        "fresh_capture_replay_missing_fields": fresh_replay_missing_fields,
        "restart_plan_script_safety_pass": restart_plan_safety_pass,
        "will_restart_process": authorization.get("will_restart_process", ""),
        "will_start_new_process": authorization.get("will_start_new_process", ""),
        "will_archive_trade_db_by_default": authorization.get("will_archive_trade_db_by_default", ""),
        "live_capture_untouched": live_capture_untouched,
        "post_restart_gate_status": post_restart.get("gate_status", ""),
        "restart_executed": restart_executed,
        "post_restart_official_rows": post_restart_official_rows,
        "min_post_restart_official_rows": min_post_restart_official_rows,
        "promotion_collection_ready": promotion_collection_ready,
        "post_restart_failure_reasons": post_restart.get("failure_reasons", ""),
        "deployable_now": deployable_now,
        "near_deployable_now": near_deployable_now,
        "current_rows_blocked": current_rows_blocked,
        "process_control_authorized": False,
        "requires_explicit_authorization": not promotion_collection_ready,
        "requires_process_control": not promotion_collection_ready,
        "no_process_action_taken": True,
        "blockers": blocker_text,
        "safe_next_action": (
            "Review this preflight and the guarded restart authorization packet only. Do not start, stop, restart, "
            "migrate, deploy, or tune BTC1H unless the user explicitly authorizes the guarded paper-shadow path."
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
    columns = [
        "checklist_id",
        "status",
        "ready_for_authorization",
        "promotion_collection_ready",
        "requires_explicit_authorization",
        "requires_process_control",
        "safe_now_action",
    ]
    return "\n".join(
        [
            "# BTC1H Clean-Clock Collection Preflight",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Preflight status: `{summary['preflight_status']}`",
            f"Ready for authorization: `{summary['ready_for_authorization']}`",
            f"Collection evidence ready: `{summary['collection_evidence_ready']}`",
            f"Post-restart official rows: `{summary['post_restart_official_rows']}`",
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
    write_csv(args.out_dir / "btc1h_clean_clock_collection_preflight_checklist.csv", rows)
    write_csv(args.out_dir / "btc1h_clean_clock_collection_preflight_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
