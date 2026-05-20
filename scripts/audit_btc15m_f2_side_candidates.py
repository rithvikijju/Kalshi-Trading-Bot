#!/usr/bin/env python3
"""Audit BTC15M F2 side-filter candidates against Predexon and live WS data.

This is a promotion-gate script, not a search script. It consumes already
materialized replay trade files and evaluates frozen side variants with the
same 2c-stressed PnL fields used by the F2 validators.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREDEXON_TRADES = PROJECT_ROOT / "backtest_outputs" / "btc15m_f2_combined_predexon_live_refresh_20260516_151627" / "combined_predexon_trades.parquet"
DEFAULT_LIVE_TRADES = PROJECT_ROOT / "backtest_outputs" / "btc15m_f2_deployment_gate_latest_20260516_151231" / "live_ws_trades.parquet"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_f2_side_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Candidate:
    name: str
    pred_strategy: str
    live_candidate: str
    side: str | None = None
    rv_min: float | None = None
    edge_min: float | None = None
    qty_min: float | None = None


CANDIDATES = [
    Candidate("q250_both", "ttl10_12_entry50_q250", "q250"),
    Candidate("q500_both", "ttl10_12_entry50_q500", "q500"),
    Candidate("q1000_both", "ttl10_12_entry50_q1000", "q1000"),
    Candidate("q250_qspeed05_both", "ttl10_12_entry50_q250_qspeed05", "q250_qspeed05"),
    Candidate("q250_yes", "ttl10_12_entry50_q250", "q250", side="yes"),
    Candidate("q500_yes", "ttl10_12_entry50_q500", "q500", side="yes"),
    Candidate("q1000_yes", "ttl10_12_entry50_q1000", "q1000", side="yes"),
    Candidate("q1000_yes_edge15", "ttl10_12_entry50_q1000", "q1000", side="yes", edge_min=15.0),
    Candidate("q1000_yes_rv40_edge15", "ttl10_12_entry50_q1000", "q1000", side="yes", rv_min=0.40, edge_min=15.0),
]


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


def apply_filters(df: pd.DataFrame, c: Candidate, mode: str) -> pd.DataFrame:
    if mode == "predexon":
        out = df[df["strategy"].eq(c.pred_strategy)].copy()
    else:
        out = df[df["candidate"].eq(c.live_candidate)].copy()
    if c.side is not None and "side" in out:
        out = out[out["side"].astype(str).str.lower().eq(c.side)].copy()
    if c.rv_min is not None and "rv_60m" in out:
        out = out[pd.to_numeric(out["rv_60m"], errors="coerce").ge(c.rv_min)].copy()
    if c.edge_min is not None and "fair_edge_cents" in out:
        out = out[pd.to_numeric(out["fair_edge_cents"], errors="coerce").ge(c.edge_min)].copy()
    if c.qty_min is not None and "visible_qty" in out:
        out = out[pd.to_numeric(out["visible_qty"], errors="coerce").ge(c.qty_min)].copy()
    return out.sort_values([col for col in ["available_at", "received_at_utc", "event_ticker"] if col in out.columns]).reset_index(drop=True)


def metrics(df: pd.DataFrame, pnl_col: str, premium_col: str, result_col: str | None = None) -> dict[str, Any]:
    if df.empty:
        return {
            "trades": 0,
            "pnl": 0.0,
            "return_on_100": 0.0,
            "premium": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
        }
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").dropna()
    work = df.loc[pnl.index].copy()
    premium = pd.to_numeric(work[premium_col], errors="coerce").fillna(0.0)
    if result_col and result_col in work:
        wins = work[result_col].astype(str).str.lower().eq(work["side"].astype(str).str.lower())
    elif "win" in work:
        wins = work["win"].astype(bool)
    else:
        wins = pd.Series(dtype=bool)
    total_pnl = float(pnl.sum())
    total_premium = float(premium.sum())
    return {
        "trades": int(len(work)),
        "pnl": total_pnl,
        "return_on_100": total_pnl,
        "premium": total_premium,
        "rop": float(total_pnl / total_premium) if total_premium > 0 else 0.0,
        "win_rate": float(wins.mean()) if len(wins) else 0.0,
        "max_dd": max_drawdown(pnl.reset_index(drop=True)),
        "sharpe": sharpe(pnl),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--predexon-trades", type=Path, default=DEFAULT_PREDEXON_TRADES)
    p.add_argument("--live-trades", type=Path, default=DEFAULT_LIVE_TRADES)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-pred-trades", type=int, default=50)
    p.add_argument("--min-live-proxy-trades", type=int, default=50)
    p.add_argument("--min-live-official-trades", type=int, default=30)
    p.add_argument("--min-sharpe", type=float, default=1.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pred = pd.read_parquet(args.predexon_trades)
    live = pd.read_parquet(args.live_trades)

    rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        p = apply_filters(pred, candidate, "predexon")
        l = apply_filters(live, candidate, "live")
        pm = metrics(p, "pnl_stress", "premium_stress")
        lm = metrics(l, "pnl_proxy_2c", "premium", "proxy_result")
        official_result_col = "official_result_filled" if "official_result_filled" in l.columns else "official_result"
        official_pnl_col = "pnl_official_rest_2c" if "pnl_official_rest_2c" in l.columns else "pnl_official_2c"
        official = (
            l[l[official_result_col].astype(str).str.lower().isin(["yes", "no"])].copy()
            if official_result_col in l
            else l.iloc[0:0]
        )
        om = metrics(official, official_pnl_col, "premium", official_result_col)

        reasons: list[str] = []
        if pm["trades"] < args.min_pred_trades:
            reasons.append("too_few_predexon_trades")
        if pm["pnl"] <= 0:
            reasons.append("predexon_not_positive")
        if pm["sharpe"] < args.min_sharpe:
            reasons.append("predexon_sharpe_low")
        if lm["trades"] < args.min_live_proxy_trades:
            reasons.append("too_few_live_proxy_trades")
        if lm["pnl"] <= 0:
            reasons.append("live_proxy_not_positive")
        if om["trades"] < args.min_live_official_trades:
            reasons.append("too_few_live_official_trades")
        if om["pnl"] <= 0 and om["trades"] > 0:
            reasons.append("live_official_not_positive")

        for window, group in p.groupby("window", dropna=False):
            wm = metrics(group, "pnl_stress", "premium_stress")
            window_rows.append({"candidate": candidate.name, "window": window, **wm})
            if wm["trades"] >= 3 and wm["pnl"] < -1.0:
                reasons.append(f"bad_window_{window}")

        rows.append(
            {
                "candidate": candidate.name,
                "deploy_ready": not reasons,
                "status": "PASS" if not reasons else "FAIL",
                "failure_reasons": ";".join(dict.fromkeys(reasons)),
                "pred_trades": pm["trades"],
                "pred_pnl": pm["pnl"],
                "pred_rop": pm["rop"],
                "pred_win_rate": pm["win_rate"],
                "pred_max_dd": pm["max_dd"],
                "pred_sharpe": pm["sharpe"],
                "live_proxy_trades": lm["trades"],
                "live_proxy_pnl": lm["pnl"],
                "live_proxy_rop": lm["rop"],
                "live_proxy_win_rate": lm["win_rate"],
                "live_proxy_max_dd": lm["max_dd"],
                "live_proxy_sharpe": lm["sharpe"],
                "live_official_trades": om["trades"],
                "live_official_pnl": om["pnl"],
                "live_official_win_rate": om["win_rate"],
            }
        )

    summary = pd.DataFrame(rows)
    by_window = pd.DataFrame(window_rows)
    summary.to_csv(args.out_dir / "side_gate_summary.csv", index=False)
    by_window.to_csv(args.out_dir / "side_gate_by_window.csv", index=False)
    report = [
        "# BTC15M F2 Side Candidate Gate",
        "",
        f"Created UTC: {datetime.now(timezone.utc).isoformat()}",
        f"Predexon trades: `{args.predexon_trades}`",
        f"Live trades: `{args.live_trades}`",
        "",
        "## Summary",
        "",
        summary.round(4).to_string(index=False),
        "",
        "## Notes",
        "",
        "A PASS here requires enough Predexon trades, positive stressed Predexon PnL, sufficient live proxy and official-settled evidence, and no >=3-trade historical window worse than -$1.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    (args.out_dir / "run_info.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "predexon_trades": str(args.predexon_trades),
                "live_trades": str(args.live_trades),
                "candidates": [c.__dict__ for c in CANDIDATES],
                "thresholds": {
                    "min_pred_trades": args.min_pred_trades,
                    "min_live_proxy_trades": args.min_live_proxy_trades,
                    "min_live_official_trades": args.min_live_official_trades,
                    "min_sharpe": args.min_sharpe,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary.round(4).to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
