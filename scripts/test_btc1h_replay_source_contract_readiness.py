#!/usr/bin/env python3
"""Tests for BTC1H replay source-contract readiness audit."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_replay_source_contract_readiness import build_readiness
from scripts.build_btc1h_faithful_replay_data_contract import FIELD_CONTRACTS


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
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def args(tmp: Path) -> argparse.Namespace:
    return argparse.Namespace(
        out_dir=tmp / "out",
        live_source=tmp / "live.py",
        materializer_source=tmp / "materializer.py",
        faithful_replay_data_contract_summary=tmp / "contract.csv",
    )


def fields_by_table() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for table, field, _role, _why in FIELD_CONTRACTS:
        out.setdefault(table, [])
        if field not in out[table]:
            out[table].append(field)
    return out


def write_live_source(path: Path, *, omit_capture: str = "", omit_recorder: str = "") -> None:
    by_table = fields_by_table()
    schema_parts = []
    for table, fields in by_table.items():
        kept = [field for field in fields if f"{table}.{field}" != omit_capture]
        schema_parts.append(f"{table!r}: {[ (field, 'VARCHAR') for field in kept ]!r}")
    signal_fields = [field for field in by_table["signal_scan"] if f"signal_scan.{field}" != omit_recorder]
    decision_fields = [field for field in by_table["order_decision"] if f"order_decision.{field}" != omit_recorder]
    signal_dict = "{" + ", ".join(f"{field!r}: None" for field in signal_fields) + "}"
    decision_dict = "{" + ", ".join(f"{field!r}: None" for field in decision_fields) + "}"
    path.write_text(
        "\n".join(
            [
                "CAPTURE_SCHEMAS = {" + ", ".join(schema_parts) + "}",
                "class Runner:",
                "    def __init__(self):",
                "        self.recorder = None",
                "    def _record_scan(self):",
                f"        self.recorder.record('signal_scan', {signal_dict})",
                "    def _record_decision(self):",
                f"        self.recorder.record('order_decision', {decision_dict})",
            ]
        ),
        encoding="utf-8",
    )


def write_materializer_source(path: Path, *, omit_table_col: str = "") -> None:
    by_table = fields_by_table()
    all_fields: list[str] = []
    for fields in by_table.values():
        for field in fields:
            if field not in all_fields:
                all_fields.append(field)
    lines = [f"SIDECAR_COLUMNS = {all_fields!r}", "def main():"]
    for table, fields in by_table.items():
        kept = [field for field in fields if f"{table}.{field}" != omit_table_col]
        lines.append(f"    create_if_present(None, {table!r}, {kept!r})")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_contract_summary(path: Path, supports: bool) -> None:
    write_csv(
        path,
        [
            {
                "contract_status": "READY" if supports else "BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS",
                "current_artifacts_can_support_faithful_replay": str(supports),
            }
        ],
    )


def test_source_ready_but_current_rows_remain_blocked(tmp_path: Path) -> None:
    write_live_source(tmp_path / "live.py")
    write_materializer_source(tmp_path / "materializer.py")
    write_contract_summary(tmp_path / "contract.csv", supports=False)

    rows, summary = build_readiness(args(tmp_path))

    assert summary["source_contract_status"] == "SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED"
    assert summary["source_missing_field_count"] == 0
    assert summary["current_source_contract_ready"] is True
    assert summary["current_artifacts_can_support_faithful_replay"] is False
    assert summary["requires_explicit_authorization_to_collect"] is True
    assert summary["requires_process_control_to_collect"] is True
    assert summary["deployable_now"] is False
    assert all(row["source_contract_ready"] for row in rows)


def test_audit_catches_materializer_table_gap(tmp_path: Path) -> None:
    write_live_source(tmp_path / "live.py")
    write_materializer_source(tmp_path / "materializer.py", omit_table_col="ws_lifecycle.open_ts")
    write_contract_summary(tmp_path / "contract.csv", supports=False)

    rows, summary = build_readiness(args(tmp_path))

    assert summary["source_contract_status"] == "SOURCE_MISSING_FAITHFUL_REPLAY_CONTRACT_FIELDS"
    assert summary["source_missing_field_count"] == 1
    assert summary["materializer_table_column_missing_count"] == 1
    assert summary["source_missing_fields"] == "ws_lifecycle.open_ts"
    missing = [row for row in rows if row["field_id"] == "ws_lifecycle.open_ts"][0]
    assert missing["missing_source_components"] == "materializer_table_columns"


def test_audit_catches_recorder_payload_gap(tmp_path: Path) -> None:
    write_live_source(tmp_path / "live.py", omit_recorder="signal_scan.quote_age_ms")
    write_materializer_source(tmp_path / "materializer.py")
    write_contract_summary(tmp_path / "contract.csv", supports=False)

    _rows, summary = build_readiness(args(tmp_path))

    assert summary["source_contract_status"] == "SOURCE_MISSING_FAITHFUL_REPLAY_CONTRACT_FIELDS"
    assert summary["recorder_payload_missing_count"] == 1
    assert summary["source_missing_fields"] == "signal_scan.quote_age_ms"
