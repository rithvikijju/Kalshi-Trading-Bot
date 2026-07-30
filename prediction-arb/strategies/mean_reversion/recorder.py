"""Tape recorder: poll a live watchlist and append quotes to SQLite for backtest.

You cannot honestly trust this strategy until you've replayed it on real captured
price paths (memory: every edge in this repo shrank under honest backtesting).
Run this during live games to build a tape, then point backtest.py at it.

    python -m strategies.mean_reversion.recorder --minutes 180
"""
from __future__ import annotations
import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import yaml
from clients.kalshi_client import KalshiClient
from strategies.mean_reversion.price_feed import PriceFeed
from strategies.mean_reversion.fair_value import NBAWinProb
from strategies.mean_reversion.market_discovery import discover
from utils.logger import setup_logger

log = setup_logger("mr.recorder")
CFG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _init_db(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE IF NOT EXISTS tape (
        ts TEXT NOT NULL, market_id TEXT NOT NULL,
        yes_bid REAL, yes_ask REAL, yes_mid REAL, last REAL,
        source TEXT, fair_yes REAL, secs_to_close REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tape_mid ON tape(market_id, ts)")
    c.commit(); c.close()


async def record(minutes: float, cfg: dict):
    db = cfg["recorder"]["tape_db_path"]
    _init_db(db)
    k = KalshiClient(environment="production")
    feed = PriceFeed(k)
    espn = NBAWinProb(sigma_game=cfg["fair_value"]["sigma_game"],
                      ttl_seconds=cfg["fair_value"]["espn_ttl_seconds"])
    poll = cfg["scanning"]["poll_interval_seconds"]
    refresh = cfg["scanning"]["watchlist_refresh_seconds"]

    metas = {m.market_id: m for m in await discover(k, espn, cfg)}
    log.info(f"recording {len(metas)} markets for {minutes} min -> {db}")
    end = datetime.now(timezone.utc).timestamp() + minutes * 60
    last_refresh = datetime.now(timezone.utc).timestamp()
    rows_written = 0

    while datetime.now(timezone.utc).timestamp() < end:
        now = datetime.now(timezone.utc).timestamp()
        if now - last_refresh >= refresh:
            metas = {m.market_id: m for m in await discover(k, espn, cfg)}
            last_refresh = now
        quotes = await feed.get_quotes(list(metas.keys()))
        c = sqlite3.connect(db)
        for mid, q in quotes.items():
            meta = metas[mid]
            fair = await espn.fair_yes_prob(meta) if meta.sport == "nba" else None
            sc = await espn.secs_to_close(meta) if meta.sport == "nba" else None
            c.execute("INSERT INTO tape VALUES (?,?,?,?,?,?,?,?,?)",
                      (q.ts.isoformat(), mid, q.yes_bid, q.yes_ask, q.yes_mid,
                       q.last, q.source, fair, sc))
            rows_written += 1
        c.commit(); c.close()
        await asyncio.sleep(poll)

    log.info(f"done. {rows_written} rows written to {db}")
    await k.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=120)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(CFG_PATH))
    asyncio.run(record(args.minutes, cfg))


if __name__ == "__main__":
    main()
