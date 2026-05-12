#!/usr/bin/env python3
"""Research NO-side fair-value guards with live-capture promotion gating.

This script targets the current failure mode: late, near-strike BTC hourly NO
trades where the research model is overconfident.  It generates causal
pre-entry guard candidates from historical data, ranks them only on historical
train/validation/test splits, then applies the official-result live-capture
gate as a final pass/fail check.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.live_capture_backtest_audit import OfficialResults
from scripts.live_capture_promotion_gate import apply_promotion_gate
from scripts.research_loss_prevention_filters import add_btc_momentum, add_features, max_drawdown, safe_mask, strike_from_ticker


DEFAULT_HISTORICAL = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "live_shadow_accuracy_20260510"
    / "historical_may8_variants"
    / "may8examine_trades.csv"
)
DEFAULT_HOLDOUT = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "holdout_late_only_features.csv"
DEFAULT_BTC = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_LIVE_DB = Path.home() / ".btc_kalshi_bot" / "research_live_trades.db"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "no_fair_value_research_20260511"


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    description: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-trades", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--historical-variant", default="baseline_current")
    parser.add_argument("--capture-holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--live-db", type=Path, default=DEFAULT_LIVE_DB)
    parser.add_argument("--btc-1m", type=Path, default=DEFAULT_BTC)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--official-cache", type=Path, default=DEFAULT_OUT / "official_results_cache.json")
    parser.add_argument("--recent-start", default="", help="UTC ISO cutoff for recent live ledger gate. Default: after capture holdout max time.")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--top-combine", type=int, default=18)
    parser.add_argument("--max-combos-per-round", type=int, default=2500)
    parser.add_argument("--min-validation-trades", type=int, default=4)
    parser.add_argument("--min-test-trades", type=int, default=8)
    parser.add_argument("--min-live-capture-trades", type=int, default=8)
    parser.add_argument("--watch", action="store_true", help="Repeat forever so new official live ledger rows join the gate.")
    parser.add_argument("--sleep-sec", type=int, default=900)
    return parser.parse_args()


def utc(value) -> pd.Timestamp:
    return pd.Timestamp(value).tz_convert("UTC") if pd.Timestamp(value).tzinfo else pd.Timestamp(value, tz="UTC")


def load_historical(path: Path, variant: str, btc_path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if variant not in set(df["variant"].astype(str)):
        raise ValueError(f"variant {variant!r} not found in {path}")
    df = df[df["variant"].astype(str).eq(variant)].copy()
    df["dataset"] = "historical"
    df["entry_time"] = df["entry_time"]
    df["premium"] = pd.to_numeric(df["entry_price"], errors="coerce") + pd.to_numeric(df["entry_fee"], errors="coerce")
    df = add_features(df)
    df = add_btc_momentum(df, btc_path)
    return df


def load_capture_holdout(path: Path, btc_path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    df["dataset"] = "live_capture_holdout"
    df["entry_time"] = df.get("entry_time", df.get("received_at_utc"))
    df["entry_spot"] = pd.to_numeric(df.get("entry_spot", df.get("btc_spot")), errors="coerce")
    df["premium"] = pd.to_numeric(df.get("premium_1c", df.get("premium", np.nan)), errors="coerce")
    df["pnl"] = pd.to_numeric(df.get("pnl_1c", df.get("pnl", np.nan)), errors="coerce")
    df = add_features(df)
    df = add_btc_momentum(df, btc_path)
    return df


def load_live_ledger(
    path: Path,
    official: OfficialResults,
    btc_path: Path,
    recent_start: pd.Timestamp | None,
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(path)
    rows = pd.read_sql_query(
        """
        SELECT *
        FROM research_live_trades
        WHERE lower(status) IN ('filled', 'partial_filled', 'paper_filled')
        ORDER BY created_at, id
        """,
        conn,
    )
    conn.close()
    if rows.empty:
        return rows
    rows["entry_time"] = pd.to_datetime(rows["created_at"], utc=True, errors="coerce")
    if recent_start is not None:
        rows = rows[rows["entry_time"] >= recent_start].copy()
    if rows.empty:
        return rows

    out_rows = []
    for row in rows.to_dict("records"):
        ticker = str(row["market_ticker"]).upper()
        result = official.get(ticker).get("result")
        if result not in {"yes", "no"}:
            continue
        side = str(row["side"]).lower()
        entry_price = float(row.get("actual_entry_price") or row.get("entry_price") or 0.0)
        contracts = int(float(row.get("fill_count") or row.get("contracts") or 1))
        fee_1c = kalshi_fee_dollars(entry_price, contracts=1, liquidity="taker")
        fee_actual = row.get("actual_fee_paid")
        if fee_actual is None or pd.isna(fee_actual):
            fee_actual = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
        win = side == result
        close_time = row.get("close_time")
        out_rows.append(
            {
                "dataset": "live_ledger_recent",
                "source": "live_ledger_recent",
                "split": "live_ledger_recent",
                "id": row.get("id"),
                "event_ticker": row.get("event_ticker"),
                "market_ticker": ticker,
                "side": side,
                "contracts": contracts,
                "entry_time": row.get("created_at"),
                "close_time": close_time,
                "entry_price": entry_price,
                "entry_fee": fee_1c,
                "premium": entry_price + fee_1c,
                "premium_actual": entry_price * contracts + float(fee_actual),
                "pnl": (1.0 if win else 0.0) - entry_price - fee_1c,
                "pnl_actual": contracts * (1.0 if win else 0.0) - entry_price * contracts - float(fee_actual),
                "official_result": result,
                "model_p_yes": row.get("model_p_yes"),
                "net_edge_cents": row.get("net_edge_cents"),
                "spread_cents": row.get("spread_cents"),
                "entry_spot": row.get("btc_spot"),
                "strike": strike_from_ticker(ticker),
            }
        )
    df = pd.DataFrame(out_rows)
    if df.empty:
        return df
    df = add_features(df)
    df = add_btc_momentum(df, btc_path)
    return df


def summarize(frame: pd.DataFrame, candidate: Candidate, dataset: str, baseline: pd.DataFrame) -> dict:
    part = frame.dropna(subset=["pnl"]).copy()
    base = baseline.dropna(subset=["pnl"]).copy()
    ordered = part.sort_values(["close_time", "entry_time", "market_ticker"]) if not part.empty else part
    base_ordered = base.sort_values(["close_time", "entry_time", "market_ticker"]) if not base.empty else base
    pnl = pd.to_numeric(ordered["pnl"], errors="coerce") if not ordered.empty else pd.Series(dtype=float)
    base_pnl = pd.to_numeric(base_ordered["pnl"], errors="coerce") if not base_ordered.empty else pd.Series(dtype=float)
    premium = pd.to_numeric(ordered.get("premium", pd.Series(dtype=float)), errors="coerce") if not ordered.empty else pd.Series(dtype=float)
    return {
        "candidate": candidate.name,
        "family": candidate.family,
        "description": candidate.description,
        "dataset": dataset,
        "trades": int(len(ordered)),
        "baseline_trades": int(len(base_ordered)),
        "trades_removed": int(len(base_ordered) - len(ordered)),
        "pnl": float(pnl.sum()) if not pnl.empty else 0.0,
        "baseline_pnl": float(base_pnl.sum()) if not base_pnl.empty else 0.0,
        "pnl_delta_vs_baseline": float(pnl.sum() - base_pnl.sum()) if not base_pnl.empty else 0.0,
        "premium": float(premium.sum()) if not premium.empty else 0.0,
        "return_on_premium": float(pnl.sum() / premium.sum()) if not premium.empty and premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "baseline_win_rate": float((base_pnl > 0).mean()) if len(base_pnl) else 0.0,
        "max_drawdown": max_drawdown(pnl) if len(pnl) else 0.0,
        "baseline_max_drawdown": max_drawdown(base_pnl) if len(base_pnl) else 0.0,
        "yes_trades": int(ordered["side"].eq("yes").sum()) if not ordered.empty else 0,
        "no_trades": int(ordered["side"].eq("no").sum()) if not ordered.empty else 0,
        "removed_losers": int(((base["pnl"] < 0) & ~base.index.isin(part.index)).sum()) if not base.empty else 0,
        "removed_winners": int(((base["pnl"] > 0) & ~base.index.isin(part.index)).sum()) if not base.empty else 0,
    }


def no_mask(d: pd.DataFrame) -> pd.Series:
    return d["side"].astype(str).str.lower().eq("no")


def yes_or(mask_fn: Callable[[pd.DataFrame], pd.Series]) -> Callable[[pd.DataFrame], pd.Series]:
    def _predicate(d: pd.DataFrame) -> pd.Series:
        return ~no_mask(d) | safe_mask(mask_fn(d), d.index)

    return _predicate


def col_num(d: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(d.get(col, np.nan), errors="coerce")


def generate_base_candidates() -> list[Candidate]:
    out: list[Candidate] = [
        Candidate("baseline_all", "baseline", "No additional NO fair-value guard.", lambda d: pd.Series(True, index=d.index)),
        Candidate("yes_only", "side", "Block every NO trade; keep YES only.", lambda d: ~no_mask(d)),
    ]
    p_thresholds = [0.70, 0.72, 0.75, 0.78, 0.80, 0.82, 0.85]
    dist_thresholds = [25, 40, 55, 75, 90, 100, 115, 130, 150, 200]
    edge_thresholds = [14, 15, 16, 17, 18, 20, 22, 25]
    momentum_thresholds = [-100, -50, -25, 0, 25, 50, 100]

    for p in p_thresholds:
        out.append(
            Candidate(
                f"no_p_ge_{int(p * 100)}",
                "no_probability",
                f"Keep NO only when model side probability >= {p:.2f}.",
                yes_or(lambda d, p=p: col_num(d, "side_probability") >= p),
            )
        )
    for dist in dist_thresholds:
        out.append(
            Candidate(
                f"no_distance_ge_{dist}",
                "no_settlement_buffer",
                f"Keep NO only when strike minus spot is at least ${dist}.",
                yes_or(lambda d, dist=dist: col_num(d, "side_distance_usd") >= dist),
            )
        )
    for edge in edge_thresholds:
        out.append(
            Candidate(
                f"no_edge_ge_{edge}",
                "no_edge_surcharge",
                f"Keep NO only when net edge >= {edge}c.",
                yes_or(lambda d, edge=edge: col_num(d, "net_edge_cents") >= edge),
            )
        )
    for col in ("side_ret_5m", "side_ret_10m", "side_ret_30m"):
        for threshold in momentum_thresholds:
            out.append(
                Candidate(
                    f"no_{col}_ge_{str(threshold).replace('-', 'neg')}",
                    "no_momentum",
                    f"Keep NO only when {col} >= {threshold}; negative means BTC moved against NO.",
                    yes_or(lambda d, col=col, threshold=threshold: col_num(d, col) >= threshold),
                )
            )

    for p, dist in itertools.product(p_thresholds, dist_thresholds):
        out.append(
            Candidate(
                f"no_p{int(p*100)}_dist{dist}",
                "no_probability_buffer",
                f"Keep NO only with p_no >= {p:.2f} and side distance >= ${dist}.",
                yes_or(lambda d, p=p, dist=dist: (col_num(d, "side_probability") >= p) & (col_num(d, "side_distance_usd") >= dist)),
            )
        )
    for dist, edge in itertools.product(dist_thresholds, edge_thresholds):
        out.append(
            Candidate(
                f"no_dist{dist}_edge{edge}",
                "no_buffer_edge",
                f"Keep NO only with side distance >= ${dist} and net edge >= {edge}c.",
                yes_or(lambda d, dist=dist, edge=edge: (col_num(d, "side_distance_usd") >= dist) & (col_num(d, "net_edge_cents") >= edge)),
            )
        )
    for p, dist, edge in itertools.product([0.72, 0.75, 0.80], [55, 75, 90, 100, 115, 130, 150], [15, 16, 18, 20]):
        out.append(
            Candidate(
                f"no_p{int(p*100)}_dist{dist}_edge{edge}",
                "no_probability_buffer_edge",
                f"Keep NO only with p_no >= {p:.2f}, side distance >= ${dist}, edge >= {edge}c.",
                yes_or(
                    lambda d, p=p, dist=dist, edge=edge: (
                        (col_num(d, "side_probability") >= p)
                        & (col_num(d, "side_distance_usd") >= dist)
                        & (col_num(d, "net_edge_cents") >= edge)
                    )
                ),
            )
        )

    for dist, beta in itertools.product([55, 75, 90, 100, 115, 130, 150], [0.25, 0.50, 0.75, 1.00]):
        out.append(
            Candidate(
                f"no_adverse10_buffer{dist}_beta{str(beta).replace('.', 'p')}",
                "no_dynamic_basis_buffer",
                f"Keep NO only when side distance covers ${dist} plus {beta:.2f}x adverse 10m BTC move.",
                yes_or(
                    lambda d, dist=dist, beta=beta: col_num(d, "side_distance_usd")
                    >= dist + beta * (-col_num(d, "side_ret_10m")).clip(lower=0)
                ),
            )
        )
    return out


def evaluate_candidates(candidates: list[Candidate], datasets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict] = []
    baseline_by_dataset = datasets
    for candidate in candidates:
        for dataset_name, data in datasets.items():
            if data.empty:
                rows.append(summarize(data, candidate, dataset_name, data))
                continue
            mask = safe_mask(candidate.predicate(data), data.index)
            selected = data[mask].copy()
            rows.append(summarize(selected, candidate, dataset_name, baseline_by_dataset[dataset_name]))
    return pd.DataFrame(rows)


def rank_for_combination(evals: pd.DataFrame, min_validation_trades: int, min_test_trades: int) -> list[str]:
    pivot = evals.pivot_table(
        index=["candidate", "family", "description"],
        columns="dataset",
        values=["trades", "pnl", "pnl_delta_vs_baseline", "max_drawdown", "win_rate"],
        aggfunc="first",
    )
    pivot.columns = [f"{metric}_{dataset}" for metric, dataset in pivot.columns]
    pivot = pivot.reset_index()
    selected = pivot[
        (pivot.get("trades_historical_validation", 0) >= min_validation_trades)
        & (pivot.get("trades_historical_test", 0) >= min_test_trades)
        & (pivot.get("pnl_historical_validation", -999) > 0)
        & (pivot.get("pnl_historical_test", -999) > 0)
        & (pivot.get("pnl_delta_vs_baseline_historical_validation", -999) >= 0)
    ].copy()
    if selected.empty:
        selected = pivot[pivot["candidate"].ne("baseline_all")].copy()
    selected["score"] = (
        selected.get("pnl_delta_vs_baseline_historical_validation", 0).fillna(0)
        + 0.5 * selected.get("pnl_delta_vs_baseline_historical_test", 0).fillna(0)
        - 0.25 * selected.get("max_drawdown_historical_validation", 0).abs().fillna(0)
    )
    return selected.sort_values(["score", "pnl_historical_test"], ascending=[False, False])["candidate"].head(80).tolist()


def combine_candidates(candidates: list[Candidate], names: list[str], round_no: int, top_n: int, max_combos: int) -> list[Candidate]:
    lookup = {c.name: c for c in candidates}
    chosen = [lookup[name] for name in names if name in lookup and name != "baseline_all"][:top_n]
    out: list[Candidate] = []
    for a, b in itertools.combinations(chosen, 2):
        if len(out) >= max_combos:
            break
        out.append(
            Candidate(
                f"r{round_no}_{a.name}__AND__{b.name}",
                f"round{round_no}_and",
                f"{a.description} AND {b.description}",
                lambda d, a=a, b=b: safe_mask(a.predicate(d), d.index) & safe_mask(b.predicate(d), d.index),
            )
        )
    return out


def split_historical(historical: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for split in ("train", "validation", "test"):
        part = historical[historical["split"].astype(str).eq(split)].copy()
        out[f"historical_{split}"] = part
    out["historical_all"] = historical.copy()
    return out


def latest_capture_time(capture: pd.DataFrame) -> pd.Timestamp | None:
    if capture.empty:
        return None
    col = "entry_time" if "entry_time" in capture.columns else "received_at_utc"
    values = pd.to_datetime(capture[col], utc=True, errors="coerce").dropna()
    return values.max() if not values.empty else None


def run_once(args: argparse.Namespace) -> dict:
    started = datetime.now(timezone.utc)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    official = OfficialResults(args.official_cache)

    historical = load_historical(args.historical_trades, args.historical_variant, args.btc_1m)
    capture = load_capture_holdout(args.capture_holdout, args.btc_1m)
    if args.recent_start.strip():
        recent_start = pd.Timestamp(args.recent_start, tz="UTC")
    else:
        recent_start = latest_capture_time(capture)
    live_ledger = load_live_ledger(args.live_db, official, args.btc_1m, recent_start)
    official.write()

    datasets = split_historical(historical)
    datasets["live_capture_holdout"] = capture
    datasets["live_ledger_recent"] = live_ledger

    all_candidates = generate_base_candidates()
    all_evals: list[pd.DataFrame] = []
    current = all_candidates
    seen = {c.name for c in all_candidates}
    for round_no in range(1, max(1, args.rounds) + 1):
        print(f"{datetime.now().isoformat()} round={round_no} candidates={len(current)} total={len(all_candidates)}")
        evals = evaluate_candidates(current, datasets)
        all_evals.append(evals)
        ranked_names = rank_for_combination(
            pd.concat(all_evals, ignore_index=True),
            args.min_validation_trades,
            args.min_test_trades,
        )
        if round_no >= args.rounds:
            break
        new_candidates = combine_candidates(
            all_candidates,
            ranked_names,
            round_no=round_no + 1,
            top_n=args.top_combine,
            max_combos=args.max_combos_per_round,
        )
        current = [c for c in new_candidates if c.name not in seen]
        for candidate in current:
            seen.add(candidate.name)
        all_candidates.extend(current)
        if not current:
            break

    eval_long = pd.concat(all_evals, ignore_index=True).drop_duplicates(["candidate", "dataset"], keep="last")
    gate = apply_promotion_gate(
        eval_long,
        min_historical_validation_trades=args.min_validation_trades,
        min_historical_test_trades=args.min_test_trades,
        min_live_capture_trades=args.min_live_capture_trades,
        min_live_ledger_trades=1,
        allow_missing_live_ledger=True,
    )

    candidate_meta = pd.DataFrame(
        [{"candidate": c.name, "family": c.family, "description": c.description} for c in all_candidates]
    ).drop_duplicates("candidate")
    gate = gate.merge(candidate_meta, on="candidate", how="left")

    eval_long.to_csv(args.output_dir / "candidate_eval_long.csv", index=False)
    gate.to_csv(args.output_dir / "promotion_gate_results.csv", index=False)
    historical.to_csv(args.output_dir / "historical_features.csv", index=False)
    capture.to_csv(args.output_dir / "live_capture_holdout_features.csv", index=False)
    live_ledger.to_csv(args.output_dir / "live_ledger_recent_features.csv", index=False)

    passed = gate[gate["gate_pass"].eq(True)].copy()
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started.isoformat(),
        "historical_trades": str(args.historical_trades),
        "historical_variant": args.historical_variant,
        "capture_holdout": str(args.capture_holdout),
        "live_db": str(args.live_db),
        "recent_start": str(recent_start) if recent_start is not None else None,
        "datasets": {name: int(len(df)) for name, df in datasets.items()},
        "candidate_count": int(eval_long["candidate"].nunique()),
        "gate_pass_count": int(len(passed)),
        "top_gate_pass": passed.head(20).to_dict("records"),
        "leakage_controls": [
            "Candidate thresholds are pre-generated from causal columns only.",
            "Historical train/validation/test ranking occurs before live gate review.",
            "Official Kalshi result is used only for PnL labels, never as a predicate input.",
            "Live capture is a final gate, not a threshold-fitting set.",
            "Recent live ledger is included because the newest live capture DB is locked while the bot runs.",
        ],
    }
    (args.output_dir / "no_fair_value_research_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nTOP PROMOTION GATE RESULTS")
    display_cols = [
        "candidate",
        "gate_pass",
        "live_ledger_delta",
        "live_ledger_pnl",
        "live_ledger_trades",
        "live_capture_delta",
        "live_capture_pnl",
        "live_capture_trades",
        "historical_validation_delta",
        "historical_test_delta",
        "reasons",
    ]
    with pd.option_context("display.max_rows", 60, "display.width", 240):
        print(gate[display_cols].head(40).to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return report


def main() -> int:
    args = parse_args()
    while True:
        run_once(args)
        if not args.watch:
            return 0
        print(f"sleeping {args.sleep_sec}s before next research/gate refresh")
        time.sleep(max(60, args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())
