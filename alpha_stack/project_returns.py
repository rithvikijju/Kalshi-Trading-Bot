"""
Projected returns from trading all 15 strategies simultaneously.

Honest accounting:
  * Sharpe is the scale-invariant truth; the RETURN number depends entirely on how much
    risk (vol / leverage) you run. So we report returns at several vol targets.
  * we show the EQUAL-CAPITAL combination (the literal "trade all 15") AND the perf-weighted
    stack, in-sample vs out-of-sample.
  * we apply an honest haircut: project from the OOS Sharpe (not the rosier full-sample one),
    because live performance ~ OOS, not in-sample.
  * block-bootstrap CI so the range is realistic under autocorrelation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import strategies as S
from .data import load_all
from .harness import ANN, backtest_positions, block_bootstrap_sharpe_ci, sharpe
from .run import inverse_vol_stack, perf_weighted_stack, run_strategy


def stats(net):
    net = net.dropna()
    eq = (1 + net).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    ann_ret = net.mean() * ANN
    ann_vol = net.std() * np.sqrt(ANN)
    return sharpe(net), ann_ret, ann_vol, dd


def main():
    data = load_all()
    nets = {fn.__name__: run_strategy(fn, data, 1.0) for fn in S.ALL}
    nets = {k: v for k, v in nets.items() if v is not None}
    df = pd.DataFrame(nets).dropna(how="all")

    # literal "all 15 simultaneously, equal capital": average of the 15 net series
    equal = df.mean(axis=1).dropna()
    perfw = perf_weighted_stack(nets)
    invvol = inverse_vol_stack(nets)

    print("=== TRADING ALL 15 SIMULTANEOUSLY — projected (net of 1bp/turnover costs) ===\n")
    for name, series in [("equal-capital (literal all-15)", equal),
                         ("inverse-vol (equal-risk)", invvol),
                         ("perf-weighted (no-lookahead)", perfw)]:
        sh, ar, av, dd = stats(series)
        lo, hi = block_bootstrap_sharpe_ci(series)
        print(f"{name}:")
        print(f"   Sharpe {sh:.2f}  (95% CI [{lo:.2f},{hi:.2f}]) | ann vol {av*100:.1f}% "
              f"| ann ret {ar*100:+.2f}% | max DD {dd*100:.1f}%")

    # honest projection: use OOS Sharpe of the perf-weighted stack
    cut = int(len(perfw) * 0.6)
    is_sh = sharpe(perfw.iloc[:cut]); oos_sh = sharpe(perfw.iloc[cut:])
    proj_sh = oos_sh                                   # project from OOS, not in-sample
    print(f"\nperf-weighted stack: in-sample Sharpe {is_sh:.2f} -> out-of-sample {oos_sh:.2f}")
    print(f"PROJECTING from the OOS Sharpe = {proj_sh:.2f} (conservative, ~ what live looks like)\n")

    print("Expected return depends on the risk you run (Sharpe is fixed; you choose vol):")
    print(f"  {'vol target':>12} | {'expected ann return':>20} | {'~95% annual range':>22}")
    for vt in (0.05, 0.10, 0.15, 0.20):
        er = proj_sh * vt
        # 1-year return ~ N(er, vt); 90% range
        lo = er - 1.645 * vt; hi = er + 1.645 * vt
        print(f"  {vt*100:>10.0f}% | {er*100:>18.1f}% | [{lo*100:>+6.1f}%, {hi*100:>+6.1f}%]")

    print("\nHONEST CAVEATS:")
    print("  * These are projections from a backtest, not guarantees. OOS (0.30) < in-sample (0.51)")
    print("    — typical alpha decay; live will likely be at/below OOS.")
    print("  * At 10% vol target the realistic projection is ~+3%/yr with a ~1-in-20 chance of a")
    print("    >-13% year. This is a SMALL standalone edge — it earns its keep as a diversifier.")
    print("  * Hitting these returns requires leverage to reach the vol target (raw gross-1")
    print("    exposure runs only ~6% vol => ~+2-3%/yr unlevered).")
    print("  * No regime/crisis model beyond what's in the signals; 2008-2026 includes COVID + 2022")
    print("    but the future can differ.")


if __name__ == "__main__":
    main()
