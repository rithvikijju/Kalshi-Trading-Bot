"""V3 Round 3 — Focus on:
  C1. T1 depth analysis (what's the actual ceiling on monotonicity arb?)
  C2. B1 fixed: large order delta signal (bug in Round 2: price filter wrong)
  C3. Maker strategy: post passive limit orders, count fills
  C4. Mining signal_scan_all: what did the live bot reject that won?
  C5. T1 + T2 stacking sanity check

Round 1+2 conclusion: directional trades on Kalshi BTC binaries get crushed
by the fee floor (min 7c). Only structural arb (T1) and convergence (T2/T3)
print. So Round 3 explores how to SCALE the things that DO work.
"""
import duckdb, json, math
from pathlib import Path

ana = duckdb.connect('backtest_outputs/analysis.duckdb', read_only=False)
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

ana.execute('CREATE OR REPLACE MACRO kalshi_fee(p) AS LEAST(0.07, 0.07 * GREATEST(0, LEAST(1, p)) / 0.50)')
ana.execute('''CREATE OR REPLACE MACRO norm_cdf(x) AS (
  WITH a AS (SELECT abs(x) AS ax),
       b AS (SELECT ax, 1.0/(1.0+0.2316419*ax) AS t, exp(-ax*ax/2)/sqrt(2*pi()) AS phi FROM a),
       c AS (SELECT phi * (t*(0.319381530 + t*(-0.356563782 + t*(1.781477937
                          + t*(-1.821255978 + t*1.330274429))))) AS tail FROM b)
  SELECT CASE WHEN x >= 0 THEN 1.0 - tail ELSE tail END FROM c)
''')

RESULTS = []
def _run(name, desc, query):
    try:
        r = ana.execute(query).fetchone()
        n = r[0] or 0
        wins = r[1] or 0
        total = float(r[2] or 0)
        avg_c = float(r[3] or 0)
        sd = float(r[4] or 0) if r[4] else 0.0
        contracts = int(r[5] or 0)
        avg_qty = float(r[6] or 0) if r[6] else 0.0
        win_pct = wins/n*100 if n else 0
        t_stat = (math.sqrt(n) * avg_c / sd) if (sd > 0 and n > 1) else 0.0
        RESULTS.append({'hyp': name, 'desc': desc, 'n': n, 'win_pct': win_pct,
                        'total_pnl': total, 'per_day': total/7, 'avg_c': avg_c,
                        'sd_c': sd, 'contracts': contracts, 'avg_qty': avg_qty,
                        't_stat': t_stat})
        star = '★' if (win_pct >= 60 and total > 5 and t_stat > 2 and n >= 20) else ' '
        sign = '+' if t_stat >= 0 else ''
        print(f'  {star} [{name:22}] n={n:>4} win={win_pct:>5.1f}% '
              f'total=${total:>+8.2f} avg/c=${avg_c:>+.4f} '
              f't={sign}{t_stat:>5.2f} qty={avg_qty:.1f}  {desc[:46]}')
        return RESULTS[-1]
    except Exception as e:
        print(f'  [{name:22}] ERROR: {str(e)[:140]}')
        return None

# ═══════════════════════════════════════════════════════════════════
# C1 — T1 DEPTH ANALYSIS: what's the actual ceiling per pair?
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' C1 — T1 Depth Scaling Analysis')
print('═' * 80)

# Get all T1 pairs at edge >=1.5c, look at the depth distribution
print('  T1 pair depth distribution:')
print(ana.execute('''
SELECT
  MIN(LEAST(ya_lo_qty, yb_hi_qty)) AS min_depth,
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY LEAST(ya_lo_qty, yb_hi_qty)) AS p25,
  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY LEAST(ya_lo_qty, yb_hi_qty)) AS p50,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY LEAST(ya_lo_qty, yb_hi_qty)) AS p75,
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY LEAST(ya_lo_qty, yb_hi_qty)) AS p95,
  MAX(LEAST(ya_lo_qty, yb_hi_qty)) AS max_depth
FROM h31_t1_pairs WHERE net_edge >= 0.015
''').df())

# How does PnL scale with qty cap?
for cap in [5, 10, 20, 50, 100]:
    _run(f'C1 T1 cap={cap:>3}', f'T1 with qty cap = {cap}', f'''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
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
       SUM(pnl_per_pair * qty), AVG(pnl_per_pair), STDDEV(pnl_per_pair),
       SUM(qty), AVG(qty) FROM sett
''')

