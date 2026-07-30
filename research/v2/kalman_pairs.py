"""Kalman dynamic hedge ratio for ETF pairs (upgrade of static-beta coint_pairs.py).

Model:
    state s_t = [alpha_t, beta_t]', random walk: s_t = s_{t-1} + w_t, w_t ~ N(0, Q)
    observation: y_t = [1, x_t] s_t + v_t,    v_t ~ N(0, R)

Innovation e_t = y_t - [1, x_t] s_{t|t-1}.
Trade z-score of innovation: e_t / sqrt(S_t) where S_t is predicted obs variance.

Test the same 4 in-sample-survivor pairs from coint_pairs.py to see if dynamic
hedge fixes the OOS blowup.
"""
import numpy as np
import pandas as pd
from _data import load_many


def kalman_hedge(y: pd.Series, x: pd.Series, delta: float = 1e-4, R: float = 1e-3):
    """One-pass Kalman filter on log-prices. Returns innovation z-scores."""
    ly, lx = np.log(y).values, np.log(x).values
    n = len(ly)

    # state init: OLS on first 60 days
    n0 = min(60, n // 4)
    Xb = np.column_stack([np.ones(n0), lx[:n0]])
    beta_init, _, _, _ = np.linalg.lstsq(Xb, ly[:n0], rcond=None)
    s = beta_init.copy()                          # [alpha, beta]
    P = np.eye(2) * 0.1                            # state covariance

    # process noise covariance: delta scales how fast beta drifts
    Wt = delta / (1 - delta) * np.eye(2)

    innov = np.full(n, np.nan)
    z = np.full(n, np.nan)
    betas = np.full(n, np.nan)
    alphas = np.full(n, np.nan)

    for t in range(n):
        F = np.array([1.0, lx[t]])                # observation operator
        # predict
        P = P + Wt
        # innovation
        yhat = F @ s
        e = ly[t] - yhat
        S = F @ P @ F + R                          # predicted obs variance
        K = (P @ F) / S                            # Kalman gain
        # update
        s = s + K * e
        P = P - np.outer(K, F) @ P

        innov[t] = e
        z[t] = e / np.sqrt(S) if S > 0 else 0
        alphas[t] = s[0]
        betas[t] = s[1]

    idx = y.index
    return pd.DataFrame({
        "alpha": alphas, "beta": betas, "innov": innov, "z": z
    }, index=idx)


def trade_kalman(y_full: pd.Series, x_full: pd.Series, train_end: str,
                 entry_z: float = 2.0, exit_z: float = 0.5, stop_z: float = 4.0,
                 cost_bp_rt: float = 10.0, delta: float = 1e-4) -> dict:
    """Backtest Kalman-pair on OOS portion of (y, x)."""
    kf = kalman_hedge(y_full, x_full, delta=delta)
    oos = kf.index > train_end
    z = kf["z"][oos]
    beta = kf["beta"][oos]
    y_oos = y_full[oos]
    x_oos = x_full[oos]

    pos = np.zeros(len(z))
    state = 0
    for i, zi in enumerate(z.values):
        if state == 0:
            if zi <= -entry_z: state = +1
            elif zi >= +entry_z: state = -1
        else:
            if abs(zi) >= stop_z:
                state = 0
            elif (state == +1 and zi >= -exit_z) or (state == -1 and zi <= +exit_z):
                state = 0
        pos[i] = state
    pos = pd.Series(pos, index=z.index)

    ry = np.log(y_oos / y_oos.shift(1))
    rx = np.log(x_oos / x_oos.shift(1))
    spread_ret = ry - beta * rx
    pnl = pos.shift(1).fillna(0) * spread_ret
    turn = pos.diff().abs().fillna(0)
    cost = turn * (cost_bp_rt / 10000.0) * (1 + beta.abs())
    net = (pnl - cost).fillna(0)
    return {"pnl": net, "positions": pos,
            "n_trades": int((pos.diff().abs() > 0).sum()),
            "avg_beta": float(beta.mean()),
            "beta_std": float(beta.std())}


def annualized_stats(pnl):
    if len(pnl) == 0 or pnl.std() == 0:
        return {"sharpe": 0, "ann_ret": 0, "ann_vol": 0, "mdd": 0}
    s = pnl.mean() / pnl.std() * np.sqrt(252)
    eq = (1 + pnl.fillna(0)).cumprod()
    return {"sharpe": float(s), "ann_ret": float(pnl.mean() * 252),
            "ann_vol": float(pnl.std() * np.sqrt(252)),
            "mdd": float((eq / eq.cummax() - 1).min())}


def main():
    survivors = [("XLI", "IYT"), ("EWA", "EWC"), ("HYG", "LQD"), ("IWM", "SPY")]
    tickers = sorted(set([t for p in survivors for t in p]))
    df = load_many(tickers, start="2005-01-01", end="2026-06-01")

    print("Kalman dynamic-hedge backtest, OOS 2018+ (same 4 pairs as static test)")
    print("delta=1e-4 (β drifts slowly), entry_z=2, exit_z=0.5, stop_z=4, cost=10bp r/t")
    print("--" * 30)

    out = []
    pnls = {}
    for y_tk, x_tk in survivors:
        sub = df[[y_tk, x_tk]].dropna()
        bt = trade_kalman(sub[y_tk], sub[x_tk], train_end="2017-12-31")
        stats = annualized_stats(bt["pnl"])
        out.append({
            "pair": f"{y_tk}/{x_tk}",
            "avg_beta": bt["avg_beta"], "beta_std": bt["beta_std"],
            "n_trades": bt["n_trades"], **stats
        })
        pnls[f"{y_tk}/{x_tk}"] = bt["pnl"]

    print(pd.DataFrame(out).to_string(index=False))
    port = pd.concat(pnls.values(), axis=1).fillna(0).mean(axis=1)
    ps = annualized_stats(port)
    print(f"\nEqual-weight portfolio: Sharpe={ps['sharpe']:.2f} "
          f"AnnRet={ps['ann_ret']*100:.1f}% Vol={ps['ann_vol']*100:.1f}% MDD={ps['mdd']*100:.1f}%")

    # try varying delta — robustness check
    print("\nDelta sensitivity (portfolio Sharpe):")
    for d in [1e-6, 1e-5, 1e-4, 1e-3, 1e-2]:
        pnls_d = {}
        for y_tk, x_tk in survivors:
            sub = df[[y_tk, x_tk]].dropna()
            bt = trade_kalman(sub[y_tk], sub[x_tk], train_end="2017-12-31", delta=d)
            pnls_d[f"{y_tk}/{x_tk}"] = bt["pnl"]
        port_d = pd.concat(pnls_d.values(), axis=1).fillna(0).mean(axis=1)
        ps_d = annualized_stats(port_d)
        print(f"  delta={d:.0e}: Sharpe={ps_d['sharpe']:+.2f}  AnnRet={ps_d['ann_ret']*100:+.1f}%")


if __name__ == "__main__":
    main()
