"""All MDD-focused strategy backtests for the full plan.

Builds 4 new strategy streams:
1. Mean reversion (regime-filtered)  — SPY z-score, only trade VIX<20
2. Stat arb sector basket            — Ledoit-Wolf shrinkage min-variance neutral
3. Multi-timeframe trend             — 1m+3m+6m+12m signal ensemble, vol-scaled
4. Alt-data overlay                  — VIX-as-proxy-for-sentiment regime gating

All output daily PnL series saved to data/_pnl_*.parquet for portfolio combo.
"""
from __future__ import annotations
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/rithvikijju/edge-bot/research/v2")
sys.path.insert(0, "/Users/rithvikijju/edge-bot/research/v2/ml_predict")
import numpy as np
import pandas as pd
from _data import load_many, load
from sklearn.covariance import LedoitWolf

DATA_OUT = "/Users/rithvikijju/edge-bot/research/v2/full_plan"


def stats(p: pd.Series):
    if p.std() == 0:
        return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0)
    s = p.mean() / p.std() * np.sqrt(252)
    eq = (1 + p).cumprod()
    return dict(sharpe=float(s), ann_ret=float(p.mean()*252),
                ann_vol=float(p.std()*np.sqrt(252)),
                mdd=float((eq/eq.cummax()-1).min()))


# ============================================================
# Strategy 1: Mean reversion with regime filter

def mean_reversion_regime():
    """Z-score reversion of SPY log-return on 5-day window, gated by VIX regime.
    The key insight: at daily SPY, fade short-term moves of >1.5σ only in calm regimes."""
    print("\n=== Strategy 1: Regime-filtered SPY mean reversion ===")
    spy = load("SPY", start="2005-01-01")
    vix = load("^VIX", start="2005-01-01")
    df = pd.concat([spy.rename("spy"), vix.rename("vix")], axis=1).dropna()
    df["ret"] = np.log(df["spy"] / df["spy"].shift(1))

    # signal: 5-day cumulative return z-score (mean-reversion at 1-week horizon)
    df["ret_5"] = df["ret"].rolling(5).sum()
    df["z"] = (df["ret_5"] - df["ret_5"].rolling(63).mean()) / df["ret_5"].rolling(63).std()

    # regime filter: VIX < 22 (calm-to-moderate)
    df["regime_ok"] = (df["vix"] < 22).astype(int)

    # trade rule: fade extreme 5d moves only in calm regime
    pos = np.zeros(len(df))
    state = 0
    z_vals = df["z"].values
    regime = df["regime_ok"].values
    for i in range(len(df)):
        if regime[i] == 0:
            state = 0
        else:
            if state == 0:
                if z_vals[i] < -1.5: state = +1
                elif z_vals[i] > +1.5: state = -1
            else:
                if abs(z_vals[i]) < 0.5: state = 0
        pos[i] = state
    df["pos"] = pos

    df["pnl"] = df["pos"].shift(1).fillna(0) * df["ret"]
    turn = df["pos"].diff().abs().fillna(0)
    df["pnl"] = df["pnl"] - turn * 2/10000
    df = df.loc["2010-01-01":]
    s = stats(df["pnl"])
    print(f"  Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")
    print(f"  Engaged: {(df['pos']!=0).mean()*100:.0f}% of days")
    df["pnl"].to_frame("pnl").to_parquet(f"{DATA_OUT}/_pnl_mr_regime.parquet")
    return df["pnl"], s


# ============================================================
# Strategy 2: Stat arb with Ledoit-Wolf shrinkage

