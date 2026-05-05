import sys, os, time, base64, sqlite3, json
from pathlib import Path
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from datetime import datetime
from dateutil import parser as dtparser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.btc_1hr_config import load_credentials

class KalshiClient:
    def __init__(self):
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        creds = load_credentials()
        self.key_id = creds.get("API_KEY_ID_KALSHI")
        kp = creds.get("PRIVATE_KEY_PATH")
        kp_path = Path(kp).expanduser()
        with open(kp_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        self.session = requests.Session()
        self._path_prefix = "/trade-api/v2"

    def _sign(self, method, path):
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split("?")[0]).encode("utf-8")
        sig = self.private_key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"), "KALSHI-ACCESS-TIMESTAMP": ts}

    def _get(self, path, params=None):
        retries = 3
        for i in range(retries):
            try:
                r = self.session.get(self.base_url + path, headers=self._sign("GET", self._path_prefix + path), params=params, timeout=15)
                if r.status_code == 429:
                    print("Rate limited. Sleeping...")
                    time.sleep(2)
                    continue
                if r.status_code != 200:
                    print(f"Error {r.status_code}: {r.text} for {path}")
                    return None
                return r.json()
            except requests.exceptions.RequestException as e:
                print(f"Request exception: {e}")
                time.sleep(2)
        return None

def setup_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            ticker TEXT PRIMARY KEY,
            series_ticker TEXT,
            event_ticker TEXT,
            open_time TEXT,
            close_time TEXT,
            floor_strike REAL,
            cap_strike REAL,
            status TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candlesticks (
            market_ticker TEXT,
            end_period_ts INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            open_interest INTEGER,
            yes_bid REAL,
            yes_ask REAL,
            price REAL,
            PRIMARY KEY (market_ticker, end_period_ts)
        )
    """)
    conn.commit()
    return conn

def download_historical_data():
    db_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data"
    db_dir.mkdir(exist_ok=True)
    db_path = db_dir / "kalshi_history.db"
    print(f"Database path: {db_path}")
    
    conn = setup_db(db_path)
    client = KalshiClient()
    
    # 1. Fetch all historical markets
    market_count = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    if market_count < 1000000:
        for series in ["KXBTC", "KXBTCD"]:
            print(f"Fetching historical markets for {series}...")
            cursor = None
            while True:
                params = {"series_ticker": series, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                    
                res = client._get("/historical/markets", params=params)
                if not res or "markets" not in res:
                    break
                    
                markets = res["markets"]
                if not markets:
                    break
                    
                for m in markets:
                    conn.execute(
                        "INSERT OR IGNORE INTO markets (ticker, series_ticker, event_ticker, open_time, close_time, floor_strike, cap_strike, status) VALUES (?,?,?,?,?,?,?,?)",
                        (m["ticker"], m.get("series_ticker"), m.get("event_ticker"), m.get("open_time"), m.get("close_time"), m.get("floor_strike"), m.get("cap_strike"), m.get("status"))
                    )
                conn.commit()
                
                print(f"  Inserted {len(markets)} markets...")
                cursor = res.get("cursor")
                if not cursor:
                    break
                
    # 2. Fetch candlesticks for each market
    markets_to_fetch = conn.execute("SELECT ticker, open_time, close_time FROM markets WHERE ticker NOT IN (SELECT DISTINCT market_ticker FROM candlesticks) ORDER BY close_time DESC").fetchall()
    print(f"Need to fetch candlesticks for {len(markets_to_fetch)} markets.")
    
    count = 0
    for tk, o_time, c_time in markets_to_fetch:
        open_ts = int(dtparser.isoparse(o_time).timestamp()) if o_time else 0
        close_ts = int(dtparser.isoparse(c_time).timestamp()) if c_time else int(time.time())
        
        c_res = client._get(f"/historical/markets/{tk}/candlesticks", params={
            "period_interval": 1,
            "start_ts": open_ts,
            "end_ts": close_ts + 3600
        })
        
        if c_res and "candlesticks" in c_res:
            candles = c_res["candlesticks"]
            for c in candles:
                vals = [tk, c.get("end_period_ts")]
                for k in ["open", "high", "low", "close", "volume", "open_interest", "yes_bid", "yes_ask", "price"]:
                    v = c.get(k)
                    vals.append(json.dumps(v) if isinstance(v, dict) else v)
                
                conn.execute(
                    "INSERT OR IGNORE INTO candlesticks (market_ticker, end_period_ts, open, high, low, close, volume, open_interest, yes_bid, yes_ask, price) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(vals)
                )
            conn.commit()
            print(f"[{count+1}/{len(markets_to_fetch)}] Fetched {len(candles)} candles for {tk}")
        else:
            # Insert a dummy record to mark as fetched even if 0 candles to avoid retrying endlessly
            conn.execute("INSERT OR IGNORE INTO candlesticks (market_ticker, end_period_ts) VALUES (?,?)", (tk, 0))
            conn.commit()
            print(f"[{count+1}/{len(markets_to_fetch)}] No candles found for {tk}")
            
        count += 1
        time.sleep(0.1) # Respect rate limits

if __name__ == "__main__":
    download_historical_data()
    print("Done!")
