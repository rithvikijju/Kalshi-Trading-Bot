#!/usr/bin/env python3
"""Refresh full-window causal BTC15M replay and REST-official artifacts.

This is the full-capture companion to `refresh_btc15m_postfreeze_replays.py`.
It rebuilds the frozen BTC15M q250/q250-YES/q1000-YES replay artifacts with
the current causal BTC spot-age gate (`max_btc_spot_age_sec=10`) so old
full-window diagnostics are not mixed with stale replay provenance.

It is read-only: it does not start, stop, restart, trade, deploy, or tune.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_CAPTURE_DB = Path.home() / ".btc_kalshi_bot" / "btc15m_live_capture.duckdb"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_full_causal_replay_refresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class ReplaySpec:
    name: str
    candidate_name: str
    replay_out_dir: Path
    rest_out_dir: Path
    replay_args: tuple[str, ...]


def replay_specs() -> list[ReplaySpec]:
    common = (
        "--fair-p-min",
        "0.60",
        "--edge-cents-min",
        "12",
        "--ttl-min",
        "10",
        "--ttl-max",
        "12",
        "--spread-max-cents",
        "2",
        "--entry-min",
        "0.02",
        "--entry-max",
        "0.50",
        "--max-btc-spot-age-sec",
        "10",
    )
    return [
        ReplaySpec(
            name="q250_firstskip_qty500",
            candidate_name="q250_firstskip_qty500",
            replay_out_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_latest_codex",
            rest_out_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex",
            replay_args=(
                *common,
                "--visible-qty-min",
                "250",
                "--side",
                "both",
                "--first-signal-visible-qty-min",
                "500",
            ),
        ),
        ReplaySpec(
            name="q250_firstskip_qty500_yes",
            candidate_name="q250_firstskip_qty500_yes",
            replay_out_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_latest_codex",
            rest_out_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex",
            replay_args=(
                *common,
                "--visible-qty-min",
                "250",
                "--side",
                "yes",
                "--first-signal-visible-qty-min",
                "500",
            ),
        ),
        ReplaySpec(
            name="q1000_yes",
            candidate_name="q1000_yes",
            replay_out_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_causal_latest_codex",
            rest_out_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex",
            replay_args=(
                *common,
                "--visible-qty-min",
                "1000",
                "--side",
                "yes",
            ),
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh full causal BTC15M replay artifacts.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--start", default=None, help="Optional replay start timestamp. Defaults to full capture.")
    parser.add_argument("--end", default=None, help="Optional replay end timestamp. Defaults to current capture end.")
    parser.add_argument("--sleep", type=float, default=0.05, help="REST-fill sleep between market metadata requests.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def py(script: str, *args: str) -> list[str]:
    return [sys.executable, script, *args]


def replay_command(spec: ReplaySpec, args: argparse.Namespace) -> list[str]:
    argv = py(
        "scripts/backtest_btc15m_f2_live_ws_holdout.py",
        "--capture-db",
        str(args.capture_db),
        "--out",
        str(spec.replay_out_dir),
        *spec.replay_args,
    )
    if args.start:
        argv.extend(["--start", args.start])
    if args.end:
        argv.extend(["--end", args.end])
    return argv


def rest_command(spec: ReplaySpec, args: argparse.Namespace) -> list[str]:
    return py(
        "scripts/fill_btc15m_live_ws_official_results.py",
        "--trades",
        str(spec.replay_out_dir / "f2_live_ws_trades.parquet"),
        "--out-dir",
        str(spec.rest_out_dir),
        "--candidate-name",
        spec.candidate_name,
        "--sleep",
        str(args.sleep),
    )


def command_text(argv: Iterable[str]) -> str:
    return " ".join(str(part) for part in argv)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[0] if rows else {}


def run_step(argv: list[str], dry_run: bool) -> int:
    print(command_text(argv), flush=True)
    if dry_run:
        return 0
    completed = subprocess.run(argv, cwd=PROJECT_ROOT)
    return int(completed.returncode)


def collect_row(spec: ReplaySpec, replay_code: int, rest_code: int) -> dict[str, Any]:
    replay_info = read_json(spec.replay_out_dir / "run_info.json")
    rest_info = read_json(spec.rest_out_dir / "run_info.json")
    rest_summary = read_summary(spec.rest_out_dir / "summary.csv")
    return {
        "candidate": spec.candidate_name,
        "replay_returncode": replay_code,
        "rest_returncode": rest_code,
        "replay_out_dir": str(spec.replay_out_dir),
        "rest_out_dir": str(spec.rest_out_dir),
        "capture_start_utc": replay_info.get("capture_start_utc", ""),
        "capture_end_utc": replay_info.get("capture_end_utc", ""),
        "raw_hits": replay_info.get("f2_raw_hits", ""),
        "first_signals": replay_info.get("f2_first_signals", ""),
        "first_signal_qty_rejects": replay_info.get("f2_first_signal_qty_rejects", ""),
        "closed_proxy_rows": replay_info.get("f2_signals_closed_with_proxy", ""),
        "rest_trade_rows": rest_info.get("trade_rows", ""),
        "rest_results": rest_info.get("rest_results", ""),
        "official_filled": rest_summary.get("official_filled", ""),
        "official_pnl_2c": rest_summary.get("official_pnl", ""),
        "official_win_rate": rest_summary.get("official_win_rate", ""),
        "official_proxy_result_mismatches": rest_summary.get("official_proxy_result_mismatches", ""),
        "official_minus_proxy_pnl_2c": rest_summary.get("official_minus_proxy_pnl_2c", ""),
    }


def write_outputs(args: argparse.Namespace, rows: list[dict[str, Any]], commands: list[dict[str, str]], all_ok: bool) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with (args.out_dir / "full_causal_replay_refresh_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "all_ok": all_ok,
        "capture_db": str(args.capture_db),
        "start": args.start,
        "end": args.end,
        "dry_run": args.dry_run,
        "note": "Read-only full-window causal replay/REST official refresh. Does not start, stop, restart, trade, deploy, or tune thresholds.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "commands.json").write_text(json.dumps(commands, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# BTC15M Full Causal Replay Refresh",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"All OK: `{all_ok}`",
        "",
        "## Summary",
        "",
    ]
    for row in rows:
        lines.append(
            "- {candidate}: replay_rc={replay_returncode}, rest_rc={rest_returncode}, "
            "end={capture_end_utc}, first_signals={first_signals}, official_rows={official_filled}, "
            "official_pnl_2c={official_pnl_2c}, mismatches={official_proxy_result_mismatches}".format(**row)
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This refresh only rebuilds diagnostic replay artifacts with current causal spot-age provenance.",
            "- Old rows remain non-promotion evidence; promotion still requires clean post-restart paper rows.",
            "",
        ]
    )
    (args.out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows: list[dict[str, Any]] = []
    commands: list[dict[str, str]] = []
    all_ok = True
    for spec in replay_specs():
        replay_argv = replay_command(spec, args)
        rest_argv = rest_command(spec, args)
        commands.append({"candidate": spec.candidate_name, "kind": "replay", "command": command_text(replay_argv)})
        replay_code = run_step(replay_argv, args.dry_run)
        commands.append({"candidate": spec.candidate_name, "kind": "rest_official", "command": command_text(rest_argv)})
        rest_code = run_step(rest_argv, args.dry_run) if replay_code == 0 else 1
        all_ok = all_ok and replay_code == 0 and rest_code == 0
        rows.append(collect_row(spec, replay_code, rest_code))
    write_outputs(args, rows, commands, all_ok)
    print(Path(args.out_dir) / "full_causal_replay_refresh_summary.csv")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
