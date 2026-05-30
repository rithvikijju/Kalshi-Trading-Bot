from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_replay_repair_target_matrix.py"


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


def test_repair_target_matrix_compares_independent_replay_to_exact_baseline(tmp_path: Path) -> None:
    current = tmp_path / "current.csv"
    baseline = tmp_path / "baseline.csv"
    roots = tmp_path / "roots.csv"
    feasibility = tmp_path / "feasibility.csv"
    write_csv(
        current,
        [
            {
                "actual_rows": 11,
                "replay_rows": 10,
                "exact_market_side_matches": 9,
                "ledger_only_rows": 2,
                "replay_only_rows": 1,
                "event_replacement_rows": 2,
                "entry_price_drift_rows": 2,
                "pnl_drift_rows": 2,
                "replay_minus_actual_pnl": 0.61,
                "promotion_usable_replay": "False",
                "blockers": "missing_actual_rows;extra_replay_rows",
            }
        ],
    )
    write_csv(
        baseline,
        [
            {
                "replay_evidence_kind": "captured_order_decision_log",
                "actual_rows": 11,
                "replay_rows": 11,
                "exact_market_side_matches": 11,
                "row_fidelity_exact": "True",
                "promotion_usable_replay": "False",
                "blockers": "diagnostic_replay_not_independent_counterfactual",
            }
        ],
    )
    write_csv(
        roots,
        [
            {
                "root_cause": "reprice_skip_then_fill_sequence_missing",
                "status": "ledger_only_missing_from_replay",
                "event_ticker": "KXBTCD-26MAY2210",
                "entry_price_abs_diff": "",
                "pnl_diff_replay_minus_ledger": "",
            },
            {
                "root_cause": "same_event_market_selection_not_row_faithful",
                "status": "replay_only_extra_vs_ledger_event_replacement",
                "event_ticker": "KXBTCD-26MAY2211",
                "entry_price_abs_diff": "",
                "pnl_diff_replay_minus_ledger": "",
            },
            {
                "root_cause": "order_decision_reprice_fill_price_missing",
                "status": "exact_market_side_match",
                "event_ticker": "KXBTCD-26MAY2212",
                "entry_price_abs_diff": 0.01,
                "pnl_diff_replay_minus_ledger": -0.01,
            },
        ],
    )
    write_csv(
        feasibility,
        [
            {
                "future_rows_required_items": "exact_model_input_capture;official_proxy_basis_gate",
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
            "--current-replay-summary",
            str(current),
            "--baseline-reconciliation-summary",
            str(baseline),
            "--root-cause-rows",
            str(roots),
            "--repair-feasibility-summary",
            str(feasibility),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_rows(out_dir / "btc1h_replay_repair_target_summary.csv")[0]
    targets = by_target(read_rows(out_dir / "btc1h_replay_repair_target_matrix.csv"))
    comparison = {row["source"]: row for row in read_rows(out_dir / "btc1h_replay_repair_comparison.csv")}
    run_info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))

    assert summary["current_independent_row_fidelity_exact"] == "False"
    assert summary["captured_order_decision_baseline_row_fidelity_exact"] == "True"
    assert summary["independent_replay_gap_is_not_data_pipeline"] == "True"
    assert "skip_then_fill_sequence" in summary["next_repair_targets"]
    assert targets["skip_then_fill_sequence"]["ledger_only_rows"] == "1"
    assert targets["same_event_market_selection"]["replay_only_rows"] == "1"
    assert targets["order_decision_reprice_fill_price"]["entry_drift_rows"] == "1"
    assert comparison["captured_order_decision_baseline"]["replay_evidence_kind"] == "captured_order_decision_log"
    assert run_info["deployable_now"] is False
