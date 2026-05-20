#!/usr/bin/env python3
"""Capture live KXBTC15M websocket orderbook data for research.

This is capture-only: it never submits orders and does not share any mutable
state with the 1-hour live trader. It reuses the hardened websocket/orderbook
and DuckDB writer code from btc_1hr_research_live.py so the resulting tables
match the live replay tooling as closely as possible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dateutil import parser as dtparser

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"btc15m_live_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("btc15m_live_capture")

from config.btc_1hr_config import CFG  # noqa: E402
from scripts import btc_1hr_research_live as live  # noqa: E402


SERIES_TICKER = "KXBTC15M"
DEFAULT_CAPTURE_DB = Path(CFG["db_dir"]).expanduser() / "btc15m_live_capture.duckdb"
DEFAULT_CAPTURE_WRITER = os.getenv("BTC15M_CAPTURE_WRITER", "readable").strip().lower()
if DEFAULT_CAPTURE_WRITER not in {"readable", "persistent"}:
    raise ValueError("BTC15M_CAPTURE_WRITER must be 'readable' or 'persistent'")


class ReadableDuckCaptureWriter(live.LiveCaptureWriter):
    """DuckDB capture writer that releases the file lock after each flush.

    The 1h live trader keeps one DuckDB connection open for maximum throughput.
    For BTC15M research capture we want to inspect the DB while the capture is
    still running, so this variant opens DuckDB only for each batch flush.
    """

    def _flush_with_fresh_connection(self, pending: dict[str, list[dict[str, Any]]], *, max_wait_sec: float = 120.0) -> bool:
        if not any(rows for rows in pending.values()):
            return True
        import duckdb

        last_exc: Exception | None = None
        started = time.monotonic()
        attempt = 0
        while True:
            con = None
            try:
                con = duckdb.connect(str(self.path))
                self._init_schema(con)
                self._flush(con, pending)
                con.close()
                self.last_error = None
                return True
            except Exception as exc:
                last_exc = exc
                try:
                    if con is not None:
                        con.close()
                except Exception:
                    pass
                if time.monotonic() - started >= max_wait_sec:
                    self.last_error = repr(last_exc)
                    queued = sum(len(rows) for rows in pending.values())
                    log.warning(
                        "fresh DuckDB flush blocked for %.1fs; pending=%d last_error=%r; will keep retrying",
                        time.monotonic() - started,
                        queued,
                        last_exc,
                    )
                    return False
                attempt += 1
                time.sleep(min(1.0, 0.05 * attempt))

    def _run(self) -> None:
        pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
        try:
            self._flush_with_fresh_connection(pending)
            last_flush = time.monotonic()
            while True:
                timeout = max(0.05, live.CAPTURE_FLUSH_SEC - (time.monotonic() - last_flush))
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    item = None
                if item is None:
                    if self._flush_with_fresh_connection(pending):
                        last_flush = time.monotonic()
                    else:
                        time.sleep(0.25)
                    if self._stop.is_set() and self._queue.empty():
                        break
                    continue
                table, row = item
                pending[table].append(row)
                queued = sum(len(rows) for rows in pending.values())
                if queued >= live.CAPTURE_BATCH_SIZE or time.monotonic() - last_flush >= live.CAPTURE_FLUSH_SEC:
                    if self._flush_with_fresh_connection(pending):
                        last_flush = time.monotonic()
                    else:
                        time.sleep(0.25)
        except Exception as exc:
            self.failed = True
            self.last_error = repr(exc)
            log.exception("readable DuckDB capture writer failed")
        finally:
            try:
                self._flush_with_fresh_connection(pending)
            except Exception:
                log.exception("readable DuckDB capture writer close failed")


class CaptureKalshiWsClient(live.KalshiWsClient):
    """Kalshi websocket client without private trading subscriptions."""

    async def _subscribe_static_channels(self, ws) -> None:
        await self._send(ws, {"cmd": "subscribe", "params": {"channels": ["market_lifecycle_v2"]}})


def utc_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = dtparser.isoparse(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def event_from_market_ticker(market_ticker: str | None) -> str | None:
    ticker = str(market_ticker or "").upper()
    if not ticker:
        return None
    parts = ticker.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else None


def scan_btc15m_events(api: live.KalshiApi, *, use_cache: bool = False) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    markets = api.get_markets(
        series_ticker=SERIES_TICKER,
        status="open",
        use_cache=use_cache,
        limit=200,
    ).get("markets", [])
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        status = str(market.get("status") or "").lower()
        if status not in {"open", "active"}:
            continue
        event_ticker = str(market.get("event_ticker") or event_from_market_ticker(market.get("ticker")) or "").upper()
        ticker = str(market.get("ticker") or "").upper()
        close_time = utc_dt(market.get("close_time"))
        if not event_ticker.startswith(SERIES_TICKER) or not ticker.startswith(f"{event_ticker}-"):
            continue
        if close_time is None or close_time < now:
            continue
        by_event[event_ticker].append(market)

    events: list[dict[str, Any]] = []
    for event_ticker, event_markets in by_event.items():
        closes = [utc_dt(m.get("close_time")) for m in event_markets]
        closes = [dt for dt in closes if dt is not None]
        if not closes:
            continue
        close_time = min(closes)
        ttl_min = (close_time - now).total_seconds() / 60.0
        events.append(
            {
                "event_ticker": event_ticker,
                "title": event_markets[0].get("title", ""),
                "close_time": close_time,
                "ttl_hours": ttl_min / 60.0,
                "markets": sorted(event_markets, key=lambda m: str(m.get("ticker") or "")),
            }
        )
    return sorted(events, key=lambda event: event["ttl_hours"])


def record_event_metadata(recorder: live.LiveCaptureWriter, events: list[dict[str, Any]], source: str) -> None:
    received_at_ns = live.utc_now_ns()
    for event in events:
        for market in event.get("markets", []):
            recorder.record(
                "ws_lifecycle",
                {
                    "received_at_ns": received_at_ns,
                    "received_at_utc": live.ns_to_utc_iso(received_at_ns),
                    "message_type": "event_refresh",
                    "event_type": source,
                    "event_ticker": event.get("event_ticker"),
                    "market_ticker": market.get("ticker"),
                    "open_ts": None,
                    "close_ts": int(event["close_time"].timestamp()) if event.get("close_time") else None,
                    "payload_json": json.dumps({"event": event, "market": market}, default=str, sort_keys=True),
                },
            )


def event_summary(events: list[dict[str, Any]]) -> str:
    if not events:
        return "events=0"
    pieces = []
    for event in events:
        close_time = event.get("close_time")
        ttl_min = float(event.get("ttl_hours") or 0.0) * 60.0
        pieces.append(
            f"{event.get('event_ticker')} markets={len(event.get('markets', []))} ttl={ttl_min:.1f}m close={close_time.isoformat() if close_time else None}"
        )
    return "; ".join(pieces)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture live KXBTC15M websocket orderbook data into DuckDB.")
    parser.add_argument("--capture-db-path", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--refresh-sec", type=float, default=10.0, help="How often to refresh active KXBTC15M markets.")
    parser.add_argument("--health-sec", type=float, default=30.0, help="How often to record/log capture health.")
    parser.add_argument("--duration-sec", type=float, default=0.0, help="Optional finite run length for smoke tests.")
    parser.add_argument("--capture-raw-ws", action="store_true", help="Also store raw snapshot levels/deltas. Usually off to avoid bloat.")
    parser.add_argument("--no-capture", action="store_true", help="Run websocket connections without writing DuckDB rows.")
    parser.add_argument(
        "--capture-writer",
        choices=["readable", "persistent"],
        default=DEFAULT_CAPTURE_WRITER,
        help="Use readable fresh-connection DuckDB writes or faster persistent writes.",
    )
    parser.add_argument("--env", choices=["prod", "demo"], default="prod")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.refresh_sec < 2:
        raise SystemExit("--refresh-sec must be >= 2")
    if args.health_sec < 5:
        raise SystemExit("--health-sec must be >= 5")

    # The reused websocket client's lifecycle filter reads this module global.
    live.RESEARCH_SERIES = SERIES_TICKER
    # The 1h parser expects "-T"/"-B" strike suffixes; KXBTC15M tickers use
    # a compact suffix like KXBTC15M-26MAY120715-15.
    live.event_from_market_ticker = event_from_market_ticker

    args.capture_db_path = Path(args.capture_db_path).expanduser()
    mode_args = SimpleNamespace(mode="btc15m_capture")
    log.info(
        "starting BTC15M capture series=%s env=%s capture=%s raw_ws=%s refresh=%.1fs health=%.1fs db=%s log=%s",
        SERIES_TICKER,
        args.env,
        not args.no_capture,
        args.capture_raw_ws,
        args.refresh_sec,
        args.health_sec,
        args.capture_db_path,
        LOG_FILE,
    )

    data_client = live.KalshiApi(env=args.env, require_auth=True)
    update_queue = live.CoalescedUpdateBuffer()
    writer_cls = live.LiveCaptureWriter if args.capture_writer == "persistent" else ReadableDuckCaptureWriter
    recorder = writer_cls(args.capture_db_path, enabled=not args.no_capture, capture_raw_ws=args.capture_raw_ws)
    state = live.LiveMarketState()
    kalshi_ws: CaptureKalshiWsClient | None = None
    spot_ws: live.KrakenWsSpot | None = None
    started = time.monotonic()
    next_refresh = 0.0
    next_health = 0.0
    current_tickers: set[str] = set()

    try:
        events = scan_btc15m_events(data_client, use_cache=False)
        current_tickers = state.set_events(events)
        record_event_metadata(recorder, events, "startup")
        log.info("initial %s", event_summary(events))

        kalshi_ws = CaptureKalshiWsClient(data_client, state, recorder, update_queue, env=args.env)
        spot_ws = live.KrakenWsSpot(state, recorder, update_queue)
        kalshi_ws.start(current_tickers)
        spot_ws.start()
        live.record_capture_health(recorder, state, mode_args, "startup", event_summary(events))

        while True:
            now = time.monotonic()
            if args.duration_sec > 0 and now - started >= args.duration_sec:
                log.info("duration reached; shutting down")
                break

            timeout = max(0.1, min(next_refresh or now, next_health or now) - now)
            first_update = None
            try:
                first_update = update_queue.get(timeout=timeout)
            except queue.Empty:
                pass
            updates = live.drain_updates(update_queue, first_update)
            lifecycle_seen = False
            for item in updates:
                if item.get("kind") == "batch":
                    lifecycle_seen = lifecycle_seen or "lifecycle" in set(item.get("flags", set()))
                elif item.get("kind") == "lifecycle":
                    lifecycle_seen = True

            now = time.monotonic()
            if next_refresh == 0.0 or lifecycle_seen or now >= next_refresh:
                try:
                    events = scan_btc15m_events(data_client, use_cache=False)
                    new_tickers = state.set_events(events)
                    record_event_metadata(recorder, events, "refresh")
                    if new_tickers != current_tickers:
                        current_tickers = new_tickers
                        if kalshi_ws:
                            kalshi_ws.update_markets(current_tickers)
                        log.info("subscription refresh %s", event_summary(events))
                    elif lifecycle_seen:
                        log.info("lifecycle refresh %s", event_summary(events))
                except Exception:
                    log.exception("BTC15M event refresh failed")
                next_refresh = now + args.refresh_sec

            if next_health == 0.0 or now >= next_health:
                ready, total = state.orderbook_coverage()
                health = state.health_snapshot()
                log.info(
                    "health market_ok=%s reason=%s books=%d/%d spot=%s age=%s queue=%d dropped=%d %s",
                    health["market_ok"],
                    health["market_reason"],
                    ready,
                    total,
                    f"{health['btc_spot']:.2f}" if health.get("btc_spot") else None,
                    f"{health['btc_spot_age_sec']:.1f}s" if health.get("btc_spot_age_sec") is not None else None,
                    recorder.depth(),
                    recorder.dropped,
                    event_summary(events),
                )
                live.record_capture_health(recorder, state, mode_args, "heartbeat", event_summary(events))
                next_health = now + args.health_sec
    finally:
        live.record_capture_health(recorder, state, mode_args, "shutdown")
        if kalshi_ws:
            kalshi_ws.stop()
        if spot_ws:
            spot_ws.stop()
        recorder.close()
        log.info("stopped BTC15M capture db=%s", args.capture_db_path)


if __name__ == "__main__":
    main()
