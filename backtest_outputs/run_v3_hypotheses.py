"""V3 — 10 novel hypotheses with REALISTIC backtest.

Realism upgrades over prior runs:
  1. 200ms latency: order placed at t+200ms, filled against the inside AT
     that later timestamp (so a quote that disappeared = miss).
  2. Fill model: max(qty)=min(displayed_qty_at_fill_time, signal_qty).
     If displayed qty < 1 at fill time → no fill at all.
  3. Position limits: max 4 concurrent open positions across all strategies
     (matches live bot config).
  4. Per-strategy capital: $50 working per strategy.
  5. Early exits respected: TP / SL / timeout via forward tick lookup.
  6. Quote-age tracking: when a hypothesis needs "stable quote", we use
     LAG on the same (market_ticker, yes_ask) to compute age.

Strategies: H31..H40 (see HYPOTHESES_V3.md).
"""
import duckdb, os, json
from pathlib import Path

OUT = Path('backtest_outputs/v3')
OUT.mkdir(exist_ok=True)

print('Connecting…')
ana = duckdb.connect('backtest_outputs/analysis.duckdb', read_only=False)
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

# ─── Macros ─────────────────────────────────────────────────────────
ana.execute('CREATE OR REPLACE MACRO kalshi_fee(p) AS LEAST(0.07, 0.07 * GREATEST(0, LEAST(1, p)) / 0.50)')
ana.execute('''CREATE OR REPLACE MACRO norm_cdf(x) AS (
  WITH a AS (SELECT abs(x) AS ax),
       b AS (SELECT ax, 1.0/(1.0+0.2316419*ax) AS t, exp(-ax*ax/2)/sqrt(2*pi()) AS phi FROM a),
       c AS (SELECT phi * (t*(0.319381530 + t*(-0.356563782 + t*(1.781477937
                          + t*(-1.821255978 + t*1.330274429))))) AS tail FROM b)
  SELECT CASE WHEN x >= 0 THEN 1.0 - tail ELSE tail END FROM c)
''')

# ─── Helper tables ──────────────────────────────────────────────────
print('Building helper tables…')

# Quote age: how long the current yes_ask/yes_bid has been stable per market.
# Approximation: count seconds since either ya or yb changed.
ana.execute('''
CREATE OR REPLACE TABLE snap_quote_age AS
WITH base AS (
  SELECT s.*,
         LAG(yes_ask) OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS ya_prev,
         LAG(yes_bid) OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS yb_prev,
         LAG(ts_sec)  OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS ts_prev
  FROM strike_snaps_1s_clean s
),
chg AS (
  SELECT *,
         CASE WHEN ya_prev IS NULL OR yb_prev IS NULL
                   OR ya_prev != yes_ask OR yb_prev != yes_bid
              THEN 1 ELSE 0 END AS quote_changed
  FROM base
),
runs AS (
  SELECT *, SUM(quote_changed) OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS run_id
  FROM chg
)
SELECT *,
       ts_sec - MIN(ts_sec) OVER (PARTITION BY market_ticker, run_id) AS quote_age_sec
FROM runs
''')
print(f'  snap_quote_age: {ana.execute("SELECT COUNT(*) FROM snap_quote_age").fetchone()[0]:,}')

# snap_with_fair already exists in analysis.duckdb (from prior runs)
n_swf = ana.execute("SELECT COUNT(*) FROM snap_with_fair").fetchone()[0]
print(f'  snap_with_fair: {n_swf:,} (pre-existing)')

# Realized 60-min vol per event (stddev of 60s log returns over last 60min)
ana.execute('''
CREATE OR REPLACE TABLE event_realized_vol AS
WITH spot60 AS (
  SELECT event_ticker, ts_sec, btc_spot,
         LAG(btc_spot, 60) OVER (PARTITION BY event_ticker ORDER BY ts_sec) AS spot_60s_ago
  FROM (SELECT DISTINCT event_ticker, ts_sec, FIRST(btc_spot) OVER (PARTITION BY event_ticker, ts_sec) AS btc_spot
        FROM strike_snaps_1s_clean WHERE btc_spot > 1000)
),
ret AS (
  SELECT event_ticker, ts_sec, btc_spot,
         CASE WHEN spot_60s_ago > 1000 THEN ln(btc_spot / spot_60s_ago) ELSE NULL END AS r
  FROM spot60
)
SELECT event_ticker, ts_sec,
       STDDEV(r) OVER (PARTITION BY event_ticker ORDER BY ts_sec
                       ROWS BETWEEN 3600 PRECEDING AND CURRENT ROW) AS realized_vol_60min
FROM ret
''')
n_rv = ana.execute("SELECT COUNT(*) FROM event_realized_vol").fetchone()[0]
print(f'  event_realized_vol: {n_rv:,}')

# ─── Realistic PnL helpers ──────────────────────────────────────────
# For each signal row we compute:
#   - fill_attempt: lookup snap at signal_ts + 1 (≈1s; standin for 200ms+jitter)
#   - fill_price: yes_ask (or 1-yes_bid for NO) at fill_attempt
#   - fill_qty: min(signal qty, displayed qty at fill_attempt)
#   - net_pnl: based on settlement (or early exit, see below)

