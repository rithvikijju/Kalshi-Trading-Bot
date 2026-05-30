#!/usr/bin/env python3
"""Measure BTC1H official results after strict snapshot execution filters.

This audit answers a narrow question: if we enforce the decision-time execution
field checks on the old paused BTC1H snapshot, what official-settled sample is
left?  It is diagnostic only and cannot promote old pre-clean-clock rows.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_execution_filter_impact_latest_codex"
DEFAULT_ROW_FLAGS = (
    BACKTEST_ROOT
    / "btc1h_snapshot_execution_realism_latest_codex"
    / "btc1h_snapshot_execution_realism_rows.csv"
)
DEFAULT_OFFICIAL_TRADES = (
    BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
)
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--row-flags", type=Path, default=DEFAULT_ROW_FLAGS)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--min-clean-official-rows", type=int, default=50)
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


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("event_ticker", "")).strip(),
        str(row.get("market_ticker", "")).strip(),
        str(row.get("side", "")).strip().lower(),
    )


def official_by_key(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    out: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        if row.get("ledger") != LEDGER:
            continue
        out[key(row)] = row
    return out


def row_passes_strict_execution(row: dict[str, str]) -> bool:
    checks = [
        "required_fields_present",
        "entry_matches_side_ask",
        "actual_entry_matches_entry",
        "fee_present_nonnegative",
        "top_visible_qty_ge_contracts",
        "quote_age_le_limit",
        "spread_le_limit",
    ]
    return all(to_bool(row.get(check, "")) for check in checks)


def profile_metrics(
    *,
    profile: str,
    rows: list[dict[str, str]],
    official_lookup: dict[tuple[str, str, str], dict[str, str]],
    total_rows: int,
    min_clean_official_rows: int,
) -> dict[str, Any]:
    official_rows: list[dict[str, str]] = []
    missing_official: list[str] = []
    for row in rows:
        official = official_lookup.get(key(row))
        if official:
            official_rows.append(official)
        else:
            missing_official.append(str(row.get("market_ticker", "")))
    official_pnl = sum(to_float(row.get("official_pnl")) for row in official_rows)
    proxy_pnl = sum(to_float(row.get("proxy_pnl")) for row in official_rows)
    mismatches = sum(1 for row in official_rows if to_bool(row.get("official_proxy_result_mismatch", "")))
    wins = sum(1 for row in official_rows if to_bool(row.get("official_win", "")))
    removed = total_rows - len(rows)
    blockers: list[str] = []
    if len(official_rows) < min_clean_official_rows:
        blockers.append("too_few_execution_filtered_official_rows")
    if mismatches > 0:
        blockers.append("official_proxy_mismatch_remaining")
    if missing_official:
        blockers.append("official_rows_missing_for_filtered_markets")
    blockers.append("old_snapshot_not_clean_clock_promotion_evidence")
    blockers.append("replay_parity_still_required")
    return {
        "profile": profile,
        "rows": len(rows),
        "removed_rows": removed,
        "official_rows": len(official_rows),
        "official_pnl": round(official_pnl, 10),
        "proxy_pnl": round(proxy_pnl, 10),
        "official_minus_proxy_pnl": round(official_pnl - proxy_pnl, 10),
        "official_win_rate": round(wins / len(official_rows), 10) if official_rows else 0.0,
        "official_proxy_mismatches": mismatches,
        "official_proxy_mismatch_rate": round(mismatches / len(official_rows), 10) if official_rows else 0.0,
        "missing_official_markets": ";".join(missing_official),
        "removed_markets": "",
        "deployable_now": False,
        "near_deployable_candidate": False,
        "blockers": ";".join(dict.fromkeys(blockers)),
    }

def build_audit(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    row_flags = read_csv(args.row_flags)
    official_lookup = official_by_key(read_csv(args.official_trades))
    strict_rows = [row for row in row_flags if row_passes_strict_execution(row)]
    removed_rows = [row for row in row_flags if not row_passes_strict_execution(row)]

    rows_by_market = {str(row.get("market_ticker", "")): row for row in row_flags}

    def metrics(profile: str, selected: list[dict[str, str]]) -> dict[str, Any]:
        out = profile_metrics(
            profile=profile,
            rows=selected,
            official_lookup=official_lookup,
            total_rows=len(row_flags),
            min_clean_official_rows=args.min_clean_official_rows,
        )
        out["removed_markets"] = ";".join(
            sorted(market for market, row in rows_by_market.items() if row not in selected)
        )
        return out

    profile_rows = [
        metrics("all_snapshot_rows", row_flags),
        metrics("strict_execution_filtered_rows", strict_rows),
    ]

    removal_rows: list[dict[str, Any]] = []
    for row in removed_rows:
        official = official_lookup.get(key(row), {})
        reasons = []
        if not to_bool(row.get("quote_age_le_limit", "")):
            reasons.append("quote_age_above_limit")
        if not to_bool(row.get("top_visible_qty_ge_contracts", "")):
            reasons.append("top_visible_qty_below_contracts")
        if not to_bool(row.get("entry_matches_side_ask", "")):
            reasons.append("entry_not_side_ask")
        if not to_bool(row.get("fee_present_nonnegative", "")):
            reasons.append("fee_missing_or_negative")
        removal_rows.append(
            {
                "event_ticker": row.get("event_ticker", ""),
                "market_ticker": row.get("market_ticker", ""),
                "side": row.get("side", ""),
                "entry_price": row.get("entry_price", ""),
                "quote_age_ms": row.get("quote_age_ms", ""),
                "top_visible_qty": row.get("top_visible_qty", ""),
                "official_result": official.get("official_result", ""),
                "proxy_result": official.get("proxy_result", ""),
                "official_pnl": official.get("official_pnl", ""),
                "proxy_pnl": official.get("proxy_pnl", ""),
                "official_proxy_result_mismatch": official.get("official_proxy_result_mismatch", ""),
                "removal_reasons": ";".join(reasons),
            }
        )

    strict = profile_rows[1]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_flags": str(args.row_flags),
        "official_trades": str(args.official_trades),
        "strict_rows": strict["rows"],
        "strict_removed_rows": strict["removed_rows"],
        "strict_official_rows": strict["official_rows"],
        "strict_official_pnl": strict["official_pnl"],
        "strict_proxy_pnl": strict["proxy_pnl"],
        "strict_official_minus_proxy_pnl": strict["official_minus_proxy_pnl"],
        "strict_official_proxy_mismatches": strict["official_proxy_mismatches"],
        "strict_official_proxy_mismatch_rate": strict["official_proxy_mismatch_rate"],
        "strict_removed_markets": strict["removed_markets"],
        "deployable_now": False,
        "near_deployable_candidate": False,
        "audit_status": "DIAGNOSTIC_EXECUTION_FILTERED_OLD_SNAPSHOT_NOT_PROMOTION_USABLE",
        "blockers": strict["blockers"],
        "next_action": (
            "Treat execution-filtered old rows as sensitivity evidence only; require a clean evidence clock, "
            "official/proxy agreement, larger official sample, and row-for-row replay parity."
        ),
    }
    return profile_rows, removal_rows, summary


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
    profile_rows, removal_rows, summary = build_audit(args)
    write_csv(args.out_dir / "btc1h_execution_filter_profiles.csv", profile_rows)
    write_csv(args.out_dir / "btc1h_execution_filter_removed_rows.csv", removal_rows)
    write_csv(args.out_dir / "btc1h_execution_filter_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC1H Execution-Filter Impact Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Audit status: `{summary['audit_status']}`",
        f"Deployable now: `{summary['deployable_now']}`",
        f"Near-deployable candidate: `{summary['near_deployable_candidate']}`",
        f"Blockers: `{summary['blockers']}`",
        "",
        "## Profiles",
        "",
        markdown_table(
            profile_rows,
            [
                "profile",
                "rows",
                "removed_rows",
                "official_rows",
                "official_pnl",
                "proxy_pnl",
                "official_proxy_mismatches",
                "official_proxy_mismatch_rate",
                "blockers",
            ],
        ),
        "",
        "## Removed Rows",
        "",
        markdown_table(
            removal_rows,
            [
                "event_ticker",
                "market_ticker",
                "side",
                "entry_price",
                "quote_age_ms",
                "official_pnl",
                "proxy_pnl",
                "removal_reasons",
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
