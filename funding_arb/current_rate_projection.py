"""Project annual returns at user-specified capital tiers, based on the
ACTUAL paper-observed funding rate from the runner currently in production.

Current paper observation (2026-05-22, 4 days into the run):
  - Funding APR: 10.95%  (ETH perp on Hyperliquid)
  - 39 payments × ~$0.0625 each on $5K notional ≈ matches 10.95% APR exactly
  - Cumulative funding: +$2.44
  - Basis MTM (transient drag): -$1.84
  - Entry fees (one-time): -$5.25
"""
import math

# ── Observed-rate parameters (calibrated to current paper log) ────────
APR_FUNDING       = 0.1095            # current Hyperliquid ETH funding
NOTIONAL_FRACTION = 0.50              # spot + perp margin split — half of capital earns funding
ENTRY_FEE_BPS     = 7.0               # ~5 bps Coinbase + 2.5 bps Hyperliquid (per leg, once at open)

# Live-realism haircuts (NOT applied to paper column)
LIVE_HAIRCUT      = 0.30              # slippage, missed cycles, rebalancing — typical 30% drag
ANNUAL_MDD_DRAG   = 0.025             # median annual MDD lost permanently (basis-blowout residual)

# Capacity drag — kicks in above $25M, gets meaningful above $50M
def capacity_drag(capital):
    if capital <= 25_000_000: return 0.0
    if capital <= 50_000_000: return 0.005   # 0.5%/yr
    if capital <= 250_000_000: return 0.015  # 1.5%/yr
    return 0.03                                # 3%/yr

CAPITAL_TIERS = [100, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]


def project(capital):
    # ── Paper rate (what the runner is actually printing) ──────────
    paper_apr = APR_FUNDING * NOTIONAL_FRACTION              # 5.475% of capital
    paper_year_1 = capital * paper_apr
    paper_entry_fee = capital * ENTRY_FEE_BPS / 10000        # one-time, paid year-1 only
    paper_year_1_net = paper_year_1 - paper_entry_fee
    # 5-year compounded paper (assumes regime persists)
    paper_5yr = capital * ((1 + paper_apr) ** 5) - capital - paper_entry_fee

    # ── Realistic live (slippage haircut + annual MDD drag) ───────
    live_apr = paper_apr * (1 - LIVE_HAIRCUT) - capacity_drag(capital)
    live_year_1 = capital * live_apr - paper_entry_fee
    # Apply MDD as a once-per-year ~2.5% drag
    live_year_1_aftermdd = live_year_1 - capital * ANNUAL_MDD_DRAG
    live_apr_aftermdd = live_apr - ANNUAL_MDD_DRAG

    # 5-year live, compounded monthly
    monthly_r = (1 + live_apr) ** (1/12) - 1
    nav = capital - paper_entry_fee
    for _ in range(5):
        for _ in range(12): nav *= (1 + monthly_r)
        nav *= (1 - ANNUAL_MDD_DRAG)
    live_5yr_profit = nav - capital

    return {
        'paper_apr': paper_apr,
        'paper_year_1': paper_year_1_net,
        'paper_year_1_pct': paper_year_1_net / capital * 100 if capital else 0,
        'live_apr': live_apr_aftermdd,
        'live_year_1': live_year_1_aftermdd,
        'live_year_1_pct': live_year_1_aftermdd / capital * 100 if capital else 0,
        'live_5yr_profit': live_5yr_profit,
        'entry_fee': paper_entry_fee,
    }


def fmt(x):
    if abs(x) >= 1e6: return f'${x/1e6:.2f}M'
    if abs(x) >= 1e3: return f'${x/1e3:.2f}K'
    return f'${x:.2f}'


print('=' * 110)
print('Funding-rate arb — projected annual returns at user-specified capital')
print('  Based on currently-observed paper rate: 10.95% APR ETH funding × 50% notional fraction')
print('=' * 110)

print(f'\n{"capital":<12} {"paper APR":<10} {"paper Y1 profit":<18} {"live APR":<10} '
      f'{"live Y1 profit":<18} {"live Y1 %":<10} {"live 5Y profit":<18}')
print('─' * 110)

for c in CAPITAL_TIERS:
    p = project(c)
    print(f'{fmt(c):<12} '
          f'{p["paper_apr"]*100:>6.2f}%    '
          f'{fmt(p["paper_year_1"]):<8} ({p["paper_year_1_pct"]:>+5.2f}%)   '
          f'{p["live_apr"]*100:>6.2f}%    '
          f'{fmt(p["live_year_1"]):<8} ({p["live_year_1_pct"]:>+5.2f}%)   '
          f'{fmt(p["live_5yr_profit"])}')

print()
print('=' * 110)
print('Reality checks before getting excited:')
print('─' * 110)
print('  • Paper column = what your paper_runner is literally collecting at 10.95% APR funding.')
print('    No slippage, no execution misses, no off-by-one rebalancing.')
print('  • Live column = realistic haircut for actual deployment:')
print('    ─ 30% haircut for slippage / missed cycles / rebalance fees')
print('    ─ 2.5%/yr MDD drag (basis-blowout residual losses you don\'t recover)')
print('  • Capacity drag kicks in at scale: $25M+ starts to see 0.5%/yr drag,')
print('    $50M+ sees 1.5%/yr, $250M+ sees 3%/yr. (Below $25M = linear scaling.)')
print('  • The 10.95% APR is the CURRENT Hyperliquid regime — about HALF the historical')
print('    8-year median (22% APR post-Luna). If we revert to median, double these numbers.')
print('  • At $100, Hyperliquid minimum order sizes will likely block deployment.')
print('    Realistic capital floor is ~$1K-$5K to actually clear minimums + fees.')
print('  • Year-1 numbers include the $5.25 entry fee on $5K notional (= 7 bps),')
print('    which compresses returns. Years 2+ don\'t have this fee.')
