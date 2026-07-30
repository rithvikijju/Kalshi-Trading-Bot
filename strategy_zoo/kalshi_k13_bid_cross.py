"""K13: yes_bid + no_bid > 1.0 means you can SELL both sides for more than you owe.

Sell YES @ yes_bid (collect yes_bid, owe $1 if YES wins)
Sell NO  @ no_bid  (collect no_bid,  owe $1 if NO  wins)
Exactly one wins → net = yes_bid + no_bid - 1 - 2 fees.

If yes_bid + no_bid - 1 > fee_floor, that's risk-free.
"""
from __future__ import annotations
import math, duckdb, pandas as pd

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def main():
    db = duckdb.connect(DB, read_only=True)
    # We only have yes_bid_close (top yes bid) and we'd need no_bid.
    # research view has no_bid? let's check.
    cols = db.execute("DESCRIBE v_research_universe").df()['column_name'].tolist()
    print("Available bid/ask cols:")
    for c in cols:
        if 'bid' in c.lower() or 'ask' in c.lower(): print(f"  {c}")
    print()

    # No direct no_bid → approximate as 1 - yes_ask (when MMs are tight, the equivalence holds)
    # That's NOT k13 directly; the real K13 requires actual no_bid from L1 book.
    # Check live capture DB instead.
    print("Schema check on live_capture DB:")
    db.close()

    live = duckdb.connect('/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb', read_only=True)
    cols = live.execute("DESCRIBE ws_orderbook_top_dedup").df()['column_name'].tolist()
    print("Live capture cols:")
    for c in cols:
        if 'bid' in c.lower() or 'ask' in c.lower(): print(f"  {c}")
    print()

    n_total = live.execute("SELECT COUNT(*) FROM ws_orderbook_top_dedup").fetchone()[0]
    print(f"Total dedup rows in live capture: {n_total:,}")

    # Now the K13 scan
    df = live.execute("""
        SELECT received_at_utc::TIMESTAMP AS ts,
               event_ticker, market_ticker,
               yes_bid, no_bid, yes_ask, no_ask,
               yes_bid_qty, no_bid_qty
        FROM ws_orderbook_top_dedup
        WHERE yes_bid IS NOT NULL AND no_bid IS NOT NULL
          AND yes_bid > 0 AND no_bid > 0
          AND yes_bid + no_bid > 1.00
    """).df()
    live.close()

    print(f"\nQuotes with yes_bid + no_bid > 1.00: {len(df):,}")
    if len(df) == 0:
        print("None — bid-side never crosses. Done.")
        return

    df['gap'] = df['yes_bid'] + df['no_bid'] - 1.0
    df['fee_yes'] = df['yes_bid'].apply(kalshi_fee)
    df['fee_no'] = df['no_bid'].apply(kalshi_fee)
    df['edge'] = df['gap'] - df['fee_yes'] - df['fee_no']

    print(f"  gap > 0:    {(df['gap']>0).sum():,}  (these are gross-positive)")
    print(f"  gap > 0.05: {(df['gap']>0.05).sum():,}")
    print(f"  gap > 0.10: {(df['gap']>0.10).sum():,}")
    print(f"  gap > 0.20: {(df['gap']>0.20).sum():,}")
    print()
    print(f"  edge > 0 (after fees): {(df['edge']>0).sum():,}")
    print(f"  edge > 1¢:             {(df['edge']>0.01).sum():,}")
    print(f"  edge > 2¢:             {(df['edge']>0.02).sum():,}")
    print(f"  edge > 5¢:             {(df['edge']>0.05).sum():,}")
    print()
    if (df['edge'] > 0).sum() > 0:
        good = df[df['edge'] > 0].sort_values('ts')
        first = good.drop_duplicates('market_ticker', keep='first')
        span = (first['ts'].max() - first['ts'].min()).total_seconds() / 86400
        print(f"Dedup to first-per-market: {len(first)} unique mkts over {span:.1f}d "
              f"= {len(first)/max(span,0.1):.1f}/day")
        print(f"Edge stats: mean={first['edge'].mean()*100:.2f}¢  median={first['edge'].median()*100:.2f}¢  "
              f"p90={first['edge'].quantile(0.9)*100:.2f}¢  max={first['edge'].max()*100:.2f}¢")
        print(f"Total edge if all filled: ${first['edge'].sum():.2f} → ${first['edge'].sum()/max(span,1):.2f}/day")
        print()
        print("Sample of top 20:")
        cols = ['ts','market_ticker','yes_bid','no_bid','gap','edge','yes_bid_qty','no_bid_qty']
        print(first.sort_values('edge', ascending=False)[cols].head(20).to_string(index=False))


if __name__ == '__main__':
    print("=" * 80)
    print("K13: yes_bid + no_bid > 1.0 (cross-side bid arb)")
    print("=" * 80)
    main()
