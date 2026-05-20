#!/usr/bin/env python3
"""Build a small multi-crypto Kalshi bid/ask research datamart.

This intentionally mirrors the BTC research datamart conventions:

* Kalshi rows are observed bid/ask candles only, never forward filled.
* YES entry is the observed YES ask.
* NO entry is implied from the observed YES bid as ``1 - yes_bid``.
* Coinbase candles are causal: a minute bar is available at bucket_start + 1m.
* Kalshi's historical cutoff is respected. Events settled before the cutoff
  are routed through historical market endpoints; recent events use event
  candlesticks.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE_URL = "https://api.exchange.coinbase.com"
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "crypto_research_datamart"
KALSHI_REQUEST_INTERVAL_SEC = 6.0
KALSHI_REQUEST_LOCK = threading.Lock()
KALSHI_LAST_REQUEST_AT = 0.0


SERIES_CONFIG: dict[str, dict[str, str]] = {
    # Hourly above/below markets.
    "KXBTCD": {"asset": "BTC", "product_id": "BTC-USD", "family": "above_below", "frequency": "hourly"},
    "KXETHD": {"asset": "ETH", "product_id": "ETH-USD", "family": "above_below", "frequency": "hourly"},
    "KXSOLD": {"asset": "SOL", "product_id": "SOL-USD", "family": "above_below", "frequency": "hourly"},
    "KXDOGED": {"asset": "DOGE", "product_id": "DOGE-USD", "family": "above_below", "frequency": "hourly"},
    "KXXRPD": {"asset": "XRP", "product_id": "XRP-USD", "family": "above_below", "frequency": "hourly"},
    "KXBNBD": {"asset": "BNB", "product_id": "BNB-USD", "family": "above_below", "frequency": "hourly"},
    "KXHYPED": {"asset": "HYPE", "product_id": "HYPE-USD", "family": "above_below", "frequency": "hourly"},
    # Hourly range markets.
    "KXBTC": {"asset": "BTC", "product_id": "BTC-USD", "family": "range", "frequency": "hourly"},
    "KXETH": {"asset": "ETH", "product_id": "ETH-USD", "family": "range", "frequency": "hourly"},
    "KXSOLE": {"asset": "SOL", "product_id": "SOL-USD", "family": "range", "frequency": "hourly"},
    "KXDOGE": {"asset": "DOGE", "product_id": "DOGE-USD", "family": "range", "frequency": "hourly"},
    "KXXRP": {"asset": "XRP", "product_id": "XRP-USD", "family": "range", "frequency": "hourly"},
    "KXBNB": {"asset": "BNB", "product_id": "BNB-USD", "family": "range", "frequency": "hourly"},
    "KXHYPE": {"asset": "HYPE", "product_id": "HYPE-USD", "family": "range", "frequency": "hourly"},
    # 15-minute up/down markets.
    "KXBTC15M": {"asset": "BTC", "product_id": "BTC-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXETH15M": {"asset": "ETH", "product_id": "ETH-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXSOL15M": {"asset": "SOL", "product_id": "SOL-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXDOGE15M": {"asset": "DOGE", "product_id": "DOGE-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXXRP15M": {"asset": "XRP", "product_id": "XRP-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXBNB15M": {"asset": "BNB", "product_id": "BNB-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXHYPE15M": {"asset": "HYPE", "product_id": "HYPE-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXADA15M": {"asset": "ADA", "product_id": "ADA-USD", "family": "updown15", "frequency": "fifteen_min"},
    "KXBCH15M": {"asset": "BCH", "product_id": "BCH-USD", "family": "updown15", "frequency": "fifteen_min"},
}

DEFAULT_SERIES = [
    "KXETHD",
    "KXSOLD",
    "KXDOGED",
    "KXXRPD",
    "KXETH",
    "KXSOLE",
    "KXDOGE",
    "KXXRP",
    "KXBTC15M",
    "KXETH15M",
    "KXSOL15M",
    "KXDOGE15M",
    "KXXRP15M",
]

EVENT_RE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hm>\d{2,4})")
MONEY_RE = re.compile(r"\$([0-9,]+(?:\.\d+)?)")
NY_TZ = "America/New_York"


def event_close_from_ticker(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker or "").upper())
    if not match:
        return None
    month_map = {
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
    month = month_map.get(match.group("mon"))
    if month is None:
        return None
    hm = match.group("hm")
    hour = int(hm[:2])
    minute = int(hm[2:]) if len(hm) == 4 else 0
    local = pd.Timestamp(
        year=2000 + int(match.group("yy")),
        month=month,
        day=int(match.group("day")),
        hour=hour,
        minute=minute,
        tz=NY_TZ,
    )
    return local.tz_convert("UTC")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact multi-crypto Kalshi/Coinbase DuckDB datamart.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--series", nargs="+", default=DEFAULT_SERIES, help="Kalshi series tickers to pull.")
    parser.add_argument("--max-hourly-events", type=int, default=6)
    parser.add_argument("--max-15m-events", type=int, default=16)
    parser.add_argument("--start", help="Optional inclusive UTC event close lower bound.")
    parser.add_argument("--end", help="Optional exclusive UTC event close upper bound.")
    parser.add_argument("--chunk-minutes", type=int, default=15)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    parser.add_argument("--spot-lookback-days", type=int, default=10)
    parser.add_argument(
        "--kalshi-request-interval-sec",
        type=float,
        default=KALSHI_REQUEST_INTERVAL_SEC,
        help="Minimum spacing between Kalshi REST calls across worker threads.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def utc_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def request_json(
    session: requests.Session,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    base_url: str = BASE_URL,
    retries: int = 6,
    timeout: int = 30,
) -> dict:
    global KALSHI_LAST_REQUEST_AT
    last_error: Exception | None = None
    throttle = base_url == BASE_URL
    for attempt in range(retries):
        try:
            if throttle:
                with KALSHI_REQUEST_LOCK:
                    now = time.monotonic()
                    wait_s = KALSHI_REQUEST_INTERVAL_SEC - (now - KALSHI_LAST_REQUEST_AT)
                    if wait_s > 0:
                        time.sleep(wait_s)
                    KALSHI_LAST_REQUEST_AT = time.monotonic()
            response = session.get(base_url + path, params=params, timeout=timeout)
            if response.status_code == 429:
                wait_s = min(30.0, 2.0 * (attempt + 1))
                print(f"  rate limited on {path}; sleeping {wait_s:.1f}s", flush=True)
                time.sleep(wait_s)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"GET {path} HTTP {response.status_code}: {response.text[:300]}")
            return response.json()
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(15.0, 1.5 * (attempt + 1)))
                continue
            break
    raise RuntimeError(f"GET {path} failed: {last_error}")


def cutoff_timestamp(session: requests.Session) -> pd.Timestamp:
    data = request_json(session, "/historical/cutoff")
    return pd.Timestamp(data["market_settled_ts"]).tz_convert("UTC")


def list_settled_events(
    session: requests.Session,
    series: str,
    limit: int,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> list[dict]:
    events: list[dict] = []
    cursor = None
    while len(events) < limit:
        params: dict[str, Any] = {"series_ticker": series, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = request_json(session, "/events", params=params)
        batch = data.get("events") or []
        if not batch:
            break
        for event in batch:
            event_ticker = event.get("event_ticker")
            if not event_ticker:
                continue
            close_guess = event_close_from_ticker(str(event_ticker))
            if close_guess is not None:
                if end is not None and close_guess >= end:
                    continue
                if start is not None and close_guess < start:
                    return events
            event["series_ticker"] = series
            # Close time is reliably on nested markets, but the ticker is useful
            # for rough filtering before fetching markets.
            events.append(event)
            if len(events) >= limit:
                break
        cursor = data.get("cursor")
        if not cursor:
            break
    return events


def fetch_markets_for_event(session: requests.Session, event_ticker: str, use_historical: bool) -> list[dict]:
    path = "/historical/markets" if use_historical else "/markets"
    markets: list[dict] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"event_ticker": event_ticker, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = request_json(session, path, params=params)
        markets.extend(data.get("markets") or [])
        cursor = data.get("cursor")
        if not cursor:
            break
    return markets


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def money_values(text: str | None) -> list[float]:
    if not text:
        return []
    values = []
    for match in MONEY_RE.finditer(str(text)):
        value = as_float(match.group(1))
        if value is not None:
            values.append(value)
    return values


def custom_strike_value(market: dict, key: str) -> float | None:
    custom = market.get("custom_strike")
    if isinstance(custom, dict):
        return as_float(custom.get(key))
    return None


def normalize_greater_strike(value: float | None, label_value: float | None = None) -> float | None:
    if label_value is not None:
        return label_value
    if value is None:
        return None
    # Kalshi tickers often encode "X or above" as X - one tick.
    magnitude = max(1.0, abs(value))
    if magnitude >= 100:
        decimals = 2
    elif magnitude >= 1:
        decimals = 4
    else:
        decimals = 7
    step = 10 ** (-decimals)
    rounded_up = round(value + step, decimals)
    if abs(rounded_up - value) <= 10 * step:
        return rounded_up
    return value


def infer_market_shape(market: dict, family: str) -> tuple[str, float | None, float | None]:
    yes_label = market.get("yes_sub_title") or market.get("subtitle") or market.get("title") or ""
    label_values = money_values(yes_label)
    custom = market.get("custom_strike") if isinstance(market.get("custom_strike"), dict) else {}
    strike_type = str(custom.get("strike_type") or "").lower() if isinstance(custom, dict) else ""
    ticker = str(market.get("ticker") or "")
    if family == "updown15":
        floor = label_values[0] if label_values else as_float(market.get("floor_strike"))
        return "updown", floor, None
    if family == "range" or "-B" in ticker or strike_type == "between":
        floor = label_values[0] if label_values else as_float(market.get("floor_strike"))
        cap = label_values[1] if len(label_values) > 1 else as_float(market.get("cap_strike"))
        if floor is None:
            floor = custom_strike_value(market, "floor_strike")
        if cap is None:
            cap = custom_strike_value(market, "cap_strike")
        return "range", floor, cap
    label_floor = label_values[0] if label_values else None
    floor = as_float(market.get("floor_strike"))
    if floor is None:
        floor = custom_strike_value(market, "floor_strike")
    return "above", normalize_greater_strike(floor, label_floor), None


def price_component(candle: dict, side: str, field: str) -> float | None:
    data = candle.get(side) or {}
    for key in (f"{field}_dollars", field):
        value = as_float(data.get(key))
        if value is not None:
            return value
    return None


def price_mean(candle: dict) -> float | None:
    data = candle.get("price") or {}
    for key in ("mean_dollars", "mean", "close_dollars", "close"):
        value = as_float(data.get(key))
        if value is not None:
            return value
    return None


def candle_rows(event_ticker: str, market_ticker: str, candles: list[dict], source: str) -> list[dict]:
    rows = []
    for candle in candles:
        raw_ts = candle.get("end_period_ts")
        if raw_ts is None:
            continue
        yes_bid = price_component(candle, "yes_bid", "close")
        yes_ask = price_component(candle, "yes_ask", "close")
        if yes_bid is None or yes_ask is None:
            continue
        if not (0.0 <= yes_bid <= yes_ask <= 1.0):
            continue
        ts = pd.Timestamp(int(raw_ts), unit="s", tz="UTC")
        rows.append(
            {
                "market_ticker": market_ticker,
                "event_ticker": event_ticker,
                "ts_end": ts,
                "available_at": ts,
                "yes_bid_close": yes_bid,
                "yes_ask_close": yes_ask,
                "yes_ask_exe": yes_ask,
                "no_ask_exe": 1.0 - yes_bid,
                "spread_cents": (yes_ask - yes_bid) * 100.0,
                "price_mean": price_mean(candle),
                "volume": as_float(candle.get("volume") or candle.get("volume_fp")),
                "open_interest": as_float(candle.get("open_interest") or candle.get("open_interest_fp")),
                "source": source,
                "fidelity": "historical_candle_bidask",
            }
        )
    return rows


def fetch_event_candles_chunked(
    session: requests.Session,
    series: str,
    event_ticker: str,
    start_ts: int,
    end_ts: int,
    chunk_minutes: int,
) -> tuple[dict[str, list[dict]], int]:
    by_ticker: dict[str, dict[int, dict]] = {}
    truncated = 0
    cursor = start_ts
    step = max(60, chunk_minutes * 60)
    while cursor < end_ts:
        target_end = min(end_ts, cursor + step)
        inner = cursor
        while inner < target_end:
            data = request_json(
                session,
                f"/series/{series}/events/{event_ticker}/candlesticks",
                params={"period_interval": 1, "start_ts": inner, "end_ts": target_end},
            )
            tickers = data.get("market_tickers") or []
            candles = data.get("market_candlesticks") or []
            if len(tickers) != len(candles):
                raise RuntimeError(f"{event_ticker}: mismatched ticker/candle response")
            for ticker, ticker_candles in zip(tickers, candles):
                lookup = by_ticker.setdefault(ticker, {})
                for candle in ticker_candles:
                    ts = candle.get("end_period_ts")
                    if ts is not None:
                        lookup[int(ts)] = candle
            adjusted_end = int(data.get("adjusted_end_ts") or target_end)
            if adjusted_end < target_end:
                truncated += 1
            if adjusted_end >= target_end:
                break
            if adjusted_end <= inner:
                raise RuntimeError(f"{event_ticker}: adjusted_end_ts did not advance")
            inner = adjusted_end
        cursor = target_end
    return {ticker: [items[ts] for ts in sorted(items)] for ticker, items in by_ticker.items()}, truncated


def fetch_historical_market_candles(
    session: requests.Session,
    market_ticker: str,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    data = request_json(
        session,
        f"/historical/markets/{market_ticker}/candlesticks",
        params={"period_interval": 1, "start_ts": start_ts, "end_ts": end_ts},
    )
    return data.get("candlesticks") or []


def event_payload(
    event: dict,
    cfg: dict[str, str],
    markets: list[dict],
    use_historical: bool,
) -> tuple[dict, pd.DataFrame]:
    market_rows = []
    open_times = []
    close_times = []
    for market in markets:
        open_time = pd.to_datetime(market.get("open_time"), utc=True, errors="coerce")
        close_time = pd.to_datetime(market.get("close_time"), utc=True, errors="coerce")
        if pd.notna(open_time):
            open_times.append(open_time)
        if pd.notna(close_time):
            close_times.append(close_time)
        market_kind, floor, cap = infer_market_shape(market, cfg["family"])
        market_rows.append(
            {
                "market_ticker": market.get("ticker"),
                "event_ticker": event.get("event_ticker"),
                "series_ticker": cfg.get("series_ticker"),
                "asset": cfg["asset"],
                "product_id": cfg["product_id"],
                "family": cfg["family"],
                "frequency": cfg["frequency"],
                "market_kind": market_kind,
                "open_time": open_time,
                "close_time": close_time,
                "floor_strike": floor,
                "cap_strike": cap,
                "status": market.get("status"),
                "result": str(market.get("result") or "").lower() or None,
                "title": market.get("title"),
                "yes_sub_title": market.get("yes_sub_title"),
                "no_sub_title": market.get("no_sub_title"),
                "api_tier": "historical" if use_historical else "live",
            }
        )
    event_row = {
        "event_ticker": event.get("event_ticker"),
        "series_ticker": cfg.get("series_ticker"),
        "asset": cfg["asset"],
        "product_id": cfg["product_id"],
        "family": cfg["family"],
        "frequency": cfg["frequency"],
        "title": event.get("title"),
        "sub_title": event.get("sub_title"),
        "open_time": min(open_times) if open_times else pd.NaT,
        "close_time": max(close_times) if close_times else pd.NaT,
        "api_tier": "historical" if use_historical else "live",
    }
    return event_row, pd.DataFrame(market_rows)


def fetch_event_bundle(
    event: dict,
    cfg: dict[str, str],
    cutoff: pd.Timestamp,
    out_parts_dir: Path,
    chunk_minutes: int,
    overwrite: bool,
    skip_existing: bool,
) -> tuple[str, dict | None, pd.DataFrame, pd.DataFrame, str]:
    event_ticker = str(event["event_ticker"])
    part_path = out_parts_dir / f"{event_ticker.lower()}.parquet"
    meta_path = out_parts_dir / f"{event_ticker.lower()}.markets.parquet"
    event_path = out_parts_dir / f"{event_ticker.lower()}.event.json"
    if skip_existing and not overwrite and part_path.exists() and meta_path.exists() and event_path.exists():
        quote_df = pd.read_parquet(part_path)
        market_df = pd.read_parquet(meta_path)
        event_row = json.loads(event_path.read_text(encoding="utf-8"))
        for key in ("open_time", "close_time"):
            if event_row.get(key):
                event_row[key] = pd.Timestamp(event_row[key]).tz_convert("UTC")
        return event_ticker, event_row, market_df, quote_df, "cached"

    with requests.Session() as session:
        # Fetch markets from the live endpoint first to get close_time. If the
        # event is older than the cutoff, route to historical endpoints.
        markets_live = fetch_markets_for_event(session, event_ticker, use_historical=False)
        close_candidates = pd.to_datetime(
            [m.get("close_time") for m in markets_live if m.get("close_time")],
            utc=True,
            errors="coerce",
        )
        close_time = close_candidates.max() if len(close_candidates) else pd.NaT
        use_historical = bool(pd.notna(close_time) and close_time < cutoff)
        if not markets_live:
            use_historical = True
        markets = fetch_markets_for_event(session, event_ticker, use_historical=use_historical) if use_historical else markets_live
        if not markets:
            return event_ticker, None, pd.DataFrame(), pd.DataFrame(), "no markets"
        cfg_with_series = dict(cfg)
        cfg_with_series["series_ticker"] = cfg["series_ticker"]
        event_row, market_df = event_payload(event, cfg_with_series, markets, use_historical)
        if pd.isna(event_row["open_time"]) or pd.isna(event_row["close_time"]):
            return event_ticker, event_row, market_df, pd.DataFrame(), "missing event times"
        start_ts = int(pd.Timestamp(event_row["open_time"]).timestamp())
        end_ts = int(pd.Timestamp(event_row["close_time"]).timestamp())
        quote_rows: list[dict] = []
        if use_historical:
            for market_ticker in market_df["market_ticker"].dropna().astype(str):
                candles = fetch_historical_market_candles(session, market_ticker, start_ts, end_ts)
                quote_rows.extend(candle_rows(event_ticker, market_ticker, candles, "historical_market_candlestick_api"))
        else:
            candles_by_ticker, truncated = fetch_event_candles_chunked(
                session,
                cfg["series_ticker"],
                event_ticker,
                start_ts,
                end_ts,
                chunk_minutes,
            )
            for market_ticker, candles in candles_by_ticker.items():
                quote_rows.extend(candle_rows(event_ticker, market_ticker, candles, "event_candlestick_api"))
            event_row["truncated_chunks"] = truncated

    quote_df = pd.DataFrame(quote_rows)
    out_parts_dir.mkdir(parents=True, exist_ok=True)
    if not quote_df.empty:
        quote_df.to_parquet(part_path, index=False)
    market_df.to_parquet(meta_path, index=False)
    serializable_event = {
        key: (value.isoformat() if isinstance(value, pd.Timestamp) else value)
        for key, value in event_row.items()
    }
    event_path.write_text(json.dumps(serializable_event, indent=2, default=str), encoding="utf-8")
    return event_ticker, event_row, market_df, quote_df, f"quotes={len(quote_df):,} markets={len(market_df):,}"


def fetch_coinbase_minutes(product_id: str, start: pd.Timestamp, end: pd.Timestamp, cache_path: Path) -> pd.DataFrame:
    cache = pd.DataFrame()
    if cache_path.exists():
        cache = pd.read_parquet(cache_path)
        cache["bucket_start"] = pd.to_datetime(cache["bucket_start"], utc=True)
    need_start = start.floor("min")
    need_end = end.ceil("min")
    if not cache.empty and cache["bucket_start"].min() <= need_start and cache["bucket_start"].max() >= need_end - pd.Timedelta(minutes=1):
        out = cache[(cache["bucket_start"] >= need_start) & (cache["bucket_start"] <= need_end)].copy()
        return add_spot_features(out, product_id)

    rows = []
    cursor = need_start
    with requests.Session() as session:
        while cursor < need_end:
            chunk_end = min(need_end, cursor + pd.Timedelta(minutes=300))
            params = {
                "granularity": 60,
                "start": cursor.isoformat(),
                "end": chunk_end.isoformat(),
            }
            data = request_json(session, f"/products/{product_id}/candles", params=params, base_url=COINBASE_URL, retries=5)
            for item in data:
                if not isinstance(item, list) or len(item) < 6:
                    continue
                rows.append(
                    {
                        "bucket_start": pd.Timestamp(int(item[0]), unit="s", tz="UTC"),
                        "low": float(item[1]),
                        "high": float(item[2]),
                        "open": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    }
                )
            cursor = chunk_end
            time.sleep(0.05)
    fetched = pd.DataFrame(rows)
    if fetched.empty:
        return fetched
    if not cache.empty:
        fetched = pd.concat([cache, fetched], ignore_index=True)
    fetched = fetched.drop_duplicates("bucket_start").sort_values("bucket_start").reset_index(drop=True)
    fetched.to_parquet(cache_path, index=False)
    out = fetched[(fetched["bucket_start"] >= need_start) & (fetched["bucket_start"] <= need_end)].copy()
    return add_spot_features(out, product_id)


def add_spot_features(df: pd.DataFrame, product_id: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy().sort_values("bucket_start").reset_index(drop=True)
    out["product_id"] = product_id
    out["asset"] = product_id.split("-")[0]
    out["available_at"] = out["bucket_start"] + pd.Timedelta(minutes=1)
    out["log_ret"] = np.log(out["close"] / out["close"].shift(1))
    af = 60 * 24 * 365
    out["rv_15m"] = out["log_ret"].rolling(15).std() * np.sqrt(af)
    out["rv_60m"] = out["log_ret"].rolling(60).std() * np.sqrt(af)
    out["rv_1d"] = out["log_ret"].rolling(1440).std() * np.sqrt(af)
    out["rkurt_60m"] = out["log_ret"].rolling(60).kurt()
    keep = [
        "product_id",
        "asset",
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
    return out[keep]


def write_duckdb(out_dir: Path, tables: dict[str, pd.DataFrame]) -> Path:
    db_path = out_dir / "crypto_research.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for name, df in tables.items():
        con.register(f"{name}_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM {name}_df")
        con.unregister(f"{name}_df")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crypto_quotes_time ON kalshi_quotes(available_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crypto_quotes_market ON kalshi_quotes(market_ticker)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crypto_spot_asset_time ON spot_1m(asset, available_at)")
    con.close()
    return db_path


def main() -> int:
    global KALSHI_REQUEST_INTERVAL_SEC
    args = parse_args()
    KALSHI_REQUEST_INTERVAL_SEC = max(0.0, float(args.kalshi_request_interval_sec))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    part_dir = args.out_dir / "kalshi_event_parts"
    cache_dir = args.out_dir / "spot_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    requested_series = [series.upper() for series in args.series]
    unknown = [series for series in requested_series if series not in SERIES_CONFIG]
    if unknown:
        raise SystemExit(f"Unknown series: {unknown}")
    start = utc_ts(args.start)
    end = utc_ts(args.end)
    with requests.Session() as session:
        cutoff = cutoff_timestamp(session)
        print(f"Kalshi historical cutoff market_settled_ts={cutoff.isoformat()}", flush=True)
        selected_events: list[dict] = []
        for series in requested_series:
            cfg = SERIES_CONFIG[series]
            event_limit = args.max_15m_events if cfg["frequency"] == "fifteen_min" else args.max_hourly_events
            print(f"listing {series} limit={event_limit}", flush=True)
            events = list_settled_events(session, series, event_limit, start, end)
            for event in events:
                event["series_ticker"] = series
            selected_events.extend(events)
            time.sleep(0.25)

    if args.dry_run:
        for event in selected_events:
            print(event.get("series_ticker"), event.get("event_ticker"), event.get("title"))
        return 0

    print(f"fetching event bid/ask candles for {len(selected_events)} events", flush=True)
    event_rows: list[dict] = []
    market_frames: list[pd.DataFrame] = []
    quote_frames: list[pd.DataFrame] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {}
        for event in selected_events:
            series = event["series_ticker"]
            cfg = dict(SERIES_CONFIG[series])
            cfg["series_ticker"] = series
            future = executor.submit(
                fetch_event_bundle,
                event,
                cfg,
                cutoff,
                part_dir,
                args.chunk_minutes,
                args.overwrite,
                args.skip_existing,
            )
            futures[future] = event
        for idx, future in enumerate(as_completed(futures), start=1):
            event = futures[future]
            ticker = event.get("event_ticker")
            try:
                event_ticker, event_row, market_df, quote_df, message = future.result()
            except Exception as exc:
                failures.append({"event_ticker": ticker, "error": repr(exc)})
                print(f"[{idx}/{len(futures)}] error {ticker}: {exc}", flush=True)
                continue
            if event_row:
                event_rows.append(event_row)
            if not market_df.empty:
                market_frames.append(market_df)
            if not quote_df.empty:
                quote_frames.append(quote_df)
            print(f"[{idx}/{len(futures)}] {event_ticker}: {message}", flush=True)

    events_df = pd.DataFrame(event_rows)
    markets_df = pd.concat(market_frames, ignore_index=True) if market_frames else pd.DataFrame()
    quotes_df = pd.concat(quote_frames, ignore_index=True) if quote_frames else pd.DataFrame()
    if not quotes_df.empty:
        quotes_df = quotes_df.drop_duplicates(["market_ticker", "ts_end"]).sort_values(["available_at", "market_ticker"]).reset_index(drop=True)
    if markets_df.empty or quotes_df.empty:
        raise SystemExit("No usable Kalshi quotes were collected.")

    print("fetching Coinbase spot data", flush=True)
    min_open = pd.to_datetime(markets_df["open_time"], utc=True).min() - pd.Timedelta(days=args.spot_lookback_days)
    max_close = pd.to_datetime(markets_df["close_time"], utc=True).max() + pd.Timedelta(minutes=1)
    spot_frames = []
    for product_id in sorted(markets_df["product_id"].dropna().unique()):
        print(f"  {product_id}: {min_open} -> {max_close}", flush=True)
        cache_path = cache_dir / f"{product_id.lower().replace('-', '_')}_1m.parquet"
        spot = fetch_coinbase_minutes(str(product_id), min_open, max_close, cache_path)
        if not spot.empty:
            spot_frames.append(spot)
    spot_df = pd.concat(spot_frames, ignore_index=True) if spot_frames else pd.DataFrame()

    tables = {
        "kalshi_events": events_df,
        "kalshi_markets": markets_df,
        "kalshi_quotes": quotes_df,
        "spot_1m": spot_df,
    }
    for name, df in tables.items():
        df.to_parquet(args.out_dir / f"{name}.parquet", index=False)
    db_path = write_duckdb(args.out_dir, tables)
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "kalshi_cutoff_market_settled_ts": cutoff.isoformat(),
        "series": requested_series,
        "events": int(len(events_df)),
        "markets": int(len(markets_df)),
        "quotes": int(len(quotes_df)),
        "spot_rows": int(len(spot_df)),
        "families": markets_df.groupby("family")["event_ticker"].nunique().to_dict() if not markets_df.empty else {},
        "quote_events": int(quotes_df["event_ticker"].nunique()) if not quotes_df.empty else 0,
        "failures": failures,
        "db_path": str(db_path),
        "notes": [
            "Kalshi quotes are observed 1-minute bid/ask candles; no forward fill.",
            "NO ask is represented as 1 - observed YES bid.",
            "Coinbase spot rows use available_at = bucket_start + 1 minute.",
            "Recent events use event candlesticks; markets older than the cutoff route to historical market candlesticks.",
        ],
    }
    (args.out_dir / "datamart_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
