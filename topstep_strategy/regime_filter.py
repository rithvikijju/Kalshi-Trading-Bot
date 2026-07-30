"""
Kalshi crypto vol-REGIME filter — the ONE thing Kalshi data is genuinely good for here.

We proved Kalshi can't predict BTC direction (see STRATEGY.md), but |move| clusters
strongly (corr ~0.30) and Kalshi's OI/volume/funding are a clean, free, real-time read on
crypto activity. This classifies the current regime so a scalp engine knows WHEN moves are
big enough to clear the MBT cost floor (~$17 BTC move) and when to size down / stand aside.
It does NOT give direction — pair it with a real directional trigger (see STRATEGY.md).

Regimes: DEAD (don't trade — moves won't clear cost), NORMAL, HOT (vol-burst; only here do
breakout scalps have a chance), TOXIC (vol so high the trailing-DD risk outweighs edge).
"""
import numpy as np
import pandas as pd

COST_BP = 2.7      # MBT round-turn, bp of notional


def realized_vol_bp(spot_1m, win=30):
    """rolling realized vol of 1-min log returns, in bp."""
    r = np.log(spot_1m).diff()
    return r.rolling(win).std() * 1e4


def classify(rv_bp, oi_z=0.0):
    """rv_bp: recent realized vol (bp/min). Returns regime label + suggested size mult."""
    if rv_bp < COST_BP:                       # a typical 1-min move can't pay the cost
        return 'DEAD', 0.0
    if rv_bp < 2 * COST_BP:
        return 'NORMAL', 0.5
    if rv_bp < 6 * COST_BP:
        return 'HOT', 1.0                     # vol-burst: breakout scalps have a shot
    return 'TOXIC', 0.25                      # too wild: cut size, protect trailing DD


def label_series(spot_1m, win=30):
    rv = realized_vol_bp(spot_1m, win)
    regimes, mults = [], []
    for v in rv:
        if np.isnan(v):
            regimes.append('NA'); mults.append(0.0); continue
        r, m = classify(v)
        regimes.append(r); mults.append(m)
    return pd.DataFrame({'rv_bp': rv.values, 'regime': regimes, 'size_mult': mults})


if __name__ == '__main__':
    from pathlib import Path
    d = pd.read_parquet(Path(__file__).resolve().parent.parent /
                        'kalshi_perp_edge' / 'data' / 'spot_BTC.parquet')
    lab = label_series(d.spot)
    vc = lab.regime.value_counts(normalize=True) * 100
    print('regime distribution over 13d of BTC (1-min):')
    for k in ('DEAD', 'NORMAL', 'HOT', 'TOXIC'):
        print(f'  {k:<7}{vc.get(k, 0):>5.1f}%')
    print(f'\n=> only ~{vc.get("HOT",0)+vc.get("TOXIC",0):.0f}% of the time are crypto moves '
          f'big enough that an MBT scalp could clear costs. Trade rarely.')
