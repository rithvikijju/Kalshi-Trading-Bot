"""
Bias-hardened evaluation harness — the part the whole program lives or dies on.

The user's #1 requirement: testing must be robust, with NO bias that fakes (or hides) alpha.
The specific biases we defend against, and how:

  1. LOOKAHEAD / leakage      — signals are computed from data up to close[t]; the position
                                earns the return from close[t]->close[t+1]. Strict .shift(1).
                                No same-bar information ever touches the traded return.
  2. TRANSACTION COSTS        — cost = turnover * cost_bps is subtracted EVERY day. A strategy
                                is only ever judged on NET returns. (This is the bug that has
                                bitten us repeatedly; here it is structural, not optional.)
  3. MULTIPLE TESTING         — testing 15+ strategies guarantees some look good by luck.
                                We compute the DEFLATED SHARPE RATIO (Bailey & Lopez de Prado):
                                it discounts each Sharpe for the number of trials, the sample
                                length, and the returns' skew/kurtosis, and asks "is this Sharpe
                                still significant *given* we ran N of them?" Plus Benjamini-
                                Hochberg FDR control across the family of p-values.
  4. AUTOCORRELATION in CI    — naive Sharpe CIs assume iid; returns aren't. We use a moving-
                                block bootstrap so confidence intervals are honest.
  5. SHORT-SAMPLE optimism    — Probabilistic Sharpe Ratio accounts for sample length T.

A strategy is a "winner" ONLY if its Deflated Sharpe Ratio p-value survives the family-wide
FDR control. Everything else is reported as not-significant, however good the raw Sharpe.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

ANN = 252
EULER = 0.5772156649015329


# --------------------------------------------------------------------- core PnL
def backtest_positions(positions: pd.DataFrame, fwd_returns: pd.DataFrame,
                       cost_bps: float = 1.0) -> pd.Series:
    """positions[t] = desired weight per instrument, decided from info up to close[t].
    fwd_returns[t] = simple return close[t]->close[t+1]. Net daily PnL after costs.

    positions are shifted by 1 internally is NOT done here — caller must pass positions already
    aligned so that positions.loc[t] is held to earn fwd_returns.loc[t]; we enforce no-lookahead
    by REQUIRING positions be built from a .shift(1)'d signal (see strategies.py). Cost charged
    on |Δposition| each day."""
    pos = positions.fillna(0.0)
    gross = (pos * fwd_returns.reindex_like(pos)).sum(axis=1)
    turnover = pos.diff().abs().sum(axis=1).fillna(pos.abs().sum(axis=1))
    cost = turnover * (cost_bps / 1e4)
    return (gross - cost).rename("net")


def sharpe(returns: pd.Series, ann: int = ANN) -> float:
    r = returns.dropna()
    sd = r.std()
    return float(r.mean() / sd * np.sqrt(ann)) if sd > 0 else 0.0


# --------------------------------------------------------------------- significance
def probabilistic_sharpe_ratio(returns: pd.Series, sr_benchmark: float = 0.0,
                               ann: int = ANN) -> float:
    """P(true Sharpe > benchmark). Accounts for sample length + non-normal returns."""
    r = returns.dropna().values
    T = len(r)
    if T < 30 or r.std() == 0:
        return 0.5
    sr = r.mean() / r.std()                  # per-period (not annualized)
    sr_b = sr_benchmark / np.sqrt(ann)
    skew = stats.skew(r)
    kurt = stats.kurtosis(r, fisher=False)   # non-excess
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4.0 * sr ** 2)
    z = (sr - sr_b) * np.sqrt(T - 1) / denom if denom > 0 else 0.0
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float, ann: int = ANN) -> float:
    """Expected MAX (per-period) Sharpe across n_trials independent null strategies — the bar a
    real Sharpe must clear (Bailey-LdP). sr_variance is variance of the trials' per-period SR."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    s = np.sqrt(sr_variance)
    e = (1 - EULER) * stats.norm.ppf(1 - 1.0 / n_trials) + \
        EULER * stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
    return float(s * e)


def deflated_sharpe_ratio(returns: pd.Series, n_trials: int, sr_variance: float,
                          ann: int = ANN) -> float:
    """DSR = PSR evaluated against the expected-max-Sharpe benchmark for n_trials. Returns the
    probability the strategy's true Sharpe exceeds what the BEST of n random trials would show."""
    sr0_per = expected_max_sharpe(n_trials, sr_variance, ann)
    return probabilistic_sharpe_ratio(returns, sr0_per * np.sqrt(ann), ann)


