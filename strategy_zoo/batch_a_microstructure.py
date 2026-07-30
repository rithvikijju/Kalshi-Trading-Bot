"""Batch A: microstructure on 1-min BTC/ETH. Strategies 01-08.
These have to overcome ~16 bps RT on HL — tough for HF."""
from __future__ import annotations
import math
from pathlib import Path
import duckdb, pandas as pd, numpy as np
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_btc_eth_1m, stats, fmt, save_result, rt_cost_bps

POS_USD = 10_000
HL_BPS = rt_cost_bps('hyperliquid', legs=1)   # single leg
HL_BPS_MAKER = rt_cost_bps('hyperliquid', legs=1, taker_frac=0.0)


def s01_ofi_predictive(asset='b', window=5, threshold=2.0, hold_min=3, pos_usd=POS_USD,
                         use_maker=True):
    """Crude OFI: signed volume = sign(close-open) * volume. Predict next-window return."""
    df = load_btc_eth_1m()
    o = df[f'{asset}_o'].values; c = df[f'{asset}_c'].values; v = df[f'{asset}_v'].values
    sgn = np.sign(c - o); ofi = sgn * v
    s = pd.Series(ofi)
    mu = s.rolling(window).mean(); sd = s.rolling(window).std()
    z = ((s - mu)/sd).values
    bps_cost = HL_BPS_MAKER if use_maker else HL_BPS
    trades = []; in_pos = None
    n = len(df); ts_all = df['ts'].values
    for t in range(n - hold_min - 1):
        if np.isnan(z[t]): continue
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold_min:
                ret = c[t]/in_pos['p_e'] - 1
                gross = ret * pos_usd * in_pos['side']
                cost = bps_cost*2/10000 * pos_usd
                trades.append({'enter':in_pos['ts_e'],'exit':ts_all[t],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(z[t]) > threshold:
            in_pos = {'t_e':t,'ts_e':ts_all[t],'p_e':c[t],'side':int(np.sign(z[t]))}
    return trades


def s02_hawkes_jump_fade(asset='b', window=15, jump_thresh_bps=20, hold_min=5):
    """When abs return > 20bps in 1 min, fade for 5min (jump reversal)."""
    df = load_btc_eth_1m()
    c = df[f'{asset}_c'].values; ret = np.zeros(len(c))
    ret[1:] = (c[1:]/c[:-1] - 1) * 10000
    trades = []; in_pos = None; ts_all = df['ts'].values
    for t in range(len(c) - hold_min - 1):
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold_min:
                gross = (c[t]/in_pos['p_e'] - 1) * POS_USD * in_pos['side']
                cost = HL_BPS_MAKER*2/10000 * POS_USD
                trades.append({'enter':in_pos['ts_e'],'exit':ts_all[t],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(ret[t]) > jump_thresh_bps:
            in_pos = {'t_e':t,'ts_e':ts_all[t],'p_e':c[t],'side':-int(np.sign(ret[t]))}
    return trades


def s05_hampel_mean_reversion(asset='b', window=30, k=3.0, threshold_sigma=2.0,
                               hold_min=5):
    """Hampel-cleaned z-score mean reversion on 1-min closes."""
    df = load_btc_eth_1m()
    c = df[f'{asset}_c'].values
    s = pd.Series(c)
    med = s.rolling(window).median()
    mad = (s - med).abs().rolling(window).median() * 1.4826
    z = ((s - med)/(mad+1e-9)).values
    trades = []; in_pos = None; ts_all = df['ts'].values
    for t in range(len(c) - hold_min - 1):
        if np.isnan(z[t]): continue
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold_min or (z[t]*in_pos['side']) < 0.5:
                gross = (c[t]/in_pos['p_e'] - 1) * POS_USD * in_pos['side']
                cost = HL_BPS_MAKER*2/10000 * POS_USD
                trades.append({'enter':in_pos['ts_e'],'exit':ts_all[t],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(z[t]) > threshold_sigma:
            in_pos = {'t_e':t,'ts_e':ts_all[t],'p_e':c[t],'side':-int(np.sign(z[t]))}
    return trades


def s07_rv_regime_rotation(asset='b', rv_window=60, mom_window=15, hold_min=5):
    """Trade momentum in HIGH realized vol regime, mean revert in LOW vol regime."""
    df = load_btc_eth_1m()
    c = df[f'{asset}_c'].values
    log_ret = np.zeros(len(c))
    log_ret[1:] = np.log(c[1:]/c[:-1])
    s = pd.Series(log_ret)
    rv = (s**2).rolling(rv_window).sum().values
    rv_med = pd.Series(rv).rolling(rv_window*5).median().values
    # Momentum signal
    mom = pd.Series(c).pct_change(mom_window).values
    trades = []; in_pos = None; ts_all = df['ts'].values
    for t in range(rv_window*5, len(c) - hold_min - 1):
        if np.isnan(rv[t]) or np.isnan(rv_med[t]) or np.isnan(mom[t]): continue
        regime_high = rv[t] > rv_med[t]
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold_min:
                gross = (c[t]/in_pos['p_e'] - 1) * POS_USD * in_pos['side']
                cost = HL_BPS_MAKER*2/10000 * POS_USD
                trades.append({'enter':in_pos['ts_e'],'exit':ts_all[t],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(mom[t]) > 0.0008:
            # High vol → momentum (chase); low vol → reversion (fade)
            side = int(np.sign(mom[t])) if regime_high else -int(np.sign(mom[t]))
            in_pos = {'t_e':t,'ts_e':ts_all[t],'p_e':c[t],'side':side}
    return trades


def run_all():
    print("\n=== BATCH A: microstructure 1-min ===\n")
    runs = [
        ('s01_ofi_btc_taker',     s01_ofi_predictive, {'asset':'b','use_maker':False}),
        ('s01_ofi_btc_maker',     s01_ofi_predictive, {'asset':'b','use_maker':True}),
        ('s01_ofi_eth_maker',     s01_ofi_predictive, {'asset':'e','use_maker':True}),
        ('s02_hawkes_fade_btc',   s02_hawkes_jump_fade, {'asset':'b'}),
        ('s02_hawkes_fade_eth',   s02_hawkes_jump_fade, {'asset':'e'}),
        ('s05_hampel_mr_btc',     s05_hampel_mean_reversion, {'asset':'b'}),
        ('s05_hampel_mr_eth',     s05_hampel_mean_reversion, {'asset':'e'}),
        ('s07_rv_rotation_btc',   s07_rv_regime_rotation, {'asset':'b'}),
        ('s07_rv_rotation_eth',   s07_rv_regime_rotation, {'asset':'e'}),
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
