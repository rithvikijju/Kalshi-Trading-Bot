"""Quick exploratory diagnostic.

Once data is fetched, run this to see:
  - How many bars per coin
  - Top cointegrated pairs (where spread mean-reverts cleanly)
  - Histogram of |z| values for top pairs (sanity check that |z|>2 happens enough)
  - Average spread move per typical hold period (sanity check vs 28bp cost)
"""
from __future__ import annotations
import math
from pathlib import Path
from itertools import combinations
import duckdb, pandas as pd, numpy as np

DB_PATH = Path('alt_statarb/data/alts_1h.duckdb')


def main():
    db = duckdb.connect(str(DB_PATH), read_only=True)
    tables = [r[0] for r in db.execute('SHOW TABLES').fetchall() if r[0].startswith('c_')]
    print(f"Tables found: {len(tables)}")

    # Load all
    data = {}
    for t in tables:
        coin = t[2:]
        df = db.execute(f"SELECT ts_ms, ts, close FROM {t} ORDER BY ts_ms").df()
        data[coin] = df
        print(f"  {coin:5}: {len(df):,} bars  ({df['ts'].iloc[0]} → {df['ts'].iloc[-1]})")
    db.close()

    if not data: return
    # Align
    base = data[list(data.keys())[0]][['ts_ms', 'ts']].copy()
    for coin, df in data.items():
        base = base.merge(df[['ts_ms', 'close']].rename(columns={'close': coin}),
                          on='ts_ms', how='inner')
    base = base.sort_values('ts_ms').reset_index(drop=True)
    coins = [c for c in base.columns if c not in ('ts_ms', 'ts')]
    print(f"\nAligned: {len(base):,} bars  shared across {len(coins)} coins")

    # Cointegration analysis
    print(f"\n=== Pair cointegration ranking (β=1 log-spread, AR(1) half-life) ===")
    print(f"{'pair':<14} {'half_life_h':>11} {'spread_sd_%':>11} {'14d move %':>11} {'score':>8}")
    results = []
    for a, b in combinations(coins, 2):
        la = np.log(base[a].values); lb = np.log(base[b].values)
        s = la - lb
        s_lag = pd.Series(s).shift(1)
        d = pd.Series(s) - s_lag
        ok = s_lag.notna() & d.notna()
        if ok.sum() < 200: continue
        slope = np.polyfit(s_lag[ok].values, d[ok].values, 1)[0]
        if slope >= 0: continue
        try: hl = -math.log(2) / math.log(1 + slope)
        except: continue
        spread_std = np.std(s) * 100
        # Average absolute spread move over 1 day = 24 hourly bars
        moves_24h = np.abs(pd.Series(s).diff(24).dropna().values) * 100
        avg_move = moves_24h.mean() if len(moves_24h) else 0
        score = spread_std / math.sqrt(max(hl, 1))
        results.append({'a': a, 'b': b, 'hl': hl, 'spread_sd': spread_std,
                        'avg_24h_move': avg_move, 'score': score})

    df = pd.DataFrame(results).sort_values('score', ascending=False)
    for _, r in df.head(20).iterrows():
        print(f"{r['a']:>5}/{r['b']:>5}  {r['hl']:>10.1f}h  {r['spread_sd']:>10.2f}%  "
              f"{r['avg_24h_move']:>10.2f}%  {r['score']:>8.2f}")

    print(f"\n=== Cost vs typical spread move per trade ===")
    print(f"  Cost per round trip: 28 bps (5bp taker + 2bp slip × 4 fills)")
    print(f"  → need spread move > 28 bp gross to break even")
    print(f"  Top pair 24h move (avg): {df['avg_24h_move'].iloc[0]:.2f}% = "
          f"{df['avg_24h_move'].iloc[0]*100:.0f} bps")
    cost_ratio = 28 / (df['avg_24h_move'].iloc[0] * 100)
    print(f"  Cost / move ratio for top pair: {cost_ratio:.2f}x "
          f"({'TRADEABLE' if cost_ratio < 0.5 else 'MARGINAL' if cost_ratio < 1 else 'NOT TRADEABLE'})")


if __name__ == '__main__':
    main()
