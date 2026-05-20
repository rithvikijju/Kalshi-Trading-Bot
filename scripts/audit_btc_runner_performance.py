#!/usr/bin/env python3
"""Sample CPU, memory, and capture sidecars for BTC collector/shadow runners.

The script is intentionally read-only. It can discover local runners by exact
BTC script names when Windows CIM access is available, or it can use the PID
manifest written by start_btc_remote_collectors.ps1 on the always-on laptop.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
HOME_BOT = Path.home() / ".btc_kalshi_bot"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_runner_performance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

TARGETS = [
    {
        "name": "btc15m_live_capture",
        "script": "btc15m_live_capture.py",
        "capture_db": HOME_BOT / "btc15m_live_capture.duckdb",
    },
    {
        "name": "btc15m_q250_qty500_firstskip_shadow",
        "script": "btc15m_f2_q250_qty500_firstskip_shadow.py",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb",
    },
    {
        "name": "btc15m_q250_qty500_firstskip_yes_shadow",
        "script": "btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb",
    },
    {
        "name": "btc15m_q1000_yes_shadow",
        "script": "btc15m_f2_q1000_yes_shadow.py",
        "capture_db": PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_capture.duckdb",
    },
    {
        "name": "btc1h_high_conf80_entry70_no_chase_shadow",
        "script": "btc_1hr_high_conf80_entry70_no_chase_shadow.py",
        "capture_db": HOME_BOT / "btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb",
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only BTC runner performance sampler.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--interval-sec", type=float, default=60.0)
    p.add_argument("--manifest", type=Path, default=RUNTIME_DIR / "btc_collectors_processes.json")
    p.add_argument("--pid", action="append", default=[], help="Optional name=pid override; can be repeated.")
    return p.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_powershell(command: str, timeout: int = 30) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"PowerShell failed with exit {proc.returncode}")
    return proc.stdout.strip()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def manifest_pids(path: Path) -> dict[str, int]:
    data = load_json(path)
    rows = data.get("started")
    if not isinstance(rows, list):
        return {}
    out: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        try:
            pid = int(row.get("process_id"))
        except (TypeError, ValueError):
            continue
        if name:
            out[name] = pid
    return out


def explicit_pids(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        name, sep, raw_pid = value.partition("=")
        if not sep:
            raise SystemExit(f"--pid must be name=pid, got {value!r}")
        out[name.strip()] = int(raw_pid)
    return out


def discover_pids_by_command_line() -> dict[str, int]:
    ps = (
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        raw = run_powershell(ps, timeout=20)
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        data = [data]
    out: dict[str, int] = {}
    for row in data:
        command_line = str(row.get("CommandLine") or "")
        for target in TARGETS:
            if target["script"] in command_line:
                try:
                    out[target["name"]] = int(row["ProcessId"])
                except (KeyError, TypeError, ValueError):
                    pass
    return out


def target_pid_map(args: argparse.Namespace) -> dict[str, int]:
    out = manifest_pids(args.manifest)
    out.update(discover_pids_by_command_line())
    out.update(explicit_pids(args.pid))
    return {name: pid for name, pid in out.items() if any(target["name"] == name for target in TARGETS)}


def sample_processes(pid_map: dict[str, int]) -> dict[str, dict[str, Any]]:
    if not pid_map:
        return {}
    ids = ",".join(str(pid) for pid in sorted(set(pid_map.values())))
    ps = (
        f"$ids=@({ids}); "
        "$rows=@(); "
        "foreach ($targetPid in $ids) { "
        "$p=Get-Process -Id $targetPid -ErrorAction SilentlyContinue; "
        "if ($p) { $rows += [pscustomobject]@{"
        "Id=$p.Id; ProcessName=$p.ProcessName; CPU=$p.CPU; WS=$p.WS; PM=$p.PM; "
        "StartTime=$p.StartTime; Path=$p.Path"
        "} } "
        "}; "
        "$rows | ConvertTo-Json -Compress"
    )
    try:
        raw = run_powershell(ps, timeout=20)
    except Exception:
        return {}
    if not raw:
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    by_pid = {int(row["Id"]): row for row in data if row.get("Id") is not None}
    out: dict[str, dict[str, Any]] = {}
    for name, pid in pid_map.items():
        row = by_pid.get(pid)
        if row is not None:
            out[name] = row
    return out


def read_sidecar(capture_db: Path) -> dict[str, Any]:
    sidecar = capture_db.with_name(capture_db.name + ".status.json")
    row: dict[str, Any] = {
        "capture_db": str(capture_db),
        "capture_db_exists": capture_db.exists(),
        "capture_sidecar": str(sidecar),
        "capture_sidecar_exists": sidecar.exists(),
    }
    if capture_db.exists():
        stat = capture_db.stat()
        row["capture_db_size_bytes"] = stat.st_size
        row["capture_db_mtime_utc"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    data = load_json(sidecar)
    if not data:
        return row
    rows_by_table = data.get("rows_by_table") if isinstance(data.get("rows_by_table"), dict) else {}
    latest_by_table = data.get("latest_utc_by_table") if isinstance(data.get("latest_utc_by_table"), dict) else {}
    row.update(
        {
            "capture_failed": bool(data.get("failed", False)),
            "capture_error": str(data.get("last_error") or ""),
            "queue_depth": data.get("queue_depth", ""),
            "max_depth": data.get("max_depth", ""),
            "dropped": data.get("dropped", ""),
            "last_flush_ms": data.get("last_flush_ms", ""),
            "updated_at_utc": str(data.get("updated_at_utc") or ""),
            "capture_health_rows": rows_by_table.get("capture_health", ""),
            "ws_orderbook_top_rows": rows_by_table.get("ws_orderbook_top", ""),
            "signal_scan_rows": rows_by_table.get("signal_scan", ""),
            "ws_orderbook_top_latest": latest_by_table.get("ws_orderbook_top", ""),
            "signal_scan_latest": latest_by_table.get("signal_scan", ""),
            "signal_scan_latest_action": str(data.get("signal_scan_latest_action") or ""),
            "signal_scan_latest_detail": str(data.get("signal_scan_latest_detail") or ""),
            "signal_scan_nonzero_candidate_rows": data.get("signal_scan_nonzero_candidate_rows", ""),
        }
    )
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt_mb(value: Any) -> float:
    try:
        return round(float(value) / (1024 * 1024), 1)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pid_map = target_pid_map(args)
    started_at = utc_now()
    first = sample_processes(pid_map)
    time.sleep(max(1.0, args.interval_sec))
    second = sample_processes(pid_map)
    ended_at = utc_now()

    rows: list[dict[str, Any]] = []
    for target in TARGETS:
        name = target["name"]
        pid = pid_map.get(name)
        s0 = first.get(name, {})
        s1 = second.get(name, {})
        cpu0 = float(s0.get("CPU") or 0.0)
        cpu1 = float(s1.get("CPU") or 0.0)
        delta = max(0.0, cpu1 - cpu0) if s1 else 0.0
        rows.append(
            {
                "name": name,
                "script": target["script"],
                "pid": pid or "",
                "running": bool(s1),
                "cpu_seconds_delta": round(delta, 3),
                "approx_one_core_pct": round(delta / max(1.0, args.interval_sec) * 100.0, 2),
                "working_set_mb": fmt_mb(s1.get("WS")),
                "private_mb": fmt_mb(s1.get("PM")),
                "start_time": s1.get("StartTime", ""),
                "path": s1.get("Path", ""),
            }
        )

    sidecar_rows = [{"name": target["name"], **read_sidecar(Path(target["capture_db"]).expanduser())} for target in TARGETS]
    total_cpu = round(sum(float(row["approx_one_core_pct"]) for row in rows), 2)
    total_ws = round(sum(float(row["working_set_mb"]) for row in rows), 1)
    summary = {
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "interval_sec": args.interval_sec,
        "target_count": len(TARGETS),
        "running_count": sum(1 for row in rows if row["running"]),
        "total_approx_one_core_pct": total_cpu,
        "total_working_set_mb": total_ws,
        "process_rows": rows,
        "sidecar_rows": sidecar_rows,
    }

    write_csv(args.out_dir / "process_performance.csv", rows)
    write_csv(args.out_dir / "capture_sidecars.csv", sidecar_rows)
    (args.out_dir / "performance_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# BTC Runner Performance Audit",
        "",
        f"- Window: {started_at} to {ended_at} ({args.interval_sec:.1f}s)",
        f"- Running targets: {summary['running_count']}/{summary['target_count']}",
        f"- Total CPU load: {total_cpu:.2f}% of one core",
        f"- Total working set: {total_ws:.1f} MB",
        "",
        "## Processes",
        "",
        "| target | pid | running | one-core CPU % | working set MB | private MB |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {row['pid']} | {row['running']} | {row['approx_one_core_pct']:.2f} | "
            f"{row['working_set_mb']:.1f} | {row['private_mb']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Capture Sidecars",
            "",
            "| target | queue | max queue | dropped | last flush ms | top rows | scan rows | latest scan detail |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in sidecar_rows:
        lines.append(
            f"| {row['name']} | {row.get('queue_depth', '')} | {row.get('max_depth', '')} | "
            f"{row.get('dropped', '')} | {row.get('last_flush_ms', '')} | "
            f"{row.get('ws_orderbook_top_rows', '')} | {row.get('signal_scan_rows', '')} | "
            f"{str(row.get('signal_scan_latest_detail', ''))[:80]} |"
        )
    (args.out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out_dir": str(args.out_dir), "total_approx_one_core_pct": total_cpu, "running_count": summary["running_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
