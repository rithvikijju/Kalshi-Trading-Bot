"""Price-calibration scan: at each (price-band, time-to-close) bucket, compare
the IMPLIED probability (= mid price) to the ACTUAL settlement rate.

If empirical_rate > implied → buy (under-priced)
If implied > empirical_rate → sell (over-priced)

Edge = |empirical - implied|, must clear fee ~7%*P*(1-P) per side."""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def calibration_scan():
    db = duckdb.connect(DB, read_only=True)
    df = db.execute("""
        WITH quotes AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.yes_bid_close AS yes_bid,
                   (q.yes_ask_close + q.yes_bid_close)/2.0 AS yes_mid,
                   q.floor_strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close,
                   ROW_NUMBER() OVER (PARTITION BY q.market_ticker,
                                      DATE_DIFF('minute', q.available_at, q.close_time)
                                      ORDER BY q.available_at) AS rn_per_minute
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.yes_bid_close IS NOT NULL AND q.yes_ask_close IS NOT NULL
              AND q.yes_ask_close > 0 AND q.yes_ask_close <= 1
        ),
        first_per_min AS (
            SELECT * FROM quotes WHERE rn_per_minute = 1
              AND mins_to_close BETWEEN 0 AND 60
        ),
        with_settle AS (
            SELECT q.*,
                   (SELECT close FROM btc_1m b
                    WHERE b.bucket_start <= q.close_time
                    ORDER BY b.bucket_start DESC LIMIT 1) AS btc_at_close
            FROM first_per_min q
        ),
        scored AS (
            SELECT *,
                CASE WHEN btc_at_close > floor_strike THEN 1.0 ELSE 0.0 END AS yes_wins,
                CASE
                  WHEN yes_mid < 0.05 THEN '00-05'
                  WHEN yes_mid < 0.10 THEN '05-10'
                  WHEN yes_mid < 0.20 THEN '10-20'
                  WHEN yes_mid < 0.30 THEN '20-30'
                  WHEN yes_mid < 0.40 THEN '30-40'
                  WHEN yes_mid < 0.50 THEN '40-50'
                  WHEN yes_mid < 0.60 THEN '50-60'
                  WHEN yes_mid < 0.70 THEN '60-70'
                  WHEN yes_mid < 0.80 THEN '70-80'
                  WHEN yes_mid < 0.90 THEN '80-90'
                  WHEN yes_mid < 0.95 THEN '90-95'
                  ELSE '95+' END AS price_band,
                CASE
                  WHEN mins_to_close <= 1 THEN '0-1m'
                  WHEN mins_to_close <= 5 THEN '1-5m'
                  WHEN mins_to_close <= 15 THEN '5-15m'
                  WHEN mins_to_close <= 30 THEN '15-30m'
                  ELSE '30-60m' END AS time_band
            FROM with_settle WHERE btc_at_close IS NOT NULL
        )
        SELECT price_band, time_band,
               COUNT(*) AS n,
               AVG(yes_mid) AS implied_p,
               AVG(yes_wins) AS empirical_p,
               AVG(yes_wins) - AVG(yes_mid) AS edge,
               AVG(yes_ask) AS avg_ask,
               AVG(yes_bid) AS avg_bid
        FROM scored
        GROUP BY price_band, time_band
        ORDER BY time_band, price_band
    """).df()
    db.close()
    return df


def show(df):
    print("=" * 100)
    print(f"{'time':<8}{'price':<8}{'n':>6}{'implied':>10}{'empirical':>11}{'edge':>10}{'ask':>8}{'bid':>8}")
    print("=" * 100)
    for time_band in sorted(df['time_band'].unique()):
        sub = df[df['time_band']==time_band].sort_values('price_band')
        for _, row in sub.iterrows():
            if row['n'] < 30: continue
            marker = '★' if abs(row['edge']) > 0.05 else ' '
            print(f"  {row['time_band']:<6}{row['price_band']:<8}{int(row['n']):>6}"
                  f"{row['implied_p']*100:>9.1f}%{row['empirical_p']*100:>10.1f}%"
                  f"{row['edge']*100:>+9.1f}%{row['avg_ask']:>8.3f}{row['avg_bid']:>8.3f}  {marker}")
    print()
    print("Edge > 5% (★) is potentially tradeable after fees (fee ≤ 1.75¢ at mid prices)")
    print()


def find_best_strategy(df):
    """Strategy: buy or sell based on calibration. Compute expected $ per trade."""
    print("=" * 100)
    print("TRADEABLE OPPORTUNITIES — pick band/time pairs with |edge| > fee-floor")
    print("=" * 100)
    profitable = []
    for _, row in df.iterrows():
        if row['n'] < 50: continue
        implied = row['implied_p']
        empirical = row['empirical_p']
        if implied <= 0 or implied >= 1: continue
        # Fee at implied price ~ 0.07 * P * (1-P)
        fee = 0.07 * implied * (1-implied)
        # If empirical > implied + 2*fee → buy YES (cost = avg_ask), settles 1 with prob empirical
        # Expected pnl = empirical * 1 - (avg_ask + fee)
        edge_buy = empirical - row['avg_ask'] - fee
        edge_sell = row['avg_bid'] - empirical - fee
        edge = max(edge_buy, edge_sell)
        side = 'BUY' if edge_buy > edge_sell else 'SELL'
        if edge > 0.005:   # at least 0.5c after fees
            ann_factor = row['n']  # rough: # trades over 65 days
            profitable.append({
                'time':row['time_band'], 'price':row['price_band'],
                'n':int(row['n']), 'side':side, 'edge_per_trade':edge,
                'implied':implied, 'empirical':empirical,
                'total_pnl_estimate':edge*row['n']
            })
    if not profitable:
        print("  No bucket has edge > 0.5c after fees.")
        return
    sorted_p = sorted(profitable, key=lambda x: -x['total_pnl_estimate'])
    for p in sorted_p[:20]:
        print(f"  {p['side']} | time={p['time']} price={p['price']:<6} | "
              f"n={p['n']:>4} edge=${p['edge_per_trade']:+.4f} "
              f"(impl={p['implied']*100:.1f}% emp={p['empirical']*100:.1f}%) "
              f"total≈${p['total_pnl_estimate']:.2f}")


if __name__ == '__main__':
    df = calibration_scan()
    show(df)
    find_best_strategy(df)
