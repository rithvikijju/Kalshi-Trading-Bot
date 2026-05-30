#!/usr/bin/env python3
"""Run BTC15M live-holdout replay over rolling windows.

This is a stability report for already-snapshotted DuckDB captures.  It reuses
the single-window holdout scoring code, but does not copy the capture DB for
each window.  Use it on paused/copied snapshots, not active locked collectors.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

def find_project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "scripts" / "backtest_btc15m_live_holdout.py").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT = find_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_live_holdout import (
    LOWDD_RULES,
    cheap_pair_lock,
    cheap_tail_candidates,
    cheap_tail_position_aware,
    cheap_tail_side,
    enrich,
    fetch_results,
    first_per_event,
    load_capture_window,
    markdown_or_text,
    score_lowdd_rule,
    summarize,
)


DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_live_holdout_window_grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--start", required=True, help="Inclusive UTC start timestamp.")
    parser.add_argument("--end", required=True, help="Exclusive UTC end timestamp.")
    parser.add_argument("--window-hours", type=float, default=1.0)
    parser.add_argument("--step-hours", type=float, default=1.0)
    parser.add_argument("--kalshi-sleep-sec", type=float, default=0.05)
    return parser.parse_args()


def parse_utc(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def build_windows(start: pd.Timestamp, end: pd.Timestamp, window_hours: float, step_hours: float) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if window_hours <= 0 or step_hours <= 0:
        raise ValueError("window-hours and step-hours must be positive")
    if start >= end:
        raise ValueError("start must be before end")
    windows = []
    cur = start
    window_delta = pd.Timedelta(hours=float(window_hours))
    step_delta = pd.Timedelta(hours=float(step_hours))
    while cur < end:
        nxt = min(cur + window_delta, end)
        if cur < nxt:
            windows.append((cur, nxt))
        cur += step_delta
    return windows


def score_strategies(q: pd.DataFrame) -> dict[str, pd.DataFrame]:
    candidates = cheap_tail_candidates(q)
    out: dict[str, pd.DataFrame] = {
        "cheap_yes_rr_first": cheap_tail_side(q, "yes"),
        "cheap_no_rr_first": cheap_tail_side(q, "no"),
        "cheap_tail_best_side_first": first_per_event(candidates, "cheap_tail_best_side_first"),
        "cheap_pair_lock_rr": cheap_pair_lock(candidates),
        "cheap_tail_position_aware": cheap_tail_position_aware(candidates),
    }
    for rule in LOWDD_RULES:
        trades = score_lowdd_rule(q, rule)
        if not trades.empty:
            trades["strategy"] = rule.name
        out[rule.name] = trades
    return out


def run_window(args: argparse.Namespace, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    top, btc, actual_start, actual_end, info = load_capture_window(
        args.capture_db,
        hours=float(args.window_hours),
        start_arg=start.isoformat(),
        end_arg=end.isoformat(),
    )
    events = sorted(top["event_ticker"].dropna().astype(str).unique().tolist())
    print(
        f"window {start.isoformat()} -> {end.isoformat()} top={len(top):,} btc={len(btc):,} events={len(events):,}",
        flush=True,
    )
    results = fetch_results(events, sleep_sec=args.kalshi_sleep_sec)
    finalized = int(results["result"].isin(["yes", "no"]).sum()) if not results.empty else 0
    q = enrich(top, btc, results)
    trades_by_name = score_strategies(q)
    summary_rows = []
    trade_frames = []
    for name, trades in trades_by_name.items():
        row = summarize(name, trades)
        row.update(
            {
                "window_start_utc": str(actual_start),
                "window_end_utc": str(actual_end),
                "top_rows": int(len(top)),
                "btc_ticks": int(len(btc)),
                "events_in_window": int(len(events)),
                "settlement_rows": int(len(results)),
                "finalized_result_rows": finalized,
                "feature_rows": int(len(q)),
            }
        )
        summary_rows.append(row)
        if not trades.empty:
            t = trades.copy()
            t["window_start_utc"] = str(actual_start)
            t["window_end_utc"] = str(actual_end)
            trade_frames.append(t)
    summary = pd.DataFrame(summary_rows)
    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    info.update(
        {
            "top_rows": int(len(top)),
            "btc_ticks": int(len(btc)),
            "events_in_window": int(len(events)),
            "settlement_rows": int(len(results)),
            "finalized_result_rows": finalized,
            "feature_rows": int(len(q)),
        }
    )
    return summary, all_trades, info


def aggregate_by_strategy(window_summary: pd.DataFrame, all_trades: pd.DataFrame) -> pd.DataFrame:
    strategies = window_summary["strategy"].dropna().astype(str).drop_duplicates().tolist()
    rows = []
    for strategy in strategies:
        trades = all_trades[all_trades["strategy"].astype(str).eq(strategy)].copy() if not all_trades.empty else pd.DataFrame()
        row = summarize(strategy, trades)
        subset = window_summary[window_summary["strategy"].astype(str).eq(strategy)]
        row.update(
            {
                "windows": int(len(subset)),
                "windows_with_trades": int((subset["trades"].astype(int) > 0).sum()),
                "positive_windows": int((subset["pnl"].astype(float) > 0).sum()),
                "negative_windows": int((subset["pnl"].astype(float) < 0).sum()),
                "flat_windows": int((subset["pnl"].astype(float) == 0).sum()),
                "min_window_pnl": float(subset["pnl"].astype(float).min()) if len(subset) else 0.0,
                "max_window_pnl": float(subset["pnl"].astype(float).max()) if len(subset) else 0.0,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    start = parse_utc(args.start)
    end = parse_utc(args.end)
    windows = build_windows(start, end, args.window_hours, args.step_hours)
    summaries = []
    trades = []
    infos = []
    for win_start, win_end in windows:
        summary, all_trades, info = run_window(args, win_start, win_end)
        summaries.append(summary)
        if not all_trades.empty:
            trades.append(all_trades)
        infos.append(info)
    window_summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    all_trades = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    aggregate = aggregate_by_strategy(window_summary, all_trades) if not window_summary.empty else pd.DataFrame()
    window_summary.to_csv(args.out_dir / "window_summary.csv", index=False)
    aggregate.to_csv(args.out_dir / "aggregate_summary.csv", index=False)
    if not all_trades.empty:
        all_trades.to_csv(args.out_dir / "all_trades.csv", index=False)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_db": str(args.capture_db),
        "start": args.start,
        "end": args.end,
        "window_hours": args.window_hours,
        "step_hours": args.step_hours,
        "windows": [(str(s), str(e)) for s, e in windows],
        "window_infos": infos,
        "notes": [
            "Input capture DB is assumed to be an already copied/snapshotted DuckDB.",
            "PnL uses official Kalshi REST settlement where available.",
            "Single-window replay filters invalid/crossed top-book states before scoring.",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    report = [
        "# BTC15M Live-Holdout Window Grid",
        "",
        f"Generated: `{manifest['created_at_utc']}`",
        f"Capture DB: `{args.capture_db}`",
        f"Window range: `{args.start}` -> `{args.end}`",
        f"Window hours: `{args.window_hours}`; step hours: `{args.step_hours}`",
        "",
        "## Aggregate",
        "",
        markdown_or_text(aggregate),
        "",
        "## Window Summary",
        "",
        markdown_or_text(window_summary),
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "windows": len(windows), "aggregate_rows": len(aggregate)}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
