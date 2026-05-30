#!/usr/bin/env python3
"""Build a BTC1H replay repair feasibility checklist.

This is a diagnostic planning artifact. It separates replay repairs that can be
validated against the current paused snapshot from promotion gates that require
future clean-clock official rows. It does not tune thresholds, start processes,
or relax official-settlement/replay gates.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_replay_repair_feasibility_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--root-cause-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_root_cause_summary.csv",
    )
    p.add_argument(
        "--root-cause-rows",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_root_cause_rows.csv",
    )
    p.add_argument(
        "--replay-mode-comparison",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_mode_comparison.csv",
    )
    p.add_argument(
        "--decision-chain",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_forward_snapshot_signal_audit_latest_codex"
        / "btc1h_selected_signal_decision_chain.csv",
    )
    p.add_argument(
        "--selected-parity-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_selected_signal_model_parity_latest_codex"
        / "btc1h_selected_signal_model_parity_summary.csv",
    )
    p.add_argument(
        "--current-evidence-snapshot",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_next_forward_candidate_packet_latest_codex" / "btc1h_current_evidence_snapshot.csv",
    )
    p.add_argument(
        "--order-decision-baseline-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_order_decision_replay_baseline_latest_codex"
        / "btc1h_order_decision_replay_summary.csv",
    )
    p.add_argument(
        "--order-decision-baseline-reconciliation-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_order_decision_replay_baseline_reconciliation_latest_codex"
        / "btc1h_replay_vs_ledger_reconciliation_summary.csv",
    )
    p.add_argument(
        "--clean-clock-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_clean_evidence_clock_gate_latest_codex" / "btc1h_clean_evidence_clock_summary.csv",
    )
    p.add_argument(
        "--multi-holdout-summary",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_multi_holdout_research_latest_codex" / "btc1h_candidate_gate_summary.csv",
    )
    p.add_argument("--min-clean-official-rows", type=int, default=50)
    p.add_argument("--max-proxy-official-mismatch-rate", type=float, default=0.02)
    return p.parse_args()


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


def first_row(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(key, "")).strip() == expected for key, expected in filters.items()):
            return row
    return rows[0] if rows and not filters else {}


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


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def semicolon_count(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    return len([part for part in text.split(";") if part.strip()])


def parse_root_counts(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    text = str(value or "").strip()
    if not text:
        return counts
    for part in text.split(";"):
        if not part.strip() or "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        if key:
            counts[key] = to_int(raw)
    return counts


def count_root_rows(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        root = str(row.get("root_cause", "")).strip()
        if root:
            counts[root] = counts.get(root, 0) + 1
    return counts


def count_roots(counts: dict[str, int], names: set[str]) -> int:
    return sum(value for key, value in counts.items() if key in names)


def count_decision_actions(rows: list[dict[str, str]], action: str) -> int:
    target = action.lower()
    return sum(1 for row in rows if str(row.get("decision_action", "")).strip().lower() == target)


def count_nonzero_entry_diffs(rows: list[dict[str, str]]) -> int:
    return sum(1 for row in rows if abs(to_float(row.get("entry_price_diff"))) >= 0.009)


def item(
    repair_item: str,
    required_for_promotion: bool,
    can_validate_from_existing_snapshot: bool,
    validation_scope: str,
    existing_evidence_status: str,
    current_blocker_count: int,
    why: str,
    next_validation_step: str,
    source_artifacts: str,
) -> dict[str, Any]:
    return {
        "repair_item": repair_item,
        "required_for_promotion": required_for_promotion,
        "can_validate_from_existing_snapshot": can_validate_from_existing_snapshot,
        "validation_scope": validation_scope,
        "existing_evidence_status": existing_evidence_status,
        "current_blocker_count": current_blocker_count,
        "why": why,
        "next_validation_step": next_validation_step,
        "source_artifacts": source_artifacts,
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    headers = list(rows[0].keys())
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(header, "")).replace("\n", " ") for header in headers) + " |")
    return "\n".join(out)


def build_feasibility(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root_summary_rows = read_csv(args.root_cause_summary)
    root_summary = first_row(root_summary_rows)
    root_rows = read_csv(args.root_cause_rows)
    replay_modes = read_csv(args.replay_mode_comparison)
    decision_chain = read_csv(args.decision_chain)
    parity_summary = first_row(read_csv(args.selected_parity_summary))
    evidence = first_row(read_csv(args.current_evidence_snapshot), variant=VARIANT)
    order_decision_baseline = first_row(read_csv(args.order_decision_baseline_summary), variant=VARIANT)
    order_decision_baseline_recon = first_row(read_csv(args.order_decision_baseline_reconciliation_summary))
    clean = first_row(read_csv(args.clean_clock_summary), variant=VARIANT)
    multi = first_row(read_csv(args.multi_holdout_summary), variant=VARIANT)

    root_counts = parse_root_counts(root_summary.get("root_cause_counts", ""))
    if not root_counts:
        root_counts = count_root_rows(root_rows)
    root_counts_text = ";".join(f"{key}={value}" for key, value in root_counts.items())

    order_count = count_roots(
        root_counts,
        {"reprice_skip_then_fill_sequence_missing", "order_decision_reprice_fill_price_missing"},
    )
    blocked_dedupe_count = count_roots(root_counts, {"blocked_dedupe_or_post_selected_scan_used_as_replay_clock"})
    same_event_count = count_roots(root_counts, {"same_event_market_selection_not_row_faithful"})
    exact_model_input_root_count = count_roots(root_counts, {"scan_time_model_does_not_recreate_captured_signal"})

    replay_mode_count = len(replay_modes)
    replay_promotion_usable_modes = sum(1 for row in replay_modes if to_bool(row.get("promotion_usable_replay", "")))
    replay_mode_blocker_count = max(0, replay_mode_count - replay_promotion_usable_modes)

    selected_signal_rows = to_int(parity_summary.get("selected_signal_rows"))
    parity_fail_rows = to_int(parity_summary.get("parity_fail_rows"))
    captured_ttl_rows = to_int(parity_summary.get("captured_ttl_available_rows"))
    exact_model_input_blockers = max(exact_model_input_root_count, selected_signal_rows - captured_ttl_rows, parity_fail_rows)

    official_rows = to_int(
        evidence.get("forward_official_rows")
        or clean.get("official_rows")
        or multi.get("forward_official_rows")
    )
    official_mismatch_rate = to_float(
        evidence.get("forward_official_mismatch_rate")
        or clean.get("official_proxy_mismatch_rate")
        or multi.get("forward_official_mismatch_rate")
    )
    official_proxy_mismatches = to_int(clean.get("official_proxy_mismatches"))
    proxy_win_official_loss_flips = to_int(evidence.get("basis_proxy_win_official_loss_flips"))
    sample_shortfall = max(0, args.min_clean_official_rows - official_rows)
    blank_policy_rows = to_int(clean.get("blank_policy_official_rows"))
    missing_schema_fields = semicolon_count(clean.get("sidecar_signal_missing_fields")) + semicolon_count(
        clean.get("sidecar_order_decision_missing_fields")
    )

    decision_chain_rows = len(decision_chain)
    decision_fill_rows = count_decision_actions(decision_chain, "paper_fill")
    decision_skip_rows = count_decision_actions(decision_chain, "skip")
    nonzero_entry_diff_rows = count_nonzero_entry_diffs(decision_chain)

    rows = [
        item(
            "order_decision_reprice_fill_model",
            True,
            True,
            "existing_paused_snapshot",
            "BLOCKED_TESTABLE_ON_EXISTING_SNAPSHOT" if order_count else "REQUIRED_RECHECK_AFTER_REPLAY_CHANGE",
            order_count,
            (
                f"{order_count} root-cause rows need order-decision reprice/fill sequencing; "
                f"decision-chain rows={decision_chain_rows}, fills={decision_fill_rows}, "
                f"skips={decision_skip_rows}, nonzero_entry_diff_rows={nonzero_entry_diff_rows}."
            ),
            (
                "Modify replay to consume captured order_decision skip/fill sequence and filled entry price, "
                "then rerun replay-vs-ledger reconciliation for exact market/entry/PnL parity."
            ),
            f"{args.root_cause_rows};{args.decision_chain}",
        ),
        item(
            "blocked_dedupe_filter",
            True,
            True,
            "existing_paused_snapshot",
            "BLOCKED_TESTABLE_ON_EXISTING_SNAPSHOT" if blocked_dedupe_count else "REQUIRED_RECHECK_AFTER_REPLAY_CHANGE",
            blocked_dedupe_count,
            (
                f"{blocked_dedupe_count} root-cause row shows replay trading from a blocked/dedupe or "
                "post-selected scan clock."
            ),
            (
                "Filter candidate-scan replay to eligible selected/candidate rows only, then rerun row-level "
                "reconciliation and verify the matched row no longer drifts by one cent."
            ),
            f"{args.root_cause_rows};{args.decision_chain}",
        ),
        item(
            "same_event_selected_market_dedupe",
            True,
            True,
            "partial_existing_snapshot",
            "PARTIAL_EXISTING_SNAPSHOT_REPLAY_FIX_NEEDED" if same_event_count else "REQUIRED_RECHECK_AFTER_REPLAY_CHANGE",
            same_event_count,
            (
                f"{same_event_count} root-cause rows replace the captured live market with another market in "
                "the same event; existing snapshot can test selected-market/dedupe semantics, but a replay code "
                "change is still needed."
            ),
            (
                "Make same-event dedupe honor captured selected_market/order_decision state and rerun all replay "
                "modes; no event-level market replacements can remain."
            ),
            f"{args.root_cause_rows};{args.replay_mode_comparison}",
        ),
        item(
            "row_for_row_reconciliation_after_repairs",
            True,
            True,
            "existing_paused_snapshot",
            "NO_PROMOTION_USABLE_REPLAY_MODE" if replay_promotion_usable_modes == 0 else "HAS_PROMOTION_USABLE_REPLAY_MODE",
            replay_mode_blocker_count,
            (
                f"Promotion-usable replay modes={replay_promotion_usable_modes}/{replay_mode_count}; every current "
                "mode still fails row-for-row market, entry, or PnL parity."
            ),
            (
                "After replay repairs, rerun the root-cause and replay-vs-ledger reconciliation stack; candidate "
                "cannot advance unless at least one mode is promotion usable and row parity is exact."
            ),
            str(args.replay_mode_comparison),
        ),
        item(
            "exact_model_input_capture",
            True,
            False,
            "future_clean_clock_rows",
            "FUTURE_CLEAN_CLOCK_REQUIRED",
            exact_model_input_blockers,
            (
                f"Old selected rows do not have captured exact model inputs for promotion: selected_signal_rows="
                f"{selected_signal_rows}, captured_ttl_available_rows={captured_ttl_rows}, "
                f"parity_fail_rows={parity_fail_rows}."
            ),
            (
                "Only count future rows with captured model_ttl_policy, model_policy_version, candle/TTL inputs, "
                "and exact model-input replay parity."
            ),
            f"{args.selected_parity_summary};{args.clean_clock_summary}",
        ),
        item(
            "official_proxy_basis_gate",
            True,
            False,
            "future_official_rows",
            (
                "CURRENT_SAMPLE_FAILS_FUTURE_ROWS_REQUIRED"
                if official_mismatch_rate > args.max_proxy_official_mismatch_rate or proxy_win_official_loss_flips
                else "CURRENT_SAMPLE_TOO_SMALL_RECHECK_WITH_FUTURE_ROWS"
            ),
            max(official_proxy_mismatches, proxy_win_official_loss_flips),
            (
                f"Current official rows={official_rows}, official/proxy mismatch rate={official_mismatch_rate:.4f}, "
                f"max allowed={args.max_proxy_official_mismatch_rate:.4f}, proxy-win official-loss flips="
                f"{proxy_win_official_loss_flips}."
            ),
            (
                "Collect future official-settled clean-clock rows and keep mismatch rate <= threshold with zero "
                "proxy-win/official-loss flips before promotion discussion."
            ),
            f"{args.current_evidence_snapshot};{args.clean_clock_summary}",
        ),
        item(
            "sample_size_gate",
            True,
            False,
            "future_official_rows",
            "FUTURE_ROWS_REQUIRED",
            sample_shortfall,
            (
                f"Current official rows={official_rows}, but they are not clean-clock rows; promotion floor is "
                f"{args.min_clean_official_rows}, leaving shortfall={sample_shortfall}."
            ),
            "Collect at least the minimum clean official rows after an explicitly authorized clean evidence clock.",
            f"{args.current_evidence_snapshot};{args.clean_clock_summary}",
        ),
        item(
            "clean_policy_identity",
            True,
            False,
            "future_clean_clock_rows",
            "BLOCKED_CONTROLLED_RESTART_REQUIRED",
            blank_policy_rows + missing_schema_fields,
            (
                f"Clean-clock status={clean.get('gate_status', '')}; clean_clock_ready="
                f"{clean.get('clean_evidence_clock_ready', '')}; blank_policy_official_rows={blank_policy_rows}; "
                f"missing_sidecar_schema_fields={missing_schema_fields}."
            ),
            (
                "After explicit authorization, start a clean evidence clock with nonblank policy identity and "
                "required sidecar schema fields; do not pool old cached-TTL rows."
            ),
            str(args.clean_clock_summary),
        ),
    ]

    future_required = [
        row["repair_item"]
        for row in rows
        if row["required_for_promotion"] and not to_bool(row["can_validate_from_existing_snapshot"])
    ]
    existing_snapshot_testable = [
        row["repair_item"]
        for row in rows
        if row["required_for_promotion"] and to_bool(row["can_validate_from_existing_snapshot"])
    ]
    next_targets = [
        row["repair_item"]
        for row in rows
        if row["repair_item"]
        in {
            "order_decision_reprice_fill_model",
            "blocked_dedupe_filter",
            "same_event_selected_market_dedupe",
        }
        and to_int(row["current_blocker_count"]) > 0
    ]
    if not next_targets:
        next_targets = ["row_for_row_reconciliation_after_repairs"]

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "deployable_now": False,
        "near_deployable_after_current_replay_repairs": False,
        "replay_repair_can_make_deployable_now": False,
        "no_repair_item_makes_candidate_deployable_now": True,
        "repair_items": len(rows),
        "required_items": sum(1 for row in rows if row["required_for_promotion"]),
        "existing_snapshot_testable_items": ";".join(existing_snapshot_testable),
        "future_rows_required_items": ";".join(future_required),
        "next_replay_engineering_target": ";".join(next_targets),
        "root_cause_counts": root_counts_text,
        "replay_promotion_usable_modes": replay_promotion_usable_modes,
        "replay_modes_compared": replay_mode_count,
        "official_rows": official_rows,
        "min_clean_official_rows": args.min_clean_official_rows,
        "official_proxy_mismatch_rate": official_mismatch_rate,
        "max_proxy_official_mismatch_rate": args.max_proxy_official_mismatch_rate,
        "clean_clock_status": clean.get("gate_status", ""),
        "clean_clock_ready": clean.get("clean_evidence_clock_ready", ""),
        "order_decision_baseline_rows": order_decision_baseline.get("baseline_rows", ""),
        "order_decision_baseline_row_fidelity_exact": order_decision_baseline_recon.get("row_fidelity_exact", ""),
        "order_decision_baseline_promotion_usable_replay": order_decision_baseline_recon.get(
            "promotion_usable_replay", ""
        ),
        "order_decision_baseline_blockers": order_decision_baseline_recon.get("blockers", ""),
        "recommendation": (
            "repair order-decision/reprice plus blocked-dedupe/same-event replay semantics on the paused snapshot; "
            "then rerun row reconciliation, but keep deployment blocked until future clean official rows pass."
        ),
    }
    return rows, summary


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = build_feasibility(args)

    write_csv(args.out_dir / "btc1h_replay_repair_feasibility.csv", rows)
    write_csv(args.out_dir / "btc1h_replay_repair_feasibility_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(
        json.dumps(
            {
                "created_at_utc": summary["created_at_utc"],
                "scope": "btc1h_replay_repair_feasibility_diagnostic_only",
                "out_dir": str(args.out_dir),
                "deployable_now": False,
                "replay_repair_can_make_deployable_now": False,
                "next_replay_engineering_target": summary["next_replay_engineering_target"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    report = [
        "# BTC1H Replay Repair Feasibility",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table([summary]),
        "",
        "## Repair Checklist",
        "",
        markdown_table(rows),
        "",
        "## Interpretation",
        "",
        "- This artifact is diagnostic only and cannot make the BTC1H candidate deployable.",
        "- The next replay engineering target is order-decision/reprice modeling plus blocked-dedupe and same-event selected-market semantics on the paused snapshot.",
        "- Exact model-input capture, official/proxy basis stability, clean policy identity, and sample size still require future clean-clock rows.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
