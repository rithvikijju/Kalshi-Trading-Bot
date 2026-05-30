#!/usr/bin/env python3
"""Compare BTC1H causal replay row sets across variants.

This is a diagnostic guard against double-counting a runner-up policy that
looks different in historical filters but produces the same live-websocket
replay rows as the active candidate on the available snapshot.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_replay_variant_overlap_latest_codex"
DEFAULT_BASE = (
    BACKTEST_ROOT
    / "btc1h_core_ws_counterfactual_snapshot_fullscan_prefilter_highconf_latest_codex"
    / "btc1h_core_ws_counterfactual_trades.csv"
)
DEFAULT_CHALLENGER = (
    BACKTEST_ROOT
    / "btc1h_core_ws_counterfactual_snapshot_fullscan_entry59_latest_codex"
    / "btc1h_core_ws_counterfactual_trades.csv"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--base-trades", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--challenger-trades", type=Path, default=DEFAULT_CHALLENGER)
    parser.add_argument("--base-variant", default="high_conf_80_entry70_no_chase")
    parser.add_argument("--challenger-variant", default="high_conf_80_entry59_70_no_chase")
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


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def row_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        str(row.get("event_ticker", "")).strip().upper(),
        str(row.get("market_ticker", "")).strip().upper(),
        str(row.get("side", "")).strip().lower(),
        str(row.get("entry_received_at_ns", "")).strip(),
    )


def filter_variant(rows: list[dict[str, str]], variant: str) -> list[dict[str, str]]:
    return [row for row in rows if str(row.get("variant", "")).strip() == variant]


def build_overlap(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows_all = read_csv(args.base_trades)
    challenger_rows_all = read_csv(args.challenger_trades)
    base_rows = filter_variant(base_rows_all, args.base_variant)
    challenger_rows = filter_variant(challenger_rows_all, args.challenger_variant)

    base_by_key = {row_key(row): row for row in base_rows if all(row_key(row))}
    challenger_by_key = {row_key(row): row for row in challenger_rows if all(row_key(row))}
    base_keys = set(base_by_key)
    challenger_keys = set(challenger_by_key)
    shared_keys = sorted(base_keys & challenger_keys)
    base_only = sorted(base_keys - challenger_keys)
    challenger_only = sorted(challenger_keys - base_keys)

    details: list[dict[str, Any]] = []
    for key in shared_keys:
        base = base_by_key[key]
        challenger = challenger_by_key[key]
        base_pnl = to_float(base.get("pnl", ""))
        challenger_pnl = to_float(challenger.get("pnl", ""))
        details.append(
            {
                "match_status": "shared",
                "event_ticker": key[0],
                "market_ticker": key[1],
                "side": key[2],
                "entry_received_at_ns": key[3],
                "base_pnl": round(base_pnl, 6),
                "challenger_pnl": round(challenger_pnl, 6),
                "pnl_diff": round(challenger_pnl - base_pnl, 6),
            }
        )
    for key in base_only:
        base = base_by_key[key]
        details.append(
            {
                "match_status": "base_only",
                "event_ticker": key[0],
                "market_ticker": key[1],
                "side": key[2],
                "entry_received_at_ns": key[3],
                "base_pnl": round(to_float(base.get("pnl", "")), 6),
                "challenger_pnl": "",
                "pnl_diff": "",
            }
        )
    for key in challenger_only:
        challenger = challenger_by_key[key]
        details.append(
            {
                "match_status": "challenger_only",
                "event_ticker": key[0],
                "market_ticker": key[1],
                "side": key[2],
                "entry_received_at_ns": key[3],
                "base_pnl": "",
                "challenger_pnl": round(to_float(challenger.get("pnl", "")), 6),
                "pnl_diff": "",
            }
        )

    base_pnl = sum(to_float(row.get("pnl", "")) for row in base_rows)
    challenger_pnl = sum(to_float(row.get("pnl", "")) for row in challenger_rows)
    exact_match = bool(base_rows or challenger_rows) and not base_only and not challenger_only
    missing = not args.base_trades.exists() or not args.challenger_trades.exists()
    status = "MISSING_REPLAY_ARTIFACT" if missing else ("EXACT_ROW_SET_MATCH" if exact_match else "HAS_INCREMENTAL_ROWS")
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_variant": args.base_variant,
        "challenger_variant": args.challenger_variant,
        "base_trades": str(args.base_trades),
        "challenger_trades": str(args.challenger_trades),
        "status": status,
        "base_rows": len(base_rows),
        "challenger_rows": len(challenger_rows),
        "shared_rows": len(shared_keys),
        "base_only_rows": len(base_only),
        "challenger_only_rows": len(challenger_only),
        "exact_row_set_match": exact_match,
        "base_pnl": round(base_pnl, 6),
        "challenger_pnl": round(challenger_pnl, 6),
        "pnl_diff": round(challenger_pnl - base_pnl, 6),
        "independent_challenger_rows": len(challenger_only),
        "deployable_now": False,
        "note": (
            "Exact overlap means the challenger adds no independent live-websocket replay evidence on this snapshot; "
            "it still needs broader causal replay or future clean forward rows before a separate shadow."
        )
        if exact_match
        else "Non-overlap is diagnostic only and still requires official settlement and forward evidence.",
    }
    return details, summary


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in cols) + " |")
    return "\n".join(lines)


def build_report(details: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    sample = details[:20]
    return (
        "# BTC1H Replay Variant Overlap Audit\n\n"
        f"Created UTC: `{summary['created_at_utc']}`\n"
        f"Status: `{summary['status']}`\n"
        f"Base: `{summary['base_variant']}` rows `{summary['base_rows']}` PnL `{summary['base_pnl']}`\n"
        f"Challenger: `{summary['challenger_variant']}` rows `{summary['challenger_rows']}` PnL `{summary['challenger_pnl']}`\n"
        f"Independent challenger rows: `{summary['independent_challenger_rows']}`\n\n"
        "## Row Sample\n\n"
        + markdown_table(sample)
        + "\n\n## Summary\n\n```json\n"
        + json.dumps(summary, indent=2, sort_keys=True)
        + "\n```\n"
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    details, summary = build_overlap(args)
    write_csv(args.out_dir / "btc1h_replay_variant_overlap_details.csv", details)
    write_csv(args.out_dir / "btc1h_replay_variant_overlap_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(details, summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
