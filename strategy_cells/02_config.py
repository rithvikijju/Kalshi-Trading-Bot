# § 1 — Config, imports, Kalshi API client
from __future__ import annotations
import asyncio, base64, hashlib, inspect, json, math, os, sqlite3, sys, threading, time, uuid, warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import pandas as pd
import requests
import websockets

# version-safe websockets header param detection
_ws_sig = inspect.signature(websockets.connect)
_WS_HEADER_PARAM = 'additional_headers' if 'additional_headers' in _ws_sig.parameters else 'extra_headers'

warnings.filterwarnings('ignore')

# ── Trading config ────────────────────────────────────────────────
CFG = {
    'mode': 'paper',                   # 'paper' or 'live'
    'live_enabled': False,

    # ─ Tier 1 (monotonicity arb — RISK-FREE) ─
    # V3 backtest: median book depth on T1 candidates is 17 (p95 52). Raising
    # cap from 10 to 20 captures more of the available depth on each arb.
    # V5 backtest: T1 in CALM spot regimes (|spot_move_30s| ≤ $30) prints
    # 100% win at avg qty 13.2 over 7d (t=3.28). Calm regimes likely persist
    # longer in live execution (less HFT competition).
    't1_enabled': True,
    't1_min_net_edge_cents': 1.5,
    't1_max_qty_per_leg': 20,         # was 10 (V3 depth-headroom finding)
    't1_max_dollars_per_pair': 40.0,  # was 20 (scaled with qty)
    't1_calm_filter_enabled': True,   # V5 — only trade T1 in calm spot regimes
    't1_calm_max_spot_move_30s': 30.0,  # dollars; |spot(t) - spot(t-30s)|

    # ─ Tier 2 (deep-ITM convergence — STATISTICAL) ─
    # Backtest H22: price≥0.88, fair≥0.95, edge≥1.5c, ttc 30-60m, spread≤2c
    #   → 71 trades, 98.6% win, +$8.99 unlimited capital
    #   → +$8.87 on $100 with 2× caps
    't2_enabled': True,
    't2_min_price': 0.88,
    't2_min_fair': 0.95,
    't2_min_edge_cents': 1.5,
    't2_min_dollar_distance_from_strike': 300.0,
    't2_min_pct_distance_from_strike': 0.004,
    't2_max_spread_at_entry': 0.02,
    't2_min_secs_to_close': 1800,
    't2_max_secs_to_close': 3600,
    't2_max_qty_per_strike': 5,       # was 2
    't2_max_dollars_per_trade': 15.0, # was 6
    't2_sigma_floor_annual': 0.35,

    # ─ Tier 3 (OTM persistence NO — NEW, validated out-of-sample) ─
    # Spot has been < strike for ≥5 min, buy NO at $0.60-$0.72 (yes_bid 0.28-0.40)
    # Backtest: 53 trades, 79.2% win, +$8.83. Asymmetric (only NO works on this sample).
    # IMPORTANT: edge is sample-specific to May 2026 BTC trend. Cap small.
    't3_enabled': True,
    't3_min_yes_bid': 0.28,
    't3_max_yes_bid': 0.40,
    't3_min_strike_distance': 100.0,
    't3_persistence_seconds': 300,    # spot must have been below K for ≥5 min
    't3_min_secs_to_close': 600,      # 10 min
    't3_max_secs_to_close': 1800,     # 30 min
    't3_max_qty_per_strike': 5,
    't3_max_dollars_per_trade': 12.0,

    # ─ Portfolio risk ─
    'max_concurrent_positions': 4,
    'max_total_exposure': 200.0,
    'daily_loss_limit': -30.0,

    # ─ Fees ─
    'kalshi_fee_cap': 0.07,

    # ─ Polling / scheduling ─
    'spot_poll_sec': 2.0,
    'decision_interval_sec': 3.0,
    'sigma_window_min': 60,
    'sigma_min_points': 15,
    'sigma_uncertainty_discount': 0.50,

    # ─ Websocket ─
    'ws_url': 'wss://external-api-ws.kalshi.com/trade-api/ws/v2',
    'ws_reconnect_base_sec': 2.0,
    'ws_reconnect_max_sec': 60.0,

    # ─ Order execution ─
    'order_buffer_cents': 1,
    'order_expiration_sec': 20,
    'order_post_timeout_sec': 3.0,

    # ─ Data persistence ─
    'trades_db_path': 'output/arb_strategy_trades.db',
    'ticks_db_path': 'output/arb_strategy_ticks.db',

    # ─ Universe ─
    # IMPORTANT — only KXBTCD (threshold-style: "BTC closes above $K") works
    # for this strategy. KXBTC (range/bucket: "BTC closes in $K–$K+100") has
    # a bell-curve price structure where monotonicity is not arbitrage:
    # both legs could end up worth $0, and a 90c loss is possible.
    'event_series': ('KXBTCD',),
}

Path('output').mkdir(exist_ok=True)


def kalshi_fee(price: float) -> float:
    p = max(0.0, min(1.0, price))
    return min(CFG['kalshi_fee_cap'], 0.07 * p / 0.50)


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


