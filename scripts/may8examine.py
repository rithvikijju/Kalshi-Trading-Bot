#!/usr/bin/env python3
"""May 8 research-model examination.

This script tests the guardrails suggested by the May 8 live-trade diagnosis
without changing the live model:

* shrink model probability toward Kalshi market mid
* shrink near-strike probabilities toward 50/50 for settlement-index basis risk
* require a volatility-scaled strike buffer
* block exhaustion-style entries after sharp side-aligned moves

It replays the variants over:

* data/research_datamart/research.duckdb, built from Kalshi historical bid/ask
  API data and local BTC minute data
* optional collected CSV exports in data/*.csv, using an assumed spread
* optional live websocket capture top-of-book rows, snapshot-copied first so the
  running bot is not touched
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR, kalshi_fee_dollars
from scripts import backtest_1hr_collected_data as csv_bt
from scripts.backtest_1hr_collected_data import RESEARCH_CFG, build_emp_cache_fast, lognormal_p_above, vectorized_p_above
from scripts.backtest_research_duckdb import BRTI_DAMPENING


DEFAULT_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"
DEFAULT_OUTPUT = PROJECT_ROOT / "backtest_outputs" / "may8examine"
DEFAULT_BTC_CACHE = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_CAPTURE_DB = Path.home() / ".btc_kalshi_bot" / "research_live_capture.duckdb"
TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")
MIN_TTL_MIN = 5.0
MAX_TTL_MIN = 65.0
MIN_EDGE_CENTS = 12.0
MAX_SPREAD_CENTS = 2.0
MIN_ENTRY = 0.25
MAX_ENTRY = 0.75
MIN_YES_P = 0.65
MAX_NO_P = 0.35


@dataclass(frozen=True)
class Variant:
    name: str
    emp_weight: float = 0.70
    lognormal_weight: float = 0.30
    brti_dampening: float = BRTI_DAMPENING
    market_shrink: float = 0.0
    basis_sigma_usd: float = 0.0
    max_basis_shrink: float = 0.0
    min_abs_distance_usd: float = 0.0
    min_distance_sigma: float = 0.0
    momentum_guard_usd: float = 0.0
    momentum_guard_distance_sigma: float = 999.0
    momentum_guard_distance_usd: float = 999999.0
    no_momentum_guard_usd: float = 0.0
    no_momentum_guard_distance_usd: float = 999999.0
    no_momentum_guard_distance_sigma: float = 999.0
    min_edge_cents: float = MIN_EDGE_CENTS
    yes_edge_add_cents: float = 0.0
    no_edge_add_cents: float = 0.0
    max_spread_cents: float = MAX_SPREAD_CENTS
    min_entry: float = MIN_ENTRY
    max_entry: float = MAX_ENTRY
    min_yes_p: float = MIN_YES_P
    max_no_p: float = MAX_NO_P
    allow_yes: bool = True
    allow_no: bool = True
    min_ttl_min: float = MIN_TTL_MIN
    max_ttl_min: float = MAX_TTL_MIN
    exclude_utc_hours: tuple[int, ...] = ()
    entry_edge_add_over_65_cents: float = 0.0
    entry_edge_add_over_70_cents: float = 0.0
    max_shape_violation_cents: float = 999.0
    min_event_valid_markets: int = 0
    max_event_median_spread_cents: float = 999.0
    adjacent_min_gross_edge_cents: float = -999.0


VARIANTS: list[Variant] = [
    Variant("baseline_current"),
    Variant("baseline_late_only", min_ttl_min=5.0, max_ttl_min=20.0),
    Variant("market_shrink_25", market_shrink=0.25),
    Variant("market_shrink_50", market_shrink=0.50),
    Variant("market_shrink_25_edge8", market_shrink=0.25, min_edge_cents=8.0),
    Variant("market_shrink_25_edge10", market_shrink=0.25, min_edge_cents=10.0),
    Variant("edge18", min_edge_cents=18.0),
    Variant("no_cautious", max_no_p=0.20, no_edge_add_cents=4.0),
    Variant("no_very_cautious", max_no_p=0.15, no_edge_add_cents=6.0),
    Variant("yes_only", allow_no=False),
    Variant("no_only_cautious", allow_yes=False, max_no_p=0.20, no_edge_add_cents=4.0),
    Variant("blend_emp_only", emp_weight=1.0, lognormal_weight=0.0),
    Variant("blend_balanced_50_50", emp_weight=0.50, lognormal_weight=0.50),
    Variant("blend_lognormal_heavy", emp_weight=0.30, lognormal_weight=0.70),
    Variant("brti_dampen_60", brti_dampening=0.60),
    Variant("brti_no_dampen", brti_dampening=1.0),
    Variant("buffer_100usd", min_abs_distance_usd=100.0, min_distance_sigma=0.50),
    Variant("buffer_150usd", min_abs_distance_usd=150.0, min_distance_sigma=0.70),
    Variant("basis_shrink_75", basis_sigma_usd=75.0, max_basis_shrink=0.45),
    Variant("vol_dist_075", min_distance_sigma=0.75, min_abs_distance_usd=50.0),
    Variant(
        "momentum_exhaustion_guard",
        momentum_guard_usd=125.0,
        momentum_guard_distance_sigma=1.15,
        momentum_guard_distance_usd=200.0,
    ),
    Variant(
        "no_dump_chase_guard",
        no_momentum_guard_usd=125.0,
        no_momentum_guard_distance_usd=225.0,
        no_momentum_guard_distance_sigma=1.20,
    ),
    Variant(
        "live_loss_guard",
        max_no_p=0.20,
        no_edge_add_cents=4.0,
        min_abs_distance_usd=75.0,
        no_momentum_guard_usd=75.0,
        no_momentum_guard_distance_usd=250.0,
        no_momentum_guard_distance_sigma=1.35,
    ),
    Variant(
        "market_shrink_no_cautious",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.20,
        no_edge_add_cents=3.0,
    ),
    Variant(
        "js_time_guard",
        market_shrink=0.25,
        min_edge_cents=8.0,
        min_entry=0.55,
        max_entry=0.80,
        exclude_utc_hours=tuple(range(17, 24)),
    ),
    Variant(
        "may8_guarded",
        market_shrink=0.35,
        basis_sigma_usd=75.0,
        max_basis_shrink=0.40,
        min_abs_distance_usd=50.0,
        min_distance_sigma=0.60,
        momentum_guard_usd=125.0,
        momentum_guard_distance_sigma=1.15,
        momentum_guard_distance_usd=200.0,
        no_momentum_guard_usd=100.0,
        no_momentum_guard_distance_usd=225.0,
        no_momentum_guard_distance_sigma=1.20,
        min_entry=0.35,
    ),
    Variant(
        "may8_conservative",
        market_shrink=0.50,
        basis_sigma_usd=100.0,
        max_basis_shrink=0.50,
        min_abs_distance_usd=75.0,
        min_distance_sigma=0.80,
        momentum_guard_usd=100.0,
        momentum_guard_distance_sigma=1.35,
        momentum_guard_distance_usd=250.0,
        no_momentum_guard_usd=75.0,
        no_momentum_guard_distance_usd=275.0,
        no_momentum_guard_distance_sigma=1.50,
        min_entry=0.50,
    ),
    Variant(
        "jump_guard_moderate",
        market_shrink=0.25,
        min_edge_cents=10.0,
        momentum_guard_usd=100.0,
        momentum_guard_distance_usd=275.0,
        momentum_guard_distance_sigma=1.35,
        max_entry=0.65,
    ),
    Variant(
        "jump_guard_strict",
        market_shrink=0.35,
        min_edge_cents=12.0,
        momentum_guard_usd=75.0,
        momentum_guard_distance_usd=325.0,
        momentum_guard_distance_sigma=1.60,
        max_entry=0.60,
        min_distance_sigma=0.50,
    ),
    Variant(
        "no_dump_chase_strict",
        market_shrink=0.25,
        min_edge_cents=10.0,
        max_no_p=0.18,
        no_edge_add_cents=8.0,
        no_momentum_guard_usd=75.0,
        no_momentum_guard_distance_usd=325.0,
        no_momentum_guard_distance_sigma=1.60,
        max_entry=0.60,
    ),
    Variant(
        "yes_pump_chase_strict",
        market_shrink=0.35,
        min_edge_cents=14.0,
        yes_edge_add_cents=4.0,
        momentum_guard_usd=75.0,
        momentum_guard_distance_usd=300.0,
        momentum_guard_distance_sigma=1.50,
        allow_no=False,
        max_entry=0.60,
    ),
    Variant(
        "strict_low_entry_core",
        market_shrink=0.35,
        min_edge_cents=18.0,
        max_entry=0.60,
        max_no_p=0.18,
        no_edge_add_cents=8.0,
        min_distance_sigma=0.60,
        max_spread_cents=1.0,
    ),
    Variant(
        "basis_jump_guard",
        market_shrink=0.25,
        basis_sigma_usd=100.0,
        max_basis_shrink=0.50,
        min_edge_cents=12.0,
        momentum_guard_usd=100.0,
        momentum_guard_distance_usd=275.0,
        momentum_guard_distance_sigma=1.35,
        no_momentum_guard_usd=75.0,
        no_momentum_guard_distance_usd=325.0,
        no_momentum_guard_distance_sigma=1.60,
        max_entry=0.65,
    ),
    Variant(
        "late_hour_reversal_guard",
        market_shrink=0.35,
        min_edge_cents=12.0,
        min_ttl_min=12.0,
        max_ttl_min=50.0,
        momentum_guard_usd=75.0,
        momentum_guard_distance_usd=300.0,
        momentum_guard_distance_sigma=1.50,
        max_entry=0.65,
    ),
    Variant(
        "market_shrink_no_cautious_lowrisk",
        market_shrink=0.50,
        min_edge_cents=18.0,
        max_entry=0.60,
        max_no_p=0.18,
        no_edge_add_cents=8.0,
        min_distance_sigma=0.50,
        max_spread_cents=1.0,
    ),
    Variant(
        "lowrisk_high_entry_surcharge",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.20,
        no_edge_add_cents=3.0,
        entry_edge_add_over_65_cents=4.0,
        entry_edge_add_over_70_cents=4.0,
    ),
    Variant(
        "lowrisk_no_extra",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.18,
        no_edge_add_cents=8.0,
    ),
    Variant(
        "market_shrink_no_cautious_shape_loose",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.20,
        no_edge_add_cents=3.0,
        max_shape_violation_cents=2.0,
        min_event_valid_markets=4,
        max_event_median_spread_cents=2.0,
    ),
    Variant(
        "market_shrink_no_cautious_shape_adjacent",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.20,
        no_edge_add_cents=3.0,
        max_shape_violation_cents=2.0,
        min_event_valid_markets=4,
        max_event_median_spread_cents=2.0,
        adjacent_min_gross_edge_cents=4.0,
    ),
]

GLOBAL_MIN_ENTRY = min(v.min_entry for v in VARIANTS)
GLOBAL_MAX_ENTRY = max(v.max_entry for v in VARIANTS)
GLOBAL_MAX_SPREAD_CENTS = max(v.max_spread_cents for v in VARIANTS)


@dataclass
class Position:
    row: dict[str, Any]


def ts_arg(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def split_name(close_time: pd.Timestamp, source: str) -> str:
    if source == "live_capture":
        return "live_capture"
    close_time = close_time.tz_convert("UTC")
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def select_variants(names: list[str] | None) -> list[Variant]:
    if not names:
        return VARIANTS
    lookup = {v.name: v for v in VARIANTS}
    missing = [name for name in names if name not in lookup]
    if missing:
        raise ValueError(f"unknown variants: {missing}; available={sorted(lookup)}")
    return [lookup[name] for name in names]


def parse_market_strike(ticker: str) -> float | None:
    match = re.search(r"-T(?P<strike>\d+(?:\.\d+)?)", str(ticker).upper())
    if not match:
        return None
    return float(match.group("strike"))


def parse_event_close(event_ticker: str) -> pd.Timestamp | None:
    return csv_bt.parse_event_close_from_ticker(str(event_ticker).upper())


def event_open_from_close(close_time: pd.Timestamp) -> pd.Timestamp:
    return close_time - pd.Timedelta(minutes=60)


def load_duckdb_quotes(
    db_path: Path,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    max_events: int | None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    con = duckdb.connect(str(db_path), read_only=True)
    event_params: list[Any] = []
    event_where = ["m.is_hourly_kxbtcd", "m.is_cumulative"]
    if start is not None:
        event_where.append("m.close_time >= ?")
        event_params.append(start.to_pydatetime())
    if end is not None:
        event_where.append("m.close_time < ?")
        event_params.append(end.to_pydatetime())
    selected_events: list[str] | None = None
    if max_events is not None:
        selected_events = [
            row[0]
            for row in con.execute(
                f"""
                SELECT m.event_ticker
                FROM kalshi_markets m
                WHERE {' AND '.join(event_where)}
                GROUP BY 1
                ORDER BY 1
                LIMIT ?
                """,
                [*event_params, int(max_events)],
            ).fetchall()
        ]
        if not selected_events:
            con.close()
            return pd.DataFrame(), pd.DataFrame(), {"source": "historical_duckdb", "db": str(db_path)}

    where = [
        "m.is_hourly_kxbtcd",
        "m.is_cumulative",
        "q.spread_cents <= ?",
        "(q.yes_ask_exe BETWEEN ? AND ? OR q.no_ask_exe BETWEEN ? AND ?)",
    ]
    params: list[Any] = [
        GLOBAL_MAX_SPREAD_CENTS,
        GLOBAL_MIN_ENTRY,
        GLOBAL_MAX_ENTRY,
        GLOBAL_MIN_ENTRY,
        GLOBAL_MAX_ENTRY,
    ]
    if start is not None:
        where.append("m.close_time >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("m.close_time < ?")
        params.append(end.to_pydatetime())
    if selected_events is not None:
        placeholders = ",".join("?" for _ in selected_events)
        where.append(f"q.event_ticker IN ({placeholders})")
        params.extend(selected_events)

    quotes = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, q.fidelity, m.open_time, m.close_time,
               m.event_open_time, m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE {' AND '.join(where)}
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
        """,
        params,
    ).fetchdf()
    if quotes.empty:
        con.close()
        return quotes, pd.DataFrame(), {"source": "historical_duckdb", "db": str(db_path)}
    btc_start = utc_series(quotes["event_open_time"]).min() - pd.Timedelta(days=10)
    btc_end = utc_series(quotes["close_time"]).max() + pd.Timedelta(minutes=1)
    btc = con.execute(
        """
        SELECT available_at AS time, open, high, low, close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        WHERE available_at >= ? AND available_at <= ?
        ORDER BY available_at
        """,
        [btc_start.to_pydatetime(), btc_end.to_pydatetime()],
    ).fetchdf()
    meta = con.execute(
        """
        SELECT fidelity, source, count(*) AS n_rows, count(distinct event_ticker) AS events
        FROM kalshi_quotes
        GROUP BY 1, 2
        ORDER BY n_rows DESC
        """
    ).fetchdf().to_dict(orient="records")
    con.close()
    for col in ("available_at", "ts_end", "open_time", "close_time", "event_open_time"):
        quotes[col] = utc_series(quotes[col])
    btc["time"] = utc_series(btc["time"])
    return quotes, feature_frame(btc), {
        "source": "historical_duckdb",
        "db": str(db_path),
        "quote_rows": int(len(quotes)),
        "events": int(quotes["event_ticker"].nunique()),
        "btc_rows": int(len(btc)),
        "quote_sources": meta,
        "settlement": "BTC minute close proxy from the datamart, not official Kalshi expiration_value.",
    }


