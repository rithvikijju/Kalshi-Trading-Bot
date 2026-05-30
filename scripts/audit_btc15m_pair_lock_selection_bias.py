#!/usr/bin/env python3
"""Audit BTC15M pair-lock rows for future-conditioned selection bias.

The replay strategy named ``cheap_pair_lock_rr`` only contains events where a
second opposite-side leg later appeared cheaply enough to lock a payout. That
is not a standalone executable policy unless the first-leg exposure for events
that never lock is also counted. This audit compares the pair-only summary with
``cheap_tail_position_aware``, which keeps those unpaired first legs.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_pair_lock_selection_bias_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", type=Path, nargs="+", required=True, help="trades.csv/all_trades.csv files to audit.")
    parser.add_argument("--labels", nargs="*", default=None, help="Optional labels matching --trades order.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--pair-strategy", default="cheap_pair_lock_rr")
    parser.add_argument("--position-strategy", default="cheap_tail_position_aware")
    return parser.parse_args()


def max_drawdown(pnls: pd.Series) -> float:
    if pnls.empty:
        return 0.0
    equity = pnls.astype(float).cumsum()
    return float((equity - equity.cummax()).min())


def pnl_sum(df: pd.DataFrame) -> float:
    return float(pd.to_numeric(df.get("pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())


def count_events(df: pd.DataFrame) -> int:
    if df.empty or "event_ticker" not in df.columns:
        return 0
    return int(df["event_ticker"].dropna().astype(str).nunique())


def is_pair_row(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    side_pair = df["side"].astype(str).str.lower().eq("pair") if "side" in df.columns else pd.Series(False, index=df.index)
    legs_pair = df["legs"].astype(str).str.contains("->", regex=False, na=False) if "legs" in df.columns else pd.Series(False, index=df.index)
    return side_pair | legs_pair


def summarize_trades(path: Path, label: str, pair_strategy: str, position_strategy: str) -> tuple[dict[str, Any], pd.DataFrame]:
    trades = pd.read_csv(path)
    if "strategy" not in trades.columns:
        raise ValueError(f"{path} has no strategy column")
    if "pnl" not in trades.columns:
        raise ValueError(f"{path} has no pnl column")
    trades["pnl"] = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
    pair = trades[trades["strategy"].astype(str).eq(pair_strategy)].copy()
    position = trades[trades["strategy"].astype(str).eq(position_strategy)].copy()
    pair_mask = is_pair_row(position)
    paired_position = position[pair_mask].copy()
    unpaired_position = position[~pair_mask].copy()
    pair_events = set(pair.get("event_ticker", pd.Series(dtype=str)).dropna().astype(str))
    paired_position_events = set(paired_position.get("event_ticker", pd.Series(dtype=str)).dropna().astype(str))
    unpaired_events = set(unpaired_position.get("event_ticker", pd.Series(dtype=str)).dropna().astype(str))
    missing_from_position = sorted(pair_events - paired_position_events)
    pair_pnl = pnl_sum(pair)
    position_pnl = pnl_sum(position)
    unpaired_pnl = pnl_sum(unpaired_position)
    paired_position_pnl = pnl_sum(paired_position)
    position_pnls = position.sort_values([c for c in ["received_at_utc", "event_ticker", "side"] if c in position.columns])["pnl"]
    if pair.empty:
        verdict = "no_pair_lock_rows"
    elif len(unpaired_position) > 0:
        verdict = "reject_pair_only_future_conditioned"
    elif missing_from_position:
        verdict = "incomplete_position_aware_comparison"
    elif position_pnl <= 0:
        verdict = "reject_position_aware_nonpositive"
    else:
        verdict = "research_only_needs_forward_runner"
    row = {
        "label": label,
        "trades_file": str(path),
        "pair_rows": int(len(pair)),
        "pair_events": count_events(pair),
        "pair_only_pnl": round(pair_pnl, 6),
        "paired_position_rows": int(len(paired_position)),
        "paired_position_pnl": round(paired_position_pnl, 6),
        "unpaired_position_rows": int(len(unpaired_position)),
        "unpaired_position_events": count_events(unpaired_position),
        "unpaired_position_pnl": round(unpaired_pnl, 6),
        "position_aware_rows": int(len(position)),
        "position_aware_events": count_events(position),
        "position_aware_pnl": round(position_pnl, 6),
        "position_aware_max_dd": round(max_drawdown(position_pnls), 6),
        "selection_bias_pnl_gap": round(pair_pnl - position_pnl, 6),
        "pair_events_missing_from_position_aware": len(missing_from_position),
        "verdict": verdict,
    }
    detail_cols = [
        c
        for c in [
            "strategy",
            "event_ticker",
            "market_ticker",
            "side",
            "legs",
            "received_at_utc",
            "first_leg_time",
            "second_leg_time",
            "entry_price",
            "premium",
            "pnl",
        ]
        if c in trades.columns
    ]
    detail = position[detail_cols].copy()
    detail.insert(0, "audit_label", label)
    return row, detail


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.labels and len(args.labels) != len(args.trades):
        raise ValueError("--labels must either be omitted or match --trades length")
    labels = args.labels or [path.parent.name for path in args.trades]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    details = []
    for path, label in zip(args.trades, labels):
        row, detail = summarize_trades(path, label, args.pair_strategy, args.position_strategy)
        summary_rows.append(row)
        if not detail.empty:
            details.append(detail)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(args.out_dir / "pair_lock_selection_bias_summary.csv", index=False)
    if details:
        pd.concat(details, ignore_index=True).to_csv(args.out_dir / "position_aware_rows.csv", index=False)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "trades": [str(path) for path in args.trades],
        "labels": labels,
        "pair_strategy": args.pair_strategy,
        "position_strategy": args.position_strategy,
        "notes": [
            "Pair-only rows condition on a later opposite-side candidate and are not standalone deployable evidence.",
            "The position-aware strategy is the causal comparison because it includes first-leg exposure when no lock arrives.",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = [
        "# BTC15M Pair-Lock Selection-Bias Audit",
        "",
        f"Generated: `{manifest['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table(summary),
        "",
        "## Interpretation",
        "",
        "`reject_pair_only_future_conditioned` means the pair-only PnL is inflated by discarding first-leg events that never got a later lock. Treat the pair-lock row as a diagnostic, not a strategy.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "rows": len(summary_rows)}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
