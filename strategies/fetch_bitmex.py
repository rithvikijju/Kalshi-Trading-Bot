"""Fetch BitMEX funding rate history for XBTUSD + ETHUSD.

BitMEX is one of the original perpetual exchanges (since 2016). Their public
funding API gives full history. We'll use this for a robust long-horizon
backtest of the funding arb strategy.

XBTUSD funding occurs every 8 hours. Inverse perpetual (USD-margined), but
funding mechanics are the same as USDT perps on Binance/OKX.
"""
import duckdb, time, requests, sys
from pathlib import Path
import pandas as pd

DATA = Path('strategies/data'); DATA.mkdir(parents=True, exist_ok=True)
BITMEX_BASE = 'https://www.bitmex.com/api/v1'

def fetch_funding(symbol='XBTUSD', max_count_per_call=500):
    """Paginate BitMEX funding via reverse=false + startTime stepping."""
    all_rows = []
    start = '2016-05-01T00:00:00.000Z'
    while True:
        params = {'symbol': symbol, 'count': max_count_per_call,
                  'reverse': 'false', 'startTime': start}
        try:
            r = requests.get(BITMEX_BASE + '/funding', params=params, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            print(f'  ! error {symbol} @{start}: {e}')
            break
        if not batch: break
        all_rows.extend(batch)
        newest = batch[-1]['timestamp']
        if newest == start: break  # no advance
        # Next: start from the latest +1 second
        start = pd.to_datetime(newest).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        if len(all_rows) % 2000 == 0:
            print(f'    {symbol} funding rows: {len(all_rows):,} ({newest})')
        # BitMEX rate limit: 60/min anonymous; sleep generously
        time.sleep(1.2)
        # Safety
        if pd.to_datetime(newest) > pd.Timestamp.now(tz='UTC'): break
    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['fundingRate'] = df['fundingRate'].astype(float)
        df = df[['timestamp','symbol','fundingRate']].sort_values('timestamp').reset_index(drop=True)
    return df

def main():
    db = duckdb.connect(str(DATA / 'crypto_long.duckdb'))
    for sym in ('XBTUSD', 'ETHUSD'):
        print(f'\n── {sym} ──')
        df = fetch_funding(sym)
        if df.empty:
            print('  no data'); continue
        print(f'  {len(df):,} rows, {df["timestamp"].min()} → {df["timestamp"].max()}')
        tbl = f'funding_{sym}'
        db.execute(f'CREATE OR REPLACE TABLE {tbl} AS SELECT * FROM df')
    db.close()
    print(f'\nWrote {DATA / "crypto_long.duckdb"}')

if __name__ == '__main__':
    main()
