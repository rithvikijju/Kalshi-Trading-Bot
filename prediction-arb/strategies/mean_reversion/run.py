"""Entry point for the live in-game mean-reversion strategy.

    python -m strategies.mean_reversion.run                 # paper (default)
    python -m strategies.mean_reversion.run --mode live     # real orders (needs Kalshi auth)
    python -m strategies.mean_reversion.run --reset         # wipe paper portfolio first
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import yaml
from clients.kalshi_client import KalshiClient
from strategies.mean_reversion.engine import MeanReversionEngine
from strategies.mean_reversion.portfolio import MRPortfolio
from utils.logger import setup_logger

log = setup_logger("mr.run")
CFG_PATH = Path(__file__).resolve().parent / "config.yaml"


async def main_async(cfg: dict):
    k = KalshiClient(environment="production")
    if cfg["mode"] == "live" and not k._authed:
        raise RuntimeError("Live mode requires Kalshi auth "
                           "(KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH).")
    engine = MeanReversionEngine(cfg, k)
    try:
        await engine.run()
    finally:
        await k.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "live"], default=None)
    ap.add_argument("--reset", action="store_true", help="wipe paper portfolio db")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(CFG_PATH))
    if args.mode:
        cfg["mode"] = args.mode
    if args.reset:
        MRPortfolio(cfg["paper"]["db_path"], cfg["paper"]["starting_capital_usd"]).reset()
        log.info("paper portfolio reset")
    log.info(f"mode={cfg['mode']}  fair_value={cfg['fair_value']['mode'] if cfg['fair_value']['enabled'] else 'off'}")
    asyncio.run(main_async(cfg))


if __name__ == "__main__":
    main()
