"""
ProjectX / TopstepX live broker adapter.

REST (httpx, async): auth + account/contract lookup + order entry + history warmup.
Realtime (pysignalr): market hub (quotes/trades) + user hub (orders/positions/trades).

Brackets are implemented as SERVER-SIDE OCO so a protective stop persists even if the
bot process dies — the single most important live-risk property. On entry we submit the
market order plus an opposite-side STOP (protective) and LIMIT (target); when one fills we
cancel its sibling. The trader sees the same Broker interface as in sim mode.

NOTE: requires `pip install pysignalr`. The exact bracket/OCO behaviour and SignalR payload
shapes should be confirmed against a live TopstepX account before trading real size —
they're coded to the documented schema but cannot be exercised without credentials here.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..config import API_BASE, MARKET_HUB, USER_HUB, INSTRUMENTS
from .base import (Broker, ContractMeta, Depth, Fill, Order, OrderType, Position, Quote,
                   Side, Trade)


class ProjectXBroker(Broker):
    def __init__(self, username: str, api_key: str, account_name: str = ""):
        super().__init__()
        self.username = username
        self.api_key = api_key
        self.account_name = account_name
        self.token: str | None = None
        self.account_id: int | None = None
        self.simulated_account: bool = True
        self._http = httpx.AsyncClient(base_url=API_BASE, timeout=20.0)
        self._cid_to_inst: dict[str, str] = {}     # contractId -> instrument key
        self._sym_to_inst: dict[str, str] = {}     # symbol/symbolId -> instrument key
        self._oco: dict[str, dict] = {}            # instrument -> {stop_id, target_id}
        self._books: dict[str, dict] = {}          # instrument -> {bids:{px:sz}, asks:{px:sz}}
        self._market_client = None
        self._user_client = None
        self._tasks: list[asyncio.Task] = []

    # ----------------------------------------------------------------- REST
    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict) -> dict:
        r = await self._http.post(path, json=body, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    async def authenticate(self):
        r = await self._http.post("/api/Auth/loginKey",
                                  json={"userName": self.username, "apiKey": self.api_key})
        r.raise_for_status()
        d = r.json()
        if not d.get("success") or not d.get("token"):
            raise RuntimeError(
                f"ProjectX auth failed (errorCode={d.get('errorCode')}, "
                f"msg={d.get('errorMessage')}). errorCode 3 = credentials rejected: use your "
                f"PROJECTX username (created when linking ProjectX in TopstepX Settings→API), "
                f"not your TopstepX login or email, and confirm the ProjectX API Access "
                f"subscription is active.")
        self.token = d["token"]

    async def _resolve_account(self):
        d = await self._post("/api/Account/search", {"onlyActiveAccounts": True})
        accounts = d.get("accounts", [])
        if not accounts:
            raise RuntimeError("no active TopstepX accounts found")
        acct = None
        if self.account_name:
            acct = next((a for a in accounts if a.get("name") == self.account_name), None)
        acct = acct or accounts[0]
        self.account_id = acct["id"]
        self.simulated_account = bool(acct.get("simulated", True))

    async def _resolve_contracts(self, instruments: list[str]):
        for key in instruments:
            search = INSTRUMENTS[key].search
            d = await self._post("/api/Contract/search", {"searchText": search, "live": False})
            contracts = d.get("contracts", [])
            # prefer an active contract whose name/description matches the search text
            pick = next((c for c in contracts if c.get("activeContract")), None) or \
                   (contracts[0] if contracts else None)
            if not pick:
                continue
            meta = ContractMeta(
                instrument=key, contract_id=str(pick["id"]),
                tick_size=float(pick.get("tickSize", INSTRUMENTS[key].tick_size)),
                tick_value=float(pick.get("tickValue", INSTRUMENTS[key].tick_value)),
                name=pick.get("name", search))
            self.contracts[key] = meta
            self._cid_to_inst[meta.contract_id] = key
            for sym_field in ("symbolId", "symbol", "name"):
                if pick.get(sym_field):
                    self._sym_to_inst[str(pick[sym_field])] = key

    async def retrieve_bars(self, instrument: str, minutes: int, n: int) -> list:
        """Warmup history via /History/retrieveBars. Returns list[Bar]."""
        from .base import Bar
        meta = self.contracts.get(instrument)
        if not meta:
            return []
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes * (n + 5))
        d = await self._post("/api/History/retrieveBars", {
            "contractId": meta.contract_id, "live": False,
            "startTime": start.isoformat(), "endTime": end.isoformat(),
            "unit": 2, "unitNumber": minutes, "limit": n, "includePartialBar": False})
        out = []
        for b in d.get("bars", []):
            ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp()
            out.append(Bar(instrument, ts, b["o"], b["h"], b["l"], b["c"], b.get("v", 0.0)))
        out.sort(key=lambda x: x.ts)
        return out

    # ----------------------------------------------------------------- lifecycle
    async def connect(self):
        await self.authenticate()
        await self._resolve_account()

    async def subscribe(self, instruments: list[str]):
        await self._resolve_contracts(instruments)
        await self._connect_hubs(instruments)

    async def _connect_hubs(self, instruments: list[str]):
        try:
            from pysignalr.client import SignalRClient
        except ImportError as e:
            raise RuntimeError("pysignalr not installed — `pip install pysignalr`") from e

        self._market_client = SignalRClient(f"{MARKET_HUB}?access_token={self.token}")
        self._user_client = SignalRClient(f"{USER_HUB}?access_token={self.token}")

        self._market_client.on("GatewayQuote", self._on_quote_evt)
        self._market_client.on("GatewayTrade", self._on_trade_evt)
        self._market_client.on("GatewayDepth", self._on_depth_evt)
        self._user_client.on("GatewayUserTrade", self._on_user_trade_evt)
        self._user_client.on("GatewayUserPosition", self._on_user_position_evt)

        async def _on_market_open():
            for key in instruments:
                meta = self.contracts.get(key)
                if meta:
                    await self._market_client.send("SubscribeContractQuotes", [meta.contract_id])
                    await self._market_client.send("SubscribeContractTrades", [meta.contract_id])
                    await self._market_client.send("SubscribeContractMarketDepth", [meta.contract_id])

        async def _on_user_open():
            await self._user_client.send("SubscribeAccounts", [])
            await self._user_client.send("SubscribeOrders", [self.account_id])
            await self._user_client.send("SubscribePositions", [self.account_id])
            await self._user_client.send("SubscribeTrades", [self.account_id])

        self._market_client.on_open(_on_market_open)
        self._user_client.on_open(_on_user_open)
        self._tasks.append(asyncio.create_task(self._market_client.run()))
        self._tasks.append(asyncio.create_task(self._user_client.run()))

    async def disconnect(self):
        for t in self._tasks:
            t.cancel()
        await self._http.aclose()

    # ----------------------------------------------------------------- event handlers
    def _inst_of(self, payload: dict) -> str | None:
        # TopstepX keys the contract differently per event: quotes use 'contract',
        # trades use 'contractId', some use 'symbolId'/'symbol'. Check them all.
        for f in ("contract", "contractId", "symbolId", "symbol"):
            v = payload.get(f)
            if v is not None:
                hit = self._cid_to_inst.get(str(v)) or self._sym_to_inst.get(str(v))
                if hit:
                    return hit
        return None

    def _lead_inst(self, args):
        """Many events are [contractId_string, payload]; resolve inst from the leading id."""
        if isinstance(args, (list, tuple)) and args and isinstance(args[0], str):
            return self._cid_to_inst.get(args[0]) or self._sym_to_inst.get(args[0])
        return None

    async def _on_quote_evt(self, args):
        inst = self._lead_inst(args)
        d = args[-1] if isinstance(args, (list, tuple)) and isinstance(args[-1], dict) else {}
        inst = inst or self._inst_of(d)
        if not inst or not d:
            return
        await self._emit_quote(Quote(
            instrument=inst, bid=float(d.get("bestBid") or 0.0), ask=float(d.get("bestAsk") or 0.0),
            last=float(d.get("lastPrice") or 0.0), volume=float(d.get("volume") or 0.0),
            ts=time.time()))

    async def _on_trade_evt(self, args):
        # GatewayTrade payload is a LIST of trade dicts: ['CONTRACT', [ {..}, {..} ]]
        inst = self._lead_inst(args)
        payload = args[-1] if isinstance(args, (list, tuple)) else args
        trades = payload if isinstance(payload, list) else [payload]
        for d in trades:
            if not isinstance(d, dict):
                continue
            i = inst or self._inst_of(d)
            if not i:
                continue
            # trade 'type': 0 = buy-aggressor, 1 = sell-aggressor
            t = d.get("type")
            aggr = 1 if t == 0 else (-1 if t == 1 else 0)
            await self._emit_trade(Trade(
                instrument=i, price=float(d.get("price") or 0.0),
                volume=float(d.get("volume") or 0.0), aggressor=aggr, ts=time.time()))

    # ProjectX DOM update 'type' enum (assumed; verify live): bid-side {2,4,9}, ask-side
    # {1,3,10}, 5=trade (ignore for book), 6=reset. volume 0 removes the level.
    _BID_TYPES = {2, 4, 9}
    _ASK_TYPES = {1, 3, 10}

    async def _on_depth_evt(self, args):
        d = args
        # GatewayDepth arrives as [contractId, [updates]] or just [updates]
        inst = None
        updates = None
        if isinstance(d, (list, tuple)):
            for el in d:
                if isinstance(el, str) and (self._cid_to_inst.get(el) or self._sym_to_inst.get(el)):
                    inst = self._cid_to_inst.get(el) or self._sym_to_inst.get(el)
                elif isinstance(el, list):
                    updates = el
            if updates is None and d and isinstance(d[0], dict):
                updates = d
        if updates is None:
            return
        if inst is None:
            inst = self._inst_of(updates[0]) if updates and isinstance(updates[0], dict) else None
        if inst is None:
            return
        book = self._books.setdefault(inst, {"bids": {}, "asks": {}})
        for u in updates:
            if not isinstance(u, dict):
                continue
            t = int(u.get("type", -1))
            px = float(u.get("price", 0.0))
            vol = float(u.get("volume", 0.0))
            if t == 6:                                  # reset
                book["bids"].clear(); book["asks"].clear(); continue
            side = "bids" if t in self._BID_TYPES else ("asks" if t in self._ASK_TYPES else None)
            if side is None:
                continue
            if vol <= 0:
                book[side].pop(px, None)
            else:
                book[side][px] = vol
        bids = sorted(book["bids"].items(), key=lambda x: -x[0])[:10]
        asks = sorted(book["asks"].items(), key=lambda x: x[0])[:10]
        if bids or asks:
            await self._emit_depth(Depth(inst, bids, asks, time.time()))

    async def _on_user_position_evt(self, args):
        d = self._data(args)
        inst = self._inst_of(d)
        if not inst:
            return
        size = int(d.get("size") or 0)
        # ProjectX position 'type': 1=long, 2=short (size is magnitude)
        if d.get("type") == 2:
            size = -abs(size)
        pos = self.position(inst)
        pos.size = size
        pos.avg_price = float(d.get("averagePrice") or pos.avg_price)
        if size == 0:
            self._oco.pop(inst, None)
        await self._emit_position(pos)

    async def _on_user_trade_evt(self, args):
        d = self._data(args)
        inst = self._inst_of(d)
        if not inst:
            return
        side = Side.BUY if int(d.get("side", 0)) == 0 else Side.SELL
        pnl = float(d.get("profitAndLoss") or 0.0)
        fees = float(d.get("fees") or 0.0)
        await self._emit_fill(Fill(inst, side, int(d.get("size") or 0),
                                   float(d.get("price") or 0.0), pnl, fees,
                                   tag=str(d.get("orderId") or "")))
        # OCO: if this fill closed the position, cancel the resting sibling
        if pnl != 0.0:
            await self._cancel_oco_siblings(inst, filled_order_id=d.get("orderId"))

    # ----------------------------------------------------------------- trading
    async def place(self, order: Order) -> Order:
        meta = self.contracts.get(order.instrument)
        if not meta or self.account_id is None:
            order.status = "rejected"
            return order
        body = {"accountId": self.account_id, "contractId": meta.contract_id,
                "type": int(order.type), "side": int(order.side), "size": order.size}
        if order.limit_price is not None:
            body["limitPrice"] = order.limit_price
        if order.stop_price is not None:
            body["stopPrice"] = order.stop_price
        if order.tag:
            body["customTag"] = order.tag
        d = await self._post("/api/Order/place", body)
        if not d.get("success"):
            order.status = "rejected"
            return order
        order.order_id = d.get("orderId")
        order.status = "working"
        # attach server-side protective stop + target as OCO siblings
        if order.bracket_stop is not None and order.bracket_target is not None:
            await self._place_brackets(order, meta)
        return order

    async def _place_brackets(self, entry: Order, meta: ContractMeta):
        opp = entry.side.opposite
        stop_order = Order(entry.instrument, opp, entry.size, OrderType.STOP,
                           stop_price=entry.bracket_stop, tag=f"{entry.tag}:stop")
        tgt_order = Order(entry.instrument, opp, entry.size, OrderType.LIMIT,
                          limit_price=entry.bracket_target, tag=f"{entry.tag}:target")
        s = await self._raw_place(stop_order, meta)
        t = await self._raw_place(tgt_order, meta)
        self._oco[entry.instrument] = {"stop_id": s, "target_id": t}

    async def _raw_place(self, order: Order, meta: ContractMeta) -> int | None:
        body = {"accountId": self.account_id, "contractId": meta.contract_id,
                "type": int(order.type), "side": int(order.side), "size": order.size}
        if order.limit_price is not None:
            body["limitPrice"] = order.limit_price
        if order.stop_price is not None:
            body["stopPrice"] = order.stop_price
        if order.tag:
            body["customTag"] = order.tag
        d = await self._post("/api/Order/place", body)
        return d.get("orderId") if d.get("success") else None

    async def _cancel_oco_siblings(self, instrument: str, filled_order_id):
        oco = self._oco.get(instrument)
        if not oco:
            return
        for oid in (oco.get("stop_id"), oco.get("target_id")):
            if oid is not None and oid != filled_order_id:
                try:
                    await self.cancel(oid)
                except Exception:
                    pass
        self._oco.pop(instrument, None)

    async def cancel(self, order_id: int):
        await self._post("/api/Order/cancel", {"accountId": self.account_id, "orderId": order_id})

    async def flatten(self, instrument: str):
        meta = self.contracts.get(instrument)
        if not meta:
            return
        await self._cancel_oco_siblings(instrument, filled_order_id=None)
        try:
            await self._post("/api/Position/closeContract",
                             {"accountId": self.account_id, "contractId": meta.contract_id})
        except Exception:
            # fallback: send an opposing market order
            pos = self.positions.get(instrument)
            if pos and not pos.flat:
                await self.place(Order(instrument, pos.side.opposite, abs(pos.size),
                                       OrderType.MARKET, tag="flatten"))
