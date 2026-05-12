#!/usr/bin/env python3
"""Live executor for the BTC 15-minute low-drawdown momentum candidate.

Strategy: `lowdd_candidate_no_rv` from the BTC15M websocket validation.

Rules:
  * KXBTC15M current event only
  * one contract max
  * TTL 4-5 minutes
  * spread <= 2c
  * entry 5-90c
  * 2-minute YES-midpoint move >= 12.5c in the traded direction
  * BTC 3-minute return must not oppose the traded direction
  * reject absolute 3-minute YES-midpoint move > 35c
  * first qualifying trade per event
  * fill-or-kill using the real YES/NO book, not NO = 1 - YES shortcuts
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import queue
import sqlite3
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"btc15m_lowdd_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("btc15m_lowdd_live")

from config.btc_1hr_config import CFG, kalshi_fee_dollars  # noqa: E402
from scripts import btc_1hr_research_live as live  # noqa: E402
from scripts.btc15m_live_capture import (  # noqa: E402
    CaptureKalshiWsClient,
    ReadableDuckCaptureWriter,
    event_from_market_ticker,
    event_summary,
    record_event_metadata,
    scan_btc15m_events,
)


SERIES_TICKER = "KXBTC15M"
STRATEGY_NAME = "btc15m_lowdd_no_rv"
MODE_LIVE = "live_btc15m_lowdd"
DEFAULT_CAPTURE_DB = Path(CFG["db_dir"]).expanduser() / "btc15m_live_capture.duckdb"
DEFAULT_TRADE_DB = Path(CFG["db_dir"]).expanduser() / "btc15m_lowdd_live_trades.db"

TTL_LO = 4.0
TTL_HI = 5.0
SPREAD_MAX_CENTS = 2.0
MKT2_THRESHOLD = 0.125
ABS3_CAP = 0.35
ENTRY_MIN = 0.05
ENTRY_MAX = 0.90
BTC_LOOKBACK_MIN = 3
MKT_LOOKBACK_MIN = 2
ABS_LOOKBACK_MIN = 3
MAX_CONTRACTS = 1
SCAN_LOG_SEC = 30.0


@dataclass(frozen=True)
class HistoryPoint:
    received_at_ns: int
    value: float


class AsofHistory:
    def __init__(self, max_age_sec: float = 900.0) -> None:
        self.max_age_ns = int(max_age_sec * 1_000_000_000)
        self.rows: dict[str, deque[HistoryPoint]] = defaultdict(deque)

    def add(self, key: str, received_at_ns: int, value: float | None) -> None:
        if value is None or not math.isfinite(float(value)):
            return
        row = HistoryPoint(int(received_at_ns), float(value))
        dq = self.rows[str(key).upper()]
        if dq and dq[-1].received_at_ns == row.received_at_ns and abs(dq[-1].value - row.value) < 1e-12:
            return
        dq.append(row)
        cutoff = row.received_at_ns - self.max_age_ns
        while len(dq) > 2 and dq[0].received_at_ns < cutoff:
            dq.popleft()

    def at_or_before(self, key: str, target_ns: int) -> float | None:
        dq = self.rows.get(str(key).upper())
        if not dq:
            return None
        # Histories are tiny for 15-minute windows; reverse scan is simpler and
        # avoids maintaining an auxiliary index.
        for row in reversed(dq):
            if row.received_at_ns <= target_ns:
                return row.value
        return None


def utc_dt(value: Any) -> datetime | None:
    return live.utc_dt(value)


def market_strike_from_compact_ticker(market_ticker: str | None) -> float:
    # KXBTC15M up/down tickers end in a compact threshold suffix. The exact
    # threshold is not needed for the live rule; keep a numeric placeholder for
    # the shared trade ledger schema.
    text = str(market_ticker or "")
    try:
        return float(text.rsplit("-", 1)[-1])
    except Exception:
        return 0.0


def quote_mid(quote: live.BookQuote) -> float | None:
    if quote.yes_bid is None or quote.yes_ask is None:
        return None
    return 0.5 * (float(quote.yes_bid) + float(quote.yes_ask))


def side_probability_proxy(side: str, entry: float, move: float) -> float:
    # This strategy is not a fair-value probability model. Store a conservative
    # ledger proxy so downstream sizing/PnL code has a bounded probability-like
    # value without pretending this is calibrated.
    strength = min(0.20, max(0.0, abs(move) - MKT2_THRESHOLD))
    p_side = min(0.95, max(entry + 0.01, entry + strength))
    return p_side if side == "yes" else 1.0 - p_side


def latest_books(state: live.LiveMarketState) -> dict[str, tuple[live.BookQuote, int]]:
    out: dict[str, tuple[live.BookQuote, int]] = {}
    with state._lock:  # same module-level state object; held briefly.
        for ticker, book in state.orderbooks.items():
            if not book.snapshot_received or book.received_at_ns is None:
                continue
            out[str(ticker).upper()] = (book.to_quote(), int(book.received_at_ns))
    return out


def latest_spot(state: live.LiveMarketState) -> tuple[float | None, int | None]:
    with state._lock:
        return state.btc_spot, state.btc_spot_received_at_ns


def record_scan(
    recorder: live.LiveCaptureWriter,
    received_at_ns: int,
    event_ticker: str | None,
    changed_markets: int,
    evaluated: int,
    spot: float | None,
    action: str,
    detail: str,
    signal: live.TradeSignal | None = None,
) -> None:
    recorder.record(
        "signal_scan",
        {
            "received_at_ns": received_at_ns,
            "received_at_utc": live.ns_to_utc_iso(received_at_ns),
            "reason": "btc15m_lowdd",
            "mode": MODE_LIVE,
            "event_ticker": signal.event_ticker if signal else event_ticker,
            "changed_markets": changed_markets,
            "evaluated_markets": evaluated,
            "candidate_count": 1 if signal else 0,
            "selected_market": signal.market_ticker if signal else None,
            "selected_side": signal.side if signal else None,
            "entry_price": signal.entry_price if signal else None,
            "net_edge_cents": signal.net_edge_cents if signal else None,
            "model_p_yes": signal.model_p_yes if signal else None,
            "btc_spot": spot,
            "latency_ms": 0.0,
            "blocked_events": 0,
            "action": action,
            "detail": detail[:900],
        },
    )


def record_decision(
    recorder: live.LiveCaptureWriter,
    signal: live.TradeSignal,
    action: str,
    detail: str,
    portfolio: live.PortfolioSnapshot | None,
    estimated_cost: float | None,
    client_order_id: str | None,
) -> None:
    recorder.record(
        "order_decision",
        {
            "received_at_ns": live.utc_now_ns(),
            "received_at_utc": live.ns_to_utc_iso(live.utc_now_ns()),
            "mode": MODE_LIVE,
            "action": action,
            "event_ticker": signal.event_ticker,
            "market_ticker": signal.market_ticker,
            "side": signal.side,
            "contracts": signal.contracts,
            "entry_price": signal.entry_price,
            "yes_limit_price": signal.yes_limit_price,
            "net_edge_cents": signal.net_edge_cents,
            "btc_spot": signal.btc_spot,
            "estimated_cost": estimated_cost,
            "portfolio_available": portfolio.available_balance if portfolio else None,
            "portfolio_value": portfolio.portfolio_value if portfolio else None,
            "client_order_id": client_order_id,
            "detail": detail[:900],
        },
    )


def build_signal(
    *,
    event: dict[str, Any],
    market: dict[str, Any],
    quote: live.BookQuote,
    quote_ns: int,
    mid_history: AsofHistory,
    btc_history: AsofHistory,
    state: live.LiveMarketState,
) -> tuple[live.TradeSignal | None, str]:
    close_time = utc_dt(event.get("close_time"))
    if close_time is None:
        return None, "missing_close_time"
    now = datetime.now(timezone.utc)
    ttl_min = (close_time - now).total_seconds() / 60.0
    if ttl_min < TTL_LO or ttl_min > TTL_HI:
        return None, f"ttl_outside_{ttl_min:.2f}"
    if quote.yes_bid is None or quote.yes_ask is None or quote.no_bid is None or quote.no_ask is None:
        return None, "incomplete_book"
    spread_cents = (float(quote.yes_ask) - float(quote.yes_bid)) * 100.0
    if spread_cents < -1e-9 or spread_cents > SPREAD_MAX_CENTS + 1e-9:
        return None, f"spread_{spread_cents:.2f}c"

    mid_now = quote_mid(quote)
    if mid_now is None:
        return None, "missing_mid"
    market_ticker = str(market.get("ticker") or quote.ticker).upper()
    mid2 = mid_history.at_or_before(market_ticker, quote_ns - int(MKT_LOOKBACK_MIN * 60 * 1_000_000_000))
    mid3 = mid_history.at_or_before(market_ticker, quote_ns - int(ABS_LOOKBACK_MIN * 60 * 1_000_000_000))
    if mid2 is None:
        return None, "need_2m_market_history"
    if mid3 is None:
        return None, "need_3m_market_history"
    chg2 = mid_now - mid2
    chg3 = mid_now - mid3
    if abs(chg3) > ABS3_CAP + 1e-12:
        return None, f"abs3_{chg3:.3f}"

    spot_now, spot_ns = latest_spot(state)
    if spot_now is None or spot_ns is None:
        return None, "missing_btc_spot"
    spot3 = btc_history.at_or_before("BTC", spot_ns - int(BTC_LOOKBACK_MIN * 60 * 1_000_000_000))
    if spot3 is None or spot3 <= 0:
        return None, "need_3m_btc_history"
    btc_ret3_bps = 10000.0 * math.log(float(spot_now) / float(spot3))

    if chg2 >= MKT2_THRESHOLD and btc_ret3_bps >= 0.0:
        side = "yes"
        entry = float(quote.yes_ask)
        available_qty = float(quote.yes_ask_qty)
        yes_order_side = "bid"
        yes_limit_price = float(quote.yes_ask)
    elif chg2 <= -MKT2_THRESHOLD and btc_ret3_bps <= 0.0:
        side = "no"
        entry = float(quote.no_ask)
        available_qty = float(quote.no_ask_qty)
        yes_order_side = "ask"
        yes_limit_price = float(quote.yes_bid)
    else:
        return None, f"direction_filter chg2={chg2:.3f} btc3={btc_ret3_bps:.2f}bps"

    if entry < ENTRY_MIN or entry > ENTRY_MAX:
        return None, f"entry_{entry:.3f}"
    if available_qty < MAX_CONTRACTS:
        return None, f"top_qty_{available_qty:.2f}"

    fee = kalshi_fee_dollars(entry, contracts=MAX_CONTRACTS, liquidity="taker")
    gross_edge_cents = abs(chg2) * 100.0
    model_p_yes = side_probability_proxy(side, entry, chg2)
    signal = live.TradeSignal(
        event_ticker=str(event.get("event_ticker") or "").upper(),
        market_ticker=market_ticker,
        side=side,
        contracts=MAX_CONTRACTS,
        entry_price=entry,
        yes_order_side=yes_order_side,
        yes_limit_price=yes_limit_price,
        available_qty=available_qty,
        model_p_yes=model_p_yes,
        edge_gross_cents=gross_edge_cents,
        entry_fee=fee,
        net_edge_cents=gross_edge_cents - fee * 100.0,
        edge_threshold_cents=MKT2_THRESHOLD * 100.0,
        spread_cents=spread_cents,
        strike=market_strike_from_compact_ticker(market_ticker),
        btc_spot=float(spot_now),
        ttl_min=ttl_min,
        close_time=close_time.isoformat(),
        yes_bid=quote.yes_bid,
        yes_ask=quote.yes_ask,
        no_bid=quote.no_bid,
        no_ask=quote.no_ask,
    )
    return signal, f"pass chg2={chg2:.3f} chg3={chg3:.3f} btc3={btc_ret3_bps:.2f}bps"


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("event_ticker") or "").upper()


def run_once(
    *,
    mode: str,
    state: live.LiveMarketState,
    recorder: live.LiveCaptureWriter,
    conn: sqlite3.Connection,
    trade_client: live.KalshiApi,
    mid_history: AsofHistory,
    btc_history: AsofHistory,
    changed_tickers: set[str],
    last_no_signal_log: dict[str, float],
) -> None:
    started_ns = live.utc_now_ns()
    books = latest_books(state)
    spot, spot_ns = latest_spot(state)
    if spot is not None and spot_ns is not None:
        btc_history.add("BTC", spot_ns, spot)
    for ticker, (quote, quote_ns) in books.items():
        mid_history.add(ticker, quote_ns, quote_mid(quote))

    events, markets_by_ticker, quotes, scan_spot, market_ok, market_reason = state.snapshot_scan_inputs(changed_tickers or None)
    event = events[0] if events else None
    current_event = event_key(event or {})
    if not market_ok:
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(quotes), scan_spot, "skip", market_reason)
        return
    if not event or not markets_by_ticker:
        record_scan(recorder, started_ns, None, len(changed_tickers), len(quotes), scan_spot, "skip", "no_current_event")
        return
    ready, total = state.orderbook_coverage()
    if total and ready < total:
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(quotes), scan_spot, "skip", f"waiting_for_books {ready}/{total}")
        return

    blocked_events = live.db_active_events(conn, datetime.now(timezone.utc))
    if current_event in blocked_events:
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(quotes), scan_spot, "blocked", "event_already_traded")
        return

    best_signal: live.TradeSignal | None = None
    best_detail = ""
    reject_detail = "no_quotes"
    for market in event.get("markets", []):
        ticker = str(market.get("ticker") or "").upper()
        quote_pack = books.get(ticker)
        if quote_pack is None:
            continue
        quote, quote_ns = quote_pack
        signal, detail = build_signal(
            event=event,
            market=market,
            quote=quote,
            quote_ns=quote_ns,
            mid_history=mid_history,
            btc_history=btc_history,
            state=state,
        )
        reject_detail = detail
        if signal is not None:
            best_signal = signal
            best_detail = detail
            break

    if best_signal is None:
        now = time.monotonic()
        if now - last_no_signal_log.get("ts", 0.0) >= SCAN_LOG_SEC:
            log.info("BTC15M no lowdd signal event=%s detail=%s evaluated=%d", current_event, reject_detail, len(books))
            last_no_signal_log["ts"] = now
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(books), scan_spot, "none", reject_detail)
        return

    record_scan(recorder, started_ns, current_event, len(changed_tickers), len(books), scan_spot, "selected", best_detail, best_signal)
    portfolio = live.get_portfolio_snapshot(trade_client)
    estimated_cost = best_signal.contracts * best_signal.entry_price + best_signal.entry_fee
    if best_signal.market_ticker in portfolio.active_tickers:
        log.info("skip %s: already active in Kalshi portfolio", best_signal.market_ticker)
        record_decision(recorder, best_signal, "skip", "portfolio_active_ticker", portfolio, estimated_cost, None)
        return
    if estimated_cost > portfolio.available_balance:
        log.info("skip %s: cost %.2f > available %.2f", best_signal.market_ticker, estimated_cost, portfolio.available_balance)
        record_decision(recorder, best_signal, "skip", "insufficient_available_balance", portfolio, estimated_cost, None)
        return

    if mode == "dry-run":
        log.info(
            "BTC15M DRY RUN would FOK %s %s x1 entry=%.4f yes_book_%s yes_price=%.4f %s",
            best_signal.market_ticker,
            best_signal.side.upper(),
            best_signal.entry_price,
            best_signal.yes_order_side,
            best_signal.yes_limit_price,
            best_detail,
        )
        record_decision(recorder, best_signal, "dry_run", "would_trade", portfolio, estimated_cost, None)
        return

    client_order_id = f"btc15m-{int(time.time())}-{uuid4().hex[:10]}"
    if not live.acquire_event_lock(conn, best_signal, MODE_LIVE, client_order_id=client_order_id):
        log.info("skip %s: event lock already held", best_signal.market_ticker)
        record_decision(recorder, best_signal, "skip", "event_lock_held", portfolio, estimated_cost, client_order_id)
        return

    trade_id = live.record_trade(conn, MODE_LIVE, "submitted", best_signal, client_order_id=client_order_id)
    record_decision(recorder, best_signal, "submit", "before_order", portfolio, estimated_cost, client_order_id)
    log.info(
        "BTC15M LIVE FOK %s %s x1 entry=%.4f yes_book_%s yes_price=%.4f %s",
        best_signal.market_ticker,
        best_signal.side.upper(),
        best_signal.entry_price,
        best_signal.yes_order_side,
        best_signal.yes_limit_price,
        best_detail,
    )
    try:
        response = trade_client.create_event_order(
            ticker=best_signal.market_ticker,
            yes_book_side=best_signal.yes_order_side,
            count=best_signal.contracts,
            yes_price=best_signal.yes_limit_price,
            client_order_id=client_order_id,
        )
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 409 and live.is_fok_no_fill_conflict(exc):
            live.update_trade_response(
                conn,
                trade_id,
                "not_filled",
                best_signal,
                {"error": {"code": "fill_or_kill_insufficient_resting_volume", "raw": live.http_error_text(exc)}},
            )
            live.release_event_lock(conn, best_signal.event_ticker, client_order_id)
            log.info("BTC15M FOK no fill %s client_order_id=%s", best_signal.market_ticker, client_order_id)
            record_decision(recorder, best_signal, "not_filled", "fok_no_fill_409", portfolio, estimated_cost, client_order_id)
            return
        synced = live.sync_order_by_client_id(trade_client, conn, trade_id, best_signal, client_order_id)
        if synced:
            status, order = synced
            log.info("BTC15M 409 synced status=%s order_id=%s", status, order.get("order_id"))
            record_decision(recorder, best_signal, status, "order_sync_after_409", portfolio, estimated_cost, client_order_id)
            return
        log.exception("BTC15M order submit failed/state unknown %s client_order_id=%s", best_signal.market_ticker, client_order_id)
        record_decision(recorder, best_signal, "submit_failed", "state_unknown", portfolio, estimated_cost, client_order_id)
        raise
    except Exception:
        log.exception("BTC15M order submit failed/state unknown %s client_order_id=%s", best_signal.market_ticker, client_order_id)
        record_decision(recorder, best_signal, "submit_failed", "state_unknown", portfolio, estimated_cost, client_order_id)
        raise

    order = live.order_from_response(response)
    fill_count = live.optional_float(order.get("fill_count_fp")) or live.optional_float(order.get("fill_count")) or 0.0
    if fill_count >= best_signal.contracts:
        status = "filled"
    elif fill_count > 0:
        status = "partial_filled"
    else:
        status = "not_filled"
    live.update_trade_response(conn, trade_id, status, best_signal, response)
    if status == "not_filled":
        live.release_event_lock(conn, best_signal.event_ticker, client_order_id)
    record_decision(recorder, best_signal, status, "order_response", portfolio, estimated_cost, client_order_id)
    log.info("BTC15M LIVE response status=%s response=%s", status, response)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "dry-run"], default="live")
    parser.add_argument("--env", choices=["prod", "demo"], default="prod")
    parser.add_argument("--capture-db-path", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--trade-db-path", type=Path, default=DEFAULT_TRADE_DB)
    parser.add_argument("--refresh-sec", type=float, default=10.0)
    parser.add_argument("--health-sec", type=float, default=30.0)
    parser.add_argument("--duration-sec", type=float, default=0.0)
    parser.add_argument("--no-capture", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.refresh_sec < 2:
        raise SystemExit("--refresh-sec must be >= 2")
    if args.health_sec < 5:
        raise SystemExit("--health-sec must be >= 5")

    live.RESEARCH_SERIES = SERIES_TICKER
    live.event_from_market_ticker = event_from_market_ticker

    data_client = live.KalshiApi(env=args.env, require_auth=True)
    trade_client = live.KalshiApi(env=args.env, require_auth=True)
    conn = live.db_connect(args.trade_db_path)
    state = live.LiveMarketState()
    update_queue = live.CoalescedUpdateBuffer()
    recorder = ReadableDuckCaptureWriter(args.capture_db_path, enabled=not args.no_capture, capture_raw_ws=False)
    mode_args = SimpleNamespace(mode=MODE_LIVE)
    mid_history = AsofHistory()
    btc_history = AsofHistory()
    last_no_signal_log: dict[str, float] = {}

    log.info(
        "starting BTC15M lowdd executor mode=%s env=%s strategy=%s contracts=1 db=%s capture_db=%s log=%s",
        args.mode,
        args.env,
        STRATEGY_NAME,
        args.trade_db_path,
        args.capture_db_path,
        LOG_FILE,
    )

    kalshi_ws: CaptureKalshiWsClient | None = None
    spot_ws: live.KrakenWsSpot | None = None
    started = time.monotonic()
    next_refresh = 0.0
    next_health = 0.0
    current_tickers: set[str] = set()
    events: list[dict[str, Any]] = []

    try:
        events_all = scan_btc15m_events(data_client, use_cache=False)
        events = events_all[:1]
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
            changed_tickers: set[str] = set()
            lifecycle_seen = False
            for item in updates:
                if item.get("kind") == "batch":
                    changed_tickers.update(str(t).upper() for t in item.get("tickers", []))
                    lifecycle_seen = lifecycle_seen or "lifecycle" in set(item.get("flags", set()))
                elif item.get("kind") == "orderbook":
                    ticker = item.get("market_ticker")
                    if ticker:
                        changed_tickers.add(str(ticker).upper())
                elif item.get("kind") == "btc_spot":
                    pass
                elif item.get("kind") == "lifecycle":
                    lifecycle_seen = True

            now = time.monotonic()
            if next_refresh == 0.0 or lifecycle_seen or now >= next_refresh:
                try:
                    events_all = scan_btc15m_events(data_client, use_cache=False)
                    events = events_all[:1]
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

            if updates:
                if args.mode == "dry-run":
                    # Reuse the same signal path but do not submit. We still
                    # record selected scans for test visibility.
                    pass
                run_once(
                    mode=args.mode,
                    state=state,
                    recorder=recorder,
                    conn=conn,
                    trade_client=trade_client,
                    mid_history=mid_history,
                    btc_history=btc_history,
                    changed_tickers=changed_tickers,
                    last_no_signal_log=last_no_signal_log,
                )

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
        conn.close()
        log.info("stopped BTC15M lowdd executor")


if __name__ == "__main__":
    main()
