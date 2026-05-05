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
import base64
import json
import logging
import math
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dateutil import parser as dtparser

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR, kalshi_fee_dollars, load_credentials
from scripts import btc_1hr_neohardened as base_strategy


LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"btc_1hr_research_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
ORDERBOOK_BATCH_SIZE = 100
ACTIVE_TRADE_STATUSES = ("paper_filled", "filled", "partial_filled", "submitted")
FLOAT_EPSILON = 1e-9
RESEARCH_SERIES = "KXBTCD"
RESEARCH_TRAIN_DAYS = 7
RESEARCH_BOOTSTRAP_BTC_DAYS = RESEARCH_TRAIN_DAYS + 2
RESEARCH_SIGNAL_CONTRACTS = 1
RESEARCH_MAX_CONTRACTS_PER_TRADE = 3
RESEARCH_MAX_TTL_MIN = 65.0
RESEARCH_EVENT_CLOSE_TOLERANCE_SEC = 120
RESEARCH_SCAN_INTERVAL_SEC = 60
RESEARCH_MAX_SIGNALS_PER_CYCLE = 1
RESEARCH_MAX_PER_MARKET_FRACTION = float(CFG.get("max_per_market", 0.20))
RESEARCH_MAX_TOTAL_EXPOSURE_FRACTION = float(CFG.get("max_total_risk", 0.50))
NY_TZ = ZoneInfo("America/New_York")
EVENT_TICKER_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")


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

    def _get(self, path: str, params=None, auth: bool = True, timeout: int = 15) -> dict:
        headers = self._sign("GET", path) if auth else {}
        response = self.session.get(self.base_url + path, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, body: dict, timeout: int = 15) -> dict:
        headers = self._sign("POST", path)
        headers["Content-Type"] = "application/json"
        response = self.session.post(self.base_url + path, headers=headers, json=body, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def get_markets(self, limit: int = 100, auto_paginate: bool = True, **kwargs) -> dict:
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
        return {"markets": markets}

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


def scan_research_events(kalshi: KalshiApi, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    markets = kalshi.get_markets(series_ticker=RESEARCH_SERIES, status="open").get("markets", [])
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


def write_btc_cache(btc_1m) -> None:
    try:
        BTC_LIVE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        trim_btc_history(btc_1m, RESEARCH_BOOTSTRAP_BTC_DAYS).to_parquet(BTC_LIVE_CACHE_PATH, index=False)
    except Exception as exc:
        log.warning("could not write BTC live cache: %s", exc)


def load_btc_history_cached(days_back: int):
    now = datetime.now(timezone.utc)
    required_start = now - timedelta(days=days_back)
    if BTC_LIVE_CACHE_PATH.exists():
        try:
            cached = pd.read_parquet(BTC_LIVE_CACHE_PATH)
            cached["time"] = pd.to_datetime(cached["time"], utc=True)
            cached = cached.drop_duplicates("time").sort_values("time").reset_index(drop=True)
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
                log.info(
                    "BTC live cache stale/short: %s -> %s; rebuilding",
                    first.isoformat(),
                    last.isoformat(),
                )
        except Exception as exc:
            log.warning("could not read BTC live cache; rebuilding: %s", exc)

    btc_1m = base_strategy.fetch_historical_minutes(days_back=days_back)
    write_btc_cache(btc_1m)
    return btc_1m


def refresh_btc_data_cached(btc_1m):
    refreshed = base_strategy.refresh_btc_data(btc_1m)
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
    entry_price = float(best["entry_price"])
    edge_gross_cents = float(best["edge"]) * 100.0
    entry_fee = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
    net_edge_cents = edge_gross_cents - entry_fee * 100.0
    threshold = min_edge_cents + edge_uncertainty_cents(p_yes, emp_cache)
    side = best["side"]
    strong_prob = (side == "yes" and p_yes >= RESEARCH_MIN_YES_P) or (
        side == "no" and p_yes <= RESEARCH_MAX_NO_P
    )
    if not strong_prob:
        return None
    if net_edge_cents < threshold:
        return None
    if best["spread"] > max_spread_cents + FLOAT_EPSILON:
        return None
    if entry_price < RESEARCH_MIN_ENTRY - FLOAT_EPSILON or entry_price > RESEARCH_MAX_ENTRY + FLOAT_EPSILON:
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
        strike=float(optional_float(parsed.get("floor")) or 0.0),
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


def parse_fill_details(signal: TradeSignal, response: dict) -> tuple[float | None, float | None, float | None, float | None, str | None]:
    order = order_from_response(response or {})
    fill_count = optional_float(order.get("fill_count_fp")) or optional_float(order.get("fill_count"))
    avg_yes = optional_float(order.get("average_fill_price"))
    avg_fee = optional_float(order.get("average_fee_paid"))
    total_fee = optional_float(order.get("taker_fees_dollars"))
    actual_entry = None
    actual_fee = None
    if fill_count and avg_yes is not None:
        actual_entry = avg_yes if signal.side == "yes" else 1.0 - avg_yes
        actual_fee = total_fee if total_fee is not None else (avg_fee or 0.0) * fill_count
    return fill_count, avg_yes, actual_entry, actual_fee, order.get("order_id")


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


def run_once(args, data_client: KalshiApi, trade_client: KalshiApi | None, conn: sqlite3.Connection, btc_1m):
    if args.mode == "live":
        status = data_client.get_exchange_status()
        if not status.get("exchange_active") or not status.get("trading_active"):
            raise RuntimeError(f"Exchange is not trading: {status}")

    btc_1m = refresh_btc_data_cached(btc_1m)
    events = scan_research_events(data_client)
    events = events[:1]
    signals, cache_by_event = find_signals(data_client=data_client, btc_1m=btc_1m, events=events)
    if not signals:
        log.info("no research signals passed filters")
        return btc_1m
    print_signals(signals, limit=args.print_candidates)

    now = datetime.now(timezone.utc)
    blocked = db_active_tickers(conn, now)
    local_active_exposure = db_active_exposure(conn, now)
    portfolio = None
    if args.mode == "live" and trade_client is not None:
        portfolio = get_portfolio_snapshot(trade_client)
        blocked.update(portfolio.active_tickers)
        log.info(
            "portfolio balance=$%.2f value=$%.2f active_exposure=$%.2f local_active=$%.2f",
            portfolio.available_balance,
            portfolio.portfolio_value,
            portfolio.market_exposure,
            local_active_exposure,
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

    candidates = [sig for sig in signals if sig.market_ticker not in blocked]
    selected = select_one_per_event(candidates, RESEARCH_MAX_SIGNALS_PER_CYCLE)
    if not selected:
        log.info("signals existed, but all were blocked by open position/dedupe checks")
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
            log.info("PAPER fill %s %s x%d @ %.4f", fresh.market_ticker, fresh.side.upper(), fresh.contracts, fresh.entry_price)
            record_trade(conn, args.mode, "paper_filled", fresh)
            spent_this_cycle += estimated_cost
        else:
            if trade_client is None:
                raise RuntimeError("trade client is required in live mode")
            client_order_id = f"research-{int(time.time())}-{uuid4().hex[:10]}"
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
            spent_this_cycle += estimated_cost if status in {"filled", "partial_filled"} else 0.0
    return btc_1m


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the deployed BTC 1-hour research strategy.")
    parser.add_argument("--dry-run", action="store_true", help="Read live data and print would-trade decisions only.")
    parser.add_argument("--paper", action="store_true", help="Record simulated fills using live orderbook prices.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle. Default is continuous live execution.")
    parser.add_argument("--interval-sec", type=int, default=RESEARCH_SCAN_INTERVAL_SEC)
    parser.add_argument("--print-candidates", type=int, default=10)
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
    return args


def validate_args(args: argparse.Namespace) -> None:
    if args.interval_sec < 15:
        raise ValueError("--interval-sec must be >= 15")


def main() -> None:
    args = parse_args()
    validate_args(args)
    log.info(
        "starting research executor mode=%s trade_env=%s series=%s train_days=%d contracts=%d interval=%ds log=%s",
        args.mode,
        args.trade_env,
        RESEARCH_SERIES,
        RESEARCH_TRAIN_DAYS,
        RESEARCH_MAX_CONTRACTS_PER_TRADE,
        args.interval_sec,
        LOG_FILE,
    )

    data_client = KalshiApi(env="prod", require_auth=True)
    trade_client = KalshiApi(env=args.trade_env, require_auth=True) if args.mode == "live" else None
    conn = db_connect(args.db_path)

    log.info("fetching initial BTC data days=%d", RESEARCH_BOOTSTRAP_BTC_DAYS)
    btc_1m = load_btc_history_cached(RESEARCH_BOOTSTRAP_BTC_DAYS)
    btc_1m = refresh_btc_data_cached(btc_1m)

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

    conn.close()


if __name__ == "__main__":
    main()
