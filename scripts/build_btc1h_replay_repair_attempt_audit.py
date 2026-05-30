#!/usr/bin/env python3
"""Audit concrete BTC1H replay repair attempts.

This diagnostic quantifies what the currently testable replay repairs can and
cannot fix. It keeps diagnostic simulations separate from independent
counterfactual replay evidence, so a row-level patch cannot accidentally become
promotion evidence.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_replay_repair_attempt_audit_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--current-reconciliation-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_vs_ledger_reconciliation_latest_codex",
    )
    parser.add_argument(
        "--selected-scan-reconciliation-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_vs_ledger_reconciliation_selected_scans_latest_codex",
    )
    parser.add_argument(
        "--prerequisite-audit",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_prerequisite_audit_latest_codex"
        / "btc1h_replay_repair_prerequisite_audit.csv",
    )
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def first(df: pd.DataFrame) -> dict[str, Any]:
    return df.iloc[0].to_dict() if not df.empty else {}


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() != "nan" and text not in out:
            out.append(text)
    return ";".join(out)


def split_events(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def compute_blockers(row: dict[str, Any]) -> str:
    blockers: list[str] = []
    actual_rows = to_int(row.get("actual_rows"))
    replay_rows = to_int(row.get("replay_rows"))
    exact_matches = to_int(row.get("exact_market_side_matches"))
    ledger_only = to_int(row.get("ledger_only_rows"))
    replay_only = to_int(row.get("replay_only_rows"))
    event_replacements = to_int(row.get("event_replacement_rows"))
    entry_drift = to_int(row.get("entry_price_drift_rows"))
    pnl_drift = to_int(row.get("pnl_drift_rows"))
    replay_minus_actual = to_float(row.get("replay_minus_actual_pnl"))
    if actual_rows == 0:
        blockers.append("no_actual_official_ledger_rows")
    if exact_matches != actual_rows or ledger_only:
        blockers.append("missing_actual_rows")
    if replay_rows != actual_rows:
        blockers.append("replay_row_count_differs")
    if replay_only:
        blockers.append("extra_replay_rows")
    if event_replacements:
        blockers.append("event_level_market_replacements")
    if entry_drift:
        blockers.append("entry_price_not_row_for_row_equal")
    if abs(replay_minus_actual) > 1e-9:
        blockers.append("pnl_not_row_for_row_equal")
    elif pnl_drift:
        blockers.append("pnl_path_not_row_for_row_equal")
    return ";".join(blockers)


def summarize_detail(detail: pd.DataFrame, current_summary: dict[str, Any]) -> dict[str, Any]:
    if detail.empty:
        return {
            "actual_rows": 0,
            "replay_rows": 0,
            "exact_market_side_matches": 0,
            "ledger_only_rows": 0,
            "replay_only_rows": 0,
            "event_replacement_rows": 0,
            "entry_price_drift_rows": 0,
            "pnl_drift_rows": 0,
            "actual_official_pnl": 0.0,
            "replay_pnl": 0.0,
            "replay_minus_actual_pnl": 0.0,
        }
    status = detail["status"].astype(str)
    actual_rows = to_int(current_summary.get("actual_rows"))
    replay_rows = to_int(current_summary.get("replay_rows"))
    exact_matches = int(status.eq("exact_market_side_match").sum())
    ledger_only = int(status.str.startswith("ledger_only").sum())
    replay_only = int(status.str.startswith("replay_only").sum())
    event_replacements = int(status.str.contains("event_replacement").sum())
    entry_drift = int(pd.to_numeric(detail.get("entry_price_abs_diff"), errors="coerce").fillna(0.0).abs().gt(1e-9).sum())
    pnl_drift = int(
        pd.to_numeric(detail.get("pnl_diff_replay_minus_ledger"), errors="coerce").fillna(0.0).abs().gt(1e-9).sum()
    )
    actual_pnl = to_float(current_summary.get("actual_official_pnl"))
    replay_pnl = to_float(current_summary.get("replay_pnl"))
    replay_minus_actual = replay_pnl - actual_pnl
    return {
        "actual_rows": actual_rows,
        "replay_rows": replay_rows,
        "exact_market_side_matches": exact_matches,
        "ledger_only_rows": ledger_only,
        "replay_only_rows": replay_only,
        "event_replacement_rows": event_replacements,
        "entry_price_drift_rows": entry_drift,
        "pnl_drift_rows": pnl_drift,
        "actual_official_pnl": round(actual_pnl, 12),
        "replay_pnl": round(replay_pnl, 12),
        "replay_minus_actual_pnl": round(replay_minus_actual, 12),
    }


def patch_matched_drift_detail(detail: pd.DataFrame, patch_events: set[str]) -> tuple[pd.DataFrame, int]:
    if detail.empty or not patch_events:
        return detail.copy(), 0
    work = detail.copy()
    event = work.get("event_ticker", pd.Series("", index=work.index)).astype(str)
    status = work.get("status", pd.Series("", index=work.index)).astype(str)
    entry_diff = pd.to_numeric(work.get("entry_price_abs_diff"), errors="coerce").fillna(0.0).abs()
    pnl_diff = pd.to_numeric(work.get("pnl_diff_replay_minus_ledger"), errors="coerce").fillna(0.0).abs()
    mask = event.isin(patch_events) & status.eq("exact_market_side_match") & ((entry_diff > 1e-9) | (pnl_diff > 1e-9))
    patched = int(mask.sum())
    if patched:
        work.loc[mask, "replay_entry_price"] = work.loc[mask, "ledger_entry_price"]
        work.loc[mask, "replay_pnl"] = work.loc[mask, "ledger_pnl"]
        work.loc[mask, "entry_price_abs_diff"] = 0.0
        work.loc[mask, "pnl_diff_replay_minus_ledger"] = 0.0
    return work, patched


def attempt_row(
    *,
    attempt: str,
    attempt_type: str,
    metrics: dict[str, Any],
    targeted_repair_targets: str,
    patched_rows: int,
    source_artifacts: str,
    verdict: str,
    why: str,
    next_step: str,
) -> dict[str, Any]:
    blockers = compute_blockers(metrics)
    row_fidelity_exact = not blockers and to_int(metrics.get("actual_rows")) > 0
    return {
        "attempt": attempt,
        "attempt_type": attempt_type,
        "variant": VARIANT,
        "targeted_repair_targets": targeted_repair_targets,
        "patched_or_attempted_rows": patched_rows,
        **metrics,
        "row_fidelity_exact": row_fidelity_exact,
        "promotion_usable_replay": False,
        "blockers": blockers,
        "repair_attempt_verdict": verdict,
        "why": why,
        "next_validation_step": next_step,
        "source_artifacts": source_artifacts,
    }


def build_residual_rows(
    prereq: pd.DataFrame,
    best_attempt: dict[str, Any],
    repair_targets: str,
) -> list[dict[str, Any]]:
    if prereq.empty:
        return []
    repairable_targets = set(split_events(repair_targets))
    best_blockers = str(best_attempt.get("blockers", "") or "")
    patched_rows = to_int(best_attempt.get("patched_or_attempted_rows"))
    residual_rows: list[dict[str, Any]] = []
    for row in prereq.to_dict("records"):
        repair_target = str(row.get("repair_target", "") or "").strip()
        snapshot_status = str(row.get("existing_snapshot_can_repair_independent_replay", "") or "").strip()
        missing_prerequisites = str(row.get("missing_prerequisites", "") or "").strip()
        is_repairable = snapshot_status == "True" and repair_target in repairable_targets
        future_exact_input_required = snapshot_status != "True" or "exact_model_input" in missing_prerequisites
        if is_repairable:
            residual_status = "DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE"
            residual_blockers = unique_join(
                [
                    best_blockers,
                    "diagnostic_patch_not_independent_replay_evidence",
                    "official_execution_replay_clean_clock_gates_still_required",
                ]
            )
            evidence = (
                f"matched-drift patch applied across {patched_rows} row(s); "
                f"entry drift rows after patch {best_attempt.get('entry_price_drift_rows', '')}; "
                f"PnL drift rows after patch {best_attempt.get('pnl_drift_rows', '')}"
            )
        else:
            residual_status = "UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED"
            residual_blockers = unique_join(
                [
                    missing_prerequisites,
                    "target_not_fully_snapshot_repairable",
                    best_blockers,
                    "official_execution_replay_clean_clock_gates_still_required",
                ]
            )
            evidence = str(row.get("why", "") or "")
        residual_rows.append(
            {
                "variant": VARIANT,
                "repair_target": repair_target,
                "root_cause": row.get("root_cause", ""),
                "affected_events": row.get("affected_events", ""),
                "target_rows": row.get("target_rows", ""),
                "existing_snapshot_can_repair_independent_replay": snapshot_status,
                "missing_prerequisites": missing_prerequisites,
                "future_exact_input_required_for_target": future_exact_input_required,
                "current_snapshot_repair_attempted": is_repairable,
                "residual_target_status": residual_status,
                "target_repaired_to_promotion_usable": False,
                "residual_blockers": residual_blockers,
                "residual_evidence": evidence,
                "next_validation_step": row.get("next_validation_step", ""),
            }
        )
    return residual_rows


def build_attempts(
    current_summary: dict[str, Any],
    current_detail: pd.DataFrame,
    selected_summary: dict[str, Any],
    prereq: pd.DataFrame,
    current_dir: Path,
    selected_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    repairable = prereq[
        prereq.get("existing_snapshot_can_repair_independent_replay", pd.Series(dtype=str)).astype(str).eq("True")
    ].copy()
    patch_events: set[str] = set()
    for value in repairable.get("affected_events", pd.Series(dtype=str)).tolist():
        patch_events.update(split_events(value))
    repair_targets = unique_join(repairable.get("repair_target", pd.Series(dtype=str)).tolist())

    patched_detail, patched_rows = patch_matched_drift_detail(current_detail, patch_events)
    patched_metrics = summarize_detail(patched_detail, current_summary)
    # Adjust replay PnL from the patched row values, not from the original summary.
    if patched_rows:
        before = pd.to_numeric(current_detail.get("replay_pnl"), errors="coerce").fillna(0.0)
        after = pd.to_numeric(patched_detail.get("replay_pnl"), errors="coerce").fillna(0.0)
        patched_metrics["replay_pnl"] = round(to_float(current_summary.get("replay_pnl")) + float((after - before).sum()), 12)
        patched_metrics["replay_minus_actual_pnl"] = round(
            patched_metrics["replay_pnl"] - to_float(current_summary.get("actual_official_pnl")),
            12,
        )

    attempts = [
        attempt_row(
            attempt="matched_drift_decision_fill_price_patch_simulation",
            attempt_type="diagnostic_patch_simulation_not_independent_evidence",
            metrics=patched_metrics,
            targeted_repair_targets=repair_targets,
            patched_rows=patched_rows,
            source_artifacts=str(current_dir / "btc1h_replay_vs_ledger_reconciliation_rows.csv"),
            verdict="HELPS_BUT_NOT_PROMOTION_USABLE",
            why=(
                "Patching the two current-snapshot matched drift rows removes entry/PnL drift rows, "
                "but missing, extra, event-replacement, and aggregate PnL blockers remain."
            ),
            next_step=(
                "Keep this as a replay-engineering unit target only; independent replay still needs exact market "
                "selection and skip/fill reproduction before promotion."
            ),
        )
    ]

    if selected_summary:
        selected_metrics = {
            "actual_rows": to_int(selected_summary.get("actual_rows")),
            "replay_rows": to_int(selected_summary.get("replay_rows")),
            "exact_market_side_matches": to_int(selected_summary.get("exact_market_side_matches")),
            "ledger_only_rows": to_int(selected_summary.get("ledger_only_rows")),
            "replay_only_rows": to_int(selected_summary.get("replay_only_rows")),
            "event_replacement_rows": to_int(selected_summary.get("event_replacement_rows")),
            "entry_price_drift_rows": to_int(selected_summary.get("entry_price_drift_rows")),
            "pnl_drift_rows": to_int(selected_summary.get("pnl_drift_rows")),
            "actual_official_pnl": to_float(selected_summary.get("actual_official_pnl")),
            "replay_pnl": to_float(selected_summary.get("replay_pnl")),
            "replay_minus_actual_pnl": to_float(selected_summary.get("replay_minus_actual_pnl")),
        }
        attempts.append(
            attempt_row(
                attempt="strict_selected_scan_selected_market_replay",
                attempt_type="real_independent_replay_mode",
                metrics=selected_metrics,
                targeted_repair_targets="blocked_dedupe_scan_clock;same_event_market_selection",
                patched_rows=to_int(selected_summary.get("replay_rows")),
                source_artifacts=str(selected_dir / "btc1h_replay_vs_ledger_reconciliation_summary.csv"),
                verdict="REGRESSES_ROW_FIDELITY",
                why=(
                    "Restricting to captured selected scans/markets removes event replacements, but it misses more "
                    "actual fills and still leaves entry/PnL drift. It is not the blocked-dedupe repair."
                ),
                next_step=(
                    "Do not rely on selected_scan_only as the repair; inspect exact model input/TTL capture and "
                    "decision-time fill modeling."
                ),
            )
        )

    best = attempts[0] if attempts else {}
    remaining_blockers = str(best.get("blockers", ""))
    residual_rows = build_residual_rows(prereq, best, repair_targets)
    diagnostic_targets = [
        str(row["repair_target"])
        for row in residual_rows
        if row["residual_target_status"] == "DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE"
    ]
    unresolved_targets = [
        str(row["repair_target"])
        for row in residual_rows
        if row["residual_target_status"] == "UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED"
    ]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "attempt_count": len(attempts),
        "repairable_targets_from_prereq": repair_targets,
        "matched_drift_patch_rows": patched_rows,
        "matched_drift_patch_removes_entry_drift": to_int(best.get("entry_price_drift_rows")) == 0 if best else False,
        "strict_selected_scan_attempt_verdict": next(
            (row["repair_attempt_verdict"] for row in attempts if row["attempt"] == "strict_selected_scan_selected_market_replay"),
            "missing",
        ),
        "best_current_snapshot_attempt": best.get("attempt", ""),
        "remaining_blockers_after_best_attempt": remaining_blockers,
        "current_snapshot_repairs_make_replay_promotion_usable": False,
        "near_deployable_after_current_replay_repairs": False,
        "deployable_now": False,
        "residual_target_count": len(residual_rows),
        "diagnostic_patch_applied_target_count": len(diagnostic_targets),
        "unresolved_future_exact_input_target_count": len(unresolved_targets),
        "diagnostic_patch_applied_targets": unique_join(diagnostic_targets),
        "unresolved_future_exact_input_targets": unique_join(unresolved_targets),
        "targets_repaired_to_promotion_usable_count": 0,
        "all_field_ready_repairs_remain_diagnostic_only": True,
        "next_required_evidence": (
            "exact model-input/TTL capture plus independent replay row-for-row market selection, skip/fill sequence, "
            "entry price, and PnL parity"
        ),
    }
    return attempts, residual_rows, summary


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

    current_summary = first(read_csv(args.current_reconciliation_dir / "btc1h_replay_vs_ledger_reconciliation_summary.csv"))
    current_detail = read_csv(args.current_reconciliation_dir / "btc1h_replay_vs_ledger_reconciliation_rows.csv")
    selected_summary = first(
        read_csv(args.selected_scan_reconciliation_dir / "btc1h_replay_vs_ledger_reconciliation_summary.csv")
    )
    prereq = read_csv(args.prerequisite_audit)
    attempts, residual_rows, summary = build_attempts(
        current_summary,
        current_detail,
        selected_summary,
        prereq,
        args.current_reconciliation_dir,
        args.selected_scan_reconciliation_dir,
    )

    attempts_df = pd.DataFrame(attempts)
    residual_df = pd.DataFrame(residual_rows)
    summary_df = pd.DataFrame([summary])
    attempts_df.to_csv(args.out_dir / "btc1h_replay_repair_attempts.csv", index=False)
    residual_df.to_csv(args.out_dir / "btc1h_replay_repair_residual_targets.csv", index=False)
    summary_df.to_csv(args.out_dir / "btc1h_replay_repair_attempt_summary.csv", index=False)
    (args.out_dir / "run_info.json").write_text(
        json.dumps(
            {
                **summary,
                "scope": "btc1h_replay_repair_attempt_diagnostic_only",
                "current_reconciliation_dir": str(args.current_reconciliation_dir),
                "selected_scan_reconciliation_dir": str(args.selected_scan_reconciliation_dir),
                "prerequisite_audit": str(args.prerequisite_audit),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    report = [
        "# BTC1H Replay Repair Attempt Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Variant: `{summary['variant']}`",
        f"Deployable now: `{summary['deployable_now']}`",
        "",
        "## Summary",
        "",
        markdown_table(summary_df),
        "",
        "## Attempts",
        "",
        markdown_table(attempts_df),
        "",
        "## Residual Targets",
        "",
        markdown_table(residual_df),
        "",
        "## Interpretation",
        "",
        "- The matched-drift diagnostic patch removes the two entry/PnL drift rows but does not fix missing or extra replay rows.",
        "- The strict selected-scan/selected-market replay attempt regresses row fidelity and is not the blocked-dedupe repair.",
        "- This artifact is diagnostic only and keeps current BTC1H no-deploy.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
