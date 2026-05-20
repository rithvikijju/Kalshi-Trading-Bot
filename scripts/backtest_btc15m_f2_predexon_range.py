#!/usr/bin/env python3
"""Backtest fixed BTC15M F2 rules on Predexon orderbook snapshots.

This script is a frozen-rule validator, not a search script.  It is meant for
outside-time validation on Jan-Mar Predexon data after the candidate was found
from April.  It uses:

* provider snapshot timestamp as available_at
* first qualifying signal per event
* executable top ask with visible top quantity
* official Kalshi result from local market metadata
* causal BTC minute close available at or before available_at
* lognormal fair value from 60m realized vol
* Kalshi taker fees plus optional adverse-entry stress
"""

from __future__ import annotations

import argparse
import json
import math
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
from scripts.backtest_btc15m_predexon_april_execution import (  # noqa: E402
    add_mid_changes,
    load_metadata,
    load_spot,
    merge_btc,
    normalize_quotes,
    parquet_glob,
    parse_ts,
)
from scripts.backtest_predexon_orderbooks import max_drawdown_from_pnl  # noqa: E402
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402


DEFAULT_PREDEXON_ROOT = PROJECT_ROOT / "data" / "predexon_kalshi_orderbooks"
DEFAULT_MARKETS = PROJECT_ROOT / "data" / "predexon_kalshi_orderbooks" / "market_metadata"
DEFAULT_SPOT = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "spot_1m.parquet"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_f2_predexon_range_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0


@dataclass(frozen=True)
class F2Rule:
    name: str
    fair_p_min: float
    edge_cents_min: float
    ttl_min: float
    ttl_max: float
    spread_max_cents: float
    entry_min: float
    entry_max: float
    visible_qty_min: float
    rationale: str
    rv_min: float | None = None
    rv_max: float | None = None
    quote_speed_min: float | None = None
    quote_speed_max: float | None = None


RULES = [
    F2Rule(
        "base_f2",
        0.60,
        12.0,
        5.0,
        12.0,
        2.0,
        0.02,
        0.60,
        1.0,
        "Frozen broad F2 from April multisplit search.",
    ),
    F2Rule(
        "ttl10_12_entry55_q50",
        0.60,
        12.0,
        10.0,
        12.0,
        2.0,
        0.02,
        0.55,
        50.0,
        "Strict forward-test subset: TTL 10-12m, entry <=55c, visible top qty >=50.",
    ),
    F2Rule(
        "ttl10_12_entry50_q250",
        0.60,
        12.0,
        10.0,
        12.0,
        2.0,
        0.02,
        0.50,
        250.0,
        "Conservative strict F2 research gate: TTL 10-12m, entry <=50c, visible top qty >=250.",
    ),
    F2Rule(
        "ttl10_12_entry50_q500",
        0.60,
        12.0,
        10.0,
        12.0,
        2.0,
        0.02,
        0.50,
        500.0,
        "Liquidity-stress variant: strict F2 with visible top qty >=500.",
    ),
    F2Rule(
        "ttl10_12_entry50_q1000",
        0.60,
        12.0,
        10.0,
        12.0,
        2.0,
        0.02,
        0.50,
        1000.0,
        "Liquidity-stress variant: strict F2 with visible top qty >=1000.",
    ),
    F2Rule(
        "ttl10_12_entry50_q500_rvmax040",
        0.60,
        12.0,
        10.0,
        12.0,
        2.0,
        0.02,
        0.50,
        500.0,
        "Liquidity plus high-volatility veto: visible top qty >=500 and 60m RV <=0.40.",
        rv_max=0.40,
    ),
    F2Rule(
        "ttl10_12_entry50_q250_qspeed05",
        0.60,
        12.0,
        10.0,
        12.0,
        2.0,
        0.02,
        0.50,
        250.0,
        "Quote-motion variant: strict F2 with visible top qty >=250 and quote speed >=0.5c.",
        quote_speed_min=0.5,
    ),
    F2Rule(
        "ttl6_12",
        0.60,
        12.0,
        6.0,
        12.0,
        2.0,
        0.02,
        0.60,
        1.0,
        "Nearby April-selected TTL 6-12m F2 variant.",
    ),
]


def normal_sf(z: np.ndarray) -> np.ndarray:
    vec = np.vectorize(lambda x: 0.5 * math.erfc(float(x) / math.sqrt(2.0)))
    return vec(z)


