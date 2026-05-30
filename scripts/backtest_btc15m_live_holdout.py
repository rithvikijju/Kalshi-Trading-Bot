#!/usr/bin/env python3
"""Backtest BTC15M rules on a recent live websocket capture window.

This is the promotion-gate style replay: the input is our locally captured
websocket state with `received_at_ns` ordering. The script snapshots the live
DuckDB first so the running trader/capture writer is not held open by analysis.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402
from scripts.backtest_predexon_orderbooks import max_drawdown_from_pnl  # noqa: E402
from scripts.btc_1hr_research_live import KalshiApi  # noqa: E402


DEFAULT_CAPTURE_DB = Path(os.path.expanduser("~/.btc_kalshi_bot/btc15m_live_capture.duckdb"))
DEFAULT_OUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"btc15m_live_holdout_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class MomentumRule:
    name: str
    ttl_lo: float
    ttl_hi: float
    spread_max: float
    mkt_lb_min: int
    mkt_thr: float
    btc_lb_min: int | None = None
    btc_min_bps: float | None = None
    min_entry: float = 0.05
    max_entry: float = 0.90
    abs3_cap: float | None = None


LOWDD_RULES = [
    MomentumRule("current_lowdd_no_rv", 4, 5, 2.0, 2, 0.125, btc_lb_min=3, btc_min_bps=0.0, abs3_cap=0.35),
]


def snapshot_capture_db(source: Path, target: Path, attempts: int = 120, sleep_sec: float = 0.25) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            shutil.copy2(source, target)
            return target
        except (OSError, PermissionError) as exc:
            last_exc = exc
            time.sleep(sleep_sec)
    raise RuntimeError(f"could not snapshot {source} to {target}: {last_exc!r}")


def connect_read_only(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


def parse_utc_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def load_capture_window(
    snapshot: Path,
    hours: float,
    start_arg: str | None = None,
    end_arg: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp, dict[str, Any]]:
    con = connect_read_only(snapshot)
    start = parse_utc_ts(start_arg)
    end = parse_utc_ts(end_arg)
    if end is None:
        max_ts = con.execute(
            """
            SELECT max(TRY_CAST(received_at_utc AS TIMESTAMPTZ))
            FROM ws_orderbook_top
            WHERE event_ticker LIKE 'KXBTC15M-%'
            """
        ).fetchone()[0]
        if max_ts is None:
            raise RuntimeError("no BTC15M ws_orderbook_top rows found")
        end = pd.Timestamp(max_ts).tz_convert("UTC") if pd.Timestamp(max_ts).tzinfo else pd.Timestamp(max_ts).tz_localize("UTC")
    if start is None:
        start = end - pd.Timedelta(hours=float(hours))
    if start >= end:
        raise ValueError(f"start must be before end: {start} >= {end}")
    top = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               market_ticker, event_ticker,
               yes_bid, yes_bid_qty, yes_ask, yes_ask_qty,
               no_bid, no_bid_qty, no_ask, no_ask_qty,
               btc_spot, source
        FROM ws_orderbook_top
        WHERE event_ticker LIKE 'KXBTC15M-%'
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?
        ORDER BY received_at_ns
        """,
        [start.to_pydatetime(), end.to_pydatetime()],
    ).fetchdf()
    btc = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               price, best_bid, best_ask
        FROM coinbase_ticker
        WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?
        ORDER BY received_at_ns
        """,
        [(start - pd.Timedelta(minutes=20)).to_pydatetime(), end.to_pydatetime()],
    ).fetchdf()
    tables = con.execute("SHOW TABLES").fetchdf()["name"].astype(str).tolist()
    info = {
        "snapshot": str(snapshot),
        "tables": tables,
        "window_start_utc": str(start),
        "window_end_utc": str(end),
    }
    con.close()
    for df in (top, btc):
        if "received_at_utc" in df.columns:
            df["received_at_utc"] = pd.to_datetime(df["received_at_utc"], utc=True, errors="coerce")
    return top, btc, start, end, info


def fetch_results(events: list[str], sleep_sec: float = 0.25) -> pd.DataFrame:
    api = KalshiApi(env="prod", require_auth=False)
    rows: list[dict[str, Any]] = []
    for idx, event in enumerate(events, start=1):
        try:
            markets = api.get_markets(event_ticker=event, limit=1000, auto_paginate=True, use_cache=False).get("markets", [])
        except Exception as exc:
            print(f"warning: failed fetching {event}: {type(exc).__name__}: {exc}", flush=True)
            markets = []
        for market in markets:
            rows.append(
                {
                    "event_ticker": event,
                    "market_ticker": str(market.get("ticker") or "").upper(),
                    "result": str(market.get("result") or "").lower(),
                    "status": market.get("status"),
                    "expiration_value": market.get("expiration_value"),
                    "floor_strike": market.get("floor_strike"),
                    "open_time": market.get("open_time"),
                    "close_time": market.get("close_time"),
                }
            )
        if idx % 10 == 0:
            print(f"fetched settlement metadata for {idx}/{len(events)} events", flush=True)
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["close_time"] = pd.to_datetime(out["close_time"], utc=True, errors="coerce")
    out["actual_yes"] = np.where(out["result"].isin(["yes", "no"]), out["result"].eq("yes"), np.nan)
    return out


def add_asof_feature(df: pd.DataFrame, value_col: str, lb_min: float, out_col: str) -> pd.DataFrame:
    pieces = []
    offset_ns = int(float(lb_min) * 60 * 1_000_000_000)
    for _, group in df.groupby("market_ticker", sort=False):
        group = group.sort_values("received_at_ns")
        left = group[["received_at_ns", value_col]].copy()
        left["target_ns"] = left["received_at_ns"] - offset_ns
        right = group[["received_at_ns", value_col]].rename(columns={"received_at_ns": "past_ns", value_col: "past_value"})
        merged = pd.merge_asof(
            left.sort_values("target_ns"),
            right.sort_values("past_ns"),
            left_on="target_ns",
            right_on="past_ns",
            direction="backward",
        ).sort_index()
        out = group.copy()
        out[out_col] = out[value_col].to_numpy() - merged["past_value"].to_numpy()
        pieces.append(out)
    return pd.concat(pieces, ignore_index=True).sort_values("received_at_ns").reset_index(drop=True) if pieces else df


def enrich(top: pd.DataFrame, btc: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    q = top.copy()
    q = q.dropna(subset=["yes_bid", "yes_ask", "no_bid", "no_ask", "event_ticker", "market_ticker"])
    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask", "yes_bid_qty", "yes_ask_qty", "no_bid_qty", "no_ask_qty"]:
        q[col] = pd.to_numeric(q[col], errors="coerce")
    q = q[
        q["yes_bid"].between(0.0, 1.0)
        & q["yes_ask"].between(0.0, 1.0)
        & q["no_bid"].between(0.0, 1.0)
        & q["no_ask"].between(0.0, 1.0)
        & q["yes_bid"].le(q["yes_ask"])
        & q["no_bid"].le(q["no_ask"])
        & q["yes_bid_qty"].fillna(0).ge(0)
        & q["yes_ask_qty"].fillna(0).ge(0)
        & q["no_bid_qty"].fillna(0).ge(0)
        & q["no_ask_qty"].fillna(0).ge(0)
    ].copy()
    q["yes_mid"] = (q["yes_bid"] + q["yes_ask"]) / 2.0
    q["no_mid"] = 1.0 - q["yes_mid"]
    q["spread_cents"] = (q["yes_ask"] - q["yes_bid"]).clip(lower=0) * 100.0
    for lb in (1, 2, 3, 5):
        q = add_asof_feature(q, "yes_mid", lb, f"yes_mid_chg_{lb}m")
    if not results.empty:
        q = q.merge(
            results[["event_ticker", "market_ticker", "result", "status", "actual_yes", "close_time"]],
            on=["event_ticker", "market_ticker"],
            how="left",
        )
    else:
        q["result"] = None
        q["status"] = None
        q["actual_yes"] = np.nan
        q["close_time"] = pd.NaT
    q["ttl_min"] = (q["close_time"] - q["received_at_utc"]).dt.total_seconds() / 60.0

    btc = btc.dropna(subset=["received_at_ns", "price"]).sort_values("received_at_ns").copy()
    btc["price"] = pd.to_numeric(btc["price"], errors="coerce")
    for lb in (1, 3, 15):
        left = q[["received_at_ns"]].copy()
        left["target_ns"] = left["received_at_ns"] - int(lb * 60 * 1_000_000_000)
        cur = pd.merge_asof(
            q[["received_at_ns"]].sort_values("received_at_ns"),
            btc[["received_at_ns", "price"]].rename(columns={"price": "btc_now"}),
            on="received_at_ns",
            direction="backward",
        )
        past = pd.merge_asof(
            left.sort_values("target_ns"),
            btc[["received_at_ns", "price"]].rename(columns={"received_at_ns": "btc_past_ns", "price": "btc_past"}),
            left_on="target_ns",
            right_on="btc_past_ns",
            direction="backward",
        ).sort_index()
        q[f"btc_ret_{lb}m_bps"] = 10000.0 * np.log(cur["btc_now"].to_numpy() / past["btc_past"].to_numpy())
    return q


def fee(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def side_counts(t: pd.DataFrame) -> tuple[int, int]:
    if t.empty or "side" not in t.columns:
        return 0, 0
    if "legs" in t.columns:
        pair_mask = t["side"].eq("pair")
        yes_pairs = int((pair_mask & t["legs"].astype(str).str.contains("yes", case=False, na=False)).sum())
        no_pairs = int((pair_mask & t["legs"].astype(str).str.contains("no", case=False, na=False)).sum())
    else:
        yes_pairs = no_pairs = 0
    return int(t["side"].eq("yes").sum()) + yes_pairs, int(t["side"].eq("no").sum()) + no_pairs


def summarize(strategy: str, trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "strategy": strategy,
            "trades": 0,
            "events": 0,
            "pnl": 0.0,
            "return_on_100": 0.0,
            "premium": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "avg_entry": 0.0,
            "yes_trades": 0,
            "no_trades": 0,
            "first_entry": "",
            "last_entry": "",
        }
    t = trades.sort_values(["received_at_utc", "event_ticker", "side"]).copy()
    pnl = t["pnl"].astype(float)
    premium = t["premium"].astype(float)
    return {
        "strategy": strategy,
        "trades": int(len(t)),
        "events": int(t["event_ticker"].nunique()),
        "pnl": float(pnl.sum()),
        "return_on_100": float(pnl.sum() / 100.0),
        "premium": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "max_dd": max_drawdown_from_pnl(pnl),
        "sharpe": trade_sharpe(pnl),
        "avg_entry": float(t["entry_price"].mean()),
        "yes_trades": side_counts(t)[0],
        "no_trades": side_counts(t)[1],
        "first_entry": str(t["received_at_utc"].min()),
        "last_entry": str(t["received_at_utc"].max()),
    }


def cheap_tail_candidates(q: pd.DataFrame) -> pd.DataFrame:
    base = q[
        q["ttl_min"].between(2.0, 8.0)
        & q["spread_cents"].between(0.0, 4.0)
        & q["actual_yes"].notna()
    ].copy()
    if base.empty:
        return pd.DataFrame()
    frames = []
    for side in ("yes", "no"):
        t = base.copy()
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
        t["side"] = side
        t["entry_fee"] = t["entry_price"].map(fee)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee"]) / t["premium"]
        actual_yes = t["actual_yes"].astype(bool)
        t["win"] = actual_yes if side == "yes" else ~actual_yes
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["premium"])
        t = t[
            t["entry_price"].between(0.02, 0.60)
            & t["visible_qty"].fillna(0).ge(1.0)
            & t["rr"].ge(0.5)
        ].copy()
        if not t.empty:
            frames.append(t)
    return pd.concat(frames, ignore_index=True).sort_values(["event_ticker", "received_at_ns", "side"]).reset_index(drop=True) if frames else pd.DataFrame()


def first_per_event(candidates: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    out = (
        candidates.sort_values(["event_ticker", "received_at_ns", "side"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )
    out["strategy"] = strategy
    return out


def cheap_tail_side(q: pd.DataFrame, side: str) -> pd.DataFrame:
    candidates = cheap_tail_candidates(q)
    if candidates.empty:
        return candidates
    return first_per_event(candidates[candidates["side"].eq(side)].copy(), f"cheap_{side}_rr_first")


def pair_row(first: pd.Series, second: pd.Series, strategy: str) -> dict[str, Any]:
    total_premium = float(first["premium"]) + float(second["premium"])
    pnl = 1.0 - total_premium
    second_is_later = int(second["received_at_ns"]) >= int(first["received_at_ns"])
    later = second if second_is_later else first
    earlier = first if second_is_later else second
    row = later.to_dict()
    row.update(
        {
            "strategy": strategy,
            "side": "pair",
            "legs": f"{str(earlier['side'])}->{str(later['side'])}",
            "first_leg_side": str(earlier["side"]),
            "second_leg_side": str(later["side"]),
            "first_leg_time": earlier["received_at_utc"],
            "second_leg_time": later["received_at_utc"],
            "first_leg_price": float(earlier["entry_price"]),
            "second_leg_price": float(later["entry_price"]),
            "entry_price": total_premium,
            "entry_fee": float(first["entry_fee"]) + float(second["entry_fee"]),
            "premium": total_premium,
            "rr": np.inf if total_premium <= 0 else pnl / total_premium,
            "win": pnl > 0,
            "pnl": pnl,
            "lock_profit": pnl,
            "received_at_ns": int(later["received_at_ns"]),
            "received_at_utc": later["received_at_utc"],
        }
    )
    return row


def cheap_pair_lock(candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if candidates.empty:
        return pd.DataFrame()
    for _, group in candidates.sort_values(["received_at_ns", "side"]).groupby("event_ticker"):
        first = group.iloc[0]
        opposite = group[(group["side"].ne(first["side"])) & (group["received_at_ns"].gt(first["received_at_ns"]))]
        if opposite.empty:
            continue
        for _, second in opposite.iterrows():
            if float(first["premium"]) + float(second["premium"]) < 1.0:
                rows.append(pair_row(first, second, "cheap_pair_lock_rr"))
                break
    return pd.DataFrame(rows)


def cheap_tail_position_aware(candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if candidates.empty:
        return pd.DataFrame()
    for _, group in candidates.sort_values(["received_at_ns", "side"]).groupby("event_ticker"):
        first = group.iloc[0]
        opposite = group[(group["side"].ne(first["side"])) & (group["received_at_ns"].gt(first["received_at_ns"]))]
        locked = False
        for _, second in opposite.iterrows():
            if float(first["premium"]) + float(second["premium"]) < 1.0:
                rows.append(pair_row(first, second, "cheap_tail_position_aware"))
                locked = True
                break
        if not locked:
            row = first.to_dict()
            row["strategy"] = "cheap_tail_position_aware"
            row["legs"] = str(first["side"])
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["received_at_ns", "event_ticker"]).reset_index(drop=True) if rows else pd.DataFrame()


def score_lowdd_rule(q: pd.DataFrame, rule: MomentumRule) -> pd.DataFrame:
    mcol = f"yes_mid_chg_{rule.mkt_lb_min}m"
    base = q[
        q["ttl_min"].between(rule.ttl_lo, rule.ttl_hi)
        & q["spread_cents"].le(rule.spread_max)
        & q[mcol].notna()
        & q["actual_yes"].notna()
    ].copy()
    if rule.abs3_cap is not None:
        base = base[base["yes_mid_chg_3m"].abs().le(rule.abs3_cap)]
    up = base[mcol].ge(rule.mkt_thr)
    down = base[mcol].le(-rule.mkt_thr)
    if rule.btc_lb_min is not None and rule.btc_min_bps is not None:
        bcol = f"btc_ret_{rule.btc_lb_min}m_bps"
        up &= base[bcol].ge(rule.btc_min_bps)
        down &= base[bcol].le(-rule.btc_min_bps)
    frames = []
    for mask, side in [(up, "yes"), (down, "no")]:
        if not mask.any():
            continue
        t = base[mask].copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
        t = t[t["entry_price"].between(rule.min_entry, rule.max_entry) & t["visible_qty"].fillna(0).ge(1.0)].copy()
        if t.empty:
            continue
        t["entry_fee"] = t["entry_price"].map(fee)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        actual_yes = t["actual_yes"].astype(bool)
        t["win"] = actual_yes if side == "yes" else ~actual_yes
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["premium"])
        t["score"] = t[mcol].abs()
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return (
        out.sort_values(["event_ticker", "received_at_ns", "score"], ascending=[True, True, False])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def run(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    snapshot = snapshot_capture_db(args.capture_db, args.output_dir / "btc15m_live_capture_snapshot.duckdb")
    top, btc, start, end, info = load_capture_window(snapshot, args.hours, args.start, args.end)
    events = sorted(top["event_ticker"].dropna().astype(str).unique().tolist())
    print(f"loaded top rows={len(top):,} btc ticks={len(btc):,} events={len(events):,} window={start} -> {end}", flush=True)
    results = fetch_results(events, sleep_sec=args.kalshi_sleep_sec)
    finalized_events = int(results["result"].isin(["yes", "no"]).sum()) if not results.empty else 0
    print(f"settlement rows={len(results):,} finalized={finalized_events:,}", flush=True)
    q = enrich(top, btc, results)
    q.to_parquet(args.output_dir / "features.parquet", index=False, compression="zstd")
    trades_by_name: dict[str, pd.DataFrame] = {}
    candidates = cheap_tail_candidates(q)
    yes = cheap_tail_side(q, "yes")
    no = cheap_tail_side(q, "no")
    best = first_per_event(candidates, "cheap_tail_best_side_first")
    pair = cheap_pair_lock(candidates)
    aware = cheap_tail_position_aware(candidates)
    trades_by_name["cheap_yes_rr_first"] = yes
    trades_by_name["cheap_no_rr_first"] = no
    trades_by_name["cheap_tail_best_side_first"] = best
    trades_by_name["cheap_pair_lock_rr"] = pair
    trades_by_name["cheap_tail_position_aware"] = aware
    for rule in LOWDD_RULES:
        t = score_lowdd_rule(q, rule)
        if not t.empty:
            t["strategy"] = rule.name
        trades_by_name[rule.name] = t
    summary = pd.DataFrame([summarize(name, trades) for name, trades in trades_by_name.items()])
    all_trades = pd.concat([t for t in trades_by_name.values() if not t.empty], ignore_index=True) if any(not t.empty for t in trades_by_name.values()) else pd.DataFrame()
    info.update(
        {
            "capture_db": str(args.capture_db),
            "hours": args.hours,
            "start_arg": args.start,
            "end_arg": args.end,
            "top_rows": int(len(top)),
            "btc_ticks": int(len(btc)),
            "events_in_window": int(len(events)),
            "settlement_rows": int(len(results)),
            "finalized_result_rows": finalized_events,
            "feature_rows": int(len(q)),
            "notes": [
                "If --start/--end are provided, the window is explicit; otherwise it is measured from max received_at_utc in ws_orderbook_top.",
                "Rules use local received_at_ns/orderbook state and known official Kalshi results.",
                "Open/unsettled rows are excluded from PnL.",
            ],
        }
    )
    return summary, all_trades, info


def markdown_or_text(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--start", default=None, help="UTC start timestamp, e.g. 2026-05-13T00:00:00Z")
    p.add_argument("--end", default=None, help="UTC end timestamp, e.g. 2026-05-14T00:00:00Z")
    p.add_argument("--kalshi-sleep-sec", type=float, default=0.35)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary, trades, info = run(args)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    if not trades.empty:
        trades.to_csv(args.output_dir / "trades.csv", index=False)
    (args.output_dir / "data_report.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
    report = [
        "# BTC15M Live Websocket Holdout",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Capture DB: `{args.capture_db}`",
        f"Window: `{info['window_start_utc']}` -> `{info['window_end_utc']}`",
        f"Top rows: {info['top_rows']:,}; BTC ticks: {info['btc_ticks']:,}; events: {info['events_in_window']:,}; finalized result rows: {info['finalized_result_rows']:,}.",
        "",
        "## Summary",
        "",
        markdown_or_text(summary),
        "",
    ]
    (args.output_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(markdown_or_text(summary))
    print(f"Wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
