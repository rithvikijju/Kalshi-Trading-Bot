"""Kalshi market feed — polls REST API for the current hourly event's strikes.

Background thread:
  - Every event_refresh_sec: find the next hourly event closing within 60 min
  - Every market_poll_sec: fetch all that event's market orderbooks
  - Threadsafe access via .snapshot()
"""
from __future__ import annotations
import re, threading, time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from config import CFG, KalshiClient


def _parse_close_time(close_time_str: str) -> Optional[datetime]:
    if not close_time_str: return None
    # Kalshi returns ISO with Z or +offset
    s = close_time_str.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


class MarketFeed:
    def __init__(self, client: Optional[KalshiClient] = None,
                 market_poll_sec: float = None,
                 event_refresh_sec: float = 120.0):
        self.client = client or KalshiClient()
        self.market_poll_sec = market_poll_sec or CFG['market_poll_sec']
        self.event_refresh_sec = event_refresh_sec
        self._lock = threading.Lock()
        self._tracked_event: Optional[str] = None
        self._tracked_close_time: Optional[datetime] = None
        self._markets: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_ts: Optional[datetime] = None
        self._last_event_refresh_ts: Optional[datetime] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._error_count = 0

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    # ──────────────────────────────────────────────────────────────
    def _find_tracked_event(self) -> Optional[Dict[str, Any]]:
        """Find the next hourly BTC event closing soonest (within next 60 min)."""
        try:
            data = self.client.get_events(series_ticker=CFG['series_ticker'],
                                            status='open', limit=20)
        except Exception:
            return None
        events = data.get('events') if isinstance(data, dict) else []
        if not events: return None
        # Kalshi events expose `strike_date` (= close time). Pick the soonest future.
        now = datetime.now(timezone.utc)
        best = None
        best_secs = None
        for e in events:
            ct = _parse_close_time(e.get('strike_date'))
            if ct is None: continue
            secs = (ct - now).total_seconds()
            if secs <= 0 or secs > 3600: continue
            if best_secs is None or secs < best_secs:
                best = e
                best_secs = secs
                best['_close_time'] = ct
        return best

    def _refresh_event(self):
        evt = self._find_tracked_event()
        if evt is None: return
        ct = evt.get('_close_time') or _parse_close_time(evt.get('strike_date'))
        with self._lock:
            self._tracked_event = evt['event_ticker']
            self._tracked_close_time = ct
            self._last_event_refresh_ts = datetime.now(timezone.utc)

    def _refresh_markets(self):
        with self._lock:
            evt = self._tracked_event
            close_time = self._tracked_close_time
        if not evt:
            return
        try:
            data = self.client.get_markets(event_ticker=evt, status='open', limit=200)
        except Exception:
            self._error_count += 1
            return
        out = {}
        for m in data.get('markets', []):
            ticker = m.get('ticker')
            if not ticker: continue
            strike = m.get('floor_strike')
            if strike is None:
                mm = re.search(r'-T(\d+\.?\d*)$', ticker)
                if mm:
                    try: strike = float(mm.group(1))
                    except: pass
            if strike is None: continue
            mc = _parse_close_time(m.get('close_time')) or close_time

            def fparse(s):
                if s is None: return None
                try: return float(s)
                except: return None

            out[ticker] = {
                'ticker': ticker,
                'event_ticker': evt,
                'strike': float(strike),
                'close_time': mc,
                'status': m.get('status', 'active'),
                'yes_bid': fparse(m.get('yes_bid_dollars')),
                'yes_ask': fparse(m.get('yes_ask_dollars')),
                'no_bid':  fparse(m.get('no_bid_dollars')),
                'no_ask':  fparse(m.get('no_ask_dollars')),
                # The markets endpoint does NOT include book depth qty; we use a
                # conservative fallback of 5, and may upgrade to /orderbook later.
                'yes_ask_qty': 5,
                'no_ask_qty':  5,
                'yes_bid_qty': 5,
                'no_bid_qty':  5,
                # Useful diagnostics:
                'open_interest': fparse(m.get('open_interest_fp')),
                'volume_24h':    fparse(m.get('volume_24h_fp')),
                'last_price':    fparse(m.get('last_price_dollars')),
            }
        with self._lock:
            self._markets = out
            self._last_refresh_ts = datetime.now(timezone.utc)

    def _loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if (self._last_event_refresh_ts is None or
                    (now - self._last_event_refresh_ts).total_seconds() > self.event_refresh_sec):
                    self._refresh_event()
                self._refresh_markets()
            except Exception:
                self._error_count += 1
            time.sleep(self.market_poll_sec)

    # ──────────────────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'event_ticker': self._tracked_event,
                'close_time': self._tracked_close_time,
                'last_refresh_ts': self._last_refresh_ts,
                'markets': dict(self._markets),
                'error_count': self._error_count,
            }
