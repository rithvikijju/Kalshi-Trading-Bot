#!/usr/bin/env python3
"""Diagnose BTC15M paper-shadow signal health.

This is a forward-validation health audit. It does not search for a strategy.
It answers whether zero post-freeze fills look like genuine no-signal behavior
or whether capture freshness, stale BTC spot, or live DB read failures make the
forward evidence weak.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_shadow_signal_health_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_SINCE = "2026-05-18T04:17:44Z"

TARGETS = [
    {
        "name": "btc15m_q250_qty500_firstskip_shadow",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb",
    },
    {
        "name": "btc15m_q1000_yes_shadow",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_capture.duckdb",
    },
    {
        "name": "btc15m_q250_qty500_firstskip_yes_shadow",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb",
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit BTC15M q250/q1000 shadow signal health.")
    p.add_argument("--since-utc", default=DEFAULT_SINCE)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--retry-count", type=int, default=3)
    p.add_argument("--retry-sleep", type=float, default=1.0)
    return p.parse_args()


def open_duckdb(path: Path, retries: int, sleep_s: float) -> duckdb.DuckDBPyConnection:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return duckdb.connect(str(path), read_only=True)
        except Exception as exc:  # pragma: no cover - live lock timing only
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def open_duckdb_with_snapshot_fallback(
    path: Path,
    retries: int,
    sleep_s: float,
) -> tuple[duckdb.DuckDBPyConnection, str, str, str, tempfile.TemporaryDirectory[str] | None]:
    """Open a live DuckDB read-only, copying it first if Windows locks block reads."""
    try:
        return open_duckdb(path, retries, sleep_s), "live_readonly", "", "", None
    except Exception as exc:
        live_error = repr(exc)
    tmp_ctx = tempfile.TemporaryDirectory(prefix="btc15m_signal_health_duckdb_")
    snapshot = Path(tmp_ctx.name) / path.name
    try:
        shutil.copy2(path, snapshot)
        con = open_duckdb(snapshot, 1, 0.0)
        return con, "snapshot_copy_after_live_lock", live_error, "", tmp_ctx
    except Exception as snap_exc:
        tmp_ctx.cleanup()
        raise RuntimeError(
            f"live_read_failed: {live_error}; snapshot_copy_failed: {snap_exc!r}"
        ) from snap_exc


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()[0]
    )


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()]


def detail_family(detail: object) -> str:
    text = str(detail or "").strip()
    if not text:
        return "blank"
    families = [
        "h02_ttl_outside",
        "h02_no_edge",
        "h02_spread",
        "h02_first_signal_skip",
        "h02_stale_btc_spot",
        "stale_btc_spot",
        "h02_missing_btc_spot",
        "h02_missing_strike",
        "h02_incomplete_book",
        "event_already_traded",
        "portfolio_event_lock",
        "rolling_risk_cap",
    ]
    for family in families:
        if text.startswith(family):
            return family
    match = re.match(r"([A-Za-z0-9_]+)", text)
    return match.group(1) if match else text[:80]


def safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def pct(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0


def summarize_target(name: str, path: Path, since_utc: str, retries: int, sleep_s: float) -> tuple[dict[str, Any], pd.DataFrame]:
    base: dict[str, Any] = {
        "name": name,
        "capture_db": str(path),
        "capture_db_exists": path.exists(),
        "capture_read_source": "",
        "readable": False,
        "read_error": "",
        "capture_snapshot_error": "",
        "since_utc": since_utc,
    }
    if not path.exists():
        base["read_error"] = "missing_capture_db"
        return base, pd.DataFrame()
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    try:
        con, read_source, live_error, snapshot_error, tmp_ctx = open_duckdb_with_snapshot_fallback(
            path,
            retries,
            sleep_s,
        )
    except Exception as exc:
        base["read_error"] = repr(exc)
        return base, pd.DataFrame()
    try:
        base["readable"] = True
        base["capture_read_source"] = read_source
        if live_error:
            base["read_error"] = f"live_read_failed_then_snapshot_succeeded: {live_error}"
        if snapshot_error:
            base["capture_snapshot_error"] = snapshot_error
        since = pd.Timestamp(since_utc, tz="UTC").to_pydatetime()

        if table_exists(con, "signal_scan"):
            signal_cols = table_columns(con, "signal_scan")
            time_expr = "TRY_CAST(received_at_utc AS TIMESTAMPTZ)"
            total_signal = int(con.execute("SELECT COUNT(*) FROM signal_scan").fetchone()[0])
            since_signal = con.execute(
                f"""
                SELECT *
                FROM signal_scan
                WHERE {time_expr} >= ?
                ORDER BY received_at_ns
                """,
                [since],
            ).fetchdf()
            latest = con.execute(
                """
                SELECT received_at_utc, action, detail, candidate_count, event_ticker, btc_spot
                FROM signal_scan
                ORDER BY received_at_ns DESC
                LIMIT 1
                """
            ).fetchone()
            base["signal_scan_rows_total"] = total_signal
            base["signal_scan_rows_since"] = int(len(since_signal))
            if latest:
                base["signal_latest_utc"] = latest[0]
                base["signal_latest_action"] = latest[1]
                base["signal_latest_detail"] = latest[2]
                base["signal_latest_candidate_count"] = latest[3]
                base["signal_latest_event"] = latest[4]
                base["signal_latest_btc_spot"] = latest[5]
            if not since_signal.empty:
                since_signal["detail_family"] = since_signal["detail"].map(detail_family)
                base["signal_nonzero_candidate_rows_since"] = int(
                    pd.to_numeric(since_signal.get("candidate_count"), errors="coerce").fillna(0).gt(0).sum()
                )
                base["signal_selected_rows_since"] = int(since_signal.get("action", pd.Series(dtype=str)).astype(str).eq("selected").sum())
                base["signal_top_detail_family"] = str(since_signal["detail_family"].value_counts().index[0])
                base["signal_top_detail_family_rows"] = int(since_signal["detail_family"].value_counts().iloc[0])
            else:
                base["signal_nonzero_candidate_rows_since"] = 0
                base["signal_selected_rows_since"] = 0
            detail_counts = (
                since_signal.assign(detail_family=since_signal["detail"].map(detail_family))
                .groupby(["detail_family", "action"], dropna=False)
                .size()
                .reset_index(name="rows")
                if not since_signal.empty and "detail" in signal_cols
                else pd.DataFrame(columns=["detail_family", "action", "rows"])
            )
            detail_counts["name"] = name
            detail_counts["share_of_signal_rows_since"] = [
                pct(int(x), int(len(since_signal))) for x in detail_counts["rows"]
            ]
        else:
            detail_counts = pd.DataFrame(columns=["name", "detail_family", "action", "rows", "share_of_signal_rows_since"])

        if table_exists(con, "capture_health"):
            health = con.execute(
                """
                SELECT received_at_utc, btc_spot, btc_spot_age_sec, coinbase_connected,
                       kalshi_connected, market_ok, ready_books, total_books, detail
                FROM capture_health
                WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
                ORDER BY received_at_ns
                """,
                [since],
            ).fetchdf()
            base["capture_health_rows_since"] = int(len(health))
            if not health.empty:
                latest_health = health.iloc[-1]
                base["capture_health_latest"] = latest_health.get("received_at_utc", "")
                base["capture_health_latest_btc_spot_age_sec"] = latest_health.get("btc_spot_age_sec", "")
                base["capture_health_latest_coinbase_connected"] = latest_health.get("coinbase_connected", "")
                ages = pd.to_numeric(health.get("btc_spot_age_sec"), errors="coerce").dropna()
                if not ages.empty:
                    base["btc_spot_age_p50_sec_since"] = round(float(ages.quantile(0.50)), 4)
                    base["btc_spot_age_p95_sec_since"] = round(float(ages.quantile(0.95)), 4)
                    base["btc_spot_age_max_sec_since"] = round(float(ages.max()), 4)
                    base["btc_spot_age_gt10_rows_since"] = int(ages.gt(10.0).sum())
                    base["btc_spot_age_gt10_share_since"] = pct(int(ages.gt(10.0).sum()), int(len(ages)))

        if table_exists(con, "coinbase_ticker"):
            ticker = con.execute(
                """
                SELECT COUNT(*) AS rows_since, MAX(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS latest_utc
                FROM coinbase_ticker
                WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
                """,
                [since],
            ).fetchone()
            base["coinbase_ticker_rows_since"] = int(ticker[0] or 0)
            base["coinbase_ticker_latest"] = ticker[1]

        if table_exists(con, "ws_orderbook_top"):
            top = con.execute(
                """
                SELECT COUNT(*) AS rows_since, MAX(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS latest_utc
                FROM ws_orderbook_top
                WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
                """,
                [since],
            ).fetchone()
            base["ws_orderbook_top_rows_since"] = int(top[0] or 0)
            base["ws_orderbook_top_latest"] = top[1]

        if table_exists(con, "order_decision"):
            decisions = con.execute(
                """
                SELECT COUNT(*) AS rows_since
                FROM order_decision
                WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
                """,
                [since],
            ).fetchone()
            base["order_decision_rows_since"] = int(decisions[0] or 0)

        return base, detail_counts
    finally:
        con.close()
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    detail_frames: list[pd.DataFrame] = []
    for target in TARGETS:
        row, details = summarize_target(
            str(target["name"]),
            Path(target["capture_db"]),
            args.since_utc,
            args.retry_count,
            args.retry_sleep,
        )
        summary_rows.append(row)
        if not details.empty:
            detail_frames.append(details)
    summary = pd.DataFrame(summary_rows)
    details = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame(
        columns=["name", "detail_family", "action", "rows", "share_of_signal_rows_since"]
    )
    summary.to_csv(args.out_dir / "shadow_signal_health_summary.csv", index=False)
    details.sort_values(["name", "rows"], ascending=[True, False]).to_csv(
        args.out_dir / "shadow_signal_detail_counts.csv",
        index=False,
    )
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "since_utc": args.since_utc,
        "targets": len(TARGETS),
        "readable_targets": int(summary.get("readable", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
        "note": "Health diagnostic only. Zero fills are not promotion evidence; this checks whether no-fill evidence is technically interpretable.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = [
        "# BTC15M Shadow Signal Health",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"Since UTC: `{args.since_utc}`",
        "",
        "## Summary",
        "",
        summary.fillna("").to_string(index=False) if not summary.empty else "_No rows._",
        "",
        "## Detail Families",
        "",
        details.sort_values(["name", "rows"], ascending=[True, False]).head(40).fillna("").to_string(index=False)
        if not details.empty
        else "_No detail rows._",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(run_info, indent=2, sort_keys=True, default=str),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
