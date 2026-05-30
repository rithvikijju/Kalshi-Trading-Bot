#!/usr/bin/env python3
"""Audit current BTC1H source readiness for the faithful-replay contract.

The live sidecar currently lacks fields required for deployable replay parity.
This script answers a different, narrower question: would the current checked
out source code emit and materialize those fields after an explicitly
authorized future clean-clock restart?  It is a static/read-only audit and does
not start, stop, restart, migrate, deploy, or tune anything.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_replay_source_contract_readiness_latest_codex"
VARIANT = "high_conf_80_entry70_no_chase"
RECORDER_REQUIRED_TABLES = {"signal_scan", "order_decision"}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_btc1h_faithful_replay_data_contract import FIELD_CONTRACTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--live-source", type=Path, default=PROJECT_ROOT / "scripts" / "btc_1hr_research_live.py")
    parser.add_argument(
        "--materializer-source",
        type=Path,
        default=PROJECT_ROOT / "scripts" / "materialize_btc_replay_sidecar.py",
    )
    parser.add_argument(
        "--faithful-replay-data-contract-summary",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_faithful_replay_data_contract_latest_codex"
        / "btc1h_faithful_replay_data_contract_summary.csv",
    )
    return parser.parse_args()


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


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass"}


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return ";".join(out)


def parse_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    return None


def capture_schema_fields(tree: ast.Module) -> dict[str, set[str]]:
    raw = literal_assignment(tree, "CAPTURE_SCHEMAS") or {}
    out: dict[str, set[str]] = {}
    for table, columns in raw.items():
        out[str(table)] = {str(name) for name, _typ in columns}
    return out


def recorder_payload_fields(tree: ast.Module) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "record"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "recorder"
        ):
            continue
        if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[1], ast.Dict):
            continue
        table = str(node.args[0].value)
        fields = out.setdefault(table, set())
        for key in node.args[1].keys:
            if isinstance(key, ast.Constant):
                fields.add(str(key.value))
    return out


def materializer_sidecar_columns(tree: ast.Module) -> set[str]:
    raw = literal_assignment(tree, "SIDECAR_COLUMNS") or []
    return {str(item) for item in raw}


def list_literal(node: ast.AST) -> list[str]:
    try:
        raw = ast.literal_eval(node)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def materializer_table_columns(tree: ast.Module) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "create_if_present"):
            continue
        if len(node.args) < 3 or not isinstance(node.args[1], ast.Constant):
            continue
        table = str(node.args[1].value)
        out[table] = set(list_literal(node.args[2]))
    return out


def build_readiness(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    live_tree = parse_tree(args.live_source)
    materializer_tree = parse_tree(args.materializer_source)
    capture_fields = capture_schema_fields(live_tree)
    recorder_fields = recorder_payload_fields(live_tree)
    sidecar_columns = materializer_sidecar_columns(materializer_tree)
    materializer_fields = materializer_table_columns(materializer_tree)
    data_contract_rows = read_csv(args.faithful_replay_data_contract_summary)
    data_contract = data_contract_rows[0] if data_contract_rows else {}

    rows: list[dict[str, Any]] = []
    for table, field, role, reason in FIELD_CONTRACTS:
        capture_has = field in capture_fields.get(table, set())
        recorder_required = table in RECORDER_REQUIRED_TABLES
        recorder_has = (field in recorder_fields.get(table, set())) if recorder_required else True
        sidecar_has = field in sidecar_columns
        materializer_has = field in materializer_fields.get(table, set())
        source_ready = capture_has and recorder_has and sidecar_has and materializer_has
        rows.append(
            {
                "table": table,
                "field": field,
                "field_id": f"{table}.{field}",
                "variant": VARIANT,
                "role": role,
                "capture_schema_has_field": capture_has,
                "recorder_payload_required": recorder_required,
                "recorder_payload_has_field": recorder_has,
                "materializer_sidecar_column_supported": sidecar_has,
                "materializer_table_column_supported": materializer_has,
                "source_contract_ready": source_ready,
                "missing_source_components": unique_join(
                    [
                        "" if capture_has else "capture_schema",
                        "" if recorder_has else "recorder_payload",
                        "" if sidecar_has else "materializer_raw_sidecar_columns",
                        "" if materializer_has else "materializer_table_columns",
                    ]
                ),
                "why_required": reason,
            }
        )

    missing_rows = [row for row in rows if not to_bool(row["source_contract_ready"])]
    missing_ids = [row["field_id"] for row in missing_rows]
    source_ready = not missing_rows
    current_artifacts_support = to_bool(data_contract.get("current_artifacts_can_support_faithful_replay", ""))
    current_rows_blocked = not current_artifacts_support
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": VARIANT,
        "source_contract_status": (
            "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED"
            if source_ready and current_rows_blocked
            else "SOURCE_READY_CURRENT_ARTIFACTS_SUPPORT_CONTRACT"
            if source_ready
            else "SOURCE_MISSING_FAITHFUL_REPLAY_CONTRACT_FIELDS"
        ),
        "required_field_count": len(rows),
        "source_ready_field_count": len(rows) - len(missing_rows),
        "source_missing_field_count": len(missing_rows),
        "source_missing_fields": unique_join(missing_ids),
        "capture_schema_missing_count": sum(1 for row in rows if not to_bool(row["capture_schema_has_field"])),
        "recorder_payload_missing_count": sum(
            1
            for row in rows
            if to_bool(row["recorder_payload_required"]) and not to_bool(row["recorder_payload_has_field"])
        ),
        "materializer_sidecar_column_missing_count": sum(
            1 for row in rows if not to_bool(row["materializer_sidecar_column_supported"])
        ),
        "materializer_table_column_missing_count": sum(
            1 for row in rows if not to_bool(row["materializer_table_column_supported"])
        ),
        "current_source_contract_ready": source_ready,
        "current_data_contract_status": data_contract.get("contract_status", ""),
        "current_artifacts_can_support_faithful_replay": current_artifacts_support,
        "current_rows_remain_blocked_until_clean_clock": current_rows_blocked,
        "deployable_now": False,
        "near_deployable_now": False,
        "requires_explicit_authorization_to_collect": current_rows_blocked,
        "requires_process_control_to_collect": current_rows_blocked,
        "no_process_action_taken": True,
        "next_action": (
            "Source/materializer readiness is necessary but not promotion evidence. Count future rows only after "
            "explicit authorization starts a clean evidence clock and the refreshed sidecar schema proves these "
            "fields are populated."
            if source_ready
            else "Patch the source/materializer gaps before any future clean-clock collection attempt."
        ),
    }
    return rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    missing_rows = [row for row in rows if not to_bool(row["source_contract_ready"])]
    columns = ["field_id", "missing_source_components", "why_required"]
    return "\n".join(
        [
            "# BTC1H Replay Source Contract Readiness",
            "",
            f"Created UTC: `{summary['created_at_utc']}`",
            f"Source contract status: `{summary['source_contract_status']}`",
            f"Current source contract ready: `{summary['current_source_contract_ready']}`",
            f"Source missing fields: `{summary['source_missing_field_count']}`",
            f"Current artifacts can support faithful replay: `{summary['current_artifacts_can_support_faithful_replay']}`",
            f"No process action taken: `{summary['no_process_action_taken']}`",
            "",
            "## Missing Source Components",
            "",
            markdown_table(missing_rows, columns),
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(summary, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = build_readiness(args)
    write_csv(args.out_dir / "btc1h_replay_source_contract_fields.csv", rows)
    write_csv(args.out_dir / "btc1h_replay_source_contract_summary.csv", [summary])
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(rows, summary), encoding="utf-8")
    print((args.out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
