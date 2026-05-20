#!/usr/bin/env python3
"""Search BTC15M rules that survive REST-official live replay.

This is an audit/research script, not a live trader.  It searches only on
historical Predexon side-candidate files, then evaluates screened rules once on
the locally captured websocket stream with REST official Kalshi results.

The intent is to avoid the failure mode where Coinbase/Kraken close proxies
make near-strike trades look profitable while Kalshi's official result flips
the PnL.  All filters are causal at decision time.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_btc15m_yes_rescue_search import (  # noqa: E402
    PRED_FILES,
    metrics,
    rest_official_results,
    stressed_pnl,
)
from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    DEFAULT_CAPTURE_DB,
    add_proxy_results,
    fee_array,
    load_capture,
    metadata_from_lifecycle,
    prepare_quotes,
)


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_official_robust_grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Rule:
    name: str
    side_mode: str
    fair_p_min: float
    edge_min: float
    ttl_min: float
    ttl_max: float
    entry_max: float
    qty_min: float
    aligned_dist_min_bps: float | None = None
    quote_speed_min: float | None = None
    rv_min: float | None = None
    rv_max: float | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Search BTC15M official-settlement-robust rules.")
    p.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--rest-sleep", type=float, default=0.02)
    p.add_argument("--min-pred-trades", type=int, default=50)
    p.add_argument("--min-pred-sharpe", type=float, default=1.5)
    p.add_argument("--min-live-official-trades", type=int, default=15)
    p.add_argument("--max-screened", type=int, default=120)
    p.add_argument("--side-modes", default="yes", help="Comma-separated side modes to search: yes,no,both")
    return p.parse_args()


def load_pred_candidates() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    keep = [
        "market_ticker",
        "event_ticker",
        "available_at",
        "timestamp_utc",
        "sequence",
        "side",
        "entry_price",
        "visible_qty",
        "side_fair_p",
        "entry_fee",
        "premium",
        "fair_edge_cents",
        "ttl_min",
        "spread_cents",
        "quote_speed_cents",
        "rv_60m",
        "distance_bps",
        "data_quality_ok",
        "prev_gap_sec",
        "next_gap_sec",
        "result",
    ]
    for window, path in PRED_FILES.items():
        if not path.exists():
            continue
        loaded = pd.read_parquet(path)
        df = loaded[[c for c in keep if c in loaded.columns]].copy()
        for col in keep:
            if col not in df:
                df[col] = np.nan
        df = df[keep].copy()
        df["window"] = window
        df["available_at"] = pd.to_datetime(df["available_at"].fillna(df["timestamp_utc"]), utc=True, errors="coerce")
        df["side"] = df["side"].astype(str).str.lower()
        df["result"] = df["result"].astype(str).str.lower()
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=keep + ["window"])
    out = pd.concat(frames, ignore_index=True)
    out = out[
        out["result"].isin(["yes", "no"])
        & out["side"].isin(["yes", "no"])
        & pd.to_numeric(out["ttl_min"], errors="coerce").between(4.0, 13.0)
        & pd.to_numeric(out["spread_cents"], errors="coerce").le(2.0)
        & pd.to_numeric(out["entry_price"], errors="coerce").between(0.02, 0.65)
        & pd.to_numeric(out["side_fair_p"], errors="coerce").ge(0.55)
        & pd.to_numeric(out["fair_edge_cents"], errors="coerce").ge(8.0)
        & pd.to_numeric(out["visible_qty"], errors="coerce").ge(50.0)
    ].copy()
    if "data_quality_ok" in out:
        out = out[out["data_quality_ok"].fillna(True).astype(bool)].copy()
    if "prev_gap_sec" in out and "next_gap_sec" in out:
        out = out[
            pd.to_numeric(out["prev_gap_sec"], errors="coerce").fillna(0).le(120)
            & pd.to_numeric(out["next_gap_sec"], errors="coerce").fillna(0).le(120)
        ].copy()
    return out.reset_index(drop=True)


def load_live_candidates(capture_db: Path, start: str | None, end: str | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    top, btc, lifecycle, _decisions, info = load_capture(capture_db, start, end)
    meta, official_results = metadata_from_lifecycle(lifecycle)
    q = prepare_quotes(top, btc, meta, official_results, "spot")
    q = add_proxy_results(q, btc, "spot")
    capture_end = pd.Timestamp(info["capture_end_utc"])
    q = q[
        q["close_time"].le(capture_end)
        & q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])
        & pd.to_numeric(q["ttl_min"], errors="coerce").between(4.0, 13.0)
        & pd.to_numeric(q["spread_cents"], errors="coerce").le(2.0)
    ].copy()
    if q.empty:
        return pd.DataFrame(), info

    p_yes = pd.to_numeric(q["lognormal_p_yes"], errors="coerce")
    yes_entry = pd.to_numeric(q["yes_ask"], errors="coerce")
    no_entry = pd.to_numeric(q["no_ask"], errors="coerce")
    yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce")
    yes_fee = pd.Series(fee_array(yes_entry), index=q.index)
    no_fee = pd.Series(fee_array(no_entry), index=q.index)
    no_p = 1.0 - p_yes
    yes_edge = (p_yes - yes_entry) * 100.0 - yes_fee * 100.0
    no_edge = (no_p - no_entry) * 100.0 - no_fee * 100.0
    distance_bps = 10000.0 * np.log(
        pd.to_numeric(q["btc_spot_model"], errors="coerce")
        / pd.to_numeric(q["floor_strike"], errors="coerce")
    )

    base_cols = [
        "received_at_utc",
        "event_ticker",
        "market_ticker",
        "seq",
        "spread_cents",
        "ttl_min",
        "quote_speed_cents",
        "rv_60m",
        "proxy_result",
        "official_result",
    ]
    frames: list[pd.DataFrame] = []
    yes_mask = (
        p_yes.ge(0.55)
        & yes_edge.ge(8.0)
        & yes_entry.between(0.02, 0.65)
        & yes_qty.ge(50.0)
    )
    if yes_mask.any():
        y = q.loc[yes_mask, base_cols].copy()
        y["side"] = "yes"
        y["entry_price"] = yes_entry.loc[yes_mask].to_numpy()
        y["visible_qty"] = yes_qty.loc[yes_mask].to_numpy()
        y["side_fair_p"] = p_yes.loc[yes_mask].to_numpy()
        y["entry_fee"] = yes_fee.loc[yes_mask].to_numpy()
        y["premium"] = y["entry_price"] + y["entry_fee"]
        y["fair_edge_cents"] = yes_edge.loc[yes_mask].to_numpy()
        y["distance_bps"] = distance_bps.loc[yes_mask].to_numpy()
        frames.append(y)
    no_mask = (
        no_p.ge(0.55)
        & no_edge.ge(8.0)
        & no_entry.between(0.02, 0.65)
        & no_qty.ge(50.0)
    )
    if no_mask.any():
        n = q.loc[no_mask, base_cols].copy()
        n["side"] = "no"
        n["entry_price"] = no_entry.loc[no_mask].to_numpy()
        n["visible_qty"] = no_qty.loc[no_mask].to_numpy()
        n["side_fair_p"] = no_p.loc[no_mask].to_numpy()
        n["entry_fee"] = no_fee.loc[no_mask].to_numpy()
        n["premium"] = n["entry_price"] + n["entry_fee"]
        n["fair_edge_cents"] = no_edge.loc[no_mask].to_numpy()
        n["distance_bps"] = distance_bps.loc[no_mask].to_numpy()
        frames.append(n)
    if not frames:
        return pd.DataFrame(), info
    c = pd.concat(frames, ignore_index=True)
    c["available_at"] = pd.to_datetime(c["received_at_utc"], utc=True, errors="coerce")
    c["result"] = c["proxy_result"].astype(str).str.lower()
    c["window"] = "live_ws"
    return c.reset_index(drop=True), info


def aligned_distance(df: pd.DataFrame) -> pd.Series:
    d = pd.to_numeric(df["distance_bps"], errors="coerce")
    side = df["side"].astype(str).str.lower()
    return d.where(side.eq("yes"), -d)


def apply_rule(df: pd.DataFrame, rule: Rule, result_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    side = df["side"].astype(str).str.lower()
    if rule.side_mode == "yes":
        side_mask = side.eq("yes")
    elif rule.side_mode == "no":
        side_mask = side.eq("no")
    else:
        side_mask = side.isin(["yes", "no"])
    mask = (
        side_mask
        & pd.to_numeric(df["side_fair_p"], errors="coerce").ge(rule.fair_p_min)
        & pd.to_numeric(df["fair_edge_cents"], errors="coerce").ge(rule.edge_min)
        & pd.to_numeric(df["ttl_min"], errors="coerce").between(rule.ttl_min, rule.ttl_max)
        & pd.to_numeric(df["entry_price"], errors="coerce").between(0.02, rule.entry_max)
        & pd.to_numeric(df["visible_qty"], errors="coerce").ge(rule.qty_min)
        & pd.to_numeric(df["spread_cents"], errors="coerce").le(2.0)
    )
    if rule.aligned_dist_min_bps is not None:
        mask &= aligned_distance(df).ge(rule.aligned_dist_min_bps)
    if rule.quote_speed_min is not None:
        mask &= pd.to_numeric(df["quote_speed_cents"], errors="coerce").ge(rule.quote_speed_min)
    if rule.rv_min is not None:
        mask &= pd.to_numeric(df["rv_60m"], errors="coerce").ge(rule.rv_min)
    if rule.rv_max is not None:
        mask &= pd.to_numeric(df["rv_60m"], errors="coerce").le(rule.rv_max)

    order = ["event_ticker", "available_at", "fair_edge_cents"]
    extra = "sequence" if "sequence" in df.columns else "seq" if "seq" in df.columns else None
    if extra:
        order.append(extra)
    hit = df.loc[mask].sort_values(order, ascending=[True, True, False] + [True] * (len(order) - 3)).copy()
    hit = hit.drop_duplicates("event_ticker", keep="first").reset_index(drop=True)
    return stressed_pnl(hit, result_col, "pnl_2c", 2.0)


def generate_rules(side_modes: list[str]) -> list[Rule]:
    rules: list[Rule] = []
    i = 0
    for side_mode in side_modes:
        for fair_p in [0.60, 0.65, 0.70]:
            for edge in [12.0, 15.0, 18.0]:
                for ttl_min, ttl_max in [(8.0, 12.0), (10.0, 12.0), (10.0, 13.0)]:
                    for entry_max in [0.50, 0.55]:
                        for qty in [250.0, 500.0, 1000.0]:
                            for dist in [None, 0.0, 2.5, 5.0, 10.0, 15.0]:
                                for qs in [None, 0.5]:
                                    i += 1
                                    rules.append(
                                        Rule(
                                            name=f"official_grid_{i:05d}",
                                            side_mode=side_mode,
                                            fair_p_min=fair_p,
                                            edge_min=edge,
                                            ttl_min=ttl_min,
                                            ttl_max=ttl_max,
                                            entry_max=entry_max,
                                            qty_min=qty,
                                            aligned_dist_min_bps=dist,
                                            quote_speed_min=qs,
                                        )
                                    )
    return rules


def evaluate_pred(pred: pd.DataFrame, rules: list[Rule], args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows: list[dict[str, Any]] = []
    selected: dict[str, pd.DataFrame] = {}
    for idx, rule in enumerate(rules, start=1):
        if idx == 1 or idx % 250 == 0:
            print(f"historical screen progress {idx}/{len(rules)}", flush=True)
        trades = apply_rule(pred, rule, "result")
        if len(trades) < args.min_pred_trades:
            continue
        m = metrics(trades, "pnl_2c")
        if m["pnl"] <= 0 or m["sharpe"] < args.min_pred_sharpe:
            continue
        window_rows = []
        bad_windows = 0
        for window, group in trades.groupby("window"):
            wm = metrics(group, "pnl_2c")
            window_rows.append({"window": window, **wm})
            if wm["trades"] >= 3 and wm["pnl"] < -1.0:
                bad_windows += 1
        by_window = pd.DataFrame(window_rows)
        core = by_window[by_window["window"].isin(["pred_apr01_14", "pred_apr15_30", "pred_may01_12"])]
        jan = by_window[by_window["window"].astype(str).str.startswith("pred_jan")]
        if bad_windows > 0:
            continue
        if not core.empty and int((core["pnl"] > 0).sum()) < min(3, len(core)):
            continue
        if not jan.empty and int((jan["pnl"] > 0).sum()) < max(1, math.ceil(0.5 * len(jan))):
            continue
        score = float(m["pnl"] + 0.25 * m["sharpe"] - 0.5 * abs(m["max_dd"]))
        row = {
            **asdict(rule),
            **{f"pred_{k}": v for k, v in m.items()},
            "pred_bad_windows": int(bad_windows),
            "core_positive_windows": int((core["pnl"] > 0).sum()) if not core.empty else 0,
            "jan_positive_windows": int((jan["pnl"] > 0).sum()) if not jan.empty else 0,
            "window_count": int(len(by_window)),
            "pred_score": round(score, 6),
        }
        rows.append(row)
        selected[rule.name] = trades
    if not rows:
        return pd.DataFrame(), {}
    summary = pd.DataFrame(rows).sort_values(["pred_score", "pred_pnl"], ascending=False).head(args.max_screened).reset_index(drop=True)
    selected = {name: selected[name] for name in summary["name"].tolist()}
    return summary, selected


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print("loading historical Predexon side candidates...", flush=True)
    pred = load_pred_candidates()
    print(f"historical rows={len(pred):,}", flush=True)
    side_modes = [x.strip().lower() for x in str(args.side_modes).split(",") if x.strip()]
    bad_modes = sorted(set(side_modes) - {"yes", "no", "both"})
    if bad_modes:
        raise ValueError(f"invalid side modes: {bad_modes}")
    rules = generate_rules(side_modes)
    print(f"rules={len(rules):,}; screening on historical only...", flush=True)
    screen, selected = evaluate_pred(pred, rules, args)
    if screen.empty:
        raise SystemExit("no historical-screened rules")
    screen.to_csv(args.out_dir / "screened_rules_before_live.csv", index=False)

    print(f"loading live websocket candidate universe for screened={len(screen):,}...", flush=True)
    live, info = load_live_candidates(args.capture_db, args.start, args.end)
    print(f"live candidate rows={len(live):,}; fetching REST official outcomes...", flush=True)
    result_by_ticker = rest_official_results(
        live["market_ticker"].astype(str).str.upper().drop_duplicates().tolist(),
        args.rest_sleep,
    )
    live["official_result_filled"] = live["market_ticker"].astype(str).str.upper().map(result_by_ticker).fillna("")

    rows: list[dict[str, Any]] = []
    live_selected: dict[str, pd.DataFrame] = {}
    for row in screen.to_dict("records"):
        rule = Rule(
            name=row["name"],
            side_mode=row["side_mode"],
            fair_p_min=float(row["fair_p_min"]),
            edge_min=float(row["edge_min"]),
            ttl_min=float(row["ttl_min"]),
            ttl_max=float(row["ttl_max"]),
            entry_max=float(row["entry_max"]),
            qty_min=float(row["qty_min"]),
            aligned_dist_min_bps=None if pd.isna(row.get("aligned_dist_min_bps")) else float(row["aligned_dist_min_bps"]),
            quote_speed_min=None if pd.isna(row.get("quote_speed_min")) else float(row["quote_speed_min"]),
            rv_min=None if pd.isna(row.get("rv_min")) else float(row["rv_min"]),
            rv_max=None if pd.isna(row.get("rv_max")) else float(row["rv_max"]),
        )
        official_trades = apply_rule(live, rule, "official_result_filled")
        official_trades = official_trades[official_trades["official_result_filled"].isin(["yes", "no"])].copy()
        proxy_trades = apply_rule(live, rule, "result")
        om = metrics(official_trades, "pnl_2c")
        pm = metrics(proxy_trades, "pnl_2c")
        final = {
            **row,
            **{f"live_official_{k}": v for k, v in om.items()},
            **{f"live_proxy_{k}": v for k, v in pm.items()},
        }
        reasons = []
        if om["trades"] < args.min_live_official_trades:
            reasons.append("too_few_live_official_trades")
        if om["pnl"] <= 0:
            reasons.append("live_official_not_positive")
        if om["sharpe"] < 0.8:
            reasons.append("live_official_sharpe_low")
        if om["max_dd"] < -2.0:
            reasons.append("live_official_drawdown")
        final["deploy_ready"] = not reasons
        final["status"] = "PASS" if not reasons else "FAIL"
        final["failure_reasons"] = ";".join(reasons)
        rows.append(final)
        if len(official_trades):
            official_trades["rule"] = rule.name
            live_selected[rule.name] = official_trades

    summary = pd.DataFrame(rows).sort_values(
        ["deploy_ready", "live_official_pnl", "live_official_sharpe", "pred_score"],
        ascending=[False, False, False, False],
    )
    summary.to_csv(args.out_dir / "official_robust_summary.csv", index=False)
    if live_selected:
        pd.concat(live_selected.values(), ignore_index=True).to_parquet(args.out_dir / "screened_live_official_trades.parquet", index=False, compression="zstd")

    info_out = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_info": info,
        "historical_rows": int(len(pred)),
        "rules": int(len(rules)),
        "screened_rules": int(len(screen)),
        "live_candidate_rows": int(len(live)),
        "live_unique_tickers": int(live["market_ticker"].nunique()) if not live.empty else 0,
        "rest_official_results": int(sum(1 for v in result_by_ticker.values() if v in {"yes", "no"})),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info_out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = [
        "# BTC15M Official-Robust Grid",
        "",
        f"Created UTC: `{info_out['created_at_utc']}`",
        "",
        "## Top Rows",
        "",
        summary.head(30).round(4).to_string(index=False),
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info_out, indent=2, sort_keys=True, default=str),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.head(30).round(4).to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
