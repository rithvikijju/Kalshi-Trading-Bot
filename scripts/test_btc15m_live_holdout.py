import pytest
import pandas as pd

from scripts.backtest_btc15m_live_holdout import enrich


def test_enrich_filters_crossed_top_book_rows():
    ts = pd.Timestamp("2026-05-30T06:00:00Z")
    top = pd.DataFrame(
        [
            {
                "received_at_ns": 120_000_000_000,
                "received_at_utc": ts,
                "event_ticker": "KXBTC15M-26MAY300200",
                "market_ticker": "KXBTC15M-26MAY300200-00",
                "yes_bid": 0.40,
                "yes_ask": 0.41,
                "yes_bid_qty": 10,
                "yes_ask_qty": 10,
                "no_bid": 0.59,
                "no_ask": 0.60,
                "no_bid_qty": 10,
                "no_ask_qty": 10,
                "btc_spot": 70000,
                "source": "test",
            },
            {
                "received_at_ns": 121_000_000_000,
                "received_at_utc": ts + pd.Timedelta(seconds=1),
                "event_ticker": "KXBTC15M-26MAY300200",
                "market_ticker": "KXBTC15M-26MAY300200-00",
                "yes_bid": 0.45,
                "yes_ask": 0.44,
                "yes_bid_qty": 10,
                "yes_ask_qty": 10,
                "no_bid": 0.56,
                "no_ask": 0.55,
                "no_bid_qty": 10,
                "no_ask_qty": 10,
                "btc_spot": 70000,
                "source": "test",
            },
        ]
    )
    btc = pd.DataFrame(
        [
            {"received_at_ns": 0, "received_at_utc": ts - pd.Timedelta(minutes=3), "price": 69900},
            {"received_at_ns": 60_000_000_000, "received_at_utc": ts - pd.Timedelta(minutes=1), "price": 69950},
            {"received_at_ns": 120_000_000_000, "received_at_utc": ts, "price": 70000},
        ]
    )
    results = pd.DataFrame(
        [
            {
                "event_ticker": "KXBTC15M-26MAY300200",
                "market_ticker": "KXBTC15M-26MAY300200-00",
                "result": "yes",
                "status": "settled",
                "actual_yes": True,
                "close_time": ts + pd.Timedelta(minutes=5),
            }
        ]
    )

    enriched = enrich(top, btc, results)

    assert enriched["received_at_ns"].tolist() == [120_000_000_000]
    assert enriched["spread_cents"].tolist() == pytest.approx([1.0])
