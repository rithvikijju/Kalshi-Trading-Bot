"""HRP allocator (López de Prado 2016) across V2 surviving strategy streams.

Combines:
- VRP (synthetic swap, monthly) — Sharpe ~2.5
- VRP (VXX-short, daily) — Sharpe ~0.83
- TSMOM (daily) — Sharpe ~0.5
- (Excluded: coint-pairs Sharpe -2.8, Avellaneda-Lee Sharpe -0.7 → DEAD, not in mix)

Builds:
1. Pairwise correlation matrix.
2. HRP weights via López de Prado's recursive bisection on the clustered dendrogram.
3. Compare to: equal-weight, inverse-vol, vol-targeted.
"""
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform


def get_hrp_weights(cov: np.ndarray) -> np.ndarray:
    """López de Prado HRP: cluster → bisect → inverse-vol weight."""
    corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
    dist = np.sqrt(0.5 * (1 - corr))
    link = linkage(squareform(dist, checks=False), method="single")

    # quasi-diagonal ordering from the linkage
    n = cov.shape[0]
    def get_order(link, n):
        # walk the dendrogram leaf-order
        order = list(link[-1, [0, 1]].astype(int))
        while max(order) >= n:
            new_order = []
            for o in order:
                if o < n:
                    new_order.append(o)
                else:
                    children = link[o - n, [0, 1]].astype(int).tolist()
                    new_order.extend(children)
            order = new_order
        return order
    order = get_order(link, n)

    # recursive bisection
    weights = pd.Series(1.0, index=range(n))
    clusters = [order]
    while clusters:
        clusters = [c[k:m] for c in clusters for k, m in
                    ((0, len(c) // 2), (len(c) // 2, len(c))) if len(c) > 1]
        for i in range(0, len(clusters), 2):
            c1 = clusters[i]; c2 = clusters[i + 1]
            v1 = _cluster_var(cov, c1); v2 = _cluster_var(cov, c2)
            alpha = 1 - v1 / (v1 + v2)
            weights[c1] *= alpha
            weights[c2] *= (1 - alpha)
    return weights.values


def _cluster_var(cov: np.ndarray, idx: list) -> float:
    sub = cov[np.ix_(idx, idx)]
    # inverse-vol weights within cluster
    iv = 1.0 / np.diag(sub)
    iv /= iv.sum()
    return float(iv @ sub @ iv)


def stats_daily(p):
    if p.std() == 0: return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0)
    s = p.mean()/p.std()*np.sqrt(252)
    eq = (1+p).cumprod()
    return dict(sharpe=float(s), ann_ret=float(p.mean()*252),
                ann_vol=float(p.std()*np.sqrt(252)),
                mdd=float((eq/eq.cummax()-1).min()))


def main():
    # Load all available PnL streams
    streams = {}
    # VRP synthetic swap is monthly — convert to daily by spreading evenly within month
    vrp_m = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_vrp.parquet")["pnl_pct"]
    # spread monthly PnL → daily
    vrp_daily = vrp_m.copy()
    vrp_daily.index = pd.to_datetime(vrp_daily.index)
    # build daily date range and assign monthly pnl/n_days_in_month evenly
    full_idx = pd.bdate_range(vrp_daily.index.min() - pd.DateOffset(months=1),
                              vrp_daily.index.max())
    vrp_d = pd.Series(0.0, index=full_idx)
    for d, v in vrp_daily.items():
        # days in the month ending at d
        m_start = d - pd.offsets.MonthBegin()
        days_in = full_idx[(full_idx >= m_start) & (full_idx <= d)]
        if len(days_in) > 0:
            vrp_d.loc[days_in] = v / len(days_in)
    streams["VRP_swap"] = vrp_d

    try:
        vxx = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_vrp_vxx.parquet")["pnl"]
        streams["VRP_VXX"] = vxx
    except Exception:
        pass

    tsmom = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_tsmom.parquet")["pnl"]
    streams["TSMOM"] = tsmom

    # try coint pairs (negative — for completeness)
    try:
        cp = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_coint_pairs.parquet")["pnl"]
        # NOT including in HRP since Sharpe negative — would just get 0 weight or screw mix
        # report stats only
    except Exception:
        cp = None

    al = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_avellaneda_lee.parquet")["pnl"]

    # align all on common dates
    df = pd.DataFrame(streams).dropna(how="all").fillna(0)
    df = df[df.index >= "2018-01-01"]
    print(f"Strategy PnL universe: {df.shape[1]} streams, {df.shape[0]} days, "
          f"{df.index[0].date()} → {df.index[-1].date()}")

    print("\nIndividual stats (post-2018):")
    for c in df.columns:
        p = df[c]
        s = stats_daily(p)
        print(f"  {c:15s}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")
    print(f"  {'TSMOM':15s} (reference) and coint/A-L excluded (negative Sharpe)")
    if cp is not None:
        s = stats_daily(cp); print(f"  CoinPairs (excluded): Sharpe={s['sharpe']:+.2f}")
    s = stats_daily(al); print(f"  A-L (excluded):       Sharpe={s['sharpe']:+.2f}")

    # correlation matrix
    print("\nCorrelation (post-2018):")
    print(df.corr().round(2).to_string())

    # build HRP weights
    cov = df.cov().values * 252  # annualized
    w_hrp = get_hrp_weights(cov)
    w_iv = (1.0/df.std()) / (1.0/df.std()).sum()
    w_eq = np.ones(len(df.columns)) / len(df.columns)

    print(f"\nWeights (gross 100%):")
    for c, h, i, e in zip(df.columns, w_hrp, w_iv, w_eq):
        print(f"  {c:15s}: HRP={h:.3f}  InvVol={i:.3f}  Equal={e:.3f}")

    # combined portfolios
    print("\nCombined portfolios (post-2018):")
    for name, w in [("Equal", w_eq), ("InvVol", w_iv.values), ("HRP", w_hrp)]:
        p = (df * w).sum(axis=1)
        s = stats_daily(p)
        print(f"  {name:7s}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")

    # save HRP combined PnL
    p_hrp = (df * w_hrp).sum(axis=1)
    p_hrp.to_frame("pnl").to_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_hrp_combined.parquet")
    print("\nSaved HRP combined PnL → research/v2/data/_pnl_hrp_combined.parquet")

    # leverage targeting: if vol < 10% target, can lever HRP combined to deliver more return at same Sharpe
    if p_hrp.std() > 0:
        ann_vol = p_hrp.std() * np.sqrt(252)
        target_lev = 0.10 / ann_vol
        print(f"\nVol-target check: HRP ann vol = {ann_vol*100:.1f}%, leverage to 10% = {target_lev:.2f}x")
        if target_lev > 1:
            p_lev = p_hrp * target_lev
            s = stats_daily(p_lev)
            print(f"  Levered to 10% vol: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")


if __name__ == "__main__":
    main()
