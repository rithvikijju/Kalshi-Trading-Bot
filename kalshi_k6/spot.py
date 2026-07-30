"""Spot feed + EWMA realized vol estimator.

Background thread polls Coinbase BTC every spot_poll_sec, maintains a rolling
history and an EWMA σ estimate. Threadsafe access via the lock.
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from config import CFG, _coinbase_spot


class SpotFeed:
    """Polls Coinbase BTC spot, exposes price + history + EWMA volatility."""

    def __init__(self, poll_sec: float = None):
        self.poll_sec = poll_sec or CFG['spot_poll_sec']
        self._lock = threading.Lock()
        self._price: Optional[float] = None
        self._ts: Optional[datetime] = None
        # Keep last 30min of ticks (~900 at 2s poll) for spot_move lookups
        self._history: deque = deque(maxlen=int(1800 / self.poll_sec))
        # EWMA σ² state — RiskMetrics λ=0.94
        self._sigma_sq: Optional[float] = None
        self._last_price_for_ewma: Optional[float] = None
        self._lambda = 0.94
        self._n_updates = 0
        # Realized variance of last 15 min log-returns (for K14 regime filter)
        self._returns_15min: deque = deque(maxlen=int(900 / self.poll_sec))
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ──────────────────────────────────────────────────────────────
    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                p = _coinbase_spot()
                if p and p > 0:
                    self._update(p)
            except Exception:
                pass
            time.sleep(self.poll_sec)

    def _update(self, new_price: float):
        now = datetime.now(timezone.utc)
        with self._lock:
            # EWMA update
            if self._last_price_for_ewma is not None:
                r = math.log(new_price / self._last_price_for_ewma)
                self._returns_15min.append(r)
                if self._sigma_sq is None:
                    self._sigma_sq = r * r
                else:
                    l = self._lambda
                    self._sigma_sq = l * self._sigma_sq + (1 - l) * r * r
                self._n_updates += 1
            self._last_price_for_ewma = new_price
            self._price = new_price
            self._ts = now
            self._history.append((now, new_price))

    # ──────────────────────────────────────────────────────────────
    @property
    def price(self) -> Optional[float]:
        with self._lock:
            return self._price

    def history(self):
        with self._lock:
            return list(self._history)

    def move_over(self, secs: float) -> Optional[float]:
        """Return |spot(now) − spot(now − secs)|. None if not enough history."""
        from datetime import timedelta
        target = datetime.now(timezone.utc) - timedelta(seconds=secs)
        with self._lock:
            now_p = self._price
            hist = list(self._history)
        if not hist or now_p is None:
            return None
        for ts, p in reversed(hist):
            if ts <= target:
                return abs(now_p - p)
        return None

    def realized_sigma_annual(self) -> Optional[float]:
        """Annualized σ from EWMA. Returns None if insufficient data."""
        with self._lock:
            if self._sigma_sq is None or self._n_updates < 30:
                return None
            sigma_per_tick = math.sqrt(self._sigma_sq)
            ticks_per_year = (365.25 * 24 * 3600) / self.poll_sec
            return sigma_per_tick * math.sqrt(ticks_per_year)

    def rv_15min_annual(self) -> Optional[float]:
        """Annualized σ from last ~15 min of log-returns (the K14 regime input)."""
        with self._lock:
            if len(self._returns_15min) < 30:
                return None
            returns = list(self._returns_15min)
        # Sample stdev × sqrt(annualization factor)
        n = len(returns)
        mean = sum(returns) / n
        var = sum((r - mean) ** 2 for r in returns) / max(1, n - 1)
        sigma_per_tick = math.sqrt(var)
        ticks_per_year = (365.25 * 24 * 3600) / self.poll_sec
        return sigma_per_tick * math.sqrt(ticks_per_year)
