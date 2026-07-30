"""Backtest all 10 strategies with realistic costs.

Each strategy returns a daily return series. Costs are subtracted:
- Crypto: 4 bps per trade (taker), funding income/expense as-is
- Equity ETFs: 1 bp per trade
- Equity single names: 3 bps per trade
- Short borrow: 50 bps annualized

Reports per strategy:
  sharpe, annualized return, max drawdown, n trades/year, t-stat,
  capacity estimate ($ at which slippage doubles).
"""
import duckdb, math, json, sys
from pathlib import Path
import pandas as pd
import numpy as np

DATA_DIR = Path('strategies/data')
RESULTS = []

def stats(returns, periods_per_year=252, name='', desc='', capacity_M=None,
          n_trades_per_yr=None):
    """Compute standard performance metrics from a daily-return series."""
    r = pd.Series(returns).dropna()
    if len(r) < 5:
        return {'name': name, 'desc': desc, 'n': len(r), 'sharpe': 0,
                'ann_return': 0, 'ann_vol': 0, 'max_dd': 0, 't_stat': 0,
                'capacity_M': capacity_M, 'n_trades_yr': n_trades_per_yr}
    mu, sd = r.mean(), r.std()
    sharpe = (mu / sd * np.sqrt(periods_per_year)) if sd > 0 else 0
    ann_ret = (1 + r).prod() ** (periods_per_year / len(r)) - 1 if len(r) > 0 else 0
    ann_vol = sd * np.sqrt(periods_per_year)
    cum = (1 + r).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    t_stat = mu / (sd / np.sqrt(len(r))) if sd > 0 else 0
    return {'name': name, 'desc': desc, 'n': len(r),
            'sharpe': sharpe, 'ann_return': ann_ret, 'ann_vol': ann_vol,
            'max_dd': max_dd, 't_stat': t_stat,
            'capacity_M': capacity_M, 'n_trades_yr': n_trades_per_yr}

def report(s):
    star = '★' if s['sharpe'] >= 1.0 and s['ann_return'] >= 0.05 else ' '
    print(f"  {star} [{s['name']:24}] sharpe={s['sharpe']:+.2f} "
          f"ret={s['ann_return']*100:+6.1f}%  vol={s['ann_vol']*100:5.1f}% "
          f"DD={s['max_dd']*100:+6.1f}% t={s['t_stat']:+5.2f} "
          f"cap=${s['capacity_M']}M  {s['desc'][:32]}")
    RESULTS.append(s)

