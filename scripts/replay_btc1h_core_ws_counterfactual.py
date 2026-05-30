#!/usr/bin/env python3
"""Replay BTC1H core fair-value variants on captured websocket top-of-book.

Research only.  This script does not import or modify live trading loops.  It
streams KXBTCD top-of-book rows in local receive order, maintains an as-of
surface per event, recomputes core model probabilities from the frozen BTC
research cache, and records the first causal trade per event per variant.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.core_model_research import (
    MAX_TTL_MIN,
    MIN_TTL_MIN,
    ModelVariant,
    add_btc_features,
    build_event_cache,
    edge_uncertainty_cents,
    model_probabilities,
)


DEFAULT_CAPTURE_DB = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless_20260512_paused.duckdb"
DEFAULT_BTC_CACHE = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"btc1h_core_ws_counterfactual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
KALSHI_PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE_START_LOOKBACK_NS = 60 * 60 * 1_000_000_000
NY_TZ = ZoneInfo("America/New_York")
EVENT_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")
STRIKE_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)$")
MONTHS = {
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


@dataclass
class QuoteState:
    received_at_ns: int
    received_at_utc: pd.Timestamp
    market_ticker: str
    event_ticker: str
    seq: int | None
    yes_bid: float
    yes_bid_qty: float
    yes_ask: float
    yes_ask_qty: float
    no_bid: float
    no_bid_qty: float
    no_ask: float
    no_ask_qty: float
    btc_spot: float
    source: str
    floor_strike: float
    close_time: pd.Timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BTC1H websocket counterfactual replay for core fair-value variants.")
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--btc-cache", type=Path, default=DEFAULT_BTC_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", help="Optional inclusive receive timestamp, e.g. 2026-05-12T00:00:00Z.")
    parser.add_argument("--end", help="Optional exclusive receive timestamp.")
    parser.add_argument("--top-table", help="Override top-of-book table name.")
    parser.add_argument("--variant-regex", help="Optional regex filter for finalist variant names.")
    parser.add_argument("--max-events", type=int, help="Limit to the first N KXBTCD events by parsed close time.")
    parser.add_argument("--max-rows", type=int, help="Stop after streaming this many top rows; smoke-test helper.")
    parser.add_argument("--chunk-vectors", type=int, default=80, help="DuckDB pandas chunk size in vectors.")
    parser.add_argument("--progress-every-rows", type=int, default=1_000_000)
    parser.add_argument(
        "--scan-stride-sec",
        type=float,
        default=1.0,
        help="Minimum seconds between model evaluations for the same event while still applying every quote update.",
    )
    parser.add_argument(
        "--max-btc-spot-age-sec",
        type=float,
        default=15.0,
        help="Reject model evaluations when the latest captured BTC spot tick is older than this many seconds.",
    )
    parser.add_argument(
        "--model-ttl-override-min",
        type=float,
        help=(
            "Diagnostic-only: use this TTL horizon for model probabilities while "
            "still enforcing the real event TTL entry window. This is for "
            "old cached-TTL parity checks, not forward scan-time policy evidence."
        ),
    )
    parser.add_argument(
        "--recompute-btc-rv",
        action="store_true",
        help="Recompute rv_15m/rv_60m/rv_1d from closes instead of preserving the live BTC cache volatility columns.",
    )
    parser.add_argument(
        "--full-stream",
        action="store_true",
        help="Use the original full-table stream instead of pruning to each event's causal decision window.",
    )
    parser.add_argument(
        "--live-scan-semantics",
        action="store_true",
        help="On quote-triggered scans, evaluate only markets changed in that scan group instead of the full event surface.",
    )
    parser.add_argument(
        "--use-signal-scan-log",
        action="store_true",
        help=(
            "Replay using captured signal_scan timestamps/counts as the scan clock. "
            "This is the closest mode for comparing against a live/paper bot ledger."
        ),
    )
    parser.add_argument(
        "--candidate-scan-only",
        action="store_true",
        help=(
            "With --use-signal-scan-log, evaluate only scans whose captured active-policy "
            "signal_scan row had candidate_count > 0. Use this for active-policy parity "
            "diagnostics, not for discovering new variants."
        ),
    )
    parser.add_argument(
        "--selected-scan-only",
        action="store_true",
        help=(
            "With --use-signal-scan-log, evaluate only scans where the captured active "
            "policy action was selected. This is stricter than --candidate-scan-only and "
            "prevents later dedupe/blocked scans from creating replacement trades."
        ),
    )
    parser.add_argument(
        "--selected-market-only",
        action="store_true",
        help=(
            "When a signal_scan row has selected_market, restrict replay evaluation to "
            "that market. This is an active-policy parity diagnostic, not a discovery mode."
        ),
    )
    parser.add_argument("--signal-table", help="Override signal scan table name when --use-signal-scan-log is set.")
    parser.add_argument("--no-public-fallback", action="store_true", help="Do not query public Kalshi for missing settlements.")
    parser.add_argument("--public-sleep-sec", type=float, default=0.10)
    return parser.parse_args()


@lru_cache(maxsize=4096)
def event_close_from_ticker(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker or "").upper())
    if not match:
        return None
    month = MONTHS.get(match.group("mon"))
    if month is None:
        return None
    local = datetime(
        2000 + int(match.group("yy")),
        month,
        int(match.group("day")),
        int(match.group("hour")),
        tzinfo=NY_TZ,
    )
    return pd.Timestamp(local.astimezone(timezone.utc))


@lru_cache(maxsize=32768)
def strike_from_market_ticker(market_ticker: str) -> float:
    match = STRIKE_RE.search(str(market_ticker or "").upper())
    if not match:
        return float("nan")
    # Kalshi encodes "$80,000 or above" as T79999.99.
    return float(match.group("strike")) + 0.01


def finite_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def parse_utc_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def parse_utc_ns(value: str | None) -> int | None:
    if not value:
        return None
    return int(parse_utc_timestamp(value).value)


def ns_to_utc(ns: int) -> pd.Timestamp:
    return pd.Timestamp(int(ns), unit="ns", tz="UTC")


def load_btc_cache(path: Path, recompute_btc_rv: bool = False) -> pd.DataFrame:
    try:
        btc = pd.read_parquet(path)
    except Exception:
        btc = duckdb.connect().execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchdf()
    if "time" not in btc.columns and "available_at" in btc.columns:
        btc = btc.rename(columns={"available_at": "time"})
    if "time" not in btc.columns:
        raise ValueError(f"{path} must contain a time or available_at column")
    btc["time"] = pd.to_datetime(btc["time"], utc=True, errors="coerce")
    btc = btc.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    preserved = {
        col: btc[col].copy()
        for col in ("rv_15m", "rv_60m", "rv_1d", "rkurt_60m")
        if col in btc.columns and not recompute_btc_rv
    }
    out = add_btc_features(btc)
    for col, values in preserved.items():
        if len(values) == len(out):
            out[col] = pd.to_numeric(values, errors="coerce")
    return out


def btc_idx_at_or_before(btc_time_values: np.ndarray, ts: pd.Timestamp) -> int | None:
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(btc_time_values, lookup, side="right")) - 1
    return idx if 0 <= idx < len(btc_time_values) else None


def detect_table(con: duckdb.DuckDBPyConnection, candidates: list[str], override: str | None = None) -> str | None:
    tables = set(con.execute("SHOW TABLES").fetchdf()["name"].astype(str))
    if override:
        if override not in tables:
            raise ValueError(f"requested table {override!r} not found; available={sorted(tables)}")
        return override
    for name in candidates:
        if name in tables:
            return name
    return None


def load_coinbase_asof(con: duckdb.DuckDBPyConnection, table: str | None, start: str | None, end: str | None) -> tuple[np.ndarray, np.ndarray]:
    if table is None:
        return np.asarray([], dtype=np.int64), np.asarray([], dtype=float)
    where = ["price IS NOT NULL"]
    params: list[Any] = []
    start_ns = parse_utc_ns(start)
    end_ns = parse_utc_ns(end)
    if start_ns is not None:
        where.append("received_at_ns >= ?")
        params.append(max(0, start_ns - COINBASE_START_LOOKBACK_NS))
    if end_ns is not None:
        where.append("received_at_ns <= ?")
        params.append(end_ns)
    query = f"""
        SELECT received_at_ns, price
        FROM {table}
        WHERE {' AND '.join(where)}
        ORDER BY received_at_ns
    """
    df = con.execute(query, params).fetchdf()
    if df.empty:
        return np.asarray([], dtype=np.int64), np.asarray([], dtype=float)
    df["received_at_ns"] = pd.to_numeric(df["received_at_ns"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["received_at_ns", "price"]).sort_values("received_at_ns")
    return df["received_at_ns"].astype("int64").to_numpy(), df["price"].astype(float).to_numpy()


def load_signal_scans(
    con: duckdb.DuckDBPyConnection,
    table: str | None,
    start: str | None,
    end: str | None,
    *,
    use_replay_windows: bool = False,
    candidate_scan_only: bool = False,
    selected_scan_only: bool = False,
) -> pd.DataFrame:
    if table is None:
        return pd.DataFrame()
    alias = "s" if use_replay_windows else ""
    prefix = f"{alias}." if alias else ""
    from_clause = table
    if use_replay_windows:
        from_clause = f"{table} s JOIN replay_windows w ON s.event_ticker = w.event_ticker AND s.received_at_ns > w.replay_start_ns AND s.received_at_ns <= w.replay_end_ns"
    where = [f"{prefix}event_ticker LIKE 'KXBTCD-%'"]
    params: list[Any] = []
    start_ns = parse_utc_ns(start)
    end_ns = parse_utc_ns(end)
    if start_ns is not None:
        where.append(f"{prefix}received_at_ns >= ?")
        params.append(start_ns)
    if end_ns is not None:
        where.append(f"{prefix}received_at_ns < ?")
        params.append(end_ns)
    if candidate_scan_only:
        where.append(f"TRY_CAST({prefix}candidate_count AS BIGINT) > 0")
    if selected_scan_only:
        where.append(f"lower(coalesce({prefix}action, '')) = 'selected'")
    query = f"""
        SELECT {prefix}received_at_ns AS received_at_ns,
               TRY_CAST({prefix}received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               {prefix}reason AS reason,
               {prefix}event_ticker AS event_ticker,
               {prefix}changed_markets AS changed_markets,
               {prefix}evaluated_markets AS evaluated_markets,
               {prefix}candidate_count AS candidate_count,
               {prefix}selected_market AS selected_market,
               {prefix}action AS action,
               {prefix}detail AS detail
        FROM {from_clause}
        WHERE {' AND '.join(where)}
        ORDER BY {prefix}received_at_ns
    """
    df = con.execute(query, params).fetchdf()
    if df.empty:
        return df
    df["received_at_ns"] = pd.to_numeric(df["received_at_ns"], errors="coerce")
    df = df.dropna(subset=["received_at_ns", "event_ticker"]).copy()
    df["received_at_ns"] = df["received_at_ns"].astype("int64")
    df["event_ticker"] = df["event_ticker"].astype(str).str.upper()
    df["reason"] = df["reason"].fillna("").astype(str)
    df["action"] = df["action"].fillna("").astype(str)
    df["changed_markets"] = pd.to_numeric(df["changed_markets"], errors="coerce").fillna(0).astype(int)
    df["evaluated_markets"] = pd.to_numeric(df["evaluated_markets"], errors="coerce").fillna(0).astype(int)
    df["candidate_count"] = pd.to_numeric(df["candidate_count"], errors="coerce").fillna(0).astype(int)
    return df.sort_values("received_at_ns").reset_index(drop=True)


def signal_scan_is_full(row: Any) -> bool:
    reason = str(getattr(row, "reason", "") or "").lower()
    changed = int(getattr(row, "changed_markets", 0) or 0)
    evaluated = int(getattr(row, "evaluated_markets", 0) or 0)
    full_reasons = ("initial_full_book", "event_refresh", "btc_candle_refresh", "kraken", "btc_spot", "private", "lifecycle")
    if any(token in reason for token in full_reasons):
        return True
    return evaluated >= 100 and evaluated > changed


def asof_price(ns_values: np.ndarray, prices: np.ndarray, scan_ns: int) -> tuple[float | None, float | None]:
    if len(ns_values) == 0:
        return None, None
    idx = int(np.searchsorted(ns_values, int(scan_ns), side="right")) - 1
    if idx < 0:
        return None, None
    return float(prices[idx]), (int(scan_ns) - int(ns_values[idx])) / 1e9


def finalist_variants() -> list[ModelVariant]:
    return [
        ModelVariant("baseline_emp70_logn_rv60", 1, "Current research fair value."),
        ModelVariant("ewma60_logn_blend", 1, "EWMA variance handles volatility clustering better than rolling RV.", vol_col="rv_ewma_60m"),
        ModelVariant("blend_85_15_rv60", 1, "Empirical-heavy blend.", emp_weight=0.85, lognormal_weight=0.15),
        ModelVariant("rv_down60_blend", 2, "Downside semivariance volatility.", vol_col="rv_down_60m"),
        ModelVariant("brti_065", 1, "More aggressive BTC dampening.", brti_dampening=0.65),
        ModelVariant("high_conf_80", 1, "Current fair value with stricter side probability.", min_yes_p=0.80, max_no_p=0.20),
        ModelVariant(
            "high_conf_80_no_chase",
            1,
            "High-confidence rule plus pre-existing 10m no-chase momentum guard.",
            min_yes_p=0.80,
            max_no_p=0.20,
        ),
        ModelVariant(
            "high_conf_80_entry70_no_chase",
            1,
            "High-confidence no-chase rule capped at 70c entry.",
            min_yes_p=0.80,
            max_no_p=0.20,
            max_entry=0.70,
        ),
        ModelVariant(
            "high_conf_80_entry59_70_no_chase",
            1,
            "High-confidence no-chase rule with 59c-70c entry band.",
            min_yes_p=0.80,
            max_no_p=0.20,
            min_entry=0.59,
            max_entry=0.70,
        ),
    ]


def result_from_payload(payload: str | None) -> str | None:
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    result = str(obj.get("result") or "").lower()
    if result in {"yes", "no"}:
        return result
    value = obj.get("settlement_value")
    try:
        if value is not None:
            return "yes" if float(value) >= 0.5 else "no"
    except (TypeError, ValueError):
        pass
    market = obj.get("market")
    if isinstance(market, dict):
        nested = str(market.get("result") or "").lower()
        if nested in {"yes", "no"}:
            return nested
    return None


def load_captured_results(
    con: duckdb.DuckDBPyConnection,
    lifecycle_table: str | None,
    private_table: str | None,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    ticker_filter = ""
    ticker_params: list[str] = []
    if tickers is not None and not tickers:
        return pd.DataFrame(columns=["market_ticker", "official_result", "result_source", "result_received_at_ns", "result_received_at_utc"])
    if tickers:
        clean_tickers = sorted({str(t).upper() for t in tickers if str(t).upper().startswith("KXBTCD-")})
        if not clean_tickers:
            return pd.DataFrame(columns=["market_ticker", "official_result", "result_source", "result_received_at_ns", "result_received_at_utc"])
        if clean_tickers:
            ticker_filter = f"AND market_ticker IN ({', '.join(['?'] * len(clean_tickers))})"
            ticker_params = clean_tickers
    if lifecycle_table:
        df = con.execute(
            f"""
            SELECT received_at_ns, TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
                   event_type, event_ticker, market_ticker, payload_json
            FROM {lifecycle_table}
            WHERE (event_ticker LIKE 'KXBTCD-%' OR market_ticker LIKE 'KXBTCD-%')
              AND payload_json IS NOT NULL
              {ticker_filter}
            ORDER BY received_at_ns
            """,
            ticker_params,
        ).fetchdf()
        for row in df.itertuples(index=False):
            result = result_from_payload(getattr(row, "payload_json", None))
            if result not in {"yes", "no"}:
                continue
            market_ticker = str(getattr(row, "market_ticker", "") or "").upper()
            if not market_ticker.startswith("KXBTCD-"):
                continue
            rows.append(
                {
                    "market_ticker": market_ticker,
                    "official_result": result,
                    "result_source": "captured_lifecycle",
                    "result_received_at_ns": getattr(row, "received_at_ns", None),
                    "result_received_at_utc": getattr(row, "received_at_utc", None),
                }
            )
    if private_table:
        df = con.execute(
            f"""
            SELECT received_at_ns, TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
                   event_ticker, market_ticker, payload_json
            FROM {private_table}
            WHERE (event_ticker LIKE 'KXBTCD-%' OR market_ticker LIKE 'KXBTCD-%')
              AND payload_json IS NOT NULL
              {ticker_filter}
            ORDER BY received_at_ns
            """,
            ticker_params,
        ).fetchdf()
        for row in df.itertuples(index=False):
            result = result_from_payload(getattr(row, "payload_json", None))
            if result not in {"yes", "no"}:
                continue
            market_ticker = str(getattr(row, "market_ticker", "") or "").upper()
            if not market_ticker.startswith("KXBTCD-"):
                continue
            rows.append(
                {
                    "market_ticker": market_ticker,
                    "official_result": result,
                    "result_source": "captured_private",
                    "result_received_at_ns": getattr(row, "received_at_ns", None),
                    "result_received_at_utc": getattr(row, "received_at_utc", None),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["market_ticker", "official_result", "result_source", "result_received_at_ns", "result_received_at_utc"])
    return (
        out.sort_values(["market_ticker", "result_received_at_ns"])
        .drop_duplicates("market_ticker", keep="last")
        .reset_index(drop=True)
    )


def fetch_public_result(session: requests.Session, ticker: str, timeout: int = 15) -> dict[str, Any] | None:
    url = f"{KALSHI_PUBLIC_BASE}/markets/{quote(ticker, safe='')}"
    response = session.get(url, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    market = data.get("market", data)
    if not isinstance(market, dict):
        return None
    result = str(market.get("result") or "").lower()
    if result not in {"yes", "no"}:
        value = market.get("settlement_value")
        try:
            if value is not None:
                result = "yes" if float(value) >= 0.5 else "no"
        except (TypeError, ValueError):
            return None
    if result not in {"yes", "no"}:
        return None
    return {
        "market_ticker": ticker,
        "official_result": result,
        "result_source": "public_kalshi",
        "result_received_at_ns": None,
        "result_received_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def quote_df_from_state(
    event_ticker: str,
    state: dict[str, QuoteState],
    scan_ts: pd.Timestamp,
    market_filter: set[str] | None = None,
) -> pd.DataFrame:
    rows = []
    clean_filter = {str(t).upper() for t in market_filter} if market_filter is not None else None
    for quote_state in state.values():
        if clean_filter is not None and quote_state.market_ticker.upper() not in clean_filter:
            continue
        rows.append(
            {
                "event_ticker": event_ticker,
                "market_ticker": quote_state.market_ticker,
                "available_at": scan_ts,
                "floor_strike": quote_state.floor_strike,
                "yes_bid_close": quote_state.yes_bid,
                "yes_ask_close": quote_state.yes_ask,
                "yes_ask_exe": quote_state.yes_ask,
                "yes_ask_qty": quote_state.yes_ask_qty,
                "no_bid_close": quote_state.no_bid,
                "no_ask_exe": quote_state.no_ask,
                "no_ask_qty": quote_state.no_ask_qty,
                "yes_spread_cents": (quote_state.yes_ask - quote_state.yes_bid) * 100.0,
                "no_spread_cents": (quote_state.no_ask - quote_state.no_bid) * 100.0,
                "spread_cents": (quote_state.yes_ask - quote_state.yes_bid) * 100.0,
                "close_time": quote_state.close_time,
                "last_top_received_at_ns": quote_state.received_at_ns,
                "last_top_received_at_utc": quote_state.received_at_utc,
                "seq": quote_state.seq,
                "top_btc_spot": quote_state.btc_spot,
                "source": quote_state.source,
            }
        )
    if not rows:
        return pd.DataFrame()
    q = pd.DataFrame(rows)
    return q.sort_values(["floor_strike", "market_ticker"]).reset_index(drop=True)


def choose_trade(
    variant: ModelVariant,
    q: pd.DataFrame,
    p_yes: np.ndarray,
    emp_cache: dict,
    btc_ret_10m_usd: float | None = None,
) -> dict[str, Any] | None:
    if q.empty or len(p_yes) != len(q):
        return None
    uncertainty = edge_uncertainty_cents(p_yes, emp_cache, variant.uncertainty_mult)
    threshold = variant.min_edge_cents + uncertainty

    candidates: list[pd.DataFrame] = []
    yes_entry = pd.to_numeric(q["yes_ask_exe"], errors="coerce").to_numpy(dtype=float)
    yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce").to_numpy(dtype=float)
    yes_spread = pd.to_numeric(q["yes_spread_cents"], errors="coerce").to_numpy(dtype=float)
    yes_fee = np.asarray([kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") if math.isfinite(float(x)) else np.nan for x in yes_entry])
    yes_edge = (p_yes - yes_entry) * 100.0 - yes_fee * 100.0 + variant.side_bias_cents
    yes_mask = (
        np.isfinite(p_yes)
        & (p_yes >= variant.min_yes_p)
        & np.isfinite(yes_entry)
        & (yes_entry >= variant.min_entry)
        & (yes_entry <= variant.max_entry)
        & np.isfinite(yes_spread)
        & (yes_spread <= variant.max_spread_cents)
        & np.isfinite(yes_qty)
        & (yes_qty >= 1.0)
        & np.isfinite(yes_edge)
        & (yes_edge >= threshold)
    )
    if "no_chase" in variant.name and btc_ret_10m_usd is not None and math.isfinite(float(btc_ret_10m_usd)):
        yes_mask &= float(btc_ret_10m_usd) < 150.0
    if yes_mask.any():
        y = q.loc[yes_mask].copy()
        y["side"] = "yes"
        y["entry_price"] = yes_entry[yes_mask]
        y["visible_qty"] = yes_qty[yes_mask]
        y["side_spread_cents"] = yes_spread[yes_mask]
        y["model_p_yes"] = p_yes[yes_mask]
        y["model_p_side"] = p_yes[yes_mask]
        y["entry_fee"] = yes_fee[yes_mask]
        y["net_edge_cents"] = yes_edge[yes_mask]
        y["edge_threshold_cents"] = threshold[yes_mask]
        candidates.append(y)

    no_entry = pd.to_numeric(q["no_ask_exe"], errors="coerce").to_numpy(dtype=float)
    no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce").to_numpy(dtype=float)
    no_spread = pd.to_numeric(q["no_spread_cents"], errors="coerce").to_numpy(dtype=float)
    no_fee = np.asarray([kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") if math.isfinite(float(x)) else np.nan for x in no_entry])
    no_p = 1.0 - p_yes
    no_edge = (no_p - no_entry) * 100.0 - no_fee * 100.0 - variant.side_bias_cents
    no_mask = (
        np.isfinite(no_p)
        & (p_yes <= variant.max_no_p)
        & np.isfinite(no_entry)
        & (no_entry >= variant.min_entry)
        & (no_entry <= variant.max_entry)
        & np.isfinite(no_spread)
        & (no_spread <= variant.max_spread_cents)
        & np.isfinite(no_qty)
        & (no_qty >= 1.0)
        & np.isfinite(no_edge)
        & (no_edge >= threshold)
    )
    if "no_chase" in variant.name and btc_ret_10m_usd is not None and math.isfinite(float(btc_ret_10m_usd)):
        no_mask &= -float(btc_ret_10m_usd) < 150.0
    if no_mask.any():
        n = q.loc[no_mask].copy()
        n["side"] = "no"
        n["entry_price"] = no_entry[no_mask]
        n["visible_qty"] = no_qty[no_mask]
        n["side_spread_cents"] = no_spread[no_mask]
        n["model_p_yes"] = p_yes[no_mask]
        n["model_p_side"] = no_p[no_mask]
        n["entry_fee"] = no_fee[no_mask]
        n["net_edge_cents"] = no_edge[no_mask]
        n["edge_threshold_cents"] = threshold[no_mask]
        candidates.append(n)

    if not candidates:
        return None
    hits = pd.concat(candidates, ignore_index=True)
    hits = hits.sort_values(["net_edge_cents", "last_top_received_at_ns", "market_ticker", "side"], ascending=[False, True, True, True])
    return hits.iloc[0].to_dict()


def variant_needs_full_surface(variant: ModelVariant) -> bool:
    return bool(float(getattr(variant, "market_weight", 0.0) or 0.0) or float(getattr(variant, "event_iv_weight", 0.0) or 0.0))


def static_book_candidate_mask(q: pd.DataFrame, variants: list[ModelVariant]) -> pd.Series:
    """Rows that could pass at least one variant's static execution gates."""
    if q.empty:
        return pd.Series(False, index=q.index)
    yes_entry = pd.to_numeric(q["yes_ask_exe"], errors="coerce")
    yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    yes_spread = pd.to_numeric(q["yes_spread_cents"], errors="coerce")
    no_entry = pd.to_numeric(q["no_ask_exe"], errors="coerce")
    no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce")
    no_spread = pd.to_numeric(q["no_spread_cents"], errors="coerce")
    mask = pd.Series(False, index=q.index)
    for variant in variants:
        mask |= (
            yes_entry.between(float(variant.min_entry), float(variant.max_entry), inclusive="both")
            & (yes_spread <= float(variant.max_spread_cents))
            & (yes_qty >= 1.0)
        )
        mask |= (
            no_entry.between(float(variant.min_entry), float(variant.max_entry), inclusive="both")
            & (no_spread <= float(variant.max_spread_cents))
            & (no_qty >= 1.0)
        )
    return mask.fillna(False).astype(bool)


def quote_state_matches_static_book(quote_state: QuoteState, variants: list[ModelVariant]) -> bool:
    yes_entry = quote_state.yes_ask
    yes_qty = quote_state.yes_ask_qty
    yes_spread = (quote_state.yes_ask - quote_state.yes_bid) * 100.0
    no_entry = quote_state.no_ask
    no_qty = quote_state.no_ask_qty
    no_spread = (quote_state.no_ask - quote_state.no_bid) * 100.0
    for variant in variants:
        min_entry = float(variant.min_entry)
        max_entry = float(variant.max_entry)
        max_spread = float(variant.max_spread_cents)
        if (
            math.isfinite(yes_entry)
            and min_entry <= yes_entry <= max_entry
            and math.isfinite(yes_spread)
            and yes_spread <= max_spread
            and math.isfinite(yes_qty)
            and yes_qty >= 1.0
        ):
            return True
        if (
            math.isfinite(no_entry)
            and min_entry <= no_entry <= max_entry
            and math.isfinite(no_spread)
            and no_spread <= max_spread
            and math.isfinite(no_qty)
            and no_qty >= 1.0
        ):
            return True
    return False


def stat_add(stats: MutableMapping[str, int] | None, key: str, value: int = 1) -> None:
    if stats is not None:
        stats[key] = int(stats.get(key, 0)) + int(value)


def evaluate_event(
    event_ticker: str,
    state: dict[str, QuoteState],
    scan_ns: int,
    variants: list[ModelVariant],
    traded_events: set[tuple[str, str]],
    btc: pd.DataFrame,
    btc_time_values: np.ndarray,
    event_cache: dict[tuple[str, int], dict],
    coinbase_ns: np.ndarray,
    coinbase_prices: np.ndarray,
    fallback_btc_spot: float | None,
    max_btc_spot_age_sec: float,
    model_ttl_override_min: float | None = None,
    market_filter: set[str] | None = None,
    static_candidate_markets: set[str] | None = None,
    stats: MutableMapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    stat_add(stats, "evaluate_event_calls")
    close_time = event_close_from_ticker(event_ticker)
    if close_time is None:
        stat_add(stats, "evaluate_event_no_close_time")
        return []
    scan_ts = ns_to_utc(scan_ns)
    ttl_min = (close_time - scan_ts).total_seconds() / 60.0
    if ttl_min < MIN_TTL_MIN or ttl_min > MAX_TTL_MIN:
        stat_add(stats, "evaluate_event_ttl_outside_window")
        return []
    model_ttl_min = ttl_min
    if model_ttl_override_min is not None:
        model_ttl_min = float(model_ttl_override_min)
        if not math.isfinite(model_ttl_min) or model_ttl_min <= 0.0:
            stat_add(stats, "evaluate_event_bad_model_ttl_override")
            return []
    if all((variant.name, event_ticker) in traded_events for variant in variants):
        stat_add(stats, "evaluate_event_all_variants_already_traded")
        return []
    if static_candidate_markets is not None:
        candidate_markets = set(static_candidate_markets)
        if market_filter is not None:
            candidate_markets &= {str(t).upper() for t in market_filter}
        if not candidate_markets:
            stat_add(stats, "evaluate_event_static_book_no_candidate_state")
            return []
        if not any(variant_needs_full_surface(variant) for variant in variants):
            market_filter = candidate_markets
    q = quote_df_from_state(event_ticker, state, scan_ts, market_filter=market_filter)
    if q.empty:
        stat_add(stats, "evaluate_event_empty_quote_surface")
        return []
    q = q[np.isfinite(pd.to_numeric(q["floor_strike"], errors="coerce"))].copy()
    if q.empty:
        stat_add(stats, "evaluate_event_no_finite_strikes")
        return []
    stat_add(stats, "static_book_rows_before", len(q))
    static_mask = static_book_candidate_mask(q, variants)
    if not bool(static_mask.any()):
        stat_add(stats, "evaluate_event_static_book_no_candidate")
        return []
    if not any(variant_needs_full_surface(variant) for variant in variants):
        q = q.loc[static_mask].copy()
    stat_add(stats, "static_book_rows_after", len(q))
    if q.empty:
        return []
    live_spot, live_spot_age_sec = asof_price(coinbase_ns, coinbase_prices, scan_ns)
    spot_source = "coinbase_ticker_all"
    if (
        live_spot is not None
        and live_spot_age_sec is not None
        and float(live_spot_age_sec) > float(max_btc_spot_age_sec)
    ):
        stat_add(stats, "evaluate_event_stale_btc_spot")
        return []
    if live_spot is None:
        live_spot = fallback_btc_spot
        live_spot_age_sec = None
        spot_source = "ws_orderbook_top.btc_spot"
    if live_spot is None or not math.isfinite(float(live_spot)) or float(live_spot) <= 0.0:
        stat_add(stats, "evaluate_event_missing_btc_spot")
        return []
    btc_idx = btc_idx_at_or_before(btc_time_values, scan_ts)
    if btc_idx is None or btc_idx < 1440:
        stat_add(stats, "evaluate_event_missing_btc_cache")
        return []
    event_open = close_time - pd.Timedelta(hours=1)
    trades: list[dict[str, Any]] = []
    for variant in variants:
        event_key = (variant.name, event_ticker)
        if event_key in traded_events:
            continue
        cache_key = (event_ticker, variant.train_days)
        emp_cache = event_cache.get(cache_key)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, variant.train_days)
            if emp_cache is None:
                stat_add(stats, "evaluate_event_missing_emp_cache")
                continue
            event_cache[cache_key] = emp_cache
        stat_add(stats, "model_probability_calls")
        stat_add(stats, "model_probability_rows", len(q))
        p_yes = model_probabilities(variant, q, btc, btc_idx, float(live_spot), model_ttl_min, emp_cache, {})
        btc_ret_10m_usd = None
        if btc_idx >= 10:
            prior_spot = finite_float(btc.iloc[btc_idx - 10]["close"])
            if math.isfinite(prior_spot):
                btc_ret_10m_usd = float(live_spot) - prior_spot
        chosen = choose_trade(variant, q, p_yes, emp_cache, btc_ret_10m_usd=btc_ret_10m_usd)
        if chosen is None:
            stat_add(stats, "evaluate_event_no_trade_after_model")
            continue
        traded_events.add(event_key)
        trades.append(
            {
                "variant": variant.name,
                "round_no": variant.round_no,
                "event_ticker": event_ticker,
                "market_ticker": str(chosen["market_ticker"]),
                "side": str(chosen["side"]),
                "entry_time": scan_ts.isoformat(),
                "entry_received_at_ns": int(scan_ns),
                "close_time": close_time.isoformat(),
                "ttl_min": float(ttl_min),
                "model_ttl_min": float(model_ttl_min),
                "model_ttl_override_min": model_ttl_override_min,
                "floor_strike": float(chosen["floor_strike"]),
                "entry_price": float(chosen["entry_price"]),
                "entry_fee": float(chosen["entry_fee"]),
                "premium": float(chosen["entry_price"]) + float(chosen["entry_fee"]),
                "visible_qty": float(chosen["visible_qty"]),
                "side_spread_cents": float(chosen["side_spread_cents"]),
                "model_p_yes": float(chosen["model_p_yes"]),
                "model_p_side": float(chosen["model_p_side"]),
                "net_edge_cents": float(chosen["net_edge_cents"]),
                "edge_threshold_cents": float(chosen["edge_threshold_cents"]),
                "btc_spot_model": float(live_spot),
                "btc_ret_10m_usd": btc_ret_10m_usd,
                "btc_spot_source": spot_source,
                "btc_spot_age_sec": live_spot_age_sec,
                "btc_research_cache_idx": int(btc_idx),
                "last_top_received_at_ns": int(chosen["last_top_received_at_ns"]),
                "last_top_received_at_utc": pd.Timestamp(chosen["last_top_received_at_utc"]).isoformat(),
                "seq": chosen.get("seq"),
                "top_source": chosen.get("source"),
            }
        )
    return trades


def stream_top_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    start: str | None,
    end: str | None,
    max_events: int | None,
    chunk_vectors: int,
):
    params: list[Any] = []
    where = ["event_ticker LIKE 'KXBTCD-%'"]
    start_ns = parse_utc_ns(start)
    end_ns = parse_utc_ns(end)
    if start_ns is not None:
        where.append("received_at_ns >= ?")
        params.append(start_ns)
    if end_ns is not None:
        where.append("received_at_ns < ?")
        params.append(end_ns)
    event_filter: list[str] | None = None
    if max_events:
        events = con.execute(
            f"""
            SELECT event_ticker, min(received_at_ns) AS first_ns
            FROM {table}
            WHERE {' AND '.join(where)}
            GROUP BY event_ticker
            ORDER BY event_ticker
            """,
            params,
        ).fetchdf()
        events["close_time"] = events["event_ticker"].map(event_close_from_ticker)
        events = events.dropna(subset=["close_time"]).sort_values(["close_time", "event_ticker"]).head(max_events)
        event_filter = events["event_ticker"].astype(str).tolist()
        if not event_filter:
            return
        placeholders = ", ".join(["?"] * len(event_filter))
        where.append(f"event_ticker IN ({placeholders})")
        params.extend(event_filter)

    query = f"""
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               market_ticker, event_ticker, seq,
               yes_bid, yes_bid_qty, yes_ask, yes_ask_qty,
               no_bid, no_bid_qty, no_ask, no_ask_qty,
               btc_spot, source
        FROM {table}
        WHERE {' AND '.join(where)}
        ORDER BY received_at_ns, seq, market_ticker
    """
    cursor = con.execute(query, params)
    while True:
        chunk = cursor.fetch_df_chunk(chunk_vectors)
        if chunk is None or chunk.empty:
            break
        yield chunk


