#!/usr/bin/env python3
"""MCTS-style strategy search over the BTC 1-hour Kalshi replay.

This is a research harness, not a live-trading script. It uses the corrected
DuckDB bid/ask replay through may8examine.py and keeps the usual chronology:

1. Search/update the tree on train data only.
2. Select candidates from train objective only.
3. Evaluate selected candidates once on the full train/validation/test span.

The action space is deliberately limited to interpretable guardrails that can
be implemented in the current executor: probability shrinkage, edge buffers,
side-specific strictness, entry caps, distance buffers, momentum-exhaustion
guards, and UTC-hour exclusions.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import may8examine as m8


Decision = tuple[str, Any]


DIMENSIONS: list[tuple[str, list[Any]]] = [
    ("family", ["balanced", "yes_lowrisk", "no_strict", "shrink_buffer"]),
    ("market_shrink", [0.15, 0.25, 0.35, 0.50]),
    ("min_edge_cents", [8.0, 10.0, 12.0, 14.0, 18.0]),
    ("yes_edge_add_cents", [0.0, 2.0, 4.0]),
    ("no_edge_add_cents", [0.0, 3.0, 6.0, 8.0]),
    ("min_yes_p", [0.65, 0.70, 0.75]),
    ("max_no_p", [0.18, 0.20, 0.25, 0.30, 0.35]),
    ("min_entry", [0.25]),
    ("max_entry", [0.65, 0.70, 0.75]),
    ("min_distance_sigma", [0.0, 0.50]),
    ("min_abs_distance_usd", [0.0, 50.0]),
    ("momentum_guard_usd", [0.0]),
    ("no_momentum_guard_usd", [0.0, 75.0]),
    ("side_mode", ["both"]),
    ("time_guard", ["none"]),
]

BASELINE_VARIANTS = [
    m8.Variant("baseline_current"),
    m8.Variant(
        "market_shrink_no_cautious",
        market_shrink=0.25,
        min_edge_cents=8.0,
        max_no_p=0.20,
        no_edge_add_cents=3.0,
    ),
    m8.Variant(
        "strict_yes_lowrisk",
        market_shrink=0.25,
        min_edge_cents=10.0,
        yes_edge_add_cents=4.0,
        no_edge_add_cents=4.0,
        min_yes_p=0.70,
        max_no_p=0.18,
        max_entry=0.65,
    ),
]


class TreeStats:
    def __init__(self) -> None:
        self.visits: dict[tuple[Decision, ...], int] = defaultdict(int)
        self.reward_sum: dict[tuple[Decision, ...], float] = defaultdict(float)

    def score_child(self, parent: tuple[Decision, ...], child: tuple[Decision, ...], c: float, rng: random.Random) -> float:
        n = self.visits[child]
        if n <= 0:
            return 1_000_000.0 + rng.random()
        parent_n = max(1, self.visits[parent])
        mean = self.reward_sum[child] / n
        return mean + c * math.sqrt(math.log(parent_n + 1.0) / n)

    def update(self, path: tuple[Decision, ...], reward: float) -> None:
        for i in range(len(path) + 1):
            prefix = path[:i]
            self.visits[prefix] += 1
            self.reward_sum[prefix] += reward


def choose_path(stats: TreeStats, rng: random.Random, exploration: float) -> tuple[Decision, ...]:
    path: tuple[Decision, ...] = ()
    for name, values in DIMENSIONS:
        children = [path + ((name, value),) for value in values]
        child = max(children, key=lambda c: stats.score_child(path, c, exploration, rng))
        path = child
    return path


def random_path(rng: random.Random) -> tuple[Decision, ...]:
    return tuple((name, rng.choice(values)) for name, values in DIMENSIONS)


def path_to_variant(path: tuple[Decision, ...], idx: int) -> m8.Variant:
    params = {name: value for name, value in path}
    family = str(params.pop("family"))
    side_mode = str(params.pop("side_mode"))
    time_guard = str(params.pop("time_guard"))

    min_entry = float(params["min_entry"])
    max_entry = float(params["max_entry"])
    if min_entry > max_entry:
        min_entry, max_entry = max_entry, min_entry

    exclude_hours: tuple[int, ...]
    if time_guard == "skip_17_23":
        exclude_hours = tuple(range(17, 24))
    elif time_guard == "skip_00_03":
        exclude_hours = tuple(range(0, 4))
    elif time_guard == "skip_07_16":
        exclude_hours = tuple(range(7, 17))
    else:
        exclude_hours = ()

    allow_yes = side_mode != "no_only"
    allow_no = side_mode != "yes_only"

    market_shrink = float(params["market_shrink"])
    min_edge = float(params["min_edge_cents"])
    yes_edge = float(params["yes_edge_add_cents"])
    no_edge = float(params["no_edge_add_cents"])
    min_yes_p = float(params["min_yes_p"])
    max_no_p = float(params["max_no_p"])
    min_dist_sigma = float(params["min_distance_sigma"])
    min_abs_dist = float(params["min_abs_distance_usd"])
    momentum_guard = float(params["momentum_guard_usd"])
    no_momentum_guard = float(params["no_momentum_guard_usd"])

    if family == "yes_lowrisk":
        min_yes_p = max(min_yes_p, 0.70)
        max_entry = min(max_entry, 0.65)
        yes_edge += 2.0
    elif family == "no_strict":
        max_no_p = min(max_no_p, 0.20)
        no_edge += 3.0
    elif family == "shrink_buffer":
        market_shrink = max(market_shrink, 0.25)
        min_dist_sigma = max(min_dist_sigma, 0.50)
        min_abs_dist = max(min_abs_dist, 50.0)
    elif family == "momentum_guard":
        momentum_guard = max(momentum_guard, 100.0)
        no_momentum_guard = max(no_momentum_guard, 100.0)

    name = (
        f"mcts_{idx:03d}_{family}_ms{market_shrink:.2f}_e{min_edge:.0f}_"
        f"yp{min_yes_p:.2f}_np{max_no_p:.2f}_mx{max_entry:.2f}_{side_mode}_{time_guard}"
    )
    name = name.replace(".", "p")

    return m8.Variant(
        name=name,
        market_shrink=market_shrink,
        min_edge_cents=min_edge,
        yes_edge_add_cents=yes_edge,
        no_edge_add_cents=no_edge,
        min_yes_p=min_yes_p,
        max_no_p=max_no_p,
        min_entry=min_entry,
        max_entry=max_entry,
        min_distance_sigma=min_dist_sigma,
        min_abs_distance_usd=min_abs_dist,
        momentum_guard_usd=momentum_guard,
        momentum_guard_distance_sigma=1.15,
        momentum_guard_distance_usd=200.0,
        no_momentum_guard_usd=no_momentum_guard,
        no_momentum_guard_distance_usd=225.0,
        no_momentum_guard_distance_sigma=1.20,
        allow_yes=allow_yes,
        allow_no=allow_no,
        exclude_utc_hours=exclude_hours,
    )


def objective_from_summary(summary: pd.DataFrame, variant: str, split: str = "train") -> float:
    if summary.empty or "variant" not in summary.columns or "split" not in summary.columns:
        return -100.0
    row = summary[(summary["variant"] == variant) & (summary["split"] == split)]
    if row.empty:
        return -100.0
    r = row.iloc[0]
    trades = int(r["trades"])
    pnl = float(r["pnl"])
    premium = float(r["premium"])
    dd = abs(float(r["max_drawdown"]))
    win_rate = float(r["win_rate"])
    rop = float(r["return_on_premium"]) if premium else 0.0
    if trades < 8:
        return -50.0 + pnl
    return pnl + 0.35 * min(trades, 80) / 10.0 + 5.0 * rop + 0.50 * win_rate - 0.75 * dd


def score_full(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    rows = []
    for variant, g in summary.groupby("variant"):
        by_split = {str(r["split"]): r for _, r in g.iterrows()}
        train = by_split.get("train")
        val = by_split.get("validation")
        test = by_split.get("test")
        if train is None or val is None or test is None:
            continue
        total_pnl = float(train["pnl"]) + float(val["pnl"]) + float(test["pnl"])
        total_premium = float(train["premium"]) + float(val["premium"]) + float(test["premium"])
        total_trades = int(train["trades"]) + int(val["trades"]) + int(test["trades"])
        min_split_pnl = min(float(train["pnl"]), float(val["pnl"]), float(test["pnl"]))
        pass_gate = (
            int(train["trades"]) >= 8
            and int(val["trades"]) >= 4
            and int(test["trades"]) >= 4
            and float(val["pnl"]) > 0
            and float(test["pnl"]) > 0
        )
        rows.append(
            {
                "variant": variant,
                "train_trades": int(train["trades"]),
                "train_pnl": float(train["pnl"]),
                "train_rop_pct": float(train["return_on_premium"]) * 100.0,
                "train_win_pct": float(train["win_rate"]) * 100.0,
                "train_dd": float(train["max_drawdown"]),
                "validation_trades": int(val["trades"]),
                "validation_pnl": float(val["pnl"]),
                "validation_rop_pct": float(val["return_on_premium"]) * 100.0,
                "validation_win_pct": float(val["win_rate"]) * 100.0,
                "validation_dd": float(val["max_drawdown"]),
                "test_trades": int(test["trades"]),
                "test_pnl": float(test["pnl"]),
                "test_rop_pct": float(test["return_on_premium"]) * 100.0,
                "test_win_pct": float(test["win_rate"]) * 100.0,
                "test_dd": float(test["max_drawdown"]),
                "total_trades": total_trades,
                "total_pnl": total_pnl,
                "total_rop_pct": (total_pnl / total_premium * 100.0) if total_premium else 0.0,
                "min_split_pnl": min_split_pnl,
                "pass_gate": pass_gate,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["rank_score"] = (
        out["validation_pnl"]
        + out["test_pnl"]
        + 0.02 * np.minimum(out["total_trades"], 120)
        - 0.5 * (out["validation_dd"].abs() + out["test_dd"].abs())
    )
    return out.sort_values(["pass_gate", "rank_score"], ascending=[False, False])


def evaluate_variants(source: str, quotes: pd.DataFrame, btc: pd.DataFrame, variants: list[m8.Variant], train_days: int, progress: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = m8.run_replay(source, quotes, btc, train_days, progress, variants)
    summary = m8.summarize(trades)
    return trades, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=m8.DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "mcts_strategy_research")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--batches", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--top-k", type=int, default=16)
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--progress-every-events", type=int, default=200)
    parser.add_argument("--exploration", type=float, default=1.25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading corrected historical DuckDB replay...", flush=True)
    quotes, btc, data_report = m8.load_duckdb_quotes(args.db, None, None, args.max_events)
    if quotes.empty:
        raise SystemExit("no historical quotes loaded")
    train_quotes = quotes[pd.to_datetime(quotes["close_time"], utc=True) < m8.TRAIN_END].copy()
    if train_quotes.empty:
        train_quotes = quotes.copy()
        print("WARNING: no train-split rows loaded; using loaded rows for search. Do not use this mode for final research.", flush=True)
    print(f"Loaded quote rows={len(quotes):,} train_rows={len(train_quotes):,}", flush=True)

    stats = TreeStats()
    tried: set[tuple[Decision, ...]] = set()
    all_train_rows = []
    all_variants: dict[str, m8.Variant] = {}

    for batch_idx in range(args.batches):
        paths: list[tuple[Decision, ...]] = []
        guard = 0
        while len(paths) < args.batch_size and guard < args.batch_size * 50:
            guard += 1
            path = choose_path(stats, rng, args.exploration)
            if path in tried:
                path = random_path(rng)
            if path in tried:
                continue
            tried.add(path)
            paths.append(path)
        variants = [path_to_variant(path, batch_idx * args.batch_size + i) for i, path in enumerate(paths)]
        for variant in variants:
            all_variants[variant.name] = variant
        print(f"\nMCTS batch {batch_idx + 1}/{args.batches}: evaluating {len(variants)} train-only candidates", flush=True)
        _, summary = evaluate_variants(
            "historical_duckdb",
            train_quotes,
            btc,
            variants,
            args.train_days,
            args.progress_every_events,
        )
        for path, variant in zip(paths, variants):
            reward = objective_from_summary(summary, variant.name, "train")
            stats.update(path, reward)
        if not summary.empty:
            summary = summary.copy()
            summary["batch"] = batch_idx
            summary["train_objective"] = [objective_from_summary(summary, v, "train") for v in summary["variant"]]
            all_train_rows.append(summary)
            pd.concat(all_train_rows, ignore_index=True).to_csv(args.output_dir / "mcts_train_summary.partial.csv", index=False)
            (args.output_dir / "mcts_variants.partial.json").write_text(
                json.dumps([asdict(v) for v in all_variants.values()], indent=2),
                encoding="utf-8",
            )

    train_summary = pd.concat(all_train_rows, ignore_index=True) if all_train_rows else pd.DataFrame()
    train_summary_path = args.output_dir / "mcts_train_summary.csv"
    train_summary.to_csv(train_summary_path, index=False)

    if train_summary.empty:
        raise SystemExit("MCTS generated no train trades")
    ranked_train = (
        train_summary[train_summary["split"] == "train"]
        .sort_values("train_objective", ascending=False)
        .drop_duplicates("variant")
    )
    selected_names = ranked_train.head(args.top_k)["variant"].tolist()
    selected = [all_variants[name] for name in selected_names]
    selected = BASELINE_VARIANTS + selected
    print(f"\nFull chronological replay of {len(selected)} selected/baseline variants", flush=True)
    full_trades, full_summary = evaluate_variants(
        "historical_duckdb",
        quotes,
        btc,
        selected,
        args.train_days,
        args.progress_every_events,
    )
    scorecard = score_full(full_summary)

    full_trades_path = args.output_dir / "mcts_full_trades.csv"
    full_summary_path = args.output_dir / "mcts_full_summary.csv"
    scorecard_path = args.output_dir / "mcts_scorecard.csv"
    variants_path = args.output_dir / "mcts_variants.json"
    report_path = args.output_dir / "mcts_report.json"
    full_trades.to_csv(full_trades_path, index=False)
    full_summary.to_csv(full_summary_path, index=False)
    scorecard.to_csv(scorecard_path, index=False)
    variants_path.write_text(json.dumps([asdict(v) for v in selected], indent=2), encoding="utf-8")
    report_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "seed": args.seed,
                "batches": args.batches,
                "batch_size": args.batch_size,
                "top_k": args.top_k,
                "train_days": args.train_days,
                "data_report": data_report,
                "train_summary_path": str(train_summary_path),
                "full_summary_path": str(full_summary_path),
                "scorecard_path": str(scorecard_path),
                "full_trades_path": str(full_trades_path),
                "variants_path": str(variants_path),
                "method_notes": [
                    "Tree/UCB search updates only from train objective.",
                    "Validation/test are used only in the final replay scorecard.",
                    "All replay PnL uses corrected historical bid/ask and estimated Kalshi taker entry fees.",
                    "Settlement uses BTC minute-close proxy where official Kalshi expiration_value is not in the historical datamart.",
                ],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\nTOP SCORECARD", flush=True)
    if scorecard.empty:
        print("No complete split scorecard rows.", flush=True)
    else:
        cols = [
            "variant",
            "train_trades",
            "train_pnl",
            "validation_trades",
            "validation_pnl",
            "test_trades",
            "test_pnl",
            "total_pnl",
            "total_rop_pct",
            "pass_gate",
        ]
        with pd.option_context("display.max_rows", 50, "display.width", 240):
            print(scorecard[cols].head(20).to_string(index=False), flush=True)
    print(f"\nWrote {scorecard_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
