#!/usr/bin/env python3
"""Build a BTC1H faithful-replay data contract.

The replay audits currently prove that BTC1H is blocked, but the exact capture
fields needed for future row-for-row replay parity are spread across the
sidecar schema, clean-clock gate, and repair diagnostics.  This report
consolidates those fields into one machine-readable contract.  It is read-only
and does not start, stop, restart, migrate, deploy, or tune anything.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_faithful_replay_data_contract_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


FIELD_CONTRACTS = [
    # Signal scan identity and clock.
    ("signal_scan", "received_at_ns", "causal_clock", "Replay scans in receive-time order."),
    ("signal_scan", "received_at_utc", "causal_clock", "Human-auditable scan timestamp."),
    ("signal_scan", "action", "scan_outcome", "Recreate selected, skipped, blocked, and none rows."),
    ("signal_scan", "event_ticker", "market_identity", "Join scan rows to the active BTC1H event."),
    ("signal_scan", "selected_market", "market_selection", "Audit same-event replacement decisions."),
    ("signal_scan", "selected_side", "market_selection", "Audit side selection and row-level joins."),
    ("signal_scan", "candidate_count", "candidate_set", "Prove whether a scan had tradeable candidates."),
    ("signal_scan", "evaluated_markets", "candidate_set", "Recreate the candidate universe at decision time."),
    ("signal_scan", "blocked_events", "dedupe_policy", "Recreate one-trade-per-event and blocked-event state."),
    ("signal_scan", "entry_price", "pricing", "Compare selected ask against order-decision fill price."),
    ("signal_scan", "model_p_yes", "model_input_output", "Prove model score parity without recomputing from stale state."),
    ("signal_scan", "net_edge_cents", "model_input_output", "Audit threshold and edge parity."),
    ("signal_scan", "btc_spot", "model_input_output", "Capture BTC spot used by the live decision."),
    # Clean-clock fields required by the current BTC1H gate.
    ("signal_scan", "signal_strategy", "policy_identity", "Separate active policy rows from stale/blank-policy rows."),
    ("signal_scan", "model_ttl_policy", "policy_identity", "Freeze scan-time TTL policy for future rows."),
    ("signal_scan", "model_policy_version", "policy_identity", "Freeze model/policy identity for future rows."),
    ("signal_scan", "edge_threshold_cents", "policy_identity", "Prove threshold identity for selected and skipped rows."),
    ("signal_scan", "spread_cents", "execution_context", "Exclude non-executable or wide-spread rows."),
    ("signal_scan", "top_visible_qty", "execution_context", "Prove visible size supports the fill quantity."),
    ("signal_scan", "quote_received_at_ns", "execution_context", "Measure quote age at decision time."),
    ("signal_scan", "quote_age_ms", "execution_context", "Enforce stale quote exclusions."),
    ("signal_scan", "ttl_min", "model_input_output", "Use captured TTL instead of recomputing after the fact."),
    ("signal_scan", "close_time", "model_input_output", "Tie TTL to the exact market close used live."),
    ("signal_scan", "btc_candle_time", "model_input_output", "Prove BTC feature timestamp parity."),
    ("signal_scan", "btc_candle_age_sec", "model_input_output", "Exclude stale BTC feature rows."),
    ("signal_scan", "btc_rv60", "model_input_output", "Capture volatility input used by the model."),
    ("signal_scan", "btc_ret_10m_usd", "model_input_output", "Capture return input used by the model."),
    # Order decision/fill chain.
    ("order_decision", "received_at_ns", "order_clock", "Replay order decisions at the captured fill/skip time."),
    ("order_decision", "received_at_utc", "order_clock", "Human-auditable order decision timestamp."),
    ("order_decision", "action", "order_outcome", "Distinguish fill, skip, no-fill, and blocked decisions."),
    ("order_decision", "event_ticker", "market_identity", "Join order decisions to BTC1H event rows."),
    ("order_decision", "market_ticker", "market_identity", "Verify exact market parity."),
    ("order_decision", "side", "market_identity", "Verify exact side parity."),
    ("order_decision", "entry_price", "pricing", "Use captured executable fill price for PnL parity."),
    ("order_decision", "yes_limit_price", "pricing", "Audit price semantics for YES/NO conversion."),
    ("order_decision", "contracts", "execution_context", "Apply visible-size/FOK checks to the requested size."),
    ("order_decision", "client_order_id", "order_identity", "Tie order-decision rows to the ledger fill when available."),
    ("order_decision", "btc_spot", "model_input_output", "Capture spot state at order decision time."),
    ("order_decision", "net_edge_cents", "model_input_output", "Audit edge drift between scan and order decision."),
    ("order_decision", "signal_strategy", "policy_identity", "Separate clean policy rows from blank-policy decisions."),
    ("order_decision", "model_ttl_policy", "policy_identity", "Prove the order decision used the frozen TTL policy."),
    ("order_decision", "model_policy_version", "policy_identity", "Prove the order decision used the frozen policy."),
    # Decision-time orderbook context.
    ("ws_orderbook_top", "received_at_ns", "book_clock", "Align top-of-book with scan/order decision time."),
    ("ws_orderbook_top", "received_at_utc", "book_clock", "Human-auditable book timestamp."),
    ("ws_orderbook_top", "event_ticker", "market_identity", "Join top-of-book to event rows."),
    ("ws_orderbook_top", "market_ticker", "market_identity", "Join top-of-book to market rows."),
    ("ws_orderbook_top", "yes_ask", "execution_context", "Use executable YES ask at decision time."),
    ("ws_orderbook_top", "yes_ask_qty", "execution_context", "Use visible YES ask quantity for FOK checks."),
    ("ws_orderbook_top", "no_ask", "execution_context", "Use executable NO ask at decision time."),
    ("ws_orderbook_top", "no_ask_qty", "execution_context", "Use visible NO ask quantity for FOK checks."),
    ("ws_orderbook_top", "btc_spot", "execution_context", "Audit provider spot/basis at quote time."),
    # Market lifecycle needed for open/close gating and replay exclusions.
    ("ws_lifecycle", "received_at_ns", "market_lifecycle", "Align market open/close events with scans."),
    ("ws_lifecycle", "event_ticker", "market_lifecycle", "Join lifecycle events to BTC1H event rows."),
    ("ws_lifecycle", "market_ticker", "market_lifecycle", "Join lifecycle events to BTC1H market rows."),
    ("ws_lifecycle", "open_ts", "market_lifecycle", "Exclude scans before market open."),
    ("ws_lifecycle", "close_ts", "market_lifecycle", "Exclude scans after market close."),
]


TARGET_FIELD_REQUIREMENTS = {
    "reprice_skip_then_fill_sequence_missing": [
        "signal_scan.model_ttl_policy",
        "signal_scan.model_policy_version",
        "signal_scan.ttl_min",
        "signal_scan.close_time",
        "signal_scan.btc_candle_time",
        "signal_scan.btc_candle_age_sec",
        "signal_scan.btc_rv60",
        "signal_scan.btc_ret_10m_usd",
        "order_decision.action",
        "order_decision.entry_price",
    ],
    "same_event_market_selection_not_row_faithful": [
        "signal_scan.selected_market",
        "signal_scan.selected_side",
        "signal_scan.evaluated_markets",
        "signal_scan.blocked_events",
        "signal_scan.model_p_yes",
        "signal_scan.net_edge_cents",
        "signal_scan.model_ttl_policy",
        "signal_scan.model_policy_version",
        "signal_scan.ttl_min",
        "signal_scan.close_time",
        "signal_scan.btc_candle_time",
        "signal_scan.btc_candle_age_sec",
        "signal_scan.btc_rv60",
        "signal_scan.btc_ret_10m_usd",
    ],
    "order_decision_reprice_fill_price_missing": [
        "order_decision.received_at_ns",
        "order_decision.action",
        "order_decision.market_ticker",
        "order_decision.side",
        "order_decision.entry_price",
        "order_decision.yes_limit_price",
        "order_decision.contracts",
    ],
    "blocked_dedupe_or_post_selected_scan_used_as_replay_clock": [
        "signal_scan.received_at_ns",
        "signal_scan.action",
        "signal_scan.event_ticker",
        "signal_scan.selected_market",
        "signal_scan.selected_side",
        "signal_scan.blocked_events",
        "order_decision.received_at_ns",
        "order_decision.action",
        "order_decision.market_ticker",
        "order_decision.side",
        "order_decision.entry_price",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--status-json",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "remote_status" / "btc1h_status_latest.json",
    )
    parser.add_argument(
        "--sidecar-schema-json",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "remote_status" / "btc1h_replay_sidecar_schema_latest.json",
    )
    parser.add_argument(
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_evidence_clock_gate_latest_codex"
        / "btc1h_clean_evidence_clock_summary.csv",
    )
    parser.add_argument(
        "--replay-reconciliation-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_vs_ledger_reconciliation_latest_codex"
        / "btc1h_replay_vs_ledger_reconciliation_summary.csv",
    )
    parser.add_argument(
        "--repair-target-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_target_matrix_latest_codex"
        / "btc1h_replay_repair_target_summary.csv",
    )
    parser.add_argument(
        "--repair-prerequisite-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_prerequisite_audit_latest_codex"
        / "btc1h_replay_repair_prerequisite_summary.csv",
    )
    parser.add_argument(
        "--repair-prerequisite-audit",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_prerequisite_audit_latest_codex"
        / "btc1h_replay_repair_prerequisite_audit.csv",
    )
    parser.add_argument(
        "--repair-attempt-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempt_summary.csv",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


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


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return ";".join(out)


def split_semicolon(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def schema_fields(schema: dict[str, Any], table: str) -> set[str]:
    raw = schema.get(table, [])
    if isinstance(raw, dict):
        raw = raw.get("columns", [])
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw}


def row_count(status: dict[str, Any], table: str) -> int:
    sidecar_rows = status.get("replay_sidecar_rows_by_table", {})
    if isinstance(sidecar_rows, dict) and table in sidecar_rows:
        return to_int(sidecar_rows.get(table))
    db_rows = status.get("rows_by_table", {})
    if isinstance(db_rows, dict) and table in db_rows:
        return to_int(db_rows.get(table))
    return 0


def field_status(present: bool, rows: int) -> str:
    if present and rows > 0:
        return "PRESENT_WITH_ROWS"
    if present:
        return "PRESENT_NO_ROWS"
    if rows > 0:
        return "MISSING_FROM_SCHEMA_WITH_ROWS"
    return "MISSING_FROM_SCHEMA_NO_ROWS"


def target_field_gaps(
    prerequisite_rows: list[dict[str, str]],
    field_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    field_by_id = {str(row.get("field_id", "")): row for row in field_rows}
    rows: list[dict[str, Any]] = []
    for prereq in prerequisite_rows:
        root_cause = str(prereq.get("root_cause", "")).strip()
        repair_target = str(prereq.get("repair_target", root_cause)).strip()
        field_ids = TARGET_FIELD_REQUIREMENTS.get(root_cause, [])
        missing_fields: list[str] = []
        present_fields: list[str] = []
        missing_roles: list[str] = []
        for field_id in field_ids:
            field_row = field_by_id.get(field_id, {})
            if not field_row or to_bool(field_row.get("missing_blocks_promotion", "")):
                missing_fields.append(field_id)
                missing_roles.append(str(field_row.get("role", "missing_contract_definition")))
            else:
                present_fields.append(field_id)
        snapshot_status = str(prereq.get("existing_snapshot_can_repair_independent_replay", "")).strip()
        missing_prerequisites = str(prereq.get("missing_prerequisites", "")).strip()
        future_exact_input_required = (
            snapshot_status != "True"
            or "exact_model_input" in missing_prerequisites
            or "model_value_parity" in missing_prerequisites
        )
        blockers = []
        if missing_fields:
            blockers.append("target_required_fields_missing")
        if future_exact_input_required:
            blockers.append("future_exact_input_or_policy_capture_required")
        if snapshot_status != "True":
            blockers.append("target_not_fully_snapshot_repairable")
        blockers.append("official_execution_replay_clean_clock_gates_still_required")
        rows.append(
            {
                "variant": VARIANT,
                "repair_target": repair_target,
                "root_cause": root_cause,
                "existing_snapshot_can_repair_independent_replay": snapshot_status,
                "target_required_field_count": len(field_ids),
                "target_present_required_fields": unique_join(present_fields),
                "target_missing_required_fields": unique_join(missing_fields),
                "target_missing_required_field_count": len(missing_fields),
                "target_missing_roles": unique_join(missing_roles),
                "target_field_contract_ready": len(missing_fields) == 0,
                "missing_prerequisites": missing_prerequisites,
                "future_exact_input_required_for_target": future_exact_input_required,
                "target_repair_can_make_promotion_usable_now": False,
                "blockers": unique_join(blockers),
                "why": prereq.get("why", ""),
                "next_validation_step": prereq.get("next_validation_step", ""),
            }
        )
    return rows


def build_contract(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    status = read_json(args.status_json)
    schema = read_json(args.sidecar_schema_json)
    clean = first(read_csv(args.clean_clock_summary))
    reconciliation = first(read_csv(args.replay_reconciliation_summary))
    target = first(read_csv(args.repair_target_summary))
    prereq = first(read_csv(args.repair_prerequisite_summary))
    prereq_rows = read_csv(args.repair_prerequisite_audit)
    attempt = first(read_csv(args.repair_attempt_summary))

    clean_signal_missing = split_semicolon(clean.get("sidecar_signal_missing_fields", ""))
    clean_decision_missing = split_semicolon(clean.get("sidecar_order_decision_missing_fields", ""))
    clean_required = {
        ("signal_scan", field) for field in clean_signal_missing
    } | {("order_decision", field) for field in clean_decision_missing}

    rows: list[dict[str, Any]] = []
    for table, field, role, reason in FIELD_CONTRACTS:
        present = field in schema_fields(schema, table)
        rows_available = row_count(status, table)
        required_by_clean_clock = (table, field) in clean_required or field in {
            "signal_strategy",
            "model_ttl_policy",
            "model_policy_version",
            "quote_age_ms",
            "quote_received_at_ns",
            "top_visible_qty",
            "ttl_min",
        }
        rows.append(
            {
                "table": table,
                "field": field,
                "field_id": f"{table}.{field}",
                "variant": VARIANT,
                "role": role,
                "required_for_faithful_replay": True,
                "required_by_clean_evidence_clock": required_by_clean_clock,
                "present_in_sidecar_schema": present,
                "sidecar_or_db_rows_available": rows_available,
                "current_status": field_status(present, rows_available),
                "missing_blocks_promotion": not present or rows_available <= 0,
                "why_required": reason,
            }
        )

    target_gap_rows = target_field_gaps(prereq_rows, rows)
    target_missing_fields = unique_join(
        [
            field
            for row in target_gap_rows
            for field in split_semicolon(row.get("target_missing_required_fields", ""))
        ]
    )
    target_missing_field_occurrence_count = sum(
        to_int(row.get("target_missing_required_field_count", "")) for row in target_gap_rows
    )
    target_unique_missing_field_count = len(split_semicolon(target_missing_fields))
    future_exact_target_count = sum(
        1 for row in target_gap_rows if to_bool(row.get("future_exact_input_required_for_target", ""))
    )
    target_field_ready_count = sum(1 for row in target_gap_rows if to_bool(row.get("target_field_contract_ready", "")))
    missing_rows = [row for row in rows if to_bool(row["missing_blocks_promotion"])]
    missing_ids = [str(row["field_id"]) for row in missing_rows]
    missing_clean_ids = [
        str(row["field_id"])
        for row in rows
        if to_bool(row["required_by_clean_evidence_clock"]) and to_bool(row["missing_blocks_promotion"])
    ]
    required_tables = sorted({row["table"] for row in rows})
    missing_tables = sorted({str(row["table"]) for row in missing_rows})
    current_field_contract_ready = not missing_rows
    clean_policy_identity_ready = (
        to_bool(clean.get("clean_evidence_clock_ready", ""))
        and to_int(clean.get("expected_policy_official_rows", "")) > 0
        and not clean_signal_missing
        and not clean_decision_missing
    )
    current_artifacts_can_support_faithful_replay = current_field_contract_ready and clean_policy_identity_ready
    promotion_usable_replay = to_bool(reconciliation.get("promotion_usable_replay", ""))
    future_exact_input_targets = str(prereq.get("future_exact_input_blocked_targets", "")).strip()
    blockers = []
    if missing_rows:
        blockers.append("missing_required_sidecar_fields")
    if not clean_policy_identity_ready:
        blockers.append("clean_policy_identity_not_ready")
    if future_exact_input_targets:
        blockers.append("future_exact_model_input_capture_required")
    if not promotion_usable_replay:
        blockers.append("current_replay_not_promotion_usable")
    if not to_bool(attempt.get("current_snapshot_repairs_make_replay_promotion_usable", "")):
        blockers.append("current_snapshot_repairs_not_promotion_usable")

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "contract_status": (
            "READY_FOR_FAITHFUL_REPLAY_EVALUATION"
            if current_artifacts_can_support_faithful_replay
            else "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS"
        ),
        "required_field_count": len(rows),
        "present_required_field_count": len(rows) - len(missing_rows),
        "missing_required_field_count": len(missing_rows),
        "required_clean_clock_missing_field_count": len(missing_clean_ids),
        "required_tables": unique_join(required_tables),
        "missing_required_tables": unique_join(missing_tables),
        "missing_required_fields": unique_join(missing_ids),
        "clean_clock_missing_required_fields": unique_join(missing_clean_ids),
        "sidecar_signal_scan_rows": row_count(status, "signal_scan"),
        "sidecar_order_decision_rows": row_count(status, "order_decision"),
        "sidecar_ws_orderbook_top_rows": row_count(status, "ws_orderbook_top"),
        "sidecar_ws_lifecycle_rows": row_count(status, "ws_lifecycle"),
        "clean_evidence_clock_ready": to_bool(clean.get("clean_evidence_clock_ready", "")),
        "expected_policy_official_rows": clean.get("expected_policy_official_rows", ""),
        "blank_policy_official_rows": clean.get("blank_policy_official_rows", ""),
        "current_field_contract_ready": current_field_contract_ready,
        "clean_policy_identity_ready": clean_policy_identity_ready,
        "current_artifacts_can_support_faithful_replay": current_artifacts_can_support_faithful_replay,
        "current_replay_promotion_usable": promotion_usable_replay,
        "target_matrix_future_rows_required_items": target.get("future_rows_required_items", ""),
        "prerequisite_future_exact_input_blocked_targets": future_exact_input_targets,
        "repair_attempt_promotion_usable": to_bool(
            attempt.get("current_snapshot_repairs_make_replay_promotion_usable", "")
        ),
        "repair_attempt_remaining_blockers": attempt.get("remaining_blockers_after_best_attempt", ""),
        "root_cause_field_gap_rows": len(target_gap_rows),
        "root_cause_future_exact_input_target_count": future_exact_target_count,
        "root_cause_target_field_contract_ready_count": target_field_ready_count,
        "root_cause_target_missing_required_field_count": target_missing_field_occurrence_count,
        "root_cause_target_missing_required_field_occurrence_count": target_missing_field_occurrence_count,
        "root_cause_target_unique_missing_required_field_count": target_unique_missing_field_count,
        "root_cause_target_missing_required_fields": target_missing_fields,
        "root_cause_target_repairs_can_make_promotion_usable_now": False,
        "deployable_now": False,
        "near_deployable_now": False,
        "requires_explicit_authorization": not current_artifacts_can_support_faithful_replay,
        "requires_process_control": not current_artifacts_can_support_faithful_replay,
        "no_process_action_taken": True,
        "blockers": unique_join(blockers),
        "next_action": (
            "Collect future clean-clock BTC1H rows with every required sidecar field populated, then rerun "
            "row-for-row replay reconciliation. Do not treat this contract as deployment evidence by itself."
        ),
    }
    return rows, target_gap_rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(
    rows: list[dict[str, Any]],
    target_gap_rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    missing_rows = [row for row in rows if to_bool(row["missing_blocks_promotion"])]
    columns = ["field_id", "role", "current_status", "required_by_clean_evidence_clock", "why_required"]
    target_columns = [
        "repair_target",
        "existing_snapshot_can_repair_independent_replay",
        "target_missing_required_field_count",
        "future_exact_input_required_for_target",
        "blockers",
    ]
    return "\n".join(
        [
            "# BTC1H Faithful Replay Data Contract",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Contract status: `{summary['contract_status']}`",
            f"Missing required fields: `{summary['missing_required_field_count']}`",
            f"Root-cause target missing-field occurrences: `{summary['root_cause_target_missing_required_field_occurrence_count']}`",
            f"Root-cause target unique missing fields: `{summary['root_cause_target_unique_missing_required_field_count']}`",
            f"Current artifacts can support faithful replay: `{summary['current_artifacts_can_support_faithful_replay']}`",
            f"Current replay promotion usable: `{summary['current_replay_promotion_usable']}`",
            f"Root-cause future exact-input targets: `{summary['root_cause_future_exact_input_target_count']}`",
            f"No process action taken: `{summary['no_process_action_taken']}`",
            "",
            "## Missing Required Fields",
            "",
            markdown_table(missing_rows, columns),
            "",
            "## Root-Cause Field Gaps",
            "",
            markdown_table(target_gap_rows, target_columns),
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
    rows, target_gap_rows, summary = build_contract(args)
    write_csv(args.out_dir / "btc1h_faithful_replay_data_contract_fields.csv", rows)
    write_csv(args.out_dir / "btc1h_faithful_replay_root_cause_field_gaps.csv", target_gap_rows)
    write_csv(args.out_dir / "btc1h_faithful_replay_data_contract_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, target_gap_rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
