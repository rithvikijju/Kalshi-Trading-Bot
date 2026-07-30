"""Basis-aware funding arb backtest — TRUE daily PnL = funding + d(basis).

This is the rigorous backtest. The previous version showed Sharpe 20+ which
is implausible because it ignored basis-trade risk (perp price diverging
from spot price). Real funds running this strategy report Sharpe 1.5-3.0.

Daily PnL for a long-spot + short-perp $1 notional position:
  - funding_received_today      (only on funding event days, every 8h)
  - + d(spot_close) ON LONG SPOT       (we're long $1 of spot)
  - - d(perp_close) ON SHORT PERP      (we're short $1 of perp)
  - = funding + (d_spot - d_perp)
  - = funding - d(basis)        where basis = perp - spot

So daily PnL = funding_received - d_basis_today.

We can also model rebalancing: if basis drifts, we'd rebalance to maintain
$1 each side. For now, assume daily mark-to-market without rebalance friction.
"""
import duckdb, math, json
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path('strategies/data')
db = duckdb.connect(str(DATA / 'crypto_long.duckdb'), read_only=True)

# Funding rates (8-hour intervals)
btc_fund = db.execute("SELECT * FROM funding_XBTUSD ORDER BY timestamp").df()
btc_fund['fundingRate'] = btc_fund['fundingRate'].astype(float)
btc_fund['date'] = pd.to_datetime(btc_fund['timestamp']).dt.date

eth_fund = db.execute("SELECT * FROM funding_ETHUSD ORDER BY timestamp").df()
eth_fund['fundingRate'] = eth_fund['fundingRate'].astype(float)
eth_fund['date'] = pd.to_datetime(eth_fund['timestamp']).dt.date

# Daily aggregate: total funding paid per UTC day (3 events per day)
btc_daily_fund = btc_fund.groupby('date')['fundingRate'].sum().reset_index()
btc_daily_fund.columns = ['date', 'funding_total']
eth_daily_fund = eth_fund.groupby('date')['fundingRate'].sum().reset_index()
eth_daily_fund.columns = ['date', 'funding_total']

# Daily OHLC for perp + spot index
btc_perp = db.execute("SELECT * FROM perp_XBTUSD ORDER BY timestamp").df()
btc_spot = db.execute("SELECT * FROM spot_XBT_index ORDER BY timestamp").df()
eth_perp = db.execute("SELECT * FROM perp_ETHUSD ORDER BY timestamp").df()
eth_spot = db.execute("SELECT * FROM spot_ETH_index ORDER BY timestamp").df()

print(f'BTC perp: {len(btc_perp):,}  BTC spot index: {len(btc_spot):,}')
print(f'ETH perp: {len(eth_perp):,}  ETH spot index: {len(eth_spot):,}')

# Build daily PnL: funding income + (spot_chg - perp_chg) = funding - d_basis
def build_daily_pnl(perp, spot, daily_fund, label):
    perp = perp.copy(); spot = spot.copy()
    perp['date'] = pd.to_datetime(perp['timestamp']).dt.date
    spot['date'] = pd.to_datetime(spot['timestamp']).dt.date
    px = perp[['date','close']].rename(columns={'close':'perp_close'}).merge(
         spot[['date','close']].rename(columns={'close':'spot_close'}),
         on='date', how='inner')
    px['basis'] = px['perp_close'] - px['spot_close']  # USD difference
    px['basis_pct'] = px['basis'] / px['spot_close']    # as fraction of spot

    # Returns (daily, on $1 notional)
    px['spot_ret'] = px['spot_close'].pct_change()
    px['perp_ret'] = px['perp_close'].pct_change()
    # Long $1 spot + short $1 perp: daily MTM = spot_ret - perp_ret = -d_basis_pct
    px['mtm_ret'] = px['spot_ret'] - px['perp_ret']

    # Join funding income
    px = px.merge(daily_fund, on='date', how='left')
    px['funding_total'] = px['funding_total'].fillna(0)

    # Total daily return (on $1 notional)
    px['daily_pnl'] = px['funding_total'] + px['mtm_ret']
    px['date'] = pd.to_datetime(px['date'])
    px = px.set_index('date').sort_index()
    return px

btc_pnl = build_daily_pnl(btc_perp, btc_spot, btc_daily_fund, 'BTC')
eth_pnl = build_daily_pnl(eth_perp, eth_spot, eth_daily_fund, 'ETH')
# Collapse any duplicate index rows (defensive)
btc_pnl = btc_pnl[~btc_pnl.index.duplicated(keep='first')]
eth_pnl = eth_pnl[~eth_pnl.index.duplicated(keep='first')]

# ─── Apply costs ──────────────────────────────────────────────────
ENTRY_COST = 0.0025   # 25 bps round trip (one-time per cycle)
REBALANCE_COST_PER_DAY = 0.0001  # 1 bp/day expected ongoing cost (= 36.5%/yr... wait that's still too high)
# Let me think: 10 bps per rebalance × ~1 rebalance per 20 days = 0.5 bps/day = 0.5/100 = 0.00005
REBALANCE_COST_PER_DAY = 0.00005  # 0.5 bp/day average
OPPCOST_PER_DAY = 0.045 / 365

