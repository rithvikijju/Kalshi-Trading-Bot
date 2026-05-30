#!/usr/bin/env python3
"""Audit decision-time distance guards for the active BTC1H candidate.

This is a research-only diagnostic.  The guards use decision-time distance from
spot to strike, then report how fixed guard thresholds would have affected the
already-frozen BTC1H candidate across historical/proxy holdouts, live-websocket
holdouts, and the current official-settled shadow rows.

The current official rows are explicitly not promotion evidence because they
predate the clean scan-time model-input evidence clock.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_decision_distance_guard_audit_latest_codex"
ACTIVE_VARIANT = "high_conf_80_entry70_no_chase"
ACTIVE_LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--shadow-official-trades", type=Path, default=None)
    parser.add_argument("--thresholds-usd", default="0,25,50,75,100,150")
    parser.add_argument("--stress-cents", type=float, default=2.0)
    return parser.parse_args()


def latest_file(pattern: str, filename: str) -> Path | None:
    matches = [p / filename for p in BACKTEST_ROOT.glob(pattern) if (p / filename).exists()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def default_paths(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        "robustness_trades": args.robustness_trades
        or latest_file("btc1h_highconf_robustness_*", "all_input_trades.csv"),
        "direct_trades": args.direct_trades
        or latest_file("btc1h_highconf_direct_aggregate_feb09_may06_*", "trades.csv"),
        "shadow_official_trades": args.shadow_official_trades
        or latest_file("remote_btc_shadow_official_settlement_latest_codex", "shadow_official_trades.csv")
        or latest_file("btc_shadow_official_settlement_latest_codex", "shadow_official_trades.csv"),
    }


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def parse_thresholds(text: str) -> list[float]:
    values: list[float] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    return sorted(dict.fromkeys(values))


def parse_time(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return series.map(lambda x: pd.to_datetime(x, utc=True, errors="coerce"))


def max_drawdown(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0
    equity = np.cumsum(arr)
    return float(np.min(equity - np.maximum.accumulate(equity)))


def sharpe(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12 or not math.isfinite(std):
        return 0.0
    return float(np.mean(arr) / std * math.sqrt(arr.size))


def as_bool(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype=bool)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        return numeric.fillna(0.0) > 0.5
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def first_present(df: pd.DataFrame, names: list[str]) -> pd.Series:
    result = pd.Series(np.nan, index=df.index, dtype=float)
    for name in names:
        if name in df.columns:
            values = pd.to_numeric(df[name], errors="coerce")
            result = result.combine_first(values)
    return result


def signed_distance(df: pd.DataFrame) -> pd.Series:
    dist = pd.Series(np.nan, index=df.index, dtype=float)
    if "entry_spot_minus_strike" in df.columns:
        dist = dist.combine_first(pd.to_numeric(df["entry_spot_minus_strike"], errors="coerce"))
    if "signed_distance_usd" in df.columns:
        dist = dist.combine_first(pd.to_numeric(df["signed_distance_usd"], errors="coerce"))
    spot = first_present(df, ["entry_btc_spot", "entry_spot", "btc_spot_model"])
    strike = first_present(df, ["floor_strike", "ticker_strike", "strike"])
    return dist.combine_first(spot - strike)


def add_side_margin(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    side = work.get("side", pd.Series("", index=work.index)).astype(str).str.lower()
    dist = signed_distance(work)
    work["entry_signed_distance_usd"] = dist
    work["entry_side_margin_usd"] = np.where(side.eq("yes"), dist, np.where(side.eq("no"), -dist, np.nan))
    return work


def historical_holdout_label(row: pd.Series) -> str:
    source = str(row.get("source", ""))
    dataset = str(row.get("dataset", ""))
    split = str(row.get("split", ""))
    cadence = row.get("cadence_sec", "")
    if source == "websocket":
        try:
            return f"H4_live_ws_may06_12_stride{int(float(cadence))}s"
        except Exception:
            return f"H4_live_ws_may06_12_stride{cadence}s"
    direct_mapping = {
        "feb09_mar24_old_val": "D0_direct_feb09_mar24_old_context",
        "mar24_apr01_train": "D1_direct_mar24_apr01_dev",
        "apr01_apr08_train": "H1a_direct_apr01_08_holdout",
        "apr08_apr15_val": "H1b_direct_apr08_15_holdout",
        "apr15_apr23_val": "H2a_direct_apr15_23_holdout",
        "apr23_may01_val": "H2b_direct_apr23_may01_holdout",
        "may03_may06_external": "H3_direct_may03_06_external",
    }
    if split in direct_mapping:
        return direct_mapping[split]
    if "20260317_20260324" in dataset:
        return "D0_predexon_mar17_24_old_context"
    if "20260324_20260401" in dataset:
        return "D1_predexon_mar24_apr01_dev"
    if "apr1_14" in dataset:
        return "H1_predexon_apr01_14_holdout"
    if "apr15_30" in dataset:
        return "H2_predexon_apr15_30_holdout"
    if "may3_5" in dataset or "may1_06" in dataset:
        return "H3_predexon_may03_06_external"
    return f"other_{dataset or split or source}"


def prepare_historical(df: pd.DataFrame, evidence_source: str, stress_cents: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "variant" not in work.columns and "model" in work.columns:
        work["variant"] = work["model"]
    work = work[work["variant"].astype(str).eq(ACTIVE_VARIANT)].copy()
    if work.empty:
        return work
    work["evidence_source"] = evidence_source
    work["entry_time"] = parse_time(work.get("entry_time", pd.Series("", index=work.index)))
    work["holdout"] = work.apply(historical_holdout_label, axis=1)
    work["entry_price"] = pd.to_numeric(work["entry_price"], errors="coerce")
    work = add_side_margin(work)
    if "win_bool" in work.columns:
        win = as_bool(work["win_bool"])
    elif "win" in work.columns:
        win = as_bool(work["win"])
    elif "settlement" in work.columns:
        win = work["settlement"].astype(str).str.lower().eq(work["side"].astype(str).str.lower())
    else:
        win = pd.to_numeric(work.get("pnl", 0.0), errors="coerce").fillna(0.0) > 0
    stressed_entry = (work["entry_price"] + stress_cents / 100.0).clip(upper=0.99)
    fees = stressed_entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    premium = stressed_entry + fees
    work["research_win"] = win.astype(bool)
    work["research_premium_stressed"] = premium.astype(float)
    work["research_pnl_stressed"] = np.where(work["research_win"], 1.0 - premium, -premium)
    return work.dropna(subset=["entry_price", "entry_side_margin_usd"]).reset_index(drop=True)


def prepare_forward(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "ledger" in work.columns:
        work = work[work["ledger"].astype(str).eq(ACTIVE_LEDGER)].copy()
    if work.empty:
        return work
    work = work[work.get("official_result", pd.Series("", index=work.index)).astype(str).str.lower().isin({"yes", "no"})].copy()
    work["variant"] = ACTIVE_VARIANT
    work["evidence_source"] = "forward_shadow_official_current_diagnostic"
    work["holdout"] = "H5_forward_remote_official_current_diagnostic"
    work["entry_time"] = parse_time(work.get("created_at", pd.Series("", index=work.index)))
    work["entry_price"] = pd.to_numeric(work["entry_price"], errors="coerce")
    work = add_side_margin(work)
    work["official_pnl"] = pd.to_numeric(work.get("official_pnl", 0.0), errors="coerce").fillna(0.0)
    work["official_win_bool"] = as_bool(work.get("official_win", pd.Series(False, index=work.index)))
    work["official_premium"] = pd.to_numeric(work.get("official_premium", np.nan), errors="coerce")
    work["official_proxy_result_mismatch_bool"] = as_bool(
        work.get("official_proxy_result_mismatch", pd.Series(False, index=work.index))
    )
    proxy_win = as_bool(work.get("proxy_win", pd.Series(False, index=work.index)))
    work["proxy_win_official_loss"] = proxy_win & ~work["official_win_bool"]
    return work.dropna(subset=["entry_price", "entry_side_margin_usd"]).reset_index(drop=True)


def summarize_pnl(group: pd.DataFrame, pnl_col: str, win_col: str, premium_col: str | None = None) -> dict[str, object]:
    pnl = pd.to_numeric(group[pnl_col], errors="coerce").fillna(0.0)
    win = as_bool(group[win_col]) if win_col in group.columns else pnl > 0
    premium = (
        pd.to_numeric(group[premium_col], errors="coerce").fillna(0.0)
        if premium_col and premium_col in group.columns
        else pd.Series(0.0, index=group.index)
    )
    return {
        "trades": int(len(group)),
        "pnl": round(float(pnl.sum()), 4),
        "premium": round(float(premium.sum()), 4),
        "win_rate": round(float(win.mean()), 4) if len(win) else 0.0,
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(group["entry_price"], errors="coerce").mean()), 4),
        "avg_entry_side_margin_usd": round(float(pd.to_numeric(group["entry_side_margin_usd"], errors="coerce").mean()), 4),
        "min_entry_side_margin_usd": round(float(pd.to_numeric(group["entry_side_margin_usd"], errors="coerce").min()), 4),
        "first_entry": str(group["entry_time"].min()) if "entry_time" in group else "",
        "last_entry": str(group["entry_time"].max()) if "entry_time" in group else "",
    }


def build_threshold_tables(
    historical: pd.DataFrame, forward: pd.DataFrame, thresholds: list[float]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    holdout_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    forward_rows: list[pd.DataFrame] = []

    for threshold in thresholds:
        hist_keep = historical[historical["entry_side_margin_usd"] >= threshold].copy()
        fwd_keep = forward[forward["entry_side_margin_usd"] >= threshold].copy()
        excluded_fwd = forward[forward["entry_side_margin_usd"] < threshold].copy()

        for (source, holdout), group in hist_keep.groupby(["evidence_source", "holdout"], dropna=False, sort=True):
            row = {
                "threshold_usd": threshold,
                "evidence_source": source,
                "holdout": holdout,
                "settlement_label": "proxy_or_captured_result_plus_adverse_entry_stress",
            }
            row.update(summarize_pnl(group, "research_pnl_stressed", "research_win", "research_premium_stressed"))
            holdout_rows.append(row)

        if not fwd_keep.empty:
            row = {
                "threshold_usd": threshold,
                "evidence_source": "forward_shadow_official_current_diagnostic",
                "holdout": "H5_forward_remote_official_current_diagnostic",
                "settlement_label": "kalshi_rest_official_actual_fee_current_diagnostic",
            }
            row.update(summarize_pnl(fwd_keep, "official_pnl", "official_win_bool", "official_premium"))
            row["official_proxy_mismatches"] = int(fwd_keep["official_proxy_result_mismatch_bool"].sum())
            row["official_proxy_mismatch_rate"] = round(float(fwd_keep["official_proxy_result_mismatch_bool"].mean()), 6)
            row["proxy_win_official_loss_flips"] = int(fwd_keep["proxy_win_official_loss"].sum())
            holdout_rows.append(row)

        for kind, rows in [("included", fwd_keep), ("excluded", excluded_fwd)]:
            if rows.empty:
                continue
            tagged = rows.copy()
            tagged["threshold_usd"] = threshold
            tagged["guard_row_status"] = kind
            forward_rows.append(tagged)

        hist_holdouts = (
            pd.DataFrame([r for r in holdout_rows if r["threshold_usd"] == threshold and r["evidence_source"] != "forward_shadow_official_current_diagnostic"])
            if holdout_rows
            else pd.DataFrame()
        )
        historical_holdouts = int(len(hist_holdouts))
        positive_historical_holdouts = (
            int((pd.to_numeric(hist_holdouts.get("pnl", pd.Series(dtype=float)), errors="coerce") > 0).sum())
            if not hist_holdouts.empty
            else 0
        )
        ws_holdouts = (
            hist_holdouts[hist_holdouts["holdout"].astype(str).str.startswith("H4_live_ws")]
            if not hist_holdouts.empty
            else pd.DataFrame()
        )
        ws_positive = (
            int((pd.to_numeric(ws_holdouts.get("pnl", pd.Series(dtype=float)), errors="coerce") > 0).sum())
            if not ws_holdouts.empty
            else 0
        )
        ws_total = int(len(ws_holdouts))
        hist_pnl = (
            float(pd.to_numeric(hist_keep.get("research_pnl_stressed", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            if not hist_keep.empty
            else 0.0
        )
        fwd_pnl = float(pd.to_numeric(fwd_keep.get("official_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
        fwd_mismatches = int(fwd_keep["official_proxy_result_mismatch_bool"].sum()) if not fwd_keep.empty else 0
        fwd_flips = int(fwd_keep["proxy_win_official_loss"].sum()) if not fwd_keep.empty else 0
        summary_rows.append(
            {
                "threshold_usd": threshold,
                "variant": ACTIVE_VARIANT,
                "historical_rows": int(len(hist_keep)),
                "historical_pnl_stressed": round(hist_pnl, 4),
                "positive_historical_holdouts": positive_historical_holdouts,
                "historical_holdouts": historical_holdouts,
                "negative_historical_holdouts": ";".join(
                    hist_holdouts.loc[pd.to_numeric(hist_holdouts.get("pnl", pd.Series(dtype=float)), errors="coerce") <= 0, "holdout"].astype(str).tolist()
                )
                if not hist_holdouts.empty
                else "",
                "ws_positive_cadences": ws_positive,
                "ws_cadences": ws_total,
                "forward_official_rows_current_diagnostic": int(len(fwd_keep)),
                "forward_official_pnl_current_diagnostic": round(fwd_pnl, 4),
                "forward_official_win_rate_current_diagnostic": round(float(fwd_keep["official_win_bool"].mean()), 4)
                if not fwd_keep.empty
                else 0.0,
                "forward_official_proxy_mismatches_current_diagnostic": fwd_mismatches,
                "forward_official_proxy_mismatch_rate_current_diagnostic": round(float(fwd_mismatches / len(fwd_keep)), 6)
                if len(fwd_keep)
                else 0.0,
                "forward_proxy_win_official_loss_flips_current_diagnostic": fwd_flips,
                "excluded_current_official_rows": int(len(excluded_fwd)),
                "excluded_current_mismatch_rows": int(excluded_fwd["official_proxy_result_mismatch_bool"].sum())
                if not excluded_fwd.empty
                else 0,
                "deployable_now": False,
                "guard_status": "research_diagnostic_only_not_preregistered_forward_evidence",
            }
        )

    holdout = pd.DataFrame(holdout_rows)
    summary = pd.DataFrame(summary_rows)
    fwd_rows = pd.concat(forward_rows, ignore_index=True, sort=False) if forward_rows else pd.DataFrame()
    return summary, holdout, fwd_rows


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    thresholds = parse_thresholds(args.thresholds_usd)
    paths = default_paths(args)

    robustness = prepare_historical(read_csv(paths["robustness_trades"]), "robustness_trade_logs", args.stress_cents)
    direct = prepare_historical(read_csv(paths["direct_trades"]), "direct_predexon_trade_logs", args.stress_cents)
    historical = pd.concat([df for df in [robustness, direct] if not df.empty], ignore_index=True, sort=False)
    forward = prepare_forward(read_csv(paths["shadow_official_trades"]))
    if historical.empty and forward.empty:
        raise SystemExit("No BTC1H active-candidate rows found for distance guard audit.")

    summary, holdout, forward_rows = build_threshold_tables(historical, forward, thresholds)
    summary.to_csv(args.out_dir / "btc1h_decision_distance_guard_summary.csv", index=False)
    holdout.to_csv(args.out_dir / "btc1h_decision_distance_guard_holdouts.csv", index=False)
    forward_cols = [
        "threshold_usd",
        "guard_row_status",
        "created_at",
        "market_ticker",
        "side",
        "entry_price",
        "entry_btc_spot",
        "floor_strike",
        "entry_signed_distance_usd",
        "entry_side_margin_usd",
        "official_result",
        "proxy_result",
        "official_pnl",
        "proxy_pnl",
        "official_proxy_result_mismatch_bool",
        "proxy_win_official_loss",
        "official_minus_proxy_spot",
        "quote_age_ms",
        "top_visible_qty",
    ]
    if not forward_rows.empty:
        cols = [c for c in forward_cols if c in forward_rows.columns]
        forward_rows[cols].to_csv(args.out_dir / "btc1h_decision_distance_guard_forward_rows.csv", index=False)
    else:
        pd.DataFrame(columns=forward_cols).to_csv(args.out_dir / "btc1h_decision_distance_guard_forward_rows.csv", index=False)

    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": ACTIVE_VARIANT,
        "active_ledger": ACTIVE_LEDGER,
        "thresholds_usd": thresholds,
        "stress_cents": args.stress_cents,
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
        "deployable_now": False,
        "note": "Research-only distance guard diagnostic. Current official rows predate the clean scan-time evidence clock and cannot promote a guard.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "threshold_usd",
        "historical_rows",
        "historical_pnl_stressed",
        "positive_historical_holdouts",
        "historical_holdouts",
        "ws_positive_cadences",
        "ws_cadences",
        "forward_official_rows_current_diagnostic",
        "forward_official_pnl_current_diagnostic",
        "forward_official_proxy_mismatches_current_diagnostic",
        "forward_proxy_win_official_loss_flips_current_diagnostic",
        "excluded_current_official_rows",
        "excluded_current_mismatch_rows",
    ]
    report = [
        "# BTC1H Decision-Time Distance Guard Audit",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Variant: `{ACTIVE_VARIANT}`",
        f"Historical/proxy adverse entry stress: `+{args.stress_cents:.1f}c`.",
        "",
        "## Verdict",
        "",
        "- Research-only diagnostic. No threshold here is deployable from current rows.",
        "- The guard feature is decision-time side margin: YES uses `spot - strike`, NO uses `strike - spot`.",
        "- Current official rows are stale-clock diagnostics and must not count toward promotion.",
        "",
        "## Threshold Summary",
        "",
        markdown_table(summary[compact_cols] if not summary.empty else summary),
        "",
        "## Holdout Detail",
        "",
        markdown_table(
            holdout[
                [
                    "threshold_usd",
                    "evidence_source",
                    "holdout",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "min_entry_side_margin_usd",
                    "official_proxy_mismatch_rate",
                ]
                if "official_proxy_mismatch_rate" in holdout.columns
                else [
                    "threshold_usd",
                    "evidence_source",
                    "holdout",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "min_entry_side_margin_usd",
                ]
            ].fillna("")
        ),
        "",
        "## Interpretation",
        "",
        "- If a distance guard looks attractive, the only honest use is to preregister it for a future clean-clock paper run.",
        "- Do not fit a guard from the current official mismatch row; use this table to decide which prospective guard is worth freezing.",
        "- A guard that removes mismatches but destroys historical holdout stability should be rejected before any forward restart.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
