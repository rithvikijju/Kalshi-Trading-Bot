#!/usr/bin/env python3
"""Fixed Apr 5-7 validation for BTC15M candidates discovered on Apr 1-4.

This script contains only pre-registered candidates selected from the Apr 1-4
study loop.  It evaluates study and holdout splits separately and does not tune
thresholds on holdout.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_INPUT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_apr1_7_deepdive_20260514_185319"
    / "side_candidates.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_apr5_7_fixed_validation_latest"
HOLDOUT_START = pd.Timestamp("2026-04-05T00:00:00Z")
HOLDOUT_END = pd.Timestamp("2026-04-08T00:00:00Z")


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    rationale: str
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


def metric_row(trades: pd.DataFrame, name: str, family: str, split: str, rationale: str = "") -> dict[str, float | int | str]:
    pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce")
    premium = pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce")
    return {
        "name": name,
        "family": family,
        "split": split,
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4) if len(trades) else 0.0,
        "premium": round(float(premium.sum()), 4) if len(trades) else 0.0,
        "rop": round(float(pnl.sum() / premium.sum()), 4) if float(premium.sum()) > 0 else 0.0,
        "return_on_100": round(float(pnl.sum()), 4),
        "win_rate": round(float(pd.to_numeric(trades.get("win", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "max_dd": round(max_dd(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(trades.get("entry_price", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "avg_ttl": round(float(pd.to_numeric(trades.get("ttl_min", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "rationale": rationale,
    }


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "side_distance_bps" not in out.columns:
        out["side_distance_bps"] = np.where(out["side"].astype(str).str.lower().eq("yes"), out["distance_bps"], -out["distance_bps"])
    if "side_mid_chg_5m" not in out.columns and "yes_mid_chg_5_0m" in out.columns:
        out["side_mid_chg_5m"] = np.where(
            out["side"].astype(str).str.lower().eq("yes"),
            pd.to_numeric(out["yes_mid_chg_5_0m"], errors="coerce"),
            -pd.to_numeric(out["yes_mid_chg_5_0m"], errors="coerce"),
        )
    if "rv_ratio_15_60" not in out.columns:
        out["rv_ratio_15_60"] = pd.to_numeric(out["rv_15m"], errors="coerce") / pd.to_numeric(out["rv_60m"], errors="coerce").replace(0, np.nan)
    return out


def age_ok(d: pd.DataFrame) -> pd.Series:
    return (
        pd.to_numeric(d["lookback_age_0_5m_sec"], errors="coerce").le(120)
        & pd.to_numeric(d["lookback_age_1_0m_sec"], errors="coerce").le(180)
        & pd.to_numeric(d["lookback_age_3_0m_sec"], errors="coerce").le(300)
    )


def candidates() -> list[Candidate]:
    return [
        Candidate(
            "A_btc5_exact",
            "btc5_continuation",
            "Apr1-4 LODO-positive exact A: early/mid TTL same-side 5m BTC trend, no strong adverse contract move.",
            lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(8, 15)
                & d["side_btc_5m_bps"].ge(5)
                & d["side_mid_chg_1m"].ge(-0.05)
            ),
        ),
        Candidate(
            "A_btc5_broader_entry90",
            "btc5_continuation",
            "Study preregistered sibling from A stress: same signal with entry capped at 90c.",
            lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.90)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(8, 15)
                & d["side_btc_5m_bps"].ge(5)
                & d["side_mid_chg_1m"].ge(-0.05)
            ),
        ),
        Candidate(
            "C_late_reversal_exact",
            "late_reversal",
            "Apr1-4 LODO-positive late reversal: contract and BTC moved against side, buy first executable snapback/reversion candidate.",
            lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(0, 6)
                & d["side_mid_chg_3m"].le(-0.05)
                & d["side_btc_1m_bps"].le(-2)
            ),
        ),
        Candidate(
            "B_micropressure_exact",
            "micropressure",
            "Positive but weaker LODO micropressure baseline.",
            lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.40, 0.95)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(2, 8)
                & d["side_depth_imbalance"].ge(-0.50)
                & d["side_micropressure"].ge(0.005)
            ),
        ),
        Candidate(
            "B_micropressure_refined",
            "micropressure",
            "Second-round study-only refinement: nonnegative depth, calm quote speed, entry 50-90c, visible qty >=2.",
            lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(2)
                & d["entry_price"].between(0.50, 0.90)
                & d["visible_qty"].ge(2)
                & d["side_depth_imbalance"].ge(0.0)
                & d["side_micropressure"].ge(0.005)
                & pd.to_numeric(d["quote_speed_cents"], errors="coerce").abs().le(1.0)
            ),
        ),
        Candidate(
            "G_v_reversal",
            "path_reversal",
            "Same-contract V reversal from path study.",
            lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(1)
                & d["entry_price"].between(0.10, 0.60)
                & d["visible_qty"].ge(50)
                & d["rr"].ge(0.50)
                & age_ok(d)
                & d["side_mid_chg_3m"].le(-0.05)
                & d["side_mid_chg_1m"].ge(0.02)
                & d["side_mid_chg_05m"].ge(0.05)
            ),
        ),
        Candidate(
            "I_itm_fair_edge_calm_vol",
            "fair_value_guard",
            "Harvey fair-value guard: trust edge only when side is slightly ITM and realized vol is calm.",
            lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(2)
                & d["entry_price"].between(0.10, 0.60)
                & d["visible_qty"].ge(25)
                & d["rr"].ge(0.50)
                & d["fair_edge_cents"].ge(5)
                & d["side_fair_p"].between(0.15, 0.85)
                & d["side_distance_bps"].between(0, 20)
                & d["rv_ratio_15_60"].le(1.10)
            ),
        ),
        Candidate(
            "K_btc5_pullback_guard",
            "pullback_guard",
            "Second-round Apr1-4 guard: same-side BTC5 strong but the contract side has pulled back enough to avoid chase.",
            lambda d: (
                d["side_btc_5m_bps"].ge(8)
                & d["side_mid_chg_3m"].le(-0.12)
                & d["rv_15m"].le(0.60)
                & d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.90)
                & d["visible_qty"].ge(1)
            ),
        ),
        Candidate(
            "L_5m_washout_snapback",
            "path_reversal",
            "Ramanujan study-only path candidate: 5m side washout followed by 30s snapback.",
            lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.90)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].gt(2)
                & d["ttl_min"].le(10)
                & d["side_mid_chg_5m"].le(-0.20)
                & d["side_mid_chg_05m"].ge(0.08)
            ),
        ),
        Candidate(
            "M_high_entry_pullback_2_4m",
            "path_continuation",
            "Ramanujan cleaner path candidate: high-entry side trend pauses in the 2-4m TTL pocket, rejecting jumpy quotes.",
            lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].ge(0.70)
                & d["entry_price"].le(0.95)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].gt(2)
                & d["ttl_min"].le(4)
                & d["side_mid_chg_2m"].ge(0.15)
                & d["side_mid_chg_05m"].between(-0.10, 0.02)
                & pd.to_numeric(d["quote_speed_cents"], errors="coerce").le(4)
                & pd.to_numeric(d["side_mid_chg_05m"], errors="coerce").abs().lt(0.20)
            ),
        ),
        Candidate(
            "N_high_entry_2m_pullback",
            "path_pullback",
            "Loop-3 Apr1-4 candidate: high-entry contract with a small 2m pullback, first event only.",
            lambda d: (
                d["visible_qty"].ge(1)
                & d["entry_price"].ge(0.75)
                & pd.to_numeric(d["side_mid_chg_2m"], errors="coerce").notna()
                & d["side_mid_chg_2m"].le(-0.01)
                & d["ttl_min"].between(3, 10)
            ),
        ),
    ]


def combined(df: pd.DataFrame, cands: list[Candidate], order: list[str]) -> pd.DataFrame:
    by_name = {c.name: c for c in cands}
    frames = []
    for i, name in enumerate(order):
        c = by_name[name]
        t = first_event(df, c.selector(df))
        if t.empty:
            continue
        t = t.copy()
        t["candidate"] = name
        t["priority"] = i
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["event_ticker", "available_at", "priority", "side", "market_ticker"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def reprice_stress(df: pd.DataFrame, trades: pd.DataFrame, name: str, split: str) -> dict[str, float | int | str]:
    if trades.empty:
        return {"name": name, "split": split, "delay_sec": 2, "slip_cents": 2, "kept": 0, "drop_rate": 1.0, "next_pnl": 0.0}
    side_df = df.sort_values(["market_ticker", "side", "available_at"]).copy()
    for col in ["available_at", "entry_price", "visible_qty", "spread_cents", "result"]:
        side_df[f"next_{col}"] = side_df.groupby(["market_ticker", "side"], sort=False)[col].shift(-1)
    side_df["next_delay_sec"] = (side_df["next_available_at"] - side_df["available_at"]).dt.total_seconds()
    side_df["next_fee"] = [
        kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") if pd.notna(x) else np.nan
        for x in side_df["next_entry_price"]
    ]
    won = side_df["result"].astype(str).str.lower().eq(side_df["side"].astype(str).str.lower())
    side_df["next_pnl"] = np.where(won, 1.0 - side_df["next_entry_price"] - side_df["next_fee"], -side_df["next_entry_price"] - side_df["next_fee"])
    keys = ["event_ticker", "market_ticker", "side", "available_at"]
    m = trades[keys].merge(
        side_df[keys + ["entry_price", "next_delay_sec", "next_entry_price", "next_visible_qty", "next_spread_cents", "next_pnl"]],
        on=keys,
        how="left",
    )
    ok = (
        m["next_delay_sec"].between(0, 2, inclusive="both")
        & m["next_entry_price"].notna()
        & (m["next_entry_price"] <= m["entry_price"] + 0.02 + 1e-12)
        & pd.to_numeric(m["next_visible_qty"], errors="coerce").ge(1)
        & pd.to_numeric(m["next_spread_cents"], errors="coerce").le(2)
    )
    kept = m.loc[ok]
    return {
        "name": name,
        "split": split,
        "delay_sec": 2,
        "slip_cents": 2,
        "kept": int(ok.sum()),
        "drop_rate": round(float(1.0 - ok.mean()), 4),
        "next_pnl": round(float(pd.to_numeric(kept["next_pnl"], errors="coerce").sum()), 4) if len(kept) else 0.0,
        "next_sharpe": round(sharpe(pd.to_numeric(kept["next_pnl"], errors="coerce")), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(args.input)
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True, errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
    df = add_derived(df)
    cands = candidates()
    rows = []
    day_rows = []
    reprice_rows = []
    trade_frames = []
    for split in ["study", "holdout"]:
        d = df[df["split"].eq(split)].copy()
        if split == "holdout":
            if d["close_time"].min() < HOLDOUT_START or d["close_time"].max() >= HOLDOUT_END:
                raise SystemExit(f"Holdout split leak: {d['close_time'].min()} -> {d['close_time'].max()}")
        for c in cands:
            t = first_event(d, c.selector(d))
            t = t.assign(candidate=c.name, candidate_family=c.family, eval_split=split)
            trade_frames.append(t)
            rows.append(metric_row(t, c.name, c.family, split, c.rationale))
            reprice_rows.append(reprice_stress(d, t, c.name, split))
            for day, g in t.groupby("day"):
                day_rows.append(metric_row(g, c.name, c.family, str(day), c.rationale))
        combos = {
            "AC_priority": ["A_btc5_exact", "C_late_reversal_exact"],
            "ABC_priority": ["A_btc5_exact", "B_micropressure_exact", "C_late_reversal_exact"],
            "A_C_Brefined_priority": ["A_btc5_exact", "C_late_reversal_exact", "B_micropressure_refined"],
            "conservative_path_fair": ["G_v_reversal", "I_itm_fair_edge_calm_vol"],
            "A_C_Kguard_priority": ["A_btc5_exact", "C_late_reversal_exact", "K_btc5_pullback_guard"],
            "B_I_priority": ["B_micropressure_exact", "I_itm_fair_edge_calm_vol"],
            "I_B_M_priority": ["I_itm_fair_edge_calm_vol", "B_micropressure_exact", "M_high_entry_pullback_2_4m"],
            "path_pack_priority": ["L_5m_washout_snapback", "M_high_entry_pullback_2_4m", "G_v_reversal"],
            "I_B_N_priority": ["I_itm_fair_edge_calm_vol", "B_micropressure_exact", "N_high_entry_2m_pullback"],
        }
        for name, order in combos.items():
            ct = combined(d, cands, order).assign(candidate=name, candidate_family="combined", eval_split=split)
            trade_frames.append(ct)
            rows.append(metric_row(ct, name, "combined", split, "Fixed priority combo from study-only candidates: " + " > ".join(order)))
            reprice_rows.append(reprice_stress(d, ct, name, split))
            for day, g in ct.groupby("day"):
                day_rows.append(metric_row(g, name, "combined", str(day), "combo"))

    summary = pd.DataFrame(rows).sort_values(["split", "sharpe", "pnl"], ascending=[True, False, False])
    by_day = pd.DataFrame(day_rows).sort_values(["name", "split"])
    repriced = pd.DataFrame(reprice_rows).sort_values(["split", "name"])
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    summary.to_csv(args.out_dir / "validation_summary.csv", index=False)
    by_day.to_csv(args.out_dir / "validation_by_day.csv", index=False)
    repriced.to_csv(args.out_dir / "validation_reprice_2s_2c.csv", index=False)
    trades.to_parquet(args.out_dir / "validation_trades.parquet", index=False)
    print("wrote", args.out_dir)
    print(summary[["name", "split", "trades", "pnl", "return_on_100", "rop", "win_rate", "max_dd", "sharpe", "avg_entry", "avg_ttl"]].to_string(index=False))
    print("\n2s/2c reprice")
    print(repriced[["name", "split", "kept", "drop_rate", "next_pnl", "next_sharpe"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
