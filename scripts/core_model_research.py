#!/usr/bin/env python3
"""Research core BTC 1h fair-value probability models.

This is deliberately separate from live execution and sizing.  It evaluates
alternative p(YES) models while keeping execution rules close to the deployed
research strategy: observed bid/ask quotes, taker fees, one position per event,
and chronological train/validation/test splits.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import duckdb
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from scipy.stats import t as student_t
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR, kalshi_fee_dollars
from scripts.backtest_1hr_collected_data import build_emp_cache_fast, lognormal_p_above, vectorized_p_above


DEFAULT_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"
DEFAULT_CAPTURE_HOLDOUT = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "holdout_late_only_features.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "backtest_outputs" / "core_model_research_20260510"

TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VALIDATION_END = pd.Timestamp("2026-04-21T00:00:00Z")

MIN_EDGE_CENTS = 12.0
MAX_SPREAD_CENTS = 2.0
MIN_ENTRY = 0.25
MAX_ENTRY = 0.75
MIN_YES_P = 0.65
MAX_NO_P = 0.35
MIN_TTL_MIN = 5.0
MAX_TTL_MIN = 20.0
BRTI_DAMPENING = 0.80

EPS = 1e-12


@dataclass(frozen=True)
class ModelVariant:
    name: str
    round_no: int
    hypothesis: str
    train_days: int = 7
    emp_weight: float = 0.70
    lognormal_weight: float = 0.30
    student_weight: float = 0.0
    market_weight: float = 0.0
    event_iv_weight: float = 0.0
    brti_dampening: float = BRTI_DAMPENING
    vol_col: str = "rv_60m"
    vol_scale: float = 1.0
    drift_ret_col: str | None = None
    drift_beta: float = 0.0
    student_df: float = 5.0
    min_edge_cents: float = MIN_EDGE_CENTS
    max_spread_cents: float = MAX_SPREAD_CENTS
    min_entry: float = MIN_ENTRY
    max_entry: float = MAX_ENTRY
    min_yes_p: float = MIN_YES_P
    max_no_p: float = MAX_NO_P
    uncertainty_mult: float = 1.0
    calibrator: str | None = None
    side_bias_cents: float = 0.0


@dataclass(frozen=True)
class OpenPosition:
    row: dict


_EMP_PROB_CACHE: dict[tuple[int, float, float, float, int], np.ndarray] = {}
_LOGN_PROB_CACHE: dict[tuple[int, float, float, float, float, float], np.ndarray] = {}
_STUDENT_PROB_CACHE: dict[tuple[int, float, float, float, float, float], np.ndarray] = {}
_EVENT_IV_CACHE: dict[tuple[int, float, float], float | None] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research BTC 1h fair-value probability models.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--capture-holdout", type=Path, default=DEFAULT_CAPTURE_HOLDOUT)
    parser.add_argument("--min-ttl", type=float, default=MIN_TTL_MIN)
    parser.add_argument("--max-ttl", type=float, default=MAX_TTL_MIN)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--progress-every-events", type=int, default=50)
    parser.add_argument("--skip-calibrators", action="store_true")
    parser.add_argument("--rounds", type=int, default=3, help="Maximum hypothesis rounds to include.")
    parser.add_argument("--variant-regex", help="Optional regex filter for variant names after round selection.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tables(db_path: Path, min_ttl: float, max_ttl: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    quotes = con.execute(
        """
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, q.fidelity, q.source,
               m.open_time, m.close_time, m.event_open_time, m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE m.is_hourly_kxbtcd
          AND m.is_cumulative
          AND date_diff('second', q.available_at, m.close_time) / 60.0 >= ?
          AND date_diff('second', q.available_at, m.close_time) / 60.0 <= ?
          AND q.spread_cents <= 2.0
          AND (
              q.yes_ask_exe BETWEEN 0.25 AND 0.75
              OR q.no_ask_exe BETWEEN 0.25 AND 0.75
          )
        ORDER BY m.close_time, q.available_at, q.market_ticker
        """,
        [min_ttl, max_ttl],
    ).fetchdf()
    if quotes.empty:
        con.close()
        return quotes, pd.DataFrame()
    btc_start = quotes["event_open_time"].min() - pd.Timedelta(days=35)
    btc_end = quotes["close_time"].max() + pd.Timedelta(minutes=90)
    btc = con.execute(
        """
        SELECT available_at AS time, open, high, low, close, volume, log_ret,
               rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        WHERE available_at >= ? AND available_at <= ?
        ORDER BY available_at
        """,
        [btc_start.to_pydatetime(), btc_end.to_pydatetime()],
    ).fetchdf()
    con.close()
    for col in ("available_at", "ts_end", "open_time", "close_time", "event_open_time"):
        quotes[col] = pd.to_datetime(quotes[col], utc=True)
    btc["time"] = pd.to_datetime(btc["time"], utc=True)
    return quotes, btc


def add_btc_features(btc: pd.DataFrame) -> pd.DataFrame:
    df = btc.copy().sort_values("time").reset_index(drop=True)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    af = float(MINUTES_PER_YEAR)
    for window in (5, 10, 15, 30, 60, 120, 240, 720, 1440):
        df[f"rv_{window}m"] = df["log_ret"].rolling(window).std() * math.sqrt(af)
        df[f"rv_mean_{window}m"] = np.sqrt(df["log_ret"].pow(2).rolling(window).mean() * af)
    for span in (15, 30, 60, 120, 240, 720):
        df[f"rv_ewma_{span}m"] = np.sqrt(df["log_ret"].pow(2).ewm(span=span, adjust=False, min_periods=max(5, span // 4)).mean() * af)
    neg = df["log_ret"].clip(upper=0.0)
    pos = df["log_ret"].clip(lower=0.0)
    for window in (30, 60, 240):
        df[f"rv_down_{window}m"] = np.sqrt(2.0 * neg.pow(2).rolling(window).mean() * af)
        df[f"rv_up_{window}m"] = np.sqrt(2.0 * pos.pow(2).rolling(window).mean() * af)
    for minutes in (5, 10, 30, 60):
        df[f"log_ret_{minutes}m"] = np.log(df["close"] / df["close"].shift(minutes))
    df["rv_har_static"] = np.sqrt(
        0.50 * df["rv_60m"].pow(2)
        + 0.30 * df["rv_240m"].pow(2)
        + 0.20 * df["rv_1d"].pow(2)
    )
    df["rv_short_long_ratio"] = df["rv_60m"] / df["rv_1d"].replace(0.0, np.nan)
    return df


def fit_har_vol(btc: pd.DataFrame, train_end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    df = btc.copy()
    # Future one-hour realized variance, available only for training labels.
    future_var = df["log_ret"].shift(-60).rolling(60).sum().shift(-59)
    # The expression above is noisy for sparse data; recompute directly.
    returns = df["log_ret"].to_numpy(dtype=float)
    target = np.full(len(df), np.nan, dtype=float)
    for i in range(len(df) - 60):
        r = returns[i + 1 : i + 61]
        if np.isfinite(r).all():
            target[i] = float(np.sum(r * r) * MINUTES_PER_YEAR / 60.0)
    feature_cols = ["rv_15m", "rv_60m", "rv_240m", "rv_1d", "rv_down_60m", "rv_up_60m", "rv_short_long_ratio"]
    X = []
    y = []
    train_mask = df["time"] < train_end
    for idx, row in df[train_mask].iterrows():
        vals = [float(row.get(c, np.nan)) for c in feature_cols]
        if not all(math.isfinite(v) and v > 0 for v in vals[:4]):
            continue
        if not math.isfinite(target[idx]) or target[idx] <= 0:
            continue
        X.append([math.log(max(v, 1e-9)) for v in vals])
        y.append(math.log(target[idx]))
    meta = {"feature_cols": feature_cols, "train_rows": len(y), "status": "not_fit"}
    if len(y) < 500:
        df["rv_har_fit"] = df["rv_har_static"]
        meta["status"] = "fallback_static"
        return df, meta
    model = LinearRegression()
    model.fit(np.asarray(X), np.asarray(y))
    preds = []
    for _, row in df.iterrows():
        vals = [float(row.get(c, np.nan)) for c in feature_cols]
        if not all(math.isfinite(v) and v > 0 for v in vals[:4]):
            preds.append(np.nan)
            continue
        pred_var = math.exp(float(model.predict([[math.log(max(v, 1e-9)) for v in vals]])[0]))
        preds.append(math.sqrt(max(pred_var, 0.0)))
    df["rv_har_fit"] = preds
    meta.update(
        {
            "status": "fit",
            "intercept": float(model.intercept_),
            "coef": [float(c) for c in model.coef_],
        }
    )
    return df, meta


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    values = btc["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def build_event_cache(btc: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train_start = event_open - pd.Timedelta(days=train_days)
    train = btc[(btc["time"] >= train_start) & (btc["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        return None
    return build_emp_cache_fast(train)


def edge_uncertainty_cents(model_p: np.ndarray, emp_cache: dict, multiplier: float) -> np.ndarray:
    counts = np.asarray([emp_cache[h]["n"] for h in emp_cache], dtype=float)
    n_eff = max(200.0, float(np.nanmedian(counts)) * 0.30) if len(counts) else 200.0
    base = 100.0 * 1.64 * np.sqrt(np.clip(model_p * (1.0 - model_p), 0.0, 0.25) / n_eff)
    return float(multiplier) * base


def lognormal_p_above_with_drift(
    strikes: np.ndarray,
    spot: float,
    ttl_min: float,
    annual_vol: float | None,
    drift_log_return: float = 0.0,
) -> np.ndarray:
    if spot <= 0 or annual_vol is None or not np.isfinite(annual_vol) or annual_vol <= 0:
        return np.full(len(strikes), np.nan)
    variance = (annual_vol**2) * ttl_min / MINUTES_PER_YEAR
    if variance <= 0:
        return (spot >= strikes).astype(float)
    sigma = math.sqrt(variance)
    thresholds = np.log(strikes / spot)
    z = (thresholds - float(drift_log_return) + 0.5 * variance) / sigma
    return np.asarray([0.5 * math.erfc(float(v) / math.sqrt(2.0)) for v in z], dtype=float)


def student_p_above(
    strikes: np.ndarray,
    spot: float,
    ttl_min: float,
    annual_vol: float | None,
    df: float,
    drift_log_return: float = 0.0,
) -> np.ndarray:
    if spot <= 0 or annual_vol is None or not np.isfinite(annual_vol) or annual_vol <= 0 or df <= 2:
        return np.full(len(strikes), np.nan)
    sigma = annual_vol * math.sqrt(ttl_min / MINUTES_PER_YEAR)
    if sigma <= 0:
        return (spot >= strikes).astype(float)
    threshold = np.log(strikes / spot)
    z = (threshold - float(drift_log_return)) / sigma
    # T / sqrt(df/(df-2)) has unit variance, so invert that scaling.
    t_threshold = z * math.sqrt(df / (df - 2.0))
    return student_t.sf(t_threshold, df=df)


def event_implied_vol(strikes: np.ndarray, mids: np.ndarray, spot: float, ttl_min: float) -> float | None:
    if spot <= 0 or ttl_min <= 0:
        return None
    valid = np.isfinite(strikes) & np.isfinite(mids) & (mids > 0.08) & (mids < 0.92)
    if valid.sum() < 3:
        return None
    scores = np.abs(mids[valid] - 0.5)
    take = np.argsort(scores)[: min(12, len(scores))]
    vols: list[float] = []
    for strike, mid in zip(strikes[valid][take], mids[valid][take]):
        lo, hi = 0.02, 3.0
        # Probability is monotone in vol only reliably near ATM. Skip impossible directions.
        for _ in range(35):
            mid_vol = 0.5 * (lo + hi)
            p = float(lognormal_p_above(np.asarray([strike]), spot, ttl_min, mid_vol)[0])
            if not math.isfinite(p):
                break
            if (strike >= spot and p < mid) or (strike < spot and p > mid):
                hi = mid_vol
            else:
                lo = mid_vol
        vol = 0.5 * (lo + hi)
        if math.isfinite(vol) and 0.02 <= vol <= 3.0:
            vols.append(vol)
    if not vols:
        return None
    return float(np.median(vols))


def safe_prob(p: np.ndarray | float) -> np.ndarray | float:
    return np.clip(p, 1e-5, 1.0 - 1e-5)


def model_probabilities(
    variant: ModelVariant,
    q: pd.DataFrame,
    btc: pd.DataFrame,
    btc_idx: int,
    spot: float,
    ttl_min: float,
    emp_cache: dict,
    calibrators: dict[str, object],
) -> np.ndarray:
    strikes = q["floor_strike"].to_numpy(dtype=float)
    scan_key = (
        str(q["event_ticker"].iloc[0]),
        int(pd.Timestamp(q["available_at"].iloc[0]).value),
        len(q),
    )
    btc_row = btc.iloc[btc_idx]
    vol = float(btc_row.get(variant.vol_col, np.nan)) * float(variant.vol_scale)
    current_vol = vol if math.isfinite(vol) and vol > 0 else None
    drift = 0.0
    if variant.drift_ret_col:
        raw = float(btc_row.get(variant.drift_ret_col, np.nan))
        if math.isfinite(raw):
            drift = variant.drift_beta * raw * (ttl_min / max(1.0, float(variant.drift_ret_col.split("_")[-1].removesuffix("m"))))

    parts: list[tuple[float, np.ndarray]] = []
    if variant.emp_weight:
        emp_key = (
            id(emp_cache),
            round(float(variant.brti_dampening), 4),
            round(float(spot), 2),
            round(float(ttl_min), 4),
            round(float(current_vol or -1.0), 6),
            hash(scan_key),
        )
        emp_p = _EMP_PROB_CACHE.get(emp_key)
        if emp_p is None:
            emp_p = vectorized_p_above(strikes, spot, ttl_min, emp_cache, current_vol, variant.brti_dampening)
            _EMP_PROB_CACHE[emp_key] = emp_p
        parts.append(
            (
                variant.emp_weight,
                emp_p,
            )
        )
    if variant.lognormal_weight:
        logn_key = (
            hash(scan_key),
            round(float(spot), 2),
            round(float(ttl_min), 4),
            round(float(current_vol or -1.0), 6),
            round(float(drift), 8),
            0.0,
        )
        logn_p = _LOGN_PROB_CACHE.get(logn_key)
        if logn_p is None:
            logn_p = lognormal_p_above_with_drift(strikes, spot, ttl_min, current_vol, drift)
            _LOGN_PROB_CACHE[logn_key] = logn_p
        parts.append((variant.lognormal_weight, logn_p))
    if variant.student_weight:
        student_key = (
            hash(scan_key),
            round(float(spot), 2),
            round(float(ttl_min), 4),
            round(float(current_vol or -1.0), 6),
            round(float(drift), 8),
            round(float(variant.student_df), 4),
        )
        student_p = _STUDENT_PROB_CACHE.get(student_key)
        if student_p is None:
            student_p = student_p_above(strikes, spot, ttl_min, current_vol, variant.student_df, drift)
            _STUDENT_PROB_CACHE[student_key] = student_p
        parts.append((variant.student_weight, student_p))
    if variant.market_weight:
        mid = 0.5 * (q["yes_bid_close"].to_numpy(dtype=float) + q["yes_ask_close"].to_numpy(dtype=float))
        parts.append((variant.market_weight, mid))
    if variant.event_iv_weight:
        mid = 0.5 * (q["yes_bid_close"].to_numpy(dtype=float) + q["yes_ask_close"].to_numpy(dtype=float))
        iv_key = (hash(scan_key), round(float(spot), 2), round(float(ttl_min), 4))
        iv = _EVENT_IV_CACHE.get(iv_key)
        if iv_key not in _EVENT_IV_CACHE:
            iv = event_implied_vol(strikes, mid, spot, ttl_min)
            _EVENT_IV_CACHE[iv_key] = iv
        iv_p = lognormal_p_above_with_drift(strikes, spot, ttl_min, iv, drift) if iv else np.full(len(q), np.nan)
        parts.append((variant.event_iv_weight, iv_p))

    numerator = np.zeros(len(q), dtype=float)
    denominator = np.zeros(len(q), dtype=float)
    for weight, p in parts:
        finite = np.isfinite(p)
        numerator[finite] += float(weight) * p[finite]
        denominator[finite] += float(weight)
    out = np.divide(numerator, denominator, out=np.full(len(q), np.nan), where=denominator > 0)
    if variant.calibrator and variant.calibrator in calibrators:
        out = apply_calibrator(calibrators[variant.calibrator], out, q, btc_row, spot)
    return np.asarray(safe_prob(out), dtype=float)


def apply_calibrator(model: object, p: np.ndarray, q: pd.DataFrame, btc_row: pd.Series, spot: float) -> np.ndarray:
    finite = np.isfinite(p)
    out = np.full(len(p), np.nan, dtype=float)
    if not finite.any():
        return out
    strikes = q["floor_strike"].to_numpy(dtype=float)
    market_mid = 0.5 * (q["yes_bid_close"].to_numpy(dtype=float) + q["yes_ask_close"].to_numpy(dtype=float))
    rv_ratio = float(btc_row.get("rv_short_long_ratio", np.nan))
    ret10 = float(btc_row.get("log_ret_10m", np.nan))
    X = np.column_stack(
        [
            logit(safe_prob(p[finite])),
            (strikes[finite] - spot) / max(1.0, spot),
            np.full(finite.sum(), rv_ratio if math.isfinite(rv_ratio) else 1.0),
            np.full(finite.sum(), ret10 if math.isfinite(ret10) else 0.0),
        ]
    )
    if getattr(model, "uses_market_mid", False):
        X = np.column_stack([X, logit(safe_prob(market_mid[finite]))])
    out[finite] = model.predict_proba(X)[:, 1]
    return out


def quote_valid_mask(q: pd.DataFrame) -> np.ndarray:
    yes_bid = q["yes_bid_close"].to_numpy(dtype=float)
    yes_ask_close = q["yes_ask_close"].to_numpy(dtype=float)
    yes_ask = q["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = q["no_ask_exe"].to_numpy(dtype=float)
    spread = q["spread_cents"].to_numpy(dtype=float)
    strikes = q["floor_strike"].to_numpy(dtype=float)
    return (
        np.isfinite(strikes)
        & np.isfinite(yes_bid)
        & np.isfinite(yes_ask_close)
        & np.isfinite(yes_ask)
        & np.isfinite(no_ask)
        & np.isfinite(spread)
        & (yes_bid >= 0.0)
        & (yes_ask_close >= yes_bid)
        & (yes_ask_close <= 1.0)
        & (yes_ask >= 0.0)
        & (yes_ask <= 1.0)
        & (no_ask >= 0.0)
        & (no_ask <= 1.0)
    )


def signals_for_variant(
    variant: ModelVariant,
    scan_quotes: pd.DataFrame,
    btc: pd.DataFrame,
    emp_cache: dict,
    scan_ts: pd.Timestamp,
    calibrators: dict[str, object],
) -> pd.DataFrame:
    spot, btc_idx = btc_at_or_before(btc, scan_ts)
    if spot is None or btc_idx is None or btc_idx < 1440:
        return pd.DataFrame()
    ttl_min = (scan_quotes["close_time"].iloc[0] - scan_ts).total_seconds() / 60.0
    if ttl_min < MIN_TTL_MIN - EPS or ttl_min > MAX_TTL_MIN + EPS:
        return pd.DataFrame()
    mask = quote_valid_mask(scan_quotes)
    if not mask.any():
        return pd.DataFrame()
    q = scan_quotes.loc[mask].copy()
    p = model_probabilities(variant, q, btc, btc_idx, spot, ttl_min, emp_cache, calibrators)
    finite = np.isfinite(p)
    if not finite.any():
        return pd.DataFrame()
    q = q.iloc[np.where(finite)[0]].copy()
    p = p[finite]
    yes_ask = q["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = q["no_ask_exe"].to_numpy(dtype=float)
    edge_yes = p - yes_ask + variant.side_bias_cents / 100.0
    edge_no = (1.0 - p) - no_ask - variant.side_bias_cents / 100.0
    choose_yes = edge_yes > edge_no
    side = np.where(choose_yes, "yes", "no")
    entry = np.where(choose_yes, yes_ask, no_ask)
    gross_edge = np.where(choose_yes, edge_yes, edge_no) * 100.0
    fees = np.asarray([kalshi_fee_dollars(float(price), contracts=1, liquidity="taker") for price in entry])
    net_edge = gross_edge - fees * 100.0
    threshold = variant.min_edge_cents + edge_uncertainty_cents(p, emp_cache, variant.uncertainty_mult)
    strong = np.where(side == "yes", p >= variant.min_yes_p, p <= variant.max_no_p)
    passed = (
        strong
        & (net_edge >= threshold)
        & (q["spread_cents"].to_numpy(dtype=float) <= variant.max_spread_cents)
        & (entry >= variant.min_entry)
        & (entry <= variant.max_entry)
    )
    if not passed.any():
        return pd.DataFrame()
    idxs = np.where(passed)[0]
    out = q.iloc[idxs].copy()
    out["variant"] = variant.name
    out["round_no"] = variant.round_no
    out["side"] = side[idxs]
    out["entry_price"] = entry[idxs]
    out["entry_fee"] = fees[idxs]
    out["model_p_yes"] = p[idxs]
    out["net_edge_cents"] = net_edge[idxs]
    out["edge_threshold_cents"] = threshold[idxs]
    out["entry_spot"] = spot
    out["scan_time"] = scan_ts
    out["ttl_min"] = ttl_min
    out["market_mid"] = 0.5 * (out["yes_bid_close"].astype(float) + out["yes_ask_close"].astype(float))
    return out.sort_values("net_edge_cents", ascending=False)


def settle_due(open_positions: dict[tuple[str, str], OpenPosition], open_events: set[tuple[str, str]], now: pd.Timestamp, btc: pd.DataFrame, settled: list[dict]) -> None:
    due = [key for key, pos in open_positions.items() if pd.Timestamp(pos.row["close_time"]) <= now]
    for key in due:
        pos = open_positions.pop(key)
        row = pos.row
        open_events.discard((row["variant"], row["event_ticker"]))
        settlement_spot, _ = btc_at_or_before(btc, pd.Timestamp(row["close_time"]))
        if settlement_spot is None:
            continue
        yes_settles = settlement_spot >= row["strike"]
        win = (yes_settles and row["side"] == "yes") or ((not yes_settles) and row["side"] == "no")
        payout = 1.0 if win else 0.0
        pnl = payout - row["entry_price"] - row["entry_fee"]
        settled.append(
            {
                **row,
                "settle_time": row["close_time"],
                "settlement_spot": settlement_spot,
                "settlement": "yes" if yes_settles else "no",
                "payout": payout,
                "pnl": pnl,
            }
        )


def run_backtest(
    quotes: pd.DataFrame,
    btc: pd.DataFrame,
    variants: list[ModelVariant],
    calibrators: dict[str, object],
    max_events: int | None,
    progress_every: int,
) -> pd.DataFrame:
    settled: list[dict] = []
    open_positions: dict[tuple[str, str], OpenPosition] = {}
    open_events: set[tuple[str, str]] = set()
    event_cache: dict[tuple[str, int], dict] = {}
    quotes = quotes.sort_values(["close_time", "available_at", "market_ticker"])
    events = list(quotes.groupby("event_ticker", sort=False))
    if max_events:
        events = events[:max_events]
    for event_idx, (event_ticker, event_quotes) in enumerate(events, start=1):
        if event_idx == 1 or event_idx % progress_every == 0:
            print(f"event {event_idx}/{len(events)} {event_ticker}", flush=True)
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            settle_due(open_positions, open_events, scan_ts, btc, settled)
            for variant in variants:
                event_key = (variant.name, event_ticker)
                if event_key in open_events:
                    continue
                cache_key = (event_ticker, variant.train_days)
                emp_cache = event_cache.get(cache_key)
                if emp_cache is None:
                    emp_cache = build_event_cache(btc, scan_quotes["event_open_time"].iloc[0], variant.train_days)
                    if emp_cache is None:
                        continue
                    event_cache[cache_key] = emp_cache
                signals = signals_for_variant(variant, scan_quotes, btc, emp_cache, scan_ts, calibrators)
                if signals.empty:
                    continue
                sig = signals.iloc[0]
                pos_key = (variant.name, str(sig["market_ticker"]))
                if pos_key in open_positions:
                    continue
                row = {
                    "variant": variant.name,
                    "round_no": variant.round_no,
                    "event_ticker": str(sig["event_ticker"]),
                    "market_ticker": str(sig["market_ticker"]),
                    "side": str(sig["side"]),
                    "strike": float(sig["floor_strike"]),
                    "entry_time": scan_ts,
                    "quote_ts_end": sig["ts_end"],
                    "close_time": sig["close_time"],
                    "entry_price": float(sig["entry_price"]),
                    "entry_fee": float(sig["entry_fee"]),
                    "contracts": 1,
                    "entry_spot": float(sig["entry_spot"]),
                    "model_p_yes": float(sig["model_p_yes"]),
                    "net_edge_cents": float(sig["net_edge_cents"]),
                    "edge_threshold_cents": float(sig["edge_threshold_cents"]),
                    "spread_cents": float(sig["spread_cents"]),
                    "market_mid": float(sig["market_mid"]),
                    "ttl_min": float(sig["ttl_min"]),
                    "fidelity": sig["fidelity"],
                }
                open_positions[pos_key] = OpenPosition(row=row)
                open_events.add(event_key)
    final_ts = max((pd.Timestamp(pos.row["close_time"]) for pos in open_positions.values()), default=pd.Timestamp.utcnow())
    settle_due(open_positions, open_events, final_ts + pd.Timedelta(days=1), btc, settled)
    if not settled:
        return pd.DataFrame()
    return pd.DataFrame(settled).sort_values(["variant", "entry_time", "market_ticker"]).reset_index(drop=True)


def split_name(ts: pd.Timestamp) -> str:
    ts = pd.Timestamp(ts).tz_convert("UTC")
    if ts < TRAIN_END:
        return "train"
    if ts < VALIDATION_END:
        return "validation"
    return "test"


def stats_for_trades(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "avg_edge": 0.0,
            "yes_trades": 0,
            "no_trades": 0,
        }
    ordered = df.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
    pnl = ordered["pnl"].astype(float)
    premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    dd = equity - equity.cummax()
    return {
        "trades": int(len(ordered)),
        "pnl": float(pnl.sum()),
        "premium": float(premium.sum()),
        "rop": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "max_drawdown": float(dd.min()) if len(dd) else 0.0,
        "avg_edge": float(ordered["net_edge_cents"].mean()),
        "yes_trades": int((ordered["side"] == "yes").sum()),
        "no_trades": int((ordered["side"] == "no").sum()),
    }


def summarize(trades: pd.DataFrame, variants: list[ModelVariant]) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["split"] = t["entry_time"].map(split_name)
    rows = []
    meta = {v.name: v for v in variants}
    for variant, vg in t.groupby("variant", sort=True):
        for split in ("train", "validation", "test", "all"):
            g = vg if split == "all" else vg[vg["split"] == split]
            row = {"variant": variant, "split": split, **stats_for_trades(g)}
            v = meta.get(variant)
            if v:
                row.update({"round_no": v.round_no, "hypothesis": v.hypothesis})
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["variant", "split"]).reset_index(drop=True)


def candidate_score(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    piv = summary.pivot_table(
        index=["variant", "round_no", "hypothesis"],
        columns="split",
        values=["trades", "pnl", "rop", "win_rate", "max_drawdown"],
        aggfunc="first",
    )
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    out = piv.reset_index()
    for col in ("pnl_train", "pnl_validation", "pnl_test", "trades_validation", "trades_test"):
        if col not in out:
            out[col] = 0.0
    out["selection_ok"] = (
        (out["trades_validation"] >= 3)
        & (out["trades_test"] >= 3)
        & (out["pnl_validation"] > 0)
        & (out["pnl_test"] > 0)
    )
    out["score"] = (
        out.get("pnl_validation", 0.0) * 1.0
        + out.get("pnl_test", 0.0) * 0.75
        - 0.25 * out.get("max_drawdown_validation", 0.0).abs()
        - 0.25 * out.get("max_drawdown_test", 0.0).abs()
    )
    return out.sort_values(["selection_ok", "score"], ascending=[False, False]).reset_index(drop=True)


def build_variants(max_round: int) -> list[ModelVariant]:
    variants: list[ModelVariant] = [
        ModelVariant("baseline_emp70_logn_rv60", 1, "Current research fair value: 7d empirical move distribution blended 70/30 with lognormal rv60."),
        ModelVariant("emp_only", 1, "Pure empirical conditional move distribution; tests whether lognormal RV blend hurts."),
        ModelVariant("lognormal_rv60_only", 1, "Pure lognormal using rv60; tests parametric RV fair value."),
        ModelVariant("blend_50_50_rv60", 1, "Equal empirical/lognormal blend.", emp_weight=0.5, lognormal_weight=0.5),
        ModelVariant("blend_30_70_rv60", 1, "Lognormal-heavy blend.", emp_weight=0.3, lognormal_weight=0.7),
        ModelVariant("blend_85_15_rv60", 1, "Empirical-heavy blend.", emp_weight=0.85, lognormal_weight=0.15),
        ModelVariant("rv15_logn_blend", 1, "Short realized volatility should price one-hour binaries better than rv60.", vol_col="rv_15m"),
        ModelVariant("rv30_logn_blend", 1, "Thirty-minute RV better matches the entry horizon.", vol_col="rv_30m"),
        ModelVariant("rv120_logn_blend", 1, "Two-hour RV stabilizes noisy one-hour estimates.", vol_col="rv_120m"),
        ModelVariant("rv240_logn_blend", 1, "Four-hour RV captures intraday volatility state.", vol_col="rv_240m"),
        ModelVariant("rv1d_logn_blend", 1, "Daily RV is a more stable volatility prior.", vol_col="rv_1d"),
        ModelVariant("ewma60_logn_blend", 1, "EWMA variance handles volatility clustering better than rolling RV.", vol_col="rv_ewma_60m"),
        ModelVariant("ewma240_logn_blend", 1, "Slower EWMA variance avoids reacting to microstructure bursts.", vol_col="rv_ewma_240m"),
        ModelVariant("har_static_logn_blend", 1, "HAR-style multi-scale RV blend improves fair-value volatility.", vol_col="rv_har_static"),
        ModelVariant("har_fit_logn_blend", 1, "Learned HAR RV forecast from train period improves one-hour fair value.", vol_col="rv_har_fit"),
        ModelVariant("vol_scale_085", 1, "Market may overprice near-term realized volatility; test lower RV scale.", vol_scale=0.85),
        ModelVariant("vol_scale_115", 1, "Realized move tails may exceed rv60; test higher RV scale.", vol_scale=1.15),
        ModelVariant("vol_scale_130", 1, "Crypto jump tails require materially fatter one-hour distribution.", vol_scale=1.30),
        ModelVariant("brti_065", 1, "Dampen BTC reference noise more aggressively in empirical returns.", brti_dampening=0.65),
        ModelVariant("brti_100", 1, "Remove BRTI dampening; spot moves should be fully passed through.", brti_dampening=1.0),
    ]
    if max_round >= 2:
        variants.extend(
            [
                ModelVariant("student_t5_rv60", 2, "Student-t innovations capture fat tails better than lognormal.", emp_weight=0.5, lognormal_weight=0.0, student_weight=0.5, student_df=5),
                ModelVariant("student_t3_rv60", 2, "Very fat-tailed Student-t distribution for crypto jumps.", emp_weight=0.5, lognormal_weight=0.0, student_weight=0.5, student_df=3),
                ModelVariant("student_t8_rv60", 2, "Moderately fat-tailed Student-t distribution.", emp_weight=0.5, lognormal_weight=0.0, student_weight=0.5, student_df=8),
                ModelVariant("drift_mom10_pos", 2, "Short-horizon momentum drift should shift binary fair value.", drift_ret_col="log_ret_10m", drift_beta=0.30),
                ModelVariant("drift_mom10_neg", 2, "Short-horizon reversal drift should shift binary fair value.", drift_ret_col="log_ret_10m", drift_beta=-0.30),
                ModelVariant("drift_mom30_pos", 2, "Thirty-minute continuation drift.", drift_ret_col="log_ret_30m", drift_beta=0.20),
                ModelVariant("drift_mom30_neg", 2, "Thirty-minute mean reversion drift.", drift_ret_col="log_ret_30m", drift_beta=-0.20),
                ModelVariant("event_iv_30", 2, "Use event-level implied volatility from Kalshi surface as vol prior.", emp_weight=0.7, lognormal_weight=0.0, event_iv_weight=0.3),
                ModelVariant("event_iv_50", 2, "Equal blend between empirical and event-implied volatility probability.", emp_weight=0.5, lognormal_weight=0.0, event_iv_weight=0.5),
                ModelVariant("market_weight_10", 2, "Small market-implied probability shrink improves calibration without eliminating edge.", market_weight=0.10),
                ModelVariant("market_weight_25", 2, "Quarter market-implied probability shrink like previous JS robust ideas.", market_weight=0.25),
                ModelVariant("market_weight_40", 2, "Heavy market-implied shrink tests whether our fair value is overconfident.", market_weight=0.40),
                ModelVariant("rv_down60_blend", 2, "Downside semivariance controls near-term volatility better in stressed BTC windows.", vol_col="rv_down_60m"),
                ModelVariant("rv_up60_blend", 2, "Upside semivariance captures asymmetric rally/chase regimes.", vol_col="rv_up_60m"),
                ModelVariant("short_train_3d", 2, "Empirical distribution should adapt faster than 7d in changing regimes.", train_days=3),
                ModelVariant("long_train_14d", 2, "Empirical distribution needs more sample stability than 7d.", train_days=14),
                ModelVariant("lower_uncertainty", 2, "Sampling uncertainty surcharge may be too conservative for calibrated fair values.", uncertainty_mult=0.50),
                ModelVariant("higher_uncertainty", 2, "Sampling uncertainty surcharge may be too optimistic for noisy empirical p.", uncertainty_mult=1.50),
                ModelVariant("yes_bias_1c", 2, "Systematic YES fair value bias from settlement/index dynamics.", side_bias_cents=1.0),
                ModelVariant("no_bias_1c", 2, "Systematic NO fair value bias from settlement/index dynamics.", side_bias_cents=-1.0),
            ]
        )
    if max_round >= 3:
        variants.extend(
            [
                ModelVariant("cal_base_no_market", 3, "Train-period Platt calibration on baseline p and BTC state, no market price feature.", calibrator="base_no_market"),
                ModelVariant("cal_base_with_market", 3, "Train-period Platt calibration includes market-implied mid as an IV/crowd prior.", calibrator="base_with_market"),
                ModelVariant("cal_market10", 3, "Light market shrink plus train-period calibration.", market_weight=0.10, calibrator="base_with_market"),
                ModelVariant("har_fit_student_t5", 3, "Learned HAR volatility with fat-tailed Student-t innovation.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, vol_col="rv_har_fit", student_df=5),
                ModelVariant("har_fit_market10", 3, "Learned HAR volatility plus light market-implied calibration.", vol_col="rv_har_fit", market_weight=0.10),
                ModelVariant("har_fit_event_iv30", 3, "Learned HAR volatility plus event-level implied vol prior.", vol_col="rv_har_fit", event_iv_weight=0.30, lognormal_weight=0.0),
                ModelVariant("ewma60_student_t5", 3, "EWMA volatility with fat-tailed innovation.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, vol_col="rv_ewma_60m", student_df=5),
                ModelVariant("ewma60_market10", 3, "EWMA volatility plus light market-implied probability shrink.", vol_col="rv_ewma_60m", market_weight=0.10),
                ModelVariant("rv30_student_t5", 3, "Thirty-minute RV with fat-tailed innovation.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, vol_col="rv_30m", student_df=5),
                ModelVariant("adaptive_high_vol_scale", 3, "Higher vol scale in high short/long volatility regimes.", vol_col="rv_ewma_60m", vol_scale=1.15),
                ModelVariant("adaptive_low_vol_scale", 3, "Lower vol scale avoids overpaying in calm regimes.", vol_col="rv_ewma_240m", vol_scale=0.90),
                ModelVariant("market25_student_t5", 3, "Market-implied probability plus fat-tailed residual distribution.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.30, market_weight=0.25, student_df=5),
                ModelVariant("eventiv30_student_t5", 3, "Event implied vol blended with Student-t tails.", emp_weight=0.45, lognormal_weight=0.0, event_iv_weight=0.25, student_weight=0.30, student_df=5),
                ModelVariant("short3_market10", 3, "Fast empirical adaptation with light market-implied calibration.", train_days=3, market_weight=0.10),
                ModelVariant("long14_market10", 3, "Stable empirical sample with light market-implied calibration.", train_days=14, market_weight=0.10),
                ModelVariant("drift_pos_market10", 3, "Momentum drift plus market-implied prior.", drift_ret_col="log_ret_10m", drift_beta=0.20, market_weight=0.10),
                ModelVariant("drift_neg_market10", 3, "Mean-reversion drift plus market-implied prior.", drift_ret_col="log_ret_10m", drift_beta=-0.20, market_weight=0.10),
                ModelVariant("student_t5_low_uncert", 3, "Fat-tail model with lower empirical uncertainty surcharge.", emp_weight=0.5, lognormal_weight=0.0, student_weight=0.5, student_df=5, uncertainty_mult=0.75),
                ModelVariant("har_fit_low_uncert", 3, "Learned HAR model with lower uncertainty surcharge.", vol_col="rv_har_fit", uncertainty_mult=0.75),
                ModelVariant("cal_no_market_low_uncert", 3, "No-market calibrated fair value with lower uncertainty surcharge.", calibrator="base_no_market", uncertainty_mult=0.75),
            ]
        )
    if max_round >= 4:
        variants.extend(
            [
                ModelVariant("rv_down60_student_t3", 4, "Downside semivariance with very fat-tailed Student-t innovations.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, vol_col="rv_down_60m", student_df=3),
                ModelVariant("rv_down60_student_t5", 4, "Downside semivariance with Student-t innovations.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, vol_col="rv_down_60m", student_df=5),
                ModelVariant("rv_down60_emp85", 4, "Empirical-heavy blend with downside semivariance as parametric prior.", emp_weight=0.85, lognormal_weight=0.15, vol_col="rv_down_60m"),
                ModelVariant("brti065_rv_down60", 4, "Aggressive BTC reference dampening combined with downside semivariance.", brti_dampening=0.65, vol_col="rv_down_60m"),
                ModelVariant("brti065_student_t3", 4, "Aggressive BTC reference dampening plus very fat tails.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, brti_dampening=0.65, student_df=3),
                ModelVariant("brti065_student_t5", 4, "Aggressive BTC reference dampening plus Student-t tails.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, brti_dampening=0.65, student_df=5),
                ModelVariant("brti065_rv_down60_student_t5", 4, "Reference dampening, downside semivariance, and Student-t tails together.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, brti_dampening=0.65, vol_col="rv_down_60m", student_df=5),
                ModelVariant("market10_rv_down60", 4, "Light market-implied shrink on downside semivariance model.", vol_col="rv_down_60m", market_weight=0.10),
                ModelVariant("market25_rv_down60", 4, "Quarter market-implied shrink on downside semivariance model.", vol_col="rv_down_60m", market_weight=0.25),
                ModelVariant("market10_student_t3", 4, "Light market shrink with very fat-tailed residual distribution.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.45, market_weight=0.10, student_df=3),
                ModelVariant("market10_down60_student_t5", 4, "Light market shrink, downside semivariance, and Student-t tails.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.45, market_weight=0.10, vol_col="rv_down_60m", student_df=5),
                ModelVariant("rv_up60_student_t5", 4, "Upside semivariance with Student-t innovations for rally regimes.", emp_weight=0.45, lognormal_weight=0.0, student_weight=0.55, vol_col="rv_up_60m", student_df=5),
                ModelVariant("short3_rv_down60", 4, "Fast empirical sample plus downside semivariance.", train_days=3, vol_col="rv_down_60m"),
                ModelVariant("long14_rv_down60", 4, "Stable empirical sample plus downside semivariance.", train_days=14, vol_col="rv_down_60m"),
                ModelVariant("rv_down60_low_uncert", 4, "Downside semivariance with lower empirical uncertainty surcharge.", vol_col="rv_down_60m", uncertainty_mult=0.75),
                ModelVariant("student_t3_low_uncert", 4, "Very fat-tail model with lower uncertainty surcharge.", emp_weight=0.5, lognormal_weight=0.0, student_weight=0.5, student_df=3, uncertainty_mult=0.75),
                ModelVariant("brti065_low_uncert", 4, "Aggressive reference dampening with lower uncertainty surcharge.", brti_dampening=0.65, uncertainty_mult=0.75),
            ]
        )
    return variants


def fit_calibrators(
    quotes: pd.DataFrame,
    btc: pd.DataFrame,
    max_rows: int = 120_000,
) -> dict[str, object]:
    rows_X_no_market: list[list[float]] = []
    rows_X_market: list[list[float]] = []
    y: list[int] = []
    event_cache: dict[str, dict] = {}
    baseline = ModelVariant("baseline_cal_source", 0, "baseline")
    train_quotes = quotes[quotes["close_time"] < TRAIN_END]
    train_quotes = train_quotes.sort_values(["close_time", "available_at", "market_ticker"])
    events = list(train_quotes.groupby("event_ticker", sort=False))
    if len(train_quotes) > max_rows:
        step = max(1, len(train_quotes) // max_rows)
    else:
        step = 1
    seen = 0
    for event_ticker, event_quotes in events:
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_quotes["event_open_time"].iloc[0], baseline.train_days)
            if emp_cache is None:
                continue
            event_cache[event_ticker] = emp_cache
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            if seen % step != 0:
                seen += len(scan_quotes)
                continue
            spot, idx = btc_at_or_before(btc, scan_ts)
            if spot is None or idx is None or idx < 1440:
                continue
            mask = quote_valid_mask(scan_quotes)
            if not mask.any():
                continue
            q = scan_quotes.loc[mask].copy()
            p = model_probabilities(baseline, q, btc, idx, spot, (q["close_time"].iloc[0] - scan_ts).total_seconds() / 60.0, emp_cache, {})
            close_spot, _ = btc_at_or_before(btc, q["close_time"].iloc[0])
            if close_spot is None:
                continue
            strikes = q["floor_strike"].to_numpy(dtype=float)
            market_mid = 0.5 * (q["yes_bid_close"].to_numpy(dtype=float) + q["yes_ask_close"].to_numpy(dtype=float))
            rv_ratio = float(btc.iloc[idx].get("rv_short_long_ratio", np.nan))
            ret10 = float(btc.iloc[idx].get("log_ret_10m", np.nan))
            finite = np.isfinite(p) & np.isfinite(strikes) & np.isfinite(market_mid) & (market_mid > 0.03) & (market_mid < 0.97)
            for i in np.where(finite)[0]:
                rows_X_no_market.append(
                    [
                        float(logit(safe_prob(p[i]))),
                        float((strikes[i] - spot) / max(1.0, spot)),
                        rv_ratio if math.isfinite(rv_ratio) else 1.0,
                        ret10 if math.isfinite(ret10) else 0.0,
                    ]
                )
                rows_X_market.append([*rows_X_no_market[-1], float(logit(safe_prob(market_mid[i])))])
                y.append(int(close_spot >= strikes[i]))
                if len(y) >= max_rows:
                    break
            if len(y) >= max_rows:
                break
            seen += len(scan_quotes)
        if len(y) >= max_rows:
            break
    if len(set(y)) < 2 or len(y) < 500:
        return {}
    cal_no_market = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=0.50))
    cal_market = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=0.50))
    cal_no_market.fit(np.asarray(rows_X_no_market), np.asarray(y))
    cal_market.fit(np.asarray(rows_X_market), np.asarray(y))
    # Attach a simple attribute used by apply_calibrator.
    cal_no_market.uses_market_mid = False  # type: ignore[attr-defined]
    cal_market.uses_market_mid = True  # type: ignore[attr-defined]
    return {"base_no_market": cal_no_market, "base_with_market": cal_market}


def evaluate_on_captured_decisions(holdout_path: Path, selected_variants: list[str]) -> pd.DataFrame:
    """Evaluate selected variants on already-captured live decisions only.

    This is not a counterfactual opportunity replay; it checks whether the new
    selected variants would have agreed with or rejected the actual captured
    late-only decisions.  Full websocket replay remains a separate heavier task.
    """
    if not holdout_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(holdout_path)
    if df.empty:
        return pd.DataFrame()
    df["entry_time"] = pd.to_datetime(df.get("entry_time", df.get("received_at_utc")), utc=True)
    df["side"] = df["side"].astype(str).str.lower()
    df["official_result"] = df["official_result"].astype(str).str.lower()
    df["pnl_1c"] = pd.to_numeric(df["pnl_1c"], errors="coerce")
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    rows = []
    # Only baseline current p exists in this holdout file; use it to audit current
    # decision quality, not to rank the new historical variants.
    current_guard = ~((df["side"].eq("no")) & (pd.to_numeric(df["side_probability"], errors="coerce") < 0.72))
    for name, mask in {
        "captured_all_actual_research": pd.Series(True, index=df.index),
        "captured_current_no_p72_guard": current_guard,
        "captured_live_only_no_p72_guard": current_guard & df["capture"].eq("research_live_capture"),
    }.items():
        g = df[mask]
        pnl = g["pnl_1c"].sum()
        premium = pd.to_numeric(g.get("premium_1c", g["entry_price"]), errors="coerce").sum()
        rows.append(
            {
                "variant": name,
                "trades": int(len(g)),
                "pnl": float(pnl),
                "premium": float(premium),
                "rop": float(pnl / premium) if premium else 0.0,
                "win_rate": float((g["pnl_1c"] > 0).mean()) if len(g) else 0.0,
                "note": "captured decision holdout only; not counterfactual replay",
            }
        )
    for v in selected_variants:
        rows.append(
            {
                "variant": v,
                "trades": 0,
                "pnl": 0.0,
                "premium": 0.0,
                "rop": 0.0,
                "win_rate": 0.0,
                "note": "requires full websocket counterfactual replay for exact holdout",
            }
        )
    return pd.DataFrame(rows)


def write_report(
    output_dir: Path,
    summary: pd.DataFrame,
    scorecard: pd.DataFrame,
    holdout: pd.DataFrame,
    variants: list[ModelVariant],
    har_meta: dict,
    sources: list[dict],
) -> None:
    def table(df: pd.DataFrame) -> str:
        if df.empty:
            return ""
        return df.to_string(index=False)

    top = scorecard.head(12)
    lines = [
        "# BTC 1h Core Model Research",
        "",
        f"Generated: {utc_now()}",
        "",
        "## Scope",
        "",
        "This run researches the fair-value probability model only: empirical distribution, realized-volatility forecasts, event-implied volatility, fat tails, drift, and calibration. It does not alter live execution, risk sizing, or the running bot.",
        "",
        "Websocket capture is held out from model selection. Historical DuckDB bid/ask candles are used for train/validation/test selection.",
        "",
        "## Literature Priors",
        "",
    ]
    for src in sources:
        lines.append(f"- {src['name']}: {src['use']}. {src['url']}")
    lines.extend(
        [
            "",
            "## Data Split",
            "",
            "- Train: entry before 2026-04-01 UTC.",
            "- Validation: 2026-04-01 through 2026-04-20 UTC.",
            "- Test: 2026-04-21 onward in historical DuckDB.",
            "- Live websocket holdout is not used to rank variants.",
            "",
            "## HAR Volatility Fit",
            "",
            "```json",
            json.dumps(har_meta, indent=2),
            "```",
            "",
            "## Top Historical Core-Model Variants",
            "",
        ]
    )
    if not top.empty:
        display_cols = [
            "variant",
            "round_no",
            "selection_ok",
            "trades_validation",
            "pnl_validation",
            "win_rate_validation",
            "max_drawdown_validation",
            "trades_test",
            "pnl_test",
            "win_rate_test",
            "max_drawdown_test",
            "score",
        ]
        lines.append("```text")
        lines.append(table(top[display_cols]))
        lines.append("```")
    lines.extend(["", "## Captured Websocket Holdout Status", ""])
    if not holdout.empty:
        lines.append("```text")
        lines.append(table(holdout))
        lines.append("```")
    else:
        lines.append("No captured holdout summary was produced.")
    lines.extend(
        [
            "",
            "## Important Caveat",
            "",
            "The historical test still settles from BTC minute-close proxy and uses historical bid/ask candle closes. It is useful for model selection but not as faithful as the captured websocket decision stream. Any finalist must be replayed against the websocket top-of-book stream before deployment.",
            "",
            "## Files",
            "",
            "- `core_model_trades.csv`",
            "- `core_model_summary.csv`",
            "- `core_model_scorecard.csv`",
            "- `core_model_variants.json`",
            "- `core_model_report.md`",
        ]
    )
    (output_dir / "core_model_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("loading historical DuckDB...", flush=True)
    quotes, btc = load_tables(args.db, args.min_ttl, args.max_ttl)
    if quotes.empty:
        raise SystemExit("No quotes loaded.")
    btc = add_btc_features(btc)
    btc, har_meta = fit_har_vol(btc, TRAIN_END)
    variants = build_variants(args.rounds)
    if args.variant_regex:
        pattern = re.compile(args.variant_regex)
        variants = [variant for variant in variants if pattern.search(variant.name)]
        if not variants:
            raise ValueError(f"variant regex matched no variants: {args.variant_regex!r}")
    print(f"loaded quotes={len(quotes):,} events={quotes['event_ticker'].nunique():,} btc={len(btc):,} variants={len(variants)}", flush=True)
    calibrators: dict[str, object] = {}
    if not args.skip_calibrators and any(v.calibrator for v in variants):
        print("fitting train-only calibrators...", flush=True)
        calibrators = fit_calibrators(quotes, btc)
        print(f"calibrators={list(calibrators)}", flush=True)
    else:
        variants = [replace(v, calibrator=None) for v in variants]
    trades = run_backtest(quotes, btc, variants, calibrators, args.max_events, args.progress_every_events)
    summary = summarize(trades, variants)
    scorecard = candidate_score(summary)
    selected = scorecard.loc[scorecard.get("selection_ok", False).astype(bool), "variant"].head(8).tolist() if not scorecard.empty else []
    holdout = evaluate_on_captured_decisions(args.capture_holdout, selected)

    trades.to_csv(args.output_dir / "core_model_trades.csv", index=False)
    summary.to_csv(args.output_dir / "core_model_summary.csv", index=False)
    scorecard.to_csv(args.output_dir / "core_model_scorecard.csv", index=False)
    holdout.to_csv(args.output_dir / "core_model_captured_holdout_note.csv", index=False)
    (args.output_dir / "core_model_variants.json").write_text(
        json.dumps([asdict(v) for v in variants], indent=2),
        encoding="utf-8",
    )
    sources = [
        {
            "name": "Corsi HAR-RV",
            "use": "multi-scale realized-volatility forecast prior",
            "url": "https://academic.oup.com/jfec/article-pdf/7/2/174/2543795/nbp001.pdf",
        },
        {
            "name": "Zhang, Mykland, Ait-Sahalia two-scale RV",
            "use": "microstructure-noise warning for high-frequency RV",
            "url": "https://www.tandfonline.com/doi/abs/10.1198/016214505000000169",
        },
        {
            "name": "Patton-Sheppard realized semivariance",
            "use": "signed upside/downside volatility hypotheses",
            "url": "https://public.econ.duke.edu/~ap172/Patton_Sheppard_Realized_Semivariance_7oct11.pdf",
        },
        {
            "name": "Bitcoin HAR model uncertainty",
            "use": "train-day and HAR averaging hypotheses for BTC RV",
            "url": "https://www.sciencedirect.com/science/article/pii/S0927539821000220",
        },
    ]
    write_report(args.output_dir, summary, scorecard, holdout, variants, har_meta, sources)
    print(json.dumps({"output_dir": str(args.output_dir), "trades": len(trades), "top": scorecard.head(5).to_dict("records")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
