#!/usr/bin/env python3
"""Faithful BTC 1-hour strategy replay from the DuckDB research datamart."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, kalshi_fee_dollars
from scripts.backtest_1hr_collected_data import PAPER_CFG, RESEARCH_CFG, build_emp_cache_fast, lognormal_p_above, vectorized_p_above


DEFAULT_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"
MIN_EDGE_CENTS = 12.0
MAX_SPREAD_CENTS = 2.0
MIN_ENTRY = 0.25
MAX_ENTRY = 0.75
MIN_YES_P = 0.65
MAX_NO_P = 0.35
BRTI_DAMPENING = 0.80
MIN_TTL_MIN = 5.0
MAX_TTL_MIN = 65.0
PAPER_MAX_SIGNALS_PER_SCAN = int(CFG.get("max_concurrent_signals", 3))


@dataclass(frozen=True)
class OpenPosition:
    row: dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest BTC 1-hour strategies from DuckDB bid/ask datamart.")
    parser.add_argument("--strategy", choices=("research", "paper"), default="research")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "research_duckdb")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--event-prefix", action="append", help="Filter by event ticker prefix, e.g. KXBTCD-26APR.")
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--progress-every-events", type=int, default=25)
    parser.add_argument("--max-events", type=int)
    parser.add_argument(
        "--allow-multiple-markets-per-event",
        action="store_true",
        help="Replay the looser old behavior. Default is one active position per hourly event.",
    )
    return parser.parse_args()


def apply_strategy_config(strategy: str) -> None:
    if strategy == "paper":
        CFG.update(PAPER_CFG)
    elif strategy == "research":
        CFG.update(RESEARCH_CFG)
    else:
        raise ValueError(f"unknown strategy {strategy}")


def ts_arg(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def load_tables(
    db_path: Path,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    event_prefixes: list[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    where = ["m.is_hourly_kxbtcd", "m.is_cumulative"]
    params: list[object] = []
    if start is not None:
        where.append("q.available_at >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("q.available_at <= ?")
        params.append(end.to_pydatetime())
    if event_prefixes:
        prefix_clauses = []
        for prefix in event_prefixes:
            prefix_clauses.append("q.event_ticker LIKE ?")
            params.append(f"{prefix.upper()}%")
        where.append(f"({' OR '.join(prefix_clauses)})")
    quote_sql = f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, q.fidelity, m.open_time, m.close_time,
               m.event_open_time, m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE {' AND '.join(where)}
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
    """
    quotes = con.execute(quote_sql, params).fetchdf()
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


