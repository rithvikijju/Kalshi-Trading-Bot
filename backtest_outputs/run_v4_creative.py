"""V4 — TRULY CREATIVE hypotheses.

Built after V3 confirmed: fee floor crushes naive directional plays, T1 has
depth headroom but is HFT-eaten. Need structural or alt-data edges.

8 creative tests:
  W1. N-STRIKE MASS CONSERVATION arb (generalize T1 to 3+ strikes)
  W2. REVERSE BUTTERFLY (exploit anti-convexity, not convexity)
  W3. COINBASE-LAG ADAPTIVE T2 (trade only when CB lag is low = fresh data)
  W4. COINBASE-SPREAD WIDENING SIGNAL (CB OB stress = Kalshi widens = edge)
  W5. BROWNIAN BRIDGE FAIR VALUE (replaces one-shot BS, better near close)
  W6. HOUR-OF-DAY pattern bias (does BTC have intraday cyclicality?)
  W7. FIRST-ACTIVE-TICK opportunity (pricing wide right after market opens)
  W8. ADJACENT-EVENT INHERITANCE (when event N settles, event N+1 starts —
      test for transition mispricing)
"""
import duckdb, math, json
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
        print(f'  {star} [{name:24}] n={n:>4} win={win_pct:>5.1f}% '
              f'total=${total:>+8.2f} avg/c=${avg_c:>+.4f} '
              f't={sign}{t_stat:>5.2f} qty={avg_qty:.1f}  {desc[:46]}')
        return RESULTS[-1]
    except Exception as e:
        print(f'  [{name:24}] ERROR: {str(e)[:140]}')
        return None

# ═══════════════════════════════════════════════════════════════════
# W1. N-STRIKE MASS CONSERVATION (sum of yes_bids > 1)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W1 — N-Strike Mass Conservation Arb')
print('═' * 80)
# For each (event, ts_sec), sum yes_bid across ALL active strikes. If sum > 1,
# then selling YES at each strike's bid pays $1 (one strike always wins) minus
# the total proceeds, which means GROSS proceeds > $1 = arb after fees.
# Actually selling YES = "buying NO". For each market, cost to buy NO = 1-yes_bid.
# Sum of (1-yes_bid) across all strikes = N - sum(yes_bid). When you settle:
# all strikes settle as YES or NO. Since spot ends in exactly one strike's
# range (between two strikes), exactly ONE of them has YES=1 and the rest are
# the right side of it.
#
# Wait — actually these are RANGE markets. Each KXBTCD-...T80999.99 = "spot >
# $81000 at close". So they're inclusive thresholds. Multiple strikes can
# simultaneously be "YES" (e.g. if spot=$82000 at close, all strikes ≤ $82000
# settle YES, all > $82000 settle NO).
#
# So sum of P(yes_K) = sum of P(spot > K_i) = N - (sum of CDFs).
# This is NOT a constant. Strike chain shows the empirical CDF.
# But the SLOPE constraint holds: P(spot > K_i) - P(spot > K_i+1) = P(spot in
# (K_i, K_i+1)) ≥ 0. So yes_K must DECREASE in K. T1 already enforces this.
#
# What's the n-strike generalization? For 3 strikes K_lo < K_mid < K_hi:
#   yes_bid(K_lo) - yes_ask(K_mid) ≥ 0 (T1: K_lo, K_mid)
#   yes_bid(K_mid) - yes_ask(K_hi) ≥ 0 (T1: K_mid, K_hi)
# These are independent T1s. The "transitive" version:
#   yes_bid(K_lo) - yes_ask(K_hi) ≥ 0 is T1: K_lo, K_hi (skipping mid).
# So 3-strike adds nothing new.
#
# BUT! What about a different structural identity: at expiration, exactly one
# of the strikes' "neighbor differences" pays $1: P(K_i < spot ≤ K_{i+1}) = 1.
# This is a butterfly identity. Buying YES(K_i) - YES(K_{i+1}) creates a binary
# payoff of $1 if spot lands in (K_i, K_{i+1}]. Cost = ya(K_i) - yb(K_{i+1}).
#
# So a "ladder of binary calendars": for each i, buy YES(K_i) and sell YES(K_{i+1}).
# Sum of all such pairs across the chain = guaranteed $1 (whichever interval
# spot lands in pays). Total cost = ya(K_1) - yb(K_N) + sum_i (ya(K_i) - yb(K_i)).
# If total cost + fees < $1 - margin, ARB.

