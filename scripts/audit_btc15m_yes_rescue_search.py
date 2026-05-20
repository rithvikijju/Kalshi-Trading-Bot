#!/usr/bin/env python3
"""Constrained BTC15M YES-only rescue search.

This is an audit/search bridge, not a production backtest engine.  It searches
only over historical Predexon side-candidate rows, then evaluates the best
frozen rules once on local live websocket capture with REST-filled official
Kalshi outcomes.

The intent is to answer a narrow question after the broad F2 rules failed:
is there a YES-only filter that keeps enough trades, fixes the weak January
windows, and also survives official-settled live replay?
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    DEFAULT_CAPTURE_DB,
    add_proxy_results,
    load_capture,
    make_side_candidates,
    metadata_from_lifecycle,
    prepare_quotes,
)


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_yes_rescue_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

PRED_FILES = {
    "pred_apr01_14": BACKTEST_ROOT / "btc15m_f2_liq_regime_apr01_14_20260516_052801" / "side_candidates.parquet",
    "pred_apr15_30": BACKTEST_ROOT / "btc15m_f2_liq_regime_apr15_30_20260516_052801" / "side_candidates.parquet",
    "pred_may01_12": BACKTEST_ROOT / "btc15m_f2_liq_regime_may01_12_20260516_052801" / "side_candidates.parquet",
    "pred_jan08_13": BACKTEST_ROOT / "btc15m_f2_predexon_jan08_13_20260516_141637" / "side_candidates.parquet",
    "pred_jan13_18": BACKTEST_ROOT / "btc15m_f2_predexon_jan13_18_20260516_141637" / "side_candidates.parquet",
    "pred_jan18_23": BACKTEST_ROOT / "btc15m_f2_predexon_jan18_23_20260516_141637" / "side_candidates.parquet",
    "pred_jan23_25": BACKTEST_ROOT / "btc15m_f2_predexon_jan23_25_20260516_142455" / "side_candidates.parquet",
    "pred_jan25_27p": BACKTEST_ROOT / "btc15m_f2_predexon_jan25_27p_20260516_142623" / "side_candidates.parquet",
    "pred_jan27_1230_jan28_0200": BACKTEST_ROOT / "btc15m_f2_predexon_jan27_1230_jan28_0200_20260516_151231" / "side_candidates.parquet",
    "pred_jan28_tail": BACKTEST_ROOT / "btc15m_f2_predexon_jan28_tail_20260516_155406" / "side_candidates.parquet",
    "pred_jan29_partial": BACKTEST_ROOT / "btc15m_f2_predexon_jan29_partial_20260516_164316" / "side_candidates.parquet",
}


@dataclass(frozen=True)
class Rule:
    name: str
    fair_p_min: float
    edge_min: float
    ttl_min: float
    ttl_max: float
    entry_max: float
    qty_min: float
    rv_min: float | None = None
    rv_max: float | None = None
    quote_speed_min: float | None = None
    dist_min_bps: float | None = None


def fee_one(entry: float) -> float:
    return kalshi_fee_dollars(float(entry), contracts=1, liquidity="taker")


def fee_array(entry: pd.Series | np.ndarray) -> np.ndarray:
    price = np.clip(np.asarray(entry, dtype=float), 0.0, 1.0)
    raw = 0.07 * price * (1.0 - price)
    return np.ceil((raw - 1e-12) * 100.0) / 100.0


def stressed_pnl(df: pd.DataFrame, result_col: str, out_col: str, slip_cents: float = 2.0) -> pd.DataFrame:
    out = df.copy()
    entry = (pd.to_numeric(out["entry_price"], errors="coerce") + slip_cents / 100.0).clip(upper=0.99)
    fee = entry.map(fee_one)
    result = out[result_col].astype(str).str.lower()
    win = result.eq(out["side"].astype(str).str.lower()) & result.isin(["yes", "no"])
    valid = result.isin(["yes", "no"])
    out["entry_stress"] = entry
    out["premium_stress"] = entry + fee
    out[f"win_{out_col}"] = np.where(valid, win, np.nan)
    out[out_col] = np.where(valid & win, 1.0 - entry - fee, np.where(valid, -(entry + fee), np.nan))
    return out


def max_drawdown(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def metrics(df: pd.DataFrame, pnl_col: str) -> dict[str, Any]:
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").dropna() if pnl_col in df else pd.Series(dtype=float)
    work = df.loc[pnl.index] if len(pnl) else df.iloc[0:0]
    wins = pd.to_numeric(work.get(f"win_{pnl_col}", pd.Series(dtype=float)), errors="coerce")
    prem = pd.to_numeric(work.get("premium_stress", work.get("premium", pd.Series(dtype=float))), errors="coerce").fillna(0.0)
    return {
        "trades": int(len(work)),
        "pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "return_on_100": float(pnl.sum()) if len(pnl) else 0.0,
        "premium": float(prem.sum()) if len(prem) else 0.0,
        "rop": float(pnl.sum() / prem.sum()) if len(pnl) and float(prem.sum()) > 0 else 0.0,
        "win_rate": float(wins.mean()) if len(wins) else 0.0,
        "max_dd": max_drawdown(pnl.reset_index(drop=True)),
        "sharpe": sharpe(pnl),
    }


def load_predexon_candidates() -> pd.DataFrame:
    cols = [
        "market_ticker",
        "event_ticker",
        "available_at",
        "sequence",
        "side",
        "entry_price",
        "visible_qty",
        "side_fair_p",
        "fair_edge_cents",
        "spread_cents",
        "ttl_min",
        "rv_60m",
        "quote_speed_cents",
        "distance_bps",
        "data_quality_ok",
        "result",
        "premium",
        "win",
    ]
    frames: list[pd.DataFrame] = []
    for label, path in PRED_FILES.items():
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=[c for c in cols if c != "window"]).copy()
        df["window"] = label
        df["side"] = df["side"].astype(str).str.lower()
        df["result"] = df["result"].astype(str).str.lower()
        # Search universe: YES-only, executable, broadly close to the F2 family.
        mask = (
            df["side"].eq("yes")
            & df["result"].isin(["yes", "no"])
            & df.get("data_quality_ok", True).astype(bool)
            & pd.to_numeric(df["ttl_min"], errors="coerce").between(4.0, 13.0)
            & pd.to_numeric(df["spread_cents"], errors="coerce").le(2.0)
            & pd.to_numeric(df["entry_price"], errors="coerce").between(0.02, 0.65)
            & pd.to_numeric(df["visible_qty"], errors="coerce").ge(50.0)
            & pd.to_numeric(df["side_fair_p"], errors="coerce").ge(0.55)
            & pd.to_numeric(df["fair_edge_cents"], errors="coerce").ge(8.0)
        )
        keep = df.loc[mask].copy()
        frames.append(keep)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True, errors="coerce")
    return out.dropna(subset=["available_at"]).reset_index(drop=True)


def load_live_candidates(capture_db: Path, start: str | None, end: str | None) -> pd.DataFrame:
    top, btc, lifecycle, _decisions, info = load_capture(capture_db, start, end)
    meta, official_results = metadata_from_lifecycle(lifecycle)
    q = prepare_quotes(top, btc, meta, official_results, "spot")
    q = add_proxy_results(q, btc, "spot")
    capture_end = pd.Timestamp(info["capture_end_utc"])
    q = q[q["close_time"].le(capture_end) & q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
    # The rescue search is YES-only. Building both sides for the full capture
    # can require multiple GiB, so materialize only the broad YES universe.
    p_yes = pd.to_numeric(q["lognormal_p_yes"], errors="coerce")
    entry = pd.to_numeric(q["yes_ask"], errors="coerce")
    visible = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    entry_fee = pd.Series(fee_array(entry), index=q.index)
    fair_edge = (p_yes - entry) * 100.0 - entry_fee * 100.0
    mask = (
        q["btc_spot_age_sec"].between(0, 120)
        & pd.to_numeric(q["ttl_min"], errors="coerce").between(4.0, 13.0)
        & pd.to_numeric(q["spread_cents"], errors="coerce").le(2.0)
        & entry.between(0.02, 0.65)
        & visible.ge(50.0)
        & p_yes.ge(0.55)
        & fair_edge.ge(8.0)
    )
    keep_cols = [
        "received_at_ns",
        "received_at_utc",
        "event_ticker",
        "market_ticker",
        "seq",
        "spread_cents",
        "btc_spot_model",
        "btc_spot_age_sec",
        "rv_60m",
        "quote_speed_cents",
        "floor_strike",
        "ttl_min",
        "close_time",
        "official_result",
        "close_btc_spot",
        "close_btc_time",
        "proxy_result",
        "proxy_distance_usd",
        "near_strike_proxy",
    ]
    candidates = q.loc[mask, keep_cols].copy()
    if candidates.empty:
        return candidates
    candidates["side"] = "yes"
    candidates["entry_price"] = entry.loc[candidates.index].to_numpy()
    candidates["visible_qty"] = visible.loc[candidates.index].to_numpy()
    candidates["side_fair_p"] = p_yes.loc[candidates.index].to_numpy()
    candidates["entry_fee"] = entry_fee.loc[candidates.index].to_numpy()
    candidates["premium"] = candidates["entry_price"] + candidates["entry_fee"]
    candidates["fair_edge_cents"] = fair_edge.loc[candidates.index].to_numpy()
    candidates["distance_bps"] = 10000.0 * np.log(
        pd.to_numeric(candidates["btc_spot_model"], errors="coerce")
        / pd.to_numeric(candidates["floor_strike"], errors="coerce")
    )
    candidates["result"] = candidates["proxy_result"].astype(str).str.lower()
    candidates["window"] = "live_ws"
    candidates["available_at"] = pd.to_datetime(candidates["received_at_utc"], utc=True, errors="coerce")
    for col in ["data_quality_ok"]:
        if col not in candidates:
            candidates[col] = True
    return candidates.reset_index(drop=True)


def rest_official_results(tickers: list[str], sleep_sec: float) -> dict[str, str]:
    session = requests.Session()
    out: dict[str, str] = {}
    for ticker in sorted(set(str(t).upper() for t in tickers)):
        for attempt in range(6):
            try:
                response = session.get(f"{BASE_URL}/markets/{ticker}", timeout=30)
            except requests.RequestException:
                if attempt == 5:
                    out[ticker] = ""
                    break
                time.sleep(min(5.0, 2.0**attempt))
                continue
            if response.status_code == 404:
                out[ticker] = ""
                break
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(5.0, 2.0**attempt)
                time.sleep(delay)
                continue
            try:
                response.raise_for_status()
                result = str((response.json().get("market") or {}).get("result") or "").lower()
            except Exception:
                result = ""
            out[ticker] = result if result in {"yes", "no"} else ""
            break
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return out


def apply_rule(df: pd.DataFrame, rule: Rule, *, result_col: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    mask = (
        pd.to_numeric(df["side_fair_p"], errors="coerce").ge(rule.fair_p_min)
        & pd.to_numeric(df["fair_edge_cents"], errors="coerce").ge(rule.edge_min)
        & pd.to_numeric(df["ttl_min"], errors="coerce").between(rule.ttl_min, rule.ttl_max)
        & pd.to_numeric(df["entry_price"], errors="coerce").between(0.02, rule.entry_max)
        & pd.to_numeric(df["visible_qty"], errors="coerce").ge(rule.qty_min)
        & pd.to_numeric(df["spread_cents"], errors="coerce").le(2.0)
    )
    if rule.rv_min is not None:
        mask &= pd.to_numeric(df["rv_60m"], errors="coerce").ge(rule.rv_min)
    if rule.rv_max is not None:
        mask &= pd.to_numeric(df["rv_60m"], errors="coerce").le(rule.rv_max)
    if rule.quote_speed_min is not None:
        mask &= pd.to_numeric(df["quote_speed_cents"], errors="coerce").ge(rule.quote_speed_min)
    if rule.dist_min_bps is not None and "distance_bps" in df:
        # YES-only: positive distance means spot above strike in the source
        # pipeline's convention.  If unavailable, leave the row out.
        mask &= pd.to_numeric(df["distance_bps"], errors="coerce").ge(rule.dist_min_bps)
    cols = [c for c in ["event_ticker", "available_at", "fair_edge_cents", "sequence", "seq"] if c in df.columns]
    order = ["event_ticker", "available_at", "fair_edge_cents"]
    if "sequence" in cols:
        order.append("sequence")
    elif "seq" in cols:
        order.append("seq")
    hit = df.loc[mask].sort_values(order, ascending=[True, True, False] + [True] * (len(order) - 3)).copy()
    hit = hit.drop_duplicates("event_ticker", keep="first").reset_index(drop=True)
    return stressed_pnl(hit, result_col, "pnl_2c", 2.0)


def generate_rules() -> list[Rule]:
    rules: list[Rule] = []
    i = 0
    for fair_p in [0.60, 0.65, 0.70]:
        for edge in [12.0, 15.0, 18.0]:
            for ttl_min, ttl_max in [(10.0, 12.0), (10.0, 13.0)]:
                for entry_max in [0.50, 0.55]:
                    for qty in [250.0, 500.0, 1000.0]:
                        for qs in [None, 0.5]:
                            for dist_min_bps in [None, 0.0, 2.5, 5.0, 10.0]:
                                i += 1
                                rules.append(
                                    Rule(
                                        name=f"yes_rescue_{i:05d}",
                                        fair_p_min=fair_p,
                                        edge_min=edge,
                                        ttl_min=ttl_min,
                                        ttl_max=ttl_max,
                                        entry_max=entry_max,
                                        qty_min=qty,
                                        quote_speed_min=qs,
                                        dist_min_bps=dist_min_bps,
                                    )
                                )
    return rules


def evaluate_pred(pred: pd.DataFrame, rules: list[Rule]) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows: list[dict[str, Any]] = []
    selected: dict[str, pd.DataFrame] = {}
    for rule in rules:
        trades = apply_rule(pred, rule, result_col="result")
        if trades.empty:
            continue
        m = metrics(trades, "pnl_2c")
        by_window = []
        bad_windows = 0
        for window, group in trades.groupby("window"):
            wm = metrics(group, "pnl_2c")
            by_window.append({"window": window, **wm})
            if wm["trades"] >= 3 and wm["pnl"] < -1.0:
                bad_windows += 1
        window_df = pd.DataFrame(by_window)
        core_windows = window_df[window_df["window"].isin(["pred_apr01_14", "pred_apr15_30", "pred_may01_12"])]
        jan_windows = window_df[window_df["window"].astype(str).str.startswith("pred_jan")]
        row = {
            **asdict(rule),
            **{f"pred_{k}": v for k, v in m.items()},
            "pred_bad_windows": int(bad_windows),
            "core_positive_windows": int((core_windows["pnl"] > 0).sum()) if not core_windows.empty else 0,
            "jan_positive_windows": int((jan_windows["pnl"] > 0).sum()) if not jan_windows.empty else 0,
            "window_count": int(len(window_df)),
        }
        rows.append(row)
        selected[rule.name] = trades
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary, selected
    summary["pred_score"] = (
        summary["pred_pnl"]
        + 1.5 * summary["pred_sharpe"]
        - 2.0 * summary["pred_bad_windows"]
        + 0.4 * summary["core_positive_windows"]
        + 0.2 * summary["jan_positive_windows"]
    )
    summary = summary.sort_values(["pred_score", "pred_pnl", "pred_trades"], ascending=[False, False, False]).reset_index(drop=True)
    return summary, selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--top-n", type=int, default=75)
    parser.add_argument("--rest-sleep", type=float, default=0.01)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print("loading Predexon side-candidate universe...", flush=True)
    pred = load_predexon_candidates()
    print(f"Predexon universe rows={len(pred):,}", flush=True)
    rules = generate_rules()
    print(f"evaluating Predexon rules={len(rules):,}", flush=True)
    pred_summary, pred_selected = evaluate_pred(pred, rules)
    if pred_summary.empty:
        raise RuntimeError("no Predexon rules produced trades")
    pred_summary.head(500).to_csv(args.out_dir / "pred_search_top500.csv", index=False)

    # Only carry plausible historical rules into live official holdout.
    screen = pred_summary[
        pred_summary["pred_trades"].ge(50)
        & pred_summary["pred_pnl"].gt(0)
        & pred_summary["pred_sharpe"].ge(1.0)
        & pred_summary["pred_bad_windows"].le(1)
    ].head(args.top_n)
    if screen.empty:
        screen = pred_summary.head(args.top_n)
    screen.to_csv(args.out_dir / "screened_rules_before_live.csv", index=False)

    print(f"building live YES candidate universe for screened_rules={len(screen)}...", flush=True)
    live = load_live_candidates(args.capture_db, args.start, args.end)
    live_candidates = live[
        live["side_fair_p"].ge(0.55)
        & live["fair_edge_cents"].ge(8.0)
        & live["entry_price"].between(0.02, 0.65)
        & live["visible_qty"].ge(50.0)
    ].copy()

    print(f"live candidate rows={len(live_candidates):,}; fetching REST outcomes...", flush=True)
    result_by_ticker = rest_official_results(live_candidates["market_ticker"].astype(str).str.upper().unique().tolist(), args.rest_sleep)
    live_candidates["official_result_filled"] = live_candidates["market_ticker"].astype(str).str.upper().map(result_by_ticker).fillna("")

    live_rows: list[dict[str, Any]] = []
    live_trades: list[pd.DataFrame] = []
    window_rows: list[dict[str, Any]] = []
    rule_by_name = {r.name: r for r in rules}
    for row in screen.to_dict("records"):
        rule = rule_by_name[row["name"]]
        lt = apply_rule(live_candidates, rule, result_col="official_result_filled")
        lt["rule"] = rule.name
        lt_proxy = apply_rule(live_candidates, rule, result_col="result")
        lm = metrics(lt[lt["official_result_filled"].isin(["yes", "no"])].copy(), "pnl_2c")
        lpm = metrics(lt_proxy, "pnl_2c")
        out = {
            **row,
            "live_official_trades": lm["trades"],
            "live_official_pnl": lm["pnl"],
            "live_official_win_rate": lm["win_rate"],
            "live_official_max_dd": lm["max_dd"],
            "live_official_sharpe": lm["sharpe"],
            "live_proxy_trades": lpm["trades"],
            "live_proxy_pnl": lpm["pnl"],
            "live_proxy_win_rate": lpm["win_rate"],
            "live_proxy_max_dd": lpm["max_dd"],
            "live_proxy_sharpe": lpm["sharpe"],
        }
        reasons: list[str] = []
        if out["pred_trades"] < 50:
            reasons.append("too_few_pred_trades")
        if out["pred_bad_windows"] > 1:
            reasons.append("too_many_bad_pred_windows")
        if out["pred_pnl"] <= 0 or out["pred_sharpe"] < 1.0:
            reasons.append("weak_pred")
        if out["live_official_trades"] < 30:
            reasons.append("too_few_live_official_trades")
        if out["live_official_pnl"] <= 0:
            reasons.append("live_official_not_positive")
        out["deploy_ready"] = not reasons
        out["status"] = "PASS" if not reasons else "FAIL"
        out["failure_reasons"] = ";".join(reasons)
        live_rows.append(out)
        live_trades.append(lt)
        for window, group in pred_selected[rule.name].groupby("window"):
            window_rows.append({"rule": rule.name, "window": window, **metrics(group, "pnl_2c")})

    final = pd.DataFrame(live_rows).sort_values(
        ["deploy_ready", "live_official_pnl", "pred_score"], ascending=[False, False, False]
    )
    final.to_csv(args.out_dir / "rescue_summary.csv", index=False)
    pd.DataFrame(window_rows).to_csv(args.out_dir / "pred_by_window_for_screened.csv", index=False)
    if live_trades:
        pd.concat(live_trades, ignore_index=True).to_parquet(args.out_dir / "screened_live_trades.parquet", index=False, compression="zstd")
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "pred_rows": int(len(pred)),
        "rules": int(len(rules)),
        "screened_rules": int(len(screen)),
        "live_candidate_rows": int(len(live_candidates)),
        "live_unique_tickers": int(live_candidates["market_ticker"].nunique()) if not live_candidates.empty else 0,
        "rest_official_results": int(sum(1 for v in result_by_ticker.values() if v in {"yes", "no"})),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M YES Rescue Search",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Top Final Rows",
        "",
        final.head(25).round(4).to_string(index=False),
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(final.head(25).round(4).to_string(index=False))
    print(json.dumps(info, indent=2, sort_keys=True))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