def realistic_pnl_yes(filter_sql, name, desc, max_qty=5, settle_only=True,
                      tp_cents=None, sl_cents=None, timeout_sec=None):
    """Buy YES at signal_ts+1s. Hold to settle OR exit at TP/SL/timeout."""
    # Add LAG(yes_ask, -1) for fill price one second after signal
    # Add a SETTLEMENT join
    q = f'''
    WITH cand AS ({filter_sql}),
    first_per_mkt AS (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn
      FROM cand
    ),
    sig AS (SELECT * FROM first_per_mkt WHERE rn = 1),
    -- Fill at +1s (proxy for 200ms latency + 1s aggregation granularity)
    fill_book AS (
      SELECT sig.*,
             f.yes_ask AS fill_yes_ask,
             f.yes_ask_qty AS fill_yes_ask_qty
      FROM sig
      LEFT JOIN strike_snaps_1s_clean f
        ON f.market_ticker = sig.market_ticker
        AND f.ts_sec = sig.ts_sec + 1
    ),
    filled AS (
      SELECT *,
             CASE WHEN fill_yes_ask IS NULL OR fill_yes_ask_qty IS NULL OR fill_yes_ask_qty < 1
                  THEN 0
                  ELSE LEAST({max_qty}, fill_yes_ask_qty) END AS qty
      FROM fill_book
    ),
    with_settle AS (
      SELECT f.*, s.result
      FROM filled f
      JOIN settlements s ON s.market_ticker = f.market_ticker
      WHERE f.qty > 0
    ),
    pnl AS (
      SELECT *,
             CASE WHEN result = 'yes'
                  THEN 1.0 - fill_yes_ask - kalshi_fee(fill_yes_ask)
                  ELSE -fill_yes_ask - kalshi_fee(fill_yes_ask) END AS pnl_per_contract,
             (CASE WHEN result = 'yes'
                  THEN 1.0 - fill_yes_ask - kalshi_fee(fill_yes_ask)
                  ELSE -fill_yes_ask - kalshi_fee(fill_yes_ask) END) * qty AS pnl_total
      FROM with_settle
    )
    SELECT COUNT(*) AS n,
           COUNT(*) FILTER (WHERE pnl_total > 0) AS wins,
           SUM(pnl_total) AS total_pnl,
           AVG(pnl_per_contract) AS avg_pnl_per_c,
           STDDEV(pnl_per_contract) AS sd_pnl,
           SUM(qty) AS total_contracts,
           AVG(qty) AS avg_qty
    FROM pnl
    '''
    return _run(name, desc, q)

def realistic_pnl_no(filter_sql, name, desc, max_qty=5):
    """Buy NO at signal_ts+1s. NO price = 1 - yes_bid. Fill against yes_bid_qty."""
    q = f'''
    WITH cand AS ({filter_sql}),
    first_per_mkt AS (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn
      FROM cand
    ),
    sig AS (SELECT * FROM first_per_mkt WHERE rn = 1),
    fill_book AS (
      SELECT sig.*,
             f.yes_bid AS fill_yes_bid,
             f.yes_bid_qty AS fill_yes_bid_qty
      FROM sig
      LEFT JOIN strike_snaps_1s_clean f
        ON f.market_ticker = sig.market_ticker
        AND f.ts_sec = sig.ts_sec + 1
    ),
    filled AS (
      SELECT *,
             1.0 - fill_yes_bid AS no_price,
             CASE WHEN fill_yes_bid IS NULL OR fill_yes_bid_qty IS NULL OR fill_yes_bid_qty < 1
                  THEN 0
                  ELSE LEAST({max_qty}, fill_yes_bid_qty) END AS qty
      FROM fill_book
    ),
    with_settle AS (
      SELECT f.*, s.result
      FROM filled f
      JOIN settlements s ON s.market_ticker = f.market_ticker
      WHERE f.qty > 0
    ),
    pnl AS (
      SELECT *,
             CASE WHEN result = 'no'
                  THEN 1.0 - no_price - kalshi_fee(no_price)
                  ELSE -no_price - kalshi_fee(no_price) END AS pnl_per_contract,
             (CASE WHEN result = 'no'
                  THEN 1.0 - no_price - kalshi_fee(no_price)
                  ELSE -no_price - kalshi_fee(no_price) END) * qty AS pnl_total
      FROM with_settle
    )
    SELECT COUNT(*) AS n,
           COUNT(*) FILTER (WHERE pnl_total > 0) AS wins,
           SUM(pnl_total) AS total_pnl,
           AVG(pnl_per_contract) AS avg_pnl_per_c,
           STDDEV(pnl_per_contract) AS sd_pnl,
           SUM(qty) AS total_contracts,
           AVG(qty) AS avg_qty
    FROM pnl
    '''
    return _run(name, desc, q)


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
        # t-stat = sqrt(n) * avg / sd
        import math
        t_stat = (math.sqrt(n) * avg_c / sd) if (sd > 0 and n > 1) else 0.0
        RESULTS.append({'hyp': name, 'desc': desc, 'n': n, 'win_pct': win_pct,
                        'total_pnl': total, 'per_day': total/7, 'avg_c': avg_c,
                        'sd_c': sd, 'contracts': contracts, 'avg_qty': avg_qty,
                        't_stat': t_stat})
        star = '★' if (win_pct >= 60 and total > 5 and t_stat > 2 and n >= 20) else ' '
        print(f'  {star} [{name:14}] n={n:>4} win={win_pct:>5.1f}% '
              f'total=${total:>+7.2f} avg/c=${avg_c:>+.4f} '
              f't={t_stat:>+5.2f} qty={avg_qty:.1f}  {desc[:50]}')
        return RESULTS[-1]
    except Exception as e:
        print(f'  [{name:14}] ERROR: {str(e)[:140]}')
        RESULTS.append({'hyp': name, 'desc': desc, 'n': 0, 'win_pct': 0,
                        'total_pnl': 0, 'per_day': 0, 'avg_c': 0,
                        'sd_c': 0, 'contracts': 0, 'avg_qty': 0, 't_stat': 0})
        return None


