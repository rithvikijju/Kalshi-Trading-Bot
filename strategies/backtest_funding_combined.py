"""Combined BTC + ETH funding arb portfolio with realistic costs.

Adds:
  - Combined 50/50 portfolio analysis
  - Year-by-year breakdown for each variant
  - Equity curve summary
  - Stress test: what happened around major events (FTX, Luna, COVID)
"""
import duckdb, math, json
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path('strategies/data')
db = duckdb.connect(str(DATA / 'crypto_long.duckdb'), read_only=True)

# Load both
btc = db.execute("SELECT * FROM funding_XBTUSD ORDER BY timestamp").df()
eth = db.execute("SELECT * FROM funding_ETHUSD ORDER BY timestamp").df()
btc['fundingRate'] = btc['fundingRate'].astype(float)
eth['fundingRate'] = eth['fundingRate'].astype(float)
btc = btc.set_index('timestamp')['fundingRate'].rename('btc')
eth = eth.set_index('timestamp')['fundingRate'].rename('eth')
# Some duplicate timestamps possible from API; collapse to mean
btc = btc.groupby(btc.index).mean().sort_index()
eth = eth.groupby(eth.index).mean().sort_index()

# ─── Strategy: hold each when 7d-MA > 0 ─────────────────────────────
btc_hold = (btc.rolling(21).mean() > 0).shift(1).fillna(False)
eth_hold = (eth.rolling(21).mean() > 0).shift(1).fillna(False)

# Apply realistic costs:
# - 10 bps round trip per switch (split across entry+exit)
# - 3 bps/day borrow on spot (=1 bp per 8h period)
COST_PER_SWITCH = 0.0010
SPOT_BORROW_PER_PERIOD = 0.0001  # 1bp per 8h ≈ 3.6% annualized

def apply_costs(returns, hold):
    switches = (hold != hold.shift(1)).fillna(False).astype(float)
    borrow_cost = hold.astype(float) * SPOT_BORROW_PER_PERIOD
    return returns - switches * COST_PER_SWITCH / 2 - borrow_cost

btc_ret = apply_costs(btc * btc_hold, btc_hold)
eth_ret = apply_costs(eth * eth_hold, eth_hold)

# Align timestamps
combined_df = pd.concat([btc_ret.rename('btc'), eth_ret.rename('eth')], axis=1)
combined_df['both'] = 0.5 * combined_df['btc'].fillna(0) + 0.5 * combined_df['eth'].fillna(0)
# When one is NaN (e.g., ETH didn't exist yet), use the other at full weight
both_available = (~combined_df['btc'].isna()) & (~combined_df['eth'].isna())
combined_df.loc[~both_available, 'both'] = combined_df.loc[~both_available, 'btc'].fillna(
    combined_df.loc[~both_available, 'eth']).fillna(0)

def stats(r, name):
    r = r.dropna()
    if len(r) < 5: return {}
    periods = 365 * 3
    mu, sd = r.mean(), r.std()
    sharpe = mu / sd * math.sqrt(periods) if sd > 0 else 0
    ann_ret = (1 + r).prod() ** (periods / len(r)) - 1
    ann_vol = sd * math.sqrt(periods)
    cum = (1 + r).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    t_stat = mu / (sd / math.sqrt(len(r))) if sd > 0 else 0
    n_years = len(r) / periods
    print(f'  {name:18}  n={len(r):>5}  years={n_years:>4.1f}  '
          f'sharpe={sharpe:>+5.2f}  ann_ret={ann_ret*100:>+6.1f}%  '
          f'vol={ann_vol*100:>5.1f}%  DD={max_dd*100:>+6.1f}%  t={t_stat:>+5.1f}')
    return {'name': name, 'n': len(r), 'years': n_years, 'sharpe': sharpe,
            'ann_ret': ann_ret, 'ann_vol': ann_vol, 'max_dd': max_dd, 't_stat': t_stat}