# Build per-event aggregates: for each (event, ts), the sum of (ya - yb) across
# strikes, plus the cost of the extreme legs (longest covered range).
ana.execute('''
CREATE OR REPLACE TABLE w1_event_chain AS
WITH per_strike AS (
  SELECT event_ticker, ts_sec, btc_spot, secs_to_close,
         strike, market_ticker, yes_bid, yes_ask, yes_bid_qty, yes_ask_qty
  FROM strike_snaps_1s_clean
  WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
    AND yes_bid BETWEEN 0.01 AND 0.99
    AND yes_ask BETWEEN 0.01 AND 0.99
    AND secs_to_close BETWEEN 600 AND 3600
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, ts_sec ORDER BY strike) AS rk,
         COUNT(*) OVER (PARTITION BY event_ticker, ts_sec) AS chain_n
  FROM per_strike
)
SELECT event_ticker, ts_sec, btc_spot, secs_to_close,
       MIN(strike) AS min_strike, MAX(strike) AS max_strike,
       chain_n,
       -- Sum of bid-ask spreads (total round-trip cost for ladder strategy)
       SUM(yes_ask - yes_bid) AS sum_spread,
       -- First and last yes_bid/yes_ask (chain extremes)
       MIN(strike) AS k_min,
       MAX(strike) AS k_max,
       FIRST(yes_ask) OVER (PARTITION BY event_ticker, ts_sec ORDER BY strike) AS ya_first,
       LAST(yes_bid) OVER (PARTITION BY event_ticker, ts_sec ORDER BY strike) AS yb_last
FROM ranked
GROUP BY event_ticker, ts_sec, btc_spot, secs_to_close, chain_n, strike, yes_ask, yes_bid
HAVING chain_n >= 5
''')
print(f'  w1_event_chain rows: {ana.execute("SELECT COUNT(*) FROM w1_event_chain").fetchone()[0]:,}')

# Actually the formula for ladder cost:
# Sum of [ya(K_i) - yb(K_{i+1})] for i=1..N-1 = ya(K_1) + sum(ya(K_i) - yb(K_i)) - yb(K_N)
#   = ya(K_min) - yb(K_max) + sum(spread_i for i in [2..N-1])
# Wait, easier: each interval [K_i, K_{i+1}] payoff requires 1 long YES(K_i)
# and 1 short YES(K_{i+1}). At settlement: long pays 1 if spot > K_i, short
# pays -1 if spot > K_{i+1}. Net = 1 if K_i < spot ≤ K_{i+1}, else 0.
# Sum of all such "diff calls" = 1 IF spot ∈ (K_min, K_max]. Otherwise 0
# (spot below K_min or above K_max).

# Cost = sum_i (ya(K_i) - yb(K_{i+1})) ≥ 0 always (no-arb on T1 pairs).
# For arb: need cost + fees + P(spot outside K_min..K_max) × 0 < 1.
# Actually if spot may end outside chain, sum payoff < 1. So we'd lose if outside.

# Probably not arb in general. But within the chain range (P(spot ∈ chain) ~ 1
# for normal events), the cost should equal ~$1.
# Test: how often is sum cost < $0.97? Those are interesting cases.

