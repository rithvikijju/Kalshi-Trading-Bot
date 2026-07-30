"""Batch B: funding-rate strategies on BTC/ETH (Aug 2018 - May 2026, 8yr).

Strategies 09-15. These collect funding payments by being short the perp;
basis-aware filter prevents shorting when basis exceeds funding (loss).
"""
from __future__ import annotations
import math
from pathlib import Path
import duckdb, pandas as pd, numpy as np
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_funding, stats, fmt, save_result, rt_cost_bps

# Funding paid 3x/day on Bybit-style (every 8h). On Bitmex (this data) 8h cycle too.
# Each funding payment = fundingRate * notional, paid from longs to shorts when fundingRate > 0.

POS_USD = 10_000   # per side
RT_BPS = rt_cost_bps('hyperliquid', legs=2, taker_frac=0.5)   # half maker half taker for entry+exit


def s09_eth_basis_aware_funding(min_funding_bps=2.0, exit_funding_bps=0.5,
                                  basis_safety_bps=3.0, only_post_luna=True):
    """ETH funding arb: short ETH perp + long ETH spot when funding > threshold AND
    funding > basis (so net carry is positive after spot moves)."""
    fd = load_funding()
    df = fd['ETH'].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    if only_post_luna:
        df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    df['funding_bps'] = df['fundingRate'] * 10000
    in_pos = None; trades = []
    for i, row in df.iterrows():
        ts = row['timestamp']; f_bps = row['funding_bps']; basis = row['basis_bps']
        if in_pos is None:
            if f_bps > min_funding_bps and (f_bps - basis) > basis_safety_bps:
                in_pos = {'enter':ts, 'perp_e':row['perp_close'], 'spot_e':row['spot_close'],
                          'cum_funding_bps':0.0}
        else:
            in_pos['cum_funding_bps'] += f_bps  # collect funding each tick (we're short perp)
            # Spot leg pnl & perp leg pnl
            spot_ret = row['spot_close']/in_pos['spot_e'] - 1
            perp_ret = row['perp_close']/in_pos['perp_e'] - 1
            spread_pnl_bps = (spot_ret - perp_ret) * 10000
            total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
            if f_bps < exit_funding_bps:
                gross = total_bps / 10000 * POS_USD
                cost  = RT_BPS / 10000 * POS_USD
                net = gross - cost
                trades.append({'enter':in_pos['enter'],'exit':ts,'side':'short_perp_long_spot',
                                'cum_funding_bps':in_pos['cum_funding_bps'],
                                'spread_bps':spread_pnl_bps,
                                'gross':gross,'cost':cost,'net':net})
                in_pos = None
    return trades


def s10_funding_momentum(asset='ETH', roc_lookback=24, roc_threshold=0.5,
                          basis_safety_bps=3.0, hold_periods=8):
    """Long the funding-arb trade when funding rate-of-change is positive."""
    fd = load_funding()
    df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    df['funding_bps'] = df['fundingRate'] * 10000
    df['roc'] = df['funding_bps'].diff(roc_lookback)
    trades = []; in_pos = None; bars_held = 0
    for i, row in df.iterrows():
        if in_pos is not None:
            bars_held += 1
            in_pos['cum_funding_bps'] += row['funding_bps']
            if bars_held >= hold_periods:
                spot_ret = row['spot_close']/in_pos['spot_e'] - 1
                perp_ret = row['perp_close']/in_pos['perp_e'] - 1
                spread_pnl_bps = (spot_ret - perp_ret) * 10000
                total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
                gross = total_bps/10000 * POS_USD
                cost = RT_BPS/10000 * POS_USD
                trades.append({'enter':in_pos['enter'],'exit':row['timestamp'],
                                'gross':gross,'cost':cost,'net':gross-cost,
                                'cum_funding_bps':in_pos['cum_funding_bps']})
                in_pos = None; bars_held = 0
        if in_pos is None and pd.notna(row['roc']) and row['roc'] > roc_threshold:
            if (row['funding_bps'] - row['basis_bps']) > basis_safety_bps:
                in_pos = {'enter':row['timestamp'],'perp_e':row['perp_close'],
                          'spot_e':row['spot_close'],'cum_funding_bps':0.0}
    return trades


