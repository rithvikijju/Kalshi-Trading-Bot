from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_replay_repair_feasibility.py"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def by_item(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["repair_item"]: row for row in rows}


def test_replay_repair_feasibility_splits_snapshot_repairs_from_future_gates(tmp_path: Path) -> None:
    root_summary = tmp_path / "root_summary.csv"
    root_rows = tmp_path / "root_rows.csv"
    modes = tmp_path / "modes.csv"
    decision_chain = tmp_path / "decision_chain.csv"
    parity = tmp_path / "parity.csv"
    evidence = tmp_path / "evidence.csv"
    baseline = tmp_path / "baseline.csv"
    baseline_recon = tmp_path / "baseline_recon.csv"
    clean = tmp_path / "clean.csv"
    multi = tmp_path / "multi.csv"

    write_csv(
        root_summary,
        [
            {
                "root_cause_rows": 5,
                "dominant_root_cause": "same_event_market_selection_not_row_faithful",
                "root_cause_counts": (
                    "same_event_market_selection_not_row_faithful=2;"
                    "reprice_skip_then_fill_sequence_missing=1;"
                    "order_decision_reprice_fill_price_missing=1;"
                    "blocked_dedupe_or_post_selected_scan_used_as_replay_clock=1"
                ),
                "requires_order_decision_reprice_model": "True",
                "requires_blocked_dedupe_filter": "True",
                "requires_exact_model_inputs": "False",
            }
        ],
    )
    write_csv(
        root_rows,
        [
            {"root_cause": "reprice_skip_then_fill_sequence_missing"},
            {"root_cause": "order_decision_reprice_fill_price_missing"},
            {"root_cause": "blocked_dedupe_or_post_selected_scan_used_as_replay_clock"},
        ],
    )
    write_csv(
        modes,
        [
            {"mode": "candidate_scan", "promotion_usable_replay": "False"},
            {"mode": "selected_scan", "promotion_usable_replay": "False"},
        ],
    )
    write_csv(
        decision_chain,
        [
            {"decision_action": "skip", "entry_price_diff": 0.0},
            {"decision_action": "paper_fill", "entry_price_diff": 0.01},
        ],
    )
    write_csv(
        parity,
        [
            {
                "selected_signal_rows": 13,
                "parity_fail_rows": 13,
                "captured_ttl_available_rows": 0,
            }
        ],
    )
    write_csv(
        evidence,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "forward_official_rows": 11,
                "forward_official_mismatch_rate": 0.0909,
                "basis_proxy_win_official_loss_flips": 1,
            }
        ],
    )
    write_csv(
        baseline,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "baseline_rows": 11,
                "promotion_usable_as_counterfactual": "False",
            }
        ],
    )
    write_csv(
        baseline_recon,
        [
            {
                "replay_evidence_kind": "captured_order_decision_log",
                "row_fidelity_exact": "True",
                "promotion_usable_replay": "False",
                "blockers": "diagnostic_replay_not_independent_counterfactual",
            }
        ],
    )
    write_csv(
        clean,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "gate_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "clean_evidence_clock_ready": "False",
                "official_rows": 11,
                "official_proxy_mismatches": 1,
                "blank_policy_official_rows": 11,
                "sidecar_signal_missing_fields": "model_policy_version;model_ttl_policy",
                "sidecar_order_decision_missing_fields": "signal_strategy",
            }
        ],
    )
    write_csv(
        multi,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "forward_official_rows": 11,
                "forward_official_mismatch_rate": 0.0909,
            }
        ],
    )

    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--root-cause-summary",
            str(root_summary),
            "--root-cause-rows",
            str(root_rows),
            "--replay-mode-comparison",
            str(modes),
            "--decision-chain",
            str(decision_chain),
            "--selected-parity-summary",
            str(parity),
            "--current-evidence-snapshot",
            str(evidence),
            "--order-decision-baseline-summary",
            str(baseline),
            "--order-decision-baseline-reconciliation-summary",
            str(baseline_recon),
            "--clean-clock-summary",
            str(clean),
            "--multi-holdout-summary",
            str(multi),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    rows = by_item(read_rows(out_dir / "btc1h_replay_repair_feasibility.csv"))
    summary = read_rows(out_dir / "btc1h_replay_repair_feasibility_summary.csv")[0]
    run_info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))

    assert rows["order_decision_reprice_fill_model"]["can_validate_from_existing_snapshot"] == "True"
    assert rows["order_decision_reprice_fill_model"]["current_blocker_count"] == "2"
    assert rows["blocked_dedupe_filter"]["existing_evidence_status"] == "BLOCKED_TESTABLE_ON_EXISTING_SNAPSHOT"
    assert rows["exact_model_input_capture"]["can_validate_from_existing_snapshot"] == "False"
    assert rows["exact_model_input_capture"]["existing_evidence_status"] == "FUTURE_CLEAN_CLOCK_REQUIRED"
    assert rows["sample_size_gate"]["current_blocker_count"] == "39"
    assert summary["deployable_now"] == "False"
    assert summary["replay_repair_can_make_deployable_now"] == "False"
    assert summary["no_repair_item_makes_candidate_deployable_now"] == "True"
    assert summary["order_decision_baseline_rows"] == "11"
    assert summary["order_decision_baseline_row_fidelity_exact"] == "True"
    assert summary["order_decision_baseline_promotion_usable_replay"] == "False"
    assert "order_decision_reprice_fill_model" in summary["next_replay_engineering_target"]
    assert "blocked_dedupe_filter" in summary["next_replay_engineering_target"]
    assert "exact_model_input_capture" in summary["future_rows_required_items"]
    assert run_info["deployable_now"] is False
