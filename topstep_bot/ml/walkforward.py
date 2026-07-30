"""
Walk-forward out-of-sample evaluation — the gate that decides if the model is real.

Data is in global time order. We use expanding-window folds: train on everything before a
cutoff, test on the next contiguous block the model has NEVER seen, roll the cutoff forward.
For each test block we select the trades the model would actually take (P(win) ≥ threshold,
threshold tuned only on a tail of the TRAIN data) and measure realized, fee-inclusive PnL.

We then pool all OOS selected trades and bootstrap a confidence interval on mean PnL/trade.
PROMOTION RULE: pooled OOS mean PnL/trade > 0 AND bootstrap 5th-percentile > 0 AND enough
trades. Anything less is reported honestly as "no OOS edge" — we do NOT tune until it looks
positive in-sample.
"""
from __future__ import annotations

import numpy as np

from .deep_model import DeepConfidence


def expanding_folds(n: int, k: int = 5, min_train_frac: float = 0.4):
    start = int(n * min_train_frac)
    block = max(1, (n - start) // k)
    cut = start
    while cut + block <= n:
        yield np.arange(0, cut), np.arange(cut, cut + block)
        cut += block


def _auc(y, p):
    y = np.asarray(y)
    if len(set(y.tolist())) < 2:
        return float("nan")
    order = np.argsort(p)
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    n1 = y.sum(); n0 = len(y) - n1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _tune_threshold(p_val, pnl_val) -> float:
    """Pick the P(win) cutoff that maximizes mean PnL on the train-tail validation set."""
    best_t, best_e = 0.5, -1e18
    for t in np.linspace(0.40, 0.75, 15):
        sel = p_val >= t
        if sel.sum() < 10:
            continue
        e = pnl_val[sel].mean()
        if e > best_e:
            best_e, best_t = e, t
    return best_t


def walk_forward(data: dict, k: int = 5, seed: int = 0, verbose: bool = True) -> dict:
    Xs, Xf, y, pnl = data["X_seq"], data["X_feat"], data["y"], data["pnl"]
    n = len(y)
    pooled_pnl, pooled_y, pooled_p = [], [], []
    fold_rows = []
    for fi, (tr, te) in enumerate(expanding_folds(n, k)):
        # carve a validation tail out of train for threshold tuning + early stop
        vcut = int(len(tr) * 0.85)
        tr_idx, val_idx = tr[:vcut], tr[vcut:]
        model = DeepConfidence()
        model.fit(Xs[tr_idx], Xf[tr_idx], y[tr_idx])
        p_val = model.predict_proba(Xs[val_idx], Xf[val_idx])
        thr = _tune_threshold(p_val, pnl[val_idx])
        p_te = model.predict_proba(Xs[te], Xf[te])
        sel = p_te >= thr
        n_sel = int(sel.sum())
        wr = float(y[te][sel].mean()) if n_sel else float("nan")
        exp = float(pnl[te][sel].mean()) if n_sel else float("nan")
        auc = _auc(y[te], p_te)
        fold_rows.append(dict(fold=fi, thr=round(thr, 3), n_test=len(te), n_sel=n_sel,
                              oos_win=wr, oos_exp=exp, auc=auc))
        if n_sel:
            pooled_pnl.extend(pnl[te][sel].tolist())
            pooled_y.extend(y[te][sel].tolist())
        pooled_p.extend(p_te.tolist())
        if verbose:
            print(f"  fold {fi}: thr={thr:.2f} test={len(te)} selected={n_sel} "
                  f"OOS win={wr:.1%} exp=${exp:+.2f}/trade auc={auc:.3f}")

    pooled_pnl = np.array(pooled_pnl)
    n_sel = len(pooled_pnl)
    if n_sel < 20:
        return dict(promote=False, reason=f"only {n_sel} OOS trades — insufficient",
                    n_oos=n_sel, folds=fold_rows)
    rng = np.random.default_rng(seed)
    boot = np.array([rng.choice(pooled_pnl, n_sel, replace=True).mean() for _ in range(2000)])
    lo, hi = np.percentile(boot, [5, 95])
    mean_exp = float(pooled_pnl.mean())
    oos_win = float(np.mean(pooled_y))
    promote = bool(mean_exp > 0 and lo > 0 and n_sel >= 20)
    report = dict(promote=promote, n_oos=n_sel, oos_win_rate=round(oos_win, 4),
                  oos_exp_per_trade=round(mean_exp, 3), boot_ci5=round(float(lo), 3),
                  boot_ci95=round(float(hi), 3), pooled_total_pnl=round(float(pooled_pnl.sum()), 2),
                  folds=fold_rows)
    report["reason"] = ("OOS edge confirmed" if promote else
                        "no OOS edge: CI includes/below zero — not promoting")
    return report
