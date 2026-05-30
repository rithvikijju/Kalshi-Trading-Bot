import sqlite3

import pytest

from scripts.build_btc15m_lowdd_postrestart_report import (
    load_postrestart_trades,
    official_pnl_total_fee,
    summarize_trades,
)


def test_official_pnl_uses_total_recorded_fee():
    trade = {
        "side": "yes",
        "contracts": 7,
        "actual_entry_price": 0.55,
        "actual_fee_paid": 0.13,
    }

    pnl, win, premium = official_pnl_total_fee(trade, "yes")

    assert win is True
    assert pnl == pytest.approx(3.02)
    assert premium == pytest.approx(3.98)


def test_load_postrestart_trades_filters_by_created_at(tmp_path):
    db = tmp_path / "trades.db"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE research_live_trades (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            event_ticker TEXT,
            market_ticker TEXT,
            side TEXT,
            contracts INTEGER,
            entry_price REAL
        )
        """
    )
    con.execute(
        "INSERT INTO research_live_trades VALUES (1, '2026-05-30T06:00:00+00:00', 'old', 'old-00', 'yes', 1, 0.5)"
    )
    con.execute(
        "INSERT INTO research_live_trades VALUES (2, '2026-05-30T07:00:00+00:00', 'new', 'new-00', 'no', 2, 0.4)"
    )
    con.commit()
    con.close()

    rows = load_postrestart_trades(db, "2026-05-30T06:33:25+00:00")

    assert [row["id"] for row in rows] == [2]


def test_summarize_trades_reports_path_drawdown():
    rows = [
        {"official_result": "yes", "official_win": True, "official_pnl": 3.0, "official_premium": 4.0},
        {"official_result": "no", "official_win": False, "official_pnl": -1.0, "official_premium": 1.0},
        {"official_result": "", "official_win": None, "official_pnl": None, "official_premium": 0.0},
    ]

    summary = summarize_trades(rows)

    assert summary["trades"] == 3
    assert summary["settled"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["official_pnl"] == pytest.approx(2.0)
    assert summary["max_drawdown"] == pytest.approx(-1.0)
