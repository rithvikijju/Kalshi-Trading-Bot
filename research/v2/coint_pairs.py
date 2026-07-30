"""Cointegration + OU-band stat-arb across ETF pairs.

Pipeline:
1. Pull daily adjusted closes 2005-2026 for a curated pair universe.
2. In-sample (2005-2017): Engle-Granger ADF on log-price residuals. Keep p<0.05.
3. For survivors: fit OU process to spread, derive optimal entry/exit z-bands.
4. Out-of-sample (2018-2026): trade z-score reversion, vol-targeted sizing,
   net of 10bp round-trip cost (5bp commission/spread + 5bp impact at $50M scale).
5. Walk-forward report: Sharpe, MDD, hit-rate, capacity.
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint, adfuller
import statsmodels.api as sm
from _data import load_many

warnings.filterwarnings("ignore")

PAIRS = [
    # Sector / sub-sector
    ("XLE", "XOP"),    # energy major vs E&P
    ("XLF", "KRE"),    # financials vs regional banks
    ("XLK", "SMH"),    # tech vs semis
    ("XLY", "XRT"),    # consumer disc vs retail
    ("XLV", "IBB"),    # healthcare vs biotech
    ("XLI", "IYT"),    # industrials vs transport
    ("XLU", "VPU"),    # utilities sanity check (same exposure)
    # Country
    ("EWA", "EWC"),    # Australia vs Canada (resource-linked)
    ("EWG", "EWQ"),    # Germany vs France
    ("EWJ", "EWT"),    # Japan vs Taiwan
    ("EWZ", "EWW"),    # Brazil vs Mexico
    # Commodity
    ("GLD", "SLV"),    # gold vs silver
    ("GDX", "GLD"),    # miners vs metal
    ("USO", "XLE"),    # oil vs energy equities
    # Rates / credit
    ("TLT", "IEF"),    # 20y vs 7-10y
    ("HYG", "LQD"),    # HY vs IG credit
    # Index near-duplicates (negative control: should cointegrate trivially)
    ("SPY", "IVV"),
    ("QQQ", "SPY"),
    ("IWM", "SPY"),
]

ALL_TICKERS = sorted(set([t for pair in PAIRS for t in pair]))


def engle_granger(y: pd.Series, x: pd.Series) -> dict:
    """Run Engle-Granger 2-step cointegration test on log prices."""
    ly, lx = np.log(y), np.log(x)
    X = sm.add_constant(lx)
    res = sm.OLS(ly, X).fit()
    alpha, beta = res.params.iloc[0], res.params.iloc[1]
    resid = ly - (alpha + beta * lx)
    adf = adfuller(resid, autolag="AIC")
    return {
        "alpha": float(alpha),
        "beta": float(beta),
        "adf_stat": float(adf[0]),
        "adf_pvalue": float(adf[1]),
        "spread_std": float(resid.std()),
    }


def fit_ou(spread: pd.Series) -> dict:
    """Fit OU dx_t = theta(mu - x_t) dt + sigma dW via OLS on dx ~ x_lag."""
    x = spread.values
    dx = np.diff(x)
    xl = x[:-1]
    X = sm.add_constant(xl)
    res = sm.OLS(dx, X).fit()
    a, b = res.params[0], res.params[1]
    if b >= 0:
        return {"theta": 0.0, "mu": float(np.mean(x)), "sigma": float(np.std(dx)),
                "half_life": np.inf, "ok": False}
    theta = -b
    mu = a / theta
    sigma = float(np.std(res.resid))
    half_life = float(np.log(2) / theta)
    return {"theta": float(theta), "mu": float(mu), "sigma": float(sigma),
            "half_life": half_life, "ok": True}


def trade_ou_pair(y_full: pd.Series, x_full: pd.Series, train_end: str,
                  entry_z: float = 2.0, exit_z: float = 0.5,
                  stop_z: float = 4.0, cost_bp_rt: float = 10.0) -> dict:
    """OOS backtest: train hedge ratio + spread stats on data up to train_end,
    then trade z-band reversion on the OOS portion. Returns daily PnL series.
    """
    ly = np.log(y_full)
    lx = np.log(x_full)
    train_mask = ly.index <= train_end

    # train: OLS hedge ratio
    Xtr = sm.add_constant(lx[train_mask])
    res = sm.OLS(ly[train_mask], Xtr).fit()
    alpha, beta = res.params.iloc[0], res.params.iloc[1]
    spread_tr = ly[train_mask] - (alpha + beta * lx[train_mask])
    mu_tr, sd_tr = float(spread_tr.mean()), float(spread_tr.std())

    # OOS
    oos = ~train_mask
    spread = ly - (alpha + beta * lx)
    z = (spread - mu_tr) / sd_tr
    z_oos = z[oos]
    y_oos = y_full[oos]
    x_oos = x_full[oos]

    # generate positions: enter when |z|>entry_z, exit at |z|<exit_z, stop at |z|>stop_z
    pos = np.zeros(len(z_oos), dtype=float)
    state = 0  # +1 long spread, -1 short spread, 0 flat
    for i, zi in enumerate(z_oos.values):
        if state == 0:
            if zi <= -entry_z:
                state = +1
            elif zi >= +entry_z:
                state = -1
        else:
            if abs(zi) >= stop_z:
                state = 0  # stop out (cointegration breaking?)
            elif (state == +1 and zi >= -exit_z) or (state == -1 and zi <= +exit_z):
                state = 0
        pos[i] = state

    pos = pd.Series(pos, index=z_oos.index)
    # PnL: long spread = long Y, short beta*X
    ry = np.log(y_oos / y_oos.shift(1))
    rx = np.log(x_oos / x_oos.shift(1))
    spread_ret = ry - beta * rx
    # position applied next bar (no look-ahead)
    pnl = pos.shift(1).fillna(0) * spread_ret
    # turnover-based costs (bp per round-trip leg, count both legs)
    turn = pos.diff().abs().fillna(0)
    # one unit of turn = one full open or close of a 1-unit spread = 1 leg trade in each
    # leg → 2 legs of (1+|beta|) gross notional. Approximate cost as bp_rt * (1+|beta|)
    cost = turn * (cost_bp_rt / 10000.0) * (1 + abs(beta))
    net = (pnl - cost).fillna(0)
    return {
        "alpha": float(alpha), "beta": float(beta),
        "mu": mu_tr, "sd": sd_tr,
        "pnl": net,
        "positions": pos,
        "n_trades": int((pos.diff().abs() > 0).sum()),
    }


def annualized_stats(pnl: pd.Series) -> dict:
    if len(pnl) == 0 or pnl.std() == 0:
        return {"sharpe": 0, "ann_ret": 0, "ann_vol": 0, "mdd": 0}
    sharpe = pnl.mean() / pnl.std() * np.sqrt(252)
    ann_ret = pnl.mean() * 252
    ann_vol = pnl.std() * np.sqrt(252)
    eq = (1 + pnl.fillna(0)).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    return {"sharpe": float(sharpe), "ann_ret": float(ann_ret),
            "ann_vol": float(ann_vol), "mdd": float(mdd)}


def main():
    print("Loading data...")
    df = load_many(ALL_TICKERS, start="2005-01-01", end="2026-06-01")
    print(f"  loaded {df.shape[1]} tickers, {df.shape[0]} days, range {df.index[0].date()} → {df.index[-1].date()}")

    train_end = "2017-12-31"
    print(f"\nIn-sample cointegration test (≤ {train_end}):\n")
    results = []
    for y_tk, x_tk in PAIRS:
        if y_tk not in df.columns or x_tk not in df.columns:
            print(f"  skip {y_tk}/{x_tk}: missing data")
            continue
        sub = df[[y_tk, x_tk]].dropna()
        train = sub[sub.index <= train_end]
        if len(train) < 252 * 3:
            print(f"  skip {y_tk}/{x_tk}: <3y train history ({len(train)})")
            continue
        eg = engle_granger(train[y_tk], train[x_tk])
        ou = fit_ou(np.log(train[y_tk]) - eg["beta"] * np.log(train[x_tk]) - eg["alpha"])
        print(f"  {y_tk}/{x_tk}: beta={eg['beta']:+.3f}, ADF p={eg['adf_pvalue']:.4f}, "
              f"OU half-life={ou['half_life']:.0f}d")
        results.append({
            "pair": f"{y_tk}/{x_tk}",
            "beta": eg["beta"], "p": eg["adf_pvalue"],
            "half_life": ou["half_life"],
        })

    surv = [r for r in results if r["p"] < 0.05 and 5 < r["half_life"] < 252]
    print(f"\n{len(surv)}/{len(results)} pairs pass: p<0.05 and 5d < HL < 252d")
    for r in surv:
        print(f"  ✓ {r['pair']}  HL={r['half_life']:.0f}d  p={r['p']:.4f}")

    if not surv:
        print("\nNo cointegrated pairs survive — moving on.")
        return

    print(f"\nOOS backtest 2018-01-01 → 2026-06-01, entry z=2, exit z=0.5, stop z=4")
    print(f"Cost: 10bp round-trip × (1+|beta|) per leg flip\n")
    survivor_pnls = {}
    summary = []
    for r in surv:
        y_tk, x_tk = r["pair"].split("/")
        sub = df[[y_tk, x_tk]].dropna()
        bt = trade_ou_pair(sub[y_tk], sub[x_tk], train_end=train_end)
        stats = annualized_stats(bt["pnl"])
        summary.append({
            "pair": r["pair"], "beta": bt["beta"],
            "n_trades": bt["n_trades"],
            **stats,
        })
        survivor_pnls[r["pair"]] = bt["pnl"]

    sdf = pd.DataFrame(summary).sort_values("sharpe", ascending=False)
    print(sdf.to_string(index=False))

    # equal-weighted portfolio of all surviving pairs
    if survivor_pnls:
        port = pd.concat(survivor_pnls.values(), axis=1).fillna(0).mean(axis=1)
        ps = annualized_stats(port)
        print(f"\nEqual-weighted portfolio (n={len(survivor_pnls)} pairs): "
              f"Sharpe={ps['sharpe']:.2f}  AnnRet={ps['ann_ret']*100:.1f}%  "
              f"AnnVol={ps['ann_vol']*100:.1f}%  MDD={ps['mdd']*100:.1f}%")

        # save portfolio pnl for downstream HRP
        port.to_frame("pnl").to_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_coint_pairs.parquet")
        print("\nSaved portfolio PnL → research/v2/data/_pnl_coint_pairs.parquet")


if __name__ == "__main__":
    main()
