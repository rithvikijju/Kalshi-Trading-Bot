"""20 more complex hypotheses backtested against the captured tick data.

Uses backtest_outputs/analysis.duckdb (built from live_capture_gapless_20260512_paused.duckdb).
Plus live tick data from output/arb_strategy_ticks.db if there's enough.
"""
import duckdb
import os

ana = duckdb.connect('backtest_outputs/analysis.duckdb')
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

# Set up math helpers
ana.execute('CREATE OR REPLACE MACRO kalshi_fee(p) AS LEAST(0.07, 0.07 * GREATEST(0, LEAST(1, p)) / 0.50)')
ana.execute('''
CREATE OR REPLACE MACRO norm_cdf(x) AS (
  WITH a AS (SELECT abs(x) AS ax),
       b AS (SELECT ax, 1.0/(1.0+0.2316419*ax) AS t, exp(-ax*ax/2)/sqrt(2*pi()) AS phi FROM a),
       c AS (SELECT phi * (t*(0.319381530 + t*(-0.356563782 + t*(1.781477937
                          + t*(-1.821255978 + t*1.330274429))))) AS tail FROM b)
  SELECT CASE WHEN x >= 0 THEN 1.0 - tail ELSE tail END FROM c)
''')

RESULTS = []

def run(name, query, description=''):
    """Run a backtest, append to RESULTS."""
    try:
        r = ana.execute(query).fetchone()
        if r is None or r[0] is None or r[0] == 0:
            RESULTS.append({'hyp': name, 'desc': description, 'n': 0, 'win_pct': 0,
                            'total': 0.0, 'per_day': 0.0, 'avg_pnl_c': 0.0,
                            'sharpe': 0.0, 'contracts': 0})
            print(f'  [{name}] n=0  (no qualifying trades)  — {description}')
            return
        n = r[0] or 0
        wins = r[1] or 0
        total_pnl = float(r[2]) if r[2] is not None else 0.0
        avg_pnl = float(r[3]) if r[3] is not None else 0.0
        contracts = int(r[4]) if r[4] is not None else 0
        stddev = float(r[5]) if r[5] is not None else 0.0
        win_pct = wins / n * 100 if n else 0
        sharpe = (avg_pnl / stddev) if stddev > 0 else 0
        per_day = total_pnl / 7
        RESULTS.append({'hyp': name, 'desc': description, 'n': n, 'win_pct': win_pct,
                        'total': total_pnl, 'per_day': per_day, 'avg_pnl_c': avg_pnl,
                        'sharpe': sharpe, 'contracts': contracts})
        print(f'  [{name}] n={n:>4} win={win_pct:>5.1f}% total=${total_pnl:>+7.2f} '
              f'avg=${avg_pnl:>+.4f}  ({description})')
    except Exception as e:
        print(f'  [{name}] ERROR: {str(e)[:120]}')

def t1_query():
    """Tier 1 baseline: monotonicity arb."""
    return '''
    WITH ranked AS (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, strike_lo, strike_hi ORDER BY ts_sec) AS rn
      FROM mono_violations
    ),
    first_only AS (SELECT * FROM ranked WHERE rn = 1),
    pnl AS (
      SELECT v.*,
             LEAST(qty_lo, qty_hi, 5) AS qty,
             (CASE WHEN s_lo.result = 'yes' THEN 1.0 ELSE 0.0 END
              - yes_ask_lo - kalshi_fee(yes_ask_lo)
              + CASE WHEN s_hi.result = 'no' THEN 1.0 ELSE 0.0 END
              - (1.0 - yes_bid_hi) - kalshi_fee(1.0 - yes_bid_hi)) AS pnl_pair
      FROM first_only v
      JOIN settlements s_lo ON s_lo.market_ticker = v.mkt_lo
      JOIN settlements s_hi ON s_hi.market_ticker = v.mkt_hi
    )
    SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_pair > 0),
           SUM(pnl_pair * qty), AVG(pnl_pair), SUM(qty), STDDEV(pnl_pair * qty)
    FROM pnl
    '''

