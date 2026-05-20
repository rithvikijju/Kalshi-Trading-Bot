#!/usr/bin/env python3
"""Promotion-gate audit for BTC15M live-compatible ML candidates.

This script reads frozen model-training and websocket replay artifacts.  It
does not train, tune, or select thresholds.  It exists to prevent a model that
looked good on one inspected websocket slice from being promoted after failing
the next post-freeze capture.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_ml_promotion_gate_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min_test_trades", type=int, default=10)
    parser.add_argument("--min_postfreeze_trades", type=int, default=5)
    return parser.parse_args()


def latest_dir(pattern: str) -> Path:
    matches = [p for p in BACKTEST_ROOT.glob(pattern) if p.is_dir()]
    if not matches:
        raise FileNotFoundError(f"no output dir matching {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def get_row(df: pd.DataFrame, **conds: str) -> pd.Series | None:
    mask = pd.Series(True, index=df.index)
    for key, value in conds.items():
        mask &= df[key].astype(str).eq(str(value))
    rows = df[mask]
    if rows.empty:
        return None
    return rows.iloc[0]


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    work = df.copy()
    for col in work.columns:
        if pd.api.types.is_float_dtype(work[col]):
            work[col] = work[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
        else:
            work[col] = work[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), int(work[col].astype(str).map(len).max())) for col in work.columns}
    lines = ["| " + " | ".join(col.ljust(widths[col]) for col in work.columns) + " |"]
    lines.append("| " + " | ".join("-" * widths[col] for col in work.columns) + " |")
    for _, row in work.iterrows():
        lines.append("| " + " | ".join(str(row[col]).ljust(widths[col]) for col in work.columns) + " |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    model_dir = BACKTEST_ROOT / "btc15m_live_compatible_models_20260515"
    inspected_ws_dir = BACKTEST_ROOT / "btc15m_ml_comparison_ws_agg_20260515"
    postfreeze_dir = latest_dir("btc15m_livecompat_xgb_postfreeze_ws_*")

    model_summary = pd.read_csv(model_dir / "model_summary.csv")
    inspected_ws = pd.read_csv(inspected_ws_dir / "aggregate_summary.csv")
    postfreeze = pd.read_csv(postfreeze_dir / "ml_live_ws_summary.csv")

    candidates = ["xgboost_tabular", "logistic_calibrator"]
    rows = []
    for model in candidates:
        train = get_row(model_summary, model=model, split="train", pnl_model="pnl_2c")
        validation = get_row(model_summary, model=model, split="validation", pnl_model="pnl_2c")
        test = get_row(model_summary, model=model, split="test", pnl_model="pnl_2c")
        inspected = get_row(inspected_ws, run="livecompat", model=model, pnl_col="pnl_2c")
        post = get_row(postfreeze, model=model, pnl_col="pnl_2c")

        def val(row: pd.Series | None, key: str, default: float = float("nan")) -> float:
            if row is None or key not in row.index:
                return default
            return float(row[key])

        train_pnl = val(train, "pnl")
        val_pnl = val(validation, "pnl")
        test_pnl = val(test, "pnl")
        test_trades = int(val(test, "trades", 0))
        inspected_pnl = val(inspected, "pnl")
        inspected_trades = int(val(inspected, "trades", 0))
        inspected_min_day = val(inspected, "min_day_pnl")
        post_pnl = val(post, "pnl")
        post_trades = int(val(post, "trades", 0))

        failures: list[str] = []
        if train_pnl <= 0:
            failures.append("train_not_positive")
        if val_pnl <= 0:
            failures.append("validation_not_positive")
        if test_trades < args.min_test_trades:
            failures.append("too_few_april_test_trades")
        elif test_pnl <= 0:
            failures.append("april_test_not_positive")
        if inspected_trades <= 0 or inspected_pnl <= 0 or inspected_min_day <= 0:
            failures.append("inspected_ws_not_all_positive")
        if post_trades < args.min_postfreeze_trades:
            failures.append("too_few_postfreeze_trades")
        elif post_pnl <= 0:
            failures.append("postfreeze_ws_not_positive")

        rows.append(
            {
                "model": model,
                "train_pnl_2c": train_pnl,
                "validation_pnl_2c": val_pnl,
                "april_test_trades": test_trades,
                "april_test_pnl_2c": test_pnl,
                "inspected_ws_trades": inspected_trades,
                "inspected_ws_pnl_2c": inspected_pnl,
                "inspected_ws_min_day_pnl": inspected_min_day,
                "postfreeze_ws_trades": post_trades,
                "postfreeze_ws_pnl_2c": post_pnl,
                "passes_promotion_gate": len(failures) == 0,
                "failure_reasons": ";".join(failures),
            }
        )

    table = pd.DataFrame(rows)
    table.to_csv(args.out_dir / "promotion_gate.csv", index=False)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_dir": str(model_dir.relative_to(PROJECT_ROOT)),
        "inspected_ws_dir": str(inspected_ws_dir.relative_to(PROJECT_ROOT)),
        "postfreeze_dir": str(postfreeze_dir.relative_to(PROJECT_ROOT)),
        "min_test_trades": args.min_test_trades,
        "min_postfreeze_trades": args.min_postfreeze_trades,
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    report = [
        "# BTC15M ML Promotion Gate Audit",
        "",
        "Frozen live-compatible ML candidates only. No training or threshold tuning is done here.",
        "",
        "## Verdict",
        "",
        f"Candidates passing promotion gate: `{int(table['passes_promotion_gate'].sum())}`.",
        "",
        "## Candidate Table",
        "",
        markdown_table(table),
        "",
        "## Input Artifacts",
        "",
        f"- `model_dir`: `{metadata['model_dir']}`",
        f"- `inspected_ws_dir`: `{metadata['inspected_ws_dir']}`",
        f"- `postfreeze_dir`: `{metadata['postfreeze_dir']}`",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(table.to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
