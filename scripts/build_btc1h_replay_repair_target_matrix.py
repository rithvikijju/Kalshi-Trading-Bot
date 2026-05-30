#!/usr/bin/env python3
"""Build a BTC1H independent replay repair target matrix.

This diagnostic compares the current independent counterfactual replay
reconciliation against the captured order-decision baseline and turns the
root-cause audit into measurable repair targets. It does not tune thresholds,
start/restart processes, or treat captured-decision rows as promotion evidence.
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
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_replay_repair_target_matrix_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"

ROOT_TARGETS = {
    "reprice_skip_then_fill_sequence_missing": {
        "repair_target": "skip_then_fill_sequence",
        "blockers": "missing_actual_rows;replay_row_count_differs;pnl_not_row_for_row_equal",
        "why": "Actual captured decision chain skipped after a failed websocket reprice and then filled the same market; independent replay missed the later fill.",
        "success": "The missing ledger-only fill appears in independent replay with exact market, side, entry, and PnL parity.",
    },
    "same_event_market_selection_not_row_faithful": {
        "repair_target": "same_event_market_selection",
        "blockers": "missing_actual_rows;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal",
        "why": "Independent replay chose a different same-event market than the captured live selected/order-decision state.",
        "success": "No event-level market replacement rows remain in replay-vs-ledger reconciliation.",
    },
    "order_decision_reprice_fill_price_missing": {
        "repair_target": "order_decision_reprice_fill_price",
        "blockers": "entry_price_not_row_for_row_equal;pnl_not_row_for_row_equal",
        "why": "Market and side matched, but independent replay used a one-cent different entry than the captured fill.",
        "success": "Matched market/side rows use the captured executable fill price and have zero entry/PnL drift.",
    },
    "blocked_dedupe_or_post_selected_scan_used_as_replay_clock": {
        "repair_target": "blocked_dedupe_scan_clock",
        "blockers": "entry_price_not_row_for_row_equal;pnl_not_row_for_row_equal",
        "why": "Independent replay appears to trade from a blocked/dedupe or post-selected scan clock instead of the captured selected/fill timing.",
        "success": "Candidate-scan replay does not emit trades from blocked/dedupe scan clocks; matched rows have exact entry/PnL parity.",
    },
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--current-replay-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_vs_ledger_reconciliation_latest_codex"
        / "btc1h_replay_vs_ledger_reconciliation_summary.csv",
    )
    p.add_argument(
        "--baseline-reconciliation-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_order_decision_replay_baseline_reconciliation_latest_codex"
        / "btc1h_replay_vs_ledger_reconciliation_summary.csv",
    )
    p.add_argument(
        "--root-cause-rows",
        type=Path,
        default=BACKTEST_ROOT / "btc1h_replay_root_cause_audit_latest_codex" / "btc1h_replay_root_cause_rows.csv",
    )
    p.add_argument(
        "--repair-feasibility-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_replay_repair_feasibility_latest_codex"
        / "btc1h_replay_repair_feasibility_summary.csv",
    )
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


def first(rows: list[dict[str, str]]) -> dict[str, str]:
    return rows[0] if rows else {}


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


def unique_join(values: list[str]) -> str:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return ";".join(out)


def status_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    out = {
        "ledger_only_rows": 0,
        "replay_only_rows": 0,
        "exact_match_drift_rows": 0,
        "entry_drift_rows": 0,
        "pnl_drift_rows": 0,
    }
    for row in rows:
        status = str(row.get("status", ""))
        if status.startswith("ledger_only"):
            out["ledger_only_rows"] += 1
        if status.startswith("replay_only"):
            out["replay_only_rows"] += 1
        if status == "exact_market_side_match" and (
            abs(to_float(row.get("entry_price_abs_diff"))) > 1e-9
            or abs(to_float(row.get("pnl_diff_replay_minus_ledger"))) > 1e-9
        ):
            out["exact_match_drift_rows"] += 1
        if abs(to_float(row.get("entry_price_abs_diff"))) > 1e-9:
            out["entry_drift_rows"] += 1
        if abs(to_float(row.get("pnl_diff_replay_minus_ledger"))) > 1e-9:
            out["pnl_drift_rows"] += 1
    return out


def summarize_reconciliation(row: dict[str, str], source: str) -> dict[str, Any]:
    blockers = str(row.get("blockers", ""))
    row_fidelity_exact = row.get("row_fidelity_exact")
    if row_fidelity_exact == "":
        row_fidelity_exact = str(not blockers)
    return {
        "source": source,
        "replay_evidence_kind": row.get("replay_evidence_kind", "independent_counterfactual"),
        "actual_rows": to_int(row.get("actual_rows")),
        "replay_rows": to_int(row.get("replay_rows")),
        "exact_market_side_matches": to_int(row.get("exact_market_side_matches")),
        "ledger_only_rows": to_int(row.get("ledger_only_rows")),
        "replay_only_rows": to_int(row.get("replay_only_rows")),
        "event_replacement_rows": to_int(row.get("event_replacement_rows")),
        "entry_price_drift_rows": to_int(row.get("entry_price_drift_rows")),
        "pnl_drift_rows": to_int(row.get("pnl_drift_rows")),
        "actual_official_pnl": to_float(row.get("actual_official_pnl")),
        "replay_pnl": to_float(row.get("replay_pnl")),
        "replay_minus_actual_pnl": to_float(row.get("replay_minus_actual_pnl")),
        "row_fidelity_exact": row_fidelity_exact,
        "promotion_usable_replay": row.get("promotion_usable_replay", ""),
        "blockers": blockers,
    }


def build_targets(root_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in root_rows:
        root = str(row.get("root_cause", "")).strip()
        if root:
            grouped.setdefault(root, []).append(row)

    target_rows: list[dict[str, Any]] = []
    for root_cause, rows in grouped.items():
        spec = ROOT_TARGETS.get(
            root_cause,
            {
                "repair_target": root_cause,
                "blockers": "row_fidelity_blocker",
                "why": "Unclassified row-fidelity issue.",
                "success": "No rows with this root cause remain.",
            },
        )
        counts = status_counts(rows)
        events = unique_join([str(row.get("event_ticker", "")) for row in rows])
        target_rows.append(
            {
                "repair_target": spec["repair_target"],
                "root_cause": root_cause,
                "required_for_promotion": True,
                "can_validate_from_existing_snapshot": True,
                "current_root_cause_rows": len(rows),
                "affected_events": events,
                **counts,
                "current_blockers_addressed": spec["blockers"],
                "baseline_confidence": "captured_order_decision_baseline_row_exact",
                "why": spec["why"],
                "success_criteria": spec["success"],
                "next_validation_step": (
                    "Rerun independent replay, then replay-vs-ledger reconciliation, root-cause audit, "
                    "and this repair target matrix."
                ),
            }
        )
    order = [
        "skip_then_fill_sequence",
        "same_event_market_selection",
        "order_decision_reprice_fill_price",
        "blocked_dedupe_scan_clock",
    ]
    rank = {name: idx for idx, name in enumerate(order)}
    return sorted(target_rows, key=lambda row: rank.get(str(row["repair_target"]), 999))


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    headers = list(rows[0].keys())
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(header, "")).replace("\n", " ") for header in headers) + " |")
    return "\n".join(out)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    current = summarize_reconciliation(first(read_csv(args.current_replay_summary)), "current_independent_replay")
    baseline = summarize_reconciliation(first(read_csv(args.baseline_reconciliation_summary)), "captured_order_decision_baseline")
    root_rows = read_csv(args.root_cause_rows)
    feasibility = first(read_csv(args.repair_feasibility_summary))
    targets = build_targets(root_rows)
    comparison = [current, baseline]

    baseline_exact = to_bool(baseline.get("row_fidelity_exact"))
    current_exact = to_bool(current.get("row_fidelity_exact"))
    next_targets = [row["repair_target"] for row in targets if to_int(row.get("current_root_cause_rows")) > 0]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "current_independent_row_fidelity_exact": current_exact,
        "current_independent_promotion_usable": to_bool(current.get("promotion_usable_replay")),
        "captured_order_decision_baseline_row_fidelity_exact": baseline_exact,
        "captured_order_decision_baseline_promotion_usable": to_bool(baseline.get("promotion_usable_replay")),
        "independent_replay_gap_is_not_data_pipeline": bool(baseline_exact and not current_exact),
        "current_exact_market_side_matches": current.get("exact_market_side_matches", 0),
        "current_actual_rows": current.get("actual_rows", 0),
        "current_replay_rows": current.get("replay_rows", 0),
        "current_replay_minus_actual_pnl": current.get("replay_minus_actual_pnl", 0.0),
        "repair_target_count": len(targets),
        "next_repair_targets": ";".join(next_targets),
        "future_rows_required_items": feasibility.get("future_rows_required_items", ""),
        "deployable_now": False,
        "promotion_note": (
            "A repair target can only make independent replay row-fidelity better; "
            "deployment still requires future clean-clock official rows and all promotion gates."
        ),
    }

    write_csv(args.out_dir / "btc1h_replay_repair_target_matrix.csv", targets)
    write_csv(args.out_dir / "btc1h_replay_repair_comparison.csv", comparison)
    write_csv(args.out_dir / "btc1h_replay_repair_target_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(
        json.dumps(
            {
                "created_at_utc": summary["created_at_utc"],
                "scope": "btc1h_independent_replay_repair_target_matrix_diagnostic_only",
                "out_dir": str(args.out_dir),
                "current_replay_summary": str(args.current_replay_summary),
                "baseline_reconciliation_summary": str(args.baseline_reconciliation_summary),
                "root_cause_rows": str(args.root_cause_rows),
                "deployable_now": False,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    report = [
        "# BTC1H Replay Repair Target Matrix",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table([summary]),
        "",
        "## Current vs Baseline",
        "",
        markdown_table(comparison),
        "",
        "## Repair Targets",
        "",
        markdown_table(targets),
        "",
        "## Interpretation",
        "",
        "- Captured order-decision row fidelity proves the data/settlement/reconciliation path can be exact.",
        "- Independent replay remains blocked until it reproduces the same row-level market, entry, and PnL behavior without using captured decisions as the trade source.",
        "- This artifact is diagnostic only and does not change deployability.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
