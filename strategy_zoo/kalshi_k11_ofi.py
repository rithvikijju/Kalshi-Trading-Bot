"""K11: order-flow imbalance — does (bid_qty - ask_qty) / (bid_qty + ask_qty)
predict short-term mid move?

Test: for each tick where YES side has both bid and ask quantities, look ahead
T seconds and see if mid moved in the direction OFI predicted.

If correlation is robust + survives fee floor, it's a signal.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb'


def main(horizon_sec=60, min_qty=5):
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")
    # Sample ticks where book is well-defined on the YES side
    print(f"Sampling ticks where yes_bid_qty + yes_ask_qty >= {min_qty}, mid in [0.10, 0.90]...")
    df = db.execute(f"""
        WITH ticks AS (
            SELECT received_at_utc::TIMESTAMP AS ts,
                   market_ticker, event_ticker,
                   yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
                   (yes_bid + yes_ask)/2.0 AS mid_now
            FROM ws_orderbook_top_dedup
            WHERE yes_bid > 0 AND yes_ask > 0
              AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
              AND yes_bid_qty IS NOT NULL AND yes_ask_qty IS NOT NULL
              AND yes_bid_qty + yes_ask_qty >= {min_qty}
              AND yes_bid < yes_ask
              AND (yes_bid + yes_ask)/2.0 BETWEEN 0.10 AND 0.90
        ),
        sampled AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY market_ticker,
                       FLOOR(EXTRACT(EPOCH FROM ts)/10) ORDER BY ts) AS rn_per_10s
            FROM ticks
        ),
        one_per_10s AS (SELECT * FROM sampled WHERE rn_per_10s = 1)
        SELECT * FROM one_per_10s
        USING SAMPLE 100000 ROWS
    """).df()
    print(f"  sampled {len(df):,} ticks")
    if len(df) == 0: return

    df['ofi'] = (df['yes_bid_qty'] - df['yes_ask_qty']) / (df['yes_bid_qty'] + df['yes_ask_qty'])

    # For each tick, look up mid at +horizon_sec by querying the live capture DB
    # Do this by joining on market_ticker and finding the latest mid before ts + horizon
    print(f"Looking up mid at +{horizon_sec}s for each tick...")
    df['target_ts'] = df['ts'] + pd.Timedelta(seconds=horizon_sec)

    # Approach: rather than do millions of correlated subqueries, do a range join with
    # the dedup table sampled to 1 tick per 10s.
    db.register('tick_lookup', df[['market_ticker', 'ts', 'target_ts', 'mid_now', 'ofi']])
    fut = db.execute(f"""
        WITH q AS (
            SELECT t.market_ticker, t.ts, t.target_ts, t.mid_now, t.ofi,
                   (
                     SELECT (yes_bid+yes_ask)/2.0
                     FROM ws_orderbook_top_dedup ob
                     WHERE ob.market_ticker = t.market_ticker
                       AND ob.received_at_utc::TIMESTAMP BETWEEN t.target_ts
                            AND t.target_ts + INTERVAL '5 seconds'
                       AND ob.yes_bid IS NOT NULL AND ob.yes_ask IS NOT NULL
                     ORDER BY ob.received_at_utc ASC LIMIT 1
                   ) AS mid_future
            FROM tick_lookup t
        )
        SELECT *, mid_future - mid_now AS dmid FROM q WHERE mid_future IS NOT NULL
    """).df()
    db.close()

    if len(fut) == 0:
        print("No future ticks within window."); return

    print(f"Resolved future mid for {len(fut):,} ticks")

    # Correlation
    c = fut[['ofi', 'dmid']].corr().iloc[0,1]
    print(f"\nCorr(OFI, Δmid over {horizon_sec}s): {c:+.4f}")

    # Bucketed
    fut['ofi_band'] = pd.cut(fut['ofi'], bins=[-1.01, -0.5, -0.2, 0.2, 0.5, 1.01],
                              labels=['<-.5','-.5..-.2','-.2..+.2','+.2..+.5','>+.5'])
    g = fut.groupby('ofi_band', observed=True)['dmid'].agg(['count','mean','median'])
    g['bps'] = g['mean']*100
    print(f"\nMean Δmid by OFI band:")
    print(g.to_string())

    # Tradeability: edge after fees
    # If OFI > 0.5 predicts +X cents and we buy YES at ask, paying ~0.5c spread + 1.75c fee
    # Need X > 2.25¢ to break even
    print(f"\nFor each |OFI| > 0.5 bucket, |Δmid| in cents:")
    pos = fut[fut['ofi'] > 0.5]['dmid']
    neg = fut[fut['ofi'] < -0.5]['dmid']
    print(f"  OFI > +0.5: n={len(pos)}  mean dmid={pos.mean()*100:+.3f}¢  median={pos.median()*100:+.3f}¢")
    print(f"  OFI < -0.5: n={len(neg)}  mean dmid={neg.mean()*100:+.3f}¢  median={neg.median()*100:+.3f}¢")


if __name__ == '__main__':
    print("=" * 80)
    print("K11: OFI → short-term mid move")
    print("=" * 80)
    for h in [30, 60, 300]:
        print(f"\n--- horizon {h}s ---")
        main(horizon_sec=h, min_qty=5)
