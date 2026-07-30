"""K6 v2: K14-filtered version. Drop low-vol trades, apply side-specific regime filter.

YES-side (spot above strike): trade only when rv_15m > tercile-1 boundary.
NO-side  (spot below strike): trade only when rv_15m < tercile-2 boundary.

Threshold = median rv_15m of the original K6 sample (rv15 ≈ 0.001 to 0.004 range).
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


def summarize(df, label):
    if df is None or len(df) == 0: print(f"  [{label}] NO DATA"); return
    n = len(df); wins = (df['pnl']>0).sum()
    span = (df['available_at'].max()-df['available_at'].min()).total_seconds()/86400
    daily = df.set_index('available_at').resample('1D')['pnl'].sum()
    sr = daily.mean()/daily.std()*np.sqrt(365) if daily.std()>0 else 0
    cum = df.sort_values('available_at')['pnl'].cumsum()
    dd = (cum - cum.cummax()).min()
    print(f"  [{label}]  n={n:>5} win={wins/n*100:5.1f}% "
          f"avg=${df['pnl'].mean():+.4f} total=${df['pnl'].sum():+.2f} "
          f"daily=${df['pnl'].sum()/max(span,1):+.2f} SR={sr:5.2f} DD=${dd:+.2f}")


if __name__ == '__main__':
    print("=" * 100)
    print("K6 v2: K14 vol-regime filter applied")
    print("=" * 100)
    all_dfs = []
    for tband, dlo, dhi, side, label in BUCKETS:
        df = run_bucket(tband, dlo, dhi, side)
        if df is not None:
            df['bucket'] = label
            all_dfs.append(df)
    big = pd.concat(all_dfs, ignore_index=True)

    # Determine regime thresholds from FULL sample
    t1, t2 = big['rv15'].quantile([0.33, 0.67])
    print(f"\nVol terciles on stacked sample: t1={t1:.5f}  t2={t2:.5f}\n")

    # Baseline (no filter)
    summarize(big, 'baseline (all regimes)')

    # Drop low-vol entirely
    no_low = big[big['rv15'] >= t1]
    summarize(no_low, 'drop low regime (rv15 >= t1)')

    # Side-aware filter
    yes_mask = (big['side']=='yes') & (big['rv15'] >= t1)  # YES only mid+high
    no_mask  = (big['side']=='no')  & (big['rv15'] <  t2)  # NO only low+mid
    filt = big[yes_mask | no_mask]
    summarize(filt, 'side-aware (YES in mid+high, NO in low+mid)')

    # Stronger version: YES only in high, NO only in low+mid
    yes_high = (big['side']=='yes') & (big['rv15'] >= t2)
    no_lowmid = (big['side']=='no')  & (big['rv15'] <  t2)
    filt2 = big[yes_high | no_lowmid]
    summarize(filt2, 'aggressive (YES high-only, NO low+mid)')

    print()
    print("Per-bucket × filter — total PnL:")
    for label, msk in [('baseline', big.index>=0),
                       ('drop low', big['rv15']>=t1),
                       ('side-aware', yes_mask | no_mask)]:
        sub = big[msk]
        g = sub.groupby('bucket')['pnl'].agg(n='count', total='sum').round(2)
        g['label'] = label
        print(f"\n  {label}:")
        print(g.to_string())
