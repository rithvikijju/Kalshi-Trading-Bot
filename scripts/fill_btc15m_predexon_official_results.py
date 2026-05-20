#!/usr/bin/env python3
"""Fill materialized Predexon BTC15M trade rows with official Kalshi results.

The Predexon research rows can carry proxy labels derived from exchange spot.
For closed Kalshi markets, the public market metadata endpoint exposes the
official result and expiration value. This script rewrites label/PnL columns
for already-materialized candidate trades so downstream screens can use the
same settlement source as the live REST-official replay.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_TRADES = (
    BACKTEST_ROOT
    / "btc15m_f2_combined_predexon_plus_jan29_20260516_164544"
    / "combined_predexon_trades.parquet"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_predexon_rest_official_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_METADATA_DIR = PROJECT_ROOT / "data" / "predexon_kalshi_orderbooks" / "market_metadata"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fill materialized Predexon BTC15M trades with official Kalshi results.")
    p.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--metadata-dir", type=Path, default=DEFAULT_METADATA_DIR)
    p.add_argument("--sleep", type=float, default=0.02)
    p.add_argument("--skip-rest", action="store_true", help="Reuse an existing official_result_rest column instead of calling Kalshi REST.")
    return p.parse_args()


def get_market(session: requests.Session, ticker: str) -> dict[str, Any]:
    url = f"{BASE_URL}/markets/{ticker}"
    for attempt in range(6):
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(min(10.0, 2.0**attempt))
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(10.0, 2.0**attempt)
            time.sleep(delay)
            continue
        if response.status_code == 404:
            return {"_fetch_error": "404_not_found"}
        response.raise_for_status()
        return response.json().get("market") or {}
    raise RuntimeError(f"failed to fetch market {ticker}")


def add_result_pnl(trades: pd.DataFrame, result_col: str, prefix: str) -> pd.DataFrame:
    out = trades.copy()
    result = out[result_col].astype(str).str.lower()
    side = out["side"].astype(str).str.lower()
    valid = result.isin(["yes", "no"])
    win = valid & result.eq(side)
    entry = pd.to_numeric(out.get("entry_stress", out["entry_price"]), errors="coerce")
    if "fee_stress" in out:
        fee = pd.to_numeric(out["fee_stress"], errors="coerce")
    elif "entry_fee" in out:
        fee = pd.to_numeric(out["entry_fee"], errors="coerce")
    else:
        fee = pd.Series(0.0, index=out.index)
    premium = entry + fee
    out[f"win_pnl_{prefix}_2c"] = np.where(valid, win, np.nan)
    out[f"premium_{prefix}_2c"] = np.where(valid, premium, np.nan)
    out[f"pnl_{prefix}_2c"] = np.where(valid & win, 1.0 - entry - fee, np.where(valid, -premium, np.nan))
    return out


def add_official_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    return add_result_pnl(trades, "official_result_rest", "official_rest")


def load_metadata_result_map(metadata_dir: Path) -> dict[str, str]:
    if not metadata_dir.exists():
        return {}
    frames: list[pd.DataFrame] = []
    for path in sorted(metadata_dir.glob("*.parquet"), key=lambda p: p.stat().st_mtime):
        try:
            df = pd.read_parquet(path, columns=["market_ticker", "result"])
        except Exception:
            continue
        frames.append(df)
    if not frames:
        return {}
    meta = pd.concat(frames, ignore_index=True)
    meta["market_ticker"] = meta["market_ticker"].astype(str).str.upper()
    meta["result"] = meta["result"].astype(str).str.lower()
    meta = meta[meta["result"].isin(["yes", "no"])].copy()
    meta = meta.drop_duplicates("market_ticker", keep="last")
    return dict(zip(meta["market_ticker"], meta["result"]))


def max_drawdown(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * (len(x) ** 0.5))


def summarize(group: pd.DataFrame, pnl_col: str, premium_col: str | None = None, win_col: str | None = None) -> dict[str, Any]:
    pnl = pd.to_numeric(group[pnl_col], errors="coerce").dropna()
    work = group.loc[pnl.index].copy()
    premium_source = premium_col if premium_col and premium_col in work else "premium_stress"
    win_source = win_col if win_col and win_col in work else "win"
    premium = pd.to_numeric(work.get(premium_source, pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    wins = pd.to_numeric(work.get(win_source, pd.Series(dtype=float)), errors="coerce")
    total_pnl = float(pnl.sum()) if len(pnl) else 0.0
    total_premium = float(premium.sum()) if len(premium) else 0.0
    return {
        "trades": int(len(work)),
        "pnl": total_pnl,
        "premium": total_premium,
        "rop": float(total_pnl / total_premium) if total_premium > 0 else 0.0,
        "win_rate": float(wins.mean()) if len(wins) else 0.0,
        "max_dd": max_drawdown(pnl.reset_index(drop=True)),
        "sharpe": sharpe(pnl),
    }


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trades = pd.read_parquet(args.trades).copy()
    tickers = sorted(set(trades["market_ticker"].astype(str).str.upper()))
    session = requests.Session()
    rows: list[dict[str, Any]] = []
    result_by_ticker: dict[str, str] = {}
    if args.skip_rest:
        if "official_result_rest" not in trades:
            raise ValueError("--skip-rest requires input trades with official_result_rest")
        source = trades[["market_ticker", "official_result_rest"]].copy()
        source["market_ticker"] = source["market_ticker"].astype(str).str.upper()
        source["official_result_rest"] = source["official_result_rest"].astype(str).str.lower()
        source = source.drop_duplicates("market_ticker", keep="last")
        result_by_ticker = dict(zip(source["market_ticker"], source["official_result_rest"]))
        rows = [{"market_ticker": ticker, "result": result_by_ticker.get(ticker, ""), "fetch_error": "skip_rest"} for ticker in tickers]
    else:
        for i, ticker in enumerate(tickers, start=1):
            if i == 1 or i % 100 == 0:
                print(f"fetching market result {i}/{len(tickers)} {ticker}", flush=True)
            market = get_market(session, ticker)
            result = str(market.get("result") or "").lower()
            result_by_ticker[ticker] = result if result in {"yes", "no"} else ""
            rows.append(
                {
                    "market_ticker": ticker,
                    "event_ticker": market.get("event_ticker"),
                    "status": market.get("status"),
                    "fetch_error": market.get("_fetch_error", ""),
                    "result": result,
                    "expiration_value": market.get("expiration_value"),
                    "floor_strike": market.get("floor_strike"),
                    "close_time": market.get("close_time"),
                    "settlement_ts": market.get("settlement_ts"),
                }
            )
            if args.sleep > 0 and i < len(tickers):
                time.sleep(args.sleep)

    trades["official_result_rest"] = trades["market_ticker"].astype(str).str.upper().map(result_by_ticker).fillna("")
    trades = add_official_pnl(trades)
    metadata_result_by_ticker = load_metadata_result_map(args.metadata_dir)
    trades["predexon_metadata_result"] = trades["market_ticker"].astype(str).str.upper().map(metadata_result_by_ticker).fillna("")
    trades = add_result_pnl(trades, "predexon_metadata_result", "predexon_metadata")
    market_results = pd.DataFrame(rows)

    strategy_rows = []
    for strategy, group in trades.groupby("strategy", dropna=False):
        valid = group[group["official_result_rest"].astype(str).str.lower().isin(["yes", "no"])].copy()
        metadata_valid = group[group["predexon_metadata_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
        row = {"strategy": strategy, "rows": int(len(group)), "official_filled": int(len(valid)), "metadata_filled": int(len(metadata_valid))}
        row.update({f"official_{k}": v for k, v in summarize(valid, "pnl_official_rest_2c", "premium_official_rest_2c", "win_pnl_official_rest_2c").items()})
        row.update({f"metadata_{k}": v for k, v in summarize(metadata_valid, "pnl_predexon_metadata_2c", "premium_predexon_metadata_2c", "win_pnl_predexon_metadata_2c").items()})
        row.update({f"proxy_{k}": v for k, v in summarize(group, "pnl_stress", "premium_stress", "win").items()})
        strategy_rows.append(row)
    summary = pd.DataFrame(strategy_rows).sort_values("strategy")

    window_rows = []
    for (strategy, window), group in trades.groupby(["strategy", "window"], dropna=False):
        valid = group[group["official_result_rest"].astype(str).str.lower().isin(["yes", "no"])].copy()
        metadata_valid = group[group["predexon_metadata_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
        row = {"strategy": strategy, "window": window, "rows": int(len(group)), "official_filled": int(len(valid)), "metadata_filled": int(len(metadata_valid))}
        row.update({f"official_{k}": v for k, v in summarize(valid, "pnl_official_rest_2c", "premium_official_rest_2c", "win_pnl_official_rest_2c").items()})
        row.update({f"metadata_{k}": v for k, v in summarize(metadata_valid, "pnl_predexon_metadata_2c", "premium_predexon_metadata_2c", "win_pnl_predexon_metadata_2c").items()})
        row.update({f"proxy_{k}": v for k, v in summarize(group, "pnl_stress", "premium_stress", "win").items()})
        window_rows.append(row)
    by_window = pd.DataFrame(window_rows).sort_values(["strategy", "window"])

    trades.to_parquet(args.out_dir / "predexon_trades_rest_official.parquet", index=False, compression="zstd")
    trades.to_csv(args.out_dir / "predexon_trades_rest_official.csv", index=False)
    market_results.to_csv(args.out_dir / "market_results.csv", index=False)
    summary.to_csv(args.out_dir / "summary_by_strategy.csv", index=False)
    by_window.to_csv(args.out_dir / "summary_by_strategy_window.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_trades": str(args.trades),
        "trade_rows": int(len(trades)),
        "unique_tickers": int(len(tickers)),
        "rest_results": int(sum(1 for value in result_by_ticker.values() if value in {"yes", "no"})),
        "metadata_results": int(sum(1 for value in metadata_result_by_ticker.values() if value in {"yes", "no"})),
        "metadata_dir": str(args.metadata_dir),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M Predexon REST Official Fill",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Input trades: `{args.trades}`",
        "",
        "## Summary By Strategy",
        "",
        summary.round(4).to_string(index=False),
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.round(4).to_string(index=False))
    print(json.dumps(info, indent=2, sort_keys=True))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
