#!/usr/bin/env python3
"""
Kalshi BTC 1-hour continuous live trading script.

Every 2 minutes:
  1. Refresh BTC minute bars (Coinbase)
  2. Rebuild empirical sample cache + HAR-RV model
  3. Check settlements on existing positions
  4. Manage open positions (stop-loss, take-profit, time-exit)
  5. Scan for new KXBTC/KXBTCD events
  6. Compute edges, size with quarter-Kelly, record/place trades
  7. Print portfolio report

Usage:
    python scripts/btc_1hr_live.py              # paper mode (default)
    python scripts/btc_1hr_live.py --mode live   # real money (requires prod creds + ack)
"""

import sys, os, time, json, argparse, sqlite3, base64, logging, math
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser
from pathlib import Path
from scipy.stats import norm
from sklearn.linear_model import LinearRegression, LogisticRegression
from dataclasses import dataclass, field
from typing import Optional, Dict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.btc_1hr_config import (
    CFG,
    MINUTES_PER_YEAR,
    TERMINAL_STATUSES,
    get_db_path,
    kalshi_fee_dollars,
    load_credentials,
)

# ── Logging setup ──────────────────────────────────────────────────────────
_LOG_DIR = Path(_PROJECT_ROOT) / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / f"btc_1hr_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(_LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("btc_1hr")

