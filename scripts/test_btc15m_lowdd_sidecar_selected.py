import duckdb
import pandas as pd
import pytest

from scripts.backtest_btc15m_lowdd_sidecar_selected import (
    build_rows,
    dedupe_selected_by_event,
    load_selected_and_orders,
    one_contract_pnl,
    order_scaled_pnl,
    summarize,
)


def test_one_contract_pnl_uses_side_result_and_fee():
    pnl, win, premium = one_contract_pnl("yes", 0.65, "yes")

    assert win is True
    assert premium == pytest.approx(0.67)
    assert pnl == pytest.approx(0.33)


def test_order_scaled_pnl_uses_estimated_cost_total():
    pnl, win, premium = order_scaled_pnl("yes", 9, 6.0, "yes")

    assert win is True
    assert premium == pytest.approx(6.0)
    assert pnl == pytest.approx(3.0)


def test_load_selected_and_orders_reads_materialized_duckdb(tmp_path):
    db = tmp_path / "sidecar.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        """
        CREATE TABLE signal_scan (
            received_at_utc VARCHAR,
            received_at_ns BIGINT,
            event_ticker VARCHAR,
            selected_market VARCHAR,
            selected_side VARCHAR,
            entry_price DOUBLE,
            net_edge_cents DOUBLE,
            model_p_yes DOUBLE,
            action VARCHAR,
            detail VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE order_decision (
            received_at_utc VARCHAR,
            received_at_ns BIGINT,
            event_ticker VARCHAR,
            market_ticker VARCHAR,
            side VARCHAR,
            contracts DOUBLE,
            entry_price DOUBLE,
            estimated_cost DOUBLE,
            portfolio_available DOUBLE,
            portfolio_value DOUBLE,
            action VARCHAR,
            detail VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO signal_scan VALUES
        ('2026-05-30T08:10:01Z', 1, 'EVT', 'MKT', 'yes', 0.65, 12.0, 0.665, 'selected', 'pass')
        """
    )
    con.execute(
        """
        INSERT INTO order_decision VALUES
        ('2026-05-30T08:10:02Z', 2, 'EVT', 'MKT', 'yes', 9, 0.65, 6.0, 98.0, 98.0, 'paper_fill', 'filled')
        """
    )
    con.close()

    selected, orders = load_selected_and_orders(db, None, None)

    assert selected["market_ticker"].tolist() == ["MKT"]
    assert orders["estimated_cost"].tolist() == [6.0]


def test_build_rows_and_summarize_with_fake_official(monkeypatch):
    selected = pd.DataFrame(
        [
            {
                "selected_at_utc": pd.Timestamp("2026-05-30T08:10:01Z"),
                "selected_received_at_ns": 1,
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "side": "yes",
                "entry_price": 0.65,
                "net_edge_cents": 12.0,
                "model_p_yes": 0.665,
                "detail": "pass",
            }
        ]
    )
    orders = pd.DataFrame(
        [
            {
                "order_at_utc": pd.Timestamp("2026-05-30T08:10:02Z"),
                "order_received_at_ns": 2,
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "side": "yes",
                "contracts": 9,
                "order_entry_price": 0.65,
                "estimated_cost": 6.0,
                "portfolio_available": 98.0,
                "portfolio_value": 98.0,
                "action": "paper_fill",
                "order_detail": "filled",
            }
        ]
    )

    monkeypatch.setattr(
        "scripts.backtest_btc15m_lowdd_sidecar_selected.fetch_market",
        lambda session, ticker: {"status": "finalized", "result": "yes", "expiration_value": "73457.14"},
    )
    rows = build_rows(selected, orders, sleep_sec=0.0)
    summary = summarize(rows)

    assert rows[0]["signal_one_contract_pnl"] == pytest.approx(0.33)
    assert rows[0]["order_scaled_pnl"] == pytest.approx(3.0)
    assert rows[0]["order_price_diff"] == pytest.approx(0.0)
    assert summary["signal_one_contract_pnl"] == pytest.approx(0.33)
    assert summary["order_scaled_pnl"] == pytest.approx(3.0)


def test_dedupe_selected_by_event_keeps_first_signal():
    selected = pd.DataFrame(
        [
            {
                "selected_at_utc": pd.Timestamp("2026-05-30T10:25:20.402405Z"),
                "selected_received_at_ns": 2,
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "side": "yes",
            },
            {
                "selected_at_utc": pd.Timestamp("2026-05-30T10:25:20.365412Z"),
                "selected_received_at_ns": 1,
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "side": "yes",
            },
        ]
    )

    kept, duplicates = dedupe_selected_by_event(selected)

    assert kept["selected_received_at_ns"].tolist() == [1]
    assert duplicates["selected_received_at_ns"].tolist() == [2]