def discover_replay_windows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    start: str | None,
    end: str | None,
    max_events: int | None,
) -> pd.DataFrame:
    start_ns = parse_utc_ns(start)
    end_ns = parse_utc_ns(end)
    events = con.execute(
        f"""
        SELECT event_ticker,
               min(received_at_ns) AS first_ns,
               max(received_at_ns) AS last_ns,
               count(*) AS row_count
        FROM {table}
        WHERE event_ticker LIKE 'KXBTCD-%'
        GROUP BY event_ticker
        ORDER BY event_ticker
        """
    ).fetchdf()
    if events.empty:
        return pd.DataFrame()
    events["event_ticker"] = events["event_ticker"].astype(str).str.upper()
    events["close_time"] = events["event_ticker"].map(event_close_from_ticker)
    events = events.dropna(subset=["close_time"]).copy()
    events["window_start_ns"] = (events["close_time"] - pd.Timedelta(minutes=MAX_TTL_MIN)).map(lambda ts: int(pd.Timestamp(ts).value))
    events["window_end_ns"] = (events["close_time"] - pd.Timedelta(minutes=MIN_TTL_MIN)).map(lambda ts: int(pd.Timestamp(ts).value))
    events["replay_start_ns"] = events["window_start_ns"]
    events["replay_end_ns"] = events["window_end_ns"]
    if start_ns is not None:
        events["replay_start_ns"] = np.maximum(events["replay_start_ns"].astype("int64"), int(start_ns))
    if end_ns is not None:
        events["replay_end_ns"] = np.minimum(events["replay_end_ns"].astype("int64"), int(end_ns))
    events = events[
        (events["replay_end_ns"] > events["replay_start_ns"])
        & (events["last_ns"].astype("int64") >= events["replay_start_ns"].astype("int64"))
        & (events["first_ns"].astype("int64") <= events["replay_end_ns"].astype("int64"))
    ].copy()
    events = events.sort_values(["close_time", "event_ticker"]).reset_index(drop=True)
    if max_events:
        events = events.head(max_events).copy()
    # The latest book state at the 20-minute boundary only needs rows from the
    # current hourly event.  Bounding this seed read keeps DuckDB from scanning
    # days of unrelated websocket rows.
    events["seed_floor_ns"] = events["replay_start_ns"].astype("int64") - int(45 * 60 * 1_000_000_000)
    return events[
        [
            "event_ticker",
            "close_time",
            "first_ns",
            "last_ns",
            "row_count",
            "window_start_ns",
            "window_end_ns",
            "seed_floor_ns",
            "replay_start_ns",
            "replay_end_ns",
        ]
    ]


