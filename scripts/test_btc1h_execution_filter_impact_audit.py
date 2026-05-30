#!/usr/bin/env python3
"""Tests for BTC1H execution-filter impact audit."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_execution_filter_impact_audit import build_audit


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


def test_execution_filter_removes_stale_quote_without_promoting(tmp_path: Path) -> None:
    flags = tmp_path / "rows.csv"
    official = tmp_path / "official.csv"
    write_csv(
        flags,
        [
            {
                "event_ticker": "E1",
                "market_ticker": "M1",
                "side": "no",
                "entry_price": 0.66,
                "quote_age_ms": 20.0,
                "top_visible_qty": 10,
                "required_fields_present": "True",
                "entry_matches_side_ask": "True",
                "actual_entry_matches_entry": "True",
                "fee_present_nonnegative": "True",
                "top_visible_qty_ge_contracts": "True",
                "quote_age_le_limit": "True",
                "spread_le_limit": "True",
            },
            {
                "event_ticker": "E2",
                "market_ticker": "M2",
                "side": "no",
                "entry_price": 0.68,
                "quote_age_ms": 260.0,
                "top_visible_qty": 75,
                "required_fields_present": "True",
                "entry_matches_side_ask": "True",
                "actual_entry_matches_entry": "True",
                "fee_present_nonnegative": "True",
                "top_visible_qty_ge_contracts": "True",
                "quote_age_le_limit": "False",
                "spread_le_limit": "True",
            },
        ],
    )
    write_csv(
        official,
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "event_ticker": "E1",
                "market_ticker": "M1",
                "side": "no",
                "official_pnl": -0.7,
                "proxy_pnl": 0.3,
                "official_win": "False",
                "official_proxy_result_mismatch": "True",
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "event_ticker": "E2",
                "market_ticker": "M2",
                "side": "no",
                "official_pnl": 0.3,
                "proxy_pnl": 0.3,
                "official_win": "True",
                "official_proxy_result_mismatch": "False",
            },
        ],
    )

    profiles, removed, summary = build_audit(
        argparse.Namespace(
            out_dir=tmp_path / "out",
            row_flags=flags,
            official_trades=official,
            min_clean_official_rows=50,
        )
    )
    by_profile = {row["profile"]: row for row in profiles}

    assert by_profile["all_snapshot_rows"]["official_pnl"] == -0.4
    assert by_profile["strict_execution_filtered_rows"]["rows"] == 1
    assert by_profile["strict_execution_filtered_rows"]["official_pnl"] == -0.7
    assert by_profile["strict_execution_filtered_rows"]["official_proxy_mismatches"] == 1
    assert by_profile["strict_execution_filtered_rows"]["removed_markets"] == "M2"
    assert removed[0]["market_ticker"] == "M2"
    assert removed[0]["removal_reasons"] == "quote_age_above_limit"
    assert summary["strict_removed_rows"] == 1
    assert summary["strict_official_rows"] == 1
    assert summary["deployable_now"] is False
    assert summary["near_deployable_candidate"] is False
    assert "too_few_execution_filtered_official_rows" in summary["blockers"]
    assert "official_proxy_mismatch_remaining" in summary["blockers"]
    assert "old_snapshot_not_clean_clock_promotion_evidence" in summary["blockers"]
