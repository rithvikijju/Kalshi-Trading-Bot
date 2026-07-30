"""HRP allocator at MONTHLY frequency — fixes the daily-broadcast inflation
in hrp_allocator.py. This is the honest combined result.
"""
import numpy as np
import pandas as pd
from hrp_allocator import get_hrp_weights


def monthly_compound(daily_pnl: pd.Series) -> pd.Series:
    """Aggregate daily simple returns to monthly compound returns."""
    return (1 + daily_pnl).resample("M").prod() - 1


def stats_monthly(p):
    if p.std() == 0: return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0)
    s = p.mean()/p.std()*np.sqrt(12)
    eq = (1+p).cumprod()
    return dict(sharpe=float(s), ann_ret=float(p.mean()*12),
                ann_vol=float(p.std()*np.sqrt(12)),
                mdd=float((eq/eq.cummax()-1).min()))


def main():
    # Load streams
    vrp_m = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_vrp.parquet")["pnl_pct"]
    vrp_m.index = pd.to_datetime(vrp_m.index)
    vrp_m = vrp_m.resample("M").last()  # already monthly

    try:
        vxx_d = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_vrp_vxx.parquet")["pnl"]
        vxx_m = monthly_compound(vxx_d)
    except Exception:
        vxx_m = None

    tsmom_d = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_tsmom.parquet")["pnl"]
    tsmom_m = monthly_compound(tsmom_d)

    cp_d = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_coint_pairs.parquet")["pnl"]
    cp_m = monthly_compound(cp_d)
    al_d = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_avellaneda_lee.parquet")["pnl"]
    al_m = monthly_compound(al_d)

    # Build monthly panel
    streams = {"VRP_swap": vrp_m}
    if vxx_m is not None:
        streams["VRP_VXX"] = vxx_m
    streams["TSMOM"] = tsmom_m
    df = pd.DataFrame(streams).dropna(how="all").fillna(0)
    df = df[df.index >= "2018-01-01"]

    print(f"Strategy panel: {df.shape[1]} streams × {df.shape[0]} months "
          f"({df.index[0].date()} → {df.index[-1].date()})\n")

    print("Individual stats (post-2018, monthly):")
    for c in df.columns:
        s = stats_monthly(df[c])
        print(f"  {c:15s}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")
    print("  --- excluded from HRP (negative Sharpe) ---")
    s = stats_monthly(cp_m); print(f"  CoinPairs:      Sharpe={s['sharpe']:+.2f}  (DEAD)")
    s = stats_monthly(al_m); print(f"  A-L residual:   Sharpe={s['sharpe']:+.2f}  (DEAD)")

    # correlations
    print("\nMonthly correlations (post-2018):")
    print(df.corr().round(3).to_string())

    # HRP
    cov = df.cov().values * 12
    w_hrp = get_hrp_weights(cov)
    w_iv = (1.0/df.std()) / (1.0/df.std()).sum()
    w_eq = np.ones(len(df.columns)) / len(df.columns)

    print(f"\nWeights (gross=100%, no leverage applied yet):")
    for c, h, i, e in zip(df.columns, w_hrp, w_iv, w_eq):
        print(f"  {c:15s}: HRP={h:.3f}  InvVol={i:.3f}  Equal={e:.3f}")

    print("\nCombined portfolios:")
    for name, w in [("Equal", w_eq), ("InvVol", w_iv.values), ("HRP", w_hrp)]:
        p = (df * w).sum(axis=1)
        s = stats_monthly(p)
        print(f"  {name:7s}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")

    # leverage to 10% vol
    p_hrp = (df * w_hrp).sum(axis=1)
    ann_vol = p_hrp.std() * np.sqrt(12)
    target_lev = 0.10 / ann_vol if ann_vol > 0 else 1
    if target_lev > 1:
        p_lev = p_hrp * target_lev
        s = stats_monthly(p_lev)
        print(f"\n  HRP vol={ann_vol*100:.1f}%; lever {target_lev:.2f}x to 10% vol target:")
        print(f"     Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")

    # deflated Sharpe estimate for HRP (de Prado 2014, simplified)
    # E[max{SR}] = SR + sqrt(Var(SR)) * Z(1 - 1/N)
    # we tested ~6 candidate strategies → N=6 effective trials
    print("\nDeflated-Sharpe sanity check (López de Prado 2014):")
    N = 6  # number of strategies tested
    # std of Sharpe estimate from sample size T
    T = len(p_hrp)
    sr = p_hrp.mean()/p_hrp.std()*np.sqrt(12)
    se = np.sqrt((1 - 0 + 0.5*sr**2/12) / T)  # rough
    # Bailey & López de Prado deflation: E[max SR] ≈ se * (1 - gamma)*Phi^-1(1 - 1/N) + Phi^-1(1 - 1/(N*e))*gamma
    # simplified
    from scipy.stats import norm
    em = se * ((1 - 0.5772) * norm.ppf(1 - 1/N) + 0.5772 * norm.ppf(1 - 1/(N * np.e)))
    deflated = (sr - em) / np.sqrt((1 - 0.5*sr**2/12 + 0.5*sr**2/12) / T) / np.sqrt(T)
    print(f"  Raw HRP Sharpe (monthly→annual): {sr:.2f}")
    print(f"  Expected max-Sharpe from {N} trials: {em*np.sqrt(12):.2f}")
    print(f"  Deflated Sharpe (heuristic): {(sr - em*np.sqrt(12)):.2f}")


if __name__ == "__main__":
    main()