def feature_frame(btc: pd.DataFrame) -> pd.DataFrame:
    out = btc.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if "log_ret" not in out or out["log_ret"].isna().all():
        out["log_ret"] = np.log(out["close"] / out["close"].shift(1))
    af = MINUTES_PER_YEAR
    if "rv_15m" not in out or out["rv_15m"].isna().all():
        out["rv_15m"] = out["log_ret"].rolling(15).std() * np.sqrt(af)
    if "rv_60m" not in out or out["rv_60m"].isna().all():
        out["rv_60m"] = out["log_ret"].rolling(60).std() * np.sqrt(af)
    if "rv_1d" not in out or out["rv_1d"].isna().all():
        out["rv_1d"] = out["log_ret"].rolling(1440).std() * np.sqrt(af)
    if "rkurt_60m" not in out or out["rkurt_60m"].isna().all():
        out["rkurt_60m"] = out["log_ret"].rolling(60).kurt()
    return out


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    if btc.empty:
        return None, None
    values = btc["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def build_event_cache(btc: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train = btc[(btc["time"] >= event_open - pd.Timedelta(days=train_days)) & (btc["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        return None
    return build_emp_cache_fast(train)


def edge_uncertainty_cents(model_p: np.ndarray, emp_cache: dict) -> np.ndarray:
    counts = np.asarray([emp_cache[h]["n"] for h in emp_cache if h in emp_cache], dtype=float)
    n_eff = max(200.0, float(np.nanmedian(counts)) * 0.30) if len(counts) else 200.0
    return 100.0 * 1.64 * np.sqrt(np.clip(model_p * (1.0 - model_p), 0.0, 0.25) / n_eff)


def lookback_ret(btc: pd.DataFrame, idx: int, minutes: int) -> float:
    prior = idx - int(minutes)
    if prior < 0:
        return float("nan")
    return float(btc.iloc[idx]["close"] - btc.iloc[prior]["close"])


def base_candidates(quotes: pd.DataFrame, btc: pd.DataFrame, emp_cache: dict, scan_ts: pd.Timestamp) -> pd.DataFrame:
    spot, idx = btc_at_or_before(btc, scan_ts)
    if spot is None or idx is None or idx < 1440:
        return pd.DataFrame()
    close_time = pd.Timestamp(quotes["close_time"].iloc[0])
    ttl_min = (close_time - scan_ts).total_seconds() / 60.0
    if ttl_min <= MIN_TTL_MIN or ttl_min > MAX_TTL_MIN:
        return pd.DataFrame()
    rv60 = btc.iloc[idx].get("rv_60m", np.nan)
    current_vol = float(rv60) if np.isfinite(rv60) else None
    strikes = quotes["floor_strike"].to_numpy(dtype=float)
    yes_bid = quotes["yes_bid_close"].to_numpy(dtype=float)
    yes_ask = quotes["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = quotes["no_ask_exe"].to_numpy(dtype=float)
    yes_ask_close = quotes["yes_ask_close"].to_numpy(dtype=float)
    spread = quotes["spread_cents"].to_numpy(dtype=float)
    valid = (
        np.isfinite(strikes)
        & np.isfinite(yes_bid)
        & np.isfinite(yes_ask_close)
        & np.isfinite(yes_ask)
        & np.isfinite(no_ask)
        & np.isfinite(spread)
        & (yes_bid >= 0.0)
        & (yes_ask_close <= 1.0)
        & (yes_ask_close >= yes_bid)
        & (yes_ask >= 0.0)
        & (yes_ask <= 1.0)
        & (no_ask >= 0.0)
        & (no_ask <= 1.0)
    )
    if not valid.any():
        return pd.DataFrame()
    q = quotes.loc[valid].copy()
    strikes = q["floor_strike"].to_numpy(dtype=float)
    emp_p = vectorized_p_above(strikes, spot, ttl_min, emp_cache, current_vol, BRTI_DAMPENING)
    normal_p = lognormal_p_above(strikes, spot, ttl_min, current_vol)
    model_p = np.where(np.isfinite(normal_p), 0.70 * emp_p + 0.30 * normal_p, emp_p)
    finite = np.isfinite(model_p) | np.isfinite(emp_p) | np.isfinite(normal_p)
    if not finite.any():
        return pd.DataFrame()
    q = q.iloc[np.where(finite)[0]].copy()
    model_p = model_p[finite]
    emp_p = emp_p[finite]
    normal_p = normal_p[finite]
    expected_move = float("nan")
    if current_vol is not None and np.isfinite(current_vol) and current_vol > 0:
        expected_move = float(spot * current_vol * math.sqrt(ttl_min / MINUTES_PER_YEAR))
    q["raw_model_p_yes"] = model_p
    q["emp_p_default"] = emp_p
    q["lognormal_p_yes"] = normal_p
    q["entry_spot"] = spot
    q["ttl_min"] = ttl_min
    q["current_vol"] = current_vol if current_vol is not None else np.nan
    q["expected_move_usd"] = expected_move
    q["market_mid"] = 0.5 * (q["yes_bid_close"].astype(float) + q["yes_ask_close"].astype(float))
    q["event_valid_markets"] = int(len(q))
    q["event_median_spread_cents"] = float(q["spread_cents"].median())
    shape = q[["floor_strike", "market_mid"]].copy().sort_values("floor_strike")
    mids = shape["market_mid"].to_numpy(dtype=float)
    violation = np.zeros(len(shape), dtype=float)
    if len(shape) >= 2:
        # Cumulative above/below YES prices should be non-increasing as strike rises.
        pair_violation = np.maximum(0.0, mids[1:] - mids[:-1]) * 100.0
        violation[:-1] = np.maximum(violation[:-1], pair_violation)
        violation[1:] = np.maximum(violation[1:], pair_violation)
    q["shape_violation_cents"] = pd.Series(violation, index=shape.index).reindex(q.index).to_numpy(dtype=float)
    q["ret_5m"] = lookback_ret(btc, idx, 5)
    q["ret_10m"] = lookback_ret(btc, idx, 10)
    q["ret_30m"] = lookback_ret(btc, idx, 30)
    q["scan_time"] = scan_ts
    return q


def signals_for_variant(candidates: pd.DataFrame, variant: Variant, emp_cache: dict) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    q = candidates.copy()
    strikes = q["floor_strike"].to_numpy(dtype=float)
    spot = q["entry_spot"].to_numpy(dtype=float)
    spot0 = float(q["entry_spot"].iloc[0])
    ttl0 = float(q["ttl_min"].iloc[0])
    current_vol0 = float(q["current_vol"].iloc[0]) if np.isfinite(float(q["current_vol"].iloc[0])) else None
    if abs(float(variant.brti_dampening) - float(BRTI_DAMPENING)) < 1e-12:
        p_emp = q["emp_p_default"].to_numpy(dtype=float)
    else:
        p_emp = vectorized_p_above(
            strikes,
            spot0,
            ttl0,
            emp_cache,
            current_vol0,
            variant.brti_dampening,
        )
    p_log = q["lognormal_p_yes"].to_numpy(dtype=float)
    total_weight = max(1e-9, float(variant.emp_weight) + float(variant.lognormal_weight))
    p = np.full(len(q), np.nan, dtype=float)
    both = np.isfinite(p_emp) & np.isfinite(p_log)
    p[both] = (variant.emp_weight * p_emp[both] + variant.lognormal_weight * p_log[both]) / total_weight
    only_emp = np.isfinite(p_emp) & ~np.isfinite(p)
    p[only_emp] = p_emp[only_emp]
    only_log = np.isfinite(p_log) & ~np.isfinite(p)
    p[only_log] = p_log[only_log]
    if variant.basis_sigma_usd > 0 and variant.max_basis_shrink > 0:
        dist_abs = np.abs(spot - strikes)
        shrink = variant.basis_sigma_usd / (dist_abs + variant.basis_sigma_usd)
        shrink = np.clip(shrink, 0.0, variant.max_basis_shrink)
        p = (1.0 - shrink) * p + shrink * 0.5
    if variant.market_shrink > 0:
        market_mid = q["market_mid"].to_numpy(dtype=float)
        p = (1.0 - variant.market_shrink) * p + variant.market_shrink * market_mid

    yes_ask = q["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = q["no_ask_exe"].to_numpy(dtype=float)
    if "yes_ask_qty" in q.columns:
        yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce").to_numpy(dtype=float)
    else:
        yes_qty = np.ones(len(q), dtype=float)
    if "no_ask_qty" in q.columns:
        no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce").to_numpy(dtype=float)
    else:
        no_qty = np.ones(len(q), dtype=float)
    edge_yes = p - yes_ask
    edge_no = (1.0 - p) - no_ask
    choose_yes = edge_yes > edge_no
    side = np.where(choose_yes, "yes", "no")
    entry = np.where(choose_yes, yes_ask, no_ask)
    visible_qty = np.where(choose_yes, yes_qty, no_qty)
    gross_edge = np.where(choose_yes, edge_yes, edge_no) * 100.0
    fees = np.asarray([kalshi_fee_dollars(float(price), contracts=1, liquidity="taker") for price in entry])
    net_edge = gross_edge - fees * 100.0
    threshold = variant.min_edge_cents + edge_uncertainty_cents(p, emp_cache)
    threshold = threshold + np.where(side == "yes", variant.yes_edge_add_cents, variant.no_edge_add_cents)
    if variant.entry_edge_add_over_65_cents > 0:
        threshold = threshold + np.where(entry > 0.65 + 1e-12, variant.entry_edge_add_over_65_cents, 0.0)
    if variant.entry_edge_add_over_70_cents > 0:
        threshold = threshold + np.where(entry > 0.70 + 1e-12, variant.entry_edge_add_over_70_cents, 0.0)
    strong = np.where(side == "yes", p >= variant.min_yes_p, p <= variant.max_no_p)

    signed_dist = np.where(side == "yes", spot - strikes, strikes - spot)
    side_p = np.where(side == "yes", p, 1.0 - p)
    exp_move = q["expected_move_usd"].to_numpy(dtype=float)
    distance_sigma = np.divide(
        signed_dist,
        exp_move,
        out=np.full(len(q), np.nan),
        where=np.isfinite(exp_move) & (exp_move > 1e-9),
    )
    ret5 = q["ret_5m"].to_numpy(dtype=float)
    ret10 = q["ret_10m"].to_numpy(dtype=float)
    ret30 = q["ret_30m"].to_numpy(dtype=float)
    side_ret5 = np.where(side == "yes", ret5, -ret5)
    side_ret10 = np.where(side == "yes", ret10, -ret10)
    side_ret30 = np.where(side == "yes", ret30, -ret30)

    passed = (
        strong
        & (net_edge >= threshold)
        & (q["spread_cents"].to_numpy(dtype=float) <= variant.max_spread_cents)
        & (entry >= variant.min_entry)
        & (entry <= variant.max_entry)
        & np.isfinite(visible_qty)
        & (visible_qty >= 1.0)
        & (q["ttl_min"].to_numpy(dtype=float) >= variant.min_ttl_min)
        & (q["ttl_min"].to_numpy(dtype=float) <= variant.max_ttl_min)
    )
    if variant.max_shape_violation_cents < 999:
        passed &= q["shape_violation_cents"].to_numpy(dtype=float) <= variant.max_shape_violation_cents + 1e-12
    if variant.min_event_valid_markets > 0:
        passed &= q["event_valid_markets"].to_numpy(dtype=float) >= float(variant.min_event_valid_markets)
    if variant.max_event_median_spread_cents < 999:
        passed &= q["event_median_spread_cents"].to_numpy(dtype=float) <= variant.max_event_median_spread_cents + 1e-12
    if variant.adjacent_min_gross_edge_cents > -999:
        order = np.argsort(strikes)
        adjacent_ok = np.zeros(len(q), dtype=bool)
        candidate_edge = np.where(side == "yes", edge_yes, edge_no) * 100.0
        for pos, idx in enumerate(order):
            left = order[pos - 1] if pos > 0 else None
            right = order[pos + 1] if pos + 1 < len(order) else None
            ok = False
            if left is not None:
                ok = ok or (side[left] == side[idx] and candidate_edge[left] >= variant.adjacent_min_gross_edge_cents)
            if right is not None:
                ok = ok or (side[right] == side[idx] and candidate_edge[right] >= variant.adjacent_min_gross_edge_cents)
            adjacent_ok[idx] = ok
        passed &= adjacent_ok
    if not variant.allow_yes:
        passed &= side != "yes"
    if not variant.allow_no:
        passed &= side != "no"
    if variant.exclude_utc_hours:
        hours = q["scan_time"].dt.hour.to_numpy(dtype=int)
        passed &= ~np.isin(hours, np.asarray(variant.exclude_utc_hours, dtype=int))
    if variant.min_abs_distance_usd > 0:
        passed &= signed_dist >= variant.min_abs_distance_usd
    if variant.min_distance_sigma > 0:
        passed &= np.isfinite(distance_sigma) & (distance_sigma >= variant.min_distance_sigma)
    if variant.momentum_guard_usd > 0:
        exhaustion = (
            (side_ret10 >= variant.momentum_guard_usd)
            & (
                (signed_dist < variant.momentum_guard_distance_usd)
                | (np.isfinite(distance_sigma) & (distance_sigma < variant.momentum_guard_distance_sigma))
            )
        )
        passed &= ~exhaustion
    if variant.no_momentum_guard_usd > 0:
        no_exhaustion = (
            (side == "no")
            & (side_ret10 >= variant.no_momentum_guard_usd)
            & (
                (signed_dist < variant.no_momentum_guard_distance_usd)
                | (np.isfinite(distance_sigma) & (distance_sigma < variant.no_momentum_guard_distance_sigma))
            )
        )
        passed &= ~no_exhaustion
    if not passed.any():
        return pd.DataFrame()
    idxs = np.where(passed)[0]
    out = q.iloc[idxs].copy()
    out["variant"] = variant.name
    out["model_p_yes"] = p[idxs]
    out["side"] = side[idxs]
    out["entry_price"] = entry[idxs]
    out["entry_fee"] = fees[idxs]
    out["visible_qty"] = visible_qty[idxs]
    out["net_edge_cents"] = net_edge[idxs]
    out["edge_threshold_cents"] = threshold[idxs]
    out["side_probability"] = side_p[idxs]
    out["signed_distance_usd"] = signed_dist[idxs]
    out["distance_sigma"] = distance_sigma[idxs]
    out["side_ret_5m"] = side_ret5[idxs]
    out["side_ret_10m"] = side_ret10[idxs]
    out["side_ret_30m"] = side_ret30[idxs]
    return out.sort_values("net_edge_cents", ascending=False)


def settle_due(
    open_positions: dict[str, Position],
    open_events: set[str],
    now: pd.Timestamp,
    btc: pd.DataFrame,
    settled: list[dict[str, Any]],
    official_cache: dict[str, tuple[str | None, float | None]] | None = None,
) -> None:
    due = [ticker for ticker, pos in open_positions.items() if pd.Timestamp(pos.row["close_time"]) <= now]
    for ticker in due:
        pos = open_positions.pop(ticker)
        row = pos.row
        open_events.discard(str(row["event_ticker"]))
        official_result = None
        official_value = None
        if official_cache is not None:
            official_result, official_value = official_cache.get(ticker, (None, None))
        settlement_spot, _ = btc_at_or_before(btc, pd.Timestamp(row["close_time"]))
        if official_result in {"yes", "no"}:
            settlement = official_result
            win = settlement == row["side"]
        else:
            if settlement_spot is None:
                continue
            yes_settles = float(settlement_spot) >= float(row["strike"])
            settlement = "yes" if yes_settles else "no"
            win = (settlement == row["side"])
        payout = 1.0 if win else 0.0
        pnl = payout - float(row["entry_price"]) - float(row["entry_fee"])
        settled.append(
            {
                **row,
                "settle_time": row["close_time"],
                "settlement_spot": settlement_spot,
                "official_expiration_value": official_value,
                "settlement": settlement,
                "payout": payout,
                "pnl": pnl,
            }
        )


def run_replay(
    source_name: str,
    quotes: pd.DataFrame,
    btc: pd.DataFrame,
    train_days: int,
    progress_every_events: int,
    variants: list[Variant],
    official_cache: dict[str, tuple[str | None, float | None]] | None = None,
) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame()
    CFG.update(RESEARCH_CFG)
    quotes = quotes.sort_values(["event_ticker", "available_at", "market_ticker"]).reset_index(drop=True)
    states: dict[str, tuple[dict[str, Position], set[str], list[dict[str, Any]]]] = {
        v.name: ({}, set(), []) for v in variants
    }
    event_cache: dict[str, dict] = {}
    events = list(quotes.groupby("event_ticker", sort=True))
    for i, (event_ticker, event_quotes) in enumerate(events, start=1):
        if i == 1 or i % progress_every_events == 0:
            print(f"  {source_name}: event {i}/{len(events)} {event_ticker}", flush=True)
        event_open = pd.Timestamp(event_quotes["event_open_time"].iloc[0])
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, train_days)
            if emp_cache is None:
                continue
            event_cache[event_ticker] = emp_cache
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            scan_ts = pd.Timestamp(scan_ts)
            for open_positions, open_events, settled in states.values():
                settle_due(open_positions, open_events, scan_ts, btc, settled, official_cache)
            candidates = base_candidates(scan_quotes, btc, emp_cache, scan_ts)
            if candidates.empty:
                continue
            for variant in variants:
                open_positions, open_events, settled = states[variant.name]
                if event_ticker in open_events:
                    continue
                signals = signals_for_variant(candidates, variant, emp_cache)
                if signals.empty:
                    continue
                sig = signals.iloc[0]
                ticker = str(sig["market_ticker"]).upper()
                if ticker in open_positions:
                    continue
                open_positions[ticker] = Position(
                    row={
                        "source": source_name,
                        "variant": variant.name,
                        "split": split_name(pd.Timestamp(sig["close_time"]), source_name),
                        "event_ticker": str(sig["event_ticker"]),
                        "market_ticker": ticker,
                        "side": str(sig["side"]),
                        "strike": float(sig["floor_strike"]),
                        "entry_time": scan_ts,
                        "quote_ts_end": pd.Timestamp(sig.get("ts_end", scan_ts)),
                        "close_time": pd.Timestamp(sig["close_time"]),
                        "entry_price": float(sig["entry_price"]),
                        "entry_fee": float(sig["entry_fee"]),
                        "contracts": 1,
                        "entry_spot": float(sig["entry_spot"]),
                        "raw_model_p_yes": float(sig["raw_model_p_yes"]),
                        "model_p_yes": float(sig["model_p_yes"]),
                        "side_probability": float(sig["side_probability"]),
                        "net_edge_cents": float(sig["net_edge_cents"]),
                        "edge_threshold_cents": float(sig["edge_threshold_cents"]),
                        "spread_cents": float(sig["spread_cents"]),
                        "market_mid": float(sig["market_mid"]),
                        "expected_move_usd": float(sig["expected_move_usd"]),
                        "signed_distance_usd": float(sig["signed_distance_usd"]),
                        "distance_sigma": float(sig["distance_sigma"]),
                        "side_ret_5m": float(sig["side_ret_5m"]),
                        "side_ret_10m": float(sig["side_ret_10m"]),
                        "side_ret_30m": float(sig["side_ret_30m"]),
                        "fidelity": str(sig.get("fidelity", "")),
                    }
                )
                open_events.add(str(sig["event_ticker"]))
    final_ts = max((pd.Timestamp(pos.row["close_time"]) for state in states.values() for pos in state[0].values()), default=pd.Timestamp.utcnow())
    for open_positions, open_events, settled in states.values():
        settle_due(open_positions, open_events, final_ts + pd.Timedelta(days=1), btc, settled, official_cache)
    rows = [row for _, _, settled in states.values() for row in settled]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["source", "variant", "entry_time", "market_ticker"]).reset_index(drop=True)


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for (source, variant, split), group in trades.groupby(["source", "variant", "split"], sort=True):
        ordered = group.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
        pnl = ordered["pnl"].astype(float)
        premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
        drawdown = equity - equity.cummax()
        rows.append(
            {
                "source": source,
                "variant": variant,
                "split": split,
                "trades": int(len(ordered)),
                "pnl": float(pnl.sum()),
                "premium": float(premium.sum()),
                "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else (math.inf if len(wins) else 0.0),
                "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
                "avg_entry": float(ordered["entry_price"].mean()) if len(ordered) else 0.0,
                "avg_side_probability": float(ordered["side_probability"].mean()) if len(ordered) else 0.0,
                "avg_distance_sigma": float(ordered["distance_sigma"].replace([np.inf, -np.inf], np.nan).mean()) if len(ordered) else 0.0,
                "avg_side_ret_10m": float(ordered["side_ret_10m"].mean()) if len(ordered) else 0.0,
                "yes_trades": int((ordered["side"] == "yes").sum()),
                "no_trades": int((ordered["side"] == "no").sum()),
            }
        )
    return pd.DataFrame(rows)


def load_csv_quotes(
    data_dir: Path,
    pattern: str,
    assumed_spread_cents: float,
    max_events: int | None,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    events, verification = csv_bt.verify_kalshi_data(data_dir, pattern)
    events = [ev for ev in events if ev.populated_cells > 0]
    if start is not None:
        events = [ev for ev in events if ev.close_ts >= start]
    if end is not None:
        events = [ev for ev in events if ev.close_ts < end]
    if max_events is not None:
        events = events[:max_events]
    if not events:
        return pd.DataFrame(), pd.DataFrame(), {"source": "csv_exports", "events": 0}
    first_ts = min(ev.first_ts for ev in events)
    last_ts = max(ev.close_ts for ev in events)
    btc_raw = csv_bt.load_or_fetch_btc_minutes(
        PROJECT_ROOT / "data" / "btc_1m_backtest_cache.parquet",
        first_ts - pd.Timedelta(days=10),
        last_ts + pd.Timedelta(minutes=1),
        refresh=False,
        fill_internal_gaps=False,
    )
    btc = feature_frame(btc_raw.rename(columns={"timestamp": "time"}) if "timestamp" in btc_raw.columns else btc_raw)
    half = assumed_spread_cents / 200.0
    rows: list[pd.DataFrame] = []
    for i, ev in enumerate(events, start=1):
        if i == 1 or i % 50 == 0:
            print(f"  csv load: event {i}/{len(events)} {ev.event_ticker}", flush=True)
        raw = pd.read_csv(ev.path)
        raw["timestamp"] = utc_series(raw["timestamp"])
        strike_cols = [col for col in raw.columns if csv_bt.strike_from_column(col) is not None]
        if not strike_cols:
            continue
        pieces = []
        for col in strike_cols:
            strike = csv_bt.strike_from_column(col)
            yes_mid = pd.to_numeric(raw[col], errors="coerce") / 100.0
            valid = yes_mid.notna()
            if not valid.any():
                continue
            piece = pd.DataFrame(
                {
                    "market_ticker": csv_bt.market_ticker(ev.event_ticker, float(strike)),
                    "event_ticker": ev.event_ticker,
                    "available_at": raw.loc[valid, "timestamp"].values,
                    "ts_end": raw.loc[valid, "timestamp"].values,
                    "yes_bid_close": np.clip(yes_mid.loc[valid].to_numpy(dtype=float) - half, 0.0, 1.0),
                    "yes_ask_close": np.clip(yes_mid.loc[valid].to_numpy(dtype=float) + half, 0.0, 1.0),
                    "yes_ask_exe": np.clip(yes_mid.loc[valid].to_numpy(dtype=float) + half, 0.0, 1.0),
                    "no_ask_exe": np.clip(1.0 - yes_mid.loc[valid].to_numpy(dtype=float) + half, 0.0, 1.0),
                    "spread_cents": assumed_spread_cents,
                    "fidelity": "csv_assumed_spread",
                    "open_time": ev.first_ts - pd.Timedelta(minutes=1),
                    "close_time": ev.close_ts,
                    "event_open_time": ev.close_ts - pd.Timedelta(minutes=60),
                    "floor_strike": float(strike),
                }
            )
            pieces.append(piece)
        if pieces:
            rows.append(pd.concat(pieces, ignore_index=True))
    quotes = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    for col in ("available_at", "ts_end", "open_time", "close_time", "event_open_time"):
        if col in quotes.columns:
            quotes[col] = utc_series(quotes[col])
    if not quotes.empty:
        quotes = quotes[
            (quotes["spread_cents"].astype(float) <= GLOBAL_MAX_SPREAD_CENTS)
            & (
                quotes["yes_ask_exe"].astype(float).between(GLOBAL_MIN_ENTRY, GLOBAL_MAX_ENTRY)
                | quotes["no_ask_exe"].astype(float).between(GLOBAL_MIN_ENTRY, GLOBAL_MAX_ENTRY)
            )
        ].copy()
    return quotes, btc, {
        "source": "csv_exports",
        "events": int(len(events)),
        "quote_rows": int(len(quotes)),
        "assumed_spread_cents": assumed_spread_cents,
        "settlement": "BTC minute close proxy; CSV prices are exported prices with an assumed spread, not observed bid/ask.",
        "verification_errors": verification.get("errors", [])[:20],
    }


def snapshot_capture_db(source: Path, output_dir: Path) -> Path | None:
    if not source.exists():
        return None
    snap_dir = output_dir / "capture_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = snap_dir / f"{source.stem}_{stamp}.duckdb"
    try:
        shutil.copy2(source, dest)
        wal = source.with_suffix(source.suffix + ".wal")
        if wal.exists():
            shutil.copy2(wal, dest.with_suffix(dest.suffix + ".wal"))
        return dest
    except OSError as exc:
        print(f"  live capture snapshot failed for {source}: {exc}", flush=True)
        return None


def load_live_btc_cache(path: Path) -> pd.DataFrame:
    btc = pd.read_parquet(path)
    if "time" not in btc.columns:
        for col in ("ts", "timestamp", "bucket_start", "available_at"):
            if col in btc.columns:
                btc = btc.rename(columns={col: "time"})
                break
    btc["time"] = utc_series(btc["time"])
    return feature_frame(btc)


def fetch_official_results(market_tickers: list[str]) -> dict[str, tuple[str | None, float | None]]:
    import requests

    out: dict[str, tuple[str | None, float | None]] = {}
    session = requests.Session()
    for i, ticker in enumerate(sorted(set(market_tickers)), start=1):
        try:
            response = session.get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}", timeout=10)
            if response.status_code == 429:
                time.sleep(1.0)
                response = session.get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}", timeout=10)
            response.raise_for_status()
            market = response.json().get("market", {})
            result = str(market.get("result") or "").lower() or None
            value_raw = market.get("expiration_value")
            value = float(value_raw) if value_raw not in (None, "") else None
            out[ticker] = (result if result in {"yes", "no"} else None, value)
        except Exception:
            out[ticker] = (None, None)
        if i % 100 == 0:
            time.sleep(0.5)
    return out