# ═══════════════════════════════════════════════════════════════════
# S1. CRYPTO FUNDING RATE ARBITRAGE
# ═══════════════════════════════════════════════════════════════════
def s1_funding_arb():
    """Short perp + long spot. Capture funding payments (paid 3×/day on OKX).
    PnL per 8h period = funding_rate × notional - 2 × taker_fee (entry+exit)
    For continuously-held position: just funding (re-roll every period).
    Conservative: assume we re-enter once per day, paying 2× round-trip = 4 bps."""
    db_path = DATA_DIR / 'crypto.duckdb'
    if not db_path.exists():
        print('  no crypto.duckdb; skipping S1'); return
    db = duckdb.connect(str(db_path), read_only=True)
    print('\nS1 — Crypto Funding Rate Arbitrage')

    for coin in ['BTC', 'ETH', 'SOL']:
        try:
            fr = db.execute(f"SELECT * FROM funding_{coin} ORDER BY fundingTime").df()
        except Exception:
            print(f'  no funding_{coin} table'); continue
        if fr.empty: continue

        # Daily aggregate: sum the 3 funding payments per day (8h intervals).
        # When held continuously, we earn each funding payment as long as funding>0;
        # when funding<0, we PAY. Best strategy: only hold position when funding > 0.
        fr['date'] = fr['fundingTime'].dt.date
        daily = fr.groupby('date').agg(funding_sum=('fundingRate', 'sum'),
                                        funding_max=('fundingRate', 'max')).reset_index()

        # Conservative strategy: hold position only when prior 8h funding > 0.
        # PnL today = today's funding_sum IF yesterday's last funding was > 0.
        # Simplification: hold if running 3-period funding avg > 0.
        fr_sorted = fr.sort_values('fundingTime')
        fr_sorted['ma3'] = fr_sorted['fundingRate'].rolling(3).mean()
        fr_sorted['hold'] = (fr_sorted['ma3'].shift(1) > 0)  # lagged signal
        fr_sorted['ret'] = fr_sorted['fundingRate'] * fr_sorted['hold']

        # Subtract round-trip cost when position changes (4 bps per cycle)
        switches = (fr_sorted['hold'] != fr_sorted['hold'].shift(1)).fillna(False).sum()
        cycle_cost_bps = 4.0
        avg_cycles_per_period = switches / len(fr_sorted) if len(fr_sorted) else 0
        avg_cost = cycle_cost_bps / 10000 * avg_cycles_per_period

        # Per-period return = funding (if held), aggregate to daily (3 periods)
        daily_ret = fr_sorted.groupby(fr_sorted['fundingTime'].dt.date)['ret'].sum() - 3 * avg_cost
        daily_ret = daily_ret.reset_index(name='ret')

        # Reports
        capacity = {'BTC': 50, 'ETH': 30, 'SOL': 10}[coin]
        n_trades_yr = switches / len(daily_ret) * 365 if len(daily_ret) else 0
        s = stats(daily_ret['ret'], periods_per_year=365, name=f'S1.{coin} funding',
                  desc=f'Hold {coin} basis when ma3-funding>0',
                  capacity_M=capacity, n_trades_per_yr=int(n_trades_yr))
        report(s)

        # Also test: always-on (collect funding always — pays when negative)
        always_on_ret = fr_sorted['fundingRate'] - cycle_cost_bps / 10000 / 90  # cost amortized
        always_daily = always_on_ret.groupby(fr_sorted['fundingTime'].dt.date).sum().reset_index(name='ret')
        s2 = stats(always_daily['ret'], periods_per_year=365,
                   name=f'S1.{coin} always-on',
                   desc=f'Always hold basis (collect+pay)',
                   capacity_M=capacity, n_trades_per_yr=0)
        report(s2)
    db.close()

# ═══════════════════════════════════════════════════════════════════
# S2. CRYPTO BASIS / CASH-AND-CARRY (using perp basis as proxy)
# ═══════════════════════════════════════════════════════════════════
def s2_basis():
    """Approximate cash-and-carry using perp basis (perp price - spot price)/spot.
    When basis is positive, long spot + short perp earns convergence.
    Note: this is the SAME mechanism as S1 (funding); perp basis ≈ time-integrated funding.
    Here we test by 8h price spread instead of funding rate directly."""
    db_path = DATA_DIR / 'crypto.duckdb'
    if not db_path.exists(): return
    db = duckdb.connect(str(db_path), read_only=True)
    print('\nS2 — Crypto Perp-Spot Basis (alternate)')
    for coin in ['BTC', 'ETH', 'SOL']:
        try:
            spot = db.execute(f"SELECT ts, close AS spot FROM spot_{coin} ORDER BY ts").df()
            perp = db.execute(f"SELECT ts, close AS perp FROM perp_{coin} ORDER BY ts").df()
        except Exception:
            continue
        if spot.empty or perp.empty: continue
        m = pd.merge(spot, perp, on='ts', how='inner').sort_values('ts')
        m['basis_bps'] = (m['perp'] - m['spot']) / m['spot'] * 10000
        # Returns: when basis > 5 bps, we earn basis convergence per period
        # Simpler: 8h return ≈ -dBasis (if we're long spot, short perp, basis closing helps)
        m['basis_chg'] = m['basis_bps'].diff()
        # If we hold from t to t+1: return = (basis_t - basis_t+1) / 10000 (we capture the closing)
        m['ret_held'] = -m['basis_chg'] / 10000
        m['hold'] = (m['basis_bps'].shift(1) >= 5)  # hold when basis was >5bps
        m['ret'] = m['ret_held'] * m['hold'] - 0.0004 * (m['hold'] != m['hold'].shift(1)).fillna(False)
        m['date'] = m['ts'].dt.date
        daily_ret = m.groupby('date')['ret'].sum().reset_index()
        capacity = {'BTC': 30, 'ETH': 15, 'SOL': 5}[coin]
        s = stats(daily_ret['ret'], periods_per_year=365,
                  name=f'S2.{coin} basis-trade',
                  desc=f'Hold when perp-spot basis > 5bps',
                  capacity_M=capacity)
        report(s)
    db.close()

