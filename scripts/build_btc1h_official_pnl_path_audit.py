#!/usr/bin/env python3
"""Build a BTC1H official-settlement PnL path audit.

Summary official PnL is not enough for BTC promotion discussions.  This
read-only audit sequences the current BTC1H official REST-settled shadow rows,
computes cumulative official PnL and drawdown, and labels the result as
diagnostic-only unless the clean-clock and collection gates are satisfied.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_official_pnl_path_latest_codex"
DEFAULT_STRICT_EXECUTION_ROWS = (
    BACKTEST_ROOT
    / "btc1h_execution_filtered_basis_mismatch_latest_codex"
    / "btc1h_execution_filtered_basis_strict_rows.csv"
)
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--official-trades",
        type=Path,
        default=None,
        help="Official settlement trades CSV. Defaults to latest remote/local shadow official settlement output.",
    )
    parser.add_argument(
        "--clean-clock-collection-preflight-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_clock_collection_preflight_latest_codex"
        / "btc1h_clean_clock_collection_preflight_summary.csv",
    )
    parser.add_argument(
        "--strict-execution-rows",
        type=Path,
        default=DEFAULT_STRICT_EXECUTION_ROWS,
        help="Strict execution-filtered BTC1H rows used to build a stricter diagnostic path.",
    )
    parser.add_argument("--min-clean-official-rows", type=int, default=50)
    parser.add_argument("--max-proxy-official-mismatch-rate", type=float, default=0.02)
    return parser.parse_args()


def latest_file(pattern: str, filename: str) -> Path | None:
    matches = [p / filename for p in BACKTEST_ROOT.glob(pattern) if (p / filename).exists()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def default_official_trades_path() -> Path | None:
    return latest_file("remote_btc_shadow_official_settlement_latest_codex", "shadow_official_trades.csv") or latest_file(
        "btc_shadow_official_settlement_latest_codex",
        "shadow_official_trades.csv",
    )


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
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


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def semi_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        for part in str(value or "").split(";"):
            text = part.strip()
            if text and text not in out:
                out.append(text)
    return ";".join(out)


def round_float(value: float) -> float:
    return round(float(value), 6)


def finalized_btc1h_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        if str(row.get("ledger", "")).strip() != LEDGER:
            continue
        if str(row.get("official_result", "")).strip().lower() not in {"yes", "no"}:
            continue
        out.append(row)
    return sorted(out, key=lambda row: parse_dt(row.get("created_at", "")))


def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("event_ticker", "")).strip(),
        str(row.get("market_ticker", "")).strip(),
        str(row.get("side", "")).strip().lower(),
    )


def path_metrics(
    *,
    rows: list[dict[str, str]],
    path_scope: str,
    min_clean_official_rows: int,
    max_proxy_official_mismatch_rate: float,
    clean_ready: bool,
    collection_ready: bool,
    process_control_authorized: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    details: list[dict[str, Any]] = []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_drawdown_index = 0
    losing_streak = 0
    max_losing_streak = 0
    wins = 0
    official_pnl_total = 0.0
    proxy_pnl_total = 0.0
    proxy_pnl_rows = 0
    mismatches = 0
    max_single_loss = 0.0
    quote_age_rows = 0
    max_quote_age_ms = 0.0
    quote_age_gt_90ms_rows = 0

    for idx, row in enumerate(rows, start=1):
        official_pnl = to_float(row.get("official_pnl", ""))
        proxy_pnl = to_float(row.get("proxy_pnl", ""), default=0.0)
        proxy_pnl_present = str(row.get("proxy_pnl", "")).strip() != ""
        mismatch = to_bool(row.get("official_proxy_result_mismatch", ""))
        official_win = to_bool(row.get("official_win", ""))
        quote_age_present = str(row.get("quote_age_ms", "")).strip() != ""
        quote_age_ms = to_float(row.get("quote_age_ms", ""), default=0.0)
        if quote_age_present:
            quote_age_rows += 1
            max_quote_age_ms = max(max_quote_age_ms, quote_age_ms)
            if quote_age_ms > 90.0:
                quote_age_gt_90ms_rows += 1
        official_pnl_total += official_pnl
        if proxy_pnl_present:
            proxy_pnl_total += proxy_pnl
            proxy_pnl_rows += 1
        if mismatch:
            mismatches += 1
        if official_win:
            wins += 1
            losing_streak = 0
        else:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        max_single_loss = min(max_single_loss, official_pnl)
        cumulative += official_pnl
        peak = max(peak, cumulative)
        drawdown = cumulative - peak
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_index = idx
        path_quality_note = semi_join(
            [
                "official_proxy_mismatch" if mismatch else "",
                "quote_age_gt_90ms" if quote_age_present and quote_age_ms > 90.0 else "",
                "official_loss" if official_pnl < 0 else "",
            ]
        )
        details.append(
            {
                "path_scope": path_scope,
                "variant": VARIANT,
                "ledger": LEDGER,
                "sequence_index": idx,
                "created_at": row.get("created_at", ""),
                "event_ticker": row.get("event_ticker", ""),
                "market_ticker": row.get("market_ticker", ""),
                "side": row.get("side", ""),
                "entry_price": row.get("entry_price", ""),
                "fee": row.get("fee", ""),
                "contracts": row.get("contracts", ""),
                "official_result": row.get("official_result", ""),
                "proxy_result": row.get("proxy_result", ""),
                "official_proxy_result_mismatch": mismatch,
                "official_pnl": round_float(official_pnl),
                "proxy_pnl": round_float(proxy_pnl) if proxy_pnl_present else "",
                "cumulative_official_pnl": round_float(cumulative),
                "running_peak_official_pnl": round_float(peak),
                "drawdown": round_float(drawdown),
                "entry_btc_spot": row.get("entry_btc_spot", ""),
                "expiration_value": row.get("expiration_value", ""),
                "proxy_close_spot": row.get("proxy_close_spot", ""),
                "ticker_strike": row.get("ticker_strike", row.get("floor_strike", "")),
                "official_minus_proxy_spot": row.get("official_minus_proxy_spot", ""),
                "proxy_close_minus_strike": row.get("proxy_close_minus_strike", ""),
                "official_expiration_minus_strike": row.get("official_expiration_minus_strike", ""),
                "quote_age_ms": row.get("quote_age_ms", ""),
                "top_visible_qty": row.get("top_visible_qty", ""),
                "path_quality_note": path_quality_note,
                "signal_strategy": row.get("signal_strategy", ""),
                "model_ttl_policy": row.get("model_ttl_policy", ""),
                "model_policy_version": row.get("model_policy_version", ""),
            }
        )

    official_rows = len(rows)
    mismatch_rate = (mismatches / official_rows) if official_rows else 0.0
    promotion_countable = (
        official_rows >= min_clean_official_rows
        and mismatch_rate <= max_proxy_official_mismatch_rate
        and clean_ready
        and collection_ready
        and process_control_authorized
    )
    blockers = [
        "too_few_clean_official_rows" if official_rows < min_clean_official_rows else "",
        "official_proxy_mismatch_rate_above_limit"
        if official_rows and mismatch_rate > max_proxy_official_mismatch_rate
        else "",
        "official_proxy_mismatch_present" if mismatches else "",
        "path_from_pre_clean_clock_rows",
        "clean_clock_not_ready" if not clean_ready else "",
        "collection_evidence_not_ready" if not collection_ready else "",
        "process_control_not_authorized" if not process_control_authorized else "",
        "replay_parity_still_required",
        "execution_realism_gate_still_required",
    ]
    path_status = (
        "PROMOTION_COUNTABLE_OFFICIAL_PATH_READY"
        if promotion_countable
        else "DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE"
    )
    worst_row = details[max_drawdown_index - 1] if max_drawdown_index > 0 and details else {}
    drawdown_to_pnl_ratio = abs(max_drawdown) / official_pnl_total if official_pnl_total > 0 else 0.0
    summary = {
        "path_scope": path_scope,
        "variant": VARIANT,
        "ledger": LEDGER,
        "path_audit_status": path_status,
        "path_status": path_status,
        "promotion_countable_path": promotion_countable,
        "current_rows_count_for_promotion": promotion_countable,
        "official_rows": official_rows,
        "min_clean_official_rows": min_clean_official_rows,
        "official_pnl": round_float(official_pnl_total),
        "official_win_rate": round_float(wins / official_rows) if official_rows else 0.0,
        "proxy_pnl_rows": proxy_pnl_rows,
        "proxy_pnl": round_float(proxy_pnl_total) if proxy_pnl_rows else "",
        "official_minus_proxy_pnl": round_float(official_pnl_total - proxy_pnl_total) if proxy_pnl_rows else "",
        "proxy_official_mismatches": mismatches,
        "proxy_official_mismatch_rate": round_float(mismatch_rate),
        "max_drawdown": round_float(max_drawdown),
        "max_drawdown_abs": round_float(abs(max_drawdown)),
        "drawdown_to_pnl_ratio": round_float(drawdown_to_pnl_ratio),
        "max_drawdown_sequence_index": max_drawdown_index,
        "max_drawdown_market": worst_row.get("market_ticker", ""),
        "max_drawdown_created_at": worst_row.get("created_at", ""),
        "max_single_loss": round_float(max_single_loss),
        "max_losing_streak": max_losing_streak,
        "quote_age_rows": quote_age_rows,
        "max_quote_age_ms": round_float(max_quote_age_ms) if quote_age_rows else "",
        "quote_age_gt_90ms_rows": quote_age_gt_90ms_rows,
        "first_created_at": details[0]["created_at"] if details else "",
        "last_created_at": details[-1]["created_at"] if details else "",
        "clean_evidence_clock_ready": clean_ready,
        "collection_evidence_ready": collection_ready,
        "process_control_authorized": process_control_authorized,
        "no_process_action_taken": True,
        "blockers": semi_join(blockers),
        "next_action": (
            "Use this row-level path as diagnostic evidence only. Promotion discussion still requires an "
            "explicitly authorized clean evidence clock, >=50 clean official rows, official/proxy agreement, "
            "complete execution realism, and faithful replay parity."
        ),
    }
    return details, summary


def build_path(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    official_trades = args.official_trades or default_official_trades_path()
    rows = finalized_btc1h_rows(read_csv(official_trades))
    preflight = first(read_csv(args.clean_clock_collection_preflight_summary))
    clean_ready = to_bool(preflight.get("clean_evidence_clock_ready", ""))
    collection_ready = to_bool(preflight.get("collection_evidence_ready", ""))
    process_control_authorized = to_bool(preflight.get("process_control_authorized", ""))
    all_details, all_summary = path_metrics(
        rows=rows,
        path_scope="all_official_rows",
        min_clean_official_rows=args.min_clean_official_rows,
        max_proxy_official_mismatch_rate=args.max_proxy_official_mismatch_rate,
        clean_ready=clean_ready,
        collection_ready=collection_ready,
        process_control_authorized=process_control_authorized,
    )

    strict_source_rows = read_csv(args.strict_execution_rows)
    strict_keys = {row_key(row) for row in strict_source_rows}
    strict_rows = [row for row in rows if row_key(row) in strict_keys]
    strict_details, strict_summary = path_metrics(
        rows=strict_rows,
        path_scope="strict_execution_filtered_rows",
        min_clean_official_rows=args.min_clean_official_rows,
        max_proxy_official_mismatch_rate=args.max_proxy_official_mismatch_rate,
        clean_ready=clean_ready,
        collection_ready=collection_ready,
        process_control_authorized=process_control_authorized,
    )
    removed_by_strict = sorted(row.get("market_ticker", "") for row in rows if row_key(row) not in strict_keys)

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "ledger": LEDGER,
        "official_trades_path": str(official_trades.resolve()) if official_trades else "",
        "official_trades_exists": bool(official_trades and official_trades.exists()),
        "strict_execution_rows_path": str(args.strict_execution_rows),
        "strict_execution_rows_exists": args.strict_execution_rows.exists(),
        **{key: value for key, value in all_summary.items() if key not in {"variant", "ledger"}},
        "strict_execution_path_audit_status": strict_summary["path_audit_status"]
        if strict_source_rows
        else "MISSING_STRICT_EXECUTION_ROWS",
        "strict_execution_path_rows": strict_summary["official_rows"] if strict_source_rows else "",
        "strict_execution_path_official_pnl": strict_summary["official_pnl"] if strict_source_rows else "",
        "strict_execution_path_official_win_rate": strict_summary["official_win_rate"] if strict_source_rows else "",
        "strict_execution_path_proxy_pnl": strict_summary["proxy_pnl"] if strict_source_rows else "",
        "strict_execution_path_official_minus_proxy_pnl": strict_summary["official_minus_proxy_pnl"]
        if strict_source_rows
        else "",
        "strict_execution_path_proxy_official_mismatches": strict_summary["proxy_official_mismatches"]
        if strict_source_rows
        else "",
        "strict_execution_path_proxy_official_mismatch_rate": strict_summary["proxy_official_mismatch_rate"]
        if strict_source_rows
        else "",
        "strict_execution_path_max_drawdown": strict_summary["max_drawdown"] if strict_source_rows else "",
        "strict_execution_path_max_drawdown_abs": strict_summary["max_drawdown_abs"] if strict_source_rows else "",
        "strict_execution_path_drawdown_to_pnl_ratio": strict_summary["drawdown_to_pnl_ratio"]
        if strict_source_rows
        else "",
        "strict_execution_path_quote_age_gt_90ms_rows": strict_summary["quote_age_gt_90ms_rows"]
        if strict_source_rows
        else "",
        "strict_execution_path_max_quote_age_ms": strict_summary["max_quote_age_ms"] if strict_source_rows else "",
        "strict_execution_path_current_rows_count_for_promotion": strict_summary["current_rows_count_for_promotion"]
        if strict_source_rows
        else False,
        "strict_execution_path_removed_markets": ";".join(removed_by_strict) if strict_source_rows else "",
        "strict_execution_path_blockers": strict_summary["blockers"] if strict_source_rows else "strict_execution_rows_missing",
    }
    return all_details, strict_details, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(
    details: list[dict[str, Any]],
    strict_details: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    columns = [
        "sequence_index",
        "created_at",
        "market_ticker",
        "side",
        "official_result",
        "proxy_result",
        "official_pnl",
        "cumulative_official_pnl",
        "drawdown",
        "official_proxy_result_mismatch",
        "path_quality_note",
    ]
    return "\n".join(
        [
            "# BTC1H Official PnL Path Audit",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Path status: `{summary['path_audit_status']}`",
            f"Official rows: `{summary['official_rows']}`",
            f"Official PnL: `{summary['official_pnl']}`",
            f"Max drawdown: `{summary['max_drawdown']}`",
            f"Proxy/official mismatches: `{summary['proxy_official_mismatches']}`",
            f"Rows over 90ms quote age: `{summary['quote_age_gt_90ms_rows']}`",
            f"Strict execution path rows: `{summary['strict_execution_path_rows']}`",
            f"Strict execution official PnL: `{summary['strict_execution_path_official_pnl']}`",
            f"Strict execution max drawdown: `{summary['strict_execution_path_max_drawdown']}`",
            f"Strict execution proxy/official mismatches: `{summary['strict_execution_path_proxy_official_mismatches']}`",
            f"No process action taken: `{summary['no_process_action_taken']}`",
            "",
            "## All Official Row Sequence",
            "",
            markdown_table(details, columns),
            "",
            "## Strict Execution-Filtered Row Sequence",
            "",
            markdown_table(strict_details, columns),
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
    details, strict_details, summary = build_path(args)
    write_csv(args.out_dir / "btc1h_official_pnl_path_sequence.csv", details)
    write_csv(args.out_dir / "btc1h_official_pnl_path_details.csv", details)
    write_csv(args.out_dir / "btc1h_official_pnl_path_strict_execution_sequence.csv", strict_details)
    write_csv(args.out_dir / "btc1h_official_pnl_path_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(details, strict_details, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
