"""Check signal correlation in K6 v2.

If BTC moves $300 in a minute, many strike markets in the same event simultaneously
hit the "spot past strike" condition. My backtest counts these as n independent
trades, but they're really 1 correlated bet.

Aggregate signals by minute and event, then re-compute Sharpe at the de-correlated
level."""
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
        with_spot AS (SELECT q.*, b.close AS spot, b.rv_15m AS rv15
            FROM q LEFT JOIN btc_1m b ON b.bucket_start = q.qmin),
        in_band AS (SELECT * FROM with_spot
            WHERE spot IS NOT NULL AND rv15 IS NOT NULL
              AND spot - strike BETWEEN {dlo} AND {dhi}),
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


if __name__ == '__main__':
    all_dfs = []
    for tband, dlo, dhi, side, label in BUCKETS:
        df = run_bucket(tband, dlo, dhi, side)
        if df is not None:
            df['bucket'] = label
            all_dfs.append(df)
    big = pd.concat(all_dfs, ignore_index=True)
    t1, t2 = big['rv15'].quantile([0.33, 0.67])
    yes_mask = (big['side']=='yes') & (big['rv15'] >= t1)
    no_mask  = (big['side']=='no')  & (big['rv15'] <  t2)
    filt = big[yes_mask | no_mask].copy()

    print(f"Total filtered trades: {len(filt)}")

    # Per-event analysis: how clustered are signals within an event?
    sig_per_event = filt.groupby('event_ticker').size()
    print(f"\nSignals per event:")
    print(f"  n events: {len(sig_per_event)}")
    print(f"  mean signals/event: {sig_per_event.mean():.2f}")
    print(f"  p50/p90/max: {sig_per_event.median():.0f} / {sig_per_event.quantile(0.9):.0f} / {sig_per_event.max():.0f}")
    print(f"  events with >5 signals: {(sig_per_event > 5).sum()}")
    print(f"  events with >10 signals: {(sig_per_event > 10).sum()}")

    # Per-minute clustering
    filt['minute'] = filt['available_at'].dt.floor('1min')
    sig_per_min = filt.groupby('minute').size()
    print(f"\nSignals per minute:")
    print(f"  mean: {sig_per_min.mean():.2f}  max: {sig_per_min.max()}")

    # Per-day P&L distribution (correlated trades aggregate)
    daily = filt.set_index('available_at').resample('1D')['pnl'].sum()
    print(f"\nDaily P&L (per 1-contract sizing across all signals):")
    print(f"  days: {len(daily)}  mean: ${daily.mean():.2f}  std: ${daily.std():.2f}")
    print(f"  positive days: {(daily>0).sum()}/{len(daily)}  ({(daily>0).mean()*100:.0f}%)")
    print(f"  worst day: ${daily.min():.2f}  best: ${daily.max():.2f}")
    print(f"  daily SR: {daily.mean()/daily.std()*np.sqrt(365):.2f}")

    # Per-EVENT P&L (most-correlated unit since one BTC move = one event's worth of signals)
    per_event_pnl = filt.groupby('event_ticker')['pnl'].sum()
    print(f"\nPer-event aggregate P&L (correlation-honest unit):")
    print(f"  n events with signals: {len(per_event_pnl)}")
    print(f"  mean PnL/event: ${per_event_pnl.mean():.3f}  std: ${per_event_pnl.std():.3f}")
    print(f"  events positive: {(per_event_pnl>0).sum()}/{len(per_event_pnl)}  ({(per_event_pnl>0).mean()*100:.0f}%)")
    print(f"  worst event: ${per_event_pnl.min():.2f}")
    print(f"  best event:  ${per_event_pnl.max():.2f}")
    # Equivalent Sharpe at event level
    if per_event_pnl.std() > 0:
        # ~24 events/day × 365 = 8760/yr
        sr_event = per_event_pnl.mean()/per_event_pnl.std()*np.sqrt(8760)
        print(f"  event-level SR (assuming 24 events/day): {sr_event:.2f}")
