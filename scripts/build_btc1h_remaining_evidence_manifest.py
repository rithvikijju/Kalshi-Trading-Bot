#!/usr/bin/env python3
"""Build a BTC1H remaining-evidence manifest.

The BTC1H research stack has promising historical/proxy diagnostics, but the
active candidate still fails official-settlement, execution-realism,
faithful-replay, and clean-clock gates.  This manifest converts those blockers
into explicit evidence requirements so the next collection step is auditable
without relaxing any deployment gate.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_remaining_evidence_manifest_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--objective-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_objective_completion_audit_latest_codex" / "btc1h_objective_summary.csv",
    )
    parser.add_argument(
        "--objective-requirements",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_objective_completion_audit_latest_codex"
        / "btc1h_objective_requirements.csv",
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
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_evidence_clock_gate_latest_codex"
        / "btc1h_clean_evidence_clock_summary.csv",
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
        "--replay-root-cause-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_root_cause_audit_latest_codex"
        / "btc1h_replay_root_cause_summary.csv",
    )
    parser.add_argument(
        "--replay-repair-attempt-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempt_summary.csv",
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
        "--clean-clock-collection-preflight-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_clean_clock_collection_preflight_latest_codex"
        / "btc1h_clean_clock_collection_preflight_summary.csv",
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
        "--candidate-packet-run-info",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_next_forward_candidate_packet_latest_codex" / "run_info.json",
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


def first(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(key, "")).strip() == value for key, value in filters.items()):
            return row
    return {}


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


def status_is_pass(value: Any) -> bool:
    text = str(value).strip().upper()
    return text == "PASS" or text.startswith("PASS_")


def semi_join(parts: list[str]) -> str:
    clean = [str(part).strip() for part in parts if str(part).strip()]
    return ";".join(dict.fromkeys(clean))


def split_semicolon(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def compact_evidence(parts: list[str]) -> str:
    clean = [part.strip(" ;") for part in parts if part and part.strip(" ;")]
    return "; ".join(clean)


def req(objective_requirements: list[dict[str, str]], requirement_id: str) -> dict[str, str]:
    return first(objective_requirements, requirement_id=requirement_id)


def gate(gap_rows: list[dict[str, str]], gate_id: str) -> dict[str, str]:
    return first(gap_rows, gate_id=gate_id)


def path_text(path: Path) -> str:
    return str(path.resolve())


def evidence_row(
    *,
    evidence_id: str,
    gate_category: str,
    current_status: str,
    current_evidence: str,
    current_artifacts: list[Path],
    current_blockers: list[str],
    required_evidence: str,
    minimum_acceptance_criteria: str,
    collection_method: str,
    requires_explicit_authorization: bool,
    requires_process_control: bool,
    can_current_artifacts_satisfy: bool,
    preregistration_required: bool,
    safe_now_action: str,
    next_action: str,
    promotion_relevance: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "variant": VARIANT,
        "gate_category": gate_category,
        "current_status": current_status or "MISSING_EVIDENCE",
        "current_evidence": current_evidence,
        "current_artifacts": semi_join([path_text(path) for path in current_artifacts]),
        "current_blockers": semi_join(current_blockers),
        "required_evidence": required_evidence,
        "minimum_acceptance_criteria": minimum_acceptance_criteria,
        "collection_method": collection_method,
        "requires_explicit_authorization": requires_explicit_authorization,
        "requires_process_control": requires_process_control,
        "can_current_artifacts_satisfy": can_current_artifacts_satisfy,
        "preregistration_required": preregistration_required,
        "safe_now_action": safe_now_action,
        "next_action": next_action,
        "promotion_relevance": promotion_relevance,
    }


def available_data_row(
    *,
    coverage_id: str,
    variant: str,
    available_data_class: str,
    source_status: str,
    rows_or_trades: Any,
    research_countable_rows: Any,
    official_forward_diagnostic_rows: Any,
    near_deployable_countable_rows: Any,
    deployable_countable_rows: Any,
    current_rows_count_for_promotion: bool,
    can_make_near_deployable_now: bool,
    can_make_deployable_now: bool,
    blocker_summary: str,
    source_artifacts: list[Path],
) -> dict[str, Any]:
    return {
        "coverage_id": coverage_id,
        "variant": variant,
        "available_data_class": available_data_class,
        "source_status": source_status,
        "rows_or_trades": rows_or_trades,
        "research_countable_rows": research_countable_rows,
        "official_forward_diagnostic_rows": official_forward_diagnostic_rows,
        "near_deployable_countable_rows": near_deployable_countable_rows,
        "deployable_countable_rows": deployable_countable_rows,
        "current_rows_count_for_promotion": current_rows_count_for_promotion,
        "can_make_near_deployable_now": can_make_near_deployable_now,
        "can_make_deployable_now": can_make_deployable_now,
        "blocker_summary": blocker_summary,
        "blockers": blocker_summary,
        "source_artifacts": semi_join([path_text(path) for path in source_artifacts]),
    }


def build_available_data_rows(
    args: argparse.Namespace,
    holdout_provenance_summary: list[dict[str, str]],
    replay_coverage_summary: list[dict[str, str]],
    data_contract: dict[str, str],
    source_contract: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provenance in holdout_provenance_summary:
        variant = str(provenance.get("variant", "")).strip()
        if not variant:
            continue
        near_rows = to_int(provenance.get("near_deployable_countable_rows", ""))
        deploy_rows = to_int(provenance.get("deployable_countable_rows", ""))
        rows.append(
            available_data_row(
                coverage_id=f"holdout_provenance_{variant}",
                variant=variant,
                available_data_class=provenance.get("current_uses", ""),
                source_status=provenance.get("holdout_evidence_status", ""),
                rows_or_trades=provenance.get("holdout_rows", ""),
                research_countable_rows=provenance.get("research_countable_rows", ""),
                official_forward_diagnostic_rows=provenance.get("official_forward_diagnostic_rows", ""),
                near_deployable_countable_rows=near_rows,
                deployable_countable_rows=deploy_rows,
                current_rows_count_for_promotion=near_rows > 0 or deploy_rows > 0,
                can_make_near_deployable_now=near_rows > 0,
                can_make_deployable_now=deploy_rows > 0,
                blocker_summary=(
                    "historical/proxy and live-WS rows are research-only; official forward rows are "
                    "diagnostic-only unless clean-clock, official-settlement, execution-realism, and replay gates pass"
                ),
                source_artifacts=[args.holdout_provenance_summary],
            )
        )

    for coverage in replay_coverage_summary:
        scope = str(coverage.get("scope", "")).strip() or "unknown"
        rows.append(
            available_data_row(
                coverage_id=f"replay_coverage_{scope}",
                variant=VARIANT,
                available_data_class="current_btc1h_replay_coverage_audit",
                source_status="PASS_REPLAY_COVERAGE" if to_bool(coverage.get("coverage_gate_pass", "")) else "BLOCKED_REPLAY_COVERAGE",
                rows_or_trades=coverage.get("paper_rows", ""),
                research_countable_rows=coverage.get("replayable_rows", ""),
                official_forward_diagnostic_rows=0,
                near_deployable_countable_rows=0,
                deployable_countable_rows=0,
                current_rows_count_for_promotion=False,
                can_make_near_deployable_now=False,
                can_make_deployable_now=False,
                blocker_summary=(
                    "coverage audit is necessary plumbing only; it does not replace official clean-clock rows "
                    "or row-for-row faithful replay"
                ),
                source_artifacts=[args.replay_coverage_summary],
            )
        )

    rows.append(
        available_data_row(
            coverage_id="faithful_replay_current_artifacts",
            variant=VARIANT,
            available_data_class="current_sidecar_and_snapshot_replay_artifacts",
            source_status=data_contract.get("contract_status", ""),
            rows_or_trades=semi_join(
                [
                    f"signal_scan={data_contract.get('sidecar_signal_scan_rows', '')}",
                    f"order_decision={data_contract.get('sidecar_order_decision_rows', '')}",
                    f"ws_orderbook_top={data_contract.get('sidecar_ws_orderbook_top_rows', '')}",
                    f"ws_lifecycle={data_contract.get('sidecar_ws_lifecycle_rows', '')}",
                ]
            ),
            research_countable_rows=0,
            official_forward_diagnostic_rows=0,
            near_deployable_countable_rows=0,
            deployable_countable_rows=0,
            current_rows_count_for_promotion=False,
            can_make_near_deployable_now=False,
            can_make_deployable_now=False,
            blocker_summary=data_contract.get("blockers", "current artifacts cannot support faithful replay"),
            source_artifacts=[args.faithful_replay_data_contract_summary],
        )
    )
    rows.append(
        available_data_row(
            coverage_id="source_contract_future_collection",
            variant=VARIANT,
            available_data_class="checked_out_source_and_materializer_for_future_rows",
            source_status=source_contract.get("source_contract_status", ""),
            rows_or_trades=source_contract.get("source_ready_field_count", ""),
            research_countable_rows=0,
            official_forward_diagnostic_rows=0,
            near_deployable_countable_rows=0,
            deployable_countable_rows=0,
            current_rows_count_for_promotion=False,
            can_make_near_deployable_now=False,
            can_make_deployable_now=False,
            blocker_summary=(
                "source readiness can collect future evidence after authorization, but it is not current "
                "collection evidence"
            ),
            source_artifacts=[args.replay_source_contract_summary],
        )
    )
    return rows


def summarize_available_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    known_classes: list[str] = []
    blocked_classes: list[str] = []
    promotion_classes: list[str] = []
    statuses: list[str] = []
    total_near_rows = 0
    total_deploy_rows = 0
    for row in rows:
        known_classes.extend(split_semicolon(row.get("available_data_class", "")))
        statuses.extend(split_semicolon(row.get("source_status", "")))
        total_near_rows += to_int(row.get("near_deployable_countable_rows", ""))
        total_deploy_rows += to_int(row.get("deployable_countable_rows", ""))
        if to_bool(row.get("current_rows_count_for_promotion", "")):
            promotion_classes.extend(split_semicolon(row.get("available_data_class", "")))
        else:
            blocked_classes.extend(split_semicolon(row.get("available_data_class", "")))
    return {
        "known_available_data_classes": semi_join(known_classes),
        "known_available_data_statuses": semi_join(statuses),
        "promotion_countable_available_data_classes": semi_join(promotion_classes),
        "blocked_available_data_classes": semi_join(blocked_classes),
        "known_available_data_near_deployable_countable_rows": total_near_rows,
        "known_available_data_deployable_countable_rows": total_deploy_rows,
        "known_available_data_can_make_near_deployable": total_near_rows > 0,
        "known_available_data_can_make_deployable": total_deploy_rows > 0,
        "no_known_available_data_class_can_make_near_deployable": total_near_rows == 0,
    }


def build_manifest(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    objective_summary_rows = read_csv(args.objective_summary)
    objective_summary = objective_summary_rows[0] if objective_summary_rows else {}
    objective_requirements = read_csv(args.objective_requirements)
    gap_rows = read_csv(args.promotion_gap_matrix)
    gap_summary_rows = read_csv(args.promotion_gap_summary)
    gap_summary = gap_summary_rows[0] if gap_summary_rows else {}
    clean_rows = read_csv(args.clean_clock_summary)
    clean = clean_rows[0] if clean_rows else {}
    snapshot_rows = read_csv(args.snapshot_execution_summary)
    snapshot = snapshot_rows[0] if snapshot_rows else {}
    execution_filter_rows = read_csv(args.execution_filter_summary)
    execution_filter = execution_filter_rows[0] if execution_filter_rows else {}
    basis_rows = read_csv(args.execution_filter_basis_summary)
    basis = basis_rows[0] if basis_rows else {}
    replay_root_rows = read_csv(args.replay_root_cause_summary)
    replay_root = replay_root_rows[0] if replay_root_rows else {}
    repair_rows = read_csv(args.replay_repair_attempt_summary)
    repair = repair_rows[0] if repair_rows else {}
    data_contract_rows = read_csv(args.faithful_replay_data_contract_summary)
    data_contract = data_contract_rows[0] if data_contract_rows else {}
    source_contract_rows = read_csv(args.replay_source_contract_summary)
    source_contract = source_contract_rows[0] if source_contract_rows else {}
    collection_preflight_rows = read_csv(args.clean_clock_collection_preflight_summary)
    collection_preflight = collection_preflight_rows[0] if collection_preflight_rows else {}
    holdout_provenance_summary = read_csv(args.holdout_provenance_summary)
    replay_coverage_summary = read_csv(args.replay_coverage_summary)
    packet = read_json(args.candidate_packet_run_info)

    official_req = req(objective_requirements, "official_settlement_gate")
    execution_req = req(objective_requirements, "execution_realism_gate")
    replay_req = req(objective_requirements, "faithful_live_replay_gate")
    clock_req = req(objective_requirements, "clean_evidence_clock_gate")
    caveat_req = req(objective_requirements, "statistical_independence_and_basis_caveats")
    verdict_req = req(objective_requirements, "deployment_or_near_deployment_verdict")
    official_agreement = gate(gap_rows, "official_proxy_agreement")
    clean_sample = gate(gap_rows, "clean_forward_sample_size")
    official_pnl = gate(gap_rows, "official_pnl")
    execution_fields = gate(gap_rows, "execution_realism_fields")
    row_for_row = gate(gap_rows, "row_for_row_replay")
    repair_gate = gate(gap_rows, "replay_repair_attempts")
    clean_clock = gate(gap_rows, "clean_evidence_clock")
    independence = gate(gap_rows, "holdout_independence")
    statistics = gate(gap_rows, "statistical_confidence")
    basis_stress = gate(gap_rows, "basis_stress")

    expected_policy_rows = to_int(clean.get("expected_policy_official_rows", ""))
    strict_rows = to_int(execution_filter.get("strict_official_rows", ""))
    strict_mismatches = to_int(basis.get("strict_official_proxy_mismatches", ""))
    objective_complete = to_bool(objective_summary.get("objective_complete", ""))
    deployable_count = to_int(objective_summary.get("deployable_candidates", ""))
    near_deployable_count = to_int(objective_summary.get("near_deployable_candidates", ""))
    clean_clock_can_satisfy = (
        to_bool(clean.get("clean_evidence_clock_ready", ""))
        and expected_policy_rows > 0
        and not str(clean.get("sidecar_signal_missing_fields", "")).strip()
        and not str(clean.get("sidecar_order_decision_missing_fields", "")).strip()
    )
    official_can_satisfy = (
        status_is_pass(official_req.get("status", ""))
        and strict_rows >= args.min_clean_official_rows
        and strict_mismatches == 0
    )
    execution_can_satisfy = (
        status_is_pass(execution_req.get("status", execution_fields.get("status", "")))
        and to_bool(snapshot.get("promotion_usable", ""))
    )
    replay_can_satisfy = (
        status_is_pass(replay_req.get("status", ""))
        and to_int(objective_summary.get("replay_promotion_usable_modes", "")) > 0
        and to_bool(data_contract.get("current_artifacts_can_support_faithful_replay", "True"))
    )
    basis_can_satisfy = strict_mismatches == 0 and status_is_pass(official_req.get("status", ""))
    sample_can_satisfy = status_is_pass(caveat_req.get("status", ""))

    rows = [
        evidence_row(
            evidence_id="clean_evidence_clock_start",
            gate_category="clean_evidence_clock_gate",
            current_status=clock_req.get("status", clean.get("gate_status", "")),
            current_evidence=compact_evidence(
                [
                    clean_clock.get("current_evidence", ""),
                    f"clean clock ready {clean.get('clean_evidence_clock_ready', '')}",
                    f"expected-policy official rows {clean.get('expected_policy_official_rows', '')}",
                    f"blank-policy official rows {clean.get('blank_policy_official_rows', '')}",
                    f"status age minutes {clean.get('status_age_minutes', '')}",
                    f"sidecar signal missing fields {clean.get('sidecar_signal_missing_fields', '')}",
                    f"sidecar order decision missing fields {clean.get('sidecar_order_decision_missing_fields', '')}",
                    f"collection preflight status {collection_preflight.get('preflight_status', '')}",
                    f"ready for authorization {collection_preflight.get('ready_for_authorization', '')}",
                    f"collection evidence ready {collection_preflight.get('collection_evidence_ready', '')}",
                    f"post-restart official rows {collection_preflight.get('post_restart_official_rows', '')}",
                ]
            ),
            current_artifacts=[
                args.clean_clock_summary,
                args.promotion_gap_matrix,
                args.clean_clock_collection_preflight_summary,
            ],
            current_blockers=[
                clock_req.get("blocker", ""),
                clean.get("gate_status", ""),
                "no_expected_policy_official_rows" if expected_policy_rows == 0 else "",
                collection_preflight.get("blockers", ""),
            ],
            required_evidence=(
                "Fresh BTC1H rows written under the exact scan-time policy identity with model TTL, "
                "quote timing, signal scan, and order-decision fields populated."
            ),
            minimum_acceptance_criteria=(
                "clean_evidence_clock_ready True; expected-policy official rows > 0 before collection starts; "
                "no missing required sidecar policy/model-input fields."
            ),
            collection_method=(
                "Run the guarded restart authorization packet and only begin a new BTC1H evidence clock "
                "after explicit user authorization for paper-shadow process control."
            ),
            requires_explicit_authorization=not clean_clock_can_satisfy,
            requires_process_control=not clean_clock_can_satisfy,
            can_current_artifacts_satisfy=clean_clock_can_satisfy,
            preregistration_required=not clean_clock_can_satisfy,
            safe_now_action="Read-only refresh and packet review only; do not restart or migrate processes.",
            next_action=clean.get("next_action", clean_clock.get("next_action", "")),
            promotion_relevance="Old blank-policy rows cannot become clean-clock promotion evidence.",
        ),
        evidence_row(
            evidence_id="official_settled_clean_sample",
            gate_category="official_settlement_gate",
            current_status=official_req.get("status", "MISSING_EVIDENCE"),
            current_evidence=compact_evidence(
                [
                    official_agreement.get("current_evidence", ""),
                    clean_sample.get("current_evidence", ""),
                    official_pnl.get("current_evidence", ""),
                    f"strict execution-filter official rows {execution_filter.get('strict_official_rows', '')}",
                    f"strict execution-filter official PnL {execution_filter.get('strict_official_pnl', '')}",
                    f"strict proxy/official mismatches {basis.get('strict_official_proxy_mismatches', '')}",
                    f"strict mismatch market {basis.get('strict_mismatch_markets', '')}",
                ]
            ),
            current_artifacts=[
                args.objective_requirements,
                args.execution_filter_summary,
                args.execution_filter_basis_summary,
            ],
            current_blockers=[
                official_req.get("blocker", ""),
                official_agreement.get("blocker", ""),
                clean_sample.get("blocker", ""),
                official_pnl.get("blocker", ""),
                basis.get("blockers", ""),
                "strict_sample_below_minimum" if strict_rows < args.min_clean_official_rows else "",
                "strict_mismatch_after_execution_filter" if strict_mismatches else "",
            ],
            required_evidence=(
                "Official REST-settled clean-clock rows with fees and row-level PnL path quality, not proxy labels."
            ),
            minimum_acceptance_criteria=(
                f">={args.min_clean_official_rows} clean official-settled rows; proxy/official mismatch rate "
                f"<={args.max_proxy_official_mismatch_rate}; no unreviewed proxy-win/official-loss flips; "
                "official drawdown sequence inspected."
            ),
            collection_method=(
                "After a clean clock exists, refresh official settlement and execution-filter audits on future rows only."
            ),
            requires_explicit_authorization=not official_can_satisfy,
            requires_process_control=not official_can_satisfy,
            can_current_artifacts_satisfy=official_can_satisfy,
            preregistration_required=not official_can_satisfy,
            safe_now_action="Keep old official rows as diagnostics; do not count them as promotion evidence.",
            next_action=official_req.get("next_action", "Collect clean official-settled rows."),
            promotion_relevance="The current official sample is too small, stale, and still has a strict filtered mismatch.",
        ),
        evidence_row(
            evidence_id="execution_realism_clean_rows",
            gate_category="execution_realism_gate",
            current_status=execution_req.get("status", execution_fields.get("status", "")),
            current_evidence=compact_evidence(
                [
                    execution_fields.get("current_evidence", ""),
                    f"snapshot status {snapshot.get('audit_status', '')}",
                    f"required field complete rate {snapshot.get('required_field_complete_rate', '')}",
                    f"quote-age pass rate {snapshot.get('quote_age_le_limit_rate', '')}",
                    f"stale quote rows {snapshot.get('stale_quote_rows', '')}",
                    f"strict rows after execution filter {execution_filter.get('strict_official_rows', '')}",
                    f"removed markets {execution_filter.get('strict_removed_markets', '')}",
                ]
            ),
            current_artifacts=[
                args.promotion_gap_matrix,
                args.snapshot_execution_summary,
                args.execution_filter_summary,
            ],
            current_blockers=[
                execution_req.get("blocker", ""),
                execution_fields.get("blocker", ""),
                snapshot.get("blockers", ""),
                execution_filter.get("blockers", ""),
            ],
            required_evidence=(
                "Clean-clock ledger/sidecar rows with executable YES/NO ask, fee, quote age, top visible size, "
                "signal timestamp, order timestamp, and FOK/no-fill state."
            ),
            minimum_acceptance_criteria=(
                "All required execution fields present on clean rows; quote-age and spread limits pass; "
                "top visible quantity covers contracts; no stale/no-fill rows counted as fills."
            ),
            collection_method=(
                "Collect future rows from the instrumented BTC1H paper shadow and rerun execution-realism audits."
            ),
            requires_explicit_authorization=not execution_can_satisfy,
            requires_process_control=not execution_can_satisfy,
            can_current_artifacts_satisfy=execution_can_satisfy,
            preregistration_required=not execution_can_satisfy,
            safe_now_action="Use old snapshot execution fields only to define what future rows must contain.",
            next_action=execution_req.get("next_action", execution_fields.get("next_action", "")),
            promotion_relevance="Old-snapshot execution realism can diagnose fill quality but cannot promote the candidate.",
        ),
        evidence_row(
            evidence_id="faithful_row_for_row_replay_parity",
            gate_category="faithful_live_replay_gate",
            current_status=replay_req.get("status", "MISSING_EVIDENCE"),
            current_evidence=compact_evidence(
                [
                    row_for_row.get("current_evidence", ""),
                    repair_gate.get("current_evidence", ""),
                    f"promotion usable modes {objective_summary.get('replay_promotion_usable_modes', '')}",
                    f"modes compared {objective_summary.get('replay_modes_compared', '')}",
                    f"root causes {replay_root.get('root_cause_counts', '')}",
                    f"best current-snapshot repair {repair.get('best_current_snapshot_attempt', '')}",
                    f"repairs promotion usable {repair.get('current_snapshot_repairs_make_replay_promotion_usable', '')}",
                    f"remaining blockers {repair.get('remaining_blockers_after_best_attempt', '')}",
                    f"data contract status {data_contract.get('contract_status', '')}",
                    f"overall missing replay contract fields {data_contract.get('missing_required_field_count', '')}",
                    f"root-cause target missing-field occurrences {data_contract.get('root_cause_target_missing_required_field_occurrence_count', data_contract.get('root_cause_target_missing_required_field_count', ''))}",
                    f"root-cause target unique missing fields {data_contract.get('root_cause_target_unique_missing_required_field_count', '')}",
                    f"current artifacts support faithful replay {data_contract.get('current_artifacts_can_support_faithful_replay', '')}",
                    f"data contract blockers {data_contract.get('blockers', '')}",
                    f"source contract status {source_contract.get('source_contract_status', '')}",
                    f"source contract ready {source_contract.get('current_source_contract_ready', '')}",
                    f"source missing fields {source_contract.get('source_missing_field_count', '')}",
                ]
            ),
            current_artifacts=[
                args.objective_summary,
                args.promotion_gap_matrix,
                args.replay_root_cause_summary,
                args.replay_repair_attempt_summary,
                args.faithful_replay_data_contract_summary,
                args.replay_source_contract_summary,
            ],
            current_blockers=[
                replay_req.get("blocker", ""),
                row_for_row.get("blocker", ""),
                repair_gate.get("blocker", ""),
                repair.get("remaining_blockers_after_best_attempt", ""),
                data_contract.get("blockers", ""),
                "" if to_bool(source_contract.get("current_source_contract_ready", "")) else "source_contract_not_ready",
            ],
            required_evidence=(
                "Independent replay from decision-time model inputs that matches actual ledger rows exactly, "
                "including market, side, skip/fill sequence, entry price, and PnL."
            ),
            minimum_acceptance_criteria=(
                "Replay promotion usable True; no missing actual rows; no extra replay rows; "
                "row count, market selection, entry price, and PnL parity all match."
            ),
            collection_method=(
                "Capture exact model-input/TTL fields in future rows, then rerun replay root-cause and repair audits "
                "without selected-row or post-label scans."
            ),
            requires_explicit_authorization=not replay_can_satisfy,
            requires_process_control=not replay_can_satisfy,
            can_current_artifacts_satisfy=replay_can_satisfy,
            preregistration_required=not replay_can_satisfy,
            safe_now_action="Keep current replay repairs as blocker diagnostics; do not patch replay into promotion evidence.",
            next_action=replay_req.get("next_action", repair.get("next_required_evidence", "")),
            promotion_relevance="Replay parity is currently 0/6 promotion-usable modes, so old rows cannot be trusted for promotion.",
        ),
        evidence_row(
            evidence_id="basis_mismatch_prospective_watch",
            gate_category="official_settlement_gate",
            current_status=basis.get("audit_status", "MISSING_EVIDENCE"),
            current_evidence=compact_evidence(
                [
                    f"strict mismatch rows {basis.get('strict_official_proxy_mismatches', '')}",
                    f"mismatch removed by execution filter {basis.get('mismatch_removed_by_execution_filter', '')}",
                    f"market {basis.get('strict_mismatch_markets', '')}",
                    f"official-minus-proxy spot {basis.get('strict_mismatch_official_minus_proxy_spot', '')}",
                    f"proxy close minus strike {basis.get('strict_mismatch_proxy_close_minus_strike', '')}",
                    f"official expiration minus strike {basis.get('strict_mismatch_official_expiration_minus_strike', '')}",
                    f"minimum proxy-side margin {basis.get('min_proxy_side_abs_margin_usd', '')}",
                ]
            ),
            current_artifacts=[args.execution_filter_basis_summary],
            current_blockers=[
                basis.get("blockers", ""),
                "guard_not_fit_from_current_rows",
                "basis_mismatch_survived_strict_execution_filter" if strict_mismatches else "",
            ],
            required_evidence=(
                "Prospectively collected basis-risk fields and pre-registered evaluation criteria on future official rows."
            ),
            minimum_acceptance_criteria=(
                "Any basis guard or watch condition must be frozen before future rows and validated on clean "
                "official settlement; current near-strike mismatch cannot fit a trading guard."
            ),
            collection_method=(
                "Freeze monitor fields only, collect future clean official rows, and evaluate whether near-strike "
                "basis flips recur without modifying live policy from current labels."
            ),
            requires_explicit_authorization=not basis_can_satisfy,
            requires_process_control=not basis_can_satisfy,
            can_current_artifacts_satisfy=basis_can_satisfy,
            preregistration_required=not basis_can_satisfy,
            safe_now_action="Record the current mismatch as a basis watch condition, not as a deployment guard.",
            next_action=basis.get("next_action", "Track prospectively on clean official rows."),
            promotion_relevance="The strict execution filter isolated, but did not remove, the only proxy/official flip.",
        ),
        evidence_row(
            evidence_id="sample_independence_and_stability",
            gate_category="statistical_independence_and_basis_caveats",
            current_status=caveat_req.get("status", "DIAGNOSTIC_ONLY"),
            current_evidence=compact_evidence(
                [
                    independence.get("current_evidence", ""),
                    statistics.get("current_evidence", ""),
                    basis_stress.get("current_evidence", ""),
                    f"critical blocked requirements {objective_summary.get('critical_blocked_requirements', '')}",
                    f"blocked promotion gates {gap_summary.get('blocked_gate_count', '')}",
                ]
            ),
            current_artifacts=[args.objective_requirements, args.promotion_gap_matrix, args.promotion_gap_summary],
            current_blockers=[
                caveat_req.get("blocker", ""),
                independence.get("blocker", ""),
                statistics.get("blocker", ""),
                basis_stress.get("blocker", ""),
            ],
            required_evidence=(
                "Clean-clock performance that remains positive after unique market/side, event concentration, "
                "basis-stress, and row-level drawdown checks."
            ),
            minimum_acceptance_criteria=(
                "Positive official PnL on enough independent clean rows; no single event/side/basis condition "
                "explains the edge; drawdown remains within frozen limits."
            ),
            collection_method=(
                "After the critical gates pass, rerun independence, statistical confidence, basis stress, and drawdown audits."
            ),
            requires_explicit_authorization=not sample_can_satisfy,
            requires_process_control=not sample_can_satisfy,
            can_current_artifacts_satisfy=sample_can_satisfy,
            preregistration_required=not sample_can_satisfy,
            safe_now_action="Keep historical 14/14 holdouts as research evidence only.",
            next_action=caveat_req.get("next_action", "Collect independent clean forward evidence."),
            promotion_relevance="Historical/proxy holdouts are encouraging but not deployability evidence.",
        ),
        evidence_row(
            evidence_id="deployment_or_near_deployment_verdict",
            gate_category="final_verdict",
            current_status=verdict_req.get("status", "MISSING_EVIDENCE"),
            current_evidence=compact_evidence(
                [
                    f"objective complete {objective_summary.get('objective_complete', '')}",
                    f"deployable candidates {objective_summary.get('deployable_candidates', '')}",
                    f"near-deployable candidates {objective_summary.get('near_deployable_candidates', '')}",
                    f"packet status {objective_summary.get('packet_status', packet.get('packet_status', ''))}",
                    f"packet deployable now {objective_summary.get('packet_deployable_now', packet.get('deployable_now', ''))}",
                ]
            ),
            current_artifacts=[args.objective_summary, args.candidate_packet_run_info],
            current_blockers=[
                objective_summary.get("critical_blocked_requirements", ""),
                "no_deployable_candidates" if deployable_count == 0 else "",
                "no_near_deployable_candidates" if near_deployable_count == 0 else "",
            ],
            required_evidence=(
                "All critical gates passed under the original objective, or a clearly identified candidate that is "
                "deployable/near-deployable without relaxing gates."
            ),
            minimum_acceptance_criteria=(
                "official settlement, execution realism, faithful live replay, clean evidence clock, and sample "
                "quality all pass with current-state evidence."
            ),
            collection_method=(
                "Use this manifest and the objective audit as blockers before any promotion discussion."
            ),
            requires_explicit_authorization=False,
            requires_process_control=False,
            can_current_artifacts_satisfy=objective_complete,
            preregistration_required=False,
            safe_now_action="Do not deploy current BTC1H variants.",
            next_action=verdict_req.get("next_action", "Continue research and clean forward collection."),
            promotion_relevance="Final verdict remains blocked until every critical requirement has direct evidence.",
        ),
    ]

    requirements_total = len(rows)
    current_satisfy_count = sum(1 for row in rows if to_bool(row["can_current_artifacts_satisfy"]))
    auth_count = sum(1 for row in rows if to_bool(row["requires_explicit_authorization"]))
    process_count = sum(1 for row in rows if to_bool(row["requires_process_control"]))
    prereg_count = sum(1 for row in rows if to_bool(row["preregistration_required"]))
    available_data_rows = build_available_data_rows(
        args,
        holdout_provenance_summary,
        replay_coverage_summary,
        data_contract,
        source_contract,
    )
    available_data_summary = summarize_available_data(available_data_rows)
    critical_missing = [
        row["evidence_id"]
        for row in rows
        if row["gate_category"]
        in {
            "official_settlement_gate",
            "execution_realism_gate",
            "faithful_live_replay_gate",
            "clean_evidence_clock_gate",
        }
        and not to_bool(row["can_current_artifacts_satisfy"])
    ]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "manifest_status": (
            "READY_CURRENT_EVIDENCE_SUFFICIENT" if objective_complete else "BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE"
        ),
        "objective_complete": objective_complete,
        "deployable_now": deployable_count > 0,
        "near_deployable_now": near_deployable_count > 0,
        "requirements_total": requirements_total,
        "requirements_current_artifacts_can_satisfy": current_satisfy_count,
        "requirements_current_artifacts_cannot_satisfy": requirements_total - current_satisfy_count,
        "requires_explicit_authorization_count": auth_count,
        "requires_process_control_count": process_count,
        "preregistration_required_count": prereg_count,
        "critical_missing_evidence_count": len(critical_missing),
        "critical_missing_evidence_ids": semi_join(critical_missing),
        "faithful_replay_data_contract_status": data_contract.get("contract_status", ""),
        "faithful_replay_missing_required_field_count": data_contract.get("missing_required_field_count", ""),
        "faithful_replay_missing_required_fields": data_contract.get("missing_required_fields", ""),
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
        "faithful_replay_current_artifacts_can_support": data_contract.get(
            "current_artifacts_can_support_faithful_replay",
            "",
        ),
        "faithful_replay_data_contract_blockers": data_contract.get("blockers", ""),
        "replay_source_contract_status": source_contract.get("source_contract_status", ""),
        "replay_source_contract_ready": source_contract.get("current_source_contract_ready", ""),
        "replay_source_missing_field_count": source_contract.get("source_missing_field_count", ""),
        "clean_clock_collection_preflight_status": collection_preflight.get("preflight_status", ""),
        "clean_clock_collection_preflight_ready_for_authorization": collection_preflight.get(
            "ready_for_authorization",
            "",
        ),
        "clean_clock_collection_preflight_collection_evidence_ready": collection_preflight.get(
            "collection_evidence_ready",
            "",
        ),
        "clean_clock_collection_preflight_process_control_authorized": collection_preflight.get(
            "process_control_authorized",
            "",
        ),
        "clean_clock_collection_preflight_blockers": collection_preflight.get("blockers", ""),
        "current_artifacts_can_make_near_deployable": False,
        **available_data_summary,
        "process_control_authorized": False,
        "no_process_action_taken": True,
        "safe_next_action": (
            "Use this as a read-only blocker manifest; do not start, stop, restart, migrate, deploy, or tune "
            "unless the user explicitly authorizes the guarded process-control path."
        ),
        "next_required_evidence": objective_summary.get(
            "next_required_evidence",
            "clean official-settled rows with execution realism and replay parity",
        ),
    }
    return rows, available_data_rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(
    rows: list[dict[str, Any]],
    available_data_rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    columns = [
        "evidence_id",
        "gate_category",
        "current_status",
        "requires_explicit_authorization",
        "requires_process_control",
        "can_current_artifacts_satisfy",
        "safe_now_action",
    ]
    available_columns = [
        "coverage_id",
        "available_data_class",
        "source_status",
        "current_rows_count_for_promotion",
        "can_make_near_deployable_now",
        "blockers",
    ]
    return "\n".join(
        [
            "# BTC1H Remaining Evidence Manifest",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Manifest status: `{summary['manifest_status']}`",
            f"Deployable now: `{summary['deployable_now']}`",
            f"Near-deployable now: `{summary['near_deployable_now']}`",
            f"Critical missing evidence: `{summary['critical_missing_evidence_ids']}`",
            f"Known available data can make near-deployable now: `{summary['known_available_data_can_make_near_deployable']}`",
            f"Promotion-countable available data classes: `{summary['promotion_countable_available_data_classes'] or 'none'}`",
            f"Faithful replay overall missing fields: `{summary['faithful_replay_missing_required_field_count']}`",
            f"Faithful replay target missing-field occurrences: `{summary['faithful_replay_root_cause_target_missing_required_field_occurrence_count']}`",
            f"Faithful replay target unique missing fields: `{summary['faithful_replay_root_cause_target_unique_missing_required_field_count']}`",
            f"No process action taken: `{summary['no_process_action_taken']}`",
            "",
            "## Evidence Requirements",
            "",
            markdown_table(rows, columns),
            "",
            "## Available Data Coverage",
            "",
            markdown_table(available_data_rows, available_columns),
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
    rows, available_data_rows, summary = build_manifest(args)
    write_csv(args.out_dir / "btc1h_remaining_evidence_requirements.csv", rows)
    write_csv(args.out_dir / "btc1h_available_data_coverage.csv", available_data_rows)
    write_csv(args.out_dir / "btc1h_remaining_evidence_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, available_data_rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