ana.execute('''
CREATE OR REPLACE TABLE w1_ladder_cost AS
WITH per_strike AS (
  SELECT event_ticker, ts_sec, btc_spot, secs_to_close,
         strike, market_ticker, yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
         ROW_NUMBER() OVER (PARTITION BY event_ticker, ts_sec ORDER BY strike) AS rk
  FROM strike_snaps_1s_clean
  WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
    AND yes_bid BETWEEN 0.01 AND 0.99
    AND yes_ask BETWEEN 0.01 AND 0.99
    AND secs_to_close BETWEEN 600 AND 3600
),
pairs AS (
  SELECT a.event_ticker, a.ts_sec, a.btc_spot, a.secs_to_close,
         a.strike AS k_lo, b.strike AS k_hi,
         a.yes_ask AS ya_lo, b.yes_bid AS yb_hi,
         a.yes_ask - b.yes_bid AS diff_cost,
         LEAST(a.yes_ask_qty, b.yes_bid_qty) AS depth
  FROM per_strike a JOIN per_strike b
    ON a.event_ticker = b.event_ticker AND a.ts_sec = b.ts_sec AND b.rk = a.rk + 1
),
totals AS (
  SELECT event_ticker, ts_sec, btc_spot, secs_to_close,
         MIN(k_lo) AS k_min, MAX(k_hi) AS k_max,
         COUNT(*) AS n_intervals,
         SUM(diff_cost) AS total_cost,
         SUM(GREATEST(diff_cost, 0)) AS total_cost_clamped,
         MIN(depth) AS min_depth,
         SUM(kalshi_fee(ya_lo) + kalshi_fee(1.0 - yb_hi)) AS total_fees,
         AVG(diff_cost) AS avg_diff_cost
  FROM pairs
  GROUP BY event_ticker, ts_sec, btc_spot, secs_to_close
  HAVING COUNT(*) >= 5
)
SELECT * FROM totals
''')
n_lc = ana.execute("SELECT COUNT(*) FROM w1_ladder_cost").fetchone()[0]
print(f'  w1_ladder_cost rows: {n_lc:,}')

# Distribution of total_cost (the all-strikes-ladder cost)
print('  Total ladder cost distribution (should cluster ≤ $1):')
print(ana.execute('''
SELECT MIN(total_cost) AS mn, AVG(total_cost) AS avg, MEDIAN(total_cost) AS med,
       PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY total_cost) AS p5,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_cost) AS p95,
       MAX(total_cost) AS mx
FROM w1_ladder_cost
''').df().to_string())

# Find ladders where cost < $0.95 (arb opportunities — spot guaranteed in range)
_run('W1 ladder<0.95', 'Ladder cost+fee<0.95, spot in (k_min, k_max]', '''
WITH cand AS (
  SELECT * FROM w1_ladder_cost
  WHERE total_cost + total_fees < 0.95
    AND btc_spot > k_min AND btc_spot <= k_max
    AND min_depth >= 1
),
first_per_event AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_event WHERE rn = 1),
-- For each ladder, total payoff is $1 if spot at close ∈ (k_min, k_max], else 0
sett AS (
  SELECT e.*, ess.settle_spot,
         CASE WHEN ess.settle_spot > e.k_min AND ess.settle_spot <= e.k_max
              THEN 1.0 - e.total_cost - e.total_fees
              ELSE -e.total_cost - e.total_fees
         END AS pnl_per_ladder,
         LEAST(3, e.min_depth) AS qty
  FROM e
  JOIN event_settlement_spot ess ON ess.event_ticker = e.event_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_ladder > 0),
       SUM(pnl_per_ladder * qty), AVG(pnl_per_ladder), STDDEV(pnl_per_ladder),
       SUM(qty), AVG(qty) FROM sett
''')