print('═' * 100)
print(' FUNDING ARB — 7d-MA gate, costs applied (10bp/switch + 1bp/period borrow)')
print('═' * 100)
res_btc = stats(combined_df['btc'], 'BTC alone')
res_eth = stats(combined_df['eth'], 'ETH alone')
res_both = stats(combined_df['both'], '50/50 portfolio')

# ─── Year by year (combined) ──────────────────────────────────────
print('\n' + '═' * 100)
print(' Year-by-year (50/50 portfolio, costs applied)')
print('═' * 100)
year_df = combined_df['both'].to_frame('ret').reset_index()
year_df['year'] = pd.to_datetime(year_df['timestamp']).dt.year
yearly = year_df.groupby('year').agg(
    n_periods=('ret', 'count'),
    total_ret=('ret', 'sum'),
    mean_ret=('ret', 'mean'),
    sd_ret=('ret', 'std'),
).reset_index()
yearly['ann_ret_pct'] = yearly['total_ret'] * 100  # non-compounded sum
yearly['sharpe'] = yearly['mean_ret'] / yearly['sd_ret'] * math.sqrt(365 * 3)
print(yearly[['year','n_periods','ann_ret_pct','sharpe']].to_string(index=False))

# ─── Stress test: specific event windows ──────────────────────────
print('\n' + '═' * 100)
print(' Stress windows')
print('═' * 100)
events = [
    ('2018 BTC crash', '2018-01-01', '2018-12-31'),
    ('2020 COVID', '2020-03-01', '2020-04-15'),
    ('2021 China ban / May 21', '2021-05-15', '2021-06-15'),
    ('2022 Luna/UST collapse', '2022-05-01', '2022-05-31'),
    ('2022 FTX collapse', '2022-11-01', '2022-12-15'),
    ('2024 spot ETF approval', '2024-01-10', '2024-02-10'),
]
for name, start, end in events:
    window = combined_df['both'][(combined_df.index >= start) & (combined_df.index <= end)]
    if len(window) > 0:
        total = window.sum() * 100
        max_dd_w = ((1 + window).cumprod() / (1 + window).cumprod().cummax() - 1).min() * 100
        print(f'  {name:32}  {start} → {end}   '
              f'total={total:>+6.2f}%   max_DD={max_dd_w:>+6.2f}%   n={len(window)}')

# ─── Capacity / sizing sanity ───────────────────────────────────────
print('\n' + '═' * 100)
print(' Implied PnL at various capital levels')
print('═' * 100)
ann_ret_eth = res_eth.get('ann_ret', 0)
ann_ret_btc = res_btc.get('ann_ret', 0)
ann_ret_both = res_both.get('ann_ret', 0)
print(f'  Capital      BTC@{ann_ret_btc*100:.1f}%  ETH@{ann_ret_eth*100:.1f}%  50/50@{ann_ret_both*100:.1f}%')
for cap in [10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]:
    print(f'  ${cap:>11,}      ${cap*ann_ret_btc:>9,.0f}     ${cap*ann_ret_eth:>9,.0f}     ${cap*ann_ret_both:>9,.0f}')

# ─── Save equity curve to CSV for plotting ──────────────────────────
out = combined_df[['btc','eth','both']].copy()
out['btc_cum'] = (1 + out['btc'].fillna(0)).cumprod()
out['eth_cum'] = (1 + out['eth'].fillna(0)).cumprod()
out['both_cum'] = (1 + out['both'].fillna(0)).cumprod()
out.to_csv(DATA / 'funding_equity_curve.csv')
print(f'\nWrote equity curve to {DATA / "funding_equity_curve.csv"}')
print(f'BTC final: ${(out["btc_cum"].iloc[-1])*1:.4f} → ${(out["btc_cum"].iloc[-1])*100000:.2f} on $100K starting')
print(f'ETH final: ${(out["eth_cum"].iloc[-1])*1:.4f} → ${(out["eth_cum"].iloc[-1])*100000:.2f} on $100K starting')
print(f'50/50 final: ${(out["both_cum"].iloc[-1])*1:.4f} → ${(out["both_cum"].iloc[-1])*100000:.2f} on $100K starting')

db.close()
