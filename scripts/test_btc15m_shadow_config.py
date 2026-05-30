#!/usr/bin/env python3
"""Safety checks for BTC15M shadow configuration."""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class Btc15mShadowConfigTest(unittest.TestCase):
    def test_h02_env_gates_can_reproduce_frozen_f2_subset(self) -> None:
        env = {
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
            "BTC15M_H02_SPREAD_MAX_CENTS": "2.0",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
            "BTC15M_H02_ENTRY_MIN": "0.02",
            "BTC15M_H02_ENTRY_MAX": "0.55",
            "BTC15M_H02_MIN_SIDE_PROB": "0.60",
            "BTC15M_H02_MIN_VISIBLE_QTY": "50",
            "BTC15M_H02_MAX_CONTRACTS": "1",
            "BTC15M_H02_ALLOWED_SIDE": "yes",
        }
        with patch.dict(os.environ, env, clear=False):
            sys.modules.pop("scripts.btc15m_lowdd_live", None)
            mod = importlib.import_module("scripts.btc15m_lowdd_live")
            self.assertEqual(mod.SIGNAL_STRATEGY, mod.H02_STRATEGY)
            self.assertEqual(mod.H02_TTL_LO, 10.0)
            self.assertEqual(mod.H02_TTL_HI, 12.0)
            self.assertEqual(mod.H02_SPREAD_MAX_CENTS, 2.0)
            self.assertEqual(mod.H02_EDGE_THRESHOLD_CENTS, 12.0)
            self.assertEqual(mod.H02_ENTRY_MIN, 0.02)
            self.assertEqual(mod.H02_ENTRY_MAX, 0.55)
            self.assertEqual(mod.H02_MIN_SIDE_PROB, 0.60)
            self.assertEqual(mod.H02_MIN_VISIBLE_QTY, 50.0)
            self.assertEqual(mod.H02_MAX_CONTRACTS, 1)
            self.assertEqual(mod.H02_ALLOWED_SIDE, "yes")

    def test_h02_signal_requires_min_visible_quantity(self) -> None:
        env = {
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
            "BTC15M_H02_ENTRY_MIN": "0.02",
            "BTC15M_H02_ENTRY_MAX": "0.55",
            "BTC15M_H02_MIN_SIDE_PROB": "0.60",
            "BTC15M_H02_MIN_VISIBLE_QTY": "50",
        }
        with patch.dict(os.environ, env, clear=False):
            sys.modules.pop("scripts.btc15m_lowdd_live", None)
            mod = importlib.import_module("scripts.btc15m_lowdd_live")
            close = datetime.now(timezone.utc) + timedelta(minutes=11)
            event = {"event_ticker": "KXBTC15M-TEST", "close_time": close.isoformat()}
            market = {"ticker": "KXBTC15M-TEST-00", "floor_strike": 99.0}
            state = mod.live.LiveMarketState()
            state.set_btc_spot(100.0, mod.live.utc_now_ns())

            thin_quote = mod.live.BookQuote("KXBTC15M-TEST-00", 0.44, 100, 0.45, 49, 0.54, 100, 0.55, 100)
            signal, detail = mod.build_h02_signal(
                event=event,
                market=market,
                quote=thin_quote,
                btc_vol_history=mod.BtcMinuteVolHistory(),
                state=state,
            )
            self.assertIsNone(signal)
            self.assertIn("min_qty=50.00", detail)

            thick_quote = mod.live.BookQuote("KXBTC15M-TEST-00", 0.44, 100, 0.45, 50, 0.54, 100, 0.55, 100)
            signal, detail = mod.build_h02_signal(
                event=event,
                market=market,
                quote=thick_quote,
                btc_vol_history=mod.BtcMinuteVolHistory(),
                state=state,
            )
            self.assertIsNotNone(signal)
            self.assertEqual(signal.side, "yes")
            self.assertGreaterEqual(signal.available_qty, 50)

    def test_h02_event_ttl_reject_is_event_level(self) -> None:
        env = {
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
        }
        with patch.dict(os.environ, env, clear=False):
            sys.modules.pop("scripts.btc15m_lowdd_live", None)
            mod = importlib.import_module("scripts.btc15m_lowdd_live")
            now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
            early = {"close_time": (now + timedelta(minutes=9, seconds=30)).isoformat()}
            valid = {"close_time": (now + timedelta(minutes=11)).isoformat()}
            late = {"close_time": (now + timedelta(minutes=12, seconds=30)).isoformat()}

            self.assertIn("h02_ttl_outside_9.50", mod.h02_event_ttl_reject_detail(early, now=now))
            self.assertIsNone(mod.h02_event_ttl_reject_detail(valid, now=now))
            self.assertIn("h02_ttl_outside_12.50", mod.h02_event_ttl_reject_detail(late, now=now))

    def test_coalesced_batch_changed_ticker_key_is_used(self) -> None:
        import scripts.btc15m_lowdd_live as mod

        batch = {"kind": "batch", "changed_tickers": {"kxbtc15m-test-1", "KXBTC15M-TEST-2"}}
        self.assertEqual(
            mod.batch_changed_tickers(batch),
            {"KXBTC15M-TEST-1", "KXBTC15M-TEST-2"},
        )

    def test_h02_first_signal_qty_skip_is_distinct_from_base_min_qty(self) -> None:
        env = {
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_H02_TTL_LO": "10.0",
            "BTC15M_H02_TTL_HI": "12.0",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
            "BTC15M_H02_ENTRY_MIN": "0.02",
            "BTC15M_H02_ENTRY_MAX": "0.50",
            "BTC15M_H02_MIN_SIDE_PROB": "0.60",
            "BTC15M_H02_MIN_VISIBLE_QTY": "250",
            "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "500",
        }
        with patch.dict(os.environ, env, clear=False):
            sys.modules.pop("scripts.btc15m_lowdd_live", None)
            mod = importlib.import_module("scripts.btc15m_lowdd_live")
            close = datetime.now(timezone.utc) + timedelta(minutes=11)
            event = {"event_ticker": "KXBTC15M-TEST", "close_time": close.isoformat()}
            market = {"ticker": "KXBTC15M-TEST-00", "floor_strike": 99.0}
            state = mod.live.LiveMarketState()
            state.set_btc_spot(100.0, mod.live.utc_now_ns())

            base_qty_quote = mod.live.BookQuote("KXBTC15M-TEST-00", 0.44, 100, 0.45, 300, 0.54, 100, 0.55, 100)
            signal, detail = mod.build_h02_signal(
                event=event,
                market=market,
                quote=base_qty_quote,
                btc_vol_history=mod.BtcMinuteVolHistory(),
                state=state,
            )
            self.assertIsNone(signal)
            self.assertIn("h02_first_signal_skip", detail)
            self.assertIn("first_min_qty=500.00", detail)

            first_skip_quote = mod.live.BookQuote("KXBTC15M-TEST-00", 0.44, 100, 0.45, 500, 0.54, 100, 0.55, 100)
            signal, _detail = mod.build_h02_signal(
                event=event,
                market=market,
                quote=first_skip_quote,
                btc_vol_history=mod.BtcMinuteVolHistory(),
                state=state,
            )
            self.assertIsNotNone(signal)
            self.assertEqual(signal.available_qty, 500)

    def test_shadow_wrapper_is_paper_mode(self) -> None:
        import scripts.btc15m_f2_ttl10_12_shadow as shadow

        source = shadow.Path(shadow.__file__).read_text(encoding="utf-8")
        self.assertIn('"paper"', source)
        self.assertIn('"--shadow-bankroll"', source)
        self.assertIn('"100"', source)
        self.assertIn('"BTC15M_H02_MIN_VISIBLE_QTY", "50"', source)
        self.assertIn('"BTC15M_H02_MIN_SIDE_PROB", "0.60"', source)

    def test_q1000_yes_live_wrapper_is_conservative_live_mode(self) -> None:
        import scripts.btc15m_f2_q1000_yes_live as wrapper

        source = wrapper.Path(wrapper.__file__).read_text(encoding="utf-8")
        self.assertIn('"live"', source)
        self.assertIn('"BTC15M_H02_ALLOWED_SIDE", "yes"', source)
        self.assertIn('"BTC15M_H02_MIN_VISIBLE_QTY", "1000"', source)
        self.assertIn('"BTC15M_H02_MAX_CONTRACTS", "1"', source)
        self.assertIn('"BTC15M_ROLLING_TRADE_CAP", "4"', source)
        self.assertIn('"BTC15M_ROLLING_PREMIUM_CAP_DOLLARS", "2.00"', source)

    def test_q250_qty500_firstskip_wrapper_is_paper_only(self) -> None:
        import scripts.btc15m_f2_q250_qty500_firstskip_shadow as wrapper

        source = wrapper.Path(wrapper.__file__).read_text(encoding="utf-8")
        self.assertIn('"paper"', source)
        self.assertIn('"BTC15M_H02_MIN_VISIBLE_QTY"] = "250"', source)
        self.assertIn('"BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY"] = "500"', source)
        self.assertIn('"BTC15M_H02_MAX_CONTRACTS"] = "1"', source)

    def test_q250_qty500_firstskip_yes_wrapper_is_paper_only(self) -> None:
        import scripts.btc15m_f2_q250_qty500_firstskip_yes_shadow as wrapper

        source = wrapper.Path(wrapper.__file__).read_text(encoding="utf-8")
        self.assertIn('"paper"', source)
        self.assertIn('"BTC15M_H02_ALLOWED_SIDE"] = "yes"', source)
        self.assertIn('"BTC15M_H02_MIN_VISIBLE_QTY"] = "250"', source)
        self.assertIn('"BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY"] = "500"', source)
        self.assertIn('"BTC15M_H02_MAX_CONTRACTS"] = "1"', source)

    def test_q250_yes_wrapper_overrides_polluted_environment(self) -> None:
        import scripts.btc15m_f2_q250_qty500_firstskip_yes_shadow as wrapper
        import scripts.btc15m_lowdd_live as live_mod

        keys = [
            "BTC15M_SIGNAL_STRATEGY",
            "BTC15M_H02_TTL_LO",
            "BTC15M_H02_TTL_HI",
            "BTC15M_H02_SPREAD_MAX_CENTS",
            "BTC15M_H02_EDGE_THRESHOLD_CENTS",
            "BTC15M_H02_ENTRY_MIN",
            "BTC15M_H02_ENTRY_MAX",
            "BTC15M_H02_MIN_SIDE_PROB",
            "BTC15M_H02_MIN_VISIBLE_QTY",
            "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY",
            "BTC15M_H02_ALLOWED_SIDE",
            "BTC15M_H02_MAX_CONTRACTS",
        ]
        polluted_env = {key: "polluted" for key in keys}
        captured: dict[str, object] = {}

        def fake_main() -> None:
            captured["argv"] = list(sys.argv)
            captured["env"] = {key: os.environ.get(key) for key in keys}

        old_argv = list(sys.argv)
        try:
            with patch.dict(os.environ, polluted_env, clear=False), patch.object(live_mod, "main", fake_main):
                wrapper.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(
            captured["env"],
            {
                "BTC15M_SIGNAL_STRATEGY": "h02",
                "BTC15M_H02_TTL_LO": "10.0",
                "BTC15M_H02_TTL_HI": "12.0",
                "BTC15M_H02_SPREAD_MAX_CENTS": "2.0",
                "BTC15M_H02_EDGE_THRESHOLD_CENTS": "12.0",
                "BTC15M_H02_ENTRY_MIN": "0.02",
                "BTC15M_H02_ENTRY_MAX": "0.50",
                "BTC15M_H02_MIN_SIDE_PROB": "0.60",
                "BTC15M_H02_MIN_VISIBLE_QTY": "250",
                "BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY": "500",
                "BTC15M_H02_ALLOWED_SIDE": "yes",
                "BTC15M_H02_MAX_CONTRACTS": "1",
            },
        )
        argv = captured["argv"]
        self.assertIsInstance(argv, list)
        self.assertIn("--mode", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "paper")
        self.assertIn("--strategy", argv)
        self.assertEqual(argv[argv.index("--strategy") + 1], "h02")
        self.assertIn("btc15m_f2_q250_qty500_firstskip_yes_shadow_trades.db", " ".join(argv))

    def test_rolling_risk_cap_blocks_after_existing_at_risk_trade(self) -> None:
        env = {
            "BTC15M_SIGNAL_STRATEGY": "h02",
            "BTC15M_RISK_WINDOW_HOURS": "24.0",
            "BTC15M_ROLLING_TRADE_CAP": "1",
            "BTC15M_ROLLING_PREMIUM_CAP_DOLLARS": "2.00",
        }
        with patch.dict(os.environ, env, clear=False):
            sys.modules.pop("scripts.btc15m_lowdd_live", None)
            mod = importlib.import_module("scripts.btc15m_lowdd_live")
            conn = mod.live.db_connect(":memory:")
            close = datetime.now(timezone.utc) + timedelta(minutes=10)
            signal = mod.live.TradeSignal(
                event_ticker="KXBTC15M-TEST",
                market_ticker="KXBTC15M-TEST-00",
                side="yes",
                contracts=1,
                entry_price=0.45,
                yes_order_side="bid",
                yes_limit_price=0.45,
                available_qty=1000,
                model_p_yes=0.70,
                edge_gross_cents=25.0,
                entry_fee=0.01,
                net_edge_cents=24.0,
                edge_threshold_cents=12.0,
                spread_cents=1.0,
                strike=100.0,
                btc_spot=101.0,
                ttl_min=10.0,
                close_time=close.isoformat(),
                yes_bid=0.44,
                yes_ask=0.45,
                no_bid=0.54,
                no_ask=0.55,
            )
            mod.live.record_trade(conn, mod.MODE_LIVE, "filled", signal, client_order_id="test-risk-cap")
            reason, summary = mod.rolling_risk_rejection(conn, mod.MODE_LIVE, datetime.now(timezone.utc), 0.10)
            self.assertEqual(summary["at_risk_trades"], 1)
            self.assertIn("rolling_trade_cap", reason or "")

    def test_lowdd_paper_summary_releases_closed_trade_with_official_result(self) -> None:
        import scripts.btc15m_lowdd_live as mod

        mod._PAPER_OFFICIAL_RESULT_CACHE.clear()
        conn = mod.live.db_connect(":memory:")
        close = datetime.now(timezone.utc) - timedelta(hours=2)
        signal = mod.live.TradeSignal(
            event_ticker="KXBTC15M-TEST",
            market_ticker="KXBTC15M-TEST-00",
            side="yes",
            contracts=1,
            entry_price=0.45,
            yes_order_side="bid",
            yes_limit_price=0.45,
            available_qty=1000,
            model_p_yes=0.70,
            edge_gross_cents=25.0,
            entry_fee=0.01,
            net_edge_cents=24.0,
            edge_threshold_cents=12.0,
            spread_cents=1.0,
            strike=100.0,
            btc_spot=101.0,
            ttl_min=4.0,
            close_time=close.isoformat(),
            yes_bid=0.44,
            yes_ask=0.45,
            no_bid=0.54,
            no_ask=0.55,
        )
        mod.live.record_trade(conn, mod.paper_mode_name(), "paper_filled", signal, client_order_id="test-official-release")

        class Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"market": {"ticker": signal.market_ticker, "result": "yes", "status": "finalized"}}

        with patch("scripts.btc15m_lowdd_live.requests.get", return_value=Response()):
            summary = mod.paper_shadow_summary(conn, mod.BtcMinuteVolHistory(), 100.0)

        self.assertEqual(summary["settled_trades"], 1)
        self.assertEqual(summary["open_trades"], 0)
        self.assertEqual(summary["active_exposure"], 0.0)
        self.assertGreater(summary["available_balance"], 100.0)


if __name__ == "__main__":
    unittest.main()
