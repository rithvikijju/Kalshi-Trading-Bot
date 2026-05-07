#!/usr/bin/env python3
"""Research a leakage-safe cross-hour entry overlay for KXBTCD.

The script builds candidate rows from the first few observed quotes of each
hourly event, joins the previous adjacent event's same-strike boundary quote,
fits a regularized train-only model, and evaluates executable bid/ask trades
on train and validation only by default.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG, kalshi_fee_dollars
from scripts.backtest_1hr_collected_data import RESEARCH_CFG, build_emp_cache_fast, lognormal_p_above, vectorized_p_above
from scripts.backtest_research_duckdb import BRTI_DAMPENING, DEFAULT_DB, feature_frame_for_strategy


TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")
FEATURES = [
    "base_logit",
    "market_logit",
    "prev_logit",
    "prev_mid",
    "mid_gap",
    "prev_settled_yes",
    "moneyness_bps",
    "boundary_moneyness_bps",
    "spot_move_bps",
    "spread_cents",
    "minute_in_event",
    "rv_15m",
    "rv_60m",
    "hour_sin",
    "hour_cos",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research cross-hour KXBTCD structure without touching test.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "cross_hour_overlay")
    parser.add_argument("--early-minutes", type=int, default=3)
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--min-entry", type=float, default=0.35)
    parser.add_argument("--max-entry", type=float, default=0.80)
    parser.add_argument("--max-spread-cents", type=float, default=2.0)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[5.0, 8.0, 10.0, 12.0, 15.0])
    parser.add_argument(
        "--include-test",
        action="store_true",
        help="Also evaluate the test split. Off by default; do not use while researching.",
    )
    return parser.parse_args()


def logit(values: np.ndarray | pd.Series) -> np.ndarray:
    x = np.clip(np.asarray(values, dtype=float), 1e-4, 1.0 - 1e-4)
    return np.log(x / (1.0 - x))


def ts_to_py(ts: pd.Timestamp):
    return ts.to_pydatetime()


def load_tables(db_path: Path, early_minutes: int, include_test: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    end = None if include_test else VAL_END
    con = duckdb.connect(str(db_path), read_only=True)
    markets_where = ["is_hourly_kxbtcd", "is_cumulative"]
    params: list[object] = []
    if end is not None:
        markets_where.append("close_time < ?")
        params.append(ts_to_py(end))
    markets = con.execute(
        f"""
        SELECT market_ticker, event_ticker, floor_strike, event_open_time, close_time
        FROM kalshi_markets
        WHERE {' AND '.join(markets_where)}
        """,
        params,
    ).fetchdf()
    if markets.empty:
        con.close()
        return markets, pd.DataFrame(), pd.DataFrame()

    quote_params: list[object] = []
    quote_end_clause = ""
    if end is not None:
        quote_end_clause = "AND m.close_time < ?"
        quote_params.append(ts_to_py(end))
    quote_params.append(int(early_minutes))
    quotes = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, q.yes_ask_exe, q.no_ask_exe,
               q.spread_cents, m.floor_strike, m.event_open_time, m.close_time
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE m.is_hourly_kxbtcd
          AND m.is_cumulative
          {quote_end_clause}
          AND q.available_at > m.event_open_time
          AND q.available_at <= m.event_open_time + (? * INTERVAL '1 minute')
          AND q.ts_end <= m.close_time
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
        """,
        quote_params,
    ).fetchdf()

    boundary_params: list[object] = []
    boundary_end_clause = ""
    if end is not None:
        boundary_end_clause = "AND m.close_time < ?"
        boundary_params.append(ts_to_py(end))
    boundary = con.execute(
        f"""
        SELECT q.market_ticker, q.event_ticker, q.ts_end,
               q.yes_bid_close, q.yes_ask_close, m.floor_strike, m.event_open_time, m.close_time
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        WHERE m.is_hourly_kxbtcd
          AND m.is_cumulative
          {boundary_end_clause}
          AND q.ts_end = m.close_time
        """,
        boundary_params,
    ).fetchdf()

    btc_start = pd.to_datetime(markets["event_open_time"], utc=True).min() - pd.Timedelta(days=10)
    btc_end = pd.to_datetime(markets["close_time"], utc=True).max() + pd.Timedelta(minutes=1)
    btc = con.execute(
        """
        SELECT available_at AS time, open, high, low, close, volume, log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m
        FROM btc_1m
        WHERE available_at >= ? AND available_at <= ?
        ORDER BY available_at
        """,
        [ts_to_py(btc_start), ts_to_py(btc_end)],
    ).fetchdf()
    con.close()

    for frame in (markets, quotes, boundary):
        for col in ("event_open_time", "close_time", "available_at", "ts_end"):
            if col in frame:
                frame[col] = pd.to_datetime(frame[col], utc=True)
    btc["time"] = pd.to_datetime(btc["time"], utc=True)
    return quotes, boundary, feature_frame_for_strategy("research", btc)


