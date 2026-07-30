"""V3 Round 2 — fade variants + new ideas.

Round 1 result: 0 NEW edges found. 1 baseline variant (H31a = tighter T1) works.
9/10 hypotheses lost. Several had high win rate but losing PnL (fee floor +
asymmetric loss). Round 2 strategy:

  A. INVERT the losing signals (H34, H38, H40) — if they're consistently
     wrong, the opposite is right.
  B. New ideas using untapped data:
     - ws_orderbook_delta_all (large limit-order additions = imminent move)
     - order_decision_all (skip-reasons that would have been profitable)
     - signal_scan_all (live bot's candidate stream)
     - hourly close auction microstructure (last 30s)
     - Spot-Kalshi divergence MEAN REVERSION (not continuation)
"""
import duckdb, json, math
from pathlib import Path

print('Connecting…')
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
        print(f'  {star} [{name:18}] n={n:>4} win={win_pct:>5.1f}% '
              f'total=${total:>+7.2f} avg/c=${avg_c:>+.4f} '
              f't={sign}{t_stat:>5.2f} qty={avg_qty:.1f}  {desc[:50]}')
        return RESULTS[-1]
    except Exception as e:
        print(f'  [{name:18}] ERROR: {str(e)[:140]}')
        return None

# ═══════════════════════════════════════════════════════════════════
# A. INVERT THE LOSERS
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' A1 — H34 INVERTED: fade the cross-strike markup')
print('═' * 80)

