#!/usr/bin/env python3
"""Audit BTC1H research objective completion from current evidence artifacts.

The objective is broader than one attractive backtest: BTC1H candidates need
multiple holdouts, official settlement, execution realism, and faithful
live-replay parity before they can be called deployable or near-deployable.
This script makes that requirement-level state machine-readable.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_objective_completion_audit_latest_codex"
ACTIVE_VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--multi-holdout-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_multi_holdout_research_latest_codex" / "btc1h_candidate_gate_summary.csv",
    )
    parser.add_argument(
        "--research-priority-matrix",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_research_priority_matrix_latest_codex" / "btc1h_research_priority_matrix.csv",
    )
    parser.add_argument(
        "--research-priority-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_research_priority_matrix_latest_codex" / "btc1h_research_priority_summary.csv",
    )
    parser.add_argument(
        "--promotion-gap-matrix",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_promotion_gap_matrix_latest_codex" / "btc1h_promotion_gap_matrix.csv",
    )
    parser.add_argument(
        "--promotion-gap-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_promotion_gap_matrix_latest_codex" / "btc1h_promotion_gap_summary.csv",
    )
    parser.add_argument(
        "--candidate-packet-run-info",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_next_forward_candidate_packet_latest_codex" / "run_info.json",
    )
    parser.add_argument(
        "--candidate-current-evidence",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_next_forward_candidate_packet_latest_codex"
        / "btc1h_current_evidence_snapshot.csv",
    )
    parser.add_argument(
        "--snapshot-execution-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_snapshot_execution_realism_latest_codex"
        / "btc1h_snapshot_execution_realism_summary.csv",
    )
    parser.add_argument(
        "--execution-filter-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_execution_filter_impact_latest_codex"
        / "btc1h_execution_filter_summary.csv",
    )
    parser.add_argument(
        "--execution-filter-basis-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_execution_filtered_basis_mismatch_latest_codex"
        / "btc1h_execution_filtered_basis_mismatch_summary.csv",
    )
    parser.add_argument(
        "--official-pnl-path-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_official_pnl_path_latest_codex"
        / "btc1h_official_pnl_path_summary.csv",
    )
    parser.add_argument(
        "--holdout-provenance-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_multi_holdout_research_latest_codex"
        / "btc1h_holdout_provenance_summary.csv",
    )
    parser.add_argument(
        "--replay-coverage-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_coverage_audit_latest_codex" / "btc1h_replay_coverage_summary.csv",
    )
    parser.add_argument(
        "--faithful-replay-data-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_faithful_replay_data_contract_latest_codex"
        / "btc1h_faithful_replay_data_contract_summary.csv",
    )
    parser.add_argument(
        "--replay-source-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_source_contract_readiness_latest_codex"
        / "btc1h_replay_source_contract_summary.csv",
    )
    parser.add_argument(
        "--replay-repair-prerequisite-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_prerequisite_audit_latest_codex"
        / "btc1h_replay_repair_prerequisite_summary.csv",
    )
    parser.add_argument(
        "--replay-repair-target-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_target_matrix_latest_codex"
        / "btc1h_replay_repair_target_summary.csv",
    )
    parser.add_argument(
        "--replay-repair-attempt-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempt_summary.csv",
    )
    parser.add_argument("--min-clean-official-rows", type=int, default=50)
    parser.add_argument("--max-proxy-official-mismatch-rate", type=float, default=0.02)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def by_key(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        value = str(row.get(key, "")).strip()
        if value:
            out[value] = row
    return out


def first(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(key, "")).strip() == value for key, value in filters.items()):
            return row
    return {}


def semicolon_join(parts: list[str]) -> str:
    clean = [part for part in parts if part]
    return ";".join(dict.fromkeys(clean))


def split_semicolon(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def gate_evidence(gates: list[dict[str, str]], gate_id: str) -> dict[str, str]:
    return first(gates, gate_id=gate_id)


def status_from_blocked_gates(gates: list[dict[str, str]], gate_ids: list[str]) -> str:
    for gate_id in gate_ids:
        gate = gate_evidence(gates, gate_id)
        if not gate:
            return "MISSING_EVIDENCE"
        if gate.get("status") == "BLOCKED":
            return "BLOCKED"
    return "PASS"


def candidate_status(priority: dict[str, str], multi: dict[str, str]) -> str:
    if to_bool(priority.get("deployable_now", multi.get("deployable_now", ""))):
        return "deployable"
    if to_bool(multi.get("near_deployable_candidate", "")):
        return "near_deployable"
    classification = str(priority.get("classification", ""))
    if to_bool(priority.get("active_forward_control", "")):
        return "promising_research_control_blocked"
    if classification.startswith("top_replay_runner_up"):
        return "promising_research_runner_up_not_independent"
    if classification == "basis_robust_watchlist":
        return "basis_watchlist_not_forward_validated"
    if classification == "low_priority_or_reject":
        return "low_priority_or_rejected"
    if to_bool(multi.get("research_promising", "")):
        return "historical_promising_blocked"
    return "low_priority_or_rejected"


def is_priority_research_candidate(status: str) -> bool:
    return status in {
        "promising_research_control_blocked",
        "promising_research_runner_up_not_independent",
        "basis_watchlist_not_forward_validated",
        "historical_promising_blocked",
    }


def has_blocker(blockers: str, needles: list[str]) -> bool:
    text = blockers.lower()
    return any(needle.lower() in text for needle in needles)


def candidate_promotion_status(
    *,
    deployable_now: bool,
    near_deployable_now: bool,
    promotion_countable_available_data: bool,
) -> str:
    if deployable_now:
        return "DEPLOYABLE"
    if near_deployable_now:
        return "NEAR_DEPLOYABLE"
    if not promotion_countable_available_data:
        return "NO_PROMOTION_COUNTABLE_AVAILABLE_DATA"
    return "BLOCKED_BY_PROMOTION_GATES"


def candidate_research_class(status: str, multi_research_promising: bool) -> str:
    if status == "deployable":
        return "deployable"
    if status == "near_deployable":
        return "near_deployable"
    if status == "promising_research_control_blocked":
        return "active_forward_control_research_only"
    if status == "promising_research_runner_up_not_independent":
        return "causal_replay_runner_up_research_only"
    if status == "basis_watchlist_not_forward_validated":
        return "basis_robust_watchlist_research_only"
    if multi_research_promising or status == "historical_promising_blocked":
        return "multi_holdout_historical_promising_research_only"
    return "low_priority_or_rejected"


def current_available_data_class(available_data_uses: str, promotion_countable_available_data: bool) -> str:
    uses = set(split_semicolon(available_data_uses))
    if promotion_countable_available_data:
        return "promotion_countable_available_data"
    if "official_forward_diagnostic_only" in uses:
        return "research_plus_official_diagnostic_only"
    if uses:
        return "research_only_no_official_forward_evidence"
    return "no_available_data"


def candidate_gate_failures(
    *,
    official_settlement_gate_blocked: bool,
    execution_realism_gate_blocked: bool,
    faithful_replay_gate_blocked: bool,
    clean_evidence_clock_gate_blocked: bool,
    statistical_or_basis_caveat_active: bool,
) -> str:
    failures: list[str] = []
    if official_settlement_gate_blocked:
        failures.append("official_settlement_gate")
    if execution_realism_gate_blocked:
        failures.append("execution_realism_gate")
    if faithful_replay_gate_blocked:
        failures.append("faithful_live_replay_gate")
    if clean_evidence_clock_gate_blocked:
        failures.append("clean_evidence_clock_gate")
    if statistical_or_basis_caveat_active:
        failures.append("statistical_or_basis_caveat")
    return semicolon_join(failures)


def why_not_near_deployable(
    *,
    status: str,
    promotion_countable_available_data: bool,
    official_settlement_gate_blocked: bool,
    execution_realism_gate_blocked: bool,
    faithful_replay_gate_blocked: bool,
    clean_evidence_clock_gate_blocked: bool,
    statistical_or_basis_caveat_active: bool,
) -> str:
    if status == "deployable":
        return "already deployable"
    if status == "near_deployable":
        return "already near-deployable"

    reasons: list[str] = []
    if not promotion_countable_available_data:
        reasons.append("no promotion-countable available data")
    if official_settlement_gate_blocked:
        reasons.append("official settlement/proxy agreement gate blocked")
    if execution_realism_gate_blocked:
        reasons.append("execution realism gate blocked")
    if faithful_replay_gate_blocked:
        reasons.append("faithful row-for-row replay gate blocked")
    if clean_evidence_clock_gate_blocked:
        reasons.append("clean evidence-clock gate blocked")
    if status == "promising_research_runner_up_not_independent":
        reasons.append("runner-up evidence is not independent from the active snapshot")
    if status == "basis_watchlist_not_forward_validated":
        reasons.append("basis watchlist lacks clean forward official validation")
    if status == "low_priority_or_rejected":
        reasons.append("low priority/rejected despite historical positives")
    if statistical_or_basis_caveat_active:
        reasons.append("statistical or basis caveat remains active")
    return semicolon_join(reasons)


def next_evidence_to_reconsider(status: str, recommended_next_action: str) -> str:
    if status == "deployable":
        return "Maintain monitoring; no promotion blocker is active in this audit."
    if status == "near_deployable":
        return "Complete final deployability checks before any live deployment decision."
    if status == "promising_research_control_blocked":
        return (
            "Explicitly authorized clean evidence clock with >=50 clean official-settled rows, "
            "full execution fields, official/proxy agreement, and independent row-for-row replay parity."
        )
    if status == "promising_research_runner_up_not_independent":
        return (
            "Broaden causal replay or wait for clean rows where this policy differs from entry70, "
            "then require the same official/execution/replay gates."
        )
    if status == "basis_watchlist_not_forward_validated":
        return (
            "Treat as watchlist only until fresh causal replay and clean official forward rows show "
            "the extra rows do not damage live-cadence PnL."
        )
    if status == "historical_promising_blocked":
        return (
            "Preregister before future collection; require clean official forward rows plus execution "
            "and replay gates."
        )
    return recommended_next_action or (
        "Deprioritize unless new preregistered evidence changes fixed holdout, live-WS cadence, "
        "and basis-stress results."
    )


def candidate_blocker_evidence_snapshot(row: dict[str, Any]) -> str:
    return (
        f"available_data_status={row.get('available_data_status', '')}; "
        f"current_available_data_class={row.get('current_available_data_class', '')}; "
        f"research_countable_rows={row.get('research_countable_rows', '')}; "
        f"official_forward_diagnostic_rows={row.get('official_forward_diagnostic_rows', '')}; "
        f"near_deployable_countable_rows={row.get('near_deployable_countable_rows', '')}; "
        f"deployable_countable_rows={row.get('deployable_countable_rows', '')}; "
        f"forward_official_rows={row.get('forward_official_rows', '')}; "
        f"forward_official_pnl={row.get('forward_official_pnl', '')}; "
        f"forward_proxy_mismatch_rate={row.get('forward_proxy_mismatch_rate', '')}; "
        f"replay_ledger_promotion_usable={row.get('replay_ledger_promotion_usable', '')}; "
        f"replay_ledger_exact_match_rate={row.get('replay_ledger_exact_match_rate', '')}; "
        f"promotion_gate_failures={row.get('promotion_gate_failures', '')}"
    )


def candidate_blocker_evidence_sources(args: argparse.Namespace, row: dict[str, Any]) -> str:
    sources: list[Path] = [
        args.multi_holdout_summary,
        args.research_priority_matrix,
        args.holdout_provenance_summary,
    ]
    if row.get("official_settlement_gate_blocked"):
        sources.extend(
            [
                args.promotion_gap_matrix,
                args.official_pnl_path_summary,
                args.execution_filter_basis_summary,
            ]
        )
    if row.get("execution_realism_gate_blocked"):
        sources.extend(
            [
                args.snapshot_execution_summary,
                args.execution_filter_summary,
            ]
        )
    if row.get("faithful_replay_gate_blocked"):
        sources.extend(
            [
                args.replay_coverage_summary,
                args.faithful_replay_data_contract_summary,
                args.replay_repair_target_summary,
                args.replay_repair_attempt_summary,
            ]
        )
    if row.get("clean_evidence_clock_gate_blocked"):
        sources.extend(
            [
                args.candidate_packet_run_info,
                args.promotion_gap_matrix,
            ]
        )
    return semicolon_join([str(source) for source in sources])


def build_candidate_rows(
    priority_rows: list[dict[str, str]],
    multi_rows: list[dict[str, str]],
    holdout_provenance_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    multi_by_variant = by_key(multi_rows, "variant")
    provenance_by_variant = by_key(holdout_provenance_rows, "variant")
    rows: list[dict[str, Any]] = []
    for priority in priority_rows:
        variant = str(priority.get("variant", "")).strip()
        if not variant:
            continue
        multi = multi_by_variant.get(variant, {})
        provenance = provenance_by_variant.get(variant, {})
        status = candidate_status(priority, multi)
        multi_research_promising = to_bool(multi.get("research_promising", ""))
        deployable_now = to_bool(priority.get("deployable_now", multi.get("deployable_now", "")))
        near_deployable_now = to_bool(multi.get("near_deployable_candidate", ""))
        near_countable_rows = to_int(provenance.get("near_deployable_countable_rows", ""))
        deploy_countable_rows = to_int(provenance.get("deployable_countable_rows", ""))
        promotion_countable_data = near_countable_rows > 0 or deploy_countable_rows > 0
        blockers = priority.get("deployment_blockers", multi.get("deploy_blockers", ""))
        available_data_uses = provenance.get("current_uses", "")
        official_settlement_gate_blocked = (not promotion_countable_data) or has_blocker(
            blockers,
            [
                "official",
                "forward_official",
                "proxy",
                "settlement",
                "no_forward_official_rows",
                "too_few_forward_official_rows",
            ],
        )
        execution_realism_gate_blocked = (not promotion_countable_data) or has_blocker(
            blockers,
            [
                "execution",
                "full_execution",
                "snapshot_row_parity",
            ],
        )
        faithful_replay_gate_blocked = (not promotion_countable_data) or has_blocker(
            blockers,
            [
                "replay",
                "counterfactual",
                "selected_signal_exact",
                "row_for_row",
            ],
        )
        clean_evidence_clock_gate_blocked = (not promotion_countable_data) or has_blocker(
            blockers,
            [
                "clean_forward",
                "clean_post_restart",
                "controlled_restart",
                "old_rows",
                "historical_only_no_clean_forward_shadow",
                "policy_identity",
            ],
        )
        statistical_or_basis_caveat_active = has_blocker(
            blockers,
            [
                "basis_stress",
                "not_all_fixed",
                "not_all_live_ws",
                "duplicate_market_side",
                "independence",
            ],
        )
        recommended_next_action = priority.get("recommended_next_action", "")
        rows.append(
            {
                "variant": variant,
                "objective_candidate_status": status,
                "candidate_research_class": candidate_research_class(status, multi_research_promising),
                "candidate_is_research_only": not deployable_now and not near_deployable_now,
                "classification": priority.get("classification", ""),
                "research_status": multi.get("research_status", priority.get("research_status", "")),
                "research_promising": multi_research_promising,
                "multi_holdout_research_promising": multi_research_promising,
                "priority_research_candidate": is_priority_research_candidate(status),
                "promotion_readiness_status": multi.get("promotion_readiness_status", ""),
                "deployable_now": deployable_now,
                "near_deployable_candidate": near_deployable_now,
                "active_forward_control": to_bool(priority.get("active_forward_control", "")),
                "available_data_status": provenance.get("holdout_evidence_status", ""),
                "available_data_uses": available_data_uses,
                "current_available_data_class": current_available_data_class(
                    available_data_uses,
                    promotion_countable_data,
                ),
                "research_countable_rows": provenance.get("research_countable_rows", ""),
                "official_forward_diagnostic_rows": provenance.get("official_forward_diagnostic_rows", ""),
                "near_deployable_countable_rows": near_countable_rows,
                "deployable_countable_rows": deploy_countable_rows,
                "promotion_countable_available_data": promotion_countable_data,
                "candidate_promotion_evidence_status": candidate_promotion_status(
                    deployable_now=deployable_now,
                    near_deployable_now=near_deployable_now,
                    promotion_countable_available_data=promotion_countable_data,
                ),
                "historical_positive_holdouts": priority.get(
                    "historical_positive_holdouts",
                    multi.get("all_positive_holdouts", ""),
                ),
                "historical_holdouts": priority.get("historical_holdouts", multi.get("all_holdouts", "")),
                "negative_holdouts": multi.get("negative_holdouts", provenance.get("negative_research_holdouts", "")),
                "historical_all_holdouts_pass": to_bool(priority.get("historical_all_holdouts_pass", ""))
                or (
                    to_float(multi.get("all_holdouts", "")) > 0
                    and to_float(multi.get("all_positive_holdouts", "")) == to_float(multi.get("all_holdouts", ""))
                ),
                "ws_positive_cadences": priority.get("ws_positive_cadences", multi.get("ws_positive_cadences", "")),
                "ws_cadences": priority.get("ws_cadences", multi.get("ws_cadences", "")),
                "ws_all_cadences_pass": to_bool(priority.get("ws_all_cadences_pass", ""))
                or (
                    to_float(multi.get("ws_cadences", "")) > 0
                    and to_float(multi.get("ws_positive_cadences", "")) == to_float(multi.get("ws_cadences", ""))
                ),
                "forward_official_rows": priority.get("forward_official_rows", multi.get("forward_official_rows", "")),
                "forward_official_pnl": priority.get("forward_official_pnl", multi.get("forward_official_pnl", "")),
                "forward_proxy_mismatch_rate": priority.get(
                    "forward_proxy_mismatch_rate",
                    multi.get("forward_official_mismatch_rate", ""),
                ),
                "replay_ledger_promotion_usable": to_bool(
                    priority.get("replay_ledger_promotion_usable", multi.get("replay_ledger_promotion_usable", ""))
                ),
                "replay_ledger_exact_match_rate": priority.get(
                    "replay_ledger_exact_match_rate",
                    multi.get("replay_ledger_exact_match_rate", ""),
                ),
                "official_settlement_gate_blocked": official_settlement_gate_blocked,
                "execution_realism_gate_blocked": execution_realism_gate_blocked,
                "faithful_replay_gate_blocked": faithful_replay_gate_blocked,
                "clean_evidence_clock_gate_blocked": clean_evidence_clock_gate_blocked,
                "statistical_or_basis_caveat_active": statistical_or_basis_caveat_active,
                "promotion_gate_failures": candidate_gate_failures(
                    official_settlement_gate_blocked=official_settlement_gate_blocked,
                    execution_realism_gate_blocked=execution_realism_gate_blocked,
                    faithful_replay_gate_blocked=faithful_replay_gate_blocked,
                    clean_evidence_clock_gate_blocked=clean_evidence_clock_gate_blocked,
                    statistical_or_basis_caveat_active=statistical_or_basis_caveat_active,
                ),
                "why_not_near_deployable": why_not_near_deployable(
                    status=status,
                    promotion_countable_available_data=promotion_countable_data,
                    official_settlement_gate_blocked=official_settlement_gate_blocked,
                    execution_realism_gate_blocked=execution_realism_gate_blocked,
                    faithful_replay_gate_blocked=faithful_replay_gate_blocked,
                    clean_evidence_clock_gate_blocked=clean_evidence_clock_gate_blocked,
                    statistical_or_basis_caveat_active=statistical_or_basis_caveat_active,
                ),
                "next_evidence_to_reconsider": next_evidence_to_reconsider(status, recommended_next_action),
                "deployment_blockers": blockers,
                "near_deployable_disqualifying_blockers": multi.get(
                    "near_deployable_disqualifying_blockers",
                    "",
                ),
                "recommended_next_action": recommended_next_action,
            }
        )
    return rows


def requirement_row(
    requirement_id: str,
    requirement: str,
    status: str,
    evidence: str,
    blocker: str,
    next_action: str,
    artifact: Path,
) -> dict[str, str]:
    return {
        "requirement_id": requirement_id,
        "requirement": requirement,
        "status": status,
        "evidence": evidence,
        "blocker": blocker,
        "next_action": next_action,
        "artifact": str(artifact),
    }


def available_data_summary(
    holdout_provenance_summary: list[dict[str, str]],
    replay_coverage_summary: list[dict[str, str]],
    data_contract: dict[str, str],
    source_contract: dict[str, str],
) -> dict[str, Any]:
    known_classes: list[str] = []
    statuses: list[str] = []
    blocked_classes: list[str] = []
    promotion_classes: list[str] = []
    total_near_rows = 0
    total_deploy_rows = 0

    for row in holdout_provenance_summary:
        classes = split_semicolon(row.get("current_uses", ""))
        near_rows = to_int(row.get("near_deployable_countable_rows", ""))
        deploy_rows = to_int(row.get("deployable_countable_rows", ""))
        known_classes.extend(classes)
        statuses.extend(split_semicolon(row.get("holdout_evidence_status", "")))
        total_near_rows += near_rows
        total_deploy_rows += deploy_rows
        if near_rows > 0 or deploy_rows > 0:
            promotion_classes.extend(classes)
        else:
            blocked_classes.extend(classes)

    for row in replay_coverage_summary:
        class_name = "current_btc1h_replay_coverage_audit"
        known_classes.append(class_name)
        status = "PASS_REPLAY_COVERAGE" if to_bool(row.get("coverage_gate_pass", "")) else "BLOCKED_REPLAY_COVERAGE"
        statuses.append(status)
        blocked_classes.append(class_name)

    if data_contract:
        class_name = "current_sidecar_and_snapshot_replay_artifacts"
        known_classes.append(class_name)
        statuses.extend(split_semicolon(data_contract.get("contract_status", "")))
        blocked_classes.append(class_name)

    if source_contract:
        class_name = "checked_out_source_and_materializer_for_future_rows"
        known_classes.append(class_name)
        statuses.extend(split_semicolon(source_contract.get("source_contract_status", "")))
        blocked_classes.append(class_name)

    return {
        "known_available_data_classes": semicolon_join(known_classes),
        "known_available_data_statuses": semicolon_join(statuses),
        "promotion_countable_available_data_classes": semicolon_join(promotion_classes),
        "blocked_available_data_classes": semicolon_join(blocked_classes),
        "known_available_data_near_deployable_countable_rows": total_near_rows,
        "known_available_data_deployable_countable_rows": total_deploy_rows,
        "known_available_data_can_make_near_deployable": total_near_rows > 0,
        "known_available_data_can_make_deployable": total_deploy_rows > 0,
        "no_known_available_data_class_can_make_near_deployable": total_near_rows == 0,
        "faithful_replay_data_contract_status": data_contract.get("contract_status", ""),
        "faithful_replay_missing_required_field_count": data_contract.get("missing_required_field_count", ""),
        "faithful_replay_missing_required_fields": data_contract.get("missing_required_fields", ""),
        "faithful_replay_current_artifacts_can_support": data_contract.get(
            "current_artifacts_can_support_faithful_replay",
            "",
        ),
        "faithful_replay_root_cause_field_gap_rows": data_contract.get("root_cause_field_gap_rows", ""),
        "faithful_replay_root_cause_future_exact_input_target_count": data_contract.get(
            "root_cause_future_exact_input_target_count",
            "",
        ),
        "faithful_replay_root_cause_target_missing_required_field_count": data_contract.get(
            "root_cause_target_missing_required_field_count",
            "",
        ),
        "faithful_replay_root_cause_target_missing_required_field_occurrence_count": data_contract.get(
            "root_cause_target_missing_required_field_occurrence_count",
            data_contract.get("root_cause_target_missing_required_field_count", ""),
        ),
        "faithful_replay_root_cause_target_unique_missing_required_field_count": data_contract.get(
            "root_cause_target_unique_missing_required_field_count",
            "",
        ),
        "faithful_replay_root_cause_target_missing_required_fields": data_contract.get(
            "root_cause_target_missing_required_fields",
            "",
        ),
        "faithful_replay_root_cause_target_repairs_can_make_promotion_usable_now": data_contract.get(
            "root_cause_target_repairs_can_make_promotion_usable_now",
            "",
        ),
        "replay_source_contract_status": source_contract.get("source_contract_status", ""),
        "replay_source_contract_ready": source_contract.get("current_source_contract_ready", ""),
    }


def replay_repair_blocker_status(
    prerequisite: dict[str, str],
    target: dict[str, str],
    attempt: dict[str, str],
) -> str:
    if not prerequisite and not target and not attempt:
        return "MISSING_EVIDENCE"
    if to_bool(prerequisite.get("current_snapshot_independent_repair_complete", "")) and to_bool(
        attempt.get("current_snapshot_repairs_make_replay_promotion_usable", "")
    ):
        return "CURRENT_SNAPSHOT_REPAIR_COMPLETE_DIAGNOSTIC_ONLY"
    if prerequisite.get("future_exact_input_blocked_targets", ""):
        return "BLOCKED_FUTURE_EXACT_INPUT_REQUIRED"
    return "BLOCKED_CURRENT_SNAPSHOT_REPAIR_INCOMPLETE"


def build_audit(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    multi_rows = read_csv(args.multi_holdout_summary)
    priority_rows = read_csv(args.research_priority_matrix)
    priority_summary_rows = read_csv(args.research_priority_summary)
    priority_summary = priority_summary_rows[0] if priority_summary_rows else {}
    gap_rows = read_csv(args.promotion_gap_matrix)
    gap_summary_rows = read_csv(args.promotion_gap_summary)
    gap_summary = gap_summary_rows[0] if gap_summary_rows else {}
    packet = read_json(args.candidate_packet_run_info)
    evidence_rows = read_csv(args.candidate_current_evidence)
    packet_evidence = evidence_rows[0] if evidence_rows else {}
    restart_authorization_status = str(
        packet.get("restart_authorization_status", packet_evidence.get("restart_authorization_status", ""))
    ).strip()
    restart_authorization_packet_ready = to_bool(
        packet.get(
            "restart_authorization_packet_ready",
            packet_evidence.get("restart_authorization_packet_ready", ""),
        )
    )
    clean_clock_collection_ready_for_authorization = to_bool(
        packet.get(
            "clean_clock_collection_ready_for_authorization",
            packet_evidence.get("clean_clock_collection_ready_for_authorization", ""),
        )
    )
    clean_clock_collection_evidence_ready = to_bool(
        packet.get(
            "clean_clock_collection_evidence_ready",
            packet_evidence.get("clean_clock_collection_evidence_ready", ""),
        )
    )
    clean_clock_collection_process_control_authorized = to_bool(
        packet.get(
            "clean_clock_collection_process_control_authorized",
            packet_evidence.get("clean_clock_collection_process_control_authorized", ""),
        )
    )
    expected_process_state = str(
        packet.get("expected_process_state", packet_evidence.get("expected_process_state", ""))
    ).strip()
    observed_process_action = str(
        packet.get("observed_process_action", packet_evidence.get("observed_process_action", ""))
    ).strip()
    will_restart_process = to_bool(
        packet.get("will_restart_process", packet_evidence.get("will_restart_process", ""))
    )
    will_start_new_process = to_bool(
        packet.get("will_start_new_process", packet_evidence.get("will_start_new_process", ""))
    )
    no_process_action_taken = to_bool(
        packet.get("no_process_action_taken", packet_evidence.get("no_process_action_taken", ""))
    )
    authorization_ready_but_collection_evidence_false = (
        to_bool(
            packet.get(
                "authorization_ready_but_collection_evidence_false",
                packet_evidence.get("authorization_ready_but_collection_evidence_false", ""),
            )
        )
        or (clean_clock_collection_ready_for_authorization and not clean_clock_collection_evidence_ready)
    )
    snapshot_execution_rows = read_csv(args.snapshot_execution_summary)
    snapshot_execution = snapshot_execution_rows[0] if snapshot_execution_rows else {}
    execution_filter_rows = read_csv(args.execution_filter_summary)
    execution_filter = execution_filter_rows[0] if execution_filter_rows else {}
    execution_filter_basis_rows = read_csv(args.execution_filter_basis_summary)
    execution_filter_basis = execution_filter_basis_rows[0] if execution_filter_basis_rows else {}
    official_path_rows = read_csv(args.official_pnl_path_summary)
    official_path = official_path_rows[0] if official_path_rows else {}
    holdout_provenance_rows = read_csv(args.holdout_provenance_summary)
    replay_coverage_rows = read_csv(args.replay_coverage_summary)
    data_contract_rows = read_csv(args.faithful_replay_data_contract_summary)
    data_contract = data_contract_rows[0] if data_contract_rows else {}
    source_contract_rows = read_csv(args.replay_source_contract_summary)
    source_contract = source_contract_rows[0] if source_contract_rows else {}
    replay_repair_prerequisite_rows = read_csv(args.replay_repair_prerequisite_summary)
    replay_repair_prerequisite = replay_repair_prerequisite_rows[0] if replay_repair_prerequisite_rows else {}
    replay_repair_target_rows = read_csv(args.replay_repair_target_summary)
    replay_repair_target = replay_repair_target_rows[0] if replay_repair_target_rows else {}
    replay_repair_attempt_rows = read_csv(args.replay_repair_attempt_summary)
    replay_repair_attempt = replay_repair_attempt_rows[0] if replay_repair_attempt_rows else {}
    data_scope = available_data_summary(
        holdout_provenance_rows,
        replay_coverage_rows,
        data_contract,
        source_contract,
    )
    candidate_rows = build_candidate_rows(priority_rows, multi_rows, holdout_provenance_rows)
    for row in candidate_rows:
        row["promotion_blocker_evidence_snapshot"] = candidate_blocker_evidence_snapshot(row)
        row["promotion_blocker_evidence_sources"] = candidate_blocker_evidence_sources(args, row)

    active = next((row for row in candidate_rows if row["variant"] == ACTIVE_VARIANT), {})
    deployable_count = sum(1 for row in candidate_rows if row["deployable_now"])
    near_deployable_count = sum(1 for row in candidate_rows if row["near_deployable_candidate"])
    multi_holdout_promising_count = sum(1 for row in candidate_rows if row["multi_holdout_research_promising"])
    priority_research_count = sum(1 for row in candidate_rows if row["priority_research_candidate"])
    multi_promising_variants = [
        str(row["variant"]) for row in candidate_rows if row["multi_holdout_research_promising"]
    ]
    priority_research_variants = [str(row["variant"]) for row in candidate_rows if row["priority_research_candidate"]]
    multi_promising_but_low_priority = [
        str(row["variant"])
        for row in candidate_rows
        if row["multi_holdout_research_promising"] and not row["priority_research_candidate"]
    ]
    priority_watchlist_not_multi_promising = [
        str(row["variant"])
        for row in candidate_rows
        if row["priority_research_candidate"] and not row["multi_holdout_research_promising"]
    ]
    candidates_with_holdouts = sum(1 for row in candidate_rows if to_int(row["historical_holdouts"]) > 0)
    active_holdouts = f"{active.get('historical_positive_holdouts', '')}/{active.get('historical_holdouts', '')}"
    active_ws = f"{active.get('ws_positive_cadences', '')}/{active.get('ws_cadences', '')}"

    row_for_row = gate_evidence(gap_rows, "row_for_row_replay")
    repair_attempts = gate_evidence(gap_rows, "replay_repair_attempts")
    official_agreement = gate_evidence(gap_rows, "official_proxy_agreement")
    clean_sample = gate_evidence(gap_rows, "clean_forward_sample_size")
    official_pnl = gate_evidence(gap_rows, "official_pnl")
    execution_fields = gate_evidence(gap_rows, "execution_realism_fields")
    snapshot_execution_evidence = ""
    if snapshot_execution:
        snapshot_execution_evidence = (
            f"; old snapshot diagnostic {snapshot_execution.get('audit_status', '')}; "
            f"field complete {snapshot_execution.get('required_field_complete_rate', '')}; "
            f"quote-age pass {snapshot_execution.get('quote_age_le_limit_rate', '')}; "
            f"stale quote rows {snapshot_execution.get('stale_quote_rows', '')}; "
            f"snapshot blockers {snapshot_execution.get('blockers', '')}"
        )
    execution_filter_evidence = ""
    if execution_filter:
        execution_filter_evidence = (
            f"; strict execution-filter rows {execution_filter.get('strict_official_rows', '')}; "
            f"official PnL {execution_filter.get('strict_official_pnl', '')}; "
            f"mismatches {execution_filter.get('strict_official_proxy_mismatches', '')}; "
            f"removed {execution_filter.get('strict_removed_markets', '')}; "
            f"filter blockers {execution_filter.get('blockers', '')}"
        )
    execution_filter_basis_evidence = ""
    if execution_filter_basis:
        execution_filter_basis_evidence = (
            f"; strict filtered basis mismatch rows "
            f"{execution_filter_basis.get('strict_official_proxy_mismatches', '')}; "
            f"market {execution_filter_basis.get('strict_mismatch_markets', '')}; "
            f"removed by execution filter {execution_filter_basis.get('mismatch_removed_by_execution_filter', '')}; "
            f"official-minus-proxy spot "
            f"{execution_filter_basis.get('strict_mismatch_official_minus_proxy_spot', '')}; "
            f"basis blockers {execution_filter_basis.get('blockers', '')}"
        )
    official_path_evidence = ""
    if official_path:
        official_path_evidence = (
            f"; official PnL path {official_path.get('path_audit_status', official_path.get('path_status', ''))}; "
            f"rows {official_path.get('official_rows', '')}; "
            f"official PnL {official_path.get('official_pnl', '')}; "
            f"max drawdown {official_path.get('max_drawdown', '')}; "
            f"drawdown/PnL {official_path.get('drawdown_to_pnl_ratio', '')}; "
            f"mismatches {official_path.get('proxy_official_mismatches', '')}; "
            f"promotion-countable {official_path.get('current_rows_count_for_promotion', official_path.get('promotion_countable_path', ''))}; "
            f"strict execution path rows {official_path.get('strict_execution_path_rows', '')}; "
            f"strict execution official PnL {official_path.get('strict_execution_path_official_pnl', '')}; "
            f"strict execution max drawdown {official_path.get('strict_execution_path_max_drawdown', '')}; "
            f"strict execution mismatches {official_path.get('strict_execution_path_proxy_official_mismatches', '')}; "
            f"strict execution promotion-countable {official_path.get('strict_execution_path_current_rows_count_for_promotion', '')}; "
            f"path blockers {official_path.get('blockers', '')}"
        )
    official_path_countable = to_bool(
        official_path.get("current_rows_count_for_promotion", official_path.get("promotion_countable_path", ""))
    )
    clean_clock = gate_evidence(gap_rows, "clean_evidence_clock")
    collection_authorization_evidence = ""
    if restart_authorization_status or expected_process_state or observed_process_action:
        collection_authorization_evidence = (
            f"; restart authorization {restart_authorization_status}; "
            f"packet ready {restart_authorization_packet_ready}; "
            f"ready for authorization {clean_clock_collection_ready_for_authorization}; "
            f"collection evidence ready {clean_clock_collection_evidence_ready}; "
            f"expected process state {expected_process_state}; "
            f"observed process action {observed_process_action}; "
            f"will start new {will_start_new_process}; "
            f"will restart {will_restart_process}; "
            f"no process action taken {no_process_action_taken}"
        )
    independence = gate_evidence(gap_rows, "holdout_independence")
    stats = gate_evidence(gap_rows, "statistical_confidence")
    basis_stress = gate_evidence(gap_rows, "basis_stress")
    replay_repair_target_evidence = ""
    if replay_repair_target:
        replay_repair_target_evidence = (
            f"; repair target matrix exact market/side matches "
            f"{replay_repair_target.get('current_exact_market_side_matches', '')}/"
            f"{replay_repair_target.get('current_actual_rows', '')}; "
            f"replay rows {replay_repair_target.get('current_replay_rows', '')}; "
            f"replay-minus-actual PnL {replay_repair_target.get('current_replay_minus_actual_pnl', '')}; "
            f"targets {replay_repair_target.get('next_repair_targets', '')}; "
            f"future rows required {replay_repair_target.get('future_rows_required_items', '')}"
        )
    replay_repair_prerequisite_evidence = ""
    if replay_repair_prerequisite:
        replay_repair_prerequisite_evidence = (
            f"; repair prerequisites targets {replay_repair_prerequisite.get('repair_target_count', '')}; "
            f"snapshot-repairable {replay_repair_prerequisite.get('existing_snapshot_true_target_count', '')}; "
            f"partial {replay_repair_prerequisite.get('existing_snapshot_partial_target_count', '')}; "
            f"not repairable {replay_repair_prerequisite.get('existing_snapshot_false_target_count', '')}; "
            f"can repair {replay_repair_prerequisite.get('existing_snapshot_can_repair_targets', '')}; "
            f"partial targets {replay_repair_prerequisite.get('existing_snapshot_partial_targets', '')}; "
            f"not repairable targets {replay_repair_prerequisite.get('existing_snapshot_false_targets', '')}; "
            f"future exact-input blocked targets "
            f"{replay_repair_prerequisite.get('future_exact_input_blocked_targets', '')}; "
            f"all targets repairable from snapshot "
            f"{replay_repair_prerequisite.get('all_targets_repairable_from_existing_snapshot', '')}; "
            f"current snapshot repair complete "
            f"{replay_repair_prerequisite.get('current_snapshot_independent_repair_complete', '')}"
        )
    replay_repair_attempt_evidence = ""
    if replay_repair_attempt:
        replay_repair_attempt_evidence = (
            f"; repair attempts {replay_repair_attempt.get('attempt_count', '')}; "
            f"best attempt {replay_repair_attempt.get('best_current_snapshot_attempt', '')}; "
            f"current snapshot repairs promotion-usable "
            f"{replay_repair_attempt.get('current_snapshot_repairs_make_replay_promotion_usable', '')}; "
            f"remaining blockers {replay_repair_attempt.get('remaining_blockers_after_best_attempt', '')}; "
            f"diagnostic patch targets {replay_repair_attempt.get('diagnostic_patch_applied_targets', '')}; "
            f"unresolved future-input targets "
            f"{replay_repair_attempt.get('unresolved_future_exact_input_targets', '')}; "
            f"targets repaired to promotion usable "
            f"{replay_repair_attempt.get('targets_repaired_to_promotion_usable_count', '')}"
        )
    faithful_data_contract_evidence = ""
    if data_contract:
        faithful_data_contract_evidence = (
            f"; faithful data contract {data_contract.get('contract_status', '')}; "
            f"overall missing required fields {data_contract.get('missing_required_field_count', '')}; "
            f"root-cause field-gap rows {data_contract.get('root_cause_field_gap_rows', '')}; "
            f"future exact-input targets "
            f"{data_contract.get('root_cause_future_exact_input_target_count', '')}; "
            f"target missing-field occurrences "
            f"{data_contract.get('root_cause_target_missing_required_field_occurrence_count', data_contract.get('root_cause_target_missing_required_field_count', ''))}; "
            f"target unique missing fields "
            f"{data_contract.get('root_cause_target_unique_missing_required_field_count', '')}; "
            f"target missing required field ids "
            f"{data_contract.get('root_cause_target_missing_required_fields', '')}; "
            f"target repairs promotion-usable now "
            f"{data_contract.get('root_cause_target_repairs_can_make_promotion_usable_now', '')}"
        )

    official_settlement_status = status_from_blocked_gates(gap_rows, ["official_proxy_agreement", "clean_forward_sample_size"])
    if not official_path:
        official_settlement_status = "MISSING_EVIDENCE"
    elif not official_path_countable:
        official_settlement_status = "BLOCKED"

    requirement_rows = [
        requirement_row(
            "candidate_universe_and_holdouts",
            "BTC1H candidate universe should be evaluated across fixed historical/proxy and live-WS holdout buckets.",
            "PASS_RESEARCH_EVIDENCE" if candidates_with_holdouts >= 4 else "MISSING_EVIDENCE",
            f"candidates with holdouts {candidates_with_holdouts}; active holdouts {active_holdouts}; active WS cadences {active_ws}",
            "" if candidates_with_holdouts >= 4 else "candidate_holdout_matrix_missing_or_too_small",
            "Keep fixed buckets; do not retune on forward official rows.",
            args.multi_holdout_summary,
        ),
        requirement_row(
            "candidate_ranking_and_identification",
            "Promising, deployable, and near-deployable BTC1H candidates should be identified separately.",
            "PASS_IDENTIFIED",
            (
                f"active {priority_summary.get('active_forward_control', '')}; top replay runner-up "
                f"{priority_summary.get('top_causal_replay_runner_up', '')}; top basis-stress variant "
                f"{priority_summary.get('top_basis_stress_variant', '')}; deployable {deployable_count}; "
                f"near-deployable {near_deployable_count}; priority research candidates {priority_research_count}; "
                f"multi-holdout promising {multi_holdout_promising_count}"
            ),
            "",
            "Treat promising research candidates as blocked until promotion gates pass.",
            args.research_priority_summary,
        ),
        requirement_row(
            "available_data_scope_and_countability",
            "Available BTC1H data classes should be explicitly scoped before judging deployability.",
            (
                "PASS_SCOPED_NO_PROMOTION_USABLE_AVAILABLE_DATA"
                if data_scope["no_known_available_data_class_can_make_near_deployable"]
                else "HAS_PROMOTION_COUNTABLE_AVAILABLE_DATA"
            ),
            (
                f"classes {data_scope['known_available_data_classes']}; "
                f"statuses {data_scope['known_available_data_statuses']}; "
                f"near-deployable-countable rows "
                f"{data_scope['known_available_data_near_deployable_countable_rows']}; "
                f"deployable-countable rows {data_scope['known_available_data_deployable_countable_rows']}; "
                f"promotion-countable classes {data_scope['promotion_countable_available_data_classes'] or 'none'}"
            ),
            "" if data_scope["known_available_data_can_make_near_deployable"] else "no_available_data_class_countable_for_promotion",
            "Use current available data for research diagnostics only; require new clean-clock evidence for promotion.",
            args.holdout_provenance_summary,
        ),
        requirement_row(
            "official_settlement_gate",
            "Official Kalshi settlement must support counted PnL and proxy/official agreement before promotion.",
            official_settlement_status,
            (
                f"{official_agreement.get('current_evidence', '')}; "
                f"{clean_sample.get('current_evidence', '')}; {official_pnl.get('current_evidence', '')}"
                f"{execution_filter_basis_evidence}"
                f"{official_path_evidence}"
            ),
            semicolon_join(
                [
                    official_agreement.get("blocker", ""),
                    clean_sample.get("blocker", ""),
                    official_pnl.get("blocker", ""),
                    execution_filter_basis.get("blockers", ""),
                    "official_pnl_path_missing" if not official_path else "",
                    official_path.get("blockers", "") if official_path and not official_path_countable else "",
                ]
            ),
            "Collect clean official-settled rows and keep row-level path diagnostics prospective.",
            args.promotion_gap_matrix,
        ),
        requirement_row(
            "execution_realism_gate",
            "Rows must include executable quote, fee, visible-size, and FOK/no-fill fields before promotion.",
            execution_fields.get("status", "MISSING_EVIDENCE"),
            execution_fields.get("current_evidence", "") + snapshot_execution_evidence + execution_filter_evidence,
            execution_fields.get("blocker", "execution_realism_gate_missing"),
            execution_fields.get("next_action", "Add complete execution-realism fields to clean rows."),
            args.promotion_gap_matrix,
        ),
        requirement_row(
            "faithful_live_replay_gate",
            "Counterfactual/live replay must be independent and row-for-row faithful before old rows count.",
            status_from_blocked_gates(gap_rows, ["row_for_row_replay", "replay_repair_attempts"]),
            (
                f"{row_for_row.get('current_evidence', '')}; "
                f"{repair_attempts.get('current_evidence', '')}"
                f"{replay_repair_target_evidence}"
                f"{replay_repair_prerequisite_evidence}"
                f"{replay_repair_attempt_evidence}"
                f"{faithful_data_contract_evidence}"
            ),
            semicolon_join(
                [
                    row_for_row.get("blocker", ""),
                    repair_attempts.get("blocker", ""),
                    data_contract.get("blockers", ""),
                    "future_exact_input_required_for:"
                    + replay_repair_prerequisite.get("future_exact_input_blocked_targets", "")
                    if replay_repair_prerequisite.get("future_exact_input_blocked_targets", "")
                    else "",
                    replay_repair_attempt.get("remaining_blockers_after_best_attempt", ""),
                ]
            ),
            "Require exact model-input/TTL capture and independent row-for-row replay parity.",
            args.promotion_gap_matrix,
        ),
        requirement_row(
            "clean_evidence_clock_gate",
            "Forward rows must come from the exact scan-time policy and clean model-policy identity.",
            clean_clock.get("status", "MISSING_EVIDENCE"),
            clean_clock.get("current_evidence", "") + collection_authorization_evidence,
            clean_clock.get("blocker", "clean_evidence_clock_missing"),
            clean_clock.get("next_action", "Start a clean evidence clock only with explicit authorization."),
            args.promotion_gap_matrix,
        ),
        requirement_row(
            "statistical_independence_and_basis_caveats",
            "Historical edge should be caveated by duplicate market/side, event concentration, and basis stress.",
            "DIAGNOSTIC_ONLY",
            (
                f"{independence.get('current_evidence', '')}; {stats.get('current_evidence', '')}; "
                f"{basis_stress.get('current_evidence', '')}"
            ),
            semicolon_join([independence.get("blocker", ""), stats.get("blocker", ""), basis_stress.get("blocker", "")]),
            "Use these as research caveats; do not promote from historical/proxy summaries alone.",
            args.promotion_gap_matrix,
        ),
        requirement_row(
            "deployment_or_near_deployment_verdict",
            "The audit must state whether any BTC1H candidate is deployable or near-deployable without relaxing gates.",
            "PASS_NO_DEPLOYABLE_OR_NEAR_DEPLOYABLE_FOUND"
            if deployable_count == 0 and near_deployable_count == 0
            else "CANDIDATE_FOUND",
            f"deployable {deployable_count}; near-deployable {near_deployable_count}; packet deployable {packet.get('deployable_now', False)}",
            "",
            "Continue research and clean forward collection; do not deploy current BTC1H variants.",
            args.research_priority_matrix,
        ),
    ]

    critical_blockers = [
        row["requirement_id"]
        for row in requirement_rows
        if row["status"] in {"BLOCKED", "MISSING_EVIDENCE"}
        and row["requirement_id"]
        in {
            "official_settlement_gate",
            "execution_realism_gate",
            "faithful_live_replay_gate",
            "clean_evidence_clock_gate",
        }
    ]
    objective_complete = not critical_blockers and bool(candidate_rows)
    current_verdict = (
        "objective_complete_deployability_state_identified"
        if objective_complete
        else "objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate"
    )
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "objective_complete": objective_complete,
        "current_verdict": current_verdict,
        "deployable_candidates": deployable_count,
        "near_deployable_candidates": near_deployable_count,
        "promising_research_candidates": priority_research_count,
        "priority_research_candidates": priority_research_count,
        "multi_holdout_promising_candidates": multi_holdout_promising_count,
        "priority_research_candidate_variants": semicolon_join(priority_research_variants),
        "multi_holdout_promising_variants": semicolon_join(multi_promising_variants),
        "multi_holdout_promising_but_low_priority_variants": semicolon_join(multi_promising_but_low_priority),
        "priority_watchlist_not_multi_holdout_promising_variants": semicolon_join(
            priority_watchlist_not_multi_promising
        ),
        "candidate_count": len(candidate_rows),
        "active_forward_control": priority_summary.get("active_forward_control", ACTIVE_VARIANT),
        "top_causal_replay_runner_up": priority_summary.get("top_causal_replay_runner_up", ""),
        "top_basis_stress_variant": priority_summary.get("top_basis_stress_variant", ""),
        "promotion_gap_blocked_gate_count": gap_summary.get("blocked_gate_count", ""),
        "packet_status": packet.get("packet_status", ""),
        "packet_deployable_now": packet.get("deployable_now", False),
        "restart_authorization_status": restart_authorization_status,
        "restart_authorization_packet_ready": restart_authorization_packet_ready,
        "clean_clock_collection_ready_for_authorization": clean_clock_collection_ready_for_authorization,
        "clean_clock_collection_evidence_ready": clean_clock_collection_evidence_ready,
        "clean_clock_collection_process_control_authorized": clean_clock_collection_process_control_authorized,
        "expected_process_state": expected_process_state,
        "observed_process_action": observed_process_action,
        "will_restart_process": will_restart_process,
        "will_start_new_process": will_start_new_process,
        "no_process_action_taken": no_process_action_taken,
        "authorization_ready_but_collection_evidence_false": authorization_ready_but_collection_evidence_false,
        "replay_promotion_usable_modes": packet.get("replay_promotion_usable_modes", ""),
        "replay_modes_compared": packet.get("replay_modes_compared", ""),
        "replay_repair_current_snapshot_repairs_promotion_usable": packet.get(
            "replay_repair_current_snapshot_repairs_promotion_usable",
            packet_evidence.get("replay_repair_current_snapshot_repairs_promotion_usable", ""),
        ),
        "replay_repair_blocker_status": replay_repair_blocker_status(
            replay_repair_prerequisite,
            replay_repair_target,
            replay_repair_attempt,
        ),
        "replay_repair_target_count": to_int(
            replay_repair_prerequisite.get("repair_target_count", replay_repair_target.get("repair_target_count", ""))
        ),
        "replay_repair_existing_snapshot_true_target_count": to_int(
            replay_repair_prerequisite.get("existing_snapshot_true_target_count", "")
        ),
        "replay_repair_existing_snapshot_partial_target_count": to_int(
            replay_repair_prerequisite.get("existing_snapshot_partial_target_count", "")
        ),
        "replay_repair_existing_snapshot_false_target_count": to_int(
            replay_repair_prerequisite.get("existing_snapshot_false_target_count", "")
        ),
        "replay_repair_existing_snapshot_can_repair_targets": replay_repair_prerequisite.get(
            "existing_snapshot_can_repair_targets",
            "",
        ),
        "replay_repair_existing_snapshot_partial_targets": replay_repair_prerequisite.get(
            "existing_snapshot_partial_targets",
            "",
        ),
        "replay_repair_existing_snapshot_false_targets": replay_repair_prerequisite.get(
            "existing_snapshot_false_targets",
            "",
        ),
        "replay_repair_future_exact_input_blocked_targets": replay_repair_prerequisite.get(
            "future_exact_input_blocked_targets",
            "",
        ),
        "replay_repair_all_targets_repairable_from_existing_snapshot": to_bool(
            replay_repair_prerequisite.get("all_targets_repairable_from_existing_snapshot", "")
        ),
        "replay_repair_current_snapshot_independent_repair_complete": to_bool(
            replay_repair_prerequisite.get("current_snapshot_independent_repair_complete", "")
        ),
        "replay_repair_no_deploy_reason": replay_repair_prerequisite.get("no_deploy_reason", ""),
        "replay_repair_target_matrix_next_targets": replay_repair_target.get("next_repair_targets", ""),
        "replay_repair_target_matrix_future_rows_required_items": replay_repair_target.get(
            "future_rows_required_items",
            "",
        ),
        "replay_repair_current_exact_market_side_matches": to_int(
            replay_repair_target.get("current_exact_market_side_matches", "")
        ),
        "replay_repair_current_actual_rows": to_int(replay_repair_target.get("current_actual_rows", "")),
        "replay_repair_current_replay_rows": to_int(replay_repair_target.get("current_replay_rows", "")),
        "replay_repair_current_replay_minus_actual_pnl": replay_repair_target.get(
            "current_replay_minus_actual_pnl",
            "",
        ),
        "replay_repair_current_independent_row_fidelity_exact": to_bool(
            replay_repair_target.get("current_independent_row_fidelity_exact", "")
        ),
        "replay_repair_current_independent_promotion_usable": to_bool(
            replay_repair_target.get("current_independent_promotion_usable", "")
        ),
        "replay_repair_order_decision_baseline_row_fidelity_exact": to_bool(
            replay_repair_target.get("captured_order_decision_baseline_row_fidelity_exact", "")
        ),
        "replay_repair_attempt_count": to_int(replay_repair_attempt.get("attempt_count", "")),
        "replay_repair_attempt_best_current_snapshot_attempt": replay_repair_attempt.get(
            "best_current_snapshot_attempt",
            "",
        ),
        "replay_repair_attempt_remaining_blockers": replay_repair_attempt.get(
            "remaining_blockers_after_best_attempt",
            "",
        ),
        "replay_repair_attempt_current_snapshot_repairs_promotion_usable": to_bool(
            replay_repair_attempt.get("current_snapshot_repairs_make_replay_promotion_usable", "")
        ),
        "replay_repair_residual_target_count": to_int(replay_repair_attempt.get("residual_target_count", "")),
        "replay_repair_diagnostic_patch_applied_target_count": to_int(
            replay_repair_attempt.get("diagnostic_patch_applied_target_count", "")
        ),
        "replay_repair_unresolved_future_exact_input_target_count": to_int(
            replay_repair_attempt.get("unresolved_future_exact_input_target_count", "")
        ),
        "replay_repair_diagnostic_patch_applied_targets": replay_repair_attempt.get(
            "diagnostic_patch_applied_targets",
            "",
        ),
        "replay_repair_unresolved_future_exact_input_targets": replay_repair_attempt.get(
            "unresolved_future_exact_input_targets",
            "",
        ),
        "replay_repair_targets_repaired_to_promotion_usable_count": to_int(
            replay_repair_attempt.get("targets_repaired_to_promotion_usable_count", "")
        ),
        "replay_repair_all_field_ready_repairs_remain_diagnostic_only": to_bool(
            replay_repair_attempt.get("all_field_ready_repairs_remain_diagnostic_only", "")
        ),
        "snapshot_execution_audit_status": snapshot_execution.get("audit_status", ""),
        "snapshot_execution_required_field_complete_rate": snapshot_execution.get("required_field_complete_rate", ""),
        "snapshot_execution_quote_age_le_limit_rate": snapshot_execution.get("quote_age_le_limit_rate", ""),
        "snapshot_execution_stale_quote_rows": snapshot_execution.get("stale_quote_rows", ""),
        "execution_filter_audit_status": execution_filter.get("audit_status", ""),
        "execution_filter_strict_official_rows": execution_filter.get("strict_official_rows", ""),
        "execution_filter_strict_official_pnl": execution_filter.get("strict_official_pnl", ""),
        "execution_filter_strict_removed_markets": execution_filter.get("strict_removed_markets", ""),
        "execution_filter_strict_official_proxy_mismatches": execution_filter.get(
            "strict_official_proxy_mismatches",
            "",
        ),
        "execution_filtered_basis_mismatch_audit_status": execution_filter_basis.get("audit_status", ""),
        "execution_filtered_basis_mismatch_rows": execution_filter_basis.get(
            "strict_official_proxy_mismatches",
            "",
        ),
        "execution_filtered_basis_mismatch_market": execution_filter_basis.get("strict_mismatch_markets", ""),
        "execution_filtered_basis_mismatch_removed_by_execution_filter": execution_filter_basis.get(
            "mismatch_removed_by_execution_filter",
            "",
        ),
        "execution_filtered_basis_mismatch_official_minus_proxy_spot": execution_filter_basis.get(
            "strict_mismatch_official_minus_proxy_spot",
            "",
        ),
        "execution_filtered_basis_mismatch_proxy_close_minus_strike": execution_filter_basis.get(
            "strict_mismatch_proxy_close_minus_strike",
            "",
        ),
        "execution_filtered_basis_mismatch_official_expiration_minus_strike": execution_filter_basis.get(
            "strict_mismatch_official_expiration_minus_strike",
            "",
        ),
        "execution_filtered_basis_mismatch_blockers": execution_filter_basis.get("blockers", ""),
        "official_pnl_path_audit_status": official_path.get(
            "path_audit_status",
            official_path.get("path_status", ""),
        ),
        "official_pnl_path_rows": official_path.get("official_rows", ""),
        "official_pnl_path_official_pnl": official_path.get("official_pnl", ""),
        "official_pnl_path_proxy_pnl": official_path.get("proxy_pnl", ""),
        "official_pnl_path_official_minus_proxy_pnl": official_path.get("official_minus_proxy_pnl", ""),
        "official_pnl_path_max_drawdown": official_path.get("max_drawdown", ""),
        "official_pnl_path_drawdown_to_pnl_ratio": official_path.get("drawdown_to_pnl_ratio", ""),
        "official_pnl_path_proxy_official_mismatches": official_path.get("proxy_official_mismatches", ""),
        "official_pnl_path_quote_age_gt_90ms_rows": official_path.get("quote_age_gt_90ms_rows", ""),
        "official_pnl_path_current_rows_count_for_promotion": official_path.get(
            "current_rows_count_for_promotion",
            official_path.get("promotion_countable_path", ""),
        ),
        "official_pnl_path_strict_execution_rows": official_path.get("strict_execution_path_rows", ""),
        "official_pnl_path_strict_execution_official_pnl": official_path.get(
            "strict_execution_path_official_pnl",
            "",
        ),
        "official_pnl_path_strict_execution_proxy_pnl": official_path.get("strict_execution_path_proxy_pnl", ""),
        "official_pnl_path_strict_execution_official_minus_proxy_pnl": official_path.get(
            "strict_execution_path_official_minus_proxy_pnl",
            "",
        ),
        "official_pnl_path_strict_execution_max_drawdown": official_path.get(
            "strict_execution_path_max_drawdown",
            "",
        ),
        "official_pnl_path_strict_execution_proxy_official_mismatches": official_path.get(
            "strict_execution_path_proxy_official_mismatches",
            "",
        ),
        "official_pnl_path_strict_execution_current_rows_count_for_promotion": official_path.get(
            "strict_execution_path_current_rows_count_for_promotion",
            "",
        ),
        "official_pnl_path_strict_execution_removed_markets": official_path.get(
            "strict_execution_path_removed_markets",
            "",
        ),
        "official_pnl_path_blockers": official_path.get("blockers", ""),
        **data_scope,
        "critical_blocked_requirements": semicolon_join(critical_blockers),
        "next_required_evidence": (
            "explicitly authorized clean evidence clock; >=50 clean official-settled rows; "
            "complete execution-realism fields; official/proxy agreement; independent row-for-row replay parity"
        ),
    }
    return candidate_rows, requirement_rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(
    candidate_rows: list[dict[str, Any]],
    requirement_rows: list[dict[str, str]],
    summary: dict[str, Any],
) -> str:
    candidate_cols = [
        "variant",
        "objective_candidate_status",
        "candidate_research_class",
        "current_available_data_class",
        "historical_positive_holdouts",
        "historical_holdouts",
        "negative_holdouts",
        "ws_positive_cadences",
        "ws_cadences",
        "forward_official_rows",
        "forward_official_pnl",
        "replay_ledger_promotion_usable",
        "promotion_readiness_status",
        "available_data_status",
        "promotion_countable_available_data",
        "candidate_promotion_evidence_status",
        "promotion_gate_failures",
        "why_not_near_deployable",
        "promotion_blocker_evidence_snapshot",
        "promotion_blocker_evidence_sources",
        "next_evidence_to_reconsider",
    ]
    requirement_cols = ["requirement_id", "status", "blocker", "next_action"]
    return "\n".join(
        [
            "# BTC1H Objective Completion Audit",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Objective complete: `{summary['objective_complete']}`",
            f"Current verdict: `{summary['current_verdict']}`",
            f"Deployable candidates: `{summary['deployable_candidates']}`",
            f"Near-deployable candidates: `{summary['near_deployable_candidates']}`",
            f"Known available data can make near-deployable: `{summary['known_available_data_can_make_near_deployable']}`",
            f"Promotion-countable available data classes: `{summary['promotion_countable_available_data_classes'] or 'none'}`",
            f"Restart authorization status: `{summary['restart_authorization_status']}`",
            f"Expected process state / observed action: `{summary['expected_process_state']}` / `{summary['observed_process_action']}`",
            f"Will start new / restart after authorization: `{summary['will_start_new_process']}` / `{summary['will_restart_process']}`",
            f"Authorization-ready but collection evidence false: `{summary['authorization_ready_but_collection_evidence_false']}`",
            f"Faithful replay overall missing fields: `{summary['faithful_replay_missing_required_field_count']}`",
            f"Faithful replay target missing-field occurrences: `{summary['faithful_replay_root_cause_target_missing_required_field_occurrence_count']}`",
            f"Faithful replay target unique missing fields: `{summary['faithful_replay_root_cause_target_unique_missing_required_field_count']}`",
            f"Replay repair blocker status: `{summary['replay_repair_blocker_status']}`",
            f"Replay repair targets: `{summary['replay_repair_target_count']}`",
            f"Critical blockers: `{summary['critical_blocked_requirements']}`",
            "",
            "## Candidate Status",
            "",
            markdown_table(candidate_rows, candidate_cols),
            "",
            "## Requirement Audit",
            "",
            markdown_table(requirement_rows, requirement_cols),
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(summary, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    candidate_rows, requirement_rows, summary = build_audit(args)
    write_csv(args.out_dir / "btc1h_candidate_objective_status.csv", candidate_rows)
    write_csv(args.out_dir / "btc1h_objective_requirements.csv", requirement_rows)
    write_csv(args.out_dir / "btc1h_objective_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(candidate_rows, requirement_rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
