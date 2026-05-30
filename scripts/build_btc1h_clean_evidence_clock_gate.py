#!/usr/bin/env python3
"""Gate BTC1H forward evidence against a clean deployability evidence clock.

The BTC1H shadow can be alive and historically promising while still failing
promotion evidence because the running process predates exact model-input
capture. This report makes that blocker explicit and machine-readable.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_clean_evidence_clock_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"
EXPECTED_TTL_POLICY = "scan_time_close_minus_now_v1"
EXPECTED_POLICY_VERSION = "btc1h_live_model_20260522_scan_ttl_v1"

REQUIRED_SIGNAL_SCAN_FIELDS = [
    "signal_strategy",
    "model_ttl_policy",
    "model_policy_version",
    "edge_threshold_cents",
    "spread_cents",
    "top_visible_qty",
    "quote_received_at_ns",
    "quote_age_ms",
    "ttl_min",
    "close_time",
    "btc_candle_time",
    "btc_candle_age_sec",
    "btc_rv60",
    "btc_ret_10m_usd",
]

REQUIRED_ORDER_DECISION_FIELDS = [
    "signal_strategy",
    "model_ttl_policy",
    "model_policy_version",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--status-json",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "remote_status" / "btc1h_status_latest.json",
        help="BTC1H capture status JSON pulled from the remote collector.",
    )
    p.add_argument(
        "--sidecar-schema-json",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "remote_status" / "btc1h_replay_sidecar_schema_latest.json",
        help="JSON mapping replay-sidecar table name to observed row columns.",
    )
    p.add_argument(
        "--forward-status-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex" / "shadow_status.csv",
        help="Fallback BTC forward status summary; used when the runtime status snapshot is stale or unavailable.",
    )
    p.add_argument(
        "--replay-sidecar-schema-scan-lines",
        type=int,
        default=300_000,
        help="Max replay-sidecar JSONL rows to scan when deriving schema directly from the sidecar.",
    )
    p.add_argument(
        "--shadow-official-summary",
        type=Path,
        default=BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_summary.csv",
    )
    p.add_argument(
        "--shadow-official-policy-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "remote_btc_shadow_official_settlement_latest_codex"
        / "shadow_official_policy_summary.csv",
    )
    p.add_argument(
        "--multi-holdout-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_multi_holdout_research_latest_codex" / "btc1h_candidate_gate_summary.csv",
    )
    p.add_argument(
        "--selected-parity-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_selected_signal_model_parity_latest_codex"
        / "btc1h_selected_signal_model_parity_summary.csv",
    )
    p.add_argument(
        "--replay-reconciliation-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_vs_ledger_reconciliation_latest_codex"
        / "btc1h_replay_vs_ledger_reconciliation_summary.csv",
    )
    p.add_argument("--min-official-rows", type=int, default=50)
    p.add_argument("--max-status-age-minutes", type=float, default=15.0)
    p.add_argument("--expected-model-ttl-policy", default=EXPECTED_TTL_POLICY)
    p.add_argument("--expected-model-policy-version", default=EXPECTED_POLICY_VERSION)
    return p.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = re.sub(r"(\.\d{6})\d+([+-]\d\d:\d\d)$", r"\1\2", text)
        out = datetime.fromisoformat(text)
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    except ValueError:
        return None


def first_row(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        ok = True
        for key, expected in filters.items():
            if str(row.get(key, "")).strip() != expected:
                ok = False
                break
        if ok:
            return row
    return {}


def sum_filtered(rows: list[dict[str, str]], field: str, **filters: str) -> float:
    total = 0.0
    for row in rows:
        if all(str(row.get(key, "")).strip() == expected for key, expected in filters.items()):
            total += to_float(row.get(field))
    return total


def schema_fields(schema: dict[str, Any], table: str) -> set[str]:
    raw = schema.get(table, [])
    if isinstance(raw, dict):
        raw = raw.get("columns", [])
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw}


def parse_jsonish_dict(value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def status_from_forward_row(row: dict[str, str]) -> dict[str, Any]:
    if not row:
        return {}
    rows_by_table = {
        "capture_health": to_float(row.get("capture_health_rows")),
        "signal_scan": to_float(row.get("signal_scan_rows")),
        "order_decision": to_float(row.get("order_decision_rows")),
        "ws_orderbook_top": to_float(row.get("ws_orderbook_top_rows")),
    }
    rows_by_table = {key: int(value) for key, value in rows_by_table.items() if value}
    return {
        "updated_at_utc": row.get("capture_sidecar_updated_at_utc")
        or row.get("capture_sidecar_mtime_utc")
        or row.get("signal_scan_latest")
        or "",
        "enabled": str(row.get("running", "")).strip().lower() == "true",
        "failed": bool(str(row.get("capture_sidecar_error", "")).strip()),
        "last_error": row.get("capture_sidecar_error", ""),
        "dropped": 0,
        "queue_depth": "",
        "rows_by_table": rows_by_table,
        "replay_sidecar": row.get("replay_sidecar_path", ""),
        "replay_sidecar_rows_by_table": parse_jsonish_dict(row.get("replay_sidecar_rows_by_table")),
        "source": "forward_status_summary",
    }


def choose_status(status_json: dict[str, Any], forward_status: dict[str, Any], now: datetime) -> tuple[dict[str, Any], str]:
    if not forward_status:
        return status_json, "status_json" if status_json else ""
    if not status_json:
        return forward_status, "forward_status_summary"
    status_age = status_age_minutes(status_json, now)
    forward_age = status_age_minutes(forward_status, now)
    if forward_age is not None and (status_age is None or forward_age < status_age):
        return forward_status, "forward_status_summary"
    return status_json, "status_json"


def derive_schema_from_replay_sidecar(path: Path, max_lines: int) -> dict[str, list[str]]:
    if not path.exists() or max_lines <= 0:
        return {}
    fields_by_table: dict[str, set[str]] = {}
    required_by_table = {
        "signal_scan": set(REQUIRED_SIGNAL_SCAN_FIELDS),
        "order_decision": set(REQUIRED_ORDER_DECISION_FIELDS),
    }
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                table = str(row.get("table") or "")
                if not table:
                    continue
                fields = fields_by_table.setdefault(table, set())
                fields.update(str(key) for key in row if key != "table")
                if all(required.issubset(fields_by_table.get(table_name, set())) for table_name, required in required_by_table.items()):
                    break
    except OSError:
        return {}
    return {table: sorted(fields) for table, fields in fields_by_table.items()}


def status_age_minutes(status: dict[str, Any], now: datetime) -> float | None:
    updated = parse_dt(status.get("updated_at_utc"))
    if updated is None:
        return None
    return max(0.0, (now - updated).total_seconds() / 60.0)


def pct(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def build_gate(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    now = datetime.now(timezone.utc)
    status = read_json(args.status_json)
    schema = read_json(args.sidecar_schema_json)
    forward_rows = read_csv_rows(args.forward_status_summary)
    forward_status_row = first_row(forward_rows, name=LEDGER)
    forward_status = status_from_forward_row(forward_status_row)
    status, status_source = choose_status(status, forward_status, now)
    if not schema:
        replay_sidecar = str(status.get("replay_sidecar") or forward_status_row.get("replay_sidecar_path", "") or "")
        if replay_sidecar:
            schema = derive_schema_from_replay_sidecar(Path(replay_sidecar), args.replay_sidecar_schema_scan_lines)
            schema_source = "replay_sidecar_jsonl" if schema else ""
        else:
            schema_source = ""
    else:
        schema_source = "sidecar_schema_json"
    official_rows = read_csv_rows(args.shadow_official_summary)
    policy_rows = read_csv_rows(args.shadow_official_policy_summary)
    holdout_rows = read_csv_rows(args.multi_holdout_summary)
    parity_rows = read_csv_rows(args.selected_parity_summary)
    reconciliation_rows = read_csv_rows(args.replay_reconciliation_summary)

    official = first_row(official_rows, ledger=LEDGER, scope="since")
    policy_expected = [
        row
        for row in policy_rows
        if row.get("ledger") == LEDGER
        and row.get("scope") == "since"
        and row.get("model_ttl_policy") == args.expected_model_ttl_policy
        and row.get("model_policy_version") == args.expected_model_policy_version
    ]
    policy_blank_rows = sum(
        to_float(row.get("official_filled_rows"))
        for row in policy_rows
        if row.get("ledger") == LEDGER
        and row.get("scope") == "since"
        and (not str(row.get("model_ttl_policy", "")).strip() or not str(row.get("model_policy_version", "")).strip())
    )
    expected_policy_official_rows = sum(to_float(row.get("official_filled_rows")) for row in policy_expected)
    holdout = first_row(holdout_rows, variant=VARIANT)
    parity = first_row(parity_rows, ledger=LEDGER)
    reconciliation = reconciliation_rows[0] if reconciliation_rows else {}

    signal_fields = schema_fields(schema, "signal_scan")
    decision_fields = schema_fields(schema, "order_decision")
    missing_signal = sorted(set(REQUIRED_SIGNAL_SCAN_FIELDS) - signal_fields)
    missing_decision = sorted(set(REQUIRED_ORDER_DECISION_FIELDS) - decision_fields)

    sidecar_rows = status.get("replay_sidecar_rows_by_table", {}) if isinstance(status, dict) else {}
    db_rows = status.get("rows_by_table", {}) if isinstance(status, dict) else {}
    age = status_age_minutes(status, now)
    official_count = to_float(official.get("official_filled_rows"))
    official_pnl = to_float(official.get("official_pnl"))
    official_win = to_float(official.get("official_win_rate"))
    proxy_mismatches = to_float(official.get("official_proxy_result_mismatches"))
    mismatch_rate = pct(proxy_mismatches, official_count)
    captured_ttl_available = to_float(parity.get("captured_ttl_available_rows"))
    selected_rows = to_float(parity.get("selected_signal_rows"))
    captured_ttl_pass = to_float(parity.get("captured_ttl_pass_rows"))
    replay_usable = to_bool(reconciliation.get("promotion_usable_replay"))

    blockers: list[dict[str, Any]] = []

    def add_blocker(blocker: str, severity: str, detail: str) -> None:
        blockers.append({"blocker": blocker, "severity": severity, "detail": detail})

    if not status:
        add_blocker("btc1h_status_json_missing", "fatal", str(args.status_json))
    if age is None:
        add_blocker("btc1h_status_updated_at_missing", "fatal", "cannot prove capture freshness")
    elif age > args.max_status_age_minutes:
        add_blocker(
            "btc1h_status_stale",
            "fatal",
            f"age_minutes={age:.2f} > max={args.max_status_age_minutes:.2f}",
        )
    if status and not bool(status.get("enabled")):
        add_blocker("btc1h_capture_disabled", "fatal", "capture status enabled=false")
    if status and bool(status.get("failed")):
        add_blocker("btc1h_capture_failed", "fatal", str(status.get("last_error", "")))
    if to_float(status.get("dropped")) > 0:
        add_blocker("btc1h_capture_dropped_rows", "fatal", f"dropped={status.get('dropped')}")
    if not schema:
        add_blocker("btc1h_sidecar_schema_missing", "fatal", str(args.sidecar_schema_json))
    if missing_signal:
        add_blocker(
            "btc1h_sidecar_signal_model_inputs_missing",
            "fatal",
            ";".join(missing_signal),
        )
    if missing_decision:
        add_blocker(
            "btc1h_sidecar_order_policy_fields_missing",
            "fatal",
            ";".join(missing_decision),
        )
    if expected_policy_official_rows <= 0:
        add_blocker(
            "btc1h_expected_policy_official_rows_missing",
            "fatal",
            f"expected={args.expected_model_policy_version}/{args.expected_model_ttl_policy}",
        )
    if policy_blank_rows > 0:
        add_blocker("btc1h_blank_policy_official_rows_present", "fatal", f"blank_policy_rows={policy_blank_rows:g}")
    if captured_ttl_available < selected_rows or selected_rows <= 0:
        add_blocker(
            "btc1h_selected_signal_captured_ttl_not_complete",
            "fatal",
            f"captured_ttl_available={captured_ttl_available:g}, selected_rows={selected_rows:g}",
        )
    elif captured_ttl_pass < captured_ttl_available:
        add_blocker(
            "btc1h_selected_signal_captured_ttl_parity_failed",
            "fatal",
            f"captured_ttl_pass={captured_ttl_pass:g}, available={captured_ttl_available:g}",
        )
    if not replay_usable:
        add_blocker(
            "btc1h_replay_vs_ledger_not_promotion_usable",
            "fatal",
            str(reconciliation.get("blockers", reconciliation.get("replay_blockers", ""))),
        )
    if official_count < args.min_official_rows:
        add_blocker(
            "btc1h_too_few_official_rows",
            "fatal",
            f"official_rows={official_count:g}, min={args.min_official_rows}",
        )
    if official_pnl <= 0:
        add_blocker("btc1h_official_pnl_not_positive", "fatal", f"official_pnl={official_pnl:g}")
    if proxy_mismatches > 0:
        add_blocker(
            "btc1h_proxy_official_mismatch_present",
            "fatal",
            f"mismatches={proxy_mismatches:g}, rate={mismatch_rate:.4f}",
        )
    if not to_bool(holdout.get("research_promising")):
        add_blocker("btc1h_multi_holdout_not_promising", "fatal", "multi-holdout research_promising=false")

    restart_required_blockers = {
        "btc1h_sidecar_signal_model_inputs_missing",
        "btc1h_sidecar_order_policy_fields_missing",
        "btc1h_expected_policy_official_rows_missing",
        "btc1h_blank_policy_official_rows_present",
        "btc1h_selected_signal_captured_ttl_not_complete",
    }
    blocker_names = {row["blocker"] for row in blockers}
    if blocker_names & restart_required_blockers:
        gate_status = "BLOCKED_CONTROLLED_RESTART_REQUIRED"
        next_action = "controlled_btc1h_shadow_restart_to_start_clean_evidence_clock"
    elif blockers:
        gate_status = "BLOCKED_COLLECT_OR_FIX"
        next_action = "collect_more_official_rows_or_fix_row_faithful_replay"
    else:
        gate_status = "PASS_CLEAN_EVIDENCE_CLOCK"
        next_action = "eligible_for_conservative_readiness_review"

    summary = {
        "created_at_utc": now.isoformat(),
        "ledger": LEDGER,
        "variant": VARIANT,
        "gate_status": gate_status,
        "clean_evidence_clock_ready": gate_status == "PASS_CLEAN_EVIDENCE_CLOCK",
        "deployable_now": False,
        "next_action": next_action,
        "blocker_count": len(blockers),
        "status_json": str(args.status_json),
        "status_source": status_source,
        "sidecar_schema_json": str(args.sidecar_schema_json),
        "sidecar_schema_source": schema_source,
        "forward_status_summary": str(args.forward_status_summary),
        "status_updated_at_utc": status.get("updated_at_utc", ""),
        "status_age_minutes": "" if age is None else round(age, 4),
        "capture_enabled": bool(status.get("enabled")) if status else False,
        "capture_failed": bool(status.get("failed")) if status else "",
        "capture_dropped": status.get("dropped", ""),
        "capture_queue_depth": status.get("queue_depth", ""),
        "db_signal_scan_rows": db_rows.get("signal_scan", ""),
        "db_order_decision_rows": db_rows.get("order_decision", ""),
        "sidecar_signal_scan_rows": sidecar_rows.get("signal_scan", ""),
        "sidecar_order_decision_rows": sidecar_rows.get("order_decision", ""),
        "sidecar_signal_missing_fields": ";".join(missing_signal),
        "sidecar_order_decision_missing_fields": ";".join(missing_decision),
        "expected_model_ttl_policy": args.expected_model_ttl_policy,
        "expected_model_policy_version": args.expected_model_policy_version,
        "expected_policy_official_rows": expected_policy_official_rows,
        "blank_policy_official_rows": policy_blank_rows,
        "official_rows": official_count,
        "official_pnl": official_pnl,
        "official_win_rate": official_win,
        "official_proxy_mismatches": proxy_mismatches,
        "official_proxy_mismatch_rate": round(mismatch_rate, 6),
        "min_official_rows": args.min_official_rows,
        "multi_holdout_research_promising": to_bool(holdout.get("research_promising")),
        "multi_holdout_all_positive_holdouts": holdout.get("all_positive_holdouts", ""),
        "multi_holdout_all_holdouts": holdout.get("all_holdouts", ""),
        "multi_holdout_ws_positive_cadences": holdout.get("ws_positive_cadences", ""),
        "multi_holdout_ws_cadences": holdout.get("ws_cadences", ""),
        "selected_signal_rows": selected_rows,
        "captured_ttl_available_rows": captured_ttl_available,
        "captured_ttl_pass_rows": captured_ttl_pass,
        "replay_ledger_promotion_usable": replay_usable,
        "replay_ledger_exact_match_rate": reconciliation.get("exact_match_rate", reconciliation.get("replay_exact_match_rate", "")),
    }
    info = {
        "created_at_utc": now.isoformat(),
        "inputs": {
            "status_json": str(args.status_json),
            "sidecar_schema_json": str(args.sidecar_schema_json),
            "forward_status_summary": str(args.forward_status_summary),
            "shadow_official_summary": str(args.shadow_official_summary),
            "shadow_official_policy_summary": str(args.shadow_official_policy_summary),
            "multi_holdout_summary": str(args.multi_holdout_summary),
            "selected_parity_summary": str(args.selected_parity_summary),
            "replay_reconciliation_summary": str(args.replay_reconciliation_summary),
        },
        "required_signal_scan_fields": REQUIRED_SIGNAL_SCAN_FIELDS,
        "required_order_decision_fields": REQUIRED_ORDER_DECISION_FIELDS,
    }
    return summary, blockers, info


def build_report(summary: dict[str, Any], blockers: list[dict[str, Any]]) -> str:
    lines = [
        "# BTC1H Clean Evidence Clock Gate",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Gate status: `{summary['gate_status']}`",
        f"- Clean evidence clock ready: `{summary['clean_evidence_clock_ready']}`",
        f"- Next action: `{summary['next_action']}`",
        f"- Deployable now: `{summary['deployable_now']}`",
        "",
        "## Current BTC1H Evidence",
        "",
        f"- Official rows: `{summary['official_rows']}` / min `{summary['min_official_rows']}`",
        f"- Official PnL: `{summary['official_pnl']}`",
        f"- Official win rate: `{summary['official_win_rate']}`",
        f"- Proxy/official mismatches: `{summary['official_proxy_mismatches']}` "
        f"({summary['official_proxy_mismatch_rate']})",
        f"- Multi-holdout promising: `{summary['multi_holdout_research_promising']}`",
        f"- Fixed holdouts positive: `{summary['multi_holdout_all_positive_holdouts']} / "
        f"{summary['multi_holdout_all_holdouts']}`",
        f"- WS cadences positive: `{summary['multi_holdout_ws_positive_cadences']} / "
        f"{summary['multi_holdout_ws_cadences']}`",
        "",
        "## Capture Clock",
        "",
        f"- Status updated UTC: `{summary['status_updated_at_utc']}`",
        f"- Status age minutes: `{summary['status_age_minutes']}`",
        f"- Capture enabled/failed/dropped: `{summary['capture_enabled']}` / "
        f"`{summary['capture_failed']}` / `{summary['capture_dropped']}`",
        f"- DB signal/order rows: `{summary['db_signal_scan_rows']}` / `{summary['db_order_decision_rows']}`",
        f"- Sidecar signal/order rows: `{summary['sidecar_signal_scan_rows']}` / "
        f"`{summary['sidecar_order_decision_rows']}`",
        f"- Missing sidecar signal fields: `{summary['sidecar_signal_missing_fields']}`",
        f"- Missing sidecar order fields: `{summary['sidecar_order_decision_missing_fields']}`",
        "",
        "## Policy/Replay Clock",
        "",
        f"- Expected policy rows: `{summary['expected_policy_official_rows']}`",
        f"- Blank-policy official rows: `{summary['blank_policy_official_rows']}`",
        f"- Captured TTL rows: `{summary['captured_ttl_available_rows']} / "
        f"{summary['selected_signal_rows']}`",
        f"- Captured TTL pass rows: `{summary['captured_ttl_pass_rows']}`",
        f"- Replay-vs-ledger promotion usable: `{summary['replay_ledger_promotion_usable']}`",
        "",
        "## Blockers",
        "",
    ]
    if not blockers:
        lines.append("- None.")
    else:
        for row in blockers:
            lines.append(f"- `{row['blocker']}` ({row['severity']}): {row['detail']}")
    lines += [
        "",
        "## Interpretation",
        "",
        "- This gate does not search thresholds or certify deployment by itself.",
        "- It asks whether the active BTC1H forward rows are from a clean, exact-input evidence clock.",
        "- If it says controlled restart required, old cached-TTL rows remain diagnostic only.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary, blockers, info = build_gate(args)
    write_csv(args.out_dir / "btc1h_clean_evidence_clock_summary.csv", [summary])
    write_csv(args.out_dir / "btc1h_clean_evidence_clock_blockers.csv", blockers)
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(summary, blockers), encoding="utf-8")
    print(build_report(summary, blockers))
    print(f"Wrote {args.out_dir}")
    return 0 if summary["clean_evidence_clock_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
