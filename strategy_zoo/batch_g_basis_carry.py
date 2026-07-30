"""Batch G: continuous basis carry + perp-spot arb on full 2018-2026 history.

The PROPER way to backtest funding arb is to track NAV daily, not count discrete
trades. Each funding cycle (8h), you earn fundingRate * position. Compute
cumulative NAV, Sharpe on daily returns. Capacity-aware sizing.
"""
from __future__ import annotations
import math
from pathlib import Path
import duckdb, pandas as pd, numpy as np
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_funding, save_result, rt_cost_bps

# Realistic exchange fees for cash-and-carry
SPOT_FEE_BPS = 5      # Coinbase maker spot
PERP_FEE_BPS = 2.5    # Hyperliquid taker perp (or BitMEX historical)
SLIP_BPS     = 1.5    # per leg
RT_2LEG_BPS  = (SPOT_FEE_BPS + SLIP_BPS) * 2 + (PERP_FEE_BPS + SLIP_BPS) * 2   # ~20 bps RT


def carry_nav(asset='ETH', enter_bps=0, exit_bps=-100, basis_safety_bps=-1000,
               start_date='2022-07-01', notional_pos=10_000, fee_bps=RT_2LEG_BPS):
    """Track NAV through continuous basis arb.
    - Enter when fundingRate > enter_bps AND (funding - basis) > basis_safety
    - Exit when fundingRate < exit_bps
    - Each 8h cycle while in position: NAV += fundingRate * notional
    - At entry/exit: NAV -= fee_bps * notional / 2 (entry) and another /2 (exit)
    - Spot/perp move: realized via spread change at exit (in this simple model,
      assume perfect hedge → no spread P&L beyond fees)
    Returns: nav_series (daily), trade_count, summary stats.
    """
    fd = load_funding()
    df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    df = df[df['timestamp'] >= pd.Timestamp(start_date, tz='UTC')].reset_index(drop=True)
    df['funding_bps'] = df['fundingRate'] * 10000

    n = len(df)
    nav = np.zeros(n)
    nav[0] = notional_pos
    in_pos = False
    n_trades = 0
    last_perp = None; last_spot = None
    for i in range(n):
        row = df.iloc[i]
        f_bps = row['funding_bps']
        basis = row['basis_bps'] if pd.notna(row['basis_bps']) else 0.0
        if i > 0: nav[i] = nav[i-1]
        # Entry
        if not in_pos and f_bps > enter_bps and (f_bps - basis) > basis_safety_bps:
            nav[i] -= fee_bps/2/10000 * notional_pos   # half RT on entry
            in_pos = True
            last_perp = row['perp_close']; last_spot = row['spot_close']
            n_trades += 1
        # Collect funding
        if in_pos:
            nav[i] += f_bps/10000 * notional_pos
            # Realized spread change (perp - spot) since last fill
            if last_perp and last_spot and pd.notna(row['perp_close']) and pd.notna(row['spot_close']):
                d_perp = row['perp_close']/last_perp - 1
                d_spot = row['spot_close']/last_spot - 1
                # We're SHORT perp + LONG spot → gain = d_spot - d_perp
                nav[i] += (d_spot - d_perp) * notional_pos
                last_perp = row['perp_close']; last_spot = row['spot_close']
        # Exit
        if in_pos and f_bps < exit_bps:
            nav[i] -= fee_bps/2/10000 * notional_pos
            in_pos = False
            last_perp = None; last_spot = None

    # Aggregate to daily
    df['nav'] = nav
    df['day'] = df['timestamp'].dt.floor('D')
    daily = df.groupby('day')['nav'].last().reset_index()
    daily['ret'] = daily['nav'].pct_change()
    return daily, df, n_trades


def metrics(daily, label='', starting=10_000):
    rets = daily['ret'].dropna().values
    if len(rets) < 2:
        return {'label':label,'n_days':0,'tot_ret':0,'sharpe':0,'mdd':0,'apr':0}
    sh = rets.mean()/rets.std() * math.sqrt(365) if rets.std() > 0 else 0
    nav = daily['nav'].values
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak)/peak
    mdd = dd.min()
    span_d = (pd.Timestamp(daily['day'].iloc[-1]) - pd.Timestamp(daily['day'].iloc[0])).days
    apr = (nav[-1]/starting) ** (365/max(span_d,1)) - 1
    return {'label':label,'n_days':len(daily),'tot_ret':nav[-1]/starting - 1,
            'sharpe':sh,'mdd':mdd,'apr':apr,'span_d':span_d,'final_nav':nav[-1]}


def fmt(m):
    if m['n_days'] == 0: return f'  [{m["label"]}] no days'
    return (f'  [{m["label"]:48}] days={m["n_days"]:>5} span={m["span_d"]:>5}d  '
            f'APR={m["apr"]*100:>+6.1f}%  Sharpe={m["sharpe"]:>+5.2f}  MDD={m["mdd"]*100:>+5.1f}%  '
            f'final=${m["final_nav"]:>9.0f}')