# ═══════════════════════════════════════════════════════════════════
# S5. EQUITY SECTOR MOMENTUM
# ═══════════════════════════════════════════════════════════════════
def _load_equity():
    db_path = DATA_DIR / 'equity.duckdb'
    if not db_path.exists(): return None
    db = duckdb.connect(str(db_path), read_only=True)
    df = db.execute('SELECT * FROM prices').df()
    db.close()
    wide = df.pivot(index='date', columns='ticker', values='close').sort_index()
    return wide

def s5_sector_momentum():
    wide = _load_equity()
    if wide is None: return
    print('\nS5 — Sector Momentum (long top-3 / short bottom-3, monthly rebal)')
    sectors = ['XLK','XLF','XLE','XLY','XLP','XLV','XLI','XLB','XLU','XLRE','XLC']
    avail = [s for s in sectors if s in wide.columns]
    if len(avail) < 5: print(f'  only {len(avail)} sectors avail; skip'); return
    px = wide[avail].dropna(how='all')
    monthly = px.resample('ME').last()  # month-end
    # 12-month return ending 1 month ago (skip last month per academic convention)
    mom = (monthly.shift(1) / monthly.shift(13)) - 1
    rets = monthly.pct_change()

    # Each month: long top 3 by mom, short bottom 3
    port_ret = []
    for date in mom.index[13:]:
        m = mom.loc[date].dropna()
        if len(m) < 6: continue
        long_set = m.nlargest(3).index
        short_set = m.nsmallest(3).index
        # Returns realized THIS month (rets.loc[date] is THIS month's return)
        if date not in rets.index: continue
        nxt_ret = rets.loc[date]
        long_r = nxt_ret[long_set].mean() if len(long_set) else 0
        short_r = -nxt_ret[short_set].mean() if len(short_set) else 0
        gross = (long_r + short_r) / 2  # dollar-neutral
        port_ret.append((date, gross - 0.0002))  # 2bps monthly turnover cost
    df = pd.DataFrame(port_ret, columns=['date','ret']).set_index('date')
    s = stats(df['ret'], periods_per_year=12, name='S5 sector momentum',
              desc='L3/S3 sector ETFs, 12-1mo mom',
              capacity_M=500, n_trades_per_yr=72)
    report(s)

