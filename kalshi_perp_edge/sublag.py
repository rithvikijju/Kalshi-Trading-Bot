"""
Sub-second lead-lag: does the Kalshi perp lag spot at the SECONDS scale (invisible to
1-min bars)? Uses REAL tick data:
  - Kalshi perp TRADES (microsecond created_time, price, taker_side) -> 1s last price
  - Binance 1s klines (data-api.binance.vision; api.binance.com is geo-blocked) -> 1s close
Both are true exchange execution timestamps (directly comparable). Cross-correlate 1s
returns at shifts; a peak at +L sec means spot leads the perp by L seconds => exploitable.
Also tests an order-flow (taker_side imbalance) predictor as a bonus.
"""
import sys, json, time, urllib.request
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
TK = 'KXBTCPERP'; MULT = 1e4; SYM = 'BTCUSDT'
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def g(u, hdr=None):
    req = urllib.request.Request(u, headers=hdr or {'User-Agent': 'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def pull_perp_trades(t_start):
    """All perp trades back to t_start (unix sec). Returns df[sec, price, side, count]."""
    B = 'https://external-api.kalshi.com/trade-api/v2/margin/trades'
    rows, cur, pages = [], '', 0
    while pages < 400:
        u = B + f'?ticker={TK}&limit=1000' + (f'&cursor={cur}' if cur else '')
        d = g(u); tr = d.get('trades', [])
        if not tr:
            break
        for t in tr:
            ct = datetime.fromisoformat(t['created_time'].replace('Z', '+00:00')).timestamp()
            rows.append((ct, float(t['price']) * MULT, t['taker_side'], float(t['count'])))
        pages += 1
        cur = d.get('cursor', '')
        if rows[-1][0] < t_start or not cur:
            break
    df = pd.DataFrame(rows, columns=['ts', 'perp', 'side', 'cnt'])
    return df[df.ts >= t_start].sort_values('ts').reset_index(drop=True)


def pull_binance_1s(t_start, t_end):
    H = 'https://data-api.binance.vision/api/v3/klines'
    rows, s = [], int(t_start * 1000)
    while s < int(t_end * 1000):
        u = f'{H}?symbol={SYM}&interval=1s&startTime={s}&endTime={int(t_end*1000)}&limit=1000'
        d = g(u)
        if not d:
            break
        for k in d:
            rows.append((k[0] // 1000, float(k[4])))   # openTime sec, close
        s = d[-1][0] + 1000
        if len(d) < 1000:
            break
    return pd.DataFrame(rows, columns=['ts', 'spot']).drop_duplicates('ts').sort_values('ts')


def main():
    t_end = time.time()
    t_start = t_end - HOURS * 3600
    perp = pull_perp_trades(t_start)
    print(f'perp trades: {len(perp)}  '
          f'{datetime.fromtimestamp(perp.ts.iloc[0], timezone.utc):%H:%M}..'
          f'{datetime.fromtimestamp(perp.ts.iloc[-1], timezone.utc):%H:%M}')
    # 1s last price + signed taker flow
    perp['sec'] = perp.ts.astype(int)
    p1 = perp.groupby('sec').agg(perp=('perp', 'last'),
                                 flow=('side', lambda s: (s == 'ask').sum() - (s == 'bid').sum())).reset_index()
    spot = pull_binance_1s(perp.sec.min(), perp.sec.max() + 1)
    print(f'binance 1s: {len(spot)} bars')
    df = pd.merge(p1.rename(columns={'sec': 'ts'}), spot, on='ts', how='inner').sort_values('ts').reset_index(drop=True)
    # fill gaps to a continuous 1s grid
    grid = pd.DataFrame({'ts': np.arange(df.ts.min(), df.ts.max() + 1)})
    df = pd.merge(grid, df, on='ts', how='left')
    df['perp'] = df.perp.ffill(); df['spot'] = df.spot.ffill(); df['flow'] = df.flow.fillna(0)
    df = df.dropna().reset_index(drop=True)
    print(f'aligned 1s grid: {len(df)} sec ({len(df)/3600:.1f}h)\n')

    df['rp'] = np.log(df.perp).diff()
    df['rs'] = np.log(df.spot).diff()
    print('cross-corr  corr(spot_ret[t-k], perp_ret[t])   (k>0 => SPOT LEADS perp by k s)')
    best = (0, -9)
    for k in range(-5, 16):
        c = df.rs.shift(k).corr(df.rp)
        if c > best[1]:
            best = (k, c)
        bar = '#' * int(max(0, c) * 50)
        print(f'  k={k:+3d}s  {c:+.3f} {bar}')
    print(f'  => peak at k={best[0]:+d}s (corr {best[1]:.3f}) '
          f'{"=> spot leads perp, EXPLOITABLE" if best[0] > 0 else "=> contemporaneous/no lag"}')

    # basis (perp - spot) std at 1s and its 1..30s mean-reversion
    df['basis_bp'] = (df.perp / df.spot - 1) * 1e4
    print(f'\n1s basis: std {df.basis_bp.std():.2f}bp  ac(1s) {df.basis_bp.autocorr(1):.3f}  '
          f'ac(5s) {df.basis_bp.autocorr(5):.3f}  ac(30s) {df.basis_bp.autocorr(30):.3f}')

    # if spot leads by L: predict perp's next-L-sec move from current basis; net vs spread
    L = max(best[0], 1)
    df['fwd_perpL'] = (np.log(df.perp.shift(-L)) - np.log(df.perp)) * 1e4
    sig = -df.basis_bp                       # cheap perp (basis<0) => expect up
    print(f'\nbasis->fwd{L}s perp corr {df.basis_bp.corr(df.fwd_perpL):+.3f}  | '
          f'flow->fwd{L}s corr {df.flow.corr(df.fwd_perpL):+.3f}')
    df.to_parquet(DATA / 'sublag_btc.parquet')
    print('saved sublag_btc.parquet')


if __name__ == '__main__':
    main()
