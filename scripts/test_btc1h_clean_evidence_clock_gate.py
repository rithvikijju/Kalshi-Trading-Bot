from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc1h_clean_evidence_clock_gate.py"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"
EXPECTED_TTL_POLICY = "scan_time_close_minus_now_v1"
EXPECTED_POLICY_VERSION = "btc1h_live_model_20260522_scan_ttl_v1"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_inputs(tmp_path: Path, *, complete_schema: bool = True, expected_policy: bool = True) -> dict[str, Path]:
    status = tmp_path / "status.json"
    schema = tmp_path / "schema.json"
    official = tmp_path / "official.csv"
    policy = tmp_path / "policy.csv"
    holdout = tmp_path / "holdout.csv"
    parity = tmp_path / "parity.csv"
    reconciliation = tmp_path / "reconciliation.csv"

    status.write_text(
        json.dumps(
            {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "enabled": True,
                "failed": False,
                "dropped": 0,
                "queue_depth": 0,
                "rows_by_table": {"signal_scan": 100, "order_decision": 50},
                "replay_sidecar_rows_by_table": {"signal_scan": 100, "order_decision": 50},
            }
        ),
        encoding="utf-8",
    )
    signal_fields = [
        "signal_strategy",
        "model_ttl_policy",
        "model_policy_version",
        "edge_threshold_cents",
        "spread_cents",
        "top_visible_qty",
        "quote_received_at_ns",
        "quote_age_ms",
        "ttl_min",
        "close_time",
        "btc_candle_time",
        "btc_candle_age_sec",
        "btc_rv60",
        "btc_ret_10m_usd",
    ]
    if not complete_schema:
        signal_fields = ["action", "received_at_ns"]
    schema.write_text(
        json.dumps(
            {
                "signal_scan": signal_fields,
                "order_decision": ["signal_strategy", "model_ttl_policy", "model_policy_version"],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        official,
        [
            {
                "ledger": LEDGER,
                "scope": "since",
                "official_filled_rows": 50,
                "official_pnl": 4.25,
                "official_win_rate": 0.72,
                "official_proxy_result_mismatches": 0,
            }
        ],
    )
    policy_rows = [
        {
            "ledger": LEDGER,
            "scope": "since",
            "model_ttl_policy": EXPECTED_TTL_POLICY if expected_policy else "",
            "model_policy_version": EXPECTED_POLICY_VERSION if expected_policy else "",
            "official_filled_rows": 50,
            "official_pnl": 4.25,
            "official_proxy_result_mismatches": 0,
        }
    ]
    write_csv(policy, policy_rows)
    write_csv(
        holdout,
        [
            {
                "variant": VARIANT,
                "research_promising": "True",
                "all_positive_holdouts": 14,
                "all_holdouts": 14,
                "ws_positive_cadences": 6,
                "ws_cadences": 6,
            }
        ],
    )
    write_csv(
        parity,
        [
            {
                "ledger": LEDGER,
                "selected_signal_rows": 13,
                "captured_ttl_available_rows": 13,
                "captured_ttl_pass_rows": 13,
            }
        ],
    )
    write_csv(reconciliation, [{"promotion_usable_replay": "True", "exact_match_rate": 1.0}])
    return {
        "status": status,
        "schema": schema,
        "official": official,
        "policy": policy,
        "holdout": holdout,
        "parity": parity,
        "reconciliation": reconciliation,
    }


def run_gate(tmp_path: Path, paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(tmp_path / "out"),
            "--status-json",
            str(paths["status"]),
            "--sidecar-schema-json",
            str(paths["schema"]),
            "--shadow-official-summary",
            str(paths["official"]),
            "--shadow-official-policy-summary",
            str(paths["policy"]),
            "--multi-holdout-summary",
            str(paths["holdout"]),
            "--selected-parity-summary",
            str(paths["parity"]),
            "--replay-reconciliation-summary",
            str(paths["reconciliation"]),
            "--forward-status-summary",
            str(paths.get("forward", tmp_path / "missing_forward.csv")),
            "--min-official-rows",
            "50",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def read_summary(tmp_path: Path) -> dict[str, str]:
    with (tmp_path / "out" / "btc1h_clean_evidence_clock_summary.csv").open(
        "r", newline="", encoding="utf-8"
    ) as f:
        return next(csv.DictReader(f))


def test_clean_evidence_clock_gate_passes_when_all_promotion_clock_inputs_exist(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    result = run_gate(tmp_path, paths)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_summary(tmp_path)
    assert summary["gate_status"] == "PASS_CLEAN_EVIDENCE_CLOCK"
    assert summary["clean_evidence_clock_ready"] == "True"


def test_clean_evidence_clock_gate_blocks_old_sidecar_schema(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path, complete_schema=False)
    result = run_gate(tmp_path, paths)
    assert result.returncode == 1
    summary = read_summary(tmp_path)
    assert summary["gate_status"] == "BLOCKED_CONTROLLED_RESTART_REQUIRED"
    assert "ttl_min" in summary["sidecar_signal_missing_fields"]


def test_clean_evidence_clock_gate_blocks_blank_policy_rows(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path, expected_policy=False)
    result = run_gate(tmp_path, paths)
    assert result.returncode == 1
    summary = read_summary(tmp_path)
    assert summary["gate_status"] == "BLOCKED_CONTROLLED_RESTART_REQUIRED"
    assert summary["expected_policy_official_rows"] == "0"
    assert summary["blank_policy_official_rows"] == "50.0"


def test_clean_evidence_clock_gate_uses_forward_status_and_replay_sidecar_fallback(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    paths["status"].unlink()
    paths["schema"].unlink()
    replay_sidecar = tmp_path / "capture.duckdb.replay.jsonl"
    replay_sidecar.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "table": "signal_scan",
                        "signal_strategy": "btc1h_high_conf80_entry70_no_chase",
                        "model_ttl_policy": EXPECTED_TTL_POLICY,
                        "model_policy_version": EXPECTED_POLICY_VERSION,
                        "edge_threshold_cents": 8,
                        "spread_cents": 1,
                        "top_visible_qty": 5,
                        "quote_received_at_ns": 1,
                        "quote_age_ms": 1,
                        "ttl_min": 42,
                        "close_time": "2026-05-22T10:00:00+00:00",
                        "btc_candle_time": "2026-05-22T09:59:00+00:00",
                        "btc_candle_age_sec": 1,
                        "btc_rv60": 3,
                        "btc_ret_10m_usd": 4,
                    }
                ),
                json.dumps(
                    {
                        "table": "order_decision",
                        "signal_strategy": "btc1h_high_conf80_entry70_no_chase",
                        "model_ttl_policy": EXPECTED_TTL_POLICY,
                        "model_policy_version": EXPECTED_POLICY_VERSION,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    forward = tmp_path / "forward_status.csv"
    write_csv(
        forward,
        [
            {
                "name": LEDGER,
                "running": "True",
                "capture_sidecar_updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "signal_scan_rows": 100,
                "order_decision_rows": 50,
                "replay_sidecar_path": str(replay_sidecar),
                "replay_sidecar_rows_by_table": json.dumps({"signal_scan": 1, "order_decision": 1}),
            }
        ],
    )
    paths["forward"] = forward

    result = run_gate(tmp_path, paths)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = read_summary(tmp_path)
    assert summary["status_source"] == "forward_status_summary"
    assert summary["sidecar_schema_source"] == "replay_sidecar_jsonl"
