#!/usr/bin/env python3
"""Backtest and research BTC Kalshi strategies on Predexon orderbook snapshots.

Predexon snapshots are materially better than Kalshi historical candles for
research because they include top-of-book and full YES depth at provider
timestamps. They are still not our live websocket replay: we do not have local
receive timestamps, every delta, or exchange response timing. This script keeps
that distinction explicit and uses conservative as-of replay rules.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, kalshi_fee_dollars  # noqa: E402
from scripts import may8examine as mx  # noqa: E402
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402


DEFAULT_ROOT = PROJECT_ROOT / "data" / "predexon_kalshi_orderbooks"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"predexon_research_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
BTC1H_MARKETS = PROJECT_ROOT / "data" / "research_datamart" / "kalshi_markets.parquet"
BTC15M_MARKETS = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "kalshi_markets.parquet"
BTC1H_SPOT = PROJECT_ROOT / "data" / "research_datamart" / "btc_1m.parquet"
BTC15M_SPOT = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "spot_1m.parquet"

TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")

BTC15M_RULE = {
    "ttl_lo": 4.0,
    "ttl_hi": 5.0,
    "spread_max_cents": 2.0,
    "mkt2_threshold": 0.125,
    "abs3_cap": 0.35,
    "entry_min": 0.05,
    "entry_max": 0.80,
    "min_rr": 0.33,
}

ONE_HOUR_VARIANTS = [
    mx.Variant(
        "current_1h_late_loss_guard",
        min_ttl_min=5.0,
        max_ttl_min=20.0,
        max_no_p=0.28,
    ),
    mx.Variant(
        "research_original_late",
        min_ttl_min=5.0,
        max_ttl_min=20.0,
    ),
    mx.Variant(
        "market_shrink_shape_adjacent",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.20,
        no_edge_add_cents=3.0,
        max_shape_violation_cents=2.0,
        min_event_valid_markets=4,
        max_event_median_spread_cents=2.0,
        adjacent_min_gross_edge_cents=4.0,
    ),
    mx.Variant(
        "strict_low_entry_core",
        market_shrink=0.35,
        min_edge_cents=18.0,
        max_entry=0.60,
        max_no_p=0.18,
        no_edge_add_cents=8.0,
        min_distance_sigma=0.60,
        max_spread_cents=1.0,
    ),
    mx.Variant(
        "high_conf_80",
        min_ttl_min=5.0,
        max_ttl_min=20.0,
        min_yes_p=0.80,
        max_no_p=0.20,
    ),
    mx.Variant(
        "high_conf_80_no_chase",
        min_ttl_min=5.0,
        max_ttl_min=20.0,
        min_yes_p=0.80,
        max_no_p=0.20,
        momentum_guard_usd=150.0,
        momentum_guard_distance_usd=999999.0,
        momentum_guard_distance_sigma=999.0,
        no_momentum_guard_usd=150.0,
        no_momentum_guard_distance_usd=999999.0,
        no_momentum_guard_distance_sigma=999.0,
    ),
    mx.Variant(
        "high_conf_80_entry70_no_chase",
        min_ttl_min=5.0,
        max_ttl_min=20.0,
        min_yes_p=0.80,
        max_no_p=0.20,
        max_entry=0.70,
        momentum_guard_usd=150.0,
        momentum_guard_distance_usd=999999.0,
        momentum_guard_distance_sigma=999.0,
        no_momentum_guard_usd=150.0,
        no_momentum_guard_distance_usd=999999.0,
        no_momentum_guard_distance_sigma=999.0,
    ),
    mx.Variant(
        "high_conf_80_entry59_70_no_chase",
        min_ttl_min=5.0,
        max_ttl_min=20.0,
        min_entry=0.59,
        max_entry=0.70,
        min_yes_p=0.80,
        max_no_p=0.20,
        momentum_guard_usd=150.0,
        momentum_guard_distance_usd=999999.0,
        momentum_guard_distance_sigma=999.0,
        no_momentum_guard_usd=150.0,
        no_momentum_guard_distance_usd=999999.0,
        no_momentum_guard_distance_sigma=999.0,
    ),
]


@dataclass(frozen=True)
class DataReport:
    source: str
    rows: int
    events: int
    markets: int
    first_ts: str | None
    last_ts: str | None
    bytes_on_disk: int


def utc_series(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def ts_arg(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def split_name(ts: pd.Timestamp) -> str:
    ts = pd.Timestamp(ts).tz_convert("UTC")
    if ts < TRAIN_END:
        return "train"
    if ts < VAL_END:
        return "validation"
    return "test"


def datetime_ns(values: Any) -> np.ndarray:
    """Return UTC nanoseconds even when parquet loads datetime64[us]."""
    return pd.to_datetime(values, utc=True, errors="coerce").astype("datetime64[ns, UTC]").astype("int64").to_numpy()


def file_bytes(root: Path) -> int:
    return int(sum(p.stat().st_size for p in root.rglob("*.parquet") if p.is_file())) if root.exists() else 0


def parquet_glob(root: Path, suffix: str) -> str:
    return str((root.resolve() / "**" / f"*.{suffix}.parquet")).replace("\\", "/")


def predexon_partition_files(
    root: Path,
    suffix: str,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    series: list[str],
) -> list[str]:
    """Return partition-pruned Predexon parquet paths for the requested window.

    Predexon data is stored as series/date partitions.  Passing the whole tree
    glob into DuckDB makes small split replays spend minutes opening irrelevant
    files, so prefer explicit per-date file lists and fall back to the broad
    glob only when the expected partition layout is absent.
    """
    if not root.exists():
        return []
    selected: list[Path] = []
    upper_series = [str(s).upper() for s in series]
    if start is not None and end is not None:
        start_day = pd.Timestamp(start).tz_convert("UTC").date()
        # Include the end date because late snapshots can sit exactly on
        # partition boundaries; timestamp filters still enforce the final range.
        end_day = pd.Timestamp(end).tz_convert("UTC").date()
        days = pd.date_range(start_day, end_day, freq="D")
        for series_name in upper_series:
            series_dir = root / f"series={series_name}"
            if not series_dir.exists():
                continue
            for day in days:
                day_dir = series_dir / f"date={pd.Timestamp(day).date().isoformat()}"
                if day_dir.exists():
                    selected.extend(day_dir.rglob(f"*.{suffix}.parquet"))
    if not selected:
        selected = list(root.rglob(f"*.{suffix}.parquet"))
    return [str(path.resolve()).replace("\\", "/") for path in sorted(selected) if path.is_file()]


def load_predexon_top(root: Path, start: pd.Timestamp | None, end: pd.Timestamp | None, series: list[str]) -> pd.DataFrame:
    top_files = predexon_partition_files(root, "top", start, end, series)
    if not top_files:
        return pd.DataFrame()
    con = duckdb.connect()
    where = ["series_ticker IN (" + ",".join("?" for _ in series) + ")"]
    params: list[Any] = [s.upper() for s in series]
    if start is not None:
        where.append("timestamp_utc >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("timestamp_utc < ?")
        params.append(end.to_pydatetime())
    top = con.execute(
        f"""
        SELECT provider, fidelity, market_ticker, event_ticker, series_ticker,
               timestamp_ms, timestamp_utc, sequence, yes_bid, yes_ask, no_bid, no_ask,
               best_bid_cents, best_ask_cents, bid_depth, ask_depth,
               yes_bid_levels, yes_ask_levels
        FROM read_parquet(?, union_by_name=true)
        WHERE {' AND '.join(where)}
        ORDER BY series_ticker, event_ticker, timestamp_utc, market_ticker
        """,
        [top_files, *params],
    ).fetchdf()
    con.close()
    if top.empty:
        return top
    top["timestamp_utc"] = utc_series(top["timestamp_utc"])
    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask", "bid_depth", "ask_depth", "sequence"]:
        top[col] = pd.to_numeric(top[col], errors="coerce")
    top = top.dropna(subset=["market_ticker", "event_ticker", "series_ticker", "timestamp_utc"])
    top = top.drop_duplicates(["market_ticker", "timestamp_ms", "sequence"], keep="last")
    return top.reset_index(drop=True)


def load_level0_sizes(root: Path, start: pd.Timestamp | None, end: pd.Timestamp | None, series: list[str]) -> pd.DataFrame:
    levels_files = predexon_partition_files(root, "levels", start, end, series)
    if not levels_files:
        return pd.DataFrame()
    con = duckdb.connect()
    where = [
        "level = 0",
        "series_ticker IN (" + ",".join("?" for _ in series) + ")",
    ]
    params: list[Any] = [s.upper() for s in series]
    if start is not None:
        where.append("timestamp_utc >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("timestamp_utc < ?")
        params.append(end.to_pydatetime())
    sizes = con.execute(
        f"""
        SELECT market_ticker, event_ticker, series_ticker, timestamp_ms, timestamp_utc, sequence,
               max(CASE WHEN side = 'yes_bid' THEN size END) AS yes_bid_qty,
               max(CASE WHEN side = 'yes_ask' THEN size END) AS yes_ask_qty
        FROM read_parquet(?, union_by_name=true)
        WHERE {' AND '.join(where)}
        GROUP BY 1,2,3,4,5,6
        """,
        [levels_files, *params],
    ).fetchdf()
    con.close()
    if sizes.empty:
        return sizes
    sizes["timestamp_utc"] = utc_series(sizes["timestamp_utc"])
    for col in ["yes_bid_qty", "yes_ask_qty"]:
        sizes[col] = pd.to_numeric(sizes[col], errors="coerce")
    return sizes


def attach_sizes(top: pd.DataFrame, sizes: pd.DataFrame) -> pd.DataFrame:
    if top.empty:
        return top
    if sizes.empty:
        out = top.copy()
        out["yes_bid_qty"] = np.nan
        out["yes_ask_qty"] = np.nan
    else:
        out = top.merge(
            sizes[
                [
                    "market_ticker",
                    "timestamp_ms",
                    "sequence",
                    "yes_bid_qty",
                    "yes_ask_qty",
                ]
            ],
            on=["market_ticker", "timestamp_ms", "sequence"],
            how="left",
        )
    # A NO ask consumes the visible YES bid side; a NO bid maps to YES ask.
    out["no_ask_qty"] = out["yes_bid_qty"]
    out["no_bid_qty"] = out["yes_ask_qty"]
    return out


def load_market_meta(root: Path = DEFAULT_ROOT) -> pd.DataFrame:
    frames = []
    if BTC1H_MARKETS.exists():
        frames.append(pd.read_parquet(BTC1H_MARKETS))
    if BTC15M_MARKETS.exists():
        frames.append(pd.read_parquet(BTC15M_MARKETS))
    metadata_dir = root / "market_metadata"
    if metadata_dir.exists():
        for path in sorted(metadata_dir.glob("*.parquet")):
            try:
                frames.append(pd.read_parquet(path))
            except Exception as exc:
                print(f"  warning: failed reading Predexon metadata {path}: {exc!r}", flush=True)
    if not frames:
        return pd.DataFrame()
    meta = pd.concat(frames, ignore_index=True, sort=False)
    for col in ["market_ticker", "event_ticker", "series_ticker"]:
        meta[col] = meta[col].astype(str).str.upper()
    for col in ["open_time", "close_time", "event_open_time"]:
        if col in meta:
            meta[col] = utc_series(meta[col])
    if "event_open_time" not in meta:
        meta["event_open_time"] = pd.NaT
    meta["event_open_time"] = meta["event_open_time"].fillna(meta["open_time"])
    if "result" not in meta:
        meta["result"] = None
    selected_rank = (
        meta["selected_for_download"].fillna(False).astype(bool).astype(int)
        if "selected_for_download" in meta.columns
        else pd.Series(0, index=meta.index)
    )
    meta["_meta_rank"] = meta["result"].astype(str).str.lower().isin(["yes", "no"]).astype(int) * 10 + selected_rank
    return (
        meta.sort_values(["market_ticker", "_meta_rank"])
        .drop_duplicates("market_ticker", keep="last")
        .drop(columns=["_meta_rank"], errors="ignore")
    )


def prepare_quotes(top: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    if top.empty:
        return top
    cols = [
        "market_ticker",
        "event_ticker",
        "series_ticker",
        "open_time",
        "close_time",
        "event_open_time",
        "floor_strike",
        "result",
    ]
    q = top.merge(meta[[c for c in cols if c in meta.columns]], on=["market_ticker", "event_ticker", "series_ticker"], how="left")
    q["available_at"] = utc_series(q["timestamp_utc"])
    q["ts_end"] = q["available_at"]
    best_bid_cents = pd.to_numeric(q["best_bid_cents"], errors="coerce")
    best_ask_cents = pd.to_numeric(q["best_ask_cents"], errors="coerce")
    # Predexon/Kalshi snapshots can use 0 to mean "no resting ask". That is
    # not an executable 0c YES ask. Treat it as a synthetic 100c YES ask with
    # zero visible YES-ask quantity; the NO ask remains 1 - YES bid.
    q["yes_bid_close"] = np.where(best_bid_cents > 0, best_bid_cents / 100.0, 0.0)
    q["yes_ask_close"] = np.where(best_ask_cents > 0, best_ask_cents / 100.0, 1.0)
    q.loc[best_ask_cents <= 0, "yes_ask_qty"] = 0.0
    q["yes_ask_exe"] = q["yes_ask_close"]
    q["no_ask_exe"] = 1.0 - q["yes_bid_close"]
    q["spread_cents"] = (q["yes_ask_close"] - q["yes_bid_close"]) * 100.0
    q = q.dropna(subset=["available_at", "close_time", "open_time", "yes_bid_close", "yes_ask_close"])
    q = q[(q["spread_cents"] >= 0.0) & (q["spread_cents"] <= 20.0)].copy()
    return q.sort_values(["series_ticker", "event_ticker", "available_at", "market_ticker"]).reset_index(drop=True)


def load_spot(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    spot = pd.read_parquet(path)
    if "time" not in spot.columns:
        spot = spot.rename(columns={"available_at": "time"})
    spot["time"] = utc_series(spot["time"])
    spot = mx.feature_frame(spot)
    return spot.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)


def btc_at_or_before(spot: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    return mx.btc_at_or_before(spot, ts)


def settle_trade(row: dict[str, Any], spot: pd.DataFrame, proxy_near_strike_usd: float) -> dict[str, Any] | None:
    settlement = str(row.get("settlement") or "").lower()
    source = str(row.get("settlement_source") or "")
    close_time = pd.Timestamp(row["close_time"]).tz_convert("UTC")
    strike = float(row["strike"])
    settlement_spot, _ = btc_at_or_before(spot, close_time)
    if settlement not in {"yes", "no"}:
        if settlement_spot is None or not math.isfinite(settlement_spot):
            return None
        if abs(float(settlement_spot) - strike) <= proxy_near_strike_usd:
            return None
        settlement = "yes" if settlement_spot >= strike else "no"
        source = "btc_proxy_drop_near_strike"
    win = settlement == str(row["side"]).lower()
    payout = 1.0 if win else 0.0
    pnl = payout - float(row["entry_price"]) - float(row["entry_fee"])
    return {
        **row,
        "settle_time": close_time,
        "settlement": settlement,
        "settlement_source": source,
        "settlement_spot": settlement_spot,
        "payout": payout,
        "pnl": pnl,
        "premium": float(row["entry_price"]) + float(row["entry_fee"]),
        "win": bool(win),
    }


def fresh_event_quotes(current: dict[str, pd.Series], scan_ts: pd.Timestamp, max_age_sec: float) -> pd.DataFrame:
    rows = []
    for row in current.values():
        age = (scan_ts - pd.Timestamp(row["available_at"])).total_seconds()
        if 0 <= age <= max_age_sec:
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def run_btc1h_replay(
    quotes: pd.DataFrame,
    spot: pd.DataFrame,
    *,
    variants: list[mx.Variant],
    train_days: int,
    scan_stride_sec: float,
    max_quote_age_sec: float,
    min_event_markets: int,
    proxy_near_strike_usd: float,
    progress_every: int,
) -> pd.DataFrame:
    q = quotes[quotes["series_ticker"].eq("KXBTCD")].copy()
    if q.empty:
        return pd.DataFrame()
    q = q.dropna(subset=["floor_strike"]).copy()
    if q.empty:
        return pd.DataFrame()
    CFG.update(mx.RESEARCH_CFG)
    rows: list[dict[str, Any]] = []
    event_cache: dict[str, dict] = {}
    grouped = list(q.groupby("event_ticker", sort=True))
    for i, (event_ticker, event_q) in enumerate(grouped, start=1):
        if i == 1 or i % progress_every == 0:
            print(f"  predexon 1h: event {i}/{len(grouped)} {event_ticker}", flush=True)
        event_q = event_q.sort_values(["available_at", "market_ticker"]).reset_index(drop=True)
        event_open = pd.Timestamp(event_q["event_open_time"].iloc[0])
        emp_cache = event_cache.get(event_ticker)
        if emp_cache is None:
            emp_cache = mx.build_event_cache(spot, event_open, train_days)
            if emp_cache is None:
                continue
            event_cache[event_ticker] = emp_cache
        current: dict[str, pd.Series] = {}
        taken: set[str] = set()
        last_scan = pd.Timestamp("1970-01-01T00:00:00Z")
        for scan_ts, tick_rows in event_q.groupby("available_at", sort=True):
            scan_ts = pd.Timestamp(scan_ts).tz_convert("UTC")
            for _, tick in tick_rows.iterrows():
                current[str(tick["market_ticker"])] = tick
            if (scan_ts - last_scan).total_seconds() < scan_stride_sec:
                continue
            last_scan = scan_ts
            scan_quotes = fresh_event_quotes(current, scan_ts, max_quote_age_sec)
            if scan_quotes["market_ticker"].nunique() < min_event_markets:
                continue
            candidates = mx.base_candidates(scan_quotes, spot, emp_cache, scan_ts)
            if candidates.empty:
                continue
            for variant in variants:
                if variant.name in taken:
                    continue
                signals = mx.signals_for_variant(candidates, variant, emp_cache)
                if signals.empty:
                    continue
                sig = signals.iloc[0]
                entry = float(sig["entry_price"])
                side = str(sig["side"]).lower()
                settled = settle_trade(
                    {
                        "source": "predexon_orderbook_snapshots",
                        "model": variant.name,
                        "variant": variant.name,
                        "split": split_name(pd.Timestamp(sig["close_time"])),
                        "event_ticker": str(sig["event_ticker"]),
                        "market_ticker": str(sig["market_ticker"]),
                        "side": side,
                        "strike": float(sig["floor_strike"]),
                        "entry_time": scan_ts,
                        "quote_ts_end": pd.Timestamp(sig.get("ts_end", scan_ts)),
                        "close_time": pd.Timestamp(sig["close_time"]),
                        "entry_price": entry,
                        "entry_fee": kalshi_fee_dollars(entry, contracts=1, liquidity="taker"),
                        "contracts": 1,
                        "visible_qty": float(sig.get("visible_qty", np.nan)),
                        "entry_spot": float(sig["entry_spot"]),
                        "raw_model_p_yes": float(sig["raw_model_p_yes"]),
                        "model_p_yes": float(sig["model_p_yes"]),
                        "side_probability": float(sig["side_probability"]),
                        "net_edge_cents": float(sig["net_edge_cents"]),
                        "edge_threshold_cents": float(sig["edge_threshold_cents"]),
                        "spread_cents": float(sig["spread_cents"]),
                        "market_mid": float(sig["market_mid"]),
                        "shape_violation_cents": float(sig.get("shape_violation_cents", np.nan)),
                        "event_valid_markets": int(sig.get("event_valid_markets", len(scan_quotes))),
                        "expected_move_usd": float(sig["expected_move_usd"]),
                        "signed_distance_usd": float(sig["signed_distance_usd"]),
                        "distance_sigma": float(sig["distance_sigma"]),
                        "side_ret_5m": float(sig["side_ret_5m"]),
                        "side_ret_10m": float(sig["side_ret_10m"]),
                        "side_ret_30m": float(sig["side_ret_30m"]),
                        "fidelity": str(sig.get("fidelity", "historical_snapshot_provider_time")),
                        "settlement": None,
                        "settlement_source": "btc_proxy",
                    },
                    spot,
                    proxy_near_strike_usd,
                )
                if settled is not None:
                    rows.append(settled)
                    taken.add(variant.name)
    return pd.DataFrame(rows).sort_values(["model", "entry_time", "market_ticker"]).reset_index(drop=True) if rows else pd.DataFrame()


def merge_btc_returns(q: pd.DataFrame, spot: pd.DataFrame, lookbacks: list[int]) -> pd.DataFrame:
    if q.empty or spot.empty:
        return q
    out = q.copy().sort_values("available_at").reset_index(drop=True)
    s = spot[["time", "close"]].dropna().sort_values("time").copy()
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    s["time"] = pd.to_datetime(s["time"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    now = pd.merge_asof(out[["available_at"]], s.rename(columns={"time": "spot_time", "close": "btc_spot"}), left_on="available_at", right_on="spot_time", direction="backward")
    out["btc_spot"] = now["btc_spot"].to_numpy()
    for lb in lookbacks:
        target = out[["available_at"]].copy()
        target["target"] = target["available_at"] - pd.Timedelta(minutes=lb)
        past = pd.merge_asof(target.sort_values("target"), s.rename(columns={"time": "past_time", "close": f"btc_past_{lb}m"}), left_on="target", right_on="past_time", direction="backward").sort_index()
        out[f"btc_ret_{lb}m_bps"] = 10000.0 * np.log(out["btc_spot"].to_numpy() / past[f"btc_past_{lb}m"].to_numpy())
    return out


def add_asof_mid_changes(q: pd.DataFrame, lookbacks_min: list[float]) -> pd.DataFrame:
    if q.empty:
        return q
    # Reset after sorting so group index arrays are positional and monotonic.
    # Without this, group labels can retain the caller's prior row order and
    # searchsorted can silently fail to find valid as-of lookbacks.
    out = q.sort_values(["market_ticker", "available_at"]).reset_index(drop=True).copy()
    out["yes_mid"] = (out["yes_bid_close"].astype(float) + out["yes_ask_close"].astype(float)) / 2.0
    for lb in lookbacks_min:
        name = f"yes_mid_chg_{str(lb).replace('.', '_')}m"
        age_name = f"lookback_age_{str(lb).replace('.', '_')}m_sec"
        values = np.full(len(out), np.nan)
        ages = np.full(len(out), np.nan)
        for _, idx in out.groupby("market_ticker").groups.items():
            pos = np.asarray(idx)
            ts_ns = datetime_ns(out.loc[pos, "available_at"])
            mid = out.loc[pos, "yes_mid"].to_numpy(dtype=float)
            target = ts_ns - int(lb * 60 * 1_000_000_000)
            past_idx = np.searchsorted(ts_ns, target, side="right") - 1
            good = past_idx >= 0
            if good.any():
                local = np.where(good)[0]
                values[pos[local]] = mid[local] - mid[past_idx[local]]
                ages[pos[local]] = (ts_ns[local] - ts_ns[past_idx[local]]) / 1_000_000_000.0
        out[name] = values
        out[age_name] = ages
    return out


def risk_reward(entry: pd.Series | float, fee_: pd.Series | float) -> pd.Series | float:
    return (1.0 - entry - fee_) / (entry + fee_)


def max_drawdown_from_pnl(pnl: pd.Series) -> float:
    pnl = pnl.dropna().astype(float)
    if pnl.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def current_15m_contracts(entry: float, fee_: float, visible_qty: float | None) -> int:
    if not math.isfinite(entry) or entry <= 0:
        return 0
    net_win = 1.0 - entry - fee_
    if net_win <= 0:
        return 0
    target_qty = math.floor(3.0 / net_win)
    premium_qty = math.floor(15.0 / max(entry + fee_, 1e-9))
    visible = math.floor(float(visible_qty)) if visible_qty is not None and math.isfinite(float(visible_qty)) else 1
    return max(0, min(20, target_qty, premium_qty, visible))


def score_btc15m_snapshot_model(quotes: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    q = quotes[quotes["series_ticker"].eq("KXBTC15M")].copy()
    if q.empty:
        return pd.DataFrame()
    q = add_asof_mid_changes(q, [1.0, 2.0, 3.0])
    q = merge_btc_returns(q, spot, [1, 3, 5])
    q["ttl_min"] = (q["close_time"] - q["available_at"]).dt.total_seconds() / 60.0
    q["quote_speed_cents"] = q.groupby("market_ticker")["yes_mid"].diff().abs().fillna(0.0) * 100.0
    q["book_imbalance"] = (q["bid_depth"].astype(float) - q["ask_depth"].astype(float)) / (q["bid_depth"].astype(float) + q["ask_depth"].astype(float)).replace(0.0, np.nan)
    base = q[
        q["ttl_min"].between(BTC15M_RULE["ttl_lo"], BTC15M_RULE["ttl_hi"])
        & q["spread_cents"].le(BTC15M_RULE["spread_max_cents"])
        & q["yes_mid_chg_2_0m"].notna()
        & q["yes_mid_chg_3_0m"].abs().le(BTC15M_RULE["abs3_cap"])
        & q["lookback_age_2_0m_sec"].between(115, 360)
        & q["lookback_age_3_0m_sec"].between(170, 420)
        & q["result"].astype(str).str.lower().isin(["yes", "no"])
    ].copy()
    if base.empty:
        return pd.DataFrame()
    rows = []
    for side, mask in [
        ("yes", base["yes_mid_chg_2_0m"].ge(BTC15M_RULE["mkt2_threshold"]) & base["btc_ret_3m_bps"].ge(0.0)),
        ("no", base["yes_mid_chg_2_0m"].le(-BTC15M_RULE["mkt2_threshold"]) & base["btc_ret_3m_bps"].le(0.0)),
    ]:
        t = base[mask].copy()
        if t.empty:
            continue
        t["model"] = "current_btc15m_lowdd"
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask_exe"].astype(float)
            t["visible_qty"] = pd.to_numeric(t["yes_ask_qty"], errors="coerce")
            t["side_probability_proxy"] = np.clip(t["entry_price"] + t["yes_mid_chg_2_0m"].abs() - BTC15M_RULE["mkt2_threshold"], 0.01, 0.99)
        else:
            t["entry_price"] = t["no_ask_exe"].astype(float)
            t["visible_qty"] = pd.to_numeric(t["no_ask_qty"], errors="coerce")
            t["side_probability_proxy"] = np.clip(t["entry_price"] + t["yes_mid_chg_2_0m"].abs() - BTC15M_RULE["mkt2_threshold"], 0.01, 0.99)
        t = t[t["entry_price"].between(BTC15M_RULE["entry_min"], BTC15M_RULE["entry_max"])].copy()
        if t.empty:
            continue
        t["entry_fee"] = t["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker"))
        t["rr"] = risk_reward(t["entry_price"], t["entry_fee"])
        t = t[t["rr"].ge(BTC15M_RULE["min_rr"]) & t["visible_qty"].fillna(0.0).ge(1.0)].copy()
        if t.empty:
            continue
        t["score"] = t["yes_mid_chg_2_0m"].abs() - 0.01 * t["spread_cents"].astype(float)
        t["entry_time"] = t["available_at"]
        t["settle_time"] = t["close_time"]
        t["settlement"] = t["result"].astype(str).str.lower()
        t["settlement_source"] = "official_kalshi_metadata"
        t["win"] = t["settlement"].eq(side)
        t["payout"] = np.where(t["win"], 1.0, 0.0)
        t["pnl"] = t["payout"] - t["entry_price"] - t["entry_fee"]
        t["contracts_current_sizing"] = [current_15m_contracts(float(r.entry_price), float(r.entry_fee), float(r.visible_qty) if pd.notna(r.visible_qty) else None) for r in t.itertuples()]
        t["pnl_current_sizing"] = t["pnl"] * t["contracts_current_sizing"]
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["premium_current_sizing"] = t["premium"] * t["contracts_current_sizing"]
        rows.append(t)
    if not rows:
        return pd.DataFrame()
    trades = pd.concat(rows, ignore_index=True)
    trades = trades.sort_values(["event_ticker", "entry_time", "score"], ascending=[True, True, False])
    trades = trades.drop_duplicates("event_ticker", keep="first").reset_index(drop=True)
    trades["split"] = trades["close_time"].map(split_name)
    return trades


def build_15m_side_candidates(quotes: pd.DataFrame, spot: pd.DataFrame, sample_sec: int) -> pd.DataFrame:
    q = quotes[quotes["series_ticker"].eq("KXBTC15M")].copy()
    if q.empty:
        return pd.DataFrame()
    q = add_asof_mid_changes(q, [0.5, 1.0, 2.0, 3.0])
    q = merge_btc_returns(q, spot, [1, 3, 5])
    q["ttl_min"] = (q["close_time"] - q["available_at"]).dt.total_seconds() / 60.0
    q["yes_mid"] = (q["yes_bid_close"].astype(float) + q["yes_ask_close"].astype(float)) / 2.0
    q["quote_speed_cents"] = q.groupby("market_ticker")["yes_mid"].diff().abs().fillna(0.0) * 100.0
    q["book_imbalance"] = (q["bid_depth"].astype(float) - q["ask_depth"].astype(float)) / (q["bid_depth"].astype(float) + q["ask_depth"].astype(float)).replace(0.0, np.nan)
    q = q[
        q["ttl_min"].between(2.0, 8.0)
        & q["spread_cents"].between(0.0, 4.0)
        & q["result"].astype(str).str.lower().isin(["yes", "no"])
    ].copy()
    if sample_sec > 0:
        q["sample_bucket"] = (datetime_ns(q["available_at"]) // int(sample_sec * 1_000_000_000)).astype("int64")
        q = q.sort_values(["market_ticker", "available_at"]).drop_duplicates(["market_ticker", "sample_bucket"], keep="last")
    frames = []
    for side in ["yes", "no"]:
        t = q.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask_exe"].astype(float)
            t["visible_qty"] = pd.to_numeric(t["yes_ask_qty"], errors="coerce")
            t["side_mid_chg_1m"] = t["yes_mid_chg_1_0m"]
            t["side_mid_chg_2m"] = t["yes_mid_chg_2_0m"]
            t["side_mid_chg_3m"] = t["yes_mid_chg_3_0m"]
        else:
            t["entry_price"] = t["no_ask_exe"].astype(float)
            t["visible_qty"] = pd.to_numeric(t["no_ask_qty"], errors="coerce")
            t["side_mid_chg_1m"] = -t["yes_mid_chg_1_0m"]
            t["side_mid_chg_2m"] = -t["yes_mid_chg_2_0m"]
            t["side_mid_chg_3m"] = -t["yes_mid_chg_3_0m"]
            t["book_imbalance"] = -t["book_imbalance"]
        t["entry_fee"] = t["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker"))
        t["rr"] = risk_reward(t["entry_price"], t["entry_fee"])
        t["win"] = t["result"].astype(str).str.lower().eq(side).astype(int)
        t = t[t["entry_price"].between(0.02, 0.95) & t["visible_qty"].fillna(0).ge(1)].copy()
        frames.append(t)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def train_ml_gate_15m(candidates: pd.DataFrame, out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty or candidates["event_ticker"].nunique() < 30:
        return pd.DataFrame(), pd.DataFrame()
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import roc_auc_score
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        print(f"  sklearn unavailable for ML research: {exc!r}", flush=True)
        return pd.DataFrame(), pd.DataFrame()

    c = candidates.copy()
    c["split"] = c["close_time"].map(split_name)
    features = [
        "ttl_min",
        "entry_price",
        "spread_cents",
        "rr",
        "yes_mid",
        "side_mid_chg_1m",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "btc_ret_1m_bps",
        "btc_ret_3m_bps",
        "btc_ret_5m_bps",
        "quote_speed_cents",
        "book_imbalance",
        "visible_qty",
        "bid_depth",
        "ask_depth",
    ]
    c = c.dropna(subset=["win", "close_time"])
    train = c[c["split"].eq("train")]
    val = c[c["split"].eq("validation")]
    test = c[c["split"].eq("test")]
    if len(train) < 200 or len(val) < 50 or len(test) < 50:
        return pd.DataFrame(), pd.DataFrame()
    models = {
        "hgb_15m_structure": make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(max_iter=160, learning_rate=0.045, max_leaf_nodes=12, l2_regularization=0.10, random_state=11)),
        "mlp_15m_structure": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), MLPClassifier(hidden_layer_sizes=(24, 12), alpha=0.02, max_iter=350, random_state=11, early_stopping=True)),
    }
    rows = []
    trades = []
    for name, model in models.items():
        model.fit(train[features], train["win"].astype(int))
        val_prob = model.predict_proba(val[features])[:, 1]
        test_prob = model.predict_proba(test[features])[:, 1]
        val_auc = roc_auc_score(val["win"], val_prob) if val["win"].nunique() > 1 else np.nan
        test_auc = roc_auc_score(test["win"], test_prob) if test["win"].nunique() > 1 else np.nan
        val_scored = val.copy()
        val_scored["pred_win_prob"] = val_prob
        thresholds = np.arange(0.54, 0.76, 0.02)
        best_thr = 0.70
        best_score = -999.0
        for thr in thresholds:
            picked = first_per_event(val_scored[val_scored["pred_win_prob"].ge(thr)].copy(), score_col="pred_win_prob")
            if len(picked) < 5:
                continue
            pnl = picked["win"].map({1: 1.0, 0: 0.0}).astype(float) - picked["entry_price"].astype(float) - picked["entry_fee"].astype(float)
            score = float(pnl.sum() - 0.5 * abs(max_drawdown_from_pnl(pnl)))
            if score > best_score:
                best_score = score
                best_thr = float(thr)
        test_scored = test.copy()
        test_scored["pred_win_prob"] = test_prob
        picked = first_per_event(test_scored[test_scored["pred_win_prob"].ge(best_thr)].copy(), score_col="pred_win_prob")
        if not picked.empty:
            picked["model"] = name + f"_thr_{best_thr:.2f}"
            picked["entry_time"] = picked["available_at"]
            picked["settle_time"] = picked["close_time"]
            picked["settlement"] = picked["result"].astype(str).str.lower()
            picked["payout"] = picked["win"].astype(float)
            picked["pnl"] = picked["payout"] - picked["entry_price"].astype(float) - picked["entry_fee"].astype(float)
            picked["premium"] = picked["entry_price"].astype(float) + picked["entry_fee"].astype(float)
            picked["split"] = "test"
            trades.append(picked)
        rows.append(
            {
                "model": name,
                "train_rows": len(train),
                "validation_rows": len(val),
                "test_rows": len(test),
                "validation_auc": float(val_auc) if math.isfinite(val_auc) else np.nan,
                "test_auc": float(test_auc) if math.isfinite(test_auc) else np.nan,
                "selected_threshold": best_thr,
                "test_trades": int(len(picked)),
                "test_pnl": float(picked["pnl"].sum()) if not picked.empty else 0.0,
                "test_win_rate": float((picked["pnl"] > 0).mean()) if not picked.empty else 0.0,
            }
        )
    report = pd.DataFrame(rows)
    report.to_csv(out_dir / "btc15m_ml_gate_report.csv", index=False)
    all_trades = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    if not all_trades.empty:
        all_trades.to_csv(out_dir / "btc15m_ml_gate_trades.csv", index=False)
    return report, all_trades


def first_per_event(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    if df.empty:
        return df
    return (
        df.sort_values(["event_ticker", "available_at", score_col], ascending=[True, True, False])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    frames = [trades.copy()]
    all_rows = trades.copy()
    all_rows["split"] = "all"
    frames.append(all_rows)
    data = pd.concat(frames, ignore_index=True)
    rows = []
    for (model, split), g in data.groupby(["model", "split"], sort=True):
        ordered = g.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
        pnl = ordered["pnl"].astype(float)
        premium = ordered["premium"].astype(float)
        rows.append(
            {
                "model": model,
                "split": split,
                "trades": int(len(ordered)),
                "pnl_1_contract": float(pnl.sum()),
                "return_on_100": float(pnl.sum() / 100.0),
                "premium": float(premium.sum()),
                "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "max_dd": max_drawdown_from_pnl(pnl),
                "sharpe_trade": trade_sharpe(pnl),
                "avg_entry": float(ordered["entry_price"].mean()) if len(ordered) else 0.0,
                "yes_trades": int(ordered["side"].astype(str).str.lower().eq("yes").sum()),
                "no_trades": int(ordered["side"].astype(str).str.lower().eq("no").sum()),
                "first_entry": str(ordered["entry_time"].min()),
                "last_entry": str(ordered["entry_time"].max()),
            }
        )
        if "pnl_current_sizing" in ordered:
            pnl_sized = ordered["pnl_current_sizing"].astype(float)
            premium_sized = ordered.get("premium_current_sizing", ordered["premium"]).astype(float)
            rows[-1]["pnl_current_sizing"] = float(pnl_sized.sum())
            rows[-1]["premium_current_sizing"] = float(premium_sized.sum())
            rows[-1]["max_dd_current_sizing"] = max_drawdown_from_pnl(pnl_sized)
    return pd.DataFrame(rows).sort_values(["model", "split"]).reset_index(drop=True)


def hypothesis_sweep_15m(trades: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    tests: list[tuple[str, pd.Series]] = []
    t = trades.copy()
    tests.append(("baseline_current", pd.Series(True, index=t.index)))
    tests.append(("entry_le_60c", t["entry_price"].le(0.60)))
    tests.append(("entry_le_60c_rr_ge_050", t["entry_price"].le(0.60) & t["rr"].ge(0.50)))
    tests.append(("rr_ge_050", t["rr"].ge(0.50)))
    tests.append(("rr_ge_075", t["rr"].ge(0.75)))
    tests.append(("spread_1c", t["spread_cents"].le(1.0)))
    tests.append(("visible_qty_ge_5", t["visible_qty"].fillna(0).ge(5.0)))
    tests.append(("visible_qty_ge_20", t["visible_qty"].fillna(0).ge(20.0)))
    tests.append(("low_quote_speed", t["quote_speed_cents"].le(10.0)))
    tests.append(("strong_mkt2_15c", t["yes_mid_chg_2_0m"].abs().ge(0.15)))
    tests.append(("strong_mkt2_20c", t["yes_mid_chg_2_0m"].abs().ge(0.20)))
    tests.append(("btc_aligned_5m", np.sign(t["btc_ret_5m_bps"].fillna(0.0)).eq(np.where(t["side"].eq("yes"), 1, -1)) | t["btc_ret_5m_bps"].abs().lt(1e-9)))
    tests.append(("book_imbalance_aligned", t["book_imbalance"].fillna(0.0).ge(-0.10)))
    tests.append(("late_4_to_4_5m", t["ttl_min"].between(4.0, 4.5)))
    tests.append(("late_4_5_to_5m", t["ttl_min"].between(4.5, 5.0)))
    tests.append(("entry_le_60_spread1_qty5", t["entry_price"].le(0.60) & t["spread_cents"].le(1.0) & t["visible_qty"].fillna(0).ge(5.0)))
    rows = []
    for name, mask in tests:
        g = t[mask.fillna(False)].copy()
        if g.empty:
            rows.append({"hypothesis": name, "trades": 0, "pnl": 0.0, "win_rate": 0.0, "max_dd": 0.0, "sharpe": 0.0})
            continue
        pnl = g["pnl"].astype(float)
        rows.append(
            {
                "hypothesis": name,
                "trades": int(len(g)),
                "pnl": float(pnl.sum()),
                "return_on_100": float(pnl.sum() / 100.0),
                "win_rate": float((pnl > 0).mean()),
                "max_dd": max_drawdown_from_pnl(pnl),
                "sharpe": trade_sharpe(pnl),
                "avg_entry": float(g["entry_price"].mean()),
                "avg_rr": float(g["rr"].mean()),
            }
        )
    out = pd.DataFrame(rows).sort_values(["pnl", "sharpe"], ascending=[False, False])
    out.to_csv(out_dir / "btc15m_hypothesis_sweep.csv", index=False)
    return out


def hypothesis_sweep_1h(trades: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    base = trades[trades["model"].eq("current_1h_late_loss_guard")].copy()
    if base.empty:
        return pd.DataFrame()
    base["ttl_min"] = (utc_series(base["close_time"]) - utc_series(base["entry_time"])).dt.total_seconds() / 60.0
    tests: list[tuple[str, pd.Series]] = [
        ("baseline_current_1h", pd.Series(True, index=base.index)),
        ("entry_le_60c", base["entry_price"].astype(float).le(0.60)),
        ("entry_le_65c", base["entry_price"].astype(float).le(0.65)),
        ("entry_le_70c", base["entry_price"].astype(float).le(0.70)),
        ("entry_40_65c", base["entry_price"].astype(float).between(0.40, 0.65)),
        ("side_prob_ge_76", base["side_probability"].astype(float).ge(0.76)),
        ("side_prob_ge_80", base["side_probability"].astype(float).ge(0.80)),
        ("net_edge_ge_16c", base["net_edge_cents"].astype(float).ge(16.0)),
        ("net_edge_ge_20c", base["net_edge_cents"].astype(float).ge(20.0)),
        ("spread_le_1c", base["spread_cents"].astype(float).le(1.0)),
        ("ttl_5_12m", base["ttl_min"].between(5.0, 12.0)),
        ("ttl_12_20m", base["ttl_min"].between(12.0, 20.0)),
        ("distance_sigma_ge_1", base["distance_sigma"].astype(float).ge(1.0)),
        ("distance_sigma_ge_1_25", base["distance_sigma"].astype(float).ge(1.25)),
        ("signed_distance_ge_150", base["signed_distance_usd"].astype(float).ge(150.0)),
        ("signed_distance_ge_250", base["signed_distance_usd"].astype(float).ge(250.0)),
        ("no_only", base["side"].astype(str).str.lower().eq("no")),
        ("yes_only", base["side"].astype(str).str.lower().eq("yes")),
        ("avoid_side_chase_10m_ge_150", base["side_ret_10m"].astype(float).lt(150.0)),
        ("avoid_side_chase_30m_ge_250", base["side_ret_30m"].astype(float).lt(250.0)),
        ("clean_shape", base.get("shape_violation_cents", pd.Series(999.0, index=base.index)).astype(float).le(1.0)),
        ("event_markets_ge_8", base.get("event_valid_markets", pd.Series(0, index=base.index)).astype(float).ge(8.0)),
        (
            "strict_combined_edge_prob_entry",
            base["entry_price"].astype(float).le(0.65)
            & base["side_probability"].astype(float).ge(0.76)
            & base["net_edge_cents"].astype(float).ge(16.0),
        ),
        (
            "strict_combined_no_chase",
            base["entry_price"].astype(float).le(0.65)
            & base["side_probability"].astype(float).ge(0.76)
            & base["side_ret_10m"].astype(float).lt(150.0),
        ),
        (
            "entry70_prob80_no_chase",
            base["entry_price"].astype(float).le(0.70)
            & base["side_probability"].astype(float).ge(0.80)
            & base["side_ret_10m"].astype(float).lt(150.0),
        ),
    ]
    rows = []
    for name, mask in tests:
        g = base[mask.fillna(False)].copy()
        if g.empty:
            rows.append({"hypothesis": name, "trades": 0, "pnl": 0.0, "return_on_100": 0.0, "win_rate": 0.0, "max_dd": 0.0, "sharpe": 0.0})
            continue
        pnl = g["pnl"].astype(float)
        rows.append(
            {
                "hypothesis": name,
                "trades": int(len(g)),
                "pnl": float(pnl.sum()),
                "return_on_100": float(pnl.sum() / 100.0),
                "win_rate": float((pnl > 0).mean()),
                "max_dd": max_drawdown_from_pnl(pnl),
                "sharpe": trade_sharpe(pnl),
                "avg_entry": float(g["entry_price"].astype(float).mean()),
                "avg_side_prob": float(g["side_probability"].astype(float).mean()),
                "avg_net_edge_cents": float(g["net_edge_cents"].astype(float).mean()),
            }
        )
    out = pd.DataFrame(rows).sort_values(["pnl", "sharpe"], ascending=[False, False])
    out.to_csv(out_dir / "btc1h_hypothesis_sweep.csv", index=False)
    return out


def write_report(
    out_dir: Path,
    data_report: DataReport,
    summary: pd.DataFrame,
    h1: pd.DataFrame,
    h15: pd.DataFrame,
    ml_report: pd.DataFrame,
    args: argparse.Namespace,
) -> None:
    lines = [
        "# 2026-05-14 Predexon BTC Orderbook Research",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Data",
        "",
        f"- Root: `{args.predexon_root}`",
        f"- Rows: {data_report.rows:,} snapshots across {data_report.events:,} events and {data_report.markets:,} markets.",
        f"- Time span: `{data_report.first_ts}` to `{data_report.last_ts}`.",
        f"- Disk size: {data_report.bytes_on_disk / (1024**2):.2f} MiB compressed Parquet.",
        "- Fidelity: provider-timestamped historical orderbook snapshots, not local websocket receive-time replay.",
        "",
        "## Current Model Backtests",
        "",
    ]
    if summary.empty:
        lines.append("No trades generated.")
    else:
        view = summary.copy()
        for col in ["return_on_100", "return_on_premium", "win_rate"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{100 * float(x):.2f}%")
        for col in ["pnl_1_contract", "premium", "max_dd", "sharpe_trade", "avg_entry", "pnl_current_sizing", "premium_current_sizing", "max_dd_current_sizing"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{float(x):.4f}")
        lines.append(markdown_or_text(view))
    lines.extend(["", "## BTC1H Current-Model Hypothesis Sweep", ""])
    if h1.empty:
        lines.append("No BTC1H current-model trades available for a filter sweep.")
    else:
        view = h1.head(25).copy()
        for col in ["return_on_100", "win_rate"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{100 * float(x):.2f}%")
        for col in ["pnl", "max_dd", "sharpe", "avg_entry", "avg_side_prob", "avg_net_edge_cents"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{float(x):.4f}")
        lines.append(markdown_or_text(view))
    lines.extend(["", "## BTC15M Hypothesis Sweep", ""])
    if h15.empty:
        lines.append("Not enough BTC15M Predexon trades for a filter sweep.")
    else:
        view = h15.head(20).copy()
        for col in ["return_on_100", "win_rate"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{100 * float(x):.2f}%")
        for col in ["pnl", "max_dd", "sharpe", "avg_entry", "avg_rr"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{float(x):.4f}")
        lines.append(markdown_or_text(view))
    lines.extend(["", "## Neural / ML Structural Gate", ""])
    if ml_report.empty:
        lines.append("Skipped: not enough chronologically split BTC15M Predexon candidate rows yet.")
    else:
        view = ml_report.copy()
        for col in ["validation_auc", "test_auc", "test_pnl", "test_win_rate"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{float(x):.4f}")
        lines.append(markdown_or_text(view))
    lines.extend(
        [
            "",
            "## Fidelity Notes",
            "",
            "- Decisions use only snapshots with `timestamp_utc <= decision_time`.",
            "- BTC spot uses one-minute close available at or before the provider timestamp.",
            "- BTC1H uses a stateful as-of event book and drops proxy settlements within the near-strike band.",
            "- BTC15M uses official `result` from local Kalshi metadata and any persisted Predexon market metadata.",
            "- Fill size is one contract for model comparison; BTC15M also reports the currently deployed target-win sizing where available.",
            "",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def markdown_or_text(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def append_ledger(text: str) -> None:
    path = PROJECT_ROOT / "docs" / "2026-05-14_predexon_research.md"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + text.rstrip() + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--predexon-root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--series", nargs="+", default=["KXBTCD", "KXBTC15M"])
    p.add_argument("--train-days", type=int, default=7)
    p.add_argument("--scan-stride-sec", type=float, default=1.0)
    p.add_argument("--max-quote-age-sec", type=float, default=120.0)
    p.add_argument("--min-event-markets", type=int, default=4)
    p.add_argument("--proxy-near-strike-usd", type=float, default=50.0)
    p.add_argument("--progress-every-events", type=int, default=50)
    p.add_argument("--ml-sample-sec", type=int, default=5)
    p.add_argument("--one-hour-variant-regex", help="Optional regex filter for BTC1H variant names.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    start = ts_arg(args.start)
    end = ts_arg(args.end)
    series = [s.upper() for s in args.series]
    print("Loading Predexon snapshots...", flush=True)
    top = load_predexon_top(args.predexon_root, start, end, series)
    sizes = load_level0_sizes(args.predexon_root, start, end, series)
    top = attach_sizes(top, sizes)
    meta = load_market_meta(args.predexon_root)
    quotes = prepare_quotes(top, meta)
    if quotes.empty:
        print("No usable Predexon quote rows found.", flush=True)
        return 1
    report = DataReport(
        source="predexon_orderbook_snapshots",
        rows=int(len(quotes)),
        events=int(quotes["event_ticker"].nunique()),
        markets=int(quotes["market_ticker"].nunique()),
        first_ts=str(quotes["available_at"].min()),
        last_ts=str(quotes["available_at"].max()),
        bytes_on_disk=file_bytes(args.predexon_root),
    )
    (args.output_dir / "data_report.json").write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")

    btc1h = load_spot(BTC1H_SPOT)
    btc15m = load_spot(BTC15M_SPOT)
    one_hour_variants = ONE_HOUR_VARIANTS
    if args.one_hour_variant_regex:
        pattern = re.compile(args.one_hour_variant_regex)
        one_hour_variants = [variant for variant in ONE_HOUR_VARIANTS if pattern.search(variant.name)]
        if not one_hour_variants:
            raise ValueError(f"--one-hour-variant-regex matched no BTC1H variants: {args.one_hour_variant_regex!r}")
    print("Running BTC1H current/research variants...", flush=True)
    trades_1h = run_btc1h_replay(
        quotes,
        btc1h,
        variants=one_hour_variants,
        train_days=args.train_days,
        scan_stride_sec=args.scan_stride_sec,
        max_quote_age_sec=args.max_quote_age_sec,
        min_event_markets=args.min_event_markets,
        proxy_near_strike_usd=args.proxy_near_strike_usd,
        progress_every=args.progress_every_events,
    )
    if not trades_1h.empty:
        trades_1h.to_csv(args.output_dir / "btc1h_predexon_trades.csv", index=False)

    print("Running BTC15M current live rule...", flush=True)
    trades_15m = score_btc15m_snapshot_model(quotes, btc15m)
    if not trades_15m.empty:
        trades_15m.to_csv(args.output_dir / "btc15m_predexon_current_trades.csv", index=False)

    print("Running BTC15M hypothesis sweep and ML structural gate...", flush=True)
    h1 = hypothesis_sweep_1h(trades_1h, args.output_dir)
    h15 = hypothesis_sweep_15m(trades_15m, args.output_dir)
    candidates_15m = build_15m_side_candidates(quotes, btc15m, args.ml_sample_sec)
    if not candidates_15m.empty:
        candidates_15m.to_parquet(args.output_dir / "btc15m_ml_candidates.parquet", index=False, compression="zstd")
    ml_report, ml_trades = train_ml_gate_15m(candidates_15m, args.output_dir)

    frames = []
    if not trades_1h.empty:
        frames.append(trades_1h)
    if not trades_15m.empty:
        frames.append(trades_15m)
    if not ml_trades.empty:
        frames.append(ml_trades)
    all_trades = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if not all_trades.empty:
        all_trades.to_csv(args.output_dir / "all_predexon_trades.csv", index=False)
    summary = summarize(all_trades)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    write_report(args.output_dir, report, summary, h1, h15, ml_report, args)
    append_ledger(
        "\n## Backtest Run\n"
        f"- Output: `{args.output_dir}`\n"
        f"- Predexon snapshots tested: {report.rows:,} rows, {report.events:,} events, {report.markets:,} markets, {report.first_ts} -> {report.last_ts}.\n"
        "- Script: `scripts/backtest_predexon_orderbooks.py`.\n"
    )
    if summary.empty:
        print("No trades generated.", flush=True)
    else:
        with pd.option_context("display.max_rows", 200, "display.width", 260):
            print(summary.to_string(index=False), flush=True)
    print(f"Wrote {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
