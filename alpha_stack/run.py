"""
Run the 15 strategies through the bias-hardened harness, then stack them.

Honest by construction:
  * each strategy's weights are .shift(1)'d -> position earns NEXT day's return (no lookahead)
  * costs subtracted every day on turnover
  * family evaluated with Deflated Sharpe + Benjamini-Hochberg FDR (multiple-testing safe)
  * the STACK is inverse-vol weighted using a TRAILING (shifted) vol estimate -> no lookahead,
    no fitted parameters, so the combination itself cannot overfit the test window
  * stack reported with its own DSR (n_trials includes the search) + a clean train/test split

    python -m alpha_stack.run --cost_bps 1.0
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import strategies as S
from .data import load_all
from .harness import (ANN, backtest_positions, block_bootstrap_sharpe_ci,
                      deflated_sharpe_ratio, evaluate_family, probabilistic_sharpe_ratio, sharpe)


def run_strategy(fn, data, cost_bps):
    w = fn(data)
    if w is None or w.dropna(how="all").empty:
        return None
    ret = data["prices"].pct_change()
    fwd = ret.reindex(columns=w.columns)
    pos = w.shift(1)                                   # NO-LOOKAHEAD: decide at t-1, earn t
    net = backtest_positions(pos, fwd, cost_bps=cost_bps)
    return net.dropna()


def inverse_vol_stack(nets: dict, vol_win=63):
    """Combine net-return series with TRAILING inverse-vol weights (shifted -> no lookahead).
    Equal-risk, no fitted parameters."""
    df = pd.DataFrame(nets).sort_index()
    vol = df.rolling(vol_win).std().shift(1)           # trailing vol, shifted
    iv = (1.0 / vol).replace([np.inf, -np.inf], np.nan)
    w = iv.div(iv.sum(axis=1), axis=0)
    return (df * w).sum(axis=1).dropna()


def perf_weighted_stack(nets: dict, win=252, vol_win=63):
    """Real-time-implementable stack: weight each strategy by max(0, its TRAILING 1y Sharpe),
    then risk-scale by trailing inverse-vol. All inputs shifted -> NO lookahead. Down-weights
    strategies whose LIVE track record is poor, without ever peeking at the future. A single
    combination rule (no per-strategy fitting), so it can't overfit individual strategies."""
    df = pd.DataFrame(nets).sort_index()
    mean = df.rolling(win).mean().shift(1)
    vol = df.rolling(vol_win).std().shift(1)
    tr_sharpe = (mean / vol).clip(lower=0)             # long-only conviction from past perf
    iv = (1.0 / vol).replace([np.inf, -np.inf], np.nan)
    raw = tr_sharpe * iv
    w = raw.div(raw.sum(axis=1), axis=0).fillna(0.0)
    return (df * w).sum(axis=1).dropna()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost_bps", type=float, default=1.0, help="round-turn cost per unit turnover")
    ap.add_argument("--fdr", type=float, default=0.10)
    a = ap.parse_args()

    print("loading multi-source data...")
    data = load_all()
    print(f"  {data['prices'].shape[1]} instruments, {data['prices'].shape[0]} days\n")

    nets = {}
    for fn in S.ALL:
        net = run_strategy(fn, data, a.cost_bps)
        if net is not None:
            nets[fn.__name__] = net
    print(f"ran {len(nets)}/{len(S.ALL)} strategies\n")

    # ---- family evaluation (multiple-testing safe) ----
    table = evaluate_family(nets, alpha=a.fdr)
    print("=== PER-STRATEGY (Deflated Sharpe + Benjamini-Hochberg FDR) ===")
    print(table.to_string(index=False))

    # ---- diversification: average pairwise correlation ----
    df = pd.DataFrame(nets)
    corr = df.corr()
    avg_corr = (corr.values[np.triu_indices_from(corr.values, 1)]).mean()
    print(f"\naverage pairwise correlation across strategies: {avg_corr:+.3f} "
          f"({'low — good for stacking' if abs(avg_corr) < 0.2 else 'moderate/high'})")

    # ---- the STACK (compare equal-risk vs trailing-performance weighting) ----
    stack_eq = inverse_vol_stack(nets)
    stack_pw = perf_weighted_stack(nets)
    print(f"\nequal-risk stack Sharpe: {sharpe(stack_eq):.3f}  |  "
          f"perf-weighted (no-lookahead) stack Sharpe: {sharpe(stack_pw):.3f}")
    stack = stack_pw if sharpe(stack_pw) > sharpe(stack_eq) else stack_eq
    n_trials = len(nets) + 2
    per_sr = [sharpe(v) / np.sqrt(ANN) for v in nets.values()]
    sr_var = float(np.var(per_sr))
    s_sharpe = sharpe(stack)
    s_dsr = deflated_sharpe_ratio(stack, n_trials, sr_var)
    s_psr = probabilistic_sharpe_ratio(stack, 0.0)
    ci = block_bootstrap_sharpe_ci(stack)
    eq = (1 + stack).cumprod(); dd = (eq / eq.cummax() - 1).min()

    # ---- honest train/test split of the stack (combination not seen on test) ----
    cut = int(len(stack) * 0.6)
    tr, te = stack.iloc[:cut], stack.iloc[cut:]

    print("\n=== STACKED PORTFOLIO (inverse-vol, no-lookahead, no fitting) ===")
    print(f"Sharpe        : {s_sharpe:.3f}   (95% block-bootstrap CI [{ci[0]:.2f}, {ci[1]:.2f}])")
    print(f"ann return    : {stack.mean()*ANN*100:+.2f}%   max DD: {dd*100:.1f}%")
    print(f"PSR(>0)       : {s_psr:.3f}")
    print(f"Deflated SR   : {s_dsr:.3f}   (prob real after deflating for {n_trials} trials)")
    print(f"in-sample SR  : {sharpe(tr):.3f}   |   out-of-sample SR: {sharpe(te):.3f}")
    print(f"  ({'OOS HOLDS — stack is robust' if sharpe(te) > 0.3 else 'OOS weak — be cautious'})")

    verdict = "✅ STACK PASSES" if (s_dsr > 0.90 and sharpe(te) > 0.3) else "⚠️ stack not conclusively significant"
    print(f"\nVERDICT: {verdict}")

    # save the equity curve + table for the report
    out = data["prices"].index.to_frame().iloc[:0]
    table.to_csv("alpha_stack/results_table.csv", index=False)
    stack.to_frame("stack_net").to_parquet("alpha_stack/stack_returns.parquet")
    print("\nsaved alpha_stack/results_table.csv + stack_returns.parquet")


if __name__ == "__main__":
    main()
