#!/usr/bin/env python3
"""Score core fair-value variants on observed live decisions and fills.

This is a strict observed-decision gate.  It does not invent new entries and it
does not retime trades.  For each captured/filled decision, it recomputes a
candidate p(YES) using only BTC data available at the decision timestamp and
asks whether that candidate would have allowed the same executable trade.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.core_model_decision_holdout import btc_at_or_before, load_btc, strike_from_ticker
from scripts.core_model_research import (
    MAX_ENTRY,
    MAX_NO_P,
    MAX_SPREAD_CENTS,
    MIN_EDGE_CENTS,
    MIN_ENTRY,
    MIN_YES_P,
    ModelVariant,
    build_event_cache,
    build_variants,
    edge_uncertainty_cents,
    model_probabilities,
    stats_for_trades,
)

DEFAULT_CAPTURE = PROJECT_ROOT / "backtest_outputs" / "loss_prevention_research_20260510" / "holdout_late_only_features.csv"
DEFAULT_LIVE_LEDGER = PROJECT_ROOT / "backtest_outputs" / "no_fair_value_research_20260511" / "live_ledger_recent_features.csv"
DEFAULT_BTC = PROJECT_ROOT / "data" / "btc_1m_research_live_cache.parquet"
DEFAULT_SCORECARD = PROJECT_ROOT / "backtest_outputs" / "core_model_research_20260510_calibrated" / "core_model_scorecard.csv"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "core_model_observed_gate_20260511"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-holdout", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--live-ledger", type=Path, default=DEFAULT_LIVE_LEDGER)
    parser.add_argument("--btc", type=Path, default=DEFAULT_BTC)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--top-historical", type=int, default=18)
    return parser.parse_args()


def finite(value: object, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def normalize_capture(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    df["dataset"] = "capture_holdout"
    df["entry_time"] = pd.to_datetime(df.get("entry_time", df.get("received_at_utc")), utc=True, errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
    df["side"] = df["side"].astype(str).str.lower()
    if "btc_spot" not in df and "entry_spot" in df:
        df["btc_spot"] = df["entry_spot"]
    if "official_result" not in df and "settlement" in df:
        df["official_result"] = df["settlement"]
    if "pnl_1c" not in df and "pnl" in df:
        df["pnl_1c"] = df["pnl"]
    if "pnl_actual" not in df:
        df["pnl_actual"] = df["pnl_1c"]
    if "contracts" not in df:
        df["contracts"] = 1
    if "capture" not in df:
        df["capture"] = "capture_holdout"
    return df


def normalize_ledger(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    df["dataset"] = "live_ledger_recent"
    df["capture"] = "research_live_ledger_recent"
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
    df["side"] = df["side"].astype(str).str.lower()
    df["btc_spot"] = pd.to_numeric(df.get("entry_spot", df.get("btc_spot")), errors="coerce")
    df["pnl_1c"] = pd.to_numeric(df.get("pnl", np.nan), errors="coerce")
    df["pnl_actual"] = pd.to_numeric(df.get("pnl_actual", df.get("pnl", np.nan)), errors="coerce")
    return df


def selected_variants(scorecard_path: Path, top_n: int) -> list[ModelVariant]:
    variants = {variant.name: variant for variant in build_variants(3)}
    names = ["baseline_emp70_logn_rv60"]
    if scorecard_path.exists():
        score = pd.read_csv(scorecard_path)
        score = score[score.get("selection_ok", False).astype(str).str.lower().isin(["true", "1"])].copy()
        if "variant" in score:
            names.extend(score["variant"].astype(str).head(top_n).tolist())
    names.extend(
        [
            "rv_down60_blend",
            "brti_065",
            "student_t3_rv60",
            "blend_85_15_rv60",
            "lower_uncertainty",
            "vol_scale_085",
            "rv_up60_blend",
        ]
    )
    out: list[ModelVariant] = []
    seen: set[str] = set()
    for name in names:
        variant = variants.get(name)
        if variant and not variant.calibrator and name not in seen:
            out.append(variant)
            seen.add(name)
    return out


def score_variant_on_observed_rows(variant: ModelVariant, rows: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    out_rows: list[dict] = []
    cache: dict[tuple[str, int], dict] = {}
    for _, row in rows.iterrows():
        entry_time = pd.Timestamp(row["entry_time"])
        close_time = pd.Timestamp(row["close_time"])
        if pd.isna(entry_time) or pd.isna(close_time):
            continue
        event_open = close_time - pd.Timedelta(hours=1)
        event_ticker = str(row.get("event_ticker", ""))
        cache_key = (event_ticker, variant.train_days)
        emp_cache = cache.get(cache_key)
        if emp_cache is None:
            emp_cache = build_event_cache(btc, event_open, variant.train_days)
            if emp_cache is None:
                continue
            cache[cache_key] = emp_cache
        spot = finite(row.get("btc_spot", row.get("entry_spot")), float("nan"))
        _, btc_idx = btc_at_or_before(btc, entry_time)
        if not math.isfinite(spot) or btc_idx is None:
            continue
        strike = finite(row.get("strike"), float("nan"))
        if not math.isfinite(strike):
            strike = strike_from_ticker(str(row["market_ticker"]))
        if not math.isfinite(strike):
            continue
        market_mid = finite(row.get("market_mid"), float("nan"))
        yes_bid_close = market_mid if math.isfinite(market_mid) else np.nan
        yes_ask_close = market_mid if math.isfinite(market_mid) else np.nan
        q = pd.DataFrame(
            [
                {
                    "event_ticker": event_ticker,
                    "available_at": entry_time,
                    "floor_strike": strike,
                    "yes_bid_close": yes_bid_close,
                    "yes_ask_close": yes_ask_close,
                    "yes_ask_exe": np.nan,
                    "no_ask_exe": np.nan,
                }
            ]
        )
        ttl_min = finite(row.get("ttl_min"), (close_time - entry_time).total_seconds() / 60.0)
        p_yes = float(model_probabilities(variant, q, btc, btc_idx, spot, ttl_min, emp_cache, {})[0])
        side = str(row["side"]).lower()
        entry = finite(row.get("entry_price"), float("nan"))
        if not math.isfinite(entry):
            continue
        fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
        if side == "yes":
            gross_edge = (p_yes - entry) * 100.0 + variant.side_bias_cents
            strong = p_yes >= variant.min_yes_p
            p_side = p_yes
        else:
            gross_edge = ((1.0 - p_yes) - entry) * 100.0 - variant.side_bias_cents
            strong = p_yes <= variant.max_no_p
            p_side = 1.0 - p_yes
        net_edge = gross_edge - fee * 100.0
        threshold = variant.min_edge_cents + float(edge_uncertainty_cents(np.asarray([p_yes]), emp_cache, variant.uncertainty_mult)[0])
        spread = finite(row.get("spread_cents"), 1.0)
        passed = (
            strong
            and net_edge >= threshold
            and MIN_ENTRY <= entry <= MAX_ENTRY
            and spread <= MAX_SPREAD_CENTS
        )
        official_result = str(row.get("official_result", row.get("settlement", ""))).lower()
        pnl_1c = finite(row.get("pnl_1c", row.get("pnl")), float("nan"))
        if not math.isfinite(pnl_1c) and official_result in {"yes", "no"}:
            pnl_1c = (1.0 if side == official_result else 0.0) - entry - fee
        contracts = int(max(1, finite(row.get("contracts"), 1.0)))
        pnl_actual = finite(row.get("pnl_actual"), pnl_1c * contracts)
        out_rows.append(
            {
                **row.to_dict(),
                "variant": variant.name,
                "variant_p_yes": p_yes,
                "variant_p_side": p_side,
                "variant_net_edge_cents": net_edge,
                "variant_threshold_cents": threshold,
                "variant_pass": bool(passed),
                "pnl_1c": pnl_1c,
                "pnl_actual": pnl_actual,
                "entry_fee": fee,
            }
        )
    return pd.DataFrame(out_rows)


def dd(values: pd.Series) -> float:
    pnl = pd.to_numeric(values, errors="coerce").dropna()
    if pnl.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def summarize(scored: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    if scored.empty:
        return pd.DataFrame()
    for variant, vg in scored.groupby("variant", sort=True):
        slices = {
            "capture_all": vg["dataset"].eq("capture_holdout"),
            "capture_live_only": vg["dataset"].eq("capture_holdout") & vg["capture"].eq("research_live_capture"),
            "capture_passed": vg["dataset"].eq("capture_holdout") & vg["variant_pass"],
            "capture_live_passed": vg["dataset"].eq("capture_holdout") & vg["capture"].eq("research_live_capture") & vg["variant_pass"],
            "ledger_all_actual": vg["dataset"].eq("live_ledger_recent"),
            "ledger_passed_actual": vg["dataset"].eq("live_ledger_recent") & vg["variant_pass"],
        }
        for label, mask in slices.items():
            g = vg[mask].copy()
            pnl_col = "pnl_actual" if "actual" in label else "pnl_1c"
            pnl = pd.to_numeric(g.get(pnl_col, pd.Series(dtype=float)), errors="coerce") if not g.empty else pd.Series(dtype=float)
            premium = pd.to_numeric(g.get("premium", g.get("premium_1c", g.get("entry_price", pd.Series(dtype=float)))), errors="coerce") if not g.empty else pd.Series(dtype=float)
            rows.append(
                {
                    "variant": variant,
                    "slice": label,
                    "trades": int(len(g)),
                    "contracts": int(pd.to_numeric(g.get("contracts", 1), errors="coerce").fillna(1).sum()) if not g.empty else 0,
                    "pnl": float(pnl.sum()) if len(pnl) else 0.0,
                    "premium": float(premium.sum()) if len(premium) else 0.0,
                    "return_on_premium": float(pnl.sum() / premium.sum()) if len(pnl) and len(premium) and premium.sum() else 0.0,
                    "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
                    "max_drawdown": dd(pnl),
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    capture = normalize_capture(args.capture_holdout)
    ledger = normalize_ledger(args.live_ledger)
    rows = pd.concat([capture, ledger], ignore_index=True, sort=False)
    rows = rows.dropna(subset=["entry_time", "close_time", "market_ticker", "side", "entry_price"]).copy()
    btc = load_btc(args.btc)
    variants = selected_variants(args.scorecard, args.top_historical)
    frames = [score_variant_on_observed_rows(variant, rows, btc) for variant in variants]
    scored = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    summary = summarize(scored)
    scored.to_csv(args.output_dir / "observed_decision_gate_rows.csv", index=False)
    summary.to_csv(args.output_dir / "observed_decision_gate_summary.csv", index=False)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "capture_holdout": str(args.capture_holdout),
        "live_ledger": str(args.live_ledger),
        "btc": str(args.btc),
        "scorecard": str(args.scorecard),
        "rows": int(len(rows)),
        "variants": [asdict(v) for v in variants],
        "limitations": [
            "Observed-decision gate only; does not add or retime entries.",
            "Official results are used for labels only.",
            "BTC features are looked up at or before entry_time.",
            "Market-weight variants use captured market_mid when present; ledger rows generally lack market_mid.",
        ],
    }
    (args.output_dir / "observed_decision_gate_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pd.option_context("display.max_rows", 120, "display.width", 240):
        print(summary.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
