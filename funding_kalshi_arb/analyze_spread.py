"""
Characterize funding-rate SPREADS between perp venues — the core of the edge.

Loads the per-venue funding history saved by pull_funding.py, converts each to an
instantaneous ANNUALIZED rate, forward-fills onto a common hourly grid, and for every
venue PAIR computes the spread series + stats that decide if a delta-neutral carry is
real: mean (static carry), mean|.| (dynamic carry, funding is known a period ahead),
sign-stability, and lag-1 persistence.

Kalshi drops in automatically once funding_kalshi_arb/data/fund_kalshi_BTC.json exists.
"""
import json, glob
from pathlib import Path
import numpy as np

DATA = Path(__file__).resolve().parent / 'data'
INTERVAL_YR = {'hyperliquid': 24*365, 'okx': 365*3, 'binance': 365*3, 'kalshi': 365*3}


def load(venue, coin):
    f = DATA / f'fund_{venue}_{coin}.json'
    if not f.exists():
        return None
    series = json.load(open(f))                      # [[ms, rate_per_interval], ...]
    if not series:
        return None
    arr = np.array(series, dtype=float)
    arr = arr[arr[:, 0].argsort()]                    # sort ascending by time (OKX is newest-first)
    t = arr[:, 0] / 1000.0                            # sec
    ann = arr[:, 1] * INTERVAL_YR[venue]              # instantaneous annualized rate
    return t, ann


def grid_align(seriesmap, step_s=3600):
    """Forward-fill each venue's annualized rate onto a common hourly grid."""
    starts = [s[0][0] for s in seriesmap.values()]
    ends = [s[0][-1] for s in seriesmap.values()]
    lo, hi = max(starts), min(ends)
    if hi <= lo:
        return None, {}
    grid = np.arange(lo, hi, step_s)
    out = {}
    for v, (t, ann) in seriesmap.items():
        idx = np.searchsorted(t, grid, side='right') - 1
        idx = np.clip(idx, 0, len(ann) - 1)
        out[v] = ann[idx]
    return grid, out


def stats(spread):
    s = spread
    return dict(
        mean=s.mean(), absmean=np.abs(s).mean(), std=s.std(),
        pos=float((s > 0).mean()), p10=np.percentile(s, 10), p90=np.percentile(s, 90),
        ac1=float(np.corrcoef(s[:-1], s[1:])[0, 1]) if len(s) > 2 else float('nan'),
    )


def main():
    venues = sorted({Path(f).stem.split('_')[1] for f in glob.glob(str(DATA/'fund_*_*.json'))})
    for coin in ('BTC', 'ETH'):
        sm = {}
        for v in venues:
            r = load(v, coin)
            if r is not None:
                sm[v] = r
        if len(sm) < 2:
            print(f'\n{coin}: <2 venues with data ({list(sm)}) — skip'); continue
        grid, aligned = grid_align(sm)
        if grid is None:
            print(f'\n{coin}: no time overlap across {list(sm)}'); continue
        days = (grid[-1]-grid[0])/86400
        print(f'\n{"="*70}\n{coin}  ({len(grid)} hourly points, {days:.1f}d overlap, venues={list(aligned)})\n{"="*70}')
        print('  current annualized:  ' + '  '.join(f'{v}={aligned[v][-1]*100:.1f}%' for v in aligned))
        vs = list(aligned)
        print(f'\n  {"pair":<24}{"mean":>8}{"mean|.|":>9}{"std":>8}{"%pos":>7}{"persist(ac1)":>13}')
        for i in range(len(vs)):
            for j in range(i+1, len(vs)):
                a, b = vs[i], vs[j]
                sp = aligned[a] - aligned[b]
                st = stats(sp)
                print(f'  {a+"-"+b:<24}{st["mean"]*100:>7.1f}%{st["absmean"]*100:>8.1f}%'
                      f'{st["std"]*100:>7.1f}%{st["pos"]*100:>6.0f}%{st["ac1"]:>13.2f}')
        print('\n  Read: mean = static long-A/short-B carry (gross annual, pre-cost).')
        print('        mean|.| = dynamic carry if you take the rich side each period (funding')
        print('        is announced ahead). %pos near 50 = sign flips a lot (needs dynamic).')
        print('        persist(ac1) near 1 = spread is sticky => predictable/tradeable.')


if __name__ == '__main__':
    main()
