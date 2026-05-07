#!/usr/bin/env python3
"""Test whether inter-market structure helps existing KXBTCD strategies.

The model is trained on train-period quote rows only. It first compares a
current-market feature set with an inter-market feature set, then applies the
inter-market score as an overlap/filter on existing strategy trades.
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

from scripts.backtest_research_duckdb import DEFAULT_DB


TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-04-21T00:00:00Z")
DEFAULT_TRADES = [
    PROJECT_ROOT / "backtest_outputs" / "research_duckdb_chunked_bidask" / "backtest_research_duckdb_trades.csv",
    PROJECT_ROOT / "backtest_outputs" / "js_robust_chunked_bidask_full" / "backtest_js_robust_duckdb_trades.csv",
]
CURRENT_FEATURES = [
    "market_logit",
    "moneyness_bps",
    "abs_moneyness_bps",
    "spread_cents",
    "minute_in_event",
    "ttl_min",
    "rv_15m",
    "rv_60m",
    "hour_sin",
    "hour_cos",
]
INTERMARKET_FEATURES = CURRENT_FEATURES + [
    "prev_logit",
    "prev_mid",
    "mid_gap",
    "prev_settled_yes",
    "boundary_moneyness_bps",
    "spot_move_bps",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate inter-market signal as an overlay on existing trades.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "intermarket_overlap")
    parser.add_argument("--trade-csv", type=Path, action="append", default=None)
    parser.add_argument("--max-minute", type=int, default=55)
    parser.add_argument("--min-ttl", type=int, default=5)
    parser.add_argument("--max-train-rows", type=int, default=350_000)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[-5.0, 0.0, 2.0, 5.0, 8.0])
    parser.add_argument("--include-test", action="store_true", help="Include held-out test rows. Off during research.")
    return parser.parse_args()


def ts_to_py(ts: pd.Timestamp):
    return ts.to_pydatetime()


def split_name(close_time: pd.Timestamp) -> str:
    close_time = close_time.tz_convert("UTC")
    if close_time < TRAIN_END:
        return "train"
    if close_time < VAL_END:
        return "validation"
    return "test"


def logit(values: pd.Series | np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(values, dtype=float), 1e-4, 1.0 - 1e-4)
    return np.log(x / (1.0 - x))


def load_candidate_rows(db_path: Path, max_minute: int, min_ttl: int, include_test: bool) -> pd.DataFrame:
    end_clause = ""
    params: list[object] = []
    if not include_test:
        end_clause = "AND m.close_time < ?"
        params.append(ts_to_py(VAL_END))
    params.extend([int(max_minute), int(min_ttl)])
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(
        f"""
        WITH event_meta AS (
            SELECT event_ticker, min(event_open_time) AS event_open_time, max(close_time) AS close_time
            FROM kalshi_markets
            WHERE is_hourly_kxbtcd AND is_cumulative
            GROUP BY event_ticker
        ),
        adjacent AS (
            SELECT curr.event_ticker, prev.event_ticker AS prev_event_ticker,
                   curr.event_open_time, curr.close_time, prev.close_time AS boundary_time
            FROM event_meta curr
            JOIN event_meta prev ON curr.event_open_time = prev.close_time
        ),
        prev_boundary AS (
            SELECT m.event_ticker, m.floor_strike,
                   0.5 * (q.yes_bid_close + q.yes_ask_close) AS prev_mid
            FROM kalshi_quotes q
            JOIN kalshi_markets m USING (market_ticker)
            WHERE m.is_hourly_kxbtcd
              AND m.is_cumulative
              AND q.ts_end = m.close_time
        )
        SELECT q.market_ticker, q.event_ticker, a.prev_event_ticker,
               q.available_at, q.ts_end, m.event_open_time, m.close_time,
               m.floor_strike, q.yes_bid_close, q.yes_ask_close,
               q.yes_ask_exe, q.no_ask_exe, q.spread_cents,
               pb.prev_mid,
               bnow.close AS entry_spot, bnow.rv_15m, bnow.rv_60m,
               bboundary.close AS boundary_spot,
               bsettle.close AS settlement_spot
        FROM kalshi_quotes q
        JOIN kalshi_markets m USING (market_ticker)
        JOIN adjacent a ON a.event_ticker = m.event_ticker
        JOIN prev_boundary pb
          ON pb.event_ticker = a.prev_event_ticker
         AND pb.floor_strike = m.floor_strike
        JOIN btc_1m bnow ON bnow.available_at = q.available_at
        JOIN btc_1m bboundary ON bboundary.available_at = a.boundary_time
        JOIN btc_1m bsettle ON bsettle.available_at = m.close_time
        WHERE m.is_hourly_kxbtcd
          AND m.is_cumulative
          {end_clause}
          AND q.ts_end <= m.close_time
          AND date_diff('minute', m.event_open_time, q.available_at) BETWEEN 1 AND ?
          AND date_diff('minute', q.available_at, m.close_time) >= ?
          AND q.spread_cents IS NOT NULL
        ORDER BY q.event_ticker, q.available_at, q.market_ticker
        """,
        params,
    ).fetchdf()
    con.close()
    for col in ("available_at", "ts_end", "event_open_time", "close_time"):
        rows[col] = pd.to_datetime(rows[col], utc=True)
    return rows


def add_features(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    rows["market_mid"] = 0.5 * (rows["yes_bid_close"].astype(float) + rows["yes_ask_close"].astype(float))
    rows["market_logit"] = logit(rows["market_mid"])
    rows["prev_mid"] = rows["prev_mid"].astype(float)
    rows["prev_logit"] = logit(rows["prev_mid"])
    rows["mid_gap"] = rows["prev_mid"] - rows["market_mid"]
    rows["prev_settled_yes"] = (rows["boundary_spot"].astype(float) >= rows["floor_strike"].astype(float)).astype(float)
    rows["settled_yes"] = (rows["settlement_spot"].astype(float) >= rows["floor_strike"].astype(float)).astype(int)
    rows["moneyness_bps"] = 10000.0 * (
        rows["entry_spot"].astype(float) - rows["floor_strike"].astype(float)
    ) / rows["entry_spot"].astype(float).clip(lower=1.0)
    rows["abs_moneyness_bps"] = rows["moneyness_bps"].abs()
    rows["boundary_moneyness_bps"] = 10000.0 * (
        rows["boundary_spot"].astype(float) - rows["floor_strike"].astype(float)
    ) / rows["boundary_spot"].astype(float).clip(lower=1.0)
    rows["spot_move_bps"] = 10000.0 * (rows["entry_spot"].astype(float) / rows["boundary_spot"].astype(float) - 1.0)
    rows["minute_in_event"] = (
        rows["available_at"] - rows["event_open_time"]
    ).dt.total_seconds() / 60.0
    rows["ttl_min"] = (rows["close_time"] - rows["available_at"]).dt.total_seconds() / 60.0
    hour = rows["available_at"].dt.hour + rows["available_at"].dt.minute / 60.0
    rows["hour_sin"] = np.sin(2.0 * math.pi * hour / 24.0)
    rows["hour_cos"] = np.cos(2.0 * math.pi * hour / 24.0)
    rows["split"] = [split_name(ts) for ts in rows["close_time"]]
    needed = INTERMARKET_FEATURES + [
        "market_ticker",
        "available_at",
        "settled_yes",
        "yes_ask_exe",
        "no_ask_exe",
        "split",
    ]
    return rows.replace([np.inf, -np.inf], np.nan).dropna(subset=needed)


def fit_model(train: pd.DataFrame, features: list[str], max_rows: int) -> object:
    sample = train
    if len(train) > max_rows:
        parts = []
        for _, group in train.groupby("settled_yes"):
            parts.append(group.sample(min(len(group), max_rows // 2), random_state=17))
        sample = pd.concat(parts, ignore_index=True).sample(frac=1.0, random_state=17)
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.10, penalty="l2", solver="lbfgs", max_iter=1000, class_weight="balanced"),
    )
    model.fit(sample[features], sample["settled_yes"].astype(int))
    return model


def probability_metrics(frame: pd.DataFrame, prob_col: str) -> dict:
    y = frame["settled_yes"].astype(int).to_numpy()
    p = np.clip(frame[prob_col].astype(float).to_numpy(), 1e-5, 1 - 1e-5)
    return {
        "rows": int(len(frame)),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
    }


def load_trades(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        df = pd.read_csv(path, parse_dates=["entry_time", "close_time", "settle_time"])
        if df.empty:
            continue
        df["trade_source"] = path.parent.name
        frames.append(df)
    if not frames:
        raise SystemExit("No trade CSV rows found.")
    trades = pd.concat(frames, ignore_index=True)
    trades["split"] = [split_name(pd.Timestamp(ts)) for ts in trades["close_time"]]
    return trades


def summarize_group(group: pd.DataFrame, strategy: str, split: str, rule: str) -> dict:
    if group.empty:
        return {
            "strategy": strategy,
            "split": split,
            "rule": rule,
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
        }
    ordered = group.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
    pnl = ordered["pnl"].astype(float)
    premium = ordered["entry_price"].astype(float) + ordered["entry_fee"].astype(float)
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    drawdown = equity - equity.cummax()
    return {
        "strategy": strategy,
        "split": split,
        "rule": rule,
        "trades": int(len(ordered)),
        "pnl": float(pnl.sum()),
        "premium": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
    }


def evaluate_overlap(trades: pd.DataFrame, features: pd.DataFrame, thresholds: list[float], include_test: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    feature_cols = ["market_ticker", "available_at", "current_p", "intermarket_p"]
    joined = trades.merge(
        features[feature_cols],
        left_on=["market_ticker", "entry_time"],
        right_on=["market_ticker", "available_at"],
        how="left",
    )
    matched = joined.dropna(subset=["intermarket_p"]).copy()
    side_yes = matched["side"].eq("yes")
    matched["intermarket_p_win"] = np.where(side_yes, matched["intermarket_p"], 1.0 - matched["intermarket_p"])
    matched["current_p_win"] = np.where(side_yes, matched["current_p"], 1.0 - matched["current_p"])
    matched["intermarket_edge_cents"] = (
        matched["intermarket_p_win"].astype(float)
        - matched["entry_price"].astype(float)
        - matched["entry_fee"].astype(float)
    ) * 100.0
    matched["current_edge_cents"] = (
        matched["current_p_win"].astype(float)
        - matched["entry_price"].astype(float)
        - matched["entry_fee"].astype(float)
    ) * 100.0

    splits = ["train", "validation"] + (["test"] if include_test else [])
    rows = []
    for strategy, full_strat_df in trades.groupby("strategy", sort=True):
        matched_strat_df = matched[matched["strategy"].eq(strategy)]
        for split in splits:
            full_base = full_strat_df[full_strat_df["split"].eq(split)]
            rows.append(summarize_group(full_base, strategy, split, "baseline_all"))
            base = matched_strat_df[matched_strat_df["split"].eq(split)]
            matched_row = summarize_group(base, strategy, split, "baseline_intermarket_available")
            matched_row["missing_signal_trades"] = int(len(full_base) - len(base))
            rows.append(matched_row)
            for threshold in thresholds:
                kept = base[base["intermarket_edge_cents"] >= threshold]
                rows.append(summarize_group(kept, strategy, split, f"intermarket_edge_ge_{threshold:g}c"))
                current_kept = base[base["current_edge_cents"] >= threshold]
                rows.append(summarize_group(current_kept, strategy, split, f"current_edge_ge_{threshold:g}c"))
            agree = base[base["intermarket_p_win"] >= 0.50]
            rows.append(summarize_group(agree, strategy, split, "intermarket_agrees"))
    return pd.DataFrame(rows), matched


def main() -> int:
    args = parse_args()
    trade_paths = args.trade_csv or DEFAULT_TRADES
    raw = load_candidate_rows(args.db, args.max_minute, args.min_ttl, args.include_test)
    features = add_features(raw)
    train = features[features["split"].eq("train")].copy()
    validation = features[features["split"].eq("validation")].copy()
    if train.empty or validation.empty:
        raise SystemExit("Need non-empty train and validation splits.")
    current_model = fit_model(train, CURRENT_FEATURES, args.max_train_rows)
    intermarket_model = fit_model(train, INTERMARKET_FEATURES, args.max_train_rows)
    features["current_p"] = current_model.predict_proba(features[CURRENT_FEATURES])[:, 1]
    features["intermarket_p"] = intermarket_model.predict_proba(features[INTERMARKET_FEATURES])[:, 1]

    metric_rows = []
    for split in ["train", "validation"] + (["test"] if args.include_test else []):
        part = features[features["split"].eq(split)]
        if part.empty:
            continue
        metric_rows.append({"split": split, "model": "market_mid", **probability_metrics(part, "market_mid")})
        metric_rows.append({"split": split, "model": "current_only", **probability_metrics(part, "current_p")})
        metric_rows.append({"split": split, "model": "intermarket", **probability_metrics(part, "intermarket_p")})

    trades = load_trades(trade_paths)
    if not args.include_test:
        trades = trades[trades["split"].isin(["train", "validation"])].copy()
    overlap_summary, overlap_detail = evaluate_overlap(trades, features, args.thresholds, args.include_test)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / "intermarket_signal_metrics.csv"
    overlap_path = args.output_dir / "intermarket_overlap_summary.csv"
    detail_path = args.output_dir / "intermarket_overlap_trade_detail.csv"
    report_path = args.output_dir / "intermarket_overlap_report.json"
    pd.DataFrame(metric_rows).to_csv(metrics_path, index=False)
    overlap_summary.to_csv(overlap_path, index=False)
    overlap_detail.to_csv(detail_path, index=False)
    report = {
        "db": str(args.db),
        "trade_csv": [str(path) for path in trade_paths],
        "splits": ["train", "validation"] + (["test"] if args.include_test else []),
        "max_minute": args.max_minute,
        "min_ttl": args.min_ttl,
        "candidate_rows": int(len(features)),
        "current_features": CURRENT_FEATURES,
        "intermarket_features": INTERMARKET_FEATURES,
        "metrics_path": str(metrics_path),
        "overlap_summary_path": str(overlap_path),
        "overlap_detail_path": str(detail_path),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(pd.DataFrame(metric_rows).to_string(index=False))
    print(overlap_summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
