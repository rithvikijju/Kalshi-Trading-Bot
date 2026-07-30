"""
Does Kalshi perp data predict BTC moves at the horizon a CME Micro Bitcoin (MBT) scalp
needs? MBT round-turn cost ~ $1.74/ct on ~$6,500 notional ~= 2.7bp, so a scalp must
predict SIGNED moves bigger than ~2.7bp to be +EV. Micro wiggles are dead.

Uses the 13-day 1-min aligned dataset (perp vol, OI, return, basis vs Coinbase spot).
Honest battery:
  1. Vol-burst persistence: does perp volume/|move| now predict a BIG |move| next? (timing)
  2. Momentum: does perp/spot return now predict SIGNED move next? (continuation vs revert)
  3. OI+price: new-money (dOI w/ price) -> continuation or exhaustion?
  4. A momentum-scalp backtest on the spot proxy, net of MBT cost.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
MBT_COST_BP = 2.7          # round-turn commission+spread on MBT, in bp of notional


def load():
    perp = pd.read_parquet(DATA / 'perp_BTC.parquet')
    perp = perp[(perp.bid > 0) & (perp.ask > 0) & (perp.ask >= perp.bid)].copy()
    perp['mid'] = (perp.bid + perp.ask) / 2
    perp = perp[(perp.ask - perp.bid) < 0.01 * perp.mid]
    perp['perp_impl'] = perp['mid'] * 1e4
    spot = pd.read_parquet(DATA / 'spot_BTC.parquet').copy()
    spot['ts'] = spot.ts + 60                                  # convention fix: coinbase start->end
    d = pd.merge(perp[['ts', 'perp_impl', 'mid', 'bid', 'ask', 'vol', 'oi']], spot,
                 on='ts', how='inner').sort_values('ts').reset_index(drop=True)
    d['basis_bp'] = (d.perp_impl / d.spot - 1) * 1e4
    d['spot_ret'] = np.log(d.spot).diff() * 1e4               # bp, this minute
    d['perp_ret'] = np.log(d.perp_impl).diff() * 1e4
    d['dOI'] = d.oi.diff()
    d['vol_z'] = (d.vol - d.vol.rolling(60).mean()) / (d.vol.rolling(60).std() + 1e-9)
    for h in (1, 3, 5):
        d[f'fwd{h}'] = (np.log(d.spot).shift(-h) - np.log(d.spot)) * 1e4      # signed bp
        d[f'absfwd{h}'] = d[f'fwd{h}'].abs()
    return d.dropna(subset=['spot_ret', 'vol_z']).reset_index(drop=True)


def corr(a, b):
    m = a.notna() & b.notna()
    return a[m].corr(b[m]) if m.sum() > 50 else np.nan


def main():
    d = load()
    print(f'{len(d)} 1-min bars. MBT needs >|{MBT_COST_BP}|bp signed move to clear cost.\n')

    print('1) VOL-BURST PERSISTENCE  corr( feature_now , |fwd move| )')
    for feat in ['vol_z', 'absfwd1']:
        pass
    for h in (1, 3, 5):
        print(f'   h={h}m: |move|~vol_z {corr(d.vol_z, d[f"absfwd{h}"]):+.3f}   '
              f'|move|~|spot_ret_now| {corr(d.spot_ret.abs(), d[f"absfwd{h}"]):+.3f}')

    print('\n2) MOMENTUM  corr( signed ret_now , signed fwd move )  (+=continuation, -=reversion)')
    for h in (1, 3, 5):
        print(f'   h={h}m: fwd~spot_ret {corr(d.spot_ret, d[f"fwd{h}"]):+.3f}   '
              f'fwd~perp_ret {corr(d.perp_ret, d[f"fwd{h}"]):+.3f}   '
              f'fwd~basis {corr(d.basis_bp, d[f"fwd{h}"]):+.3f}')

    print('\n3) OI x PRICE  (sign of dOI*ret_now -> +=new money w/ trend)  corr w/ signed fwd')
    d['newmoney'] = np.sign(d.dOI) * np.sign(d.spot_ret) * d.spot_ret.abs()
    for h in (1, 3, 5):
        print(f'   h={h}m: fwd~newmoney {corr(d.newmoney, d[f"fwd{h}"]):+.3f}')

    print('\n4) MOMENTUM-SCALP BACKTEST on spot proxy (enter dir of last-min move, hold h, '
          f'cost {MBT_COST_BP}bp)')
    print(f'   {"signal":<22}{"h":>3}{"n":>7}{"gross bp":>10}{"net bp":>9}{"win%":>7}')
    for thr in (5, 10, 20):                      # only act on bursts >= thr bp this minute
        for h in (1, 3, 5):
            sig = d[d.spot_ret.abs() >= thr]
            ret = np.sign(sig.spot_ret) * sig[f'fwd{h}']        # momentum: follow the move
            net = ret - MBT_COST_BP
            print(f'   mom |ret|>={thr:>2}bp        {h:>3}{len(sig):>7}'
                  f'{ret.mean():>10.2f}{net.mean():>9.2f}{(net>0).mean()*100:>6.0f}%')
        # reversion variant
    print('   --- reversion variant (fade the move) ---')
    for thr in (10, 20):
        for h in (3, 5):
            sig = d[d.spot_ret.abs() >= thr]
            ret = -np.sign(sig.spot_ret) * sig[f'fwd{h}']
            net = ret - MBT_COST_BP
            print(f'   fade |ret|>={thr:>2}bp       {h:>3}{len(sig):>7}'
                  f'{ret.mean():>10.2f}{net.mean():>9.2f}{(net>0).mean()*100:>6.0f}%')


if __name__ == '__main__':
    main()
