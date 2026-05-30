#!/usr/bin/env python3
"""Non-destructive preflight for restarting BTC paper shadows.

The active shadow ledgers can be old SQLite files opened by long-running
processes. This script does not touch those live files. It proves whether the
current source code can create a deployable ledger schema in a fresh DB and can
migrate a copy of each active DB before a user-authorized restart/migration.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_shadow_restart_preflight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
REQUIRED_REPLAY_SIGNAL_SCAN_FIELDS = [
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
REQUIRED_REPLAY_ORDER_DECISION_FIELDS = [
    "signal_strategy",
    "model_ttl_policy",
    "model_policy_version",
]

from scripts import btc_1hr_research_live as live  # noqa: E402
from scripts.build_btc_ledger_schema_preflight import (  # noqa: E402
    LEDGERS,
    REQUIRED_BASE_COLUMNS,
    REQUIRED_REALISM_COLUMNS,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight BTC shadow restart/migration readiness.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def table_columns(conn: sqlite3.Connection) -> list[str]:
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(research_live_trades)").fetchall()]


def table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='research_live_trades'"
    ).fetchone()
    return row is not None


def count_rows(conn: sqlite3.Connection) -> int:
    if not table_exists(conn):
        return 0
    row = conn.execute("SELECT COUNT(*) FROM research_live_trades").fetchone()
    return int(row[0] if row else 0)


def rows_with_any_nonnull(conn: sqlite3.Connection, cols: list[str]) -> int:
    existing = set(table_columns(conn))
    present = [col for col in cols if col in existing]
    if not present:
        return 0
    clauses = " OR ".join(f"{col} IS NOT NULL" for col in present)
    row = conn.execute(f"SELECT COUNT(*) FROM research_live_trades WHERE {clauses}").fetchone()
    return int(row[0] if row else 0)


def schema_status(conn: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
    cols = table_columns(conn) if table_exists(conn) else []
    colset = set(cols)
    missing_base = [col for col in REQUIRED_BASE_COLUMNS if col not in colset]
    missing_realism = [col for col in REQUIRED_REALISM_COLUMNS if col not in colset]
    status = "PASS_SCHEMA_READY"
    if not table_exists(conn):
        status = "FAIL_TABLE_MISSING"
    elif missing_base:
        status = "FAIL_BASE_SCHEMA"
    elif missing_realism:
        status = "FAIL_REALISM_SCHEMA"
    return status, {
        "schema_column_count": len(cols),
        "base_columns_present": len(REQUIRED_BASE_COLUMNS) - len(missing_base),
        "base_columns_missing": len(missing_base),
        "realism_columns_present": len(REQUIRED_REALISM_COLUMNS) - len(missing_realism),
        "realism_columns_missing": len(missing_realism),
        "missing_base_columns": ";".join(missing_base),
        "missing_realism_columns": ";".join(missing_realism),
    }


def sample_signal(ledger: str) -> live.TradeSignal:
    now_ns = 1_770_000_000_000_000_000
    prefix = "KXBTC15M" if ledger.startswith("btc15m") else "KXBTCD"
    return live.TradeSignal(
        event_ticker=f"{prefix}-PRECHECK",
        market_ticker=f"{prefix}-PRECHECK-T99999.99",
        side="yes",
        contracts=1,
        entry_price=0.42,
        yes_order_side="yes",
        yes_limit_price=0.42,
        available_qty=321.0,
        model_p_yes=0.64,
        edge_gross_cents=22.0,
        entry_fee=0.02,
        net_edge_cents=20.0,
        edge_threshold_cents=12.0,
        spread_cents=1.0,
        strike=99999.99,
        btc_spot=100050.0,
        ttl_min=11.0,
        close_time=datetime.now(timezone.utc).isoformat(),
        yes_bid=0.41,
        yes_ask=0.42,
        no_bid=0.57,
        no_ask=0.58,
        signal_received_at_ns=now_ns + 250_000_000,
        quote_received_at_ns=now_ns,
        quote_age_ms=250.0,
        top_visible_qty=321.0,
    )


def insert_smoke_row(conn: sqlite3.Connection, ledger: str) -> tuple[str, str]:
    try:
        live.record_trade(conn, "paper", "paper_filled", sample_signal(ledger))
        row = conn.execute(
            """
            SELECT available_qty, top_visible_qty, quote_received_at_ns,
                   signal_received_at_ns, quote_age_ms, edge_threshold_cents,
                   strike, ttl_min, yes_bid, yes_ask, no_bid, no_ask
            FROM research_live_trades
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception as exc:
        return "FAIL_INSERT_REALISM_FIELDS", repr(exc)
    missing = [col for col in REQUIRED_REALISM_COLUMNS if row[col] is None]
    if missing:
        return "FAIL_INSERT_REALISM_FIELDS", "missing inserted values: " + ";".join(missing)
    return "PASS_INSERT_REALISM_FIELDS", ""


