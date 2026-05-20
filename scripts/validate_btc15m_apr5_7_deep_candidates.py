#!/usr/bin/env python3
"""Validate fixed Apr1-4 BTC15M candidates on Apr5-7 holdout.

The candidates here are frozen from Apr1-4-only research.  This script does
not tune thresholds on holdout.  It reports:
  - base one-contract fee-inclusive PnL
  - +1c/+2c/+3c adverse-entry stress
  - next-quote reprice fill realism for 2s/+2c and 5s/+2c
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

from config.btc_1hr_config import kalshi_fee_dollars


DEFAULT_INPUT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_apr1_7_deepdive_20260514_185319"
    / "side_candidates.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_apr5_7_deep_candidates_latest"
STUDY_START = pd.Timestamp("2026-04-01T00:00:00Z")
STUDY_END = pd.Timestamp("2026-04-05T00:00:00Z")
HOLDOUT_END = pd.Timestamp("2026-04-08T00:00:00Z")


@dataclass(frozen=True)
class Rule:
    name: str
    family: str
    rationale: str
    selector: Callable[[pd.DataFrame], pd.Series]


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def max_dd(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def pnl_at_entry(side: pd.Series, result: pd.Series, entry: pd.Series) -> pd.Series:
    entry = pd.to_numeric(entry, errors="coerce")
    fee = entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") if pd.notna(x) else np.nan)
    won = side.astype(str).str.lower().eq(result.astype(str).str.lower())
    pnl = np.where(won, 1.0 - entry - fee, -entry - fee)
    return pd.Series(pnl, index=entry.index, dtype=float)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True, errors="coerce")
    out["close_time"] = pd.to_datetime(out["close_time"], utc=True, errors="coerce")
    out["event_date_split"] = np.select(
        [
            (out["close_time"] >= STUDY_START) & (out["close_time"] < STUDY_END),
            (out["close_time"] >= STUDY_END) & (out["close_time"] < HOLDOUT_END),
        ],
        ["study_apr01_04", "holdout_apr05_07"],
        default="outside",
    )
    out["day"] = out["close_time"].dt.strftime("%Y-%m-%d")
    if "side_distance_bps" not in out.columns:
        out["side_distance_bps"] = np.where(
            out["side"].astype(str).str.lower().eq("yes"),
            pd.to_numeric(out["distance_bps"], errors="coerce"),
            -pd.to_numeric(out["distance_bps"], errors="coerce"),
        )
    if "fee_edge" not in out.columns:
        out["fee_edge"] = (
            pd.to_numeric(out["side_fair_p"], errors="coerce")
            - pd.to_numeric(out["entry_price"], errors="coerce")
            - pd.to_numeric(out["entry_fee"], errors="coerce")
        )
    if "side_mid_chg_5m" not in out.columns and "yes_mid_chg_5_0m" in out.columns:
        out["side_mid_chg_5m"] = np.where(
            out["side"].astype(str).str.lower().eq("yes"),
            pd.to_numeric(out["yes_mid_chg_5_0m"], errors="coerce"),
            -pd.to_numeric(out["yes_mid_chg_5_0m"], errors="coerce"),
        )
    return out


def add_next_quote(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["market_ticker", "side", "available_at", "timestamp_ms"]).copy()
    group = out.groupby(["market_ticker", "side"], sort=False)
    for col in ["available_at", "entry_price", "visible_qty", "spread_cents", "result"]:
        out[f"next_{col}"] = group[col].shift(-1)
    out["next_delay_sec"] = (
        pd.to_datetime(out["next_available_at"], utc=True, errors="coerce")
        - pd.to_datetime(out["available_at"], utc=True, errors="coerce")
    ).dt.total_seconds()
    out["next_pnl"] = pnl_at_entry(out["side"], out["result"], out["next_entry_price"])
    return out


def first_per_event(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    order = ["event_ticker", "available_at", "timestamp_ms", "fair_edge_cents", "sequence", "side", "market_ticker"]
    return (
        df.loc[mask]
        .sort_values(order, ascending=[True, True, True, False, True, True, True])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def rules() -> list[Rule]:
    base = lambda d: d["data_quality_ok"].fillna(False)
    return [
        Rule(
            "F1_exact_fair_p65_edge8",
            "fair_fee_edge",
            "Apr1-4 fair-value agent primary exact rule: calibrated probability floor plus raw edge cushion.",
            lambda d: d["side_fair_p"].ge(0.65) & d["fair_edge_cents"].ge(8),
        ),
        Rule(
            "F2_exact_fair_p60_edge12",
            "fair_fee_edge",
            "Apr1-4 fair-value agent high-cushion exact rule.",
            lambda d: d["side_fair_p"].ge(0.60) & d["fair_edge_cents"].ge(12),
        ),
        Rule(
            "F3_exact_fair_p90_edge5",
            "fair_fee_edge",
            "Apr1-4 fair-value agent conservative high-probability exact rule.",
            lambda d: d["side_fair_p"].ge(0.90) & d["fair_edge_cents"].ge(5),
        ),
        Rule(
            "F4_exact_no_fair_p65_edge8",
            "fair_fee_edge",
            "Apr1-4 fair-value agent NO-side ablation exact rule.",
            lambda d: d["side"].astype(str).str.lower().eq("no") & d["side_fair_p"].ge(0.65) & d["fair_edge_cents"].ge(8),
        ),
        Rule(
            "F5_filtered_fair_p60_edge12_spread2",
            "fair_fee_edge",
            "Execution-conservative version of F2 requiring quality, spread<=2, visible ask, and sane entry.",
            lambda d: base(d)
            & d["side_fair_p"].ge(0.60)
            & d["fair_edge_cents"].ge(12)
            & d["spread_cents"].le(2)
            & d["visible_qty"].ge(1)
            & d["entry_price"].between(0.03, 0.98),
        ),
        Rule(
            "M1_yes_opposite_wall",
            "microstructure",
            "Apr1-4 microstructure agent A: YES side with large opposite wall and pressure.",
            lambda d: base(d)
            & d["side"].astype(str).str.lower().eq("yes")
            & d["entry_price"].between(0.50, 0.80)
            & d["ttl_min"].between(0, 15)
            & d["spread_cents"].le(1)
            & d["visible_qty"].ge(25)
            & d["opp_visible_qty"].ge(500)
            & d["side_micropressure"].ge(0.003),
        ),
        Rule(
            "M2_depth_pressure",
            "microstructure",
            "Apr1-4 microstructure agent B: side-adjusted depth pressure.",
            lambda d: base(d)
            & d["entry_price"].between(0.50, 0.80)
            & d["ttl_min"].between(2, 8)
            & d["spread_cents"].le(2)
            & d["visible_qty"].ge(25)
            & d["side_micropressure"].ge(0.003)
            & d["side_depth_imbalance"].ge(0.50),
        ),
        Rule(
            "M3_A_or_B_composite",
            "microstructure",
            "Apr1-4 microstructure agent composite: first event-level signal satisfying M1 or M2.",
            lambda d: (
                (
                    base(d)
                    & d["side"].astype(str).str.lower().eq("yes")
                    & d["entry_price"].between(0.50, 0.80)
                    & d["ttl_min"].between(0, 15)
                    & d["spread_cents"].le(1)
                    & d["visible_qty"].ge(25)
                    & d["opp_visible_qty"].ge(500)
                    & d["side_micropressure"].ge(0.003)
                )
                | (
                    base(d)
                    & d["entry_price"].between(0.50, 0.80)
                    & d["ttl_min"].between(2, 8)
                    & d["spread_cents"].le(2)
                    & d["visible_qty"].ge(25)
                    & d["side_micropressure"].ge(0.003)
                    & d["side_depth_imbalance"].ge(0.50)
                )
            ),
        ),
        Rule(
            "M4_broad_micropressure",
            "microstructure",
            "Local grid broad mid-TTL micropressure candidate.",
            lambda d: base(d)
            & d["spread_cents"].le(2)
            & d["visible_qty"].ge(1)
            & d["entry_price"].between(0.40, 0.95)
            & d["ttl_min"].between(2, 8)
            & d["side_micropressure"].ge(0.005)
            & d["side_depth_imbalance"].ge(-0.5),
        ),
        Rule(
            "P1_path_washout_snapback",
            "path",
            "Path agent R1: 2m washout plus 30s snapback, included as a stress candidate not deployment candidate.",
            lambda d: base(d)
            & d["entry_price"].between(0.05, 0.95)
            & d["ttl_min"].between(3, 12)
            & d["quote_speed_cents"].ge(0.5)
            & d["lookback_age_2_0m_sec"].le(180)
            & d["lookback_age_0_5m_sec"].le(75)
            & d["side_mid_chg_2m"].le(-0.20)
            & d["side_mid_chg_05m"].ge(0.05),
        ),
    ]


def summarize(trades: pd.DataFrame, name: str, family: str, split: str, rationale: str) -> dict[str, object]:
    row: dict[str, object] = {"name": name, "family": family, "split": split, "trades": int(len(trades)), "rationale": rationale}
    for slip in [0, 1, 2, 3]:
        col = "pnl" if slip == 0 else f"pnl_{slip}c"
        pnl = pd.to_numeric(trades.get(col, pd.Series(dtype=float)), errors="coerce").dropna()
        row[col] = round(float(pnl.sum()), 4) if len(pnl) else 0.0
        row[f"max_dd_{slip}c"] = round(max_dd(pnl), 4) if len(pnl) else 0.0
        row[f"sharpe_{slip}c"] = round(sharpe(pnl), 4) if len(pnl) else 0.0
    premium = pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce")
    row["premium"] = round(float(premium.sum()), 4) if len(premium) else 0.0
    row["rop_2c"] = round(float(row["pnl_2c"]) / row["premium"], 4) if row["premium"] else 0.0
    row["win_rate"] = round(float(pd.to_numeric(trades.get("win", pd.Series(dtype=float)), errors="coerce").mean()), 4) if len(trades) else 0.0
    row["avg_entry"] = round(float(pd.to_numeric(trades.get("entry_price", pd.Series(dtype=float)), errors="coerce").mean()), 4) if len(trades) else 0.0
    row["avg_ttl"] = round(float(pd.to_numeric(trades.get("ttl_min", pd.Series(dtype=float)), errors="coerce").mean()), 4) if len(trades) else 0.0
    return row


def reprice_rows(trades: pd.DataFrame, name: str, split: str) -> list[dict[str, object]]:
    rows = []
    for delay in [2, 5]:
        for slip in [1, 2, 3]:
            ok = (
                pd.to_numeric(trades["next_delay_sec"], errors="coerce").between(0, delay, inclusive="both")
                & pd.to_numeric(trades["next_visible_qty"], errors="coerce").ge(1)
                & pd.to_numeric(trades["next_spread_cents"], errors="coerce").le(2)
                & (pd.to_numeric(trades["next_entry_price"], errors="coerce") <= pd.to_numeric(trades["entry_price"], errors="coerce") + slip / 100.0 + 1e-12)
            )
            kept = trades.loc[ok]
            pnl = pd.to_numeric(kept.get("next_pnl", pd.Series(dtype=float)), errors="coerce")
            rows.append(
                {
                    "name": name,
                    "split": split,
                    "delay_sec": delay,
                    "max_slip_cents": slip,
                    "signals": int(len(trades)),
                    "filled": int(ok.sum()),
                    "fill_rate": round(float(ok.mean()), 4) if len(trades) else 0.0,
                    "pnl_next": round(float(pnl.sum()), 4) if len(pnl) else 0.0,
                    "sharpe_next": round(sharpe(pnl), 4) if len(pnl) else 0.0,
                }
            )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    df = add_next_quote(add_derived(pd.read_parquet(args.input)))
    df = df[df["event_date_split"].isin(["study_apr01_04", "holdout_apr05_07"])].copy()
    for slip in [1, 2, 3]:
        entry = (pd.to_numeric(df["entry_price"], errors="coerce") + slip / 100.0).clip(upper=0.99)
        df[f"pnl_{slip}c"] = pnl_at_entry(df["side"], df["result"], entry)

    summary_rows = []
    daily_rows = []
    reprice = []
    trade_frames = []
    for rule in rules():
        selected = first_per_event(df, rule.selector(df))
        selected["rule"] = rule.name
        selected["family"] = rule.family
        trade_frames.append(selected)
        for split in ["study_apr01_04", "holdout_apr05_07"]:
            sample = selected[selected["event_date_split"].eq(split)].copy()
            summary_rows.append(summarize(sample, rule.name, rule.family, split, rule.rationale))
            reprice.extend(reprice_rows(sample, rule.name, split))
            for day, g in sample.groupby("day", dropna=False):
                row = summarize(g, rule.name, rule.family, str(day), rule.rationale)
                row["parent_split"] = split
                daily_rows.append(row)

    summary = pd.DataFrame(summary_rows)
    daily = pd.DataFrame(daily_rows)
    reprice_df = pd.DataFrame(reprice)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    summary.to_csv(args.out / "deep_candidate_validation_summary.csv", index=False)
    daily.to_csv(args.out / "deep_candidate_validation_daily.csv", index=False)
    reprice_df.to_csv(args.out / "deep_candidate_validation_reprice.csv", index=False)
    trades.to_parquet(args.out / "deep_candidate_validation_trades.parquet", index=False, compression="zstd")
    print(f"wrote {args.out}")
    print(summary.to_string(index=False))
    print("\nReprice 2s/+2c holdout:")
    print(reprice_df[(reprice_df["split"].eq("holdout_apr05_07")) & (reprice_df["delay_sec"].eq(2)) & (reprice_df["max_slip_cents"].eq(2))].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
