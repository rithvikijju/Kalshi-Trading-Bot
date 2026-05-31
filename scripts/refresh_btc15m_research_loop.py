#!/usr/bin/env python3
"""Run the repeatable BTC15M research-loop refresh.

This wrapper intentionally does not start, stop, or restart live processes. It
refreshes the current lowdd forward evidence, scores the frozen non-trading ML
sidecar on remote raw capture, rebuilds the consolidated candidate screen, and
writes one compact status packet for check-ins.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
DEFAULT_SINCE_UTC = "2026-05-30T06:33:25+00:00"
DEFAULT_ML_START_UTC = "2026-05-30T13:00:00+00:00"
PRIORITY_CANDIDATES = {
    "branch_inventory",
    "btc15m_lowdd_current_wrapper",
    "ml_lightgbm_tabular",
    "ml_xgboost_tabular",
    "btc15m_lightgbm_tabular_nontrading_sidecar",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh BTC15M research-loop evidence and summary.")
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--ml-start-utc", default=DEFAULT_ML_START_UTC)
    parser.add_argument("--skip-lowdd-refresh", action="store_true")
    parser.add_argument("--skip-ml-score", action="store_true")
    parser.add_argument("--skip-git-fetch", action="store_true")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Write a partial summary even if one refresh step fails.",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_step(name: str, command: list[str], *, env: dict[str, str], timeout: int) -> dict[str, Any]:
    started_at = utc_now()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "name": name,
        "command": command,
        "started_at_utc": started_at,
        "completed_at_utc": utc_now(),
        "returncode": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }


def run_git_command(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    return {
        "args": args,
        "returncode": int(completed.returncode),
        "stdout": stdout,
        "stderr": stderr,
    }


def git_snapshot(*, fetch: bool) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"created_at_utc": utc_now()}
    if fetch:
        snapshot["fetch_all_prune"] = run_git_command(["fetch", "--all", "--prune"])
    snapshot["status_short_branch"] = run_git_command(["status", "--short", "--branch"])
    snapshot["head"] = run_git_command(["log", "-1", "--oneline", "--decorate"])
    snapshot["remote_branch_recency"] = run_git_command(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(committerdate:iso8601) %(refname:short) %(objectname:short)",
            "refs/remotes",
        ]
    )
    return snapshot


def latest_dir(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def preferred_or_latest(root: Path, preferred: str, pattern: str) -> Path | None:
    candidate = root / preferred
    if candidate.exists() and candidate.is_dir():
        return candidate
    return latest_dir(root, pattern)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_priority_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row.get("candidate_id") in PRIORITY_CANDIDATES
        ]


def rel(path: Path | None, base: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def summarize_remote_status(status: dict[str, Any]) -> dict[str, Any]:
    raw = status.get("raw_status", {})
    lowdd = status.get("lowdd_status", {})
    return {
        "raw": {
            "pid": raw.get("pid"),
            "failed": raw.get("failed"),
            "dropped": raw.get("dropped"),
            "updated_at_utc": raw.get("updated_at_utc"),
            "latest_utc_by_table": raw.get("latest_utc_by_table", {}),
            "rows_by_table": raw.get("rows_by_table", {}),
        },
        "lowdd": {
            "pid": lowdd.get("pid"),
            "failed": lowdd.get("failed"),
            "dropped": lowdd.get("dropped"),
            "updated_at_utc": lowdd.get("updated_at_utc"),
            "latest_utc_by_table": lowdd.get("latest_utc_by_table", {}),
            "rows_by_table": lowdd.get("rows_by_table", {}),
            "signal_scan_action_counts": lowdd.get("signal_scan_action_counts", {}),
        },
    }


def build_summary(
    *,
    stamp: str,
    out_dir: Path,
    backtest_root: Path,
    runtime_root: Path,
    steps: list[dict[str, Any]],
    git: dict[str, Any],
) -> dict[str, Any]:
    lowdd_dir = preferred_or_latest(
        backtest_root,
        f"btc15m_lowdd_forward_promotion_gate_remote_{stamp}",
        "btc15m_lowdd_forward_promotion_gate_remote_*",
    )
    ml_dir = preferred_or_latest(
        backtest_root,
        f"btc15m_ml_sidecar_remote_raw_score_{stamp}",
        "btc15m_ml_sidecar_remote_raw_score_*",
    )
    screen_dir = preferred_or_latest(
        backtest_root,
        f"btc15m_research_candidate_screen_{stamp}",
        "btc15m_research_candidate_screen_*",
    )
    lowdd_refresh_dir = preferred_or_latest(
        runtime_root / "remote_snapshots",
        f"lowdd_refresh_{stamp}",
        "lowdd_refresh_*",
    )
    ml_refresh_dir = preferred_or_latest(
        runtime_root / "remote_snapshots",
        f"ml_sidecar_raw_score_{stamp}",
        "ml_sidecar_raw_score_*",
    )
    remote_status = read_json((lowdd_refresh_dir or ml_refresh_dir) / "remote_status.json" if (lowdd_refresh_dir or ml_refresh_dir) else None)
    screen_info = read_json(screen_dir / "run_info.json" if screen_dir else None)
    screen_rows = read_priority_rows(screen_dir / "candidate_screen.csv" if screen_dir else None)
    lowdd = read_json(lowdd_dir / "lowdd_forward_promotion_gate_summary.json" if lowdd_dir else None)
    ml = read_json(ml_dir / "ml_sidecar_remote_score_summary.json" if ml_dir else None)
    failed_steps = [step for step in steps if not step.get("ok", False)]

    return {
        "created_at_utc": utc_now(),
        "stamp": stamp,
        "out_dir": rel(out_dir, PROJECT_ROOT),
        "steps_ok": not failed_steps,
        "failed_steps": [step.get("name", "") for step in failed_steps],
        "git": git,
        "remote_status_artifact": rel((lowdd_refresh_dir or ml_refresh_dir) / "remote_status.json" if (lowdd_refresh_dir or ml_refresh_dir) else None, PROJECT_ROOT),
        "remote_status": summarize_remote_status(remote_status),
        "lowdd_gate_artifact": rel(lowdd_dir, PROJECT_ROOT),
        "lowdd_gate": lowdd,
        "ml_sidecar_artifact": rel(ml_dir, PROJECT_ROOT),
        "ml_sidecar": ml,
        "candidate_screen_artifact": rel(screen_dir, PROJECT_ROOT),
        "candidate_screen_info": screen_info,
        "priority_rows": screen_rows,
        "started_or_restarted_processes": False,
        "deployed_live": False,
    }


def build_report(summary: dict[str, Any]) -> str:
    lowdd = summary.get("lowdd_gate", {})
    ml = summary.get("ml_sidecar", {})
    screen = summary.get("candidate_screen_info", {})
    raw_status = summary.get("remote_status", {}).get("raw", {})
    lowdd_status = summary.get("remote_status", {}).get("lowdd", {})
    git_status = summary.get("git", {}).get("status_short_branch", {}).get("stdout", "")
    branch_lines = summary.get("git", {}).get("remote_branch_recency", {}).get("stdout", "").splitlines()[:6]
    lines = [
        "# BTC15M Research Loop Refresh",
        "",
        f"Created UTC: `{summary.get('created_at_utc', '')}`",
        f"Stamp: `{summary.get('stamp', '')}`",
        "",
        "## Verdict",
        "",
        f"- Candidate screen deployable count: `{screen.get('deployable_now_count', '')}`.",
        (
            "- Lowdd gate: "
            f"`{lowdd.get('research_status', '')}`, production_ready=`{lowdd.get('production_ready', '')}`, "
            f"paper_settled_rows=`{lowdd.get('paper_settled_rows', '')}`, "
            f"official_pnl=`{lowdd.get('paper_official_pnl', '')}`, blockers=`{lowdd.get('blockers', '')}`."
        ),
        (
            "- ML sidecar: "
            f"`{ml.get('research_status', '')}`, proxy_rows=`{ml.get('proxy_rows', '')}`, "
            f"official_rows=`{ml.get('official_rows', '')}`, proxy_pnl_2c=`{ml.get('proxy_pnl_2c', '')}`, "
            f"blockers=`{ml.get('blockers', '')}`."
        ),
        "- No live deployment or process restart was performed.",
        "",
        "## Remote Capture",
        "",
        (
            "- Raw: "
            f"pid=`{raw_status.get('pid', '')}`, failed=`{raw_status.get('failed', '')}`, "
            f"dropped=`{raw_status.get('dropped', '')}`, updated=`{raw_status.get('updated_at_utc', '')}`."
        ),
        (
            "- Lowdd: "
            f"pid=`{lowdd_status.get('pid', '')}`, failed=`{lowdd_status.get('failed', '')}`, "
            f"dropped=`{lowdd_status.get('dropped', '')}`, updated=`{lowdd_status.get('updated_at_utc', '')}`."
        ),
        "",
        "## Git",
        "",
        "```",
        git_status,
        "```",
        "",
        "Latest remote branches:",
        "",
        "```",
        "\n".join(branch_lines),
        "```",
        "",
        "## Artifacts",
        "",
        f"- Lowdd gate: `{summary.get('lowdd_gate_artifact', '')}`",
        f"- ML sidecar: `{summary.get('ml_sidecar_artifact', '')}`",
        f"- Candidate screen: `{summary.get('candidate_screen_artifact', '')}`",
        f"- Remote status: `{summary.get('remote_status_artifact', '')}`",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    env = os.environ.copy()
    out_dir = args.out_dir or BACKTEST_ROOT / f"btc15m_research_loop_refresh_{args.stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[dict[str, Any]] = []
    if not args.skip_lowdd_refresh:
        steps.append(
            run_step(
                "refresh_lowdd_forward_evidence",
                [
                    sys.executable,
                    "scripts/refresh_btc15m_lowdd_remote_evidence.py",
                    "--stamp",
                    args.stamp,
                    "--since-utc",
                    args.since_utc,
                ],
                env=env,
                timeout=900,
            )
        )
        if not steps[-1]["ok"] and not args.continue_on_error:
            (out_dir / "steps.json").write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
            raise SystemExit(steps[-1]["returncode"])

    if not args.skip_ml_score:
        steps.append(
            run_step(
                "score_ml_sidecar_remote_raw",
                [
                    sys.executable,
                    "scripts/score_btc15m_ml_sidecar_remote_raw.py",
                    "--stamp",
                    args.stamp,
                    "--start-utc",
                    args.ml_start_utc,
                ],
                env=env,
                timeout=1200,
            )
        )
        if not steps[-1]["ok"] and not args.continue_on_error:
            (out_dir / "steps.json").write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
            raise SystemExit(steps[-1]["returncode"])

    screen_dir = BACKTEST_ROOT / f"btc15m_research_candidate_screen_{args.stamp}"
    steps.append(
        run_step(
            "build_research_candidate_screen",
            [sys.executable, "scripts/build_btc15m_research_candidate_screen.py", "--out-dir", str(screen_dir)],
            env=env,
            timeout=180,
        )
    )
    if not steps[-1]["ok"] and not args.continue_on_error:
        (out_dir / "steps.json").write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
        raise SystemExit(steps[-1]["returncode"])

    git = git_snapshot(fetch=not args.skip_git_fetch)
    summary = build_summary(
        stamp=args.stamp,
        out_dir=out_dir,
        backtest_root=BACKTEST_ROOT,
        runtime_root=RUNTIME_ROOT,
        steps=steps,
        git=git,
    )
    (out_dir / "steps.json").write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "research_loop_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "report.md").write_text(build_report(summary), encoding="utf-8")
    compact = {
        "out_dir": str(out_dir),
        "steps_ok": summary["steps_ok"],
        "deployable_now_count": summary.get("candidate_screen_info", {}).get("deployable_now_count"),
        "lowdd_status": summary.get("lowdd_gate", {}).get("research_status"),
        "lowdd_rows": summary.get("lowdd_gate", {}).get("paper_settled_rows"),
        "lowdd_pnl": summary.get("lowdd_gate", {}).get("paper_official_pnl"),
        "ml_status": summary.get("ml_sidecar", {}).get("research_status"),
        "ml_proxy_rows": summary.get("ml_sidecar", {}).get("proxy_rows"),
    }
    print(json.dumps(compact, indent=2, sort_keys=True))
    return 0 if summary["steps_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