# ── Kalshi REST + Auth Client ─────────────────────────────────────
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_credentials(env_path='~/.kalshi/credentials.env'):
    creds = {}
    path = Path(env_path).expanduser()
    if not path.exists():
        print(f'Warning: no credentials file at {path}')
        return creds
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        creds[k.strip()] = v.strip()
    return creds


class KalshiClient:
    PROD_URL = 'https://api.elections.kalshi.com/trade-api/v2'
    DEMO_URL = 'https://demo-api.kalshi.co/trade-api/v2'

    def __init__(self, env='prod', key_id=None, private_key_path=None):
        assert env in ('prod', 'demo')
        self.env = env
        self.base_url = self.PROD_URL if env == 'prod' else self.DEMO_URL
        self._path_prefix = '/trade-api/v2'

        creds = load_credentials()
        prefix = 'KALSHI_PROD_' if env == 'prod' else 'KALSHI_DEMO_'
        self.key_id = key_id or creds.get(prefix + 'KEY_ID')
        kp = private_key_path or creds.get(prefix + 'PRIVATE_KEY_PATH')
        self.private_key = None
        if kp:
            kp_path = Path(kp).expanduser()
            if kp_path.exists():
                with open(kp_path, 'rb') as f:
                    self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        self.session = requests.Session()

    def _sign(self, method, path):
        if not self.private_key or not self.key_id:
            return {}
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split('?')[0]).encode('utf-8')
        sig = self.private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
        return {
            'KALSHI-ACCESS-KEY': self.key_id,
            'KALSHI-ACCESS-SIGNATURE': base64.b64encode(sig).decode(),
            'KALSHI-ACCESS-TIMESTAMP': ts,
        }

    def ws_auth_headers(self):
        return self._sign('GET', '/trade-api/ws/v2')

    def _get(self, path, params=None):
        url = self.base_url + path
        headers = self._sign('GET', self._path_prefix + path)
        r = self.session.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_events(self, series_ticker=None, status='open', limit=100):
        params = {'status': status, 'limit': limit}
        if series_ticker:
            params['series_ticker'] = series_ticker
        return self._get('/events', params)

    def get_markets(self, event_ticker=None, status='open', limit=200):
        params = {'status': status, 'limit': limit}
        if event_ticker:
            params['event_ticker'] = event_ticker
        return self._get('/markets', params)

    def get_market(self, ticker):
        return self._get(f'/markets/{ticker}')

    def get_orderbook(self, ticker, depth=10):
        return self._get(f'/markets/{ticker}/orderbook', {'depth': depth})

    def get_balance(self):
        return self._get('/portfolio/balance')

    def get_positions(self):
        return self._get('/portfolio/positions')

    def place_order(self, ticker, side, action, count, yes_price_cents, expiration_sec=20):
        # Limit order. side in {'yes','no'}, action in {'buy','sell'}.
        body = {
            'ticker': ticker, 'side': side, 'action': action, 'count': count,
            'type': 'limit',
            'client_order_id': f'arb-{uuid.uuid4().hex[:16]}',
            f'{side}_price': int(round(yes_price_cents)),
            'expiration_ts': int(time.time()) + expiration_sec,
        }
        h = self._sign('POST', self._path_prefix + '/portfolio/orders')
        h['Content-Type'] = 'application/json'
        r = self.session.post(self.base_url + '/portfolio/orders',
                              headers=h, json=body, timeout=10)
        if r.status_code >= 400:
            return {'error': r.text[:200], 'status': r.status_code}
        return r.json()

    def cancel_order(self, order_id):
        h = self._sign('DELETE', self._path_prefix + f'/portfolio/orders/{order_id}')
        r = self.session.delete(self.base_url + f'/portfolio/orders/{order_id}',
                                headers=h, timeout=10)
        return {'status': r.status_code, 'body': r.text[:200]}


# ── Set up clients ────────────────────────────────────────────────
kalshi_prod = KalshiClient(env='prod')
kalshi_prod.private_key = None
kalshi_prod.key_id = None

kalshi_live = None
try:
    _kl = KalshiClient(env='prod')
    if _kl.private_key and _kl.key_id:
        bal = _kl.get_balance()
        balance_cents = bal.get('balance', 0) if isinstance(bal, dict) else 0
        print(f'Live auth OK. Balance: ${balance_cents/100:.2f}')
        kalshi_live = _kl
    else:
        print('No prod credentials — paper mode only.')
except Exception as e:
    print(f'Live auth failed: {e}')

try:
    test = kalshi_prod.get_events(series_ticker='KXBTCD', status='open', limit=3)
    print(f'Prod market data OK — {len(test.get("events", []))} events')
except Exception as e:
    print(f'Prod market data failed: {e}')

print(f'Mode: {CFG["mode"]}  T1: {CFG["t1_enabled"]}  T2: {CFG["t2_enabled"]}')
print(f'WS: {CFG["ws_url"]}  (param: {_WS_HEADER_PARAM}, websockets {websockets.__version__})')
