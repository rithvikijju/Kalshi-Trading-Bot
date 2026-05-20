#!/usr/bin/env python3
"""Significance diagnostics for the BTC15M F2 family.

This is intentionally not a parameter search.  It evaluates already-known
F2-style predicates on April Predexon candidates and compares their realized
PnL against a fee-aware efficient-entry null:

    P(win) = stressed_entry + stressed_fee

Under that null a taker buyer has zero expected value after fees.  The Monte
Carlo p-value estimates how often random fair outcomes would produce at least
the observed stressed PnL for the same sequence of entry prices.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_CANDIDATES = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_april_multisplit_search_20260515"
    / "side_candidates_april.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_f2_significance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class StrategySpec:
    name: str
    rationale: str
    fn: Callable[[pd.DataFrame], pd.Series]


SPLITS = [
    ("apr01_04", "2026-04-01T00:00:00Z", "2026-04-05T00:00:00Z"),
    ("apr05_07", "2026-04-05T00:00:00Z", "2026-04-08T00:00:00Z"),
    ("apr08_14", "2026-04-08T00:00:00Z", "2026-04-15T00:00:00Z"),
    ("apr15_30", "2026-04-15T00:00:00Z", "2026-05-01T00:00:00Z"),
    ("apr01_14", "2026-04-01T00:00:00Z", "2026-04-15T00:00:00Z"),
    ("apr01_30", "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"),
]


def utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def fee(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def max_dd(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def load_candidates(path: Path) -> pd.DataFrame:
    cols = [
        "available_at",
        "event_ticker",
        "market_ticker",
        "sequence",
        "side",
        "result",
        "entry_price",
        "entry_fee",
        "premium",
        "visible_qty",
        "win",
        "pnl",
        "side_fair_p",
        "fair_edge_cents",
        "ttl_min",
        "spread_cents",
        "side_btc_3m_bps",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "side_micropressure",
        "side_depth_imbalance",
        "rv_60m",
        "distance_bps",
    ]
    df = pd.read_parquet(path, columns=cols)
    df["available_at"] = utc(df["available_at"])
    for col in cols:
        if col in {"available_at", "event_ticker", "market_ticker", "side", "result", "win"}:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["win"] = df["win"].astype(bool)
    return df.sort_values(["event_ticker", "available_at", "sequence", "side", "market_ticker"]).reset_index(drop=True)


def specs() -> list[StrategySpec]:
    base = lambda d: (
        d["side_fair_p"].ge(0.60)
        & d["fair_edge_cents"].ge(12.0)
        & d["ttl_min"].between(5.0, 12.0)
        & d["spread_cents"].le(2.0)
        & d["entry_price"].between(0.02, 0.60)
        & d["visible_qty"].fillna(0).ge(1)
    )
    return [
        StrategySpec(
            "base_f2",
            "F2 as selected by April multisplit search: fair_p>=60%, edge>=12c, TTL 5-12m, spread<=2c, entry 2-60c.",
            base,
        ),
        StrategySpec(
            "ttl10_12_entry55_q50",
            "Frozen forward-test subset: base F2 plus TTL 10-12m, entry<=55c, visible top qty>=50.",
            lambda d: base(d) & d["ttl_min"].between(10.0, 12.0) & d["entry_price"].le(0.55) & d["visible_qty"].fillna(0).ge(50),
        ),
        StrategySpec(
            "base_f2_ttl6_12",
            "Neighbor selected by April splits before websocket checks: base F2 with TTL 6-12m.",
            lambda d: base(d) & d["ttl_min"].between(6.0, 12.0),
        ),
        StrategySpec(
            "base_f2_entry70",
            "Capacity neighbor: base F2 with entry cap relaxed to 70c.",
            lambda d: (
                d["side_fair_p"].ge(0.60)
                & d["fair_edge_cents"].ge(12.0)
                & d["ttl_min"].between(5.0, 12.0)
                & d["spread_cents"].le(2.0)
                & d["entry_price"].between(0.02, 0.70)
                & d["visible_qty"].fillna(0).ge(1)
            ),
        ),
    ]


def select_trades(df: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    mask = spec.fn(df).fillna(False)
    trades = df.loc[mask].drop_duplicates("event_ticker", keep="first").copy()
    entry = (pd.to_numeric(trades["entry_price"], errors="coerce") + 0.02).clip(upper=0.99)
    stressed_fee = entry.map(fee)
    won = trades["side"].astype(str).str.lower().eq(trades["result"].astype(str).str.lower())
    trades["entry_stress"] = entry
    trades["fee_stress"] = stressed_fee
    trades["premium_stress"] = entry + stressed_fee
    trades["pnl_stress"] = np.where(won, 1.0 - entry - stressed_fee, -entry - stressed_fee)
    trades["win"] = won
    return trades.sort_values("available_at").reset_index(drop=True)


def efficient_null_pvalue(trades: pd.DataFrame, observed_pnl: float, sims: int, rng: np.random.Generator) -> tuple[float, float, float]:
    if trades.empty:
        return 1.0, 0.0, 0.0
    entry = pd.to_numeric(trades["entry_stress"], errors="coerce").to_numpy(dtype=float)
    fees = pd.to_numeric(trades["fee_stress"], errors="coerce").to_numpy(dtype=float)
    breakeven_p = np.clip(entry + fees, 0.0, 1.0)
    # Chunk to avoid allocating huge matrices for larger rule sets.
    ge = 0
    totals: list[np.ndarray] = []
    remaining = sims
    while remaining > 0:
        n = min(remaining, 20_000)
        wins = rng.random((n, len(entry))) < breakeven_p
        pnl = np.where(wins, 1.0 - entry - fees, -entry - fees).sum(axis=1)
        ge += int((pnl >= observed_pnl).sum())
        totals.append(pnl)
        remaining -= n
    all_totals = np.concatenate(totals)
    return (ge + 1.0) / (sims + 1.0), float(np.percentile(all_totals, 5)), float(np.percentile(all_totals, 95))


def summarize(trades: pd.DataFrame, split_name: str, spec: StrategySpec, sims: int, rng: np.random.Generator) -> dict[str, object]:
    if trades.empty:
        return {
            "strategy": spec.name,
            "split": split_name,
            "trades": 0,
            "pnl_stress": 0.0,
            "premium_stress": 0.0,
            "return_on_100": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "null_pvalue": 1.0,
            "null_p05": 0.0,
            "null_p95": 0.0,
            "bonferroni_1237": 1.0,
            "avg_entry": 0.0,
            "avg_ttl": 0.0,
            "avg_edge": 0.0,
            "rationale": spec.rationale,
        }
    pnl = pd.to_numeric(trades["pnl_stress"], errors="coerce").fillna(0.0)
    premium = pd.to_numeric(trades["premium_stress"], errors="coerce").fillna(0.0)
    observed = float(pnl.sum())
    pvalue, null_p05, null_p95 = efficient_null_pvalue(trades, observed, sims, rng)
    return {
        "strategy": spec.name,
        "split": split_name,
        "trades": int(len(trades)),
        "pnl_stress": round(observed, 4),
        "premium_stress": round(float(premium.sum()), 4),
        "return_on_100": round(observed / 100.0, 4),
        "rop": round(float(observed / premium.sum()), 4) if float(premium.sum()) > 0 else 0.0,
        "win_rate": round(float(trades["win"].mean()), 4),
        "max_dd": round(max_dd(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "null_pvalue": round(float(pvalue), 6),
        "null_p05": round(null_p05, 4),
        "null_p95": round(null_p95, 4),
        "bonferroni_1237": round(min(1.0, float(pvalue) * 1237.0), 6),
        "avg_entry": round(float(pd.to_numeric(trades["entry_price"], errors="coerce").mean()), 4),
        "avg_ttl": round(float(pd.to_numeric(trades["ttl_min"], errors="coerce").mean()), 4),
        "avg_edge": round(float(pd.to_numeric(trades["fair_edge_cents"], errors="coerce").mean()), 4),
        "rationale": spec.rationale,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sims", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260515)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    df = load_candidates(args.candidates)
    rng = np.random.default_rng(args.seed)
    rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    for spec in specs():
        all_trades = select_trades(df, spec)
        all_trades["strategy"] = spec.name
        trade_frames.append(all_trades)
        for split_name, start, end in SPLITS:
            s = pd.Timestamp(start)
            e = pd.Timestamp(end)
            sub = all_trades[all_trades["available_at"].ge(s) & all_trades["available_at"].lt(e)].copy()
            rows.append(summarize(sub, split_name, spec, args.sims, rng))

    summary = pd.DataFrame(rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    summary.to_csv(args.out_dir / "summary.csv", index=False)
    trades.to_parquet(args.out_dir / "trades.parquet", index=False, compression="zstd")
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidates": str(args.candidates),
        "sims": int(args.sims),
        "seed": int(args.seed),
        "null": "For each trade, simulate Bernoulli win with p=stressed_entry+stressed_fee; PnL includes stressed entry and taker fee.",
        "bonferroni_rules": 1237,
        "strategies": [{"name": s.name, "rationale": s.rationale} for s in specs()],
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    view = summary[summary["split"].isin(["apr01_14", "apr15_30", "apr01_30"])][
        [
            "strategy",
            "split",
            "trades",
            "pnl_stress",
            "win_rate",
            "max_dd",
            "sharpe",
            "null_pvalue",
            "bonferroni_1237",
        ]
    ]
    print(view.to_string(index=False), flush=True)
    print(f"wrote {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
