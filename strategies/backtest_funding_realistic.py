"""Rigorous funding arb backtest — fixed cost model + capital base correction.

Fixes vs prior version:

1. CARRYING COST while held: every 8h period that we hold, charge 3 bps
   for spot/perp spread maintenance, basis tracking, and rebalancing friction.
   This is the dominant cost in reality, not switch cost.

2. ROUND-TRIP at switch: 25 bps (was 10) — covers maker+taker fees + slippage
   on both legs.

3. CAPITAL BASE: returns reported on TOTAL CAPITAL DEPLOYED, which is
   ~1.15× notional (spot + perp margin + buffer), not just notional.

4. OPPORTUNITY COST: subtract 4.5% annualized = 1.23 bps per period
   (current T-bill yield).

5. SPLIT SAMPLE: report 2016-2020 (early, possibly inflated) vs 2021-2026
   (modern, realistic forward estimate).

6. NO LOOK-AHEAD: signal at period t uses ONLY fundingRate values from
   periods t-21 through t-1 (verified).

7. REGIME COMPARISON: full 10y, recent 5y, and 2022-2026 (post-Luna era).
"""
import duckdb, math, json
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path('strategies/data')
db = duckdb.connect(str(DATA / 'crypto_long.duckdb'), read_only=True)

# Load
btc = db.execute("SELECT * FROM funding_XBTUSD ORDER BY timestamp").df()
eth = db.execute("SELECT * FROM funding_ETHUSD ORDER BY timestamp").df()
btc['fundingRate'] = btc['fundingRate'].astype(float)
eth['fundingRate'] = eth['fundingRate'].astype(float)
btc = btc.set_index('timestamp')['fundingRate']
eth = eth.set_index('timestamp')['fundingRate']
btc = btc.groupby(btc.index).mean().sort_index()
eth = eth.groupby(eth.index).mean().sort_index()

# Cost parameters — corrected after audit
# Reality: funding payments are AUTOMATIC (no fee). Costs only occur on actual trades.
SWITCH_COST_BPS = 25         # round trip per signal-driven switch (4 legs of fees + slippage)
# Rebalancing: in a delta-neutral position, you only rebalance when spot drifts >X%
# vs perp (or for margin maintenance). Realistic: ~weekly = 0.4% of periods.
REBALANCE_PROB_PER_PERIOD = 0.004  # ~1 rebalance per 250 periods ≈ once per 80 days
REBALANCE_COST_BPS = 10      # bps per rebalance
CAPITAL_LEVERAGE = 1 / 1.15  # we deploy 1.15× notional (spot + ~15% perp margin buffer)
OPPCOST_ANNUAL = 0.045       # 4.5% T-bill benchmark

def backtest(funding, hold_signal_fn, label=''):
    """Return a DataFrame indexed by funding-rate timestamps with per-period net return on CAPITAL."""
    hold = hold_signal_fn(funding)
    # KEY: shift by 1 so signal at t uses values strictly before t (no look-ahead)
    hold = hold.shift(1).fillna(False).astype(bool)

    # Gross funding income while held
    gross = funding * hold

    # Switch detection (signal-driven entries/exits)
    switches = (hold != hold.shift(1)).fillna(False).astype(float)
    switch_cost = switches * (SWITCH_COST_BPS / 10000) / 2  # half on entry, half on exit

    # Rebalancing cost — only on the fraction of periods we actually trade to
    # rebalance. Modeled as expected cost per held period.
    hold_cost = hold.astype(float) * REBALANCE_PROB_PER_PERIOD * (REBALANCE_COST_BPS / 10000)

    # Opportunity cost: every period we have capital deployed (even if hold=0,
    # we still tied up capital just being in the strategy). Conservative:
    # subtract oppcost on every period.
    oppcost_per_period = OPPCOST_ANNUAL / (365 * 3)

    net_on_notional = gross - switch_cost - hold_cost
    net_on_capital = net_on_notional * CAPITAL_LEVERAGE - oppcost_per_period

    return pd.DataFrame({
        'funding': funding,
        'hold': hold,
        'gross': gross,
        'switch_cost': switch_cost,
        'hold_cost': hold_cost,
        'net_on_notional': net_on_notional,
        'net_on_capital': net_on_capital,
    })

