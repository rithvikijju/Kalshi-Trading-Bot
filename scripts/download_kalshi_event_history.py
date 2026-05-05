#!/usr/bin/env python3
"""
Download Kalshi event price-history CSVs.

This automates the manual browser flow for pages like:
  https://kalshi.com/markets/kxbtcd/bitcoin-price-abovebelow/kxbtcd-26mar0507

It uses Kalshi's public market/event candlestick API and writes one wide CSV per
event with the same shape as the website's minute CSV export:
  timestamp,$62250 or above,$62500 or above,...
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

import requests


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
MONTH_TO_NUM = {
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
NUM_TO_MONTH = {v: k for k, v in MONTH_TO_NUM.items()}
EVENT_RE = re.compile(
    r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$"
)
TICKER_STRIKE_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)$")


class KalshiDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class EventId:
    series: str
    local_hour: datetime

    @property
    def ticker(self) -> str:
        mon = NUM_TO_MONTH[self.local_hour.month]
        return (
            f"{self.series}-{self.local_hour:%y}{mon}"
            f"{self.local_hour:%d}{self.local_hour:%H}"
        )

    @property
    def filename_stem(self) -> str:
        return self.ticker.lower()


def parse_event_ticker(raw: str) -> EventId:
    ticker = raw.strip().upper()
    match = EVENT_RE.match(ticker)
    if not match:
        raise ValueError(
            f"Invalid event ticker {raw!r}. Expected a ticker like KXBTCD-26MAR0507."
        )

    mon = match.group("mon")
    if mon not in MONTH_TO_NUM:
        raise ValueError(f"Invalid month abbreviation in event ticker {raw!r}.")

    year = 2000 + int(match.group("yy"))
    local_hour = datetime(
        year,
        MONTH_TO_NUM[mon],
        int(match.group("day")),
        int(match.group("hour")),
    )
    return EventId(series=match.group("series"), local_hour=local_hour)


def iter_event_range(start: str, end: str) -> Iterable[EventId]:
    start_event = parse_event_ticker(start)
    end_event = parse_event_ticker(end)
    if start_event.series != end_event.series:
        raise ValueError("start-event and end-event must use the same series ticker.")
    if end_event.local_hour < start_event.local_hour:
        raise ValueError("end-event must be at or after start-event.")

    cursor = start_event.local_hour
    while cursor <= end_event.local_hour:
        yield EventId(series=start_event.series, local_hour=cursor)
        cursor += timedelta(hours=1)


def parse_iso_utc(value: str) -> datetime:
    if not value:
        raise ValueError("Expected an ISO timestamp, got empty value.")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def request_json(
    session: requests.Session,
    path: str,
    params: dict | None = None,
    retries: int = 4,
    timeout: int = 30,
) -> dict:
    url = f"{BASE_URL}{path}"
    last_error = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code == 429 and attempt < retries - 1:
                wait_s = 2.0 * (attempt + 1)
                print(f"  rate limited; sleeping {wait_s:.1f}s")
                time.sleep(wait_s)
                continue
            if response.status_code >= 400:
                raise KalshiDownloadError(
                    f"GET {path} failed with HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            return response.json()
        except (requests.RequestException, ValueError, KalshiDownloadError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            break

    raise KalshiDownloadError(f"GET {path} failed: {last_error}")


def fetch_markets(session: requests.Session, event_ticker: str) -> list[dict]:
    markets: list[dict] = []
    for path in ("/markets", "/historical/markets"):
        cursor = None
        while True:
            params = {"event_ticker": event_ticker, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = request_json(session, path, params=params)
            markets.extend(data.get("markets") or [])
            cursor = data.get("cursor")
            if not cursor:
                break
        if markets:
            return markets
    return markets


def normalize_strike(raw: Decimal) -> int:
    # Kalshi "or above" BTC tickers usually encode $73,000 as T72999.99.
    fractional = raw - int(raw)
    if fractional == Decimal("0.99"):
        raw += Decimal("0.01")
    return int(raw.to_integral_value(rounding=ROUND_HALF_UP))


def strike_from_ticker(ticker: str) -> int | None:
    match = TICKER_STRIKE_RE.search(ticker)
    if not match:
        return None
    try:
        return normalize_strike(Decimal(match.group("strike")))
    except InvalidOperation:
        return None


def strike_from_market(market: dict) -> int:
    for key in ("floor_strike", "cap_strike"):
        value = market.get(key)
        if value is None:
            continue
        try:
            return normalize_strike(Decimal(str(value)))
        except InvalidOperation:
            pass

    strike = strike_from_ticker(market["ticker"])
    if strike is None:
        raise ValueError(f"Could not infer strike from market {market['ticker']!r}.")
    return strike


def price_value(candle: dict, field: str) -> Decimal | None:
    price = candle.get("price") or {}
    field_keys = {
        "mean": ("mean_dollars", "mean", "close_dollars", "close"),
        "close": ("close_dollars", "close"),
        "open": ("open_dollars", "open"),
        "high": ("high_dollars", "high"),
        "low": ("low_dollars", "low"),
    }[field]

    for key in field_keys:
        raw = price.get(key)
        if raw is None:
            continue
        try:
            return Decimal(str(raw)) * Decimal("100")
        except InvalidOperation:
            return None
    return None


def format_cents(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value.quantize(Decimal('0.01')):.2f}"


def fetch_event_candlesticks(
    session: requests.Session,
    series_ticker: str,
    event_ticker: str,
    start_ts: int,
    end_ts: int,
    period_interval: int,
) -> dict[str, list[dict]]:
    data = request_json(
        session,
        f"/series/{series_ticker}/events/{event_ticker}/candlesticks",
        params={
            "period_interval": period_interval,
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
    )
    tickers = data.get("market_tickers") or []
    candles = data.get("market_candlesticks") or []
    if len(tickers) != len(candles):
        raise KalshiDownloadError(
            f"{event_ticker}: response had {len(tickers)} tickers but "
            f"{len(candles)} candlestick arrays."
        )
    return dict(zip(tickers, candles))


def write_event_csv(
    output_path: Path,
    markets: list[dict],
    candles_by_ticker: dict[str, list[dict]],
    start_ts: int,
    end_ts: int,
    period_interval: int,
    field: str,
) -> tuple[int, int]:
    market_rows = sorted(
        (
            {
                "ticker": market["ticker"],
                "strike": strike_from_market(market),
                "candles": candles_by_ticker.get(market["ticker"], []),
            }
            for market in markets
        ),
        key=lambda row: row["strike"],
    )

    headers = ["timestamp"] + [
        f"${row['strike']} or above" for row in market_rows
    ]
    candle_lookup = {
        row["ticker"]: {c["end_period_ts"]: c for c in row["candles"]}
        for row in market_rows
    }
    last_value: dict[str, Decimal] = {}
    step_seconds = period_interval * 60
    row_count = 0
    non_empty_values = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        ts = start_ts + step_seconds
        while ts <= end_ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            row = [dt.strftime("%Y-%m-%dT%H:%M:%SZ")]

            for market in market_rows:
                ticker = market["ticker"]
                candle = candle_lookup[ticker].get(ts)
                if candle:
                    value = price_value(candle, field)
                    if value is not None:
                        last_value[ticker] = value

                value = last_value.get(ticker)
                if value is not None:
                    non_empty_values += 1
                row.append(format_cents(value))

            writer.writerow(row)
            row_count += 1
            ts += step_seconds

    return row_count, non_empty_values


def output_filename(event_ticker: str, period_interval: int) -> str:
    label = {1: "minute", 60: "hour", 1440: "day"}.get(
        period_interval,
        f"{period_interval}m",
    )
    return f"kalshi-price-history-{event_ticker.lower()}-{label}.csv"


def download_event(
    session: requests.Session,
    event: EventId,
    output_dir: Path,
    period_interval: int,
    field: str,
    overwrite: bool,
    max_duration_minutes: int,
) -> Path | None:
    event_ticker = event.ticker
    output_path = output_dir / output_filename(event_ticker, period_interval)
    if output_path.exists() and not overwrite:
        print(f"skip {event_ticker}: {output_path.name} already exists")
        return None

    markets = fetch_markets(session, event_ticker)
    if not markets:
        print(f"skip {event_ticker}: no markets returned")
        return None

    series_ticker = markets[0].get("series_ticker") or event.series
    open_times = [parse_iso_utc(m["open_time"]) for m in markets if m.get("open_time")]
    close_times = [parse_iso_utc(m["close_time"]) for m in markets if m.get("close_time")]
    if not open_times or not close_times:
        raise KalshiDownloadError(f"{event_ticker}: missing open_time/close_time metadata.")

    start_ts = int(min(open_times).timestamp())
    end_ts = int(max(close_times).timestamp())
    duration_minutes = (end_ts - start_ts) // 60
    if max_duration_minutes > 0 and duration_minutes > max_duration_minutes:
        print(
            f"skip {event_ticker}: duration is {duration_minutes} minutes "
            f"(max {max_duration_minutes}); likely not an hourly market"
        )
        return None

    title = markets[0].get("title") or event_ticker
    print(
        f"download {event_ticker}: {len(markets)} markets, "
        f"{datetime.fromtimestamp(start_ts, tz=timezone.utc):%Y-%m-%d %H:%MZ}"
        f" -> {datetime.fromtimestamp(end_ts, tz=timezone.utc):%Y-%m-%d %H:%MZ}"
    )
    print(f"  {title}")

    candles_by_ticker = fetch_event_candlesticks(
        session,
        series_ticker=series_ticker,
        event_ticker=event_ticker,
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=period_interval,
    )
    rows, values = write_event_csv(
        output_path,
        markets=markets,
        candles_by_ticker=candles_by_ticker,
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=period_interval,
        field=field,
    )
    print(f"  wrote {output_path} ({rows} rows, {values} populated cells)")
    return output_path


def build_event_list(args: argparse.Namespace) -> list[EventId]:
    if args.events:
        return [parse_event_ticker(event) for event in args.events]
    if args.start_event and args.end_event:
        return list(iter_event_range(args.start_event, args.end_event))
    raise ValueError("Pass --events or both --start-event and --end-event.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Kalshi BTC above/below event price history CSVs."
    )
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument(
        "--events",
        nargs="+",
        help="One or more event tickers, e.g. KXBTCD-26MAR0507 KXBTCD-26MAR0508.",
    )
    parser.add_argument(
        "--start-event",
        help="Inclusive first event ticker for an hourly event-ticker range.",
    )
    parser.add_argument(
        "--end-event",
        help="Inclusive last event ticker for an hourly event-ticker range.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for CSVs. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--period-interval",
        type=int,
        choices=(1, 60, 1440),
        default=1,
        help="Candlestick interval in minutes. Default: 1.",
    )
    parser.add_argument(
        "--field",
        choices=("mean", "close", "open", "high", "low"),
        default="mean",
        help="Price field to export from each candle. The website CSV uses mean.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing CSV files instead of skipping them.",
    )
    parser.add_argument(
        "--max-duration-minutes",
        type=int,
        default=60,
        help=(
            "Skip events whose market open/close span is longer than this. "
            "Default: 60 for hourly BTC markets. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the event tickers that would be downloaded without calling Kalshi.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds to sleep between events. Default: 0.2.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel event downloads. Keep modest to avoid Kalshi rate limits. Default: 1.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        events = build_event_list(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for event in events:
            print(event.ticker)
        return 0

    downloaded = 0
    if args.workers <= 1:
        session = requests.Session()
        for idx, event in enumerate(events, start=1):
            print(f"[{idx}/{len(events)}]", end=" ")
            try:
                if download_event(
                    session,
                    event=event,
                    output_dir=args.output_dir,
                    period_interval=args.period_interval,
                    field=args.field,
                    overwrite=args.overwrite,
                    max_duration_minutes=args.max_duration_minutes,
                ):
                    downloaded += 1
            except Exception as exc:
                print(f"error {event.ticker}: {exc}", file=sys.stderr)
            if idx < len(events) and args.delay > 0:
                time.sleep(args.delay)
    else:
        def worker(event: EventId) -> tuple[str, bool, str | None]:
            try:
                with requests.Session() as session:
                    path = download_event(
                        session,
                        event=event,
                        output_dir=args.output_dir,
                        period_interval=args.period_interval,
                        field=args.field,
                        overwrite=args.overwrite,
                        max_duration_minutes=args.max_duration_minutes,
                    )
                return event.ticker, path is not None, None
            except Exception as exc:
                return event.ticker, False, str(exc)

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(worker, event): event for event in events}
            for idx, future in enumerate(as_completed(futures), start=1):
                ticker, ok, error = future.result()
                if ok:
                    downloaded += 1
                elif error:
                    print(f"error {ticker}: {error}", file=sys.stderr)
                print(f"progress {idx}/{len(events)} downloaded={downloaded}", flush=True)
                if args.delay > 0:
                    time.sleep(args.delay)

    print(f"done: downloaded {downloaded}, skipped/failed {len(events) - downloaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
