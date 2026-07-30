"""20 NOVEL strategies on Kalshi BTC binary markets.

Goals: win% ≥ 60%, real PnL, more trades than current 207/week.

Strategy families:
  A) Settlement sniping (final-minute drift to $1)
  B) Spot momentum / cross-strike
  C) Order book microstructure (bid/ask qty, spread compression)
  D) Cross-strike curve outliers
  E) Cross-event continuation
  F) Mean reversion in mid
"""
import duckdb, os

ana = duckdb.connect('backtest_outputs/analysis.duckdb')
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

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
def run(name, query, desc=''):
    try:
        r = ana.execute(query).fetchone()
        if r is None or r[0] is None or r[0] == 0:
            RESULTS.append({'hyp': name, 'desc': desc, 'n': 0, 'win': 0,
                            'total': 0.0, 'per_day': 0.0, 'avg_c': 0.0, 'contracts': 0})
            print(f'  [{name:14}] n=0  {desc}'); return
        n = r[0] or 0; wins = r[1] or 0
        total = float(r[2] or 0); avg = float(r[3] or 0)
        contracts = int(r[4] or 0)
        win_pct = wins/n*100 if n else 0
        RESULTS.append({'hyp': name, 'desc': desc, 'n': n, 'win': win_pct,
                        'total': total, 'per_day': total/7, 'avg_c': avg, 'contracts': contracts})
        ind = '★' if win_pct >= 60 and total > 5 else ' '
        print(f'  {ind} [{name:14}] n={n:>5} win={win_pct:>5.1f}% total=${total:>+7.2f} '
              f'avg=${avg:>+.4f}  {desc}')
    except Exception as e:
        print(f'  [{name:14}] ERROR: {str(e)[:120]}')

# ───────────────────────────────────────────────────────────────────
# Build necessary supporting tables
# ───────────────────────────────────────────────────────────────────
print('Building helper tables...')

# Per-event sigma already exists. Build per-strike snapshots WITH spot velocity.
# Velocity = spot change in last 60s.
ana.execute('''
CREATE OR REPLACE TABLE snap_with_velocity AS
SELECT s.*,
       s.btc_spot - LAG(s.btc_spot, 60) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_move_60s,
       s.btc_spot - LAG(s.btc_spot, 300) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_move_300s
FROM strike_snaps_1s_clean s
''')
print(f'  snap_with_velocity: {ana.execute("SELECT COUNT(*) FROM snap_with_velocity").fetchone()[0]:,}')

# Compute fair_yes for each snapshot using event sigma
ana.execute('''
CREATE OR REPLACE TABLE snap_with_fair AS
SELECT s.*,
       es.sigma_annual,
       s.btc_spot * es.sigma_annual / SQRT(365.25 * 24 * 3600) * SQRT(GREATEST(1, s.secs_to_close)) AS sig_rem,
       CASE WHEN s.btc_spot > s.strike
            THEN norm_cdf(ABS(s.btc_spot - s.strike) / (s.btc_spot * es.sigma_annual / SQRT(365.25*24*3600) * SQRT(GREATEST(1, s.secs_to_close))))
            ELSE 1.0 - norm_cdf(ABS(s.btc_spot - s.strike) / (s.btc_spot * es.sigma_annual / SQRT(365.25*24*3600) * SQRT(GREATEST(1, s.secs_to_close))))
       END AS fair_yes
FROM snap_with_velocity s
JOIN event_sigma es USING (event_ticker)
WHERE s.btc_spot > 1000 AND s.secs_to_close > 0
''')
print(f'  snap_with_fair: {ana.execute("SELECT COUNT(*) FROM snap_with_fair").fetchone()[0]:,}')

# ===================================================================
# GROUP A: SETTLEMENT SNIPING (final-minute drift to $1)
# ===================================================================
print('\n' + '═' * 75 + '\n GROUP A — Settlement sniping')

