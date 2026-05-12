#!/usr/bin/env python3
"""Iterative signal research over historical and live-captured BTC 1h data.

This is a read-only research harness. It evaluates many leakage-safe filter
hypotheses on historical replay trades first, then scores the same predicates
on live websocket-selected candidates as a final gate.

The websocket section is intentionally limited: it tests whether a rule would
accept or reject signals the live scanners actually emitted. It does not claim
to discover alternate entries that the live scanners never selected.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.live_capture_backtest_audit import OfficialResults


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "iterative_signal_research_desk_20260511"
DEFAULT_HISTORICAL = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "live_shadow_accuracy_20260510"
    / "historical_may8_variants"
    / "may8examine_trades.csv"
)
DEFAULT_CSV_TRADES = PROJECT_ROOT / "backtest_outputs" / "may8examine_csv_2c" / "may8examine_trades.csv"
DEFAULT_HIST_API = PROJECT_ROOT / "backtest_outputs" / "may8examine_historical" / "may8examine_trades.csv"
DEFAULT_HOLDOUT = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "holdout_late_only_features.csv"
DEFAULT_AUDIT = (
    PROJECT_ROOT / "backtest_outputs" / "live_shadow_accuracy_20260510" / "official_live_shadow_trade_audit.csv"
)
DEFAULT_CAPTURE_DB = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless.duckdb"
DEFAULT_CORE_TRADES = PROJECT_ROOT / "backtest_outputs" / "core_model_research_20260510_skipcal" / "core_model_trades.csv"
DEFAULT_CORE_SUMMARY = PROJECT_ROOT / "backtest_outputs" / "core_model_research_20260510_skipcal" / "core_model_summary.csv"
DEFAULT_CACHE_SEEDS = [
    PROJECT_ROOT / "backtest_outputs" / "live_shadow_accuracy_20260510" / "official_results_cache.json",
    PROJECT_ROOT / "backtest_outputs" / "scaling_sizing_research_edge_20260510" / "official_results_cache.json",
]

TRAIN_END = pd.Timestamp("2026-04-01T00:00:00Z")
VALIDATION_END = pd.Timestamp("2026-04-21T00:00:00Z")
NY_TZ = ZoneInfo("America/New_York")
EVENT_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")
STRIKE_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)", re.IGNORECASE)
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class Predicate:
    name: str
    description: str
    fn: Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class Hypothesis:
    round_no: int
    hypothesis_id: str
    description: str
    predicates: tuple[Predicate, ...]
    source: str = "generated"

    def mask(self, frame: pd.DataFrame) -> pd.Series:
        out = pd.Series(True, index=frame.index)
        for pred in self.predicates:
            out &= safe_mask(pred.fn(frame), frame.index)
        return out.fillna(False)


def utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def strike_from_ticker(ticker: str) -> float:
    match = STRIKE_RE.search(str(ticker).upper())
    if not match:
        return float("nan")
    return float(match.group("strike")) + 0.01


def close_from_event(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker).upper())
    if not match:
        return None
    month = MONTHS.get(match.group("mon"))
    if month is None:
        return None
    return pd.Timestamp(
        year=2000 + int(match.group("yy")),
        month=month,
        day=int(match.group("day")),
        hour=int(match.group("hour")),
        tz=NY_TZ,
    ).tz_convert("UTC")


def safe_mask(mask: pd.Series | np.ndarray | bool, index: pd.Index) -> pd.Series:
    if isinstance(mask, (bool, np.bool_)):
        return pd.Series(bool(mask), index=index)
    out = mask if isinstance(mask, pd.Series) else pd.Series(mask, index=index)
    return out.reindex(index).fillna(False).astype(bool)


def max_drawdown(values: pd.Series) -> float:
    clean = values.dropna().astype(float)
    if clean.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), clean.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def sharpe_like(values: pd.Series) -> float:
    clean = values.dropna().astype(float)
    if len(clean) < 2:
        return 0.0
    std = clean.std(ddof=1)
    if not math.isfinite(std) or std == 0:
        return 0.0
    return float(clean.mean() / std * math.sqrt(len(clean)))


def split_from_time(entry_time: pd.Series) -> pd.Series:
    t = utc(entry_time)
    return pd.Series(
        np.where(t < TRAIN_END, "train", np.where(t < VALIDATION_END, "validation", "test")),
        index=entry_time.index,
    )


def add_features(frame: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    df = frame.copy()
    if df.empty:
        return df
    if "entry_time" not in df:
        for col in ("created_at", "received_at_utc", "received_at"):
            if col in df.columns:
                df["entry_time"] = df[col]
                break
    if "market_ticker" not in df and "selected_market" in df.columns:
        df["market_ticker"] = df["selected_market"]
    if "side" not in df and "selected_side" in df.columns:
        df["side"] = df["selected_side"]
    if "close_time" not in df:
        df["close_time"] = df.get("event_ticker", pd.Series(index=df.index, dtype=object)).map(close_from_event)
    df["entry_time"] = utc(df["entry_time"])
    df["close_time"] = utc(df["close_time"])
    if "split" not in df:
        df["split"] = split_from_time(df["entry_time"])
    df["source_name"] = source_name
    df["side"] = df.get("side", "").astype(str).str.lower()
    if "strike" not in df:
        df["strike"] = np.nan
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    missing_strike = df["strike"].isna()
    if missing_strike.any() and "market_ticker" in df:
        df.loc[missing_strike, "strike"] = df.loc[missing_strike, "market_ticker"].map(strike_from_ticker)
    for col in (
        "entry_price",
        "net_edge_cents",
        "edge_threshold_cents",
        "spread_cents",
        "market_mid",
        "entry_spot",
        "btc_spot",
        "model_p_yes",
        "side_probability",
        "distance_sigma",
        "side_ret_5m",
        "side_ret_10m",
        "side_ret_30m",
        "visible_side_ask_qty",
        "visible_side_ask_price",
        "latency_ms",
        "contracts",
        "pnl",
        "premium",
    ):
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df["entry_spot"].isna().all():
        df["entry_spot"] = df["btc_spot"]
    else:
        df["entry_spot"] = df["entry_spot"].fillna(df["btc_spot"])
    if df["contracts"].isna().all():
        df["contracts"] = 1
    df["contracts"] = df["contracts"].fillna(1).astype(int)
    df["ttl_min"] = (df["close_time"] - df["entry_time"]).dt.total_seconds() / 60.0
    df["entry_minute"] = df["entry_time"].dt.minute
    df["entry_hour_utc"] = df["entry_time"].dt.hour
    df["side_distance_usd"] = np.where(
        df["side"].eq("yes"),
        df["entry_spot"] - df["strike"],
        df["strike"] - df["entry_spot"],
    )
    df["abs_distance_usd"] = (df["entry_spot"] - df["strike"]).abs()
    df["side_moneyness_bps"] = 10000.0 * df["side_distance_usd"] / df["entry_spot"].clip(lower=1.0)
    if df["side_probability"].isna().all() and df["model_p_yes"].notna().any():
        df["side_probability"] = np.where(df["side"].eq("yes"), df["model_p_yes"], 1.0 - df["model_p_yes"])
    if df["edge_threshold_cents"].isna().all():
        df["edge_threshold_cents"] = np.nan
    df["edge_margin_cents"] = df["net_edge_cents"] - df["edge_threshold_cents"]
    if "official_result" in df.columns:
        df["official_result"] = df["official_result"].astype(str).str.lower()
    if df["premium"].isna().all():
        fee = df["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker") if pd.notna(p) else np.nan)
        df["premium"] = df["entry_price"] + fee
    return df


def normalize_trade_file(path: Path, source_name: str) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 2:
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "pnl" not in df and "pnl_1c" in df:
        df["pnl"] = df["pnl_1c"]
    if "premium" not in df and "premium_1c" in df:
        df["premium"] = df["premium_1c"]
    if "variant" not in df:
        df["variant"] = source_name
    return add_features(df, source_name=source_name)


def load_historical_sets(args: argparse.Namespace) -> pd.DataFrame:
    frames = [
        normalize_trade_file(args.historical_trades, "historical_duckdb_may8"),
        normalize_trade_file(args.csv_trades, "csv_chart_export_replay"),
        normalize_trade_file(args.historical_api_trades, "historical_api_replay"),
        normalize_trade_file(args.core_model_trades, "core_model_duckdb"),
    ]
    out = pd.concat([f for f in frames if not f.empty], ignore_index=True, sort=False)
    if out.empty:
        return out
    if "variant" not in out:
        out["variant"] = "unknown"
    out["variant"] = out["variant"].astype(str)
    return out


def load_forward_ledgers(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df.get("pnl", pd.Series(dtype=float)).notna()].copy()
    if "ledger" not in df:
        df["ledger"] = "forward_ledger"
    df["variant"] = df["ledger"].astype(str)
    return add_features(df, source_name="official_forward_ledger")


def load_holdout(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "pnl" not in df and "pnl_1c" in df:
        df["pnl"] = df["pnl_1c"]
    if "premium" not in df and "premium_1c" in df:
        df["premium"] = df["premium_1c"]
    df["variant"] = "live_capture_holdout"
    return add_features(df, source_name="live_capture_order_decisions")


def seed_official_cache(cache_path: Path) -> None:
    if cache_path.exists():
        return
    merged: dict[str, object] = {}
    for src in DEFAULT_CACHE_SEEDS:
        if not src.exists():
            continue
        try:
            merged.update(json.loads(src.read_text(encoding="utf-8")))
        except Exception:
            continue
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")


def load_websocket_selected(capture_db: Path, cache_path: Path, max_rows: int | None = None) -> pd.DataFrame:
    if not capture_db.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(capture_db), read_only=True)
    tables = {row[0] for row in con.execute("show tables").fetchall()}
    if "signal_scan_dedup" not in tables:
        con.close()
        return pd.DataFrame()
    limit_sql = f"LIMIT {int(max_rows)}" if max_rows else ""
    # LATERAL keeps this small: signal_scan selected rows are only hundreds.
    selected = con.execute(
        f"""
        WITH s AS (
            SELECT row_number() OVER () AS signal_id,
                   received_at_ns,
                   received_at_utc,
                   capture_label,
                   mode,
                   event_ticker,
                   selected_market AS market_ticker,
                   lower(selected_side) AS side,
                   entry_price,
                   net_edge_cents,
                   model_p_yes,
                   btc_spot,
                   latency_ms,
                   reason
            FROM signal_scan_dedup
            WHERE action = 'selected'
              AND selected_market IS NOT NULL
            ORDER BY received_at_ns
            {limit_sql}
        )
        SELECT s.*,
               t.received_at_ns AS top_received_at_ns,
               t.received_at_utc AS top_received_at_utc,
               t.yes_ask,
               t.yes_ask_qty,
               t.no_ask,
               t.no_ask_qty,
               t.yes_bid,
               t.yes_bid_qty,
               t.no_bid,
               t.no_bid_qty,
               spot_now.price AS spot_now_price,
               spot_5.price AS spot_5m_price,
               spot_10.price AS spot_10m_price,
               spot_30.price AS spot_30m_price
        FROM s
        LEFT JOIN LATERAL (
            SELECT *
            FROM ws_orderbook_top_dedup t
            WHERE t.market_ticker = s.market_ticker
              AND t.capture_label = s.capture_label
              AND t.received_at_ns <= s.received_at_ns
            ORDER BY t.received_at_ns DESC
            LIMIT 1
        ) t ON TRUE
        LEFT JOIN LATERAL (
            SELECT price
            FROM coinbase_ticker_all c
            WHERE c.capture_label = s.capture_label
              AND c.received_at_ns <= s.received_at_ns
            ORDER BY c.received_at_ns DESC
            LIMIT 1
        ) spot_now ON TRUE
        LEFT JOIN LATERAL (
            SELECT price
            FROM coinbase_ticker_all c
            WHERE c.capture_label = s.capture_label
              AND c.received_at_ns <= s.received_at_ns - 300000000000
            ORDER BY c.received_at_ns DESC
            LIMIT 1
        ) spot_5 ON TRUE
        LEFT JOIN LATERAL (
            SELECT price
            FROM coinbase_ticker_all c
            WHERE c.capture_label = s.capture_label
              AND c.received_at_ns <= s.received_at_ns - 600000000000
            ORDER BY c.received_at_ns DESC
            LIMIT 1
        ) spot_10 ON TRUE
        LEFT JOIN LATERAL (
            SELECT price
            FROM coinbase_ticker_all c
            WHERE c.capture_label = s.capture_label
              AND c.received_at_ns <= s.received_at_ns - 1800000000000
            ORDER BY c.received_at_ns DESC
            LIMIT 1
        ) spot_30 ON TRUE
        ORDER BY s.received_at_ns
        """
    ).fetchdf()
    con.close()
    if selected.empty:
        return selected
    selected["entry_time"] = selected["received_at_utc"]
    selected["close_time"] = selected["event_ticker"].map(close_from_event)
    selected["visible_side_ask_qty"] = np.where(selected["side"].eq("yes"), selected["yes_ask_qty"], selected["no_ask_qty"])
    selected["visible_side_ask_price"] = np.where(selected["side"].eq("yes"), selected["yes_ask"], selected["no_ask"])
    spot_now = pd.to_numeric(selected["spot_now_price"], errors="coerce").fillna(pd.to_numeric(selected["btc_spot"], errors="coerce"))
    side_sign = np.where(selected["side"].eq("yes"), 1.0, -1.0)
    for minutes in (5, 10, 30):
        prior = pd.to_numeric(selected[f"spot_{minutes}m_price"], errors="coerce")
        selected[f"side_ret_{minutes}m"] = side_sign * (spot_now - prior)
    selected["price_matches_entry"] = np.isclose(
        pd.to_numeric(selected["visible_side_ask_price"], errors="coerce"),
        pd.to_numeric(selected["entry_price"], errors="coerce"),
        atol=0.001,
        equal_nan=False,
    )
    seed_official_cache(cache_path)
    official = OfficialResults(cache_path)
    results: list[str | None] = []
    statuses: list[str | None] = []
    expiration_values: list[float | None] = []
    for ticker in selected["market_ticker"].astype(str):
        row = official.get(ticker)
        results.append(row.get("result"))
        statuses.append(row.get("status"))
        try:
            expiration_values.append(float(row.get("expiration_value")) if row.get("expiration_value") is not None else None)
        except Exception:
            expiration_values.append(None)
    official.write()
    selected["official_result"] = results
    selected["official_status"] = statuses
    selected["official_expiration_value"] = expiration_values
    selected["settled"] = selected["official_result"].isin(["yes", "no"])
    selected["entry_price"] = pd.to_numeric(selected["entry_price"], errors="coerce")
    fees = selected["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker") if pd.notna(p) else np.nan)
    selected["premium"] = selected["entry_price"] + fees
    selected["pnl"] = np.where(
        selected["settled"],
        np.where(selected["side"].eq(selected["official_result"]), 1.0, 0.0) - selected["premium"],
        np.nan,
    )
    selected["variant"] = selected["mode"].astype(str).str.replace("paper:", "", regex=False)
    return add_features(selected, source_name="websocket_selected_signals")


def make_predicates() -> list[Predicate]:
    def col(name: str, default: float = np.nan) -> Callable[[pd.DataFrame], pd.Series]:
        return lambda d: pd.to_numeric(d.get(name, default), errors="coerce")

    return [
        Predicate("all", "No additional filter", lambda d: pd.Series(True, index=d.index)),
        Predicate("late_5_20", "TTL 5-20 minutes", lambda d: col("ttl_min")(d).between(5, 20)),
        Predicate("late_8_18", "TTL 8-18 minutes", lambda d: col("ttl_min")(d).between(8, 18)),
        Predicate("late_10_20", "TTL 10-20 minutes", lambda d: col("ttl_min")(d).between(10, 20)),
        Predicate("very_late_5_12", "TTL 5-12 minutes", lambda d: col("ttl_min")(d).between(5, 12)),
        Predicate("not_last7", "Avoid final 7 minutes", lambda d: col("ttl_min")(d) >= 7),
        Predicate("yes_only", "YES side only", lambda d: d["side"].eq("yes")),
        Predicate("no_only", "NO side only", lambda d: d["side"].eq("no")),
        Predicate("entry_le55", "Entry <=55c", lambda d: col("entry_price")(d) <= 0.55),
        Predicate("entry_le60", "Entry <=60c", lambda d: col("entry_price")(d) <= 0.60),
        Predicate("entry_le65", "Entry <=65c", lambda d: col("entry_price")(d) <= 0.65),
        Predicate("entry_45_65", "Entry 45-65c", lambda d: col("entry_price")(d).between(0.45, 0.65)),
        Predicate("entry_50_70", "Entry 50-70c", lambda d: col("entry_price")(d).between(0.50, 0.70)),
        Predicate("avoid_fav70", "Avoid entries >70c", lambda d: col("entry_price")(d) <= 0.70),
        Predicate("favorite_gt60", "Favorite entries >60c", lambda d: col("entry_price")(d) > 0.60),
        Predicate("edge_gte14", "Net edge >=14c", lambda d: col("net_edge_cents")(d) >= 14),
        Predicate("edge_gte15", "Net edge >=15c", lambda d: col("net_edge_cents")(d) >= 15),
        Predicate("edge_gte16", "Net edge >=16c", lambda d: col("net_edge_cents")(d) >= 16),
        Predicate("edge_gte18", "Net edge >=18c", lambda d: col("net_edge_cents")(d) >= 18),
        Predicate("edge_lt16", "Net edge <16c", lambda d: col("net_edge_cents")(d) < 16),
        Predicate("side_p_gte68", "Side probability >=68%", lambda d: col("side_probability")(d) >= 0.68),
        Predicate("side_p_gte72", "Side probability >=72%", lambda d: col("side_probability")(d) >= 0.72),
        Predicate("side_p_gte76", "Side probability >=76%", lambda d: col("side_probability")(d) >= 0.76),
        Predicate("side_p_gte80", "Side probability >=80%", lambda d: col("side_probability")(d) >= 0.80),
        Predicate("side_dist_pos", "Side is in the money at entry", lambda d: col("side_distance_usd")(d) > 0),
        Predicate("side_dist_gt25", "Side distance >$25", lambda d: col("side_distance_usd")(d) > 25),
        Predicate("side_dist_gt50", "Side distance >$50", lambda d: col("side_distance_usd")(d) > 50),
        Predicate("side_dist_gt75", "Side distance >$75", lambda d: col("side_distance_usd")(d) > 75),
        Predicate("side_dist_gt100", "Side distance >$100", lambda d: col("side_distance_usd")(d) > 100),
        Predicate("side_dist_lt150", "Side distance <$150", lambda d: col("side_distance_usd")(d) < 150),
        Predicate("distance_sigma_gt05", "Distance >0.5 expected sigma", lambda d: col("distance_sigma")(d) > 0.5),
        Predicate("distance_sigma_gt075", "Distance >0.75 expected sigma", lambda d: col("distance_sigma")(d) > 0.75),
        Predicate("side_ret5_pos", "5m side-aligned momentum positive", lambda d: col("side_ret_5m")(d) > 0),
        Predicate("side_ret10_pos", "10m side-aligned momentum positive", lambda d: col("side_ret_10m")(d) > 0),
        Predicate("side_ret10_gt25", "10m side-aligned momentum >$25", lambda d: col("side_ret_10m")(d) > 25),
        Predicate("side_ret10_gt75", "10m side-aligned momentum >$75", lambda d: col("side_ret_10m")(d) > 75),
        Predicate("side_ret30_pos", "30m side-aligned momentum positive", lambda d: col("side_ret_30m")(d) > 0),
        Predicate("spread_le1", "Spread <=1c", lambda d: col("spread_cents")(d) <= 1.01),
        Predicate("spread_le2", "Spread <=2c", lambda d: col("spread_cents")(d) <= 2.01),
        Predicate("visible_qty_ge3", "Visible top-of-book quantity >=3", lambda d: col("visible_side_ask_qty")(d) >= 3),
        Predicate("visible_qty_ge5", "Visible top-of-book quantity >=5", lambda d: col("visible_side_ask_qty")(d) >= 5),
        Predicate("visible_qty_ge10", "Visible top-of-book quantity >=10", lambda d: col("visible_side_ask_qty")(d) >= 10),
        Predicate("price_match", "Top-of-book ask matches selected entry", lambda d: d.get("price_matches_entry", pd.Series(False, index=d.index)).astype(bool)),
        Predicate("latency_lt1000", "Signal latency under 1000ms", lambda d: col("latency_ms")(d) < 1000),
        Predicate("latency_lt2500", "Signal latency under 2500ms", lambda d: col("latency_ms")(d) < 2500),
    ]


def build_round1(predicates: list[Predicate]) -> list[Hypothesis]:
    return [
        Hypothesis(1, pred.name, pred.description, (pred,), "single_factor")
        for pred in predicates
        if pred.name != "all"
    ] + [Hypothesis(1, "all", "No additional filter baseline", (predicates[0],), "baseline")]


def summarize(
    frame: pd.DataFrame,
    *,
    dataset: str,
    hypothesis: Hypothesis,
    split: str,
    strategy_variant: str = "all",
) -> dict[str, object]:
    part = frame.dropna(subset=["pnl", "premium"]).copy()
    if "entry_time" in part:
        part = part.sort_values(["entry_time", "event_ticker", "market_ticker"])
    pnl = pd.to_numeric(part["pnl"], errors="coerce")
    premium = pd.to_numeric(part["premium"], errors="coerce")
    return {
        "round": hypothesis.round_no,
        "hypothesis_id": hypothesis.hypothesis_id,
        "description": hypothesis.description,
        "hypothesis_source": hypothesis.source,
        "dataset": dataset,
        "strategy_variant": strategy_variant,
        "split": split,
        "rows": int(len(part)),
        "events": int(part["event_ticker"].nunique()) if "event_ticker" in part else 0,
        "premium": float(premium.sum()) if len(part) else 0.0,
        "pnl": float(pnl.sum()) if len(part) else 0.0,
        "return_on_premium": float(pnl.sum() / premium.sum()) if len(part) and premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(part) else 0.0,
        "max_drawdown": max_drawdown(pnl),
        "sharpe_like": sharpe_like(pnl),
        "yes_rows": int(part["side"].eq("yes").sum()) if "side" in part else 0,
        "no_rows": int(part["side"].eq("no").sum()) if "side" in part else 0,
        "avg_entry": float(part["entry_price"].mean()) if len(part) else 0.0,
        "avg_edge": float(part["net_edge_cents"].mean()) if len(part) else 0.0,
        "avg_side_p": float(part["side_probability"].mean()) if len(part) else 0.0,
        "avg_side_dist": float(part["side_distance_usd"].mean()) if len(part) else 0.0,
    }


def first_per_event(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    keys = [c for c in ("source_name", "capture_label", "variant", "mode", "event_ticker") if c in frame.columns]
    if not keys:
        keys = ["event_ticker"]
    return (
        frame.sort_values(["entry_time", "market_ticker"])
        .drop_duplicates(keys, keep="first")
        .sort_values(["entry_time", "event_ticker", "market_ticker"])
    )


def evaluate_dataset(
    frame: pd.DataFrame,
    dataset: str,
    hypotheses: list[Hypothesis],
    *,
    first_event: bool,
    group_variants: bool = False,
    strategy_variant: str = "all",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if frame.empty:
        return pd.DataFrame()
    if group_variants and "variant" in frame.columns:
        pieces = []
        for variant, part in frame.groupby("variant", dropna=False, sort=False):
            pieces.append(
                evaluate_dataset(
                    part.copy(),
                    dataset,
                    hypotheses,
                    first_event=first_event,
                    group_variants=False,
                    strategy_variant=str(variant),
                )
            )
        return pd.concat([p for p in pieces if not p.empty], ignore_index=True, sort=False) if pieces else pd.DataFrame()
    for hyp in hypotheses:
        selected = frame[hyp.mask(frame)].copy()
        if first_event:
            selected = first_per_event(selected)
        for split in ["train", "validation", "test", "all"]:
            part = selected if split == "all" else selected[selected["split"].eq(split)]
            rows.append(summarize(part, dataset=dataset, hypothesis=hyp, split=split, strategy_variant=strategy_variant))
    return pd.DataFrame(rows)


def score_historical(results: pd.DataFrame, *, min_val_rows: int, min_test_rows: int) -> pd.DataFrame:
    score_datasets = ["historical_duckdb_may8"]
    hist = results[(results["dataset"].isin(score_datasets)) & (results["split"].isin(["train", "validation", "test"]))].copy()
    rows: list[dict[str, object]] = []
    for (round_no, hyp_id, desc, strategy_variant), part in hist.groupby(
        ["round", "hypothesis_id", "description", "strategy_variant"], sort=False
    ):
        split_rows = {row["split"]: row for _, row in part.iterrows()}
        if not {"train", "validation", "test"} <= set(split_rows):
            continue
        train = split_rows["train"]
        val = split_rows["validation"]
        test = split_rows["test"]
        all_rows = results[
            (results["round"].eq(round_no))
            & (results["hypothesis_id"].eq(hyp_id))
            & (results["strategy_variant"].eq(strategy_variant))
            & (results["dataset"].isin(score_datasets))
            & (results["split"].eq("all"))
        ]
        all_pnl = float(all_rows["pnl"].sum()) if not all_rows.empty else 0.0
        all_premium = float(all_rows["premium"].sum()) if not all_rows.empty else 0.0
        worst_split = min(float(train["pnl"]), float(val["pnl"]), float(test["pnl"]))
        split_consistent = (
            float(train["pnl"]) > 0
            and float(val["pnl"]) > 0
            and float(test["pnl"]) > 0
            and int(val["rows"]) >= min_val_rows
            and int(test["rows"]) >= min_test_rows
        )
        rows.append(
            {
                "round": int(round_no),
                "hypothesis_id": hyp_id,
                "description": desc,
                "strategy_variant": strategy_variant,
                "train_rows": int(train["rows"]),
                "validation_rows": int(val["rows"]),
                "test_rows": int(test["rows"]),
                "train_pnl": float(train["pnl"]),
                "validation_pnl": float(val["pnl"]),
                "test_pnl": float(test["pnl"]),
                "all_pnl": all_pnl,
                "all_premium": all_premium,
                "all_rop": all_pnl / all_premium if all_premium else 0.0,
                "worst_split_pnl": worst_split,
                "worst_dd": float(min(train["max_drawdown"], val["max_drawdown"], test["max_drawdown"])),
                "split_consistent": split_consistent,
                "score": all_pnl + 2.0 * worst_split + 0.04 * (int(train["rows"]) + int(val["rows"]) + int(test["rows"])) + min(0.0, float(min(train["max_drawdown"], val["max_drawdown"], test["max_drawdown"]))),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["split_consistent", "score", "all_pnl"], ascending=[False, False, False])


def make_combinations(ranked: pd.DataFrame, predicate_map: dict[str, Predicate], round_no: int, limit: int) -> list[Hypothesis]:
    if ranked.empty:
        return []
    candidates = []
    for hyp_id in ranked["hypothesis_id"].astype(str):
        if hyp_id in predicate_map and hyp_id not in {"all"}:
            candidates.append(hyp_id)
    seen: set[str] = set()
    combos: list[Hypothesis] = []
    for i, first in enumerate(candidates[:12]):
        for second in candidates[i + 1 : 16]:
            names = tuple(sorted([first, second]))
            if names in seen:
                continue
            seen.add(names)
            preds = tuple(predicate_map[n] for n in names)
            combos.append(
                Hypothesis(
                    round_no,
                    "and:" + "+".join(names),
                    " AND ".join(p.description for p in preds),
                    preds,
                    "ranked_combo",
                )
            )
            if len(combos) >= limit:
                return combos
    return combos


def evaluate_sources(frame: pd.DataFrame, hypotheses: list[Hypothesis], *, first_event: bool) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    pieces = []
    for source_name, part in frame.groupby("source_name", dropna=False, sort=False):
        pieces.append(
            evaluate_dataset(
                part.copy(),
                str(source_name),
                hypotheses,
                first_event=first_event,
                group_variants=True,
            )
        )
    return pd.concat([p for p in pieces if not p.empty], ignore_index=True, sort=False) if pieces else pd.DataFrame()


def make_literature_hypotheses(predicate_map: dict[str, Predicate], round_no: int) -> list[Hypothesis]:
    def h(name: str, parts: list[str], desc: str) -> Hypothesis:
        return Hypothesis(round_no, name, desc, tuple(predicate_map[p] for p in parts), "literature_informed")

    return [
        h("lit:semivariance_proxy_no_high_p_late", ["late_5_20", "no_only", "side_p_gte72"], "Downside semivariance / bad-vol proxy: late high-confidence NO only."),
        h("lit:momentum_continuation_yes", ["late_5_20", "yes_only", "side_ret10_pos"], "Short-horizon continuation for YES entries."),
        h("lit:momentum_continuation_no", ["late_5_20", "no_only", "side_ret10_pos"], "Short-horizon continuation for NO entries."),
        h("lit:jump_guard", ["late_5_20", "side_ret10_gt75", "avoid_fav70"], "Only trade after a decisive side-aligned move, avoid overpaying."),
        h("lit:micro_liquidity_gate", ["late_5_20", "visible_qty_ge5", "price_match"], "Require executable top-of-book liquidity and exact reprice match."),
        h("lit:micro_latency_gate", ["late_5_20", "latency_lt2500", "price_match"], "Avoid stale/high-latency selected signals."),
        h("lit:near_strike_guard", ["late_5_20", "side_dist_gt50", "entry_le65"], "Require side cushion near expiry without paying >65c."),
        h("lit:market_uncertainty_cheap", ["late_5_20", "entry_45_65", "edge_gte15"], "Prefer mid-priced binaries where model edge is not just favorite carry."),
        h("lit:high_conf_not_chase", ["late_5_20", "side_p_gte76", "avoid_fav70"], "High model probability, avoid expensive favorites."),
        h("lit:strict_exec_quality", ["late_5_20", "visible_qty_ge10", "price_match", "latency_lt1000"], "Strict microstructure quality gate for live execution."),
    ]


def evaluate_variant_families(historical: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if historical.empty or "variant" not in historical:
        return pd.DataFrame()
    for (source, variant, split), part in historical.groupby(["source_name", "variant", "split"], dropna=False):
        rows.append(
            summarize(
                part,
                dataset=str(source),
                hypothesis=Hypothesis(0, str(variant), f"Precomputed strategy/model variant {variant}", (Predicate("all", "all", lambda d: True),), "variant"),
                split=str(split),
                strategy_variant=str(variant),
            )
        )
    for (source, variant), part in historical.groupby(["source_name", "variant"], dropna=False):
        rows.append(
            summarize(
                part,
                dataset=str(source),
                hypothesis=Hypothesis(0, str(variant), f"Precomputed strategy/model variant {variant}", (Predicate("all", "all", lambda d: True),), "variant"),
                split="all",
                strategy_variant=str(variant),
            )
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(out_dir / "precomputed_variant_family_summary.csv", index=False)
    return df


def markdown_table(frame: pd.DataFrame, floatfmt: str = ".3f") -> str:
    if frame.empty:
        return ""
    cols = list(frame.columns)
    string_rows: list[list[str]] = []
    for _, row in frame.iterrows():
        values: list[str] = []
        for col in cols:
            value = row[col]
            if isinstance(value, (float, np.floating)):
                values.append(format(float(value), floatfmt))
            else:
                values.append(str(value))
        string_rows.append(values)
    widths = [len(str(col)) for col in cols]
    for values in string_rows:
        widths = [max(width, len(value)) for width, value in zip(widths, values)]
    header = "| " + " | ".join(str(col).ljust(width) for col, width in zip(cols, widths)) + " |"
    sep = "| " + " | ".join("-" * width for width in widths) + " |"
    body = ["| " + " | ".join(value.ljust(width) for value, width in zip(values, widths)) + " |" for values in string_rows]
    return "\n".join([header, sep] + body)


def write_report(
    out_dir: Path,
    manifest: dict[str, object],
    ranked: pd.DataFrame,
    ws_results: pd.DataFrame,
    variant_summary: pd.DataFrame,
) -> None:
    lines = [
        "# Iterative Signal Research Desk",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Data",
        "",
    ]
    for key, value in manifest.items():
        lines.append(f"- `{key}`: `{value}`")
    lines += [
        "",
        "## Selection Rule",
        "",
        "Historical replay train/validation/test ranks hypotheses. Websocket-selected signals are a final gate only.",
        "A websocket result here means: if the live scanner emitted a selected signal, would this rule have accepted the first accepted signal for that event?",
        "",
        "## Top Historical Candidates",
        "",
    ]
    if ranked.empty:
        lines.append("No ranked candidates.")
    else:
        cols = [
            "round",
            "hypothesis_id",
            "strategy_variant",
            "train_rows",
            "validation_rows",
            "test_rows",
            "train_pnl",
            "validation_pnl",
            "test_pnl",
            "all_pnl",
            "worst_dd",
            "split_consistent",
        ]
        lines.append(markdown_table(ranked.head(25)[cols]))
    if not ws_results.empty:
        lines += ["", "## Websocket Gate Top Rows", ""]
        ws_all = ws_results[(ws_results["dataset"].eq("websocket_selected_signals")) & (ws_results["split"].eq("all"))].copy()
        ws_all = ws_all.sort_values(["pnl", "rows"], ascending=[False, False]).head(25)
        cols = ["round", "hypothesis_id", "strategy_variant", "rows", "events", "pnl", "return_on_premium", "win_rate", "max_drawdown", "avg_entry", "avg_edge"]
        lines.append(markdown_table(ws_all[cols]))
    if not variant_summary.empty:
        lines += ["", "## Strongest Precomputed Model/Strategy Families", ""]
        all_rows = variant_summary[variant_summary["split"].eq("all")].copy()
        all_rows = all_rows.sort_values(["pnl", "rows"], ascending=[False, False]).head(25)
        cols = ["dataset", "strategy_variant", "hypothesis_id", "rows", "pnl", "return_on_premium", "win_rate", "max_drawdown"]
        lines.append(markdown_table(all_rows[cols]))
    lines += [
        "",
        "## Caveats",
        "",
        "- CSV chart-export replay is included for diagnostics but is not promotion-grade.",
        "- Historical API candles are minute/candle bid-ask observations, not exact websocket execution.",
        "- Websocket selected-signal data is promotion-grade only for gates on signals the live scanner actually emitted.",
        "- Alternate core models still need implementation in the live websocket scanner before true live-capture replay.",
    ]
    (out_dir / "research_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-trades", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--csv-trades", type=Path, default=DEFAULT_CSV_TRADES)
    parser.add_argument("--historical-api-trades", type=Path, default=DEFAULT_HIST_API)
    parser.add_argument("--holdout-features", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--official-audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--core-model-trades", type=Path, default=DEFAULT_CORE_TRADES)
    parser.add_argument("--core-model-summary", type=Path, default=DEFAULT_CORE_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-validation-rows", type=int, default=5)
    parser.add_argument("--min-test-rows", type=int, default=5)
    parser.add_argument("--max-ws-selected-rows", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    historical = load_historical_sets(args)
    forward = load_forward_ledgers(args.official_audit)
    holdout = load_holdout(args.holdout_features)
    cache_path = args.output_dir / "official_results_cache.json"
    websocket = load_websocket_selected(args.capture_db, cache_path, args.max_ws_selected_rows)

    predicates = make_predicates()
    predicate_map = {p.name: p for p in predicates}
    round1 = build_round1(predicates)
    lit = make_literature_hypotheses(predicate_map, 2)
    first_pass_results = pd.concat(
        [
            evaluate_sources(historical, round1 + lit, first_event=False),
            evaluate_dataset(websocket, "websocket_selected_signals", round1 + lit, first_event=True, group_variants=True),
            evaluate_dataset(holdout, "live_capture_order_decisions", round1 + lit, first_event=False, group_variants=True),
            evaluate_dataset(forward, "official_forward_ledger", round1 + lit, first_event=False, group_variants=True),
        ],
        ignore_index=True,
        sort=False,
    )
    first_rank = score_historical(first_pass_results, min_val_rows=args.min_validation_rows, min_test_rows=args.min_test_rows)
    round3 = make_combinations(first_rank, predicate_map, 3, 60)
    second_pass_results = pd.concat(
        [
            evaluate_sources(historical, round3, first_event=False),
            evaluate_dataset(websocket, "websocket_selected_signals", round3, first_event=True, group_variants=True),
            evaluate_dataset(holdout, "live_capture_order_decisions", round3, first_event=False, group_variants=True),
            evaluate_dataset(forward, "official_forward_ledger", round3, first_event=False, group_variants=True),
        ],
        ignore_index=True,
        sort=False,
    )
    all_results = pd.concat([first_pass_results, second_pass_results], ignore_index=True, sort=False)
    ranked = score_historical(all_results, min_val_rows=args.min_validation_rows, min_test_rows=args.min_test_rows)
    variant_summary = evaluate_variant_families(historical, args.output_dir)

    hypotheses = round1 + lit + round3
    pd.DataFrame(
        [
            {
                "round": h.round_no,
                "hypothesis_id": h.hypothesis_id,
                "description": h.description,
                "source": h.source,
                "predicates": ",".join(p.name for p in h.predicates),
            }
            for h in hypotheses
        ]
    ).to_csv(args.output_dir / "hypothesis_manifest.csv", index=False)
    all_results.to_csv(args.output_dir / "hypothesis_results_long.csv", index=False)
    ranked.to_csv(args.output_dir / "hypothesis_ranked.csv", index=False)
    if not websocket.empty:
        websocket.to_csv(args.output_dir / "websocket_selected_candidates_settled.csv", index=False)
    if not historical.empty:
        historical.head(5000).to_csv(args.output_dir / "historical_input_sample.csv", index=False)
    if args.core_model_summary.exists():
        shutil.copy2(args.core_model_summary, args.output_dir / "core_model_summary_source.csv")

    manifest = {
        "historical_rows": int(len(historical)),
        "historical_sources": sorted(historical["source_name"].dropna().unique().tolist()) if not historical.empty else [],
        "forward_rows": int(len(forward)),
        "holdout_rows": int(len(holdout)),
        "websocket_selected_rows": int(len(websocket)),
        "websocket_selected_events": int(websocket["event_ticker"].nunique()) if not websocket.empty else 0,
        "capture_db": str(args.capture_db),
        "hypotheses": len(hypotheses),
        "train_end": str(TRAIN_END),
        "validation_end": str(VALIDATION_END),
        "fee_mode": "Kalshi taker fee at entry; no exit fees for hold-to-settlement filters.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    write_report(args.output_dir, manifest, ranked, all_results, variant_summary)

    print(f"output_dir={args.output_dir}")
    print(f"historical_rows={len(historical):,} websocket_selected_rows={len(websocket):,} hypotheses={len(hypotheses):,}")
    if not ranked.empty:
        cols = [
            "round",
            "hypothesis_id",
            "strategy_variant",
            "train_rows",
            "validation_rows",
            "test_rows",
            "train_pnl",
            "validation_pnl",
            "test_pnl",
            "all_pnl",
            "worst_dd",
            "split_consistent",
        ]
        print("\nTOP HISTORICAL RANKS")
        print(ranked.head(20)[cols].to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    ws_all = all_results[(all_results["dataset"].eq("websocket_selected_signals")) & (all_results["split"].eq("all"))].copy()
    if not ws_all.empty:
        print("\nTOP WEBSOCKET-SELECTED GATES")
        cols = ["round", "hypothesis_id", "rows", "events", "pnl", "return_on_premium", "win_rate", "max_drawdown"]
        cols = ["round", "hypothesis_id", "strategy_variant", "rows", "events", "pnl", "return_on_premium", "win_rate", "max_drawdown"]
        print(ws_all.sort_values(["pnl", "rows"], ascending=[False, False]).head(20)[cols].to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
