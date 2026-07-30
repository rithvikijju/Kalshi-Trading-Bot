"""
Robustness battery for the perp lead-lag scalp. Answers the questions that decide
whether the lag=0 edge is real-but-latency-bound vs an artifact:

  1. BREAK-EVEN CAPTURE FRACTION. Gross catch-up >> spread, so a taker only needs to
     capture a fraction phi* of the [t,t+1] move to clear the round-trip spread.
     phi* small (and the implied max execution latency large) => robustly capturable.
  2. JUMP-CONDITIONED. The lag is largest right after big spot moves (less latency-
     critical, fewer trades). Does edge concentrate there?
  3. TIME STABILITY. Split-half: is the signal present in BOTH halves of the sample?
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COIN = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
MULT = {'BTC': 1e4, 'ETH': 1e3}[COIN]


def load():
    df = pd.read_parquet(DATA / f'merged_{COIN}.parquet').reset_index(drop=True)
    df['perp_mid'] = df.mid * MULT
    df['catchup_bp'] = (np.log(df.perp_mid.shift(-1)) - np.log(df.perp_mid)) * 1e4  # [t,t+1] perp move
    df['rt_spread_bp'] = df.spread / df.mid * 1e4                                    # round-trip cost
    df['spot_ret_bp'] = (np.log(df.spot) - np.log(df.spot.shift(1))) * 1e4           # [t-1,t] spot move
    return df


def main():
    df = load()
    print(f'{COIN}: {len(df)} bars\n')

    # ---- 1. break-even capture fraction --------------------------------------
    # Trade only when |basis|>=thr; gross = signed catch-up in trade direction.
    print('BREAK-EVEN CAPTURE FRACTION  (taker needs to capture phi* of catch-up to clear spread)')
    print(f'{"thr":>5} {"n":>6} {"gross bp":>9} {"rt_spread":>10} {"phi*":>7} {"max latency*":>13}')
    for thr in (3, 5, 8, 12):
        m = df.basis_bp.abs() >= thr
        sub = df[m & df.catchup_bp.notna()]
        # signed gross capture if we trade toward reversion: long when basis<0 => +catchup
        sign = -np.sign(sub.basis_bp)
        gross = (sign * sub.catchup_bp).mean()
        rt = sub.rt_spread_bp.mean()
        phi = rt / gross if gross > 0 else np.nan
        # if catch-up is ~linear across the 60s bar, capturing (1-tau/60) => break-even tau:
        max_tau = (1 - phi) * 60 if phi == phi else np.nan
        print(f'{thr:>5} {len(sub):>6} {gross:>9.2f} {rt:>10.2f} {phi:>7.2f} {max_tau:>10.0f} s')

    # ---- 2. jump-conditioned -------------------------------------------------
    print('\nJUMP-CONDITIONED  (trade in direction of the last-minute spot move; hold 1 bar)')
    print(f'{"|spot move| >=":>14} {"n":>6} {"gross bp":>9} {"net(-rtspread)":>15} {"win%":>6}')
    for jthr in (2, 5, 10, 20, 40):
        m = df.spot_ret_bp.abs() >= jthr
        sub = df[m & df.catchup_bp.notna()].copy()
        sign = np.sign(sub.spot_ret_bp)            # perp should follow spot
        gross = sign * sub.catchup_bp
        net = gross - sub.rt_spread_bp
        print(f'{jthr:>12}bp {len(sub):>6} {gross.mean():>9.2f} {net.mean():>15.2f} '
              f'{(net>0).mean()*100:>5.0f}%')

    # ---- 3. time stability (split-half) --------------------------------------
    print('\nTIME STABILITY  (lag=0 event-study net bp/trade, thr=5, H=1, by sample half)')
    half = len(df) // 2
    for name, seg in [('first half', df.iloc[:half]), ('second half', df.iloc[half:])]:
        s = seg[seg.basis_bp.abs() >= 5].copy()
        sign = -np.sign(s.basis_bp)
        net = (sign * s.catchup_bp) - s.rt_spread_bp
        net = net.dropna()
        print(f'  {name:<12} n={len(net):>5}  net {net.mean():>6.2f}bp  '
              f'win {(net>0).mean()*100:>3.0f}%')


if __name__ == '__main__':
    main()
