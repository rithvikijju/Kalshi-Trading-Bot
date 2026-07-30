"""Round 2: ATM/near-ATM directional strategies where EV math is favorable.

Key insight from round 1:
  Buy at price p, fee = 7c. Breakeven win rate = p + 0.07.
  → At $0.50: need 57% win. At $0.65: need 72% win. At $0.85: need 92%.
  → Deep-ITM is mathematically punishing. ATM is where edge can be captured.

These tests focus on ATM (0.35-0.55) and mid (0.55-0.65) with directional signals.
"""
import duckdb
ana = duckdb.connect('backtest_outputs/analysis.duckdb')
ana.execute("ATTACH 'live_capture_gapless_20260512_paused.duckdb' AS src (READ_ONLY)")

ana.execute('CREATE OR REPLACE MACRO kalshi_fee(p) AS LEAST(0.07, 0.07 * GREATEST(0, LEAST(1, p)) / 0.50)')

RESULTS = []
def run(name, query, desc=''):
    try:
        r = ana.execute(query).fetchone()
        if r is None or r[0] is None or r[0] == 0:
            RESULTS.append({'hyp': name, 'desc': desc, 'n': 0, 'win': 0,
                            'total': 0.0, 'per_day': 0.0, 'avg_c': 0.0, 'contracts': 0})
            print(f'  [{name:18}] n=0  {desc}'); return
        n = r[0] or 0; wins = r[1] or 0
        total = float(r[2] or 0); avg = float(r[3] or 0)
        contracts = int(r[4] or 0)
        win_pct = wins/n*100 if n else 0
        # breakeven analysis
        avg_price = float(r[6] or 0)
        breakeven_wr = (avg_price + 0.07) * 100  # required win rate
        RESULTS.append({'hyp': name, 'desc': desc, 'n': n, 'win': win_pct,
                        'total': total, 'per_day': total/7, 'avg_c': avg, 'contracts': contracts,
                        'avg_price': avg_price, 'breakeven_wr': breakeven_wr})
        edge_vs_be = win_pct - breakeven_wr
        ind = '★' if win_pct >= 60 and total > 5 else ' '
        print(f'  {ind} [{name:18}] n={n:>5} win={win_pct:>5.1f}% (BE@{breakeven_wr:.0f}%, edge{edge_vs_be:+5.1f}%) '
              f'total=${total:>+7.2f}/day=${total/7:>+5.2f}  {desc}')
    except Exception as e:
        print(f'  [{name:18}] ERROR: {str(e)[:120]}')

# Common framework: pnl + return avg_price too
def std_query(filter_sql, side='yes', max_qty=5):
    """Build a settlement PnL query for either side."""
    if side == 'yes':
        price_expr = 'yes_ask'
        qty_expr = 'COALESCE(yes_ask_qty, 1)'
        pnl_expr = '''CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
                          ELSE -yes_ask - kalshi_fee(yes_ask) END'''
    else:  # 'no'
        price_expr = '(1.0 - yes_bid)'
        qty_expr = 'COALESCE(yes_bid_qty, 1)'
        pnl_expr = '''CASE WHEN s.result='no' THEN 1.0 - (1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid)
                          ELSE -(1.0 - yes_bid) - kalshi_fee(1.0 - yes_bid) END'''
    return f'''
WITH cand AS ({filter_sql}),
first_per_mkt AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn FROM cand
),
e AS (SELECT * FROM first_per_mkt WHERE rn = 1),
p AS (
  SELECT e.*, s.result, LEAST({qty_expr}, {max_qty}) AS qty,
         {price_expr} AS entry_price,
         {pnl_expr} AS pnl_c
  FROM e JOIN settlements s ON s.market_ticker = e.market_ticker
)
SELECT COUNT(*), COUNT(*) FILTER(WHERE pnl_c > 0),
       SUM(pnl_c * qty), AVG(pnl_c), SUM(qty), STDDEV(pnl_c * qty),
       AVG(entry_price) FROM p
'''

# ───────────────────────────────────────────────────────────────────
# GROUP H: Simple ATM directional
# ───────────────────────────────────────────────────────────────────
print('═' * 75 + '\n GROUP H — Simple ATM directional')

# H1: Pure baseline: any ATM YES with price 0.40-0.55, no signal — test base rate
run('H1 ATM-yes-base', std_query('''
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.40 AND 0.55
    AND btc_spot > strike
'''), 'ATM YES baseline (no signal)')

