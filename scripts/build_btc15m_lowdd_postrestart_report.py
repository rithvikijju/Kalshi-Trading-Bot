#!/usr/bin/env python3
"""Build a post-restart evidence report for the BTC15M lowdd paper shadow.

The report is deliberately narrow: only rows written after a known clean
restart count.  It reads the paper ledger, fetches official Kalshi settlement
for filled rows, optionally audits the replay sidecar for selected/order rows,
and emits CSV/JSON/Markdown artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_SINCE_UTC = "2026-05-30T06:33:25+00:00"
DEFAULT_TRADE_DB = Path.home() / ".btc_kalshi_bot" / "btc15m_lowdd_forward_shadow_trades.db"
DEFAULT_SIDECAR = Path.home() / ".btc_kalshi_bot" / "btc15m_lowdd_forward_shadow_capture.duckdb.replay.jsonl"
DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_lowdd_postrestart_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


TRADE_COLUMNS = [
    "id",
    "created_at",
    "event_ticker",
    "market_ticker",
    "side",
    "contracts",
    "entry_price",
    "fee",
    "status",
    "close_time",
    "client_order_id",
    "quote_age_ms",
    "top_visible_qty",
    "spread_cents",
    "model_p_yes",
    "net_edge_cents",
    "official_status",
    "official_result",
    "expiration_value",
    "official_win",
    "official_pnl",
    "official_premium",
    "fetch_error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-db", type=Path, default=DEFAULT_TRADE_DB)
    parser.add_argument("--sidecar", type=Path, default=DEFAULT_SIDECAR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--end-utc", default="", help="Optional exclusive UTC upper bound for paper rows.")
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--skip-sidecar", action="store_true")
    return parser.parse_args()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def load_postrestart_trades(path: Path, since_utc: str, end_utc: str = "") -> list[dict[str, Any]]:
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_live_trades'"
        ).fetchone()
        if exists is None:
            return []
        cols = table_columns(con, "research_live_trades")
        time_col = next((col for col in ["created_at_utc", "created_at", "entry_ts_utc"] if col in cols), None)
        if time_col is None:
            return [dict(row) for row in con.execute("SELECT * FROM research_live_trades ORDER BY id").fetchall()]
        clauses = [f"{time_col} >= ?"]
        params = [since_utc]
        if end_utc:
            clauses.append(f"{time_col} < ?")
            params.append(end_utc)
        where_sql = " AND ".join(clauses)
        return [
            dict(row)
            for row in con.execute(
                f"SELECT * FROM research_live_trades WHERE {where_sql} ORDER BY {time_col}, id",
                params,
            ).fetchall()
        ]
    finally:
        con.close()


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


def official_pnl_total_fee(trade: dict[str, Any], result: str) -> tuple[float | None, bool | None, float]:
    side = str(trade.get("side") or "").lower()
    result = str(result or "").lower()
    if side not in {"yes", "no"} or result not in {"yes", "no"}:
        return None, None, 0.0
    contracts = as_float(trade.get("contracts"), 0.0)
    entry = as_float(trade.get("actual_entry_price"), as_float(trade.get("entry_price"), 0.0))
    fee = as_float(
        trade.get("actual_fee_paid"),
        as_float(trade.get("entry_fee_estimate"), 0.0),
    )
    premium = contracts * entry + fee
    win = side == result
    pnl = contracts * (1.0 - entry) - fee if win else -premium
    return pnl, win, premium


def normalize_trade(trade: dict[str, Any], market: dict[str, Any] | None) -> dict[str, Any]:
    result = str((market or {}).get("result") or "").lower()
    pnl, win, premium = official_pnl_total_fee(trade, result)
    return {
        "id": trade.get("id"),
        "created_at": trade.get("created_at_utc") or trade.get("created_at") or trade.get("entry_ts_utc"),
        "event_ticker": trade.get("event_ticker"),
        "market_ticker": trade.get("market_ticker"),
        "side": trade.get("side"),
        "contracts": trade.get("contracts"),
        "entry_price": trade.get("actual_entry_price") or trade.get("entry_price"),
        "fee": trade.get("actual_fee_paid") if trade.get("actual_fee_paid") is not None else trade.get("entry_fee_estimate"),
        "status": trade.get("status"),
        "close_time": trade.get("close_time"),
        "client_order_id": trade.get("client_order_id"),
        "quote_age_ms": trade.get("quote_age_ms"),
        "top_visible_qty": trade.get("top_visible_qty"),
        "spread_cents": trade.get("spread_cents"),
        "model_p_yes": trade.get("model_p_yes"),
        "net_edge_cents": trade.get("net_edge_cents"),
        "official_status": (market or {}).get("status"),
        "official_result": result,
        "expiration_value": (market or {}).get("expiration_value"),
        "official_win": win,
        "official_pnl": pnl,
        "official_premium": premium,
        "fetch_error": (market or {}).get("fetch_error", ""),
    }


def add_official_results(trades: list[dict[str, Any]], sleep_sec: float) -> list[dict[str, Any]]:
    session = requests.Session()
    cache: dict[str, dict[str, Any]] = {}
    rows = []
    for trade in trades:
        ticker = str(trade.get("market_ticker") or "").upper()
        if ticker not in cache:
            cache[ticker] = fetch_market(session, ticker)
            if sleep_sec > 0:
                time.sleep(sleep_sec)
        rows.append(normalize_trade(trade, cache[ticker]))
    return rows


def summarize_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("official_result") in {"yes", "no"}]
    pnl_values = [as_float(row.get("official_pnl"), 0.0) for row in settled]
    premium_values = [as_float(row.get("official_premium"), 0.0) for row in settled]
    wins = [row for row in settled if row.get("official_win") is True]
    return {
        "trades": len(rows),
        "settled": len(settled),
        "wins": len(wins),
        "losses": len(settled) - len(wins),
        "official_pnl": round(sum(pnl_values), 4),
        "official_premium": round(sum(premium_values), 4),
        "return_on_premium": round(sum(pnl_values) / sum(premium_values), 6) if sum(premium_values) else 0.0,
        "win_rate": round(len(wins) / len(settled), 6) if settled else 0.0,
        "max_drawdown": round(max_drawdown(pnl_values), 4),
    }


def max_drawdown(pnl_values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def sidecar_audit(path: Path, since_utc: str) -> dict[str, Any]:
    if not path.exists():
        return {"sidecar": str(path), "exists": False}
    try:
        import duckdb
    except Exception as exc:
        return {"sidecar": str(path), "exists": True, "error": f"duckdb_import_failed: {exc!r}"}
    cols = (
        "{'table':'VARCHAR','received_at_utc':'VARCHAR','received_at_ns':'UBIGINT',"
        "'action':'VARCHAR','detail':'VARCHAR','event_ticker':'VARCHAR','market_ticker':'VARCHAR',"
        "'side':'VARCHAR','contracts':'DOUBLE','entry_price':'DOUBLE','estimated_cost':'DOUBLE',"
        "'portfolio_available':'DOUBLE','portfolio_value':'DOUBLE'}"
    )
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone='UTC'")
    try:
        sidecar = str(path).replace("'", "''")
        since_sql = since_utc.replace("'", "''")
        con.execute(
            f"""
            CREATE TEMP VIEW s AS
            SELECT * FROM read_json('{sidecar}', format='newline_delimited', columns={cols},
                                    union_by_name=true, ignore_errors=true, maximum_object_size=16777216)
            WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= TRY_CAST('{since_sql}' AS TIMESTAMPTZ)
            """
        )
        table_counts = [
            dict(zip(["table", "rows", "min_ts", "max_ts"], row))
            for row in con.execute(
                'SELECT "table", COUNT(*)::BIGINT, MIN(received_at_utc), MAX(received_at_utc) FROM s GROUP BY 1 ORDER BY 1'
            ).fetchall()
        ]
        signal_actions = [
            dict(zip(["action", "rows", "min_ts", "max_ts"], row))
            for row in con.execute(
                'SELECT action, COUNT(*)::BIGINT, MIN(received_at_utc), MAX(received_at_utc) '
                'FROM s WHERE "table" = \'signal_scan\' GROUP BY 1 ORDER BY 2 DESC'
            ).fetchall()
        ]
        order_actions = [
            dict(zip(["action", "detail", "rows", "min_ts", "max_ts"], row))
            for row in con.execute(
                'SELECT action, detail, COUNT(*)::BIGINT, MIN(received_at_utc), MAX(received_at_utc) '
                'FROM s WHERE "table" = \'order_decision\' GROUP BY 1,2 ORDER BY 3 DESC'
            ).fetchall()
        ]
        latest_orders = [
            dict(
                zip(
                    [
                        "received_at_utc",
                        "action",
                        "detail",
                        "event_ticker",
                        "market_ticker",
                        "side",
                        "contracts",
                        "entry_price",
                        "estimated_cost",
                        "portfolio_available",
                        "portfolio_value",
                    ],
                    row,
                )
            )
            for row in con.execute(
                """
                SELECT received_at_utc, action, detail, event_ticker, market_ticker, side,
                       contracts, entry_price, estimated_cost, portfolio_available, portfolio_value
                FROM s
                WHERE "table" = 'order_decision'
                ORDER BY TRY_CAST(received_at_utc AS TIMESTAMPTZ) DESC, received_at_ns DESC
                LIMIT 20
                """
            ).fetchall()
        ]
        return {
            "sidecar": str(path),
            "exists": True,
            "table_counts": table_counts,
            "signal_actions": signal_actions,
            "order_actions": order_actions,
            "latest_orders": latest_orders,
        }
    finally:
        con.close()


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_empty_"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join([header, sep, *body])


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trades = load_postrestart_trades(args.trade_db, args.since_utc, args.end_utc)
    official_rows = add_official_results(trades, args.sleep)
    summary = summarize_trades(official_rows)
    sidecar = {} if args.skip_sidecar else sidecar_audit(args.sidecar, args.since_utc)
    write_csv(args.out_dir / "lowdd_postrestart_trades.csv", official_rows, TRADE_COLUMNS)
    write_csv(args.out_dir / "lowdd_postrestart_summary.csv", [summary], list(summary.keys()))
    (args.out_dir / "lowdd_postrestart_sidecar.json").write_text(json.dumps(sidecar, indent=2, default=str), encoding="utf-8")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "trade_db": str(args.trade_db),
        "sidecar": str(args.sidecar),
        "since_utc": args.since_utc,
        "end_utc": args.end_utc,
        "summary": summary,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    report = [
        "# BTC15M Lowdd Post-Restart Evidence",
        "",
        f"Generated: `{manifest['created_at_utc']}`",
        f"Since UTC: `{args.since_utc}`",
        f"End UTC: `{args.end_utc or 'unbounded'}`",
        f"Trade DB: `{args.trade_db}`",
        "",
        "## Summary",
        "",
        markdown_table([summary], list(summary.keys())),
        "",
        "## Trades",
        "",
        markdown_table(official_rows, TRADE_COLUMNS),
        "",
        "## Sidecar",
        "",
        "```json",
        json.dumps(sidecar, indent=2, default=str),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "summary": summary, "trades": len(official_rows)}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