def btc_at_or_before(btc: pd.DataFrame, ts: pd.Timestamp) -> tuple[float | None, int | None]:
    values = btc["time"].dt.tz_localize(None).to_numpy()
    lookup = ts.tz_convert("UTC").tz_localize(None).to_datetime64()
    idx = int(np.searchsorted(values, lookup, side="right")) - 1
    if idx < 0 or idx >= len(btc):
        return None, None
    return float(btc.iloc[idx]["close"]), idx


def build_event_cache(btc: pd.DataFrame, event_open: pd.Timestamp, train_days: int) -> dict | None:
    train = btc[(btc["time"] >= event_open - pd.Timedelta(days=train_days)) & (btc["time"] <= event_open)].copy()
    min_rows = 1440 + max(CFG["emp_horizons"]) + 1
    if len(train) < min_rows:
        return None
    return build_emp_cache_fast(train)


def split_name(close_time: pd.Timestamp) -> str:
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def build_candidates(quotes: pd.DataFrame, boundary: pd.DataFrame, btc: pd.DataFrame, train_days: int) -> pd.DataFrame:
    CFG.update(RESEARCH_CFG)
    boundary = boundary.copy()
    boundary["prev_mid"] = 0.5 * (boundary["yes_bid_close"].astype(float) + boundary["yes_ask_close"].astype(float))
    boundary_by_event = {event: group for event, group in boundary.groupby("event_ticker", sort=True)}
    events_meta = (
        quotes.groupby("event_ticker", sort=True)
        .agg(event_open_time=("event_open_time", "min"), close_time=("close_time", "max"))
        .reset_index()
        .sort_values("event_open_time")
    )
    prev_by_event: dict[str, str] = {}
    prev_close_by_event: dict[str, pd.Timestamp] = {}
    prev_row = None
    for row in events_meta.itertuples(index=False):
        if prev_row is not None and pd.Timestamp(prev_row.close_time) == pd.Timestamp(row.event_open_time):
            prev_by_event[str(row.event_ticker)] = str(prev_row.event_ticker)
            prev_close_by_event[str(row.event_ticker)] = pd.Timestamp(prev_row.close_time)
        prev_row = row

    rows: list[pd.DataFrame] = []
    cache_by_event: dict[str, dict] = {}
    for idx, (event_ticker, event_quotes) in enumerate(quotes.groupby("event_ticker", sort=True), start=1):
        if idx == 1 or idx % 200 == 0:
            print(f"candidate event {idx}/{events_meta.shape[0]} {event_ticker}", flush=True)
        prev_event = prev_by_event.get(str(event_ticker))
        if not prev_event:
            continue
        prev_boundary = boundary_by_event.get(prev_event)
        if prev_boundary is None or prev_boundary.empty:
            continue
        event_open = pd.Timestamp(event_quotes["event_open_time"].iloc[0])
        close_time = pd.Timestamp(event_quotes["close_time"].iloc[0])
        emp_cache = cache_by_event.get(str(event_ticker))
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, train_days)
            if emp_cache is None:
                continue
            cache_by_event[str(event_ticker)] = emp_cache
        boundary_spot, _ = btc_at_or_before(btc, prev_close_by_event[str(event_ticker)])
        settle_spot, _ = btc_at_or_before(btc, close_time)
        if boundary_spot is None or settle_spot is None:
            continue
        for scan_ts, scan_quotes in event_quotes.groupby("available_at", sort=True):
            spot, btc_idx = btc_at_or_before(btc, pd.Timestamp(scan_ts))
            if spot is None or btc_idx is None or btc_idx < 1440:
                continue
            ttl_min = (close_time - pd.Timestamp(scan_ts)).total_seconds() / 60.0
            if ttl_min <= 5.0 or ttl_min > 65.0:
                continue
            q = scan_quotes.merge(
                prev_boundary[["floor_strike", "prev_mid"]],
                on="floor_strike",
                how="inner",
            )
            if q.empty:
                continue
            strikes = q["floor_strike"].to_numpy(dtype=float)
            rv60 = btc.iloc[btc_idx].get("rv_60m", np.nan)
            current_vol = float(rv60) if np.isfinite(rv60) else None
            base_p = vectorized_p_above(strikes, spot, ttl_min, emp_cache, current_vol, BRTI_DAMPENING)
            normal_p = lognormal_p_above(strikes, spot, ttl_min, current_vol)
            base_p = np.where(np.isfinite(normal_p), 0.70 * base_p + 0.30 * normal_p, base_p)
            finite = np.isfinite(base_p)
            if not finite.any():
                continue
            q = q.iloc[np.where(finite)[0]].copy()
            base_p = base_p[finite]
            strikes = q["floor_strike"].to_numpy(dtype=float)
            market_mid = 0.5 * (q["yes_bid_close"].to_numpy(dtype=float) + q["yes_ask_close"].to_numpy(dtype=float))
            q["base_p"] = base_p
            q["market_mid"] = market_mid
            q["base_logit"] = logit(base_p)
            q["market_logit"] = logit(market_mid)
            q["prev_logit"] = logit(q["prev_mid"])
            q["mid_gap"] = q["prev_mid"].to_numpy(dtype=float) - market_mid
            q["prev_settled_yes"] = (boundary_spot >= strikes).astype(float)
            q["moneyness_bps"] = 10000.0 * (spot - strikes) / max(1.0, spot)
            q["boundary_moneyness_bps"] = 10000.0 * (boundary_spot - strikes) / max(1.0, boundary_spot)
            q["spot_move_bps"] = 10000.0 * (spot / boundary_spot - 1.0)
            q["minute_in_event"] = (pd.Timestamp(scan_ts) - event_open).total_seconds() / 60.0
            q["rv_15m"] = float(btc.iloc[btc_idx].get("rv_15m", np.nan))
            q["rv_60m"] = float(btc.iloc[btc_idx].get("rv_60m", np.nan))
            hour = pd.Timestamp(scan_ts).hour + pd.Timestamp(scan_ts).minute / 60.0
            q["hour_sin"] = math.sin(2.0 * math.pi * hour / 24.0)
            q["hour_cos"] = math.cos(2.0 * math.pi * hour / 24.0)
            q["settled_yes"] = (settle_spot >= strikes).astype(int)
            q["settlement_spot"] = settle_spot
            q["entry_spot"] = spot
            q["split"] = split_name(close_time)
            rows.append(q)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURES + ["settled_yes"])
    return out