# A1: TTC < 60s + spot decisively > K (by 2σ_rem) + yes_ask < 0.99 → buy YES, near-certain $1
run('A1 snipe<60s', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 5 AND 60
    AND btc_spot - strike > 2 * sig_rem AND btc_spot > strike
    AND yes_ask BETWEEN 0.85 AND 0.98
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(yes_ask_qty, 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'TTC<60s, spot>K by 2σ, buy YES <0.98')

# A2: Same but NO side — spot decisively < K
run('A2 snipe<60s NO', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 5 AND 60
    AND strike - btc_spot > 2 * sig_rem AND btc_spot < strike
    AND yes_bid BETWEEN 0.02 AND 0.15
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(yes_bid_qty, 5) AS qty,
    (1.0 - yes_bid) AS no_price,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'TTC<60s, K-spot>2σ, buy NO yes_bid<0.15')

# A3: Wider window — TTC < 120s, 1.5σ instead of 2σ
run('A3 snipe<120s', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 5 AND 120
    AND btc_spot - strike > 1.5 * sig_rem AND btc_spot > strike
    AND yes_ask BETWEEN 0.85 AND 0.98
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(yes_ask_qty, 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'TTC<120s, spot>K by 1.5σ')

# A4: Very-deep-ITM snipe — fair > 0.995, any TTC < 300s
run('A4 super-itm', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 5 AND 300
    AND fair_yes > 0.995
    AND yes_ask BETWEEN 0.90 AND 0.98
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(yes_ask_qty, 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'fair>0.995, ttc<5min, buy YES 0.90-0.98')

# ===================================================================
# GROUP B: SPOT MOMENTUM CROSS-STRIKE
# ===================================================================
print('\n' + '═' * 75 + '\n GROUP B — Spot momentum')

# B1: Spot just crossed K from below in last 60s → buy YES (assumes momentum)
run('B1 cross-up', '''
WITH cand AS (
  SELECT s.*, LAG(s.btc_spot, 60) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS spot_60s_ago
  FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 300 AND 3600
    AND s.yes_ask BETWEEN 0.45 AND 0.70
),
crossed AS (
  SELECT * FROM cand
  WHERE spot_60s_ago < strike AND btc_spot > strike  -- spot crossed UP through K
    AND btc_spot - strike < 100  -- still close
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM crossed
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'Spot crossed up through K, buy YES 0.45-0.70')

# B2: Spot just crossed K from above → buy NO
run('B2 cross-down', '''
WITH cand AS (
  SELECT s.*, LAG(s.btc_spot, 60) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS spot_60s_ago
  FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 300 AND 3600
    AND s.yes_bid BETWEEN 0.30 AND 0.55
),
crossed AS (
  SELECT * FROM cand
  WHERE spot_60s_ago > strike AND btc_spot < strike
    AND strike - btc_spot < 100
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM crossed
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_bid_qty,1), 5) AS qty,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'Spot crossed DOWN through K, buy NO')

# B3: Strong momentum (spot moved >$100 in 60s) — buy in direction of move
# When spot moved UP >$100, contracts where K just above (not yet crossed) get repriced — but
# the lagging contracts where K is just BELOW spot offer cheap YES.
run('B3 momentum-up', '''
WITH cand AS (
  SELECT s.* FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 300 AND 3600
    AND s.spot_move_60s > 100  -- spot up >$100 in last min
    AND s.btc_spot > s.strike  -- contract is ITM
    AND s.btc_spot - s.strike < 200  -- but only just
    AND s.yes_ask BETWEEN 0.55 AND 0.85
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'spot+$100 in 60s, ITM, buy YES')

# B4: Mean reversion — spot moved >$300 in 5min, expect partial fade.
# Buy NO if spot moved up sharply (expect pullback) and contract is just above strike.
run('B4 revert-up', '''
WITH cand AS (
  SELECT s.* FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 300 AND 1800
    AND s.spot_move_300s > 300  -- spot up sharply
    AND s.btc_spot > s.strike  -- contract currently YES-leaning
    AND s.btc_spot - s.strike < 150  -- but barely
    AND s.yes_ask BETWEEN 0.55 AND 0.85
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_bid_qty,1), 5) AS qty,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'spot+$300 in 5min, fade with NO buy')

# ===================================================================
# GROUP C: ORDER BOOK MICROSTRUCTURE
# ===================================================================
print('\n' + '═' * 75 + '\n GROUP C — Microstructure')

# C1: Bid > 2x Ask qty (buying pressure) on near-ATM, ITM YES
run('C1 bid-pressure', '''
WITH cand AS (
  SELECT s.* FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 600 AND 3600
    AND s.btc_spot > s.strike  -- YES-leaning
    AND s.btc_spot - s.strike < 200  -- near ATM
    AND s.yes_ask BETWEEN 0.55 AND 0.85
    AND s.yes_bid_qty > 0 AND s.yes_ask_qty > 0
    AND s.yes_bid_qty > 2 * s.yes_ask_qty
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(yes_ask_qty, 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'bid_qty > 2× ask_qty → buy YES near ATM')

# C2: Ask >> Bid (sell pressure) on near-ATM, OTM → buy NO
run('C2 ask-pressure', '''
WITH cand AS (
  SELECT s.* FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 600 AND 3600
    AND s.btc_spot < s.strike
    AND s.strike - s.btc_spot < 200
    AND s.yes_bid BETWEEN 0.15 AND 0.45
    AND s.yes_bid_qty > 0 AND s.yes_ask_qty > 0
    AND s.yes_ask_qty > 2 * s.yes_bid_qty
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(yes_bid_qty, 5) AS qty,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'ask_qty > 2× bid_qty → buy NO when OTM')

# C3: Wide spread > 5c — fade by buying the deeper side
run('C3 wide-spread', '''
WITH cand AS (
  SELECT s.*, s.yes_ask - s.yes_bid AS spread FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 600 AND 3600
    AND s.yes_ask - s.yes_bid >= 0.05  -- wide
    AND s.yes_ask BETWEEN 0.65 AND 0.92
    AND s.fair_yes >= 0.85
    AND s.btc_spot > s.strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'spread ≥ 5c + fair ≥ 0.85 → buy YES')

# C4: Tight spread (≤1c) + deep ITM + small edge — high confidence
run('C4 tight-deep', '''
WITH cand AS (
  SELECT s.* FROM snap_with_fair s
  WHERE s.secs_to_close BETWEEN 600 AND 3600
    AND s.yes_ask - s.yes_bid <= 0.01
    AND s.yes_ask BETWEEN 0.90 AND 0.97
    AND s.fair_yes >= 0.96
    AND s.btc_spot > s.strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'spread ≤ 1c + fair ≥ 0.96 → buy YES')

# ===================================================================
# GROUP D: CROSS-STRIKE CURVE OUTLIERS
# ===================================================================
print('\n' + '═' * 75 + '\n GROUP D — Cross-strike curve')

# Compute median yes_ask across nearby strikes; find outliers
# For each event/ts/strike, compute the median yes_ask of the 5 adjacent strikes (±2)
# An outlier is a strike whose yes_ask is >3c off the curve.
print('  Building curve-fit table (may take a moment)...')
ana.execute('''
CREATE OR REPLACE TABLE strike_curve AS
SELECT a.*,
       (SELECT AVG(b.yes_ask) FROM strike_snaps_1s_clean b
        WHERE b.event_ticker = a.event_ticker
          AND b.ts_sec = a.ts_sec
          AND ABS(b.strike - a.strike) <= 300
          AND b.market_ticker != a.market_ticker
          AND b.yes_ask IS NOT NULL) AS neighbor_avg_ask
FROM strike_snaps_1s_clean a
WHERE a.yes_ask IS NOT NULL AND a.yes_bid IS NOT NULL
  AND a.secs_to_close BETWEEN 600 AND 3600
LIMIT 500000
''')
print(f'  strike_curve: {ana.execute("SELECT COUNT(*) FROM strike_curve").fetchone()[0]:,} (sampled)')

# D1: Strike's yes_ask is >3c BELOW neighbor average → underpriced, buy YES
run('D1 curve-low', '''
WITH cand AS (
  SELECT *, neighbor_avg_ask - yes_ask AS underprice
  FROM strike_curve
  WHERE neighbor_avg_ask IS NOT NULL
    AND neighbor_avg_ask - yes_ask >= 0.03
    AND yes_ask BETWEEN 0.10 AND 0.90
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'yes_ask ≥ 3c below neighbor avg → buy YES')

# D2: yes_bid >3c ABOVE neighbor average → overpriced, buy NO (sell YES at bid)
run('D2 curve-high', '''
WITH cand AS (
  SELECT *, yes_bid - neighbor_avg_ask AS overprice
  FROM strike_curve
  WHERE neighbor_avg_ask IS NOT NULL
    AND yes_bid - neighbor_avg_ask >= 0.03
    AND yes_bid BETWEEN 0.10 AND 0.90
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_bid_qty,1), 5) AS qty,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'yes_bid ≥ 3c above neighbor avg → buy NO')

# ===================================================================
# GROUP E: TIME-OF-EVENT FILTERS
# ===================================================================
print('\n' + '═' * 75 + '\n GROUP E — Time-of-event')

# E1: Skip first 5 min of event (TTC > 55min for hourly), trade only mid+late
run('E1 mid-event', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3300  -- 10-55min
    AND yes_ask BETWEEN 0.80 AND 0.97
    AND fair_yes >= 0.93
    AND ABS(btc_spot - strike) >= 200
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker, 'yes' ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' AND btc_spot>strike THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         WHEN s.result='no'  AND btc_spot<strike THEN 1.0 - (1.0-yes_bid) - kalshi_fee(1.0-yes_bid)
         WHEN s.result='no'  AND btc_spot>strike THEN -yes_ask - kalshi_fee(yes_ask)
         WHEN s.result='yes' AND btc_spot<strike THEN -(1.0-yes_bid) - kalshi_fee(1.0-yes_bid)
    END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
WHERE pnl_c IS NOT NULL
''', '10-55min, deep-ITM mixed yes/no')

# E2: Final 5 min only, deep-ITM, both sides
run('E2 final5m', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 10 AND 300
    AND fair_yes >= 0.95
    AND yes_ask BETWEEN 0.85 AND 0.98
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'final 5min, fair≥0.95, buy YES 0.85-0.98')

# ===================================================================
# GROUP F: STATISTICAL EDGE COMBINED
# ===================================================================
print('\n' + '═' * 75 + '\n GROUP F — Combined statistical')

# F1: Settlement snipe combined: TTC<180s + fair>0.99 + price<0.98 + spread<=2c
run('F1 super-snipe', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 10 AND 180
    AND fair_yes >= 0.99
    AND yes_ask BETWEEN 0.88 AND 0.98
    AND yes_ask - yes_bid <= 0.02
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'TTC<3min + fair>0.99 + spread≤2c YES')

# F2: NO-side super snipe
run('F2 super-snipe-NO', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 10 AND 180
    AND (1.0 - fair_yes) >= 0.99
    AND yes_bid BETWEEN 0.02 AND 0.12
    AND yes_ask - yes_bid <= 0.02
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_bid_qty,1), 5) AS qty,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', 'TTC<3min + fair_no>0.99 + buy NO')

# F3: Distance + final-window combined — most aggressive
run('F3 dist+late', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 60 AND 600
    AND fair_yes >= 0.97
    AND ABS(btc_spot - strike) >= 300
    AND yes_ask BETWEEN 0.88 AND 0.98
    AND btc_spot > strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_ask_qty,1), 5) AS qty,
    CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
         ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', '1-10min + dist≥$300 + fair≥0.97 YES')

# F4: Combined NO version
run('F4 dist+late-NO', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 60 AND 600
    AND (1.0 - fair_yes) >= 0.97
    AND ABS(btc_spot - strike) >= 300
    AND yes_bid BETWEEN 0.02 AND 0.12
    AND btc_spot < strike
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST(COALESCE(yes_bid_qty,1), 5) AS qty,
    CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
         ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty) FROM p
''', '1-10min + dist≥$300 + fair_no≥0.97 NO')

# ===================================================================
# REPORT
# ===================================================================
print('\n' + '═' * 100)
print(' SUMMARY — sorted by total PnL (★ = win≥60% AND total>$5)')
print('═' * 100)
print(f'{"hyp":<16} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"qty":>6}  desc')
print('─' * 100)
for r in sorted(RESULTS, key=lambda x: x['total'], reverse=True):
    star = '★' if r['win'] >= 60 and r['total'] > 5 else ' '
    print(f'{star} {r["hyp"]:<14} {r["n"]:>5} {r["win"]:>5.1f}% {r["total"]:>+9.2f} '
          f'{r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f} {r["contracts"]:>6}  {r["desc"][:42]}')

# Find winners
winners = [r for r in RESULTS if r['win'] >= 60 and r['total'] > 5 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies met goal: win≥60%, total>$5, n≥20')

ana.close()