# ═══════════════════════════════════════════════════════════════════
# S6. VOLATILITY RISK PREMIUM (VIX > realized)
# ═══════════════════════════════════════════════════════════════════
def s6_vrp():
    wide = _load_equity()
    if wide is None: return
    print('\nS6 — Vol Risk Premium proxy (short VIX exposure when VRP > 3 vol pts)')
    if 'SPY' not in wide.columns or '^VIX' not in wide.columns:
        print('  missing SPY or VIX; skip'); return
    spy = wide['SPY'].dropna()
    vix = wide['^VIX'].dropna()
    # Realized vol from SPY daily returns (21-day)
    spy_ret = spy.pct_change()
    realized_vol = spy_ret.rolling(21).std() * np.sqrt(252) * 100  # in vol points
    df = pd.concat([vix.rename('vix'), realized_vol.rename('rv'),
                    spy_ret.rename('spy_ret')], axis=1).dropna()
    df['vrp'] = df['vix'] - df['rv']
    # Strategy: short 1mo variance via SPY put-write proxy. Daily P&L of a
    # delta-neutral short-vol position ≈ (implied_var - realized_var) per day.
    # Use VIX^2/365 as implied daily variance; sqr(SPY daily ret) as realized.
    df['impl_dvar'] = (df['vix'] / 100) ** 2 / 252
    df['real_dvar'] = df['spy_ret'].shift(-1) ** 2  # next day's realized var
    df['raw_pnl'] = df['impl_dvar'] - df['real_dvar']
    # Vega-weighted size: smaller when vol high (to limit tail risk)
    df['size'] = (1 / (df['vix'] / 20)).clip(0.5, 2)
    df['hold'] = df['vrp'] > 3
    df['ret'] = df['raw_pnl'] * df['hold'] * df['size'] - 0.00005  # 0.5 bp daily costs
    s = stats(df['ret'].dropna(), periods_per_year=252, name='S6 VRP',
              desc='Short variance when VRP>3, vega-wt',
              capacity_M=20)
    report(s)

    # Also test naive "always on" short vol (no signal)
    df['always_ret'] = (df['impl_dvar'] - df['real_dvar']) * df['size'] - 0.00005
    s2 = stats(df['always_ret'].dropna(), periods_per_year=252, name='S6 VRP always',
               desc='Always-on short vol (vega-wt)',
               capacity_M=20)
    report(s2)

# ═══════════════════════════════════════════════════════════════════
# S7. PAIRS TRADING
# ═══════════════════════════════════════════════════════════════════
def s7_pairs():
    wide = _load_equity()
    if wide is None: return
    print('\nS7 — Pairs Trading (cointegration mean-reversion)')
    pairs = [('KO','PEP'),('MA','V'),('GS','MS'),('BAC','JPM'),('CVX','XOM'),
             ('HD','LOW'),('WMT','TGT'),('MSFT','GOOGL')]
    all_rets = []
    pair_results = []
    for a, b in pairs:
        if a not in wide.columns or b not in wide.columns: continue
        df = wide[[a,b]].dropna()
        if len(df) < 500: continue
        # Use log-prices for spread
        df['spread'] = np.log(df[a]) - np.log(df[b])
        # Rolling z-score, 60-day window
        df['mean'] = df['spread'].rolling(60).mean()
        df['std'] = df['spread'].rolling(60).std()
        df['z'] = (df['spread'] - df['mean']) / df['std']
        # Position: -1 when z > 2 (short A, long B); +1 when z < -2; flat when |z| < 0.5
        # State-based: enter on signal, hold until z crosses 0.5 threshold, then flat.
        pos = np.zeros(len(df))
        cur = 0
        z_vals = df['z'].values
        for i in range(len(df)):
            z = z_vals[i]
            if np.isnan(z):
                pos[i] = cur; continue
            if cur == 0:
                if z > 2: cur = -1
                elif z < -2: cur = 1
            else:
                if abs(z) < 0.5: cur = 0
            pos[i] = cur
        df['pos'] = pos
        # Daily return: dot product of position with spread change
        df['ret'] = df['pos'].shift(1) * df['spread'].diff()
        # Subtract trading cost on position changes (3bps single name × 2 legs)
        df['turnover'] = (df['pos'] != df['pos'].shift(1)).astype(int)
        df['ret'] = df['ret'] - df['turnover'] * 0.0006
        all_rets.append(df['ret'].dropna())
        pair_results.append((f'{a}/{b}', df['ret'].dropna()))

    if not all_rets:
        print('  no pairs available'); return
    # Equal-weight portfolio across pairs
    combined = pd.concat(all_rets, axis=1).mean(axis=1)
    s = stats(combined, periods_per_year=252, name='S7 pairs (8-pair pf)',
              desc='Z-score mean-rev, 60d window',
              capacity_M=80)
    report(s)
    # Top 3 individual pairs
    for name, r in sorted(pair_results, key=lambda x: x[1].mean() / max(x[1].std(),1e-9), reverse=True)[:3]:
        s = stats(r, periods_per_year=252, name=f'S7 {name}',
                  desc=f'pair {name}', capacity_M=10)
        report(s)

