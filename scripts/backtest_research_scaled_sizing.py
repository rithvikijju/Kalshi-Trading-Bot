#!/usr/bin/env python3
"""Backtest bankroll-scaled sizing on the saved research trade streams.

This is a portfolio sizing overlay. It keeps the already backtested research
signal sequence unchanged, then sizes each accepted entry from bankroll,
available cash, and active exposure.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, kalshi_fee_dollars


DEFAULT_INPUTS = [
    PROJECT_ROOT / "backtest_outputs" / "march_official_fee_2c" / "backtest_1hr_research_trades.csv",
    PROJECT_ROOT / "backtest_outputs" / "april_official_fee_2c" / "backtest_1hr_research_trades.csv",
]


@dataclass
class OpenPosition:
    row: dict
    contracts: int
    cost: float
    fee: float


def cost_for(price: float, contracts: int) -> float:
    return price * contracts + kalshi_fee_dollars(price, contracts=contracts, liquidity="taker")


def choose_contracts(
    price: float,
    cash: float,
    bankroll: float,
    active_cost: float,
    per_market_fraction: float,
    max_total_fraction: float,
    max_contracts_per_trade: int,
) -> int:
    per_market_budget = bankroll * per_market_fraction
    total_remaining_budget = bankroll * max_total_fraction - active_cost
    budget = min(cash, per_market_budget, total_remaining_budget)
    if budget <= 0 or cost_for(price, 1) > budget:
        return 0

    high = max(1, int(math.floor(budget / max(price, 0.01))) + 2)
    high = min(high, max_contracts_per_trade)
    if high <= 0:
        return 0
    while cost_for(price, high) <= budget:
        if high >= max_contracts_per_trade:
            break
        high *= 2
        high = min(high, max_contracts_per_trade)
    low, best = 1, 1
    while low <= high:
        mid = (low + high) // 2
        if cost_for(price, mid) <= budget:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def settle_due(open_positions: dict[str, OpenPosition], now: pd.Timestamp, cash: float, settled: list[dict]) -> float:
    due = [
        ticker
        for ticker, pos in open_positions.items()
        if pd.Timestamp(pos.row["settle_time"]) <= now
    ]
    for ticker in due:
        pos = open_positions.pop(ticker)
        payout = float(pos.row["payout"]) * pos.contracts
        pnl = payout - pos.cost
        cash += payout
        out = dict(pos.row)
        out["contracts"] = pos.contracts
        out["entry_fee"] = pos.fee
        out["premium_deployed"] = pos.cost
        out["payout"] = payout
        out["pnl"] = pnl
        out["cash_after_settle"] = cash
        settled.append(out)
    return cash


def simulate(
    trades: pd.DataFrame,
    starting_bankroll: float,
    per_market_fraction: float,
    max_total_fraction: float,
    max_contracts_per_trade: int,
) -> tuple[pd.DataFrame, dict]:
    trades = trades.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["settle_time"] = pd.to_datetime(trades["settle_time"], utc=True)
    trades = trades.sort_values(["entry_time", "market_ticker"]).reset_index(drop=True)

    cash = float(starting_bankroll)
    open_positions: dict[str, OpenPosition] = {}
    settled: list[dict] = []
    skipped = {"duplicate": 0, "budget": 0}
    active_cost_peak = 0.0
    active_positions_peak = 0

    for _, row_obj in trades.iterrows():
        row = row_obj.to_dict()
        now = pd.Timestamp(row["entry_time"])
        cash = settle_due(open_positions, now, cash, settled)

        ticker = row["market_ticker"]
        if ticker in open_positions:
            skipped["duplicate"] += 1
            continue

        active_cost = sum(pos.cost for pos in open_positions.values())
        bankroll = cash + active_cost
        contracts = choose_contracts(
            price=float(row["entry_price"]),
            cash=cash,
            bankroll=bankroll,
            active_cost=active_cost,
            per_market_fraction=per_market_fraction,
            max_total_fraction=max_total_fraction,
            max_contracts_per_trade=max_contracts_per_trade,
        )
        if contracts <= 0:
            skipped["budget"] += 1
            continue

        fee = kalshi_fee_dollars(float(row["entry_price"]), contracts=contracts, liquidity="taker")
        cost = float(row["entry_price"]) * contracts + fee
        cash -= cost
        open_positions[ticker] = OpenPosition(row=row, contracts=contracts, cost=cost, fee=fee)
        active_cost_peak = max(active_cost_peak, sum(pos.cost for pos in open_positions.values()))
        active_positions_peak = max(active_positions_peak, len(open_positions))

    if open_positions:
        final_ts = max(pd.Timestamp(pos.row["settle_time"]) for pos in open_positions.values())
        cash = settle_due(open_positions, final_ts, cash, settled)

    settled_df = pd.DataFrame(settled)
    stats = compute_stats(
        settled_df,
        starting_bankroll=starting_bankroll,
        ending_cash=cash,
        skipped=skipped,
        active_cost_peak=active_cost_peak,
        active_positions_peak=active_positions_peak,
    )
    return settled_df, stats


def compute_stats(
    settled: pd.DataFrame,
    starting_bankroll: float,
    ending_cash: float,
    skipped: dict[str, int],
    active_cost_peak: float,
    active_positions_peak: int,
) -> dict:
    if settled.empty:
        return {
            "trades": 0,
            "contracts": 0,
            "starting_bankroll": starting_bankroll,
            "ending_bankroll": ending_cash,
            "total_pnl": ending_cash - starting_bankroll,
            "return_on_start": (ending_cash / starting_bankroll - 1.0) if starting_bankroll else 0.0,
            "premium_deployed": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "max_active_cost": active_cost_peak,
            "max_active_positions": active_positions_peak,
            "avg_contracts": 0.0,
            "max_contracts": 0,
            "skipped_duplicate": skipped["duplicate"],
            "skipped_budget": skipped["budget"],
        }

    pnl = settled["pnl"].astype(float)
    premium = settled["premium_deployed"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
    equity = starting_bankroll + pnl.cumsum()
    drawdown = equity - equity.cummax()
    drawdown_pct = drawdown / equity.cummax().replace(0, pd.NA)

    return {
        "trades": int(len(settled)),
        "contracts": int(settled["contracts"].sum()),
        "starting_bankroll": float(starting_bankroll),
        "ending_bankroll": float(ending_cash),
        "total_pnl": float(ending_cash - starting_bankroll),
        "return_on_start": float(ending_cash / starting_bankroll - 1.0) if starting_bankroll else 0.0,
        "premium_deployed": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0),
        "max_drawdown": float(drawdown.min()),
        "max_drawdown_pct": float(drawdown_pct.min()) if len(drawdown_pct.dropna()) else 0.0,
        "max_active_cost": float(active_cost_peak),
        "max_active_positions": int(active_positions_peak),
        "avg_contracts": float(settled["contracts"].mean()),
        "max_contracts": int(settled["contracts"].max()),
        "skipped_duplicate": int(skipped["duplicate"]),
        "skipped_budget": int(skipped["budget"]),
    }


def print_summary(name: str, stats: dict) -> None:
    print(f"\n{name}")
    print("-" * len(name))
    print(f"trades:                 {stats['trades']}")
    print(f"contracts:              {stats['contracts']}")
    print(f"starting_bankroll:      ${stats['starting_bankroll']:.2f}")
    print(f"ending_bankroll:        ${stats['ending_bankroll']:.2f}")
    print(f"total_pnl:              ${stats['total_pnl']:.2f}")
    print(f"return_on_start:        {stats['return_on_start']:.2%}")
    print(f"premium_deployed:       ${stats['premium_deployed']:.2f}")
    print(f"return_on_premium:      {stats['return_on_premium']:.2%}")
    print(f"win_rate:               {stats['win_rate']:.2%}")
    print(f"profit_factor:          {stats['profit_factor']:.2f}")
    print(f"max_drawdown:           ${stats['max_drawdown']:.2f} ({stats['max_drawdown_pct']:.2%})")
    print(f"max_active_cost:        ${stats['max_active_cost']:.2f}")
    print(f"max_active_positions:   {stats['max_active_positions']}")
    print(f"avg_contracts:          {stats['avg_contracts']:.2f}")
    print(f"max_contracts:          {stats['max_contracts']}")
    print(f"skipped_budget:         {stats['skipped_budget']}")
    print(f"skipped_duplicate:      {stats['skipped_duplicate']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest scaled sizing on saved research trades.")
    parser.add_argument("--starting-bankroll", type=float, default=20.0)
    parser.add_argument("--per-market-fraction", type=float, default=float(CFG.get("max_per_market", 0.20)))
    parser.add_argument("--max-total-fraction", type=float, default=float(CFG.get("max_total_risk", 0.50)))
    parser.add_argument("--max-contracts-per-trade", type=int, default=5)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "scaled_sizing_20")
    parser.add_argument("inputs", nargs="*", type=Path, default=DEFAULT_INPUTS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    loaded = []
    for path in args.inputs:
        df = pd.read_csv(path)
        name = path.parent.name
        scaled, stats = simulate(
            df,
            args.starting_bankroll,
            args.per_market_fraction,
            args.max_total_fraction,
            args.max_contracts_per_trade,
        )
        scaled.to_csv(args.out_dir / f"{name}_scaled_trades.csv", index=False)
        stats_row = {"run": name, **stats}
        summary_rows.append(stats_row)
        print_summary(name, stats)
        loaded.append(df)

    if len(loaded) > 1:
        combined = pd.concat(loaded, ignore_index=True)
        scaled, stats = simulate(
            combined,
            args.starting_bankroll,
            args.per_market_fraction,
            args.max_total_fraction,
            args.max_contracts_per_trade,
        )
        scaled.to_csv(args.out_dir / "combined_scaled_trades.csv", index=False)
        summary_rows.append({"run": "combined", **stats})
        print_summary("combined", stats)

    pd.DataFrame(summary_rows).to_csv(args.out_dir / "scaled_summary.csv", index=False)
    print(f"\nwrote {args.out_dir / 'scaled_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