def t2_query(extra_filter='', use_t2_filters=True, side='both', max_qty=5):
    """Tier 2 deep-ITM convergence with customizable filters."""
    base_t2 = '''price >= 0.88 AND fair >= 0.94 AND edge > 0.005''' if use_t2_filters else 'TRUE'
    side_filter = ''
    if side == 'yes':
        side_filter = "AND side = 'yes'"
    elif side == 'no':
        side_filter = "AND side = 'no'"
    return f'''
    WITH first_per_market AS (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, side ORDER BY ts_sec) AS rn
      FROM h3_signals
      WHERE {base_t2}
        AND secs_to_close BETWEEN 60 AND 3600
        {side_filter}
        {extra_filter}
    ),
    entries AS (SELECT * FROM first_per_market WHERE rn = 1),
    pnl AS (
      SELECT e.*, s.result, LEAST(qty_avail, {max_qty}) AS qty,
             CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
                  WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
                  WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
                  WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
             END AS pnl_per_c
      FROM entries e JOIN settlements s ON s.market_ticker = e.market_ticker
    )
    SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
           SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty)
    FROM pnl
    '''

# =======================================================================
# BASELINES (for reference)
# =======================================================================
print('═' * 75)
print(' BASELINES')
print('═' * 75)
run('B1 Tier1', t1_query(), 'monotonicity arb (known: $27)')
run('B2 Tier2', t2_query(), 'deep-ITM convergence (known: $16.68)')

# =======================================================================
# GROUP A — Static arbitrage variants
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP A — Structural arb variants')
print('═' * 75)

# H1: Yes_bid + No_bid > 1.0 — sell both at bid for guaranteed cash
ana.execute('''CREATE OR REPLACE TABLE h_bidsum AS
SELECT s.*, (s.yes_bid + (1.0 - s.yes_ask)) AS bid_sum_proxy,
       s.yes_bid AS yes_bid_used, (1.0 - s.yes_ask) AS no_bid_used
FROM snaps s
WHERE s.yes_bid IS NOT NULL AND s.yes_ask IS NOT NULL
  AND (s.yes_bid + (1.0 - s.yes_ask)) > 1.01
  AND s.secs_to_close BETWEEN 60 AND 3500''')
n = ana.execute('SELECT COUNT(*) FROM h_bidsum').fetchone()[0]
print(f'  [H1 bidsum>1.01] {n} ticks where yes_bid + (1-yes_ask) > 1.01 — would be cross-spread arb')

# H2: Tighter monotonicity — min edge 1.5c (filter out marginal trades)
run('H2 mono+', '''
WITH viol AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, strike_lo, strike_hi ORDER BY ts_sec) AS rn
  FROM mono_violations WHERE net_edge > 0.015
),
f AS (SELECT * FROM viol WHERE rn = 1),
pnl AS (
  SELECT v.*, LEAST(qty_lo, qty_hi, 5) AS qty,
         (CASE WHEN s_lo.result='yes' THEN 1.0 ELSE 0.0 END
          - yes_ask_lo - kalshi_fee(yes_ask_lo)
          + CASE WHEN s_hi.result='no' THEN 1.0 ELSE 0.0 END
          - (1.0 - yes_bid_hi) - kalshi_fee(1.0 - yes_bid_hi)) AS pnl_pair
  FROM f v JOIN settlements s_lo ON s_lo.market_ticker = v.mkt_lo
           JOIN settlements s_hi ON s_hi.market_ticker = v.mkt_hi)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_pair > 0),
       SUM(pnl_pair * qty), AVG(pnl_pair), SUM(qty), STDDEV(pnl_pair * qty) FROM pnl
''', 'tier 1 with min_edge >= 1.5c')

