#!/usr/bin/env python3
"""Refresh BTC15M lowdd forward evidence from the remote laptop.

This is an orchestration wrapper around the existing lowdd evidence scripts. It
does not tune or rescore the strategy. It snapshots the remote paper ledger,
materializes only the replay sidecar tables needed for live-policy parity, then
runs:

- build_btc15m_lowdd_postrestart_report.py
- backtest_btc15m_lowdd_sidecar_selected.py
- audit_btc15m_lowdd_paper_replay_parity.py
- build_btc15m_lowdd_forward_promotion_gate.py

Generated DB/report artifacts stay under ignored runtime/backtest_outputs
directories. The SSH password is read from CLAW_PASS or --password and is never
written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
DEFAULT_HOST = os.environ.get("CLAW_HOST", "100.92.9.80")
DEFAULT_USER = os.environ.get("CLAW_USER", "ClawService")
DEFAULT_REMOTE_ROOT = r"C:\Users\ClawService\Kalshi-Trading-Bot"
DEFAULT_REMOTE_BOT_DIR = r"C:\Users\ClawService\.btc_kalshi_bot"
DEFAULT_SINCE_UTC = "2026-05-30T06:33:25+00:00"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=os.environ.get("CLAW_PASS", ""))
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--remote-bot-dir", default=DEFAULT_REMOTE_BOT_DIR)
    parser.add_argument(
        "--remote-python",
        default="",
        help="Remote Python with duckdb installed. Defaults to <remote-root>\\.venv\\Scripts\\python.exe.",
    )
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--sleep", type=float, default=0.03, help="REST fetch sleep passed to report scripts.")
    return parser.parse_args()


def remote_join(root: str, *parts: str) -> str:
    out = root.rstrip("\\/")
    for part in parts:
        cleaned = str(part).strip("\\/")
        if cleaned:
            out += "\\" + cleaned
    return out


def win_quote(value: str) -> str:
    if value.lower() == "python":
        return value
    return '"' + value.replace('"', '""') + '"'


def ensure_remote_dir(sftp: Any, path: str) -> None:
    parts = path.replace("/", "\\").split("\\")
    if not parts:
        return
    current = parts[0]
    rest = parts[1:]
    if current.endswith(":"):
        current += "\\"
    for part in rest:
        if not part:
            continue
        current = current.rstrip("\\") + "\\" + part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def sftp_stat_json(sftp: Any, path: str) -> dict[str, Any]:
    try:
        stat = sftp.stat(path)
    except OSError:
        return {"path": path, "exists": False, "size": 0, "mtime_utc": ""}
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return {"path": path, "exists": True, "size": int(stat.st_size), "mtime_utc": mtime}


def sftp_read_json(sftp: Any, path: str) -> dict[str, Any]:
    try:
        with sftp.file(path, "r") as handle:
            text = handle.read().decode("utf-8", errors="replace")
        return json.loads(text)
    except Exception as exc:
        return {"_read_error": repr(exc), "path": path}


def run_remote(client: Any, command: str, *, timeout: int = 900) -> tuple[int, str, str]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = int(stdout.channel.recv_exit_status())
    return code, out, err


def run_local(command: list[str], cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write_text_remote(sftp: Any, path: str, text: str) -> None:
    ensure_remote_dir(sftp, str(Path(path).parent).replace("/", "\\"))
    with sftp.file(path, "w") as handle:
        handle.write(text)


def backup_script(remote_trade_db: str, remote_snapshot_db: str) -> str:
    return f"""\
import json
import os
import sqlite3

