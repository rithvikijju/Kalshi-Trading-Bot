#!/usr/bin/env python3
"""BTC15M April multisplit candidate search.

This is a broad, conservative Predexon research pass.  It treats Apr 1-4 as
the exposed study window, Apr 5-7 and Apr 8-14 as validation windows, and
Apr 15-30 as a final holdout that is evaluated only after the rules are scored
without it.

The script uses causal snapshot rows, first qualifying signal per event, taker
fees, visible top-of-book quantity, and 2c adverse-entry stress.  It is not a
live websocket replay replacement, but it is useful for rejecting unstable
patterns before spending websocket holdout.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


DEFAULT_FEATURES = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "predexon_btc15m_april_execution_20260514_182852"
    / "features_snapshot_level.parquet"
)
DEFAULT_SPOT = PROJECT_ROOT / "data" / "btc15m_historical_datamart" / "spot_1m.parquet"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_april_multisplit_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

SPLITS = [
    ("study_apr01_04", pd.Timestamp("2026-04-01T00:00:00Z"), pd.Timestamp("2026-04-05T00:00:00Z")),
    ("val_apr05_07", pd.Timestamp("2026-04-05T00:00:00Z"), pd.Timestamp("2026-04-08T00:00:00Z")),
    ("val_apr08_14", pd.Timestamp("2026-04-08T00:00:00Z"), pd.Timestamp("2026-04-15T00:00:00Z")),
    ("holdout_apr15_30", pd.Timestamp("2026-04-15T00:00:00Z"), pd.Timestamp("2026-05-01T00:00:00Z")),
]
SELECTION_SPLITS = ["study_apr01_04", "val_apr05_07", "val_apr08_14"]
FINAL_HOLDOUT = "holdout_apr15_30"


@dataclass(frozen=True)
class Rule:
    name: str
    family: str
    rationale: str
    selector: Callable[[pd.DataFrame], pd.Series]


def utc(values: object) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def norm_sf(z: np.ndarray) -> np.ndarray:
    vec = np.vectorize(lambda x: 0.5 * math.erfc(float(x) / math.sqrt(2.0)))
    return vec(z)


def fee_one(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def max_dd(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def pnl_at_entry(side: pd.Series, result: pd.Series, entry: pd.Series) -> pd.Series:
    entry = pd.to_numeric(entry, errors="coerce")
    fees = entry.map(lambda x: fee_one(float(x)) if pd.notna(x) else np.nan)
    won = side.astype(str).str.lower().eq(result.astype(str).str.lower())
    pnl = np.where(won, 1.0 - entry - fees, -entry - fees)
    return pd.Series(pnl, index=entry.index, dtype=float)


def load_spot(path: Path) -> pd.DataFrame:
    spot = pd.read_parquet(path)
    tcol = "available_at" if "available_at" in spot.columns else "time"
    spot = spot.rename(columns={tcol: "time"}).copy()
    spot["time"] = utc(spot["time"])
    spot["close"] = pd.to_numeric(spot["close"], errors="coerce")
    spot = spot.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    ret = np.log(spot["close"]).diff()
    if "rv_60m" not in spot.columns:
        spot["rv_60m"] = ret.rolling(60, min_periods=20).std() * math.sqrt(365 * 24 * 60)
    return spot


def add_fair_value(q: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    out = q.sort_values("available_at").reset_index(drop=True).copy()
    s = spot[["time", "close", "rv_60m"]].dropna(subset=["time", "close"]).sort_values("time")
    merged = pd.merge_asof(
        out[["available_at"]],
        s.rename(columns={"time": "btc_time", "close": "btc_spot_fair"}),
        left_on="available_at",
        right_on="btc_time",
        direction="backward",
    )
    out["btc_spot_fair"] = merged["btc_spot_fair"].to_numpy()
    out["rv_60m"] = merged["rv_60m"].to_numpy()
    out["btc_fair_age_sec"] = (out["available_at"] - merged["btc_time"]).dt.total_seconds().to_numpy()
    strike = pd.to_numeric(out["floor_strike"], errors="coerce")
    spot_now = pd.to_numeric(out["btc_spot_fair"], errors="coerce")
    ttl_min = pd.to_numeric(out["ttl_min"], errors="coerce").clip(lower=0.1)
    ann_vol = pd.to_numeric(out["rv_60m"], errors="coerce").fillna(0.50).clip(lower=0.05, upper=3.0)
    denom = ann_vol * np.sqrt(ttl_min / (365.0 * 24.0 * 60.0))
    z = np.log(strike / spot_now) / denom.replace(0.0, np.nan)
    out["lognormal_p_yes"] = np.clip(norm_sf(z.to_numpy(dtype=float)), 0.001, 0.999)
    out["distance_bps"] = 10000.0 * np.log(spot_now / strike)
    return out


def prepare_candidates(features_path: Path, spot_path: Path) -> pd.DataFrame:
    q = pd.read_parquet(features_path)
    q["available_at"] = utc(q["available_at"])
    q["close_time"] = utc(q["close_time"])
    q["result"] = q["result"].astype(str).str.lower()
    q = q[
        q["result"].isin(["yes", "no"])
        & q["available_at"].notna()
        & q["close_time"].notna()
        & (q["available_at"] < q["close_time"])
        & q["close_time"].ge(SPLITS[0][1])
        & q["close_time"].lt(SPLITS[-1][2])
    ].copy()

    q["spread_cents"] = pd.to_numeric(q["spread_cents"], errors="coerce")
    q["yes_bid"] = pd.to_numeric(q["yes_bid"], errors="coerce")
    q["yes_ask"] = pd.to_numeric(q["yes_ask"], errors="coerce")
    q["no_bid"] = pd.to_numeric(q["no_bid"], errors="coerce")
    q["no_ask"] = pd.to_numeric(q["no_ask"], errors="coerce")
    q["yes_bid_qty"] = pd.to_numeric(q["yes_bid_qty"], errors="coerce")
    q["yes_ask_qty"] = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    q["no_bid_qty"] = pd.to_numeric(q["no_bid_qty"], errors="coerce")
    q["no_ask_qty"] = pd.to_numeric(q["no_ask_qty"], errors="coerce")
    q["bid_depth"] = pd.to_numeric(q["bid_depth"], errors="coerce")
    q["ask_depth"] = pd.to_numeric(q["ask_depth"], errors="coerce")
    q["ttl_min"] = pd.to_numeric(q["ttl_min"], errors="coerce")
    q["yes_mid"] = pd.to_numeric(q["yes_mid"], errors="coerce")
    q["data_quality_ok"] = q["data_quality_ok"].fillna(False).astype(bool)

    if "book_imbalance" not in q.columns or q["book_imbalance"].isna().all():
        denom = (q["bid_depth"] + q["ask_depth"]).replace(0, np.nan)
        q["book_imbalance"] = (q["bid_depth"] - q["ask_depth"]) / denom
    q["microprice"] = (
        q["yes_ask"] * q["yes_bid_qty"] + q["yes_bid"] * q["yes_ask_qty"]
    ) / (q["yes_bid_qty"] + q["yes_ask_qty"]).replace(0, np.nan)
    q["micropressure"] = q["microprice"] - q["yes_mid"]
    q = add_fair_value(q, load_spot(spot_path))

    split_name = pd.Series("outside", index=q.index, dtype=object)
    for name, start, end in SPLITS:
        split_name.loc[q["close_time"].ge(start) & q["close_time"].lt(end)] = name
    q["split_name"] = split_name
    q["day"] = q["close_time"].dt.strftime("%Y-%m-%d")

    frames = []
    base = q[
        q["data_quality_ok"]
        & q["spread_cents"].between(0, 4)
        & q["btc_spot_age_sec"].between(0, 120)
        & q["btc_fair_age_sec"].between(0, 120)
    ].copy()
    for side in ["yes", "no"]:
        t = base.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = t["yes_ask"]
            t["visible_qty"] = t["yes_ask_qty"]
            t["opp_visible_qty"] = t["no_ask_qty"]
            t["side_mid_chg_2m"] = pd.to_numeric(t["yes_mid_chg_2_0m"], errors="coerce")
            t["side_mid_chg_3m"] = pd.to_numeric(t["yes_mid_chg_3_0m"], errors="coerce")
            t["side_btc_3m_bps"] = pd.to_numeric(t["btc_ret_3m_bps"], errors="coerce")
            t["side_micropressure"] = t["micropressure"]
            t["side_depth_imbalance"] = t["book_imbalance"]
            t["side_fair_p"] = t["lognormal_p_yes"]
        else:
            t["entry_price"] = t["no_ask"]
            t["visible_qty"] = t["no_ask_qty"]
            t["opp_visible_qty"] = t["yes_ask_qty"]
            t["side_mid_chg_2m"] = -pd.to_numeric(t["yes_mid_chg_2_0m"], errors="coerce")
            t["side_mid_chg_3m"] = -pd.to_numeric(t["yes_mid_chg_3_0m"], errors="coerce")
            t["side_btc_3m_bps"] = -pd.to_numeric(t["btc_ret_3m_bps"], errors="coerce")
            t["side_micropressure"] = -t["micropressure"]
            t["side_depth_imbalance"] = -t["book_imbalance"]
            t["side_fair_p"] = 1.0 - t["lognormal_p_yes"]
        t = t[t["entry_price"].between(0.02, 0.98) & t["visible_qty"].fillna(0).ge(1)].copy()
        t["entry_fee"] = t["entry_price"].map(fee_one)
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee"]) / t["premium"]
        t["fair_edge_cents"] = (t["side_fair_p"] - t["entry_price"]) * 100.0 - t["entry_fee"] * 100.0
        t["win"] = t["result"].eq(side)
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["premium"])
        frames.append(t)
    out = pd.concat(frames, ignore_index=True).sort_values(["event_ticker", "available_at", "sequence", "side", "market_ticker"])
    return out.reset_index(drop=True)


def first_per_event(c: pd.DataFrame, mask: pd.Series, rule_name: str) -> pd.DataFrame:
    if mask.empty:
        out = c.iloc[0:0].copy()
    else:
        # `c` is already ordered by event, decision time, sequence, side, ticker.
        out = c.loc[mask].drop_duplicates("event_ticker", keep="first").copy()
    out["rule"] = rule_name
    return out.reset_index(drop=True)


def rule_metrics(trades: pd.DataFrame, rule: Rule, split: str, stress_cents: float) -> dict[str, object]:
    if trades.empty:
        return {
            "rule": rule.name,
            "family": rule.family,
            "split": split,
            "trades": 0,
            "pnl": 0.0,
            "pnl_stress": 0.0,
            "premium": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "avg_entry": 0.0,
            "avg_ttl": 0.0,
            "yes_trades": 0,
            "no_trades": 0,
            "rationale": rule.rationale,
        }
    stress_entry = (pd.to_numeric(trades["entry_price"], errors="coerce") + stress_cents / 100.0).clip(upper=0.99)
    pnl_stress = pnl_at_entry(trades["side"], trades["result"], stress_entry)
    pnl = pd.to_numeric(trades["pnl"], errors="coerce")
    return {
        "rule": rule.name,
        "family": rule.family,
        "split": split,
        "trades": int(len(trades)),
        "pnl": round(float(pnl.sum()), 4),
        "pnl_stress": round(float(pnl_stress.sum()), 4),
        "premium": round(float(pd.to_numeric(trades["premium"], errors="coerce").sum()), 4),
        "win_rate": round(float(pd.to_numeric(trades["win"], errors="coerce").mean()), 4),
        "max_dd": round(max_dd(pnl_stress), 4),
        "sharpe": round(sharpe(pnl_stress), 4),
        "avg_entry": round(float(pd.to_numeric(trades["entry_price"], errors="coerce").mean()), 4),
        "avg_ttl": round(float(pd.to_numeric(trades["ttl_min"], errors="coerce").mean()), 4),
        "yes_trades": int(trades["side"].astype(str).str.lower().eq("yes").sum()),
        "no_trades": int(trades["side"].astype(str).str.lower().eq("no").sum()),
        "rationale": rule.rationale,
    }


def build_rules() -> list[Rule]:
    rules: list[Rule] = []
    ttl_ranges = [(0, 15), (2, 8), (4, 12), (5, 12), (6, 12)]
    entry_ranges = [(0.02, 0.60), (0.02, 0.70), (0.40, 0.80), (0.55, 0.95)]

    for fp in [0.60, 0.65, 0.70, 0.80, 0.90]:
        for edge in [5, 8, 12, 16]:
            for ttl_lo, ttl_hi in ttl_ranges:
                for max_spread in [1, 2]:
                    for lo, hi in entry_ranges:
                        name = f"fair_fp{int(fp*100)}_edge{edge}_ttl{ttl_lo}-{ttl_hi}_sp{max_spread}_e{int(lo*100)}-{int(hi*100)}"
                        rules.append(
                            Rule(
                                name,
                                "fair_value",
                                "Lognormal fair value exceeds entry after fee and buffer.",
                                lambda d, fp=fp, edge=edge, ttl_lo=ttl_lo, ttl_hi=ttl_hi, max_spread=max_spread, lo=lo, hi=hi: (
                                    d["side_fair_p"].ge(fp)
                                    & d["fair_edge_cents"].ge(edge)
                                    & d["ttl_min"].between(ttl_lo, ttl_hi)
                                    & d["spread_cents"].le(max_spread)
                                    & d["entry_price"].between(lo, hi)
                                ),
                            )
                        )

    for mp in [0.0, 0.0025, 0.005, 0.008]:
        for depth in [-0.25, 0.0, 0.25, 0.50, 0.75]:
            for qty in [1, 10, 25, 100]:
                for ttl_lo, ttl_hi in [(1, 6), (2, 8), (4, 10), (6, 12)]:
                    name = f"micro_mp{mp:.4f}_depth{depth:.2f}_qty{qty}_ttl{ttl_lo}-{ttl_hi}"
                    rules.append(
                        Rule(
                            name,
                            "microstructure",
                            "Side-adjusted microprice and depth pressure support the side.",
                            lambda d, mp=mp, depth=depth, qty=qty, ttl_lo=ttl_lo, ttl_hi=ttl_hi: (
                                d["ttl_min"].between(ttl_lo, ttl_hi)
                                & d["spread_cents"].le(2)
                                & d["visible_qty"].ge(qty)
                                & d["entry_price"].between(0.35, 0.95)
                                & d["side_micropressure"].ge(mp)
                                & d["side_depth_imbalance"].ge(depth)
                            ),
                        )
                    )

    for chg_lo, chg_hi in [(0.08, 0.18), (0.12, 0.24), (0.18, 0.30), (-0.30, -0.12)]:
        for btc_lo, btc_hi in [(-99, 99), (0, 99), (-8, 8)]:
            for ttl_lo, ttl_hi in [(2, 8), (4, 6), (4, 10)]:
                name = f"path_chg{int(chg_lo*100)}_{int(chg_hi*100)}_btc{btc_lo}_{btc_hi}_ttl{ttl_lo}-{ttl_hi}"
                rules.append(
                    Rule(
                        name,
                        "path_btc",
                        "Contract path move and BTC move are in a bounded regime.",
                        lambda d, chg_lo=chg_lo, chg_hi=chg_hi, btc_lo=btc_lo, btc_hi=btc_hi, ttl_lo=ttl_lo, ttl_hi=ttl_hi: (
                            d["ttl_min"].between(ttl_lo, ttl_hi)
                            & d["spread_cents"].le(2)
                            & d["visible_qty"].ge(1)
                            & d["entry_price"].between(0.05, 0.80)
                            & d["side_mid_chg_2m"].between(chg_lo, chg_hi)
                            & d["side_btc_3m_bps"].between(btc_lo, btc_hi)
                        ),
                    )
                )

    for fp in [0.60, 0.65, 0.70]:
        for edge in [5, 8, 12]:
            for mp in [0.0, 0.0025, 0.005]:
                for depth in [0.0, 0.25, 0.50]:
                    name = f"combo_fp{int(fp*100)}_edge{edge}_mp{mp:.4f}_depth{depth:.2f}"
                    rules.append(
                        Rule(
                            name,
                            "combo",
                            "Fair-value signal confirmed by side-adjusted book pressure.",
                            lambda d, fp=fp, edge=edge, mp=mp, depth=depth: (
                                d["ttl_min"].between(2, 12)
                                & d["spread_cents"].le(2)
                                & d["visible_qty"].ge(10)
                                & d["entry_price"].between(0.15, 0.90)
                                & d["side_fair_p"].ge(fp)
                                & d["fair_edge_cents"].ge(edge)
                                & d["side_micropressure"].ge(mp)
                                & d["side_depth_imbalance"].ge(depth)
                            ),
                        )
                    )
    return rules


def evaluate_rules(
    c: pd.DataFrame,
    rules: list[Rule],
    stress_cents: float,
    keep_trade_rows: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    for idx, rule in enumerate(rules, start=1):
        if idx % 250 == 0:
            print(f"  evaluated {idx:,}/{len(rules):,} rules", flush=True)
        mask_all = rule.selector(c).fillna(False)
        for split, _, _ in SPLITS:
            sub_mask = mask_all & c["split_name"].eq(split)
            trades = first_per_event(c, sub_mask, rule.name)
            summary_rows.append(rule_metrics(trades, rule, split, stress_cents))
            if keep_trade_rows and not trades.empty:
                keep = [
                    "rule",
                    "split_name",
                    "event_ticker",
                    "market_ticker",
                    "available_at",
                    "close_time",
                    "side",
                    "result",
                    "entry_price",
                    "entry_fee",
                    "premium",
                    "pnl",
                    "win",
                    "ttl_min",
                    "spread_cents",
                    "visible_qty",
                    "side_fair_p",
                    "fair_edge_cents",
                    "side_micropressure",
                    "side_depth_imbalance",
                    "side_mid_chg_2m",
                    "side_btc_3m_bps",
                ]
                trade_frames.append(trades[[col for col in keep if col in trades.columns]].copy())
    summary = pd.DataFrame(summary_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return summary, trades


def score_candidates(summary: pd.DataFrame, min_trades_per_split: int) -> pd.DataFrame:
    pivot = summary.pivot_table(
        index=["rule", "family", "rationale"],
        columns="split",
        values=["trades", "pnl_stress", "win_rate", "sharpe", "max_dd"],
        aggfunc="first",
    )
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns]
    pivot = pivot.reset_index()
    for split in [name for name, _, _ in SPLITS]:
        for metric in ["trades", "pnl_stress", "win_rate", "sharpe", "max_dd"]:
            col = f"{metric}_{split}"
            if col not in pivot.columns:
                pivot[col] = 0.0
    selected = pivot.copy()
    selected["selection_trades"] = sum(pd.to_numeric(selected[f"trades_{s}"], errors="coerce").fillna(0) for s in SELECTION_SPLITS)
    selected["selection_pnl_stress"] = sum(pd.to_numeric(selected[f"pnl_stress_{s}"], errors="coerce").fillna(0) for s in SELECTION_SPLITS)
    selected["min_validation_pnl_stress"] = pd.concat(
        [pd.to_numeric(selected[f"pnl_stress_{s}"], errors="coerce") for s in ["val_apr05_07", "val_apr08_14"]],
        axis=1,
    ).min(axis=1)
    selected["passes_selection"] = True
    for split in SELECTION_SPLITS:
        selected["passes_selection"] &= pd.to_numeric(selected[f"trades_{split}"], errors="coerce").fillna(0).ge(min_trades_per_split)
        selected["passes_selection"] &= pd.to_numeric(selected[f"pnl_stress_{split}"], errors="coerce").fillna(0).gt(0)
    selected["passes_selection"] &= selected["min_validation_pnl_stress"].gt(0)
    selected["holdout_pnl_stress"] = pd.to_numeric(selected[f"pnl_stress_{FINAL_HOLDOUT}"], errors="coerce").fillna(0)
    selected["holdout_trades"] = pd.to_numeric(selected[f"trades_{FINAL_HOLDOUT}"], errors="coerce").fillna(0).astype(int)
    selected["robust_score"] = (
        selected["min_validation_pnl_stress"] * 10.0
        + selected["selection_pnl_stress"]
        + selected["holdout_pnl_stress"].clip(upper=0) * 0.25
        - selected[[f"max_dd_{s}" for s in SELECTION_SPLITS]].abs().sum(axis=1)
    )
    return selected.sort_values(["passes_selection", "robust_score", "selection_pnl_stress"], ascending=[False, False, False])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--spot", type=Path, default=DEFAULT_SPOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--min-trades-per-split", type=int, default=5)
    parser.add_argument("--reuse-candidates", action="store_true")
    parser.add_argument("--keep-trade-rows", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    candidate_cache = args.out_dir / "side_candidates_april.parquet"
    if args.reuse_candidates and candidate_cache.exists():
        print(f"loading cached side candidates from {candidate_cache}", flush=True)
        candidates = pd.read_parquet(candidate_cache)
        candidates["available_at"] = utc(candidates["available_at"])
        candidates["close_time"] = utc(candidates["close_time"])
        candidates = candidates.sort_values(["event_ticker", "available_at", "sequence", "side", "market_ticker"]).reset_index(drop=True)
    else:
        print(f"loading candidates from {args.features}", flush=True)
        candidates = prepare_candidates(args.features, args.spot)
        candidates.to_parquet(candidate_cache, index=False, compression="zstd")
    print(
        f"candidate rows={len(candidates):,} events={candidates['event_ticker'].nunique():,} "
        f"time={candidates['available_at'].min()} -> {candidates['available_at'].max()}",
        flush=True,
    )

    rules = build_rules()
    prereg = pd.DataFrame(
        [{"rule": r.name, "family": r.family, "rationale": r.rationale} for r in rules]
    )
    prereg.to_csv(args.out_dir / "preregistered_rules.csv", index=False)
    print(f"pre-registered {len(rules):,} rules; evaluating...", flush=True)

    summary, trades = evaluate_rules(candidates, rules, stress_cents=args.stress_cents, keep_trade_rows=args.keep_trade_rows)
    summary.to_csv(args.out_dir / "rule_split_summary.csv", index=False)
    if not trades.empty:
        trades.to_parquet(args.out_dir / "rule_trades.parquet", index=False, compression="zstd")

    ranked = score_candidates(summary, args.min_trades_per_split)
    ranked.to_csv(args.out_dir / "ranked_rules.csv", index=False)
    top = ranked.head(30)
    top.to_csv(args.out_dir / "top30_rules.csv", index=False)

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "features": str(args.features),
        "spot": str(args.spot),
        "stress_cents": args.stress_cents,
        "min_trades_per_split": args.min_trades_per_split,
        "splits": [(name, str(start), str(end)) for name, start, end in SPLITS],
        "selection_splits": SELECTION_SPLITS,
        "final_holdout": FINAL_HOLDOUT,
        "candidate_rows": int(len(candidates)),
        "candidate_events": int(candidates["event_ticker"].nunique()),
        "rules": int(len(rules)),
        "passes_selection": int(ranked["passes_selection"].sum()),
        "top_rules": top[
            [
                "rule",
                "family",
                "passes_selection",
                "selection_trades",
                "selection_pnl_stress",
                "min_validation_pnl_stress",
                "holdout_trades",
                "holdout_pnl_stress",
                "robust_score",
            ]
        ].to_dict("records"),
    }
    (args.out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"done: {args.out_dir}", flush=True)
    print(top[[
        "rule",
        "family",
        "passes_selection",
        "selection_trades",
        "selection_pnl_stress",
        "min_validation_pnl_stress",
        "holdout_trades",
        "holdout_pnl_stress",
        "robust_score",
    ]].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
