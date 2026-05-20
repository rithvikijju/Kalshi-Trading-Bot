#!/usr/bin/env python3
"""Backtest the currently running BTC 1h and BTC15M live models on Kalshi historical candles.

This is intentionally conservative for historical API data:

* decisions use only candle close at ``available_at == candle_end``
* no forward fill
* one contract max for historical fills
* reject isolated quote candles and unstable spread neighborhoods
* use official Kalshi settlement where available
* keep proxy-settled rows only when they are not near the strike
* apply a live-capture top-of-book fillability model as a haircut

Historical candles remain research-grade, not websocket-execution-grade. The
fillability model is an explicit adjustment layer, not a claim that historical
candles contain depth or FOK queue state.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts import may8examine as mx  # noqa: E402
from scripts.backtest_1hr_collected_data import parse_event_close_from_ticker  # noqa: E402
from scripts.build_crypto_research_datamart import event_close_from_ticker  # noqa: E402


DEFAULT_1H_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"
DEFAULT_15M_DB = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "crypto_research.duckdb"
DEFAULT_OUTPUT = PROJECT_ROOT / "backtest_outputs" / f"current_live_models_historical_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_1H_CAPTURE = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless_20260512_paused.duckdb"
DEFAULT_15M_CAPTURE = PROJECT_ROOT / "backtest_outputs" / "mcts_contract_15m_20260512" / "btc15m_live_capture_validation_20260512" / "btc15m_live_capture_snapshot.duckdb"

BTC15M_RULE = {
    "ttl_lo": 4.0,
    "ttl_hi": 5.0,
    "spread_max_cents": 2.0,
    "mkt2_threshold": 0.125,
    "abs3_cap": 0.35,
    "entry_min": 0.05,
    "entry_max": 0.90,
    "btc_lookback_min": 3,
}

ONE_HOUR_VARIANT = mx.Variant(
    "current_1h_late_loss_guard_cap1",
    min_ttl_min=5.0,
    max_ttl_min=20.0,
    max_no_p=0.28,
)


@dataclass(frozen=True)
class FillModel:
    global_prob: float
    by_bucket: dict[tuple[str, str, str, str, str], float]
    by_series_side: dict[tuple[str, str], float]
    rows: int
    sources: list[str]


def ts_arg(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc_series(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def fee(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def parse_close_any(event_ticker: str) -> pd.Timestamp | None:
    ticker = str(event_ticker or "").upper()
    try:
        if ticker.startswith("KXBTC15M-"):
            close = event_close_from_ticker(ticker)
        else:
            close = parse_event_close_from_ticker(ticker)
    except Exception:
        close = None
    return pd.Timestamp(close).tz_convert("UTC") if close is not None else None


def event_family(event_ticker: str) -> str:
    ticker = str(event_ticker or "").upper()
    if ticker.startswith("KXBTC15M-"):
        return "btc15m"
    if ticker.startswith("KXBTCD-"):
        return "btc1h"
    return "other"


def entry_bucket(price: float) -> str:
    if price < 0.25:
        return "00_25"
    if price < 0.50:
        return "25_50"
    if price < 0.75:
        return "50_75"
    return "75_100"


def spread_bucket(spread_cents: float) -> str:
    if spread_cents <= 1.0:
        return "le1c"
    if spread_cents <= 2.0:
        return "le2c"
    if spread_cents <= 4.0:
        return "le4c"
    return "gt4c"


def ttl_bucket(ttl_min: float) -> str:
    if ttl_min <= 5:
        return "ttl_le5"
    if ttl_min <= 20:
        return "ttl_5_20"
    return "ttl_gt20"


def max_drawdown(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    equity = pnl.astype(float).cumsum()
    return float((equity - equity.cummax()).min())


def trade_sharpe(pnl: pd.Series) -> float:
    pnl = pnl.dropna().astype(float)
    if len(pnl) < 2:
        return 0.0
    std = float(pnl.std(ddof=1))
    if std <= 0:
        return 0.0
    return float(pnl.mean() / std * math.sqrt(len(pnl)))


def split_name(close_time: pd.Timestamp) -> str:
    close_time = pd.Timestamp(close_time).tz_convert("UTC")
    if close_time < pd.Timestamp("2026-04-01T00:00:00Z"):
        return "train"
    if close_time < pd.Timestamp("2026-04-21T00:00:00Z"):
        return "validation"
    return "test"


def snapshot_duckdb(path: Path, out_dir: Path) -> Path | None:
    if not path.exists():
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.duckdb"
    try:
        shutil.copy2(path, dest)
        wal = path.with_suffix(path.suffix + ".wal")
        if wal.exists():
            shutil.copy2(wal, dest.with_suffix(dest.suffix + ".wal"))
        return dest
    except OSError:
        return path


def load_live_top_rows(db_path: Path, sample_mod: int, max_rows: int) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(db_path), read_only=True)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    table_name = None
    for candidate in ("ws_orderbook_top_dedup", "ws_orderbook_top", "ws_orderbook_top_all"):
        if candidate in tables:
            table_name = candidate
            break
    if table_name is None:
        con.close()
        return pd.DataFrame()
    where = [
        "yes_bid IS NOT NULL",
        "yes_ask IS NOT NULL",
        "received_at_ns IS NOT NULL",
        "(event_ticker LIKE 'KXBTCD-%' OR event_ticker LIKE 'KXBTC15M-%')",
    ]
    if sample_mod > 1:
        where.append(f"abs(received_at_ns % {int(sample_mod)}) = 0")
    sql = f"""
        SELECT received_at_ns, received_at_utc, event_ticker, market_ticker,
               yes_bid, yes_bid_qty, yes_ask, yes_ask_qty,
               no_bid, no_bid_qty, no_ask, no_ask_qty
        FROM {table_name}
        WHERE {' AND '.join(where)}
        ORDER BY received_at_ns
        LIMIT {int(max_rows)}
    """
    out = con.execute(sql).fetchdf()
    con.close()
    if out.empty:
        return out
    out["received_at_utc"] = utc_series(out["received_at_utc"])
    return out


def fit_fill_model(capture_dbs: list[Path], output_dir: Path) -> FillModel:
    frames = []
    sources = []
    snap_dir = output_dir / "capture_calibration_snapshots"
    for path in capture_dbs:
        if not path.exists():
            continue
        # Sample the large 1h capture, keep all/smaller BTC15M snapshots.
        sample_mod = 37 if "live_capture_gapless" in str(path).lower() else 1
        try:
            frame = load_live_top_rows(path, sample_mod=sample_mod, max_rows=800_000)
        except (duckdb.Error, OSError):
            snap = snapshot_duckdb(path, snap_dir)
            if snap is None or not Path(snap).exists():
                continue
            frame = load_live_top_rows(Path(snap), sample_mod=sample_mod, max_rows=800_000)
        if not frame.empty:
            frame["source_db"] = str(path)
            frames.append(frame)
            sources.append(str(path))
    if not frames:
        return FillModel(global_prob=0.90, by_bucket={}, by_series_side={}, rows=0, sources=[])
    top = pd.concat(frames, ignore_index=True)
    top["series_family"] = top["event_ticker"].map(event_family)
    top["close_time"] = top["event_ticker"].map(parse_close_any)
    top = top.dropna(subset=["close_time"])
    top["ttl_min"] = (utc_series(top["close_time"]) - top["received_at_utc"]).dt.total_seconds() / 60.0
    top["spread_cents"] = (pd.to_numeric(top["yes_ask"], errors="coerce") - pd.to_numeric(top["yes_bid"], errors="coerce")) * 100.0
    top["mid"] = (pd.to_numeric(top["yes_ask"], errors="coerce") + pd.to_numeric(top["yes_bid"], errors="coerce")) / 2.0
    top = top.sort_values(["market_ticker", "received_at_ns"]).reset_index(drop=True)
    top["mid_prev"] = top.groupby("market_ticker")["mid"].shift(1)
    top["quote_speed_cents"] = (top["mid"] - top["mid_prev"]).abs().fillna(0.0) * 100.0

    side_rows = []
    for side, ask_col, qty_col in [
        ("yes", "yes_ask", "yes_ask_qty"),
        ("no", "no_ask", "no_ask_qty"),
    ]:
        piece = top[["series_family", "ttl_min", "spread_cents", "quote_speed_cents", ask_col, qty_col]].copy()
        piece = piece.rename(columns={ask_col: "entry_price", qty_col: "visible_qty"})
        piece["side"] = side
        side_rows.append(piece)
    fit = pd.concat(side_rows, ignore_index=True)
    for col in ("entry_price", "visible_qty", "ttl_min", "spread_cents", "quote_speed_cents"):
        fit[col] = pd.to_numeric(fit[col], errors="coerce")
    fit = fit.dropna(subset=["entry_price", "spread_cents", "ttl_min"])
    fit = fit[(fit["entry_price"] > 0.0) & (fit["entry_price"] < 1.0) & (fit["spread_cents"] >= 0.0)]
    fit["fillable"] = fit["visible_qty"].fillna(0.0) >= 1.0
    fit["entry_bucket"] = fit["entry_price"].map(entry_bucket)
    fit["spread_bucket"] = fit["spread_cents"].map(spread_bucket)
    fit["ttl_bucket"] = fit["ttl_min"].map(ttl_bucket)

    prior = 8.0
    global_prob = float((fit["fillable"].sum() + prior * 0.90) / (len(fit) + prior)) if len(fit) else 0.90
    by_bucket: dict[tuple[str, str, str, str, str], float] = {}
    for key, group in fit.groupby(["series_family", "side", "entry_bucket", "spread_bucket", "ttl_bucket"], dropna=False):
        p = float((group["fillable"].sum() + prior * global_prob) / (len(group) + prior))
        by_bucket[tuple(str(x) for x in key)] = p
    by_series_side: dict[tuple[str, str], float] = {}
    for key, group in fit.groupby(["series_family", "side"], dropna=False):
        p = float((group["fillable"].sum() + prior * global_prob) / (len(group) + prior))
        by_series_side[tuple(str(x) for x in key)] = p
    fit.groupby(["series_family", "side", "entry_bucket", "spread_bucket", "ttl_bucket"], dropna=False).agg(
        rows=("fillable", "size"),
        visible_qty_ge_1=("fillable", "mean"),
    ).reset_index().to_csv(output_dir / "live_fillability_model_bins.csv", index=False)
    return FillModel(global_prob=global_prob, by_bucket=by_bucket, by_series_side=by_series_side, rows=int(len(fit)), sources=sources)


def apply_fill_model(trades: pd.DataFrame, model: FillModel) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    probs = []
    for row in out.to_dict("records"):
        family = event_family(str(row.get("event_ticker", "")))
        side = str(row.get("side", "")).lower()
        entry = float(row.get("entry_price", np.nan))
        spread = float(row.get("spread_cents", np.nan))
        ttl = float(row.get("ttl_min", row.get("ttl_min_float", np.nan)))
        key = (family, side, entry_bucket(entry), spread_bucket(spread), ttl_bucket(ttl))
        p = model.by_bucket.get(key)
        if p is None:
            p = model.by_series_side.get((family, side), model.global_prob)
        speed = abs(float(row.get("quote_speed_cents", 0.0) or 0.0))
        if speed > 20:
            p *= 0.80
        elif speed > 10:
            p *= 0.90
        probs.append(max(0.0, min(1.0, float(p))))
    out["hist_fill_prob"] = probs
    out = out[out["hist_fill_prob"] >= 0.50].copy()
    out["pnl_fill_adj"] = out["pnl"].astype(float) * out["hist_fill_prob"].astype(float)
    out["premium_fill_adj"] = out["premium"].astype(float) * out["hist_fill_prob"].astype(float)
    return out


def conservative_quote_filter(quotes: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    if quotes.empty:
        return quotes, {"input_rows": 0, "kept_rows": 0}
    q = quotes.copy().sort_values(["market_ticker", "available_at"]).reset_index(drop=True)
    q["available_at"] = utc_series(q["available_at"])
    q["spread_cents"] = pd.to_numeric(q["spread_cents"], errors="coerce")
    q["yes_mid"] = (pd.to_numeric(q["yes_bid_close"], errors="coerce") + pd.to_numeric(q["yes_ask_close"], errors="coerce")) / 2.0
    q["prev_time"] = q.groupby("market_ticker")["available_at"].shift(1)
    q["next_time"] = q.groupby("market_ticker")["available_at"].shift(-1)
    q["prev_spread"] = q.groupby("market_ticker")["spread_cents"].shift(1)
    q["next_spread"] = q.groupby("market_ticker")["spread_cents"].shift(-1)
    q["prev_mid"] = q.groupby("market_ticker")["yes_mid"].shift(1)
    q["quote_speed_cents"] = (q["yes_mid"] - q["prev_mid"]).abs().fillna(0.0) * 100.0
    prev_gap = (q["available_at"] - q["prev_time"]).dt.total_seconds()
    next_gap = (q["next_time"] - q["available_at"]).dt.total_seconds()
    local_spread_max = q[["prev_spread", "spread_cents", "next_spread"]].max(axis=1)
    local_spread_min = q[["prev_spread", "spread_cents", "next_spread"]].min(axis=1)
    keep = (
        q["spread_cents"].between(0.0, 2.0)
        & prev_gap.between(1, 90)
        & next_gap.between(1, 90)
        & (local_spread_max <= 2.0)
        & ((local_spread_max - local_spread_min) <= 2.0)
    )
    out = q[keep].drop(columns=["prev_time", "next_time", "prev_spread", "next_spread", "prev_mid"]).copy()
    return out, {
        "input_rows": int(len(q)),
        "kept_rows": int(len(out)),
        "dropped_rows": int(len(q) - len(out)),
    }


def official_result_for_market(session: requests.Session, ticker: str, cache: dict[str, Any]) -> dict[str, Any]:
    ticker = str(ticker).upper()
    if ticker in cache:
        return cache[ticker]
    row: dict[str, Any] = {"result": None, "expiration_value": None, "status": None, "source": None}
    for path, source in [(f"/markets/{ticker}", "live_market"), (f"/historical/markets/{ticker}", "historical_market")]:
        try:
            response = session.get(f"https://api.elections.kalshi.com/trade-api/v2{path}", timeout=15)
            if response.status_code == 429:
                time.sleep(1.0)
                response = session.get(f"https://api.elections.kalshi.com/trade-api/v2{path}", timeout=15)
            if not response.ok:
                continue
            payload = response.json()
            market = payload.get("market") or payload
            result = str(market.get("result") or "").lower()
            value_raw = market.get("expiration_value")
            row = {
                "result": result if result in {"yes", "no"} else None,
                "expiration_value": float(value_raw) if value_raw not in (None, "") else None,
                "status": market.get("status"),
                "source": source,
            }
            break
        except Exception as exc:
            row = {"result": None, "expiration_value": None, "status": type(exc).__name__, "source": source}
    cache[ticker] = row
    return row


def apply_official_results(
    trades: pd.DataFrame,
    cache_path: Path,
    *,
    fetch: bool,
    proxy_near_strike_usd: float,
) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    out["settlement_source"] = out.get("settlement_source", "btc_proxy")
    out["official_expiration_value"] = out.get("official_expiration_value", np.nan)
    if fetch:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        session = requests.Session()
        tickers = sorted(out["market_ticker"].dropna().astype(str).str.upper().unique())
        for idx, ticker in enumerate(tickers, start=1):
            row = official_result_for_market(session, ticker, cache)
            if row.get("result") in {"yes", "no"}:
                mask = out["market_ticker"].astype(str).str.upper().eq(ticker)
                out.loc[mask, "settlement"] = row["result"]
                out.loc[mask, "official_expiration_value"] = row.get("expiration_value")
                out.loc[mask, "settlement_source"] = "official_kalshi"
            if idx % 100 == 0:
                print(f"  official settlement fetch {idx}/{len(tickers)}", flush=True)
                cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
                time.sleep(0.5)
        cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    win = out["settlement"].astype(str).str.lower().eq(out["side"].astype(str).str.lower())
    out["payout"] = np.where(win, 1.0, 0.0)
    out["pnl"] = out["payout"] - out["entry_price"].astype(float) - out["entry_fee"].astype(float)
    # Keep official rows. For proxy rows, drop near-strike results where the
    # Coinbase/Kraken proxy could disagree with the official index.
    strike = pd.to_numeric(out.get("strike", out.get("floor_strike", np.nan)), errors="coerce")
    ref = pd.to_numeric(out.get("official_expiration_value", np.nan), errors="coerce")
    if "settlement_spot" in out:
        ref = ref.fillna(pd.to_numeric(out["settlement_spot"], errors="coerce"))
    near = (ref - strike).abs() <= float(proxy_near_strike_usd)
    keep = out["settlement_source"].eq("official_kalshi") | (~near.fillna(True))
    out = out[keep].copy()
    out["premium"] = out["entry_price"].astype(float) + out["entry_fee"].astype(float)
    return out


def run_1h(args: argparse.Namespace, fill_model: FillModel, output_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    print("Running current BTC 1h late-only loss guard on historical API DuckDB...", flush=True)
    quotes, btc, meta = mx.load_duckdb_quotes(args.one_hour_db, args.start_ts, args.end_ts, args.max_events)
    filtered, filter_report = conservative_quote_filter(quotes)
    trades = mx.run_replay("historical_api_btc1h_conservative", filtered, btc, args.train_days, args.progress_every_events, [ONE_HOUR_VARIANT])
    if trades.empty:
        return trades, {"model": "btc1h_current_late_loss_guard", "data": meta, "filter": filter_report}
    trades["model"] = "btc1h_current_late_loss_guard"
    trades["ttl_min"] = (utc_series(trades["close_time"]) - utc_series(trades["entry_time"])).dt.total_seconds() / 60.0
    trades["premium"] = trades["entry_price"].astype(float) + trades["entry_fee"].astype(float)
    trades = apply_official_results(
        trades,
        output_dir / "official_results_cache.json",
        fetch=args.fetch_official_results,
        proxy_near_strike_usd=args.proxy_near_strike_usd,
    )
    trades = apply_fill_model(trades, fill_model)
    trades["split"] = utc_series(trades["close_time"]).map(split_name)
    trades.to_csv(output_dir / "btc1h_current_late_loss_guard_trades.csv", index=False)
    meta = dict(meta)
    meta["settlement"] = (
        "official Kalshi get_market result for retained trades when --fetch-official-results is set; "
        "otherwise BTC proxy rows are kept only outside the near-strike exclusion band."
    )
    return trades, {"model": "btc1h_current_late_loss_guard", "data": meta, "filter": filter_report}


def load_15m_tables(db_path: Path, start: pd.Timestamp | None, end: pd.Timestamp | None, max_events: int | None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    con = duckdb.connect(str(db_path), read_only=True)
    where = ["m.series_ticker = 'KXBTC15M'", "q.yes_bid_close IS NOT NULL", "q.yes_ask_close IS NOT NULL"]
    params: list[Any] = []
    if start is not None:
        where.append("m.close_time >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        where.append("m.close_time < ?")
        params.append(end.to_pydatetime())
    selected_events: list[str] | None = None
    if max_events:
        selected_events = [
            row[0]
            for row in con.execute(
                f"""
                SELECT m.event_ticker
                FROM kalshi_markets m
                WHERE {' AND '.join(where)}
                GROUP BY 1, m.close_time
                ORDER BY m.close_time, 1
                LIMIT ?
                """,
                [*params, int(max_events)],
            ).fetchall()
        ]
    if selected_events:
        placeholders = ",".join("?" for _ in selected_events)
        where.append(f"q.event_ticker IN ({placeholders})")
        params.extend(selected_events)
    quotes = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, q.volume, q.open_interest, q.source, q.fidelity,
               m.series_ticker, m.asset, m.product_id, m.family, m.frequency,
               m.market_kind, m.open_time, m.close_time, m.floor_strike,
               m.cap_strike, m.result
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE {' AND '.join(where)}
        ORDER BY m.close_time, q.available_at, q.market_ticker
        """,
        params,
    ).fetchdf()
    spot = con.execute(
        """
        SELECT product_id, asset, available_at AS time, bucket_start, open, high, low, close, volume,
               log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM spot_1m
        WHERE product_id = 'BTC-USD'
        ORDER BY available_at
        """
    ).fetchdf()
    meta = con.execute(
        """
        SELECT count(*) AS quote_rows, count(distinct event_ticker) AS events,
               min(available_at) AS first_quote, max(available_at) AS last_quote
        FROM kalshi_quotes
        WHERE event_ticker LIKE 'KXBTC15M-%'
        """
    ).fetchdf().iloc[0].to_dict()
    con.close()
    for col in ("available_at", "ts_end", "open_time", "close_time"):
        if col in quotes:
            quotes[col] = utc_series(quotes[col])
    if not spot.empty:
        spot["time"] = utc_series(spot["time"])
        spot["bucket_start"] = utc_series(spot["bucket_start"])
    return quotes, spot, {"source": "historical_api_btc15m_duckdb", **meta}


