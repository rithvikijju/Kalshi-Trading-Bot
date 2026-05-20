#!/usr/bin/env python3
"""Compare Predexon market metadata labels against Kalshi REST official labels.

This is a source-fidelity audit. It answers a narrow question: can full-coverage
Predexon metadata results be treated as an official-like historical research
label when Kalshi REST results are missing? Even a clean overlap does not make
metadata rows promotion evidence; final promotion still requires live/paper rows
with Kalshi REST official settlement where available.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_PREDEXON = (
    BACKTEST_ROOT
    / "btc15m_predexon_rest_official_latest_codex"
    / "predexon_trades_rest_official.parquet"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_predexon_metadata_vs_rest_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


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
    Candidate("all_rows", "", "both", 0.0, min_side_fair_p=0.0, min_edge_cents=-999.0, max_entry=1.0, max_spread_cents=100.0, ttl_min=-999.0, ttl_max=999.0),
    Candidate("q250_both", "ttl10_12_entry50_q250", "both", 250.0),
    Candidate("q250_firstskip_qty500", "ttl10_12_entry50_q250", "both", 500.0),
    Candidate("q250_yes", "ttl10_12_entry50_q250", "yes", 250.0),
    Candidate("q500_both", "ttl10_12_entry50_q500", "both", 500.0),
    Candidate("q1000_both", "ttl10_12_entry50_q1000", "both", 1000.0),
    Candidate("q1000_yes", "ttl10_12_entry50_q1000", "yes", 1000.0),
    Candidate("q250_qspeed05_both", "ttl10_12_entry50_q250_qspeed05", "both", 250.0),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Predexon metadata labels versus REST official labels.")
    parser.add_argument("--predexon-trades", type=Path, default=DEFAULT_PREDEXON)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-official-mismatch-rate", type=float, default=0.0)
    parser.add_argument("--min-rest-overlap-rows", type=int, default=20)
    return parser.parse_args()


def clean_result(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower()


def valid_result(series: pd.Series) -> pd.Series:
    return clean_result(series).isin(["yes", "no"])


def apply_candidate(trades: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    if candidate.name == "all_rows":
        return trades.copy()
    out = trades[trades["strategy"].astype(str).eq(candidate.strategy)].copy()
    if out.empty:
        return out
    side = clean_result(out["side"])
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
    return out.loc[mask].copy()


def pnl_delta(df: pd.DataFrame, left: str, right: str) -> float:
    if left not in df or right not in df:
        return 0.0
    left_v = pd.to_numeric(df[left], errors="coerce")
    right_v = pd.to_numeric(df[right], errors="coerce")
    both = left_v.notna() & right_v.notna()
    if not bool(both.any()):
        return 0.0
    return float((left_v.loc[both] - right_v.loc[both]).sum())


def summarize_candidate(
    trades: pd.DataFrame,
    candidate: Candidate,
    *,
    max_official_mismatch_rate: float,
    min_rest_overlap_rows: int,
) -> dict[str, Any]:
    selected = apply_candidate(trades, candidate)
    meta_valid = valid_result(selected.get("predexon_metadata_result", pd.Series(dtype=str)))
    proxy_valid = valid_result(selected.get("result", pd.Series(dtype=str)))
    rest_valid = valid_result(selected.get("official_result_rest", pd.Series(dtype=str)))
    meta_proxy_overlap = selected.loc[meta_valid & proxy_valid].copy()
    rest_meta_overlap = selected.loc[rest_valid & meta_valid].copy()
    rest_proxy_overlap = selected.loc[rest_valid & proxy_valid].copy()

    metadata_matches_proxy = (
        clean_result(meta_proxy_overlap["predexon_metadata_result"]).eq(clean_result(meta_proxy_overlap["result"]))
        if not meta_proxy_overlap.empty
        else pd.Series(dtype=bool)
    )
    metadata_matches_rest = (
        clean_result(rest_meta_overlap["predexon_metadata_result"]).eq(clean_result(rest_meta_overlap["official_result_rest"]))
        if not rest_meta_overlap.empty
        else pd.Series(dtype=bool)
    )
    proxy_matches_rest = (
        clean_result(rest_proxy_overlap["result"]).eq(clean_result(rest_proxy_overlap["official_result_rest"]))
        if not rest_proxy_overlap.empty
        else pd.Series(dtype=bool)
    )

    rest_metadata_mismatches = int((~metadata_matches_rest).sum()) if len(metadata_matches_rest) else 0
    rest_metadata_mismatch_rate = float(rest_metadata_mismatches / len(metadata_matches_rest)) if len(metadata_matches_rest) else 0.0
    metadata_source_match_rate = float(metadata_matches_proxy.mean()) if len(metadata_matches_proxy) else 0.0
    rest_proxy_mismatch_rate = float((~proxy_matches_rest).mean()) if len(proxy_matches_rest) else 0.0

    blockers: list[str] = []
    if len(rest_meta_overlap) < min_rest_overlap_rows:
        blockers.append("too_few_rest_metadata_overlap_rows")
    if rest_metadata_mismatch_rate > max_official_mismatch_rate:
        blockers.append("metadata_rest_mismatch_rate_nonzero")
    if blockers:
        research_status = "FAIL_RESEARCH_LABEL_OVERLAP"
    else:
        research_status = "PASS_RESEARCH_LABEL_OVERLAP"
    source_identity_note = (
        "metadata_matches_existing_result_column"
        if metadata_source_match_rate >= 0.999 and len(meta_proxy_overlap) > 0
        else ""
    )

    return {
        **asdict(candidate),
        "selected_rows": int(len(selected)),
        "metadata_rows": int(meta_valid.sum()) if len(selected) else 0,
        "source_result_rows": int(proxy_valid.sum()) if len(selected) else 0,
        "rest_official_rows": int(rest_valid.sum()) if len(selected) else 0,
        "metadata_source_overlap_rows": int(len(meta_proxy_overlap)),
        "metadata_source_match_rows": int(metadata_matches_proxy.sum()) if len(metadata_matches_proxy) else 0,
        "metadata_source_match_rate": metadata_source_match_rate,
        "source_identity_note": source_identity_note,
        "rest_metadata_overlap_rows": int(len(rest_meta_overlap)),
        "rest_metadata_match_rows": int(metadata_matches_rest.sum()) if len(metadata_matches_rest) else 0,
        "rest_metadata_mismatches": rest_metadata_mismatches,
        "rest_metadata_mismatch_rate": rest_metadata_mismatch_rate,
        "rest_proxy_overlap_rows": int(len(rest_proxy_overlap)),
        "rest_proxy_mismatch_rate": rest_proxy_mismatch_rate,
        "official_minus_metadata_pnl_2c_on_overlap": pnl_delta(
            rest_meta_overlap, "pnl_official_rest_2c", "pnl_predexon_metadata_2c"
        ),
        "official_minus_proxy_pnl_2c_on_overlap": pnl_delta(rest_proxy_overlap, "pnl_official_rest_2c", "pnl_stress"),
        "metadata_research_label_status": research_status,
        "metadata_usable_as_historical_research_label": len(blockers) == 0,
        "metadata_usable_as_promotion_label": False,
        "deployment_usable": False,
        "blockers": ";".join(blockers),
    }


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trades = pd.read_parquet(args.predexon_trades).copy()

    rows = [
        summarize_candidate(
            trades,
            candidate,
            max_official_mismatch_rate=args.max_official_mismatch_rate,
            min_rest_overlap_rows=args.min_rest_overlap_rows,
        )
        for candidate in CANDIDATES
    ]
    summary = pd.DataFrame(rows)
    summary.to_csv(args.out_dir / "predexon_metadata_vs_rest_summary.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predexon_trades": str(args.predexon_trades),
        "rows": int(len(trades)),
        "max_official_mismatch_rate": args.max_official_mismatch_rate,
        "min_rest_overlap_rows": args.min_rest_overlap_rows,
        "metadata_research_label_pass_count": int(summary["metadata_usable_as_historical_research_label"].sum()),
        "note": "Diagnostic only. Metadata labels can support historical research if REST overlap agrees, but cannot replace live/paper REST-official promotion evidence.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC15M Predexon Metadata vs REST Official Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        summary.round(4).to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- `deployment_usable` is intentionally false for every row.",
        "- A clean REST overlap supports Predexon metadata as a historical research label for uncovered rows.",
        "- `source_identity_note` records when metadata is identical to the existing `result` column; this is provenance information, not deployment evidence.",
        "- REST official settlement remains the promotion label where available; metadata-only rows are research diagnostics only.",
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
