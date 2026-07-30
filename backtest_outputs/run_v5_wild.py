"""V5 — Last creative push.

5 wild ideas:
  Z1. EMPIRICAL vs IMPLIED probability surface — compute P(yes) from history
      bucketed by (dist_sigma, TTC), trade where market deviates
  Z2. DELTA-VOLUME momentum — sum of |delta_qty| over 60s as flow signal
  Z3. COINBASE OUTAGE DETECTION — when CB ticks gap >5s, spot is stale
      → either pause OR trade if Kalshi MMs are slow to widen
  Z4. WINNING-PATTERN REVERSE ENGINEER — for trades the live bot DID make
      and that settled profitable, what features did they share?
  Z5. MASS-CONSERVATION + EXTREME-DEPTH variant of W1 — relax the "spot
      in range" constraint by adding tail strikes from outside-chain regions
"""
import duckdb, math, json
from pathlib import Path

ana = duckdb.connect('backtest_outputs/analysis.duckdb', read_only=False)
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

ana.execute('CREATE OR REPLACE MACRO kalshi_fee(p) AS LEAST(0.07, 0.07 * GREATEST(0, LEAST(1, p)) / 0.50)')

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
        print(f'  {star} [{name:28}] n={n:>4} win={win_pct:>5.1f}% '
              f'total=${total:>+8.2f} avg/c=${avg_c:>+.4f} '
              f't={sign}{t_stat:>5.2f} qty={avg_qty:.1f}  {desc[:42]}')
        return RESULTS[-1]
    except Exception as e:
        print(f'  [{name:28}] ERROR: {str(e)[:140]}')
        return None

# ═══════════════════════════════════════════════════════════════════
# Z1. EMPIRICAL vs IMPLIED probability surface
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' Z1 — Empirical-vs-Implied (calibration trades)')
print('═' * 80)

# For each (dist_sigma_bucket, TTC_bucket), compute empirical P(yes)
# from historical settle results. Compare to market implied (yes_mid).
# Trade where market < empirical (buy YES) or market > empirical (buy NO).

# Build bucketed empirical
ana.execute('''
CREATE OR REPLACE TABLE empirical_p_yes AS
WITH snap_with_settle AS (
  SELECT swf.event_ticker, swf.market_ticker, swf.ts_sec, swf.strike,
         swf.btc_spot, swf.sig_rem, swf.secs_to_close,
         (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) AS dist_sigma,
         s.result
  FROM snap_with_fair swf
  JOIN settlements s ON s.market_ticker = swf.market_ticker
  WHERE swf.sig_rem > 0 AND swf.secs_to_close > 300
),
bucketed AS (
  SELECT
    -- Bucket dist_sigma: -3..-1.5, -1.5..-0.5, -0.5..0.5, 0.5..1.5, 1.5..3, 3+
    CASE
      WHEN dist_sigma < -2.5 THEN '01_dn3'
      WHEN dist_sigma < -1.5 THEN '02_dn2'
      WHEN dist_sigma < -0.75 THEN '03_dn1'
      WHEN dist_sigma < -0.25 THEN '04_atm-'
      WHEN dist_sigma <  0.25 THEN '05_atm'
      WHEN dist_sigma <  0.75 THEN '06_atm+'
      WHEN dist_sigma <  1.5 THEN '07_up1'
      WHEN dist_sigma <  2.5 THEN '08_up2'
      ELSE '09_up3'
    END AS dist_bucket,
    CASE
      WHEN secs_to_close < 600  THEN 't1'
      WHEN secs_to_close < 1800 THEN 't2'
      WHEN secs_to_close < 3000 THEN 't3'
      ELSE 't4'
    END AS ttc_bucket,
    result
  FROM snap_with_settle
)
SELECT dist_bucket, ttc_bucket,
       COUNT(*) AS n,
       COUNT(*) FILTER(WHERE result='yes') AS n_yes,
       1.0 * COUNT(*) FILTER(WHERE result='yes') / COUNT(*) AS empirical_p_yes
FROM bucketed
GROUP BY dist_bucket, ttc_bucket
ORDER BY dist_bucket, ttc_bucket
''')
print('  Empirical P(yes) by (dist_sigma, TTC) bucket:')
print(ana.execute('SELECT * FROM empirical_p_yes ORDER BY dist_bucket, ttc_bucket').df().to_string())

