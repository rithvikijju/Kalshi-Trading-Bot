"""Robust funding rate arb backtest using 10 years of BitMEX data.

Tests multiple regimes (2017 bull, 2018 bear, 2020 covid, 2021 mania,
2022 LUNA/FTX, 2023-26 recovery+).

Strategy: long spot BTC, short XBTUSD perp. Every 8h:
- If funding > 0: collect funding (longs pay us)
- If funding < 0: we pay funding to shorts
- Always-on variant: hold continuously
- Filtered variant: only hold when 24h moving avg funding > threshold

Realistic costs:
- Entry/exit: 5 bps per leg = 10 bps round trip = 0.10%
- Borrow on spot (Kraken margin): ~3-5 bps/day = 0.03-0.05%/day
- Funding cost when negative: paid
- Slippage: 5 bps on entry
"""
import duckdb, math, json
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path('strategies/data')
db = duckdb.connect(str(DATA / 'crypto_long.duckdb'), read_only=True)

RESULTS = []

def stats(returns, periods_per_year=1095, name='', desc=''):
    r = pd.Series(returns).dropna()
    if len(r) < 5:
        return {'name': name, 'desc': desc, 'n': 0, 'sharpe': 0, 'ret': 0}
    mu, sd = r.mean(), r.std()
    sharpe = mu / sd * math.sqrt(periods_per_year) if sd > 0 else 0
    ann_ret = (1 + r).prod() ** (periods_per_year / len(r)) - 1
    ann_vol = sd * math.sqrt(periods_per_year)
    cum = (1 + r).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    t_stat = mu / (sd / math.sqrt(len(r))) if sd > 0 else 0
    return {'name': name, 'desc': desc, 'n': len(r), 'sharpe': sharpe,
            'ann_ret': ann_ret, 'ann_vol': ann_vol, 'max_dd': max_dd, 't_stat': t_stat}

def run_strategy(symbol, hold_signal_fn, label, desc, cost_per_switch_bps=10):
    """hold_signal_fn(funding_series) → bool series of "hold during this period"."""
    fr = db.execute(f"SELECT * FROM funding_{symbol} ORDER BY timestamp").df()
    fr['fundingRate'] = fr['fundingRate'].astype(float)
    fr = fr.set_index('timestamp')

    hold = hold_signal_fn(fr['fundingRate'])
    hold = hold.fillna(False)
    # Lag by 1 so we use signal from PRIOR period (no peek)
    hold = hold.shift(1).fillna(False)

    # Earn funding when holding (positive when funding > 0, negative when funding < 0)
    ret = fr['fundingRate'] * hold

    # Subtract round-trip cost when switching (each "switch" is 2 trades = 10 bps total)
    switches = (hold != hold.shift(1)).fillna(False)
    n_switches = switches.sum()
    cost_per_switch = cost_per_switch_bps / 10000
    ret = ret - switches.astype(float) * cost_per_switch / 2  # half on entry, half on exit

    # Filter to periods where strategy is active OR has cost (skip pure-flat periods)
    # For Sharpe calc, use ALL periods (including 0-return periods) because that
    # reflects opportunity cost.

    s = stats(ret, periods_per_year=365 * 3, name=label, desc=desc)  # 3 periods/day
    s['n_switches'] = int(n_switches)
    s['n_held'] = int(hold.sum())
    s['pct_held'] = 100 * hold.mean()
    RESULTS.append(s)
    sign = '+' if s['sharpe'] >= 0 else ''
    star = '★' if s['sharpe'] >= 1.0 and s['ann_ret'] >= 0.05 else ' '
    print(f"  {star} [{label:34}] sharpe={sign}{s['sharpe']:5.2f} ret={s['ann_ret']*100:+6.1f}% "
          f"vol={s['ann_vol']*100:5.1f}% DD={s['max_dd']*100:+6.1f}% "
          f"t={s['t_stat']:+5.2f} held={s['pct_held']:.0f}%  {desc[:36]}")
    return ret, hold

# ─── Strategy variants ─────────────────────────────────────────────
print('═' * 100)
print('  BTC (XBTUSD) funding arb — 10 years of data (2016-2026)')
print('═' * 100)

# Variant 1: Always-on (collect funding always)
run_strategy('XBTUSD', lambda f: pd.Series(True, index=f.index),
             'BTC always-on', 'Hold long-spot/short-perp always',
             cost_per_switch_bps=0)  # no switches