# Lower threshold to see if more T1 opps exist at smaller edges
print('\n  T1 edge sensitivity (cap=10):')
for thr in [0.005, 0.010, 0.015, 0.025, 0.050]:
    _run(f'C1 T1 thr={thr*100:.1f}c', f'T1 net_edge>={thr*100:.1f}c, cap=10', f'''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= {thr} AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS r_lo, s2.result AS r_hi,
         LEAST(10, e.ya_lo_qty, e.yb_hi_qty) AS qty,
         (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
         (CASE WHEN s2.result='no'  THEN 1.0 ELSE 0.0 END) -
         e.ya_lo - (1.0 - e.yb_hi) - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_hi) AS pnl_per_pair
  FROM e
  JOIN settlements s1 ON s1.market_ticker = e.mkt_lo
  JOIN settlements s2 ON s2.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
       SUM(pnl_per_pair * qty), AVG(pnl_per_pair), STDDEV(pnl_per_pair),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# C2 — B1 FIXED: large delta signal
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' C2 — Large limit-add delta signal (price filter fixed)')
print('═' * 80)

# Rebuild big_delta with CORRECT price range (0..1 not 5..95!)
ana.execute('''
CREATE OR REPLACE TABLE big_delta AS
SELECT received_at_ns, market_ticker, side, price, delta_qty,
       CAST(received_at_ns / 1000000000 AS BIGINT) AS ts_sec
FROM src.ws_orderbook_delta_all
WHERE delta_qty >= 30 AND price BETWEEN 0.05 AND 0.95
''')
n_bd = ana.execute("SELECT COUNT(*) FROM big_delta").fetchone()[0]
print(f'  big_delta rows: {n_bd:,}')

# YES-side add (someone bid up YES) → predicts YES wins?
# Note: positive delta on YES side means more buying interest at that price.
# Aggregate within 5s windows per market.
_run('C2 yes-add-30', 'YES add >=30 contracts → buy YES at next tick', '''
WITH cand AS (
  SELECT b.market_ticker, b.ts_sec, b.delta_qty,
         s.yes_ask, s.yes_ask_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM big_delta b
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = b.market_ticker AND s.ts_sec = b.ts_sec
  WHERE b.side = 'yes' AND b.delta_qty >= 30
    AND s.secs_to_close BETWEEN 600 AND 3300
    AND s.yes_ask BETWEEN 0.15 AND 0.85
    AND s.yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_ask AS fill_ya, f.yes_ask_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_ask_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(4, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('C2 no-add-30', 'NO add >=30 contracts → buy NO at next tick', '''
WITH cand AS (
  SELECT b.market_ticker, b.ts_sec, b.delta_qty,
         s.yes_bid, s.yes_bid_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM big_delta b
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = b.market_ticker AND s.ts_sec = b.ts_sec
  WHERE b.side = 'no' AND b.delta_qty >= 30
    AND s.secs_to_close BETWEEN 600 AND 3300
    AND s.yes_bid BETWEEN 0.15 AND 0.85
    AND s.yes_bid_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_bid AS fill_yb, f.yes_bid_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_bid_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(4, f.fill_qty) AS qty,
         (1.0 - fill_yb) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-fill_yb) - kalshi_fee(1.0-fill_yb)
              ELSE -(1.0-fill_yb) - kalshi_fee(1.0-fill_yb) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# C2 with much larger threshold — only VERY large orders (whale signal)
_run('C2 yes-add-500', 'YES add >=500 contracts (whale) → buy YES', '''
WITH cand AS (
  SELECT b.market_ticker, b.ts_sec, b.delta_qty,
         s.yes_ask, s.yes_ask_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM big_delta b
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = b.market_ticker AND s.ts_sec = b.ts_sec
  WHERE b.side = 'yes' AND b.delta_qty >= 500
    AND s.secs_to_close BETWEEN 600 AND 3300
    AND s.yes_ask BETWEEN 0.10 AND 0.90
    AND s.yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_ask AS fill_ya, f.yes_ask_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_ask_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(4, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('C2 no-add-500', 'NO add >=500 contracts (whale) → buy NO', '''
WITH cand AS (
  SELECT b.market_ticker, b.ts_sec, b.delta_qty,
         s.yes_bid, s.yes_bid_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM big_delta b
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = b.market_ticker AND s.ts_sec = b.ts_sec
  WHERE b.side = 'no' AND b.delta_qty >= 500
    AND s.secs_to_close BETWEEN 600 AND 3300
    AND s.yes_bid BETWEEN 0.10 AND 0.90
    AND s.yes_bid_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_bid AS fill_yb, f.yes_bid_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_bid_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(4, f.fill_qty) AS qty,
         (1.0 - fill_yb) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-fill_yb) - kalshi_fee(1.0-fill_yb)
              ELSE -(1.0-fill_yb) - kalshi_fee(1.0-fill_yb) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# C3 — MAKER STRATEGY: passive limit buy at one-tick-back from inside
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' C3 — Maker: buy YES at yes_bid (don\'t cross spread)')
print('═' * 80)

# Simulate: place limit at yes_bid + 0.01. If next 60s of ticks shows
# yes_bid drops to our price (someone hits our limit), we filled.
# At fill, hold to settlement.
# This is BEST CASE — we assume immediate queue priority.
# Edge: avoid crossing 2c spread = save 2c (vs 7c fee saving — actually
# fee is min(7c, 7% × price), so for low prices fee is small).

# For BS deep-ITM trade: was ask=0.95, our maker bid=0.93. If ask drops to 0.93
# (next tick), we get filled. We saved 2c.
# But — we need to wait at the bid. If price moves AGAINST us (toward $0), our
# limit is left in the dust and we don't trade. If price moves FOR us (toward $1),
# we got filled at the worst time.
# This is "adverse selection".
# Simulate by checking if our limit price was hit within 60s.

_run('C3 maker-yes-itm', 'Maker buy YES at fair_yes-0.02 when fair>=0.92', '''
WITH cand AS (
  -- Place a limit at price floor(yes_ask, fair_yes) - 0.01
  SELECT *, LEAST(yes_ask, fair_yes) - 0.01 AS limit_px
  FROM snap_with_fair
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND fair_yes >= 0.92
    AND yes_ask BETWEEN 0.88 AND 0.97
    AND yes_bid <= LEAST(yes_ask, fair_yes) - 0.01
    AND btc_spot > strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
sig AS (SELECT * FROM first_per_mkt WHERE rn = 1),
-- Within next 60s, did yes_bid (or some sell order) drop to our limit_px?
fill_check AS (
  SELECT sig.*,
         (SELECT MIN(f.yes_ask) FROM strike_snaps_1s_clean f
          WHERE f.market_ticker = sig.market_ticker
            AND f.ts_sec BETWEEN sig.ts_sec + 1 AND sig.ts_sec + 60) AS min_ask_60s
  FROM sig
),
filled AS (
  SELECT * FROM fill_check WHERE min_ask_60s <= limit_px
),
sett AS (
  SELECT f.*, s.result, 3 AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - limit_px - kalshi_fee(limit_px)
              ELSE -limit_px - kalshi_fee(limit_px) END AS pnl_per_c
  FROM filled f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('C3 maker-no-otm', 'Maker buy NO at 0.99-fair_yes-0.02 when fair_yes<=0.08', '''
WITH cand AS (
  SELECT *, LEAST(1.0 - yes_bid, 1.0 - fair_yes) - 0.01 AS limit_no_px
  FROM snap_with_fair
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND fair_yes <= 0.08
    AND yes_bid BETWEEN 0.03 AND 0.12
    AND (1.0 - yes_ask) <= LEAST(1.0 - yes_bid, 1.0 - fair_yes) - 0.01
    AND btc_spot < strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
sig AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill_check AS (
  SELECT sig.*,
         (SELECT MAX(f.yes_bid) FROM strike_snaps_1s_clean f
          WHERE f.market_ticker = sig.market_ticker
            AND f.ts_sec BETWEEN sig.ts_sec + 1 AND sig.ts_sec + 60) AS max_bid_60s
  FROM sig
),
filled AS (
  SELECT * FROM fill_check WHERE (1.0 - max_bid_60s) <= limit_no_px
),
sett AS (
  SELECT f.*, s.result, 3 AS qty,
         CASE WHEN s.result='no' THEN 1.0 - limit_no_px - kalshi_fee(limit_no_px)
              ELSE -limit_no_px - kalshi_fee(limit_no_px) END AS pnl_per_c
  FROM filled f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# C4 — MINING signal_scan_all: what did the live bot consider but skip?
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' C4 — Live bot decision mining')
print('═' * 80)

# Check what's in order_decision_all
print('  order_decision_all schema:')
print(ana.execute('DESCRIBE src.order_decision_all').df())
print('\n  Sample skip reasons:')
print(ana.execute('''
SELECT action, detail, COUNT(*) as n FROM src.order_decision_all
GROUP BY action, detail ORDER BY n DESC LIMIT 20
''').df().to_string())

# For each "skip" with action != 'send', join to settlement and see what
# would have happened if we'd taken it.
_run('C4 missed-skip', 'What if we took every skipped order decision?', '''
WITH cand AS (
  SELECT o.event_ticker, o.market_ticker, o.side, o.entry_price, o.yes_limit_price,
         o.net_edge_cents, o.detail, o.action, o.btc_spot,
         CAST(o.received_at_ns / 1000000000 AS BIGINT) AS ts_sec
  FROM src.order_decision_all o
  WHERE o.action != 'send'
    AND o.market_ticker IS NOT NULL
    AND o.entry_price IS NOT NULL
    AND o.entry_price BETWEEN 0.05 AND 0.95
),
sett AS (
  SELECT c.*, s.result, 3 AS qty,
         CASE WHEN c.side = 'yes' AND s.result='yes'
              THEN 1.0 - c.entry_price - kalshi_fee(c.entry_price)
              WHEN c.side = 'yes' AND s.result='no'
              THEN -c.entry_price - kalshi_fee(c.entry_price)
              WHEN c.side = 'no' AND s.result='no'
              THEN 1.0 - c.entry_price - kalshi_fee(c.entry_price)
              WHEN c.side = 'no' AND s.result='yes'
              THEN -c.entry_price - kalshi_fee(c.entry_price)
         END AS pnl_per_c
  FROM cand c JOIN settlements s ON s.market_ticker = c.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett WHERE pnl_per_c IS NOT NULL
''')

# What did the bot send (action='send')?
_run('C4 actual-sends', 'Live bot actual sends (sanity check)', '''
WITH cand AS (
  SELECT o.event_ticker, o.market_ticker, o.side, o.entry_price,
         CAST(o.received_at_ns / 1000000000 AS BIGINT) AS ts_sec
  FROM src.order_decision_all o
  WHERE o.action = 'send'
    AND o.market_ticker IS NOT NULL
    AND o.entry_price BETWEEN 0.05 AND 0.99
),
sett AS (
  SELECT c.*, s.result, 3 AS qty,
         CASE WHEN c.side = 'yes' AND s.result='yes'
              THEN 1.0 - c.entry_price - kalshi_fee(c.entry_price)
              WHEN c.side = 'yes' AND s.result='no'
              THEN -c.entry_price - kalshi_fee(c.entry_price)
              WHEN c.side = 'no' AND s.result='no'
              THEN 1.0 - c.entry_price - kalshi_fee(c.entry_price)
              WHEN c.side = 'no' AND s.result='yes'
              THEN -c.entry_price - kalshi_fee(c.entry_price)
         END AS pnl_per_c
  FROM cand c JOIN settlements s ON s.market_ticker = c.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett WHERE pnl_per_c IS NOT NULL
''')

# ═══════════════════════════════════════════════════════════════════
# C5 — T1 + T2 + T3 STACKING (would they conflict on capital?)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' C5 — Combined T1+T2 portfolio sanity check')
print('═' * 80)

# T1: edge>=1.5c, cap=10
# T2: existing filters
# T3: existing filters
# Add them all up — assume infinite capital (capital constraint is realistic
# but for upper bound)

# T2 baseline
_run('C5 T2-baseline', 'T2: fair>=0.94, price 0.88-0.97, TTC 30-60min, dist>=200', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND fair_yes >= 0.94
    AND yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(btc_spot - strike) >= 200
    AND yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_ask AS fill_ya, f.yes_ask_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_ask_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(10, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# T3 baseline (from prior winning hypothesis report)
_run('C5 T3-baseline', 'T3: yes_bid 0.28-0.40, strike-spot>=$100, 5-min OTM persistence', '''
WITH base AS (
  SELECT s.*,
         LAG(s.btc_spot, 60)  OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_60s,
         LAG(s.btc_spot, 180) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_180s,
         LAG(s.btc_spot, 300) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_300s
  FROM strike_snaps_1s_clean s
  WHERE s.yes_bid BETWEEN 0.28 AND 0.40
    AND s.strike > s.btc_spot + 100
    AND s.secs_to_close BETWEEN 600 AND 1800
    AND s.yes_bid_qty >= 1
),
persisted AS (
  SELECT * FROM base
  WHERE spot_60s < strike AND spot_180s < strike AND spot_300s < strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM persisted
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_bid AS fill_yb, f.yes_bid_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_bid_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(2, f.fill_qty) AS qty,
         (1.0 - fill_yb) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-fill_yb) - kalshi_fee(1.0-fill_yb)
              ELSE -(1.0-fill_yb) - kalshi_fee(1.0-fill_yb) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 100)
print(' ROUND 3 SUMMARY — sorted by total PnL (★ = win≥60%, total>$5, t>2, n≥20)')
print('═' * 100)
print(f'{"hyp":<24} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"t":>7} {"qty":>5}  desc')
print('─' * 110)

for r in sorted(RESULTS, key=lambda x: x.get('total_pnl', 0), reverse=True):
    star = '★' if (r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20) else ' '
    sign = '+' if r['t_stat'] >= 0 else ''
    print(f'{star} {r["hyp"]:<22} {r["n"]:>5} {r["win_pct"]:>5.1f}% {r["total_pnl"]:>+9.2f} '
          f'{r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f} {sign}{r["t_stat"]:>5.2f} '
          f'{r["avg_qty"]:>5.1f}  {r["desc"][:42]}')

winners = [r for r in RESULTS if r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies passed criteria')

Path('backtest_outputs/v3/round3_results.json').write_text(json.dumps(RESULTS, indent=2))
ana.close()
