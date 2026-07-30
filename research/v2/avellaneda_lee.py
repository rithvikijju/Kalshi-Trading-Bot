"""Avellaneda-Lee (2010) statistical arbitrage via PCA + OU on residuals.

Universe: ~40 large-cap names spanning the S&P sectors, daily 2005-2026.

Daily pipeline (rolling, no look-ahead):
1. Compute trailing 252d log returns matrix R [T_w x N].
2. PCA → keep top k=5 components → factor returns F [T_w x k].
3. OLS: r_i = alpha_i + beta_i'F + eps_i. Store epsilon series.
4. For each stock, accumulate residual X_i(t) = sum_{s<=t} eps_i(s) over the
   same window. Fit OU(theta, mu, sigma) to X_i. Compute "s-score" =
   (X_i(t) - mu) / sigma_eq where sigma_eq = sigma / sqrt(2*theta).
5. Cross-sectional positions: long names with s < -1.25, short s > +1.25.
   Close at |s| < 0.5. Sector-neutral by construction (PCs absorb sector beta).
   Vol target each leg to 10bp daily.

Walk-forward: re-fit PCA + OU every day on trailing 252d. Trade on next day.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from numpy.linalg import lstsq
from sklearn.decomposition import PCA
from _data import load_many

# 40 large, S&P-eligible names with deep history. Sectors balanced for PCA.
UNIVERSE = [
    # Tech
    "AAPL","MSFT","NVDA","ORCL","CSCO","INTC","ADBE","CRM",
    # Financials
    "JPM","BAC","GS","WFC","C","MS","AXP","BLK",
    # Healthcare
    "JNJ","PFE","UNH","ABT","MRK","LLY","TMO","BMY",
    # Industrials / energy
    "BA","CAT","GE","XOM","CVX","HON","UNP",
    # Consumer
    "WMT","KO","PEP","PG","HD","MCD","NKE","COST","TGT","DIS",
]

WINDOW = 252      # trailing days for PCA + OU fit
N_PCS  = 5
ENTRY = 1.25
EXIT  = 0.5
COST_BP_R_T = 8.0  # 8bp r/t per leg (large-cap equity)


def fit_ou(x: np.ndarray):
    """Fit OU to series x. Return (theta, mu, sigma_eq) or None if bad."""
    if len(x) < 30:
        return None
    dx = np.diff(x)
    xl = x[:-1]
    # OLS dx = a + b*xl + e
    X = np.column_stack([np.ones_like(xl), xl])
    coefs, *_ = lstsq(X, dx, rcond=None)
    a, b = coefs[0], coefs[1]
    if b >= 0:
        return None
    theta = -b
    mu = a / theta
    resid = dx - (a + b * xl)
    sigma = np.std(resid)
    sigma_eq = sigma / np.sqrt(2 * theta)
    if sigma_eq <= 0 or theta < 1.0/252:  # half-life > 1y is no good
        return None
    return theta, mu, sigma_eq


def run_backtest(prices: pd.DataFrame, start_oos: str = "2018-01-01",
                 cost_bp: float = COST_BP_R_T) -> dict:
    rets = np.log(prices / prices.shift(1)).dropna(how="all")
    rets = rets.dropna(axis=1, thresh=int(0.9 * len(rets)))  # drop sparse names
    names = rets.columns.tolist()

    pnl_daily = []
    pos_history = []
    dates = []

    pos_prev = pd.Series(0.0, index=names)

    rets_filled = rets.fillna(0)
    cum = rets_filled.cumsum()  # for residual accumulation

    iter_dates = rets.index[rets.index >= start_oos]

    for t_idx, dt in enumerate(iter_dates):
        i = rets.index.get_loc(dt)
        if i < WINDOW:
            continue
        # trailing window ends at i-1 (no look-ahead — today's pos uses up-to-yesterday)
        win = rets_filled.iloc[i - WINDOW: i]
        if win.shape[0] < WINDOW: continue
        # normalize each name's returns
        mu_r = win.mean()
        sd_r = win.std().replace(0, np.nan)
        win_z = ((win - mu_r) / sd_r).dropna(axis=1)
        if win_z.shape[1] < 10: continue

        # PCA on the normalized return matrix
        pca = PCA(n_components=min(N_PCS, win_z.shape[1] - 1))
        F = pca.fit_transform(win_z.values)   # [T_w x k]

        positions_today = pd.Series(0.0, index=names)
        for nm in win_z.columns:
            r_i = win[nm].values  # raw (not z) returns of this name, length T_w
            # OLS r_i on factors F
            X = np.column_stack([np.ones(len(F)), F])
            beta, *_ = lstsq(X, r_i, rcond=None)
            eps = r_i - X @ beta
            X_acc = np.cumsum(eps)
            ou = fit_ou(X_acc)
            if ou is None: continue
            theta, mu, sigma_eq = ou
            s = (X_acc[-1] - mu) / sigma_eq
            # mean-reversion: long when s very negative, short when very positive
            if s < -ENTRY:
                positions_today[nm] = +1
            elif s > +ENTRY:
                positions_today[nm] = -1
            else:
                # hold previous position if |s| < EXIT we exit; in between hold prev
                prev = pos_prev[nm]
                if prev != 0 and abs(s) < EXIT:
                    positions_today[nm] = 0
                else:
                    positions_today[nm] = prev

        # dollar-neutral, equal-weighted within each side
        n_long = (positions_today > 0).sum()
        n_short = (positions_today < 0).sum()
        weights = pd.Series(0.0, index=names)
        if n_long > 0:
            weights[positions_today > 0] = +0.5 / n_long
        if n_short > 0:
            weights[positions_today < 0] = -0.5 / n_short

        # next-bar PnL (use today's returns weighted by yesterday's positions)
        if t_idx > 0 and pos_history:
            w_prev = pos_history[-1]
            r_today = rets.iloc[i].fillna(0)
            day_pnl = (w_prev * r_today).sum()
            # turnover cost
            dw = (weights - w_prev).abs().sum()
            cost = dw * (cost_bp / 10000.0)
            day_pnl -= cost
            pnl_daily.append(day_pnl)
            dates.append(dt)

        pos_history.append(weights)
        pos_prev = positions_today

    pnl = pd.Series(pnl_daily, index=dates)
    if pnl.std() > 0:
        sharpe = pnl.mean() / pnl.std() * np.sqrt(252)
    else:
        sharpe = 0
    eq = (1 + pnl).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    return {
        "pnl": pnl, "sharpe": float(sharpe),
        "ann_ret": float(pnl.mean() * 252),
        "ann_vol": float(pnl.std() * np.sqrt(252)),
        "mdd": float(mdd),
        "n_days": len(pnl),
        "avg_turnover": float(pd.Series([
            (pos_history[k] - pos_history[k-1]).abs().sum()
            for k in range(1, len(pos_history))
        ]).mean()),
    }


def main():
    print(f"Loading {len(UNIVERSE)} names...")
    px = load_many(UNIVERSE, start="2005-01-01", end="2026-06-01")
    print(f"  got {px.shape[1]}/{len(UNIVERSE)} names, {px.shape[0]} days")
    print(f"  earliest full: {px.dropna().index[0].date() if not px.dropna().empty else 'sparse'}")

    print("\nRunning Avellaneda-Lee, OOS 2018-01-01 →")
    res = run_backtest(px)
    print(f"  Sharpe: {res['sharpe']:+.2f}")
    print(f"  AnnRet: {res['ann_ret']*100:+.1f}%")
    print(f"  AnnVol: {res['ann_vol']*100:.1f}%")
    print(f"  MDD:    {res['mdd']*100:+.1f}%")
    print(f"  N days: {res['n_days']}")
    print(f"  Avg daily turnover (sum of |dw|): {res['avg_turnover']:.3f}")

    # split into 2-year sub-periods to detect regime sensitivity
    pnl = res["pnl"]
    print("\nWalk-forward 2-year buckets:")
    for ystart in [2018, 2020, 2022, 2024]:
        sub = pnl[(pnl.index >= f"{ystart}-01-01") & (pnl.index < f"{ystart+2}-01-01")]
        if len(sub) > 10:
            s = sub.mean() / sub.std() * np.sqrt(252) if sub.std() > 0 else 0
            print(f"  {ystart}-{ystart+1}: Sharpe={s:+.2f}, AnnRet={sub.mean()*252*100:+.1f}%")

    if len(pnl) > 100:
        pnl.to_frame("pnl").to_parquet(
            "/Users/rithvikijju/edge-bot/research/v2/data/_pnl_avellaneda_lee.parquet"
        )
        print("\nSaved PnL → research/v2/data/_pnl_avellaneda_lee.parquet")


if __name__ == "__main__":
    main()
