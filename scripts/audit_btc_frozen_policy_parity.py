#!/usr/bin/env python3
"""Audit frozen BTC paper-shadow wrapper policy parity.

This script is read-only. It checks that paper-shadow wrapper files still match
the preregistered BTC15M/BTC1H policies that future rows are supposed to
validate. A running process and a clean ledger schema are not enough; rows can
only become promotion evidence if the wrapper itself still encodes the frozen
thresholds, side policy, sizing, and paper-only mode.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_frozen_policy_parity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class PolicySpec:
    policy_id: str
    family: str
    candidate: str
    ledger: str
    wrapper_script: str
    role: str
    expected_env: dict[str, str]
    env_defaults: dict[str, str]
    required_argv_flags: dict[str, str]
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()


POLICIES = [
    PolicySpec(
        policy_id="btc15m_q250_firstskip_qty500_control",
        family="BTC15M",
        candidate="q250_firstskip_qty500",
        ledger="btc15m_q250_qty500_firstskip_shadow",
        wrapper_script=r"scripts\btc15m_f2_q250_qty500_firstskip_shadow.py",
        role="both_side_basis_decomposition_control",
        expected_env={
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
            "BTC15M_H02_SPREAD_MAX_CENTS": "2.0",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
            "BTC15M_H02_ENTRY_MIN": "0.02",
            "BTC15M_H02_ENTRY_MAX": "0.50",
            "BTC15M_H02_MIN_SIDE_PROB": "0.60",
            "BTC15M_H02_MIN_VISIBLE_QTY": "250",
            "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "500",
            "BTC15M_H02_ALLOWED_SIDE": "",
            "BTC15M_H02_MAX_CONTRACTS": "1",
            "BTC15M_MAX_REPRICE_WORSE_CENTS": "2.0",
            "BTC15M_ORDER_CHASE_COOLDOWN_SEC": "0.0",
        },
        env_defaults={},
        required_argv_flags={"--mode": "paper", "--strategy": "h02", "--shadow-bankroll": "100"},
    ),
    PolicySpec(
        policy_id="btc15m_q250_firstskip_qty500_yes_forward",
        family="BTC15M",
        candidate="q250_firstskip_qty500_yes",
        ledger="btc15m_q250_qty500_firstskip_yes_shadow",
        wrapper_script=r"scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        role="primary_future_paper_policy",
        expected_env={
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
            "BTC15M_H02_SPREAD_MAX_CENTS": "2.0",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
            "BTC15M_H02_ENTRY_MIN": "0.02",
            "BTC15M_H02_ENTRY_MAX": "0.50",
            "BTC15M_H02_MIN_SIDE_PROB": "0.60",
            "BTC15M_H02_MIN_VISIBLE_QTY": "250",
            "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "500",
            "BTC15M_H02_ALLOWED_SIDE": "yes",
            "BTC15M_H02_MAX_CONTRACTS": "1",
            "BTC15M_MAX_REPRICE_WORSE_CENTS": "2.0",
            "BTC15M_ORDER_CHASE_COOLDOWN_SEC": "0.0",
        },
        env_defaults={},
        required_argv_flags={"--mode": "paper", "--strategy": "h02", "--shadow-bankroll": "100"},
    ),
    PolicySpec(
        policy_id="btc15m_q1000_yes_sparse_control",
        family="BTC15M",
        candidate="q1000_yes",
        ledger="btc15m_q1000_yes_shadow",
        wrapper_script=r"scripts\btc15m_f2_q1000_yes_shadow.py",
        role="existing_sparse_yes_control",
        expected_env={
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
            "BTC15M_H02_SPREAD_MAX_CENTS": "2.0",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
            "BTC15M_H02_ENTRY_MIN": "0.02",
            "BTC15M_H02_ENTRY_MAX": "0.50",
            "BTC15M_H02_MIN_SIDE_PROB": "0.60",
            "BTC15M_H02_MIN_VISIBLE_QTY": "1000",
            "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "0.0",
            "BTC15M_H02_ALLOWED_SIDE": "yes",
            "BTC15M_H02_MAX_CONTRACTS": "1",
            "BTC15M_MAX_REPRICE_WORSE_CENTS": "2.0",
            "BTC15M_ORDER_CHASE_COOLDOWN_SEC": "0.0",
        },
        env_defaults={"BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "0.0"},
        required_argv_flags={"--mode": "paper", "--strategy": "h02", "--shadow-bankroll": "100"},
    ),
    PolicySpec(
        policy_id="btc1h_high_conf80_entry70_no_chase_observe",
        family="BTC1H",
        candidate="btc1h_high_conf80_entry70_no_chase",
        ledger="btc1h_high_conf80_entry70_no_chase_shadow",
        wrapper_script=r"scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py",
        role="observe_only_runner_up",
        expected_env={
            "BTC_1HR_EXECUTOR_NAME": "btc_1hr_high_conf80_entry70_no_chase_shadow",
            "BTC_1HR_SIGNAL_STRATEGY": "high_conf_80_entry70_no_chase",
            "BTC_1HR_SIZING_POLICY": "flat_max",
            "BTC_1HR_MIN_TTL_MIN": "5",
            "BTC_1HR_MAX_TTL_MIN": "20",
            "BTC_1HR_MAX_CONTRACTS_PER_TRADE": "1",
        },
        env_defaults={},
        required_argv_flags={},
        required_text=(
            '"--paper"',
            '"--signal-strategy", STRATEGY',
            '"--sizing-policy", "flat_max"',
            '"--shadow-bankroll", "100"',
            '"--db-path"',
            '"--capture-db-path"',
            '"--signal-strategy"',
            '"--sizing-policy"',
        ),
        forbidden_text=("--live",),
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BTC frozen paper-shadow policy parity.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def literal_value(node: ast.AST, constants: dict[str, str] | None = None) -> str | None:
    constants = constants or {}
    if isinstance(node, ast.Constant):
        return "" if node.value is None else str(node.value)
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def env_key(node: ast.AST, constants: dict[str, str] | None = None) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    target = node.value
    if not (
        isinstance(target, ast.Attribute)
        and target.attr == "environ"
        and isinstance(target.value, ast.Name)
        and target.value.id == "os"
    ):
        return None
    return literal_value(node.slice, constants)


def extract_name_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = literal_value(node.value, constants)
        if value is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    return constants


def extract_env_assignments(tree: ast.AST, constants: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = literal_value(node.value, constants)
        if value is None:
            continue
        for target in node.targets:
            key = env_key(target, constants)
            if key:
                env[key] = value
    return env


def literal_list(node: ast.AST, constants: dict[str, str]) -> list[str] | None:
    if not isinstance(node, ast.List):
        return None
    out: list[str] = []
    for item in node.elts:
        value = literal_value(item, constants)
        if value is None:
            value = "<expr>"
        out.append(value)
    return out


def extract_sys_argv(tree: ast.AST, constants: dict[str, str]) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "argv"
                and isinstance(target.value, ast.Name)
                and target.value.id == "sys"
            ):
                values = literal_list(node.value, constants)
                if values:
                    out = values
    return out


def argv_value(argv: list[str], flag: str) -> str | None:
    for idx, item in enumerate(argv):
        if item == flag and idx + 1 < len(argv):
            return argv[idx + 1]
        if item.startswith(f"{flag}="):
            return item.split("=", 1)[1]
    return None


def norm(value: str) -> str:
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text.lower()
    return f"{number:.10g}"


def audit_policy(spec: PolicySpec, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    path = project_root / spec.wrapper_script
    row: dict[str, Any] = {
        "policy_id": spec.policy_id,
        "family": spec.family,
        "candidate": spec.candidate,
        "ledger": spec.ledger,
        "role": spec.role,
        "wrapper_script": spec.wrapper_script,
        "wrapper_exists": path.exists(),
        "policy_parity_pass": False,
        "policy_parity_status": "FAIL_POLICY_PARITY",
        "policy_parity_blockers": "",
        "observed_env_json": "",
        "observed_argv_json": "",
    }
    if not path.exists():
        row["policy_parity_blockers"] = "wrapper_missing"
        return row

    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        row["policy_parity_blockers"] = f"wrapper_syntax_error:{exc}"
        return row

    constants = extract_name_constants(tree)
    env = extract_env_assignments(tree, constants)
    argv = extract_sys_argv(tree, constants)
    row["observed_env_json"] = json.dumps(env, sort_keys=True)
    row["observed_argv_json"] = json.dumps(argv)

    blockers: list[str] = []
    for key, expected in spec.expected_env.items():
        observed = env.get(key, spec.env_defaults.get(key))
        if observed is None:
            blockers.append(f"missing_env:{key}")
        elif norm(observed) != norm(expected):
            blockers.append(f"env_mismatch:{key}:expected={expected}:observed={observed}")

    for flag, expected in spec.required_argv_flags.items():
        observed = argv_value(argv, flag)
        if observed is None:
            blockers.append(f"missing_argv:{flag}")
        elif norm(observed) != norm(expected):
            blockers.append(f"argv_mismatch:{flag}:expected={expected}:observed={observed}")

    for needle in spec.required_text:
        if needle not in source:
            blockers.append(f"missing_required_text:{needle}")
    for needle in spec.forbidden_text:
        if needle in source:
            blockers.append(f"forbidden_text:{needle}")

    if not blockers:
        row["policy_parity_pass"] = True
        row["policy_parity_status"] = "PASS_FROZEN_POLICY_PARITY"
    row["policy_parity_blockers"] = ";".join(blockers)
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = [audit_policy(spec) for spec in POLICIES]
    pass_count = sum(1 for row in rows if bool(row["policy_parity_pass"]))
    all_pass = pass_count == len(rows)
    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Read-only frozen-policy parity audit. Does not start, stop, tune, trade, or deploy.",
        "policy_count": len(rows),
        "policy_parity_pass_count": pass_count,
        "all_policy_parity_pass": all_pass,
    }

    write_csv(args.out_dir / "frozen_policy_parity_summary.csv", rows)
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Frozen Policy Parity Audit",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        f"All policy parity pass: `{all_pass}`",
        "",
        "## Summary",
        "",
    ]
    for row in rows:
        report.append(
            f"- {row['policy_parity_status']}: `{row['candidate']}` via `{row['wrapper_script']}`"
            + (f" blockers=`{row['policy_parity_blockers']}`" if row["policy_parity_blockers"] else "")
        )
    report.extend(
        [
            "",
            "## Rule",
            "",
            (
                "Future paper rows are not promotion evidence unless the running wrapper matches "
                "the frozen policy audited here, in addition to official settlement, execution "
                "realism, row reconciliation, clean schemas, and post-restart evidence-clock gates."
            ),
            "",
            "```json",
            json.dumps(run_info, indent=2, sort_keys=True),
            "```",
        ]
    )
    (args.out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