def s09b_continuous_basis_carry():
    """Continuous version of s09: stay in arb while funding > basis_safety."""
    print("\n=== Continuous basis carry — ETH ===")
    for params in [
        # (enter_bps, exit_bps, basis_safety, start)
        (0,    -100,  -1000, '2022-07-01'),     # always on (post-Luna)
        (1,    -10,   -50,   '2022-07-01'),     # mild filter
        (2,    -5,    0,     '2022-07-01'),     # require funding > basis
        (5,    -5,    0,     '2022-07-01'),     # aggressive entry
        (0,    -100,  -1000, '2018-08-01'),     # full history
    ]:
        enter, exit_, safety, start = params
        daily, _, ntrades = carry_nav('ETH', enter_bps=enter, exit_bps=exit_,
                                       basis_safety_bps=safety, start_date=start)
        m = metrics(daily, f'ETH e={enter} x={exit_} safe={safety} {start[:7]} trades={ntrades}')
        print(fmt(m))
        save_result('s09b_eth_carry', m['label'], 'hyperliquid', {
            'n':ntrades,'win_pct':0,'tpd':ntrades/max(m['span_d'],1),
            'gross':0,'cost':0,'net':m['final_nav']-10_000,
            'sharpe':m['sharpe'],'max_dd':abs(m['mdd']),'span_d':m['span_d'],
            'cap':10_000}, notes=f'apr={m["apr"]:.4f}')

    print("\n=== Continuous basis carry — BTC ===")
    for params in [
        (0,    -100,  -1000, '2022-07-01'),
        (1,    -10,   -50,   '2022-07-01'),
        (5,    -5,    0,     '2022-07-01'),
        (0,    -100,  -1000, '2018-08-01'),
    ]:
        enter, exit_, safety, start = params
        daily, _, ntrades = carry_nav('BTC', enter_bps=enter, exit_bps=exit_,
                                       basis_safety_bps=safety, start_date=start)
        m = metrics(daily, f'BTC e={enter} x={exit_} safe={safety} {start[:7]} trades={ntrades}')
        print(fmt(m))
        save_result('s09b_btc_carry', m['label'], 'hyperliquid', {
            'n':ntrades,'win_pct':0,'tpd':ntrades/max(m['span_d'],1),
            'gross':0,'cost':0,'net':m['final_nav']-10_000,
            'sharpe':m['sharpe'],'max_dd':abs(m['mdd']),'span_d':m['span_d'],
            'cap':10_000}, notes=f'apr={m["apr"]:.4f}')


def s44_perp_spot_basis_arb():
    """When basis_bps > funding_bps + safety_margin, the perp is rich:
    short perp, long spot, collect (basis - funding) per cycle."""
    print("\n=== Perp-spot basis arb (basis > funding) ===")
    for asset in ['ETH', 'BTC']:
        fd = load_funding()
        df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
        df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
        df['funding_bps'] = df['fundingRate'] * 10000
        n = len(df); nav = np.zeros(n); nav[0] = 10_000
        in_pos = False; ntrades = 0; last_perp = None; last_spot = None
        for i, row in df.iterrows():
            if i > 0: nav[i] = nav[i-1]
            # Enter when basis is well above funding (perp expensive relative to funding)
            spread = row['basis_bps'] - row['funding_bps']
            if not in_pos and spread > 5:
                nav[i] -= 10/10000 * 10_000   # 10 bps half-RT
                in_pos = True; ntrades += 1
                last_perp = row['perp_close']; last_spot = row['spot_close']
            if in_pos:
                nav[i] += row['funding_bps']/10000 * 10_000
                if last_perp and last_spot:
                    d_perp = row['perp_close']/last_perp - 1
                    d_spot = row['spot_close']/last_spot - 1
                    nav[i] += (d_spot - d_perp) * 10_000
                    last_perp = row['perp_close']; last_spot = row['spot_close']
            if in_pos and spread < -5:
                nav[i] -= 10/10000 * 10_000
                in_pos = False; last_perp = None; last_spot = None
        df['nav'] = nav; df['day'] = df['timestamp'].dt.floor('D')
        daily = df.groupby('day')['nav'].last().reset_index()
        daily['ret'] = daily['nav'].pct_change()
        m = metrics(daily, f'{asset} basis>funding+5 trades={ntrades}')
        print(fmt(m))
        save_result('s44_perp_spot_basis', m['label'], 'hyperliquid', {
            'n':ntrades,'win_pct':0,'tpd':ntrades/max(m['span_d'],1),
            'gross':0,'cost':0,'net':m['final_nav']-10_000,
            'sharpe':m['sharpe'],'max_dd':abs(m['mdd']),'span_d':m['span_d'],
            'cap':10_000}, notes=f'apr={m["apr"]:.4f}')


if __name__ == '__main__':
    s09b_continuous_basis_carry()
    s44_perp_spot_basis_arb()