# H3: 3-strike butterfly imbalance — if yes_bid(K_mid) is significantly above 0.5*(yes_ask(K_lo) + yes_ask(K_hi))
# This is the "K_mid is overpriced" arb. Sell K_mid, buy K_lo and K_hi at half-quantity.
print('  [H3 butterfly] checking 3-strike convexity violations...')
ana.execute('''
CREATE OR REPLACE TABLE h_butterfly AS
SELECT
  a.event_ticker, a.ts_sec,
  a.market_ticker AS mkt_lo, b.market_ticker AS mkt_mid, c.market_ticker AS mkt_hi,
  a.strike AS K_lo, b.strike AS K_mid, c.strike AS K_hi,
  a.yes_ask AS ask_lo, b.yes_bid AS bid_mid, c.yes_ask AS ask_hi,
  b.yes_bid - 0.5 * (a.yes_ask + c.yes_ask) AS conv_violation
FROM strike_snaps_1s_clean a
JOIN strike_snaps_1s_clean b USING (event_ticker, ts_sec)
JOIN strike_snaps_1s_clean c USING (event_ticker, ts_sec)
WHERE b.strike > a.strike AND c.strike > b.strike
  AND b.strike - a.strike = c.strike - b.strike
  AND b.yes_bid > 0.5 * (a.yes_ask + c.yes_ask) + 0.03  -- 3c above midpoint
  AND a.yes_ask BETWEEN 0.05 AND 0.95
  AND c.yes_ask BETWEEN 0.05 AND 0.95
  AND b.yes_bid BETWEEN 0.05 AND 0.95
  AND a.secs_to_close BETWEEN 60 AND 3500
''')
n_butterfly = ana.execute('SELECT COUNT(*) FROM h_butterfly').fetchone()[0]
print(f'  [H3 butterfly] {n_butterfly} convexity-violating triples found')

# =======================================================================
# GROUP B — Time-window splits
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP B — Time-window splits')
print('═' * 75)
run('H4 ttc<5m',   t2_query('AND secs_to_close < 300'),               'TTC < 5 min')
run('H5 5-15m',    t2_query('AND secs_to_close BETWEEN 300 AND 900'), 'TTC 5–15 min')
run('H6 15-30m',   t2_query('AND secs_to_close BETWEEN 900 AND 1800'),'TTC 15–30 min')
run('H7 30-60m',   t2_query('AND secs_to_close BETWEEN 1800 AND 3600'),'TTC 30–60 min')

# =======================================================================
# GROUP C — Distance / sigma filters
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP C — Distance / σ filters')
print('═' * 75)

# Need to compute distance and sig_rem in the signal table
ana.execute('''CREATE OR REPLACE VIEW h3_signals_aug AS
SELECT s.*,
       es.sigma_annual,
       s.btc_spot * es.sigma_annual / SQRT(365.25 * 24 * 3600) * SQRT(s.secs_to_close) AS sig_rem,
       ABS(s.btc_spot - s.strike) AS dist_dollar,
       ABS(s.btc_spot - s.strike) / (s.btc_spot * es.sigma_annual / SQRT(365.25 * 24 * 3600) * SQRT(s.secs_to_close)) AS dist_sigma
FROM h3_signals s JOIN event_sigma es USING (event_ticker)
WHERE s.btc_spot > 0''')

def t2_with_dist(extra, max_qty=5):
    return f'''
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, side ORDER BY ts_sec) AS rn
  FROM h3_signals_aug WHERE price >= 0.88 AND fair >= 0.94 AND edge > 0.005
    AND secs_to_close BETWEEN 60 AND 3600 {extra}
),
e AS (SELECT * FROM ranked WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, {max_qty}) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c
      FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty)
FROM p
'''

run('H8 d>=1.5σ',  t2_with_dist('AND dist_sigma >= 1.5'),  'distance ≥ 1.5σ_rem')
run('H9 d>=2.0σ',  t2_with_dist('AND dist_sigma >= 2.0'),  'distance ≥ 2.0σ_rem')
run('H10 d>=$500', t2_with_dist('AND dist_dollar >= 500'), 'distance ≥ $500')
run('H11 d>=$1000',t2_with_dist('AND dist_dollar >= 1000'),'distance ≥ $1000')

# =======================================================================
# GROUP D — Side variants
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP D — Side variants')
print('═' * 75)
run('H12 YES only', t2_query(side='yes'), 'YES side only')
run('H13 NO only',  t2_query(side='no'),  'NO side only')
run('H14 YES+d$500', t2_with_dist("AND side='yes' AND dist_dollar >= 500"), 'YES + distance ≥ $500')
run('H15 NO+d$1000', t2_with_dist("AND side='no' AND dist_dollar >= 1000"), 'NO + distance ≥ $1000')

