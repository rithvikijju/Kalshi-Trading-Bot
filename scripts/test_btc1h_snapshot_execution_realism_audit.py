#!/usr/bin/env python3
"""Tests for BTC1H snapshot execution-realism audit."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from scripts.build_btc1h_snapshot_execution_realism_audit import build_audit


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


def write_ledger(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        create table research_live_trades (
            created_at text,
            status text,
            event_ticker text,
            market_ticker text,
            side text,
            contracts integer,
            entry_price real,
            entry_fee_estimate real,
            actual_entry_price real,
            actual_fee_paid real,
            top_visible_qty real,
            quote_age_ms real,
            quote_received_at_ns integer,
            signal_received_at_ns integer,
            yes_bid real,
            yes_ask real,
            no_bid real,
            no_ask real,
            spread_cents real,
            ttl_min real,
            model_p_yes real,
            net_edge_cents real
        )
        """
    )
    rows = [
        (
            "2026-05-19T00:00:00+00:00",
            "paper_filled",
            "E1",
            "M1",
            "no",
            1,
            0.66,
            0.02,
            0.66,
            0.02,
            200.0,
            20.0,
            100,
            120,
            0.34,
            0.36,
            0.64,
            0.66,
            2.0,
            10.0,
            0.2,
            12.0,
        ),
        (
            "2026-05-19T01:00:00+00:00",
            "paper_filled",
            "E2",
            "M2",
            "yes",
            1,
            0.66,
            0.02,
            0.66,
            0.02,
            1.0,
            260.0,
            200,
            220,
            0.65,
            0.66,
            0.34,
            0.35,
            1.0,
            11.0,
            0.82,
            14.0,
        ),
    ]
    con.executemany("insert into research_live_trades values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_snapshot_execution_audit_is_diagnostic_not_promotion(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    official = tmp_path / "official.csv"
    write_ledger(ledger)
    write_csv(
        official,
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "official_status": "finalized",
                "official_pnl": 0.32,
                "official_proxy_result_mismatch": "False",
                "model_policy_version": "",
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "official_status": "finalized",
                "official_pnl": -0.66,
                "official_proxy_result_mismatch": "True",
                "model_policy_version": "",
            },
        ],
    )
    row_flags, summary = build_audit(
        argparse.Namespace(
            out_dir=tmp_path / "out",
            ledger_db=ledger,
            official_trades=official,
            max_quote_age_ms=250.0,
            max_spread_cents=2.0,
        )
    )

    assert len(row_flags) == 2
    assert summary["rows"] == 2
    assert summary["official_rows"] == 2
    assert summary["official_proxy_mismatches"] == 1
    assert summary["blank_policy_rows"] == 2
    assert summary["required_field_complete_rate"] == 1.0
    assert summary["entry_matches_side_ask_rate"] == 1.0
    assert summary["top_visible_qty_ge_contracts_rate"] == 1.0
    assert summary["quote_age_le_limit_rate"] == 0.5
    assert summary["stale_quote_rows"] == 1
    assert summary["promotion_usable"] is False
    assert "quote_age_above_limit" in summary["blockers"]
    assert "pre_clean_clock_blank_policy_rows" in summary["blockers"]
    assert "old_snapshot_not_clean_clock_promotion_evidence" in summary["blockers"]