def s11_funding_z_reversion(asset='ETH', lookback=30*3, z_in=3.0, z_out=0.5,
                              max_hold=8*3):
    """When funding > z>3 (extreme), fade — but fade by entering the SHORT-perp arb
    (yes — extreme positive funding = perfect entry for shorts)."""
    fd = load_funding()
    df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    df['funding_bps'] = df['fundingRate'] * 10000
    df['mu']  = df['funding_bps'].rolling(lookback).mean()
    df['sd']  = df['funding_bps'].rolling(lookback).std()
    df['z']   = (df['funding_bps'] - df['mu']) / df['sd']
    trades = []; in_pos = None; bars_held = 0
    for i, row in df.iterrows():
        if in_pos is not None:
            bars_held += 1
            in_pos['cum_funding_bps'] += row['funding_bps']
            exit_now = False
            if pd.notna(row['z']) and abs(row['z']) < z_out: exit_now = True
            if bars_held >= max_hold: exit_now = True
            if exit_now:
                spot_ret = row['spot_close']/in_pos['spot_e'] - 1
                perp_ret = row['perp_close']/in_pos['perp_e'] - 1
                spread_pnl_bps = (spot_ret - perp_ret) * 10000
                total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
                gross = total_bps/10000 * POS_USD
                cost = RT_BPS/10000 * POS_USD
                trades.append({'enter':in_pos['enter'],'exit':row['timestamp'],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None; bars_held = 0
        if in_pos is None and pd.notna(row['z']) and row['z'] > z_in:
            in_pos = {'enter':row['timestamp'],'perp_e':row['perp_close'],
                      'spot_e':row['spot_close'],'cum_funding_bps':0.0}
    return trades


def s12_funding_carry_cycle(asset='ETH'):
    """Trade based on funding-magnitude regime (Markov-style). High |funding| → enter."""
    fd = load_funding()
    df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    df['funding_bps'] = df['fundingRate'] * 10000
    # Define HIGH regime: |funding| > 75th percentile rolling 60d
    df['p75'] = df['funding_bps'].abs().rolling(60*3).quantile(0.75)
    df['regime'] = (df['funding_bps'].abs() > df['p75']).astype(int)
    trades = []; in_pos = None
    for i, row in df.iterrows():
        if in_pos is not None:
            in_pos['cum_funding_bps'] += row['funding_bps'] * np.sign(in_pos['side'])
            if row['regime'] == 0:
                spot_ret = row['spot_close']/in_pos['spot_e'] - 1
                perp_ret = row['perp_close']/in_pos['perp_e'] - 1
                # If we shorted perp (side=+1), profit when spot rises faster than perp
                spread_pnl_bps = (spot_ret - perp_ret) * 10000 * in_pos['side']
                total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
                gross = total_bps/10000 * POS_USD
                cost = RT_BPS/10000 * POS_USD
                trades.append({'enter':in_pos['enter'],'exit':row['timestamp'],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and row['regime'] == 1 and pd.notna(row['funding_bps']):
            side = 1 if row['funding_bps'] > 0 else -1
            in_pos = {'enter':row['timestamp'],'perp_e':row['perp_close'],
                      'spot_e':row['spot_close'],'cum_funding_bps':0.0,'side':side}
    return trades


def s13_funding_weekend_seasonality(asset='ETH'):
    """Weekend (Sat-Sun UTC) funding tends to be different from weekday.
    Enter Friday close, exit Monday open. Capture weekend funding carry."""
    fd = load_funding()
    df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    df['dow'] = df['timestamp'].dt.dayofweek   # Mon=0 Sun=6
    trades = []; in_pos = None
    for i, row in df.iterrows():
        if in_pos is not None:
            in_pos['cum_funding_bps'] += row['fundingRate']*10000
            # Exit on Monday (dow=0)
            if row['dow'] == 0:
                spot_ret = row['spot_close']/in_pos['spot_e'] - 1
                perp_ret = row['perp_close']/in_pos['perp_e'] - 1
                spread_pnl_bps = (spot_ret - perp_ret)*10000
                total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
                gross = total_bps/10000 * POS_USD
                cost = RT_BPS/10000 * POS_USD
                trades.append({'enter':in_pos['enter'],'exit':row['timestamp'],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and row['dow'] == 4:   # Friday entry
            in_pos = {'enter':row['timestamp'],'perp_e':row['perp_close'],
                      'spot_e':row['spot_close'],'cum_funding_bps':0.0}
    return trades


def s14_funding_btc_eth_spread(z_in=2.0, z_out=0.5, lookback=90*3):
    """When BTC funding >> ETH funding (or vice versa), the funding-yield spread
    is extreme. Enter the higher-funding side, exit when spread normalizes.
    HARD constraint: only sided we're entering if funding > basis_safety."""
    fd = load_funding()
    eth = fd['ETH'].copy(); btc = fd['BTC'].copy()
    eth['funding_bps'] = eth['fundingRate']*10000
    btc['funding_bps'] = btc['fundingRate']*10000
    m = eth[['timestamp','funding_bps','perp_close','spot_close','basis_bps']].merge(
        btc[['timestamp','funding_bps','perp_close','spot_close','basis_bps']],
        on='timestamp', suffixes=('_e','_b')).dropna().reset_index(drop=True)
    m = m[m['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    m['spread'] = m['funding_bps_e'] - m['funding_bps_b']
    m['mu'] = m['spread'].rolling(lookback).mean()
    m['sd'] = m['spread'].rolling(lookback).std()
    m['z']  = (m['spread'] - m['mu']) / m['sd']
    trades = []; in_pos = None
    for i, row in m.iterrows():
        if in_pos is not None:
            # Short the high-funding side
            if in_pos['side'] == 'eth_short':
                in_pos['cum_funding_bps'] += row['funding_bps_e']
            else:
                in_pos['cum_funding_bps'] += row['funding_bps_b']
            if pd.notna(row['z']) and abs(row['z']) < z_out:
                if in_pos['side'] == 'eth_short':
                    spot_ret = row['spot_close_e']/in_pos['spot_e_e'] - 1
                    perp_ret = row['perp_close_e']/in_pos['perp_e_e'] - 1
                else:
                    spot_ret = row['spot_close_b']/in_pos['spot_e_b'] - 1
                    perp_ret = row['perp_close_b']/in_pos['perp_e_b'] - 1
                spread_pnl_bps = (spot_ret - perp_ret)*10000
                total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
                gross = total_bps/10000 * POS_USD
                cost = RT_BPS/10000 * POS_USD
                trades.append({'enter':in_pos['enter'],'exit':row['timestamp'],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and pd.notna(row['z']):
            if row['z'] > z_in and (row['funding_bps_e'] - row['basis_bps_e']) > 3:
                in_pos = {'enter':row['timestamp'],'side':'eth_short',
                          'perp_e_e':row['perp_close_e'],'spot_e_e':row['spot_close_e'],
                          'cum_funding_bps':0.0}
            elif row['z'] < -z_in and (row['funding_bps_b'] - row['basis_bps_b']) > 3:
                in_pos = {'enter':row['timestamp'],'side':'btc_short',
                          'perp_e_b':row['perp_close_b'],'spot_e_b':row['spot_close_b'],
                          'cum_funding_bps':0.0}
    return trades


def s15_funding_overshoot_fade(asset='ETH', threshold_bps=15, hold_periods=3):
    """Fade single-period funding spikes — after a 1-period print > 15 bps,
    enter short-perp arb expecting funding to mean-revert."""
    fd = load_funding()
    df = fd[asset].dropna(subset=['fundingRate','perp_close','spot_close']).copy()
    df = df[df['timestamp'] >= pd.Timestamp('2022-07-01', tz='UTC')].reset_index(drop=True)
    df['funding_bps'] = df['fundingRate']*10000
    trades = []; in_pos = None; bars_held = 0
    for i, row in df.iterrows():
        if in_pos is not None:
            bars_held += 1
            in_pos['cum_funding_bps'] += row['funding_bps']
            if bars_held >= hold_periods:
                spot_ret = row['spot_close']/in_pos['spot_e'] - 1
                perp_ret = row['perp_close']/in_pos['perp_e'] - 1
                spread_pnl_bps = (spot_ret - perp_ret)*10000
                total_bps = in_pos['cum_funding_bps'] + spread_pnl_bps
                gross = total_bps/10000 * POS_USD
                cost = RT_BPS/10000 * POS_USD
                trades.append({'enter':in_pos['enter'],'exit':row['timestamp'],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None; bars_held = 0
        if in_pos is None and row['funding_bps'] > threshold_bps:
            in_pos = {'enter':row['timestamp'],'perp_e':row['perp_close'],
                      'spot_e':row['spot_close'],'cum_funding_bps':0.0}
    return trades


def run_all():
    print("\n=== BATCH B: funding strategies ===\n")
    runs = [
        ('s09_eth_basis_aware',           s09_eth_basis_aware_funding, {}),
        ('s09_btc_basis_aware',           s09_eth_basis_aware_funding, {}),   # reuse, will swap later
        ('s10_funding_mom_eth',           s10_funding_momentum, {'asset':'ETH'}),
        ('s10_funding_mom_btc',           s10_funding_momentum, {'asset':'BTC'}),
        ('s11_funding_z_eth',             s11_funding_z_reversion, {'asset':'ETH'}),
        ('s11_funding_z_btc',             s11_funding_z_reversion, {'asset':'BTC'}),
        ('s12_funding_cycle_eth',         s12_funding_carry_cycle, {'asset':'ETH'}),
        ('s12_funding_cycle_btc',         s12_funding_carry_cycle, {'asset':'BTC'}),
        ('s13_weekend_seasonality_eth',   s13_funding_weekend_seasonality, {'asset':'ETH'}),
        ('s13_weekend_seasonality_btc',   s13_funding_weekend_seasonality, {'asset':'BTC'}),
        ('s14_btc_eth_funding_spread',    s14_funding_btc_eth_spread, {}),
        ('s15_funding_overshoot_eth',     s15_funding_overshoot_fade, {'asset':'ETH'}),
        ('s15_funding_overshoot_btc',     s15_funding_overshoot_fade, {'asset':'BTC'}),
    ]
    results = []
    for sid, fn, kw in runs:
        try:
            trades = fn(**kw)
            s = stats(trades, sid)
            print(fmt(s))
            save_result(sid, sid, 'hyperliquid', s)
            results.append((sid, s, trades))
        except Exception as e:
            print(f"  [{sid}] ERROR: {e}")
    return results


if __name__ == '__main__':
    run_all()
