#!/usr/bin/env python3
"""Conservative derived audit for BTC1H entry59-70 high-confidence candidate.

This script does not replace a full causal replay.  It takes already-generated
`high_conf_80_entry70_no_chase` trades and keeps only decisions whose entry was
inside the proposed 59c-70c band.  That is conservative because a full replay
could find a later in-band signal in an event where the first entry70 signal was
below 59c.
"""

from __future__ import annotations

import argparse
import json
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


DEFAULT_INPUT = (
    PROJECT_ROOT / "backtest_outputs" / "btc1h_highconf_robustness_20260516_012255" / "all_input_trades.csv"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc1h_entry59_70_derived_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
BASE_VARIANT = "high_conf_80_entry70_no_chase"
DERIVED_VARIANT = "high_conf_80_entry59_70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-trades", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-entry", type=float, default=0.59)
    parser.add_argument("--max-entry", type=float, default=0.70)
    parser.add_argument("--stress-cents", nargs="+", type=float, default=[0.0, 1.0, 2.0, 3.0, 4.0])
    return parser.parse_args()


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


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def normalize_win(df: pd.DataFrame) -> pd.Series:
    if "win_bool" in df.columns:
        return df["win_bool"].astype(str).str.lower().isin({"true", "1", "yes"})
    if "win" in df.columns:
        return df["win"].astype(str).str.lower().isin({"true", "1", "yes"})
    return pd.to_numeric(df.get("pnl", np.nan), errors="coerce").fillna(0.0) > 0


def pnl_with_stress(df: pd.DataFrame, stress_cents: float) -> tuple[pd.Series, pd.Series]:
    stressed_entry = (pd.to_numeric(df["entry_price"], errors="coerce") + stress_cents / 100.0).clip(upper=0.99)
    fees = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    premium = stressed_entry + fees
    win = normalize_win(df)
    pnl = pd.Series(np.where(win.to_numpy(), 1.0 - premium.to_numpy(), -premium.to_numpy()), index=df.index)
    return pnl, premium


def summarize(df: pd.DataFrame, group_cols: list[str], stress_cents: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy().sort_values("entry_time")
    pnl, premium = pnl_with_stress(work, stress_cents)
    work["pnl_stress"] = pnl
    work["premium_stress"] = premium
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
                "win_rate": round(float(normalize_win(group).mean()), 4),
                "max_dd": round(max_drawdown(gpnl), 4),
                "sharpe": round(sharpe(gpnl), 4),
                "first_entry": str(group["entry_time"].min()),
                "last_entry": str(group["entry_time"].max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(args.input_trades)
    raw["entry_time"] = pd.to_datetime(raw["entry_time"], utc=True, errors="coerce")
    raw["entry_price"] = pd.to_numeric(raw["entry_price"], errors="coerce")
    base = raw[raw["variant"].astype(str).eq(BASE_VARIANT)].copy()
    derived = base[base["entry_price"].between(args.min_entry, args.max_entry, inclusive="both")].copy()
    derived["variant"] = DERIVED_VARIANT
    derived["model"] = DERIVED_VARIANT
    derived.to_csv(args.out_dir / "derived_trades.csv", index=False)

    split_frames = []
    source_frames = []
    cadence_frames = []
    for stress in args.stress_cents:
        split_frames.append(summarize(derived, ["source", "dataset", "variant"], stress))
        source_frames.append(summarize(derived, ["source", "variant"], stress))
        ws = derived[derived["source"].astype(str).eq("websocket")].copy()
        if not ws.empty:
            cadence_frames.append(summarize(ws, ["cadence_sec", "variant"], stress))
    split_summary = pd.concat([f for f in split_frames if not f.empty], ignore_index=True) if split_frames else pd.DataFrame()
    source_summary = pd.concat([f for f in source_frames if not f.empty], ignore_index=True) if source_frames else pd.DataFrame()
    cadence_summary = pd.concat([f for f in cadence_frames if not f.empty], ignore_index=True) if cadence_frames else pd.DataFrame()
    split_summary.to_csv(args.out_dir / "split_stress_summary.csv", index=False)
    source_summary.to_csv(args.out_dir / "source_stress_summary.csv", index=False)
    cadence_summary.to_csv(args.out_dir / "ws_cadence_stress_summary.csv", index=False)

    stress2_source = source_summary[source_summary["extra_stress_cents"].eq(2.0)].copy()
    stress2_split = split_summary[split_summary["extra_stress_cents"].eq(2.0)].copy()
    stress2_cadence = cadence_summary[cadence_summary["extra_stress_cents"].eq(2.0)].copy()
    report = [
        "# BTC1H Entry59-70 Derived Audit",
        "",
        "This is a conservative derived audit, not a full causal replay.",
        "",
        f"Input trades: `{args.input_trades}`",
        f"Base variant filtered: `{BASE_VARIANT}`",
        f"Derived variant: `{DERIVED_VARIANT}`",
        f"Entry band: `{args.min_entry:.2f}` to `{args.max_entry:.2f}`",
        "",
        "## Source Summary, +2c Stress",
        "",
        markdown_table(stress2_source[["source", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe", "premium"]]),
        "",
        "## Split Summary, +2c Stress",
        "",
        markdown_table(
            stress2_split[
                ["source", "dataset", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe", "first_entry", "last_entry"]
            ].sort_values(["source", "dataset"])
        ),
        "",
        "## Websocket Cadence Summary, +2c Stress",
        "",
        markdown_table(
            stress2_cadence[["cadence_sec", "variant", "trades", "pnl", "win_rate", "max_dd", "sharpe"]].sort_values(
                "cadence_sec"
            )
        ),
        "",
        "## Caveat",
        "",
        "- If a filtered-out cheap first signal occurred in an event, this derived audit does not search for a later in-band signal.",
        "- Promotion still requires a full replay and post-freeze paper shadow evidence.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    meta = {
        "input_trades": str(args.input_trades),
        "base_variant": BASE_VARIANT,
        "derived_variant": DERIVED_VARIANT,
        "min_entry": args.min_entry,
        "max_entry": args.max_entry,
        "raw_base_trades": int(len(base)),
        "derived_trades": int(len(derived)),
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(stress2_source.to_string(index=False))
    print(stress2_cadence.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
