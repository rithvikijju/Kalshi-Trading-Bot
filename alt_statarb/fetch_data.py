"""Fetch 1h bars for top-20 liquid crypto pairs on Coinbase via ccxt.

Goal: find cointegrated alt-pairs with wider spread variance than BTC/ETH,
where mean-reversion edge can survive retail trading costs.
"""
import time, sys
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import duckdb
import ccxt

DATA = Path('alt_statarb/data'); DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'alts_1h.duckdb'

# Top liquid USD pairs on Coinbase as of 2026 (post-MATIC→POL rebrand)
COINS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK',
    'DOT', 'POL', 'LTC', 'BCH', 'ATOM', 'UNI', 'XLM',
    'NEAR', 'AAVE', 'SUI', 'APT', 'INJ',
]

def fetch_one(symbol, months=6, max_errors=3):
    cb = ccxt.coinbase({'enableRateLimit': True, 'timeout': 15000})
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - months * 30 * 24 * 60 * 60 * 1000
    all_bars, cursor = [], start_ms
    err_count = 0
    while cursor < end_ms and err_count < max_errors:
        try:
            bars = cb.fetch_ohlcv(symbol, timeframe='1h', since=cursor, limit=300)
        except Exception as e:
            err_count += 1
            print(f' err({err_count}): {str(e)[:60]}', end='', flush=True)
            if err_count >= max_errors: break
            time.sleep(1); continue
        if not bars: break
        all_bars.extend(bars)
        last = bars[-1][0]
        if last <= cursor: break
        cursor = last + 60 * 60 * 1000
    df = pd.DataFrame(all_bars, columns=['ts_ms','open','high','low','close','volume'])
    df = df.drop_duplicates(subset='ts_ms').sort_values('ts_ms').reset_index(drop=True)
    df['ts'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
    return df

def main():
    if DB_PATH.exists(): DB_PATH.unlink()
    db = duckdb.connect(str(DB_PATH))
    got = 0
    for coin in COINS:
        sym = f'{coin}/USD'
        print(f'  {sym}...', end=' ', flush=True)
        try:
            df = fetch_one(sym, months=6)
        except Exception as e:
            print(f'FAIL: {e}'); continue
        if df.empty:
            print('empty'); continue
        table = f'c_{coin.replace("-","_")}'
        db.execute(f'CREATE TABLE {table} AS SELECT * FROM df')
        print(f'{len(df):,} bars  ({df["ts"].min().date()} → {df["ts"].max().date()})')
        got += 1
    db.close()
    print(f'\nDone. {got}/{len(COINS)} coins, DB at {DB_PATH}')

if __name__ == '__main__':
    main()
