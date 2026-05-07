#!/usr/bin/env python3
"""Replay model-vs-market emergency exits on faithful KXBTCD trade logs.

This is deliberately not a routine stop-loss. An early exit triggers only when
the updated model assigns a low probability of winning and the executable bid,
after another taker fee, is materially richer than the model's hold value.
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
from scripts.backtest_research_duckdb import BRTI_DAMPENING, JS_MARKET_SHRINK, DEFAULT_DB, feature_frame_for_strategy


DEFAULT_TRADES = PROJECT_ROOT / "backtest_outputs" / "js_robust_chunked_bidask_full" / "backtest_js_robust_duckdb_trades.csv"
TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")


@dataclass(frozen=True)
class EmergencyRule:
    name: str
    max_model_p_win: float
    min_market_over_model_cents: float
    min_exit_bid: float
    min_ttl_to_exit: float = 3.0
    min_age_minutes: float = 1.0


RULES = [
    EmergencyRule("hold", 0.0, 999.0, 999.0),
    EmergencyRule("pwin_le_25_mkt_plus_3", 0.25, 3.0, 0.20),
    EmergencyRule("pwin_le_30_mkt_plus_5", 0.30, 5.0, 0.25),
    EmergencyRule("pwin_le_35_mkt_plus_5", 0.35, 5.0, 0.30),
    EmergencyRule("pwin_le_35_mkt_plus_8", 0.35, 8.0, 0.30),
    EmergencyRule("pwin_le_40_mkt_plus_8", 0.40, 8.0, 0.35),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay model-vs-market emergency exits on train/validation.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "model_emergency_exits")
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument(
        "--splits",
        nargs="+",
        choices=["train", "validation", "test"],
        default=["train", "validation"],
        help="Event-close splits to replay. Defaults to train+validation.",
    )
    parser.add_argument(
        "--rules",
        nargs="+",
        default=None,
        help="Optional rule-name allowlist. Use this after freezing a candidate before test.",
    )
    return parser.parse_args()


def split_name(close_time: pd.Timestamp) -> str:
    close_time = close_time.tz_convert("UTC")
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def load_market_paths(db_path: Path, market_tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    quotes = con.execute(
        """
        SELECT q.market_ticker, q.available_at, q.ts_end, q.yes_bid_close, q.yes_ask_close,
               m.event_ticker, m.event_open_time, m.close_time, m.floor_strike
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE q.market_ticker IN (SELECT unnest(?))
        ORDER BY q.market_ticker, q.available_at
        """,
        [market_tickers],
    ).fetchdf()
    if quotes.empty:
        con.close()
        return quotes, pd.DataFrame()
    btc_start = pd.to_datetime(quotes["event_open_time"], utc=True).min() - pd.Timedelta(days=10)
    btc_end = pd.to_datetime(quotes["close_time"], utc=True).max() + pd.Timedelta(minutes=1)
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
    for col in ("available_at", "ts_end", "event_open_time", "close_time"):
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


def build_event_cache(btc: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train = btc[(btc["time"] >= event_open - pd.Timedelta(days=train_days)) & (btc["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        return None
    return build_emp_cache_fast(train)


def model_p_yes_for_quote(row: pd.Series, quote: pd.Series, btc: pd.DataFrame, emp_cache: dict, strategy: str) -> float | None:
    now = pd.Timestamp(quote["available_at"])
    spot, idx = btc_at_or_before(btc, now)
    if spot is None or idx is None or idx < 1440:
        return None
    ttl_min = (pd.Timestamp(quote["close_time"]) - now).total_seconds() / 60.0
    if ttl_min <= 0.0:
        return None
    rv60 = btc.iloc[idx].get("rv_60m", np.nan)
    current_vol = float(rv60) if np.isfinite(rv60) else None
    strike = np.asarray([float(row["strike"])], dtype=float)
    p_yes = vectorized_p_above(strike, spot, ttl_min, emp_cache, current_vol, BRTI_DAMPENING)[0]
    normal_p = lognormal_p_above(strike, spot, ttl_min, current_vol)[0]
    if np.isfinite(normal_p):
        p_yes = 0.70 * p_yes + 0.30 * normal_p
    if not np.isfinite(p_yes):
        return None
    if str(strategy).startswith("js_robust"):
        market_mid = 0.5 * (float(quote["yes_bid_close"]) + float(quote["yes_ask_close"]))
        p_yes = (1.0 - JS_MARKET_SHRINK) * p_yes + JS_MARKET_SHRINK * market_mid
    return float(np.clip(p_yes, 0.0, 1.0))


def choose_exit(
    row: pd.Series,
    path: pd.DataFrame,
    btc: pd.DataFrame,
    emp_cache: dict | None,
    rule: EmergencyRule,
) -> dict:
    if rule.name == "hold" or path.empty or emp_cache is None:
        return {
            "exit_type": "settlement",
            "exit_time": row["settle_time"],
            "exit_price": np.nan,
            "exit_fee": 0.0,
            "model_p_win": np.nan,
            "market_over_model_cents": np.nan,
            "pnl": float(row["pnl"]),
        }
    side = str(row["side"])
    entry_time = pd.Timestamp(row["entry_time"])
    close_time = pd.Timestamp(row["close_time"])
    entry_price = float(row["entry_price"])
    entry_fee = float(row["entry_fee"])
    strategy = str(row.get("strategy", "research"))

    for _, quote in path.iterrows():
        now = pd.Timestamp(quote["available_at"])
        ttl_min = (close_time - now).total_seconds() / 60.0
        age_min = (now - entry_time).total_seconds() / 60.0
        if ttl_min < rule.min_ttl_to_exit or age_min < rule.min_age_minutes:
            continue
        yes_bid = float(quote["yes_bid_close"])
        yes_ask = float(quote["yes_ask_close"])
        exit_bid = yes_bid if side == "yes" else 1.0 - yes_ask
        if not np.isfinite(exit_bid) or exit_bid <= 0.0 or exit_bid >= 1.0:
            continue
        p_yes = model_p_yes_for_quote(row, quote, btc, emp_cache, strategy)
        if p_yes is None:
            continue
        p_win = p_yes if side == "yes" else 1.0 - p_yes
        exit_fee = kalshi_fee_dollars(exit_bid, contracts=1, liquidity="taker")
        exit_value = exit_bid - exit_fee
        market_over_model_cents = 100.0 * (exit_value - p_win)
        if (
            p_win <= rule.max_model_p_win
            and exit_bid >= rule.min_exit_bid
            and market_over_model_cents >= rule.min_market_over_model_cents
        ):
            return {
                "exit_type": "model_emergency",
                "exit_time": now,
                "exit_price": exit_bid,
                "exit_fee": exit_fee,
                "model_p_win": p_win,
                "market_over_model_cents": market_over_model_cents,
                "pnl": exit_bid - entry_price - entry_fee - exit_fee,
            }

    return {
        "exit_type": "settlement",
        "exit_time": row["settle_time"],
        "exit_price": np.nan,
        "exit_fee": 0.0,
        "model_p_win": np.nan,
        "market_over_model_cents": np.nan,
        "pnl": float(row["pnl"]),
    }


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (rule, split), group in results.groupby(["rule", "split"], sort=True):
        ordered = group.sort_values(["final_time", "entry_time", "market_ticker"]).reset_index(drop=True)
        pnl = ordered["exit_pnl"].astype(float)
        premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
        equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
        drawdown = equity - equity.cummax()
        rows.append(
            {
                "rule": rule,
                "split": split,
                "trades": int(len(ordered)),
                "early_exits": int((ordered["exit_type"] != "settlement").sum()),
                "pnl": float(pnl.sum()),
                "premium": float(premium.sum()),
                "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    CFG.update(RESEARCH_CFG)
    trades = pd.read_csv(args.trades, parse_dates=["entry_time", "close_time", "settle_time"])
    if trades.empty:
        raise SystemExit("No trades found.")
    trades["split"] = [split_name(pd.Timestamp(t)) for t in trades["close_time"]]
    trades = trades[trades["split"].isin(args.splits)].copy()
    if trades.empty:
        raise SystemExit(f"No trades found after split filter: {args.splits}")
    rules = RULES
    if args.rules:
        wanted = set(args.rules)
        rules = [rule for rule in RULES if rule.name in wanted]
        missing = wanted - {rule.name for rule in rules}
        if missing:
            raise SystemExit(f"Unknown rule names: {sorted(missing)}")
        if not any(rule.name == "hold" for rule in rules):
            rules = [RULES[0], *rules]
    quotes, btc = load_market_paths(args.db, sorted(trades["market_ticker"].unique()))
    quote_groups = {ticker: group.reset_index(drop=True) for ticker, group in quotes.groupby("market_ticker")}
    event_cache: dict[str, dict | None] = {}

    result_rows: list[dict] = []
    for idx, row in trades.iterrows():
        if idx == 0 or len(result_rows) % 500 == 0:
            print(f"trade {idx + 1}/{len(trades)} {row['market_ticker']}", flush=True)
        path = quote_groups.get(row["market_ticker"], pd.DataFrame())
        if path.empty:
            post_entry = pd.DataFrame()
            emp_cache = None
        else:
            post_entry = path[
                (path["available_at"] > row["entry_time"])
                & (path["available_at"] < row["close_time"])
            ].copy()
            event_open = pd.Timestamp(path["event_open_time"].iloc[0])
            event_ticker = str(path["event_ticker"].iloc[0])
            if event_ticker not in event_cache:
                event_cache[event_ticker] = build_event_cache(btc, event_open, args.train_days)
            emp_cache = event_cache[event_ticker]
        for rule in rules:
            exit_row = choose_exit(row, post_entry, btc, emp_cache, rule)
            result_rows.append(
                {
                    "rule": rule.name,
                    "split": row["split"],
                    "strategy": row["strategy"],
                    "event_ticker": row["event_ticker"],
                    "market_ticker": row["market_ticker"],
                    "side": row["side"],
                    "entry_time": row["entry_time"],
                    "close_time": row["close_time"],
                    "entry_price": float(row["entry_price"]),
                    "entry_fee": float(row["entry_fee"]),
                    "baseline_pnl": float(row["pnl"]),
                    "exit_type": exit_row["exit_type"],
                    "exit_time": exit_row["exit_time"],
                    "final_time": exit_row["exit_time"],
                    "exit_price": exit_row["exit_price"],
                    "exit_fee": exit_row["exit_fee"],
                    "model_p_win": exit_row["model_p_win"],
                    "market_over_model_cents": exit_row["market_over_model_cents"],
                    "exit_pnl": exit_row["pnl"],
                }
            )

    results = pd.DataFrame(result_rows)
    summary = summarize(results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = args.output_dir / f"{args.trades.stem}_model_emergency_exit_detail.csv"
    summary_path = args.output_dir / f"{args.trades.stem}_model_emergency_exit_summary.csv"
    report_path = args.output_dir / f"{args.trades.stem}_model_emergency_exit_report.json"
    results.to_csv(detail_path, index=False)
    summary.to_csv(summary_path, index=False)
    report = {
        "db": str(args.db),
        "trades": str(args.trades),
        "splits": args.splits,
        "rules": [rule.__dict__ for rule in rules],
        "detail_path": str(detail_path),
        "summary_path": str(summary_path),
        "exit_decision": "exit only if executable bid minus exit taker fee exceeds updated model hold value by rule margin",
    }
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(summary.sort_values(["split", "return_on_premium"], ascending=[True, False]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