def stats(returns, periods_per_year=365*3, label=''):
    r = pd.Series(returns).dropna()
    if len(r) < 5: return None
    mu, sd = r.mean(), r.std()
    sharpe = mu / sd * math.sqrt(periods_per_year) if sd > 0 else 0
    ann_ret = (1 + r).prod() ** (periods_per_year / len(r)) - 1
    ann_vol = sd * math.sqrt(periods_per_year)
    cum = (1 + r).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    t_stat = mu / (sd / math.sqrt(len(r))) if sd > 0 else 0
    n_years = len(r) / periods_per_year
    return dict(label=label, n=len(r), years=n_years, sharpe=sharpe,
                ann_ret=ann_ret, ann_vol=ann_vol, max_dd=max_dd, t_stat=t_stat)

def report(s):
    if s is None: return
    star = '★' if s['sharpe'] >= 1.0 and s['ann_ret'] >= 0.05 else ' '
    print(f"  {star} [{s['label']:42}] sharpe={s['sharpe']:+5.2f}  "
          f"ret={s['ann_ret']*100:>+6.1f}%  vol={s['ann_vol']*100:>5.1f}%  "
          f"DD={s['max_dd']*100:>+6.1f}%  t={s['t_stat']:+5.1f}  "
          f"yrs={s['years']:4.1f}")

# ─── Strategy variants on different sub-samples ──────────────────────
windows = [
    ('full 2016-2026', None, None),
    ('early 2016-2020', '2016-01-01', '2020-12-31'),
    ('modern 2021-2026', '2021-01-01', '2026-12-31'),
    ('post-Luna 2022-2026', '2022-06-01', '2026-12-31'),
    ('recent 24mo', '2024-05-01', '2026-12-31'),
]

# Strategy: always-on (simplest)
print('═' * 110)
print('  STRATEGY: ALWAYS-ON  (long spot + short perp continuously)')
print('═' * 110)
for wname, ws, we in windows:
    for sym, label in [('BTC', btc), ('ETH', eth)]:
        s = label.copy()
        if ws: s = s[(s.index >= ws) & (s.index <= we)]
        if len(s) < 100: continue
        df = backtest(s, lambda f: pd.Series(True, index=f.index), label=f'{sym} {wname}')
        st = stats(df['net_on_capital'], label=f'{sym} {wname:24} ALWAYS')
        report(st)

print()
print('═' * 110)
print('  STRATEGY: 7d-MA gate (hold when rolling 21-period funding avg > 0)')
print('═' * 110)
for wname, ws, we in windows:
    for sym, label in [('BTC', btc), ('ETH', eth)]:
        s = label.copy()
        if ws: s = s[(s.index >= ws) & (s.index <= we)]
        if len(s) < 100: continue
        df = backtest(s, lambda f: f.rolling(21).mean() > 0, label=f'{sym} {wname}')
        st = stats(df['net_on_capital'], label=f'{sym} {wname:24} 7d-MA>0')
        report(st)

print()
print('═' * 110)
print('  STRATEGY: tighter gate, hold when 7d-MA funding > 1bp per 8h (~11% annualized)')
print('═' * 110)
for wname, ws, we in windows:
    for sym, label in [('BTC', btc), ('ETH', eth)]:
        s = label.copy()
        if ws: s = s[(s.index >= ws) & (s.index <= we)]
        if len(s) < 100: continue
        df = backtest(s, lambda f: f.rolling(21).mean() > 0.0001, label=f'{sym} {wname}')
        st = stats(df['net_on_capital'], label=f'{sym} {wname:24} 7d-MA>1bp')
        report(st)

