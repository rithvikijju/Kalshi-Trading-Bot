"""Round 3: Cheap OTM tickets where fees are small in absolute terms.

Fee at $0.15 = 2.1c. Breakeven win rate = 17%.
Fee at $0.30 = 4.2c. Breakeven win rate = 34%.

If we can find filters where actual win > implied, this could work.
"""
import duckdb
ana = duckdb.connect('backtest_outputs/analysis.duckdb')

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
        avg_price = float(r[6] or 0)
        breakeven_wr = (avg_price + kalshi_fee_for(avg_price)) * 100  # required win rate
        RESULTS.append({'hyp': name, 'desc': desc, 'n': n, 'win': win_pct,
                        'total': total, 'per_day': total/7, 'avg_c': avg, 'contracts': contracts,
                        'avg_price': avg_price, 'breakeven_wr': breakeven_wr})
        edge = win_pct - breakeven_wr
        ind = '★' if win_pct >= 60 and total > 5 else ' '
        print(f'  {ind} [{name:18}] n={n:>5} win={win_pct:>5.1f}% '
              f'(p={avg_price:.2f}, BE@{breakeven_wr:.0f}%, edge{edge:+5.1f}%) '
              f'total=${total:>+7.2f}/day=${total/7:>+5.2f}  {desc}')
    except Exception as e:
        print(f'  [{name:18}] ERROR: {str(e)[:120]}')

def kalshi_fee_for(p):
    return min(0.07, 0.07 * max(0, min(1, p)) / 0.50)

def std_query(filter_sql, side='yes', max_qty=5):
    if side == 'yes':
        price_expr = 'yes_ask'
        qty_expr = 'COALESCE(yes_ask_qty, 1)'
        pnl_expr = '''CASE WHEN s.result='yes' THEN 1.0 - yes_ask - kalshi_fee(yes_ask)
                          ELSE -yes_ask - kalshi_fee(yes_ask) END'''
    else:
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
# GROUP P: Cheap YES with strong upside signal
# ───────────────────────────────────────────────────────────────────
print('═' * 75 + '\n GROUP P — Cheap YES with momentum')

# P1: yes_ask 0.15-0.30 + spot above strike + spot trending up
run('P1 cheap-yes-trend', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.15 AND 0.32
    AND btc_spot > strike
    AND spot_60s_ago < btc_spot  -- trending up
    AND btc_spot - spot_60s_ago >= 20
'''), 'cheap YES 0.15-0.32 + spot trending up')

# P2: yes_ask 0.15-0.30 + spot persistently above strike for >= 3 min
run('P2 cheap-yes-persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.15 AND 0.32
    AND btc_spot > strike
    AND spot_180s_ago > strike
'''), 'cheap YES 0.15-0.32 + spot ITM ≥3min')

# P3: Same but tighter range
run('P3 cheap-yes-narrow', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_ask BETWEEN 0.20 AND 0.30
    AND btc_spot > strike
    AND spot_180s_ago > strike
    AND btc_spot - strike >= 50
'''), 'cheap YES 0.20-0.30 + ITM persist + dist≥$50')

# ───────────────────────────────────────────────────────────────────
# GROUP Q: Cheap NO with strong downside signal
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP Q — Cheap NO with momentum')

run('Q1 cheap-no-trend', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.68 AND 0.85
    AND btc_spot < strike
    AND spot_60s_ago > btc_spot  -- trending down
    AND spot_60s_ago - btc_spot >= 20
''', side='no'), 'cheap NO + spot trending down')

run('Q2 cheap-no-persist', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.68 AND 0.85
    AND btc_spot < strike
    AND spot_180s_ago < strike
''', side='no'), 'cheap NO + spot OTM ≥3min')

run('Q3 cheap-no-narrow', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 3300
    AND yes_bid BETWEEN 0.70 AND 0.80
    AND btc_spot < strike
    AND spot_180s_ago < strike
    AND strike - btc_spot >= 50
''', side='no'), 'cheap NO 0.20-0.30 + OTM persist + dist≥$50')

# ───────────────────────────────────────────────────────────────────
# GROUP R: Very cheap tickets (yes_ask 0.05-0.15)
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP R — Lottery tickets')

# Tickets at 5-15c need very low win rate (12-22%) to be profitable.
# But fair_yes for these strikes is naturally low. We need to find structural mispricing.
run('R1 cheap-yes-far', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 1800 AND 3300  -- early in event
    AND yes_ask BETWEEN 0.05 AND 0.15
    AND btc_spot < strike  -- OTM
    AND strike - btc_spot < 300  -- but not too far
'''), 'OTM YES 0.05-0.15, K within $300, early')

run('R2 cheap-no-far', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 1800 AND 3300
    AND yes_bid BETWEEN 0.85 AND 0.95
    AND btc_spot > strike
    AND btc_spot - strike < 300
''', side='no'), 'OTM NO 0.05-0.15, K within $300, early')

