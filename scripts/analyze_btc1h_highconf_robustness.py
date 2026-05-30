#!/usr/bin/env python3
"""Robustness audit for BTC1H high-confidence candidates.

This script does not search thresholds.  It consolidates already-frozen BTC1H
candidate trade files and applies extra adverse entry stress to test whether
the apparent edge survives small execution worsening.
"""

from __future__ import annotations

import math
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402

DEFAULT_OUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"btc1h_highconf_robustness_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
MODELS = [
    "current_1h_late_loss_guard",
    "research_original_late",
    "high_conf_80",
    "high_conf_80_no_chase",
    "high_conf_80_entry70_no_chase",
    "high_conf_80_entry59_70_no_chase",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return p.parse_args()


def latest_dir(pattern: str) -> Path | None:
    matches = [p for p in (PROJECT_ROOT / "backtest_outputs").glob(pattern) if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


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


def read_trades(path: Path, dataset: str, source: str, cadence: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "variant" not in df.columns and "model" in df.columns:
        df["variant"] = df["model"]
    if "entry_time" not in df.columns:
        raise ValueError(f"{path} has no entry_time")
    df["dataset"] = dataset
    df["source"] = source
    df["cadence_sec"] = cadence
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    if "win" in df.columns:
        numeric_win = pd.to_numeric(df["win"], errors="coerce")
        if numeric_win.notna().any():
            win = numeric_win.fillna(0.0) > 0.5
        elif df["win"].dtype == bool:
            win = df["win"]
        else:
            win = df["win"].astype(str).str.lower().isin(["true", "1", "yes"])
    elif "settlement" in df.columns and "side" in df.columns:
        win = df["settlement"].astype(str).str.lower().eq(df["side"].astype(str).str.lower())
    else:
        win = pd.to_numeric(df.get("pnl", np.nan), errors="coerce") > 0
    df["win_bool"] = win.fillna(False).astype(bool)
    df = df[df["variant"].isin(MODELS)].copy()
    return df.dropna(subset=["entry_time", "entry_price"]).reset_index(drop=True)


def default_trade_files() -> list[tuple[Path, str, str, int | None]]:
    bt = PROJECT_ROOT / "backtest_outputs"
    files: list[tuple[Path, str, str, int | None]] = []
    chunk_dirs = sorted(bt.glob("predexon_btc1h_2026*_stride30_highconf_visible_*"))
    for d in chunk_dirs:
        p = d / "btc1h_predexon_trades.csv"
        if p.exists():
            files.append((p, d.name.replace("predexon_btc1h_", "pred_"), "predexon", 30))
    split_patterns = [
        ("pred_apr1_14_visible", "predexon", "predexon_btc1h_apr1_14_stride30_highconf_visible_*"),
        ("pred_apr15_30_visible", "predexon", "predexon_btc1h_apr15_30_stride30_highconf_visible_*"),
        ("pred_may3_5_visible", "predexon", "predexon_btc1h_may1_06_stride30_highconf_visible_*"),
    ]
    for dataset, source, pattern in split_patterns:
        d = latest_dir(pattern)
        if d is None:
            d = bt / pattern
        p = d / "btc1h_predexon_trades.csv"
        if p.exists():
            files.append((p, dataset, source, 30))
    for cadence in [1, 5, 10, 15, 20, 30]:
        d = bt / f"btc1h_highconf_ws_fixed_stride{cadence}_20260515_seq"
        if cadence == 30:
            d = bt / "btc1h_highconf_ws_fixed_stride30_20260515"
        p = d / "btc1h_core_ws_counterfactual_trades.csv"
        if p.exists():
            files.append((p, f"ws_may6_12_{cadence}s", "websocket", cadence))
    return files


def pnl_with_stress(df: pd.DataFrame, extra_cents: float) -> pd.Series:
    stressed_entry = (df["entry_price"].astype(float) + extra_cents / 100.0).clip(upper=0.99)
    fees = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    premium = stressed_entry + fees
    return pd.Series(np.where(df["win_bool"].to_numpy(), 1.0 - premium.to_numpy(), -premium.to_numpy()), index=df.index)


def summarize(df: pd.DataFrame, group_cols: list[str], extra_cents: float) -> pd.DataFrame:
    rows = []
    if df.empty:
        return pd.DataFrame()
    pnl_col = pnl_with_stress(df, extra_cents)
    work = df.copy()
    work["pnl_stress"] = pnl_col
    work["premium_stress"] = (work["entry_price"].astype(float) + extra_cents / 100.0).clip(upper=0.99).map(
        lambda x: float(x) + kalshi_fee_dollars(float(x), contracts=1, liquidity="taker")
    )
    for key, g in work.sort_values("entry_time").groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        pnl = g["pnl_stress"].astype(float)
        prem = g["premium_stress"].astype(float)
        row = {col: val for col, val in zip(group_cols, key)}
        row.update(
            {
                "extra_stress_cents": extra_cents,
                "trades": int(len(g)),
                "pnl": round(float(pnl.sum()), 4),
                "return_on_100_pct": round(float(pnl.sum()), 4),
                "premium": round(float(prem.sum()), 4),
                "rop": round(float(pnl.sum() / prem.sum()), 4) if float(prem.sum()) > 0 else 0.0,
                "win_rate": round(float(g["win_bool"].mean()), 4),
                "max_dd": round(max_drawdown(pnl), 4),
                "sharpe": round(sharpe(pnl), 4),
                "first_entry": str(g["entry_time"].min()),
                "last_entry": str(g["entry_time"].max()),
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
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    files = default_trade_files()
    frames = [read_trades(path, dataset, source, cadence) for path, dataset, source, cadence in files]
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
    if trades.empty:
        raise SystemExit("No BTC1H high-confidence trade files found.")
    trades.to_csv(out_dir / "all_input_trades.csv", index=False)

    stress_levels = [0.0, 1.0, 2.0, 3.0]
    split_rows = []
    source_rows = []
    cadence_rows = []
    predexon_trades = trades[trades["source"].eq("predexon")].copy()
    websocket_trades = trades[trades["source"].eq("websocket")].copy()
    for stress in stress_levels:
        split_rows.append(summarize(trades, ["dataset", "variant"], stress))
        source_rows.append(summarize(predexon_trades, ["source", "variant"], stress))
        cadence_rows.append(summarize(websocket_trades, ["cadence_sec", "variant"], stress))
    split_summary = pd.concat(split_rows, ignore_index=True)
    source_summary = pd.concat(source_rows, ignore_index=True)
    cadence_summary = pd.concat(cadence_rows, ignore_index=True) if cadence_rows else pd.DataFrame()

    split_summary.to_csv(out_dir / "split_stress_summary.csv", index=False)
    source_summary.to_csv(out_dir / "source_stress_summary.csv", index=False)
    cadence_summary.to_csv(out_dir / "ws_cadence_stress_summary.csv", index=False)

    base = source_summary[source_summary["extra_stress_cents"].eq(0.0)].copy()
    extra2 = source_summary[source_summary["extra_stress_cents"].eq(2.0)].copy()
    report = [
        "# BTC1H High-Confidence Robustness Audit",
        "",
        "This is an audit of frozen candidate trade files, not a threshold search.",
        "",
        "## Input Files",
        "",
        *[f"- `{path}` as `{dataset}`" for path, dataset, _, _ in files],
        "",
        "## Predexon Source Summary, No Extra Stress",
        "",
        markdown_table(
            base[
                ["source", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe", "premium", "first_entry", "last_entry"]
            ].sort_values(["source", "pnl"], ascending=[True, False])
        ),
        "",
        "## Predexon Source Summary, +2c Extra Adverse Entry Stress",
        "",
        markdown_table(
            extra2[
                ["source", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe", "premium"]
            ].sort_values(["source", "pnl"], ascending=[True, False])
        ),
        "",
        "## Websocket Cadence, No Extra Stress",
        "",
        markdown_table(
            cadence_summary[cadence_summary["extra_stress_cents"].eq(0.0)][
                ["cadence_sec", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe"]
            ].sort_values(["variant", "cadence_sec"])
        ),
        "",
        "## Websocket Cadence, +2c Extra Adverse Entry Stress",
        "",
        markdown_table(
            cadence_summary[cadence_summary["extra_stress_cents"].eq(2.0)][
                ["cadence_sec", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe"]
            ].sort_values(["variant", "cadence_sec"])
        ),
        "",
        "## Interpretation",
        "",
        "- Plain `high_conf_80` is the most cadence-stable websocket candidate.",
        "- `high_conf_80_no_chase` is stronger on Predexon splits but fails at 1s websocket cadence.",
        "- `entry70_no_chase` survives these summaries but adds another tuned cap, so it remains research-only.",
        "- Extra stress tests are adverse-entry approximations; they do not replace live FOK fill validation.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {out_dir}")
    print(base.sort_values(["source", "pnl"], ascending=[True, False]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