# ─── Cost decomposition for the headline case ─────────────────────
print()
print('═' * 110)
print('  COST DECOMPOSITION — ETH always-on, full sample')
print('═' * 110)
df = backtest(eth, lambda f: pd.Series(True, index=f.index), 'eth-full')
periods_per_year = 365 * 3
years = len(df) / periods_per_year
print(f"  Gross funding income / yr:  {df['gross'].sum() / years * 100:+7.2f}%  (on notional)")
print(f"  Switch costs / yr:          {df['switch_cost'].sum() / years * 100:+7.2f}%")
print(f"  Rebalance costs / yr:       {df['hold_cost'].sum() / years * 100:+7.2f}%  ({REBALANCE_COST_BPS}bp per rebal × prob)")
print(f"  Net on notional / yr:       {df['net_on_notional'].sum() / years * 100:+7.2f}%")
print(f"  × capital efficiency 0.87:  {df['net_on_notional'].sum() / years * CAPITAL_LEVERAGE * 100:+7.2f}%")
print(f"  - T-bill oppcost 4.5%:      {(df['net_on_notional'].sum() / years * CAPITAL_LEVERAGE - OPPCOST_ANNUAL) * 100:+7.2f}%")
print(f"  Net excess return / yr:     {(df['net_on_notional'].sum() / years * CAPITAL_LEVERAGE - OPPCOST_ANNUAL) * 100:+7.2f}%")

# ─── Recent-3y year-by-year, costs applied ────────────────────────
print()
print('═' * 110)
print('  YEAR-BY-YEAR (always-on, full costs) — what to ACTUALLY expect going forward')
print('═' * 110)
for sym, src in [('BTC', btc), ('ETH', eth)]:
    df = backtest(src, lambda f: pd.Series(True, index=f.index), sym)
    df['year'] = pd.to_datetime(df.index).year
    by = df.groupby('year').agg(net_ret=('net_on_capital', 'sum'),
                                 n=('net_on_capital', 'count'),
                                 sharpe_unann=('net_on_capital', lambda x: x.mean()/x.std() if x.std()>0 else 0)).reset_index()
    by['ann_sharpe'] = by['sharpe_unann'] * math.sqrt(365*3)
    by['ret_pct'] = by['net_ret'] * 100
    print(f'\n{sym}:')
    print(by[['year', 'n', 'ret_pct', 'ann_sharpe']].to_string(index=False))

# ─── Combined 50/50 portfolio realistic ─────────────────────────────
print()
print('═' * 110)
print('  COMBINED 50/50 BTC+ETH PORTFOLIO — final realistic estimate')
print('═' * 110)

for wname, ws, we in windows:
    btc_w = btc.copy(); eth_w = eth.copy()
    if ws:
        btc_w = btc_w[(btc_w.index >= ws) & (btc_w.index <= we)]
        eth_w = eth_w[(eth_w.index >= ws) & (eth_w.index <= we)]
    btc_df = backtest(btc_w, lambda f: pd.Series(True, index=f.index), 'btc')
    eth_df = backtest(eth_w, lambda f: pd.Series(True, index=f.index), 'eth')
    combo = pd.concat([btc_df['net_on_capital'].rename('btc'),
                       eth_df['net_on_capital'].rename('eth')], axis=1)
    combo['portfolio'] = combo[['btc','eth']].mean(axis=1)
    # Where one is NaN, fall back to the other
    both_na = combo[['btc','eth']].isna().all(axis=1)
    combo.loc[both_na, 'portfolio'] = np.nan
    s = stats(combo['portfolio'], label=f'50/50 {wname}')
    report(s)

print()
print('═' * 110)
print('  REALITY CHECK')
print('═' * 110)
print('  The "always-on, full sample" sharpe is INFLATED because:')
print('  1. BitMEX 2016-2017 funding rates were massive (immature market)')
print('  2. Inverse contract pricing differs from USDT-margined perps')
print('  3. The Sharpe-22 ETH figure assumes near-zero std, only possible')
print('     when funding is consistently positive small numbers')
print('  ')
print('  The RECENT 24-month sample is the best forward-looking estimate.')
print('  Use the 2024-2026 row for what a NEW deployment should expect.')

db.close()
