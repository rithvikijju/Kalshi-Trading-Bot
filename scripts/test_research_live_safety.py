#!/usr/bin/env python3
"""Deterministic safety checks for btc_1hr_research_live.py."""

from __future__ import annotations

import asyncio
import json
import unittest
import queue
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

import duckdb
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.btc_1hr_research_live import (
    BookQuote,
    CoalescedUpdateBuffer,
    KalshiWsClient,
    KalshiApi,
    LiveCaptureWriter,
    LiveMarketState,
    LiveOrderbook,
    PortfolioSnapshot,
    TradeSignal,
    WsResearchExecutor,
    acquire_event_lock,
    bankroll_allows_trade,
    build_research_event,
    choose_contracts_for_signal,
    db_active_events,
    db_active_tickers,
    db_connect,
    event_from_market_ticker,
    local_status_from_kalshi_order,
    market_is_research_cumulative,
    paper_shadow_summary,
    parse_fill_details,
    parse_event_close_from_ticker,
    quote_from_orderbook,
    record_trade,
    release_event_lock,
    resize_signal,
    safe_put_update,
    set_sizing_policy,
    set_signal_strategy,
    signal_from_book,
    shadow_portfolio_snapshot,
    update_trade_response,
    utc_now_ns,
    is_fok_no_fill_conflict,
)


class DummyRecorder:
    def __init__(self) -> None:
        self.rows = []
        self.capture_raw_ws = False

    def record(self, table, row) -> None:
        self.rows.append((table, row))


class DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> dict:
        return self._payload


