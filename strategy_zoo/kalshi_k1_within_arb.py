"""K1 within-market YES+NO cross arb.

If yes_ask + no_ask < 1.00 per share, buy 1 of each → guaranteed $1 payout.
Edge after fees: 1 - yes_ask - no_ask - fee_yes - fee_no.
Per-side Kalshi fee = ceil(0.07 * P * (1-P) * 100)/100.

Need yes_ask + no_ask + fee_yes + fee_no < 1.00. At symmetric mid 0.5,
fees are ~1.75¢ each → need sum < 96.5¢.
"""
from __future__ import annotations
import math, duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def scan():
    db = duckdb.connect(DB, read_only=True)
    df = db.execute("""
        SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
               q.yes_ask_close AS yes_ask, q.no_ask_exe AS no_ask,
               q.yes_bid_close AS yes_bid, q.floor_strike,
               DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close
        FROM v_research_universe q
        WHERE q.close_time IS NOT NULL
          AND q.yes_ask_close IS NOT NULL AND q.no_ask_exe IS NOT NULL
          AND q.yes_ask_close > 0 AND q.no_ask_exe > 0
          AND q.yes_ask_close + q.no_ask_exe < 1.05
    """).df()
    db.close()
    print(f"Total quotes where yes_ask + no_ask < 1.05: {len(df):,}")

    df['sum_ask'] = df['yes_ask'] + df['no_ask']
    df['fee_yes'] = df['yes_ask'].apply(kalshi_fee)
    df['fee_no']  = df['no_ask'].apply(kalshi_fee)
    df['net_cost'] = df['sum_ask'] + df['fee_yes'] + df['fee_no']
    df['edge'] = 1.0 - df['net_cost']

    print()
    print(f"  sum_ask < 1.00 (gross arb):        {(df['sum_ask'] < 1.0).sum():,}")
    print(f"  sum_ask + fees < 1.00 (true arb):  {(df['edge'] > 0).sum():,}")
    print(f"  edge > 0.5¢:                       {(df['edge'] > 0.005).sum():,}")
    print(f"  edge > 1¢:                         {(df['edge'] > 0.01).sum():,}")
    print(f"  edge > 2¢:                         {(df['edge'] > 0.02).sum():,}")
    print()

    arb = df[df['edge'] > 0.005].copy()
    if len(arb) == 0:
        print("No tradeable cross-arb after fees.")
        return df

    # Dedupe: one trade per (market_ticker) — take first occurrence in time.
    arb = arb.sort_values('available_at')
    first = arb.drop_duplicates('market_ticker', keep='first')
    print(f"Unique markets with arb opportunity: {len(first):,}")
    span_d = (first['available_at'].max() - first['available_at'].min()).total_seconds() / 86400
    print(f"Span: {span_d:.0f} days → {len(first)/max(span_d,0.1):.1f} arbs/day")

    print(f"\nEdge distribution ($/contract pair):")
    print(f"  mean={first['edge'].mean()*100:.2f}¢  median={first['edge'].median()*100:.2f}¢")
    print(f"  p90={first['edge'].quantile(0.9)*100:.2f}¢  max={first['edge'].max()*100:.2f}¢")

    print(f"\nWhere they occur (time-to-close):")
    print(first.groupby(pd.cut(first['mins_to_close'],
        bins=[-1,1,5,15,30,60,1440])).size())

    print(f"\nWhere they occur (yes_ask band):")
    print(first.groupby(pd.cut(first['yes_ask'],
        bins=[0,0.1,0.3,0.5,0.7,0.9,1.0])).size())

    print(f"\nTotal P&L (assuming filled at observed asks, 1 pair per signal):")
    total_edge = first['edge'].sum()
    print(f"  ${total_edge:.2f} over {span_d:.0f} days = ${total_edge/max(span_d/365,0.001):.2f}/yr")

    return df


if __name__ == '__main__':
    print("=" * 80)
    print("K1: within-market YES+NO cross-arb")
    print("=" * 80)
    df = scan()
