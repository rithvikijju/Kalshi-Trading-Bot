"""Kalshi async client. Low-latency: persistent httpx.AsyncClient + connection pool.

Auth: RSA-PSS-signed headers per Kalshi spec. Credentials read from (in order):
  1. Constructor args (api_key_id, private_key_path)
  2. Project .env via python-dotenv (KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH)
  3. Existing ~/.kalshi/credentials.env (KALSHI_PROD_KEY_ID + KALSHI_PROD_PRIVATE_KEY_PATH)

Critical 2026 changes implemented:
- Fixed-point dollar string prices ('0.6500' not 65)
- Use yes_price_dollars / no_price_dollars on order placement
"""
from __future__ import annotations
import os, time, base64, asyncio, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _load_kalshi_env() -> dict:
    """Find Kalshi credentials wherever they live. Returns dict that may
    include KALSHI_API_KEY_ID, KALSHI_PROD_KEY_ID, etc."""
    creds = {}
    # 1. Project .env (current directory or one up)
    for envfile in (Path(".env"), Path("../.env"), Path(__file__).resolve().parents[1] / ".env"):
        if envfile.exists():
            for line in envfile.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip().strip('"').strip("'")
            break
    # 2. Existing ~/.kalshi/credentials.env (the kalshi bot's convention)
    home_env = Path.home() / ".kalshi" / "credentials.env"
    if home_env.exists():
        for line in home_env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            creds.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # 3. Already-set environment variables take precedence
    for k in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH", "KALSHI_ENV",
              "KALSHI_PROD_KEY_ID", "KALSHI_PROD_PRIVATE_KEY_PATH",
              "KALSHI_DEMO_KEY_ID", "KALSHI_DEMO_PRIVATE_KEY_PATH"):
        if k in os.environ: creds[k] = os.environ[k]
    return creds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients.base_client import PredictionMarketClient
from data.models import (NormalizedMarket, NormalizedPrice, OrderBook, OrderBookLevel,
                          OrderRequest, OrderResponse, OrderStatus, Position,
                          Platform, Side)
from utils.helpers import retry_async, AsyncRateLimiter
from utils.logger import setup_logger

log = setup_logger("kalshi")


def _to_dt(s: str | None) -> Optional[datetime]:
    if not s: return None
    try:
        if s.endswith('Z'): s = s[:-1] + '+00:00'
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _parse_price(v) -> Optional[float]:
    """Kalshi 2026: dollar strings like '0.6500'. Old: integers like 65 (cents)."""
    if v is None or v == "": return None
    try:
        f = float(v)
        return f if f <= 1.0 else f / 100.0  # handle legacy cent integers gracefully
    except (TypeError, ValueError):
        return None


