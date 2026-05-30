#!/usr/bin/env python3
"""Audit BTC1H replay repair prerequisites.

The repair target matrix says which row-fidelity problems remain. This
diagnostic asks the stricter follow-up: can each target be repaired in an
independent counterfactual replay using only the current paused snapshot, or
does it require future exact model-input/TTL capture?

It is diagnostic only. It does not tune thresholds, start/restart processes,
or turn captured order-decision logs into promotion evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_replay_repair_prerequisite_audit_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--repair-target-matrix",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_target_matrix_latest_codex"
        / "btc1h_replay_repair_target_matrix.csv",
    )
    parser.add_argument(
        "--root-cause-rows",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_root_cause_rows.csv",
    )
    parser.add_argument(
        "--selected-parity",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_selected_signal_model_parity_latest_codex"
        / "btc1h_selected_signal_model_parity.csv",
    )
    parser.add_argument(
        "--decision-chain",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_forward_snapshot_signal_audit_latest_codex"
        / "btc1h_selected_signal_decision_chain.csv",
    )
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


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return ";".join(out)


def max_numeric(rows: list[dict[str, str]], field: str) -> str:
    values = [to_float(row.get(field), math.nan) for row in rows if str(row.get(field, "")).strip() != ""]
    values = [value for value in values if math.isfinite(value)]
    if not values:
        return ""
    return f"{max(values):.12g}"


def sum_numeric(rows: list[dict[str, str]], field: str) -> float:
    return sum(to_float(row.get(field), 0.0) for row in rows)


def split_join_key(event_ticker: str, join_key: str) -> tuple[str, str, str] | None:
    text = str(join_key or "").strip()
    event = str(event_ticker or "").strip()
    if not text or "|" not in text:
        return None
    market, side = text.rsplit("|", 1)
    market = market.strip()
    side = side.strip().lower()
    if not event or not market or not side:
        return None
    return (event, market, side)


def row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        str(row.get("event_ticker", "")).strip(),
        str(row.get("market_ticker", "")).strip(),
        str(row.get("side", "")).strip().lower(),
    )


def target_keys(rows: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    for row in rows:
        event = str(row.get("event_ticker", "")).strip()
        for field in ("ledger_join_key", "replay_join_key"):
            key = split_join_key(event, row.get(field, ""))
            if key and key not in keys:
                keys.append(key)
    return keys


def matching_rows(rows: list[dict[str, str]], keys: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    key_set = set(keys)
    return [row for row in rows if row_key(row) in key_set]


def action_count(rows: list[dict[str, str]], action: str) -> int:
    target = action.strip().lower()
    return sum(1 for row in rows if str(row.get("decision_action", "")).strip().lower() == target)


def status_set(rows: list[dict[str, str]]) -> str:
    statuses: list[str] = []
    for row in rows:
        root_statuses = str(row.get("parity_statuses", "")).strip()
        if root_statuses:
            for status in root_statuses.split(";"):
                if status.strip():
                    statuses.append(status.strip())
        parity_status = str(row.get("parity_status", "")).strip()
        if parity_status:
            statuses.append(parity_status)
    return unique_join(statuses)


def classify_target(repair_target: str, stats: dict[str, Any]) -> tuple[str, str, str, str]:
    signal_rows = to_float(stats.get("scan_ttl_recomputed_signal_rows"))
    no_signal_rows = to_float(stats.get("scan_ttl_no_signal_rows"))
    max_model_diff = to_float(stats.get("max_model_p_yes_abs_diff"))
    max_edge_diff = to_float(stats.get("max_net_edge_cents_abs_diff"))
    chain_rows = to_int(stats.get("selected_chain_rows"))
    fill_rows = to_int(stats.get("selected_chain_fill_rows"))
    skip_rows = to_int(stats.get("selected_chain_skip_rows"))
    max_decision_entry_diff = to_float(stats.get("max_decision_entry_abs_diff"))
    max_replay_minus_selected = abs(to_float(stats.get("max_replay_minus_selected_sec")))

    if repair_target == "skip_then_fill_sequence":
        if no_signal_rows > 0 and signal_rows == 0:
            return (
                "False",
                "exact_model_input_capture_or_order_decision_sequence_model",
                (
                    "Captured decision-chain rows show the skip/fill sequence, but scan-TTL parity has no "
                    "recomputed signal for the target fill market. The snapshot can diagnose the sequence; it "
                    "cannot make an independent replay recreate that signal."
                ),
                "Capture exact model inputs/TTL policy on future rows, then rerun independent replay row-fidelity.",
            )
        return (
            "Partial",
            "row_for_row_sequence_replay_validation",
            "The decision chain is present, but independent replay still needs row-for-row validation after sequencing changes.",
            "Implement sequencing in replay and rerun reconciliation before treating this target as repaired.",
        )

    if repair_target == "same_event_market_selection":
        missing = ["exact_model_input_or_selected_market_policy_capture"]
        if chain_rows <= 0:
            missing.append("captured_selected_market_decision_chain")
        if max_model_diff > 0 or max_edge_diff > 0:
            missing.append("model_value_parity")
        return (
            "Partial",
            unique_join(missing),
            (
                "The captured selected market exists, but independent recomputation still has model/edge drift "
                "and the replay-only replacement market has no captured selected-chain row. Event-level dedupe can "
                "be tested, but exact market choice is not fully repairable from this snapshot."
            ),
            "Treat event-level market-selection work as diagnostic until exact model-input capture proves row-fidelity.",
        )

    if repair_target == "order_decision_reprice_fill_price":
        if fill_rows > 0 and max_decision_entry_diff >= 0.009:
            return (
                "True",
                "",
                (
                    "Market/side already match and the selected-to-decision chain contains the executable fill "
                    "price that explains the one-cent entry/PnL drift."
                ),
                "Make replay PnL use the decision/fill price, then rerun reconciliation and root-cause audit.",
            )
        return (
            "Partial",
            "decision_fill_price_drift_row",
            "The target is a price repair, but the current inputs do not expose a clear decision-fill price drift row.",
            "Inspect the decision chain before changing replay price logic.",
        )

    if repair_target == "blocked_dedupe_scan_clock":
        if fill_rows > 0 and max_replay_minus_selected > 0:
            return (
                "True",
                "",
                (
                    "The selected-chain fill row exists and the replay clock differs from selected timing, so the "
                    "current snapshot can test a blocked/dedupe scan-clock filter on the matched drift row."
                ),
                "Filter blocked/dedupe scan clocks in independent replay, then require exact entry/PnL parity.",
            )
        if fill_rows > 0 or skip_rows > 0:
            return (
                "Partial",
                "replay_scan_clock_delta",
                "Decision-chain rows exist, but the replay-vs-selected clock delta is not explicit enough to prove the repair.",
                "Add scan-clock diagnostics before changing replay eligibility.",
            )
        return (
            "False",
            "captured_selected_chain_fill_row",
            "No selected-chain row is available for the suspected blocked/dedupe clock target.",
            "Capture selected-chain/fill timing before trying to repair this target.",
        )

    return (
        "Partial",
        "unclassified_repair_target_prerequisites",
        "This repair target is not classified by the prerequisite audit.",
        "Classify the target before treating it as replay-repairable.",
    )


def build_audit(
    target_rows: list[dict[str, str]],
    root_rows: list[dict[str, str]],
    parity_rows: list[dict[str, str]],
    decision_chain: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root_by_cause: dict[str, list[dict[str, str]]] = {}
    for row in root_rows:
        root = str(row.get("root_cause", "")).strip()
        if root:
            root_by_cause.setdefault(root, []).append(row)

    audit_rows: list[dict[str, Any]] = []
    for target in target_rows:
        root_cause = str(target.get("root_cause", "")).strip()
        repair_target = str(target.get("repair_target", root_cause)).strip()
        rows = root_by_cause.get(root_cause, [])
        keys = target_keys(rows)
        parity_matches = matching_rows(parity_rows, keys)
        chain_matches = matching_rows(decision_chain, keys)
        combined_status_rows = rows + parity_matches
        stats: dict[str, Any] = {
            "repair_target": repair_target,
            "root_cause": root_cause,
            "affected_events": unique_join([row.get("event_ticker", "") for row in rows]),
            "target_rows": to_int(target.get("current_root_cause_rows"), len(rows)) or len(rows),
            "target_keys": unique_join([f"{event}|{market}|{side}" for event, market, side in keys]),
            "selected_chain_rows": len(chain_matches),
            "selected_chain_fill_rows": action_count(chain_matches, "paper_fill"),
            "selected_chain_skip_rows": action_count(chain_matches, "skip"),
            "parity_rows": len(parity_matches),
            "parity_statuses": status_set(combined_status_rows),
            "scan_ttl_recomputed_signal_rows": f"{sum_numeric(rows, 'scan_ttl_recomputed_signal_rows'):.12g}",
            "scan_ttl_no_signal_rows": f"{sum_numeric(rows, 'scan_ttl_no_signal_rows'):.12g}",
            "implied_ttl_pass_rows": f"{sum_numeric(rows, 'implied_ttl_pass_rows'):.12g}",
            "max_model_p_yes_abs_diff": max_numeric(rows, "max_model_p_yes_abs_diff"),
            "max_net_edge_cents_abs_diff": max_numeric(rows, "max_net_edge_cents_abs_diff"),
            "max_top_entry_price_abs_diff": max_numeric(parity_matches, "top_entry_price_abs_diff"),
            "max_decision_entry_abs_diff": max_numeric(rows + chain_matches, "max_decision_entry_abs_diff")
            or max_numeric(chain_matches, "entry_price_diff"),
            "max_replay_minus_selected_sec": max_numeric(rows, "replay_minus_selected_sec"),
            "max_quote_age_ms": max_numeric(rows, "max_quote_age_ms"),
        }
        can_repair, missing, why, next_step = classify_target(repair_target, stats)
        stats.update(
            {
                "existing_snapshot_can_repair_independent_replay": can_repair,
                "missing_prerequisites": missing,
                "why": why,
                "next_validation_step": next_step,
            }
        )
        audit_rows.append(stats)

    true_targets = [row["repair_target"] for row in audit_rows if row["existing_snapshot_can_repair_independent_replay"] == "True"]
    partial_targets = [
        row["repair_target"] for row in audit_rows if row["existing_snapshot_can_repair_independent_replay"] == "Partial"
    ]
    false_targets = [
        row["repair_target"] for row in audit_rows if row["existing_snapshot_can_repair_independent_replay"] == "False"
    ]
    true_events = unique_join(
        [
            event
            for row in audit_rows
            if row["existing_snapshot_can_repair_independent_replay"] == "True"
            for event in str(row.get("affected_events", "")).split(";")
        ]
    )
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "repair_target_count": len(audit_rows),
        "existing_snapshot_true_target_count": len(true_targets),
        "existing_snapshot_partial_target_count": len(partial_targets),
        "existing_snapshot_false_target_count": len(false_targets),
        "existing_snapshot_can_repair_targets": ";".join(true_targets),
        "existing_snapshot_partial_targets": ";".join(partial_targets),
        "existing_snapshot_false_targets": ";".join(false_targets),
        "matched_drift_repair_events": true_events,
        "matched_drift_repair_event_count": len([event for event in true_events.split(";") if event]),
        "future_exact_input_blocked_targets": unique_join(partial_targets + false_targets),
        "all_targets_repairable_from_existing_snapshot": len(audit_rows) > 0 and not partial_targets and not false_targets,
        "target_matrix_overstates_pure_repairability_without_this_audit": bool(partial_targets or false_targets),
        "current_snapshot_independent_repair_complete": False,
        "near_deployable_after_current_replay_repairs": False,
        "deployable_now": False,
        "no_deploy_reason": (
            "Two matched-drift rows can test replay engineering, but same-event selection and skip-then-fill "
            "still need exact model-input/TTL capture or future clean-clock evidence."
        ),
    }
    return audit_rows, summary


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    headers = list(rows[0].keys())
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append(
            "| "
            + " | ".join(str(row.get(header, "")).replace("\n", " ").replace("|", "\\|") for header in headers)
            + " |"
        )
    return "\n".join(out)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    target_rows = read_csv(args.repair_target_matrix)
    root_rows = read_csv(args.root_cause_rows)
    parity_rows = read_csv(args.selected_parity)
    decision_chain = read_csv(args.decision_chain)
    audit_rows, summary = build_audit(target_rows, root_rows, parity_rows, decision_chain)

    write_csv(args.out_dir / "btc1h_replay_repair_prerequisite_audit.csv", audit_rows)
    write_csv(args.out_dir / "btc1h_replay_repair_prerequisite_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(
        json.dumps(
            {
                **summary,
                "scope": "btc1h_replay_repair_prerequisite_audit_diagnostic_only",
                "repair_target_matrix": str(args.repair_target_matrix),
                "root_cause_rows": str(args.root_cause_rows),
                "selected_parity": str(args.selected_parity),
                "decision_chain": str(args.decision_chain),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    report = [
        "# BTC1H Replay Repair Prerequisite Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Variant: `{summary['variant']}`",
        f"Deployable now: `{summary['deployable_now']}`",
        "",
        "## Summary",
        "",
        markdown_table([summary]),
        "",
        "## Repair Prerequisites",
        "",
        markdown_table(audit_rows),
        "",
        "## Interpretation",
        "",
        "- Existing paused-snapshot rows can test entry-price and scan-clock fixes on the matched drift events.",
        "- Same-event market selection is only partially repairable because exact model/edge parity still drifts.",
        "- Skip-then-fill sequencing is not independently repairable from the current snapshot because scan-TTL parity has no recreated signal for the target fill market.",
        "- This reinforces no-deploy: captured order-decision parity is diagnostic, not independent counterfactual promotion evidence.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
