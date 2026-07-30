"""Polymarket CLOB WS listener for BTC 5-min up/down markets.

Public market channel endpoint:
    wss://ws-subscriptions-clob.polymarket.com/ws/market

Subscribe with: {"assets_ids": ["<token_id_1>", ...], "type": "market"}

Event types observed:
    book               — full snapshot (asks/bids arrays)
    price_change       — single-level update
    tick_size_change   — minimum tick changed
    last_trade_price   — last trade fill

Polymarket asks/bids quote PRICES in [0, 1] decimal — we keep that scale
in `price` so it's compatible with Kalshi's $-scale.
"""
from __future__ import annotations
import asyncio, json, ssl, sys, time
from pathlib import Path
from typing import Dict, List, Optional, Set

import certifi
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tick_capture.writer import ParquetWriter, now_us, to_us, safe_dumps


WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_SSL = ssl.create_default_context(cafile=certifi.where())


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse(msgs, token_to_market: Dict[str, str],
           token_to_side: Dict[str, str]) -> List[dict]:
    """Polymarket sends either a single dict or a list of dicts.
    Each item has event_type indicating the kind of update."""
    rows: List[dict] = []
    recv = now_us()
    if isinstance(msgs, dict):
        msgs = [msgs]
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        ev = m.get("event_type", "")
        asset = m.get("asset_id", "") or m.get("token_id", "") or ""
        market_id = m.get("market", "") or token_to_market.get(asset, "")
        side_hint = token_to_side.get(asset, "")
        # Polymarket stamps `timestamp` as ms-since-epoch (string).
        ts_raw = m.get("timestamp") or m.get("time")
        ts_us = None
        if ts_raw:
            try:
                t = float(ts_raw)
                # heuristic: ms vs s
                ts_us = int(t * 1000) if t > 1e12 else int(t * 1_000_000)
            except Exception:
                ts_us = None
        if ts_us is None:
            ts_us = recv

        if ev == "book":
            asks = m.get("asks") or []
            bids = m.get("bids") or []
            # Best bid = max(bid price); best ask = min(ask price). PM lists in arrays.
            try:
                best_bid = max((float(b.get("price", 0)) for b in bids), default=None)
            except Exception:
                best_bid = None
            try:
                best_ask = min((float(a.get("price", 0)) for a in asks), default=None)
            except Exception:
                best_ask = None
            rows.append({
                "ts_us": ts_us, "recv_us": recv,
                "venue": "polymarket", "msg_type": "book",
                "market_id": market_id, "asset_id": asset, "side": side_hint,
                "price": best_bid, "size": best_ask,
                "payload": safe_dumps(m),
            })
            continue

        if ev == "price_change":
            # Single-level update — emit one row per change.
            changes = m.get("changes") or m.get("price_changes") or []
            if not changes:
                # Some envelopes carry price/size at the top level.
                price = _f(m.get("price"))
                size  = _f(m.get("size"))
                side  = (m.get("side") or "").lower()
                rows.append({
                    "ts_us": ts_us, "recv_us": recv,
                    "venue": "polymarket", "msg_type": "price_change",
                    "market_id": market_id, "asset_id": asset, "side": side,
                    "price": price, "size": size, "payload": safe_dumps(m),
                })
            else:
                for c in changes:
                    rows.append({
                        "ts_us": ts_us, "recv_us": recv,
                        "venue": "polymarket", "msg_type": "price_change",
                        "market_id": market_id, "asset_id": asset,
                        "side": (c.get("side") or "").lower(),
                        "price": _f(c.get("price")),
                        "size":  _f(c.get("size")),
                        "payload": safe_dumps(c),
                    })
            continue

        if ev == "last_trade_price":
            rows.append({
                "ts_us": ts_us, "recv_us": recv,
                "venue": "polymarket", "msg_type": "last_trade_price",
                "market_id": market_id, "asset_id": asset,
                "side": (m.get("side") or "").lower(),
                "price": _f(m.get("price")),
                "size":  _f(m.get("size")),
                "payload": safe_dumps(m),
            })
            continue

        if ev == "tick_size_change":
            rows.append({
                "ts_us": ts_us, "recv_us": recv,
                "venue": "polymarket", "msg_type": "tick_size_change",
                "market_id": market_id, "asset_id": asset, "side": side_hint,
                "price": _f(m.get("new_tick_size")) or _f(m.get("tick_size")),
                "size":  None, "payload": safe_dumps(m),
            })
            continue

        # Catch-all (e.g. PONG, subscribe ack)
        rows.append({
            "ts_us": ts_us, "recv_us": recv,
            "venue": "polymarket", "msg_type": ev or "unknown",
            "market_id": market_id, "asset_id": asset, "side": side_hint,
            "price": None, "size": None, "payload": safe_dumps(m),
        })
    return rows