def feature_frame_for_strategy(strategy: str, btc: pd.DataFrame) -> pd.DataFrame:
    df = btc.copy().sort_values("time").reset_index(drop=True)
    if strategy == "paper":
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
        af = 60 * 24 * 365
        df["rv_15m"] = df["log_ret"].rolling(15).std() * np.sqrt(af)
        df["rv_60m"] = df["log_ret"].rolling(60).std() * np.sqrt(af)
        df["rv_1d"] = df["log_ret"].rolling(1440).std() * np.sqrt(af)
        df["rkurt_60m"] = df["log_ret"].rolling(60).kurt()
    return df


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    values = btc["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def build_event_cache(btc: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train_start = event_open - pd.Timedelta(days=train_days)
    train = btc[(btc["time"] >= train_start) & (btc["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        return None
    return build_emp_cache_fast(train)


def edge_uncertainty_cents(model_p: np.ndarray, emp_cache: dict) -> np.ndarray:
    counts = np.asarray([emp_cache[h]["n"] for h in emp_cache if h in emp_cache], dtype=float)
    n_eff = max(200.0, float(np.nanmedian(counts)) * 0.30) if len(counts) else 200.0
    return 100.0 * 1.64 * np.sqrt(np.clip(model_p * (1.0 - model_p), 0.0, 0.25) / n_eff)


def signals_for_time(
    strategy: str,
    quotes: pd.DataFrame,
    btc: pd.DataFrame,
    emp_cache: dict,
    scan_ts: pd.Timestamp,
) -> pd.DataFrame:
    spot, idx = btc_at_or_before(btc, scan_ts)
    if spot is None or idx is None or idx < 1440:
        return pd.DataFrame()
    ttl_min = (quotes["close_time"].iloc[0] - scan_ts).total_seconds() / 60.0
    if strategy == "paper":
        if ttl_min <= float(CFG["min_ttl_min"]) or ttl_min >= float(CFG["max_ttl_hours"]) * 60.0:
            return pd.DataFrame()
    elif ttl_min <= MIN_TTL_MIN or ttl_min > MAX_TTL_MIN:
        return pd.DataFrame()
    rv60 = btc.iloc[idx].get("rv_60m", np.nan)
    current_vol = float(rv60) if np.isfinite(rv60) else None
    strikes = quotes["floor_strike"].to_numpy(dtype=float)
    yes_bid_close = quotes["yes_bid_close"].to_numpy(dtype=float)
    yes_ask_close = quotes["yes_ask_close"].to_numpy(dtype=float)
    yes_ask_exe = quotes["yes_ask_exe"].to_numpy(dtype=float)
    no_ask_exe = quotes["no_ask_exe"].to_numpy(dtype=float)
    spread_cents = quotes["spread_cents"].to_numpy(dtype=float)
    valid = (
        np.isfinite(strikes)
        & np.isfinite(yes_bid_close)
        & np.isfinite(yes_ask_close)
        & np.isfinite(yes_ask_exe)
        & np.isfinite(no_ask_exe)
        & np.isfinite(spread_cents)
        & (yes_bid_close >= 0.0)
        & (yes_ask_close <= 1.0)
        & (yes_ask_close >= yes_bid_close)
        & (yes_ask_exe >= 0.0)
        & (yes_ask_exe <= 1.0)
        & (no_ask_exe >= 0.0)
        & (no_ask_exe <= 1.0)
    )
    if not valid.any():
        return pd.DataFrame()
    q = quotes.loc[valid].copy()
    strikes = q["floor_strike"].to_numpy(dtype=float)
    brti_dampening = BRTI_DAMPENING if strategy == "research" else 1.0
    model_p = vectorized_p_above(strikes, spot, ttl_min, emp_cache, current_vol, brti_dampening)
    if strategy == "research":
        normal_p = lognormal_p_above(strikes, spot, ttl_min, current_vol)
        model_p = np.where(np.isfinite(normal_p), 0.70 * model_p + 0.30 * normal_p, model_p)
    finite = np.isfinite(model_p)
    if not finite.any():
        return pd.DataFrame()
    q = q.iloc[np.where(finite)[0]].copy()
    model_p = model_p[finite]
    yes_ask = q["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = q["no_ask_exe"].to_numpy(dtype=float)
    edge_yes = model_p - yes_ask
    edge_no = (1.0 - model_p) - no_ask
    choose_yes = edge_yes > edge_no
    side = np.where(choose_yes, "yes", "no")
    entry_price = np.where(choose_yes, yes_ask, no_ask)
    gross_edge = np.where(choose_yes, edge_yes, edge_no) * 100.0
    fees = np.asarray([kalshi_fee_dollars(float(price), contracts=1, liquidity="taker") for price in entry_price])
    net_edge = gross_edge - fees * 100.0
    if strategy == "research":
        threshold = MIN_EDGE_CENTS + edge_uncertainty_cents(model_p, emp_cache)
        strong = np.where(side == "yes", model_p >= MIN_YES_P, model_p <= MAX_NO_P)
        max_spread = MAX_SPREAD_CENTS
        min_entry = MIN_ENTRY
        max_entry = MAX_ENTRY
    else:
        threshold = np.full(len(model_p), float(CFG["min_edge_cents"]), dtype=float)
        strong = np.ones(len(model_p), dtype=bool)
        max_spread = float(CFG["max_spread_cents"])
        min_entry = float(CFG["min_entry_price"])
        max_entry = float(CFG["max_entry_price"])
    passed = (
        strong
        & (net_edge >= threshold)
        & (q["spread_cents"].to_numpy(dtype=float) <= max_spread)
        & (entry_price >= min_entry)
        & (entry_price <= max_entry)
    )
    if not passed.any():
        return pd.DataFrame()
    q = q.iloc[np.where(passed)[0]].copy()
    idxs = np.where(passed)[0]
    q["side"] = side[idxs]
    q["entry_price"] = entry_price[idxs]
    q["entry_fee"] = fees[idxs]
    q["model_p_yes"] = model_p[idxs]
    q["net_edge_cents"] = net_edge[idxs]
    q["edge_threshold_cents"] = threshold[idxs]
    q["entry_spot"] = spot
    q["scan_time"] = scan_ts
    return q.sort_values("net_edge_cents", ascending=False)


def settle_due(
    open_positions: dict[str, OpenPosition],
    open_events: set[str],
    now: pd.Timestamp,
    btc: pd.DataFrame,
    settled: list[dict],
) -> None:
    due = [ticker for ticker, pos in open_positions.items() if pos.row["close_time"] <= now]
    for ticker in due:
        pos = open_positions.pop(ticker)
        row = pos.row
        open_events.discard(str(row["event_ticker"]))
        settlement_spot, _ = btc_at_or_before(btc, row["close_time"])
        if settlement_spot is None:
            continue
        yes_settles = settlement_spot >= row["strike"]
        win = (yes_settles and row["side"] == "yes") or ((not yes_settles) and row["side"] == "no")
        payout = 1.0 if win else 0.0
        pnl = payout - row["entry_price"] - row["entry_fee"]
        settled.append(
            {
                **row,
                "settle_time": row["close_time"],
                "settlement_spot": settlement_spot,
                "settlement": "yes" if yes_settles else "no",
                "payout": payout,
                "pnl": pnl,
            }
        )


def run_backtest(
    strategy: str,
    quotes: pd.DataFrame,
    btc: pd.DataFrame,
    train_days: int,
    max_events: int | None,
    progress_every: int,
    allow_multiple_markets_per_event: bool,
) -> pd.DataFrame:
    settled: list[dict] = []
    open_positions: dict[str, OpenPosition] = {}
    open_events: set[str] = set()
    event_cache: dict[str, dict] = {}
    events = list(quotes.groupby("event_ticker", sort=True))
    if max_events:
        events = events[:max_events]
    for idx, (event_ticker, event_quotes) in enumerate(events, start=1):
        if idx == 1 or idx % progress_every == 0:
            print(f"event {idx}/{len(events)} {event_ticker}", flush=True)
        event_open = event_quotes["event_open_time"].iloc[0]
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, train_days)
            if emp_cache is None:
                continue
            event_cache[event_ticker] = emp_cache
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            settle_due(open_positions, open_events, scan_ts, btc, settled)
            if strategy == "research" and not allow_multiple_markets_per_event and event_ticker in open_events:
                continue
            signals = signals_for_time(strategy, scan_quotes, btc, emp_cache, scan_ts)
            if signals.empty:
                continue
            selected = signals.head(1 if strategy == "research" else PAPER_MAX_SIGNALS_PER_SCAN)
            for _, sig in selected.iterrows():
                ticker = sig["market_ticker"]
                if ticker in open_positions:
                    continue
                if strategy == "research" and not allow_multiple_markets_per_event and sig["event_ticker"] in open_events:
                    continue
                open_positions[ticker] = OpenPosition(
                    row={
                        "strategy": f"{strategy}_duckdb",
                        "event_ticker": sig["event_ticker"],
                        "market_ticker": ticker,
                        "side": sig["side"],
                        "strike": float(sig["floor_strike"]),
                        "entry_time": scan_ts,
                        "quote_ts_end": sig["ts_end"],
                        "close_time": sig["close_time"],
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
                open_events.add(str(sig["event_ticker"]))
    final_ts = max((pd.Timestamp(pos.row["close_time"]) for pos in open_positions.values()), default=pd.Timestamp.utcnow())
    settle_due(open_positions, open_events, final_ts + pd.Timedelta(days=1), btc, settled)
    if not settled:
        return pd.DataFrame()
    return pd.DataFrame(settled).sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)


def compute_stats(trades: pd.DataFrame, strategy: str) -> dict:
    if trades.empty:
        return {
            "strategy": f"{strategy}_duckdb",
            "trades": 0,
            "total_pnl": 0.0,
            "premium_deployed": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }
    ordered = trades.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
    premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
    pnl = ordered["pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    drawdown = equity - equity.cummax()
    exposure_events: list[tuple[pd.Timestamp, float]] = []
    for _, row in ordered.iterrows():
        premium_i = float(row["entry_price"]) * float(row.get("contracts", 1)) + float(row["entry_fee"])
        exposure_events.append((pd.Timestamp(row["entry_time"]), premium_i))
        exposure_events.append((pd.Timestamp(row["settle_time"]), -premium_i))
    active = 0.0
    max_active_premium = 0.0
    for _, delta in sorted(exposure_events, key=lambda item: (item[0], item[1])):
        active += delta
        max_active_premium = max(max_active_premium, active)
    return {
        "strategy": f"{strategy}_duckdb",
        "trades": int(len(ordered)),
        "total_pnl": float(pnl.sum()),
        "premium_deployed": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "avg_pnl": float(pnl.mean()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else float("inf"),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "max_active_premium": float(max_active_premium),
        "avg_edge_cents": float(ordered["net_edge_cents"].mean()),
        "yes_trades": int((ordered["side"] == "yes").sum()),
        "no_trades": int((ordered["side"] == "no").sum()),
    }


def main() -> int:
    args = parse_args()
    apply_strategy_config(args.strategy)
    start = ts_arg(args.start)
    end = ts_arg(args.end)
    quotes, btc = load_tables(args.db, start, end, args.event_prefix)
    if quotes.empty:
        raise SystemExit("No quotes found for requested range.")
    btc = feature_frame_for_strategy(args.strategy, btc)
    trades = run_backtest(
        args.strategy,
        quotes,
        btc,
        args.train_days,
        args.max_events,
        args.progress_every_events,
        args.allow_multiple_markets_per_event,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = args.output_dir / f"backtest_{args.strategy}_duckdb_trades.csv"
    summary_path = args.output_dir / f"backtest_{args.strategy}_duckdb_summary.csv"
    trades.to_csv(trades_path, index=False)
    stats = compute_stats(trades, args.strategy)
    pd.DataFrame([stats]).to_csv(summary_path, index=False)
    report = {
        "db": str(args.db),
        "strategy": args.strategy,
        "start": str(start),
        "end": str(end),
        "event_prefix": args.event_prefix,
        "quote_rows": len(quotes),
        "btc_rows": len(btc),
        "allow_multiple_markets_per_event": bool(args.allow_multiple_markets_per_event) if args.strategy == "research" else True,
        "paper_notes": "paper uses original btc_1hr_paper.py signal thresholds with official taker fees in edge and PnL" if args.strategy == "paper" else None,
        "execution_source": "observed historical Kalshi bid/ask candle close; no forward fill",
        "btc_timestamp_rule": "Coinbase bucket_start close is available at bucket_start + 1 minute",
        "trades_path": str(trades_path),
        "summary_path": str(summary_path),
        "stats": stats,
    }
    (args.output_dir / f"backtest_{args.strategy}_duckdb_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
