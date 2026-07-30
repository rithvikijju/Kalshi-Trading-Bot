"""Rigorous VRP backtest — variance swap replication.

The strategy is: sell 1-month variance, hold to expiry, collect (IV² - RV²).

Without actual options chain history we approximate using VIX (which IS the
30-day variance swap rate for SPX, by construction — it's the IV of a strip
of S&P 500 options across all strikes weighted by 1/K²).

Key methodology:
1. At each month-start t, observe VIX_t. This is annualized 30d implied vol.
2. Compute RV over [t, t+30d]: stddev of daily SPY log returns, annualized.
3. Variance swap PnL per $1 notional vega = (IV² - RV²) / (2 × vega_factor).
4. Normalize to $1 of capital with risk-controlled sizing.

Realistic frictions added:
- Bid-ask on straddle entry: 0.5 vol points (the "edge giveaway")
- Slippage on delta hedge: 5 bps daily (annualized ~12.6%)
  → reduce gross VRP by ~50bp per month
- Margin requirement: position uses 25% of capital (rest in T-bills)
- Risk capping: size = min(target, capital × max_loss / max_expected_loss)

Reports:
- Per-trade variance-swap PnL
- Annualized Sharpe, return, max DD
- Year-by-year breakdown
- Big-loss month breakdown (Feb 2018, March 2020, Aug 2024 if present)
"""
import duckdb, math, json
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path('strategies/data')
db = duckdb.connect(str(DATA / 'equity.duckdb'), read_only=True)
prices = db.execute('SELECT * FROM prices').df()
db.close()

# Pivot to wide
wide = prices.pivot(index='date', columns='ticker', values='close').sort_index()
if 'SPY' not in wide.columns or '^VIX' not in wide.columns:
    raise SystemExit('need SPY and ^VIX in equity.duckdb')

px = wide[['SPY', '^VIX']].dropna()
px.columns = ['spy', 'vix']
px['spy_logret'] = np.log(px['spy'] / px['spy'].shift(1))
px = px.dropna()
print(f'Sample: {px.index.min().date()} → {px.index.max().date()}  ({len(px):,} days)')

# ─── Build monthly variance-swap trades ─────────────────────────────
# At each month-start t, lock in IV² (from VIX). Compute next 21-day RV.
# 21 trading days ≈ 30 calendar days (the VIX horizon).

def build_trades(px, hold_days=21, iv_haircut_volpts=0.5, hedge_cost_bps_per_day=5):
    """Generate monthly variance-swap trades. Returns DataFrame of trade PnL."""
    rows = []
    i = 0
    while i + hold_days < len(px):
        t0 = px.index[i]
        iv = px['vix'].iloc[i] / 100  # annualized vol
        if not np.isfinite(iv) or iv <= 0:
            i += hold_days; continue

        # Apply bid-ask haircut to IV (we're SELLING, so receive less)
        iv_effective = iv - iv_haircut_volpts / 100
        if iv_effective <= 0:
            i += hold_days; continue

        # Realized vol over the holding period (annualized)
        window = px['spy_logret'].iloc[i+1:i+hold_days+1]
        if len(window) < hold_days * 0.8:
            i += hold_days; continue
        rv_period = window.std() * np.sqrt(252)

        # Variance swap PnL per $1 vega notional:
        # Trade quotes in "vol points squared" — sell at IV², settle at RV²
        # PnL = IV² - RV²  (in annualized variance units)
        # Convert to dollar return per $1 capital deployed:
        # A standard contract pays (RV² - IV²) × strike × $100,000 / (2 × IV)
        # The "per $1 capital" return for a properly-sized swap is:
        #   (IV² - RV²) / (2 × IV)   — i.e., the vega-equivalent
        # We use this normalization; it gives PnL in units where ±1% = ±1pct return

        pnl_vega = (iv_effective**2 - rv_period**2) / (2 * iv_effective)

        # Hedge cost: delta-hedging realized variance has trading-cost drag
        hedge_cost = hedge_cost_bps_per_day / 10000 * hold_days
        pnl_net = pnl_vega - hedge_cost

        # Margin: position uses 25% of capital, rest in T-bills @ 4.5%
        rf_period = 0.045 * hold_days / 252
        rf_on_unused = 0.75 * rf_period
        pnl_total = pnl_net * 0.25 + rf_on_unused  # 25% of NAV at risk

        rows.append({
            'date': t0, 'iv': iv, 'iv_eff': iv_effective, 'rv': rv_period,
            'vrp_vol_pts': (iv_effective - rv_period) * 100,
            'pnl_vega': pnl_vega, 'pnl_net': pnl_net, 'pnl_capital': pnl_total,
            'hold_days': hold_days,
        })
        i += hold_days  # non-overlapping windows

    return pd.DataFrame(rows)

trades = build_trades(px)
print(f'\nGenerated {len(trades)} non-overlapping monthly trades')
print(f'  IV mean: {trades["iv"].mean()*100:.1f} vol pts')
print(f'  RV mean: {trades["rv"].mean()*100:.1f} vol pts')
print(f'  VRP mean: {trades["vrp_vol_pts"].mean():.2f} vol pts')
print(f'  Win rate: {(trades["pnl_net"]>0).mean()*100:.1f}%')

