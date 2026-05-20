#!/usr/bin/env python3
"""Compare BTC15M Predexon snapshots against our local websocket replay.

The goal is not to make Predexon look good or bad. It is to answer a narrower
question: on the exact time/market overlap, do strategies make similar
decisions when the execution model is held constant?

Outputs include:

* Predexon snapshots scored directly.
* Local websocket top-of-book sampled at the same Predexon timestamps.
* Local websocket dense rows for the same markets as a diagnostic only.

The execution model is intentionally simple and identical for both sources:
one taker contract, top ask only, visible top quantity >= 1, Kalshi taker fee,
official Kalshi settlement, one first signal per event where applicable. There
is no candle high/low and no future quote selection.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
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
from scripts.backtest_btc15m_live_holdout import (  # noqa: E402
    cheap_pair_lock,
    cheap_tail_candidates,
    cheap_tail_position_aware,
    cheap_tail_side,
    first_per_event,
    markdown_or_text,
    summarize,
)
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402
from scripts.backtest_predexon_orderbooks import (  # noqa: E402
    DEFAULT_ROOT as DEFAULT_PREDEXON_ROOT,
    attach_sizes,
    load_level0_sizes,
    load_market_meta,
    load_predexon_top,
    max_drawdown_from_pnl,
    prepare_quotes,
)
from scripts.btc_1hr_research_live import KalshiApi  # noqa: E402


DEFAULT_START = "2026-05-12T10:42:45Z"
DEFAULT_END = "2026-05-12T22:29:02.423Z"
DEFAULT_CAPTURE_DB = Path.home() / ".btc_kalshi_bot" / "btc15m_live_capture.duckdb"
DEFAULT_SNAPSHOT_DB = PROJECT_ROOT / "backtest_outputs" / "btc15m_overlap_check_snapshot.duckdb"
DEFAULT_OUT_DIR = PROJECT_ROOT / "backtest_outputs" / f"btc15m_predexon_ws_overlap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_ts(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc_series(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def datetime_ns(values: Any) -> np.ndarray:
    return utc_series(values).astype("datetime64[ns, UTC]").astype("int64").to_numpy()


def snapshot_capture_db(source: Path, target: Path, reuse_existing: bool) -> Path:
    if reuse_existing and target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.duckdb")
    if tmp.exists():
        tmp.unlink()
    last_exc: Exception | None = None
    for _ in range(120):
        try:
            shutil.copy2(source, tmp)
            if target.exists():
                target.unlink()
            tmp.replace(target)
            return target
        except (OSError, PermissionError) as exc:
            last_exc = exc
            time.sleep(0.25)
    raise RuntimeError(f"could not snapshot {source} to {target}: {last_exc!r}")


def load_ws(snapshot: Path, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(snapshot), read_only=True)
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
    con.close()
    for df in (top, btc):
        df["received_at_utc"] = utc_series(df["received_at_utc"])
    for col in ["yes_bid", "yes_bid_qty", "yes_ask", "yes_ask_qty", "no_bid", "no_bid_qty", "no_ask", "no_ask_qty"]:
        if col in top:
            top[col] = pd.to_numeric(top[col], errors="coerce")
    if "price" in btc:
        btc["price"] = pd.to_numeric(btc["price"], errors="coerce")
    return top, btc


def load_predexon(root: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    top = load_predexon_top(root, start, end + pd.Timedelta(milliseconds=1), ["KXBTC15M"])
    sizes = load_level0_sizes(root, start, end + pd.Timedelta(milliseconds=1), ["KXBTC15M"])
    top = attach_sizes(top, sizes)
    meta = load_market_meta(root)
    q = prepare_quotes(top, meta)
    if q.empty:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "received_at_ns": datetime_ns(q["available_at"]),
            "received_at_utc": utc_series(q["available_at"]),
            "market_ticker": q["market_ticker"].astype(str).str.upper(),
            "event_ticker": q["event_ticker"].astype(str).str.upper(),
            "yes_bid": pd.to_numeric(q["yes_bid_close"], errors="coerce"),
            "yes_ask": pd.to_numeric(q["yes_ask_close"], errors="coerce"),
            "yes_bid_qty": pd.to_numeric(q.get("yes_bid_qty", np.nan), errors="coerce"),
            "yes_ask_qty": pd.to_numeric(q.get("yes_ask_qty", np.nan), errors="coerce"),
            "btc_spot": np.nan,
            "source": "predexon_snapshot",
            "sequence": pd.to_numeric(q.get("sequence", np.nan), errors="coerce"),
        }
    )
    out["no_bid"] = (1.0 - out["yes_ask"]).clip(0.0, 1.0)
    out["no_ask"] = (1.0 - out["yes_bid"]).clip(0.0, 1.0)
    out["no_bid_qty"] = out["yes_ask_qty"]
    out["no_ask_qty"] = out["yes_bid_qty"]
    out = out.dropna(subset=["received_at_utc", "market_ticker", "event_ticker", "yes_bid", "yes_ask"])
    out = out[(out["yes_ask"] >= out["yes_bid"]) & out["yes_bid"].between(0, 1) & out["yes_ask"].between(0, 1)]
    out = out.sort_values(["market_ticker", "received_at_ns", "sequence"]).drop_duplicates(
        ["market_ticker", "received_at_ns"], keep="last"
    )
    return out.sort_values("received_at_ns").reset_index(drop=True)


def common_market_intervals(ws: pd.DataFrame, pred: pd.DataFrame) -> pd.DataFrame:
    ws_g = (
        ws.groupby(["market_ticker", "event_ticker"], as_index=False)
        .agg(ws_first=("received_at_utc", "min"), ws_last=("received_at_utc", "max"), ws_rows=("market_ticker", "size"))
    )
    pred_g = (
        pred.groupby(["market_ticker", "event_ticker"], as_index=False)
        .agg(pred_first=("received_at_utc", "min"), pred_last=("received_at_utc", "max"), pred_rows=("market_ticker", "size"))
    )
    common = ws_g.merge(pred_g, on=["market_ticker", "event_ticker"], how="inner")
    if common.empty:
        return common
    common["overlap_start"] = common[["ws_first", "pred_first"]].max(axis=1)
    common["overlap_end"] = common[["ws_last", "pred_last"]].min(axis=1)
    common = common[common["overlap_start"].le(common["overlap_end"])].copy()
    return common.sort_values(["event_ticker", "market_ticker"]).reset_index(drop=True)


def restrict_to_intervals(df: pd.DataFrame, intervals: pd.DataFrame) -> pd.DataFrame:
    if df.empty or intervals.empty:
        return pd.DataFrame(columns=df.columns)
    merged = df.merge(intervals[["market_ticker", "overlap_start", "overlap_end"]], on="market_ticker", how="inner")
    merged = merged[merged["received_at_utc"].between(merged["overlap_start"], merged["overlap_end"], inclusive="both")]
    return merged.drop(columns=["overlap_start", "overlap_end"]).sort_values("received_at_ns").reset_index(drop=True)


def local_asof_at_pred_times(ws: pd.DataFrame, pred: pd.DataFrame, max_age_sec: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[pd.DataFrame] = []
    align_rows: list[pd.DataFrame] = []
    for market, pred_g in pred.groupby("market_ticker", sort=True):
        ws_g = ws[ws["market_ticker"].eq(market)].sort_values("received_at_ns")
        if ws_g.empty:
            continue
        left = pred_g.sort_values("received_at_ns").copy()
        right = ws_g.copy()
        merged = pd.merge_asof(
            left[["received_at_ns", "received_at_utc", "market_ticker", "event_ticker", "yes_bid", "yes_ask", "no_bid", "no_ask"]],
            right,
            on="received_at_ns",
            direction="backward",
            suffixes=("_pred", "_ws"),
        )
        merged["quote_age_sec"] = (
            merged["received_at_utc_pred"] - utc_series(merged["received_at_utc_ws"])
        ).dt.total_seconds()
        aligned = merged[merged["quote_age_sec"].between(0.0, max_age_sec)].copy()
        if aligned.empty:
            continue
        sampled = pd.DataFrame(
            {
                "received_at_ns": aligned["received_at_ns"],
                "received_at_utc": aligned["received_at_utc_pred"],
                "market_ticker": aligned["market_ticker_pred"],
                "event_ticker": aligned["event_ticker_pred"],
                "yes_bid": aligned["yes_bid_ws"],
                "yes_bid_qty": aligned["yes_bid_qty"],
                "yes_ask": aligned["yes_ask_ws"],
                "yes_ask_qty": aligned["yes_ask_qty"],
                "no_bid": aligned["no_bid_ws"],
                "no_bid_qty": aligned["no_bid_qty"],
                "no_ask": aligned["no_ask_ws"],
                "no_ask_qty": aligned["no_ask_qty"],
                "btc_spot": aligned.get("btc_spot", np.nan),
                "source": "local_ws_asof_predexon_time",
                "local_quote_age_sec": aligned["quote_age_sec"],
                "local_received_at_utc": utc_series(aligned["received_at_utc_ws"]),
            }
        )
        rows.append(sampled)
        align = pd.DataFrame(
            {
                "market_ticker": aligned["market_ticker_pred"],
                "event_ticker": aligned["event_ticker_pred"],
                "pred_ts": aligned["received_at_utc_pred"],
                "local_ts": utc_series(aligned["received_at_utc_ws"]),
                "age_sec": aligned["quote_age_sec"],
                "pred_yes_bid": aligned["yes_bid_pred"],
                "local_yes_bid": aligned["yes_bid_ws"],
                "pred_yes_ask": aligned["yes_ask_pred"],
                "local_yes_ask": aligned["yes_ask_ws"],
                "pred_no_ask": aligned["no_ask_pred"],
                "local_no_ask": aligned["no_ask_ws"],
            }
        )
        align["bid_diff_cents"] = (align["local_yes_bid"] - align["pred_yes_bid"]) * 100.0
        align["ask_diff_cents"] = (align["local_yes_ask"] - align["pred_yes_ask"]) * 100.0
        align["no_ask_diff_cents"] = (align["local_no_ask"] - align["pred_no_ask"]) * 100.0
        align_rows.append(align)
    sampled_out = pd.concat(rows, ignore_index=True).sort_values("received_at_ns").reset_index(drop=True) if rows else pd.DataFrame()
    align_out = pd.concat(align_rows, ignore_index=True).sort_values("pred_ts").reset_index(drop=True) if align_rows else pd.DataFrame()
    return sampled_out, align_out


def fetch_results(events: list[str], sleep_sec: float) -> pd.DataFrame:
    api = KalshiApi(env="prod", require_auth=False)
    rows: list[dict[str, Any]] = []
    for i, event in enumerate(events, start=1):
        try:
            markets = api.get_markets(event_ticker=event, limit=1000, auto_paginate=True, use_cache=False).get("markets", [])
        except Exception as exc:
            print(f"warning: failed result fetch for {event}: {type(exc).__name__}: {exc}", flush=True)
            markets = []
        for market in markets:
            rows.append(
                {
                    "event_ticker": event,
                    "market_ticker": str(market.get("ticker") or "").upper(),
                    "result": str(market.get("result") or "").lower(),
                    "status": market.get("status"),
                    "close_time": market.get("close_time"),
                    "expiration_value": market.get("expiration_value"),
                }
            )
        if i % 10 == 0:
            print(f"fetched Kalshi result metadata {i}/{len(events)} events", flush=True)
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["close_time"] = utc_series(out["close_time"])
    out["actual_yes"] = np.where(out["result"].isin(["yes", "no"]), out["result"].eq("yes"), np.nan)
    return out


def add_mid_change_with_age(df: pd.DataFrame, lb_min: float) -> pd.DataFrame:
    pieces = []
    offset_ns = int(lb_min * 60 * 1_000_000_000)
    for _, group in df.groupby("market_ticker", sort=False):
        g = group.sort_values("received_at_ns").copy()
        left = g[["received_at_ns", "yes_mid"]].copy()
        left["target_ns"] = left["received_at_ns"] - offset_ns
        right = g[["received_at_ns", "yes_mid"]].rename(columns={"received_at_ns": "past_ns", "yes_mid": "past_mid"})
        m = pd.merge_asof(
            left.sort_values("target_ns"),
            right.sort_values("past_ns"),
            left_on="target_ns",
            right_on="past_ns",
            direction="backward",
        )
        m = m.sort_index()
        g[f"yes_mid_chg_{int(lb_min)}m"] = g["yes_mid"].to_numpy() - m["past_mid"].to_numpy()
        g[f"lookback_age_{int(lb_min)}m_sec"] = (g["received_at_ns"].to_numpy() - m["past_ns"].to_numpy()) / 1_000_000_000.0
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True).sort_values("received_at_ns").reset_index(drop=True) if pieces else df


def add_btc_return_with_age(q: pd.DataFrame, btc: pd.DataFrame, lb_min: int) -> pd.DataFrame:
    if q.empty:
        return q
    b = btc.dropna(subset=["received_at_ns", "price"]).sort_values("received_at_ns").copy()
    b["price"] = pd.to_numeric(b["price"], errors="coerce")
    cur = pd.merge_asof(
        q[["received_at_ns"]].sort_values("received_at_ns"),
        b[["received_at_ns", "price"]].rename(columns={"price": "btc_now"}),
        on="received_at_ns",
        direction="backward",
    ).sort_index()
    left = q[["received_at_ns"]].copy()
    left["target_ns"] = left["received_at_ns"] - int(lb_min * 60 * 1_000_000_000)
    past = pd.merge_asof(
        left.sort_values("target_ns"),
        b[["received_at_ns", "price"]].rename(columns={"received_at_ns": "btc_past_ns", "price": "btc_past"}),
        left_on="target_ns",
        right_on="btc_past_ns",
        direction="backward",
    ).sort_index()
    out = q.copy()
    out[f"btc_ret_{lb_min}m_bps"] = 10000.0 * np.log(cur["btc_now"].to_numpy() / past["btc_past"].to_numpy())
    out[f"btc_lookback_age_{lb_min}m_sec"] = (out["received_at_ns"].to_numpy() - past["btc_past_ns"].to_numpy()) / 1_000_000_000.0
    return out


def build_features(top: pd.DataFrame, btc: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    q = top.copy()
    q = q.dropna(subset=["received_at_ns", "received_at_utc", "market_ticker", "event_ticker", "yes_bid", "yes_ask", "no_bid", "no_ask"])
    for col in ["yes_bid", "yes_bid_qty", "yes_ask", "yes_ask_qty", "no_bid", "no_bid_qty", "no_ask", "no_ask_qty"]:
        q[col] = pd.to_numeric(q[col], errors="coerce")
    q["received_at_utc"] = utc_series(q["received_at_utc"])
    q["yes_mid"] = (q["yes_bid"] + q["yes_ask"]) / 2.0
    q["spread_cents"] = (q["yes_ask"] - q["yes_bid"]).clip(lower=0.0) * 100.0
    q = add_mid_change_with_age(q, 1)
    q = add_mid_change_with_age(q, 2)
    q = add_mid_change_with_age(q, 3)
    q = add_mid_change_with_age(q, 5)
    q = add_btc_return_with_age(q, btc, 3)
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
    return q.sort_values("received_at_ns").reset_index(drop=True)


def risk_reward(entry: pd.Series | float, fee_: pd.Series | float) -> pd.Series | float:
    return (1.0 - entry - fee_) / (entry + fee_)


def current_live_lowdd_rr033(q: pd.DataFrame, require_age_guard: bool = True) -> pd.DataFrame:
    base = q[
        q["ttl_min"].between(4.0, 5.0)
        & q["spread_cents"].le(2.0)
        & q["yes_mid_chg_2m"].notna()
        & q["yes_mid_chg_3m"].abs().le(0.35)
        & q["actual_yes"].notna()
    ].copy()
    if require_age_guard:
        base = base[
            base["lookback_age_2m_sec"].between(115, 360)
            & base["lookback_age_3m_sec"].between(170, 420)
            & base["btc_lookback_age_3m_sec"].between(170, 420)
        ].copy()
    if base.empty:
        return pd.DataFrame()
    frames = []
    for side, mask in [
        ("yes", base["yes_mid_chg_2m"].ge(0.125) & base["btc_ret_3m_bps"].ge(0.0)),
        ("no", base["yes_mid_chg_2m"].le(-0.125) & base["btc_ret_3m_bps"].le(0.0)),
    ]:
        t = base[mask].copy()
        if t.empty:
            continue
        t["strategy"] = "current_live_lowdd_rr033"
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"].astype(float)
            t["visible_qty"] = t["yes_ask_qty"].astype(float)
        else:
            t["entry_price"] = t["no_ask"].astype(float)
            t["visible_qty"] = t["no_ask_qty"].astype(float)
        t = t[t["entry_price"].between(0.05, 0.80) & t["visible_qty"].fillna(0.0).ge(1.0)].copy()
        if t.empty:
            continue
        t["entry_fee"] = t["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker"))
        t["rr"] = risk_reward(t["entry_price"], t["entry_fee"])
        t = t[t["rr"].ge(0.33)].copy()
        if t.empty:
            continue
        actual_yes = t["actual_yes"].astype(bool)
        t["win"] = actual_yes if side == "yes" else ~actual_yes
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -(t["entry_price"] + t["entry_fee"]))
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["score"] = t["yes_mid_chg_2m"].abs() - 0.01 * t["spread_cents"].astype(float)
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return (
        out.sort_values(["event_ticker", "received_at_ns", "score"], ascending=[True, True, False])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def liquid_low_spread_rr(q: pd.DataFrame) -> pd.DataFrame:
    candidates = cheap_tail_candidates(q)
    if candidates.empty:
        return candidates
    t = candidates[
        candidates["spread_cents"].le(1.0)
        & candidates["visible_qty"].fillna(0.0).ge(50.0)
        & candidates["rr"].ge(0.50)
    ].copy()
    return first_per_event(t, "liquid_low_spread_rr")


def score_all(source: str, top: pd.DataFrame, btc: pd.DataFrame, results: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    q = build_features(top, btc, results)
    q["comparison_source"] = source
    trades_by_name: dict[str, pd.DataFrame] = {
        "current_live_lowdd_rr033": current_live_lowdd_rr033(q),
        "cheap_yes_rr_first": cheap_tail_side(q, "yes"),
        "cheap_no_rr_first": cheap_tail_side(q, "no"),
        "cheap_tail_best_side_first": first_per_event(cheap_tail_candidates(q), "cheap_tail_best_side_first"),
        "cheap_pair_lock_rr": cheap_pair_lock(cheap_tail_candidates(q)),
        "cheap_tail_position_aware": cheap_tail_position_aware(cheap_tail_candidates(q)),
        "liquid_low_spread_rr": liquid_low_spread_rr(q),
    }
    summaries = []
    trades = []
    for name, frame in trades_by_name.items():
        if not frame.empty:
            frame = frame.copy()
            frame["strategy"] = name
            frame["comparison_source"] = source
            trades.append(frame)
        row = summarize(name, frame)
        row["comparison_source"] = source
        summaries.append(row)
    return pd.DataFrame(summaries), pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()


def summarize_alignment(alignment: pd.DataFrame) -> pd.DataFrame:
    if alignment.empty:
        return pd.DataFrame()
    rows = []
    for name, group in [("all", alignment), *list(alignment.groupby("event_ticker", sort=True))]:
        g = group if isinstance(group, pd.DataFrame) else group[1]
        key = name if isinstance(name, str) else group[0]
        rows.append(
            {
                "event_ticker": key,
                "rows": int(len(g)),
                "median_age_sec": float(g["age_sec"].median()),
                "p95_age_sec": float(g["age_sec"].quantile(0.95)),
                "match_within_1s": float(g["age_sec"].le(1.0).mean()),
                "match_within_5s": float(g["age_sec"].le(5.0).mean()),
                "median_abs_bid_diff_c": float(g["bid_diff_cents"].abs().median()),
                "median_abs_ask_diff_c": float(g["ask_diff_cents"].abs().median()),
                "p95_abs_bid_diff_c": float(g["bid_diff_cents"].abs().quantile(0.95)),
                "p95_abs_ask_diff_c": float(g["ask_diff_cents"].abs().quantile(0.95)),
            }
        )
    return pd.DataFrame(rows)


def trade_match_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    sources = sorted(trades["comparison_source"].dropna().unique().tolist())
    for strategy, g in trades.groupby("strategy", sort=True):
        pred = g[g["comparison_source"].eq("predexon")]
        local = g[g["comparison_source"].eq("local_asof_pred_times")]
        pred_keys = set(zip(pred["event_ticker"], pred["market_ticker"]))
        local_keys = set(zip(local["event_ticker"], local["market_ticker"]))
        common = pred_keys & local_keys
        side_same = 0
        pnl_delta = 0.0
        for event, market in common:
            p = pred[pred["event_ticker"].eq(event) & pred["market_ticker"].eq(market)].iloc[0]
            l = local[local["event_ticker"].eq(event) & local["market_ticker"].eq(market)].iloc[0]
            side_same += int(str(p.get("side")) == str(l.get("side")))
            pnl_delta += float(l.get("pnl", 0.0)) - float(p.get("pnl", 0.0))
        rows.append(
            {
                "strategy": strategy,
                "sources_seen": ",".join(sources),
                "predexon_trades": int(len(pred)),
                "local_asof_trades": int(len(local)),
                "common_event_market_trades": int(len(common)),
                "predexon_only": int(len(pred_keys - local_keys)),
                "local_asof_only": int(len(local_keys - pred_keys)),
                "same_side_on_common": int(side_same),
                "pnl_local_minus_pred_on_common": float(pnl_delta),
            }
        )
    return pd.DataFrame(rows)


def format_summary(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    cols = [
        "comparison_source",
        "strategy",
        "trades",
        "events",
        "pnl",
        "return_on_100",
        "premium",
        "return_on_premium",
        "win_rate",
        "max_dd",
        "sharpe",
        "avg_entry",
        "first_entry",
        "last_entry",
    ]
    out = summary[[c for c in cols if c in summary.columns]].copy()
    for col in ["pnl", "return_on_100", "premium", "return_on_premium", "win_rate", "max_dd", "sharpe", "avg_entry"]:
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(4)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--predexon-root", type=Path, default=DEFAULT_PREDEXON_ROOT)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--snapshot-db", type=Path, default=DEFAULT_SNAPSHOT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--reuse-snapshot", action="store_true")
    parser.add_argument("--max-asof-age-sec", type=float, default=10.0)
    parser.add_argument("--kalshi-sleep-sec", type=float, default=0.25)
    args = parser.parse_args()

    start = parse_ts(args.start)
    end = parse_ts(args.end)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_capture_db(args.capture_db, args.snapshot_db, args.reuse_snapshot)
    print(f"loading local snapshot {snapshot}", flush=True)
    ws, btc = load_ws(snapshot, start, end)
    print(f"local ws rows={len(ws):,} btc ticks={len(btc):,}", flush=True)
    pred = load_predexon(args.predexon_root, start, end)
    print(f"predexon rows={len(pred):,}", flush=True)
    intervals = common_market_intervals(ws, pred)
    if intervals.empty:
        raise SystemExit("No exact market/time overlap between local WS and Predexon.")
    intervals.to_csv(args.output_dir / "common_market_intervals.csv", index=False)
    common_markets = sorted(intervals["market_ticker"].unique().tolist())
    print(f"common markets={len(common_markets):,}", flush=True)

    pred_common = restrict_to_intervals(pred, intervals)
    ws_dense_common = restrict_to_intervals(ws[ws["market_ticker"].isin(common_markets)].copy(), intervals)
    ws_asof, alignment = local_asof_at_pred_times(ws_dense_common, pred_common, args.max_asof_age_sec)
    if alignment.empty:
        raise SystemExit("Predexon rows did not match local WS rows under the as-of age tolerance.")
    alignment.to_csv(args.output_dir / "quote_alignment_rows.csv", index=False)
    align_summary = summarize_alignment(alignment)
    align_summary.to_csv(args.output_dir / "quote_alignment_summary.csv", index=False)
    print(f"pred common rows={len(pred_common):,} local-asof rows={len(ws_asof):,} dense local rows={len(ws_dense_common):,}", flush=True)

    events = sorted(set(intervals["event_ticker"].astype(str)))
    results = fetch_results(events, sleep_sec=args.kalshi_sleep_sec)
    results.to_csv(args.output_dir / "kalshi_results.csv", index=False)
    result_finalized = int(results["actual_yes"].notna().sum()) if not results.empty else 0
    print(f"result rows={len(results):,} finalized={result_finalized:,}", flush=True)

    all_summaries = []
    all_trades = []
    for source, frame in [
        ("predexon", pred_common),
        ("local_asof_pred_times", ws_asof),
        ("local_dense_same_markets", ws_dense_common),
    ]:
        summary, trades = score_all(source, frame, btc, results)
        all_summaries.append(summary)
        if not trades.empty:
            all_trades.append(trades)
    summary = pd.concat(all_summaries, ignore_index=True)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    summary_fmt = format_summary(summary)
    summary.to_csv(args.output_dir / "summary_raw.csv", index=False)
    summary_fmt.to_csv(args.output_dir / "summary.csv", index=False)
    if not trades.empty:
        trades.to_csv(args.output_dir / "trades.csv", index=False)
    match = trade_match_summary(trades)
    match.to_csv(args.output_dir / "trade_match_summary.csv", index=False)

    info = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start_utc": str(start),
        "window_end_utc": str(end),
        "predexon_root": str(args.predexon_root),
        "capture_db": str(args.capture_db),
        "snapshot_db": str(snapshot),
        "max_asof_age_sec": args.max_asof_age_sec,
        "ws_rows_window": int(len(ws)),
        "btc_ticks_window": int(len(btc)),
        "predexon_rows_window": int(len(pred)),
        "common_markets": int(len(common_markets)),
        "common_events": int(intervals["event_ticker"].nunique()),
        "pred_common_rows": int(len(pred_common)),
        "local_asof_rows": int(len(ws_asof)),
        "local_dense_rows": int(len(ws_dense_common)),
        "settlement_rows": int(len(results)),
        "settlement_finalized_rows": int(result_finalized),
        "assumptions": [
            "Predexon and local_asof_pred_times use identical decision timestamps: Predexon snapshot timestamps.",
            "Local_asof_pred_times uses the latest local websocket book at or before each Predexon timestamp.",
            "Trades assume one taker contract at visible top-of-book with Kalshi taker fees.",
            "No candle high/low, no future quote selection, and no future-best RR selection.",
            "Current strategy requires non-stale 2m/3m market and 3m BTC lookbacks to avoid sparse-snapshot leakage.",
            "No REST order acknowledgment or real FOK failure probability is simulated.",
        ],
    }
    (args.output_dir / "data_report.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")

    report = [
        "# BTC15M Predexon vs Local WS Overlap",
        "",
        f"Generated: `{info['generated_at_utc']}`",
        f"Window: `{info['window_start_utc']}` -> `{info['window_end_utc']}`",
        f"Common exact markets: `{info['common_markets']}`; common events: `{info['common_events']}`.",
        f"Rows: Predexon common `{info['pred_common_rows']:,}`, local-asof `{info['local_asof_rows']:,}`, local dense same markets `{info['local_dense_rows']:,}`.",
        "",
        "## Backtest Summary",
        "",
        markdown_or_text(summary_fmt),
        "",
        "## Trade Match Summary",
        "",
        markdown_or_text(match),
        "",
        "## Quote Alignment",
        "",
        markdown_or_text(align_summary.head(12).round(4)),
        "",
        "## Assumptions",
        "",
        *[f"- {item}" for item in info["assumptions"]],
        "",
    ]
    (args.output_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(markdown_or_text(summary_fmt))
    print(f"Wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
