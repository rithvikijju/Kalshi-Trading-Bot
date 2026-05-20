#!/usr/bin/env python3
"""Read-only preflight for BTC shadow ledger execution-realism schema.

Current paper-shadow processes can keep an old SQLite connection alive even
after the source code has learned to record execution-realism fields. This
audit compares the live ledger DB schemas to the required column contract and
flags rows that cannot count toward deployment until a clean restart/migration
produces the required fields.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_ledger_schema_preflight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

LEDGERS = [
    (
        "btc15m_q250_qty500_firstskip_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_trades.db",
        "btc15m_f2_q250_qty500_firstskip_shadow.py",
    ),
    (
        "btc15m_q250_qty500_firstskip_yes_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_trades.db",
        "btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
    ),
    (
        "btc15m_q1000_yes_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_trades.db",
        "btc15m_f2_q1000_yes_shadow.py",
    ),
    (
        "btc1h_high_conf80_entry70_no_chase_shadow",
        Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_entry70_no_chase_shadow.db",
        "btc_1hr_high_conf80_entry70_no_chase_shadow.py",
    ),
]

REQUIRED_BASE_COLUMNS = [
    "created_at",
    "mode",
    "status",
    "event_ticker",
    "market_ticker",
    "side",
    "contracts",
    "entry_price",
    "yes_order_side",
    "yes_limit_price",
    "entry_fee_estimate",
    "model_p_yes",
    "edge_gross_cents",
    "net_edge_cents",
    "spread_cents",
    "btc_spot",
    "close_time",
    "actual_entry_price",
    "actual_fee_paid",
]

REQUIRED_REALISM_COLUMNS = [
    "available_qty",
    "top_visible_qty",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "quote_age_ms",
    "edge_threshold_cents",
    "strike",
    "ttl_min",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only BTC ledger schema preflight.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def scalar(conn: sqlite3.Connection, query: str) -> Any:
    try:
        row = conn.execute(query).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def rows_with_any_nonnull(conn: sqlite3.Connection, cols: list[str]) -> int:
    if not cols:
        return 0
    clauses = " OR ".join(f"{col} IS NOT NULL" for col in cols)
    value = scalar(conn, f"SELECT COUNT(*) FROM research_live_trades WHERE {clauses}")
    return int(value or 0)


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


def audit_ledger(name: str, path: Path, expected_process_script: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary: dict[str, Any] = {
        "ledger": name,
        "path": str(path),
        "expected_process_script": expected_process_script,
        "db_exists": path.exists(),
        "table_exists": False,
        "row_count": 0,
        "paper_filled_rows": 0,
        "schema_column_count": 0,
        "base_columns_present": 0,
        "base_columns_missing": len(REQUIRED_BASE_COLUMNS),
        "realism_columns_present": 0,
        "realism_columns_missing": len(REQUIRED_REALISM_COLUMNS),
        "rows_with_any_realism_field": 0,
        "restart_required_for_deployable_ledger": True,
        "preflight_status": "DB_MISSING",
        "reason": "ledger DB not found",
    }
    missing_rows: list[dict[str, Any]] = []
    if not path.exists():
        return summary, missing_rows

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_live_trades'"
        ).fetchone()
        if exists is None:
            summary.update({"preflight_status": "TABLE_MISSING", "reason": "research_live_trades table not found"})
            return summary, missing_rows
        cols = table_columns(conn, "research_live_trades")
        colset = set(cols)
        missing_base = [col for col in REQUIRED_BASE_COLUMNS if col not in colset]
        missing_realism = [col for col in REQUIRED_REALISM_COLUMNS if col not in colset]
        present_realism = [col for col in REQUIRED_REALISM_COLUMNS if col in colset]
        row_count = int(scalar(conn, "SELECT COUNT(*) FROM research_live_trades") or 0)
        paper_filled_rows = int(
            scalar(conn, "SELECT COUNT(*) FROM research_live_trades WHERE lower(status) = 'paper_filled'") or 0
        )
        rows_with_realism = rows_with_any_nonnull(conn, present_realism)
        for col in missing_base:
            missing_rows.append({"ledger": name, "column": col, "kind": "base"})
        for col in missing_realism:
            missing_rows.append({"ledger": name, "column": col, "kind": "execution_realism"})

        status = "PASS_SCHEMA_READY"
        reason = "schema has required deployment ledger columns"
        restart_required = False
        if missing_base:
            status = "FAIL_BASE_SCHEMA"
            reason = "base trade columns missing"
            restart_required = True
        elif missing_realism:
            status = "FAIL_REALISM_SCHEMA_RESTART_REQUIRED"
            reason = "execution-realism columns missing from current live ledger DB"
            restart_required = True
        elif row_count and rows_with_realism == 0:
            status = "SCHEMA_READY_BUT_ROWS_LACK_REALISM_VALUES"
            reason = "schema exists but existing rows do not populate execution-realism values"
            restart_required = True

        summary.update(
            {
                "table_exists": True,
                "row_count": row_count,
                "paper_filled_rows": paper_filled_rows,
                "schema_column_count": len(cols),
                "base_columns_present": len(REQUIRED_BASE_COLUMNS) - len(missing_base),
                "base_columns_missing": len(missing_base),
                "realism_columns_present": len(present_realism),
                "realism_columns_missing": len(missing_realism),
                "rows_with_any_realism_field": rows_with_realism,
                "restart_required_for_deployable_ledger": restart_required,
                "preflight_status": status,
                "reason": reason,
            }
        )
        return summary, missing_rows
    finally:
        conn.close()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for name, path, script in LEDGERS:
        summary, missing_rows = audit_ledger(name, path, script)
        summaries.append(summary)
        missing.extend(missing_rows)

    write_csv(args.out_dir / "ledger_schema_preflight_summary.csv", summaries)
    write_csv(args.out_dir / "ledger_schema_missing_columns.csv", missing)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "required_realism_columns": REQUIRED_REALISM_COLUMNS,
        "required_base_columns": REQUIRED_BASE_COLUMNS,
        "note": "Read-only preflight. Do not count future paper fills as deployable evidence until the running shadow DB schema has execution-realism columns populated.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Ledger Schema Preflight",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        csv_table(summaries),
        "",
        "## Interpretation",
        "",
        "- This audit is read-only; it does not migrate or restart live shadows.",
        "- A running shadow with an old 25-column ledger schema cannot produce deployable paper-ledger evidence.",
        "- If a strategy fills before a clean restart/migration, that row is useful as a signal-count diagnostic but not as execution-realistic promotion evidence.",
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
    return 0


def csv_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(empty)"
    keep = [
        "ledger",
        "db_exists",
        "table_exists",
        "row_count",
        "paper_filled_rows",
        "schema_column_count",
        "realism_columns_present",
        "realism_columns_missing",
        "rows_with_any_realism_field",
        "restart_required_for_deployable_ledger",
        "preflight_status",
        "reason",
    ]
    widths = {col: max(len(col), *(len(str(row.get(col, ""))) for row in rows)) for col in keep}
    header = " | ".join(col.ljust(widths[col]) for col in keep)
    sep = "-+-".join("-" * widths[col] for col in keep)
    body = [" | ".join(str(row.get(col, "")).ljust(widths[col]) for col in keep) for row in rows]
    return "\n".join([header, sep, *body])


if __name__ == "__main__":
    raise SystemExit(main())
