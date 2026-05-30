#!/usr/bin/env python3
"""Audit BTC1H execution realism from the paused remote snapshot.

This is a diagnostic for old BTC1H shadow rows.  It can prove whether the
paused snapshot contains decision-time top-book fields, fees, quote age, and
visible quantity, but it cannot make those rows promotion evidence because the
current clean-clock/policy-identity gates still require a controlled restart
and fresh rows.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_snapshot_execution_realism_latest_codex"
DEFAULT_LEDGER_DB = (
    PROJECT_ROOT
    / "runtime"
    / "remote_snapshots"
    / "snapshot_20260521_145951"
    / "btc_1hr_high_conf80_entry70_no_chase_shadow.db"
)
DEFAULT_OFFICIAL_TRADES = (
    BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
)
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
REQUIRED_FIELDS = [
    "created_at",
    "event_ticker",
    "market_ticker",
    "side",
    "contracts",
    "entry_price",
    "entry_fee_estimate",
    "actual_entry_price",
    "actual_fee_paid",
    "top_visible_qty",
    "quote_age_ms",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "spread_cents",
    "ttl_min",
    "model_p_yes",
    "net_edge_cents",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_LEDGER_DB)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--max-quote-age-ms", type=float, default=250.0)
    parser.add_argument("--max-spread-cents", type=float, default=2.0)
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


def load_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in con.execute(
                "select * from research_live_trades where status = 'paper_filled' order by created_at, market_ticker"
            )
        ]
    finally:
        con.close()
    return rows


def present(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.lower() not in {"nan", "none", "nat"}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def side_ask(row: dict[str, Any]) -> float:
    side = str(row.get("side", "")).lower()
    if side == "yes":
        return to_float(row.get("yes_ask"), float("nan"))
    if side == "no":
        return to_float(row.get("no_ask"), float("nan"))
    return float("nan")


def rate(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if bool(row.get(key))) / len(rows)


def official_summary(official_rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = [row for row in official_rows if row.get("ledger") == LEDGER and row.get("official_status") == "finalized"]
    pnl = sum(to_float(row.get("official_pnl")) for row in rows)
    mismatches = sum(1 for row in rows if str(row.get("official_proxy_result_mismatch", "")).lower() == "true")
    blank_policy = sum(1 for row in rows if not present(row.get("model_policy_version")))
    return {
        "official_rows": len(rows),
        "official_pnl": round(pnl, 10),
        "official_proxy_mismatches": mismatches,
        "blank_policy_rows": blank_policy,
    }


def build_audit(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ledger_rows = load_ledger_rows(args.ledger_db)
    official = official_summary(read_csv(args.official_trades))
    row_flags: list[dict[str, Any]] = []
    for row in ledger_rows:
        expected_entry = side_ask(row)
        entry = to_float(row.get("entry_price"))
        actual_entry = to_float(row.get("actual_entry_price"))
        fee = to_float(row.get("actual_fee_paid"), -1.0)
        quote_age = to_float(row.get("quote_age_ms"), -1.0)
        spread = to_float(row.get("spread_cents"), 999.0)
        top_visible = to_float(row.get("top_visible_qty"), -1.0)
        contracts = to_float(row.get("contracts"), 0.0)
        row_flags.append(
            {
                "created_at": row.get("created_at", ""),
                "event_ticker": row.get("event_ticker", ""),
                "market_ticker": row.get("market_ticker", ""),
                "side": row.get("side", ""),
                "contracts": contracts,
                "entry_price": entry,
                "actual_entry_price": actual_entry,
                "expected_side_ask": expected_entry,
                "actual_fee_paid": fee,
                "top_visible_qty": top_visible,
                "quote_age_ms": quote_age,
                "spread_cents": spread,
                "required_fields_present": all(present(row.get(field)) for field in REQUIRED_FIELDS),
                "entry_matches_side_ask": abs(entry - expected_entry) <= 1e-9,
                "actual_entry_matches_entry": abs(actual_entry - entry) <= 1e-9,
                "fee_present_nonnegative": fee >= 0.0,
                "top_visible_qty_ge_contracts": top_visible >= contracts,
                "quote_age_le_limit": 0.0 <= quote_age <= args.max_quote_age_ms,
                "spread_le_limit": spread <= args.max_spread_cents,
                "pre_clean_clock_row": True,
                "promotion_usable": False,
            }
        )

    field_presence = {
        field: (sum(1 for row in ledger_rows if present(row.get(field))) / len(ledger_rows) if ledger_rows else 0.0)
        for field in REQUIRED_FIELDS
    }
    missing_fields = [field for field, presence_rate in field_presence.items() if presence_rate < 1.0]
    stale_quote_rows = sum(1 for row in row_flags if not row["quote_age_le_limit"])
    event_count = len({str(row.get("event_ticker", "")) for row in ledger_rows if present(row.get("event_ticker"))})
    duplicate_event_rows = len(ledger_rows) - event_count
    blockers: list[str] = []
    if not ledger_rows:
        blockers.append("no_snapshot_ledger_rows")
    if missing_fields:
        blockers.append("snapshot_execution_fields_missing")
    if stale_quote_rows:
        blockers.append("quote_age_above_limit")
    if duplicate_event_rows:
        blockers.append("duplicate_event_rows")
    if official["official_proxy_mismatches"]:
        blockers.append("official_proxy_mismatch_present")
    if official["blank_policy_rows"]:
        blockers.append("pre_clean_clock_blank_policy_rows")
    blockers.append("old_snapshot_not_clean_clock_promotion_evidence")

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": LEDGER,
        "ledger_db": str(args.ledger_db),
        "official_trades": str(args.official_trades),
        "rows": len(ledger_rows),
        "official_rows": official["official_rows"],
        "official_pnl": official["official_pnl"],
        "official_proxy_mismatches": official["official_proxy_mismatches"],
        "blank_policy_rows": official["blank_policy_rows"],
        "required_field_complete_rate": min(field_presence.values()) if field_presence else 0.0,
        "missing_fields": ";".join(missing_fields),
        "entry_matches_side_ask_rate": rate(row_flags, "entry_matches_side_ask"),
        "actual_entry_matches_entry_rate": rate(row_flags, "actual_entry_matches_entry"),
        "fee_present_nonnegative_rate": rate(row_flags, "fee_present_nonnegative"),
        "top_visible_qty_ge_contracts_rate": rate(row_flags, "top_visible_qty_ge_contracts"),
        "quote_age_le_limit_rate": rate(row_flags, "quote_age_le_limit"),
        "stale_quote_rows": stale_quote_rows,
        "max_quote_age_ms": max((row["quote_age_ms"] for row in row_flags), default=0.0),
        "spread_le_limit_rate": rate(row_flags, "spread_le_limit"),
        "max_spread_cents": max((row["spread_cents"] for row in row_flags), default=0.0),
        "min_top_visible_qty": min((row["top_visible_qty"] for row in row_flags), default=0.0),
        "duplicate_event_rows": duplicate_event_rows,
        "promotion_usable": False,
        "audit_status": "DIAGNOSTIC_OLD_SNAPSHOT_EXECUTION_FIELDS_NOT_PROMOTION_USABLE",
        "blockers": ";".join(dict.fromkeys(blockers)),
        "next_action": (
            "Keep this as old-snapshot diagnostics; require clean-clock rows with fresh policy identity, "
            "quote age within limit, official agreement, and replay parity."
        ),
    }
    return row_flags, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    row_flags, summary = build_audit(args)
    write_csv(args.out_dir / "btc1h_snapshot_execution_realism_rows.csv", row_flags)
    write_csv(args.out_dir / "btc1h_snapshot_execution_realism_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC1H Snapshot Execution-Realism Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Audit status: `{summary['audit_status']}`",
        f"Promotion usable: `{summary['promotion_usable']}`",
        f"Rows: `{summary['rows']}`",
        f"Official rows / PnL: `{summary['official_rows']}` / `{summary['official_pnl']}`",
        f"Blockers: `{summary['blockers']}`",
        "",
        "## Summary",
        "",
        markdown_table([summary], list(summary.keys())),
        "",
        "## Row Flags",
        "",
        markdown_table(
            row_flags,
            [
                "event_ticker",
                "market_ticker",
                "side",
                "entry_price",
                "top_visible_qty",
                "quote_age_ms",
                "entry_matches_side_ask",
                "top_visible_qty_ge_contracts",
                "quote_age_le_limit",
            ],
        ),
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
