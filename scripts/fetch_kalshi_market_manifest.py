#!/usr/bin/env python3
"""Fetch Kalshi market metadata into a local Parquet manifest.

Predexon orderbook pulls need market tickers and close/open windows. Predexon's
market-list endpoint cannot filter by date, so for old BTC ranges it is faster
to discover tickers from Kalshi's paginated market metadata endpoints, filter
locally, and then pass the manifest to the Predexon downloader.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def parse_time(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def to_unix_seconds(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).tz_convert("UTC").timestamp())


def request_json(session: requests.Session, path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = BASE_URL + path
    for attempt in range(6):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(min(20.0, 2.0**attempt))
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(20.0, 2.0**attempt)
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"failed request: {path}")


def normalize(rows: list[dict[str, Any]], source: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).copy()
    if "ticker" in df.columns and "market_ticker" not in df.columns:
        df["market_ticker"] = df["ticker"]
    for col in ["market_ticker", "ticker", "event_ticker", "series_ticker"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype(str).str.upper()
    for col in [
        "open_time",
        "close_time",
        "expected_expiration_time",
        "expiration_time",
        "latest_expiration_time",
        "settlement_time",
        "determination_time",
        "created_time",
        "created_at",
        "updated_time",
        "updated_at",
    ]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    for col in ["floor_strike", "cap_strike", "volume", "open_interest"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["metadata_source"] = source
    df["downloaded_at_utc"] = pd.Timestamp.now(tz="UTC")
    return df.dropna(subset=["market_ticker", "open_time", "close_time"])


def fetch_series_endpoint(
    session: requests.Session,
    *,
    endpoint: str,
    series: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    limit: int,
    max_pages: int | None,
    sleep_seconds: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cursor = ""
    pages = 0
    seen_in_range = False
    source = endpoint.strip("/").replace("/", "_")
    while True:
        params: dict[str, Any] = {"series_ticker": series, "limit": limit}
        if endpoint == "/markets":
            params["min_close_ts"] = to_unix_seconds(start)
            params["max_close_ts"] = to_unix_seconds(end)
        if cursor:
            params["cursor"] = cursor
        data = request_json(session, endpoint, params)
        markets = data.get("markets") or []
        pages += 1
        frame = normalize(markets, source)
        kept = pd.DataFrame()
        if not frame.empty:
            closes = pd.to_datetime(frame["close_time"], utc=True, errors="coerce")
            in_range = (closes >= start) & (closes < end)
            if bool(in_range.any()):
                seen_in_range = True
                kept = frame.loc[in_range].copy()
                rows.extend(kept.to_dict("records"))
            min_close = closes.min()
            max_close = closes.max()
        else:
            min_close = pd.NaT
            max_close = pd.NaT
        print(
            f"{series} {endpoint} page={pages} got={len(markets)} kept={len(kept)} "
            f"page_close={min_close} -> {max_close} total_kept={len(rows)}",
            flush=True,
        )
        if max_pages and pages >= max_pages:
            break
        cursor = str(data.get("cursor") or "")
        if not cursor or not markets:
            break
        if endpoint == "/historical/markets" and seen_in_range and pd.notna(max_close) and max_close < start:
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Kalshi market metadata manifest.")
    parser.add_argument("--series", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--include-current", action="store_true")
    args = parser.parse_args()

    start = parse_time(args.start)
    end = parse_time(args.end)
    endpoints = ["/historical/markets"]
    if args.include_current:
        endpoints.append("/markets")

    session = requests.Session()
    frames: list[pd.DataFrame] = []
    for series in args.series:
        for endpoint in endpoints:
            frames.append(
                fetch_series_endpoint(
                    session,
                    endpoint=endpoint,
                    series=series.upper(),
                    start=start,
                    end=end,
                    limit=min(max(args.limit, 1), 1000),
                    max_pages=args.max_pages,
                    sleep_seconds=max(args.sleep, 0.0),
                )
            )

    out = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
    if not out.empty:
        out = out.drop_duplicates("market_ticker").sort_values(["series_ticker", "close_time", "market_ticker"]).reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.suffix.lower() == ".csv":
        out.to_csv(args.out, index=False)
    else:
        out.to_parquet(args.out, index=False, compression="zstd")
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "start": str(start),
        "end": str(end),
        "series": [s.upper() for s in args.series],
        "rows": int(len(out)),
        "events": int(out["event_ticker"].nunique()) if "event_ticker" in out.columns and not out.empty else 0,
        "path": str(args.out),
    }
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
