"""
Pull BTC + ETH funding rates from crypto-native perp venues (Binance, OKX,
Hyperliquid) -- current snapshot + history -- annualize, and save aligned series.

This is the baseline against which we measure whether Kalshi's perp funding diverges
(the whole edge thesis). Kalshi leg is wired in separately once its API endpoint is
confirmed (see funding_kalshi_arb/pull_kalshi.py).

Public APIs, no auth. Annualization:
  Binance/OKX  -> 8h interval -> x1095/yr
  Hyperliquid  -> 1h interval -> x8760/yr
"""
import json, time, urllib.request, urllib.parse
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'data'
OUT.mkdir(parents=True, exist_ok=True)

YR = {'8h': 365 * 3, '1h': 24 * 365}


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'funding-research/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_post(url, body, timeout=15):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'funding-research/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def binance(sym):
    """Current + history (last ~1000 prints, 8h)."""
    cur = http_get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}')
    hist = http_get(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&limit=1000')
    rate = float(cur['lastFundingRate'])
    series = [(int(h['fundingTime']), float(h['fundingRate'])) for h in hist]
    return rate, '8h', series


def okx(inst):
    cur = http_get(f'https://www.okx.com/api/v5/public/funding-rate?instId={inst}')
    rate = float(cur['data'][0]['fundingRate'])
    h = http_get(f'https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&limit=100')
    series = [(int(d['fundingTime']), float(d['realizedRate'] if 'realizedRate' in d else d['fundingRate']))
              for d in h['data']]
    return rate, '8h', series


def hyperliquid(coin):
    ctxs = http_post('https://api.hyperliquid.xyz/info', {'type': 'metaAndAssetCtxs'})
    meta, ctx = ctxs[0]['universe'], ctxs[1]
    idx = next(i for i, u in enumerate(meta) if u['name'] == coin)
    rate = float(ctx[idx]['funding'])           # hourly
    start = int(time.time() * 1000) - 30 * 24 * 3600 * 1000
    h = http_post('https://api.hyperliquid.xyz/info',
                  {'type': 'fundingHistory', 'coin': coin, 'startTime': start})
    series = [(int(d['time']), float(d['fundingRate'])) for d in h]
    return rate, '1h', series


VENUES = {
    'binance':     {'BTC': lambda: binance('BTCUSDT'), 'ETH': lambda: binance('ETHUSDT')},
    'okx':         {'BTC': lambda: okx('BTC-USDT-SWAP'), 'ETH': lambda: okx('ETH-USDT-SWAP')},
    'hyperliquid': {'BTC': lambda: hyperliquid('BTC'), 'ETH': lambda: hyperliquid('ETH')},
}


def main():
    print(f'{"venue":<12}{"coin":<5}{"interval":<9}{"rate/intvl":>12}{"annualized":>12}')
    print('-' * 50)
    rows = []
    for venue, coins in VENUES.items():
        for coin, fn in coins.items():
            try:
                rate, intv, series = fn()
                ann = rate * YR[intv]
                print(f'{venue:<12}{coin:<5}{intv:<9}{rate*100:>11.4f}%{ann*100:>11.2f}%')
                rows.append({'venue': venue, 'coin': coin, 'interval': intv,
                             'rate': rate, 'annualized': ann, 'n_hist': len(series)})
                # save history
                with open(OUT / f'fund_{venue}_{coin}.json', 'w') as f:
                    json.dump(series, f)
            except Exception as ex:
                print(f'{venue:<12}{coin:<5} ERROR: {type(ex).__name__}: {str(ex)[:90]}')
    with open(OUT / 'funding_current.json', 'w') as f:
        json.dump(rows, f, indent=2)
    print(f'\nsaved current + history to {OUT}')

    # quick spread readout (annualized), BTC + ETH, across native venues
    print('\nNative cross-venue annualized funding (the spread Kalshi must beat to be cheap, '
          'or undercut to be rich):')
    for coin in ('BTC', 'ETH'):
        vals = {r['venue']: r['annualized'] for r in rows if r['coin'] == coin}
        if vals:
            hi = max(vals, key=vals.get); lo = min(vals, key=vals.get)
            print(f'  {coin}: ' + '  '.join(f'{v}={vals[v]*100:.1f}%' for v in vals)
                  + f'   | spread {hi}-{lo} = {(vals[hi]-vals[lo])*100:.1f}%')


if __name__ == '__main__':
    main()
