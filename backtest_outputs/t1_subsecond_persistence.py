"""Check T1 arb persistence at sub-second resolution.

Method: for each T1 candidate (from h31_t1_pairs at edge>=1.5c), use the raw
ws_orderbook_top_dedup feed (nanosecond timestamps) to measure how long the
arb persisted before disappearing. Then we know what fraction is capturable
at typical bot latency (200-500ms).
"""
import duckdb

ana = duckdb.connect('backtest_outputs/analysis.duckdb', read_only=False)
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

# Sample a few T1 candidates and trace their nanosecond persistence
print('═' * 80)
print(' Sub-second T1 arb persistence (raw ws feed)')
print('═' * 80)

# Get 10 T1 candidate snapshots
candidates = ana.execute('''
SELECT mkt_lo, mkt_hi, ts_sec, ya_lo, yb_hi, ya_lo_qty, yb_hi_qty, net_edge
FROM h31_t1_pairs WHERE net_edge >= 0.015
ORDER BY ts_sec LIMIT 10
''').df()
print(f'\nSampling {len(candidates)} T1 candidates:\n')

for _, row in candidates.iterrows():
    mkt_lo, mkt_hi = row['mkt_lo'], row['mkt_hi']
    ts_sec = int(row['ts_sec'])
    ya_lo, yb_hi = float(row['ya_lo']), float(row['yb_hi'])

    # Get all raw ticks for both markets within [ts_sec, ts_sec + 5]
    # Find earliest tick where the arb breaks (ya_lo rises OR yb_hi drops)
    breakout = ana.execute(f'''
    WITH lo_ticks AS (
      SELECT received_at_ns, yes_ask, yes_ask_qty FROM src.ws_orderbook_top_dedup
      WHERE market_ticker = '{mkt_lo}'
        AND received_at_ns BETWEEN {ts_sec * 10**9} AND {(ts_sec + 5) * 10**9}
      ORDER BY received_at_ns
    ),
    hi_ticks AS (
      SELECT received_at_ns, yes_bid, yes_bid_qty FROM src.ws_orderbook_top_dedup
      WHERE market_ticker = '{mkt_hi}'
        AND received_at_ns BETWEEN {ts_sec * 10**9} AND {(ts_sec + 5) * 10**9}
      ORDER BY received_at_ns
    ),
    -- Find FIRST tick on EITHER side where the arb breaks
    lo_break AS (
      SELECT MIN(received_at_ns) AS break_ns FROM lo_ticks
      WHERE yes_ask > {ya_lo} + 0.001 OR yes_ask_qty < 1
    ),
    hi_break AS (
      SELECT MIN(received_at_ns) AS break_ns FROM hi_ticks
      WHERE yes_bid < {yb_hi} - 0.001 OR yes_bid_qty < 1
    )
    SELECT
      LEAST(COALESCE((SELECT break_ns FROM lo_break), {(ts_sec + 5) * 10**9}),
            COALESCE((SELECT break_ns FROM hi_break), {(ts_sec + 5) * 10**9})) AS first_break,
      (SELECT COUNT(*) FROM lo_ticks) AS lo_ticks_n,
      (SELECT COUNT(*) FROM hi_ticks) AS hi_ticks_n
    ''').fetchone()

    base_ns = ts_sec * 10**9
    break_ns = breakout[0]
    persistence_ms = (break_ns - base_ns) / 1e6 if break_ns else None
    print(f'  {mkt_lo[-22:]} / {mkt_hi[-22:]}: '
          f'persistence={persistence_ms:>6.1f}ms  '
          f'(lo ticks={breakout[1]}, hi ticks={breakout[2]}, '
          f'edge={row["net_edge"]:.3f})')

print('\n' + '═' * 80)
print(' Persistence statistics across ALL T1 candidates')
print('═' * 80)

ana.execute('''
CREATE OR REPLACE TABLE t1_persistence AS
WITH cand AS (
  SELECT mkt_lo, mkt_hi, ts_sec, ya_lo, yb_hi
  FROM h31_t1_pairs WHERE net_edge >= 0.015
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1)
SELECT e.mkt_lo, e.mkt_hi, e.ts_sec, e.ya_lo, e.yb_hi,
       -- Earliest break = first tick where lo ask rises OR hi bid drops
       (SELECT MIN(t.received_at_ns) FROM src.ws_orderbook_top_dedup t
        WHERE (t.market_ticker = e.mkt_lo AND (t.yes_ask > e.ya_lo + 0.001 OR t.yes_ask_qty < 1))
           OR (t.market_ticker = e.mkt_hi AND (t.yes_bid < e.yb_hi - 0.001 OR t.yes_bid_qty < 1))
          AND t.received_at_ns BETWEEN e.ts_sec * 1000000000 AND (e.ts_sec + 30) * 1000000000
       ) AS first_break_ns
FROM e
''')

print(ana.execute('''
SELECT
  COUNT(*) AS n_candidates,
  COUNT(first_break_ns) AS n_with_break_detected,
  -- Persistence in ms (capped at 30s)
  AVG((first_break_ns - ts_sec * 1000000000) / 1e6) AS avg_persistence_ms,
  MEDIAN((first_break_ns - ts_sec * 1000000000) / 1e6) AS med_persistence_ms,
  PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY (first_break_ns - ts_sec * 1000000000) / 1e6) AS p10_ms,
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY (first_break_ns - ts_sec * 1000000000) / 1e6) AS p25_ms,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY (first_break_ns - ts_sec * 1000000000) / 1e6) AS p75_ms,
  COUNT(*) FILTER(WHERE (first_break_ns - ts_sec * 1000000000) / 1e6 >= 500) AS n_persist_500ms,
  COUNT(*) FILTER(WHERE (first_break_ns - ts_sec * 1000000000) / 1e6 >= 1000) AS n_persist_1s
FROM t1_persistence
''').df().to_string())

ana.close()
