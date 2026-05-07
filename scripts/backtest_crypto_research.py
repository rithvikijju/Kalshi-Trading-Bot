#!/usr/bin/env python3
"""Backtest multi-crypto Kalshi strategies from the crypto research DuckDB."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_1hr_collected_data import build_emp_cache_fast, lognormal_p_above, vectorized_p_above  # noqa: E402


DEFAULT_DB = PROJECT_ROOT / "data" / "crypto_research_datamart" / "crypto_research.duckdb"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "backtest_outputs" / "crypto_research"
MINUTES_PER_YEAR = 60 * 24 * 365

RESEARCH_MIN_EDGE = 12.0
RESEARCH_MAX_SPREAD = 2.0
RESEARCH_MIN_ENTRY = 0.25
RESEARCH_MAX_ENTRY = 0.75
RESEARCH_MIN_YES_P = 0.65
RESEARCH_MAX_NO_P = 0.35

JS_MIN_EDGE = 8.0
JS_MAX_SPREAD = 2.0
JS_MIN_ENTRY = 0.55
JS_MAX_ENTRY = 0.80
JS_MARKET_SHRINK = 0.25
JS_MAX_MONEYNESS_BPS = 60.0
JS_EXCLUDED_UTC_HOURS = set(range(17, 24))

RANGE_MIN_EDGE = 6.0
RANGE_MAX_SPREAD = 3.0
RANGE_MIN_ENTRY = 0.20
RANGE_MAX_ENTRY = 0.80
RANGE_MARKET_SHRINK = 0.15
RANGE_MAX_DISTANCE_BPS = 100.0

MICRO_MIN_EDGE = 5.0
MICRO_MAX_SPREAD = 3.0
MICRO_MIN_ENTRY = 0.35
MICRO_MAX_ENTRY = 0.75
MICRO_MARKET_SHRINK = 0.25
MICRO_MAX_MONEYNESS_BPS = 90.0

CRYPTO_FAIR_MIN_EDGE = 6.0
CRYPTO_FAIR_MAX_SPREAD = 3.0
CRYPTO_FAIR_MIN_ENTRY = 0.35
CRYPTO_FAIR_MAX_ENTRY = 0.80
CRYPTO_FAIR_MARKET_SHRINK = 0.20
CRYPTO_FAIR_MAX_MONEYNESS_BPS = 100.0

CRYPTO_LOOSE_MIN_EDGE = 4.0
CRYPTO_LOOSE_MAX_SPREAD = 4.0
CRYPTO_LOOSE_MIN_ENTRY = 0.25
CRYPTO_LOOSE_MAX_ENTRY = 0.85
CRYPTO_LOOSE_MARKET_SHRINK = 0.35
CRYPTO_LOOSE_MAX_MONEYNESS_BPS = 150.0

UPDOWN_FAST_MIN_EDGE = 3.0
UPDOWN_FAST_MAX_SPREAD = 4.0
UPDOWN_FAST_MIN_ENTRY = 0.25
UPDOWN_FAST_MAX_ENTRY = 0.85
UPDOWN_FAST_MARKET_SHRINK = 0.40
UPDOWN_FAST_MAX_MONEYNESS_BPS = 150.0

ETH_1H_MIN_EDGE = 6.0
ETH_1H_MAX_SPREAD = 3.0
ETH_1H_MIN_ENTRY = 0.25
ETH_1H_MAX_ENTRY = 0.80
ETH_1H_MARKET_SHRINK = 0.25
ETH_1H_MAX_MONEYNESS_BPS = 120.0

BRTI_DAMPENING = 0.80


@dataclass(frozen=True)
class OpenPosition:
    row: dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest multi-crypto Kalshi strategies from DuckDB.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=[
            "ported_research",
            "ported_js_guarded",
            "crypto_fair_value",
            "crypto_fair_value_loose",
            "range_fair_value",
            "micro_updown_guarded",
            "updown15_fast",
            "eth_1h_fair_value",
        ],
        choices=[
            "ported_research",
            "ported_js_guarded",
            "crypto_fair_value",
            "crypto_fair_value_loose",
            "range_fair_value",
            "micro_updown_guarded",
            "updown15_fast",
            "eth_1h_fair_value",
        ],
    )
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--progress-every-events", type=int, default=20)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--series", action="append", help="Restrict to one or more Kalshi series tickers.")
    return parser.parse_args()


def load_tables(db_path: Path, series_filter: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    where = [
        "q.yes_bid_close IS NOT NULL",
        "q.yes_ask_close IS NOT NULL",
        "m.floor_strike IS NOT NULL",
    ]
    params: list[object] = []
    if series_filter:
        placeholders = ", ".join(["?"] * len(series_filter))
        where.append(f"m.series_ticker IN ({placeholders})")
        params.extend(series_filter)
    quotes = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, q.fidelity,
               m.series_ticker, m.asset, m.product_id, m.family, m.frequency,
               m.market_kind, m.open_time, m.close_time, m.floor_strike,
               m.cap_strike, m.result
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE {' AND '.join(where)}
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
        """,
        params,
    ).fetchdf()
    spot = con.execute(
        """
        SELECT product_id, asset, available_at AS time, bucket_start, open, high, low,
               close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM spot_1m
        ORDER BY asset, available_at
        """
    ).fetchdf()
    con.close()
    if quotes.empty:
        return quotes, spot
    for col in ("available_at", "ts_end", "open_time", "close_time"):
        quotes[col] = pd.to_datetime(quotes[col], utc=True)
    for col in ("floor_strike", "cap_strike", "yes_bid_close", "yes_ask_close", "yes_ask_exe", "no_ask_exe", "spread_cents"):
        quotes[col] = pd.to_numeric(quotes[col], errors="coerce")
    if not spot.empty:
        spot["time"] = pd.to_datetime(spot["time"], utc=True)
        spot["bucket_start"] = pd.to_datetime(spot["bucket_start"], utc=True)
    return quotes, spot