class KalshiClient(PredictionMarketClient):
    platform = Platform.KALSHI

    PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
    PATH_PREFIX = "/trade-api/v2"

    def __init__(self,
                 api_key_id: str | None = None,
                 private_key_path: str | None = None,
                 environment: str = "production",
                 rate_limit_reads_per_sec: float = 18.0):
        creds = _load_kalshi_env()
        env = environment or creds.get("KALSHI_ENV") or "production"
        # Prefer prod-specific vars; fall back to generic names
        if env == "production":
            self.api_key_id = (api_key_id
                                or creds.get("KALSHI_API_KEY_ID")
                                or creds.get("KALSHI_PROD_KEY_ID"))
            kp = (private_key_path
                  or creds.get("KALSHI_PRIVATE_KEY_PATH")
                  or creds.get("KALSHI_PROD_PRIVATE_KEY_PATH"))
        else:
            self.api_key_id = (api_key_id
                                or creds.get("KALSHI_API_KEY_ID")
                                or creds.get("KALSHI_DEMO_KEY_ID"))
            kp = (private_key_path
                  or creds.get("KALSHI_PRIVATE_KEY_PATH")
                  or creds.get("KALSHI_DEMO_PRIVATE_KEY_PATH"))
        self.base_url = self.PROD_URL if env == "production" else self.DEMO_URL

        self._private_key = None
        if kp and Path(kp).expanduser().exists():
            with open(Path(kp).expanduser(), "rb") as f:
                self._private_key = serialization.load_pem_private_key(f.read(), password=None)

        # Connection pooling for low latency — HTTP/2, keep-alive, reuse TCP socket
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self._rate = AsyncRateLimiter(rate_limit_reads_per_sec)
        self._authed = bool(self.api_key_id and self._private_key)
        log.info(f"KalshiClient init  env={environment}  authed={self._authed}")

    async def close(self):
        await self._http.aclose()

    # ─── Auth ───────────────────────────────────────────────────────
    def _sign_headers(self, method: str, path: str) -> dict:
        """RSA-PSS sign with SHA-256, salt_length=DIGEST_LENGTH."""
        if not self._authed:
            return {}
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path.split("?")[0]).encode("utf-8")
        sig = self._private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "Accept": "application/json",
        }

    @retry_async(max_retries=3, exceptions=(httpx.HTTPError,))
    async def _request(self, method: str, path: str, **kw):
        await self._rate.acquire()
        headers = self._sign_headers(method, self.PATH_PREFIX + path)
        headers.update(kw.pop("headers", {}))
        r = await self._http.request(method, path, headers=headers, **kw)
        if r.status_code == 429:
            await asyncio.sleep(1.0)
            r.raise_for_status()
        r.raise_for_status()
        return r.json()

    # ─── Markets ────────────────────────────────────────────────────
    async def get_markets(self, status: str = "open", limit: int = 100,
                          category: str | None = None,
                          series_ticker: str | None = None) -> list[NormalizedMarket]:
        """List markets. Paginates via cursor up to `limit`."""
        params: dict[str, object] = {"status": status, "limit": min(limit, 200)}
        if series_ticker: params["series_ticker"] = series_ticker
        out: list[NormalizedMarket] = []
        cursor = None
        while len(out) < limit:
            if cursor: params["cursor"] = cursor
            data = await self._request("GET", "/markets", params=params)
            for m in data.get("markets", []):
                out.append(self._normalize_market(m))
                if len(out) >= limit: break
            cursor = data.get("cursor")
            if not cursor: break
        if category:
            cat_l = category.lower()
            out = [m for m in out if cat_l in (m.category or "").lower()]
        return out

    async def get_events_with_markets(self, series_tickers: list[str] | None = None,
                                       limit_per_series: int = 50,
                                       status: str = "open"):
        """Fetch Kalshi /events with nested markets. Returns list[NormalizedEvent]."""
        from data.models import NormalizedEvent
        out = []
        series_list = series_tickers or [None]
        for series in series_list:
            params = {"limit": min(limit_per_series, 200), "status": status,
                       "with_nested_markets": "true"}
            if series: params["series_ticker"] = series
            try:
                data = await self._request("GET", "/events", params=params)
            except Exception as e:
                log.debug(f"events fetch err {series}: {e}"); continue
            for e in data.get("events", []) or []:
                try:
                    out.append(self._normalize_event(e))
                except Exception as ex:
                    log.debug(f"event normalize fail: {ex}")
        return out

    def _normalize_event(self, e: dict):
        from data.models import NormalizedEvent
        cat = (e.get("category") or "").lower() or "other"
        cat_map = {"financials": "finance", "world": "geopolitics", "climate": "weather"}
        cat = cat_map.get(cat, cat)
        end_dt = _to_dt(e.get("strike_date") or e.get("expected_expiration_time"))
        # Normalize child markets too
        markets = []
        for m in e.get("markets", []) or []:
            try:
                nm = self._normalize_market(m)
                # Tag with parent event metadata for downstream pairing
                nm.raw["_parent_event_ticker"] = e.get("event_ticker")
                markets.append(nm)
            except Exception:
                pass
        return NormalizedEvent(
            platform=Platform.KALSHI,
            event_id=e.get("event_ticker", ""),
            title=e.get("title") or "",
            sub_title=e.get("sub_title") or "",
            category=cat,
            series=e.get("series_ticker") or "",
            end_date=end_dt,
            markets=markets,
            mutually_exclusive=bool(e.get("mutually_exclusive", False)),
            raw=e,
        )

    async def get_markets_by_series(self, series_tickers: list[str],
                                     limit_per_series: int = 100,
                                     status: str = "open") -> list[NormalizedMarket]:
        """Pull markets from multiple specific series in parallel. Use this when
        you want a CATEGORY-BALANCED sample (e.g., crypto + politics + econ),
        not just whatever's on the top of the global feed (which tends to be sports)."""
        tasks = [self.get_markets(status=status, limit=limit_per_series,
                                   series_ticker=s) for s in series_tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[NormalizedMarket] = []
        for r in results:
            if isinstance(r, list): out.extend(r)
        return out

    async def get_market(self, market_id: str) -> NormalizedMarket:
        data = await self._request("GET", f"/markets/{market_id}")
        return self._normalize_market(data.get("market", data))

    async def get_price(self, market_id: str, token_id: str | None = None) -> NormalizedPrice:
        ob = await self._request("GET", f"/markets/{market_id}/orderbook", params={"depth": 5})
        # 2026 spec: orderbook_fp + yes_dollars/no_dollars (dollar strings)
        # Legacy:   orderbook    + yes/no                (integer cents)
        book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
        yes_book = book.get("yes_dollars") or book.get("yes") or []
        no_book  = book.get("no_dollars")  or book.get("no")  or []
        # Kalshi sorts ascending by price → best bid is the LAST element
        yes_top  = yes_book[-1] if yes_book else None
        no_top   = no_book[-1]  if no_book  else None
        yes_bid  = _parse_price(yes_top[0]) if yes_top else None
        no_bid   = _parse_price(no_top[0])  if no_top  else None
        yes_bid_sz = float(yes_top[1]) if yes_top else None
        no_bid_sz  = float(no_top[1])  if no_top  else None
        return NormalizedPrice(
            platform=Platform.KALSHI, market_id=market_id,
            yes_bid=yes_bid, yes_bid_size=yes_bid_sz,
            no_bid=no_bid,   no_bid_size=no_bid_sz,
            # The OPPOSITE side's bid gives us OUR ask (no_bid=0.99 → yes_ask=0.01)
            yes_ask=(1.0 - no_bid) if no_bid is not None else None,
            yes_ask_size=no_bid_sz,
            no_ask=(1.0 - yes_bid) if yes_bid is not None else None,
            no_ask_size=yes_bid_sz,
        )

    async def get_orderbook(self, market_id: str, side: Side = Side.YES,
                            depth: int = 10) -> OrderBook:
        ob = await self._request("GET", f"/markets/{market_id}/orderbook", params={"depth": depth})
        book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
        yes_raw = book.get("yes_dollars") or book.get("yes") or []
        no_raw  = book.get("no_dollars")  or book.get("no")  or []
        # YES asks are derived from NO bids: yes_ask = 1 - no_bid
        if side == Side.YES:
            yes_bids = [OrderBookLevel(price=_parse_price(l[0]) or 0, size=float(l[1]))
                        for l in yes_raw]
            yes_bids.sort(key=lambda x: -x.price)  # descending: best bid first
            yes_asks = [OrderBookLevel(price=1.0 - (_parse_price(l[0]) or 0), size=float(l[1]))
                        for l in no_raw]
            yes_asks.sort(key=lambda x: x.price)   # ascending: best ask first
            return OrderBook(platform=Platform.KALSHI, market_id=market_id,
                             side=Side.YES, bids=yes_bids, asks=yes_asks)
        else:
            no_bids = [OrderBookLevel(price=_parse_price(l[0]) or 0, size=float(l[1]))
                       for l in no_raw]
            no_bids.sort(key=lambda x: -x.price)
            no_asks = [OrderBookLevel(price=1.0 - (_parse_price(l[0]) or 0), size=float(l[1]))
                       for l in yes_raw]
            no_asks.sort(key=lambda x: x.price)
            return OrderBook(platform=Platform.KALSHI, market_id=market_id,
                             side=Side.NO, bids=no_bids, asks=no_asks)

    # ─── Trades (price fallback) ────────────────────────────────────
    async def get_recent_trades(self, market_id: str, limit: int = 50) -> list[dict]:
        """Most-recent public trades for a market, newest first.

        Important: the public `api.elections.kalshi.com` mirror returns a *null*
        orderbook for many live SPORTS markets even while they trade actively,
        so `get_price`/`get_orderbook` come back empty there. The trades feed
        still works, which makes this the only reliable price source for those
        markets. Each trade dict has yes_price_dollars / no_price_dollars /
        count_fp / taker_side / created_time."""
        try:
            data = await self._request("GET", "/markets/trades",
                                       params={"ticker": market_id, "limit": min(limit, 1000)})
        except Exception as e:
            log.debug(f"get_recent_trades {market_id}: {e}")
            return []
        return data.get("trades", []) or []

    async def get_last_trade_price(self, market_id: str) -> Optional[float]:
        """Last traded YES price (0..1), or None if the market has never traded."""
        trades = await self.get_recent_trades(market_id, limit=1)
        if not trades:
            return None
        return _parse_price(trades[0].get("yes_price_dollars"))

    # ─── Trading ────────────────────────────────────────────────────
    async def place_order(self, order: OrderRequest) -> OrderResponse:
        if not self._authed:
            raise RuntimeError("Kalshi client not authenticated. Set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH.")
        body = {
            "ticker": order.market_id,
            "side": order.side.value,
            "action": order.action,
            "count": int(order.size),
            "type": "limit" if order.limit_price else "market",
            "client_order_id": f"arb-{int(time.time()*1e6)}",
        }
        if order.limit_price:
            # 2026 spec: dollar string
            body[f"{order.side.value}_price_dollars"] = f"{order.limit_price:.4f}"
        if order.post_only:
            body["post_only"] = True
        data = await self._request("POST", "/portfolio/orders", json=body)
        o = data.get("order", data)
        return OrderResponse(
            order_id=o.get("order_id", ""),
            client_order_id=o.get("client_order_id"),
            platform=Platform.KALSHI,
            market_id=order.market_id,
            side=order.side,
            requested_size=order.size,
            filled_size=float(o.get("filled_count", 0)),
            avg_fill_price=_parse_price(o.get("average_price")),
            status=OrderStatus(o.get("status", "pending").lower()) if o.get("status") else OrderStatus.PENDING,
            fees_paid_usd=float(o.get("fee", 0)) / 100,
            raw=o,
        )

    async def cancel_order(self, order_id: str) -> bool:
        if not self._authed:
            raise RuntimeError("Kalshi client not authenticated")
        await self._request("DELETE", f"/portfolio/orders/{order_id}")
        return True

    async def get_positions(self) -> list[Position]:
        if not self._authed: return []
        data = await self._request("GET", "/portfolio/positions")
        out = []
        for p in data.get("market_positions", []):
            size = int(p.get("position", 0))
            if size == 0: continue
            side = Side.YES if size > 0 else Side.NO
            out.append(Position(
                platform=Platform.KALSHI,
                market_id=p.get("ticker", ""),
                side=side,
                size=abs(size),
                avg_entry_price=_parse_price(p.get("market_exposure", 0)) or 0,
                realized_pnl=float(p.get("realized_pnl", 0)) / 100,
            ))
        return out

    async def get_balance_usd(self) -> float:
        if not self._authed: return 0.0
        data = await self._request("GET", "/portfolio/balance")
        bal = data.get("balance", 0)
        return float(bal) / 100  # Kalshi returns cents

    # ─── Normalize ──────────────────────────────────────────────────
    def _normalize_market(self, m: dict) -> NormalizedMarket:
        cat = (m.get("category") or "").lower()
        # Kalshi categories tend to be high-level; map common ones
        cat_map = {
            "politics": "politics", "economics": "economics",
            "financials": "finance", "weather": "weather",
            "sports": "sports", "world": "geopolitics", "crypto": "crypto",
            "climate": "weather", "science": "tech",
        }
        category = cat_map.get(cat, cat or "other")
        # Fallback: infer from ticker prefix (KXBTC* = crypto, KXFED* = econ, etc.)
        if category == "other":
            tk = (m.get("ticker") or "").upper()
            series = (m.get("series_ticker") or "").upper()
            key = (series or tk[:20])
            inferred = None
            for prefix, cat_name in [
                ("KXBTC", "crypto"), ("KXETH", "crypto"), ("KXSOL", "crypto"),
                ("KXFED", "economics"), ("KXCPI", "economics"), ("KXJOBS", "economics"),
                ("KXGDP", "economics"), ("KXRATE", "economics"),
                ("KXPRES", "politics"), ("KXSEN", "politics"), ("KXHOUSE", "politics"),
                ("KXSPX", "finance"), ("KXNDX", "finance"),
                ("KXMVE", "sports"), ("KXNBA", "sports"), ("KXNFL", "sports"),
                ("KXMLB", "sports"), ("KXWC", "sports"), ("KXSOC", "sports"),
                ("KXWORLD", "geopolitics"), ("KXWAR", "geopolitics"),
                ("KXTEMP", "weather"), ("KXHURR", "weather"),
                ("KXOSCAR", "culture"), ("KXGRAMMY", "culture"),
            ]:
                if key.startswith(prefix): inferred = cat_name; break
            if inferred: category = inferred
        return NormalizedMarket(
            platform=Platform.KALSHI,
            market_id=m.get("ticker", ""),
            title=m.get("title") or m.get("subtitle") or "",
            description=m.get("rules_primary") or m.get("subtitle") or "",
            category=category,
            resolution_date=_to_dt(m.get("close_time") or m.get("expected_expiration_time")),
            volume_usd=float(m.get("volume", 0)),
            open_interest=float(m.get("open_interest", 0)),
            tradeable=(m.get("status") or "").lower() == "active",
            raw=m,
        )