def sample_capture_rows(ledger: str) -> list[tuple[str, dict[str, Any]]]:
    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1_000_000_000)
    prefix = "KXBTC15M" if ledger.startswith("btc15m") else "KXBTCD"
    event_ticker = f"{prefix}-PRECHECK"
    market_ticker = f"{event_ticker}-T99999.99"
    iso = now.isoformat()
    return [
        (
            "capture_health",
            {
                "received_at_ns": now_ns,
                "received_at_utc": iso,
                "mode": "paper",
                "kind": "preflight",
                "event_ticker": event_ticker,
                "kalshi_connected": True,
                "coinbase_connected": True,
                "market_ok": True,
                "market_reason": "preflight",
                "ready_books": 1,
                "total_books": 1,
                "subscribed_markets": 1,
                "btc_spot": 100050.0,
                "btc_spot_age_sec": 0.1,
                "capture_queue_depth": 0,
                "capture_dropped": 0,
                "detail": "capture_sidecar_preflight",
            },
        ),
        (
            "ws_orderbook_top",
            {
                "received_at_ns": now_ns + 1,
                "received_at_utc": iso,
                "market_ticker": market_ticker,
                "event_ticker": event_ticker,
                "sid": 1,
                "seq": 1,
                "yes_bid": 0.41,
                "yes_bid_qty": 321.0,
                "yes_ask": 0.42,
                "yes_ask_qty": 321.0,
                "no_bid": 0.57,
                "no_bid_qty": 321.0,
                "no_ask": 0.58,
                "no_ask_qty": 321.0,
                "btc_spot": 100050.0,
                "source": "preflight",
            },
        ),
        (
            "signal_scan",
            {
                "received_at_ns": now_ns + 2,
                "received_at_utc": iso,
                "reason": "preflight",
                "mode": "paper",
                "event_ticker": event_ticker,
                "changed_markets": 1,
                "evaluated_markets": 1,
                "candidate_count": 1,
                "selected_market": market_ticker,
                "selected_side": "yes",
                "signal_strategy": "high_conf_80_entry70_no_chase" if ledger.startswith("btc1h") else "preflight",
                "model_ttl_policy": live.MODEL_TTL_POLICY,
                "model_policy_version": live.MODEL_POLICY_VERSION,
                "entry_price": 0.42,
                "net_edge_cents": 20.0,
                "model_p_yes": 0.64,
                "edge_threshold_cents": 12.0,
                "spread_cents": 1.0,
                "top_visible_qty": 321.0,
                "quote_received_at_ns": now_ns,
                "quote_age_ms": 2.0,
                "ttl_min": 11.0,
                "close_time": now.isoformat(),
                "btc_spot": 100050.0,
                "btc_candle_time": now.isoformat(),
                "btc_candle_age_sec": 0.5,
                "btc_rv60": 0.25,
                "btc_ret_10m_usd": 12.34,
                "latency_ms": 1.0,
                "blocked_events": 0,
                "action": "preflight_candidate",
                "detail": "capture_sidecar_preflight",
            },
        ),
        (
            "order_decision",
            {
                "received_at_ns": now_ns + 3,
                "received_at_utc": iso,
                "mode": "paper",
                "action": "paper_filled",
                "signal_strategy": "high_conf_80_entry70_no_chase" if ledger.startswith("btc1h") else "preflight",
                "model_ttl_policy": live.MODEL_TTL_POLICY,
                "model_policy_version": live.MODEL_POLICY_VERSION,
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "side": "yes",
                "contracts": 1,
                "entry_price": 0.42,
                "yes_limit_price": 0.42,
                "net_edge_cents": 20.0,
                "btc_spot": 100050.0,
                "estimated_cost": 0.44,
                "portfolio_available": 100.0,
                "portfolio_value": 100.0,
                "client_order_id": "preflight",
                "detail": "capture_sidecar_preflight",
            },
        ),
    ]