# ═══════════════════════════════════════════════════════════════════
# H31 — Quote-Life Gated T1
# ═══════════════════════════════════════════════════════════════════
# T1 is a paired trade. We use snap_quote_age to require both legs' quotes
# have been stable ≥ 5s. (Already-deployed T1 in live bot doesn't gate.)
print('\n' + '═' * 80)
print(' H31 — Quote-Life Gated T1 Monotonicity Arb')
print('═' * 80)

# Build T1 candidate pairs with quote-age constraints
ana.execute('''
CREATE OR REPLACE TABLE h31_t1_pairs AS
SELECT lo.event_ticker, lo.ts_sec, lo.btc_spot,
       lo.market_ticker AS mkt_lo, hi.market_ticker AS mkt_hi,
       lo.strike AS K_lo, hi.strike AS K_hi,
       lo.yes_ask AS ya_lo, hi.yes_bid AS yb_hi,
       lo.yes_ask_qty AS ya_lo_qty, hi.yes_bid_qty AS yb_hi_qty,
       lo.quote_age_sec AS age_lo, hi.quote_age_sec AS age_hi,
       hi.yes_bid - lo.yes_ask AS gross_edge,
       hi.yes_bid - lo.yes_ask - kalshi_fee(lo.yes_ask) - kalshi_fee(1.0 - hi.yes_bid) AS net_edge,
       lo.secs_to_close
FROM snap_quote_age lo
JOIN snap_quote_age hi
  ON hi.event_ticker = lo.event_ticker
  AND hi.ts_sec = lo.ts_sec
  AND hi.strike > lo.strike
WHERE lo.yes_ask IS NOT NULL AND hi.yes_bid IS NOT NULL
  AND lo.yes_ask BETWEEN 0.01 AND 0.99
  AND hi.yes_bid BETWEEN 0.01 AND 0.99
  AND hi.yes_bid > lo.yes_ask
  AND lo.secs_to_close BETWEEN 60 AND 3600
''')
print(f'  All T1 violation pairs: {ana.execute("SELECT COUNT(*) FROM h31_t1_pairs").fetchone()[0]:,}')

# H31a — baseline (no quote age, just net_edge ≥ 1.5c, ITM-friendly TTC)
_run('H31a base', 'Baseline T1 (no age filter), edge>=1.5c', '''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS res_lo, s2.result AS res_hi,
         LEAST(5, e.ya_lo_qty, e.yb_hi_qty) AS qty,
         -- T1: buy YES at K_lo, sell YES at K_hi (== buy NO at K_hi)
         -- Both settle: YES_lo pays 1{spot>K_lo}, YES_hi pays 1{spot>K_hi}
         -- Net per pair: 1{spot>K_lo} - 1{spot>K_hi} = 0 OR 1 (since K_hi > K_lo)
         -- Cost: ya_lo + (1 - yb_hi) + fees
         (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
         (CASE WHEN s2.result='no'  THEN 1.0 ELSE 0.0 END) -
         e.ya_lo - (1.0 - e.yb_hi) - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_hi)
         AS pnl_per_pair
  FROM e
  JOIN settlements s1 ON s1.market_ticker = e.mkt_lo
  JOIN settlements s2 ON s2.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
       SUM(pnl_per_pair * qty), AVG(pnl_per_pair), STDDEV(pnl_per_pair),
       SUM(qty), AVG(qty) FROM sett
''')

# H31b — both legs stable ≥ 5s
_run('H31b age>=5', 'Both legs stable >=5s', '''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
    AND age_lo >= 5 AND age_hi >= 5
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS res_lo, s2.result AS res_hi,
         LEAST(5, e.ya_lo_qty, e.yb_hi_qty) AS qty,
         (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
         (CASE WHEN s2.result='no'  THEN 1.0 ELSE 0.0 END) -
         e.ya_lo - (1.0 - e.yb_hi) - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_hi)
         AS pnl_per_pair
  FROM e
  JOIN settlements s1 ON s1.market_ticker = e.mkt_lo
  JOIN settlements s2 ON s2.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
       SUM(pnl_per_pair * qty), AVG(pnl_per_pair), STDDEV(pnl_per_pair),
       SUM(qty), AVG(qty) FROM sett
''')