# ── Crypto auth ────────────────────────────────────────────────────────────
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ═══════════════════════════════════════════════════════════════════════════
# KALSHI CLIENT
# ═══════════════════════════════════════════════════════════════════════════
class KalshiClient:
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
    PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, env="demo", key_id=None, private_key_path=None):
        assert env in ("demo", "prod")
        self.env = env
        self.base_url = self.DEMO_URL if env == "demo" else self.PROD_URL
        creds = {}
        try:
            creds = load_credentials()
        except Exception:
            pass

        # Same key for both demo and prod
        self.key_id = key_id or creds.get("API_KEY_ID_KALSHI")
        kp = private_key_path or creds.get("PRIVATE_KEY_PATH")

        self.private_key = None
        if kp:
            kp_path = Path(kp).expanduser()
            if kp_path.exists():
                with open(kp_path, "rb") as f:
                    self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        self.session = requests.Session()
        self._path_prefix = "/trade-api/v2"

    def _sign(self, method, path):
        if not self.private_key or not self.key_id:
            return {}
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split("?")[0]).encode("utf-8")
        sig = self.private_key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"), "KALSHI-ACCESS-TIMESTAMP": ts}

    def _get(self, path, params=None):
        r = self.session.get(self.base_url + path, headers=self._sign("GET", self._path_prefix + path), params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body):
        headers = self._sign("POST", self._path_prefix + path)
        headers["Content-Type"] = "application/json"
        r = self.session.post(self.base_url + path, headers=headers, json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_events(self, series_ticker=None, status="open", limit=200):
        params = {"status": status, "limit": limit}
        if series_ticker: params["series_ticker"] = series_ticker
        return self._get("/events", params)

    def get_markets(self, limit=100, auto_paginate=True, **kwargs):
        params = {"limit": limit}
        params.update(kwargs)
        data = self._get("/markets", params)
        markets = data.get("markets", [])
        cursor = data.get("cursor")
        while auto_paginate and cursor:
            params["cursor"] = cursor
            nxt = self._get("/markets", params)
            markets.extend(nxt.get("markets", []))
            cursor = nxt.get("cursor")
        return {"markets": markets}

    def get_market(self, ticker):
        return self._get(f"/markets/{ticker}")

    def get_balance(self):
        return self._get("/portfolio/balance")

    def place_order(
        self,
        ticker,
        side,
        action,
        count,
        type_="market",
        yes_price=None,
        no_price=None,
        time_in_force="fill_or_kill",
    ):
        if action != "buy":
            raise ValueError("V2 place_order currently supports buy actions only.")
        if side == "yes":
            if yes_price is None:
                raise ValueError("yes_price is required to buy YES.")
            book_side = "bid"
            price = float(yes_price)
        elif side == "no":
            if no_price is None:
                raise ValueError("no_price is required to buy NO.")
            book_side = "ask"
            price = 1.0 - float(no_price)
        else:
            raise ValueError(f"Unsupported side: {side!r}")

        body = {
            "ticker": ticker,
            "side": book_side,
            "count": f"{int(count)}.00",
            "price": f"{price:.4f}",
            "client_order_id": f"bot-{int(time.time()*1000)}",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        if time_in_force:
            body["time_in_force"] = time_in_force
        return self._post("/portfolio/events/orders", body)

# ═══════════════════════════════════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════════════════════════════════
def fetch_coinbase_candles(product_id="BTC-USD", granularity=60, start=None, end=None):
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    params = {"granularity": granularity}
    if start: params["start"] = start.isoformat()
    if end: params["end"] = end.isoformat()
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=["time","low","high","open","close","volume"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.sort_values("time").reset_index(drop=True)

def fetch_historical_minutes(days_back=2):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    all_c = []
    cursor = start
    chunk = timedelta(minutes=300)
    while cursor < end:
        ce = min(cursor + chunk, end)
        try:
            all_c.append(fetch_coinbase_candles(start=cursor, end=ce))
            cursor = ce
            time.sleep(0.3)
        except Exception:
            time.sleep(2)
            cursor = ce
    if not all_c: return pd.DataFrame()
    df = pd.concat(all_c, ignore_index=True).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return df

def get_btc_spot():
    return float(requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10).json()["data"]["amount"])

def refresh_btc_data(btc_1m):
    """Incremental refresh: only fetch the last ~10 minutes of new bars, not 2 full days."""
    now = datetime.now(timezone.utc)
    # Only fetch from 10 minutes ago to now (covers the 2-min cycle with margin)
    start = now - timedelta(minutes=10)
    try:
        fresh = fetch_coinbase_candles(start=start, end=now)
    except Exception as e:
        print(f"  Refresh failed: {e}")
        return btc_1m
    if len(fresh) == 0:
        return btc_1m
        
    # Drop current incomplete minute to prevent jitter in RV/kurtosis
    current_min = now.replace(second=0, microsecond=0)
    fresh = fresh[fresh["time"] < current_min]
    
    fresh["log_ret"] = np.log(fresh["close"] / fresh["close"].shift(1))
    btc_1m = pd.concat([btc_1m, fresh]).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    btc_1m["log_ret"] = np.log(btc_1m["close"] / btc_1m["close"].shift(1))
    af = MINUTES_PER_YEAR
    btc_1m["rv_15m"]  = btc_1m["log_ret"].rolling(15).std()  * np.sqrt(af)
    btc_1m["rv_60m"]  = btc_1m["log_ret"].rolling(60).std()  * np.sqrt(af)
    btc_1m["rv_1d"]   = btc_1m["log_ret"].rolling(1440).std()* np.sqrt(af)
    btc_1m["rkurt_60m"] = btc_1m["log_ret"].rolling(60).kurt()
    return btc_1m

# ═══════════════════════════════════════════════════════════════════════════
# EMPIRICAL MODEL
# ═══════════════════════════════════════════════════════════════════════════
def build_emp(df, horizon_min, max_idx=None, n=5000, seed=0, demean=True):
    if max_idx is None: max_idx = len(df) - horizon_min
    rng = np.random.default_rng(seed)
    close = df["close"].to_numpy(); rv60 = df["rv_60m"].to_numpy()
    R, V = [], []
    for i in range(1440, max_idx - horizon_min):
        v = rv60[i]
        if not np.isfinite(v): continue
        s, e = close[i], close[i + horizon_min]
        if s <= 0 or e <= 0: continue
        R.append(np.log(e/s)); V.append(v)
    R, V = np.asarray(R), np.asarray(V)
    if len(R) > n:
        idx = rng.choice(len(R), n, replace=False); R, V = R[idx], V[idx]
    if demean and len(R) > 0: R = R - R.mean()
    return {"horizon_min": horizon_min, "log_returns": R, "starting_vols": V, "n": len(R)}

def build_live_cache(btc_1m, verbose=True):
    cache = {}
    for h in CFG["emp_horizons"]:
        s = build_emp(btc_1m, h, n=CFG["emp_n_samples"])
        cache[h] = s
        if verbose:
            print(f"  h={h:4d}m: {s['n']:,} samples")
    return cache

def emp_p_above(spot, strike, horizon_min, samples, current_vol=None, bw=0.30):
    R = samples["log_returns"].copy(); V = samples["starting_vols"]; h = samples["horizon_min"]
    if horizon_min != h and h > 0: R = R * np.sqrt(horizon_min / h)
    if current_vol is not None and len(V) > 100:
        k = max(200, int(len(V) * bw)); keep = np.argsort(np.abs(V - current_vol))[:k]; R = R[keep]
    if len(R) == 0: return 0.5
    return float((R > np.log(strike / spot)).mean())

def emp_p_bucket(spot, floor, cap, horizon_min, samples, current_vol=None, bw=0.30):
    R = samples["log_returns"].copy(); V = samples["starting_vols"]; h = samples["horizon_min"]
    if horizon_min != h and h > 0: R = R * np.sqrt(horizon_min / h)
    if current_vol is not None and len(V) > 100:
        k = max(200, int(len(V) * bw)); keep = np.argsort(np.abs(V - current_vol))[:k]; R = R[keep]
    if len(R) == 0: return 0.5
    future = spot * np.exp(R)
    return float(((future >= floor) & (future < cap)).mean())

def lognormal_p_above(spot, strike, ttl_min, annual_vol):
    if spot <= 0 or not annual_vol or not math.isfinite(annual_vol) or annual_vol <= 0:
        return np.nan
    variance = annual_vol * annual_vol * ttl_min / MINUTES_PER_YEAR
    if variance <= 0:
        return 1.0 if spot >= strike else 0.0
    sigma = math.sqrt(variance)
    z = (math.log(strike / spot) + 0.5 * variance) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2.0))

