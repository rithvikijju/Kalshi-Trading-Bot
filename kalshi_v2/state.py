"""Shared mutable state for the bot.

This module is the single source of truth for all in-memory state that
crosses module boundaries (locks, BTC spot, Kalshi orderbooks, tracked
event, WebSocket session, bot lifecycle).

It MUST stay tiny and edit-free. Putting state here — instead of
`data.py` — prevents an autoreload of `data.py` from rebinding the
state dicts to fresh objects while other modules still hold the old
references. As long as nobody edits `state.py`, every consumer sees
the same dict identity through the lifetime of the kernel.

If you find yourself wanting to extend this module: don't. Add the
new state to `state.py` only. Anything else (logic, threads, IO) goes
in `data.py` or wherever it's used.
"""
from __future__ import annotations
import threading
from typing import Dict


LOCK = threading.Lock()

# Coinbase BTC spot (updated by spot_poller in data.py)
SPOT: Dict = {"price": None, "ts": None, "history": []}

# Kalshi orderbooks per ticker (updated by ws_listener / REST fallback)
BOOKS: Dict[str, Dict] = {}

# Currently tracked event (updated by event_tracker)
TRACKED: Dict = {"event": None, "close_time": None, "refreshed_at": None}

# WebSocket connection state
WS_STATE: Dict = {
    "connected":           False,
    "subscribed_event":    None,
    "reconnect_count":     0,
    "last_msg_ts":         None,
    "msg_count":           0,
    "needs_resubscribe":   False,
    "mode":                "websocket",   # 'websocket' or 'rest_fallback'
}

# Bot lifecycle state
BOT_STATE: Dict = {
    "running":  False,
    "threads":  [],
    "log":      [],
    "iter":     0,
    "trades":   0,
}
