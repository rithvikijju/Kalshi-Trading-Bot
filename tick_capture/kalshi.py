"""Kalshi WS listener for BTC short-horizon markets.

Connects to wss://api.elections.kalshi.com/trade-api/ws/v2 with RSA-PSS
signed handshake (KalshiClient.ws_auth_headers). Subscribes to
orderbook_delta + ticker + trade for the discovered market tickers.
Re-subscribes whenever the discovery loop reports a new universe.

Every inbound message is normalized into a row and pushed to ParquetWriter.
"""
from __future__ import annotations
import asyncio, inspect, json, ssl, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import certifi
import websockets

_SSL = ssl.create_default_context(cafile=certifi.where())

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kalshi_v2.client import KalshiClient

from tick_capture.writer import ParquetWriter, now_us, to_us, safe_dumps


WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
# Fallback used by kalshi_v2/config.py; some prod accounts route here instead.
WS_URL_ALT = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
CHANNELS = ("orderbook_delta", "ticker", "trade")


def _header_param() -> str:
    try:
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            return "additional_headers"
        if "extra_headers" in sig.parameters:
            return "extra_headers"
    except (ValueError, TypeError):
        pass
    return "additional_headers"


_HEADER_PARAM = _header_param()


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_msg(data: dict) -> List[dict]:
    """Turn one Kalshi WS message into 0+ writer rows."""
    msg_type = data.get("type", "")
    msg = data.get("msg") or {}
    rows: List[dict] = []
    recv = now_us()

    if msg_type == "orderbook_snapshot":
        tk = msg.get("market_ticker", "")
        ts = to_us(msg.get("ts")) or recv
        # Top of book — yes_ask side derives from inverse no_bid
        yes_levels = msg.get("yes", []) or []
        no_levels  = msg.get("no",  []) or []
        yes_bid = max((float(p)/100 for p, *_ in yes_levels), default=None) if yes_levels else None
        no_bid  = max((float(p)/100 for p, *_ in no_levels),  default=None) if no_levels  else None
        rows.append({
            "ts_us": ts, "recv_us": recv,
            "venue": "kalshi", "msg_type": "orderbook_snapshot",
            "market_id": tk, "asset_id": "", "side": "",
            "price": yes_bid, "size": float(len(yes_levels)+len(no_levels)),
            "payload": safe_dumps(msg),
        })
        if no_bid is not None:
            rows.append({
                "ts_us": ts, "recv_us": recv,
                "venue": "kalshi", "msg_type": "orderbook_snapshot",
                "market_id": tk, "asset_id": "", "side": "no",
                "price": no_bid, "size": None, "payload": "",
            })
        return rows

    if msg_type == "orderbook_delta":
        tk = msg.get("market_ticker", "")
        ts = to_us(msg.get("ts")) or recv
        # delta carries: {price, delta, side} on Kalshi v2
        price = _f(msg.get("price"))
        if price is not None:
            price = price / 100.0  # cents → dollars
        delta = _f(msg.get("delta"))
        side = (msg.get("side") or "").lower()
        rows.append({
            "ts_us": ts, "recv_us": recv,
            "venue": "kalshi", "msg_type": "orderbook_delta",
            "market_id": tk, "asset_id": "", "side": side,
            "price": price, "size": delta, "payload": safe_dumps(msg),
        })
        return rows

    if msg_type == "ticker":
        tk = msg.get("market_ticker", "")
        ts = to_us(msg.get("ts")) or recv
        yb = _f(msg.get("yes_bid_dollars"))
        ya = _f(msg.get("yes_ask_dollars"))
        rows.append({
            "ts_us": ts, "recv_us": recv,
            "venue": "kalshi", "msg_type": "ticker",
            "market_id": tk, "asset_id": "", "side": "yes",
            "price": yb, "size": ya, "payload": safe_dumps(msg),
        })
        return rows

    if msg_type == "trade":
        tk = msg.get("market_ticker", "")
        ts = to_us(msg.get("ts") or msg.get("created_time")) or recv
        yp = _f(msg.get("yes_price"))
        if yp is not None: yp = yp / 100.0
        cnt = _f(msg.get("count"))
        side = (msg.get("taker_side") or "").lower()
        rows.append({
            "ts_us": ts, "recv_us": recv,
            "venue": "kalshi", "msg_type": "trade",
            "market_id": tk, "asset_id": "", "side": side,
            "price": yp, "size": cnt, "payload": safe_dumps(msg),
        })
        return rows

    # Unknown / system messages — store as raw for later forensics.
    rows.append({
        "ts_us": recv, "recv_us": recv,
        "venue": "kalshi", "msg_type": msg_type or "unknown",
        "market_id": "", "asset_id": "", "side": "",
        "price": None, "size": None, "payload": safe_dumps(data),
    })
    return rows


