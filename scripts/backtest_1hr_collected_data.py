#!/usr/bin/env python3
"""
Backtest btc_1hr_paper.py and btc_1hr_neohardened.py on collected Kalshi CSVs.

This script is intentionally different from the older comparison scripts:
  - Kalshi prices come from data/kalshi-price-history-*.csv.
  - Each CSV remains one event, so TTL and settlement are event-specific.
  - BTC features and empirical samples are built from history available at
    the simulated time, not from future bars.
  - Signal selection calls the strategy modules' compute_edges/tradeable_signals.

The Kalshi CSVs contain historical YES price history, not bid/ask books. By
default this script treats the observed YES price as a zero-spread executable
mid/last price. Use --assumed-spread-cents to make fills more conservative.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR
import scripts.btc_1hr_paper as paper_strategy
import scripts.btc_1hr_neohardened as neo_strategy


DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "backtest_outputs"
BTC_CACHE_PATH = DATA_DIR / "btc_1m_backtest_cache.parquet"
KALSHI_FILE_RE = re.compile(
    r"^kalshi-price-history-(?P<event>[a-z0-9]+-\d{2}[a-z]{3}\d{4})-minute\.csv$",
    re.IGNORECASE,
)
STRIKE_RE = re.compile(r"^\$(?P<strike>[\d,]+(?:\.\d+)?) or above$")
NY_TZ = ZoneInfo("America/New_York")

PAPER_CFG = {
    "min_edge_cents": 4.0,
    "max_spread_cents": 3,
    "min_entry_price": 0.40,
    "max_entry_price": 0.60,
}
NEOHARDENED_CFG = {
    "min_edge_cents": 7.0,
    "max_spread_cents": 3,
    "min_entry_price": 0.20,
    "max_entry_price": 0.80,
}
RESEARCH_CFG = {
    "min_edge_cents": 12.0,
    "max_spread_cents": 2,
    "min_entry_price": 0.25,
    "max_entry_price": 0.75,
}
_TIME_VALUES_CACHE: dict[int, np.ndarray] = {}


def ceil_to_cent(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 0.0
    return math.ceil((value - 1e-12) * 100.0) / 100.0


def kalshi_fee_dollars(price: float, contracts: int = 1, liquidity: str = "taker") -> float:
    """Return the standard Kalshi trading fee for one order in dollars."""
    if contracts <= 0:
        return 0.0
    p = min(1.0, max(0.0, float(price)))
    multiplier = 0.0175 if liquidity == "maker" else 0.07
    return ceil_to_cent(multiplier * float(contracts) * p * (1.0 - p))


def fee_array(prices: np.ndarray, contracts: int = 1, liquidity: str = "taker") -> np.ndarray:
    p = np.clip(prices.astype(float), 0.0, 1.0)
    multiplier = 0.0175 if liquidity == "maker" else 0.07
    raw = multiplier * float(contracts) * p * (1.0 - p)
    return np.ceil((raw - 1e-12) * 100.0) / 100.0


@dataclass(frozen=True)
class EventFile:
    path: Path
    event_ticker: str
    first_ts: pd.Timestamp
    close_ts: pd.Timestamp
    row_count: int
    market_count: int
    populated_cells: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_event_from_filename(path: Path) -> str:
    match = KALSHI_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Unexpected Kalshi CSV filename: {path.name}")
    return match.group("event").upper()


def parse_event_close_from_ticker(event_ticker: str) -> pd.Timestamp | None:
    match = re.match(
        r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$",
        event_ticker,
    )
    if not match:
        return None
    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    mon = month_map.get(match.group("mon"))
    if mon is None:
        return None
    local = datetime(
        2000 + int(match.group("yy")),
        mon,
        int(match.group("day")),
        int(match.group("hour")),
        tzinfo=NY_TZ,
    )
    return pd.Timestamp(local.astimezone(timezone.utc))


def strike_from_column(col: str) -> float | None:
    match = STRIKE_RE.match(col)
    if not match:
        return None
    return float(match.group("strike").replace(",", ""))


def market_ticker(event_ticker: str, strike: float) -> str:
    # Kalshi encodes "$73,000 or above" as T72999.99 in the API.
    return f"{event_ticker}-T{strike - 0.01:.2f}"


def list_kalshi_files(data_dir: Path, pattern: str) -> list[Path]:
    return sorted(data_dir.glob(pattern))


def inspect_event_file(path: Path) -> tuple[EventFile | None, list[str]]:
    errors: list[str] = []
    try:
        event_ticker = parse_event_from_filename(path)
    except ValueError as exc:
        return None, [str(exc)]

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return None, [f"{path.name}: could not read CSV: {exc}"]

    if "timestamp" not in df.columns:
        return None, [f"{path.name}: missing timestamp column"]

    try:
        ts = pd.to_datetime(df["timestamp"], utc=True)
    except Exception as exc:
        return None, [f"{path.name}: invalid timestamp values: {exc}"]

    if len(df) != 60:
        errors.append(f"{path.name}: expected 60 rows, got {len(df)}")
    if ts.isna().any():
        errors.append(f"{path.name}: timestamp column contains nulls")
    if ts.duplicated().any():
        errors.append(f"{path.name}: timestamp column contains duplicates")
    if len(ts) > 1:
        deltas = ts.diff().dropna()
        if not (deltas == pd.Timedelta(minutes=1)).all():
            errors.append(f"{path.name}: timestamps are not spaced by one minute")

    expected_close = parse_event_close_from_ticker(event_ticker)
    if expected_close is not None and len(ts) > 0 and ts.iloc[-1] != expected_close:
        errors.append(
            f"{path.name}: close timestamp {ts.iloc[-1]} does not match "
            f"event ticker close {expected_close}"
        )

    strike_cols = [c for c in df.columns if strike_from_column(c) is not None]
    if not strike_cols:
        errors.append(f"{path.name}: no strike columns found")

    populated_cells = 0
    if strike_cols:
        numeric_prices = df[strike_cols].apply(pd.to_numeric, errors="coerce")
        populated_cells = int(numeric_prices.notna().sum().sum())
        bad_values = numeric_prices.notna() & ((numeric_prices < 0) | (numeric_prices > 100))
        if bool(bad_values.any().any()):
            bad_col = next(col for col in strike_cols if bool(bad_values[col].any()))
            errors.append(f"{path.name}: {bad_col} contains values outside 0..100")

    if populated_cells == 0:
        errors.append(f"{path.name}: no populated price cells")

    meta = EventFile(
        path=path,
        event_ticker=event_ticker,
        first_ts=ts.iloc[0] if len(ts) else pd.NaT,
        close_ts=ts.iloc[-1] if len(ts) else pd.NaT,
        row_count=len(df),
        market_count=len(strike_cols),
        populated_cells=populated_cells,
    )
    return meta, errors


def summarize_missing_by_month(events: list[EventFile]) -> dict:
    present = {ev.event_ticker for ev in events}
    by_month: dict[str, dict] = {}

    for ev in events:
        match = re.match(r"^[A-Z0-9]+-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$", ev.event_ticker)
        if not match:
            continue
        key = f"20{match.group('yy')}-{match.group('mon')}"
        by_month.setdefault(key, {"present": 0, "missing": [], "missing_by_hour": {}})
        by_month[key]["present"] += 1

    month_num = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    days_in_month = {
        1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
    }

    # Only compute calendar completeness for fully represented months in this
    # project. For the current dataset that mainly means April 2026.
    for key in list(by_month.keys()):
        year_s, mon_s = key.split("-")
        mon = month_num.get(mon_s)
        if mon is None:
            continue
        yy = int(year_s[-2:])
        prefix = f"KXBTCD-{yy:02d}{mon_s}"
        expected = [
            f"{prefix}{day:02d}{hour:02d}"
            for day in range(1, days_in_month[mon] + 1)
            for hour in range(24)
        ]
        missing = [ticker for ticker in expected if ticker not in present]
        missing_by_hour: dict[str, list[str]] = {}
        for ticker in missing:
            hour = ticker[-2:]
            missing_by_hour.setdefault(hour, []).append(ticker[-4:-2])
        by_month[key]["expected_calendar_candidates"] = len(expected)
        by_month[key]["missing"] = missing
        by_month[key]["missing_count"] = len(missing)
        by_month[key]["missing_by_hour"] = {
            hour: days for hour, days in sorted(missing_by_hour.items())
        }

    return by_month


def verify_kalshi_data(data_dir: Path, pattern: str) -> tuple[list[EventFile], dict]:
    paths = list_kalshi_files(data_dir, pattern)
    events: list[EventFile] = []
    errors: list[str] = []
    for path in paths:
        meta, file_errors = inspect_event_file(path)
        if meta is not None:
            events.append(meta)
        errors.extend(file_errors)

    duplicate_events = sorted(
        event for event in {ev.event_ticker for ev in events}
        if sum(1 for ev in events if ev.event_ticker == event) > 1
    )
    if duplicate_events:
        errors.append(f"duplicate event files: {duplicate_events[:20]}")

    row_counts = pd.Series([ev.row_count for ev in events], dtype="int64")
    market_counts = pd.Series([ev.market_count for ev in events], dtype="int64")
    populated = pd.Series([ev.populated_cells for ev in events], dtype="int64")

    report = {
        "checked_at": utc_now_iso(),
        "data_dir": str(data_dir),
        "pattern": pattern,
        "file_count": len(events),
        "first_timestamp": str(min((ev.first_ts for ev in events), default=pd.NaT)),
        "last_timestamp": str(max((ev.close_ts for ev in events), default=pd.NaT)),
        "row_count_min": int(row_counts.min()) if len(row_counts) else 0,
        "row_count_max": int(row_counts.max()) if len(row_counts) else 0,
        "market_count_min": int(market_counts.min()) if len(market_counts) else 0,
        "market_count_max": int(market_counts.max()) if len(market_counts) else 0,
        "populated_cells_min": int(populated.min()) if len(populated) else 0,
        "populated_cells_median": float(populated.median()) if len(populated) else 0.0,
        "populated_cells_max": int(populated.max()) if len(populated) else 0,
        "errors": errors,
        "missing_by_month": summarize_missing_by_month(events),
    }
    return sorted(events, key=lambda ev: (ev.close_ts, ev.event_ticker)), report


def fetch_coinbase_minutes(
    start: pd.Timestamp,
    end: pd.Timestamp,
    delay: float = 0.05,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    owns_session = session is None
    session = session or requests.Session()
    start_dt = start.to_pydatetime()
    end_dt = end.to_pydatetime()
    cursor = start_dt
    chunk = timedelta(minutes=300)
    frames: list[pd.DataFrame] = []
    request_count = 0
    total_chunks = max(1, math.ceil((end_dt - start_dt).total_seconds() / chunk.total_seconds()))
    print(f"  fetching BTC from Coinbase: {start} -> {end} ({total_chunks} chunks)", flush=True)

    while cursor < end_dt:
        chunk_end = min(cursor + chunk, end_dt)
        params = {
            "granularity": 60,
            "start": cursor.isoformat(),
            "end": chunk_end.isoformat(),
        }
        for attempt in range(5):
            try:
                response = session.get(url, params=params, timeout=20)
                if response.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                response.raise_for_status()
                data = response.json()
                if data:
                    df = pd.DataFrame(
                        data,
                        columns=["time", "low", "high", "open", "close", "volume"],
                    )
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    frames.append(df)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.0 * (attempt + 1))

        request_count += 1
        if request_count == 1 or request_count % 50 == 0 or request_count == total_chunks:
            print(
                f"  fetched BTC chunks: {request_count}/{total_chunks} through {chunk_end.isoformat()}",
                flush=True,
            )
        cursor = chunk_end
        if delay > 0:
            time.sleep(delay)

    if owns_session:
        session.close()

    if not frames:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )[["time", "open", "high", "low", "close", "volume"]]


def load_or_fetch_btc_minutes(
    cache_path: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    refresh: bool = False,
    fill_internal_gaps: bool = False,
) -> pd.DataFrame:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    if cache_path.exists() and not refresh:
        print(f"  reading BTC cache: {cache_path}", flush=True)
        existing = pd.read_parquet(cache_path)
        existing["time"] = pd.to_datetime(existing["time"], utc=True)
        keep_cols = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in existing.columns]
        existing = existing[keep_cols].copy()
    elif refresh and cache_path.exists():
        print("  refresh requested; rebuilding BTC cache edge coverage", flush=True)
    else:
        print(f"  BTC cache not found; building {cache_path}", flush=True)

    existing = existing.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    needed_existing = existing[(existing["time"] >= start) & (existing["time"] <= end)].copy()
    if not needed_existing.empty:
        print(
            f"  cached BTC coverage in requested range: "
            f"{needed_existing['time'].min()} -> {needed_existing['time'].max()} "
            f"({len(needed_existing):,} rows)",
            flush=True,
        )

    missing_ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    if needed_existing.empty:
        missing_ranges.append((start, end))
    else:
        have_start = needed_existing["time"].min()
        have_end = needed_existing["time"].max()
        if have_start > start:
            missing_ranges.append((start, have_start - pd.Timedelta(minutes=1)))
        if have_end < end:
            missing_ranges.append((have_end + pd.Timedelta(minutes=1), end))

        if fill_internal_gaps:
            sorted_existing = needed_existing.sort_values("time").reset_index(drop=True)
            diffs = sorted_existing["time"].diff()
            gap_locs = diffs[diffs > pd.Timedelta(minutes=1)].index.tolist()
            if len(gap_locs) > 50:
                print(
                    f"  found {len(gap_locs):,} internal BTC gaps; refetching the requested span in chunks",
                    flush=True,
                )
                missing_ranges.append((start, end))
            else:
                for loc in gap_locs:
                    prev_ts = sorted_existing.loc[loc - 1, "time"]
                    next_ts = sorted_existing.loc[loc, "time"]
                    gap_start = max(prev_ts + pd.Timedelta(minutes=1), start)
                    gap_end = min(next_ts - pd.Timedelta(minutes=1), end)
                    if gap_start <= gap_end:
                        missing_ranges.append((gap_start, gap_end))
        else:
            sorted_existing = needed_existing.sort_values("time").reset_index(drop=True)
            gaps = sorted_existing["time"].diff()
            internal_gap_count = int((gaps > pd.Timedelta(minutes=1)).sum())
            if internal_gap_count:
                print(
                    f"  leaving {internal_gap_count:,} internal BTC cache gaps unfilled "
                    "(use --fill-internal-btc-gaps to fetch them)",
                    flush=True,
                )

    fetched: list[pd.DataFrame] = []
    if missing_ranges:
        with requests.Session() as session:
            for i, (lo, hi) in enumerate(missing_ranges, start=1):
                if lo > hi:
                    continue
                print(f"  BTC cache missing range {i}/{len(missing_ranges)}: {lo} -> {hi}", flush=True)
                fetched.append(fetch_coinbase_minutes(lo, hi + pd.Timedelta(minutes=1), session=session))
    else:
        print("  BTC cache already covers requested edge range", flush=True)

    if fetched:
        existing = pd.concat([existing, *fetched], ignore_index=True)
        existing = existing.drop_duplicates("time").sort_values("time").reset_index(drop=True)
        existing.to_parquet(cache_path, index=False)
        print(f"  wrote BTC cache: {cache_path} ({len(existing):,} rows)", flush=True)

    out = existing[(existing["time"] >= start) & (existing["time"] <= end)].copy()
    out = out.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    out["log_ret"] = np.log(out["close"] / out["close"].shift(1))
    return out


def compute_paper_features(btc: pd.DataFrame) -> pd.DataFrame:
    df = btc.copy().sort_values("time").reset_index(drop=True)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    af = MINUTES_PER_YEAR
    df["rv_15m"] = df["log_ret"].rolling(15).std() * np.sqrt(af)
    df["rv_60m"] = df["log_ret"].rolling(60).std() * np.sqrt(af)
    df["rv_1d"] = df["log_ret"].rolling(1440).std() * np.sqrt(af)
    df["rkurt_60m"] = df["log_ret"].rolling(60).kurt()
    return df


def compute_neohardened_features(btc: pd.DataFrame) -> pd.DataFrame:
    df = btc.copy().sort_values("time").reset_index(drop=True)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    af = MINUTES_PER_YEAR
    df["rv_15m"] = df["log_ret"].rolling(15).std() * np.sqrt(af)
    log_ho = np.log(df["high"] / df["open"])
    log_lo = np.log(df["low"] / df["open"])
    log_co = np.log(df["close"] / df["open"])
    log_oc = np.log(df["open"] / df["close"].shift(1))
    close_vol = log_oc.rolling(60).var()
    open_vol = log_co.rolling(60).var()
    rs_vol = (log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)).rolling(60).mean()
    k = 0.34 / (1.34 + 61 / 59)
    df["rv_60m"] = np.sqrt((close_vol + k * open_vol + (1 - k) * rs_vol) * af)
    df["rv_1d"] = df["log_ret"].rolling(1440).std() * np.sqrt(af)
    df["rkurt_60m"] = df["log_ret"].rolling(60).kurt()
    return df


def apply_strategy_cfg(strategy_name: str) -> None:
    if strategy_name == "paper":
        CFG.update(PAPER_CFG)
    elif strategy_name == "neohardened":
        CFG.update(NEOHARDENED_CFG)
    elif strategy_name == "research":
        CFG.update(RESEARCH_CFG)
    else:
        raise ValueError(f"unknown strategy {strategy_name}")


def strategy_module(strategy_name: str):
    if strategy_name == "research":
        raise ValueError("research strategy is only implemented in the vectorized backtest engine")
    return paper_strategy if strategy_name == "paper" else neo_strategy


def feature_frame_for_strategy(strategy_name: str, btc: pd.DataFrame) -> pd.DataFrame:
    return compute_paper_features(btc) if strategy_name == "paper" else compute_neohardened_features(btc)


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    if ts.tzinfo is not None:
        lookup_ts = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    else:
        lookup_ts = ts.to_datetime64()
    cache_key = id(btc)
    time_values = _TIME_VALUES_CACHE.get(cache_key)
    if time_values is None:
        time_values = btc["time"].dt.tz_localize(None).values
        _TIME_VALUES_CACHE[cache_key] = time_values
    idx = int(np.searchsorted(time_values, lookup_ts, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def build_markets_from_row(
    row: pd.Series,
    event_ticker: str,
    close_ts: pd.Timestamp,
    assumed_spread_cents: float,
) -> list[dict]:
    markets = []
    half_spread = assumed_spread_cents / 200.0
    for col, raw in row.items():
        strike = strike_from_column(col)
        if strike is None or pd.isna(raw):
            continue
        try:
            yes_mid = float(raw) / 100.0
        except Exception:
            continue
        if not math.isfinite(yes_mid) or yes_mid < 0 or yes_mid > 1:
            continue

        yes_bid = max(0.0, yes_mid - half_spread)
        yes_ask = min(1.0, yes_mid + half_spread)
        no_mid = 1.0 - yes_mid
        no_bid = max(0.0, no_mid - half_spread)
        no_ask = min(1.0, no_mid + half_spread)
        markets.append(
            {
                "ticker": market_ticker(event_ticker, strike),
                "event_ticker": event_ticker,
                "floor_strike": strike,
                "cap_strike": None,
                "strike_type": "greater",
                "yes_bid_dollars": yes_bid,
                "yes_ask_dollars": yes_ask,
                "no_bid_dollars": no_bid,
                "no_ask_dollars": no_ask,
                "open_interest_fp": None,
                "volume_24h_fp": None,
                "status": "active",
                "close_time": close_ts.isoformat(),
            }
        )
    return markets


def build_emp_cache_for_event(
    strategy_name: str,
    features: pd.DataFrame,
    event_open: pd.Timestamp,
    train_days: int,
) -> dict | None:
    module = strategy_module(strategy_name)
    train_start = event_open - pd.Timedelta(days=train_days)
    train = features[(features["time"] >= train_start) & (features["time"] <= event_open)].copy()
    min_required = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_required:
        return None
    apply_strategy_cfg(strategy_name)
    with contextlib.redirect_stdout(io.StringIO()):
        return module.build_live_cache(train, verbose=False)


def settle_positions(
    open_positions: dict[str, dict],
    now_ts: pd.Timestamp,
    btc_features: pd.DataFrame,
    settled: list[dict],
) -> None:
    to_close = [
        ticker for ticker, pos in open_positions.items()
        if pos["close_time"] <= now_ts
    ]
    for ticker in to_close:
        pos = open_positions.pop(ticker)
        settlement_spot, _ = btc_at_or_before(btc_features, pos["close_time"])
        if settlement_spot is None:
            continue
        yes_settles = settlement_spot >= pos["strike"]
        payout = 1.0 if (yes_settles and pos["side"] == "yes") or ((not yes_settles) and pos["side"] == "no") else 0.0
        entry_fee = float(pos.get("entry_fee", 0.0))
        pnl = payout * pos["contracts"] - pos["entry_price"] * pos["contracts"] - entry_fee
        settled.append({
            **pos,
            "settle_time": pos["close_time"],
            "settlement_spot": settlement_spot,
            "settlement": "yes" if yes_settles else "no",
            "payout": payout * pos["contracts"],
            "pnl": pnl,
        })


def should_skip_neohardened_for_cooldown(settled: list[dict], scan_ts: pd.Timestamp) -> bool:
    recent = sorted(settled, key=lambda r: r["settle_time"])
    recent = [r for r in recent if r.get("strategy") == "neohardened"]
    if len(recent) < 3:
        return False
    last3 = recent[-3:]
    if not all(r["pnl"] <= 0 for r in last3):
        return False
    return (scan_ts - last3[-1]["settle_time"]).total_seconds() < 30 * 60


def select_signals_for_scan(
    strategy_name: str,
    event: EventFile,
    row: pd.Series,
    scan_ts: pd.Timestamp,
    btc_features: pd.DataFrame,
    emp_cache: dict,
    assumed_spread_cents: float,
) -> pd.DataFrame:
    module = strategy_module(strategy_name)
    ttl_hours = (event.close_ts - scan_ts).total_seconds() / 3600.0
    markets = build_markets_from_row(row, event.event_ticker, event.close_ts, assumed_spread_cents)
    if not markets:
        return pd.DataFrame()

    spot, idx = btc_at_or_before(btc_features, scan_ts)
    if spot is None or idx is None:
        return pd.DataFrame()
    hist_start = max(0, idx - (CFG["btc_data_days"] * 24 * 60 + 120))
    btc_window = btc_features.iloc[hist_start:idx + 1].copy()
    if len(btc_window) < 1440:
        return pd.DataFrame()

    old_spot_fn = module.get_btc_spot
    module.get_btc_spot = lambda: spot
    apply_strategy_cfg(strategy_name)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            edges = module.compute_edges(
                {"event_ticker": event.event_ticker, "ttl_hours": ttl_hours, "markets": markets},
                btc_window,
                emp_cache,
            )
            if strategy_name == "paper":
                signals = module.tradeable_signals(edges)
            else:
                signals = module.tradeable_signals(edges, btc_window)
    finally:
        module.get_btc_spot = old_spot_fn

    if signals is None or len(signals) == 0:
        return pd.DataFrame()
    signals = signals.copy()
    signals["event_ticker"] = event.event_ticker
    signals["scan_time"] = scan_ts
    signals["close_time"] = event.close_ts
    signals["entry_spot"] = spot
    return signals.sort_values("net_edge_cents", ascending=False)


def run_strategy_backtest_module(
    strategy_name: str,
    events: list[EventFile],
    btc_raw: pd.DataFrame,
    train_days: int,
    assumed_spread_cents: float,
    fee_liquidity: str,
    max_events: int | None = None,
    progress_every: int = 50,
) -> pd.DataFrame:
    apply_strategy_cfg(strategy_name)
    features = feature_frame_for_strategy(strategy_name, btc_raw)
    open_positions: dict[str, dict] = {}
    settled: list[dict] = []
    cache_by_event: dict[str, dict] = {}
    event_iter = events[:max_events] if max_events else events

    for i, event in enumerate(event_iter, start=1):
        if i == 1 or i % progress_every == 0:
            print(f"  {strategy_name}: event {i}/{len(event_iter)} {event.event_ticker}", flush=True)

        df = pd.read_csv(event.path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        event_open = event.first_ts - pd.Timedelta(minutes=1)
        cache = cache_by_event.get(event.event_ticker)
        if cache is None:
            cache = build_emp_cache_for_event(strategy_name, features, event_open, train_days)
            if cache is None:
                continue
            cache_by_event[event.event_ticker] = cache

        for _, row in df.iterrows():
            scan_ts = row["timestamp"]
            settle_positions(open_positions, scan_ts, features, settled)

            ttl_min = (event.close_ts - scan_ts).total_seconds() / 60.0
            if ttl_min <= CFG["min_ttl_min"] or ttl_min >= CFG["max_ttl_hours"] * 60:
                continue
            if strategy_name == "neohardened" and should_skip_neohardened_for_cooldown(settled, scan_ts):
                continue

            signals = select_signals_for_scan(
                strategy_name=strategy_name,
                event=event,
                row=row,
                scan_ts=scan_ts,
                btc_features=features,
                emp_cache=cache,
                assumed_spread_cents=assumed_spread_cents,
            )
            if len(signals) == 0:
                continue
            signals = signals.copy()
            signals["entry_fee"] = signals["entry_price"].apply(
                lambda price: kalshi_fee_dollars(float(price), contracts=1, liquidity=fee_liquidity)
            )
            signals["net_edge_cents"] = signals["edge_gross_cents"] - signals["entry_fee"] * 100.0

            selected = signals.sort_values("net_edge_cents", ascending=False).head(CFG["max_concurrent_signals"])
            if strategy_name == "neohardened":
                filtered = []
                event_counts: dict[str, int] = {}
                for _, sig in selected.iterrows():
                    ev = sig.get("event_ticker", "")
                    if event_counts.get(ev, 0) >= 2:
                        continue
                    event_counts[ev] = event_counts.get(ev, 0) + 1
                    filtered.append(sig)
                selected = pd.DataFrame(filtered) if filtered else pd.DataFrame()

            for _, sig in selected.iterrows():
                ticker = sig["ticker"]
                if ticker in open_positions:
                    continue
                strike = float(sig.get("floor", sig.get("floor_strike", np.nan)))
                if not math.isfinite(strike):
                    continue
                open_positions[ticker] = {
                    "strategy": strategy_name,
                    "event_ticker": event.event_ticker,
                    "market_ticker": ticker,
                    "side": sig["side"],
                    "strike": strike,
                    "entry_time": scan_ts,
                    "close_time": event.close_ts,
                    "entry_price": float(sig["entry_price"]),
                    "entry_fee": float(sig["entry_fee"]),
                    "contracts": 1,
                    "entry_spot": float(sig["entry_spot"]),
                    "model_p_yes": float(sig["model_p_yes"]),
                    "net_edge_cents": float(sig["net_edge_cents"]),
                    "spread_cents": float(sig.get("spread_cents", 0.0)),
                }

    final_time = max((ev.close_ts for ev in event_iter), default=pd.Timestamp.utcnow())
    settle_positions(open_positions, final_time + pd.Timedelta(days=1), features, settled)
    return pd.DataFrame(settled).sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)


def build_emp_cache_fast(features: pd.DataFrame) -> dict:
    close = features["close"].to_numpy(dtype=float)
    rv60 = features["rv_60m"].to_numpy(dtype=float)
    n_rows = len(features)
    cache: dict[int, dict] = {}

    for horizon in CFG["emp_horizons"]:
        start_idx = 1440
        end_idx = n_rows - int(horizon)
        if end_idx <= start_idx:
            cache[horizon] = {
                "horizon_min": horizon,
                "log_returns": np.array([], dtype=float),
                "starting_vols": np.array([], dtype=float),
                "n": 0,
            }
            continue

        idx = np.arange(start_idx, end_idx)
        valid = (
            np.isfinite(rv60[idx])
            & np.isfinite(close[idx])
            & np.isfinite(close[idx + int(horizon)])
            & (close[idx] > 0)
            & (close[idx + int(horizon)] > 0)
        )
        idx = idx[valid]
        returns = np.log(close[idx + int(horizon)] / close[idx])
        vols = rv60[idx]

        if len(returns) > CFG["emp_n_samples"]:
            rng = np.random.default_rng(0)
            keep = rng.choice(len(returns), CFG["emp_n_samples"], replace=False)
            returns = returns[keep]
            vols = vols[keep]
        if len(returns):
            returns = returns - returns.mean()

        cache[horizon] = {
            "horizon_min": horizon,
            "log_returns": returns,
            "starting_vols": vols,
            "n": len(returns),
        }
    return cache


def vectorized_p_above(
    strikes: np.ndarray,
    spot: float,
    ttl_min: float,
    emp_cache: dict,
    current_vol: float | None,
    brti_dampening: float,
) -> np.ndarray:
    horizons = sorted(emp_cache.keys())
    if not horizons or spot <= 0:
        return np.full(len(strikes), np.nan)

    horizon = min(horizons, key=lambda h: abs(h - ttl_min))
    samples = emp_cache[horizon]
    returns = samples["log_returns"]
    vols = samples["starting_vols"]
    if len(returns) == 0:
        return np.full(len(strikes), 0.5)

    returns = returns * brti_dampening
    if ttl_min != horizon and horizon > 0:
        returns = returns * math.sqrt(ttl_min / horizon)

    if current_vol is not None and np.isfinite(current_vol) and len(vols) > 100:
        k = max(200, int(len(vols) * 0.30))
        k = min(k, len(vols))
        keep = np.argpartition(np.abs(vols - current_vol), k - 1)[:k]
        returns = returns[keep]

    sorted_returns = np.sort(returns)
    thresholds = np.log(strikes / spot)
    above_counts = len(sorted_returns) - np.searchsorted(sorted_returns, thresholds, side="right")
    return above_counts / len(sorted_returns)


def lognormal_p_above(
    strikes: np.ndarray,
    spot: float,
    ttl_min: float,
    annual_vol: float | None,
) -> np.ndarray:
    if spot <= 0 or annual_vol is None or not np.isfinite(annual_vol) or annual_vol <= 0:
        return np.full(len(strikes), np.nan)
    variance = (annual_vol ** 2) * ttl_min / MINUTES_PER_YEAR
    if variance <= 0:
        return (spot >= strikes).astype(float)
    sigma = math.sqrt(variance)
    z = (np.log(strikes / spot) + 0.5 * variance) / sigma
    return np.asarray([0.5 * math.erfc(float(v) / math.sqrt(2.0)) for v in z], dtype=float)


def vectorized_signals_for_row(
    strategy_name: str,
    event: EventFile,
    scan_ts: pd.Timestamp,
    row_prices: np.ndarray,
    strikes: np.ndarray,
    strike_cols: list[str],
    btc_features: pd.DataFrame,
    emp_cache: dict,
    assumed_spread_cents: float,
    fee_liquidity: str,
) -> pd.DataFrame:
    spot, idx = btc_at_or_before(btc_features, scan_ts)
    if spot is None or idx is None or idx < 1440:
        return pd.DataFrame()

    rv60 = btc_features.iloc[idx].get("rv_60m", np.nan)
    current_vol = float(rv60) if np.isfinite(rv60) else None
    ttl_min = (event.close_ts - scan_ts).total_seconds() / 60.0
    valid = np.isfinite(row_prices) & (row_prices >= 0) & (row_prices <= 100)
    if not valid.any():
        return pd.DataFrame()

    valid_prices = row_prices[valid] / 100.0
    valid_strikes = strikes[valid]
    valid_cols = [col for col, keep in zip(strike_cols, valid) if keep]
    if strategy_name == "neohardened":
        brti_dampening = 0.65
    elif strategy_name == "research":
        brti_dampening = 0.80
    else:
        brti_dampening = 1.0
    model_p = vectorized_p_above(
        valid_strikes,
        spot=spot,
        ttl_min=ttl_min,
        emp_cache=emp_cache,
        current_vol=current_vol,
        brti_dampening=brti_dampening,
    )
    if strategy_name == "research":
        normal_p = lognormal_p_above(valid_strikes, spot=spot, ttl_min=ttl_min, annual_vol=current_vol)
        has_normal = np.isfinite(normal_p)
        model_p = np.where(has_normal, 0.70 * model_p + 0.30 * normal_p, model_p)
    finite = np.isfinite(model_p)
    if not finite.any():
        return pd.DataFrame()

    valid_prices = valid_prices[finite]
    valid_strikes = valid_strikes[finite]
    model_p = model_p[finite]
    valid_cols = [col for col, keep in zip(valid_cols, finite) if keep]

    half_spread = assumed_spread_cents / 200.0
    if strategy_name in {"paper", "research"}:
        yes_ask = np.clip(valid_prices + half_spread, 0.0, 1.0)
        no_ask = np.clip(1.0 - valid_prices + half_spread, 0.0, 1.0)
        edge_yes = model_p - yes_ask
        edge_no = (1.0 - model_p) - no_ask
        choose_yes = edge_yes > edge_no
        entry_price = np.where(choose_yes, yes_ask, no_ask)
        edge_gross_cents = np.where(choose_yes, edge_yes, edge_no) * 100.0
        side = np.where(choose_yes, "yes", "no")
        if strategy_name == "research":
            sample_counts = np.asarray([emp_cache[h]["n"] for h in emp_cache.keys() if h in emp_cache], dtype=float)
            n_eff = max(200.0, float(np.nanmedian(sample_counts)) * 0.30) if len(sample_counts) else 200.0
            uncertainty_cents = 100.0 * 1.64 * np.sqrt(np.clip(model_p * (1.0 - model_p), 0.0, 0.25) / n_eff)
            edge_threshold = CFG["min_edge_cents"] + uncertainty_cents
            strong_prob_mask = np.where(side == "yes", model_p >= 0.65, model_p <= 0.35)
            coinflip_mask = ~strong_prob_mask
        else:
            edge_threshold = CFG["min_edge_cents"]
            coinflip_mask = np.zeros(len(model_p), dtype=bool)
    else:
        yes_mid = valid_prices
        no_mid = 1.0 - valid_prices
        yes_entry = np.clip(yes_mid + half_spread, 0.0, 1.0)
        no_entry = np.clip(no_mid + half_spread, 0.0, 1.0)
        edge_yes = model_p - yes_entry
        edge_no = (1.0 - model_p) - no_entry
        choose_yes = edge_yes > edge_no
        entry_price = np.where(choose_yes, yes_entry, no_entry)
        edge_gross_cents = np.where(choose_yes, edge_yes, edge_no) * 100.0
        side = np.where(choose_yes, "yes", "no")
        rv60_cur = current_vol if current_vol is not None else 0.50
        if rv60_cur > 0.80:
            dyn_edge = 5.0
        elif rv60_cur < 0.40:
            dyn_edge = 10.0
        else:
            dyn_edge = 7.0
        edge_threshold = max(dyn_edge, 10.0)
        coinflip_mask = (model_p >= 0.45) & (model_p <= 0.55)

    entry_fee = fee_array(entry_price, contracts=1, liquidity=fee_liquidity)
    net_edge = edge_gross_cents - entry_fee * 100.0
    spread_cents = np.full(len(model_p), assumed_spread_cents, dtype=float)
    passed = (
        (net_edge >= edge_threshold)
        & (~coinflip_mask)
        & (spread_cents <= CFG["max_spread_cents"])
        & (entry_price >= CFG["min_entry_price"])
        & (entry_price <= CFG["max_entry_price"])
    )
    if not passed.any():
        return pd.DataFrame()

    rows = []
    for j in np.where(passed)[0]:
        strike = float(valid_strikes[j])
        rows.append({
            "ticker": market_ticker(event.event_ticker, strike),
            "event_ticker": event.event_ticker,
            "scan_time": scan_ts,
            "close_time": event.close_ts,
            "floor": strike,
            "column": valid_cols[j],
            "side": side[j],
            "entry_price": float(entry_price[j]),
            "entry_fee": float(entry_fee[j]),
            "model_p_yes": float(model_p[j]),
            "net_edge_cents": float(net_edge[j]),
            "spread_cents": float(spread_cents[j]),
            "entry_spot": float(spot),
        })
    return pd.DataFrame(rows).sort_values("net_edge_cents", ascending=False)


def run_strategy_backtest(
    strategy_name: str,
    events: list[EventFile],
    btc_raw: pd.DataFrame,
    train_days: int,
    assumed_spread_cents: float,
    fee_liquidity: str,
    max_events: int | None = None,
    progress_every: int = 50,
) -> pd.DataFrame:
    apply_strategy_cfg(strategy_name)
    features = feature_frame_for_strategy(strategy_name, btc_raw)
    open_positions: dict[str, dict] = {}
    settled: list[dict] = []
    event_iter = events[:max_events] if max_events else events

    for i, event in enumerate(event_iter, start=1):
        if i == 1 or i % progress_every == 0:
            print(f"  {strategy_name}: event {i}/{len(event_iter)} {event.event_ticker}", flush=True)

        event_open = event.first_ts - pd.Timedelta(minutes=1)
        train_start = event_open - pd.Timedelta(days=train_days)
        train = features[(features["time"] >= train_start) & (features["time"] <= event_open)].copy()
        if len(train) < 1440 + max(CFG["emp_horizons"]) + 1:
            continue
        emp_cache = build_emp_cache_fast(train)

        df = pd.read_csv(event.path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        strike_cols = [col for col in df.columns if strike_from_column(col) is not None]
        if not strike_cols:
            continue
        strikes = np.asarray([strike_from_column(col) for col in strike_cols], dtype=float)
        price_matrix = df[strike_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)

        for row_idx, scan_ts in enumerate(df["timestamp"]):
            settle_positions(open_positions, scan_ts, features, settled)

            ttl_min = (event.close_ts - scan_ts).total_seconds() / 60.0
            if ttl_min <= CFG["min_ttl_min"] or ttl_min >= CFG["max_ttl_hours"] * 60:
                continue
            if strategy_name == "neohardened" and should_skip_neohardened_for_cooldown(settled, scan_ts):
                continue

            signals = vectorized_signals_for_row(
                strategy_name=strategy_name,
                event=event,
                scan_ts=scan_ts,
                row_prices=price_matrix[row_idx],
                strikes=strikes,
                strike_cols=strike_cols,
                btc_features=features,
                emp_cache=emp_cache,
                assumed_spread_cents=assumed_spread_cents,
                fee_liquidity=fee_liquidity,
            )
            if signals.empty:
                continue

            selected = signals.head(CFG["max_concurrent_signals"])
            if strategy_name == "neohardened":
                selected = selected.head(2)
            elif strategy_name == "research":
                selected = selected.head(1)

            for _, sig in selected.iterrows():
                ticker = sig["ticker"]
                if ticker in open_positions:
                    continue
                open_positions[ticker] = {
                    "strategy": strategy_name,
                    "event_ticker": event.event_ticker,
                    "market_ticker": ticker,
                    "side": sig["side"],
                    "strike": float(sig["floor"]),
                    "entry_time": scan_ts,
                    "close_time": event.close_ts,
                    "entry_price": float(sig["entry_price"]),
                    "entry_fee": float(sig["entry_fee"]),
                    "contracts": 1,
                    "entry_spot": float(sig["entry_spot"]),
                    "model_p_yes": float(sig["model_p_yes"]),
                    "net_edge_cents": float(sig["net_edge_cents"]),
                    "spread_cents": float(sig["spread_cents"]),
                }

    final_time = max((ev.close_ts for ev in event_iter), default=pd.Timestamp.utcnow())
    settle_positions(open_positions, final_time + pd.Timedelta(days=1), features, settled)
    return pd.DataFrame(settled).sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)


def max_active_premium(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0

    events: list[tuple[pd.Timestamp, float]] = []
    for _, trade in trades.iterrows():
        premium = (
            float(trade["entry_price"]) * float(trade.get("contracts", 1))
            + float(trade.get("entry_fee", 0.0))
        )
        events.append((pd.Timestamp(trade["entry_time"]), premium))
        events.append((pd.Timestamp(trade["settle_time"]), -premium))

    active = 0.0
    max_active = 0.0
    for _, delta in sorted(events, key=lambda item: (item[0], -item[1])):
        active += delta
        max_active = max(max_active, active)
    return max_active


def compute_stats(trades: pd.DataFrame, strategy_name: str) -> dict:
    if trades.empty:
        return {
            "strategy": strategy_name,
            "trades": 0,
            "total_pnl": 0.0,
            "premium_deployed": 0.0,
            "return_on_premium": 0.0,
            "avg_trade_return": 0.0,
            "max_active_premium": 0.0,
            "return_on_max_active_premium": 0.0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "avg_edge_cents": 0.0,
        }

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    gross_profit = float(wins["pnl"].sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses["pnl"].sum())) if len(losses) else 0.0
    equity = trades.sort_values("settle_time")["pnl"].cumsum()
    drawdown = equity - equity.cummax()
    premium = trades["entry_price"].astype(float) * trades["contracts"].astype(float)
    if "entry_fee" in trades.columns:
        premium = premium + trades["entry_fee"].astype(float)
    total_premium = float(premium.sum())
    active_premium = max_active_premium(trades)
    trade_returns = trades["pnl"].astype(float) / premium.replace(0, np.nan)
    return {
        "strategy": strategy_name,
        "trades": int(len(trades)),
        "total_pnl": float(trades["pnl"].sum()),
        "premium_deployed": total_premium,
        "return_on_premium": float(trades["pnl"].sum()) / total_premium if total_premium else 0.0,
        "avg_trade_return": float(trade_returns.mean()) if len(trade_returns) else 0.0,
        "median_trade_return": float(trade_returns.median()) if len(trade_returns) else 0.0,
        "max_active_premium": active_premium,
        "return_on_max_active_premium": float(trades["pnl"].sum()) / active_premium if active_premium else 0.0,
        "win_rate": float((trades["pnl"] > 0).mean()),
        "avg_pnl": float(trades["pnl"].mean()),
        "median_pnl": float(trades["pnl"].median()),
        "avg_win": float(wins["pnl"].mean()) if len(wins) else 0.0,
        "avg_loss": float(losses["pnl"].mean()) if len(losses) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "avg_edge_cents": float(trades["net_edge_cents"].mean()),
        "yes_trades": int((trades["side"] == "yes").sum()),
        "no_trades": int((trades["side"] == "no").sum()),
    }


def print_verification_report(report: dict) -> None:
    print("\nDATA VERIFICATION")
    print(f"  files: {report['file_count']}")
    print(f"  time range: {report['first_timestamp']} -> {report['last_timestamp']}")
    print(f"  rows/file: {report['row_count_min']}..{report['row_count_max']}")
    print(f"  markets/file: {report['market_count_min']}..{report['market_count_max']}")
    print(
        "  populated cells/file: "
        f"{report['populated_cells_min']} min, "
        f"{report['populated_cells_median']:.0f} median, "
        f"{report['populated_cells_max']} max"
    )
    if report["errors"]:
        print(f"  errors: {len(report['errors'])}")
        for err in report["errors"][:20]:
            print(f"    - {err}")
    else:
        print("  structural checks: OK")

    april = report["missing_by_month"].get("2026-APR")
    if april:
        print(
            "  April calendar candidates: "
            f"{april['present']} present / {april['expected_calendar_candidates']} possible"
        )
        if april["missing_count"]:
            compact = {
                hour: ",".join(days)
                for hour, days in april["missing_by_hour"].items()
            }
            print(f"  April missing candidate pattern: {compact}")


def print_summary(stats: list[dict]) -> None:
    print("\nBACKTEST SUMMARY")
    names = [s["strategy"] for s in stats]
    by_name = {s["strategy"]: s for s in stats}
    col_w = 14
    print(f"{'metric':<22}" + "".join(f" {name:>{col_w}}" for name in names))
    print("-" * (22 + 1 + (col_w + 1) * len(names)))
    rows = [
        ("trades", "trades", "int"),
        ("total_pnl", "total_pnl", "float"),
        ("premium_deployed", "premium_deployed", "float"),
        ("return_on_premium", "return_on_premium", "pct"),
        ("avg_trade_return", "avg_trade_return", "pct"),
        ("max_active_premium", "max_active_premium", "float"),
        ("return_on_max_active", "return_on_max_active_premium", "pct"),
        ("win_rate", "win_rate", "pct"),
        ("avg_pnl", "avg_pnl", "float"),
        ("profit_factor", "profit_factor", "float"),
        ("max_drawdown", "max_drawdown", "float"),
        ("avg_edge_cents", "avg_edge_cents", "float"),
        ("yes_trades", "yes_trades", "int"),
        ("no_trades", "no_trades", "int"),
    ]
    for label, key, kind in rows:
        line = f"{label:<22}"
        for name in names:
            value = by_name.get(name, {}).get(key, 0.0)
            if kind == "pct":
                cell = f"{float(value):>{col_w - 1}.2%}"
            elif kind == "float":
                cell = f"{float(value):>{col_w}.4f}"
            else:
                cell = f"{int(value):>{col_w}}"
            line += " " + cell
        print(line)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument(
        "--pattern",
        default="kalshi-price-history-kxbtcd-*.csv",
        help="Glob under --data-dir for collected Kalshi CSVs.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--btc-cache", type=Path, default=BTC_CACHE_PATH)
    parser.add_argument("--refresh-btc", action="store_true")
    parser.add_argument(
        "--fill-internal-btc-gaps",
        action="store_true",
        help=(
            "Fetch missing one-minute bars inside the existing BTC cache range. "
            "This can make hundreds or thousands of Coinbase calls, so it is off by default."
        ),
    )
    parser.add_argument("--train-days", type=int, default=int(CFG["btc_data_days"]))
    parser.add_argument("--assumed-spread-cents", type=float, default=0.0)
    parser.add_argument(
        "--fee-liquidity",
        choices=("taker", "maker"),
        default="taker",
        help="Kalshi fee schedule to apply. Default is standard taker fees.",
    )
    parser.add_argument(
        "--progress-every-events",
        type=int,
        default=25,
        help="Print strategy progress every N event CSVs.",
    )
    parser.add_argument(
        "--engine",
        choices=("vectorized", "module"),
        default="vectorized",
        help=(
            "vectorized is fast and mirrors the strategy math for cumulative "
            "BTC markets; module calls the strategy functions once per minute "
            "and is mainly for small exact spot checks."
        ),
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=("paper", "neohardened", "research"),
        default=["paper", "neohardened", "research"],
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--max-events", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    args.output_dir.mkdir(parents=True, exist_ok=True)

    events, report = verify_kalshi_data(args.data_dir, args.pattern)
    print_verification_report(report)

    report_path = args.output_dir / "kalshi_data_verification.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  wrote {report_path}")

    if report["errors"]:
        print("\nData verification found structural errors; refusing to backtest.")
        return 1
    if args.verify_only:
        return 0
    if not events:
        print("No event CSVs found.")
        return 1
    if args.engine == "module" and "research" in args.strategies:
        print("The research strategy is only available with --engine vectorized.")
        return 1

    first_ts = min(ev.first_ts for ev in events)
    last_ts = max(ev.close_ts for ev in events)
    btc_start = first_ts - pd.Timedelta(days=args.train_days + 1)
    btc_end = last_ts + pd.Timedelta(hours=1)
    print("\nBTC DATA")
    print(f"  needed range: {btc_start} -> {btc_end}")
    btc_raw = load_or_fetch_btc_minutes(
        args.btc_cache,
        btc_start,
        btc_end,
        refresh=args.refresh_btc,
        fill_internal_gaps=args.fill_internal_btc_gaps,
    )
    print(f"  loaded BTC minutes: {len(btc_raw):,} ({btc_raw['time'].min()} -> {btc_raw['time'].max()})")
    if len(btc_raw) < 1440 + max(CFG["emp_horizons"]):
        print("Not enough BTC history for empirical sample construction.")
        return 1

    stats: list[dict] = []
    for strategy_name in args.strategies:
        t0 = time.time()
        print(f"\nRUNNING {strategy_name.upper()}")
        runner = run_strategy_backtest_module if args.engine == "module" else run_strategy_backtest
        trades = runner(
            strategy_name=strategy_name,
            events=events,
            btc_raw=btc_raw,
            train_days=args.train_days,
            assumed_spread_cents=args.assumed_spread_cents,
            fee_liquidity=args.fee_liquidity,
            max_events=args.max_events,
            progress_every=max(1, args.progress_every_events),
        )
        trade_path = args.output_dir / f"backtest_1hr_{strategy_name}_trades.csv"
        trades.to_csv(trade_path, index=False)
        strategy_stats = compute_stats(trades, strategy_name)
        stats.append(strategy_stats)
        print(
            f"  done in {time.time() - t0:.1f}s: "
            f"{strategy_stats['trades']} trades, pnl={strategy_stats['total_pnl']:+.4f}"
        )
        print(f"  wrote {trade_path}")

    summary = pd.DataFrame(stats)
    summary_path = args.output_dir / "backtest_1hr_summary.csv"
    summary.to_csv(summary_path, index=False)
    print_summary(stats)
    print(f"\nWrote summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
