#!/usr/bin/env python3
"""Analyze fragility of materialized BTC15M first-signal candidates.

This is not a search script. It audits a small set of already-known rule
families against side splits, historical windows, and live settlement flips so
we can tell whether the current q250 first-signal candidate is robust or mostly
benefiting from a thin live sample.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_btc15m_materialized_filter_grid import Rule, apply_rule, metrics

DEFAULT_PREDEXON = (
    BACKTEST_ROOT
    / "btc15m_predexon_metadata_settlement_20260517_codex"
    / "predexon_trades_rest_official.parquet"
)
DEFAULT_LIVE = (
    BACKTEST_ROOT
    / "btc15m_live_ws_rest_official_refresh_20260516_183818"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_MARKET_RESULTS = (
    BACKTEST_ROOT
    / "btc15m_live_ws_rest_official_refresh_20260516_183818"
    / "market_results.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_materialized_fragility_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit BTC15M materialized candidate fragility.")
    p.add_argument("--predexon-trades", type=Path, default=DEFAULT_PREDEXON)
    p.add_argument("--live-trades", type=Path, default=DEFAULT_LIVE)
    p.add_argument("--market-results", type=Path, default=DEFAULT_MARKET_RESULTS)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def aligned_distance(df: pd.DataFrame) -> pd.Series:
    if "distance_bps" in df:
        d = pd.to_numeric(df["distance_bps"], errors="coerce")
    elif {"btc_spot_model", "floor_strike"}.issubset(df.columns):
        d = 10000.0 * np.log(
            pd.to_numeric(df["btc_spot_model"], errors="coerce")
            / pd.to_numeric(df["floor_strike"], errors="coerce")
        )
    else:
        return pd.Series(np.nan, index=df.index)
    side = df["side"].astype(str).str.lower()
    return d.where(side.eq("yes"), -d)


def build_rules() -> list[Rule]:
    rules: list[Rule] = []
    specs = [
        ("q250", "ttl10_12_entry50_q250", [500.0], ["both", "yes", "no"]),
        ("q1000", "ttl10_12_entry50_q1000", [250.0, 1000.0], ["yes"]),
    ]
    for candidate, strategy, qty_values, side_modes in specs:
        for side_mode in side_modes:
            for qty in qty_values:
                for dist in [None, 0.0, 5.0, 10.0]:
                    suffix = "none" if dist is None else str(int(dist))
                    rules.append(
                        Rule(
                            name=f"{candidate}_{side_mode}_qty{int(qty)}_dist{suffix}",
                            live_candidate=candidate,
                            pred_strategy=strategy,
                            side_mode=side_mode,
                            fair_p_min=0.60,
                            edge_min=12.0,
                            entry_max=0.50,
                            qty_min=qty,
                            spread_max=2.0,
                            ttl_min=10.0,
                            ttl_max=12.0,
                            aligned_dist_min_bps=dist,
                        )
                    )
    return rules


def summarize_rule(
    label: str,
    rule: Rule,
    df: pd.DataFrame,
    mode: str,
    pnl_col: str,
    win_col: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    selected = apply_rule(df, rule, mode)
    selected = selected.copy()
    selected["rule"] = rule.name
    selected["source"] = label
    selected["aligned_dist_bps"] = aligned_distance(selected)
    valid = selected[pd.to_numeric(selected.get(pnl_col, pd.Series(dtype=float)), errors="coerce").notna()].copy()
    row = {
        "source": label,
        "rule": rule.name,
        "side_mode": rule.side_mode,
        "qty_min": rule.qty_min,
        "aligned_dist_min_bps": rule.aligned_dist_min_bps,
        **{f"{k}": v for k, v in metrics(valid, pnl_col, win_col).items()},
    }
    if len(valid):
        row.update(
            {
                "median_aligned_dist_bps": float(pd.to_numeric(valid["aligned_dist_bps"], errors="coerce").median()),
                "median_visible_qty": float(pd.to_numeric(valid["visible_qty"], errors="coerce").median()),
                "median_entry": float(pd.to_numeric(valid["entry_price"], errors="coerce").median()),
            }
        )
    else:
        row.update({"median_aligned_dist_bps": math.nan, "median_visible_qty": math.nan, "median_entry": math.nan})
    return row, selected


def window_rows(rule: Rule, selected: pd.DataFrame, pnl_col: str, win_col: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if "window" not in selected:
        return out
    for window, group in selected.groupby("window", dropna=False):
        valid = group[pd.to_numeric(group.get(pnl_col, pd.Series(dtype=float)), errors="coerce").notna()].copy()
        row = {"rule": rule.name, "window": window, **metrics(valid, pnl_col, win_col)}
        out.append(row)
    return out


def add_live_settlement_context(live: pd.DataFrame, market_results_path: Path) -> pd.DataFrame:
    out = live.copy()
    if not market_results_path.exists():
        return out
    results = pd.read_csv(market_results_path)
    keep = [c for c in ["market_ticker", "expiration_value"] if c in results.columns]
    if keep != ["market_ticker", "expiration_value"]:
        return out
    results = results[keep].drop_duplicates("market_ticker", keep="last")
    out = out.merge(results, on="market_ticker", how="left")
    out["expiration_value"] = pd.to_numeric(out["expiration_value"], errors="coerce")
    out["close_btc_spot"] = pd.to_numeric(out.get("close_btc_spot"), errors="coerce")
    out["floor_strike"] = pd.to_numeric(out.get("floor_strike"), errors="coerce")
    out["basis_official_minus_proxy_usd"] = out["expiration_value"] - out["close_btc_spot"]
    out["official_distance_usd"] = out["expiration_value"] - out["floor_strike"]
    return out


def live_flip_rows(rule: Rule, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return selected.copy()
    work = selected.copy()
    proxy = work.get("proxy_result", pd.Series("", index=work.index)).astype(str).str.lower()
    official = work.get("official_result_filled", pd.Series("", index=work.index)).astype(str).str.lower()
    flips = work[proxy.isin(["yes", "no"]) & official.isin(["yes", "no"]) & proxy.ne(official)].copy()
    if flips.empty:
        return flips
    cols = [
        "rule",
        "market_ticker",
        "side",
        "floor_strike",
        "entry_price",
        "visible_qty",
        "fair_edge_cents",
        "side_fair_p",
        "spread_cents",
        "ttl_min",
        "aligned_dist_bps",
        "close_btc_spot",
        "expiration_value",
        "basis_official_minus_proxy_usd",
        "proxy_distance_usd",
        "official_distance_usd",
        "proxy_result",
        "official_result_filled",
        "pnl_proxy_2c",
        "pnl_official_rest_2c",
    ]
    return flips[[c for c in cols if c in flips.columns]].sort_values(
        [c for c in ["rule", "market_ticker"] if c in flips.columns]
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pred = pd.read_parquet(args.predexon_trades)
    live = add_live_settlement_context(pd.read_parquet(args.live_trades), args.market_results)
    if "available_at" not in live and "received_at_utc" in live:
        live["available_at"] = live["received_at_utc"]
    if "ttl_min" not in live and {"close_time", "received_at_utc"}.issubset(live.columns):
        live["ttl_min"] = (
            pd.to_datetime(live["close_time"], utc=True, errors="coerce")
            - pd.to_datetime(live["received_at_utc"], utc=True, errors="coerce")
        ).dt.total_seconds() / 60.0

    summary_rows: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    live_flips: list[pd.DataFrame] = []
    all_selected: list[pd.DataFrame] = []
    for rule in build_rules():
        pred_meta_row, pred_meta_selected = summarize_rule(
            "predexon_metadata",
            rule,
            pred,
            "predexon",
            "pnl_predexon_metadata_2c",
            "win_pnl_predexon_metadata_2c",
        )
        pred_rest_row, pred_rest_selected = summarize_rule(
            "predexon_rest_subset",
            rule,
            pred,
            "predexon",
            "pnl_official_rest_2c",
            "win_pnl_official_rest_2c",
        )
        live_row, live_selected = summarize_rule(
            "live_rest_official",
            rule,
            live,
            "live",
            "pnl_official_rest_2c",
            "win_pnl_official_rest_2c",
        )
        live_proxy_row, _ = summarize_rule(
            "live_proxy",
            rule,
            live,
            "live",
            "pnl_proxy_2c",
            "win_pnl_proxy_2c",
        )
        summary_rows.extend([pred_meta_row, pred_rest_row, live_row, live_proxy_row])
        windows.extend(window_rows(rule, pred_meta_selected, "pnl_predexon_metadata_2c", "win_pnl_predexon_metadata_2c"))
        live_flips.append(live_flip_rows(rule, live_selected))
        all_selected.extend([pred_meta_selected, live_selected])

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(args.out_dir / "fragility_summary.csv", index=False)
    pd.DataFrame(windows).to_csv(args.out_dir / "fragility_predexon_windows.csv", index=False)
    flips = pd.concat([x for x in live_flips if not x.empty], ignore_index=True) if any(not x.empty for x in live_flips) else pd.DataFrame()
    flips.to_csv(args.out_dir / "fragility_live_flips.csv", index=False)

    focus = summary[
        summary["rule"].isin(
            [
                "q250_both_qty500_distnone",
                "q250_yes_qty500_distnone",
                "q250_no_qty500_distnone",
                "q250_both_qty500_dist5",
                "q1000_yes_qty250_distnone",
                "q1000_yes_qty1000_distnone",
            ]
        )
    ].copy()
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predexon_trades": str(args.predexon_trades),
        "live_trades": str(args.live_trades),
        "market_results": str(args.market_results),
        "rules": len(build_rules()),
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC15M Materialized Candidate Fragility",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Focus Summary",
        "",
        focus.round(4).to_string(index=False),
        "",
        "## Live Settlement Flips",
        "",
        flips.round(4).to_string(index=False) if not flips.empty else "No live proxy/official flips in audited rows.",
        "",
        "## Interpretation Notes",
        "",
        "- `predexon_metadata` uses provider market metadata labels for historical rows.",
        "- `predexon_rest_subset` includes only historical rows whose ticker is still available from Kalshi REST.",
        "- `live_rest_official` is the current live websocket replay filled with Kalshi REST official results.",
        "- Distance guards are causal decision-time aligned distance in bps, not close-time distance.",
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