# ───────────────────────────────────────────────────────────────────
# GROUP S: Slightly OTM with directional kick
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP S — OTM + directional kick')

# S1: contract is OTM (spot < strike) but spot moving UP fast → maybe crosses
run('S1 otm-yes-rising', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 2400
    AND yes_ask BETWEEN 0.20 AND 0.40
    AND btc_spot < strike  -- OTM
    AND strike - btc_spot < 150  -- close to crossing
    AND spot_60s_ago < btc_spot - 30  -- moving up
'''), 'OTM YES + spot rising toward K')

run('S2 otm-no-falling', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 2400
    AND yes_bid BETWEEN 0.60 AND 0.80
    AND btc_spot > strike
    AND btc_spot - strike < 150
    AND spot_60s_ago > btc_spot + 30  -- moving down
''', side='no'), 'OTM NO + spot falling toward K')

# ───────────────────────────────────────────────────────────────────
# GROUP T: Modest ITM (0.60-0.75) with strong persistence
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP T — Modest ITM (need 67-82% win)')

run('T1 mid-itm-strong', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800
    AND yes_ask BETWEEN 0.60 AND 0.72
    AND btc_spot - strike >= 100
    AND spot_60s_ago > strike
    AND spot_180s_ago > strike
    AND spot_300s_ago > strike  -- 5 min persistence
    AND yes_ask_qty > 0
'''), '10-30m + ITM 5min persist + price 0.60-0.72 YES')

run('T2 mid-otm-strong', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800
    AND yes_bid BETWEEN 0.28 AND 0.40
    AND strike - btc_spot >= 100
    AND spot_60s_ago < strike
    AND spot_180s_ago < strike
    AND spot_300s_ago < strike
    AND yes_bid_qty > 0
''', side='no'), '10-30m + OTM 5min persist + price 0.60-0.72 NO')

# Tighter — distance ≥ $200
run('T3 mid-itm-dist200', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800
    AND yes_ask BETWEEN 0.55 AND 0.72
    AND btc_spot - strike >= 200
    AND spot_180s_ago - strike >= 100
'''), '10-30m + dist≥$200 + 3min persist YES')

run('T4 mid-otm-dist200', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800
    AND yes_bid BETWEEN 0.28 AND 0.45
    AND strike - btc_spot >= 200
    AND strike - spot_180s_ago >= 100
''', side='no'), '10-30m + dist≥$200 + 3min persist NO')

# ───────────────────────────────────────────────────────────────────
# GROUP U: Sanity checks — the original H22 settings
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 75 + '\n GROUP U — Baseline for comparison')

# Replicate H22 (the current Tier 2 best from previous backtest)
run('U1 H22-baseline', std_query('''
  SELECT * FROM snap_persistence
  WHERE secs_to_close BETWEEN 1800 AND 3600
    AND yes_ask BETWEEN 0.88 AND 0.97
    AND btc_spot - strike >= 100
'''), 'H22-style deep-ITM 30-60min')

# ───────────────────────────────────────────────────────────────────
# REPORT
# ───────────────────────────────────────────────────────────────────
print('\n' + '═' * 110)
print(' SUMMARY — sorted by total PnL (★ = win≥60%, total>$5, n≥20)')
print('═' * 110)
print(f'{"hyp":<20} {"n":>5} {"win%":>6} {"p":>5} {"BE%":>5} {"edge":>6} {"total$":>9} {"$/day":>7}  desc')
print('─' * 110)
for r in sorted(RESULTS, key=lambda x: x['total'], reverse=True):
    star = '★' if r['win'] >= 60 and r['total'] > 5 and r['n'] >= 20 else ' '
    be = r.get('breakeven_wr', 0)
    edge = r['win'] - be
    avg_p = r.get('avg_price', 0)
    print(f'{star} {r["hyp"]:<18} {r["n"]:>5} {r["win"]:>5.1f}% {avg_p:>5.2f} {be:>4.0f}% {edge:>+5.1f}% '
          f'{r["total"]:>+9.2f} {r["per_day"]:>+7.2f}  {r["desc"][:48]}')

winners = [r for r in RESULTS if r['win'] >= 60 and r['total'] > 5 and r['n'] >= 20]
print(f'\n>>> {len(winners)} strategies met goal: win≥60%, total>$5, n≥20')
if winners:
    print('\nWINNERS:')
    for w in winners:
        print(f'  {w["hyp"]:<18} n={w["n"]} win={w["win"]:.0f}% total=+${w["total"]:.2f} '
              f'(${w["per_day"]:.2f}/day, {w["contracts"]} contracts)')
        print(f'    {w["desc"]}')

ana.close()