class PolymarketListener:
    def __init__(self, writer: ParquetWriter):
        self.writer = writer
        self._subscribed: Set[str] = set()
        self._token_market: Dict[str, str] = {}
        self._token_side: Dict[str, str] = {}
        self._ws = None

    async def run(self, get_markets) -> None:
        """`get_markets()` returns the current list of polymarket discovery dicts
        (from discover.discover_polymarket_btc_5m). We re-resolve on each
        connect and any time the universe grows."""
        backoff = 2.0
        while True:
            try:
                # Reset and snapshot the current universe.
                self._token_market.clear()
                self._token_side.clear()
                self._subscribed.clear()
                markets = get_markets()
                tokens = self._extract_tokens(markets)
                if not tokens:
                    print("[polymarket] no tokens to subscribe yet, waiting…", flush=True)
                    await asyncio.sleep(15)
                    continue

                async with websockets.connect(
                        WS_URL, ssl=_SSL,
                        ping_interval=20, ping_timeout=15,
                        max_size=2**22) as ws:
                    self._ws = ws
                    print(f"[polymarket] connected, subscribing {len(tokens)} tokens",
                          flush=True)
                    await ws.send(json.dumps({"assets_ids": tokens, "type": "market"}))
                    self._subscribed.update(tokens)
                    backoff = 2.0

                    async def refresh():
                        while True:
                            await asyncio.sleep(30)
                            try:
                                new_markets = get_markets()
                                new_tokens = self._extract_tokens(new_markets)
                                add = [t for t in new_tokens if t not in self._subscribed]
                                if add and self._ws is not None:
                                    # Polymarket's market channel doesn't have an
                                    # incremental add; reconnecting is cleanest.
                                    print(f"[polymarket] {len(add)} new tokens; "
                                          f"forcing reconnect", flush=True)
                                    await self._ws.close()
                                    return
                            except Exception as e:
                                print(f"[polymarket] refresh err: {e}", flush=True)

                    refresh_task = asyncio.create_task(refresh())
                    try:
                        async for raw in ws:
                            if raw == "PONG":
                                continue
                            try:
                                data = json.loads(raw)
                            except Exception:
                                continue
                            try:
                                rows = _parse(data, self._token_market,
                                              self._token_side)
                            except Exception as e:
                                rows = [{
                                    "ts_us": now_us(), "recv_us": now_us(),
                                    "venue": "polymarket", "msg_type": "parse_error",
                                    "market_id": "", "asset_id": "", "side": "",
                                    "price": None, "size": None,
                                    "payload": safe_dumps({"err": str(e), "raw": str(raw)[:1000]}),
                                }]
                            self.writer.write_many(rows)
                    finally:
                        refresh_task.cancel()
                        self._ws = None
            except Exception as e:
                self._ws = None
                print(f"[polymarket] ws err: {type(e).__name__}: {e}", flush=True)
            print(f"[polymarket] reconnecting in {backoff:.1f}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    def _extract_tokens(self, markets: List[Dict]) -> List[str]:
        tokens: List[str] = []
        for m in markets or []:
            cid = m.get("condition_id", "")
            for side, key in (("yes", "yes_token_id"), ("no", "no_token_id")):
                tok = m.get(key)
                if tok:
                    tokens.append(str(tok))
                    self._token_market[str(tok)] = cid
                    self._token_side[str(tok)] = side
        return tokens
