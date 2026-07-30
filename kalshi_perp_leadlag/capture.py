"""
Kalshi BTC perpetual + Coinbase spot capture logger.

Purpose: gather the ONE dataset that could still change the lead-lag verdict --
the Kalshi *perp's* real spread and how fast it lags Coinbase. (The binary version
is dead: signal ~0.5c < spread ~2.6c; see memory kalshi_btc_leadlag_finding.)

Run this forward for a few days, then re-run the kalshi_v2/leadlag.py + leadlag_exec.py
methodology on the captured perp price (instead of binary mid). If the perp spread is
much tighter than the binaries' ~2.6c AND it still lags on jumps, a fast maker *might*
clear costs -- otherwise this confirms the scalp is not worth building.

Reuses the proven kalshi_k6 KalshiClient. Market data is unauthenticated.

NOTE / TO VERIFY ON FIRST RUN: the exact perp series ticker + orderbook endpoint.
This script DISCOVERS them (prints what the API returns) so you can confirm before
trusting the poll. Pass --ticker once you know it (e.g. the BTCPERP market ticker).
"""
import sys, time, json, argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'kalshi_k6'))
from config import KalshiClient, _coinbase_spot  # reuse proven client

OUT = Path(__file__).resolve().parent / 'data'
OUT.mkdir(exist_ok=True)


def now_ns():
    return time.time_ns()


def discover(client):
    """Find perp markets and print them so the ticker can be confirmed."""
    print('Discovering Kalshi perpetual markets ...', flush=True)
    # Perps may live under a series like 'KXBTCPERP'/'BTCPERP'. Try a few, plus a
    # broad open-markets scan filtered for 'PERP'. Endpoints subject to confirmation.
    candidates = []
    for series in ('BTCPERP', 'KXBTCPERP', 'KXBTC'):
        try:
            ev = client.get_events(series_ticker=series, status='open', limit=20)
            for e in ev.get('events', []):
                candidates.append(('event', series, e.get('event_ticker')))
        except Exception as ex:
            print(f'  series {series}: {type(ex).__name__} {str(ex)[:80]}')
    try:
        mk = client.get_markets(status='open', limit=200)
        for m in mk.get('markets', []):
            t = m.get('ticker', '')
            if 'PERP' in t.upper():
                candidates.append(('market', t, m.get('title', '')[:60]))
    except Exception as ex:
        print(f'  market scan: {type(ex).__name__} {str(ex)[:80]}')
    print('Candidates found:')
    for c in candidates:
        print('  ', c)
    if not candidates:
        print('  (none — confirm the perp endpoint in perps_openapi.yaml; the perp API '
              'path may differ from /trade-api/v2/markets)')
    return candidates


def poll_loop(client, ticker, hz):
    """Log synchronized Coinbase spot + Kalshi perp top-of-book to JSONL."""
    fn = OUT / f'perp_capture_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl'
    period = 1.0 / hz
    print(f'Logging {ticker} + Coinbase BTC-USD at {hz}Hz -> {fn}', flush=True)
    n = 0
    with open(fn, 'a') as f:
        while True:
            t0 = time.time()
            rec = {'ts_ns': now_ns(), 'ticker': ticker}
            try:
                rec['spot'] = _coinbase_spot('BTC-USD')
            except Exception as ex:
                rec['spot_err'] = str(ex)[:80]
            try:
                ob = client.get_orderbook(ticker)
                rec['orderbook'] = ob.get('orderbook', ob)
            except Exception as ex:
                rec['ob_err'] = str(ex)[:80]
            f.write(json.dumps(rec) + '\n')
            n += 1
            if n % 60 == 0:
                f.flush()
                print(f'  {n} samples  last spot={rec.get("spot")}', flush=True)
            time.sleep(max(0, period - (time.time() - t0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticker', help='Kalshi perp market ticker (e.g. the BTCPERP market). '
                                      'If omitted, runs discovery only.')
    ap.add_argument('--hz', type=float, default=2.0, help='poll rate (samples/sec)')
    args = ap.parse_args()

    client = KalshiClient()
    if not args.ticker:
        discover(client)
        print('\nRe-run with --ticker <confirmed_ticker> to start logging.')
        return
    poll_loop(client, args.ticker, args.hz)


if __name__ == '__main__':
    main()
