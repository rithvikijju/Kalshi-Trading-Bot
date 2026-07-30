"""
Signal engine — fuses the ICT primitives into concrete, risk-defined Setups.

Two archetypes (the two things the user asked for):

  REVERSAL ("price reverts to a point"):
      a liquidity sweep (stop run) + rejection, confirmed by a CHoCH or displacement in
      the reversal direction, entered on the sweep bar with the stop beyond the swept wick
      and the target at the next opposing liquidity pool / session level. This is the
      mean-reversion-after-stop-grab trade.

  CONTINUATION ("enough momentum to signal a bull/bear switch"):
      an established trend that just printed a BOS, entered on a retrace into an
      unmitigated FVG or order block in the trend direction, stop beyond that zone,
      target the next draw on liquidity. This is the momentum trade.

Every Setup carries WHY it fired (tags + reason) and a numeric `features` dict the ML
sizing model consumes — so nothing is a black box and everything is backtestable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..broker.base import Bar, Side
from . import fvg as fvg_mod
from . import order_blocks as ob_mod
from . import liquidity as liq_mod
from . import sessions as sess
from .structure import atr, displacement, structure_state


@dataclass
class Setup:
    instrument: str
    ts: float
    direction: Side
    entry: float
    stop: float
    target: float
    archetype: str                  # "reversal" | "continuation"
    tags: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward(self) -> float:
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float:
        return self.reward / self.risk if self.risk > 0 else 0.0

    @property
    def stop_ticks_fn(self):
        return self.risk


class SignalEngine:
    def __init__(self, min_bars: int = 60, swing_k: int = 2, min_target_r: float = 0.0):
        self.min_bars = min_bars
        self.swing_k = swing_k
        # if >0, push the target out so reward is at least this multiple of risk (keeps the
        # structural pool target but enforces a floor — directly attacks small-target -EV).
        self.min_target_r = min_target_r

    def _apply_target_floor(self, entry, stop, target, d):
        if self.min_target_r <= 0:
            return target
        risk = abs(entry - stop)
        floor = entry + d * self.min_target_r * risk
        return max(target, floor) if d > 0 else min(target, floor)

    def _nearest_pool(self, pools, price, above: bool):
        cands = [p for p in pools if (p.price > price) == above]
        if not cands:
            return None
        return min(cands, key=lambda p: abs(p.price - price))

    def diagnose(self, instrument: str, bars: list[Bar]) -> dict:
        """A light read of current state for verbose logging (no setup decision)."""
        if len(bars) < self.min_bars:
            return {"status": f"warming up ({len(bars)}/{self.min_bars} bars)"}
        last = bars[-1]
        a = atr(bars, 14)
        st = structure_state(bars, self.swing_k)
        pools = liq_mod.liquidity_pools(bars, self.swing_k)
        sweep = liq_mod.detect_sweep(bars, pools, self.swing_k)
        fvgs = fvg_mod.active_fvgs(bars, min_size=0.10 * a) if a > 0 else []
        obs = ob_mod.active_order_blocks(bars)
        kz = sess.current_killzone(last.ts)
        return dict(close=last.c, trend=st.trend, bos=st.bos, choch=st.choch,
                    atr=round(a, 2), n_fvg=len(fvgs), n_ob=len(obs),
                    sweep=bool(sweep), kz=kz or "-", bars=len(bars))

    def generate(self, instrument: str, bars: list[Bar]) -> Setup | None:
        if len(bars) < self.min_bars:
            return None
        last = bars[-1]
        a = atr(bars, 14)
        if a <= 0:
            return None
        buf = 0.25 * a                                  # stop buffer beyond the zone/wick

        st = structure_state(bars, self.swing_k)
        disp = displacement(bars)
        pools = liq_mod.liquidity_pools(bars, self.swing_k)
        sweep = liq_mod.detect_sweep(bars, pools, self.swing_k)
        fvgs = fvg_mod.active_fvgs(bars, min_size=0.10 * a)
        obs = ob_mod.active_order_blocks(bars)
        sl = sess.session_levels(bars)
        kz = sess.current_killzone(last.ts)
        hour = sess.et_dt(last.ts).hour

        # shared base features
        base = dict(
            atr=a, body_atr=last.body / a, range_atr=last.range / a,
            trend_up=1.0 if st.trend == "up" else 0.0,
            trend_down=1.0 if st.trend == "down" else 0.0,
            bos=1.0 if st.bos else 0.0, choch=1.0 if st.choch else 0.0,
            disp_mag=disp.magnitude if disp else 0.0,
            disp_dir=float(disp.direction) if disp else 0.0,
            n_active_fvg=float(len(fvgs)), n_active_ob=float(len(obs)),
            hour_et=float(hour),
            kz_london=1.0 if kz == "london" else 0.0,
            kz_ny_am=1.0 if kz == "ny_am" else 0.0,
            kz_ny_pm=1.0 if kz == "ny_pm" else 0.0,
            in_killzone=1.0 if kz else 0.0,
        )

        # ---- REVERSAL: sweep + rejection (+ confirmation) ----
        if sweep is not None:
            d = sweep.direction
            direction = Side.BUY if d > 0 else Side.SELL
            confirm = (st.choch and ((d > 0) == (st.trend != "up"))) or \
                      (disp and disp.direction == d)
            entry = last.c
            if d > 0:        # swept sell-side lows, reverse up
                stop = min(last.l, sweep.level) - buf
                tgt_pool = self._nearest_pool(pools, entry, above=True)
                target = tgt_pool.price if tgt_pool else max(sl.midnight_open, sl.prev_day_high, entry + 2 * a)
            else:            # swept buy-side highs, reverse down
                stop = max(last.h, sweep.level) + buf
                tgt_pool = self._nearest_pool(pools, entry, above=False)
                target = tgt_pool.price if tgt_pool else min(sl.midnight_open or entry, sl.prev_day_low or entry, entry - 2 * a)
            target = self._apply_target_floor(entry, stop, target, d)
            tags = ["liquidity_sweep", f"sweep_rej_{sweep.rejection / a:.2f}atr"]
            if confirm:
                tags.append("choch_or_displacement_confirm")
            in_fvg = any(g.direction == d and g.contains(entry) for g in fvgs)
            in_ob = any(o.direction == d and o.contains(entry) for o in obs)
            if in_fvg:
                tags.append("entry_in_fvg")
            if in_ob:
                tags.append("entry_in_ob")
            feats = {**base, "archetype_reversal": 1.0, "archetype_continuation": 0.0,
                     "sweep_rej_atr": sweep.rejection / a, "confirm": 1.0 if confirm else 0.0,
                     "entry_in_fvg": 1.0 if in_fvg else 0.0, "entry_in_ob": 1.0 if in_ob else 0.0,
                     "dist_stop_atr": abs(entry - stop) / a,
                     "dist_target_atr": abs(target - entry) / a}
            s = Setup(instrument, last.ts, direction, entry, stop, target, "reversal",
                      tags, feats, reason=f"swept {'sell' if d>0 else 'buy'}-side liquidity "
                      f"@ {sweep.level:.2f} and rejected; "
                      f"{'CHoCH/displacement confirms' if confirm else 'unconfirmed'}")
            feats["rr"] = s.rr
            return s if s.rr > 0 else None

        # ---- CONTINUATION: trend + BOS, retrace into FVG/OB ----
        if st.trend in ("up", "down") and (st.bos or True):
            d = 1 if st.trend == "up" else -1
            direction = Side.BUY if d > 0 else Side.SELL
            # require price to currently sit inside an aligned, unmitigated FVG or OB (the retrace)
            zone = None
            zone_kind = None
            for g in fvgs:
                if g.direction == d and g.contains(last.c):
                    zone, zone_kind = g, "fvg"
                    break
            if zone is None:
                for o in obs:
                    if o.direction == d and o.contains(last.c):
                        zone, zone_kind = o, "ob"
                        break
            if zone is None:
                return None
            entry = last.c
            if d > 0:
                stop = zone.bottom - buf
                tgt_pool = self._nearest_pool(pools, entry, above=True)
                target = tgt_pool.price if tgt_pool else (sl.prev_day_high or entry + 2 * a)
            else:
                stop = zone.top + buf
                tgt_pool = self._nearest_pool(pools, entry, above=False)
                target = tgt_pool.price if tgt_pool else (sl.prev_day_low or entry - 2 * a)
            target = self._apply_target_floor(entry, stop, target, d)
            tags = [f"trend_{st.trend}", "bos" if st.bos else "trend_pullback",
                    f"retrace_into_{zone_kind}"]
            feats = {**base, "archetype_reversal": 0.0, "archetype_continuation": 1.0,
                     "sweep_rej_atr": 0.0, "confirm": 1.0 if st.bos else 0.0,
                     "entry_in_fvg": 1.0 if zone_kind == "fvg" else 0.0,
                     "entry_in_ob": 1.0 if zone_kind == "ob" else 0.0,
                     "dist_stop_atr": abs(entry - stop) / a,
                     "dist_target_atr": abs(target - entry) / a}
            s = Setup(instrument, last.ts, direction, entry, stop, target, "continuation",
                      tags, feats, reason=f"{st.trend} trend"
                      f"{' + BOS' if st.bos else ''}, retrace into {zone_kind} — continuation")
            feats["rr"] = s.rr
            return s if s.rr > 0 else None

        return None
