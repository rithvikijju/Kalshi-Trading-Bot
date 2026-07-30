"""Realistic capital projections for ETH funding arb.

USES BIAS-CORRECTED ASSUMPTIONS (from bias_audit.py):
  - Sharpe: 6 (high), 3 (mid), 1.5 (pessimistic)
  - APR: 22% (8yr median honest), 12% (current regime, +11% funding),
         8% (worst-year + capacity drag)
  - MDD: -2.4% to -8% depending on basis stress in the year

Plus REGIME ADJUSTMENT: current ETH funding is +11% APR (lower than the 22%
historical median). If we just use current funding-rate APR as the base, expected
returns are much lower than the historical median.

CAPACITY DRAG:
  $0-25k:    no drag, you're invisible
  $25-100k:  no drag
  $100-500k: ~5% drag from slippage on rebalances
  $500k-2M:  ~10% drag
  $2M-10M:   ~20% drag (you start moving the market on entry/exit)
  $10M+:     ~30%+ drag, mostly maker-only orders

Three scenarios per capital level: BEAR (negative funding regime, sit out),
BASE (mid expectation), BULL (favorable funding regime).
"""
from __future__ import annotations
import math
import pandas as pd, numpy as np


def annual_return_with_drag(base_apr, capital):
    """Apply capacity drag based on capital level."""
    if capital <  25_000: return base_apr
    if capital < 100_000: return base_apr
    if capital < 500_000: return base_apr * 0.95
    if capital < 2_000_000: return base_apr * 0.90
    if capital < 10_000_000: return base_apr * 0.80
    return base_apr * 0.70


def project(start_cap, apr, mdd_pct, months=12, monthly_inj=0):
    monthly_ret = (1 + apr) ** (1/12) - 1
    nav = start_cap; rows = []
    worst_dd_dollar = 0
    for m in range(1, months+1):
        nav = nav * (1 + monthly_ret)
        nav += monthly_inj
        worst_dd_dollar = max(worst_dd_dollar, nav * mdd_pct)
        rows.append({'month':m,'nav':nav,'cum_capital':start_cap+m*monthly_inj,
                      'gain_over_capital':nav-start_cap-m*monthly_inj,
                      'expected_max_dd_dollar':nav*mdd_pct})
    return pd.DataFrame(rows)


def print_scenario(label, start_cap, apr, mdd, monthly_inj=0):
    df = project(start_cap, apr, mdd, monthly_inj=monthly_inj)
    final = df.iloc[-1]
    gain = final['gain_over_capital']
    cap_used = final['cum_capital']
    roi = gain / cap_used * 100
    print(f"    {label:<24}  APR={apr*100:>5.1f}%  MDD={mdd*100:>4.1f}%  "
          f"final NAV=${final['nav']:>11,.0f}  "
          f"gain=${gain:>+10,.0f}  ROI={roi:>+5.1f}%  "
          f"~worst_DD=${final['expected_max_dd_dollar']:>9,.0f}")


def header(cap, monthly_inj):
    inj_str = f" + ${monthly_inj:,.0f}/mo" if monthly_inj else ""
    print(f"\n  Starting capital: ${cap:,.0f}{inj_str}")
    print(f"  {'─'*108}")


def main():
    print("=" * 110)
    print("REALISTIC 1-YEAR PROJECTIONS — ETH funding-arb basis carry")
    print("=" * 110)
    print("""
  BIAS-CORRECTED ASSUMPTIONS (from bias_audit.py Monte Carlo over 30 seeds):
    - 8-year median APR:   22%  (clean Sharpe 6, MDD 2.4%)
    - Current regime APR:  11%  (ETH funding right now is +0.125 bps/hr)
    - Bear regime APR:      5%  (sit-out months when funding ≤ 0)
    - Realistic MDD:        3-8% per year, depending on basis stress

  THREE SCENARIOS at each capital level:
    BEAR:    funding stays at ~5% APR for full year (rare extended bear regime)
    BASE:    funding mean-reverts toward 15% APR — split between current low + historical median
    BULL:    funding spikes during one or more leverage cycles (~25% APR full year)

  Capacity drag: 0% under $100k; 5% to $500k; 10% to $2M; 20% to $10M; 30% above $10M.""")

    # Single-shot capital
    print("\n" + "═" * 110)
    print("PART A — SINGLE LUMP, NO MONTHLY INJECTION (year-1 return on starting capital only)")
    print("═" * 110)

    for cap in [5_000, 10_000, 25_000, 100_000, 500_000, 2_000_000, 10_000_000]:
        header(cap, 0)
        for label, apr, mdd in [
            ('BEAR (~5% APR all yr)',  annual_return_with_drag(0.05, cap),  0.03),
            ('BASE (15% mid-cycle) ',  annual_return_with_drag(0.15, cap),  0.04),
            ('BULL (25% favorable) ',  annual_return_with_drag(0.25, cap),  0.06),
        ]:
            print_scenario(label, cap, apr, mdd, 0)

    # With injections (more realistic for someone building the strategy)
    print("\n" + "═" * 110)
    print("PART B — WITH MONTHLY EQUITY INJECTIONS (you keep adding savings)")
    print("═" * 110)
    for cap, inj in [(5_000,500), (10_000,1_000), (25_000,2_000),
                       (100_000,5_000), (500_000,10_000)]:
        header(cap, inj)
        for label, apr, mdd in [
            ('BEAR',  annual_return_with_drag(0.05, cap),  0.03),
            ('BASE',  annual_return_with_drag(0.15, cap),  0.04),
            ('BULL',  annual_return_with_drag(0.25, cap),  0.06),
        ]:
            print_scenario(label, cap, apr, mdd, inj)

    # Honest caveats
    print("\n" + "═" * 110)
    print("WHAT THE NUMBERS DON'T TELL YOU")
    print("═" * 110)
    print("""
  1. TAXES. Funding payments are ORDINARY INCOME, not capital gains.
     Federal + state can be 30-50%. The ROI numbers above are PRE-TAX.

  2. CURRENT FUNDING IS LOW. As of today, ETH funding on Hyperliquid is +0.125
     bps/hr → ~11% APR. That's roughly the BASE case. If you start now, expect
     about 11% in the first quarter unless funding spikes.

  3. PUTS YOUR CAPITAL AT EXCHANGE RISK. Hyperliquid is a smart contract DEX;
     Coinbase is regulated. Both have non-zero risk of catastrophic loss.
     The MDD column ignores tail-risk exchange failures.

  4. THE FUNDING RATE CAN GO TO ZERO. The strategy doesn't lose money in that
     case — you just earn nothing. But it does mean your capital is sitting
     idle (could be in T-bills earning ~4.5%).

  5. CAPACITY ISN'T LINEAR. The $10M projection (30% drag → effective ~15.5%
     APR) is optimistic. Above $10M you'd need to use maker-only orders and
     split across multiple venues to avoid market impact.

  6. SHARPE 6 IS STILL VERY GOOD. Even after corrections, Sharpe 6 means you
     have ~1 down month per year in dollar terms. That's better than equities
     (Sharpe ~0.6), better than most quant strategies (Sharpe 1-2), and on par
     with what real basis-trade funds actually report.

  7. THIS IS THE WHOLE STRATEGY. There is no "secret sauce" to find — the edge
     is structural (perp lev demand > spot lev demand on most days). You
     compete with everyone who can read funding rates. Capacity at $10-50M
     globally is the binding constraint, but your $25k won't move that.
""")


if __name__ == '__main__':
    main()