def fee_one(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def latest_market_metadata(root_or_file: Path, series: str, start: pd.Timestamp, end: pd.Timestamp) -> Path:
    if root_or_file.is_file():
        return root_or_file
    candidates = sorted(root_or_file.glob("*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            df = pd.read_parquet(path, columns=["series_ticker", "close_time"])
        except Exception:
            continue
        close = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
        mask = df["series_ticker"].astype(str).str.upper().eq(series) & close.ge(start) & close.lt(end)
        if int(mask.sum()) > 0:
            return path
    raise FileNotFoundError(f"no market metadata covering {series} {start} -> {end} under {root_or_file}")


def load_metadata_range(root_or_file: Path, series: str, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    """Load all local market metadata rows that overlap the requested range."""
    if root_or_file.is_file():
        meta = load_metadata(root_or_file)
        close = pd.to_datetime(meta["close_time"], utc=True, errors="coerce")
        mask = meta["series_ticker"].astype(str).str.upper().eq(series) & close.ge(start) & close.lt(end)
        return meta.loc[mask].copy(), str(root_or_file)

    frames: list[pd.DataFrame] = []
    used: list[str] = []
    for path in sorted(root_or_file.glob("*.parquet")):
        try:
            probe = pd.read_parquet(path, columns=["series_ticker", "close_time"])
        except Exception:
            continue
        close = pd.to_datetime(probe["close_time"], utc=True, errors="coerce")
        mask = probe["series_ticker"].astype(str).str.upper().eq(series) & close.ge(start) & close.lt(end)
        if not bool(mask.any()):
            continue
        meta = load_metadata(path)
        close2 = pd.to_datetime(meta["close_time"], utc=True, errors="coerce")
        meta = meta.loc[meta["series_ticker"].astype(str).str.upper().eq(series) & close2.ge(start) & close2.lt(end)].copy()
        if not meta.empty:
            frames.append(meta)
            used.append(str(path))
    if not frames:
        raise FileNotFoundError(f"no market metadata covering {series} {start} -> {end} under {root_or_file}")
    out = pd.concat(frames, ignore_index=True)
    out = (
        out.sort_values(["market_ticker", "event_ticker", "close_time"])
        .drop_duplicates(["market_ticker", "event_ticker", "series_ticker"], keep="last")
        .reset_index(drop=True)
    )
    return out, ";".join(used)


def build_or_load_prepared(args: argparse.Namespace, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    cache = args.out_dir / "prepared_top_with_l0_size.parquet"
    if args.reuse_prepared and cache.exists():
        print(f"loading prepared cache {cache}", flush=True)
        return pd.read_parquet(cache)

    top_glob = parquet_glob(args.predexon_root, "top")
    levels_glob = parquet_glob(args.predexon_root, "levels")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.out_dir / "duckdb_tmp"
    temp_dir.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"PRAGMA temp_directory='{str(temp_dir.resolve()).replace(chr(92), '/')}'")
    con.execute(f"PRAGMA threads={max(1, int(args.threads))}")
    t0 = time.perf_counter()
    print(f"reading Predexon top/level snapshots {start} -> {end}", flush=True)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE top AS
        SELECT *
        FROM (
            SELECT provider, fidelity, market_ticker, event_ticker, series_ticker,
                   timestamp_ms, timestamp_utc, sequence,
                   best_bid_cents, best_ask_cents, yes_bid, yes_ask, no_bid, no_ask,
                   bid_depth, ask_depth, yes_bid_levels, yes_ask_levels,
                   row_number() OVER (
                       PARTITION BY market_ticker, timestamp_ms, sequence
                       ORDER BY downloaded_at_utc DESC NULLS LAST
                   ) AS rn
            FROM read_parquet(?, union_by_name=true)
            WHERE series_ticker = 'KXBTC15M'
              AND timestamp_utc >= ?
              AND timestamp_utc < ?
        )
        WHERE rn = 1
        """,
        [top_glob, start.to_pydatetime(), end.to_pydatetime()],
    )
    top_rows = con.execute("SELECT count(*) FROM top").fetchone()[0]
    print(f"  top rows={top_rows:,}", flush=True)
    if top_rows == 0:
        con.close()
        return pd.DataFrame()
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE sizes AS
        SELECT market_ticker, timestamp_ms, sequence,
               max(CASE WHEN side = 'yes_bid' THEN size END) AS yes_bid_qty,
               max(CASE WHEN side = 'yes_ask' THEN size END) AS yes_ask_qty
        FROM read_parquet(?, union_by_name=true)
        WHERE series_ticker = 'KXBTC15M'
          AND timestamp_utc >= ?
          AND timestamp_utc < ?
          AND level = 0
          AND side IN ('yes_bid', 'yes_ask')
        GROUP BY 1,2,3
        """,
        [levels_glob, start.to_pydatetime(), end.to_pydatetime()],
    )
    size_rows = con.execute("SELECT count(*) FROM sizes").fetchone()[0]
    print(f"  level-0 size rows={size_rows:,}", flush=True)
    out_path = str(cache.resolve()).replace("\\", "/")
    con.execute(
        f"""
        COPY (
            SELECT top.provider, top.fidelity, top.market_ticker, top.event_ticker, top.series_ticker,
                   top.timestamp_ms, top.timestamp_utc, top.sequence,
                   top.best_bid_cents, top.best_ask_cents, top.bid_depth, top.ask_depth,
                   top.yes_bid_levels, top.yes_ask_levels,
                   sizes.yes_bid_qty, sizes.yes_ask_qty
            FROM top
            LEFT JOIN sizes USING (market_ticker, timestamp_ms, sequence)
            ORDER BY top.market_ticker, top.timestamp_utc, top.sequence
        )
        TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    con.close()
    print(f"prepared cache written in {time.perf_counter() - t0:.1f}s: {cache}", flush=True)
    return pd.read_parquet(cache)


def add_fair_value(q: pd.DataFrame) -> pd.DataFrame:
    out = q.sort_values("available_at").reset_index(drop=True).copy()
    spot = pd.to_numeric(out["btc_spot"], errors="coerce")
    strike = pd.to_numeric(out["floor_strike"], errors="coerce")
    ttl = pd.to_numeric(out["ttl_min"], errors="coerce").clip(lower=0.1)
    rv = pd.to_numeric(out["rv_60m"], errors="coerce").fillna(0.50).clip(lower=0.05, upper=3.0)
    denom = rv * np.sqrt(ttl / MINUTES_PER_YEAR)
    z = np.log(strike / spot) / denom.replace(0.0, np.nan)
    out["lognormal_p_yes"] = np.clip(normal_sf(z.to_numpy(dtype=float)), 0.001, 0.999)
    out["distance_bps"] = 10000.0 * np.log(spot / strike)
    return out


def add_spot_rv(q: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    s = spot.copy()
    if "rv_60m" not in s.columns:
        s = s.sort_values("time").reset_index(drop=True)
        ret = np.log(s["close"].astype(float)).diff()
        s["rv_60m"] = ret.rolling(60, min_periods=20).std() * math.sqrt(MINUTES_PER_YEAR)
    out = merge_btc(q, s, [3])
    s2 = s[["time", "rv_60m"]].dropna(subset=["time"]).sort_values("time").copy()
    out = out.sort_values("available_at").reset_index(drop=True)
    merged = pd.merge_asof(
        out[["available_at"]],
        s2.rename(columns={"time": "rv_time"}),
        left_on="available_at",
        right_on="rv_time",
        direction="backward",
    )
    out["rv_60m"] = merged["rv_60m"].to_numpy()
    out["rv_age_sec"] = (out["available_at"] - merged["rv_time"]).dt.total_seconds().to_numpy()
    return out


def add_close_spot(q: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    out = q.sort_values("close_time").reset_index(drop=True).copy()
    s = spot[["time", "close"]].dropna().sort_values("time").copy()
    merged = pd.merge_asof(
        out[["close_time"]],
        s.rename(columns={"time": "close_spot_time", "close": "btc_close_spot"}),
        left_on="close_time",
        right_on="close_spot_time",
        direction="backward",
        tolerance=pd.Timedelta(minutes=2),
    )
    out["btc_close_spot"] = merged["btc_close_spot"].to_numpy()
    out["close_spot_age_sec"] = (out["close_time"] - merged["close_spot_time"]).dt.total_seconds().to_numpy()
    out["proxy_close_margin_usd"] = (pd.to_numeric(out["btc_close_spot"], errors="coerce") - pd.to_numeric(out["floor_strike"], errors="coerce")).abs()
    return out.sort_values(["market_ticker", "available_at", "sequence"]).reset_index(drop=True)


def fill_missing_btc15m_strike_proxy(q: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """Fill missing BTC15M price-to-beat with a causal Coinbase open proxy.

    Older Kalshi metadata can show "Price to beat: TBD" after settlement.  The
    live market knows the exact price to beat, but historical metadata may not.
    For research-only older validation, use Coinbase's minute close for the
    event-open minute, available at open_time + 1 minute.  Rows before that
    availability keep a missing strike and therefore cannot trigger fair-value
    trades.
    """
    out = q.copy()
    out["target_source"] = np.where(out["floor_strike"].notna(), "kalshi_metadata", "missing")
    if "open_time" not in out.columns:
        return out
    missing_events = (
        out.loc[out["floor_strike"].isna(), ["event_ticker", "open_time"]]
        .dropna()
        .drop_duplicates("event_ticker")
        .copy()
    )
    if missing_events.empty:
        return out
    s = spot[["time", "close"]].dropna().sort_values("time").copy()
    if s.empty:
        return out
    missing_events["target_available_at"] = pd.to_datetime(missing_events["open_time"], utc=True) + pd.Timedelta(minutes=1)
    proxy = pd.merge_asof(
        missing_events.sort_values("target_available_at"),
        s.rename(columns={"time": "spot_time", "close": "proxy_floor_strike"}),
        left_on="target_available_at",
        right_on="spot_time",
        direction="backward",
        tolerance=pd.Timedelta(seconds=90),
    )
    proxy = proxy[["event_ticker", "proxy_floor_strike", "target_available_at"]]
    out = out.merge(proxy, on="event_ticker", how="left")
    can_use_proxy = (
        out["floor_strike"].isna()
        & out["proxy_floor_strike"].notna()
        & pd.to_datetime(out["available_at"], utc=True).ge(pd.to_datetime(out["target_available_at"], utc=True))
    )
    out.loc[can_use_proxy, "floor_strike"] = out.loc[can_use_proxy, "proxy_floor_strike"]
    out.loc[can_use_proxy, "target_source"] = "coinbase_open_minute_proxy"
    out = out.drop(columns=["proxy_floor_strike", "target_available_at"], errors="ignore")
    return out


def make_side_candidates(q: pd.DataFrame) -> pd.DataFrame:
    base = q[
        q["data_quality_ok"]
        & q["result"].isin(["yes", "no"])
        & q["btc_spot_age_sec"].between(0.0, 120.0)
        & q["rv_age_sec"].between(0.0, 120.0)
    ].copy()
    frames: list[pd.DataFrame] = []
    for side in ["yes", "no"]:
        t = base.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
            t["opp_entry"] = t["no_ask"].astype(float)
            t["side_fair_p"] = t["lognormal_p_yes"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
            t["opp_entry"] = t["yes_ask"].astype(float)
            t["side_fair_p"] = 1.0 - t["lognormal_p_yes"].astype(float)
        t = t[t["entry_price"].between(0.01, 0.99) & t["visible_qty"].fillna(0).ge(1.0)].copy()
        t["entry_fee"] = t["entry_price"].map(fee_one)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["fair_edge_cents"] = (t["side_fair_p"] - t["entry_price"]) * 100.0 - t["entry_fee"] * 100.0
        t["win"] = t["result"].eq(side)
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["premium"])
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(
        ["event_ticker", "available_at", "fair_edge_cents", "sequence", "side"],
        ascending=[True, True, False, True, True],
    ).reset_index(drop=True)


def select_rule(c: pd.DataFrame, rule: F2Rule, stress_cents: float, drop_proxy_near_strike_usd: float) -> pd.DataFrame:
    mask = (
        c["side_fair_p"].ge(rule.fair_p_min)
        & c["fair_edge_cents"].ge(rule.edge_cents_min)
        & c["ttl_min"].between(rule.ttl_min, rule.ttl_max)
        & c["spread_cents"].le(rule.spread_max_cents)
        & c["entry_price"].between(rule.entry_min, rule.entry_max)
        & c["visible_qty"].fillna(0).ge(rule.visible_qty_min)
    )
    if rule.rv_min is not None:
        mask &= pd.to_numeric(c["rv_60m"], errors="coerce").ge(float(rule.rv_min))
    if rule.rv_max is not None:
        mask &= pd.to_numeric(c["rv_60m"], errors="coerce").le(float(rule.rv_max))
    if rule.quote_speed_min is not None:
        mask &= pd.to_numeric(c["quote_speed_cents"], errors="coerce").ge(float(rule.quote_speed_min))
    if rule.quote_speed_max is not None:
        mask &= pd.to_numeric(c["quote_speed_cents"], errors="coerce").le(float(rule.quote_speed_max))
    out = c.loc[mask].drop_duplicates("event_ticker", keep="first").copy()
    if drop_proxy_near_strike_usd > 0 and "target_source" in out.columns and "proxy_close_margin_usd" in out.columns:
        proxy_target = out["target_source"].astype(str).eq("coinbase_open_minute_proxy")
        near_proxy = pd.to_numeric(out["proxy_close_margin_usd"], errors="coerce").le(float(drop_proxy_near_strike_usd))
        out = out.loc[~(proxy_target & near_proxy)].copy()
    out["strategy"] = rule.name
    out["rationale"] = rule.rationale
    stressed_entry = (pd.to_numeric(out["entry_price"], errors="coerce") + stress_cents / 100.0).clip(upper=0.99)
    stressed_fee = stressed_entry.map(fee_one)
    out["entry_stress"] = stressed_entry
    out["fee_stress"] = stressed_fee
    out["premium_stress"] = stressed_entry + stressed_fee
    out["pnl_stress"] = np.where(out["win"], 1.0 - stressed_entry - stressed_fee, -(stressed_entry + stressed_fee))
    return out.sort_values("available_at").reset_index(drop=True)


def summarize(d: pd.DataFrame, strategy: str, label: str) -> dict[str, Any]:
    if d.empty:
        return {
            "strategy": strategy,
            "split": label,
            "trades": 0,
            "pnl_stress": 0.0,
            "premium_stress": 0.0,
            "return_on_100": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "avg_entry": 0.0,
            "avg_ttl": 0.0,
            "avg_edge": 0.0,
            "avg_qty": 0.0,
            "first_entry": "",
            "last_entry": "",
        }
    pnl = pd.to_numeric(d["pnl_stress"], errors="coerce").fillna(0.0)
    prem = pd.to_numeric(d["premium_stress"], errors="coerce").fillna(0.0)
    return {
        "strategy": strategy,
        "split": label,
        "trades": int(len(d)),
        "pnl_stress": round(float(pnl.sum()), 4),
        "premium_stress": round(float(prem.sum()), 4),
        "return_on_100": round(float(pnl.sum()) / 100.0, 4),
        "rop": round(float(pnl.sum() / prem.sum()), 4) if float(prem.sum()) > 0 else 0.0,
        "win_rate": round(float(d["win"].mean()), 4),
        "max_dd": round(max_drawdown_from_pnl(pnl), 4),
        "sharpe": round(trade_sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(d["entry_price"], errors="coerce").mean()), 4),
        "avg_ttl": round(float(pd.to_numeric(d["ttl_min"], errors="coerce").mean()), 4),
        "avg_edge": round(float(pd.to_numeric(d["fair_edge_cents"], errors="coerce").mean()), 4),
        "avg_qty": round(float(pd.to_numeric(d["visible_qty"], errors="coerce").mean()), 4),
        "first_entry": str(d["available_at"].min()),
        "last_entry": str(d["available_at"].max()),
    }


def split_ranges(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    days = max(1, int(math.ceil((end - start).total_seconds() / 86400.0)))
    mid = start + (end - start) / 2
    return [
        ("all", start, end),
        ("first_half", start, mid),
        ("second_half", mid, end),
    ] + [
        (f"day_{i+1:02d}", start + pd.Timedelta(days=i), min(end, start + pd.Timedelta(days=i + 1)))
        for i in range(min(days, 14))
    ]


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predexon-root", type=Path, default=DEFAULT_PREDEXON_ROOT)
    ap.add_argument("--markets", type=Path, default=DEFAULT_MARKETS, help="Market metadata file or directory.")
    ap.add_argument("--spot", type=Path, default=DEFAULT_SPOT)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--stress-cents", type=float, default=2.0)
    ap.add_argument("--drop-proxy-near-strike-usd", type=float, default=20.0)
    ap.add_argument("--reuse-prepared", action="store_true")
    args = ap.parse_args()

    start = parse_ts(args.start)
    end = parse_ts(args.end)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = build_or_load_prepared(args, start, end)
    if raw.empty:
        raise SystemExit("No Predexon BTC15M top snapshots found for requested window.")
    meta, markets_path = load_metadata_range(args.markets, "KXBTC15M", start, end)
    print(f"using market metadata rows={len(meta):,} sources={markets_path}", flush=True)
    spot = load_spot(args.spot)
    print("normalizing and computing fixed F2 features...", flush=True)
    q = normalize_quotes(raw, meta)
    q = fill_missing_btc15m_strike_proxy(q, spot)
    q = add_mid_changes(q, [2.0, 3.0])
    q = add_spot_rv(q, spot)
    q = add_fair_value(q)
    q = add_close_spot(q, spot)
    q.to_parquet(args.out_dir / "features_snapshot_level.parquet", index=False, compression="zstd")
    c = make_side_candidates(q)
    c.to_parquet(args.out_dir / "side_candidates.parquet", index=False, compression="zstd")

    trade_frames = [select_rule(c, rule, args.stress_cents, args.drop_proxy_near_strike_usd) for rule in RULES]
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    if not trades.empty:
        trades.to_parquet(args.out_dir / "trades.parquet", index=False, compression="zstd")
        trades.to_csv(args.out_dir / "trades.csv", index=False)

    rows: list[dict[str, Any]] = []
    for rule in RULES:
        rt = trades[trades["strategy"].eq(rule.name)].copy() if not trades.empty else pd.DataFrame()
        for label, lo, hi in split_ranges(start, end):
            sub = rt[rt["available_at"].ge(lo) & rt["available_at"].lt(hi)].copy() if not rt.empty else rt
            rows.append(summarize(sub, rule.name, label))
    summary = pd.DataFrame(rows)
    summary.to_csv(args.out_dir / "summary.csv", index=False)

    meta_report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "start": str(start),
        "end": str(end),
        "predexon_root": str(args.predexon_root),
        "markets_path": str(markets_path),
        "spot_path": str(args.spot),
        "stress_cents": float(args.stress_cents),
        "drop_proxy_near_strike_usd": float(args.drop_proxy_near_strike_usd),
        "raw_rows": int(len(raw)),
        "feature_rows": int(len(q)),
        "candidate_rows": int(len(c)),
        "events": int(q["event_ticker"].nunique()) if not q.empty else 0,
        "quality_ok_rows": int(q["data_quality_ok"].sum()) if "data_quality_ok" in q else 0,
        "target_sources": q["target_source"].value_counts(dropna=False).to_dict() if "target_source" in q else {},
        "rules": [rule.__dict__ for rule in RULES],
        "assumptions": [
            "Predexon provider timestamp is treated as available_at.",
            "First qualifying signal per event after applying each rule.",
            "YES and NO are priced from separate executable top ask derived from YES book; no assumption NO=1-YES ask.",
            "Visible top level size is required and used only as a fill cap/gate for one-contract replay.",
            "BTC minute close and 60m realized vol are joined backward as-of; high/low are not used.",
            "When older BTC15M metadata lacks the exact price-to-beat, missing strike is filled with Coinbase event-open minute close available at open+1m and flagged as coinbase_open_minute_proxy.",
            "For proxy-target rows, outcomes within drop_proxy_near_strike_usd of the proxy strike are excluded from scoring.",
            "PnL includes taker fees and +stress-cents adverse entry.",
        ],
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(meta_report, indent=2, default=str), encoding="utf-8")
    display = summary[summary["split"].isin(["all", "first_half", "second_half"])].copy()
    for col in display.columns:
        if display[col].dtype.kind in "fc":
            display[col] = display[col].round(4)
    report = [
        "# BTC15M F2 Predexon Range Validation",
        "",
        f"Window: `{start}` -> `{end}`",
        f"Rows: `{len(q):,}` features, `{q['event_ticker'].nunique():,}` events, `{len(c):,}` side candidates.",
        "",
        "## Summary",
        "",
        markdown_table(display),
        "",
        "## Assumptions",
        "",
        *[f"- {item}" for item in meta_report["assumptions"]],
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(display.to_string(index=False), flush=True)
    print(f"Wrote {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
