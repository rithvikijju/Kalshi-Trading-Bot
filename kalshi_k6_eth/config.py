"""K6 strategy config + Kalshi REST client.

Standalone — does not depend on the existing strategy_cells/ bot. Reuses the
same auth scheme (RSA-PSS signature over timestamp+method+path) that Kalshi's
prod API expects.
"""
from __future__ import annotations
import base64, json, math, os, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

import requests

CFG = {
    # ─ Universe ─
    'series_ticker': 'KXETHD',        # BTC hourly threshold markets
    'spot_symbol':   'ETH-USD',       # Coinbase product id

    # ─ K6 entry thresholds (validated in strategy_zoo/kalshi_k6_v2.py) ─
    # Distance brackets in dollars (spot − strike). YES when positive, NO when negative.
    'k6_dist_brackets': [(1, 5), (5, 20)],    # ETH scale
    # Time-to-close brackets in seconds (0-5min, 5-15min)
    'k6_time_brackets': [(0, 300), (300, 900)],

    # ─ K14 vol regime filter (uses EWMA σ on spot returns) ─
    # YES side requires high vol (recent moves create staleness).
    # NO side requires low vol (high vol risks spot crossing back).
    # Thresholds are realized 15-min normalized stdev of log returns.
    # Empirical IS tercile boundaries on rv_15m: t1 ≈ 0.33, t2 ≈ 0.65 (annualized %).
    'k14_yes_min_rv': 0.33,    # YES only when rv_15m >= this (mid+high regime)
    'k14_no_max_rv':  0.65,    # NO  only when rv_15m  < this (low+mid regime)

    # ─ Position sizing & risk ─
    'starting_bankroll': 100.0,
    'max_concurrent_positions': 10,
    'max_total_exposure':       80.0,    # $80 max open at once on $100 bankroll
    'max_qty_per_signal':       20,
    'max_dollars_per_signal':   15.0,
    'kelly_fraction':           0.25,    # quarter-Kelly
    'expected_win_prob':        0.88,    # historical win rate
    'daily_loss_limit':        -10.0,    # halt for the day if -$10

    # ─ Polling cadence ─
    'spot_poll_sec':      2.0,
    'market_poll_sec':    3.0,   # REST poll Kalshi markets every 3s
    'decision_loop_sec':  3.0,
    'state_save_sec':    15.0,

    # ─ Kalshi fee ─
    'kalshi_fee_cap': 0.07,

    # ─ Endpoints ─
    'kalshi_base':   'https://api.elections.kalshi.com/trade-api/v2',
    'coinbase_url':  'https://api.exchange.coinbase.com/products/{pair}/ticker',

    # ─ Data persistence ─
    'data_dir':   Path(__file__).parent / 'data',
    'paper_log':  Path(__file__).parent / 'data' / 'paper_log.jsonl',
    'paper_state':Path(__file__).parent / 'data' / 'paper_state.json',
    'live_log':   Path(__file__).parent / 'data' / 'live_log.jsonl',
    'live_state': Path(__file__).parent / 'data' / 'live_state.json',

    # ─ Misc ─
    'order_expiration_sec': 20,
    'verbose': True,
}

CFG['data_dir'].mkdir(parents=True, exist_ok=True)


def kalshi_fee(price: float) -> float:
    """Per-contract fee. Matches Kalshi's published formula."""
    p = max(0.0, min(1.0, price))
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def load_credentials(env_path='~/.kalshi/credentials.env') -> Dict[str, str]:
    creds = {}
    path = Path(env_path).expanduser()
    if not path.exists():
        return creds
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        creds[k.strip()] = v.strip()
    return creds


class KalshiClient:
    """Minimal Kalshi REST client — supports unauthenticated market-data reads
    and (when private key is loaded) signed order placement."""

    def __init__(self, key_id: Optional[str] = None,
                 private_key_path: Optional[str] = None,
                 base_url: str = None):
        self.base = base_url or CFG['kalshi_base']
        creds = load_credentials()
        self.key_id = key_id or creds.get('KALSHI_PROD_KEY_ID')
        kp = private_key_path or creds.get('KALSHI_PROD_PRIVATE_KEY_PATH')
        self.private_key = None
        if kp:
            from cryptography.hazmat.primitives import serialization
            kp_path = Path(kp).expanduser()
            if kp_path.exists():
                with open(kp_path, 'rb') as f:
                    self.private_key = serialization.load_pem_private_key(
                        f.read(), password=None)
        self.session = requests.Session()

    def _sign(self, method: str, path: str) -> Dict[str, str]:
        if not self.private_key or not self.key_id:
            return {}
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split('?')[0]).encode('utf-8')
        sig = self.private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
        return {
            'KALSHI-ACCESS-KEY': self.key_id,
            'KALSHI-ACCESS-SIGNATURE': base64.b64encode(sig).decode(),
            'KALSHI-ACCESS-TIMESTAMP': ts,
        }

    def _get(self, path: str, params=None) -> dict:
        url = self.base + path
        h = self._sign('GET', '/trade-api/v2' + path)
        r = self.session.get(url, headers=h, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_events(self, series_ticker=None, status='open', limit=100):
        p = {'status': status, 'limit': limit}
        if series_ticker:
            p['series_ticker'] = series_ticker
        return self._get('/events', p)

    def get_markets(self, event_ticker=None, status='open', limit=200):
        p = {'status': status, 'limit': limit}
        if event_ticker:
            p['event_ticker'] = event_ticker
        return self._get('/markets', p)

    def get_orderbook(self, ticker: str, depth: int = 5):
        return self._get(f'/markets/{ticker}/orderbook', {'depth': depth})

    def get_balance(self):
        return self._get('/portfolio/balance')

    def get_positions(self):
        return self._get('/portfolio/positions')

    def place_limit(self, ticker: str, side: str, action: str,
                    count: int, price_cents: int,
                    expiration_sec: int = None) -> dict:
        """Limit order. side='yes'|'no', action='buy'|'sell'."""
        body = {
            'ticker': ticker, 'side': side, 'action': action, 'count': count,
            'type': 'limit',
            'client_order_id': f'k6-{uuid.uuid4().hex[:16]}',
            f'{side}_price': int(round(price_cents)),
            'expiration_ts': int(time.time()) +
                             (expiration_sec or CFG['order_expiration_sec']),
        }
        h = self._sign('POST', '/trade-api/v2/portfolio/orders')
        h['Content-Type'] = 'application/json'
        r = self.session.post(self.base + '/portfolio/orders',
                              headers=h, json=body, timeout=10)
        if r.status_code >= 400:
            return {'error': r.text[:200], 'status': r.status_code}
        return r.json()

    def cancel_order(self, order_id: str):
        h = self._sign('DELETE', f'/trade-api/v2/portfolio/orders/{order_id}')
        r = self.session.delete(self.base + f'/portfolio/orders/{order_id}',
                                headers=h, timeout=10)
        return {'status': r.status_code, 'body': r.text[:200]}


def _coinbase_spot(symbol: str = None) -> Optional[float]:
    """Pull latest BTC mid from Coinbase public ticker. No auth needed."""
    pair = symbol or CFG['spot_symbol']
    try:
        r = requests.get(CFG['coinbase_url'].format(pair=pair), timeout=5)
        r.raise_for_status()
        d = r.json()
        return float(d.get('price') or d.get('bid'))
    except Exception:
        return None