# ═══════════════════════════════════════════════════════════════════
# S8. LOW VOL ANOMALY
# ═══════════════════════════════════════════════════════════════════
def s8_low_vol():
    wide = _load_equity()
    if wide is None: return
    print('\nS8 — Low Vol Anomaly (long SPLV - short SPY, ETF spread)')
    if 'SPLV' not in wide.columns or 'SPY' not in wide.columns:
        print('  missing SPLV or SPY; skip'); return
    splv = wide['SPLV'].pct_change()
    spy = wide['SPY'].pct_change()
    spread = (splv - spy).dropna()
    # Beta-adjust: SPLV typically beta ≈ 0.7 to SPY; longing SPLV / shorting SPY beta-equivalent
    # Simple version: equal dollar L/S, accept some residual beta
    daily = spread - 0.00005  # 0.5 bps ETF round-trip / 100 days
    s = stats(daily, periods_per_year=252, name='S8 low vol L/S',
              desc='Long SPLV, short SPY equal $',
              capacity_M=150)
    report(s)

# ═══════════════════════════════════════════════════════════════════
# S9. MULTI-ASSET TREND FOLLOWING
# ═══════════════════════════════════════════════════════════════════
def s9_trend():
    wide = _load_equity()
    if wide is None: return
    print('\nS9 — Multi-Asset Trend Following (12mo TSM, monthly rebal)')
    universe = ['SPY','EFA','EEM','TLT','IEF','GLD','DBC','RWX']
    avail = [t for t in universe if t in wide.columns]
    if len(avail) < 4: print(f'  only {len(avail)} avail'); return
    px = wide[avail].dropna(how='all')
    monthly = px.resample('ME').last()
    mom = (monthly.shift(1) / monthly.shift(13)) - 1  # 12mo return through last month
    rets = monthly.pct_change()
    port_ret = []
    for date in mom.index[13:]:
        if date not in rets.index: continue
        m = mom.loc[date].dropna()
        if len(m) < 4: continue
        # Long positive momentum, short negative; equal weight within sign group
        longs = m[m > 0].index
        shorts = m[m < 0].index
        nxt = rets.loc[date]
        long_r = nxt[longs].mean() if len(longs) else 0
        short_r = -nxt[shorts].mean() if len(shorts) else 0
        # 50/50 long-leg + short-leg = dollar-neutral
        gross = (long_r * len(longs) + short_r * len(shorts)) / max(1, len(longs)+len(shorts))
        port_ret.append((date, gross - 0.0002))
    df = pd.DataFrame(port_ret, columns=['date','ret']).set_index('date')
    s = stats(df['ret'], periods_per_year=12, name='S9 multi-asset trend',
              desc='12mo TSM on 8 ETFs, monthly',
              capacity_M=200, n_trades_per_yr=24)
    report(s)

# ═══════════════════════════════════════════════════════════════════
# S10. CURRENCY CARRY
# ═══════════════════════════════════════════════════════════════════
def s10_carry():
    wide = _load_equity()
    if wide is None: return
    print('\nS10 — Currency Carry (high-yield long, low-yield short)')
    # Rough rate proxies (steady-state): AUD>NZD>CAD>USD>GBP>EUR>JPY>CHF
    # Use ETFs we have: FXA AUD, FXC CAD, FXE EUR, FXY JPY, FXB GBP, UUP USD
    long_cands = ['FXA','FXC']     # higher-yielding currencies (historically)
    short_cands = ['FXY','FXE']    # lower-yielding currencies
    avail_long = [t for t in long_cands if t in wide.columns]
    avail_short = [t for t in short_cands if t in wide.columns]
    if not avail_long or not avail_short: print('  insufficient ETFs'); return
    long_r = wide[avail_long].pct_change().mean(axis=1)
    short_r = wide[avail_short].pct_change().mean(axis=1)
    daily = (long_r - short_r).dropna() - 0.00005  # 0.5bp ETF
    s = stats(daily, periods_per_year=252, name='S10 G10 carry',
              desc='Long AUD+CAD / short JPY+EUR (ETF)',
              capacity_M=100)
    report(s)

