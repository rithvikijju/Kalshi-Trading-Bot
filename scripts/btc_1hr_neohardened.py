#!/usr/bin/env python3
"""
Paper-only overnight scanner for Kalshi BTC markets.

Identical model to btc_1hr_live.py but:
  - NEVER places real orders (hardcoded paper mode)
  - Uses a separate DB (paper_draft_trades.db) so live DB is untouched
  - Separate log file (btc_1hr_paper_*.log)
  - Safe to leave running overnight

Usage:
    python scripts/btc_1hr_paper.py
"""

import sys, os, time, json, sqlite3, base64, logging
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.btc_1hr_config import CFG, MINUTES_PER_YEAR, TERMINAL_STATUSES, load_credentials

# ── Override DB to separate paper DB ──────────────────────────────────────
PAPER_DB_DIR = Path.home() / ".btc_kalshi_bot"
PAPER_DB_DIR.mkdir(parents=True, exist_ok=True)
PAPER_DB_PATH = str(PAPER_DB_DIR / "neohardened_trades.db")

# ── Hardened Config Overrides ──────────────────────────────────────────────
CFG["min_edge_cents"] = 7.0        # raised from 4.0; 4-6c bucket was -$6.57 on 51 trades
CFG["max_spread_cents"] = 3        # ensure tight book
CFG["min_entry_price"] = 0.20      # widen from 0.40 to capture cheap out-of-the-money
CFG["max_entry_price"] = 0.80      # widen from 0.60 to capture deep in-the-money

# ── Logging setup ──────────────────────────────────────────────────────────
_LOG_DIR = Path(_PROJECT_ROOT) / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / f"btc_1hr_neohardened_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(_LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("btc_1hr_paper")

# ── Crypto auth ────────────────────────────────────────────────────────────
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ═══════════════════════════════════════════════════════════════════════════
# KALSHI CLIENT (read-only for paper mode)
# ═══════════════════════════════════════════════════════════════════════════
class KalshiClient:
    PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, key_id=None, private_key_path=None):
        self.base_url = self.PROD_URL
        creds = {}
        try:
            creds = load_credentials()
        except Exception:
            pass
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


# ═══════════════════════════════════════════════════════════════════════════
# DATA (identical to live script)
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
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        fresh = fetch_coinbase_candles(start=start, end=now)
    except Exception as e:
        print(f"  Refresh failed: {e}")
        return btc_1m
    if len(fresh) == 0:
        return btc_1m
    current_min = now.replace(second=0, microsecond=0)
    fresh = fresh[fresh["time"] < current_min]
    fresh["log_ret"] = np.log(fresh["close"] / fresh["close"].shift(1))
    btc_1m = pd.concat([btc_1m, fresh]).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    btc_1m["log_ret"] = np.log(btc_1m["close"] / btc_1m["close"].shift(1))
    af = MINUTES_PER_YEAR
    btc_1m["rv_15m"]  = btc_1m["log_ret"].rolling(15).std()  * np.sqrt(af)
    # Yang-Zhang 60m volatility
    log_ho = np.log(btc_1m["high"] / btc_1m["open"])
    log_lo = np.log(btc_1m["low"] / btc_1m["open"])
    log_co = np.log(btc_1m["close"] / btc_1m["open"])
    log_oc = np.log(btc_1m["open"] / btc_1m["close"].shift(1))
    close_vol = log_oc.rolling(60).var()
    open_vol = log_co.rolling(60).var()
    rs_vol = (log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)).rolling(60).mean()
    k = 0.34 / (1.34 + 61 / 59)
    btc_1m["rv_60m"] = np.sqrt((close_vol + k * open_vol + (1 - k) * rs_vol) * af)
    btc_1m["rv_1d"]   = btc_1m["log_ret"].rolling(1440).std()* np.sqrt(af)
    btc_1m["rkurt_60m"] = btc_1m["log_ret"].rolling(60).kurt()
    return btc_1m


# ═══════════════════════════════════════════════════════════════════════════
# EMPIRICAL MODEL (identical to live script)
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

def emp_p_above(spot, strike, horizon_min, samples, current_vol=None, bw=0.30, brti_dampening=1.0):
    R = samples["log_returns"].copy() * brti_dampening; V = samples["starting_vols"]; h = samples["horizon_min"]
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


