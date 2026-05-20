#!/usr/bin/env python3
"""Diagnose BTC15M F2 transfer from Predexon April to live websocket replay.

The intent is not to discover a new parameter by looking at the websocket
holdout.  It replays the already-frozen F2 predicate and evaluates a small,
fixed filter set that was motivated by April diagnostics:

    side_fair_p >= 0.60
    fair_edge_cents >= 12
    5 <= ttl_min <= 12
    spread <= 2c
    2c <= entry <= 60c

For websocket data, this script consumes an existing F2 replay directory from
`backtest_btc15m_f2_live_ws_holdout.py` and uses the proxy +2c PnL for broad
coverage.  Official-only summaries should still be checked from that replay
directory before deployment decisions.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = (
    PROJECT_ROOT
    / "backtest_outputs"
    / "btc15m_april_multisplit_search_20260515"
    / "side_candidates_april.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_f2_transfer_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class FilterSpec:
    name: str
    rationale: str
    fn: Callable[[pd.DataFrame], pd.Series]


def utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def latest_ws_dir() -> Path:
    paths = sorted(PROJECT_ROOT.glob("backtest_outputs/btc15m_f2_exact_live_ws_refresh_*"), key=lambda p: p.stat().st_mtime)
    if not paths:
        paths = sorted(PROJECT_ROOT.glob("backtest_outputs/btc15m_fair_ttl5_12_live_ws_*"), key=lambda p: p.stat().st_mtime)
    if not paths:
        raise FileNotFoundError("No exact F2 websocket replay directory found")
    return paths[-1]


def max_dd(pnl: pd.Series) -> float:
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


def summarize(name: str, d: pd.DataFrame, pnl_col: str = "pnl_stress") -> dict[str, object]:
    pnl = pd.to_numeric(d[pnl_col], errors="coerce") if len(d) else pd.Series(dtype=float)
    wins = pd.to_numeric(d["win"], errors="coerce") if len(d) else pd.Series(dtype=float)
    premium = pd.to_numeric(d.get("premium", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    return {
        "sample": name,
        "trades": int(len(d)),
        "pnl": round(float(pnl.sum()), 4) if len(d) else 0.0,
        "premium": round(float(premium.sum()), 4) if len(d) else 0.0,
        "rop": round(float(pnl.sum() / premium.sum()), 4) if float(premium.sum()) > 0 else 0.0,
        "win_rate": round(float(wins.mean()), 4) if len(d) else 0.0,
        "max_dd": round(max_dd(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(d.get("entry_price", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(d)
        else 0.0,
        "avg_edge": round(float(pd.to_numeric(d.get("fair_edge_cents", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(d)
        else 0.0,
        "avg_qty": round(float(pd.to_numeric(d.get("visible_qty", pd.Series(dtype=float)), errors="coerce").mean()), 4)
        if len(d)
        else 0.0,
    }


def stress_pnl(d: pd.DataFrame, cents: float = 2.0) -> pd.Series:
    entry = (pd.to_numeric(d["entry_price"], errors="coerce") + cents / 100.0).clip(upper=0.99)
    # F2 artifacts use one-contract taker fees rounded to cents. Stress keeps
    # the original fee approximation simple: one extra 2c in entry cost.
    base = pd.to_numeric(d["pnl"], errors="coerce")
    return base - cents / 100.0


def load_april(candidates: Path) -> dict[str, pd.DataFrame]:
    cols = [
        "available_at",
        "event_ticker",
        "market_ticker",
        "side",
        "entry_price",
        "entry_fee",
        "premium",
        "visible_qty",
        "result",
        "win",
        "pnl",
        "side_fair_p",
        "fair_edge_cents",
        "ttl_min",
        "spread_cents",
        "sequence",
        "close_time",
        "side_btc_3m_bps",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "side_micropressure",
        "side_depth_imbalance",
        "rv_60m",
        "distance_bps",
    ]
    c = pd.read_parquet(candidates, columns=cols)
    c["available_at"] = utc(c["available_at"])
    c["close_time"] = utc(c["close_time"])
    for col in [
        "entry_price",
        "entry_fee",
        "premium",
        "visible_qty",
        "pnl",
        "side_fair_p",
        "fair_edge_cents",
        "ttl_min",
        "spread_cents",
        "side_btc_3m_bps",
        "side_mid_chg_2m",
        "side_mid_chg_3m",
        "side_micropressure",
        "side_depth_imbalance",
        "rv_60m",
        "distance_bps",
    ]:
        c[col] = pd.to_numeric(c[col], errors="coerce")
    mask = (
        c["side_fair_p"].ge(0.60)
        & c["fair_edge_cents"].ge(12.0)
        & c["ttl_min"].between(5.0, 12.0)
        & c["spread_cents"].le(2.0)
        & c["entry_price"].between(0.02, 0.60)
        & c["visible_qty"].fillna(0).ge(1)
    )
    c = (
        c.loc[mask]
        .sort_values(["event_ticker", "available_at", "sequence", "side", "market_ticker"])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )
    c["win"] = c["win"].astype(bool)
    c["pnl_stress"] = stress_pnl(c, 2.0)
    return {
        "apr01_14": c[c["available_at"].ge("2026-04-01") & c["available_at"].lt("2026-04-15")].copy(),
        "apr15_30": c[c["available_at"].ge("2026-04-15") & c["available_at"].lt("2026-05-01")].copy(),
    }


def load_ws(ws_dir: Path) -> pd.DataFrame:
    path = ws_dir / "f2_live_ws_trades.parquet"
    d = pd.read_parquet(path)
    d["available_at"] = utc(d["received_at_utc"])
    d["close_time"] = utc(d["close_time"])
    d["win"] = pd.to_numeric(d["win_pnl_proxy_2c"], errors="coerce").astype(bool)
    d["pnl"] = pd.to_numeric(d["pnl_proxy_0c"], errors="coerce")
    d["pnl_stress"] = pd.to_numeric(d["pnl_proxy_2c"], errors="coerce")
    d["premium"] = pd.to_numeric(d["premium"], errors="coerce")
    d["ttl_min"] = (d["close_time"] - d["available_at"]).dt.total_seconds() / 60.0
    for col in ["entry_price", "visible_qty", "side_fair_p", "fair_edge_cents", "ttl_min", "spread_cents", "btc_spot_age_sec", "rv_60m"]:
        if col in d:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    return d.reset_index(drop=True)


def filters() -> list[FilterSpec]:
    return [
        FilterSpec("base_f2", "Frozen exact F2, no extra filter.", lambda d: pd.Series(True, index=d.index)),
        FilterSpec("ttl_10_12", "Late but not last-minute: strongest bucket in April and May diagnostics.", lambda d: d["ttl_min"].between(10.0, 12.0)),
        FilterSpec("entry_le55_q50", "Avoid highest entries and weak visible top liquidity.", lambda d: d["entry_price"].le(0.55) & d["visible_qty"].ge(50)),
        FilterSpec("fp65_entry_le55_q50", "April postfilter: higher fair probability, entry cap, visible qty.", lambda d: d["side_fair_p"].ge(0.65) & d["entry_price"].le(0.55) & d["visible_qty"].ge(50)),
        FilterSpec("edge14_entry_le55_q50", "Higher edge cushion with same execution quality filter.", lambda d: d["fair_edge_cents"].ge(14) & d["entry_price"].le(0.55) & d["visible_qty"].ge(50)),
        FilterSpec("edge20_entry_le55_q50", "Very high edge cushion, low capacity but sanity check.", lambda d: d["fair_edge_cents"].ge(20) & d["entry_price"].le(0.55) & d["visible_qty"].ge(50)),
        FilterSpec("ttl_10_12_entry_le55_q50", "Intersection of robust TTL bucket and execution quality.", lambda d: d["ttl_min"].between(10.0, 12.0) & d["entry_price"].le(0.55) & d["visible_qty"].ge(50)),
    ]


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    text = df.astype(str)
    headers = list(text.columns)
    rows = text.values.tolist()
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    header = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    sep = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    body = ["| " + " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def streak_rows(name: str, d: pd.DataFrame, pnl_col: str = "pnl_stress") -> list[dict[str, object]]:
    d = d.sort_values("available_at").reset_index(drop=True).copy()
    rows: list[dict[str, object]] = []
    if d.empty:
        return rows
    wins = d["win"].astype(bool).to_numpy()
    pnl = pd.to_numeric(d[pnl_col], errors="coerce").fillna(0.0).to_numpy()
    prev_loss_count = 0
    prev_win_count = 0
    enriched = []
    for i, w in enumerate(wins):
        enriched.append(
            {
                "sample": name,
                "i": i,
                "prev_loss_streak": prev_loss_count,
                "prev_win_streak": prev_win_count,
                "win": bool(w),
                "pnl": float(pnl[i]),
            }
        )
        if w:
            prev_win_count += 1
            prev_loss_count = 0
        else:
            prev_loss_count += 1
            prev_win_count = 0
    e = pd.DataFrame(enriched)
    for label, mask in {
        "all": pd.Series(True, index=e.index),
        "after_1_loss": e["prev_loss_streak"].eq(1),
        "after_2plus_losses": e["prev_loss_streak"].ge(2),
        "after_1_win": e["prev_win_streak"].eq(1),
        "after_2plus_wins": e["prev_win_streak"].ge(2),
    }.items():
        g = e.loc[mask]
        rows.append(
            {
                "sample": name,
                "condition": label,
                "trades": int(len(g)),
                "pnl": round(float(g["pnl"].sum()), 4) if len(g) else 0.0,
                "win_rate": round(float(g["win"].mean()), 4) if len(g) else 0.0,
            }
        )
    # Run length distribution.
    run_type = "win" if wins[0] else "loss"
    run_len = 0
    max_win = 0
    max_loss = 0
    for w in wins:
        typ = "win" if w else "loss"
        if typ != run_type:
            if run_type == "win":
                max_win = max(max_win, run_len)
            else:
                max_loss = max(max_loss, run_len)
            run_type = typ
            run_len = 0
        run_len += 1
    if run_type == "win":
        max_win = max(max_win, run_len)
    else:
        max_loss = max(max_loss, run_len)
    rows.append({"sample": name, "condition": "max_win_streak", "trades": max_win, "pnl": 0.0, "win_rate": 0.0})
    rows.append({"sample": name, "condition": "max_loss_streak", "trades": max_loss, "pnl": 0.0, "win_rate": 0.0})
    return rows


def bucket_summary(sample: str, d: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    specs = {
        "entry": pd.cut(d["entry_price"], [-0.001, 0.4, 0.5, 0.55, 0.6, 1.0]),
        "ttl": pd.cut(d["ttl_min"], [-0.001, 6, 8, 10, 12, 15]),
        "fair_p": pd.cut(d["side_fair_p"], [0.599, 0.65, 0.7, 0.8, 1.0]),
        "edge": pd.cut(d["fair_edge_cents"], [12, 14, 18, 25, 100], include_lowest=True),
        "visible_qty": pd.cut(d["visible_qty"], [-0.001, 10, 50, 250, 1000, 1e12]),
        "side": d["side"].astype(str).str.lower(),
    }
    for btype, bins in specs.items():
        tmp = d.copy()
        tmp["bucket"] = bins.astype(str)
        for bucket, g in tmp.groupby("bucket", dropna=False):
            r = summarize(sample, g)
            r["bucket_type"] = btype
            r["bucket"] = bucket
            rows.append(r)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    ap.add_argument("--ws-dir", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    ws_dir = args.ws_dir or latest_ws_dir()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    samples = load_april(args.candidates)
    samples["ws_may_refresh"] = load_ws(ws_dir)

    filter_rows: list[dict[str, object]] = []
    filtered_trades = []
    for filt in filters():
        for sample_name, df in samples.items():
            chosen = df.loc[filt.fn(df).fillna(False)].copy()
            row = {"filter": filt.name, "rationale": filt.rationale}
            row.update(summarize(sample_name, chosen))
            filter_rows.append(row)
            chosen["sample"] = sample_name
            chosen["filter"] = filt.name
            filtered_trades.append(chosen)
    filter_summary = pd.DataFrame(filter_rows)
    filter_summary.to_csv(args.out_dir / "filter_summary.csv", index=False)
    if filtered_trades:
        pd.concat(filtered_trades, ignore_index=True).to_parquet(args.out_dir / "filtered_trades.parquet", index=False, compression="zstd")

    streaks = pd.DataFrame([r for sample, df in samples.items() for r in streak_rows(sample, df)])
    streaks.to_csv(args.out_dir / "streak_summary.csv", index=False)

    buckets = pd.concat([bucket_summary(name, df) for name, df in samples.items()], ignore_index=True)
    buckets.to_csv(args.out_dir / "bucket_summary.csv", index=False)

    lines = [
        "# BTC15M F2 Transfer Diagnostics",
        "",
        f"- Created UTC: {datetime.now(timezone.utc).isoformat()}",
        f"- April candidates: `{args.candidates}`",
        f"- Websocket replay dir: `{ws_dir}`",
        "- PnL column: one-contract, taker fee included, +2c adverse-entry stress.",
        "- Websocket settlement: proxy +2c for broad coverage; check replay's official subset separately.",
        "",
        "## Fixed Filter Summary",
        "",
        markdown_table(filter_summary[
            [
                "filter",
                "sample",
                "trades",
                "pnl",
                "win_rate",
                "max_dd",
                "sharpe",
                "avg_entry",
                "avg_edge",
                "avg_qty",
            ]
        ]),
        "",
        "## Streak Summary",
        "",
        markdown_table(streaks),
        "",
        "## Initial Interpretation",
        "",
        "- If a filter is positive on Apr 1-14 and Apr 15-30 but negative on the refreshed websocket sample, treat it as a Predexon-to-live transfer failure, not deployable alpha.",
        "- If a streak condition looks strong only in one sample, it is not evidence of a tradable state variable.",
        "- Filters are intentionally fixed and coarse; this script should not be used to tune against websocket holdout.",
    ]
    (args.out_dir / "diagnostics.md").write_text("\n".join(lines), encoding="utf-8")
    print(filter_summary[filter_summary["sample"].eq("ws_may_refresh")].to_string(index=False), flush=True)
    print(f"wrote {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
