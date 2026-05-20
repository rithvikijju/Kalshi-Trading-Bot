#!/usr/bin/env python3
"""Refresh BTC deployability evidence in dependency order.

This script is intentionally read-only with respect to live/paper processes:
it does not start, stop, restart, archive, migrate, trade, or tune thresholds.
Its job is to prevent mixed-state artifacts by running dependent gate builders
sequentially. In particular, anything that consumes
`btc_shadow_official_settlement_latest_codex` runs only after the REST-official
settlement refresh has completed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_evidence_stack_refresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_FREEZE_UTC = "2026-05-18T04:17:44Z"
DEFAULT_SINCE_UTC = DEFAULT_FREEZE_UTC
REFRESH_METADATA_FILES = ("refresh_steps.json", "run_info.json", "report.md")


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    expected_codes: tuple[int, ...] = (0,)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sequentially refresh BTC deployability evidence artifacts.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--since-utc", default=DEFAULT_SINCE_UTC)
    parser.add_argument("--freeze-utc", default=DEFAULT_FREEZE_UTC)
    parser.add_argument("--skip-packet", action="store_true", help="Do not rebuild the GPT Pro packet at the end.")
    parser.add_argument(
        "--start-at",
        default=None,
        help="Resume at a 1-based step index or step name without rerunning earlier dependency steps.",
    )
    parser.add_argument(
        "--stop-after",
        default=None,
        help="Stop after a 1-based step index or step name. Useful for bounded refresh chunks.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the planned commands without running them.")
    return parser.parse_args()


def py(script: str, *args: str) -> tuple[str, ...]:
    return (sys.executable, script, *args)


def planned_steps(args: argparse.Namespace) -> list[Step]:
    steps = [
        Step(
            "forward_shadow_status",
            py(
                "scripts/check_btc_forward_shadow_status.py",
                "--out-dir",
                "backtest_outputs/btc_forward_shadow_status_latest_codex",
                "--since-utc",
                args.since_utc,
            ),
        ),
        Step(
            "shadow_official_settlement",
            py(
                "scripts/check_btc_shadow_official_settlement.py",
                "--out-dir",
                "backtest_outputs/btc_shadow_official_settlement_latest_codex",
                "--since-utc",
                args.since_utc,
            ),
        ),
        # Refresh focused BTC15M replay artifacts before downstream audits consume
        # q250/q250-YES/q1000 replay rows.
        Step(
            "btc15m_postfreeze_replay_refresh",
            py(
                "scripts/refresh_btc15m_postfreeze_replays.py",
                "--out-dir",
                "backtest_outputs/btc15m_postfreeze_replay_refresh_latest_codex",
                "--freeze-utc",
                args.freeze_utc,
            ),
        ),
        Step(
            "btc15m_full_causal_replay_refresh",
            py(
                "scripts/refresh_btc15m_full_causal_replays.py",
                "--out-dir",
                "backtest_outputs/btc15m_full_causal_replay_refresh_latest_codex",
            ),
        ),
        Step(
            "btc15m_candidate_overlap",
            py(
                "scripts/audit_btc15m_candidate_overlap.py",
                "--out-dir",
                "backtest_outputs/btc15m_candidate_overlap_latest_codex",
            ),
        ),
        Step(
            "btc15m_shadow_replay_config_audit",
            py(
                "scripts/build_btc15m_shadow_replay_config_audit.py",
                "--out-dir",
                "backtest_outputs/btc15m_shadow_replay_config_audit_latest_codex",
            ),
        ),
        Step(
            "btc15m_latest_live_replay_diagnostics",
            py(
                "scripts/analyze_btc15m_latest_live_replay_diagnostics.py",
                "--out-dir",
                "backtest_outputs/btc15m_latest_live_replay_diagnostics_latest_codex",
            ),
        ),
        Step(
            "btc15m_settlement_basis_watch",
            py(
                "scripts/build_btc15m_settlement_basis_watch.py",
                "--out-dir",
                "backtest_outputs/btc15m_settlement_basis_watch_latest_codex",
            ),
        ),
        # Everything below can consume shadow_official_settlement_latest_codex and
        # refreshed focused BTC15M replay artifacts.
        Step(
            "execution_realism_audit",
            py("scripts/build_btc_execution_realism_audit.py", "--out-dir", "backtest_outputs/btc_execution_realism_audit_latest_codex"),
        ),
        Step(
            "ledger_schema_preflight",
            py("scripts/build_btc_ledger_schema_preflight.py", "--out-dir", "backtest_outputs/btc_ledger_schema_preflight_latest_codex"),
        ),
        Step(
            "shadow_restart_preflight",
            py("scripts/build_btc_shadow_restart_preflight.py", "--out-dir", "backtest_outputs/btc_shadow_restart_preflight_latest_codex"),
        ),
        Step(
            "restart_authorization_packet",
            py("scripts/build_btc_restart_authorization_packet.py", "--out-dir", "backtest_outputs/btc_restart_authorization_packet_latest_codex"),
        ),
        Step(
            "post_restart_collection_gate",
            py("scripts/build_btc_post_restart_collection_gate.py", "--out-dir", "backtest_outputs/btc_post_restart_collection_gate_latest_codex"),
        ),
        Step(
            "post_restart_verification",
            py(
                "scripts/build_btc_post_restart_verification.py",
                "--out-dir",
                "backtest_outputs/btc_post_restart_verification_latest_codex",
            ),
        ),
        Step(
            "drawdown_sequence_audit",
            py("scripts/build_btc_drawdown_sequence_audit.py", "--out-dir", "backtest_outputs/btc_drawdown_sequence_audit_latest_codex"),
        ),
        Step(
            "btc15m_shadow_signal_health",
            py(
                "scripts/analyze_btc15m_shadow_signal_health.py",
                "--out-dir",
                "backtest_outputs/btc15m_shadow_signal_health_latest_codex",
                "--since-utc",
                args.freeze_utc,
            ),
        ),
        Step(
            "btc15m_signal_starvation",
            py(
                "scripts/build_btc15m_signal_starvation_report.py",
                "--out-dir",
                "backtest_outputs/btc15m_signal_starvation_latest_codex",
                "--since-utc",
                args.freeze_utc,
            ),
        ),
        Step(
            "btc15m_frozen_opportunity_rate",
            py(
                "scripts/build_btc15m_frozen_opportunity_rate_report.py",
                "--out-dir",
                "backtest_outputs/btc15m_frozen_opportunity_rate_latest_codex",
                "--freeze-utc",
                args.freeze_utc,
            ),
        ),
        Step(
            "btc15m_first_signal_side_semantics",
            py(
                "scripts/audit_btc15m_first_signal_side_semantics.py",
                "--out-dir",
                "backtest_outputs/btc15m_first_signal_side_semantics_latest_codex",
            ),
        ),
        Step(
            "forward_row_reconciliation",
            py("scripts/build_btc_forward_row_reconciliation.py", "--out-dir", "backtest_outputs/btc_forward_row_reconciliation_latest_codex"),
        ),
        Step(
            "official_settlement_feature_table",
            py("scripts/build_btc_official_settlement_feature_table.py", "--out-dir", "backtest_outputs/btc_official_settlement_feature_table_latest_codex"),
        ),
        Step(
            "btc15m_next_forward_candidate_packet",
            py(
                "scripts/build_btc15m_next_forward_candidate_packet.py",
                "--out-dir",
                "backtest_outputs/btc15m_next_forward_candidate_packet_latest_codex",
            ),
        ),
        Step(
            "frozen_policy_parity",
            py(
                "scripts/audit_btc_frozen_policy_parity.py",
                "--out-dir",
                "backtest_outputs/btc_frozen_policy_parity_latest_codex",
            ),
        ),
        Step(
            "btc15m_predexon_official_coverage",
            py(
                "scripts/audit_btc15m_predexon_official_coverage.py",
                "--out-dir",
                "backtest_outputs/btc15m_predexon_official_coverage_latest_codex",
            ),
        ),
        Step(
            "btc15m_predexon_metadata_vs_rest",
            py(
                "scripts/audit_btc15m_predexon_metadata_vs_rest.py",
                "--out-dir",
                "backtest_outputs/btc15m_predexon_metadata_vs_rest_latest_codex",
            ),
        ),
        Step(
            "settlement_basis_risk_audit",
            py(
                "scripts/build_btc_settlement_basis_risk_audit.py",
                "--out-dir",
                "backtest_outputs/btc_settlement_basis_risk_audit_latest_codex",
                "--freeze-utc",
                args.freeze_utc,
            ),
        ),
        Step(
            "basis_danger_table",
            py("scripts/build_btc_basis_danger_table.py", "--out-dir", "backtest_outputs/btc_basis_danger_table_latest_codex"),
        ),
        Step(
            "settlement_basis_guard_candidates",
            py(
                "scripts/build_btc_settlement_basis_guard_candidates.py",
                "--out-dir",
                "backtest_outputs/btc_settlement_basis_guard_candidates_latest_codex",
            ),
        ),
        Step(
            "settlement_basis_model_feasibility",
            py("scripts/build_btc_settlement_basis_model_feasibility.py", "--out-dir", "backtest_outputs/btc_settlement_basis_model_feasibility_latest_codex"),
        ),
        Step(
            "btc1h_replay_coverage",
            py(
                "scripts/build_btc1h_replay_coverage_audit.py",
                "--out-dir",
                "backtest_outputs/btc1h_replay_coverage_audit_latest_codex",
                "--since-utc",
                args.freeze_utc,
            ),
        ),
        Step(
            "deployment_readiness",
            py("scripts/check_btc_deployment_readiness.py", "--out-dir", "backtest_outputs/deployment_readiness_latest_codex"),
            expected_codes=(0, 1),
        ),
        Step(
            "forward_evidence_report",
            py("scripts/build_btc_forward_evidence_report.py", "--out-dir", "backtest_outputs/btc_forward_evidence_report_latest_codex"),
        ),
        Step(
            "strategy_triage",
            py("scripts/audit_btc_strategy_triage.py", "--out-dir", "backtest_outputs/btc_strategy_triage_latest_codex"),
        ),
        Step(
            "kill_continue_report",
            py("scripts/build_btc_kill_continue_report.py", "--out-dir", "backtest_outputs/btc_kill_continue_latest_codex"),
        ),
        Step(
            "forward_consistency_audit",
            py("scripts/build_btc_forward_consistency_audit.py", "--out-dir", "backtest_outputs/btc_forward_consistency_audit_latest_codex"),
        ),
        Step(
            "gpt_pro_action_status",
            py("scripts/build_btc_gpt_pro_action_status.py", "--out-dir", "backtest_outputs/btc_gpt_pro_action_status_latest_codex"),
        ),
    ]
    if not args.skip_packet:
        steps.append(Step("gpt_pro_strategy_packet", py("scripts/build_gpt_pro_strategy_packet.py")))
    return steps


def command_text(argv: Iterable[str]) -> str:
    return " ".join(str(part) for part in argv)


def resolve_step_ref(value: str | None, steps: list[Step], *, default: int) -> int:
    if not value:
        return default
    raw = str(value).strip()
    if raw.isdigit():
        idx = int(raw)
        if idx < 1 or idx > len(steps):
            raise ValueError(f"step index out of range: {raw}; expected 1..{len(steps)}")
        return idx
    matches = [idx for idx, step in enumerate(steps, start=1) if step.name == raw]
    if not matches:
        names = ", ".join(step.name for step in steps)
        raise ValueError(f"unknown step name: {raw}; expected one of: {names}")
    return matches[0]


def selected_steps(args: argparse.Namespace, steps: list[Step]) -> list[tuple[int, Step]]:
    start = resolve_step_ref(args.start_at, steps, default=1)
    stop = resolve_step_ref(args.stop_after, steps, default=len(steps))
    if stop < start:
        raise ValueError(f"--stop-after ({stop}) must be >= --start-at ({start})")
    return [(idx, step) for idx, step in enumerate(steps, start=1) if start <= idx <= stop]


def cleanup_previous_refresh_artifacts(out_dir: Path, step_indexes: set[int] | None = None) -> None:
    """Remove only this controller's prior selected logs/metadata from a reused out-dir."""
    if step_indexes is None:
        patterns = ["[0-9][0-9]_*.log"]
    else:
        patterns = [f"{idx:02d}_*.log" for idx in sorted(step_indexes)]
    for pattern in patterns:
        for path in out_dir.glob(pattern):
            if path.is_file():
                path.unlink()
    for name in REFRESH_METADATA_FILES:
        path = out_dir / name
        if path.is_file():
            path.unlink()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    steps = planned_steps(args)
    try:
        step_plan = selected_steps(args, steps)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    cleanup_previous_refresh_artifacts(args.out_dir, {idx for idx, _step in step_plan})
    started_at = datetime.now(timezone.utc)
    rows: list[dict[str, object]] = []

    if args.dry_run:
        for idx, step in step_plan:
            print(f"{idx:02d}. {step.name}: {command_text(step.argv)}")
        (args.out_dir / "run_info.json").write_text(
            json.dumps(
                {
                    "created_at_utc": started_at.isoformat(),
                    "dry_run": True,
                    "start_at": args.start_at,
                    "stop_after": args.stop_after,
                    "steps_planned_total": len(steps),
                    "steps": [
                        {
                            "index": idx,
                            "name": step.name,
                            "argv": list(step.argv),
                            "expected_codes": list(step.expected_codes),
                        }
                        for idx, step in step_plan
                    ],
                    "note": "Dry run only. No artifacts were refreshed.",
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0

    for idx, step in step_plan:
        print(f"[{idx}/{len(steps)}] {step.name}: {command_text(step.argv)}", flush=True)
        step_started = datetime.now(timezone.utc)
        completed = subprocess.run(
            list(step.argv),
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        step_finished = datetime.now(timezone.utc)
        output_path = args.out_dir / f"{idx:02d}_{step.name}.log"
        output_path.write_text(completed.stdout or "", encoding="utf-8", errors="replace")
        ok = completed.returncode in step.expected_codes
        rows.append(
            {
                "index": idx,
                "name": step.name,
                "returncode": completed.returncode,
                "expected_codes": list(step.expected_codes),
                "ok": ok,
                "started_at_utc": step_started.isoformat(),
                "finished_at_utc": step_finished.isoformat(),
                "log": str(output_path),
            }
        )
        if not ok:
            print(completed.stdout)
            break

    all_ok = all(bool(row["ok"]) for row in rows) and len(rows) == len(step_plan)
    run_info = {
        "created_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": False,
        "since_utc": args.since_utc,
        "freeze_utc": args.freeze_utc,
        "start_at": args.start_at,
        "stop_after": args.stop_after,
        "all_ok": all_ok,
        "steps_completed": len(rows),
        "steps_planned": len(step_plan),
        "steps_planned_total": len(steps),
        "step_indexes_planned": [idx for idx, _step in step_plan],
        "note": "Sequential read-only refresh. This script does not start, stop, restart, archive, migrate, trade, deploy, or tune thresholds.",
    }
    (args.out_dir / "refresh_steps.json").write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    report_lines = [
        "# BTC Evidence Stack Refresh",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"All OK: `{all_ok}`",
        "",
        "## Steps",
        "",
    ]
    for row in rows:
        status = "PASS" if row["ok"] else "FAIL"
        report_lines.append(f"- {status}: {row['index']:02d} `{row['name']}` returncode `{row['returncode']}`")
    report_lines.extend(
        [
            "",
            "## Run Info",
            "",
            "```json",
            json.dumps(run_info, indent=2, sort_keys=True),
            "```",
        ]
    )
    (args.out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print("\n".join(report_lines))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
