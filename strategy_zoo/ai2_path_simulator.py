"""AI-2: Empirical generative path simulator.

Instead of assuming Gaussian (Black-Scholes), sample what BTC has *actually
done* in similar past situations. For each (current_spot, mtc, vol-regime,
drift-regime), find KNN matches in the training period, get their realized
terminal spot, and compute empirical P(BTC > strike).

Trade when empirical-prob − Kalshi ask − fee > threshold.

Honesty checks:
  - Strict chronological split (no KNN match from the future)
  - Train half = first 35d (used to build the path database)
  - Test half = last 30d (queries use only training paths, evaluated on real outcomes)
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from sklearn.neighbors import NearestNeighbors

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'

KNN_FEATURES = [
    'mtc',          # time to close (must match closely)
    'rv15',         # short vol
    'rv60',         # longer vol
    'drift_5m',     # recent direction
    'drift_15m',
    'range_10m',
    'hour_sin', 'hour_cos',
]


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def split_train_test(df):
    df = df.sort_values('available_at').reset_index(drop=True)
    cutoff = df['available_at'].quantile(0.50)
    train = df[df['available_at'] < cutoff].reset_index(drop=True)
    test  = df[df['available_at'] >= cutoff].reset_index(drop=True)
    return train, test


def build_path_db(train: pd.DataFrame):
    """For each training row, record:
       - starting features (those used for KNN matching)
       - the realized fractional move = (btc_close - spot) / spot
    """
    X = train[KNN_FEATURES].values.astype(np.float64)
    mean = X.mean(axis=0); std = X.std(axis=0)
    std[std < 1e-8] = 1.0
    X_norm = (X - mean) / std
    realized = (train['btc_close'].values - train['spot'].values) / train['spot'].values
    nn = NearestNeighbors(n_neighbors=200, algorithm='ball_tree').fit(X_norm)
    return {'nn': nn, 'mean': mean, 'std': std, 'realized': realized,
            'train_spots': train['spot'].values}


def empirical_prob_above(db, query_features, current_spot, strike, k=200):
    """Query KNN, look at how often (spot + realized_move) > strike."""
    x = (query_features - db['mean']) / db['std']
    _, idx = db['nn'].kneighbors(x.reshape(1, -1), n_neighbors=min(k, len(db['realized'])))
    moves = db['realized'][idx[0]]
    terminal = current_spot * (1 + moves)
    return (terminal > strike).mean()


def backtest(threshold=0.02, k=200):
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)
    train, test = split_train_test(df)
    print(f'Train: {len(train):,} rows  Test: {len(test):,} rows')
    db = build_path_db(train)

    test_X = test[KNN_FEATURES].values.astype(np.float64)
    trades = []
    for i in range(len(test)):
        row = test.iloc[i]
        feat = test_X[i]
        emp_p = empirical_prob_above(db, feat, row['spot'], row['floor_strike'], k=k)
        ask_y = row['ask_yes']; ask_n = row['ask_no']
        fee_y = kalshi_fee(ask_y); fee_n = kalshi_fee(ask_n)
        edge_y = emp_p - ask_y - fee_y
        edge_n = (1 - emp_p) - ask_n - fee_n
        if edge_y > threshold and edge_y >= edge_n:
            trades.append({
                'ts': row['available_at'], 'market': row['market_ticker'],
                'side': 'yes', 'paid': ask_y, 'fee': fee_y,
                'emp_prob': emp_p, 'edge_pred': edge_y,
                'settles': row['yes_wins'],
                'pnl': row['yes_wins'] - ask_y - fee_y,
            })
        elif edge_n > threshold:
            trades.append({
                'ts': row['available_at'], 'market': row['market_ticker'],
                'side': 'no', 'paid': ask_n, 'fee': fee_n,
                'emp_prob': 1 - emp_p, 'edge_pred': edge_n,
                'settles': 1 - row['yes_wins'],
                'pnl': (1 - row['yes_wins']) - ask_n - fee_n,
            })

    if not trades:
        print(f'  threshold={threshold} → no trades.')
        return None
    t = pd.DataFrame(trades)
    n = len(t); wins = (t['pnl'] > 0).sum()
    avg_pnl = t['pnl'].mean()
    total = t['pnl'].sum()
    span_d = (pd.to_datetime(t['ts'].max()) - pd.to_datetime(t['ts'].min())).total_seconds() / 86400
    tpd = n / max(span_d, 0.1)
    # event-level for honest Sharpe
    t['event'] = t['market'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
    ev = t.groupby('event')['pnl'].sum()
    ev_sr = ev.mean() / ev.std() * math.sqrt(8760) if ev.std() > 0 else 0
    print()
    print(f'=== AI-2 path-simulator (threshold={threshold}, k={k}) ===')
    print(f'  Trades: {n:,}  ({tpd:.1f}/day over {span_d:.0f}d OOS)')
    print(f'  Win rate: {wins/n*100:.1f}%')
    print(f'  Avg PnL: ${avg_pnl:+.4f}  Total: ${total:+.2f}')
    print(f'  Event-level Sharpe (annualized): {ev_sr:+.2f}')
    print(f'  Predicted edge mean: {t["edge_pred"].mean()*100:.2f}¢  '
          f'realized PnL mean: {avg_pnl*100:+.2f}¢')
    print(f'  Edge prediction quality: '
          f'{"matched" if abs(t["edge_pred"].mean() - avg_pnl) < 0.01 else "model OVERESTIMATES"}')
    return t


if __name__ == '__main__':
    print('=' * 80)
    print('AI-2: Empirical generative path simulator (KNN bootstrap)')
    print('=' * 80)
    for thr in [0.01, 0.02, 0.05, 0.08]:
        for k in [50, 200, 500]:
            print(f'\n--- threshold={thr}, k={k} ---')
            backtest(threshold=thr, k=k)
