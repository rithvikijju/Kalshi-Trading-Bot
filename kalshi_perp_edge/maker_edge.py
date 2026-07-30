"""
Is ZERO-FEE market-making on the Kalshi perp profitable? (the real candidate edge)

For every public perp trade we know price + taker_side, so we know what the resting
MAKER did:
  taker_side='bid'  -> taker SOLD into the bid -> maker BOUGHT at price (now long)
  taker_side='ask'  -> taker LIFTED the ask    -> maker SOLD at price   (now short)
Maker edge at horizon D (per contract, signed so + = maker profit):
  edge(D) = s * (perp_mid_{t+D} - price),   s=+1 if maker bought else -1
edge(0) ~ +half-spread (the capture); as D grows, adverse selection erodes it.
If mean edge stays > 0 at D where the maker can realistically flatten, MM is +EV at
zero fees. We use the 1s perp last-price as the mid proxy and also report it in bp.
"""
import sys, json, time, urllib.request
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pathlib import Path

TK = 'KXBTCPERP'; MULT = 1e4
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def g(u):
    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def pull_trades(t_start):
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
        pages += 1; cur = d.get('cursor', '')
        if rows[-1][0] < t_start or not cur:
            break
    df = pd.DataFrame(rows, columns=['ts', 'price', 'side', 'cnt'])
    return df[df.ts >= t_start].sort_values('ts').reset_index(drop=True)


def main():
    t_end = time.time()
    tr = pull_trades(t_end - HOURS * 3600)
    print(f'{len(tr)} trades, {(tr.ts.iloc[-1]-tr.ts.iloc[0])/3600:.1f}h | '
          f"maker-buys {(tr.side=='bid').sum()} maker-sells {(tr.side=='ask').sum()}")
    # 1s last-price mid proxy
    tr['sec'] = tr.ts.astype(int)
    mid = tr.groupby('sec').price.last()
    grid = np.arange(mid.index.min(), mid.index.max() + 1)
    mid = mid.reindex(grid).ffill()
    midmap = mid.to_dict()
    s = np.where(tr.side.values == 'bid', 1.0, -1.0)        # +1 maker bought, -1 sold
    price = tr.price.values
    sec = tr.sec.values
    spot_ref = float(np.nanmedian(price))
    print(f'\nmaker edge per fill (size-weighted), zero fee:')
    print(f'{"horizon":>8} {"$/ct":>9} {"bp":>8} {"win%":>7}')
    for D in (0, 1, 2, 5, 15, 30, 60, 120):
        fwd = np.array([midmap.get(t + D, np.nan) for t in sec])
        edge = s * (fwd - price)                              # $/contract
        m = ~np.isnan(edge)
        w = tr.cnt.values[m]
        e = edge[m]
        mean_dollar = np.average(e, weights=w)
        bp = mean_dollar / spot_ref * 1e4
        win = np.average((e > 0).astype(float), weights=w) * 100
        print(f'{D:>6}s {mean_dollar:>9.5f} {bp:>8.3f} {win:>6.0f}%')
    print('\nRead: edge(0)=half-spread captured; if it stays >0 at the horizon you can '
          'realistically flatten, zero-fee MM is +EV. <0 => adverse selection wins.')

    # net spread-capture model: maker quotes BOTH sides, earns full spread per round trip
    # only if buy-fill and sell-fill adverse selection both small. Approx round-trip edge:
    for D in (5, 15, 30):
        fwd = np.array([midmap.get(t + D, np.nan) for t in sec])
        edge = s * (fwd - price); m = ~np.isnan(edge)
        # inventory-neutralized: pair each fill's edge; mean*2 ~ round trip both sides
        print(f'  implied round-trip MM edge @ D={D}s: '
              f'{2*np.average(edge[m], weights=tr.cnt.values[m]):.5f} $/ct '
              f'({2*np.average(edge[m], weights=tr.cnt.values[m])/spot_ref*1e4:.3f} bp)')


if __name__ == '__main__':
    main()