# ═══════════════════════════════════════════════════════════════════════════
# MARKET PARSING + SCANNING (identical to live script)
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

def scan_btc_events(kalshi):
    now = datetime.now(timezone.utc)
    all_mkts = []
    for series in CFG["event_whitelist"]:
        try:
            mkts = kalshi.get_markets(series_ticker=series, status="open").get("markets", [])
            for m in mkts:
                m["_series"] = series
            all_mkts.extend(mkts)
        except Exception as ex:
            print(f"  market list {series} error: {ex}")
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
# EDGE COMPUTATION (identical to live script)
# ═══════════════════════════════════════════════════════════════════════════
def compute_edges(event, btc_1m, emp_cache):
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
    probs = []
    for _, r in quoted.iterrows():
        tk = r.get("ticker","")
        mtype = "cumulative" if "-T" in tk else ("bucket" if "-B" in tk else "unknown")
        fl = r.get("floor"); cap = r.get("cap")
        if mtype == "cumulative" and fl is not None:
            p = emp_p_above(spot, fl, ttl_min, samples, cur_v, brti_dampening=0.65)
        elif mtype == "bucket" and fl is not None:
            cap_v = cap if cap and cap > fl else (fl + 100)
            p = emp_p_bucket(spot, fl, cap_v, ttl_min, samples, cur_v)
        else: p = np.nan
        probs.append(p if np.isfinite(p) else np.nan)
    quoted["model_p_yes"] = probs
    quoted = quoted.dropna(subset=["model_p_yes"])
    if len(quoted) == 0: return pd.DataFrame()
    cfg = CFG
    quoted["yes_mid"] = (quoted["yes_bid"] + quoted["yes_ask"]) / 2.0
    quoted["no_mid"] = (quoted["no_bid"] + quoted["no_ask"]) / 2.0
    quoted["edge_buy_yes"] = quoted["model_p_yes"] - quoted["yes_mid"]
    quoted["edge_buy_no"]  = (1.0 - quoted["model_p_yes"]) - quoted["no_mid"]
    quoted["side"] = np.where(quoted["edge_buy_yes"] > quoted["edge_buy_no"], "yes", "no")
    quoted["edge_gross_cents"] = np.where(quoted["side"]=="yes", quoted["edge_buy_yes"]*100, quoted["edge_buy_no"]*100)
    quoted["net_edge_cents"] = quoted["edge_gross_cents"] - cfg["kalshi_fee_cents"]
    quoted["spread_cents"] = np.where(quoted["side"]=="yes", (quoted["yes_ask"] - quoted["yes_bid"])*100, (quoted["no_ask"] - quoted["no_bid"])*100)
    quoted["moneyness"] = quoted["floor"] / spot
    quoted["entry_price"] = np.where(quoted["side"]=="yes", quoted["yes_mid"], quoted["no_mid"])
    return quoted

def tradeable_signals(edges, btc_1m):
    cfg = CFG
    if len(edges) == 0: return edges
    best = edges.nlargest(3, "net_edge_cents")
    for _, r in best.iterrows():
        print(f"    raw: {r.get('ticker','?'):35s} side={r.get('side','?'):3s} "
              f"edge={r['net_edge_cents']:+.1f}c spread={r.get('spread_cents',99):.0f}c "
              f"entry={r.get('entry_price',0):.2f} model_p={r.get('model_p_yes',0):.3f}")
              
    rv60_cur = float(btc_1m["rv_60m"].dropna().iloc[-1]) if "rv_60m" in btc_1m.columns else 0.50
    if rv60_cur > 0.80: dyn_edge = 5.0
    elif rv60_cur < 0.40: dyn_edge = 10.0
    else: dyn_edge = 7.0

    is_cumulative = edges["ticker"].str.contains("-T", na=False)
    cum_min_edge = max(dyn_edge, 10.0)
    edge_threshold = np.where(is_cumulative, cum_min_edge, dyn_edge)
    
    coinflip_mask = (edges["model_p_yes"] >= 0.45) & (edges["model_p_yes"] <= 0.55)

    mask = (edges["net_edge_cents"] >= edge_threshold) & \
           (~coinflip_mask) & \
           (edges["spread_cents"] <= cfg["max_spread_cents"]) & \
           (edges["entry_price"] >= cfg["min_entry_price"]) & \
           (edges["entry_price"] <= cfg["max_entry_price"])
    passed = edges[mask]
    if len(passed) == 0 and len(edges) > 0:
        top = edges.iloc[edges["net_edge_cents"].argmax()]
        reasons = []
        top_thresh = cum_min_edge if "-T" in str(top.get("ticker","")) else cfg["min_edge_cents"]
        if top["net_edge_cents"] < top_thresh: reasons.append(f"edge {top['net_edge_cents']:.1f}<{top_thresh}")
        if top["spread_cents"] > cfg["max_spread_cents"]: reasons.append(f"spread {top['spread_cents']:.0f}>{cfg['max_spread_cents']}")
        if top["entry_price"] < cfg["min_entry_price"]: reasons.append(f"entry {top['entry_price']:.2f}<{cfg['min_entry_price']}")
        if top["entry_price"] > cfg["max_entry_price"]: reasons.append(f"entry {top['entry_price']:.2f}>{cfg['max_entry_price']}")
        print(f"    REJECTED: {', '.join(reasons)}")
    return passed.sort_values("net_edge_cents", ascending=False)


