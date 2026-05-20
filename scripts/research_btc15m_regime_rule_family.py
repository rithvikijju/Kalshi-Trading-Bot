#!/usr/bin/env python3
"""BTC15M fixed-family regime rule research on Predexon side candidates.

This script deliberately separates:

* train: Apr1-14, used only to fit/select from a predeclared rule family
* validation: Apr15-30 and May1-12, used to test transfer
* external: optional Jan/live-like files, reported only

Every rule is causal and executable in the same simplified way:
first qualifying candidate per event, one contract, top ask entry, visible
top quantity gate, Kalshi taker fee, and +2c adverse entry stress.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_regime_rule_family_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


@dataclass(frozen=True)
class Rule:
    name: str
    fair_p_min: float = 0.60
    edge_min: float = 12.0
    ttl_min: float = 5.0
    ttl_max: float = 12.0
    spread_max: float = 2.0
    entry_min: float = 0.02
    entry_max: float = 0.60
    qty_min: float = 1.0
    rv_min: float | None = None
    rv_max: float | None = None
    abs_distance_min: float | None = None
    abs_distance_max: float | None = None
    side_btc_ret_min: float | None = None
    side_btc_ret_max: float | None = None
    side_mid_chg_2m_min: float | None = None
    side_mid_chg_2m_max: float | None = None
    side_mid_chg_3m_min: float | None = None
    side_mid_chg_3m_max: float | None = None
    quote_speed_max: float | None = None
    fair_edge_max: float | None = None
    rationale: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--train", type=Path, required=True)
    p.add_argument("--val", type=Path, required=True)
    p.add_argument("--may", type=Path, required=True)
    p.add_argument("--external", type=Path, default=None)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--stress-cents", type=float, default=2.0)
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--min-train-trades", type=int, default=25)
    return p.parse_args()


def rule_family() -> list[Rule]:
    rules: list[Rule] = []

    # Baselines and strict F2 variants.
    for fair_p in [0.60, 0.65, 0.70]:
        for edge in [10.0, 12.0, 15.0]:
            rules.append(Rule(f"fv_p{int(fair_p*100)}_e{int(edge)}_ttl5_12_sp2_e2_60", fair_p_min=fair_p, edge_min=edge))
    rules.extend(
        [
            Rule("strict_ttl10_12_e55_q50", ttl_min=10, ttl_max=12, entry_max=0.55, qty_min=50, rationale="current strict F2 shadow family"),
            Rule("ttl6_12_e60", ttl_min=6, ttl_max=12, rationale="less early than base"),
            Rule("ttl8_12_e60", ttl_min=8, ttl_max=12, rationale="late-window freshness"),
            Rule("ttl10_12_e60", ttl_min=10, ttl_max=12, rationale="very late-window freshness"),
        ]
    )

    # Regime gates motivated by prior diagnostics: low RV and thin books hurt.
    for rv in [0.20, 0.275, 0.32, 0.40]:
        for qty in [1, 50, 100, 300]:
            rules.append(
                Rule(
                    f"highrv{int(rv*1000)}_q{qty}_base",
                    rv_min=rv,
                    qty_min=float(qty),
                    rationale="trade only when realized BTC vol and displayed liquidity suggest the book is informative",
                )
            )
            rules.append(
                Rule(
                    f"highrv{int(rv*1000)}_q{qty}_ttl6_12",
                    ttl_min=6,
                    ttl_max=12,
                    rv_min=rv,
                    qty_min=float(qty),
                    rationale="high-RV plus later decision window",
                )
            )

    # Avoid overly cute reported edges where the fair-value model may be stale.
    for max_edge in [18.0, 22.0, 28.0]:
        rules.append(Rule(f"edge12_to_{int(max_edge)}_highrv32", rv_min=0.32, fair_edge_max=max_edge, rationale="cap extreme model edge"))
        rules.append(Rule(f"edge12_to_{int(max_edge)}_ttl6_highrv32", ttl_min=6, ttl_max=12, rv_min=0.32, fair_edge_max=max_edge))

    # Contract price-history/microstructure families using side-relative mid changes.
    # Positive side_mid_chg means the market has moved in the candidate side's
    # direction before entry; negative means the candidate side has cheapened.
    for mom in [0.0, 0.02, 0.05]:
        rules.append(Rule(f"side_mom2m_ge_{int(mom*100)}_highrv20", rv_min=0.20, side_mid_chg_2m_min=mom, rationale="book confirms side momentum"))
        rules.append(Rule(f"side_mom3m_ge_{int(mom*100)}_highrv20", rv_min=0.20, side_mid_chg_3m_min=mom))
    for cheap in [-0.02, -0.05, -0.10]:
        rules.append(Rule(f"side_not_falling2m_ge_{int(cheap*100)}", side_mid_chg_2m_min=cheap, rv_min=0.20, rationale="avoid catching rapidly falling side"))
        rules.append(Rule(f"side_pullback2m_le_{int(cheap*100)}_highrv32", side_mid_chg_2m_max=cheap, rv_min=0.32, rationale="buy side pullback only in high RV"))

    # BTC momentum alignment families. side_btc_ret is positive when spot moved
    # toward the side over the last 3 minutes.
    for bps in [0.0, 2.0, 5.0, 10.0]:
        rules.append(Rule(f"btc_align3m_ge_{int(bps)}_highrv20", rv_min=0.20, side_btc_ret_min=bps, rationale="spot confirms fair-value side"))
    for bps in [-2.0, 0.0]:
        rules.append(Rule(f"btc_not_against3m_ge_{int(bps)}", side_btc_ret_min=bps, rationale="avoid trades against recent spot move"))

    # Distance and stability regimes.
    for max_dist in [5.0, 10.0, 20.0, 50.0]:
        rules.append(Rule(f"near_strike_absdist_le_{int(max_dist)}_highrv20", rv_min=0.20, abs_distance_max=max_dist, rationale="focus near-strike where book may misprice path"))
    for min_dist in [5.0, 10.0, 20.0]:
        rules.append(Rule(f"away_from_strike_absdist_ge_{int(min_dist)}_highrv20", rv_min=0.20, abs_distance_min=min_dist, rationale="avoid noisy knife-edge near strike"))
    for qmax in [0.5, 1.0, 2.0]:
        rules.append(Rule(f"stable_quote_qspeed_le_{str(qmax).replace('.', '_')}_highrv20", rv_min=0.20, quote_speed_max=qmax, rationale="avoid fast-moving stale quote regimes"))

    # A few combined liquidity/volatility/momentum rules, fixed before seeing this run's results.
    rules.extend(
        [
            Rule("liquid_highrv32_not_against", rv_min=0.32, qty_min=100, side_btc_ret_min=0.0, entry_max=0.60),
            Rule("liquid_highrv32_side_mom", rv_min=0.32, qty_min=100, side_mid_chg_2m_min=0.0, entry_max=0.60),
            Rule("liquid_highrv32_ttl8_stable", ttl_min=8, ttl_max=12, rv_min=0.32, qty_min=100, quote_speed_max=1.0),
            Rule("liquid_highrv32_edge_capped", rv_min=0.32, qty_min=100, fair_edge_max=22.0),
            Rule("strict_plus_highrv20", ttl_min=10, ttl_max=12, entry_max=0.55, qty_min=50, rv_min=0.20),
            Rule("strict_plus_highrv32", ttl_min=10, ttl_max=12, entry_max=0.55, qty_min=50, rv_min=0.32),
            Rule("strict_plus_not_against", ttl_min=10, ttl_max=12, entry_max=0.55, qty_min=50, side_btc_ret_min=0.0),
            Rule("strict_plus_stable", ttl_min=10, ttl_max=12, entry_max=0.55, qty_min=50, quote_speed_max=1.0),
        ]
    )

    # Deduplicate by name while preserving order.
    seen: set[str] = set()
    deduped: list[Rule] = []
    for rule in rules:
        if rule.name not in seen:
            seen.add(rule.name)
            deduped.append(rule)
    return deduped


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True)
    df["side_sign"] = np.where(df["side"].astype(str).str.lower() == "yes", 1.0, -1.0)
    df["abs_distance_bps"] = df["distance_bps"].abs()
    df["side_btc_ret_3m_bps"] = df["btc_ret_3m_bps"] * df["side_sign"]
    df["side_mid_chg_2m"] = df["yes_mid_chg_2_0m"] * df["side_sign"]
    df["side_mid_chg_3m"] = df["yes_mid_chg_3_0m"] * df["side_sign"]
    df = df.sort_values(["available_at", "market_ticker", "side"]).reset_index(drop=True)
    return df


def load_candidates(path: Path) -> pd.DataFrame:
    cols = [
        "market_ticker",
        "event_ticker",
        "available_at",
        "close_time",
        "side",
        "entry_price",
        "visible_qty",
        "side_fair_p",
        "fair_edge_cents",
        "spread_cents",
        "ttl_min",
        "rv_60m",
        "distance_bps",
        "btc_ret_3m_bps",
        "quote_speed_cents",
        "yes_mid_chg_2_0m",
        "yes_mid_chg_3_0m",
        "win",
    ]
    df = pd.read_parquet(path, columns=cols)
    return add_derived(df)


def apply_rule_mask(df: pd.DataFrame, rule: Rule) -> pd.Series:
    mask = (
        df["win"].notna()
        & df["entry_price"].between(rule.entry_min, rule.entry_max, inclusive="both")
        & (df["side_fair_p"] >= rule.fair_p_min)
        & (df["fair_edge_cents"] >= rule.edge_min)
        & df["ttl_min"].between(rule.ttl_min, rule.ttl_max, inclusive="both")
        & (df["spread_cents"] <= rule.spread_max)
        & (df["visible_qty"] >= rule.qty_min)
    )
    checks: list[tuple[str, Callable[[pd.Series], pd.Series]]] = [
        ("rv_min", lambda s: df["rv_60m"] >= s),
        ("rv_max", lambda s: df["rv_60m"] <= s),
        ("abs_distance_min", lambda s: df["abs_distance_bps"] >= s),
        ("abs_distance_max", lambda s: df["abs_distance_bps"] <= s),
        ("side_btc_ret_min", lambda s: df["side_btc_ret_3m_bps"] >= s),
        ("side_btc_ret_max", lambda s: df["side_btc_ret_3m_bps"] <= s),
        ("side_mid_chg_2m_min", lambda s: df["side_mid_chg_2m"] >= s),
        ("side_mid_chg_2m_max", lambda s: df["side_mid_chg_2m"] <= s),
        ("side_mid_chg_3m_min", lambda s: df["side_mid_chg_3m"] >= s),
        ("side_mid_chg_3m_max", lambda s: df["side_mid_chg_3m"] <= s),
        ("quote_speed_max", lambda s: df["quote_speed_cents"] <= s),
        ("fair_edge_max", lambda s: df["fair_edge_cents"] <= s),
    ]
    for attr, fn in checks:
        value = getattr(rule, attr)
        if value is not None:
            mask &= fn(value)
    return mask.fillna(False)


def score_trades(trades: pd.DataFrame, stress_cents: float) -> pd.DataFrame:
    out = trades.copy()
    out["entry_stress"] = (out["entry_price"].astype(float) + stress_cents / 100.0).clip(upper=0.99)
    out["fee_stress"] = out["entry_stress"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker"))
    out["premium_stress"] = out["entry_stress"] + out["fee_stress"]
    out["pnl_stress"] = np.where(out["win"].astype(bool), 1.0 - out["premium_stress"], -out["premium_stress"])
    return out


def max_drawdown(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    equity = pnl.astype(float).cumsum()
    return float((equity - equity.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    if len(pnl) < 2:
        return 0.0
    std = float(pnl.std(ddof=1))
    if std == 0:
        return 0.0
    return float(pnl.mean() / std * math.sqrt(len(pnl)))


def evaluate_rule(df: pd.DataFrame, rule: Rule, split: str, stress_cents: float) -> tuple[dict[str, object], pd.DataFrame]:
    eligible = df.loc[apply_rule_mask(df, rule)].copy()
    if eligible.empty:
        row = {
            "split": split,
            "rule": rule.name,
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "first_entry": "",
            "last_entry": "",
        }
        return row, eligible
    trades = eligible.sort_values(["available_at", "market_ticker", "side"]).drop_duplicates("event_ticker", keep="first")
    trades = score_trades(trades, stress_cents)
    pnl = trades["pnl_stress"].astype(float)
    premium = float(trades["premium_stress"].sum())
    row = {
        "split": split,
        "rule": rule.name,
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4),
        "premium": round(premium, 4),
        "rop": round(float(pnl.sum() / premium), 4) if premium else 0.0,
        "win_rate": round(float(trades["win"].mean()), 4),
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "first_entry": str(trades["available_at"].min()),
        "last_entry": str(trades["available_at"].max()),
    }
    trades["rule"] = rule.name
    trades["split"] = split
    return row, trades


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rules = rule_family()
    datasets = {
        "train_apr1_14": load_candidates(args.train),
        "val_apr15_30": load_candidates(args.val),
        "val_may1_12": load_candidates(args.may),
    }
    if args.external:
        datasets["external"] = load_candidates(args.external)

    jobs: list[tuple[str, Rule]] = [(split, rule) for split in datasets for rule in rules]
    rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []

    def run_job(item: tuple[str, Rule]) -> tuple[dict[str, object], pd.DataFrame]:
        split, rule = item
        return evaluate_rule(datasets[split], rule, split, args.stress_cents)

    with ThreadPoolExecutor(max_workers=max(1, args.threads)) as ex:
        for row, trades in ex.map(run_job, jobs):
            rows.append(row)
            if not trades.empty:
                keep = [
                    "split",
                    "rule",
                    "event_ticker",
                    "market_ticker",
                    "available_at",
                    "close_time",
                    "side",
                    "entry_price",
                    "entry_stress",
                    "premium_stress",
                    "visible_qty",
                    "side_fair_p",
                    "fair_edge_cents",
                    "ttl_min",
                    "spread_cents",
                    "rv_60m",
                    "side_btc_ret_3m_bps",
                    "side_mid_chg_2m",
                    "side_mid_chg_3m",
                    "quote_speed_cents",
                    "win",
                    "pnl_stress",
                ]
                trade_frames.append(trades[keep])

    summary = pd.DataFrame(rows)
    rule_defs = pd.DataFrame([asdict(r) for r in rules])
    wide = summary.pivot(index="rule", columns="split", values=["trades", "pnl", "win_rate", "max_dd", "sharpe", "premium", "rop"])
    wide.columns = [f"{metric}_{split}" for metric, split in wide.columns]
    wide = wide.reset_index().merge(rule_defs, left_on="rule", right_on="name", how="left").drop(columns=["name"])
    for col in wide.columns:
        if col.startswith("trades_"):
            wide[col] = wide[col].fillna(0).astype(int)
    wide["train_pass"] = (
        (wide.get("trades_train_apr1_14", 0) >= args.min_train_trades)
        & (wide.get("pnl_train_apr1_14", 0.0) > 0)
        & (wide.get("win_rate_train_apr1_14", 0.0) >= 0.58)
        & (wide.get("max_dd_train_apr1_14", 0.0) >= -4.0)
    )
    wide["val_composite_pnl"] = wide.get("pnl_val_apr15_30", 0.0).fillna(0.0) + wide.get("pnl_val_may1_12", 0.0).fillna(0.0)
    wide["val_pass_soft"] = (
        (wide.get("trades_val_apr15_30", 0) >= 10)
        & (wide.get("trades_val_may1_12", 0) >= 5)
        & (wide.get("pnl_val_apr15_30", 0.0) > 0)
        & (wide.get("pnl_val_may1_12", 0.0) > 0)
        & (wide.get("max_dd_val_apr15_30", 0.0) >= -3.0)
        & (wide.get("max_dd_val_may1_12", 0.0) >= -2.0)
    )
    wide = wide.sort_values(["train_pass", "val_pass_soft", "val_composite_pnl", "pnl_train_apr1_14"], ascending=[False, False, False, False])

    summary.to_csv(args.out_dir / "split_summary_long.csv", index=False)
    wide.to_csv(args.out_dir / "rule_summary_wide.csv", index=False)
    rule_defs.to_csv(args.out_dir / "rule_definitions.csv", index=False)
    if trade_frames:
        all_trades = pd.concat(trade_frames, ignore_index=True)
        all_trades.to_parquet(args.out_dir / "all_rule_trades.parquet", index=False)
    else:
        all_trades = pd.DataFrame()

    report_lines = [
        "# BTC15M Regime Rule Family",
        "",
        "Training split: Apr1-14 only. Validation splits: Apr15-30 and May1-12. This is a fixed family diagnostic, not a deployment decision.",
        "",
        "## Top Rules",
    ]
    show_cols = [
        "rule",
        "trades_train_apr1_14",
        "pnl_train_apr1_14",
        "win_rate_train_apr1_14",
        "max_dd_train_apr1_14",
        "trades_val_apr15_30",
        "pnl_val_apr15_30",
        "win_rate_val_apr15_30",
        "max_dd_val_apr15_30",
        "trades_val_may1_12",
        "pnl_val_may1_12",
        "win_rate_val_may1_12",
        "max_dd_val_may1_12",
        "val_composite_pnl",
        "train_pass",
        "val_pass_soft",
        "rationale",
    ]
    for col in show_cols:
        if col not in wide:
            wide[col] = np.nan
    report_lines.append(wide[show_cols].head(40).to_string(index=False))
    report_lines.append("")
    report_lines.append("## Guardrails")
    report_lines.append("- Rules are selected only from a predeclared family.")
    report_lines.append("- A rule is not deployable unless it survives external Jan/live websocket validation.")
    report_lines.append("- May/Jan/live weakness should override attractive Apr-only performance.")
    (args.out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    meta = {
        "train": str(args.train),
        "val": str(args.val),
        "may": str(args.may),
        "external": str(args.external) if args.external else None,
        "stress_cents": args.stress_cents,
        "rules": len(rules),
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Wrote {args.out_dir}")
    print(wide[show_cols].head(25).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
