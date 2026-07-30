"""Validate: T1 depth scaling at cap=20/50 is real, not regime-specific.

Tests:
  1. Split temporally (days 1-4 vs days 5-7) — does cap=20 hold in both?
  2. Per-trade depth distribution: how many ACTUAL trades would fill at cap=50?
  3. Realistic fill model: if both legs need fill within 1s, what % fill atomically?
  4. Worst-case scenario: only one leg fills, you're stuck with naked position.
"""
import duckdb, math, json
from pathlib import Path

ana = duckdb.connect('backtest_outputs/analysis.duckdb', read_only=False)
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

ana.execute('CREATE OR REPLACE MACRO kalshi_fee(p) AS LEAST(0.07, 0.07 * GREATEST(0, LEAST(1, p)) / 0.50)')

print('═' * 80)
print(' Test 1 — Temporal split (in-sample vs out-of-sample)')
print('═' * 80)

# Min/max ts_sec in t1_pairs
mm = ana.execute('SELECT MIN(ts_sec), MAX(ts_sec) FROM h31_t1_pairs').fetchone()
print(f'  T1 pair ts_sec range: {mm[0]} to {mm[1]}')
midpoint = (mm[0] + mm[1]) // 2
print(f'  Split at: {midpoint}')

for cap in [5, 20, 50]:
    print(f'\n  cap={cap}:')
    for label, where in [('Days 1-3.5', f'AND ts_sec < {midpoint}'),
                          ('Days 3.5-7', f'AND ts_sec >= {midpoint}')]:
        r = ana.execute(f'''
        WITH cand AS (
          SELECT * FROM h31_t1_pairs
          WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
          {where}
        ),
        first_per_pair AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
        ),
        e AS (SELECT * FROM first_per_pair WHERE rn = 1),
        sett AS (
          SELECT e.*, s1.result AS r_lo, s2.result AS r_hi,
                 LEAST({cap}, e.ya_lo_qty, e.yb_hi_qty) AS qty,
                 (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
                 (CASE WHEN s2.result='no'  THEN 1.0 ELSE 0.0 END) -
                 e.ya_lo - (1.0 - e.yb_hi) - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_hi) AS pnl_per_pair
          FROM e
          JOIN settlements s1 ON s1.market_ticker = e.mkt_lo
          JOIN settlements s2 ON s2.market_ticker = e.mkt_hi
        )
        SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
               SUM(pnl_per_pair * qty), AVG(pnl_per_pair), AVG(qty) FROM sett
        ''').fetchone()
        n, wins, total, avg_pnl, avg_qty = r
        print(f'    {label}: n={n} wins={wins} total=${total:.2f} avg_pnl_per_pair=${avg_pnl:.4f} avg_qty={avg_qty:.1f}')

print('\n' + '═' * 80)
print(' Test 2 — Per-trade depth distribution at cap=20/50')
print('═' * 80)

print(ana.execute('''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1)
SELECT
  -- Fraction of trades where depth supports specified cap
  COUNT(*) AS total_trades,
  COUNT(*) FILTER(WHERE LEAST(ya_lo_qty, yb_hi_qty) >= 5) AS depth_5plus,
  COUNT(*) FILTER(WHERE LEAST(ya_lo_qty, yb_hi_qty) >= 10) AS depth_10plus,
  COUNT(*) FILTER(WHERE LEAST(ya_lo_qty, yb_hi_qty) >= 20) AS depth_20plus,
  COUNT(*) FILTER(WHERE LEAST(ya_lo_qty, yb_hi_qty) >= 50) AS depth_50plus,
  AVG(LEAST(ya_lo_qty, yb_hi_qty)) AS avg_depth,
  MEDIAN(LEAST(ya_lo_qty, yb_hi_qty)) AS med_depth
FROM e
''').df())

print('\n' + '═' * 80)
print(' Test 3 — Realistic fill simulation: does the arb persist for ≥1 second?')
print('═' * 80)

