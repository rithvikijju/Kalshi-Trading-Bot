#!/usr/bin/env python3
"""Research-only BTC ML model candidate prototype.

This script intentionally avoids live executors and websocket-capture holdouts.

BTC15M default:
    Use Apr 1-4 only from the Predexon side-candidate table.  Apr 5-7 remains
    untouched for validation of a frozen candidate.

BTC1H default:
    Use historical research DuckDB rows only.  Labels are BTC minute proxy
    settlement, so this is discovery-grade rather than promotion-grade.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


BTC15M_INPUT = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_fee_edge_april_validation_latest"
    / "april_side_candidates_fee_features.parquet"
)
BTC1H_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "btc_ml_candidate_prototype_20260515"

BTC15M_START = pd.Timestamp("2026-04-01T00:00:00Z")
BTC15M_TRAIN_END = pd.Timestamp("2026-04-04T00:00:00Z")
BTC15M_INTERNAL_VAL_END = pd.Timestamp("2026-04-05T00:00:00Z")

BTC1H_TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
BTC1H_INTERNAL_VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")

LEAK_COLUMNS = {
    "provider",
    "fidelity",
    "market_ticker",
    "event_ticker",
    "series_ticker",
    "timestamp_utc",
    "available_at",
    "open_time",
    "close_time",
    "btc_time",
    "result",
    "status",
    "win",
    "pnl",
    "pnl_2c",
    "split",
    "eval_split",
    "day",
    "entry_bucket",
    "yes_bid_levels",
    "yes_ask_levels",
    "settlement_spot",
    "close_spot",
}

BTC15M_FEATURES = [
    "entry_price",
    "entry_fee",
    "premium",
    "rr",
    "spread_cents",
    "visible_qty",
    "opp_visible_qty",
    "visible_ratio",
    "ttl_min",
    "best_bid_cents",
    "best_ask_cents",
    "bid_depth",
    "ask_depth",
    "yes_bid_qty",
    "yes_ask_qty",
    "no_bid_qty",
    "no_ask_qty",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_mid",
    "microprice",
    "micropressure",
    "quote_speed_cents",
    "book_imbalance",
    "side_depth_imbalance",
    "side_micropressure",
    "yes_mid_chg_0_5m",
    "yes_mid_chg_1_0m",
    "yes_mid_chg_2_0m",
    "yes_mid_chg_3_0m",
    "yes_mid_chg_5_0m",
    "side_mid_chg_05m",
    "side_mid_chg_1m",
    "side_mid_chg_2m",
    "side_mid_chg_3m",
    "spread_chg_1_0m",
    "btc_spot_age_sec",
    "btc_ret_1m_bps",
    "btc_ret_3m_bps",
    "btc_ret_5m_bps",
    "side_btc_1m_bps",
    "side_btc_3m_bps",
    "side_btc_5m_bps",
    "btc_lookback_age_1m_sec",
    "btc_lookback_age_3m_sec",
    "btc_lookback_age_5m_sec",
    "rv_60m",
    "rv_15m",
    "rv_ratio_15_60",
    "lognormal_p_yes",
    "side_fair_p",
    "fair_edge_cents",
    "fee_edge",
    "distance_bps",
    "side_distance_bps",
    "lookback_age_0_5m_sec",
    "lookback_age_1_0m_sec",
    "lookback_age_2_0m_sec",
    "lookback_age_3_0m_sec",
    "lookback_age_5_0m_sec",
    "spread_lookback_age_1_0m_sec",
]

BTC1H_FEATURES = [
    "entry_price",
    "entry_fee",
    "spread_cents",
    "ttl_min",
    "yes_bid_close",
    "yes_ask_close",
    "yes_mid",
    "market_mid",
    "side_yes",
    "spot",
    "distance_bps",
    "side_distance_bps",
    "rv_15m",
    "rv_60m",
    "rv_1d",
    "rkurt_60m",
    "btc_ret_5m_bps",
    "btc_ret_15m_bps",
    "btc_ret_60m_bps",
    "side_btc_5m_bps",
    "side_btc_15m_bps",
    "side_btc_60m_bps",
    "lognormal_p_yes",
    "side_fair_p",
    "fair_edge_cents",
]


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str
    notes: str


def utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def max_drawdown(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if x.empty:
        return 0.0
    return float((x - x.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 0.0:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = utc(out["close_time"])
    avail = utc(out["available_at"])
    close_minutes = close.dt.hour.astype(float) * 60.0 + close.dt.minute.astype(float)
    avail_minutes = avail.dt.hour.astype(float) * 60.0 + avail.dt.minute.astype(float)
    out["close_day_frac_sin"] = np.sin(2.0 * np.pi * close_minutes / 1440.0)
    out["close_day_frac_cos"] = np.cos(2.0 * np.pi * close_minutes / 1440.0)
    out["avail_day_frac_sin"] = np.sin(2.0 * np.pi * avail_minutes / 1440.0)
    out["avail_day_frac_cos"] = np.cos(2.0 * np.pi * avail_minutes / 1440.0)
    out["close_hour"] = close.dt.hour.astype(float)
    out["close_quarter"] = (close.dt.minute // 15).astype(float)
    return out


def feature_columns(df: pd.DataFrame, base: list[str]) -> list[str]:
    candidates = base + [
        "side_yes",
        "close_day_frac_sin",
        "close_day_frac_cos",
        "avail_day_frac_sin",
        "avail_day_frac_cos",
        "close_hour",
        "close_quarter",
    ]
    cols = [c for c in candidates if c in df.columns and c not in LEAK_COLUMNS]
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return [c for c in cols if df[c].notna().any()]


def first_per_event(df: pd.DataFrame, score_col: str = "pred_ev") -> pd.DataFrame:
    if df.empty:
        return df.copy()
    sort_cols = ["event_ticker", "available_at", score_col, "pred_win_prob", "side"]
    ascending = [True, True, False, False, True]
    return (
        df.sort_values(sort_cols, ascending=ascending, na_position="last")
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def metric_row(frame: pd.DataFrame, pnl_col: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "rop_pct": 0.0,
            "win_rate_pct": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
        }
    pnl = pd.to_numeric(frame[pnl_col], errors="coerce").fillna(0.0)
    premium = pd.to_numeric(frame["entry_price"], errors="coerce").fillna(0.0)
    wins = pd.to_numeric(frame["win"], errors="coerce").fillna(0.0)
    return {
        "trades": int(len(frame)),
        "pnl": round(float(pnl.sum()), 4),
        "premium": round(float(premium.sum()), 4),
        "rop_pct": round(float(pnl.sum() / premium.sum() * 100.0), 2) if float(premium.sum()) > 0 else 0.0,
        "win_rate_pct": round(float(wins.mean() * 100.0), 2),
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
    }


def lognormal_p_above(strike: np.ndarray, spot: np.ndarray, ttl_min: np.ndarray, annual_vol: np.ndarray) -> np.ndarray:
    strike = np.asarray(strike, dtype=float)
    spot = np.asarray(spot, dtype=float)
    ttl_min = np.asarray(ttl_min, dtype=float)
    annual_vol = np.asarray(annual_vol, dtype=float)
    out = np.full(len(strike), np.nan, dtype=float)
    valid = (strike > 0) & (spot > 0) & (ttl_min > 0) & (annual_vol > 0)
    if not valid.any():
        return out
    variance = (annual_vol[valid] ** 2) * ttl_min[valid] / (365.0 * 24.0 * 60.0)
    sigma = np.sqrt(np.maximum(variance, 1e-12))
    z = (np.log(strike[valid] / spot[valid]) + 0.5 * variance) / sigma
    out[valid] = np.asarray([0.5 * math.erfc(float(v) / math.sqrt(2.0)) for v in z], dtype=float)
    return out


def load_btc15m(path: Path, max_rows: int | None) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    df = pd.read_parquet(path)
    df["close_time"] = utc(df["close_time"])
    df["available_at"] = utc(df["available_at"])
    df = df[(df["close_time"] >= BTC15M_START) & (df["close_time"] < BTC15M_INTERNAL_VAL_END)].copy()
    mask = (
        df["data_quality_ok"].astype(bool)
        & df["available_at"].lt(df["close_time"])
        & pd.to_numeric(df["spread_cents"], errors="coerce").le(2.0)
        & pd.to_numeric(df["visible_qty"], errors="coerce").ge(1.0)
        & pd.to_numeric(df["ttl_min"], errors="coerce").between(0.0, 15.0, inclusive="both")
        & pd.to_numeric(df["entry_price"], errors="coerce").between(0.01, 0.99, inclusive="both")
        & pd.to_numeric(df["btc_spot_age_sec"], errors="coerce").le(90.0)
    )
    df = df[mask].copy()
    df["side_yes"] = df["side"].astype(str).str.lower().eq("yes").astype(float)
    df["entry_fee"] = pd.to_numeric(df["entry_fee"], errors="coerce").fillna(0.0)
    entry_2c = (pd.to_numeric(df["entry_price"], errors="coerce") + 0.02).clip(upper=0.99)
    fee_2c = entry_2c.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    df["pnl_2c"] = np.where(df["win"].astype(bool), 1.0 - entry_2c - fee_2c, -entry_2c - fee_2c)
    df["split"] = np.where(df["close_time"] < BTC15M_TRAIN_END, "train", "internal_val")
    df = add_time_features(df)
    features = feature_columns(df, BTC15M_FEATURES)
    if max_rows and len(df) > max_rows:
        parts = []
        for _, group in df.groupby("split", sort=False):
            n = max(1, int(max_rows * len(group) / len(df)))
            parts.append(group.sample(n=min(n, len(group)), random_state=15))
        df = pd.concat(parts, ignore_index=True).sort_values(["close_time", "available_at"]).reset_index(drop=True)
    meta = {
        "market": "btc15m",
        "input": str(path),
        "label_source": "Kalshi/Predexon settlement fields already present in candidate parquet",
        "holdouts_preserved": ["Apr 5-7 BTC15M validation", "websocket capture final holdout"],
    }
    return df, features, meta


def load_btc1h(db_path: Path, max_rows: int | None) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    con = duckdb.connect(str(db_path), read_only=True)
    quotes = con.execute(
        """
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, m.open_time, m.close_time, m.event_open_time,
               m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE m.is_hourly_kxbtcd
          AND m.is_cumulative
          AND date_diff('second', q.available_at, m.close_time) / 60.0 BETWEEN 5.0 AND 20.0
          AND q.spread_cents <= 2.0
          AND (q.yes_ask_exe BETWEEN 0.25 AND 0.75 OR q.no_ask_exe BETWEEN 0.25 AND 0.75)
        ORDER BY q.available_at, q.market_ticker
        """
    ).fetchdf()
    btc = con.execute(
        """
        SELECT available_at, close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        ORDER BY available_at
        """
    ).fetchdf()
    con.close()
    if quotes.empty or btc.empty:
        raise SystemExit("missing BTC1H historical tables")
    for col in ["available_at", "ts_end", "open_time", "close_time", "event_open_time"]:
        quotes[col] = utc(quotes[col])
    btc["available_at"] = utc(btc["available_at"])
    btc = btc.sort_values("available_at").reset_index(drop=True)
    btc["btc_ret_5m_bps"] = np.log(btc["close"] / btc["close"].shift(5)) * 10000.0
    btc["btc_ret_15m_bps"] = np.log(btc["close"] / btc["close"].shift(15)) * 10000.0
    btc["btc_ret_60m_bps"] = np.log(btc["close"] / btc["close"].shift(60)) * 10000.0

    left = quotes.sort_values("available_at").reset_index(drop=True)
    now_btc = btc.rename(columns={"available_at": "btc_available_at", "close": "spot"})
    frame = pd.merge_asof(
        left,
        now_btc,
        left_on="available_at",
        right_on="btc_available_at",
        direction="backward",
        tolerance=pd.Timedelta(minutes=3),
    )
    settle_btc = btc[["available_at", "close"]].rename(columns={"available_at": "settle_available_at", "close": "settlement_spot"})
    frame = pd.merge_asof(
        frame.sort_values("close_time"),
        settle_btc,
        left_on="close_time",
        right_on="settle_available_at",
        direction="backward",
        tolerance=pd.Timedelta(minutes=3),
    ).sort_values(["close_time", "available_at", "market_ticker"])

    frame = frame.dropna(subset=["spot", "settlement_spot", "floor_strike"]).copy()
    frame["ttl_min"] = (frame["close_time"] - frame["available_at"]).dt.total_seconds() / 60.0
    frame["yes_mid"] = (pd.to_numeric(frame["yes_bid_close"], errors="coerce") + pd.to_numeric(frame["yes_ask_close"], errors="coerce")) / 2.0
    frame["market_mid"] = frame["yes_mid"]
    frame["lognormal_p_yes"] = lognormal_p_above(
        frame["floor_strike"].to_numpy(dtype=float),
        frame["spot"].to_numpy(dtype=float),
        frame["ttl_min"].to_numpy(dtype=float),
        frame["rv_60m"].to_numpy(dtype=float),
    )
    frame["yes_win"] = frame["settlement_spot"] >= frame["floor_strike"]
    yes = frame.copy()
    yes["side"] = "yes"
    yes["side_yes"] = 1.0
    yes["entry_price"] = pd.to_numeric(yes["yes_ask_exe"], errors="coerce")
    yes["win"] = yes["yes_win"]
    no = frame.copy()
    no["side"] = "no"
    no["side_yes"] = 0.0
    no["entry_price"] = pd.to_numeric(no["no_ask_exe"], errors="coerce")
    no["win"] = ~no["yes_win"]
    out = pd.concat([yes, no], ignore_index=True)
    out = out[pd.to_numeric(out["entry_price"], errors="coerce").between(0.25, 0.75, inclusive="both")].copy()
    out["entry_fee"] = out["entry_price"].map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    out["pnl"] = np.where(out["win"].astype(bool), 1.0 - out["entry_price"] - out["entry_fee"], -out["entry_price"] - out["entry_fee"])
    out["side_fair_p"] = np.where(out["side"].eq("yes"), out["lognormal_p_yes"], 1.0 - out["lognormal_p_yes"])
    out["fair_edge_cents"] = 100.0 * (out["side_fair_p"] - out["entry_price"])
    out["distance_bps"] = (out["spot"] / out["floor_strike"] - 1.0) * 10000.0
    out["side_distance_bps"] = np.where(out["side"].eq("yes"), out["distance_bps"], -out["distance_bps"])
    for minutes in (5, 15, 60):
        out[f"side_btc_{minutes}m_bps"] = np.where(out["side"].eq("yes"), out[f"btc_ret_{minutes}m_bps"], -out[f"btc_ret_{minutes}m_bps"])
    out["split"] = np.select(
        [out["available_at"] < BTC1H_TRAIN_END, out["available_at"] < BTC1H_INTERNAL_VAL_END],
        ["train", "internal_val"],
        default="research_test",
    )
    out = out[out["split"].isin(["train", "internal_val"])].copy()
    out = add_time_features(out)
    features = feature_columns(out, BTC1H_FEATURES)
    if max_rows and len(out) > max_rows:
        parts = []
        for _, group in out.groupby("split", sort=False):
            n = max(1, int(max_rows * len(group) / len(out)))
            parts.append(group.sample(n=min(n, len(group)), random_state=16))
        out = pd.concat(parts, ignore_index=True).sort_values(["close_time", "available_at"]).reset_index(drop=True)
    meta = {
        "market": "btc1h",
        "input": str(db_path),
        "label_source": "BTC minute proxy at event close, not official settlement",
        "holdouts_preserved": ["websocket capture final holdout"],
    }
    return out, features, meta


def fit_candidates(train: pd.DataFrame, features: list[str]) -> list[tuple[Candidate, Any]]:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    candidates = [
        (
            Candidate(
                "hgb_tabular",
                "tree",
                "Small histogram GBDT on causal quote, BTC, fair-value, and time features.",
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                HistGradientBoostingClassifier(
                    max_iter=140,
                    learning_rate=0.045,
                    max_leaf_nodes=16,
                    l2_regularization=0.2,
                    random_state=15,
                ),
            ),
        ),
        (
            Candidate(
                "logistic_l2_calibrator",
                "linear",
                "Regularized linear probability layer; useful as a live-safe calibration baseline.",
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                LogisticRegression(C=0.20, max_iter=500, class_weight="balanced", random_state=15),
            ),
        ),
        (
            Candidate(
                "mlp_32_16",
                "nn",
                "Tiny tabular MLP, constrained to stay exportable and causal.",
            ),
            make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                MLPClassifier(
                    hidden_layer_sizes=(32, 16),
                    alpha=0.03,
                    learning_rate_init=5e-4,
                    max_iter=180,
                    early_stopping=True,
                    random_state=15,
                ),
            ),
        ),
    ]
    fitted = []
    for candidate, model in candidates:
        start = time.time()
        model.fit(train[features], train["win"].astype(int))
        print(f"trained {candidate.name} in {time.time() - start:.1f}s", flush=True)
        fitted.append((candidate, model))
    return fitted


def score_model(model: Any, frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    out = frame.copy()
    out["pred_win_prob"] = model.predict_proba(out[features])[:, 1].astype(float)
    entry = pd.to_numeric(out["entry_price"], errors="coerce").astype(float)
    fee = pd.to_numeric(out.get("entry_fee", 0.0), errors="coerce").fillna(0.0).astype(float)
    out["pred_ev"] = out["pred_win_prob"] * (1.0 - entry - fee) + (1.0 - out["pred_win_prob"]) * (-entry - fee)
    return out


def choose_gate(scored: pd.DataFrame, min_trades: int) -> dict[str, float | int]:
    best: dict[str, float | int] | None = None
    for min_p in np.round(np.arange(0.54, 0.91, 0.02), 2):
        for min_ev in np.round(np.arange(0.00, 0.181, 0.01), 2):
            selected = first_per_event(scored[(scored["pred_win_prob"] >= min_p) & (scored["pred_ev"] >= min_ev)])
            if len(selected) < min_trades:
                continue
            pnl = pd.to_numeric(selected["pnl"], errors="coerce").fillna(0.0)
            score = float(pnl.sum() + 0.5 * max_drawdown(pnl) - 0.01 * max(0, min_trades * 2 - len(selected)))
            row = {
                "gate_min_p": float(min_p),
                "gate_min_ev": float(min_ev),
                "gate_trades": int(len(selected)),
                "gate_pnl": float(pnl.sum()),
                "gate_max_dd": max_drawdown(pnl),
                "gate_score": score,
            }
            if best is None or score > float(best["gate_score"]):
                best = row
    if best is None:
        return {
            "gate_min_p": 0.70,
            "gate_min_ev": 0.00,
            "gate_trades": 0,
            "gate_pnl": 0.0,
            "gate_max_dd": 0.0,
            "gate_score": -1e9,
        }
    return best


def evaluate_market(name: str, frame: pd.DataFrame, features: list[str], out_dir: Path, min_trades: int) -> dict[str, Any]:
    train = frame[frame["split"].eq("train")].copy()
    internal_val = frame[frame["split"].eq("internal_val")].copy()
    if train.empty or internal_val.empty:
        raise SystemExit(f"{name}: empty train/internal_val split")
    fitted = fit_candidates(train, features)
    summary_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    for candidate, model in fitted:
        val_scored = score_model(model, internal_val, features)
        gate = choose_gate(val_scored, min_trades=min_trades)
        for split_name, part in [("train", train), ("internal_val", internal_val)]:
            scored = score_model(model, part, features)
            selected = first_per_event(scored[(scored["pred_win_prob"] >= gate["gate_min_p"]) & (scored["pred_ev"] >= gate["gate_min_ev"])])
            selected["market_scope"] = name
            selected["candidate"] = candidate.name
            selected["gate_min_p"] = gate["gate_min_p"]
            selected["gate_min_ev"] = gate["gate_min_ev"]
            trade_frames.append(selected)
            row = {
                "market_scope": name,
                "candidate": candidate.name,
                "kind": candidate.kind,
                "split": split_name,
                "features": len(features),
                **gate,
                **metric_row(selected, "pnl"),
            }
            try:
                from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

                y = scored["win"].astype(int)
                p = scored["pred_win_prob"].astype(float).clip(1e-5, 1 - 1e-5)
                row["auc"] = round(float(roc_auc_score(y, p)), 4) if y.nunique() > 1 else 0.0
                row["brier"] = round(float(brier_score_loss(y, p)), 4)
                row["logloss"] = round(float(log_loss(y, p)), 4)
            except Exception:
                row["auc"] = row["brier"] = row["logloss"] = 0.0
            summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    summary.to_csv(out_dir / f"{name}_summary.csv", index=False)
    if not trades.empty:
        trades.to_parquet(out_dir / f"{name}_trades.parquet", index=False, compression="zstd")
    return {
        "rows": int(len(frame)),
        "events": int(frame["event_ticker"].nunique()),
        "features": features,
        "splits": frame.groupby("split")["event_ticker"].agg(rows="size", events="nunique").reset_index().to_dict("records"),
        "summary": summary.to_dict("records"),
    }


def write_report(out_dir: Path, report: dict[str, Any]) -> None:
    lines = [
        "# BTC ML Candidate Prototype",
        "",
        "Research-only prototype. No live script was modified, restarted, or read for execution.",
        "",
        "## Holdout Discipline",
        "",
        "- BTC15M uses Apr 1-3 train and Apr 4 internal validation only.",
        "- BTC15M Apr 5-7 remains untouched for a frozen-candidate validation pass.",
        "- Websocket capture remains final holdout for both BTC15M and BTC1H.",
        "- BTC1H labels in this prototype are BTC minute proxy settlement, not promotion-grade official settlement.",
        "",
        "## Results",
        "",
    ]
    for market, data in report["markets"].items():
        lines.extend(
            [
                f"### {market}",
                "",
                f"- Rows: {data['rows']:,}",
                f"- Events: {data['events']:,}",
                f"- Features: {len(data['features'])}",
                "",
                "```",
                pd.DataFrame(data["splits"]).to_string(index=False),
                "```",
                "",
                "```",
                pd.DataFrame(data["summary"]).to_string(index=False),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Candidate Interpretation",
            "",
            "- `hgb_tabular`: best first candidate when event count is limited; easy to replay causally from a row feature vector.",
            "- `logistic_l2_calibrator`: live-safe baseline and calibration sanity check; should not be beaten only by overfit thresholding.",
            "- `mlp_32_16`: smallest NN candidate worth testing before sequence models; promotion requires stability across rolling folds.",
            "",
            "## Next Frozen-Candidate Gate",
            "",
            "A candidate is frozen only after architecture, feature list, imputation, scaler, threshold grid, and execution filters are fixed from the internal split. Then it can be scored once on BTC15M Apr 5-7 or the BTC1H historical test split. Websocket replay remains last.",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["btc15m", "btc1h", "both"], default="both")
    parser.add_argument("--btc15m-input", type=Path, default=BTC15M_INPUT)
    parser.add_argument("--btc1h-db", type=Path, default=BTC1H_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-rows", type=int, default=80_000, help="smoke cap across each market; use 0 for full")
    parser.add_argument("--min-validation-trades", type=int, default=8)
    args = parser.parse_args()

    max_rows = None if args.max_rows == 0 else args.max_rows
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"created_by": "prototype_btc_ml_candidates.py", "markets": {}, "meta": {}}

    if args.market in {"btc15m", "both"}:
        frame, features, meta = load_btc15m(args.btc15m_input, max_rows=max_rows)
        report["meta"]["btc15m"] = meta
        report["markets"]["btc15m"] = evaluate_market("btc15m", frame, features, args.out_dir, args.min_validation_trades)
    if args.market in {"btc1h", "both"}:
        frame, features, meta = load_btc1h(args.btc1h_db, max_rows=max_rows)
        report["meta"]["btc1h"] = meta
        report["markets"]["btc1h"] = evaluate_market("btc1h", frame, features, args.out_dir, args.min_validation_trades)

    (args.out_dir / "manifest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_report(args.out_dir, report)
    print(f"wrote {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
