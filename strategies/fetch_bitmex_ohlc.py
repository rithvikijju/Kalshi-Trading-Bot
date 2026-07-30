"""Fetch BitMEX daily OHLC for XBTUSD perp + BXBT spot index, same for ETH.

Needed to compute real basis = perp_close - spot_close per day, which is the
dominant source of daily PnL variance for a funding-arb position.
"""
import duckdb, time, requests
from pathlib import Path
import pandas as pd

DATA = Path('strategies/data')
db = duckdb.connect(str(DATA / 'crypto_long.duckdb'))

BASE = 'https://www.bitmex.com/api/v1'

def fetch_ohlc(symbol):
    """Daily OHLC. BitMEX max 1000/call. Paginate forward via startTime."""
    start = '2016-05-01T00:00:00.000Z'
    all_rows = []
    while True:
        params = {'symbol': symbol, 'binSize': '1d', 'partial': 'false',
                  'count': 1000, 'startTime': start, 'reverse': 'false'}
        try:
            r = requests.get(BASE + '/trade/bucketed', params=params, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            print(f'  ! err {symbol} @{start}: {e}'); break
        if not batch: break
        all_rows.extend(batch)
        newest = batch[-1]['timestamp']
        if newest == start: break
        start = pd.to_datetime(newest).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        if pd.to_datetime(newest) > pd.Timestamp.now(tz='UTC'): break
        time.sleep(1.2)
        if len(all_rows) % 1000 == 0:
            print(f'    {symbol}: {len(all_rows):,} rows ({newest})')
    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        for c in ('open','high','low','close'):
            df[c] = df[c].astype(float)
    return df

for sym, table in [('XBTUSD', 'perp_XBTUSD'),
                    ('.BXBT', 'spot_XBT_index'),
                    ('ETHUSD', 'perp_ETHUSD'),
                    ('.BETH', 'spot_ETH_index')]:
    print(f'\n── {sym} ──')
    df = fetch_ohlc(sym)
    if df.empty:
        print('  no data'); continue
    print(f'  {len(df):,} rows  {df["timestamp"].min()} → {df["timestamp"].max()}')
    db.execute(f'CREATE OR REPLACE TABLE {table} AS SELECT * FROM df')

db.close()
print('\nDone.')
