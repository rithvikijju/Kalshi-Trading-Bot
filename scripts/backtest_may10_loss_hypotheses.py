#!/usr/bin/env python3
"""Backtest focused guards suggested by the 2026-05-10 late NO loss.

This script evaluates filters only. It does not modify live execution.

Datasets:
  1. Historical late-only baseline features from the May 10 loss-prevention run.
  2. Captured websocket decision holdout from the same run.
  3. Actual live filled trades from the local SQLite trade log, scored with
     Kalshi official results when finalized.

The most important hypotheses are:
  - high-priced favorite requires distance-to-strike >= X short-vol sigma
  - do not chase the same market right after a websocket reprice/filter skip
  - high-priced favorite near expiry is too fragile
  - combinations of those filters
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORICAL = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "historical_baseline_features.csv"
DEFAULT_HOLDOUT = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "holdout_late_only_features.csv"
DEFAULT_HIST_BTC = PROJECT_ROOT / "data" / "research_datamart" / "btc_1m.parquet"
DEFAULT_LIVE_BTC = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_TRADE_DB = Path.home() / ".btc_kalshi_bot" / "research_live_trades.db"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "may10_loss_hypotheses"
MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0
LOCAL_TZ = "America/Denver"


@dataclass(frozen=True)
class Hypothesis:
    name: str
    description: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--historical-btc", type=Path, default=DEFAULT_HIST_BTC)
    parser.add_argument("--live-btc", type=Path, default=DEFAULT_LIVE_BTC)
    parser.add_argument("--trade-db", type=Path, default=DEFAULT_TRADE_DB)
    parser.add_argument("--logs-dir", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--official-cache", type=Path, default=DEFAULT_OUT / "official_results_cache.json")
    return parser.parse_args()


def strike_from_ticker(ticker: str) -> float:
    match = re.search(r"-T(?P<strike>\d+(?:\.\d+)?)", str(ticker).upper())
    if not match:
        return float("nan")
    return float(match.group("strike")) + 0.01


def utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def kalshi_fee(entry: float, contracts: int = 1) -> float:
    # The deployed scripts use Kalshi's rounded taker estimate. For the entry
    # range we trade, this has matched observed 1-contract fees at 2 cents.
    if not math.isfinite(entry) or contracts <= 0:
        return 0.0
    return round(contracts * 0.02, 2)


def max_drawdown(pnl: pd.Series) -> float:
    vals = pd.to_numeric(pnl, errors="coerce").dropna()
    if vals.empty:
        return 0.0
    equity = vals.cumsum()
    return float((equity - equity.cummax()).min())


def load_btc(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if "time" not in df.columns and "available_at" in df.columns:
        df = df.rename(columns={"available_at": "time"})
    if "bucket_start" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"bucket_start": "time"})
    df["time"] = utc(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    if "log_ret" not in df.columns:
        df["log_ret"] = np.log(pd.to_numeric(df["close"], errors="coerce") / pd.to_numeric(df["close"], errors="coerce").shift(1))
    for window in (5, 10, 15, 30, 60):
        col = f"rv_{window}m"
        if col not in df.columns:
            df[col] = df["log_ret"].rolling(window).std() * math.sqrt(MINUTES_PER_YEAR)
    if "rv_1d" not in df.columns:
        df["rv_1d"] = df["log_ret"].rolling(1440).std() * math.sqrt(MINUTES_PER_YEAR)
    return df


def add_btc_features(df: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty or btc.empty:
        for col in ["rv_15m", "rv_60m", "rv_1d", "exp_move_rv15", "exp_move_rv60", "dist_sigma_rv15", "dist_sigma_rv60"]:
            out[col] = np.nan
        return out
    times = btc["time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    lookup = out["entry_time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    idx = np.searchsorted(times, lookup, side="right") - 1
    valid = (idx >= 0) & (idx < len(btc))
    for col in ["close", "rv_15m", "rv_60m", "rv_1d"]:
        vals = np.full(len(out), np.nan, dtype=float)
        arr = pd.to_numeric(btc[col], errors="coerce").to_numpy(dtype=float)
        vals[valid] = arr[idx[valid]]
        out[f"btc_{col}" if col == "close" else col] = vals
    if out["entry_spot"].isna().any():
        out.loc[out["entry_spot"].isna(), "entry_spot"] = out.loc[out["entry_spot"].isna(), "btc_close"]
    ttl_years = out["ttl_min"].clip(lower=0.01) / MINUTES_PER_YEAR
    for label, rv_col in [("rv15", "rv_15m"), ("rv60", "rv_60m"), ("rv1d", "rv_1d")]:
        move_col = f"exp_move_{label}"
        sigma_col = f"dist_sigma_{label}"
        out[move_col] = out["entry_spot"] * out[rv_col] * np.sqrt(ttl_years)
        out[sigma_col] = out["side_distance_usd"] / out[move_col].replace(0.0, np.nan)
    return out


def normalize_trade_frame(df: pd.DataFrame, source: str, btc: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dataset_source"] = source
    if "entry_time" not in out.columns and "received_at_utc" in out.columns:
        out["entry_time"] = out["received_at_utc"]
    if "entry_time" not in out.columns and "created_at" in out.columns:
        out["entry_time"] = out["created_at"]
    out["entry_time"] = utc(out["entry_time"])
    out["close_time"] = utc(out["close_time"])
    out["ttl_min"] = pd.to_numeric(out.get("ttl_min"), errors="coerce")
    missing_ttl = out["ttl_min"].isna()
    out.loc[missing_ttl, "ttl_min"] = (out.loc[missing_ttl, "close_time"] - out.loc[missing_ttl, "entry_time"]).dt.total_seconds() / 60.0
    out["market_ticker"] = out["market_ticker"].astype(str)
    out["event_ticker"] = out["event_ticker"].astype(str)
    out["side"] = out["side"].astype(str).str.lower()
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    if "entry_fee" not in out.columns:
        fee_source = "actual_fee_paid" if "actual_fee_paid" in out.columns else "entry_fee_estimate"
        out["entry_fee"] = pd.to_numeric(out.get(fee_source, np.nan), errors="coerce")
    out["contracts"] = pd.to_numeric(out.get("contracts", 1), errors="coerce").fillna(1).astype(int)
    out["entry_fee"] = pd.to_numeric(out["entry_fee"], errors="coerce")
    fee_missing = out["entry_fee"].isna()
    out.loc[fee_missing, "entry_fee"] = [kalshi_fee(e, c) for e, c in zip(out.loc[fee_missing, "entry_price"], out.loc[fee_missing, "contracts"])]
    if "strike" not in out.columns:
        out["strike"] = out["market_ticker"].map(strike_from_ticker)
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    if "entry_spot" not in out.columns:
        out["entry_spot"] = pd.to_numeric(out.get("btc_spot", np.nan), errors="coerce")
    else:
        out["entry_spot"] = pd.to_numeric(out["entry_spot"], errors="coerce")
    if "model_p_yes" not in out.columns:
        out["model_p_yes"] = np.nan
    out["model_p_yes"] = pd.to_numeric(out["model_p_yes"], errors="coerce")
    out["side_probability"] = np.where(out["side"].eq("yes"), out["model_p_yes"], 1.0 - out["model_p_yes"])
    out["side_distance_usd"] = np.where(out["side"].eq("yes"), out["entry_spot"] - out["strike"], out["strike"] - out["entry_spot"])
    out["abs_distance_usd"] = (out["entry_spot"] - out["strike"]).abs()
    out["is_favorite"] = out["entry_price"] >= 0.65
    out["is_high_favorite"] = out["entry_price"] >= 0.68
    if "premium" not in out.columns:
        out["premium"] = out["entry_price"] * out["contracts"] + out["entry_fee"]
    out["premium"] = pd.to_numeric(out["premium"], errors="coerce")
    if "pnl" not in out.columns:
        out["pnl"] = np.nan
    out["pnl"] = pd.to_numeric(out["pnl"], errors="coerce")
    if "official_result" in out.columns:
        result = out["official_result"].astype(str).str.lower()
        missing_pnl = out["pnl"].isna() & result.isin(["yes", "no"])
        won = out["side"].eq(result)
        out.loc[missing_pnl, "pnl"] = np.where(
            won[missing_pnl],
            out.loc[missing_pnl, "contracts"] * (1.0 - out.loc[missing_pnl, "entry_price"]) - out.loc[missing_pnl, "entry_fee"],
            -out.loc[missing_pnl, "contracts"] * out.loc[missing_pnl, "entry_price"] - out.loc[missing_pnl, "entry_fee"],
        )
    return add_btc_features(out, btc)


def load_historical(path: Path, btc: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(path)
    return normalize_trade_frame(df, "historical_late_only", btc)


def load_holdout(path: Path, btc: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "pnl" not in df.columns and "pnl_1c" in df.columns:
        df["pnl"] = df["pnl_1c"]
    if "premium" not in df.columns and "premium_1c" in df.columns:
        df["premium"] = df["premium_1c"]
    return normalize_trade_frame(df, "captured_decision_holdout", btc)


def fetch_official_result(ticker: str, cache: dict[str, dict]) -> dict:
    if ticker in cache:
        return cache[ticker]
    url = f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        market = response.json().get("market", {})
        result = {
            "status": market.get("status"),
            "result": market.get("result"),
            "expiration_value": market.get("expiration_value"),
            "settlement_ts": market.get("settlement_ts"),
        }
    except Exception as exc:
        result = {"status": "error", "error": repr(exc)}
    cache[ticker] = result
    return result


def load_live_trades(trade_db: Path, btc: pd.DataFrame, cache_path: Path) -> pd.DataFrame:
    if not trade_db.exists():
        return pd.DataFrame()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    con = sqlite3.connect(trade_db)
    df = pd.read_sql_query(
        """
        SELECT id, created_at, mode, status, event_ticker, market_ticker, side,
               contracts, entry_price, actual_fee_paid, entry_fee_estimate,
               model_p_yes, net_edge_cents, spread_cents, btc_spot, close_time,
               client_order_id, order_id
        FROM research_live_trades
        WHERE status IN ('filled', 'paper_filled')
        ORDER BY created_at
        """,
        con,
    )
    con.close()
    if df.empty:
        return df
    results = [fetch_official_result(t, cache) for t in df["market_ticker"]]
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    df["official_status"] = [r.get("status") for r in results]
    df["official_result"] = [r.get("result") for r in results]
    df["official_expiration_value"] = [r.get("expiration_value") for r in results]
    df = df[df["official_result"].isin(["yes", "no"])].copy()
    df["entry_time"] = df["created_at"]
    out = normalize_trade_frame(df, "actual_live_filled", btc)
    process_start = pd.Timestamp("2026-05-10T22:20:40Z")
    out["since_current_process"] = out["entry_time"] >= process_start
    out["recent_trade_id"] = pd.to_numeric(out.get("id"), errors="coerce")
    return out


SKIP_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*skip(?: strategy=[a-zA-Z0-9_]+)? (?P<market>KXBTCD-[A-Z0-9]+-T\d+(?:\.\d+)?): failed .*reprice/filter",
    re.IGNORECASE,
)


def load_reprice_skips(logs_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    if not logs_dir.exists():
        return pd.DataFrame(columns=["skip_time", "market_ticker", "event_ticker"])
    for path in logs_dir.glob("*.out.log"):
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = SKIP_RE.search(line)
                if not match:
                    continue
                local = pd.Timestamp(match.group("ts")).tz_localize(LOCAL_TZ)
                market = match.group("market")
                rows.append(
                    {
                        "skip_time": local.tz_convert("UTC"),
                        "market_ticker": market,
                        "event_ticker": market.rsplit("-", 1)[0],
                        "source_log": path.name,
                    }
                )
        except OSError:
            continue
    if not rows:
        return pd.DataFrame(columns=["skip_time", "market_ticker", "event_ticker"])
    return pd.DataFrame(rows).drop_duplicates().sort_values("skip_time").reset_index(drop=True)


def attach_reprice_features(trades: pd.DataFrame, skips: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    for scope in ["market", "event"]:
        for minutes in [2, 3, 5, 10]:
            out[f"recent_reprice_skip_same_{scope}_{minutes}m"] = False
            out[f"seconds_since_reprice_skip_same_{scope}"] = np.nan
    if out.empty or skips.empty:
        return out
    for idx, row in out.iterrows():
        ts = row["entry_time"]
        for scope, key in [("market", "market_ticker"), ("event", "event_ticker")]:
            ss = skips[(skips[key].eq(row[key])) & (skips["skip_time"] < ts)].copy()
            if ss.empty:
                continue
            delta = (ts - ss["skip_time"]).dt.total_seconds()
            min_delta = float(delta.min())
            out.at[idx, f"seconds_since_reprice_skip_same_{scope}"] = min_delta
            for minutes in [2, 3, 5, 10]:
                out.at[idx, f"recent_reprice_skip_same_{scope}_{minutes}m"] = min_delta <= minutes * 60
    return out


def make_hypotheses() -> list[Hypothesis]:
    hyps: list[Hypothesis] = []
    add = hyps.append
    add(Hypothesis("baseline_all", "No extra guard", lambda d: pd.Series(True, index=d.index)))
    for sigma in [0.50, 0.75, 1.00, 1.25, 1.50]:
        add(Hypothesis(
            f"fav_ge65_dist_rv15_ge_{str(sigma).replace('.', 'p')}",
            f"For entry >=65c, require distance >= {sigma:.2f}x rv15 expected move",
            lambda d, sigma=sigma: ~(d["entry_price"].ge(0.65) & d["dist_sigma_rv15"].lt(sigma)),
        ))
        add(Hypothesis(
            f"fav_ge65_dist_rv60_ge_{str(sigma).replace('.', 'p')}",
            f"For entry >=65c, require distance >= {sigma:.2f}x rv60 expected move",
            lambda d, sigma=sigma: ~(d["entry_price"].ge(0.65) & d["dist_sigma_rv60"].lt(sigma)),
        ))
    for dist in [75, 100, 125, 150, 200]:
        add(Hypothesis(
            f"fav_ge65_side_dist_ge_{dist}",
            f"For entry >=65c, require side distance >= ${dist}",
            lambda d, dist=dist: ~(d["entry_price"].ge(0.65) & d["side_distance_usd"].lt(dist)),
        ))
    for entry in [0.65, 0.68, 0.70]:
        add(Hypothesis(
            f"entry_lt_{int(entry * 100)}",
            f"Skip all entries >= {entry:.2f}",
            lambda d, entry=entry: d["entry_price"].lt(entry),
        ))
    for ttl in [6.0, 7.5, 10.0]:
        add(Hypothesis(
            f"fav_ge65_ttl_ge_{str(ttl).replace('.', 'p')}",
            f"For entry >=65c, require TTL >= {ttl:.1f}m",
            lambda d, ttl=ttl: ~(d["entry_price"].ge(0.65) & d["ttl_min"].lt(ttl)),
        ))
    for minutes in [2, 3, 5, 10]:
        add(Hypothesis(
            f"no_recent_same_market_reprice_{minutes}m",
            f"Skip if same market had failed websocket reprice/filter in prior {minutes}m",
            lambda d, minutes=minutes: ~d[f"recent_reprice_skip_same_market_{minutes}m"].fillna(False),
        ))
        add(Hypothesis(
            f"no_recent_same_event_reprice_{minutes}m",
            f"Skip if same event had failed websocket reprice/filter in prior {minutes}m",
            lambda d, minutes=minutes: ~d[f"recent_reprice_skip_same_event_{minutes}m"].fillna(False),
        ))
    # Focused rules that would have blocked the May 10 T81399.99 NO loss.
    add(Hypothesis(
        "may10_fav_dist_rv15_and_market_reprice",
        "Entry >=65c needs rv15 distance >=1.0x and no same-market reprice skip in prior 3m",
        lambda d: ~(d["entry_price"].ge(0.65) & d["dist_sigma_rv15"].lt(1.0)) & ~d["recent_reprice_skip_same_market_3m"].fillna(False),
    ))
    add(Hypothesis(
        "may10_fav_dist_rv60_and_market_reprice",
        "Entry >=65c needs rv60 distance >=1.0x and no same-market reprice skip in prior 3m",
        lambda d: ~(d["entry_price"].ge(0.65) & d["dist_sigma_rv60"].lt(1.0)) & ~d["recent_reprice_skip_same_market_3m"].fillna(False),
    ))
    add(Hypothesis(
        "may10_fav_dist_rv15_or_abs125_and_market_reprice",
        "Entry >=65c requires rv15 distance >=1.0x or $125 buffer, plus no same-market reprice skip in prior 3m",
        lambda d: ~(
            d["entry_price"].ge(0.65)
            & d["dist_sigma_rv15"].lt(1.0)
            & d["side_distance_usd"].lt(125)
        )
        & ~d["recent_reprice_skip_same_market_3m"].fillna(False),
    ))
    add(Hypothesis(
        "may10_fav_dist_rv15_and_ttl7p5",
        "Entry >=65c requires rv15 distance >=1.0x and TTL >=7.5m",
        lambda d: ~(
            d["entry_price"].ge(0.65)
            & (d["dist_sigma_rv15"].lt(1.0) | d["ttl_min"].lt(7.5))
        ),
    ))
    add(Hypothesis(
        "may10_fav_full_guard",
        "Entry >=65c requires rv15 distance >=1.0x, TTL >=7.5m, and no same-market reprice skip in prior 3m",
        lambda d: ~(
            d["entry_price"].ge(0.65)
            & (d["dist_sigma_rv15"].lt(1.0) | d["ttl_min"].lt(7.5))
        )
        & ~d["recent_reprice_skip_same_market_3m"].fillna(False),
    ))
    add(Hypothesis(
        "may10_fav_no_weak_distance",
        "Skip high favorites when both rv15 distance <1.0x and side buffer <$125",
        lambda d: ~(d["entry_price"].ge(0.65) & d["dist_sigma_rv15"].lt(1.0) & d["side_distance_usd"].lt(125)),
    ))
    return hyps


def summarize_selected(df: pd.DataFrame, selected: pd.DataFrame, name: str, desc: str) -> dict:
    part = selected.dropna(subset=["pnl"]).sort_values(["close_time", "entry_time"])
    base = df.dropna(subset=["pnl"]).sort_values(["close_time", "entry_time"])
    pnl = float(part["pnl"].sum()) if not part.empty else 0.0
    premium = float(part["premium"].sum()) if not part.empty else 0.0
    base_pnl = float(base["pnl"].sum()) if not base.empty else 0.0
    return {
        "dataset": str(df["dataset"].iloc[0]) if "dataset" in df.columns and len(df) else "unknown",
        "hypothesis": name,
        "description": desc,
        "trades": int(len(part)),
        "removed": int(len(base) - len(part)),
        "pnl": pnl,
        "pnl_delta": pnl - base_pnl,
        "premium": premium,
        "return_on_premium": pnl / premium if premium else 0.0,
        "return_on_100": pnl / 100.0,
        "win_rate": float((part["pnl"] > 0).mean()) if not part.empty else 0.0,
        "max_drawdown": max_drawdown(part["pnl"]) if not part.empty else 0.0,
        "yes_trades": int(part["side"].eq("yes").sum()) if not part.empty else 0,
        "no_trades": int(part["side"].eq("no").sum()) if not part.empty else 0,
        "removed_losers": int((base.loc[~base.index.isin(part.index), "pnl"] < 0).sum()) if not base.empty else 0,
        "removed_winners": int((base.loc[~base.index.isin(part.index), "pnl"] > 0).sum()) if not base.empty else 0,
    }


def evaluate_dataset(df: pd.DataFrame, dataset: str, hyps: list[Hypothesis]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["dataset"] = dataset
    rows = []
    for hyp in hyps:
        mask = hyp.predicate(work).reindex(work.index).fillna(False).astype(bool)
        rows.append(summarize_selected(work, work[mask].copy(), hyp.name, hyp.description))
    return pd.DataFrame(rows)


def write_filtered_trades(df: pd.DataFrame, hyps: list[Hypothesis], out_dir: Path, label: str) -> None:
    if df.empty:
        return
    for hyp in hyps:
        if hyp.name not in {
            "baseline_all",
            "may10_fav_dist_rv15_and_market_reprice",
            "may10_fav_dist_rv15_or_abs125_and_market_reprice",
            "may10_fav_full_guard",
            "fav_ge65_dist_rv15_ge_1p0",
            "no_recent_same_market_reprice_3m",
        }:
            continue
        mask = hyp.predicate(df).reindex(df.index).fillna(False).astype(bool)
        df.loc[mask].to_csv(out_dir / f"{label}_{hyp.name}_trades.csv", index=False)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    hist_btc = load_btc(args.historical_btc)
    live_btc = load_btc(args.live_btc)
    skips = load_reprice_skips(args.logs_dir)

    datasets: list[tuple[str, pd.DataFrame]] = []
    if args.historical.exists():
        datasets.append(("historical_late_only", attach_reprice_features(load_historical(args.historical, hist_btc), skips)))
    if args.holdout.exists():
        datasets.append(("captured_decision_holdout", attach_reprice_features(load_holdout(args.holdout, live_btc), skips)))
    live = load_live_trades(args.trade_db, live_btc, args.official_cache)
    if not live.empty:
        live = attach_reprice_features(live, skips)
        datasets.append(("actual_live_all_finalized", live))
        recent = live[live["entry_time"] >= pd.Timestamp("2026-05-10T00:00:00Z")].copy()
        if not recent.empty:
            datasets.append(("actual_live_may10_plus", recent))
        current = live[live.get("since_current_process", False).eq(True)].copy()
        if not current.empty:
            datasets.append(("actual_live_since_current_process", current))

    hyps = make_hypotheses()
    frames = [evaluate_dataset(df, label, hyps) for label, df in datasets]
    results = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
    results.to_csv(args.output_dir / "may10_loss_hypothesis_results.csv", index=False)
    skips.to_csv(args.output_dir / "parsed_reprice_skips.csv", index=False)
    for label, df in datasets:
        df.to_csv(args.output_dir / f"{label}_features.csv", index=False)
        write_filtered_trades(df, hyps, args.output_dir, label)

    # Ranking: require no worse than baseline on historical and captured holdout
    # where available, then show live impact. This ranking is descriptive only.
    pivot = results.pivot_table(index=["hypothesis", "description"], columns="dataset", values=["trades", "pnl", "pnl_delta", "win_rate", "max_drawdown", "removed_losers", "removed_winners"], aggfunc="first")
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns]
    pivot = pivot.reset_index()
    score = pd.Series(0.0, index=pivot.index)
    for col in pivot.columns:
        if col.startswith("pnl_delta_historical"):
            score += pivot[col].fillna(0.0)
        if col.startswith("pnl_delta_captured"):
            score += 2.0 * pivot[col].fillna(0.0)
        if col.startswith("pnl_delta_actual_live_may10"):
            score += 1.5 * pivot[col].fillna(0.0)
    pivot["score"] = score
    ranked = pivot.sort_values(["score"], ascending=False)
    ranked.to_csv(args.output_dir / "may10_loss_hypothesis_ranked.csv", index=False)

    print("DATASETS")
    for label, df in datasets:
        print(f"{label}: rows={len(df)} pnl={df['pnl'].sum():.2f} win={(df['pnl'] > 0).mean():.1%}")
    print("\nTOP RANKED")
    cols = ["hypothesis", "score"]
    for c in [
        "trades_historical_late_only", "pnl_historical_late_only", "pnl_delta_historical_late_only",
        "trades_captured_decision_holdout", "pnl_captured_decision_holdout", "pnl_delta_captured_decision_holdout",
        "trades_actual_live_may10_plus", "pnl_actual_live_may10_plus", "pnl_delta_actual_live_may10_plus",
        "removed_losers_actual_live_may10_plus", "removed_winners_actual_live_may10_plus",
    ]:
        if c in ranked.columns:
            cols.append(c)
    print(ranked[cols].head(20).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
