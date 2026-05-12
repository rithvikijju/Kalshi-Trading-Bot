#!/usr/bin/env python3
"""Research NO-side risk throttles without changing the live signal.

The previous fair-value guard search found a useful failure mode but no
deployable hard filter: blocking the recent bad NO trades also removed too much
validated historical edge.  This script keeps the passed signal stream intact
and tests whether risky NO trades should be allowed only at smaller size.

Evaluation sets:
* historical_train/validation/test: corrected historical replay entries
* live_capture_holdout: captured websocket decisions with official settlement
* live_ledger_recent_actual: real fills since the live-capture holdout cutoff

No threshold is fit on live_capture_holdout or live_ledger_recent_actual.  The
live datasets are used only as final promotion gates.
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

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.live_capture_backtest_audit import OfficialResults
from scripts.research_loss_prevention_filters import max_drawdown
from scripts.research_no_fair_value_loop import (
    DEFAULT_BTC,
    DEFAULT_HOLDOUT,
    DEFAULT_HISTORICAL,
    DEFAULT_LIVE_DB,
    latest_capture_time,
    load_capture_holdout,
    load_historical,
    load_live_ledger,
    split_historical,
)
from scripts.risk_adjusted_research import RiskSizingConfig, choose_risk_adjusted_contracts

DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "no_sizing_throttle_research_20260511"


@dataclass(frozen=True)
class ThrottlePolicy:
    name: str
    description: str
    cap_fn: Callable[[pd.Series, int], int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-trades", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--historical-variant", default="baseline_current")
    parser.add_argument("--capture-holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--live-db", type=Path, default=DEFAULT_LIVE_DB)
    parser.add_argument("--btc-1m", type=Path, default=DEFAULT_BTC)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--official-cache", type=Path, default=DEFAULT_OUT / "official_results_cache.json")
    parser.add_argument("--recent-start", default="")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--baseline-max-contracts", type=int, default=5)
    parser.add_argument("--min-validation-pnl-delta", type=float, default=-0.25)
    parser.add_argument("--min-test-pnl-delta", type=float, default=-0.25)
    parser.add_argument("--min-live-capture-pnl-delta", type=float, default=-0.10)
    return parser.parse_args()


def finite(value: object, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def current_live_config(bankroll: float, max_contracts: int) -> RiskSizingConfig:
    return RiskSizingConfig(
        starting_bankroll=bankroll,
        max_contracts=max_contracts,
        no_side_contract_cap=max_contracts,
        kelly_fraction=0.25,
        edge_confidence=0.50,
        medium_entry_cap=0.55,
        high_entry_cap=0.65,
    )


def result_col(frame: pd.DataFrame) -> str:
    for col in ("official_result", "official_or_proxy_settlement", "settlement"):
        if col in frame.columns:
            return col
    raise ValueError("no settlement/result column found")


def visible_qty(row: pd.Series) -> float | None:
    for col in ("available_qty", "visible_side_ask_qty", "side_ask_qty", "ask_qty"):
        value = finite(row.get(col), float("nan"))
        if math.isfinite(value):
            return value
    return None


def unit_premium(entry_price: float, contracts: int) -> tuple[float, float]:
    fee = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
    return entry_price * contracts + fee, fee


def settle_open(open_positions: list[dict], now: pd.Timestamp, cash: float, settled: list[dict]) -> tuple[float, list[dict]]:
    still_open: list[dict] = []
    for pos in open_positions:
        if pd.Timestamp(pos["close_time"]) <= now:
            cash += float(pos["payout"])
            settled.append(pos)
        else:
            still_open.append(pos)
    return cash, still_open


def baseline_contracts_for_row(
    row: pd.Series,
    *,
    cash: float,
    active_exposure: float,
    bankroll: float,
    config: RiskSizingConfig,
    ledger_actual: bool,
) -> int:
    if ledger_actual:
        return max(0, int(finite(row.get("contracts"), 0.0)))
    decision = choose_risk_adjusted_contracts(
        entry_price=finite(row.get("entry_price"), 0.0),
        model_p_yes=finite(row.get("model_p_yes"), 0.5),
        side=str(row.get("side", "")).lower(),
        bankroll=max(0.0, cash + active_exposure),
        available_cash=max(0.0, cash),
        active_exposure=max(0.0, active_exposure),
        config=config,
        available_qty=visible_qty(row),
        liquidity="taker",
        net_edge_cents=finite(row.get("net_edge_cents"), 0.0),
    )
    return int(decision.contracts)


def simulate_policy(
    frame: pd.DataFrame,
    policy: ThrottlePolicy,
    *,
    bankroll: float,
    config: RiskSizingConfig,
    dataset: str,
    ledger_actual: bool = False,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    result_name = result_col(frame)
    rows = frame.copy()
    rows["entry_time"] = pd.to_datetime(rows["entry_time"], utc=True, errors="coerce")
    rows["close_time"] = pd.to_datetime(rows["close_time"], utc=True, errors="coerce")
    rows = rows.dropna(subset=["entry_time", "close_time", "entry_price"]).sort_values(["entry_time", "market_ticker"])

    cash = bankroll
    open_positions: list[dict] = []
    settled: list[dict] = []
    for _, row in rows.iterrows():
        now = pd.Timestamp(row["entry_time"])
        cash, open_positions = settle_open(open_positions, now, cash, settled)
        active = sum(float(pos["premium"]) for pos in open_positions)
        base_contracts = baseline_contracts_for_row(
            row,
            cash=cash,
            active_exposure=active,
            bankroll=bankroll,
            config=config,
            ledger_actual=ledger_actual,
        )
        cap = max(0, int(policy.cap_fn(row, base_contracts)))
        contracts = min(base_contracts, cap)
        if contracts <= 0:
            continue
        entry_price = finite(row.get("entry_price"), 0.0)
        premium, fee = unit_premium(entry_price, contracts)
        if premium > cash + 1e-9 and not ledger_actual:
            continue
        side = str(row.get("side", "")).lower()
        settlement = str(row.get(result_name, "")).lower()
        payout = contracts * (1.0 if side == settlement else 0.0)
        if not ledger_actual:
            cash -= premium
        open_positions.append(
            {
                "dataset": dataset,
                "policy": policy.name,
                "entry_time": row["entry_time"],
                "close_time": row["close_time"],
                "event_ticker": row.get("event_ticker"),
                "market_ticker": row.get("market_ticker"),
                "side": side,
                "settlement": settlement,
                "entry_price": entry_price,
                "model_p_yes": finite(row.get("model_p_yes"), 0.5),
                "side_probability": finite(row.get("side_probability"), float("nan")),
                "net_edge_cents": finite(row.get("net_edge_cents"), float("nan")),
                "ttl_min": finite(row.get("ttl_min"), float("nan")),
                "side_distance_usd": finite(row.get("side_distance_usd"), float("nan")),
                "side_ret_5m": finite(row.get("side_ret_5m"), float("nan")),
                "side_ret_10m": finite(row.get("side_ret_10m"), float("nan")),
                "baseline_contracts": base_contracts,
                "contracts": contracts,
                "contracts_reduced": base_contracts - contracts,
                "fee": fee,
                "premium": premium,
                "payout": payout,
                "pnl": payout - premium,
                "split": row.get("split", dataset),
                "source_id": row.get("id", row.get("source_id", "")),
            }
        )

    for pos in open_positions:
        if not ledger_actual:
            cash += float(pos["payout"])
        settled.append(pos)
    if not settled:
        return pd.DataFrame()
    return pd.DataFrame(settled).sort_values(["close_time", "entry_time", "market_ticker"]).reset_index(drop=True)


def summarize(trades: pd.DataFrame, policy: ThrottlePolicy, dataset: str, baseline: pd.DataFrame, bankroll: float) -> dict:
    pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce") if not trades.empty else pd.Series(dtype=float)
    premium = (
        pd.to_numeric(trades.get("premium", pd.Series(dtype=float)), errors="coerce") if not trades.empty else pd.Series(dtype=float)
    )
    base_pnl = pd.to_numeric(baseline.get("pnl", pd.Series(dtype=float)), errors="coerce") if not baseline.empty else pd.Series(dtype=float)
    base_premium = (
        pd.to_numeric(baseline.get("premium", pd.Series(dtype=float)), errors="coerce") if not baseline.empty else pd.Series(dtype=float)
    )
    return {
        "policy": policy.name,
        "description": policy.description,
        "dataset": dataset,
        "trades": int(len(trades)),
        "contracts": int(trades["contracts"].sum()) if not trades.empty else 0,
        "contracts_reduced": int(trades["contracts_reduced"].sum()) if not trades.empty else 0,
        "premium": float(premium.sum()) if not premium.empty else 0.0,
        "pnl": float(pnl.sum()) if not pnl.empty else 0.0,
        "return_on_100": float(pnl.sum() / bankroll * 100.0) if bankroll else 0.0,
        "return_on_premium": float(pnl.sum() / premium.sum()) if not premium.empty and premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "max_drawdown": max_drawdown(pnl) if len(pnl) else 0.0,
        "baseline_trades": int(len(baseline)),
        "baseline_contracts": int(baseline["contracts"].sum()) if not baseline.empty else 0,
        "baseline_premium": float(base_premium.sum()) if not base_premium.empty else 0.0,
        "baseline_pnl": float(base_pnl.sum()) if not base_pnl.empty else 0.0,
        "pnl_delta_vs_baseline": float(pnl.sum() - base_pnl.sum()) if len(base_pnl) else float(pnl.sum()),
        "drawdown_delta_vs_baseline": float(max_drawdown(pnl) - max_drawdown(base_pnl)) if len(base_pnl) else max_drawdown(pnl),
        "baseline_max_drawdown": max_drawdown(base_pnl) if len(base_pnl) else 0.0,
    }


def no(row: pd.Series) -> bool:
    return str(row.get("side", "")).lower() == "no"


def lt(row: pd.Series, col: str, threshold: float) -> bool:
    value = finite(row.get(col), float("nan"))
    return math.isfinite(value) and value < threshold


def ge(row: pd.Series, col: str, threshold: float) -> bool:
    value = finite(row.get(col), float("nan"))
    return math.isfinite(value) and value >= threshold


def cap_if(predicate: Callable[[pd.Series], bool], cap: int) -> Callable[[pd.Series, int], int]:
    return lambda row, base: min(base, cap) if predicate(row) else base


def all_cap(cap: int) -> Callable[[pd.Series, int], int]:
    return lambda row, base: min(base, cap)


def generate_policies() -> list[ThrottlePolicy]:
    policies: list[ThrottlePolicy] = [
        ThrottlePolicy("baseline_cap5_current", "Current live cap-5 risk sizing.", lambda row, base: base),
        ThrottlePolicy("all_cap3", "Cap every trade at 3 contracts.", all_cap(3)),
        ThrottlePolicy("all_cap2", "Cap every trade at 2 contracts.", all_cap(2)),
        ThrottlePolicy("all_cap1", "Cap every trade at 1 contract.", all_cap(1)),
        ThrottlePolicy("no_all_cap1", "Cap every NO trade at 1 contract.", cap_if(lambda r: no(r), 1)),
        ThrottlePolicy("no_all_cap2", "Cap every NO trade at 2 contracts.", cap_if(lambda r: no(r), 2)),
    ]
    for dist in (35, 50, 75, 100, 115, 130, 150):
        policies.append(
            ThrottlePolicy(
                f"no_dist_lt_{dist}_cap1",
                f"Cap NO at 1 contract when side distance is below ${dist}.",
                cap_if(lambda r, dist=dist: no(r) and lt(r, "side_distance_usd", dist), 1),
            )
        )
        policies.append(
            ThrottlePolicy(
                f"no_dist_lt_{dist}_cap2",
                f"Cap NO at 2 contracts when side distance is below ${dist}.",
                cap_if(lambda r, dist=dist: no(r) and lt(r, "side_distance_usd", dist), 2),
            )
        )
    for prob in (0.72, 0.75, 0.78, 0.80, 0.83, 0.85):
        policies.append(
            ThrottlePolicy(
                f"no_p_lt_{int(prob * 100)}_cap1",
                f"Cap NO at 1 contract when model NO probability is below {prob:.2f}.",
                cap_if(lambda r, prob=prob: no(r) and lt(r, "side_probability", prob), 1),
            )
        )
    for entry in (0.45, 0.50, 0.55, 0.60, 0.65):
        policies.append(
            ThrottlePolicy(
                f"no_entry_ge_{int(entry * 100)}_cap1",
                f"Cap NO at 1 contract when entry price is at least {entry:.2f}.",
                cap_if(lambda r, entry=entry: no(r) and ge(r, "entry_price", entry), 1),
            )
        )
    policies.extend(
        [
            ThrottlePolicy(
                "no_dist_lt100_or_p_lt80_cap1",
                "Cap NO at 1 when side distance < $100 or p_no < 0.80.",
                cap_if(lambda r: no(r) and (lt(r, "side_distance_usd", 100) or lt(r, "side_probability", 0.80)), 1),
            ),
            ThrottlePolicy(
                "no_dist_lt115_or_p_lt83_cap1",
                "Cap NO at 1 when side distance < $115 or p_no < 0.83.",
                cap_if(lambda r: no(r) and (lt(r, "side_distance_usd", 115) or lt(r, "side_probability", 0.83)), 1),
            ),
            ThrottlePolicy(
                "no_ttl_lt8_dist_lt115_cap1",
                "Cap NO at 1 when ttl < 8m and side distance < $115.",
                cap_if(lambda r: no(r) and lt(r, "ttl_min", 8) and lt(r, "side_distance_usd", 115), 1),
            ),
            ThrottlePolicy(
                "no_ttl_lt20_dist_lt100_cap1",
                "Cap NO at 1 when ttl < 20m and side distance < $100.",
                cap_if(lambda r: no(r) and lt(r, "ttl_min", 20) and lt(r, "side_distance_usd", 100), 1),
            ),
            ThrottlePolicy(
                "no_chase10_gt125_dist_lt115_cap1",
                "Cap NO at 1 after a large favorable 10m move when distance remains < $115.",
                cap_if(lambda r: no(r) and ge(r, "side_ret_10m", 125) and lt(r, "side_distance_usd", 115), 1),
            ),
            ThrottlePolicy(
                "no_chase5_gt150_dist_lt115_cap1",
                "Cap NO at 1 after a large favorable 5m move when distance remains < $115.",
                cap_if(lambda r: no(r) and ge(r, "side_ret_5m", 150) and lt(r, "side_distance_usd", 115), 1),
            ),
            ThrottlePolicy(
                "no_close_or_cheap_cap1",
                "Cap NO at 1 when distance < $100 or entry price < 0.55.",
                cap_if(lambda r: no(r) and (lt(r, "side_distance_usd", 100) or lt(r, "entry_price", 0.55)), 1),
            ),
        ]
    )
    return policies


def evaluate(
    datasets: dict[str, pd.DataFrame],
    policies: list[ThrottlePolicy],
    *,
    bankroll: float,
    config: RiskSizingConfig,
    output_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_summaries: list[dict] = []
    all_trades: list[pd.DataFrame] = []
    baselines: dict[str, pd.DataFrame] = {}
    for dataset, frame in datasets.items():
        baseline = simulate_policy(
            frame,
            policies[0],
            bankroll=bankroll,
            config=config,
            dataset=dataset,
            ledger_actual=dataset == "live_ledger_recent_actual",
        )
        baselines[dataset] = baseline
    for policy in policies:
        for dataset, frame in datasets.items():
            trades = simulate_policy(
                frame,
                policy,
                bankroll=bankroll,
                config=config,
                dataset=dataset,
                ledger_actual=dataset == "live_ledger_recent_actual",
            )
            all_summaries.append(summarize(trades, policy, dataset, baselines[dataset], bankroll))
            if not trades.empty:
                all_trades.append(trades)
                if policy.name in {"baseline_cap5_current", "no_all_cap1", "no_dist_lt100_or_p_lt80_cap1"}:
                    trades.to_csv(output_dir / f"{dataset}_{policy.name}_trades.csv", index=False)
    summary = pd.DataFrame(all_summaries)
    trades_long = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return summary, trades_long


def build_gate(summary: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    pivot = summary.pivot_table(
        index=["policy", "description"],
        columns="dataset",
        values=["trades", "contracts", "pnl", "pnl_delta_vs_baseline", "max_drawdown", "baseline_max_drawdown"],
        aggfunc="first",
    )
    pivot.columns = [f"{metric}_{dataset}" for metric, dataset in pivot.columns]
    gate = pivot.reset_index()
    reasons: list[str] = []
    passes: list[bool] = []
    for _, row in gate.iterrows():
        row_reasons: list[str] = []
        if row["policy"] == "baseline_cap5_current":
            row_reasons.append("baseline")
        if finite(row.get("pnl_delta_vs_baseline_historical_validation"), -999) < args.min_validation_pnl_delta:
            row_reasons.append("historical validation worsened too much")
        if finite(row.get("pnl_delta_vs_baseline_historical_test"), -999) < args.min_test_pnl_delta:
            row_reasons.append("historical test worsened too much")
        if finite(row.get("pnl_delta_vs_baseline_live_capture_holdout"), -999) < args.min_live_capture_pnl_delta:
            row_reasons.append("live capture holdout worsened")
        if finite(row.get("pnl_delta_vs_baseline_live_ledger_recent_actual"), 0.0) < 0.0:
            row_reasons.append("recent actual ledger worsened")
        if finite(row.get("contracts_live_ledger_recent_actual"), 0.0) <= 0.0 and row["policy"] != "baseline_cap5_current":
            row_reasons.append("no recent ledger exposure")
        passes.append(not row_reasons)
        reasons.append("; ".join(row_reasons) if row_reasons else "pass")
    gate["risk_gate_pass"] = passes
    gate["reasons"] = reasons
    sort_cols = [
        "risk_gate_pass",
        "pnl_delta_vs_baseline_live_ledger_recent_actual",
        "pnl_delta_vs_baseline_live_capture_holdout",
        "pnl_delta_vs_baseline_historical_validation",
        "pnl_delta_vs_baseline_historical_test",
    ]
    present = [col for col in sort_cols if col in gate.columns]
    return gate.sort_values(present, ascending=[False] + [False] * (len(present) - 1)).reset_index(drop=True)


def run_once(args: argparse.Namespace) -> dict:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    official = OfficialResults(args.official_cache)
    historical = load_historical(args.historical_trades, args.historical_variant, args.btc_1m)
    capture = load_capture_holdout(args.capture_holdout, args.btc_1m)
    recent_start = pd.Timestamp(args.recent_start, tz="UTC") if args.recent_start.strip() else latest_capture_time(capture)
    live_ledger = load_live_ledger(args.live_db, official, args.btc_1m, recent_start)
    official.write()

    datasets = split_historical(historical)
    datasets["live_capture_holdout"] = capture
    datasets["live_ledger_recent_actual"] = live_ledger

    config = current_live_config(args.bankroll, args.baseline_max_contracts)
    policies = generate_policies()
    summary, trades_long = evaluate(datasets, policies, bankroll=args.bankroll, config=config, output_dir=args.output_dir)
    gate = build_gate(summary, args)

    summary.to_csv(args.output_dir / "sizing_throttle_eval_long.csv", index=False)
    gate.to_csv(args.output_dir / "sizing_throttle_gate.csv", index=False)
    trades_long.to_csv(args.output_dir / "sizing_throttle_trades_long.csv", index=False)
    historical.to_csv(args.output_dir / "historical_features.csv", index=False)
    capture.to_csv(args.output_dir / "live_capture_holdout_features.csv", index=False)
    live_ledger.to_csv(args.output_dir / "live_ledger_recent_features.csv", index=False)

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "historical_trades": str(args.historical_trades),
        "historical_variant": args.historical_variant,
        "capture_holdout": str(args.capture_holdout),
        "live_db": str(args.live_db),
        "recent_start": str(recent_start) if recent_start is not None else None,
        "bankroll": args.bankroll,
        "baseline_max_contracts": args.baseline_max_contracts,
        "datasets": {name: int(len(frame)) for name, frame in datasets.items()},
        "policies": len(policies),
        "risk_gate_pass_count": int(gate["risk_gate_pass"].sum()),
        "top": gate.head(20).to_dict("records"),
        "leakage_controls": [
            "Policies use only pre-entry features: side, entry price, model probability, distance, ttl, and prior BTC momentum.",
            "Official settlement labels are used only for PnL scoring.",
            "Historical validation/test and live capture are evaluated separately.",
            "Recent live ledger uses actual filled contracts for the baseline and only caps those fills for candidate policies.",
        ],
    }
    (args.output_dir / "sizing_throttle_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nTOP RISK THROTTLE RESULTS")
    cols = [
        "policy",
        "risk_gate_pass",
        "pnl_delta_vs_baseline_live_ledger_recent_actual",
        "pnl_delta_vs_baseline_live_capture_holdout",
        "pnl_delta_vs_baseline_historical_validation",
        "pnl_delta_vs_baseline_historical_test",
        "contracts_live_ledger_recent_actual",
        "pnl_live_ledger_recent_actual",
        "pnl_historical_validation",
        "pnl_historical_test",
        "reasons",
    ]
    present = [col for col in cols if col in gate.columns]
    with pd.option_context("display.max_rows", 80, "display.width", 260):
        print(gate[present].head(50).to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return report


def main() -> int:
    args = parse_args()
    run_once(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
