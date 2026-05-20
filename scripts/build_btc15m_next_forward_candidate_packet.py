#!/usr/bin/env python3
"""Build a BTC15M next-candidate preregistration packet.

This packet freezes candidate directions for future paper-only collection. It
does not start processes, tune thresholds, deploy, or count old rows as
promotion evidence.
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
DEFAULT_FEATURE_TABLE = (
    BACKTEST_ROOT
    / "btc_official_settlement_feature_table_latest_codex"
    / "official_settlement_feature_table.csv"
)
DEFAULT_BASIS_GUARD_SUMMARY = (
    BACKTEST_ROOT
    / "btc_settlement_basis_guard_candidates_latest_codex"
    / "settlement_basis_guard_summary.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_next_forward_candidate_packet_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class CandidateFreeze:
    candidate_id: str
    status: str
    family: str
    base_candidate: str
    side: str
    wrapper_script: str
    mode: str
    ttl_min: float
    ttl_max: float
    min_visible_qty: float
    first_signal_min_visible_qty: float
    min_side_fair_p: float
    min_edge_cents: float
    max_entry: float
    max_spread_cents: float
    max_contracts: int
    min_future_official_rows: int
    freeze_rationale: str


@dataclass(frozen=True)
class BasisGuardFreeze:
    guard_candidate_id: str
    status: str
    family: str
    base_candidate: str
    side_policy: str
    wrapper_script: str
    evaluation_mode: str
    guard_name: str
    guard_type: str
    min_aligned_distance_usd: float | None
    no_side_min_aligned_distance_usd: float | None
    min_future_official_rows: int
    max_contracts: int
    deployable_guard_now: bool
    freeze_rationale: str


FREEZES = [
    CandidateFreeze(
        candidate_id="btc15m_q250_firstskip_qty500_yes_forward",
        status="PREREGISTER_FOR_FUTURE_PAPER_COLLECTION",
        family="BTC15M",
        base_candidate="q250_firstskip_qty500",
        side="yes",
        wrapper_script=r"scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        mode="paper_only",
        ttl_min=10.0,
        ttl_max=12.0,
        min_visible_qty=250.0,
        first_signal_min_visible_qty=500.0,
        min_side_fair_p=0.60,
        min_edge_cents=12.0,
        max_entry=0.50,
        max_spread_cents=2.0,
        max_contracts=1,
        min_future_official_rows=100,
        freeze_rationale=(
            "Fresh YES-only q250 firstskip shadow to avoid the known NO-side "
            "official/proxy settlement fragility. Prior rows are diagnostics only."
        ),
    ),
    CandidateFreeze(
        candidate_id="btc15m_q250_firstskip_qty500_no_blocked",
        status="DO_NOT_START_AS_NEW_FORWARD_CANDIDATE",
        family="BTC15M",
        base_candidate="q250_firstskip_qty500",
        side="no",
        wrapper_script="",
        mode="none",
        ttl_min=10.0,
        ttl_max=12.0,
        min_visible_qty=250.0,
        first_signal_min_visible_qty=500.0,
        min_side_fair_p=0.60,
        min_edge_cents=12.0,
        max_entry=0.50,
        max_spread_cents=2.0,
        max_contracts=1,
        min_future_official_rows=100,
        freeze_rationale=(
            "NO-side q250 firstskip has adverse official/proxy settlement flips "
            "in old live replay and should not be expanded without a preregistered "
            "basis guard."
        ),
    ),
    CandidateFreeze(
        candidate_id="btc15m_q1000_yes_existing_sparse_control",
        status="KEEP_EXISTING_FORWARD_ONLY_SPARSE_CONTROL",
        family="BTC15M",
        base_candidate="q1000_yes",
        side="yes",
        wrapper_script=r"scripts\btc15m_f2_q1000_yes_shadow.py",
        mode="paper_only_existing",
        ttl_min=10.0,
        ttl_max=12.0,
        min_visible_qty=1000.0,
        first_signal_min_visible_qty=0.0,
        min_side_fair_p=0.60,
        min_edge_cents=12.0,
        max_entry=0.50,
        max_spread_cents=2.0,
        max_contracts=1,
        min_future_official_rows=100,
        freeze_rationale="Existing YES-only sparse control; cleaner settlement behavior but too few rows.",
    ),
]

BASIS_GUARD_FREEZES = [
    BasisGuardFreeze(
        guard_candidate_id="btc15m_q250_firstskip_qty500_yes_only_primary",
        status="PRIMARY_FORWARD_POLICY_FOR_FUTURE_PAPER_COLLECTION",
        family="BTC15M",
        base_candidate="q250_firstskip_qty500",
        side_policy="yes_only",
        wrapper_script=r"scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        evaluation_mode="paper_only_policy",
        guard_name="yes_only",
        guard_type="side_filter",
        min_aligned_distance_usd=None,
        no_side_min_aligned_distance_usd=None,
        min_future_official_rows=100,
        max_contracts=1,
        deployable_guard_now=False,
        freeze_rationale=(
            "Primary next BTC15M path: avoids old q250 NO-side official/proxy flips, "
            "but it needs fresh post-start paper evidence before any promotion discussion."
        ),
    ),
    BasisGuardFreeze(
        guard_candidate_id="btc15m_q1000_yes_no_extra_guard_control",
        status="EXISTING_SPARSE_FORWARD_CONTROL",
        family="BTC15M",
        base_candidate="q1000_yes",
        side_policy="yes_only",
        wrapper_script=r"scripts\btc15m_f2_q1000_yes_shadow.py",
        evaluation_mode="paper_only_existing_control",
        guard_name="no_guard",
        guard_type="control",
        min_aligned_distance_usd=None,
        no_side_min_aligned_distance_usd=None,
        min_future_official_rows=100,
        max_contracts=1,
        deployable_guard_now=False,
        freeze_rationale="Existing q1000 YES-only sparse control; clean but far too few official forward rows.",
    ),
    BasisGuardFreeze(
        guard_candidate_id="btc15m_q1000_yes_aligned_distance_usd15_sidecar",
        status="DIAGNOSTIC_SIDECAR_ONLY_NOT_TRADING_POLICY",
        family="BTC15M",
        base_candidate="q1000_yes",
        side_policy="yes_only",
        wrapper_script="",
        evaluation_mode="sidecar_metric_only",
        guard_name="aligned_distance_usd_ge_15",
        guard_type="distance_veto",
        min_aligned_distance_usd=15.0,
        no_side_min_aligned_distance_usd=None,
        min_future_official_rows=100,
        max_contracts=1,
        deployable_guard_now=False,
        freeze_rationale=(
            "Tiny diagnostic sample looked cleaner with an aligned-distance guard; "
            "track as a sidecar only, not as a trading policy."
        ),
    ),
    BasisGuardFreeze(
        guard_candidate_id="btc15m_q250_firstskip_yes_or_no_distance_usd40_sidecar",
        status="DIAGNOSTIC_SIDECAR_ONLY_NOT_TRADING_POLICY",
        family="BTC15M",
        base_candidate="q250_firstskip_qty500",
        side_policy="yes_or_no_with_no_distance_veto",
        wrapper_script="",
        evaluation_mode="sidecar_metric_only",
        guard_name="yes_or_no_distance_usd_ge_40",
        guard_type="no_side_distance_veto",
        min_aligned_distance_usd=None,
        no_side_min_aligned_distance_usd=40.0,
        min_future_official_rows=100,
        max_contracts=1,
        deployable_guard_now=False,
        freeze_rationale=(
            "Allows YES and only far-from-strike NO decisions; not startable now "
            "because old q250 NO-side evidence is settlement-fragile."
        ),
    ),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC15M next forward candidate preregistration packet.")
    p.add_argument("--feature-table", type=Path, default=DEFAULT_FEATURE_TABLE)
    p.add_argument("--basis-guard-summary", type=Path, default=DEFAULT_BASIS_GUARD_SUMMARY)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def summarize_evidence(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    for freeze in FREEZES:
        group = rows[
            rows["base_candidate"].astype(str).eq(freeze.base_candidate)
            & rows["side"].astype(str).str.lower().eq(freeze.side)
        ].copy()
        if group.empty:
            out.append({**asdict(freeze), "diagnostic_rows": 0})
            continue
        for (source, fidelity), g in group.groupby(["source", "fidelity_label"], dropna=False):
            official = g[g["has_official_result"].astype(str).str.lower().isin(["true", "1", "yes"])]
            both = g[
                g["has_official_result"].astype(str).str.lower().isin(["true", "1", "yes"])
                & g["has_proxy_result"].astype(str).str.lower().isin(["true", "1", "yes"])
            ]
            row = asdict(freeze)
            row.update(
                {
                    "source": source,
                    "fidelity_label": fidelity,
                    "diagnostic_rows": int(len(g)),
                    "official_rows": int(len(official)),
                    "both_result_rows": int(len(both)),
                    "official_pnl_2c": round(float(num(official["official_pnl_2c"]).sum()), 4)
                    if len(official)
                    else 0.0,
                    "proxy_pnl_2c": round(float(num(g["proxy_pnl_2c"]).sum()), 4),
                    "mismatches": int(g["proxy_official_result_mismatch"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()),
                    "adverse_mismatches": int(
                        g["adverse_proxy_official_mismatch"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()
                    ),
                    "proxy_win_official_loss_rows": int(
                        g["proxy_win_official_loss"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()
                    ),
                    "promotion_usable_rows": int(g["promotion_usable"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()),
                    "mean_basis_usd": round(float(num(g["basis_usd"]).mean()), 4) if num(g["basis_usd"]).notna().any() else "",
                    "p95_abs_basis_usd": round(float(num(g["abs_basis_usd"]).quantile(0.95)), 4)
                    if num(g["abs_basis_usd"]).notna().any()
                    else "",
                    "diagnostic_only": True,
                    "can_count_old_rows_for_promotion": False,
                }
            )
            out.append(row)
    return pd.DataFrame(out)


def summarize_basis_guard_evidence(summary: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    required = {"candidate", "guard"}
    has_required = required.issubset(set(summary.columns))

    for freeze in BASIS_GUARD_FREEZES:
        row = asdict(freeze)
        row.update(
            {
                "old_rows_can_count_for_promotion": False,
                "requires_fresh_preregistered_forward_evaluation": True,
            }
        )
        if summary.empty or not has_required:
            row.update(
                {
                    "diagnostic_rows": 0,
                    "diagnostic_missing": True,
                    "diagnostic_guard_blockers": "basis_guard_summary_missing_or_schema_mismatch",
                }
            )
            out.append(row)
            continue

        matched = summary[
            summary["candidate"].astype(str).eq(freeze.base_candidate)
            & summary["guard"].astype(str).eq(freeze.guard_name)
        ]
        if matched.empty:
            row.update(
                {
                    "diagnostic_rows": 0,
                    "diagnostic_missing": True,
                    "diagnostic_guard_blockers": "basis_guard_row_missing",
                }
            )
            out.append(row)
            continue

        src = matched.iloc[0]
        row.update(
            {
                "diagnostic_rows": int(len(matched)),
                "diagnostic_missing": False,
                "input_rows": src.get("input_rows", ""),
                "kept_rows": src.get("kept_rows", ""),
                "kept_trade_rate": src.get("kept_trade_rate", ""),
                "kept_official_pnl_2c": src.get("kept_official_pnl_2c", ""),
                "kept_proxy_pnl_2c": src.get("kept_proxy_pnl_2c", ""),
                "kept_mismatches": src.get("kept_mismatches", ""),
                "kept_adverse_mismatches": src.get("kept_adverse_mismatches", ""),
                "kept_proxy_win_official_loss_rows": src.get("kept_proxy_win_official_loss_rows", ""),
                "vetoed_rows": src.get("vetoed_rows", ""),
                "vetoed_official_pnl_2c": src.get("vetoed_official_pnl_2c", ""),
                "research_promising": src.get("research_promising", ""),
                "diagnostic_guard_blockers": src.get("guard_blockers", ""),
            }
        )
        out.append(row)
    return pd.DataFrame(out)


def report_text(
    freeze_specs: pd.DataFrame,
    evidence: pd.DataFrame,
    guard_specs: pd.DataFrame,
    guard_evidence: pd.DataFrame,
    info: dict[str, Any],
) -> str:
    if "candidate_id" in evidence.columns:
        yes = evidence[evidence["candidate_id"].eq("btc15m_q250_firstskip_qty500_yes_forward")]
        no = evidence[evidence["candidate_id"].eq("btc15m_q250_firstskip_qty500_no_blocked")]
    else:
        yes = pd.DataFrame()
        no = pd.DataFrame()
    lines = [
        "# BTC15M Next Forward Candidate Packet",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        "- No candidate is deployable from this packet.",
        "- The next fresh BTC15M paper-only candidate to prepare is `btc15m_q250_firstskip_qty500_yes_forward`.",
        "- Old live replay and Predexon rows are diagnostic support only; they cannot count as promotion evidence.",
        "- q250 firstskip NO-side expansion is blocked until a separately preregistered settlement-basis guard exists.",
        "- Basis-guard rows in this packet are preregistered diagnostics; none are deployable now.",
        "",
        "## Freeze Specs",
        "",
        freeze_specs.to_string(index=False),
        "",
        "## q250 YES Diagnostic Evidence",
        "",
        yes.to_string(index=False) if not yes.empty else "No q250 YES diagnostic rows found.",
        "",
        "## q250 NO Diagnostic Evidence",
        "",
        no.to_string(index=False) if not no.empty else "No q250 NO diagnostic rows found.",
        "",
        "## Basis Guard Freeze Specs",
        "",
        guard_specs.to_string(index=False),
        "",
        "## Basis Guard Diagnostic Evidence",
        "",
        guard_evidence.to_string(index=False) if not guard_evidence.empty else "No basis guard diagnostic evidence found.",
        "",
        "## Promotion Rules For Future Rows",
        "",
        "- Candidate must be running in paper-only mode before any row is counted.",
        "- Count only rows after explicit preregistration and controlled start/restart.",
        "- Basis guards must be evaluated as preregistered forward sidecars before they can change a trading policy.",
        "- Require official Kalshi REST settlement for every counted row.",
        "- Require complete execution-realism fields: quote age, top visible quantity, quote/signal timestamps, strike, TTL, YES/NO book prices.",
        "- Require one trade per event, fees included, executable side ask, visible top-of-book FOK realism, and no proxy/official result mismatches.",
        "- Minimum future official-settled rows before promotion discussion: `100`.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_table(args.feature_table)
    basis_guard_summary = read_table(args.basis_guard_summary)
    if not rows.empty and "candidate" in rows.columns:
        rows = rows.copy()
        rows["base_candidate"] = rows["candidate"].astype(str)

    freeze_specs = pd.DataFrame([asdict(freeze) for freeze in FREEZES])
    evidence = summarize_evidence(rows)
    guard_specs = pd.DataFrame([asdict(freeze) for freeze in BASIS_GUARD_FREEZES])
    guard_evidence = summarize_basis_guard_evidence(basis_guard_summary)
    freeze_specs.to_csv(args.out_dir / "candidate_freeze_specs.csv", index=False)
    evidence.to_csv(args.out_dir / "candidate_diagnostic_evidence.csv", index=False)
    guard_specs.to_csv(args.out_dir / "basis_guard_freeze_specs.csv", index=False)
    guard_evidence.to_csv(args.out_dir / "basis_guard_diagnostic_evidence.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "feature_table": str(args.feature_table),
        "basis_guard_summary": str(args.basis_guard_summary),
        "candidate_count": len(FREEZES),
        "basis_guard_candidate_count": len(BASIS_GUARD_FREEZES),
        "started_or_restarted_processes": False,
        "deployed_live": False,
        "note": "Preregistration packet only; all old rows are diagnostic-only and cannot count as promotion evidence.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(
        report_text(freeze_specs, evidence, guard_specs, guard_evidence, info),
        encoding="utf-8",
    )

    compact_cols = [
        "candidate_id",
        "status",
        "source",
        "fidelity_label",
        "diagnostic_rows",
        "official_rows",
        "official_pnl_2c",
        "mismatches",
        "adverse_mismatches",
        "promotion_usable_rows",
    ]
    print(evidence[[col for col in compact_cols if col in evidence.columns]].fillna("").to_string(index=False))
    guard_cols = [
        "guard_candidate_id",
        "status",
        "base_candidate",
        "guard_name",
        "kept_rows",
        "kept_official_pnl_2c",
        "kept_mismatches",
        "kept_adverse_mismatches",
        "diagnostic_guard_blockers",
    ]
    print(guard_evidence[[col for col in guard_cols if col in guard_evidence.columns]].fillna("").to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
