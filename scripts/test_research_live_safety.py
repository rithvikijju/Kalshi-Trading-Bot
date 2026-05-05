#!/usr/bin/env python3
"""Deterministic safety checks for btc_1hr_research_live.py."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.btc_1hr_research_live import (
    BookQuote,
    PortfolioSnapshot,
    TradeSignal,
    bankroll_allows_trade,
    build_research_event,
    choose_contracts_for_signal,
    db_active_tickers,
    db_connect,
    market_is_research_cumulative,
    parse_event_close_from_ticker,
    quote_from_orderbook,
    record_trade,
    resize_signal,
    signal_from_book,
    update_trade_response,
)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def market(event_ticker: str, close: datetime, suffix: str = "T99999.99", status: str = "open") -> dict:
    return {
        "ticker": f"{event_ticker}-{suffix}",
        "event_ticker": event_ticker,
        "status": status,
        "close_time": iso_z(close),
        "floor_strike": 100000.0,
        "cap_strike": None,
    }


def sample_signal(close_time: datetime) -> TradeSignal:
    return TradeSignal(
        event_ticker="KXBTCD-26MAY0502",
        market_ticker="KXBTCD-26MAY0502-T99999.99",
        side="yes",
        contracts=1,
        entry_price=0.40,
        yes_order_side="bid",
        yes_limit_price=0.40,
        available_qty=10,
        model_p_yes=0.70,
        edge_gross_cents=30.0,
        entry_fee=0.02,
        net_edge_cents=28.0,
        edge_threshold_cents=12.0,
        spread_cents=2.0,
        strike=100000.0,
        btc_spot=100500.0,
        ttl_min=30.0,
        close_time=iso_z(close_time),
        yes_bid=0.38,
        yes_ask=0.40,
        no_bid=0.60,
        no_ask=0.62,
    )


class ResearchLiveSafetyTests(unittest.TestCase):
    def test_event_ticker_close_is_new_york_hour(self) -> None:
        close = parse_event_close_from_ticker("KXBTCD-26MAY0502")
        self.assertEqual(close, datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc))

    def test_only_current_hourly_kxbtcd_event_is_accepted(self) -> None:
        now = datetime(2026, 5, 5, 5, 30, tzinfo=timezone.utc)
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = build_research_event("KXBTCD-26MAY0502", [market("KXBTCD-26MAY0502", close)], now)
        self.assertIsNotNone(event)

        tomorrow_close = datetime(2026, 5, 6, 6, 0, tzinfo=timezone.utc)
        tomorrow = build_research_event(
            "KXBTCD-26MAY0602",
            [market("KXBTCD-26MAY0602", tomorrow_close)],
            now,
        )
        self.assertIsNone(tomorrow)

    def test_api_close_time_must_match_ticker_close(self) -> None:
        now = datetime(2026, 5, 5, 5, 30, tzinfo=timezone.utc)
        wrong_close = datetime(2026, 5, 5, 7, 0, tzinfo=timezone.utc)
        event = build_research_event("KXBTCD-26MAY0502", [market("KXBTCD-26MAY0502", wrong_close)], now)
        self.assertIsNone(event)

    def test_buckets_and_cross_event_markets_are_rejected(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        self.assertFalse(market_is_research_cumulative("KXBTCD-26MAY0502", market("KXBTCD-26MAY0502", close, "B99999"), close))
        self.assertFalse(market_is_research_cumulative("KXBTCD-26MAY0502", market("KXBTCD-26MAY0503", close), close))

    def test_signal_path_rejects_non_research_market_before_model(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = {"event_ticker": "KXBTCD-26MAY0502", "close_time": close, "ttl_hours": 0.5}
        bucket = market("KXBTCD-26MAY0502", close, "B99999")
        quote = BookQuote("x", 0.40, 1, 0.42, 1, 0.58, 1, 0.60, 1)
        self.assertIsNone(signal_from_book(event, bucket, quote, None, {}, 100000.0, 1, 12.0, 2.0))

    def test_orderbook_uses_real_yes_and_no_books(self) -> None:
        q = quote_from_orderbook("T", {"orderbook": {"yes": [[62, 10]], "no": [[35, 7]]}})
        self.assertEqual((round(q.yes_bid, 4), round(q.yes_ask, 4), round(q.no_bid, 4), round(q.no_ask, 4)), (0.62, 0.65, 0.35, 0.38))
        q2 = quote_from_orderbook("T", {"orderbook_fp": {"yes_dollars": [["0.6200", "10"]], "no_dollars": [["0.3500", "7"]]}})
        self.assertEqual((round(q2.yes_bid, 4), round(q2.yes_ask, 4), round(q2.no_bid, 4), round(q2.no_ask, 4)), (0.62, 0.65, 0.35, 0.38))

    def test_signal_allows_spread_at_limit_with_float_noise(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = {"event_ticker": "KXBTCD-26MAY0502", "close_time": close, "ttl_hours": 0.5}
        mkt = market("KXBTCD-26MAY0502", close)
        quote = BookQuote("x", 0.48, 10, 0.500000000001, 10, None, 0, None, 0)
        emp_cache = {30: {"n": 100000}}
        with patch("scripts.btc_1hr_research_live.model_probability", return_value=(0.80, 30.0)):
            sig = signal_from_book(event, mkt, quote, None, emp_cache, 100500.0, 1, 12.0, 2.0)
        self.assertIsNotNone(sig)

    def test_submitted_and_partial_fills_block_reentry(self) -> None:
        close = datetime.now(timezone.utc) + timedelta(hours=1)
        sig = sample_signal(close)
        conn = db_connect(":memory:")
        trade_id = record_trade(conn, "live", "submitted", sig, client_order_id="cid")
        self.assertIn(sig.market_ticker, db_active_tickers(conn, datetime.now(timezone.utc)))

        update_trade_response(
            conn,
            trade_id,
            "partial_filled",
            sig,
            {"order": {"order_id": "oid", "fill_count": "0.50", "average_fill_price": "0.4000", "average_fee_paid": "0.0100"}},
        )
        self.assertIn(sig.market_ticker, db_active_tickers(conn, datetime.now(timezone.utc)))

    def test_bankroll_gates_fail_closed(self) -> None:
        sig = sample_signal(datetime.now(timezone.utc) + timedelta(hours=1))
        rich = PortfolioSnapshot(available_balance=100.0, portfolio_value=100.0, active_tickers=set(), market_exposure=0.0)
        self.assertTrue(bankroll_allows_trade(sig, rich, 0.0, 0.0)[0])

        no_cash = PortfolioSnapshot(available_balance=0.10, portfolio_value=100.0, active_tickers=set(), market_exposure=0.0)
        self.assertFalse(bankroll_allows_trade(sig, no_cash, 0.0, 0.0)[0])

        maxed = PortfolioSnapshot(available_balance=100.0, portfolio_value=100.0, active_tickers=set(), market_exposure=50.0)
        self.assertFalse(bankroll_allows_trade(sig, maxed, 0.0, 0.0)[0])

    def test_live_sizing_caps_at_three_and_recalculates_fee(self) -> None:
        sig = sample_signal(datetime.now(timezone.utc) + timedelta(hours=1))
        portfolio = PortfolioSnapshot(available_balance=20.0, portfolio_value=20.0, active_tickers=set(), market_exposure=0.0)
        contracts = choose_contracts_for_signal(sig, portfolio, local_active_exposure=0.0, spent_this_cycle=0.0)
        self.assertEqual(contracts, 3)

        sized = resize_signal(sig, contracts)
        self.assertEqual(sized.contracts, 3)
        self.assertGreater(sized.entry_fee, sig.entry_fee)
        self.assertLessEqual(sized.contracts * sized.entry_price + sized.entry_fee, 4.0)

    def test_live_sizing_respects_cash_and_active_exposure(self) -> None:
        sig = sample_signal(datetime.now(timezone.utc) + timedelta(hours=1))
        low_cash = PortfolioSnapshot(available_balance=1.0, portfolio_value=20.0, active_tickers=set(), market_exposure=0.0)
        self.assertEqual(choose_contracts_for_signal(sig, low_cash, 0.0, 0.0), 2)

        maxed = PortfolioSnapshot(available_balance=20.0, portfolio_value=20.0, active_tickers=set(), market_exposure=10.0)
        self.assertEqual(choose_contracts_for_signal(sig, maxed, 0.0, 0.0), 0)


if __name__ == "__main__":
    unittest.main()
