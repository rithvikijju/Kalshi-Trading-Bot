"""Fetch 1-min OHLCV for BTC-USD and ETH-USD from Coinbase via ccxt.

Coinbase Advanced retains ~6 months of 1-min bars. We paginate backwards
with `since` parameter, 300 bars per request, ~34ms rate limit.
"""
import ccxt, time, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import duckdb

DATA = Path('hf_pairs/data'); DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'btc_eth_1min.duckdb'

def fetch_pair(symbol, months_back=6):
    """Paginate 1-min OHLCV backwards from now."""
    cb = ccxt.coinbase({'enableRateLimit': True})
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - months_back * 30 * 24 * 60 * 60 * 1000

    all_bars = []
    cursor = start_ms
    request_n = 0
    while cursor < end_ms:
        try:
            bars = cb.fetch_ohlcv(symbol, timeframe='1m', since=cursor, limit=300)
        except Exception as e:
            print(f'  ! {symbol} fetch err at {cursor}: {e}')
            time.sleep(1.0)
            continue
        if not bars:
            break
        all_bars.extend(bars)
        last_ts = bars[-1][0]
        if last_ts <= cursor:
            break  # no progress
        cursor = last_ts + 60_000  # advance one minute past last bar
        request_n += 1
        if request_n % 50 == 0:
            print(f'    {symbol}: {len(all_bars):,} bars '
                  f'({datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)})')

    df = pd.DataFrame(all_bars, columns=['ts_ms','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
    df = df.drop_duplicates(subset='ts_ms').sort_values('ts_ms').reset_index(drop=True)
    print(f'  {symbol}: {len(df):,} bars  '
          f'{df["ts"].min()} → {df["ts"].max()}')
    return df

def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = duckdb.connect(str(DB_PATH))

    for sym in ['BTC/USD', 'ETH/USD']:
        print(f'\n── {sym} ──')
        df = fetch_pair(sym, months_back=months)
        table = sym.replace('/', '_').replace('-', '_')
        db.execute(f'CREATE TABLE {table} AS SELECT * FROM df')

    db.close()
    print(f'\nWrote {DB_PATH} ({DB_PATH.stat().st_size/1e6:.1f} MB)')

if __name__ == '__main__':
    main()
