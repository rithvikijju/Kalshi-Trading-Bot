#!/usr/bin/env python3
"""Validate Apr 1-4-derived BTC15M fee-edge candidates on April Predexon data.

This script starts from the already prepared April Predexon BTC15M quote cache
so it does not re-query Predexon.  It reconstructs the causal features required
for `side_fair_p` and evaluates fixed rules without tuning on validation spans.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_btc15m_predexon_april_execution import (  # noqa: E402
    DEFAULT_MARKETS,
    DEFAULT_SPOT,
    add_mid_changes,
    load_metadata,
    load_spot,
    merge_btc,
    normalize_quotes,
)
from scripts.research_btc15m_apr1_7_deepdive import add_btc_state, add_spread_changes, make_side_candidates  # noqa: E402


DEFAULT_PREPARED = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "predexon_btc15m_april_execution_20260514_182852"
    / "prepared_btc15m_april_quotes.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_fee_edge_april_validation_latest"
APR_START = pd.Timestamp("2026-04-01T00:00:00Z")
APR_END = pd.Timestamp("2026-05-01T00:00:00Z")


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def max_dd(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def first_per_event(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    order = ["event_ticker", "available_at", "timestamp_ms", "fee_edge", "sequence", "side"]
    return (
        df.sort_values(order, ascending=[True, True, True, False, True, True])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def split_name(close_time: pd.Series) -> pd.Series:
    ts = pd.to_datetime(close_time, utc=True, errors="coerce")
    return np.select(
        [
            ts < pd.Timestamp("2026-04-05T00:00:00Z"),
            ts < pd.Timestamp("2026-04-08T00:00:00Z"),
            ts < pd.Timestamp("2026-04-11T00:00:00Z"),
            ts < pd.Timestamp("2026-04-21T00:00:00Z"),
            ts < APR_END,
        ],
        ["study_apr01_04", "holdout_apr05_07", "post_apr08_10", "post_apr11_20", "post_apr21_30"],
        default="outside",
    )


def add_two_cent_stress(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    if out.empty:
        out["pnl_2c"] = []
        return out
    entry = (pd.to_numeric(out["entry_price"], errors="coerce") + 0.02).clip(upper=0.99)
    fee = entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    won = out["result"].astype(str).str.lower().eq(out["side"].astype(str).str.lower())
    out["pnl_2c"] = np.where(won, 1.0 - entry - fee, -entry - fee)
    return out


def metrics(trades: pd.DataFrame, pnl_col: str) -> dict[str, float | int]:
    pnl = pd.to_numeric(trades[pnl_col], errors="coerce") if len(trades) else pd.Series(dtype=float)
    premium = pd.to_numeric(trades["premium"], errors="coerce") if len(trades) else pd.Series(dtype=float)
    return {
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4) if len(trades) else 0.0,
        "return_on_100": round(float(pnl.sum()), 4) if len(trades) else 0.0,
        "premium": round(float(premium.sum()), 4) if len(trades) else 0.0,
        "rop": round(float(pnl.sum() / premium.sum()), 4) if float(premium.sum()) > 0 else 0.0,
        "win_rate": round(float(pd.to_numeric(trades.get("win", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "max_dd": round(max_dd(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(trades.get("entry_price", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "avg_fee_edge": round(float(pd.to_numeric(trades.get("fee_edge", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "avg_fair_p": round(float(pd.to_numeric(trades.get("side_fair_p", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
    }


def prepare_candidates(args: argparse.Namespace) -> pd.DataFrame:
    cached = args.out_dir / "april_side_candidates_fee_features.parquet"
    if args.reuse_features and cached.exists():
        print(f"using cached candidates {cached}", flush=True)
        return pd.read_parquet(cached)

    print(f"loading prepared quote cache {args.prepared}", flush=True)
    raw = pd.read_parquet(args.prepared)
    meta = load_metadata(args.markets)
    spot = load_spot(args.spot)
    print("normalizing quotes", flush=True)
    q = normalize_quotes(raw, meta)
    q = q[(q["close_time"] >= APR_START) & (q["close_time"] < APR_END) & (q["available_at"] < q["close_time"])].copy()
    before = len(q)
    q = (
        q.sort_values(["event_ticker", "market_ticker", "available_at", "sequence"], na_position="first")
        .drop_duplicates(["event_ticker", "market_ticker", "available_at"], keep="last")
        .reset_index(drop=True)
    )
    print(f"deduped rows={before-len(q):,}; feature rows={len(q):,}", flush=True)
    print("adding causal quote/BTC/fair-value features", flush=True)
    q = add_mid_changes(q, [0.5, 1.0, 2.0, 3.0, 5.0])
    q = add_spread_changes(q, [1.0])
    q = merge_btc(q, spot, [1, 3, 5])
    q = add_btc_state(q, spot)
    q["microprice"] = (
        q["yes_ask"].astype(float) * q["yes_bid_qty"].astype(float)
        + q["yes_bid"].astype(float) * q["yes_ask_qty"].astype(float)
    ) / (q["yes_bid_qty"].astype(float) + q["yes_ask_qty"].astype(float)).replace(0, np.nan)
    q["micropressure"] = q["microprice"] - q["yes_mid"]
    c = make_side_candidates(q)
    c["eval_split"] = split_name(c["close_time"])
    c["day"] = pd.to_datetime(c["close_time"], utc=True).dt.strftime("%Y-%m-%d")
    c["fee_edge"] = pd.to_numeric(c["side_fair_p"], errors="coerce") - pd.to_numeric(c["entry_price"], errors="coerce") - pd.to_numeric(c["entry_fee"], errors="coerce")
    c["side_distance_bps"] = np.where(c["side"].astype(str).str.lower().eq("yes"), c["distance_bps"], -c["distance_bps"])
    c["rv_ratio_15_60"] = pd.to_numeric(c["rv_15m"], errors="coerce") / pd.to_numeric(c["rv_60m"], errors="coerce").replace(0, np.nan)
    c.to_parquet(cached, index=False, compression="zstd")
    print(f"wrote cached candidates {cached} rows={len(c):,}", flush=True)
    return c


def rules() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    return {
        "P_feeedge_60_10": lambda d: d["side_fair_p"].ge(0.60) & d["fee_edge"].ge(0.10),
        "P_feeedge_90_05": lambda d: d["side_fair_p"].ge(0.90) & d["fee_edge"].ge(0.05),
        "P_fair95": lambda d: d["side_fair_p"].ge(0.95),
        "P_cheap_dist": lambda d: d["entry_price"].between(0.05, 0.20, inclusive="left") & d["side_distance_bps"].ge(-25),
        "Q_dist_rv_bounded_calib": lambda d: (
            d["spread_cents"].le(2)
            & d["visible_qty"].ge(1)
            & d["ttl_min"].between(0, 15)
            & d["entry_price"].between(0.08, 0.80)
            & d["side_fair_p"].between(0.60, 0.95)
            & d["side_distance_bps"].between(5, 40)
            & d["rv_ratio_15_60"].le(1.0)
            & d["fair_edge_cents"].between(-10, 10)
        ),
        "current_lowdd_like": lambda d: (
            d["ttl_min"].between(4, 5)
            & d["spread_cents"].le(2)
            & d["entry_price"].between(0.05, 0.80)
            & d["rr"].ge(0.33)
            & d["side_mid_chg_2m"].ge(0.125)
            & d["side_btc_3m_bps"].ge(0)
            & d["side_mid_chg_3m"].abs().le(0.35)
            & d["lookback_age_2_0m_sec"].between(115, 360)
            & d["lookback_age_3_0m_sec"].between(170, 420)
            & d["btc_lookback_age_3m_sec"].between(170, 420)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    parser.add_argument("--spot", type=Path, default=DEFAULT_SPOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--reuse-features", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    c = prepare_candidates(args)

    summary_rows = []
    daily_rows = []
    trade_frames = []
    for name, selector in rules().items():
        selected = first_per_event(c[selector(c)].copy())
        selected = add_two_cent_stress(selected)
        selected["strategy"] = name
        trade_frames.append(selected)
        for split in ["study_apr01_04", "holdout_apr05_07", "post_apr08_10", "post_apr11_20", "post_apr21_30", "all"]:
            sample = selected if split == "all" else selected[selected["eval_split"].eq(split)]
            for pnl_col in ["pnl", "pnl_2c"]:
                row = {"strategy": name, "split": split, "pnl_model": pnl_col}
                row.update(metrics(sample, pnl_col))
                summary_rows.append(row)
        for (split, day), g in selected.groupby(["eval_split", "day"], dropna=False):
            row = {"strategy": name, "split": split, "day": day, "pnl_model": "pnl_2c"}
            row.update(metrics(g, "pnl_2c"))
            daily_rows.append(row)

    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    daily = pd.DataFrame(daily_rows)
    trades.to_parquet(args.out_dir / "fee_edge_april_trades.parquet", index=False, compression="zstd")
    summary.to_csv(args.out_dir / "fee_edge_april_summary.csv", index=False)
    daily.to_csv(args.out_dir / "fee_edge_april_daily.csv", index=False)
    print("wrote", args.out_dir, flush=True)
    print(summary[summary["pnl_model"].eq("pnl_2c")].sort_values(["strategy", "split"]).to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
