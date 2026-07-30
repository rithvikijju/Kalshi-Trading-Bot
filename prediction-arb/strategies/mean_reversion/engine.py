"""Orchestration loop: discover -> quote -> signal -> manage exits -> enter.

Paper mode simulates fills at the quoted side price plus slippage (and applies
the same Kalshi fee model the gate assumes). Live mode routes marketable limit
orders through KalshiClient. Defaults to paper; live requires Kalshi auth.
"""
from __future__ import annotations
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clients.kalshi_client import KalshiClient
from data.models import OrderRequest, OrderStatus, Platform, Side
from strategies.mean_reversion.models import MarketMeta, MRPosition, Quote, Signal
from strategies.mean_reversion.price_feed import PriceFeed
from strategies.mean_reversion.signal_engine import SignalEngine
from strategies.mean_reversion.fair_value import NBAWinProb, NoopFairValue
from strategies.mean_reversion.market_discovery import discover
from strategies.mean_reversion.portfolio import MRPortfolio
from strategies.mean_reversion.risk import MRRiskManager
from utils.logger import setup_logger

log = setup_logger("mr.engine")


class MeanReversionEngine:
    def __init__(self, cfg: dict, kalshi: KalshiClient):
        self.cfg = cfg
        self.k = kalshi
        self.mode = cfg["mode"]
        self.feed = PriceFeed(kalshi)
        self.signals = SignalEngine(cfg)
        self.risk = MRRiskManager(cfg)
        self.pf = MRPortfolio(cfg["paper"]["db_path"],
                              cfg["paper"]["starting_capital_usd"],
                              fee_model=cfg["fees"]["model"])
        fv = cfg["fair_value"]
        self.fair = (NBAWinProb(sigma_game=fv["sigma_game"],
                                ttl_seconds=fv["espn_ttl_seconds"])
                     if fv["enabled"] else NoopFairValue())
        self.slip = cfg["fees"]["slippage_cents"] / 100.0
        self.size = cfg["sizing"]["contracts_per_trade"]
        self.max_ct = cfg["sizing"]["max_contracts_per_market"]

        self._watch: dict[str, MarketMeta] = {}
        self._open: dict[int, MRPosition] = {}     # pos_id -> position

    # ─── lifecycle ─────────────────────────────────────────────────
    async def refresh_watchlist(self):
        metas = await discover(self.k, self.fair if isinstance(self.fair, NBAWinProb)
                               else NBAWinProb(), self.cfg)
        self._watch = {m.market_id: m for m in metas}
        log.info(f"watchlist: {len(self._watch)} markets")

    def _load_open(self):
        self._open = {p.pos_id: p for p in self.pf.open_positions()}

    async def _fair_and_close(self, meta: MarketMeta):
        """(fair_yes_prob, secs_to_close) for a market, model permitting."""
        if meta.sport != "nba":
            return None, None
        fp = await self.fair.fair_yes_prob(meta)
        sc = await self.fair.secs_to_close(meta)
        return fp, sc

    # ─── one cycle ─────────────────────────────────────────────────
    async def cycle(self):
        if not self._watch:
            return
        ids = list(self._watch.keys())
        quotes = await self.feed.get_quotes(ids)

        # 1. manage exits on open positions first (free up capital / cut risk)
        self._load_open()
        for pos in list(self._open.values()):
            q = quotes.get(pos.market_id)
            if q is None:
                continue
            meta = self._watch.get(pos.market_id, MarketMeta(market_id=pos.market_id))
            _, sc = await self._fair_and_close(meta)
            ex = self.signals.check_exit(pos, q, datetime.now(timezone.utc), sc)
            if ex:
                exit_price, reason = ex
                await self._exit(pos, exit_price, reason)

        # 2. look for new entries
        self.risk.check_daily_loss(self.pf)
        for mid in ids:
            q = quotes.get(mid)
            if q is None:
                continue
            meta = self._watch[mid]
            fp, sc = await self._fair_and_close(meta)
            sig = self.signals.on_quote(q, fair_yes_prob=fp, secs_to_close=sc)
            if sig:
                await self._enter(sig, q)

    async def _enter(self, sig: Signal, q: Quote):
        held_now = self.pf.market_exposure_usd(sig.market_id)
        size = min(self.size, self.max_ct)
        if size < 1:
            return
        fill = min(0.99, sig.entry_price + self.slip)   # pay up slightly
        ok, reason = self.risk.approve_entry(sig, size, fill, self.pf)
        if not ok:
            log.debug(f"skip {sig.market_id}: {reason}")
            return
        if self.mode == "live":
            filled = await self._live_buy(sig, size, fill)
            if not filled:
                return
            fill = filled
        pos = self.pf.open_position(sig, size, fill)
        self._open[pos.pos_id] = pos
        log.info(f"ENTER {sig.type.value} {sig.market_id} {sig.side.value} "
                 f"x{size} @ {fill:.2f} tgt={sig.target_price:.2f} "
                 f"stop={sig.stop_price:.2f} edge={sig.expected_edge_cents:.1f}c "
                 f"fair={sig.fair_yes_prob}")

    async def _exit(self, pos: MRPosition, exit_price: float, reason: str):
        fill = max(0.01, exit_price - self.slip)        # receive slightly less
        if self.mode == "live":
            got = await self._live_sell(pos, fill)
            if got is not None:
                fill = got
        pnl = self.pf.close_position(pos, fill, reason)
        self._open.pop(pos.pos_id, None)
        log.info(f"EXIT  {pos.market_id} {pos.side.value} @ {fill:.2f} "
                 f"({reason}) pnl=${pnl:+.2f}")

    # ─── live order routing (simplified marketable-limit) ──────────
    async def _live_buy(self, sig: Signal, size: int, limit: float):
        try:
            resp = await self.k.place_order(OrderRequest(
                platform=Platform.KALSHI, market_id=sig.market_id, side=sig.side,
                action="buy", size=size, limit_price=round(limit, 2)))
        except Exception as e:
            log.error(f"live buy failed {sig.market_id}: {e}")
            return None
        if resp.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
            return resp.avg_fill_price or limit
        return None

    async def _live_sell(self, pos: MRPosition, limit: float):
        try:
            resp = await self.k.place_order(OrderRequest(
                platform=Platform.KALSHI, market_id=pos.market_id, side=pos.side,
                action="sell", size=int(pos.size), limit_price=round(limit, 2)))
        except Exception as e:
            log.error(f"live sell failed {pos.market_id}: {e}")
            return None
        if resp.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
            return resp.avg_fill_price or limit
        return None

    # ─── run forever ───────────────────────────────────────────────
    async def run(self):
        log.info(f"mean-reversion engine starting  mode={self.mode}")
        poll = self.cfg["scanning"]["poll_interval_seconds"]
        refresh = self.cfg["scanning"]["watchlist_refresh_seconds"]
        await self.refresh_watchlist()
        last_refresh = datetime.now(timezone.utc)
        cycle = 0
        while True:
            try:
                if (datetime.now(timezone.utc) - last_refresh).total_seconds() >= refresh:
                    await self.refresh_watchlist()
                    last_refresh = datetime.now(timezone.utc)
                await self.cycle()
                if cycle % 10 == 0:
                    s = self.pf.stats()
                    log.info(f"cycle {cycle}: open={s['open']} closed={s['closed']} "
                             f"wins={s['wins']} losses={s['losses']} "
                             f"pnl=${s['pnl_usd']:+.2f} cash=${s['cash_usd']:.0f}")
                if self.risk.kill_switch:
                    log.error(f"kill switch -> stopping: {self.risk.kill_reason}")
                    break
                cycle += 1
                await asyncio.sleep(poll)
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"cycle err: {e}")
                await asyncio.sleep(3)
        log.info("engine stopped")