def blended_p_above(spot, strike, ttl_min, samples, current_vol):
    p_emp = emp_p_above(spot, strike, ttl_min, samples, current_vol=current_vol)
    p_norm = lognormal_p_above(spot, strike, ttl_min, current_vol)
    if np.isfinite(p_norm):
        return 0.70 * p_emp + 0.30 * p_norm
    return p_emp

# ═══════════════════════════════════════════════════════════════════════════
# MARKET PARSING + SCANNING
# ═══════════════════════════════════════════════════════════════════════════
def fdollar(m, key):
    v = m.get(key)
    if v is None or v == '': return None
    try: return float(v)
    except: return None

def parse_market(m):
    return {"ticker": m.get("ticker"), "floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
            "strike_type": m.get("strike_type"), 
            "yes_bid": fdollar(m,"yes_bid_dollars"), "yes_ask": fdollar(m,"yes_ask_dollars"),
            "no_bid": fdollar(m,"no_bid_dollars"), "no_ask": fdollar(m,"no_ask_dollars"),
            "open_interest": fdollar(m,"open_interest_fp"),
            "volume_24h": fdollar(m,"volume_24h_fp"), "status": m.get("status"), "close_time": m.get("close_time")}

def scan_btc_events(kalshi_prod):
    now = datetime.now(timezone.utc)
    all_mkts = []
    # Fetch all markets for the series in a single highly-efficient API call
    for series in CFG["event_whitelist"]:
        try:
            mkts = kalshi_prod.get_markets(series_ticker=series, status="open").get("markets", [])
            for m in mkts:
                m["_series"] = series
            all_mkts.extend(mkts)
        except Exception as ex:
            print(f"  market list {series} error: {ex}")
            
    # Group markets by event
    events_map = {}
    for m in all_mkts:
        ev = m.get("event_ticker")
        if not ev: continue
        events_map.setdefault(ev, []).append(m)

    viable = []
    for ev_ticker, mkts in events_map.items():
        try:
            closes = [dtparser.isoparse(m["close_time"]) for m in mkts if m.get("close_time")]
            if not closes: continue
            earliest = min(closes)
            ttl_min = (earliest - now).total_seconds() / 60
            if not (CFG["min_ttl_min"] < ttl_min < CFG["max_ttl_hours"] * 60): continue
            
            viable.append({"event_ticker": ev_ticker, "title": mkts[0].get("title", ""),
                           "close_time": earliest, "ttl_hours": ttl_min/60, "markets": mkts})
        except Exception as ex:
            print(f"  event {ev_ticker} error: {ex}")
    return sorted(viable, key=lambda x: x["ttl_hours"])

