#!/usr/bin/env python3
"""Backtest BTC15M lowdd selected sidecar signals with official settlement.

This is the policy-equivalent replay path for a running lowdd paper wrapper:
it uses the wrapper's own ``signal_scan`` rows where ``action='selected'`` and
the exact selected market, side, and entry price that the live process emitted.
Generic top-book replays remain useful diagnostics, but this report is the
right input for live wrapper parity and post-restart forward evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_lowdd_sidecar_selected_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materialized-db", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--start-utc", help="Optional inclusive selected-signal lower bound.")
    parser.add_argument("--end-utc", help="Optional exclusive selected-signal upper bound.")
    parser.add_argument("--sleep", type=float, default=0.05)
    return parser.parse_args()


def build_time_filter(start_utc: str | None, end_utc: str | None) -> tuple[str, list[str]]:
    clauses = ["action = 'selected'"]
    params: list[str] = []
    if start_utc:
        clauses.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= TRY_CAST(? AS TIMESTAMPTZ)")
        params.append(start_utc)
    if end_utc:
        clauses.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) < TRY_CAST(? AS TIMESTAMPTZ)")
        params.append(end_utc)
    return " AND ".join(clauses), params


def load_selected_and_orders(path: Path, start_utc: str | None, end_utc: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        where_sql, params = build_time_filter(start_utc, end_utc)
        selected = con.execute(
            f"""
            SELECT TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS selected_at_utc,
                   received_at_ns AS selected_received_at_ns,
                   event_ticker,
                   selected_market AS market_ticker,
                   selected_side AS side,
                   entry_price,
                   net_edge_cents,
                   model_p_yes,
                   detail
            FROM signal_scan
            WHERE {where_sql}
            ORDER BY TRY_CAST(received_at_utc AS TIMESTAMPTZ), received_at_ns
            """,
            params,
        ).fetchdf()
        orders = con.execute(
            """
            SELECT TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS order_at_utc,
                   received_at_ns AS order_received_at_ns,
                   event_ticker,
                   market_ticker,
                   side,
                   contracts,
                   entry_price AS order_entry_price,
                   estimated_cost,
                   portfolio_available,
                   portfolio_value,
                   action,
                   detail AS order_detail
            FROM order_decision
            WHERE action = 'paper_fill'
            ORDER BY TRY_CAST(received_at_utc AS TIMESTAMPTZ), received_at_ns
            """
        ).fetchdf()
    finally:
        con.close()
    for df in (selected, orders):
        for col in ["selected_at_utc", "order_at_utc"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        for col in [
            "selected_received_at_ns",
            "order_received_at_ns",
            "entry_price",
            "net_edge_cents",
            "model_p_yes",
            "contracts",
            "order_entry_price",
            "estimated_cost",
            "portfolio_available",
            "portfolio_value",
        ]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ["event_ticker", "market_ticker", "side"]:
            if col in df.columns:
                df[col] = df[col].astype(str)
    return selected, orders


def fetch_market(session: requests.Session, ticker: str) -> dict[str, Any]:
    url = f"{BASE_URL}/markets/{ticker}"
    for attempt in range(6):
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException as exc:
            if attempt == 5:
                return {"ticker": ticker, "fetch_error": repr(exc)}
            time.sleep(min(10.0, 2.0**attempt))
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(10.0, 2.0**attempt)
            time.sleep(delay)
            continue
        if response.status_code == 404:
            return {"ticker": ticker, "fetch_error": "404_not_found"}
        try:
            response.raise_for_status()
            return response.json().get("market") or {}
        except Exception as exc:
            return {"ticker": ticker, "fetch_error": repr(exc)}
    return {"ticker": ticker, "fetch_error": "unknown_fetch_failure"}


def one_contract_pnl(side: str, entry_price: float, result: str) -> tuple[float | None, bool | None, float]:
    side = str(side or "").lower()
    result = str(result or "").lower()
    if side not in {"yes", "no"} or result not in {"yes", "no"}:
        return None, None, 0.0
    fee = kalshi_fee_dollars(float(entry_price), contracts=1, liquidity="taker")
    premium = float(entry_price) + fee
    win = side == result
    pnl = 1.0 - float(entry_price) - fee if win else -premium
    return pnl, win, premium


def order_scaled_pnl(side: str, contracts: float | None, estimated_cost: float | None, result: str) -> tuple[float | None, bool | None, float]:
    side = str(side or "").lower()
    result = str(result or "").lower()
    if side not in {"yes", "no"} or result not in {"yes", "no"}:
        return None, None, 0.0
    if contracts is None or pd.isna(contracts) or estimated_cost is None or pd.isna(estimated_cost):
        return None, None, 0.0
    contracts_f = float(contracts)
    cost_f = float(estimated_cost)
    win = side == result
    pnl = contracts_f - cost_f if win else -cost_f
    return pnl, win, cost_f


def attach_nearest_order(row: pd.Series, orders: pd.DataFrame) -> pd.Series | None:
    if orders.empty:
        return None
    mask = (
        orders["event_ticker"].eq(str(row["event_ticker"]))
        & orders["market_ticker"].eq(str(row["market_ticker"]))
        & orders["side"].str.lower().eq(str(row["side"]).lower())
    )
    subset = orders[mask].copy()
    if subset.empty:
        return None
    subset["_abs_dt_sec"] = (subset["order_at_utc"] - row["selected_at_utc"]).dt.total_seconds().abs()
    return subset.sort_values(["_abs_dt_sec", "order_at_utc"]).iloc[0]


def build_rows(selected: pd.DataFrame, orders: pd.DataFrame, sleep_sec: float) -> list[dict[str, Any]]:
    session = requests.Session()
    cache: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for _, signal in selected.iterrows():
        ticker = str(signal["market_ticker"]).upper()
        if ticker not in cache:
            cache[ticker] = fetch_market(session, ticker)
            if sleep_sec > 0:
                time.sleep(sleep_sec)
        market = cache[ticker]
        result = str(market.get("result") or "").lower()
        signal_pnl, signal_win, signal_premium = one_contract_pnl(str(signal["side"]), float(signal["entry_price"]), result)
        order = attach_nearest_order(signal, orders)
        order_pnl, order_win, order_premium = order_scaled_pnl(
            str(signal["side"]),
            None if order is None else order.get("contracts"),
            None if order is None else order.get("estimated_cost"),
            result,
        )
        order_entry = None if order is None else order.get("order_entry_price")
        rows.append(
            {
                "selected_at_utc": signal.get("selected_at_utc"),
                "event_ticker": signal.get("event_ticker"),
                "market_ticker": signal.get("market_ticker"),
                "side": str(signal.get("side")).lower(),
                "signal_entry_price": signal.get("entry_price"),
                "net_edge_cents": signal.get("net_edge_cents"),
                "model_p_yes": signal.get("model_p_yes"),
                "selected_detail": signal.get("detail"),
                "official_status": market.get("status"),
                "official_result": result,
                "expiration_value": market.get("expiration_value"),
                "signal_one_contract_win": signal_win,
                "signal_one_contract_pnl": signal_pnl,
                "signal_one_contract_premium": signal_premium,
                "order_at_utc": None if order is None else order.get("order_at_utc"),
                "order_contracts": None if order is None else order.get("contracts"),
                "order_entry_price": order_entry,
                "order_estimated_cost": None if order is None else order.get("estimated_cost"),
                "order_price_diff": None if order is None or pd.isna(order_entry) else float(order_entry) - float(signal["entry_price"]),
                "order_win": order_win,
                "order_scaled_pnl": order_pnl,
                "order_scaled_premium": order_premium,
                "fetch_error": market.get("fetch_error", ""),
            }
        )
    return rows


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("official_result") in {"yes", "no"}]
    signal_pnls = [float(row["signal_one_contract_pnl"]) for row in settled if row.get("signal_one_contract_pnl") is not None]
    signal_premiums = [float(row["signal_one_contract_premium"]) for row in settled if row.get("signal_one_contract_premium") is not None]
    order_rows = [row for row in settled if row.get("order_scaled_pnl") is not None]
    order_pnls = [float(row["order_scaled_pnl"]) for row in order_rows]
    order_premiums = [float(row["order_scaled_premium"]) for row in order_rows]
    return {
        "selected_rows": len(rows),
        "settled_rows": len(settled),
        "signal_one_contract_pnl": round(sum(signal_pnls), 6),
        "signal_one_contract_premium": round(sum(signal_premiums), 6),
        "signal_one_contract_return_on_premium": round(sum(signal_pnls) / sum(signal_premiums), 6) if sum(signal_premiums) else 0.0,
        "signal_win_rate": round(sum(1 for row in settled if row.get("signal_one_contract_win") is True) / len(settled), 6) if settled else 0.0,
        "signal_max_drawdown": round(max_drawdown(signal_pnls), 6),
        "order_rows": len(order_rows),
        "order_scaled_pnl": round(sum(order_pnls), 6),
        "order_scaled_premium": round(sum(order_premiums), 6),
        "order_scaled_return_on_premium": round(sum(order_pnls) / sum(order_premiums), 6) if sum(order_premiums) else 0.0,
        "order_win_rate": round(sum(1 for row in order_rows if row.get("order_win") is True) / len(order_rows), 6) if order_rows else 0.0,
        "order_max_drawdown": round(max_drawdown(order_pnls), 6),
        "order_price_mismatch_rows": sum(1 for row in rows if row.get("order_price_diff") is not None and abs(float(row["order_price_diff"])) > 1e-9),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


ROW_COLUMNS = [
    "selected_at_utc",
    "event_ticker",
    "market_ticker",
    "side",
    "signal_entry_price",
    "net_edge_cents",
    "model_p_yes",
    "official_status",
    "official_result",
    "expiration_value",
    "signal_one_contract_win",
    "signal_one_contract_pnl",
    "signal_one_contract_premium",
    "order_at_utc",
    "order_contracts",
    "order_entry_price",
    "order_estimated_cost",
    "order_price_diff",
    "order_win",
    "order_scaled_pnl",
    "order_scaled_premium",
    "fetch_error",
    "selected_detail",
]


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    selected, orders = load_selected_and_orders(args.materialized_db, args.start_utc, args.end_utc)
    rows = build_rows(selected, orders, args.sleep)
    summary = summarize(rows)
    write_csv(args.out_dir / "selected_signal_trades.csv", rows, ROW_COLUMNS)
    write_csv(args.out_dir / "selected_signal_summary.csv", [summary], list(summary.keys()))
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "materialized_db": str(args.materialized_db),
        "start_utc": args.start_utc or "",
        "end_utc": args.end_utc or "",
        "notes": [
            "Rows come from signal_scan action='selected', not from generic top-book rescoring.",
            "signal_one_contract_* fields use one-contract taker-fee PnL.",
            "order_scaled_* fields use order_decision estimated_cost for paper wrapper size.",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report_df = pd.DataFrame(rows)
    report_cols = [
        "selected_at_utc",
        "market_ticker",
        "side",
        "signal_entry_price",
        "official_result",
        "signal_one_contract_pnl",
        "order_contracts",
        "order_scaled_pnl",
        "order_price_diff",
    ]
    report = [
        "# BTC15M Lowdd Sidecar-Selected Official Replay",
        "",
        f"Generated: `{manifest['created_at_utc']}`",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(summary, indent=2, default=str),
        "```",
        "",
        "## Rows",
        "",
        markdown_table(report_df[[c for c in report_cols if c in report_df.columns]]) if not report_df.empty else "_empty_",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "summary": summary}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
