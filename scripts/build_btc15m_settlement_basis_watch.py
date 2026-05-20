#!/usr/bin/env python3
"""Build a BTC15M settlement-basis watch table from REST-official trade files.

This is a diagnostic artifact, not a strategy search. It combines focused
REST-official live replay rows and computes row-level proxy-vs-official basis
features so settlement-index fragility is visible before promotion decisions.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_settlement_basis_watch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_DIRS = [
    BACKTEST_ROOT / "btc15m_live_ws_rest_official_refresh_20260516_183818",
    BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex",
    BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex",
    BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC15M REST-official settlement basis diagnostics.")
    p.add_argument("--rest-official-dir", type=Path, action="append", dest="dirs", help="Directory with live_ws_trades_rest_official.parquet and market_results.csv. Repeatable.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--near-strike-usd", type=float, default=10.0)
    return p.parse_args()


def norm_result(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().replace({"nan": "", "none": "", "<na>": ""})


def source_label(path: Path) -> str:
    name = path.name
    replacements = {
        "btc15m_live_ws_rest_official_refresh_20260516_183818": "broad_live_ws_rest_official",
        "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex": "q250_firstskip_causal_rest_official",
        "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex": "q250_firstskip_yes_causal_rest_official",
        "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex": "q1000_yes_causal_rest_official",
    }
    return replacements.get(name, name)


def load_one_dir(path: Path) -> pd.DataFrame:
    trades_path = path / "live_ws_trades_rest_official.parquet"
    market_path = path / "market_results.csv"
    if not trades_path.exists():
        return pd.DataFrame()
    trades = pd.read_parquet(trades_path).copy()
    trades["source_dir"] = str(path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path)
    trades["source"] = source_label(path)
    if "candidate" not in trades.columns:
        trades["candidate"] = trades["source"]

    if market_path.exists():
        market = pd.read_csv(market_path)
        keep = [c for c in ["market_ticker", "result", "status", "expiration_value", "settlement_ts"] if c in market.columns]
        market = market[keep].rename(
            columns={
                "result": "rest_result",
                "status": "rest_status",
                "expiration_value": "rest_expiration_value",
            }
        )
        trades = trades.merge(market, on="market_ticker", how="left")
    return trades


def bps(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    den = pd.to_numeric(denominator, errors="coerce").replace(0.0, np.nan)
    return 10000.0 * pd.to_numeric(numerator, errors="coerce") / den


def enrich(rows: pd.DataFrame, near_strike_usd: float) -> pd.DataFrame:
    if rows.empty:
        return rows
    out = rows.copy()
    out["received_at_utc"] = pd.to_datetime(out.get("received_at_utc"), utc=True, errors="coerce")
    out["close_time"] = pd.to_datetime(out.get("close_time"), utc=True, errors="coerce")
    for col in [
        "floor_strike",
        "entry_price",
        "visible_qty",
        "spread_cents",
        "ttl_min",
        "side_fair_p",
        "fair_edge_cents",
        "btc_spot_model",
        "close_btc_spot",
        "proxy_distance_usd",
        "pnl_proxy_2c",
        "pnl_official_rest_2c",
    ]:
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    if "expiration_value" not in out.columns:
        out["expiration_value"] = np.nan
    out["expiration_value"] = pd.to_numeric(out["expiration_value"], errors="coerce")
    out["expiration_value"] = out["expiration_value"].fillna(pd.to_numeric(out.get("rest_expiration_value"), errors="coerce"))

    if "official_result_filled" not in out.columns:
        out["official_result_filled"] = out.get("official_result_rest", out.get("official_result", out.get("rest_result", "")))
    out["official_result_filled"] = norm_result(out["official_result_filled"])
    out["proxy_result"] = norm_result(out.get("proxy_result", ""))
    out["side"] = out.get("side", "").astype(str).str.lower()

    both = out["official_result_filled"].isin(["yes", "no"]) & out["proxy_result"].isin(["yes", "no"])
    out["proxy_official_both_results"] = both
    out["proxy_official_mismatch"] = both & out["official_result_filled"].ne(out["proxy_result"])
    out["official_distance_usd"] = out["expiration_value"] - out["floor_strike"]
    out["basis_official_minus_proxy_usd"] = out["expiration_value"] - out["close_btc_spot"]
    out["basis_official_minus_proxy_bps"] = bps(out["basis_official_minus_proxy_usd"], out["close_btc_spot"])
    out["decision_distance_usd"] = out["btc_spot_model"] - out["floor_strike"]
    out["aligned_decision_distance_usd"] = np.where(out["side"].eq("yes"), out["decision_distance_usd"], -out["decision_distance_usd"])
    out["aligned_decision_distance_bps"] = bps(out["aligned_decision_distance_usd"], out["floor_strike"])
    out["abs_proxy_distance_usd"] = out["proxy_distance_usd"].abs()
    out["near_strike_proxy"] = out["abs_proxy_distance_usd"].le(near_strike_usd)
    out["pnl_delta_official_minus_proxy_2c"] = out["pnl_official_rest_2c"] - out["pnl_proxy_2c"]
    return out


def finite_quantile(series: pd.Series, q: float) -> float:
    x = pd.to_numeric(series, errors="coerce").dropna()
    if x.empty:
        return math.nan
    return float(x.quantile(q))


def summarize(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    summary_rows: list[dict[str, Any]] = []
    for key, g in rows.groupby(group_cols, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        official = g[g["official_result_filled"].isin(["yes", "no"])]
        proxy = g[g["proxy_result"].isin(["yes", "no"])]
        both = g[g["proxy_official_both_results"]]
        row = dict(zip(group_cols, key_tuple, strict=False))
        official_pnl = pd.to_numeric(official.get("pnl_official_rest_2c"), errors="coerce")
        proxy_pnl = pd.to_numeric(proxy.get("pnl_proxy_2c"), errors="coerce")
        delta = pd.to_numeric(g.get("pnl_delta_official_minus_proxy_2c"), errors="coerce")
        basis = pd.to_numeric(g.get("basis_official_minus_proxy_usd"), errors="coerce")
        row.update(
            {
                "rows": int(len(g)),
                "official_rows": int(len(official)),
                "proxy_rows": int(len(proxy)),
                "both_result_rows": int(len(both)),
                "mismatches": int(g["proxy_official_mismatch"].sum()),
                "mismatch_rate": round(float(g["proxy_official_mismatch"].sum() / len(both)), 4) if len(both) else 0.0,
                "official_pnl_2c": round(float(official_pnl.sum()), 4) if len(official_pnl.dropna()) else 0.0,
                "proxy_pnl_2c": round(float(proxy_pnl.sum()), 4) if len(proxy_pnl.dropna()) else 0.0,
                "pnl_delta_official_minus_proxy_2c": round(float(delta.sum()), 4) if len(delta.dropna()) else 0.0,
                "mean_basis_usd": round(float(basis.mean()), 4) if len(basis.dropna()) else math.nan,
                "median_basis_usd": round(float(basis.median()), 4) if len(basis.dropna()) else math.nan,
                "basis_p05_usd": round(finite_quantile(basis, 0.05), 4),
                "basis_p95_usd": round(finite_quantile(basis, 0.95), 4),
                "max_abs_basis_usd": round(float(basis.abs().max()), 4) if len(basis.dropna()) else math.nan,
                "near_strike_rows": int(g["near_strike_proxy"].fillna(False).sum()),
                "median_abs_proxy_distance_usd": round(float(pd.to_numeric(g["abs_proxy_distance_usd"], errors="coerce").median()), 4)
                if len(pd.to_numeric(g["abs_proxy_distance_usd"], errors="coerce").dropna())
                else math.nan,
            }
        )
        summary_rows.append(row)
    return pd.DataFrame(summary_rows).sort_values(group_cols).reset_index(drop=True)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    input_dirs = args.dirs if args.dirs else DEFAULT_DIRS
    loaded = [load_one_dir(path) for path in input_dirs]
    rows = pd.concat([df for df in loaded if not df.empty], ignore_index=True) if any(not df.empty for df in loaded) else pd.DataFrame()
    rows = enrich(rows, args.near_strike_usd)
    mismatches = rows[rows.get("proxy_official_mismatch", pd.Series(False, index=rows.index)).fillna(False)].copy() if not rows.empty else pd.DataFrame()
    summary = summarize(rows, ["source", "candidate", "side"])
    summary_by_candidate = summarize(rows, ["candidate", "side"])

    rows.to_csv(args.out_dir / "settlement_basis_rows.csv", index=False)
    summary.to_csv(args.out_dir / "settlement_basis_summary.csv", index=False)
    summary_by_candidate.to_csv(args.out_dir / "settlement_basis_summary_by_candidate.csv", index=False)
    mismatches.to_csv(args.out_dir / "settlement_basis_mismatches.csv", index=False)
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dirs": [str(path) for path in input_dirs],
        "rows": int(len(rows)),
        "mismatch_rows": int(len(mismatches)),
        "near_strike_usd": args.near_strike_usd,
        "note": "Diagnostic only. Do not tune or promote from this aggregate basis watch.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M Settlement Basis Watch",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        summary.round(4).to_string(index=False) if not summary.empty else "_No rows._",
        "",
        "## Mismatches",
        "",
        mismatches[
            [
                "source",
                "candidate",
                "market_ticker",
                "side",
                "received_at_utc",
                "close_time",
                "entry_price",
                "visible_qty",
                "floor_strike",
                "btc_spot_model",
                "close_btc_spot",
                "expiration_value",
                "basis_official_minus_proxy_usd",
                "proxy_distance_usd",
                "official_distance_usd",
                "proxy_result",
                "official_result_filled",
                "pnl_proxy_2c",
                "pnl_official_rest_2c",
                "pnl_delta_official_minus_proxy_2c",
            ]
        ].round(4).to_string(index=False)
        if not mismatches.empty
        else "_No mismatches._",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
