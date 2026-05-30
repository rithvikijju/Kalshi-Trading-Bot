#!/usr/bin/env python3
"""Score April-trained BTC15M tabular models on live websocket capture.

This is a promotion-gate diagnostic for the April LightGBM/XGBoost candidates.
It reconstructs causal top-of-book side candidates from our local websocket
capture and applies the frozen validation gates from the April training run.

The replay is still top-of-book only. Features requiring full depth that were
not captured are left as NaN and handled by the model's training imputer.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    DEFAULT_CAPTURE_DB,
    add_proxy_results,
    load_capture,
    metadata_from_lifecycle,
    prepare_quotes,
)
from scripts.train_btc15m_april_top_models import metrics as train_metrics  # noqa: E402
from scripts.train_btc15m_april_top_models import prepare_features  # noqa: E402


DEFAULT_MODEL_DIR = PROJECT_ROOT / "backtest_outputs" / "btc15m_april_top_models_20260514_clean"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_ml_live_ws_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def fee_array(price: pd.Series | np.ndarray) -> np.ndarray:
    return np.asarray([kalshi_fee_dollars(float(x), contracts=1, liquidity="taker") for x in np.asarray(price, dtype=float)])


def max_drawdown(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if x.empty:
        return 0.0
    return float((x - x.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def add_asof_change(
    df: pd.DataFrame,
    group_col: str,
    value_col: str,
    lb_min: float,
    out_col: str,
) -> pd.DataFrame:
    out = df.copy()
    out[out_col] = np.nan
    age_col = f"lookback_age_{str(lb_min).replace('.', '_')}m_sec"
    out[age_col] = np.nan
    offset_ns = int(float(lb_min) * 60 * 1_000_000_000)
    for _, idx in out.groupby(group_col, sort=False).groups.items():
        group = out.loc[idx, ["received_at_ns", value_col]].sort_values("received_at_ns").copy()
        left = group[["received_at_ns", value_col]].copy()
        left["_idx"] = group.index.to_numpy()
        left["target_ns"] = left["received_at_ns"] - offset_ns
        right = group[["received_at_ns", value_col]].rename(
            columns={"received_at_ns": "past_ns", value_col: "past_value"}
        )
        merged = pd.merge_asof(
            left.sort_values("target_ns"),
            right.sort_values("past_ns"),
            left_on="target_ns",
            right_on="past_ns",
            direction="backward",
        )
        target_idx = merged["_idx"].to_numpy()
        out.loc[target_idx, out_col] = (
            merged[value_col].to_numpy(dtype=float) - merged["past_value"].to_numpy(dtype=float)
        )
        out.loc[target_idx, age_col] = (
            merged["received_at_ns"].to_numpy(dtype=np.int64) - merged["past_ns"].to_numpy(dtype=float)
        ) / 1_000_000_000.0
    return out


def add_btc_returns(q: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    out = q.sort_values("received_at_ns").copy()
    b = btc.dropna(subset=["received_at_ns", "price"]).copy()
    b["price"] = pd.to_numeric(b["price"], errors="coerce")
    b = b.dropna(subset=["price"]).sort_values("received_at_ns")
    for lb in [1, 3, 5]:
        left = out[["received_at_ns"]].copy()
        left["target_ns"] = left["received_at_ns"] - int(lb * 60 * 1_000_000_000)
        now_px = pd.merge_asof(
            out[["received_at_ns"]].sort_values("received_at_ns"),
            b[["received_at_ns", "price"]].rename(columns={"price": "btc_now"}),
            on="received_at_ns",
            direction="backward",
        )
        past = pd.merge_asof(
            left.sort_values("target_ns"),
            b[["received_at_ns", "price"]].rename(columns={"received_at_ns": "btc_past_ns", "price": "btc_past"}),
            left_on="target_ns",
            right_on="btc_past_ns",
            direction="backward",
        ).sort_index()
        out[f"btc_ret_{lb}m_bps"] = 10000.0 * np.log(
            pd.to_numeric(now_px["btc_now"], errors="coerce").to_numpy(dtype=float)
            / pd.to_numeric(past["btc_past"], errors="coerce").to_numpy(dtype=float)
        )
        out[f"btc_lookback_age_{lb}m_sec"] = (
            out["received_at_ns"].to_numpy(dtype=np.int64) - past["btc_past_ns"].to_numpy(dtype=float)
        ) / 1_000_000_000.0
    return out


def build_side_candidates(q: pd.DataFrame, btc: pd.DataFrame, capture_end: pd.Timestamp, btc_model: str) -> pd.DataFrame:
    q = q.copy()
    q = q[q["close_time"].le(capture_end)].copy()
    q = add_proxy_results(q, btc, btc_model)
    q = q[q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
    if q.empty:
        return pd.DataFrame()
    q["yes_mid"] = (pd.to_numeric(q["yes_bid"], errors="coerce") + pd.to_numeric(q["yes_ask"], errors="coerce")) / 2.0
    q["best_bid_cents"] = pd.to_numeric(q["yes_bid"], errors="coerce") * 100.0
    q["best_ask_cents"] = pd.to_numeric(q["yes_ask"], errors="coerce") * 100.0
    q["yes_bid_qty"] = pd.to_numeric(q["yes_bid_qty"], errors="coerce")
    q["yes_ask_qty"] = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    q["no_bid_qty"] = pd.to_numeric(q["no_bid_qty"], errors="coerce")
    q["no_ask_qty"] = pd.to_numeric(q["no_ask_qty"], errors="coerce")
    denom = (q["yes_bid_qty"] + q["yes_ask_qty"]).replace(0, np.nan)
    q["microprice"] = (q["yes_ask"] * q["yes_bid_qty"] + q["yes_bid"] * q["yes_ask_qty"]) / denom
    q["micropressure"] = q["microprice"] - q["yes_mid"]
    q["book_imbalance"] = (q["yes_bid_qty"] - q["yes_ask_qty"]) / (q["yes_bid_qty"] + q["yes_ask_qty"]).replace(0, np.nan)
    q["distance_usd"] = pd.to_numeric(q["floor_strike"], errors="coerce") - pd.to_numeric(q["btc_spot_model"], errors="coerce")
    q["distance_bps"] = 10000.0 * q["distance_usd"] / pd.to_numeric(q["btc_spot_model"], errors="coerce")
    q = add_btc_returns(q, btc)
    for lb, name in [(0.5, "0_5"), (1.0, "1_0"), (2.0, "2_0"), (3.0, "3_0"), (5.0, "5_0")]:
        q = add_asof_change(q, "market_ticker", "yes_mid", lb, f"yes_mid_chg_{name}m")
    q = add_asof_change(q, "market_ticker", "spread_cents", 1.0, "spread_chg_1_0m")
    q = q.rename(columns={"lookback_age_1_0m_sec": "spread_lookback_age_1_0m_sec"})
    # Recreate the 1m yes-mid age that the spread rename consumed.
    q = add_asof_change(q, "market_ticker", "yes_mid", 1.0, "yes_mid_chg_1_0m")

    frames: list[pd.DataFrame] = []
    for side in ["yes", "no"]:
        t = q.copy()
        t["side"] = side
        if side == "yes":
            t["entry_price"] = pd.to_numeric(t["yes_ask"], errors="coerce")
            t["visible_qty"] = pd.to_numeric(t["yes_ask_qty"], errors="coerce")
            t["opp_visible_qty"] = pd.to_numeric(t["no_ask_qty"], errors="coerce")
            t["side_fair_p"] = pd.to_numeric(t["lognormal_p_yes"], errors="coerce")
            t["side_distance_bps"] = pd.to_numeric(t["distance_bps"], errors="coerce")
            t["side_micropressure"] = pd.to_numeric(t["micropressure"], errors="coerce")
            for lb in [1, 3, 5]:
                t[f"side_btc_{lb}m_bps"] = t[f"btc_ret_{lb}m_bps"]
            for src, dst in [
                ("yes_mid_chg_0_5m", "side_mid_chg_05m"),
                ("yes_mid_chg_1_0m", "side_mid_chg_1m"),
                ("yes_mid_chg_2_0m", "side_mid_chg_2m"),
                ("yes_mid_chg_3_0m", "side_mid_chg_3m"),
            ]:
                t[dst] = t[src]
        else:
            t["entry_price"] = pd.to_numeric(t["no_ask"], errors="coerce")
            t["visible_qty"] = pd.to_numeric(t["no_ask_qty"], errors="coerce")
            t["opp_visible_qty"] = pd.to_numeric(t["yes_ask_qty"], errors="coerce")
            t["side_fair_p"] = 1.0 - pd.to_numeric(t["lognormal_p_yes"], errors="coerce")
            t["side_distance_bps"] = -pd.to_numeric(t["distance_bps"], errors="coerce")
            t["side_micropressure"] = -pd.to_numeric(t["micropressure"], errors="coerce")
            for lb in [1, 3, 5]:
                t[f"side_btc_{lb}m_bps"] = -t[f"btc_ret_{lb}m_bps"]
            for src, dst in [
                ("yes_mid_chg_0_5m", "side_mid_chg_05m"),
                ("yes_mid_chg_1_0m", "side_mid_chg_1m"),
                ("yes_mid_chg_2_0m", "side_mid_chg_2m"),
                ("yes_mid_chg_3_0m", "side_mid_chg_3m"),
            ]:
                t[dst] = -t[src]
        t["entry_fee"] = fee_array(t["entry_price"])
        t["premium"] = t["entry_price"] + t["entry_fee"]
        t["rr"] = (1.0 - t["entry_price"] - t["entry_fee"]) / t["premium"].replace(0, np.nan)
        t["fair_edge_cents"] = (t["side_fair_p"] - t["entry_price"]) * 100.0 - t["entry_fee"] * 100.0
        t["fee_edge"] = t["side_fair_p"] - t["entry_price"] - t["entry_fee"]
        t["visible_ratio"] = t["visible_qty"] / (t["visible_qty"] + t["opp_visible_qty"]).replace(0, np.nan)
        t["side_depth_imbalance"] = (t["visible_qty"] - t["opp_visible_qty"]) / (t["visible_qty"] + t["opp_visible_qty"]).replace(0, np.nan)
        t["rv_15m"] = np.nan
        t["rv_ratio_15_60"] = np.nan
        t["bid_depth"] = np.nan
        t["ask_depth"] = np.nan
        t["quote_speed_cents"] = pd.to_numeric(t["yes_mid_chg_0_5m"], errors="coerce").abs() * 100.0
        t["win"] = t["proxy_result"].astype(str).str.lower().eq(side)
        t["pnl"] = np.where(t["win"], 1.0 - t["entry_price"] - t["entry_fee"], -t["entry_price"] - t["entry_fee"])
        frames.append(t)
    c = pd.concat(frames, ignore_index=True)
    c["available_at"] = c["received_at_utc"]
    c["data_quality_ok"] = True
    mask = (
        c["available_at"].lt(c["close_time"])
        & c["spread_cents"].le(2)
        & c["visible_qty"].ge(1)
        & c["ttl_min"].between(0, 15)
        & c["entry_price"].between(0.01, 0.99)
        & c["btc_spot_age_sec"].between(0, 90)
    )
    return c[mask].sort_values(["event_ticker", "received_at_ns", "side", "market_ticker"]).reset_index(drop=True)


def load_gate(model_dir: Path, model_name: str) -> tuple[float, float]:
    summary = pd.read_csv(model_dir / "model_summary.csv")
    row = summary[(summary["model"].eq(model_name)) & (summary["split"].eq("validation")) & (summary["pnl_model"].eq("pnl"))]
    if row.empty:
        raise ValueError(f"no validation gate found for {model_name}")
    return float(row.iloc[0]["gate_min_p"]), float(row.iloc[0]["gate_min_ev"])


def first_per_event(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return (
        df.sort_values(["event_ticker", "received_at_ns", "pred_ev", "pred_win_prob"], ascending=[True, True, False, False])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def add_two_cent_stress(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    entry = (pd.to_numeric(out["entry_price"], errors="coerce") + 0.02).clip(upper=0.99)
    entry_fee = fee_array(entry)
    win = out["win"].astype(bool).to_numpy()
    out["pnl_2c"] = np.where(win, 1.0 - entry - entry_fee, -entry - entry_fee)
    return out


def add_official_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    if "official_result" not in out.columns:
        out["official_result"] = np.nan
    has_official = out["official_result"].notna()
    official_win = out["official_result"].astype(str).str.lower().eq(out["side"].astype(str).str.lower())
    entry = pd.to_numeric(out["entry_price"], errors="coerce")
    fee = pd.to_numeric(out["entry_fee"], errors="coerce")
    out["official_win"] = np.where(has_official, official_win, np.nan)
    out["pnl_official"] = np.where(has_official, np.where(official_win, 1.0 - entry - fee, -entry - fee), np.nan)
    entry_2c = (entry + 0.02).clip(upper=0.99)
    fee_2c = fee_array(entry_2c)
    out["pnl_official_2c"] = np.where(
        has_official,
        np.where(official_win, 1.0 - entry_2c - fee_2c, -entry_2c - fee_2c),
        np.nan,
    )
    return out


def summarize(trades: pd.DataFrame, model_name: str, gate_p: float, gate_ev: float, pnl_col: str) -> dict[str, Any]:
    m = train_metrics(trades, pnl_col)
    return {"model": model_name, "gate_min_p": gate_p, "gate_min_ev": gate_ev, "pnl_col": pnl_col, **m}


def summarize_official_subset(trades: pd.DataFrame, model_name: str, gate_p: float, gate_ev: float, pnl_col: str) -> dict[str, Any]:
    official = trades[trades["official_result"].notna()].copy() if "official_result" in trades.columns else trades.iloc[0:0].copy()
    if not official.empty:
        official["win"] = official["official_win"].astype(float)
    row = summarize(official, model_name, gate_p, gate_ev, pnl_col)
    row["official_subset"] = True
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--btc-model", choices=["spot", "rolling60"], default="spot")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--models", default="xgboost_tabular,lightgbm_tabular")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    top, btc, lifecycle, decisions, info = load_capture(args.capture_db, args.start, args.end)
    meta, official = metadata_from_lifecycle(lifecycle)
    q = prepare_quotes(top, btc, meta, official, args.btc_model)
    capture_end = pd.Timestamp(info["capture_end_utc"])
    candidates = build_side_candidates(q, btc, capture_end, args.btc_model)
    if candidates.empty:
        raise SystemExit("no candidates after causal websocket feature reconstruction")
    candidates, existing_features = prepare_features(candidates)

    summary_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    for model_name in [m.strip() for m in args.models.split(",") if m.strip()]:
        model_path = args.model_dir / f"{model_name}.pkl"
        with model_path.open("rb") as f:
            payload = pickle.load(f)
        model = payload["model"]
        feature_cols = list(payload["features"])
        for col in feature_cols:
            if col not in candidates.columns:
                candidates[col] = np.nan
        scored = candidates.copy()
        scored["model"] = model_name
        scored["pred_win_prob"] = model.predict_proba(scored[feature_cols])[:, 1].astype(float)
        entry = pd.to_numeric(scored["entry_price"], errors="coerce")
        fee = pd.to_numeric(scored["entry_fee"], errors="coerce")
        scored["pred_ev"] = scored["pred_win_prob"] * (1.0 - entry - fee) + (1.0 - scored["pred_win_prob"]) * (-entry - fee)
        gate_p, gate_ev = load_gate(args.model_dir, model_name)
        trades = first_per_event(scored[(scored["pred_win_prob"] >= gate_p) & (scored["pred_ev"] >= gate_ev)]).copy()
        trades = add_two_cent_stress(trades)
        trades = add_official_pnl(trades)
        trades["gate_min_p"] = gate_p
        trades["gate_min_ev"] = gate_ev
        trade_frames.append(trades)
        for row in [
            summarize(trades, model_name, gate_p, gate_ev, "pnl"),
            summarize(trades, model_name, gate_p, gate_ev, "pnl_2c"),
        ]:
            row["official_subset"] = False
            summary_rows.append(row)
        summary_rows.append(summarize_official_subset(trades, model_name, gate_p, gate_ev, "pnl_official"))
        summary_rows.append(summarize_official_subset(trades, model_name, gate_p, gate_ev, "pnl_official_2c"))

    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    run_info = {
        **info,
        "models": args.models,
        "model_dir": str(args.model_dir),
        "btc_model": args.btc_model,
        "candidate_rows": int(len(candidates)),
        "candidate_events": int(candidates["event_ticker"].nunique()),
        "feature_columns_available_after_prepare": existing_features,
        "feature_reconstruction_note": "top-of-book websocket only; missing full-depth fields are NaN and imputed by model pipelines",
        "label_note": "pnl/pnl_2c use proxy close labels; pnl_official/pnl_official_2c rows summarize only trades with Kalshi official_result populated in lifecycle capture.",
    }
    all_trades.to_parquet(args.out / "ml_live_ws_trades.parquet", index=False, compression="zstd")
    summary.to_csv(args.out / "ml_live_ws_summary.csv", index=False)
    (args.out / "run_info.json").write_text(json.dumps(run_info, indent=2, default=str), encoding="utf-8")
    print(json.dumps(run_info, indent=2, default=str))
    print(summary.to_string(index=False))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