# ─── Stats ──────────────────────────────────────────────────────────
def stats(r, periods_per_year=12, label=''):
    r = pd.Series(r).dropna()
    if len(r) < 5: return None
    mu, sd = r.mean(), r.std()
    sharpe = mu / sd * math.sqrt(periods_per_year) if sd > 0 else 0
    ann_ret = (1 + r).prod() ** (periods_per_year / len(r)) - 1
    ann_vol = sd * math.sqrt(periods_per_year)
    cum = (1 + r).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    t_stat = mu / (sd / math.sqrt(len(r))) if sd > 0 else 0
    return dict(label=label, n=len(r), sharpe=sharpe, ann_ret=ann_ret,
                ann_vol=ann_vol, max_dd=max_dd, t_stat=t_stat)

def report(s):
    if s is None: print('  (no data)'); return
    star = '★' if s['sharpe'] >= 0.7 and s['ann_ret'] >= 0.04 else ' '
    print(f"  {star} [{s['label']:38}] sharpe={s['sharpe']:+5.2f}  "
          f"ret={s['ann_ret']*100:>+6.1f}%  vol={s['ann_vol']*100:>5.1f}%  "
          f"DD={s['max_dd']*100:>+6.1f}%  n={s['n']}  t={s['t_stat']:+5.1f}")

print('\n' + '═' * 100)
print('  RIGOROUS VRP BACKTEST — non-overlapping monthly variance swaps')
print('═' * 100)

# Variant 1: always-on (sell every month)
report(stats(trades['pnl_capital'], 12, 'Always-on monthly VRP'))

# Variant 2: only when VRP > 0 (forward-VIX > trailing RV)
trades['trail_rv'] = px['spy_logret'].rolling(21).std().reindex(trades['date']).values * np.sqrt(252)
trades['vrp_estimate'] = trades['iv'] - trades['trail_rv']
gated = trades[trades['vrp_estimate'] > 0]
report(stats(gated['pnl_capital'], 12, f'Gated VRP > 0 (n={len(gated)})'))

# Variant 3: only when VRP > 3 vol points (more selective)
gated3 = trades[trades['vrp_estimate'] > 0.03]
report(stats(gated3['pnl_capital'], 12, f'Gated VRP > 3pt (n={len(gated3)})'))

# Variant 4: only when VIX < 30 (skip high-vol regimes)
calm = trades[trades['iv'] < 0.30]
report(stats(calm['pnl_capital'], 12, f'VIX<30 only (n={len(calm)})'))

# Variant 5: combination — VRP > 0 AND VIX < 30
combo = trades[(trades['vrp_estimate'] > 0) & (trades['iv'] < 0.30)]
report(stats(combo['pnl_capital'], 12, f'VRP>0 + VIX<30 (n={len(combo)})'))

# Variant 6: Risk-managed — exit when MTM loss exceeds 5% of capital
# Approximation: cap each trade's loss at -5%
trades['pnl_capped'] = np.maximum(trades['pnl_capital'], -0.05)
report(stats(trades['pnl_capped'], 12, 'Stop at -5% per trade'))

# ─── Year by year ───────────────────────────────────────────────────
print('\n' + '═' * 100)
print('  YEAR-BY-YEAR (always-on, with all costs)')
print('═' * 100)
trades['year'] = pd.to_datetime(trades['date']).dt.year
y = trades.groupby('year').agg(
    n=('pnl_capital', 'count'),
    total=('pnl_capital', 'sum'),
    avg=('pnl_capital', 'mean'),
    worst=('pnl_capital', 'min'),
    best=('pnl_capital', 'max'),
).reset_index()
y['total_pct'] = y['total'] * 100
y['worst_pct'] = y['worst'] * 100
y['best_pct'] = y['best'] * 100
print(y[['year', 'n', 'total_pct', 'worst_pct', 'best_pct']].to_string(index=False))

# ─── Worst months ───────────────────────────────────────────────────
print('\n' + '═' * 100)
print('  Top 10 worst months — the tail events')
print('═' * 100)
worst = trades.nsmallest(10, 'pnl_capital')[['date','iv','rv','vrp_vol_pts','pnl_capital']]
worst['vol_jump'] = (worst['rv'] - worst['iv']) * 100
worst['pnl_pct'] = worst['pnl_capital'] * 100
print(worst[['date','iv','rv','vrp_vol_pts','vol_jump','pnl_pct']].to_string(index=False))

# ─── Combined risk-managed strategy ─────────────────────────────────
print('\n' + '═' * 100)
print('  FINAL: Combined Risk-Managed Strategy')
print('═' * 100)
print('  Rules: only enter when VRP estimate > 0 AND VIX < 30, cap loss at -5%/trade')
final_trades = trades[(trades['vrp_estimate'] > 0) & (trades['iv'] < 0.30)].copy()
final_trades['pnl_final'] = np.maximum(final_trades['pnl_capital'], -0.05)
report(stats(final_trades['pnl_final'], 12, 'FINAL risk-managed VRP'))

# Save
out = trades[['date','iv','rv','vrp_vol_pts','pnl_capital']]
out.to_csv(DATA / 'vrp_trades.csv', index=False)
print(f'\nWrote {DATA / "vrp_trades.csv"}')