# Even tighter — cost < $0.90
_run('W1 ladder<0.90', 'Ladder cost+fee<0.90 (deeper arb)', '''
WITH cand AS (
  SELECT * FROM w1_ladder_cost
  WHERE total_cost + total_fees < 0.90
    AND btc_spot > k_min AND btc_spot <= k_max
    AND min_depth >= 1
),
first_per_event AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_event WHERE rn = 1),
sett AS (
  SELECT e.*, ess.settle_spot,
         CASE WHEN ess.settle_spot > e.k_min AND ess.settle_spot <= e.k_max
              THEN 1.0 - e.total_cost - e.total_fees
              ELSE -e.total_cost - e.total_fees
         END AS pnl_per_ladder,
         LEAST(3, e.min_depth) AS qty
  FROM e
  JOIN event_settlement_spot ess ON ess.event_ticker = e.event_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_ladder > 0),
       SUM(pnl_per_ladder * qty), AVG(pnl_per_ladder), STDDEV(pnl_per_ladder),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# W2. REVERSE BUTTERFLY (exploit anti-convexity)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W2 — Reverse Butterfly: exploit anti-convexity')
print('═' * 80)

# Prior butterfly arb LOST -$185 expecting convexity. So the market has
# anti-convexity: the middle strike is OVERPRICED relative to wings.
# Strategy: SELL middle (buy NO at K_mid), BUY wings (buy YES at K_lo, K_hi).
# This earns the convexity premium.

ana.execute('''
CREATE OR REPLACE TABLE w2_triples AS
WITH per_strike AS (
  SELECT event_ticker, ts_sec, btc_spot, secs_to_close,
         strike, market_ticker, yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
         ROW_NUMBER() OVER (PARTITION BY event_ticker, ts_sec ORDER BY strike) AS rk
  FROM strike_snaps_1s_clean
  WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
    AND secs_to_close BETWEEN 1200 AND 3600
)
SELECT a.event_ticker, a.ts_sec, a.btc_spot, a.secs_to_close,
       a.strike AS k_lo, a.market_ticker AS mkt_lo,
       b.strike AS k_mid, b.market_ticker AS mkt_mid,
       c.strike AS k_hi, c.market_ticker AS mkt_hi,
       a.yes_ask AS ya_lo, b.yes_bid AS yb_mid, c.yes_ask AS ya_hi,
       a.yes_ask_qty AS qty_lo, b.yes_bid_qty AS qty_mid, c.yes_ask_qty AS qty_hi,
       -- Anti-convexity violation: yb(K_mid) > (ya(K_lo) + ya(K_hi)) / 2 + threshold
       b.yes_bid - (a.yes_ask + c.yes_ask) / 2 AS antiC_amount
FROM per_strike a JOIN per_strike b
  ON a.event_ticker = b.event_ticker AND a.ts_sec = b.ts_sec AND b.rk = a.rk + 1
JOIN per_strike c
  ON c.event_ticker = a.event_ticker AND c.ts_sec = a.ts_sec AND c.rk = a.rk + 2
WHERE a.yes_ask BETWEEN 0.05 AND 0.95
  AND b.yes_bid BETWEEN 0.05 AND 0.95
  AND c.yes_ask BETWEEN 0.05 AND 0.95
''')
print(f'  w2_triples rows: {ana.execute("SELECT COUNT(*) FROM w2_triples").fetchone()[0]:,}')

# Reverse butterfly: sell yb_mid + buy ya_lo + buy ya_hi
# Payoff at settle:
#   Sell YES at K_mid: pays -1 if spot > K_mid, else 0; revenue = +yb_mid
#   Buy YES at K_lo:   pays +1 if spot > K_lo, else 0; cost = -ya_lo
#   Buy YES at K_hi:   pays +1 if spot > K_hi, else 0; cost = -ya_hi
# Net payoff by spot:
#   spot ≤ K_lo:    0 + 0 + 0 - costs = -ya_lo - ya_hi + yb_mid
#   K_lo < spot ≤ K_mid: 0 + 1 + 0 - costs = 1 - ya_lo - ya_hi + yb_mid
#   K_mid < spot ≤ K_hi: -1 + 1 + 0 - costs = 0 - ya_lo - ya_hi + yb_mid
#   spot > K_hi:    -1 + 1 + 1 - costs = 1 - ya_lo - ya_hi + yb_mid
# So we win $1 in 2 of 4 regions, lose 0 in 2 of 4.
# Expected payoff = 1 × (P(K_lo<spot≤K_mid) + P(spot>K_hi)) - ya_lo - ya_hi + yb_mid
# For this to be profitable, we need the "anti-convexity" yb_mid - (ya_lo + ya_hi)/2
# to be high enough.

_run('W2 antiC>=3c', 'Reverse butterfly: yb_mid - (ya_lo+ya_hi)/2 ≥ 3c', '''
WITH cand AS (
  SELECT * FROM w2_triples
  WHERE antiC_amount >= 0.03
    AND qty_lo >= 1 AND qty_mid >= 1 AND qty_hi >= 1
),
first_per_event AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, k_mid ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_event WHERE rn = 1),
sett AS (
  SELECT e.*, s_lo.result AS r_lo, s_mid.result AS r_mid, s_hi.result AS r_hi,
         LEAST(3, e.qty_lo, e.qty_mid, e.qty_hi) AS qty,
         -- Payoff per share
         ((CASE WHEN s_lo.result='yes'  THEN  1.0 ELSE 0.0 END) +
          (CASE WHEN s_mid.result='yes' THEN -1.0 ELSE 0.0 END) +
          (CASE WHEN s_hi.result='yes'  THEN  1.0 ELSE 0.0 END)) +
         -- Cash position
         e.yb_mid - e.ya_lo - e.ya_hi
         -- Fees: 3 contracts
         - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_mid) - kalshi_fee(e.ya_hi) AS pnl_per_unit
  FROM e
  JOIN settlements s_lo  ON s_lo.market_ticker = e.mkt_lo
  JOIN settlements s_mid ON s_mid.market_ticker = e.mkt_mid
  JOIN settlements s_hi  ON s_hi.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_unit > 0),
       SUM(pnl_per_unit * qty), AVG(pnl_per_unit), STDDEV(pnl_per_unit),
       SUM(qty), AVG(qty) FROM sett