def apply_costs(daily_pnl):
    return daily_pnl - REBALANCE_COST_PER_DAY - OPPCOST_PER_DAY

btc_pnl['net_daily'] = apply_costs(btc_pnl['daily_pnl'])
eth_pnl['net_daily'] = apply_costs(eth_pnl['daily_pnl'])

# ─── Stats ────────────────────────────────────────────────────────
def stats(r, label):
    r = r.dropna()
    if len(r) < 30: return None
    mu, sd = r.mean(), r.std()
    sharpe = mu/sd * math.sqrt(252) if sd > 0 else 0
    ann_ret = mu * 252
    ann_vol = sd * math.sqrt(252)
    cum = (1 + r).cumprod()
    max_dd = (cum/cum.cummax() - 1).min()
    t_stat = mu / (sd / math.sqrt(len(r))) if sd > 0 else 0
    return dict(label=label, n=len(r), years=len(r)/252,
                sharpe=sharpe, ann_ret=ann_ret, ann_vol=ann_vol,
                max_dd=max_dd, t_stat=t_stat,
                avg_funding=mu, avg_basis_chg=0)

def report(s):
    if s is None: return
    star = '★' if s['sharpe'] >= 1.0 and s['ann_ret'] >= 0.05 else ' '
    print(f"  {star} [{s['label']:38}] sharpe={s['sharpe']:+5.2f}  "
          f"ret={s['ann_ret']*100:>+6.1f}%  vol={s['ann_vol']*100:>5.1f}%  "
          f"DD={s['max_dd']*100:>+6.1f}%  t={s['t_stat']:+5.1f}  yrs={s['years']:.1f}")

print()
print('═' * 100)
print('  REAL Daily PnL = funding + (spot_chg - perp_chg) -- basis-aware')
print('═' * 100)

windows = [
    ('full sample',         None,         None),
    ('2016-2020',           '2016-01-01', '2020-12-31'),
    ('2021-2026',           '2021-01-01', '2026-12-31'),
    ('post-Luna 2022+',     '2022-06-01', '2026-12-31'),
    ('recent 24mo',         '2024-05-01', '2026-12-31'),
]

print('\n[ BTC ]')
for wname, ws, we in windows:
    df = btc_pnl.copy()
    if ws: df = df[(df.index >= ws) & (df.index <= we)]
    if len(df) < 30: continue
    s = stats(df['net_daily'], f'BTC {wname}')
    report(s)

print('\n[ ETH ]')
for wname, ws, we in windows:
    df = eth_pnl.copy()
    if ws: df = df[(df.index >= ws) & (df.index <= we)]
    if len(df) < 30: continue
    s = stats(df['net_daily'], f'ETH {wname}')
    report(s)

# Combined 50/50 portfolio
print('\n[ 50/50 BTC + ETH ]')
joined = pd.concat([btc_pnl['net_daily'].rename('btc'),
                    eth_pnl['net_daily'].rename('eth')], axis=1)
joined['port'] = joined[['btc','eth']].mean(axis=1)
both = joined[['btc','eth']].notna().all(axis=1)
joined.loc[~both, 'port'] = np.nan

for wname, ws, we in windows:
    df = joined.copy()
    if ws: df = df[(df.index >= ws) & (df.index <= we)]
    if len(df.dropna()) < 30: continue
    s = stats(df['port'], f'50/50 {wname}')
    report(s)

# Decomposition: what's the realistic source of variance?
print()
print('═' * 100)
print('  VARIANCE DECOMPOSITION (recent 24mo, ETH)')
print('═' * 100)
df = eth_pnl[eth_pnl.index >= '2024-05-01'].copy()
print(f"  Mean funding income / day:       {df['funding_total'].mean()*100:+6.3f}%")
print(f"  Mean basis-MTM / day:            {df['mtm_ret'].mean()*100:+6.3f}%")
print(f"  Std funding income / day:        {df['funding_total'].std()*100:6.3f}%")
print(f"  Std basis-MTM / day:             {df['mtm_ret'].std()*100:6.3f}%")
print(f"  Std combined daily PnL:          {df['daily_pnl'].std()*100:6.3f}%")
print(f"  Correlation funding ↔ basis-MTM: {df[['funding_total','mtm_ret']].corr().iloc[0,1]:+.3f}")
print(f"  → basis risk is {df['mtm_ret'].std() / df['funding_total'].std():.1f}× funding std")

# Also report worst single-day basis events
print()
print('═' * 100)
print('  Worst 10 days (basis-MTM moves), ETH')
print('═' * 100)
worst = eth_pnl.nsmallest(10, 'mtm_ret')[['mtm_ret', 'basis_pct', 'funding_total', 'daily_pnl']]
worst.columns = ['mtm_chg', 'basis_lvl', 'funding', 'total']
print((worst * 100).round(3).to_string())

db.close()
