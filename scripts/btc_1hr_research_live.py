#!/usr/bin/env python3
"""
Live execution wrapper for the BTC 1-hour research strategy.

Default mode is live production execution. Use --dry-run or --paper for tests.

The live signal path is intentionally pinned to the successful backtest:
  - KXBTCD hourly BTC markets only
  - cumulative "$X or above" markets only
  - one signal per minute/event
  - one contract per signal
  - empirical training window ends at event open, not during the event

Pricing is taken from the live orderbook:
  - buy YES at YES ask = 1 - best NO bid
  - buy NO at NO ask = 1 - best YES bid

Kalshi V2 event orders quote everything on the YES book:
  - buy YES -> side=bid, price=YES entry price
  - buy NO  -> side=ask, price=1 - NO entry price
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
import queue
import random
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dateutil import parser as dtparser

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR, kalshi_fee_dollars, load_credentials
from scripts import btc_1hr_neohardened as base_strategy
from scripts.risk_adjusted_research import RiskSizingConfig, choose_risk_adjusted_contracts


EXECUTOR_NAME = os.getenv("BTC_1HR_EXECUTOR_NAME", "btc_1hr_research_live")
SIGNAL_STRATEGY = os.getenv("BTC_1HR_SIGNAL_STRATEGY", "research").strip().lower() or "research"
SUPPORTED_SIGNAL_STRATEGIES = {"research", "js_guarded", "market_shrink_no_cautious"}
SIZING_POLICY = os.getenv("BTC_1HR_SIZING_POLICY", "flat_max").strip().lower() or "flat_max"
SUPPORTED_SIZING_POLICIES = {"flat_max", "risk_adjusted"}

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"{EXECUTOR_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
BTC_LIVE_CACHE_PATH = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("btc_1hr_research_live")


RESEARCH_MIN_EDGE_CENTS = 12.0
RESEARCH_MAX_SPREAD_CENTS = 2.0
RESEARCH_MIN_ENTRY = 0.25
RESEARCH_MAX_ENTRY = 0.75
RESEARCH_MIN_YES_P = 0.65
RESEARCH_MAX_NO_P = 0.35
BRTI_DAMPENING = 0.80
JS_MARKET_SHRINK = 0.25
JS_MIN_EDGE_CENTS = 8.0
JS_MAX_SPREAD_CENTS = 2.0
JS_MIN_ENTRY = 0.55
JS_MAX_ENTRY = 0.80
JS_GUARDED_MAX_ABS_MONEYNESS_BPS = 60.0
JS_GUARDED_EXCLUDED_UTC_HOURS = set(range(17, 24))
MARKET_SHRINK_NO_CAUTIOUS_MARKET_SHRINK = 0.25
MARKET_SHRINK_NO_CAUTIOUS_MIN_EDGE_CENTS = 8.0
MARKET_SHRINK_NO_CAUTIOUS_MAX_NO_P = 0.20
MARKET_SHRINK_NO_CAUTIOUS_NO_EDGE_ADD_CENTS = 3.0
ORDERBOOK_BATCH_SIZE = 100
ACTIVE_TRADE_STATUSES = ("paper_filled", "filled", "partial_filled", "submitted")
FLOAT_EPSILON = 1e-9
KALSHI_WS_PATH = "/trade-api/ws/v2"
KALSHI_PROD_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_WS_URL = "wss://ws.kraken.com"
BTC_SPOT_MAX_AGE_SEC = 15.0
BTC_CANDLE_MAX_AGE_SEC = 180.0
BTC_CANDLE_REFRESH_LOOKBACK_MIN = 10
BTC_CANDLE_FALLBACK_SOURCE = os.getenv("BTC_1HR_BTC_CANDLE_FALLBACK", "kraken").strip().lower()
WS_SCAN_STATUS_TTL_SEC = 30.0
WS_EVENT_REFRESH_SEC = 180.0
WS_EVENT_REFRESH_JITTER_SEC = 30.0
WS_BTC_CANDLE_REFRESH_SEC = 60.0
WS_MAX_SCAN_WORKERS = max(2, min(8, (os.cpu_count() or 4)))
CAPTURE_QUEUE_MAX = 250_000
CAPTURE_BATCH_SIZE = 2_000
CAPTURE_FLUSH_SEC = 1.0
CAPTURE_HIGH_WATERMARKS = (0.50, 0.75, 0.90, 0.95)
CAPTURE_LOW_PRIORITY_TABLES = {"ws_orderbook_delta", "ws_orderbook_snapshot_level"}
CAPTURE_RAW_WS_DEFAULT = os.getenv("BTC_1HR_CAPTURE_RAW_WS", "").strip().lower() in {"1", "true", "yes", "on"}
REST_MAX_RETRIES = 5
REST_BASE_BACKOFF_SEC = 0.5
REST_MAX_BACKOFF_SEC = 20.0
REST_MIN_INTERVAL_SEC = 0.12
OPEN_MARKETS_CACHE_TTL_SEC = 240.0
RESEARCH_SERIES = "KXBTCD"
RESEARCH_TRAIN_DAYS = 7
RESEARCH_BOOTSTRAP_BTC_DAYS = RESEARCH_TRAIN_DAYS + 2
RESEARCH_SIGNAL_CONTRACTS = 1
RESEARCH_MAX_CONTRACTS_PER_TRADE = int(os.getenv("BTC_1HR_MAX_CONTRACTS_PER_TRADE", "3"))
RESEARCH_MAX_TTL_MIN = 65.0
RESEARCH_EVENT_CLOSE_TOLERANCE_SEC = 120
RESEARCH_SCAN_INTERVAL_SEC = 60
RESEARCH_MAX_SIGNALS_PER_CYCLE = 1
RESEARCH_MAX_PER_MARKET_FRACTION = float(CFG.get("max_per_market", 0.20))
RESEARCH_MAX_TOTAL_EXPOSURE_FRACTION = float(CFG.get("max_total_risk", 0.50))
RISK_ADJUSTED_KELLY_FRACTION = float(os.getenv("BTC_1HR_RISK_KELLY_FRACTION", "0.25"))
RISK_ADJUSTED_EDGE_CONFIDENCE = float(os.getenv("BTC_1HR_RISK_EDGE_CONFIDENCE", "0.50"))
RISK_ADJUSTED_MEDIUM_ENTRY_CAP = float(os.getenv("BTC_1HR_RISK_MEDIUM_ENTRY_CAP", "0.55"))
RISK_ADJUSTED_HIGH_ENTRY_CAP = float(os.getenv("BTC_1HR_RISK_HIGH_ENTRY_CAP", "0.65"))
RISK_ADJUSTED_NO_SIDE_CONTRACT_CAP = int(os.getenv("BTC_1HR_RISK_NO_SIDE_CONTRACT_CAP", "3"))
NY_TZ = ZoneInfo("America/New_York")
EVENT_TICKER_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")


class ExpectedWsReconnect(RuntimeError):
    """Raised when a subscription change intentionally forces fresh snapshots."""


class StaleBtcCandleData(RuntimeError):
    """Raised when Coinbase candle history is too stale for faithful signals."""


def set_signal_strategy(strategy: str) -> None:
    global SIGNAL_STRATEGY
    strategy = str(strategy or "").strip().lower()
    if strategy not in SUPPORTED_SIGNAL_STRATEGIES:
        raise ValueError(f"unsupported signal strategy {strategy!r}; expected one of {sorted(SUPPORTED_SIGNAL_STRATEGIES)}")
    SIGNAL_STRATEGY = strategy


def set_sizing_policy(policy: str) -> None:
    global SIZING_POLICY
    policy = str(policy or "").strip().lower()
    if policy not in SUPPORTED_SIZING_POLICIES:
        raise ValueError(f"unsupported sizing policy {policy!r}; expected one of {sorted(SUPPORTED_SIZING_POLICIES)}")
    SIZING_POLICY = policy


@dataclass(frozen=True)
class BookQuote:
    ticker: str
    yes_bid: float | None
    yes_bid_qty: float
    yes_ask: float | None
    yes_ask_qty: float
    no_bid: float | None
    no_bid_qty: float
    no_ask: float | None
    no_ask_qty: float

    @property
    def yes_spread_cents(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return max(0.0, (self.yes_ask - self.yes_bid) * 100.0)

    @property
    def no_spread_cents(self) -> float | None:
        if self.no_bid is None or self.no_ask is None:
            return None
        return max(0.0, (self.no_ask - self.no_bid) * 100.0)


@dataclass(frozen=True)
class TradeSignal:
    event_ticker: str
    market_ticker: str
    side: str
    contracts: int
    entry_price: float
    yes_order_side: str
    yes_limit_price: float
    available_qty: float
    model_p_yes: float
    edge_gross_cents: float
    entry_fee: float
    net_edge_cents: float
    edge_threshold_cents: float
    spread_cents: float
    strike: float
    btc_spot: float
    ttl_min: float
    close_time: str
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None


@dataclass(frozen=True)
class PortfolioSnapshot:
    available_balance: float
    portfolio_value: float
    active_tickers: set[str]
    market_exposure: float


class KalshiApi:
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
    PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, env: str = "prod", require_auth: bool = True):
        if env not in {"prod", "demo"}:
            raise ValueError("env must be prod or demo")
        self.env = env
        self.base_url = self.PROD_URL if env == "prod" else self.DEMO_URL
        self.path_prefix = "/trade-api/v2"
        self.session = requests.Session()
        self.key_id = None
        self.private_key = None
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._markets_cache_lock = threading.Lock()
        self._markets_cache: dict[tuple[tuple[str, Any], ...], tuple[float, dict]] = {}

        try:
            creds = load_credentials()
            self.key_id = creds.get("API_KEY_ID_KALSHI")
            private_key_path = creds.get("PRIVATE_KEY_PATH")
            if private_key_path:
                key_path = Path(private_key_path).expanduser()
                with key_path.open("rb") as f:
                    self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        except Exception:
            if require_auth:
                raise

        if require_auth and (not self.key_id or not self.private_key):
            raise RuntimeError("Kalshi credentials are required for this operation.")

    def _sign(self, method: str, path: str) -> dict[str, str]:
        if not self.private_key or not self.key_id:
            return {}
        timestamp = str(int(time.time() * 1000))
        path_for_sig = path.split("?", 1)[0]
        message = (timestamp + method.upper() + self.path_prefix + path_for_sig).encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def websocket_headers(self) -> dict[str, str]:
        if not self.private_key or not self.key_id:
            raise RuntimeError("Kalshi websocket authentication requires API credentials.")
        timestamp = str(int(time.time() * 1000))
        message = (timestamp + "GET" + KALSHI_WS_PATH).encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def _rate_limit(self) -> None:
        with self._request_lock:
            now = time.monotonic()
            wait_s = REST_MIN_INTERVAL_SEC - (now - self._last_request_at)
            if wait_s > 0:
                time.sleep(wait_s)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _retry_after_seconds(response: requests.Response | None) -> float | None:
        if response is None:
            return None
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = dtparser.isoparse(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                return None

    def _backoff_seconds(self, attempt: int, response: requests.Response | None = None) -> float:
        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            return min(REST_MAX_BACKOFF_SEC, retry_after)
        base = min(REST_MAX_BACKOFF_SEC, REST_BASE_BACKOFF_SEC * (2 ** attempt))
        return min(REST_MAX_BACKOFF_SEC, base + random.uniform(0.0, 0.25 * base))

    def _get(self, path: str, params=None, auth: bool = True, timeout: int = 15) -> dict:
        last_exc: Exception | None = None
        for attempt in range(REST_MAX_RETRIES):
            headers = self._sign("GET", path) if auth else {}
            try:
                self._rate_limit()
                response = self.session.get(self.base_url + path, headers=headers, params=params, timeout=timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    wait_s = self._backoff_seconds(attempt, response)
                    log.warning("Kalshi GET %s status=%s; retrying in %.2fs", path, response.status_code, wait_s)
                    time.sleep(wait_s)
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                wait_s = self._backoff_seconds(attempt)
                log.warning("Kalshi GET %s transient failure: %s; retrying in %.2fs", path, exc, wait_s)
                time.sleep(wait_s)
            except requests.HTTPError as exc:
                raise exc
            except ValueError as exc:
                last_exc = exc
                wait_s = self._backoff_seconds(attempt)
                log.warning("Kalshi GET %s decode failure: %s; retrying in %.2fs", path, exc, wait_s)
                time.sleep(wait_s)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Kalshi GET {path} failed after {REST_MAX_RETRIES} attempts")

    def _post(self, path: str, body: dict, timeout: int = 15) -> dict:
        headers = self._sign("POST", path)
        headers["Content-Type"] = "application/json"
        self._rate_limit()
        response = self.session.post(self.base_url + path, headers=headers, json=body, timeout=timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            log.warning("Kalshi POST %s failed status=%s body=%s", path, response.status_code, response.text[:1000])
            raise exc
        return response.json()

    def get_markets(self, limit: int = 100, auto_paginate: bool = True, use_cache: bool = True, **kwargs) -> dict:
        cache_key = tuple(sorted((str(k), v) for k, v in {"limit": limit, "auto_paginate": auto_paginate, **kwargs}.items()))
        cacheable = (
            auto_paginate
            and kwargs.get("series_ticker") == RESEARCH_SERIES
            and str(kwargs.get("status") or "").lower() == "open"
        )
        if cacheable and use_cache:
            with self._markets_cache_lock:
                cached = self._markets_cache.get(cache_key)
                if cached and time.monotonic() - cached[0] <= OPEN_MARKETS_CACHE_TTL_SEC:
                    return {"markets": list(cached[1].get("markets", []))}
        params = {"limit": limit}
        params.update(kwargs)
        markets: list[dict] = []
        cursor = None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets", params=params, auth=False)
            markets.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not auto_paginate or not cursor:
                break
        result = {"markets": markets}
        if cacheable:
            with self._markets_cache_lock:
                self._markets_cache[cache_key] = (time.monotonic(), {"markets": list(markets)})
        return result

    def get_exchange_status(self) -> dict:
        return self._get("/exchange/status", auth=False)

    def get_balance(self) -> dict:
        return self._get("/portfolio/balance", auth=True)

    def get_positions(self, **params) -> dict:
        return self._get("/portfolio/positions", params=params or None, auth=True)

    def get_all_positions(self, **params) -> dict:
        market_positions: list[dict] = []
        event_positions: list[dict] = []
        cursor = None
        while True:
            page_params = dict(params)
            page_params.setdefault("limit", 1000)
            if cursor:
                page_params["cursor"] = cursor
            data = self.get_positions(**page_params)
            market_positions.extend(data.get("market_positions") or data.get("positions") or [])
            event_positions.extend(data.get("event_positions") or [])
            cursor = data.get("cursor")
            if not cursor:
                break
        return {"market_positions": market_positions, "event_positions": event_positions}

    def get_orders(self, **params) -> dict:
        page_params = dict(params)
        page_params.setdefault("limit", 100)
        return self._get("/portfolio/orders", params=page_params, auth=True)

    def find_order_by_client_id(self, client_order_id: str, **params) -> dict | None:
        data = self.get_orders(**params)
        for order in data.get("orders", []):
            if str(order.get("client_order_id") or "") == str(client_order_id):
                return order
        return None

    def get_orderbook(self, ticker: str, depth: int = 1) -> dict:
        return self._get(f"/markets/{ticker}/orderbook", params={"depth": depth}, auth=True)

    def get_orderbooks(self, tickers: list[str], depth: int = 1) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for chunk in chunks(tickers, ORDERBOOK_BATCH_SIZE):
            params: list[tuple[str, str | int]] = [("depth", depth)]
            params.extend(("tickers", ticker) for ticker in chunk)
            try:
                data = self._get("/markets/orderbooks", params=params, auth=True, timeout=25)
            except requests.HTTPError:
                # Some API gateways encode arrays as comma-separated strings; keep this fallback local.
                data = self._get(
                    "/markets/orderbooks",
                    params={"depth": depth, "tickers": ",".join(chunk)},
                    auth=True,
                    timeout=25,
                )
            for item in data.get("orderbooks", []):
                ticker = item.get("ticker")
                if ticker:
                    out[ticker] = item
        missing = [ticker for ticker in tickers if ticker not in out]
        for ticker in missing:
            try:
                out[ticker] = self.get_orderbook(ticker, depth=depth)
            except Exception as exc:
                log.warning("orderbook fetch failed for %s: %s", ticker, exc)
        return out

    def create_event_order(
        self,
        ticker: str,
        yes_book_side: str,
        count: int,
        yes_price: float,
        client_order_id: str,
    ) -> dict:
        if yes_book_side not in {"bid", "ask"}:
            raise ValueError("yes_book_side must be bid or ask")
        body = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": yes_book_side,
            "count": f"{int(count)}.00",
            "price": f"{yes_price:.4f}",
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        return self._post("/portfolio/events/orders", body)


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def normalize_price(value) -> float | None:
    price = optional_float(value)
    if price is None:
        return None
    if price > 1.000001:
        # Kalshi exposes both fixed-point dollar books and legacy cent books.
        price = price / 100.0
    if price < 0.0 or price > 1.0:
        return None
    return price


def best_bid(levels) -> tuple[float | None, float]:
    if not levels:
        return None, 0.0
    parsed = []
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = normalize_price(level[0])
        qty = optional_float(level[1])
        if price is None or qty is None or qty <= 0:
            continue
        parsed.append((price, qty))
    if not parsed:
        return None, 0.0
    return max(parsed, key=lambda item: item[0])


def quote_from_orderbook(ticker: str, payload: dict) -> BookQuote:
    raw_book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes_bid, yes_bid_qty = best_bid(raw_book.get("yes_dollars") or raw_book.get("yes") or [])
    no_bid, no_bid_qty = best_bid(raw_book.get("no_dollars") or raw_book.get("no") or [])
    yes_ask = 1.0 - no_bid if no_bid is not None else None
    no_ask = 1.0 - yes_bid if yes_bid is not None else None
    return BookQuote(
        ticker=ticker,
        yes_bid=yes_bid,
        yes_bid_qty=yes_bid_qty,
        yes_ask=yes_ask,
        yes_ask_qty=no_bid_qty if yes_ask is not None else 0.0,
        no_bid=no_bid,
        no_bid_qty=no_bid_qty,
        no_ask=no_ask,
        no_ask_qty=yes_bid_qty if no_ask is not None else 0.0,
    )


def utc_now_ns() -> int:
    return time.time_ns()


def ns_to_utc_iso(ns: int | None) -> str | None:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()


def parse_ws_levels(msg: dict, side: str) -> list[tuple[float, float]]:
    if side == "yes":
        raw = msg.get("yes_dollars_fp") or msg.get("yes_dollars") or msg.get("yes") or []
    else:
        raw = msg.get("no_dollars_fp") or msg.get("no_dollars") or msg.get("no") or []
    levels: list[tuple[float, float]] = []
    for level in raw or []:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = normalize_price(level[0])
        qty = optional_float(level[1])
        if price is None or qty is None or qty <= 0:
            continue
        levels.append((price, qty))
    return levels


def best_bid_from_levels(levels: dict[float, float]) -> tuple[float | None, float]:
    active = [(price, qty) for price, qty in levels.items() if qty > 0]
    if not active:
        return None, 0.0
    return max(active, key=lambda item: item[0])


@dataclass
class LiveOrderbook:
    market_ticker: str
    sid: int | None = None
    seq: int | None = None
    yes: dict[float, float] = field(default_factory=dict)
    no: dict[float, float] = field(default_factory=dict)
    received_at_ns: int | None = None
    exchange_ts: str | None = None
    snapshot_received: bool = False

    def apply_snapshot(self, msg: dict, sid: int | None, seq: int | None, received_at_ns: int) -> None:
        self.sid = sid
        self.seq = seq
        self.yes = {price: qty for price, qty in parse_ws_levels(msg, "yes")}
        self.no = {price: qty for price, qty in parse_ws_levels(msg, "no")}
        self.received_at_ns = received_at_ns
        self.exchange_ts = msg.get("ts") or msg.get("time")
        self.snapshot_received = True

    def apply_delta(self, msg: dict, sid: int | None, seq: int | None, received_at_ns: int) -> None:
        side = str(msg.get("side") or "").lower()
        price = normalize_price(msg.get("price_dollars") or msg.get("price"))
        delta = optional_float(msg.get("delta_fp") or msg.get("delta"))
        if side not in {"yes", "no"} or price is None or delta is None:
            return
        levels = self.yes if side == "yes" else self.no
        new_qty = levels.get(price, 0.0) + delta
        if new_qty <= 1e-9:
            levels.pop(price, None)
        else:
            levels[price] = new_qty
        self.sid = sid
        self.seq = seq
        self.received_at_ns = received_at_ns
        self.exchange_ts = msg.get("ts") or msg.get("time")

    def to_quote(self) -> BookQuote:
        yes_bid, yes_bid_qty = best_bid_from_levels(self.yes)
        no_bid, no_bid_qty = best_bid_from_levels(self.no)
        yes_ask = 1.0 - no_bid if no_bid is not None else None
        no_ask = 1.0 - yes_bid if yes_bid is not None else None
        return BookQuote(
            ticker=self.market_ticker,
            yes_bid=yes_bid,
            yes_bid_qty=yes_bid_qty,
            yes_ask=yes_ask,
            yes_ask_qty=no_bid_qty if yes_ask is not None else 0.0,
            no_bid=no_bid,
            no_bid_qty=no_bid_qty,
            no_ask=no_ask,
            no_ask_qty=yes_bid_qty if no_ask is not None else 0.0,
        )


class LiveMarketState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.events: list[dict] = []
        self.markets_by_ticker: dict[str, dict] = {}
        self.orderbooks: dict[str, LiveOrderbook] = {}
        self.btc_spot: float | None = None
        self.btc_spot_received_at_ns: int | None = None
        self.kalshi_connected = False
        self.coinbase_connected = False

    def set_events(self, events: list[dict]) -> set[str]:
        with self._lock:
            self.events = list(events)
            self.markets_by_ticker = {
                str(market["ticker"]).upper(): market
                for event in self.events
                for market in event.get("markets", [])
                if market.get("ticker")
            }
            wanted = set(self.markets_by_ticker)
            for ticker in list(self.orderbooks):
                if ticker not in wanted:
                    self.orderbooks.pop(ticker, None)
            return wanted

    def desired_market_tickers(self) -> set[str]:
        with self._lock:
            return set(self.markets_by_ticker)

    def current_event_tickers(self) -> set[str]:
        with self._lock:
            return {str(event.get("event_ticker")).upper() for event in self.events if event.get("event_ticker")}

    def set_btc_spot(self, spot: float, received_at_ns: int) -> None:
        if not math.isfinite(spot) or spot <= 0:
            return
        with self._lock:
            self.btc_spot = float(spot)
            self.btc_spot_received_at_ns = received_at_ns

    def mark_kalshi_connected(self, connected: bool) -> None:
        with self._lock:
            self.kalshi_connected = connected

    def reset_orderbooks(self) -> None:
        with self._lock:
            self.orderbooks = {}

    def mark_coinbase_connected(self, connected: bool) -> None:
        with self._lock:
            self.coinbase_connected = connected

    def mark_spot_feed_connected(self, connected: bool) -> None:
        self.mark_coinbase_connected(connected)

    def apply_orderbook_snapshot(self, sid: int | None, seq: int | None, msg: dict, received_at_ns: int) -> BookQuote | None:
        ticker = str(msg.get("market_ticker") or "").upper()
        if not ticker:
            return None
        with self._lock:
            book = self.orderbooks.setdefault(ticker, LiveOrderbook(ticker))
            book.apply_snapshot(msg, sid=sid, seq=seq, received_at_ns=received_at_ns)
            return book.to_quote()

    def apply_orderbook_delta(self, sid: int | None, seq: int | None, msg: dict, received_at_ns: int) -> BookQuote | None:
        ticker = str(msg.get("market_ticker") or "").upper()
        if not ticker:
            return None
        with self._lock:
            book = self.orderbooks.setdefault(ticker, LiveOrderbook(ticker))
            book.apply_delta(msg, sid=sid, seq=seq, received_at_ns=received_at_ns)
            return book.to_quote() if book.snapshot_received else None

    def snapshot_scan_inputs(
        self,
        changed_tickers: set[str] | None = None,
    ) -> tuple[list[dict], dict[str, dict], dict[str, BookQuote], float | None, bool, str]:
        now_ns = utc_now_ns()
        with self._lock:
            markets = dict(self.markets_by_ticker)
            if changed_tickers:
                tickers = {str(t).upper() for t in changed_tickers if str(t).upper() in markets}
            else:
                tickers = set(markets)
            quotes: dict[str, BookQuote] = {}
            for ticker in tickers:
                book = self.orderbooks.get(ticker)
                if not book or not book.snapshot_received or book.received_at_ns is None:
                    continue
                quotes[ticker] = book.to_quote()
            if not self.kalshi_connected:
                market_ok = False
                reason = "kalshi_ws_disconnected"
            elif not self.coinbase_connected:
                market_ok = False
                reason = "spot_ws_disconnected"
            elif self.btc_spot_received_at_ns is None:
                market_ok = False
                reason = "no_btc_spot"
            elif (now_ns - self.btc_spot_received_at_ns) / 1_000_000_000 > BTC_SPOT_MAX_AGE_SEC:
                market_ok = False
                reason = "stale_btc_spot"
            else:
                market_ok = True
                reason = "ok"
            return list(self.events), markets, quotes, self.btc_spot, market_ok, reason

    def orderbook_coverage(self) -> tuple[int, int]:
        with self._lock:
            total = len(self.markets_by_ticker)
            ready = sum(
                1
                for ticker in self.markets_by_ticker
                if ticker in self.orderbooks and self.orderbooks[ticker].snapshot_received
            )
            return ready, total

    def health_snapshot(self) -> dict[str, Any]:
        now_ns = utc_now_ns()
        with self._lock:
            event_tickers = sorted(
                str(event.get("event_ticker")).upper()
                for event in self.events
                if event.get("event_ticker")
            )
            ready, total = self.orderbook_coverage()
            btc_age_sec = None
            if self.btc_spot_received_at_ns is not None:
                btc_age_sec = (now_ns - self.btc_spot_received_at_ns) / 1_000_000_000
            if not self.kalshi_connected:
                market_ok = False
                reason = "kalshi_ws_disconnected"
            elif not self.coinbase_connected:
                market_ok = False
                reason = "spot_ws_disconnected"
            elif self.btc_spot_received_at_ns is None:
                market_ok = False
                reason = "no_btc_spot"
            elif btc_age_sec is not None and btc_age_sec > BTC_SPOT_MAX_AGE_SEC:
                market_ok = False
                reason = "stale_btc_spot"
            elif total and ready < total:
                market_ok = False
                reason = f"waiting_for_orderbook_snapshots {ready}/{total}"
            else:
                market_ok = True
                reason = "ok"
            return {
                "event_ticker": ",".join(event_tickers) if event_tickers else None,
                "kalshi_connected": self.kalshi_connected,
                "coinbase_connected": self.coinbase_connected,
                "market_ok": market_ok,
                "market_reason": reason,
                "ready_books": ready,
                "total_books": total,
                "subscribed_markets": len(self.markets_by_ticker),
                "btc_spot": self.btc_spot,
                "btc_spot_age_sec": btc_age_sec,
            }

    def top_row(
        self,
        ticker: str,
        quote: BookQuote,
        sid: int | None,
        seq: int | None,
        received_at_ns: int,
        source: str,
    ) -> dict[str, Any]:
        with self._lock:
            btc_spot = self.btc_spot
        return {
            "received_at_ns": received_at_ns,
            "received_at_utc": ns_to_utc_iso(received_at_ns),
            "market_ticker": ticker,
            "event_ticker": event_from_market_ticker(ticker),
            "sid": sid,
            "seq": seq,
            "yes_bid": quote.yes_bid,
            "yes_bid_qty": quote.yes_bid_qty,
            "yes_ask": quote.yes_ask,
            "yes_ask_qty": quote.yes_ask_qty,
            "no_bid": quote.no_bid,
            "no_bid_qty": quote.no_bid_qty,
            "no_ask": quote.no_ask,
            "no_ask_qty": quote.no_ask_qty,
            "btc_spot": btc_spot,
            "source": source,
        }


CAPTURE_SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "ws_orderbook_snapshot_level": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("market_ticker", "VARCHAR"),
        ("sid", "BIGINT"),
        ("seq", "BIGINT"),
        ("side", "VARCHAR"),
        ("price", "DOUBLE"),
        ("qty", "DOUBLE"),
    ],
    "ws_orderbook_delta": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("market_ticker", "VARCHAR"),
        ("sid", "BIGINT"),
        ("seq", "BIGINT"),
        ("side", "VARCHAR"),
        ("price", "DOUBLE"),
        ("delta_qty", "DOUBLE"),
        ("exchange_ts", "VARCHAR"),
        ("client_order_id", "VARCHAR"),
    ],
    "ws_orderbook_top": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("market_ticker", "VARCHAR"),
        ("event_ticker", "VARCHAR"),
        ("sid", "BIGINT"),
        ("seq", "BIGINT"),
        ("yes_bid", "DOUBLE"),
        ("yes_bid_qty", "DOUBLE"),
        ("yes_ask", "DOUBLE"),
        ("yes_ask_qty", "DOUBLE"),
        ("no_bid", "DOUBLE"),
        ("no_bid_qty", "DOUBLE"),
        ("no_ask", "DOUBLE"),
        ("no_ask_qty", "DOUBLE"),
        ("btc_spot", "DOUBLE"),
        ("source", "VARCHAR"),
    ],
    "coinbase_ticker": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("product_id", "VARCHAR"),
        ("price", "DOUBLE"),
        ("best_bid", "DOUBLE"),
        ("best_ask", "DOUBLE"),
        ("sequence", "BIGINT"),
        ("exchange_time", "VARCHAR"),
    ],
    "ws_lifecycle": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("message_type", "VARCHAR"),
        ("event_type", "VARCHAR"),
        ("event_ticker", "VARCHAR"),
        ("market_ticker", "VARCHAR"),
        ("open_ts", "BIGINT"),
        ("close_ts", "BIGINT"),
        ("payload_json", "VARCHAR"),
    ],
    "ws_private_event": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("message_type", "VARCHAR"),
        ("market_ticker", "VARCHAR"),
        ("event_ticker", "VARCHAR"),
        ("payload_json", "VARCHAR"),
    ],
    "ws_control": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("source", "VARCHAR"),
        ("message_type", "VARCHAR"),
        ("sid", "BIGINT"),
        ("seq", "BIGINT"),
        ("payload_json", "VARCHAR"),
    ],
    "signal_scan": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("reason", "VARCHAR"),
        ("mode", "VARCHAR"),
        ("event_ticker", "VARCHAR"),
        ("changed_markets", "INTEGER"),
        ("evaluated_markets", "INTEGER"),
        ("candidate_count", "INTEGER"),
        ("selected_market", "VARCHAR"),
        ("selected_side", "VARCHAR"),
        ("entry_price", "DOUBLE"),
        ("net_edge_cents", "DOUBLE"),
        ("model_p_yes", "DOUBLE"),
        ("btc_spot", "DOUBLE"),
        ("latency_ms", "DOUBLE"),
        ("blocked_events", "INTEGER"),
        ("action", "VARCHAR"),
        ("detail", "VARCHAR"),
    ],
    "order_decision": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("mode", "VARCHAR"),
        ("action", "VARCHAR"),
        ("event_ticker", "VARCHAR"),
        ("market_ticker", "VARCHAR"),
        ("side", "VARCHAR"),
        ("contracts", "INTEGER"),
        ("entry_price", "DOUBLE"),
        ("yes_limit_price", "DOUBLE"),
        ("net_edge_cents", "DOUBLE"),
        ("btc_spot", "DOUBLE"),
        ("estimated_cost", "DOUBLE"),
        ("portfolio_available", "DOUBLE"),
        ("portfolio_value", "DOUBLE"),
        ("client_order_id", "VARCHAR"),
        ("detail", "VARCHAR"),
    ],
    "capture_health": [
        ("received_at_ns", "BIGINT"),
        ("received_at_utc", "VARCHAR"),
        ("mode", "VARCHAR"),
        ("kind", "VARCHAR"),
        ("event_ticker", "VARCHAR"),
        ("kalshi_connected", "BOOLEAN"),
        ("coinbase_connected", "BOOLEAN"),
        ("market_ok", "BOOLEAN"),
        ("market_reason", "VARCHAR"),
        ("ready_books", "INTEGER"),
        ("total_books", "INTEGER"),
        ("subscribed_markets", "INTEGER"),
        ("btc_spot", "DOUBLE"),
        ("btc_spot_age_sec", "DOUBLE"),
        ("capture_queue_depth", "INTEGER"),
        ("capture_dropped", "INTEGER"),
        ("detail", "VARCHAR"),
    ],
}


class LiveCaptureWriter:
    def __init__(self, path: Path | str, enabled: bool = True, capture_raw_ws: bool = CAPTURE_RAW_WS_DEFAULT) -> None:
        self.path = Path(path).expanduser()
        self.enabled = enabled
        self.capture_raw_ws = capture_raw_ws
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(maxsize=CAPTURE_QUEUE_MAX)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped = 0
        self.dropped_by_table: dict[str, int] = defaultdict(int)
        self.suppressed_by_table: dict[str, int] = defaultdict(int)
        self.max_depth = 0
        self.last_flush_ms: float | None = None
        self.last_error: str | None = None
        self.failed = False
        self._watermark_idx = -1
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(target=self._run, name="live-capture-writer", daemon=True)
            self._thread.start()

    def record(self, table: str, row: dict[str, Any]) -> None:
        if not self.enabled:
            return
        if table in CAPTURE_LOW_PRIORITY_TABLES and not self.capture_raw_ws:
            self.suppressed_by_table[table] += 1
            return
        if self.failed:
            self._drop(table, "writer_failed")
            return
        if table in CAPTURE_LOW_PRIORITY_TABLES and self._queue.qsize() >= int(CAPTURE_QUEUE_MAX * 0.50):
            self._drop(table, "backpressure")
            return
        try:
            self._queue.put_nowait((table, row))
        except queue.Full:
            if table not in CAPTURE_LOW_PRIORITY_TABLES:
                try:
                    self._queue.put((table, row), timeout=0.05)
                    self._update_depth()
                    return
                except queue.Full:
                    pass
            self._drop(table, "queue_full")
        else:
            self._update_depth()

    def _drop(self, table: str, reason: str) -> None:
        self.dropped += 1
        self.dropped_by_table[table] += 1
        if self.dropped == 1 or self.dropped % 1000 == 0:
            log.warning("live capture queue full/backpressured; dropped=%d table=%s reason=%s", self.dropped, table, reason)

    def _update_depth(self) -> None:
        depth = self._queue.qsize()
        self.max_depth = max(self.max_depth, depth)
        ratio = depth / max(1, CAPTURE_QUEUE_MAX)
        for idx, watermark in enumerate(CAPTURE_HIGH_WATERMARKS):
            if ratio >= watermark and idx > self._watermark_idx:
                self._watermark_idx = idx
                log.warning("live capture queue high watermark %.0f%% depth=%d/%d", watermark * 100.0, depth, CAPTURE_QUEUE_MAX)

    def depth(self) -> int:
        return self._queue.qsize() if self.enabled else 0

    def is_healthy(self) -> bool:
        if not self.enabled:
            return True
        return not self.failed and self.depth() < int(CAPTURE_QUEUE_MAX * 0.95)

    def health_detail(self) -> str:
        if not self.enabled:
            return "capture_disabled"
        pieces = [
            f"depth={self.depth()}",
            f"max_depth={self.max_depth}",
            f"dropped={self.dropped}",
            f"raw_ws={self.capture_raw_ws}",
        ]
        if self.last_flush_ms is not None:
            pieces.append(f"last_flush_ms={self.last_flush_ms:.1f}")
        if self.suppressed_by_table:
            pieces.append("suppressed=" + json.dumps(dict(sorted(self.suppressed_by_table.items())), sort_keys=True))
        if self.dropped_by_table:
            pieces.append("dropped_by_table=" + json.dumps(dict(sorted(self.dropped_by_table.items())), sort_keys=True))
        if self.last_error:
            pieces.append(f"last_error={self.last_error[:200]}")
        return " ".join(pieces)

    def close(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=10)

    def _init_schema(self, con) -> None:
        for table, columns in CAPTURE_SCHEMAS.items():
            defs = ", ".join(f"{name} {typ}" for name, typ in columns)
            con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({defs})")

    def _flush(self, con, pending: dict[str, list[dict[str, Any]]]) -> None:
        started = time.monotonic()
        for table, rows in list(pending.items()):
            if not rows or table not in CAPTURE_SCHEMAS:
                continue
            columns = [name for name, _ in CAPTURE_SCHEMAS[table]]
            values = [tuple(row.get(column) for column in columns) for row in rows]
            placeholders = ",".join("?" for _ in columns)
            con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
            pending[table].clear()
        self.last_flush_ms = (time.monotonic() - started) * 1000.0

    def _run(self) -> None:
        import duckdb

        con = None
        pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
        try:
            con = duckdb.connect(str(self.path))
            self._init_schema(con)
            last_flush = time.monotonic()
            while True:
                timeout = max(0.05, CAPTURE_FLUSH_SEC - (time.monotonic() - last_flush))
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    item = None
                if item is None:
                    self._flush(con, pending)
                    last_flush = time.monotonic()
                    if self._stop.is_set() and self._queue.empty():
                        break
                    continue
                table, row = item
                pending[table].append(row)
                queued = sum(len(rows) for rows in pending.values())
                if queued >= CAPTURE_BATCH_SIZE or time.monotonic() - last_flush >= CAPTURE_FLUSH_SEC:
                    self._flush(con, pending)
                    last_flush = time.monotonic()
        except Exception as exc:
            self.failed = True
            self.last_error = repr(exc)
            log.exception("live capture writer failed")
        finally:
            if con is not None:
                try:
                    self._flush(con, pending)
                    con.close()
                except Exception:
                    log.exception("live capture writer close failed")


class CoalescedUpdateBuffer:
    """Bounded websocket scan trigger buffer.

    The trading loop only needs to know which books changed and whether a full
    scan trigger occurred. Keeping every delta as a separate queue item makes a
    busy orderbook burst indistinguishable from useful work.
    """

    def __init__(self, max_tickers: int = 10_000) -> None:
        self.max_tickers = max_tickers
        self._cond = threading.Condition()
        self._changed_tickers: set[str] = set()
        self._reasons: set[str] = set()
        self._flags: set[str] = set()
        self.dropped_tickers = 0

    def put_update(self, item: dict[str, Any]) -> None:
        kind = str(item.get("kind") or "")
        with self._cond:
            if kind == "orderbook" and item.get("market_ticker"):
                ticker = str(item["market_ticker"]).upper()
                if len(self._changed_tickers) < self.max_tickers or ticker in self._changed_tickers:
                    self._changed_tickers.add(ticker)
                else:
                    self.dropped_tickers += 1
                self._reasons.add(str(item.get("source") or "orderbook"))
            elif kind:
                self._flags.add(kind)
                self._reasons.add(str(item.get("source") or kind))
            self._cond.notify()

    def _has_updates(self) -> bool:
        return bool(self._changed_tickers or self._flags or self._reasons)

    def _snapshot_locked(self) -> dict[str, Any]:
        item = {
            "kind": "batch",
            "changed_tickers": set(self._changed_tickers),
            "flags": set(self._flags),
            "reasons": set(self._reasons),
            "dropped_tickers": self.dropped_tickers,
        }
        self._changed_tickers.clear()
        self._flags.clear()
        self._reasons.clear()
        self.dropped_tickers = 0
        return item

    def get(self, timeout: float | None = None) -> dict[str, Any]:
        with self._cond:
            if not self._has_updates():
                self._cond.wait(timeout=timeout)
            if not self._has_updates():
                raise queue.Empty()
            return self._snapshot_locked()

    def get_nowait(self) -> dict[str, Any]:
        with self._cond:
            if not self._has_updates():
                raise queue.Empty()
            return self._snapshot_locked()

    def qsize(self) -> int:
        with self._cond:
            return len(self._changed_tickers) + len(self._flags)


def safe_put_update(update_queue: queue.Queue, item: dict[str, Any]) -> None:
    if hasattr(update_queue, "put_update"):
        update_queue.put_update(item)
        return
    try:
        update_queue.put_nowait(item)
    except queue.Full:
        log.warning("market update queue full; dropping %s", item.get("kind"))


async def connect_websocket(url: str, headers: dict[str, str] | None = None):
    kwargs = {
        "ping_interval": 20,
        "ping_timeout": 20,
        "max_queue": 4096,
        "close_timeout": 5,
    }
    if headers:
        try:
            return await websockets.connect(url, additional_headers=headers, **kwargs)
        except TypeError as exc:
            if "additional_headers" not in str(exc):
                raise
            return await websockets.connect(url, extra_headers=headers, **kwargs)
    return await websockets.connect(url, **kwargs)


class KalshiWsClient:
    def __init__(
        self,
        api: KalshiApi,
        state: LiveMarketState,
        recorder: LiveCaptureWriter,
        update_queue: queue.Queue,
        env: str = "prod",
    ) -> None:
        self.api = api
        self.state = state
        self.recorder = recorder
        self.update_queue = update_queue
        self.ws_url = KALSHI_PROD_WS_URL if env == "prod" else KALSHI_DEMO_WS_URL
        self._desired_markets: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: asyncio.Queue | None = None
        self._next_id = 1
        self._orderbook_sid: int | None = None
        self._subscribed_markets: set[str] = set()
        self._last_seq_by_sid: dict[int, int] = {}
        self._last_top_by_ticker: dict[str, tuple[Any, ...]] = {}

    def start(self, market_tickers: set[str]) -> None:
        self._desired_markets = {ticker.upper() for ticker in market_tickers}
        self._thread = threading.Thread(target=self._thread_main, name="kalshi-ws", daemon=True)
        self._thread.start()

    def update_markets(self, market_tickers: set[str]) -> None:
        desired = {ticker.upper() for ticker in market_tickers}
        self._desired_markets = desired
        if self._loop and self._commands:
            self._loop.call_soon_threadsafe(self._commands.put_nowait, {"type": "markets", "tickers": desired})

    def stop(self) -> None:
        self._stop.set()
        if self._loop and self._commands:
            self._loop.call_soon_threadsafe(self._commands.put_nowait, {"type": "stop"})
        if self._thread:
            self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._orderbook_sid = None
            self._subscribed_markets = set()
            self._last_seq_by_sid = {}
            self._last_top_by_ticker = {}
            self.state.mark_kalshi_connected(False)
            try:
                await self._run_connection()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except ExpectedWsReconnect as exc:
                if not self._stop.is_set():
                    log.info("Kalshi websocket reconnecting for fresh subscription: %s", exc)
                    await asyncio.sleep(backoff)
                    backoff = min(10.0, backoff * 1.5)
            except Exception:
                if not self._stop.is_set():
                    log.exception("Kalshi websocket connection failed")
                    await asyncio.sleep(backoff)
                    backoff = min(30.0, backoff * 2.0)

    async def _run_connection(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        headers = self.api.websocket_headers()
        ws = await connect_websocket(self.ws_url, headers=headers)
        log.info("Kalshi websocket connected: %s", self.ws_url)
        self.state.reset_orderbooks()
        self.state.mark_kalshi_connected(True)
        try:
            await self._subscribe_static_channels(ws)
            await self._sync_orderbook_subscription(ws)
            recv_task = asyncio.create_task(ws.recv())
            command_task = asyncio.create_task(self._commands.get())
            while not self._stop.is_set():
                done, _ = await asyncio.wait(
                    {recv_task, command_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if recv_task in done:
                    raw = recv_task.result()
                    await self._handle_message(ws, raw)
                    recv_task = asyncio.create_task(ws.recv())
                if command_task in done:
                    command = command_task.result()
                    if command.get("type") == "stop":
                        break
                    if command.get("type") == "markets":
                        self._desired_markets = set(command.get("tickers") or [])
                        await self._sync_orderbook_subscription(ws)
                    command_task = asyncio.create_task(self._commands.get())
            recv_task.cancel()
            command_task.cancel()
        finally:
            self.state.mark_kalshi_connected(False)
            self.state.reset_orderbooks()
            await ws.close()
            log.info("Kalshi websocket disconnected")

    async def _send(self, ws, payload: dict[str, Any]) -> int:
        command_id = self._next_id
        self._next_id += 1
        payload = dict(payload)
        payload["id"] = command_id
        await ws.send(json.dumps(payload))
        return command_id

    async def _subscribe_static_channels(self, ws) -> None:
        await self._send(ws, {"cmd": "subscribe", "params": {"channels": ["market_lifecycle_v2"]}})
        await self._send(ws, {"cmd": "subscribe", "params": {"channels": ["fill"]}})
        await self._send(ws, {"cmd": "subscribe", "params": {"channels": ["user_orders"]}})
        await self._send(ws, {"cmd": "subscribe", "params": {"channels": ["market_positions"]}})

    async def _sync_orderbook_subscription(self, ws) -> None:
        desired = set(self._desired_markets)
        if not desired and self._orderbook_sid is None:
            return
        if self._orderbook_sid is None:
            if desired:
                await self._send(
                    ws,
                    {
                        "cmd": "subscribe",
                        "params": {"channels": ["orderbook_delta"], "market_tickers": sorted(desired)},
                    },
                )
                self._subscribed_markets.update(desired)
            return
        if desired != self._subscribed_markets:
            raise ExpectedWsReconnect("market subscription set changed")

    def _seq_status(self, sid: int | None, seq: int | None) -> str:
        if not isinstance(sid, int) or not isinstance(seq, int):
            return "ok"
        prev_seq = self._last_seq_by_sid.get(sid)
        if prev_seq is None:
            self._last_seq_by_sid[sid] = seq
            return "ok"
        if seq <= prev_seq:
            return "stale"
        if seq > prev_seq + 1:
            return "gap"
        self._last_seq_by_sid[sid] = seq
        return "ok"

    def _top_changed(self, ticker: str, quote: BookQuote) -> bool:
        top = (
            quote.yes_bid,
            quote.yes_bid_qty,
            quote.yes_ask,
            quote.yes_ask_qty,
            quote.no_bid,
            quote.no_bid_qty,
            quote.no_ask,
            quote.no_ask_qty,
        )
        previous = self._last_top_by_ticker.get(ticker)
        if previous == top:
            return False
        self._last_top_by_ticker[ticker] = top
        return True

    async def _handle_message(self, ws, raw: str) -> None:
        received_at_ns = utc_now_ns()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.recorder.record(
                "ws_control",
                {
                    "received_at_ns": received_at_ns,
                    "received_at_utc": ns_to_utc_iso(received_at_ns),
                    "source": "kalshi",
                    "message_type": "decode_error",
                    "sid": None,
                    "seq": None,
                    "payload_json": raw[:1000],
                },
            )
            return
        msg_type = data.get("type")
        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
        sid = data.get("sid")
        seq = data.get("seq")
        if msg_type in {"subscribed", "ok", "unsubscribed", "error"}:
            self._handle_control_message(data, received_at_ns)
            return
        seq_status = self._seq_status(sid, seq)
        if seq_status == "stale":
            log.warning("Kalshi WS stale/out-of-order message ignored sid=%s seq=%s", sid, seq)
            self.recorder.record(
                "ws_control",
                {
                    "received_at_ns": received_at_ns,
                    "received_at_utc": ns_to_utc_iso(received_at_ns),
                    "source": "kalshi",
                    "message_type": "stale_seq_ignored",
                    "sid": sid,
                    "seq": seq,
                    "payload_json": json.dumps(data, sort_keys=True),
                },
            )
            return
        if seq_status == "gap":
            prev_seq = self._last_seq_by_sid.get(sid)
            log.error("Kalshi WS seq gap sid=%s prev=%s new=%s; invalidating books and reconnecting", sid, prev_seq, seq)
            self.state.reset_orderbooks()
            self.state.mark_kalshi_connected(False)
            self._last_top_by_ticker.clear()
            self.recorder.record(
                "ws_control",
                {
                    "received_at_ns": received_at_ns,
                    "received_at_utc": ns_to_utc_iso(received_at_ns),
                    "source": "kalshi",
                    "message_type": "seq_gap_reconnect",
                    "sid": sid,
                    "seq": seq,
                    "payload_json": json.dumps(data, sort_keys=True),
                },
            )
            raise RuntimeError(f"Kalshi websocket sequence gap sid={sid} prev={prev_seq} seq={seq}")
        if msg_type == "orderbook_snapshot":
            self._handle_orderbook_snapshot(sid, seq, msg, received_at_ns)
        elif msg_type == "orderbook_delta":
            self._handle_orderbook_delta(sid, seq, msg, received_at_ns)
        elif msg_type in {"market_lifecycle_v2", "event_lifecycle"}:
            self._handle_lifecycle(msg_type, msg, received_at_ns)
        elif msg_type in {"fill", "user_order", "market_position"}:
            self._handle_private(msg_type, msg, received_at_ns)
        else:
            self.recorder.record(
                "ws_control",
                {
                    "received_at_ns": received_at_ns,
                    "received_at_utc": ns_to_utc_iso(received_at_ns),
                    "source": "kalshi",
                    "message_type": str(msg_type),
                    "sid": sid,
                    "seq": seq,
                    "payload_json": json.dumps(data, sort_keys=True),
                },
            )

    def _handle_control_message(self, data: dict, received_at_ns: int) -> None:
        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
        channel = msg.get("channel")
        sid = data.get("sid") or msg.get("sid")
        if data.get("type") == "subscribed" and channel == "orderbook_delta" and sid is not None:
            self._orderbook_sid = int(sid)
            log.info("Kalshi WS subscribed orderbook_delta sid=%s markets=%d", self._orderbook_sid, len(self._subscribed_markets))
        if data.get("type") == "error":
            log.error("Kalshi WS error: %s", data)
        self.recorder.record(
            "ws_control",
            {
                "received_at_ns": received_at_ns,
                "received_at_utc": ns_to_utc_iso(received_at_ns),
                "source": "kalshi",
                "message_type": str(data.get("type")),
                "sid": sid,
                "seq": data.get("seq"),
                "payload_json": json.dumps(data, sort_keys=True),
            },
        )

    def _handle_orderbook_snapshot(self, sid: int | None, seq: int | None, msg: dict, received_at_ns: int) -> None:
        ticker = str(msg.get("market_ticker") or "").upper()
        if ticker not in self.state.desired_market_tickers():
            return
        if self.recorder.capture_raw_ws:
            for side in ("yes", "no"):
                for price, qty in parse_ws_levels(msg, side):
                    self.recorder.record(
                        "ws_orderbook_snapshot_level",
                        {
                            "received_at_ns": received_at_ns,
                            "received_at_utc": ns_to_utc_iso(received_at_ns),
                            "market_ticker": ticker,
                            "sid": sid,
                            "seq": seq,
                            "side": side,
                            "price": price,
                            "qty": qty,
                        },
                    )
        quote = self.state.apply_orderbook_snapshot(sid, seq, msg, received_at_ns)
        if quote:
            self._top_changed(ticker, quote)
            self.recorder.record("ws_orderbook_top", self.state.top_row(ticker, quote, sid, seq, received_at_ns, "snapshot"))
            safe_put_update(self.update_queue, {"kind": "orderbook", "market_ticker": ticker, "source": "snapshot"})

    def _handle_orderbook_delta(self, sid: int | None, seq: int | None, msg: dict, received_at_ns: int) -> None:
        ticker = str(msg.get("market_ticker") or "").upper()
        if ticker not in self.state.desired_market_tickers():
            return
        if self.recorder.capture_raw_ws:
            self.recorder.record(
                "ws_orderbook_delta",
                {
                    "received_at_ns": received_at_ns,
                    "received_at_utc": ns_to_utc_iso(received_at_ns),
                    "market_ticker": ticker,
                    "sid": sid,
                    "seq": seq,
                    "side": str(msg.get("side") or "").lower(),
                    "price": normalize_price(msg.get("price_dollars") or msg.get("price")),
                    "delta_qty": optional_float(msg.get("delta_fp") or msg.get("delta")),
                    "exchange_ts": msg.get("ts") or msg.get("time"),
                    "client_order_id": msg.get("client_order_id"),
                },
            )
        quote = self.state.apply_orderbook_delta(sid, seq, msg, received_at_ns)
        if quote and self._top_changed(ticker, quote):
            self.recorder.record("ws_orderbook_top", self.state.top_row(ticker, quote, sid, seq, received_at_ns, "delta"))
            safe_put_update(self.update_queue, {"kind": "orderbook", "market_ticker": ticker, "source": "delta"})

    def _handle_lifecycle(self, msg_type: str, msg: dict, received_at_ns: int) -> None:
        market_ticker = str(msg.get("market_ticker") or "").upper() or None
        meta = msg.get("additional_metadata") if isinstance(msg.get("additional_metadata"), dict) else {}
        event_ticker = (
            msg.get("event_ticker")
            or meta.get("event_ticker")
            or event_from_market_ticker(market_ticker)
        )
        event_ticker = str(event_ticker or "").upper() or None
        self.recorder.record(
            "ws_lifecycle",
            {
                "received_at_ns": received_at_ns,
                "received_at_utc": ns_to_utc_iso(received_at_ns),
                "message_type": msg_type,
                "event_type": msg.get("event_type"),
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "open_ts": msg.get("open_ts"),
                "close_ts": msg.get("close_ts"),
                "payload_json": json.dumps(msg, sort_keys=True),
            },
        )
        if event_ticker and event_ticker.startswith(RESEARCH_SERIES):
            safe_put_update(
                self.update_queue,
                {"kind": "lifecycle", "event_ticker": event_ticker, "event_type": msg.get("event_type")},
            )

    def _handle_private(self, msg_type: str, msg: dict, received_at_ns: int) -> None:
        market_ticker = str(msg.get("market_ticker") or "").upper() or None
        self.recorder.record(
            "ws_private_event",
            {
                "received_at_ns": received_at_ns,
                "received_at_utc": ns_to_utc_iso(received_at_ns),
                "message_type": msg_type,
                "market_ticker": market_ticker,
                "event_ticker": event_from_market_ticker(market_ticker),
                "payload_json": json.dumps(msg, sort_keys=True),
            },
        )
        safe_put_update(self.update_queue, {"kind": "private", "message_type": msg_type, "market_ticker": market_ticker})


class KrakenWsSpot:
    def __init__(self, state: LiveMarketState, recorder: LiveCaptureWriter, update_queue: queue.Queue) -> None:
        self.state = state
        self.recorder = recorder
        self.update_queue = update_queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="kraken-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self.state.mark_spot_feed_connected(False)
            try:
                await self._run_connection()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._stop.is_set():
                    log.exception("Kraken websocket connection failed")
                    await asyncio.sleep(backoff)
                    backoff = min(30.0, backoff * 2.0)

    async def _run_connection(self) -> None:
        ws = await connect_websocket(KRAKEN_WS_URL)
        log.info("Kraken websocket connected: %s", KRAKEN_WS_URL)
        self.state.mark_spot_feed_connected(True)
        try:
            await ws.send(
                json.dumps(
                    {
                        "event": "subscribe",
                        "pair": ["XBT/USD"],
                        "subscription": {"name": "ticker"},
                    }
                )
            )
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                self._handle_message(raw)
        finally:
            self.state.mark_spot_feed_connected(False)
            await ws.close()
            log.info("Kraken websocket disconnected")

    def _handle_message(self, raw: str) -> None:
        received_at_ns = utc_now_ns()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(data, dict):
            event_type = data.get("event")
            if event_type in {"systemStatus", "heartbeat"}:
                return
            if event_type == "subscriptionStatus":
                self.recorder.record(
                    "ws_control",
                    {
                        "received_at_ns": received_at_ns,
                        "received_at_utc": ns_to_utc_iso(received_at_ns),
                        "source": "kraken",
                        "message_type": f"subscription_{data.get('status')}",
                        "sid": data.get("channelID"),
                        "seq": None,
                        "payload_json": json.dumps(data, sort_keys=True),
                    },
                )
                return
            return

        if not isinstance(data, list) or len(data) < 4 or data[-2] != "ticker" or data[-1] != "XBT/USD":
            return

        ticker = data[1] if isinstance(data[1], dict) else {}
        best_ask = optional_float((ticker.get("a") or [None])[0])
        best_bid = optional_float((ticker.get("b") or [None])[0])
        last_trade = optional_float((ticker.get("c") or [None])[0])
        if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0:
            price = (best_bid + best_ask) / 2.0
        else:
            price = last_trade
        if price is None or price <= 0:
            return
        self.state.set_btc_spot(price, received_at_ns)
        self.recorder.record(
            "coinbase_ticker",
            {
                "received_at_ns": received_at_ns,
                "received_at_utc": ns_to_utc_iso(received_at_ns),
                "product_id": "KRAKEN:XBT/USD",
                "price": price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "sequence": data[0],
                "exchange_time": None,
            },
        )
        safe_put_update(self.update_queue, {"kind": "btc_spot", "source": "kraken"})


def order_from_response(response: dict) -> dict:
    if not isinstance(response, dict):
        return {}
    order = response.get("order")
    return order if isinstance(order, dict) else response


def utc_dt(value) -> datetime | None:
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


def parse_event_close_from_ticker(event_ticker: str) -> datetime | None:
    match = EVENT_TICKER_RE.match(str(event_ticker or "").upper())
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
    local_close = datetime(
        2000 + int(match.group("yy")),
        month,
        int(match.group("day")),
        int(match.group("hour")),
        tzinfo=NY_TZ,
    )
    return local_close.astimezone(timezone.utc)


def close_times_match(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return abs((left - right).total_seconds()) <= RESEARCH_EVENT_CLOSE_TOLERANCE_SEC


def event_close_from_markets(markets: list[dict]) -> datetime | None:
    closes = [utc_dt(m.get("close_time")) for m in markets if m.get("close_time")]
    closes = [dt for dt in closes if dt is not None]
    return min(closes) if closes else None


def market_is_research_cumulative(event_ticker: str, market: dict, event_close: datetime) -> bool:
    ticker = str(market.get("ticker") or "").upper()
    if not ticker.startswith(f"{event_ticker}-T"):
        return False
    if market.get("event_ticker") and str(market.get("event_ticker")).upper() != event_ticker:
        return False
    if str(market.get("status") or "").lower() not in {"open", "active"}:
        return False
    market_close = utc_dt(market.get("close_time"))
    if not close_times_match(market_close, event_close):
        return False
    parsed = base_strategy.parse_market(market)
    return optional_float(parsed.get("floor")) is not None


def build_research_event(event_ticker: str, markets: list[dict], now: datetime) -> dict | None:
    event_ticker = str(event_ticker or "").upper()
    expected_close = parse_event_close_from_ticker(event_ticker)
    actual_close = event_close_from_markets(markets)
    if not close_times_match(expected_close, actual_close):
        return None
    ttl_min = (actual_close - now).total_seconds() / 60.0
    if ttl_min <= CFG["min_ttl_min"] or ttl_min > RESEARCH_MAX_TTL_MIN:
        return None
    filtered = [
        market
        for market in markets
        if market_is_research_cumulative(event_ticker, market, actual_close)
    ]
    if not filtered:
        return None
    return {
        "event_ticker": event_ticker,
        "title": filtered[0].get("title", ""),
        "close_time": actual_close,
        "expected_close_time": expected_close,
        "ttl_hours": ttl_min / 60.0,
        "markets": filtered,
    }


def scan_research_events(kalshi: KalshiApi, now: datetime | None = None, use_cache: bool = True) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    markets = kalshi.get_markets(series_ticker=RESEARCH_SERIES, status="open", use_cache=use_cache).get("markets", [])
    by_event: dict[str, list[dict]] = {}
    for market in markets:
        event_ticker = str(market.get("event_ticker") or "").upper()
        if EVENT_TICKER_RE.match(event_ticker):
            by_event.setdefault(event_ticker, []).append(market)
    events = []
    for event_ticker, event_markets in by_event.items():
        event = build_research_event(event_ticker, event_markets, now)
        if event is not None:
            events.append(event)
    return sorted(events, key=lambda event: event["ttl_hours"])


def build_event_emp_cache(btc_1m, event: dict, verbose: bool = False) -> dict:
    close_time = utc_dt(event.get("close_time"))
    if close_time is None:
        return {}
    event_open = close_time - timedelta(minutes=60)
    train_start = event_open - timedelta(days=RESEARCH_TRAIN_DAYS)
    train = btc_1m[(btc_1m["time"] >= train_start) & (btc_1m["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        log.warning(
            "skip %s: only %d BTC train rows for %s -> %s; need >=%d",
            event.get("event_ticker"),
            len(train),
            train_start.isoformat(),
            event_open.isoformat(),
            min_rows,
        )
        return {}
    return base_strategy.build_live_cache(train, verbose=verbose)


def trim_btc_history(btc_1m, days_back: int):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back + 1)
    return btc_1m[btc_1m["time"] >= cutoff].sort_values("time").reset_index(drop=True)


def normalize_btc_candles(btc_1m):
    if btc_1m is None or len(btc_1m) == 0:
        return pd.DataFrame(columns=["time", "low", "high", "open", "close", "volume", "log_ret", "rv_15m", "rv_60m", "rv_1d", "rkurt_60m"])
    required = {"time", "low", "high", "open", "close", "volume"}
    if not required.issubset(set(btc_1m.columns)):
        missing = sorted(required - set(btc_1m.columns))
        raise ValueError(f"BTC candle dataframe missing columns: {missing}")
    df = btc_1m.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for column in ["low", "high", "open", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["time", "low", "high", "open", "close"])
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    if df.empty:
        return df

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


def write_btc_cache(btc_1m) -> None:
    try:
        BTC_LIVE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        trim_btc_history(normalize_btc_candles(btc_1m), RESEARCH_BOOTSTRAP_BTC_DAYS).to_parquet(BTC_LIVE_CACHE_PATH, index=False)
    except Exception as exc:
        log.warning("could not write BTC live cache: %s", exc)


def fetch_kraken_btc_candles(start: datetime, end: datetime):
    if BTC_CANDLE_FALLBACK_SOURCE not in {"kraken", "on", "true", "1", "yes"}:
        raise RuntimeError("Kraken BTC candle fallback disabled")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if end <= start:
        return pd.DataFrame()

    rows: list[dict[str, float | datetime]] = []
    session = requests.Session()
    since = max(0, int(start.timestamp()) - 60)
    end_ts = int(end.timestamp())
    for _ in range(60):
        response = session.get(
            KRAKEN_OHLC_URL,
            params={"pair": "XBTUSD", "interval": 1, "since": since},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("error") or []
        if errors:
            raise RuntimeError(f"Kraken OHLC error: {errors}")
        result = payload.get("result") or {}
        pair_key = next((key for key in result.keys() if key != "last"), None)
        candles = result.get(pair_key, []) if pair_key else []
        if not candles:
            break
        last_ts = since
        for candle in candles:
            if len(candle) < 7:
                continue
            ts = int(float(candle[0]))
            last_ts = max(last_ts, ts)
            rows.append(
                {
                    "time": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[6]),
                }
            )
        if last_ts >= end_ts - 60:
            break
        next_since = last_ts + 60
        if next_since <= since:
            break
        since = next_since
        time.sleep(0.20)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = normalize_btc_candles(df)
    return df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)


def fetch_btc_candles_with_fallback(start: datetime, end: datetime):
    errors: list[str] = []
    try:
        coinbase = base_strategy.fetch_coinbase_candles(start=start, end=end)
        coinbase = normalize_btc_candles(coinbase)
        if not coinbase.empty:
            return coinbase, "coinbase_exchange"
        errors.append("coinbase_exchange returned no rows")
    except Exception as exc:
        errors.append(f"coinbase_exchange: {exc}")

    try:
        fallback = fetch_kraken_btc_candles(start, end)
        fallback = normalize_btc_candles(fallback)
        if not fallback.empty:
            log.warning(
                "using Kraken BTC candle fallback for %s -> %s rows=%d; Coinbase error=%s",
                start.astimezone(timezone.utc).isoformat(),
                end.astimezone(timezone.utc).isoformat(),
                len(fallback),
                "; ".join(errors)[-500:],
            )
            return fallback, "kraken"
        errors.append("kraken returned no rows")
    except Exception as exc:
        errors.append(f"kraken: {exc}")
    raise RuntimeError("all BTC candle sources failed: " + "; ".join(errors))


def fetch_btc_history_with_fallback(days_back: int):
    now = datetime.now(timezone.utc)
    try:
        coinbase = base_strategy.fetch_historical_minutes(days_back=days_back)
        coinbase = normalize_btc_candles(coinbase)
        if not coinbase.empty and btc_candles_are_fresh(coinbase, now=now):
            return trim_btc_history(coinbase, days_back)
        log.warning("Coinbase BTC history unavailable/stale: %s", describe_btc_candle_freshness(coinbase))
    except Exception as exc:
        log.warning("Coinbase BTC history failed; using fallback: %s", exc)

    start = now - timedelta(days=days_back)
    fallback = fetch_kraken_btc_candles(start, now)
    fallback = normalize_btc_candles(fallback)
    if fallback.empty or not btc_candles_are_fresh(fallback, now=now):
        raise StaleBtcCandleData(f"fallback BTC history is stale/unusable: {describe_btc_candle_freshness(fallback)}")
    log.warning(
        "using Kraken BTC history fallback for %s -> %s rows=%d",
        start.isoformat(),
        now.isoformat(),
        len(fallback),
    )
    return trim_btc_history(fallback, days_back)


def load_btc_history_cached(days_back: int):
    now = datetime.now(timezone.utc)
    required_start = now - timedelta(days=days_back)
    if BTC_LIVE_CACHE_PATH.exists():
        try:
            cached = pd.read_parquet(BTC_LIVE_CACHE_PATH)
            cached = normalize_btc_candles(cached)
            if not cached.empty:
                first = cached["time"].iloc[0].to_pydatetime()
                last = cached["time"].iloc[-1].to_pydatetime()
                if first <= required_start and last >= now - timedelta(minutes=20):
                    log.info(
                        "using BTC live cache: %s -> %s (%d rows)",
                        first.isoformat(),
                        last.isoformat(),
                        len(cached),
                    )
                    return trim_btc_history(cached, days_back)
                if first <= required_start:
                    log.info(
                        "BTC live cache stale: %s -> %s; refreshing missing gap",
                        first.isoformat(),
                        last.isoformat(),
                    )
                    try:
                        refreshed = refresh_btc_data_cached(cached, require_fresh=True)
                        return trim_btc_history(refreshed, days_back)
                    except Exception as exc:
                        log.warning("BTC cache gap refresh failed; rebuilding: %s", exc)
                log.info(
                    "BTC live cache stale/short: %s -> %s; rebuilding",
                    first.isoformat(),
                    last.isoformat(),
                )
        except Exception as exc:
            log.warning("could not read BTC live cache; rebuilding: %s", exc)

    btc_1m = fetch_btc_history_with_fallback(days_back)
    write_btc_cache(btc_1m)
    return btc_1m


def btc_last_candle_time(btc_1m) -> datetime | None:
    if btc_1m is None or len(btc_1m) == 0 or "time" not in btc_1m.columns:
        return None
    try:
        times = pd.to_datetime(btc_1m["time"], utc=True, errors="coerce").dropna()
    except Exception:
        return None
    if times.empty:
        return None
    return times.max().to_pydatetime()


def btc_candle_age_sec(btc_1m, now: datetime | None = None) -> float | None:
    last = btc_last_candle_time(btc_1m)
    if last is None:
        return None
    now = now or datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last.astimezone(timezone.utc)).total_seconds()


def btc_candles_are_fresh(btc_1m, now: datetime | None = None) -> bool:
    age = btc_candle_age_sec(btc_1m, now=now)
    return age is not None and age <= BTC_CANDLE_MAX_AGE_SEC


def describe_btc_candle_freshness(btc_1m) -> str:
    last = btc_last_candle_time(btc_1m)
    age = btc_candle_age_sec(btc_1m)
    if last is None or age is None:
        return "last_candle=none"
    return f"last_candle={last.astimezone(timezone.utc).isoformat()} age={age:.1f}s max_age={BTC_CANDLE_MAX_AGE_SEC:.1f}s"


def refresh_btc_data_cached(btc_1m, require_fresh: bool = False):
    now = datetime.now(timezone.utc)
    before_last = btc_last_candle_time(btc_1m)
    if before_last is None:
        start = now - timedelta(minutes=BTC_CANDLE_REFRESH_LOOKBACK_MIN)
    else:
        start = min(now - timedelta(minutes=BTC_CANDLE_REFRESH_LOOKBACK_MIN), before_last.astimezone(timezone.utc) - timedelta(minutes=1))
    current_min = now.replace(second=0, microsecond=0)
    try:
        fresh, source = fetch_btc_candles_with_fallback(start, now)
        fresh = fresh[fresh["time"] < current_min].copy()
        if fresh.empty:
            refreshed = normalize_btc_candles(btc_1m)
            log.warning("BTC candle refresh source=%s returned no completed minutes; %s", source, describe_btc_candle_freshness(refreshed))
        else:
            refreshed = normalize_btc_candles(pd.concat([normalize_btc_candles(btc_1m), fresh], ignore_index=True))
            log.info(
                "BTC candle refresh source=%s rows=%d %s",
                source,
                len(fresh),
                describe_btc_candle_freshness(refreshed),
            )
    except Exception as exc:
        refreshed = normalize_btc_candles(btc_1m)
        if require_fresh:
            raise StaleBtcCandleData(f"BTC candle refresh failed; refusing to trade: {exc}") from exc
        log.warning("BTC candle refresh failed; keeping prior data: %s; %s", exc, describe_btc_candle_freshness(refreshed))
    after_last = btc_last_candle_time(refreshed)
    if after_last is None:
        raise StaleBtcCandleData("BTC candle refresh returned no usable candles")
    if before_last is not None and after_last <= before_last and not btc_candles_are_fresh(refreshed):
        log.warning("BTC candle refresh did not advance stale data: %s", describe_btc_candle_freshness(refreshed))
    if require_fresh and not btc_candles_are_fresh(refreshed):
        raise StaleBtcCandleData(f"BTC candle data is stale; refusing to trade: {describe_btc_candle_freshness(refreshed)}")
    write_btc_cache(refreshed)
    return refreshed


def lognormal_p_above(spot: float, strike: float, ttl_min: float, annual_vol: float | None) -> float:
    if spot <= 0 or not annual_vol or not math.isfinite(annual_vol) or annual_vol <= 0:
        return float("nan")
    variance = annual_vol * annual_vol * ttl_min / MINUTES_PER_YEAR
    if variance <= 0:
        return 1.0 if spot >= strike else 0.0
    sigma = math.sqrt(variance)
    z = (math.log(strike / spot) + 0.5 * variance) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def blended_p_above(
    spot: float,
    strike: float,
    ttl_min: float,
    samples: dict,
    current_vol: float | None,
) -> float:
    p_emp = base_strategy.emp_p_above(
        spot,
        strike,
        ttl_min,
        samples,
        current_vol=current_vol,
        brti_dampening=BRTI_DAMPENING,
    )
    p_norm = lognormal_p_above(spot, strike, ttl_min, current_vol)
    if math.isfinite(p_norm):
        return 0.70 * p_emp + 0.30 * p_norm
    return p_emp


def edge_uncertainty_cents(model_p: float, emp_cache: dict) -> float:
    sample_counts = np.asarray([s.get("n", 0) for s in emp_cache.values()], dtype=float)
    n_eff = max(200.0, float(np.nanmedian(sample_counts)) * 0.30) if len(sample_counts) else 200.0
    return 100.0 * 1.64 * math.sqrt(max(0.0, min(0.25, model_p * (1.0 - model_p))) / n_eff)


def latest_rv60(btc_1m) -> float | None:
    if "rv_60m" not in btc_1m.columns:
        return None
    values = btc_1m["rv_60m"].dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def model_probability(event: dict, market: dict, btc_1m, emp_cache: dict, spot: float) -> tuple[float, float]:
    parsed = base_strategy.parse_market(market)
    ttl_min = float(event["ttl_hours"]) * 60.0
    current_vol = latest_rv60(btc_1m)
    horizons = sorted(emp_cache.keys())
    if not horizons:
        return float("nan"), ttl_min
    horizon = min(horizons, key=lambda h: abs(h - ttl_min))
    samples = emp_cache[horizon]
    ticker = parsed.get("ticker") or ""
    floor = optional_float(parsed.get("floor"))
    cap = optional_float(parsed.get("cap"))
    if "-T" in ticker and floor is not None:
        p = blended_p_above(spot, floor, ttl_min, samples, current_vol)
    else:
        p = float("nan")
    return p, ttl_min


def signal_from_book(
    event: dict,
    market: dict,
    quote: BookQuote,
    btc_1m,
    emp_cache: dict,
    spot: float,
    contracts: int,
    min_edge_cents: float,
    max_spread_cents: float,
    now: datetime | None = None,
) -> TradeSignal | None:
    parsed = base_strategy.parse_market(market)
    ticker = str(parsed.get("ticker") or "").upper()
    event_ticker = str(event.get("event_ticker") or "").upper()
    if not ticker or str(market.get("status") or "").lower() not in {"open", "active"}:
        return None
    event_close = utc_dt(event.get("close_time"))
    if event_close is None or not market_is_research_cumulative(event_ticker, market, event_close):
        return None
    p_yes, ttl_min = model_probability(event, market, btc_1m, emp_cache, spot)
    if not math.isfinite(p_yes):
        return None
    floor = optional_float(parsed.get("floor"))
    signal_strategy = SIGNAL_STRATEGY
    if signal_strategy in {"js_guarded", "market_shrink_no_cautious"}:
        if floor is None or spot <= 0:
            return None
        if quote.yes_bid is None or quote.yes_ask is None:
            return None
        if signal_strategy == "js_guarded":
            now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            if now_utc.hour in JS_GUARDED_EXCLUDED_UTC_HOURS:
                return None
            moneyness_bps = 10000.0 * (spot - floor) / max(1.0, spot)
            if abs(moneyness_bps) > JS_GUARDED_MAX_ABS_MONEYNESS_BPS:
                return None
        market_mid = 0.5 * (quote.yes_bid + quote.yes_ask)
        shrink = JS_MARKET_SHRINK if signal_strategy == "js_guarded" else MARKET_SHRINK_NO_CAUTIOUS_MARKET_SHRINK
        p_yes = (1.0 - shrink) * p_yes + shrink * market_mid

    candidates = []
    if quote.yes_ask is not None and quote.yes_spread_cents is not None:
        candidates.append(
            {
                "side": "yes",
                "entry_price": quote.yes_ask,
                "yes_order_side": "bid",
                "yes_limit_price": quote.yes_ask,
                "edge": p_yes - quote.yes_ask,
                "spread": quote.yes_spread_cents,
                "available_qty": quote.yes_ask_qty,
            }
        )
    if quote.no_ask is not None and quote.no_spread_cents is not None:
        candidates.append(
            {
                "side": "no",
                "entry_price": quote.no_ask,
                "yes_order_side": "ask",
                "yes_limit_price": 1.0 - quote.no_ask,
                "edge": (1.0 - p_yes) - quote.no_ask,
                "spread": quote.no_spread_cents,
                "available_qty": quote.no_ask_qty,
            }
        )
    if not candidates:
        return None

    best = max(candidates, key=lambda item: item["edge"])
    side = best["side"]
    entry_price = float(best["entry_price"])
    edge_gross_cents = float(best["edge"]) * 100.0
    entry_fee = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
    net_edge_cents = edge_gross_cents - entry_fee * 100.0
    if signal_strategy == "js_guarded":
        threshold = JS_MIN_EDGE_CENTS + edge_uncertainty_cents(p_yes, emp_cache)
        strong_prob = True
        max_spread = JS_MAX_SPREAD_CENTS
        min_entry = JS_MIN_ENTRY
        max_entry = JS_MAX_ENTRY
    elif signal_strategy == "market_shrink_no_cautious":
        threshold = MARKET_SHRINK_NO_CAUTIOUS_MIN_EDGE_CENTS + edge_uncertainty_cents(p_yes, emp_cache)
        if side == "no":
            threshold += MARKET_SHRINK_NO_CAUTIOUS_NO_EDGE_ADD_CENTS
        strong_prob = (side == "yes" and p_yes >= RESEARCH_MIN_YES_P) or (
            side == "no" and p_yes <= MARKET_SHRINK_NO_CAUTIOUS_MAX_NO_P
        )
        max_spread = max_spread_cents
        min_entry = RESEARCH_MIN_ENTRY
        max_entry = RESEARCH_MAX_ENTRY
    else:
        threshold = min_edge_cents + edge_uncertainty_cents(p_yes, emp_cache)
        strong_prob = (side == "yes" and p_yes >= RESEARCH_MIN_YES_P) or (
            side == "no" and p_yes <= RESEARCH_MAX_NO_P
        )
        max_spread = max_spread_cents
        min_entry = RESEARCH_MIN_ENTRY
        max_entry = RESEARCH_MAX_ENTRY
    if not strong_prob:
        return None
    if net_edge_cents < threshold:
        return None
    if best["spread"] > max_spread + FLOAT_EPSILON:
        return None
    if entry_price < min_entry - FLOAT_EPSILON or entry_price > max_entry + FLOAT_EPSILON:
        return None
    if best["available_qty"] < contracts:
        return None

    close_time = market.get("close_time") or event.get("close_time")
    return TradeSignal(
        event_ticker=event_ticker,
        market_ticker=ticker,
        side=side,
        contracts=contracts,
        entry_price=entry_price,
        yes_order_side=best["yes_order_side"],
        yes_limit_price=float(best["yes_limit_price"]),
        available_qty=float(best["available_qty"]),
        model_p_yes=float(p_yes),
        edge_gross_cents=edge_gross_cents,
        entry_fee=entry_fee,
        net_edge_cents=net_edge_cents,
        edge_threshold_cents=threshold,
        spread_cents=float(best["spread"]),
        strike=float(floor or 0.0),
        btc_spot=float(spot),
        ttl_min=float(ttl_min),
        close_time=str(close_time),
        yes_bid=quote.yes_bid,
        yes_ask=quote.yes_ask,
        no_bid=quote.no_bid,
        no_ask=quote.no_ask,
    )


def db_connect(path: Path | str) -> sqlite3.Connection:
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_live_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            event_ticker TEXT NOT NULL,
            market_ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            yes_order_side TEXT NOT NULL,
            yes_limit_price REAL NOT NULL,
            entry_fee_estimate REAL NOT NULL,
            model_p_yes REAL NOT NULL,
            edge_gross_cents REAL NOT NULL,
            net_edge_cents REAL NOT NULL,
            spread_cents REAL NOT NULL,
            btc_spot REAL NOT NULL,
            close_time TEXT,
            client_order_id TEXT,
            order_id TEXT,
            fill_count REAL,
            average_yes_fill_price REAL,
            actual_entry_price REAL,
            actual_fee_paid REAL,
            raw_response TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_event_locks (
            event_ticker TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            market_ticker TEXT,
            client_order_id TEXT
        )
        """
    )
    conn.commit()
    return conn


