#!/usr/bin/env python3
"""Tests for BTC1H execution-filtered basis mismatch audit."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_execution_filtered_basis_mismatch_audit import build_audit


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


def test_strict_execution_filtered_mismatch_remains_diagnostic(tmp_path: Path) -> None:
    flags = tmp_path / "rows.csv"
    official = tmp_path / "official.csv"
    write_csv(
        flags,
        [
            {
                "event_ticker": "E1",
                "market_ticker": "M1",
                "side": "no",
                "entry_price": 0.68,
                "quote_age_ms": 72.0,
                "top_visible_qty": 881,
                "spread_cents": 1.0,
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
                "spread_cents": 1.0,
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
                "entry_price": 0.68,
                "official_result": "yes",
                "proxy_result": "no",
                "official_pnl": -0.7,
                "proxy_pnl": 0.3,
                "official_minus_proxy_spot": 42.15,
                "official_minus_proxy_bps": 5.45,
                "proxy_close_minus_strike": -8.37,
                "official_expiration_minus_strike": 33.78,
                "official_proxy_result_mismatch": "True",
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "event_ticker": "E2",
                "market_ticker": "M2",
                "side": "no",
                "entry_price": 0.68,
                "official_result": "no",
                "proxy_result": "no",
                "official_pnl": 0.3,
                "proxy_pnl": 0.3,
                "official_minus_proxy_spot": 3.0,
                "proxy_close_minus_strike": -40.0,
                "official_expiration_minus_strike": -37.0,
                "official_proxy_result_mismatch": "False",
            },
        ],
    )

    mismatch_rows, strict_rows, summary = build_audit(
        argparse.Namespace(
            out_dir=tmp_path / "out",
            row_flags=flags,
            official_trades=official,
            min_clean_official_rows=50,
            watch_boundary_usd=50.0,
        )
    )

    assert len(strict_rows) == 1
    assert len(mismatch_rows) == 1
    assert mismatch_rows[0]["market_ticker"] == "M1"
    assert mismatch_rows[0]["proxy_side_margin_usd"] == 8.37
    assert mismatch_rows[0]["official_side_margin_usd"] == -33.78
    assert mismatch_rows[0]["adverse_basis_usd"] == 42.15
    assert mismatch_rows[0]["basis_consumed_proxy_margin"] == 5.0358422939
    assert mismatch_rows[0]["near_either_boundary"] is True
    assert summary["strict_official_rows"] == 1
    assert summary["strict_official_pnl"] == -0.7
    assert summary["strict_proxy_pnl"] == 0.3
    assert summary["strict_official_proxy_mismatches"] == 1
    assert summary["mismatch_removed_by_execution_filter"] is False
    assert summary["strict_mismatch_markets"] == "M1"
    assert summary["audit_status"] == "DIAGNOSTIC_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH_REMAINS"
    assert summary["basis_guard_deployable_now"] is False
    assert "single_mismatch_after_execution_filter" in summary["blockers"]
    assert "too_few_strict_official_rows" in summary["blockers"]
    assert "guard_not_fit_from_current_rows" in summary["blockers"]


def test_mismatch_removed_by_execution_filter_is_reported(tmp_path: Path) -> None:
    flags = tmp_path / "rows.csv"
    official = tmp_path / "official.csv"
    write_csv(
        flags,
        [
            {
                "event_ticker": "E1",
                "market_ticker": "M1",
                "side": "no",
                "required_fields_present": "True",
                "entry_matches_side_ask": "True",
                "actual_entry_matches_entry": "True",
                "fee_present_nonnegative": "True",
                "top_visible_qty_ge_contracts": "True",
                "quote_age_le_limit": "False",
                "spread_le_limit": "True",
            }
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
                "official_minus_proxy_spot": 42.15,
                "proxy_close_minus_strike": -8.37,
                "official_expiration_minus_strike": 33.78,
                "official_proxy_result_mismatch": "True",
            }
        ],
    )

    mismatch_rows, strict_rows, summary = build_audit(
        argparse.Namespace(
            out_dir=tmp_path / "out",
            row_flags=flags,
            official_trades=official,
            min_clean_official_rows=50,
            watch_boundary_usd=50.0,
        )
    )

    assert mismatch_rows == []
    assert strict_rows == []
    assert summary["all_snapshot_official_proxy_mismatches"] == 1
    assert summary["strict_official_proxy_mismatches"] == 0
    assert summary["mismatch_removed_by_execution_filter"] is True
    assert summary["removed_mismatch_markets"] == "M1"
    assert summary["audit_status"] == "DIAGNOSTIC_NO_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH"
