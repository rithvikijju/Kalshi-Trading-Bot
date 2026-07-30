"""Fetch all data for the 10 strategies.

Crypto via OKX (Binance blocks US). Equity via yfinance.

Outputs:
  strategies/data/crypto.duckdb — funding rates, spot+perp klines BTC/ETH/SOL
  strategies/data/equity.duckdb — daily close prices for ~40 tickers
"""
import duckdb, json, time, sys
from pathlib import Path
from datetime import datetime, timezone
import requests
import pandas as pd

DATA_DIR = Path('strategies/data'); DATA_DIR.mkdir(parents=True, exist_ok=True)

# ─── Crypto via OKX ──────────────────────────────────────────────
OKX_BASE = 'https://www.okx.com/api/v5'

def okx_funding_rates(inst_id='BTC-USDT-SWAP', months_back=24):
    """Funding rate history. OKX max 100/call. Paginate via 'after' (older)."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - months_back * 30 * 24 * 3600 * 1000
    cursor = end_ms
    all_rows = []
    n_calls = 0
    while True:
        params = {'instId': inst_id, 'limit': '100', 'before': str(start_ms),
                  'after': str(cursor)}
        try:
            r = requests.get(OKX_BASE + '/public/funding-rate-history',
                             params=params, timeout=15)
            r.raise_for_status()
            data = r.json().get('data', [])
        except Exception as e:
            print(f'  ! funding fetch error {inst_id}: {e}')
            break
        if not data: break
        all_rows.extend(data)
        oldest = min(int(d['fundingTime']) for d in data)
        if oldest >= cursor: break
        cursor = oldest - 1
        if cursor <= start_ms: break
        n_calls += 1
        if n_calls % 20 == 0:
            print(f'    {inst_id} funding rows: {len(all_rows):,}')
        time.sleep(0.12)
    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['fundingTime'] = pd.to_datetime(df['fundingTime'].astype(int), unit='ms', utc=True)
        df['fundingRate'] = df['fundingRate'].astype(float)
        df = df[['fundingTime', 'instId', 'fundingRate']].sort_values('fundingTime').reset_index(drop=True)
    return df

def okx_klines(inst_id, bar='8H', months_back=24):
    """OKX history-candles endpoint, paginate backwards via 'after'."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - months_back * 30 * 24 * 3600 * 1000
    cursor = end_ms
    all_rows = []
    n_calls = 0
    while True:
        params = {'instId': inst_id, 'bar': bar, 'limit': '100', 'after': str(cursor)}
        try:
            r = requests.get(OKX_BASE + '/market/history-candles',
                             params=params, timeout=15)
            r.raise_for_status()
            data = r.json().get('data', [])
        except Exception as e:
            print(f'  ! klines fetch error {inst_id}: {e}')
            break
        if not data: break
        all_rows.extend(data)
        oldest = min(int(row[0]) for row in data)
        if oldest >= cursor: break
        cursor = oldest - 1
        if cursor <= start_ms: break
        n_calls += 1
        if n_calls % 20 == 0:
            print(f'    {inst_id} klines: {len(all_rows):,}')
        time.sleep(0.12)
    cols = ['ts', 'open', 'high', 'low', 'close', 'volume', 'volCcy', 'volCcyQuote', 'confirm']
    df = pd.DataFrame(all_rows, columns=cols[:len(all_rows[0])] if all_rows else cols)
    if not df.empty:
        df['ts'] = pd.to_datetime(df['ts'].astype(int), unit='ms', utc=True)
        for c in ('open','high','low','close','volume'):
            df[c] = df[c].astype(float)
        df = df.sort_values('ts').reset_index(drop=True)
    return df

def fetch_crypto():
    db_path = DATA_DIR / 'crypto.duckdb'
    if db_path.exists(): db_path.unlink()  # rewrite cleanly
    db = duckdb.connect(str(db_path))

    coins = [('BTC', 'BTC-USDT', 'BTC-USDT-SWAP'),
             ('ETH', 'ETH-USDT', 'ETH-USDT-SWAP'),
             ('SOL', 'SOL-USDT', 'SOL-USDT-SWAP')]
    for tag, spot_inst, perp_inst in coins:
        print(f'\n── {tag} ──')
        print(f'  Funding ({perp_inst})...')
        fr = okx_funding_rates(perp_inst, months_back=36)
        if not fr.empty:
            print(f'    {len(fr):,} rows, {fr["fundingTime"].min()} → {fr["fundingTime"].max()}')
            db.execute(f'CREATE TABLE funding_{tag} AS SELECT * FROM fr')

        print(f'  Spot klines (8h)...')
        spot = okx_klines(spot_inst, bar='8H', months_back=36)
        if not spot.empty:
            print(f'    {len(spot):,} rows')
            db.execute(f'CREATE TABLE spot_{tag} AS SELECT * FROM spot')

        print(f'  Perp klines (8h)...')
        perp = okx_klines(perp_inst, bar='8H', months_back=36)
        if not perp.empty:
            print(f'    {len(perp):,} rows')
            db.execute(f'CREATE TABLE perp_{tag} AS SELECT * FROM perp')
    db.close()
    print(f'\nWrote {db_path}')

# ─── Equity via yfinance ──────────────────────────────────────────
def fetch_equity():
    import yfinance as yf
    tickers = (
        'XLK XLF XLE XLY XLP XLV XLI XLB XLU XLRE XLC '   # sectors (S5)
        'SPY EFA EEM TLT IEF GLD DBC RWX VNQ '              # cross-asset (S9)
        'FXA FXC FXE FXY FXB UUP '                          # FX (S10)
        'SVXY UVXY VXX VIXY '                               # vol (S6)
        'SPLV USMV '                                        # low-vol (S8)
        'KO PEP MA V GS MS BAC JPM CVX XOM HD LOW WMT TGT MSFT GOOGL META '  # pairs (S7)
        '^VIX BTC-USD ETH-USD'                              # vol + crypto via yf
    ).split()
    print(f'Fetching {len(tickers)} tickers...')
    data = yf.download(tickers, start='2010-01-01', end=None,
                       progress=False, auto_adjust=True, threads=True)
    if data is None or data.empty:
        print('!! no data'); return
    close = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data[['Close']]
    close = close.dropna(how='all')
    print(f'Got {len(close):,} rows × {len(close.columns)} tickers')
    print(f'Range: {close.index.min()} → {close.index.max()}')

    db_path = DATA_DIR / 'equity.duckdb'
    if db_path.exists(): db_path.unlink()
    db = duckdb.connect(str(db_path))
    # Flatten to long format (date, ticker, close) for easy querying
    long = close.reset_index().melt(id_vars=close.index.name or 'Date',
                                     var_name='ticker', value_name='close')
    long.columns = ['date', 'ticker', 'close']
    long = long.dropna(subset=['close'])
    long['date'] = pd.to_datetime(long['date'])
    db.execute('CREATE TABLE prices AS SELECT * FROM long')
    db.execute('CREATE INDEX idx_pt ON prices(ticker, date)')
    n = db.execute('SELECT COUNT(*) FROM prices').fetchone()[0]
    print(f'Wrote {db_path} ({n:,} rows)')
    db.close()

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode in ('all', 'crypto'):
        fetch_crypto()
    if mode in ('all', 'equity'):
        fetch_equity()
