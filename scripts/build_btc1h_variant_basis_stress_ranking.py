#!/usr/bin/env python3
"""Rank frozen BTC1H variants under side-adverse official-basis stress.

This is research-only.  It compares already-frozen BTC1H variants on rows with
usable proxy settlement spots, then shifts settlement against each trade side
by fixed dollar shocks.  It does not search thresholds, fit a basis guard,
promote a variant, deploy, or touch live/paper processes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402
from scripts.build_btc1h_basis_stress_audit import (  # noqa: E402
    add_observed_basis_shocks,
    parse_shocks,
    pnl_from_result,
    safe_read_csv,
    settlement_spot_series,
    strike_series,
)
from scripts.build_btc1h_multi_holdout_research import (  # noqa: E402
    BACKTEST_ROOT,
    PRIMARY_VARIANTS,
    direct_holdout_label,
    historical_holdout_label,
    latest_file,
    markdown_table,
    max_drawdown,
    parse_time,
    sharpe,
)


DEFAULT_OUT = BACKTEST_ROOT / "btc1h_variant_basis_stress_ranking_latest_codex"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--derived-entry59-trades", type=Path, default=None)
    parser.add_argument(
        "--basis-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_official_basis_mismatch_audit_latest_codex" / "btc1h_basis_mismatch_summary.csv",
    )
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--basis-shocks-usd", default="0,50,75")
    return parser.parse_args()


def default_paths(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        "robustness_trades": args.robustness_trades
        or latest_file("btc1h_highconf_robustness_*", "all_input_trades.csv"),
        "direct_trades": args.direct_trades
        or latest_file("btc1h_highconf_direct_aggregate_feb09_may06_*", "trades.csv"),
        "derived_entry59_trades": args.derived_entry59_trades
        or latest_file("btc1h_entry59_70_derived_*", "derived_trades.csv"),
    }


def event_from_market(market_ticker: Any) -> str:
    text = str(market_ticker or "").upper()
    if "-T" in text:
        return text.split("-T", 1)[0]
    return text


def normalize_variant_frame(df: pd.DataFrame, source_name: str, stress_cents: float, label_func) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "variant" not in work.columns and "model" in work.columns:
        work["variant"] = work["model"]
    work = work[work["variant"].astype(str).isin(PRIMARY_VARIANTS)].copy()
    if work.empty:
        return pd.DataFrame()
    work["evidence_source"] = source_name
    work["entry_time"] = parse_time(work.get("entry_time", pd.Series("", index=work.index)))
    work["holdout"] = work.apply(label_func, axis=1)
    work["entry_price"] = pd.to_numeric(work["entry_price"], errors="coerce")
    work["entry_stressed"] = (work["entry_price"] + stress_cents / 100.0).clip(upper=0.99)
    work["premium_for_stress"] = work["entry_stressed"].map(
        lambda x: float(x) + kalshi_fee_dollars(float(x), contracts=1, liquidity="taker")
    )
    work["strike_for_stress"] = strike_series(work)
    work["proxy_settlement_spot"] = settlement_spot_series(work)
    work["side"] = work["side"].astype(str).str.lower()
    work["market_ticker"] = work["market_ticker"].astype(str).str.upper()
    if "event_ticker" not in work.columns:
        work["event_ticker"] = ""
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    missing_event = work["event_ticker"].isin(["", "NAN", "NONE"])
    work.loc[missing_event, "event_ticker"] = work.loc[missing_event, "market_ticker"].map(event_from_market)
    work["market_side_key"] = work["market_ticker"] + "|" + work["side"]
    proxy_yes = work["proxy_settlement_spot"] >= work["strike_for_stress"]
    work["proxy_result_for_stress"] = np.where(proxy_yes, "yes", "no")
    return work.dropna(subset=["entry_stressed", "strike_for_stress", "proxy_settlement_spot"]).reset_index(drop=True)


def load_rows(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Path | None]]:
    paths = default_paths(args)
    frames = [
        normalize_variant_frame(
            safe_read_csv(paths["robustness_trades"]),
            "robustness_trade_logs",
            args.stress_cents,
            historical_holdout_label,
        ),
        normalize_variant_frame(
            safe_read_csv(paths["direct_trades"]),
            "direct_predexon_trade_logs",
            args.stress_cents,
            direct_holdout_label,
        ),
        normalize_variant_frame(
            safe_read_csv(paths["derived_entry59_trades"]),
            "derived_entry59_trade_logs",
            args.stress_cents,
            historical_holdout_label,
        ),
    ]
    rows = pd.concat([df for df in frames if not df.empty], ignore_index=True, sort=False) if frames else pd.DataFrame()
    return rows, paths


def stress_rows(rows: pd.DataFrame, shocks: list[float]) -> pd.DataFrame:
    out: list[pd.DataFrame] = []
    for shock in shocks:
        work = rows.copy()
        adverse_direction = np.where(work["side"].eq("yes"), -1.0, 1.0)
        work["basis_shock_usd"] = shock
        work["stressed_settlement_spot"] = work["proxy_settlement_spot"] + adverse_direction * shock
        stressed_yes = work["stressed_settlement_spot"] >= work["strike_for_stress"]
        work["stressed_result"] = np.where(stressed_yes, "yes", "no")
        work["basis_result_flip"] = work["stressed_result"] != work["proxy_result_for_stress"]
        work["basis_stressed_pnl"] = pnl_from_result(
            work["side"], work["stressed_result"], work["premium_for_stress"]
        )
        work["basis_stressed_win"] = work["side"].eq(work["stressed_result"])
        out.append(work)
    return pd.concat(out, ignore_index=True, sort=False) if out else pd.DataFrame()


def summarize_group(group: pd.DataFrame) -> dict[str, Any]:
    pnl = pd.to_numeric(group["basis_stressed_pnl"], errors="coerce").fillna(0.0)
    unique = group.sort_values([c for c in ["entry_time", "event_ticker", "market_ticker", "side"] if c in group.columns])
    unique = unique.drop_duplicates("market_side_key", keep="first")
    unique_pnl = pd.to_numeric(unique["basis_stressed_pnl"], errors="coerce").fillna(0.0)
    flips = group["basis_result_flip"].astype(bool)
    wins = group["basis_stressed_win"].astype(bool)
    return {
        "rows": int(len(group)),
        "unique_market_side_rows": int(len(unique)),
        "duplicate_market_side_rows": int(group.duplicated("market_side_key").sum()),
        "event_clusters": int(group["event_ticker"].nunique()),
        "pnl": round(float(pnl.sum()), 6),
        "unique_market_side_pnl": round(float(unique_pnl.sum()), 6),
        "win_rate": round(float(wins.mean()), 6) if len(wins) else 0.0,
        "basis_flip_rows": int(flips.sum()),
        "basis_flip_rate": round(float(flips.mean()), 6) if len(flips) else 0.0,
        "max_drawdown": round(max_drawdown(pnl), 6),
        "sharpe": round(sharpe(pnl), 6),
    }


def build_tables(stressed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    for (variant, shock), group in stressed.groupby(["variant", "basis_shock_usd"], dropna=False, sort=True):
        row = {"variant": variant, "basis_shock_usd": shock}
        row.update(summarize_group(group))
        by_holdout = []
        for (source, holdout), hg in group.groupby(["evidence_source", "holdout"], dropna=False, sort=True):
            hrow = {
                "variant": variant,
                "basis_shock_usd": shock,
                "evidence_source": source,
                "holdout": holdout,
            }
            hrow.update(summarize_group(hg))
            hrow["positive_pnl"] = bool(hrow["pnl"] > 0)
            holdout_rows.append(hrow)
            by_holdout.append(hrow)
        holdout_df = pd.DataFrame(by_holdout)
        if holdout_df.empty:
            row["positive_holdouts"] = 0
            row["holdouts"] = 0
            row["negative_holdouts"] = ""
        else:
            row["positive_holdouts"] = int(holdout_df["positive_pnl"].sum())
            row["holdouts"] = int(len(holdout_df))
            neg = holdout_df[~holdout_df["positive_pnl"]]
            row["negative_holdouts"] = ";".join(
                (neg["evidence_source"].astype(str) + "|" + neg["holdout"].astype(str)).tolist()
            )
        row["positive_pnl"] = bool(row["pnl"] > 0)
        row["positive_unique_market_side_pnl"] = bool(row["unique_market_side_pnl"] > 0)
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary = summary.sort_values(
            ["basis_shock_usd", "positive_unique_market_side_pnl", "positive_holdouts", "unique_market_side_pnl"],
            ascending=[True, False, False, False],
        ).reset_index(drop=True)
    return summary, pd.DataFrame(holdout_rows)


def build_ranking(summary: pd.DataFrame, shocks: list[float]) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for variant, group in summary.groupby("variant", sort=True):
        g = group.set_index("basis_shock_usd")
        available_shocks = sorted(float(x) for x in group["basis_shock_usd"].unique())
        positive_unique_shocks = [
            shock
            for shock in available_shocks
            if bool(g.loc[shock, "positive_unique_market_side_pnl"]) if shock in g.index
        ]
        max_positive_unique = max(positive_unique_shocks) if positive_unique_shocks else float("nan")
        p95_shock = 78.915 if 78.915 in g.index else max(available_shocks)
        p95_row = g.loc[p95_shock]
        shock50 = g.loc[50.0] if 50.0 in g.index else pd.Series(dtype=object)
        rows.append(
            {
                "variant": variant,
                "max_basis_shock_with_positive_unique_pnl": max_positive_unique,
                "pnl_at_50": shock50.get("pnl", ""),
                "unique_pnl_at_50": shock50.get("unique_market_side_pnl", ""),
                "positive_holdouts_at_50": shock50.get("positive_holdouts", ""),
                "holdouts_at_50": shock50.get("holdouts", ""),
                "p95_or_max_basis_shock": p95_shock,
                "pnl_at_p95_or_max": p95_row.get("pnl", ""),
                "unique_pnl_at_p95_or_max": p95_row.get("unique_market_side_pnl", ""),
                "positive_holdouts_at_p95_or_max": p95_row.get("positive_holdouts", ""),
                "holdouts_at_p95_or_max": p95_row.get("holdouts", ""),
                "basis_flips_at_p95_or_max": p95_row.get("basis_flip_rows", ""),
                "deployable_now": False,
            }
        )
    return pd.DataFrame(rows).sort_values(
        [
            "max_basis_shock_with_positive_unique_pnl",
            "positive_holdouts_at_50",
            "unique_pnl_at_50",
        ],
        ascending=[False, False, False],
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows, paths = load_rows(args)
    if rows.empty:
        raise SystemExit("No stressable frozen BTC1H variant rows found.")
    shocks = add_observed_basis_shocks(parse_shocks(args.basis_shocks_usd), args.basis_summary)
    stressed = stress_rows(rows, shocks)
    summary, holdouts = build_tables(stressed)
    ranking = build_ranking(summary, shocks)

    summary.to_csv(args.out_dir / "btc1h_variant_basis_stress_summary.csv", index=False)
    holdouts.to_csv(args.out_dir / "btc1h_variant_basis_stress_by_holdout.csv", index=False)
    ranking.to_csv(args.out_dir / "btc1h_variant_basis_stress_ranking.csv", index=False)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis_shocks_usd": shocks,
        "stress_cents": args.stress_cents,
        "variants": PRIMARY_VARIANTS,
        "basis_summary": str(args.basis_summary),
        "deployable_now": False,
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
        "note": "Research-only fixed-variant basis stress ranking. No threshold search, guard fitting, promotion, or deployment.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    report_cols = [
        "variant",
        "max_basis_shock_with_positive_unique_pnl",
        "pnl_at_50",
        "unique_pnl_at_50",
        "positive_holdouts_at_50",
        "holdouts_at_50",
        "pnl_at_p95_or_max",
        "unique_pnl_at_p95_or_max",
        "positive_holdouts_at_p95_or_max",
        "holdouts_at_p95_or_max",
        "basis_flips_at_p95_or_max",
    ]
    report = [
        "# BTC1H Variant Basis-Stress Ranking",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Historical/proxy adverse entry stress: `+{args.stress_cents:.1f}c`.",
        "",
        "## Verdict",
        "",
        "- Research-only diagnostic. It cannot promote or deploy any BTC1H variant.",
        "- Variants are fixed; no threshold search or basis guard fitting occurs here.",
        "- Compare unique-market-side PnL and holdout stability, not just pooled PnL.",
        "",
        "## Ranking",
        "",
        markdown_table(ranking[[c for c in report_cols if c in ranking.columns]] if not ranking.empty else ranking),
        "",
        "## Shock Summary",
        "",
        markdown_table(
            summary[
                [
                    "variant",
                    "basis_shock_usd",
                    "rows",
                    "unique_market_side_rows",
                    "pnl",
                    "unique_market_side_pnl",
                    "positive_holdouts",
                    "holdouts",
                    "basis_flip_rows",
                    "negative_holdouts",
                ]
            ]
            if not summary.empty
            else summary
        ),
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
