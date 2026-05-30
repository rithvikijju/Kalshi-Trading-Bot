#!/usr/bin/env python3
"""Live executor for the BTC 15-minute low-drawdown momentum candidate.

Strategy: `lowdd_candidate_no_rv` from the BTC15M websocket validation.

Rules:
  * KXBTC15M current event only
  * target +$3 net win sizing, downsized by visible top book and $15 premium cap
  * TTL 4-5 minutes
  * spread <= 2c
  * entry 5-80c
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
import os
import queue
import re
import sqlite3
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
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
    DEFAULT_CAPTURE_WRITER,
    ReadableDuckCaptureWriter,
    event_from_market_ticker,
    event_summary,
    record_event_metadata,
    scan_btc15m_events,
)


SERIES_TICKER = "KXBTC15M"
LOWDD_STRATEGY = "lowdd"
H02_STRATEGY = "h02_fair_value_transfer"
STRATEGY_ALIASES = {
    "lowdd": LOWDD_STRATEGY,
    "btc15m_lowdd_no_rv": LOWDD_STRATEGY,
    "h02": H02_STRATEGY,
    "h02_fair_value_transfer": H02_STRATEGY,
    "fair_value": H02_STRATEGY,
}
SIGNAL_STRATEGY = STRATEGY_ALIASES.get(os.getenv("BTC15M_SIGNAL_STRATEGY", LOWDD_STRATEGY).strip().lower(), LOWDD_STRATEGY)
STRATEGY_NAME = "btc15m_h02_fair_value_transfer" if SIGNAL_STRATEGY == H02_STRATEGY else "btc15m_lowdd_no_rv"
MODE_LIVE = "live_btc15m_h02" if SIGNAL_STRATEGY == H02_STRATEGY else "live_btc15m_lowdd"
DEFAULT_CAPTURE_DB = Path(CFG["db_dir"]).expanduser() / "btc15m_live_capture.duckdb"
DEFAULT_TRADE_DB = Path(CFG["db_dir"]).expanduser() / "btc15m_lowdd_live_trades.db"

TTL_LO = 4.0
TTL_HI = 5.0
SPREAD_MAX_CENTS = 2.0
MKT2_THRESHOLD = 0.125
ABS3_CAP = 0.35
ENTRY_MIN = 0.05
ENTRY_MAX = 0.80
BTC_LOOKBACK_MIN = 3
MKT_LOOKBACK_MIN = 2
ABS_LOOKBACK_MIN = 3
MAX_CONTRACTS = 20
TARGET_WIN_DOLLARS = 3.0
MAX_PREMIUM_DOLLARS = 15.0
MAX_PER_MARKET_FRACTION = float(CFG.get("max_per_market", 0.20))
MAX_TOTAL_EXPOSURE_FRACTION = float(CFG.get("max_total_risk", 0.50))
SCAN_LOG_SEC = 30.0
MIN_RISK_REWARD = float(os.getenv("BTC15M_MIN_RISK_REWARD", "0.33"))
MAX_REPRICE_WORSE_CENTS = float(os.getenv("BTC15M_MAX_REPRICE_WORSE_CENTS", "2.0"))
ORDER_CHASE_COOLDOWN_SEC = float(os.getenv("BTC15M_ORDER_CHASE_COOLDOWN_SEC", "0.0"))
PORTFOLIO_CACHE_TTL_SEC = float(os.getenv("BTC15M_PORTFOLIO_CACHE_TTL_SEC", "30.0"))
PORTFOLIO_WARM_REFRESH_SEC = float(os.getenv("BTC15M_PORTFOLIO_WARM_REFRESH_SEC", "15.0"))
MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0
H02_TTL_LO = float(os.getenv("BTC15M_H02_TTL_LO", "2.0"))
H02_TTL_HI = float(os.getenv("BTC15M_H02_TTL_HI", "8.0"))
H02_SPREAD_MAX_CENTS = float(os.getenv("BTC15M_H02_SPREAD_MAX_CENTS", "2.0"))
H02_EDGE_THRESHOLD_CENTS = float(os.getenv("BTC15M_H02_EDGE_THRESHOLD_CENTS", "10.0"))
H02_ENTRY_MIN = float(os.getenv("BTC15M_H02_ENTRY_MIN", "0.01"))
H02_ENTRY_MAX = float(os.getenv("BTC15M_H02_ENTRY_MAX", "0.99"))
H02_MIN_SIDE_PROB = float(os.getenv("BTC15M_H02_MIN_SIDE_PROB", "0.0"))
H02_MIN_VISIBLE_QTY = float(os.getenv("BTC15M_H02_MIN_VISIBLE_QTY", "1.0"))
H02_FIRST_SIGNAL_MIN_VISIBLE_QTY = float(os.getenv("BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY", "0.0"))
H02_ALLOWED_SIDE = os.getenv("BTC15M_H02_ALLOWED_SIDE", "").strip().lower()
if H02_ALLOWED_SIDE not in {"", "yes", "no"}:
    raise ValueError("BTC15M_H02_ALLOWED_SIDE must be empty, 'yes', or 'no'")
H02_DEFAULT_ANNUAL_VOL = float(os.getenv("BTC15M_H02_DEFAULT_ANNUAL_VOL", "0.50"))
H02_MIN_ANNUAL_VOL = float(os.getenv("BTC15M_H02_MIN_ANNUAL_VOL", "0.05"))
H02_MAX_ANNUAL_VOL = float(os.getenv("BTC15M_H02_MAX_ANNUAL_VOL", "3.00"))
H02_BTC_MAX_AGE_SEC = float(os.getenv("BTC15M_H02_BTC_MAX_AGE_SEC", "10.0"))
H02_MAX_CONTRACTS = int(os.getenv("BTC15M_H02_MAX_CONTRACTS", "1"))
RISK_WINDOW_HOURS = float(os.getenv("BTC15M_RISK_WINDOW_HOURS", "24.0"))
ROLLING_TRADE_CAP = int(os.getenv("BTC15M_ROLLING_TRADE_CAP", "0"))
ROLLING_PREMIUM_CAP_DOLLARS = float(os.getenv("BTC15M_ROLLING_PREMIUM_CAP_DOLLARS", "0.0"))
MONEY_RE = re.compile(r"\$([0-9,]+(?:\.\d+)?)")
_PAPER_OFFICIAL_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class HistoryPoint:
    received_at_ns: int
    value: float


@dataclass
class PortfolioCache:
    snapshot: live.PortfolioSnapshot | None = None
    updated_at: float = 0.0


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


@dataclass
class MinuteBar:
    minute_ns: int
    open: float
    high: float
    low: float
    close: float


class BtcMinuteVolHistory:
    """Causal 1-minute BTC close history for h02 fair-value volatility."""

    def __init__(self, max_rows: int = 240) -> None:
        self.max_rows = int(max_rows)
        self.rows: deque[MinuteBar] = deque()

    def seed_from_kraken(self) -> int:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=3)
        try:
            candles = live.fetch_kraken_btc_candles(start, end)
        except Exception:
            log.exception("BTC15M h02 Kraken candle seed failed; using default vol until websocket history fills")
            return 0
        count = 0
        for row in candles.to_dict("records") if candles is not None and not candles.empty else []:
            ts = utc_dt(row.get("time"))
            close = row.get("close")
            if ts is None or close is None:
                continue
            minute_ns = int(ts.timestamp()) * 1_000_000_000
            price = float(close)
            if not math.isfinite(price) or price <= 0:
                continue
            self._upsert_bar(minute_ns, price, price, price, price)
            count += 1
        return count

    def add_tick(self, received_at_ns: int | None, spot: float | None) -> None:
        if received_at_ns is None or spot is None:
            return
        price = float(spot)
        if not math.isfinite(price) or price <= 0:
            return
        minute_ns = int(received_at_ns) // 60_000_000_000 * 60_000_000_000
        if self.rows and self.rows[-1].minute_ns == minute_ns:
            bar = self.rows[-1]
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            return
        self._upsert_bar(minute_ns, price, price, price, price)

    def _upsert_bar(self, minute_ns: int, open_: float, high: float, low: float, close: float) -> None:
        if self.rows and self.rows[-1].minute_ns == minute_ns:
            self.rows[-1] = MinuteBar(minute_ns, open_, high, low, close)
        else:
            self.rows.append(MinuteBar(minute_ns, open_, high, low, close))
        while len(self.rows) > self.max_rows:
            self.rows.popleft()

    def annual_vol(self) -> float:
        closes = [row.close for row in self.rows if row.close > 0]
        if len(closes) < 61:
            return H02_DEFAULT_ANNUAL_VOL
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        window = rets[-60:]
        if len(window) < 2:
            return H02_DEFAULT_ANNUAL_VOL
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / (len(window) - 1)
        vol = math.sqrt(max(0.0, var)) * math.sqrt(MINUTES_PER_YEAR)
        if not math.isfinite(vol):
            return H02_DEFAULT_ANNUAL_VOL
        return min(H02_MAX_ANNUAL_VOL, max(H02_MIN_ANNUAL_VOL, vol))


def normalize_strategy(value: str | None) -> str:
    key = str(value or "").strip().lower()
    if key not in STRATEGY_ALIASES:
        raise ValueError(f"unsupported BTC15M strategy {value!r}; expected one of {sorted(STRATEGY_ALIASES)}")
    return STRATEGY_ALIASES[key]


def set_signal_strategy(strategy: str) -> None:
    global SIGNAL_STRATEGY, STRATEGY_NAME, MODE_LIVE
    SIGNAL_STRATEGY = normalize_strategy(strategy)
    if SIGNAL_STRATEGY == H02_STRATEGY:
        STRATEGY_NAME = "btc15m_h02_fair_value_transfer"
        MODE_LIVE = "live_btc15m_h02"
    else:
        STRATEGY_NAME = "btc15m_lowdd_no_rv"
        MODE_LIVE = "live_btc15m_lowdd"


def paper_mode_name() -> str:
    return f"paper_{MODE_LIVE}"


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


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def market_floor_strike(market: dict[str, Any]) -> float | None:
    for text_key in ("yes_sub_title", "subtitle", "title"):
        text = market.get(text_key)
        if text:
            match = MONEY_RE.search(str(text))
            if match:
                value = as_float(match.group(1))
                if value is not None:
                    return value
    value = as_float(market.get("floor_strike"))
    if value is not None:
        return value
    custom = market.get("custom_strike")
    if isinstance(custom, dict):
        value = as_float(custom.get("floor_strike"))
        if value is not None:
            return value
    return None


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


def normal_survival(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def h02_p_yes(spot: float, strike: float, ttl_min: float, annual_vol: float) -> float:
    if spot <= 0 or strike <= 0 or ttl_min <= 0:
        return float("nan")
    vol = min(H02_MAX_ANNUAL_VOL, max(H02_MIN_ANNUAL_VOL, float(annual_vol or H02_DEFAULT_ANNUAL_VOL)))
    denom = vol * math.sqrt(max(ttl_min, 0.1) / MINUTES_PER_YEAR)
    if denom <= 0 or not math.isfinite(denom):
        return float("nan")
    z = math.log(strike / spot) / denom
    return min(0.999, max(0.001, normal_survival(z)))


def h02_side_metrics(side: str, entry: float, p_yes: float, fee: float) -> tuple[float, float, float]:
    side_p = p_yes if side == "yes" else 1.0 - p_yes
    gross_edge_cents = (side_p - entry) * 100.0
    net_edge_cents = gross_edge_cents - fee * 100.0
    return side_p, gross_edge_cents, net_edge_cents


def strategy_entry_bounds() -> tuple[float, float]:
    if SIGNAL_STRATEGY == H02_STRATEGY:
        return H02_ENTRY_MIN, H02_ENTRY_MAX
    return ENTRY_MIN, ENTRY_MAX


def strategy_spread_max_cents() -> float:
    return H02_SPREAD_MAX_CENTS if SIGNAL_STRATEGY == H02_STRATEGY else SPREAD_MAX_CENTS


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
            "reason": STRATEGY_NAME,
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


def get_cached_portfolio(
    trade_client: live.KalshiApi,
    cache: PortfolioCache,
    *,
    force: bool = False,
) -> live.PortfolioSnapshot:
    now = time.monotonic()
    if force or cache.snapshot is None or now - cache.updated_at >= PORTFOLIO_CACHE_TTL_SEC:
        cache.snapshot = live.get_portfolio_snapshot(trade_client)
        cache.updated_at = now
    return cache.snapshot


def btc_close_at_or_before(history: BtcMinuteVolHistory, close_time: datetime) -> float | None:
    close_ns = int(close_time.timestamp()) * 1_000_000_000
    for row in reversed(history.rows):
        if row.minute_ns <= close_ns:
            return row.close
    return None


def paper_official_market_result(ticker: str, ttl_sec: float = 60.0) -> dict[str, Any]:
    cached = _PAPER_OFFICIAL_RESULT_CACHE.get(ticker)
    now_monotonic = time.monotonic()
    if cached is not None:
        cached_at, market = cached
        result = str(market.get("result") or "").lower()
        if result in {"yes", "no"} or now_monotonic - cached_at < ttl_sec:
            return market
    try:
        response = requests.get(f"{live.KalshiApi.PROD_URL}/markets/{ticker}", timeout=10)
        if response.status_code == 404:
            market = {"fetch_error": "404_not_found"}
        else:
            response.raise_for_status()
            market = response.json().get("market") or {}
    except Exception as exc:
        market = {"fetch_error": repr(exc)}
    _PAPER_OFFICIAL_RESULT_CACHE[ticker] = (now_monotonic, market)
    return market


def paper_shadow_summary(
    conn: sqlite3.Connection,
    btc_history: BtcMinuteVolHistory,
    starting_bankroll: float,
    *,
    mode: str | None = None,
    now: datetime | None = None,
) -> dict[str, float | int]:
    mode = mode or paper_mode_name()
    now = now or datetime.now(timezone.utc)
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
    active_tickers: set[str] = set()
    for row in rows:
        contracts = int(row["contracts"] or 0)
        entry = live.optional_float(row["actual_entry_price"]) or live.optional_float(row["entry_price"]) or 0.0
        fee = live.optional_float(row["actual_fee_paid"])
        if fee is None:
            fee = live.optional_float(row["entry_fee_estimate"]) or 0.0
        premium = entry * contracts + fee
        close_time = live.utc_dt(row["close_time"])
        if close_time is None or close_time > now:
            active_exposure += premium
            open_trades += 1
            active_tickers.add(str(row["market_ticker"]).upper())
            continue
        market = paper_official_market_result(str(row["market_ticker"]))
        result = str(market.get("result") or "").lower()
        if result in {"yes", "no"}:
            yes_wins = result == "yes"
        else:
            settlement_spot = btc_close_at_or_before(btc_history, close_time)
            strike = live.market_strike_from_ticker(row["market_ticker"])
            if settlement_spot is None or strike is None:
                active_exposure += premium
                open_trades += 1
                active_tickers.add(str(row["market_ticker"]).upper())
                continue
            yes_wins = settlement_spot >= strike
        won = (row["side"] == "yes" and yes_wins) or (row["side"] == "no" and not yes_wins)
        pnl = (float(contracts) if won else 0.0) - premium
        realized_pnl += pnl
        settled_premium += premium
        settled_trades += 1
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    equity = float(starting_bankroll) + realized_pnl
    return {
        "starting_bankroll": float(starting_bankroll),
        "equity": float(equity),
        "available_balance": float(equity - active_exposure),
        "realized_pnl": float(realized_pnl),
        "return_on_bankroll": float(realized_pnl / starting_bankroll) if starting_bankroll > 0 else 0.0,
        "settled_premium": float(settled_premium),
        "return_on_premium": float(realized_pnl / settled_premium) if settled_premium > 0 else 0.0,
        "active_exposure": float(active_exposure),
        "total_trades": int(len(rows)),
        "settled_trades": int(settled_trades),
        "open_trades": int(open_trades),
        "wins": int(wins),
        "losses": int(losses),
        "active_tickers": active_tickers,
    }


def paper_shadow_portfolio_snapshot(
    conn: sqlite3.Connection,
    btc_history: BtcMinuteVolHistory,
    starting_bankroll: float,
) -> live.PortfolioSnapshot:
    summary = paper_shadow_summary(conn, btc_history, starting_bankroll)
    return live.PortfolioSnapshot(
        available_balance=max(0.0, float(summary["available_balance"])),
        portfolio_value=max(0.0, float(summary["equity"])),
        active_tickers=set(summary["active_tickers"]),
        market_exposure=float(summary["active_exposure"]),
    )


def log_paper_shadow_summary(
    conn: sqlite3.Connection,
    btc_history: BtcMinuteVolHistory,
    starting_bankroll: float,
    *,
    label: str = "BTC15M PAPER",
) -> None:
    summary = paper_shadow_summary(conn, btc_history, starting_bankroll)
    log.info(
        "%s report strategy=%s start=$%.2f equity=$%.2f realized_pnl=$%.2f bankroll_return=%.3f%% "
        "settled_premium=$%.2f premium_return=%.3f%% trades=%d settled=%d open=%d wins=%d losses=%d "
        "active_exposure=$%.2f available=$%.2f",
        label,
        STRATEGY_NAME,
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


def mark_portfolio_cache_stale(cache: PortfolioCache) -> None:
    cache.updated_at = 0.0


def signal_cooldown_detail(cooldowns: dict[str, float], event_ticker: str | None) -> str | None:
    if ORDER_CHASE_COOLDOWN_SEC <= 0:
        return None
    key = str(event_ticker or "").upper()
    if not key:
        return None
    now = time.monotonic()
    until = cooldowns.get(key, 0.0)
    remaining = until - now
    if remaining <= 0:
        cooldowns.pop(key, None)
        return None
    return f"order_chase_cooldown remaining={remaining:.1f}s"


def mark_signal_cooldown(cooldowns: dict[str, float], event_ticker: str | None) -> None:
    if ORDER_CHASE_COOLDOWN_SEC <= 0:
        return
    key = str(event_ticker or "").upper()
    if not key:
        return
    cooldowns[key] = max(cooldowns.get(key, 0.0), time.monotonic() + ORDER_CHASE_COOLDOWN_SEC)


def reprice_signal_from_hot_book(
    state: live.LiveMarketState,
    signal: live.TradeSignal,
    btc_vol_history: BtcMinuteVolHistory | None = None,
) -> tuple[live.TradeSignal | None, str]:
    books = latest_books(state)
    quote_pack = books.get(signal.market_ticker.upper())
    if quote_pack is None:
        return None, "reprice_missing_book"
    quote, quote_ns = quote_pack
    if quote.yes_bid is None or quote.yes_ask is None or quote.no_bid is None or quote.no_ask is None:
        return None, "reprice_incomplete_book"

    spread_cents = (float(quote.yes_ask) - float(quote.yes_bid)) * 100.0
    spread_max = strategy_spread_max_cents()
    if spread_cents < -1e-9 or spread_cents > spread_max + 1e-9:
        return None, f"reprice_spread_{spread_cents:.2f}c"

    if signal.side == "yes":
        entry = float(quote.yes_ask)
        available_qty = float(quote.yes_ask_qty)
        yes_order_side = "bid"
        yes_limit_price = float(quote.yes_ask)
    else:
        entry = float(quote.no_ask)
        available_qty = float(quote.no_ask_qty)
        yes_order_side = "ask"
        yes_limit_price = float(quote.yes_bid)

    entry_min, entry_max = strategy_entry_bounds()
    if entry < entry_min or entry > entry_max:
        return None, f"reprice_entry_{entry:.3f}"
    if available_qty < 1:
        return None, f"reprice_top_qty_{available_qty:.2f}"

    worse_cents = (entry - float(signal.entry_price)) * 100.0
    if worse_cents > MAX_REPRICE_WORSE_CENTS + 1e-9:
        return None, f"reprice_worse_{worse_cents:.2f}c>{MAX_REPRICE_WORSE_CENTS:.2f}c"

    fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
    model_p_yes = signal.model_p_yes
    edge_gross_cents = signal.edge_gross_cents
    net_edge_cents = signal.edge_gross_cents - fee * 100.0
    threshold_cents = signal.edge_threshold_cents
    if SIGNAL_STRATEGY == H02_STRATEGY:
        spot_now, spot_ns = latest_spot(state)
        if spot_now is None or spot_ns is None:
            return None, "reprice_missing_btc_spot"
        if live.utc_now_ns() - int(spot_ns) > int(H02_BTC_MAX_AGE_SEC * 1_000_000_000):
            return None, "reprice_stale_btc_spot"
        vol = btc_vol_history.annual_vol() if btc_vol_history else H02_DEFAULT_ANNUAL_VOL
        p_yes = h02_p_yes(float(spot_now), float(signal.strike), float(signal.ttl_min), vol)
        if not math.isfinite(p_yes):
            return None, "reprice_bad_h02_probability"
        _side_p, edge_gross_cents, net_edge_cents = h02_side_metrics(signal.side, entry, p_yes, fee)
        model_p_yes = p_yes
        threshold_cents = H02_EDGE_THRESHOLD_CENTS
        if net_edge_cents < H02_EDGE_THRESHOLD_CENTS - 1e-12:
            return None, f"reprice_h02_edge_{net_edge_cents:.2f}c<thr_{H02_EDGE_THRESHOLD_CENTS:.2f}c"
    else:
        rr = risk_reward_ratio(entry)
        if rr < MIN_RISK_REWARD - 1e-12:
            return None, f"reprice_rr_{rr:.3f}<min_{MIN_RISK_REWARD:.3f}"

    signal_received_at_ns = live.utc_now_ns()
    quote_received_at_ns = int(quote_ns)
    fresh = replace(
        signal,
        contracts=1,
        entry_price=entry,
        yes_order_side=yes_order_side,
        yes_limit_price=yes_limit_price,
        available_qty=available_qty,
        model_p_yes=model_p_yes,
        edge_gross_cents=edge_gross_cents,
        entry_fee=fee,
        net_edge_cents=net_edge_cents,
        edge_threshold_cents=threshold_cents,
        spread_cents=spread_cents,
        yes_bid=quote.yes_bid,
        yes_ask=quote.yes_ask,
        no_bid=quote.no_bid,
        no_ask=quote.no_ask,
        signal_received_at_ns=signal_received_at_ns,
        quote_received_at_ns=quote_received_at_ns,
        quote_age_ms=max(0.0, (signal_received_at_ns - quote_received_at_ns) / 1_000_000.0),
        top_visible_qty=available_qty,
    )
    return fresh, f"reprice_ok worse={max(0.0, worse_cents):.2f}c top_qty={available_qty:.2f}"


def cost_for_contracts(entry_price: float, contracts: int) -> float:
    if contracts <= 0:
        return 0.0
    return contracts * float(entry_price) + kalshi_fee_dollars(float(entry_price), contracts=contracts, liquidity="taker")


def win_profit_for_contracts(entry_price: float, contracts: int) -> float:
    if contracts <= 0:
        return 0.0
    return contracts * (1.0 - float(entry_price)) - kalshi_fee_dollars(float(entry_price), contracts=contracts, liquidity="taker")


def target_contracts_for_net_win(entry_price: float, target_win: float) -> int | None:
    for contracts in range(1, MAX_CONTRACTS + 1):
        if win_profit_for_contracts(entry_price, contracts) >= target_win:
            return contracts
    return None


def max_contracts_for_budget(entry_price: float, budget: float) -> int:
    chosen = 0
    for contracts in range(1, MAX_CONTRACTS + 1):
        if cost_for_contracts(entry_price, contracts) <= budget + 1e-9:
            chosen = contracts
        else:
            break
    return chosen


def risk_reward_ratio(entry_price: float) -> float:
    fee = kalshi_fee_dollars(float(entry_price), contracts=1, liquidity="taker")
    risk = float(entry_price) + fee
    reward = 1.0 - float(entry_price) - fee
    if risk <= 0.0:
        return 0.0
    return reward / risk


def mode_for_risk_book(mode: str) -> str:
    return paper_mode_name() if mode == "paper" else MODE_LIVE


def rolling_risk_summary(conn: sqlite3.Connection, mode: str, now: datetime) -> dict[str, float | int | str]:
    if RISK_WINDOW_HOURS <= 0:
        cutoff = now
    else:
        cutoff = now - timedelta(hours=RISK_WINDOW_HOURS)
    rows = conn.execute(
        """
        SELECT
            COUNT(*) AS at_risk_trades,
            COALESCE(SUM(entry_price * contracts + entry_fee_estimate), 0.0) AS at_risk_premium
        FROM research_live_trades
        WHERE mode = ?
          AND status IN ('submitted', 'filled', 'partial_filled')
          AND created_at >= ?
        """,
        (mode, cutoff.isoformat()),
    ).fetchone()
    return {
        "mode": mode,
        "window_hours": float(RISK_WINDOW_HOURS),
        "cutoff": cutoff.isoformat(),
        "at_risk_trades": int(rows["at_risk_trades"] or 0),
        "at_risk_premium": float(rows["at_risk_premium"] or 0.0),
    }


def rolling_risk_rejection(
    conn: sqlite3.Connection,
    mode: str,
    now: datetime,
    additional_cost: float,
) -> tuple[str | None, dict[str, float | int | str]]:
    summary = rolling_risk_summary(conn, mode, now)
    at_risk_trades = int(summary["at_risk_trades"])
    at_risk_premium = float(summary["at_risk_premium"])
    if ROLLING_TRADE_CAP > 0 and at_risk_trades >= ROLLING_TRADE_CAP:
        return f"rolling_trade_cap_{at_risk_trades}>={ROLLING_TRADE_CAP}", summary
    if ROLLING_PREMIUM_CAP_DOLLARS > 0 and at_risk_premium + additional_cost > ROLLING_PREMIUM_CAP_DOLLARS + 1e-9:
        return (
            f"rolling_premium_cap_{at_risk_premium + additional_cost:.2f}>{ROLLING_PREMIUM_CAP_DOLLARS:.2f}",
            summary,
        )
    return None, summary


def choose_contracts_for_signal(
    signal: live.TradeSignal,
    portfolio: live.PortfolioSnapshot,
    local_active_exposure: float,
) -> tuple[live.TradeSignal | None, float, str]:
    bankroll = max(float(portfolio.portfolio_value), float(portfolio.available_balance), 0.0)
    active_exposure = max(float(portfolio.market_exposure), float(local_active_exposure), 0.0)
    per_market_budget = bankroll * MAX_PER_MARKET_FRACTION if bankroll > 0 else 0.0
    total_budget_left = max(0.0, bankroll * MAX_TOTAL_EXPOSURE_FRACTION - active_exposure)
    budget = min(MAX_PREMIUM_DOLLARS, float(portfolio.available_balance), per_market_budget, total_budget_left)
    visible_contracts = int(math.floor(float(signal.available_qty) + 1e-6))
    if SIGNAL_STRATEGY == H02_STRATEGY:
        budget_contracts = max_contracts_for_budget(signal.entry_price, budget)
        contracts = min(max(0, H02_MAX_CONTRACTS), visible_contracts, budget_contracts)
        estimated_cost = cost_for_contracts(signal.entry_price, contracts)
        log.info(
            "BTC15M sizing h02_flat %s %s entry=%.4f max_contracts=%d top_qty=%.2f budget=$%.2f "
            "contracts=%d cost=$%.2f portfolio=$%.2f available=$%.2f active_exposure=$%.2f",
            signal.market_ticker,
            signal.side.upper(),
            signal.entry_price,
            H02_MAX_CONTRACTS,
            signal.available_qty,
            budget,
            contracts,
            estimated_cost,
            portfolio.portfolio_value,
            portfolio.available_balance,
            active_exposure,
        )
        if contracts <= 0:
            return None, estimated_cost, "h02_budget_or_liquidity_zero"
        return live.resize_signal(signal, contracts), estimated_cost, "h02_flat"

    target_contracts = target_contracts_for_net_win(signal.entry_price, TARGET_WIN_DOLLARS)
    if target_contracts is None:
        return None, 0.0, "target_not_reachable_with_max_contracts"
    budget_contracts = max_contracts_for_budget(signal.entry_price, budget)
    contracts = min(target_contracts, visible_contracts, budget_contracts, MAX_CONTRACTS)
    estimated_cost = cost_for_contracts(signal.entry_price, contracts)
    win_profit = win_profit_for_contracts(signal.entry_price, contracts)
    log.info(
        "BTC15M sizing target_win_cap %s %s entry=%.4f target=$%.2f max_premium=$%.2f "
        "contracts=%d target_contracts=%d top_qty=%.2f budget=$%.2f cost=$%.2f win_if_win=$%.2f "
        "portfolio=$%.2f available=$%.2f active_exposure=$%.2f",
        signal.market_ticker,
        signal.side.upper(),
        signal.entry_price,
        TARGET_WIN_DOLLARS,
        MAX_PREMIUM_DOLLARS,
        contracts,
        target_contracts,
        signal.available_qty,
        budget,
        estimated_cost,
        win_profit,
        portfolio.portfolio_value,
        portfolio.available_balance,
        active_exposure,
    )
    if contracts <= 0:
        return None, estimated_cost, "budget_or_liquidity_zero"
    sized = live.resize_signal(signal, contracts)
    if contracts < target_contracts:
        if visible_contracts < target_contracts:
            reason = "target_win_downsized_top_book"
        elif budget_contracts < target_contracts:
            reason = "target_win_downsized_budget"
        else:
            reason = "target_win_downsized_contract_cap"
    else:
        reason = "target_win"
    return sized, estimated_cost, reason


def build_lowdd_signal(
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
    if available_qty < 1:
        return None, f"top_qty_{available_qty:.2f}"

    fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
    rr = risk_reward_ratio(entry)
    if rr < MIN_RISK_REWARD - 1e-12:
        return None, f"rr_{rr:.3f}<min_{MIN_RISK_REWARD:.3f}"
    gross_edge_cents = abs(chg2) * 100.0
    model_p_yes = side_probability_proxy(side, entry, chg2)
    signal_received_at_ns = live.utc_now_ns()
    signal = live.TradeSignal(
        event_ticker=str(event.get("event_ticker") or "").upper(),
        market_ticker=market_ticker,
        side=side,
        contracts=1,
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
        signal_received_at_ns=signal_received_at_ns,
        quote_received_at_ns=quote_ns,
        quote_age_ms=max(0.0, (signal_received_at_ns - int(quote_ns)) / 1_000_000.0),
        top_visible_qty=available_qty,
    )
    return signal, f"pass chg2={chg2:.3f} chg3={chg3:.3f} btc3={btc_ret3_bps:.2f}bps"


def build_h02_signal(
    *,
    event: dict[str, Any],
    market: dict[str, Any],
    quote: live.BookQuote,
    btc_vol_history: BtcMinuteVolHistory,
    state: live.LiveMarketState,
    quote_ns: int | None = None,
) -> tuple[live.TradeSignal | None, str]:
    close_time = utc_dt(event.get("close_time"))
    if close_time is None:
        return None, "missing_close_time"
    now = datetime.now(timezone.utc)
    ttl_min = (close_time - now).total_seconds() / 60.0
    if ttl_min < H02_TTL_LO or ttl_min > H02_TTL_HI:
        return None, f"h02_ttl_outside_{ttl_min:.2f}"
    if quote.yes_bid is None or quote.yes_ask is None or quote.no_bid is None or quote.no_ask is None:
        return None, "h02_incomplete_book"
    spread_cents = (float(quote.yes_ask) - float(quote.yes_bid)) * 100.0
    if spread_cents < -1e-9 or spread_cents > H02_SPREAD_MAX_CENTS + 1e-9:
        return None, f"h02_spread_{spread_cents:.2f}c"

    spot_now, spot_ns = latest_spot(state)
    if spot_now is None or spot_ns is None:
        return None, "h02_missing_btc_spot"
    if live.utc_now_ns() - int(spot_ns) > int(H02_BTC_MAX_AGE_SEC * 1_000_000_000):
        return None, "h02_stale_btc_spot"
    strike = market_floor_strike(market)
    if strike is None or strike <= 0:
        return None, "h02_missing_strike"

    annual_vol = btc_vol_history.annual_vol()
    p_yes = h02_p_yes(float(spot_now), float(strike), ttl_min, annual_vol)
    if not math.isfinite(p_yes):
        return None, "h02_bad_probability"

    market_ticker = str(market.get("ticker") or quote.ticker).upper()
    candidates: list[tuple[str, float, float, str, float, float, float, float]] = []
    rejection_counts = {"entry": 0, "qty": 0, "edge": 0}
    evaluated_sides = 0
    side_snapshots: list[str] = []
    for side in ("yes", "no"):
        if H02_ALLOWED_SIDE and side != H02_ALLOWED_SIDE:
            continue
        evaluated_sides += 1
        if side == "yes":
            entry = float(quote.yes_ask)
            available_qty = float(quote.yes_ask_qty)
            yes_order_side = "bid"
            yes_limit_price = float(quote.yes_ask)
        else:
            entry = float(quote.no_ask)
            available_qty = float(quote.no_ask_qty)
            yes_order_side = "ask"
            yes_limit_price = float(quote.yes_bid)
        side_snapshots.append(f"{side}_entry={entry:.3f} {side}_qty={available_qty:.2f}")
        if entry < H02_ENTRY_MIN or entry > H02_ENTRY_MAX:
            rejection_counts["entry"] += 1
            continue
        if available_qty < H02_MIN_VISIBLE_QTY:
            rejection_counts["qty"] += 1
            continue
        fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
        side_p, edge_gross_cents, net_edge_cents = h02_side_metrics(side, entry, p_yes, fee)
        if side_p >= H02_MIN_SIDE_PROB and net_edge_cents >= H02_EDGE_THRESHOLD_CENTS:
            candidates.append((side, entry, available_qty, yes_order_side, yes_limit_price, fee, edge_gross_cents, net_edge_cents))
        else:
            rejection_counts["edge"] += 1

    if not candidates:
        return None, (
            f"h02_no_edge p_yes={p_yes:.3f} min_side_p={H02_MIN_SIDE_PROB:.3f} "
            f"edge_min={H02_EDGE_THRESHOLD_CENTS:.2f}c "
            f"entry_min={H02_ENTRY_MIN:.3f} entry_max={H02_ENTRY_MAX:.3f} "
            f"min_qty={H02_MIN_VISIBLE_QTY:.2f} checked_sides={evaluated_sides} "
            f"entry_rejects={rejection_counts['entry']} qty_rejects={rejection_counts['qty']} "
            f"edge_rejects={rejection_counts['edge']} allowed_side={H02_ALLOWED_SIDE or 'both'} "
            f"{' '.join(side_snapshots)} strike={strike:.2f} spot={float(spot_now):.2f} vol={annual_vol:.3f}"
        )

    # The backtest takes the first qualifying side per event. Both sides cannot
    # have positive net fair edge at the same quote under a normal crossed-free
    # complementary book, but sorting keeps this deterministic.
    side, entry, available_qty, yes_order_side, yes_limit_price, fee, edge_gross_cents, net_edge_cents = sorted(candidates, key=lambda row: row[0])[0]
    if H02_FIRST_SIGNAL_MIN_VISIBLE_QTY > H02_MIN_VISIBLE_QTY and available_qty < H02_FIRST_SIGNAL_MIN_VISIBLE_QTY:
        return None, (
            f"h02_first_signal_skip qty={available_qty:.2f}<first_min_qty={H02_FIRST_SIGNAL_MIN_VISIBLE_QTY:.2f} "
            f"side={side} entry={entry:.3f} net_edge={net_edge_cents:.2f}c"
        )
    signal_received_at_ns = live.utc_now_ns()
    quote_received_at_ns = int(
        quote_ns
        if quote_ns is not None
        else (quote.received_at_ns if quote.received_at_ns is not None else signal_received_at_ns)
    )
    signal = live.TradeSignal(
        event_ticker=str(event.get("event_ticker") or "").upper(),
        market_ticker=market_ticker,
        side=side,
        contracts=1,
        entry_price=entry,
        yes_order_side=yes_order_side,
        yes_limit_price=yes_limit_price,
        available_qty=available_qty,
        model_p_yes=p_yes,
        edge_gross_cents=edge_gross_cents,
        entry_fee=fee,
        net_edge_cents=net_edge_cents,
        edge_threshold_cents=H02_EDGE_THRESHOLD_CENTS,
        spread_cents=spread_cents,
        strike=float(strike),
        btc_spot=float(spot_now),
        ttl_min=ttl_min,
        close_time=close_time.isoformat(),
        yes_bid=quote.yes_bid,
        yes_ask=quote.yes_ask,
        no_bid=quote.no_bid,
        no_ask=quote.no_ask,
        signal_received_at_ns=signal_received_at_ns,
        quote_received_at_ns=quote_received_at_ns,
        quote_age_ms=max(0.0, (signal_received_at_ns - quote_received_at_ns) / 1_000_000.0),
        top_visible_qty=available_qty,
    )
    return signal, (
        f"h02_pass p_yes={p_yes:.3f} side={side} entry={entry:.3f} "
        f"net_edge={net_edge_cents:.2f}c min_side_p={H02_MIN_SIDE_PROB:.3f} "
        f"min_qty={H02_MIN_VISIBLE_QTY:.2f} strike={strike:.2f} spot={float(spot_now):.2f} vol={annual_vol:.3f}"
    )


def build_signal(
    *,
    event: dict[str, Any],
    market: dict[str, Any],
    quote: live.BookQuote,
    quote_ns: int,
    mid_history: AsofHistory,
    btc_history: AsofHistory,
    btc_vol_history: BtcMinuteVolHistory,
    state: live.LiveMarketState,
) -> tuple[live.TradeSignal | None, str]:
    if SIGNAL_STRATEGY == H02_STRATEGY:
        return build_h02_signal(
            event=event,
            market=market,
            quote=quote,
            quote_ns=quote_ns,
            btc_vol_history=btc_vol_history,
            state=state,
        )
    return build_lowdd_signal(
        event=event,
        market=market,
        quote=quote,
        quote_ns=quote_ns,
        mid_history=mid_history,
        btc_history=btc_history,
        state=state,
    )


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("event_ticker") or "").upper()


def h02_event_ttl_reject_detail(event: dict[str, Any], *, now: datetime | None = None) -> str | None:
    close_time = utc_dt(event.get("close_time"))
    if close_time is None:
        return "missing_close_time"
    now_dt = now or datetime.now(timezone.utc)
    ttl_min = (close_time - now_dt).total_seconds() / 60.0
    if ttl_min < H02_TTL_LO or ttl_min > H02_TTL_HI:
        return f"h02_ttl_outside_{ttl_min:.2f}"
    return None


def batch_changed_tickers(item: dict[str, Any]) -> set[str]:
    values = item.get("changed_tickers", item.get("tickers", set()))
    return {str(ticker).upper() for ticker in values if str(ticker).strip()}


def run_once(
    *,
    mode: str,
    state: live.LiveMarketState,
    recorder: live.LiveCaptureWriter,
    conn: sqlite3.Connection,
    trade_client: live.KalshiApi,
    portfolio_cache: PortfolioCache,
    mid_history: AsofHistory,
    btc_history: AsofHistory,
    btc_vol_history: BtcMinuteVolHistory,
    changed_tickers: set[str],
    last_no_signal_log: dict[str, float],
    signal_cooldowns: dict[str, float],
    event_skip_locks: dict[str, str],
    shadow_bankroll: float = 0.0,
) -> None:
    scan_perf = time.perf_counter()
    started_ns = live.utc_now_ns()
    spot, spot_ns = latest_spot(state)
    if spot is not None and spot_ns is not None:
        btc_history.add("BTC", spot_ns, spot)
        btc_vol_history.add_tick(spot_ns, spot)

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

    if SIGNAL_STRATEGY == H02_STRATEGY:
        ttl_reject = h02_event_ttl_reject_detail(event)
        if ttl_reject:
            record_scan(recorder, started_ns, current_event, len(changed_tickers), len(quotes), scan_spot, "none", ttl_reject)
            return

    books = latest_books(state)
    if SIGNAL_STRATEGY != H02_STRATEGY:
        for ticker, (quote, quote_ns) in books.items():
            mid_history.add(ticker, quote_ns, quote_mid(quote))

    blocked_events = live.db_active_events(conn, datetime.now(timezone.utc))
    if current_event in blocked_events:
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(quotes), scan_spot, "blocked", "event_already_traded")
        return
    if current_event in event_skip_locks:
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(quotes), scan_spot, "blocked", event_skip_locks[current_event])
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
            btc_vol_history=btc_vol_history,
            state=state,
        )
        reject_detail = detail
        if signal is not None:
            best_signal = signal
            best_detail = detail
            break

    if best_signal is None:
        if current_event and reject_detail.startswith("h02_first_signal_skip"):
            event_skip_locks[current_event] = reject_detail
            log.info("BTC15M event skip lock %s detail=%s", current_event, reject_detail)
        now = time.monotonic()
        if now - last_no_signal_log.get("ts", 0.0) >= SCAN_LOG_SEC:
            log.info("BTC15M no %s signal event=%s detail=%s evaluated=%d", STRATEGY_NAME, current_event, reject_detail, len(books))
            last_no_signal_log["ts"] = now
        record_scan(recorder, started_ns, current_event, len(changed_tickers), len(books), scan_spot, "none", reject_detail)
        return

    record_scan(recorder, started_ns, current_event, len(changed_tickers), len(books), scan_spot, "selected", best_detail, best_signal)
    original_entry_price = float(best_signal.entry_price)

    cooldown_detail = signal_cooldown_detail(signal_cooldowns, best_signal.event_ticker)
    if cooldown_detail:
        log.info("skip %s: %s", best_signal.market_ticker, cooldown_detail)
        record_decision(recorder, best_signal, "skip", cooldown_detail, None, None, None)
        return

    fresh_signal, reprice_detail = reprice_signal_from_hot_book(state, best_signal, btc_vol_history)
    if fresh_signal is None:
        mark_signal_cooldown(signal_cooldowns, best_signal.event_ticker)
        log.info("skip %s: failed hot-book reprice/filter %s", best_signal.market_ticker, reprice_detail)
        record_decision(recorder, best_signal, "skip", reprice_detail, None, None, None)
        return
    best_signal = fresh_signal
    best_detail = f"{best_detail}; {reprice_detail}"

    portfolio_perf = time.perf_counter()
    if mode == "paper" and shadow_bankroll > 0:
        portfolio = paper_shadow_portfolio_snapshot(conn, btc_vol_history, shadow_bankroll)
    else:
        portfolio = get_cached_portfolio(trade_client, portfolio_cache)
    portfolio_ms = (time.perf_counter() - portfolio_perf) * 1000.0
    if best_signal.market_ticker in portfolio.active_tickers:
        log.info("skip %s: already active in Kalshi portfolio", best_signal.market_ticker)
        record_decision(recorder, best_signal, "skip", "portfolio_active_ticker", portfolio, None, None)
        return

    reprice_reference = replace(best_signal, contracts=1, entry_price=original_entry_price)
    fresh_signal, final_reprice_detail = reprice_signal_from_hot_book(state, reprice_reference, btc_vol_history)
    if fresh_signal is None:
        mark_signal_cooldown(signal_cooldowns, best_signal.event_ticker)
        log.info("skip %s: failed final hot-book reprice/filter %s", best_signal.market_ticker, final_reprice_detail)
        record_decision(recorder, best_signal, "skip", final_reprice_detail, portfolio, None, None)
        return
    best_signal = fresh_signal
    best_detail = f"{best_detail}; final_{final_reprice_detail}"

    local_active_exposure = live.db_active_exposure(conn, datetime.now(timezone.utc))
    sized_signal, sizing_cost, sizing_reason = choose_contracts_for_signal(best_signal, portfolio, local_active_exposure)
    if sized_signal is None:
        log.info("skip %s: sizing blocked reason=%s", best_signal.market_ticker, sizing_reason)
        record_decision(recorder, best_signal, "skip", f"sizing_{sizing_reason}", portfolio, sizing_cost, None)
        return
    best_signal = sized_signal
    estimated_cost = best_signal.contracts * best_signal.entry_price + best_signal.entry_fee
    if estimated_cost > portfolio.available_balance:
        log.info("skip %s: cost %.2f > available %.2f", best_signal.market_ticker, estimated_cost, portfolio.available_balance)
        record_decision(recorder, best_signal, "skip", "insufficient_available_balance", portfolio, estimated_cost, None)
        return
    risk_reason, risk_summary = rolling_risk_rejection(
        conn,
        mode_for_risk_book(mode),
        datetime.now(timezone.utc),
        estimated_cost,
    )
    if risk_reason:
        log.info(
            "skip %s: risk cap %s window=%.1fh at_risk_trades=%d at_risk_premium=$%.2f additional=$%.2f",
            best_signal.market_ticker,
            risk_reason,
            float(risk_summary["window_hours"]),
            int(risk_summary["at_risk_trades"]),
            float(risk_summary["at_risk_premium"]),
            estimated_cost,
        )
        record_decision(recorder, best_signal, "skip", f"risk_cap_{risk_reason}", portfolio, estimated_cost, None)
        return
    scan_to_ready_ms = (time.perf_counter() - scan_perf) * 1000.0

    if mode == "dry-run":
        log.info(
            "BTC15M DRY RUN would FOK %s %s x%d entry=%.4f yes_book_%s yes_price=%.4f ready_ms=%.1f portfolio_ms=%.1f %s",
            best_signal.market_ticker,
            best_signal.side.upper(),
            best_signal.contracts,
            best_signal.entry_price,
            best_signal.yes_order_side,
            best_signal.yes_limit_price,
            scan_to_ready_ms,
            portfolio_ms,
            best_detail,
        )
        record_decision(recorder, best_signal, "dry_run", "would_trade", portfolio, estimated_cost, None)
        return

    client_order_id = f"btc15m-{int(time.time())}-{uuid4().hex[:10]}"
    if not live.acquire_event_lock(conn, best_signal, MODE_LIVE, client_order_id=client_order_id):
        log.info("skip %s: event lock already held", best_signal.market_ticker)
        record_decision(recorder, best_signal, "skip", "event_lock_held", portfolio, estimated_cost, client_order_id)
        return

    if mode == "paper":
        live.record_trade(conn, paper_mode_name(), "paper_filled", best_signal, client_order_id=client_order_id)
        record_decision(recorder, best_signal, "paper_fill", "filled", portfolio, estimated_cost, client_order_id)
        log.info(
            "BTC15M PAPER fill %s %s x%d entry=%.4f yes_book_%s yes_price=%.4f ready_ms=%.1f portfolio_ms=%.1f client_order_id=%s %s",
            best_signal.market_ticker,
            best_signal.side.upper(),
            best_signal.contracts,
            best_signal.entry_price,
            best_signal.yes_order_side,
            best_signal.yes_limit_price,
            scan_to_ready_ms,
            portfolio_ms,
            client_order_id,
            best_detail,
        )
        return

    trade_id = live.record_trade(conn, MODE_LIVE, "submitted", best_signal, client_order_id=client_order_id)
    submit_perf = time.perf_counter()
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
            submit_ms = (time.perf_counter() - submit_perf) * 1000.0
            live.update_trade_response(
                conn,
                trade_id,
                "not_filled",
                best_signal,
                {"error": {"code": "fill_or_kill_insufficient_resting_volume", "raw": live.http_error_text(exc)}},
            )
            live.release_event_lock(conn, best_signal.event_ticker, client_order_id)
            mark_signal_cooldown(signal_cooldowns, best_signal.event_ticker)
            log.info(
                "BTC15M FOK no fill %s %s x%d entry=%.4f submit_ms=%.1f ready_ms=%.1f client_order_id=%s",
                best_signal.market_ticker,
                best_signal.side.upper(),
                best_signal.contracts,
                best_signal.entry_price,
                submit_ms,
                scan_to_ready_ms,
                client_order_id,
            )
            record_decision(
                recorder,
                best_signal,
                "not_filled",
                f"fok_no_fill_409 submit_ms={submit_ms:.1f} ready_ms={scan_to_ready_ms:.1f}",
                portfolio,
                estimated_cost,
                client_order_id,
            )
            return
        synced = live.sync_order_by_client_id(trade_client, conn, trade_id, best_signal, client_order_id)
        if synced:
            status, order = synced
            mark_portfolio_cache_stale(portfolio_cache)
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
        mark_signal_cooldown(signal_cooldowns, best_signal.event_ticker)
    else:
        mark_portfolio_cache_stale(portfolio_cache)
    submit_ms = (time.perf_counter() - submit_perf) * 1000.0
    record_decision(recorder, best_signal, status, "order_response", portfolio, estimated_cost, client_order_id)
    log.info(
        "BTC15M LIVE response status=%s %s %s x%d entry=%.4f yes_book_%s yes_price=%.4f submit_ms=%.1f ready_ms=%.1f response=%s",
        status,
        best_signal.market_ticker,
        best_signal.side.upper(),
        best_signal.contracts,
        best_signal.entry_price,
        best_signal.yes_order_side,
        best_signal.yes_limit_price,
        submit_ms,
        scan_to_ready_ms,
        response,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "dry-run", "paper"], default="live")
    parser.add_argument("--env", choices=["prod", "demo"], default="prod")
    parser.add_argument("--strategy", choices=sorted(STRATEGY_ALIASES), default=SIGNAL_STRATEGY)
    parser.add_argument("--capture-db-path", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--trade-db-path", type=Path, default=DEFAULT_TRADE_DB)
    parser.add_argument("--refresh-sec", type=float, default=10.0)
    parser.add_argument("--health-sec", type=float, default=30.0)
    parser.add_argument("--duration-sec", type=float, default=0.0)
    parser.add_argument("--no-capture", action="store_true")
    parser.add_argument(
        "--capture-writer",
        choices=["readable", "persistent"],
        default=DEFAULT_CAPTURE_WRITER,
        help="Use readable fresh-connection DuckDB writes or faster persistent writes.",
    )
    parser.add_argument("--shadow-bankroll", type=float, default=0.0, help="Paper-mode bankroll for mock sizing/reporting.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_signal_strategy(args.strategy)
    if args.refresh_sec < 2:
        raise SystemExit("--refresh-sec must be >= 2")
    if args.health_sec < 5:
        raise SystemExit("--health-sec must be >= 5")
    if args.shadow_bankroll < 0:
        raise SystemExit("--shadow-bankroll must be non-negative")

    live.RESEARCH_SERIES = SERIES_TICKER
    live.event_from_market_ticker = event_from_market_ticker

    data_client = live.KalshiApi(env=args.env, require_auth=True)
    trade_client = live.KalshiApi(env=args.env, require_auth=True)
    conn = live.db_connect(args.trade_db_path)
    state = live.LiveMarketState()
    update_queue = live.CoalescedUpdateBuffer()
    writer_cls = live.LiveCaptureWriter if args.capture_writer == "persistent" else ReadableDuckCaptureWriter
    recorder = writer_cls(args.capture_db_path, enabled=not args.no_capture, capture_raw_ws=False)
    mode_args = SimpleNamespace(mode=MODE_LIVE)
    mid_history = AsofHistory()
    btc_history = AsofHistory()
    btc_vol_history = BtcMinuteVolHistory()
    last_no_signal_log: dict[str, float] = {}
    portfolio_cache = PortfolioCache()
    signal_cooldowns: dict[str, float] = {}
    event_skip_locks: dict[str, str] = {}

    log.info(
        "starting BTC15M executor mode=%s env=%s strategy=%s sizing=%s target=$%.2f max_premium=$%.2f entry=%.2f..%.2f min_rr=%.2f max_contracts=%d h02_edge=%.2fc h02_min_side_p=%.3f h02_min_qty=%.2f h02_first_min_qty=%.2f h02_allowed_side=%s h02_ttl=%.1f..%.1fm reprice_worse<=%.1fc chase_cooldown=%.0fs portfolio_cache=%.0fs warm_refresh=%.0fs rolling_window=%.1fh rolling_trade_cap=%d rolling_premium_cap=$%.2f shadow_bankroll=$%.2f db=%s capture_db=%s log=%s",
        args.mode,
        args.env,
        STRATEGY_NAME,
        "h02_flat" if SIGNAL_STRATEGY == H02_STRATEGY else "target_win_cap",
        TARGET_WIN_DOLLARS,
        MAX_PREMIUM_DOLLARS,
        strategy_entry_bounds()[0],
        strategy_entry_bounds()[1],
        MIN_RISK_REWARD,
        H02_MAX_CONTRACTS if SIGNAL_STRATEGY == H02_STRATEGY else MAX_CONTRACTS,
        H02_EDGE_THRESHOLD_CENTS,
        H02_MIN_SIDE_PROB,
        H02_MIN_VISIBLE_QTY,
        H02_FIRST_SIGNAL_MIN_VISIBLE_QTY,
        H02_ALLOWED_SIDE or "both",
        H02_TTL_LO,
        H02_TTL_HI,
        MAX_REPRICE_WORSE_CENTS,
        ORDER_CHASE_COOLDOWN_SEC,
        PORTFOLIO_CACHE_TTL_SEC,
        PORTFOLIO_WARM_REFRESH_SEC,
        RISK_WINDOW_HOURS,
        ROLLING_TRADE_CAP,
        ROLLING_PREMIUM_CAP_DOLLARS,
        args.shadow_bankroll,
        args.trade_db_path,
        args.capture_db_path,
        LOG_FILE,
    )
    if SIGNAL_STRATEGY == H02_STRATEGY:
        seeded = btc_vol_history.seed_from_kraken()
        log.info("BTC15M h02 seeded Kraken 1m vol history rows=%d annual_vol=%.3f", seeded, btc_vol_history.annual_vol())

    kalshi_ws: CaptureKalshiWsClient | None = None
    spot_ws: live.KrakenWsSpot | None = None
    started = time.monotonic()
    next_refresh = 0.0
    next_health = 0.0
    next_portfolio_refresh = 0.0
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
        next_refresh = time.monotonic() + args.refresh_sec
        try:
            if args.mode == "paper" and args.shadow_bankroll > 0:
                portfolio = paper_shadow_portfolio_snapshot(conn, btc_vol_history, args.shadow_bankroll)
            else:
                portfolio = get_cached_portfolio(trade_client, portfolio_cache, force=True)
            log.info(
                "portfolio warm cache available=$%.2f value=$%.2f active=%d exposure=$%.2f",
                portfolio.available_balance,
                portfolio.portfolio_value,
                len(portfolio.active_tickers),
                portfolio.market_exposure,
            )
        except Exception:
            log.exception("initial portfolio warm cache failed")
        next_portfolio_refresh = time.monotonic() + PORTFOLIO_WARM_REFRESH_SEC

        while True:
            now = time.monotonic()
            if args.duration_sec > 0 and now - started >= args.duration_sec:
                log.info("duration reached; shutting down")
                break
            timeout = max(0.1, min(next_refresh or now, next_health or now, next_portfolio_refresh or now) - now)
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
                    changed_tickers.update(batch_changed_tickers(item))
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
            scanned_updates = False
            refresh_due = next_refresh == 0.0 or lifecycle_seen or now >= next_refresh
            if updates and not lifecycle_seen:
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
                    portfolio_cache=portfolio_cache,
                    mid_history=mid_history,
                    btc_history=btc_history,
                    btc_vol_history=btc_vol_history,
                    changed_tickers=changed_tickers,
                    last_no_signal_log=last_no_signal_log,
                    signal_cooldowns=signal_cooldowns,
                    event_skip_locks=event_skip_locks,
                    shadow_bankroll=args.shadow_bankroll,
                )
                scanned_updates = True

            if refresh_due:
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

            if (
                (args.mode == "live" or (args.mode == "paper" and args.shadow_bankroll <= 0))
                and PORTFOLIO_WARM_REFRESH_SEC > 0
                and now >= next_portfolio_refresh
            ):
                try:
                    get_cached_portfolio(trade_client, portfolio_cache, force=True)
                except Exception:
                    log.exception("portfolio warm refresh failed")
                next_portfolio_refresh = now + PORTFOLIO_WARM_REFRESH_SEC

            if updates and not scanned_updates:
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
                    portfolio_cache=portfolio_cache,
                    mid_history=mid_history,
                    btc_history=btc_history,
                    btc_vol_history=btc_vol_history,
                    changed_tickers=changed_tickers,
                    last_no_signal_log=last_no_signal_log,
                    signal_cooldowns=signal_cooldowns,
                    event_skip_locks=event_skip_locks,
                    shadow_bankroll=args.shadow_bankroll,
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
                if args.mode == "paper" and args.shadow_bankroll > 0:
                    log_paper_shadow_summary(conn, btc_vol_history, args.shadow_bankroll)
                live.record_capture_health(recorder, state, mode_args, "heartbeat", event_summary(events))
                next_health = now + args.health_sec
    finally:
        if args.mode == "paper" and args.shadow_bankroll > 0:
            log_paper_shadow_summary(conn, btc_vol_history, args.shadow_bankroll, label="BTC15M PAPER final")
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
