import pandas as pd
import pytest

from scripts.backtest_btc15m_v2_binary_adapter import (
    V2BinaryVariant,
    attach_causal_volatility,
    lognormal_p_above,
    score_variant,
)


def test_lognormal_probability_increases_with_spot():
    low = lognormal_p_above(spot=99.0, strike=100.0, ttl_min=10.0, sigma_annualized=0.5)
    high = lognormal_p_above(spot=101.0, strike=100.0, ttl_min=10.0, sigma_annualized=0.5)

    assert low is not None
    assert high is not None
    assert high > low


def test_attach_causal_volatility_does_not_use_future_bar():
    q = pd.DataFrame(
        [
            {
                "received_at_ns": 175,
                "received_at_utc": pd.Timestamp("2026-05-30T00:02:55Z"),
            }
        ]
    )
    btc = pd.DataFrame(
        [
            {"received_at_ns": 100, "received_at_utc": pd.Timestamp("2026-05-30T00:00:50Z"), "price": 100.0},
            {"received_at_ns": 150, "received_at_utc": pd.Timestamp("2026-05-30T00:01:50Z"), "price": 101.0},
            {"received_at_ns": 200, "received_at_utc": pd.Timestamp("2026-05-30T00:02:50Z"), "price": 103.0},
        ]
    )
    out = attach_causal_volatility(q, btc, [V2BinaryVariant(name="t", rv_lookback_min=2, min_vol_points=1)])

    assert out["vol_points_2m"].iloc[0] == pytest.approx(1.0)


def test_score_variant_uses_executable_ask_and_one_trade_per_event():
    ts = pd.Timestamp("2026-05-30T00:00:00Z")
    q = pd.DataFrame(
        [
            {
                "received_at_ns": 1,
                "received_at_utc": ts,
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "yes_bid": 0.40,
                "yes_ask": 0.41,
                "yes_ask_qty": 10.0,
                "no_bid": 0.58,
                "no_ask": 0.59,
                "no_ask_qty": 10.0,
                "yes_mid": 0.405,
                "spread_cents": 1.0,
                "btc_spot": 105.0,
                "floor_strike": 100.0,
                "expiration_value": 106.0,
                "close_time": ts + pd.Timedelta(minutes=10),
                "ttl_min": 10.0,
                "actual_yes": True,
                "result": "yes",
                "sigma_2m": 0.5,
                "vol_points_2m": 2.0,
            },
            {
                "received_at_ns": 2,
                "received_at_utc": ts + pd.Timedelta(seconds=1),
                "event_ticker": "EVT",
                "market_ticker": "MKT",
                "yes_bid": 0.45,
                "yes_ask": 0.46,
                "yes_ask_qty": 10.0,
                "no_bid": 0.53,
                "no_ask": 0.54,
                "no_ask_qty": 10.0,
                "yes_mid": 0.455,
                "spread_cents": 1.0,
                "btc_spot": 105.0,
                "floor_strike": 100.0,
                "expiration_value": 106.0,
                "close_time": ts + pd.Timedelta(minutes=10),
                "ttl_min": 9.99,
                "actual_yes": True,
                "result": "yes",
                "sigma_2m": 0.5,
                "vol_points_2m": 2.0,
            },
        ]
    )
    variant = V2BinaryVariant(
        name="test",
        min_edge_cents=1.0,
        rv_lookback_min=2,
        min_vol_points=1,
        min_entry=0.01,
        max_entry=0.99,
    )

    trades = score_variant(q, variant)

    assert len(trades) == 1
    assert trades["received_at_ns"].iloc[0] == 1
    assert trades["side"].iloc[0] == "yes"
    assert trades["entry_price"].iloc[0] == pytest.approx(0.41)
    assert trades["pnl"].iloc[0] > 0