class KalshiListener:
    def __init__(self, writer: ParquetWriter):
        self.writer = writer
        self.client = KalshiClient(env="prod")
        self._subscribed: Set[str] = set()
        self._ws = None
        self._next_cmd_id = 1

    async def update_universe(self, tickers: List[str]) -> None:
        """Subscribe to any new tickers we haven't seen yet.

        Kalshi v2 WS supports `update_subscription` with action=add_markets,
        which is cheaper than tearing down the sub. If we have no active
        subscription yet, we issue a fresh subscribe.
        """
        if self._ws is None:
            return
        new_tickers = [t for t in tickers if t not in self._subscribed]
        if not new_tickers:
            return
        if not self._subscribed:
            # First-time subscribe.
            for ch in CHANNELS:
                cmd = {
                    "id": self._next_cmd_id, "cmd": "subscribe",
                    "params": {"channels": [ch], "market_tickers": new_tickers},
                }
                self._next_cmd_id += 1
                await self._ws.send(json.dumps(cmd))
        else:
            # Incremental add.
            # update_subscription needs sid per channel; easiest is to send
            # a brand new subscribe block — server will dedup.
            for ch in CHANNELS:
                cmd = {
                    "id": self._next_cmd_id, "cmd": "subscribe",
                    "params": {"channels": [ch], "market_tickers": new_tickers},
                }
                self._next_cmd_id += 1
                await self._ws.send(json.dumps(cmd))
        self._subscribed.update(new_tickers)
        print(f"[kalshi] subscribed {len(new_tickers)} new tickers "
              f"(total {len(self._subscribed)})", flush=True)

    async def run(self, get_universe) -> None:
        backoff = 2.0
        while True:
            headers = self.client.ws_auth_headers()
            if not headers:
                print("[kalshi] no auth → cannot connect", flush=True)
                await asyncio.sleep(30)
                continue
            try:
                async with websockets.connect(
                        WS_URL, ssl=_SSL,
                        **{_HEADER_PARAM: headers},
                        ping_interval=20, ping_timeout=15,
                        max_size=2**22) as ws:
                    self._ws = ws
                    self._subscribed.clear()
                    print("[kalshi] connected", flush=True)
                    backoff = 2.0

                    # Initial subscribe to current universe.
                    await self.update_universe(get_universe())

                    # Periodic refresh task (poll get_universe each 30s).
                    async def refresh():
                        while True:
                            await asyncio.sleep(30)
                            try:
                                await self.update_universe(get_universe())
                            except Exception as e:
                                print(f"[kalshi] refresh err: {e}", flush=True)

                    refresh_task = asyncio.create_task(refresh())
                    try:
                        async for raw in ws:
                            try:
                                data = json.loads(raw)
                            except Exception:
                                continue
                            try:
                                rows = _parse_msg(data)
                            except Exception as e:
                                rows = [{
                                    "ts_us": now_us(), "recv_us": now_us(),
                                    "venue": "kalshi", "msg_type": "parse_error",
                                    "market_id": "", "asset_id": "", "side": "",
                                    "price": None, "size": None,
                                    "payload": safe_dumps({"err": str(e), "raw": raw[:1000]}),
                                }]
                            self.writer.write_many(rows)
                    finally:
                        refresh_task.cancel()
                        self._ws = None
            except Exception as e:
                self._ws = None
                print(f"[kalshi] ws err: {type(e).__name__}: {e}", flush=True)
            print(f"[kalshi] reconnecting in {backoff:.1f}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