def load_capture_quotes(
    capture_db: Path,
    btc_cache: Path,
    output_dir: Path,
    fetch_official: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, tuple[str | None, float | None]] | None]:
    snap = snapshot_capture_db(capture_db, output_dir)
    if snap is None:
        return pd.DataFrame(), pd.DataFrame(), {"source": "live_capture", "status": "snapshot_failed"}, None
    try:
        con = duckdb.connect(str(snap), read_only=True)
        tables = {r[0] for r in con.execute("show tables").fetchall()}
        if "ws_orderbook_top" not in tables:
            con.close()
            return pd.DataFrame(), pd.DataFrame(), {"source": "live_capture", "snapshot": str(snap), "status": "missing_ws_orderbook_top"}, None
        tops = con.execute(
            """
            SELECT received_at_utc, market_ticker, event_ticker, yes_bid, yes_ask,
                   no_bid, no_ask, btc_spot, source
            FROM ws_orderbook_top
            WHERE received_at_utc IS NOT NULL
              AND market_ticker LIKE 'KXBTCD-%'
            ORDER BY received_at_utc, market_ticker
            """
        ).fetchdf()
        con.close()
    except Exception as exc:
        return pd.DataFrame(), pd.DataFrame(), {"source": "live_capture", "snapshot": str(snap), "status": f"read_failed: {exc}"}, None
    if tops.empty:
        return pd.DataFrame(), pd.DataFrame(), {"source": "live_capture", "snapshot": str(snap), "status": "empty"}, None
    tops["available_at"] = utc_series(tops["received_at_utc"])
    tops["ts_end"] = tops["available_at"]
    tops["floor_strike"] = tops["market_ticker"].map(parse_market_strike)
    tops["close_time"] = tops["event_ticker"].map(parse_event_close)
    tops = tops.dropna(subset=["available_at", "floor_strike", "close_time"])
    tops["event_open_time"] = tops["close_time"].map(event_open_from_close)
    tops["open_time"] = tops["event_open_time"]
    tops["yes_bid_close"] = pd.to_numeric(tops["yes_bid"], errors="coerce")
    tops["yes_ask_close"] = pd.to_numeric(tops["yes_ask"], errors="coerce")
    tops["yes_ask_exe"] = tops["yes_ask_close"]
    tops["no_ask_exe"] = pd.to_numeric(tops["no_ask"], errors="coerce")
    tops["spread_cents"] = (tops["yes_ask_close"] - tops["yes_bid_close"]) * 100.0
    tops["fidelity"] = "live_ws_top_of_book"
    keep_cols = [
        "market_ticker", "event_ticker", "available_at", "ts_end",
        "yes_bid_close", "yes_ask_close", "yes_ask_exe", "no_ask_exe",
        "spread_cents", "fidelity", "open_time", "close_time",
        "event_open_time", "floor_strike",
    ]
    quotes = tops[keep_cols].dropna().copy()
    btc = load_live_btc_cache(btc_cache)
    official = fetch_official_results(quotes["market_ticker"].unique().tolist()) if fetch_official else None
    return quotes, btc, {
        "source": "live_capture",
        "snapshot": str(snap),
        "quote_rows": int(len(quotes)),
        "events": int(quotes["event_ticker"].nunique()),
        "markets": int(quotes["market_ticker"].nunique()),
        "time_start": str(quotes["available_at"].min()),
        "time_end": str(quotes["available_at"].max()),
        "settlement": "Official Kalshi result if already finalized and --fetch-official-results is enabled; otherwise BTC minute close proxy.",
    }, official


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--progress-every-events", type=int, default=100)
    parser.add_argument("--skip-historical", action="store_true")
    parser.add_argument("--include-csv", action="store_true")
    parser.add_argument("--csv-pattern", default="kalshi-price-history-kxbtcd-*.csv")
    parser.add_argument("--assumed-spread-cents", type=float, default=3.0)
    parser.add_argument("--include-live-capture", action="store_true")
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--btc-cache", type=Path, default=DEFAULT_BTC_CACHE)
    parser.add_argument("--fetch-official-results", action="store_true")
    parser.add_argument("--variant", action="append", help="Run only this variant name; repeat for multiple variants.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    variants = select_variants(args.variant)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    start = ts_arg(args.start)
    end = ts_arg(args.end)
    reports: list[dict[str, Any]] = []
    all_trades: list[pd.DataFrame] = []

    if not args.skip_historical:
        print("Loading historical DuckDB / Kalshi historical bid-ask replay...", flush=True)
        quotes, btc, report = load_duckdb_quotes(args.db, start, end, args.max_events)
        reports.append(report)
        if not quotes.empty:
            trades = run_replay("historical_duckdb", quotes, btc, args.train_days, args.progress_every_events, variants)
            all_trades.append(trades)

    if args.include_csv:
        print("Loading collected CSV export data...", flush=True)
        quotes, btc, report = load_csv_quotes(
            PROJECT_ROOT / "data",
            args.csv_pattern,
            args.assumed_spread_cents,
            args.max_events,
            start,
            end,
        )
        reports.append(report)
        if not quotes.empty:
            trades = run_replay("csv_exports", quotes, btc, args.train_days, args.progress_every_events, variants)
            all_trades.append(trades)

    if args.include_live_capture:
        print("Snapshotting and loading live websocket capture data...", flush=True)
        quotes, btc, report, official = load_capture_quotes(args.capture_db, args.btc_cache, args.output_dir, args.fetch_official_results)
        reports.append(report)
        if not quotes.empty:
            trades = run_replay("live_capture", quotes, btc, args.train_days, args.progress_every_events, variants, official)
            all_trades.append(trades)

    nonempty_trades = [df for df in all_trades if not df.empty]
    combined = pd.concat(nonempty_trades, ignore_index=True) if nonempty_trades else pd.DataFrame()
    summary = summarize(combined)
    trades_path = args.output_dir / "may8examine_trades.csv"
    summary_path = args.output_dir / "may8examine_summary.csv"
    report_path = args.output_dir / "may8examine_report.json"
    variants_path = args.output_dir / "may8examine_variants.json"
    combined.to_csv(trades_path, index=False)
    summary.to_csv(summary_path, index=False)
    variants_path.write_text(json.dumps([asdict(v) for v in variants], indent=2), encoding="utf-8")
    final_report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start": str(start),
        "end": str(end),
        "train_days": args.train_days,
        "variants": [asdict(v) for v in variants],
        "data_reports": reports,
        "trades_path": str(trades_path),
        "summary_path": str(summary_path),
        "variants_path": str(variants_path),
        "notes": [
            "Historical and CSV settlement uses BTC minute close proxy because official Kalshi expiration_value is not stored for those datasets.",
            "Live capture replay uses top-of-book changes only; it is useful for tick-level collected-data sanity checks, not a complete replacement for full orderbook replay.",
            "All PnL includes estimated Kalshi taker entry fees; no early exits are modeled in this script.",
        ],
    }
    report_path.write_text(json.dumps(final_report, indent=2, default=str), encoding="utf-8")

    if summary.empty:
        print("No trades generated.", flush=True)
    else:
        print("\nSUMMARY", flush=True)
        cols = ["source", "variant", "split", "trades", "pnl", "premium", "return_on_premium", "win_rate", "max_drawdown", "yes_trades", "no_trades"]
        with pd.option_context("display.max_rows", 500, "display.width", 220):
            print(summary[cols].sort_values(["source", "split", "pnl"], ascending=[True, True, False]).to_string(index=False), flush=True)
    print(f"\nWrote {summary_path}", flush=True)
    print(f"Wrote {trades_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