# If H34 mark-up (buy YES one strike beyond) lost -$129 with 37.6% win,
# the right play might be: when there's coordinated markup, FADE it (buy NO at the
# very strike that got marked up).
_run('H34i mark-up-fade', '>=3 strikes marked up → buy NO at center of band', '''
WITH flag AS (
  SELECT * FROM event_markup_count WHERE n_up >= 3 AND n_dn <= 1
),
sigs AS (
  -- Pick the strike that ITSELF marked up (highest individual markup)
  SELECT f.event_ticker, f.ts_sec, f.btc_spot,
         (SELECT s.market_ticker FROM strike_markup_10s s
          WHERE s.event_ticker = f.event_ticker AND s.ts_sec = f.ts_sec
            AND s.yes_bid - s.yb_10s_ago >= 0.005
            AND s.yes_bid BETWEEN 0.35 AND 0.75
            AND s.secs_to_close BETWEEN 600 AND 3300
            AND s.yes_bid_qty >= 1
          ORDER BY s.yes_bid - s.yb_10s_ago DESC LIMIT 1) AS market_ticker
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

_run('H34i mark-dn-fade', '>=3 strikes marked dn → buy YES at center of band', '''
WITH flag AS (
  SELECT * FROM event_markup_count WHERE n_dn >= 3 AND n_up <= 1
),
sigs AS (
  SELECT f.event_ticker, f.ts_sec, f.btc_spot,
         (SELECT s.market_ticker FROM strike_markup_10s s
          WHERE s.event_ticker = f.event_ticker AND s.ts_sec = f.ts_sec
            AND s.yes_bid - s.yb_10s_ago <= -0.005
            AND s.yes_ask BETWEEN 0.25 AND 0.65
            AND s.secs_to_close BETWEEN 600 AND 3300
            AND s.yes_ask_qty >= 1
          ORDER BY s.yes_bid - s.yb_10s_ago ASC LIMIT 1) AS market_ticker
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

print('\n' + '═' * 80)
print(' A2 — H38 INVERTED: depth imbalance fade')
print('═' * 80)

# bid_qty >= 5x ask_qty → original was buy YES (-$200). Try buy NO.
_run('H38i bid5x-fade', 'bid_qty>=5x ask_qty → fade by buying NO', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3600
    AND yes_bid BETWEEN 0.20 AND 0.80
    AND yes_bid_qty >= 50 AND yes_ask_qty >= 1
    AND yes_bid_qty >= 5 * yes_ask_qty
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

_run('H38i ask5x-fade', 'ask_qty>=5x bid_qty → fade by buying YES', '''
WITH cand AS (
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3600
    AND yes_ask BETWEEN 0.20 AND 0.80
    AND yes_ask_qty >= 50 AND yes_bid_qty >= 1
    AND yes_ask_qty >= 5 * yes_bid_qty
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

print('\n' + '═' * 80)
print(' A3 — H40 INVERTED: curve deviation IS information, follow it')
print('═' * 80)

# H40 over (yes_mid >= smoothed+1.5c, buy NO): -$106. So fade the smoothing —
# trust the price not the neighbors. Buy YES when overpriced.
_run('H40i over-follow', 'yes_mid >= smoothed+1.5c → buy YES (trust price)', '''
WITH cand AS (
  SELECT *, yes_mid - smoothed_mid AS dev FROM smoothed_curve
  WHERE smoothed_mid IS NOT NULL
    AND yes_mid - smoothed_mid >= 0.015
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

_run('H40i under-follow', 'yes_mid <= smoothed-1.5c → buy NO (trust price)', '''
WITH cand AS (
  SELECT *, smoothed_mid - yes_mid AS dev FROM smoothed_curve
  WHERE smoothed_mid IS NOT NULL
    AND smoothed_mid - yes_mid >= 0.015
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

# ═══════════════════════════════════════════════════════════════════
# B. NEW IDEAS
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 80)
print(' B1 — Large limit-order delta as imminent-move signal')
print('═' * 80)

# When a single order delta adds >=20 contracts at a price near inside, it's
# a signal someone has conviction. Trade with the side they're adding to.
# Note: ws_orderbook_delta_all has rows per order book change.
print('  Building delta features...')

ana.execute('''
CREATE OR REPLACE TABLE big_delta AS
SELECT received_at_ns, market_ticker, side, price, delta_qty,
       received_at_ns / 1000000000 AS ts_sec
FROM src.ws_orderbook_delta_all
WHERE delta_qty >= 20 AND price BETWEEN 5 AND 95
''')
print(f'  big_delta: {ana.execute("SELECT COUNT(*) FROM big_delta").fetchone()[0]:,} rows')

# Join with snap to figure out side bought/sold and project a settle
_run('B1 big-yes-add', 'YES-side add of >=20 contracts → buy YES at next ask', '''
WITH cand AS (
  SELECT b.market_ticker, b.ts_sec, b.price, b.delta_qty,
         s.yes_ask, s.yes_ask_qty, s.yes_bid, s.yes_bid_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM big_delta b
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = b.market_ticker AND s.ts_sec = b.ts_sec
  WHERE b.side = 'yes' AND b.delta_qty >= 30
    AND s.secs_to_close BETWEEN 600 AND 3300
    AND s.yes_ask BETWEEN 0.20 AND 0.80
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

_run('B1 big-no-add', 'NO-side add of >=20 contracts → buy NO', '''
WITH cand AS (
  SELECT b.market_ticker, b.ts_sec, b.price, b.delta_qty,
         s.yes_bid, s.yes_bid_qty, s.yes_ask, s.yes_ask_qty, s.secs_to_close, s.btc_spot, s.strike
  FROM big_delta b
  JOIN strike_snaps_1s_clean s
    ON s.market_ticker = b.market_ticker AND s.ts_sec = b.ts_sec
  WHERE b.side = 'no' AND b.delta_qty >= 30
    AND s.secs_to_close BETWEEN 600 AND 3300
    AND s.yes_bid BETWEEN 0.20 AND 0.80
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

print('\n' + '═' * 80)
print(' B2 — Spot–Kalshi divergence MEAN REVERSION')
print('═' * 80)

# H32 tested CONTINUATION (Kalshi leads spot). Here: REVERSION — when
# |spot - atm_strike| > $200 (Kalshi is "behind" spot), expect Kalshi to
# catch up. Buy YES at the strike spot is now beyond.
_run('B2 spot-ahead-up', 'spot - atm_strike > $200 → buy YES at ATM_old', '''
WITH cand AS (
  SELECT i.*, i.btc_spot - i.atm_strike AS divergence
  FROM implied_spot i
  WHERE i.btc_spot - i.atm_strike >= 200
    AND i.atm_strike < i.btc_spot
),
-- buy YES at the strike right below spot (likely to be ITM as Kalshi catches up)
sigs AS (
  SELECT c.event_ticker, c.ts_sec, c.btc_spot, c.atm_strike,
         (SELECT s.market_ticker FROM strike_snaps_1s_clean s
          WHERE s.event_ticker = c.event_ticker AND s.ts_sec = c.ts_sec
            AND s.strike = c.atm_strike  -- buy at the ATM strike that's now ITM
            AND s.yes_ask BETWEEN 0.40 AND 0.65
            AND s.secs_to_close BETWEEN 600 AND 3300
            AND s.yes_ask_qty >= 1
          LIMIT 1) AS market_ticker
  FROM cand c
),
joined AS (
  SELECT s.market_ticker, s.ts_sec, b.yes_ask, b.yes_ask_qty
  FROM sigs s JOIN strike_snaps_1s_clean b
    ON b.market_ticker = s.market_ticker AND b.ts_sec = s.ts_sec + 1
  WHERE b.yes_ask_qty >= 1 AND s.market_ticker IS NOT NULL
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

_run('B2 spot-ahead-dn', 'atm_strike - spot > $200 → buy NO at ATM', '''
WITH cand AS (
  SELECT i.*, i.atm_strike - i.btc_spot AS divergence
  FROM implied_spot i
  WHERE i.atm_strike - i.btc_spot >= 200
    AND i.atm_strike > i.btc_spot
),
sigs AS (
  SELECT c.event_ticker, c.ts_sec, c.btc_spot, c.atm_strike,
         (SELECT s.market_ticker FROM strike_snaps_1s_clean s
          WHERE s.event_ticker = c.event_ticker AND s.ts_sec = c.ts_sec
            AND s.strike = c.atm_strike
            AND s.yes_bid BETWEEN 0.35 AND 0.60
            AND s.secs_to_close BETWEEN 600 AND 3300
            AND s.yes_bid_qty >= 1
          LIMIT 1) AS market_ticker
  FROM cand c
),
joined AS (
  SELECT s.market_ticker, s.ts_sec, b.yes_bid, b.yes_bid_qty
  FROM sigs s JOIN strike_snaps_1s_clean b
    ON b.market_ticker = s.market_ticker AND b.ts_sec = s.ts_sec + 1
  WHERE b.yes_bid_qty >= 1 AND s.market_ticker IS NOT NULL
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

print('\n' + '═' * 80)
print(' B3 — Implied prob MEAN REVERSION over 5 minutes')
print('═' * 80)

# When yes_mid moved >0.10 in 5min (without spot crossing strike), expect partial fade.
ana.execute('''
CREATE OR REPLACE TABLE mid_5min AS
SELECT market_ticker, event_ticker, ts_sec, btc_spot, strike, secs_to_close,
       yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
       (yes_ask + yes_bid) / 2 AS yes_mid,
       LAG((yes_ask + yes_bid)/2, 300) OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS mid_5m_ago,
       LAG(btc_spot, 300) OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS spot_5m_ago
FROM strike_snaps_1s_clean
WHERE yes_ask IS NOT NULL AND yes_bid IS NOT NULL
''')

_run('B3 mid-up-fade', 'yes_mid up >10c in 5min without spot crossing → buy NO', '''
WITH cand AS (
  SELECT * FROM mid_5min
  WHERE mid_5m_ago IS NOT NULL AND spot_5m_ago IS NOT NULL
    AND yes_mid - mid_5m_ago >= 0.10
    AND yes_bid BETWEEN 0.40 AND 0.75
    AND yes_bid_qty >= 1
    AND secs_to_close BETWEEN 600 AND 3300
    -- spot did NOT cross strike (no fundamental cause)
    AND ((spot_5m_ago > strike AND btc_spot > strike) OR (spot_5m_ago < strike AND btc_spot < strike))
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

_run('B3 mid-dn-fade', 'yes_mid dn >10c in 5min without spot crossing → buy YES', '''
WITH cand AS (
  SELECT * FROM mid_5min
  WHERE mid_5m_ago IS NOT NULL AND spot_5m_ago IS NOT NULL
    AND yes_mid - mid_5m_ago <= -0.10
    AND yes_ask BETWEEN 0.25 AND 0.60
    AND yes_ask_qty >= 1
    AND secs_to_close BETWEEN 600 AND 3300
    AND ((spot_5m_ago > strike AND btc_spot > strike) OR (spot_5m_ago < strike AND btc_spot < strike))
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

print('\n' + '═' * 80)
print(' B4 — Close-auction widening (last 60s before settlement)')
print('═' * 80)

# In last 60s, market makers often widen and pull. If we can buy NO at very
# low yes_bid (e.g. <0.05) on an OTM contract where spot is clearly < strike,
# that's almost-guaranteed settle to NO.
# This data may be truncated — check what's available.
_run('B4 close-no-otm', 'TTC 30-180s, yes_bid<0.08, spot<strike-100 → buy NO', '''
WITH cand AS (
  SELECT * FROM strike_snaps_1s_clean
  WHERE secs_to_close BETWEEN 30 AND 180
    AND yes_bid BETWEEN 0.01 AND 0.08
    AND btc_spot < strike - 100
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

_run('B4 close-yes-itm', 'TTC 30-180s, yes_ask 0.92-0.98, spot>strike+100 → buy YES', '''
WITH cand AS (
  SELECT * FROM strike_snaps_1s_clean
  WHERE secs_to_close BETWEEN 30 AND 180
    AND yes_ask BETWEEN 0.92 AND 0.98
    AND btc_spot > strike + 100
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
  SELECT f.*, s.result, LEAST(5, f.fill_qty) AS qty,
         CASE WHEN s.result='yes' THEN 1.0 - fill_ya - kalshi_fee(fill_ya)
              ELSE -fill_ya - kalshi_fee(fill_ya) END AS pnl_per_c
  FROM fill f JOIN settlements s ON s.market_ticker = f.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_per_c > 0),
       SUM(pnl_per_c * qty), AVG(pnl_per_c), STDDEV(pnl_per_c),
       SUM(qty), AVG(qty) FROM sett
''')

print('\n' + '═' * 80)
print(' B5 — Persistence-of-NO continuation (T3 generalization)')
print('═' * 80)

# T3 in live bot uses 5-min persistence. Test 10-min and 15-min persistence
# windows with tighter price ranges.
_run('B5 persist-10m', '10-min OTM persistence, NO at yes_bid 0.10-0.25', '''
WITH cand AS (
  SELECT s.*,
         LAG(s.btc_spot, 600) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_10m,
         LAG(s.btc_spot, 300) OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_5m,
         LAG(s.btc_spot, 60)  OVER (PARTITION BY s.event_ticker ORDER BY s.ts_sec) AS spot_1m
  FROM strike_snaps_1s_clean s
  WHERE s.secs_to_close BETWEEN 600 AND 1800
    AND s.yes_bid BETWEEN 0.10 AND 0.25
    AND s.strike > s.btc_spot + 150
    AND s.yes_bid_qty >= 1
),
persisted AS (
  SELECT * FROM cand
  WHERE spot_10m IS NOT NULL AND spot_5m IS NOT NULL AND spot_1m IS NOT NULL
    AND spot_10m < strike - 50  AND spot_5m < strike - 50 AND spot_1m < strike - 50
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
# REPORT
# ═══════════════════════════════════════════════════════════════════
print('\n' + '═' * 100)
print(' ROUND 2 SUMMARY — sorted by t-stat (★ = win≥60%, total>$5, t>2, n≥20)')
print('═' * 100)
print(f'{"hyp":<20} {"n":>5} {"win%":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9} {"t":>7} {"qty":>5}  desc')
print('─' * 100)

for r in sorted(RESULTS, key=lambda x: x.get('t_stat', 0), reverse=True):
    star = '★' if (r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20) else ' '
    sign = '+' if r['t_stat'] >= 0 else ''
    print(f'{star} {r["hyp"]:<18} {r["n"]:>5} {r["win_pct"]:>5.1f}% {r["total_pnl"]:>+9.2f} '
          f'{r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f} {sign}{r["t_stat"]:>5.2f} '
          f'{r["avg_qty"]:>5.1f}  {r["desc"][:42]}')

winners = [r for r in RESULTS if r['win_pct'] >= 60 and r['total_pnl'] > 5 and r['t_stat'] > 2 and r['n'] >= 20]
print(f'\n>>> {len(winners)} new strategies passed: win≥60%, total>$5, t>2, n≥20')
if winners:
    print('Winners:')
    for w in winners:
        print(f'  {w["hyp"]}: n={w["n"]}, win={w["win_pct"]:.1f}%, '
              f'pnl=${w["total_pnl"]:.2f}, t={w["t_stat"]:.2f}')

Path('backtest_outputs/v3/round2_results.json').write_text(json.dumps(RESULTS, indent=2))
print('Wrote backtest_outputs/v3/round2_results.json')

ana.close()