# For each T1 candidate, check: at t+1s, do BOTH legs still exist with adequate depth?
# This tests whether an "atomic" execution is plausible.
print(ana.execute('''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
forward AS (
  SELECT e.*,
         lo1.yes_ask AS lo_ya_1s, lo1.yes_ask_qty AS lo_ya_1s_qty,
         hi1.yes_bid AS hi_yb_1s, hi1.yes_bid_qty AS hi_yb_1s_qty
  FROM e
  LEFT JOIN strike_snaps_1s_clean lo1
    ON lo1.market_ticker = e.mkt_lo AND lo1.ts_sec = e.ts_sec + 1
  LEFT JOIN strike_snaps_1s_clean hi1
    ON hi1.market_ticker = e.mkt_hi AND hi1.ts_sec = e.ts_sec + 1
)
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER(WHERE lo_ya_1s IS NOT NULL AND hi_yb_1s IS NOT NULL) AS both_present_1s,
  COUNT(*) FILTER(WHERE lo_ya_1s <= ya_lo + 0.01 AND hi_yb_1s >= yb_hi - 0.01
                   AND lo_ya_1s_qty >= 1 AND hi_yb_1s_qty >= 1) AS both_fillable_1s,
  COUNT(*) FILTER(WHERE lo_ya_1s <= ya_lo + 0.01 AND hi_yb_1s >= yb_hi - 0.01
                   AND lo_ya_1s_qty >= 10 AND hi_yb_1s_qty >= 10) AS both_fillable_10qty,
  COUNT(*) FILTER(WHERE lo_ya_1s <= ya_lo + 0.01 AND hi_yb_1s >= yb_hi - 0.01
                   AND lo_ya_1s_qty >= 20 AND hi_yb_1s_qty >= 20) AS both_fillable_20qty,
  COUNT(*) FILTER(WHERE lo_ya_1s <= ya_lo + 0.01 AND hi_yb_1s >= yb_hi - 0.01
                   AND lo_ya_1s_qty >= 50 AND hi_yb_1s_qty >= 50) AS both_fillable_50qty
FROM forward
''').df().to_string())

# Same but with realistic fill model: only count trades where both legs ACTUALLY had ≥cap qty 1s later
print('\n  Realistic-fill PnL (require both legs ≥cap qty at t+1s):')
for cap in [5, 10, 20, 50]:
    r = ana.execute(f'''
    WITH cand AS (
      SELECT * FROM h31_t1_pairs
      WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
    ),
    first_per_pair AS (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
    ),
    e AS (SELECT * FROM first_per_pair WHERE rn = 1),
    forward AS (
      SELECT e.*,
             lo1.yes_ask AS lo_ya_1s, lo1.yes_ask_qty AS lo_ya_1s_qty,
             hi1.yes_bid AS hi_yb_1s, hi1.yes_bid_qty AS hi_yb_1s_qty
      FROM e
      JOIN strike_snaps_1s_clean lo1
        ON lo1.market_ticker = e.mkt_lo AND lo1.ts_sec = e.ts_sec + 1
      JOIN strike_snaps_1s_clean hi1
        ON hi1.market_ticker = e.mkt_hi AND hi1.ts_sec = e.ts_sec + 1
    ),
    fillable AS (
      SELECT * FROM forward
      WHERE lo_ya_1s <= ya_lo + 0.01 AND hi_yb_1s >= yb_hi - 0.01
        AND lo_ya_1s_qty >= 1 AND hi_yb_1s_qty >= 1
    ),
    sett AS (
      SELECT f.*, s1.result AS r_lo, s2.result AS r_hi,
             LEAST({cap}, f.lo_ya_1s_qty, f.hi_yb_1s_qty) AS qty,
             (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
             (CASE WHEN s2.result='no'  THEN 1.0 ELSE 0.0 END) -
             f.lo_ya_1s - (1.0 - f.hi_yb_1s) - kalshi_fee(f.lo_ya_1s) - kalshi_fee(1.0 - f.hi_yb_1s) AS pnl_per_pair
      FROM fillable f
      JOIN settlements s1 ON s1.market_ticker = f.mkt_lo
      JOIN settlements s2 ON s2.market_ticker = f.mkt_hi
    )
    SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
           SUM(pnl_per_pair * qty), AVG(pnl_per_pair), AVG(qty) FROM sett
    ''').fetchone()
    n, wins, total, avg_pnl, avg_qty = r
    win_pct = wins/n*100 if n else 0
    print(f'    cap={cap:>3}: n={n} win={win_pct:.0f}% total=${total:.2f} avg_pair=${avg_pnl:.4f} avg_qty={avg_qty:.1f}')

print('\n' + '═' * 80)
print(' Test 4 — One-leg-only fill catastrophe estimate')
print('═' * 80)

# If only one leg fills, you have a naked position. Estimate the worst-case
# loss if the OTHER leg's quote vanished. Worst case: you're long YES at ya_lo,
# and market settles NO → loss = ya_lo + fee. Or vice versa.
print(ana.execute('''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1)
SELECT
  -- Average cost-at-risk per leg
  AVG(ya_lo) AS avg_lo_ask,
  AVG(1.0 - yb_hi) AS avg_hi_no_price,
  AVG(GREATEST(ya_lo, 1.0 - yb_hi)) AS avg_worst_leg
FROM e
''').df().to_string())

ana.close()
