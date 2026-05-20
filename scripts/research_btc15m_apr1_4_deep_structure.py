#!/usr/bin/env python3
"""Deep Apr 1-4-only BTC15M structure search.

This script is deliberately study-only.  It loads the prepared BTC15M
side-candidate parquet and refuses to evaluate Apr 5-7 unless a separate
validation script is run later.  The goal is to find candidate structure inside
Apr 1-4 while keeping the future holdout untouched.

Outputs:
  - deep_structure_summary.csv: all generated candidate rules
  - deep_structure_top.csv: robust-looking rules after strict study filters
  - deep_structure_trades.parquet: selected trades for top rules
  - model_lodo_predictions.parquet: leave-one-day-out model predictions
"""

from __future__ import annotations

import argparse
import itertools
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

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
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc15m_apr1_4_deep_structure_latest"
STUDY_START = pd.Timestamp("2026-04-01T00:00:00Z")
STUDY_END = pd.Timestamp("2026-04-05T00:00:00Z")


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    rationale: str
    mask: np.ndarray


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


def stressed_pnl(side: pd.Series, result: pd.Series, entry: pd.Series, slip_cents: int) -> pd.Series:
    stressed_entry = pd.to_numeric(entry, errors="coerce") + slip_cents / 100.0
    fee = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") if pd.notna(x) else np.nan)
    won = side.astype(str).str.lower().eq(result.astype(str).str.lower())
    pnl = np.where(won, 1.0 - stressed_entry - fee, -stressed_entry - fee)
    pnl = pd.Series(pnl, index=entry.index, dtype=float)
    pnl = pnl.mask(stressed_entry >= 0.995, np.nan)
    return pnl