# Trade where market_implied diverges from empirical by > 5c
_run('Z1 emp<imp YES', 'empirical_p_yes > market_yes_ask+5c → buy YES', '''
WITH snap_b AS (
  SELECT swf.*,
    (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) AS dist_sigma,
    CASE
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -2.5 THEN '01_dn3'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -1.5 THEN '02_dn2'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -0.75 THEN '03_dn1'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -0.25 THEN '04_atm-'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  0.25 THEN '05_atm'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  0.75 THEN '06_atm+'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  1.5 THEN '07_up1'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  2.5 THEN '08_up2'
      ELSE '09_up3'
    END AS dist_bucket,
    CASE
      WHEN swf.secs_to_close < 600  THEN 't1'
      WHEN swf.secs_to_close < 1800 THEN 't2'
      WHEN swf.secs_to_close < 3000 THEN 't3'
      ELSE 't4'
    END AS ttc_bucket
  FROM snap_with_fair swf
  WHERE swf.sig_rem > 0
),
cand AS (
  SELECT s.*, e.empirical_p_yes
  FROM snap_b s JOIN empirical_p_yes e USING (dist_bucket, ttc_bucket)
  WHERE e.n >= 50  -- need bucket support
    AND s.yes_ask BETWEEN 0.10 AND 0.90
    AND e.empirical_p_yes - s.yes_ask >= 0.05
    AND s.yes_ask_qty >= 1
    AND s.secs_to_close BETWEEN 600 AND 3300
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

_run('Z1 emp>imp NO', 'empirical_p_yes < (1-no_price)-5c → buy NO', '''
WITH snap_b AS (
  SELECT swf.*,
    (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) AS dist_sigma,
    CASE
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -2.5 THEN '01_dn3'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -1.5 THEN '02_dn2'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -0.75 THEN '03_dn1'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) < -0.25 THEN '04_atm-'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  0.25 THEN '05_atm'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  0.75 THEN '06_atm+'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  1.5 THEN '07_up1'
      WHEN (swf.btc_spot - swf.strike) / NULLIF(swf.sig_rem, 0) <  2.5 THEN '08_up2'
      ELSE '09_up3'
    END AS dist_bucket,
    CASE
      WHEN swf.secs_to_close < 600  THEN 't1'
      WHEN swf.secs_to_close < 1800 THEN 't2'
      WHEN swf.secs_to_close < 3000 THEN 't3'
      ELSE 't4'
    END AS ttc_bucket
  FROM snap_with_fair swf
  WHERE swf.sig_rem > 0
),
cand AS (
  SELECT s.*, e.empirical_p_yes
  FROM snap_b s JOIN empirical_p_yes e USING (dist_bucket, ttc_bucket)
  WHERE e.n >= 50
    AND s.yes_bid BETWEEN 0.10 AND 0.90
    AND s.yes_bid - e.empirical_p_yes >= 0.05  -- market overpriced YES = underpriced NO
    AND s.yes_bid_qty >= 1
    AND s.secs_to_close BETWEEN 600 AND 3300
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
# Z2. DELTA-VOLUME MOMENTUM
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' Z2 — Delta-volume momentum (sum |delta| in last 60s)')
print('═' * 80)

# Aggregate delta volume per market per ts_sec
ana.execute('''
CREATE OR REPLACE TABLE delta_vol_60s AS
WITH deltas AS (
  SELECT market_ticker, side, price, ABS(delta_qty) AS abs_qty,
         delta_qty AS signed_qty,
         CAST(received_at_ns / 1000000000 AS BIGINT) AS ts_sec
  FROM src.ws_orderbook_delta_all
  WHERE price BETWEEN 0.05 AND 0.95
),
per_ts AS (
  SELECT market_ticker, ts_sec,
         SUM(abs_qty) FILTER(WHERE side='yes') AS yes_vol,
         SUM(abs_qty) FILTER(WHERE side='no')  AS no_vol,
         SUM(signed_qty) FILTER(WHERE side='yes') AS yes_signed,
         SUM(signed_qty) FILTER(WHERE side='no')  AS no_signed
  FROM deltas GROUP BY market_ticker, ts_sec
)
SELECT * FROM per_ts
''')
print(f'  delta_vol_60s rows: {ana.execute("SELECT COUNT(*) FROM delta_vol_60s").fetchone()[0]:,}')

