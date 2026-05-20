#!/usr/bin/env python3
"""Audit REST-official coverage for materialized BTC15M Predexon candidates.

This is a deployment-control diagnostic, not a candidate search. It makes the
partial nature of historical REST official fills explicit: April/May rows may
have Kalshi official results while January rows can be REST 404 and therefore
cannot be counted as official-settled promotion evidence.
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
    / "btc15m_predexon_rest_official_20260517_codex"
    / "predexon_trades_rest_official.parquet"
)
DEFAULT_MARKET_RESULTS = (
    BACKTEST_ROOT
    / "btc15m_predexon_rest_official_20260517_codex"
    / "market_results.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_predexon_official_coverage_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Candidate:
    name: str
    strategy: str
    side_mode: str
    min_visible_qty: float
    min_side_fair_p: float = 0.60
    min_edge_cents: float = 12.0
    max_entry: float = 0.50
    max_spread_cents: float = 2.0
    ttl_min: float = 10.0
    ttl_max: float = 12.0


CANDIDATES = [
    Candidate("q250_both", "ttl10_12_entry50_q250", "both", 250.0),
    Candidate("q250_firstskip_qty500", "ttl10_12_entry50_q250", "both", 500.0),
    Candidate("q250_yes", "ttl10_12_entry50_q250", "yes", 250.0),
    Candidate("q500_both", "ttl10_12_entry50_q500", "both", 500.0),
    Candidate("q1000_both", "ttl10_12_entry50_q1000", "both", 1000.0),
    Candidate("q1000_yes", "ttl10_12_entry50_q1000", "yes", 1000.0),
    Candidate("q250_qspeed05_both", "ttl10_12_entry50_q250_qspeed05", "both", 250.0),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BTC15M Predexon REST-official coverage.")
    parser.add_argument("--predexon-trades", type=Path, default=DEFAULT_PREDEXON)
    parser.add_argument("--market-results", type=Path, default=DEFAULT_MARKET_RESULTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-official-rows", type=int, default=50)
    return parser.parse_args()


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
        return {"rows": 0, "pnl": 0.0, "win_rate": 0.0, "max_dd": 0.0, "sharpe": 0.0}
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").dropna()
    work = df.loc[pnl.index].copy()
    if win_col and win_col in work:
        wins = pd.to_numeric(work[win_col], errors="coerce")
    else:
        wins = pd.to_numeric(work.get("win", pd.Series(dtype=float)), errors="coerce")
    return {
        "rows": int(len(work)),
        "pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "win_rate": float(wins.mean()) if len(wins) else 0.0,
        "max_dd": max_drawdown(pnl.reset_index(drop=True)),
        "sharpe": sharpe(pnl),
    }


def apply_candidate(trades: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    out = trades[trades["strategy"].astype(str).eq(candidate.strategy)].copy()
    if out.empty:
        return out
    side = out["side"].astype(str).str.lower()
    if candidate.side_mode == "yes":
        mask = side.eq("yes")
    elif candidate.side_mode == "no":
        mask = side.eq("no")
    else:
        mask = side.isin(["yes", "no"])
    mask &= pd.to_numeric(out["side_fair_p"], errors="coerce").ge(candidate.min_side_fair_p)
    mask &= pd.to_numeric(out["fair_edge_cents"], errors="coerce").ge(candidate.min_edge_cents)
    mask &= pd.to_numeric(out["entry_price"], errors="coerce").le(candidate.max_entry)
    mask &= pd.to_numeric(out["visible_qty"], errors="coerce").ge(candidate.min_visible_qty)
    mask &= pd.to_numeric(out["spread_cents"], errors="coerce").le(candidate.max_spread_cents)
    mask &= pd.to_numeric(out["ttl_min"], errors="coerce").between(candidate.ttl_min, candidate.ttl_max)
    sort_cols = [col for col in ["available_at", "timestamp_utc", "event_ticker"] if col in out.columns]
    return out.loc[mask].sort_values(sort_cols).reset_index(drop=True)


def official_mask(df: pd.DataFrame) -> pd.Series:
    return df["official_result_rest"].astype(str).str.lower().isin(["yes", "no"])


def market_result_summary(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    market_results = pd.read_csv(path)
    rows = []
    for column in ["status", "fetch_error", "result"]:
        if column not in market_results:
            continue
        counts = market_results[column].fillna("").astype(str).value_counts(dropna=False)
        for value, count in counts.items():
            rows.append({"field": column, "value": value, "count": int(count)})
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trades = pd.read_parquet(args.predexon_trades).copy()

    summary_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    selected_rows: list[pd.DataFrame] = []

    for candidate in CANDIDATES:
        selected = apply_candidate(trades, candidate)
        has_official = official_mask(selected) if not selected.empty else pd.Series(dtype=bool)
        official = selected.loc[has_official].copy()
        uncovered = selected.loc[~has_official].copy()

        proxy_all = metrics(selected, "pnl_stress", "win")
        official_m = metrics(official, "pnl_official_rest_2c", "win_pnl_official_rest_2c")
        uncovered_proxy = metrics(uncovered, "pnl_stress", "win")

        bad_uncovered_windows = 0
        for window, group in selected.groupby("window", dropna=False):
            group_has_official = official_mask(group)
            group_official = group.loc[group_has_official]
            group_uncovered = group.loc[~group_has_official]
            group_proxy = metrics(group, "pnl_stress", "win")
            group_official_m = metrics(group_official, "pnl_official_rest_2c", "win_pnl_official_rest_2c")
            group_uncovered_proxy = metrics(group_uncovered, "pnl_stress", "win")
            if group_uncovered_proxy["rows"] >= 2 and group_uncovered_proxy["pnl"] < 0:
                bad_uncovered_windows += 1
            window_rows.append(
                {
                    "candidate": candidate.name,
                    "window": window,
                    "rows": int(len(group)),
                    "official_rows": int(group_has_official.sum()),
                    "official_coverage_rate": float(group_has_official.mean()) if len(group) else 0.0,
                    "proxy_pnl_all": group_proxy["pnl"],
                    "official_pnl_covered": group_official_m["pnl"],
                    "uncovered_proxy_rows": group_uncovered_proxy["rows"],
                    "uncovered_proxy_pnl": group_uncovered_proxy["pnl"],
                    "uncovered_proxy_win_rate": group_uncovered_proxy["win_rate"],
                }
            )

        blockers: list[str] = []
        if official_m["rows"] < args.min_official_rows:
            blockers.append("too_few_rest_official_pred_rows")
        if len(selected) and official_m["rows"] < len(selected):
            blockers.append("partial_rest_official_coverage")
        if uncovered_proxy["rows"] > 0:
            blockers.append("uncovered_rows_proxy_only")
        if uncovered_proxy["pnl"] < 0:
            blockers.append("uncovered_proxy_pnl_negative")
        if bad_uncovered_windows:
            blockers.append("uncovered_proxy_bad_windows")
        if official_m["pnl"] <= 0:
            blockers.append("covered_official_pnl_not_positive")

        official_plus_uncovered_proxy_pnl = official_m["pnl"] + uncovered_proxy["pnl"]
        row = {
            **asdict(candidate),
            "selected_rows": int(len(selected)),
            "rest_official_rows": official_m["rows"],
            "rest_official_coverage_rate": float(has_official.mean()) if len(selected) else 0.0,
            "rest_official_pnl_2c": official_m["pnl"],
            "rest_official_win_rate": official_m["win_rate"],
            "rest_official_max_dd": official_m["max_dd"],
            "rest_official_sharpe": official_m["sharpe"],
            "proxy_all_pnl_2c": proxy_all["pnl"],
            "proxy_all_win_rate": proxy_all["win_rate"],
            "proxy_all_max_dd": proxy_all["max_dd"],
            "proxy_all_sharpe": proxy_all["sharpe"],
            "uncovered_rows": uncovered_proxy["rows"],
            "uncovered_proxy_pnl_2c": uncovered_proxy["pnl"],
            "uncovered_proxy_win_rate": uncovered_proxy["win_rate"],
            "uncovered_proxy_max_dd": uncovered_proxy["max_dd"],
            "official_plus_uncovered_proxy_pnl_2c": official_plus_uncovered_proxy_pnl,
            "bad_uncovered_windows": int(bad_uncovered_windows),
            "deployment_blockers": ";".join(blockers),
            "deployment_usable": False,
        }
        summary_rows.append(row)
        if not selected.empty:
            tmp = selected.copy()
            tmp["candidate"] = candidate.name
            tmp["has_rest_official_result"] = has_official.to_numpy()
            selected_rows.append(tmp)

    summary = pd.DataFrame(summary_rows).sort_values(
        ["deployment_usable", "rest_official_pnl_2c", "selected_rows"],
        ascending=[False, False, False],
    )
    by_window = pd.DataFrame(window_rows).sort_values(["candidate", "window"])
    market_summary = market_result_summary(args.market_results)

    summary.to_csv(args.out_dir / "predexon_official_coverage_summary.csv", index=False)
    by_window.to_csv(args.out_dir / "predexon_official_coverage_by_window.csv", index=False)
    if not market_summary.empty:
        market_summary.to_csv(args.out_dir / "market_result_status_summary.csv", index=False)
    if selected_rows:
        pd.concat(selected_rows, ignore_index=True).to_parquet(
            args.out_dir / "predexon_coverage_selected_trades.parquet",
            index=False,
            compression="zstd",
        )

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predexon_trades": str(args.predexon_trades),
        "market_results": str(args.market_results),
        "candidates": [candidate.name for candidate in CANDIDATES],
        "rows": int(len(trades)),
        "min_official_rows": args.min_official_rows,
        "note": "Diagnostic only. REST-official covered rows are evidence; uncovered rows are stress/coverage warnings, not promotion support.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC15M Predexon REST-Official Coverage Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Candidate Summary",
        "",
        summary.round(4).to_string(index=False),
        "",
        "## Market Result Status",
        "",
        market_summary.to_string(index=False) if not market_summary.empty else "No market-results file available.",
        "",
        "## Interpretation",
        "",
        "- `deployment_usable` is intentionally false for all rows; this audit is diagnostic.",
        "- REST-official covered rows can support research ranking, but uncovered rows cannot be promoted as official settlement evidence.",
        "- Negative uncovered proxy windows are stress warnings, especially for January REST 404 rows.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.round(4).to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