def assign_splits(quotes: pd.DataFrame) -> dict[str, str]:
    events = (
        quotes[["event_ticker", "close_time"]]
        .drop_duplicates("event_ticker")
        .sort_values(["close_time", "event_ticker"])
        .reset_index(drop=True)
    )
    close_times = events["close_time"].drop_duplicates().sort_values().reset_index(drop=True)
    n = len(close_times)
    train_cut = max(1, int(n * 0.60))
    val_cut = max(train_cut + 1, int(n * 0.80)) if n >= 3 else n
    split_by_close: dict[pd.Timestamp, str] = {}
    for idx, close_time in close_times.items():
        if idx < train_cut:
            split_by_close[close_time] = "train"
        elif idx < val_cut:
            split_by_close[close_time] = "validation"
        else:
            split_by_close[close_time] = "test"
    out: dict[str, str] = {}
    for _, row in events.iterrows():
        split = split_by_close[pd.Timestamp(row["close_time"])]
        out[str(row["event_ticker"])] = split
    return out


def spot_for_asset(spot: pd.DataFrame, asset: str) -> pd.DataFrame:
    out = spot[spot["asset"] == asset].copy()
    return out.sort_values("time").reset_index(drop=True)


def spot_at_or_before(asset_spot: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    if asset_spot.empty:
        return None, None
    values = asset_spot["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(asset_spot):
        return None, None
    return float(asset_spot.iloc[idx]["close"]), idx


def build_event_cache(asset_spot: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train_start = event_open - pd.Timedelta(days=train_days)
    train = asset_spot[(asset_spot["time"] >= train_start) & (asset_spot["time"] <= event_open)].copy()
    if len(train) < 1440 + 241:
        return None
    return build_emp_cache_fast(train)


def edge_uncertainty_cents(model_p: np.ndarray, emp_cache: dict) -> np.ndarray:
    counts = np.asarray([emp_cache[h]["n"] for h in emp_cache if h in emp_cache], dtype=float)
    n_eff = max(200.0, float(np.nanmedian(counts)) * 0.30) if len(counts) else 200.0
    return 100.0 * 1.64 * np.sqrt(np.clip(model_p * (1.0 - model_p), 0.0, 0.25) / n_eff)


def ttl_bounds(frequency: str) -> tuple[float, float]:
    if frequency == "fifteen_min":
        return 1.0, 15.5
    return 5.0, 65.0


def model_probability_for_quotes(
    q: pd.DataFrame,
    asset_spot: pd.DataFrame,
    emp_cache: dict,
    scan_ts: pd.Timestamp,
) -> tuple[np.ndarray, float | None, int | None]:
    spot, idx = spot_at_or_before(asset_spot, scan_ts)
    if spot is None or idx is None or idx < 1440:
        return np.full(len(q), np.nan), spot, idx
    ttl_min = (q["close_time"].iloc[0] - scan_ts).total_seconds() / 60.0
    current_vol = asset_spot.iloc[idx].get("rv_60m", np.nan)
    current_vol = float(current_vol) if np.isfinite(current_vol) else None
    kind = str(q["market_kind"].iloc[0])
    floors = q["floor_strike"].to_numpy(dtype=float)
    p_floor = vectorized_p_above(floors, spot, ttl_min, emp_cache, current_vol, BRTI_DAMPENING)
    normal_floor = lognormal_p_above(floors, spot, ttl_min, current_vol)
    p_floor = np.where(np.isfinite(normal_floor), 0.70 * p_floor + 0.30 * normal_floor, p_floor)
    if kind == "range":
        caps = q["cap_strike"].to_numpy(dtype=float)
        valid_caps = np.isfinite(caps)
        p_cap = np.full(len(q), np.nan)
        if valid_caps.any():
            p_cap[valid_caps] = vectorized_p_above(caps[valid_caps], spot, ttl_min, emp_cache, current_vol, BRTI_DAMPENING)
            normal_cap = lognormal_p_above(caps[valid_caps], spot, ttl_min, current_vol)
            p_cap[valid_caps] = np.where(np.isfinite(normal_cap), 0.70 * p_cap[valid_caps] + 0.30 * normal_cap, p_cap[valid_caps])
        p_yes = np.clip(p_floor - p_cap, 0.0, 1.0)
    else:
        p_yes = p_floor
    return p_yes, spot, idx


def distance_bps(q: pd.DataFrame, spot: float) -> np.ndarray:
    floors = q["floor_strike"].to_numpy(dtype=float)
    caps = q["cap_strike"].to_numpy(dtype=float)
    kind = str(q["market_kind"].iloc[0])
    if kind == "range":
        below = np.maximum(floors - spot, 0.0)
        above = np.maximum(spot - caps, 0.0)
        distance = np.maximum(below, above)
    else:
        distance = np.abs(floors - spot)
    return 10000.0 * distance / max(1e-12, spot)


def signals_for_time(
    strategy: str,
    scan_quotes: pd.DataFrame,
    asset_spot: pd.DataFrame,
    emp_cache: dict,
    scan_ts: pd.Timestamp,
) -> pd.DataFrame:
    frequency = str(scan_quotes["frequency"].iloc[0])
    family = str(scan_quotes["family"].iloc[0])
    market_kind = str(scan_quotes["market_kind"].iloc[0])
    ttl_min = (scan_quotes["close_time"].iloc[0] - scan_ts).total_seconds() / 60.0
    min_ttl, max_ttl = ttl_bounds(frequency)
    if ttl_min <= min_ttl or ttl_min > max_ttl:
        return pd.DataFrame()
    if strategy == "range_fair_value" and market_kind != "range":
        return pd.DataFrame()
    if strategy in {"micro_updown_guarded", "updown15_fast"} and family != "updown15":
        return pd.DataFrame()
    if strategy in {"crypto_fair_value", "crypto_fair_value_loose", "eth_1h_fair_value"} and market_kind == "range":
        return pd.DataFrame()
    if strategy == "eth_1h_fair_value" and not (str(scan_quotes["asset"].iloc[0]) == "ETH" and frequency == "hourly" and family == "above_below"):
        return pd.DataFrame()

    valid = (
        scan_quotes["floor_strike"].notna()
        & scan_quotes["yes_bid_close"].between(0.0, 1.0)
        & scan_quotes["yes_ask_close"].between(0.0, 1.0)
        & scan_quotes["yes_ask_exe"].between(0.0, 1.0)
        & scan_quotes["no_ask_exe"].between(0.0, 1.0)
        & scan_quotes["spread_cents"].ge(0.0)
    )
    if market_kind == "range":
        valid &= scan_quotes["cap_strike"].notna()
    q = scan_quotes[valid].copy()
    if q.empty:
        return pd.DataFrame()

    p_yes, spot, idx = model_probability_for_quotes(q, asset_spot, emp_cache, scan_ts)
    if spot is None or idx is None:
        return pd.DataFrame()
    finite = np.isfinite(p_yes)
    if not finite.any():
        return pd.DataFrame()
    q = q.iloc[np.where(finite)[0]].copy()
    p_yes = p_yes[finite]

    market_mid = 0.5 * (q["yes_bid_close"].to_numpy(dtype=float) + q["yes_ask_close"].to_numpy(dtype=float))
    if strategy == "ported_js_guarded":
        p_yes = (1.0 - JS_MARKET_SHRINK) * p_yes + JS_MARKET_SHRINK * market_mid
    elif strategy == "crypto_fair_value":
        p_yes = (1.0 - CRYPTO_FAIR_MARKET_SHRINK) * p_yes + CRYPTO_FAIR_MARKET_SHRINK * market_mid
    elif strategy == "crypto_fair_value_loose":
        p_yes = (1.0 - CRYPTO_LOOSE_MARKET_SHRINK) * p_yes + CRYPTO_LOOSE_MARKET_SHRINK * market_mid
    elif strategy == "range_fair_value":
        p_yes = (1.0 - RANGE_MARKET_SHRINK) * p_yes + RANGE_MARKET_SHRINK * market_mid
    elif strategy == "micro_updown_guarded":
        p_yes = (1.0 - MICRO_MARKET_SHRINK) * p_yes + MICRO_MARKET_SHRINK * market_mid
    elif strategy == "updown15_fast":
        p_yes = (1.0 - UPDOWN_FAST_MARKET_SHRINK) * p_yes + UPDOWN_FAST_MARKET_SHRINK * market_mid
    elif strategy == "eth_1h_fair_value":
        p_yes = (1.0 - ETH_1H_MARKET_SHRINK) * p_yes + ETH_1H_MARKET_SHRINK * market_mid

    yes_ask = q["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = q["no_ask_exe"].to_numpy(dtype=float)
    edge_yes = p_yes - yes_ask
    edge_no = (1.0 - p_yes) - no_ask
    choose_yes = edge_yes > edge_no
    side = np.where(choose_yes, "yes", "no")
    entry_price = np.where(choose_yes, yes_ask, no_ask)
    gross_edge = np.where(choose_yes, edge_yes, edge_no) * 100.0
    fees = np.asarray([kalshi_fee_dollars(float(price), contracts=1, liquidity="taker") for price in entry_price])
    net_edge = gross_edge - fees * 100.0
    uncertainty = edge_uncertainty_cents(p_yes, emp_cache)

    if strategy == "ported_research":
        threshold = RESEARCH_MIN_EDGE + uncertainty
        strong = np.where(side == "yes", p_yes >= RESEARCH_MIN_YES_P, p_yes <= RESEARCH_MAX_NO_P)
        max_spread = RESEARCH_MAX_SPREAD
        min_entry = RESEARCH_MIN_ENTRY
        max_entry = RESEARCH_MAX_ENTRY
        passed = strong
    elif strategy == "ported_js_guarded":
        threshold = JS_MIN_EDGE + uncertainty
        max_spread = JS_MAX_SPREAD
        min_entry = JS_MIN_ENTRY
        max_entry = JS_MAX_ENTRY
        scan_hour = int(scan_ts.tz_convert("UTC").hour)
        passed = (~np.isin(np.full(len(q), scan_hour), list(JS_EXCLUDED_UTC_HOURS))) & (distance_bps(q, spot) <= JS_MAX_MONEYNESS_BPS)
    elif strategy == "crypto_fair_value":
        threshold = CRYPTO_FAIR_MIN_EDGE + 0.5 * uncertainty
        max_spread = CRYPTO_FAIR_MAX_SPREAD
        min_entry = CRYPTO_FAIR_MIN_ENTRY
        max_entry = CRYPTO_FAIR_MAX_ENTRY
        passed = distance_bps(q, spot) <= CRYPTO_FAIR_MAX_MONEYNESS_BPS
    elif strategy == "crypto_fair_value_loose":
        threshold = CRYPTO_LOOSE_MIN_EDGE + 0.35 * uncertainty
        max_spread = CRYPTO_LOOSE_MAX_SPREAD
        min_entry = CRYPTO_LOOSE_MIN_ENTRY
        max_entry = CRYPTO_LOOSE_MAX_ENTRY
        passed = distance_bps(q, spot) <= CRYPTO_LOOSE_MAX_MONEYNESS_BPS
    elif strategy == "range_fair_value":
        threshold = RANGE_MIN_EDGE + 0.5 * uncertainty
        max_spread = RANGE_MAX_SPREAD
        min_entry = RANGE_MIN_ENTRY
        max_entry = RANGE_MAX_ENTRY
        passed = distance_bps(q, spot) <= RANGE_MAX_DISTANCE_BPS
    elif strategy == "micro_updown_guarded":
        threshold = MICRO_MIN_EDGE + 0.5 * uncertainty
        max_spread = MICRO_MAX_SPREAD
        min_entry = MICRO_MIN_ENTRY
        max_entry = MICRO_MAX_ENTRY
        passed = distance_bps(q, spot) <= MICRO_MAX_MONEYNESS_BPS
    elif strategy == "updown15_fast":
        threshold = UPDOWN_FAST_MIN_EDGE + 0.25 * uncertainty
        max_spread = UPDOWN_FAST_MAX_SPREAD
        min_entry = UPDOWN_FAST_MIN_ENTRY
        max_entry = UPDOWN_FAST_MAX_ENTRY
        passed = distance_bps(q, spot) <= UPDOWN_FAST_MAX_MONEYNESS_BPS
    elif strategy == "eth_1h_fair_value":
        threshold = ETH_1H_MIN_EDGE + 0.35 * uncertainty
        max_spread = ETH_1H_MAX_SPREAD
        min_entry = ETH_1H_MIN_ENTRY
        max_entry = ETH_1H_MAX_ENTRY
        passed = distance_bps(q, spot) <= ETH_1H_MAX_MONEYNESS_BPS
    else:
        raise ValueError(strategy)

    passed &= (
        (net_edge >= threshold)
        & (q["spread_cents"].to_numpy(dtype=float) <= max_spread)
        & (entry_price >= min_entry)
        & (entry_price <= max_entry)
    )
    if not passed.any():
        return pd.DataFrame()

    idxs = np.where(passed)[0]
    out = q.iloc[idxs].copy()
    out["side"] = side[idxs]
    out["entry_price"] = entry_price[idxs]
    out["entry_fee"] = fees[idxs]
    out["model_p_yes"] = p_yes[idxs]
    out["market_mid"] = market_mid[idxs]
    out["net_edge_cents"] = net_edge[idxs]
    out["edge_threshold_cents"] = np.asarray(threshold)[idxs]
    out["entry_spot"] = spot
    out["scan_time"] = scan_ts
    out["ttl_min"] = ttl_min
    out["moneyness_bps"] = distance_bps(out, spot)
    return out.sort_values("net_edge_cents", ascending=False)


def settle_due(
    open_positions: dict[str, OpenPosition],
    open_events: set[str],
    now: pd.Timestamp,
    asset_spot_by_asset: dict[str, pd.DataFrame],
    settled: list[dict],
) -> None:
    due = [ticker for ticker, pos in open_positions.items() if pos.row["close_time"] <= now]
    for ticker in due:
        pos = open_positions.pop(ticker)
        row = pos.row
        open_events.discard(str(row["event_ticker"]))
        result = str(row.get("result") or "").lower()
        if result not in {"yes", "no"}:
            result = fallback_result(row, asset_spot_by_asset)
        if result not in {"yes", "no"}:
            continue
        payout = 1.0 if row["side"] == result else 0.0
        pnl = payout - row["entry_price"] - row["entry_fee"]
        settled.append(
            {
                **row,
                "settle_time": row["close_time"],
                "settlement": result,
                "payout": payout,
                "pnl": pnl,
            }
        )


def fallback_result(row: dict, asset_spot_by_asset: dict[str, pd.DataFrame]) -> str | None:
    asset_spot = asset_spot_by_asset.get(str(row["asset"]))
    if asset_spot is None or asset_spot.empty:
        return None
    spot, _ = spot_at_or_before(asset_spot, pd.Timestamp(row["close_time"]))
    if spot is None:
        return None
    if row["market_kind"] == "range":
        cap = row.get("cap_strike")
        if cap is None or not np.isfinite(float(cap)):
            return None
        yes = float(row["strike"]) <= spot <= float(cap)
    else:
        yes = spot >= float(row["strike"])
    return "yes" if yes else "no"


def run_strategy(
    strategy: str,
    quotes: pd.DataFrame,
    spot: pd.DataFrame,
    split_by_event: dict[str, str],
    train_days: int,
    max_events: int | None,
    progress_every: int,
) -> pd.DataFrame:
    settled: list[dict] = []
    open_positions: dict[str, OpenPosition] = {}
    open_events: set[str] = set()
    asset_spot_by_asset = {asset: spot_for_asset(spot, str(asset)) for asset in spot["asset"].dropna().unique()}
    event_cache: dict[str, dict] = {}
    events = list(quotes.groupby("event_ticker", sort=True))
    events.sort(key=lambda item: (item[1]["close_time"].iloc[0], item[0]))
    if max_events:
        events = events[:max_events]
    for idx, (event_ticker, event_quotes) in enumerate(events, start=1):
        if idx == 1 or idx % progress_every == 0:
            print(f"{strategy}: event {idx}/{len(events)} {event_ticker}", flush=True)
        asset = str(event_quotes["asset"].iloc[0])
        asset_spot = asset_spot_by_asset.get(asset)
        if asset_spot is None or asset_spot.empty:
            continue
        event_open = event_quotes["open_time"].iloc[0]
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = build_event_cache(asset_spot, event_open, train_days)
            if emp_cache is None:
                continue
            event_cache[event_ticker] = emp_cache
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            settle_due(open_positions, open_events, scan_ts, asset_spot_by_asset, settled)
            if event_ticker in open_events:
                continue
            signals = []
            for _, kind_quotes in scan_quotes.groupby("market_kind", sort=False):
                sigs = signals_for_time(strategy, kind_quotes, asset_spot, emp_cache, scan_ts)
                if not sigs.empty:
                    signals.append(sigs)
            if not signals:
                continue
            selected = pd.concat(signals, ignore_index=True).sort_values("net_edge_cents", ascending=False).head(1)
            for _, sig in selected.iterrows():
                ticker = str(sig["market_ticker"])
                if ticker in open_positions or str(sig["event_ticker"]) in open_events:
                    continue
                open_positions[ticker] = OpenPosition(
                    row={
                        "strategy": strategy,
                        "split": split_by_event.get(str(sig["event_ticker"]), "unknown"),
                        "event_ticker": sig["event_ticker"],
                        "market_ticker": ticker,
                        "series_ticker": sig["series_ticker"],
                        "asset": sig["asset"],
                        "family": sig["family"],
                        "frequency": sig["frequency"],
                        "market_kind": sig["market_kind"],
                        "side": sig["side"],
                        "strike": float(sig["floor_strike"]),
                        "cap_strike": float(sig["cap_strike"]) if pd.notna(sig["cap_strike"]) else np.nan,
                        "entry_time": scan_ts,
                        "quote_ts_end": sig["ts_end"],
                        "close_time": sig["close_time"],
                        "entry_price": float(sig["entry_price"]),
                        "entry_fee": float(sig["entry_fee"]),
                        "contracts": 1,
                        "entry_spot": float(sig["entry_spot"]),
                        "model_p_yes": float(sig["model_p_yes"]),
                        "market_mid": float(sig["market_mid"]),
                        "net_edge_cents": float(sig["net_edge_cents"]),
                        "edge_threshold_cents": float(sig["edge_threshold_cents"]),
                        "spread_cents": float(sig["spread_cents"]),
                        "ttl_min": float(sig["ttl_min"]),
                        "moneyness_bps": float(sig["moneyness_bps"]),
                        "result": sig.get("result"),
                        "fidelity": sig["fidelity"],
                    }
                )
                open_events.add(str(sig["event_ticker"]))
    final_ts = max((pd.Timestamp(pos.row["close_time"]) for pos in open_positions.values()), default=pd.Timestamp.utcnow())
    settle_due(open_positions, open_events, final_ts + pd.Timedelta(days=1), asset_spot_by_asset, settled)
    if not settled:
        return pd.DataFrame()
    return pd.DataFrame(settled).sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)


def compute_stats(trades: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    if trades.empty:
        return pd.DataFrame()
    for key, part in trades.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        ordered = part.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
        premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
        pnl = ordered["pnl"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
        drawdown = equity - equity.cummax()
        row = {col: value for col, value in zip(group_cols, key)}
        row.update(
            {
                "trades": int(len(ordered)),
                "total_pnl": float(pnl.sum()),
                "premium_deployed": float(premium.sum()),
                "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()),
                "avg_pnl": float(pnl.mean()),
                "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else float("inf"),
                "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
                "avg_edge_cents": float(ordered["net_edge_cents"].mean()),
                "avg_spread_cents": float(ordered["spread_cents"].mean()),
                "yes_trades": int((ordered["side"] == "yes").sum()),
                "no_trades": int((ordered["side"] == "no").sum()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    series_filter = [series.upper() for series in args.series] if args.series else None
    quotes, spot = load_tables(args.db, series_filter)
    if quotes.empty:
        raise SystemExit("No quotes found.")
    if spot.empty:
        raise SystemExit("No spot data found.")
    split_by_event = assign_splits(quotes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_trades = []
    for strategy in args.strategies:
        trades = run_strategy(
            strategy,
            quotes,
            spot,
            split_by_event,
            args.train_days,
            args.max_events,
            args.progress_every_events,
        )
        if not trades.empty:
            all_trades.append(trades)
            trades.to_csv(args.output_dir / f"{strategy}_trades.csv", index=False)
        else:
            pd.DataFrame().to_csv(args.output_dir / f"{strategy}_trades.csv", index=False)

    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    trades_all.to_csv(args.output_dir / "crypto_strategy_trades.csv", index=False)
    summary = compute_stats(trades_all, ["strategy", "split"]) if not trades_all.empty else pd.DataFrame()
    by_family = compute_stats(trades_all, ["strategy", "split", "family"]) if not trades_all.empty else pd.DataFrame()
    by_series = compute_stats(trades_all, ["strategy", "split", "series_ticker"]) if not trades_all.empty else pd.DataFrame()
    summary.to_csv(args.output_dir / "crypto_strategy_summary.csv", index=False)
    by_family.to_csv(args.output_dir / "crypto_strategy_by_family.csv", index=False)
    by_series.to_csv(args.output_dir / "crypto_strategy_by_series.csv", index=False)

    event_table = (
        quotes[["event_ticker", "close_time", "series_ticker", "family", "asset"]]
        .drop_duplicates("event_ticker")
        .copy()
    )
    event_table["split"] = event_table["event_ticker"].map(split_by_event)
    split_ranges = (
        event_table.groupby("split")["close_time"]
        .agg(["min", "max", "count"])
        .reset_index()
        .to_dict("records")
    )
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "db": str(args.db),
        "strategies": args.strategies,
        "series_filter": series_filter,
        "quote_rows": int(len(quotes)),
        "events": int(quotes["event_ticker"].nunique()),
        "markets": int(quotes["market_ticker"].nunique()),
        "spot_rows": int(len(spot)),
        "split_method": "chronological 60/20/20 by unique event close timestamp within collected sample",
        "split_ranges": split_ranges,
        "fee_model": "Kalshi taker fee estimate ceil_to_cent(0.07 * contracts * price * (1-price)); entry fee included in edge and PnL",
        "execution_source": "observed Kalshi 1-minute bid/ask candles; no forward fill",
        "settlement_source": "Kalshi market result when available; Coinbase spot fallback only if result missing",
        "summary_path": str(args.output_dir / "crypto_strategy_summary.csv"),
        "by_family_path": str(args.output_dir / "crypto_strategy_by_family.csv"),
        "by_series_path": str(args.output_dir / "crypto_strategy_by_series.csv"),
        "trades_path": str(args.output_dir / "crypto_strategy_trades.csv"),
    }
    (args.output_dir / "crypto_strategy_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    if not summary.empty:
        print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
