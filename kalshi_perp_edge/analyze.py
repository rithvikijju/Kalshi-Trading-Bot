"""
Characterize the Kalshi-perp vs Coinbase-spot BASIS: is there a tradeable signal?

Loads perp + spot 1-min series, builds basis (in bps of price), and reports:
  - clean spread distribution (the cost floor)
  - basis distribution, autocorrelation, OU mean-reversion half-life
  - lead-lag: does Coinbase lead the Kalshi perp? (cross-corr of 1-min returns)
  - does basis predict next-minute perp return? (the core signal test)
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COIN = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
MULT = {'BTC': 1e4, 'ETH': 1e3}[COIN]


def load():
    perp = pd.read_parquet(DATA / f'perp_{COIN}.parquet')
    spot = pd.read_parquet(DATA / f'spot_{COIN}.parquet')
    # clean perp: valid bid<ask, positive, sane spread (< 1% of price)
    perp = perp[(perp.bid > 0) & (perp.ask > 0) & (perp.ask >= perp.bid)].copy()
    perp['mid'] = (perp.bid + perp.ask) / 2
    perp['spread'] = perp.ask - perp.bid
    perp = perp[perp.spread < 0.01 * perp.mid]          # drop empty-book sentinel rows
    perp['perp_impl'] = perp['mid'] * MULT              # implied underlying from perp mid
    df = pd.merge(perp[['ts', 'mid', 'bid', 'ask', 'spread', 'perp_impl', 'vol', 'oi', 'close']],
                  spot, on='ts', how='inner').sort_values('ts').reset_index(drop=True)
    df['basis'] = df.perp_impl - df.spot                # $ basis in underlying terms
    df['basis_bp'] = df.basis / df.spot * 1e4           # basis in bps
    df['spread_bp'] = df.spread / df.mid * 1e4
    return df


def ou_halflife(x):
    """Half-life of mean reversion from AR(1): x_t - m = phi (x_{t-1}-m)."""
    x = pd.Series(x).dropna().values
    if len(x) < 50:
        return np.nan, np.nan
    dx = np.diff(x)
    lag = x[:-1]
    A = np.vstack([lag, np.ones(len(lag))]).T
    beta, _ = np.linalg.lstsq(A, dx, rcond=None)[0], None
    b = beta[0]                                          # dx = b*lag + c ; phi = 1+b
    if b >= 0:
        return np.inf, 1 + b
    return -np.log(2) / np.log(1 + b), 1 + b


def main():
    df = load()
    n = len(df)
    print(f'\n{"="*64}\n{COIN}: {n} aligned 1-min obs '
          f'({n/1440:.1f} days)\n{"="*64}')

    sp = df.spread_bp
    print(f'\nSPREAD (cost floor):  median {sp.median():.2f}bp  mean {sp.mean():.2f}bp  '
          f'p90 {sp.quantile(.9):.2f}bp')
    print(f'  half-spread (taker cost one-way) median {sp.median()/2:.2f}bp')
    print(f'  vol/min: median {df.vol.median():.0f} ct  OI median {df.oi.median():.0f} ct')

    b = df.basis_bp
    print(f'\nBASIS (perp_impl - spot, bps):  mean {b.mean():.2f}  std {b.std():.2f}  '
          f'p5 {b.quantile(.05):.2f}  p95 {b.quantile(.95):.2f}')
    print(f'  |basis| > spread fraction: {(b.abs() > sp).mean()*100:.0f}%  '
          f'(basis exceeds round-trip cost => potentially tradeable)')

    hl, phi = ou_halflife(b)
    ac1 = b.autocorr(1)
    print(f'\nMEAN REVERSION:  AR(1) phi {phi:.3f}  half-life {hl:.1f} min  ac1 {ac1:.3f}')

    # lead-lag: corr( spot return at t-k , perp return at t )
    df['r_perp'] = np.log(df.perp_impl).diff()
    df['r_spot'] = np.log(df.spot).diff()
    print('\nLEAD-LAG  corr(spot_ret[t-k], perp_ret[t]):  (k>0 => spot LEADS perp)')
    for k in (-3, -2, -1, 0, 1, 2, 3):
        c = df.r_spot.shift(k).corr(df.r_perp)
        tag = ' <= spot leads' if k > 0 and c > 0 else ''
        print(f'   k={k:+d}: {c:+.3f}{tag}')

    # CORE SIGNAL: does basis_bp predict next-minute perp move toward spot (reversion)?
    # future perp return; if basis>0 (perp rich) expect perp to fall => negative corr.
    df['fwd_perp_ret_bp'] = (np.log(df.perp_impl).shift(-1) - np.log(df.perp_impl)) * 1e4
    valid = df.dropna(subset=['basis_bp', 'fwd_perp_ret_bp'])
    corr = valid.basis_bp.corr(valid.fwd_perp_ret_bp)
    print(f'\nSIGNAL: corr(basis_bp, next-min perp ret) = {corr:+.3f}  '
          f'(negative => rich perp reverts down: tradeable)')
    # conditional: mean fwd return in extreme-basis buckets
    valid = valid.copy()
    valid['bucket'] = pd.qcut(valid.basis_bp, 5, labels=['B1(cheap)', 'B2', 'B3', 'B4', 'B5(rich)'])
    g = valid.groupby('bucket', observed=True).fwd_perp_ret_bp.agg(['mean', 'count'])
    print('  next-min perp return (bp) by basis quintile:')
    for idx, row in g.iterrows():
        print(f'    {idx:<11} mean {row["mean"]:+.3f}bp  n={int(row["count"])}')

    df.to_parquet(DATA / f'merged_{COIN}.parquet')


if __name__ == '__main__':
    main()
