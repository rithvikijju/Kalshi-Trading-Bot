"""AI-5: Stacked meta-learner — require LinUCB AND path-sim to agree.

For each test row:
  1. Get LinUCB prediction (predicted PnL for YES + NO arms)
  2. Get path-simulator empirical prob → predicted edge for YES + NO
  3. Trade only if BOTH say positive expected edge on the SAME side,
     AND path-sim predicted edge > 0.15 (the sweet-spot threshold from AI-2)

If the ensemble has REAL information beyond either alone, the
agreement-filtered trades should be more profitable per trade than either
single model.
"""
from __future__ import annotations
import math, sys
import numpy as np, pandas as pd
from sklearn.neighbors import NearestNeighbors

sys.path.insert(0, '/Users/rithvikijju/edge-bot/strategy_zoo')
from ai1_linucb import LinUCB, FEATURE_COLS, standardize_train
from ai2_refined import build_db, KNN_FEATURES

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def train_linucb_on(train, alpha=1.0):
    """Train two LinUCB models on training set."""
    X = train[FEATURE_COLS].values.astype(np.float64)
    mean, std = standardize_train(X)
    X_norm = (X - mean) / std
    X_norm = np.hstack([X_norm, np.ones((len(X_norm), 1))])
    d = X_norm.shape[1]
    yes_arm = LinUCB(d, alpha=alpha)
    no_arm  = LinUCB(d, alpha=alpha)
    for i in range(len(train)):
        yes_arm.update(X_norm[i], train.iloc[i]['pnl_buy_yes'])
        no_arm.update(X_norm[i],  train.iloc[i]['pnl_buy_no'])
    return yes_arm, no_arm, mean, std


def predict_linucb(test, yes_arm, no_arm, mean, std):
    X = test[FEATURE_COLS].values.astype(np.float64)
    X_norm = (X - mean) / std
    X_norm = np.hstack([X_norm, np.ones((len(X_norm), 1))])
    pred_y = np.array([yes_arm.predict(X_norm[i]) for i in range(len(X_norm))])
    pred_n = np.array([no_arm.predict(X_norm[i])  for i in range(len(X_norm))])
    return pred_y, pred_n


def predict_pathsim(test, db, k=500):
    X = (test[KNN_FEATURES].values - db['mean']) / db['std']
    _, idx = db['nn'].kneighbors(X, n_neighbors=k)
    moves = db['realized'][idx]
    spots = test['spot'].values.reshape(-1, 1)
    strikes = test['floor_strike'].values.reshape(-1, 1)
    return ((spots * (1 + moves)) > strikes).mean(axis=1)


def trade_stacked(test, linucb_yes, linucb_no, lin_mean, lin_std, ps_db,
                   path_threshold=0.10, linucb_min=0.005, k=500):
    """Take trade if:
      - path-sim predicted edge on side X > path_threshold
      - LinUCB also predicts positive PnL on same side X (> linucb_min)
    """
    pred_lin_y, pred_lin_n = predict_linucb(test, linucb_yes, linucb_no, lin_mean, lin_std)
    emp_p = predict_pathsim(test, ps_db, k=k)

    ay = test['ask_yes'].values
    an = test['ask_no'].values
    fy = np.array([kalshi_fee(a) for a in ay])
    fn = np.array([kalshi_fee(a) for a in an])
    ps_edge_y = emp_p - ay - fy
    ps_edge_n = (1 - emp_p) - an - fn

    take_yes = (ps_edge_y > path_threshold) & (pred_lin_y > linucb_min)
    take_no  = (ps_edge_n > path_threshold) & (pred_lin_n > linucb_min) & ~take_yes

    pny = test['yes_wins'].values - ay - fy
    pnn = (1 - test['yes_wins'].values) - an - fn
    pnls = np.where(take_yes, pny, np.where(take_no, pnn, np.nan))
    sides = np.where(take_yes, 'yes', np.where(take_no, 'no', 'skip'))
    mask = ~np.isnan(pnls)
    out = test[mask].copy()
    out['side'] = sides[mask]
    out['pnl'] = pnls[mask]
    out['ps_edge'] = np.where(take_yes, ps_edge_y, ps_edge_n)[mask]
    out['lin_pred'] = np.where(take_yes, pred_lin_y, pred_lin_n)[mask]
    return out


def summarize(t, label):
    if len(t) == 0:
        print(f'  {label}: 0 trades'); return None
    n = len(t); wins = (t['pnl'] > 0).sum()
    avg = t['pnl'].mean(); total = t['pnl'].sum()
    span = (pd.to_datetime(t['available_at'].max()) -
             pd.to_datetime(t['available_at'].min())).total_seconds() / 86400
    tpd = n / max(span, 0.1)
    t = t.copy()
    t['event'] = t['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
    ev = t.groupby('event')['pnl'].sum()
    ev_sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
    print(f'  {label}: n={n:>4} tpd={tpd:5.1f} win={wins/n*100:4.1f}% '
          f'avg=${avg:+.4f} total=${total:+7.2f} ev_SR={ev_sr:+5.2f}')
    return total


def main():
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['available_at']).dt.date

    print('=' * 100)
    print('AI-5: Stacked meta-learner (LinUCB ∧ path-sim agreement)')
    print('=' * 100)

    # 50/50 split first
    cut = df['available_at'].quantile(0.5)
    train = df[df['available_at'] < cut].reset_index(drop=True)
    test  = df[df['available_at'] >= cut].reset_index(drop=True)

    print(f'\nTEST 1: 50/50 split, vary path_threshold')
    print(f'  Training LinUCB on {len(train)} rows...')
    lin_y, lin_n, lin_mean, lin_std = train_linucb_on(train, alpha=1.0)
    print(f'  Training path-sim KNN on {len(train)} rows...')
    ps_db = build_db(train)
    for thr in [0.05, 0.10, 0.15, 0.20]:
        t = trade_stacked(test, lin_y, lin_n, lin_mean, lin_std, ps_db,
                            path_threshold=thr, linucb_min=0.005)
        summarize(t, f'path_thr={thr}')

    print(f'\nTEST 2: Rolling 14-day OOS, path_thr=0.15, linucb_min=0.005')
    dates = sorted(df['date'].unique())
    window = 14
    cum = 0.0; n_pos = 0; n_tot = 0; all_t = []
    for w_start in range(7, len(dates) - window, 7):
        td_end = dates[w_start]
        te_end = dates[min(w_start + window, len(dates) - 1)]
        tr = df[df['date'] < td_end]
        te = df[(df['date'] >= td_end) & (df['date'] < te_end)]
        if len(tr) < 1000 or len(te) < 100: continue
        ly, ln_, lm, ls = train_linucb_on(tr)
        pdb = build_db(tr)
        t = trade_stacked(te, ly, ln_, lm, ls, pdb, path_threshold=0.15)
        n_tot += 1
        if len(t) > 0:
            all_t.append(t)
            tot = t['pnl'].sum()
            cum += tot
            if tot > 0: n_pos += 1
            print(f'  {td_end} → {te_end}  n={len(t):>4} avg=${t["pnl"].mean():+.4f} '
                  f'total=${tot:+.2f}')
        else:
            print(f'  {td_end} → {te_end}  no trades')
    print(f'\n  Cumulative: ${cum:+.2f}  positive in {n_pos}/{n_tot}')
    if all_t:
        big = pd.concat(all_t, ignore_index=True)
        big['event'] = big['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
        ev = big.groupby('event')['pnl'].sum()
        sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
        print(f'  Combined event-level SR: {sr:+.2f}')


if __name__ == '__main__':
    main()
