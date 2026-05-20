#!/usr/bin/env python3
"""Replay the frozen BTC15M F2 rule on our live websocket capture.

F2 is frozen from the Apr1-4 Predexon research:

    side_fair_p >= 0.60 and fair_edge_cents >= 12

This script uses only locally captured websocket data.  It does not query
Kalshi.  Official results are taken from captured lifecycle `determined`
messages when available; otherwise a clearly labelled Coinbase-at-close proxy
is used for broader coverage.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars


DEFAULT_CAPTURE_DB = Path(os.path.expanduser("~/.btc_kalshi_bot/btc15m_live_capture.duckdb"))
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_f2_live_ws_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0


def normal_sf(z: np.ndarray) -> np.ndarray:
    vec = np.vectorize(lambda x: 0.5 * math.erfc(float(x) / math.sqrt(2.0)))
    return vec(z)


def max_drawdown(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def fee(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def fee_array(price: pd.Series | np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(price, dtype=float), 0.0, 1.0)
    raw = 0.07 * p * (1.0 - p)
    return np.ceil((raw - 1e-12) * 100.0) / 100.0


def load_capture(db_path: Path, start: str | None, end: str | None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    last_exc: Exception | None = None
    con = None
    for _ in range(120):
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            break
        except duckdb.IOException as exc:
            last_exc = exc
            time.sleep(0.25)
    if con is None:
        raise RuntimeError(f"could not open capture DB read-only after retries: {last_exc!r}")
    max_ts = con.execute(
        """
        SELECT max(TRY_CAST(received_at_utc AS TIMESTAMPTZ))
        FROM ws_orderbook_top
        WHERE event_ticker LIKE 'KXBTC15M-%'
        """
    ).fetchone()[0]
    min_ts = con.execute(
        """
        SELECT min(TRY_CAST(received_at_utc AS TIMESTAMPTZ))
        FROM ws_orderbook_top
        WHERE event_ticker LIKE 'KXBTC15M-%'
        """
    ).fetchone()[0]
    if max_ts is None or min_ts is None:
        raise RuntimeError("no KXBTC15M rows in ws_orderbook_top")
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp(max_ts).tz_convert("UTC")
    start_ts = pd.Timestamp(start, tz="UTC") if start else pd.Timestamp(min_ts).tz_convert("UTC")
    if start_ts >= end_ts:
        raise ValueError(f"start must be before end: {start_ts} >= {end_ts}")

    top = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               market_ticker, event_ticker,
               sid, seq,
               yes_bid, yes_bid_qty, yes_ask, yes_ask_qty,
               no_bid, no_bid_qty, no_ask, no_ask_qty,
               btc_spot, source
        FROM ws_orderbook_top
        WHERE event_ticker LIKE 'KXBTC15M-%'
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?
        ORDER BY received_at_ns, seq
        """,
        [start_ts.to_pydatetime(), end_ts.to_pydatetime()],
    ).fetchdf()
    btc = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               price, best_bid, best_ask, sequence
        FROM coinbase_ticker
        WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?
        ORDER BY received_at_ns
        """,
        [(start_ts - pd.Timedelta(hours=2)).to_pydatetime(), (end_ts + pd.Timedelta(minutes=5)).to_pydatetime()],
    ).fetchdf()
    lifecycle = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               message_type, event_type, event_ticker, market_ticker, payload_json
        FROM ws_lifecycle
        WHERE event_ticker LIKE 'KXBTC15M-%'
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?
        ORDER BY received_at_ns
        """,
        [(start_ts - pd.Timedelta(hours=1)).to_pydatetime(), (end_ts + pd.Timedelta(minutes=10)).to_pydatetime()],
    ).fetchdf()
    decisions = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               mode, action, event_ticker, market_ticker, side, contracts,
               entry_price, net_edge_cents, btc_spot, detail
        FROM order_decision
        WHERE event_ticker LIKE 'KXBTC15M-%'
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?
          AND TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?
        ORDER BY received_at_ns
        """,
        [start_ts.to_pydatetime(), end_ts.to_pydatetime()],
    ).fetchdf()
    con.close()
    for df in [top, btc, lifecycle, decisions]:
        if "received_at_utc" in df.columns:
            df["received_at_utc"] = pd.to_datetime(df["received_at_utc"], utc=True, errors="coerce")
    info = {
        "capture_db": str(db_path),
        "capture_start_utc": str(start_ts),
        "capture_end_utc": str(end_ts),
        "top_rows": int(len(top)),
        "btc_rows": int(len(btc)),
        "lifecycle_rows": int(len(lifecycle)),
        "decision_rows": int(len(decisions)),
    }
    return top, btc, lifecycle, decisions, info


def metadata_from_lifecycle(lifecycle: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    meta_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    for row in lifecycle.itertuples(index=False):
        payload = getattr(row, "payload_json", None)
        if not payload:
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        event_type = str(getattr(row, "event_type", "") or "")
        if event_type == "determined":
            result = str(obj.get("result") or "").lower()
            if result in {"yes", "no"}:
                result_rows.append(
                    {
                        "event_ticker": getattr(row, "event_ticker"),
                        "market_ticker": getattr(row, "market_ticker"),
                        "official_result": result,
                        "determined_at_utc": getattr(row, "received_at_utc"),
                    }
                )
        event = obj.get("event") if isinstance(obj, dict) else None
        markets = []
        if isinstance(event, dict):
            markets.extend(event.get("markets") or [])
        market = obj.get("market") if isinstance(obj, dict) else None
        if isinstance(market, dict):
            markets.append(market)
        for mk in markets:
            if not isinstance(mk, dict):
                continue
            ticker = str(mk.get("ticker") or getattr(row, "market_ticker") or "").upper()
            if not ticker.startswith("KXBTC15M-"):
                continue
            meta_rows.append(
                {
                    "event_ticker": str(mk.get("event_ticker") or getattr(row, "event_ticker") or "").upper(),
                    "market_ticker": ticker,
                    "floor_strike": pd.to_numeric(mk.get("floor_strike"), errors="coerce"),
                    "open_time": mk.get("open_time"),
                    "close_time": mk.get("close_time") or (event or {}).get("close_time"),
                    "status": mk.get("status"),
                    "result_in_refresh": str(mk.get("result") or "").lower(),
                    "meta_received_at_utc": getattr(row, "received_at_utc"),
                }
            )
    meta = pd.DataFrame(meta_rows)
    if not meta.empty:
        meta["open_time"] = pd.to_datetime(meta["open_time"], utc=True, errors="coerce")
        meta["close_time"] = pd.to_datetime(meta["close_time"], utc=True, errors="coerce")
        meta = (
            meta.dropna(subset=["event_ticker", "market_ticker", "floor_strike", "close_time"])
            .sort_values(["event_ticker", "market_ticker", "meta_received_at_utc"])
            .drop_duplicates(["event_ticker", "market_ticker"], keep="last")
            .reset_index(drop=True)
        )
    results = pd.DataFrame(result_rows)
    if not results.empty:
        results = (
            results.sort_values(["event_ticker", "market_ticker", "determined_at_utc"])
            .drop_duplicates(["event_ticker", "market_ticker"], keep="last")
            .reset_index(drop=True)
        )
    return meta, results


def add_btc_vol(btc: pd.DataFrame) -> pd.DataFrame:
    s = btc.dropna(subset=["received_at_utc", "price"]).copy()
    s["received_at_utc"] = pd.to_datetime(s["received_at_utc"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    s["price"] = pd.to_numeric(s["price"], errors="coerce")
    s = s.dropna(subset=["price"]).sort_values("received_at_utc")
    if s.empty:
        return s
    minute = (
        s.set_index("received_at_utc")["price"]
        .resample("1min")
        .last()
        .ffill()
        .dropna()
        .reset_index()
        .rename(columns={"received_at_utc": "btc_minute", "price": "btc_close"})
    )
    returns = np.log(minute["btc_close"].astype(float)).diff()
    minute["rv_60m"] = returns.rolling(60, min_periods=20).std() * math.sqrt(MINUTES_PER_YEAR)
    return minute


def add_btc_rolling_60s(btc: pd.DataFrame) -> pd.DataFrame:
    b = btc.dropna(subset=["received_at_utc", "price"]).copy()
    b["received_at_utc"] = pd.to_datetime(b["received_at_utc"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    b["price"] = pd.to_numeric(b["price"], errors="coerce")
    b = b.dropna(subset=["received_at_utc", "price"]).sort_values("received_at_utc")
    if b.empty:
        return b[["received_at_utc", "price"]].assign(btc_roll60=np.nan)
    roll = (
        b.set_index("received_at_utc")["price"]
        .rolling("60s", min_periods=1)
        .mean()
        .reset_index()
        .rename(columns={"price": "btc_roll60"})
    )
    return b.merge(roll, on="received_at_utc", how="left")


def prepare_quotes(top: pd.DataFrame, btc: pd.DataFrame, meta: pd.DataFrame, results: pd.DataFrame, btc_model: str) -> pd.DataFrame:
    q = top.copy()
    q["received_at_utc"] = pd.to_datetime(q["received_at_utc"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    meta = meta.copy()
    meta["close_time"] = pd.to_datetime(meta["close_time"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    meta["open_time"] = pd.to_datetime(meta["open_time"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    for col in [
        "yes_bid",
        "yes_bid_qty",
        "yes_ask",
        "yes_ask_qty",
        "no_bid",
        "no_bid_qty",
        "no_ask",
        "no_ask_qty",
        "btc_spot",
    ]:
        q[col] = pd.to_numeric(q[col], errors="coerce")
    q = q.dropna(subset=["received_at_utc", "market_ticker", "event_ticker", "yes_bid", "yes_ask", "no_bid", "no_ask", "btc_spot"])
    q = q.merge(meta[["event_ticker", "market_ticker", "floor_strike", "open_time", "close_time", "status"]], on=["event_ticker", "market_ticker"], how="inner")
    q = q[q["received_at_utc"] < q["close_time"]].copy()
    q["ttl_min"] = (q["close_time"] - q["received_at_utc"]).dt.total_seconds() / 60.0
    q = q[q["ttl_min"].gt(0)].copy()
    q["yes_mid"] = (q["yes_bid"] + q["yes_ask"]) / 2.0
    q = q.sort_values(["market_ticker", "received_at_ns", "seq"]).reset_index(drop=True)
    q["quote_speed_cents"] = (
        q.groupby("market_ticker", sort=False)["yes_mid"]
        .diff()
        .abs()
        .mul(100.0)
        .fillna(0.0)
    )
    q["spread_cents"] = (q["yes_ask"] - q["yes_bid"]) * 100.0
    # BTC tick age and 60m realized vol, both causal/as-of.
    b = add_btc_rolling_60s(btc)
    q = pd.merge_asof(
        q.sort_values("received_at_utc"),
        b[["received_at_utc", "price", "btc_roll60"]].rename(columns={"received_at_utc": "btc_time", "price": "btc_asof"}),
        left_on="received_at_utc",
        right_on="btc_time",
        direction="backward",
    )
    q["btc_spot_age_sec"] = (q["received_at_utc"] - q["btc_time"]).dt.total_seconds()
    if btc_model == "rolling60":
        q["btc_spot_model"] = q["btc_roll60"].fillna(q["btc_asof"]).fillna(q["btc_spot"])
    else:
        q["btc_spot_model"] = q["btc_asof"].fillna(q["btc_spot"])
    minute = add_btc_vol(btc)
    if not minute.empty:
        q = pd.merge_asof(
            q.sort_values("received_at_utc"),
            minute[["btc_minute", "rv_60m"]],
            left_on="received_at_utc",
            right_on="btc_minute",
            direction="backward",
        )
    else:
        q["rv_60m"] = np.nan
    q["rv_60m"] = pd.to_numeric(q["rv_60m"], errors="coerce").fillna(0.50).clip(lower=0.05, upper=3.0)
    ttl = q["ttl_min"].clip(lower=0.1)
    denom = q["rv_60m"] * np.sqrt(ttl / MINUTES_PER_YEAR)
    z = np.log(pd.to_numeric(q["floor_strike"], errors="coerce") / pd.to_numeric(q["btc_spot_model"], errors="coerce")) / denom.replace(0.0, np.nan)
    q["lognormal_p_yes"] = np.clip(normal_sf(z.to_numpy(dtype=float)), 0.001, 0.999)
    if not results.empty:
        q = q.merge(results[["event_ticker", "market_ticker", "official_result"]], on=["event_ticker", "market_ticker"], how="left")
    else:
        q["official_result"] = np.nan
    return q.sort_values(["event_ticker", "received_at_ns", "seq"]).reset_index(drop=True)


def add_proxy_results(q: pd.DataFrame, btc: pd.DataFrame, btc_model: str) -> pd.DataFrame:
    events = q[["event_ticker", "market_ticker", "floor_strike", "close_time"]].drop_duplicates().sort_values("close_time")
    events["close_time"] = pd.to_datetime(events["close_time"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    b = add_btc_rolling_60s(btc)
    close_col = "btc_roll60" if btc_model == "rolling60" else "price"
    close_px = pd.merge_asof(
        events.sort_values("close_time"),
        b[["received_at_utc", close_col]].rename(columns={"received_at_utc": "close_btc_time", close_col: "close_btc_spot"}),
        left_on="close_time",
        right_on="close_btc_time",
        direction="forward",
        tolerance=pd.Timedelta(minutes=2),
    )
    fallback = close_px["close_btc_spot"].isna()
    if fallback.any():
        back = pd.merge_asof(
            close_px.loc[fallback, ["event_ticker", "market_ticker", "floor_strike", "close_time"]].sort_values("close_time"),
            b[["received_at_utc", close_col]].rename(columns={"received_at_utc": "close_btc_time_back", close_col: "close_btc_spot_back"}),
            left_on="close_time",
            right_on="close_btc_time_back",
            direction="backward",
            tolerance=pd.Timedelta(minutes=2),
        )
        fallback_index = close_px.index[fallback]
        close_px.loc[fallback_index, "close_btc_spot"] = back["close_btc_spot_back"].to_numpy()
        close_px.loc[fallback_index, "close_btc_time"] = pd.to_datetime(
            back["close_btc_time_back"],
            utc=True,
            errors="coerce",
        ).array
    valid_proxy = close_px["close_btc_spot"].notna() & close_px["floor_strike"].notna()
    close_px["proxy_result"] = pd.Series(np.nan, index=close_px.index, dtype=object)
    close_px.loc[valid_proxy, "proxy_result"] = np.where(
        close_px.loc[valid_proxy, "close_btc_spot"].ge(close_px.loc[valid_proxy, "floor_strike"]),
        "yes",
        "no",
    )
    close_px["proxy_distance_usd"] = close_px["close_btc_spot"] - close_px["floor_strike"]
    close_px["near_strike_proxy"] = close_px["proxy_distance_usd"].abs().le(10.0)
    return q.merge(
        close_px[
            [
                "event_ticker",
                "market_ticker",
                "close_btc_spot",
                "close_btc_time",
                "proxy_result",
                "proxy_distance_usd",
                "near_strike_proxy",
            ]
        ],
        on=["event_ticker", "market_ticker"],
        how="left",
    )


def make_side_candidates(q: pd.DataFrame) -> pd.DataFrame:
    base = q[q["btc_spot_age_sec"].between(0, 120) & q["floor_strike"].notna() & q["close_time"].notna()].copy()
    frames = []
    for side in ["yes", "no"]:
        t = base.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
            t["side_fair_p"] = t["lognormal_p_yes"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
            t["side_fair_p"] = 1.0 - t["lognormal_p_yes"].astype(float)
        t = t[t["entry_price"].between(0.01, 0.99) & t["visible_qty"].fillna(0).ge(1)].copy()
        t["entry_fee"] = t["entry_price"].map(fee)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["fair_edge_cents"] = (t["side_fair_p"] - t["entry_price"]) * 100.0 - t["entry_fee"] * 100.0
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    c = pd.concat(frames, ignore_index=True)
    return c.sort_values(["event_ticker", "received_at_ns", "fair_edge_cents", "seq", "side"], ascending=[True, True, False, True, True]).reset_index(drop=True)


def first_f2(c: pd.DataFrame) -> pd.DataFrame:
    if c.empty:
        return c.copy()
    selected = c[c["side_fair_p"].ge(0.60) & c["fair_edge_cents"].ge(12.0)].copy()
    if selected.empty:
        return selected
    return (
        selected.sort_values(["event_ticker", "received_at_ns", "fair_edge_cents", "seq", "side"], ascending=[True, True, False, True, True])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def select_f2_trades(
    q: pd.DataFrame,
    fair_p_min: float,
    edge_cents_min: float,
    ttl_min: float,
    ttl_max: float,
    spread_max_cents: float,
    entry_min: float,
    entry_max: float,
    visible_qty_min: float,
    side: str = "both",
    first_signal_visible_qty_min: float | None = None,
    quote_speed_min: float | None = None,
    quote_speed_max: float | None = None,
    max_btc_spot_age_sec: float = 10.0,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Materialize only rows that pass a frozen fair-value rule."""
    p_yes = pd.to_numeric(q["lognormal_p_yes"], errors="coerce").to_numpy(dtype=float)
    yes_entry = pd.to_numeric(q["yes_ask"], errors="coerce").to_numpy(dtype=float)
    no_entry = pd.to_numeric(q["no_ask"], errors="coerce").to_numpy(dtype=float)
    yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce").to_numpy(dtype=float)
    no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce").to_numpy(dtype=float)
    yes_fee = fee_array(yes_entry)
    no_fee = fee_array(no_entry)
    yes_edge = (p_yes - yes_entry) * 100.0 - yes_fee * 100.0
    no_p = 1.0 - p_yes
    no_edge = (no_p - no_entry) * 100.0 - no_fee * 100.0
    yes_mask = (
        np.isfinite(p_yes)
        & (p_yes >= fair_p_min)
        & np.isfinite(yes_edge)
        & (yes_edge >= edge_cents_min)
        & (yes_entry >= entry_min)
        & (yes_entry <= entry_max)
        & (yes_qty >= visible_qty_min)
    )
    no_mask = (
        np.isfinite(no_p)
        & (no_p >= fair_p_min)
        & np.isfinite(no_edge)
        & (no_edge >= edge_cents_min)
        & (no_entry >= entry_min)
        & (no_entry <= entry_max)
        & (no_qty >= visible_qty_min)
    )
    common = (
        pd.to_numeric(q["ttl_min"], errors="coerce").between(ttl_min, ttl_max).to_numpy()
        & (pd.to_numeric(q["spread_cents"], errors="coerce").to_numpy(dtype=float) <= spread_max_cents)
    )
    btc_spot_age = pd.to_numeric(q.get("btc_spot_age_sec", pd.Series(index=q.index, dtype=float)), errors="coerce").to_numpy(dtype=float)
    common &= np.isfinite(btc_spot_age) & (btc_spot_age >= 0.0) & (btc_spot_age <= float(max_btc_spot_age_sec))
    quote_speed = pd.to_numeric(q.get("quote_speed_cents", pd.Series(index=q.index, dtype=float)), errors="coerce").to_numpy(dtype=float)
    if quote_speed_min is not None:
        common &= np.isfinite(quote_speed) & (quote_speed >= float(quote_speed_min))
    if quote_speed_max is not None:
        common &= np.isfinite(quote_speed) & (quote_speed <= float(quote_speed_max))
    yes_mask &= common
    no_mask &= common
    if side == "yes":
        no_mask &= False
    elif side == "no":
        yes_mask &= False
    elif side != "both":
        raise ValueError("--side must be one of: both, yes, no")
    base_cols = [
        "received_at_ns",
        "received_at_utc",
        "event_ticker",
        "market_ticker",
        "seq",
        "yes_ask",
        "yes_ask_qty",
        "no_ask",
        "no_ask_qty",
        "spread_cents",
        "btc_spot_model",
        "btc_spot_age_sec",
        "rv_60m",
        "quote_speed_cents",
        "floor_strike",
        "close_time",
        "official_result",
        "close_btc_spot",
        "close_btc_time",
        "proxy_result",
        "proxy_distance_usd",
        "near_strike_proxy",
    ]
    frames = []
    if yes_mask.any():
        y = q.loc[yes_mask, base_cols].copy()
        y["side"] = "yes"
        y["entry_price"] = yes_entry[yes_mask]
        y["visible_qty"] = yes_qty[yes_mask]
        y["side_fair_p"] = p_yes[yes_mask]
        y["entry_fee"] = yes_fee[yes_mask]
        y["fair_edge_cents"] = yes_edge[yes_mask]
        y["opp_entry"] = no_entry[yes_mask]
        frames.append(y)
    if no_mask.any():
        n = q.loc[no_mask, base_cols].copy()
        n["side"] = "no"
        n["entry_price"] = no_entry[no_mask]
        n["visible_qty"] = no_qty[no_mask]
        n["side_fair_p"] = no_p[no_mask]
        n["entry_fee"] = no_fee[no_mask]
        n["fair_edge_cents"] = no_edge[no_mask]
        n["opp_entry"] = yes_entry[no_mask]
        frames.append(n)
    if not frames:
        signal_cols = [
            "side",
            "entry_price",
            "visible_qty",
            "side_fair_p",
            "entry_fee",
            "fair_edge_cents",
            "opp_entry",
            "premium",
        ]
        return pd.DataFrame(columns=[*base_cols, *signal_cols]), {
            "yes_hits": 0,
            "no_hits": 0,
            "raw_hits": 0,
            "first_signals": 0,
            "first_signal_qty_rejects": 0,
        }
    hits = pd.concat(frames, ignore_index=True)
    hits["premium"] = hits["entry_price"] + hits["entry_fee"]
    hits = hits.sort_values(
        ["event_ticker", "received_at_ns", "fair_edge_cents", "seq", "side"],
        ascending=[True, True, False, True, True],
    )
    first_signals = hits.drop_duplicates("event_ticker", keep="first").reset_index(drop=True)
    rejected_first_signal_qty = 0
    if first_signal_visible_qty_min is not None and first_signal_visible_qty_min > visible_qty_min:
        keep = pd.to_numeric(first_signals["visible_qty"], errors="coerce").ge(float(first_signal_visible_qty_min))
        rejected_first_signal_qty = int((~keep).sum())
        trades = first_signals.loc[keep].reset_index(drop=True)
    else:
        trades = first_signals
    return trades, {
        "yes_hits": int(yes_mask.sum()),
        "no_hits": int(no_mask.sum()),
        "raw_hits": int(len(hits)),
        "first_signals": int(len(first_signals)),
        "first_signal_qty_rejects": rejected_first_signal_qty,
    }


