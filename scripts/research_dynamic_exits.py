#!/usr/bin/env python3
"""Replay executable post-entry exits for BTC 1-hour Kalshi strategies.

The entry trades come from an existing faithful backtest CSV. Exit fills use
observed bid-side historical candles only:

* long YES exits at yes_bid_close
* long NO exits at 1 - yes_ask_close

Every early exit pays an estimated taker exit fee. Holding to settlement keeps
the original backtest settlement PnL.
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

from config.btc_1hr_config import kalshi_fee_dollars


DEFAULT_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"
DEFAULT_TRADES = PROJECT_ROOT / "backtest_outputs" / "js_robust_chunked_bidask_full" / "backtest_js_robust_duckdb_trades.csv"
TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")


@dataclass(frozen=True)
class ExitRule:
    name: str
    take_profit_cents: float | None = None
    stop_loss_cents: float | None = None
    trailing_drawdown_cents: float | None = None
    activate_trailing_profit_cents: float | None = None
    exit_if_bid_ge: float | None = None
    exit_if_bid_le: float | None = None
    min_ttl_to_exit: float = 3.0


RULES = [
    ExitRule("hold"),
    ExitRule("tp_10c", take_profit_cents=10.0),
    ExitRule("tp_15c", take_profit_cents=15.0),
    ExitRule("sl_10c", stop_loss_cents=10.0),
    ExitRule("sl_15c", stop_loss_cents=15.0),
    ExitRule("tp10_sl10", take_profit_cents=10.0, stop_loss_cents=10.0),
    ExitRule("tp15_sl10", take_profit_cents=15.0, stop_loss_cents=10.0),
    ExitRule("trail_8_after_10", trailing_drawdown_cents=8.0, activate_trailing_profit_cents=10.0),
    ExitRule("sell_bid_ge_80", exit_if_bid_ge=0.80),
    ExitRule("sell_bid_ge_85", exit_if_bid_ge=0.85),
    ExitRule("panic_bid_le_40", exit_if_bid_le=0.40),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay post-entry exit rules on faithful Kalshi bid/ask paths.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "dynamic_exits")
    parser.add_argument(
        "--splits",
        nargs="+",
        choices=["train", "validation", "test"],
        default=["train", "validation"],
        help="Event-close splits to replay. Defaults to train+validation so test remains held out.",
    )
    return parser.parse_args()


def split_name(close_time: pd.Timestamp) -> str:
    close_time = close_time.tz_convert("UTC")
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def load_exit_quotes(db_path: Path, market_tickers: list[str]) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    quotes = con.execute(
        """
        SELECT market_ticker, available_at, ts_end, yes_bid_close, yes_ask_close
        FROM kalshi_quotes
        WHERE market_ticker IN (SELECT unnest(?))
        ORDER BY market_ticker, available_at
        """,
        [market_tickers],
    ).fetchdf()
    con.close()
    if quotes.empty:
        return quotes
    quotes["available_at"] = pd.to_datetime(quotes["available_at"], utc=True)
    quotes["ts_end"] = pd.to_datetime(quotes["ts_end"], utc=True)
    quotes["yes_bid_close"] = pd.to_numeric(quotes["yes_bid_close"], errors="coerce")
    quotes["yes_ask_close"] = pd.to_numeric(quotes["yes_ask_close"], errors="coerce")
    quotes["no_bid_close"] = 1.0 - quotes["yes_ask_close"]
    return quotes


def choose_exit(row: pd.Series, path: pd.DataFrame, rule: ExitRule) -> dict:
    if rule.name == "hold" or path.empty:
        return {
            "exit_type": "settlement",
            "exit_time": row["settle_time"],
            "exit_price": np.nan,
            "exit_fee": 0.0,
            "pnl": float(row["pnl"]),
        }

    side = row["side"]
    entry_price = float(row["entry_price"])
    entry_fee = float(row["entry_fee"])
    close_time = pd.Timestamp(row["close_time"])
    high_bid = -np.inf
    active_trailing = False

    for _, quote in path.iterrows():
        now = pd.Timestamp(quote["available_at"])
        ttl_min = (close_time - now).total_seconds() / 60.0
        if ttl_min < rule.min_ttl_to_exit:
            continue

        exit_bid = float(quote["yes_bid_close"] if side == "yes" else quote["no_bid_close"])
        if not np.isfinite(exit_bid) or exit_bid <= 0.0 or exit_bid >= 1.0:
            continue
        exit_fee = kalshi_fee_dollars(exit_bid, contracts=1, liquidity="taker")
        mark_pnl = exit_bid - entry_price - entry_fee - exit_fee
        profit_cents = 100.0 * (exit_bid - entry_price)
        net_loss_cents = -100.0 * mark_pnl
        high_bid = max(high_bid, exit_bid)

        reasons: list[str] = []
        if rule.take_profit_cents is not None and profit_cents >= rule.take_profit_cents:
            reasons.append("take_profit")
        if rule.stop_loss_cents is not None and net_loss_cents >= rule.stop_loss_cents:
            reasons.append("stop_loss")
        if rule.exit_if_bid_ge is not None and exit_bid >= rule.exit_if_bid_ge:
            reasons.append("bid_ge")
        if rule.exit_if_bid_le is not None and exit_bid <= rule.exit_if_bid_le:
            reasons.append("bid_le")
        if (
            rule.trailing_drawdown_cents is not None
            and rule.activate_trailing_profit_cents is not None
            and 100.0 * (high_bid - entry_price) >= rule.activate_trailing_profit_cents
        ):
            active_trailing = True
        if active_trailing and 100.0 * (high_bid - exit_bid) >= float(rule.trailing_drawdown_cents):
            reasons.append("trailing")

        if reasons:
            return {
                "exit_type": "+".join(reasons),
                "exit_time": now,
                "exit_price": exit_bid,
                "exit_fee": exit_fee,
                "pnl": mark_pnl,
            }

    return {
        "exit_type": "settlement",
        "exit_time": row["settle_time"],
        "exit_price": np.nan,
        "exit_fee": 0.0,
        "pnl": float(row["pnl"]),
    }


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (rule, split), group in results.groupby(["rule", "split"], sort=True):
        ordered = group.sort_values(["final_time", "entry_time", "market_ticker"])
        pnl = ordered["exit_pnl"].astype(float)
        premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
        equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
        drawdown = equity - equity.cummax()
        rows.append(
            {
                "rule": rule,
                "split": split,
                "trades": len(ordered),
                "early_exits": int((ordered["exit_type"] != "settlement").sum()),
                "pnl": pnl.sum(),
                "premium": premium.sum(),
                "return_on_premium": pnl.sum() / premium.sum() if premium.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    trades = pd.read_csv(args.trades, parse_dates=["entry_time", "close_time", "settle_time"])
    if trades.empty:
        raise SystemExit("No trades found.")
    trades["split"] = [split_name(pd.Timestamp(t)) for t in trades["close_time"]]
    trades = trades[trades["split"].isin(args.splits)].copy()
    if trades.empty:
        raise SystemExit(f"No trades found after split filter: {args.splits}")
    quotes = load_exit_quotes(args.db, sorted(trades["market_ticker"].unique()))
    quote_groups = {ticker: group.reset_index(drop=True) for ticker, group in quotes.groupby("market_ticker")}

    result_rows: list[dict] = []
    for _, row in trades.iterrows():
        path = quote_groups.get(row["market_ticker"], pd.DataFrame())
        if not path.empty:
            path = path[
                (path["available_at"] > row["entry_time"])
                & (path["available_at"] < row["close_time"])
            ].copy()
        for rule in RULES:
            exit_row = choose_exit(row, path, rule)
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
                    "exit_pnl": exit_row["pnl"],
                }
            )

    results = pd.DataFrame(result_rows)
    summary = summarize(results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = args.output_dir / f"{args.trades.stem}_dynamic_exit_detail.csv"
    summary_path = args.output_dir / f"{args.trades.stem}_dynamic_exit_summary.csv"
    report_path = args.output_dir / f"{args.trades.stem}_dynamic_exit_report.json"
    results.to_csv(detail_path, index=False)
    summary.to_csv(summary_path, index=False)
    report = {
        "db": str(args.db),
        "trades": str(args.trades),
        "detail_path": str(detail_path),
        "summary_path": str(summary_path),
        "splits": args.splits,
        "rules": [rule.__dict__ for rule in RULES],
    }
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(summary.sort_values(["split", "return_on_premium"], ascending=[True, False]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
