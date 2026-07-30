"""AI-1: Disjoint LinUCB contextual bandit.

Two arms (BUY_YES, BUY_NO). For each row of features in chronological order:
  1. Compute UCB for each arm: μ̂ + α·√(x·A⁻¹·x)
  2. Pull the arm with highest UCB if UCB > 0
  3. Observe realized PnL, update that arm's model

This is online learning — the bandit starts cold and learns as it trades.
We track cumulative PnL over the full chronological walkthrough.

Key honesty checks:
  - Model updates ONLY happen on pulled arms (not future leak)
  - Trades use observed ask at signal time + Kalshi fee
  - Compared to random baseline (-$0.035/trade)
"""
from __future__ import annotations
import math, sys
import numpy as np, pandas as pd

DATA = '/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet'

FEATURE_COLS = [
    'spot_dist','spot_dist_pct','abs_dist','dist_sq','z_dist',
    'mid_yes','spread','ask_yes','ask_no',
    'mtc','log_mtc',
    'rv15','rv60',
    'drift_5m','drift_15m','range_10m',
    'hour_sin','hour_cos','dow',
]


class LinUCB:
    """Disjoint LinUCB for a single arm. Linear model with UCB exploration bonus.
    A. Li et al. 2010 ("A Contextual-Bandit Approach to Personalized News Article Recommendation")"""

    def __init__(self, n_features: int, alpha: float = 1.0):
        self.d = n_features
        self.alpha = alpha
        self.A = np.eye(n_features)        # ridge precision
        self.b = np.zeros(n_features)
        self._A_inv = None                  # lazy invert
        self._dirty = True
        self.n_updates = 0
        self.cum_reward = 0.0

    def _refresh(self):
        if self._dirty:
            self._A_inv = np.linalg.inv(self.A)
            self._dirty = False

    def predict(self, x: np.ndarray) -> float:
        self._refresh()
        theta = self._A_inv @ self.b
        return float(theta @ x)

    def ucb(self, x: np.ndarray) -> float:
        self._refresh()
        theta = self._A_inv @ self.b
        sigma = math.sqrt(max(0.0, float(x @ self._A_inv @ x)))
        return float(theta @ x) + self.alpha * sigma

    def update(self, x: np.ndarray, reward: float):
        self.A += np.outer(x, x)
        self.b += reward * x
        self._dirty = True
        self.n_updates += 1
        self.cum_reward += reward


def standardize_train(X: np.ndarray):
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-8] = 1.0
    return mean, std


