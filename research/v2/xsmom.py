"""Cross-sectional momentum across country/sector ETFs (Asness 1997 style).

At each month-end, rank assets by trailing-12m return (skip last 1m for the
classic 12-1 momentum). Long top-tercile, short bottom-tercile, equal-weighted.
Vol-target combined sleeve to 10% ann.

Universe: same as TSMOM (diversified asset ETFs). XSMOM and TSMOM are
~uncorrelated despite using the same signal — different aggregation
(cross-section vs time-series).
"""
import numpy as np
import pandas as pd
from _data import load_many

# more variety in single asset class for cross-sectional ranking
UNIVERSE = [
    # 11 country/regional ETFs - the classic XSMOM universe
    "SPY", "EFA", "EEM", "EWJ", "EWG", "EWU", "EWA", "EWC", "EWZ", "EWT", "EWY",
    # bonds
    "TLT", "IEF", "BWX", "EMB",
    # commodities
    "GLD", "SLV", "USO", "DBA", "DBC",
    # real estate
    "VNQ", "RWO",
]


def stats(p):
    if p.std() == 0: return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0)
    s = p.mean()/p.std()*np.sqrt(252)
    eq = (1+p).cumprod()
    return dict(sharpe=float(s), ann_ret=float(p.mean()*252),
                ann_vol=float(p.std()*np.sqrt(252)),
                mdd=float((eq/eq.cummax()-1).min()))


def main():
    print(f"Loading {len(UNIVERSE)} assets...")
    px = load_many(UNIVERSE, start="2005-01-01", end="2026-06-01")
    px = px.dropna(axis=1, thresh=int(0.5 * len(px)))
    print(f"  got {px.shape[1]} assets")

    rets = np.log(px / px.shift(1))
    px_me = px.resample("M").last()

    # 12-1 momentum: trailing 12m skip last 1m
    mom = np.log(px_me.shift(1) / px_me.shift(12))
    # at each ME, rank cross-sectionally
    ranks = mom.rank(axis=1, pct=True)

    # positions: long top 1/3 (rank > 0.66), short bottom 1/3 (rank < 0.33)
    long = (ranks > 0.66).astype(float)
    short = (ranks < 0.33).astype(float)
    n_long = long.sum(axis=1).replace(0, np.nan)
    n_short = short.sum(axis=1).replace(0, np.nan)
    pos_me = long.div(n_long, axis=0) - short.div(n_short, axis=0)
    pos_me = pos_me.fillna(0)

    # broadcast to daily, shift 1 to avoid look-ahead
    pos_d = pos_me.reindex(rets.index, method="ffill").shift(1).fillna(0)

    daily_pnl = (pos_d * rets).sum(axis=1).fillna(0)
    turnover = pos_d.diff().abs().sum(axis=1).fillna(0)
    cost = turnover * 5/10000
    pnl = (daily_pnl - cost)
    pnl_oos = pnl[pnl.index >= "2010-01-01"]

    print(f"\nFull post-2010 OOS:")
    print(f"  {stats(pnl_oos)}")

    print("\nBy 2-year bucket:")
    for y in range(2010, 2026, 2):
        sub = pnl_oos[(pnl_oos.index >= f"{y}-01-01") & (pnl_oos.index < f"{y+2}-01-01")]
        if len(sub) > 10:
            s = stats(sub)
            print(f"  {y}-{y+1}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%")

    pnl_oos.to_frame("pnl").to_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_xsmom.parquet")
    print("\nSaved → research/v2/data/_pnl_xsmom.parquet")


if __name__ == "__main__":
    main()
