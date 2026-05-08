"""Kalshi REST client with RSA-PSS signing. Includes WebSocket auth helper.

This module defines the bare HTTP implementation. It does NOT install any
wrapper on KalshiClient._get — wrappers (rate limit, retry) belong in callers
that opt into them. Re-importing this module never accumulates wrappers.
"""
from __future__ import annotations
import base64, time, requests
from pathlib import Path
from typing import Dict, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_credentials(env_path: str = "~/.kalshi/credentials.env") -> Dict[str, str]:
    creds = {}
    path = Path(env_path).expanduser()
    if not path.exists():
        return creds
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        creds[k.strip()] = v.strip()
    return creds


class KalshiClient:
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
    PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, env: str = "prod", key_id: Optional[str] = None,
                 private_key_path: Optional[str] = None):
        assert env in ("demo", "prod")
        self.env = env
        self.base_url = self.DEMO_URL if env == "demo" else self.PROD_URL
        self._path_prefix = "/trade-api/v2"

        creds = load_credentials()
        if env == "prod":
            self.key_id = key_id or creds.get("KALSHI_PROD_KEY_ID")
            kp = private_key_path or creds.get("KALSHI_PROD_PRIVATE_KEY_PATH")
        else:
            self.key_id = key_id or creds.get("KALSHI_DEMO_KEY_ID")
            kp = private_key_path or creds.get("KALSHI_DEMO_PRIVATE_KEY_PATH")

        self.private_key = None
        if kp:
            kp_path = Path(kp).expanduser()
            if kp_path.exists():
                with open(kp_path, "rb") as f:
                    self.private_key = serialization.load_pem_private_key(
                        f.read(), password=None)

        self.session = requests.Session()

    # ── Signing ────────────────────────────────────────────────
    def _sign(self, method: str, path: str) -> Dict[str, str]:
        if not self.private_key or not self.key_id:
            return {}
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split("?")[0]).encode("utf-8")
        sig = self.private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
        return {
            "KALSHI-ACCESS-KEY":       self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    def ws_auth_headers(self) -> Dict[str, str]:
        """Sign for the WebSocket handshake. Same RSA-PSS scheme as REST,
        but path = '/trade-api/ws/v2' and method = 'GET'."""
        return self._sign("GET", "/trade-api/ws/v2")

    # ── REST ───────────────────────────────────────────────────
    def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        url = self.base_url + path
        headers = self._sign("GET", self._path_prefix + path)
        r = self.session.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Dict) -> Dict:
        url = self.base_url + path
        headers = self._sign("POST", self._path_prefix + path)
        headers["Content-Type"] = "application/json"
        r = self.session.post(url, headers=headers, json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> Dict:
        url = self.base_url + path
        headers = self._sign("DELETE", self._path_prefix + path)
        r = self.session.delete(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json() if r.text else {}

    # ── Public read endpoints ──────────────────────────────────
    def get_events(self, series_ticker=None, status="open", limit=200):
        params = {"status": status, "limit": min(limit, 200)}
        if series_ticker: params["series_ticker"] = series_ticker
        return self._get("/events", params)

    def get_markets(self, event_ticker=None, status="open", limit=200):
        params = {"status": status, "limit": min(limit, 200)}
        if event_ticker: params["event_ticker"] = event_ticker
        return self._get("/markets", params)

    def get_market(self, ticker):
        return self._get(f"/markets/{ticker}")

    def get_orderbook(self, ticker, depth=10):
        return self._get(f"/markets/{ticker}/orderbook", {"depth": depth})

    # ── Authenticated endpoints ────────────────────────────────
    def get_balance(self):
        return self._get("/portfolio/balance")

    def get_positions(self):
        return self._get("/portfolio/positions")

    def place_order(self, ticker, side, action, count, type_="limit",
                     yes_price=None, no_price=None,
                     expiration_ts=None, client_order_id=None):
        body = {
            "ticker":          ticker,
            "side":            side,
            "action":          action,
            "count":           int(count),
            "type":            type_,
            "client_order_id": client_order_id or f"v2-{int(time.time()*1000)}",
        }
        if type_ == "limit":
            if side == "yes" and yes_price is not None:
                body["yes_price"] = int(round(yes_price * 100))
            elif side == "no" and no_price is not None:
                body["no_price"]  = int(round(no_price * 100))
        if expiration_ts is not None:
            body["expiration_ts"] = int(expiration_ts)
        return self._post("/portfolio/orders", body)

    def cancel_order(self, order_id):
        return self._delete(f"/portfolio/orders/{order_id}")


def parse_market_fields(m: dict) -> dict:
    """Extract the fields we care about from Kalshi's market dict.
    Handles the 2026 API format with *_dollars / *_fp string fields."""
    def fdollar(key):
        v = m.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    return {
        "ticker":        m.get("ticker"),
        "subtitle":      m.get("yes_sub_title") or m.get("subtitle", ""),
        "floor":         m.get("floor_strike"),
        "cap":           m.get("cap_strike"),
        "strike_type":   m.get("strike_type"),
        "yes_bid":       fdollar("yes_bid_dollars") or
                          (float(m["yes_bid"])/100 if m.get("yes_bid") is not None else None),
        "yes_ask":       fdollar("yes_ask_dollars") or
                          (float(m["yes_ask"])/100 if m.get("yes_ask") is not None else None),
        "no_bid":        fdollar("no_bid_dollars"),
        "no_ask":        fdollar("no_ask_dollars"),
        "last_price":    fdollar("last_price_dollars") or
                          (float(m["last_price"])/100 if m.get("last_price") is not None else None),
        "yes_bid_size":  fdollar("yes_bid_size_fp"),
        "yes_ask_size":  fdollar("yes_ask_size_fp"),
        "volume_24h":    fdollar("volume_24h_fp"),
        "volume_total":  fdollar("volume_fp") or m.get("volume"),
        "open_interest": fdollar("open_interest_fp") or m.get("open_interest"),
        "liquidity":     fdollar("liquidity_dollars"),
        "status":        m.get("status"),
        "close_time":    m.get("close_time"),
    }