# ═══════════════════════════════════════════════════════════════════════════
# PAPER DATABASE (separate from live)
# ═══════════════════════════════════════════════════════════════════════════
def db_conn():
    conn = sqlite3.connect(PAPER_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, market_ticker TEXT, side TEXT,
        entry_price REAL, contracts INTEGER, timestamp_utc TEXT,
        btc_spot_entry REAL, predicted_edge_cents REAL, our_pred_p REAL,
        settle_price REAL, pnl_dollars REAL, settled INTEGER DEFAULT 0)""")
    conn.commit()
    return conn

def record_trades(signals):
    conn = db_conn()
    spot = get_btc_spot()

    # ── Loss streak cooldown (notebook §11) ─────────────────────────────
    recent_settled = conn.execute(
        "SELECT pnl_dollars FROM paper_trades WHERE settled=1 ORDER BY id DESC LIMIT 3"
    ).fetchall()
    if len(recent_settled) >= 3 and all(r["pnl_dollars"] <= 0 for r in recent_settled):
        last_loss = conn.execute(
            "SELECT timestamp_utc FROM paper_trades WHERE settled=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_loss:
            try:
                loss_time = dtparser.isoparse(last_loss["timestamp_utc"])
                cooldown_min = 30
                if (datetime.now(timezone.utc) - loss_time).total_seconds() < cooldown_min * 60:
                    print(f"    COOLDOWN: 3 consecutive losses, pausing {cooldown_min}min")
                    conn.close()
                    return 0
            except Exception:
                pass

    open_tickers = set(r["market_ticker"] for r in
        conn.execute("SELECT market_ticker FROM paper_trades WHERE settled=0").fetchall())
    recorded = 0
    event_counts = {}
    for _, s in signals.iterrows():
        ev = s.get("event", "")
        if event_counts.get(ev, 0) >= 2:
            print(f"    SKIP (event cap): {s['ticker']}")
            continue
        if s["ticker"] in open_tickers:
            print(f"    SKIP (already open): {s['ticker']}")
            continue
        event_counts[ev] = event_counts.get(ev, 0) + 1
        contracts = 1
        print(f"    PAPER: {s['ticker']} {s['side'].upper()} x{contracts} "
              f"@ ${s['entry_price']:.2f} edge={s['net_edge_cents']:+.1f}c")
        conn.execute(
            "INSERT INTO paper_trades (market_ticker,side,entry_price,contracts,"
            "timestamp_utc,btc_spot_entry,predicted_edge_cents,our_pred_p,settled) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            (s["ticker"], s["side"], s["entry_price"], contracts,
             datetime.now(timezone.utc).isoformat(), spot,
             s["net_edge_cents"], s.get("model_p_yes", 0)))
        open_tickers.add(s["ticker"])
        recorded += 1
    conn.commit(); conn.close()
    return recorded

def check_settlements(kalshi):
    conn = db_conn()
    rows = conn.execute("SELECT id, market_ticker, side, entry_price, contracts FROM paper_trades WHERE settled=0").fetchall()
    settled = 0
    for r in rows:
        try:
            m = kalshi.get_market(r["market_ticker"]).get("market", {})
            status = m.get("status", "").lower()
            if status not in TERMINAL_STATUSES: continue
            result = m.get("result", "")
            if result == "yes": settle_p = 1.0
            elif result == "no": settle_p = 0.0
            else: continue
            fee = CFG["kalshi_fee_cents"] / 100.0
            if r["side"] == "yes": pnl = (settle_p - r["entry_price"] - fee) * r["contracts"]
            else: pnl = ((1.0 - settle_p) - r["entry_price"] - fee) * r["contracts"]
            conn.execute("UPDATE paper_trades SET settled=1, settle_price=?, pnl_dollars=? WHERE id=?", (settle_p, pnl, r["id"]))
            settled += 1
            print(f"    SETTLED: {r['market_ticker']} {r['side']} -> {result} pnl=${pnl:+.3f}")
        except Exception as ex:
            pass
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
        print(f"  Avg win: ${settled[settled['pnl_dollars']>0]['pnl_dollars'].mean():.3f}" if len(wins) else "")
        losses = settled[settled["pnl_dollars"] <= 0]
        print(f"  Avg loss: ${losses['pnl_dollars'].mean():.3f}" if len(losses) else "")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════
def run_cycle(btc_1m, emp_cache, last_cache_time, kalshi):
    print(f"\n{'='*60}")
    print(f"  PAPER SCAN @ {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")

    print("[1/6] Refreshing BTC data...")
    btc_1m = refresh_btc_data(btc_1m)
    print(f"  {len(btc_1m):,} bars (latest {btc_1m['time'].iloc[-1]})")

    if time.time() - last_cache_time > 300:
        print("[1.5/6] Rebuilding empirical cache...")
        emp_cache = build_live_cache(btc_1m, verbose=False)
        last_cache_time = time.time()

    print("[2/6] Checking settlements...")
    n_settled = check_settlements(kalshi)
    print(f"  Settled: {n_settled}")

    print("[3/6] Scanning events...")
    events = scan_btc_events(kalshi)
    print(f"  Found {len(events)} candidate events")

    print("[4/6] Computing edges...")
    all_signals = []
    for e in events[:15]:
        try:
            edges = compute_edges(e, btc_1m, emp_cache)
            signals = tradeable_signals(edges, btc_1m)
            if len(signals) > 0:
                signals = signals.copy()
                signals["event"] = e["event_ticker"]
                all_signals.append(signals)
                print(f"  {e['event_ticker']:30s} ttl={e['ttl_hours']:.1f}h signals={len(signals)}")
        except Exception as ex:
            print(f"  {e['event_ticker']} error: {ex}")

    print("[5/6] Recording paper trades...")
    if all_signals:
        combined = pd.concat(all_signals, ignore_index=True)
        combined = combined.sort_values("net_edge_cents", ascending=False).head(CFG["max_concurrent_signals"])
        n = record_trades(combined)
        print(f"  Recorded {n} paper trades")
    else:
        print("  No signals passed filters")

    print("[6/6] Portfolio:")
    portfolio_report()

    return btc_1m, emp_cache, last_cache_time


def main():
    print("="*60)
    print(f"  KALSHI BTC PAPER SCANNER | bankroll=${CFG['bankroll']}")
    print(f"  DB: {PAPER_DB_PATH}")
    print(f"  Log: {_LOG_FILE}")
    print(f"  Scan interval: {CFG['loop_interval_sec']}s")
    print("="*60)

    kalshi = KalshiClient()

    print(f"\nFetching initial BTC data ({CFG['btc_data_days']} days)...")
    btc_1m = fetch_historical_minutes(days_back=CFG["btc_data_days"])
    btc_1m["log_ret"] = np.log(btc_1m["close"] / btc_1m["close"].shift(1))
    btc_1m = refresh_btc_data(btc_1m)
    print(f"  {len(btc_1m):,} bars loaded")

    print("\nBuilding empirical sample cache...")
    emp_cache = build_live_cache(btc_1m, verbose=True)
    last_cache_time = time.time()

    print(f"\nStarting paper scan loop (every {CFG['loop_interval_sec']}s)...")
    while True:
        try:
            btc_1m, emp_cache, last_cache_time = run_cycle(btc_1m, emp_cache, last_cache_time, kalshi)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as ex:
            print(f"\nCycle error: {ex}")
        print(f"\nSleeping {CFG['loop_interval_sec']}s...")
        time.sleep(CFG["loop_interval_sec"])

if __name__ == "__main__":
    main()