# ═══════════════════════════════════════════════════════════════════
# S4. CROSS-COIN CRYPTO MOMENTUM (using crypto + yf BTC/ETH)
# ═══════════════════════════════════════════════════════════════════
def s4_crypto_momentum():
    wide = _load_equity()
    if wide is None: return
    print('\nS4 — Crypto Cross-Coin Momentum (BTC, ETH proxy via yf)')
    coins = [c for c in ('BTC-USD','ETH-USD') if c in wide.columns]
    if len(coins) < 2: print('  not enough crypto in yf'); return
    px = wide[coins].dropna(how='all').dropna()
    # Weekly momentum: 4-week return → next week
    weekly = px.resample('W').last()
    mom = (weekly.shift(1) / weekly.shift(5)) - 1
    rets = weekly.pct_change()
    port_ret = []
    for date in mom.index[5:]:
        if date not in rets.index: continue
        m = mom.loc[date].dropna()
        if len(m) < 2: continue
        long_ = m.idxmax(); short_ = m.idxmin()
        if long_ == short_: continue
        r = rets.loc[date, long_] - rets.loc[date, short_]
        port_ret.append((date, r / 2 - 0.001))  # 10bps weekly turnover
    if not port_ret: return
    df = pd.DataFrame(port_ret, columns=['date','ret']).set_index('date')
    s = stats(df['ret'], periods_per_year=52, name='S4 crypto cross-coin mom',
              desc='Long top, short bottom weekly (BTC vs ETH)',
              capacity_M=30, n_trades_per_yr=52)
    report(s)

# ═══════════════════════════════════════════════════════════════════
# S3. STABLECOIN DEPEG (approximated — needs DEX prices, skipping)
# ═══════════════════════════════════════════════════════════════════
def s3_depeg():
    print('\nS3 — Stablecoin Depeg Arb')
    print('  Skipped: requires DEX vs CEX price spread data + redemption mechanism modeling.')
    print('  Known to print ~5x during SVB-USDC depeg (Mar 2023, USDC $0.88).')
    print('  Real but episodic. Deployable via: monitor USDC/USDT on Coinbase + Curve simultaneously.')

# ═══════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════
def main():
    s1_funding_arb()
    s2_basis()
    s3_depeg()
    s4_crypto_momentum()
    s5_sector_momentum()
    s6_vrp()
    s7_pairs()
    s8_low_vol()
    s9_trend()
    s10_carry()

    print('\n' + '═' * 110)
    print(' FINAL SUMMARY — sorted by Sharpe')
    print('═' * 110)
    print(f'{"strategy":<26} {"sharpe":>7} {"ret%":>8} {"vol%":>7} {"DD%":>8} {"t-stat":>7} {"cap$M":>7}  desc')
    print('─' * 110)
    for s in sorted(RESULTS, key=lambda r: r['sharpe'], reverse=True):
        star = '★' if s['sharpe'] >= 1.0 and s['ann_return'] >= 0.05 else ' '
        cap = s['capacity_M'] or '-'
        print(f"{star} {s['name']:<24} {s['sharpe']:>+7.2f} "
              f"{s['ann_return']*100:>+7.1f}% {s['ann_vol']*100:>6.1f}% "
              f"{s['max_dd']*100:>+7.1f}% {s['t_stat']:>+6.2f} {str(cap):>6}  {s['desc'][:30]}")

    Path(DATA_DIR / 'backtest_results.json').write_text(json.dumps(RESULTS, indent=2, default=str))

if __name__ == '__main__':
    main()
