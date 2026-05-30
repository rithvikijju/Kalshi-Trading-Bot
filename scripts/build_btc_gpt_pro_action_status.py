#!/usr/bin/env python3
"""Build a current-status checklist from the latest GPT Pro BTC review.

This is a control artifact, not a strategy search and not an execution tool.
It binds GPT Pro's recommended next steps to local evidence so that prose
recommendations do not quietly turn into deployment permission.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
REVIEW_ROOT = PROJECT_ROOT / "docs" / "gpt_pro_reviews"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_gpt_pro_action_status_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def find_default_review() -> Path:
    candidates = sorted(
        REVIEW_ROOT.glob("gpt_pro_strategy_advisor*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return REVIEW_ROOT / "gpt_pro_strategy_advisor_20260518_023800_exact.md"


DEFAULT_REVIEW = find_default_review()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build GPT Pro BTC action/status checklist.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--pro-review", type=Path, default=DEFAULT_REVIEW)
    p.add_argument("--readiness-dir", type=Path, default=BACKTEST_ROOT / "deployment_readiness_latest_codex")
    p.add_argument("--consistency-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_consistency_audit_latest_codex")
    p.add_argument("--row-reconciliation-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_row_reconciliation_latest_codex")
    p.add_argument("--restart-auth-dir", type=Path, default=BACKTEST_ROOT / "btc_restart_authorization_packet_latest_codex")
    p.add_argument("--post-restart-dir", type=Path, default=BACKTEST_ROOT / "btc_post_restart_collection_gate_latest_codex")
    p.add_argument("--post-restart-verification-dir", type=Path, default=BACKTEST_ROOT / "btc_post_restart_verification_latest_codex")
    p.add_argument("--frozen-policy-dir", type=Path, default=BACKTEST_ROOT / "btc_frozen_policy_parity_latest_codex")
    p.add_argument("--execution-realism-dir", type=Path, default=BACKTEST_ROOT / "btc_execution_realism_audit_latest_codex")
    p.add_argument("--kill-continue-dir", type=Path, default=BACKTEST_ROOT / "btc_kill_continue_latest_codex")
    p.add_argument("--shadow-status-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex")
    p.add_argument("--shadow-official-dir", type=Path, default=BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex")
    p.add_argument("--candidate-packet-dir", type=Path, default=BACKTEST_ROOT / "btc15m_next_forward_candidate_packet_latest_codex")
    p.add_argument("--starvation-dir", type=Path, default=BACKTEST_ROOT / "btc15m_signal_starvation_latest_codex")
    p.add_argument("--side-semantics-dir", type=Path, default=BACKTEST_ROOT / "btc15m_first_signal_side_semantics_latest_codex")
    p.add_argument("--btc1h-coverage-dir", type=Path, default=BACKTEST_ROOT / "btc1h_replay_coverage_audit_latest_codex")
    p.add_argument(
        "--btc1h-promotion-deficit-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_candidate_promotion_deficit_latest_codex",
    )
    p.add_argument("--basis-model-dir", type=Path, default=BACKTEST_ROOT / "btc_settlement_basis_model_feasibility_latest_codex")
    p.add_argument("--official-feature-dir", type=Path, default=BACKTEST_ROOT / "btc_official_settlement_feature_table_latest_codex")
    p.add_argument("--drawdown-dir", type=Path, default=BACKTEST_ROOT / "btc_drawdown_sequence_audit_latest_codex")
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    out = str(value)
    if out.lower() in {"nan", "none", "<na>"}:
        return default
    return out


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return text(value).strip().lower() in {"true", "1", "yes", "y"}


def num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(out):
        return default
    return out


def same_path_text(left: Any, right: Any) -> bool:
    left_text = text(left)
    right_text = text(right)
    if not left_text or not right_text:
        return False
    try:
        return Path(left_text).resolve() == Path(right_text).resolve()
    except Exception:
        return left_text.rstrip("\\/").lower() == right_text.rstrip("\\/").lower()


def first_row(df: pd.DataFrame, **filters: str) -> dict[str, Any]:
    if df.empty:
        return {}
    work = df.copy()
    for col, value in filters.items():
        if col not in work.columns:
            return {}
        work = work[work[col].astype(str).eq(str(value))]
    if work.empty:
        return {}
    return work.iloc[0].to_dict()


def tokens(value: Any) -> list[str]:
    raw = text(value)
    if not raw:
        return []
    return [part for part in raw.split(";") if part]


def execution_realism_gate_summary(execution: pd.DataFrame) -> dict[str, Any]:
    """Summarize whether current evidence proves fee/top-book/FOK realism.

    This is intentionally a global action-check summary, not a candidate
    promotion decision. Row reconciliation and readiness still decide whether
    any specific candidate is deployable.
    """
    if execution.empty:
        return {
            "passes": False,
            "status": "BLOCKS_DEPLOYMENT",
            "evidence": "execution_realism_summary missing",
            "replay_ready": 0,
            "replay_total": 0,
            "ledger_ready": 0,
            "ledger_total": 0,
            "fee_issue_rows": 0,
            "top_book_or_fok_issue_rows": 0,
            "no_filled_ledger_rows": 0,
        }

    work = execution.copy()
    profile = work.get("profile", pd.Series("", index=work.index)).astype(str)
    status = work.get("audit_status", pd.Series("", index=work.index)).astype(str)
    blockers = work.get("blockers", pd.Series("", index=work.index)).fillna("").astype(str)
    row_count = pd.to_numeric(work.get("rows", pd.Series(0, index=work.index)), errors="coerce").fillna(0)
    has_rows = row_count.gt(0)

    fee_present = pd.to_numeric(work.get("fee_present_rate", pd.Series(0, index=work.index)), errors="coerce").fillna(0)
    fee_nonnegative = pd.to_numeric(
        work.get("fee_nonnegative_rate", pd.Series(0, index=work.index)), errors="coerce"
    ).fillna(0)
    entry_match = pd.to_numeric(
        work.get("entry_matches_side_ask_rate", pd.Series(0, index=work.index)), errors="coerce"
    ).fillna(0)
    visible_match = pd.to_numeric(
        work.get("visible_qty_matches_side_ask_qty_rate", pd.Series(0, index=work.index)), errors="coerce"
    ).fillna(0)
    visible_min = pd.to_numeric(
        work.get("visible_qty_ge_candidate_min_rate", pd.Series(0, index=work.index)), errors="coerce"
    ).fillna(0)
    quote_age = pd.to_numeric(
        work.get("quote_age_le_limit_rate", pd.Series(0, index=work.index)), errors="coerce"
    ).fillna(0)
    top_visible = pd.to_numeric(
        work.get("top_visible_qty_ge_contracts_rate", pd.Series(0, index=work.index)), errors="coerce"
    ).fillna(0)
    one_trade = work.get("one_trade_per_event", pd.Series(False, index=work.index)).map(boolish)

    replay_mask = profile.eq("live_ws_replay")
    ledger_mask = profile.eq("paper_shadow_ledger")
    replay_ready = (
        replay_mask
        & status.str.startswith("PASS_REPLAY_EXECUTION_FIELDS")
        & fee_present.ge(1.0)
        & fee_nonnegative.ge(1.0)
        & entry_match.ge(1.0)
        & visible_match.ge(1.0)
        & visible_min.ge(1.0)
        & one_trade
    )
    ledger_ready = (
        ledger_mask
        & status.str.startswith("PASS_LEDGER_EXECUTION_FIELDS")
        & fee_present.ge(1.0)
        & fee_nonnegative.ge(1.0)
        & entry_match.ge(1.0)
        & quote_age.ge(1.0)
        & top_visible.ge(1.0)
        & one_trade
    )

    fee_issue = (
        has_rows
        & (
            fee_present.lt(1.0)
            | fee_nonnegative.lt(1.0)
            | blockers.str.contains("fee_missing_or_negative", regex=False)
        )
    )
    top_book_issue = (
        has_rows
        & (
            blockers.str.contains("entry_not_side_ask", regex=False)
            | blockers.str.contains("visible_qty_not_side_ask_qty", regex=False)
            | blockers.str.contains("visible_qty_below_candidate_min", regex=False)
            | blockers.str.contains("quote_age_missing_or_above_limit", regex=False)
            | blockers.str.contains("top_visible_qty_missing_or_below_contracts", regex=False)
            | blockers.str.contains("entry_not_reconciled_to_side_ask", regex=False)
            | blockers.str.contains("duplicate_event_rows", regex=False)
        )
    )
    no_filled = status.eq("NO_FILLED_LEDGER_ROWS") | blockers.str.contains("no_filled_ledger_rows", regex=False)

    replay_ready_count = int(replay_ready.sum())
    replay_total = int(replay_mask.sum())
    ledger_ready_count = int(ledger_ready.sum())
    ledger_total = int(ledger_mask.sum())
    fee_issue_rows = int(fee_issue.sum())
    top_book_issue_rows = int(top_book_issue.sum())
    no_filled_rows = int(no_filled.sum())
    passes = (
        replay_total > 0
        and replay_ready_count == replay_total
        and ledger_total > 0
        and ledger_ready_count == ledger_total
        and fee_issue_rows == 0
        and top_book_issue_rows == 0
        and no_filled_rows == 0
    )
    return {
        "passes": passes,
        "status": "REVIEW_REQUIRED" if passes else "BLOCKS_DEPLOYMENT",
        "evidence": (
            f"replay_ready={replay_ready_count}/{replay_total}; "
            f"ledger_ready={ledger_ready_count}/{ledger_total}; "
            f"fee_issue_rows={fee_issue_rows}; "
            f"top_book_or_fok_issue_rows={top_book_issue_rows}; "
            f"no_filled_ledger_rows={no_filled_rows}"
        ),
        "replay_ready": replay_ready_count,
        "replay_total": replay_total,
        "ledger_ready": ledger_ready_count,
        "ledger_total": ledger_total,
        "fee_issue_rows": fee_issue_rows,
        "top_book_or_fok_issue_rows": top_book_issue_rows,
        "no_filled_ledger_rows": no_filled_rows,
    }


def side_semantics_gate_summary(side_semantics: pd.DataFrame) -> dict[str, Any]:
    """Summarize whether q250 YES-only is a clean global-first subset."""
    if side_semantics.empty:
        return {
            "passes": False,
            "status": "BLOCKS_Q250_YES_PROMOTION",
            "evidence": "first_signal_side_semantics_summary missing",
            "passed_rows": 0,
            "required_rows": 2,
            "extra_events": 0,
            "missing_target_side_events": 0,
            "opposite_side_global_first_events": 0,
        }
    work = side_semantics.copy()
    target = work.get("target_side", pd.Series("", index=work.index)).astype(str).str.lower()
    comparison = work.get("comparison", pd.Series("", index=work.index)).astype(str)
    required = work[target.eq("yes") & comparison.isin(["full_old_replay", "postfreeze_replay"])].copy()
    if required.empty:
        return {
            "passes": False,
            "status": "BLOCKS_Q250_YES_PROMOTION",
            "evidence": "required q250 YES side-semantics rows missing",
            "passed_rows": 0,
            "required_rows": 2,
            "extra_events": 0,
            "missing_target_side_events": 0,
            "opposite_side_global_first_events": 0,
        }

    status = required.get("audit_status", pd.Series("", index=required.index)).astype(str)
    config_comparable = required.get("config_comparable", pd.Series(False, index=required.index)).map(boolish)
    extra_events = pd.to_numeric(
        required.get("side_first_extra_events", pd.Series(0, index=required.index)), errors="coerce"
    ).fillna(0)
    missing_target = pd.to_numeric(
        required.get("global_target_side_missing_from_side_replay", pd.Series(0, index=required.index)),
        errors="coerce",
    ).fillna(0)
    opposite_side = pd.to_numeric(
        required.get("global_first_opposite_side_events", pd.Series(0, index=required.index)), errors="coerce"
    ).fillna(0)
    passed = (
        status.eq("SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST")
        & config_comparable
        & extra_events.eq(0)
        & missing_target.eq(0)
    )
    passed_rows = int(passed.sum())
    required_rows = 2
    passes = len(required) >= required_rows and passed_rows == len(required)
    return {
        "passes": passes,
        "status": "REVIEW_REQUIRED" if passes else "BLOCKS_Q250_YES_PROMOTION",
        "evidence": (
            f"subset_rows_passed={passed_rows}/{len(required)}; "
            f"required_comparisons={required_rows}; "
            f"side_first_extra_events={int(extra_events.sum())}; "
            f"global_target_side_missing={int(missing_target.sum())}; "
            f"global_first_opposite_side_events={int(opposite_side.sum())}"
        ),
        "passed_rows": passed_rows,
        "required_rows": required_rows,
        "extra_events": int(extra_events.sum()),
        "missing_target_side_events": int(missing_target.sum()),
        "opposite_side_global_first_events": int(opposite_side.sum()),
    }


def process_hygiene_gate_summary(shadow_info: dict[str, Any]) -> dict[str, Any]:
    """Summarize duplicate/unmanaged BTC process risk from the shadow status audit."""
    duplicate_count = int(num(shadow_info.get("duplicate_target_process_count", 0), 0))
    unmanaged_count = int(num(shadow_info.get("unmanaged_matching_process_count", 0), 0))
    duplicate_names = text(shadow_info.get("duplicate_target_names", ""))
    passes = duplicate_count == 0 and unmanaged_count == 0
    evidence_parts = [
        f"duplicate_target_processes={duplicate_count}",
        f"unmanaged_matching_processes={unmanaged_count}",
    ]
    if duplicate_names:
        evidence_parts.append(f"duplicate_targets={duplicate_names}")
    created_at = text(shadow_info.get("created_at_utc", ""))
    if created_at:
        evidence_parts.append(f"shadow_status_created={created_at}")
    return {
        "passes": passes,
        "status": "REVIEW_REQUIRED" if passes else "BLOCKS_DEPLOYMENT",
        "duplicate_target_process_count": duplicate_count,
        "unmanaged_matching_process_count": unmanaged_count,
        "duplicate_target_names": duplicate_names,
        "evidence": "; ".join(evidence_parts),
    }


def btc1h_remote_provenance_gate_summary(consistency_row: dict[str, Any]) -> dict[str, Any]:
    if not consistency_row:
        return {
            "passes": False,
            "status": "BLOCKS_BTC1H_PROMOTION",
            "evidence": "btc1h consistency row missing",
            "verdict": "MISSING",
            "status_age_minutes": 0.0,
            "official_rows": 0,
            "official_pnl": 0.0,
            "proxy_official_mismatches": 0,
            "clean_clock_status": "MISSING",
            "clean_clock_ready": False,
        }
    verdict = text(consistency_row.get("btc1h_remote_provenance_verdict", "MISSING"))
    status_age = num(consistency_row.get("btc1h_remote_status_age_minutes", 0), 0)
    status_fresh = boolish(consistency_row.get("btc1h_remote_status_fresh", False))
    official_rows = int(num(consistency_row.get("btc1h_remote_official_rows", 0), 0))
    official_pnl = num(consistency_row.get("btc1h_remote_official_pnl", 0), 0)
    mismatches = int(num(consistency_row.get("btc1h_remote_proxy_official_mismatches", 0), 0))
    clean_status = text(consistency_row.get("btc1h_clean_clock_status", ""))
    clean_ready = boolish(consistency_row.get("btc1h_clean_clock_ready", False))
    blockers = text(consistency_row.get("blocking_reasons", ""))
    passes = (
        verdict == "REMOTE_CLEAN_CLOCK_READY"
        and status_fresh
        and clean_ready
        and official_rows >= 50
        and mismatches == 0
        and official_pnl > 0
    )
    evidence = (
        f"verdict={verdict}; "
        f"remote_status_age_minutes={status_age}; "
        f"remote_status_fresh={status_fresh}; "
        f"remote_official_rows={official_rows}; "
        f"remote_official_pnl={official_pnl}; "
        f"remote_proxy_official_mismatches={mismatches}; "
        f"clean_clock_status={clean_status}; "
        f"clean_clock_ready={clean_ready}"
    )
    if blockers:
        evidence = f"{evidence}; blockers={blockers}"
    return {
        "passes": passes,
        "status": "REVIEW_REQUIRED" if passes else "BLOCKS_BTC1H_PROMOTION",
        "evidence": evidence,
        "verdict": verdict,
        "status_age_minutes": status_age,
        "official_rows": official_rows,
        "official_pnl": official_pnl,
        "proxy_official_mismatches": mismatches,
        "clean_clock_status": clean_status,
        "clean_clock_ready": clean_ready,
    }


def btc1h_candidate_promotion_deficit_gate_summary(deficit_row: dict[str, Any]) -> dict[str, Any]:
    if not deficit_row:
        return {
            "passes": False,
            "status": "BLOCKS_BTC1H_PROMOTION",
            "evidence": "btc1h candidate promotion deficit summary missing",
            "near_deployable_count": 0,
            "clean_row_deficit": 0,
            "mismatch_excess": 0.0,
            "replay_exact_deficit": 0.0,
            "execution_field_deficit": 0.0,
        }
    near_count = int(num(deficit_row.get("candidates_current_artifacts_can_make_near_deployable", 0), 0))
    no_countable = int(num(deficit_row.get("candidates_with_no_promotion_countable_data", 0), 0))
    clean_deficit = int(num(deficit_row.get("clean_official_row_deficit", 0), 0))
    mismatch_excess = num(deficit_row.get("active_proxy_official_mismatch_rate_excess", 0), 0)
    replay_deficit = num(deficit_row.get("active_replay_exact_match_rate_deficit", 0), 0)
    execution_deficit = num(deficit_row.get("active_execution_field_complete_rate_deficit", 0), 0)
    missing_fields = int(num(deficit_row.get("faithful_replay_missing_required_field_count", 0), 0))
    faithful_support = boolish(deficit_row.get("faithful_replay_current_artifacts_can_support", False))
    objective_complete = boolish(deficit_row.get("objective_complete", False))
    passes = (
        objective_complete
        and near_count > 0
        and no_countable == 0
        and clean_deficit == 0
        and mismatch_excess == 0
        and replay_deficit == 0
        and execution_deficit == 0
        and missing_fields == 0
        and faithful_support
    )
    return {
        "passes": passes,
        "status": "REVIEW_REQUIRED" if passes else "BLOCKS_BTC1H_PROMOTION",
        "evidence": (
            f"current_artifacts_near_deployable_candidates={near_count}; "
            f"no_promotion_countable_candidates={no_countable}; "
            f"clean_row_deficit={clean_deficit}; "
            f"active_mismatch_excess={mismatch_excess}; "
            f"active_replay_exact_deficit={replay_deficit}; "
            f"active_execution_field_deficit={execution_deficit}; "
            f"faithful_missing_fields={missing_fields}; "
            f"faithful_artifacts_support={faithful_support}"
        ),
        "near_deployable_count": near_count,
        "clean_row_deficit": clean_deficit,
        "mismatch_excess": mismatch_excess,
        "replay_exact_deficit": replay_deficit,
        "execution_field_deficit": execution_deficit,
        "missing_fields": missing_fields,
        "faithful_artifacts_support": faithful_support,
    }


def latest_review_excerpt(path: Path) -> str:
    if not path.exists():
        return "missing_gpt_pro_review"
    body = path.read_text(encoding="utf-8", errors="replace")
    for line in body.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if clean.startswith("# GPT Pro Review:"):
            continue
        if clean.startswith(("Saved:", "Packet manifest:", "ChatGPT URL:", "---")):
            continue
        if clean == "Pasted markdown":
            continue
        if clean:
            return clean[:240]
    return "empty_gpt_pro_review"


def candidate_row(
    *,
    candidate: str,
    pro_rank: str,
    pro_instruction: str,
    restart_row: dict[str, Any],
    kill_row: dict[str, Any],
    shadow_row: dict[str, Any],
    official_since: dict[str, Any],
    freeze_row: dict[str, Any],
    starvation_row: dict[str, Any],
    reconciliation_row: dict[str, Any],
    policy_row: dict[str, Any],
    evidence_clock_ready: bool,
    consistency_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    consistency_row = consistency_row or {}
    running = boolish(shadow_row.get("running", False))
    source_freshness_status = text(shadow_row.get("source_freshness_status", ""))
    process_predates_latest_source = boolish(shadow_row.get("process_predates_latest_source", False))
    source_freshness_current = running and source_freshness_status == "RUNNING_SOURCE_CURRENT"
    source_freshness_stale = (
        source_freshness_status == "RUNNING_SOURCE_STALE_RESTART_REQUIRED"
        or process_predates_latest_source
    )
    production_ready = boolish(kill_row.get("production_ready", False))
    post_restart_rows = num(restart_row.get("post_restart_official_rows", 0), 0)
    min_post_restart = num(restart_row.get("min_post_restart_official_rows", 0), 0)
    action = text(kill_row.get("action", ""))
    authorization = text(restart_row.get("authorization_packet_status", ""))
    can_collect_after_auth = authorization.startswith("READY_FOR_USER_AUTHORIZATION")
    row_reconciliation_promotion_usable = boolish(reconciliation_row.get("promotion_usable", False))
    frozen_policy_pass = boolish(policy_row.get("policy_parity_pass", False))
    deployable_now = (
        production_ready
        and source_freshness_current
        and evidence_clock_ready
        and frozen_policy_pass
        and post_restart_rows >= min_post_restart
        and min_post_restart > 0
        and row_reconciliation_promotion_usable
    )
    if deployable_now:
        status = "DEPLOYMENT_REVIEW_POSSIBLE"
    elif source_freshness_stale:
        status = "RUNNING_SOURCE_STALE_RESTART_REQUIRED"
    elif candidate.startswith("btc1h_") and text(
        consistency_row.get("agreement_status", "")
    ).startswith("btc1h_remote_"):
        status = text(consistency_row.get("agreement_status", "")).upper()
    elif can_collect_after_auth:
        status = "WAITING_FOR_EXPLICIT_PAPER_RESTART_AUTHORIZATION"
    elif not running and "yes" in candidate:
        status = "NOT_RUNNING_NEEDS_EXPLICIT_PAPER_START"
    else:
        status = "RESEARCH_CONTROL_ONLY"
    return {
        "candidate": candidate,
        "pro_rank": pro_rank,
        "pro_instruction": pro_instruction,
        "current_status": status,
        "deployable_now": deployable_now,
        "running": running,
        "current_pids": text(shadow_row.get("pids", "")) or text(restart_row.get("current_pids", "")),
        "process_count": int(num(shadow_row.get("process_count", 0), 0)),
        "duplicate_process_count": int(num(shadow_row.get("duplicate_process_count", 0), 0)),
        "process_hygiene_status": text(shadow_row.get("process_hygiene_status", "")),
        "source_freshness_status": source_freshness_status,
        "source_freshness_current": source_freshness_current,
        "source_latest_path": text(shadow_row.get("source_latest_path", "")),
        "source_latest_mtime_utc": text(shadow_row.get("source_latest_mtime_utc", "")),
        "process_predates_latest_source": process_predates_latest_source,
        "kill_continue_action": action,
        "authorization_packet_status": authorization,
        "user_permission_required": boolish(restart_row.get("user_permission_required", True)),
        "restart_path_status": text(restart_row.get("restart_path_status", "")),
        "latest_restart_plan_script_safety_pass": boolish(
            restart_row.get("latest_restart_plan_script_safety_pass", False)
        ),
        "active_ledger_schema_status": text(restart_row.get("active_ledger_schema_status", "")),
        "post_restart_gate_status": text(restart_row.get("post_restart_gate_status", "")),
        "post_restart_evidence_clock_ready": evidence_clock_ready,
        "frozen_policy_parity_status": text(policy_row.get("policy_parity_status", "")),
        "frozen_policy_parity_pass": frozen_policy_pass,
        "frozen_policy_parity_blockers": text(policy_row.get("policy_parity_blockers", "")),
        "post_restart_official_rows": post_restart_rows,
        "min_post_restart_official_rows": min_post_restart,
        "official_filled_since": num(official_since.get("official_filled_rows", 0), 0),
        "official_pnl_since": num(official_since.get("official_pnl", 0), 0),
        "proxy_official_mismatches_since": num(
            official_since.get("official_proxy_result_mismatches", 0), 0
        ),
        "shadow_signal_rows_since": num(starvation_row.get("signal_rows_since", 0), 0),
        "shadow_nonzero_candidate_rows_since": num(
            starvation_row.get("nonzero_candidate_rows_since", 0), 0
        ),
        "shadow_order_decision_rows_since": num(starvation_row.get("order_decision_rows_since", 0), 0),
        "starvation_status": text(starvation_row.get("collection_status", "")),
        "row_reconciliation_status": text(reconciliation_row.get("reconciliation_status", "")),
        "row_reconciliation_pass": boolish(reconciliation_row.get("row_reconciliation_pass", False)),
        "row_reconciliation_promotion_usable": row_reconciliation_promotion_usable,
        "row_reconciliation_matched_rows": num(reconciliation_row.get("matched_rows", 0), 0),
        "row_reconciliation_blockers": text(reconciliation_row.get("blocking_reasons", "")),
        "forward_consistency_status": text(consistency_row.get("agreement_status", "")),
        "forward_consistency_blockers": text(consistency_row.get("blocking_reasons", "")),
        "btc1h_remote_provenance_verdict": text(
            consistency_row.get("btc1h_remote_provenance_verdict", "")
        ),
        "btc1h_remote_status_age_minutes": num(
            consistency_row.get("btc1h_remote_status_age_minutes", 0), 0
        ),
        "btc1h_remote_status_fresh": boolish(
            consistency_row.get("btc1h_remote_status_fresh", False)
        ),
        "btc1h_remote_official_rows": num(consistency_row.get("btc1h_remote_official_rows", 0), 0),
        "btc1h_remote_official_pnl": num(consistency_row.get("btc1h_remote_official_pnl", 0), 0),
        "btc1h_remote_proxy_official_mismatches": num(
            consistency_row.get("btc1h_remote_proxy_official_mismatches", 0), 0
        ),
        "btc1h_clean_clock_status": text(consistency_row.get("btc1h_clean_clock_status", "")),
        "btc1h_clean_clock_ready": boolish(consistency_row.get("btc1h_clean_clock_ready", False)),
        "freeze_status": text(freeze_row.get("status", "")),
        "freeze_min_future_official_rows": text(freeze_row.get("min_future_official_rows", "")),
        "next_allowed_action": text(
            kill_row.get("next_step", "")
            or restart_row.get("kill_continue_next_step", "")
            or "No deployment action allowed from current evidence."
        ),
    }


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    readiness_info = read_json(args.readiness_dir / "run_info.json")
    consistency_info = read_json(args.consistency_dir / "run_info.json")
    row_reconciliation_info = read_json(args.row_reconciliation_dir / "run_info.json")
    restart_info = read_json(args.restart_auth_dir / "run_info.json")
    post_restart_info = read_json(args.post_restart_dir / "run_info.json")
    post_restart_verification_info = read_json(args.post_restart_verification_dir / "run_info.json")
    frozen_policy_info = read_json(args.frozen_policy_dir / "run_info.json")
    basis_info = read_json(args.basis_model_dir / "run_info.json")
    drawdown_info = read_json(args.drawdown_dir / "run_info.json")
    shadow_info = read_json(args.shadow_status_dir / "run_info.json")

    readiness = read_csv(args.readiness_dir / "readiness_summary.csv")
    consistency = read_csv(args.consistency_dir / "forward_consistency_summary.csv")
    row_reconciliation = read_csv(args.row_reconciliation_dir / "row_reconciliation_summary.csv")
    restart = read_csv(args.restart_auth_dir / "restart_authorization_summary.csv")
    post_restart = read_csv(args.post_restart_dir / "post_restart_collection_gate_summary.csv")
    frozen_policy = read_csv(args.frozen_policy_dir / "frozen_policy_parity_summary.csv")
    execution_realism = read_csv(args.execution_realism_dir / "execution_realism_summary.csv")
    kill = read_csv(args.kill_continue_dir / "kill_continue_summary.csv")
    shadows = read_csv(args.shadow_status_dir / "shadow_status.csv")
    official = read_csv(args.shadow_official_dir / "shadow_official_summary.csv")
    freeze = read_csv(args.candidate_packet_dir / "candidate_freeze_specs.csv")
    starvation = read_csv(args.starvation_dir / "signal_starvation_summary.csv")
    side_semantics = read_csv(args.side_semantics_dir / "first_signal_side_semantics_summary.csv")
    btc1h_coverage = read_csv(args.btc1h_coverage_dir / "btc1h_replay_coverage_summary.csv")
    btc1h_promotion_deficit = read_csv(
        args.btc1h_promotion_deficit_dir / "btc1h_candidate_promotion_deficit_summary.csv"
    )
    feature_summary = read_csv(args.official_feature_dir / "official_settlement_feature_summary.csv")
    guard = read_csv(args.basis_model_dir / "basis_guard_deployment_verdict.csv")
    drawdown = read_csv(args.drawdown_dir / "drawdown_sequence_summary.csv")

    production_ready_count = int(readiness_info.get("production_ready_count", 0) or 0)
    consistent_count = int(consistency_info.get("consistent_enough_for_promotion_count", 0) or 0)
    row_reconciliation_pass_count = int(
        row_reconciliation["row_reconciliation_pass"].astype(str).str.lower().eq("true").sum()
        if not row_reconciliation.empty and "row_reconciliation_pass" in row_reconciliation.columns
        else 0
    )
    row_reconciliation_promotion_usable_count = int(
        row_reconciliation["promotion_usable"].astype(str).str.lower().eq("true").sum()
        if not row_reconciliation.empty and "promotion_usable" in row_reconciliation.columns
        else 0
    )
    restart_executed = boolish(post_restart_info.get("restart_executed", False))
    verification_restart_executed = boolish(post_restart_verification_info.get("restart_executed", False))
    evidence_clock_ready = boolish(post_restart_verification_info.get("evidence_clock_ready", False))
    verification_collection_gate_ready = boolish(post_restart_verification_info.get("collection_gate_ready", False))
    verifier_restart_dir = text(post_restart_verification_info.get("restart_dir", ""))
    latest_restart_plan_dir = text(restart_info.get("latest_restart_plan_dir", ""))
    verifier_restart_alignment_pass = same_path_text(verifier_restart_dir, latest_restart_plan_dir)
    frozen_policy_pass_count = int(frozen_policy_info.get("policy_parity_pass_count", 0) or 0)
    frozen_policy_count = int(frozen_policy_info.get("policy_count", 0) or 0)
    all_frozen_policy_pass = boolish(frozen_policy_info.get("all_policy_parity_pass", False))
    ready_auth_count = int(restart_info.get("ready_for_user_authorization_count", 0) or 0)
    latest_plan_execute = boolish(restart_info.get("latest_restart_plan_execute", False))
    promotion_usable_rows = int(
        pd.to_numeric(feature_summary.get("promotion_usable_rows", pd.Series(dtype=float)), errors="coerce")
        .fillna(0)
        .sum()
        if not feature_summary.empty
        else 0
    )
    deployable_basis_guards = int(
        guard["deployable_guard_now"].astype(str).str.lower().eq("true").sum()
        if not guard.empty and "deployable_guard_now" in guard.columns
        else 0
    )
    btc1h_since_coverage = first_row(btc1h_coverage, scope="since")
    btc1h_coverage_gate_pass = boolish(btc1h_since_coverage.get("coverage_gate_pass", False))
    btc1h_replayable_rows = int(num(btc1h_since_coverage.get("replayable_rows", 0), 0))
    btc1h_paper_rows = int(num(btc1h_since_coverage.get("paper_rows", 0), 0))
    btc1h_locked_rows = int(num(btc1h_since_coverage.get("locked_blocked_rows", 0), 0))
    btc1h_consistency_row = first_row(consistency, candidate="btc1h_high_conf80_entry70_no_chase")
    btc1h_remote_gate = btc1h_remote_provenance_gate_summary(btc1h_consistency_row)
    btc1h_deficit_row = btc1h_promotion_deficit.iloc[0].to_dict() if not btc1h_promotion_deficit.empty else {}
    btc1h_deficit_gate = btc1h_candidate_promotion_deficit_gate_summary(btc1h_deficit_row)
    target_shadow_names = {
        "btc15m_q250_qty500_firstskip_shadow",
        "btc15m_q250_qty500_firstskip_yes_shadow",
        "btc15m_q1000_yes_shadow",
        "btc1h_high_conf80_entry70_no_chase_shadow",
    }
    target_shadows = (
        shadows[shadows["name"].astype(str).isin(target_shadow_names)].copy()
        if not shadows.empty and "name" in shadows.columns
        else pd.DataFrame()
    )
    source_status = target_shadows.get("source_freshness_status", pd.Series(dtype=str)).astype(str)
    source_running = target_shadows.get("running", pd.Series(dtype=str)).astype(str).str.lower().eq("true")
    source_predates = (
        target_shadows.get("process_predates_latest_source", pd.Series(dtype=str))
        .astype(str)
        .str.lower()
        .eq("true")
    )
    source_stale_count = int((source_status.eq("RUNNING_SOURCE_STALE_RESTART_REQUIRED") | source_predates).sum())
    source_current_count = int((source_status.eq("RUNNING_SOURCE_CURRENT") & source_running).sum())
    source_not_running_or_missing_count = len(target_shadow_names) - int(source_running.sum())
    source_freshness_gate_pass = (
        source_stale_count == 0
        and source_not_running_or_missing_count == 0
        and source_current_count == len(target_shadow_names)
    )
    execution_gate = execution_realism_gate_summary(execution_realism)
    side_semantics_gate = side_semantics_gate_summary(side_semantics)
    process_hygiene_gate = process_hygiene_gate_summary(shadow_info)
    promotion_drawdown = (
        drawdown[drawdown["scope"].astype(str).eq("post_restart_promotion_window")]
        if not drawdown.empty and "scope" in drawdown.columns
        else pd.DataFrame()
    )
    drawdown_gate_pass_count = int(
        promotion_drawdown["drawdown_gate_pass"].astype(str).str.lower().eq("true").sum()
        if not promotion_drawdown.empty and "drawdown_gate_pass" in promotion_drawdown.columns
        else 0
    )
    drawdown_statuses = (
        ";".join(promotion_drawdown.get("drawdown_gate_status", pd.Series(dtype=str)).astype(str).tolist())
        if not promotion_drawdown.empty
        else "missing_drawdown_sequence_summary"
    )

    checks = [
        {
            "check": "current_deployment_verdict",
            "status": "BLOCKS_DEPLOYMENT" if production_ready_count == 0 else "REVIEW_REQUIRED",
            "passes_for_deployment": production_ready_count > 0,
            "evidence": f"readiness production_ready_count={production_ready_count}",
            "next_action": "Do not deploy or canary unless readiness becomes positive and all underlying gates agree.",
        },
        {
            "check": "forward_layer_agreement",
            "status": "BLOCKS_DEPLOYMENT" if consistent_count == 0 else "REVIEW_REQUIRED",
            "passes_for_deployment": consistent_count > 0,
            "evidence": f"forward consistency count={consistent_count}",
            "next_action": "Require agreement across replay, paper ledger, official settlement, and basis gates.",
        },
        {
            "check": "paper_replay_row_reconciliation",
            "status": "BLOCKS_DEPLOYMENT" if row_reconciliation_promotion_usable_count == 0 else "REVIEW_REQUIRED",
            "passes_for_deployment": row_reconciliation_promotion_usable_count > 0,
            "evidence": (
                f"row_reconciliation_pass={row_reconciliation_pass_count}; "
                f"promotion_usable={row_reconciliation_promotion_usable_count}; "
                f"artifact_created={row_reconciliation_info.get('created_at_utc', '')}"
            ),
            "next_action": "Require per-row paper/replay/official agreement with complete ledger execution fields before promotion.",
        },
        {
            "check": "fee_top_book_fok_execution_realism",
            "status": execution_gate["status"],
            "passes_for_deployment": bool(execution_gate["passes"]),
            "evidence": execution_gate["evidence"],
            "next_action": "Require fee-present, nonnegative-fee, executable side-ask, visible-size/FOK, quote-freshness, and one-trade-per-event evidence in both replay and ledger rows.",
        },
        {
            "check": "q250_yes_first_signal_side_semantics",
            "status": side_semantics_gate["status"],
            "passes_for_deployment": bool(side_semantics_gate["passes"]),
            "evidence": side_semantics_gate["evidence"],
            "next_action": "Treat q250 YES-only as a fresh policy if side-filtered first-signal replay ever adds events beyond global first-signal replay or config comparability fails.",
        },
        {
            "check": "promotion_usable_official_rows",
            "status": "BLOCKS_DEPLOYMENT" if promotion_usable_rows == 0 else "REVIEW_REQUIRED",
            "passes_for_deployment": promotion_usable_rows > 0,
            "evidence": f"canonical official feature table promotion_usable_rows={promotion_usable_rows}",
            "next_action": "Only rows with official settlement plus decision/execution fields can count.",
        },
        {
            "check": "process_hygiene",
            "status": process_hygiene_gate["status"],
            "passes_for_deployment": bool(process_hygiene_gate["passes"]),
            "evidence": process_hygiene_gate["evidence"],
            "next_action": "Resolve duplicate or unmanaged BTC paper/capture processes only with explicit user authorization before counting future rows.",
        },
        {
            "check": "running_source_freshness",
            "status": "BLOCKS_DEPLOYMENT" if not source_freshness_gate_pass else "REVIEW_REQUIRED",
            "passes_for_deployment": source_freshness_gate_pass,
            "evidence": (
                f"target_source_current={source_current_count}/{len(target_shadow_names)}; "
                f"stale_or_predates_latest_source={source_stale_count}; "
                f"not_running_or_missing={source_not_running_or_missing_count}"
            ),
            "next_action": "Rows from a process that predates its source file cannot count toward promotion; restart/start only with explicit user authorization.",
        },
        {
            "check": "post_restart_collection_gate",
            "status": "BLOCKS_DEPLOYMENT" if not restart_executed else "REVIEW_REQUIRED",
            "passes_for_deployment": restart_executed and not post_restart.empty,
            "evidence": f"restart_executed={restart_executed}; restart_utc={post_restart_info.get('restart_utc', '')}",
            "next_action": "Needs explicit user-authorized paper-only restart/start before future rows can count.",
        },
        {
            "check": "post_restart_verifier_alignment",
            "status": "REVIEW_REQUIRED" if verifier_restart_alignment_pass else "BLOCKS_DEPLOYMENT",
            "passes_for_deployment": verifier_restart_alignment_pass,
            "evidence": (
                f"verifier_restart_dir={verifier_restart_dir}; "
                f"authorization_latest_restart_plan_dir={latest_restart_plan_dir}"
            ),
            "next_action": "Regenerate post-restart verification after each restart dry run or authorization packet refresh so artifacts share the same restart plan.",
        },
        {
            "check": "post_restart_evidence_clock",
            "status": "BLOCKS_DEPLOYMENT" if not evidence_clock_ready else "REVIEW_REQUIRED",
            "passes_for_deployment": evidence_clock_ready,
            "evidence": (
                f"restart_executed={verification_restart_executed}; "
                f"evidence_clock_ready={evidence_clock_ready}; "
                f"collection_gate_ready={verification_collection_gate_ready}; "
                f"artifact_created={post_restart_verification_info.get('created_at_utc', '')}"
            ),
            "next_action": "Require fresh post-restart target PIDs, capture sidecars, and active ledger schemas before any future rows can count.",
        },
        {
            "check": "frozen_policy_parity",
            "status": "BLOCKS_DEPLOYMENT" if not all_frozen_policy_pass else "REVIEW_REQUIRED",
            "passes_for_deployment": all_frozen_policy_pass,
            "evidence": (
                f"policy_parity_pass={frozen_policy_pass_count}/{frozen_policy_count}; "
                f"artifact_created={frozen_policy_info.get('created_at_utc', '')}"
            ),
            "next_action": "Require active wrappers to match preregistered thresholds, side policy, sizing, and paper-only mode before future rows can count.",
        },
        {
            "check": "official_drawdown_sequence_gate",
            "status": "BLOCKS_DEPLOYMENT" if drawdown_gate_pass_count == 0 else "REVIEW_REQUIRED",
            "passes_for_deployment": drawdown_gate_pass_count > 0,
            "evidence": (
                f"post_restart_drawdown_gate_pass={drawdown_gate_pass_count}; "
                f"statuses={drawdown_statuses}; "
                f"artifact_created={drawdown_info.get('created_at_utc', '')}"
            ),
            "next_action": "Require row-level official PnL sequence and max-drawdown gate on clean post-restart rows.",
        },
        {
            "check": "paper_restart_authorization_path",
            "status": "READY_FOR_EXPLICIT_USER_AUTHORIZATION" if ready_auth_count >= 4 and not latest_plan_execute else "NOT_READY",
            "passes_for_deployment": False,
            "evidence": (
                f"ready_for_user_authorization_count={ready_auth_count}; "
                f"latest_restart_plan_execute={latest_plan_execute}; "
                f"latest_restart_plan_dir={restart_info.get('latest_restart_plan_dir', '')}"
            ),
            "next_action": "This authorizes nothing by itself; execute only if the user explicitly approves the restart command.",
        },
        {
            "check": "basis_guard_deployability",
            "status": "BLOCKS_DEPLOYMENT" if deployable_basis_guards == 0 else "REVIEW_REQUIRED",
            "passes_for_deployment": deployable_basis_guards > 0,
            "evidence": f"deployable settlement-basis guards={deployable_basis_guards}; model_rows={basis_info.get('model_rows', '')}",
            "next_action": "Treat basis model as diagnostic until preregistered and validated on fresh official rows.",
        },
        {
            "check": "btc1h_replay_coverage",
            "status": "BLOCKS_BTC1H_PROMOTION" if not btc1h_coverage_gate_pass else "REVIEW_REQUIRED",
            "passes_for_deployment": False,
            "evidence": (
                f"since_replayable_rows={btc1h_replayable_rows}/{btc1h_paper_rows}; "
                f"locked_blocked_rows={btc1h_locked_rows}; "
                f"coverage_gate_pass={btc1h_coverage_gate_pass}"
            ),
            "next_action": "BTC1H needs a readable causal replay capture for since-freeze paper rows before row reconciliation can count.",
        },
        {
            "check": "btc1h_remote_provenance",
            "status": btc1h_remote_gate["status"],
            "passes_for_deployment": bool(btc1h_remote_gate["passes"]),
            "evidence": btc1h_remote_gate["evidence"],
            "next_action": "BTC1H remote official rows cannot count toward promotion until remote status is fresh, the clean evidence clock is ready, proxy/official mismatches are cleared or below gate, and at least 50 official rows exist.",
        },
        {
            "check": "btc1h_candidate_promotion_deficit",
            "status": btc1h_deficit_gate["status"],
            "passes_for_deployment": bool(btc1h_deficit_gate["passes"]),
            "evidence": btc1h_deficit_gate["evidence"],
            "next_action": "Keep BTC1H observe-only until the numeric deficit audit shows clean rows, official/proxy agreement, execution fields, faithful replay, and policy identity all at zero deficit.",
        },
    ]
    checks_df = pd.DataFrame(checks)

    specs = [
        (
            "q250_firstskip_qty500_yes",
            "1",
            "Start/collect paper-only after controlled restart; do not count old rows.",
            "btc15m_q250_qty500_firstskip_yes_shadow",
            "btc15m_q250_firstskip_qty500_yes_forward",
        ),
        (
            "q250_firstskip_qty500",
            "2",
            "Keep as side/basis decomposition control only; raw q250 is not deployable.",
            "btc15m_q250_qty500_firstskip_shadow",
            "",
        ),
        (
            "q1000_yes",
            "3",
            "Keep as sparse cleaner YES control.",
            "btc15m_q1000_yes_shadow",
            "btc15m_q1000_yes_existing_sparse_control",
        ),
        (
            "btc1h_high_conf80_entry70_no_chase",
            "observe_only",
            "Keep observe-only until clean-schema official forward rows exist.",
            "btc1h_high_conf80_entry70_no_chase_shadow",
            "",
        ),
    ]
    candidate_rows = []
    for candidate, rank, instruction, ledger, freeze_id in specs:
        candidate_rows.append(
            candidate_row(
                candidate=candidate,
                pro_rank=rank,
                pro_instruction=instruction,
                restart_row=first_row(restart, candidate=candidate),
                kill_row=first_row(kill, candidate=candidate),
                shadow_row=first_row(shadows, name=ledger),
                official_since=first_row(official, ledger=ledger, scope="since"),
                freeze_row=first_row(freeze, candidate_id=freeze_id) if freeze_id else {},
                starvation_row=first_row(starvation, candidate=candidate),
                reconciliation_row=first_row(row_reconciliation, candidate=candidate),
                policy_row=first_row(frozen_policy, candidate=candidate),
                evidence_clock_ready=evidence_clock_ready,
                consistency_row=first_row(consistency, candidate=candidate),
            )
        )
    candidates_df = pd.DataFrame(candidate_rows)

    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Control artifact only. GPT Pro advice is advisory and cannot override local deployment gates.",
        "gpt_pro_review": str(args.pro_review),
        "gpt_pro_excerpt": latest_review_excerpt(args.pro_review),
        "production_ready_count": production_ready_count,
        "consistent_enough_for_promotion_count": consistent_count,
        "row_reconciliation_pass_count": row_reconciliation_pass_count,
        "row_reconciliation_promotion_usable_count": row_reconciliation_promotion_usable_count,
        "execution_realism_replay_ready": execution_gate["replay_ready"],
        "execution_realism_replay_total": execution_gate["replay_total"],
        "execution_realism_ledger_ready": execution_gate["ledger_ready"],
        "execution_realism_ledger_total": execution_gate["ledger_total"],
        "execution_realism_fee_issue_rows": execution_gate["fee_issue_rows"],
        "execution_realism_top_book_or_fok_issue_rows": execution_gate["top_book_or_fok_issue_rows"],
        "execution_realism_no_filled_ledger_rows": execution_gate["no_filled_ledger_rows"],
        "execution_realism_gate_pass": execution_gate["passes"],
        "q250_yes_side_semantics_gate_pass": side_semantics_gate["passes"],
        "q250_yes_side_semantics_passed_rows": side_semantics_gate["passed_rows"],
        "q250_yes_side_semantics_required_rows": side_semantics_gate["required_rows"],
        "q250_yes_side_semantics_extra_events": side_semantics_gate["extra_events"],
        "q250_yes_side_semantics_missing_target_side_events": side_semantics_gate["missing_target_side_events"],
        "q250_yes_side_semantics_opposite_side_global_first_events": side_semantics_gate[
            "opposite_side_global_first_events"
        ],
        "promotion_usable_rows": promotion_usable_rows,
        "restart_executed": restart_executed,
        "post_restart_verifier_alignment_pass": verifier_restart_alignment_pass,
        "post_restart_verifier_restart_dir": verifier_restart_dir,
        "post_restart_verification_restart_executed": verification_restart_executed,
        "post_restart_evidence_clock_ready": evidence_clock_ready,
        "post_restart_verification_collection_gate_ready": verification_collection_gate_ready,
        "process_hygiene_gate_pass": process_hygiene_gate["passes"],
        "duplicate_target_process_count": process_hygiene_gate["duplicate_target_process_count"],
        "unmanaged_matching_process_count": process_hygiene_gate["unmanaged_matching_process_count"],
        "duplicate_target_names": process_hygiene_gate["duplicate_target_names"],
        "source_freshness_gate_pass": source_freshness_gate_pass,
        "source_freshness_current_count": source_current_count,
        "source_freshness_stale_count": source_stale_count,
        "source_freshness_not_running_or_missing_count": source_not_running_or_missing_count,
        "frozen_policy_parity_pass_count": frozen_policy_pass_count,
        "frozen_policy_count": frozen_policy_count,
        "all_frozen_policy_parity_pass": all_frozen_policy_pass,
        "ready_for_user_authorization_count": ready_auth_count,
        "latest_restart_plan_execute": latest_plan_execute,
        "deployable_basis_guards": deployable_basis_guards,
        "drawdown_gate_pass_count": drawdown_gate_pass_count,
        "btc1h_replayable_rows_since": btc1h_replayable_rows,
        "btc1h_paper_rows_since": btc1h_paper_rows,
        "btc1h_locked_blocked_rows_since": btc1h_locked_rows,
        "btc1h_coverage_gate_pass": btc1h_coverage_gate_pass,
        "btc1h_remote_provenance_gate_pass": btc1h_remote_gate["passes"],
        "btc1h_remote_provenance_verdict": btc1h_remote_gate["verdict"],
        "btc1h_remote_status_age_minutes": btc1h_remote_gate["status_age_minutes"],
        "btc1h_remote_official_rows": btc1h_remote_gate["official_rows"],
        "btc1h_remote_official_pnl": btc1h_remote_gate["official_pnl"],
        "btc1h_remote_proxy_official_mismatches": btc1h_remote_gate["proxy_official_mismatches"],
        "btc1h_clean_clock_status": btc1h_remote_gate["clean_clock_status"],
        "btc1h_clean_clock_ready": btc1h_remote_gate["clean_clock_ready"],
        "btc1h_candidate_promotion_deficit_gate_pass": btc1h_deficit_gate["passes"],
        "btc1h_candidates_current_artifacts_can_make_near_deployable": btc1h_deficit_gate[
            "near_deployable_count"
        ],
        "btc1h_clean_official_row_deficit": btc1h_deficit_gate["clean_row_deficit"],
        "btc1h_active_proxy_official_mismatch_rate_excess": btc1h_deficit_gate["mismatch_excess"],
        "btc1h_active_replay_exact_match_rate_deficit": btc1h_deficit_gate["replay_exact_deficit"],
        "btc1h_active_execution_field_complete_rate_deficit": btc1h_deficit_gate["execution_field_deficit"],
    }

    checks_df.to_csv(args.out_dir / "gpt_pro_action_checklist.csv", index=False)
    candidates_df.to_csv(args.out_dir / "gpt_pro_candidate_status.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC GPT Pro Action Status",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"GPT Pro review: `{args.pro_review}`",
        "",
        "## Verdict",
        "",
        "NO DEPLOY. GPT Pro's recommendations remain blocked by the local gates unless future official-settled, execution-realistic rows prove otherwise.",
        "",
        "## Checklist",
        "",
        checks_df.to_string(index=False),
        "",
        "## Candidate Status",
        "",
        candidates_df.to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- The only ready next operational path is explicit user authorization for the guarded paper-only restart/start.",
        "- Row-level paper/replay reconciliation is now an explicit blocker; aggregate count agreement is not promotion evidence by itself.",
        "- Fee, executable side-ask, visible-size/FOK, quote-freshness, and one-trade-per-event realism are explicit checklist blockers.",
        "- q250 YES-only side semantics are explicit: it must remain a clean subset of global first-signal replay unless it is deliberately treated as a new policy.",
        "- Process hygiene is explicit: duplicate target PIDs or unmanaged BTC processes block future rows from counting until resolved with explicit authorization.",
        "- Running source freshness is explicit: rows from stale pre-edit processes cannot count toward promotion.",
        "- q250 YES-only is the top research path, but it has zero promotion-usable rows and is not running yet.",
        "- q250 raw remains a decomposition/control path because NO-side settlement-basis risk blocks deployment.",
        "- q1000 YES remains a sparse cleaner control.",
        "- BTC1H remains observe-only because the clean-schema official forward sample does not exist.",
        "- BTC1H since-freeze replay coverage is blocked while the active capture DB is locked and lacks a readable sidecar.",
        "- BTC1H remote official rows are shown separately from local process status; stale remote status and the blocked clean evidence clock keep them out of promotion evidence.",
        "- BTC1H promotion deficits are explicit: current artifacts must show zero clean-row, mismatch, execution-field, replay, faithful-capture, and policy-identity deficits before near-deployable discussion.",
        "- This artifact does not execute processes, deploy strategies, or tune thresholds.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
