"""
Timestamp-alignment diagnostic + CORRECTED lead-lag.

Coinbase 1-min candles are labeled by bucket START (close = price at ts+60).
Kalshi perp candles use end_period_ts = bucket END (close = price at ts).
=> correct same-instant pairing is  coinbase_ts + 60 == kalshi_ts.

This scans spot time-shifts and reports, for each, the contemporaneous return
correlation. The shift that MAXIMIZES corr is the true alignment; residual lead-lag
(if any) shows up as a peak at a nonzero shift AFTER applying the +60s fix.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COIN = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
MULT = {'BTC': 1e4, 'ETH': 1e3}[COIN]


def main():
    perp = pd.read_parquet(DATA / f'perp_{COIN}.parquet')
    spot = pd.read_parquet(DATA / f'spot_{COIN}.parquet')
    perp = perp[(perp.bid > 0) & (perp.ask > 0) & (perp.ask >= perp.bid)].copy()
    perp['mid'] = (perp.bid + perp.ask) / 2
    perp = perp[(perp.ask - perp.bid) < 0.01 * perp.mid]
    perp['perp_impl'] = perp['mid'] * MULT

    print(f'{COIN}: contemporaneous corr(spot_ret, perp_ret) vs spot time-shift')
    print('(shift = seconds ADDED to coinbase ts before merge; +60 = the convention fix)')
    best = None
    for shift in range(-180, 181, 60):
        s = spot.copy(); s['ts'] = s.ts + shift
        df = pd.merge(perp[['ts', 'perp_impl']], s, on='ts', how='inner').sort_values('ts')
        if len(df) < 100:
            continue
        rp = np.log(df.perp_impl).diff()
        rs = np.log(df.spot).diff()
        c = rp.corr(rs)
        flag = ''
        if best is None or c > best[1]:
            best = (shift, c)
        print(f'  shift {shift:+4d}s  n={len(df):>6}  corr={c:+.3f}')
    print(f'  => best contemporaneous alignment at shift {best[0]:+d}s (corr {best[1]:.3f})')

    # With the CORRECT alignment, redo the k-lag lead-lag to see residual structure.
    s = spot.copy(); s['ts'] = s.ts + 60
    df = pd.merge(perp[['ts', 'perp_impl', 'mid', 'bid', 'ask']], s, on='ts', how='inner').sort_values('ts').reset_index(drop=True)
    df['rp'] = np.log(df.perp_impl).diff()
    df['rs'] = np.log(df.spot).diff()
    df['basis_bp'] = (df.perp_impl / df.spot - 1) * 1e4
    print(f'\nCORRECTED (shift +60s) lead-lag corr(spot_ret[t-k], perp_ret[t]):')
    for k in (-2, -1, 0, 1, 2):
        c = df.rs.shift(k).corr(df.rp)
        print(f'  k={k:+d}: {c:+.3f}' + ('  <= spot leads perp' if k > 0 else ''))
    df['fwd'] = (np.log(df.perp_impl).shift(-1) - np.log(df.perp_impl)) * 1e4
    print(f'\nCORRECTED signal corr(basis_bp, next-min perp ret) = '
          f'{df.basis_bp.corr(df.fwd):+.3f}')
    print(f'CORRECTED basis: mean {df.basis_bp.mean():.2f}bp std {df.basis_bp.std():.2f}bp '
          f'ac1 {df.basis_bp.autocorr(1):.3f}')
    df.to_parquet(DATA / f'merged_aligned_{COIN}.parquet')


if __name__ == '__main__':
    main()