# =======================================================================
# GROUP E — Price/fair tightness
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP E — Tighter price/fair filters')
print('═' * 75)
# Override the entire query for tighter price/fair
run('H16 p>=0.92', '''
WITH r AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, side ORDER BY ts_sec) AS rn
  FROM h3_signals WHERE price >= 0.92 AND fair >= 0.96 AND edge > 0.005
    AND secs_to_close BETWEEN 60 AND 3600),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'price ≥ 0.92, fair ≥ 0.96')

run('H17 p>=0.95', '''
WITH r AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, side ORDER BY ts_sec) AS rn
  FROM h3_signals WHERE price >= 0.95 AND fair >= 0.98 AND edge > 0.005
    AND secs_to_close BETWEEN 60 AND 3600),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'price ≥ 0.95, fair ≥ 0.98')

# H18 edge >= 2c
run('H18 edge>=2c', '''
WITH r AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, side ORDER BY ts_sec) AS rn
  FROM h3_signals WHERE price >= 0.88 AND fair >= 0.94 AND edge >= 0.02
    AND secs_to_close BETWEEN 60 AND 3600),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'min edge ≥ 2¢')

# =======================================================================
# GROUP F — Microstructure (spread, recent trend)
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP F — Microstructure-conditioned')
print('═' * 75)

# Augment h3 signals with spread and recent spot velocity
# Build a per-event spot velocity table: how much did spot move in the last 60s?
ana.execute('''CREATE OR REPLACE TABLE spot_velocity AS
SELECT
  ec.event_ticker,
  CAST(c.received_at_ns / 1e9 AS BIGINT) AS ts_sec,
  c.price AS spot,
  c.price - LAG(c.price, 30) OVER (PARTITION BY ec.event_ticker ORDER BY c.received_at_ns) AS move_30,
  c.price - LAG(c.price, 60) OVER (PARTITION BY ec.event_ticker ORDER BY c.received_at_ns) AS move_60
FROM event_close ec
JOIN src.coinbase_ticker_all c
  ON c.received_at_ns/1e9 BETWEEN ec.close_ts_unix - 3700 AND ec.close_ts_unix''')

ana.execute('''CREATE INDEX IF NOT EXISTS idx_sv_ev_ts ON spot_velocity(event_ticker, ts_sec)''')

# Join velocity onto h3_signals (nearest spot tick)
ana.execute('''CREATE OR REPLACE VIEW h3_signals_with_velocity AS
SELECT s.*,
       (SELECT ABS(v.move_60) FROM spot_velocity v
        WHERE v.event_ticker = s.event_ticker AND v.ts_sec <= s.ts_sec
        ORDER BY v.ts_sec DESC LIMIT 1) AS abs_move_60s
FROM h3_signals s''')

run('H19 calm', '''
WITH r AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, side ORDER BY ts_sec) AS rn
  FROM h3_signals_with_velocity
  WHERE price >= 0.88 AND fair >= 0.94 AND edge > 0.005
    AND secs_to_close BETWEEN 60 AND 3600
    AND abs_move_60s < 50),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'calm market (|60s move| < $50)')

