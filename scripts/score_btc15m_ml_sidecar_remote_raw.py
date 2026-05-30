#!/usr/bin/env python3
"""Score the frozen BTC15M LightGBM sidecar on remote raw capture.

This is an offline research scorer. It snapshots a time-bounded slice of the
remote raw replay sidecar into a local DuckDB, runs the frozen ML replay, and
writes a conservative gate summary. It never starts, stops, or restarts remote
processes and never submits live or paper orders.
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

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
DEFAULT_HOST = os.environ.get("CLAW_HOST", "100.92.9.80")
DEFAULT_USER = os.environ.get("CLAW_USER", "ClawService")
DEFAULT_REMOTE_ROOT = r"C:\Users\ClawService\Kalshi-Trading-Bot"
DEFAULT_REMOTE_BOT_DIR = r"C:\Users\ClawService\.btc_kalshi_bot"
DEFAULT_START_UTC = "2026-05-30T13:00:00+00:00"
DEFAULT_MODEL_NAME = "lightgbm_tabular"


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
    parser.add_argument("--start-utc", default=DEFAULT_START_UTC)
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--min-official-rows", type=int, default=50)
    parser.add_argument("--min-clean-proxy-rows", type=int, default=100)
    parser.add_argument("--out-dir", type=Path, default=None)
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


def write_text_remote(sftp: Any, path: str, text: str) -> None:
    ensure_remote_dir(sftp, str(Path(path).parent).replace("/", "\\"))
    with sftp.file(path, "w") as handle:
        handle.write(text)


def run_remote(client: Any, command: str, *, timeout: int = 1200) -> tuple[int, str, str]:
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


def connect(args: argparse.Namespace) -> Any:
    if not args.password:
        raise SystemExit("Set CLAW_PASS or pass --password. The password is not logged or stored.")
    try:
        import paramiko
    except Exception as exc:
        raise SystemExit(f"paramiko is required for remote scoring: {exc!r}") from exc
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


def materialize_remote_raw(args: argparse.Namespace) -> dict[str, Any]:
    remote_python = args.remote_python or remote_join(args.remote_root, ".venv", "Scripts", "python.exe")
    remote_snap_dir = remote_join(args.remote_root, "runtime", "codex_snapshots")
    remote_raw_replay = remote_join(args.remote_bot_dir, "btc15m_live_capture.duckdb.replay.jsonl")
    remote_raw_status = remote_join(args.remote_bot_dir, "btc15m_live_capture.duckdb.status.json")
    remote_out_db = remote_join(remote_snap_dir, f"btc15m_raw_ml_sidecar_{args.stamp}.duckdb")
    remote_materializer = remote_join(remote_snap_dir, "materialize_btc_replay_sidecar_codex.py")

    local_snapshot_dir = RUNTIME_ROOT / "remote_snapshots" / f"ml_sidecar_raw_score_{args.stamp}"
    local_snapshot_dir.mkdir(parents=True, exist_ok=True)
    local_db = local_snapshot_dir / "btc15m_raw_ml_sidecar.duckdb"
    local_status_json = local_snapshot_dir / "remote_status.json"

    client = connect(args)
    try:
        sftp = client.open_sftp()
        try:
            ensure_remote_dir(sftp, remote_snap_dir)
            status = {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "remote_files": {
                    "raw_status": sftp_stat_json(sftp, remote_raw_status),
                    "raw_replay": sftp_stat_json(sftp, remote_raw_replay),
                },
                "raw_status": sftp_read_json(sftp, remote_raw_status),
            }
            local_status_json.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
            materializer_text = (PROJECT_ROOT / "scripts" / "materialize_btc_replay_sidecar.py").read_text(
                encoding="utf-8"
            )
            write_text_remote(sftp, remote_materializer, materializer_text)
        finally:
            sftp.close()

        cmd_parts = [
            win_quote(remote_python),
            win_quote(remote_materializer),
            "--sidecar",
            win_quote(remote_raw_replay),
            "--out-db",
            win_quote(remote_out_db),
            "--overwrite",
            "--start-utc",
            args.start_utc,
            "--table",
            "ws_orderbook_top",
            "--table",
            "ws_lifecycle",
            "--table",
            "coinbase_ticker",
            "--table",
            "order_decision",
        ]
        if args.end_utc:
            cmd_parts.extend(["--end-utc", args.end_utc])
        code, out, err = run_remote(client, " ".join(cmd_parts), timeout=1800)
        if code != 0:
            raise RuntimeError(f"remote raw sidecar materialization failed code={code} stdout={out!r} stderr={err!r}")
        materialize_info = json.loads(out.strip())

        sftp = client.open_sftp()
        try:
            sftp.get(remote_out_db, str(local_db))
            manifest_remote = remote_out_db + ".manifest.json"
            manifest_local = local_db.with_suffix(local_db.suffix + ".manifest.json")
            try:
                sftp.get(manifest_remote, str(manifest_local))
            except OSError:
                pass
        finally:
            sftp.close()
    finally:
        client.close()

    return {
        "remote_raw_replay": remote_raw_replay,
        "remote_out_db": remote_out_db,
        "local_snapshot_dir": str(local_snapshot_dir),
        "local_db": str(local_db),
        "local_status_json": str(local_status_json),
        "materialize_info": materialize_info,
    }


def read_summary_row(summary: pd.DataFrame, model: str, pnl_col: str, official_subset: bool) -> pd.Series | None:
    if summary.empty:
        return None
    subset_text = "True" if official_subset else "False"
    mask = (
        summary["model"].astype(str).eq(model)
        & summary["pnl_col"].astype(str).eq(pnl_col)
        & summary["official_subset"].astype(str).eq(subset_text)
    )
    rows = summary[mask]
    if rows.empty:
        return None
    return rows.iloc[0]


def as_float(row: pd.Series | None, col: str, default: float = 0.0) -> float:
    if row is None or col not in row:
        return default
    try:
        if pd.isna(row[col]):
            return default
        return float(row[col])
    except (TypeError, ValueError):
        return default


def as_int(row: pd.Series | None, col: str, default: int = 0) -> int:
    return int(round(as_float(row, col, float(default))))


def build_gate_summary(
    *,
    model_name: str,
    score_dir: Path,
    materialized_db: Path,
    start_utc: str,
    end_utc: str,
    min_official_rows: int,
    min_clean_proxy_rows: int,
) -> dict[str, Any]:
    summary = pd.read_csv(score_dir / "ml_live_ws_summary.csv")
    run_info = json.loads((score_dir / "run_info.json").read_text(encoding="utf-8"))
    proxy = read_summary_row(summary, model_name, "pnl_2c", official_subset=False)
    official = read_summary_row(summary, model_name, "pnl_official_2c", official_subset=True)
    proxy_rows = as_int(proxy, "trades")
    official_rows = as_int(official, "trades")
    proxy_pnl = as_float(proxy, "pnl")
    official_pnl = as_float(official, "pnl")
    blockers: list[str] = []
    advisories: list[str] = []
    if proxy_rows == 0:
        blockers.append("no_selected_proxy_rows")
    if proxy_rows < min_clean_proxy_rows:
        blockers.append("clean_proxy_rows_below_min")
    if official_rows < min_official_rows:
        blockers.append("official_rows_below_min")
    if proxy_rows > 0 and proxy_pnl <= 0:
        blockers.append("proxy_pnl_2c_not_positive")
    if official_rows > 0 and official_pnl <= 0:
        blockers.append("official_pnl_2c_not_positive")
    if official_rows == 0:
        advisories.append("no_official_settled_rows_yet")

    return {
        "candidate_id": "btc15m_lightgbm_tabular_nontrading_sidecar",
        "model_name": model_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "start_utc": start_utc,
        "end_utc": end_utc or str(run_info.get("capture_end_utc", "")),
        "materialized_db": str(materialized_db),
        "score_dir": str(score_dir),
        "candidate_rows": int(run_info.get("candidate_rows", 0)),
        "candidate_events": int(run_info.get("candidate_events", 0)),
        "proxy_rows": proxy_rows,
        "proxy_pnl_2c": round(proxy_pnl, 6),
        "proxy_premium": round(as_float(proxy, "premium"), 6),
        "proxy_win_rate_pct": round(as_float(proxy, "win_rate_pct"), 6),
        "proxy_max_drawdown": round(as_float(proxy, "max_dd"), 6),
        "official_rows": official_rows,
        "official_pnl_2c": round(official_pnl, 6),
        "official_premium": round(as_float(official, "premium"), 6),
        "official_win_rate_pct": round(as_float(official, "win_rate_pct"), 6),
        "official_max_drawdown": round(as_float(official, "max_dd"), 6),
        "min_clean_proxy_rows": int(min_clean_proxy_rows),
        "min_official_rows": int(min_official_rows),
        "deployable_now": False,
        "paper_order_allowed": False,
        "order_submission_allowed": False,
        "research_status": "metric_only_collect_more" if blockers else "metric_only_ready_for_review_not_deployment",
        "blockers": ";".join(blockers),
        "advisories": ";".join(advisories),
        "next_action": "score_later_raw_capture_snapshot;do_not_start_ordering_shadow",
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    df = pd.DataFrame(rows)
    for col in df.columns:
        df[col] = df[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), int(df[col].map(len).max())) for col in df.columns}
    lines = ["| " + " | ".join(col.ljust(widths[col]) for col in df.columns) + " |"]
    lines.append("| " + " | ".join("-" * widths[col] for col in df.columns) + " |")
    for _, src in df.iterrows():
        lines.append("| " + " | ".join(str(src[col]).ljust(widths[col]) for col in df.columns) + " |")
    return "\n".join(lines)


def write_report(out_dir: Path, gate: dict[str, Any], run_info: dict[str, Any]) -> None:
    rows = [
        {
            "candidate_id": gate["candidate_id"],
            "research_status": gate["research_status"],
            "proxy_rows": gate["proxy_rows"],
            "proxy_pnl_2c": gate["proxy_pnl_2c"],
            "official_rows": gate["official_rows"],
            "official_pnl_2c": gate["official_pnl_2c"],
            "blockers": gate["blockers"],
        }
    ]
    lines = [
        "# BTC15M ML Sidecar Remote Raw Score",
        "",
        "## Verdict",
        "",
        "- This is an offline metric score only.",
        "- No orders or paper orders were submitted.",
        "- Deployment remains false by construction.",
        "",
        "## Gate Summary",
        "",
        markdown_table(rows),
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(run_info, indent=2, sort_keys=True),
        "```",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_score(args: argparse.Namespace, inputs: dict[str, Any]) -> dict[str, Any]:
    out_dir = args.out_dir or BACKTEST_ROOT / f"btc15m_ml_sidecar_remote_raw_score_{args.stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    score_dir = out_dir / "ml_live_ws_score"
    materialized_db = Path(inputs["local_db"])
    cmd = [
        sys.executable,
        "scripts/backtest_btc15m_ml_live_ws_holdout.py",
        "--capture-db",
        str(materialized_db),
        "--start",
        args.start_utc,
        "--models",
        args.model_name,
        "--out",
        str(score_dir),
    ]
    if args.end_utc:
        cmd.extend(["--end", args.end_utc])
    completed = run_local(cmd)
    gate = build_gate_summary(
        model_name=args.model_name,
        score_dir=score_dir,
        materialized_db=materialized_db,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        min_official_rows=args.min_official_rows,
        min_clean_proxy_rows=args.min_clean_proxy_rows,
    )
    pd.DataFrame([gate]).to_csv(out_dir / "ml_sidecar_remote_score_summary.csv", index=False)
    (out_dir / "ml_sidecar_remote_score_summary.json").write_text(
        json.dumps(gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "score_command": cmd,
        "score_stdout": completed.stdout,
        "score_stderr": completed.stderr,
        "gate_summary": gate,
        "started_or_restarted_processes": False,
        "submitted_orders": False,
        "paper_orders": False,
        "deployed_live": False,
    }
    (out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_report(out_dir, gate, run_info)
    return {"out_dir": str(out_dir), "score_dir": str(score_dir), "gate_summary": gate}


def main() -> int:
    args = parse_args()
    inputs = materialize_remote_raw(args)
    result = run_score(args, inputs)
    print(json.dumps(result["gate_summary"], indent=2, sort_keys=True))
    print(f"Wrote {result['out_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
