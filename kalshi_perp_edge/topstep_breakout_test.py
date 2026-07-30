"""
Can asymmetric volatility-breakout (small stop / let-run target) on MBT turn the REAL
vol-clustering signal into +EV despite BTC direction being unpredictable?

Uses the perp 1-min OHLC as the MBT proxy (MBT ~ perp ~ spot, cash-settled to BRR).
Trigger: a vol burst (this bar's |ret| >= trig). Enter at close in the break direction
(momentum) AND test the fade direction. Then walk forward up to H bars using intrabar
HIGH/LOW for first-touch of stop vs target. Net of MBT round-turn cost.

If even the best (trigger, stop, target, dir) is <= 0 net, the crypto-micro scalp is dead
and the honest move is to pivot instrument / use Kalshi as a regime filter only.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COST_BP = 2.7


def load():
    p = pd.read_parquet(DATA / 'perp_BTC.parquet')
    p = p[(p.bid > 0) & (p.ask > 0)].copy()
    p['mid'] = (p.bid + p.ask) / 2
    p = p[(p.ask - p.bid) < 0.01 * p.mid]
    # implied BTC OHLC from perp price *1e4
    for col in ('o', 'h', 'l', 'close'):
        p[col + 'U'] = p[col] * 1e4
    p['ret'] = np.log(p.closeU).diff() * 1e4
    return p.dropna().reset_index(drop=True)


def simulate(d, trig, stop_bp, tgt_bp, direction, H):
    """First-touch stop/target over next H bars. direction=+1 momentum, -1 fade."""
    o = d.oU.values; h = d.hU.values; l = d.lU.values; c = d.closeU.values
    ret = d.ret.values
    n = len(d)
    pnls = []
    for t in range(1, n - H):
        if abs(ret[t]) < trig:
            continue
        side = direction * np.sign(ret[t])      # +1 long, -1 short
        entry = c[t]
        if side > 0:
            tgt = entry * (1 + tgt_bp / 1e4); stp = entry * (1 - stop_bp / 1e4)
        else:
            tgt = entry * (1 - tgt_bp / 1e4); stp = entry * (1 + stop_bp / 1e4)
        out = None
        for k in range(t + 1, t + 1 + H):
            hi, lo = h[k], l[k]
            if side > 0:
                hit_stop = lo <= stp; hit_tgt = hi >= tgt
            else:
                hit_stop = hi >= stp; hit_tgt = lo <= tgt
            if hit_stop and hit_tgt:
                out = -stop_bp; break            # assume stop first (conservative)
            if hit_stop:
                out = -stop_bp; break
            if hit_tgt:
                out = tgt_bp; break
        if out is None:
            out = (c[t + H] - entry) / entry * 1e4 * side   # time exit
        pnls.append(out - COST_BP)
    return np.array(pnls)


def main():
    d = load()
    print(f'{len(d)} bars (perp OHLC as MBT proxy). cost {COST_BP}bp.\n')
    print(f'{"dir":>5}{"trig":>6}{"stop":>6}{"tgt":>6}{"H":>4}{"n":>7}{"net bp":>9}{"win%":>7}{"$/trade":>9}')
    best = None
    for direction, dname in ((1, 'mom'), (-1, 'fade')):
        for trig in (10, 20, 40):
            for stop_bp, tgt_bp in ((10, 20), (10, 40), (15, 45), (20, 60)):
                for H in (5, 15, 30):
                    p = simulate(d, trig, stop_bp, tgt_bp, direction, H)
                    if len(p) < 30:
                        continue
                    net = p.mean()
                    # $/trade on MBT: bp * notional(0.1 BTC ~$6500)/1e4
                    dollars = net / 1e4 * 6500
                    row = (dname, trig, stop_bp, tgt_bp, H, len(p), net, (p > 0).mean()*100, dollars)
                    if best is None or net > best[6]:
                        best = row
                    if net > 0:               # only print the (rare) positive ones + track best
                        print(f'{dname:>5}{trig:>6}{stop_bp:>6}{tgt_bp:>6}{H:>4}{len(p):>7}'
                              f'{net:>9.2f}{(p>0).mean()*100:>6.0f}%{dollars:>9.2f}')
    print('\nBEST overall config:')
    b = best
    print(f'  {b[0]} trig{b[1]} stop{b[2]} tgt{b[3]} H{b[4]}: n={b[5]} net={b[6]:.2f}bp '
          f'win={b[7]:.0f}% ${b[8]:.2f}/trade')
    print('  (if best net <= 0 after cost, the crypto-micro breakout scalp is not viable)')


if __name__ == '__main__':
    main()
