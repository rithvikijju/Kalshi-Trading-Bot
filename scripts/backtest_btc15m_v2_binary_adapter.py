#!/usr/bin/env python3
"""Backtest v2-inspired BTC15M binary rules on captured websocket data.

This is a research adapter, not a branch-original replay.  The kalshi_v2
branches were built for cumulative ``-T``/bucket markets and TP/SL exits.  The
current BTC15M capture contains binary up/down markets, so this script adapts
only the fair-value signal idea:

- map each BTC15M market to its Kalshi REST ``floor_strike``;
- compute a causal lognormal P(YES) from decision-time BTC spot and rolling
  realized volatility;
- use executable top-of-book YES/NO asks with visible quantity;
- hold one contract to official REST settlement.

TP/SL/time-exit logic is deliberately excluded until live exit-fill behavior is
validated separately.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "remote_snapshots"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_v2_binary_adapter_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_btc15m_live_holdout import (  # noqa: E402
    fetch_results,
    load_capture_window,
    markdown_or_text,
)
from scripts.backtest_current_live_models_historical import trade_sharpe  # noqa: E402
from scripts.backtest_predexon_orderbooks import max_drawdown_from_pnl  # noqa: E402


MINUTES_PER_YEAR = 60 * 24 * 365


@dataclass(frozen=True)
class V2BinaryVariant:
    name: str
    min_edge_cents: float = 2.5
    no_side_edge_surcharge_cents: float = 0.0
    market_shrink: float = 0.0
    brti_dampening: float = 1.0
    side_policy: str = "both"
    min_entry: float = 0.20
    max_entry: float = 0.80
    max_spread_cents: float = 3.0
    min_visible_qty: float = 1.0
    min_ttl_min: float = 4.0
    max_ttl_min: float = 14.0
    rv_lookback_min: int = 30
    min_vol_points: int = 12


VARIANTS = [
    V2BinaryVariant(name="v2_default_binary_h2s"),
    V2BinaryVariant(
        name="v2_awareness_binary_h2s",
        no_side_edge_surcharge_cents=3.0,
        market_shrink=0.10,
        brti_dampening=0.80,
    ),
    V2BinaryVariant(
        name="v2_awareness_yes_only_h2s",
        market_shrink=0.10,
        brti_dampening=0.80,
        side_policy="yes_only",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--kalshi-sleep-sec", type=float, default=0.0)
    return parser.parse_args()


def latest_capture_db() -> Path | None:
    candidates: list[Path] = []
    for root in [RUNTIME_ROOT, PROJECT_ROOT / "runtime"]:
        if root.exists():
            candidates.extend(root.glob("**/btc15m_raw_*.duckdb"))
            candidates.extend(root.glob("**/btc15m_live_capture*.duckdb"))
    candidates = [path for path in candidates if path.is_file() and not str(path).endswith(".wal")]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def lognormal_p_above(spot: float, strike: float, ttl_min: float, sigma_annualized: float) -> float | None:
    if spot <= 0 or strike <= 0 or ttl_min <= 0 or sigma_annualized <= 0:
        return None
    t_years = ttl_min / MINUTES_PER_YEAR
    if t_years <= 0:
        return None
    denom = sigma_annualized * math.sqrt(t_years)
    if denom <= 0 or not math.isfinite(denom):
        return None
    d2 = (math.log(spot / strike) - 0.5 * sigma_annualized * sigma_annualized * t_years) / denom
    return float(norm_cdf(d2))


def fee_one(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def build_minute_volatility(
    btc: pd.DataFrame,
    *,
    lookback_min: int,
    min_points: int,
) -> pd.DataFrame:
    if btc.empty:
        return pd.DataFrame(columns=["available_ns", f"sigma_{lookback_min}m", f"vol_points_{lookback_min}m"])
    work = btc.dropna(subset=["received_at_ns", "received_at_utc", "price"]).copy()
    work["received_at_utc"] = pd.to_datetime(work["received_at_utc"], utc=True, errors="coerce")
    work["received_at_ns"] = pd.to_numeric(work["received_at_ns"], errors="coerce")
    work["price"] = pd.to_numeric(work["price"], errors="coerce")
    work = work.dropna(subset=["received_at_ns", "received_at_utc", "price"])
    if work.empty:
        return pd.DataFrame(columns=["available_ns", f"sigma_{lookback_min}m", f"vol_points_{lookback_min}m"])
    work["minute"] = work["received_at_utc"].dt.floor("min")
    bars = (
        work.sort_values("received_at_ns")
        .groupby("minute", as_index=False)
        .agg(close=("price", "last"), available_ns=("received_at_ns", "max"))
        .sort_values("available_ns")
        .reset_index(drop=True)
    )
    bars["log_ret"] = np.log(bars["close"] / bars["close"].shift(1))
    rolling = bars["log_ret"].rolling(int(lookback_min), min_periods=int(min_points))
    bars[f"sigma_{lookback_min}m"] = rolling.std() * math.sqrt(MINUTES_PER_YEAR)
    bars[f"vol_points_{lookback_min}m"] = rolling.count()
    return bars[["available_ns", f"sigma_{lookback_min}m", f"vol_points_{lookback_min}m"]]


def attach_causal_volatility(q: pd.DataFrame, btc: pd.DataFrame, variants: list[V2BinaryVariant]) -> pd.DataFrame:
    out = q.sort_values("received_at_ns").copy()
    for lookback in sorted({variant.rv_lookback_min for variant in variants}):
        min_points = min(variant.min_vol_points for variant in variants if variant.rv_lookback_min == lookback)
        vol = build_minute_volatility(btc, lookback_min=lookback, min_points=min_points)
        if vol.empty:
            out[f"sigma_{lookback}m"] = np.nan
            out[f"vol_points_{lookback}m"] = 0.0
            continue
        out = pd.merge_asof(
            out.sort_values("received_at_ns"),
            vol.sort_values("available_ns"),
            left_on="received_at_ns",
            right_on="available_ns",
            direction="backward",
        )
        out = out.drop(columns=["available_ns"], errors="ignore")
    return out.sort_values("received_at_ns").reset_index(drop=True)


def prepare_features(
    top: pd.DataFrame,
    btc: pd.DataFrame,
    results: pd.DataFrame,
    variants: list[V2BinaryVariant],
) -> pd.DataFrame:
    q = top.copy()
    q["received_at_utc"] = pd.to_datetime(q["received_at_utc"], utc=True, errors="coerce")
    q = q.dropna(subset=["received_at_ns", "received_at_utc", "event_ticker", "market_ticker"])
    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask", "yes_bid_qty", "yes_ask_qty", "no_bid_qty", "no_ask_qty", "btc_spot"]:
        q[col] = pd.to_numeric(q[col], errors="coerce")
    q = q[
        q["yes_bid"].between(0.0, 1.0)
        & q["yes_ask"].between(0.0, 1.0)
        & q["no_bid"].between(0.0, 1.0)
        & q["no_ask"].between(0.0, 1.0)
        & q["yes_bid"].le(q["yes_ask"])
        & q["no_bid"].le(q["no_ask"])
        & q["yes_ask_qty"].fillna(0).ge(0)
        & q["no_ask_qty"].fillna(0).ge(0)
        & q["btc_spot"].gt(0)
    ].copy()
    q["yes_mid"] = (q["yes_bid"] + q["yes_ask"]) / 2.0
    q["spread_cents"] = (q["yes_ask"] - q["yes_bid"]).clip(lower=0.0) * 100.0

    if results.empty:
        q["result"] = ""
        q["actual_yes"] = np.nan
        q["close_time"] = pd.NaT
        q["floor_strike"] = np.nan
        q["expiration_value"] = np.nan
    else:
        meta_cols = [
            "event_ticker",
            "market_ticker",
            "result",
            "status",
            "actual_yes",
            "close_time",
            "floor_strike",
            "expiration_value",
        ]
        q = q.merge(results[[col for col in meta_cols if col in results.columns]], on=["event_ticker", "market_ticker"], how="left")
    q["close_time"] = pd.to_datetime(q["close_time"], utc=True, errors="coerce")
    q["floor_strike"] = pd.to_numeric(q["floor_strike"], errors="coerce")
    q["expiration_value"] = pd.to_numeric(q["expiration_value"], errors="coerce")
    q["ttl_min"] = (q["close_time"] - q["received_at_utc"]).dt.total_seconds() / 60.0
    q = attach_causal_volatility(q, btc, variants)
    return q


def pnl_for_trade(side: str, entry: float, actual_yes: bool) -> tuple[float, bool, float, float]:
    entry_fee = fee_one(entry)
    premium = float(entry) + entry_fee
    win = bool(actual_yes) if side == "yes" else not bool(actual_yes)
    pnl = 1.0 - float(entry) - entry_fee if win else -premium
    return pnl, win, premium, entry_fee


def candidate_frame(q: pd.DataFrame, variant: V2BinaryVariant, side: str) -> pd.DataFrame:
    if side not in {"yes", "no"}:
        raise ValueError(f"unsupported side: {side}")
    sigma_col = f"sigma_{variant.rv_lookback_min}m"
    points_col = f"vol_points_{variant.rv_lookback_min}m"
    entry_col = "yes_ask" if side == "yes" else "no_ask"
    qty_col = "yes_ask_qty" if side == "yes" else "no_ask_qty"
    base = q[
        q["actual_yes"].notna()
        & q["floor_strike"].notna()
        & q["ttl_min"].between(variant.min_ttl_min, variant.max_ttl_min)
        & q["spread_cents"].le(variant.max_spread_cents)
        & q[entry_col].between(variant.min_entry, variant.max_entry)
        & q[qty_col].fillna(0).ge(variant.min_visible_qty)
        & q[sigma_col].notna()
        & q[points_col].fillna(0).ge(variant.min_vol_points)
    ].copy()
    if base.empty:
        return base
    if variant.side_policy == "yes_only" and side != "yes":
        return pd.DataFrame()
    if variant.side_policy == "no_only" and side != "no":
        return pd.DataFrame()

    effective_sigma = base[sigma_col].astype(float) * float(variant.brti_dampening)
    p_yes = [
        lognormal_p_above(float(spot), float(strike), float(ttl), float(sigma))
        for spot, strike, ttl, sigma in zip(base["btc_spot"], base["floor_strike"], base["ttl_min"], effective_sigma, strict=False)
    ]
    base["p_yes_raw"] = p_yes
    base = base[base["p_yes_raw"].notna()].copy()
    if base.empty:
        return base
    if variant.market_shrink:
        base["model_p_yes"] = (1.0 - variant.market_shrink) * base["p_yes_raw"].astype(float) + variant.market_shrink * base["yes_mid"].astype(float)
    else:
        base["model_p_yes"] = base["p_yes_raw"].astype(float)
    base["side"] = side
    base["entry_price"] = base[entry_col].astype(float)
    base["visible_qty"] = base[qty_col].astype(float)
    base["entry_fee"] = base["entry_price"].map(fee_one)
    if side == "yes":
        base["side_fair_p"] = base["model_p_yes"].astype(float)
    else:
        base["side_fair_p"] = 1.0 - base["model_p_yes"].astype(float)
    base["gross_edge_cents"] = (base["side_fair_p"] - base["entry_price"]) * 100.0
    surcharge = variant.no_side_edge_surcharge_cents if side == "no" else 0.0
    base["net_edge_cents"] = base["gross_edge_cents"] - base["entry_fee"] * 100.0
    base["edge_threshold_cents"] = variant.min_edge_cents + surcharge
    base = base[base["net_edge_cents"].ge(base["edge_threshold_cents"])].copy()
    if base.empty:
        return base
    pnl_rows = [
        pnl_for_trade(side, float(entry), bool(actual_yes))
        for entry, actual_yes in zip(base["entry_price"], base["actual_yes"], strict=False)
    ]
    base["pnl"] = [row[0] for row in pnl_rows]
    base["win"] = [row[1] for row in pnl_rows]
    base["premium"] = [row[2] for row in pnl_rows]
    base["entry_fee"] = [row[3] for row in pnl_rows]
    base["strategy"] = variant.name
    base["variant"] = variant.name
    base["brti_dampening"] = variant.brti_dampening
    base["market_shrink"] = variant.market_shrink
    base["no_side_edge_surcharge_cents"] = variant.no_side_edge_surcharge_cents
    base["side_policy"] = variant.side_policy
    base["sigma_annualized"] = base[sigma_col]
    base["vol_points"] = base[points_col]
    return base


def score_variant(q: pd.DataFrame, variant: V2BinaryVariant) -> pd.DataFrame:
    frames = [candidate_frame(q, variant, side) for side in ("yes", "no")]
    candidates = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if any(not frame.empty for frame in frames) else pd.DataFrame()
    if candidates.empty:
        return candidates
    return (
        candidates.sort_values(["event_ticker", "received_at_ns", "net_edge_cents"], ascending=[True, True, False])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def summarize_trades(strategy: str, trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "strategy": strategy,
            "trades": 0,
            "events": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
            "avg_entry": 0.0,
            "yes_trades": 0,
            "no_trades": 0,
            "first_entry": "",
            "last_entry": "",
            "research_status": "no_trades",
        }
    ordered = trades.sort_values(["received_at_utc", "event_ticker"]).copy()
    pnl = ordered["pnl"].astype(float)
    premium = ordered["premium"].astype(float)
    return {
        "strategy": strategy,
        "trades": int(len(ordered)),
        "events": int(ordered["event_ticker"].nunique()),
        "pnl": float(pnl.sum()),
        "premium": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "max_dd": max_drawdown_from_pnl(pnl),
        "sharpe": trade_sharpe(pnl),
        "avg_entry": float(ordered["entry_price"].mean()),
        "yes_trades": int(ordered["side"].eq("yes").sum()),
        "no_trades": int(ordered["side"].eq("no").sum()),
        "first_entry": str(ordered["received_at_utc"].min()),
        "last_entry": str(ordered["received_at_utc"].max()),
        "research_status": "research_only_sample_small" if len(ordered) < 50 else "research_needs_robustness_gate",
    }


TRADE_COLUMNS = [
    "strategy",
    "received_at_ns",
    "received_at_utc",
    "event_ticker",
    "market_ticker",
    "side",
    "entry_price",
    "entry_fee",
    "premium",
    "visible_qty",
    "spread_cents",
    "btc_spot",
    "floor_strike",
    "expiration_value",
    "close_time",
    "ttl_min",
    "sigma_annualized",
    "vol_points",
    "p_yes_raw",
    "model_p_yes",
    "side_fair_p",
    "gross_edge_cents",
    "net_edge_cents",
    "edge_threshold_cents",
    "result",
    "actual_yes",
    "win",
    "pnl",
    "brti_dampening",
    "market_shrink",
    "no_side_edge_surcharge_cents",
    "side_policy",
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    capture_db = args.capture_db or latest_capture_db()
    if capture_db is None:
        raise FileNotFoundError("No BTC15M capture DuckDB found; pass --capture-db explicitly.")
    top, btc, start, end, info = load_capture_window(capture_db, args.hours, args.start, args.end)
    events = sorted(top["event_ticker"].dropna().astype(str).unique().tolist())
    print(f"loaded top rows={len(top):,} btc ticks={len(btc):,} events={len(events):,} window={start} -> {end}", flush=True)
    results = fetch_results(events, sleep_sec=args.kalshi_sleep_sec)
    q = prepare_features(top, btc, results, VARIANTS)
    q.to_parquet(args.output_dir / "features.parquet", index=False, compression="zstd")

    trades_by_variant = {variant.name: score_variant(q, variant) for variant in VARIANTS}
    summary = pd.DataFrame([summarize_trades(name, trades) for name, trades in trades_by_variant.items()])
    all_trades = pd.concat([trades for trades in trades_by_variant.values() if not trades.empty], ignore_index=True) if any(not trades.empty for trades in trades_by_variant.values()) else pd.DataFrame()
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    if not all_trades.empty:
        all_trades[[col for col in TRADE_COLUMNS if col in all_trades.columns]].to_csv(args.output_dir / "trades.csv", index=False)
    else:
        (args.output_dir / "trades.csv").write_text("", encoding="utf-8")

    data_report = {
        **info,
        "capture_db": str(capture_db),
        "top_rows": int(len(top)),
        "btc_ticks": int(len(btc)),
        "events_in_window": int(len(events)),
        "settlement_rows": int(len(results)),
        "finalized_result_rows": int(results["result"].astype(str).str.lower().isin(["yes", "no"]).sum()) if not results.empty else 0,
        "feature_rows": int(len(q)),
        "variant_specs": [asdict(variant) for variant in VARIANTS],
        "research_limitations": [
            "Research adapter only; not a branch-original kalshi_v2 replay.",
            "Holds to official REST settlement; TP/SL/time-exit branch logic intentionally excluded.",
            "Uses captured BTC spot/coinbase_ticker stream; synthetic sidecar Coinbase provenance remains a promotion blocker.",
            "Sample-size and robustness gates still apply before any forward-test or deployment discussion.",
        ],
    }
    (args.output_dir / "data_report.json").write_text(json.dumps(data_report, indent=2, default=str), encoding="utf-8")
    report = [
        "# BTC15M v2 Binary Adapter Backtest",
        "",
        f"Generated UTC: `{datetime.now(timezone.utc).isoformat()}`",
        f"Capture DB: `{capture_db}`",
        f"Window: `{start}` -> `{end}`",
        "",
        "## Summary",
        "",
        markdown_or_text(summary),
        "",
        "## Interpretation",
        "",
        "- Research-only adapter: not original kalshi_v2 deployment evidence.",
        "- Official REST settlement and one-contract taker fees are included.",
        "- Execution uses decision-time executable asks and visible top quantity.",
        "- TP/SL/time-exit behavior remains out of scope.",
    ]
    (args.output_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.output_dir), "summary_rows": int(len(summary)), "trade_rows": int(len(all_trades))}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
