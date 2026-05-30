from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_promotion_gap_matrix.py"


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


def gate(rows: list[dict[str, str]], gate_id: str) -> dict[str, str]:
    matches = [row for row in rows if row["gate_id"] == gate_id]
    assert len(matches) == 1
    return matches[0]


def test_promotion_gap_matrix_blocks_stale_positive_candidate(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.csv"
    multi = tmp_path / "multi.csv"
    clean = tmp_path / "clean.csv"
    basis = tmp_path / "basis.csv"
    basis_stress = tmp_path / "basis_stress.csv"
    variant_basis = tmp_path / "variant_basis.csv"
    distance = tmp_path / "distance.csv"
    side = tmp_path / "side.csv"
    independence = tmp_path / "independence.csv"
    stat = tmp_path / "stat.csv"
    replay_root = tmp_path / "replay_root.csv"
    replay_modes = tmp_path / "replay_modes.csv"
    repair_summary = tmp_path / "repair_summary.csv"
    repair_attempts = tmp_path / "repair_attempts.csv"
    packet = tmp_path / "packet.json"
    write_csv(
        readiness,
        [
            {
                "family": "BTC1H",
                "candidate": "high_conf_80_entry70_no_chase",
                "source": "btc1h_multi_holdout_research",
                "production_ready": "False",
                "live_official_trades": 11,
                "live_official_pnl": 0.5,
                "shadow_official_policy_blank_rows": 11,
                "shadow_official_policy_official_rows": 0,
                "shadow_ledger_execution_field_complete_rate": 0,
                "shadow_ledger_execution_missing_fields": "quote_age_ms;top_visible_qty",
                "frozen_policy_parity_pass": "True",
                "frozen_policy_parity_status": "PASS_FROZEN_POLICY_PARITY",
                "shadow_process_count": 1,
                "shadow_duplicate_process_count": 0,
                "shadow_source_freshness_status": "RUNNING_SOURCE_STALE_RESTART_REQUIRED",
            }
        ],
    )
    write_csv(
        multi,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "near_deployable_candidate": "True",
                "all_positive_holdouts": 14,
                "all_holdouts": 14,
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
                "historical_pnl_sum": 21.39,
                "replay_ledger_promotion_usable": "False",
                "replay_ledger_exact_match_rate": 0.818182,
                "replay_ledger_blockers": "missing_actual_rows",
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
            }
        ],
    )
    write_csv(
        basis,
        [
            {
                "variant": "high_conf_80_entry70_no_chase",
                "official_rows": 11,
                "official_pnl": 0.5,
                "official_proxy_mismatch_rate": 0.0909,
                "proxy_win_official_loss_flips": 1,
            }
        ],
    )
    write_csv(
        basis_stress,
        [
            {
                "basis_shock_usd": 50.0,
                "pnl": 9.04,
                "unique_market_side_pnl": 4.78,
                "basis_flip_rows": 0,
                "negative_holdouts": "",
            },
            {
                "basis_shock_usd": 78.915,
                "pnl": 0.04,
                "unique_market_side_pnl": -0.22,
                "basis_flip_rows": 9,
                "negative_holdouts": "H1;H2",
            },
        ],
    )
    write_csv(
        variant_basis,
        [
            {
                "variant": "high_conf_80_no_chase",
                "max_basis_shock_with_positive_unique_pnl": 87.42,
                "unique_pnl_at_p95_or_max": 1.6,
            },
            {
                "variant": "high_conf_80_entry70_no_chase",
                "max_basis_shock_with_positive_unique_pnl": 75.0,
                "unique_pnl_at_p95_or_max": -0.22,
            },
        ],
    )
    write_csv(
        distance,
        [
            {
                "threshold_usd": 50.0,
                "positive_historical_holdouts": 13,
                "historical_holdouts": 15,
                "ws_positive_cadences": 4,
                "ws_cadences": 6,
                "forward_official_proxy_mismatches_current_diagnostic": 0,
            }
        ],
    )
    write_csv(
        side,
        [
            {
                "profile": "all_active",
                "historical_pnl_stressed": 21.39,
            },
            {
                "profile": "yes_only",
                "positive_historical_holdouts": 14,
                "historical_holdouts": 15,
                "ws_positive_cadences": 5,
                "ws_cadences": 6,
            },
            {
                "profile": "entry_60_70",
                "forward_official_pnl_current_diagnostic": 0.09,
                "forward_official_proxy_mismatches_current_diagnostic": 1,
            },
        ],
    )
    write_csv(
        independence,
        [
            {
                "panel": "historical_all_rows",
                "status": "WEAK_OR_CONCENTRATED_RESEARCH",
                "rows": 159,
                "duplicate_market_side_rows": 90,
                "pnl": 21.39,
                "unique_market_side_pnl": 5.82,
                "leave_one_event_min_pnl": 19.41,
                "blockers": "duplicate_market_side_rows",
            },
            {
                "panel": "historical_unique_market_side_rows",
                "status": "PASS_RESEARCH_INDEPENDENCE",
                "rows": 69,
                "duplicate_market_side_rows": 0,
                "pnl": 5.82,
                "unique_market_side_pnl": 5.82,
                "leave_one_event_min_pnl": 5.26,
            },
        ],
    )
    write_csv(
        stat,
        [
            {
                "panel": "historical_naive_all_rows",
                "status": "PASS_RESEARCH_STAT",
                "event_cluster_bootstrap_total_pnl_p025": 2.0,
                "breakeven_null_pvalue": 0.01,
            },
            {
                "panel": "historical_unique_market_side",
                "status": "WEAK_OR_FRAGILE_RESEARCH_STAT",
                "event_cluster_bootstrap_total_pnl_p025": -1.2,
                "breakeven_null_pvalue": 0.04,
            },
            {
                "panel": "forward_official_stale_rows",
                "status": "DIAGNOSTIC_ONLY_STALE_OFFICIAL",
                "event_cluster_bootstrap_total_pnl_p025": -2.6,
                "breakeven_null_pvalue": 0.51,
            },
        ],
    )
    write_csv(
        replay_root,
        [
            {
                "root_cause_rows": 5,
                "unique_root_causes": 4,
                "dominant_root_cause": "same_event_market_selection_not_row_faithful",
                "root_cause_counts": "same_event_market_selection_not_row_faithful=2;reprice_skip_then_fill_sequence_missing=1",
                "requires_order_decision_reprice_model": "True",
                "requires_blocked_dedupe_filter": "True",
            }
        ],
    )
    write_csv(
        replay_modes,
        [
            {
                "mode": "candidate_scan",
                "promotion_usable_replay": "False",
            },
            {
                "mode": "ttl15_candidate_scan",
                "promotion_usable_replay": "False",
            },
        ],
    )
    write_csv(
        repair_summary,
        [
            {
                "attempt_count": 2,
                "best_current_snapshot_attempt": "matched_drift_decision_fill_price_patch_simulation",
                "strict_selected_scan_attempt_verdict": "REGRESSES_ROW_FIDELITY",
                "matched_drift_patch_rows": 2,
                "matched_drift_patch_removes_entry_drift": "True",
                "remaining_blockers_after_best_attempt": "missing_actual_rows;replay_row_count_differs;extra_replay_rows",
                "current_snapshot_repairs_make_replay_promotion_usable": "False",
            }
        ],
    )
    write_csv(
        repair_attempts,
        [
            {
                "attempt": "matched_drift_decision_fill_price_patch_simulation",
                "attempt_verdict": "HELPS_BUT_NOT_PROMOTION_USABLE",
            },
            {
                "attempt": "strict_selected_scan_selected_market_replay",
                "attempt_verdict": "REGRESSES_ROW_FIDELITY",
            },
        ],
    )
    packet.write_text(json.dumps({"packet_status": "READY_FOR_EXPLICIT_RESTART_AUTHORIZATION"}), encoding="utf-8")
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--readiness-summary",
            str(readiness),
            "--multi-holdout-summary",
            str(multi),
            "--clean-clock-summary",
            str(clean),
            "--basis-summary",
            str(basis),
            "--basis-stress-summary",
            str(basis_stress),
            "--variant-basis-stress-ranking",
            str(variant_basis),
            "--distance-summary",
            str(distance),
            "--side-entry-summary",
            str(side),
            "--holdout-independence-summary",
            str(independence),
            "--statistical-summary",
            str(stat),
            "--replay-root-cause-summary",
            str(replay_root),
            "--replay-mode-comparison",
            str(replay_modes),
            "--replay-repair-attempt-summary",
            str(repair_summary),
            "--replay-repair-attempts",
            str(repair_attempts),
            "--next-forward-run-info",
            str(packet),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = read_rows(out_dir / "btc1h_promotion_gap_matrix.csv")
    assert gate(rows, "historical_multi_holdout")["status"] == "PASS_RESEARCH_ONLY"
    assert gate(rows, "clean_forward_sample_size")["status"] == "BLOCKED"
    assert gate(rows, "official_pnl")["status"] == "DIAGNOSTIC_ONLY"
    assert gate(rows, "basis_stress")["status"] == "DIAGNOSTIC_ONLY"
    assert gate(rows, "variant_basis_stress_ranking")["status"] == "DIAGNOSTIC_ONLY"
    assert gate(rows, "holdout_independence")["status"] == "DIAGNOSTIC_ONLY"
    assert gate(rows, "statistical_confidence")["status"] == "DIAGNOSTIC_ONLY"
    assert gate(rows, "official_proxy_agreement")["status"] == "BLOCKED"
    row_replay = gate(rows, "row_for_row_replay")
    assert row_replay["status"] == "BLOCKED"
    assert "same_event_market_selection_not_row_faithful=2" in row_replay["current_evidence"]
    assert "promotion-usable replay modes 0/2" in row_replay["current_evidence"]
    repair_gate = gate(rows, "replay_repair_attempts")
    assert repair_gate["status"] == "BLOCKED"
    assert "REGRESSES_ROW_FIDELITY" in repair_gate["current_evidence"]
    assert "current_snapshot_repairs_not_promotion_usable" in repair_gate["blocker"]
    assert "selected_scan_attempt_regresses_row_fidelity" in repair_gate["blocker"]
    assert gate(rows, "distance_guard_candidate")["status"] == "REJECTED_DIAGNOSTIC"
    summary = read_rows(out_dir / "btc1h_promotion_gap_summary.csv")[0]
    assert summary["deployable_now"] == "False"
    assert summary["recommended_policy_change"] == "none"
    assert summary["blocked_gate_count"] == "9"
    assert summary["replay_dominant_root_cause"] == "same_event_market_selection_not_row_faithful"
    assert summary["replay_promotion_usable_modes"] == "0"
    assert summary["replay_modes_compared"] == "2"
    assert summary["replay_repair_attempt_count"] == "2"
    assert summary["replay_repair_strict_selected_scan_attempt_verdict"] == "REGRESSES_ROW_FIDELITY"
    assert summary["replay_repair_current_snapshot_repairs_promotion_usable"] == "False"
