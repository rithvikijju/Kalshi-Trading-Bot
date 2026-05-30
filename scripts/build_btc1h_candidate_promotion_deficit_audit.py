#!/usr/bin/env python3
"""Quantify BTC1H candidate deficits to deployable or near-deployable status.

The objective audit says which gates are blocked.  This script converts the
same evidence into candidate-level numeric deficits so "promising research"
cannot be mistaken for "close enough to deploy" under the original gates.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_candidate_promotion_deficit_latest_codex"
ACTIVE_VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--candidate-status",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_objective_completion_audit_latest_codex"
        / "btc1h_candidate_objective_status.csv",
    )
    parser.add_argument(
        "--objective-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_objective_completion_audit_latest_codex" / "btc1h_objective_summary.csv",
    )
    parser.add_argument(
        "--promotion-gap-matrix",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_promotion_gap_matrix_latest_codex" / "btc1h_promotion_gap_matrix.csv",
    )
    parser.add_argument(
        "--snapshot-execution-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_snapshot_execution_realism_latest_codex"
        / "btc1h_snapshot_execution_realism_summary.csv",
    )
    parser.add_argument(
        "--faithful-replay-data-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_faithful_replay_data_contract_latest_codex"
        / "btc1h_faithful_replay_data_contract_summary.csv",
    )
    parser.add_argument("--min-clean-official-rows", type=int, default=50)
    parser.add_argument("--max-proxy-official-mismatch-rate", type=float, default=0.02)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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


def first(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(key, "")).strip() == value for key, value in filters.items()):
            return row
    return {}


def semi_join(parts: list[str]) -> str:
    clean = [part for part in (str(part).strip() for part in parts) if part]
    return ";".join(dict.fromkeys(clean))


def number_after(text: str, label: str, default: float = 0.0) -> float:
    match = re.search(re.escape(label) + r"\s+(-?\d+(?:\.\d+)?)", text)
    if not match:
        return default
    return to_float(match.group(1), default)


def gate(rows: list[dict[str, str]], gate_id: str) -> dict[str, str]:
    return first(rows, gate_id=gate_id)


def mismatch_status(*, forward_rows: int, clean_rows: int, mismatch_rate: float, max_rate: float) -> str:
    if clean_rows <= 0:
        return "BLOCKED_NO_CLEAN_OFFICIAL_ROWS"
    if forward_rows <= 0:
        return "BLOCKED_NO_FORWARD_OFFICIAL_ROWS"
    if mismatch_rate > max_rate:
        return "BLOCKED_RATE_EXCEEDS_MAX"
    return "PASS_RATE_ONLY"


def build_candidate_rows(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = read_csv(args.candidate_status)
    summary_rows = read_csv(args.objective_summary)
    objective_summary = summary_rows[0] if summary_rows else {}
    gap_rows = read_csv(args.promotion_gap_matrix)
    snapshot_rows = read_csv(args.snapshot_execution_summary)
    snapshot = snapshot_rows[0] if snapshot_rows else {}
    data_rows = read_csv(args.faithful_replay_data_contract_summary)
    data_contract = data_rows[0] if data_rows else {}

    clean_sample = gate(gap_rows, "clean_forward_sample_size")
    execution_fields = gate(gap_rows, "execution_realism_fields")
    policy_identity = gate(gap_rows, "policy_identity_fields")
    row_for_row = gate(gap_rows, "row_for_row_replay")

    clean_evidence = clean_sample.get("current_evidence", "")
    clean_rows = to_int(number_after(clean_evidence, "clean post-restart official rows"))
    stale_official_rows = to_int(number_after(clean_evidence, "current stale official rows"))
    execution_complete_rate = number_after(execution_fields.get("current_evidence", ""), "field complete rate")
    expected_policy_rows = to_int(number_after(policy_identity.get("current_evidence", ""), "expected-policy official rows"))
    blank_policy_rows = to_int(number_after(policy_identity.get("current_evidence", ""), "blank policy rows"))

    replay_promotion_usable_modes = to_int(objective_summary.get("replay_promotion_usable_modes", ""))
    replay_modes_compared = to_int(objective_summary.get("replay_modes_compared", ""))
    missing_required_fields = to_int(data_contract.get("missing_required_field_count", ""))
    target_unique_missing_fields = to_int(data_contract.get("root_cause_target_unique_missing_required_field_count", ""))
    artifacts_support_faithful_replay = to_bool(data_contract.get("current_artifacts_can_support_faithful_replay", ""))

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        variant = candidate.get("variant", "")
        forward_rows = to_int(candidate.get("forward_official_rows", ""))
        mismatch_rate = to_float(candidate.get("forward_proxy_mismatch_rate", ""))
        replay_exact_rate = to_float(candidate.get("replay_ledger_exact_match_rate", ""))
        near_rows = to_int(candidate.get("near_deployable_countable_rows", ""))
        deploy_rows = to_int(candidate.get("deployable_countable_rows", ""))
        promotion_countable = to_bool(candidate.get("promotion_countable_available_data", ""))
        replay_usable = to_bool(candidate.get("replay_ledger_promotion_usable", ""))

        clean_row_deficit = max(0, args.min_clean_official_rows - clean_rows)
        policy_row_deficit = max(0, args.min_clean_official_rows - expected_policy_rows)
        mismatch_excess = max(0.0, mismatch_rate - args.max_proxy_official_mismatch_rate)
        execution_field_deficit = max(0.0, 1.0 - execution_complete_rate)
        replay_exact_deficit = max(0.0, 1.0 - replay_exact_rate)
        near_countable_deficit = max(0, args.min_clean_official_rows - near_rows)

        blockers = []
        if not promotion_countable:
            blockers.append("no_promotion_countable_available_data")
        if clean_row_deficit:
            blockers.append("too_few_clean_official_rows")
        if forward_rows <= 0:
            blockers.append("no_forward_official_rows")
        elif mismatch_excess:
            blockers.append("official_proxy_mismatch_rate_above_limit")
        if execution_field_deficit:
            blockers.append("execution_realism_fields_incomplete")
        if not replay_usable or replay_exact_deficit:
            blockers.append("row_for_row_replay_not_promotion_usable")
        if missing_required_fields or not artifacts_support_faithful_replay:
            blockers.append("faithful_replay_capture_fields_missing")
        if policy_row_deficit:
            blockers.append("clean_policy_identity_rows_missing")

        can_current_artifacts_make_near_deployable = (
            promotion_countable
            and clean_row_deficit == 0
            and mismatch_excess == 0
            and forward_rows > 0
            and execution_field_deficit == 0
            and replay_usable
            and replay_exact_deficit == 0
            and missing_required_fields == 0
            and artifacts_support_faithful_replay
            and policy_row_deficit == 0
            and near_rows >= args.min_clean_official_rows
        )

        rows.append(
            {
                "variant": variant,
                "objective_candidate_status": candidate.get("objective_candidate_status", ""),
                "candidate_research_class": candidate.get("candidate_research_class", ""),
                "can_current_artifacts_make_near_deployable": can_current_artifacts_make_near_deployable,
                "promotion_countable_available_data": promotion_countable,
                "near_deployable_countable_rows": near_rows,
                "deployable_countable_rows": deploy_rows,
                "near_deployable_countable_row_deficit": near_countable_deficit,
                "min_clean_official_rows": args.min_clean_official_rows,
                "current_clean_post_restart_official_rows": clean_rows,
                "clean_official_row_deficit": clean_row_deficit,
                "current_stale_official_rows": stale_official_rows,
                "candidate_forward_official_rows": forward_rows,
                "candidate_forward_official_pnl": candidate.get("forward_official_pnl", ""),
                "max_proxy_official_mismatch_rate": args.max_proxy_official_mismatch_rate,
                "candidate_forward_proxy_mismatch_rate": mismatch_rate,
                "proxy_official_mismatch_rate_excess": round(mismatch_excess, 6),
                "proxy_official_mismatch_status": mismatch_status(
                    forward_rows=forward_rows,
                    clean_rows=clean_rows,
                    mismatch_rate=mismatch_rate,
                    max_rate=args.max_proxy_official_mismatch_rate,
                ),
                "execution_field_complete_rate": execution_complete_rate,
                "execution_field_complete_rate_deficit": round(execution_field_deficit, 6),
                "snapshot_execution_audit_status": snapshot.get("audit_status", ""),
                "snapshot_execution_required_field_complete_rate": snapshot.get("required_field_complete_rate", ""),
                "replay_ledger_promotion_usable": replay_usable,
                "replay_ledger_exact_match_rate": replay_exact_rate,
                "replay_exact_match_rate_deficit": round(replay_exact_deficit, 6),
                "replay_promotion_usable_modes": replay_promotion_usable_modes,
                "replay_modes_compared": replay_modes_compared,
                "row_for_row_replay_evidence": row_for_row.get("current_evidence", ""),
                "faithful_replay_missing_required_field_count": missing_required_fields,
                "faithful_replay_target_unique_missing_required_field_count": target_unique_missing_fields,
                "faithful_replay_current_artifacts_can_support": artifacts_support_faithful_replay,
                "blank_policy_rows": blank_policy_rows,
                "expected_policy_official_rows": expected_policy_rows,
                "clean_policy_identity_row_deficit": policy_row_deficit,
                "promotion_deficit_blockers": semi_join(blockers),
                "next_evidence_to_reconsider": candidate.get("next_evidence_to_reconsider", ""),
            }
        )

    active = next((row for row in rows if row["variant"] == ACTIVE_VARIANT), rows[0] if rows else {})
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(rows),
        "objective_complete": to_bool(objective_summary.get("objective_complete", "")),
        "current_verdict": objective_summary.get("current_verdict", ""),
        "deployable_candidates": to_int(objective_summary.get("deployable_candidates", "")),
        "near_deployable_candidates": to_int(objective_summary.get("near_deployable_candidates", "")),
        "promising_research_candidates": to_int(objective_summary.get("promising_research_candidates", "")),
        "candidates_current_artifacts_can_make_near_deployable": sum(
            1 for row in rows if to_bool(row["can_current_artifacts_make_near_deployable"])
        ),
        "candidates_with_no_promotion_countable_data": sum(
            1 for row in rows if not to_bool(row["promotion_countable_available_data"])
        ),
        "min_clean_official_rows": args.min_clean_official_rows,
        "current_clean_post_restart_official_rows": clean_rows,
        "clean_official_row_deficit": max(0, args.min_clean_official_rows - clean_rows),
        "max_proxy_official_mismatch_rate": args.max_proxy_official_mismatch_rate,
        "active_variant": ACTIVE_VARIANT,
        "active_clean_official_row_deficit": active.get("clean_official_row_deficit", ""),
        "active_proxy_official_mismatch_rate_excess": active.get("proxy_official_mismatch_rate_excess", ""),
        "active_replay_exact_match_rate_deficit": active.get("replay_exact_match_rate_deficit", ""),
        "active_execution_field_complete_rate_deficit": active.get("execution_field_complete_rate_deficit", ""),
        "faithful_replay_missing_required_field_count": missing_required_fields,
        "faithful_replay_target_unique_missing_required_field_count": target_unique_missing_fields,
        "faithful_replay_current_artifacts_can_support": artifacts_support_faithful_replay,
        "critical_blocked_requirements": objective_summary.get("critical_blocked_requirements", ""),
        "no_process_action_taken": True,
    }
    return rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    columns = [
        "variant",
        "candidate_research_class",
        "can_current_artifacts_make_near_deployable",
        "clean_official_row_deficit",
        "candidate_forward_proxy_mismatch_rate",
        "proxy_official_mismatch_rate_excess",
        "execution_field_complete_rate_deficit",
        "replay_exact_match_rate_deficit",
        "faithful_replay_missing_required_field_count",
        "promotion_deficit_blockers",
    ]
    return "\n".join(
        [
            "# BTC1H Candidate Promotion Deficit Audit",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Current verdict: `{summary['current_verdict']}`",
            f"Deployable candidates: `{summary['deployable_candidates']}`",
            f"Near-deployable candidates: `{summary['near_deployable_candidates']}`",
            f"Candidates current artifacts can make near-deployable: `{summary['candidates_current_artifacts_can_make_near_deployable']}`",
            f"Clean official row deficit: `{summary['clean_official_row_deficit']}`",
            f"Active proxy mismatch excess: `{summary['active_proxy_official_mismatch_rate_excess']}`",
            f"Active replay exact-match deficit: `{summary['active_replay_exact_match_rate_deficit']}`",
            "",
            markdown_table(rows, columns),
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
    rows, summary = build_candidate_rows(args)
    write_csv(args.out_dir / "btc1h_candidate_promotion_deficits.csv", rows)
    write_csv(args.out_dir / "btc1h_candidate_promotion_deficit_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
