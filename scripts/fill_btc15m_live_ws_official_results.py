#!/usr/bin/env python3
"""Fill official Kalshi results for BTC15M live-replay trades.

The websocket capture only contains `determined` lifecycle messages observed
while the collector was connected.  For already-closed markets, Kalshi's public
market metadata endpoint exposes finalized `result`, which gives a stronger
official-settlement audit than relying only on captured lifecycle messages.

This script does not search or backtest new signals.  It takes an existing
live replay trade file, fetches official results for its market tickers, and
rewrites official PnL columns.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_f2_live_ws_holdout import add_pnl, metrics  # noqa: E402


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_TRADES = PROJECT_ROOT / "backtest_outputs" / "btc15m_f2_deployment_gate_latest_20260516_154453" / "live_ws_trades.parquet"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_live_ws_rest_official_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill BTC15M live replay trades with REST official Kalshi results.")
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--candidate-name", default="f2_live_ws", help="Candidate label to use when the trade file lacks a candidate column.")
    return parser.parse_args()


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
        response.raise_for_status()
        return response.json().get("market") or {}
    raise RuntimeError(f"failed to fetch market {ticker}")


def empty_metrics(prefix: str) -> dict[str, float | int]:
    return {
        f"{prefix}_trades": 0,
        f"{prefix}_pnl": 0.0,
        f"{prefix}_return_on_100": 0.0,
        f"{prefix}_premium": 0.0,
        f"{prefix}_rop": 0.0,
        f"{prefix}_win_rate": 0.0,
        f"{prefix}_max_dd": 0.0,
        f"{prefix}_sharpe": 0.0,
    }


def summarize(df: pd.DataFrame, default_candidate: str = "") -> pd.DataFrame:
    if df.empty:
        row: dict[str, Any] = {"candidate": default_candidate, "rows": 0, "official_filled": 0}
        row.update(empty_metrics("official"))
        row.update(empty_metrics("proxy"))
        row["official_proxy_both_results"] = 0
        row["official_proxy_result_mismatches"] = 0
        row["official_minus_proxy_pnl_2c"] = 0.0
        return pd.DataFrame([row])
    rows: list[dict[str, Any]] = []
    for candidate, group in df.groupby("candidate", dropna=False):
        official = group[group["official_result_filled"].astype(str).str.lower().isin(["yes", "no"])].copy()
        row = {"candidate": candidate, "rows": int(len(group)), "official_filled": int(len(official))}
        row.update({f"official_{k}": v for k, v in metrics(official, "pnl_official_rest_2c").items()})
        row.update({f"proxy_{k}": v for k, v in metrics(group, "pnl_proxy_2c").items()})
        if not official.empty and "proxy_result" in official.columns:
            proxy_valid = official["proxy_result"].astype(str).str.lower().isin(["yes", "no"])
            both = official.loc[proxy_valid].copy()
            if not both.empty:
                mismatch = ~both["official_result_filled"].astype(str).str.lower().eq(
                    both["proxy_result"].astype(str).str.lower()
                )
                row["official_proxy_both_results"] = int(len(both))
                row["official_proxy_result_mismatches"] = int(mismatch.sum())
                row["official_minus_proxy_pnl_2c"] = round(
                    float(
                        pd.to_numeric(both["pnl_official_rest_2c"], errors="coerce").fillna(0.0).sum()
                        - pd.to_numeric(both["pnl_proxy_2c"], errors="coerce").fillna(0.0).sum()
                    ),
                    4,
                )
            else:
                row["official_proxy_both_results"] = 0
                row["official_proxy_result_mismatches"] = 0
                row["official_minus_proxy_pnl_2c"] = 0.0
        rows.append(row)
    return pd.DataFrame(rows).sort_values("candidate")


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trades = pd.read_parquet(args.trades).copy()
    if "candidate" not in trades.columns:
        trades["candidate"] = args.candidate_name
    tickers = sorted(set(trades["market_ticker"].astype(str).str.upper()))
    session = requests.Session()
    rows: list[dict[str, Any]] = []
    result_by_ticker: dict[str, str] = {}
    status_by_ticker: dict[str, str] = {}
    for i, ticker in enumerate(tickers, start=1):
        market = get_market(session, ticker)
        result = str(market.get("result") or "").lower()
        status = str(market.get("status") or "").lower()
        result_by_ticker[ticker] = result if result in {"yes", "no"} else ""
        status_by_ticker[ticker] = status
        rows.append(
            {
                "market_ticker": ticker,
                "event_ticker": market.get("event_ticker"),
                "status": status,
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
    trades["market_status_rest"] = trades["market_ticker"].astype(str).str.upper().map(status_by_ticker).fillna("")
    existing = trades["official_result"].astype(str).str.lower()
    rest = trades["official_result_rest"].astype(str).str.lower()
    trades["official_result_filled"] = existing.where(existing.isin(["yes", "no"]), rest)
    trades = add_pnl(trades, "official_result_filled", "pnl_official_rest_0c", 0)
    trades = add_pnl(trades, "official_result_filled", "pnl_official_rest_2c", 2)

    market_results = pd.DataFrame(rows)
    summary = summarize(trades, args.candidate_name)
    mismatched_existing = trades[
        existing.isin(["yes", "no"])
        & rest.isin(["yes", "no"])
        & ~existing.eq(rest)
    ].copy()
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_trades": str(args.trades),
        "trade_rows": int(len(trades)),
        "unique_tickers": int(len(tickers)),
        "rest_results": int(sum(1 for v in result_by_ticker.values() if v in {"yes", "no"})),
        "existing_rest_mismatches": int(len(mismatched_existing)),
    }
    trades.to_parquet(args.out_dir / "live_ws_trades_rest_official.parquet", index=False, compression="zstd")
    trades.to_csv(args.out_dir / "live_ws_trades_rest_official.csv", index=False)
    market_results.to_csv(args.out_dir / "market_results.csv", index=False)
    summary.to_csv(args.out_dir / "summary.csv", index=False)
    if not mismatched_existing.empty:
        mismatched_existing.to_csv(args.out_dir / "existing_rest_mismatches.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M Live Replay REST Official Fill",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Input trades: `{args.trades}`",
        "",
        "## Summary",
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