run('H20 spread<=2c', '''
WITH r AS (
  SELECT s.*, t.yes_ask - t.yes_bid AS spread,
         ROW_NUMBER() OVER (PARTITION BY s.market_ticker, s.side ORDER BY s.ts_sec) AS rn
  FROM h3_signals s
  JOIN strike_snaps_1s_clean t USING (event_ticker, market_ticker, ts_sec)
  WHERE s.price >= 0.88 AND s.fair >= 0.94 AND s.edge > 0.005
    AND s.secs_to_close BETWEEN 60 AND 3600
    AND t.yes_ask - t.yes_bid <= 0.02),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'spread ≤ 2¢ at entry')

# =======================================================================
# GROUP G — Bonus: butterfly PnL + multi-filter sweet spot + combined
# =======================================================================
print('\n' + '═' * 75)
print(' GROUP G — Butterfly + combinations')
print('═' * 75)

# Butterfly is NOT pure arb but the 127K "violations" might still be profitable
# bets if market consistently overprices the middle strike.
# Trade: BUY 1×K_lo YES + BUY 1×K_hi YES - SELL 2×K_mid YES
# Payoff: +$1 if K_lo ≤ settle < K_mid, -$1 if K_mid ≤ settle < K_hi, 0 otherwise
run('H21 butterfly', '''
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, K_lo, K_mid, K_hi ORDER BY ts_sec) AS rn
  FROM h_butterfly
),
first AS (SELECT * FROM ranked WHERE rn = 1),
pnl_calc AS (
  SELECT f.*, ess.settle_spot,
    -- payoff at settle:
    --   K_lo <= spot < K_mid → +1
    --   K_mid <= spot < K_hi → -1
    --   else 0
    CASE
      WHEN ess.settle_spot IS NULL THEN NULL
      WHEN ess.settle_spot >= K_lo AND ess.settle_spot < K_mid THEN 1.0
      WHEN ess.settle_spot >= K_mid AND ess.settle_spot < K_hi THEN -1.0
      ELSE 0.0
    END AS payoff,
    -- cost: ask_lo + ask_hi - 2*bid_mid + fees (3 legs)
    (ask_lo + ask_hi - 2 * bid_mid
     + kalshi_fee(ask_lo) + kalshi_fee(ask_hi) + 2 * kalshi_fee(1 - bid_mid)) AS cost
  FROM first f
  JOIN event_settlement_spot ess ON ess.event_ticker = f.event_ticker
)
SELECT
  COUNT(*) FILTER (WHERE payoff IS NOT NULL),
  COUNT(*) FILTER (WHERE payoff IS NOT NULL AND (payoff - cost) > 0),
  SUM(payoff - cost),
  AVG(payoff - cost),
  COUNT(*) FILTER (WHERE payoff IS NOT NULL),
  STDDEV(payoff - cost)
FROM pnl_calc
WHERE payoff IS NOT NULL
''', '3-strike butterfly (BUY lo+hi, SELL 2×mid)')

# H22: Best Tier 2 combo: spread<=2c AND edge>=1c AND ttc 30-60min
run('H22 T2-best', '''
WITH r AS (
  SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.market_ticker, s.side ORDER BY s.ts_sec) AS rn
  FROM h3_signals s
  JOIN strike_snaps_1s_clean t USING (event_ticker, market_ticker, ts_sec)
  WHERE s.price >= 0.88 AND s.fair >= 0.94 AND s.edge >= 0.01
    AND s.secs_to_close BETWEEN 1800 AND 3600
    AND t.yes_ask - t.yes_bid <= 0.02),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'T2: edge≥1c AND ttc 30-60m AND spread≤2c')

# H23: Even tighter — edge >= 1.5c, plus tighter window
run('H23 T2-elite', '''
WITH r AS (
  SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.market_ticker, s.side ORDER BY s.ts_sec) AS rn
  FROM h3_signals s
  JOIN strike_snaps_1s_clean t USING (event_ticker, market_ticker, ts_sec)
  WHERE s.price >= 0.88 AND s.fair >= 0.95 AND s.edge >= 0.015
    AND s.secs_to_close BETWEEN 1800 AND 3600
    AND t.yes_ask - t.yes_bid <= 0.02),
e AS (SELECT * FROM r WHERE rn = 1),
p AS (SELECT e.*, s.result, LEAST(qty_avail, 5) AS qty,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c FROM e JOIN settlements s ON s.market_ticker = e.market_ticker)
SELECT COUNT(*), COUNT(*) FILTER (WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), SUM(qty), STDDEV(pnl_per_c * qty) FROM p
''', 'T2: edge≥1.5c, fair≥.95, ttc 30-60m, spread≤2c')

