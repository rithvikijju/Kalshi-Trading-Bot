#!/usr/bin/env python3
"""Build diagnostic BTC settlement-basis danger tables.

This is not a trading-rule search. It bins the normalized official-vs-proxy
settlement rows by decision-time features so we can see where adverse flips are
coming from before pre-registering any future basis-risk guard.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_basis_danger_table_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build diagnostic BTC settlement-basis danger tables.")
    p.add_argument(
        "--basis-risk-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc_settlement_basis_risk_audit_latest_codex",
    )
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([math.nan] * len(df), index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def boolish(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df[col].astype(str).str.lower().isin(["true", "1", "yes", "y"])


def clean_text(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col not in df.columns:
        return pd.Series([default] * len(df), index=df.index)
    return df[col].fillna(default).astype(str).replace({"nan": default, "None": default, "<NA>": default})


def bucket(series: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series, bins=bins, labels=labels, include_lowest=True).astype(str).replace("nan", "missing")


def prepare(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    out = rows.copy()
    out["family"] = clean_text(out, "family")
    out["candidate"] = clean_text(out, "candidate")
    out["side"] = clean_text(out, "side").str.lower()
    out["source"] = clean_text(out, "source")
    out["market_ticker"] = clean_text(out, "market_ticker")
    out["created_at"] = clean_text(out, "created_at")
    out["both_result_rows"] = boolish(out, "both_result_rows")
    out["proxy_official_mismatch"] = boolish(out, "proxy_official_mismatch")
    out["adverse_proxy_official_mismatch"] = boolish(out, "adverse_proxy_official_mismatch")
    out["proxy_win_official_loss"] = boolish(out, "proxy_win_official_loss")
    out["official_pnl_2c"] = num(out, "official_pnl_2c")
    out["proxy_pnl_2c"] = num(out, "proxy_pnl_2c")
    out["pnl_delta_official_minus_proxy_2c"] = num(out, "pnl_delta_official_minus_proxy_2c")
    out["basis_usd"] = num(out, "basis_usd")
    out["abs_basis_usd"] = num(out, "abs_basis_usd").fillna(out["basis_usd"].abs())
    out["proxy_distance_usd"] = num(out, "proxy_distance_usd")
    out["abs_proxy_distance_usd"] = num(out, "abs_proxy_distance_usd").fillna(out["proxy_distance_usd"].abs())
    out["aligned_decision_distance_bps"] = num(out, "aligned_decision_distance_bps").fillna(
        num(out, "entry_spot_distance_bps")
    )
    out["abs_aligned_decision_distance_bps"] = out["aligned_decision_distance_bps"].abs()
    out["ttl_min"] = num(out, "ttl_min")
    out["entry_price"] = num(out, "entry_price")
    out["visible_qty"] = num(out, "visible_qty").fillna(num(out, "top_visible_qty"))
    out["spread_cents"] = num(out, "spread_cents")
    out["quote_age_ms"] = num(out, "quote_age_ms")
    out["btc_spot_age_sec"] = num(out, "btc_spot_age_sec")
    out["rv_60m"] = num(out, "rv_60m")
    out["near_strike_proxy"] = boolish(out, "near_strike_proxy")

    out["abs_proxy_distance_bucket"] = bucket(
        out["abs_proxy_distance_usd"],
        [-math.inf, 10, 20, 50, 100, math.inf],
        ["<=10", "10-20", "20-50", "50-100", ">100"],
    )
    out["decision_distance_bps_bucket"] = bucket(
        out["abs_aligned_decision_distance_bps"],
        [-math.inf, 2.5, 5, 10, 20, math.inf],
        ["<=2.5", "2.5-5", "5-10", "10-20", ">20"],
    )
    out["ttl_bucket"] = bucket(
        out["ttl_min"],
        [-math.inf, 5, 10, 12, 30, math.inf],
        ["<=5", "5-10", "10-12", "12-30", ">30"],
    )
    out["entry_price_bucket"] = bucket(
        out["entry_price"],
        [-math.inf, 0.25, 0.50, 0.75, math.inf],
        ["<=25c", "25-50c", "50-75c", ">75c"],
    )
    out["visible_qty_bucket"] = bucket(
        out["visible_qty"],
        [-math.inf, 250, 500, 1000, math.inf],
        ["<250", "250-500", "500-1000", ">=1000"],
    )
    out["quote_age_bucket"] = bucket(
        out["quote_age_ms"],
        [-math.inf, 500, 2000, 10000, math.inf],
        ["<=0.5s", "0.5-2s", "2-10s", ">10s"],
    )
    out["btc_spot_age_bucket"] = bucket(
        out["btc_spot_age_sec"],
        [-math.inf, 2, 5, 10, 30, math.inf],
        ["<=2s", "2-5s", "5-10s", "10-30s", ">30s"],
    )
    out["near_strike_proxy_bucket"] = out["near_strike_proxy"].map({True: "near_proxy", False: "not_near_proxy"})
    return out


def summarize_group(g: pd.DataFrame) -> dict[str, Any]:
    both = g[g["both_result_rows"]]
    official = g[g["official_pnl_2c"].notna()]
    proxy = g[g["proxy_pnl_2c"].notna()]
    delta = g["pnl_delta_official_minus_proxy_2c"].dropna()
    return {
        "rows": int(len(g)),
        "both_result_rows": int(len(both)),
        "mismatches": int(g["proxy_official_mismatch"].sum()),
        "adverse_mismatches": int(g["adverse_proxy_official_mismatch"].sum()),
        "proxy_win_official_loss_rows": int(g["proxy_win_official_loss"].sum()),
        "mismatch_rate": round(float(g["proxy_official_mismatch"].sum() / len(both)), 4) if len(both) else 0.0,
        "adverse_mismatch_rate": round(float(g["adverse_proxy_official_mismatch"].sum() / len(both)), 4)
        if len(both)
        else 0.0,
        "official_pnl_2c": round(float(official["official_pnl_2c"].sum()), 4) if len(official) else 0.0,
        "proxy_pnl_2c": round(float(proxy["proxy_pnl_2c"].sum()), 4) if len(proxy) else 0.0,
        "pnl_delta_official_minus_proxy_2c": round(float(delta.sum()), 4) if len(delta) else 0.0,
        "pnl_delta_per_both_trade_2c": round(float(delta.sum() / len(both)), 4) if len(both) else 0.0,
        "mean_abs_basis_usd": round(float(g["abs_basis_usd"].mean()), 4) if g["abs_basis_usd"].notna().any() else math.nan,
        "p95_abs_basis_usd": round(float(g["abs_basis_usd"].quantile(0.95)), 4)
        if g["abs_basis_usd"].notna().any()
        else math.nan,
        "median_abs_proxy_distance_usd": round(float(g["abs_proxy_distance_usd"].median()), 4)
        if g["abs_proxy_distance_usd"].notna().any()
        else math.nan,
        "median_decision_distance_bps": round(float(g["abs_aligned_decision_distance_bps"].median()), 4)
        if g["abs_aligned_decision_distance_bps"].notna().any()
        else math.nan,
        "median_ttl_min": round(float(g["ttl_min"].median()), 4) if g["ttl_min"].notna().any() else math.nan,
        "median_visible_qty": round(float(g["visible_qty"].median()), 4) if g["visible_qty"].notna().any() else math.nan,
    }


def summarize(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    out_rows: list[dict[str, Any]] = []
    for key, g in rows.groupby(group_cols, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        row = dict(zip(group_cols, key_tuple, strict=False))
        row.update(summarize_group(g))
        out_rows.append(row)
    return pd.DataFrame(out_rows).sort_values(group_cols + ["rows"], ascending=[True] * len(group_cols) + [False])


def bin_summaries(rows: pd.DataFrame) -> pd.DataFrame:
    dimensions = [
        "abs_proxy_distance_bucket",
        "decision_distance_bps_bucket",
        "ttl_bucket",
        "entry_price_bucket",
        "visible_qty_bucket",
        "quote_age_bucket",
        "btc_spot_age_bucket",
        "near_strike_proxy_bucket",
    ]
    frames: list[pd.DataFrame] = []
    for dimension in dimensions:
        summary = summarize(rows, ["family", "candidate", "side", dimension])
        if summary.empty:
            continue
        summary = summary.rename(columns={dimension: "bucket"})
        summary.insert(0, "dimension", dimension)
        frames.append(summary)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def compact_rows(rows: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "family",
        "candidate",
        "side",
        "source",
        "market_ticker",
        "created_at",
        "proxy_official_mismatch",
        "adverse_proxy_official_mismatch",
        "proxy_win_official_loss",
        "official_pnl_2c",
        "proxy_pnl_2c",
        "pnl_delta_official_minus_proxy_2c",
        "basis_usd",
        "abs_basis_usd",
        "proxy_distance_usd",
        "abs_proxy_distance_usd",
        "aligned_decision_distance_bps",
        "ttl_min",
        "entry_price",
        "visible_qty",
        "spread_cents",
        "quote_age_ms",
        "btc_spot_age_sec",
        "rv_60m",
        "abs_proxy_distance_bucket",
        "decision_distance_bps_bucket",
        "ttl_bucket",
        "entry_price_bucket",
        "visible_qty_bucket",
        "quote_age_bucket",
        "btc_spot_age_bucket",
    ]
    return rows[[col for col in columns if col in rows.columns]].copy()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = read_csv(args.basis_risk_dir / "settlement_basis_risk_rows.csv")
    rows = prepare(raw)
    candidate_summary = summarize(rows, ["family", "candidate", "side"])
    side_summary = summarize(rows, ["family", "side"])
    bins = bin_summaries(rows)
    features = compact_rows(rows)

    features.to_csv(args.out_dir / "basis_danger_features.csv", index=False)
    candidate_summary.to_csv(args.out_dir / "basis_danger_by_candidate.csv", index=False)
    side_summary.to_csv(args.out_dir / "basis_danger_by_side.csv", index=False)
    bins.to_csv(args.out_dir / "basis_danger_bins.csv", index=False)

    high_signal_bins = (
        bins[(bins["both_result_rows"] >= 3) & ((bins["adverse_mismatch_rate"] > 0) | (bins["mismatch_rate"] >= 0.1))]
        .sort_values(["adverse_mismatch_rate", "mismatch_rate", "both_result_rows"], ascending=[False, False, False])
        .head(25)
        if not bins.empty
        else pd.DataFrame()
    )
    high_signal_bins.to_csv(args.out_dir / "basis_danger_high_signal_bins.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis_risk_dir": str(args.basis_risk_dir),
        "rows": int(len(rows)),
        "candidate_groups": int(len(candidate_summary)),
        "bin_rows": int(len(bins)),
        "note": "Diagnostic only. These empirical bins must not become deployment thresholds without pre-registration and fresh forward validation.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Settlement Basis Danger Table",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Rows: `{info['rows']}`",
        "",
        "## Candidate Summary",
        "",
        candidate_summary.fillna("").round(4).to_string(index=False) if not candidate_summary.empty else "_No rows._",
        "",
        "## High-Signal Diagnostic Bins",
        "",
        high_signal_bins.fillna("").round(4).to_string(index=False) if not high_signal_bins.empty else "_No high-signal bins._",
        "",
        "## Interpretation",
        "",
        "- This is a diagnostic basis-danger map, not a filter grid.",
        "- q250/q1000 NO-side rows concentrate the adverse proxy/official flips; q1000 YES and q250 first-skip YES remain cleaner but sparse.",
        "- BTC1H has too few rows for modeling and remains observe-only; its large basis rows are warnings, not deployable edge.",
        "- Any future guard derived from these bins must be frozen first and evaluated only on fresh post-freeze official rows.",
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
