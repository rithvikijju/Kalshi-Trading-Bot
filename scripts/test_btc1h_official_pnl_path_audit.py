#!/usr/bin/env python3
"""Tests for BTC1H official PnL path audit."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.build_btc1h_official_pnl_path_audit import build_path


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
        official_trades=tmp / "official_trades.csv",
        clean_clock_collection_preflight_summary=tmp / "preflight.csv",
        strict_execution_rows=tmp / "strict_rows.csv",
        min_clean_official_rows=50,
        max_proxy_official_mismatch_rate=0.02,
    )


def test_official_path_sequences_rows_and_marks_old_path_diagnostic(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "official_trades.csv",
        [
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-19T02:54:37+00:00",
                "event_ticker": "KXBTCD-26MAY1823",
                "market_ticker": "KXBTCD-26MAY1823-T76599.99",
                "side": "no",
                "entry_price": 0.66,
                "fee": 0.02,
                "contracts": 1,
                "official_result": "no",
                "proxy_result": "no",
                "official_proxy_result_mismatch": "False",
                "official_win": "True",
                "official_pnl": 0.32,
                "proxy_pnl": 0.32,
                "quote_age_ms": 30,
                "top_visible_qty": 200,
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-20T11:52:04+00:00",
                "event_ticker": "KXBTCD-26MAY2008",
                "market_ticker": "KXBTCD-26MAY2008-T77299.99",
                "side": "no",
                "entry_price": 0.68,
                "fee": 0.02,
                "contracts": 1,
                "official_result": "yes",
                "proxy_result": "no",
                "official_proxy_result_mismatch": "True",
                "official_win": "False",
                "official_pnl": -0.70,
                "proxy_pnl": 0.30,
                "quote_age_ms": 120,
                "top_visible_qty": 42,
            },
            {
                "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                "created_at": "2026-05-21T13:40:11+00:00",
                "event_ticker": "KXBTCD-26MAY2110",
                "market_ticker": "KXBTCD-26MAY2110-T76699.99",
                "side": "yes",
                "entry_price": 0.66,
                "fee": 0.02,
                "contracts": 1,
                "official_result": "yes",
                "proxy_result": "yes",
                "official_proxy_result_mismatch": "False",
                "official_win": "True",
                "official_pnl": 0.32,
                "proxy_pnl": 0.32,
                "quote_age_ms": 95,
                "top_visible_qty": 75,
            },
        ],
    )
    write_csv(
        tmp_path / "preflight.csv",
        [
            {
                "clean_evidence_clock_ready": "False",
                "collection_evidence_ready": "False",
                "process_control_authorized": "False",
            }
        ],
    )
    write_csv(
        tmp_path / "strict_rows.csv",
        [
            {"event_ticker": "KXBTCD-26MAY1823", "market_ticker": "KXBTCD-26MAY1823-T76599.99", "side": "no"},
            {"event_ticker": "KXBTCD-26MAY2008", "market_ticker": "KXBTCD-26MAY2008-T77299.99", "side": "no"},
        ],
    )

    sequence, strict_sequence, summary = build_path(args(tmp_path))

    assert [row["sequence_index"] for row in sequence] == [1, 2, 3]
    assert [row["sequence_index"] for row in strict_sequence] == [1, 2]
    assert sequence[1]["official_proxy_result_mismatch"] is True
    assert sequence[1]["path_quality_note"] == "official_proxy_mismatch;quote_age_gt_90ms;official_loss"
    assert summary["path_audit_status"] == "DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE"
    assert summary["official_rows"] == 3
    assert summary["official_pnl"] == -0.06
    assert summary["proxy_pnl"] == 0.94
    assert summary["official_minus_proxy_pnl"] == -1.0
    assert summary["max_drawdown"] == -0.7
    assert summary["proxy_official_mismatches"] == 1
    assert summary["quote_age_gt_90ms_rows"] == 2
    assert summary["strict_execution_path_rows"] == 2
    assert summary["strict_execution_path_official_pnl"] == -0.38
    assert summary["strict_execution_path_proxy_pnl"] == 0.62
    assert summary["strict_execution_path_official_minus_proxy_pnl"] == -1.0
    assert summary["strict_execution_path_max_drawdown"] == -0.7
    assert summary["strict_execution_path_proxy_official_mismatches"] == 1
    assert summary["strict_execution_path_quote_age_gt_90ms_rows"] == 1
    assert summary["strict_execution_path_removed_markets"] == "KXBTCD-26MAY2110-T76699.99"
    assert summary["strict_execution_path_current_rows_count_for_promotion"] is False
    assert summary["current_rows_count_for_promotion"] is False
    assert "too_few_clean_official_rows" in summary["blockers"]
    assert "official_proxy_mismatch_present" in summary["blockers"]
    assert summary["no_process_action_taken"] is True
