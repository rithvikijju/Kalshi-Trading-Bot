"""Build the watchlist: which Kalshi markets are live + high-volatility right now.

NBA path (flagship): pull ESPN's scoreboard, keep in-progress games, then match
open Kalshi KXNBAGAME markets to them by team abbreviation so each watched market
carries its espn_event_id + home/away tag for the fair-value model.

Generic path: any configured Kalshi series, price-only (no model), filtered to
markets that have traded recently (a liveness proxy).
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clients.kalshi_client import KalshiClient
from strategies.mean_reversion.models import MarketMeta
from strategies.mean_reversion.fair_value import NBAWinProb
from utils.logger import setup_logger

log = setup_logger("mr.discovery")

# Kalshi game-winner series per ESPN sport key
SPORT_SERIES = {"nba": "KXNBAGAME"}


async def discover_nba(kalshi: KalshiClient, espn: NBAWinProb) -> list[MarketMeta]:
    try:
        sb = await espn.scoreboard()
    except Exception as e:
        log.warning(f"ESPN scoreboard failed: {e}")
        return []

    # Per in-progress game, collect each competitor's identifying strings.
    # NOTE: ESPN abbreviates e.g. San Antonio as "SA"/"NY" while Kalshi tickers
    # use "SAS"/"NYK", so we match on team NAME, not abbreviation, then carry
    # ESPN's abbr through (that's what the fair-value model keys on).
    competitors: list[dict] = []
    for ev in sb.get("events", []):
        try:
            comp = ev["competitions"][0]
            if comp["status"]["type"]["state"] != "in":
                continue
            for c in comp["competitors"]:
                team = c.get("team", {})
                names = {(team.get(k) or "").lower()
                         for k in ("location", "displayName", "shortDisplayName", "name")}
                names.discard("")
                competitors.append({
                    "event_id": ev["id"],
                    "abbr": (team.get("abbreviation") or "").upper(),
                    "is_home": c.get("homeAway") == "home",
                    "names": names,
                })
        except Exception:
            continue

    if not competitors:
        log.info("no NBA games in progress")
        return []

    markets = await kalshi.get_markets(series_ticker=SPORT_SERIES["nba"],
                                       status="open", limit=400)
    metas: list[MarketMeta] = []
    for m in markets:
        # Kalshi game-winner market's team is in yes_sub_title ("San Antonio").
        team_str = (m.raw.get("yes_sub_title") or m.raw.get("subtitle")
                    or m.title or "").lower().strip()
        if not team_str:
            continue
        match = None
        for c in competitors:
            if any(team_str == n or team_str in n or n in team_str for n in c["names"]):
                match = c
                break
        if match:
            metas.append(MarketMeta(
                market_id=m.market_id, title=m.title, sport="nba",
                espn_event_id=match["event_id"], team_abbr=match["abbr"],
                is_home=match["is_home"], resolution_date=m.resolution_date,
            ))
    log.info(f"NBA watchlist: {len(metas)} live game-winner markets "
             f"({len(competitors)} teams in progress)")
    return metas


async def discover_generic(kalshi: KalshiClient, series: list[str],
                           max_markets: int) -> list[MarketMeta]:
    """Price-only watchlist from arbitrary series, keep recently-traded markets."""
    if not series:
        return []
    metas: list[MarketMeta] = []
    for s in series:
        try:
            mkts = await kalshi.get_markets(series_ticker=s, status="open", limit=200)
        except Exception as e:
            log.debug(f"generic series {s}: {e}")
            continue
        for m in mkts:
            last = await kalshi.get_last_trade_price(m.market_id)
            if last is not None:   # has traded at least once -> alive
                metas.append(MarketMeta(market_id=m.market_id, title=m.title,
                                        resolution_date=m.resolution_date))
            if len(metas) >= max_markets:
                return metas
    return metas


async def discover(kalshi: KalshiClient, espn: NBAWinProb, cfg: dict) -> list[MarketMeta]:
    metas: list[MarketMeta] = []
    if "nba" in cfg["scanning"]["sports"]:
        metas += await discover_nba(kalshi, espn)
    metas += await discover_generic(kalshi, cfg["scanning"]["generic_series"],
                                    cfg["scanning"]["max_markets"])
    return metas[: cfg["scanning"]["max_markets"]]
