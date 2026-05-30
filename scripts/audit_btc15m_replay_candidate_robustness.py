#!/usr/bin/env python3
"""Audit BTC15M live-replay candidates for sample and path robustness.

This does not find new strategies. It takes replay trade files that already use
decision-time websocket state and official settlement, then asks whether a
positive summary survives basic scientific checks: sample size, path drawdown,
trade bootstrap, and rolling-window stability when window summaries are
available.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_replay_candidate_robustness_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_EXCLUDED = {"cheap_pair_lock_rr"}


@dataclass(frozen=True)
class DatasetSpec:
    label: str
    trades_path: Path
    window_summary_path: Path | None = None


def parse_dataset(value: str) -> DatasetSpec:
    parts = value.split("|")
    if len(parts) not in {2, 3}:
        raise argparse.ArgumentTypeError("dataset must be label|trades.csv or label|trades.csv|window_summary.csv")
    label = parts[0].strip()
    trades = Path(parts[1].strip())
    window = Path(parts[2].strip()) if len(parts) == 3 and parts[2].strip() else None
    if not label:
        raise argparse.ArgumentTypeError("dataset label cannot be blank")
    return DatasetSpec(label, trades, window)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=parse_dataset, action="append", required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--bootstrap-iters", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--min-bootstrap-profit-prob", type=float, default=0.95)
    parser.add_argument("--excluded-strategy", action="append", default=sorted(DEFAULT_EXCLUDED))
    return parser.parse_args()


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"strategy", "pnl"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")
    df["strategy"] = df["strategy"].astype(str)
    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
    if "premium" in df.columns:
        df["premium"] = pd.to_numeric(df["premium"], errors="coerce").fillna(0.0)
    else:
        df["premium"] = 0.0
    if "received_at_utc" in df.columns:
        df["received_at_utc"] = pd.to_datetime(df["received_at_utc"], utc=True, errors="coerce")
    else:
        df["received_at_utc"] = pd.NaT
    return df


def load_window_summary(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "strategy" not in df.columns or "pnl" not in df.columns:
        raise ValueError(f"{path} missing strategy/pnl columns")
    df["strategy"] = df["strategy"].astype(str)
    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
    if "trades" in df.columns:
        df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0).astype(int)
    else:
        df["trades"] = 0
    return df


def max_drawdown(pnls: pd.Series) -> float:
    if pnls.empty:
        return 0.0
    equity = pnls.astype(float).cumsum()
    return float((equity - equity.cummax()).min())


def bootstrap_pnl(pnls: np.ndarray, iters: int, rng: np.random.Generator) -> dict[str, float]:
    if len(pnls) == 0:
        return {
            "bootstrap_pnl_p05": 0.0,
            "bootstrap_pnl_p50": 0.0,
            "bootstrap_pnl_p95": 0.0,
            "bootstrap_prob_profit": 0.0,
        }
    samples = rng.choice(pnls, size=(iters, len(pnls)), replace=True).sum(axis=1)
    return {
        "bootstrap_pnl_p05": float(np.quantile(samples, 0.05)),
        "bootstrap_pnl_p50": float(np.quantile(samples, 0.50)),
        "bootstrap_pnl_p95": float(np.quantile(samples, 0.95)),
        "bootstrap_prob_profit": float((samples > 0).mean()),
    }


def window_metrics(window_summary: pd.DataFrame, strategy: str) -> dict[str, Any]:
    if window_summary.empty:
        return {
            "windows": 0,
            "windows_with_trades": 0,
            "positive_windows": 0,
            "negative_windows": 0,
            "flat_windows": 0,
            "positive_window_rate": 0.0,
            "min_window_pnl": 0.0,
            "max_window_pnl": 0.0,
        }
    w = window_summary[window_summary["strategy"].eq(strategy)].copy()
    if w.empty:
        return window_metrics(pd.DataFrame(), strategy)
    windows = int(len(w))
    positive = int((w["pnl"] > 0).sum())
    negative = int((w["pnl"] < 0).sum())
    flat = int((w["pnl"] == 0).sum())
    return {
        "windows": windows,
        "windows_with_trades": int((w["trades"] > 0).sum()),
        "positive_windows": positive,
        "negative_windows": negative,
        "flat_windows": flat,
        "positive_window_rate": float(positive / windows) if windows else 0.0,
        "min_window_pnl": float(w["pnl"].min()),
        "max_window_pnl": float(w["pnl"].max()),
    }


def robustness_verdict(row: dict[str, Any], min_trades: int, min_prob: float, excluded: set[str]) -> str:
    if row["strategy"] in excluded:
        return "excluded_by_prior_selection_bias_audit"
    if row["trades"] == 0:
        return "no_trades"
    if row["pnl"] <= 0:
        return "reject_nonpositive_official_pnl"
    if row["trades"] < min_trades:
        if row["bootstrap_pnl_p05"] <= 0 or row["bootstrap_prob_profit"] < min_prob:
            return "research_promising_insufficient_sample_bootstrap_fragile"
        return "research_promising_insufficient_sample"
    if row["bootstrap_pnl_p05"] <= 0 or row["bootstrap_prob_profit"] < min_prob:
        return "research_promising_bootstrap_fragile"
    if row["windows"] and row["positive_window_rate"] < 0.70:
        return "research_promising_window_unstable"
    return "research_promising_needs_forward_paper"


def summarize_strategy(
    label: str,
    trades: pd.DataFrame,
    window_summary: pd.DataFrame,
    strategy: str,
    rng: np.random.Generator,
    iters: int,
    min_trades: int,
    min_prob: float,
    excluded: set[str],
) -> dict[str, Any]:
    subset = trades[trades["strategy"].eq(strategy)].copy()
    subset = subset.sort_values([c for c in ["received_at_utc", "event_ticker", "side"] if c in subset.columns])
    pnl_series = subset["pnl"].astype(float)
    premium = float(subset["premium"].sum()) if "premium" in subset.columns else 0.0
    row: dict[str, Any] = {
        "label": label,
        "strategy": strategy,
        "trades": int(len(subset)),
        "events": int(subset["event_ticker"].dropna().astype(str).nunique()) if "event_ticker" in subset.columns else int(len(subset)),
        "pnl": float(pnl_series.sum()) if not pnl_series.empty else 0.0,
        "premium": premium,
        "return_on_premium": float(pnl_series.sum() / premium) if premium else 0.0,
        "win_rate": float((pnl_series > 0).mean()) if not pnl_series.empty else 0.0,
        "max_dd": max_drawdown(pnl_series),
        "worst_trade": float(pnl_series.min()) if not pnl_series.empty else 0.0,
        "best_trade": float(pnl_series.max()) if not pnl_series.empty else 0.0,
        "pnl_without_best_trade": float(pnl_series.sum() - pnl_series.max()) if not pnl_series.empty else 0.0,
    }
    row.update(bootstrap_pnl(pnl_series.to_numpy(dtype=float), iters, rng))
    row.update(window_metrics(window_summary, strategy))
    row["verdict"] = robustness_verdict(row, min_trades, min_prob, excluded)
    return row


def audit_dataset(
    spec: DatasetSpec,
    iters: int,
    seed: int,
    min_trades: int,
    min_prob: float,
    excluded: set[str],
) -> list[dict[str, Any]]:
    trades = load_trades(spec.trades_path)
    windows = load_window_summary(spec.window_summary_path)
    strategies = sorted(trades["strategy"].dropna().astype(str).unique().tolist())
    rows = []
    for idx, strategy in enumerate(strategies):
        rng = np.random.default_rng(seed + idx)
        rows.append(summarize_strategy(spec.label, trades, windows, strategy, rng, iters, min_trades, min_prob, excluded))
    return rows


def markdown_table(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    excluded = set(args.excluded_strategy or [])
    rows: list[dict[str, Any]] = []
    for spec in args.dataset:
        rows.extend(
            audit_dataset(
                spec,
                iters=args.bootstrap_iters,
                seed=args.seed,
                min_trades=args.min_trades,
                min_prob=args.min_bootstrap_profit_prob,
                excluded=excluded,
            )
        )
    summary = pd.DataFrame(rows)
    summary.to_csv(args.out_dir / "candidate_robustness_summary.csv", index=False)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "datasets": [
            {
                "label": spec.label,
                "trades_path": str(spec.trades_path),
                "window_summary_path": str(spec.window_summary_path) if spec.window_summary_path else "",
            }
            for spec in args.dataset
        ],
        "bootstrap_iters": args.bootstrap_iters,
        "seed": args.seed,
        "min_trades": args.min_trades,
        "min_bootstrap_profit_prob": args.min_bootstrap_profit_prob,
        "excluded_strategy": sorted(excluded),
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    cols = [
        "label",
        "strategy",
        "trades",
        "pnl",
        "return_on_premium",
        "win_rate",
        "max_dd",
        "bootstrap_pnl_p05",
        "bootstrap_prob_profit",
        "windows",
        "positive_windows",
        "negative_windows",
        "verdict",
    ]
    report = [
        "# BTC15M Replay Candidate Robustness Audit",
        "",
        f"Generated: `{manifest['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table(summary[[c for c in cols if c in summary.columns]]),
        "",
        "## Gate Meaning",
        "",
        "A positive replay candidate remains research-only when it lacks enough trades, has a non-positive 5th-percentile trade-bootstrap PnL, or shows unstable rolling windows. Excluded strategies were rejected by separate causal/execution audits.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "rows": len(summary)}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