def db_active_tickers(conn: sqlite3.Connection, now: datetime) -> set[str]:
    placeholders = ",".join("?" for _ in ACTIVE_TRADE_STATUSES)
    rows = conn.execute(
        f"""
        SELECT market_ticker
        FROM research_live_trades
        WHERE status IN ({placeholders})
          AND (close_time IS NULL OR close_time = '' OR close_time > ?)
        """,
        (*ACTIVE_TRADE_STATUSES, now.isoformat()),
    ).fetchall()
    return {row["market_ticker"] for row in rows}


def event_from_market_ticker(market_ticker: str | None) -> str | None:
    if not market_ticker:
        return None
    text = str(market_ticker).upper()
    for sep in ("-T", "-B"):
        if sep in text:
            return text.split(sep, 1)[0]
    return None


def market_strike_from_ticker(market_ticker: str | None) -> float | None:
    if not market_ticker:
        return None
    text = str(market_ticker).upper()
    match = re.search(r"-(?:T|B)([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return optional_float(match.group(1))


def db_active_events(conn: sqlite3.Connection, now: datetime) -> set[str]:
    placeholders = ",".join("?" for _ in ACTIVE_TRADE_STATUSES)
    rows = conn.execute(
        f"""
        SELECT event_ticker, market_ticker
        FROM research_live_trades
        WHERE status IN ({placeholders})
          AND (close_time IS NULL OR close_time = '' OR close_time > ?)
        """,
        (*ACTIVE_TRADE_STATUSES, now.isoformat()),
    ).fetchall()
    events: set[str] = set()
    for row in rows:
        event_ticker = row["event_ticker"] or event_from_market_ticker(row["market_ticker"])
        if event_ticker:
            events.add(str(event_ticker).upper())
    lock_rows = conn.execute(
        """
        SELECT event_ticker
        FROM research_event_locks
        WHERE expires_at > ?
        """,
        (now.isoformat(),),
    ).fetchall()
    for row in lock_rows:
        event_ticker = row["event_ticker"]
        if event_ticker:
            events.add(str(event_ticker).upper())
    return events


def acquire_event_lock(
    conn: sqlite3.Connection,
    signal: TradeSignal,
    mode: str,
    client_order_id: str | None = None,
) -> bool:
    now = datetime.now(timezone.utc)
    close_time = utc_dt(signal.close_time) or (now + timedelta(hours=1))
    expires_at = max(close_time + timedelta(minutes=5), now + timedelta(minutes=10))
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO research_event_locks (
            event_ticker, created_at, expires_at, mode, market_ticker, client_order_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            signal.event_ticker.upper(),
            now.isoformat(),
            expires_at.isoformat(),
            mode,
            signal.market_ticker,
            client_order_id,
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def release_event_lock(conn: sqlite3.Connection, event_ticker: str, client_order_id: str | None = None) -> None:
    event_ticker = str(event_ticker or "").upper()
    if not event_ticker:
        return
    if client_order_id:
        conn.execute(
            """
            DELETE FROM research_event_locks
            WHERE event_ticker = ?
              AND (client_order_id IS NULL OR client_order_id = ?)
            """,
            (event_ticker, client_order_id),
        )
    else:
        conn.execute("DELETE FROM research_event_locks WHERE event_ticker = ?", (event_ticker,))
    conn.commit()


def parse_fill_details(signal: TradeSignal, response: dict) -> tuple[float | None, float | None, float | None, float | None, str | None]:
    order = order_from_response(response or {})
    fill_count = optional_float(order.get("fill_count_fp"))
    if fill_count is None:
        fill_count = optional_float(order.get("fill_count"))
    avg_yes = optional_float(order.get("average_fill_price"))
    if avg_yes is None:
        avg_yes = optional_float(order.get("yes_price_dollars"))
    avg_fee = optional_float(order.get("average_fee_paid"))
    total_fee = optional_float(order.get("taker_fees_dollars"))
    actual_entry = None
    actual_fee = None
    if fill_count is not None and fill_count > FLOAT_EPSILON and avg_yes is not None:
        actual_entry = avg_yes if signal.side == "yes" else 1.0 - avg_yes
        actual_fee = total_fee if total_fee is not None else (avg_fee or 0.0) * fill_count
    return fill_count, avg_yes, actual_entry, actual_fee, order.get("order_id")


def local_status_from_kalshi_order(order: dict, expected_contracts: int) -> str:
    fill_count = optional_float(order.get("fill_count_fp")) or optional_float(order.get("fill_count")) or 0.0
    api_status = str(order.get("status") or "").lower()
    if fill_count >= max(1, expected_contracts) - FLOAT_EPSILON or api_status == "executed":
        return "filled"
    if fill_count > FLOAT_EPSILON:
        return "partial_filled"
    if api_status in {"canceled", "cancelled", "expired", "rejected"}:
        return "not_filled"
    return "submitted"


def sync_order_by_client_id(
    client: KalshiApi,
    conn: sqlite3.Connection,
    trade_id: int,
    signal: TradeSignal,
    client_order_id: str,
) -> tuple[str, dict] | None:
    order = client.find_order_by_client_id(client_order_id, ticker=signal.market_ticker, limit=100)
    if not order:
        return None
    status = local_status_from_kalshi_order(order, signal.contracts)
    update_trade_response(conn, trade_id, status, signal, {"order": order})
    if status == "not_filled":
        release_event_lock(conn, signal.event_ticker, client_order_id)
    return status, order


def http_error_text(exc: requests.HTTPError) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        return str(response.text or "")
    except Exception:
        return ""


def is_fok_no_fill_conflict(exc: requests.HTTPError) -> bool:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code != 409:
        return False
    body = http_error_text(exc).lower()
    return "fill_or_kill_insufficient_resting_volume" in body


def record_trade(
    conn: sqlite3.Connection,
    mode: str,
    status: str,
    signal: TradeSignal,
    client_order_id: str | None = None,
    response: dict | None = None,
) -> int:
    response = response or {}
    fill_count, avg_yes, actual_entry, actual_fee, order_id = parse_fill_details(signal, response)
    if status == "paper_filled":
        fill_count = float(signal.contracts)
        actual_entry = signal.entry_price
        actual_fee = signal.entry_fee

    cur = conn.execute(
        """
        INSERT INTO research_live_trades (
            created_at, mode, status, event_ticker, market_ticker, side,
            contracts, entry_price, yes_order_side, yes_limit_price,
            entry_fee_estimate, model_p_yes, edge_gross_cents, net_edge_cents,
            spread_cents, btc_spot, close_time, client_order_id, order_id,
            fill_count, average_yes_fill_price, actual_entry_price,
            actual_fee_paid, raw_response
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            mode,
            status,
            signal.event_ticker,
            signal.market_ticker,
            signal.side,
            signal.contracts,
            signal.entry_price,
            signal.yes_order_side,
            signal.yes_limit_price,
            signal.entry_fee,
            signal.model_p_yes,
            signal.edge_gross_cents,
            signal.net_edge_cents,
            signal.spread_cents,
            signal.btc_spot,
            signal.close_time,
            client_order_id,
            order_id,
            fill_count,
            avg_yes,
            actual_entry,
            actual_fee,
            json.dumps(response, sort_keys=True),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_trade_response(
    conn: sqlite3.Connection,
    trade_id: int,
    status: str,
    signal: TradeSignal,
    response: dict,
) -> None:
    fill_count, avg_yes, actual_entry, actual_fee, order_id = parse_fill_details(signal, response)
    conn.execute(
        """
        UPDATE research_live_trades
        SET status = ?,
            order_id = ?,
            fill_count = ?,
            average_yes_fill_price = ?,
            actual_entry_price = ?,
            actual_fee_paid = ?,
            raw_response = ?
        WHERE id = ?
        """,
        (
            status,
            order_id,
            fill_count,
            avg_yes,
            actual_entry,
            actual_fee,
            json.dumps(response, sort_keys=True),
            trade_id,
        ),
    )
    conn.commit()


def portfolio_position_tickers(client: KalshiApi, strict: bool = False) -> set[str]:
    try:
        data = client.get_positions(count_filter="position", limit=1000)
    except Exception as exc:
        msg = f"could not fetch portfolio positions: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        log.warning(msg)
        return set()
    out = set()
    rows = data.get("market_positions") or data.get("positions") or []
    for row in rows:
        pos = optional_float(row.get("position_fp"))
        if pos is None:
            pos = optional_float(row.get("position"))
        if pos is not None and abs(pos) > 1e-9:
            out.add(row.get("ticker") or row.get("market_ticker"))
    return {ticker for ticker in out if ticker}


def available_balance_dollars(client: KalshiApi, strict: bool = False) -> float | None:
    try:
        data = client.get_balance()
    except Exception as exc:
        msg = f"could not fetch portfolio balance: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        log.warning(msg)
        return None
    dollars = optional_float(data.get("balance_dollars"))
    if dollars is not None:
        return dollars
    cents = optional_float(data.get("balance"))
    if cents is not None:
        return cents / 100.0
    fixed_point = optional_float(data.get("balance_fp"))
    if fixed_point is not None:
        return fixed_point
    if strict:
        raise RuntimeError(f"could not parse portfolio balance response: {data}")
    return None


def portfolio_value_dollars(client: KalshiApi, strict: bool = False) -> float | None:
    try:
        data = client.get_balance()
    except Exception as exc:
        msg = f"could not fetch portfolio balance: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        log.warning(msg)
        return None
    dollars = optional_float(data.get("portfolio_value_dollars"))
    if dollars is not None:
        return dollars
    cents = optional_float(data.get("portfolio_value"))
    if cents is not None:
        return cents / 100.0
    if strict:
        raise RuntimeError(f"could not parse portfolio value response: {data}")
    return None


def position_market_exposure(row: dict) -> float:
    for key in ("market_exposure_dollars", "event_exposure_dollars", "total_cost_dollars"):
        value = optional_float(row.get(key))
        if value is not None:
            return abs(value)
    position = optional_float(row.get("position_fp")) or optional_float(row.get("position")) or 0.0
    return abs(position)


def get_portfolio_snapshot(client: KalshiApi) -> PortfolioSnapshot:
    balance_data = client.get_balance()
    cents_balance = optional_float(balance_data.get("balance"))
    available = optional_float(balance_data.get("balance_dollars"))
    if available is None and cents_balance is not None:
        available = cents_balance / 100.0
    cents_value = optional_float(balance_data.get("portfolio_value"))
    portfolio_value = optional_float(balance_data.get("portfolio_value_dollars"))
    if portfolio_value is None and cents_value is not None:
        portfolio_value = cents_value / 100.0
    if available is None or portfolio_value is None:
        raise RuntimeError(f"could not parse portfolio balance response: {balance_data}")

    positions = client.get_all_positions(count_filter="position")
    active_tickers: set[str] = set()
    exposure = 0.0
    for row in positions.get("market_positions", []):
        ticker = row.get("ticker") or row.get("market_ticker")
        pos = optional_float(row.get("position_fp"))
        if pos is None:
            pos = optional_float(row.get("position"))
        if ticker and pos is not None and abs(pos) > 1e-9:
            active_tickers.add(str(ticker).upper())
            exposure += position_market_exposure(row)
    return PortfolioSnapshot(
        available_balance=float(available),
        portfolio_value=float(max(portfolio_value, available)),
        active_tickers=active_tickers,
        market_exposure=float(exposure),
    )


def estimated_trade_cost(signal: TradeSignal) -> float:
    return signal.contracts * signal.entry_price + signal.entry_fee


def choose_contracts_for_signal(
    signal: TradeSignal,
    portfolio: PortfolioSnapshot | None,
    local_active_exposure: float,
    spent_this_cycle: float,
) -> int:
    max_contracts = min(RESEARCH_MAX_CONTRACTS_PER_TRADE, int(math.floor(signal.available_qty)))
    if max_contracts <= 0:
        return 0
    if SIZING_POLICY == "risk_adjusted":
        if portfolio is None:
            bankroll = float(CFG.get("bankroll", 20.0))
            available = bankroll
            total_exposure = spent_this_cycle
        else:
            bankroll = max(portfolio.portfolio_value, portfolio.available_balance)
            available = portfolio.available_balance
            total_exposure = max(portfolio.market_exposure, local_active_exposure) + spent_this_cycle
        cfg = RiskSizingConfig(
            starting_bankroll=bankroll,
            max_contracts=RESEARCH_MAX_CONTRACTS_PER_TRADE,
            max_per_market_fraction=RESEARCH_MAX_PER_MARKET_FRACTION,
            max_total_exposure_fraction=RESEARCH_MAX_TOTAL_EXPOSURE_FRACTION,
            kelly_fraction=RISK_ADJUSTED_KELLY_FRACTION,
            edge_confidence=RISK_ADJUSTED_EDGE_CONFIDENCE,
            medium_entry_cap=RISK_ADJUSTED_MEDIUM_ENTRY_CAP,
            high_entry_cap=RISK_ADJUSTED_HIGH_ENTRY_CAP,
            no_side_contract_cap=RISK_ADJUSTED_NO_SIDE_CONTRACT_CAP,
        )
        decision = choose_risk_adjusted_contracts(
            entry_price=signal.entry_price,
            model_p_yes=signal.model_p_yes,
            side=signal.side,
            bankroll=bankroll,
            available_cash=available,
            active_exposure=total_exposure,
            config=cfg,
            available_qty=min(signal.available_qty, max_contracts),
        )
        log.info(
            "sizing risk_adjusted %s %s entry=%.4f p_side=%.3f p_cons=%.3f contracts=%d cost=$%.2f "
            "budget=$%.2f full_kelly=%.2f%% applied_kelly=%.2f%% risk_budget=$%.2f reason=%s",
            signal.market_ticker,
            signal.side.upper(),
            signal.entry_price,
            decision.side_probability,
            decision.conservative_probability,
            decision.contracts,
            decision.cost,
            decision.budget,
            100.0 * decision.full_kelly_fraction,
            100.0 * decision.applied_kelly_fraction,
            decision.risk_budget,
            decision.reason,
        )
        return decision.contracts
    if portfolio is None:
        return max_contracts

    bankroll = max(portfolio.portfolio_value, portfolio.available_balance)
    total_exposure = max(portfolio.market_exposure, local_active_exposure) + spent_this_cycle
    budget = min(
        portfolio.available_balance,
        bankroll * RESEARCH_MAX_PER_MARKET_FRACTION,
        bankroll * RESEARCH_MAX_TOTAL_EXPOSURE_FRACTION - total_exposure,
    )
    if budget <= 0:
        return 0

    chosen = 0
    for contracts in range(1, max_contracts + 1):
        fee = kalshi_fee_dollars(signal.entry_price, contracts=contracts, liquidity="taker")
        cost = signal.entry_price * contracts + fee
        if cost <= budget:
            chosen = contracts
        else:
            break
    return chosen


def resize_signal(signal: TradeSignal, contracts: int) -> TradeSignal:
    fee = kalshi_fee_dollars(signal.entry_price, contracts=contracts, liquidity="taker")
    fee_cents_per_contract = (fee / contracts) * 100.0 if contracts > 0 else 0.0
    return replace(
        signal,
        contracts=contracts,
        entry_fee=fee,
        net_edge_cents=signal.edge_gross_cents - fee_cents_per_contract,
    )


def db_active_exposure(conn: sqlite3.Connection, now: datetime) -> float:
    placeholders = ",".join("?" for _ in ACTIVE_TRADE_STATUSES)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(entry_price * contracts + entry_fee_estimate), 0.0) AS exposure
        FROM research_live_trades
        WHERE status IN ({placeholders})
          AND (close_time IS NULL OR close_time = '' OR close_time > ?)
        """,
        (*ACTIVE_TRADE_STATUSES, now.isoformat()),
    ).fetchone()
    return float(row["exposure"] or 0.0)


def btc_close_at_or_before(btc_1m, ts: datetime) -> float | None:
    if btc_1m is None or len(btc_1m) == 0 or "time" not in btc_1m.columns or "close" not in btc_1m.columns:
        return None
    times = pd.to_datetime(btc_1m["time"], utc=True)
    lookup = pd.Timestamp(ts).tz_convert("UTC")
    idx = int(np.searchsorted(times.dt.tz_localize(None).to_numpy(), lookup.tz_localize(None).to_datetime64(), side="right")) - 1
    if idx < 0 or idx >= len(btc_1m):
        return None
    value = optional_float(btc_1m.iloc[idx]["close"])
    return value if value is not None and math.isfinite(value) and value > 0 else None


def paper_shadow_summary(
    conn: sqlite3.Connection,
    btc_1m,
    starting_bankroll: float,
    now: datetime,
    mode: str = "paper",
) -> dict[str, float | int]:
    rows = conn.execute(
        """
        SELECT *
        FROM research_live_trades
        WHERE mode = ?
          AND status = 'paper_filled'
        ORDER BY created_at, id
        """,
        (mode,),
    ).fetchall()
    realized_pnl = 0.0
    settled_premium = 0.0
    active_exposure = 0.0
    open_trades = 0
    settled_trades = 0
    wins = 0
    losses = 0
    for row in rows:
        contracts = int(row["contracts"] or 0)
        entry = optional_float(row["actual_entry_price"]) or optional_float(row["entry_price"]) or 0.0
        fee = optional_float(row["actual_fee_paid"])
        if fee is None:
            fee = optional_float(row["entry_fee_estimate"]) or 0.0
        premium = entry * contracts + fee
        close_time = utc_dt(row["close_time"])
        if close_time is None or close_time > now:
            active_exposure += premium
            open_trades += 1
            continue
        settlement_spot = btc_close_at_or_before(btc_1m, close_time)
        if settlement_spot is None:
            active_exposure += premium
            open_trades += 1
            continue
        strike = market_strike_from_ticker(row["market_ticker"])
        if strike is None:
            active_exposure += premium
            open_trades += 1
            continue
        yes_wins = settlement_spot >= strike
        won = (row["side"] == "yes" and yes_wins) or (row["side"] == "no" and not yes_wins)
        payout = float(contracts) if won else 0.0
        pnl = payout - premium
        realized_pnl += pnl
        settled_premium += premium
        settled_trades += 1
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    total_trades = len(rows)
    equity = starting_bankroll + realized_pnl
    available = equity - active_exposure
    return {
        "starting_bankroll": float(starting_bankroll),
        "equity": float(equity),
        "available_balance": float(available),
        "realized_pnl": float(realized_pnl),
        "return_on_bankroll": float(realized_pnl / starting_bankroll) if starting_bankroll > 0 else 0.0,
        "settled_premium": float(settled_premium),
        "return_on_premium": float(realized_pnl / settled_premium) if settled_premium > 0 else 0.0,
        "active_exposure": float(active_exposure),
        "total_trades": int(total_trades),
        "settled_trades": int(settled_trades),
        "open_trades": int(open_trades),
        "wins": int(wins),
        "losses": int(losses),
    }


def shadow_portfolio_snapshot(
    conn: sqlite3.Connection,
    btc_1m,
    starting_bankroll: float,
    now: datetime,
    mode: str = "paper",
) -> PortfolioSnapshot:
    summary = paper_shadow_summary(conn, btc_1m, starting_bankroll, now, mode=mode)
    return PortfolioSnapshot(
        available_balance=max(0.0, float(summary["available_balance"])),
        portfolio_value=max(0.0, float(summary["equity"])),
        active_tickers=db_active_tickers(conn, now),
        market_exposure=float(summary["active_exposure"]),
    )


def log_paper_shadow_summary(
    conn: sqlite3.Connection,
    btc_1m,
    starting_bankroll: float,
    mode: str = "paper",
    label: str = "SHADOW",
) -> None:
    summary = paper_shadow_summary(conn, btc_1m, starting_bankroll, datetime.now(timezone.utc), mode=mode)
    log.info(
        "%s report strategy=%s start=$%.2f equity=$%.2f realized_pnl=$%.2f bankroll_return=%.3f%% "
        "settled_premium=$%.2f premium_return=%.3f%% trades=%d settled=%d open=%d wins=%d losses=%d "
        "active_exposure=$%.2f available=$%.2f",
        label,
        SIGNAL_STRATEGY,
        summary["starting_bankroll"],
        summary["equity"],
        summary["realized_pnl"],
        100.0 * float(summary["return_on_bankroll"]),
        summary["settled_premium"],
        100.0 * float(summary["return_on_premium"]),
        summary["total_trades"],
        summary["settled_trades"],
        summary["open_trades"],
        summary["wins"],
        summary["losses"],
        summary["active_exposure"],
        summary["available_balance"],
    )


def bankroll_allows_trade(
    signal: TradeSignal,
    portfolio: PortfolioSnapshot,
    local_active_exposure: float,
    spent_this_cycle: float,
) -> tuple[bool, str]:
    cost = estimated_trade_cost(signal)
    bankroll = max(portfolio.portfolio_value, portfolio.available_balance)
    total_exposure = max(portfolio.market_exposure, local_active_exposure) + spent_this_cycle
    if cost > portfolio.available_balance:
        return False, f"cost ${cost:.2f} exceeds available balance ${portfolio.available_balance:.2f}"
    per_market_cap = bankroll * RESEARCH_MAX_PER_MARKET_FRACTION
    if cost > per_market_cap:
        return False, f"cost ${cost:.2f} exceeds per-market cap ${per_market_cap:.2f}"
    total_cap = bankroll * RESEARCH_MAX_TOTAL_EXPOSURE_FRACTION
    if total_exposure + cost > total_cap:
        return False, f"active exposure ${total_exposure + cost:.2f} exceeds total cap ${total_cap:.2f}"
    return True, "ok"


def select_one_per_event(signals: list[TradeSignal], max_count: int) -> list[TradeSignal]:
    if max_count <= 0:
        return []
    selected = []
    used_events = set()
    for sig in sorted(signals, key=lambda s: s.net_edge_cents, reverse=True):
        if sig.event_ticker in used_events:
            continue
        selected.append(sig)
        used_events.add(sig.event_ticker)
        if len(selected) >= max_count:
            break
    return selected


def find_signals(
    data_client: KalshiApi,
    btc_1m,
    events: list[dict],
) -> tuple[list[TradeSignal], dict[str, dict]]:
    log.info("candidate %s current-hour events: %d", RESEARCH_SERIES, len(events))
    if not events:
        return [], {}

    spot = base_strategy.get_btc_spot()
    signals: list[TradeSignal] = []
    cache_by_event: dict[str, dict] = {}
    for event in events:
        emp_cache = build_event_emp_cache(btc_1m, event, verbose=False)
        if not emp_cache:
            continue
        cache_by_event[event["event_ticker"]] = emp_cache
        markets = [m for m in event["markets"] if m.get("ticker")]
        tickers = [m["ticker"] for m in markets]
        log.info(
            "fetching orderbooks: %s markets=%d ttl=%.1fm close=%s",
            event["event_ticker"],
            len(tickers),
            event["ttl_hours"] * 60,
            utc_dt(event["close_time"]).isoformat(),
        )
        books = data_client.get_orderbooks(tickers, depth=1)
        for market in markets:
            ticker = market["ticker"]
            payload = books.get(ticker)
            if not payload:
                continue
            quote = quote_from_orderbook(ticker, payload)
            signal = signal_from_book(
                event,
                market,
                quote,
                btc_1m,
                emp_cache,
                spot=spot,
                contracts=RESEARCH_SIGNAL_CONTRACTS,
                min_edge_cents=RESEARCH_MIN_EDGE_CENTS,
                max_spread_cents=RESEARCH_MAX_SPREAD_CENTS,
            )
            if signal:
                signals.append(signal)
    return sorted(signals, key=lambda s: s.net_edge_cents, reverse=True), cache_by_event


def reprice_signal(
    data_client: KalshiApi,
    signal: TradeSignal,
    events_by_ticker: dict[str, dict],
    markets_by_ticker: dict[str, dict],
    btc_1m,
    emp_cache: dict,
) -> TradeSignal | None:
    event = events_by_ticker.get(signal.event_ticker)
    market = markets_by_ticker.get(signal.market_ticker)
    if not event or not market:
        return None
    spot = base_strategy.get_btc_spot()
    payload = data_client.get_orderbook(signal.market_ticker, depth=1)
    quote = quote_from_orderbook(signal.market_ticker, payload)
    fresh = signal_from_book(
        event,
        market,
        quote,
        btc_1m,
        emp_cache,
        spot=spot,
        contracts=signal.contracts,
        min_edge_cents=RESEARCH_MIN_EDGE_CENTS,
        max_spread_cents=RESEARCH_MAX_SPREAD_CENTS,
    )
    if fresh and fresh.side == signal.side:
        return fresh
    return None


def print_signals(signals: list[TradeSignal], limit: int = 10) -> None:
    for sig in signals[:limit]:
        log.info(
            "signal %s %s %s entry=%.4f net_edge=%.2fc threshold=%.2fc spread=%.2fc p_yes=%.3f qty=%.2f",
            sig.event_ticker,
            sig.market_ticker,
            sig.side.upper(),
            sig.entry_price,
            sig.net_edge_cents,
            sig.edge_threshold_cents,
            sig.spread_cents,
            sig.model_p_yes,
            sig.available_qty,
        )


def find_signals_from_quotes(
    btc_1m,
    events: list[dict],
    quotes: dict[str, BookQuote],
    cache_by_event: dict[str, dict],
    spot: float,
    executor: ThreadPoolExecutor | None = None,
) -> list[TradeSignal]:
    jobs: list[tuple[dict, dict, BookQuote, dict]] = []
    for event in events:
        emp_cache = cache_by_event.get(event["event_ticker"])
        if not emp_cache:
            continue
        for market in event.get("markets", []):
            ticker = str(market.get("ticker") or "").upper()
            quote = quotes.get(ticker)
            if quote is not None:
                jobs.append((event, market, quote, emp_cache))

    def evaluate(job: tuple[dict, dict, BookQuote, dict]) -> TradeSignal | None:
        event, market, quote, emp_cache = job
        return signal_from_book(
            event,
            market,
            quote,
            btc_1m,
            emp_cache,
            spot=spot,
            contracts=RESEARCH_SIGNAL_CONTRACTS,
            min_edge_cents=RESEARCH_MIN_EDGE_CENTS,
            max_spread_cents=RESEARCH_MAX_SPREAD_CENTS,
        )

    if not jobs:
        return []
    if executor is not None and len(jobs) >= 12:
        signals = list(executor.map(evaluate, jobs))
    else:
        signals = [evaluate(job) for job in jobs]
    return sorted([signal for signal in signals if signal is not None], key=lambda s: s.net_edge_cents, reverse=True)


def reprice_signal_from_state(
    state: LiveMarketState,
    signal: TradeSignal,
    events_by_ticker: dict[str, dict],
    markets_by_ticker: dict[str, dict],
    btc_1m,
    emp_cache: dict,
    spot: float,
) -> TradeSignal | None:
    event = events_by_ticker.get(signal.event_ticker)
    market = markets_by_ticker.get(signal.market_ticker)
    if not event or not market:
        return None
    _, _, quotes, _, market_ok, _ = state.snapshot_scan_inputs({signal.market_ticker})
    if not market_ok:
        return None
    quote = quotes.get(signal.market_ticker)
    if quote is None:
        return None
    fresh = signal_from_book(
        event,
        market,
        quote,
        btc_1m,
        emp_cache,
        spot=spot,
        contracts=signal.contracts,
        min_edge_cents=RESEARCH_MIN_EDGE_CENTS,
        max_spread_cents=RESEARCH_MAX_SPREAD_CENTS,
    )
    if fresh and fresh.side == signal.side:
        return fresh
    return None


class WsResearchExecutor:
    def __init__(
        self,
        args,
        data_client: KalshiApi,
        trade_client: KalshiApi | None,
        conn: sqlite3.Connection,
        state: LiveMarketState,
        recorder: LiveCaptureWriter,
    ) -> None:
        self.args = args
        self.data_client = data_client
        self.trade_client = trade_client
        self.conn = conn
        self.state = state
        self.recorder = recorder
        self.btc_1m = None
        self.events: list[dict] = []
        self.cache_by_event: dict[str, dict] = {}
        self.events_by_ticker: dict[str, dict] = {}
        self.markets_by_ticker: dict[str, dict] = {}
        self.executor = ThreadPoolExecutor(max_workers=WS_MAX_SCAN_WORKERS, thread_name_prefix="research-scan")
        self._last_no_signal_log = 0.0
        self._last_exchange_status_check = 0.0
        self._exchange_active = True
        self._last_event_log_key: tuple[Any, ...] | None = None

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)

    def clear_events(self) -> set[str]:
        self.events = []
        self.events_by_ticker = {}
        self.markets_by_ticker = {}
        self.cache_by_event = {}
        self._last_event_log_key = None
        return self.state.set_events([])

    def set_btc_1m(self, btc_1m) -> None:
        self.btc_1m = btc_1m

    def refresh_events(self) -> set[str]:
        try:
            use_cache = bool(self.events) and not current_event_needs_refresh(self.events)
            events = scan_research_events(self.data_client, use_cache=use_cache)[:1]
        except Exception as exc:
            if self.events and not current_event_needs_refresh(self.events):
                log.warning("event refresh failed; keeping prior event set: %s", exc)
                return self.state.desired_market_tickers()
            log.warning("event refresh failed with no eligible prior event; clearing current event set: %s", exc)
            return self.clear_events()
        if not events and self.events and not current_event_needs_refresh(self.events):
            log.warning("event refresh returned no eligible events; keeping prior event set")
            return self.state.desired_market_tickers()
        self.events = events
        self.events_by_ticker = {event["event_ticker"]: event for event in events}
        self.markets_by_ticker = {
            str(market["ticker"]).upper(): market
            for event in events
            for market in event.get("markets", [])
            if market.get("ticker")
        }
        tickers = self.state.set_events(events)
        new_cache: dict[str, dict] = {}
        for event in events:
            previous = self.cache_by_event.get(event["event_ticker"])
            if previous:
                new_cache[event["event_ticker"]] = previous
            else:
                emp_cache = build_event_emp_cache(self.btc_1m, event, verbose=False)
                if emp_cache:
                    new_cache[event["event_ticker"]] = emp_cache
        self.cache_by_event = new_cache
        event_log_key: tuple[Any, ...]
        if events:
            event = events[0]
            event_log_key = (event["event_ticker"], len(event.get("markets", [])), str(utc_dt(event["close_time"])))
            if event_log_key != self._last_event_log_key:
                log.info(
                    "WS current event %s markets=%d ttl=%.1fm close=%s",
                    event["event_ticker"],
                    len(event.get("markets", [])),
                    event["ttl_hours"] * 60.0,
                    utc_dt(event["close_time"]).isoformat(),
                )
        else:
            event_log_key = ("none",)
            if event_log_key != self._last_event_log_key:
                log.info("WS current event: none eligible")
        self._last_event_log_key = event_log_key
        return tickers

    def exchange_is_active(self) -> bool:
        if self.args.mode != "live":
            return True
        now = time.monotonic()
        if now - self._last_exchange_status_check < WS_SCAN_STATUS_TTL_SEC:
            return self._exchange_active
        status = self.data_client.get_exchange_status()
        self._exchange_active = bool(status.get("exchange_active") and status.get("trading_active"))
        self._last_exchange_status_check = now
        if not self._exchange_active:
            log.warning("exchange is not trading: %s", status)
        return self._exchange_active

    def scan(self, changed_tickers: set[str] | None, reason: str) -> None:
        started_ns = utc_now_ns()
        if self.btc_1m is None:
            return
        if self.args.mode == "live" and not self.recorder.is_healthy():
            detail = "capture_unhealthy " + self.recorder.health_detail()
            log.warning("skip live scan: %s", detail)
            self._record_scan(started_ns, reason, changed_tickers, [], [], 0.0, "skip", detail[:900])
            return
        if not self.exchange_is_active():
            self._record_scan(started_ns, reason, changed_tickers, [], [], 0.0, "skip", "exchange_inactive")
            return
        events, markets_by_ticker, quotes, spot, market_ok, market_reason = self.state.snapshot_scan_inputs(changed_tickers)
        if not market_ok:
            self._record_scan(started_ns, reason, changed_tickers, [], [], 0.0, "skip", market_reason)
            return
        if not events or not markets_by_ticker:
            self._record_scan(started_ns, reason, changed_tickers, [], [], 0.0, "skip", "no_current_event")
            return
        ready_books, total_books = self.state.orderbook_coverage()
        if total_books and ready_books < total_books:
            self._record_scan(
                started_ns,
                reason,
                changed_tickers,
                [],
                [],
                spot or 0.0,
                "skip",
                f"waiting_for_orderbook_snapshots {ready_books}/{total_books}",
            )
            return
        if not quotes:
            self._record_scan(started_ns, reason, changed_tickers, [], [], 0.0, "skip", "no_fresh_book")
            return
        if spot is None or not math.isfinite(spot) or spot <= 0:
            self._record_scan(started_ns, reason, changed_tickers, [], [], 0.0, "skip", "no_btc_spot")
            return

        signals = find_signals_from_quotes(
            self.btc_1m,
            events,
            quotes,
            self.cache_by_event,
            spot,
            executor=self.executor,
        )
        if not signals:
            now = time.monotonic()
            if now - self._last_no_signal_log >= 30:
                log.info("WS scan no %s signals reason=%s evaluated=%d", SIGNAL_STRATEGY, reason, len(quotes))
                self._last_no_signal_log = now
            self._record_scan(started_ns, reason, changed_tickers, [], [], spot, "none", "filters_rejected")
            return

        now_dt = datetime.now(timezone.utc)
        blocked = db_active_tickers(self.conn, now_dt)
        blocked_events = db_active_events(self.conn, now_dt)
        local_active_exposure = db_active_exposure(self.conn, now_dt)
        portfolio = None
        if self.args.mode == "live" and self.trade_client is not None:
            portfolio = get_portfolio_snapshot(self.trade_client)
            blocked.update(portfolio.active_tickers)
            blocked_events.update(
                event
                for event in (event_from_market_ticker(ticker) for ticker in portfolio.active_tickers)
                if event
            )
        elif self.args.mode == "paper" and self.args.shadow_bankroll > 0:
            portfolio = shadow_portfolio_snapshot(self.conn, self.btc_1m, self.args.shadow_bankroll, now_dt, mode=self.args.mode)
            blocked.update(portfolio.active_tickers)
            local_active_exposure = portfolio.market_exposure

        candidates = [
            sig
            for sig in signals
            if sig.market_ticker not in blocked and sig.event_ticker.upper() not in blocked_events
        ]
        selected = select_one_per_event(candidates, RESEARCH_MAX_SIGNALS_PER_CYCLE)
        if not selected:
            self._record_scan(started_ns, reason, changed_tickers, signals, [], spot, "blocked", "dedupe")
            return
        print_signals(selected, limit=self.args.print_candidates)
        self._record_scan(started_ns, reason, changed_tickers, signals, selected, spot, "selected", "candidate")

        spent_this_cycle = 0.0
        for original in selected:
            emp_cache = self.cache_by_event.get(original.event_ticker)
            if not emp_cache:
                self._record_decision(original, "skip", "missing_emp_cache", None, None, None)
                continue
            fresh = reprice_signal_from_state(
                self.state,
                original,
                self.events_by_ticker,
                self.markets_by_ticker,
                self.btc_1m,
                emp_cache,
                spot,
            )
            if not fresh:
                self._record_decision(original, "skip", "failed_ws_reprice_filter", None, None, None)
                log.info("skip %s: failed websocket book reprice/filter", original.market_ticker)
                continue
            sized_contracts = choose_contracts_for_signal(fresh, portfolio, local_active_exposure, spent_this_cycle)
            if sized_contracts <= 0:
                self._record_decision(fresh, "skip", "bankroll_depth_gate_zero", portfolio, None, None)
                log.info("skip %s: bankroll/depth gate allowed 0 contracts", fresh.market_ticker)
                continue
            fresh = resize_signal(fresh, sized_contracts)
            estimated_cost = fresh.contracts * fresh.entry_price + fresh.entry_fee
            if portfolio is not None:
                ok, why = bankroll_allows_trade(fresh, portfolio, local_active_exposure, spent_this_cycle)
                if not ok:
                    self._record_decision(fresh, "skip", why, portfolio, estimated_cost, None)
                    log.info("skip %s: %s", fresh.market_ticker, why)
                    continue

            if self.args.mode == "dry-run":
                log.info("DRY RUN would buy %s %s x%d @ %.4f", fresh.market_ticker, fresh.side.upper(), fresh.contracts, fresh.entry_price)
                self._record_decision(fresh, "dry_run", "would_trade", portfolio, estimated_cost, None)
            elif self.args.mode == "paper":
                if not acquire_event_lock(self.conn, fresh, self.args.mode):
                    log.info("skip %s: event lock already held for %s", fresh.market_ticker, fresh.event_ticker)
                    self._record_decision(fresh, "skip", "event_lock_held", portfolio, estimated_cost, None)
                    continue
                log.info("PAPER fill %s %s x%d @ %.4f", fresh.market_ticker, fresh.side.upper(), fresh.contracts, fresh.entry_price)
                record_trade(self.conn, self.args.mode, "paper_filled", fresh)
                self._record_decision(fresh, "paper_fill", "filled", portfolio, estimated_cost, None)
                spent_this_cycle += estimated_cost
            else:
                if self.trade_client is None:
                    raise RuntimeError("trade client is required in live mode")
                client_order_id = f"research-{int(time.time())}-{uuid4().hex[:10]}"
                if not acquire_event_lock(self.conn, fresh, self.args.mode, client_order_id=client_order_id):
                    log.info("skip %s: event lock already held for %s", fresh.market_ticker, fresh.event_ticker)
                    self._record_decision(fresh, "skip", "event_lock_held", portfolio, estimated_cost, client_order_id)
                    continue
                trade_id = record_trade(self.conn, self.args.mode, "submitted", fresh, client_order_id=client_order_id)
                self._record_decision(fresh, "submit", "before_order", portfolio, estimated_cost, client_order_id)
                log.info(
                    "LIVE FOK %s YES_BOOK_%s x%d yes_price=%.4f economic_side=%s entry=%.4f",
                    fresh.market_ticker,
                    fresh.yes_order_side.upper(),
                    fresh.contracts,
                    fresh.yes_limit_price,
                    fresh.side.upper(),
                    fresh.entry_price,
                )
                try:
                    response = self.trade_client.create_event_order(
                        ticker=fresh.market_ticker,
                        yes_book_side=fresh.yes_order_side,
                        count=fresh.contracts,
                        yes_price=fresh.yes_limit_price,
                        client_order_id=client_order_id,
                    )
                except requests.HTTPError as exc:
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    if status_code == 409:
                        synced = sync_order_by_client_id(
                            self.trade_client,
                            self.conn,
                            trade_id,
                            fresh,
                            client_order_id,
                        )
                        if synced:
                            status, order = synced
                            log.info(
                                "LIVE 409 synced as status=%s order_id=%s fill_count=%s",
                                status,
                                order.get("order_id"),
                                order.get("fill_count_fp") or order.get("fill_count"),
                            )
                            self._record_decision(fresh, status, "order_sync_after_409", portfolio, estimated_cost, client_order_id)
                            spent_this_cycle += estimated_cost if status in {"filled", "partial_filled"} else 0.0
                            continue
                        if is_fok_no_fill_conflict(exc):
                            response_text = http_error_text(exc)
                            update_trade_response(
                                self.conn,
                                trade_id,
                                "not_filled",
                                fresh,
                                {"error": {"code": "fill_or_kill_insufficient_resting_volume", "raw": response_text}},
                            )
                            release_event_lock(self.conn, fresh.event_ticker, client_order_id)
                            log.info(
                                "LIVE 409 FOK no fill for %s client_order_id=%s; marked not_filled and continuing",
                                fresh.market_ticker,
                                client_order_id,
                            )
                            self._record_decision(fresh, "not_filled", "fok_no_fill_409", portfolio, estimated_cost, client_order_id)
                            continue
                    log.exception(
                        "LIVE order submit failed or state unknown for %s client_order_id=%s; keeping row status=submitted",
                        fresh.market_ticker,
                        client_order_id,
                    )
                    self._record_decision(fresh, "submit_failed", "state_unknown", portfolio, estimated_cost, client_order_id)
                    raise
                except Exception:
                    log.exception(
                        "LIVE order submit failed or state unknown for %s client_order_id=%s; keeping row status=submitted",
                        fresh.market_ticker,
                        client_order_id,
                    )
                    self._record_decision(fresh, "submit_failed", "state_unknown", portfolio, estimated_cost, client_order_id)
                    raise
                order = order_from_response(response)
                fill_count = optional_float(order.get("fill_count_fp")) or optional_float(order.get("fill_count")) or 0.0
                if fill_count >= fresh.contracts:
                    status = "filled"
                elif fill_count > 0:
                    status = "partial_filled"
                else:
                    status = "not_filled"
                log.info("LIVE response status=%s response=%s", status, response)
                update_trade_response(self.conn, trade_id, status, fresh, response)
                if status == "not_filled":
                    release_event_lock(self.conn, fresh.event_ticker, client_order_id)
                self._record_decision(fresh, status, "order_response", portfolio, estimated_cost, client_order_id)
                spent_this_cycle += estimated_cost if status in {"filled", "partial_filled"} else 0.0

    def _record_scan(
        self,
        started_ns: int,
        reason: str,
        changed_tickers: set[str] | None,
        signals: list[TradeSignal],
        selected: list[TradeSignal],
        spot: float,
        action: str,
        detail: str,
    ) -> None:
        selected_signal = selected[0] if selected else None
        event_ticker = selected_signal.event_ticker if selected_signal else (self.events[0]["event_ticker"] if self.events else None)
        self.recorder.record(
            "signal_scan",
            {
                "received_at_ns": started_ns,
                "received_at_utc": ns_to_utc_iso(started_ns),
                "reason": reason,
                "mode": self.args.mode,
                "event_ticker": event_ticker,
                "changed_markets": len(changed_tickers or []),
                "evaluated_markets": len(changed_tickers or self.markets_by_ticker),
                "candidate_count": len(signals),
                "selected_market": selected_signal.market_ticker if selected_signal else None,
                "selected_side": selected_signal.side if selected_signal else None,
                "entry_price": selected_signal.entry_price if selected_signal else None,
                "net_edge_cents": selected_signal.net_edge_cents if selected_signal else None,
                "model_p_yes": selected_signal.model_p_yes if selected_signal else None,
                "btc_spot": spot if spot else None,
                "latency_ms": (utc_now_ns() - started_ns) / 1_000_000,
                "blocked_events": len(db_active_events(self.conn, datetime.now(timezone.utc))),
                "action": action,
                "detail": detail,
            },
        )

    def _record_decision(
        self,
        signal: TradeSignal,
        action: str,
        detail: str,
        portfolio: PortfolioSnapshot | None,
        estimated_cost: float | None,
        client_order_id: str | None,
    ) -> None:
        received_at_ns = utc_now_ns()
        self.recorder.record(
            "order_decision",
            {
                "received_at_ns": received_at_ns,
                "received_at_utc": ns_to_utc_iso(received_at_ns),
                "mode": self.args.mode,
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
                "detail": detail,
            },
        )


def run_once(args, data_client: KalshiApi, trade_client: KalshiApi | None, conn: sqlite3.Connection, btc_1m):
    if args.mode == "live":
        status = data_client.get_exchange_status()
        if not status.get("exchange_active") or not status.get("trading_active"):
            raise RuntimeError(f"Exchange is not trading: {status}")

    btc_1m = refresh_btc_data_cached(btc_1m, require_fresh=True)
    events = scan_research_events(data_client)
    events = events[:1]
    signals, cache_by_event = find_signals(data_client=data_client, btc_1m=btc_1m, events=events)
    if not signals:
        log.info("no %s signals passed filters", SIGNAL_STRATEGY)
        return btc_1m
    print_signals(signals, limit=args.print_candidates)

    now = datetime.now(timezone.utc)
    blocked = db_active_tickers(conn, now)
    blocked_events = db_active_events(conn, now)
    local_active_exposure = db_active_exposure(conn, now)
    portfolio = None
    if args.mode == "live" and trade_client is not None:
        portfolio = get_portfolio_snapshot(trade_client)
        blocked.update(portfolio.active_tickers)
        blocked_events.update(
            event
            for event in (event_from_market_ticker(ticker) for ticker in portfolio.active_tickers)
            if event
        )
        log.info(
            "portfolio balance=$%.2f value=$%.2f active_exposure=$%.2f local_active=$%.2f blocked_events=%d",
            portfolio.available_balance,
            portfolio.portfolio_value,
            portfolio.market_exposure,
            local_active_exposure,
            len(blocked_events),
        )
    elif args.mode == "paper" and args.shadow_bankroll > 0:
        portfolio = shadow_portfolio_snapshot(conn, btc_1m, args.shadow_bankroll, now, mode=args.mode)
        blocked.update(portfolio.active_tickers)
        local_active_exposure = portfolio.market_exposure
        log.info(
            "shadow portfolio balance=$%.2f value=$%.2f active_exposure=$%.2f blocked_events=%d",
            portfolio.available_balance,
            portfolio.portfolio_value,
            portfolio.market_exposure,
            len(blocked_events),
        )

    fresh_events = scan_research_events(data_client)[:1]
    event_keys = {event["event_ticker"] for event in events}
    fresh_event_keys = {event["event_ticker"] for event in fresh_events}
    if event_keys != fresh_event_keys:
        log.info("skip cycle: current event changed during scan old=%s fresh=%s", sorted(event_keys), sorted(fresh_event_keys))
        return btc_1m
    events = fresh_events
    events_by_ticker = {event["event_ticker"]: event for event in events}
    markets_by_ticker = {
        market["ticker"]: market
        for event in events
        for market in event["markets"]
        if market.get("ticker")
    }

    candidates = [
        sig
        for sig in signals
        if sig.market_ticker not in blocked and sig.event_ticker.upper() not in blocked_events
    ]
    selected = select_one_per_event(candidates, RESEARCH_MAX_SIGNALS_PER_CYCLE)
    if not selected:
        log.info("signals existed, but all were blocked by open ticker/event dedupe checks")
        return btc_1m

    spent_this_cycle = 0.0
    for original in selected:
        emp_cache = cache_by_event.get(original.event_ticker)
        if not emp_cache:
            log.info("skip %s: missing event-open empirical cache", original.market_ticker)
            continue
        fresh = reprice_signal(
            data_client,
            original,
            events_by_ticker,
            markets_by_ticker,
            btc_1m,
            emp_cache,
        )
        if not fresh:
            log.info("skip %s: failed fresh orderbook reprice/filter", original.market_ticker)
            continue
        sized_contracts = choose_contracts_for_signal(fresh, portfolio, local_active_exposure, spent_this_cycle)
        if sized_contracts <= 0:
            log.info("skip %s: bankroll/depth gate allowed 0 contracts", fresh.market_ticker)
            continue
        fresh = resize_signal(fresh, sized_contracts)
        estimated_cost = fresh.contracts * fresh.entry_price + fresh.entry_fee
        if portfolio is not None:
            ok, reason = bankroll_allows_trade(fresh, portfolio, local_active_exposure, spent_this_cycle)
            if not ok:
                log.info("skip %s: %s", fresh.market_ticker, reason)
                continue

        if args.mode == "dry-run":
            log.info("DRY RUN would buy %s %s x%d @ %.4f", fresh.market_ticker, fresh.side.upper(), fresh.contracts, fresh.entry_price)
        elif args.mode == "paper":
            if not acquire_event_lock(conn, fresh, args.mode):
                log.info("skip %s: event lock already held for %s", fresh.market_ticker, fresh.event_ticker)
                continue
            log.info("PAPER fill %s %s x%d @ %.4f", fresh.market_ticker, fresh.side.upper(), fresh.contracts, fresh.entry_price)
            record_trade(conn, args.mode, "paper_filled", fresh)
            spent_this_cycle += estimated_cost
            blocked.add(fresh.market_ticker)
            blocked_events.add(fresh.event_ticker.upper())
        else:
            if trade_client is None:
                raise RuntimeError("trade client is required in live mode")
            client_order_id = f"research-{int(time.time())}-{uuid4().hex[:10]}"
            if not acquire_event_lock(conn, fresh, args.mode, client_order_id=client_order_id):
                log.info("skip %s: event lock already held for %s", fresh.market_ticker, fresh.event_ticker)
                continue
            trade_id = record_trade(conn, args.mode, "submitted", fresh, client_order_id=client_order_id)
            log.info(
                "LIVE FOK %s YES_BOOK_%s x%d yes_price=%.4f economic_side=%s entry=%.4f",
                fresh.market_ticker,
                fresh.yes_order_side.upper(),
                fresh.contracts,
                fresh.yes_limit_price,
                fresh.side.upper(),
                fresh.entry_price,
            )
            try:
                response = trade_client.create_event_order(
                    ticker=fresh.market_ticker,
                    yes_book_side=fresh.yes_order_side,
                    count=fresh.contracts,
                    yes_price=fresh.yes_limit_price,
                    client_order_id=client_order_id,
                )
            except requests.HTTPError as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 409:
                    synced = sync_order_by_client_id(trade_client, conn, trade_id, fresh, client_order_id)
                    if synced:
                        status, order = synced
                        log.info(
                            "LIVE 409 synced as status=%s order_id=%s fill_count=%s",
                            status,
                            order.get("order_id"),
                            order.get("fill_count_fp") or order.get("fill_count"),
                        )
                        spent_this_cycle += estimated_cost if status in {"filled", "partial_filled"} else 0.0
                        continue
                    if is_fok_no_fill_conflict(exc):
                        response_text = http_error_text(exc)
                        update_trade_response(
                            conn,
                            trade_id,
                            "not_filled",
                            fresh,
                            {"error": {"code": "fill_or_kill_insufficient_resting_volume", "raw": response_text}},
                        )
                        release_event_lock(conn, fresh.event_ticker, client_order_id)
                        log.info(
                            "LIVE 409 FOK no fill for %s client_order_id=%s; marked not_filled and continuing",
                            fresh.market_ticker,
                            client_order_id,
                        )
                        continue
                log.exception(
                    "LIVE order submit failed or state unknown for %s client_order_id=%s; keeping row status=submitted",
                    fresh.market_ticker,
                    client_order_id,
                )
                raise
            except Exception:
                log.exception(
                    "LIVE order submit failed or state unknown for %s client_order_id=%s; keeping row status=submitted",
                    fresh.market_ticker,
                    client_order_id,
                )
                raise
            order = order_from_response(response)
            fill_count = optional_float(order.get("fill_count_fp")) or optional_float(order.get("fill_count")) or 0.0
            if fill_count >= fresh.contracts:
                status = "filled"
            elif fill_count > 0:
                status = "partial_filled"
            else:
                status = "not_filled"
            log.info("LIVE response status=%s response=%s", status, response)
            update_trade_response(conn, trade_id, status, fresh, response)
            if status == "not_filled":
                release_event_lock(conn, fresh.event_ticker, client_order_id)
            spent_this_cycle += estimated_cost if status in {"filled", "partial_filled"} else 0.0
            if status in {"filled", "partial_filled"}:
                blocked.add(fresh.market_ticker)
                blocked_events.add(fresh.event_ticker.upper())
    return btc_1m


def current_event_needs_refresh(events: list[dict]) -> bool:
    if not events:
        return False
    close_time = utc_dt(events[0].get("close_time"))
    if close_time is None:
        return True
    ttl_min = (close_time - datetime.now(timezone.utc)).total_seconds() / 60.0
    return ttl_min <= CFG["min_ttl_min"] or ttl_min > RESEARCH_MAX_TTL_MIN


def drain_updates(update_queue: queue.Queue, first: dict[str, Any] | None) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    if first is not None:
        updates.append(first)
    while len(updates) < 5000:
        try:
            updates.append(update_queue.get_nowait())
        except queue.Empty:
            break
    return updates


def record_capture_health(
    recorder: LiveCaptureWriter,
    state: LiveMarketState,
    args,
    kind: str,
    detail: str = "",
) -> None:
    received_at_ns = utc_now_ns()
    health = state.health_snapshot()
    capture_detail = recorder.health_detail()
    if detail:
        capture_detail = f"{detail} {capture_detail}"
    recorder.record(
        "capture_health",
        {
            "received_at_ns": received_at_ns,
            "received_at_utc": ns_to_utc_iso(received_at_ns),
            "mode": args.mode,
            "kind": kind,
            "event_ticker": health["event_ticker"],
            "kalshi_connected": health["kalshi_connected"],
            "coinbase_connected": health["coinbase_connected"],
            "market_ok": health["market_ok"],
            "market_reason": health["market_reason"],
            "ready_books": health["ready_books"],
            "total_books": health["total_books"],
            "subscribed_markets": health["subscribed_markets"],
            "btc_spot": health["btc_spot"],
            "btc_spot_age_sec": health["btc_spot_age_sec"],
            "capture_queue_depth": recorder.depth(),
            "capture_dropped": recorder.dropped,
            "detail": capture_detail,
        },
    )


def run_websocket_loop(
    args,
    data_client: KalshiApi,
    trade_client: KalshiApi | None,
    conn: sqlite3.Connection,
    btc_1m,
) -> None:
    update_queue = CoalescedUpdateBuffer()
    recorder = LiveCaptureWriter(args.capture_db_path, enabled=not args.no_capture, capture_raw_ws=args.capture_raw_ws)
    state = LiveMarketState()
    executor = WsResearchExecutor(args, data_client, trade_client, conn, state, recorder)
    executor.set_btc_1m(btc_1m)
    kalshi_ws: KalshiWsClient | None = None
    spot_ws: KrakenWsSpot | None = None
    try:
        log.info("waiting for fresh Kraken websocket BTC spot")

        market_tickers = executor.refresh_events()
        kalshi_ws = KalshiWsClient(data_client, state, recorder, update_queue, env=args.trade_env)
        spot_ws = KrakenWsSpot(state, recorder, update_queue)
        kalshi_ws.start(market_tickers)
        spot_ws.start()
        log.info(
            "websocket mode active capture=%s raw_ws_capture=%s capture_db=%s scan_workers=%d",
            not args.no_capture,
            args.capture_raw_ws,
            args.capture_db_path,
            WS_MAX_SCAN_WORKERS,
        )
        record_capture_health(recorder, state, args, "startup")

        next_event_refresh = time.monotonic() + WS_EVENT_REFRESH_SEC + random.uniform(0.0, WS_EVENT_REFRESH_JITTER_SEC)
        next_btc_refresh = time.monotonic() + WS_BTC_CANDLE_REFRESH_SEC
        next_health = time.monotonic() + 30.0
        next_paper_report = time.monotonic() + min(60.0, float(args.paper_report_sec))
        last_hourly_report_key: str | None = None
        last_stale_btc_log = 0.0
        full_snapshot_scanned_key: tuple[str, ...] | None = None
        while True:
            now = time.monotonic()
            timeout = max(0.05, min(next_event_refresh, next_btc_refresh, next_health, next_paper_report) - now)
            first_update = None
            try:
                first_update = update_queue.get(timeout=timeout)
            except queue.Empty:
                pass
            updates = drain_updates(update_queue, first_update)
            now = time.monotonic()

            changed_tickers: set[str] = set()
            reasons: set[str] = set()
            full_scan = False
            lifecycle_refresh = False
            for item in updates:
                kind = item.get("kind")
                if kind == "batch":
                    changed_tickers.update(str(ticker).upper() for ticker in item.get("changed_tickers", set()))
                    flags = set(item.get("flags", set()))
                    reasons.update(str(reason) for reason in item.get("reasons", set()) if reason)
                    if "btc_spot" in flags:
                        full_scan = True
                    if "lifecycle" in flags:
                        lifecycle_refresh = True
                        full_scan = True
                    if "private" in flags:
                        full_scan = True
                    if item.get("dropped_tickers"):
                        log.warning("coalesced update buffer dropped_tickers=%s", item.get("dropped_tickers"))
                elif kind == "orderbook" and item.get("market_ticker"):
                    changed_tickers.add(str(item["market_ticker"]).upper())
                    reasons.add(str(item.get("source") or "orderbook"))
                elif kind == "btc_spot":
                    full_scan = True
                    reasons.add("btc_spot")
                elif kind == "lifecycle":
                    lifecycle_refresh = True
                    full_scan = True
                    reasons.add("lifecycle")
                elif kind == "private":
                    full_scan = True
                    reasons.add("private")

            if lifecycle_refresh or now >= next_event_refresh or current_event_needs_refresh(executor.events):
                try:
                    old_event_key = tuple(sorted(state.current_event_tickers()))
                    old_market_tickers = state.desired_market_tickers()
                    market_tickers = executor.refresh_events()
                    if kalshi_ws:
                        kalshi_ws.update_markets(market_tickers)
                    new_event_key = tuple(sorted(state.current_event_tickers()))
                    if old_event_key != new_event_key or old_market_tickers != market_tickers:
                        full_scan = True
                        reasons.add("event_refresh")
                        full_snapshot_scanned_key = None
                except Exception:
                    log.exception("event refresh failed")
                next_event_refresh = now + WS_EVENT_REFRESH_SEC + random.uniform(0.0, WS_EVENT_REFRESH_JITTER_SEC)

            if now >= next_btc_refresh:
                try:
                    btc_1m = refresh_btc_data_cached(btc_1m)
                    executor.set_btc_1m(btc_1m)
                    if btc_candles_are_fresh(btc_1m):
                        reasons.add("btc_candle_refresh")
                        full_scan = True
                    elif now - last_stale_btc_log >= 30.0:
                        log.warning("BTC candle refresh stale; scans blocked until fresh: %s", describe_btc_candle_freshness(btc_1m))
                        record_capture_health(recorder, state, args, "stale_btc_candles", describe_btc_candle_freshness(btc_1m))
                        last_stale_btc_log = now
                except Exception:
                    log.exception("BTC candle refresh failed")
                next_btc_refresh = now + WS_BTC_CANDLE_REFRESH_SEC

            ready_books, total_books = state.orderbook_coverage()
            current_event_key = tuple(sorted(state.current_event_tickers()))
            if total_books and ready_books >= total_books and full_snapshot_scanned_key != current_event_key:
                full_scan = True
                reasons.add("initial_full_book")
                full_snapshot_scanned_key = current_event_key
                record_capture_health(recorder, state, args, "full_book_ready")

            if updates or full_scan:
                reason = "+".join(sorted(reasons)) if reasons else "websocket"
                ready_before_scan, total_before_scan = state.orderbook_coverage()
                if not btc_candles_are_fresh(executor.btc_1m):
                    if now - last_stale_btc_log >= 30.0:
                        log.warning("WS scan blocked: stale BTC candles %s reason=%s", describe_btc_candle_freshness(executor.btc_1m), reason)
                        record_capture_health(recorder, state, args, "scan_blocked", f"stale_btc_candles reason={reason} {describe_btc_candle_freshness(executor.btc_1m)}")
                        last_stale_btc_log = now
                else:
                    executor.scan(None if full_scan else changed_tickers, reason=reason)
                if not args.loop:
                    if not total_before_scan or ready_before_scan >= total_before_scan:
                        break

            if now >= next_health:
                record_capture_health(recorder, state, args, "heartbeat")
                next_health = now + 30.0
            if args.mode == "paper" and args.shadow_bankroll > 0 and now >= next_paper_report:
                log_paper_shadow_summary(
                    conn,
                    executor.btc_1m,
                    args.shadow_bankroll,
                    mode=args.mode,
                    label="SHADOW",
                )
                next_paper_report = now + float(args.paper_report_sec)
            if args.mode == "paper" and args.shadow_bankroll > 0:
                now_utc = datetime.now(timezone.utc)
                hour_key = now_utc.strftime("%Y%m%d%H")
                if now_utc.minute <= 5 and hour_key != last_hourly_report_key:
                    log_paper_shadow_summary(
                        conn,
                        executor.btc_1m,
                        args.shadow_bankroll,
                        mode=args.mode,
                        label="SHADOW hourly",
                    )
                    last_hourly_report_key = hour_key
    finally:
        if args.mode == "paper" and args.shadow_bankroll > 0:
            log_paper_shadow_summary(conn, executor.btc_1m, args.shadow_bankroll, mode=args.mode, label="SHADOW final")
        record_capture_health(recorder, state, args, "shutdown")
        if kalshi_ws:
            kalshi_ws.stop()
        if spot_ws:
            spot_ws.stop()
        executor.close()
        recorder.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the deployed BTC 1-hour research strategy.")
    parser.add_argument("--dry-run", action="store_true", help="Read live data and print would-trade decisions only.")
    parser.add_argument("--paper", action="store_true", help="Record simulated fills using live orderbook prices.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle. Default is continuous live execution.")
    parser.add_argument("--interval-sec", type=int, default=RESEARCH_SCAN_INTERVAL_SEC)
    parser.add_argument("--print-candidates", type=int, default=10)
    parser.add_argument("--signal-strategy", choices=sorted(SUPPORTED_SIGNAL_STRATEGIES), default=SIGNAL_STRATEGY)
    parser.add_argument("--sizing-policy", choices=sorted(SUPPORTED_SIZING_POLICIES), default=SIZING_POLICY)
    parser.add_argument("--shadow-bankroll", type=float, default=0.0, help="Paper-mode bankroll to use for mock sizing/reporting.")
    parser.add_argument("--paper-report-sec", type=int, default=300, help="Paper shadow performance report interval.")
    parser.add_argument("--polling", action="store_true", help="Use the legacy REST polling loop instead of websocket market data.")
    parser.add_argument("--no-capture", action="store_true", help="Disable live websocket/signal capture DuckDB writes.")
    parser.add_argument("--capture-raw-ws", action="store_true", default=CAPTURE_RAW_WS_DEFAULT, help="Capture raw websocket deltas/snapshot levels in addition to top-of-book rows.")
    parser.add_argument(
        "--capture-db-path",
        type=Path,
        default=Path(CFG["db_dir"]).expanduser() / "research_live_capture.duckdb",
        help="DuckDB path for exact live websocket/orderbook/signal capture.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path(CFG["db_dir"]).expanduser() / "research_live_trades.db",
    )
    args = parser.parse_args()
    if args.dry_run and args.paper:
        raise SystemExit("Choose only one of --dry-run or --paper.")
    args.mode = "dry-run" if args.dry_run else ("paper" if args.paper else "live")
    args.loop = not args.once
    args.trade_env = "prod"
    args.market_data = "polling" if args.polling else "websocket"
    return args


def validate_args(args: argparse.Namespace) -> None:
    if args.market_data == "polling" and args.interval_sec < 15:
        raise ValueError("--interval-sec must be >= 15")
    if args.shadow_bankroll < 0:
        raise ValueError("--shadow-bankroll must be non-negative")
    if args.paper_report_sec < 15:
        raise ValueError("--paper-report-sec must be >= 15")


def main() -> None:
    args = parse_args()
    set_signal_strategy(args.signal_strategy)
    set_sizing_policy(args.sizing_policy)
    validate_args(args)
    log.info(
        "starting research executor mode=%s signal_strategy=%s sizing_policy=%s trade_env=%s market_data=%s series=%s train_days=%d contracts=%d interval=%ds shadow_bankroll=$%.2f capture_db=%s log=%s",
        args.mode,
        SIGNAL_STRATEGY,
        SIZING_POLICY,
        args.trade_env,
        args.market_data,
        RESEARCH_SERIES,
        RESEARCH_TRAIN_DAYS,
        RESEARCH_MAX_CONTRACTS_PER_TRADE,
        args.interval_sec,
        args.shadow_bankroll,
        args.capture_db_path,
        LOG_FILE,
    )

    data_client = KalshiApi(env="prod", require_auth=True)
    trade_client = KalshiApi(env=args.trade_env, require_auth=True) if args.mode == "live" else None
    conn = db_connect(args.db_path)

    log.info("fetching initial BTC data days=%d", RESEARCH_BOOTSTRAP_BTC_DAYS)
    btc_1m = load_btc_history_cached(RESEARCH_BOOTSTRAP_BTC_DAYS)
    btc_1m = refresh_btc_data_cached(btc_1m, require_fresh=True)

    try:
        if args.market_data == "websocket":
            run_websocket_loop(args, data_client, trade_client, conn, btc_1m)
        else:
            while True:
                try:
                    btc_1m = run_once(args, data_client, trade_client, conn, btc_1m)
                except KeyboardInterrupt:
                    log.info("stopped by user")
                    break
                except Exception:
                    log.exception("scan cycle failed")
                if not args.loop:
                    break
                time.sleep(max(5, args.interval_sec))
    except KeyboardInterrupt:
        log.info("stopped by user")

    conn.close()


if __name__ == "__main__":
    main()