def benjamini_hochberg(pvals: list[float], alpha: float = 0.10) -> list[bool]:
    """FDR control across a family of tests. Returns which hypotheses are rejected (=winners)."""
    m = len(pvals)
    order = np.argsort(pvals)
    reject = [False] * m
    kmax = -1
    for i, idx in enumerate(order):
        if pvals[idx] <= (i + 1) / m * alpha:
            kmax = i
    for i in range(kmax + 1):
        reject[order[i]] = True
    return reject


def block_bootstrap_sharpe_ci(returns: pd.Series, block: int = 21, n: int = 2000,
                              ann: int = ANN, seed: int = 0) -> tuple[float, float]:
    """Moving-block bootstrap CI for the Sharpe — honest under autocorrelation."""
    r = returns.dropna().values
    T = len(r)
    if T < block * 3:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    nblocks = T // block + 1
    starts = np.arange(0, T - block)
    out = []
    for _ in range(n):
        idx = rng.choice(starts, nblocks)
        samp = np.concatenate([r[s:s + block] for s in idx])[:T]
        sd = samp.std()
        out.append(samp.mean() / sd * np.sqrt(ann) if sd > 0 else 0.0)
    return (float(np.percentile(out, 5)), float(np.percentile(out, 95)))


# --------------------------------------------------------------------- walk-forward
def purged_walkforward_oos(signal_fn, fwd_returns: pd.DataFrame, n_splits: int = 5,
                           embargo: int = 5, cost_bps: float = 1.0) -> pd.Series:
    """Build positions out-of-sample with a purge+embargo gap so no train info leaks into test.
    signal_fn(train_df_index, full_data) must return positions for the WHOLE period but is only
    fit on train; here strategies are non-parametric (no fit), so this mainly enforces the
    embargo gap between any in-sample normalization window and the traded test bars.

    For our rule-based signals (no fitting) the key guarantee is already the .shift(1); this
    provides the scaffold for any future *fitted* strategy to be evaluated leakage-free."""
    # rule-based signals don't fit, so OOS == full net series; kept for fitted-model extension.
    raise NotImplementedError("strategies here are non-parametric; use evaluate() directly")


# --------------------------------------------------------------------- top-level eval
@dataclass
class StratResult:
    name: str
    net: pd.Series
    sharpe: float
    psr: float
    dsr: float
    ci_low: float
    ci_high: float
    ann_return: float
    max_dd: float
    n_days: int
    turnover: float


def evaluate_one(name: str, net: pd.Series, n_trials: int, sr_variance: float,
                 turnover: float = 0.0) -> StratResult:
    eq = (1 + net.fillna(0)).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return StratResult(
        name=name, net=net, sharpe=sharpe(net),
        psr=probabilistic_sharpe_ratio(net, 0.0),
        dsr=deflated_sharpe_ratio(net, n_trials, sr_variance),
        ci_low=block_bootstrap_sharpe_ci(net)[0], ci_high=block_bootstrap_sharpe_ci(net)[1],
        ann_return=float(net.mean() * ANN), max_dd=float(dd), n_days=int(net.notna().sum()),
        turnover=float(turnover))


def evaluate_family(strategies: dict, alpha: float = 0.10) -> pd.DataFrame:
    """strategies: {name: net_return_series}. Computes per-strategy stats, the trial-count
    deflation, FDR control, and flags winners. This is the honest verdict table."""
    nets = {k: v for k, v in strategies.items() if v is not None and v.notna().sum() > 60}
    n_trials = len(nets)
    # variance of the trials' per-period Sharpes (input to the deflation)
    per_sr = [sharpe(v) / np.sqrt(ANN) for v in nets.values()]
    sr_var = float(np.var(per_sr)) if len(per_sr) > 1 else 0.04 / ANN
    rows = []
    for name, net in nets.items():
        r = evaluate_one(name, net, n_trials, sr_var)
        rows.append(r)
    # DSR p-value = 1 - DSR (prob it is NOT real); FDR across the family
    pvals = [1 - r.dsr for r in rows]
    winners = benjamini_hochberg(pvals, alpha)
    df = pd.DataFrame([{
        "strategy": r.name, "sharpe": round(r.sharpe, 3), "ann_ret%": round(r.ann_return * 100, 2),
        "max_dd%": round(r.max_dd * 100, 1), "PSR": round(r.psr, 3), "DSR": round(r.dsr, 3),
        "sharpe_CI": f"[{r.ci_low:.2f},{r.ci_high:.2f}]", "n_days": r.n_days,
        "winner": w} for r, w in zip(rows, winners)])
    return df.sort_values("DSR", ascending=False).reset_index(drop=True)