# ═══════════════════════════════════════════════════════════════════════════
# EDGE COMPUTATION + SIZING
# ═══════════════════════════════════════════════════════════════════════════
def compute_edges(event, btc_1m, emp_cache, calibrator, strategy="paper"):
    spot = get_btc_spot()
    df = pd.DataFrame([parse_market(m) for m in event["markets"]])
    quoted = df[df["yes_bid"].notna() & df["yes_ask"].notna()].copy()
    if len(quoted) == 0: return pd.DataFrame()

    ttl_min = event["ttl_hours"] * 60
    cur_v = float(btc_1m["rv_60m"].dropna().iloc[-1]) if "rv_60m" in btc_1m.columns else None
    horizons = sorted(emp_cache.keys())
    h = min(horizons, key=lambda x: abs(x - ttl_min)) if horizons else 60
    samples = emp_cache.get(h)
    if samples is None: return pd.DataFrame()

    def cal_p(p):
        if calibrator is not None and np.isfinite(p):
            try: return float(calibrator.predict_proba(np.array([[p]]))[:, 1][0])
            except: pass
        return p

    probs = []
    for _, r in quoted.iterrows():
        tk = r.get("ticker","")
        mtype = "cumulative" if "-T" in tk else ("bucket" if "-B" in tk else "unknown")
        fl = r.get("floor"); cap = r.get("cap")
        if mtype == "cumulative" and fl is not None:
            if strategy == "research":
                p = blended_p_above(spot, fl, ttl_min, samples, cur_v)
            else:
                p = emp_p_above(spot, fl, ttl_min, samples, cur_v)
        elif mtype == "bucket" and fl is not None:
            cap_v = cap if cap and cap > fl else (fl + 100)
            p = emp_p_bucket(spot, fl, cap_v, ttl_min, samples, cur_v)
        else: p = np.nan
        probs.append(cal_p(p) if np.isfinite(p) else np.nan)

    quoted["model_p_yes"] = probs
    quoted = quoted.dropna(subset=["model_p_yes"])
    if len(quoted) == 0: return pd.DataFrame()

    cfg = CFG
    quoted["edge_buy_yes"] = quoted["model_p_yes"] - quoted["yes_ask"]
    quoted["edge_buy_no"]  = (1.0 - quoted["model_p_yes"]) - quoted["no_ask"]
    quoted["side"] = np.where(quoted["edge_buy_yes"] > quoted["edge_buy_no"], "yes", "no")
    quoted["entry_price"] = np.where(quoted["side"]=="yes", quoted["yes_ask"], quoted["no_ask"])
    quoted["entry_fee"] = quoted["entry_price"].apply(lambda p: kalshi_fee_dollars(p, contracts=1, liquidity="taker"))
    quoted["edge_gross_cents"] = np.where(quoted["side"]=="yes", quoted["edge_buy_yes"]*100, quoted["edge_buy_no"]*100)
    quoted["net_edge_cents"] = quoted["edge_gross_cents"] - quoted["entry_fee"] * 100.0
    quoted["spread_cents"] = np.where(quoted["side"]=="yes", (quoted["yes_ask"] - quoted["yes_bid"])*100, (quoted["no_ask"] - quoted["no_bid"])*100)
    quoted["moneyness"] = quoted["floor"] / spot
    return quoted