def install_replay_windows(con: duckdb.DuckDBPyConnection, windows: pd.DataFrame) -> None:
    con.execute("DROP TABLE IF EXISTS replay_windows")
    con.register("_replay_windows_df", windows)
    con.execute(
        """
        CREATE TEMP TABLE replay_windows AS
        SELECT event_ticker,
               CAST(seed_floor_ns AS BIGINT) AS seed_floor_ns,
               CAST(replay_start_ns AS BIGINT) AS replay_start_ns,
               CAST(replay_end_ns AS BIGINT) AS replay_end_ns
        FROM _replay_windows_df
        """
    )
    con.unregister("_replay_windows_df")


def load_seed_rows(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    return con.execute(
        f"""
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               market_ticker, event_ticker, seq,
               yes_bid, yes_bid_qty, yes_ask, yes_ask_qty,
               no_bid, no_bid_qty, no_ask, no_ask_qty,
               btc_spot, source
        FROM (
            SELECT t.*,
                   row_number() OVER (
                       PARTITION BY t.event_ticker, t.market_ticker
                       ORDER BY t.received_at_ns DESC, COALESCE(t.seq, 0) DESC
                   ) AS rn
            FROM {table} t
            JOIN replay_windows w
              ON t.event_ticker = w.event_ticker
             AND t.received_at_ns >= w.seed_floor_ns
             AND t.received_at_ns <= w.replay_start_ns
            WHERE t.event_ticker IN (SELECT event_ticker FROM replay_windows)
              AND t.received_at_ns >= (SELECT min(seed_floor_ns) FROM replay_windows)
              AND t.received_at_ns <= (SELECT max(replay_start_ns) FROM replay_windows)
        )
        WHERE rn = 1
        ORDER BY event_ticker, market_ticker
        """
    ).fetchdf()


def stream_window_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    chunk_vectors: int,
):
    query = f"""
        SELECT t.received_at_ns,
               TRY_CAST(t.received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               t.market_ticker, t.event_ticker, t.seq,
               t.yes_bid, t.yes_bid_qty, t.yes_ask, t.yes_ask_qty,
               t.no_bid, t.no_bid_qty, t.no_ask, t.no_ask_qty,
               t.btc_spot, t.source
        FROM {table} t
        JOIN replay_windows w
          ON t.event_ticker = w.event_ticker
         AND t.received_at_ns > w.replay_start_ns
         AND t.received_at_ns <= w.replay_end_ns
        WHERE t.event_ticker IN (SELECT event_ticker FROM replay_windows)
          AND t.received_at_ns > (SELECT min(replay_start_ns) FROM replay_windows)
          AND t.received_at_ns <= (SELECT max(replay_end_ns) FROM replay_windows)
        ORDER BY t.received_at_ns, t.seq, t.market_ticker
    """
    cursor = con.execute(query)
    while True:
        chunk = cursor.fetch_df_chunk(chunk_vectors)
        if chunk is None or chunk.empty:
            break
        yield chunk


