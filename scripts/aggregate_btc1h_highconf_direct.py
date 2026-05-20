#!/usr/bin/env python3
"""Aggregate direct BTC1H high-confidence Predexon replay chunks."""

from __future__ import annotations

import argparse
import math
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc1h_highconf_direct_aggregate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_VARIANTS = [
    "high_conf_80_no_chase",
    "high_conf_80_entry70_no_chase",
    "high_conf_80_entry59_70_no_chase",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dirs", nargs="+", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS)
    parser.add_argument("--stress-cents", nargs="+", type=float, default=[0.0, 1.0, 2.0, 3.0, 4.0])
    return parser.parse_args()


def parse_mixed_utc(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return series.map(lambda x: pd.to_datetime(x, utc=True, errors="coerce"))


def split_label(ts: pd.Timestamp) -> str:
    if ts < pd.Timestamp("2026-03-24T00:00:00Z"):
        return "feb09_mar24_old_val"
    if ts < pd.Timestamp("2026-04-01T00:00:00Z"):
        return "mar24_apr01_train"
    if ts < pd.Timestamp("2026-04-08T00:00:00Z"):
        return "apr01_apr08_train"
    if ts < pd.Timestamp("2026-04-15T00:00:00Z"):
        return "apr08_apr15_val"
    if ts < pd.Timestamp("2026-04-23T00:00:00Z"):
        return "apr15_apr23_val"
    if ts < pd.Timestamp("2026-05-01T00:00:00Z"):
        return "apr23_may01_val"
    if ts < pd.Timestamp("2026-05-06T00:00:00Z"):
        return "may03_may06_external"
    return "other"


def max_drawdown(pnl: Iterable[float]) -> float:
    arr = np.asarray(list(pnl), dtype=float)
    if arr.size == 0:
        return 0.0
    eq = np.cumsum(arr)
    return float(np.min(eq - np.maximum.accumulate(eq)))


def sharpe(pnl: Iterable[float]) -> float:
    arr = np.asarray(list(pnl), dtype=float)
    if arr.size < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(np.mean(arr) / sd * math.sqrt(arr.size))


def win_series(df: pd.DataFrame) -> pd.Series:
    if "win" in df.columns:
        raw = df["win"]
        numeric = pd.to_numeric(raw, errors="coerce")
        if numeric.notna().any():
            return numeric.fillna(0.0) > 0.5
        return raw.astype(str).str.lower().isin({"true", "1", "yes"})
    return pd.to_numeric(df.get("pnl", 0.0), errors="coerce").fillna(0.0) > 0.0


def pnl_with_stress(df: pd.DataFrame, stress_cents: float) -> tuple[pd.Series, pd.Series]:
    entry = (pd.to_numeric(df["entry_price"], errors="coerce") + stress_cents / 100.0).clip(upper=0.99)
    fees = entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    premium = entry + fees
    win = win_series(df)
    pnl = pd.Series(np.where(win.to_numpy(), 1.0 - premium.to_numpy(), -premium.to_numpy()), index=df.index)
    return pnl, premium


def summarize(df: pd.DataFrame, group_cols: list[str], stress_cents: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy().sort_values(["entry_time", "variant", "market_ticker"])
    pnl, premium = pnl_with_stress(work, stress_cents)
    work["pnl_stress"] = pnl
    work["premium_stress"] = premium
    work["win_bool"] = win_series(work)
    rows = []
    for key, group in work.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        gpnl = group["pnl_stress"].astype(float)
        prem = group["premium_stress"].astype(float)
        row = {col: val for col, val in zip(group_cols, key)}
        row.update(
            {
                "extra_stress_cents": float(stress_cents),
                "trades": int(len(group)),
                "pnl": round(float(gpnl.sum()), 4),
                "return_on_100_pct": round(float(gpnl.sum()), 4),
                "premium": round(float(prem.sum()), 4),
                "rop": round(float(gpnl.sum() / prem.sum()), 4) if float(prem.sum()) > 0 else 0.0,
                "win_rate": round(float(group["win_bool"].mean()), 4),
                "max_dd": round(max_drawdown(gpnl), 4),
                "sharpe": round(sharpe(gpnl), 4),
                "avg_entry": round(float(pd.to_numeric(group["entry_price"], errors="coerce").mean()), 4),
                "first_entry": str(group["entry_time"].min()),
                "last_entry": str(group["entry_time"].max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    missing = []
    zero_trade_dirs = []
    for directory in args.dirs:
        path = directory / "btc1h_predexon_trades.csv"
        if not path.exists():
            summary_path = directory / "summary.csv"
            if summary_path.exists() and summary_path.stat().st_size <= 2:
                zero_trade_dirs.append(str(directory))
            else:
                missing.append(str(path))
            continue
        df = pd.read_csv(path)
        if df.empty:
            zero_trade_dirs.append(str(directory))
            continue
        df["source_dir"] = str(directory)
        frames.append(df)
    if not frames:
        raise SystemExit(f"no trade files found; missing={missing}")

    trades = pd.concat(frames, ignore_index=True, sort=False)
    if "variant" not in trades.columns and "model" in trades.columns:
        trades["variant"] = trades["model"]
    trades = trades[trades["variant"].astype(str).isin(set(args.variants))].copy()
    trades["entry_time"] = parse_mixed_utc(trades["entry_time"])
    trades["entry_price"] = pd.to_numeric(trades["entry_price"], errors="coerce")
    trades = trades.dropna(subset=["entry_time", "entry_price"]).sort_values(
        ["entry_time", "variant", "market_ticker"]
    ).reset_index(drop=True)
    trades["split"] = trades["entry_time"].map(split_label)
    trades.to_csv(args.out_dir / "trades.csv", index=False)

    summaries = []
    for stress in args.stress_cents:
        summaries.append(summarize(trades, ["variant"], stress).assign(split="all"))
        summaries.append(summarize(trades, ["split", "variant"], stress))
    summary = pd.concat([s for s in summaries if not s.empty], ignore_index=True)
    summary.to_csv(args.out_dir / "summary.csv", index=False)

    stress2 = summary[summary["extra_stress_cents"].eq(2.0)].copy()
    all_stress = summary[summary["split"].eq("all")].copy()
    report = [
        "# BTC1H High-Confidence Direct Predexon Aggregate",
        "",
        "This aggregates full direct Predexon causal replay chunks for frozen high-confidence BTC1H candidates.",
        "",
        "Input dirs:",
        *[f"- `{d}`" for d in args.dirs],
        "",
        "Missing trade files:",
        *(["- none"] if not missing else [f"- `{m}`" for m in missing]),
        "",
        "Zero-trade chunks:",
        *(["- none"] if not zero_trade_dirs else [f"- `{d}`" for d in zero_trade_dirs]),
        "",
        "## +2c Stress Summary",
        "",
        markdown_table(
            stress2[
                [
                    "split",
                    "variant",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "premium",
                    "avg_entry",
                    "first_entry",
                    "last_entry",
                ]
            ].sort_values(["split", "pnl"], ascending=[True, False])
        ),
        "",
        "## All-Window Stress Curve",
        "",
        markdown_table(
            all_stress[
                [
                    "variant",
                    "extra_stress_cents",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "premium",
                    "rop",
                ]
            ].sort_values(["variant", "extra_stress_cents"])
        ),
        "",
        "## Caveat",
        "",
        "- These are Predexon snapshots, not local live websocket captures.",
        "- This is a fixed-candidate comparison, not threshold search.",
        "- Promotion still requires enough post-freeze paper-shadow/live-ledger evidence.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(stress2.sort_values(["split", "pnl"], ascending=[True, False]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
