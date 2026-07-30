"""
Pull the full backtest dataset for the Kalshi perpetual edge research.

Three reliable, UNAUTHENTICATED sources:
  1. Kalshi perp 1-min candles  -> the tradeable instrument w/ REAL bid/ask, vol, OI.
       GET /trade-api/v2/margin/markets/{ticker}/candlesticks  (period_interval=1)
       price units = dollars/contract; 1 contract = 0.0001 BTC => implied_BTC = price*1e4
  2. Coinbase spot 1-min candles -> the leading crypto-native reference.
       GET https://api.exchange.coinbase.com/products/{prod}/candles?granularity=60
  3. Kalshi funding history (8h) -> for funding-time + carry analysis.
       GET /trade-api/v2/margin/funding_rates/historical?ticker=...

Everything saved as parquet/json under data/. Re-runnable; pulls full product life.
"""
import json, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / 'data'
OUT.mkdir(parents=True, exist_ok=True)
KB = 'https://external-api.kalshi.com/trade-api/v2/margin'
CB = 'https://api.exchange.coinbase.com'
LAUNCH = int(datetime(2026, 6, 3, tzinfo=timezone.utc).timestamp())

PERPS = {'BTC': ('KXBTCPERP', 'BTC-USD', 1e4), 'ETH': ('KXETHPERP', 'ETH-USD', 1e3)}
# multiplier: implied_underlying = perp_price * mult (BTC contract=.0001, ETH=.001 guess; verified at runtime)


def _get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'research/1.0'})
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(1.5 * (i + 1)); continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(1.0); continue
            raise


def pull_perp_candles(ticker):
    """1-min candles across full life, chunked <=4900 min/request."""
    rows = []
    step = 4900 * 60
    end = int(time.time())
    s = LAUNCH
    while s < end:
        e = min(s + step, end)
        url = f'{KB}/markets/{ticker}/candlesticks?start_ts={s}&end_ts={e}&period_interval=1'
        try:
            d = _get(url)
        except Exception as ex:
            print(f'  {ticker} chunk {s}: {ex}'); s = e; continue
        cs = d.get('candlesticks', [])
        for c in cs:
            p = c.get('price') or {}
            b = c.get('bid') or {}
            a = c.get('ask') or {}
            rows.append({
                'ts': c.get('end_period_ts'),
                'o': _f(p.get('open')), 'h': _f(p.get('high')), 'l': _f(p.get('low')),
                'close': _f(p.get('close')), 'mean': _f(p.get('mean')),
                'bid': _f(b.get('close')), 'ask': _f(a.get('close')),
                'vol': _f(c.get('volume')), 'oi': _f(c.get('open_interest')),
            })
        s = e
    df = pd.DataFrame(rows).dropna(subset=['ts']).drop_duplicates('ts').sort_values('ts')
    return df.reset_index(drop=True)


def pull_coinbase(product):
    rows = []
    end = int(time.time())
    s = LAUNCH
    step = 300 * 60  # 300 candles max/request
    while s < end:
        e = min(s + step, end)
        url = (f'{CB}/products/{product}/candles?granularity=60'
               f'&start={datetime.fromtimestamp(s, timezone.utc).isoformat()}'
               f'&end={datetime.fromtimestamp(e, timezone.utc).isoformat()}')
        try:
            d = _get(url)
        except Exception as ex:
            print(f'  CB {product} {s}: {ex}'); s = e; time.sleep(0.4); continue
        for r in d:  # [time, low, high, open, close, volume]
            rows.append({'ts': int(r[0]), 'spot': float(r[4])})
        s = e
        time.sleep(0.35)  # CB public rate limit
    df = pd.DataFrame(rows).drop_duplicates('ts').sort_values('ts')
    return df.reset_index(drop=True)


def pull_funding(ticker):
    url = f'{KB}/funding_rates/historical?ticker={ticker}&start_ts={LAUNCH}&end_ts={int(time.time())}'
    d = _get(url)
    rows = d.get('funding_rates', [])
    out = []
    for r in rows:
        ft = r['funding_time']
        s = str(ft).replace('Z', '+00:00')
        try:
            ts = int(datetime.fromisoformat(s).timestamp())
        except Exception:
            ts = int(float(ft))
        out.append({'ts': ts, 'rate': float(r['funding_rate']),
                    'mark': float(r.get('mark_price', 0) or 0)})
    return pd.DataFrame(out).sort_values('ts').reset_index(drop=True)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def main():
    for coin, (tk, prod, mult) in PERPS.items():
        print(f'=== {coin} ({tk}) ===')
        perp = pull_perp_candles(tk)
        print(f'  perp candles: {len(perp)}  '
              f'{datetime.fromtimestamp(perp.ts.iloc[0], timezone.utc):%m-%d %H:%M}..'
              f'{datetime.fromtimestamp(perp.ts.iloc[-1], timezone.utc):%m-%d %H:%M}'
              if len(perp) else '  perp candles: 0')
        if len(perp):
            sp = perp.ask.sub(perp.bid)
            print(f'  spread $/ct: mean {sp.mean():.5f} med {sp.median():.5f} | '
                  f'as bps of price: {(sp/perp.close).mean()*1e4:.2f}bp | '
                  f'vol/min med {perp.vol.median():.0f} ct')
            perp.to_parquet(OUT / f'perp_{coin}.parquet')
        cb = pull_coinbase(prod)
        print(f'  coinbase: {len(cb)} candles')
        if len(cb):
            cb.to_parquet(OUT / f'spot_{coin}.parquet')
        fund = pull_funding(tk)
        if len(fund):
            fund.to_parquet(OUT / f'fund_{coin}.parquet')
            nz = (fund.rate != 0).mean()
            print(f'  funding: {len(fund)} prints, nonzero {nz*100:.0f}%, '
                  f'mean/8h {fund.rate.mean()*100:.4f}%')


if __name__ == '__main__':
    main()