# H24: Combined Tier 1 (mono+) + Tier 2 elite (filter out duplicate markets)
print('  [H24 combined] Tier 1 (min_edge≥1.5c) + Tier 2 (elite filters)...')
ana.execute('''
CREATE OR REPLACE TABLE final_combined AS
WITH t1 AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, strike_lo, strike_hi ORDER BY ts_sec) AS rn
  FROM mono_violations WHERE net_edge > 0.015
),
t1_trades AS (
  SELECT v.event_ticker, v.mkt_lo AS m, v.mkt_hi AS m2, v.qty_lo, v.qty_hi,
         v.yes_ask_lo AS p_lo, v.yes_bid_hi AS p_hi,
         s_lo.result AS r_lo, s_hi.result AS r_hi,
         LEAST(v.qty_lo, v.qty_hi, 5) AS qty,
         ((CASE WHEN s_lo.result='yes' THEN 1.0 ELSE 0.0 END - yes_ask_lo - kalshi_fee(yes_ask_lo))
        + (CASE WHEN s_hi.result='no' THEN 1.0 ELSE 0.0 END - (1.0 - yes_bid_hi) - kalshi_fee(1.0 - yes_bid_hi))) AS pnl_per_pair
  FROM t1 v JOIN settlements s_lo ON s_lo.market_ticker = v.mkt_lo
            JOIN settlements s_hi ON s_hi.market_ticker = v.mkt_hi
  WHERE v.rn = 1
),
t1_markets AS (SELECT m AS market FROM t1_trades UNION SELECT m2 FROM t1_trades),
t2_signals AS (
  SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.market_ticker, s.side ORDER BY s.ts_sec) AS rn
  FROM h3_signals s
  JOIN strike_snaps_1s_clean t USING (event_ticker, market_ticker, ts_sec)
  WHERE s.price >= 0.88 AND s.fair >= 0.95 AND s.edge >= 0.015
    AND s.secs_to_close BETWEEN 1800 AND 3600
    AND t.yes_ask - t.yes_bid <= 0.02
),
t2_trades AS (
  SELECT e.market_ticker AS m, e.side, LEAST(qty_avail, 5) AS qty, e.price, s.result,
         CASE WHEN side='yes' AND result='yes' THEN 1.0 - price - kalshi_fee(price)
              WHEN side='yes' AND result='no'  THEN -price - kalshi_fee(price)
              WHEN side='no'  AND result='no'  THEN 1.0 - price - kalshi_fee(price)
              WHEN side='no'  AND result='yes' THEN -price - kalshi_fee(price)
         END AS pnl_per_c
  FROM t2_signals e JOIN settlements s ON s.market_ticker = e.market_ticker
  WHERE rn = 1 AND e.market_ticker NOT IN (SELECT market FROM t1_markets)
)
SELECT 'tier1' AS tier, COUNT(*) AS n, SUM(pnl_per_pair * qty) AS pnl, SUM(qty) AS contracts FROM t1_trades
UNION ALL
SELECT 'tier2', COUNT(*), SUM(pnl_per_c * qty), SUM(qty) FROM t2_trades
''')
for r in ana.execute('SELECT * FROM final_combined').fetchall():
    print(f'    {r[0]}: n={r[1]} contracts={int(r[3] or 0)} PnL=${(r[2] or 0):.2f}')
tot = ana.execute('SELECT SUM(pnl) FROM final_combined').fetchone()[0]
print(f'    TOTAL combined: ${tot:.2f}  ({tot/7:.2f}/day)')
RESULTS.append({'hyp': 'H24 final', 'desc': 'Tier1(edge≥1.5c) + Tier2-elite combined',
                'n': 0, 'win_pct': 0, 'total': tot, 'per_day': tot/7, 'avg_pnl_c': 0, 'sharpe': 0, 'contracts': 0})

# =======================================================================
# RESULTS TABLE
# =======================================================================
print('\n' + '═' * 95)
print(' SUMMARY — sorted by total PnL')
print('═' * 95)
print(f'{"Hypothesis":<14} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"contracts":>9}  desc')
print('─' * 95)
for r in sorted(RESULTS, key=lambda x: x['total'], reverse=True):
    print(f'{r["hyp"]:<14} {r["n"]:>5} {r["win_pct"]:>5.1f}% {r["total"]:>+9.2f} {r["per_day"]:>+7.2f} '
          f'{r["avg_pnl_c"]:>+9.4f} {r["contracts"]:>9}  {r["desc"][:35]}')

ana.close()
