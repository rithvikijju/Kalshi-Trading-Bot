"""Live trading entry point. Run after `notebooks/04_go_live_checklist.ipynb` passes.

Usage:
    python3 -m live.run_bot
"""
from __future__ import annotations
import asyncio, sys, time, signal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient
from core.event_matcher import EventMatcher, flatten_market_pairs
from core.arb_detector import detect_all
from core.portfolio import Portfolio
from core.risk_manager import RiskManager
from core.execution_engine import ExecutionEngine
from data.models import Platform
from utils.logger import setup_logger

log = setup_logger("live_bot")

KALSHI_SERIES = [
    "KXPRES", "KXSEN", "KXHOUSE", "KXIMPEACH",
    "KXBTCD", "KXETHD", "KXBTC", "KXETH", "KXSOL",
    "KXFEDDECISION", "KXCPI", "KXJOBS",
    "KXSPX", "KXNASDAQ",
    "KXWORLDCUP", "KXNBA", "KXWAR",
]


async def main_loop():
    cfg = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "config.yaml"))
    mode = cfg["mode"]
    log.info(f"Booting live bot  mode={mode}")

    k = KalshiClient(environment=cfg["kalshi"]["environment"])
    p = PolymarketClient()
    if mode == "live":
        if not k._authed:
            raise RuntimeError("Kalshi not authed. Set KALSHI_API_KEY_ID/PATH.")
        if not p._authed:
            log.warning("Polymarket not authed — live mode requires POLYMARKET_PRIVATE_KEY")
            raise RuntimeError("Polymarket creds missing for live mode")

    matcher = EventMatcher(
        min_event_score=0.45,
        strike_tolerance_pct=0.02,
        max_end_date_delta_days=14,
    )
    portfolio = Portfolio(
        db_path=cfg["paper"]["db_path"],
        starting_capital_usd=cfg["paper"]["starting_capital_usd"],
    )
    risk = RiskManager(
        max_position_per_market_usd=cfg["risk"]["max_position_per_market_usd"],
        max_total_exposure_usd=cfg["risk"]["max_total_exposure_usd"],
        max_daily_loss_usd=cfg["risk"]["max_daily_loss_usd"],
        min_net_edge_cents=cfg["arbitrage"]["min_net_edge_cents"],
        min_match_confidence=cfg["risk"]["min_match_confidence"],
        min_liquidity_usd=cfg["arbitrage"]["min_liquidity_usd"],
    )
    engine = ExecutionEngine(k, p, portfolio, risk, mode=mode,
                              leg_timeout_seconds=cfg["execution"]["leg_timeout_seconds"],
                              failed_leg_behavior=cfg["execution"]["failed_leg_behavior"])

    # Refresh markets every N cycles
    poll_interval = cfg["scanning"]["poll_interval_seconds"]
    market_refresh_every_n = max(1, int(600 / poll_interval))   # ~10min
    pairs_to_scan = []
    cycle = 0
    running = True

    def _stop(*_): nonlocal_running()
    def nonlocal_running(): nonlocal running; running = False
    signal.signal(signal.SIGINT, lambda *_: setattr(__builtins__, '_stop_flag', True))

    while running:
        try:
            if cycle % market_refresh_every_n == 0:
                k_events, p_events = await asyncio.gather(
                    k.get_events_with_markets(KALSHI_SERIES, limit_per_series=30),
                    p.get_events_with_markets(limit=300),
                )
                event_pairs = matcher.match(k_events, p_events)
                pairs = flatten_market_pairs(event_pairs)
                pairs_to_scan = [pp for pp in pairs if pp.confidence >= 0.65][:100]
                log.info(f"refreshed: {len(k_events)} K events + {len(p_events)} P events "
                         f"→ {len(event_pairs)} event-pairs → {len(pairs_to_scan)} market pairs")

            # Pull prices concurrently
            tasks = []
            for pp in pairs_to_scan:
                tasks.append(k.get_price(pp.kalshi_market.market_id))
                tasks.append(p.get_price(pp.polymarket_market.market_id))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            prices = {}
            for i, pp in enumerate(pairs_to_scan):
                kr = results[2*i]; pr = results[2*i+1]
                if not isinstance(kr, Exception):
                    prices[(Platform.KALSHI, pp.kalshi_market.market_id)] = kr
                if not isinstance(pr, Exception):
                    prices[(Platform.POLYMARKET, pp.polymarket_market.market_id)] = pr

            ops = detect_all(
                pairs_to_scan, prices,
                min_net_edge_cents=cfg["arbitrage"]["min_net_edge_cents"],
                min_liquidity_usd=cfg["arbitrage"]["min_liquidity_usd"],
                max_capital_usd=cfg["risk"]["max_position_per_market_usd"],
                max_slippage_pct=cfg["arbitrage"]["max_slippage_pct"],
            )
            for op in ops:
                await engine.execute(op)

            snap = portfolio.snapshot()
            log.info(f"cycle {cycle}: arbs={len(ops)} open={snap.open_arb_pairs} "
                     f"cash=${snap.cash_usd:.0f} realized=${snap.realized_pnl_usd:+.2f}")
            if risk.kill_switch:
                log.error(f"kill switch — stopping. reason: {risk.kill_reason}")
                break
            cycle += 1
            await asyncio.sleep(poll_interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"loop err: {e}")
            await asyncio.sleep(5)

    log.info("Shutting down")
    await k.close(); await p.close()


if __name__ == "__main__":
    asyncio.run(main_loop())
