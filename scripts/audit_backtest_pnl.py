#!/usr/bin/env python3
"""Audit generated backtest trade CSVs for PnL and win/loss consistency."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


TOL = 1e-8


def as_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def audit_may8_trade_file(path: Path) -> tuple[int, int, int, int]:
    try:
        trades = pd.read_csv(path)
    except Exception:
        return (0, 0, 0, 0)
    if trades.empty:
        return (0, 0, 0, 0)

    entry = as_num(trades["entry_price"])
    fee = as_num(trades["entry_fee"])
    payout = as_num(trades["payout"])
    pnl = as_num(trades["pnl"])

    pnl_bad = int(((payout - entry - fee - pnl).abs() > TOL).sum())
    settlement_bad = int(
        (
            payout
            - (
                trades["settlement"].astype(str).str.lower()
                == trades["side"].astype(str).str.lower()
            ).astype(float)
        )
        .abs()
        .gt(TOL)
        .sum()
    )

    right = trades["settlement"].astype(str).str.lower() == trades["side"].astype(str).str.lower()
    profitable = pnl > 0
    win_label_bad = int((right ^ profitable).sum())

    summary_bad = audit_may8_summary(path.with_name("may8examine_summary.csv"), trades)
    return (len(trades), pnl_bad, settlement_bad, win_label_bad + summary_bad)


def audit_may8_summary(summary_path: Path, trades: pd.DataFrame) -> int:
    if not summary_path.exists() or trades.empty:
        return 0
    summary = pd.read_csv(summary_path)
    key_cols = ["source", "variant", "split"]
    rows: list[dict[str, object]] = []
    for key, group in trades.groupby(key_cols, sort=True):
        ordered = group.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
        pnl = as_num(ordered["pnl"])
        premium = as_num(ordered["entry_price"]) + as_num(ordered["entry_fee"])
        equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
        drawdown = equity - equity.cummax()
        rows.append(
            {
                "source": key[0],
                "variant": key[1],
                "split": key[2],
                "trades": int(len(ordered)),
                "pnl": float(pnl.sum()),
                "premium": float(premium.sum()),
                "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
                "yes_trades": int((ordered["side"].astype(str).str.lower() == "yes").sum()),
                "no_trades": int((ordered["side"].astype(str).str.lower() == "no").sum()),
            }
        )
    calc = pd.DataFrame(rows)
    merged = summary.merge(calc, on=key_cols, suffixes=("_reported", "_calc"), how="outer", indicator=True)
    bad = int((merged["_merge"] != "both").sum())
    for col in [
        "trades",
        "pnl",
        "premium",
        "return_on_premium",
        "win_rate",
        "max_drawdown",
        "yes_trades",
        "no_trades",
    ]:
        reported = as_num(merged[f"{col}_reported"])
        calc_col = as_num(merged[f"{col}_calc"])
        bad += int(((reported - calc_col).abs() > TOL).sum())
    return bad


def audit_risk_summary(path: Path) -> tuple[int, int, int]:
    summary = pd.read_csv(path)
    ok = 0
    bad = 0
    empty_trade_files = 0
    for _, row in summary.iterrows():
        trades_path = Path(str(row["trades_path"]))
        if not trades_path.is_absolute():
            trades_path = Path.cwd() / trades_path
        try:
            trades = pd.read_csv(trades_path)
        except Exception:
            trades = pd.DataFrame()
            empty_trade_files += 1

        if trades.empty:
            calc_pnl = 0.0
            calc_premium = 0.0
            calc_win_rate = 0.0
            calc_contracts = 0
            row_bad = 0
        else:
            payout = as_num(trades["payout"])
            premium = as_num(trades["premium_deployed"])
            pnl = as_num(trades["pnl"])
            row_bad = int(((payout - premium - pnl).abs() > TOL).sum())
            calc_pnl = float(pnl.sum())
            calc_premium = float(premium.sum())
            calc_win_rate = float((pnl > 0).mean())
            calc_contracts = int(as_num(trades["contracts"]).sum())

        matches = (
            abs(float(row["total_pnl"]) - calc_pnl) <= TOL
            and abs(float(row["premium_deployed"]) - calc_premium) <= TOL
            and abs(float(row["win_rate"]) - calc_win_rate) <= TOL
            and int(row["contracts"]) == calc_contracts
            and row_bad == 0
        )
        if matches:
            ok += 1
        else:
            bad += 1
            print(f"MISMATCH risk summary={path} policy={row.get('policy')} trades={trades_path}")
    return ok, bad, empty_trade_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("backtest_outputs"))
    args = parser.parse_args()

    root = args.root
    may8_files = sorted(root.rglob("may8examine_trades.csv"))
    risk_summaries = sorted(root.rglob("risk_adjusted_research_summary.csv"))

    total_rows = 0
    total_pnl_bad = 0
    total_settlement_bad = 0
    total_summary_bad = 0
    for path in may8_files:
        rows, pnl_bad, settlement_bad, summary_bad = audit_may8_trade_file(path)
        total_rows += rows
        total_pnl_bad += pnl_bad
        total_settlement_bad += settlement_bad
        total_summary_bad += summary_bad

    risk_ok = 0
    risk_bad = 0
    risk_empty = 0
    for path in risk_summaries:
        ok, bad, empty = audit_risk_summary(path)
        risk_ok += ok
        risk_bad += bad
        risk_empty += empty

    print("Backtest PnL audit")
    print(f"may8_trade_files={len(may8_files)} may8_rows={total_rows}")
    print(f"may8_pnl_formula_bad={total_pnl_bad}")
    print(f"may8_payout_vs_settlement_bad={total_settlement_bad}")
    print(f"may8_summary_or_winlabel_bad={total_summary_bad}")
    print(f"risk_summary_rows_ok={risk_ok} risk_summary_rows_bad={risk_bad} empty_risk_trade_files={risk_empty}")

    return 1 if total_pnl_bad or total_settlement_bad or total_summary_bad or risk_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