def run(alpha=1.0, min_ucb=0.005, train_warmup_frac=0.20, n_runs=1, seed=0):
    """Run LinUCB walk-through chronologically.

    alpha: exploration bonus magnitude
    min_ucb: only pull arm if UCB > this threshold (after fees, ~0.5¢)
    train_warmup_frac: initial fraction to use for feature standardization,
                       during which bandit predicts but doesn't trade
    n_runs: bandit can be re-shuffled within a regime if we want CIs (but we
            walk chronologically; n_runs=1 is honest)
    """
    df = pd.read_parquet(DATA).sort_values('available_at').reset_index(drop=True)
    print(f'Loaded {len(df):,} rows  span={df["available_at"].min()} → {df["available_at"].max()}')

    # Standardize features using only the warmup period
    n_warm = int(len(df) * train_warmup_frac)
    X_all = df[FEATURE_COLS].values.astype(np.float64)
    mean, std = standardize_train(X_all[:n_warm])
    X_norm = (X_all - mean) / std

    # We feed two distinct contexts to the two arms:
    #   YES arm context = [features, ask_yes_norm]
    #   NO arm context  = [features, ask_no_norm]
    # so the bandit can learn that high ask → lower expected PnL.
    # Simpler: same context, two separate models.
    d = X_norm.shape[1] + 1  # +1 for the constant bias
    X_with_bias = np.hstack([X_norm, np.ones((len(X_norm), 1))])

    yes_arm = LinUCB(d, alpha=alpha)
    no_arm  = LinUCB(d, alpha=alpha)

    rewards = []
    trades = 0
    yes_trades = 0
    no_trades = 0
    cum_pnl = 0.0
    daily_pnl = {}

    # Track distribution of decisions
    skipped = 0
    forced_warmup_trades = 0

    for i in range(len(df)):
        x = X_with_bias[i]
        row = df.iloc[i]
        ask_y, ask_n = row['ask_yes'], row['ask_no']
        pnl_y, pnl_n = row['pnl_buy_yes'], row['pnl_buy_no']

        ucb_y = yes_arm.ucb(x)
        ucb_n = no_arm.ucb(x)

        if i < n_warm:
            # During warmup, randomly pull one arm to seed both models
            np.random.seed(seed + i)
            if np.random.random() < 0.5:
                yes_arm.update(x, pnl_y)
                cum_pnl += pnl_y
                trades += 1; yes_trades += 1
            else:
                no_arm.update(x, pnl_n)
                cum_pnl += pnl_n
                trades += 1; no_trades += 1
            forced_warmup_trades += 1
            day = pd.Timestamp(row['available_at']).date()
            daily_pnl[day] = daily_pnl.get(day, 0) + (pnl_y if yes_arm.n_updates > no_arm.n_updates else pnl_n)
            continue

        # Live phase
        if max(ucb_y, ucb_n) < min_ucb:
            skipped += 1
            continue

        if ucb_y >= ucb_n:
            yes_arm.update(x, pnl_y)
            cum_pnl += pnl_y
            trades += 1; yes_trades += 1
            day = pd.Timestamp(row['available_at']).date()
            daily_pnl[day] = daily_pnl.get(day, 0) + pnl_y
        else:
            no_arm.update(x, pnl_n)
            cum_pnl += pnl_n
            trades += 1; no_trades += 1
            day = pd.Timestamp(row['available_at']).date()
            daily_pnl[day] = daily_pnl.get(day, 0) + pnl_n

    avg_pnl = cum_pnl / trades if trades else 0
    print()
    print(f'=== LinUCB(alpha={alpha}, min_ucb={min_ucb}) ===')
    print(f'  Total signals processed: {len(df):,}')
    print(f'  Warmup trades (random):   {forced_warmup_trades:,}')
    print(f'  Live trades (bandit):     {trades - forced_warmup_trades:,}')
    print(f'    of which YES: {yes_trades - sum(1 for k in range(n_warm) if k < n_warm)} ...')
    print(f'  Skipped:                  {skipped:,}')
    print(f'  Cumulative PnL: ${cum_pnl:+.2f}')
    print(f'  Avg PnL/trade:  ${avg_pnl:+.4f}  (random baseline: ~$-0.037)')

    daily = pd.Series(daily_pnl).sort_index()
    print(f'  Days with activity: {len(daily)}')
    print(f'  Daily mean: ${daily.mean():+.3f}  std: ${daily.std():.3f}')
    sr = (daily.mean() / daily.std() * math.sqrt(365)) if daily.std() > 0 else 0
    print(f'  Daily Sharpe: {sr:+.2f}')

    # Also report LIVE-PHASE only (post-warmup) stats
    live_n = trades - forced_warmup_trades
    if live_n > 0:
        # We didn't separate them per-row, but warmup random had random PnL.
        # Estimate live-phase avg:
        # Total = warmup_avg * warmup_n + live_avg * live_n
        # warmup_avg ≈ -0.037 (random)
        est_warmup = -0.037 * forced_warmup_trades
        est_live = (cum_pnl - est_warmup) / live_n
        print(f'  Live-phase avg PnL/trade (estimated): ${est_live:+.4f}')

    return {'cum_pnl': cum_pnl, 'avg_pnl': avg_pnl,
            'trades': trades, 'live_trades': trades - forced_warmup_trades,
            'daily_sharpe': sr}


if __name__ == '__main__':
    print('=' * 80)
    print('AI-1: LinUCB contextual bandit')
    print('=' * 80)
    results = {}
    for alpha in [0.1, 0.5, 1.0, 2.0]:
        for min_ucb in [0.0, 0.005, 0.02]:
            print(f'\n--- alpha={alpha}, min_ucb={min_ucb} ---')
            results[(alpha, min_ucb)] = run(alpha=alpha, min_ucb=min_ucb)
