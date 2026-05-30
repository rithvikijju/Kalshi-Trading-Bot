#!/usr/bin/env python3
"""Audit BTC1H selected-signal model parity from a paused capture snapshot.

This is a fast, narrow replay diagnostic for the active BTC1H paper shadow. It
does not search thresholds and it does not prove no missed opportunities. It
recomputes the frozen live strategy only at captured `signal_scan` rows whose
action was `selected`, using the selected market's as-of websocket top book and
an as-of BTC candle slice, then compares the recomputed signal with the values
logged by the live shadow.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import btc_1hr_research_live as live  # noqa: E402


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_selected_signal_model_parity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
DEFAULT_STRATEGY = "high_conf_80_entry70_no_chase"
DEFAULT_CAPTURE_DB = (
    PROJECT_ROOT
    / "runtime"
    / "remote_snapshots"
    / "snapshot_20260521_145951"
    / "btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb"
)
DEFAULT_BTC_CACHE = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_OFFICIAL_TRADES = BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
DEFAULT_SINCE_UTC = "2026-05-18T04:17:44Z"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--btc-cache", type=Path, default=DEFAULT_BTC_CACHE)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--until-utc", default="")
    parser.add_argument("--entry-tol", type=float, default=1e-9)
    parser.add_argument("--p-tol", type=float, default=1e-6)
    parser.add_argument("--edge-tol-cents", type=float, default=1e-4)
    parser.add_argument("--implied-ttl-p-tol", type=float, default=0.001)
    parser.add_argument("--implied-ttl-min-offset-sec", type=int, default=-300)
    parser.add_argument("--implied-ttl-max-offset-sec", type=int, default=300)
    parser.add_argument("--min-forward-official-rows", type=int, default=50)
    parser.add_argument("--max-official-proxy-mismatch-rate", type=float, default=0.02)
    return parser.parse_args()


def parse_ts(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts)


def ns_to_utc(ns: int) -> pd.Timestamp:
    return pd.Timestamp(int(ns), unit="ns", tz="UTC")


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            "select count(*) from information_schema.tables where table_schema='main' and table_name=?",
            [table],
        ).fetchone()[0]
    )


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    if not table_exists(con, table):
        return set()
    return {str(row[1]) for row in con.execute(f"pragma table_info({table})").fetchall()}


def optional_select(columns: set[str], name: str, alias: str, sql_type: str = "VARCHAR") -> str:
    if name in columns:
        return f"{name} as {alias}"
    return f"NULL::{sql_type} as {alias}"


def load_selected_signals(
    con: duckdb.DuckDBPyConnection,
    since_utc: str,
    until_utc: str,
) -> pd.DataFrame:
    if not table_exists(con, "signal_scan"):
        return pd.DataFrame()
    columns = table_columns(con, "signal_scan")
    where = [
        "event_ticker LIKE 'KXBTCD-%'",
        "lower(coalesce(action, '')) = 'selected'",
        "coalesce(candidate_count, 0) > 0",
    ]
    params: list[Any] = []
    if since_utc:
        where.append("try_cast(received_at_utc as timestamptz) >= try_cast(? as timestamptz)")
        params.append(since_utc)
    if until_utc:
        where.append("try_cast(received_at_utc as timestamptz) < try_cast(? as timestamptz)")
        params.append(until_utc)
    df = con.execute(
        f"""
        select
            received_at_ns as selected_received_at_ns,
            try_cast(received_at_utc as timestamptz) as selected_received_at_utc,
            reason,
            event_ticker,
            changed_markets,
            evaluated_markets,
            candidate_count,
            selected_market as market_ticker,
            selected_side as side,
            {optional_select(columns, "signal_strategy", "selected_signal_strategy")},
            {optional_select(columns, "model_ttl_policy", "selected_model_ttl_policy")},
            {optional_select(columns, "model_policy_version", "selected_model_policy_version")},
            entry_price as selected_entry_price,
            net_edge_cents as selected_net_edge_cents,
            model_p_yes as selected_model_p_yes,
            {optional_select(columns, "edge_threshold_cents", "selected_edge_threshold_cents", "DOUBLE")},
            {optional_select(columns, "spread_cents", "selected_spread_cents", "DOUBLE")},
            {optional_select(columns, "top_visible_qty", "selected_top_visible_qty", "DOUBLE")},
            {optional_select(columns, "quote_received_at_ns", "selected_quote_received_at_ns", "BIGINT")},
            {optional_select(columns, "quote_age_ms", "selected_quote_age_ms", "DOUBLE")},
            {optional_select(columns, "ttl_min", "selected_ttl_min", "DOUBLE")},
            {optional_select(columns, "close_time", "selected_close_time")},
            btc_spot as selected_btc_spot,
            {optional_select(columns, "btc_candle_time", "selected_btc_candle_time")},
            {optional_select(columns, "btc_candle_age_sec", "selected_btc_candle_age_sec", "DOUBLE")},
            {optional_select(columns, "btc_rv60", "selected_btc_rv60", "DOUBLE")},
            {optional_select(columns, "btc_ret_10m_usd", "selected_btc_ret_10m_usd", "DOUBLE")},
            latency_ms,
            blocked_events,
            action,
            detail
        from signal_scan
        where {' and '.join(where)}
        order by received_at_ns
        """,
        params,
    ).fetchdf()
    if df.empty:
        return df
    for col in ("event_ticker", "market_ticker"):
        df[col] = df[col].fillna("").astype(str).str.upper()
    df["side"] = df["side"].fillna("").astype(str).str.lower()
    df["selected_received_at_ns"] = pd.to_numeric(df["selected_received_at_ns"], errors="coerce").astype("Int64")
    return df.dropna(subset=["selected_received_at_ns", "event_ticker", "market_ticker"]).copy()


def load_asof_top(
    con: duckdb.DuckDBPyConnection,
    market_ticker: str,
    scan_ns: int,
) -> dict[str, Any] | None:
    if not table_exists(con, "ws_orderbook_top"):
        return None
    row = con.execute(
        """
        select
            received_at_ns,
            try_cast(received_at_utc as timestamptz) as received_at_utc,
            market_ticker,
            event_ticker,
            seq,
            yes_bid,
            yes_bid_qty,
            yes_ask,
            yes_ask_qty,
            no_bid,
            no_bid_qty,
            no_ask,
            no_ask_qty,
            btc_spot,
            source
        from ws_orderbook_top
        where upper(market_ticker) = ?
          and received_at_ns <= ?
        order by received_at_ns desc, coalesce(seq, 0) desc
        limit 1
        """,
        [market_ticker.upper(), int(scan_ns)],
    ).fetchdf()
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def load_btc_cache(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_parquet(path)
    except Exception:
        con = duckdb.connect()
        try:
            df = con.execute("select * from read_parquet(?)", [str(path)]).fetchdf()
        finally:
            con.close()
    if "time" not in df.columns and "available_at" in df.columns:
        df = df.rename(columns={"available_at": "time"})
    if "time" not in df.columns:
        raise ValueError(f"{path} must contain a time or available_at column")
    if {"open", "high", "low", "close", "volume"}.issubset(set(df.columns)):
        out = live.normalize_btc_candles(df)
    else:
        out = df.copy()
        out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
        out["close"] = pd.to_numeric(out["close"], errors="coerce")
        out = out.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    return out.sort_values("time").reset_index(drop=True)


def asof_btc(btc: pd.DataFrame, scan_ts: pd.Timestamp) -> pd.DataFrame:
    times = pd.to_datetime(btc["time"], utc=True, errors="coerce")
    return btc.loc[times <= scan_ts].copy().reset_index(drop=True)


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def make_event_and_market(event_ticker: str, market_ticker: str, scan_ts: pd.Timestamp) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    close_dt = live.parse_event_close_from_ticker(event_ticker)
    if close_dt is None:
        return None, None
    floor_strike = None
    try:
        if "-T" in market_ticker.upper():
            floor_strike = float(market_ticker.upper().rsplit("-T", 1)[1]) + 0.01
    except (TypeError, ValueError):
        floor_strike = None
    market = {
        "ticker": market_ticker,
        "event_ticker": event_ticker,
        "status": "open",
        "close_time": close_dt.isoformat(),
        "floor_strike": floor_strike,
        "cap_strike": None,
        "strike_type": "greater",
        "title": "",
    }
    event = live.build_research_event(event_ticker, [market], scan_ts.floor("us").to_pydatetime())
    return event, market if event is not None else None


def make_quote(row: dict[str, Any]) -> live.BookQuote:
    return live.BookQuote(
        ticker=str(row.get("market_ticker") or "").upper(),
        yes_bid=finite_float(row.get("yes_bid")),
        yes_bid_qty=finite_float(row.get("yes_bid_qty")) or 0.0,
        yes_ask=finite_float(row.get("yes_ask")),
        yes_ask_qty=finite_float(row.get("yes_ask_qty")) or 0.0,
        no_bid=finite_float(row.get("no_bid")),
        no_bid_qty=finite_float(row.get("no_bid_qty")) or 0.0,
        no_ask=finite_float(row.get("no_ask")),
        no_ask_qty=finite_float(row.get("no_ask_qty")) or 0.0,
        received_at_ns=int(row["received_at_ns"]) if row.get("received_at_ns") is not None else None,
    )


def signed_abs_diff(left: Any, right: Any) -> tuple[float | None, float | None]:
    lval = finite_float(left)
    rval = finite_float(right)
    if lval is None or rval is None:
        return None, None
    diff = lval - rval
    return diff, abs(diff)


def infer_implied_ttl(
    event: dict[str, Any],
    market: dict[str, Any],
    emp_cache: dict,
    spot: float,
    selected_p_yes: float | None,
    current_vol: float | None,
    min_offset_sec: int,
    max_offset_sec: int,
) -> dict[str, Any]:
    actual_ttl_min = float(event["ttl_hours"]) * 60.0
    out: dict[str, Any] = {
        "actual_ttl_min": actual_ttl_min,
        "asof_btc_rv60": current_vol,
        "implied_ttl_status": "not_checked",
    }
    if selected_p_yes is None or not math.isfinite(selected_p_yes):
        out["implied_ttl_status"] = "missing_selected_p"
        return out
    if current_vol is None or not math.isfinite(float(current_vol)):
        out["implied_ttl_status"] = "missing_current_vol"
        return out
    parsed = live.base_strategy.parse_market(market)
    floor = live.optional_float(parsed.get("floor"))
    if floor is None:
        out["implied_ttl_status"] = "missing_floor"
        return out
    horizons = sorted(emp_cache.keys())
    if not horizons:
        out["implied_ttl_status"] = "missing_emp_cache"
        return out
    best: tuple[float, int, float, float, int] | None = None
    for offset_sec in range(int(min_offset_sec), int(max_offset_sec) + 1):
        ttl_min = actual_ttl_min + offset_sec / 60.0
        if ttl_min <= 0:
            continue
        horizon = min(horizons, key=lambda h: abs(h - ttl_min))
        p_yes = live.blended_p_above(float(spot), float(floor), float(ttl_min), emp_cache[horizon], float(current_vol))
        if not math.isfinite(p_yes):
            continue
        diff = abs(float(p_yes) - float(selected_p_yes))
        if best is None or diff < best[0]:
            best = (diff, offset_sec, ttl_min, float(p_yes), int(horizon))
    if best is None:
        out["implied_ttl_status"] = "no_finite_ttl_match"
        return out
    diff, offset_sec, ttl_min, p_yes, horizon = best
    out.update(
        {
            "implied_ttl_model_p_yes_abs_diff": diff,
            "implied_ttl_offset_sec": offset_sec,
            "implied_ttl_min": ttl_min,
            "implied_ttl_model_p_yes": p_yes,
            "implied_ttl_horizon_min": horizon,
            "implied_ttl_status": "computed",
        }
    )
    return out


def recompute_rows(args: argparse.Namespace, selected: pd.DataFrame, con: duckdb.DuckDBPyConnection, btc: pd.DataFrame) -> pd.DataFrame:
    live.set_signal_strategy(args.strategy)
    rows: list[dict[str, Any]] = []
    emp_cache_by_event: dict[str, dict[str, Any]] = {}
    for item in selected.itertuples(index=False):
        scan_ns = int(item.selected_received_at_ns)
        scan_ts = ns_to_utc(scan_ns)
        event_ticker = str(item.event_ticker).upper()
        market_ticker = str(item.market_ticker).upper()
        side = str(item.side).lower()
        top = load_asof_top(con, market_ticker, scan_ns)
        row: dict[str, Any] = {
            "selected_received_at_ns": scan_ns,
            "selected_received_at_utc": scan_ts.isoformat(),
            "event_ticker": event_ticker,
            "market_ticker": market_ticker,
            "side": side,
            "selected_entry_price": finite_float(item.selected_entry_price),
            "selected_model_p_yes": finite_float(item.selected_model_p_yes),
            "selected_net_edge_cents": finite_float(item.selected_net_edge_cents),
            "selected_edge_threshold_cents": finite_float(getattr(item, "selected_edge_threshold_cents", None)),
            "selected_spread_cents": finite_float(getattr(item, "selected_spread_cents", None)),
            "selected_top_visible_qty": finite_float(getattr(item, "selected_top_visible_qty", None)),
            "selected_quote_received_at_ns": int(item.selected_quote_received_at_ns)
            if finite_float(getattr(item, "selected_quote_received_at_ns", None)) is not None
            else None,
            "selected_quote_age_ms": finite_float(getattr(item, "selected_quote_age_ms", None)),
            "selected_ttl_min": finite_float(getattr(item, "selected_ttl_min", None)),
            "selected_close_time": str(getattr(item, "selected_close_time", "") or ""),
            "selected_btc_spot": finite_float(item.selected_btc_spot),
            "selected_btc_candle_time": str(getattr(item, "selected_btc_candle_time", "") or ""),
            "selected_btc_candle_age_sec": finite_float(getattr(item, "selected_btc_candle_age_sec", None)),
            "selected_btc_rv60": finite_float(getattr(item, "selected_btc_rv60", None)),
            "selected_btc_ret_10m_usd": finite_float(getattr(item, "selected_btc_ret_10m_usd", None)),
            "selected_signal_strategy": str(getattr(item, "selected_signal_strategy", "") or ""),
            "selected_model_ttl_policy": str(getattr(item, "selected_model_ttl_policy", "") or ""),
            "selected_model_policy_version": str(getattr(item, "selected_model_policy_version", "") or ""),
            "reason": str(item.reason or ""),
            "changed_markets": int(item.changed_markets or 0),
            "evaluated_markets": int(item.evaluated_markets or 0),
            "candidate_count": int(item.candidate_count or 0),
            "latency_ms": finite_float(item.latency_ms),
            "blocked_events": int(item.blocked_events or 0),
            "parity_status": "unchecked",
        }
        if top is None:
            row["parity_status"] = "missing_asof_top"
            rows.append(row)
            continue
        quote = make_quote(top)
        row.update(
            {
                "top_received_at_ns": int(top["received_at_ns"]),
                "top_received_at_utc": pd.Timestamp(top["received_at_utc"]).isoformat(),
                "quote_age_ms": max(0.0, (scan_ns - int(top["received_at_ns"])) / 1_000_000.0),
                "top_yes_bid": finite_float(top.get("yes_bid")),
                "top_yes_ask": finite_float(top.get("yes_ask")),
                "top_yes_ask_qty": finite_float(top.get("yes_ask_qty")),
                "top_no_bid": finite_float(top.get("no_bid")),
                "top_no_ask": finite_float(top.get("no_ask")),
                "top_no_ask_qty": finite_float(top.get("no_ask_qty")),
                "top_btc_spot": finite_float(top.get("btc_spot")),
                "top_source": str(top.get("source") or ""),
            }
        )
        event, market = make_event_and_market(event_ticker, market_ticker, scan_ts)
        spot = finite_float(item.selected_btc_spot)
        if event is None or market is None:
            row["parity_status"] = "event_parse_failed"
            rows.append(row)
            continue
        if spot is None or spot <= 0.0:
            row["parity_status"] = "missing_selected_btc_spot"
            rows.append(row)
            continue
        btc_asof = asof_btc(btc, scan_ts)
        if len(btc_asof) < 1440:
            row["parity_status"] = "insufficient_btc_asof"
            rows.append(row)
            continue
        emp_cache = emp_cache_by_event.get(event_ticker)
        if emp_cache is None:
            emp_cache = live.build_event_emp_cache(btc_asof, event, verbose=False)
            emp_cache_by_event[event_ticker] = emp_cache
        if not emp_cache:
            row["parity_status"] = "missing_emp_cache"
            rows.append(row)
            continue
        current_vol = live.latest_rv60(btc_asof)
        row["asof_btc_last_time"] = str(btc_asof.iloc[-1]["time"]) if len(btc_asof) else ""
        row.update(
            infer_implied_ttl(
                event,
                market,
                emp_cache,
                float(spot),
                finite_float(item.selected_model_p_yes),
                current_vol,
                args.implied_ttl_min_offset_sec,
                args.implied_ttl_max_offset_sec,
            )
        )
        if (
            row.get("implied_ttl_status") == "computed"
            and finite_float(row.get("implied_ttl_model_p_yes_abs_diff")) is not None
            and float(row["implied_ttl_model_p_yes_abs_diff"]) <= float(args.implied_ttl_p_tol)
        ):
            row["implied_ttl_status"] = "pass"
        captured_ttl = finite_float(row.get("selected_ttl_min"))
        selected_p = finite_float(item.selected_model_p_yes)
        selected_rv60 = finite_float(row.get("selected_btc_rv60"))
        captured_vol = selected_rv60 if selected_rv60 is not None else current_vol
        row["captured_ttl_status"] = "not_available"
        if captured_ttl is not None and captured_ttl > 0 and selected_p is not None:
            parsed = live.base_strategy.parse_market(market)
            floor = live.optional_float(parsed.get("floor"))
            horizons = sorted(emp_cache.keys())
            if floor is None:
                row["captured_ttl_status"] = "missing_floor"
            elif captured_vol is None or not math.isfinite(float(captured_vol)):
                row["captured_ttl_status"] = "missing_current_vol"
            elif not horizons:
                row["captured_ttl_status"] = "missing_emp_cache"
            else:
                horizon = min(horizons, key=lambda h: abs(h - float(captured_ttl)))
                captured_p = live.blended_p_above(float(spot), float(floor), float(captured_ttl), emp_cache[horizon], float(captured_vol))
                row.update(
                    {
                        "captured_ttl_model_p_yes": captured_p,
                        "captured_ttl_model_p_yes_abs_diff": abs(float(captured_p) - float(selected_p))
                        if math.isfinite(float(captured_p))
                        else None,
                        "captured_ttl_horizon_min": int(horizon),
                        "captured_ttl_rv60": captured_vol,
                    }
                )
                row["captured_ttl_status"] = (
                    "pass"
                    if row["captured_ttl_model_p_yes_abs_diff"] is not None
                    and float(row["captured_ttl_model_p_yes_abs_diff"]) <= float(args.p_tol)
                    else "value_mismatch"
                )
        recomputed = live.signal_from_book(
            event,
            market,
            quote,
            btc_asof,
            emp_cache,
            spot=spot,
            contracts=live.RESEARCH_SIGNAL_CONTRACTS,
            min_edge_cents=live.RESEARCH_MIN_EDGE_CENTS,
            max_spread_cents=live.RESEARCH_MAX_SPREAD_CENTS,
            now=scan_ts.floor("us").to_pydatetime(),
            signal_strategy=args.strategy,
        )
        if recomputed is None:
            row["parity_status"] = "recomputed_no_signal"
            rows.append(row)
            continue
        row.update(
            {
                "recomputed_side": recomputed.side,
                "recomputed_entry_price": recomputed.entry_price,
                "recomputed_model_p_yes": recomputed.model_p_yes,
                "recomputed_net_edge_cents": recomputed.net_edge_cents,
                "recomputed_edge_threshold_cents": recomputed.edge_threshold_cents,
                "recomputed_spread_cents": recomputed.spread_cents,
                "recomputed_top_visible_qty": recomputed.top_visible_qty,
                "recomputed_ttl_min": recomputed.ttl_min,
                "recomputed_strike": recomputed.strike,
            }
        )
        entry_diff, entry_abs = signed_abs_diff(recomputed.entry_price, item.selected_entry_price)
        p_diff, p_abs = signed_abs_diff(recomputed.model_p_yes, item.selected_model_p_yes)
        edge_diff, edge_abs = signed_abs_diff(recomputed.net_edge_cents, item.selected_net_edge_cents)
        top_entry = quote.yes_ask if side == "yes" else quote.no_ask
        top_entry_diff, top_entry_abs = signed_abs_diff(top_entry, item.selected_entry_price)
        row.update(
            {
                "entry_price_diff": entry_diff,
                "entry_price_abs_diff": entry_abs,
                "model_p_yes_diff": p_diff,
                "model_p_yes_abs_diff": p_abs,
                "net_edge_cents_diff": edge_diff,
                "net_edge_cents_abs_diff": edge_abs,
                "top_entry_price": top_entry,
                "top_entry_price_diff": top_entry_diff,
                "top_entry_price_abs_diff": top_entry_abs,
            }
        )
        checks = [
            recomputed.side == side,
            entry_abs is not None and entry_abs <= args.entry_tol,
            p_abs is not None and p_abs <= args.p_tol,
            edge_abs is not None and edge_abs <= args.edge_tol_cents,
            top_entry_abs is not None and top_entry_abs <= args.entry_tol,
        ]
        row["parity_status"] = "pass" if all(checks) else "value_mismatch"
        rows.append(row)
    return pd.DataFrame(rows)


def apply_window(df: pd.DataFrame, col: str, since_utc: str, until_utc: str) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return df.copy()
    out = df.copy()
    ts = pd.to_datetime(out[col], utc=True, errors="coerce")
    keep = ts.notna()
    since = parse_ts(since_utc)
    until = parse_ts(until_utc)
    if since is not None:
        keep &= ts >= since
    if until is not None:
        keep &= ts < until
    return out.loc[keep].copy()


def load_official(path: Path, ledger: str, since_utc: str, until_utc: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty or "ledger" not in df.columns:
        return pd.DataFrame()
    df = df.loc[df["ledger"].astype(str).eq(ledger)].copy()
    df = apply_window(df, "created_at", since_utc, until_utc)
    status = df.get("status", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
    return normalize_keys(df.loc[status.eq("paper_filled")].copy())


def load_decisions(con: duckdb.DuckDBPyConnection, since_utc: str, until_utc: str) -> pd.DataFrame:
    if not table_exists(con, "order_decision"):
        return pd.DataFrame()
    where: list[str] = []
    params: list[Any] = []
    if since_utc:
        where.append("try_cast(received_at_utc as timestamptz) >= try_cast(? as timestamptz)")
        params.append(since_utc)
    if until_utc:
        where.append("try_cast(received_at_utc as timestamptz) < try_cast(? as timestamptz)")
        params.append(until_utc)
    where_sql = "where " + " and ".join(where) if where else ""
    df = con.execute(
        f"""
        select
            received_at_ns as decision_received_at_ns,
            try_cast(received_at_utc as timestamptz) as decision_received_at_utc,
            action as decision_action,
            event_ticker,
            market_ticker,
            side,
            contracts as decision_contracts,
            entry_price as decision_entry_price,
            yes_limit_price as decision_yes_limit_price,
            net_edge_cents as decision_net_edge_cents,
            btc_spot as decision_btc_spot,
            estimated_cost as decision_estimated_cost,
            detail as decision_detail
        from order_decision
        {where_sql}
        order by received_at_ns
        """,
        params,
    ).fetchdf()
    return normalize_keys(df)


def normalize_keys(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in ("event_ticker", "market_ticker"):
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.upper()
    if "side" in out.columns:
        out["side"] = out["side"].fillna("").astype(str).str.lower()
    return out


def add_signal_decision_occurrence(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = normalize_keys(df)
    out["_sort_ts"] = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    out = out.sort_values(["event_ticker", "market_ticker", "side", "_sort_ts"], kind="mergesort")
    out["_chain_occurrence"] = out.groupby(["event_ticker", "market_ticker", "side"], dropna=False).cumcount()
    out["chain_key"] = (
        out["event_ticker"].astype(str)
        + "|"
        + out["market_ticker"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|"
        + out["_chain_occurrence"].astype(str)
    )
    return out.drop(columns=["_sort_ts"])


def add_fill_occurrence(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = normalize_keys(df)
    out["_sort_ts"] = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    out = out.sort_values(["market_ticker", "side", "_sort_ts"], kind="mergesort")
    out["_fill_occurrence"] = out.groupby(["market_ticker", "side"], dropna=False).cumcount()
    out["fill_key"] = (
        out["market_ticker"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|"
        + out["_fill_occurrence"].astype(str)
    )
    return out.drop(columns=["_sort_ts"])


def boolish(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.lower().isin({"true", "1", "yes"})


def max_drawdown(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if nums.size == 0:
        return 0.0
    equity = np.cumsum(nums)
    return float(np.min(equity - np.maximum.accumulate(equity)))


def sharpe(values: pd.Series) -> float:
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if nums.size < 2:
        return 0.0
    std = float(nums.std(ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(nums.mean() / std * math.sqrt(nums.size))


def summarize_official(df: pd.DataFrame, holdout: str) -> dict[str, Any]:
    if df.empty:
        return {
            "holdout": holdout,
            "official_rows": 0,
            "official_pnl": 0.0,
            "official_premium": 0.0,
            "official_win_rate": 0.0,
            "max_drawdown": 0.0,
            "trade_sharpe": 0.0,
            "official_proxy_mismatches": 0,
            "official_proxy_mismatch_rate": 0.0,
        }
    pnl = pd.to_numeric(df["official_pnl"], errors="coerce").fillna(0.0)
    premium = pd.to_numeric(df["official_premium"], errors="coerce").fillna(0.0)
    wins = boolish(df["official_win"])
    mismatches = boolish(df.get("official_proxy_result_mismatch", pd.Series(dtype=str))).sum()
    return {
        "holdout": holdout,
        "official_rows": int(len(df)),
        "official_pnl": round(float(pnl.sum()), 6),
        "official_premium": round(float(premium.sum()), 6),
        "official_win_rate": round(float(wins.mean()), 6) if len(wins) else 0.0,
        "max_drawdown": round(max_drawdown(pnl), 6),
        "trade_sharpe": round(sharpe(pnl), 6),
        "official_proxy_mismatches": int(mismatches),
        "official_proxy_mismatch_rate": round(float(mismatches) / len(df), 6) if len(df) else 0.0,
    }


def build_official_holdouts(official: pd.DataFrame) -> pd.DataFrame:
    rows = [summarize_official(official, "H5_forward_selected_signal_all")]
    if not official.empty:
        work = official.copy()
        work["_date"] = pd.to_datetime(work["created_at"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
        for date, group in work.groupby("_date", sort=True):
            rows.append(summarize_official(group.drop(columns=["_date"]), f"H5_forward_selected_signal_{date}"))
    return pd.DataFrame(rows)


def build_selected_official_impact(
    parity: pd.DataFrame,
    decisions: pd.DataFrame,
    official: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if parity.empty:
        return pd.DataFrame(), pd.DataFrame()
    selected = add_signal_decision_occurrence(parity, "selected_received_at_utc")
    decisions_keyed = add_signal_decision_occurrence(decisions, "decision_received_at_utc")
    chain = selected.merge(
        decisions_keyed,
        on=["chain_key", "event_ticker", "market_ticker", "side"],
        how="left",
        suffixes=("", "_decision"),
    )
    if not chain.empty:
        chain["scan_ttl_recomputed_signal"] = (
            chain.get("recomputed_side", pd.Series("", index=chain.index)).fillna("").astype(str).str.lower().eq(chain["side"].astype(str))
            & (pd.to_numeric(chain.get("entry_price_abs_diff"), errors="coerce") <= float(args.entry_tol))
            & (pd.to_numeric(chain.get("top_entry_price_abs_diff"), errors="coerce") <= float(args.entry_tol))
        )
        chain["scan_ttl_exact_model_pass"] = chain.get("parity_status", pd.Series("", index=chain.index)).fillna("").astype(str).eq("pass")
        chain["implied_ttl_pass"] = chain.get("implied_ttl_status", pd.Series("", index=chain.index)).fillna("").astype(str).eq("pass")
        chain["captured_ttl_pass"] = chain.get("captured_ttl_status", pd.Series("", index=chain.index)).fillna("").astype(str).eq("pass")
    fills = decisions_keyed.loc[
        decisions_keyed.get("decision_action", pd.Series("", index=decisions_keyed.index)).fillna("").astype(str).str.lower().eq("paper_fill")
    ].copy()
    fills = add_fill_occurrence(fills, "decision_received_at_utc")
    official_keyed = add_fill_occurrence(official, "created_at")
    if not fills.empty:
        fill_keys = fills[["chain_key", "fill_key"]].copy()
        chain = chain.merge(fill_keys, on="chain_key", how="left")
    else:
        chain["fill_key"] = pd.NA
    if not official_keyed.empty:
        official_cols = [
            "fill_key",
            "created_at",
            "status",
            "official_status",
            "official_result",
            "official_win",
            "official_premium",
            "official_pnl",
            "proxy_result",
            "proxy_pnl",
            "official_proxy_result_mismatch",
            "expiration_value",
        ]
        official_cols = [col for col in official_cols if col in official_keyed.columns]
        chain = chain.merge(official_keyed[official_cols], on="fill_key", how="left", suffixes=("", "_official"))
    else:
        chain["official_pnl"] = np.nan
    chain["has_official_fill"] = pd.to_numeric(chain.get("official_pnl"), errors="coerce").notna()

    def summarize_bucket(name: str, group: pd.DataFrame) -> dict[str, Any]:
        official_rows = group.loc[group["has_official_fill"]].copy()
        pnl = pd.to_numeric(official_rows.get("official_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        win = boolish(official_rows.get("official_win", pd.Series(dtype=str)))
        mismatches = boolish(official_rows.get("official_proxy_result_mismatch", pd.Series(dtype=str))).sum()
        return {
            "bucket": name,
            "selected_rows": int(len(group)),
            "decision_rows": int(group.get("decision_action", pd.Series(dtype=str)).notna().sum()),
            "paper_fill_decisions": int(
                group.get("decision_action", pd.Series("", index=group.index)).fillna("").astype(str).str.lower().eq("paper_fill").sum()
            ),
            "official_rows": int(len(official_rows)),
            "official_pnl": round(float(pnl.sum()), 6) if len(pnl) else 0.0,
            "official_win_rate": round(float(win.mean()), 6) if len(win) else 0.0,
            "official_proxy_mismatches": int(mismatches),
            "official_proxy_mismatch_rate": round(float(mismatches) / len(official_rows), 6) if len(official_rows) else 0.0,
            "max_drawdown": round(max_drawdown(pnl), 6) if len(pnl) else 0.0,
        }

    buckets = {
        "selected_all": chain,
        "paper_fill_all": chain.loc[
            chain.get("decision_action", pd.Series("", index=chain.index)).fillna("").astype(str).str.lower().eq("paper_fill")
        ],
        "selected_scan_ttl_recomputed_signal": chain.loc[chain["scan_ttl_recomputed_signal"]],
        "selected_scan_ttl_no_signal": chain.loc[~chain["scan_ttl_recomputed_signal"]],
        "paper_fill_scan_ttl_recomputed_signal": chain.loc[
            chain.get("decision_action", pd.Series("", index=chain.index)).fillna("").astype(str).str.lower().eq("paper_fill")
            & chain["scan_ttl_recomputed_signal"]
        ],
        "paper_fill_scan_ttl_no_signal": chain.loc[
            chain.get("decision_action", pd.Series("", index=chain.index)).fillna("").astype(str).str.lower().eq("paper_fill")
            & ~chain["scan_ttl_recomputed_signal"]
        ],
        "selected_exact_scan_ttl_model_pass": chain.loc[chain["scan_ttl_exact_model_pass"]],
        "selected_implied_cached_ttl_pass": chain.loc[chain["implied_ttl_pass"]],
        "selected_captured_ttl_model_pass": chain.loc[chain["captured_ttl_pass"]],
    }
    impact = pd.DataFrame([summarize_bucket(name, group) for name, group in buckets.items()])
    return chain, impact


def finite_max(series: pd.Series) -> float:
    nums = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(nums.max()), 9) if len(nums) else 0.0


def finite_min(series: pd.Series) -> float:
    nums = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(nums.min()), 9) if len(nums) else 0.0


def build_summary(parity: pd.DataFrame, official: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    holdouts = build_official_holdouts(official)
    all_official = holdouts[holdouts["holdout"].eq("H5_forward_selected_signal_all")].iloc[0].to_dict()
    selected_rows = int(len(parity))
    pass_rows = int(parity["parity_status"].eq("pass").sum()) if not parity.empty else 0
    implied_pass_rows = int(parity["implied_ttl_status"].eq("pass").sum()) if "implied_ttl_status" in parity else 0
    captured_ttl_available_rows = int(pd.to_numeric(parity.get("selected_ttl_min", pd.Series(dtype=float)), errors="coerce").notna().sum())
    captured_ttl_pass_rows = int(parity.get("captured_ttl_status", pd.Series(dtype=str)).eq("pass").sum())
    official_rows = int(all_official["official_rows"])
    mismatch_rate = float(all_official["official_proxy_mismatch_rate"])
    blockers = []
    if selected_rows == 0:
        blockers.append("no_selected_signal_rows")
    if pass_rows != selected_rows:
        blockers.append("selected_signal_model_parity_failed")
        if selected_rows and captured_ttl_available_rows == selected_rows:
            if captured_ttl_pass_rows != selected_rows:
                blockers.append("captured_ttl_model_parity_failed")
        elif selected_rows and implied_pass_rows == selected_rows:
            blockers.append("exact_live_cached_ttl_missing_current_rows")
        elif selected_rows:
            blockers.append("implied_ttl_model_parity_failed")
    if official_rows < args.min_forward_official_rows:
        blockers.append("too_few_forward_official_rows")
    if mismatch_rate > args.max_official_proxy_mismatch_rate:
        blockers.append("official_proxy_mismatch_gate_failed")
    blockers.append("actual_selected_signal_model_parity_only_not_all_window")
    summary = pd.DataFrame(
        [
            {
                "ledger": args.ledger,
                "strategy": args.strategy,
                "capture_db": str(args.capture_db),
                "btc_cache": str(args.btc_cache),
                "official_trades": str(args.official_trades),
                "selected_signal_rows": selected_rows,
                "parity_pass_rows": pass_rows,
                "parity_fail_rows": selected_rows - pass_rows,
                "parity_pass_rate": round(float(pass_rows) / selected_rows, 6) if selected_rows else 0.0,
                "implied_ttl_pass_rows": implied_pass_rows,
                "implied_ttl_fail_rows": selected_rows - implied_pass_rows,
                "implied_ttl_pass_rate": round(float(implied_pass_rows) / selected_rows, 6) if selected_rows else 0.0,
                "captured_ttl_available_rows": captured_ttl_available_rows,
                "captured_ttl_pass_rows": captured_ttl_pass_rows,
                "captured_ttl_fail_rows": captured_ttl_available_rows - captured_ttl_pass_rows,
                "captured_ttl_pass_rate": round(float(captured_ttl_pass_rows) / captured_ttl_available_rows, 6)
                if captured_ttl_available_rows
                else 0.0,
                "max_captured_ttl_model_p_yes_abs_diff": finite_max(
                    parity.get("captured_ttl_model_p_yes_abs_diff", pd.Series(dtype=float))
                ),
                "max_implied_ttl_model_p_yes_abs_diff": finite_max(
                    parity.get("implied_ttl_model_p_yes_abs_diff", pd.Series(dtype=float))
                ),
                "min_implied_ttl_offset_sec": finite_min(parity.get("implied_ttl_offset_sec", pd.Series(dtype=float))),
                "max_implied_ttl_offset_sec": finite_max(parity.get("implied_ttl_offset_sec", pd.Series(dtype=float))),
                "max_entry_price_abs_diff": finite_max(parity.get("entry_price_abs_diff", pd.Series(dtype=float))),
                "max_model_p_yes_abs_diff": finite_max(parity.get("model_p_yes_abs_diff", pd.Series(dtype=float))),
                "max_net_edge_cents_abs_diff": finite_max(parity.get("net_edge_cents_abs_diff", pd.Series(dtype=float))),
                "max_top_entry_price_abs_diff": finite_max(parity.get("top_entry_price_abs_diff", pd.Series(dtype=float))),
                "max_quote_age_ms": finite_max(parity.get("quote_age_ms", pd.Series(dtype=float))),
                "official_rows": official_rows,
                "official_pnl": all_official["official_pnl"],
                "official_win_rate": all_official["official_win_rate"],
                "official_proxy_mismatch_rate": mismatch_rate,
                "deployable_now": False,
                "deploy_blockers": ";".join(blockers),
            }
        ]
    )
    return summary, holdouts


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    btc = load_btc_cache(args.btc_cache)
    con = duckdb.connect(str(args.capture_db), read_only=True)
    try:
        selected = load_selected_signals(con, args.since_utc, args.until_utc)
        decisions = load_decisions(con, args.since_utc, args.until_utc)
        parity = recompute_rows(args, selected, con, btc)
    finally:
        con.close()
    official = load_official(args.official_trades, args.ledger, args.since_utc, args.until_utc)
    selected_official, impact = build_selected_official_impact(parity, decisions, official, args)
    summary, holdouts = build_summary(parity, official, args)
    if not impact.empty and not summary.empty:
        summary["scan_ttl_recomputed_official_rows"] = int(
            impact.loc[impact["bucket"].eq("paper_fill_scan_ttl_recomputed_signal"), "official_rows"].fillna(0).sum()
        )
        summary["scan_ttl_recomputed_official_pnl"] = round(
            float(impact.loc[impact["bucket"].eq("paper_fill_scan_ttl_recomputed_signal"), "official_pnl"].fillna(0).sum()),
            6,
        )
        summary["scan_ttl_no_signal_official_rows"] = int(
            impact.loc[impact["bucket"].eq("paper_fill_scan_ttl_no_signal"), "official_rows"].fillna(0).sum()
        )
        summary["scan_ttl_no_signal_official_pnl"] = round(
            float(impact.loc[impact["bucket"].eq("paper_fill_scan_ttl_no_signal"), "official_pnl"].fillna(0).sum()),
            6,
        )

    parity.to_csv(args.out_dir / "btc1h_selected_signal_model_parity.csv", index=False)
    selected_official.to_csv(args.out_dir / "btc1h_selected_signal_official_impact_rows.csv", index=False)
    impact.to_csv(args.out_dir / "btc1h_selected_signal_official_impact_summary.csv", index=False)
    summary.to_csv(args.out_dir / "btc1h_selected_signal_model_parity_summary.csv", index=False)
    holdouts.to_csv(args.out_dir / "btc1h_selected_signal_model_parity_holdouts.csv", index=False)
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "actual_selected_signal_model_parity_only",
        "note": (
            "Recomputes live BTC1H strategy values only at captured selected signal_scan rows. "
            "It also reports whether selected probabilities can be reconciled by an implied cached "
            "event TTL, because the current live loop stores event ttl_hours at event refresh time. "
            "This is not a full all-window counterfactual replay and cannot prove missed no-signal rows."
        ),
        "summary": summary.iloc[0].to_dict() if not summary.empty else {},
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC1H Selected Signal Model Parity",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"Scope: `{run_info['scope']}`",
        "",
        "## Summary",
        "",
        markdown_table(summary),
        "",
        "## Official Holdouts",
        "",
        markdown_table(holdouts),
        "",
        "## Official Impact",
        "",
        markdown_table(impact),
        "",
        "## Interpretation",
        "",
        "- This validates selected forward signal rows against the live strategy code, as-of BTC candles, and as-of websocket top book.",
        "- `parity_pass_rows` requires exact recomputation using scan-time TTL; `implied_ttl_pass_rows` diagnoses whether the logged model value is consistent with a cached event TTL.",
        "- Passing implied TTL parity is useful diagnosis, but promotion needs the live loop to capture exact TTL/model inputs rather than infer them afterward.",
        "- This still does not replace a full all-window counterfactual replay.",
        "- Deployment remains blocked by sample size, official/proxy mismatch, and full readiness gates.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
