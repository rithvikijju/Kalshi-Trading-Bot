#!/usr/bin/env python3
"""Backtest the BTC 1-hour research signal with risk-adjusted sizing.

Sources:
  duckdb: corrected historical Kalshi bid/ask candle replay.
  csv:    originally collected Kalshi minute CSV exports, with an assumed spread.

The signal is intentionally the existing research strategy. This script only
compares sizing policies on top of that signal.
"""

from __future__ import annotations

import argparse
import re
import json
import math
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG
from scripts import backtest_1hr_collected_data as csv_bt
from scripts import backtest_research_duckdb as duck_bt
from scripts.risk_adjusted_research import (
    RiskSizingConfig,
    SizingDecision,
    choose_flat_contracts,
    choose_risk_adjusted_contracts,
    compute_sized_stats,
    premium_deployed,
    unit_payout_from_trade,
)


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "backtest_outputs" / "risk_adjusted_research"
DEFAULT_LIVE_BTC_CACHE = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_LIVE_TRADES_DB = Path.home() / ".btc_kalshi_bot" / "research_live_trades.db"
DEFAULT_CAPTURE_DBS = [
    Path.home() / ".btc_kalshi_bot" / "research_ws_dryrun_20260505_214611.duckdb",
    Path.home() / ".btc_kalshi_bot" / "research_live_capture_verify.duckdb",
    Path.home() / ".btc_kalshi_bot" / "research_live_capture_health_verify.duckdb",
]


@dataclass
class OpenPosition:
    row: dict[str, Any]
    contracts: int
    cost: float
    fee: float
    sizing: SizingDecision


def ts_arg(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def normalize_signal_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    rename = {
        "scan_time": "entry_time",
        "ticker": "market_ticker",
        "floor": "strike",
        "floor_strike": "strike",
    }
    for old, new in rename.items():
        if old in out.columns and new not in out.columns:
            out[new] = out[old]
    if "close_time" in out.columns and "settle_time" not in out.columns:
        out["settle_time"] = out["close_time"]
    for col in ("entry_time", "settle_time", "close_time"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], utc=True)
    if "event_ticker" in out.columns:
        out["event_ticker"] = out["event_ticker"].astype(str).str.upper()
    if "market_ticker" in out.columns:
        out["market_ticker"] = out["market_ticker"].astype(str).str.upper()
    if "side" in out.columns:
        out["side"] = out["side"].astype(str).str.lower()
    return out.sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)


def strike_from_market_ticker(ticker: str) -> float | None:
    match = re.search(r"-T(?P<strike>\d+(?:\.\d+)?)", str(ticker).upper())
    if not match:
        return None
    # Kalshi cumulative BTC tickers encode "$81,400 or above" as T81399.99.
    return float(match.group("strike")) + 0.01


def load_live_btc_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"BTC live cache not found: {path}")
    btc = pd.read_parquet(path)
    btc["time"] = pd.to_datetime(btc["time"], utc=True)
    return btc.sort_values("time").reset_index(drop=True)


def btc_settlement_unit_payout(row: dict[str, Any], btc: pd.DataFrame | None) -> tuple[float, float | None, str | None]:
    if btc is not None and "strike" in row and pd.notna(row.get("strike")):
        close_time = pd.Timestamp(row.get("close_time") or row.get("settle_time"))
        settlement_spot, _ = duck_bt.btc_at_or_before(btc, close_time)
        if settlement_spot is not None:
            yes_settles = float(settlement_spot) >= float(row["strike"])
            side = str(row["side"]).lower()
            won = (side == "yes" and yes_settles) or (side == "no" and not yes_settles)
            return (1.0 if won else 0.0), float(settlement_spot), "yes" if yes_settles else "no"
    payout = unit_payout_from_trade(row)
    settlement = row.get("settlement")
    settlement_spot = row.get("settlement_spot")
    return payout, (float(settlement_spot) if pd.notna(settlement_spot) else None), str(settlement) if pd.notna(settlement) else None


