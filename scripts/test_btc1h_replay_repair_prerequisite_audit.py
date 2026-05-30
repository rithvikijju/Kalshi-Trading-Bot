from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_replay_repair_prerequisite_audit.py"


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


def by_target(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["repair_target"]: row for row in rows}


def test_prerequisite_audit_separates_repairable_rows_from_exact_model_input_blockers(tmp_path: Path) -> None:
    target_matrix = tmp_path / "targets.csv"
    roots = tmp_path / "roots.csv"
    parity = tmp_path / "parity.csv"
    chain = tmp_path / "chain.csv"
    write_csv(
        target_matrix,
        [
            {
                "repair_target": "skip_then_fill_sequence",
                "root_cause": "reprice_skip_then_fill_sequence_missing",
                "current_root_cause_rows": 1,
            },
            {
                "repair_target": "same_event_market_selection",
                "root_cause": "same_event_market_selection_not_row_faithful",
                "current_root_cause_rows": 2,
            },
            {
                "repair_target": "order_decision_reprice_fill_price",
                "root_cause": "order_decision_reprice_fill_price_missing",
                "current_root_cause_rows": 1,
            },
            {
                "repair_target": "blocked_dedupe_scan_clock",
                "root_cause": "blocked_dedupe_or_post_selected_scan_used_as_replay_clock",
                "current_root_cause_rows": 1,
            },
        ],
    )
    write_csv(
        roots,
        [
            {
                "root_cause": "reprice_skip_then_fill_sequence_missing",
                "event_ticker": "KXBTCD-26MAY1911",
                "ledger_join_key": "KXBTCD-26MAY1911-T76299.99|no",
                "replay_join_key": "",
                "scan_ttl_recomputed_signal_rows": 0,
                "scan_ttl_no_signal_rows": 2,
                "implied_ttl_pass_rows": 2,
                "max_quote_age_ms": 46.9949,
                "parity_statuses": "recomputed_no_signal",
            },
            {
                "root_cause": "same_event_market_selection_not_row_faithful",
                "event_ticker": "KXBTCD-26MAY2010",
                "ledger_join_key": "KXBTCD-26MAY2010-T77199.99|no",
                "replay_join_key": "",
                "scan_ttl_recomputed_signal_rows": 1,
                "scan_ttl_no_signal_rows": 0,
                "implied_ttl_pass_rows": 1,
                "max_model_p_yes_abs_diff": 0.003437,
                "max_net_edge_cents_abs_diff": 0.343706,
                "max_quote_age_ms": 83.029,
                "parity_statuses": "value_mismatch",
            },
            {
                "root_cause": "same_event_market_selection_not_row_faithful",
                "event_ticker": "KXBTCD-26MAY2010",
                "ledger_join_key": "",
                "replay_join_key": "KXBTCD-26MAY2010-T77299.99|no",
            },
            {
                "root_cause": "order_decision_reprice_fill_price_missing",
                "event_ticker": "KXBTCD-26MAY2107",
                "ledger_join_key": "KXBTCD-26MAY2107-T77299.99|no",
                "replay_join_key": "KXBTCD-26MAY2107-T77299.99|no",
                "scan_ttl_recomputed_signal_rows": 1,
                "scan_ttl_no_signal_rows": 0,
                "implied_ttl_pass_rows": 1,
                "max_model_p_yes_abs_diff": 0.00037,
                "max_net_edge_cents_abs_diff": 0.037,
                "max_decision_entry_abs_diff": 0.01,
                "max_quote_age_ms": 1.9987,
                "parity_statuses": "value_mismatch",
            },
            {
                "root_cause": "blocked_dedupe_or_post_selected_scan_used_as_replay_clock",
                "event_ticker": "KXBTCD-26MAY2110",
                "ledger_join_key": "KXBTCD-26MAY2110-T76699.99|yes",
                "replay_join_key": "KXBTCD-26MAY2110-T76699.99|yes",
                "scan_ttl_recomputed_signal_rows": 1,
                "scan_ttl_no_signal_rows": 0,
                "implied_ttl_pass_rows": 1,
                "max_model_p_yes_abs_diff": 0.002825,
                "max_net_edge_cents_abs_diff": 0.282465,
                "max_decision_entry_abs_diff": 0,
                "replay_minus_selected_sec": 0.727796,
                "max_quote_age_ms": 19.1144,
                "parity_statuses": "value_mismatch",
            },
        ],
    )
    write_csv(
        parity,
        [
            {
                "event_ticker": "KXBTCD-26MAY1911",
                "market_ticker": "KXBTCD-26MAY1911-T76299.99",
                "side": "no",
                "parity_status": "recomputed_no_signal",
                "top_entry_price_abs_diff": "",
            },
            {
                "event_ticker": "KXBTCD-26MAY1911",
                "market_ticker": "KXBTCD-26MAY1911-T76299.99",
                "side": "no",
                "parity_status": "recomputed_no_signal",
                "top_entry_price_abs_diff": "",
            },
            {
                "event_ticker": "KXBTCD-26MAY2010",
                "market_ticker": "KXBTCD-26MAY2010-T77199.99",
                "side": "no",
                "parity_status": "value_mismatch",
                "top_entry_price_abs_diff": 0,
            },
            {
                "event_ticker": "KXBTCD-26MAY2107",
                "market_ticker": "KXBTCD-26MAY2107-T77299.99",
                "side": "no",
                "parity_status": "value_mismatch",
                "top_entry_price_abs_diff": 0,
            },
            {
                "event_ticker": "KXBTCD-26MAY2110",
                "market_ticker": "KXBTCD-26MAY2110-T76699.99",
                "side": "yes",
                "parity_status": "value_mismatch",
                "top_entry_price_abs_diff": 0,
            },
        ],
    )
    write_csv(
        chain,
        [
            {
                "event_ticker": "KXBTCD-26MAY1911",
                "market_ticker": "KXBTCD-26MAY1911-T76299.99",
                "side": "no",
                "decision_action": "skip",
                "entry_price_diff": 0,
            },
            {
                "event_ticker": "KXBTCD-26MAY1911",
                "market_ticker": "KXBTCD-26MAY1911-T76299.99",
                "side": "no",
                "decision_action": "paper_fill",
                "entry_price_diff": 0,
            },
            {
                "event_ticker": "KXBTCD-26MAY2010",
                "market_ticker": "KXBTCD-26MAY2010-T77199.99",
                "side": "no",
                "decision_action": "paper_fill",
                "entry_price_diff": -0.08,
            },
            {
                "event_ticker": "KXBTCD-26MAY2107",
                "market_ticker": "KXBTCD-26MAY2107-T77299.99",
                "side": "no",
                "decision_action": "paper_fill",
                "entry_price_diff": -0.01,
            },
            {
                "event_ticker": "KXBTCD-26MAY2110",
                "market_ticker": "KXBTCD-26MAY2110-T76699.99",
                "side": "yes",
                "decision_action": "paper_fill",
                "entry_price_diff": 0,
            },
        ],
    )
    out_dir = tmp_path / "out"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--repair-target-matrix",
            str(target_matrix),
            "--root-cause-rows",
            str(roots),
            "--selected-parity",
            str(parity),
            "--decision-chain",
            str(chain),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_rows(out_dir / "btc1h_replay_repair_prerequisite_summary.csv")[0]
    rows = by_target(read_rows(out_dir / "btc1h_replay_repair_prerequisite_audit.csv"))
    run_info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))

    assert rows["skip_then_fill_sequence"]["existing_snapshot_can_repair_independent_replay"] == "False"
    assert "exact_model_input_capture" in rows["skip_then_fill_sequence"]["missing_prerequisites"]
    assert rows["skip_then_fill_sequence"]["selected_chain_rows"] == "2"
    assert rows["skip_then_fill_sequence"]["selected_chain_fill_rows"] == "1"
    assert rows["skip_then_fill_sequence"]["selected_chain_skip_rows"] == "1"
    assert rows["skip_then_fill_sequence"]["parity_rows"] == "2"
    assert rows["same_event_market_selection"]["existing_snapshot_can_repair_independent_replay"] == "Partial"
    assert "model_value_parity" in rows["same_event_market_selection"]["missing_prerequisites"]
    assert rows["order_decision_reprice_fill_price"]["existing_snapshot_can_repair_independent_replay"] == "True"
    assert rows["blocked_dedupe_scan_clock"]["existing_snapshot_can_repair_independent_replay"] == "True"
    assert rows["blocked_dedupe_scan_clock"]["max_replay_minus_selected_sec"] == "0.727796"
    assert summary["existing_snapshot_true_target_count"] == "2"
    assert summary["existing_snapshot_partial_target_count"] == "1"
    assert summary["existing_snapshot_false_target_count"] == "1"
    assert summary["all_targets_repairable_from_existing_snapshot"] == "False"
    assert summary["target_matrix_overstates_pure_repairability_without_this_audit"] == "True"
    assert summary["deployable_now"] == "False"
    assert run_info["deployable_now"] is False