def probability_metrics(frame: pd.DataFrame, prob_col: str) -> dict:
    y = frame["settled_yes"].astype(int).to_numpy()
    p = np.clip(frame[prob_col].astype(float).to_numpy(), 1e-5, 1 - 1e-5)
    return {
        "rows": int(len(frame)),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
    }


def select_trades(frame: pd.DataFrame, threshold_cents: float, args: argparse.Namespace) -> pd.DataFrame:
    q = frame.copy()
    yes_entry = q["yes_ask_exe"].astype(float).to_numpy()
    no_entry = q["no_ask_exe"].astype(float).to_numpy()
    p = q["overlay_p"].astype(float).to_numpy()
    yes_fee = np.asarray([kalshi_fee_dollars(float(price), liquidity="taker") for price in yes_entry])
    no_fee = np.asarray([kalshi_fee_dollars(float(price), liquidity="taker") for price in no_entry])
    yes_edge = (p - yes_entry) * 100.0 - yes_fee * 100.0
    no_edge = ((1.0 - p) - no_entry) * 100.0 - no_fee * 100.0
    choose_yes = yes_edge >= no_edge
    q["side"] = np.where(choose_yes, "yes", "no")
    q["entry_price"] = np.where(choose_yes, yes_entry, no_entry)
    q["entry_fee"] = np.where(choose_yes, yes_fee, no_fee)
    q["net_edge_cents"] = np.where(choose_yes, yes_edge, no_edge)
    passed = (
        (q["spread_cents"].astype(float) <= float(args.max_spread_cents))
        & (q["entry_price"] >= float(args.min_entry))
        & (q["entry_price"] <= float(args.max_entry))
        & (q["net_edge_cents"] >= float(threshold_cents))
    )
    q = q[passed].sort_values(["event_ticker", "available_at", "net_edge_cents"], ascending=[True, True, False])
    if q.empty:
        return q
    trades = q.groupby("event_ticker", as_index=False, sort=False).head(1).copy()
    side_yes = trades["side"].eq("yes")
    win = (side_yes & trades["settled_yes"].eq(1)) | (~side_yes & trades["settled_yes"].eq(0))
    trades["payout"] = win.astype(float)
    trades["pnl"] = trades["payout"] - trades["entry_price"].astype(float) - trades["entry_fee"].astype(float)
    return trades


