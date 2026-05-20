#!/usr/bin/env python3
"""Fast BTC15M filter grid on already-materialized first-signal trades.

This intentionally avoids rebuilding the raw websocket/Predexon candidate
matrix. A rule here means: run the frozen base strategy, then only accept its
first event-level signal if that signal also passes the extra causal filter.
It never waits for a later better signal in the same event, so it is
conservative and executable by adding a rejection gate to the live scanner.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_PREDEXON = (
    BACKTEST_ROOT
    / "btc15m_f2_combined_predexon_plus_jan29_20260516_164544"
    / "combined_predexon_trades.parquet"
)
DEFAULT_LIVE = (
    BACKTEST_ROOT
    / "btc15m_live_ws_rest_official_refresh_20260516_183818"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_materialized_filter_grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


STRATEGY_MAP = {
    "q250": "ttl10_12_entry50_q250",
    "q250_qspeed05": "ttl10_12_entry50_q250_qspeed05",
    "q500": "ttl10_12_entry50_q500",
    "q1000": "ttl10_12_entry50_q1000",
}


@dataclass(frozen=True)
class Rule:
    name: str
    live_candidate: str
    pred_strategy: str
    side_mode: str
    fair_p_min: float
    edge_min: float
    entry_max: float
    qty_min: float
    spread_max: float
    ttl_min: float
    ttl_max: float
    rv_min: float | None = None
    rv_max: float | None = None
    quote_speed_min: float | None = None
    aligned_dist_min_bps: float | None = None


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


def metrics(df: pd.DataFrame, pnl_col: str, win_col: str | None = None) -> dict[str, Any]:
    if df.empty or pnl_col not in df:
        return {
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
        }
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").dropna()
    work = df.loc[pnl.index].copy()
    premium = pd.to_numeric(work.get("premium_stress", work.get("premium", pd.Series(dtype=float))), errors="coerce").fillna(0.0)
    if win_col and win_col in work:
        wins = pd.to_numeric(work[win_col], errors="coerce")
    elif "win" in work:
        wins = work["win"].astype(float)
    else:
        result_col = "official_result_filled" if "official_result_filled" in work else "result"
        wins = work[result_col].astype(str).str.lower().eq(work["side"].astype(str).str.lower()).astype(float)
    total_pnl = float(pnl.sum())
    total_premium = float(premium.sum())
    return {
        "trades": int(len(work)),
        "pnl": total_pnl,
        "premium": total_premium,
        "rop": float(total_pnl / total_premium) if total_premium > 0 else 0.0,
        "win_rate": float(wins.mean()) if len(wins) else 0.0,
        "max_dd": max_drawdown(pnl.reset_index(drop=True)),
        "sharpe": sharpe(pnl),
    }


def aligned_distance(df: pd.DataFrame) -> pd.Series:
    if "distance_bps" in df:
        d = pd.to_numeric(df["distance_bps"], errors="coerce")
    elif {"btc_spot_model", "floor_strike"}.issubset(df.columns):
        d = 10000.0 * np.log(pd.to_numeric(df["btc_spot_model"], errors="coerce") / pd.to_numeric(df["floor_strike"], errors="coerce"))
    else:
        return pd.Series(np.nan, index=df.index)
    side = df["side"].astype(str).str.lower()
    return d.where(side.eq("yes"), -d)


def apply_rule(df: pd.DataFrame, rule: Rule, mode: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    base_col = "strategy" if mode == "predexon" else "candidate"
    base_value = rule.pred_strategy if mode == "predexon" else rule.live_candidate
    out = df[df[base_col].astype(str).eq(base_value)].copy()
    if out.empty:
        return out
    side = out["side"].astype(str).str.lower()
    if rule.side_mode == "yes":
        mask = side.eq("yes")
    elif rule.side_mode == "no":
        mask = side.eq("no")
    else:
        mask = side.isin(["yes", "no"])
    mask &= pd.to_numeric(out["side_fair_p"], errors="coerce").ge(rule.fair_p_min)
    mask &= pd.to_numeric(out["fair_edge_cents"], errors="coerce").ge(rule.edge_min)
    mask &= pd.to_numeric(out["entry_price"], errors="coerce").le(rule.entry_max)
    mask &= pd.to_numeric(out["visible_qty"], errors="coerce").ge(rule.qty_min)
    mask &= pd.to_numeric(out["spread_cents"], errors="coerce").le(rule.spread_max)
    mask &= pd.to_numeric(out["ttl_min"], errors="coerce").between(rule.ttl_min, rule.ttl_max)
    if rule.rv_min is not None:
        mask &= pd.to_numeric(out["rv_60m"], errors="coerce").ge(rule.rv_min)
    if rule.rv_max is not None:
        mask &= pd.to_numeric(out["rv_60m"], errors="coerce").le(rule.rv_max)
    if rule.quote_speed_min is not None:
        mask &= pd.to_numeric(out["quote_speed_cents"], errors="coerce").ge(rule.quote_speed_min)
    if rule.aligned_dist_min_bps is not None:
        mask &= aligned_distance(out).ge(rule.aligned_dist_min_bps)
    return out.loc[mask].sort_values([c for c in ["available_at", "received_at_utc", "event_ticker"] if c in out.columns]).reset_index(drop=True)


def generate_rules() -> list[Rule]:
    rules: list[Rule] = []
    i = 0
    for live_candidate, pred_strategy in STRATEGY_MAP.items():
        # Focused, interpretable grid. NO-side variants are excluded here
        # because latest REST-official replay showed the source-mismatch loss
        # mode is concentrated there.
        for side_mode in ["both", "yes"]:
            for fair_p in [0.60, 0.65]:
                for edge in [12.0, 15.0, 18.0]:
                    for entry_max in [0.50, 0.55]:
                        for qty in [250.0, 500.0, 1000.0]:
                            for spread_max in [1.0, 2.0]:
                                for dist in [None, 0.0, 5.0]:
                                    for rv_max in [None, 0.40]:
                                        i += 1
                                        rules.append(
                                            Rule(
                                                name=f"mat_grid_{i:05d}",
                                                live_candidate=live_candidate,
                                                pred_strategy=pred_strategy,
                                                side_mode=side_mode,
                                                fair_p_min=fair_p,
                                                edge_min=edge,
                                                entry_max=entry_max,
                                                qty_min=qty,
                                                spread_max=spread_max,
                                                ttl_min=10.0,
                                                ttl_max=12.0,
                                                rv_max=rv_max,
                                                aligned_dist_min_bps=dist,
                                            )
                                        )
    return rules


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fast grid on materialized BTC15M trades.")
    p.add_argument("--predexon-trades", type=Path, default=DEFAULT_PREDEXON)
    p.add_argument("--live-trades", type=Path, default=DEFAULT_LIVE)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-pred-trades", type=int, default=30)
    p.add_argument("--min-live-official-trades", type=int, default=8)
    p.add_argument("--min-pred-sharpe", type=float, default=1.2)
    p.add_argument("--pred-pnl-col", default="pnl_stress")
    p.add_argument("--pred-win-col", default="win")
    p.add_argument(
        "--min-pred-official-coverage",
        type=float,
        default=None,
        help=(
            "Minimum fraction of selected Predexon rows that must have the requested "
            "official PnL column filled. Defaults to 1.0 for official columns and 0.0 otherwise."
        ),
    )
    p.add_argument("--top-n", type=int, default=100)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    min_pred_official_coverage = args.min_pred_official_coverage
    if min_pred_official_coverage is None:
        min_pred_official_coverage = 1.0 if "official" in args.pred_pnl_col.lower() else 0.0
    pred = pd.read_parquet(args.predexon_trades)
    live = pd.read_parquet(args.live_trades)
    if "available_at" not in live and "received_at_utc" in live:
        live["available_at"] = live["received_at_utc"]
    if "ttl_min" not in live and {"close_time", "received_at_utc"}.issubset(live.columns):
        live["ttl_min"] = (
            pd.to_datetime(live["close_time"], utc=True, errors="coerce")
            - pd.to_datetime(live["received_at_utc"], utc=True, errors="coerce")
        ).dt.total_seconds() / 60.0
    rules = generate_rules()

    rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    selected_live: list[pd.DataFrame] = []
    for rule in rules:
        pt = apply_rule(pred, rule, "predexon")
        if len(pt) < args.min_pred_trades:
            continue
        if args.pred_pnl_col not in pt:
            raise ValueError(f"missing pred pnl column {args.pred_pnl_col!r}")
        pred_win_col = args.pred_win_col if args.pred_win_col in pt else None
        pm = metrics(pt, args.pred_pnl_col, pred_win_col)
        if pm["pnl"] <= 0 or pm["sharpe"] < args.min_pred_sharpe:
            continue
        pred_selected_rows = int(len(pt))
        pred_scored_rows = int(pm["trades"])
        pred_missing_rows = int(max(0, pred_selected_rows - pred_scored_rows))
        pred_official_coverage = float(pred_scored_rows / pred_selected_rows) if pred_selected_rows else 0.0
        missing_pred = pt[pd.to_numeric(pt[args.pred_pnl_col], errors="coerce").isna()].copy()
        missing_proxy_metrics = metrics(missing_pred, "pnl_stress", "win") if "pnl_stress" in missing_pred else metrics(pd.DataFrame(), "pnl_stress", "win")
        missing_bad_windows = 0
        if not missing_pred.empty and "window" in missing_pred:
            for _window, missing_group in missing_pred.groupby("window", dropna=False):
                missing_window_metrics = metrics(missing_group, "pnl_stress", "win")
                if missing_window_metrics["trades"] >= 3 and missing_window_metrics["pnl"] < -1.0:
                    missing_bad_windows += 1
        bad_windows = 0
        for window, group in pt.groupby("window", dropna=False):
            wm = metrics(group, args.pred_pnl_col, pred_win_col)
            window_rows.append({"rule": rule.name, "window": window, **wm})
            if wm["trades"] >= 3 and wm["pnl"] < -1.0:
                bad_windows += 1
        if bad_windows:
            continue
        lt = apply_rule(live, rule, "live")
        official = lt[lt["official_result_filled"].astype(str).str.lower().isin(["yes", "no"])].copy()
        om = metrics(official, "pnl_official_rest_2c", "win_pnl_official_rest_2c")
        lpm = metrics(lt, "pnl_proxy_2c", "win_pnl_proxy_2c")
        reasons: list[str] = []
        if om["trades"] < args.min_live_official_trades:
            reasons.append("too_few_live_official_trades")
        if om["pnl"] <= 0:
            reasons.append("live_official_not_positive")
        if om["max_dd"] < -2.0:
            reasons.append("live_official_drawdown")
        if lpm["pnl"] <= 0:
            reasons.append("live_proxy_not_positive")
        if pred_official_coverage < min_pred_official_coverage:
            reasons.append("pred_official_coverage_incomplete")
        if missing_bad_windows:
            reasons.append("pred_uncovered_proxy_bad_windows")
        row = {
            **asdict(rule),
            "pred_selected_rows": pred_selected_rows,
            "pred_scored_rows": pred_scored_rows,
            "pred_missing_official_rows": pred_missing_rows,
            "pred_official_coverage": pred_official_coverage,
            **{f"pred_missing_proxy_{k}": v for k, v in missing_proxy_metrics.items()},
            "pred_missing_proxy_bad_windows": int(missing_bad_windows),
            **{f"pred_{k}": v for k, v in pm.items()},
            **{f"live_official_{k}": v for k, v in om.items()},
            **{f"live_proxy_{k}": v for k, v in lpm.items()},
            "pred_bad_windows": int(bad_windows),
            "deploy_ready": not reasons and om["trades"] >= 30,
            "research_pass": not reasons,
            "failure_reasons": ";".join(reasons),
        }
        rows.append(row)
        if not official.empty:
            tmp = official.copy()
            tmp["rule"] = rule.name
            selected_live.append(tmp)

    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary = summary.sort_values(
            ["research_pass", "live_official_pnl", "live_official_trades", "pred_sharpe"],
            ascending=[False, False, False, False],
        ).head(args.top_n)
    summary.to_csv(args.out_dir / "materialized_filter_summary.csv", index=False)
    if window_rows:
        pd.DataFrame(window_rows).to_csv(args.out_dir / "materialized_filter_by_window.csv", index=False)
    if selected_live:
        pd.concat(selected_live, ignore_index=True).to_parquet(args.out_dir / "materialized_filter_live_trades.parquet", index=False, compression="zstd")
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predexon_trades": str(args.predexon_trades),
        "live_trades": str(args.live_trades),
        "rules": len(rules),
        "screened_rows": int(len(summary)),
        "min_pred_trades": args.min_pred_trades,
        "min_pred_official_coverage": min_pred_official_coverage,
        "min_live_official_trades": args.min_live_official_trades,
        "note": "Conservative first-signal rejection filters only; not a raw candidate search.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M Materialized Filter Grid",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Top Rows",
        "",
        summary.head(30).round(4).to_string(index=False) if not summary.empty else "No screened rows.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.head(30).round(4).to_string(index=False) if not summary.empty else "No screened rows.")
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
