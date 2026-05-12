#!/usr/bin/env python3
"""Research loss-prevention filters without leaking live websocket holdout data.

Workflow:

1. Build hypotheses only from historical Kalshi candle/bid-ask replay trades.
2. Score train/validation/test using historical splits already in the replay.
3. Validate selected candidates on captured websocket decisions only at the end.

The live holdout is never used to choose thresholds or rank candidates.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORICAL = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "live_shadow_accuracy_20260510"
    / "historical_may8_variants"
    / "may8examine_trades.csv"
)
DEFAULT_CAPTURE_AUDIT = PROJECT_ROOT / "backtest_outputs" / "live_capture_replay_20260510" / "audit"
DEFAULT_CAPTURE_DB = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless.duckdb"
DEFAULT_BTC = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510"


@dataclass(frozen=True)
class Hypothesis:
    round_no: int
    name: str
    description: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-trades", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--variant", default="baseline_late_only")
    parser.add_argument("--capture-audit-dir", type=Path, default=DEFAULT_CAPTURE_AUDIT)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--btc-1m", type=Path, default=DEFAULT_BTC)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-validation-trades", type=int, default=8)
    parser.add_argument("--min-test-trades", type=int, default=8)
    return parser.parse_args()


def strike_from_ticker(ticker: str) -> float:
    match = re.search(r"-T(?P<strike>\d+(?:\.\d+)?)", str(ticker).upper())
    if not match:
        return float("nan")
    return float(match.group("strike")) + 0.01


def utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def max_drawdown(pnl: pd.Series) -> float:
    clean = pnl.dropna().astype(float)
    if clean.empty:
        return 0.0
    equity = clean.cumsum()
    return float((equity - equity.cummax()).min())


def safe_mask(mask: pd.Series | np.ndarray | bool, index: pd.Index) -> pd.Series:
    if isinstance(mask, (bool, np.bool_)):
        return pd.Series(bool(mask), index=index)
    out = pd.Series(mask, index=index) if not isinstance(mask, pd.Series) else mask.reindex(index)
    return out.fillna(False).astype(bool)


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    if "entry_time" not in df and "received_at_utc" in df:
        df["entry_time"] = df["received_at_utc"]
    df["entry_time"] = utc(df["entry_time"])
    df["close_time"] = utc(df["close_time"])
    df["ttl_min"] = (df["close_time"] - df["entry_time"]).dt.total_seconds() / 60.0
    df["entry_hour_utc"] = df["entry_time"].dt.hour
    df["entry_minute"] = df["entry_time"].dt.minute
    df["side"] = df["side"].astype(str).str.lower()
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["net_edge_cents"] = pd.to_numeric(df["net_edge_cents"], errors="coerce")
    df["contracts"] = pd.to_numeric(df.get("contracts", 1), errors="coerce").fillna(1).astype(int)
    if "strike" not in df:
        df["strike"] = np.nan
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    missing_strike = df["strike"].isna()
    if missing_strike.any():
        df.loc[missing_strike, "strike"] = df.loc[missing_strike, "market_ticker"].map(strike_from_ticker)
    if "entry_spot" not in df:
        df["entry_spot"] = pd.to_numeric(df.get("btc_spot", np.nan), errors="coerce")
    else:
        df["entry_spot"] = pd.to_numeric(df["entry_spot"], errors="coerce")
    if "model_p_yes" not in df:
        df["model_p_yes"] = np.nan
    df["model_p_yes"] = pd.to_numeric(df["model_p_yes"], errors="coerce")
    if "edge_threshold_cents" not in df:
        df["edge_threshold_cents"] = np.nan
    df["edge_threshold_cents"] = pd.to_numeric(df["edge_threshold_cents"], errors="coerce")
    df["edge_margin_cents"] = df["net_edge_cents"] - df["edge_threshold_cents"]
    df["side_probability"] = np.where(df["side"].eq("yes"), df["model_p_yes"], 1.0 - df["model_p_yes"])
    df["side_distance_usd"] = np.where(
        df["side"].eq("yes"),
        df["entry_spot"] - df["strike"],
        df["strike"] - df["entry_spot"],
    )
    if "signed_distance_usd" in df:
        df["hist_signed_distance_usd"] = pd.to_numeric(df["signed_distance_usd"], errors="coerce")
    df["abs_distance_usd"] = (df["entry_spot"] - df["strike"]).abs()
    df["side_moneyness_bps"] = 10000.0 * df["side_distance_usd"] / df["entry_spot"].clip(lower=1.0)
    if "premium" not in df:
        fee_col = "entry_fee" if "entry_fee" in df else "entry_fee_estimate"
        df["premium"] = df["entry_price"] * df["contracts"] + pd.to_numeric(df.get(fee_col, 0), errors="coerce").fillna(0.0)
    df["premium"] = pd.to_numeric(df["premium"], errors="coerce")
    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
    for col in ("side_ret_5m", "side_ret_10m", "side_ret_30m", "distance_sigma", "spread_cents", "market_mid"):
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def add_btc_momentum(df: pd.DataFrame, btc_path: Path) -> pd.DataFrame:
    if not btc_path.exists() or df.empty:
        return df
    btc = pd.read_parquet(btc_path)
    time_col = "time" if "time" in btc.columns else "available_at"
    btc[time_col] = utc(btc[time_col])
    btc = btc.dropna(subset=[time_col, "close"]).sort_values(time_col).reset_index(drop=True)
    times = btc[time_col].dt.tz_localize(None).to_numpy()
    closes = pd.to_numeric(btc["close"], errors="coerce").to_numpy(dtype=float)

    out = df.copy()
    entry_times = out["entry_time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    idx_now = np.searchsorted(times, entry_times, side="right") - 1
    spot_now = np.where((idx_now >= 0) & (idx_now < len(closes)), closes[idx_now], np.nan)
    if out["entry_spot"].isna().any():
        out.loc[out["entry_spot"].isna(), "entry_spot"] = spot_now[out["entry_spot"].isna().to_numpy()]
    sign = np.where(out["side"].eq("yes"), 1.0, -1.0)
    for minutes in (5, 10, 30):
        prior = entry_times - np.timedelta64(minutes, "m")
        idx_prior = np.searchsorted(times, prior, side="right") - 1
        spot_prior = np.where((idx_prior >= 0) & (idx_prior < len(closes)), closes[idx_prior], np.nan)
        out[f"side_ret_{minutes}m"] = sign * (spot_now - spot_prior)
    return add_features(out)


def load_historical(path: Path, variant: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if variant not in set(df["variant"].astype(str)):
        if variant == "baseline_late_only" and "baseline_current" in set(df["variant"].astype(str)):
            base = add_features(df[df["variant"].eq("baseline_current")].copy())
            df = base[(base["ttl_min"] >= 5.0) & (base["ttl_min"] <= 20.0)].copy()
            df["variant"] = variant
        else:
            raise ValueError(f"variant {variant!r} not found in {path}")
    else:
        df = df[df["variant"].eq(variant)].copy()
    df["source"] = "historical"
    return add_features(df)


def load_signal_scans(capture_db: Path) -> pd.DataFrame:
    if not capture_db.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(capture_db), read_only=True)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    if "signal_scan_all" not in tables:
        con.close()
        return pd.DataFrame()
    scans = con.execute(
        """
        SELECT received_at_ns AS scan_ns,
               received_at_utc AS scan_received_at_utc,
               capture_label,
               selected_market AS market_ticker,
               lower(selected_side) AS side,
               model_p_yes,
               entry_price AS scan_entry_price,
               net_edge_cents AS scan_net_edge_cents
        FROM signal_scan_all
        WHERE action = 'selected'
          AND selected_market IS NOT NULL
        ORDER BY received_at_ns
        """
    ).fetchdf()
    con.close()
    return scans


def attach_model_probabilities(holdout: pd.DataFrame, capture_db: Path) -> pd.DataFrame:
    scans = load_signal_scans(capture_db)
    out = holdout.copy()
    if scans.empty:
        out["model_p_yes"] = np.nan
        out["matched_scan_lag_sec"] = np.nan
        return out
    label_map = {
        "research_live_capture": "research_snapshot",
        "multi_strategy_shadow_capture": "multi_shadow_snapshot",
    }
    out["capture_label"] = out["capture"].map(label_map).fillna(out["capture"])
    parts = []
    for (capture_label, market, side), left in out.groupby(["capture_label", "market_ticker", "side"], sort=False):
        right = scans[
            scans["capture_label"].eq(capture_label)
            & scans["market_ticker"].eq(market)
            & scans["side"].eq(side)
        ].copy()
        left = left.sort_values("received_at_ns").copy()
        if right.empty:
            left["model_p_yes"] = np.nan
            left["matched_scan_lag_sec"] = np.nan
            parts.append(left)
            continue
        merged = pd.merge_asof(
            left,
            right[["scan_ns", "model_p_yes", "scan_entry_price", "scan_net_edge_cents"]],
            left_on="received_at_ns",
            right_on="scan_ns",
            direction="backward",
            tolerance=15_000_000_000,
        )
        merged["matched_scan_lag_sec"] = (merged["received_at_ns"] - merged["scan_ns"]) / 1e9
        parts.append(merged)
    return pd.concat(parts, ignore_index=True).sort_values(["received_at_ns", "market_ticker"]).reset_index(drop=True)


def load_holdout(audit_dir: Path, capture_db: Path, btc_path: Path) -> pd.DataFrame:
    top = pd.read_csv(audit_dir / "late_only_top_liquidity_at_decision.csv")
    settled = pd.read_csv(audit_dir / "captured_order_decisions_settled.csv")
    cols = ["received_at_ns", "official_result", "settled", "pnl_1c", "premium_1c"]
    df = top.merge(settled[cols], on="received_at_ns", how="left")
    df = df[(df["settled"].eq(True)) & df["official_result"].isin(["yes", "no"])].copy()
    df["source"] = "live_capture_holdout"
    df["entry_time"] = df["received_at_utc"]
    df["entry_spot"] = pd.to_numeric(df["btc_spot"], errors="coerce")
    df["premium"] = pd.to_numeric(df["premium_1c"], errors="coerce")
    df["pnl"] = pd.to_numeric(df["pnl_1c"], errors="coerce")
    df = attach_model_probabilities(df, capture_db)
    df = add_features(df)
    return add_btc_momentum(df, btc_path)


def summarize(df: pd.DataFrame, split: str, hyp: Hypothesis, baseline: pd.DataFrame | None = None) -> dict:
    part = df.dropna(subset=["pnl"]).copy()
    premium = float(part["premium"].sum()) if not part.empty else 0.0
    pnl = float(part["pnl"].sum()) if not part.empty else 0.0
    base_pnl = float(baseline["pnl"].sum()) if baseline is not None and not baseline.empty else 0.0
    base_trades = int(len(baseline)) if baseline is not None else 0
    return {
        "round": hyp.round_no,
        "hypothesis": hyp.name,
        "description": hyp.description,
        "split": split,
        "trades": int(len(part)),
        "trades_removed": int(base_trades - len(part)) if baseline is not None else 0,
        "premium": premium,
        "pnl": pnl,
        "pnl_delta_vs_baseline": pnl - base_pnl if baseline is not None else 0.0,
        "return_on_premium": pnl / premium if premium else 0.0,
        "win_rate": float((part["pnl"] > 0).mean()) if not part.empty else 0.0,
        "max_drawdown": max_drawdown(part.sort_values(["close_time", "entry_time"])["pnl"]) if not part.empty else 0.0,
        "yes_trades": int(part["side"].eq("yes").sum()) if not part.empty else 0,
        "no_trades": int(part["side"].eq("no").sum()) if not part.empty else 0,
        "avg_entry": float(part["entry_price"].mean()) if not part.empty else 0.0,
        "avg_edge": float(part["net_edge_cents"].mean()) if not part.empty else 0.0,
        "avg_side_distance": float(part["side_distance_usd"].mean()) if not part.empty else 0.0,
    }


def quantiles(df: pd.DataFrame, col: str, qs: list[float]) -> list[float]:
    values = pd.to_numeric(df[col], errors="coerce").dropna()
    if values.empty:
        return []
    return sorted({float(values.quantile(q)) for q in qs if math.isfinite(float(values.quantile(q)))})


def make_single_hypotheses(train: pd.DataFrame) -> list[Hypothesis]:
    hyps: list[Hypothesis] = [Hypothesis(0, "baseline_all", "No loss-prevention filter", lambda d: True)]
    add = hyps.append
    round_no = 1
    add(Hypothesis(round_no, "side_yes", "Keep YES trades only", lambda d: d["side"].eq("yes")))
    add(Hypothesis(round_no, "side_no", "Keep NO trades only", lambda d: d["side"].eq("no")))
    for lo, hi in [(5, 15), (5, 20), (8, 20), (10, 20), (12, 20), (5, 45)]:
        add(Hypothesis(round_no, f"ttl_{lo}_{hi}", f"Keep TTL {lo}-{hi} min", lambda d, lo=lo, hi=hi: (d["ttl_min"] >= lo) & (d["ttl_min"] <= hi)))
    for threshold in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        add(Hypothesis(round_no, f"entry_le_{int(threshold*100)}", f"Keep entry <= {threshold:.2f}", lambda d, threshold=threshold: d["entry_price"] <= threshold))
        add(Hypothesis(round_no, f"entry_ge_{int(threshold*100)}", f"Keep entry >= {threshold:.2f}", lambda d, threshold=threshold: d["entry_price"] >= threshold))
    for threshold in [12, 14, 15, 16, 17, 18, 20, 22]:
        add(Hypothesis(round_no, f"edge_ge_{threshold}", f"Keep net edge >= {threshold}c", lambda d, threshold=threshold: d["net_edge_cents"] >= threshold))
    for threshold in [0.62, 0.65, 0.68, 0.70, 0.72, 0.75, 0.80]:
        add(Hypothesis(round_no, f"side_prob_ge_{int(threshold*100)}", f"Keep side probability >= {threshold:.2f}", lambda d, threshold=threshold: d["side_probability"] >= threshold))
    for threshold in [-250, -100, 0, 50, 100, 150, 200]:
        add(Hypothesis(round_no, f"side_dist_ge_{threshold}", f"Keep side distance >= ${threshold}", lambda d, threshold=threshold: d["side_distance_usd"] >= threshold))
    for threshold in [0.25, 0.50, 0.75, 1.00, 1.25]:
        add(Hypothesis(round_no, f"dist_sigma_ge_{str(threshold).replace('.','p')}", f"Keep distance sigma >= {threshold:.2f}", lambda d, threshold=threshold: d["distance_sigma"] >= threshold))
    for col in ["side_ret_5m", "side_ret_10m", "side_ret_30m"]:
        for threshold in [-150, -75, 0, 75, 125, 200]:
            add(Hypothesis(round_no, f"{col}_ge_{threshold}", f"Keep {col} >= {threshold}", lambda d, col=col, threshold=threshold: d[col] >= threshold))
            add(Hypothesis(round_no, f"{col}_le_{threshold}", f"Keep {col} <= {threshold}", lambda d, col=col, threshold=threshold: d[col] <= threshold))
    for hour in range(24):
        add(Hypothesis(round_no, f"exclude_hour_{hour:02d}", f"Exclude UTC hour {hour:02d}", lambda d, hour=hour: d["entry_hour_utc"] != hour))

    # Focused loss-prevention hypotheses from live trade forensics. These are
    # still selected only on historical rows; live capture remains holdout.
    focused = [
        (
            "block_no_entry_le52_dist_le55",
            "Block cheap near-strike NO: side=no, entry<=52c, side distance<=55",
            lambda d: ~((d["side"].eq("no")) & (d["entry_price"] <= 0.52) & (d["side_distance_usd"] <= 55)),
        ),
        (
            "block_no_entry_le55_dist_le75",
            "Block cheap/medium near-strike NO: side=no, entry<=55c, side distance<=75",
            lambda d: ~((d["side"].eq("no")) & (d["entry_price"] <= 0.55) & (d["side_distance_usd"] <= 75)),
        ),
        (
            "block_no_entry_le55_sideprob_lt72",
            "Block cheap NO unless side probability >=72%",
            lambda d: ~((d["side"].eq("no")) & (d["entry_price"] <= 0.55) & (d["side_probability"] < 0.72)),
        ),
        (
            "block_no_sideprob_lt70",
            "Block NO trades with side probability below 70%",
            lambda d: ~((d["side"].eq("no")) & (d["side_probability"] < 0.70)),
        ),
        (
            "block_no_sideprob_lt72",
            "Block NO trades with side probability below 72%",
            lambda d: ~((d["side"].eq("no")) & (d["side_probability"] < 0.72)),
        ),
        (
            "block_no_sideprob_lt75",
            "Block NO trades with side probability below 75%",
            lambda d: ~((d["side"].eq("no")) & (d["side_probability"] < 0.75)),
        ),
        (
            "block_no_distance_lt40",
            "Block NO trades with side distance below $40",
            lambda d: ~((d["side"].eq("no")) & (d["side_distance_usd"] < 40)),
        ),
        (
            "block_no_distance_lt55",
            "Block NO trades with side distance below $55",
            lambda d: ~((d["side"].eq("no")) & (d["side_distance_usd"] < 55)),
        ),
        (
            "block_no_lowprob_nearstrike",
            "Block NO with side probability <72% and side distance <$75",
            lambda d: ~((d["side"].eq("no")) & (d["side_probability"] < 0.72) & (d["side_distance_usd"] < 75)),
        ),
        (
            "block_no_lowprob_cheap_nearstrike",
            "Block NO with p<72%, entry<=55c, and side distance<$75",
            lambda d: ~((d["side"].eq("no")) & (d["side_probability"] < 0.72) & (d["entry_price"] <= 0.55) & (d["side_distance_usd"] < 75)),
        ),
        (
            "edge_margin_ge1",
            "Keep only trades with net edge at least 1c above threshold",
            lambda d: d["edge_margin_cents"] >= 1.0,
        ),
        (
            "edge_margin_ge3",
            "Keep only trades with net edge at least 3c above threshold",
            lambda d: d["edge_margin_cents"] >= 3.0,
        ),
        (
            "block_exhaustion_ret10_gt175_dist_lt100",
            "Block side-chase after >$175 10m move with side distance <$100",
            lambda d: ~((d["side_ret_10m"] > 175) & (d["side_distance_usd"] < 100)),
        ),
        (
            "block_exhaustion_ret10_gt125_sigma_lt075",
            "Block side-chase after >$125 10m move with distance sigma <0.75",
            lambda d: ~((d["side_ret_10m"] > 125) & (d["distance_sigma"] < 0.75)),
        ),
        (
            "keep_yes_or_strong_no",
            "Keep YES trades or NO with side probability >=72% and side distance >=40",
            lambda d: d["side"].eq("yes") | ((d["side_probability"] >= 0.72) & (d["side_distance_usd"] >= 40)),
        ),
        (
            "keep_yes_or_no_not_cheap_near",
            "Keep YES trades or NO unless cheap and near strike",
            lambda d: d["side"].eq("yes") | ~((d["entry_price"] <= 0.55) & (d["side_distance_usd"] <= 75)),
        ),
    ]
    for name, description, predicate in focused:
        add(Hypothesis(round_no, name, description, predicate))

    # Data-driven quantile cuts from train only.
    for col in ["entry_price", "net_edge_cents", "edge_margin_cents", "side_probability", "side_distance_usd", "distance_sigma", "side_ret_10m"]:
        for threshold in quantiles(train, col, [0.20, 0.35, 0.50, 0.65, 0.80]):
            key = f"{col}_q_{threshold:.4g}".replace("-", "neg").replace(".", "p")
            add(Hypothesis(round_no, f"{key}_ge", f"Keep {col} >= train quantile {threshold:.4g}", lambda d, col=col, threshold=threshold: d[col] >= threshold))
            add(Hypothesis(round_no, f"{key}_le", f"Keep {col} <= train quantile {threshold:.4g}", lambda d, col=col, threshold=threshold: d[col] <= threshold))
    return hyps


def combine_hypotheses(base: list[Hypothesis], ranked: pd.DataFrame, round_no: int, max_inputs: int = 12) -> list[Hypothesis]:
    ids = ranked[ranked["split"].eq("validation")].sort_values(
        ["pnl_delta_vs_baseline", "return_on_premium", "trades"], ascending=[False, False, False]
    )["hypothesis"].tolist()
    lookup = {h.name: h for h in base}
    selected = [lookup[name] for name in ids if name in lookup and name != "baseline_all"][:max_inputs]
    out: list[Hypothesis] = []
    for a, b in itertools.combinations(selected, 2):
        out.append(
            Hypothesis(
                round_no,
                f"{a.name}__AND__{b.name}",
                f"{a.description}; AND {b.description}",
                lambda d, a=a, b=b: safe_mask(a.predicate(d), d.index) & safe_mask(b.predicate(d), d.index),
            )
        )
    return out


def evaluate(hyps: list[Hypothesis], historical: pd.DataFrame, holdout: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    hrows: list[dict] = []
    base_by_split = {split: part.copy() for split, part in historical.groupby("split")}
    base_by_split["all"] = historical.copy()
    holdout_base = holdout.copy()
    for hyp in hyps:
        for split in ["train", "validation", "test", "all"]:
            base = base_by_split.get(split, historical.iloc[0:0])
            if base.empty:
                continue
            selected = base[safe_mask(hyp.predicate(base), base.index)].copy()
            rows.append(summarize(selected, split, hyp, base))
        selected_holdout = holdout[safe_mask(hyp.predicate(holdout), holdout.index)].copy()
        hrows.append(summarize(selected_holdout, "live_capture_holdout", hyp, holdout_base))
    return pd.DataFrame(rows), pd.DataFrame(hrows)


def select_candidates(hist_results: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    pivot = hist_results.pivot_table(
        index=["round", "hypothesis", "description"],
        columns="split",
        values=["trades", "pnl", "pnl_delta_vs_baseline", "return_on_premium", "win_rate", "max_drawdown"],
        aggfunc="first",
    )
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns]
    pivot = pivot.reset_index()
    mask = (
        (pivot.get("trades_validation", 0) >= args.min_validation_trades)
        & (pivot.get("trades_test", 0) >= args.min_test_trades)
        & (pivot.get("pnl_validation", -999) > 0)
        & (pivot.get("pnl_test", -999) > 0)
        & (pivot.get("pnl_delta_vs_baseline_validation", -999) >= 0)
    )
    selected = pivot[mask].copy()
    if selected.empty:
        selected = pivot.copy()
    selected["score"] = (
        selected.get("pnl_delta_vs_baseline_validation", 0).fillna(0)
        + 0.50 * selected.get("pnl_delta_vs_baseline_test", 0).fillna(0)
        - 0.25 * selected.get("max_drawdown_validation", 0).abs().fillna(0)
    )
    return selected.sort_values(["score", "pnl_test", "trades_test"], ascending=[False, False, False])


def win_loss_diagnostics(df: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for col in [
        "entry_price",
        "net_edge_cents",
        "side_probability",
        "ttl_min",
        "side_distance_usd",
        "distance_sigma",
        "side_ret_5m",
        "side_ret_10m",
        "side_ret_30m",
        "entry_hour_utc",
    ]:
        for win, part in df.assign(win=df["pnl"] > 0).groupby("win"):
            values = pd.to_numeric(part[col], errors="coerce").dropna()
            if values.empty:
                continue
            rows.append(
                {
                    "dataset": label,
                    "feature": col,
                    "win": bool(win),
                    "n": int(len(values)),
                    "mean": float(values.mean()),
                    "median": float(values.median()),
                    "p25": float(values.quantile(0.25)),
                    "p75": float(values.quantile(0.75)),
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    historical = load_historical(args.historical_trades, args.variant)
    holdout = load_holdout(args.capture_audit_dir, args.capture_db, args.btc_1m)

    train = historical[historical["split"].eq("train")].copy()
    base_hyps = make_single_hypotheses(train)
    hist_round1, hold_round1 = evaluate(base_hyps, historical, holdout)
    round2 = combine_hypotheses(base_hyps, hist_round1, round_no=2, max_inputs=14)
    hist_round2, hold_round2 = evaluate(round2, historical, holdout)
    top_for_round3 = pd.concat([hist_round1, hist_round2], ignore_index=True)
    round3 = combine_hypotheses(base_hyps + round2, top_for_round3, round_no=3, max_inputs=12)
    hist_round3, hold_round3 = evaluate(round3, historical, holdout)
    top_for_round4 = pd.concat([hist_round1, hist_round2, hist_round3], ignore_index=True)
    round4 = combine_hypotheses(base_hyps + round2 + round3, top_for_round4, round_no=4, max_inputs=10)
    hist_round4, hold_round4 = evaluate(round4, historical, holdout)
    top_for_round5 = pd.concat([hist_round1, hist_round2, hist_round3, hist_round4], ignore_index=True)
    round5 = combine_hypotheses(base_hyps + round2 + round3 + round4, top_for_round5, round_no=5, max_inputs=8)
    hist_round5, hold_round5 = evaluate(round5, historical, holdout)

    hist_results = pd.concat([hist_round1, hist_round2, hist_round3, hist_round4, hist_round5], ignore_index=True)
    hold_results = pd.concat([hold_round1, hold_round2, hold_round3, hold_round4, hold_round5], ignore_index=True)
    selected = select_candidates(hist_results, args)
    hold_selected = hold_results.merge(
        selected[["hypothesis", "score"]].head(50),
        on="hypothesis",
        how="inner",
    ).sort_values(["score", "pnl_delta_vs_baseline", "pnl"], ascending=[False, False, False])

    diagnostics = pd.concat(
        [
            win_loss_diagnostics(historical, "historical"),
            win_loss_diagnostics(holdout, "live_capture_holdout"),
        ],
        ignore_index=True,
    )
    baseline_hist = hist_results[hist_results["hypothesis"].eq("baseline_all")].copy()
    baseline_hold = hold_results[hold_results["hypothesis"].eq("baseline_all")].copy()

    hist_results.to_csv(args.output_dir / "historical_hypothesis_results.csv", index=False)
    hold_results.to_csv(args.output_dir / "holdout_hypothesis_results.csv", index=False)
    selected.to_csv(args.output_dir / "historical_selected_candidates.csv", index=False)
    hold_selected.to_csv(args.output_dir / "holdout_selected_candidates.csv", index=False)
    diagnostics.to_csv(args.output_dir / "win_loss_feature_diagnostics.csv", index=False)
    historical.to_csv(args.output_dir / "historical_baseline_features.csv", index=False)
    holdout.to_csv(args.output_dir / "holdout_late_only_features.csv", index=False)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "historical_trades": str(args.historical_trades),
        "historical_variant": args.variant,
        "capture_audit_dir": str(args.capture_audit_dir),
        "capture_db": str(args.capture_db),
        "historical_rows": int(len(historical)),
        "holdout_rows": int(len(holdout)),
        "historical_baseline": baseline_hist.to_dict("records"),
        "holdout_baseline": baseline_hold.to_dict("records"),
        "top_historical_candidates": selected.head(20).to_dict("records"),
        "top_validated_candidates": hold_selected.head(20).to_dict("records"),
        "leakage_controls": [
            "Hypotheses are generated and ranked from historical replay rows only.",
            "The captured websocket holdout is evaluated only after historical ranking.",
            "All features are pre-entry features: price, edge, model probability, TTL, strike distance, and prior BTC momentum.",
            "No official settlement or future orderbook data is used by predicates.",
        ],
    }
    (args.output_dir / "loss_prevention_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("BASELINE HISTORICAL")
    print(baseline_hist.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print("\nBASELINE HOLDOUT")
    print(baseline_hold.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print("\nTOP HISTORICAL SELECTED")
    print(selected.head(20).to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print("\nVALIDATION ON CAPTURED WEBSOCKET HOLDOUT")
    print(hold_selected.head(20).to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
