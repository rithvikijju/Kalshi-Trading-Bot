"""K9: first-N-minute mean reversion.

When an hourly event opens, early prints can be wide/extreme. Hypothesis: extreme
quotes in the first 2-5 minutes mean-revert toward a more correct probability
as MMs respond and reset spreads.

Test: at minute 58-60 (just after event open), find quotes where yes_ask is extreme
(>0.95 or <0.05) but spot is NOT decisive on that side. Then see what happens to
the mid over the next 10 minutes.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def main():
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")

    # Find the FIRST quote per market at very-early-event (mins_to_close >= 58)
    # then look up the mid at mins_to_close ≈ 50 (8 mins later)
    df = db.execute("""
        WITH first_quotes AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.yes_bid_close AS yes_bid,
                   (q.yes_ask_close + q.yes_bid_close)/2 AS yes_mid_now,
                   q.floor_strike AS strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mtc,
                   DATE_TRUNC('minute', q.available_at) AS qmin,
                   ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.yes_ask_close IS NOT NULL AND q.yes_bid_close IS NOT NULL
              AND q.yes_ask_close > 0 AND q.yes_ask_close < 1
              AND DATE_DIFF('minute', q.available_at, q.close_time) BETWEEN 58 AND 60
        ),
        first_only AS (SELECT * FROM first_quotes WHERE rn = 1),
        with_spot AS (
            SELECT f.*, b.close AS spot
            FROM first_only f LEFT JOIN btc_1m b ON b.bucket_start = f.qmin
        )
        SELECT * FROM with_spot WHERE spot IS NOT NULL
    """).df()

    print(f"  First quotes at mins_to_close 58-60: {len(df):,}")
    df['spot_dist'] = df['spot'] - df['strike']

    # For each of these, look up the mid at mtc ≈ 45-55 (5-15 min later)
    db.register('opening_quotes', df[['market_ticker', 'close_time', 'available_at',
                                        'yes_mid_now', 'spot_dist']])
    later = db.execute("""
        SELECT o.market_ticker, o.yes_mid_now, o.spot_dist, o.available_at,
               (
                 SELECT (yes_ask_close + yes_bid_close)/2.0
                 FROM v_research_universe r
                 WHERE r.market_ticker = o.market_ticker
                   AND DATE_DIFF('minute', r.available_at, o.close_time) BETWEEN 45 AND 55
                   AND r.yes_ask_close IS NOT NULL AND r.yes_bid_close IS NOT NULL
                 ORDER BY r.available_at ASC LIMIT 1
               ) AS mid_later
        FROM opening_quotes o
    """).df()
    db.close()

    later = later.dropna(subset=['mid_later'])
    print(f"  Resolved later mid: {len(later):,}")
    if len(later) == 0: return

    later['delta'] = later['mid_later'] - later['yes_mid_now']

    # Bucket by spot_dist and opening mid
    later['dist_band'] = pd.cut(later['spot_dist'],
        bins=[-9999,-500,-200,-50,50,200,500,9999],
        labels=['<-500','-500..-200','-200..-50','-50..+50','+50..+200','+200..+500','>+500'])
    later['mid_band'] = pd.cut(later['yes_mid_now'],
        bins=[0,0.05,0.20,0.50,0.80,0.95,1.0],
        labels=['<5%','5-20%','20-50%','50-80%','80-95%','95+'])

    print(f"\nMean Δmid by opening mid_band:")
    g = later.groupby('mid_band', observed=True)['delta'].agg(['count','mean','median'])
    print(g.to_string())

    print(f"\nMean Δmid by spot_dist_band:")
    g = later.groupby('dist_band', observed=True)['delta'].agg(['count','mean','median'])
    print(g.to_string())

    print(f"\nCross-tab (mid × dist) — mean Δmid in cents (n>=20):")
    pvt = later.pivot_table(index='mid_band', columns='dist_band', values='delta',
                              aggfunc='mean', observed=True)
    cnt = later.pivot_table(index='mid_band', columns='dist_band', values='delta',
                              aggfunc='count', observed=True)
    pvt_masked = pvt.where(cnt >= 20)
    print((pvt_masked * 100).round(1).to_string())


if __name__ == '__main__':
    print("=" * 90)
    print("K9: opening quote (mtc 58-60) → mid at mtc 45-55")
    print("=" * 90)
    main()
