import pandas as pd

from scripts.audit_btc15m_pair_lock_selection_bias import summarize_trades


def test_pair_lock_audit_rejects_future_conditioned_pairs(tmp_path):
    trades = pd.DataFrame(
        [
            {
                "strategy": "cheap_pair_lock_rr",
                "event_ticker": "A",
                "side": "pair",
                "legs": "yes->no",
                "received_at_utc": "2026-05-30T00:05:00Z",
                "pnl": 0.10,
            },
            {
                "strategy": "cheap_tail_position_aware",
                "event_ticker": "A",
                "side": "pair",
                "legs": "yes->no",
                "received_at_utc": "2026-05-30T00:05:00Z",
                "pnl": 0.10,
            },
            {
                "strategy": "cheap_tail_position_aware",
                "event_ticker": "B",
                "side": "yes",
                "legs": "yes",
                "received_at_utc": "2026-05-30T00:20:00Z",
                "pnl": -0.40,
            },
        ]
    )
    path = tmp_path / "trades.csv"
    trades.to_csv(path, index=False)

    row, detail = summarize_trades(path, "sample", "cheap_pair_lock_rr", "cheap_tail_position_aware")

    assert row["pair_only_pnl"] == 0.10
    assert row["position_aware_pnl"] == -0.30
    assert row["unpaired_position_rows"] == 1
    assert row["selection_bias_pnl_gap"] == 0.40
    assert row["verdict"] == "reject_pair_only_future_conditioned"
    assert len(detail) == 2


def test_pair_lock_audit_handles_empty_pair_rows(tmp_path):
    trades = pd.DataFrame(
        [
            {
                "strategy": "cheap_tail_position_aware",
                "event_ticker": "B",
                "side": "yes",
                "legs": "yes",
                "received_at_utc": "2026-05-30T00:20:00Z",
                "pnl": -0.40,
            },
        ]
    )
    path = tmp_path / "trades.csv"
    trades.to_csv(path, index=False)

    row, _ = summarize_trades(path, "sample", "cheap_pair_lock_rr", "cheap_tail_position_aware")

    assert row["pair_rows"] == 0
    assert row["verdict"] == "no_pair_lock_rows"