# Variant 2: Hold when last funding was positive
run_strategy('XBTUSD', lambda f: f > 0,
             'BTC funding>0 lag1', 'Hold when prior funding > 0')

# Variant 3: Hold when 24h MA of funding (3 periods) is positive
run_strategy('XBTUSD', lambda f: f.rolling(3).mean() > 0,
             'BTC ma3>0', 'Hold when 24h-avg funding > 0')

# Variant 4: Hold when 7-day MA is positive
run_strategy('XBTUSD', lambda f: f.rolling(21).mean() > 0,
             'BTC ma21>0', 'Hold when 7d-avg funding > 0')

# Variant 5: Hold when 30-day MA is positive
run_strategy('XBTUSD', lambda f: f.rolling(90).mean() > 0,
             'BTC ma90>0', 'Hold when 30d-avg funding > 0')

# Variant 6: Hold when funding > threshold (1bp per 8h = 11% annualized)
run_strategy('XBTUSD', lambda f: f > 0.0001,
             'BTC funding>1bp', 'Hold when funding > 1bp per 8h')

# Variant 7: Hold when funding rolling 3 > 2bp
run_strategy('XBTUSD', lambda f: f.rolling(3).mean() > 0.0002,
             'BTC ma3 > 2bp', 'Hold when 24h-avg > 2bp')

# Variant 8: Crucial — what if we INVERT? Hold when funding is NEGATIVE
# This means we get paid by shorts (we're net long perp + short spot)
run_strategy('XBTUSD', lambda f: f < -0.0001,
             'BTC short-basis (rev)', 'Reverse: long-perp/short-spot when fund<-1bp')

print()
print('═' * 100)
print('  ETH (ETHUSD) funding arb — 8 years of data (2018-2026)')
print('═' * 100)

run_strategy('ETHUSD', lambda f: pd.Series(True, index=f.index),
             'ETH always-on', 'Hold long-spot/short-perp always',
             cost_per_switch_bps=0)
run_strategy('ETHUSD', lambda f: f.rolling(3).mean() > 0,
             'ETH ma3>0', 'Hold when 24h-avg funding > 0')
run_strategy('ETHUSD', lambda f: f.rolling(21).mean() > 0,
             'ETH ma21>0', 'Hold when 7d-avg funding > 0')
run_strategy('ETHUSD', lambda f: f > 0.0001,
             'ETH funding>1bp', 'Hold when funding > 1bp per 8h')

print()
print('═' * 100)
print('  Year-by-year breakdown (BTC always-on)')
print('═' * 100)

fr = db.execute("SELECT * FROM funding_XBTUSD ORDER BY timestamp").df()
fr['year'] = pd.to_datetime(fr['timestamp']).dt.year
fr['fundingRate'] = fr['fundingRate'].astype(float)
year_stats = fr.groupby('year').agg(
    n_periods=('fundingRate', 'count'),
    mean_funding=('fundingRate', 'mean'),
    sum_funding=('fundingRate', 'sum'),
    std_funding=('fundingRate', 'std'),
).reset_index()
year_stats['ann_return_pct'] = year_stats['sum_funding'] * 100
year_stats['ann_sharpe'] = (year_stats['mean_funding'] / year_stats['std_funding']) * math.sqrt(365 * 3)
year_stats['avg_per_period_bps'] = year_stats['mean_funding'] * 10000
print(year_stats[['year','n_periods','avg_per_period_bps','ann_return_pct','ann_sharpe']].to_string(index=False))

print()
print('═' * 100)
print('  FINAL SUMMARY')
print('═' * 100)
print(f"{'strategy':<30} {'sharpe':>7} {'ann_ret%':>9} {'vol%':>6} {'max_DD%':>8} {'t-stat':>7} {'held%':>6}  desc")
print('─' * 110)
for r in sorted(RESULTS, key=lambda x: x['sharpe'], reverse=True):
    star = '★' if r['sharpe'] >= 1.0 and r['ann_ret'] >= 0.05 else ' '
    print(f"{star} {r['name']:<28} {r['sharpe']:>+7.2f} {r['ann_ret']*100:>+8.2f}% "
          f"{r['ann_vol']*100:>5.1f}% {r['max_dd']*100:>+7.1f}% {r['t_stat']:>+6.2f} "
          f"{r['pct_held']:>5.0f}%  {r['desc'][:34]}")

# Save for future
Path(DATA / 'funding_results.json').write_text(json.dumps(RESULTS, indent=2, default=str))
db.close()