def max_drawdown(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if values.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), values.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(values) < 2:
        return 0.0
    sd = float(values.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(values.mean() / sd * math.sqrt(len(values)))


def settle_trades(trades: pd.DataFrame, captured_results: pd.DataFrame, use_public: bool, sleep_sec: float) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    out = trades.merge(captured_results, on="market_ticker", how="left")
    missing = sorted(out.loc[~out["official_result"].astype(str).str.lower().isin(["yes", "no"]), "market_ticker"].dropna().unique())
    if missing and use_public:
        session = requests.Session()
        fetched = []
        for ticker in missing:
            try:
                result = fetch_public_result(session, str(ticker))
                if result:
                    fetched.append(result)
            except Exception as exc:
                print(f"public settlement fallback failed for {ticker}: {exc}", file=sys.stderr, flush=True)
            if sleep_sec > 0:
                time.sleep(sleep_sec)
        if fetched:
            public = pd.DataFrame(fetched)
            fill = out[~out["official_result"].astype(str).str.lower().isin(["yes", "no"])].drop(
                columns=["official_result", "result_source", "result_received_at_ns", "result_received_at_utc"],
                errors="ignore",
            )
            keep = out[out["official_result"].astype(str).str.lower().isin(["yes", "no"])]
            fill = fill.merge(public, on="market_ticker", how="left")
            out = pd.concat([keep, fill], ignore_index=True).sort_values(["variant", "entry_received_at_ns", "market_ticker"])
    result = out["official_result"].astype(str).str.lower()
    valid = result.isin(["yes", "no"])
    win = valid & result.eq(out["side"].astype(str).str.lower())
    out["settled"] = valid
    out["win"] = np.where(valid, win, np.nan)
    out["payout"] = np.where(valid & win, 1.0, np.where(valid, 0.0, np.nan))
    out["pnl"] = out["payout"] - pd.to_numeric(out["entry_price"], errors="coerce") - pd.to_numeric(out["entry_fee"], errors="coerce")
    return out.sort_values(["variant", "entry_received_at_ns", "market_ticker"]).reset_index(drop=True)


def summarize(trades: pd.DataFrame, variants: list[ModelVariant]) -> pd.DataFrame:
    rows = []
    for variant in variants:
        vg = trades[trades["variant"].eq(variant.name)].copy() if not trades.empty else pd.DataFrame()
        settled = vg[vg.get("settled", pd.Series(dtype=bool)).fillna(False).astype(bool)].copy() if not vg.empty else pd.DataFrame()
        ordered = settled.sort_values(["close_time", "entry_received_at_ns", "market_ticker"]) if not settled.empty else settled
        pnl = pd.to_numeric(ordered.get("pnl", pd.Series(dtype=float)), errors="coerce")
        wins = pd.to_numeric(ordered.get("win", pd.Series(dtype=float)), errors="coerce")
        premium = pd.to_numeric(ordered.get("premium", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "variant": variant.name,
                "signals": int(len(vg)),
                "trades": int(len(settled)),
                "unsettled": int(len(vg) - len(settled)),
                "pnl": round(float(pnl.sum()), 6) if len(pnl) else 0.0,
                "wins": int(wins.sum()) if len(wins) else 0,
                "win_rate": round(float(wins.mean()), 6) if len(wins) else 0.0,
                "maxdd": round(max_drawdown(pnl), 6),
                "sharpe": round(sharpe(pnl), 6),
                "premium": round(float(premium.sum()), 6) if len(premium) else 0.0,
                "avg_edge_cents": round(float(pd.to_numeric(vg.get("net_edge_cents", pd.Series(dtype=float)), errors="coerce").mean()), 6)
                if len(vg)
                else 0.0,
                "yes_trades": int((settled.get("side", pd.Series(dtype=str)) == "yes").sum()) if len(settled) else 0,
                "no_trades": int((settled.get("side", pd.Series(dtype=str)) == "no").sum()) if len(settled) else 0,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    variants = finalist_variants()
    if args.variant_regex:
        pattern = re.compile(args.variant_regex)
        variants = [variant for variant in variants if pattern.search(variant.name)]
        if not variants:
            raise ValueError(f"--variant-regex matched no variants: {args.variant_regex!r}")
    btc = load_btc_cache(args.btc_cache, recompute_btc_rv=args.recompute_btc_rv)
    btc_time_values = btc["time"].dt.tz_localize(None).to_numpy()

    con = duckdb.connect(str(args.capture_db), read_only=True)
    top_table = detect_table(con, ["ws_orderbook_top_dedup", "ws_orderbook_top_all", "ws_orderbook_top"], args.top_table)
    if top_table is None:
        raise RuntimeError("no ws_orderbook_top table found")
    coinbase_table = detect_table(con, ["coinbase_ticker_all", "coinbase_ticker"])
    signal_table = detect_table(con, ["signal_scan"], args.signal_table) if args.use_signal_scan_log or args.signal_table else None
    if args.use_signal_scan_log and signal_table is None:
        raise RuntimeError("--use-signal-scan-log was requested, but no signal_scan table was found")
    lifecycle_table = detect_table(con, ["ws_lifecycle_all", "ws_lifecycle"])
    private_table = detect_table(con, ["ws_private_event_all", "ws_private_event"])
    coinbase_ns, coinbase_prices = load_coinbase_asof(con, coinbase_table, args.start, args.end)
    signal_scans = pd.DataFrame()

    event_states: dict[str, dict[str, QuoteState]] = {}
    static_candidate_markets_by_event: dict[str, set[str]] = {}
    event_cache: dict[tuple[str, int], dict] = {}
    traded_events: set[tuple[str, str]] = set()
    trade_rows: list[dict[str, Any]] = []
    changed_since_signal_scan: dict[str, set[str]] = {}
    last_top_spot: float | None = None
    last_scan_ns_by_event: dict[str, int] = {}
    scan_stride_ns = max(0, int(float(args.scan_stride_sec) * 1_000_000_000))
    pending_ns: int | None = None
    pending_rows: list[Any] = []
    rows_seen = 0
    groups_seen = 0
    windows_used = pd.DataFrame()
    replay_stats: dict[str, int] = {}
    stopped_early_by_max_rows = False

    def apply_row_to_state(row: Any) -> tuple[str | None, float | None]:
        event_ticker = str(row.event_ticker or "").upper()
        market_ticker = str(row.market_ticker or "").upper()
        close_time = event_close_from_ticker(event_ticker)
        strike = strike_from_market_ticker(market_ticker)
        if close_time is None or not math.isfinite(strike):
            return None, None
        btc_spot = finite_float(row.btc_spot)
        state = event_states.setdefault(event_ticker, {})
        quote_state = QuoteState(
            received_at_ns=int(row.received_at_ns),
            received_at_utc=row.received_at_utc,
            market_ticker=market_ticker,
            event_ticker=event_ticker,
            seq=int(row.seq) if pd.notna(row.seq) else None,
            yes_bid=finite_float(row.yes_bid),
            yes_bid_qty=finite_float(row.yes_bid_qty),
            yes_ask=finite_float(row.yes_ask),
            yes_ask_qty=finite_float(row.yes_ask_qty),
            no_bid=finite_float(row.no_bid),
            no_bid_qty=finite_float(row.no_bid_qty),
            no_ask=finite_float(row.no_ask),
            no_ask_qty=finite_float(row.no_ask_qty),
            btc_spot=btc_spot,
            source=str(row.source or ""),
            floor_strike=strike,
            close_time=close_time,
        )
        state[market_ticker] = quote_state
        static_candidate_markets = static_candidate_markets_by_event.setdefault(event_ticker, set())
        if quote_state_matches_static_book(quote_state, variants):
            static_candidate_markets.add(market_ticker)
        else:
            static_candidate_markets.discard(market_ticker)
        return event_ticker, btc_spot if math.isfinite(btc_spot) else None

    def flush_group(rows: list[Any], scan_ns: int) -> None:
        nonlocal last_top_spot, groups_seen
        changed_events: set[str] = set()
        changed_markets_by_event: dict[str, set[str]] = {}
        for row in rows:
            event_ticker, btc_spot = apply_row_to_state(row)
            if event_ticker is None:
                continue
            changed_events.add(event_ticker)
            changed_markets_by_event.setdefault(event_ticker, set()).add(str(row.market_ticker or "").upper())
            if btc_spot is not None:
                last_top_spot = btc_spot
        groups_seen += 1
        for event_ticker in sorted(changed_events):
            last_scan_ns = last_scan_ns_by_event.get(event_ticker)
            if scan_stride_ns and last_scan_ns is not None and int(scan_ns) - int(last_scan_ns) < scan_stride_ns:
                continue
            last_scan_ns_by_event[event_ticker] = int(scan_ns)
            new_trades = evaluate_event(
                event_ticker,
                event_states.get(event_ticker, {}),
                scan_ns,
                variants,
                traded_events,
                btc,
                btc_time_values,
                event_cache,
                coinbase_ns,
                coinbase_prices,
                last_top_spot,
                args.max_btc_spot_age_sec,
                model_ttl_override_min=args.model_ttl_override_min,
                market_filter=changed_markets_by_event.get(event_ticker) if args.live_scan_semantics else None,
                static_candidate_markets=static_candidate_markets_by_event.get(event_ticker, set()),
                stats=replay_stats,
            )
            trade_rows.extend(new_trades)

    def process_signal_scan(row: Any) -> None:
        event_ticker = str(getattr(row, "event_ticker", "") or "").upper()
        if not event_ticker or event_ticker not in event_states:
            changed_since_signal_scan.pop(event_ticker, None)
            return
        scan_ns = int(getattr(row, "received_at_ns"))
        action = str(getattr(row, "action", "") or "").lower()
        if action == "skip":
            # The live loop consumed that update batch even if it skipped trading.
            changed_since_signal_scan.pop(event_ticker, None)
            return
        if args.selected_scan_only and action != "selected":
            changed_since_signal_scan.pop(event_ticker, None)
            return
        if args.candidate_scan_only and int(getattr(row, "candidate_count", 0) or 0) <= 0:
            changed_since_signal_scan.pop(event_ticker, None)
            return
        selected_market = str(getattr(row, "selected_market", "") or "").upper()
        if signal_scan_is_full(row):
            market_filter = None
        elif args.selected_market_only and selected_market:
            market_filter = {selected_market}
        else:
            market_filter = changed_since_signal_scan.get(event_ticker, set())
            if not market_filter:
                return
        if args.selected_market_only and selected_market:
            market_filter = {selected_market}
        new_trades = evaluate_event(
            event_ticker,
            event_states.get(event_ticker, {}),
            scan_ns,
            variants,
            traded_events,
            btc,
            btc_time_values,
            event_cache,
            coinbase_ns,
            coinbase_prices,
            last_top_spot,
            args.max_btc_spot_age_sec,
            model_ttl_override_min=args.model_ttl_override_min,
            market_filter=market_filter,
            static_candidate_markets=static_candidate_markets_by_event.get(event_ticker, set()),
            stats=replay_stats,
        )
        trade_rows.extend(new_trades)
        changed_since_signal_scan.pop(event_ticker, None)

    if args.full_stream:
        row_chunks = stream_top_rows(con, top_table, args.start, args.end, args.max_events, args.chunk_vectors)
        if args.use_signal_scan_log:
            signal_scans = load_signal_scans(
                con,
                signal_table,
                args.start,
                args.end,
                candidate_scan_only=args.candidate_scan_only,
                selected_scan_only=args.selected_scan_only,
            )
    else:
        windows_used = discover_replay_windows(con, top_table, args.start, args.end, args.max_events)
        if windows_used.empty:
            row_chunks = iter(())
            if args.use_signal_scan_log:
                signal_scans = pd.DataFrame()
        else:
            install_replay_windows(con, windows_used)
            if args.use_signal_scan_log:
                signal_scans = load_signal_scans(
                    con,
                    signal_table,
                    args.start,
                    args.end,
                    use_replay_windows=True,
                    candidate_scan_only=args.candidate_scan_only,
                    selected_scan_only=args.selected_scan_only,
                )
                if (args.candidate_scan_only or args.selected_scan_only) and not signal_scans.empty:
                    scan_events = set(signal_scans["event_ticker"].astype(str).str.upper())
                    windows_used = windows_used[windows_used["event_ticker"].astype(str).str.upper().isin(scan_events)].copy()
                    install_replay_windows(con, windows_used)
                elif args.candidate_scan_only or args.selected_scan_only:
                    windows_used = windows_used.iloc[0:0].copy()
                    install_replay_windows(con, windows_used)
            seed = load_seed_rows(con, top_table)
            for row in seed.itertuples(index=False):
                event_ticker, btc_spot = apply_row_to_state(row)
                if event_ticker is not None and btc_spot is not None:
                    last_top_spot = btc_spot
            print(
                f"window-pruned replay events={len(windows_used)} seed_rows={len(seed)} "
                f"window_range={ns_to_utc(int(windows_used['replay_start_ns'].min())).isoformat()}.."
                f"{ns_to_utc(int(windows_used['replay_end_ns'].max())).isoformat()}",
                flush=True,
            )
            row_chunks = stream_window_rows(con, top_table, args.chunk_vectors)

    if args.use_signal_scan_log:
        scan_rows = list(signal_scans.itertuples(index=False))
        scan_idx = 0
        for chunk in row_chunks:
            for row in chunk.itertuples(index=False):
                ns = int(row.received_at_ns)
                while scan_idx < len(scan_rows) and int(scan_rows[scan_idx].received_at_ns) < ns:
                    process_signal_scan(scan_rows[scan_idx])
                    groups_seen += 1
                    scan_idx += 1
                event_ticker, btc_spot = apply_row_to_state(row)
                if event_ticker is not None:
                    changed_since_signal_scan.setdefault(event_ticker, set()).add(str(row.market_ticker or "").upper())
                if btc_spot is not None:
                    last_top_spot = btc_spot
                rows_seen += 1
                if args.progress_every_rows and rows_seen % args.progress_every_rows == 0:
                    print(
                        f"streamed_rows={rows_seen} signal_scans={groups_seen} trades={len(trade_rows)}",
                        flush=True,
                    )
                if args.max_rows and rows_seen >= args.max_rows:
                    stopped_early_by_max_rows = True
                    break
            if args.max_rows and rows_seen >= args.max_rows:
                break
        while not stopped_early_by_max_rows and scan_idx < len(scan_rows):
            process_signal_scan(scan_rows[scan_idx])
            groups_seen += 1
            scan_idx += 1
    else:
        for chunk in row_chunks:
            for row in chunk.itertuples(index=False):
                ns = int(row.received_at_ns)
                if pending_ns is None:
                    pending_ns = ns
                if ns != pending_ns:
                    flush_group(pending_rows, pending_ns)
                    pending_rows = []
                    pending_ns = ns
                pending_rows.append(row)
                rows_seen += 1
                if args.progress_every_rows and rows_seen % args.progress_every_rows == 0:
                    print(f"streamed_rows={rows_seen} groups={groups_seen} trades={len(trade_rows)}", flush=True)
                if args.max_rows and rows_seen >= args.max_rows:
                    stopped_early_by_max_rows = True
                    break
            if args.max_rows and rows_seen >= args.max_rows:
                break
        if pending_rows and pending_ns is not None:
            flush_group(pending_rows, pending_ns)

    trades = pd.DataFrame(trade_rows)
    traded_tickers = sorted(trades["market_ticker"].dropna().astype(str).unique()) if not trades.empty else []
    captured_results = load_captured_results(con, lifecycle_table, private_table, traded_tickers)
    con.close()

    trades = settle_trades(trades, captured_results, not args.no_public_fallback, args.public_sleep_sec)
    summary = summarize(trades, variants)

    trades_path = args.output_dir / "btc1h_core_ws_counterfactual_trades.csv"
    summary_path = args.output_dir / "btc1h_core_ws_counterfactual_summary.csv"
    meta_path = args.output_dir / "btc1h_core_ws_counterfactual_meta.json"
    trades.to_csv(trades_path, index=False)
    summary.to_csv(summary_path, index=False)
    meta = {
        "capture_db": str(args.capture_db),
        "top_table": top_table,
        "coinbase_table": coinbase_table,
        "signal_table": signal_table,
        "lifecycle_table": lifecycle_table,
        "private_table": private_table,
        "btc_cache": str(args.btc_cache),
        "recompute_btc_rv": bool(args.recompute_btc_rv),
        "output_dir": str(args.output_dir),
        "rows_seen": rows_seen,
        "groups_seen": groups_seen,
        "full_stream": bool(args.full_stream),
        "live_scan_semantics": bool(args.live_scan_semantics),
        "use_signal_scan_log": bool(args.use_signal_scan_log),
        "candidate_scan_only": bool(args.candidate_scan_only),
        "selected_scan_only": bool(args.selected_scan_only),
        "selected_market_only": bool(args.selected_market_only),
        "signal_scans_loaded": int(len(signal_scans)) if isinstance(signal_scans, pd.DataFrame) else 0,
        "signal_scans_sql_window_pruned": bool(args.use_signal_scan_log and not args.full_stream),
        "signal_scans_sql_candidate_pruned": bool(args.use_signal_scan_log and args.candidate_scan_only),
        "signal_scans_sql_selected_pruned": bool(args.use_signal_scan_log and args.selected_scan_only),
        "scan_stride_sec": float(args.scan_stride_sec),
        "max_btc_spot_age_sec": float(args.max_btc_spot_age_sec),
        "model_ttl_override_min": args.model_ttl_override_min,
        "event_windows": int(len(windows_used)) if isinstance(windows_used, pd.DataFrame) else 0,
        "event_windows_pruned_to_signal_events": bool(
            args.use_signal_scan_log and not args.full_stream and (args.candidate_scan_only or args.selected_scan_only)
        ),
        "event_window_start": ns_to_utc(int(windows_used["replay_start_ns"].min())).isoformat()
        if isinstance(windows_used, pd.DataFrame) and not windows_used.empty
        else None,
        "event_window_end": ns_to_utc(int(windows_used["replay_end_ns"].max())).isoformat()
        if isinstance(windows_used, pd.DataFrame) and not windows_used.empty
        else None,
        "replay_stats": dict(sorted(replay_stats.items())),
        "signals": int(len(trades)),
        "stopped_early_by_max_rows": bool(stopped_early_by_max_rows),
        "settled_signals": int(trades["settled"].sum()) if "settled" in trades else 0,
        "captured_result_markets": int(len(captured_results)),
        "variants": [v.name for v in variants],
        "filters": {
            "min_ttl_min": MIN_TTL_MIN,
            "max_ttl_min": MAX_TTL_MIN,
            "variant_specific_spread_entry_edge_thresholds": True,
            "static_executable_book_prefilter": True,
            "visible_top_qty_min": 1.0,
            "one_trade_per_event_per_variant": True,
            "causal_order": "received_at_ns",
            "scan_stride_sec": float(args.scan_stride_sec),
            "model_ttl_override_min": args.model_ttl_override_min,
            "scan_clock": "captured_signal_scan" if args.use_signal_scan_log else "orderbook_top_receive_groups",
            "changed_ticker_filter": bool(args.live_scan_semantics or args.use_signal_scan_log),
            "candidate_scan_only": bool(args.candidate_scan_only),
            "selected_scan_only": bool(args.selected_scan_only),
            "selected_market_only": bool(args.selected_market_only),
            "signal_scans_sql_window_pruned": bool(args.use_signal_scan_log and not args.full_stream),
            "signal_scans_sql_candidate_pruned": bool(args.use_signal_scan_log and args.candidate_scan_only),
            "signal_scans_sql_selected_pruned": bool(args.use_signal_scan_log and args.selected_scan_only),
            "event_windows_pruned_to_signal_events": bool(
                args.use_signal_scan_log and not args.full_stream and (args.candidate_scan_only or args.selected_scan_only)
            ),
            "max_btc_spot_age_sec": float(args.max_btc_spot_age_sec),
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    print(summary.to_string(index=False))
    print(f"wrote {summary_path}")
    print(f"wrote {trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
