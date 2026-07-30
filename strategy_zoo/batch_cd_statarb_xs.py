"""Batch C+D: stat-arb (PCA residual, RMT, copula, wavelet) and cross-sectional
(momentum, reversal, vol-scaled mom). Uses 20 alts at 1h."""
from __future__ import annotations
import math
from pathlib import Path
import duckdb, pandas as pd, numpy as np
from itertools import combinations
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_alts_1h, stats, fmt, save_result, rt_cost_bps

POS_USD = 5_000
HL_BPS = rt_cost_bps('hyperliquid', legs=2)   # 2-leg trade


# ─── C16. PCA factor residual ──────────────────────────────────────
def s16_pca_residual(window=168, z_in=2.0, z_out=0.5, max_hold=72,
                      n_factors=3, min_close=20):
    """Build log-return matrix, fit rolling PCA, trade idiosyncratic residual mean-rev."""
    df, coins = load_alts_1h()
    log_ret = pd.DataFrame({c: np.log(df[f'c_{c}']).diff() for c in coins})
    n = len(df); trades = []; in_pos = {}
    # For each bar t, compute rolling residual based on last `window` bars
    for t in range(window, n-1):
        r = log_ret.iloc[t-window:t].values
        if np.isnan(r).any(): continue
        # Center
        mu = r.mean(axis=0); rc = r - mu
        # PCA via SVD
        U, S, Vt = np.linalg.svd(rc, full_matrices=False)
        loadings = Vt[:n_factors]   # (k, n_assets)
        # Residual at time t (today's return)
        r_today = log_ret.iloc[t].values
        if np.isnan(r_today).any(): continue
        # Factor scores
        f = loadings @ (r_today - mu)
        proj = loadings.T @ f
        resid = (r_today - mu) - proj
        # Z-score residuals against history of residuals
        proj_hist = (rc @ loadings.T) @ loadings
        resid_hist = rc - proj_hist
        z = (resid - resid_hist.mean(0)) / (resid_hist.std(0) + 1e-12)
        ts = df['ts'].iloc[t]
        # Enter on z>z_in or z<-z_in
        for i, coin in enumerate(coins):
            if coin in in_pos:
                p = in_pos[coin]
                held = t - p['t_e']
                price_now = df[f'c_{coin}'].iloc[t]
                ret = (price_now/p['price_e'] - 1) * p['side']
                exit_now = False
                if abs(z[i]) < z_out: exit_now = True
                if held >= max_hold: exit_now = True
                if exit_now:
                    gross = ret * POS_USD
                    cost = HL_BPS/10000 * POS_USD
                    trades.append({'enter':p['ts_e'],'exit':ts,'coin':coin,
                                    'gross':gross,'cost':cost,'net':gross-cost})
                    del in_pos[coin]
            elif abs(z[i]) > z_in and not pd.isna(df[f'c_{coin}'].iloc[t]):
                in_pos[coin] = {'t_e':t,'ts_e':ts,'price_e':df[f'c_{coin}'].iloc[t],
                                 'side':-int(np.sign(z[i]))}   # mean-revert: z>0 → short
    return trades


