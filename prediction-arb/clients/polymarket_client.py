"""Polymarket async client.

Two APIs:
- Gamma (https://gamma-api.polymarket.com): public, no auth, market discovery
- CLOB  (https://clob.polymarket.com): public reads (book/midpoint/price) + auth trading

For paper trading we only need Gamma + CLOB public endpoints.
Trading methods raise clearly until wallet creds are provided.
"""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients.base_client import PredictionMarketClient
from data.models import (NormalizedMarket, NormalizedPrice, OrderBook, OrderBookLevel,
                          OrderRequest, OrderResponse, Position,
                          Platform, Side)
from utils.helpers import retry_async, AsyncRateLimiter
from utils.logger import setup_logger

log = setup_logger("polymarket")


def _to_dt(s: str | None) -> Optional[datetime]:
    if not s: return None
    try:
        if s.endswith("Z"): s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _safe_float(v, default=None):
    try: return float(v)
    except (TypeError, ValueError): return default


class PolymarketClient(PredictionMarketClient):
    platform = Platform.POLYMARKET

    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL  = "https://clob.polymarket.com"

    def __init__(self,
                 private_key: str | None = None,
                 funder_address: str | None = None,
                 chain_id: int = 137,
                 rate_limit_reads_per_sec: float = 10.0):
        self.private_key = private_key or os.environ.get("POLYMARKET_PRIVATE_KEY")
        self.funder = funder_address or os.environ.get("POLYMARKET_FUNDER_ADDRESS")
        self.chain_id = chain_id
        self._authed = bool(self.private_key)

        self._gamma = httpx.AsyncClient(
            base_url=self.GAMMA_URL,
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self._clob = httpx.AsyncClient(
            base_url=self.CLOB_URL,
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self._rate = AsyncRateLimiter(rate_limit_reads_per_sec)
        log.info(f"PolymarketClient init  authed={self._authed}")

    async def close(self):
        await self._gamma.aclose()
        await self._clob.aclose()

    @retry_async(max_retries=3, exceptions=(httpx.HTTPError,))
    async def _gamma_get(self, path, **kw):
        await self._rate.acquire()
        r = await self._gamma.get(path, **kw)
        r.raise_for_status()
        return r.json()

    @retry_async(max_retries=3, exceptions=(httpx.HTTPError,))
    async def _clob_get(self, path, **kw):
        await self._rate.acquire()
        r = await self._clob.get(path, **kw)
        r.raise_for_status()
        return r.json()

    # ─── Markets via Gamma (public, no auth) ────────────────────────
    async def get_markets(self, status: str = "open", limit: int = 100,
                          category: str | None = None) -> list[NormalizedMarket]:
        """Active markets via Gamma. Gamma caps at 100/req — we paginate."""
        out: list[NormalizedMarket] = []
        offset = 0
        page_size = 100
        while len(out) < limit:
            params: dict[str, object] = {
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
            }
            if category:
                params["tag"] = category
            data = await self._gamma_get("/markets", params=params)
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            data = data or []
            if not data: break
            for m in data:
                try:
                    norm = self._normalize_market(m)
                    if norm.tradeable:
                        out.append(norm)
                    if len(out) >= limit: break
                except Exception as e:
                    log.debug(f"normalize fail: {e}")
            if len(data) < page_size: break
            offset += page_size
        return out[:limit]

    async def get_events_with_markets(self, limit: int = 200,
                                       category: str | None = None,
                                       tag_id: str | None = None):
        """Fetch Polymarket /events with nested markets. Returns list[NormalizedEvent].
        Each event has 1-N child binary markets."""
        from data.models import NormalizedEvent
        out = []
        offset = 0; page_size = 100
        while len(out) < limit:
            params = {"limit": page_size, "offset": offset,
                       "active": "true", "closed": "false",
                       "order": "volume24hr", "ascending": "false"}
            if tag_id: params["tag_id"] = tag_id
            try:
                data = await self._gamma_get("/events", params=params)
            except Exception as e:
                log.debug(f"events err: {e}"); break
            if not data: break
            for e in data:
                try:
                    out.append(self._normalize_event(e))
                    if len(out) >= limit: break
                except Exception as ex:
                    log.debug(f"event normalize fail: {ex}")
            if len(data) < page_size: break
            offset += page_size
        return out[:limit]

    def _normalize_event(self, e: dict):
        from data.models import NormalizedEvent
        # Tags: structured array of dicts with 'label' or just strings
        tags_raw = e.get("tags") or []
        tag_labels = []
        for t in tags_raw:
            if isinstance(t, dict):
                lbl = t.get("label") or t.get("slug") or ""
                if lbl: tag_labels.append(str(lbl))
            elif isinstance(t, str):
                tag_labels.append(t)
        # Infer category from tags
        tag_text = " ".join(tag_labels).lower()
        category = "other"
        for cand in ["politics", "sports", "crypto", "geopolitics", "finance",
                     "economics", "culture", "weather", "tech"]:
            if cand in tag_text: category = cand; break
        if category == "other":
            if any(k in tag_text for k in ("election", "trump", "biden", "senate")): category = "politics"
            elif any(k in tag_text for k in ("bitcoin", "ethereum", "btc", "eth", "solana")): category = "crypto"
            elif any(k in tag_text for k in ("soccer", "football", "nba", "nfl", "mlb", "world cup")): category = "sports"
            elif any(k in tag_text for k in ("fed", "rate", "inflation", "cpi", "gdp")): category = "economics"

        end_dt = _to_dt(e.get("endDate") or e.get("end_date"))
        # Normalize markets
        markets = []
        for m in e.get("markets", []) or []:
            try:
                nm = self._normalize_market(m)
                nm.raw["_parent_event_slug"] = e.get("slug")
                nm.raw["_parent_event_title"] = e.get("title")
                markets.append(nm)
            except Exception:
                pass
        return NormalizedEvent(
            platform=Platform.POLYMARKET,
            event_id=e.get("slug") or e.get("id") or "",
            title=e.get("title") or "",
            sub_title=e.get("description", "")[:200] if e.get("description") else "",
            category=category,
            tags=tag_labels,
            end_date=end_dt,
            markets=markets,
            mutually_exclusive=False,
            raw=e,
        )

    async def get_market(self, market_id: str) -> NormalizedMarket:
        """market_id here is the polymarket slug OR condition_id (0x...)."""
        # If looks like a hex conditionId, query that param directly
        is_condition_id = isinstance(market_id, str) and market_id.startswith("0x")
        if is_condition_id:
            data = await self._gamma_get("/markets", params={"condition_ids": market_id})
            if isinstance(data, list) and data:
                # Verify the returned market matches the requested conditionId
                for m in data:
                    if m.get("conditionId") == market_id:
                        return self._normalize_market(m)
                # If no exact match, fall through
        # Try slug
        try:
            data = await self._gamma_get("/markets", params={"slug": market_id})
            if isinstance(data, list) and data:
                return self._normalize_market(data[0])
        except Exception:
            pass
        raise ValueError(f"Polymarket market not found: {market_id}")

    async def get_price(self, market_id: str, token_id: str | None = None) -> NormalizedPrice:
        """market_id = condition_id, token_id = YES token id (CLOB)."""
        if not token_id:
            m = await self.get_market(market_id)
            token_id = m.yes_token_id
        if not token_id:
            raise ValueError(f"No YES token_id for {market_id}")
        # Get YES side book top
        yes_data = await self._clob_get("/book", params={"token_id": token_id})
        return self._book_to_price(market_id, token_id, yes_data)

    async def get_orderbook(self, market_id: str, side: Side = Side.YES,
                            depth: int = 10) -> OrderBook:
        """market_id = condition_id. YES book + NO book are separate tokens."""
        m = await self.get_market(market_id)
        tok = m.yes_token_id if side == Side.YES else m.no_token_id
        if not tok:
            raise ValueError(f"No token_id for side {side}")
        data = await self._clob_get("/book", params={"token_id": tok})
        bids = [OrderBookLevel(price=_safe_float(b.get("price"), 0),
                               size=_safe_float(b.get("size"), 0))
                for b in (data.get("bids") or [])[:depth]]
        asks = [OrderBookLevel(price=_safe_float(a.get("price"), 0),
                               size=_safe_float(a.get("size"), 0))
                for a in (data.get("asks") or [])[:depth]]
        bids.sort(key=lambda x: -x.price)
        asks.sort(key=lambda x: x.price)
        return OrderBook(platform=Platform.POLYMARKET, market_id=market_id,
                         side=side, bids=bids, asks=asks)

    def _book_to_price(self, market_id: str, token_id: str, data: dict) -> NormalizedPrice:
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        # Top of book
        yes_bid = _safe_float(bids[-1]["price"]) if bids else None
        yes_ask = _safe_float(asks[0]["price"]) if asks else None
        yes_bid_sz = _safe_float(bids[-1]["size"]) if bids else None
        yes_ask_sz = _safe_float(asks[0]["size"]) if asks else None
        # NO side is derived: NO_bid = 1 - YES_ask, NO_ask = 1 - YES_bid
        no_bid = (1.0 - yes_ask) if yes_ask is not None else None
        no_ask = (1.0 - yes_bid) if yes_bid is not None else None
        return NormalizedPrice(
            platform=Platform.POLYMARKET,
            market_id=market_id,
            yes_bid=yes_bid, yes_ask=yes_ask,
            yes_bid_size=yes_bid_sz, yes_ask_size=yes_ask_sz,
            no_bid=no_bid, no_ask=no_ask,
            no_bid_size=yes_ask_sz, no_ask_size=yes_bid_sz,
        )

    # ─── Trading (require auth) ─────────────────────────────────────
    async def place_order(self, order: OrderRequest) -> OrderResponse:
        if not self._authed:
            raise RuntimeError(
                "Polymarket client not authenticated. Set POLYMARKET_PRIVATE_KEY "
                "(and optionally POLYMARKET_FUNDER_ADDRESS) and install py-clob-client."
            )
        # When wallet is available, use py-clob-client here:
        #   from py_clob_client.client import ClobClient
        #   from py_clob_client.clob_types import OrderArgs, OrderType
        #   client = ClobClient(...)
        #   client.create_and_post_order(OrderArgs(token_id=..., price=..., size=..., side=...))
        raise NotImplementedError("Wire up py-clob-client when going live")

    async def cancel_order(self, order_id: str) -> bool:
        if not self._authed:
            raise RuntimeError("Polymarket client not authenticated")
        raise NotImplementedError("Wire up py-clob-client when going live")

    async def get_positions(self) -> list[Position]:
        if not self._authed: return []
        raise NotImplementedError("Wire up py-clob-client when going live")

    async def get_balance_usd(self) -> float:
        if not self._authed: return 0.0
        # USDC balance via Polygon — would call web3.eth.contract(USDC).balanceOf(wallet)
        return 0.0

    # ─── Normalize Gamma response ───────────────────────────────────
    def _normalize_market(self, m: dict) -> NormalizedMarket:
        # Token IDs (CLOB): the Gamma response embeds them as a JSON array string
        token_ids_field = m.get("clobTokenIds")
        token_ids = []
        if isinstance(token_ids_field, str):
            try: token_ids = json.loads(token_ids_field)
            except Exception: token_ids = []
        elif isinstance(token_ids_field, list):
            token_ids = token_ids_field
        yes_id = token_ids[0] if len(token_ids) > 0 else None
        no_id  = token_ids[1] if len(token_ids) > 1 else None

        # Category: try tags, then events, then question keywords
        tags = m.get("tags") or []
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except Exception: tags = []
        events = m.get("events") or []
        tag_text_parts = [str(t).lower() for t in tags]
        for e in (events if isinstance(events, list) else []):
            if isinstance(e, dict):
                tag_text_parts.append((e.get("category") or "").lower())
                tag_text_parts.append((e.get("slug") or "").lower())
        tag_text_parts.append((m.get("question") or "").lower())
        joined = " ".join(tag_text_parts)
        category = "other"
        for k in ("crypto", "bitcoin", "ethereum", "btc", "eth"):
            if k in joined: category = "crypto"; break
        if category == "other":
            for k_name, k_match in [
                ("politics", ["politics", "election", "president", "trump", "biden", "congress", "senate"]),
                ("sports",   ["sports", "nba", "nfl", "mlb", "world-cup", "football", "soccer", "tennis"]),
                ("geopolitics", ["geopolitics", "putin", "ukraine", "china", "iran", "israel", "war"]),
                ("finance",  ["finance", "stock", "market", "s&p", "nasdaq"]),
                ("economics", ["economics", "fed", "interest-rate", "inflation", "cpi", "gdp", "rate-cut"]),
                ("weather",  ["weather", "hurricane", "temperature", "snow"]),
                ("tech",     ["tech", "ai-", "openai", "tesla", "elon"]),
                ("culture",  ["culture", "oscar", "movie", "music", "grammy"]),
            ]:
                if any(p in joined for p in k_match): category = k_name; break

        # Tradeable check — Gamma exposes `enableOrderBook` + `acceptingOrders`
        tradeable = bool(m.get("enableOrderBook")) and bool(m.get("acceptingOrders", True))
        # Closed markets are NOT tradeable
        if m.get("closed") or not m.get("active", True): tradeable = False

        volume = _safe_float(m.get("volume24hr") or m.get("volume"), 0.0) or 0.0

        return NormalizedMarket(
            platform=Platform.POLYMARKET,
            market_id=m.get("conditionId") or m.get("condition_id") or m.get("slug", ""),
            yes_token_id=yes_id,
            no_token_id=no_id,
            title=m.get("question") or m.get("title") or m.get("slug") or "",
            description=m.get("description") or "",
            category=category,
            resolution_date=_to_dt(m.get("endDate") or m.get("end_date_iso") or m.get("end_date")),
            volume_usd=volume,
            open_interest=_safe_float(m.get("openInterest"), 0.0) or 0.0,
            tradeable=tradeable,
            raw=m,
        )