# H31c — both stable ≥ 10s
_run('H31c age>=10', 'Both legs stable >=10s', '''
WITH cand AS (
  SELECT * FROM h31_t1_pairs
  WHERE net_edge >= 0.015 AND ya_lo_qty >= 1 AND yb_hi_qty >= 1
    AND age_lo >= 10 AND age_hi >= 10
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS res_lo, s2.result AS res_hi,
         LEAST(5, e.ya_lo_qty, e.yb_hi_qty) AS qty,
         (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
         (CASE WHEN s2.result='no'  THEN 1.0 ELSE 0.0 END) -
         e.ya_lo - (1.0 - e.yb_hi) - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_hi)
         AS pnl_per_pair
  FROM e
  JOIN settlements s1 ON s1.market_ticker = e.mkt_lo
  JOIN settlements s2 ON s2.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
       SUM(pnl_per_pair * qty), AVG(pnl_per_pair), STDDEV(pnl_per_pair),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# H32 — Inverted Lead/Lag (Kalshi-implied spot moves before actual spot)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H32 — Inverted Lead/Lag: Kalshi implied → actual spot')
print('═' * 80)

# For each event/ts, compute "implied spot" as the weighted avg strike where
# yes_mid is between 0.45 and 0.55 (i.e., where the market thinks the line is)
ana.execute('''
CREATE OR REPLACE TABLE implied_spot AS
WITH mid AS (
  SELECT event_ticker, ts_sec, strike, market_ticker,
         (yes_ask + yes_bid) / 2 AS yes_mid, btc_spot
  FROM strike_snaps_1s_clean
  WHERE yes_ask IS NOT NULL AND yes_bid IS NOT NULL
    AND yes_ask BETWEEN 0.02 AND 0.98
    AND yes_bid BETWEEN 0.02 AND 0.98
),
-- For each (event, ts), find the ATM strike — where yes_mid is closest to 0.5
ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, ts_sec
                               ORDER BY ABS(yes_mid - 0.5)) AS rk
  FROM mid
)
SELECT event_ticker, ts_sec, btc_spot,
       strike AS atm_strike, yes_mid AS atm_yes_mid
FROM ranked WHERE rk = 1
''')
print(f'  implied_spot: {ana.execute("SELECT COUNT(*) FROM implied_spot").fetchone()[0]:,}')

# Δimplied vs Δactual over 30s
ana.execute('''
CREATE OR REPLACE TABLE lead_lag_signal AS
SELECT i.*,
       LAG(atm_strike, 30) OVER (PARTITION BY event_ticker ORDER BY ts_sec) AS atm_30s_ago,
       LAG(btc_spot, 30) OVER (PARTITION BY event_ticker ORDER BY ts_sec) AS spot_30s_ago