def settle_due(
    open_positions: dict[str, OpenPosition],
    open_events: set[str],
    now: pd.Timestamp,
    btc: pd.DataFrame | None,
    settled: list[dict[str, Any]],
) -> float:
    cash_in = 0.0
    due = [
        ticker
        for ticker, pos in open_positions.items()
        if pd.Timestamp(pos.row.get("close_time") or pos.row.get("settle_time")) <= now
    ]
    for ticker in due:
        pos = open_positions.pop(ticker)
        row = pos.row
        open_events.discard(str(row["event_ticker"]).upper())
        unit_payout, settlement_spot, settlement = btc_settlement_unit_payout(row, btc)
        payout = unit_payout * pos.contracts
        pnl = payout - pos.cost
        cash_in += payout
        settled.append(
            {
                **row,
                "contracts": pos.contracts,
                "entry_fee": pos.fee,
                "premium_deployed": pos.cost,
                "payout": payout,
                "pnl": pnl,
                "settlement_spot": settlement_spot,
                "settlement": settlement,
                "sizing_reason": pos.sizing.reason,
                "sizing_budget": pos.sizing.budget,
                "sizing_risk_budget": pos.sizing.risk_budget,
                "sizing_price_band_cap": pos.sizing.price_band_cap,
                "sizing_side_probability": pos.sizing.side_probability,
                "sizing_conservative_probability": pos.sizing.conservative_probability,
                "sizing_full_kelly_fraction": pos.sizing.full_kelly_fraction,
                "sizing_applied_kelly_fraction": pos.sizing.applied_kelly_fraction,
            }
        )
    return cash_in


def active_exposure(open_positions: dict[str, OpenPosition]) -> float:
    return sum(pos.cost for pos in open_positions.values())


def sizing_for_policy(
    policy: str,
    signal: dict[str, Any],
    cash: float,
    active: float,
    config: RiskSizingConfig,
) -> SizingDecision:
    bankroll = max(0.0, cash + active)
    entry_price = float(signal["entry_price"])
    available_qty = signal.get("available_qty")
    if policy == "fixed_1":
        return choose_flat_contracts(
            entry_price=entry_price,
            bankroll=bankroll,
            available_cash=cash,
            active_exposure=active,
            max_contracts=1,
            max_per_market_fraction=config.max_per_market_fraction,
            max_total_exposure_fraction=config.max_total_exposure_fraction,
            available_qty=available_qty,
        )
    if policy == "flat_3":
        return choose_flat_contracts(
            entry_price=entry_price,
            bankroll=bankroll,
            available_cash=cash,
            active_exposure=active,
            max_contracts=config.max_contracts,
            max_per_market_fraction=config.max_per_market_fraction,
            max_total_exposure_fraction=config.max_total_exposure_fraction,
            available_qty=available_qty,
        )
    if policy == "risk_adjusted":
        return choose_risk_adjusted_contracts(
            entry_price=entry_price,
            model_p_yes=float(signal.get("model_p_yes", 0.5)),
            side=str(signal.get("side", "")),
            bankroll=bankroll,
            available_cash=cash,
            active_exposure=active,
            config=config,
            available_qty=available_qty,
        )
    raise ValueError(f"unknown sizing policy {policy}")