def summarize_trades(trades: pd.DataFrame, split: str, threshold: float) -> dict:
    if trades.empty:
        return {
            "split": split,
            "threshold_cents": threshold,
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
        }
    ordered = trades.sort_values(["close_time", "available_at", "market_ticker"]).reset_index(drop=True)
    pnl = ordered["pnl"].astype(float)
    premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    drawdown = equity - equity.cummax()
    return {
        "split": split,
        "threshold_cents": threshold,
        "trades": int(len(ordered)),
        "pnl": float(pnl.sum()),
        "premium": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "max_drawdown": float(drawdown.min()),
    }


def main() -> int:
    args = parse_args()
    quotes, boundary, btc = load_tables(args.db, args.early_minutes, args.include_test)
    candidates = build_candidates(quotes, boundary, btc, args.train_days)
    if candidates.empty:
        raise SystemExit("No cross-hour candidates built.")

    train = candidates[candidates["split"].eq("train")].copy()
    validation = candidates[candidates["split"].eq("validation")].copy()
    if train.empty or validation.empty:
        raise SystemExit("Need non-empty train and validation splits.")

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.10, penalty="l2", solver="lbfgs", max_iter=1000, class_weight="balanced"),
    )
    model.fit(train[FEATURES], train["settled_yes"].astype(int))
    candidates["overlay_p"] = model.predict_proba(candidates[FEATURES])[:, 1]

    metric_rows = []
    for split in ["train", "validation"] + (["test"] if args.include_test else []):
        part = candidates[candidates["split"].eq(split)]
        if part.empty:
            continue
        metric_rows.append({"split": split, "model": "base_research", **probability_metrics(part, "base_p")})
        metric_rows.append({"split": split, "model": "market_mid", **probability_metrics(part, "market_mid")})
        metric_rows.append({"split": split, "model": "cross_hour_overlay", **probability_metrics(part, "overlay_p")})

    trade_rows = []
    detail_frames = []
    for threshold in args.thresholds:
        for split in ["train", "validation"] + (["test"] if args.include_test else []):
            part = candidates[candidates["split"].eq(split)]
            trades = select_trades(part, threshold, args)
            trade_rows.append(summarize_trades(trades, split, threshold))
            if not trades.empty:
                detail = trades.copy()
                detail["threshold_cents"] = threshold
                detail_frames.append(detail)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = args.output_dir / "cross_hour_overlay_candidates.parquet"
    metrics_path = args.output_dir / "cross_hour_overlay_metrics.csv"
    summary_path = args.output_dir / "cross_hour_overlay_trade_summary.csv"
    trades_path = args.output_dir / "cross_hour_overlay_trade_detail.csv"
    report_path = args.output_dir / "cross_hour_overlay_report.json"
    candidates.to_parquet(candidates_path, index=False)
    pd.DataFrame(metric_rows).to_csv(metrics_path, index=False)
    pd.DataFrame(trade_rows).to_csv(summary_path, index=False)
    if detail_frames:
        pd.concat(detail_frames, ignore_index=True).to_csv(trades_path, index=False)
    else:
        pd.DataFrame().to_csv(trades_path, index=False)
    report = {
        "db": str(args.db),
        "early_minutes": args.early_minutes,
        "splits": ["train", "validation"] + (["test"] if args.include_test else []),
        "features": FEATURES,
        "candidate_rows": int(len(candidates)),
        "metrics_path": str(metrics_path),
        "summary_path": str(summary_path),
        "trades_path": str(trades_path),
        "candidates_path": str(candidates_path),
        "execution_source": "first observed current-event bid/ask candles; previous-event same-strike boundary quote only",
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(pd.DataFrame(metric_rows).to_string(index=False))
    print(pd.DataFrame(trade_rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