def statarb_shrinkage():
    print("\n=== Strategy 2: Sector stat arb with Ledoit-Wolf shrinkage ===")
    SECTORS = ["XLE", "XLF", "XLK", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE"]
    px = load_many(SECTORS, start="2005-01-01", end="2026-06-01")
    px = px.dropna()
    rets = np.log(px / px.shift(1)).dropna()

    # rolling window: 60d trailing, refit weekly
    pnl_daily = []
    dates = []
    w_prev = pd.Series(0.0, index=rets.columns)

    window = 60
    for i in range(window, len(rets)):
        if i % 5 != 0 and i > window + 5:  # refit weekly
            # carry weights forward (with daily PnL though)
            pass
        else:
            R_win = rets.iloc[i-window:i]
            # Ledoit-Wolf shrinkage covariance
            try:
                lw = LedoitWolf().fit(R_win.values)
                cov = lw.covariance_
            except Exception:
                cov = R_win.cov().values
            # min-variance equal-return-target portfolio (residual reversion proxy):
            # weights ∝ -inv(cov) @ recent_returns_z; market neutral
            r_recent = R_win.iloc[-5:].mean().values  # avg of last 5d (signal)
            r_z = (r_recent - r_recent.mean()) / (r_recent.std() + 1e-8)
            try:
                inv = np.linalg.pinv(cov)
                w = -inv @ r_z
            except Exception:
                w = -r_z
            # market-neutral: subtract mean
            w = w - w.mean()
            # scale to gross 1.0
            if np.abs(w).sum() > 0:
                w = w / np.abs(w).sum()
            w_prev = pd.Series(w, index=rets.columns)
        r_today = rets.iloc[i]
        pnl_today = (w_prev * r_today).sum()
        # turnover cost (rough; only refit days have full turnover, but linear is OK approx)
        if i % 5 == 0:
            pnl_today -= 0.0005  # ~5bp per refit
        pnl_daily.append(pnl_today)
        dates.append(rets.index[i])

    pnl = pd.Series(pnl_daily, index=dates)
    pnl = pnl.loc["2018-01-01":]  # OOS-style window
    # vol-target to 8%
    if pnl.std() > 0:
        pnl = pnl / pnl.std() / np.sqrt(252) * 0.08
    s = stats(pnl)
    print(f"  Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")
    pnl.to_frame("pnl").to_parquet(f"{DATA_OUT}/_pnl_statarb_lw.parquet")
    return pnl, s


# ============================================================
# Strategy 3: Multi-timeframe trend following

def multi_trend():
    print("\n=== Strategy 3: Multi-timeframe vol-scaled trend ===")
    UNIVERSE = ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "USO", "DBC", "UUP", "HYG"]
    px = load_many(UNIVERSE, start="2005-01-01", end="2026-06-01")
    px = px.dropna(axis=1, thresh=int(0.5*len(px)))
    rets = np.log(px / px.shift(1))

    # signals: trailing 21d, 63d, 126d, 252d log-return → ensemble sign
    s21 = np.log(px / px.shift(21))
    s63 = np.log(px / px.shift(63))
    s126 = np.log(px / px.shift(126))
    s252 = np.log(px / px.shift(252))
    ensemble = (np.sign(s21) + np.sign(s63) + np.sign(s126) + np.sign(s252)) / 4.0

    # vol target per leg: 10% / realized vol
    rv60 = rets.rolling(60).std() * np.sqrt(252)
    w = ensemble.shift(1) * (0.10 / rv60.replace(0, np.nan))
    w = w.clip(lower=-3, upper=3)
    # normalize gross to 1.0
    gross = w.abs().sum(axis=1).replace(0, np.nan)
    w = w.divide(gross, axis=0).fillna(0)

    # PnL
    daily_pnl = (w * rets).sum(axis=1).fillna(0)
    turn = w.diff().abs().sum(axis=1).fillna(0)
    cost = turn * 5/10000
    pnl_net = (daily_pnl - cost).loc["2010-01-01":]

    # vol target to 10% AND drawdown-trigger leverage reduction (8% DD → cut 50%)
    if pnl_net.std() > 0:
        scaled = pnl_net / pnl_net.std() / np.sqrt(252) * 0.10
    else:
        scaled = pnl_net
    eq = (1 + scaled).cumprod()
    dd = eq / eq.cummax() - 1
    lev = (dd > -0.08).astype(float) + 0.5 * (dd <= -0.08).astype(float)
    pnl_adj = scaled * lev.shift(1).fillna(1)
    s = stats(pnl_adj)
    print(f"  Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")
    pnl_adj.to_frame("pnl").to_parquet(f"{DATA_OUT}/_pnl_trend_mtf.parquet")
    return pnl_adj, s


# ============================================================
# Strategy 4: Alt-data overlay (VIX regime as sentiment proxy)
# Demonstrates the framework. Real alt data would replace this with
# LLM-derived earnings/Fed-speech sentiment scores.

def alt_data_overlay():
    print("\n=== Strategy 4: Alt-data overlay (VIX regime → SPY positioning) ===")
    spy = load("SPY", start="2005-01-01")
    vix = load("^VIX", start="2005-01-01")
    df = pd.concat([spy.rename("spy"), vix.rename("vix")], axis=1).dropna()
    df["ret"] = np.log(df["spy"] / df["spy"].shift(1))
    # "sentiment" proxy: rolling VIX z-score
    df["vix_z"] = (df["vix"] - df["vix"].rolling(63).mean()) / df["vix"].rolling(63).std()

    # rule: when VIX z < -0.5 → vol-suppressed/complacency → long bias
    # when VIX z > +1.0 → stress signaled → de-risk
    df["pos"] = 0.0
    df.loc[df["vix_z"] < -0.5, "pos"] = +0.5    # gentle long
    df.loc[df["vix_z"] >  1.0, "pos"] = -0.3    # gentle short
    df["pnl"] = df["pos"].shift(1).fillna(0) * df["ret"]
    turn = df["pos"].diff().abs().fillna(0)
    df["pnl"] = df["pnl"] - turn * 2/10000
    df = df.loc["2010-01-01":]
    s = stats(df["pnl"])
    print(f"  Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")
    df["pnl"].to_frame("pnl").to_parquet(f"{DATA_OUT}/_pnl_alt_overlay.parquet")
    return df["pnl"], s


if __name__ == "__main__":
    r1, s1 = mean_reversion_regime()
    r2, s2 = statarb_shrinkage()
    r3, s3 = multi_trend()
    r4, s4 = alt_data_overlay()
    print("\n=== Summary ===")
    for name, s in [("MeanRev-regime", s1), ("StatArb-LW", s2),
                    ("Trend-MTF", s3), ("AltData-overlay", s4)]:
        print(f"  {name:20s}: Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")
