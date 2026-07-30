"""AI-2 FINAL TEST: threshold=0.15 across rolling 14-day windows.
This is the make-or-break test. If 3+ of 4 windows are positive AND
cumulative PnL > 0, AI-2 is a real strategy.
"""
from __future__ import annotations
import math, sys
import numpy as np, pandas as pd
from sklearn.neighbors import NearestNeighbors

sys.path.insert(0, '/Users/rithvikijju/edge-bot/strategy_zoo')
from ai2_refined import build_db, trade, kalshi_fee


DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'


def main():
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['available_at']).dt.date

    print('=' * 100)
    print('AI-2 FINAL: threshold=0.15, k=500, mtc-unrestricted, rolling 14d OOS')
    print('=' * 100)

    dates = sorted(df['date'].unique())
    window = 14
    all_trades = []
    n_pos = 0; n_total = 0
    rows = []

    for w_start in range(7, len(dates) - window, 7):  # rolling every 7d (overlap)
        td_end = dates[w_start]
        te_end = dates[min(w_start + window, len(dates) - 1)]
        train = df[df['date'] < td_end]
        test  = df[(df['date'] >= td_end) & (df['date'] < te_end)]
        if len(train) < 1000 or len(test) < 100: continue
        db = build_db(train)
        t = trade(test, db, threshold=0.15, k=500)
        n_total += 1
        if len(t) == 0:
            rows.append([str(td_end), str(te_end), len(train), 0, 0, 0, 0])
            continue
        pnl = t['pnl'].sum()
        wins = (t['pnl'] > 0).sum()
        if pnl > 0: n_pos += 1
        all_trades.append(t)
        ev = t['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
        t['ev'] = ev
        ev_pnl = t.groupby('ev')['pnl'].sum()
        ev_sr = ev_pnl.mean() / ev_pnl.std() * math.sqrt(8760) if ev_pnl.std() > 0 else 0
        rows.append([str(td_end), str(te_end), len(train), len(t),
                      wins/len(t)*100 if len(t) else 0,
                      t['pnl'].mean(), pnl, ev_sr])

    print(f'\n{"start":<12} {"end":<12} {"train":>8} {"n":>5} {"win%":>6} '
          f'{"avg":>8} {"total":>9} {"ev_SR":>7}')
    print('-' * 90)
    for r in rows:
        print(f'{r[0]:<12} {r[1]:<12} {r[2]:>8} {r[3]:>5} '
              f'{r[4]:>5.1f}% '
              f'{r[5]:>+8.4f} {r[6]:>+8.2f} '
              f'{r[7]:>+7.2f}' if len(r) > 7 else
              f'{r[0]:<12} {r[1]:<12} {r[2]:>8} {r[3]:>5} -- no trades')

    if all_trades:
        big = pd.concat(all_trades, ignore_index=True)
        print()
        print(f'Cumulative: ${big["pnl"].sum():+.2f}  '
              f'over {len(big)} trades  '
              f'avg ${big["pnl"].mean():+.4f}/trade')
        print(f'Windows positive: {n_pos}/{n_total}')
        # Honest event-level SR
        big['ev'] = big['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
        ev_pnl = big.groupby('ev')['pnl'].sum()
        ev_sr = ev_pnl.mean() / ev_pnl.std() * math.sqrt(8760) if ev_pnl.std() > 0 else 0
        print(f'Event-level Sharpe (all combined): {ev_sr:+.2f}')

        # Daily/active-day stats
        daily = big.set_index('available_at').resample('1D')['pnl'].sum()
        daily_pos = daily[daily != 0]
        print(f'Active days: {len(daily_pos)}  '
              f'mean ${daily_pos.mean():+.2f}/day  '
              f'positive days: {(daily_pos > 0).sum()}/{len(daily_pos)}')


if __name__ == '__main__':
    main()