def add_btc_returns(q: pd.DataFrame, spot: pd.DataFrame, lookback_min: int) -> pd.DataFrame:
    if q.empty or spot.empty:
        q[f"btc_ret_{lookback_min}m_bps"] = np.nan
        return q
    q = q.copy()
    q["available_at"] = pd.to_datetime(q["available_at"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    spot = spot.dropna(subset=["time", "close"]).sort_values("time").copy()
    spot["time"] = pd.to_datetime(spot["time"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    spot["past_time"] = spot["time"] + pd.Timedelta(minutes=lookback_min)
    left = q[["available_at"]].sort_values("available_at").copy()
    now = pd.merge_asof(
        left,
        spot[["time", "close"]].rename(columns={"time": "spot_time", "close": "btc_now"}),
        left_on="available_at",
        right_on="spot_time",
        direction="backward",
    )
    past_lookup = left.copy()
    past_lookup["target"] = past_lookup["available_at"] - pd.Timedelta(minutes=lookback_min)
    past = pd.merge_asof(
        past_lookup.sort_values("target"),
        spot[["time", "close"]].rename(columns={"time": "past_time", "close": "btc_past"}),
        left_on="target",
        right_on="past_time",
        direction="backward",
    ).sort_index()
    q = q.copy()
    q[f"btc_ret_{lookback_min}m_bps"] = 10000.0 * np.log(now["btc_now"].to_numpy() / past["btc_past"].to_numpy())
    q["btc_spot"] = now["btc_now"].to_numpy()
    return q


def score_btc15m_lowdd(quotes: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame()
    q, _ = conservative_quote_filter(quotes)
    if q.empty:
        return pd.DataFrame()
    q = q.sort_values(["event_ticker", "available_at", "market_ticker"]).reset_index(drop=True)
    q["ttl_min"] = (q["close_time"] - q["available_at"]).dt.total_seconds() / 60.0
    q["ttl_int"] = q["ttl_min"].round().astype("Int64")
    q["yes_mid"] = (q["yes_bid_close"].astype(float) + q["yes_ask_close"].astype(float)) / 2.0
    q["yes_mid_chg_1m"] = q.groupby("event_ticker")["yes_mid"].diff(1)
    q["yes_mid_chg_2m"] = q.groupby("event_ticker")["yes_mid"].diff(2)
    q["yes_mid_chg_3m"] = q.groupby("event_ticker")["yes_mid"].diff(3)
    q["quote_speed_cents"] = q["yes_mid_chg_1m"].abs().fillna(0.0) * 100.0
    q = add_btc_returns(q, spot, BTC15M_RULE["btc_lookback_min"])

    base = q[
        q["ttl_min"].between(BTC15M_RULE["ttl_lo"], BTC15M_RULE["ttl_hi"])
        & q["spread_cents"].le(BTC15M_RULE["spread_max_cents"])
        & q["yes_mid_chg_2m"].notna()
        & q["yes_mid_chg_3m"].abs().le(BTC15M_RULE["abs3_cap"])
        & q["yes_ask_exe"].between(0.0, 1.0)
        & q["no_ask_exe"].between(0.0, 1.0)
    ].copy()
    if base.empty:
        return pd.DataFrame()
    btc_col = f"btc_ret_{BTC15M_RULE['btc_lookback_min']}m_bps"
    up = base["yes_mid_chg_2m"].ge(BTC15M_RULE["mkt2_threshold"]) & base[btc_col].ge(0.0)
    down = base["yes_mid_chg_2m"].le(-BTC15M_RULE["mkt2_threshold"]) & base[btc_col].le(0.0)
    frames = []
    for mask, side in [(up, "yes"), (down, "no")]:
        if not mask.any():
            continue
        t = base[mask].copy()
        t["model"] = "btc15m_lowdd_live"
        t["side"] = side
        t["entry_price"] = t["yes_ask_exe"] if side == "yes" else t["no_ask_exe"]
        t = t[t["entry_price"].between(BTC15M_RULE["entry_min"], BTC15M_RULE["entry_max"])].copy()
        if t.empty:
            continue
        t["entry_fee"] = t["entry_price"].map(fee)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["score"] = t["yes_mid_chg_2m"].abs()
        actual_yes = t["result"].astype(str).str.lower().eq("yes")
        t["settlement"] = np.where(t["result"].astype(str).str.lower().isin(["yes", "no"]), t["result"].astype(str).str.lower(), None)
        t["settlement_source"] = np.where(t["settlement"].isin(["yes", "no"]), "official_kalshi", "proxy_missing")
        t["win"] = actual_yes if side == "yes" else ~actual_yes
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["entry_price"] - t["entry_fee"])
        t["entry_time"] = t["available_at"]
        t["settle_time"] = t["close_time"]
        t["strike"] = t["floor_strike"]
        t["contracts"] = 1
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    trades = pd.concat(frames, ignore_index=True)
    trades = trades[trades["settlement_source"].eq("official_kalshi")].copy()
    trades = trades.sort_values(["event_ticker", "entry_time", "score"], ascending=[True, True, False])
    trades = trades.drop_duplicates("event_ticker", keep="first").reset_index(drop=True)
    trades["split"] = trades["close_time"].map(split_name)
    keep_cols = [
        "model", "split", "event_ticker", "market_ticker", "side", "entry_time", "settle_time",
        "close_time", "entry_price", "entry_fee", "premium", "pnl", "settlement",
        "settlement_source", "contracts", "strike", "floor_strike", "spread_cents",
        "ttl_min", "yes_mid", "yes_mid_chg_1m", "yes_mid_chg_2m", "yes_mid_chg_3m",
        btc_col, "btc_spot", "quote_speed_cents", "source", "fidelity",
    ]
    return trades[[col for col in keep_cols if col in trades.columns]]


def run_15m(args: argparse.Namespace, fill_model: FillModel, output_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    print("Running current BTC15M lowdd live model on historical API DuckDB...", flush=True)
    quotes, spot, meta = load_15m_tables(args.btc15m_db, args.start_ts, args.end_ts, args.max_events)
    _, filter_report = conservative_quote_filter(quotes)
    # score_btc15m_lowdd applies the conservative filter once internally.
    trades = score_btc15m_lowdd(quotes, spot)
    if trades.empty:
        return trades, {"model": "btc15m_lowdd_live", "data": meta, "filter": filter_report}
    trades = apply_fill_model(trades, fill_model)
    trades.to_csv(output_dir / "btc15m_lowdd_live_trades.csv", index=False)
    return trades, {"model": "btc15m_lowdd_live", "data": meta, "filter": filter_report}


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    expanded = [trades]
    all_rows = trades.copy()
    all_rows["split"] = "all"
    expanded.append(all_rows)
    data = pd.concat(expanded, ignore_index=True)
    for (model, split), group in data.groupby(["model", "split"], sort=True):
        ordered = group.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
        pnl = ordered["pnl"].astype(float)
        pnl_adj = ordered.get("pnl_fill_adj", pnl).astype(float)
        premium = ordered["premium"].astype(float)
        premium_adj = ordered.get("premium_fill_adj", premium).astype(float)
        rows.append(
            {
                "model": model,
                "split": split,
                "trades": int(len(ordered)),
                "raw_pnl": float(pnl.sum()),
                "fill_adj_pnl": float(pnl_adj.sum()),
                "return_on_100_fill_adj": float(pnl_adj.sum() / 100.0),
                "raw_premium": float(premium.sum()),
                "fill_adj_premium": float(premium_adj.sum()),
                "fill_adj_rop": float(pnl_adj.sum() / premium_adj.sum()) if premium_adj.sum() else 0.0,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                "max_dd_raw": max_drawdown(pnl),
                "max_dd_fill_adj": max_drawdown(pnl_adj),
                "trade_sharpe_fill_adj": trade_sharpe(pnl_adj),
                "avg_fill_prob": float(ordered.get("hist_fill_prob", pd.Series([1.0])).mean()),
                "yes_trades": int(ordered["side"].astype(str).str.lower().eq("yes").sum()),
                "no_trades": int(ordered["side"].astype(str).str.lower().eq("no").sum()),
                "first_entry": str(ordered["entry_time"].min()),
                "last_entry": str(ordered["entry_time"].max()),
                "official_settled_pct": float(ordered["settlement_source"].eq("official_kalshi").mean())
                if "settlement_source" in ordered
                else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["model", "split"]).reset_index(drop=True)


def write_report(output_dir: Path, summary: pd.DataFrame, reports: list[dict[str, Any]], fill_model: FillModel, args: argparse.Namespace) -> None:
    lines = [
        "# Current Live Models on Historical Kalshi API Candles",
        "",
        f"Created: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Scope",
        "",
        f"- Window: `{args.start_ts}` to `{args.end_ts}`.",
        "- BTC 1h model: current late-only loss-guard wrapper, one-contract historical cap.",
        "- BTC15M model: current `btc15m_lowdd_live.py` rule, one-contract historical cap.",
        "- Historical source: Kalshi 1-minute bid/ask candle close only.",
        "",
        "## Live-Like Historical Adjustments",
        "",
        "- No forward fill.",
        "- Reject isolated quote candles without adjacent previous/next minute candles.",
        "- Reject spread neighborhoods where the local previous/current/next spread exceeds 2c.",
        "- Use official Kalshi settlement where available; proxy near-strike rows are dropped.",
        "- Apply live-capture top-of-book fillability haircut from visible ask quantity bins.",
        "",
        "## Fillability Calibration",
        "",
        f"- Calibration rows: {fill_model.rows:,}.",
        f"- Global visible top-qty >= 1 probability: {fill_model.global_prob:.4f}.",
        f"- Sources: {', '.join(fill_model.sources) if fill_model.sources else 'none; fallback used'}.",
        "",
        "## Summary",
        "",
    ]
    if summary.empty:
        lines.append("No trades generated.")
    else:
        view = summary.copy()
        pct_cols = ["return_on_100_fill_adj", "fill_adj_rop", "win_rate", "avg_fill_prob", "official_settled_pct"]
        for col in pct_cols:
            if col in view:
                view[col] = view[col].map(lambda x: f"{float(x) * 100:.2f}%")
        for col in ["raw_pnl", "fill_adj_pnl", "raw_premium", "fill_adj_premium", "max_dd_raw", "max_dd_fill_adj", "trade_sharpe_fill_adj"]:
            if col in view:
                view[col] = view[col].map(lambda x: f"{float(x):.4f}")
        try:
            lines.append(view.to_markdown(index=False))
        except Exception:
            lines.extend(["```", view.to_string(index=False), "```"])
    lines.extend(
        [
            "",
            "## Data Reports",
            "",
            "```json",
            json.dumps(reports, indent=2, default=str),
            "```",
            "",
            "## Caveat",
            "",
            "This is the best broad historical API replay, but it is still not the same as websocket replay. "
            "The final deployment gate remains our captured websocket data because historical candles do not contain intra-minute orderbook paths, queue state, or exact FOK availability.",
            "",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one-hour-db", type=Path, default=DEFAULT_1H_DB)
    parser.add_argument("--btc15m-db", type=Path, default=DEFAULT_15M_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start", default="2026-03-01T00:00:00Z")
    parser.add_argument("--end")
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--progress-every-events", type=int, default=250)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--fetch-official-results", action="store_true")
    parser.add_argument("--proxy-near-strike-usd", type=float, default=50.0)
    parser.add_argument("--capture-db", type=Path, action="append", default=[DEFAULT_1H_CAPTURE, DEFAULT_15M_CAPTURE])
    args = parser.parse_args()
    args.start_ts = ts_arg(args.start)
    args.end_ts = ts_arg(args.end) if args.end else pd.Timestamp.now(tz="UTC")
    return args


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fill_model = fit_fill_model(args.capture_db, args.output_dir)
    (args.output_dir / "fill_model_manifest.json").write_text(
        json.dumps(
            {
                "global_prob": fill_model.global_prob,
                "rows": fill_model.rows,
                "sources": fill_model.sources,
                "bucket_count": len(fill_model.by_bucket),
                "series_side_count": len(fill_model.by_series_side),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    tasks = {
        "btc1h": lambda: run_1h(args, fill_model, args.output_dir),
        "btc15m": lambda: run_15m(args, fill_model, args.output_dir),
    }
    trades_frames: list[pd.DataFrame] = []
    reports: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                trades, report = future.result()
            except Exception as exc:
                report = {"model": name, "error": repr(exc)}
                trades = pd.DataFrame()
                print(f"{name} failed: {exc!r}", flush=True)
            reports.append(report)
            if not trades.empty:
                trades_frames.append(trades)

    all_trades = pd.concat(trades_frames, ignore_index=True) if trades_frames else pd.DataFrame()
    all_trades.to_csv(args.output_dir / "all_current_live_model_historical_trades.csv", index=False)
    summary = summarize(all_trades)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    write_report(args.output_dir, summary, reports, fill_model, args)
    if summary.empty:
        print("No trades generated.", flush=True)
    else:
        with pd.option_context("display.max_rows", 200, "display.width", 240):
            print(summary.to_string(index=False), flush=True)
    print(f"Wrote {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
