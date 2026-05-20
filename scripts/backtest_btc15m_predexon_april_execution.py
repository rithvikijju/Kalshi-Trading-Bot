#!/usr/bin/env python3
"""Conservative April Predexon BTC15M execution backtest.

This is for broad validation, not a live-replay replacement. The script uses
Predexon historical top-of-book snapshots and level-0 visible size, then applies
the same causal rules to each strategy:

* only quotes available at the snapshot timestamp
* official Kalshi result for scoring only
* taker fill at the visible top ask
* one first trade per event
* no candle high/low and no future-best quote/score selection
* data-quality filter rejecting isolated sparse snapshots

The main candidate under test is `new_liquid_rr_first`: the first tight-spread,
liquid, cheap-tail side per event. It is compared to the current BTC15M live
lowdd momentum rule.
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

from config.btc_1hr_config import CFG, kalshi_fee_dollars  # noqa: E402
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402
from scripts.backtest_predexon_orderbooks import max_drawdown_from_pnl  # noqa: E402


DEFAULT_PREDEXON_ROOT = PROJECT_ROOT / "data" / "predexon_kalshi_orderbooks"
DEFAULT_MARKETS = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "kalshi_markets.parquet"
DEFAULT_SPOT = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "spot_1m.parquet"
DEFAULT_OUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"predexon_btc15m_april_execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

START = pd.Timestamp("2026-04-01T00:00:00Z")
END = pd.Timestamp("2026-05-01T00:00:00Z")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    description: str
    ttl_lo: float
    ttl_hi: float
    spread_max_cents: float
    min_visible_qty: float
    min_entry: float
    max_entry: float
    min_rr: float


NEW_LIQUID = StrategySpec(
    name="new_liquid_rr_first",
    description="first side per event with TTL 2-8m, spread <=1c, visible top qty >=50, entry <=60c, RR >=0.5",
    ttl_lo=2.0,
    ttl_hi=8.0,
    spread_max_cents=1.0,
    min_visible_qty=50.0,
    min_entry=0.02,
    max_entry=0.60,
    min_rr=0.50,
)


NEW_LIQUID_QTY250 = StrategySpec(
    name="new_liquid_rr_first_qty250",
    description="same as new_liquid_rr_first but visible top qty >=250",
    ttl_lo=2.0,
    ttl_hi=8.0,
    spread_max_cents=1.0,
    min_visible_qty=250.0,
    min_entry=0.02,
    max_entry=0.60,
    min_rr=0.50,
)


def parse_ts(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def datetime_ns(values: Any) -> np.ndarray:
    return utc(values).astype("datetime64[ns, UTC]").astype("int64").to_numpy()


def parquet_glob(root: Path, suffix: str) -> str:
    return str((root.resolve() / "series=KXBTC15M" / "**" / f"*.{suffix}.parquet")).replace("\\", "/")


def build_or_load_prepared(args: argparse.Namespace, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    cache = args.output_dir / "prepared_btc15m_april_quotes.parquet"
    if args.reuse_prepared and cache.exists():
        print(f"loading prepared cache {cache}", flush=True)
        return pd.read_parquet(cache)

    top_glob = parquet_glob(args.predexon_root, "top")
    levels_glob = parquet_glob(args.predexon_root, "levels")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output_dir / "duckdb_tmp"
    temp_dir.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"PRAGMA temp_directory='{str(temp_dir.resolve()).replace(chr(92), '/')}'")
    con.execute(f"PRAGMA threads={max(1, int(args.threads))}")
    started = time.perf_counter()
    print("preparing April top snapshots and level-0 sizes with DuckDB...", flush=True)
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
    print(f"  top rows={con.execute('SELECT count(*) FROM top').fetchone()[0]:,}", flush=True)
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
    print(f"  level-0 size rows={con.execute('SELECT count(*) FROM sizes').fetchone()[0]:,}", flush=True)
    prepared_path = str(cache.resolve()).replace("\\", "/")
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
        TO '{prepared_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    con.close()
    print(f"prepared cache written in {time.perf_counter() - started:.1f}s: {cache}", flush=True)
    return pd.read_parquet(cache)


def load_metadata(path: Path) -> pd.DataFrame:
    meta = pd.read_parquet(path)
    for col in ["market_ticker", "event_ticker", "series_ticker", "result", "status"]:
        if col in meta:
            meta[col] = meta[col].astype(str)
    for col in ["open_time", "close_time"]:
        meta[col] = utc(meta[col])
    needed = [
        "market_ticker",
        "event_ticker",
        "series_ticker",
        "open_time",
        "close_time",
        "result",
        "status",
        "floor_strike",
    ]
    return meta[[c for c in needed if c in meta.columns]].drop_duplicates(["market_ticker", "event_ticker", "series_ticker"])


def load_spot(path: Path) -> pd.DataFrame:
    spot = pd.read_parquet(path)
    tcol = "available_at" if "available_at" in spot.columns else "time"
    spot = spot.rename(columns={tcol: "time"}).copy()
    spot["time"] = utc(spot["time"])
    spot["close"] = pd.to_numeric(spot["close"], errors="coerce")
    return spot.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)


def normalize_quotes(raw: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    q = raw.copy()
    q["available_at"] = utc(q["timestamp_utc"])
    q["market_ticker"] = q["market_ticker"].astype(str).str.upper()
    q["event_ticker"] = q["event_ticker"].astype(str).str.upper()
    q["series_ticker"] = q["series_ticker"].astype(str).str.upper()
    q["best_bid_cents"] = pd.to_numeric(q["best_bid_cents"], errors="coerce")
    q["best_ask_cents"] = pd.to_numeric(q["best_ask_cents"], errors="coerce")
    q["yes_bid_qty"] = pd.to_numeric(q["yes_bid_qty"], errors="coerce")
    q["yes_ask_qty"] = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    q["bid_depth"] = pd.to_numeric(q["bid_depth"], errors="coerce")
    q["ask_depth"] = pd.to_numeric(q["ask_depth"], errors="coerce")
    # Predexon/Kalshi snapshots use 0 ask when there is no resting ask.
    # This is not an executable free ask. Treat it as a 100c synthetic ask with
    # zero visible ask quantity, and derive the NO book from the real YES top.
    no_resting_ask = q["best_ask_cents"].fillna(0).le(0)
    q["yes_bid"] = np.where(q["best_bid_cents"].fillna(0).gt(0), q["best_bid_cents"] / 100.0, 0.0)
    q["yes_ask"] = np.where(no_resting_ask, 1.0, q["best_ask_cents"] / 100.0)
    q.loc[no_resting_ask, "yes_ask_qty"] = 0.0
    q["no_bid"] = (1.0 - q["yes_ask"]).clip(0.0, 1.0)
    q["no_ask"] = (1.0 - q["yes_bid"]).clip(0.0, 1.0)
    q["no_bid_qty"] = q["yes_ask_qty"]
    q["no_ask_qty"] = q["yes_bid_qty"]
    q["spread_cents"] = (q["yes_ask"] - q["yes_bid"]) * 100.0
    q = q[q["spread_cents"].between(0.0, 20.0)].copy()
    q = q.merge(meta, on=["market_ticker", "event_ticker", "series_ticker"], how="left")
    q["result"] = q["result"].astype(str).str.lower()
    q = q[q["result"].isin(["yes", "no"]) & q["close_time"].notna()].copy()
    q["ttl_min"] = (q["close_time"] - q["available_at"]).dt.total_seconds() / 60.0
    q["yes_mid"] = (q["yes_bid"].astype(float) + q["yes_ask"].astype(float)) / 2.0
    q = q.sort_values(["market_ticker", "available_at", "sequence"]).reset_index(drop=True)
    q["prev_gap_sec"] = q.groupby("market_ticker")["available_at"].diff().dt.total_seconds()
    q["next_gap_sec"] = -q.groupby("market_ticker")["available_at"].diff(-1).dt.total_seconds()
    q["data_quality_ok"] = q["prev_gap_sec"].le(180.0) & q["next_gap_sec"].le(180.0)
    q["quote_speed_cents"] = q.groupby("market_ticker")["yes_mid"].diff().abs().fillna(0.0) * 100.0
    denom = (q["bid_depth"].astype(float) + q["ask_depth"].astype(float)).replace(0.0, np.nan)
    q["book_imbalance"] = (q["bid_depth"].astype(float) - q["ask_depth"].astype(float)) / denom
    return q


def add_mid_changes(q: pd.DataFrame, lookbacks: list[float]) -> pd.DataFrame:
    out = q.sort_values(["market_ticker", "available_at"]).reset_index(drop=True).copy()
    for lb in lookbacks:
        suffix = str(lb).replace(".", "_")
        values = np.full(len(out), np.nan)
        ages = np.full(len(out), np.nan)
        for _, idx in out.groupby("market_ticker", sort=False).groups.items():
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
        out[f"yes_mid_chg_{suffix}m"] = values
        out[f"lookback_age_{suffix}m_sec"] = ages
    return out


def merge_btc(q: pd.DataFrame, spot: pd.DataFrame, lookbacks: list[int]) -> pd.DataFrame:
    out = q.sort_values("available_at").reset_index(drop=True).copy()
    s = spot[["time", "close"]].dropna().sort_values("time").copy()
    out["available_at"] = utc(out["available_at"]).astype("datetime64[ns, UTC]")
    s["time"] = utc(s["time"]).astype("datetime64[ns, UTC]")
    now = pd.merge_asof(
        out[["available_at"]],
        s.rename(columns={"time": "spot_time", "close": "btc_spot"}),
        left_on="available_at",
        right_on="spot_time",
        direction="backward",
    )
    out["btc_spot"] = now["btc_spot"].to_numpy()
    out["btc_spot_age_sec"] = (out["available_at"] - now["spot_time"]).dt.total_seconds().to_numpy()
    for lb in lookbacks:
        target = out[["available_at"]].copy()
        target["target"] = target["available_at"] - pd.Timedelta(minutes=lb)
        past = pd.merge_asof(
            target.sort_values("target"),
            s.rename(columns={"time": "past_time", "close": f"btc_past_{lb}m"}),
            left_on="target",
            right_on="past_time",
            direction="backward",
        ).sort_index()
        out[f"btc_ret_{lb}m_bps"] = 10000.0 * np.log(out["btc_spot"].to_numpy() / past[f"btc_past_{lb}m"].to_numpy())
        out[f"btc_lookback_age_{lb}m_sec"] = (out["available_at"] - past["past_time"]).dt.total_seconds().to_numpy()
    return out


def fee_one(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def make_side_candidates(q: pd.DataFrame) -> pd.DataFrame:
    base = q[q["data_quality_ok"] & q["result"].isin(["yes", "no"])].copy()
    frames = []
    for side in ("yes", "no"):
        t = base.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
            t["side_mid_chg_2m"] = t["yes_mid_chg_2_0m"]
            t["side_btc_3m_bps"] = t["btc_ret_3m_bps"]
            t["side_book_imbalance"] = t["book_imbalance"]
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
            t["side_mid_chg_2m"] = -t["yes_mid_chg_2_0m"]
            t["side_btc_3m_bps"] = -t["btc_ret_3m_bps"]
            t["side_book_imbalance"] = -t["book_imbalance"]
        t = t[t["entry_price"].between(0.01, 0.99) & t["visible_qty"].fillna(0).gt(0)].copy()
        t["entry_fee_1c"] = t["entry_price"].map(fee_one)
        t["premium_1c"] = t["entry_price"] + t["entry_fee_1c"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee_1c"]) / t["premium_1c"]
        t["win"] = t["result"].eq(side)
        t["pnl_1c"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee_1c"], -t["premium_1c"])
        frames.append(t)
    return pd.concat(frames, ignore_index=True).sort_values(["event_ticker", "available_at", "side"]).reset_index(drop=True)


def pick_first_event(candidates: pd.DataFrame, name: str) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    out = (
        candidates.sort_values(["event_ticker", "available_at", "rr", "side"], ascending=[True, True, False, True])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )
    out["strategy"] = name
    return out


def select_new_liquid(candidates: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    eligible = candidates[
        candidates["ttl_min"].between(spec.ttl_lo, spec.ttl_hi)
        & candidates["spread_cents"].le(spec.spread_max_cents)
        & candidates["entry_price"].between(spec.min_entry, spec.max_entry)
        & candidates["rr"].ge(spec.min_rr)
        & candidates["visible_qty"].fillna(0).ge(spec.min_visible_qty)
    ].copy()
    return pick_first_event(eligible, spec.name)


def select_current(q: pd.DataFrame) -> pd.DataFrame:
    base = q[
        q["data_quality_ok"]
        & q["ttl_min"].between(4.0, 5.0)
        & q["spread_cents"].le(2.0)
        & q["yes_mid_chg_2_0m"].notna()
        & q["yes_mid_chg_3_0m"].abs().le(0.35)
        & q["lookback_age_2_0m_sec"].between(115.0, 360.0)
        & q["lookback_age_3_0m_sec"].between(170.0, 420.0)
        & q["btc_lookback_age_3m_sec"].between(170.0, 420.0)
        & q["btc_spot_age_sec"].between(0.0, 120.0)
    ].copy()
    if base.empty:
        return pd.DataFrame()
    frames = []
    for side, mask in [
        ("yes", base["yes_mid_chg_2_0m"].ge(0.125) & base["btc_ret_3m_bps"].ge(0.0)),
        ("no", base["yes_mid_chg_2_0m"].le(-0.125) & base["btc_ret_3m_bps"].le(0.0)),
    ]:
        t = base[mask].copy()
        if t.empty:
            continue
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
        t = t[t["entry_price"].between(0.05, 0.80) & t["visible_qty"].fillna(0).ge(1.0)].copy()
        if t.empty:
            continue
        t["entry_fee_1c"] = t["entry_price"].map(fee_one)
        t["premium_1c"] = t["entry_price"] + t["entry_fee_1c"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee_1c"]) / t["premium_1c"]
        t = t[t["rr"].ge(0.33)].copy()
        if t.empty:
            continue
        t["win"] = t["result"].eq(side)
        t["pnl_1c"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee_1c"], -t["premium_1c"])
        t["score"] = t["yes_mid_chg_2_0m"].abs() - 0.01 * t["spread_cents"]
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = (
        out.sort_values(["event_ticker", "available_at", "score"], ascending=[True, True, False])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )
    out["strategy"] = "current_live_lowdd_rr033"
    return out


def exact_live_target_contracts(entry_price: float, visible_qty: float, target_win: float, max_premium: float, max_contracts: int) -> int:
    if not math.isfinite(float(entry_price)) or entry_price <= 0:
        return 0
    visible = int(math.floor(float(visible_qty))) if math.isfinite(float(visible_qty)) else 0
    if visible <= 0:
        return 0
    target_contracts = None
    for contracts in range(1, max_contracts + 1):
        fee = kalshi_fee_dollars(float(entry_price), contracts=contracts, liquidity="taker")
        win_profit = contracts * (1.0 - float(entry_price)) - fee
        if win_profit >= target_win:
            target_contracts = contracts
            break
    if target_contracts is None:
        return 0
    budget_contracts = 0
    for contracts in range(1, max_contracts + 1):
        fee = kalshi_fee_dollars(float(entry_price), contracts=contracts, liquidity="taker")
        cost = contracts * float(entry_price) + fee
        if cost <= max_premium + 1e-9:
            budget_contracts = contracts
        else:
            break
    return max(0, min(target_contracts, budget_contracts, visible, max_contracts))


def target_contracts_with_bankroll(
    entry_price: float,
    visible_qty: float,
    bankroll: float,
    available_cash: float,
    target_win: float = 3.0,
    max_premium: float = 15.0,
    max_contracts: int = 20,
) -> int:
    per_market_fraction = float(CFG.get("max_per_market", 0.20)) if isinstance(CFG, dict) else 0.20
    total_fraction = float(CFG.get("max_total_risk", 0.50)) if isinstance(CFG, dict) else 0.50
    budget = min(float(max_premium), float(available_cash), per_market_fraction * float(bankroll), total_fraction * float(bankroll))
    return exact_live_target_contracts(entry_price, visible_qty, target_win, budget, max_contracts)


def add_sizing(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    out["contracts_live_target3_max15"] = [
        exact_live_target_contracts(float(r.entry_price), float(r.visible_qty), 3.0, 15.0, 20)
        for r in out.itertuples()
    ]
    out["entry_fee_sized"] = [
        kalshi_fee_dollars(float(r.entry_price), contracts=int(r.contracts_live_target3_max15), liquidity="taker")
        if int(r.contracts_live_target3_max15) > 0
        else 0.0
        for r in out.itertuples()
    ]
    out["premium_sized"] = out["contracts_live_target3_max15"] * out["entry_price"].astype(float) + out["entry_fee_sized"]
    out["pnl_sized"] = np.where(
        out["contracts_live_target3_max15"].gt(0),
        np.where(
            out["win"],
            out["contracts_live_target3_max15"] * (1.0 - out["entry_price"].astype(float)) - out["entry_fee_sized"],
            -out["premium_sized"],
        ),
        0.0,
    )
    return out


def simulate_bankroll_target3(trades: pd.DataFrame, starting_bankroll: float = 100.0) -> dict[str, Any]:
    bankroll = float(starting_bankroll)
    min_bankroll = bankroll
    pnl_values: list[float] = []
    executed = 0
    skipped = 0
    max_contracts_seen = 0
    for row in trades.sort_values(["available_at", "event_ticker", "side"]).itertuples():
        contracts = target_contracts_with_bankroll(
            float(row.entry_price),
            float(row.visible_qty),
            bankroll=bankroll,
            available_cash=bankroll,
        )
        if contracts <= 0:
            skipped += 1
            pnl_values.append(0.0)
            continue
        fee = kalshi_fee_dollars(float(row.entry_price), contracts=contracts, liquidity="taker")
        premium = contracts * float(row.entry_price) + fee
        if premium > bankroll + 1e-9:
            skipped += 1
            pnl_values.append(0.0)
            continue
        if bool(row.win):
            pnl = contracts * (1.0 - float(row.entry_price)) - fee
        else:
            pnl = -premium
        bankroll += pnl
        min_bankroll = min(min_bankroll, bankroll)
        max_contracts_seen = max(max_contracts_seen, contracts)
        executed += 1
        pnl_values.append(float(pnl))
    pnl_series = pd.Series(pnl_values, dtype=float)
    return {
        "bankroll100_pnl_target3": float(bankroll - starting_bankroll),
        "bankroll100_end_target3": float(bankroll),
        "bankroll100_min_target3": float(min_bankroll),
        "bankroll100_max_dd_target3": max_drawdown_from_pnl(pnl_series),
        "bankroll100_executed_target3": int(executed),
        "bankroll100_skipped_target3": int(skipped),
        "bankroll100_max_contracts_target3": int(max_contracts_seen),
    }


def split_name(ts: pd.Timestamp) -> str:
    ts = pd.Timestamp(ts).tz_convert("UTC")
    if ts < pd.Timestamp("2026-04-11T00:00:00Z"):
        return "apr01_10"
    if ts < pd.Timestamp("2026-04-21T00:00:00Z"):
        return "apr11_20"
    return "apr21_30"


def summarize_trades(trades: pd.DataFrame, strategy: str, split: str) -> dict[str, Any]:
    if trades.empty:
        row = {
            "strategy": strategy,
            "split": split,
            "trades": 0,
            "events": 0,
            "pnl_1c": 0.0,
            "return_on_100_1c": 0.0,
            "premium_1c": 0.0,
            "rop_1c": 0.0,
            "win_rate": 0.0,
            "max_dd_1c": 0.0,
            "sharpe_1c": 0.0,
            "pnl_target3_max15": 0.0,
            "premium_target3_max15": 0.0,
            "max_dd_target3_max15": 0.0,
            "avg_entry": np.nan,
            "avg_rr": np.nan,
            "avg_visible_qty": np.nan,
            "first_entry": "",
            "last_entry": "",
        }
        row.update(simulate_bankroll_target3(trades))
        return row
    ordered = trades.sort_values(["available_at", "event_ticker", "side"]).reset_index(drop=True)
    pnl = ordered["pnl_1c"].astype(float)
    premium = ordered["premium_1c"].astype(float)
    pnl_sized = ordered["pnl_sized"].astype(float)
    premium_sized = ordered["premium_sized"].astype(float)
    row = {
        "strategy": strategy,
        "split": split,
        "trades": int(len(ordered)),
        "events": int(ordered["event_ticker"].nunique()),
        "pnl_1c": float(pnl.sum()),
        "return_on_100_1c": float(pnl.sum() / 100.0),
        "premium_1c": float(premium.sum()),
        "rop_1c": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "max_dd_1c": max_drawdown_from_pnl(pnl),
        "sharpe_1c": trade_sharpe(pnl),
        "pnl_target3_max15": float(pnl_sized.sum()),
        "premium_target3_max15": float(premium_sized.sum()),
        "max_dd_target3_max15": max_drawdown_from_pnl(pnl_sized),
        "avg_contracts_target3_max15": float(ordered["contracts_live_target3_max15"].mean()),
        "zero_sized_trades": int(ordered["contracts_live_target3_max15"].eq(0).sum()),
        "avg_entry": float(ordered["entry_price"].mean()),
        "avg_rr": float(ordered["rr"].mean()),
        "avg_visible_qty": float(ordered["visible_qty"].mean()),
        "yes_trades": int(ordered["side"].eq("yes").sum()),
        "no_trades": int(ordered["side"].eq("no").sum()),
        "first_entry": str(ordered["available_at"].min()),
        "last_entry": str(ordered["available_at"].max()),
    }
    row.update(simulate_bankroll_target3(ordered))
    return row


def build_summary(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy, group in trades.groupby("strategy", sort=True):
        rows.append(summarize_trades(group, strategy, "all"))
        for split, sg in group.groupby("split", sort=True):
            rows.append(summarize_trades(sg, strategy, split))
    return pd.DataFrame(rows).sort_values(["strategy", "split"]).reset_index(drop=True)


def daily_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["date_utc"] = t["available_at"].dt.strftime("%Y-%m-%d")
    rows = []
    for (strategy, day), g in t.groupby(["strategy", "date_utc"], sort=True):
        row = summarize_trades(g, strategy, day)
        row["date_utc"] = day
        rows.append(row)
    return pd.DataFrame(rows)


def round_for_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype.kind in "fc":
            out[col] = out[col].round(4)
    return out


def markdown_or_text(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predexon-root", type=Path, default=DEFAULT_PREDEXON_ROOT)
    parser.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    parser.add_argument("--spot", type=Path, default=DEFAULT_SPOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--start", default=str(START))
    parser.add_argument("--end", default=str(END))
    parser.add_argument("--threads", type=int, default=max(1, min(8, (CFG.get("scan_workers") or 8) if isinstance(CFG, dict) else 8)))
    parser.add_argument("--reuse-prepared", action="store_true")
    args = parser.parse_args()

    start = parse_ts(args.start)
    end = parse_ts(args.end)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw = build_or_load_prepared(args, start, end)
    meta = load_metadata(args.markets)
    spot = load_spot(args.spot)
    print("normalizing quotes and computing causal features...", flush=True)
    q = normalize_quotes(raw, meta)
    q = add_mid_changes(q, [2.0, 3.0])
    q = merge_btc(q, spot, [3])
    q["split"] = q["close_time"].map(split_name)
    q.to_parquet(args.output_dir / "features_snapshot_level.parquet", index=False, compression="zstd")
    print(
        f"feature rows={len(q):,} events={q['event_ticker'].nunique():,} "
        f"quality_ok={int(q['data_quality_ok'].sum()):,}",
        flush=True,
    )

    side_candidates = make_side_candidates(q)
    current = select_current(q)
    new_main = select_new_liquid(side_candidates, NEW_LIQUID)
    new_qty250 = select_new_liquid(side_candidates, NEW_LIQUID_QTY250)
    trades = pd.concat([current, new_main, new_qty250], ignore_index=True) if any(
        not x.empty for x in [current, new_main, new_qty250]
    ) else pd.DataFrame()
    if not trades.empty:
        trades["split"] = trades["close_time"].map(split_name)
        trades = add_sizing(trades)
        trades.to_csv(args.output_dir / "trades.csv", index=False)
        trades.to_parquet(args.output_dir / "trades.parquet", index=False, compression="zstd")
    summary = build_summary(trades) if not trades.empty else pd.DataFrame()
    daily = daily_summary(trades) if not trades.empty else pd.DataFrame()
    summary_disp = round_for_display(summary)
    daily_disp = round_for_display(daily)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    daily.to_csv(args.output_dir / "daily_summary.csv", index=False)
    data_report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start": str(start),
        "window_end": str(end),
        "predexon_root": str(args.predexon_root),
        "markets_path": str(args.markets),
        "spot_path": str(args.spot),
        "raw_rows": int(len(raw)),
        "feature_rows": int(len(q)),
        "events": int(q["event_ticker"].nunique()),
        "markets": int(q["market_ticker"].nunique()),
        "quality_ok_rows": int(q["data_quality_ok"].sum()),
        "result_rows": int(q["result"].isin(["yes", "no"]).sum()),
        "strategy_notes": {
            NEW_LIQUID.name: NEW_LIQUID.description,
            NEW_LIQUID_QTY250.name: NEW_LIQUID_QTY250.description,
            "current_live_lowdd_rr033": (
                "current BTC15M live rule: TTL 4-5m, spread <=2c, entry 5-80c, "
                "2m YES-mid move >=12.5c in side direction, BTC 3m not opposing, "
                "abs 3m YES-mid <=35c, RR >=0.33"
            ),
        },
        "execution_assumptions": [
            "One first trade per event; no future-best quote or future-best RR selection.",
            "Taker fill at visible top ask; top visible size is required and fees use kalshi_fee_dollars.",
            "Data-quality filter rejects isolated sparse snapshots using previous and next same-market snapshot gaps <=180s.",
            "Current strategy additionally requires non-stale 2m/3m quote lookbacks and 3m BTC lookback.",
            "BTC feature uses local 1-minute BTC close at or before the snapshot timestamp, never high/low.",
            "No real REST latency, quote fade, or FOK no-fill probability is simulated.",
        ],
    }
    (args.output_dir / "data_report.json").write_text(json.dumps(data_report, indent=2, default=str), encoding="utf-8")

    lines = [
        "# BTC15M April Predexon Conservative Execution Backtest",
        "",
        f"Generated: `{data_report['generated_at_utc']}`",
        f"Window: `{start}` -> `{end}`",
        f"Rows: `{len(q):,}` feature snapshots, `{q['event_ticker'].nunique():,}` events, `{int(q['data_quality_ok'].sum()):,}` quality-ok rows.",
        "",
        "## Summary",
        "",
        markdown_or_text(summary_disp) if not summary_disp.empty else "No trades.",
        "",
        "## Assumptions",
        "",
        *[f"- {item}" for item in data_report["execution_assumptions"]],
        "",
    ]
    (args.output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(summary_disp.to_string(index=False) if not summary_disp.empty else "No trades.")
    print(f"Wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
