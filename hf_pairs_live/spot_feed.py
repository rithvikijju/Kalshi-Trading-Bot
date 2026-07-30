"""Dual BTC/ETH spot feed with rolling log-spread tracking."""
from __future__ import annotations
import math, threading, time, requests
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Tuple

from config import CFG


class DualSpotFeed:
    """Polls Coinbase BTC + ETH every poll_sec, maintains rolling log-spread
    window for z-score computation."""

    def __init__(self, poll_sec=None, lookback_min=None):
        self.poll_sec = poll_sec or CFG['spot_poll_sec']
        self.lookback_min = lookback_min or CFG['lookback_minutes']
        self.beta = CFG['beta']
        ticks_in_window = int(self.lookback_min * 60 / self.poll_sec)
        self._spread_history: deque = deque(maxlen=ticks_in_window)
        self._ts_history: deque = deque(maxlen=ticks_in_window)
        self._lock = threading.Lock()
        self._btc: Optional[float] = None
        self._eth: Optional[float] = None
        self._spread: Optional[float] = None
        self._ts: Optional[datetime] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._n_polls = 0
        self._n_errors = 0

    def _pull_one(self, pair: str) -> Optional[float]:
        try:
            r = requests.get(CFG['coinbase_url'].format(pair=pair), timeout=5)
            r.raise_for_status()
            return float(r.json()['price'])
        except Exception:
            self._n_errors += 1
            return None

    def _loop(self):
        while self._running:
            btc = self._pull_one(CFG['btc_pair'])
            eth = self._pull_one(CFG['eth_pair'])
            if btc and eth and btc > 0 and eth > 0:
                spread = math.log(btc) - self.beta * math.log(eth)
                now = datetime.now(timezone.utc)
                with self._lock:
                    self._btc = btc
                    self._eth = eth
                    self._spread = spread
                    self._ts = now
                    self._spread_history.append(spread)
                    self._ts_history.append(now)
                    self._n_polls += 1
            time.sleep(self.poll_sec)

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def snapshot(self) -> dict:
        with self._lock:
            hist = list(self._spread_history)
            return {
                'btc': self._btc, 'eth': self._eth,
                'spread': self._spread, 'ts': self._ts,
                'history': hist, 'n_polls': self._n_polls,
                'n_errors': self._n_errors,
            }

    def z_score(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Return (z, mean, std) of current spread vs rolling history."""
        with self._lock:
            hist = list(self._spread_history)
            cur = self._spread
        if cur is None or len(hist) < CFG['min_history_required']:
            return None, None, None
        n = len(hist)
        mean = sum(hist) / n
        var = sum((x - mean) ** 2 for x in hist) / max(1, n - 1)
        std = math.sqrt(var) if var > 0 else 0
        if std < 1e-8:
            return None, mean, std
        return (cur - mean) / std, mean, std
