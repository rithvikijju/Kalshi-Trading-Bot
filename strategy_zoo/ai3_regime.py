"""AI-3: Unsupervised regime detection + regime-specific trading.

1. Fit a Gaussian Mixture Model on (vol, drift, spread, hour) features
   from the training set → discovers N latent market regimes.
2. For each regime, compute the *historical* per-trade PnL of trading
   spot-displacement signals (long YES when spot > strike + $200, etc.).
3. Trade ONLY in regimes whose historical PnL was positive.
4. Validate OOS: assign test-set rows to regimes (using only training-fitted
   GMM), apply the regime-conditional trading rule, measure realized PnL.

Genuine AI: GMM discovers structure we didn't hand-code. Different from
LinUCB which used a single linear model; here different regimes can have
different trade signs.
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from scipy.cluster.vq import kmeans2

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'

REGIME_FEATURES = ['rv15', 'rv60', 'drift_5m', 'drift_15m', 'range_10m',
                    'spread', 'hour_sin', 'hour_cos']


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def split_chrono(df, frac):
    df = df.sort_values('available_at').reset_index(drop=True)
    cut = df['available_at'].quantile(frac)
    return df[df['available_at'] < cut].reset_index(drop=True), \
           df[df['available_at'] >= cut].reset_index(drop=True)


def make_candidate_trades(df, dist_lo=50, dist_hi=500):
    """Same K6 candidate structure: when spot is past strike by $50-$500, candidate
    BUY YES. When spot is below by $50-$500, candidate BUY NO. Use that as the
    candidate pool, then let the regime model decide whether to take it."""
    yes_mask = (df['spot_dist'] >= dist_lo) & (df['spot_dist'] <= dist_hi)
    no_mask  = (df['spot_dist'] <= -dist_lo) & (df['spot_dist'] >= -dist_hi)
    yes_t = df[yes_mask].copy()
    yes_t['side'] = 'yes'
    yes_t['paid'] = yes_t['ask_yes']
    yes_t['fee']  = yes_t['ask_yes'].apply(kalshi_fee)
    yes_t['pnl']  = yes_t['yes_wins'] - yes_t['paid'] - yes_t['fee']
    no_t = df[no_mask].copy()
    no_t['side'] = 'no'
    no_t['paid'] = no_t['ask_no']
    no_t['fee']  = no_t['ask_no'].apply(kalshi_fee)
    no_t['pnl']  = (1 - no_t['yes_wins']) - no_t['paid'] - no_t['fee']
    return pd.concat([yes_t, no_t], ignore_index=True).sort_values('available_at')


def fit_regimes(train, n_regimes=6, random_state=0):
    """Use scipy kmeans2 (no threadpoolctl dep) — basically equivalent to GMM
    with spherical covariance for this use case."""
    X = train[REGIME_FEATURES].values.astype(np.float64)
    mean = X.mean(0); std = X.std(0); std[std < 1e-8] = 1.0
    X_norm = (X - mean) / std
    np.random.seed(random_state)
    centroids, _ = kmeans2(X_norm, n_regimes, minit='++', iter=50, seed=random_state)
    return {'centroids': centroids, 'mean': mean, 'std': std}


def assign_regimes(df, regime_model):
    X = df[REGIME_FEATURES].values.astype(np.float64)
    X_norm = (X - regime_model['mean']) / regime_model['std']
    # Nearest centroid
    cents = regime_model['centroids']
    # Compute squared distances
    diffs = X_norm[:, None, :] - cents[None, :, :]
    dists = (diffs ** 2).sum(axis=2)
    return np.argmin(dists, axis=1)


def regime_pnl_table(trades, regimes):
    """For each (regime, side), compute average historical PnL."""
    t = trades.copy()
    t['regime'] = regimes
    g = t.groupby(['regime', 'side'])['pnl'].agg(['count', 'mean', 'sum']).reset_index()
    return g


def trade_with_regime_rules(test_trades, test_regimes, regime_pnl, threshold=0.005):
    """Take a trade only if its (regime, side) showed average PnL > threshold
    in the training set."""
    t = test_trades.copy()
    t['regime'] = test_regimes
    # Build lookup of allowed (regime, side)
    allowed = set()
    for _, row in regime_pnl.iterrows():
        if row['mean'] > threshold and row['count'] >= 50:
            allowed.add((row['regime'], row['side']))
    t['allow'] = t.apply(lambda r: (r['regime'], r['side']) in allowed, axis=1)
    return t[t['allow']].copy()


def summarize(t, label):
    if len(t) == 0:
        print(f'  {label}: 0 trades')
        return
    n = len(t); wins = (t['pnl'] > 0).sum()
    avg = t['pnl'].mean()
    total = t['pnl'].sum()
    span = (pd.to_datetime(t['available_at'].max()) -
             pd.to_datetime(t['available_at'].min())).total_seconds() / 86400
    tpd = n / max(span, 0.1)
    t = t.copy()
    t['event'] = t['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
    ev = t.groupby('event')['pnl'].sum()
    ev_sr = ev.mean() / ev.std() * math.sqrt(8760) if ev.std() > 0 else 0
    print(f'  {label}: n={n:>4} tpd={tpd:5.1f} win={wins/n*100:4.1f}% '
          f'avg=${avg:+.4f} total=${total:+7.2f} ev_SR={ev_sr:+5.2f}')


def main():
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['available_at']).dt.date

    print('=' * 100)
    print('AI-3: GMM regime detection + regime-specific trading')
    print('=' * 100)

    # ── Single split test ──────────────────────────────────────────
    print('\nTEST 1: 50/50 split, vary n_regimes')
    train, test = split_chrono(df, 0.50)
    train_trades = make_candidate_trades(train)
    test_trades  = make_candidate_trades(test)
    print(f'  Train candidates: {len(train_trades):,}  Test candidates: {len(test_trades):,}')

    for n_reg in [3, 4, 5, 6, 8, 10]:
        rm = fit_regimes(train, n_regimes=n_reg)
        train_regimes = assign_regimes(train_trades, rm)
        test_regimes  = assign_regimes(test_trades, rm)
        reg_pnl = regime_pnl_table(train_trades, train_regimes)
        traded = trade_with_regime_rules(test_trades, test_regimes, reg_pnl, threshold=0.005)
        summarize(traded, f'n_regimes={n_reg}')

    # Show the regime PnL table for n=6 (sweet spot usually)
    print('\n  Regime PnL table (n_regimes=6, training set):')
    rm = fit_regimes(train, n_regimes=6)
    train_regimes = assign_regimes(train_trades, rm)
    print(regime_pnl_table(train_trades, train_regimes).to_string(index=False))

    # ── Rolling validation ───────────────────────────────────────
    print('\nTEST 2: Rolling 14-day OOS windows, n_regimes=6, threshold=0.005')
    dates = sorted(df['date'].unique())
    window = 14
    cum_pnl = 0.0; n_pos = 0; n_total = 0
    all_traded = []
    for w_start in range(7, len(dates) - window, 7):
        td_end = dates[w_start]
        te_end = dates[min(w_start + window, len(dates) - 1)]
        tr = df[df['date'] < td_end]
        te = df[(df['date'] >= td_end) & (df['date'] < te_end)]
        if len(tr) < 1000 or len(te) < 100: continue
        tr_t = make_candidate_trades(tr)
        te_t = make_candidate_trades(te)
        if len(tr_t) < 200 or len(te_t) < 50: continue
        rm = fit_regimes(tr, n_regimes=6)
        tr_r = assign_regimes(tr_t, rm)
        te_r = assign_regimes(te_t, rm)
        reg_pnl = regime_pnl_table(tr_t, tr_r)
        traded = trade_with_regime_rules(te_t, te_r, reg_pnl, threshold=0.005)
        if len(traded) == 0:
            continue
        all_traded.append(traded)
        pnl = traded['pnl'].sum()
        cum_pnl += pnl
        n_total += 1
        if pnl > 0: n_pos += 1
        ev_pnl = traded.copy()
        ev_pnl['ev'] = ev_pnl['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
        ev = ev_pnl.groupby('ev')['pnl'].sum()
        ev_sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
        print(f'  {td_end} → {te_end}  n={len(traded):>4}  '
              f'avg=${traded["pnl"].mean():+.4f}  total=${pnl:+.2f}  ev_SR={ev_sr:+.2f}')
    print(f'\n  Cumulative across {n_total} windows: ${cum_pnl:+.2f}  '
          f'(positive in {n_pos}/{n_total})')
    if all_traded:
        big = pd.concat(all_traded, ignore_index=True)
        big['ev'] = big['market_ticker'].str.extract(r'^(KXBTCD-\d+[A-Z]+\d+)', expand=False)
        ev = big.groupby('ev')['pnl'].sum()
        sr = ev.mean()/ev.std()*math.sqrt(8760) if ev.std() > 0 else 0
        print(f'  Combined event-level SR: {sr:+.2f}')


if __name__ == '__main__':
    main()
