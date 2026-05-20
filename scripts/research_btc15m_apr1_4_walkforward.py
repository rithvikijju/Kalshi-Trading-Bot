#!/usr/bin/env python3
"""Apr 1-4-only walk-forward stress for BTC15M candidate families.

This script never reads holdout rows.  For each family it generates a compact
parameter grid, selects parameters on three study days, and reports performance
on the left-out study day.  This is not final validation; it is a guard against
choosing a brittle Apr 1-4 threshold before the real Apr 5-7 holdout.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_INPUT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_apr1_7_deepdive_20260514_185319"
    / "side_candidates.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_apr1_4_walkforward_latest"
STUDY_START = pd.Timestamp("2026-04-01T00:00:00Z")
STUDY_END = pd.Timestamp("2026-04-05T00:00:00Z")


@dataclass(frozen=True)
class GridRule:
    family: str
    name: str
    selector: Callable[[pd.DataFrame], pd.Series]


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


def first_event(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    return (
        df.loc[mask]
        .sort_values(["event_ticker", "available_at", "side", "market_ticker"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def metrics(trades: pd.DataFrame) -> dict[str, float | int]:
    pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce")
    prem = pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce")
    return {
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4) if len(trades) else 0.0,
        "premium": round(float(prem.sum()), 4) if len(trades) else 0.0,
        "rop": round(float(pnl.sum() / prem.sum()), 4) if float(prem.sum()) > 0 else 0.0,
        "win_rate": round(float(pd.to_numeric(trades.get("win", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "max_dd": round(max_dd(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
    }


def score(trades: pd.DataFrame) -> float:
    if len(trades) < 20:
        return -1e9
    day_pnl = trades.groupby("day")["pnl"].sum()
    positive_days = int((day_pnl > 0).sum())
    worst = float(day_pnl.min()) if len(day_pnl) else -999.0
    m = metrics(trades)
    return float(m["pnl"]) + 2.0 * float(m["sharpe"]) + 0.75 * positive_days + 0.5 * worst


def age_ok(d: pd.DataFrame) -> pd.Series:
    return (
        pd.to_numeric(d["lookback_age_0_5m_sec"], errors="coerce").le(120)
        & pd.to_numeric(d["lookback_age_1_0m_sec"], errors="coerce").le(180)
        & pd.to_numeric(d["lookback_age_3_0m_sec"], errors="coerce").le(300)
    )


def build_grids() -> Iterable[GridRule]:
    for ttl_lo in [6, 7, 8, 9]:
        for btc5 in [3, 5, 7, 9]:
            for mid1 in [-0.08, -0.05, -0.02, 0.0]:
                for entry_hi in [0.70, 0.80, 0.90]:
                    name = f"A_ttl{ttl_lo}_btc{btc5}_mid{mid1}_entry{entry_hi}"
                    yield GridRule(
                        "A_btc5_continuation",
                        name,
                        lambda d, ttl_lo=ttl_lo, btc5=btc5, mid1=mid1, entry_hi=entry_hi: (
                            d["spread_cents"].le(2)
                            & d["entry_price"].between(0.05, entry_hi)
                            & d["visible_qty"].ge(1)
                            & d["ttl_min"].between(ttl_lo, 15)
                            & d["side_btc_5m_bps"].ge(btc5)
                            & d["side_mid_chg_1m"].ge(mid1)
                        ),
                    )
    for ttl_lo, ttl_hi in [(2, 8), (4, 8), (5, 8), (6, 8)]:
        for mp in [0.002, 0.003, 0.005, 0.008]:
            for depth in [-0.5, 0.0, 0.1, 0.3, 0.5]:
                for entry_lo in [0.30, 0.40, 0.50]:
                    name = f"B_ttl{ttl_lo}_{ttl_hi}_mp{mp}_depth{depth}_entrylo{entry_lo}"
                    yield GridRule(
                        "B_micropressure_depth",
                        name,
                        lambda d, ttl_lo=ttl_lo, ttl_hi=ttl_hi, mp=mp, depth=depth, entry_lo=entry_lo: (
                            d["spread_cents"].le(2)
                            & d["entry_price"].between(entry_lo, 0.95)
                            & d["visible_qty"].ge(1)
                            & d["ttl_min"].between(ttl_lo, ttl_hi)
                            & d["side_depth_imbalance"].ge(depth)
                            & d["side_micropressure"].ge(mp)
                        ),
                    )
    for ttl_hi in [4, 5, 6, 8]:
        for mid3 in [-0.08, -0.05, -0.03]:
            for btc1 in [-1, -2, -3, -5]:
                for entry_hi in [0.60, 0.70, 0.80]:
                    name = f"C_ttl0_{ttl_hi}_mid{mid3}_btc{btc1}_entry{entry_hi}"
                    yield GridRule(
                        "C_late_reversal",
                        name,
                        lambda d, ttl_hi=ttl_hi, mid3=mid3, btc1=btc1, entry_hi=entry_hi: (
                            d["spread_cents"].le(2)
                            & d["entry_price"].between(0.05, entry_hi)
                            & d["visible_qty"].ge(1)
                            & d["ttl_min"].between(0, ttl_hi)
                            & d["side_mid_chg_3m"].le(mid3)
                            & d["side_btc_1m_bps"].le(btc1)
                        ),
                    )
    for spread in [1, 2]:
        for entry_hi in [0.55, 0.60, 0.70]:
            for mid3 in [-0.08, -0.05, -0.03]:
                for mid05 in [0.03, 0.05, 0.07]:
                    name = f"G_vrev_s{spread}_e{entry_hi}_m3{mid3}_m05{mid05}"
                    yield GridRule(
                        "G_v_reversal",
                        name,
                        lambda d, spread=spread, entry_hi=entry_hi, mid3=mid3, mid05=mid05: (
                            d["ttl_min"].between(2, 8)
                            & d["spread_cents"].le(spread)
                            & d["entry_price"].between(0.10, entry_hi)
                            & d["visible_qty"].ge(50)
                            & d["rr"].ge(0.50)
                            & age_ok(d)
                            & d["side_mid_chg_3m"].le(mid3)
                            & d["side_mid_chg_1m"].ge(0.02)
                            & d["side_mid_chg_05m"].ge(mid05)
                        ),
                    )
    for spread in [1, 2]:
        for entry_hi in [0.60, 0.70, 0.80]:
            for mid3 in [0.15, 0.20, 0.25]:
                for flat in [0.02, 0.03, 0.05]:
                    name = f"H_pull_s{spread}_e{entry_hi}_m3{mid3}_flat{flat}"
                    yield GridRule(
                        "H_pullback_continuation",
                        name,
                        lambda d, spread=spread, entry_hi=entry_hi, mid3=mid3, flat=flat: (
                            d["ttl_min"].between(2, 8)
                            & d["spread_cents"].le(spread)
                            & d["entry_price"].between(0.05, entry_hi)
                            & d["visible_qty"].ge(10)
                            & d["rr"].ge(0.25)
                            & age_ok(d)
                            & d["side_mid_chg_3m"].ge(mid3)
                            & d["side_mid_chg_05m"].between(-flat, flat)
                        ),
                    )
    for entry_lo, entry_hi in [(0.10, 0.60), (0.20, 0.70), (0.40, 0.80)]:
        for fair_edge in [0, 5, 10, 20]:
            for fair_lo in [0.15, 0.20, 0.50, 0.65, 0.70, 0.90]:
                for rv_cap in [0.35, 0.45, 0.60, 0.80]:
                    name = f"I_fair_e{entry_lo}_{entry_hi}_edge{fair_edge}_p{fair_lo}_rv{rv_cap}"
                    yield GridRule(
                        "I_fair_calibration",
                        name,
                        lambda d, entry_lo=entry_lo, entry_hi=entry_hi, fair_edge=fair_edge, fair_lo=fair_lo, rv_cap=rv_cap: (
                            d["ttl_min"].between(2, 8)
                            & d["spread_cents"].le(2)
                            & d["entry_price"].between(entry_lo, entry_hi)
                            & d["visible_qty"].ge(10)
                            & d["rr"].ge(0.25)
                            & d["fair_edge_cents"].ge(fair_edge)
                            & d["side_fair_p"].between(fair_lo, 0.99)
                            & pd.to_numeric(d["rv_15m"], errors="coerce").le(rv_cap)
                        ),
                    )
    for ttl_lo, ttl_hi in [(2, 6), (2, 8), (2, 10), (4, 8), (4, 10)]:
        for mid5 in [-0.30, -0.20, -0.15, -0.10]:
            for mid05 in [0.04, 0.06, 0.08, 0.10]:
                for entry_hi in [0.60, 0.80, 0.90]:
                    name = f"L_wash_ttl{ttl_lo}_{ttl_hi}_m5{mid5}_m05{mid05}_e{entry_hi}"
                    yield GridRule(
                        "L_washout_snapback",
                        name,
                        lambda d, ttl_lo=ttl_lo, ttl_hi=ttl_hi, mid5=mid5, mid05=mid05, entry_hi=entry_hi: (
                            d["spread_cents"].le(2)
                            & d["entry_price"].between(0.05, entry_hi)
                            & d["visible_qty"].ge(1)
                            & d["ttl_min"].gt(ttl_lo)
                            & d["ttl_min"].le(ttl_hi)
                            & d["side_mid_chg_5m"].le(mid5)
                            & d["side_mid_chg_05m"].ge(mid05)
                        ),
                    )
    for ttl_hi in [4, 5, 6]:
        for entry_lo in [0.65, 0.70, 0.75, 0.80]:
            for mid2 in [0.10, 0.15, 0.20]:
                for low, high in [(-0.10, 0.02), (-0.08, 0.02), (-0.05, 0.03)]:
                    for qs in [1, 2, 4]:
                        name = f"M_highpull_ttl2_{ttl_hi}_e{entry_lo}_m2{mid2}_rng{low}_{high}_qs{qs}"
                        yield GridRule(
                            "M_high_entry_pullback",
                            name,
                            lambda d, ttl_hi=ttl_hi, entry_lo=entry_lo, mid2=mid2, low=low, high=high, qs=qs: (
                                d["spread_cents"].le(2)
                                & d["entry_price"].between(entry_lo, 0.95)
                                & d["visible_qty"].ge(1)
                                & d["ttl_min"].gt(2)
                                & d["ttl_min"].le(ttl_hi)
                                & d["side_mid_chg_2m"].ge(mid2)
                                & d["side_mid_chg_05m"].between(low, high)
                                & pd.to_numeric(d["quote_speed_cents"], errors="coerce").le(qs)
                            ),
                        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    df = pd.read_parquet(args.input)
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True, errors="coerce")
    df = df[df["split"].eq("study")].copy()
    if "side_mid_chg_5m" not in df.columns and "yes_mid_chg_5_0m" in df.columns:
        df["side_mid_chg_5m"] = np.where(
            df["side"].astype(str).str.lower().eq("yes"),
            pd.to_numeric(df["yes_mid_chg_5_0m"], errors="coerce"),
            -pd.to_numeric(df["yes_mid_chg_5_0m"], errors="coerce"),
        )
    if df["close_time"].min() < STUDY_START or df["close_time"].max() >= STUDY_END:
        raise SystemExit("study split leak detected")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rules = list(build_grids())
    all_rows = []
    folds = []
    days = sorted(df["day"].dropna().unique().tolist())
    for rule in rules:
        trades = first_event(df, rule.selector(df))
        m = metrics(trades)
        m.update({"family": rule.family, "name": rule.name, "positive_days": int((trades.groupby("day")["pnl"].sum() > 0).sum()) if len(trades) else 0})
        all_rows.append(m)
    all_df = pd.DataFrame(all_rows).sort_values(["family", "sharpe", "pnl"], ascending=[True, False, False])
    all_df.to_csv(args.out_dir / "all_grid_study.csv", index=False)

    for family in sorted(all_df["family"].unique()):
        fam_rules = [r for r in rules if r.family == family]
        for leftout in days:
            train = df[df["day"].ne(leftout)]
            test = df[df["day"].eq(leftout)]
            scored = []
            for rule in fam_rules:
                tr = first_event(train, rule.selector(train))
                scored.append((score(tr), rule.name, rule))
            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0][2]
            train_trades = first_event(train, best.selector(train))
            test_trades = first_event(test, best.selector(test))
            row = {"family": family, "leftout_day": leftout, "selected": best.name, "train_score": round(scored[0][0], 4)}
            row.update({f"train_{k}": v for k, v in metrics(train_trades).items()})
            row.update({f"test_{k}": v for k, v in metrics(test_trades).items()})
            folds.append(row)
    folds_df = pd.DataFrame(folds)
    folds_df.to_csv(args.out_dir / "leave_one_day_out.csv", index=False)

    summary = folds_df.groupby("family", as_index=False).agg(
        folds=("leftout_day", "size"),
        test_trades=("test_trades", "sum"),
        test_pnl=("test_pnl", "sum"),
        avg_test_pnl=("test_pnl", "mean"),
        positive_folds=("test_pnl", lambda s: int((s > 0).sum())),
        worst_fold=("test_pnl", "min"),
        avg_train_pnl=("train_pnl", "mean"),
    )
    summary["selection_overfit_ratio"] = summary["test_pnl"] / summary["avg_train_pnl"].replace(0, np.nan)
    summary.to_csv(args.out_dir / "walkforward_summary.csv", index=False)
    print("wrote", args.out_dir)
    print(summary.sort_values("test_pnl", ascending=False).to_string(index=False))
    print("\nfolds")
    print(folds_df.sort_values(["family", "leftout_day"]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