def replay_sidecar_fields(path: Path) -> dict[str, set[str]]:
    replay_path = path.with_name(path.name + ".replay.jsonl")
    fields: dict[str, set[str]] = {}
    if not replay_path.exists():
        return fields
    with replay_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            table = str(row.get("table") or "")
            if not table or table in fields:
                continue
            fields[table] = {str(key) for key in row if key != "table"}
            if {"signal_scan", "order_decision"}.issubset(fields):
                break
    return fields


def check_fresh_capture_sidecar(ledger: str, path: Path) -> dict[str, Any]:
    if path.exists():
        path.unlink()
    status_path = path.with_name(path.name + ".status.json")
    if status_path.exists():
        status_path.unlink()
    replay_path = path.with_name(path.name + ".replay.jsonl")
    if replay_path.exists():
        replay_path.unlink()
    recorder = live.LiveCaptureWriter(path, enabled=True, capture_raw_ws=True)
    try:
        for table, row in sample_capture_rows(ledger):
            recorder.record(table, row)
    finally:
        recorder.close()

    result: dict[str, Any] = {
        "fresh_capture_db_path": str(path),
        "fresh_capture_sidecar_path": str(status_path),
        "fresh_capture_sidecar_status": "FAIL_CAPTURE_SIDECAR",
        "fresh_capture_sidecar_error": "",
        "fresh_capture_health_rows": 0,
        "fresh_capture_top_rows": 0,
        "fresh_capture_signal_rows": 0,
        "fresh_capture_signal_nonzero_rows": 0,
        "fresh_capture_sidecar_updated_at_utc": "",
        "fresh_capture_replay_schema_status": "FAIL_REPLAY_SIDECAR_SCHEMA",
        "fresh_capture_replay_schema_error": "",
        "fresh_capture_replay_signal_missing_fields": "",
        "fresh_capture_replay_order_missing_fields": "",
    }
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8-sig"))
        rows_by_table = payload.get("rows_by_table") if isinstance(payload.get("rows_by_table"), dict) else {}
        replay_rows_by_table = (
            payload.get("replay_sidecar_rows_by_table")
            if isinstance(payload.get("replay_sidecar_rows_by_table"), dict)
            else {}
        )
        latest_by_table = payload.get("latest_utc_by_table") if isinstance(payload.get("latest_utc_by_table"), dict) else {}
        result.update(
            {
                "fresh_capture_health_rows": int(rows_by_table.get("capture_health") or 0),
                "fresh_capture_top_rows": int(rows_by_table.get("ws_orderbook_top") or 0),
                "fresh_capture_signal_rows": int(rows_by_table.get("signal_scan") or 0),
                "fresh_capture_order_decision_rows": int(rows_by_table.get("order_decision") or 0),
                "fresh_capture_signal_nonzero_rows": int(payload.get("signal_scan_nonzero_candidate_rows") or 0),
                "fresh_capture_replay_signal_rows": int(replay_rows_by_table.get("signal_scan") or 0),
                "fresh_capture_replay_order_decision_rows": int(replay_rows_by_table.get("order_decision") or 0),
                "fresh_capture_sidecar_updated_at_utc": str(payload.get("updated_at_utc") or ""),
            }
        )
        missing_latest = [
            table
            for table in ["capture_health", "ws_orderbook_top", "signal_scan"]
            if not latest_by_table.get(table)
        ]
        failures = []
        if result["fresh_capture_health_rows"] < 1:
            failures.append("missing_capture_health_rows")
        if result["fresh_capture_top_rows"] < 1:
            failures.append("missing_ws_orderbook_top_rows")
        if result["fresh_capture_signal_rows"] < 1:
            failures.append("missing_signal_scan_rows")
        if result["fresh_capture_order_decision_rows"] < 1:
            failures.append("missing_order_decision_rows")
        if result["fresh_capture_signal_nonzero_rows"] < 1:
            failures.append("missing_nonzero_candidate_rows")
        if missing_latest:
            failures.append("missing_latest_utc_for_" + ";".join(missing_latest))
        if payload.get("failed"):
            failures.append("writer_failed")
        replay_fields = replay_sidecar_fields(path)
        signal_missing = sorted(set(REQUIRED_REPLAY_SIGNAL_SCAN_FIELDS) - replay_fields.get("signal_scan", set()))
        order_missing = sorted(set(REQUIRED_REPLAY_ORDER_DECISION_FIELDS) - replay_fields.get("order_decision", set()))
        result["fresh_capture_replay_signal_missing_fields"] = ";".join(signal_missing)
        result["fresh_capture_replay_order_missing_fields"] = ";".join(order_missing)
        replay_failures = []
        if result["fresh_capture_replay_signal_rows"] < 1:
            replay_failures.append("missing_replay_signal_scan_rows")
        if result["fresh_capture_replay_order_decision_rows"] < 1:
            replay_failures.append("missing_replay_order_decision_rows")
        if signal_missing:
            replay_failures.append("missing_replay_signal_fields:" + ";".join(signal_missing))
        if order_missing:
            replay_failures.append("missing_replay_order_fields:" + ";".join(order_missing))
        if replay_failures:
            result["fresh_capture_replay_schema_error"] = "|".join(replay_failures)
        else:
            result["fresh_capture_replay_schema_status"] = "PASS_REPLAY_SIDECAR_SCHEMA"
        if failures:
            result["fresh_capture_sidecar_error"] = ";".join(failures)
        else:
            result["fresh_capture_sidecar_status"] = "PASS_CAPTURE_SIDECAR"
    except Exception as exc:
        result["fresh_capture_sidecar_error"] = repr(exc)
    return result


