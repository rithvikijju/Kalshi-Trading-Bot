"""Robustness checks on K6 v2 (side-aware vol-regime filter).

- Hour-of-day breakdown
- Spread filter (spread_cents <= N)
- Day-of-week distribution
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


def run_bucket(tband, dlo, dhi, side):
    tlo, thi = TIME_BANDS[tband]
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")
    df = db.execute(f"""
        WITH q AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.no_ask_exe AS no_ask,
                   q.spread_cents,
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
        first_sig AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn FROM in_band),
        first_only AS (SELECT * FROM first_sig WHERE rn = 1),
        with_close AS (SELECT f.*, b.close AS btc_close
            FROM first_only f LEFT JOIN btc_1m b ON b.bucket_start = f.cmin)
        SELECT * FROM with_close WHERE btc_close IS NOT NULL
    """).df()
    db.close()
    if len(df) == 0: return None
    yes_wins = (df['btc_close'] > df['strike']).astype(float)
    df['settles'] = yes_wins if side == 'yes' else (1 - yes_wins)
    df['paid'] = df['yes_ask'] if side == 'yes' else df['no_ask']
    df['fee'] = df['paid'].apply(kalshi_fee)
    df['pnl'] = df['settles'] - df['paid'] - df['fee']
    df['side'] = side
    return df


def apply_side_aware(big):
    t1, t2 = big['rv15'].quantile([0.33, 0.67])
    yes_mask = (big['side']=='yes') & (big['rv15'] >= t1)
    no_mask  = (big['side']=='no')  & (big['rv15'] <  t2)
    return big[yes_mask | no_mask].copy()


def summarize(df, label):
    if df is None or len(df) == 0: print(f"  [{label}] NO DATA"); return
    n = len(df); wins = (df['pnl']>0).sum()
    span = (df['available_at'].max()-df['available_at'].min()).total_seconds()/86400
    daily = df.set_index('available_at').resample('1D')['pnl'].sum()
    sr = daily.mean()/daily.std()*np.sqrt(365) if daily.std()>0 else 0
    print(f"  [{label}]  n={n:>5}  win={wins/n*100:5.1f}%  "
          f"avg=${df['pnl'].mean():+.4f}  total=${df['pnl'].sum():+.2f}  "
          f"daily=${df['pnl'].sum()/max(span,1):+.2f}  SR={sr:5.2f}")


if __name__ == '__main__':
    print("=" * 100)
    print("K6 v2 robustness")
    print("=" * 100)
    all_dfs = []
    for tband, dlo, dhi, side, label in BUCKETS:
        df = run_bucket(tband, dlo, dhi, side)
        if df is not None:
            df['bucket'] = label
            all_dfs.append(df)
    big = pd.concat(all_dfs, ignore_index=True)
    filt = apply_side_aware(big)

    summarize(filt, 'k6 v2 baseline')

    # Spread filter sweep
    print("\n--- Spread filter (cents) ---")
    for sp in [1, 2, 3, 5, 10, 999]:
        sub = filt[filt['spread_cents'] <= sp]
        summarize(sub, f'spread_cents<={sp}')

    # Hour of day (UTC)
    filt['hour_utc'] = filt['available_at'].dt.tz_convert('UTC').dt.hour
    print("\n--- Hour-of-day (UTC) breakdown ---")
    h = filt.groupby('hour_utc')['pnl'].agg(['count','mean','sum'])
    h['win_pct'] = filt.groupby('hour_utc')['pnl'].apply(lambda s: (s>0).mean()*100)
    print(h.to_string())

    # Day of week
    filt['dow'] = filt['available_at'].dt.dayofweek
    print("\n--- Day-of-week (0=Mon) ---")
    d = filt.groupby('dow')['pnl'].agg(['count','mean','sum'])
    d['win_pct'] = filt.groupby('dow')['pnl'].apply(lambda s: (s>0).mean()*100)
    print(d.to_string())