''')

_run('W2 antiC>=5c', 'Reverse butterfly: ≥5c anti-convexity', '''
WITH cand AS (
  SELECT * FROM w2_triples
  WHERE antiC_amount >= 0.05
    AND qty_lo >= 1 AND qty_mid >= 1 AND qty_hi >= 1
),
first_per_event AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, k_mid ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_event WHERE rn = 1),
sett AS (
  SELECT e.*, s_lo.result AS r_lo, s_mid.result AS r_mid, s_hi.result AS r_hi,
         LEAST(3, e.qty_lo, e.qty_mid, e.qty_hi) AS qty,
         ((CASE WHEN s_lo.result='yes'  THEN  1.0 ELSE 0.0 END) +
          (CASE WHEN s_mid.result='yes' THEN -1.0 ELSE 0.0 END) +
          (CASE WHEN s_hi.result='yes'  THEN  1.0 ELSE 0.0 END)) +
         e.yb_mid - e.ya_lo - e.ya_hi
         - kalshi_fee(e.ya_lo) - kalshi_fee(1.0 - e.yb_mid) - kalshi_fee(e.ya_hi) AS pnl_per_unit
  FROM e
  JOIN settlements s_lo  ON s_lo.market_ticker = e.mkt_lo
  JOIN settlements s_mid ON s_mid.market_ticker = e.mkt_mid
  JOIN settlements s_hi  ON s_hi.market_ticker = e.mkt_hi
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_unit > 0),
       SUM(pnl_per_unit * qty), AVG(pnl_per_unit), STDDEV(pnl_per_unit),
       SUM(qty), AVG(qty) FROM sett
''')

# ═══════════════════════════════════════════════════════════════════
# W3. COINBASE-LAG ADAPTIVE T2
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W3 — Coinbase-lag-adaptive T2')
print('═' * 80)

# Build per-second CB lag aggregate
ana.execute('''
CREATE OR REPLACE TABLE cb_lag_per_sec AS
SELECT CAST(received_at_ns / 1000000000 AS BIGINT) AS ts_sec,
       AVG((received_at_ns/1e9 - EXTRACT(EPOCH FROM CAST(exchange_time AS TIMESTAMP))) * 1000) AS avg_lag_ms,
       MAX((received_at_ns/1e9 - EXTRACT(EPOCH FROM CAST(exchange_time AS TIMESTAMP))) * 1000) AS max_lag_ms,
       COUNT(*) AS ticks
