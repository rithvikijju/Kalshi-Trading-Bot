"""
Trader — the live decision loop.

Per closed 1-min bar:  signal engine → setup?  → confidence → gates (killzone, R:R,
confidence) → deterministic risk cap × confidence scale → place bracketed order.
Per quote:             mark positions, enforce the trailing-DD floor in real time, and
                       flatten before the session close (never carry overnight).
Per fill:              update the risk engine on realized PnL, label the originating setup
                       (win/loss) into the capture DB, and periodically retrain the model.

Identical in sim and live — only the injected Broker differs.
"""
from __future__ import annotations

import asyncio

from ..broker.base import Broker, Fill, Order, OrderType, Position, Quote, Side
from ..data.bars import BarAggregator
from ..data.capture import Capture
from ..ml.confidence import ConfidenceModel
from ..risk.engine import LiveRiskManager
from ..risk.sizing import decide_size
from ..signals.engine import SignalEngine
from ..signals.sessions import current_killzone, et_dt, minutes_to_session_close


class Trader:
    def __init__(self, cfg, broker: Broker, notify=None):
        self.cfg = cfg
        self.broker = broker
        self.notify = notify or (lambda *_: asyncio.sleep(0))
        self.risk = LiveRiskManager(cfg)
        self.engine = SignalEngine(min_bars=max(60, cfg.warmup_bars // 4),
                                   min_target_r=cfg.min_target_r)
        self.model = ConfidenceModel(cfg.model_path)
        self.cap = Capture(cfg.db_path)
        self.aggs: dict[str, BarAggregator] = {}
        self.last_quote: dict[str, Quote] = {}
        self._last_book_ts: dict[str, float] = {}
        self.open_setup: dict[str, dict] = {}     # instrument -> {sid, setup, ts_in, size, side, entry}
        self.pending: dict[str, dict] = {}        # manual-approve queue
        self.paused = False
        self.halted = False                       # hard stop (floor breach / blown)
        self._last_et_date = None
        self._closed_trades = 0

    # ----------------------------------------------------------------- startup
    async def start(self):
        await self.broker.connect()
        instruments = [i.key for i in self.cfg.enabled_instruments()]
        await self.broker.subscribe(instruments)
        await self._warmup(instruments)
        for key in instruments:
            agg = self.aggs[key]
            agg.on_bar(self._on_bar)
        self.broker.on_quote(self._on_quote)
        self.broker.on_trade(self._on_trade)
        self.broker.on_depth(self._on_depth)
        self.broker.on_fill(self._on_fill)
        asyncio.create_task(self._heartbeat())
        await self._say(f"🟢 Trader started — mode={self.cfg.mode.upper()} "
                        f"account={self.cfg.account_size} instruments={instruments}\n"
                        f"{self._risk_line()}")

    async def _warmup(self, instruments):
        src = getattr(self.broker, "data_source", None) or self.broker
        for key in instruments:
            self.aggs[key] = BarAggregator(key, self.cfg.bar_minutes)
            if hasattr(src, "retrieve_bars"):
                try:
                    bars = await src.retrieve_bars(key, self.cfg.bar_minutes, self.cfg.warmup_bars)
                    self.aggs[key].seed(bars)
                    for b in bars:
                        self.cap.save_bar(b)
                except Exception as e:
                    await self._say(f"⚠️ warmup history failed for {key}: {e}")

    async def _heartbeat(self):
        """Write a status snapshot every 30s so progress can be checked without touching the
        DB the bot is writing to (single-connection, no lock contention)."""
        import json
        import time
        path = "topstep_bot/data/status.json"
        while True:
            try:
                data = dict(
                    updated=time.strftime("%Y-%m-%d %H:%M:%S"), mode=self.cfg.mode,
                    paused=self.paused, halted=self.halted, model=self.model.mode(),
                    risk=self.risk.snapshot(),
                    positions={i: p.size for i, p in self.broker.positions.items() if not p.flat},
                    unrealized=round(self._unrealized(), 2),
                    capture=self.cap.micro_stats())
                with open(path, "w") as f:
                    json.dump(data, f, indent=2, default=str)
            except Exception:
                pass
            await asyncio.sleep(30)

    # ----------------------------------------------------------------- feed
    async def _on_trade(self, t):
        agg = self.aggs.get(t.instrument)
        if agg:
            await agg.update_trade(t)
        if self.cfg.capture_microstructure:
            self.cap.save_tape(t.instrument, t.ts, t.price, t.volume, t.aggressor)

    async def _on_depth(self, d):
        """Record a DOM snapshot — real book imbalance + micro-price."""
        if not self.cfg.capture_microstructure:
            return
        last = self._last_book_ts.get(d.instrument, 0.0)
        if d.ts - last < 1.0 / max(self.cfg.book_snap_hz, 0.1):
            return
        self._last_book_ts[d.instrument] = d.ts
        bid, bsz = (d.bids[0] if d.bids else (0.0, 0.0))
        ask, asz = (d.asks[0] if d.asks else (0.0, 0.0))
        self.cap.save_book(d.instrument, d.ts, bid, ask, bsz, asz,
                           d.imbalance(), d.micro_price())

    async def _on_quote(self, q: Quote):
        self.last_quote[q.instrument] = q
        agg = self.aggs.get(q.instrument)
        if agg and self.broker.meta(q.instrument):  # quote-fallback bars only if needed
            await agg.update_quote(q)
        await self._risk_watch()
        await self._session_watch(q.ts)

    def _unrealized(self) -> float:
        total = 0.0
        for inst, pos in self.broker.positions.items():
            if pos.flat:
                continue
            q = self.last_quote.get(inst)
            meta = self.broker.meta(inst)
            if not q or not meta or not meta.tick_size:
                continue
            dpp = meta.tick_value / meta.tick_size
            mark = q.mid
            total += (mark - pos.avg_price) * (1 if pos.size > 0 else -1) * abs(pos.size) * dpp
        return total

    async def _risk_watch(self):
        if self.halted:
            return
        unrl = self._unrealized()
        if self.risk.hard_floor_breached(unrl):
            self.halted = True
            await self.broker.flatten_all()
            await self._say(f"🛑 TRAILING-DD FLOOR BREACH — flattened all, trading halted.\n"
                            f"{self._risk_line()}")

    async def _session_watch(self, ts: float):
        d = et_dt(ts).date()
        if self._last_et_date is None:
            self._last_et_date = d
        elif d != self._last_et_date:
            self.risk.end_of_day()
            self._last_et_date = d
            self.halted = self.risk.eng.blown
            await self._say(f"📅 New session {d}. {self._risk_line()}")
        # flatten before the cash close
        m = minutes_to_session_close(ts)
        if 0 < m <= self.cfg.flatten_before_close_min:
            for inst, pos in list(self.broker.positions.items()):
                if not pos.flat:
                    await self.broker.flatten(inst)
                    await self._say(f"⏰ Flattening {inst} {m:.1f}m before close.")

    # ----------------------------------------------------------------- signal → order
    async def _on_bar(self, bar):
        self.cap.save_bar(bar)
        inst = bar.instrument
        window = self.aggs[inst].history
        if self.cfg.verbose:
            d = self.engine.diagnose(inst, window)
            if "status" in d:
                await self._say(f"🧠 {inst} {d['status']}")
            else:
                await self._say(
                    f"🧠 {inst} {d['close']:.2f} | trend={d['trend']} bos={int(d['bos'])} "
                    f"choch={int(d['choch'])} | atr={d['atr']} fvg={d['n_fvg']} ob={d['n_ob']} "
                    f"sweep={'Y' if d['sweep'] else '-'} | KZ={d['kz']}")
        if self.halted or self.paused:
            return
        setup = self.engine.generate(inst, window)
        if setup is None:
            return
        confidence = self.model.predict(setup, bars=window)
        meta = self.broker.meta(inst)
        n_open = sum(1 for p in self.broker.positions.values() if not p.flat)

        # evaluate every gate so verbose mode can report exactly why a setup is skipped
        skip = None
        if setup.rr < self.cfg.min_rr:
            skip = f"R:R {setup.rr:.2f} < {self.cfg.min_rr}"
        elif self.cfg.trade_only_in_killzones and current_killzone(bar.ts) is None:
            skip = "outside killzone"
        elif not self.broker.position(inst).flat:
            skip = "already in a position"
        elif not self.risk.can_enter(n_open, self._unrealized()):
            skip = self.risk.reason_blocked
        decision = None
        if skip is None:
            decision = decide_size(setup, meta, self.risk, confidence,
                                   self.cfg.min_confidence, n_open, self._unrealized())
            if decision.size <= 0:
                skip = decision.reason

        if self.cfg.verbose:
            verdict = f"SKIP ({skip})" if skip else f"TAKE x{decision.size}"
            await self._say(
                f"   ↳ {setup.archetype} {setup.direction.name} entry {setup.entry:.2f} "
                f"stop {setup.stop:.2f} tgt {setup.target:.2f} | R:R {setup.rr:.2f} "
                f"conf {confidence:.2f} → {verdict}\n      why: {setup.reason}"
                f"\n      tags: {', '.join(setup.tags)}")
        if skip is not None:
            return

        sid = self.cap.save_setup(setup, confidence)
        if self.cfg.require_manual_approve:
            self.pending[inst] = dict(setup=setup, size=decision.size, meta=meta, sid=sid,
                                      confidence=confidence)
            await self._say(self._setup_card(setup, decision, confidence, sid,
                                             pending=True))
            return
        await self._enter(inst, setup, decision.size, sid, confidence, decision)

    async def _enter(self, inst, setup, size, sid, confidence, decision=None):
        order = Order(inst, setup.direction, size, OrderType.MARKET,
                      bracket_stop=setup.stop, bracket_target=setup.target, tag=f"ict:{sid}")
        placed = await self.broker.place(order)
        if placed.status in ("rejected",):
            await self._say(f"❌ order rejected for {inst} (setup {sid})")
            return
        self.open_setup[inst] = dict(sid=sid, setup=setup, ts_in=setup.ts, size=size,
                                     side=setup.direction, entry=placed.avg_fill or setup.entry)
        line = decision.reason if decision else f"size {size}"
        await self._say(f"✅ ENTER {inst} {setup.direction.name} x{size} @~{setup.entry:.2f} "
                        f"stop {setup.stop:.2f} tgt {setup.target:.2f} (R:R {setup.rr:.2f}, "
                        f"conf {confidence:.2f})\n   {setup.reason}\n   {line}")

    # ----------------------------------------------------------------- fills
    async def _on_fill(self, f: Fill):
        net = f.pnl - f.fees
        if f.pnl == 0.0 and f.fees == 0.0:
            return  # an opening fill — nothing to realize/label
        # realize into the risk engine
        alive = self.risk.on_closed_trade(net)
        self._closed_trades += 1
        os = self.open_setup.pop(f.instrument, None)
        if os:
            self.cap.save_trade(os["sid"], f.instrument, int(os["side"]), os["size"],
                                os["entry"], f.price, f.pnl, f.fees, os["ts_in"], f.ts)
        emoji = "🟩" if net > 0 else ("🟥" if net < 0 else "⬜")
        await self._say(f"{emoji} CLOSED {f.instrument} pnl ${net:+.2f} (gross ${f.pnl:+.2f} "
                        f"fees ${f.fees:.2f}). {self._risk_line()}")
        if not alive:
            self.halted = True
            await self.broker.flatten_all()
            await self._say("🛑 Account blown on that close — halted.")
        elif self.risk.eng.passed:
            self.paused = True
            await self._say("🎯 Combine target reached — auto-paused. Stop or reset.")
        await self._maybe_retrain()

    async def _maybe_retrain(self):
        if self._closed_trades % 25 != 0:
            return
        X, y = self.cap.label_training_set()
        if self.model.train(X, y):
            await self._say(f"🧠 Confidence model retrained on {len(y)} labelled trades "
                            f"(win rate {y.mean():.1%}).")

    # ----------------------------------------------------------------- manual approve
    async def approve(self, instrument: str | None = None):
        items = ([instrument] if instrument else list(self.pending.keys()))
        for inst in items:
            p = self.pending.pop(inst, None)
            if p:
                await self._enter(inst, p["setup"], p["size"], p["sid"], p["confidence"])

    # ----------------------------------------------------------------- helpers
    def _risk_line(self) -> str:
        s = self.risk.snapshot()
        return (f"💼 eq ${s['equity']:.0f} | floor ${s['floor']:.0f} | dayPnL ${s['day_pnl']:+.0f} "
                f"| target left ${s['target_remaining']:.0f}"
                f"{' | LOCKED' if s['locked_today'] else ''}"
                f"{' | BLOWN' if s['blown'] else ''}{' | PASSED' if s['passed'] else ''}")

    def _setup_card(self, setup, decision, confidence, sid, pending=False):
        head = "🟡 PENDING APPROVAL" if pending else "Setup"
        return (f"{head} [{sid}] {setup.instrument} {setup.archetype.upper()} "
                f"{setup.direction.name} x{decision.size}\n"
                f"   entry {setup.entry:.2f} stop {setup.stop:.2f} tgt {setup.target:.2f} "
                f"(R:R {setup.rr:.2f}, conf {confidence:.2f})\n"
                f"   why: {setup.reason}\n   tags: {', '.join(setup.tags)}"
                + (f"\n   /approve {setup.instrument} to take it" if pending else ""))

    async def _say(self, msg: str):
        try:
            await self.notify(msg)
        except Exception:
            print(msg)

    # ----------------------------------------------------------------- control surface
    async def pause(self):
        self.paused = True
        await self._say("⏸️ Paused — no new entries (open positions still managed).")

    async def resume(self):
        if self.halted:
            await self._say("Cannot resume: trading is HALTED (floor breach / blown).")
            return
        self.paused = False
        await self._say("▶️ Resumed.")

    async def flatten_all(self):
        await self.broker.flatten_all()
        await self._say("🧹 Flattened all positions.")

    def status(self) -> str:
        pos = [f"{i}:{p.size}@{p.avg_price:.2f}" for i, p in self.broker.positions.items() if not p.flat]
        st = self.cap.stats()
        return (f"mode={self.cfg.mode} paused={self.paused} halted={self.halted}\n"
                f"{self._risk_line()}\n"
                f"positions: {pos or 'flat'} | unrealized ${self._unrealized():+.2f}\n"
                f"captured: {st['bars']} bars, {st['setups']} setups, {st['trades']} trades "
                f"(win {st['win_rate']:.0%}) | model {self.model.mode()}")
