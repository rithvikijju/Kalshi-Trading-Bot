#!/usr/bin/env python3
"""Build a BTC15M frozen-shadow signal starvation report.

This is a forward-validation control artifact. It does not search thresholds or
recommend a new strategy. It answers whether the currently frozen q250/q1000
BTC15M shadows are producing enough post-freeze candidates to collect future
official-settled evidence.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_signal_starvation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_SINCE = "2026-05-18T04:17:44Z"

TTL_WINDOW_MIN = 10.0
TTL_WINDOW_MAX = 12.0
STARVATION_MIN_SIGNAL_ROWS = 50_000

TARGETS = [
    {
        "name": "btc15m_q250_qty500_firstskip_shadow",
        "candidate": "q250_firstskip_qty500",
        "promotion_min_official_rows": 100,
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb",
    },
    {
        "name": "btc15m_q1000_yes_shadow",
        "candidate": "q1000_yes",
        "promotion_min_official_rows": 100,
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_capture.duckdb",
    },
    {
        "name": "btc15m_q250_qty500_firstskip_yes_shadow",
        "candidate": "q250_firstskip_qty500_yes",
        "promotion_min_official_rows": 100,
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb",
    },
]

TTL_RE = re.compile(r"h02_ttl_outside_(-?\d+(?:\.\d+)?)")
PYES_RE = re.compile(r"\bp_yes=(-?\d+(?:\.\d+)?)")
SPREAD_RE = re.compile(r"h02_spread_(-?\d+(?:\.\d+)?)c")
FIRST_SIGNAL_QTY_RE = re.compile(r"h02_first_signal_skip qty=(-?\d+(?:\.\d+)?)")
MIN_QTY_RE = re.compile(r"\bmin_qty=(-?\d+(?:\.\d+)?)")
ENTRY_REJECTS_RE = re.compile(r"\bentry_rejects=(\d+)")
QTY_REJECTS_RE = re.compile(r"\bqty_rejects=(\d+)")
EDGE_REJECTS_RE = re.compile(r"\bedge_rejects=(\d+)")
CHECKED_SIDES_RE = re.compile(r"\bchecked_sides=(\d+)")
YES_ENTRY_RE = re.compile(r"\byes_entry=(-?\d+(?:\.\d+)?)")
YES_QTY_RE = re.compile(r"\byes_qty=(-?\d+(?:\.\d+)?)")
NO_ENTRY_RE = re.compile(r"\bno_entry=(-?\d+(?:\.\d+)?)")
NO_QTY_RE = re.compile(r"\bno_qty=(-?\d+(?:\.\d+)?)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC15M frozen-shadow signal starvation report.")
    p.add_argument("--since-utc", default=DEFAULT_SINCE)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--retry-count", type=int, default=3)
    p.add_argument("--retry-sleep", type=float, default=1.0)
    p.add_argument("--min-signal-rows", type=int, default=STARVATION_MIN_SIGNAL_ROWS)
    return p.parse_args()


def open_duckdb(path: Path, retries: int, sleep_s: float) -> duckdb.DuckDBPyConnection:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return duckdb.connect(str(path), read_only=True)
        except Exception as exc:  # pragma: no cover - depends on live DB lock timing
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def open_duckdb_with_snapshot_fallback(
    path: Path,
    retries: int,
    sleep_s: float,
) -> tuple[duckdb.DuckDBPyConnection, str, str, tempfile.TemporaryDirectory[str] | None]:
    try:
        return open_duckdb(path, retries, sleep_s), "live_readonly", "", None
    except Exception as exc:
        live_error = repr(exc)
    tmp_ctx = tempfile.TemporaryDirectory(prefix="btc15m_signal_starvation_duckdb_")
    snapshot = Path(tmp_ctx.name) / path.name
    try:
        shutil.copy2(path, snapshot)
        return (
            open_duckdb(snapshot, 1, 0.0),
            "snapshot_copy_after_live_lock",
            live_error,
            tmp_ctx,
        )
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


def detail_family(detail: object) -> str:
    text = str(detail or "").strip()
    if not text:
        return "blank"
    families = [
        "h02_ttl_outside",
        "h02_no_edge",
        "h02_spread",
        "h02_entry_qty",
        "h02_first_signal_skip",
        "h02_stale_btc_spot",
        "stale_btc_spot",
        "h02_missing_btc_spot",
        "h02_missing_strike",
        "h02_incomplete_book",
        "kalshi_ws_disconnected",
        "no_current_event",
        "waiting_for_books",
        "event_already_traded",
        "portfolio_event_lock",
        "rolling_risk_cap",
    ]
    for family in families:
        if text.startswith(family):
            return family
    match = re.match(r"([A-Za-z0-9_]+)", text)
    return match.group(1) if match else text[:80]


def pct(num: float, den: float) -> float:
    return round(float(num) / float(den), 6) if den else 0.0


def finite(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def series_stat(series: pd.Series, fn: str) -> float | str:
    nums = pd.to_numeric(series, errors="coerce").dropna()
    if nums.empty:
        return ""
    if fn == "min":
        return round(float(nums.min()), 4)
    if fn == "max":
        return round(float(nums.max()), 4)
    if fn == "p50":
        return round(float(nums.quantile(0.50)), 4)
    if fn == "p95":
        return round(float(nums.quantile(0.95)), 4)
    raise ValueError(fn)


def parse_numeric_from_detail(details: pd.Series, regex: re.Pattern[str]) -> pd.Series:
    values = details.astype(str).str.extract(regex, expand=False)
    return pd.to_numeric(values, errors="coerce")


def sum_numeric_from_detail(details: pd.Series, regex: re.Pattern[str]) -> int:
    values = parse_numeric_from_detail(details, regex)
    if values.dropna().empty:
        return 0
    return int(values.fillna(0).sum())


def collection_status(rows: int, nonzero: int, selected: int, decisions: int, min_signal_rows: int) -> tuple[str, str]:
    if rows < min_signal_rows:
        return (
            "INSUFFICIENT_SIGNAL_SCAN_ROWS",
            "Too few post-freeze signal scans to judge collection feasibility.",
        )
    if nonzero == 0 and selected == 0 and decisions == 0:
        return (
            "STARVED_NO_POSTFREEZE_CANDIDATES",
            "Frozen policy has produced zero post-freeze candidates/decisions; this cannot accumulate promotion rows at the observed rate.",
        )
    candidate_rate = nonzero / max(rows, 1)
    if nonzero < 5 or candidate_rate < 0.0005:
        return (
            "SPARSE_CANDIDATES",
            "Frozen policy produced some candidates, but the observed rate is too sparse for near-term promotion evidence.",
        )
    return (
        "COLLECTING_CANDIDATES",
        "Frozen policy is producing candidates; promotion still requires official settlement and execution-realism gates.",
    )


def summarize_target(
    target: dict[str, Any],
    since_utc: str,
    min_signal_rows: int,
    retries: int,
    sleep_s: float,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any], dict[str, Any]]:
    path = Path(target["capture_db"])
    row: dict[str, Any] = {
        "name": target["name"],
        "candidate": target["candidate"],
        "capture_db": str(path),
        "capture_db_exists": path.exists(),
        "capture_read_source": "",
        "readable": False,
        "read_error": "",
        "since_utc": since_utc,
        "promotion_min_official_rows": target["promotion_min_official_rows"],
        "audit_min_signal_rows": min_signal_rows,
    }
    empty_detail = pd.DataFrame(columns=["name", "candidate", "detail_family", "action", "rows", "share_of_signal_rows_since"])
    empty_ttl: dict[str, Any] = {}
    empty_no_edge: dict[str, Any] = {}
    if not path.exists():
        row["read_error"] = "missing_capture_db"
        row["signal_rows_since"] = 0
        row["nonzero_candidate_rows_since"] = 0
        row["selected_rows_since"] = 0
        row["order_decision_rows_since"] = 0
        row["collection_status"] = "NOT_STARTED_MISSING_CAPTURE_DB"
        row["collection_status_reason"] = "Capture DB does not exist; this target has not started collecting paper-shadow evidence."
        row["promotion_implication"] = "DO_NOT_PROMOTE_NOT_RUNNING"
        row["research_implication"] = "Requires explicit user-authorized paper-only start before future rows can be counted."
        return row, empty_detail, empty_ttl, empty_no_edge

    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    try:
        con, read_source, live_error, tmp_ctx = open_duckdb_with_snapshot_fallback(path, retries, sleep_s)
    except Exception as exc:
        row["read_error"] = repr(exc)
        row["signal_rows_since"] = 0
        row["nonzero_candidate_rows_since"] = 0
        row["selected_rows_since"] = 0
        row["order_decision_rows_since"] = 0
        row["collection_status"] = "UNREADABLE_CAPTURE_DB"
        row["collection_status_reason"] = "Capture DB could not be opened for a read-only health check."
        row["promotion_implication"] = "DO_NOT_PROMOTE_UNREADABLE_CAPTURE"
        row["research_implication"] = "Resolve DB readability before interpreting forward collection evidence."
        return row, empty_detail, empty_ttl, empty_no_edge

    try:
        row["readable"] = True
        row["capture_read_source"] = read_source
        if live_error:
            row["read_error"] = f"live_read_failed_then_snapshot_succeeded: {live_error}"
        since = pd.Timestamp(since_utc, tz="UTC").to_pydatetime()
        if not table_exists(con, "signal_scan"):
            row["read_error"] = "missing_signal_scan_table"
            return row, empty_detail, empty_ttl, empty_no_edge

        signal = con.execute(
            """
            SELECT received_at_ns, received_at_utc, action, detail, candidate_count,
                   event_ticker, selected_market, selected_side, entry_price,
                   net_edge_cents, model_p_yes, btc_spot
            FROM signal_scan
            WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
            ORDER BY received_at_ns
            """,
            [since],
        ).fetchdf()
        rows = int(len(signal))
        row["signal_rows_since"] = rows
        if rows == 0:
            row["nonzero_candidate_rows_since"] = 0
            row["selected_rows_since"] = 0
            row["order_decision_rows_since"] = 0
            row["collection_status"], row["collection_status_reason"] = collection_status(0, 0, 0, 0, min_signal_rows)
            return row, empty_detail, empty_ttl, empty_no_edge

        signal["detail_family"] = signal["detail"].map(detail_family)
        signal["candidate_count_num"] = pd.to_numeric(signal["candidate_count"], errors="coerce").fillna(0)
        signal["ts"] = pd.to_datetime(signal["received_at_utc"], utc=True, errors="coerce")

        nonzero = int(signal["candidate_count_num"].gt(0).sum())
        selected = int(signal["action"].astype(str).eq("selected").sum())
        distinct_events = int(signal["event_ticker"].dropna().astype(str).nunique())
        row["distinct_events_since"] = distinct_events
        row["nonzero_candidate_rows_since"] = nonzero
        row["selected_rows_since"] = selected
        row["candidate_row_rate"] = pct(nonzero, rows)
        row["selected_row_rate"] = pct(selected, rows)
        row["first_signal_utc"] = signal["ts"].dropna().min()
        row["latest_signal_utc"] = signal["ts"].dropna().max()
        if pd.notna(row["first_signal_utc"]) and pd.notna(row["latest_signal_utc"]):
            duration_hours = (row["latest_signal_utc"] - row["first_signal_utc"]).total_seconds() / 3600.0
            row["signal_duration_hours"] = round(float(duration_hours), 4)
            row["signal_rows_per_hour"] = round(rows / max(duration_hours, 1e-9), 2)
        latest = signal.iloc[-1]
        row["latest_action"] = latest.get("action", "")
        row["latest_detail"] = latest.get("detail", "")
        row["latest_event"] = latest.get("event_ticker", "")
        row["latest_candidate_count"] = latest.get("candidate_count", "")
        row["latest_btc_spot"] = latest.get("btc_spot", "")

        details = (
            signal.groupby(["detail_family", "action"], dropna=False)
            .size()
            .reset_index(name="rows")
            .sort_values("rows", ascending=False)
        )
        details["name"] = target["name"]
        details["candidate"] = target["candidate"]
        details["share_of_signal_rows_since"] = [pct(int(x), rows) for x in details["rows"]]
        top = details.iloc[0]
        row["top_detail_family"] = top["detail_family"]
        row["top_detail_family_rows"] = int(top["rows"])
        row["top_detail_family_share"] = pct(int(top["rows"]), rows)
        stale_rows = int(details.loc[details["detail_family"].isin(["stale_btc_spot", "h02_stale_btc_spot"]), "rows"].sum())
        row["stale_btc_signal_rows_since"] = stale_rows
        row["stale_btc_signal_share_since"] = pct(stale_rows, rows)

        ttl_values = parse_numeric_from_detail(signal["detail"], TTL_RE).dropna()
        ttl_info: dict[str, Any] = {
            "name": target["name"],
            "candidate": target["candidate"],
            "ttl_window_min": TTL_WINDOW_MIN,
            "ttl_window_max": TTL_WINDOW_MAX,
            "ttl_outside_rows": int(len(ttl_values)),
            "ttl_outside_share": pct(len(ttl_values), rows),
            "ttl_below_window_rows": int(ttl_values.lt(TTL_WINDOW_MIN).sum()),
            "ttl_above_window_rows": int(ttl_values.gt(TTL_WINDOW_MAX).sum()),
            "ttl_min": series_stat(ttl_values, "min"),
            "ttl_p50": series_stat(ttl_values, "p50"),
            "ttl_p95": series_stat(ttl_values, "p95"),
            "ttl_max": series_stat(ttl_values, "max"),
        }
        ttl_info["ttl_below_window_share_of_ttl"] = pct(ttl_info["ttl_below_window_rows"], ttl_info["ttl_outside_rows"])
        ttl_info["ttl_above_window_share_of_ttl"] = pct(ttl_info["ttl_above_window_rows"], ttl_info["ttl_outside_rows"])
        row.update({f"ttl_{k}": v for k, v in ttl_info.items() if k not in {"name", "candidate"}})

        no_edge_mask = signal["detail_family"].eq("h02_no_edge")
        no_edge_details = signal.loc[no_edge_mask, "detail"]
        p_yes = parse_numeric_from_detail(no_edge_details, PYES_RE).dropna()
        min_qty = parse_numeric_from_detail(no_edge_details, MIN_QTY_RE)
        yes_entry = parse_numeric_from_detail(no_edge_details, YES_ENTRY_RE)
        yes_qty = parse_numeric_from_detail(no_edge_details, YES_QTY_RE)
        no_entry = parse_numeric_from_detail(no_edge_details, NO_ENTRY_RE)
        no_qty = parse_numeric_from_detail(no_edge_details, NO_QTY_RE)
        entry_rejects = sum_numeric_from_detail(no_edge_details, ENTRY_REJECTS_RE)
        qty_rejects = sum_numeric_from_detail(no_edge_details, QTY_REJECTS_RE)
        edge_rejects = sum_numeric_from_detail(no_edge_details, EDGE_REJECTS_RE)
        checked_sides = sum_numeric_from_detail(no_edge_details, CHECKED_SIDES_RE)
        yes_qty_below_min = int((yes_qty < min_qty).fillna(False).sum()) if len(yes_qty) else 0
        no_qty_below_min = int((no_qty < min_qty).fillna(False).sum()) if len(no_qty) else 0
        no_edge_info: dict[str, Any] = {
            "name": target["name"],
            "candidate": target["candidate"],
            "no_edge_rows": int(no_edge_mask.sum()),
            "no_edge_share": pct(int(no_edge_mask.sum()), rows),
            "no_edge_p_yes_min": series_stat(p_yes, "min"),
            "no_edge_p_yes_p50": series_stat(p_yes, "p50"),
            "no_edge_p_yes_p95": series_stat(p_yes, "p95"),
            "no_edge_p_yes_max": series_stat(p_yes, "max"),
            "no_edge_p_yes_ge_055_rows": int(p_yes.ge(0.55).sum()),
            "no_edge_p_yes_ge_060_rows": int(p_yes.ge(0.60).sum()),
            "no_edge_checked_sides": checked_sides,
            "no_edge_entry_reject_units": entry_rejects,
            "no_edge_qty_reject_units": qty_rejects,
            "no_edge_edge_reject_units": edge_rejects,
            "no_edge_entry_reject_share_of_checked_sides": pct(entry_rejects, checked_sides),
            "no_edge_qty_reject_share_of_checked_sides": pct(qty_rejects, checked_sides),
            "no_edge_edge_reject_share_of_checked_sides": pct(edge_rejects, checked_sides),
            "no_edge_yes_entry_p50": series_stat(yes_entry, "p50"),
            "no_edge_yes_entry_p95": series_stat(yes_entry, "p95"),
            "no_edge_yes_qty_p50": series_stat(yes_qty, "p50"),
            "no_edge_yes_qty_p95": series_stat(yes_qty, "p95"),
            "no_edge_yes_qty_below_min_rows": yes_qty_below_min,
            "no_edge_no_entry_p50": series_stat(no_entry, "p50"),
            "no_edge_no_entry_p95": series_stat(no_entry, "p95"),
            "no_edge_no_qty_p50": series_stat(no_qty, "p50"),
            "no_edge_no_qty_p95": series_stat(no_qty, "p95"),
            "no_edge_no_qty_below_min_rows": no_qty_below_min,
        }
        no_edge_info["no_edge_p_yes_ge_055_share"] = pct(no_edge_info["no_edge_p_yes_ge_055_rows"], len(p_yes))
        no_edge_info["no_edge_p_yes_ge_060_share"] = pct(no_edge_info["no_edge_p_yes_ge_060_rows"], len(p_yes))
        row.update({f"{k}": v for k, v in no_edge_info.items() if k not in {"name", "candidate"}})

        first_signal_qty = parse_numeric_from_detail(signal["detail"], FIRST_SIGNAL_QTY_RE).dropna()
        row["first_signal_skip_rows"] = int(len(first_signal_qty))
        row["first_signal_skip_share"] = pct(len(first_signal_qty), rows)
        row["first_signal_skip_qty_min"] = series_stat(first_signal_qty, "min")
        row["first_signal_skip_qty_p50"] = series_stat(first_signal_qty, "p50")
        row["first_signal_skip_qty_p95"] = series_stat(first_signal_qty, "p95")
        row["first_signal_skip_qty_max"] = series_stat(first_signal_qty, "max")

        spread_values = parse_numeric_from_detail(signal["detail"], SPREAD_RE).dropna()
        row["spread_reject_rows"] = int(len(spread_values))
        row["spread_reject_share"] = pct(len(spread_values), rows)
        row["spread_reject_p50_cents"] = series_stat(spread_values, "p50")
        row["spread_reject_max_cents"] = series_stat(spread_values, "max")

        decisions = 0
        if table_exists(con, "order_decision"):
            decisions = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM order_decision
                    WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
                    """,
                    [since],
                ).fetchone()[0]
                or 0
            )
        row["order_decision_rows_since"] = decisions
        row["collection_status"], row["collection_status_reason"] = collection_status(rows, nonzero, selected, decisions, min_signal_rows)
        row["promotion_implication"] = (
            "DO_NOT_PROMOTE_NO_POSTFREEZE_CANDIDATES"
            if row["collection_status"] == "STARVED_NO_POSTFREEZE_CANDIDATES"
            else "DO_NOT_PROMOTE_COLLECTION_GATE_STILL_REQUIRED"
        )
        row["research_implication"] = (
            "If this persists after an authorized clean restart, freeze a new preregistered candidate before looking at future holdout rows."
            if row["collection_status"] == "STARVED_NO_POSTFREEZE_CANDIDATES"
            else "Continue collecting only if official settlement and execution-realism fields remain clean."
        )
        return row, details, ttl_info, no_edge_info
    finally:
        con.close()
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    detail_frames: list[pd.DataFrame] = []
    ttl_rows: list[dict[str, Any]] = []
    no_edge_rows: list[dict[str, Any]] = []
    for target in TARGETS:
        row, details, ttl_info, no_edge_info = summarize_target(
            target,
            args.since_utc,
            args.min_signal_rows,
            args.retry_count,
            args.retry_sleep,
        )
        rows.append(row)
        if not details.empty:
            detail_frames.append(details)
        if ttl_info:
            ttl_rows.append(ttl_info)
        if no_edge_info:
            no_edge_rows.append(no_edge_info)

    summary = pd.DataFrame(rows)
    details = (
        pd.concat(detail_frames, ignore_index=True)
        if detail_frames
        else pd.DataFrame(columns=["name", "candidate", "detail_family", "action", "rows", "share_of_signal_rows_since"])
    )
    ttl = pd.DataFrame(ttl_rows)
    no_edge = pd.DataFrame(no_edge_rows)

    summary.to_csv(args.out_dir / "signal_starvation_summary.csv", index=False)
    details.sort_values(["name", "rows"], ascending=[True, False]).to_csv(args.out_dir / "detail_family_counts.csv", index=False)
    ttl.to_csv(args.out_dir / "ttl_outside_distribution.csv", index=False)
    no_edge.to_csv(args.out_dir / "no_edge_p_yes_distribution.csv", index=False)

    status_counts = (
        summary["collection_status"].fillna("UNKNOWN").value_counts().to_dict()
        if "collection_status" in summary.columns
        else {}
    )
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "since_utc": args.since_utc,
        "min_signal_rows": args.min_signal_rows,
        "targets": len(TARGETS),
        "readable_targets": int(summary.get("readable", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
        "status_counts": status_counts,
        "note": "Forward collection-feasibility artifact only. Do not use this to retune thresholds on the post-freeze window.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True, default=str), encoding="utf-8")

    compact_cols = [
        "candidate",
        "collection_status",
        "signal_rows_since",
        "distinct_events_since",
        "signal_duration_hours",
        "nonzero_candidate_rows_since",
        "selected_rows_since",
        "order_decision_rows_since",
        "top_detail_family",
        "top_detail_family_share",
        "ttl_ttl_outside_share",
        "ttl_ttl_below_window_rows",
        "ttl_ttl_above_window_rows",
        "no_edge_rows",
        "no_edge_p_yes_p95",
        "no_edge_entry_reject_units",
        "no_edge_qty_reject_units",
        "no_edge_edge_reject_units",
        "first_signal_skip_rows",
        "first_signal_skip_qty_p50",
        "stale_btc_signal_share_since",
        "latest_detail",
        "promotion_implication",
    ]
    compact = summary[[col for col in compact_cols if col in summary.columns]].copy()
    report = [
        "# BTC15M Signal Starvation Report",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"Since UTC: `{args.since_utc}`",
        "",
        "## Verdict",
        "",
        "This is not a strategy-search result. It measures whether the frozen BTC15M q250/q1000 shadows can collect deployable forward evidence under the current policy.",
        "",
        "## Summary",
        "",
        compact.fillna("").to_string(index=False) if not compact.empty else "_No rows._",
        "",
        "## Detail Families",
        "",
        details.sort_values(["name", "rows"], ascending=[True, False]).head(40).fillna("").to_string(index=False)
        if not details.empty
        else "_No detail rows._",
        "",
        "## TTL Outside Distribution",
        "",
        ttl.fillna("").to_string(index=False) if not ttl.empty else "_No TTL rows._",
        "",
        "## No-Edge P(YES) Distribution",
        "",
        no_edge.fillna("").to_string(index=False) if not no_edge.empty else "_No no-edge rows._",
        "",
        "## Interpretation",
        "",
        "- `STARVED_NO_POSTFREEZE_CANDIDATES` means the runner is scanning but the frozen policy is not generating candidates or order decisions.",
        "- Entry/quantity/edge reject units are parsed from enhanced future `h02_no_edge` details; older rows may have blanks because the prior logger did not preserve those sub-reasons.",
        "- `first_signal_skip_rows` isolates cases that passed base filters but failed the stricter first-signal visible-quantity gate.",
        "- This is a collection feasibility blocker, not permission to tune on the post-freeze window.",
        "- If starvation persists after an explicit controlled restart with fresh ledger schema, the honest next step is to preregister a new candidate before evaluating future holdout rows.",
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
