import duckdb
import pandas as pd

from scripts.audit_btc15m_lowdd_paper_replay_parity import (
    audit_rows,
    load_sidecar_tables,
    row_verdict,
    summarize,
    strategy_replay_rows,
)


def test_row_verdict_separates_live_parity_from_generic_replay_mismatch():
    verdict = row_verdict(
        signal_diff=0.0,
        order_diff=0.0,
        signal_order_reprice_cents=0.0,
        signal_dt=-0.2,
        order_dt=0.1,
        replay_match=pd.Series({"entry_price": 0.74}),
        replay_diff=0.09,
        price_tolerance=1e-9,
        time_tolerance=10.0,
        max_signal_order_reprice_cents=2.0,
    )

    assert verdict == "live_sidecar_parity_pass_generic_replay_price_mismatch"


def test_row_verdict_allows_bounded_signal_to_order_reprice():
    verdict = row_verdict(
        signal_diff=-0.01,
        order_diff=0.0,
        signal_order_reprice_cents=1.0,
        signal_dt=-0.4,
        order_dt=0.1,
        replay_match=pd.Series({"entry_price": 0.57}),
        replay_diff=-0.01,
        price_tolerance=1e-9,
        time_tolerance=10.0,
        max_signal_order_reprice_cents=2.0,
    )

    assert verdict == "live_sidecar_order_parity_pass_signal_reprice_generic_replay_price_mismatch"


def test_row_verdict_rejects_over_limit_signal_to_order_reprice():
    verdict = row_verdict(
        signal_diff=-0.03,
        order_diff=0.0,
        signal_order_reprice_cents=3.0,
        signal_dt=-0.4,
        order_dt=0.1,
        replay_match=pd.Series({"entry_price": 0.55}),
        replay_diff=-0.03,
        price_tolerance=1e-9,
        time_tolerance=10.0,
        max_signal_order_reprice_cents=2.0,
    )

    assert verdict == "fail_signal_order_reprice_over_limit"


def test_summarize_counts_bounded_reprice_as_live_parity_pass():
    summary = summarize(
        [
            {
                "verdict": "live_sidecar_order_parity_pass_signal_reprice_generic_replay_price_mismatch",
                "signal_order_reprice_cents": 1.0,
            },
            {
                "verdict": "live_sidecar_parity_pass",
                "signal_order_reprice_cents": 0.0,
            },
        ]
    )

    assert summary["live_sidecar_parity_pass_rows"] == 2
    assert summary["signal_order_reprice_rows"] == 1
    assert summary["signal_order_reprice_over_limit_rows"] == 0
    assert summary["max_signal_order_worse_reprice_cents"] == 1.0
    assert summary["generic_replay_price_mismatch_rows"] == 1


def test_load_sidecar_tables_and_audit_rows(tmp_path):
    db = tmp_path / "sidecar.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        """
        CREATE TABLE signal_scan (
            received_at_utc VARCHAR,
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
            event_ticker VARCHAR,
            market_ticker VARCHAR,
            side VARCHAR,
            contracts DOUBLE,
            entry_price DOUBLE,
            estimated_cost DOUBLE,
            action VARCHAR,
            detail VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO signal_scan VALUES
        ('2026-05-30T08:10:01.700000Z', 'EVT', 'MKT', 'yes', 0.65, 12.0, 0.66, 'selected', 'pass')
        """
    )
    con.execute(
        """
        INSERT INTO order_decision VALUES
        ('2026-05-30T08:10:02.000000Z', 'EVT', 'MKT', 'yes', 9, 0.65, 6.0, 'paper_fill', 'filled')
        """
    )
    con.close()
    selected, orders = load_sidecar_tables(db)
    paper = pd.DataFrame(
        [
            {
                "id": 1,
                "created_at": pd.Timestamp("2026-05-30T08:10:01.900000Z"),
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "side": "yes",
                "contracts": 9,
                "entry_price": 0.65,
                "official_pnl": 3.0,
            }
        ]
    )
    replay = pd.DataFrame(
        [
            {
                "strategy": "current_lowdd_no_rv",
                "received_at_utc": pd.Timestamp("2026-05-30T08:10:00Z"),
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "side": "yes",
                "entry_price": 0.74,
                "pnl": 0.24,
            }
        ]
    )

    rows = audit_rows(
        paper,
        selected,
        orders,
        replay,
        price_tolerance=1e-9,
        time_tolerance=10.0,
        max_signal_order_reprice_cents=2.0,
    )

    assert rows[0]["signal_entry_price"] == 0.65
    assert rows[0]["order_entry_price"] == 0.65
    assert rows[0]["signal_order_reprice_cents"] == 0.0
    assert rows[0]["replay_entry_price"] == 0.74
    assert rows[0]["verdict"] == "live_sidecar_parity_pass_generic_replay_price_mismatch"


def test_strategy_replay_rows_filters_strategy(tmp_path):
    path = tmp_path / "trades.csv"
    pd.DataFrame(
        [
            {"strategy": "current_lowdd_no_rv", "event_ticker": "A", "pnl": 1.0},
            {"strategy": "other", "event_ticker": "B", "pnl": -1.0},
        ]
    ).to_csv(path, index=False)

    rows = strategy_replay_rows(path, "current_lowdd_no_rv")

    assert rows["event_ticker"].tolist() == ["A"]
