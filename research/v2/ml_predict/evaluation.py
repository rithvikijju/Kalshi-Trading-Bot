"""Bias-controlled evaluation framework.

Key components:
- Train/val/test split: 2014-2020 train, 2020-2022 val, 2022-2026 test (8 yrs OOS)
  (limited by BTC-USD history starting Oct 2014)
- Purged k-fold CV within train (López de Prado 2018, ch.7): for fold k, embargo
  21 days on either side of test rows to prevent label-leakage from rolling
  features.
- Walk-forward in test: refit annually, predict next year.
- Trading rule: long if P(up)>0.5+thr, short if P(up)<0.5-thr, flat otherwise.
  (or for regression: scale by predicted return magnitude)
- Costs: 1bp r/t (SPY effective spread + commission)
- Deflated Sharpe (Bailey/López de Prado 2014) for the strategies tested.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.preprocessing import StandardScaler

# Periods — expanded test to include COVID (Mar 2020), 2022 bear, and 2023-2026 bull
TRAIN_END = "2019-12-31"
VAL_END   = "2019-12-31"   # no separate val; first walk-forward year handles model selection
TEST_END  = "2026-12-31"


def split_data(df: pd.DataFrame) -> dict:
    """Return train/val/test splits as DataFrame dicts."""
    return {
        "train": df[df.index <= TRAIN_END],
        "val":   df[(df.index > TRAIN_END) & (df.index <= VAL_END)],
        "test":  df[df.index > VAL_END],
    }


def feature_cols(df: pd.DataFrame) -> list:
    """All columns except targets."""
    return [c for c in df.columns
            if c not in ("target_ret", "target_up")
            and not c.startswith("LEAK_")]


def feature_cols_with_leakage(df: pd.DataFrame) -> list:
    return [c for c in df.columns if c not in ("target_ret", "target_up")]


# ---------------------------------------------------------------------------
# Purged k-fold (López de Prado 2018, ch.7)

def purged_kfold_splits(n: int, n_splits: int = 5, embargo: int = 21):
    """Generate (train_idx, val_idx) for k-fold with embargo around val.

    Yields integer index arrays of length n.
    """
    fold_size = n // n_splits
    for k in range(n_splits):
        val_start = k * fold_size
        val_end = val_start + fold_size if k < n_splits - 1 else n
        val_idx = np.arange(val_start, val_end)
        # train = all rows EXCEPT (val ± embargo)
        ban = np.zeros(n, dtype=bool)
        ban[max(0, val_start - embargo): min(n, val_end + embargo)] = True
        train_idx = np.where(~ban)[0]
        yield train_idx, val_idx


# ---------------------------------------------------------------------------
# Walk-forward refit in test

def walk_forward_splits(test_index: pd.DatetimeIndex, refit_months: int = 12):
    """Yield (start_idx, end_idx) tuples for walk-forward refit windows.
    Each tuple represents a contiguous block in which we use the model
    trained on (everything strictly before start).
    """
    blocks = []
    cur_start = 0
    cur_year = test_index[0].year
    for i, dt in enumerate(test_index):
        if dt.year != cur_year:
            blocks.append((cur_start, i))
            cur_start = i
            cur_year = dt.year
    blocks.append((cur_start, len(test_index)))
    return blocks


# ---------------------------------------------------------------------------
# Performance metrics

def trade_pnl(pred_prob_up: np.ndarray, actual_ret: np.ndarray,
              threshold: float = 0.05, cost_bp_rt: float = 1.0) -> pd.Series:
    """Apply trading rule: long if p>0.5+thr, short if p<0.5-thr, else flat.
    Returns daily PnL (in log-return space, simple-return equivalent for small).
    """
    pos = np.zeros(len(pred_prob_up))
    pos[pred_prob_up > 0.5 + threshold] = +1
    pos[pred_prob_up < 0.5 - threshold] = -1
    # PnL = position × actual next-day return (signal already at t-1, return at t+1
    # in our setup, so direct multiply is correct — no further shift needed)
    pnl = pos * actual_ret
    # turnover cost when position changes
    turn = np.abs(np.diff(np.concatenate([[0], pos])))
    cost = turn * (cost_bp_rt / 10000)
    return pd.Series(pnl - cost), pos


def stats(pnl: pd.Series) -> dict:
    if len(pnl) == 0 or pnl.std() == 0:
        return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0, hit=0, n=0)
    sharpe = pnl.mean() / pnl.std() * np.sqrt(252)
    eq = (1 + pnl).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    nonzero = pnl[pnl != 0]
    hit = (nonzero > 0).mean() if len(nonzero) > 0 else 0
    return dict(sharpe=float(sharpe), ann_ret=float(pnl.mean()*252),
                ann_vol=float(pnl.std()*np.sqrt(252)),
                mdd=float(mdd), hit=float(hit), n=int(len(pnl)))


def deflated_sharpe(sr: float, n_trials: int, T: int) -> float:
    """Bailey & López de Prado 2014 deflated Sharpe (simplified)."""
    if T < 30: return 0
    # SE of Sharpe estimator
    se = np.sqrt((1 + 0.5*sr**2) / T)
    # Expected max Sharpe under null (zero true SR) across N trials
    em = se * ((1 - 0.5772) * norm.ppf(1 - 1/n_trials)
               + 0.5772 * norm.ppf(1 - 1/(n_trials * np.e)))
    return float(sr - em * np.sqrt(252))


# ---------------------------------------------------------------------------
# Common: standardize, fit, predict

def fit_and_score(model, X_tr, y_tr, X_te):
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)
    model.fit(X_tr_s, y_tr)
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X_te_s)[:, 1]
    else:
        # regression model: convert to "prob up" via sigmoid of predicted return
        pred = model.predict(X_te_s)
        proba = 1 / (1 + np.exp(-pred / max(0.005, np.std(pred))))
    return proba
