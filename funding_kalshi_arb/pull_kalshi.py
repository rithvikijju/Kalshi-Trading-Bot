"""
Pull Kalshi perp funding-rate history (unauthenticated) and save in the same format
as pull_funding.py so analyze_spread.py picks it up as another venue.

Endpoint (verified): GET https://external-api.kalshi.com/trade-api/v2/margin/funding_rates/historical
  ?ticker=KXBTCPERP&start_ts=<unix_s>&end_ts=<unix_s>
Response: {"funding_rates":[{market_ticker, funding_time, funding_rate, mark_price}, ...]}
  funding_rate = decimal per 8h interval (e.g. 0.000115 = 0.0115%/8h).
"""
import json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'data'
OUT.mkdir(parents=True, exist_ok=True)
BASE = 'https://external-api.kalshi.com/trade-api/v2/margin/funding_rates/historical'
TICKERS = {'BTC': 'KXBTCPERP', 'ETH': 'KXETHPERP'}


def to_ms(ft):
    if isinstance(ft, (int, float)):
        return int(ft * 1000) if ft < 1e12 else int(ft)
    s = str(ft).replace('Z', '+00:00')
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return int(float(ft) * 1000)


def pull(ticker):
    end = int(time.time())
    start = end - 45 * 24 * 3600          # cover full life of the product (~since launch)
    url = f'{BASE}?ticker={ticker}&start_ts={start}&end_ts={end}'
    req = urllib.request.Request(url, headers={'User-Agent': 'funding-research/1.0'})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    rows = d.get('funding_rates', d if isinstance(d, list) else [])
    out = []
    for r in rows:
        out.append([to_ms(r['funding_time']), float(r['funding_rate']),
                    float(r.get('mark_price', 0) or 0)])
    out.sort()
    return out


def main():
    for coin, tk in TICKERS.items():
        try:
            rows = pull(tk)
            series = [[ms, rate] for ms, rate, mk in rows]   # analyze_spread format
            json.dump(series, open(OUT / f'fund_kalshi_{coin}.json', 'w'))
            if rows:
                t0 = datetime.fromtimestamp(rows[0][0]/1000, timezone.utc)
                t1 = datetime.fromtimestamp(rows[-1][0]/1000, timezone.utc)
                rates = [r for _, r, _ in rows]
                nz = [r for r in rates if r != 0]
                ann = sum(rates)/len(rates) * 365*3
                print(f'{coin} ({tk}): {len(rows)} prints  {t0:%Y-%m-%d}..{t1:%Y-%m-%d}')
                print(f'   rate/8h: mean {sum(rates)/len(rates)*100:.4f}%  '
                      f'nonzero {len(nz)}/{len(rates)} ({100*len(nz)/len(rates):.0f}%)  '
                      f'min {min(rates)*100:.4f}% max {max(rates)*100:.4f}%')
                print(f'   => mean annualized: {ann*100:.2f}%')
            else:
                print(f'{coin} ({tk}): no rows returned')
        except Exception as ex:
            print(f'{coin} ({tk}): ERROR {type(ex).__name__}: {str(ex)[:120]}')


if __name__ == '__main__':
    main()
