#!/usr/bin/env python3
"""Audit live-capture backtests against actual trade ledgers.

This uses the self-captured websocket executor tables, not historical candles:

* order_decision rows are exact decisions produced by the live websocket engine.
* signal_scan rows are used for diagnostics only.
* PnL is settled with official Kalshi market results where finalized.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "live_capture_replay_20260510" / "audit"
EVENT_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
NY_TZ = ZoneInfo("America/New_York")


class OfficialResults:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.cache: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.session = requests.Session()

    def get(self, ticker: str) -> dict[str, Any]:
        ticker = str(ticker).upper()
        cached = self.cache.get(ticker)
        if cached and cached.get("result") in {"yes", "no"}:
            return cached
        try:
            response = self.session.get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}", timeout=10)
            if response.ok:
                market = response.json().get("market", {})
                result = (market.get("result") or "").lower() or None
                row = {
                    "result": result if result in {"yes", "no"} else None,
                    "status": market.get("status"),
                    "expiration_value": market.get("expiration_value"),
                    "close_time": market.get("close_time"),
                }
            else:
                row = {"result": None, "status": f"http{response.status_code}", "expiration_value": None, "close_time": None}
        except Exception as exc:
            row = {"result": None, "status": type(exc).__name__, "expiration_value": None, "close_time": None}
        row["fetched_at"] = datetime.now(timezone.utc).isoformat()
        self.cache[ticker] = row
        time.sleep(0.015)
        return row

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")


def close_from_event(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker).upper())
    if not match:
        return None
    year = 2000 + int(match.group("yy"))
    month = MONTHS.get(match.group("mon"))
    if month is None:
        return None
    return pd.Timestamp(
        year=year,
        month=month,
        day=int(match.group("day")),
        hour=int(match.group("hour")),
        minute=0,
        tz=NY_TZ,
    ).tz_convert("UTC")


def read_order_decisions(path: Path, label: str) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    tables = {row[0] for row in con.execute("show tables").fetchall()}
    if "order_decision" not in tables:
        con.close()
        return pd.DataFrame()
    frame = con.execute("select * from order_decision order by received_at_ns").fetchdf()
    con.close()
    if frame.empty:
        return frame
    frame["capture"] = label
    frame["received_at"] = pd.to_datetime(frame["received_at_utc"], utc=True, errors="coerce")
    frame["close_time"] = frame["event_ticker"].map(close_from_event)
    frame["ttl_min"] = (frame["close_time"] - frame["received_at"]).dt.total_seconds() / 60.0
    frame["strategy"] = frame["mode"].astype(str).str.replace("paper:", "", regex=False).str.replace("live", "research", regex=False)
    frame["source_mode"] = frame["mode"].astype(str)
    return frame


def read_signal_scan_counts(path: Path, label: str) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    tables = {row[0] for row in con.execute("show tables").fetchall()}
    if "signal_scan" not in tables:
        con.close()
        return pd.DataFrame()
    frame = con.execute(
        """
        select mode, action, detail, count(*) as rows
        from signal_scan
        group by mode, action, detail
        order by rows desc
        """
    ).fetchdf()
    con.close()
    frame["capture"] = label
    return frame


def settle_decisions(decisions: pd.DataFrame, official: OfficialResults) -> pd.DataFrame:
    if decisions.empty:
        return decisions
    rows = []
    for row in decisions.to_dict("records"):
        result = official.get(row["market_ticker"]).get("result")
        row["official_result"] = result
        row["settled"] = result in {"yes", "no"}
        contracts = int(row.get("contracts") or 0)
        entry_price = float(row.get("entry_price") or 0.0)
        fee_1c = kalshi_fee_dollars(entry_price, contracts=1, liquidity="taker")
        premium_1c = entry_price + fee_1c
        win = result == str(row.get("side", "")).lower()
        row["premium_1c"] = premium_1c if row["settled"] else math.nan
        row["pnl_1c"] = (1.0 if win else 0.0) - premium_1c if row["settled"] else math.nan
        estimated = row.get("estimated_cost")
        if estimated is None or (isinstance(estimated, float) and math.isnan(estimated)):
            estimated = entry_price * contracts + kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
        row["premium_actual_contracts"] = float(estimated) if row["settled"] else math.nan
        row["pnl_actual_contracts"] = (contracts * (1.0 if win else 0.0) - float(estimated)) if row["settled"] else math.nan
        rows.append(row)
    return pd.DataFrame(rows)


def drawdown(values: pd.Series) -> float:
    clean = values.dropna().astype(float)
    if clean.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), clean.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def summarize_strategy(frame: pd.DataFrame, bankroll: float, source: str, strategy: str, unit: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "source": source,
            "strategy": strategy,
            "unit": unit,
            "trades": 0,
            "contracts": 0,
            "premium": 0.0,
            "pnl": 0.0,
            "return_on_100": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "yes_trades": 0,
            "no_trades": 0,
        }
    pnl_col = "pnl_1c" if unit == "one_contract" else "pnl_actual_contracts"
    premium_col = "premium_1c" if unit == "one_contract" else "premium_actual_contracts"
    ordered = frame.sort_values(["close_time", "received_at", "market_ticker"])
    pnl = ordered[pnl_col].astype(float)
    premium = ordered[premium_col].astype(float)
    return {
        "source": source,
        "strategy": strategy,
        "unit": unit,
        "trades": int(len(ordered)),
        "contracts": int(ordered["contracts"].sum()) if unit == "actual_contracts" else int(len(ordered)),
        "premium": float(premium.sum()),
        "pnl": float(pnl.sum()),
        "return_on_100": float(pnl.sum() / bankroll) if bankroll else 0.0,
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "max_drawdown": drawdown(pnl),
        "yes_trades": int((ordered["side"].astype(str).str.lower() == "yes").sum()),
        "no_trades": int((ordered["side"].astype(str).str.lower() == "no").sum()),
        "first": str(ordered["received_at"].min()),
        "last": str(ordered["received_at"].max()),
    }


def read_sqlite_trades(path: Path, ledger: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(path)
    rows = pd.read_sql_query("select * from research_live_trades order by created_at, id", conn)
    conn.close()
    rows["ledger"] = ledger
    rows["created_at_ts"] = pd.to_datetime(rows["created_at"], utc=True, errors="coerce")
    return rows


def compare_live_capture_to_sqlite(capture_fills: pd.DataFrame, sqlite_rows: pd.DataFrame) -> dict[str, Any]:
    sqlite_status = sqlite_rows["status"].astype(str).str.lower()
    sqlite_fills = sqlite_rows[sqlite_status.isin({"filled", "partial_filled", "paper_filled"})].copy()
    if capture_fills.empty or sqlite_fills.empty:
        return {
            "capture_fills": int(len(capture_fills)),
            "sqlite_fills_overlap": int(len(sqlite_fills)),
            "matched_client_order_ids": 0,
            "capture_missing_in_sqlite": int(len(capture_fills)),
            "sqlite_missing_in_capture": int(len(sqlite_fills)),
        }
    start = capture_fills["received_at"].min() - pd.Timedelta(minutes=5)
    end = capture_fills["received_at"].max() + pd.Timedelta(minutes=5)
    sqlite_overlap = sqlite_fills[(sqlite_fills["created_at_ts"] >= start) & (sqlite_fills["created_at_ts"] <= end)].copy()
    cap_ids = set(capture_fills["client_order_id"].dropna().astype(str))
    sql_ids = set(sqlite_overlap["client_order_id"].dropna().astype(str))
    return {
        "capture_fills": int(len(capture_fills)),
        "sqlite_fills_overlap": int(len(sqlite_overlap)),
        "matched_client_order_ids": int(len(cap_ids & sql_ids)),
        "capture_missing_in_sqlite": int(len(cap_ids - sql_ids)),
        "sqlite_missing_in_capture": int(len(sql_ids - cap_ids)),
        "overlap_start": str(start),
        "overlap_end": str(end),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-capture", type=Path, required=True)
    parser.add_argument("--multi-capture", type=Path, required=True)
    parser.add_argument("--live-db", type=Path, default=Path.home() / ".btc_kalshi_bot" / "research_live_trades.db")
    parser.add_argument("--shadow-db-dir", type=Path, default=Path.home() / ".btc_kalshi_bot" / "multi_strategy_shadow")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bankroll", type=float, default=100.0)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    official = OfficialResults(args.output_dir / "official_results_cache.json")

    research_decisions = read_order_decisions(args.research_capture, "research_live_capture")
    multi_decisions = read_order_decisions(args.multi_capture, "multi_strategy_shadow_capture")
    decisions = pd.concat([research_decisions, multi_decisions], ignore_index=True)
    settled = settle_decisions(decisions, official)
    official.write()

    executable = settled[settled["action"].isin(["filled", "paper_fill"]) & settled["settled"]].copy()
    executable = executable[executable["strategy"] != "js_guarded"].copy()
    late = executable[
        (executable["strategy"] == "research")
        & (executable["ttl_min"] >= 5.0)
        & (executable["ttl_min"] <= 20.0)
    ].copy()
    late["strategy"] = "research_late_only"

    viable = pd.concat([executable, late], ignore_index=True)

    summary_rows = []
    for (source, strategy), group in viable.groupby(["capture", "strategy"], sort=True):
        for unit in ("one_contract", "actual_contracts"):
            summary_rows.append(summarize_strategy(group, args.bankroll, source, strategy, unit))
    combined_research = viable[viable["strategy"] == "research"].copy()
    combined_late = viable[viable["strategy"] == "research_late_only"].copy()
    for name, group in (("research_combined", combined_research), ("research_late_only_combined", combined_late)):
        for unit in ("one_contract", "actual_contracts"):
            summary_rows.append(summarize_strategy(group, args.bankroll, "combined", name, unit))

    summary = pd.DataFrame(summary_rows).sort_values(["unit", "source", "strategy"]).reset_index(drop=True)

    skipped = settled[settled["action"].eq("skip") & settled["detail"].eq("failed_ws_reprice_filter") & settled["settled"]].copy()
    skipped_summary = []
    for (source, strategy), group in skipped.groupby(["capture", "strategy"], sort=True):
        skipped_summary.append(summarize_strategy(group, args.bankroll, source, strategy + "_failed_reprice_if_forced", "one_contract"))
    skipped_summary_df = pd.DataFrame(skipped_summary)

    scan_counts = pd.concat(
        [
            read_signal_scan_counts(args.research_capture, "research_live_capture"),
            read_signal_scan_counts(args.multi_capture, "multi_strategy_shadow_capture"),
        ],
        ignore_index=True,
    )

    live_sqlite = read_sqlite_trades(args.live_db, "live_research")
    live_capture_fills = executable[
        (executable["capture"] == "research_live_capture")
        & (executable["source_mode"] == "live")
        & executable["client_order_id"].notna()
    ].copy()
    match_report = compare_live_capture_to_sqlite(live_capture_fills, live_sqlite)

    settled.to_csv(args.output_dir / "captured_order_decisions_settled.csv", index=False)
    executable.to_csv(args.output_dir / "captured_executable_trades.csv", index=False)
    summary.to_csv(args.output_dir / "captured_strategy_summary.csv", index=False)
    skipped.to_csv(args.output_dir / "failed_reprice_candidates_settled.csv", index=False)
    skipped_summary_df.to_csv(args.output_dir / "failed_reprice_summary.csv", index=False)
    scan_counts.to_csv(args.output_dir / "signal_scan_counts.csv", index=False)
    (args.output_dir / "capture_vs_sqlite_match.json").write_text(json.dumps(match_report, indent=2), encoding="utf-8")
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "research_capture": str(args.research_capture),
                "multi_capture": str(args.multi_capture),
                "bankroll": args.bankroll,
                "match_report": match_report,
                "notes": [
                    "Primary replay uses order_decision rows captured by the websocket executor.",
                    "This is exact for strategies that were running and recorded order decisions.",
                    "It is not a full counterfactual replay for strategies that were not running.",
                    "All PnL is official-result settled and includes estimated Kalshi taker entry fees.",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("MATCH")
    print(json.dumps(match_report, indent=2))
    print("\nSUMMARY")
    with pd.option_context("display.max_rows", 200, "display.width", 220):
        print(summary.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    if not skipped_summary_df.empty:
        print("\nFAILED REPRICE IF FORCED")
        print(skipped_summary_df.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
