"""Fair-value providers — the in-game model overlay.

A pure price-reversion bot will happily fade a move that was actually justified
(a star fouls out, a 12-0 real run). The fair-value overlay supplies an
independent estimate of the true YES probability so the signal engine can VETO
fades that agree with the move (mode=confirm) or only trade genuine overshoots
(mode=require).

NBAWinProb derives a live win probability from ESPN's box score:
    projected final margin = current_margin + spread * (S_remaining / 2880)
    sd_remaining           = sigma_game * sqrt(S_remaining / 2880)
    P(home win)            = Phi(projected_margin / sd_remaining)
where margin is home-away, spread is the pregame home margin (favorite negative
on the book, so home_spread_margin = -book_spread_for_home).
"""
from __future__ import annotations
import asyncio
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.mean_reversion.models import MarketMeta
from utils.logger import setup_logger

log = setup_logger("mr.fairvalue")

REG_SECONDS = 2880.0   # 4 x 12 min
OT_SECONDS = 300.0     # 5 min


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


class FairValueProvider:
    async def fair_yes_prob(self, meta: MarketMeta) -> Optional[float]:
        return None

    async def secs_to_close(self, meta: MarketMeta) -> Optional[float]:
        return None


class NoopFairValue(FairValueProvider):
    """Price-only mode: no model, nothing vetoed."""
    pass


class NBAWinProb(FairValueProvider):
    """Live NBA win-probability model fed by ESPN, cached per scoreboard pull."""

    SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"

    def __init__(self, sigma_game: float = 13.0, ttl_seconds: float = 5.0):
        self.sigma = sigma_game
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[float, dict]] = {}   # event_id -> (ts, parsed state)

    # ─── HTTP (sync fetch run in a thread to stay async-friendly) ────
    @staticmethod
    def _get(url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())

    async def scoreboard(self) -> dict:
        return await asyncio.to_thread(self._get, self.SCOREBOARD)

    async def _game_state(self, event_id: str) -> Optional[dict]:
        now = datetime.now(timezone.utc).timestamp()
        cached = self._cache.get(event_id)
        if cached and (now - cached[0]) < self.ttl:
            return cached[1]
        try:
            s = await asyncio.to_thread(self._get, self.SUMMARY.format(event_id))
        except Exception as e:
            log.debug(f"espn summary {event_id}: {e}")
            return None
        state = self._parse(s)
        if state is not None:
            self._cache[event_id] = (now, state)
        return state

    def _parse(self, s: dict) -> Optional[dict]:
        try:
            comp = s["header"]["competitions"][0]
            status = comp["status"]
            st = status["type"]["state"]            # pre | in | post
            period = int(status.get("period", 0))
            clock = float(status.get("clock", 0.0))  # seconds left in current period
            home = away = None
            home_abbr = away_abbr = None
            for c in comp["competitors"]:
                abbr = (c.get("team", {}).get("abbreviation") or "").upper()
                score = float(c.get("score", 0) or 0)
                if c.get("homeAway") == "home":
                    home, home_abbr = score, abbr
                else:
                    away, away_abbr = score, abbr
            if home is None or away is None:
                return None
            # Expected pregame HOME margin (pts). ESPN 'spread' is the home line:
            # negative when home is favored (e.g. SA -5.5), so home margin = -spread.
            spread_home = 0.0
            for o in s.get("pickcenter", []) or []:
                sp = o.get("spread")
                if sp is not None:
                    spread_home = -float(sp)
                    break
            # seconds remaining in the game (assume current period is the last unless OT going)
            if period <= 4:
                secs = max(0.0, (4 - period) * 720.0 + clock)
            else:
                secs = max(0.0, clock)  # in OT, assume it ends this period
            return {
                "state": st, "period": period, "clock": clock, "secs_remaining": secs,
                "home_score": home, "away_score": away,
                "home_abbr": home_abbr, "away_abbr": away_abbr,
                "spread_home": spread_home,
            }
        except Exception as e:
            log.debug(f"parse espn: {e}")
            return None

    def _win_prob_home(self, state: dict) -> Optional[float]:
        if state["state"] == "pre":
            return None
        if state["state"] == "post":
            return 1.0 if state["home_score"] > state["away_score"] else 0.0
        margin = state["home_score"] - state["away_score"]
        secs = state["secs_remaining"]
        frac = max(secs, 1.0) / REG_SECONDS
        # remaining expected drift from the pregame line
        projected = margin + state["spread_home"] * frac
        sd = self.sigma * math.sqrt(frac)
        if sd <= 1e-6:
            return 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
        return _norm_cdf(projected / sd)

    async def fair_yes_prob(self, meta: MarketMeta) -> Optional[float]:
        if not meta.espn_event_id or meta.team_abbr is None:
            return None
        state = await self._game_state(meta.espn_event_id)
        if state is None:
            return None
        p_home = self._win_prob_home(state)
        if p_home is None:
            return None
        team = (meta.team_abbr or "").upper()
        if team == state["home_abbr"]:
            return p_home
        if team == state["away_abbr"]:
            return 1.0 - p_home
        # fall back on is_home flag if abbreviations don't line up
        if meta.is_home is True:
            return p_home
        if meta.is_home is False:
            return 1.0 - p_home
        return None

    async def secs_to_close(self, meta: MarketMeta) -> Optional[float]:
        if not meta.espn_event_id:
            return None
        state = await self._game_state(meta.espn_event_id)
        if state is None:
            return None
        if state["state"] == "post":
            return 0.0
        if state["state"] == "pre":
            return None
        return state["secs_remaining"]
