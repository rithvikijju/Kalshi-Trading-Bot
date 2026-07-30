"""Validate the K6 spot-displacement findings with a proper trade-level backtest.

For each (time_band, dist_band) profitable bucket from the scan, simulate:
  - Sample first quote per (market, 5-min-window) where the bucket condition holds
  - BUY at yes_ask (or NO equivalent at no_ask), pay Kalshi fee
  - Settle 1.0/0.0 based on BTC at close
  - Compute realized PnL, win rate, sharpe, drawdown

Stack all profitable buckets into one portfolio P&L curve.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np
from datetime import timedelta

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


# Profitable buckets from K6 scan:
# Format: (time_band, dist_lo, dist_hi, side) where side='yes' means BUY YES at ask
BUCKETS = [
    # Profitable buckets only (A3/C1/D1 dropped after validation)
    ('0-5m',   50,   200,  'yes',  'A1 0-5m d+50..+200'),
    ('0-5m',   200,  500,  'yes',  'A2 0-5m d+200..+500'),
    ('5-15m',  50,   200,  'yes',  'B1 5-15m d+50..+200'),
    ('5-15m',  200,  500,  'yes',  'B2 5-15m d+200..+500'),
    # Symmetric NO-side: when spot is BELOW strike, NO should be favored
    ('0-5m',  -200,  -50,  'no',   'A1n 0-5m d-200..-50 NO'),
    ('0-5m',  -500, -200,  'no',   'A2n 0-5m d-500..-200 NO'),
    ('5-15m', -200,  -50,  'no',   'B1n 5-15m d-200..-50 NO'),
    ('5-15m', -500, -200,  'no',   'B2n 5-15m d-500..-200 NO'),
]

TIME_BANDS = {
    '0-5m':   (0, 5),
    '5-15m':  (5, 15),
    '15-30m': (15, 30),
    '30-60m': (30, 60),
}


def run_bucket(tband, dlo, dhi, side):
    tlo, thi = TIME_BANDS[tband]
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")
    df = db.execute(f"""
        WITH q AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.yes_bid_close AS yes_bid,
                   q.no_ask_exe AS no_ask,
                   q.floor_strike AS strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close,
                   DATE_TRUNC('minute', q.available_at) AS qmin,
                   DATE_TRUNC('minute', q.close_time) AS cmin
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.yes_ask_close IS NOT NULL AND q.yes_bid_close IS NOT NULL
              AND q.no_ask_exe IS NOT NULL
              AND q.yes_ask_close > 0 AND q.yes_ask_close < 1
              AND q.no_ask_exe > 0 AND q.no_ask_exe < 1
              AND DATE_DIFF('minute', q.available_at, q.close_time) BETWEEN {tlo} AND {thi}
        ),
        with_spot AS (
            SELECT q.*, b.close AS spot
            FROM q LEFT JOIN btc_1m b ON b.bucket_start = q.qmin
        ),
        in_band AS (
            SELECT * FROM with_spot
            WHERE spot IS NOT NULL
              AND spot - strike BETWEEN {dlo} AND {dhi}
        ),
        first_sig AS (
            SELECT *, ROW_NUMBER() OVER
              (PARTITION BY market_ticker ORDER BY available_at) AS rn
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
    return df[['available_at', 'event_ticker', 'market_ticker', 'spot', 'strike',
               'paid', 'fee', 'settles', 'pnl']].copy()


def summarize(df, label):
    if df is None or len(df) == 0:
        print(f"  [{label}] NO DATA"); return None
    n = len(df); wins = (df['pnl'] > 0).sum()
    avg = df['pnl'].mean(); total = df['pnl'].sum()
    span = (df['available_at'].max() - df['available_at'].min()).total_seconds() / 86400
    tpd = n / max(span, 0.1)
    cap = df['paid'].mean()
    daily = df.set_index('available_at').resample('1D')['pnl'].sum()
    sr = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    cum = df.sort_values('available_at')['pnl'].cumsum()
    dd = (cum - cum.cummax()).min()
    print(f"  [{label}]")
    print(f"    n={n:>5}  win={wins/n*100:5.1f}%  tpd={tpd:5.1f}  "
          f"avg_pnl=${avg:+.4f}  total=${total:+.2f}  cap=${cap:.2f}  "
          f"daily_SR={sr:5.2f}  maxDD=${dd:+.2f}")
    return df


if __name__ == '__main__':
    print("=" * 100)
    print("K6 validation: per-bucket and stacked portfolio backtest")
    print("=" * 100)
    all_trades = []
    for tband, dlo, dhi, side, label in BUCKETS:
        df = run_bucket(tband, dlo, dhi, side)
        d2 = summarize(df, label)
        if d2 is not None:
            d2['bucket'] = label
            all_trades.append(d2)

    if not all_trades:
        print("\nNo trades across any bucket.")
    else:
        all_df = pd.concat(all_trades, ignore_index=True).sort_values('available_at')
        print()
        print("=" * 100)
        print("STACKED PORTFOLIO (1 contract per signal across all profitable buckets)")
        print("=" * 100)
        summarize(all_df, 'all-buckets-stacked')
        # bucket-level summary
        print()
        print("PnL breakdown by bucket (sorted):")
        b = all_df.groupby('bucket').agg(n=('pnl','count'),
                                          win=('pnl', lambda s: (s>0).mean()),
                                          avg=('pnl','mean'),
                                          total=('pnl','sum')).sort_values('total', ascending=False)
        print(b.to_string())