def add_pnl(trades: pd.DataFrame, result_col: str, out_col: str, slip_cents: int = 0) -> pd.DataFrame:
    out = trades.copy()
    result = out[result_col].astype(str).str.lower()
    valid = result.isin(["yes", "no"])
    entry = (pd.to_numeric(out["entry_price"], errors="coerce") + slip_cents / 100.0).clip(upper=0.99)
    entry_fee = entry.map(fee)
    win = result.eq(out["side"].astype(str).str.lower()) & valid
    out[f"win_{out_col}"] = np.where(valid, win, np.nan)
    out[out_col] = np.where(valid & win, 1.0 - entry - entry_fee, np.where(valid, -(entry + entry_fee), np.nan))
    return out


def metrics(trades: pd.DataFrame, pnl_col: str) -> dict[str, Any]:
    pnl = pd.to_numeric(trades[pnl_col], errors="coerce").dropna()
    premium = pd.to_numeric(trades.loc[pnl.index, "premium"], errors="coerce") if len(pnl) else pd.Series(dtype=float)
    wins = pd.to_numeric(trades.loc[pnl.index, f"win_{pnl_col}"], errors="coerce") if f"win_{pnl_col}" in trades.columns and len(pnl) else pd.Series(dtype=float)
    return {
        "trades": int(len(pnl)),
        "pnl": round(float(pnl.sum()), 4) if len(pnl) else 0.0,
        "return_on_100": round(float(pnl.sum()), 4) if len(pnl) else 0.0,
        "premium": round(float(premium.sum()), 4) if len(premium) else 0.0,
        "rop": round(float(pnl.sum() / premium.sum()), 4) if len(pnl) and float(premium.sum()) > 0 else 0.0,
        "win_rate": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
    }


