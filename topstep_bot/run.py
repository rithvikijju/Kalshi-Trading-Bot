"""
Entrypoint: wire broker + Discord + Trader and run.

    python -m topstep_bot.run --mode sim          # paper-trade the LIVE feed (default)
    python -m topstep_bot.run --mode live         # send REAL orders (explicit promotion)
    python -m topstep_bot.run --mode sim --no-discord   # console alerts only

Both modes need TopStep API creds (sim still consumes the live market feed; it just
simulates fills). Promotion to live is deliberately a separate restart, not a toggle.
"""
from __future__ import annotations

import argparse
import asyncio

from .config import BotConfig
from .bot.trader import Trader
from .broker.projectx import ProjectXBroker
from .broker.sim import SimBroker


def build_broker(cfg: BotConfig):
    live_feed = ProjectXBroker(cfg.username, cfg.api_key, cfg.account_name)
    if cfg.mode == "live":
        return live_feed
    # sim: real market data, simulated fills
    return SimBroker(data_source=live_feed)


async def main_async(cfg: BotConfig, use_discord: bool):
    broker = build_broker(cfg)

    if use_discord and cfg.discord_token:
        from .bot.discord_bot import DiscordControl
        control = DiscordControl(cfg)
        trader = Trader(cfg, broker, notify=control.notify)
        control.trader = trader
        await asyncio.gather(control.run(), _start_then_idle(trader))
    else:
        async def console_notify(msg):
            print(msg, flush=True)
        trader = Trader(cfg, broker, notify=console_notify)
        await _start_then_idle(trader)


async def _start_then_idle(trader: Trader):
    await trader.start()
    while True:                      # event-driven; keep the loop alive for callbacks
        await asyncio.sleep(3600)


def main():
    ap = argparse.ArgumentParser(description="TopStep ICT bot")
    ap.add_argument("--mode", choices=["sim", "live"], default="sim")
    ap.add_argument("--account", default="50K", choices=["50K", "100K", "150K"])
    ap.add_argument("--per-trade-risk", type=float, default=200.0)
    ap.add_argument("--manual-approve", action="store_true",
                    help="require !approve in Discord before every entry")
    ap.add_argument("--no-discord", action="store_true")
    ap.add_argument("--verbose", action="store_true",
                    help="stream the per-bar reasoning (structure read + every setup + why skipped)")
    args = ap.parse_args()

    cfg = BotConfig(mode=args.mode, account_size=args.account,
                    per_trade_risk=args.per_trade_risk,
                    require_manual_approve=args.manual_approve,
                    verbose=args.verbose)
    if cfg.mode == "live" and not cfg.api_key:
        raise SystemExit("TOPSTEP_API_KEY not set — refusing to run live without creds.")
    print(f"Starting TopStep ICT bot | mode={cfg.mode} account={cfg.account_size} "
          f"instruments={[i.key for i in cfg.enabled_instruments()]}")
    try:
        asyncio.run(main_async(cfg, use_discord=not args.no_discord))
    except KeyboardInterrupt:
        print("shutting down.")


if __name__ == "__main__":
    main()
