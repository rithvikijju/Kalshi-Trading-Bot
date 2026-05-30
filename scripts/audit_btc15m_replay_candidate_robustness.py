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
    parser.add_argument("--min-visible-qty", type=float, default=1.0)
    parser.add_argument("--max-spread-cents", type=float, default=3.0)
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
    for col in ["result", "status", "event_ticker"]:
        df[f"_had_{col}_column"] = col in df.columns
        if col in df.columns:
            df[col] = df[col].astype(str)
        else:
            df[col] = ""
    for col in ["visible_qty", "spread_cents", "entry_price"]:
        df[f"_had_{col}_column"] = col in df.columns
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan
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


def row_quality_metrics(subset: pd.DataFrame, min_visible_qty: float, max_spread_cents: float) -> dict[str, Any]:
    trades = int(len(subset))
    if trades == 0:
        return {
            "official_settled_rows": 0,
            "duplicate_event_rows": 0,
            "executable_quote_rows": 0,
            "non_executable_quote_rows": 0,
            "max_spread_cents_observed": 0.0,
            "min_visible_qty_observed": 0.0,
            "row_quality_blockers": "",
        }

    blockers: list[str] = []
    had_result = bool(subset["_had_result_column"].all()) if "_had_result_column" in subset.columns else False
    had_event = bool(subset["_had_event_ticker_column"].all()) if "_had_event_ticker_column" in subset.columns else False
    had_visible = bool(subset["_had_visible_qty_column"].all()) if "_had_visible_qty_column" in subset.columns else False
    had_spread = bool(subset["_had_spread_cents_column"].all()) if "_had_spread_cents_column" in subset.columns else False
    had_entry = bool(subset["_had_entry_price_column"].all()) if "_had_entry_price_column" in subset.columns else False

    if had_result:
        official_settled = int(subset["result"].str.lower().isin(["yes", "no"]).sum())
        if official_settled != trades:
            blockers.append("unsettled_or_missing_official_result")
    else:
        official_settled = 0
        blockers.append("missing_official_result_column")

    if had_event:
        duplicate_event_rows = trades - int(subset["event_ticker"].astype(str).nunique())
        if duplicate_event_rows > 0:
            blockers.append("duplicate_event_rows")
    else:
        duplicate_event_rows = 0
        blockers.append("missing_event_ticker_column")

    if had_visible and had_spread and had_entry:
        visible = pd.to_numeric(subset["visible_qty"], errors="coerce")
        spread = pd.to_numeric(subset["spread_cents"], errors="coerce")
        entry = pd.to_numeric(subset["entry_price"], errors="coerce")
        executable_mask = (
            visible.ge(float(min_visible_qty) - 1e-9)
            & spread.le(float(max_spread_cents) + 1e-9)
            & entry.ge(0.01 - 1e-9)
            & entry.le(0.99 + 1e-9)
        )
        executable_rows = int(executable_mask.sum())
        non_executable_rows = trades - executable_rows
        max_spread = float(spread.max()) if spread.notna().any() else 0.0
        min_visible = float(visible.min()) if visible.notna().any() else 0.0
        if non_executable_rows > 0:
            blockers.append("non_executable_or_wide_quote_rows")
    else:
        executable_rows = 0
        non_executable_rows = trades
        max_spread = 0.0
        min_visible = 0.0
        blockers.append("missing_executable_quote_fields")

    return {
        "official_settled_rows": official_settled,
        "duplicate_event_rows": duplicate_event_rows,
        "executable_quote_rows": executable_rows,
        "non_executable_quote_rows": non_executable_rows,
        "max_spread_cents_observed": max_spread,
        "min_visible_qty_observed": min_visible,
        "row_quality_blockers": ";".join(sorted(set(blockers))),
    }


def robustness_verdict(row: dict[str, Any], min_trades: int, min_prob: float, excluded: set[str]) -> str:
    if row["strategy"] in excluded:
        return "excluded_by_prior_selection_bias_audit"
    if row.get("row_quality_blockers"):
        return "reject_execution_or_settlement_quality"
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
    min_visible_qty: float,
    max_spread_cents: float,
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
    row.update(row_quality_metrics(subset, min_visible_qty, max_spread_cents))
    row["verdict"] = robustness_verdict(row, min_trades, min_prob, excluded)
    return row


def audit_dataset(
    spec: DatasetSpec,
    iters: int,
    seed: int,
    min_trades: int,
    min_prob: float,
    excluded: set[str],
    min_visible_qty: float = 1.0,
    max_spread_cents: float = 3.0,
) -> list[dict[str, Any]]:
    trades = load_trades(spec.trades_path)
    windows = load_window_summary(spec.window_summary_path)
    strategies = sorted(trades["strategy"].dropna().astype(str).unique().tolist())
    rows = []
    for idx, strategy in enumerate(strategies):
        rng = np.random.default_rng(seed + idx)
        rows.append(
            summarize_strategy(
                spec.label,
                trades,
                windows,
                strategy,
                rng,
                iters,
                min_trades,
                min_prob,
                excluded,
                min_visible_qty,
                max_spread_cents,
            )
        )
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
                min_visible_qty=args.min_visible_qty,
                max_spread_cents=args.max_spread_cents,
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
        "min_visible_qty": args.min_visible_qty,
        "max_spread_cents": args.max_spread_cents,
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
        "official_settled_rows",
        "executable_quote_rows",
        "non_executable_quote_rows",
        "duplicate_event_rows",
        "row_quality_blockers",
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
        "A positive replay candidate remains research-only when it lacks enough trades, has a non-positive 5th-percentile trade-bootstrap PnL, shows unstable rolling windows, or fails official-settlement / one-event / executable-quote row-quality checks. Excluded strategies were rejected by separate causal/execution audits.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "rows": len(summary)}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
