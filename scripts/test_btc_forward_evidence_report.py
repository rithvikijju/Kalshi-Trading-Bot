from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_btc_forward_evidence_report.py"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"


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


def read_one(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def test_forward_report_separates_btc1h_remote_official_evidence_from_local_status(tmp_path: Path) -> None:
    status_dir = tmp_path / "status"
    local_official_dir = tmp_path / "local_official"
    remote_official_dir = tmp_path / "remote_official"
    readiness_dir = tmp_path / "readiness"
    clean_clock = tmp_path / "clean_clock.csv"
    promotion_deficit = tmp_path / "promotion_deficit.csv"
    remote_status = tmp_path / "remote_status.json"
    out_dir = tmp_path / "out"
    now = datetime.now(timezone.utc)

    write_csv(
        status_dir / "shadow_status.csv",
        [
            {
                "name": LEDGER,
                "family": "BTC1H",
                "kind": "paper_shadow",
                "running": "False",
                "process_hygiene_status": "NOT_RUNNING",
                "source_freshness_status": "NOT_RUNNING_OR_PROCESS_TIME_MISSING",
                "signal_scan_rows": 10,
                "order_decision_rows": 0,
            }
        ],
    )
    (status_dir / "run_info.json").write_text(
        json.dumps({"created_at_utc": now.isoformat(), "duplicate_target_process_count": 0}),
        encoding="utf-8",
    )

    write_csv(
        local_official_dir / "shadow_official_summary.csv",
        [{"ledger": LEDGER, "scope": "since", "official_filled_rows": 0, "official_pnl": 0}],
    )
    write_csv(
        local_official_dir / "shadow_official_trades.csv",
        [
            {
                "ledger": LEDGER,
                "created_at": "",
                "market_ticker": "",
                "side": "",
                "official_proxy_result_mismatch": "False",
                "official_minus_proxy_spot": 0,
            }
        ],
    )
    (local_official_dir / "run_info.json").write_text(
        json.dumps({"created_at_utc": now.isoformat()}),
        encoding="utf-8",
    )

    write_csv(
        remote_official_dir / "shadow_official_summary.csv",
        [
            {
                "ledger": LEDGER,
                "scope": "since",
                "official_filled_rows": 11,
                "official_pnl": 0.5,
                "proxy_pnl": 1.5,
                "official_minus_proxy_pnl": -1.0,
                "official_proxy_result_mismatches": 1,
                "max_abs_official_minus_proxy_spot": 87.42,
            }
        ],
    )
    (remote_official_dir / "run_info.json").write_text(
        json.dumps({"created_at_utc": (now - timedelta(minutes=240)).isoformat()}),
        encoding="utf-8",
    )

    write_csv(
        readiness_dir / "readiness_summary.csv",
        [
            {
                "family": "BTC1H",
                "candidate": "high_conf_80_entry70_no_chase",
                "source": "btc1h_multi_holdout_research",
                "production_ready": "False",
                "failure_reasons": "controlled_restart_not_executed",
                "live_official_trades": 11,
                "live_official_pnl": 0.5,
            }
        ],
    )
    (readiness_dir / "run_info.json").write_text(
        json.dumps({"created_at_utc": now.isoformat()}),
        encoding="utf-8",
    )

    write_csv(
        clean_clock,
        [
            {
                "gate_status": "BLOCKED_CONTROLLED_RESTART_REQUIRED",
                "clean_evidence_clock_ready": "False",
                "status_source": "status_json",
                "status_age_minutes": 240,
                "blocker_count": 9,
                "blank_policy_official_rows": 11,
                "official_rows": 11,
                "official_proxy_mismatches": 1,
            }
        ],
    )
    write_csv(
        promotion_deficit,
        [
            {
                "current_verdict": "objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate",
                "candidates_current_artifacts_can_make_near_deployable": 0,
                "candidates_with_no_promotion_countable_data": 4,
                "clean_official_row_deficit": 50,
                "active_proxy_official_mismatch_rate_excess": 0.0709,
                "active_replay_exact_match_rate_deficit": 0.181818,
                "active_execution_field_complete_rate_deficit": 1.0,
                "faithful_replay_missing_required_field_count": 17,
                "faithful_replay_current_artifacts_can_support": "False",
            }
        ],
    )
    remote_status.write_text(
        json.dumps(
            {
                "updated_at_utc": (now - timedelta(minutes=240)).isoformat(),
                "enabled": True,
                "failed": False,
                "queue_depth": 0,
                "rows_by_table": {"signal_scan": 615439, "order_decision": 14},
                "replay_sidecar_rows_by_table": {"signal_scan": 134648, "order_decision": 1},
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--status-dir",
            str(status_dir),
            "--official-dir",
            str(local_official_dir),
            "--remote-official-dir",
            str(remote_official_dir),
            "--readiness-dir",
            str(readiness_dir),
            "--btc1h-remote-status-json",
            str(remote_status),
            "--btc1h-clean-clock-summary",
            str(clean_clock),
            "--btc1h-promotion-deficit-summary",
            str(promotion_deficit),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    provenance = read_one(out_dir / "btc1h_remote_provenance.csv")
    run_info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))
    assert provenance["local_shadow_running"] == "False"
    assert provenance["remote_official_rows"] == "11"
    assert provenance["remote_proxy_official_mismatches"] == "1.0"
    assert provenance["clean_clock_status"] == "BLOCKED_CONTROLLED_RESTART_REQUIRED"
    assert provenance["provenance_verdict"] == "REMOTE_STATUS_STALE_OR_UNAVAILABLE"
    assert run_info["btc1h_remote_provenance_verdict"] == "REMOTE_STATUS_STALE_OR_UNAVAILABLE"
    assert run_info["btc1h_candidates_current_artifacts_can_make_near_deployable"] == 0
    assert run_info["btc1h_clean_official_row_deficit"] == 50
    assert read_one(out_dir / "btc1h_promotion_deficit_summary.csv")[
        "active_proxy_official_mismatch_rate_excess"
    ] == "0.0709"
    report = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "BTC1H Remote Provenance" in report
    assert "BTC1H Promotion Deficit" in report
