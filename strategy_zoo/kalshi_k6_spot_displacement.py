"""K6 spot-displacement staleness.

If spot is FAR above strike (BTC > strike + $D) the conditional YES probability
should be very high (95%+). If the Kalshi yes_ask is materially below that, the
quote is stale → buy YES.

Symmetric: if spot is far BELOW strike, buy NO.

We bucket by (spot_distance, time_to_close) and compare implied vs empirical.
"""
from __future__ import annotations
import math, duckdb, pandas as pd

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def scan():
    db = duckdb.connect(DB, read_only=True)
    # Pre-aggregate btc minute prices into a small table that fits in RAM, then asof-join
    db.execute("PRAGMA memory_limit='4GB'")
    df = db.execute("""
        WITH btc AS (
            SELECT bucket_start, close FROM btc_1m
        ),
        q AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.yes_ask_close AS yes_ask, q.yes_bid_close AS yes_bid,
                   (q.yes_ask_close + q.yes_bid_close)/2 AS yes_mid,
                   q.floor_strike AS strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close,
                   -- bucket timestamp to nearest minute (floor) for asof-style join
                   DATE_TRUNC('minute', q.available_at) AS quote_minute,
                   DATE_TRUNC('minute', q.close_time)    AS close_minute
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.yes_ask_close IS NOT NULL AND q.yes_bid_close IS NOT NULL
              AND q.yes_ask_close > 0 AND q.yes_ask_close < 1
        ),
        sampled AS (
            -- sample 1 quote per (market, 5-min-bucket) to compress
            SELECT *, ROW_NUMBER() OVER
              (PARTITION BY market_ticker, FLOOR(mins_to_close/5)
               ORDER BY available_at) AS rn
            FROM q
        ),
        s AS (SELECT * FROM sampled WHERE rn = 1),
        with_spot AS (
            SELECT s.*, b.close AS spot_at_quote
            FROM s LEFT JOIN btc b ON b.bucket_start = s.quote_minute
        ),
        with_settle AS (
            SELECT w.*, b.close AS btc_at_close
            FROM with_spot w LEFT JOIN btc b ON b.bucket_start = w.close_minute
        ),
        scored AS (
            SELECT *,
                spot_at_quote - strike AS spot_dist,
                CASE WHEN btc_at_close > strike THEN 1.0 ELSE 0.0 END AS yes_wins
            FROM with_settle
            WHERE spot_at_quote IS NOT NULL AND btc_at_close IS NOT NULL
        ),
        banded AS (
            SELECT *,
                CASE
                  WHEN spot_dist < -1000 THEN 'd <-1000'
                  WHEN spot_dist < -500 THEN 'd -1000..-500'
                  WHEN spot_dist < -200 THEN 'd -500..-200'
                  WHEN spot_dist < -50  THEN 'd -200..-50'
                  WHEN spot_dist < 50   THEN 'd -50..+50'
                  WHEN spot_dist < 200  THEN 'd +50..+200'
                  WHEN spot_dist < 500  THEN 'd +200..+500'
                  WHEN spot_dist < 1000 THEN 'd +500..+1000'
                  ELSE 'd >+1000' END AS dist_band,
                CASE
                  WHEN mins_to_close <= 5 THEN '0-5m'
                  WHEN mins_to_close <= 15 THEN '5-15m'
                  WHEN mins_to_close <= 30 THEN '15-30m'
                  ELSE '30-60m' END AS time_band
            FROM scored
        )
        SELECT dist_band, time_band,
               COUNT(*) AS n,
               AVG(yes_mid) AS implied_mid,
               AVG(yes_ask) AS avg_ask,
               AVG(yes_bid) AS avg_bid,
               AVG(yes_wins) AS empirical
        FROM banded
        GROUP BY dist_band, time_band
        ORDER BY time_band, dist_band
    """).df()
    db.close()
    return df


def print_table(df):
    print(f"{'time':<8}{'dist':<18}{'n':>7}{'mid':>9}{'ask':>9}{'bid':>9}{'emp':>9}{'edge_buy':>11}{'edge_sell':>11}")
    print("-" * 90)
    for time_band in sorted(df['time_band'].unique()):
        sub = df[df['time_band'] == time_band]
        for _, row in sub.iterrows():
            if row['n'] < 50: continue
            implied = row['implied_mid']
            emp = row['empirical']
            fee_buy = 0.07 * row['avg_ask'] * (1-row['avg_ask'])
            edge_buy = emp - row['avg_ask'] - fee_buy
            fee_sell = 0.07 * row['avg_bid'] * (1-row['avg_bid'])
            edge_sell = row['avg_bid'] - emp - fee_sell
            mark = '★' if max(edge_buy, edge_sell) > 0.01 else ' '
            print(f"{row['time_band']:<8}{row['dist_band']:<18}{int(row['n']):>7}"
                  f"{implied*100:>8.1f}%{row['avg_ask']:>9.3f}{row['avg_bid']:>9.3f}{emp*100:>8.1f}%"
                  f"{edge_buy*100:>+10.2f}%{edge_sell*100:>+10.2f}%  {mark}")


if __name__ == '__main__':
    print("=" * 100)
    print("K6 spot-displacement staleness: empirical settlement by (spot_dist_at_quote, time)")
    print("=" * 100)
    df = scan()
    print_table(df)
    print()
    print("edge_buy  = empirical - ask - fee  (buy YES when ★)")
    print("edge_sell = bid - empirical - fee  (sell YES when ★)")