# High-volume YES side (yes_vol >= 100, with positive net signed)
_run('Z2 yes-flow-100', 'yes_vol>=100 + net yes adds, buy YES', '''
WITH cand AS (
  SELECT d.market_ticker, d.ts_sec, d.yes_vol, d.yes_signed,
         s.yes_ask, s.yes_ask_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM delta_vol_60s d
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = d.market_ticker AND s.ts_sec = d.ts_sec
  WHERE d.yes_vol >= 100 AND d.yes_signed >= 50
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

_run('Z2 no-flow-100', 'no_vol>=100 + net no adds, buy NO', '''
WITH cand AS (
  SELECT d.market_ticker, d.ts_sec, d.no_vol, d.no_signed,
         s.yes_bid, s.yes_bid_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM delta_vol_60s d
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = d.market_ticker AND s.ts_sec = d.ts_sec
  WHERE d.no_vol >= 100 AND d.no_signed >= 50
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

# Combined: total flow >= 200 + direction
_run('Z2 flow-direction', 'Total flow >= 200, follow signed direction', '''
WITH cand AS (
  SELECT d.market_ticker, d.ts_sec, d.yes_vol + d.no_vol AS total_vol,
         d.yes_signed, d.no_signed,
         s.yes_ask, s.yes_bid, s.yes_ask_qty, s.yes_bid_qty,
         s.secs_to_close, s.btc_spot, s.strike,
         -- Side to trade: if yes_signed dominates, buy YES; else NO
         CASE WHEN d.yes_signed > d.no_signed THEN 'yes' ELSE 'no' END AS trade_side
  FROM delta_vol_60s d
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = d.market_ticker AND s.ts_sec = d.ts_sec
  WHERE d.yes_vol + d.no_vol >= 200
    AND s.secs_to_close BETWEEN 600 AND 3300
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_ask AS fill_ya, f.yes_bid AS fill_yb,
         f.yes_ask_qty AS fill_ask_qty, f.yes_bid_qty AS fill_bid_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE (e.trade_side = 'yes' AND f.yes_ask_qty >= 1 AND f.yes_ask BETWEEN 0.15 AND 0.85)
     OR (e.trade_side = 'no'  AND f.yes_bid_qty >= 1 AND f.yes_bid BETWEEN 0.15 AND 0.85)
),
sett AS (
  SELECT f.*, s.result,
         CASE WHEN f.trade_side = 'yes' THEN LEAST(4, fill_ask_qty)
              ELSE LEAST(4, fill_bid_qty) END AS qty,
         CASE
           WHEN f.trade_side = 'yes' AND s.result = 'yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
           WHEN f.trade_side = 'yes' AND s.result = 'no'  THEN -fill_ya - kalshi_fee(fill_ya)
           WHEN f.trade_side = 'no'  AND s.result = 'no'  THEN 1.0 - (1.0 - fill_yb) - kalshi_fee(1.0 - fill_yb)
           WHEN f.trade_side = 'no'  AND s.result = 'yes' THEN -(1.0 - fill_yb) - kalshi_fee(1.0 - fill_yb)
         END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett WHERE pnl_per_c IS NOT NULL
''')

# ═══════════════════════════════════════════════════════════════════
# Z3. CB OUTAGE detection (no ticks for >5s)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' Z3 — Coinbase outage detection')
print('═' * 80)

# Per-second gap: tick count = 0 means no CB updates that second
ana.execute('''
CREATE OR REPLACE TABLE cb_gaps AS
WITH ts AS (
  SELECT CAST(received_at_ns / 1000000000 AS BIGINT) AS ts_sec
  FROM src.coinbase_ticker_all
),
ticks_per_sec AS (
  SELECT ts_sec, COUNT(*) AS n_ticks FROM ts GROUP BY ts_sec
),
all_secs AS (
  SELECT MIN(ts_sec) AS min_s, MAX(ts_sec) AS max_s FROM ticks_per_sec
),
seq AS (
  SELECT n.ts_sec FROM (
    SELECT (SELECT min_s FROM all_secs) + RANGE AS ts_sec
    FROM RANGE(0, (SELECT max_s - min_s FROM all_secs) + 1)
  ) n
)
SELECT s.ts_sec, COALESCE(t.n_ticks, 0) AS n_ticks
FROM seq s LEFT JOIN ticks_per_sec t ON t.ts_sec = s.ts_sec
''')

# Find gaps of >=5 consecutive seconds without ticks
print('  CB tick gap stats:')
print(ana.execute('''
SELECT
  COUNT(*) AS total_secs,
  COUNT(*) FILTER(WHERE n_ticks = 0) AS gap_secs,
  100.0 * COUNT(*) FILTER(WHERE n_ticks = 0) / COUNT(*) AS pct_gap
FROM cb_gaps
''').df().to_string())

# Identify ts_secs that are in or right after a gap (>=3 consecutive missing)
ana.execute('''
CREATE OR REPLACE TABLE cb_outage_periods AS
WITH gaps AS (
  SELECT ts_sec, n_ticks,
         SUM(CASE WHEN n_ticks > 0 THEN 1 ELSE 0 END) OVER (ORDER BY ts_sec) AS group_id
  FROM cb_gaps
),
runs AS (
  SELECT group_id, MIN(ts_sec) AS run_start, MAX(ts_sec) AS run_end, COUNT(*) AS run_len
  FROM gaps WHERE n_ticks = 0
  GROUP BY group_id
)
SELECT run_start AS gap_start, run_end AS gap_end, run_len
FROM runs WHERE run_len >= 3
''')
print(f'  CB outages (>=3s) found: {ana.execute("SELECT COUNT(*) FROM cb_outage_periods").fetchone()[0]:,}')

# Trade T2 ONLY when there's NO recent CB outage (last 60s clean)
_run('Z3 T2-clean-cb', 'T2 only when no CB gap in last 60s', '''
WITH cand AS (
  SELECT swf.*
  FROM snap_with_fair swf
  WHERE swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
    AND NOT EXISTS (
      SELECT 1 FROM cb_outage_periods o
      WHERE o.gap_end BETWEEN swf.ts_sec - 60 AND swf.ts_sec
    )
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
  SELECT f.*, s.result, LEAST(5, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('Z3 T2-during-outage', 'T2 DURING or right after CB outage (CB stale, MMs likely lag)', '''
WITH cand AS (
  SELECT swf.*
  FROM snap_with_fair swf
  WHERE swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
    AND EXISTS (
      SELECT 1 FROM cb_outage_periods o
      WHERE o.gap_end BETWEEN swf.ts_sec - 30 AND swf.ts_sec
    )
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
  SELECT f.*, s.result, LEAST(5, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# Z4. Live-bot WIN reverse-engineer
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' Z4 — Live-bot win reverse-engineering')
print('═' * 80)

# Get the live bot's actual fills from ws_private_event_all
print('  Private event types:')
print(ana.execute('''
SELECT message_type, COUNT(*) FROM src.ws_private_event_all GROUP BY 1
''').df())

print('\n  Sample payloads (first 3 fills):')
print(ana.execute('''
SELECT received_at_ns, message_type, market_ticker, payload_json
FROM src.ws_private_event_all WHERE message_type = 'fill' LIMIT 3
''').df().to_string()[:1500])

# Extract fills with side, price, qty from JSON, then join settlement
ana.execute('''
CREATE OR REPLACE TABLE private_fills AS
SELECT received_at_ns, market_ticker,
       payload_json->>'action' AS action,
       payload_json->>'side' AS side,
       CAST(payload_json->>'count' AS INTEGER) AS qty,
       CAST(payload_json->>'yes_price' AS DOUBLE) / 100.0 AS yes_price,
       CAST(payload_json->>'no_price' AS DOUBLE) / 100.0 AS no_price,
       CAST(received_at_ns / 1000000000 AS BIGINT) AS ts_sec
FROM src.ws_private_event_all
WHERE message_type = 'fill'
''')
print(f'\n  private_fills: {ana.execute("SELECT COUNT(*) FROM private_fills").fetchone()[0]}')
print(ana.execute('SELECT * FROM private_fills LIMIT 5').df().to_string())

# Compute outcome of each actual fill
_run('Z4 actual-fills', 'Live bot actual fills (real PnL ground truth)', '''
WITH cand AS (
  SELECT pf.*, s.result
  FROM private_fills pf
  JOIN settlements s ON s.market_ticker = pf.market_ticker
  WHERE pf.action = 'taker'  -- buying, not selling
    AND pf.yes_price BETWEEN 0.01 AND 0.99
),
pnl AS (
  SELECT *,
    CASE
      WHEN side='yes' AND result='yes' THEN 1.0 - yes_price - kalshi_fee(yes_price)
      WHEN side='yes' AND result='no'  THEN -yes_price - kalshi_fee(yes_price)
      WHEN side='no'  AND result='no'  THEN 1.0 - no_price - kalshi_fee(no_price)
      WHEN side='no'  AND result='yes' THEN -no_price - kalshi_fee(no_price)
    END AS pnl_per_c
  FROM cand
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM pnl WHERE pnl_per_c IS NOT NULL
''')

# ═══════════════════════════════════════════════════════════════════
# Z5. T1 + recent-spot-stability filter (NEW SCALING ANGLE)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' Z5 — T1 with recent-spot-stability filter')
print('═' * 80)

# Hypothesis: T1 arbs that occur in CALM spot regimes (no $50 move in last 30s)
# may persist LONGER than during volatile regimes. Test: split T1 by recent
# spot velocity.
ana.execute('''
CREATE OR REPLACE TABLE t1_with_velocity AS
SELECT t.*,
       sv.move_30 AS spot_move_30s,
       sv.move_60 AS spot_move_60s
FROM h31_t1_pairs t
LEFT JOIN spot_velocity sv
  ON sv.event_ticker = t.event_ticker AND sv.ts_sec = t.ts_sec
WHERE t.net_edge >= 0.015
''')

_run('Z5 T1-calm', 'T1 when |spot_move_30s|<$30 (calm)', '''
WITH cand AS (
  SELECT * FROM t1_with_velocity
  WHERE spot_move_30s IS NOT NULL AND ABS(spot_move_30s) <= 30
    AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS r_lo, s2.result AS r_hi,
         LEAST(20, e.ya_lo_qty, e.yb_hi_qty) AS qty,
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

_run('Z5 T1-volatile', 'T1 when |spot_move_30s|>$50 (volatile)', '''
WITH cand AS (
  SELECT * FROM t1_with_velocity
  WHERE spot_move_30s IS NOT NULL AND ABS(spot_move_30s) > 50
    AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS r_lo, s2.result AS r_hi,
         LEAST(20, e.ya_lo_qty, e.yb_hi_qty) AS qty,
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
# REPORT
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 100)
print(' ROUND 5 SUMMARY')
print('═' * 100)
print(f'{"hyp":<30} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"t":>7} {"qty":>5}  desc')
print('─' * 110)

for r in sorted(RESULTS, key=lambda x: x.get('total_pnl', 0), reverse=True):
    star = '★' if (r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20) else ' '
    sign = '+' if r['t_stat'] >= 0 else ''
    print(f'{star} {r["hyp"]:<28} {r["n"]:>5} {r["win_pct"]:>5.1f}% {r["total_pnl"]:>+9.2f} '
          f'{r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f} {sign}{r["t_stat"]:>5.2f} '
          f'{r["avg_qty"]:>5.1f}  {r["desc"][:42]}')

winners = [r for r in RESULTS if r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies passed criteria')

Path('backtest_outputs/v3/round5_results.json').write_text(json.dumps(RESULTS, indent=2))
ana.close()
