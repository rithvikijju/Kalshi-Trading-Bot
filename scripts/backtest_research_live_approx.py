#!/usr/bin/env python3
"""Replay the research bot against logged scan times with adjustable candle alignment.

This is a diagnostic tool, not the canonical faithful backtest.  It answers:
"If I force historical Kalshi candles to approximate the live orderbook seen by
the bot, do I recover the actual live trades?"

The default canonical replay uses completed candles only.  The useful diagnostic
mode here is usually:

    --quote-timing containing --price-mode close

That uses the candle bucket that contains the scan timestamp.  It is intentionally
marked approximate because the full bucket was not known at the decision second.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, kalshi_fee_dollars
from scripts.backtest_research_duckdb import (
    DEFAULT_DB,
    build_event_cache,
    btc_at_or_before,
    signals_for_time,
)

LOCAL_TZ = ZoneInfo("America/Denver")
FETCH_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| fetching orderbooks: (KXBTCD-[A-Z0-9]+) ")
PORTFOLIO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| portfolio balance=\$(\d+(?:\.\d+)?) "
    r"value=\$(\d+(?:\.\d+)?) active_exposure=\$(\d+(?:\.\d+)?) local_active=\$(\d+(?:\.\d+)?)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approximate live replay from logged scan times.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--log", action="append", type=Path, help="Live stdout log. Defaults to logs/research_live_*.out.log")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "research_live_approx")
    parser.add_argument(
        "--quote-timing",
        choices=("causal", "containing"),
        default="causal",
        help=(
            "causal uses only the last completed Kalshi candle. containing uses the current "
            "minute bucket and is lookahead unless --allow-lookahead-diagnostic is set."
        ),
    )
    parser.add_argument(
        "--allow-lookahead-diagnostic",
        action="store_true",
        help="Permit --quote-timing containing for diagnostics only. Never use it as a valid backtest.",
    )
    parser.add_argument("--price-mode", choices=("open", "close", "worst", "best", "midrange"), default="close")
    parser.add_argument("--dedupe", choices=("ticker", "event"), default="ticker")
    parser.add_argument("--contracts", type=int, default=3)
    parser.add_argument("--starting-bankroll", type=float, default=20.59)
    parser.add_argument("--no-logged-portfolio", action="store_true")
    parser.add_argument("--compare-live-db", type=Path, default=Path.home() / ".btc_kalshi_bot" / "research_live_trades.db")
    return parser.parse_args()


def ts_arg(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def log_ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize(LOCAL_TZ).tz_convert("UTC")


def parse_scan_cycles(logs: list[Path], start: pd.Timestamp, end: pd.Timestamp, use_portfolio: bool) -> pd.DataFrame:
    cycles: list[dict] = []
    current: dict | None = None
    for log_path in logs:
        if not log_path.exists():
            continue
        for line in log_path.read_text(errors="replace").splitlines():
            fetch = FETCH_RE.search(line)
            if fetch:
                scan_ts = log_ts(fetch.group(1))
                if start <= scan_ts <= end:
                    current = {
                        "scan_ts": scan_ts,
                        "event_ticker": fetch.group(2),
                        "log": log_path.name,
                        "portfolio_available": float("nan"),
                        "portfolio_value": float("nan"),
                        "portfolio_active_exposure": float("nan"),
                        "portfolio_local_active": float("nan"),
                    }
                    cycles.append(current)
                else:
                    current = None
                continue
            portfolio = PORTFOLIO_RE.search(line)
            if use_portfolio and portfolio and current is not None:
                current["portfolio_available"] = float(portfolio.group(2))
                current["portfolio_value"] = float(portfolio.group(3))
                current["portfolio_active_exposure"] = float(portfolio.group(4))
                current["portfolio_local_active"] = float(portfolio.group(5))
    return pd.DataFrame(cycles).sort_values("scan_ts").reset_index(drop=True)


def load_tables(db: Path, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db), read_only=True)
    quotes = con.execute(
        """
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_open, q.yes_bid_high, q.yes_bid_low, q.yes_bid_close,
               q.yes_ask_open, q.yes_ask_high, q.yes_ask_low, q.yes_ask_close,
               q.yes_ask_exe, q.no_ask_exe, q.spread_cents, q.fidelity,
               m.open_time, m.close_time, m.event_open_time, m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE m.is_hourly_kxbtcd AND m.is_cumulative
          AND q.available_at >= ? AND q.available_at <= ?
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
        """,
        [(start - pd.Timedelta(minutes=2)).to_pydatetime(), (end + pd.Timedelta(minutes=2)).to_pydatetime()],
    ).fetchdf()
    btc = con.execute(
        """
        SELECT available_at AS time, open, high, low, close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        WHERE available_at >= ? AND available_at <= ?
        ORDER BY available_at
        """,
        [(start - pd.Timedelta(days=10)).to_pydatetime(), (end + pd.Timedelta(hours=2)).to_pydatetime()],
    ).fetchdf()
    con.close()
    for col in ("available_at", "ts_end", "open_time", "close_time", "event_open_time"):
        quotes[col] = pd.to_datetime(quotes[col], utc=True)
    btc["time"] = pd.to_datetime(btc["time"], utc=True)
    return quotes, btc


def quote_time_for_scan(scan_ts: pd.Timestamp, timing: str) -> pd.Timestamp:
    minute = scan_ts.floor("min")
    if timing == "causal":
        return minute
    return minute + pd.Timedelta(minutes=1)


def apply_price_mode(scan_quotes: pd.DataFrame, mode: str) -> pd.DataFrame:
    q = scan_quotes.copy()
    if mode == "close":
        bid = q["yes_bid_close"].astype(float)
        ask = q["yes_ask_close"].astype(float)
    elif mode == "open":
        bid = q["yes_bid_open"].astype(float)
        ask = q["yes_ask_open"].astype(float)
    elif mode == "worst":
        bid = q["yes_bid_low"].astype(float)
        ask = q["yes_ask_high"].astype(float)
    elif mode == "best":
        bid = q["yes_bid_high"].astype(float)
        ask = q["yes_ask_low"].astype(float)
    else:
        bid = 0.5 * (q["yes_bid_low"].astype(float) + q["yes_bid_high"].astype(float))
        ask = 0.5 * (q["yes_ask_low"].astype(float) + q["yes_ask_high"].astype(float))
    bid = bid.clip(0.0, 1.0)
    ask = ask.clip(0.0, 1.0)
    ask = ask.where(ask >= bid, bid)
    q["yes_bid_close"] = bid
    q["yes_ask_close"] = ask
    q["yes_ask_exe"] = ask
    q["no_ask_exe"] = 1.0 - bid
    q["spread_cents"] = (ask - bid) * 100.0
    q["fidelity"] = "live_approx_" + mode
    return q


def active_exposure(open_positions: dict[str, dict]) -> float:
    return sum(pos["contracts"] * pos["entry_price"] + pos["entry_fee"] for pos in open_positions.values())


def choose_contracts(entry_price: float, max_contracts: int, bankroll: float, available: float, total_exposure: float) -> int:
    budget = min(
        available,
        bankroll * float(CFG.get("max_per_market", 0.20)),
        bankroll * float(CFG.get("max_total_risk", 0.50)) - total_exposure,
    )
    if budget <= 0:
        return 0
    chosen = 0
    for contracts in range(1, max_contracts + 1):
        fee = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
        if entry_price * contracts + fee <= budget:
            chosen = contracts
    return chosen


def settle_due(open_positions: dict[str, dict], now: pd.Timestamp, btc: pd.DataFrame, settled: list[dict]) -> float:
    cash_in = 0.0
    for ticker, pos in list(open_positions.items()):
        if pos["close_time"] > now:
            continue
        settlement_spot, _ = btc_at_or_before(btc, pos["close_time"])
        if settlement_spot is None:
            continue
        yes_settles = settlement_spot >= pos["strike"]
        win = (yes_settles and pos["side"] == "yes") or ((not yes_settles) and pos["side"] == "no")
        payout = pos["contracts"] * (1.0 if win else 0.0)
        pnl = payout - pos["contracts"] * pos["entry_price"] - pos["entry_fee"]
        settled.append(
            {
                **pos,
                "settle_time": pos["close_time"],
                "settlement_spot": settlement_spot,
                "settlement": "yes" if yes_settles else "no",
                "payout": payout,
                "pnl": pnl,
            }
        )
        cash_in += payout
        del open_positions[ticker]
    return cash_in


def run_replay(args: argparse.Namespace, cycles: pd.DataFrame, quotes: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    quote_groups = {key: group for key, group in quotes.groupby(["event_ticker", "available_at"], sort=False)}
    event_cache: dict[str, dict] = {}
    open_positions: dict[str, dict] = {}
    settled: list[dict] = []
    synthetic_cash = float(args.starting_bankroll)
    blocked_events: set[str] = set()

    for _, cycle in cycles.iterrows():
        scan_ts = pd.Timestamp(cycle["scan_ts"])
        event_ticker = str(cycle["event_ticker"])
        synthetic_cash += settle_due(open_positions, scan_ts, btc, settled)
        if args.dedupe == "event":
            blocked_events = {pos["event_ticker"] for pos in open_positions.values()}
            if event_ticker in blocked_events:
                continue

        quote_time = quote_time_for_scan(scan_ts, args.quote_timing)
        scan_quotes = quote_groups.get((event_ticker, quote_time))
        if scan_quotes is None or scan_quotes.empty:
            continue
        event_open = scan_quotes["event_open_time"].iloc[0]
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, 7)
            event_cache[event_ticker] = emp_cache
        if not emp_cache:
            continue

        priced = apply_price_mode(scan_quotes, args.price_mode)
        signals = signals_for_time("research", priced, btc, emp_cache, scan_ts)
        if signals.empty:
            continue
        signals = signals[~signals["market_ticker"].isin(open_positions)]
        if args.dedupe == "event":
            signals = signals[~signals["event_ticker"].isin(blocked_events)]
        if signals.empty:
            continue
        sig = signals.iloc[0]

        logged_portfolio = not args.no_logged_portfolio and pd.notna(cycle.get("portfolio_available"))
        if logged_portfolio:
            available = float(cycle["portfolio_available"])
            bankroll = max(float(cycle["portfolio_value"]), available)
            exposure = max(float(cycle["portfolio_active_exposure"]), active_exposure(open_positions))
        else:
            available = synthetic_cash
            bankroll = max(float(args.starting_bankroll), synthetic_cash)
            exposure = active_exposure(open_positions)
        contracts = choose_contracts(float(sig["entry_price"]), args.contracts, bankroll, available, exposure)
        if contracts <= 0:
            continue
        fee = kalshi_fee_dollars(float(sig["entry_price"]), contracts=contracts, liquidity="taker")
        cost = contracts * float(sig["entry_price"]) + fee
        synthetic_cash -= cost
        open_positions[str(sig["market_ticker"])] = {
            "strategy": "research_live_approx",
            "event_ticker": str(sig["event_ticker"]),
            "market_ticker": str(sig["market_ticker"]),
            "side": str(sig["side"]),
            "strike": float(sig["floor_strike"]),
            "entry_time": scan_ts,
            "source_quote_time": quote_time,
            "quote_timing": args.quote_timing,
            "price_mode": args.price_mode,
            "close_time": pd.Timestamp(sig["close_time"]),
            "entry_price": float(sig["entry_price"]),
            "entry_fee": fee,
            "contracts": contracts,
            "entry_spot": float(sig["entry_spot"]),
            "model_p_yes": float(sig["model_p_yes"]),
            "net_edge_cents": float(sig["net_edge_cents"]),
            "edge_threshold_cents": float(sig["edge_threshold_cents"]),
            "spread_cents": float(sig["spread_cents"]),
            "used_logged_portfolio": bool(logged_portfolio),
        }

    synthetic_cash += settle_due(open_positions, cycles["scan_ts"].max() + pd.Timedelta(days=1), btc, settled)
    if not settled:
        return pd.DataFrame()
    return pd.DataFrame(settled).sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)


def load_live_fills(live_db: Path, db: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if not live_db.exists():
        return pd.DataFrame()
    live = pd.read_sql_query("select * from research_live_trades order by created_at", sqlite3.connect(live_db))
    if live.empty:
        return live
    live["created_at_ts"] = pd.to_datetime(live["created_at"], utc=True)
    live = live[(live["created_at_ts"] >= start) & (live["created_at_ts"] <= end) & live["status"].isin(["filled", "partial_filled"])].copy()
    if live.empty:
        return live
    con = duckdb.connect(str(db), read_only=True)
    rows: list[dict] = []
    for _, row in live.iterrows():
        strike_row = con.execute("select floor_strike from kalshi_markets where market_ticker=? limit 1", [row["market_ticker"]]).fetchone()
        if not strike_row:
            continue
        close_time = pd.Timestamp(row["close_time"]).tz_convert("UTC")
        settle = con.execute(
            "select close from btc_1m where available_at <= ? order by available_at desc limit 1",
            [close_time.to_pydatetime()],
        ).fetchone()
        if not settle:
            continue
        strike = float(strike_row[0])
        settlement_spot = float(settle[0])
        yes_settles = settlement_spot >= strike
        side = str(row["side"]).lower()
        win = (yes_settles and side == "yes") or ((not yes_settles) and side == "no")
        contracts = int(row["contracts"])
        entry_price = float(row["actual_entry_price"] if pd.notna(row["actual_entry_price"]) else row["entry_price"])
        entry_fee = float(row["actual_fee_paid"] if pd.notna(row["actual_fee_paid"]) else row["entry_fee_estimate"])
        rows.append(
            {
                "created_at": row["created_at_ts"],
                "event_ticker": row["event_ticker"],
                "market_ticker": row["market_ticker"],
                "side": side,
                "contracts": contracts,
                "entry_price": entry_price,
                "entry_fee": entry_fee,
                "settlement_spot": settlement_spot,
                "settlement": "yes" if yes_settles else "no",
                "pnl": contracts * (1.0 if win else 0.0) - contracts * entry_price - entry_fee,
            }
        )
    con.close()
    return pd.DataFrame(rows)


def summarize(replay: pd.DataFrame, live: pd.DataFrame, cycles: pd.DataFrame, args: argparse.Namespace) -> dict:
    replay_keys = set(zip(replay.get("market_ticker", []), replay.get("side", [])))
    live_keys = set(zip(live.get("market_ticker", []), live.get("side", [])))
    if not replay.empty:
        replay_exact = set(zip(replay["market_ticker"], replay["side"], pd.to_datetime(replay["entry_time"], utc=True).dt.floor("min")))
    else:
        replay_exact = set()
    if not live.empty:
        live_exact = set(zip(live["market_ticker"], live["side"], pd.to_datetime(live["created_at"], utc=True).dt.floor("min")))
    else:
        live_exact = set()

    def strike_from_ticker(ticker: object) -> float:
        try:
            return float(str(ticker).split("-T", 1)[1])
        except Exception:
            return float("nan")

    near_100 = 0
    near_200 = 0
    same_event = 0
    extra_100 = 0
    if not replay.empty and not live.empty:
        replay_scored = replay.copy()
        live_scored = live.copy()
        replay_scored["strike"] = replay_scored["market_ticker"].map(strike_from_ticker)
        live_scored["strike"] = live_scored["market_ticker"].map(strike_from_ticker)
        for _, live_row in live_scored.iterrows():
            same_event_rows = replay_scored[replay_scored["event_ticker"] == live_row["event_ticker"]]
            if not same_event_rows.empty:
                same_event += 1
            same_side = same_event_rows[same_event_rows["side"] == live_row["side"]]
            if not same_side.empty and ((same_side["strike"] - live_row["strike"]).abs() <= 100.01).any():
                near_100 += 1
            if not same_side.empty and ((same_side["strike"] - live_row["strike"]).abs() <= 200.01).any():
                near_200 += 1
        for _, replay_row in replay_scored.iterrows():
            same_side = live_scored[
                (live_scored["event_ticker"] == replay_row["event_ticker"])
                & (live_scored["side"] == replay_row["side"])
            ]
            if same_side.empty or not ((same_side["strike"] - replay_row["strike"]).abs() <= 100.01).any():
                extra_100 += 1

    return {
        "start": args.start,
        "end": args.end,
        "quote_timing": args.quote_timing,
        "price_mode": args.price_mode,
        "dedupe": args.dedupe,
        "scan_cycles": int(len(cycles)),
        "replay_trades": int(len(replay)),
        "replay_pnl": float(replay["pnl"].sum()) if not replay.empty else 0.0,
        "replay_deployed": float((replay["contracts"] * replay["entry_price"] + replay["entry_fee"]).sum()) if not replay.empty else 0.0,
        "live_trades": int(len(live)),
        "live_pnl": float(live["pnl"].sum()) if not live.empty else 0.0,
        "live_deployed": float((live["contracts"] * live["entry_price"] + live["entry_fee"]).sum()) if not live.empty else 0.0,
        "same_contract_side_matches": int(len(replay_keys & live_keys)),
        "exact_minute_matches": int(len(replay_exact & live_exact)),
        "same_event_side_strike_100_matches": int(near_100),
        "same_event_side_strike_200_matches": int(near_200),
        "same_event_any_side_matches": int(same_event),
        "extra_replay_trades_vs_live_strike_100": int(extra_100),
        "missed_live_trades_vs_replay_strike_100": int(len(live) - near_100),
    }


def main() -> int:
    args = parse_args()
    if args.quote_timing == "containing" and not args.allow_lookahead_diagnostic:
        raise SystemExit(
            "--quote-timing containing uses future information inside the current minute. "
            "Use --allow-lookahead-diagnostic only for labeled diagnostics, never for a valid backtest."
        )
    start = ts_arg(args.start)
    end = ts_arg(args.end)
    logs = args.log or sorted((PROJECT_ROOT / "logs").glob("research_live_*.out.log"))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cycles = parse_scan_cycles(logs, start, end, use_portfolio=not args.no_logged_portfolio)
    quotes, btc = load_tables(args.db, start, end)
    replay = run_replay(args, cycles, quotes, btc)
    live = load_live_fills(args.compare_live_db, args.db, start, end)
    summary = summarize(replay, live, cycles, args)

    replay_path = args.output_dir / f"replay_{args.quote_timing}_{args.price_mode}_{args.dedupe}.csv"
    live_path = args.output_dir / "actual_live_fills.csv"
    summary_path = args.output_dir / f"summary_{args.quote_timing}_{args.price_mode}_{args.dedupe}.json"
    replay.to_csv(replay_path, index=False)
    live.to_csv(live_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2))
    print(json.dumps({**summary, "replay_path": str(replay_path), "live_path": str(live_path), "summary_path": str(summary_path)}, indent=2))
    if not replay.empty:
        print("\nReplay trades:")
        print(replay[["entry_time", "event_ticker", "market_ticker", "side", "contracts", "entry_price", "entry_fee", "settlement", "pnl", "source_quote_time"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
