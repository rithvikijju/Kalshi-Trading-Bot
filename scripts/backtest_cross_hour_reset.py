#!/usr/bin/env python3
"""Backtest a simple cross-hour carryover hypothesis for KXBTCD.

Hypothesis: immediately after one hourly event settles, the next hourly event
may underreact to the previous hour's same-strike terminal state. The strategy
only scans the first few minutes of the next event, requires a same-strike quote
from the previous event boundary, and then still demands a positive model-vs-
ask edge before buying.

This is intentionally simple and chronology-splittable; it is not connected to
the live executor.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, kalshi_fee_dollars
from scripts.backtest_1hr_collected_data import RESEARCH_CFG, build_emp_cache_fast, lognormal_p_above, vectorized_p_above
from scripts.backtest_research_duckdb import (
    BRTI_DAMPENING,
    DEFAULT_DB,
    edge_uncertainty_cents,
    feature_frame_for_strategy,
    ts_arg,
)


TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")


@dataclass(frozen=True)
class Position:
    row: dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest simple KXBTCD cross-hour reset signals.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "cross_hour_reset")
    parser.add_argument("--event-close-start")
    parser.add_argument("--event-close-end")
    parser.add_argument("--early-minutes", type=int, default=5)
    parser.add_argument("--min-edge-cents", type=float, default=8.0)
    parser.add_argument("--max-spread-cents", type=float, default=2.0)
    parser.add_argument("--min-entry", type=float, default=0.35)
    parser.add_argument("--max-entry", type=float, default=0.80)
    parser.add_argument("--prev-extreme", type=float, default=0.90)
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--progress-every-events", type=int, default=200)
    return parser.parse_args()


def split_name(close_time: pd.Timestamp) -> str:
    close_time = close_time.tz_convert("UTC")
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def load_data(db_path: Path, event_close_start: pd.Timestamp | None, event_close_end: pd.Timestamp | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    where = ["m.is_hourly_kxbtcd", "m.is_cumulative", "q.ts_end <= m.close_time"]
    params: list[object] = []
    if event_close_start is not None:
        where.append("m.close_time >= ?")
        params.append(event_close_start.to_pydatetime())
    if event_close_end is not None:
        where.append("m.close_time < ?")
        params.append(event_close_end.to_pydatetime())
    quotes = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, m.open_time, m.close_time, m.event_open_time, m.floor_strike
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
    return quotes, feature_frame_for_strategy("research", btc)


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    values = btc["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def event_cache(btc: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train = btc[(btc["time"] >= event_open - pd.Timedelta(days=train_days)) & (btc["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        return None
    return build_emp_cache_fast(train)


def signal_rows(
    event_quotes: pd.DataFrame,
    prev_boundary: pd.DataFrame,
    btc: pd.DataFrame,
    cache: dict,
    scan_ts: pd.Timestamp,
    args: argparse.Namespace,
) -> pd.DataFrame:
    spot, idx = btc_at_or_before(btc, scan_ts)
    if spot is None or idx is None or idx < 1440:
        return pd.DataFrame()
    ttl_min = (event_quotes["close_time"].iloc[0] - scan_ts).total_seconds() / 60.0
    if ttl_min <= 5.0 or ttl_min > 65.0:
        return pd.DataFrame()
    minute_in_event = (scan_ts - event_quotes["event_open_time"].iloc[0]).total_seconds() / 60.0
    if minute_in_event < 1.0 or minute_in_event > float(args.early_minutes):
        return pd.DataFrame()

    q = event_quotes.merge(
        prev_boundary[["floor_strike", "prev_mid"]],
        on="floor_strike",
        how="inner",
    )
    if q.empty:
        return q
    rv60 = btc.iloc[idx].get("rv_60m", np.nan)
    current_vol = float(rv60) if np.isfinite(rv60) else None
    strikes = q["floor_strike"].to_numpy(dtype=float)
    model_p = vectorized_p_above(strikes, spot, ttl_min, cache, current_vol, BRTI_DAMPENING)
    normal_p = lognormal_p_above(strikes, spot, ttl_min, current_vol)
    model_p = np.where(np.isfinite(normal_p), 0.70 * model_p + 0.30 * normal_p, model_p)
    finite = np.isfinite(model_p)
    if not finite.any():
        return pd.DataFrame()
    q = q.iloc[np.where(finite)[0]].copy()
    model_p = model_p[finite]

    prev_mid = q["prev_mid"].to_numpy(dtype=float)
    force_yes = prev_mid >= float(args.prev_extreme)
    force_no = prev_mid <= 1.0 - float(args.prev_extreme)
    direction_ok = force_yes | force_no
    side = np.where(force_yes, "yes", "no")
    yes_ask = q["yes_ask_exe"].to_numpy(dtype=float)
    no_ask = q["no_ask_exe"].to_numpy(dtype=float)
    entry_price = np.where(force_yes, yes_ask, no_ask)
    side_p = np.where(force_yes, model_p, 1.0 - model_p)
    fees = np.asarray([kalshi_fee_dollars(float(price), contracts=1, liquidity="taker") for price in entry_price])
    net_edge = (side_p - entry_price) * 100.0 - fees * 100.0
    threshold = float(args.min_edge_cents) + edge_uncertainty_cents(model_p, cache)
    passed = (
        direction_ok
        & np.isfinite(entry_price)
        & (q["spread_cents"].to_numpy(dtype=float) <= float(args.max_spread_cents))
        & (entry_price >= float(args.min_entry))
        & (entry_price <= float(args.max_entry))
        & (net_edge >= threshold)
    )
    if not passed.any():
        return pd.DataFrame()
    out = q.iloc[np.where(passed)[0]].copy()
    idxs = np.where(passed)[0]
    out["side"] = side[idxs]
    out["entry_price"] = entry_price[idxs]
    out["entry_fee"] = fees[idxs]
    out["model_p_yes"] = model_p[idxs]
    out["net_edge_cents"] = net_edge[idxs]
    out["edge_threshold_cents"] = threshold[idxs]
    out["entry_spot"] = spot
    out["minute_in_event"] = minute_in_event
    out["prev_mid"] = prev_mid[idxs]
    return out.sort_values("net_edge_cents", ascending=False)


def settle_due(open_positions: dict[str, Position], now: pd.Timestamp, btc: pd.DataFrame, settled: list[dict]) -> None:
    due = [ticker for ticker, pos in open_positions.items() if pos.row["close_time"] <= now]
    for ticker in due:
        pos = open_positions.pop(ticker)
        row = pos.row
        settlement_spot, _ = btc_at_or_before(btc, row["close_time"])
        if settlement_spot is None:
            continue
        yes_settles = settlement_spot >= row["strike"]
        win = (yes_settles and row["side"] == "yes") or ((not yes_settles) and row["side"] == "no")
        payout = 1.0 if win else 0.0
        settled.append(
            {
                **row,
                "settle_time": row["close_time"],
                "settlement_spot": settlement_spot,
                "settlement": "yes" if yes_settles else "no",
                "payout": payout,
                "pnl": payout - row["entry_price"] - row["entry_fee"],
            }
        )


def run_backtest(quotes: pd.DataFrame, btc: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    CFG.update(RESEARCH_CFG)
    events = {event: group.sort_values("available_at") for event, group in quotes.groupby("event_ticker", sort=True)}
    meta = (
        quotes.groupby("event_ticker", sort=True)
        .agg(open_time=("event_open_time", "min"), close_time=("close_time", "max"))
        .reset_index()
        .sort_values("open_time")
    )
    prev_by_event: dict[str, str] = {}
    prev_close_by_event: dict[str, pd.Timestamp] = {}
    prev_row = None
    for row in meta.itertuples(index=False):
        if prev_row is not None and pd.Timestamp(prev_row.close_time) == pd.Timestamp(row.open_time):
            prev_by_event[row.event_ticker] = prev_row.event_ticker
            prev_close_by_event[row.event_ticker] = pd.Timestamp(prev_row.close_time)
        prev_row = row

    settled: list[dict] = []
    open_positions: dict[str, Position] = {}
    open_events: set[str] = set()
    cache_by_event: dict[str, dict] = {}
    for idx, row in enumerate(meta.itertuples(index=False), start=1):
        if idx == 1 or idx % args.progress_every_events == 0:
            print(f"event {idx}/{len(meta)} {row.event_ticker}", flush=True)
        prev_event = prev_by_event.get(row.event_ticker)
        if not prev_event:
            continue
        event_quotes = events[row.event_ticker]
        prev_quotes = events.get(prev_event)
        if prev_quotes is None:
            continue
        boundary = prev_close_by_event[row.event_ticker]
        prev_boundary = prev_quotes[prev_quotes["ts_end"] == boundary].copy()
        if prev_boundary.empty:
            continue
        prev_boundary["prev_mid"] = 0.5 * (
            prev_boundary["yes_bid_close"].astype(float) + prev_boundary["yes_ask_close"].astype(float)
        )
        cache = cache_by_event.get(row.event_ticker)
        if cache is None:
            cache = event_cache(btc, pd.Timestamp(row.open_time), args.train_days)
            if cache is None:
                continue
            cache_by_event[row.event_ticker] = cache
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            settle_due(open_positions, scan_ts, btc, settled)
            if row.event_ticker in open_events:
                continue
            signals = signal_rows(scan_quotes, prev_boundary, btc, cache, scan_ts, args)
            if signals.empty:
                continue
            sig = signals.iloc[0]
            open_positions[sig["market_ticker"]] = Position(
                {
                    "strategy": "cross_hour_reset",
                    "event_ticker": sig["event_ticker"],
                    "prev_event_ticker": prev_event,
                    "market_ticker": sig["market_ticker"],
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
                    "minute_in_event": float(sig["minute_in_event"]),
                    "prev_mid": float(sig["prev_mid"]),
                }
            )
            open_events.add(str(sig["event_ticker"]))
    final_ts = max((pd.Timestamp(pos.row["close_time"]) for pos in open_positions.values()), default=pd.Timestamp.utcnow())
    settle_due(open_positions, final_ts + pd.Timedelta(days=1), btc, settled)
    return pd.DataFrame(settled)


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "total_pnl": 0.0, "premium_deployed": 0.0, "return_on_premium": 0.0}
    ordered = trades.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
    premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
    pnl = ordered["pnl"].astype(float)
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    drawdown = equity - equity.cummax()
    return {
        "trades": int(len(ordered)),
        "total_pnl": float(pnl.sum()),
        "premium_deployed": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": float(pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum())) if (pnl < 0).any() else float("inf"),
        "max_drawdown": float(drawdown.min()),
        "yes_trades": int((ordered["side"] == "yes").sum()),
        "no_trades": int((ordered["side"] == "no").sum()),
    }


def main() -> int:
    args = parse_args()
    event_close_start = ts_arg(args.event_close_start)
    event_close_end = ts_arg(args.event_close_end)
    quotes, btc = load_data(args.db, event_close_start, event_close_end)
    if quotes.empty:
        raise SystemExit("No quotes found.")
    trades = run_backtest(quotes, btc, args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = args.output_dir / "cross_hour_reset_trades.csv"
    summary_path = args.output_dir / "cross_hour_reset_summary.csv"
    report_path = args.output_dir / "cross_hour_reset_report.json"
    trades.to_csv(trades_path, index=False)
    stats = summarize(trades)
    pd.DataFrame([stats]).to_csv(summary_path, index=False)
    report = {
        "db": str(args.db),
        "event_close_start": str(event_close_start),
        "event_close_end": str(event_close_end),
        "early_minutes": args.early_minutes,
        "min_edge_cents": args.min_edge_cents,
        "max_spread_cents": args.max_spread_cents,
        "prev_extreme": args.prev_extreme,
        "execution_source": "observed historical Kalshi bid/ask candle close; no forward fill",
        "trades_path": str(trades_path),
        "summary_path": str(summary_path),
        "stats": stats,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
