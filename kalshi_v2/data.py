"""Data layer.

REST-side: BTC historical minute candles (Coinbase) + Kalshi market/event REST
fallback. All cached.

WebSocket-side: Async websockets connection to Kalshi's ws/v2 endpoint with
RSA-signed handshake. Subscribes to ticker / orderbook_delta / fill channels.
Falls back to REST polling on auth failure.
"""
from __future__ import annotations
import asyncio, inspect, json, threading, time, requests
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

import numpy as np
import pandas as pd
import websockets

from .config import CFG
from .client import KalshiClient, parse_market_fields
from .state import LOCK, SPOT, BOOKS, TRACKED, WS_STATE, BOT_STATE


# Older websockets (≤10.x) use `extra_headers`; v13+ use `additional_headers`.
# Detect once at import so `_ws_loop` can pass the right kwarg.
def _ws_header_param() -> str:
    try:
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            return "additional_headers"
        if "extra_headers" in sig.parameters:
            return "extra_headers"
    except (ValueError, TypeError):
        pass
    # Default to the modern name; the runtime error will surface if it's wrong.
    return "additional_headers"


_WS_HEADER_PARAM = _ws_header_param()


# ════════════════════════════════════════════════════════════════════════
#  BTC historical data (Coinbase, REST)
# ════════════════════════════════════════════════════════════════════════
def fetch_coinbase_candles(product_id="BTC-USD", granularity=60,
                            start=None, end=None) -> pd.DataFrame:
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    params = {"granularity": granularity}
    if start: params["start"] = start.isoformat()
    if end:   params["end"]   = end.isoformat()
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    df = pd.DataFrame(r.json(),
                      columns=["time", "low", "high", "open", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.sort_values("time").reset_index(drop=True)


def fetch_historical_minutes(days_back=90, product_id="BTC-USD") -> pd.DataFrame:
    """Paginated minute-bar fetch. Coinbase caps at 300 bars/request."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    chunks = []
    cursor = start
    chunk_size = timedelta(minutes=300)
    while cursor < end:
        chunk_end = min(cursor + chunk_size, end)
        try:
            chunk = fetch_coinbase_candles(product_id, 60, cursor, chunk_end)
            chunks.append(chunk)
            cursor = chunk_end
            time.sleep(0.3)
        except Exception:
            time.sleep(2)
            cursor = chunk_end
    if not chunks:
        return pd.DataFrame()
    df = pd.concat(chunks, ignore_index=True)
    return df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)


def add_rv_features(btc_1m: pd.DataFrame) -> pd.DataFrame:
    """Add log_ret + rolling realized vol columns."""
    btc_1m = btc_1m.copy()
    btc_1m["log_ret"] = np.log(btc_1m["close"] / btc_1m["close"].shift(1))
    af = 60 * 24 * 365
    for w in (5, 15, 60, 240, 1440):
        btc_1m[f"rv_{w}m"] = btc_1m["log_ret"].rolling(w).std() * np.sqrt(af)
    return btc_1m


# ════════════════════════════════════════════════════════════════════════
#  Live data state — single canonical home is `state.py`. Re-exported
#  here for backwards compatibility with consumers that still do
#  `from .data import BOT_STATE`. New code should import from .state.
# ════════════════════════════════════════════════════════════════════════


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    BOT_STATE["log"].append(f"[{ts}] {msg}")
    if len(BOT_STATE["log"]) > 500:
        BOT_STATE["log"] = BOT_STATE["log"][-200:]


def _sleep(secs: float):
    end = time.time() + secs
    while time.time() < end and BOT_STATE["running"]:
        time.sleep(0.2)


# ════════════════════════════════════════════════════════════════════════
#  Coinbase spot poller (REST, every 2 s)
# ════════════════════════════════════════════════════════════════════════
def _coinbase_spot() -> float:
    try:
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        r.raise_for_status()
        return float(r.json()["data"]["amount"])
    except Exception:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids":"bitcoin","vs_currencies":"usd"}, timeout=5)
        r.raise_for_status()
        return float(r.json()["bitcoin"]["usd"])


def spot_poller():
    while BOT_STATE["running"]:
        try:
            price = _coinbase_spot()
            now = datetime.now(timezone.utc)
            with LOCK:
                SPOT["price"] = price
                SPOT["ts"]    = now
                SPOT["history"].append((now, price))
                cutoff = now - timedelta(minutes=70)
                SPOT["history"] = [(t,p) for t,p in SPOT["history"] if t > cutoff]
        except Exception as e:
            _log(f"spot err: {e}")
        _sleep(CFG["spot_poll_sec"])


# ════════════════════════════════════════════════════════════════════════
#  Kalshi event tracker (REST, every 60 s)
# ════════════════════════════════════════════════════════════════════════
def _ticker_close_time(event_ticker: str) -> Optional[datetime]:
    """Parse close datetime from ticker like KXBTCD-26MAY0617 (May 6, 17:00 UTC)."""
    import re
    m = re.match(r"^KXBTCD?-(\d{2})([A-Z]{3})(\d{2})(\d{2})", event_ticker or "")
    if not m: return None
    yy, mon, dd, hh = m.groups()
    months = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
              "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
    month = months.get(mon)
    if not month: return None
    return datetime(2000+int(yy), month, int(dd), int(hh), 0, 0, tzinfo=timezone.utc)


def event_tracker(kalshi_md: KalshiClient):
    """Find the nearest open BTC event whose TTL is in the configured window."""
    from dateutil import parser as dtparser
    while BOT_STATE["running"]:
        try:
            now = datetime.now(timezone.utc)
            best_event, best_close = None, None
            for series in CFG["event_series"]:
                try:
                    resp = kalshi_md.get_events(series_ticker=series,
                                                  status="open", limit=200)
                except Exception as e:
                    _log(f"tracker {series}: {e}")
                    continue
                for ev in resp.get("events", []):
                    et = ev.get("event_ticker", "")
                    # First try ticker-encoded close time (cheap, no extra API call)
                    ct = _ticker_close_time(et)
                    if ct is None:
                        # Fallback: fetch markets to read close_time
                        try:
                            mkts = kalshi_md.get_markets(event_ticker=et,
                                                          limit=5).get("markets", [])
                            if mkts:
                                ct_str = mkts[0].get("close_time")
                                if ct_str:
                                    ct = dtparser.isoparse(ct_str)
                        except Exception:
                            continue
                    if ct is None: continue
                    ttl_min = (ct - now).total_seconds() / 60
                    if not (CFG["scan_min_ttl_min"] < ttl_min < CFG["scan_max_ttl_hours"]*60):
                        continue
                    if best_close is None or ct < best_close:
                        best_event, best_close = et, ct

            with LOCK:
                old = TRACKED.get("event")
                if best_event and best_event != old:
                    _log(f"tracking: {best_event} closes {best_close}")
                    BOOKS.clear()
                    WS_STATE["needs_resubscribe"] = True
                    _seed_books_rest(kalshi_md, best_event)
                TRACKED["event"]        = best_event
                TRACKED["close_time"]   = best_close
                TRACKED["refreshed_at"] = now
        except Exception as e:
            _log(f"tracker err: {e}")
        _sleep(60)


def _seed_books_rest(kalshi_md: KalshiClient, event_ticker: str):
    """Initial population of BOOKS via REST (cheap, one call)."""
    try:
        mkts = kalshi_md.get_markets(event_ticker=event_ticker, limit=200).get("markets", [])
        now = datetime.now(timezone.utc)
        with LOCK:
            for m in mkts:
                tk = m.get("ticker")
                if not tk: continue
                pf = parse_market_fields(m)
                BOOKS[tk] = {
                    "yes_bid":    pf["yes_bid"],
                    "yes_ask":    pf["yes_ask"],
                    "no_ask":     1.0 - pf["yes_bid"] if pf["yes_bid"] is not None else None,
                    "floor":      pf["floor"],
                    "cap":        pf["cap"],
                    "close_time": pf["close_time"],
                    "status":     (pf["status"] or "").lower(),
                    "ts":         now,
                    "volume":     pf["volume_total"],
                    "last_price": pf["last_price"],
                }
        _log(f"REST seed: {len(mkts)} markets for {event_ticker}")
    except Exception as e:
        _log(f"REST seed err: {e}")


def _get_event_tickers(kalshi_md: KalshiClient, event_ticker: str) -> List[str]:
    try:
        mkts = kalshi_md.get_markets(event_ticker=event_ticker, limit=200).get("markets", [])
        return [m["ticker"] for m in mkts if m.get("ticker")]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════════════
#  Kalshi WebSocket listener (async)
# ════════════════════════════════════════════════════════════════════════
def _parse_ticker_msg(msg_data: dict):
    """Parse a Kalshi ticker WS message into BOOKS."""
    tk = msg_data.get("market_ticker")
    if not tk: return

    def _d(key):
        v = msg_data.get(key)
        if v is None or v == "": return None
        try: return float(v)
        except (ValueError, TypeError): return None

    ya = _d("yes_ask_dollars")
    yb = _d("yes_bid_dollars")
    na = (1.0 - yb) if yb is not None else None
    now = datetime.now(timezone.utc)

    with LOCK:
        existing = BOOKS.get(tk, {})
        BOOKS[tk] = {
            **existing,
            "yes_bid": yb if yb is not None else existing.get("yes_bid"),
            "yes_ask": ya if ya is not None else existing.get("yes_ask"),
            "no_ask":  na if na is not None else existing.get("no_ask"),
            "ts":      now,
            "volume":  _d("volume_fp") or existing.get("volume"),
        }
        WS_STATE["last_msg_ts"] = now
        WS_STATE["msg_count"]  += 1


def _parse_orderbook_msg(msg_data: dict):
    """Parse a Kalshi orderbook_delta WS message — store top-of-book."""
    tk = msg_data.get("market_ticker")
    if not tk: return
    # orderbook_delta carries level updates; we extract top-of-book
    yes = msg_data.get("yes", []) or []
    no = msg_data.get("no", []) or []
    yes_bid = max((float(p)/100 for p, *_ in yes), default=None) if yes else None
    no_bid  = max((float(p)/100 for p, *_ in no), default=None) if no else None
    yes_ask = (1.0 - no_bid) if no_bid is not None else None
    if yes_bid is None and yes_ask is None: return
    now = datetime.now(timezone.utc)
    with LOCK:
        existing = BOOKS.get(tk, {})
        BOOKS[tk] = {
            **existing,
            "yes_bid": yes_bid if yes_bid is not None else existing.get("yes_bid"),
            "yes_ask": yes_ask if yes_ask is not None else existing.get("yes_ask"),
            "ts":      now,
        }
        WS_STATE["last_msg_ts"] = now
        WS_STATE["msg_count"]  += 1


async def _ws_loop(kalshi_md: KalshiClient, kalshi_live: Optional[KalshiClient]):
    """Main async WebSocket loop. Reconnects with exponential backoff.
    Falls back to REST if auth fails."""
    while BOT_STATE["running"]:
        if kalshi_live is None or not kalshi_live.private_key or not kalshi_live.key_id:
            _log("WS: no auth client → REST fallback")
            WS_STATE["mode"] = "rest_fallback"
            return

        auth_headers = kalshi_live.ws_auth_headers()
        if not auth_headers:
            _log("WS: no auth headers → REST fallback")
            WS_STATE["mode"] = "rest_fallback"
            return

        try:
            async with websockets.connect(
                    CFG["ws_url"], **{_WS_HEADER_PARAM: auth_headers}) as ws:
                WS_STATE["connected"]       = True
                WS_STATE["reconnect_count"] = 0
                WS_STATE["mode"]            = "websocket"
                _log("WS connected")

                with LOCK:
                    event = TRACKED.get("event")
                if event:
                    _seed_books_rest(kalshi_md, event)
                    tickers = _get_event_tickers(kalshi_md, event)
                    if tickers:
                        for ch in CFG["ws_subscribe_channels"]:
                            await ws.send(json.dumps({
                                "id": int(time.time() * 1000) % 1_000_000,
                                "cmd": "subscribe",
                                "params": {"channels": [ch], "market_tickers": tickers},
                            }))
                        WS_STATE["subscribed_event"] = event
                        _log(f"WS subscribed: {len(tickers)} tickers × {len(CFG['ws_subscribe_channels'])} channels")

                # Message loop
                while BOT_STATE["running"]:
                    if WS_STATE["needs_resubscribe"]:
                        WS_STATE["needs_resubscribe"] = False
                        with LOCK:
                            new_event = TRACKED.get("event")
                        if new_event and new_event != WS_STATE.get("subscribed_event"):
                            _seed_books_rest(kalshi_md, new_event)
                            tickers = _get_event_tickers(kalshi_md, new_event)
                            if tickers:
                                for ch in CFG["ws_subscribe_channels"]:
                                    await ws.send(json.dumps({
                                        "id": int(time.time() * 1000) % 1_000_000,
                                        "cmd": "subscribe",
                                        "params": {"channels": [ch],
                                                    "market_tickers": tickers},
                                    }))
                                WS_STATE["subscribed_event"] = new_event
                                _log(f"WS resubscribed: {len(tickers)} tickers")

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue
                    if not raw: continue

                    try: data = json.loads(raw)
                    except json.JSONDecodeError: continue

                    msg_type = data.get("type", "")
                    if msg_type == "ticker":
                        _parse_ticker_msg(data.get("msg", {}))
                    elif msg_type == "orderbook_delta":
                        _parse_orderbook_msg(data.get("msg", {}))
                    elif msg_type == "fill":
                        _log(f"WS fill: {data.get('msg', {})}")
                    elif msg_type == "subscribed":
                        sid = data.get("msg", {}).get("sid")
                        _log(f"WS sub confirmed sid={sid}")
                    elif msg_type == "error":
                        _log(f"WS error: {data.get('msg', {})}")

        except websockets.exceptions.InvalidStatusCode as e:
            _log(f"WS rejected: HTTP {e.status_code}")
            if e.status_code in (401, 403):
                _log("WS auth failed → REST fallback")
                WS_STATE["mode"] = "rest_fallback"
                WS_STATE["connected"] = False
                return
        except Exception as e:
            _log(f"WS error: {e}")

        WS_STATE["connected"] = False
        WS_STATE["subscribed_event"] = None
        if not BOT_STATE["running"]: break

        WS_STATE["reconnect_count"] += 1
        delay = min(CFG["ws_reconnect_base_sec"] * (2 ** WS_STATE["reconnect_count"]),
                     CFG["ws_reconnect_max_sec"])
        _log(f"WS reconnecting in {delay:.0f}s")
        await asyncio.sleep(delay)


def ws_listener(kalshi_md: KalshiClient, kalshi_live: Optional[KalshiClient]):
    """Thread entry point: drive the async WS loop, fall back to REST polling."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_ws_loop(kalshi_md, kalshi_live))
    except Exception as e:
        _log(f"WS loop crashed: {e}")
    finally:
        loop.close()
    if WS_STATE["mode"] == "rest_fallback":
        _rest_poller(kalshi_md)


def _rest_poller(kalshi_md: KalshiClient):
    """Fallback: poll Kalshi REST API for orderbooks every N seconds."""
    _log(f"REST poller active ({CFG['rest_book_interval_sec']}s)")
    while BOT_STATE["running"]:
        with LOCK:
            event = TRACKED.get("event")
        if not event:
            _sleep(2); continue
        _seed_books_rest(kalshi_md, event)
        _sleep(CFG["rest_book_interval_sec"])


# ════════════════════════════════════════════════════════════════════════
#  Causal sigma estimator from spot history (for live decisions)
# ════════════════════════════════════════════════════════════════════════
def causal_sigma_from_spot() -> Optional[float]:
    """Annualized vol from the recent Coinbase spot history."""
    with LOCK:
        hist = list(SPOT["history"])
    if len(hist) < 15: return None
    prices = np.array([p for _, p in hist], dtype=float)
    lr = np.diff(np.log(prices))
    if len(lr) < 5: return None
    s = float(np.std(lr) * np.sqrt(60 * 24 * 365))
    if not np.isfinite(s) or s <= 0: return None
    return s