def backup_sqlite_to_copy(src: Path, dest: Path) -> tuple[bool, str]:
    if not src.exists():
        return False, "source DB missing"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
        try:
            dest_conn = sqlite3.connect(dest)
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            src_conn.close()
        return True, ""
    except Exception as exc:
        try:
            shutil.copy2(src, dest)
            return True, f"sqlite_backup_failed_used_copy2: {exc!r}"
        except Exception as copy_exc:
            return False, f"sqlite_backup_failed={exc!r}; copy2_failed={copy_exc!r}"


def check_fresh_db(ledger: str, path: Path) -> dict[str, Any]:
    if path.exists():
        path.unlink()
    conn = live.db_connect(path)
    try:
        status, metrics = schema_status(conn)
        insert_status, insert_error = insert_smoke_row(conn, ledger)
        return {
            "fresh_db_path": str(path),
            "fresh_schema_status": status,
            "fresh_insert_status": insert_status,
            "fresh_insert_error": insert_error,
            **{f"fresh_{key}": value for key, value in metrics.items()},
        }
    finally:
        conn.close()


def check_copied_db(ledger: str, src: Path, dest: Path) -> dict[str, Any]:
    copied, copy_error = backup_sqlite_to_copy(src, dest)
    if not copied:
        return {
            "copy_db_path": str(dest),
            "copy_status": "FAIL_COPY_ACTIVE_DB",
            "copy_error": copy_error,
        }
    before_conn = sqlite3.connect(dest)
    before_conn.row_factory = sqlite3.Row
    try:
        before_status, before_metrics = schema_status(before_conn)
        rows_before = count_rows(before_conn)
        realism_rows_before = rows_with_any_nonnull(before_conn, REQUIRED_REALISM_COLUMNS)
    finally:
        before_conn.close()

    migrated_conn = live.db_connect(dest)
    try:
        after_status, after_metrics = schema_status(migrated_conn)
        rows_after_migration = count_rows(migrated_conn)
        realism_rows_after_migration = rows_with_any_nonnull(migrated_conn, REQUIRED_REALISM_COLUMNS)
        insert_status, insert_error = insert_smoke_row(migrated_conn, ledger)
        rows_after_insert = count_rows(migrated_conn)
        realism_rows_after_insert = rows_with_any_nonnull(migrated_conn, REQUIRED_REALISM_COLUMNS)
    finally:
        migrated_conn.close()

    return {
        "copy_db_path": str(dest),
        "copy_status": "PASS_COPIED_ACTIVE_DB",
        "copy_error": copy_error,
        "copy_before_schema_status": before_status,
        "copy_rows_before": rows_before,
        "copy_rows_with_realism_before": realism_rows_before,
        **{f"copy_before_{key}": value for key, value in before_metrics.items()},
        "copy_after_schema_status": after_status,
        "copy_rows_after_migration": rows_after_migration,
        "copy_rows_with_realism_after_migration": realism_rows_after_migration,
        "copy_insert_status": insert_status,
        "copy_insert_error": insert_error,
        "copy_rows_after_insert": rows_after_insert,
        "copy_rows_with_realism_after_insert": realism_rows_after_insert,
        **{f"copy_after_{key}": value for key, value in after_metrics.items()},
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compact_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(empty)"
    keep = [
        "ledger",
        "fresh_schema_status",
        "fresh_insert_status",
        "copy_status",
        "copy_before_schema_status",
        "copy_after_schema_status",
        "copy_insert_status",
        "fresh_capture_sidecar_status",
        "fresh_capture_replay_schema_status",
        "copy_rows_before",
        "copy_rows_with_realism_before",
        "copy_rows_with_realism_after_insert",
        "restart_path_status",
        "deployable_without_live_restart",
    ]
    widths = {col: max(len(col), *(len(str(row.get(col, ""))) for row in rows)) for col in keep}
    header = " | ".join(col.ljust(widths[col]) for col in keep)
    sep = "-+-".join("-" * widths[col] for col in keep)
    body = [" | ".join(str(row.get(col, "")).ljust(widths[col]) for col in keep) for row in rows]
    return "\n".join([header, sep, *body])


def row_status(row: dict[str, Any]) -> str:
    if row.get("fresh_capture_sidecar_status") != "PASS_CAPTURE_SIDECAR":
        return "FAIL_RESTART_PATH"
    if row.get("fresh_capture_replay_schema_status") != "PASS_REPLAY_SIDECAR_SCHEMA":
        return "FAIL_RESTART_PATH"
    if not row.get("active_db_exists", False):
        required = [
            row.get("fresh_schema_status") == "PASS_SCHEMA_READY",
            row.get("fresh_insert_status") == "PASS_INSERT_REALISM_FIELDS",
        ]
        return "PASS_RESTART_PATH_READY" if all(required) else "FAIL_RESTART_PATH"
    required = [
        row.get("fresh_schema_status") == "PASS_SCHEMA_READY",
        row.get("fresh_insert_status") == "PASS_INSERT_REALISM_FIELDS",
        row.get("copy_after_schema_status") == "PASS_SCHEMA_READY",
        row.get("copy_insert_status") == "PASS_INSERT_REALISM_FIELDS",
    ]
    return "PASS_RESTART_PATH_READY" if all(required) else "FAIL_RESTART_PATH"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fresh_dir = args.out_dir / "fresh_dbs"
    copy_dir = args.out_dir / "copied_active_dbs"
    fresh_capture_dir = args.out_dir / "fresh_capture_dbs"
    fresh_dir.mkdir(exist_ok=True)
    copy_dir.mkdir(exist_ok=True)
    fresh_capture_dir.mkdir(exist_ok=True)

    rows: list[dict[str, Any]] = []
    for ledger, active_path, script_name in LEDGERS:
        row: dict[str, Any] = {
            "ledger": ledger,
            "expected_process_script": script_name,
            "active_db_path": str(active_path),
            "active_db_exists": active_path.exists(),
        }
        row.update(check_fresh_db(ledger, fresh_dir / f"{ledger}.db"))
        row.update(check_fresh_capture_sidecar(ledger, fresh_capture_dir / f"{ledger}_capture.duckdb"))
        if active_path.exists():
            row.update(check_copied_db(ledger, active_path, copy_dir / f"{ledger}.db"))
        else:
            row.update(
                {
                    "copy_db_path": "",
                    "copy_status": "SKIP_NO_ACTIVE_DB_NEW_START",
                    "copy_error": "",
                    "copy_before_schema_status": "NOT_APPLICABLE_NEW_DB",
                    "copy_after_schema_status": "NOT_APPLICABLE_NEW_DB",
                    "copy_insert_status": "NOT_APPLICABLE_NEW_DB",
                    "copy_rows_before": 0,
                    "copy_rows_with_realism_before": 0,
                    "copy_rows_with_realism_after_insert": 0,
                }
            )
        row["restart_path_status"] = row_status(row)
        row["deployable_without_live_restart"] = False
        row["interpretation"] = (
            "Current code can create/migrate a deployable ledger schema, but active runners still need explicit restart/migration."
            if row["restart_path_status"] == "PASS_RESTART_PATH_READY" and row["active_db_exists"]
            else "Current code can create a deployable ledger schema for this new paper-only shadow, but it has not been started."
            if row["restart_path_status"] == "PASS_RESTART_PATH_READY"
            else "Do not restart for deployable evidence until this failure is fixed."
        )
        rows.append(row)

    write_csv(args.out_dir / "shadow_restart_preflight_summary.csv", rows)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Non-destructive preflight only. Active live/paper processes were not stopped, restarted, or migrated.",
        "required_base_columns": REQUIRED_BASE_COLUMNS,
        "required_realism_columns": REQUIRED_REALISM_COLUMNS,
        "required_replay_signal_scan_fields": REQUIRED_REPLAY_SIGNAL_SCAN_FIELDS,
        "required_replay_order_decision_fields": REQUIRED_REPLAY_ORDER_DECISION_FIELDS,
        "requires_capture_status_sidecar": True,
        "requires_replay_sidecar_model_input_fields": True,
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Shadow Restart Preflight",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        compact_table(rows),
        "",
        "## Interpretation",
        "",
        "- This is a non-destructive preflight; it uses fresh DBs and copies of active DBs.",
        "- A PASS means the current code can create or migrate the required schema after an explicit user-authorized restart/migration.",
        "- A PASS also requires the current capture writer to emit a lock-free status sidecar from a fresh capture DB.",
        "- A PASS also requires the lock-free replay sidecar to preserve exact model-input fields needed by BTC1H clean evidence-clock replay.",
        "- It does not make the currently running shadows deployable, because their live ledger handles still point at stale schemas.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0 if all(row["restart_path_status"] == "PASS_RESTART_PATH_READY" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
