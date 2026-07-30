"""
Forward tick capture: Kalshi perp top-of-book (BTC+ETH) + Coinbase spot at ~1 Hz.

This is the missing ground truth. The 1-min candle backtest shows the perp lags
Coinbase by <1 min with a huge lead-lag corr, but cannot resolve WHERE in that
minute the catch-up happens -> the capturable edge sits between an 8bp upper bound
(instant exec) and a negative floor (1 min late). This logs synchronized sub-second
quotes so we can measure the true lag profile and realistic taker/maker capture.

Logs JSONL: {ts_ns, btc:{bid,ask,mark,oi}, eth:{...}, spot_btc, spot_eth}
"""
import sys, time, json
from pathlib import Path
from datetime import datetime, timezone
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'kalshi_k6'))
import config

OUT = Path(__file__).resolve().parent / 'data'
OUT.mkdir(exist_ok=True)
TICKERS = {'btc': 'KXBTCPERP', 'eth': 'KXETHPERP'}
CB = {'btc': 'BTC-USD', 'eth': 'ETH-USD'}


def cb_spot(prod):
    u = f'https://api.exchange.coinbase.com/products/{prod}/ticker'
    req = urllib.request.Request(u, headers={'User-Agent': 'res/1.0'})
    d = json.loads(urllib.request.urlopen(req, timeout=4).read())
    return float(d['price'])


def main():
    hz = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    c = config.KalshiClient()
    c.base = 'https://external-api.kalshi.com/trade-api/v2'
    fn = OUT / f'ticks_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl'
    period = 1.0 / hz
    print(f'Capturing {list(TICKERS)} perps + Coinbase at {hz}Hz -> {fn.name}', flush=True)
    n = 0
    with open(fn, 'a') as f:
        while True:
            t0 = time.time()
            rec = {'ts_ns': time.time_ns()}
            for k, tk in TICKERS.items():
                try:
                    m = c._get(f'/margin/markets/{tk}')['market']
                    rec[k] = {'bid': float(m['bid']), 'ask': float(m['ask']),
                              'mark': float(m.get('mark_price', m.get('last_price', 0)) or 0),
                              'oi': float(m.get('open_interest', 0) or 0)}
                except Exception as ex:
                    rec[k + '_err'] = str(ex)[:60]
            for k, prod in CB.items():
                try:
                    rec['spot_' + k] = cb_spot(prod)
                except Exception as ex:
                    rec['spot_' + k + '_err'] = str(ex)[:60]
            f.write(json.dumps(rec) + '\n')
            n += 1
            if n % 120 == 0:
                f.flush()
                print(f'  {n} ticks  btc bid={rec.get("btc",{}).get("bid")} '
                      f'spot={rec.get("spot_btc")}', flush=True)
            time.sleep(max(0, period - (time.time() - t0)))


if __name__ == '__main__':
    main()
