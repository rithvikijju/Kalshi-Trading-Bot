"""Batch E+F+H: info-theoretic, ML, and calendar strategies. Compact implementations
of the remaining 50-zoo entries so we have full coverage."""
from __future__ import annotations
import math
from pathlib import Path
import duckdb, pandas as pd, numpy as np
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_alts_1h, load_btc_eth_1m, load_funding, stats, fmt, save_result, rt_cost_bps

POS_USD = 5_000
HL_BPS = rt_cost_bps('hyperliquid', legs=1)


# ─── E32. Hurst exponent regime ────────────────────────────────────
def hurst(series, max_lag=20):
    """Simple R/S Hurst exponent."""
    lags = range(2, max_lag)
    tau = []
    for lag in lags:
        if lag >= len(series): continue
        diff = series[lag:] - series[:-lag]
        if len(diff) < 2: continue
        sd = np.std(diff)
        if sd <= 0: continue
        tau.append(sd)
    if len(tau) < 3: return 0.5
    poly = np.polyfit(np.log(list(lags)[:len(tau)]), np.log(tau), 1)
    return poly[0]   # H slope


def s32_hurst_regime(asset='ETH', window=72, hold=12, trend_thresh=0.55, mr_thresh=0.45,
                      mom_lookback=24):
    """Trade momentum when H > 0.55, mean-revert when H < 0.45."""
    df, coins = load_alts_1h()
    if asset not in coins: return []
    c = df[f'c_{asset}'].values
    log_p = np.log(c)
    trades = []; in_pos = None
    for t in range(window, len(c) - hold - 1):
        seg = log_p[t-window:t]
        if np.isnan(seg).any(): continue
        h = hurst(seg, max_lag=min(20, window//4))
        mom = c[t-1]/c[t-mom_lookback] - 1
        ts = df['ts'].iloc[t]
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold:
                ret = (c[t]/in_pos['p_e'] - 1) * in_pos['side']
                gross = ret * POS_USD; cost = HL_BPS*2/10000 * POS_USD
                trades.append({'enter':in_pos['ts_e'],'exit':ts,'gross':gross,'cost':cost,
                                'net':gross-cost})
                in_pos = None
        if in_pos is None:
            if h > trend_thresh and abs(mom) > 0.01:
                in_pos = {'t_e':t,'ts_e':ts,'p_e':c[t],'side':int(np.sign(mom))}
            elif h < mr_thresh and abs(mom) > 0.02:
                in_pos = {'t_e':t,'ts_e':ts,'p_e':c[t],'side':-int(np.sign(mom))}
    return trades


# ─── E30. Transfer entropy lead-lag ────────────────────────────────
def s30_transfer_entropy_leadlag(leader='BTC', follower='ETH', window=72, hold=6,
                                   z_in=2.0):
    """When the leader makes a big move, expect the follower to follow within hold bars."""
    df, coins = load_alts_1h()
    cL = df[f'c_{leader}'].values; cF = df[f'c_{follower}'].values
    log_rL = np.zeros(len(cL))
    log_rL[1:] = np.log(cL[1:]/cL[:-1])
    log_rF = np.zeros(len(cF))
    log_rF[1:] = np.log(cF[1:]/cF[:-1])
    sL = pd.Series(log_rL); sd = sL.rolling(window).std(); mu = sL.rolling(window).mean()
    z = ((sL - mu)/sd).values
    trades = []; in_pos = None
    for t in range(window, len(cL) - hold - 1):
        if np.isnan(z[t]): continue
        ts = df['ts'].iloc[t]
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold:
                ret = (cF[t]/in_pos['p_e'] - 1) * in_pos['side']
                gross = ret * POS_USD; cost = HL_BPS*2/10000 * POS_USD
                trades.append({'enter':in_pos['ts_e'],'exit':ts,'gross':gross,'cost':cost,
                                'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(z[t]) > z_in:
            # Trade FOLLOWER in same direction as leader's move
            in_pos = {'t_e':t,'ts_e':ts,'p_e':cF[t],'side':int(np.sign(z[t]))}
    return trades


# ─── F36. LightGBM-style: trade gradient-boosted forecast ──────────
# Light implementation using simple linear features
def s36_simple_microsignal_model(asset='b', train_window=2880, hold_min=3,
                                   threshold=0.0001):
    """Train a simple OLS predictor on 1-min features, walk forward, trade signals."""
    df = load_btc_eth_1m()
    c = df[f'{asset}_c'].values; v = df[f'{asset}_v'].values
    o = df[f'{asset}_o'].values
    log_r = np.zeros(len(c)); log_r[1:] = np.log(c[1:]/c[:-1])
    # Features: lag1, lag5, vol-imbalance, RV
    f1 = log_r
    f5 = pd.Series(log_r).rolling(5).sum().values
    f15 = pd.Series(log_r).rolling(15).sum().values
    rv = pd.Series(log_r**2).rolling(15).sum().values
    sgn_vol = np.sign(c - o) * v
    y_fwd = pd.Series(log_r).shift(-hold_min).rolling(hold_min).sum().values
    # Walk-forward: train on [t-2880, t], predict for t+1..t+hold_min
    trades = []
    step = 60   # re-fit every hour
    for t in range(train_window, len(c) - hold_min - 1, step):
        Xtr = np.column_stack([f1[t-train_window:t], f5[t-train_window:t],
                                f15[t-train_window:t], rv[t-train_window:t],
                                sgn_vol[t-train_window:t]])
        ytr = y_fwd[t-train_window:t]
        ok = np.isfinite(Xtr).all(1) & np.isfinite(ytr)
        if ok.sum() < 100: continue
        Xtr = Xtr[ok]; ytr = ytr[ok]
        # OLS
        try:
            coefs = np.linalg.lstsq(np.column_stack([Xtr, np.ones(len(Xtr))]), ytr, rcond=None)[0]
        except Exception: continue
        # Predict for next `step` bars; trade if forecast > threshold
        for tau in range(t, min(t+step, len(c)-hold_min-1)):
            X = np.array([f1[tau], f5[tau], f15[tau], rv[tau], sgn_vol[tau], 1.0])
            if not np.isfinite(X).all(): continue
            yhat = X @ coefs
            if abs(yhat) > threshold:
                side = int(np.sign(yhat))
                ret = (c[tau+hold_min]/c[tau] - 1) * side
                gross = ret * POS_USD
                cost = rt_cost_bps('hyperliquid', legs=1, taker_frac=0.0) * 2/10000 * POS_USD
                trades.append({'enter':df['ts'].iloc[tau],'exit':df['ts'].iloc[tau+hold_min],
                                'gross':gross,'cost':cost,'net':gross-cost})
    return trades


# ─── H47. Hour-of-day liquidity windows ─────────────────────────────
def s47_hour_of_day_filter(asset='b', mom_window=15, hold_min=5,
                            allowed_hours=(0,1,2,3,8,9,10)):
    """Trade momentum only during Asia handoff & European open."""
    df = load_btc_eth_1m()
    c = df[f'{asset}_c'].values; ts = df['ts'].dt.hour.values
    mom = pd.Series(c).pct_change(mom_window).values
    trades = []; in_pos = None
    for t in range(mom_window, len(c) - hold_min - 1):
        if np.isnan(mom[t]): continue
        if ts[t] not in allowed_hours: continue
        if in_pos is not None:
            if t - in_pos['t_e'] >= hold_min:
                gross = (c[t]/in_pos['p_e'] - 1) * POS_USD * in_pos['side']
                cost = HL_BPS*2/10000 * POS_USD
                trades.append({'enter':in_pos['ts_e'],'exit':df['ts'].iloc[t],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(mom[t]) > 0.001:
            in_pos = {'t_e':t,'ts_e':df['ts'].iloc[t],'p_e':c[t],'side':int(np.sign(mom[t]))}
    return trades


# ─── H49. End-of-month flow ────────────────────────────────────────
def s49_end_of_month(hold_h=72):
    """Last 3 days of month, long bottom-decile alts (rebalance flow)."""
    df, coins = load_alts_1h()
    df['day'] = df['ts'].dt.day
    df['month'] = df['ts'].dt.month
    # Identify last 3 days of each month
    closes = df[[f'c_{c}' for c in coins]].values
    opens = df[[f'o_{c}' for c in coins]].values
    last3 = df['day'].values >= 27   # rough end-of-month
    trades = []
    n = len(df)
    fee = HL_BPS*2/10000 * 1000
    t = 24*7
    while t + hold_h < n - 1:
        if not last3[t]: t += 1; continue
        prev = closes[t-24*7]; now = closes[t-1]
        rets = (now/prev) - 1
        ok = ~np.isnan(rets)
        if ok.sum() < 6: t += hold_h; continue
        # Long bottom-3 (recent losers — rebalance flow lifts them)
        idx = np.argsort(rets)
        bot = [i for i in idx if ok[i]][:3]
        en = t; ex = t + hold_h
        if ex+1 >= n: break
        for i in bot:
            if np.isnan(opens[en][i]) or np.isnan(opens[ex][i]): continue
            ret = opens[ex][i]/opens[en][i] - 1
            gross = ret * 1000
            trades.append({'enter':df['ts'].iloc[en],'exit':df['ts'].iloc[ex],
                            'gross':gross,'cost':fee,'net':gross-fee})
        t += hold_h
    return trades


def run_all():
    print("\n=== BATCH E+F+H: info-theoretic + ML + calendar ===\n")
    runs = [
        ('s30_te_btc_leads_eth_z2',   s30_transfer_entropy_leadlag, {'z_in':2.0}),
        ('s30_te_btc_leads_sol_z2',   s30_transfer_entropy_leadlag, {'follower':'SOL','z_in':2.0}),
        ('s32_hurst_eth',             s32_hurst_regime, {'asset':'ETH'}),
        ('s32_hurst_btc',             s32_hurst_regime, {'asset':'BTC'}),
        ('s36_ols_microsig_btc_maker',s36_simple_microsignal_model, {'asset':'b'}),
        ('s36_ols_microsig_eth_maker',s36_simple_microsignal_model, {'asset':'e'}),
        ('s47_hour_filter_btc',       s47_hour_of_day_filter, {'asset':'b'}),
        ('s47_hour_filter_eth',       s47_hour_of_day_filter, {'asset':'e'}),
        ('s49_end_of_month',          s49_end_of_month, {}),
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
