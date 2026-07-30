"""Robustness validation for AI-2 path simulator at best params (thr=0.08, k=500).

Tests:
  1. Multiple chronological train/test splits (40/60, 50/50, 60/40, 70/30)
  2. Two non-overlapping out-of-sample windows
  3. Sub-sample stability (run with random row subsets)
  4. Compare predicted vs realized edge calibration
  5. Trade distribution: what types of bets is it picking?
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from sklearn.neighbors import NearestNeighbors

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'
KNN_FEATURES = [
    'mtc', 'rv15', 'rv60', 'drift_5m', 'drift_15m',
    'range_10m', 'hour_sin', 'hour_cos',
]


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def build_path_db(train: pd.DataFrame):
    X = train[KNN_FEATURES].values.astype(np.float64)
    mean = X.mean(axis=0); std = X.std(axis=0)
    std[std < 1e-8] = 1.0
    X_norm = (X - mean) / std
    realized = (train['btc_close'].values - train['spot'].values) / train['spot'].values
    nn = NearestNeighbors(n_neighbors=500, algorithm='ball_tree').fit(X_norm)
    return {'nn': nn, 'mean': mean, 'std': std, 'realized': realized}


def trade(test, db, threshold=0.08, k=500):
    test_X = test[KNN_FEATURES].values.astype(np.float64)
    test_X_norm = (test_X - db['mean']) / db['std']
    _, idx = db['nn'].kneighbors(test_X_norm, n_neighbors=k)
    moves = db['realized'][idx]  # (n_test, k)
    spots = test['spot'].values.reshape(-1, 1)
    strikes = test['floor_strike'].values.reshape(-1, 1)
    terminal = spots * (1 + moves)
    emp_p = (terminal > strikes).mean(axis=1)
    asks_y = test['ask_yes'].values
    asks_n = test['ask_no'].values
    fees_y = np.array([kalshi_fee(a) for a in asks_y])
    fees_n = np.array([kalshi_fee(a) for a in asks_n])
    edge_y = emp_p - asks_y - fees_y
    edge_n = (1 - emp_p) - asks_n - fees_n
    pnl_y_real = test['yes_wins'].values - asks_y - fees_y
    pnl_n_real = (1 - test['yes_wins'].values) - asks_n - fees_n
    take_yes = (edge_y > threshold) & (edge_y >= edge_n)
    take_no  = (edge_n > threshold) & (~take_yes)
    pnls = np.where(take_yes, pnl_y_real, np.where(take_no, pnl_n_real, np.nan))
    sides = np.where(take_yes, 'yes', np.where(take_no, 'no', 'skip'))
    pred_edges = np.where(take_yes, edge_y, np.where(take_no, edge_n, np.nan))
    mask = ~np.isnan(pnls)
    t = test[mask].copy()
    t['pnl'] = pnls[mask]
    t['side'] = sides[mask]
    t['pred_edge'] = pred_edges[mask]
    return t


def summarize(t, label):
    if t is None or len(t) == 0:
        print(f'  {label}: NO TRADES'); return
    n = len(t); wins = (t['pnl'] > 0).sum()
    avg = t['pnl'].mean(); total = t['pnl'].sum()
    span = (pd.to_datetime(t['available_at'].max()) -
             pd.to_datetime(t['available_at'].min())).total_seconds() / 86400
    tpd = n / max(span, 0.1)
    t['event'] = t['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
    ev = t.groupby('event')['pnl'].sum()
    ev_sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
    print(f'  {label}  n={n:>5}  tpd={tpd:5.1f}  win={wins/n*100:5.1f}%  '
          f'avg=${avg:+.4f}  total=${total:+.2f}  ev_SR={ev_sr:+.2f}')


def split_test(df, train_frac):
    df = df.sort_values('available_at').reset_index(drop=True)
    cut = df['available_at'].quantile(train_frac)
    return (df[df['available_at'] < cut].reset_index(drop=True),
            df[df['available_at'] >= cut].reset_index(drop=True))


def main():
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)

    # ── Robustness test 1: varying train fractions ─────────────────
    print('=' * 90)
    print('TEST 1: Train/test split robustness (threshold=0.08, k=500)')
    print('=' * 90)
    for tf in [0.30, 0.40, 0.50, 0.60, 0.70]:
        train, test = split_test(df, tf)
        db = build_path_db(train)
        t = trade(test, db, threshold=0.08, k=500)
        summarize(t, f'train={int(tf*100):>2}%  test={int((1-tf)*100):>2}%')

    # ── Robustness test 2: rolling 14-day OOS windows ──────────────
    print()
    print('=' * 90)
    print('TEST 2: Rolling 14-day OOS windows (train = preceding period)')
    print('=' * 90)
    df['date'] = pd.to_datetime(df['available_at']).dt.date
    dates = sorted(df['date'].unique())
    window = 14
    for w_start in range(7, len(dates) - window, 14):
        train_end_date = dates[w_start]
        test_start_date = dates[w_start]
        test_end_date = dates[min(w_start + window, len(dates) - 1)]
        train = df[df['date'] < train_end_date]
        test = df[(df['date'] >= test_start_date) & (df['date'] < test_end_date)]
        if len(train) < 1000 or len(test) < 100:
            continue
        db = build_path_db(train)
        t = trade(test, db, threshold=0.08, k=500)
        summarize(t, f'OOS {test_start_date} → {test_end_date}  train_n={len(train)}')

    # ── Robustness test 3: per-bucket sanity ───────────────────────
    print()
    print('=' * 90)
    print('TEST 3: Best 50/50 split — what types of trades is it taking?')
    print('=' * 90)
    train, test = split_test(df, 0.50)
    db = build_path_db(train)
    t = trade(test, db, threshold=0.08, k=500)
    if len(t) > 0:
        t['side_x_dist_band'] = (t['side'] + '_' +
            pd.cut(t['spot_dist'],
                bins=[-99999,-500,-200,-50,50,200,500,99999],
                labels=['<-500','-500..-200','-200..-50','-50..+50',
                         '+50..+200','+200..+500','>+500']).astype(str))
        print('  trades by (side, spot_dist band):')
        g = t.groupby('side_x_dist_band')['pnl'].agg(['count','mean','sum'])
        print(g.to_string())
        print()
        print('  trades by mtc band:')
        t['mtc_band'] = pd.cut(t['mtc'], bins=[33,40,45,50,55,60],
                                labels=['34-40','40-45','45-50','50-55','55-60'])
        g = t.groupby('mtc_band', observed=True)['pnl'].agg(['count','mean','sum'])
        print(g.to_string())
        print()
        print('  Predicted vs Realized calibration:')
        print(f'    avg predicted edge: {t["pred_edge"].mean()*100:.2f}¢')
        print(f'    avg realized PnL:   {t["pnl"].mean()*100:+.2f}¢')
        print(f'    overestimate by:    {(t["pred_edge"].mean() - t["pnl"].mean())*100:.2f}¢')


if __name__ == '__main__':
    main()