src = r\"{remote_trade_db}\"
dst = r\"{remote_snapshot_db}\"
os.makedirs(os.path.dirname(dst), exist_ok=True)
out = {{\"src\": src, \"dst\": dst, \"src_exists\": os.path.exists(src)}}
if os.path.exists(src):
    source = sqlite3.connect(f\"file:{{src}}?mode=ro\", uri=True, timeout=30)
    try:
        dest = sqlite3.connect(dst, timeout=30)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    out[\"dst_exists\"] = os.path.exists(dst)
    out[\"dst_size\"] = os.path.getsize(dst) if os.path.exists(dst) else 0
print(json.dumps(out, sort_keys=True))
"""


def read_one_csv(path: Path) -> dict[str, str]:
    import csv

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[0] if rows else {}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def connect(args: argparse.Namespace) -> Any:
    if not args.password:
        raise SystemExit("Set CLAW_PASS or pass --password. The password is not logged or stored.")
    try:
        import paramiko
    except Exception as exc:
        raise SystemExit(f"paramiko is required for remote refresh: {exc!r}") from exc
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=args.host,
        username=args.user,
        password=args.password,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def refresh_remote_inputs(args: argparse.Namespace) -> dict[str, Any]:
    remote_python = args.remote_python or remote_join(args.remote_root, ".venv", "Scripts", "python.exe")
    remote_snap_dir = remote_join(args.remote_root, "runtime", "codex_snapshots")
    remote_trade_db = remote_join(args.remote_bot_dir, "btc15m_lowdd_forward_shadow_trades.db")
    remote_sidecar = remote_join(args.remote_bot_dir, "btc15m_lowdd_forward_shadow_capture.duckdb.replay.jsonl")
    remote_raw_status = remote_join(args.remote_bot_dir, "btc15m_live_capture.duckdb.status.json")
    remote_lowdd_status = remote_join(args.remote_bot_dir, "btc15m_lowdd_forward_shadow_capture.duckdb.status.json")
    remote_raw_replay = remote_join(args.remote_bot_dir, "btc15m_live_capture.duckdb.replay.jsonl")
    remote_lowdd_replay = remote_sidecar
    remote_trade_snapshot = remote_join(remote_snap_dir, f"btc15m_lowdd_forward_shadow_trades_{args.stamp}.db")
    remote_signal_order_db = remote_join(remote_snap_dir, f"btc15m_lowdd_signal_order_{args.stamp}.duckdb")
    remote_backup_script = remote_join(remote_snap_dir, f"codex_backup_lowdd_{args.stamp}.py")
    remote_materializer = remote_join(remote_snap_dir, "materialize_btc_replay_sidecar_codex.py")

    local_snapshot_dir = RUNTIME_ROOT / "remote_snapshots" / f"lowdd_refresh_{args.stamp}"
    local_snapshot_dir.mkdir(parents=True, exist_ok=True)
    local_trade_db = local_snapshot_dir / "btc15m_lowdd_forward_shadow_trades.db"
    local_signal_order_db = local_snapshot_dir / "btc15m_lowdd_signal_order.duckdb"
    local_status_json = local_snapshot_dir / "remote_status.json"

    client = connect(args)
    try:
        sftp = client.open_sftp()
        try:
            ensure_remote_dir(sftp, remote_snap_dir)
            remote_files = {
                "raw_status": remote_raw_status,
                "lowdd_status": remote_lowdd_status,
                "raw_replay": remote_raw_replay,
                "lowdd_replay": remote_lowdd_replay,
                "lowdd_trade_db": remote_trade_db,
            }
            status = {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "remote_files": {name: sftp_stat_json(sftp, path) for name, path in remote_files.items()},
                "raw_status": sftp_read_json(sftp, remote_raw_status),
                "lowdd_status": sftp_read_json(sftp, remote_lowdd_status),
            }
            local_status_json.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")

            write_text_remote(sftp, remote_backup_script, backup_script(remote_trade_db, remote_trade_snapshot))
            materializer_text = (PROJECT_ROOT / "scripts" / "materialize_btc_replay_sidecar.py").read_text(encoding="utf-8")
            write_text_remote(sftp, remote_materializer, materializer_text)
        finally:
            sftp.close()

        backup_cmd = f"{win_quote(remote_python)} {win_quote(remote_backup_script)}"
        code, out, err = run_remote(client, backup_cmd, timeout=120)
        if code != 0:
            raise RuntimeError(f"remote ledger backup failed code={code} stdout={out!r} stderr={err!r}")
        backup_info = json.loads(out.strip())
        if not backup_info.get("dst_exists"):
            raise RuntimeError(f"remote ledger backup did not create destination: {backup_info}")

        materialize_cmd = " ".join(
            [
                win_quote(remote_python),
                win_quote(remote_materializer),
                "--sidecar",
                win_quote(remote_sidecar),
                "--out-db",
                win_quote(remote_signal_order_db),
                "--overwrite",
                "--start-utc",
                args.since_utc,
                "--table",
                "signal_scan",
                "--table",
                "order_decision",
            ]
        )
        code, out, err = run_remote(client, materialize_cmd, timeout=1200)
        if code != 0:
            raise RuntimeError(f"remote sidecar materialization failed code={code} stdout={out!r} stderr={err!r}")
        materialize_info = json.loads(out.strip())

        sftp = client.open_sftp()
        try:
            sftp.get(remote_trade_snapshot, str(local_trade_db))
            sftp.get(remote_signal_order_db, str(local_signal_order_db))
            manifest_remote = remote_signal_order_db + ".manifest.json"
            manifest_local = local_signal_order_db.with_suffix(local_signal_order_db.suffix + ".manifest.json")
            try:
                sftp.get(manifest_remote, str(manifest_local))
            except OSError:
                pass
        finally:
            sftp.close()
    finally:
        client.close()

    return {
        "local_snapshot_dir": str(local_snapshot_dir),
        "local_trade_db": str(local_trade_db),
        "local_signal_order_db": str(local_signal_order_db),
        "local_status_json": str(local_status_json),
        "backup_info": backup_info,
        "materialize_info": materialize_info,
    }


def run_reports(args: argparse.Namespace, inputs: dict[str, Any]) -> dict[str, Any]:
    tag = f"remote_{args.stamp}"
    paper_dir = BACKTEST_ROOT / f"btc15m_lowdd_postrestart_{tag}"
    selected_dir = BACKTEST_ROOT / f"btc15m_lowdd_sidecar_selected_{tag}"
    parity_dir = BACKTEST_ROOT / f"btc15m_lowdd_paper_replay_parity_{tag}"
    gate_dir = BACKTEST_ROOT / f"btc15m_lowdd_forward_promotion_gate_{tag}"

    local_trade_db = Path(inputs["local_trade_db"])
    local_signal_order_db = Path(inputs["local_signal_order_db"])

    commands = [
        [
            sys.executable,
            "scripts/build_btc15m_lowdd_postrestart_report.py",
            "--trade-db",
            str(local_trade_db),
            "--out-dir",
            str(paper_dir),
            "--since-utc",
            args.since_utc,
            "--skip-sidecar",
            "--sleep",
            str(args.sleep),
        ],
        [
            sys.executable,
            "scripts/backtest_btc15m_lowdd_sidecar_selected.py",
            "--materialized-db",
            str(local_signal_order_db),
            "--out-dir",
            str(selected_dir),
            "--start-utc",
            args.since_utc,
            "--sleep",
            str(args.sleep),
        ],
        [
            sys.executable,
            "scripts/audit_btc15m_lowdd_paper_replay_parity.py",
            "--paper-trades",
            str(paper_dir / "lowdd_postrestart_trades.csv"),
            "--materialized-db",
            str(local_signal_order_db),
            "--out-dir",
            str(parity_dir),
        ],
        [
            sys.executable,
            "scripts/build_btc15m_lowdd_forward_promotion_gate.py",
            "--selected-dir",
            str(selected_dir),
            "--parity-dir",
            str(parity_dir),
            "--paper-dir",
            str(paper_dir),
            "--out-dir",
            str(gate_dir),
        ],
    ]

    command_results = []
    for command in commands:
        result = run_local(command)
        command_results.append(
            {
                "command": command,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            }
        )

    return {
        "paper_dir": str(paper_dir),
        "selected_dir": str(selected_dir),
        "parity_dir": str(parity_dir),
        "gate_dir": str(gate_dir),
        "paper_summary": read_one_csv(paper_dir / "lowdd_postrestart_summary.csv"),
        "selected_summary": read_one_csv(selected_dir / "selected_signal_summary.csv"),
        "parity_summary": read_json(parity_dir / "paper_replay_parity_summary.json"),
        "gate_summary": read_json(gate_dir / "lowdd_forward_promotion_gate_summary.json"),
        "commands": command_results,
    }


def main() -> int:
    args = parse_args()
    inputs = refresh_remote_inputs(args)
    reports = run_reports(args, inputs)
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": args.host,
        "user": args.user,
        "remote_root": args.remote_root,
        "remote_bot_dir": args.remote_bot_dir,
        "since_utc": args.since_utc,
        "stamp": args.stamp,
        "inputs": inputs,
        "reports": reports,
    }
    run_info_path = Path(inputs["local_snapshot_dir"]) / "refresh_run_info.json"
    run_info_path.write_text(json.dumps(run_info, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"run_info": str(run_info_path), **reports["gate_summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
