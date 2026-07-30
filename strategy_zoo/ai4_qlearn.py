"""AI-4: Tabular Q-learning agent.

Discretize state into bins; learn Q-values for {skip, BUY_YES, BUY_NO}
from training data. Apply learned policy on test set.

State features (binned):
  spot_dist_band (8 bins from -500..+500)
  mtc_band       (5 bins)
  vol_band       (3 bins from rv_15m tercile)
  ask_band       (5 bins from ask price)

Reward = realized PnL after fees if action is BUY_YES or BUY_NO; 0 if skip.

Q-update:
  Q(s, a) ← Q(s, a) + α (r + γ·max_a' Q(s', a') − Q(s, a))
  No transition between markets in episode (each market is one step,
  so γ=0 and Q(s,a) = mean reward of taking a in state s).

So this collapses to **mean-reward-per-state-action** estimation, which is
sane and unbiased. We then take the argmax action per state in test.
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def discretize(df, train_means):
    """Bin continuous features into discrete state."""
    spot_dist = df['spot_dist'].values
    sd_bins = np.array([-1e9, -500, -200, -50, 50, 200, 500, 1e9])
    sd_band = np.digitize(spot_dist, sd_bins) - 1   # 0..7

    mtc = df['mtc'].values
    mt_bins = np.array([0, 38, 44, 50, 56, 60])
    mt_band = np.digitize(mtc, mt_bins) - 1   # 0..4

    rv = df['rv15'].values
    rv_q33 = train_means['rv15_q33']
    rv_q67 = train_means['rv15_q67']
    rv_band = np.where(rv < rv_q33, 0, np.where(rv < rv_q67, 1, 2))

    ask = df['ask_yes'].values
    a_bins = np.array([0, 0.10, 0.30, 0.50, 0.70, 1.01])
    a_band = np.digitize(ask, a_bins) - 1   # 0..4

    return np.stack([sd_band, mt_band, rv_band, a_band], axis=1)


def train_q_table(train, n_states_per_dim=(8, 5, 3, 5)):
    """For each state, compute mean reward of BUY_YES, BUY_NO, and effective
    'skip' reward (=0). Returns Q[state_tuple] = (q_yes, q_no, n_yes, n_no)."""
    rv_q33 = np.quantile(train['rv15'].values, 0.33)
    rv_q67 = np.quantile(train['rv15'].values, 0.67)
    train_means = {'rv15_q33': rv_q33, 'rv15_q67': rv_q67}

    states = discretize(train, train_means)
    pnl_y = train['pnl_buy_yes'].values
    pnl_n = train['pnl_buy_no'].values

    Q = {}
    for i in range(len(train)):
        key = tuple(int(x) for x in states[i])
        if key not in Q:
            Q[key] = [0.0, 0.0, 0, 0]  # sum_yes, sum_no, n_yes, n_no
        Q[key][0] += pnl_y[i]
        Q[key][1] += pnl_n[i]
        Q[key][2] += 1
        Q[key][3] += 1
    # Convert to mean
    Q_means = {}
    for key, v in Q.items():
        Q_means[key] = (v[0] / v[2], v[1] / v[3], v[2], v[3])
    return Q_means, train_means


def apply_policy(test, Q_means, train_means, threshold=0.005, min_n=50):
    """For each test row, look up Q[state]. Take BUY_YES if q_yes > threshold
    and q_yes > q_no. Take BUY_NO if q_no > threshold and q_no > q_yes.
    Require state to have at least min_n training examples (avoid overfitting)."""
    states = discretize(test, train_means)
    pnl_y = test['pnl_buy_yes'].values
    pnl_n = test['pnl_buy_no'].values
    actions = []
    rewards = []
    asks = []
    sides = []
    for i in range(len(test)):
        key = tuple(int(x) for x in states[i])
        if key not in Q_means:
            actions.append('skip'); continue
        q_y, q_n, n_y, n_n = Q_means[key]
        if max(q_y, q_n) < threshold or min(n_y, n_n) < min_n:
            actions.append('skip'); continue
        if q_y >= q_n:
            actions.append('yes')
            rewards.append(pnl_y[i])
            asks.append(test.iloc[i]['ask_yes'])
            sides.append((i, 'yes'))
        else:
            actions.append('no')
            rewards.append(pnl_n[i])
            asks.append(test.iloc[i]['ask_no'])
            sides.append((i, 'no'))
    # Build trade df
    traded_idx = [s[0] for s in sides]
    if not traded_idx:
        return pd.DataFrame()
    out = test.iloc[traded_idx].copy()
    out['side'] = [s[1] for s in sides]
    out['pnl'] = rewards
    out['paid'] = asks
    return out


def summarize(t, label):
    if len(t) == 0:
        print(f'  {label}: 0 trades'); return
    n = len(t); wins = (t['pnl'] > 0).sum()
    avg = t['pnl'].mean(); total = t['pnl'].sum()
    t = t.copy()
    t['event'] = t['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
    ev = t.groupby('event')['pnl'].sum()
    ev_sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
    span_d = (pd.to_datetime(t['available_at'].max()) -
              pd.to_datetime(t['available_at'].min())).total_seconds() / 86400
    tpd = n / max(span_d, 0.1)
    print(f'  {label}: n={n:>4} tpd={tpd:5.1f} win={wins/n*100:4.1f}% '
          f'avg=${avg:+.4f} total=${total:+7.2f} ev_SR={ev_sr:+5.2f}')


def main():
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['available_at']).dt.date

    print('=' * 100)
    print('AI-4: Tabular Q-learning (per-state mean-reward policy)')
    print('=' * 100)

    # 50/50 chrono
    cut = df['available_at'].quantile(0.5)
    train = df[df['available_at'] < cut].reset_index(drop=True)
    test  = df[df['available_at'] >= cut].reset_index(drop=True)

    print(f'\nTest 1: 50/50 chrono split, vary threshold (min_n=50)')
    Q, tm = train_q_table(train)
    print(f'  Q-table size: {len(Q)} unique states')
    for thr in [0.0, 0.005, 0.01, 0.02, 0.05]:
        t = apply_policy(test, Q, tm, threshold=thr, min_n=50)
        summarize(t, f'threshold={thr}')

    print(f'\nTest 2: 50/50 chrono split, vary min_n (threshold=0.01)')
    for min_n in [10, 30, 50, 100, 200]:
        t = apply_policy(test, Q, tm, threshold=0.01, min_n=min_n)
        summarize(t, f'min_n={min_n}')

    print(f'\nTest 3: Rolling 14-day OOS windows (threshold=0.01, min_n=50)')
    dates = sorted(df['date'].unique())
    window = 14
    all_t = []; n_pos = 0; n_tot = 0
    for w_start in range(7, len(dates) - window, 7):
        td_end = dates[w_start]
        te_end = dates[min(w_start + window, len(dates) - 1)]
        tr = df[df['date'] < td_end]
        te = df[(df['date'] >= td_end) & (df['date'] < te_end)]
        if len(tr) < 1000 or len(te) < 100: continue
        Q, tm = train_q_table(tr)
        t = apply_policy(te, Q, tm, threshold=0.01, min_n=50)
        n_tot += 1
        if len(t) > 0 and t['pnl'].sum() > 0: n_pos += 1
        all_t.append(t)
        if len(t) > 0:
            pnl = t['pnl'].sum()
            print(f'  {td_end} → {te_end}  n={len(t):>4} avg=${t["pnl"].mean():+.4f} '
                  f'total=${pnl:+.2f}')
    big = pd.concat([x for x in all_t if len(x) > 0], ignore_index=True) \
        if any(len(x) > 0 for x in all_t) else pd.DataFrame()
    if len(big) > 0:
        print(f'\n  Cumulative: ${big["pnl"].sum():+.2f}  positive in {n_pos}/{n_tot}')
        big['event'] = big['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
        ev = big.groupby('event')['pnl'].sum()
        ev_sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
        print(f'  Combined event-level SR: {ev_sr:+.2f}')


if __name__ == '__main__':
    main()
