import pandas as pd

from scripts.audit_btc15m_sidecar_fidelity import verdict


def test_verdict_distinguishes_filterable_book_rows_from_hard_failure():
    quote = pd.DataFrame(
        [
            {"metric": "rows", "value": 100},
            {"metric": "null_received_at_utc", "value": 0},
            {"metric": "null_received_at_ns", "value": 0},
            {"metric": "null_market_ticker", "value": 0},
            {"metric": "null_event_ticker", "value": 0},
            {"metric": "null_book_fields", "value": 1},
            {"metric": "price_out_of_range", "value": 0},
            {"metric": "crossed_yes_book", "value": 0},
            {"metric": "crossed_no_book", "value": 0},
            {"metric": "negative_qty", "value": 0},
            {"metric": "implausible_btc_spot", "value": 0},
            {"metric": "valid_book_rows", "value": 99},
            {"metric": "valid_book_rate", "value": 0.99},
        ]
    )
    gaps = pd.DataFrame([{"gaps_over_threshold": 0}])
    coinbase = pd.DataFrame(
        [
            {"metric": "synthetic_coinbase_from_top", "value": False},
        ]
    )

    out = verdict(quote, gaps, coinbase, manifest={}, compare_df=pd.DataFrame()).iloc[0]

    assert not bool(out["top_book_research_grade"])
    assert bool(out["filtered_top_book_research_grade"])
    assert bool(out["promotion_grade_coinbase_ticks"])
    assert out["filterable_failures"] == "null_book_fields"
    assert out["hard_failures"] == ""


def test_verdict_rejects_filtered_replay_when_clock_fields_are_missing():
    quote = pd.DataFrame(
        [
            {"metric": "rows", "value": 100},
            {"metric": "null_received_at_utc", "value": 1},
            {"metric": "null_received_at_ns", "value": 0},
            {"metric": "null_market_ticker", "value": 0},
            {"metric": "null_event_ticker", "value": 0},
            {"metric": "null_book_fields", "value": 0},
            {"metric": "price_out_of_range", "value": 0},
            {"metric": "crossed_yes_book", "value": 0},
            {"metric": "crossed_no_book", "value": 0},
            {"metric": "negative_qty", "value": 0},
            {"metric": "implausible_btc_spot", "value": 0},
            {"metric": "valid_book_rows", "value": 99},
            {"metric": "valid_book_rate", "value": 0.99},
        ]
    )
    gaps = pd.DataFrame([{"gaps_over_threshold": 0}])
    coinbase = pd.DataFrame([{"metric": "synthetic_coinbase_from_top", "value": False}])

    out = verdict(quote, gaps, coinbase, manifest={}, compare_df=pd.DataFrame()).iloc[0]

    assert not bool(out["filtered_top_book_research_grade"])
    assert out["hard_failures"] == "null_received_at_utc"
