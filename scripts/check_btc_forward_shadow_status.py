#!/usr/bin/env python3
"""Summarize BTC forward capture and paper-shadow status.

This is intentionally read-only. It checks the current Python process list,
known BTC15M/BTC1H capture databases, and known paper trade ledgers so forward
validation can be audited without touching live trading state.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
HOME_BOT = Path.home() / ".btc_kalshi_bot"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_forward_shadow_status_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


TARGETS = [
    {
        "name": "btc15m_live_capture",
        "family": "BTC15M",
        "kind": "capture_only",
        "script": "btc15m_live_capture.py",
        "engine_script": None,
        "capture_db": HOME_BOT / "btc15m_live_capture.duckdb",
        "trade_db": None,
    },
    {
        "name": "btc15m_q250_qty500_firstskip_shadow",
        "family": "BTC15M",
        "kind": "paper_shadow",
        "script": "btc15m_f2_q250_qty500_firstskip_shadow.py",
        "engine_script": "btc15m_lowdd_live.py",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb",
        "trade_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_trades.db",
    },
    {
        "name": "btc15m_q250_qty500_firstskip_yes_shadow",
        "family": "BTC15M",
        "kind": "paper_shadow_preregistered_not_started",
        "script": "btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        "engine_script": "btc15m_lowdd_live.py",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb",
        "trade_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_trades.db",
    },
    {
        "name": "btc15m_q1000_yes_shadow",
        "family": "BTC15M",
        "kind": "paper_shadow",
        "script": "btc15m_f2_q1000_yes_shadow.py",
        "engine_script": "btc15m_lowdd_live.py",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_capture.duckdb",
        "trade_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_trades.db",
    },
    {
        "name": "btc1h_high_conf80_entry70_no_chase_shadow",
        "family": "BTC1H",
        "kind": "paper_shadow",
        "script": "btc_1hr_high_conf80_entry70_no_chase_shadow.py",
        "engine_script": "btc_1hr_research_live.py",
        "capture_db": HOME_BOT / "btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb",
        "trade_db": HOME_BOT / "btc_1hr_high_conf80_entry70_no_chase_shadow.db",
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check BTC forward shadow/capture status.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--since-utc", default="", help="Optional ISO timestamp for post-freeze trade counts.")
    p.add_argument("--duckdb-retry-count", type=int, default=3)
    p.add_argument("--duckdb-retry-sleep", type=float, default=1.0)
    return p.parse_args()


def matching_processes() -> list[dict[str, str]]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*Kalshi-Trading-Bot*' -or $_.CommandLine -like '*btc15m*' -or $_.CommandLine -like '*btc_1hr*' -or $_.CommandLine -like '*predexon*' } | "
        "Select-Object ProcessId,"
        "@{Name='CreationDateUtc';Expression={ if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' } }},"
        "CommandLine | ConvertTo-Json -Depth 3",
    ]
    try:
        raw = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return []
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "pid": str(row.get("ProcessId", "")),
            "created_at_utc": str(row.get("CreationDateUtc", "")),
            "command_line": str(row.get("CommandLine", "")),
        }
        for row in data
    ]


def process_hygiene(command_matches: list[dict[str, str]]) -> dict[str, Any]:
    process_count = len(command_matches)
    duplicate_count = max(0, process_count - 1)
    if process_count == 0:
        status = "NOT_RUNNING"
    elif duplicate_count:
        status = "DUPLICATE_TARGET_PROCESSES"
    else:
        status = "ONE_TARGET_PROCESS"
    return {
        "process_count": process_count,
        "duplicate_process_count": duplicate_count,
        "process_hygiene_status": status,
    }


def unmanaged_processes(processes: list[dict[str, str]], target_scripts: set[str]) -> list[dict[str, str]]:
    return [
        process
        for process in processes
        if not any(script in process.get("command_line", "") for script in target_scripts)
    ]


def parse_iso_utc(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def source_freshness(script: str, engine_script: str | None, process_created_values: list[str]) -> dict[str, Any]:
    paths = [PROJECT_ROOT / "scripts" / script]
    if engine_script:
        paths.append(PROJECT_ROOT / "scripts" / engine_script)
    existing = [path for path in paths if path.exists()]
    if not existing:
        return {
            "source_latest_path": "",
            "source_latest_mtime_utc": "",
            "process_predates_latest_source": "",
            "source_freshness_status": "SOURCE_FILE_MISSING",
        }

    latest = max(existing, key=lambda path: path.stat().st_mtime)
    latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    process_times = [ts for raw in process_created_values if (ts := parse_iso_utc(raw)) is not None]
    if not process_times:
        return {
            "source_latest_path": str(latest.relative_to(PROJECT_ROOT)),
            "source_latest_mtime_utc": latest_mtime.isoformat(),
            "process_predates_latest_source": "",
            "source_freshness_status": "NOT_RUNNING_OR_PROCESS_TIME_MISSING",
        }

    predates = min(process_times) < latest_mtime
    return {
        "source_latest_path": str(latest.relative_to(PROJECT_ROOT)),
        "source_latest_mtime_utc": latest_mtime.isoformat(),
        "process_predates_latest_source": predates,
        "source_freshness_status": "RUNNING_SOURCE_STALE_RESTART_REQUIRED" if predates else "RUNNING_SOURCE_CURRENT",
    }


def table_columns_duckdb(con: Any, table: str) -> list[str]:
    try:
        rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
    except Exception:
        return []
    return [str(row[1]) for row in rows]


def table_exists_duckdb(con: Any, table: str) -> bool:
    try:
        return bool(
            con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [table],
            ).fetchone()[0]
        )
    except Exception:
        return False


def open_duckdb_readonly(path: Path, retries: int, sleep_s: float) -> Any:
    import duckdb

    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return duckdb.connect(str(path), read_only=True)
        except Exception as exc:  # pragma: no cover - live DB lock timing only
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def load_capture_status_sidecar(path: Path) -> tuple[dict[str, Any], str]:
    sidecar = path.with_name(path.name + ".status.json")
    if not sidecar.exists():
        return {}, ""
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {}, repr(exc)
    rows_by_table = data.get("rows_by_table") if isinstance(data.get("rows_by_table"), dict) else {}
    latest_by_table = data.get("latest_utc_by_table") if isinstance(data.get("latest_utc_by_table"), dict) else {}
    replay_rows_by_table = (
        data.get("replay_sidecar_rows_by_table")
        if isinstance(data.get("replay_sidecar_rows_by_table"), dict)
        else {}
    )
    replay_sidecar = str(data.get("replay_sidecar") or "")
    replay_sidecar_path = Path(replay_sidecar).expanduser() if replay_sidecar else None
    replay_sidecar_stat = replay_sidecar_path.stat() if replay_sidecar_path is not None and replay_sidecar_path.exists() else None
    stat = sidecar.stat()
    action_counts = data.get("signal_scan_action_counts")
    if isinstance(action_counts, dict):
        signal_action_counts = ";".join(
            f"{action}:{count}" for action, count in sorted(action_counts.items(), key=lambda item: str(item[0]))
        )
    else:
        signal_action_counts = ""
    return (
        {
            "capture_read_source": "sidecar_after_live_lock",
            "capture_sidecar_path": str(sidecar),
            "capture_sidecar_mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "capture_sidecar_updated_at_utc": str(data.get("updated_at_utc") or ""),
            "replay_sidecar_path": replay_sidecar,
            "replay_sidecar_exists": bool(replay_sidecar_stat is not None),
            "replay_sidecar_mtime_utc": datetime.fromtimestamp(replay_sidecar_stat.st_mtime, tz=timezone.utc).isoformat()
            if replay_sidecar_stat is not None
            else "",
            "replay_sidecar_rows_by_table": json.dumps(replay_rows_by_table, sort_keys=True),
            "capture_health_rows": rows_by_table.get("capture_health", ""),
            "capture_health_latest": latest_by_table.get("capture_health", ""),
            "ws_orderbook_top_rows": rows_by_table.get("ws_orderbook_top", ""),
            "ws_orderbook_top_latest": latest_by_table.get("ws_orderbook_top", ""),
            "signal_scan_rows": rows_by_table.get("signal_scan", ""),
            "signal_scan_latest": latest_by_table.get("signal_scan", ""),
            "signal_scan_nonzero_candidate_rows": data.get("signal_scan_nonzero_candidate_rows", ""),
            "signal_scan_action_counts": signal_action_counts,
            "signal_scan_latest_action": str(data.get("signal_scan_latest_action") or ""),
            "signal_scan_latest_detail": str(data.get("signal_scan_latest_detail") or ""),
            "order_decision_rows": rows_by_table.get("order_decision", ""),
        },
        "",
    )


def summarize_duckdb(path: Path, retries: int, sleep_s: float) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    sidecar_data, sidecar_error = load_capture_status_sidecar(path)
    out: dict[str, Any] = {
        "capture_db": str(path),
        "capture_db_exists": exists,
        "capture_db_size_bytes": int(stat.st_size) if stat is not None else "",
        "capture_db_mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        if stat is not None
        else "",
        "capture_read_source": "",
        "capture_health_rows": "",
        "capture_health_latest": "",
        "ws_orderbook_top_rows": "",
        "ws_orderbook_top_latest": "",
        "signal_scan_rows": "",
        "signal_scan_latest": "",
        "signal_scan_nonzero_candidate_rows": "",
        "signal_scan_action_counts": "",
        "signal_scan_latest_action": "",
        "signal_scan_latest_detail": "",
        "order_decision_rows": "",
        "capture_error": "",
        "capture_snapshot_error": "",
        "capture_sidecar_path": "",
        "capture_sidecar_mtime_utc": "",
        "capture_sidecar_updated_at_utc": "",
        "capture_sidecar_error": "",
        "replay_sidecar_path": "",
        "replay_sidecar_exists": "",
        "replay_sidecar_mtime_utc": "",
        "replay_sidecar_rows_by_table": "",
    }
    for key in ["capture_sidecar_path", "capture_sidecar_mtime_utc", "capture_sidecar_updated_at_utc"]:
        out[key] = sidecar_data.get(key, "")
    out["capture_sidecar_error"] = sidecar_error
    if not exists:
        out.update({key: value for key, value in sidecar_data.items() if value not in ("", None)})
        return out
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    con: Any | None = None
    try:
        con = open_duckdb_readonly(path, retries, sleep_s)
        out["capture_read_source"] = "live_readonly"
    except Exception as exc:
        live_error = repr(exc)
        tmp_ctx = tempfile.TemporaryDirectory(prefix="btc_forward_status_duckdb_")
        snapshot = Path(tmp_ctx.name) / path.name
        try:
            shutil.copy2(path, snapshot)
            con = open_duckdb_readonly(snapshot, 1, 0.0)
            out["capture_read_source"] = "snapshot_copy_after_live_lock"
            out["capture_error"] = f"live_read_failed_then_snapshot_succeeded: {live_error}"
        except Exception as snap_exc:
            if sidecar_data:
                out.update(sidecar_data)
                out["capture_error"] = f"live_read_failed_then_sidecar_used: {live_error}"
                out["capture_snapshot_error"] = repr(snap_exc)
                tmp_ctx.cleanup()
                return out
            out["capture_error"] = live_error
            out["capture_snapshot_error"] = repr(snap_exc)
            tmp_ctx.cleanup()
            return out
    try:
        for table, row_key, latest_key in [
            ("capture_health", "capture_health_rows", "capture_health_latest"),
            ("ws_orderbook_top", "ws_orderbook_top_rows", "ws_orderbook_top_latest"),
        ]:
            if not table_exists_duckdb(con, table):
                continue
            out[row_key] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            cols = table_columns_duckdb(con, table)
            for candidate in ["received_at_utc", "received_at", "ts_utc", "created_at_utc", "created_at"]:
                if candidate in cols:
                    out[latest_key] = con.execute(f"SELECT MAX({candidate}) FROM {table}").fetchone()[0]
                    break
        if table_exists_duckdb(con, "signal_scan"):
            out["signal_scan_rows"] = int(con.execute("SELECT COUNT(*) FROM signal_scan").fetchone()[0])
            cols = table_columns_duckdb(con, "signal_scan")
            time_col = ""
            for candidate in ["received_at_utc", "received_at", "ts_utc", "created_at_utc", "created_at"]:
                if candidate in cols:
                    time_col = candidate
                    out["signal_scan_latest"] = con.execute(f"SELECT MAX({candidate}) FROM signal_scan").fetchone()[0]
                    break
            if "candidate_count" in cols:
                out["signal_scan_nonzero_candidate_rows"] = int(
                    con.execute("SELECT COUNT(*) FROM signal_scan WHERE candidate_count > 0").fetchone()[0]
                )
            if "action" in cols:
                action_rows = con.execute(
                    """
                    SELECT action, COUNT(*) AS n
                    FROM signal_scan
                    GROUP BY action
                    ORDER BY n DESC
                    LIMIT 8
                    """
                ).fetchall()
                out["signal_scan_action_counts"] = ";".join(f"{action}:{count}" for action, count in action_rows)
            latest_cols = [
                col
                for col in ["action", "detail", "candidate_count", "event_ticker"]
                if col in cols
            ]
            if time_col and latest_cols:
                latest = con.execute(
                    f"SELECT {', '.join(latest_cols)} FROM signal_scan ORDER BY {time_col} DESC LIMIT 1"
                ).fetchone()
                if latest is not None:
                    latest_map = dict(zip(latest_cols, latest))
                    out["signal_scan_latest_action"] = latest_map.get("action", "")
                    out["signal_scan_latest_detail"] = latest_map.get("detail", "")
        if table_exists_duckdb(con, "order_decision"):
            out["order_decision_rows"] = int(con.execute("SELECT COUNT(*) FROM order_decision").fetchone()[0])
    except Exception as exc:
        out["capture_error"] = repr(exc)
    finally:
        if con is not None:
            con.close()
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
    return out


def table_columns_sqlite(con: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def table_exists_sqlite(con: sqlite3.Connection, table: str) -> bool:
    try:
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [table]).fetchone()
        return row is not None
    except Exception:
        return False


def summarize_sqlite(path: Path | None, since_utc: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {
        "trade_db": str(path) if path else "",
        "trade_db_exists": bool(path and path.exists()),
        "trade_rows": "",
        "paper_filled_rows": "",
        "trade_rows_since": "",
        "paper_filled_rows_since": "",
        "latest_trade_created": "",
        "trade_status_counts": "",
        "trade_error": "",
    }
    if path is None or not path.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except Exception as exc:
        out["trade_error"] = repr(exc)
        return out
    try:
        if not table_exists_sqlite(con, "research_live_trades"):
            return out
        out["trade_rows"] = int(con.execute("SELECT COUNT(*) FROM research_live_trades").fetchone()[0])
        cols = table_columns_sqlite(con, "research_live_trades")
        if "status" in cols:
            out["paper_filled_rows"] = int(
                con.execute("SELECT COUNT(*) FROM research_live_trades WHERE status = 'paper_filled'").fetchone()[0]
            )
            status_rows = con.execute(
                "SELECT status, COUNT(*) FROM research_live_trades GROUP BY status ORDER BY status"
            ).fetchall()
            out["trade_status_counts"] = ";".join(f"{status}:{count}" for status, count in status_rows)
        for candidate in ["created_at_utc", "created_at", "ts_utc", "timestamp"]:
            if candidate in cols:
                if since_utc:
                    out["trade_rows_since"] = int(
                        con.execute(
                            f"SELECT COUNT(*) FROM research_live_trades WHERE {candidate} >= ?",
                            [since_utc],
                        ).fetchone()[0]
                    )
                    if "status" in cols:
                        out["paper_filled_rows_since"] = int(
                            con.execute(
                                f"SELECT COUNT(*) FROM research_live_trades WHERE {candidate} >= ? AND status = 'paper_filled'",
                                [since_utc],
                            ).fetchone()[0]
                        )
                out["latest_trade_created"] = con.execute(
                    f"SELECT MAX({candidate}) FROM research_live_trades"
                ).fetchone()[0]
                break
    except Exception as exc:
        out["trade_error"] = repr(exc)
    finally:
        con.close()
    return out


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


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    processes = matching_processes()
    rows: list[dict[str, Any]] = []
    target_scripts = {str(target["script"]) for target in TARGETS}
    for target in TARGETS:
        command_matches = [p for p in processes if target["script"] in p["command_line"]]
        row = {
            "name": target["name"],
            "family": target["family"],
            "kind": target["kind"],
            "script": target["script"],
            "engine_script": target["engine_script"] or "",
            "running": bool(command_matches),
            "pids": ",".join(p["pid"] for p in command_matches),
            "process_created_at_utc": ",".join(p.get("created_at_utc", "") for p in command_matches),
        }
        row.update(process_hygiene(command_matches))
        row.update(
            source_freshness(
                str(target["script"]),
                str(target["engine_script"]) if target["engine_script"] else None,
                [p.get("created_at_utc", "") for p in command_matches],
            )
        )
        row.update(summarize_duckdb(Path(target["capture_db"]), args.duckdb_retry_count, args.duckdb_retry_sleep))
        row.update(summarize_sqlite(Path(target["trade_db"]) if target["trade_db"] else None, args.since_utc))
        rows.append(row)

    unmanaged = unmanaged_processes(processes, target_scripts)
    write_csv(args.out_dir / "shadow_status.csv", rows)
    write_csv(args.out_dir / "unmanaged_processes.csv", unmanaged)
    (args.out_dir / "processes.json").write_text(json.dumps(processes, indent=2, sort_keys=True), encoding="utf-8")
    duplicate_rows = [row for row in rows if int(row.get("duplicate_process_count", 0) or 0) > 0]
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "targets": len(TARGETS),
        "matching_processes": len(processes),
        "target_process_instances": sum(int(row.get("process_count", 0) or 0) for row in rows),
        "duplicate_target_process_count": sum(
            int(row.get("duplicate_process_count", 0) or 0) for row in rows
        ),
        "duplicate_target_names": ";".join(str(row.get("name", "")) for row in duplicate_rows),
        "unmanaged_matching_process_count": len(unmanaged),
        "running_targets": sum(1 for row in rows if row["running"]),
        "since_utc": args.since_utc,
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Forward Shadow Status",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Running targets: `{info['running_targets']}/{info['targets']}`",
        f"Duplicate target processes: `{info['duplicate_target_process_count']}`",
        f"Unmanaged matching processes: `{info['unmanaged_matching_process_count']}`",
        "",
        "## Status",
        "",
        "```text",
        csv_table(rows),
        "```",
        "",
        "## Unmanaged Matching Processes",
        "",
        "```text",
        csv_table(unmanaged, fields=["pid", "created_at_utc", "command_line"]) if unmanaged else "(none)",
        "```",
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


def csv_table(rows: list[dict[str, Any]], fields: list[str] | None = None) -> str:
    if not rows:
        return ""
    if fields is None:
        fields = [
            "name",
            "running",
            "pids",
            "process_count",
            "duplicate_process_count",
            "process_hygiene_status",
            "process_created_at_utc",
            "source_freshness_status",
            "source_latest_path",
            "source_latest_mtime_utc",
            "process_predates_latest_source",
            "capture_read_source",
            "capture_db_mtime_utc",
            "capture_db_size_bytes",
            "capture_health_rows",
            "capture_health_latest",
            "ws_orderbook_top_rows",
            "ws_orderbook_top_latest",
            "signal_scan_rows",
            "signal_scan_nonzero_candidate_rows",
            "signal_scan_latest_detail",
            "order_decision_rows",
            "trade_rows",
            "paper_filled_rows",
            "trade_rows_since",
            "paper_filled_rows_since",
            "latest_trade_created",
            "trade_status_counts",
            "capture_error",
            "capture_snapshot_error",
            "capture_sidecar_updated_at_utc",
            "capture_sidecar_error",
            "replay_sidecar_exists",
            "replay_sidecar_mtime_utc",
            "replay_sidecar_rows_by_table",
            "trade_error",
        ]
    widths = {
        field: min(48, max(len(field), *(len(str(row.get(field, ""))) for row in rows)))
        for field in fields
    }

    def fmt(value: Any, field: str) -> str:
        text = str(value)
        if len(text) > widths[field]:
            text = text[: widths[field] - 3] + "..."
        return text.ljust(widths[field])

    header = " | ".join(fmt(field, field) for field in fields)
    sep = "-+-".join("-" * widths[field] for field in fields)
    body = [" | ".join(fmt(row.get(field, ""), field) for field in fields) for row in rows]
    return "\n".join([header, sep, *body])


if __name__ == "__main__":
    raise SystemExit(main())
