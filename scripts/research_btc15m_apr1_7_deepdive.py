#!/usr/bin/env python3
"""Deep-dive BTC15M Apr 1-7 Predexon research slice.

This script intentionally separates the fixed study window (Apr 1-4 UTC) from
the untouched holdout window (Apr 5-7 UTC). It is designed as an audit trail,
not just a table generator.

Hard limits:

* uses only KXBTC15M snapshots with event close in [2026-04-01, 2026-04-08)
* study split: close_time in [2026-04-01, 2026-04-05)
* holdout split: close_time in [2026-04-05, 2026-04-08)
* no future-best row selection; every rule takes the first qualifying row/event
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
from typing import Any, Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402
from scripts.backtest_predexon_orderbooks import max_drawdown_from_pnl  # noqa: E402
from scripts.backtest_btc15m_predexon_april_execution import (  # noqa: E402
    DEFAULT_MARKETS,
    DEFAULT_PREDEXON_ROOT,
    DEFAULT_SPOT,
    add_mid_changes,
    build_or_load_prepared,
    load_metadata,
    load_spot,
    merge_btc,
    normalize_quotes,
)


START = pd.Timestamp("2026-04-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2026-04-05T00:00:00Z")
END = pd.Timestamp("2026-04-08T00:00:00Z")
DEFAULT_OUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"btc15m_apr1_7_deepdive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Strategy:
    name: str
    family: str
    description: str
    selector: Callable[[pd.DataFrame], pd.DataFrame]


def utc(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def parse_ts(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def fee_one(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def pnorm_sf(z: np.ndarray) -> np.ndarray:
    vec = np.vectorize(lambda x: 0.5 * math.erfc(float(x) / math.sqrt(2.0)))
    return vec(z)


def add_btc_state(q: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    out = q.sort_values("available_at").reset_index(drop=True).copy()
    s = spot.copy()
    if "rv_60m" not in s.columns:
        returns = np.log(s["close"].astype(float)).diff()
        s["rv_60m"] = returns.rolling(60, min_periods=20).std() * math.sqrt(365 * 24 * 60)
    keep_cols = ["time", "close", "rv_60m", "rv_15m"]
    keep_cols = [c for c in keep_cols if c in s.columns]
    s = s[keep_cols].dropna(subset=["time", "close"]).sort_values("time").copy()
    out["available_at"] = utc(out["available_at"]).astype("datetime64[ns, UTC]")
    s["time"] = utc(s["time"]).astype("datetime64[ns, UTC]")
    merged = pd.merge_asof(
        out[["available_at"]],
        s.rename(columns={"time": "btc_time", "close": "btc_spot"}),
        left_on="available_at",
        right_on="btc_time",
        direction="backward",
    )
    for col in merged.columns:
        if col not in {"available_at"}:
            out[col] = merged[col].to_numpy()
    out["btc_spot_age_sec"] = (out["available_at"] - merged["btc_time"]).dt.total_seconds().to_numpy()
    strike = pd.to_numeric(out["floor_strike"], errors="coerce")
    spot_now = pd.to_numeric(out["btc_spot"], errors="coerce")
    ttl_min = pd.to_numeric(out["ttl_min"], errors="coerce").clip(lower=0.1)
    ann_vol = pd.to_numeric(out.get("rv_60m", np.nan), errors="coerce").fillna(0.50).clip(lower=0.05, upper=3.0)
    denom = ann_vol * np.sqrt(ttl_min / (365.0 * 24.0 * 60.0))
    z = np.log(strike / spot_now) / denom.replace(0.0, np.nan)
    out["lognormal_p_yes"] = np.clip(pnorm_sf(z.to_numpy(dtype=float)), 0.001, 0.999)
    out["distance_usd"] = spot_now - strike
    out["distance_bps"] = 10000.0 * np.log(spot_now / strike)
    return out


def add_spread_changes(q: pd.DataFrame, lookbacks: list[float]) -> pd.DataFrame:
    out = q.sort_values(["market_ticker", "available_at"]).reset_index(drop=True).copy()
    for lb in lookbacks:
        suffix = str(lb).replace(".", "_")
        values = np.full(len(out), np.nan)
        ages = np.full(len(out), np.nan)
        for _, idx in out.groupby("market_ticker", sort=False).groups.items():
            pos = np.asarray(idx)
            ts_ns = out.loc[pos, "available_at"].astype("int64").to_numpy()
            spread = out.loc[pos, "spread_cents"].to_numpy(dtype=float)
            target = ts_ns - int(lb * 60 * 1_000_000_000)
            past_idx = np.searchsorted(ts_ns, target, side="right") - 1
            good = past_idx >= 0
            if good.any():
                local = np.where(good)[0]
                values[pos[local]] = spread[local] - spread[past_idx[local]]
                ages[pos[local]] = (ts_ns[local] - ts_ns[past_idx[local]]) / 1_000_000_000.0
        out[f"spread_chg_{suffix}m"] = values
        out[f"spread_lookback_age_{suffix}m_sec"] = ages
    return out


def prepare_features(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    raw = build_or_load_prepared(args, START, END)
    meta = load_metadata(args.markets)
    spot = load_spot(args.spot)
    q = normalize_quotes(raw, meta)
    q = q[(q["close_time"] >= START) & (q["close_time"] < END) & (q["available_at"] < q["close_time"])].copy()
    q["available_at"] = utc(q["available_at"])
    dedupe_keys = ["event_ticker", "market_ticker", "available_at"]
    order_cols = dedupe_keys + (["sequence"] if "sequence" in q.columns else [])
    before_dedupe = len(q)
    q = (
        q.sort_values(order_cols, na_position="first")
        .drop_duplicates(dedupe_keys, keep="last")
        .reset_index(drop=True)
    )
    deduped_rows = before_dedupe - len(q)
    q = add_mid_changes(q, [0.5, 1.0, 2.0, 3.0, 5.0])
    q = add_spread_changes(q, [1.0])
    q = merge_btc(q, spot, [1, 3, 5])
    q = add_btc_state(q, spot)
    q["split"] = np.where(q["close_time"] < TRAIN_END, "study", "holdout")
    q["day"] = q["close_time"].dt.strftime("%Y-%m-%d")
    q["hour"] = q["close_time"].dt.hour
    q["ttl_bin"] = pd.cut(q["ttl_min"], bins=[0, 2, 4, 6, 8, 10, 15, 999], include_lowest=True).astype(str)
    q["microprice"] = (
        q["yes_ask"].astype(float) * q["yes_bid_qty"].astype(float)
        + q["yes_bid"].astype(float) * q["yes_ask_qty"].astype(float)
    ) / (q["yes_bid_qty"].astype(float) + q["yes_ask_qty"].astype(float)).replace(0, np.nan)
    q["micropressure"] = q["microprice"] - q["yes_mid"]
    report = {
        "raw_rows": int(len(raw)),
        "feature_rows": int(len(q)),
        "events": int(q["event_ticker"].nunique()),
        "markets": int(q["market_ticker"].nunique()),
        "study_events": int(q.loc[q["split"].eq("study"), "event_ticker"].nunique()),
        "holdout_events": int(q.loc[q["split"].eq("holdout"), "event_ticker"].nunique()),
        "quality_ok_rows": int(q["data_quality_ok"].sum()),
        "deduped_rows": int(deduped_rows),
        "first_ts": str(q["available_at"].min()),
        "last_ts": str(q["available_at"].max()),
    }
    return q, spot, report


def reliability_map(q: pd.DataFrame) -> pd.DataFrame:
    g = q.groupby(["event_ticker", "market_ticker", "split", "day"], as_index=False)
    out = g.agg(
        first_ts=("available_at", "min"),
        last_ts=("available_at", "max"),
        rows=("available_at", "size"),
        min_ttl=("ttl_min", "min"),
        max_ttl=("ttl_min", "max"),
        median_prev_gap_sec=("prev_gap_sec", "median"),
        p95_prev_gap_sec=("prev_gap_sec", lambda s: float(pd.to_numeric(s, errors="coerce").quantile(0.95))),
        max_prev_gap_sec=("prev_gap_sec", "max"),
        quality_ok_rate=("data_quality_ok", "mean"),
        missing_yes_bid_qty=("yes_bid_qty", lambda s: float(pd.to_numeric(s, errors="coerce").isna().mean())),
        missing_yes_ask_qty=("yes_ask_qty", lambda s: float(pd.to_numeric(s, errors="coerce").isna().mean())),
        max_spread_cents=("spread_cents", "max"),
        median_spread_cents=("spread_cents", "median"),
    )
    out["reliable_event"] = (
        out["quality_ok_rate"].ge(0.75)
        & out["p95_prev_gap_sec"].le(180.0)
        & out["missing_yes_bid_qty"].le(0.05)
        & out["missing_yes_ask_qty"].le(0.05)
    )
    return out.sort_values(["day", "event_ticker"]).reset_index(drop=True)


def make_side_candidates(q: pd.DataFrame) -> pd.DataFrame:
    base = q[
        q["data_quality_ok"]
        & q["result"].isin(["yes", "no"])
        & q["btc_spot_age_sec"].between(0, 120)
    ].copy()
    frames = []
    for side in ["yes", "no"]:
        t = base.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
            t["opp_visible_qty"] = t["no_ask_qty"].astype(float)
            t["side_mid_chg_05m"] = t["yes_mid_chg_0_5m"]
            t["side_mid_chg_1m"] = t["yes_mid_chg_1_0m"]
            t["side_mid_chg_2m"] = t["yes_mid_chg_2_0m"]
            t["side_mid_chg_3m"] = t["yes_mid_chg_3_0m"]
            t["side_btc_1m_bps"] = t["btc_ret_1m_bps"]
            t["side_btc_3m_bps"] = t["btc_ret_3m_bps"]
            t["side_btc_5m_bps"] = t["btc_ret_5m_bps"]
            t["side_micropressure"] = t["micropressure"]
            t["side_depth_imbalance"] = t["book_imbalance"]
            t["side_fair_p"] = t["lognormal_p_yes"]
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
            t["opp_visible_qty"] = t["yes_ask_qty"].astype(float)
            t["side_mid_chg_05m"] = -t["yes_mid_chg_0_5m"]
            t["side_mid_chg_1m"] = -t["yes_mid_chg_1_0m"]
            t["side_mid_chg_2m"] = -t["yes_mid_chg_2_0m"]
            t["side_mid_chg_3m"] = -t["yes_mid_chg_3_0m"]
            t["side_btc_1m_bps"] = -t["btc_ret_1m_bps"]
            t["side_btc_3m_bps"] = -t["btc_ret_3m_bps"]
            t["side_btc_5m_bps"] = -t["btc_ret_5m_bps"]
            t["side_micropressure"] = -t["micropressure"]
            t["side_depth_imbalance"] = -t["book_imbalance"]
            t["side_fair_p"] = 1.0 - t["lognormal_p_yes"]
        t = t[t["entry_price"].between(0.01, 0.99) & t["visible_qty"].fillna(0).ge(1)].copy()
        t["entry_fee"] = t["entry_price"].map(fee_one)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee"]) / t["premium"]
        t["fair_edge_cents"] = (t["side_fair_p"] - t["entry_price"]) * 100.0 - t["entry_fee"] * 100.0
        t["win"] = t["result"].eq(side)
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["premium"])
        t["visible_ratio"] = t["visible_qty"] / t["opp_visible_qty"].replace(0, np.nan)
        t["entry_bucket"] = pd.cut(t["entry_price"], bins=[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]).astype(str)
        frames.append(t)
    c = pd.concat(frames, ignore_index=True)
    return c.sort_values(["event_ticker", "available_at", "side"]).reset_index(drop=True)


def first_per_event(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["strategy"] = name
        return out
    out = (
        df.sort_values(["event_ticker", "available_at", "side"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )
    out["strategy"] = name
    return out


def mask_base(c: pd.DataFrame, ttl_lo: float = 2, ttl_hi: float = 8, spread: float = 4) -> pd.Series:
    return c["ttl_min"].between(ttl_lo, ttl_hi) & c["spread_cents"].le(spread)


def current_lowdd(q: pd.DataFrame) -> pd.DataFrame:
    base = q[
        q["data_quality_ok"]
        & q["ttl_min"].between(4, 5)
        & q["spread_cents"].le(2)
        & q["yes_mid_chg_2_0m"].notna()
        & q["yes_mid_chg_3_0m"].abs().le(0.35)
        & q["lookback_age_2_0m_sec"].between(115, 360)
        & q["lookback_age_3_0m_sec"].between(170, 420)
        & q["btc_lookback_age_3m_sec"].between(170, 420)
    ].copy()
    rows = []
    for side, cond in [
        ("yes", base["yes_mid_chg_2_0m"].ge(0.125) & base["btc_ret_3m_bps"].ge(0)),
        ("no", base["yes_mid_chg_2_0m"].le(-0.125) & base["btc_ret_3m_bps"].le(0)),
    ]:
        t = base[cond].copy()
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
        t["side"] = side
        t = t[t["entry_price"].between(0.05, 0.80) & t["visible_qty"].fillna(0).ge(1)].copy()
        if t.empty:
            continue
        t["entry_fee"] = t["entry_price"].map(fee_one)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee"]) / t["premium"]
        t = t[t["rr"].ge(0.33)].copy()
        t["win"] = t["result"].eq(side)
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["premium"])
        rows.append(t)
    return first_per_event(pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(), "h01_current_lowdd")


def current_lowdd_from_candidates(c: pd.DataFrame) -> pd.DataFrame:
    base = c[
        c["ttl_min"].between(4, 5)
        & c["spread_cents"].le(2)
        & c["entry_price"].between(0.05, 0.80)
        & c["rr"].ge(0.33)
        & c["side_mid_chg_2m"].ge(0.125)
        & c["side_btc_3m_bps"].ge(0)
        & c["side_mid_chg_3m"].abs().le(0.35)
        & c["lookback_age_2_0m_sec"].between(115, 360)
        & c["lookback_age_3_0m_sec"].between(170, 420)
        & c["btc_lookback_age_3m_sec"].between(170, 420)
    ]
    return first_per_event(base.copy(), "h01_current_lowdd")


def build_rule_strategies(c: pd.DataFrame) -> list[Strategy]:
    def sel(name: str, mask_fn: Callable[[pd.DataFrame], pd.Series]) -> Callable[[pd.DataFrame], pd.DataFrame]:
        return lambda d: first_per_event(d[mask_fn(d)].copy(), name)

    return [
        Strategy("h03_liquid_cheap_tail", "liquidity", "entry<=60c spread<=1 qty>=50 RR>=0.5", sel("h03_liquid_cheap_tail", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5))),
        Strategy("h04_ultra_liquid_tail", "liquidity", "h03 with qty>=250", sel("h04_ultra_liquid_tail", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(250) & d["rr"].ge(0.5))),
        Strategy("h05_tail_btc3_align", "btc", "h03 plus BTC 3m aligned", sel("h05_tail_btc3_align", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5) & d["side_btc_3m_bps"].ge(0))),
        Strategy("h06_tail_btc5_align", "btc", "h03 plus BTC 5m aligned", sel("h06_tail_btc5_align", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5) & d["side_btc_5m_bps"].ge(0))),
        Strategy("h07_tail_btc1_align", "btc", "h03 plus BTC 1m aligned", sel("h07_tail_btc1_align", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5) & d["side_btc_1m_bps"].ge(0))),
        Strategy("h08_tail_1m_reversal_5m_align", "btc", "h03 plus BTC 1m contra and 5m aligned", sel("h08_tail_1m_reversal_5m_align", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5) & d["side_btc_1m_bps"].le(0) & d["side_btc_5m_bps"].ge(0))),
        Strategy("h09_imbalance_nonnegative", "orderbook", "side-adjusted depth imbalance >=0", sel("h09_imbalance_nonnegative", lambda d: mask_base(d, 2, 8, 2) & d["side_depth_imbalance"].ge(0))),
        Strategy("h10_strong_imbalance", "orderbook", "side-adjusted depth imbalance >=0.25", sel("h10_strong_imbalance", lambda d: mask_base(d, 2, 8, 2) & d["side_depth_imbalance"].ge(0.25))),
        Strategy("h11_microprice_pressure", "orderbook", "side micropressure >=0.5c", sel("h11_microprice_pressure", lambda d: mask_base(d, 2, 8, 2) & (d["side_micropressure"] * 100).ge(0.5))),
        Strategy("h12_microprice_reversal", "orderbook", "side micropressure <=-0.5c and entry<=60c", sel("h12_microprice_reversal", lambda d: mask_base(d, 2, 8, 2) & d["entry_price"].le(0.60) & (d["side_micropressure"] * 100).le(-0.5))),
        Strategy("h13_spread_tight", "orderbook", "spread <=1c and previous gap stable", sel("h13_spread_tight", lambda d: mask_base(d, 2, 8, 1) & d["prev_gap_sec"].le(120))),
        Strategy("h14_spread_widen_avoid", "orderbook", "h03 reject spread widened >=1c over previous 60s", sel("h14_spread_widen_avoid", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5) & d["spread_chg_1_0m"].lt(1.0) & d["spread_lookback_age_1_0m_sec"].between(55, 180))),
        Strategy("h15_quote_speed_low", "orderbook", "quote speed <=5c", sel("h15_quote_speed_low", lambda d: mask_base(d, 2, 8, 2) & d["quote_speed_cents"].le(5))),
        Strategy("h16_quote_speed_high_momentum", "orderbook", "quote speed >=10c and side mid move positive", sel("h16_quote_speed_high_momentum", lambda d: mask_base(d, 2, 8, 2) & d["quote_speed_cents"].ge(10) & d["side_mid_chg_1m"].ge(0.05))),
        Strategy("h17_ttl_2_4_tail", "timing", "h03 in TTL 2-4m", sel("h17_ttl_2_4_tail", lambda d: mask_base(d, 2, 4, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5))),
        Strategy("h18_ttl_4_6_tail", "timing", "h03 in TTL 4-6m", sel("h18_ttl_4_6_tail", lambda d: mask_base(d, 4, 6, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5))),
        Strategy("h19_ttl_6_8_tail", "timing", "h03 in TTL 6-8m", sel("h19_ttl_6_8_tail", lambda d: mask_base(d, 6, 8, 1) & d["entry_price"].le(0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5))),
        Strategy("h20_entry_10_30", "entry", "entry 10-30c spread<=1 RR>=1.5", sel("h20_entry_10_30", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].between(0.10, 0.30) & d["rr"].ge(1.5))),
        Strategy("h21_entry_30_60", "entry", "entry 30-60c spread<=1 RR>=0.5", sel("h21_entry_30_60", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].between(0.30, 0.60) & d["rr"].ge(0.5))),
        Strategy("h22_tail_no_lottery", "entry", "h03 with entry>=10c", sel("h22_tail_no_lottery", lambda d: mask_base(d, 2, 8, 1) & d["entry_price"].between(0.10, 0.60) & d["visible_qty"].ge(50) & d["rr"].ge(0.5))),
        Strategy("h23_top_qty_wall", "liquidity", "visible ratio >=2", sel("h23_top_qty_wall", lambda d: mask_base(d, 2, 8, 2) & d["visible_ratio"].ge(2))),
        Strategy("h24_opposite_wall_fade", "liquidity", "opposite wall and BTC contrarian", sel("h24_opposite_wall_fade", lambda d: mask_base(d, 2, 8, 2) & d["visible_ratio"].le(0.5) & d["side_btc_3m_bps"].le(0))),
        Strategy("h25_depth_wall_combo", "orderbook", "depth imbalance >=0.2 and qty>=100", sel("h25_depth_wall_combo", lambda d: mask_base(d, 2, 8, 2) & d["side_depth_imbalance"].ge(0.2) & d["visible_qty"].ge(100))),
        Strategy("h26_fair_imbalance", "fair_value", "fair edge >=5c and depth nonnegative", sel("h26_fair_imbalance", lambda d: mask_base(d, 2, 8, 2) & d["fair_edge_cents"].ge(5) & d["side_depth_imbalance"].ge(0))),
        Strategy("h27_fair_btc3", "fair_value", "fair edge >=5c and BTC 3m aligned", sel("h27_fair_btc3", lambda d: mask_base(d, 2, 8, 2) & d["fair_edge_cents"].ge(5) & d["side_btc_3m_bps"].ge(0))),
        Strategy("h31_cross_sectional_graph", "graph", "not applicable: BTC15M slice has one market per event", sel("h31_cross_sectional_graph", lambda d: pd.Series(False, index=d.index))),
        Strategy("h32_stale_one_sided_fade", "orderbook", "stable side quote while BTC moved favorably over 60s", sel("h32_stale_one_sided_fade", lambda d: mask_base(d, 2, 8, 2) & d["entry_price"].le(0.70) & d["visible_qty"].ge(50) & d["side_mid_chg_1m"].abs().le(0.01) & d["side_btc_1m_bps"].ge(2.0) & d["lookback_age_1_0m_sec"].between(55, 180))),
    ]


def tune_fair_value(c: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    study = c[c["split"].eq("study")]
    best = None
    records = []
    for edge in [2, 4, 6, 8, 10, 12, 15, 20]:
        picked = first_per_event(study[mask_base(study, 2, 8, 2) & study["fair_edge_cents"].ge(edge)].copy(), "h02_fair_value_transfer")
        pnl = float(picked["pnl"].sum()) if not picked.empty else 0.0
        dd = max_drawdown_from_pnl(picked["pnl"]) if not picked.empty else 0.0
        score = pnl + 0.25 * dd
        records.append({"edge_cents": edge, "study_trades": len(picked), "study_pnl": pnl, "study_dd": dd, "score": score})
        if best is None or score > best["score"]:
            best = records[-1]
    threshold = float(best["edge_cents"]) if best else 999.0
    picked_all = first_per_event(c[mask_base(c, 2, 8, 2) & c["fair_edge_cents"].ge(threshold)].copy(), "h02_fair_value_transfer")
    return picked_all, {"chosen_edge_cents": threshold, "grid": records}


def ml_strategies(c: pd.DataFrame, out_dir: Path) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    feature_cols = [
        "entry_price",
        "spread_cents",
        "visible_qty",
        "rr",
        "ttl_min",
        "side_mid_chg_05m",
        "side_mid_chg_1m",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "side_btc_1m_bps",
        "side_btc_3m_bps",
        "side_btc_5m_bps",
        "quote_speed_cents",
        "side_depth_imbalance",
        "side_micropressure",
        "visible_ratio",
        "fair_edge_cents",
        "distance_bps",
    ]
    base = c[mask_base(c, 2, 8, 4) & c["entry_price"].between(0.02, 0.90)].copy()
    fit = base[base["close_time"] < pd.Timestamp("2026-04-04T00:00:00Z")]
    internal_val = base[(base["close_time"] >= pd.Timestamp("2026-04-04T00:00:00Z")) & (base["close_time"] < TRAIN_END)]
    outputs: list[pd.DataFrame] = []
    reports: list[dict[str, Any]] = []
    if len(fit) < 500 or len(internal_val) < 100:
        reports.append({"model": "ml_all", "status": "skipped_insufficient_rows"})
        return outputs, reports
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        reports.append({"model": "ml_all", "status": f"skipped_import_error:{type(exc).__name__}"})
        return outputs, reports

    models = {
        "h28_hgb_microstructure": make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(max_iter=160, learning_rate=0.04, max_leaf_nodes=10, l2_regularization=0.25, random_state=7)),
        "h29_logistic_microstructure": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=500, C=0.25, class_weight="balanced")),
        "h30_mlp_microstructure": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), MLPClassifier(hidden_layer_sizes=(24, 12), alpha=0.05, max_iter=250, early_stopping=True, random_state=7)),
    }
    for name, model in models.items():
        try:
            model.fit(fit[feature_cols], fit["win"].astype(int))
            val = internal_val.copy()
            val["pred_win_prob"] = model.predict_proba(val[feature_cols])[:, 1]
            best = {"threshold": 0.99, "score": -1e9, "pnl": 0.0, "trades": 0}
            for threshold in np.arange(0.52, 0.82, 0.02):
                picked = first_per_event(val[val["pred_win_prob"].ge(threshold)].copy(), name)
                if len(picked) < 10:
                    continue
                pnl = float(picked["pnl"].sum())
                dd = max_drawdown_from_pnl(picked["pnl"])
                score = pnl + 0.25 * dd
                if score > best["score"]:
                    best = {"threshold": float(threshold), "score": float(score), "pnl": pnl, "trades": int(len(picked))}
            all_scored = base.copy()
            all_scored["pred_win_prob"] = model.predict_proba(all_scored[feature_cols])[:, 1]
            picked_all = first_per_event(all_scored[all_scored["pred_win_prob"].ge(best["threshold"])].copy(), name)
            outputs.append(picked_all)
            reports.append({"model": name, "status": "ok", **best})
        except Exception as exc:
            reports.append({"model": name, "status": f"failed:{type(exc).__name__}:{exc}"[:180]})
    pd.DataFrame(reports).to_csv(out_dir / "ml_model_report.csv", index=False)
    return outputs, reports


def summarize_strategy(trades: pd.DataFrame, name: str, family: str, description: str, split: str) -> dict[str, Any]:
    sample = trades if split == "all" else trades[trades["split"].eq(split)] if not trades.empty and "split" in trades.columns else pd.DataFrame()
    if sample.empty:
        return {
            "strategy": name,
            "family": family,
            "description": description,
            "split": split,
            "trades": 0,
            "events": 0,
            "pnl": 0.0,
            "return_on_100": 0.0,
            "premium": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "avg_entry": np.nan,
            "yes_trades": 0,
            "no_trades": 0,
        }
    ordered = sample.sort_values(["available_at", "event_ticker", "side"]).reset_index(drop=True)
    pnl = ordered["pnl"].astype(float)
    premium = ordered["premium"].astype(float)
    return {
        "strategy": name,
        "family": family,
        "description": description,
        "split": split,
        "trades": int(len(ordered)),
        "events": int(ordered["event_ticker"].nunique()),
        "pnl": float(pnl.sum()),
        "return_on_100": float(pnl.sum() / 100.0),
        "premium": float(premium.sum()),
        "rop": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "max_dd": max_drawdown_from_pnl(pnl),
        "sharpe": trade_sharpe(pnl),
        "avg_entry": float(ordered["entry_price"].mean()),
        "avg_rr": float(ordered["rr"].mean()) if "rr" in ordered else np.nan,
        "yes_trades": int(ordered["side"].eq("yes").sum()),
        "no_trades": int(ordered["side"].eq("no").sum()),
        "first_entry": str(ordered["available_at"].min()),
        "last_entry": str(ordered["available_at"].max()),
    }


def permutation_pvalue(trades_holdout: pd.DataFrame, c_holdout: pd.DataFrame, n_perm: int, seed: int) -> float:
    if trades_holdout.empty:
        return 1.0
    observed = float(trades_holdout["pnl"].sum())
    if observed <= 0:
        return 1.0
    rng = np.random.default_rng(seed)
    probs = c_holdout.groupby("side")["win"].mean().to_dict()
    entries = trades_holdout["entry_price"].astype(float).to_numpy()
    fees = trades_holdout["entry_fee"].astype(float).to_numpy()
    sides = trades_holdout["side"].astype(str).to_numpy()
    count_ge = 1
    for _ in range(n_perm):
        ps = np.asarray([probs.get(s, 0.5) for s in sides], dtype=float)
        wins = rng.random(len(entries)) < ps
        pnl = np.where(wins, 1.0 - entries - fees, -(entries + fees)).sum()
        if pnl >= observed:
            count_ge += 1
    return float(count_ge / (n_perm + 1))


def run_strategies(q: pd.DataFrame, candidates: pd.DataFrame, out_dir: Path, n_perm: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    strategy_frames: list[tuple[str, str, str, pd.DataFrame]] = []
    cur = current_lowdd_from_candidates(candidates)
    strategy_frames.append(("h01_current_lowdd", "current", "current live BTC15M lowdd rule", cur))
    fv, fv_report = tune_fair_value(candidates)
    strategy_frames.append(("h02_fair_value_transfer", "fair_value", f"lognormal fair-value transfer chosen edge {fv_report['chosen_edge_cents']}c", fv))
    for strat in build_rule_strategies(candidates):
        strategy_frames.append((strat.name, strat.family, strat.description, strat.selector(candidates)))
    ml_frames, ml_report = ml_strategies(candidates, out_dir)
    desc_by_ml = {r["model"]: json.dumps(r) for r in ml_report if "model" in r}
    for frame in ml_frames:
        name = str(frame["strategy"].iloc[0]) if not frame.empty and "strategy" in frame else "ml_empty"
        strategy_frames.append((name, "ml", desc_by_ml.get(name, "ML model"), frame))

    rows = []
    all_trades = []
    c_holdout = candidates[candidates["split"].eq("holdout")]
    for name, family, desc, trades in strategy_frames:
        if not trades.empty:
            trades = trades.copy()
            trades["strategy"] = name
            all_trades.append(trades)
        for split in ["study", "holdout", "all"]:
            rows.append(summarize_strategy(trades, name, family, desc, split))
        holdout = trades[trades["split"].eq("holdout")] if not trades.empty else pd.DataFrame()
        p = permutation_pvalue(holdout, c_holdout, n_perm=n_perm, seed=17 + len(rows))
        rows[-2]["perm_p_holdout"] = p  # holdout row was appended before all.
        rows[-2]["bonferroni_p_holdout"] = min(1.0, p * 32.0)
    summary = pd.DataFrame(rows)
    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    fail = failure_log(summary)
    return summary, trades_all, fail


def failure_log(summary: pd.DataFrame) -> pd.DataFrame:
    holdout = summary[summary["split"].eq("holdout")].copy()
    study = summary[summary["split"].eq("study")][["strategy", "pnl", "sharpe"]].rename(columns={"pnl": "study_pnl", "sharpe": "study_sharpe"})
    out = holdout.merge(study, on="strategy", how="left")
    reasons = []
    for row in out.itertuples():
        r = []
        if row.trades < 20:
            r.append("too_few_holdout_trades")
        if row.pnl <= 0:
            r.append("holdout_pnl_nonpositive")
        if row.sharpe < 0.8:
            r.append("holdout_sharpe_lt_0_8")
        if row.max_dd < -abs(row.pnl) and row.pnl > 0:
            r.append("drawdown_larger_than_profit")
        if getattr(row, "study_pnl", 0.0) > 0 and row.pnl <= 0:
            r.append("train_positive_holdout_negative")
        p_adj = getattr(row, "bonferroni_p_holdout", np.nan)
        if pd.notna(p_adj) and p_adj > 0.05:
            r.append("not_multiple_comparison_significant")
        reasons.append(";".join(r) if r else "passes_numeric_filters_needs_live_ws")
    out["failure_reason"] = reasons
    out["passes_numeric_filters"] = out["failure_reason"].eq("passes_numeric_filters_needs_live_ws")
    return out.sort_values(["passes_numeric_filters", "pnl", "sharpe"], ascending=[False, False, False])


def make_eda_tables(q: pd.DataFrame, c: pd.DataFrame, reliability: pd.DataFrame, out_dir: Path) -> None:
    tables = out_dir / "tables"
    tables.mkdir(exist_ok=True)
    q.groupby(["split", "day"]).agg(rows=("available_at", "size"), events=("event_ticker", "nunique"), quality_ok=("data_quality_ok", "mean")).to_csv(tables / "01_rows_events_quality_by_day.csv")
    q.groupby(["split", "ttl_bin"], observed=False).agg(rows=("available_at", "size"), spread_med=("spread_cents", "median"), spread_p95=("spread_cents", lambda s: s.quantile(0.95)), qty_bid_med=("yes_bid_qty", "median"), qty_ask_med=("yes_ask_qty", "median")).to_csv(tables / "02_spread_qty_by_ttl.csv")
    q.groupby(["day", "hour"]).size().rename("rows").reset_index().to_csv(tables / "03_rows_by_day_hour.csv", index=False)
    c.groupby(["split", "side", "entry_bucket"], observed=False).agg(rows=("pnl", "size"), win_rate=("win", "mean"), pnl=("pnl", "sum"), avg_entry=("entry_price", "mean")).to_csv(tables / "04_candidate_entry_buckets.csv")
    c.groupby(["split", "side"]).agg(rows=("pnl", "size"), win_rate=("win", "mean"), pnl=("pnl", "sum"), avg_spread=("spread_cents", "mean"), avg_qty=("visible_qty", "mean")).to_csv(tables / "05_candidate_side_summary.csv")
    reliability.to_csv(tables / "06_reliability_map_by_event.csv", index=False)
    q[["spread_cents", "yes_bid_qty", "yes_ask_qty", "bid_depth", "ask_depth", "book_imbalance", "quote_speed_cents", "micropressure", "btc_ret_1m_bps", "btc_ret_3m_bps", "btc_ret_5m_bps", "distance_bps"]].describe(percentiles=[0.01, 0.05, 0.5, 0.95, 0.99]).to_csv(tables / "07_feature_describe.csv")


def make_plots(q: pd.DataFrame, c: pd.DataFrame, summary: pd.DataFrame, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plots = out_dir / "plots"
    plots.mkdir(exist_ok=True)
    made: list[Path] = []

    def save(name: str) -> None:
        path = plots / f"{len(made)+1:02d}_{name}.png"
        plt.tight_layout()
        plt.savefig(path, dpi=140)
        plt.close()
        made.append(path)

    q.groupby("day").size().plot(kind="bar", title="Quote rows by day")
    save("quote_rows_by_day")
    q.groupby("day")["event_ticker"].nunique().plot(kind="bar", title="Events by day")
    save("events_by_day")
    q["spread_cents"].clip(0, 10).hist(bins=40)
    plt.title("Spread distribution")
    save("spread_hist")
    q.boxplot(column="spread_cents", by="ttl_bin", rot=45)
    plt.suptitle("")
    plt.title("Spread by TTL")
    save("spread_by_ttl")
    np.log1p(q["yes_bid_qty"].dropna()).hist(bins=40)
    plt.title("log1p YES bid top qty")
    save("yes_bid_qty")
    np.log1p(q["yes_ask_qty"].dropna()).hist(bins=40)
    plt.title("log1p YES ask top qty")
    save("yes_ask_qty")
    np.log1p(q["bid_depth"].dropna()).hist(bins=40)
    plt.title("log1p bid depth")
    save("bid_depth")
    np.log1p(q["ask_depth"].dropna()).hist(bins=40)
    plt.title("log1p ask depth")
    save("ask_depth")
    q["book_imbalance"].clip(-1, 1).hist(bins=50)
    plt.title("Depth imbalance")
    save("imbalance")
    q["prev_gap_sec"].clip(0, 600).hist(bins=50)
    plt.title("Previous snapshot gap seconds")
    save("gap_seconds")
    q.groupby("day")["data_quality_ok"].mean().plot(kind="bar", title="Quality-ok rate by day")
    save("quality_rate")
    q.groupby("day")[["yes_bid_qty", "yes_ask_qty"]].apply(lambda d: d.isna().mean()).plot(kind="bar", title="Missing top qty rate")
    save("missing_qty")
    q["quote_speed_cents"].clip(0, 50).hist(bins=50)
    plt.title("Quote speed cents")
    save("quote_speed")
    (q["micropressure"] * 100).clip(-5, 5).hist(bins=50)
    plt.title("Micropressure cents")
    save("micropressure")
    q["btc_ret_1m_bps"].clip(-30, 30).hist(bins=50)
    plt.title("BTC 1m return bps")
    save("btc_1m")
    q["btc_ret_3m_bps"].clip(-50, 50).hist(bins=50)
    plt.title("BTC 3m return bps")
    save("btc_3m")
    q["distance_bps"].clip(-80, 80).hist(bins=50)
    plt.title("Distance to target bps")
    save("distance_bps")
    c.groupby("entry_bucket", observed=False)["win"].mean().plot(kind="bar", title="Candidate win rate by entry bucket")
    save("win_by_entry")
    c.groupby("ttl_bin", observed=False)["pnl"].sum().plot(kind="bar", title="Candidate raw PnL by TTL bin")
    save("candidate_pnl_by_ttl")
    heat = q.groupby(["day", "hour"]).size().unstack(fill_value=0)
    plt.imshow(heat.to_numpy(), aspect="auto")
    plt.yticks(range(len(heat.index)), heat.index)
    plt.xticks(range(len(heat.columns)), heat.columns, rotation=90)
    plt.colorbar(label="rows")
    plt.title("Rows by day/hour")
    save("day_hour_heatmap")
    hold = summary[summary["split"].eq("holdout")].sort_values("pnl", ascending=False).head(15)
    hold.plot(kind="barh", x="strategy", y="pnl", legend=False, title="Top holdout PnL by strategy")
    save("top_holdout_pnl")
    return made


def make_pdf_report(out_dir: Path, data_report: dict[str, Any], summary: pd.DataFrame, failure: pd.DataFrame, plot_paths: list[Path]) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    path = out_dir / "btc15m_apr1_7_deepdive_report.pdf"
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(11, 8.5))
        ax = fig.add_subplot(111)
        ax.axis("off")
        txt = [
            "BTC15M Apr 1-7 Deep-Dive",
            f"Generated: {data_report['generated_at_utc']}",
            f"Rows: {data_report['feature_rows']:,}; events: {data_report['events']:,}",
            f"Study events: {data_report['study_events']:,}; holdout events: {data_report['holdout_events']:,}",
            "",
            "Executive result: no strategy is production-ready from this slice alone.",
            "Predexon validation is research-only; live WS replay remains required.",
        ]
        ax.text(0.03, 0.95, "\n".join(txt), va="top", fontsize=14)
        pdf.savefig(fig)
        plt.close(fig)

        hold = summary[summary["split"].eq("holdout")].sort_values(["pnl", "sharpe"], ascending=[False, False]).head(18)
        fig = plt.figure(figsize=(11, 8.5))
        ax = fig.add_subplot(111)
        ax.axis("off")
        cols = ["strategy", "trades", "pnl", "win_rate", "max_dd", "sharpe"]
        ax.text(0.01, 0.98, "Holdout Strategy Ranking", va="top", fontsize=13)
        ax.text(0.01, 0.92, hold[cols].round(4).to_string(index=False), va="top", family="monospace", fontsize=7)
        pdf.savefig(fig)
        plt.close(fig)

        fail = failure[["strategy", "pnl", "sharpe", "failure_reason"]].head(18)
        fig = plt.figure(figsize=(11, 8.5))
        ax = fig.add_subplot(111)
        ax.axis("off")
        ax.text(0.01, 0.98, "Failure Log", va="top", fontsize=13)
        ax.text(0.01, 0.92, fail.round(4).to_string(index=False), va="top", family="monospace", fontsize=6)
        pdf.savefig(fig)
        plt.close(fig)

        for p in plot_paths[:12]:
            img = plt.imread(p)
            fig = plt.figure(figsize=(11, 8.5))
            ax = fig.add_subplot(111)
            ax.imshow(img)
            ax.axis("off")
            ax.set_title(p.stem)
            pdf.savefig(fig)
            plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predexon-root", type=Path, default=DEFAULT_PREDEXON_ROOT)
    parser.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    parser.add_argument("--spot", type=Path, default=DEFAULT_SPOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--reuse-prepared", action="store_true")
    parser.add_argument("--n-perm", type=int, default=200)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    q, spot, data_report = prepare_features(args)
    data_report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    data_report["elapsed_prepare_sec"] = round(time.perf_counter() - started, 3)
    q.to_parquet(args.output_dir / "features.parquet", index=False, compression="zstd")
    reliability = reliability_map(q)
    reliability.to_csv(args.output_dir / "reliability_map.csv", index=False)
    candidates = make_side_candidates(q)
    candidates.to_parquet(args.output_dir / "side_candidates.parquet", index=False, compression="zstd")
    make_eda_tables(q, candidates, reliability, args.output_dir)
    summary, trades, failure = run_strategies(q, candidates, args.output_dir, args.n_perm)
    summary.to_csv(args.output_dir / "strategy_summary.csv", index=False)
    failure.to_csv(args.output_dir / "failure_log.csv", index=False)
    if not trades.empty:
        trades.to_csv(args.output_dir / "strategy_trades.csv", index=False)
        trades.to_parquet(args.output_dir / "strategy_trades.parquet", index=False, compression="zstd")
    plot_paths = make_plots(q, candidates, summary, args.output_dir)
    data_report["plot_count"] = len(plot_paths)
    data_report["strategies_tested"] = int(summary["strategy"].nunique()) if not summary.empty else 0
    data_report["elapsed_total_sec"] = round(time.perf_counter() - started, 3)
    (args.output_dir / "data_report.json").write_text(json.dumps(data_report, indent=2, default=str), encoding="utf-8")
    pdf = make_pdf_report(args.output_dir, data_report, summary, failure, plot_paths)

    top = summary[summary["split"].eq("holdout")].sort_values(["pnl", "sharpe"], ascending=[False, False]).head(12)
    lines = [
        "# BTC15M Apr 1-7 Deep-Dive Results",
        "",
        f"Generated: `{data_report['generated_at_utc']}`",
        f"Pre-registration: `docs/2026-05-14_btc15m_apr1_7_preregistration.md`",
        f"PDF report: `{pdf}`",
        f"Rows: `{data_report['feature_rows']:,}`; events: `{data_report['events']:,}`; plots: `{len(plot_paths)}`.",
        "",
        "## Holdout Top Rows",
        "",
        "```\n" + top[["strategy", "family", "trades", "pnl", "win_rate", "max_dd", "sharpe", "perm_p_holdout", "bonferroni_p_holdout"]].round(4).to_string(index=False) + "\n```",
        "",
        "## Conclusion",
        "",
        "No strategy is production-ready from this slice alone unless it passes the failure log and then live websocket replay. See `failure_log.csv`.",
    ]
    (args.output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(top[["strategy", "family", "trades", "pnl", "win_rate", "max_dd", "sharpe", "perm_p_holdout", "bonferroni_p_holdout"]].round(4).to_string(index=False))
    print(f"Wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