# H2: Pure baseline NO at 0.40-0.55
run('H2 ATM-no-base', std_query('''
  SELECT * FROM snap_with_fair
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.45 AND 0.60  -- → no_price 0.40-0.55
    AND btc_spot < strike
''', side='no'), 'ATM NO baseline (no signal)')

# ───────────────────────────────────────────────────────────────────
# GROUP I: Spot persistence (spot has been on right side of strike for a while)
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP I — Spot persistence')

# Build persistence: how long has spot been > strike?
# Simpler approach: check spot 60s/120s/300s ago — was it ALSO > strike?
ana.execute('''
CREATE OR REPLACE TABLE snap_persistence AS
SELECT s.*,
       LAG(s.btc_spot, 60) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS spot_60s_ago,
       LAG(s.btc_spot, 180) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS spot_180s_ago,
       LAG(s.btc_spot, 300) OVER (PARTITION BY s.market_ticker ORDER BY s.ts_sec) AS spot_300s_ago
FROM snap_with_fair s
''')

# I1: Spot has been > strike for >=180s + ATM YES at 0.40-0.55 → buy YES
run('I1 persist180-yes', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.40 AND 0.58
    AND btc_spot > strike
    AND spot_180s_ago > strike  -- has been above for 3 min
'''), 'spot above K for ≥3min + ATM YES')

# I2: Spot has been < strike for >=180s + ATM NO → buy NO
run('I2 persist180-no', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.42 AND 0.60
    AND btc_spot < strike
    AND spot_180s_ago < strike
''', side='no'), 'spot below K for ≥3min + ATM NO')

# I3: Stronger persistence — 5 minutes
run('I3 persist300-yes', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.40 AND 0.58
    AND btc_spot > strike
    AND spot_300s_ago > strike
'''), 'spot above K for ≥5min + ATM YES')

run('I4 persist300-no', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.42 AND 0.60
    AND btc_spot < strike
    AND spot_300s_ago < strike
''', side='no'), 'spot below K for ≥5min + ATM NO')

# ───────────────────────────────────────────────────────────────────
# GROUP J: Combo — persistence + bid pressure
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP J — Persistence + book pressure')

# J1: persistence + bid pressure
run('J1 persist+bidQ', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.40 AND 0.60
    AND btc_spot > strike
    AND spot_180s_ago > strike
    AND yes_bid_qty > 0 AND yes_ask_qty > 0
    AND yes_bid_qty > 1.5 * yes_ask_qty
'''), 'persist3min + bid_qty>1.5×ask_qty YES')

run('J2 persist+askQ-no', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.40 AND 0.60
    AND btc_spot < strike
    AND spot_180s_ago < strike
    AND yes_bid_qty > 0 AND yes_ask_qty > 0
    AND yes_ask_qty > 1.5 * yes_bid_qty
''', side='no'), 'persist3min + ask_qty>1.5×bid_qty NO')

# ───────────────────────────────────────────────────────────────────
# GROUP K: Strong directional momentum sustained
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP K — Sustained directional momentum')

# K1: spot persistently rising over 5min (current > 60s ago > 300s ago) + ATM YES
run('K1 trend-up-atm', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.40 AND 0.60
    AND btc_spot > strike
    AND spot_300s_ago < btc_spot  -- spot rose over 5min
    AND spot_60s_ago < btc_spot   -- and recent 60s
    AND btc_spot - spot_300s_ago > 50  -- meaningful move
'''), 'sustained spot rise + ATM YES')

run('K2 trend-down-atm', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.40 AND 0.60
    AND btc_spot < strike
    AND spot_300s_ago > btc_spot
    AND spot_60s_ago > btc_spot
    AND spot_300s_ago - btc_spot > 50
''', side='no'), 'sustained spot drop + ATM NO')

# ───────────────────────────────────────────────────────────────────
# GROUP L: Slight ITM (price 0.55-0.65)
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP L — Slight ITM (need 62-72% win)')

