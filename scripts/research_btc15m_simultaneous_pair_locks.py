#!/usr/bin/env python3
"""Research simultaneous BTC15M YES+NO pair locks.

This is a non-directional structural test: if YES ask + NO ask + taker fees is
below $1 at the same observed book timestamp, buying one of each contract should
lock payout $1 if both FOK legs fill.  The replay is conservative:

* same timestamp/book row only, not a later second leg
* one pair per event, first qualifying timestamp
* configurable per-leg adverse stress and minimum locked profit
* top-of-book visible quantity cap reported
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_pair_locks_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class PairRule:
    name: str
    ttl_min: float
    ttl_max: float
    min_qty: float
    stress_cents_per_leg: float
    min_lock_profit_cents: float


RULES = [
    PairRule("pair_ttl2_8_q1_stress0_profit0", 2, 8, 1, 0, 0),
    PairRule("pair_ttl2_8_q1_stress1_profit0", 2, 8, 1, 1, 0),
    PairRule("pair_ttl2_8_q1_stress1_profit1", 2, 8, 1, 1, 1),
    PairRule("pair_ttl2_8_q10_stress1_profit1", 2, 8, 10, 1, 1),
    PairRule("pair_ttl2_8_q50_stress1_profit1", 2, 8, 50, 1, 1),
    PairRule("pair_ttl4_8_q1_stress1_profit1", 4, 8, 1, 1, 1),
    PairRule("pair_ttl4_8_q10_stress1_profit1", 4, 8, 10, 1, 1),
    PairRule("pair_ttl2_8_q1_stress2_profit1", 2, 8, 1, 2, 1),
    PairRule("pair_ttl2_8_q1_stress2_profit2", 2, 8, 1, 2, 2),
    PairRule("pair_ttl4_8_q10_stress2_profit2", 4, 8, 10, 2, 2),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pred-train", type=Path, required=True)
    p.add_argument("--pred-val", type=Path, required=True)
    p.add_argument("--pred-may", type=Path, required=True)
    p.add_argument("--pred-external", type=Path, required=True)
    p.add_argument("--live-features", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def load_features(path: Path, split: str) -> pd.DataFrame:
    cols = [
        "event_ticker",
        "market_ticker",
        "yes_ask",
        "yes_ask_qty",
        "no_ask",
        "no_ask_qty",
        "ttl_min",
        "spread_cents",
    ]
    for ts_col in ["available_at", "timestamp_utc", "received_at_utc"]:
        try:
            sample_cols = pd.read_parquet(path, columns=[ts_col])
            cols.append(ts_col)
            break
        except Exception:
            continue
    df = pd.read_parquet(path, columns=list(dict.fromkeys(cols)))
    if "available_at" in df.columns:
        df["ts"] = pd.to_datetime(df["available_at"], utc=True)
    elif "timestamp_utc" in df.columns:
        df["ts"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    else:
        df["ts"] = pd.to_datetime(df["received_at_utc"], utc=True)
    df["split"] = split
    for col in ["yes_ask", "yes_ask_qty", "no_ask", "no_ask_qty", "ttl_min", "spread_cents"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["ts", "event_ticker", "yes_ask", "no_ask", "yes_ask_qty", "no_ask_qty", "ttl_min"]).sort_values("ts")


def fee(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def eval_rule(df: pd.DataFrame, rule: PairRule, split: str) -> tuple[dict[str, object], pd.DataFrame]:
    y = (df["yes_ask"] + rule.stress_cents_per_leg / 100.0).clip(upper=0.99)
    n = (df["no_ask"] + rule.stress_cents_per_leg / 100.0).clip(upper=0.99)
    fees = y.map(fee) + n.map(fee)
    cost = y + n + fees
    profit = 1.0 - cost
    qty = np.minimum(df["yes_ask_qty"], df["no_ask_qty"])
    mask = (
        df["ttl_min"].between(rule.ttl_min, rule.ttl_max, inclusive="both")
        & qty.ge(rule.min_qty)
        & profit.ge(rule.min_lock_profit_cents / 100.0)
    )
    hits = df.loc[mask].copy()
    if hits.empty:
        row = {"split": split, "rule": rule.name, "pairs": 0, "pnl": 0.0, "min_profit": 0.0, "avg_profit": 0.0, "max_dd": 0.0, "first": "", "last": ""}
        return row, hits
    hits["yes_entry_stress"] = y.loc[hits.index].to_numpy()
    hits["no_entry_stress"] = n.loc[hits.index].to_numpy()
    hits["fee_total"] = fees.loc[hits.index].to_numpy()
    hits["pair_cost"] = cost.loc[hits.index].to_numpy()
    hits["pnl"] = profit.loc[hits.index].to_numpy()
    hits["visible_pair_qty"] = qty.loc[hits.index].to_numpy()
    trades = hits.sort_values(["event_ticker", "ts"]).drop_duplicates("event_ticker", keep="first").sort_values("ts").copy()
    eq = trades["pnl"].cumsum()
    row = {
        "split": split,
        "rule": rule.name,
        "pairs": int(len(trades)),
        "pnl": round(float(trades["pnl"].sum()), 4),
        "min_profit": round(float(trades["pnl"].min()), 4),
        "avg_profit": round(float(trades["pnl"].mean()), 4),
        "max_dd": round(float((eq - eq.cummax()).min()), 4),
        "avg_visible_pair_qty": round(float(trades["visible_pair_qty"].mean()), 2),
        "first": str(trades["ts"].min()),
        "last": str(trades["ts"].max()),
    }
    trades["rule"] = rule.name
    trades["split"] = split
    return row, trades


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    datasets = {
        "train_apr1_14": load_features(args.pred_train, "train_apr1_14"),
        "val_apr15_30": load_features(args.pred_val, "val_apr15_30"),
        "val_may1_12": load_features(args.pred_may, "val_may1_12"),
        "external_jan": load_features(args.pred_external, "external_jan"),
        "live_ws": load_features(args.live_features, "live_ws"),
    }
    rows = []
    frames = []
    for split, df in datasets.items():
        for rule in RULES:
            row, trades = eval_rule(df, rule, split)
            rows.append(row)
            if not trades.empty:
                frames.append(trades)
    summary = pd.DataFrame(rows)
    wide = summary.pivot(index="rule", columns="split", values=["pairs", "pnl", "min_profit", "avg_profit", "max_dd", "avg_visible_pair_qty"])
    wide.columns = [f"{metric}_{split}" for metric, split in wide.columns]
    wide = wide.reset_index()
    wide = wide.merge(pd.DataFrame([asdict(r) for r in RULES]), left_on="rule", right_on="name", how="left").drop(columns=["name"])
    wide.to_csv(args.out_dir / "pair_lock_summary_wide.csv", index=False)
    summary.to_csv(args.out_dir / "pair_lock_summary_long.csv", index=False)
    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(args.out_dir / "pair_lock_trades.parquet", index=False)
    (args.out_dir / "metadata.json").write_text(json.dumps({k: str(v) for k, v in vars(args).items()}, indent=2), encoding="utf-8")
    report = "# BTC15M Simultaneous Pair Locks\n\n" + wide.to_string(index=False) + "\n"
    (args.out_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(wide.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