def simulate_signal_stream(
    signals: pd.DataFrame,
    *,
    policy: str,
    config: RiskSizingConfig,
    btc: pd.DataFrame | None,
    one_position_per_event: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    stream = normalize_signal_frame(signals)
    cash = float(config.starting_bankroll)
    open_positions: dict[str, OpenPosition] = {}
    open_events: set[str] = set()
    settled: list[dict[str, Any]] = []
    skipped = {"budget": 0, "event_lock": 0, "duplicate_ticker": 0}

    for _, row_obj in stream.iterrows():
        row = row_obj.to_dict()
        now = pd.Timestamp(row["entry_time"])
        cash += settle_due(open_positions, open_events, now, btc, settled)

        ticker = str(row["market_ticker"]).upper()
        event_ticker = str(row["event_ticker"]).upper()
        if ticker in open_positions:
            skipped["duplicate_ticker"] += 1
            continue
        if one_position_per_event and event_ticker in open_events:
            skipped["event_lock"] += 1
            continue

        active = active_exposure(open_positions)
        decision = sizing_for_policy(policy, row, cash, active, config)
        if decision.contracts <= 0:
            skipped["budget"] += 1
            continue

        cash -= decision.cost
        open_positions[ticker] = OpenPosition(
            row={**row, "strategy": policy},
            contracts=decision.contracts,
            cost=decision.cost,
            fee=decision.fee,
            sizing=decision,
        )
        open_events.add(event_ticker)

    if open_positions:
        final_ts = max(pd.Timestamp(pos.row.get("close_time") or pos.row.get("settle_time")) for pos in open_positions.values())
        cash += settle_due(open_positions, open_events, final_ts + pd.Timedelta(days=1), btc, settled)

    out = normalize_signal_frame(pd.DataFrame(settled))
    stats = compute_sized_stats(out, config.starting_bankroll, skipped)
    stats["policy"] = policy
    stats["skipped_duplicate_ticker"] = int(skipped["duplicate_ticker"])
    return out, stats


def collect_duckdb_research_signals(
    *,
    db: Path,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    event_close_start: pd.Timestamp | None,
    event_close_end: pd.Timestamp | None,
    train_days: int,
    max_events: int | None,
    progress_every: int,
    event_prefixes: list[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    duck_bt.apply_strategy_config("research")
    quotes, btc = load_duckdb_tables(db, start, end, event_close_start, event_close_end, event_prefixes, max_events)
    if quotes.empty:
        raise RuntimeError("No DuckDB quotes found for requested range.")
    btc = duck_bt.feature_frame_for_strategy("research", btc)

    rows: list[dict[str, Any]] = []
    events = list(quotes.groupby("event_ticker", sort=True))
    if max_events:
        events = events[:max_events]
    event_cache: dict[str, dict] = {}
    for idx, (event_ticker, event_quotes) in enumerate(events, start=1):
        if idx == 1 or idx % progress_every == 0:
            print(f"  duckdb signals: event {idx}/{len(events)} {event_ticker}", flush=True)
        event_open = event_quotes["event_open_time"].iloc[0]
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = duck_bt.build_event_cache(btc, event_open, train_days)
            if emp_cache is None:
                continue
            event_cache[event_ticker] = emp_cache

        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            signals = duck_bt.signals_for_time("research", scan_quotes, btc, emp_cache, pd.Timestamp(scan_ts))
            if signals.empty:
                continue
            sig = signals.iloc[0]
            rows.append(
                {
                    "strategy": "research_signal",
                    "event_ticker": str(sig["event_ticker"]),
                    "market_ticker": str(sig["market_ticker"]),
                    "side": str(sig["side"]),
                    "strike": float(sig["floor_strike"]),
                    "entry_time": pd.Timestamp(scan_ts),
                    "quote_ts_end": pd.Timestamp(sig["ts_end"]),
                    "close_time": pd.Timestamp(sig["close_time"]),
                    "settle_time": pd.Timestamp(sig["close_time"]),
                    "entry_price": float(sig["entry_price"]),
                    "entry_fee": float(sig["entry_fee"]),
                    "contracts": 1,
                    "entry_spot": float(sig["entry_spot"]),
                    "model_p_yes": float(sig["model_p_yes"]),
                    "net_edge_cents": float(sig["net_edge_cents"]),
                    "edge_threshold_cents": float(sig["edge_threshold_cents"]),
                    "spread_cents": float(sig["spread_cents"]),
                    "fidelity": sig["fidelity"],
                }
            )
    stream = normalize_signal_frame(pd.DataFrame(rows))
    report = {
        "db": str(db),
        "quote_rows": int(len(quotes)),
        "btc_rows": int(len(btc)),
        "events": int(len(events)),
        "signal_rows": int(len(stream)),
        "source": "corrected historical Kalshi bid/ask candles; q.available_at is the causal quote time",
    }
    return stream, btc, report


def duckdb_columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return set(con.execute(f"DESCRIBE {table}").fetchdf()["column_name"].astype(str))


def load_duckdb_tables(
    db_path: Path,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    event_close_start: pd.Timestamp | None,
    event_close_end: pd.Timestamp | None,
    event_prefixes: list[str] | None,
    max_events: int | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    quote_cols = duckdb_columns(con, "kalshi_quotes")
    market_cols = duckdb_columns(con, "kalshi_markets")
    fidelity_expr = "q.fidelity" if "fidelity" in quote_cols else "'unknown' AS fidelity"
    open_time_expr = "m.open_time" if "open_time" in market_cols else "m.event_open_time AS open_time"
    status_filter = "m.is_hourly_kxbtcd AND m.is_cumulative"

    event_where = [status_filter]
    event_params: list[Any] = []
    if event_close_start is not None:
        event_where.append("m.close_time >= ?")
        event_params.append(event_close_start.to_pydatetime())
    if event_close_end is not None:
        event_where.append("m.close_time < ?")
        event_params.append(event_close_end.to_pydatetime())
    if event_prefixes:
        prefix_clauses = []
        for prefix in event_prefixes:
            prefix_clauses.append("m.event_ticker LIKE ?")
            event_params.append(f"{prefix.upper()}%")
        event_where.append(f"({' OR '.join(prefix_clauses)})")

    selected_events: list[str] | None = None
    if max_events is not None:
        selected_events = [
            row[0]
            for row in con.execute(
                f"""
                SELECT m.event_ticker
                FROM kalshi_markets m
                WHERE {' AND '.join(event_where)}
                GROUP BY m.event_ticker
                ORDER BY m.event_ticker
                LIMIT ?
                """,
                [*event_params, int(max_events)],
            ).fetchall()
        ]
        if not selected_events:
            con.close()
            return pd.DataFrame(), pd.DataFrame()

    where = ["m.is_hourly_kxbtcd", "m.is_cumulative"]
    params: list[Any] = []
    if start is not None:
        where.append("q.available_at >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("q.available_at <= ?")
        params.append(end.to_pydatetime())
    if event_close_start is not None:
        where.append("m.close_time >= ?")
        params.append(event_close_start.to_pydatetime())
    if event_close_end is not None:
        where.append("m.close_time < ?")
        params.append(event_close_end.to_pydatetime())
    if event_prefixes:
        prefix_clauses = []
        for prefix in event_prefixes:
            prefix_clauses.append("q.event_ticker LIKE ?")
            params.append(f"{prefix.upper()}%")
        where.append(f"({' OR '.join(prefix_clauses)})")
    if selected_events is not None:
        placeholders = ",".join("?" for _ in selected_events)
        where.append(f"q.event_ticker IN ({placeholders})")
        params.extend(selected_events)

    quotes = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, {fidelity_expr}, {open_time_expr}, m.close_time,
               m.event_open_time, m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE {' AND '.join(where)}
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
        """,
        params,
    ).fetchdf()
    if quotes.empty:
        con.close()
        return quotes, pd.DataFrame()

    btc_start = quotes["event_open_time"].min() - pd.Timedelta(days=10)
    btc_end = quotes["close_time"].max() + pd.Timedelta(minutes=1)
    btc = con.execute(
        """
        SELECT available_at AS time, open, high, low, close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        WHERE available_at >= ? AND available_at <= ?
        ORDER BY available_at
        """,
        [btc_start.to_pydatetime(), btc_end.to_pydatetime()],
    ).fetchdf()
    con.close()
    for col in ("available_at", "ts_end", "open_time", "close_time", "event_open_time"):
        quotes[col] = pd.to_datetime(quotes[col], utc=True)
    btc["time"] = pd.to_datetime(btc["time"], utc=True)
    return quotes, btc


def collect_csv_research_trades(
    *,
    data_dir: Path,
    pattern: str,
    btc_cache: Path,
    train_days: int,
    assumed_spread_cents: float,
    fee_liquidity: str,
    max_events: int | None,
    progress_every: int,
    event_close_start: pd.Timestamp | None,
    event_close_end: pd.Timestamp | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    events, verification = csv_bt.verify_kalshi_data(data_dir, pattern)
    empty_price_errors = [
        err for err in verification["errors"]
        if "no populated price cells" in str(err)
    ]
    other_errors = [
        err for err in verification["errors"]
        if "no populated price cells" not in str(err)
    ]
    if other_errors:
        raise RuntimeError(f"CSV verification failed with {len(other_errors)} structural errors: {other_errors[:5]}")
    if empty_price_errors:
        print(f"  csv verification: skipping {len(empty_price_errors)} empty-price event file(s)", flush=True)
        events = [ev for ev in events if ev.populated_cells > 0]
        verification = {
            **verification,
            "ignored_empty_price_errors": empty_price_errors,
            "errors": [],
        }
    if event_close_start is not None:
        events = [ev for ev in events if ev.close_ts >= event_close_start]
    if event_close_end is not None:
        events = [ev for ev in events if ev.close_ts < event_close_end]
    if max_events:
        events = events[:max_events]
    if not events:
        raise RuntimeError("No collected CSV events found for requested range.")

    first_ts = min(ev.first_ts for ev in events)
    last_ts = max(ev.close_ts for ev in events)
    btc_start = first_ts - pd.Timedelta(days=train_days + 1)
    btc_end = last_ts + pd.Timedelta(hours=1)
    print(f"  csv BTC needed: {btc_start} -> {btc_end}", flush=True)
    btc_raw = csv_bt.load_or_fetch_btc_minutes(
        btc_cache,
        btc_start,
        btc_end,
        refresh=False,
        fill_internal_gaps=False,
    )
    trades = csv_bt.run_strategy_backtest(
        strategy_name="research",
        events=events,
        btc_raw=btc_raw,
        train_days=train_days,
        assumed_spread_cents=assumed_spread_cents,
        fee_liquidity=fee_liquidity,
        max_events=None,
        progress_every=max(1, progress_every),
    )
    stream = normalize_signal_frame(trades)
    report = {
        "data_dir": str(data_dir),
        "pattern": pattern,
        "events": int(len(events)),
        "signal_rows": int(len(stream)),
        "assumed_spread_cents": float(assumed_spread_cents),
        "source": "collected Kalshi minute CSV exports; prices are not full bid/ask orderbook candles",
        "verification": verification,
    }
    return stream, report


def collect_capture_scan_signals(
    *,
    capture_dbs: list[Path],
    btc_cache: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for path in capture_dbs:
        path = Path(path).expanduser()
        if not path.exists():
            reports.append({"path": str(path), "status": "missing"})
            continue
        try:
            con = duckdb.connect(str(path), read_only=True)
            tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
            if "signal_scan" not in tables:
                reports.append({"path": str(path), "status": "missing_signal_scan"})
                con.close()
                continue
            df = con.execute(
                """
                SELECT received_at_utc, event_ticker, selected_market, selected_side,
                       entry_price, net_edge_cents, model_p_yes, btc_spot, action, detail
                FROM signal_scan
                WHERE selected_market IS NOT NULL
                  AND entry_price IS NOT NULL
                  AND selected_side IS NOT NULL
                ORDER BY received_at_utc, selected_market
                """
            ).fetchdf()
            scan_rows = con.execute("SELECT count(*) FROM signal_scan").fetchone()[0]
            top_rows = con.execute("SELECT count(*) FROM ws_orderbook_top").fetchone()[0] if "ws_orderbook_top" in tables else 0
            minmax = con.execute("SELECT min(received_at_utc), max(received_at_utc) FROM signal_scan").fetchone()
            con.close()
            reports.append(
                {
                    "path": str(path),
                    "status": "ok",
                    "signal_scan_rows": int(scan_rows),
                    "selected_rows": int(len(df)),
                    "ws_orderbook_top_rows": int(top_rows),
                    "first_scan": minmax[0],
                    "last_scan": minmax[1],
                }
            )
        except Exception as exc:
            reports.append({"path": str(path), "status": "read_failed", "error": str(exc)})
            continue

        for _, scan in df.iterrows():
            market_ticker = str(scan["selected_market"]).upper()
            event_ticker = str(scan["event_ticker"]).upper()
            strike = strike_from_market_ticker(market_ticker)
            close_time = csv_bt.parse_event_close_from_ticker(event_ticker)
            if strike is None or close_time is None:
                continue
            rows.append(
                {
                    "strategy": "capture_scan_research",
                    "event_ticker": event_ticker,
                    "market_ticker": market_ticker,
                    "side": str(scan["selected_side"]).lower(),
                    "strike": float(strike),
                    "entry_time": pd.Timestamp(scan["received_at_utc"]),
                    "close_time": close_time,
                    "settle_time": close_time,
                    "entry_price": float(scan["entry_price"]),
                    "contracts": 1,
                    "entry_spot": float(scan["btc_spot"]) if pd.notna(scan["btc_spot"]) else float("nan"),
                    "model_p_yes": float(scan["model_p_yes"]) if pd.notna(scan["model_p_yes"]) else 0.5,
                    "net_edge_cents": float(scan["net_edge_cents"]) if pd.notna(scan["net_edge_cents"]) else float("nan"),
                    "spread_cents": float("nan"),
                    "capture_action": scan["action"],
                    "capture_detail": scan["detail"],
                }
            )
    btc = load_live_btc_cache(btc_cache)
    report = {
        "source": "self-captured websocket signal_scan rows from DuckDB capture files",
        "btc_cache": str(btc_cache),
        "capture_dbs": reports,
        "signal_rows": int(len(rows)),
        "note": "This only replays scans that already selected a signal; captures with zero selected rows are a data-presence check, not a strategy result.",
    }
    return normalize_signal_frame(pd.DataFrame(rows)), btc, report


def collect_live_trade_signals(
    *,
    live_trades_db: Path,
    btc_cache: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    live_trades_db = Path(live_trades_db).expanduser()
    if not live_trades_db.exists():
        raise FileNotFoundError(f"live trades DB not found: {live_trades_db}")
    conn = sqlite3.connect(live_trades_db)
    df = pd.read_sql_query("SELECT * FROM research_live_trades ORDER BY created_at, id", conn)
    conn.close()
    if df.empty:
        btc = load_live_btc_cache(btc_cache)
        return pd.DataFrame(), btc, {"source": "actual live research trade ledger", "rows": 0}

    status = df["status"].astype(str).str.lower()
    mode = df["mode"].astype(str).str.lower() if "mode" in df.columns else pd.Series([""] * len(df))
    fill_count = pd.to_numeric(df.get("fill_count", 0), errors="coerce").fillna(0.0)
    df = df[((mode == "live") | (mode == "")) & (status.isin(["filled", "partial_filled"]) | (fill_count > 0))].copy()
    rows: list[dict[str, Any]] = []
    for _, trade in df.iterrows():
        market_ticker = str(trade["market_ticker"]).upper()
        event_ticker = str(trade["event_ticker"]).upper()
        strike = strike_from_market_ticker(market_ticker)
        close_time = pd.Timestamp(trade["close_time"]) if pd.notna(trade.get("close_time")) else csv_bt.parse_event_close_from_ticker(event_ticker)
        if strike is None or close_time is None:
            continue
        entry = trade.get("actual_entry_price")
        if pd.isna(entry):
            entry = trade.get("entry_price")
        rows.append(
            {
                "strategy": "actual_live_research_signal",
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "side": str(trade["side"]).lower(),
                "strike": float(strike),
                "entry_time": pd.Timestamp(trade["created_at"]),
                "close_time": pd.Timestamp(close_time),
                "settle_time": pd.Timestamp(close_time),
                "entry_price": float(entry),
                "contracts": 1,
                "entry_spot": float(trade["btc_spot"]) if pd.notna(trade.get("btc_spot")) else float("nan"),
                "model_p_yes": float(trade["model_p_yes"]) if pd.notna(trade.get("model_p_yes")) else 0.5,
                "net_edge_cents": float(trade["net_edge_cents"]) if pd.notna(trade.get("net_edge_cents")) else float("nan"),
                "spread_cents": float(trade["spread_cents"]) if pd.notna(trade.get("spread_cents")) else float("nan"),
                "actual_live_status": trade["status"],
                "client_order_id": trade.get("client_order_id"),
            }
        )
    btc = load_live_btc_cache(btc_cache)
    report = {
        "source": "actual live research filled trade ledger; useful for sizing diagnostics, not a full opportunity replay",
        "live_trades_db": str(live_trades_db),
        "btc_cache": str(btc_cache),
        "ledger_rows": int(len(df)),
        "signal_rows": int(len(rows)),
    }
    return normalize_signal_frame(pd.DataFrame(rows)), btc, report


def run_source(
    name: str,
    stream: pd.DataFrame,
    btc: pd.DataFrame | None,
    policies: list[str],
    config: RiskSizingConfig,
    out_dir: Path,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    source_dir = out_dir / name
    source_dir.mkdir(parents=True, exist_ok=True)
    stream_path = source_dir / "research_signal_stream.csv"
    stream.to_csv(stream_path, index=False)
    summaries: list[dict[str, Any]] = []
    for policy in policies:
        t0 = time.time()
        trades, stats = simulate_signal_stream(stream, policy=policy, config=config, btc=btc)
        trades_path = source_dir / f"{policy}_trades.csv"
        trades.to_csv(trades_path, index=False)
        stats = {
            "source": name,
            **stats,
            "trades_path": str(trades_path),
            "runtime_sec": time.time() - t0,
        }
        summaries.append(stats)
        print(
            f"  {name}/{policy}: trades={stats['trades']} contracts={stats['contracts']} "
            f"pnl={stats['total_pnl']:+.4f} return={stats['return_on_start']:.2%} "
            f"premium_ret={stats['return_on_premium']:.2%}",
            flush=True,
        )
    report_path = source_dir / "source_report.json"
    report_path.write_text(json.dumps({**report, "stream_path": str(stream_path)}, indent=2, default=str), encoding="utf-8")
    return summaries


def print_summary_table(summary: pd.DataFrame) -> None:
    cols = [
        "source",
        "policy",
        "trades",
        "contracts",
        "total_pnl",
        "return_on_start",
        "premium_deployed",
        "return_on_premium",
        "win_rate",
        "max_drawdown",
        "avg_contracts",
        "avg_entry_price",
        "skipped_budget",
        "skipped_event_lock",
    ]
    show = summary[cols].copy()
    for col in ("total_pnl", "premium_deployed", "max_drawdown", "avg_contracts", "avg_entry_price"):
        show[col] = show[col].map(lambda v: f"{float(v):.4f}")
    for col in ("return_on_start", "return_on_premium", "win_rate"):
        show[col] = show[col].map(lambda v: f"{float(v):.2%}")
    print("\nRISK-ADJUSTED BACKTEST SUMMARY")
    print(show.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", choices=("duckdb", "csv", "capture", "live_trades"), default=["duckdb", "csv"])
    parser.add_argument("--policies", nargs="+", choices=("fixed_1", "flat_3", "risk_adjusted"), default=["fixed_1", "flat_3", "risk_adjusted"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--starting-bankroll", type=float, default=20.0)
    parser.add_argument("--max-contracts", type=int, default=3)
    parser.add_argument("--max-per-market-fraction", type=float, default=float(CFG.get("max_per_market", 0.20)))
    parser.add_argument("--max-total-exposure-fraction", type=float, default=float(CFG.get("max_total_risk", 0.50)))
    parser.add_argument("--kelly-fraction", type=float, default=0.25)
    parser.add_argument("--edge-confidence", type=float, default=0.50)
    parser.add_argument("--medium-entry-cap", type=float, default=0.55)
    parser.add_argument("--high-entry-cap", type=float, default=0.65)
    parser.add_argument("--no-side-contract-cap", type=int, default=3)
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--progress-every-events", type=int, default=25)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--event-close-start")
    parser.add_argument("--event-close-end")
    parser.add_argument("--event-prefix", action="append")
    parser.add_argument("--db", type=Path, default=duck_bt.DEFAULT_DB)
    parser.add_argument("--csv-data-dir", type=Path, default=csv_bt.DATA_DIR)
    parser.add_argument("--csv-pattern", default="kalshi-price-history-kxbtcd-*.csv")
    parser.add_argument("--csv-btc-cache", type=Path, default=csv_bt.BTC_CACHE_PATH)
    parser.add_argument("--csv-assumed-spread-cents", type=float, default=2.0)
    parser.add_argument("--fee-liquidity", choices=("taker", "maker"), default="taker")
    parser.add_argument("--live-btc-cache", type=Path, default=DEFAULT_LIVE_BTC_CACHE)
    parser.add_argument("--live-trades-db", type=Path, default=DEFAULT_LIVE_TRADES_DB)
    parser.add_argument("--capture-db", action="append", type=Path, help="Readable self-captured websocket DuckDB. Can be passed multiple times.")
    return parser.parse_args()


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = RiskSizingConfig(
        starting_bankroll=args.starting_bankroll,
        max_contracts=args.max_contracts,
        max_per_market_fraction=args.max_per_market_fraction,
        max_total_exposure_fraction=args.max_total_exposure_fraction,
        kelly_fraction=args.kelly_fraction,
        edge_confidence=args.edge_confidence,
        medium_entry_cap=args.medium_entry_cap,
        high_entry_cap=args.high_entry_cap,
        no_side_contract_cap=args.no_side_contract_cap,
    )

    start = ts_arg(args.start)
    end = ts_arg(args.end)
    event_close_start = ts_arg(args.event_close_start)
    event_close_end = ts_arg(args.event_close_end)

    all_summaries: list[dict[str, Any]] = []
    run_report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "sizing_config": asdict(config),
        "args": {k: jsonable(v) for k, v in vars(args).items()},
    }

    if "duckdb" in args.sources:
        print("\nCOLLECTING DUCKDB RESEARCH SIGNAL STREAM", flush=True)
        stream, btc, report = collect_duckdb_research_signals(
            db=args.db,
            start=start,
            end=end,
            event_close_start=event_close_start,
            event_close_end=event_close_end,
            train_days=args.train_days,
            max_events=args.max_events,
            progress_every=max(1, args.progress_every_events),
            event_prefixes=args.event_prefix,
        )
        print(f"  duckdb stream rows: {len(stream)}", flush=True)
        all_summaries.extend(run_source("duckdb", stream, btc, args.policies, config, args.output_dir, report))

    if "csv" in args.sources:
        print("\nCOLLECTING CSV RESEARCH SIGNAL STREAM", flush=True)
        stream, report = collect_csv_research_trades(
            data_dir=args.csv_data_dir,
            pattern=args.csv_pattern,
            btc_cache=args.csv_btc_cache,
            train_days=args.train_days,
            assumed_spread_cents=args.csv_assumed_spread_cents,
            fee_liquidity=args.fee_liquidity,
            max_events=args.max_events,
            progress_every=max(1, args.progress_every_events),
            event_close_start=event_close_start,
            event_close_end=event_close_end,
        )
        print(f"  csv stream rows: {len(stream)}", flush=True)
        all_summaries.extend(run_source("csv", stream, None, args.policies, config, args.output_dir, report))

    if "capture" in args.sources:
        print("\nCOLLECTING SELF-CAPTURED WEBSOCKET SIGNAL STREAM", flush=True)
        capture_dbs = args.capture_db or DEFAULT_CAPTURE_DBS
        stream, btc, report = collect_capture_scan_signals(capture_dbs=capture_dbs, btc_cache=args.live_btc_cache)
        print(f"  capture stream rows: {len(stream)}", flush=True)
        all_summaries.extend(run_source("capture", stream, btc, args.policies, config, args.output_dir, report))

    if "live_trades" in args.sources:
        print("\nCOLLECTING ACTUAL LIVE FILL STREAM", flush=True)
        stream, btc, report = collect_live_trade_signals(live_trades_db=args.live_trades_db, btc_cache=args.live_btc_cache)
        print(f"  live trade stream rows: {len(stream)}", flush=True)
        all_summaries.extend(run_source("live_trades", stream, btc, args.policies, config, args.output_dir, report))

    summary = pd.DataFrame(all_summaries)
    summary_path = args.output_dir / "risk_adjusted_research_summary.csv"
    summary.to_csv(summary_path, index=False)
    run_report["summary_path"] = str(summary_path)
    run_report["completed_at"] = datetime.now(timezone.utc).isoformat()
    (args.output_dir / "risk_adjusted_research_report.json").write_text(json.dumps(run_report, indent=2, default=str), encoding="utf-8")
    print_summary_table(summary)
    print(f"\nWrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