# L1: persistence + price 0.55-0.65
run('L1 mid-yes-persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.55 AND 0.68
    AND btc_spot > strike
    AND spot_300s_ago > strike  -- 5min persistence
    AND spot_180s_ago > strike
'''), 'price 0.55-0.68 + spot persistent ITM ≥5min')

run('L2 mid-no-persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.32 AND 0.45  -- → no_price 0.55-0.68
    AND btc_spot < strike
    AND spot_300s_ago < strike
    AND spot_180s_ago < strike
''', side='no'), 'price 0.55-0.68 + spot persistent OTM ≥5min')

# ───────────────────────────────────────────────────────────────────
# GROUP M: Distance + persistence
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP M — Distance + persistence')

# M1: spot > strike by >=$200 for >=3min + ATM YES
run('M1 dist+persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.45 AND 0.65
    AND btc_spot - strike >= 200
    AND spot_180s_ago - strike >= 100  -- has been distinctly ITM
'''), 'dist≥$200 + persistent ITM + ATM YES')

run('M2 dist+persist-no', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.35 AND 0.55
    AND strike - btc_spot >= 200
    AND strike - spot_180s_ago >= 100
''', side='no'), 'dist≥$200 + persistent OTM + ATM NO')

# ───────────────────────────────────────────────────────────────────
# GROUP N: Final-window with adjusted boundaries
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP N — Late-event (TTC 6-15min)')

# Data ends at TTC 375s (6.25min), but use the absolute last available
run('N1 late-yes-persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 380 AND 900
    AND yes_ask BETWEEN 0.50 AND 0.75
    AND btc_spot > strike
    AND spot_60s_ago > strike
    AND btc_spot - strike >= 100
'''), '6-15min + ITM persistent + ATM-mid YES')

run('N2 late-no-persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 380 AND 900
    AND yes_bid BETWEEN 0.25 AND 0.50
    AND btc_spot < strike
    AND spot_60s_ago < strike
    AND strike - btc_spot >= 100
''', side='no'), '6-15min + OTM persistent + ATM-mid NO')

# ───────────────────────────────────────────────────────────────────
# GROUP O: Single best combo
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP O — Best combo')

run('O1 all-filters-yes', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800  -- 10-30min sweet spot
    AND yes_ask BETWEEN 0.50 AND 0.70
    AND btc_spot - strike >= 150
    AND spot_180s_ago - strike >= 50
    AND spot_300s_ago < btc_spot  -- and trending up
    AND yes_bid_qty > 0 AND yes_ask_qty > 0
    AND yes_bid_qty > yes_ask_qty  -- modest pressure
'''), '10-30m + dist + persist + trend + bid≥ask YES')

run('O2 all-filters-no', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800
    AND yes_bid BETWEEN 0.30 AND 0.50
    AND strike - btc_spot >= 150
    AND strike - spot_180s_ago >= 50
    AND spot_300s_ago > btc_spot
    AND yes_bid_qty > 0 AND yes_ask_qty > 0
    AND yes_ask_qty > yes_bid_qty
''', side='no'), '10-30m + dist + persist + trend + ask≥bid NO')

# ───────────────────────────────────────────────────────────────────
# REPORT
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 110)
print(' SUMMARY — sorted by total PnL (★ = win≥60%, total>$5, n≥20)')
print('═' * 110)
print(f'{"hyp":<20} {"n":>5} {"win%":>6} {"BE%":>5} {"edge":>6} {"total$":>9} {"$/day":>7} {"avg/c":>9}  desc')
print('─' * 110)
for r in sorted(RESULTS, key=lambda x: x['total'], reverse=True):
    star = '★' if r['win'] >= 60 and r['total'] > 5 and r['n'] >= 20 else ' '
    be = r.get('breakeven_wr', 0)
    edge = r['win'] - be
    print(f'{star} {r["hyp"]:<18} {r["n"]:>5} {r["win"]:>5.1f}% {be:>4.0f}% {edge:>+5.1f}% '
          f'{r["total"]:>+9.2f} {r["per_day"]:>+7.2f} {r["avg_c"]:>+9.4f}  {r["desc"][:48]}')

winners = [r for r in RESULTS if r['win'] >= 60 and r['total'] > 5 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies met goal: win≥60%, total>$5, n≥20')
if winners:
    print('\nWINNERS:')
    for w in winners:
        print(f'  {w["hyp"]:<18} {w["n"]} trades, {w["win"]:.0f}% win, +${w["total"]:.2f} '
              f'(${w["per_day"]:.2f}/day, {w["contracts"]} contracts) — {w["desc"]}')

ana.close()