def add_surface_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add same-event same-timestamp cross-contract structure features.

    The input already has side rows.  Features are built from the YES mid surface
    at the exact available_at timestamp, then mapped back to YES and NO sides
    without assuming NO = 1 - YES for execution.
    """
    out = df.copy()
    out["fee_edge"] = out["side_fair_p"] - out["entry_price"] - out["entry_fee"]
    out["rv_ratio_15_60"] = pd.to_numeric(out["rv_15m"], errors="coerce") / pd.to_numeric(out["rv_60m"], errors="coerce")
    out["abs_distance_bps"] = pd.to_numeric(out["distance_bps"], errors="coerce").abs()
    out["entry_x_ttl"] = pd.to_numeric(out["entry_price"], errors="coerce") * pd.to_numeric(out["ttl_min"], errors="coerce")

    base_cols = ["event_ticker", "available_at", "market_ticker", "floor_strike", "yes_mid", "yes_bid", "yes_ask"]
    base = (
        out[base_cols]
        .drop_duplicates(["event_ticker", "available_at", "market_ticker"])
        .sort_values(["event_ticker", "available_at", "floor_strike"])
        .copy()
    )
    g = base.groupby(["event_ticker", "available_at"], sort=False)
    base["strike_rank"] = g.cumcount()
    base["n_strikes"] = g["market_ticker"].transform("size")
    base["rank_pct"] = base["strike_rank"] / (base["n_strikes"] - 1).clip(lower=1)
    base["yes_mid_prev"] = g["yes_mid"].shift(1)
    base["yes_mid_next"] = g["yes_mid"].shift(-1)
    base["local_slope_prev"] = base["yes_mid"] - base["yes_mid_prev"]
    base["local_slope_next"] = base["yes_mid_next"] - base["yes_mid"]
    base["local_curvature"] = base["yes_mid_prev"] - 2.0 * base["yes_mid"] + base["yes_mid_next"]
    # YES prices should usually fall as strike rises.  Positive slope is a
    # same-timestamp monotonicity violation; large negative slope can indicate
    # a steep local threshold wall.
    base["monotone_violation_prev"] = base["local_slope_prev"].gt(0).astype(int)
    base["monotone_violation_next"] = base["local_slope_next"].gt(0).astype(int)
    base["abs_local_curvature"] = base["local_curvature"].abs()
    base["surface_center_mid"] = g["yes_mid"].transform("median")
    base["surface_mid_z"] = (base["yes_mid"] - g["yes_mid"].transform("mean")) / g["yes_mid"].transform("std").replace(0, np.nan)

    merge_cols = [
        "event_ticker",
        "available_at",
        "market_ticker",
        "strike_rank",
        "n_strikes",
        "rank_pct",
        "local_slope_prev",
        "local_slope_next",
        "local_curvature",
        "abs_local_curvature",
        "monotone_violation_prev",
        "monotone_violation_next",
        "surface_center_mid",
        "surface_mid_z",
    ]
    out = out.merge(base[merge_cols], on=["event_ticker", "available_at", "market_ticker"], how="left")
    out["side_local_slope_into_money"] = np.where(
        out["side"].astype(str).str.upper().eq("YES"),
        -out["local_slope_next"],
        out["local_slope_prev"],
    )
    out["side_curvature_favor"] = np.where(
        out["side"].astype(str).str.upper().eq("YES"),
        -out["local_curvature"],
        out["local_curvature"],
    )
    return out


def prepare(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True, errors="coerce")
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    study = df[
        (df["available_at"] >= STUDY_START)
        & (df["available_at"] < STUDY_END)
        & df["split"].astype(str).eq("study")
    ].copy()
    study = add_surface_features(study)
    study = study.sort_values(["event_ticker", "available_at", "side", "market_ticker"]).reset_index(drop=True)
    for slip in [0, 1, 2, 3]:
        study[f"pnl_{slip}c"] = stressed_pnl(study["side"], study["result"], study["entry_price"], slip)
    return study


def first_per_event(df: pd.DataFrame, mask: np.ndarray) -> pd.DataFrame:
    cols = ["event_ticker", "available_at", "side", "market_ticker"]
    t = df.loc[mask].sort_values(cols).drop_duplicates("event_ticker", keep="first").copy()
    return t.reset_index(drop=True)


def summarize_trades(trades: pd.DataFrame, name: str, family: str, rationale: str) -> dict[str, object]:
    row: dict[str, object] = {"name": name, "family": family, "rationale": rationale, "trades": int(len(trades))}
    for slip in [0, 1, 2, 3]:
        col = f"pnl_{slip}c"
        pnl = pd.to_numeric(trades.get(col, pd.Series(dtype=float)), errors="coerce").dropna()
        row[f"pnl_{slip}c"] = round(float(pnl.sum()), 4) if len(pnl) else 0.0
        row[f"max_dd_{slip}c"] = round(max_dd(pnl), 4) if len(pnl) else 0.0
        row[f"sharpe_{slip}c"] = round(sharpe(pnl), 4) if len(pnl) else 0.0
    row["premium"] = round(float(pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce").sum()), 4)
    row["rop_2c"] = round(float(row["pnl_2c"]) / row["premium"], 4) if row["premium"] else 0.0
    row["win_rate"] = round(float(pd.to_numeric(trades.get("win", pd.Series(dtype=float)), errors="coerce").mean()), 4) if len(trades) else 0.0
    row["avg_entry"] = round(float(pd.to_numeric(trades.get("entry_price", pd.Series(dtype=float)), errors="coerce").mean()), 4) if len(trades) else 0.0
    row["avg_ttl"] = round(float(pd.to_numeric(trades.get("ttl_min", pd.Series(dtype=float)), errors="coerce").mean()), 4) if len(trades) else 0.0
    day = trades.groupby("day")["pnl_2c"].sum() if len(trades) else pd.Series(dtype=float)
    row["positive_days_2c"] = int((day > 0).sum())
    row["worst_day_2c"] = round(float(day.min()), 4) if len(day) else 0.0
    row["best_day_2c"] = round(float(day.max()), 4) if len(day) else 0.0
    row["max_day_trade_frac"] = round(float(trades.groupby("day").size().max() / len(trades)), 4) if len(trades) else 0.0
    row["days"] = ",".join(str(x) for x in sorted(trades["day"].astype(str).unique())) if len(trades) else ""
    return row


def base_masks(df: pd.DataFrame) -> dict[str, np.ndarray]:
    q = df["data_quality_ok"].fillna(False).to_numpy(bool)
    finite = np.isfinite(pd.to_numeric(df["entry_price"], errors="coerce").to_numpy(float))
    return {
        "quality": q & finite,
        "fresh": q
        & pd.to_numeric(df["btc_spot_age_sec"], errors="coerce").fillna(9999).le(120).to_numpy(bool)
        & pd.to_numeric(df["lookback_age_1_0m_sec"], errors="coerce").fillna(9999).le(240).to_numpy(bool),
    }


def candidates(df: pd.DataFrame) -> Iterable[Candidate]:
    b = base_masks(df)
    quality = b["quality"]
    fresh = b["fresh"]
    spread = pd.to_numeric(df["spread_cents"], errors="coerce")
    entry = pd.to_numeric(df["entry_price"], errors="coerce")
    visible = pd.to_numeric(df["visible_qty"], errors="coerce")
    ttl = pd.to_numeric(df["ttl_min"], errors="coerce")
    fairp = pd.to_numeric(df["side_fair_p"], errors="coerce")
    fee_edge = pd.to_numeric(df["fee_edge"], errors="coerce")
    rv_ratio = pd.to_numeric(df["rv_ratio_15_60"], errors="coerce")
    dist = pd.to_numeric(df["abs_distance_bps"], errors="coerce")
    rr = pd.to_numeric(df["rr"], errors="coerce")
    mp = pd.to_numeric(df["side_micropressure"], errors="coerce")
    depth = pd.to_numeric(df["side_depth_imbalance"], errors="coerce")
    quote_speed = pd.to_numeric(df["quote_speed_cents"], errors="coerce").fillna(0)
    btc1 = pd.to_numeric(df["side_btc_1m_bps"], errors="coerce")
    btc3 = pd.to_numeric(df["side_btc_3m_bps"], errors="coerce")
    btc5 = pd.to_numeric(df["side_btc_5m_bps"], errors="coerce")
    mid05 = pd.to_numeric(df["side_mid_chg_05m"], errors="coerce")
    mid1 = pd.to_numeric(df["side_mid_chg_1m"], errors="coerce")
    mid2 = pd.to_numeric(df["side_mid_chg_2m"], errors="coerce")
    mid3 = pd.to_numeric(df["side_mid_chg_3m"], errors="coerce")
    slope = pd.to_numeric(df["side_local_slope_into_money"], errors="coerce")
    curv = pd.to_numeric(df["side_curvature_favor"], errors="coerce")
    mono_prev = pd.to_numeric(df["monotone_violation_prev"], errors="coerce").fillna(0)
    mono_next = pd.to_numeric(df["monotone_violation_next"], errors="coerce").fillna(0)
    rank_pct = pd.to_numeric(df["rank_pct"], errors="coerce")

    # H1: high-confidence fee edge.  This is economically coherent: only buy
    # when our causal BTC/RV fair probability clears the exchange price by fees
    # plus an execution buffer.
    for fp in [0.75, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97]:
        for edge in [0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]:
            for ttl_lo, ttl_hi in [(0, 15), (2, 15), (4, 15), (2, 10), (5, 12)]:
                for entry_hi in [0.60, 0.70, 0.80, 0.90, 0.97]:
                    for sp in [1, 2]:
                        mask = (
                            quality
                            & spread.le(sp).to_numpy(bool)
                            & visible.ge(1).to_numpy(bool)
                            & entry.between(0.03, entry_hi).to_numpy(bool)
                            & ttl.between(ttl_lo, ttl_hi).to_numpy(bool)
                            & fairp.ge(fp).to_numpy(bool)
                            & fee_edge.ge(edge).to_numpy(bool)
                        )
                        yield Candidate(
                            f"H1_fee_fp{fp:.2f}_edge{edge:.2f}_ttl{ttl_lo}-{ttl_hi}_e{entry_hi:.2f}_s{sp}",
                            "fair_fee_edge",
                            "Causal fair probability exceeds executable price plus fee/buffer.",
                            mask,
                        )

    # H2: fair edge only when distance is not a near-strike coinflip and
    # realized volatility is not expanding too much.
    for fp in [0.60, 0.70, 0.80, 0.90]:
        for edge in [0.04, 0.08, 0.12, 0.16]:
            for d_lo, d_hi in [(4, 80), (8, 60), (12, 45), (20, 90)]:
                for rv_cap in [0.6, 0.8, 1.0, 1.25]:
                    mask = (
                        fresh
                        & spread.le(2).to_numpy(bool)
                        & visible.ge(5).to_numpy(bool)
                        & entry.between(0.05, 0.90).to_numpy(bool)
                        & ttl.between(2, 12).to_numpy(bool)
                        & fairp.ge(fp).to_numpy(bool)
                        & fee_edge.ge(edge).to_numpy(bool)
                        & dist.between(d_lo, d_hi).to_numpy(bool)
                        & rv_ratio.le(rv_cap).to_numpy(bool)
                    )
                    yield Candidate(
                        f"H2_bounded_fp{fp:.2f}_edge{edge:.2f}_d{d_lo}-{d_hi}_rv{rv_cap}",
                        "bounded_fair_value",
                        "Fair-value signal filtered to avoid near-strike/noisy high-RV regimes.",
                        mask,
                    )

    # H3: micropressure continuation.  This is a market-only signal.
    for ttl_lo, ttl_hi in [(1, 6), (2, 8), (4, 10), (6, 12)]:
        for mpcut in [0.002, 0.005, 0.008, 0.012, 0.02]:
            for dcut in [-0.5, 0.0, 0.25, 0.50, 0.75]:
                for vcut in [1, 10, 25, 50, 100]:
                    for e_lo, e_hi in [(0.10, 0.60), (0.20, 0.80), (0.40, 0.95), (0.55, 0.95)]:
                        mask = (
                            quality
                            & spread.le(2).to_numpy(bool)
                            & visible.ge(vcut).to_numpy(bool)
                            & entry.between(e_lo, e_hi).to_numpy(bool)
                            & ttl.between(ttl_lo, ttl_hi).to_numpy(bool)
                            & mp.ge(mpcut).to_numpy(bool)
                            & depth.ge(dcut).to_numpy(bool)
                            & quote_speed.le(3).to_numpy(bool)
                        )
                        yield Candidate(
                            f"H3_mp{mpcut}_depth{dcut}_v{vcut}_e{e_lo:.2f}-{e_hi:.2f}_ttl{ttl_lo}-{ttl_hi}",
                            "micropressure",
                            "Orderbook pressure and depth favor the trade side with stable top of book.",
                            mask,
                        )

    # H4: BTC move confirmation but only after the contract has not already
    # fully repriced.  This avoids chasing every spot uptick.
    for btc_cut in [2, 3, 5, 8, 12]:
        for lag_mid in [-0.10, -0.05, -0.02, 0.0, 0.03]:
            for ttl_lo, ttl_hi in [(6, 15), (8, 15), (4, 12), (2, 10)]:
                for e_hi in [0.55, 0.65, 0.75, 0.85]:
                    mask = (
                        fresh
                        & spread.le(2).to_numpy(bool)
                        & visible.ge(1).to_numpy(bool)
                        & entry.between(0.05, e_hi).to_numpy(bool)
                        & ttl.between(ttl_lo, ttl_hi).to_numpy(bool)
                        & btc5.ge(btc_cut).to_numpy(bool)
                        & mid1.ge(lag_mid).to_numpy(bool)
                        & mid3.le(0.25).to_numpy(bool)
                    )
                    yield Candidate(
                        f"H4_btc5_{btc_cut}_mid1_{lag_mid}_e{e_hi}_ttl{ttl_lo}-{ttl_hi}",
                        "btc_confirmation",
                        "Spot move points to this side while contract has not fully chased.",
                        mask,
                    )

    # H5: path snapback / panic reversal.
    for ttl_hi in [4, 6, 8, 10]:
        for drop in [-0.30, -0.20, -0.12, -0.08, -0.05]:
            for bounce in [0.02, 0.04, 0.06, 0.08]:
                for e_hi in [0.50, 0.60, 0.75, 0.90]:
                    mask = (
                        quality
                        & spread.le(2).to_numpy(bool)
                        & visible.ge(5).to_numpy(bool)
                        & entry.between(0.05, e_hi).to_numpy(bool)
                        & ttl.between(1, ttl_hi).to_numpy(bool)
                        & mid3.le(drop).to_numpy(bool)
                        & mid05.ge(bounce).to_numpy(bool)
                        & rr.ge(0.25).to_numpy(bool)
                    )
                    yield Candidate(
                        f"H5_snap_drop{drop}_bounce{bounce}_e{e_hi}_ttl{ttl_hi}",
                        "path_snapback",
                        "Same-contract washout followed by immediate top-of-book recovery.",
                        mask,
                    )

    # H6: surface kink / adjacent strike pressure.  This tests whether local
    # cross-contract surface shape contains exploitable structural information.
    for s_cut in [0.00, 0.02, 0.04, 0.06, 0.10]:
        for c_cut in [0.00, 0.03, 0.06, 0.10]:
            for rank_lo, rank_hi in [(0.05, 0.95), (0.15, 0.85), (0.30, 0.70)]:
                for e_lo, e_hi in [(0.10, 0.70), (0.30, 0.90), (0.50, 0.95)]:
                    mask = (
                        quality
                        & spread.le(2).to_numpy(bool)
                        & visible.ge(5).to_numpy(bool)
                        & entry.between(e_lo, e_hi).to_numpy(bool)
                        & ttl.between(2, 12).to_numpy(bool)
                        & rank_pct.between(rank_lo, rank_hi).to_numpy(bool)
                        & slope.ge(s_cut).to_numpy(bool)
                        & curv.ge(c_cut).to_numpy(bool)
                    )
                    yield Candidate(
                        f"H6_surface_s{s_cut}_c{c_cut}_r{rank_lo}-{rank_hi}_e{e_lo}-{e_hi}",
                        "surface_kink",
                        "Adjacent strike surface locally supports the chosen side.",
                        mask,
                    )

    # H7: monotonicity violations.  These are rare, so treat as exploratory.
    for e_hi in [0.60, 0.80, 0.95]:
        for ttl_lo, ttl_hi in [(0, 15), (2, 10), (5, 12)]:
            mask = (
                quality
                & spread.le(2).to_numpy(bool)
                & visible.ge(1).to_numpy(bool)
                & entry.between(0.05, e_hi).to_numpy(bool)
                & ttl.between(ttl_lo, ttl_hi).to_numpy(bool)
                & (mono_prev.add(mono_next).ge(1)).to_numpy(bool)
            )
            yield Candidate(
                f"H7_mono_violation_e{e_hi}_ttl{ttl_lo}-{ttl_hi}",
                "surface_monotonicity",
                "Same-timestamp adjacent strike monotonicity violation.",
                mask,
            )

    # H8: combined high-confidence structure after fair edge and surface agree.
    for fp in [0.80, 0.90, 0.95]:
        for edge in [0.06, 0.10, 0.15]:
            for s_cut in [0.00, 0.03, 0.06]:
                for mp_cut in [0.0, 0.003, 0.008]:
                    mask = (
                        quality
                        & spread.le(2).to_numpy(bool)
                        & visible.ge(5).to_numpy(bool)
                        & entry.between(0.08, 0.95).to_numpy(bool)
                        & ttl.between(2, 12).to_numpy(bool)
                        & fairp.ge(fp).to_numpy(bool)
                        & fee_edge.ge(edge).to_numpy(bool)
                        & slope.ge(s_cut).fillna(False).to_numpy(bool)
                        & mp.ge(mp_cut).fillna(False).to_numpy(bool)
                    )
                    yield Candidate(
                        f"H8_fair_surface_fp{fp}_edge{edge}_s{s_cut}_mp{mp_cut}",
                        "fair_surface_combo",
                        "Fair-value edge is confirmed by local surface and orderbook pressure.",
                        mask,
                    )


def fit_lodo_models(df: pd.DataFrame, max_train_rows: int = 120_000) -> pd.DataFrame:
    feature_cols = [
        "entry_price",
        "ttl_min",
        "spread_cents",
        "visible_qty",
        "opp_visible_qty",
        "visible_ratio",
        "rr",
        "side_fair_p",
        "fee_edge",
        "fair_edge_cents",
        "rv_60m",
        "rv_15m",
        "rv_ratio_15_60",
        "abs_distance_bps",
        "side_btc_1m_bps",
        "side_btc_3m_bps",
        "side_btc_5m_bps",
        "side_mid_chg_05m",
        "side_mid_chg_1m",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "side_micropressure",
        "side_depth_imbalance",
        "quote_speed_cents",
        "rank_pct",
        "side_local_slope_into_money",
        "side_curvature_favor",
        "surface_mid_z",
    ]
    work = df.copy()
    for col in feature_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work["model_p_lodo"] = np.nan
    work["model_edge_lodo"] = np.nan
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import make_pipeline
    except Exception as exc:
        print(f"sklearn unavailable, skipping model path: {exc}")
        return work

    for day in sorted(work["day"].astype(str).unique()):
        train = work["day"].astype(str).ne(day)
        test = work["day"].astype(str).eq(day)
        # Downsample to one row per event/side/market minute-ish timestamp to
        # reduce duplicate quote autocorrelation in the learner.
        train_df = work.loc[train].copy()
        test_df = work.loc[test].copy()
        if train_df["win"].nunique() < 2 or len(test_df) == 0:
            continue
        if len(train_df) > max_train_rows:
            train_df = train_df.sample(n=max_train_rows, random_state=17)
        clf = make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingClassifier(
                max_iter=80,
                learning_rate=0.05,
                max_leaf_nodes=15,
                l2_regularization=0.2,
                random_state=17,
            ),
        )
        clf.fit(train_df[feature_cols], train_df["win"].astype(int))
        p = clf.predict_proba(test_df[feature_cols])[:, 1]
        work.loc[test, "model_p_lodo"] = p
        work.loc[test, "model_edge_lodo"] = p - work.loc[test, "entry_price"].to_numpy(float) - work.loc[test, "entry_fee"].to_numpy(float)
    return work


def model_candidates(df: pd.DataFrame) -> Iterable[Candidate]:
    quality = base_masks(df)["quality"]
    spread = pd.to_numeric(df["spread_cents"], errors="coerce")
    entry = pd.to_numeric(df["entry_price"], errors="coerce")
    visible = pd.to_numeric(df["visible_qty"], errors="coerce")
    ttl = pd.to_numeric(df["ttl_min"], errors="coerce")
    p = pd.to_numeric(df["model_p_lodo"], errors="coerce")
    edge = pd.to_numeric(df["model_edge_lodo"], errors="coerce")
    fairp = pd.to_numeric(df["side_fair_p"], errors="coerce")
    for pcut in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        for ecut in [0.02, 0.04, 0.06, 0.08, 0.10, 0.15]:
            for entry_hi in [0.60, 0.75, 0.90, 0.97]:
                for ttl_lo, ttl_hi in [(0, 15), (2, 12), (5, 12)]:
                    mask = (
                        quality
                        & spread.le(2).to_numpy(bool)
                        & visible.ge(1).to_numpy(bool)
                        & entry.between(0.03, entry_hi).to_numpy(bool)
                        & ttl.between(ttl_lo, ttl_hi).to_numpy(bool)
                        & p.ge(pcut).to_numpy(bool)
                        & edge.ge(ecut).to_numpy(bool)
                    )
                    yield Candidate(
                        f"M_lodo_p{pcut}_edge{ecut}_e{entry_hi}_ttl{ttl_lo}-{ttl_hi}",
                        "lodo_ml_edge",
                        "Leave-one-day-out ML predicted edge inside Apr1-4 only.",
                        mask,
                    )
    for pcut in [0.60, 0.70, 0.80]:
        for fp in [0.75, 0.85, 0.90, 0.95]:
            mask = (
                quality
                & spread.le(2).to_numpy(bool)
                & visible.ge(1).to_numpy(bool)
                & entry.between(0.05, 0.90).to_numpy(bool)
                & ttl.between(2, 12).to_numpy(bool)
                & p.ge(pcut).to_numpy(bool)
                & fairp.ge(fp).to_numpy(bool)
            )
            yield Candidate(
                f"M_lodo_p{pcut}_fair{fp}",
                "lodo_ml_plus_fair",
                "LODO ML edge agrees with causal fair probability.",
                mask,
            )


def evaluate_all(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    generated = itertools.chain(candidates(df), model_candidates(df))
    for i, cand in enumerate(generated, 1):
        if i % 1000 == 0:
            print(f"evaluated {i} candidate rules", flush=True)
        trades = first_per_event(df, cand.mask)
        if len(trades) < 5:
            continue
        row = summarize_trades(trades, cand.name, cand.family, cand.rationale)
        row["candidate_index"] = i
        rows.append(row)
        if (
            row["trades"] >= 12
            and row["pnl_2c"] > 0
            and row["positive_days_2c"] >= 3
            and row["worst_day_2c"] > -2.0
            and row["max_day_trade_frac"] <= 0.55
        ):
            tagged = trades.copy()
            tagged["candidate_name"] = cand.name
            tagged["candidate_family"] = cand.family
            trade_frames.append(tagged)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary, pd.DataFrame()
    summary["robust_score"] = (
        summary["pnl_2c"].astype(float)
        + 1.25 * summary["sharpe_2c"].astype(float)
        + 0.5 * summary["positive_days_2c"].astype(float)
        + 0.5 * summary["worst_day_2c"].astype(float)
        - 0.25 * summary["max_day_trade_frac"].astype(float)
        + 0.20 * summary["rop_2c"].astype(float)
    )
    summary = summary.sort_values(["robust_score", "pnl_2c"], ascending=False).reset_index(drop=True)
    trades_out = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return summary, trades_out


def write_day_tables(summary: pd.DataFrame, trades: pd.DataFrame, out_dir: Path) -> None:
    if trades.empty:
        return
    top_names = summary.head(60)["name"].tolist()
    daily = (
        trades[trades["candidate_name"].isin(top_names)]
        .groupby(["candidate_name", "candidate_family", "day"], as_index=False)
        .agg(trades=("pnl_2c", "size"), pnl_0c=("pnl_0c", "sum"), pnl_1c=("pnl_1c", "sum"), pnl_2c=("pnl_2c", "sum"), pnl_3c=("pnl_3c", "sum"), win_rate=("win", "mean"))
    )
    daily.to_csv(out_dir / "deep_structure_daily_top60.csv", index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--skip-model", action="store_true", help="Skip leave-one-day-out ML path for faster rule-only search.")
    args = ap.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    df = prepare(args.input)
    print(
        f"loaded study rows={len(df):,} events={df['event_ticker'].nunique():,} days={sorted(df['day'].astype(str).unique())}",
        flush=True,
    )
    if args.skip_model:
        df["model_p_lodo"] = np.nan
        df["model_edge_lodo"] = np.nan
    else:
        df = fit_lodo_models(df)
    df.to_parquet(out_dir / "model_lodo_predictions.parquet", index=False)
    summary, trades = evaluate_all(df)
    summary.to_csv(out_dir / "deep_structure_summary.csv", index=False)
    if not summary.empty:
        top = summary[
            (summary["trades"] >= 12)
            & (summary["pnl_2c"] > 0)
            & (summary["positive_days_2c"] >= 3)
            & (summary["worst_day_2c"] > -2.0)
            & (summary["max_day_trade_frac"] <= 0.55)
        ].copy()
        top.to_csv(out_dir / "deep_structure_top.csv", index=False)
    if not trades.empty:
        trades.to_parquet(out_dir / "deep_structure_trades.parquet", index=False)
    write_day_tables(summary, trades, out_dir)
    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# BTC15M Apr 1-4 Deep Structure Search",
                "",
                f"Generated: {datetime.utcnow().isoformat()}Z",
                "",
                "This run is study-only. It uses Apr 1-4 rows from the prepared side-candidate parquet and does not evaluate Apr 5-7.",
                "",
                f"Rows: {len(df):,}",
                f"Events: {df['event_ticker'].nunique():,}",
                "",
                "Files:",
                "- `deep_structure_summary.csv`: all evaluated candidate rules.",
                "- `deep_structure_top.csv`: candidates passing strict Apr1-4 robustness filters.",
                "- `deep_structure_daily_top60.csv`: day-level stats for top candidates.",
                "- `model_lodo_predictions.parquet`: leave-one-day-out ML predictions.",
            ]
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_dir}")
    if not summary.empty:
        print(summary.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