def tradeable_signals(edges, strategy="paper"):
    cfg = CFG
    if len(edges) == 0:
        return edges

    # Log what we see before filtering
    best = edges.nlargest(3, "net_edge_cents")
    for _, r in best.iterrows():
        print(f"    raw: {r.get('ticker','?'):35s} side={r.get('side','?'):3s} "
              f"edge={r['net_edge_cents']:+.1f}c spread={r.get('spread_cents',99):.0f}c "
              f"entry={r.get('entry_price',0):.2f} model_p={r.get('model_p_yes',0):.3f}")

    if strategy == "research":
        strong_prob = np.where(
            edges["side"] == "yes",
            edges["model_p_yes"] >= 0.65,
            edges["model_p_yes"] <= 0.35,
        )
        mask = (edges["net_edge_cents"] >= 12.0) & \
               strong_prob & \
               (edges["spread_cents"] <= 2.0) & \
               (edges["entry_price"] >= 0.25) & \
               (edges["entry_price"] <= 0.75)
    else:
        mask = (edges["net_edge_cents"] >= cfg["min_edge_cents"]) & \
               (edges["spread_cents"] <= cfg["max_spread_cents"]) & \
               (edges["entry_price"] >= cfg["min_entry_price"]) & \
               (edges["entry_price"] <= cfg["max_entry_price"])
    passed = edges[mask]
    if len(passed) == 0 and len(edges) > 0:
        # Show why the best candidate was rejected
        top = edges.iloc[edges["net_edge_cents"].argmax()]
        reasons = []
        min_edge = 12.0 if strategy == "research" else cfg["min_edge_cents"]
        max_spread = 2.0 if strategy == "research" else cfg["max_spread_cents"]
        min_entry = 0.25 if strategy == "research" else cfg["min_entry_price"]
        max_entry = 0.75 if strategy == "research" else cfg["max_entry_price"]
        if top["net_edge_cents"] < min_edge: reasons.append(f"edge {top['net_edge_cents']:.1f}<{min_edge}")
        if top["spread_cents"] > max_spread: reasons.append(f"spread {top['spread_cents']:.0f}>{max_spread}")
        if top["entry_price"] < min_entry: reasons.append(f"entry {top['entry_price']:.2f}<{min_entry}")
        if top["entry_price"] > max_entry: reasons.append(f"entry {top['entry_price']:.2f}>{max_entry}")
        if strategy == "research":
            strong = (top["side"] == "yes" and top["model_p_yes"] >= 0.65) or (
                top["side"] == "no" and top["model_p_yes"] <= 0.35
            )
            if not strong:
                reasons.append(f"prob {top['model_p_yes']:.3f} not strong")
        print(f"    REJECTED: {', '.join(reasons)}")
    passed = passed.sort_values("net_edge_cents", ascending=False)
    if strategy == "research":
        return passed.head(1)
    return passed

def kelly_size(edge_pct, entry_price, bankroll):
    if edge_pct <= 0 or entry_price <= 0 or entry_price >= 1: return 0
    p = entry_price + edge_pct
    q = 1 - p
    b = (1 - entry_price) / entry_price
    if b <= 0: return 0
    f = (p * b - q) / b
    f = max(0, f) * CFG["kelly_fraction"]
    bet = f * bankroll
    max_bet = bankroll * CFG["max_per_market"]
    return int(min(bet, max_bet) / entry_price) if entry_price > 0 else 0