class DummySession:
    def __init__(self, responses: list[DummyResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs) -> DummyResponse:
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return DummyResponse(200, {"ok": True})


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

    def test_js_guarded_signal_uses_guarded_live_filters(self) -> None:
        close = datetime(2026, 5, 5, 16, 0, tzinfo=timezone.utc)
        event = {"event_ticker": "KXBTCD-26MAY0512", "close_time": close, "ttl_hours": 0.5}
        mkt = market("KXBTCD-26MAY0512", close, "T100000.00")
        quote = BookQuote(mkt["ticker"], 0.68, 10, 0.70, 10, 0.29, 10, 0.32, 10)
        try:
            set_signal_strategy("js_guarded")
            with patch("scripts.btc_1hr_research_live.model_probability", return_value=(0.90, 30.0)):
                with patch("scripts.btc_1hr_research_live.edge_uncertainty_cents", return_value=0.0):
                    sig = signal_from_book(
                        event,
                        mkt,
                        quote,
                        None,
                        {},
                        100000.0,
                        1,
                        12.0,
                        2.0,
                        now=datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc),
                    )
                    self.assertIsNotNone(sig)
                    self.assertEqual(sig.side, "yes")
                    self.assertGreaterEqual(sig.entry_price, 0.55)

                    blocked_hour = signal_from_book(
                        event,
                        mkt,
                        quote,
                        None,
                        {},
                        100000.0,
                        1,
                        12.0,
                        2.0,
                        now=datetime(2026, 5, 5, 18, 30, tzinfo=timezone.utc),
                    )
                    self.assertIsNone(blocked_hour)

                    far_strike = signal_from_book(
                        event,
                        mkt,
                        quote,
                        None,
                        {},
                        101000.0,
                        1,
                        12.0,
                        2.0,
                        now=datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc),
                    )
                    self.assertIsNone(far_strike)
        finally:
            set_signal_strategy("research")

    def test_shadow_portfolio_reports_realized_pnl_and_active_exposure(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        conn = db_connect(":memory:")
        win = sample_signal(close)
        loss = resize_signal(sample_signal(close + timedelta(hours=1)), 2)
        loss = replace(loss, side="no", market_ticker="KXBTCD-26MAY0503-T100000.00")
        open_sig = sample_signal(close + timedelta(hours=2))
        record_trade(conn, "paper", "paper_filled", win)
        record_trade(conn, "paper", "paper_filled", loss)
        record_trade(conn, "paper", "paper_filled", open_sig)
        btc = pd.DataFrame(
            {
                "time": [
                    close,
                    close + timedelta(hours=1),
                    close + timedelta(hours=2),
                ],
                "close": [100500.0, 100500.0, 100500.0],
            }
        )
        summary = paper_shadow_summary(conn, btc, 1000.0, close + timedelta(hours=1, minutes=1))
        self.assertEqual(summary["settled_trades"], 2)
        self.assertEqual(summary["open_trades"], 1)
        self.assertAlmostEqual(summary["realized_pnl"], 0.58 - 0.84, places=6)
        portfolio = shadow_portfolio_snapshot(conn, btc, 1000.0, close + timedelta(hours=1, minutes=1))
        self.assertGreater(portfolio.available_balance, 998.0)
        self.assertEqual(len(portfolio.active_tickers), 1)

    def test_kalshi_canceled_fok_maps_to_not_filled(self) -> None:
        order = {
            "status": "canceled",
            "fill_count_fp": "0.00",
            "initial_count_fp": "3.00",
            "order_id": "oid",
        }
        self.assertEqual(local_status_from_kalshi_order(order, 3), "not_filled")
        filled = dict(order, status="executed", fill_count_fp="3.00")
        self.assertEqual(local_status_from_kalshi_order(filled, 3), "filled")
        partial = dict(order, status="canceled", fill_count_fp="1.00")
        self.assertEqual(local_status_from_kalshi_order(partial, 3), "partial_filled")

    def test_parse_fill_details_preserves_zero_fill_count(self) -> None:
        sig = sample_signal(datetime.now(timezone.utc) + timedelta(hours=1))
        fill_count, avg_yes, actual_entry, actual_fee, order_id = parse_fill_details(
            sig,
            {
                "order": {
                    "order_id": "oid",
                    "fill_count_fp": "0.00",
                    "yes_price_dollars": "0.4600",
                    "taker_fees_dollars": "0.000000",
                }
            },
        )
        self.assertEqual(fill_count, 0.0)
        self.assertEqual(avg_yes, 0.46)
        self.assertIsNone(actual_entry)
        self.assertIsNone(actual_fee)
        self.assertEqual(order_id, "oid")

    def test_websocket_orderbook_replays_snapshot_and_delta(self) -> None:
        book = LiveOrderbook("KXBTCD-26MAY0502-T99999.99")
        book.apply_snapshot(
            {
                "market_ticker": book.market_ticker,
                "yes_dollars_fp": [["0.6200", "10.00"], ["0.6100", "20.00"]],
                "no_dollars_fp": [["0.3500", "7.00"]],
            },
            sid=1,
            seq=10,
            received_at_ns=1,
        )
        q = book.to_quote()
        self.assertEqual((round(q.yes_bid, 4), round(q.yes_ask, 4), round(q.no_bid, 4), round(q.no_ask, 4)), (0.62, 0.65, 0.35, 0.38))

        book.apply_delta(
            {
                "market_ticker": book.market_ticker,
                "side": "no",
                "price_dollars": "0.3700",
                "delta_fp": "3.00",
                "ts": "2026-05-05T06:00:00Z",
            },
            sid=1,
            seq=11,
            received_at_ns=2,
        )
        q = book.to_quote()
        self.assertEqual((round(q.yes_bid, 4), round(q.yes_ask, 4), round(q.no_bid, 4), round(q.no_ask, 4)), (0.62, 0.63, 0.37, 0.38))

    def test_live_market_state_requires_all_initial_snapshots(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = build_research_event(
            "KXBTCD-26MAY0502",
            [
                market("KXBTCD-26MAY0502", close, "T99999.99"),
                market("KXBTCD-26MAY0502", close, "T100099.99"),
            ],
            datetime(2026, 5, 5, 5, 30, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(event)
        state = LiveMarketState()
        state.set_events([event])
        self.assertEqual(state.orderbook_coverage(), (0, 2))
        state.apply_orderbook_snapshot(
            sid=1,
            seq=1,
            msg={
                "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                "yes_dollars_fp": [["0.5000", "1.00"]],
                "no_dollars_fp": [["0.4900", "1.00"]],
            },
            received_at_ns=1,
        )
        self.assertEqual(state.orderbook_coverage(), (1, 2))

    def test_reconnect_reset_requires_fresh_orderbook_snapshots(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = build_research_event(
            "KXBTCD-26MAY0502",
            [market("KXBTCD-26MAY0502", close, "T99999.99")],
            datetime(2026, 5, 5, 5, 30, tzinfo=timezone.utc),
        )
        state = LiveMarketState()
        state.set_events([event])
        state.apply_orderbook_snapshot(
            sid=1,
            seq=1,
            msg={
                "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                "yes_dollars_fp": [["0.5000", "1.00"]],
                "no_dollars_fp": [["0.4900", "1.00"]],
            },
            received_at_ns=utc_now_ns(),
        )
        self.assertEqual(state.orderbook_coverage(), (1, 1))
        state.reset_orderbooks()
        self.assertEqual(state.orderbook_coverage(), (0, 1))

    def test_live_market_state_requires_recent_coinbase_spot(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = build_research_event(
            "KXBTCD-26MAY0502",
            [market("KXBTCD-26MAY0502", close, "T99999.99")],
            datetime(2026, 5, 5, 5, 30, tzinfo=timezone.utc),
        )
        state = LiveMarketState()
        state.set_events([event])
        state.mark_kalshi_connected(True)
        state.apply_orderbook_snapshot(
            sid=1,
            seq=1,
            msg={
                "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                "yes_dollars_fp": [["0.5000", "1.00"]],
                "no_dollars_fp": [["0.4900", "1.00"]],
            },
            received_at_ns=utc_now_ns(),
        )
        _, _, _, _, ok, reason = state.snapshot_scan_inputs()
        self.assertFalse(ok)
        self.assertEqual(reason, "coinbase_ws_disconnected")

        state.mark_coinbase_connected(True)
        state.set_btc_spot(100000.0, utc_now_ns() - 20_000_000_000)
        _, _, _, _, ok, reason = state.snapshot_scan_inputs()
        self.assertFalse(ok)
        self.assertEqual(reason, "stale_btc_spot")

        state.set_btc_spot(100000.0, utc_now_ns())
        _, _, _, _, ok, reason = state.snapshot_scan_inputs()
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_websocket_sequence_status_rejects_stale_and_gaps(self) -> None:
        ws = KalshiWsClient(None, LiveMarketState(), DummyRecorder(), queue.Queue())
        self.assertEqual(ws._seq_status(1, 10), "ok")
        self.assertEqual(ws._seq_status(1, 10), "stale")
        self.assertEqual(ws._seq_status(1, 9), "stale")
        self.assertEqual(ws._seq_status(1, 12), "gap")
        self.assertEqual(ws._seq_status(1, 11), "ok")

    def test_coalesced_update_buffer_preserves_unique_tickers_and_full_scan_flags(self) -> None:
        updates = CoalescedUpdateBuffer(max_tickers=500)
        for idx in range(100_000):
            safe_put_update(updates, {"kind": "orderbook", "market_ticker": f"KXBTCD-26MAY0502-T{idx % 188}", "source": "delta"})
        safe_put_update(updates, {"kind": "btc_spot", "source": "coinbase"})
        safe_put_update(updates, {"kind": "lifecycle", "event_ticker": "KXBTCD-26MAY0502"})
        batch = updates.get_nowait()
        self.assertEqual(batch["kind"], "batch")
        self.assertEqual(len(batch["changed_tickers"]), 188)
        self.assertIn("btc_spot", batch["flags"])
        self.assertIn("lifecycle", batch["flags"])
        self.assertIn("delta", batch["reasons"])
        with self.assertRaises(queue.Empty):
            updates.get_nowait()

    def test_capture_writer_suppresses_raw_ws_and_persists_top_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.duckdb"
            writer = LiveCaptureWriter(path, enabled=True, capture_raw_ws=False)
            for idx in range(1000):
                writer.record(
                    "ws_orderbook_delta",
                    {
                        "received_at_ns": idx,
                        "received_at_utc": "2026-05-05T00:00:00+00:00",
                        "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                        "sid": 1,
                        "seq": idx,
                        "side": "yes",
                        "price": 0.5,
                        "delta_qty": 1.0,
                        "exchange_ts": None,
                        "client_order_id": None,
                    },
                )
            writer.record(
                "ws_orderbook_top",
                {
                    "received_at_ns": 1001,
                    "received_at_utc": "2026-05-05T00:00:00+00:00",
                    "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                    "event_ticker": "KXBTCD-26MAY0502",
                    "sid": 1,
                    "seq": 1001,
                    "yes_bid": 0.49,
                    "yes_bid_qty": 1.0,
                    "yes_ask": 0.51,
                    "yes_ask_qty": 1.0,
                    "no_bid": 0.49,
                    "no_bid_qty": 1.0,
                    "no_ask": 0.51,
                    "no_ask_qty": 1.0,
                    "btc_spot": 100000.0,
                    "source": "delta",
                },
            )
            writer.close()
            self.assertEqual(writer.suppressed_by_table["ws_orderbook_delta"], 1000)
            self.assertTrue(writer.is_healthy())
            con = duckdb.connect(str(path), read_only=True)
            self.assertEqual(con.execute("SELECT count(*) FROM ws_orderbook_top").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM ws_orderbook_delta").fetchone()[0], 0)
            con.close()

    def test_kalshi_get_retries_429_with_retry_after(self) -> None:
        api = KalshiApi(require_auth=False)
        api.session = DummySession(
            [
                DummyResponse(429, {"error": "rate_limited"}, headers={"Retry-After": "0"}),
                DummyResponse(200, {"ok": True}),
            ]
        )
        with patch("scripts.btc_1hr_research_live.time.sleep", return_value=None):
            result = api._get("/markets", params={"series_ticker": "KXBTCD"}, auth=False)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(api.session.calls, 2)

    def test_get_markets_cache_can_be_bypassed_for_event_rotation(self) -> None:
        api = KalshiApi(require_auth=False)
        api.session = DummySession(
            [
                DummyResponse(200, {"markets": [{"ticker": "a"}]}),
                DummyResponse(200, {"markets": [{"ticker": "b"}]}),
            ]
        )
        first = api.get_markets(series_ticker="KXBTCD", status="open")
        cached = api.get_markets(series_ticker="KXBTCD", status="open")
        bypassed = api.get_markets(series_ticker="KXBTCD", status="open", use_cache=False)
        self.assertEqual(first["markets"][0]["ticker"], "a")
        self.assertEqual(cached["markets"][0]["ticker"], "a")
        self.assertEqual(bypassed["markets"][0]["ticker"], "b")
        self.assertEqual(api.session.calls, 2)

    def test_event_refresh_failure_clears_expired_prior_event(self) -> None:
        close = datetime.now(timezone.utc) - timedelta(minutes=1)
        event = {
            "event_ticker": "KXBTCD-26MAY0502",
            "close_time": close,
            "ttl_hours": -1.0 / 60.0,
            "markets": [market("KXBTCD-26MAY0502", close, "T99999.99")],
        }
        state = LiveMarketState()
        state.set_events([event])
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_connect(Path(tmp) / "state.db")
            executor = WsResearchExecutor(
                type("Args", (), {"mode": "paper"})(),
                KalshiApi(require_auth=False),
                None,
                conn,
                state,
                DummyRecorder(),
            )
            executor.events = [event]
            with patch("scripts.btc_1hr_research_live.scan_research_events", side_effect=RuntimeError("rate limited")):
                self.assertEqual(executor.refresh_events(), set())
            self.assertEqual(state.current_event_tickers(), set())
            self.assertEqual(state.desired_market_tickers(), set())
            executor.close()
            conn.close()

    def test_websocket_handler_ignores_stale_and_invalidates_on_gap(self) -> None:
        close = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
        event = build_research_event(
            "KXBTCD-26MAY0502",
            [market("KXBTCD-26MAY0502", close, "T99999.99")],
            datetime(2026, 5, 5, 5, 30, tzinfo=timezone.utc),
        )
        state = LiveMarketState()
        state.set_events([event])
        state.mark_kalshi_connected(True)
        recorder = DummyRecorder()
        ws = KalshiWsClient(None, state, recorder, queue.Queue())

        snapshot = {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 10,
            "msg": {
                "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                "yes_dollars_fp": [["0.6200", "10.00"]],
                "no_dollars_fp": [["0.3500", "7.00"]],
            },
        }
        asyncio.run(ws._handle_message(None, json.dumps(snapshot)))
        _, _, quotes, _, _, _ = state.snapshot_scan_inputs()
        self.assertEqual(round(quotes["KXBTCD-26MAY0502-T99999.99"].yes_ask, 4), 0.65)

        stale_delta = {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 10,
            "msg": {
                "market_ticker": "KXBTCD-26MAY0502-T99999.99",
                "side": "no",
                "price_dollars": "0.3700",
                "delta_fp": "3.00",
            },
        }
        asyncio.run(ws._handle_message(None, json.dumps(stale_delta)))
        _, _, quotes, _, _, _ = state.snapshot_scan_inputs()
        self.assertEqual(round(quotes["KXBTCD-26MAY0502-T99999.99"].yes_ask, 4), 0.65)

        gap_delta = dict(stale_delta, seq=12)
        with self.assertRaises(RuntimeError):
            asyncio.run(ws._handle_message(None, json.dumps(gap_delta)))
        self.assertEqual(state.orderbook_coverage(), (0, 1))
        _, _, _, _, ok, reason = state.snapshot_scan_inputs()
        self.assertFalse(ok)
        self.assertEqual(reason, "kalshi_ws_disconnected")

    def test_event_lock_is_atomic_across_connections(self) -> None:
        sig = sample_signal(datetime.now(timezone.utc) + timedelta(hours=1))
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "locks.db"
            conn1 = db_connect(db_path)
            conn2 = db_connect(db_path)
            self.assertTrue(acquire_event_lock(conn1, sig, "live", client_order_id="a"))
            self.assertFalse(acquire_event_lock(conn2, sig, "live", client_order_id="b"))
            self.assertIn(sig.event_ticker, db_active_events(conn2, datetime.now(timezone.utc)))
            conn1.close()
            conn2.close()

    def test_not_filled_can_release_event_lock(self) -> None:
        sig = sample_signal(datetime.now(timezone.utc) + timedelta(hours=1))
        conn = db_connect(":memory:")
        self.assertTrue(acquire_event_lock(conn, sig, "live", client_order_id="cid"))
        self.assertIn(sig.event_ticker, db_active_events(conn, datetime.now(timezone.utc)))
        release_event_lock(conn, sig.event_ticker, "cid")
        self.assertNotIn(sig.event_ticker, db_active_events(conn, datetime.now(timezone.utc)))

    def test_fok_no_fill_409_is_recognized(self) -> None:
        response = DummyResponse(
            409,
            {"error": {"code": "fill_or_kill_insufficient_resting_volume", "message": "fill or kill insufficient resting volume"}},
        )
        exc = requests.HTTPError("409", response=response)
        self.assertTrue(is_fok_no_fill_conflict(exc))

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
        self.assertIn(sig.event_ticker, db_active_events(conn, datetime.now(timezone.utc)))

        update_trade_response(
            conn,
            trade_id,
            "partial_filled",
            sig,
            {"order": {"order_id": "oid", "fill_count": "0.50", "average_fill_price": "0.4000", "average_fee_paid": "0.0100"}},
        )
        self.assertIn(sig.market_ticker, db_active_tickers(conn, datetime.now(timezone.utc)))
        self.assertIn(sig.event_ticker, db_active_events(conn, datetime.now(timezone.utc)))

    def test_market_ticker_maps_to_event_for_portfolio_dedupe(self) -> None:
        self.assertEqual(
            event_from_market_ticker("KXBTCD-26MAY0502-T99999.99"),
            "KXBTCD-26MAY0502",
        )
        self.assertEqual(
            event_from_market_ticker("KXBTCD-26MAY0502-B99999"),
            "KXBTCD-26MAY0502",
        )

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

    def test_risk_adjusted_sizing_caps_high_entry_to_one(self) -> None:
        sig = replace(sample_signal(datetime.now(timezone.utc) + timedelta(hours=1)), entry_price=0.67, model_p_yes=0.83)
        portfolio = PortfolioSnapshot(available_balance=20.0, portfolio_value=20.0, active_tickers=set(), market_exposure=0.0)
        set_sizing_policy("risk_adjusted")
        try:
            self.assertEqual(choose_contracts_for_signal(sig, portfolio, 0.0, 0.0), 1)
        finally:
            set_sizing_policy("flat_max")


if __name__ == "__main__":
    unittest.main()
