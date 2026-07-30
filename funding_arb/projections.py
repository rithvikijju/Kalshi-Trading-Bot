"""Project funding-rate arb returns across capital tiers and time horizons.

Honest parameters (from strategy_zoo/bias_audit.py and funding_arb/STRATEGY.md):
  - ETH funding APR distribution (post-Luna 4yr): median 22%, 5-95% band 11-30%
  - Current Hyperliquid regime: ~11% APR (about half historical median)
  - Effective notional = 50% of capital (spot leg + perp leg matched, half is margin reserve)
  - Realistic live haircut on backtest APR: 30-40% (slippage, ops, basis MTM events)
  - Median annual max-DD: 2.4%, 5th-percentile worst year DD: 3.7%
  - Capacity: smooth up to ~$25M ETH, slippage drag above ~$50M

Three regimes:
  pessimistic = current-regime APR × full haircut → realistic year-1 paper-to-live
  base        = historical median × 30% haircut  → 4-yr post-Luna realistic
  optimistic  = high-funding cycle × 30% haircut → 2021-style ETH backwardation
"""
import math

# ─── Parameters ────────────────────────────────────────────────────
REGIMES = {
    'pessimistic': {'apr_funding': 0.11, 'haircut': 0.40, 'annual_mdd': -0.025},
    'base':        {'apr_funding': 0.22, 'haircut': 0.30, 'annual_mdd': -0.025},
    'optimistic':  {'apr_funding': 0.32, 'haircut': 0.30, 'annual_mdd': -0.025},
}

# Effective notional fraction (spot + perp = matched ~ half of total capital)
EFFECTIVE_NOTIONAL = 0.50

# Slippage drag on capital above $25M (capacity tier)
CAPACITY_DRAG = {
    100:        0.0,
    1_000:      0.0,
    5_000:      0.0,
    25_000:     0.0,
    100_000:    0.0,
    1_000_000:  0.0,
    10_000_000: 0.005,    # 50bp/yr drag at $10M (modest)
    50_000_000: 0.015,    # 1.5%/yr at $50M (real slippage)
}

CAPITALS  = [100, 1_000, 5_000, 25_000, 100_000, 1_000_000, 10_000_000]
HORIZONS  = [1, 3, 5, 10]


def realistic_apr(regime: str, capital: float) -> float:
    """Realistic annual return on capital after haircuts and capacity drag."""
    r = REGIMES[regime]
    # Funding earns on notional, which is half of capital
    gross = r['apr_funding'] * EFFECTIVE_NOTIONAL
    # Live haircut
    net = gross * (1 - r['haircut'])
    # Capacity drag at scale
    for cap_tier in sorted(CAPACITY_DRAG.keys()):
        if capital >= cap_tier:
            drag = CAPACITY_DRAG[cap_tier]
    net -= drag
    return net


def project(capital: float, years: int, regime: str) -> dict:
    """Return projected NAV at end of horizon, plus annualized stats."""
    apr = realistic_apr(regime, capital)
    # Compounding, monthly intervals
    months = years * 12
    monthly_r = (1 + apr) ** (1 / 12) - 1
    # Inject a single worst-case month's MDD per year as drag
    annual_mdd = REGIMES[regime]['annual_mdd']
    # Simulate: each year, one month is the MDD month
    nav = capital
    for m in range(months):
        nav *= (1 + monthly_r)
        # Apply MDD at end of each year
        if (m + 1) % 12 == 0:
            nav *= (1 + annual_mdd)
    profit = nav - capital
    cagr = (nav / capital) ** (1 / years) - 1
    return {
        'apr_realistic': apr,
        'nav_end': nav,
        'profit': profit,
        'cagr': cagr,
    }


# ─── Print table ───────────────────────────────────────────────────
def fmt_money(x):
    if abs(x) >= 1e9: return f'${x/1e9:.2f}B'
    if abs(x) >= 1e6: return f'${x/1e6:.2f}M'
    if abs(x) >= 1e3: return f'${x/1e3:.1f}K'
    return f'${x:.0f}'


if __name__ == '__main__':
    print('=' * 96)
    print('Funding-rate arb — projected NAV by capital × horizon × regime')
    print('  Notional = 50% of capital. Includes annual MDD drag and capacity slippage.')
    print('=' * 96)

    for regime in REGIMES:
        r = REGIMES[regime]
        gross = r['apr_funding'] * EFFECTIVE_NOTIONAL
        net = gross * (1 - r['haircut'])
        print(f"\n--- {regime.upper():<12} "
              f"funding APR {r['apr_funding']*100:.0f}% × notional {EFFECTIVE_NOTIONAL*100:.0f}% "
              f"× ({100-r['haircut']*100:.0f}% live yield) "
              f"= {net*100:.2f}% on capital  (MDD={r['annual_mdd']*100:.1f}%/yr)")
        # Header
        h = ' Capital   '
        for y in HORIZONS:
            h += f'  {y}yr NAV    {y}yr profit  '
        print(h)
        print('  ' + '─' * 90)
        for c in CAPITALS:
            row = f'  {fmt_money(c):<8}  '
            for y in HORIZONS:
                p = project(c, y, regime)
                row += f' {fmt_money(p["nav_end"]):<10}  {fmt_money(p["profit"]):<10}   '
            print(row)
        # CAGR row
        print(f"\n  Realistic CAGR @ this regime  (any horizon):  "
              f"{project(1_000, 5, regime)['cagr']*100:+.2f}%/yr")

    print()
    print('=' * 96)
    print('Quick summary — what % returns at each capital tier (base regime)')
    print('=' * 96)
    print(f'  {"capital":<12} {"realistic APR":<18} {"after 1 year":<18} {"after 5 years":<18}')
    print('  ' + '─' * 80)
    for c in CAPITALS:
        p1 = project(c, 1, 'base')
        p5 = project(c, 5, 'base')
        apr = realistic_apr('base', c)
        print(f'  {fmt_money(c):<12} {apr*100:>+6.2f}%           '
              f'{fmt_money(p1["profit"]):>10} ({p1["profit"]/c*100:+5.1f}%)  '
              f'{fmt_money(p5["profit"]):>10} ({p5["profit"]/c*100:+6.1f}%)')

    print()
    print('=' * 96)
    print('Reality checks (read these before getting excited)')
    print('=' * 96)
    print("""
  1. Linear scaling — these projections assume you can deploy at scale without
     touching the funding rate. True up to ~$25M ETH. Beyond that, slippage
     and notional impact start to matter (modeled with CAPACITY_DRAG).
  2. No T-bill on idle — USDC margin earns nothing on Hyperliquid. If you
     keep half your capital in T-bills instead, add ~2-2.5%/yr to the return
     but you'd have less margin buffer.
  3. The annual MDD model is generous — assumes ONE bad month per year. A
     real basis-blowout year (LUNA, FTX, COVID) could be 2-3× worse.
  4. Current Hyperliquid funding (~11% APR) is half the historical median.
     "Pessimistic" reflects that. "Base" assumes regime reverts to median
     (post-ETF retail returns to leverage). Plan for pessimistic.
  5. Operational drag at very small capital ($100): fees on rebalances eat
     into the % return more than at scale. Realistic threshold to actually
     justify the operational overhead: ~$5-10K minimum.
""")
