#!/usr/bin/env python3
"""Study-only BTC15M Apr 1-4 alpha loop.

This is intentionally narrower than the Apr 1-7 report.  It reads the prepared
side-candidate file, filters to `split == study`, and refuses to inspect the
holdout unless `--allow-holdout` is supplied.  The goal is to stress candidate
structure inside Apr 1-4 before any Apr 5-7 validation.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars


DEFAULT_INPUT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_apr1_7_deepdive_20260514_185319"
    / "side_candidates.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_apr1_4_alpha_loop_latest"
STUDY_START = pd.Timestamp("2026-04-01T00:00:00Z")
STUDY_END = pd.Timestamp("2026-04-05T00:00:00Z")


@dataclass(frozen=True)
class Rule:
    name: str
    family: str
    rationale: str
    selector: Callable[[pd.DataFrame], pd.Series]
    contra_selector: Callable[[pd.DataFrame], pd.Series] | None = None


def trade_sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def max_drawdown(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if x.empty:
        return 0.0
    return float((x - x.cummax()).min())


def pnl_with_entry(row: pd.Series, entry_col: str = "entry_price") -> float:
    entry = float(row[entry_col])
    fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
    won = str(row["result"]).lower() == str(row["side"]).lower()
    return (1.0 - entry - fee) if won else (-entry - fee)


def first_per_event(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    cols = ["event_ticker", "available_at", "side", "market_ticker"]
    out = df.loc[mask].sort_values(cols).drop_duplicates("event_ticker", keep="first").copy()
    return out.reset_index(drop=True)


def metrics(trades: pd.DataFrame, name: str, family: str, tag: str = "study") -> dict[str, float | int | str]:
    pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce")
    premium = pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce")
    return {
        "name": name,
        "family": family,
        "tag": tag,
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4) if len(trades) else 0.0,
        "premium": round(float(premium.sum()), 4) if len(trades) else 0.0,
        "rop": round(float(pnl.sum() / premium.sum()), 4) if float(premium.sum()) > 0 else 0.0,
        "win_rate": round(float(pd.to_numeric(trades.get("win", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(trade_sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(trades.get("entry_price", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
        "avg_ttl": round(float(pd.to_numeric(trades.get("ttl_min", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(trades)
        else 0.0,
    }


def add_forward_reprice(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["market_ticker", "side", "available_at"]).copy()
    group_cols = ["market_ticker", "side"]
    for col in ["available_at", "entry_price", "visible_qty", "spread_cents", "result"]:
        out[f"next_{col}"] = out.groupby(group_cols, sort=False)[col].shift(-1)
    out["next_delay_sec"] = (
        pd.to_datetime(out["next_available_at"], utc=True, errors="coerce")
        - pd.to_datetime(out["available_at"], utc=True, errors="coerce")
    ).dt.total_seconds()
    out["next_entry_fee"] = [
        kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") if pd.notna(x) else np.nan
        for x in out["next_entry_price"]
    ]
    won = out["result"].astype(str).str.lower().eq(out["side"].astype(str).str.lower())
    out["next_pnl"] = np.where(won, 1.0 - out["next_entry_price"] - out["next_entry_fee"], -out["next_entry_price"] - out["next_entry_fee"])
    return out


def reprice_metrics(repriced: pd.DataFrame, trades: pd.DataFrame, name: str) -> list[dict[str, float | int | str]]:
    if trades.empty:
        return []
    key_cols = ["event_ticker", "market_ticker", "side", "available_at"]
    merged = trades[key_cols].merge(
        repriced[key_cols + ["entry_price", "pnl", "next_delay_sec", "next_entry_price", "next_visible_qty", "next_spread_cents", "next_pnl"]],
        on=key_cols,
        how="left",
    )
    rows: list[dict[str, float | int | str]] = []
    for max_delay in [1.0, 2.0, 5.0, 10.0]:
        for max_slip in [0.01, 0.02, 0.03]:
            ok = (
                merged["next_delay_sec"].between(0, max_delay, inclusive="both")
                & merged["next_entry_price"].notna()
                & (merged["next_entry_price"] <= merged["entry_price"] + max_slip + 1e-12)
                & (pd.to_numeric(merged["next_visible_qty"], errors="coerce") >= 1)
                & (pd.to_numeric(merged["next_spread_cents"], errors="coerce") <= 2)
            )
            kept = merged.loc[ok].copy()
            rows.append(
                {
                    "name": name,
                    "max_delay_sec": max_delay,
                    "max_slip_cents": int(round(max_slip * 100)),
                    "kept": int(ok.sum()),
                    "drop_rate": round(float(1.0 - ok.mean()), 4),
                    "next_pnl": round(float(pd.to_numeric(kept["next_pnl"], errors="coerce").sum()), 4) if len(kept) else 0.0,
                    "next_sharpe": round(trade_sharpe(pd.to_numeric(kept["next_pnl"], errors="coerce")), 4),
                    "adverse_2c_rate": round(
                        float(
                            (
                                merged["next_delay_sec"].between(0, max_delay, inclusive="both")
                                & (merged["next_entry_price"] > merged["entry_price"] + 0.02 + 1e-12)
                            ).mean()
                        ),
                        4,
                    ),
                }
            )
    return rows


def bootstrap_event_matched(df: pd.DataFrame, trades: pd.DataFrame, rng: np.random.Generator, n: int = 5000) -> dict[str, float | int]:
    if trades.empty:
        return {"n_boot": n, "p_ge": 1.0, "boot_mean": 0.0, "boot_p95": 0.0}
    base = df[
        df["data_quality_ok"]
        & df["spread_cents"].le(2)
        & df["visible_qty"].ge(1)
        & df["entry_price"].between(0.02, 0.9)
        & df["ttl_min"].between(0.0, 12.0)
    ].copy()
    first = base.sort_values(["event_ticker", "available_at", "side"]).groupby(["day", "event_ticker"], as_index=False).head(1)
    by_day = {str(day): g["pnl"].to_numpy(dtype=float) for day, g in first.groupby("day")}
    day_counts = trades.groupby("day").size().to_dict()
    obs = float(trades["pnl"].sum())
    vals = []
    for _ in range(n):
        total = 0.0
        possible = True
        for day, count in day_counts.items():
            pool = by_day.get(str(day))
            if pool is None or len(pool) == 0:
                possible = False
                break
            total += float(rng.choice(pool, size=int(count), replace=True).sum())
        if possible:
            vals.append(total)
    arr = np.asarray(vals, dtype=float)
    return {
        "n_boot": int(len(arr)),
        "p_ge": round(float((arr >= obs).mean()), 5) if len(arr) else 1.0,
        "boot_mean": round(float(arr.mean()), 4) if len(arr) else 0.0,
        "boot_p95": round(float(np.quantile(arr, 0.95)), 4) if len(arr) else 0.0,
    }


def combined_strategy(df: pd.DataFrame, rules: list[Rule], order: list[str]) -> pd.DataFrame:
    frames = []
    priority = {name: i for i, name in enumerate(order)}
    by_name = {r.name: r for r in rules}
    for name in order:
        rule = by_name[name]
        t = first_per_event(df, rule.selector(df))
        if t.empty:
            continue
        t = t.copy()
        t["rule"] = name
        t["priority"] = priority[name]
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    all_trades = pd.concat(frames, ignore_index=True)
    return (
        all_trades.sort_values(["event_ticker", "available_at", "priority", "side", "market_ticker"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def rule_set() -> list[Rule]:
    ages_ok = lambda d: (
        d["lookback_age_0_5m_sec"].le(120)
        & d["lookback_age_1_0m_sec"].le(180)
        & d["lookback_age_3_0m_sec"].le(300)
    )
    return [
        Rule(
            name="A_early_btc5_momentum_confirmed",
            family="btc_momentum",
            rationale="Early/mid event: same-side 5m BTC trend confirms contract direction, and the contract has not already moved materially against us.",
            selector=lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(8, 15)
                & d["side_btc_5m_bps"].ge(5)
                & d["side_mid_chg_1m"].ge(-0.05)
            ),
            contra_selector=lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(8, 15)
                & d["side_btc_5m_bps"].le(-5)
                & d["side_mid_chg_1m"].le(0.05)
            ),
        ),
        Rule(
            name="B_mid_ttl_micropressure_depth",
            family="microstructure",
            rationale="2-8m TTL: buy only when microprice pressure is supported by non-hostile depth.",
            selector=lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.40, 0.95)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(2, 8)
                & d["side_depth_imbalance"].ge(-0.50)
                & d["side_micropressure"].ge(0.005)
            ),
            contra_selector=lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.40, 0.95)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(2, 8)
                & d["side_depth_imbalance"].le(0.50)
                & d["side_micropressure"].le(-0.005)
            ),
        ),
        Rule(
            name="C_late_reversal_after_contract_drop",
            family="reversal",
            rationale="Late event: contract-side price sold off while same-side BTC move is also adverse; buy mean reversion only while still executable.",
            selector=lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(0, 6)
                & d["side_mid_chg_3m"].le(-0.05)
                & d["side_btc_1m_bps"].le(-2)
            ),
            contra_selector=lambda d: (
                d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(1)
                & d["ttl_min"].between(0, 6)
                & d["side_mid_chg_3m"].ge(0.05)
                & d["side_btc_1m_bps"].ge(2)
            ),
        ),
        Rule(
            name="D_depth_backed_pressure",
            family="subagent_microstructure",
            rationale="Subagent H1: micropressure is only useful when same-side depth confirms the queue state.",
            selector=lambda d: (
                d["spread_cents"].le(2)
                & d["ttl_min"].between(1, 12)
                & d["visible_qty"].ge(20)
                & d["premium"].between(0.15, 0.85)
                & d["side_micropressure"].ge(0.003)
                & d["side_depth_imbalance"].ge(0.50)
            ),
            contra_selector=lambda d: (
                d["spread_cents"].le(2)
                & d["ttl_min"].between(1, 12)
                & d["visible_qty"].ge(20)
                & d["premium"].between(0.15, 0.85)
                & d["side_micropressure"].le(-0.003)
                & d["side_depth_imbalance"].le(-0.50)
            ),
        ),
        Rule(
            name="E_queue_asymmetry_pressure",
            family="subagent_microstructure",
            rationale="Subagent H2: pressure plus small visible entry queue avoids the adverse visible wall regime.",
            selector=lambda d: (
                d["spread_cents"].le(2)
                & d["ttl_min"].between(1, 12)
                & d["visible_qty"].ge(20)
                & d["premium"].between(0.15, 0.85)
                & d["side_micropressure"].ge(0.003)
                & d["visible_ratio"].le(0.10)
                & d["side_depth_imbalance"].ge(0.10)
            ),
            contra_selector=lambda d: (
                d["spread_cents"].le(2)
                & d["ttl_min"].between(1, 12)
                & d["visible_qty"].ge(20)
                & d["premium"].between(0.15, 0.85)
                & d["side_micropressure"].ge(0.001)
                & d["visible_ratio"].ge(0.50)
            ),
        ),
        Rule(
            name="F_mid_ttl_no_btc_chase_pressure",
            family="timing_microstructure",
            rationale="Subagent timing: micropressure is cleaner when not just chasing a simultaneous BTC jump.",
            selector=lambda d: (
                d["ttl_min"].between(4, 8)
                & d["spread_cents"].le(2)
                & d["side_micropressure"].ge(0.005)
                & d["entry_price"].between(0.02, 0.90)
                & d["rr"].ge(0.10)
                & d["side_btc_1m_bps"].abs().le(5)
            ),
            contra_selector=None,
        ),
        Rule(
            name="G_v_reversal_snapback",
            family="path_reversal",
            rationale="Same-contract V reversal: after a 3m side washout, require the 1m and 30s tape to have already turned.",
            selector=lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(1)
                & d["entry_price"].between(0.10, 0.60)
                & d["visible_qty"].ge(50)
                & d["rr"].ge(0.50)
                & ages_ok(d)
                & d["side_mid_chg_3m"].le(-0.05)
                & d["side_mid_chg_1m"].ge(0.02)
                & d["side_mid_chg_05m"].ge(0.05)
            ),
            contra_selector=lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(1)
                & d["entry_price"].between(0.10, 0.60)
                & d["visible_qty"].ge(50)
                & d["rr"].ge(0.50)
                & ages_ok(d)
                & d["side_mid_chg_3m"].ge(0.05)
                & d["side_mid_chg_1m"].le(-0.02)
                & d["side_mid_chg_05m"].le(-0.05)
            ),
        ),
        Rule(
            name="H_pullback_continuation",
            family="path_continuation",
            rationale="Same-contract continuation: strong 3m side repricing that pauses instead of reversing.",
            selector=lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(10)
                & d["rr"].ge(0.25)
                & ages_ok(d)
                & d["side_mid_chg_3m"].ge(0.20)
                & d["side_mid_chg_05m"].between(-0.03, 0.03)
            ),
            contra_selector=lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(2)
                & d["entry_price"].between(0.05, 0.80)
                & d["visible_qty"].ge(10)
                & d["rr"].ge(0.25)
                & ages_ok(d)
                & d["side_mid_chg_3m"].le(-0.20)
                & d["side_mid_chg_05m"].between(-0.03, 0.03)
            ),
        ),
        Rule(
            name="I_itm_fair_edge_calm_vol",
            family="fair_value_guard",
            rationale="Fair-value edge is only trusted when the side is already slightly ITM and realized vol is calm.",
            selector=lambda d: (
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
            contra_selector=lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(2)
                & d["entry_price"].between(0.10, 0.60)
                & d["visible_qty"].ge(25)
                & d["rr"].ge(0.50)
                & d["fair_edge_cents"].ge(5)
                & d["side_fair_p"].between(0.15, 0.85)
                & d["side_distance_bps"].lt(0)
            ),
        ),
        Rule(
            name="J_tight_fair_pullback_continuation",
            family="fair_value_path",
            rationale="A narrower fair-value/path rule: fair edge plus 5m BTC trend still aligned after a 1m pullback.",
            selector=lambda d: (
                d["ttl_min"].between(2, 8)
                & d["spread_cents"].le(1)
                & d["entry_price"].between(0.08, 0.60)
                & d["visible_qty"].ge(50)
                & d["rr"].ge(0.50)
                & d["fair_edge_cents"].ge(10)
                & d["side_fair_p"].between(0.20, 0.80)
                & d["side_btc_1m_bps"].le(0)
                & d["side_btc_5m_bps"].ge(0)
            ),
            contra_selector=None,
        ),
    ]


def perturbations(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ttl_lo in [6, 7, 8]:
        for btc5 in [3, 5, 7, 9]:
            for mid in [-0.08, -0.05, -0.02, 0.0]:
                mask = (
                    df["spread_cents"].le(2)
                    & df["entry_price"].between(0.05, 0.80)
                    & df["visible_qty"].ge(1)
                    & df["ttl_min"].between(ttl_lo, 15)
                    & df["side_btc_5m_bps"].ge(btc5)
                    & df["side_mid_chg_1m"].ge(mid)
                )
                t = first_per_event(df, mask)
                row = metrics(t, f"A_ttl{ttl_lo}_btc{btc5}_mid{mid}", "perturb_A")
                row["positive_days"] = int((t.groupby("day")["pnl"].sum() > 0).sum()) if len(t) else 0
                rows.append(row)
    for mp in [0.002, 0.003, 0.005, 0.008, 0.010]:
        for depth in [-0.5, 0.0, 0.1, 0.3, 0.5]:
            for lo, hi in [(2, 8), (4, 8), (5, 8), (6, 8)]:
                mask = (
                    df["spread_cents"].le(2)
                    & df["entry_price"].between(0.40, 0.95)
                    & df["visible_qty"].ge(1)
                    & df["ttl_min"].between(lo, hi)
                    & df["side_depth_imbalance"].ge(depth)
                    & df["side_micropressure"].ge(mp)
                )
                t = first_per_event(df, mask)
                row = metrics(t, f"B_ttl{lo}_{hi}_mp{mp}_depth{depth}", "perturb_B")
                row["positive_days"] = int((t.groupby("day")["pnl"].sum() > 0).sum()) if len(t) else 0
                rows.append(row)
    for ttl_hi in [4, 5, 6, 8]:
        for mid3 in [-0.08, -0.05, -0.03]:
            for btc1 in [-1, -2, -3, -5]:
                mask = (
                    df["spread_cents"].le(2)
                    & df["entry_price"].between(0.05, 0.80)
                    & df["visible_qty"].ge(1)
                    & df["ttl_min"].between(0, ttl_hi)
                    & df["side_mid_chg_3m"].le(mid3)
                    & df["side_btc_1m_bps"].le(btc1)
                )
                t = first_per_event(df, mask)
                row = metrics(t, f"C_ttl0_{ttl_hi}_mid{mid3}_btc{btc1}", "perturb_C")
                row["positive_days"] = int((t.groupby("day")["pnl"].sum() > 0).sum()) if len(t) else 0
                rows.append(row)
    return pd.DataFrame(rows).sort_values(["sharpe", "pnl"], ascending=False).reset_index(drop=True)


def write_markdown(
    out_dir: Path,
    summary: pd.DataFrame,
    by_day: pd.DataFrame,
    bootstrap: pd.DataFrame,
    reprice: pd.DataFrame,
    combined: pd.DataFrame,
) -> None:
    promoted = summary[
        (summary["trades"] >= 50)
        & (summary["pnl"] > 0)
        & (summary["sharpe"] >= 1.5)
        & (summary["positive_days"] >= 3)
        & (summary["worst_day_pnl"] > -1.5)
    ].copy()
    def md(df: pd.DataFrame) -> str:
        try:
            return df.to_markdown(index=False)
        except ImportError:
            return "```csv\n" + df.to_csv(index=False).strip() + "\n```"

    lines = [
        "# BTC15M Apr 1-4 Study-Only Alpha Loop",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "This report uses only `split == study` rows with event close times in Apr 1-4 UTC. No Apr 5-7 holdout rows are used.",
        "",
        "## Candidate Summary",
        "",
        md(summary),
        "",
        "## Study-Only Promotion Screen",
        "",
        "Promotion screen here means worth testing on holdout, not deployment: trades >= 50, pnl > 0, Sharpe >= 1.5, positive on at least 3 study days, and worst day above -$1.50 per 1 contract.",
        "",
        md(promoted) if len(promoted) else "No candidate passed the study-only promotion screen.",
        "",
        "## Bootstrap",
        "",
        md(bootstrap),
        "",
        "## Combined Rules",
        "",
        md(combined),
        "",
        "## Reprice Stress",
        "",
        "This is a rough Predexon snapshot stress: re-enter at the next same-market/same-side snapshot only if it arrives within the delay and has at most the allowed adverse slippage.",
        "",
        md(reprice),
        "",
        "## By Day",
        "",
        md(by_day),
    ]
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--allow-holdout", action="store_true", help="Permit Apr 5-7 validation output. Off by default.")
    args = parser.parse_args()

    df = pd.read_parquet(args.input)
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True, errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
    if "side_distance_bps" not in df.columns:
        df["side_distance_bps"] = np.where(df["side"].astype(str).str.lower().eq("yes"), df["distance_bps"], -df["distance_bps"])
    if "rv_ratio_15_60" not in df.columns:
        df["rv_ratio_15_60"] = pd.to_numeric(df["rv_15m"], errors="coerce") / pd.to_numeric(df["rv_60m"], errors="coerce").replace(0, np.nan)
    study = df[df["split"].eq("study")].copy()
    if study.empty:
        raise SystemExit("No study rows found")
    if study["close_time"].min() < STUDY_START or study["close_time"].max() >= STUDY_END:
        raise SystemExit(f"Study split leaks outside Apr 1-4: {study['close_time'].min()} -> {study['close_time'].max()}")
    if not args.allow_holdout:
        df = study.copy()
    else:
        df = df[df["close_time"].lt(pd.Timestamp("2026-04-08T00:00:00Z"))].copy()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260514)
    repriced = add_forward_reprice(df)
    rules = rule_set()

    summary_rows = []
    by_day_rows = []
    boot_rows = []
    reprice_rows = []
    selected_frames = []
    for rule in rules:
        trades = first_per_event(df, rule.selector(df))
        selected_frames.append(trades.assign(rule=rule.name, rule_family=rule.family, contra=False))
        row = metrics(trades, rule.name, rule.family)
        day_pnl = trades.groupby("day")["pnl"].sum() if len(trades) else pd.Series(dtype=float)
        row["positive_days"] = int((day_pnl > 0).sum()) if len(day_pnl) else 0
        row["worst_day_pnl"] = round(float(day_pnl.min()), 4) if len(day_pnl) else 0.0
        row["rationale"] = rule.rationale
        summary_rows.append(row)
        for day, g in trades.groupby("day"):
            by_day_rows.append(metrics(g, rule.name, rule.family, tag=str(day)))
        boot = bootstrap_event_matched(df, trades, rng)
        boot["name"] = rule.name
        boot_rows.append(boot)
        reprice_rows.extend(reprice_metrics(repriced, trades, rule.name))

        if rule.contra_selector is not None:
            contra = first_per_event(df, rule.contra_selector(df))
            selected_frames.append(contra.assign(rule=rule.name, rule_family=rule.family, contra=True))
            crow = metrics(contra, f"{rule.name}_CONTRA", rule.family, tag="study_contra")
            cday = contra.groupby("day")["pnl"].sum() if len(contra) else pd.Series(dtype=float)
            crow["positive_days"] = int((cday > 0).sum()) if len(cday) else 0
            crow["worst_day_pnl"] = round(float(cday.min()), 4) if len(cday) else 0.0
            crow["rationale"] = "Contra sanity check"
            summary_rows.append(crow)
            for day, g in contra.groupby("day"):
                by_day_rows.append(metrics(g, f"{rule.name}_CONTRA", rule.family, tag=str(day)))

    summary = pd.DataFrame(summary_rows).sort_values(["tag", "sharpe", "pnl"], ascending=[True, False, False])
    by_day = pd.DataFrame(by_day_rows).sort_values(["name", "tag"])
    bootstrap = pd.DataFrame(boot_rows).sort_values("p_ge")
    reprice = pd.DataFrame(reprice_rows).sort_values(["name", "max_delay_sec", "max_slip_cents"])
    perturb = perturbations(df)
    combined_rows = []
    combos = {
        "ABC_priority": ["A_early_btc5_momentum_confirmed", "B_mid_ttl_micropressure_depth", "C_late_reversal_after_contract_drop"],
        "ABCGHIJ_priority": [
            "A_early_btc5_momentum_confirmed",
            "B_mid_ttl_micropressure_depth",
            "C_late_reversal_after_contract_drop",
            "G_v_reversal_snapback",
            "H_pullback_continuation",
            "I_itm_fair_edge_calm_vol",
            "J_tight_fair_pullback_continuation",
        ],
        "micro_path_priority": [
            "B_mid_ttl_micropressure_depth",
            "G_v_reversal_snapback",
            "H_pullback_continuation",
            "C_late_reversal_after_contract_drop",
            "D_depth_backed_pressure",
        ],
        "conservative_path_fair": [
            "G_v_reversal_snapback",
            "J_tight_fair_pullback_continuation",
            "I_itm_fair_edge_calm_vol",
            "H_pullback_continuation",
        ],
    }
    for combo, order in combos.items():
        ct = combined_strategy(df, rules, order)
        row = metrics(ct, combo, "combined")
        day_pnl = ct.groupby("day")["pnl"].sum() if len(ct) else pd.Series(dtype=float)
        row["positive_days"] = int((day_pnl > 0).sum()) if len(day_pnl) else 0
        row["worst_day_pnl"] = round(float(day_pnl.min()), 4) if len(day_pnl) else 0.0
        row["rules"] = " > ".join(order)
        combined_rows.append(row)
    combined = pd.DataFrame(combined_rows).sort_values(["sharpe", "pnl"], ascending=False)
    selected = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()

    summary.to_csv(out_dir / "candidate_summary.csv", index=False)
    by_day.to_csv(out_dir / "candidate_by_day.csv", index=False)
    bootstrap.to_csv(out_dir / "candidate_bootstrap.csv", index=False)
    reprice.to_csv(out_dir / "candidate_reprice_stress.csv", index=False)
    perturb.to_csv(out_dir / "perturbation_grid.csv", index=False)
    combined.to_csv(out_dir / "combined_summary.csv", index=False)
    selected.to_parquet(out_dir / "selected_trades.parquet", index=False)
    write_markdown(out_dir, summary, by_day, bootstrap, reprice, combined)

    print(f"wrote {out_dir}")
    print(summary[["name", "tag", "trades", "pnl", "rop", "win_rate", "max_dd", "sharpe", "positive_days", "worst_day_pnl"]].to_string(index=False))
    print("\nCombined:")
    print(combined[["name", "trades", "pnl", "rop", "win_rate", "max_dd", "sharpe", "positive_days", "worst_day_pnl"]].to_string(index=False))
    print("\nTop perturbations:")
    print(perturb.head(20)[["name", "trades", "pnl", "rop", "win_rate", "max_dd", "sharpe", "positive_days"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