# ═══════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════
def db_conn():
    path = get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, market_ticker TEXT, side TEXT,
        entry_price REAL, contracts INTEGER, timestamp_utc TEXT,
        btc_spot_entry REAL, predicted_edge_cents REAL, our_pred_p REAL,
        settle_price REAL, pnl_dollars REAL, settled INTEGER DEFAULT 0,
        entry_fee REAL DEFAULT 0.0,
        mode TEXT DEFAULT 'paper')""")
    try:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN entry_fee REAL DEFAULT 0.0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

def record_trades(signals, mode="paper", kalshi_trade=None):
    conn = db_conn()
    spot = get_btc_spot()

    # Dedup: never re-enter a ticker we already hold
    open_tickers = set(r["market_ticker"] for r in
        conn.execute("SELECT market_ticker FROM paper_trades WHERE settled=0").fetchall())

    recorded = 0
    for _, s in signals.iterrows():
        if s["ticker"] in open_tickers:
            print(f"    SKIP (already open): {s['ticker']}")
            continue

        contracts = 1  # always 1 contract at this bankroll size
        entry_fee = float(s.get("entry_fee", kalshi_fee_dollars(s["entry_price"], contracts=contracts, liquidity="taker")))
        trade_entry_price = float(s["entry_price"])

        if mode == "live" and kalshi_trade is not None:
            try:
                resp = kalshi_trade.place_order(
                    ticker=s["ticker"], side=s["side"], action="buy",
                    count=contracts, type_="limit",
                    yes_price=s["entry_price"] if s["side"] == "yes" else None,
                    no_price=s["entry_price"] if s["side"] == "no" else None,
                    time_in_force="fill_or_kill")
                order = resp.get("order", resp)
                order_id = order.get("order_id", "?")
                fill_count = float(order.get("fill_count_fp") or order.get("fill_count") or 0)
                if fill_count < contracts:
                    print(f"    >>> NOT FILLED: {s['ticker']} order_id={order_id} fill_count={fill_count:g}")
                    continue
                avg_fill = order.get("average_fill_price")
                if avg_fill is not None:
                    yes_price = float(avg_fill)
                    trade_entry_price = yes_price if s["side"] == "yes" else 1.0 - yes_price
                entry_fee = float(order.get("taker_fees_dollars") or entry_fee)
                if order.get("average_fee_paid") is not None:
                    entry_fee = float(order["average_fee_paid"]) * fill_count
                print(f"    >>> FILLED: {s['ticker']} {s['side'].upper()} x{contracts} "
                      f"LIMIT @ ${trade_entry_price:.2f} edge={s['net_edge_cents']:+.1f}c "
                      f"order_id={order_id}")
            except Exception as ex:
                print(f"    >>> ORDER FAILED: {s['ticker']} {ex}")
                continue
        else:
            print(f"    PAPER: {s['ticker']} {s['side'].upper()} x{contracts} "
                  f"@ ${s['entry_price']:.2f} edge={s['net_edge_cents']:+.1f}c")

        conn.execute(
            "INSERT INTO paper_trades (market_ticker,side,entry_price,contracts,"
            "timestamp_utc,btc_spot_entry,predicted_edge_cents,our_pred_p,settled,entry_fee,mode) "
            "VALUES (?,?,?,?,?,?,?,?,0,?,?)",
            (s["ticker"], s["side"], trade_entry_price, contracts,
             datetime.now(timezone.utc).isoformat(), spot,
             s["net_edge_cents"], s.get("model_p_yes", 0), entry_fee, mode))
        open_tickers.add(s["ticker"])
        recorded += 1

    conn.commit(); conn.close()
    return recorded

def check_settlements(kalshi_prod):
    conn = db_conn()
    rows = conn.execute("SELECT id, market_ticker, side, entry_price, contracts, COALESCE(entry_fee,0.0) AS entry_fee FROM paper_trades WHERE settled=0").fetchall()
    settled = 0
    for r in rows:
        try:
            m = kalshi_prod.get_market(r["market_ticker"]).get("market", {})
            status = m.get("status", "").lower()
            if status not in TERMINAL_STATUSES: continue
            result = m.get("result", "")
            if result == "yes": settle_p = 1.0
            elif result == "no": settle_p = 0.0
            else: continue
            if r["side"] == "yes": pnl = settle_p * r["contracts"] - r["entry_price"] * r["contracts"] - r["entry_fee"]
            else: pnl = (1.0 - settle_p) * r["contracts"] - r["entry_price"] * r["contracts"] - r["entry_fee"]
            conn.execute("UPDATE paper_trades SET settled=1, settle_price=?, pnl_dollars=? WHERE id=?", (settle_p, pnl, r["id"]))
            settled += 1
        except: pass
    conn.commit(); conn.close()
    return settled

def portfolio_report():
    conn = db_conn()
    trades = pd.read_sql("SELECT * FROM paper_trades", conn)
    conn.close()
    if len(trades) == 0:
        print("  No trades recorded yet.")
        return
    settled = trades[trades["settled"]==1]
    total_pnl = settled["pnl_dollars"].sum() if len(settled) else 0
    equity = CFG["bankroll"] + total_pnl
    open_n = len(trades[trades["settled"]==0])
    print(f"  Equity: ${equity:.2f} | PnL: ${total_pnl:+.2f} | Open: {open_n} | Settled: {len(settled)}")
    if len(settled) > 0:
        wins = settled[settled["pnl_dollars"] > 0]
        print(f"  Win rate: {len(wins)}/{len(settled)} ({len(wins)/len(settled)*100:.1f}%)")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════
def run_cycle(btc_1m, emp_cache, last_cache_time, calibrator, kalshi_prod, kalshi_trade, mode, strategy):
    print(f"\n{'='*60}")
    print(f"  SCAN CYCLE @ {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")

    # 1. Refresh BTC data
    print("[1/6] Refreshing BTC data...")
    btc_1m = refresh_btc_data(btc_1m)
    print(f"  {len(btc_1m):,} bars (latest {btc_1m['time'].iloc[-1]})")

    # 1.5. Rebuild cache
    if time.time() - last_cache_time > 300:
        print("[1.5/6] Rebuilding empirical cache...")
        emp_cache = build_live_cache(btc_1m, verbose=False)
        last_cache_time = time.time()

    # 2. Check settlements
    print("[2/6] Checking settlements...")
    n_settled = check_settlements(kalshi_prod)
    print(f"  Settled: {n_settled}")

    # 3. Scan for events
    print("[3/6] Scanning events...")
    events = scan_btc_events(kalshi_prod)
    print(f"  Found {len(events)} candidate events")

    # 4. Compute edges
    print("[4/6] Computing edges...")
    all_signals = []
    for e in events[:15]:
        try:
            edges = compute_edges(e, btc_1m, emp_cache, calibrator, strategy=strategy)
            signals = tradeable_signals(edges, strategy=strategy)
            if len(signals) > 0:
                signals = signals.copy()
                signals["event"] = e["event_ticker"]
                all_signals.append(signals)
                print(f"  {e['event_ticker']:30s} ttl={e['ttl_hours']:.1f}h signals={len(signals)}")
        except Exception as ex:
            print(f"  {e['event_ticker']} error: {ex}")

    # 5. Record/place trades
    print("[5/6] Recording trades...")
    if all_signals:
        combined = pd.concat(all_signals, ignore_index=True)
        max_signals = 2 if strategy == "research" else CFG["max_concurrent_signals"]
        combined = combined.sort_values("net_edge_cents", ascending=False).head(max_signals)
        n = record_trades(combined, mode=mode, kalshi_trade=kalshi_trade)
        print(f"  Recorded {n} trades (mode={mode})")
    else:
        print("  No signals passed filters")

    # 6. Portfolio report
    print("[6/6] Portfolio:")
    portfolio_report()

    return btc_1m, emp_cache, last_cache_time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=CFG["mode"], choices=["paper","live"])
    parser.add_argument("--strategy", default="paper", choices=["paper","research"])
    args = parser.parse_args()
    mode = args.mode
    strategy = args.strategy

    if mode == "live" and not CFG["i_acknowledge_real_money_risk"]:
        print("ERROR: Set i_acknowledge_real_money_risk=True in config to trade live.")
        sys.exit(1)

    print("="*60)
    print(f"  KALSHI BTC 1HR BOT | mode={mode} | strategy={strategy} | bankroll=${CFG['bankroll']}")
    print(f"  Scan interval: {CFG['loop_interval_sec']}s | Kelly: {CFG['kelly_fraction']}")
    print("="*60)

    # Init clients (authenticated to get 10x higher rate limits on prod data)
    kalshi_prod = KalshiClient(env="prod")
    env = "prod" if mode == "live" else "demo"
    kalshi_trade = KalshiClient(env=env)

    # Init data
    print(f"\nFetching initial BTC data ({CFG['btc_data_days']} days)...")
    btc_1m = fetch_historical_minutes(days_back=CFG["btc_data_days"])
    btc_1m["log_ret"] = np.log(btc_1m["close"] / btc_1m["close"].shift(1))
    btc_1m = refresh_btc_data(btc_1m)
    print(f"  {len(btc_1m):,} bars loaded")

    # Build empirical cache
    print("\nBuilding empirical sample cache...")
    emp_cache = build_live_cache(btc_1m, verbose=True)
    last_cache_time = time.time()

    # No calibrator for now (simple mode)
    calibrator = None

    # Main loop
    print(f"\nStarting scan loop (every {CFG['loop_interval_sec']}s)...")
    while True:
        try:
            btc_1m, emp_cache, last_cache_time = run_cycle(
                btc_1m, emp_cache, last_cache_time, calibrator, kalshi_prod, kalshi_trade, mode, strategy)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as ex:
            print(f"\nCycle error: {ex}")
        print(f"\nSleeping {CFG['loop_interval_sec']}s...")
        time.sleep(CFG["loop_interval_sec"])

if __name__ == "__main__":
    main()