FROM src.coinbase_ticker_all
WHERE exchange_time IS NOT NULL
GROUP BY 1
''')
print(f'  cb_lag_per_sec rows: {ana.execute("SELECT COUNT(*) FROM cb_lag_per_sec").fetchone()[0]:,}')

# Restrict T2 to LOW-lag windows (avg_lag_ms <= 400)
_run('W3 T2-cb-fresh', 'T2 in CB low-lag (<=400ms median) windows', '''
WITH cand AS (
  SELECT swf.* FROM snap_with_fair swf
  JOIN cb_lag_per_sec cl ON cl.ts_sec = swf.ts_sec
  WHERE cl.avg_lag_ms <= 400
    AND swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
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

_run('W3 T2-cb-stale', 'T2 in CB HIGH-lag (>500ms median) windows (sanity)', '''
WITH cand AS (
  SELECT swf.* FROM snap_with_fair swf
  JOIN cb_lag_per_sec cl ON cl.ts_sec = swf.ts_sec
  WHERE cl.avg_lag_ms > 500
    AND swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
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
# W4. COINBASE SPREAD WIDENING SIGNAL
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W4 — CB Spread Widening')
print('═' * 80)

ana.execute('''
CREATE OR REPLACE TABLE cb_spread_per_sec AS
SELECT CAST(received_at_ns / 1000000000 AS BIGINT) AS ts_sec,
       AVG(best_ask - best_bid) AS avg_spread,
       MAX(best_ask - best_bid) AS max_spread
FROM src.coinbase_ticker_all
WHERE best_bid > 1000 AND best_ask > 1000 AND best_ask > best_bid
GROUP BY 1
''')

# Wide CB spread (>$2) usually indicates uncertainty → Kalshi MMs widen too.
# At those moments, deep-ITM contracts might briefly have larger spreads we
# can buy through.
_run('W4 cb-wide-T2', 'T2 only when CB spread >$2 (high uncertainty)', '''
WITH cand AS (
  SELECT swf.* FROM snap_with_fair swf
  JOIN cb_spread_per_sec cs ON cs.ts_sec = swf.ts_sec
  WHERE cs.avg_spread >= 2.0
    AND swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
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

_run('W4 cb-tight-T2', 'T2 only when CB spread <$1 (calm)', '''
WITH cand AS (
  SELECT swf.* FROM snap_with_fair swf
  JOIN cb_spread_per_sec cs ON cs.ts_sec = swf.ts_sec
  WHERE cs.avg_spread <= 1.0
    AND swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
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
# W5. BROWNIAN BRIDGE FAIR VALUE for last-15-min T2
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W5 — Brownian Bridge fair value (T2 near close)')
print('═' * 80)

# Brownian bridge: at time t, spot is S_t, expected at close = S_t * exp(-r*(T-t))
# but for short horizons r≈0, so E[S_close|S_t] = S_t.
# Variance under bridge from now to close = sigma^2 * (T-t).
# For YES = P(S_close > K | S_t), with drift=0:
#   d = (S_t - K) / (sigma * sqrt(T-t))
#   P(yes) = N(d)  if S_t > K
# This is essentially the same as plain BS for short horizons.
# BUT: for last 15min specifically, the EWMA σ has been estimating with
# noise → use a TIGHTER σ estimate (last 15min realized only).

# Build "near close" T2 candidates with TIGHTER sigma
ana.execute('''
CREATE OR REPLACE TABLE w5_bridge_signal AS
WITH base AS (
  SELECT s.*,
         LAG(s.btc_spot, 60) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_60s
  FROM strike_snaps_1s_clean s
  WHERE s.secs_to_close BETWEEN 60 AND 900   -- last 15min
    AND s.yes_ask BETWEEN 0.85 AND 0.98
    AND s.yes_ask_qty >= 1
    AND s.btc_spot > 1000
),
with_rv AS (
  SELECT b.*, evt.realized_vol_60min,
         -- Compute σ_remain using rv60min (per-sec stddev × √horizon)
         b.btc_spot * COALESCE(evt.realized_vol_60min, 0.0005) * SQRT(GREATEST(1, b.secs_to_close)) AS sig_rem_bridge
  FROM base b
  LEFT JOIN event_realized_vol evt
    ON evt.event_ticker = b.event_ticker AND evt.ts_sec = b.ts_sec
),
fair_calc AS (
  SELECT *,
         CASE WHEN btc_spot > strike AND sig_rem_bridge > 0
              THEN norm_cdf(ABS(btc_spot - strike) / sig_rem_bridge)
              WHEN btc_spot <= strike AND sig_rem_bridge > 0
              THEN 1.0 - norm_cdf(ABS(btc_spot - strike) / sig_rem_bridge)
              ELSE NULL
         END AS fair_yes_bridge
  FROM with_rv
)
SELECT * FROM fair_calc WHERE fair_yes_bridge IS NOT NULL
''')

_run('W5 bridge-T2', 'Bridge T2 last 15min, bridge_fair-ask>=1c', '''
WITH cand AS (
  SELECT * FROM w5_bridge_signal
  WHERE fair_yes_bridge >= 0.96
    AND (fair_yes_bridge - yes_ask) >= 0.01
    AND btc_spot > strike
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
# W6. HOUR-OF-DAY pattern
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W6 — Hour-of-day pattern')
print('═' * 80)

# Extract hour from event_ticker (KXBTCD-26MAY0608 → hour 08)
# Check T2 PnL by hour. BTC has known intraday cyclicality (Asian/EU/US sessions).
ana.execute('''
CREATE OR REPLACE TABLE hourly_t2_results AS
WITH t2_trades AS (
  SELECT swf.*,
         CAST(SUBSTR(swf.event_ticker, -2) AS INTEGER) AS hour_utc,
         f.yes_ask AS fill_ya, f.yes_ask_qty AS fill_qty
  FROM snap_with_fair swf
  JOIN strike_snaps_1s_clean f
    ON f.market_ticker = swf.market_ticker AND f.ts_sec = swf.ts_sec + 1
  WHERE swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
    AND f.yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM t2_trades
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
sett AS (
  SELECT e.*, s.result,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT hour_utc, COUNT(*) AS n,
       COUNT(*) FILTER(WHERE pnl_per_c > 0) AS wins,
       SUM(pnl_per_c) AS total_pnl,
       AVG(pnl_per_c) AS avg_pnl
FROM sett GROUP BY hour_utc ORDER BY hour_utc
''')
print('  T2 by hour-of-day:')
print(ana.execute('SELECT * FROM hourly_t2_results').df().to_string())

# Best 8 hours
print('\n  Best 8 hours by total PnL:')
best_hours = ana.execute('''
SELECT hour_utc FROM hourly_t2_results
WHERE n >= 3 ORDER BY total_pnl DESC LIMIT 8
''').fetchall()
best_h_list = [h[0] for h in best_hours]
print(f'  Best hours: {best_h_list}')

# Run T2 restricted to best hours
hours_str = ','.join(str(h) for h in best_h_list)
_run('W6 T2-best-hours', f'T2 only in best 8 hours: {best_h_list[:3]}…', f'''
WITH cand AS (
  SELECT swf.*, CAST(SUBSTR(swf.event_ticker, -2) AS INTEGER) AS hour_utc
  FROM snap_with_fair swf
  WHERE swf.secs_to_close BETWEEN 1800 AND 3600
    AND swf.fair_yes >= 0.94
    AND swf.yes_ask BETWEEN 0.88 AND 0.97
    AND ABS(swf.btc_spot - swf.strike) >= 200
    AND (swf.fair_yes - swf.yes_ask) >= 0.005
    AND swf.yes_ask_qty >= 1
),
filtered AS (SELECT * FROM cand WHERE hour_utc IN ({hours_str})),
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
# W7. FIRST-ACTIVE-TICK pricing inefficiency
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W7 — First-active-tick: trade markets in their first 60s after activation')
print('═' * 80)

# Get activation timestamps from lifecycle
ana.execute('''
CREATE OR REPLACE TABLE market_activation AS
SELECT market_ticker, MIN(received_at_ns) AS activation_ns,
       CAST(MIN(received_at_ns) / 1000000000 AS BIGINT) AS activation_sec
FROM src.ws_lifecycle_all
WHERE event_type = 'activated'
GROUP BY market_ticker
''')
print(f'  market_activation rows: {ana.execute("SELECT COUNT(*) FROM market_activation").fetchone()[0]:,}')

# A market in its first 60s — buy YES at any deep-ITM contract
_run('W7 first-60s-itm', 'In first 60s of activation, fair>=0.95, buy YES', '''
WITH cand AS (
  SELECT swf.*, ma.activation_sec,
         swf.ts_sec - ma.activation_sec AS age_sec
  FROM snap_with_fair swf
  JOIN market_activation ma ON ma.market_ticker = swf.market_ticker
  WHERE swf.ts_sec - ma.activation_sec BETWEEN 0 AND 60
    AND swf.fair_yes >= 0.95
    AND swf.yes_ask BETWEEN 0.85 AND 0.98
    AND swf.yes_ask_qty >= 1
    AND swf.btc_spot > swf.strike
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

# Same for OTM NO side
_run('W7 first-60s-otm', 'In first 60s of activation, fair_no>=0.95, buy NO', '''
WITH cand AS (
  SELECT swf.*, ma.activation_sec,
         swf.ts_sec - ma.activation_sec AS age_sec
  FROM snap_with_fair swf
  JOIN market_activation ma ON ma.market_ticker = swf.market_ticker
  WHERE swf.ts_sec - ma.activation_sec BETWEEN 0 AND 60
    AND (1.0 - swf.fair_yes) >= 0.95
    AND swf.yes_bid BETWEEN 0.02 AND 0.15
    AND swf.yes_bid_qty >= 1
    AND swf.btc_spot < swf.strike
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
# W8. ADJACENT-EVENT INHERITANCE (when event N+1 just opened)
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' W8 — Adjacent-event inheritance / settlement-into-open carry')
print('═' * 80)

# When event N settles at spot S, then event N+1 has its strikes available.
# In the first few seconds, the strikes of event N+1 should reflect S exactly.
# Edge: any inefficiency in the transition.
# Strategy: in event N+1's first 60s, find strikes ITM at S minus margin and
# buy YES at low prices.

# Use event_settlement_spot + event_close
_run('W8 carry-itm', 'Event N+1, first 90s, strike < prev_settle_spot - $300', '''
WITH event_seq AS (
  SELECT ec.event_ticker, ec.close_ts_unix, ess.settle_spot,
         LEAD(ec.close_ts_unix) OVER (ORDER BY ec.close_ts_unix) AS next_close_unix,
         LEAD(ec.event_ticker) OVER (ORDER BY ec.close_ts_unix) AS next_event,
         LEAD(ess.settle_spot) OVER (ORDER BY ec.close_ts_unix) AS next_settle_spot
  FROM event_close ec
  JOIN event_settlement_spot ess ON ess.event_ticker = ec.event_ticker
),
sigs AS (
  SELECT swf.market_ticker, swf.event_ticker, swf.ts_sec, swf.strike, swf.btc_spot,
         swf.yes_ask, swf.yes_ask_qty, swf.secs_to_close,
         es.settle_spot AS prev_settle
  FROM snap_with_fair swf
  JOIN event_seq es ON es.next_event = swf.event_ticker
  -- First 90s after the event's market activation
  WHERE swf.secs_to_close BETWEEN 3510 AND 3600
    AND swf.strike < es.settle_spot - 300  -- ITM by $300+
    AND swf.yes_ask BETWEEN 0.85 AND 0.97
    AND swf.yes_ask_qty >= 1
),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM sigs
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

# ═══════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 100)
print(' ROUND 4 SUMMARY')
print('═' * 100)
print(f'{"hyp":<26} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"t":>7} {"qty":>5}  desc')
print('─' * 110)

for r in sorted(RESULTS, key=lambda x: x.get('total_pnl', 0), reverse=True):
    star = '★' if (r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20) else ' '
    sign = '+' if r['t_stat'] >= 0 else ''
    print(f'{star} {r["hyp"]:<24} {r["n"]:>5} {r["win_pct"]:>5.1f}% {r["total_pnl"]:>+9.2f} '
          f'{r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f} {sign}{r["t_stat"]:>5.2f} '
          f'{r["avg_qty"]:>5.1f}  {r["desc"][:42]}')

winners = [r for r in RESULTS if r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies passed criteria')

Path('backtest_outputs/v3/round4_results.json').write_text(json.dumps(RESULTS, indent=2))
ana.close()
