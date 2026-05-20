#!/usr/bin/env python3
"""Audit BTC15M paper-shadow wrapper configs against frozen replay specs.

This is a read-only deployment-control artifact. It proves whether the paper
shadow wrappers that would be started/restarted later actually match the frozen
live-websocket replay thresholds they are supposed to collect forward evidence
for. It does not start processes, tune thresholds, or promote a strategy.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_shadow_replay_config_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


LOWDD_ENV_DEFAULTS: dict[str, str] = {
    "BTC15M_SIGNAL_STRATEGY": "h02",
    "BTC15M_H02_TTL_LO": "2.0",
    "BTC15M_H02_TTL_HI": "8.0",
    "BTC15M_H02_SPREAD_MAX_CENTS": "2.0",
    "BTC15M_H02_EDGE_THRESHOLD_CENTS": "10.0",
    "BTC15M_H02_ENTRY_MIN": "0.01",
    "BTC15M_H02_ENTRY_MAX": "0.99",
    "BTC15M_H02_MIN_SIDE_PROB": "0.0",
    "BTC15M_H02_MIN_VISIBLE_QTY": "1.0",
    "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "0.0",
    "BTC15M_H02_ALLOWED_SIDE": "",
    "BTC15M_H02_BTC_MAX_AGE_SEC": "10.0",
    "BTC15M_H02_MAX_CONTRACTS": "1",
    "BTC15M_MAX_REPRICE_WORSE_CENTS": "2.0",
}


@dataclass(frozen=True)
class CandidateSpec:
    candidate: str
    wrapper: Path
    replay_dir: Path
    expected_side: str
    expected_first_signal_qty: float | None


CANDIDATES = [
    CandidateSpec(
        candidate="q250_firstskip_qty500",
        wrapper=PROJECT_ROOT / "scripts" / "btc15m_f2_q250_qty500_firstskip_shadow.py",
        replay_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_causal_latest_codex",
        expected_side="both",
        expected_first_signal_qty=500.0,
    ),
    CandidateSpec(
        candidate="q250_firstskip_qty500_yes",
        wrapper=PROJECT_ROOT / "scripts" / "btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
        replay_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_causal_latest_codex",
        expected_side="yes",
        expected_first_signal_qty=500.0,
    ),
    CandidateSpec(
        candidate="q1000_yes",
        wrapper=PROJECT_ROOT / "scripts" / "btc15m_f2_q1000_yes_shadow.py",
        replay_dir=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_causal_latest_codex",
        expected_side="yes",
        expected_first_signal_qty=None,
    ),
]


FIELD_MAP = [
    ("strategy", "BTC15M_SIGNAL_STRATEGY", None, "h02"),
    ("ttl_min", "BTC15M_H02_TTL_LO", "ttl_min", None),
    ("ttl_max", "BTC15M_H02_TTL_HI", "ttl_max", None),
    ("spread_max_cents", "BTC15M_H02_SPREAD_MAX_CENTS", "spread_max_cents", None),
    ("edge_cents_min", "BTC15M_H02_EDGE_THRESHOLD_CENTS", "edge_cents_min", None),
    ("entry_min", "BTC15M_H02_ENTRY_MIN", "entry_min", None),
    ("entry_max", "BTC15M_H02_ENTRY_MAX", "entry_max", None),
    ("fair_p_min", "BTC15M_H02_MIN_SIDE_PROB", "fair_p_min", None),
    ("visible_qty_min", "BTC15M_H02_MIN_VISIBLE_QTY", "visible_qty_min", None),
    ("first_signal_visible_qty_min", "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY", "first_signal_visible_qty_min", None),
    ("side", "BTC15M_H02_ALLOWED_SIDE", "side", None),
    ("max_btc_spot_age_sec", "BTC15M_H02_BTC_MAX_AGE_SEC", "max_btc_spot_age_sec", 10.0),
    ("max_contracts", "BTC15M_H02_MAX_CONTRACTS", None, 1.0),
    ("max_reprice_worse_cents", "BTC15M_MAX_REPRICE_WORSE_CENTS", None, 2.0),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit BTC15M paper-shadow configs against frozen replay specs.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--restart-script", type=Path, default=PROJECT_ROOT / "scripts" / "restart_btc_paper_shadows.ps1")
    return p.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_env_setdefaults(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "setdefault"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[1], ast.Constant)
            ):
                out[str(node.args[0].value)] = str(node.args[1].value)
            continue

        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.value, ast.Constant):
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "environ"
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "os"
            and isinstance(target.slice, ast.Constant)
        ):
            continue
        out[str(target.slice.value)] = str(node.value.value)
    return out


def parse_wrapper_flags(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return {
        "paper_mode_locked": bool(re.search(r'"--mode"\s*,\s*"paper"', text)),
        "contains_live_mode": bool(re.search(r'"--mode"\s*,\s*"live"', text)),
        "has_shadow_bankroll": '"--shadow-bankroll"' in text,
    }


def normalize_side(value: Any) -> str:
    text = "" if value is None else str(value).strip().lower()
    if text in {"", "both", "none", "nan"}:
        return "both"
    return text


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def equal_values(name: str, expected: Any, actual: Any) -> bool:
    if name == "side":
        return normalize_side(expected) == normalize_side(actual)
    if expected is None:
        return actual is None or numeric(actual) in {None, 0.0}
    exp_num = numeric(expected)
    act_num = numeric(actual)
    if exp_num is not None and act_num is not None:
        return abs(exp_num - act_num) <= 1e-9
    return str(expected).strip().lower() == str(actual).strip().lower()


def wrapper_value(env: dict[str, str], key: str) -> tuple[Any, str]:
    if key in env:
        return env[key], "wrapper_literal_or_setdefault"
    return LOWDD_ENV_DEFAULTS.get(key), "btc15m_lowdd_live_default"


def expected_value(spec: CandidateSpec, replay: dict[str, Any], replay_key: str | None, fallback: Any) -> tuple[Any, str]:
    if replay_key and replay_key in replay:
        value = replay.get(replay_key)
        if replay_key == "side":
            return normalize_side(value), "replay_run_info"
        return value, "replay_run_info"
    if replay_key == "first_signal_visible_qty_min":
        return spec.expected_first_signal_qty, "frozen_candidate_spec"
    if replay_key == "side":
        return spec.expected_side, "frozen_candidate_spec"
    return fallback, "frozen_or_engine_default"


def audit_candidate(spec: CandidateSpec, restart_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    env = parse_env_setdefaults(spec.wrapper)
    flags = parse_wrapper_flags(spec.wrapper)
    replay = read_json(spec.replay_dir / "run_info.json")
    rows: list[dict[str, Any]] = []

    for field_name, env_key, replay_key, fallback in FIELD_MAP:
        expected, expected_source = expected_value(spec, replay, replay_key, fallback)
        actual, actual_source = wrapper_value(env, env_key)
        if field_name == "strategy":
            expected = "h02"
        status = "PASS" if equal_values(field_name, expected, actual) else "FAIL"
        rows.append(
            {
                "candidate": spec.candidate,
                "check": field_name,
                "env_key": env_key,
                "expected": "" if expected is None else expected,
                "expected_source": expected_source,
                "wrapper_value": "" if actual is None else actual,
                "wrapper_value_source": actual_source,
                "status": status,
            }
        )

    safety_checks = {
        "paper_mode_locked": flags["paper_mode_locked"],
        "no_live_mode_argv": not flags["contains_live_mode"],
        "has_shadow_bankroll": flags["has_shadow_bankroll"],
        "restart_script_includes_target": str(spec.wrapper.relative_to(PROJECT_ROOT)).replace("/", "\\") in restart_text,
        "replay_run_info_exists": bool(replay),
    }
    for check, ok in safety_checks.items():
        rows.append(
            {
                "candidate": spec.candidate,
                "check": check,
                "env_key": "",
                "expected": True,
                "expected_source": "safety_invariant",
                "wrapper_value": bool(ok),
                "wrapper_value_source": "source_or_artifact",
                "status": "PASS" if ok else "FAIL",
            }
        )

    fail_count = sum(1 for row in rows if row["status"] != "PASS")
    summary = {
        "candidate": spec.candidate,
        "wrapper": str(spec.wrapper),
        "replay_dir": str(spec.replay_dir),
        "config_checks": len(rows),
        "failed_checks": fail_count,
        "policy_matches_frozen_replay": fail_count == 0,
        "paper_mode_locked": flags["paper_mode_locked"],
        "restart_script_includes_target": safety_checks["restart_script_includes_target"],
        "replay_raw_hits": replay.get("f2_raw_hits", ""),
        "replay_first_signals": replay.get("f2_first_signals", ""),
        "replay_closed_proxy_rows": replay.get("f2_signals_closed_with_proxy", ""),
        "promotion_meaning": (
            "Wrapper/replay config match only proves the future paper shadow would collect the intended frozen policy; "
            "it does not make old replay rows or stale ledger rows deployable."
        ),
    }
    return summary, rows


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    restart_text = args.restart_script.read_text(encoding="utf-8") if args.restart_script.exists() else ""

    summaries: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for spec in CANDIDATES:
        summary, rows = audit_candidate(spec, restart_text)
        summaries.append(summary)
        checks.extend(rows)

    summary_df = pd.DataFrame(summaries)
    checks_df = pd.DataFrame(checks)
    summary_df.to_csv(args.out_dir / "shadow_replay_config_summary.csv", index=False)
    checks_df.to_csv(args.out_dir / "shadow_replay_config_checks.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "restart_script": str(args.restart_script),
        "candidates": [spec.candidate for spec in CANDIDATES],
        "note": "Read-only config-equivalence audit. Passing does not start a process, tune a threshold, or authorize deployment.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC15M Shadow Replay Config Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        summary_df.to_string(index=False),
        "",
        "## Failed Checks",
        "",
        (
            checks_df[checks_df["status"].ne("PASS")].to_string(index=False)
            if not checks_df[checks_df["status"].ne("PASS")].empty
            else "No failed checks."
        ),
        "",
        "## Interpretation",
        "",
        "- Passing this audit means the wrapper and restart script are configured to collect the frozen replay policy.",
        "- It does not count old replay rows, stale-schema paper rows, or non-running q250 YES-only rows as promotion evidence.",
        "- Deployment still requires official-settled post-restart paper rows with execution-realism fields and live/paper agreement.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0 if int(summary_df["failed_checks"].sum()) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
