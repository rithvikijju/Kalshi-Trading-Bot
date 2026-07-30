"""K9 validation: trade the opening miscalibration.

At mtc 58-60 (first 2 min of event), for each market:
  - Check spot displacement from strike at that moment
  - If spot >> strike but opening yes_ask is "too low" (market hasn't repriced),
    BUY YES, hold to settlement
  - Symmetric: spot << strike but yes_bid is "too high" → BUY NO, hold

Compare P&L vs K6 (which fires at mtc 0-15 only). K9 should be ORTHOGONAL since
different time window.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


# Buckets: filter on opening mid being WRONG given spot_dist
# YES side: spot above strike, but opening mid < 0.80 (market hasn't repriced YES up yet)
# NO side: spot below strike, but opening mid > 0.20 (= NO mid < 0.80, market hasn't repriced NO up yet)
BUCKETS = [
    ('yes',   50,   200,  0.05, 0.80,  'K9-Y1 d+50..+200 mid<0.80'),
    ('yes',  200,   500,  0.05, 0.80,  'K9-Y2 d+200..+500 mid<0.80'),
    ('yes',  500,  9999,  0.05, 0.80,  'K9-Y3 d>+500 mid<0.80'),
    ('yes',  500,  9999,  0.05, 0.50,  'K9-Y3b d>+500 mid<0.50'),
    ('no',  -200,   -50,  0.20, 0.95,  'K9-N1 d-200..-50 mid>0.20'),
    ('no',  -500,  -200,  0.20, 0.95,  'K9-N2 d-500..-200 mid>0.20'),
    ('no', -9999,  -500,  0.20, 0.95,  'K9-N3 d<-500 mid>0.20'),
    ('no', -9999,  -500,  0.50, 0.95,  'K9-N3b d<-500 mid>0.50'),
]


def run_bucket(side, dlo, dhi, mid_lo, mid_hi, label):
    # mid filter applies to YES mid; for NO buckets, "yes_mid > 0.20" means
    # "yes is rated >20% likely", i.e., NO is rated <80%, so NO is undervalued.
    # That's the right framing.
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")
    df = db.execute(f"""
        WITH q AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.no_ask_exe AS no_ask,
                   (q.yes_ask_close + q.yes_bid_close)/2 AS yes_mid,
                   q.floor_strike AS strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mtc,
                   DATE_TRUNC('minute', q.available_at) AS qmin,
                   DATE_TRUNC('minute', q.close_time) AS cmin
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.yes_ask_close IS NOT NULL AND q.no_ask_exe IS NOT NULL
              AND q.yes_ask_close > 0 AND q.yes_ask_close < 1
              AND q.no_ask_exe > 0 AND q.no_ask_exe < 1
              AND DATE_DIFF('minute', q.available_at, q.close_time) BETWEEN 58 AND 60
        ),
        with_spot AS (SELECT q.*, b.close AS spot, b.rv_15m AS rv15
            FROM q LEFT JOIN btc_1m b ON b.bucket_start = q.qmin),
        in_band AS (SELECT * FROM with_spot
            WHERE spot IS NOT NULL AND spot - strike BETWEEN {dlo} AND {dhi}
              AND yes_mid BETWEEN {mid_lo} AND {mid_hi}),
        first AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn FROM in_band),
        first_only AS (SELECT * FROM first WHERE rn = 1),
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
    df['bucket'] = label
    return df


def summarize(df, label):
    if df is None or len(df) == 0: print(f"  [{label}] NO DATA"); return
    n = len(df); wins = (df['pnl']>0).sum()
    span = (df['available_at'].max()-df['available_at'].min()).total_seconds()/86400
    daily = df.set_index('available_at').resample('1D')['pnl'].sum()
    sr = daily.mean()/daily.std()*np.sqrt(365) if daily.std()>0 else 0
    cum = df.sort_values('available_at')['pnl'].cumsum()
    dd = (cum - cum.cummax()).min()
    cap = df['paid'].mean()
    print(f"  [{label}] n={n:>5}  win={wins/n*100:5.1f}%  cap=${cap:.2f}  "
          f"avg=${df['pnl'].mean():+.4f}  total=${df['pnl'].sum():+.2f}  "
          f"daily=${df['pnl'].sum()/max(span,1):+.2f}  SR={sr:5.2f}  DD=${dd:+.2f}")


if __name__ == '__main__':
    print("=" * 100)
    print("K9 validation: opening-quote (mtc 58-60) → hold to settlement")
    print("=" * 100)
    all_dfs = []
    for side, dlo, dhi, mlo, mhi, label in BUCKETS:
        df = run_bucket(side, dlo, dhi, mlo, mhi, label)
        summarize(df, label)
        if df is not None: all_dfs.append(df)
    big = pd.concat(all_dfs, ignore_index=True) if all_dfs else None
    if big is not None and len(big) > 0:
        print()
        print("=" * 100)
        print("Stacked K9 portfolio")
        print("=" * 100)
        summarize(big, 'K9 stacked')

        # Vol-regime filter
        t1, t2 = big['rv15'].quantile([0.33, 0.67])
        yes_mask = (big['side']=='yes') & (big['rv15'] >= t1)
        no_mask  = (big['side']=='no')  & (big['rv15'] <  t2)
        filt = big[yes_mask | no_mask]
        summarize(filt, 'K9 side-aware vol-filtered')