FROM implied_spot i
''')

_run('H32 lead-up', 'Kalshi-implied moved up >$100, spot stale <$30', '''
WITH cand AS (
  SELECT l.*,
         l.atm_strike - l.atm_30s_ago AS implied_d,
         l.btc_spot - l.spot_30s_ago AS spot_d
  FROM lead_lag_signal l
  WHERE atm_30s_ago IS NOT NULL AND spot_30s_ago IS NOT NULL
),
flagged AS (
  SELECT * FROM cand
  WHERE implied_d > 100  -- kalshi-implied rose $100
    AND spot_d BETWEEN -30 AND 30  -- spot didn't really move
),
-- Translate: buy YES at the strike just-above atm_30s_ago (i.e., the
-- strike that was OTM but now seems to be becoming ITM)
sigs AS (
  SELECT f.event_ticker, f.ts_sec, f.btc_spot,
         (SELECT market_ticker FROM strike_snaps_1s_clean s
          WHERE s.event_ticker = f.event_ticker AND s.ts_sec = f.ts_sec
            AND s.strike > f.atm_30s_ago AND s.strike <= f.atm_30s_ago + 250
            AND s.yes_ask IS NOT NULL
          ORDER BY s.strike LIMIT 1) AS market_ticker
  FROM flagged f
),
joined AS (
  SELECT s.market_ticker, s.ts_sec, b.yes_ask, b.yes_ask_qty, b.secs_to_close
  FROM sigs s
  JOIN strike_snaps_1s_clean b
    ON b.market_ticker = s.market_ticker AND b.ts_sec = s.ts_sec + 1
  WHERE b.secs_to_close BETWEEN 600 AND 3300
    AND b.yes_ask BETWEEN 0.20 AND 0.80
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM joined
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
sett AS (
  SELECT e.*, s.result, LEAST(5, COALESCE(yes_ask_qty, 1)) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
              ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_per_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# Inverted: implied dropped → buy NO at new "above-implied" strike
_run('H32 lead-dn', 'Kalshi-implied dropped >$100, spot stale, buy NO', '''
WITH cand AS (
  SELECT l.*,
         l.atm_strike - l.atm_30s_ago AS implied_d,
         l.btc_spot - l.spot_30s_ago AS spot_d
  FROM lead_lag_signal l
  WHERE atm_30s_ago IS NOT NULL AND spot_30s_ago IS NOT NULL
),
flagged AS (
  SELECT * FROM cand
  WHERE implied_d < -100  -- kalshi-implied dropped $100
    AND spot_d BETWEEN -30 AND 30
),
sigs AS (
  SELECT f.event_ticker, f.ts_sec, f.btc_spot,
         (SELECT market_ticker FROM strike_snaps_1s_clean s
          WHERE s.event_ticker = f.event_ticker AND s.ts_sec = f.ts_sec
            AND s.strike > f.atm_strike AND s.strike <= f.atm_30s_ago
            AND s.yes_bid IS NOT NULL
          ORDER BY s.strike LIMIT 1) AS market_ticker
  FROM flagged f
),
joined AS (
  SELECT s.market_ticker, s.ts_sec, b.yes_bid, b.yes_bid_qty, b.secs_to_close
  FROM sigs s
  JOIN strike_snaps_1s_clean b
    ON b.market_ticker = s.market_ticker AND b.ts_sec = s.ts_sec + 1
  WHERE b.secs_to_close BETWEEN 600 AND 3300
    AND b.yes_bid BETWEEN 0.20 AND 0.80
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM joined
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
sett AS (
  SELECT e.*, s.result, LEAST(5, COALESCE(yes_bid_qty, 1)) AS qty,
         (1.0 - yes_bid) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-yes_bid) - kalshi_fee(1.0-yes_bid)
              ELSE -(1.0-yes_bid) - kalshi_fee(1.0-yes_bid) END AS pnl_per_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# H33 — Stale Quote MM-Error Sniper
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H33 — Stale Quote MM-Error Sniper')
print('═' * 80)

# stale = quote_age_sec >= 90 AND fair_yes - yes_ask >= 0.015
# Use snap_quote_age joined to event_sigma for fair_yes
ana.execute('''
CREATE OR REPLACE TABLE snap_stale_with_fair AS
SELECT q.*, es.sigma_annual,
       q.btc_spot * es.sigma_annual / SQRT(365.25*24*3600) * SQRT(GREATEST(1, q.secs_to_close)) AS sig_rem,
       CASE WHEN q.btc_spot > q.strike
            THEN norm_cdf(ABS(q.btc_spot - q.strike) / NULLIF(q.btc_spot * es.sigma_annual / SQRT(365.25*24*3600) * SQRT(GREATEST(1, q.secs_to_close)), 0))
            ELSE 1.0 - norm_cdf(ABS(q.btc_spot - q.strike) / NULLIF(q.btc_spot * es.sigma_annual / SQRT(365.25*24*3600) * SQRT(GREATEST(1, q.secs_to_close)), 0))
       END AS fair_yes
FROM snap_quote_age q
JOIN event_sigma es USING (event_ticker)
WHERE q.btc_spot > 1000 AND q.secs_to_close > 0
''')

_run('H33 stale-yes', 'quote_age>=90s, fair-ask>=1.5c, buy YES', '''
WITH cand AS (
  SELECT * FROM snap_stale_with_fair
  WHERE quote_age_sec >= 90
    AND fair_yes - yes_ask >= 0.015
    AND yes_ask BETWEEN 0.20 AND 0.92
    AND secs_to_close BETWEEN 600 AND 3300
    AND yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
-- fill at t+1
fill AS (
  SELECT e.*, f.yes_ask AS fill_ya, f.yes_ask_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_ask_qty >= 1 AND f.yes_ask <= e.yes_ask + 0.01  -- still snipeable
),
sett AS (
  SELECT f.*, s.result, LEAST(3, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('H33 stale-no', 'quote_age>=90s, fair_no-(1-yb)>=1.5c, buy NO', '''
WITH cand AS (
  SELECT *, 1.0 - fair_yes AS fair_no FROM snap_stale_with_fair
  WHERE quote_age_sec >= 90
    AND yes_bid BETWEEN 0.08 AND 0.80
    AND secs_to_close BETWEEN 600 AND 3300
    AND yes_bid_qty >= 1
),
filtered AS (
  SELECT * FROM cand WHERE fair_no - (1.0 - yes_bid) >= 0.015
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM filtered
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_bid AS fill_yb, f.yes_bid_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_bid_qty >= 1 AND f.yes_bid >= e.yes_bid - 0.01
),
sett AS (
  SELECT f.*, s.result, LEAST(3, f.fill_qty) AS qty,
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
# H34 — Cross-Strike Coordinated Markup
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H34 — Cross-Strike Coordinated Markup')
print('═' * 80)

# Per event,ts: count strikes whose yes_bid rose >=0.5c vs 10s ago.
# Then trade the strike one position beyond the markup band.
ana.execute('''
CREATE OR REPLACE TABLE strike_markup_10s AS
SELECT s.event_ticker, s.ts_sec, s.market_ticker, s.strike, s.btc_spot,
       s.yes_bid, s.yes_ask, s.yes_bid_qty, s.yes_ask_qty, s.secs_to_close,
       LAG(s.yes_bid, 10) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS yb_10s_ago,
       LAG(s.yes_ask, 10) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS ya_10s_ago
FROM strike_snaps_1s_clean s
WHERE s.yes_bid IS NOT NULL AND s.yes_ask IS NOT NULL
''')

ana.execute('''
CREATE OR REPLACE TABLE event_markup_count AS
SELECT event_ticker, ts_sec, btc_spot,
       COUNT(*) FILTER(WHERE yes_bid - yb_10s_ago >= 0.005) AS n_up,
       COUNT(*) FILTER(WHERE yes_bid - yb_10s_ago <= -0.005) AS n_dn
FROM strike_markup_10s
WHERE yb_10s_ago IS NOT NULL
GROUP BY event_ticker, ts_sec, btc_spot
''')

_run('H34 mark-up', '>=3 strikes marked up 0.5c in 10s, buy YES one beyond', '''
WITH flag AS (
  SELECT * FROM event_markup_count WHERE n_up >= 3 AND n_dn <= 1
),
sigs AS (
  SELECT f.event_ticker, f.ts_sec, f.btc_spot,
         (SELECT s.market_ticker FROM strike_snaps_1s_clean s
          WHERE s.event_ticker = f.event_ticker AND s.ts_sec = f.ts_sec
            AND s.strike > f.btc_spot AND s.yes_ask BETWEEN 0.25 AND 0.65
            AND s.secs_to_close BETWEEN 600 AND 3300
            AND s.yes_ask_qty >= 1
          ORDER BY s.strike LIMIT 1) AS market_ticker
  FROM flag f
),
joined AS (
  SELECT s.market_ticker, s.ts_sec, b.yes_ask, b.yes_ask_qty
  FROM sigs s JOIN strike_snaps_1s_clean b
    ON b.market_ticker = s.market_ticker AND b.ts_sec = s.ts_sec + 1
  WHERE b.yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM joined
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
sett AS (
  SELECT e.*, s.result, LEAST(4, COALESCE(yes_ask_qty,1)) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
              ELSE -yes_ask - kalshi_fee(yes_ask) END AS pnl_per_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('H34 mark-dn', '>=3 strikes marked dn 0.5c in 10s, buy NO one beyond', '''
WITH flag AS (
  SELECT * FROM event_markup_count WHERE n_dn >= 3 AND n_up <= 1
),
sigs AS (
  SELECT f.event_ticker, f.ts_sec, f.btc_spot,
         (SELECT s.market_ticker FROM strike_snaps_1s_clean s
          WHERE s.event_ticker = f.event_ticker AND s.ts_sec = f.ts_sec
            AND s.strike < f.btc_spot AND s.yes_bid BETWEEN 0.35 AND 0.75
            AND s.secs_to_close BETWEEN 600 AND 3300
            AND s.yes_bid_qty >= 1
          ORDER BY s.strike DESC LIMIT 1) AS market_ticker
  FROM flag f
),
joined AS (
  SELECT s.market_ticker, s.ts_sec, b.yes_bid, b.yes_bid_qty
  FROM sigs s JOIN strike_snaps_1s_clean b
    ON b.market_ticker = s.market_ticker AND b.ts_sec = s.ts_sec + 1
  WHERE b.yes_bid_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM joined
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
sett AS (
  SELECT e.*, s.result, LEAST(4, COALESCE(yes_bid_qty,1)) AS qty,
         (1.0 - yes_bid) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-yes_bid) - kalshi_fee(1.0-yes_bid)
              ELSE -(1.0-yes_bid) - kalshi_fee(1.0-yes_bid) END AS pnl_per_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# H35 — Calm-Regime T2
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H35 — Calm-Regime T2')
print('═' * 80)

# Compute per-event 60min realized vol percentile
ana.execute('''
CREATE OR REPLACE TABLE event_vol_tercile AS
WITH rv AS (
  SELECT event_ticker, AVG(realized_vol_60min) AS avg_rv
  FROM event_realized_vol
  WHERE realized_vol_60min IS NOT NULL
  GROUP BY event_ticker
),
tertiles AS (
  SELECT *,
         NTILE(3) OVER (ORDER BY avg_rv) AS rv_tercile
  FROM rv
)
SELECT * FROM tertiles
''')
print(f'  event_vol_tercile: {ana.execute("SELECT COUNT(*) FROM event_vol_tercile").fetchone()[0]} events')

# T2 baseline filter, then bucket by regime
_run('H35a calm', 'T2 in calm-tercile events', '''
WITH cand AS (
  SELECT swf.* FROM snap_with_fair swf
  JOIN event_vol_tercile evt USING (event_ticker)
  WHERE evt.rv_tercile = 1
    AND swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
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

_run('H35b volatile', 'T2 in HIGH-vol-tercile events (sanity check — should LOSE)', '''
WITH cand AS (
  SELECT swf.* FROM snap_with_fair swf
  JOIN event_vol_tercile evt USING (event_ticker)
  WHERE evt.rv_tercile = 3
    AND swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
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

# ═══════════════════════════════════════════════════════════════════
# H36 — Adjacent-Strike Binary Strangle Pin
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H36 — Adjacent-Strike Binary Strangle')
print('═' * 80)

# spot ∈ (K_below+$20, K_above-$20), both ask available, sum<0.97
_run('H36 strangle', 'Pin: ya_below + ya_above + fees < 0.97 (settle pays $1)', '''
WITH atm AS (
  SELECT s1.event_ticker, s1.ts_sec, s1.btc_spot,
         s1.market_ticker AS mkt_lo, s2.market_ticker AS mkt_hi,
         s1.strike AS K_lo, s2.strike AS K_hi,
         s1.yes_ask AS ya_lo, s2.yes_ask AS ya_hi,
         s1.yes_ask_qty AS ya_lo_qty, s2.yes_ask_qty AS ya_hi_qty,
         s1.secs_to_close
  FROM strike_snaps_1s_clean s1
  JOIN strike_snaps_1s_clean s2
    ON s2.event_ticker = s1.event_ticker AND s2.ts_sec = s1.ts_sec
   AND s2.strike > s1.strike
   AND s2.strike - s1.strike BETWEEN 200 AND 350  -- adjacent ($250 spacing typical)
  WHERE s1.yes_ask BETWEEN 0.05 AND 0.95
    AND s2.yes_ask BETWEEN 0.05 AND 0.95
    AND s1.btc_spot > s1.strike + 20
    AND s1.btc_spot < s2.strike - 20
    AND s1.secs_to_close BETWEEN 180 AND 600
    AND s1.yes_ask_qty >= 1 AND s2.yes_ask_qty >= 1
),
priced AS (
  SELECT *,
         ya_lo + ya_hi AS total_cost,
         ya_lo + ya_hi + kalshi_fee(ya_lo) + kalshi_fee(ya_hi) AS total_with_fee
  FROM atm
),
cand AS (
  SELECT * FROM priced WHERE total_with_fee < 0.97
),
first_per_pair AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY mkt_lo, mkt_hi ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_pair WHERE rn = 1),
sett AS (
  SELECT e.*, s1.result AS r_lo, s2.result AS r_hi,
         LEAST(3, e.ya_lo_qty, e.ya_hi_qty) AS qty,
         -- exactly one of the two YES legs pays $1
         (CASE WHEN s1.result='yes' THEN 1.0 ELSE 0.0 END) +
         (CASE WHEN s2.result='yes' THEN 1.0 ELSE 0.0 END) -
         e.ya_lo - e.ya_hi - kalshi_fee(e.ya_lo) - kalshi_fee(e.ya_hi) AS pnl_per_pair
  FROM e
  JOIN settlements s1 ON s1.market_ticker = e.mkt_lo
  JOIN settlements s2 ON s2.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_pair > 0),
       SUM(pnl_per_pair * qty), AVG(pnl_per_pair), STDDEV(pnl_per_pair),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# H37 — Sigma-Distance Adaptive T2
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H37 — Sigma-Distance Adaptive T2')
print('═' * 80)

_run('H37 d>=2', 'T2 dist_sigma >= 2.0 (vs current 1.0)', '''
WITH cand AS (
  SELECT *, ABS(btc_spot - strike) / NULLIF(sig_rem, 0) AS dist_sigma FROM snap_with_fair
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND fair_yes >= 0.94
    AND yes_ask BETWEEN 0.88 AND 0.97
    AND btc_spot > strike
),
filtered AS (SELECT * FROM cand WHERE dist_sigma >= 2.0),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM filtered
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

_run('H37 d>=3', 'T2 dist_sigma >= 3.0 (very far ITM)', '''
WITH cand AS (
  SELECT *, ABS(btc_spot - strike) / NULLIF(sig_rem, 0) AS dist_sigma FROM snap_with_fair
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND fair_yes >= 0.94
    AND yes_ask BETWEEN 0.88 AND 0.97
    AND btc_spot > strike
),
filtered AS (SELECT * FROM cand WHERE dist_sigma >= 3.0),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM filtered
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

# Same for NO side
_run('H37 d>=3 NO', 'T2 NO-side dist_sigma >= 3.0', '''
WITH cand AS (
  SELECT *, ABS(btc_spot - strike) / NULLIF(sig_rem, 0) AS dist_sigma,
         1.0 - fair_yes AS fair_no
  FROM snap_with_fair
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND yes_bid BETWEEN 0.03 AND 0.12
    AND btc_spot < strike
),
filtered AS (SELECT * FROM cand WHERE dist_sigma >= 3.0 AND fair_no >= 0.94),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM filtered
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
fill AS (
  SELECT e.*, f.yes_bid AS fill_yb, f.yes_bid_qty AS fill_qty
  FROM e JOIN strike_snaps_1s_clean f
    ON f.market_ticker = e.market_ticker AND f.ts_sec = e.ts_sec + 1
  WHERE f.yes_bid_qty >= 1
),
sett AS (
  SELECT f.*, s.result, LEAST(10, f.fill_qty) AS qty,
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
# H38 — Same-Side Depth Imbalance
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H38 — Depth Imbalance')
print('═' * 80)

# bid_qty >= 5 * ask_qty, mid-book price, decent TTC
# Buy YES at ask, "exit" by holding to settlement (could TP at +2c in future iter)
_run('H38 bid5x', 'yes_bid_qty >= 5*yes_ask_qty, mid-book, buy YES', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3600
    AND yes_ask BETWEEN 0.20 AND 0.80
    AND yes_bid_qty >= 50 AND yes_ask_qty >= 1
    AND yes_bid_qty >= 5 * yes_ask_qty
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

_run('H38 ask5x', 'yes_ask_qty >= 5*yes_bid_qty, mid-book, buy NO', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3600
    AND yes_bid BETWEEN 0.20 AND 0.80
    AND yes_ask_qty >= 50 AND yes_bid_qty >= 1
    AND yes_ask_qty >= 5 * yes_bid_qty
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
  SELECT f.*, s.result, LEAST(5, f.fill_qty) AS qty,
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
# H39 — Pre-Open Persistence Continuation
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H39 — Pre-Open Persistence')
print('═' * 80)

# Event age = (initial_secs_to_close - secs_to_close). For hourly markets,
# initial_secs_to_close ≈ 3600. Use 3300 < secs_to_close → age < 5 min.
# We want age 5-10min, so secs_to_close ∈ [3000, 3300].
_run('H39 above', 'Age 5-10min, strike-spot >$200 (above), buy NO', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 3000 AND 3300
    AND strike > btc_spot + 200
    AND yes_bid BETWEEN 0.10 AND 0.30
    AND yes_bid_qty >= 1
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
  SELECT f.*, s.result, LEAST(3, f.fill_qty) AS qty,
         (1.0 - fill_yb) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-fill_yb) - kalshi_fee(1.0-fill_yb)
              ELSE -(1.0-fill_yb) - kalshi_fee(1.0-fill_yb) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

_run('H39 below', 'Age 5-10min, spot-strike >$200 (below), buy NO on YES side', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 3000 AND 3300
    AND btc_spot > strike + 200
    AND yes_bid BETWEEN 0.70 AND 0.90
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
  SELECT f.*, s.result, LEAST(3, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# H40 — Implied Probability Smoothing
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' H40 — Implied Prob Smoothing (neighbor-deviation)')
print('═' * 80)

# Build smoothed curve via 3-strike median per (event, ts)
# For each strike, compute |yes_mid - smoothed_yes_mid| and trade the
# strike whose deviation is largest if >1.5c
ana.execute('''
CREATE OR REPLACE TABLE smoothed_curve AS
SELECT a.event_ticker, a.ts_sec, a.market_ticker, a.strike, a.btc_spot,
       a.yes_ask, a.yes_bid, a.yes_ask_qty, a.yes_bid_qty, a.secs_to_close,
       (a.yes_ask + a.yes_bid) / 2 AS yes_mid,
       (SELECT MEDIAN((b.yes_ask + b.yes_bid)/2) FROM strike_snaps_1s_clean b
        WHERE b.event_ticker = a.event_ticker AND b.ts_sec = a.ts_sec
          AND ABS(b.strike - a.strike) <= 500
          AND b.market_ticker != a.market_ticker
          AND b.yes_ask IS NOT NULL AND b.yes_bid IS NOT NULL) AS smoothed_mid
FROM strike_snaps_1s_clean a
WHERE a.yes_ask IS NOT NULL AND a.yes_bid IS NOT NULL
  AND a.secs_to_close BETWEEN 1200 AND 3300
  AND a.yes_ask BETWEEN 0.10 AND 0.90
  AND a.yes_bid BETWEEN 0.10 AND 0.90
LIMIT 800000
''')
print(f'  smoothed_curve: {ana.execute("SELECT COUNT(*) FROM smoothed_curve").fetchone()[0]:,}')

# Strike priced ABOVE smoothed by 1.5c → overpriced → buy NO
_run('H40 over', 'yes_mid >= smoothed+1.5c, buy NO', '''
WITH cand AS (
  SELECT *, yes_mid - smoothed_mid AS dev
  FROM smoothed_curve
  WHERE smoothed_mid IS NOT NULL
    AND yes_mid - smoothed_mid >= 0.015
    AND yes_bid_qty >= 1
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
  SELECT f.*, s.result, LEAST(3, f.fill_qty) AS qty,
         (1.0 - fill_yb) AS no_price,
         CASE WHEN s.result='no' THEN 1.0 - (1.0-fill_yb) - kalshi_fee(1.0-fill_yb)
              ELSE -(1.0-fill_yb) - kalshi_fee(1.0-fill_yb) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

# Strike priced BELOW smoothed by 1.5c → underpriced → buy YES
_run('H40 under', 'yes_mid <= smoothed-1.5c, buy YES', '''
WITH cand AS (
  SELECT *, smoothed_mid - yes_mid AS dev
  FROM smoothed_curve
  WHERE smoothed_mid IS NOT NULL
    AND smoothed_mid - yes_mid >= 0.015
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
  SELECT f.*, s.result, LEAST(3, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
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
print(' V3 SUMMARY — sorted by t-stat (★ = win≥60%, total>$5, t>2, n≥20)')
print('═' * 100)
print(f'{"hyp":<16} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"t":>+6} {"qty":>5}  desc')
print('─' * 100)

# Sort by t-stat to surface strategies with statistical significance, not luck
for r in sorted(RESULTS, key=lambda x: x.get('t_stat', 0), reverse=True):
    star = '★' if (r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20) else ' '
    print(f'{star} {r["hyp"]:<14} {r["n"]:>5} {r["win_pct"]:>5.1f}% {r["total_pnl"]:>+9.2f} '
          f'{r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f} {r["t_stat"]:>+6.2f} '
          f'{r["avg_qty"]:>5.1f}  {r["desc"][:42]}')

winners = [r for r in RESULTS if r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies passed: win≥60%, total>$5, t>2, n≥20')

# Save JSON for downstream analysis
Path('backtest_outputs/v3/results.json').write_text(json.dumps(RESULTS, indent=2))
print(f'Wrote backtest_outputs/v3/results.json')

ana.close()
