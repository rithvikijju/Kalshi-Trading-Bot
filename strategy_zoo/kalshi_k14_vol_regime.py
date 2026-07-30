"""K14: split K6 spot-displacement trades by BTC realized vol regime.

If K6 works because of stale quotes after fast moves, edge should be bigger in
high-vol regimes. Conversely, in calm regimes the spot doesn't move past strikes
much, so even when it does the MM may reprice fast.

Split by rv_15m (15-min realized vol) tercile and run kalshi_k6_validate buckets
within each regime.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


BUCKETS = [
    ('0-5m',   50,   200,  'yes',  'A1'),
    ('0-5m',   200,  500,  'yes',  'A2'),
    ('5-15m',  50,   200,  'yes',  'B1'),
    ('5-15m',  200,  500,  'yes',  'B2'),
    ('0-5m',  -200,  -50,  'no',   'A1n'),
    ('0-5m',  -500, -200,  'no',   'A2n'),
    ('5-15m', -200,  -50,  'no',   'B1n'),
    ('5-15m', -500, -200,  'no',   'B2n'),
]

TIME_BANDS = {'0-5m':(0,5), '5-15m':(5,15)}


def run_with_vol(tband, dlo, dhi, side):
    tlo, thi = TIME_BANDS[tband]
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")
    df = db.execute(f"""
        WITH q AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.no_ask_exe AS no_ask,
                   q.floor_strike AS strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close,
                   DATE_TRUNC('minute', q.available_at) AS qmin,
                   DATE_TRUNC('minute', q.close_time) AS cmin
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.yes_ask_close IS NOT NULL AND q.no_ask_exe IS NOT NULL
              AND q.yes_ask_close > 0 AND q.yes_ask_close < 1
              AND q.no_ask_exe > 0 AND q.no_ask_exe < 1
              AND DATE_DIFF('minute', q.available_at, q.close_time) BETWEEN {tlo} AND {thi}
        ),
        with_spot AS (
            SELECT q.*, b.close AS spot, b.rv_15m AS rv15
            FROM q LEFT JOIN btc_1m b ON b.bucket_start = q.qmin
        ),
        in_band AS (
            SELECT * FROM with_spot
            WHERE spot IS NOT NULL AND rv15 IS NOT NULL
              AND spot - strike BETWEEN {dlo} AND {dhi}
        ),
        first_sig AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM in_band
        ),
        first_only AS (SELECT * FROM first_sig WHERE rn = 1),
        with_close AS (
            SELECT f.*, b.close AS btc_close
            FROM first_only f LEFT JOIN btc_1m b ON b.bucket_start = f.cmin
        )
        SELECT * FROM with_close WHERE btc_close IS NOT NULL
    """).df()
    db.close()
    if len(df) == 0: return None

    yes_wins = (df['btc_close'] > df['strike']).astype(float)
    df['settles'] = yes_wins if side == 'yes' else (1 - yes_wins)
    df['paid'] = df['yes_ask'] if side == 'yes' else df['no_ask']
    df['fee'] = df['paid'].apply(kalshi_fee)
    df['pnl'] = df['settles'] - df['paid'] - df['fee']
    return df


def summarize_by_regime(df, label):
    if df is None or len(df) == 0:
        print(f"  [{label}] NO DATA"); return
    # Tercile boundaries based on this bucket's rv15 distribution
    t1, t2 = df['rv15'].quantile([0.33, 0.67])
    df['regime'] = pd.cut(df['rv15'], bins=[-1, t1, t2, 1e9], labels=['low','mid','high'])
    g = df.groupby('regime', observed=True)['pnl'].agg(['count','mean','sum'])
    g['win_pct'] = df.groupby('regime', observed=True)['pnl'].apply(lambda s: (s>0).mean()*100)
    print(f"  [{label}]  rv15 terciles: t1={t1:.5f}  t2={t2:.5f}")
    print(g.to_string())
    print()


if __name__ == '__main__':
    print("=" * 95)
    print("K14: K6 trades split by 15-min realized vol regime")
    print("=" * 95)
    all_dfs = []
    for tband, dlo, dhi, side, label in BUCKETS:
        df = run_with_vol(tband, dlo, dhi, side)
        if df is not None:
            df['bucket'] = label
            all_dfs.append(df)

    big = pd.concat(all_dfs, ignore_index=True)
    print(f"Total stacked trades: {len(big)}\n")
    summarize_by_regime(big, 'STACKED-ALL')

    # Also: cross-tab bucket × regime
    t1, t2 = big['rv15'].quantile([0.33, 0.67])
    big['regime'] = pd.cut(big['rv15'], bins=[-1, t1, t2, 1e9], labels=['low','mid','high'])
    print("\nBucket × regime — avg PnL per trade:")
    pvt = big.pivot_table(index='bucket', columns='regime', values='pnl', aggfunc='mean', observed=True)
    print((pvt*100).round(2).to_string())
    print("\nBucket × regime — trade count:")
    pvt = big.pivot_table(index='bucket', columns='regime', values='pnl', aggfunc='count', observed=True)
    print(pvt.to_string())
