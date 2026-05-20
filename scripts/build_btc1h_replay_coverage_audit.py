#!/usr/bin/env python3
"""Audit whether BTC1H paper rows have readable live-WS replay coverage.

This is a promotion-control diagnostic. It does not replay or search a
strategy. It answers the narrower question needed before BTC1H row
reconciliation can become meaningful: do we have a readable capture DB with
the orderbook and signal-scan rows around each paper fill?
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
HOME_BTC = Path.home() / ".btc_kalshi_bot"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_replay_coverage_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_SHADOW_TRADES = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
DEFAULT_SINCE_UTC = "2026-05-18T04:17:44Z"

NY_TZ = ZoneInfo("America/New_York")
EVENT_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class CaptureSource:
    label: str
    path: Path


def default_capture_sources() -> list[CaptureSource]:
    return [
        CaptureSource(
            "btc1h_entry70_active_shadow_capture",
            HOME_BTC / "btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb",
        ),
        CaptureSource("multi_strategy_shadow_capture", HOME_BTC / "multi_strategy_shadow_capture.duckdb"),
        CaptureSource("research_live_capture", HOME_BTC / "research_live_capture.duckdb"),
        CaptureSource(
            "local_gapless_paused_may12",
            PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless_20260512_paused.duckdb",
        ),
        CaptureSource(
            "local_gapless_may10",
            PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless.duckdb",
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BTC1H paper rows for readable replay-capture coverage.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--shadow-trades", type=Path, default=DEFAULT_SHADOW_TRADES)
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--ledger", default="btc1h_high_conf80_entry70_no_chase_shadow")
    parser.add_argument("--pre-close-min", type=float, default=80.0)
    parser.add_argument("--post-close-min", type=float, default=8.0)
    parser.add_argument(
        "--capture-db",
        action="append",
        default=[],
        help="Optional capture DB override as label=path. Can be passed multiple times.",
    )
    return parser.parse_args()


def parse_capture_overrides(values: list[str]) -> list[CaptureSource]:
    if not values:
        return default_capture_sources()
    out: list[CaptureSource] = []
    for raw in values:
        if "=" in raw:
            label, path = raw.split("=", 1)
        else:
            path = raw
            label = Path(raw).stem
        out.append(CaptureSource(label.strip() or Path(path).stem, Path(path).expanduser()))
    return out


def event_close_from_ticker(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker or "").upper())
    if not match:
        return None
    month = MONTHS.get(match.group("mon"))
    if month is None:
        return None
    local = datetime(
        2000 + int(match.group("yy")),
        month,
        int(match.group("day")),
        int(match.group("hour")),
        tzinfo=NY_TZ,
    )
    return pd.Timestamp(local.astimezone(timezone.utc))


def parse_utc(value: Any) -> pd.Timestamp | None:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def ns(ts: pd.Timestamp) -> int:
    return int(ts.value)


def read_shadow(path: Path, ledger: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows = pd.read_csv(path)
    if rows.empty or "ledger" not in rows.columns:
        return pd.DataFrame()
    return rows[rows["ledger"].astype(str).eq(ledger)].copy()


def table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "select table_name from information_schema.tables where table_schema='main'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def first_existing(names: set[str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in names:
            return name
    return None


def count_event_rows(
    con: duckdb.DuckDBPyConnection,
    table: str | None,
    event_ticker: str,
    start_ns: int,
    end_ns: int,
) -> int:
    if table is None:
        return 0
    cols = {row[1] for row in con.execute(f"pragma table_info('{table}')").fetchall()}
    if "event_ticker" not in cols or "received_at_ns" not in cols:
        return 0
    return int(
        con.execute(
            f"""
            select count(*)
            from {table}
            where event_ticker = ?
              and received_at_ns between ? and ?
            """,
            [event_ticker, start_ns, end_ns],
        ).fetchone()[0]
    )


def count_event_rows_batch(
    con: duckdb.DuckDBPyConnection,
    table: str | None,
    windows: pd.DataFrame,
) -> dict[int, int]:
    if table is None or windows.empty:
        return {}
    cols = {row[1] for row in con.execute(f"pragma table_info('{table}')").fetchall()}
    if "event_ticker" not in cols or "received_at_ns" not in cols:
        return {}
    con.register("_btc1h_probe_windows", windows[["row_id", "event_ticker", "start_ns", "end_ns"]])
    try:
        rows = con.execute(
            f"""
            select w.row_id, count(t.received_at_ns) as n
            from _btc1h_probe_windows w
            left join {table} t
              on t.event_ticker = w.event_ticker
             and t.received_at_ns between w.start_ns and w.end_ns
            group by w.row_id
            """,
        ).fetchall()
    finally:
        try:
            con.unregister("_btc1h_probe_windows")
        except Exception:
            pass
    return {int(row_id): int(count) for row_id, count in rows}


def fill_probe_counts(con: duckdb.DuckDBPyConnection, row: dict[str, Any], event_ticker: str, start_ns: int, end_ns: int) -> None:
    names = table_names(con)
    top_table = first_existing(names, ["ws_orderbook_top", "ws_orderbook_top_dedup", "ws_orderbook_top_all"])
    signal_table = first_existing(names, ["signal_scan", "signal_scan_dedup", "signal_scan_all"])
    lifecycle_table = first_existing(names, ["ws_lifecycle", "ws_lifecycle_all"])
    row.update(
        {
            "capture_readable": True,
            "top_table": top_table or "",
            "signal_table": signal_table or "",
            "lifecycle_table": lifecycle_table or "",
            "top_rows": count_event_rows(con, top_table, event_ticker, start_ns, end_ns),
            "signal_scan_rows": count_event_rows(con, signal_table, event_ticker, start_ns, end_ns),
            "lifecycle_rows": count_event_rows(con, lifecycle_table, event_ticker, start_ns, end_ns),
        }
    )


def make_probe_row(source: CaptureSource) -> dict[str, Any]:
    sidecar = replay_sidecar_path(source.path)
    return {
        "capture_label": source.label,
        "capture_path": str(source.path),
        "capture_exists": source.path.exists(),
        "capture_readable": False,
        "top_table": "",
        "signal_table": "",
        "lifecycle_table": "",
        "top_rows": 0,
        "signal_scan_rows": 0,
        "lifecycle_rows": 0,
        "order_decision_rows": 0,
        "capture_read_source": "",
        "read_error": "",
        "replay_sidecar_path": str(sidecar),
        "replay_sidecar_exists": sidecar.exists(),
        "replay_sidecar_error": "",
        "replay_sidecar_bad_lines": 0,
    }


def fill_probe_counts_batch(
    con: duckdb.DuckDBPyConnection,
    source: CaptureSource,
    windows: pd.DataFrame,
    *,
    read_source: str,
    prior_error: str = "",
) -> dict[int, dict[str, Any]]:
    names = table_names(con)
    top_table = first_existing(names, ["ws_orderbook_top", "ws_orderbook_top_dedup", "ws_orderbook_top_all"])
    signal_table = first_existing(names, ["signal_scan", "signal_scan_dedup", "signal_scan_all"])
    lifecycle_table = first_existing(names, ["ws_lifecycle", "ws_lifecycle_all"])
    top_counts = count_event_rows_batch(con, top_table, windows)
    signal_counts = count_event_rows_batch(con, signal_table, windows)
    lifecycle_counts = count_event_rows_batch(con, lifecycle_table, windows)
    out: dict[int, dict[str, Any]] = {}
    for row_id in windows["row_id"].astype(int).tolist():
        row = make_probe_row(source)
        row.update(
            {
                "capture_readable": True,
                "top_table": top_table or "",
                "signal_table": signal_table or "",
                "lifecycle_table": lifecycle_table or "",
                "top_rows": int(top_counts.get(row_id, 0)),
                "signal_scan_rows": int(signal_counts.get(row_id, 0)),
                "lifecycle_rows": int(lifecycle_counts.get(row_id, 0)),
                "capture_read_source": read_source,
                "read_error": prior_error,
            }
        )
        out[row_id] = row
    return out


def replay_sidecar_path(source: Path) -> Path:
    return source.with_name(source.name + ".replay.jsonl")


def safe_int(value: Any) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return int(value)
    except Exception:
        return None


def fill_probe_counts_from_replay_sidecar(
    sidecar: Path,
    row: dict[str, Any],
    event_ticker: str,
    start_ns: int,
    end_ns: int,
    *,
    read_source: str,
    prior_error: str = "",
) -> bool:
    row["replay_sidecar_path"] = str(sidecar)
    row["replay_sidecar_exists"] = sidecar.exists()
    if not sidecar.exists():
        return False

    counts = {
        "ws_orderbook_top": 0,
        "signal_scan": 0,
        "ws_lifecycle": 0,
        "order_decision": 0,
    }
    bad_lines = 0
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                table = str(item.get("table") or "")
                if table not in counts:
                    continue
                if str(item.get("event_ticker") or "").upper() != event_ticker:
                    continue
                received_at_ns = safe_int(item.get("received_at_ns"))
                if received_at_ns is None or received_at_ns < start_ns or received_at_ns > end_ns:
                    continue
                counts[table] += 1
    except Exception as exc:
        row["replay_sidecar_error"] = repr(exc)
        return False

    row.update(
        {
            "capture_readable": True,
            "capture_read_source": read_source,
            "top_table": "ws_orderbook_top:replay_sidecar",
            "signal_table": "signal_scan:replay_sidecar",
            "lifecycle_table": "ws_lifecycle:replay_sidecar",
            "top_rows": counts["ws_orderbook_top"],
            "signal_scan_rows": counts["signal_scan"],
            "lifecycle_rows": counts["ws_lifecycle"],
            "order_decision_rows": counts["order_decision"],
            "replay_sidecar_bad_lines": bad_lines,
        }
    )
    if prior_error:
        row["read_error"] = f"{read_source}: {prior_error}"
    return True


def fill_probe_counts_from_replay_sidecar_batch(
    sidecar: Path,
    source: CaptureSource,
    windows: pd.DataFrame,
    *,
    read_source: str,
    prior_error: str = "",
) -> dict[int, dict[str, Any]] | None:
    if not sidecar.exists():
        return None
    out = {int(row_id): make_probe_row(source) for row_id in windows["row_id"].astype(int).tolist()}
    windows_by_event: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for _, row in windows.iterrows():
        windows_by_event[str(row["event_ticker"]).upper()].append(
            (int(row["row_id"]), int(row["start_ns"]), int(row["end_ns"]))
        )
    bad_lines = 0
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                table = str(item.get("table") or "")
                if table not in {"ws_orderbook_top", "signal_scan", "ws_lifecycle", "order_decision"}:
                    continue
                event = str(item.get("event_ticker") or "").upper()
                received_at_ns = safe_int(item.get("received_at_ns"))
                if received_at_ns is None:
                    continue
                for row_id, start_ns, end_ns in windows_by_event.get(event, []):
                    if received_at_ns < start_ns or received_at_ns > end_ns:
                        continue
                    target = out[row_id]
                    if table == "ws_orderbook_top":
                        target["top_rows"] = int(target.get("top_rows", 0) or 0) + 1
                    elif table == "signal_scan":
                        target["signal_scan_rows"] = int(target.get("signal_scan_rows", 0) or 0) + 1
                    elif table == "ws_lifecycle":
                        target["lifecycle_rows"] = int(target.get("lifecycle_rows", 0) or 0) + 1
                    elif table == "order_decision":
                        target["order_decision_rows"] = int(target.get("order_decision_rows", 0) or 0) + 1
    except Exception as exc:
        return {
            int(row_id): {
                **make_probe_row(source),
                "replay_sidecar_error": repr(exc),
            }
            for row_id in windows["row_id"].astype(int).tolist()
        }
    for row in out.values():
        row.update(
            {
                "capture_readable": True,
                "capture_read_source": read_source,
                "top_table": "ws_orderbook_top:replay_sidecar",
                "signal_table": "signal_scan:replay_sidecar",
                "lifecycle_table": "ws_lifecycle:replay_sidecar",
                "replay_sidecar_exists": True,
                "replay_sidecar_bad_lines": bad_lines,
            }
        )
        if prior_error:
            row["read_error"] = f"{read_source}: {prior_error}"
    return out


def copy_capture_snapshot(source: Path, dest_dir: Path) -> Path:
    snapshot = dest_dir / source.name
    shutil.copy2(source, snapshot)
    wal = source.with_name(source.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, snapshot.with_name(snapshot.name + ".wal"))
    return snapshot


def probe_source(
    source: CaptureSource,
    event_ticker: str,
    start_ns: int,
    end_ns: int,
    locked_no_sidecar_cache: set[str] | None = None,
) -> dict[str, Any]:
    sidecar = replay_sidecar_path(source.path)
    cache_key = str(source.path.resolve()) if source.path.exists() else str(source.path)
    row: dict[str, Any] = {
        "capture_label": source.label,
        "capture_path": str(source.path),
        "capture_exists": source.path.exists(),
        "capture_readable": False,
        "top_table": "",
        "signal_table": "",
        "lifecycle_table": "",
        "top_rows": 0,
        "signal_scan_rows": 0,
        "lifecycle_rows": 0,
        "order_decision_rows": 0,
        "capture_read_source": "",
        "read_error": "",
        "replay_sidecar_path": str(sidecar),
        "replay_sidecar_exists": sidecar.exists(),
        "replay_sidecar_error": "",
        "replay_sidecar_bad_lines": 0,
    }
    if locked_no_sidecar_cache is not None and cache_key in locked_no_sidecar_cache and not sidecar.exists():
        row["read_error"] = "cached_live_read_failed_locked_no_replay_sidecar: being used by another process"
        return row
    if not source.path.exists():
        if fill_probe_counts_from_replay_sidecar(
            sidecar,
            row,
            event_ticker,
            start_ns,
            end_ns,
            read_source="replay_sidecar_without_capture_db",
        ):
            return row
        row["read_error"] = "capture_missing"
        return row
    try:
        con = duckdb.connect(str(source.path), read_only=True)
        row["capture_read_source"] = "live_readonly"
        fill_probe_counts(con, row, event_ticker, start_ns, end_ns)
        con.close()
    except Exception as exc:
        live_error = repr(exc)
        if fill_probe_counts_from_replay_sidecar(
            sidecar,
            row,
            event_ticker,
            start_ns,
            end_ns,
            read_source="replay_sidecar_after_live_read_failure",
            prior_error=live_error,
        ):
            return row
        with tempfile.TemporaryDirectory(prefix="btc1h_replay_capture_snapshot_") as tmp:
            snapshot_dir = Path(tmp)
            try:
                snapshot = copy_capture_snapshot(source.path, snapshot_dir)
                con = duckdb.connect(str(snapshot), read_only=True)
                row["capture_read_source"] = "snapshot_copy_after_live_read_failure"
                row["read_error"] = f"live_read_failed_then_snapshot_succeeded: {live_error}"
                fill_probe_counts(con, row, event_ticker, start_ns, end_ns)
                con.close()
            except Exception as snap_exc:
                row["read_error"] = f"{live_error}; snapshot_error={snap_exc!r}"
                if (
                    locked_no_sidecar_cache is not None
                    and not sidecar.exists()
                    and "being used by another process" in row["read_error"]
                ):
                    locked_no_sidecar_cache.add(cache_key)
    return row


def probe_source_batch(source: CaptureSource, windows: pd.DataFrame) -> dict[int, dict[str, Any]]:
    row_ids = windows["row_id"].astype(int).tolist()
    if windows.empty:
        return {}
    sidecar = replay_sidecar_path(source.path)

    if not source.path.exists():
        sidecar_rows = fill_probe_counts_from_replay_sidecar_batch(
            sidecar,
            source,
            windows,
            read_source="replay_sidecar_without_capture_db",
        )
        if sidecar_rows is not None:
            return sidecar_rows
        return {
            row_id: {
                **make_probe_row(source),
                "read_error": "capture_missing",
            }
            for row_id in row_ids
        }

    try:
        con = duckdb.connect(str(source.path), read_only=True)
        try:
            return fill_probe_counts_batch(con, source, windows, read_source="live_readonly")
        finally:
            con.close()
    except Exception as exc:
        live_error = repr(exc)
        sidecar_rows = fill_probe_counts_from_replay_sidecar_batch(
            sidecar,
            source,
            windows,
            read_source="replay_sidecar_after_live_read_failure",
            prior_error=live_error,
        )
        if sidecar_rows is not None:
            return sidecar_rows
        with tempfile.TemporaryDirectory(prefix="btc1h_replay_capture_snapshot_") as tmp:
            snapshot_dir = Path(tmp)
            try:
                snapshot = copy_capture_snapshot(source.path, snapshot_dir)
                con = duckdb.connect(str(snapshot), read_only=True)
                try:
                    return fill_probe_counts_batch(
                        con,
                        source,
                        windows,
                        read_source="snapshot_copy_after_live_read_failure",
                        prior_error=f"live_read_failed_then_snapshot_succeeded: {live_error}",
                    )
                finally:
                    con.close()
            except Exception as snap_exc:
                read_error = f"{live_error}; snapshot_error={snap_exc!r}"
                return {
                    row_id: {
                        **make_probe_row(source),
                        "read_error": read_error,
                    }
                    for row_id in row_ids
                }


def classify_source_rows(source_rows: list[dict[str, Any]]) -> tuple[str, str, str]:
    readable = [row for row in source_rows if row.get("capture_readable")]
    replayable = [
        row
        for row in readable
        if int(row.get("top_rows", 0) or 0) > 0 and int(row.get("signal_scan_rows", 0) or 0) > 0
    ]
    if replayable:
        best = max(replayable, key=lambda row: (int(row.get("signal_scan_rows", 0) or 0), int(row.get("top_rows", 0) or 0)))
        return "REPLAYABLE_FROM_READABLE_CAPTURE", str(best["capture_label"]), ""
    locked = [row for row in source_rows if not row.get("capture_readable") and "being used by another process" in str(row.get("read_error", ""))]
    if locked:
        return "CAPTURE_LOCKED_NEEDS_SIDECAR_OR_SNAPSHOT", "", ";".join(str(row["capture_label"]) for row in locked)
    existing_errors = [row for row in source_rows if row.get("capture_exists") and row.get("read_error")]
    if existing_errors:
        return "CAPTURE_READ_ERROR", "", ";".join(str(row["capture_label"]) for row in existing_errors)
    return "NO_READABLE_CAPTURE_COVERAGE", "", ""


def build_audit(
    trades: pd.DataFrame,
    sources: list[CaptureSource],
    *,
    since_utc: str,
    pre_close_min: float,
    post_close_min: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    since = parse_utc(since_utc)
    trade_specs: list[dict[str, Any]] = []
    row_outputs: list[dict[str, Any]] = []
    source_outputs: list[dict[str, Any]] = []
    for row_id, trade in trades.reset_index(drop=True).iterrows():
        event = str(trade.get("event_ticker", ""))
        created = parse_utc(trade.get("created_at", ""))
        close = event_close_from_ticker(event)
        scope_since = bool(created is not None and since is not None and created >= since)
        base = {
            "ledger": trade.get("ledger", ""),
            "scope_since": scope_since,
            "created_at": trade.get("created_at", ""),
            "event_ticker": event,
            "market_ticker": trade.get("market_ticker", ""),
            "side": trade.get("side", ""),
            "entry_price": trade.get("entry_price", ""),
            "official_result": trade.get("official_result", ""),
            "official_pnl": trade.get("official_pnl", ""),
            "close_time_utc": close.isoformat() if close is not None else "",
        }
        if close is None:
            row_outputs.append({**base, "coverage_status": "MISSING_EVENT_CLOSE", "best_capture_label": "", "locked_capture_labels": ""})
            continue
        start = close - pd.Timedelta(minutes=pre_close_min)
        end = close + pd.Timedelta(minutes=post_close_min)
        trade_specs.append(
            {
                **base,
                "row_id": int(row_id),
                "event_ticker": event,
                "start_ns": ns(start),
                "end_ns": ns(end),
                "window_start_utc": start.isoformat(),
                "window_end_utc": end.isoformat(),
            }
        )

    if not trade_specs:
        return pd.DataFrame(row_outputs), pd.DataFrame(source_outputs)

    windows = pd.DataFrame(
        [
            {
                "row_id": spec["row_id"],
                "event_ticker": spec["event_ticker"],
                "start_ns": spec["start_ns"],
                "end_ns": spec["end_ns"],
            }
            for spec in trade_specs
        ]
    )
    probes_by_row: dict[int, list[dict[str, Any]]] = {int(spec["row_id"]): [] for spec in trade_specs}
    for source in sources:
        source_probes = probe_source_batch(source, windows)
        for spec in trade_specs:
            row_id = int(spec["row_id"])
            probe = source_probes.get(row_id, make_probe_row(source))
            probes_by_row[row_id].append(probe)
            source_outputs.append(
                {
                    **{key: value for key, value in spec.items() if key not in {"row_id", "start_ns", "end_ns"}},
                    **probe,
                }
            )

    for spec in trade_specs:
        row_id = int(spec["row_id"])
        probes = probes_by_row.get(row_id, [])
        for probe in probes:
            probe.setdefault("capture_readable", False)
        status, best_label, locked_labels = classify_source_rows(probes)
        best = next((probe for probe in probes if probe["capture_label"] == best_label), {})
        row_outputs.append(
            {
                **{key: value for key, value in spec.items() if key not in {"row_id", "start_ns", "end_ns"}},
                "coverage_status": status,
                "best_capture_label": best_label,
                "locked_capture_labels": locked_labels,
                "best_top_rows": int(best.get("top_rows", 0) or 0),
                "best_signal_scan_rows": int(best.get("signal_scan_rows", 0) or 0),
                "best_lifecycle_rows": int(best.get("lifecycle_rows", 0) or 0),
                "best_capture_read_source": str(best.get("capture_read_source", "")),
            }
        )
    return pd.DataFrame(row_outputs), pd.DataFrame(source_outputs)


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    for scope_name, work in [("all", rows), ("since", rows[rows.get("scope_since", pd.Series(dtype=bool)).eq(True)] if not rows.empty else rows)]:
        status = work.get("coverage_status", pd.Series(dtype=str)).astype(str) if not work.empty else pd.Series(dtype=str)
        out.append(
            {
                "scope": scope_name,
                "paper_rows": int(len(work)),
                "replayable_rows": int(status.eq("REPLAYABLE_FROM_READABLE_CAPTURE").sum()),
                "locked_blocked_rows": int(status.eq("CAPTURE_LOCKED_NEEDS_SIDECAR_OR_SNAPSHOT").sum()),
                "no_coverage_rows": int(status.eq("NO_READABLE_CAPTURE_COVERAGE").sum()),
                "read_error_rows": int(status.eq("CAPTURE_READ_ERROR").sum()),
                "missing_close_rows": int(status.eq("MISSING_EVENT_CLOSE").sum()),
                "coverage_gate_pass": bool(len(work) > 0 and status.eq("REPLAYABLE_FROM_READABLE_CAPTURE").all()),
            }
        )
    return pd.DataFrame(out)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sources = parse_capture_overrides(args.capture_db)
    trades = read_shadow(args.shadow_trades, args.ledger)
    rows, sources_df = build_audit(
        trades,
        sources,
        since_utc=args.since_utc,
        pre_close_min=args.pre_close_min,
        post_close_min=args.post_close_min,
    )
    summary = summarize(rows)
    rows.to_csv(args.out_dir / "btc1h_replay_coverage_rows.csv", index=False)
    sources_df.to_csv(args.out_dir / "btc1h_replay_coverage_sources.csv", index=False)
    summary.to_csv(args.out_dir / "btc1h_replay_coverage_summary.csv", index=False)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": args.ledger,
        "shadow_trades": str(args.shadow_trades),
        "since_utc": args.since_utc,
        "pre_close_min": args.pre_close_min,
        "post_close_min": args.post_close_min,
        "capture_sources": [{"label": source.label, "path": str(source.path)} for source in sources],
        "note": "Coverage diagnostic only. A PASS here would only mean a BTC1H causal replay can be run; it is not deployment evidence.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC1H Replay Coverage Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Ledger: `{args.ledger}`",
        "",
        "## Summary",
        "",
        summary.fillna("").to_string(index=False),
        "",
        "## Rows",
        "",
        rows.fillna("").to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- This does not replay the strategy; it checks whether readable live-WS capture exists around each paper fill.",
        "- Rows blocked by a locked capture DB need a sidecar, snapshot, or authorized restart before BTC1H row reconciliation can become promotion-grade.",
        "- BTC1H remains observe-only unless a full causal replay comparator and official-settled clean-schema paper ledger agree.",
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


if __name__ == "__main__":
    raise SystemExit(main())
