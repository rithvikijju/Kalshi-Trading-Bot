#!/usr/bin/env python3
"""Build a compact BTC1H promotion gap matrix.

This script summarizes why the best current BTC1H candidate is promising but
not deployable.  It consumes existing readiness, multi-holdout, clean-clock,
basis, distance-guard, and side/entry artifacts.  It does not start processes,
change policies, or search thresholds.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_promotion_gap_matrix_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--readiness-summary",
        type=Path,
        default=BACKTEST_ROOT / "deployment_readiness_latest_codex" / "readiness_summary.csv",
    )
    parser.add_argument(
        "--multi-holdout-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_multi_holdout_research_latest_codex" / "btc1h_candidate_gate_summary.csv",
    )
    parser.add_argument(
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_clean_evidence_clock_gate_latest_codex" / "btc1h_clean_evidence_clock_summary.csv",
    )
    parser.add_argument(
        "--basis-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_official_basis_mismatch_audit_latest_codex" / "btc1h_basis_mismatch_summary.csv",
    )
    parser.add_argument(
        "--basis-stress-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_basis_stress_audit_latest_codex" / "btc1h_basis_stress_summary.csv",
    )
    parser.add_argument(
        "--variant-basis-stress-ranking",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_variant_basis_stress_ranking_latest_codex"
        / "btc1h_variant_basis_stress_ranking.csv",
    )
    parser.add_argument(
        "--distance-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_decision_distance_guard_audit_latest_codex" / "btc1h_decision_distance_guard_summary.csv",
    )
    parser.add_argument(
        "--side-entry-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_side_entry_profile_audit_latest_codex" / "btc1h_side_entry_profile_summary.csv",
    )
    parser.add_argument(
        "--holdout-independence-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_holdout_independence_audit_latest_codex"
        / "btc1h_holdout_independence_summary.csv",
    )
    parser.add_argument(
        "--statistical-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_statistical_confidence_audit_latest_codex"
        / "btc1h_statistical_confidence_summary.csv",
    )
    parser.add_argument(
        "--replay-root-cause-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_root_cause_summary.csv",
    )
    parser.add_argument(
        "--replay-mode-comparison",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_mode_comparison.csv",
    )
    parser.add_argument(
        "--replay-repair-attempt-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempt_summary.csv",
    )
    parser.add_argument(
        "--replay-repair-attempts",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_attempt_audit_latest_codex"
        / "btc1h_replay_repair_attempts.csv",
    )
    parser.add_argument(
        "--next-forward-run-info",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_next_forward_candidate_packet_latest_codex" / "run_info.json",
    )
    parser.add_argument("--min-clean-official-rows", type=int, default=50)
    parser.add_argument("--max-mismatch-rate", type=float, default=0.02)
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


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def status_rank(status: str) -> int:
    order = {
        "PASS_RESEARCH_ONLY": 0,
        "PASS_OPERATIONAL": 1,
        "DIAGNOSTIC_ONLY": 2,
        "BLOCKED": 3,
        "REJECTED_DIAGNOSTIC": 4,
    }
    return order.get(status, 9)


def gate(
    gate_id: str,
    requirement: str,
    status: str,
    current_evidence: str,
    blocker: str,
    next_action: str,
    artifact: str,
) -> dict[str, str]:
    return {
        "gate_id": gate_id,
        "requirement": requirement,
        "status": status,
        "current_evidence": current_evidence,
        "blocker": blocker,
        "next_action": next_action,
        "artifact": artifact,
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    headers = list(rows[0].keys())
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(header, "")).replace("\n", " ") for header in headers) + " |")
    return "\n".join(out)


def build_gap_matrix(args: argparse.Namespace) -> tuple[list[dict[str, str]], dict[str, Any]]:
    readiness_rows = read_csv(args.readiness_summary)
    multi_rows = read_csv(args.multi_holdout_summary)
    clean_rows = read_csv(args.clean_clock_summary)
    basis_rows = read_csv(args.basis_summary)
    basis_stress_rows = read_csv(args.basis_stress_summary)
    variant_basis_rows = read_csv(args.variant_basis_stress_ranking)
    distance_rows = read_csv(args.distance_summary)
    side_rows = read_csv(args.side_entry_summary)
    independence_rows = read_csv(args.holdout_independence_summary)
    stat_rows = read_csv(args.statistical_summary)
    replay_root_cause_rows = read_csv(args.replay_root_cause_summary)
    replay_mode_rows = read_csv(args.replay_mode_comparison)
    replay_attempt_summary_rows = read_csv(args.replay_repair_attempt_summary)
    replay_attempt_rows = read_csv(args.replay_repair_attempts)
    packet = read_json(args.next_forward_run_info)

    readiness = first(readiness_rows, family="BTC1H", candidate=VARIANT, source="btc1h_multi_holdout_research")
    if not readiness:
        readiness = first(readiness_rows, family="BTC1H", candidate=VARIANT)
    multi = first(multi_rows, variant=VARIANT)
    clean = first(clean_rows, variant=VARIANT) or (clean_rows[0] if clean_rows else {})
    basis = first(basis_rows, variant=VARIANT) or (basis_rows[0] if basis_rows else {})
    basis_stress_50 = first(basis_stress_rows, basis_shock_usd="50.0")
    basis_stress_p95 = first(basis_stress_rows, basis_shock_usd="78.915")
    if not basis_stress_p95 and basis_stress_rows:
        basis_stress_p95 = basis_stress_rows[-1]
    variant_basis_top = variant_basis_rows[0] if variant_basis_rows else {}
    variant_basis_active = first(variant_basis_rows, variant=VARIANT)
    distance_50 = first(distance_rows, threshold_usd="50.0")
    side_all = first(side_rows, profile="all_active")
    side_yes = first(side_rows, profile="yes_only")
    side_entry_60_70 = first(side_rows, profile="entry_60_70")
    independence_hist = first(independence_rows, panel="historical_all_rows")
    independence_unique = first(independence_rows, panel="historical_unique_market_side_rows")
    stat_naive = first(stat_rows, panel="historical_naive_all_rows")
    stat_unique = first(stat_rows, panel="historical_unique_market_side")
    stat_forward = first(stat_rows, panel="forward_official_stale_rows")
    replay_root_cause = replay_root_cause_rows[0] if replay_root_cause_rows else {}
    replay_mode_count = len(replay_mode_rows)
    replay_mode_promotion_usable_count = sum(1 for row in replay_mode_rows if to_bool(row.get("promotion_usable_replay", "")))
    replay_attempt = replay_attempt_summary_rows[0] if replay_attempt_summary_rows else {}
    replay_attempt_count = len(replay_attempt_rows)
    replay_attempt_repairs_usable = to_bool(
        replay_attempt.get("current_snapshot_repairs_make_replay_promotion_usable", "")
    )
    replay_attempt_blocker = (
        "current_snapshot_repairs_not_promotion_usable"
        if not replay_attempt_repairs_usable
        else ""
    )
    if str(replay_attempt.get("strict_selected_scan_attempt_verdict", "")).strip() == "REGRESSES_ROW_FIDELITY":
        replay_attempt_blocker = (
            replay_attempt_blocker + ";selected_scan_attempt_regresses_row_fidelity"
            if replay_attempt_blocker
            else "selected_scan_attempt_regresses_row_fidelity"
        )

    gates: list[dict[str, str]] = []
    hist_pos = to_float(multi.get("all_positive_holdouts", readiness.get("btc1h_all_positive_holdouts", "")))
    hist_all = to_float(multi.get("all_holdouts", readiness.get("btc1h_all_holdouts", "")))
    ws_pos = to_float(multi.get("ws_positive_cadences", readiness.get("btc1h_ws_positive_cadences", "")))
    ws_all = to_float(multi.get("ws_cadences", readiness.get("btc1h_ws_cadences", "")))
    gates.append(
        gate(
            "historical_multi_holdout",
            "Fixed BTC1H candidate should be positive across historical/proxy and live-WS holdout buckets.",
            "PASS_RESEARCH_ONLY" if hist_all and hist_pos == hist_all and ws_all and ws_pos == ws_all else "BLOCKED",
            f"holdouts {hist_pos:g}/{hist_all:g}; WS cadences {ws_pos:g}/{ws_all:g}; historical PnL {multi.get('historical_pnl_sum', '')}",
            "" if hist_all and hist_pos == hist_all and ws_all and ws_pos == ws_all else "not_all_holdouts_positive",
            "Keep as research support only; this is not deployment evidence.",
            str(args.multi_holdout_summary),
        )
    )

    gates.append(
        gate(
            "holdout_independence",
            "Multi-holdout evidence should be interpreted after duplicate market/side and concentration checks.",
            "DIAGNOSTIC_ONLY",
            f"pooled rows {independence_hist.get('rows', '')}, duplicate market/side rows {independence_hist.get('duplicate_market_side_rows', '')}; unique rows {independence_unique.get('rows', '')}, unique PnL {independence_unique.get('pnl', '')}; leave-one-event min {independence_unique.get('leave_one_event_min_pnl', '')}",
            independence_hist.get("blockers", "") or "holdout_independence_diagnostic_only",
            "Use de-duplicated/concentration metrics as research caveats; require clean official forward evidence before promotion.",
            str(args.holdout_independence_summary),
        )
    )

    unique_lower = to_float(stat_unique.get("event_cluster_bootstrap_total_pnl_p025", ""))
    unique_null_p = to_float(stat_unique.get("breakeven_null_pvalue", "1"), 1.0)
    forward_null_p = to_float(stat_forward.get("breakeven_null_pvalue", "1"), 1.0)
    gates.append(
        gate(
            "statistical_confidence",
            "Historical edge should remain credible after de-duplication/event clustering; stale official stats cannot promote.",
            "DIAGNOSTIC_ONLY",
            f"naive status {stat_naive.get('status', '')}, p025 {stat_naive.get('event_cluster_bootstrap_total_pnl_p025', '')}; unique status {stat_unique.get('status', '')}, p025 {unique_lower:g}, null p {unique_null_p:g}; stale official null p {forward_null_p:g}",
            "unique_market_side_or_stale_official_stat_weak",
            "Keep as research-only; require clean official forward sample before statistical promotion claims.",
            str(args.statistical_summary),
        )
    )

    official_rows = to_float(basis.get("official_rows", clean.get("official_rows", readiness.get("live_official_trades", ""))))
    post_restart_rows = to_float(readiness.get("post_restart_official_rows", clean.get("post_restart_official_rows", "")))
    gates.append(
        gate(
            "clean_forward_sample_size",
            f"At least {args.min_clean_official_rows} clean post-restart official-settled rows.",
            "BLOCKED" if post_restart_rows < args.min_clean_official_rows else "PASS_OPERATIONAL",
            f"current stale official rows {official_rows:g}; clean post-restart official rows {post_restart_rows:g}",
            "too_few_clean_post_restart_official_rows" if post_restart_rows < args.min_clean_official_rows else "",
            "Explicitly authorized controlled paper-shadow restart, then collect clean official rows.",
            str(args.clean_clock_summary),
        )
    )

    clean_ready = to_bool(clean.get("clean_evidence_clock_ready", readiness.get("btc1h_clean_evidence_clock_ready", "")))
    gates.append(
        gate(
            "clean_evidence_clock",
            "Rows must come from the exact scan-time policy with captured model-input fields.",
            "PASS_OPERATIONAL" if clean_ready else "BLOCKED",
            clean.get("gate_status", readiness.get("btc1h_clean_evidence_clock_status", "")),
            "controlled_restart_required_or_model_inputs_missing" if not clean_ready else "",
            "Start a separate clean evidence clock; do not pool old cached-TTL rows.",
            str(args.clean_clock_summary),
        )
    )

    blank_policy = to_float(readiness.get("shadow_official_policy_blank_rows", clean.get("blank_policy_official_rows", "")))
    expected_policy_rows = to_float(readiness.get("shadow_official_policy_official_rows", ""))
    gates.append(
        gate(
            "policy_identity_fields",
            "Counted official rows must have nonblank expected model policy and TTL policy.",
            "BLOCKED" if blank_policy > 0 or expected_policy_rows == 0 else "PASS_OPERATIONAL",
            f"blank policy rows {blank_policy:g}; expected-policy official rows {expected_policy_rows:g}",
            "old_rows_missing_policy_identity" if blank_policy > 0 or expected_policy_rows == 0 else "",
            "Require nonblank policy fields after controlled restart before counting rows.",
            str(args.readiness_summary),
        )
    )

    official_pnl = to_float(basis.get("official_pnl", readiness.get("live_official_pnl", "")))
    gates.append(
        gate(
            "official_pnl",
            "Official Kalshi-settled PnL after fees must be positive on counted clean rows.",
            "DIAGNOSTIC_ONLY" if official_pnl > 0 and post_restart_rows == 0 else ("PASS_OPERATIONAL" if official_pnl > 0 else "BLOCKED"),
            f"stale official PnL {official_pnl:g}",
            "positive_only_on_stale_rows" if official_pnl > 0 and post_restart_rows == 0 else ("" if official_pnl > 0 else "official_pnl_not_positive"),
            "Re-evaluate on clean post-restart official rows.",
            str(args.basis_summary),
        )
    )

    mismatch_rate = to_float(basis.get("official_proxy_mismatch_rate", readiness.get("btc1h_basis_official_proxy_mismatch_rate", "")))
    flips = to_float(basis.get("proxy_win_official_loss_flips", readiness.get("btc1h_basis_proxy_win_official_loss_flips", "")))
    gates.append(
        gate(
            "official_proxy_agreement",
            f"Proxy/official mismatch rate <= {args.max_mismatch_rate:.2%} and zero proxy-win/official-loss flips.",
            "BLOCKED" if mismatch_rate > args.max_mismatch_rate or flips > 0 else "PASS_OPERATIONAL",
            f"mismatch rate {mismatch_rate:.4f}; proxy-win/official-loss flips {flips:g}",
            "proxy_official_mismatch_or_flip" if mismatch_rate > args.max_mismatch_rate or flips > 0 else "",
            "Collect clean rows and monitor basis; do not fit a guard from stale rows.",
            str(args.basis_summary),
        )
    )

    gates.append(
        gate(
            "basis_stress",
            "Historical/proxy labels should remain research-only under adverse official-basis shocks.",
            "DIAGNOSTIC_ONLY",
            f"$50 stress PnL {basis_stress_50.get('pnl', '')}, flips {basis_stress_50.get('basis_flip_rows', '')}; p95/max stress {basis_stress_p95.get('basis_shock_usd', '')} PnL {basis_stress_p95.get('pnl', '')}, unique PnL {basis_stress_p95.get('unique_market_side_pnl', '')}, negative holdouts {basis_stress_p95.get('negative_holdouts', '')}",
            "proxy_label_basis_stress_fragility",
            "Use as settlement-risk caveat only; require future clean official rows before any basis guard or promotion.",
            str(args.basis_stress_summary),
        )
    )

    gates.append(
        gate(
            "variant_basis_stress_ranking",
            "Frozen BTC1H variants should be compared under the same official-basis stress before changing research priority.",
            "DIAGNOSTIC_ONLY",
            f"top basis-stress variant {variant_basis_top.get('variant', '')} max positive unique shock {variant_basis_top.get('max_basis_shock_with_positive_unique_pnl', '')}; active max positive unique shock {variant_basis_active.get('max_basis_shock_with_positive_unique_pnl', '')}; active p95 unique PnL {variant_basis_active.get('unique_pnl_at_p95_or_max', '')}",
            "runner_up_basis_stress_diagnostic_not_forward_validated",
            "Use for research prioritization only; no variant switch without causal replay and clean forward official rows.",
            str(args.variant_basis_stress_ranking),
        )
    )

    replay_usable = to_bool(multi.get("replay_ledger_promotion_usable", readiness.get("btc1h_replay_ledger_promotion_usable", "")))
    exact_match_rate = to_float(multi.get("replay_ledger_exact_match_rate", readiness.get("btc1h_replay_ledger_exact_match_rate", "")))
    root_cause_counts = replay_root_cause.get("root_cause_counts", "")
    dominant_root_cause = replay_root_cause.get("dominant_root_cause", "")
    replay_evidence = (
        f"promotion usable {replay_usable}; exact match rate {exact_match_rate:.6f}; "
        f"blockers {multi.get('replay_ledger_blockers', readiness.get('btc1h_replay_ledger_blockers', ''))}"
    )
    if root_cause_counts:
        replay_evidence += f"; root causes {root_cause_counts}; dominant {dominant_root_cause}"
    if replay_mode_count:
        replay_evidence += f"; promotion-usable replay modes {replay_mode_promotion_usable_count}/{replay_mode_count}"
    gates.append(
        gate(
            "row_for_row_replay",
            "Counterfactual/live replay must reproduce paper ledger rows exactly by market/side/PnL.",
            "PASS_OPERATIONAL" if replay_usable else "BLOCKED",
            replay_evidence,
            "replay_not_row_faithful" if not replay_usable else "",
            "Model order-decision reprice/fill sequencing, same-event market selection, blocked-dedupe clocking, and clean captured model inputs before promotion.",
            str(args.replay_root_cause_summary if root_cause_counts else args.multi_holdout_summary),
        )
    )
    gates.append(
        gate(
            "replay_repair_attempts",
            "Current snapshot replay repairs must make independent replay promotion-usable before they can close the replay gate.",
            "PASS_OPERATIONAL" if replay_attempt_repairs_usable else "BLOCKED",
            (
                f"attempts {replay_attempt_count}; best {replay_attempt.get('best_current_snapshot_attempt', '')}; "
                f"matched drift patch rows {replay_attempt.get('matched_drift_patch_rows', '')}; "
                f"strict selected-scan verdict {replay_attempt.get('strict_selected_scan_attempt_verdict', '')}; "
                f"remaining blockers {replay_attempt.get('remaining_blockers_after_best_attempt', '')}"
            ),
            replay_attempt_blocker,
            (
                "Do not treat matched-drift patch simulations or selected-scan-only replay as promotion evidence; "
                "require exact model-input/TTL capture and row-for-row independent replay parity."
            ),
            str(args.replay_repair_attempt_summary),
        )
    )

    field_complete = to_float(readiness.get("shadow_ledger_execution_field_complete_rate", "0"))
    gates.append(
        gate(
            "execution_realism_fields",
            "Ledger/replay rows must include side ask, fee, quote age, visible size, and timestamp fields.",
            "PASS_OPERATIONAL" if field_complete >= 1.0 else "BLOCKED",
            f"field complete rate {field_complete:g}; missing {readiness.get('shadow_ledger_execution_missing_fields', '')}",
            "execution_realism_fields_missing" if field_complete < 1.0 else "",
            "Use post-restart schema/sidecar fields; require complete rows.",
            str(args.readiness_summary),
        )
    )

    frozen_policy_pass = to_bool(readiness.get("frozen_policy_parity_pass", ""))
    gates.append(
        gate(
            "frozen_policy_parity",
            "Running wrapper/source must match the frozen active candidate definition.",
            "PASS_OPERATIONAL" if frozen_policy_pass else "BLOCKED",
            readiness.get("frozen_policy_parity_status", ""),
            "" if frozen_policy_pass else "frozen_policy_parity_failed",
            "Keep frozen policy unchanged through the next clean evidence clock.",
            str(args.readiness_summary),
        )
    )

    process_count = to_float(readiness.get("shadow_process_count", "0"))
    duplicates = to_float(readiness.get("shadow_duplicate_process_count", "0"))
    source_stale = str(readiness.get("shadow_source_freshness_status", "")).strip()
    gates.append(
        gate(
            "process_hygiene_and_source_freshness",
            "Exactly one target process, no duplicates, and source/current process alignment for countable rows.",
            "BLOCKED" if source_stale == "RUNNING_SOURCE_STALE_RESTART_REQUIRED" or process_count != 1 or duplicates else "PASS_OPERATIONAL",
            f"processes {process_count:g}; duplicates {duplicates:g}; source freshness {source_stale}",
            "running_process_predates_latest_source" if source_stale == "RUNNING_SOURCE_STALE_RESTART_REQUIRED" else "",
            "Do not count old rows; restart only with explicit authorization.",
            str(args.readiness_summary),
        )
    )

    gates.append(
        gate(
            "distance_guard_candidate",
            "A decision-time distance guard should not break fixed holdout/WS robustness.",
            "REJECTED_DIAGNOSTIC",
            f"$50 guard: holdouts {distance_50.get('positive_historical_holdouts', '')}/{distance_50.get('historical_holdouts', '')}; WS {distance_50.get('ws_positive_cadences', '')}/{distance_50.get('ws_cadences', '')}; current mismatches {distance_50.get('forward_official_proxy_mismatches_current_diagnostic', '')}",
            "fixes_current_flip_but_breaks_ws_cadences",
            "Do not add a distance guard to the frozen next-forward policy.",
            str(args.distance_summary),
        )
    )

    gates.append(
        gate(
            "side_entry_retune_candidates",
            "Side-only or entry-band retunes should improve robustness and not retain official-settlement blockers.",
            "REJECTED_DIAGNOSTIC",
            f"all_active PnL {side_all.get('historical_pnl_stressed', '')}; yes_only holdouts {side_yes.get('positive_historical_holdouts', '')}/{side_yes.get('historical_holdouts', '')}, WS {side_yes.get('ws_positive_cadences', '')}/{side_yes.get('ws_cadences', '')}; entry_60_70 current official PnL {side_entry_60_70.get('forward_official_pnl_current_diagnostic', '')}, mismatches {side_entry_60_70.get('forward_official_proxy_mismatches_current_diagnostic', '')}",
            "no_side_or_entry_slice_improves_deployability",
            "Keep active frozen policy; no side/entry retune for next evidence clock.",
            str(args.side_entry_summary),
        )
    )

    production_ready = to_bool(readiness.get("production_ready", ""))
    gates.append(
        gate(
            "overall_deployment_readiness",
            "All promotion gates must pass before deployment.",
            "PASS_OPERATIONAL" if production_ready else "BLOCKED",
            f"production_ready={production_ready}; packet={packet.get('packet_status', '')}; deployable_now={packet.get('deployable_now', False)}",
            "production_ready_false" if not production_ready else "",
            "Continue research/clean forward collection; no live deployment.",
            str(args.readiness_summary),
        )
    )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "deployable_now": False,
        "near_deployable_research_candidate": to_bool(multi.get("near_deployable_candidate", readiness.get("btc1h_near_deployable_candidate", ""))),
        "recommended_policy_for_next_clean_clock": VARIANT,
        "recommended_policy_change": "none",
        "recommended_next_action": "explicitly_authorized_controlled_restart_then_collect_50_clean_official_rows",
        "blocked_gate_count": sum(1 for row in gates if row["status"] == "BLOCKED"),
        "rejected_diagnostic_count": sum(1 for row in gates if row["status"] == "REJECTED_DIAGNOSTIC"),
        "diagnostic_only_count": sum(1 for row in gates if row["status"] == "DIAGNOSTIC_ONLY"),
        "pass_research_only_count": sum(1 for row in gates if row["status"] == "PASS_RESEARCH_ONLY"),
        "pass_operational_count": sum(1 for row in gates if row["status"] == "PASS_OPERATIONAL"),
        "replay_root_cause_counts": root_cause_counts,
        "replay_dominant_root_cause": dominant_root_cause,
        "replay_promotion_usable_modes": replay_mode_promotion_usable_count,
        "replay_modes_compared": replay_mode_count,
        "replay_repair_attempt_count": replay_attempt_count,
        "replay_repair_best_current_snapshot_attempt": replay_attempt.get("best_current_snapshot_attempt", ""),
        "replay_repair_strict_selected_scan_attempt_verdict": replay_attempt.get(
            "strict_selected_scan_attempt_verdict",
            "",
        ),
        "replay_repair_matched_drift_patch_rows": replay_attempt.get("matched_drift_patch_rows", ""),
        "replay_repair_matched_drift_patch_removes_entry_drift": replay_attempt.get(
            "matched_drift_patch_removes_entry_drift",
            "",
        ),
        "replay_repair_current_snapshot_repairs_promotion_usable": replay_attempt_repairs_usable,
        "replay_repair_remaining_blockers_after_best_attempt": replay_attempt.get(
            "remaining_blockers_after_best_attempt",
            "",
        ),
    }
    return sorted(gates, key=lambda row: (status_rank(row["status"]), row["gate_id"])), summary


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    gates, summary = build_gap_matrix(args)
    write_csv(args.out_dir / "btc1h_promotion_gap_matrix.csv", gates)
    write_csv(args.out_dir / "btc1h_promotion_gap_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC1H Promotion Gap Matrix",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Variant: `{summary['variant']}`",
        f"Deployable now: `{summary['deployable_now']}`",
        f"Recommended policy change: `{summary['recommended_policy_change']}`",
        f"Recommended next action: `{summary['recommended_next_action']}`",
        "",
        "## Summary",
        "",
        markdown_table([summary]),
        "",
        "## Gate Matrix",
        "",
        markdown_table(gates),
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