def daily(trades: pd.DataFrame, pnl_col: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    work = trades.copy()
    work["day"] = pd.to_datetime(work["close_time"], utc=True).dt.strftime("%Y-%m-%d")
    for day, g in work.groupby("day", dropna=False):
        row = {"day": day}
        row.update(metrics(g, pnl_col))
        rows.append(row)
    return pd.DataFrame(rows)


def compare_official_proxy(trades: pd.DataFrame) -> dict[str, Any]:
    both = trades[trades["official_result"].astype(str).str.lower().isin(["yes", "no"]) & trades["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
    if both.empty:
        return {"events_with_both": 0, "matches": 0, "mismatches": 0, "match_rate": None}
    match = both["official_result"].astype(str).str.lower().eq(both["proxy_result"].astype(str).str.lower())
    return {
        "events_with_both": int(len(both)),
        "matches": int(match.sum()),
        "mismatches": int((~match).sum()),
        "match_rate": round(float(match.mean()), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--fair-p-min", type=float, default=0.60)
    ap.add_argument("--edge-cents-min", type=float, default=12.0)
    ap.add_argument("--ttl-min", type=float, default=0.0)
    ap.add_argument("--ttl-max", type=float, default=999.0)
    ap.add_argument("--spread-max-cents", type=float, default=999.0)
    ap.add_argument("--entry-min", type=float, default=0.01)
    ap.add_argument("--entry-max", type=float, default=0.99)
    ap.add_argument("--visible-qty-min", type=float, default=1.0)
    ap.add_argument("--side", choices=["both", "yes", "no"], default="both")
    ap.add_argument(
        "--first-signal-visible-qty-min",
        type=float,
        help=(
            "If set above --visible-qty-min, first choose the first base signal per event, "
            "then reject that event unless the first signal has this visible top-ask quantity. "
            "This matches the q250 qty500 first-skip shadow."
        ),
    )
    ap.add_argument("--quote-speed-min", type=float)
    ap.add_argument("--quote-speed-max", type=float)
    ap.add_argument("--btc-model", choices=["spot", "rolling60"], default="spot")
    ap.add_argument(
        "--max-btc-spot-age-sec",
        type=float,
        default=10.0,
        help="Maximum age of the causal BTC spot tick used for decisions. Default matches BTC15M_H02_BTC_MAX_AGE_SEC.",
    )
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    top, btc, lifecycle, decisions, info = load_capture(args.capture_db, args.start, args.end)
    meta, official_results = metadata_from_lifecycle(lifecycle)
    info.update(
        {
            "meta_markets": int(len(meta)),
            "official_result_markets": int(len(official_results)),
            "meta_first_close": str(meta["close_time"].min()) if not meta.empty else "",
            "meta_last_close": str(meta["close_time"].max()) if not meta.empty else "",
        }
    )
    q = prepare_quotes(top, btc, meta, official_results, args.btc_model)
    q = add_proxy_results(q, btc, args.btc_model)
    capture_end = pd.Timestamp(info["capture_end_utc"])
    # Exclude events that have not closed or lack proxy settlement before
    # materializing side candidates.
    q = q[q["close_time"].le(capture_end) & q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
    trades, hit_info = select_f2_trades(
        q,
        fair_p_min=args.fair_p_min,
        edge_cents_min=args.edge_cents_min,
        ttl_min=args.ttl_min,
        ttl_max=args.ttl_max,
        spread_max_cents=args.spread_max_cents,
        entry_min=args.entry_min,
        entry_max=args.entry_max,
        visible_qty_min=args.visible_qty_min,
        side=args.side,
        first_signal_visible_qty_min=args.first_signal_visible_qty_min,
        quote_speed_min=args.quote_speed_min,
        quote_speed_max=args.quote_speed_max,
        max_btc_spot_age_sec=args.max_btc_spot_age_sec,
    )
    trades = add_pnl(trades, "proxy_result", "pnl_proxy_0c", 0)
    trades = add_pnl(trades, "proxy_result", "pnl_proxy_2c", 2)
    trades = add_pnl(trades, "official_result", "pnl_official_0c", 0)
    trades = add_pnl(trades, "official_result", "pnl_official_2c", 2)
    trades["want_opp"] = np.where(trades["side"].eq("yes"), "no", "yes")
    valid_opp = trades["opp_entry"].notna()
    opp_win = trades["proxy_result"].astype(str).str.lower().eq(trades["want_opp"])
    trades["opp_pnl_proxy_2c"] = np.where(
        valid_opp & opp_win,
        1.0 - (trades["opp_entry"] + 0.02).clip(upper=0.99) - (trades["opp_entry"] + 0.02).clip(upper=0.99).map(fee),
        np.where(valid_opp, -((trades["opp_entry"] + 0.02).clip(upper=0.99) + (trades["opp_entry"] + 0.02).clip(upper=0.99).map(fee)), np.nan),
    )

    summary_rows = []
    for label, col in [
        ("proxy_0c", "pnl_proxy_0c"),
        ("proxy_2c", "pnl_proxy_2c"),
        ("official_0c_subset", "pnl_official_0c"),
        ("official_2c_subset", "pnl_official_2c"),
        ("opposite_proxy_2c", "opp_pnl_proxy_2c"),
    ]:
        row = {"result_mode": label}
        row.update(metrics(trades, col))
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    day_proxy = daily(trades, "pnl_proxy_2c")
    official_day = daily(trades[trades["official_result"].astype(str).str.lower().isin(["yes", "no"])], "pnl_official_2c")
    compare = compare_official_proxy(trades)
    info.update(
        {
            "quote_rows_after_meta": int(len(q)),
            "f2_yes_raw_hits": int(hit_info.get("yes_hits", 0)),
            "f2_no_raw_hits": int(hit_info.get("no_hits", 0)),
            "f2_raw_hits": int(hit_info.get("raw_hits", 0)),
            "f2_first_signals": int(hit_info.get("first_signals", 0)),
            "f2_first_signal_qty_rejects": int(hit_info.get("first_signal_qty_rejects", 0)),
            "f2_signals_closed_with_proxy": int(len(trades)),
            "f2_official_result_signals": int(trades["official_result"].astype(str).str.lower().isin(["yes", "no"]).sum()),
            "near_strike_proxy_signals_abs_le_10_usd": int(trades["near_strike_proxy"].fillna(False).sum()),
            "fair_p_min": args.fair_p_min,
            "edge_cents_min": args.edge_cents_min,
            "ttl_min": args.ttl_min,
            "ttl_max": args.ttl_max,
            "spread_max_cents": args.spread_max_cents,
            "entry_min": args.entry_min,
            "entry_max": args.entry_max,
            "visible_qty_min": args.visible_qty_min,
            "side": args.side,
            "first_signal_visible_qty_min": args.first_signal_visible_qty_min,
            "quote_speed_min": args.quote_speed_min,
            "quote_speed_max": args.quote_speed_max,
            "btc_model": args.btc_model,
            "max_btc_spot_age_sec": args.max_btc_spot_age_sec,
        }
    )
    info.update({f"official_proxy_{k}": v for k, v in compare.items()})

    trades.to_parquet(args.out / "f2_live_ws_trades.parquet", index=False, compression="zstd")
    summary.to_csv(args.out / "f2_live_ws_summary.csv", index=False)
    day_proxy.to_csv(args.out / "f2_live_ws_daily_proxy_2c.csv", index=False)
    official_day.to_csv(args.out / "f2_live_ws_daily_official_2c.csv", index=False)
    decisions.to_csv(args.out / "captured_order_decisions.csv", index=False)
    (args.out / "run_info.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")

    print(json.dumps(info, indent=2, default=str))
    print("\nSUMMARY")
    print(summary.to_string(index=False))
    print("\nDAILY proxy +2c")
    print(day_proxy.to_string(index=False))
    print("\nOFFICIAL subset daily +2c")
    print(official_day.to_string(index=False))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
