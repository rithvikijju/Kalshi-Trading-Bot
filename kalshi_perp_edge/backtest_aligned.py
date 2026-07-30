"""
HONEST backtest on timestamp-corrected data (coinbase_ts+60 == kalshi_ts).
After removing the 60s misalignment artifact, the minute-scale lead-lag is gone;
this measures whatever RESIDUAL basis-mean-reversion edge remains, with real
bid/ask taker fills and zero fee. If net<=0 after spread, the 1-min strategy is dead
and the only hope is sub-minute (tick) lag — which the forward capture will test.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COIN = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
MULT = {'BTC': 1e4, 'ETH': 1e3}[COIN]


def main():
    df = pd.read_parquet(DATA / f'merged_aligned_{COIN}.parquet').reset_index(drop=True)
    df['bidU'] = df.bid * MULT
    df['askU'] = df.ask * MULT
    df['spread_bp'] = (df.askU - df.bidU) / df.spot * 1e4
    days = (df.ts.iloc[-1] - df.ts.iloc[0]) / 86400
    print(f'{COIN} ALIGNED: {len(df)} bars, {days:.1f}d | basis std {df.basis_bp.std():.2f}bp '
          f'ac1 {df.basis_bp.autocorr(1):.2f} | spread med {df.spread_bp.median():.2f}bp\n')

    # event study: at t, basis rich/cheap -> taker fade, exit H bars later opposite side
    bid = df.bidU.values; ask = df.askU.values; spot = df.spot.values; basis = df.basis_bp.values
    n = len(df)
    print(f'{"thr":>5} {"H":>3} | {"n":>6} {"gross bp":>9} {"net bp":>8} {"win%":>6}')
    for thr in (2, 3, 4, 5):
        for H in (1, 2, 3):
            g = []; ng = []
            for t in range(n - H):
                s = basis[t]
                if abs(s) < thr or np.isnan(s):
                    continue
                if s <= -thr:                      # cheap -> buy ask, sell bid later
                    gross = (df.perp_impl.values[t + H] - df.perp_impl.values[t])  # mid-based gross
                    net = (bid[t + H] - ask[t])
                else:                              # rich -> SELL: hit bid now, buy ask later
                    gross = (df.perp_impl.values[t] - df.perp_impl.values[t + H])
                    net = (bid[t] - ask[t + H])
                g.append(gross / spot[t] * 1e4); ng.append(net / spot[t] * 1e4)
            if len(ng) < 20:
                continue
            g = np.array(g); ng = np.array(ng)
            print(f'{thr:>5} {H:>3} | {len(ng):>6} {g.mean():>9.3f} {ng.mean():>8.3f} '
                  f'{(ng>0).mean()*100:>5.0f}%')


if __name__ == '__main__':
    main()
