"""
Spot-informed MM test: are perp maker-fills that happen right after a sharp SPOT move
more toxic (negative forward edge)? If yes, a maker that PULLS quotes during fast spot
moves removes the toxic fills and keeps the profitable calm-flow spread capture.

For each perp trade (maker fill), compute:
  - prior spot move over last W sec (how fast spot was moving when we got filled)
  - forward maker edge over D sec  = s*(perp_mid_{t+D} - price)   (+ = maker profit)
Bucket by |prior spot move| and by whether the fill was 'with' or 'against' the spot
move direction. The against-the-move fills are the pickoffs to avoid.
"""
import sys, json, time, urllib.request
from datetime import datetime, timezone
import numpy as np
import pandas as pd

TK = 'KXBTCPERP'; MULT = 1e4; SYM = 'BTCUSDT'
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
W = 3        # prior-spot-move window (s)
D = 15       # forward edge horizon (s)


def g(u):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read())


def pull_trades(t0):
    B = 'https://external-api.kalshi.com/trade-api/v2/margin/trades'
    rows, cur, p = [], '', 0
    while p < 400:
        d = g(B + f'?ticker={TK}&limit=1000' + (f'&cursor={cur}' if cur else ''))
        tr = d.get('trades', [])
        if not tr:
            break
        for t in tr:
            ct = datetime.fromisoformat(t['created_time'].replace('Z', '+00:00')).timestamp()
            rows.append((ct, float(t['price']) * MULT, t['side'] if 'side' in t else t['taker_side'], float(t['count'])))
        p += 1; cur = d.get('cursor', '')
        if rows[-1][0] < t0 or not cur:
            break
    df = pd.DataFrame(rows, columns=['ts', 'price', 'side', 'cnt'])
    return df[df.ts >= t0].sort_values('ts').reset_index(drop=True)


def binance_1s(a, b):
    H = 'https://data-api.binance.vision/api/v3/klines'
    rows, s = [], int(a * 1000)
    while s < int(b * 1000):
        d = g(f'{H}?symbol={SYM}&interval=1s&startTime={s}&endTime={int(b*1000)}&limit=1000')
        if not d:
            break
        rows += [(k[0] // 1000, float(k[4])) for k in d]
        s = d[-1][0] + 1000
        if len(d) < 1000:
            break
    return pd.DataFrame(rows, columns=['sec', 'spot']).drop_duplicates('sec').set_index('sec').spot


def main():
    t_end = time.time()
    tr = pull_trades(t_end - HOURS * 3600)
    sec0, sec1 = int(tr.ts.min()), int(tr.ts.max()) + 1
    spot = binance_1s(sec0 - W - 1, sec1 + D + 1)
    grid = np.arange(spot.index.min(), spot.index.max() + 1)
    spot = spot.reindex(grid).ffill()
    sp = spot.to_dict()
    perp_mid = tr.assign(sec=tr.ts.astype(int)).groupby('sec').price.last()
    pm = perp_mid.reindex(np.arange(perp_mid.index.min(), perp_mid.index.max() + 1)).ffill().to_dict()

    sec = tr.ts.astype(int).values
    price = tr.price.values
    s = np.where(tr.side.values == 'bid', 1.0, -1.0)         # +1 maker bought, -1 sold
    cnt = tr.cnt.values
    # prior spot move (bp) over W sec, signed
    prior = np.array([(sp.get(t, np.nan) / sp.get(t - W, np.nan) - 1) * 1e4 for t in sec])
    fwd = np.array([pm.get(t + D, np.nan) for t in sec])
    edge_bp = s * (fwd - price) / np.nanmedian(price) * 1e4   # maker fwd edge in bp
    m = ~np.isnan(edge_bp) & ~np.isnan(prior)
    edge_bp, prior, s, cnt = edge_bp[m], prior[m], s[m], cnt[m]

    def wmean(x, w):
        return np.average(x, weights=w) if len(x) else float('nan')

    print(f'{m.sum()} fills | overall fwd{D}s maker edge {wmean(edge_bp, cnt):+.3f}bp\n')
    print(f'By |prior {W}s spot move| bucket:')
    print(f'{"bucket":>14} {"fills":>7} {"edge bp":>9} {"win%":>6}')
    edgesabs = np.abs(prior)
    bins = [0, 1, 2, 4, 8, 1e9]
    labs = ['<1bp', '1-2bp', '2-4bp', '4-8bp', '>8bp']
    for lo, hi, lab in zip(bins[:-1], bins[1:], labs):
        b = (edgesabs >= lo) & (edgesabs < hi)
        if b.sum() < 20:
            print(f'{lab:>14} {b.sum():>7} (few)'); continue
        print(f'{lab:>14} {b.sum():>7} {wmean(edge_bp[b], cnt[b]):>9.3f} '
              f'{wmean((edge_bp[b]>0).astype(float), cnt[b])*100:>5.0f}%')

    # with vs against: was the maker filled AGAINST the way spot was moving? (pickoff)
    # maker bought (s=+1) while spot falling (prior<0) = caught a falling knife = against.
    against = (s * prior) < 0
    print(f'\nfill direction vs prior spot move:')
    print(f'  WITH  move ({(~against).sum():>6} fills): edge {wmean(edge_bp[~against], cnt[~against]):+.3f}bp')
    print(f'  AGAINST move ({against.sum():>6} fills): edge {wmean(edge_bp[against], cnt[against]):+.3f}bp')
    # spot-informed rule: drop fills during fast moves (|prior|>=thr). report kept-edge.
    print(f'\nspot-informed quoting (only quote when |prior {W}s move| < thr):')
    for thr in (8, 4, 2, 1):
        keep = edgesabs < thr
        print(f'  thr {thr}bp: keep {keep.sum():>6} fills ({keep.mean()*100:>3.0f}%)  '
              f'edge {wmean(edge_bp[keep], cnt[keep]):+.3f}bp')


if __name__ == '__main__':
    main()
