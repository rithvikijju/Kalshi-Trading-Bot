"""AI-2 refined: only trade the mtc 55-60 (event-open) regime where the
path simulator showed concentrated edge. Cross-validate.

Hypothesis: open-of-event Kalshi quotes are slow to reflect new
information about BTC's current trajectory. The KNN historical-path lookup
finds 'in similar past openings, BTC moved this much' and identifies
strikes the market is mispricing at open.
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from sklearn.neighbors import NearestNeighbors

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'
KNN_FEATURES = ['mtc', 'rv15', 'rv60', 'drift_5m', 'drift_15m',
                  'range_10m', 'hour_sin', 'hour_cos']


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def build_db(train):
    X = train[KNN_FEATURES].values.astype(np.float64)
    mean, std = X.mean(0), X.std(0); std[std < 1e-8] = 1.0
    realized = (train['btc_close'].values - train['spot'].values) / train['spot'].values
    nn = NearestNeighbors(n_neighbors=500, algorithm='ball_tree').fit((X - mean) / std)
    return {'nn': nn, 'mean': mean, 'std': std, 'realized': realized}


def trade(test, db, threshold=0.08, k=500):
    X = (test[KNN_FEATURES].values - db['mean']) / db['std']
    _, idx = db['nn'].kneighbors(X, n_neighbors=k)
    moves = db['realized'][idx]
    spots = test['spot'].values.reshape(-1, 1)
    strikes = test['floor_strike'].values.reshape(-1, 1)
    emp_p = ((spots * (1 + moves)) > strikes).mean(axis=1)
    ay, an = test['ask_yes'].values, test['ask_no'].values
    fy = np.array([kalshi_fee(a) for a in ay])
    fn = np.array([kalshi_fee(a) for a in an])
    ey = emp_p - ay - fy
    en = (1 - emp_p) - an - fn
    pny = test['yes_wins'].values - ay - fy
    pnn = (1 - test['yes_wins'].values) - an - fn
    yes_mask = (ey > threshold) & (ey >= en)
    no_mask  = (en > threshold) & ~yes_mask
    pnls = np.where(yes_mask, pny, np.where(no_mask, pnn, np.nan))
    sides = np.where(yes_mask, 'yes', np.where(no_mask, 'no', 'skip'))
    asks = np.where(yes_mask, ay, np.where(no_mask, an, np.nan))
    mask = ~np.isnan(pnls)
    out = test[mask].copy()
    out['pnl'] = pnls[mask]; out['side'] = sides[mask]; out['paid'] = asks[mask]
    out['pred_edge'] = np.where(yes_mask, ey, np.where(no_mask, en, np.nan))[mask]
    return out


def split(df, frac):
    df = df.sort_values('available_at').reset_index(drop=True)
    cut = df['available_at'].quantile(frac)
    return df[df['available_at'] < cut].reset_index(drop=True), \
           df[df['available_at'] >= cut].reset_index(drop=True)


def summarize(t, label):
    if len(t) == 0:
        print(f'  {label}: 0 trades'); return
    n = len(t); wins = (t['pnl'] > 0).sum()
    avg = t['pnl'].mean(); total = t['pnl'].sum()
    span = (pd.to_datetime(t['available_at'].max()) -
             pd.to_datetime(t['available_at'].min())).total_seconds() / 86400
    tpd = n / max(span, 0.1)
    t['event'] = t['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
    ev = t.groupby('event')['pnl'].sum()
    ev_sr = ev.mean() / ev.std() * math.sqrt(8760) if ev.std() > 0 else 0
    daily = t.set_index('available_at').resample('1D')['pnl'].sum()
    daily_sr = daily.mean() / daily.std() * math.sqrt(365) if daily.std() > 0 else 0
    print(f'  {label}: n={n:>4} tpd={tpd:5.1f} win={wins/n*100:4.1f}% '
          f'avg=${avg:+.4f} total=${total:+7.2f} ev_SR={ev_sr:+5.2f} '
          f'daily_SR={daily_sr:+5.2f}')


def main():
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)

    print('=' * 100)
    print('AI-2 REFINED — restrict to mtc 55-60 (event-open) window, threshold=0.08, k=500')
    print('=' * 100)

    # Test 1: 50/50 split, restrict TEST to mtc 55-60
    print('\nTEST 1: Restrict test set to mtc 55-60 only')
    train, test = split(df, 0.50)
    test_open = test[test['mtc'] >= 55].reset_index(drop=True)
    db = build_db(train)
    summarize(trade(test_open, db, 0.08, 500), 'test-only filter mtc 55-60')
    summarize(trade(test, db, 0.08, 500), 'full test set (baseline)')

    # Test 2: restrict BOTH train and test to mtc 55-60
    print('\nTEST 2: Restrict BOTH train AND test to mtc 55-60')
    train_open = train[train['mtc'] >= 55].reset_index(drop=True)
    db_open = build_db(train_open)
    summarize(trade(test_open, db_open, 0.08, 500), 'train+test mtc 55-60')

    # Test 3: rolling 14d windows, restricted to mtc 55-60
    print('\nTEST 3: Rolling 14-day OOS windows, restricted to mtc 55-60')
    df['date'] = pd.to_datetime(df['available_at']).dt.date
    dates = sorted(df['date'].unique())
    window = 14
    cumulative_pnl = 0.0
    n_pos = 0; n_total = 0
    for w_start in range(7, len(dates) - window, 14):
        td_end = dates[w_start]
        te_end = dates[min(w_start + window, len(dates) - 1)]
        train = df[df['date'] < td_end]
        test  = df[(df['date'] >= td_end) & (df['date'] < te_end)]
        train_o = train[train['mtc'] >= 55]
        test_o  = test[test['mtc'] >= 55]
        if len(train_o) < 1000 or len(test_o) < 50: continue
        db = build_db(train_o)
        t = trade(test_o, db, 0.08, 500)
        summarize(t, f'{td_end} → {te_end}  train_n={len(train_o)}')
        n_total += 1
        if len(t) > 0:
            cumulative_pnl += t['pnl'].sum()
            if t['pnl'].sum() > 0: n_pos += 1
    print(f'\n  Cumulative across {n_total} windows: ${cumulative_pnl:+.2f}  '
          f'(positive in {n_pos}/{n_total})')

    # Test 4: vary threshold to find robust sweet spot
    print('\nTEST 4: Threshold sweep with mtc 55-60 + 50/50 split')
    train, test = split(df, 0.50)
    train_o = train[train['mtc'] >= 55]; test_o = test[test['mtc'] >= 55]
    db = build_db(train_o)
    for thr in [0.03, 0.05, 0.08, 0.10, 0.12, 0.15]:
        t = trade(test_o, db, thr, 500)
        summarize(t, f'threshold={thr}')

    # Test 5: what's the predicted-vs-realized calibration look like
    # in the refined regime?
    print('\nTEST 5: Calibration plot (predicted edge bands → realized PnL)')
    train_o = train[train['mtc'] >= 55]; test_o = test[test['mtc'] >= 55]
    db = build_db(train_o)
    t = trade(test_o, db, 0.0, 500)  # take ALL trades (no threshold) so we see calibration
    t['pred_band'] = pd.cut(t['pred_edge'],
        bins=[-1, 0, 0.05, 0.10, 0.20, 1], labels=['<0','0-5','5-10','10-20','>20'])
    g = t.groupby('pred_band', observed=True)['pnl'].agg(['count','mean'])
    print('  predicted-edge band  →  realized PnL/trade:')
    print(g.to_string())


if __name__ == '__main__':
    main()
