#!/usr/bin/env python3
"""REST-official settlement audit for BTC paper-shadow ledgers.

The shadow engines write local SQLite ledgers. This script leaves those ledgers
untouched, fetches official Kalshi market results for filled paper trades, and
recomputes hold-to-settlement PnL with the recorded entry and fee.
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
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_shadow_official_settlement_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_BTC_1M = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"


DEFAULT_LEDGERS = [
    (
        "btc15m_q250_qty500_firstskip_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_shadow"
        / "btc15m_f2_q250_qty500_firstskip_shadow_trades.db",
    ),
    (
        "btc15m_q250_qty500_firstskip_yes_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow"
        / "btc15m_f2_q250_qty500_firstskip_yes_shadow_trades.db",
    ),
    (
        "btc15m_q1000_yes_shadow",
        PROJECT_ROOT
        / ".codex_work"
        / "btc15m_f2_q1000_yes_shadow"
        / "btc15m_f2_q1000_yes_shadow_trades.db",
    ),
    (
        "btc1h_high_conf80_entry70_no_chase_shadow",
        Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_entry70_no_chase_shadow.db",
    ),
]

SHADOW_OFFICIAL_TRADE_COLUMNS = [
    "ledger",
    "created_at",
    "status",
    "signal_strategy",
    "model_ttl_policy",
    "model_policy_version",
    "event_ticker",
    "market_ticker",
    "side",
    "contracts",
    "entry_price",
    "fee",
    "model_p_yes",
    "net_edge_cents",
    "spread_cents",
    "entry_btc_spot",
    "quote_age_ms",
    "top_visible_qty",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "official_status",
    "official_result",
    "expiration_value",
    "floor_strike",
    "ticker_strike",
    "settlement_ts",
    "fetch_error",
    "official_win",
    "official_premium",
    "official_pnl",
    "proxy_close_spot",
    "proxy_result",
    "proxy_win",
    "proxy_pnl",
    "official_minus_proxy_spot",
    "official_minus_proxy_bps",
    "entry_spot_minus_strike",
    "entry_spot_distance_bps",
    "proxy_close_minus_strike",
    "proxy_close_distance_bps",
    "official_expiration_minus_strike",
    "official_expiration_distance_bps",
    "official_proxy_result_mismatch",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check shadow ledgers against Kalshi REST official settlement.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--since-utc", default="", help="Optional ISO timestamp for post-freeze counts.")
    p.add_argument("--btc-1m", type=Path, default=DEFAULT_BTC_1M, help="Optional BTC 1m cache for proxy settlement comparison.")
    p.add_argument("--sleep", type=float, default=0.05)
    return p.parse_args()


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def load_trades(name: str, path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_live_trades'"
        ).fetchone()
        if exists is None:
            return []
        cols = table_columns(con, "research_live_trades")
        rows = con.execute("SELECT * FROM research_live_trades ORDER BY created_at").fetchall()
        out = []
        for row in rows:
            item = dict(zip(cols, row))
            item["ledger"] = name
            item["ledger_path"] = str(path)
            out.append(item)
        return out
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def market_strike_from_ticker(ticker: str) -> float | None:
    try:
        return float(str(ticker).rsplit("-T", 1)[1])
    except Exception:
        return None


def load_btc_1m(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    if "time" not in df or "close" not in df:
        return pd.DataFrame()
    out = df[["time", "close"]].copy()
    out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    return out


def btc_close_at_or_before(btc_1m: pd.DataFrame, ts: str) -> float | None:
    if btc_1m.empty:
        return None
    lookup = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(lookup):
        return None
    times = btc_1m["time"].dt.tz_localize(None).to_numpy()
    idx = int(pd.Series(times).searchsorted(lookup.tz_localize(None).to_datetime64(), side="right")) - 1
    if idx < 0 or idx >= len(btc_1m):
        return None
    value = as_float(btc_1m.iloc[idx]["close"], default=float("nan"))
    return value if value == value and value > 0 else None


def trade_pnl(trade: dict[str, Any], result: str) -> tuple[float | None, bool | None, float, float]:
    side = str(trade.get("side", "")).lower()
    if result not in {"yes", "no"} or side not in {"yes", "no"}:
        return None, None, 0.0, 0.0
    entry = as_float(trade.get("actual_entry_price"), as_float(trade.get("entry_price")))
    fee = as_float(trade.get("actual_fee_paid"), as_float(trade.get("entry_fee_estimate")))
    contracts = as_float(trade.get("contracts"), 1.0)
    premium = contracts * (entry + fee)
    win = side == result
    pnl = contracts * (1.0 - entry - fee) if win else -premium
    return pnl, win, premium, contracts


def official_pnl(trade: dict[str, Any], result: str) -> tuple[float | None, bool | None, float, float]:
    return trade_pnl(trade, result)


def proxy_result_and_pnl(trade: dict[str, Any], btc_1m: pd.DataFrame) -> tuple[str, float | None, bool | None, float | None]:
    strike = market_strike_from_ticker(str(trade.get("market_ticker", "")))
    close_time = str(trade.get("close_time", ""))
    close_spot = btc_close_at_or_before(btc_1m, close_time)
    if strike is None or close_spot is None:
        return "", None, None, close_spot
    result = "yes" if close_spot >= strike else "no"
    pnl, win, _, _ = trade_pnl(trade, result)
    return result, pnl, win, close_spot


def safe_subtract(left: Any, right: Any) -> float | None:
    left_num = as_float(left, default=float("nan"))
    right_num = as_float(right, default=float("nan"))
    if left_num != left_num or right_num != right_num:
        return None
    return left_num - right_num


def bps(numerator: float | None, denominator: Any) -> float | None:
    denom = as_float(denominator, default=float("nan"))
    if numerator is None or denom != denom or denom == 0:
        return None
    return 10_000.0 * numerator / denom


def numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        out.append(as_float(value))
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], default_fieldnames: list[str] | None = None) -> None:
    fieldnames: list[str] = list(default_fieldnames or [])
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]], since_utc: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ledgers = sorted(set(row["ledger"] for row in rows) | {name for name, _ in DEFAULT_LEDGERS})
    for ledger in ledgers:
        group = [row for row in rows if row.get("ledger") == ledger]
        filled = [row for row in group if str(row.get("status", "")).lower() == "paper_filled"]
        official = [row for row in filled if row.get("official_result") in {"yes", "no"}]
        since = [row for row in official if not since_utc or str(row.get("created_at", "")) >= since_utc]
        for label, subset in [("all", official), ("since", since)]:
            pnl_values = numeric_values(subset, "official_pnl")
            premium_values = numeric_values(subset, "official_premium")
            proxy_pnl_values = numeric_values(subset, "proxy_pnl")
            basis_values = numeric_values(subset, "official_minus_proxy_spot")
            wins = [1.0 if str(row.get("official_win")).lower() == "true" else 0.0 for row in subset]
            total_pnl = sum(pnl_values)
            total_premium = sum(premium_values)
            proxy_pnl = sum(proxy_pnl_values)
            proxy_mismatches = sum(
                1
                for row in subset
                if row.get("proxy_result") in {"yes", "no"}
                and row.get("official_result") in {"yes", "no"}
                and row.get("proxy_result") != row.get("official_result")
            )
            out.append(
                {
                    "ledger": ledger,
                    "scope": label,
                    "paper_filled_rows": len(filled) if label == "all" else len([row for row in filled if not since_utc or str(row.get("created_at", "")) >= since_utc]),
                    "official_filled_rows": len(subset),
                    "official_pnl": total_pnl,
                    "official_premium": total_premium,
                    "official_rop": total_pnl / total_premium if total_premium > 0 else 0.0,
                    "official_win_rate": sum(wins) / len(wins) if wins else 0.0,
                    "proxy_filled_rows": len(proxy_pnl_values),
                    "proxy_pnl": proxy_pnl,
                    "official_minus_proxy_pnl": total_pnl - proxy_pnl if proxy_pnl_values else "",
                    "official_proxy_result_mismatches": proxy_mismatches,
                    "mean_official_minus_proxy_spot": sum(basis_values) / len(basis_values) if basis_values else "",
                    "max_abs_official_minus_proxy_spot": max(abs(x) for x in basis_values) if basis_values else "",
                }
            )
    return out


def summarize_by_policy(rows: list[dict[str, Any]], since_utc: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    def policy_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("ledger") or ""),
            str(row.get("signal_strategy") or ""),
            str(row.get("model_ttl_policy") or ""),
            str(row.get("model_policy_version") or ""),
        )

    keys = sorted(
        {policy_key(row) for row in rows}
    )
    for ledger, signal_strategy, model_ttl_policy, model_policy_version in keys:
        group = [
            row
            for row in rows
            if policy_key(row) == (ledger, signal_strategy, model_ttl_policy, model_policy_version)
        ]
        filled = [row for row in group if str(row.get("status", "")).lower() == "paper_filled"]
        official = [row for row in filled if row.get("official_result") in {"yes", "no"}]
        since = [row for row in official if not since_utc or str(row.get("created_at", "")) >= since_utc]
        for label, subset in [("all", official), ("since", since)]:
            pnl_values = numeric_values(subset, "official_pnl")
            premium_values = numeric_values(subset, "official_premium")
            proxy_pnl_values = numeric_values(subset, "proxy_pnl")
            basis_values = numeric_values(subset, "official_minus_proxy_spot")
            wins = [1.0 if str(row.get("official_win")).lower() == "true" else 0.0 for row in subset]
            total_pnl = sum(pnl_values)
            total_premium = sum(premium_values)
            proxy_pnl = sum(proxy_pnl_values)
            proxy_mismatches = sum(
                1
                for row in subset
                if row.get("proxy_result") in {"yes", "no"}
                and row.get("official_result") in {"yes", "no"}
                and row.get("proxy_result") != row.get("official_result")
            )
            out.append(
                {
                    "ledger": ledger,
                    "scope": label,
                    "signal_strategy": signal_strategy,
                    "model_ttl_policy": model_ttl_policy,
                    "model_policy_version": model_policy_version,
                    "paper_filled_rows": len(filled)
                    if label == "all"
                    else len([row for row in filled if not since_utc or str(row.get("created_at", "")) >= since_utc]),
                    "official_filled_rows": len(subset),
                    "official_pnl": total_pnl,
                    "official_premium": total_premium,
                    "official_rop": total_pnl / total_premium if total_premium > 0 else 0.0,
                    "official_win_rate": sum(wins) / len(wins) if wins else 0.0,
                    "proxy_filled_rows": len(proxy_pnl_values),
                    "proxy_pnl": proxy_pnl,
                    "official_minus_proxy_pnl": total_pnl - proxy_pnl if proxy_pnl_values else "",
                    "official_proxy_result_mismatches": proxy_mismatches,
                    "mean_official_minus_proxy_spot": sum(basis_values) / len(basis_values) if basis_values else "",
                    "max_abs_official_minus_proxy_spot": max(abs(x) for x in basis_values) if basis_values else "",
                }
            )
    return out


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    btc_1m = load_btc_1m(args.btc_1m)
    trades: list[dict[str, Any]] = []
    for name, path in DEFAULT_LEDGERS:
        trades.extend(load_trades(name, path))

    session = requests.Session()
    market_cache: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    tickers = sorted({str(row.get("market_ticker", "")) for row in trades if row.get("market_ticker")})
    for i, ticker in enumerate(tickers):
        market_cache[ticker] = fetch_market(session, ticker)
        if args.sleep > 0 and i + 1 < len(tickers):
            time.sleep(args.sleep)
    for trade in trades:
        ticker = str(trade.get("market_ticker", ""))
        market = market_cache.get(ticker, {})
        result = str(market.get("result") or "").lower()
        pnl, win, premium, contracts = official_pnl(trade, result)
        recorded_contracts = as_float(trade.get("contracts"), 1.0)
        proxy_result, proxy_pnl_value, proxy_win, proxy_close_spot = proxy_result_and_pnl(trade, btc_1m)
        strike = market_strike_from_ticker(ticker)
        expiration_value = market.get("expiration_value", "")
        entry_spot = as_float(trade.get("btc_spot"), default=float("nan"))
        official_minus_proxy_spot = safe_subtract(expiration_value, proxy_close_spot)
        entry_spot_minus_strike = safe_subtract(entry_spot, strike)
        proxy_close_minus_strike = safe_subtract(proxy_close_spot, strike)
        official_expiration_minus_strike = safe_subtract(expiration_value, strike)
        row = {
            "ledger": trade.get("ledger", ""),
            "created_at": trade.get("created_at", ""),
            "status": trade.get("status", ""),
            "signal_strategy": trade.get("signal_strategy", ""),
            "model_ttl_policy": trade.get("model_ttl_policy", ""),
            "model_policy_version": trade.get("model_policy_version", ""),
            "event_ticker": trade.get("event_ticker", ""),
            "market_ticker": ticker,
            "side": trade.get("side", ""),
            "contracts": recorded_contracts,
            "entry_price": as_float(trade.get("actual_entry_price"), as_float(trade.get("entry_price"))),
            "fee": as_float(trade.get("actual_fee_paid"), as_float(trade.get("entry_fee_estimate"))),
            "model_p_yes": trade.get("model_p_yes", ""),
            "net_edge_cents": trade.get("net_edge_cents", ""),
            "spread_cents": trade.get("spread_cents", ""),
            "entry_btc_spot": trade.get("btc_spot", ""),
            "quote_age_ms": trade.get("quote_age_ms", ""),
            "top_visible_qty": trade.get("top_visible_qty", trade.get("available_qty", "")),
            "quote_received_at_ns": trade.get("quote_received_at_ns", ""),
            "signal_received_at_ns": trade.get("signal_received_at_ns", ""),
            "yes_bid": trade.get("yes_bid", ""),
            "yes_ask": trade.get("yes_ask", ""),
            "no_bid": trade.get("no_bid", ""),
            "no_ask": trade.get("no_ask", ""),
            "official_status": market.get("status", ""),
            "official_result": result,
            "expiration_value": expiration_value,
            "floor_strike": market.get("floor_strike", ""),
            "ticker_strike": strike,
            "settlement_ts": market.get("settlement_ts", ""),
            "fetch_error": market.get("fetch_error", ""),
            "official_win": win,
            "official_premium": premium,
            "official_pnl": pnl,
            "proxy_close_spot": proxy_close_spot,
            "proxy_result": proxy_result,
            "proxy_win": proxy_win,
            "proxy_pnl": proxy_pnl_value,
            "official_minus_proxy_spot": official_minus_proxy_spot,
            "official_minus_proxy_bps": bps(official_minus_proxy_spot, proxy_close_spot),
            "entry_spot_minus_strike": entry_spot_minus_strike,
            "entry_spot_distance_bps": bps(entry_spot_minus_strike, entry_spot),
            "proxy_close_minus_strike": proxy_close_minus_strike,
            "proxy_close_distance_bps": bps(proxy_close_minus_strike, proxy_close_spot),
            "official_expiration_minus_strike": official_expiration_minus_strike,
            "official_expiration_distance_bps": bps(official_expiration_minus_strike, expiration_value),
            "official_proxy_result_mismatch": (
                proxy_result in {"yes", "no"} and result in {"yes", "no"} and proxy_result != result
            ),
        }
        rows.append(row)

    summary = summarize(rows, args.since_utc)
    policy_summary = summarize_by_policy(rows, args.since_utc)
    write_csv(args.out_dir / "shadow_official_trades.csv", rows, SHADOW_OFFICIAL_TRADE_COLUMNS)
    write_csv(args.out_dir / "shadow_official_summary.csv", summary)
    write_csv(args.out_dir / "shadow_official_policy_summary.csv", policy_summary)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledgers": {name: str(path) for name, path in DEFAULT_LEDGERS},
        "trades": len(trades),
        "unique_tickers": len(tickers),
        "since_utc": args.since_utc,
        "btc_1m": str(args.btc_1m),
        "btc_1m_rows": int(len(btc_1m)),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC Shadow Official Settlement",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        pd.DataFrame(summary).round(4).to_string(index=False) if summary else "No shadow trades.",
        "",
        "## Policy Summary",
        "",
        pd.DataFrame(policy_summary).round(4).to_string(index=False) if policy_summary else "No shadow trades.",
        "",
        "## Recent Official Rows",
        "",
        pd.DataFrame(rows).tail(12).round(4).to_string(index=False) if rows else "No shadow trades.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
