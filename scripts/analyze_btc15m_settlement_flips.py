#!/usr/bin/env python3
"""Explain BTC15M proxy-vs-official settlement flips.

This is an audit artifact, not a strategy search. It joins replay trade rows to
Kalshi REST market expiration values and writes row-level tables showing where
Coinbase/Kraken proxy labels disagree with official settlement.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_BROAD_TRADES = BACKTEST_ROOT / "btc15m_live_ws_rest_official_refresh_20260516_183818" / "live_ws_trades_rest_official.parquet"
DEFAULT_MARKET_RESULTS = BACKTEST_ROOT / "btc15m_live_ws_rest_official_refresh_20260516_183818" / "market_results.csv"
DEFAULT_MATERIALIZED_TRADES = (
    BACKTEST_ROOT
    / "btc15m_materialized_filter_grid_pred_official_20260517_codex"
    / "materialized_filter_live_trades.parquet"
)
DEFAULT_MATERIALIZED_SUMMARY = (
    BACKTEST_ROOT
    / "btc15m_materialized_filter_grid_pred_official_20260517_codex"
    / "materialized_filter_summary.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_settlement_flip_attribution_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


ROW_COLUMNS = [
    "source",
    "candidate",
    "rule",
    "event_ticker",
    "market_ticker",
    "side",
    "floor_strike",
    "entry_price",
    "entry_stress_2c",
    "entry_fee",
    "premium",
    "visible_qty",
    "spread_cents",
    "ttl_min",
    "side_fair_p",
    "fair_edge_cents",
    "received_at_utc",
    "close_time",
    "btc_spot_model",
    "decision_distance_usd",
    "side_decision_distance_usd",
    "close_btc_spot",
    "expiration_value",
    "basis_official_minus_proxy_usd",
    "proxy_distance_usd",
    "official_distance_usd",
    "proxy_result",
    "official_result_filled",
    "proxy_official_flip",
    "win_pnl_proxy_2c",
    "pnl_proxy_2c",
    "win_pnl_official_rest_2c",
    "pnl_official_rest_2c",
    "pnl_delta_official_minus_proxy_2c",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC15M settlement flip attribution tables.")
    p.add_argument("--broad-trades", type=Path, default=DEFAULT_BROAD_TRADES)
    p.add_argument("--market-results", type=Path, default=DEFAULT_MARKET_RESULTS)
    p.add_argument("--materialized-trades", type=Path, default=DEFAULT_MATERIALIZED_TRADES)
    p.add_argument("--materialized-summary", type=Path, default=DEFAULT_MATERIALIZED_SUMMARY)
    p.add_argument("--materialized-rule", default="", help="Rule id to audit; defaults to first row in materialized summary.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def norm_result(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().replace({"nan": "", "none": ""})


def load_market_results(path: Path) -> pd.DataFrame:
    market = pd.read_csv(path)
    keep = ["market_ticker", "result", "expiration_value", "status", "settlement_ts"]
    return market[[c for c in keep if c in market.columns]].copy()


def choose_materialized_rule(summary_path: Path, requested: str) -> str:
    if requested:
        return requested
    if not summary_path.exists():
        return ""
    summary = pd.read_csv(summary_path)
    if summary.empty or "name" not in summary.columns:
        return ""
    return str(summary.iloc[0]["name"])


def enrich(trades: pd.DataFrame, market_results: pd.DataFrame, source: str) -> pd.DataFrame:
    out = trades.copy()
    out["source"] = source
    if "rule" not in out.columns:
        out["rule"] = ""
    out = out.merge(
        market_results.rename(
            columns={
                "result": "rest_result_market",
                "status": "rest_market_status",
                "expiration_value": "rest_expiration_value",
            }
        ),
        on="market_ticker",
        how="left",
    )
    if "expiration_value" not in out.columns:
        out["expiration_value"] = np.nan
    out["expiration_value"] = pd.to_numeric(out["expiration_value"], errors="coerce")
    out["expiration_value"] = out["expiration_value"].fillna(pd.to_numeric(out.get("rest_expiration_value"), errors="coerce"))
    if "official_result_filled" not in out.columns:
        out["official_result_filled"] = out.get("official_result_rest", out.get("official_result", ""))
    out["official_result_filled"] = norm_result(out["official_result_filled"])
    out["proxy_result"] = norm_result(out.get("proxy_result", ""))
    out["proxy_official_flip"] = (
        out["proxy_result"].isin(["yes", "no"])
        & out["official_result_filled"].isin(["yes", "no"])
        & out["proxy_result"].ne(out["official_result_filled"])
    )
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    out["entry_stress_2c"] = out["entry_price"] + 0.02
    out["floor_strike"] = pd.to_numeric(out["floor_strike"], errors="coerce")
    out["btc_spot_model"] = pd.to_numeric(out.get("btc_spot_model"), errors="coerce")
    out["close_btc_spot"] = pd.to_numeric(out.get("close_btc_spot"), errors="coerce")
    out["decision_distance_usd"] = out["btc_spot_model"] - out["floor_strike"]
    out["side_decision_distance_usd"] = np.where(
        out["side"].astype(str).str.lower().eq("yes"),
        out["decision_distance_usd"],
        -out["decision_distance_usd"],
    )
    out["official_distance_usd"] = out["expiration_value"] - out["floor_strike"]
    out["basis_official_minus_proxy_usd"] = out["expiration_value"] - out["close_btc_spot"]
    out["pnl_delta_official_minus_proxy_2c"] = (
        pd.to_numeric(out.get("pnl_official_rest_2c"), errors="coerce")
        - pd.to_numeric(out.get("pnl_proxy_2c"), errors="coerce")
    )
    for col in ROW_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return out[ROW_COLUMNS].copy()


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["pnl_proxy_2c"] = pd.to_numeric(work["pnl_proxy_2c"], errors="coerce")
    work["pnl_official_rest_2c"] = pd.to_numeric(work["pnl_official_rest_2c"], errors="coerce")
    work["pnl_delta_official_minus_proxy_2c"] = pd.to_numeric(work["pnl_delta_official_minus_proxy_2c"], errors="coerce")
    rows: list[dict[str, Any]] = []
    for key, group in work.groupby(group_cols, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        row = dict(zip(group_cols, key_tuple, strict=False))
        row.update(
            {
                "rows": int(len(group)),
                "flips": int(group["proxy_official_flip"].sum()),
                "flip_rate": float(group["proxy_official_flip"].mean()) if len(group) else 0.0,
                "proxy_pnl_2c": float(group["pnl_proxy_2c"].sum()),
                "official_pnl_2c": float(group["pnl_official_rest_2c"].sum()),
                "pnl_delta_official_minus_proxy_2c": float(group["pnl_delta_official_minus_proxy_2c"].sum()),
                "mean_basis_usd": float(pd.to_numeric(group["basis_official_minus_proxy_usd"], errors="coerce").mean()),
                "median_abs_decision_distance_usd": float(pd.to_numeric(group["decision_distance_usd"], errors="coerce").abs().median()),
                "median_visible_qty": float(pd.to_numeric(group["visible_qty"], errors="coerce").median()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(group_cols).reset_index(drop=True)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    market_results = load_market_results(args.market_results)

    broad = enrich(pd.read_parquet(args.broad_trades), market_results, "broad_live_replay")

    materialized_rule = choose_materialized_rule(args.materialized_summary, args.materialized_rule)
    materialized = pd.DataFrame(columns=ROW_COLUMNS)
    if args.materialized_trades.exists() and materialized_rule:
        mt = pd.read_parquet(args.materialized_trades)
        mt = mt[mt["rule"].astype(str).eq(materialized_rule)].copy()
        materialized = enrich(mt, market_results, f"materialized_{materialized_rule}")

    all_rows = pd.concat([broad, materialized], ignore_index=True)
    flips = all_rows[all_rows["proxy_official_flip"]].copy()
    summaries = {
        "summary_by_source_candidate_side": summarize(all_rows, ["source", "candidate", "side"]),
        "summary_by_source_candidate": summarize(all_rows, ["source", "candidate"]),
        "flip_summary_by_candidate_side": summarize(flips, ["source", "candidate", "side"]),
    }

    all_rows.to_csv(args.out_dir / "settlement_attribution_rows.csv", index=False)
    flips.to_csv(args.out_dir / "settlement_flip_rows.csv", index=False)
    for name, df in summaries.items():
        df.to_csv(args.out_dir / f"{name}.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "broad_trades": str(args.broad_trades),
        "market_results": str(args.market_results),
        "materialized_trades": str(args.materialized_trades),
        "materialized_rule": materialized_rule,
        "rows": int(len(all_rows)),
        "flips": int(len(flips)),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC15M Settlement Flip Attribution",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Broad rows: `{len(broad)}`",
        f"Materialized rule: `{materialized_rule}` rows `{len(materialized)}`",
        f"Total flips: `{len(flips)}`",
        "",
        "## Summary By Source/Candidate/Side",
        "",
        summaries["summary_by_source_candidate_side"].round(4).to_string(index=False),
        "",
        "## Flip Rows",
        "",
        flips[[
            "source",
            "candidate",
            "rule",
            "market_ticker",
            "side",
            "floor_strike",
            "entry_price",
            "visible_qty",
            "basis_official_minus_proxy_usd",
            "proxy_distance_usd",
            "official_distance_usd",
            "proxy_result",
            "official_result_filled",
            "pnl_proxy_2c",
            "pnl_official_rest_2c",
            "pnl_delta_official_minus_proxy_2c",
        ]].round(4).to_string(index=False)
        if not flips.empty
        else "(none)",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {args.out_dir}")
    print(summaries["summary_by_source_candidate_side"].round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
