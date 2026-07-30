"""Signal engine: rolling per-market price stats -> entry signals + exit checks.

Core idea (Bollinger-style mean reversion with a momentum gate):
  baseline = EMA(yes_mid)                     # short-run "fair-ish" anchor
  z        = (yes_mid - baseline) / std       # how stretched we are
  spike    = |yes_mid - yes_mid[spike_window ago]|   # was the move FAST?

We only fade when the price is BOTH stretched (|z| >= entry_z) AND the stretch
came from a fast burst (spike >= spike_min_move). A slow drift to a new level is
usually real repricing, not noise — fading it is how mean-reversion bleeds out.

Everything downstream works in the HELD SIDE's price space (see models.py).
"""
from __future__ import annotations
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from data.models import Side
from strategies.mean_reversion.models import (Quote, Signal, SignalType,
                                              MRPosition, PosStatus)
from strategies.mean_reversion.fees import round_trip_fee_cents


class RollingState:
    """Per-market price history + EMA/std/z computations."""

    def __init__(self, ema_span: int, vol_window: int, spike_window: int):
        self.ema_span = ema_span
        self.vol_window = vol_window
        self.spike_window = spike_window
        self.mids: deque[float] = deque(maxlen=max(vol_window, spike_window) + 2)
        self.ema: Optional[float] = None
        self.n = 0

    def update(self, mid: float):
        self.mids.append(mid)
        self.n += 1
        k = 2.0 / (self.ema_span + 1.0)
        self.ema = mid if self.ema is None else (mid * k + self.ema * (1 - k))

    @property
    def std(self) -> float:
        if len(self.mids) < 3:
            return 0.0
        window = list(self.mids)[-self.vol_window:]
        return pstdev(window)

    def zscore(self, mid: float) -> float:
        s = self.std
        if s <= 1e-6 or self.ema is None:
            return 0.0
        return (mid - self.ema) / s

    def spike_move(self, mid: float) -> float:
        if len(self.mids) <= self.spike_window:
            return 0.0
        ref = list(self.mids)[-self.spike_window - 1]
        return mid - ref


class SignalEngine:
    def __init__(self, cfg: dict):
        s = cfg["signal"]
        self.ema_span = s["ema_span"]
        self.vol_window = s["vol_window"]
        self.warmup = s["warmup_samples"]
        self.entry_z = s["entry_z"]
        self.spike_window = s["spike_window"]
        self.spike_min_move = s["spike_min_move"]
        self.reversion_frac = s["reversion_frac"]
        self.price_floor = s["price_floor"]
        self.min_secs_to_close = s["min_secs_to_close"]

        e = cfg["exits"]
        self.stop_cents = e["stop_cents"]
        self.max_hold_seconds = e["max_hold_seconds"]
        self.tp_at_target = e["take_profit_at_target"]

        fv = cfg["fair_value"]
        self.fv_enabled = fv["enabled"]
        self.fv_mode = fv["mode"]                  # off | confirm | require
        self.fv_min_edge_cents = fv["min_model_edge_cents"]

        self.fee_model = cfg["fees"]["model"]
        self.slippage_cents = cfg["fees"]["slippage_cents"]
        self.min_edge_cents = cfg["risk"]["min_edge_cents"]

        self._states: dict[str, RollingState] = {}

    def state(self, market_id: str) -> RollingState:
        st = self._states.get(market_id)
        if st is None:
            st = RollingState(self.ema_span, self.vol_window, self.spike_window)
            self._states[market_id] = st
        return st

    # ─── Entry ──────────────────────────────────────────────────────
    def on_quote(self, quote: Quote, fair_yes_prob: Optional[float] = None,
                 secs_to_close: Optional[float] = None) -> Optional[Signal]:
        """Update rolling state with a new quote; return a Signal if one fires."""
        mid = quote.yes_mid
        if mid is None:
            return None
        st = self.state(quote.market_id)
        st.update(mid)

        if st.n < self.warmup:
            return None
        if not (self.price_floor <= mid <= 1 - self.price_floor):
            return None
        if secs_to_close is not None and secs_to_close < self.min_secs_to_close:
            return None

        z = st.zscore(mid)
        spike = st.spike_move(mid)
        baseline = st.ema

        # Need a stretched price AND a fast burst in the same direction.
        if z >= self.entry_z and spike >= self.spike_min_move:
            sig_type, side = SignalType.FADE_SPIKE, Side.NO     # overbought YES -> buy NO
        elif z <= -self.entry_z and spike <= -self.spike_min_move:
            sig_type, side = SignalType.BUY_DIP, Side.YES        # oversold YES -> buy YES
        else:
            return None

        # Translate to the held side's price space.
        entry = quote.side_ask(side)
        if entry is None or not (0.01 < entry < 0.99):
            return None
        base_side = baseline if side == Side.YES else 1.0 - baseline
        # Target = partway back to the baseline (we don't assume full reversion).
        target = entry + self.reversion_frac * (base_side - entry)
        if target <= entry:
            return None
        stop = entry - self.stop_cents / 100.0

        # Fair-value overlay: don't fade *justified* moves.
        if self.fv_enabled and self.fv_mode != "off" and fair_yes_prob is not None:
            fair_side = fair_yes_prob if side == Side.YES else 1.0 - fair_yes_prob
            model_edge_cents = (fair_side - entry) * 100.0  # >0 => held side underpriced vs model
            if self.fv_mode == "confirm" and model_edge_cents < 0:
                # Model says the side we'd buy is OVERpriced -> the move was real. Veto.
                return None
            if self.fv_mode == "require" and model_edge_cents < self.fv_min_edge_cents:
                return None

        # Fee floor gate: expected reversion must clear round-trip fees + slippage.
        gross_cents = (target - entry) * 100.0
        rt_fee = round_trip_fee_cents(entry, target, self.fee_model)
        net_edge = gross_cents - rt_fee - 2 * self.slippage_cents
        if net_edge < self.min_edge_cents:
            return None

        return Signal(
            market_id=quote.market_id, type=sig_type, side=side,
            entry_price=round(entry, 4), target_price=round(target, 4),
            stop_price=round(stop, 4), z=round(z, 2), spike_move=round(spike, 4),
            expected_edge_cents=round(net_edge, 2), fair_yes_prob=fair_yes_prob,
            reason=f"{sig_type.value} z={z:+.1f} spike={spike:+.3f} "
                   f"net_edge={net_edge:.1f}c",
        )

    # ─── Exit ───────────────────────────────────────────────────────
    def check_exit(self, pos: MRPosition, quote: Quote, now: datetime,
                   secs_to_close: Optional[float] = None) -> Optional[tuple[float, str]]:
        """Return (exit_price, reason) if the position should be closed now."""
        bid = quote.side_bid(pos.side)   # what we'd receive to exit
        if bid is None:
            return None
        held = (now - pos.opened_at).total_seconds()

        if self.tp_at_target and bid >= pos.target_price:
            return bid, "take_profit"
        if bid <= pos.stop_price:
            return bid, "stop_loss"
        if held >= self.max_hold_seconds:
            return bid, "time_stop"
        if secs_to_close is not None and secs_to_close < self.min_secs_to_close:
            return bid, "near_close"
        return None