# ─── C18. RMT-cleaned eigenportfolio ───────────────────────────────
def s18_rmt_eigenportfolio(window=336, lookahead=24, top_eigvec=1,
                            entry_threshold=1.5, exit_threshold=0.3, max_hold=48):
    """Marchenko-Pastur denoising — clean cov, build eigenportfolio, trade its
    short-horizon reversion."""
    df, coins = load_alts_1h()
    log_ret = pd.DataFrame({c: np.log(df[f'c_{c}']).diff() for c in coins}).fillna(0)
    n = len(df); trades = []
    Q = len(coins) / window
    if Q >= 1: return trades   # Need T > N
    lam_plus = (1 + np.sqrt(Q))**2  # MP upper bound for noise eigenvalues
    in_pos = None
    for t in range(window, n-1, 6):   # step every 6h
        R = log_ret.iloc[t-window:t].values
        if np.isnan(R).any(): continue
        R = (R - R.mean(0)) / (R.std(0)+1e-12)
        C = R.T @ R / window
        w, v = np.linalg.eigh(C)
        idx = np.argsort(w)[::-1]
        w = w[idx]; v = v[:, idx]
        # Replace noise eigenvalues with their MP mean
        sig2 = 1.0
        noise_mask = w < lam_plus * sig2
        w_clean = w.copy()
        w_clean[noise_mask] = w[noise_mask].mean()
        # Top eigenportfolio = top eigenvector
        top_v = v[:, 0]
        # Z-score eigenportfolio return
        ret_e = R @ top_v
        mu = ret_e.mean(); sd = ret_e.std()
        cur = ret_e[-1]
        z = (cur - mu)/(sd+1e-12)
        ts = df['ts'].iloc[t]
        # We weight ports by SECOND eigenvector — first is market, second is style
        style_v = v[:, 1] if v.shape[1]>1 else top_v
        ret_s = R @ style_v
        z_s = (ret_s[-1] - ret_s.mean()) / (ret_s.std()+1e-12)
        # Trade style eigenportfolio mean reversion
        if in_pos is not None:
            held = t - in_pos['t_e']
            r_now = log_ret.iloc[in_pos['t_e']:t][coins].values.sum(0)  # vec
            r_port_now = (r_now @ in_pos['weights'])
            exit_now = (abs(z_s) < exit_threshold) or held >= max_hold
            if exit_now:
                gross = r_port_now * POS_USD * in_pos['side']
                cost = HL_BPS/10000 * POS_USD * len(coins)/10   # ~scaled to nlegs
                trades.append({'enter':in_pos['ts_e'],'exit':ts,
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(z_s) > entry_threshold:
            in_pos = {'t_e':t,'ts_e':ts,'weights':style_v,
                       'side':-int(np.sign(z_s))}
    return trades


# ─── C21. Kalman β hedge alt-vs-BTC residual ───────────────────────
def s21_kalman_alt_btc_residual(alt='ETH', q_proc=1e-4, r_obs=1e-2,
                                  z_in=2.0, z_out=0.5, max_hold=48):
    """Time-varying β via Kalman, trade residual mean reversion."""
    df, coins = load_alts_1h()
    if alt not in coins or 'BTC' not in coins: return []
    y = np.log(df[f'c_{alt}']).diff().fillna(0).values
    x = np.log(df['c_BTC']).diff().fillna(0).values
    # KF: beta_t = beta_{t-1} + w_t (w~N(0,Q)); y_t = beta_t * x_t + e_t (e~N(0,R))
    beta = 0.0; P = 1.0; betas = []
    for t in range(len(y)):
        P_pred = P + q_proc
        K = P_pred * x[t] / (x[t]**2 * P_pred + r_obs)
        beta = beta + K * (y[t] - beta * x[t])
        P = (1 - K * x[t]) * P_pred
        betas.append(beta)
    betas = np.array(betas)
    # Residual
    resid = y - betas * x
    # Z-score residual rolling
    s_resid = pd.Series(resid)
    mu = s_resid.rolling(72).mean()
    sd = s_resid.rolling(72).std()
    z = ((s_resid - mu)/sd).values
    trades = []; in_pos = None
    for t in range(len(z)-1):
        if np.isnan(z[t]): continue
        if in_pos is not None:
            held = t - in_pos['t_e']
            # exit on mean revert OR max hold
            if abs(z[t]) < z_out or held >= max_hold:
                # PnL: long alt short β*BTC, weighted by side
                alt_ret = df[f'c_{alt}'].iloc[t+1]/df[f'c_{alt}'].iloc[in_pos['t_e']+1] - 1
                btc_ret = df['c_BTC'].iloc[t+1]/df['c_BTC'].iloc[in_pos['t_e']+1] - 1
                pnl = (alt_ret - in_pos['beta']*btc_ret) * in_pos['side']
                gross = pnl * POS_USD
                cost = HL_BPS/10000 * POS_USD
                trades.append({'enter':df['ts'].iloc[in_pos['t_e']],
                                'exit':df['ts'].iloc[t],
                                'gross':gross,'cost':cost,'net':gross-cost})
                in_pos = None
        if in_pos is None and abs(z[t]) > z_in:
            in_pos = {'t_e':t,'beta':betas[t],'side':-int(np.sign(z[t]))}
    return trades


# ─── D24. XS momentum ──────────────────────────────────────────────
def s24_xs_momentum(lookback=24, hold=12, top_n=3, pos_leg_usd=1000):
    df, coins = load_alts_1h()
    closes = df[[f'c_{c}' for c in coins]].values
    opens  = df[[f'o_{c}' for c in coins]].values
    tss = df['ts'].values; n = len(df)
    fee_leg = HL_BPS/10000 * pos_leg_usd
    trades = []
    t = lookback + 1
    while t + hold < n - 1:
        prev = closes[t-lookback]; now = closes[t-1]
        rets = (now/prev) - 1
        ok = ~np.isnan(rets)
        if ok.sum() < 2*top_n: t += hold; continue
        idx = np.argsort(rets)
        top_idx = [i for i in idx[::-1] if ok[i]][:top_n]
        bot_idx = [i for i in idx if ok[i]][:top_n]
        en, ex = t, t+hold
        if ex+1 >= n: break
        en_p = opens[en]; ex_p = opens[ex]
        for i in top_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        for i in bot_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = -ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        t += hold
    return trades


# ─── D25. XS reversal ───────────────────────────────────────────────
def s25_xs_reversal(lookback=12, hold=12, top_n=3, pos_leg_usd=1000):
    df, coins = load_alts_1h()
    closes = df[[f'c_{c}' for c in coins]].values
    opens  = df[[f'o_{c}' for c in coins]].values
    tss = df['ts'].values; n = len(df)
    fee_leg = HL_BPS/10000 * pos_leg_usd
    trades = []
    t = lookback + 1
    while t + hold < n - 1:
        prev = closes[t-lookback]; now = closes[t-1]
        rets = (now/prev) - 1
        ok = ~np.isnan(rets)
        if ok.sum() < 2*top_n: t += hold; continue
        idx = np.argsort(rets)
        top_idx = [i for i in idx[::-1] if ok[i]][:top_n]
        bot_idx = [i for i in idx if ok[i]][:top_n]
        en, ex = t, t+hold
        if ex+1 >= n: break
        en_p = opens[en]; ex_p = opens[ex]
        # SHORT top, LONG bottom (reversal)
        for i in top_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = -ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        for i in bot_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        t += hold
    return trades


# ─── D26. Vol-scaled XS momentum ───────────────────────────────────
def s26_xs_vol_scaled_momentum(lookback=24, hold=12, top_n=3, pos_leg_usd=1000, vol_window=48):
    df, coins = load_alts_1h()
    closes = df[[f'c_{c}' for c in coins]].values
    opens  = df[[f'o_{c}' for c in coins]].values
    tss = df['ts'].values; n = len(df)
    fee_leg = HL_BPS/10000 * pos_leg_usd
    # log returns
    log_ret = np.log(closes); log_ret[1:] = log_ret[1:] - log_ret[:-1]; log_ret[0] = 0
    trades = []
    t = max(lookback, vol_window) + 1
    while t + hold < n - 1:
        prev = closes[t-lookback]; now = closes[t-1]
        rets = (now/prev) - 1
        # Compute realized vol over vol_window
        vol_window_data = log_ret[t-vol_window:t]
        vols = np.std(vol_window_data, axis=0)
        scaled = rets / (vols + 1e-9)
        ok = ~np.isnan(scaled) & ~np.isnan(rets)
        if ok.sum() < 2*top_n: t += hold; continue
        idx = np.argsort(scaled)
        top_idx = [i for i in idx[::-1] if ok[i]][:top_n]
        bot_idx = [i for i in idx if ok[i]][:top_n]
        en, ex = t, t+hold
        if ex+1 >= n: break
        en_p = opens[en]; ex_p = opens[ex]
        for i in top_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        for i in bot_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = -ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        t += hold
    return trades


# ─── D27. Idiosyncratic reversal (residual on BTC) ─────────────────
def s27_idio_reversal(lookback=12, hold=6, top_n=3, pos_leg_usd=1000):
    """Compute residual of each coin's return vs BTC, short top residuals (winners) long bottom."""
    df, coins = load_alts_1h()
    closes = df[[f'c_{c}' for c in coins]].values
    opens  = df[[f'o_{c}' for c in coins]].values
    tss = df['ts'].values; n = len(df)
    btc_idx = coins.index('BTC')
    fee_leg = HL_BPS/10000 * pos_leg_usd
    trades = []
    t = lookback + 1
    while t + hold < n - 1:
        prev = closes[t-lookback]; now = closes[t-1]
        rets = (now/prev) - 1
        if np.isnan(rets[btc_idx]): t += hold; continue
        btc_ret = rets[btc_idx]
        # crude residual: alt_ret - btc_ret (β=1)
        resid = rets - btc_ret
        resid[btc_idx] = np.nan
        ok = ~np.isnan(resid)
        if ok.sum() < 2*top_n: t += hold; continue
        idx = np.argsort(resid)
        top_idx = [i for i in idx[::-1] if ok[i]][:top_n]
        bot_idx = [i for i in idx if ok[i]][:top_n]
        en, ex = t, t+hold
        if ex+1 >= n: break
        en_p = opens[en]; ex_p = opens[ex]
        # SHORT top residual winners, LONG bottom (mean-revert)
        for i in top_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = -ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        for i in bot_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i]/en_p[i] - 1
            gross = ret * pos_leg_usd
            trades.append({'enter':tss[en],'exit':tss[ex],'gross':gross,'cost':fee_leg,
                            'net':gross-fee_leg})
        t += hold
    return trades


def run_all():
    print("\n=== BATCH C+D: statarb + cross-sectional ===\n")
    runs = [
        ('s16_pca_residual',          s16_pca_residual, {}),
        ('s21_kalman_alt_btc_eth',    s21_kalman_alt_btc_residual, {'alt':'ETH'}),
        ('s21_kalman_alt_btc_sol',    s21_kalman_alt_btc_residual, {'alt':'SOL'}),
        ('s24_xs_momentum_24h_12h',   s24_xs_momentum, {}),
        ('s24_xs_momentum_48h_24h',   s24_xs_momentum, {'lookback':48,'hold':24}),
        ('s24_xs_momentum_168h_24h',  s24_xs_momentum, {'lookback':168,'hold':24}),
        ('s25_xs_reversal_12h_6h',    s25_xs_reversal, {}),
        ('s25_xs_reversal_24h_12h',   s25_xs_reversal, {'lookback':24,'hold':12}),
        ('s26_xs_vs_mom_24h_12h',     s26_xs_vol_scaled_momentum, {}),
        ('s26_xs_vs_mom_48h_24h',     s26_xs_vol_scaled_momentum, {'lookback':48,'hold':24}),
        ('s26_xs_vs_mom_168h_24h',    s26_xs_vol_scaled_momentum, {'lookback':168,'hold':24}),
        ('s27_idio_reversal_12h_6h',  s27_idio_reversal, {}),
        ('s27_idio_reversal_24h_12h', s27_idio_reversal, {'lookback':24,'hold':12}),
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
