#!/usr/bin/env python3
"""Paper-shadow multiple viable BTC 1-hour strategies on one live stream.

This is intentionally paper-only. It shares one Kalshi/Kraken websocket state
and one capture DuckDB, then writes a separate SQLite trade ledger per strategy
so that strategy locks and bankroll accounting do not interfere with each other.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import queue
import random
import sqlite3
import sys
import time

os.environ.setdefault("BTC_1HR_EXECUTOR_NAME", "btc_1hr_multi_strategy_shadow")
os.environ.setdefault("BTC_1HR_SIGNAL_STRATEGY", "research")
os.environ.setdefault("BTC_1HR_SIZING_POLICY", "risk_adjusted")
os.environ.setdefault("BTC_1HR_MAX_CONTRACTS_PER_TRADE", "1")
os.environ.setdefault("BTC_1HR_RISK_KELLY_FRACTION", "0.10")
os.environ.setdefault("BTC_1HR_RISK_NO_SIDE_CONTRACT_CAP", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG
from scripts import btc_1hr_research_live as live


DEFAULT_STRATEGIES = (
    "research",
    "market_shrink_no_cautious",
    "market_shrink_no_cautious_shape_adjacent",
    "js_guarded",
)


@dataclass
class ShadowStrategy:
    name: str
    conn: sqlite3.Connection
    db_path: Path
    bankroll: float
    last_no_signal_log: float = 0.0
    last_hourly_report_key: str | None = None

    @property
    def mode(self) -> str:
        return f"paper:{self.name}"


def parse_strategy_list(value: str) -> list[str]:
    strategies = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not strategies:
        raise argparse.ArgumentTypeError("at least one strategy is required")
    unsupported = sorted(set(strategies) - live.SUPPORTED_SIGNAL_STRATEGIES)
    if unsupported:
        raise argparse.ArgumentTypeError(
            f"unsupported strategies {unsupported}; expected one of {sorted(live.SUPPORTED_SIGNAL_STRATEGIES)}"
        )
    return list(dict.fromkeys(strategies))


def parse_args() -> argparse.Namespace:
    db_dir = Path(CFG["db_dir"]).expanduser()
    parser = argparse.ArgumentParser(description="Paper-shadow multiple BTC 1-hour strategies on one websocket feed.")
    parser.add_argument("--once", action="store_true", help="Run until the first full-book scan completes, then exit.")
    parser.add_argument("--strategies", type=parse_strategy_list, default=list(DEFAULT_STRATEGIES))
    parser.add_argument("--shadow-bankroll", type=float, default=1000.0)
    parser.add_argument("--max-contracts", type=int, default=1, help="Hard paper-fill contract cap per strategy trade.")
    parser.add_argument("--paper-report-sec", type=int, default=60)
    parser.add_argument("--print-candidates", type=int, default=4)
    parser.add_argument("--no-capture", action="store_true")
    parser.add_argument("--capture-raw-ws", action="store_true", default=live.CAPTURE_RAW_WS_DEFAULT)
    parser.add_argument("--db-dir", type=Path, default=db_dir / "multi_strategy_shadow")
    parser.add_argument("--capture-db-path", type=Path, default=db_dir / "multi_strategy_shadow_capture.duckdb")
    args = parser.parse_args()
    if args.shadow_bankroll <= 0:
        raise ValueError("--shadow-bankroll must be positive")
    if args.max_contracts <= 0:
        raise ValueError("--max-contracts must be positive")
    if args.paper_report_sec < 15:
        raise ValueError("--paper-report-sec must be >= 15")
    args.mode = "paper"
    args.loop = not args.once
    args.trade_env = "prod"
    args.market_data = "websocket"
    return args


def record_scan(
    recorder: live.LiveCaptureWriter,
    strategy: ShadowStrategy,
    started_ns: int,
    reason: str,
    changed_tickers: set[str] | None,
    evaluated_markets: int,
    events: list[dict],
    signals: list[live.TradeSignal],
    selected: list[live.TradeSignal],
    spot: float | None,
    action: str,
    detail: str,
) -> None:
    selected_signal = selected[0] if selected else None
    event_ticker = selected_signal.event_ticker if selected_signal else (events[0]["event_ticker"] if events else None)
    recorder.record(
        "signal_scan",
        {
            "received_at_ns": started_ns,
            "received_at_utc": live.ns_to_utc_iso(started_ns),
            "reason": reason,
            "mode": strategy.mode,
            "event_ticker": event_ticker,
            "changed_markets": len(changed_tickers or []),
            "evaluated_markets": evaluated_markets,
            "candidate_count": len(signals),
            "selected_market": selected_signal.market_ticker if selected_signal else None,
            "selected_side": selected_signal.side if selected_signal else None,
            "entry_price": selected_signal.entry_price if selected_signal else None,
            "net_edge_cents": selected_signal.net_edge_cents if selected_signal else None,
            "model_p_yes": selected_signal.model_p_yes if selected_signal else None,
            "btc_spot": spot if spot else None,
            "latency_ms": (live.utc_now_ns() - started_ns) / 1_000_000,
            "blocked_events": len(live.db_active_events(strategy.conn, datetime.now(timezone.utc))),
            "action": action,
            "detail": detail[:900],
        },
    )


def record_decision(
    recorder: live.LiveCaptureWriter,
    strategy: ShadowStrategy,
    signal: live.TradeSignal,
    action: str,
    detail: str,
    portfolio: live.PortfolioSnapshot | None,
    estimated_cost: float | None,
) -> None:
    received_at_ns = live.utc_now_ns()
    recorder.record(
        "order_decision",
        {
            "received_at_ns": received_at_ns,
            "received_at_utc": live.ns_to_utc_iso(received_at_ns),
            "mode": strategy.mode,
            "action": action,
            "event_ticker": signal.event_ticker,
            "market_ticker": signal.market_ticker,
            "side": signal.side,
            "contracts": signal.contracts,
            "entry_price": signal.entry_price,
            "yes_limit_price": signal.yes_limit_price,
            "net_edge_cents": signal.net_edge_cents,
            "btc_spot": signal.btc_spot,
            "estimated_cost": estimated_cost,
            "portfolio_available": portfolio.available_balance if portfolio else None,
            "portfolio_value": portfolio.portfolio_value if portfolio else None,
            "client_order_id": None,
            "detail": detail[:900],
        },
    )


def log_shadow_summary(strategy: ShadowStrategy, btc_1m, label: str = "MULTI SHADOW") -> None:
    summary = live.paper_shadow_summary(strategy.conn, btc_1m, strategy.bankroll, datetime.now(timezone.utc), mode="paper")
    live.log.info(
        "%s strategy=%s start=$%.2f equity=$%.2f realized_pnl=$%.2f bankroll_return=%.3f%% "
        "settled_premium=$%.2f premium_return=%.3f%% trades=%d settled=%d open=%d wins=%d losses=%d "
        "active_exposure=$%.2f available=$%.2f db=%s",
        label,
        strategy.name,
        summary["starting_bankroll"],
        summary["equity"],
        summary["realized_pnl"],
        100.0 * float(summary["return_on_bankroll"]),
        summary["settled_premium"],
        100.0 * float(summary["return_on_premium"]),
        summary["total_trades"],
        summary["settled_trades"],
        summary["open_trades"],
        summary["wins"],
        summary["losses"],
        summary["active_exposure"],
        summary["available_balance"],
        strategy.db_path,
    )


class MultiStrategyShadowExecutor(live.WsResearchExecutor):
    def __init__(
        self,
        args: argparse.Namespace,
        data_client: live.KalshiApi,
        strategies: list[ShadowStrategy],
        state: live.LiveMarketState,
        recorder: live.LiveCaptureWriter,
    ) -> None:
        super().__init__(args, data_client, None, strategies[0].conn, state, recorder)
        self.strategies = strategies

    def scan(self, changed_tickers: set[str] | None, reason: str) -> None:
        if self.btc_1m is None:
            return
        started_ns = live.utc_now_ns()
        events, markets_by_ticker, quotes, spot, market_ok, market_reason = self.state.snapshot_scan_inputs(None)
        evaluated_markets = len(quotes)
        if not market_ok:
            for strategy in self.strategies:
                record_scan(
                    self.recorder,
                    strategy,
                    started_ns,
                    reason,
                    changed_tickers,
                    evaluated_markets,
                    events,
                    [],
                    [],
                    spot or 0.0,
                    "skip",
                    market_reason,
                )
            return
        if not events or not markets_by_ticker or not quotes or spot is None or not math.isfinite(spot) or spot <= 0:
            detail = "missing_events_or_quotes_or_spot"
            for strategy in self.strategies:
                record_scan(
                    self.recorder,
                    strategy,
                    started_ns,
                    reason,
                    changed_tickers,
                    evaluated_markets,
                    events,
                    [],
                    [],
                    spot or 0.0,
                    "skip",
                    detail,
                )
            return
        ready_books, total_books = self.state.orderbook_coverage()
        if total_books and ready_books < total_books:
            detail = f"waiting_for_orderbook_snapshots {ready_books}/{total_books}"
            for strategy in self.strategies:
                record_scan(
                    self.recorder,
                    strategy,
                    started_ns,
                    reason,
                    changed_tickers,
                    evaluated_markets,
                    events,
                    [],
                    [],
                    spot,
                    "skip",
                    detail,
                )
            return

        for strategy in self.strategies:
            self._scan_strategy(strategy, changed_tickers, reason, events, markets_by_ticker, quotes, float(spot))

    def _scan_strategy(
        self,
        strategy: ShadowStrategy,
        changed_tickers: set[str] | None,
        reason: str,
        events: list[dict],
        markets_by_ticker: dict[str, dict],
        quotes: dict[str, live.BookQuote],
        spot: float,
    ) -> None:
        started_ns = live.utc_now_ns()
        signals = live.find_signals_from_quotes(
            self.btc_1m,
            events,
            quotes,
            self.cache_by_event,
            spot,
            executor=self.executor,
            signal_strategy=strategy.name,
        )
        if not signals:
            now = time.monotonic()
            if now - strategy.last_no_signal_log >= 30.0:
                live.log.info("MULTI SHADOW no %s signals reason=%s evaluated=%d", strategy.name, reason, len(quotes))
                strategy.last_no_signal_log = now
            record_scan(
                self.recorder,
                strategy,
                started_ns,
                reason,
                changed_tickers,
                len(quotes),
                events,
                [],
                [],
                spot,
                "none",
                "filters_rejected",
            )
            return

        now_dt = datetime.now(timezone.utc)
        blocked = live.db_active_tickers(strategy.conn, now_dt)
        blocked_events = live.db_active_events(strategy.conn, now_dt)
        portfolio = live.shadow_portfolio_snapshot(strategy.conn, self.btc_1m, strategy.bankroll, now_dt, mode="paper")
        blocked.update(portfolio.active_tickers)
        candidates = [
            sig
            for sig in signals
            if sig.market_ticker not in blocked and sig.event_ticker.upper() not in blocked_events
        ]
        selected = live.select_one_per_event(candidates, live.RESEARCH_MAX_SIGNALS_PER_CYCLE)
        if not selected:
            record_scan(
                self.recorder,
                strategy,
                started_ns,
                reason,
                changed_tickers,
                len(quotes),
                events,
                signals,
                [],
                spot,
                "blocked",
                "dedupe",
            )
            return

        for signal in selected[: self.args.print_candidates]:
            live.log.info(
                "MULTI SHADOW signal strategy=%s %s %s %s entry=%.4f net_edge=%.2fc threshold=%.2fc spread=%.2fc p_yes=%.3f qty=%.2f",
                strategy.name,
                signal.event_ticker,
                signal.market_ticker,
                signal.side.upper(),
                signal.entry_price,
                signal.net_edge_cents,
                signal.edge_threshold_cents,
                signal.spread_cents,
                signal.model_p_yes,
                signal.available_qty,
            )
        record_scan(
            self.recorder,
            strategy,
            started_ns,
            reason,
            changed_tickers,
            len(quotes),
            events,
            signals,
            selected,
            spot,
            "selected",
            "candidate",
        )

        spent_this_cycle = 0.0
        events_by_ticker = {str(event.get("event_ticker") or "").upper(): event for event in events}
        for original in selected:
            emp_cache = self.cache_by_event.get(original.event_ticker)
            if not emp_cache:
                record_decision(self.recorder, strategy, original, "skip", "missing_emp_cache", None, None)
                continue
            fresh = live.reprice_signal_from_state(
                self.state,
                original,
                events_by_ticker,
                markets_by_ticker,
                self.btc_1m,
                emp_cache,
                spot,
                signal_strategy=strategy.name,
            )
            if not fresh:
                record_decision(self.recorder, strategy, original, "skip", "failed_ws_reprice_filter", None, None)
                live.log.info("MULTI SHADOW skip strategy=%s %s: failed websocket book reprice/filter", strategy.name, original.market_ticker)
                continue
            contracts = min(int(self.args.max_contracts), int(math.floor(fresh.available_qty)))
            if contracts <= 0:
                record_decision(self.recorder, strategy, fresh, "skip", "no_visible_depth", portfolio, None)
                continue
            fresh = live.resize_signal(fresh, contracts)
            estimated_cost = live.estimated_trade_cost(fresh)
            ok, why = live.bankroll_allows_trade(fresh, portfolio, portfolio.market_exposure, spent_this_cycle)
            if not ok:
                record_decision(self.recorder, strategy, fresh, "skip", why, portfolio, estimated_cost)
                live.log.info("MULTI SHADOW skip strategy=%s %s: %s", strategy.name, fresh.market_ticker, why)
                continue
            if not live.acquire_event_lock(strategy.conn, fresh, "paper"):
                record_decision(self.recorder, strategy, fresh, "skip", "event_lock_held", portfolio, estimated_cost)
                continue
            live.log.info(
                "MULTI SHADOW PAPER fill strategy=%s %s %s x%d @ %.4f",
                strategy.name,
                fresh.market_ticker,
                fresh.side.upper(),
                fresh.contracts,
                fresh.entry_price,
            )
            live.record_trade(strategy.conn, "paper", "paper_filled", fresh)
            record_decision(self.recorder, strategy, fresh, "paper_fill", "filled", portfolio, estimated_cost)
            spent_this_cycle += estimated_cost


def drain_updates(update_queue: live.CoalescedUpdateBuffer, first_update):
    return live.drain_updates(update_queue, first_update)


def run_websocket_loop(args: argparse.Namespace, data_client: live.KalshiApi, strategies: list[ShadowStrategy], btc_1m) -> None:
    update_queue = live.CoalescedUpdateBuffer()
    recorder = live.LiveCaptureWriter(args.capture_db_path, enabled=not args.no_capture, capture_raw_ws=args.capture_raw_ws)
    state = live.LiveMarketState()
    executor = MultiStrategyShadowExecutor(args, data_client, strategies, state, recorder)
    executor.set_btc_1m(btc_1m)
    kalshi_ws: live.KalshiWsClient | None = None
    spot_ws: live.KrakenWsSpot | None = None
    try:
        live.log.info("waiting for fresh Kraken websocket BTC spot")
        market_tickers = executor.refresh_events()
        kalshi_ws = live.KalshiWsClient(data_client, state, recorder, update_queue, env=args.trade_env)
        spot_ws = live.KrakenWsSpot(state, recorder, update_queue)
        kalshi_ws.start(market_tickers)
        spot_ws.start()
        live.log.info(
            "multi-strategy websocket shadow active strategies=%s capture=%s raw_ws_capture=%s capture_db=%s max_contracts=%d",
            ",".join(strategy.name for strategy in strategies),
            not args.no_capture,
            args.capture_raw_ws,
            args.capture_db_path,
            args.max_contracts,
        )
        live.record_capture_health(recorder, state, args, "startup", "multi_strategy_shadow")

        next_event_refresh = time.monotonic() + live.WS_EVENT_REFRESH_SEC + random.uniform(0.0, live.WS_EVENT_REFRESH_JITTER_SEC)
        next_btc_refresh = time.monotonic() + live.WS_BTC_CANDLE_REFRESH_SEC
        next_health = time.monotonic() + 30.0
        next_paper_report = time.monotonic() + min(60.0, float(args.paper_report_sec))
        full_snapshot_scanned_key: tuple[str, ...] | None = None
        last_stale_btc_log = 0.0

        while True:
            now = time.monotonic()
            timeout = max(0.05, min(next_event_refresh, next_btc_refresh, next_health, next_paper_report) - now)
            first_update = None
            try:
                first_update = update_queue.get(timeout=timeout)
            except queue.Empty:
                pass
            updates = drain_updates(update_queue, first_update)
            now = time.monotonic()

            changed_tickers: set[str] = set()
            reasons: set[str] = set()
            full_scan = False
            lifecycle_refresh = False
            for item in updates:
                kind = item.get("kind")
                if kind == "batch":
                    changed_tickers.update(str(ticker).upper() for ticker in item.get("changed_tickers", set()))
                    flags = set(item.get("flags", set()))
                    reasons.update(str(reason) for reason in item.get("reasons", set()) if reason)
                    if "btc_spot" in flags or "private" in flags:
                        full_scan = True
                    if "lifecycle" in flags:
                        lifecycle_refresh = True
                        full_scan = True
                    if item.get("dropped_tickers"):
                        live.log.warning("coalesced update buffer dropped_tickers=%s", item.get("dropped_tickers"))
                elif kind == "orderbook" and item.get("market_ticker"):
                    changed_tickers.add(str(item["market_ticker"]).upper())
                    reasons.add(str(item.get("source") or "orderbook"))
                elif kind == "btc_spot":
                    full_scan = True
                    reasons.add("btc_spot")
                elif kind == "lifecycle":
                    lifecycle_refresh = True
                    full_scan = True
                    reasons.add("lifecycle")
                elif kind == "private":
                    full_scan = True
                    reasons.add("private")

            if lifecycle_refresh or now >= next_event_refresh or live.current_event_needs_refresh(executor.events):
                try:
                    old_event_key = tuple(sorted(state.current_event_tickers()))
                    old_market_tickers = state.desired_market_tickers()
                    market_tickers = executor.refresh_events()
                    if kalshi_ws:
                        kalshi_ws.update_markets(market_tickers)
                    new_event_key = tuple(sorted(state.current_event_tickers()))
                    if old_event_key != new_event_key or old_market_tickers != market_tickers:
                        full_scan = True
                        reasons.add("event_refresh")
                        full_snapshot_scanned_key = None
                except Exception:
                    live.log.exception("event refresh failed")
                next_event_refresh = now + live.WS_EVENT_REFRESH_SEC + random.uniform(0.0, live.WS_EVENT_REFRESH_JITTER_SEC)

            if now >= next_btc_refresh:
                try:
                    btc_1m = live.refresh_btc_data_cached(btc_1m)
                    executor.set_btc_1m(btc_1m)
                    if live.btc_candles_are_fresh(btc_1m):
                        reasons.add("btc_candle_refresh")
                        full_scan = True
                    elif now - last_stale_btc_log >= 30.0:
                        live.log.warning("BTC candle refresh stale; scans blocked until fresh: %s", live.describe_btc_candle_freshness(btc_1m))
                        live.record_capture_health(recorder, state, args, "stale_btc_candles", live.describe_btc_candle_freshness(btc_1m))
                        last_stale_btc_log = now
                except Exception:
                    live.log.exception("BTC candle refresh failed")
                next_btc_refresh = now + live.WS_BTC_CANDLE_REFRESH_SEC

            ready_books, total_books = state.orderbook_coverage()
            current_event_key = tuple(sorted(state.current_event_tickers()))
            if total_books and ready_books >= total_books and full_snapshot_scanned_key != current_event_key:
                full_scan = True
                reasons.add("initial_full_book")
                full_snapshot_scanned_key = current_event_key
                live.record_capture_health(recorder, state, args, "full_book_ready")

            if updates or full_scan:
                reason = "+".join(sorted(reasons)) if reasons else "websocket"
                ready_before_scan, total_before_scan = state.orderbook_coverage()
                if not live.btc_candles_are_fresh(executor.btc_1m):
                    if now - last_stale_btc_log >= 30.0:
                        live.log.warning("WS scan blocked: stale BTC candles %s reason=%s", live.describe_btc_candle_freshness(executor.btc_1m), reason)
                        live.record_capture_health(recorder, state, args, "scan_blocked", f"stale_btc_candles reason={reason}")
                        last_stale_btc_log = now
                else:
                    executor.scan(None if full_scan else changed_tickers, reason=reason)
                if not args.loop and (not total_before_scan or ready_before_scan >= total_before_scan):
                    break

            if now >= next_health:
                live.record_capture_health(recorder, state, args, "heartbeat", "multi_strategy_shadow")
                next_health = now + 30.0
            if now >= next_paper_report:
                for strategy in strategies:
                    log_shadow_summary(strategy, executor.btc_1m)
                next_paper_report = now + float(args.paper_report_sec)
            now_utc = datetime.now(timezone.utc)
            if now_utc.minute <= 5:
                hour_key = now_utc.strftime("%Y%m%d%H")
                for strategy in strategies:
                    if strategy.last_hourly_report_key != hour_key:
                        log_shadow_summary(strategy, executor.btc_1m, label="MULTI SHADOW hourly")
                        strategy.last_hourly_report_key = hour_key
    finally:
        for strategy in strategies:
            log_shadow_summary(strategy, executor.btc_1m, label="MULTI SHADOW final")
        live.record_capture_health(recorder, state, args, "shutdown", "multi_strategy_shadow")
        if kalshi_ws:
            kalshi_ws.stop()
        if spot_ws:
            spot_ws.stop()
        executor.close()
        recorder.close()


def main() -> None:
    args = parse_args()
    args.db_dir = Path(args.db_dir).expanduser()
    args.db_dir.mkdir(parents=True, exist_ok=True)
    args.capture_db_path = Path(args.capture_db_path).expanduser()
    strategies = [
        ShadowStrategy(
            name=name,
            conn=live.db_connect(args.db_dir / f"{name}_trades.db"),
            db_path=args.db_dir / f"{name}_trades.db",
            bankroll=args.shadow_bankroll,
        )
        for name in args.strategies
    ]
    live.log.info(
        "starting multi-strategy shadow mode=paper strategies=%s bankroll=$%.2f max_contracts=%d db_dir=%s capture_db=%s log=%s",
        ",".join(args.strategies),
        args.shadow_bankroll,
        args.max_contracts,
        args.db_dir,
        args.capture_db_path,
        live.LOG_FILE,
    )
    try:
        data_client = live.KalshiApi(env="prod", require_auth=True)
        live.log.info("fetching initial BTC data days=%d", live.RESEARCH_BOOTSTRAP_BTC_DAYS)
        btc_1m = live.load_btc_history_cached(live.RESEARCH_BOOTSTRAP_BTC_DAYS)
        btc_1m = live.refresh_btc_data_cached(btc_1m, require_fresh=True)
        run_websocket_loop(args, data_client, strategies, btc_1m)
    finally:
        for strategy in strategies:
            strategy.conn.close()


if __name__ == "__main__":
    main()
