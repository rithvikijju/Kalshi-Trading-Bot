#!/usr/bin/env python3
"""Evaluate simple preregisterable settlement-basis veto candidates.

This is a research diagnostic, not a deployment gate. It asks what would have
happened to the already official-scored basis-risk rows if we had used simple
decision-time-safe vetoes such as YES-only or minimum distance-to-strike.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_BASIS_RISK_DIR = BACKTEST_ROOT / "btc_settlement_basis_risk_audit_latest_codex"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_settlement_basis_guard_candidates_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Guard:
    name: str
    description: str
    yes_only: bool = False
    min_aligned_distance_usd: float | None = None
    min_aligned_distance_bps: float | None = None
    no_side_min_aligned_distance_usd: float | None = None
    no_side_min_aligned_distance_bps: float | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC settlement-basis guard candidate diagnostics.")
    p.add_argument("--basis-risk-dir", type=Path, default=DEFAULT_BASIS_RISK_DIR)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-btc15m-rows", type=int, default=100)
    p.add_argument("--min-btc1h-rows", type=int, default=50)
    p.add_argument("--max-mismatch-rate", type=float, default=0.02)
    p.add_argument("--min-kept-trade-rate", type=float, default=0.20)
    return p.parse_args()


def norm_text(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().replace({"nan": "", "none": "", "<na>": ""})


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def first_existing_numeric(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    for col in cols:
        if col in df:
            return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype=float)


def load_rows(path: Path) -> pd.DataFrame:
    rows = read_csv(path / "settlement_basis_risk_rows.csv")
    if rows.empty:
        return pd.DataFrame()
    out = rows.copy()
    out["family"] = out.get("family", "").astype(str).str.upper()
    out["candidate"] = out.get("candidate", "").astype(str)
    out["side"] = norm_text(out.get("side", pd.Series("", index=out.index)))
    out["official_result"] = norm_text(out.get("official_result", pd.Series("", index=out.index)))
    out["proxy_result"] = norm_text(out.get("proxy_result", pd.Series("", index=out.index)))
    out["official_pnl_2c"] = first_existing_numeric(
        out,
        ["official_pnl_2c", "pnl_official_rest_2c", "official_pnl"],
    )
    out["proxy_pnl_2c"] = first_existing_numeric(out, ["proxy_pnl_2c", "pnl_proxy_2c", "proxy_pnl"])
    if "aligned_decision_distance_usd" not in out:
        decision_distance = first_existing_numeric(out, ["decision_distance_usd", "entry_spot_minus_strike"])
        out["aligned_decision_distance_usd"] = np.where(out["side"].eq("yes"), decision_distance, -decision_distance)
    else:
        out["aligned_decision_distance_usd"] = pd.to_numeric(out["aligned_decision_distance_usd"], errors="coerce")
    if "aligned_decision_distance_bps" not in out:
        decision_bps = first_existing_numeric(out, ["entry_spot_distance_bps"])
        out["aligned_decision_distance_bps"] = np.where(out["side"].eq("yes"), decision_bps, -decision_bps)
    else:
        out["aligned_decision_distance_bps"] = pd.to_numeric(out["aligned_decision_distance_bps"], errors="coerce")
    both_results = out["official_result"].isin(["yes", "no"]) & out["proxy_result"].isin(["yes", "no"])
    out["both_result_rows"] = both_results
    if "proxy_official_mismatch" not in out:
        out["proxy_official_mismatch"] = both_results & out["official_result"].ne(out["proxy_result"])
    else:
        out["proxy_official_mismatch"] = out["proxy_official_mismatch"].fillna(False).astype(bool)
    out["pnl_delta_official_minus_proxy_2c"] = out["official_pnl_2c"] - out["proxy_pnl_2c"]
    if "adverse_proxy_official_mismatch" not in out:
        out["adverse_proxy_official_mismatch"] = out["proxy_official_mismatch"] & out[
            "pnl_delta_official_minus_proxy_2c"
        ].lt(0)
    else:
        out["adverse_proxy_official_mismatch"] = out["adverse_proxy_official_mismatch"].fillna(False).astype(bool)
    if "proxy_win_official_loss" not in out:
        out["proxy_win_official_loss"] = out["proxy_official_mismatch"] & out[
            "pnl_delta_official_minus_proxy_2c"
        ].lt(0)
    else:
        out["proxy_win_official_loss"] = out["proxy_win_official_loss"].fillna(False).astype(bool)
    valid = out["both_result_rows"] & out["official_pnl_2c"].notna() & out["proxy_pnl_2c"].notna()
    return out.loc[valid].copy()


def guard_library() -> list[Guard]:
    guards: list[Guard] = [
        Guard("no_guard", "Keep all rows; baseline for comparison."),
        Guard("yes_only", "Veto every NO-side row; keeps only decision-time YES entries.", yes_only=True),
    ]
    for usd in [10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0]:
        guards.append(
            Guard(
                f"aligned_distance_usd_ge_{int(usd)}",
                f"Require side-aligned decision spot distance to strike >= ${usd:.0f}.",
                min_aligned_distance_usd=usd,
            )
        )
    for bps in [2.0, 3.0, 5.0, 7.5, 10.0]:
        label = str(bps).replace(".", "p")
        guards.append(
            Guard(
                f"aligned_distance_bps_ge_{label}",
                f"Require side-aligned decision spot distance to strike >= {bps:g} bps.",
                min_aligned_distance_bps=bps,
            )
        )
    for usd in [20.0, 30.0, 40.0, 50.0]:
        guards.append(
            Guard(
                f"yes_or_no_distance_usd_ge_{int(usd)}",
                f"Always allow YES; require NO-side aligned distance >= ${usd:.0f}.",
                no_side_min_aligned_distance_usd=usd,
            )
        )
    for bps in [3.0, 5.0, 7.5, 10.0]:
        label = str(bps).replace(".", "p")
        guards.append(
            Guard(
                f"yes_or_no_distance_bps_ge_{label}",
                f"Always allow YES; require NO-side aligned distance >= {bps:g} bps.",
                no_side_min_aligned_distance_bps=bps,
            )
        )
    return guards


def apply_guard(rows: pd.DataFrame, guard: Guard) -> pd.Series:
    keep = pd.Series(True, index=rows.index)
    if guard.yes_only:
        keep &= rows["side"].eq("yes")
    if guard.min_aligned_distance_usd is not None:
        keep &= pd.to_numeric(rows["aligned_decision_distance_usd"], errors="coerce").ge(
            guard.min_aligned_distance_usd
        )
    if guard.min_aligned_distance_bps is not None:
        keep &= pd.to_numeric(rows["aligned_decision_distance_bps"], errors="coerce").ge(
            guard.min_aligned_distance_bps
        )
    if guard.no_side_min_aligned_distance_usd is not None:
        no_side = rows["side"].eq("no")
        keep &= ~no_side | pd.to_numeric(rows["aligned_decision_distance_usd"], errors="coerce").ge(
            guard.no_side_min_aligned_distance_usd
        )
    if guard.no_side_min_aligned_distance_bps is not None:
        no_side = rows["side"].eq("no")
        keep &= ~no_side | pd.to_numeric(rows["aligned_decision_distance_bps"], errors="coerce").ge(
            guard.no_side_min_aligned_distance_bps
        )
    return keep.fillna(False)


def sum_numeric(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0.0).sum())


def max_drawdown(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def guard_blockers(row: dict[str, Any], args: argparse.Namespace) -> list[str]:
    min_rows = args.min_btc15m_rows if row["family"] == "BTC15M" else args.min_btc1h_rows
    blockers = ["no_preregistered_fresh_forward_guard_evaluation"]
    if row["kept_rows"] < min_rows:
        blockers.append("too_few_kept_official_rows")
    if row["kept_trade_rate"] < args.min_kept_trade_rate:
        blockers.append("guard_too_sparse")
    if row["kept_official_pnl_2c"] <= 0:
        blockers.append("kept_official_pnl_not_positive")
    if row["kept_mismatch_rate"] > args.max_mismatch_rate:
        blockers.append("kept_proxy_official_mismatch_rate_high")
    if row["kept_adverse_mismatches"] > 0:
        blockers.append("kept_adverse_mismatches")
    if row["kept_proxy_win_official_loss_rows"] > 0:
        blockers.append("kept_proxy_win_official_loss_rows")
    return blockers


def summarize_guard(group: pd.DataFrame, kept: pd.DataFrame, vetoed: pd.DataFrame, guard: Guard, args: argparse.Namespace) -> dict[str, Any]:
    input_rows = int(len(group))
    kept_rows = int(len(kept))
    vetoed_rows = int(len(vetoed))
    row: dict[str, Any] = {
        "family": str(group["family"].iloc[0]),
        "candidate": str(group["candidate"].iloc[0]),
        "guard": guard.name,
        "guard_description": guard.description,
        "input_rows": input_rows,
        "kept_rows": kept_rows,
        "vetoed_rows": vetoed_rows,
        "kept_trade_rate": float(kept_rows / input_rows) if input_rows else 0.0,
        "kept_yes_rows": int(kept["side"].eq("yes").sum()) if kept_rows else 0,
        "kept_no_rows": int(kept["side"].eq("no").sum()) if kept_rows else 0,
        "kept_official_pnl_2c": round(sum_numeric(kept, "official_pnl_2c"), 4),
        "kept_proxy_pnl_2c": round(sum_numeric(kept, "proxy_pnl_2c"), 4),
        "kept_official_minus_proxy_pnl_2c": round(sum_numeric(kept, "pnl_delta_official_minus_proxy_2c"), 4),
        "kept_mismatches": int(kept["proxy_official_mismatch"].sum()) if kept_rows else 0,
        "kept_adverse_mismatches": int(kept["adverse_proxy_official_mismatch"].sum()) if kept_rows else 0,
        "kept_proxy_win_official_loss_rows": int(kept["proxy_win_official_loss"].sum()) if kept_rows else 0,
        "kept_max_dd_official_2c": round(max_drawdown(kept["official_pnl_2c"].reset_index(drop=True)), 4)
        if kept_rows
        else 0.0,
        "vetoed_official_pnl_2c": round(sum_numeric(vetoed, "official_pnl_2c"), 4),
        "vetoed_proxy_pnl_2c": round(sum_numeric(vetoed, "proxy_pnl_2c"), 4),
        "vetoed_mismatches": int(vetoed["proxy_official_mismatch"].sum()) if vetoed_rows else 0,
        "vetoed_adverse_mismatches": int(vetoed["adverse_proxy_official_mismatch"].sum()) if vetoed_rows else 0,
        "vetoed_proxy_win_official_loss_rows": int(vetoed["proxy_win_official_loss"].sum()) if vetoed_rows else 0,
    }
    row["kept_mismatch_rate"] = round(float(row["kept_mismatches"] / kept_rows), 4) if kept_rows else 0.0
    row["vetoed_mismatch_rate"] = round(float(row["vetoed_mismatches"] / vetoed_rows), 4) if vetoed_rows else 0.0
    blockers = guard_blockers(row, args)
    row["research_promising"] = (
        row["kept_rows"] >= 5
        and row["kept_official_pnl_2c"] > 0
        and row["kept_adverse_mismatches"] == 0
        and row["kept_proxy_win_official_loss_rows"] == 0
        and row["kept_trade_rate"] >= args.min_kept_trade_rate
    )
    row["deployable_guard_now"] = False
    row["guard_blockers"] = ";".join(blockers)
    return row


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.basis_risk_dir)
    guards = guard_library()
    summary_rows: list[dict[str, Any]] = []
    detail_frames: list[pd.DataFrame] = []

    if not rows.empty:
        for (_family, _candidate), group in rows.groupby(["family", "candidate"], dropna=False):
            group = group.sort_values([c for c in ["created_at", "received_at_utc", "market_ticker"] if c in group.columns])
            for guard in guards:
                keep_mask = apply_guard(group, guard)
                kept = group.loc[keep_mask].copy()
                vetoed = group.loc[~keep_mask].copy()
                summary_rows.append(summarize_guard(group, kept, vetoed, guard, args))
                detail = group.copy()
                detail["guard"] = guard.name
                detail["guard_kept"] = keep_mask.astype(bool)
                detail_frames.append(detail)

    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary = summary.sort_values(
            [
                "research_promising",
                "candidate",
                "kept_proxy_win_official_loss_rows",
                "kept_adverse_mismatches",
                "kept_official_pnl_2c",
                "kept_rows",
            ],
            ascending=[False, True, True, True, False, False],
        )
    details = pd.concat(detail_frames, ignore_index=True, sort=False) if detail_frames else pd.DataFrame()

    summary.to_csv(args.out_dir / "settlement_basis_guard_summary.csv", index=False)
    details.to_csv(args.out_dir / "settlement_basis_guard_row_details.csv", index=False)
    if not summary.empty:
        promising = summary[summary["research_promising"]].copy()
    else:
        promising = pd.DataFrame()
    promising.to_csv(args.out_dir / "settlement_basis_guard_promising.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis_risk_dir": str(args.basis_risk_dir),
        "rows": int(len(rows)),
        "guards": len(guards),
        "summary_rows": int(len(summary)),
        "promising_research_rows": int(len(promising)),
        "min_btc15m_rows": args.min_btc15m_rows,
        "min_btc1h_rows": args.min_btc1h_rows,
        "max_mismatch_rate": args.max_mismatch_rate,
        "min_kept_trade_rate": args.min_kept_trade_rate,
        "note": "Research-only decision-time-safe veto diagnostics; no guard is deployable without preregistered forward evaluation.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    top_cols = [
        "family",
        "candidate",
        "guard",
        "research_promising",
        "kept_rows",
        "kept_trade_rate",
        "kept_official_pnl_2c",
        "kept_proxy_pnl_2c",
        "kept_mismatches",
        "kept_adverse_mismatches",
        "kept_proxy_win_official_loss_rows",
        "vetoed_rows",
        "vetoed_official_pnl_2c",
        "vetoed_proxy_win_official_loss_rows",
        "guard_blockers",
    ]
    top = summary[[c for c in top_cols if c in summary.columns]].head(40) if not summary.empty else pd.DataFrame()
    report = [
        "# BTC Settlement Basis Guard Candidates",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Interpretation",
        "",
        "- These are preregistration candidates for future paper/live replay evaluation, not deployable guards.",
        "- Guard inputs are decision-time-safe side and distance-to-strike fields; official/proxy outcomes are used only for scoring after the fact.",
        "- `deployable_guard_now` is always false because no guard has fresh preregistered forward evaluation.",
        "",
        "## Top Diagnostic Rows",
        "",
        top.fillna("").to_string(index=False) if not top.empty else "_No rows._",
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
