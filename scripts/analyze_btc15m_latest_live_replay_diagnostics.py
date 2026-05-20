#!/usr/bin/env python3
"""Diagnostics for latest BTC15M live replay REST-official rows.

This is not a search script. It audits focused, pre-existing cuts on already
materialized live replay trades so proxy/official settlement failures are easy
to see before anyone mistakes a green proxy result for deployment evidence.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_Q250 = (
    BACKTEST_ROOT
    / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_Q1000 = (
    BACKTEST_ROOT
    / "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_latest_live_replay_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit latest BTC15M live replay REST-official diagnostics.")
    parser.add_argument("--q250-trades", type=Path, default=DEFAULT_Q250)
    parser.add_argument("--q1000-trades", type=Path, default=DEFAULT_Q1000)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def load(path: Path, fallback_candidate: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path).copy()
    if "candidate" not in df:
        df["candidate"] = fallback_candidate
    for col in ["received_at_utc", "close_time"]:
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    return df


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


def aligned_distance_bps(df: pd.DataFrame) -> pd.Series:
    spot = pd.to_numeric(df.get("btc_spot_model"), errors="coerce")
    strike = pd.to_numeric(df.get("floor_strike"), errors="coerce")
    raw = 10000.0 * np.log(spot / strike)
    side = df.get("side", pd.Series(index=df.index, dtype=str)).astype(str).str.lower()
    return raw.where(side.eq("yes"), -raw)


def summarize(df: pd.DataFrame, candidate: str, label: str) -> dict[str, Any]:
    official = pd.to_numeric(df.get("pnl_official_rest_2c"), errors="coerce")
    proxy = pd.to_numeric(df.get("pnl_proxy_2c"), errors="coerce")
    valid = official.notna()
    work = df.loc[valid].copy()
    official = official.loc[valid]
    proxy = proxy.loc[valid]
    wins = pd.to_numeric(work.get("win_pnl_official_rest_2c"), errors="coerce")
    result = work.get("official_result_filled", pd.Series(index=work.index, dtype=str)).astype(str).str.lower()
    proxy_result = work.get("proxy_result", pd.Series(index=work.index, dtype=str)).astype(str).str.lower()
    both = result.isin(["yes", "no"]) & proxy_result.isin(["yes", "no"])
    mismatches = both & ~result.eq(proxy_result)
    return {
        "candidate": candidate,
        "diagnostic": label,
        "trades": int(len(work)),
        "official_pnl": round(float(official.sum()), 4) if len(official) else 0.0,
        "proxy_pnl": round(float(proxy.sum()), 4) if len(proxy) else 0.0,
        "official_minus_proxy_pnl": round(float(official.sum() - proxy.sum()), 4) if len(official) else 0.0,
        "official_win_rate": round(float(wins.mean()), 4) if len(wins.dropna()) else 0.0,
        "official_max_dd": round(max_drawdown(official.reset_index(drop=True)), 4),
        "official_sharpe": round(sharpe(official), 4),
        "official_proxy_both_results": int(both.sum()),
        "official_proxy_result_mismatches": int(mismatches.sum()),
        "near_strike_abs_le_10_rows": int(work.get("near_strike_proxy", pd.Series(False, index=work.index)).fillna(False).astype(bool).sum()),
    }


def candidate_diagnostics(df: pd.DataFrame, candidate: str) -> list[dict[str, Any]]:
    if df.empty:
        return []
    work = df.copy()
    work["aligned_distance_bps"] = aligned_distance_bps(work)
    rows = [summarize(work, candidate, "all")]
    for side in ["yes", "no"]:
        rows.append(summarize(work[work["side"].astype(str).str.lower().eq(side)], candidate, f"side_{side}"))
    rows.append(summarize(work[work["aligned_distance_bps"].ge(0.0)], candidate, "aligned_dist_ge_0bps"))
    rows.append(summarize(work[work["aligned_distance_bps"].ge(5.0)], candidate, "aligned_dist_ge_5bps"))
    rows.append(
        summarize(
            work[~work.get("near_strike_proxy", pd.Series(False, index=work.index)).fillna(False).astype(bool)],
            candidate,
            "exclude_proxy_abs_distance_le_10usd",
        )
    )
    return rows


def mismatch_rows(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "candidate",
        "received_at_utc",
        "close_time",
        "market_ticker",
        "side",
        "entry_price",
        "visible_qty",
        "side_fair_p",
        "fair_edge_cents",
        "btc_spot_model",
        "floor_strike",
        "aligned_distance_bps",
        "official_result_filled",
        "proxy_result",
        "pnl_official_rest_2c",
        "pnl_proxy_2c",
        "proxy_distance_usd",
        "near_strike_proxy",
    ]
    if df.empty:
        return pd.DataFrame(columns=cols)
    result = df.get("official_result_filled", pd.Series(index=df.index, dtype=str)).astype(str).str.lower()
    proxy = df.get("proxy_result", pd.Series(index=df.index, dtype=str)).astype(str).str.lower()
    out = df.loc[result.isin(["yes", "no"]) & proxy.isin(["yes", "no"]) & ~result.eq(proxy)].copy()
    if out.empty:
        return pd.DataFrame(columns=cols)
    out["aligned_distance_bps"] = aligned_distance_bps(out)
    return out[[col for col in cols if col in out.columns]].sort_values("close_time")


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    q250 = load(args.q250_trades, "q250_firstskip_qty500")
    q1000 = load(args.q1000_trades, "q1000_yes")
    rows = []
    rows.extend(candidate_diagnostics(q250, "q250_firstskip_qty500"))
    rows.extend(candidate_diagnostics(q1000, "q1000_yes"))
    summary = pd.DataFrame(rows)
    mismatch_frames = [mismatch_rows(q250), mismatch_rows(q1000)]
    nonempty_mismatch_frames = [frame for frame in mismatch_frames if not frame.empty]
    mismatches = (
        pd.concat(nonempty_mismatch_frames, ignore_index=True)
        if nonempty_mismatch_frames
        else mismatch_frames[0].copy()
    )
    summary.to_csv(args.out_dir / "diagnostic_summary.csv", index=False)
    mismatches.to_csv(args.out_dir / "proxy_official_mismatches.csv", index=False)
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "q250_trades": str(args.q250_trades),
        "q1000_trades": str(args.q1000_trades),
        "q250_rows": int(len(q250)),
        "q1000_rows": int(len(q1000)),
        "diagnostic_rows": int(len(summary)),
        "mismatch_rows": int(len(mismatches)),
        "note": "Diagnostics only. Pre-existing side/distance/near-strike cuts; do not promote from this live-row audit.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M Latest Live Replay Diagnostics",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        summary.round(4).to_string(index=False) if not summary.empty else "_No rows._",
        "",
        "## Proxy/Official Mismatches",
        "",
        mismatches.round(4).to_string(index=False) if not mismatches.empty else "_No mismatches._",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(run_info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
