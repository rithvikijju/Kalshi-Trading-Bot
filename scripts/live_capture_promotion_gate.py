#!/usr/bin/env python3
"""Promotion gate for BTC 1-hour strategy research.

The gate consumes candidate evaluation rows produced by research scripts and
marks a candidate deployable only if it survives both historical validation and
official-result live-capture checks.  It intentionally does not generate
strategy rules; it is a fail-closed final check.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_OUT = Path("backtest_outputs") / "promotion_gate"
HISTORICAL_DATASETS = ("historical_validation", "historical_test")
LIVE_DATASETS = ("live_capture_holdout", "live_ledger_recent")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-long", type=Path, required=True, help="Candidate evaluation CSV.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-historical-validation-trades", type=int, default=4)
    parser.add_argument("--min-historical-test-trades", type=int, default=8)
    parser.add_argument("--min-live-capture-trades", type=int, default=8)
    parser.add_argument("--min-live-ledger-trades", type=int, default=1)
    parser.add_argument("--allow-missing-live-ledger", action="store_true")
    return parser.parse_args()


def _num(row: pd.Series, col: str, default: float = 0.0) -> float:
    value = row.get(col, default)
    if pd.isna(value):
        return default
    return float(value)


def _int(row: pd.Series, col: str, default: int = 0) -> int:
    value = row.get(col, default)
    if pd.isna(value):
        return default
    return int(value)


def apply_promotion_gate(
    eval_long: pd.DataFrame,
    *,
    min_historical_validation_trades: int = 4,
    min_historical_test_trades: int = 8,
    min_live_capture_trades: int = 8,
    min_live_ledger_trades: int = 1,
    allow_missing_live_ledger: bool = False,
) -> pd.DataFrame:
    """Return one gate row per candidate.

    Expected input columns:
    candidate, dataset, trades, pnl, pnl_delta_vs_baseline, max_drawdown,
    win_rate.  Extra columns are preserved only through metrics.
    """

    if eval_long.empty:
        return pd.DataFrame()
    required = {"candidate", "dataset", "trades", "pnl", "pnl_delta_vs_baseline", "max_drawdown"}
    missing = required - set(eval_long.columns)
    if missing:
        raise ValueError(f"evaluation CSV missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    for candidate, group in eval_long.groupby("candidate", sort=True):
        by_dataset = {str(row["dataset"]): row for _, row in group.iterrows()}
        reasons: list[str] = []

        val = by_dataset.get("historical_validation")
        test = by_dataset.get("historical_test")
        capture = by_dataset.get("live_capture_holdout")
        ledger = by_dataset.get("live_ledger_recent")

        if val is None:
            reasons.append("missing historical_validation")
        else:
            if _int(val, "trades") < min_historical_validation_trades:
                reasons.append("too few historical_validation trades")
            if _num(val, "pnl") <= 0:
                reasons.append("historical_validation pnl <= 0")
            if _num(val, "pnl_delta_vs_baseline") < 0:
                reasons.append("historical_validation worse than baseline")

        if test is None:
            reasons.append("missing historical_test")
        else:
            if _int(test, "trades") < min_historical_test_trades:
                reasons.append("too few historical_test trades")
            if _num(test, "pnl") <= 0:
                reasons.append("historical_test pnl <= 0")
            if _num(test, "pnl_delta_vs_baseline") < 0:
                reasons.append("historical_test worse than baseline")

        if capture is None:
            reasons.append("missing live_capture_holdout")
        else:
            if _int(capture, "trades") < min_live_capture_trades:
                reasons.append("too few live_capture_holdout trades")
            if _num(capture, "pnl") <= 0:
                reasons.append("live_capture_holdout pnl <= 0")
            if _num(capture, "pnl_delta_vs_baseline") < 0:
                reasons.append("live_capture_holdout worse than baseline")

        if ledger is None:
            if not allow_missing_live_ledger:
                reasons.append("missing live_ledger_recent")
        else:
            if _int(ledger, "baseline_trades") > 0:
                if _int(ledger, "trades") < min_live_ledger_trades:
                    reasons.append("too few live_ledger_recent trades")
                if _num(ledger, "pnl_delta_vs_baseline") < 0:
                    reasons.append("live_ledger_recent worse than baseline")
                if _num(ledger, "pnl") < 0:
                    reasons.append("live_ledger_recent pnl < 0")

        gate_pass = not reasons
        rows.append(
            {
                "candidate": candidate,
                "gate_pass": gate_pass,
                "reasons": "; ".join(reasons),
                "historical_validation_trades": _int(val, "trades") if val is not None else 0,
                "historical_validation_pnl": _num(val, "pnl") if val is not None else 0.0,
                "historical_validation_delta": _num(val, "pnl_delta_vs_baseline") if val is not None else 0.0,
                "historical_test_trades": _int(test, "trades") if test is not None else 0,
                "historical_test_pnl": _num(test, "pnl") if test is not None else 0.0,
                "historical_test_delta": _num(test, "pnl_delta_vs_baseline") if test is not None else 0.0,
                "live_capture_trades": _int(capture, "trades") if capture is not None else 0,
                "live_capture_pnl": _num(capture, "pnl") if capture is not None else 0.0,
                "live_capture_delta": _num(capture, "pnl_delta_vs_baseline") if capture is not None else 0.0,
                "live_capture_max_drawdown": _num(capture, "max_drawdown") if capture is not None else 0.0,
                "live_ledger_trades": _int(ledger, "trades") if ledger is not None else 0,
                "live_ledger_pnl": _num(ledger, "pnl") if ledger is not None else 0.0,
                "live_ledger_delta": _num(ledger, "pnl_delta_vs_baseline") if ledger is not None else 0.0,
                "live_ledger_max_drawdown": _num(ledger, "max_drawdown") if ledger is not None else 0.0,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(
        [
            "gate_pass",
            "live_ledger_delta",
            "live_capture_delta",
            "historical_validation_delta",
            "historical_test_delta",
        ],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)


def main() -> int:
    args = parse_args()
    eval_long = pd.read_csv(args.eval_long)
    gate = apply_promotion_gate(
        eval_long,
        min_historical_validation_trades=args.min_historical_validation_trades,
        min_historical_test_trades=args.min_historical_test_trades,
        min_live_capture_trades=args.min_live_capture_trades,
        min_live_ledger_trades=args.min_live_ledger_trades,
        allow_missing_live_ledger=args.allow_missing_live_ledger,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    gate.to_csv(args.output_dir / "promotion_gate_results.csv", index=False)
    (args.output_dir / "promotion_gate_manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "eval_long": str(args.eval_long),
                "rows": int(len(eval_long)),
                "candidates": int(eval_long["candidate"].nunique()) if "candidate" in eval_long else 0,
                "gate_pass_count": int(gate["gate_pass"].sum()) if not gate.empty else 0,
                "rules": [
                    "Historical validation and test must be profitable and no worse than baseline.",
                    "Live-capture holdout must be official-result settled, profitable, and no worse than baseline.",
                    "Recent official live ledger must not be worsened by the candidate.",
                    "The gate never invents rules; it only validates precomputed candidates.",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with pd.option_context("display.max_rows", 80, "display.width", 220):
        print(gate.head(40).to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
