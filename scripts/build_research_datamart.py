#!/usr/bin/env python3
"""Build a Parquet + DuckDB datamart for BTC/Kalshi research backtests.

The generated quote table is deliberately stricter than the old wide CSV
exports: it keeps only observed historical bid/ask candles and never
forward-fills prices. Coinbase candle timestamps are also normalized so the
bar close is only available at bucket_start + 1 minute.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUT_DIR = DATA_DIR / "research_datamart"
NY_TZ = ZoneInfo("America/New_York")
EVENT_RE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})")
MARKET_STRIKE_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)$")
CSV_FILE_RE = re.compile(
    r"^kalshi-price-history-(?P<event>[a-z0-9]+-\d{2}[a-z]{3}\d{4})-minute\.csv$",
    re.IGNORECASE,
)
CSV_STRIKE_RE = re.compile(r"^\$(?P<strike>[\d,]+(?:\.\d+)?) or above$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the research Parquet/DuckDB datamart.")
    parser.add_argument("--source-sqlite", type=Path, default=DATA_DIR / "kalshi_history.db")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--csv-dir", action="append", type=Path, default=[DATA_DIR])
    parser.add_argument("--btc-cache", type=Path, help="Defaults to <out-dir>/btc_1m_cache.parquet.")
    parser.add_argument("--series", default="KXBTCD")
    parser.add_argument("--start", help="Optional inclusive UTC start timestamp or event ticker.")
    parser.add_argument("--end", help="Optional inclusive UTC end timestamp or event ticker.")
    parser.add_argument("--skip-csv-raw", action="store_true")
    parser.add_argument("--skip-btc-fetch", action="store_true")
    parser.add_argument("--fill-internal-btc-gaps", action="store_true")
    parser.add_argument("--bidask-dir", type=Path, help="Defaults to <out-dir>/kalshi_bidask_events if it exists.")
    return parser.parse_args()


def parse_time_bound(value: str | None, is_end: bool) -> pd.Timestamp | None:
    if not value:
        return None
    value = value.strip().upper()
    if EVENT_RE.match(value):
        close_time = event_close_from_ticker(value)
        if close_time is None:
            return None
        return close_time if is_end else close_time - pd.Timedelta(hours=1)
    return pd.Timestamp(value, tz="UTC") if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert("UTC")


def event_close_from_ticker(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker).upper())
    if not match:
        return None
    months = {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
    }
    month = months.get(match.group("mon"))
    if month is None:
        return None
    local = datetime(
        2000 + int(match.group("yy")),
        month,
        int(match.group("day")),
        int(match.group("hour")),
        tzinfo=NY_TZ,
    )
    return pd.Timestamp(local).tz_convert("UTC")


def normalize_strike(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    frac = out - math.floor(out)
    if abs(frac - 0.99) < 1e-9:
        out += 0.01
    return float(round(out, 2))


def strike_from_ticker(ticker: str) -> float | None:
    match = MARKET_STRIKE_RE.search(str(ticker))
    if not match:
        return None
    return normalize_strike(match.group("strike"))


def parse_json_price(value: Any, key: str = "close") -> float | None:
    if value is None or value == "":
        return None
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            try:
                out = float(value)
            except ValueError:
                return None
            return out if math.isfinite(out) else None
    if isinstance(data, dict):
        raw = data.get(key)
    else:
        raw = data
    if raw is None or raw == "":
        return None
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def read_sqlite_markets(sqlite_path: Path, series: str) -> pd.DataFrame:
    if not sqlite_path.exists():
        return empty_markets()
    con = sqlite3.connect(sqlite_path)
    query = """
        SELECT ticker AS market_ticker, series_ticker, event_ticker, open_time,
               close_time, floor_strike, cap_strike, status
        FROM markets
        WHERE ticker LIKE ? OR event_ticker LIKE ?
    """
    df = pd.read_sql_query(query, con, params=(f"{series}-%", f"{series}-%"))
    con.close()
    if df.empty:
        return empty_markets()
    df["series_ticker"] = df["series_ticker"].fillna(series)
    df["event_ticker"] = df["event_ticker"].fillna(df["market_ticker"].str.extract(rf"^({series}-\d{{2}}[A-Z]{{3}}\d{{4}})")[0])
    df["floor_strike"] = [
        normalize_strike(v) if pd.notna(v) else strike_from_ticker(t)
        for v, t in zip(df["floor_strike"], df["market_ticker"])
    ]
    df["cap_strike"] = [normalize_strike(v) if pd.notna(v) else None for v in df["cap_strike"]]
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
    missing_close = df["close_time"].isna()
    if missing_close.any():
        df.loc[missing_close, "close_time"] = df.loc[missing_close, "event_ticker"].map(event_close_from_ticker)
    missing_open = df["open_time"].isna() & df["close_time"].notna()
    df.loc[missing_open, "open_time"] = df.loc[missing_open, "close_time"] - pd.Timedelta(hours=1)
    df["event_open_time"] = df["close_time"] - pd.Timedelta(hours=1)
    df["is_cumulative"] = df["market_ticker"].str.contains("-T", regex=False) & df["floor_strike"].notna()
    df["is_hourly_kxbtcd"] = (
        df["market_ticker"].str.startswith(f"{series}-")
        & df["open_time"].notna()
        & df["close_time"].notna()
        & ((df["close_time"] - df["open_time"]) <= pd.Timedelta(minutes=65))
    )
    return df.drop_duplicates("market_ticker").reset_index(drop=True)


def empty_markets() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "market_ticker",
            "series_ticker",
            "event_ticker",
            "open_time",
            "close_time",
            "floor_strike",
            "cap_strike",
            "status",
            "event_open_time",
            "is_cumulative",
            "is_hourly_kxbtcd",
        ]
    )


def read_sqlite_quotes(sqlite_path: Path, markets: pd.DataFrame, series: str) -> pd.DataFrame:
    if not sqlite_path.exists() or markets.empty:
        return pd.DataFrame()
    con = sqlite3.connect(sqlite_path)
    query = """
        SELECT market_ticker, end_period_ts, yes_bid, yes_ask, price, volume, open_interest
        FROM candlesticks
        WHERE end_period_ts != 0
          AND market_ticker LIKE ?
          AND yes_bid IS NOT NULL
          AND yes_ask IS NOT NULL
    """
    chunks: list[pd.DataFrame] = []
    market_cols = markets[["market_ticker", "event_ticker"]]
    for chunk in pd.read_sql_query(query, con, params=(f"{series}-%",), chunksize=100_000):
        if chunk.empty:
            continue
        for side in ("yes_bid", "yes_ask"):
            for field in ("open", "high", "low", "close"):
                chunk[f"{side}_{field}"] = [parse_json_price(v, field) for v in chunk[side]]
        chunk["price_mean"] = [parse_json_price(v, "mean") for v in chunk["price"]]
        chunk["ts_end"] = pd.to_datetime(chunk["end_period_ts"], unit="s", utc=True)
        chunk["available_at"] = chunk["ts_end"]
        chunk = chunk.merge(market_cols, on="market_ticker", how="left")
        chunk["yes_ask_exe"] = chunk["yes_ask_close"]
        chunk["no_ask_exe"] = 1.0 - chunk["yes_bid_close"]
        chunk["spread_cents"] = (chunk["yes_ask_close"] - chunk["yes_bid_close"]) * 100.0
        chunk["source"] = "sqlite_historical_bidask_candle"
        chunk["fidelity"] = "historical_candle_bidask"
        keep = [
            "market_ticker",
            "event_ticker",
            "ts_end",
            "available_at",
            "yes_bid_open",
            "yes_bid_high",
            "yes_bid_low",
            "yes_bid_close",
            "yes_ask_open",
            "yes_ask_high",
            "yes_ask_low",
            "yes_ask_close",
            "yes_ask_exe",
            "no_ask_exe",
            "spread_cents",
            "price_mean",
            "volume",
            "open_interest",
            "source",
            "fidelity",
        ]
        chunks.append(chunk[keep])
    con.close()
    if not chunks:
        return pd.DataFrame()
    quotes = pd.concat(chunks, ignore_index=True)
    numeric_cols = ["yes_bid_close", "yes_ask_close", "yes_ask_exe", "no_ask_exe", "spread_cents"]
    for col in numeric_cols:
        quotes = quotes[quotes[col].between(0.0, 1.0) if col != "spread_cents" else quotes[col].ge(0.0)]
    return quotes.drop_duplicates(["market_ticker", "ts_end"]).sort_values(["available_at", "market_ticker"]).reset_index(drop=True)


def csv_strike_from_column(col: str) -> float | None:
    match = CSV_STRIKE_RE.match(col)
    if not match:
        return None
    return normalize_strike(match.group("strike").replace(",", ""))


def market_ticker_from_strike(event_ticker: str, strike: float) -> str:
    return f"{event_ticker}-T{strike - 0.01:.2f}"


def read_csv_prices(csv_dirs: list[Path], series: str) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    seen: set[Path] = set()
    for csv_dir in csv_dirs:
        for path in csv_dir.rglob("kalshi-price-history-*-minute.csv"):
            if path in seen:
                continue
            seen.add(path)
            match = CSV_FILE_RE.match(path.name)
            if not match:
                continue
            event_ticker = match.group("event").upper()
            if not event_ticker.startswith(f"{series}-"):
                continue
            if len(seen) == 1 or len(seen) % 100 == 0:
                print(f"  scanned CSV files: {len(seen)}", flush=True)
            df = pd.read_csv(path)
            if "timestamp" not in df.columns:
                continue
            df["ts_end"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            strike_cols = [col for col in df.columns if csv_strike_from_column(col) is not None]
            if not strike_cols:
                continue
            melted = df[["ts_end", *strike_cols]].melt("ts_end", var_name="strike_label", value_name="yes_mean_cents")
            melted = melted.dropna(subset=["ts_end", "yes_mean_cents"])
            if melted.empty:
                continue
            melted["yes_mean_cents"] = pd.to_numeric(melted["yes_mean_cents"], errors="coerce")
            melted = melted.dropna(subset=["yes_mean_cents"])
            melted = melted[melted["yes_mean_cents"].between(0, 100)]
            melted["event_ticker"] = event_ticker
            melted["floor_strike"] = [csv_strike_from_column(v) for v in melted["strike_label"]]
            melted["market_ticker"] = [
                market_ticker_from_strike(event_ticker, strike)
                for strike in melted["floor_strike"]
            ]
            melted["source_path"] = str(path)
            rows.append(melted[["event_ticker", "market_ticker", "ts_end", "floor_strike", "yes_mean_cents", "source_path"]])
    if not rows:
        return empty_csv_prices()
    return pd.concat(rows, ignore_index=True).drop_duplicates(["market_ticker", "ts_end", "source_path"])


def empty_csv_prices() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["event_ticker", "market_ticker", "ts_end", "floor_strike", "yes_mean_cents", "source_path"]
    )


def empty_quotes() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "market_ticker",
            "event_ticker",
            "ts_end",
            "available_at",
            "yes_bid_open",
            "yes_bid_high",
            "yes_bid_low",
            "yes_bid_close",
            "yes_ask_open",
            "yes_ask_high",
            "yes_ask_low",
            "yes_ask_close",
            "yes_ask_exe",
            "no_ask_exe",
            "spread_cents",
            "price_mean",
            "volume",
            "open_interest",
            "source",
            "fidelity",
        ]
    )


def read_bidask_parquet_parts(path: Path | None, series: str) -> pd.DataFrame:
    if path is None or not path.exists():
        return empty_quotes()
    df = pd.read_parquet(path)
    if df.empty:
        return empty_quotes()
    if "event_ticker" not in df.columns:
        df["event_ticker"] = df["market_ticker"].str.extract(rf"^({series}-\d{{2}}[A-Z]{{3}}\d{{4}})")[0]
    if "available_at" not in df.columns:
        df["available_at"] = df["ts_end"]
    for col in ("ts_end", "available_at"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    df["yes_ask_exe"] = pd.to_numeric(df["yes_ask_exe"], errors="coerce")
    df["no_ask_exe"] = pd.to_numeric(df["no_ask_exe"], errors="coerce")
    df["yes_bid_close"] = pd.to_numeric(df["yes_bid_close"], errors="coerce")
    df["yes_ask_close"] = pd.to_numeric(df["yes_ask_close"], errors="coerce")
    df["spread_cents"] = pd.to_numeric(df["spread_cents"], errors="coerce")
    df["source"] = df.get("source", "event_candlestick_api")
    df["fidelity"] = df.get("fidelity", "historical_candle_bidask")
    keep = empty_quotes().columns.tolist()
    for col in keep:
        if col not in df.columns:
            df[col] = None
    df = df[keep].dropna(subset=["market_ticker", "event_ticker", "ts_end", "yes_bid_close", "yes_ask_close"])
    df = df[
        df["yes_bid_close"].between(0.0, 1.0)
        & df["yes_ask_close"].between(0.0, 1.0)
        & df["yes_ask_exe"].between(0.0, 1.0)
        & df["no_ask_exe"].between(0.0, 1.0)
        & df["spread_cents"].ge(0.0)
    ]
    return df.drop_duplicates(["market_ticker", "ts_end"]).sort_values(["available_at", "market_ticker"]).reset_index(drop=True)


def combine_quote_sources(sqlite_quotes: pd.DataFrame, event_quotes: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if not sqlite_quotes.empty:
        q = sqlite_quotes.copy()
        q["_source_rank"] = 1
        frames.append(q)
    if not event_quotes.empty:
        q = event_quotes.copy()
        q["_source_rank"] = 0
        frames.append(q)
    if not frames:
        return empty_quotes()
    out = (
        pd.concat(frames, ignore_index=True)
        .sort_values(["market_ticker", "ts_end", "_source_rank"])
        .drop_duplicates(["market_ticker", "ts_end"], keep="first")
        .drop(columns=["_source_rank"])
        .sort_values(["available_at", "market_ticker"])
        .reset_index(drop=True)
    )
    return out


def infer_markets_from_csv_prices(csv_prices: pd.DataFrame, series: str) -> pd.DataFrame:
    if csv_prices.empty:
        return pd.DataFrame()
    markets = (
        csv_prices[["event_ticker", "market_ticker", "floor_strike"]]
        .drop_duplicates("market_ticker")
        .copy()
    )
    markets["series_ticker"] = series
    markets["open_time"] = markets["event_ticker"].map(event_close_from_ticker) - pd.Timedelta(hours=1)
    markets["close_time"] = markets["event_ticker"].map(event_close_from_ticker)
    markets["event_open_time"] = markets["open_time"]
    markets["cap_strike"] = None
    markets["status"] = "csv_raw_reference"
    markets["is_cumulative"] = True
    markets["is_hourly_kxbtcd"] = True
    return markets[
        [
            "market_ticker",
            "series_ticker",
            "event_ticker",
            "open_time",
            "close_time",
            "floor_strike",
            "cap_strike",
            "status",
            "event_open_time",
            "is_cumulative",
            "is_hourly_kxbtcd",
        ]
    ]


def infer_markets_from_quotes(quotes: pd.DataFrame, series: str) -> pd.DataFrame:
    if quotes.empty:
        return empty_markets()
    markets = quotes[["event_ticker", "market_ticker"]].drop_duplicates("market_ticker").copy()
    markets["floor_strike"] = markets["market_ticker"].map(strike_from_ticker)
    markets = markets[markets["floor_strike"].notna()].copy()
    if markets.empty:
        return empty_markets()
    markets["series_ticker"] = series
    markets["close_time"] = markets["event_ticker"].map(event_close_from_ticker)
    markets["open_time"] = markets["close_time"] - pd.Timedelta(hours=1)
    markets["event_open_time"] = markets["open_time"]
    markets["cap_strike"] = None
    markets["status"] = "bidask_parquet_reference"
    markets["is_cumulative"] = markets["market_ticker"].str.contains("-T", regex=False)
    markets["is_hourly_kxbtcd"] = markets["close_time"].notna()
    return markets[
        [
            "market_ticker",
            "series_ticker",
            "event_ticker",
            "open_time",
            "close_time",
            "floor_strike",
            "cap_strike",
            "status",
            "event_open_time",
            "is_cumulative",
            "is_hourly_kxbtcd",
        ]
    ]


def filter_csv_prices_by_range(
    csv_prices: pd.DataFrame,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DataFrame:
    if csv_prices.empty or (start is None and end is None):
        return csv_prices
    out = csv_prices.copy()
    event_close = out["event_ticker"].map(event_close_from_ticker)
    event_open = event_close - pd.Timedelta(hours=1)
    mask = pd.Series(True, index=out.index)
    if start is not None:
        mask &= event_close >= start
    if end is not None:
        mask &= event_open <= end
    return out[mask].reset_index(drop=True)


def load_or_fetch_btc(
    markets: pd.DataFrame,
    cache_path: Path,
    skip_fetch: bool,
    fill_internal_gaps: bool,
) -> pd.DataFrame:
    if markets.empty:
        raise RuntimeError("Cannot build BTC table without market coverage.")
    start = markets["event_open_time"].min() - pd.Timedelta(days=9)
    end = markets["close_time"].max() + pd.Timedelta(minutes=1)
    existing = pd.DataFrame()
    if cache_path.exists():
        existing = pd.read_parquet(cache_path)
        existing["time"] = pd.to_datetime(existing["time"], utc=True)
    if skip_fetch:
        btc = existing[(existing["time"] >= start) & (existing["time"] <= end)].copy()
    else:
        from scripts.backtest_1hr_collected_data import load_or_fetch_btc_minutes

        btc = load_or_fetch_btc_minutes(
            cache_path,
            start,
            end,
            refresh=False,
            fill_internal_gaps=fill_internal_gaps,
        )
    if btc.empty:
        raise RuntimeError(f"No BTC data available for {start} -> {end}.")
    btc = btc.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    btc["bucket_start"] = btc["time"]
    btc["available_at"] = btc["bucket_start"] + pd.Timedelta(minutes=1)
    btc["log_ret"] = np.log(btc["close"] / btc["close"].shift(1))
    af = 60 * 24 * 365
    btc["rv_15m"] = btc["log_ret"].rolling(15).std() * np.sqrt(af)
    log_ho = np.log(btc["high"] / btc["open"])
    log_lo = np.log(btc["low"] / btc["open"])
    log_co = np.log(btc["close"] / btc["open"])
    log_oc = np.log(btc["open"] / btc["close"].shift(1))
    close_vol = log_oc.rolling(60).var()
    open_vol = log_co.rolling(60).var()
    rs_vol = (log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)).rolling(60).mean()
    k = 0.34 / (1.34 + 61 / 59)
    btc["rv_60m"] = np.sqrt((close_vol + k * open_vol + (1 - k) * rs_vol) * af)
    btc["rv_1d"] = btc["log_ret"].rolling(1440).std() * np.sqrt(af)
    btc["rkurt_60m"] = btc["log_ret"].rolling(60).kurt()
    keep = [
        "bucket_start",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "log_ret",
        "rv_15m",
        "rv_60m",
        "rv_1d",
        "rkurt_60m",
    ]
    return btc[keep]


def write_duckdb(out_dir: Path, tables: dict[str, pd.DataFrame]) -> Path:
    db_path = out_dir / "research.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for name, df in tables.items():
        con.register(f"{name}_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM {name}_df")
        con.unregister(f"{name}_df")
    con.execute("CREATE INDEX IF NOT EXISTS idx_quotes_time ON kalshi_quotes(available_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_quotes_market ON kalshi_quotes(market_ticker)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_btc_available ON btc_1m(available_at)")
    con.execute(
        """
        CREATE VIEW v_research_universe AS
        SELECT q.*, m.open_time, m.close_time, m.event_open_time, m.floor_strike, m.status
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE m.is_hourly_kxbtcd AND m.is_cumulative
        """
    )
    con.execute(
        """
        CREATE VIEW v_btc_available AS
        SELECT available_at AS time, open, high, low, close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        """
    )
    con.close()
    return db_path


def write_outputs(out_dir: Path, tables: dict[str, pd.DataFrame], report: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(out_dir / f"{name}.parquet", index=False)
    write_duckdb(out_dir, tables)
    (out_dir / "datamart_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def minute_gap_count(ts: pd.Series) -> int:
    if ts.empty:
        return 0
    diffs = pd.to_datetime(ts, utc=True).sort_values().diff()
    return int((diffs > pd.Timedelta(minutes=1)).sum())


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    btc_cache = args.btc_cache or (args.out_dir / "btc_1m_cache.parquet")
    bidask_dir = args.bidask_dir
    if bidask_dir is None:
        candidate_bidask_dir = args.out_dir / "kalshi_bidask_events"
        bidask_dir = candidate_bidask_dir if candidate_bidask_dir.exists() else None
    start = parse_time_bound(args.start, is_end=False)
    end = parse_time_bound(args.end, is_end=True)
    print(f"reading markets from {args.source_sqlite}", flush=True)
    markets = read_sqlite_markets(args.source_sqlite, args.series)
    if start is not None:
        markets = markets[markets["close_time"] >= start]
    if end is not None:
        markets = markets[markets["event_open_time"] <= end]
    print(f"loaded markets: {len(markets):,}", flush=True)
    print("reading observed Kalshi bid/ask candles", flush=True)
    sqlite_quotes = read_sqlite_quotes(args.source_sqlite, markets, args.series)
    event_quotes = read_bidask_parquet_parts(bidask_dir, args.series)
    if start is not None and not event_quotes.empty:
        event_quotes = event_quotes[event_quotes["available_at"] >= start]
    if end is not None and not event_quotes.empty:
        event_quotes = event_quotes[event_quotes["available_at"] <= end]
    quotes = combine_quote_sources(sqlite_quotes, event_quotes)
    if start is not None and not quotes.empty:
        quotes = quotes[quotes["available_at"] >= start]
    if end is not None and not quotes.empty:
        quotes = quotes[quotes["available_at"] <= end]
    if quotes.empty:
        quotes = empty_quotes()
    print(f"loaded quote rows: {len(quotes):,}", flush=True)
    print("copying raw Kalshi CSV exports", flush=True)
    csv_prices = empty_csv_prices() if args.skip_csv_raw else read_csv_prices(args.csv_dir, args.series)
    csv_prices = filter_csv_prices_by_range(csv_prices, start, end)
    print(f"loaded raw CSV price rows: {len(csv_prices):,}", flush=True)
    csv_markets = infer_markets_from_csv_prices(csv_prices, args.series)
    quote_markets = infer_markets_from_quotes(quotes, args.series)
    market_sources = []
    if not markets.empty:
        q = markets.copy()
        q["_source_rank"] = 0
        market_sources.append(q)
    if not quote_markets.empty:
        q = quote_markets.copy()
        q["_source_rank"] = 1
        market_sources.append(q)
    if not csv_markets.empty:
        q = csv_markets.copy()
        q["_source_rank"] = 2
        market_sources.append(q)
    if market_sources:
        markets = (
            pd.concat(market_sources, ignore_index=True)
            .sort_values(["market_ticker", "_source_rank"])
            .drop_duplicates("market_ticker", keep="first")
            .drop(columns=["_source_rank"])
            .reset_index(drop=True)
        )
    if markets.empty:
        raise SystemExit("No matching markets found.")
    covered_events: set[str] = set()
    if not quotes.empty:
        covered_events.update(str(v) for v in quotes["event_ticker"].dropna().unique())
    if not csv_prices.empty:
        covered_events.update(str(v) for v in csv_prices["event_ticker"].dropna().unique())
    if covered_events:
        markets = markets[markets["event_ticker"].isin(covered_events)].copy()
        quotes = quotes[quotes["event_ticker"].isin(covered_events)].copy()
    print(f"building BTC table with cache {btc_cache}", flush=True)
    btc = load_or_fetch_btc(markets, btc_cache, args.skip_btc_fetch, args.fill_internal_btc_gaps)
    live_spot_snapshots = pd.DataFrame(columns=["observed_at", "spot", "source"])
    tables = {
        "kalshi_markets": markets,
        "kalshi_quotes": quotes,
        "kalshi_csv_prices": csv_prices,
        "btc_1m": btc,
        "live_spot_snapshots": live_spot_snapshots,
    }
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_sqlite": str(args.source_sqlite),
        "out_dir": str(args.out_dir),
        "btc_cache": str(btc_cache),
        "bidask_dir": str(bidask_dir) if bidask_dir else None,
        "markets": len(markets),
        "quote_rows": len(quotes),
        "quote_events": int(quotes["event_ticker"].nunique()) if not quotes.empty else 0,
        "sqlite_quote_rows": len(sqlite_quotes),
        "event_bidask_quote_rows": len(event_quotes),
        "csv_price_rows": len(csv_prices),
        "csv_events": int(csv_prices["event_ticker"].nunique()) if not csv_prices.empty else 0,
        "btc_rows": len(btc),
        "btc_start_available_at": str(btc["available_at"].min()),
        "btc_end_available_at": str(btc["available_at"].max()),
        "btc_internal_gap_count": minute_gap_count(btc["bucket_start"]),
        "notes": [
            "kalshi_quotes contains observed bid/ask candles only; no forward fill.",
            "btc_1m.available_at is Coinbase bucket_start + 1 minute.",
            "kalshi_csv_prices is raw/reference data and is not execution-faithful.",
        ],
    }
    write_outputs(args.out_dir, tables, report)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
