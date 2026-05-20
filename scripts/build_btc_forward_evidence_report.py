#!/usr/bin/env python3
"""Build a compact forward-evidence report for BTC deployability review.

This script is read-only. It combines the current process/shadow status,
official settlement ledger audit, and deployment readiness output into one
daily/heartbeat artifact. It does not search for new strategies and it does
not touch live processes.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_forward_evidence_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_STATUS_DIR = BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex"
DEFAULT_OFFICIAL_DIR = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex"
DEFAULT_READINESS_DIR = BACKTEST_ROOT / "deployment_readiness_latest_codex"

FOCUS_READINESS = [
    ("BTC15M", "q250:mat_grid_00019"),
    ("BTC15M", "q250_firstskip_qty500"),
    ("BTC15M", "q1000_yes"),
    ("BTC15M", "btc15m_q250_qty500_firstskip_shadow:all"),
    ("BTC15M", "btc15m_q250_qty500_firstskip_shadow:since"),
    ("BTC15M", "btc15m_q1000_yes_shadow:all"),
    ("BTC15M", "btc15m_q1000_yes_shadow:since"),
    ("BTC1H", "high_conf_80_entry70_no_chase"),
    ("BTC1H", "btc1h_high_conf80_entry70_no_chase_shadow:all"),
    ("BTC1H", "btc1h_high_conf80_entry70_no_chase_shadow:since"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a BTC forward evidence report.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--status-dir", type=Path, default=DEFAULT_STATUS_DIR)
    parser.add_argument("--official-dir", type=Path, default=DEFAULT_OFFICIAL_DIR)
    parser.add_argument("--readiness-dir", type=Path, default=DEFAULT_READINESS_DIR)
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        if value == "":
            return default
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    return int(to_float(value, float(default)))


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


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


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    if df.empty:
        return "_No rows._"
    existing = [col for col in columns if col in df.columns]
    if not existing:
        return "_No requested columns available._"
    values = df[existing].fillna("").astype(str)
    widths = {
        col: max(len(col), *(len(str(value)) for value in values[col].tolist()))
        for col in existing
    }
    header = "| " + " | ".join(col.ljust(widths[col]) for col in existing) + " |"
    divider = "| " + " | ".join("-" * widths[col] for col in existing) + " |"
    lines = [header, divider]
    for _, row in values.iterrows():
        lines.append("| " + " | ".join(str(row[col]).ljust(widths[col]) for col in existing) + " |")
    return "\n".join(lines)


def focused_readiness(readiness: pd.DataFrame) -> pd.DataFrame:
    if readiness.empty:
        return readiness
    rows = []
    for family, candidate in FOCUS_READINESS:
        mask = (readiness.get("family", "") == family) & (readiness.get("candidate", "") == candidate)
        rows.append(readiness.loc[mask])
    focused = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if focused.empty:
        return readiness.head(20).copy()
    return focused


def build_shadow_summary(status: pd.DataFrame, official: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, status_row in status.iterrows():
        name = str(status_row.get("name", ""))
        official_all = official[
            (official.get("ledger", "") == name) & (official.get("scope", "") == "all")
        ]
        official_since = official[
            (official.get("ledger", "") == name) & (official.get("scope", "") == "since")
        ]
        all_row = official_all.iloc[0] if not official_all.empty else {}
        since_row = official_since.iloc[0] if not official_since.empty else {}
        rows.append(
            {
                "name": name,
                "family": status_row.get("family", ""),
                "kind": status_row.get("kind", ""),
                "running": status_row.get("running", ""),
                "pids": status_row.get("pids", ""),
                "process_count": status_row.get("process_count", ""),
                "duplicate_process_count": status_row.get("duplicate_process_count", ""),
                "process_hygiene_status": status_row.get("process_hygiene_status", ""),
                "source_freshness_status": status_row.get("source_freshness_status", ""),
                "source_latest_path": status_row.get("source_latest_path", ""),
                "source_latest_mtime_utc": status_row.get("source_latest_mtime_utc", ""),
                "process_predates_latest_source": status_row.get("process_predates_latest_source", ""),
                "capture_read_source": status_row.get("capture_read_source", ""),
                "capture_db_mtime_utc": status_row.get("capture_db_mtime_utc", ""),
                "capture_db_size_bytes": status_row.get("capture_db_size_bytes", ""),
                "capture_health_latest": status_row.get("capture_health_latest", ""),
                "ws_orderbook_top_latest": status_row.get("ws_orderbook_top_latest", ""),
                "signal_scan_rows": status_row.get("signal_scan_rows", ""),
                "signal_scan_nonzero_candidate_rows": status_row.get("signal_scan_nonzero_candidate_rows", ""),
                "signal_scan_latest": status_row.get("signal_scan_latest", ""),
                "signal_scan_latest_detail": status_row.get("signal_scan_latest_detail", ""),
                "order_decision_rows": status_row.get("order_decision_rows", ""),
                "trade_rows": status_row.get("trade_rows", ""),
                "paper_filled_rows": status_row.get("paper_filled_rows", ""),
                "paper_filled_rows_since": status_row.get("paper_filled_rows_since", ""),
                "official_filled_all": all_row.get("official_filled_rows", ""),
                "official_pnl_all": all_row.get("official_pnl", ""),
                "official_win_rate_all": all_row.get("official_win_rate", ""),
                "proxy_pnl_all": all_row.get("proxy_pnl", ""),
                "proxy_official_mismatches_all": all_row.get("official_proxy_result_mismatches", ""),
                "official_filled_since": since_row.get("official_filled_rows", ""),
                "official_pnl_since": since_row.get("official_pnl", ""),
                "proxy_official_mismatches_since": since_row.get("official_proxy_result_mismatches", ""),
                "mean_basis_all": all_row.get("mean_official_minus_proxy_spot", ""),
                "max_abs_basis_all": all_row.get("max_abs_official_minus_proxy_spot", ""),
                "capture_error": status_row.get("capture_error", ""),
                "capture_snapshot_error": status_row.get("capture_snapshot_error", ""),
                "trade_error": status_row.get("trade_error", ""),
            }
        )
    return rows


def basis_watchlist(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    out["abs_basis"] = out.get("official_minus_proxy_spot", pd.Series(dtype=float)).map(abs)
    mismatch = out.get("official_proxy_result_mismatch", pd.Series(dtype=str)).map(as_bool)
    big_basis = out["abs_basis"].fillna(0.0) >= 50.0
    return out.loc[mismatch | big_basis].sort_values("abs_basis", ascending=False)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    status = read_csv(args.status_dir / "shadow_status.csv")
    official_summary = read_csv(args.official_dir / "shadow_official_summary.csv")
    official_trades = read_csv(args.official_dir / "shadow_official_trades.csv")
    readiness = read_csv(args.readiness_dir / "readiness_summary.csv")
    status_info = read_json(args.status_dir / "run_info.json")
    official_info = read_json(args.official_dir / "run_info.json")
    readiness_info = read_json(args.readiness_dir / "run_info.json")

    shadow_rows = build_shadow_summary(status, official_summary)
    focused = focused_readiness(readiness)
    watchlist = basis_watchlist(official_trades)
    production_ready_count = sum(1 for _, row in readiness.iterrows() if as_bool(row.get("production_ready")))
    running_targets = sum(1 for _, row in status.iterrows() if as_bool(row.get("running")))

    write_csv(args.out_dir / "forward_shadow_summary.csv", shadow_rows)
    focused.to_csv(args.out_dir / "focused_readiness.csv", index=False)
    watchlist.to_csv(args.out_dir / "basis_watchlist.csv", index=False)

    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status_dir": str(args.status_dir),
        "official_dir": str(args.official_dir),
        "readiness_dir": str(args.readiness_dir),
        "running_targets": running_targets,
        "status_targets": int(len(status)),
        "production_ready_count": int(production_ready_count),
        "official_trades": int(len(official_trades)),
        "basis_watchlist_rows": int(len(watchlist)),
        "status_created_at_utc": status_info.get("created_at_utc", ""),
        "duplicate_target_process_count": to_int(status_info.get("duplicate_target_process_count", 0)),
        "unmanaged_matching_process_count": to_int(status_info.get("unmanaged_matching_process_count", 0)),
        "official_created_at_utc": official_info.get("created_at_utc", ""),
        "readiness_created_at_utc": readiness_info.get("created_at_utc", ""),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    verdict = (
        "NO DEPLOY: readiness production_ready_count is 0."
        if production_ready_count == 0
        else f"REVIEW REQUIRED: {production_ready_count} readiness rows are marked production_ready."
    )
    shadow_df = pd.DataFrame(shadow_rows)
    btc15m_shadow = shadow_df[
        shadow_df.get("name", pd.Series(dtype=str)).astype(str).isin(
            ["btc15m_q250_qty500_firstskip_shadow", "btc15m_q1000_yes_shadow"]
        )
    ].copy()
    btc15m_since_fills = (
        pd.to_numeric(btc15m_shadow.get("paper_filled_rows_since", pd.Series(dtype=float)), errors="coerce")
        .fillna(0)
        .sum()
    )
    btc15m_since_official = (
        pd.to_numeric(btc15m_shadow.get("official_filled_since", pd.Series(dtype=float)), errors="coerce")
        .fillna(0)
        .sum()
    )
    btc15m_since_pnl = (
        pd.to_numeric(btc15m_shadow.get("official_pnl_since", pd.Series(dtype=float)), errors="coerce")
        .fillna(0.0)
        .sum()
    )
    if btc15m_since_fills > 0:
        btc15m_interpretation = (
            f"- BTC15M q250/q1000 have `{int(btc15m_since_fills)}` paper fill(s) since the freeze, "
            f"`{int(btc15m_since_official)}` official-settled, official PnL `{btc15m_since_pnl:.2f}`; "
            "this is diagnostic only because active source/schema freshness and post-restart collection gates have not passed."
        )
    else:
        btc15m_interpretation = (
            "- BTC15M q250/q1000 paper shadows need actual post-freeze fills before they can move toward promotion."
        )
    report = [
        "# BTC Forward Evidence Report",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        "",
        f"## Verdict",
        "",
        verdict,
        "",
        "This report is read-only and only combines existing status, official-settlement, and readiness artifacts.",
        "",
        "## Artifact Inputs",
        "",
        f"- Status: `{args.status_dir}`",
        f"- Official settlement: `{args.official_dir}`",
        f"- Readiness: `{args.readiness_dir}`",
        f"- Duplicate target processes: `{run_info['duplicate_target_process_count']}`",
        f"- Unmanaged matching BTC processes: `{run_info['unmanaged_matching_process_count']}`",
        "",
        "## Forward Shadow Summary",
        "",
        markdown_table(
            pd.DataFrame(shadow_rows),
            [
                "name",
                "running",
                "pids",
                "process_count",
                "duplicate_process_count",
                "process_hygiene_status",
                "source_freshness_status",
                "paper_filled_rows",
                "paper_filled_rows_since",
                "official_filled_all",
                "official_pnl_all",
                "official_filled_since",
                "official_pnl_since",
                "proxy_official_mismatches_all",
                "max_abs_basis_all",
            ],
        ),
        "",
        "## Signal Diagnostics",
        "",
        markdown_table(
            pd.DataFrame(shadow_rows),
            [
                "name",
                "source_latest_path",
                "source_latest_mtime_utc",
                "process_predates_latest_source",
                "capture_read_source",
                "capture_db_mtime_utc",
                "capture_health_latest",
                "ws_orderbook_top_latest",
                "signal_scan_rows",
                "signal_scan_nonzero_candidate_rows",
                "signal_scan_latest",
                "signal_scan_latest_detail",
                "order_decision_rows",
                "capture_error",
                "capture_snapshot_error",
            ],
        ),
        "",
        "## Focused Readiness Blockers",
        "",
        markdown_table(
            focused,
            [
                "family",
                "candidate",
                "source",
                "production_ready",
                "failure_reasons",
                "pred_trades",
                "pred_pnl",
                "live_official_trades",
                "live_official_pnl",
            ],
        ),
        "",
        "## Basis Watchlist",
        "",
        markdown_table(
            watchlist,
            [
                "ledger",
                "created_at",
                "market_ticker",
                "side",
                "top_visible_qty",
                "quote_age_ms",
                "official_result",
                "proxy_result",
                "official_proxy_result_mismatch",
                "expiration_value",
                "proxy_close_spot",
                "official_minus_proxy_spot",
                "official_pnl",
                "proxy_pnl",
            ],
        ),
        "",
        "## Interpretation",
        "",
        btc15m_interpretation,
        "- BTC1H shadow evidence is still too small and the old proxy-summary mismatch remains a blocker until a restarted official-settlement shadow produces clean forward rows.",
        "- Do not promote proxy-only PnL, metadata-only settlement, or a candidate selected after seeing live replay.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
