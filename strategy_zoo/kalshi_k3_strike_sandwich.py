"""K3 strike-spread sandwich.

Buy YES at strike X, sell YES at strike X+ΔX (or buy NO at X+ΔX, same thing).
Synthetic position pays $1 if BTC settles in (X, X+ΔX], else 0.

Cost = yes_ask(X) - yes_bid(X+ΔX)  + fees
If empirical_P(BTC ∈ band) > cost, profitable.

For an hourly KXBTCD event with strikes at $100 spacing, this lets us bet on
narrow bands. The standard pricing should be roughly the empirical density of
BTC at hourly close.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def scan(time_band_min, time_band_max, dx=100):
    """For each (event, snapshot_time, strike) pair, find the strike at X+dx,
    compute synthetic spread cost and the actual band hit rate."""
    db = duckdb.connect(DB, read_only=True)
    df = db.execute(f"""
        WITH snap AS (
            SELECT event_ticker, available_at, close_time,
                   floor_strike,
                   yes_ask_close, yes_bid_close,
                   DATE_DIFF('minute', available_at, close_time) AS mins_to_close
            FROM v_research_universe
            WHERE close_time IS NOT NULL
              AND yes_ask_close IS NOT NULL AND yes_bid_close IS NOT NULL
              AND yes_ask_close > 0 AND yes_ask_close < 1
        ),
        same_snap AS (
            SELECT a.event_ticker AS event_ticker,
                   a.available_at AS available_at,
                   a.close_time AS close_time,
                   a.floor_strike AS lower_strike,
                   b.floor_strike AS upper_strike,
                   a.yes_ask_close AS yes_ask_lower,
                   b.yes_bid_close AS yes_bid_upper,
                   a.mins_to_close
            FROM snap a
            JOIN snap b USING (event_ticker, available_at)
            WHERE b.floor_strike - a.floor_strike = {dx}
              AND a.mins_to_close BETWEEN {time_band_min} AND {time_band_max}
        ),
        dedup AS (
            SELECT *, ROW_NUMBER() OVER
                (PARTITION BY event_ticker, lower_strike ORDER BY available_at) AS rn
            FROM same_snap
        ),
        first_only AS (SELECT * FROM dedup WHERE rn = 1),
        with_settle AS (
            SELECT q.*,
                   (SELECT close FROM btc_1m b
                    WHERE b.bucket_start <= q.close_time
                    ORDER BY b.bucket_start DESC LIMIT 1) AS btc_close
            FROM first_only q
        )
        SELECT *,
               (btc_close > lower_strike AND btc_close <= upper_strike)::INT AS in_band,
               yes_ask_lower - yes_bid_upper AS spread_cost
        FROM with_settle
        WHERE btc_close IS NOT NULL
    """).df()
    db.close()

    if len(df) == 0: return None

    # Per-side fees: pay yes_ask_lower (with fee), receive yes_bid_upper (subject to fee)
    df['fee_in'] = df['yes_ask_lower'].apply(kalshi_fee)
    df['fee_out'] = df['yes_bid_upper'].apply(kalshi_fee)
    df['total_cost'] = df['spread_cost'] + df['fee_in'] + df['fee_out']
    df['edge'] = df['in_band'] - df['total_cost']
    return df


def summarize(df, label):
    if df is None or len(df) == 0:
        print(f"  [{label}] NO DATA"); return
    # Filter to "tradeable" — where cost is reasonable (0 < cost < 0.5)
    sub = df[(df['total_cost'] > 0) & (df['total_cost'] < 0.5)].copy()
    if len(sub) == 0:
        print(f"  [{label}] no rows with sane cost"); return
    n = len(sub); avg_cost = sub['total_cost'].mean()
    actual_rate = sub['in_band'].mean()
    avg_edge = sub['edge'].mean()
    pos_edge = (sub['edge'] > 0).mean()
    sum_edge = sub['edge'].sum()
    span = (sub['available_at'].max() - sub['available_at'].min()).total_seconds() / 86400
    print(f"  [{label}]  n={n:>6}  span={span:.0f}d")
    print(f"    avg synthetic_cost={avg_cost:.4f}  in_band_rate={actual_rate:.3f}  "
          f"avg_edge=${avg_edge:+.4f}  win%={pos_edge*100:.1f}%")
    print(f"    total_edge=${sum_edge:+.2f}  ({sum_edge/max(span,1):.2f}/day)")


if __name__ == '__main__':
    print("=" * 90)
    print("K3 strike-spread sandwich: buy YES@X, sell YES@X+100 → pays $1 if BTC in (X,X+100]")
    print("=" * 90)
    print("\n--- Scan by time-to-close (dx=100) ---")
    for tlo, thi in [(0, 5), (5, 15), (15, 30), (30, 60), (60, 1440)]:
        df = scan(tlo, thi, dx=100)
        summarize(df, f't {tlo}-{thi}m  dx=$100')

    print("\n--- Wider spread (dx=500) for thicker bands ---")
    for tlo, thi in [(5, 15), (15, 30), (30, 60)]:
        df = scan(tlo, thi, dx=500)
        summarize(df, f't {tlo}-{thi}m  dx=$500')
