#!/usr/bin/env python3
"""Download execution-faithful Kalshi event bid/ask candles to Parquet parts."""

from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.download_kalshi_event_history import (
    BASE_URL,
    EventId,
    fetch_event_candlesticks,
    iter_event_range,
    parse_event_ticker,
)


DEFAULT_CSV_DIR = PROJECT_ROOT / "data"
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "research_datamart" / "kalshi_bidask_events"
NY_TZ = ZoneInfo("America/New_York")
CSV_FILE_RE = re.compile(
    r"^kalshi-price-history-(?P<event>[a-z0-9]+-\d{2}[a-z]{3}\d{4})-minute\.csv$",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Kalshi event bid/ask candles as Parquet parts.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--events", nargs="+", help="Explicit event tickers.")
    source.add_argument("--from-csv-dir", type=Path, default=DEFAULT_CSV_DIR, help="Infer events from downloaded CSV files.")
    parser.add_argument("--start-event", help="Inclusive event ticker start.")
    parser.add_argument("--end-event", help="Inclusive event ticker end.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--series", default="KXBTCD")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def events_from_csv_dir(csv_dir: Path, series: str) -> list[EventId]:
    events: dict[str, EventId] = {}
    for path in csv_dir.rglob("kalshi-price-history-*-minute.csv"):
        match = CSV_FILE_RE.match(path.name)
        if not match:
            continue
        ticker = match.group("event").upper()
        if not ticker.startswith(f"{series.upper()}-"):
            continue
        try:
            event = parse_event_ticker(ticker)
        except ValueError:
            continue
        events[event.ticker] = event
    return [events[key] for key in sorted(events)]


def build_event_list(args: argparse.Namespace) -> list[EventId]:
    if args.events:
        events = [parse_event_ticker(event) for event in args.events]
    elif args.start_event and args.end_event:
        events = list(iter_event_range(args.start_event, args.end_event))
    elif args.from_csv_dir:
        events = events_from_csv_dir(args.from_csv_dir, args.series)
    else:
        raise ValueError("Pass --events, --start-event/--end-event, or --from-csv-dir.")
    if args.max_events:
        events = events[: args.max_events]
    return events


def event_close(event: EventId) -> datetime:
    local = event.local_hour.replace(tzinfo=NY_TZ)
    return local.astimezone(ZoneInfo("UTC"))


def price_component(candle: dict, side: str, field: str) -> float | None:
    data = candle.get(side) or {}
    for key in (f"{field}_dollars", field):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


def numeric_value(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def price_mean(candle: dict) -> float | None:
    data = candle.get("price") or {}
    for key in ("mean_dollars", "mean", "close_dollars", "close"):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


def candle_rows(event_ticker: str, market_ticker: str, candles: Iterable[dict]) -> list[dict]:
    rows = []
    for candle in candles:
        ts = candle.get("end_period_ts")
        if not ts:
            continue
        yes_bid_close = price_component(candle, "yes_bid", "close")
        yes_ask_close = price_component(candle, "yes_ask", "close")
        if yes_bid_close is None or yes_ask_close is None:
            continue
        rows.append(
            {
                "market_ticker": market_ticker,
                "event_ticker": event_ticker,
                "ts_end": pd.Timestamp(int(ts), unit="s", tz="UTC"),
                "available_at": pd.Timestamp(int(ts), unit="s", tz="UTC"),
                "yes_bid_open": price_component(candle, "yes_bid", "open"),
                "yes_bid_high": price_component(candle, "yes_bid", "high"),
                "yes_bid_low": price_component(candle, "yes_bid", "low"),
                "yes_bid_close": yes_bid_close,
                "yes_ask_open": price_component(candle, "yes_ask", "open"),
                "yes_ask_high": price_component(candle, "yes_ask", "high"),
                "yes_ask_low": price_component(candle, "yes_ask", "low"),
                "yes_ask_close": yes_ask_close,
                "yes_ask_exe": yes_ask_close,
                "no_ask_exe": 1.0 - yes_bid_close,
                "spread_cents": (yes_ask_close - yes_bid_close) * 100.0,
                "price_mean": price_mean(candle),
                "volume": numeric_value(candle.get("volume") or candle.get("volume_fp")),
                "open_interest": numeric_value(candle.get("open_interest") or candle.get("open_interest_fp")),
                "source": "event_candlestick_api",
                "fidelity": "historical_candle_bidask",
            }
        )
    return rows


def download_event(event: EventId, out_dir: Path, overwrite: bool) -> tuple[str, bool, str]:
    out_path = out_dir / f"{event.ticker.lower()}.parquet"
    if out_path.exists() and not overwrite:
        return event.ticker, False, "exists"

    close_dt = event_close(event)
    open_dt = close_dt - timedelta(hours=1)
    start_ts = int(open_dt.timestamp())
    end_ts = int(close_dt.timestamp())

    with requests.Session() as session:
        candles_by_ticker = fetch_event_candlesticks(
            session,
            series_ticker=event.series,
            event_ticker=event.ticker,
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=1,
        )

    rows: list[dict] = []
    for market_ticker, candles in candles_by_ticker.items():
        rows.extend(candle_rows(event.ticker, market_ticker, candles))
    if not rows:
        return event.ticker, False, "no bid/ask candles"

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    return event.ticker, True, f"{len(rows)} rows"


def main() -> int:
    args = parse_args()
    try:
        events = build_event_list(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"events: {len(events)} out_dir={args.out_dir} endpoint={BASE_URL}", flush=True)
    if args.dry_run:
        for event in events:
            print(event.ticker)
        return 0

    downloaded = 0
    skipped = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download_event, event, args.out_dir, args.overwrite): event for event in events}
        for idx, future in enumerate(as_completed(futures), start=1):
            ticker = futures[future].ticker
            try:
                ticker, wrote, message = future.result()
            except Exception as exc:
                failed += 1
                print(f"[{idx}/{len(events)}] error {ticker}: {exc}", file=sys.stderr, flush=True)
            else:
                if wrote:
                    downloaded += 1
                    action = "wrote"
                else:
                    skipped += 1
                    action = "skip"
                print(f"[{idx}/{len(events)}] {action} {ticker}: {message}", flush=True)
            if args.delay > 0:
                time.sleep(args.delay)

    print(f"done: wrote={downloaded} skipped={skipped} failed={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
