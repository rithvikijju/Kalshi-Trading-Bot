"""Simulate the combined strategy on a $100 starting cash account.

Constraints:
  - Start cash: $100
  - Tier 1: pair trade — costs (ask_lo + (1-bid_hi)) × qty upfront
  - Tier 2: single trade — costs price × qty upfront
  - Tier 3: single NO trade
  - Each trade locks cash until settlement (~hour later)
  - Skip trades we can't afford or that would push exposure too high
  - Process all signals in chronological order
"""
import duckdb
import heapq
from collections import deque

ana = duckdb.connect('backtest_outputs/analysis.duckdb', read_only=True)

# Fee
def kalshi_fee(p):
    return min(0.07, 0.07 * max(0, min(1, p)) / 0.50)

# ──────────────────────────────────────────────────────────────────
# Pull all entries from each tier, chronologically
# ──────────────────────────────────────────────────────────────────
# Tier 1: monotonicity arb (min_edge ≥ 1.5c)
t1_query = '''
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_ticker, strike_lo, strike_hi ORDER BY ts_sec) AS rn
  FROM mono_violations WHERE net_edge > 0.015
)
SELECT v.ts_sec, v.event_ticker, ec.close_ts_unix, v.mkt_lo, v.mkt_hi,
       v.yes_ask_lo, v.yes_bid_hi,
       LEAST(COALESCE(v.qty_lo, 1), COALESCE(v.qty_hi, 1), 5) AS qty,
       s_lo.result AS r_lo, s_hi.result AS r_hi
FROM ranked v
JOIN event_close ec ON ec.event_ticker = v.event_ticker
JOIN settlements s_lo ON s_lo.market_ticker = v.mkt_lo
JOIN settlements s_hi ON s_hi.market_ticker = v.mkt_hi
WHERE v.rn = 1
ORDER BY v.ts_sec
'''

# Tier 2: deep-ITM H22 (price≥0.88, fair≥0.95, edge≥1.5c, ttc 30-60m, spread≤2c)
t2_query = '''
WITH cand AS (
  SELECT s.*, t.yes_ask - t.yes_bid AS spread,
         ROW_NUMBER() OVER (PARTITION BY s.market_ticker, s.side ORDER BY s.ts_sec) AS rn
  FROM h3_signals s
  JOIN strike_snaps_1s_clean t USING (event_ticker, market_ticker, ts_sec)
  WHERE s.price >= 0.88 AND s.fair >= 0.95 AND s.edge >= 0.015
    AND s.secs_to_close BETWEEN 1800 AND 3600
    AND t.yes_ask - t.yes_bid <= 0.02
)
SELECT c.ts_sec, c.event_ticker, ec.close_ts_unix, c.market_ticker, c.side,
       c.price, LEAST(COALESCE(c.qty_avail,1), 5) AS qty,
       s.result
FROM cand c
JOIN event_close ec ON ec.event_ticker = c.event_ticker
JOIN settlements s ON s.market_ticker = c.market_ticker
WHERE c.rn = 1
ORDER BY c.ts_sec
'''

# Tier 3: OTM persistence NO
t3_query = '''
WITH cand AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts_sec) AS rn
  FROM snap_persistence
  WHERE secs_to_close BETWEEN 600 AND 1800
    AND yes_bid BETWEEN 0.28 AND 0.40
    AND strike - btc_spot >= 100
    AND spot_60s_ago < strike
    AND spot_180s_ago < strike
    AND spot_300s_ago < strike
    AND yes_bid_qty > 0
)
SELECT c.ts_sec, c.event_ticker, ec.close_ts_unix, c.market_ticker,
       c.yes_bid, LEAST(COALESCE(c.yes_bid_qty,1), 5) AS qty,
       s.result
FROM cand c
JOIN event_close ec ON ec.event_ticker = c.event_ticker
JOIN settlements s ON s.market_ticker = c.market_ticker
WHERE c.rn = 1
ORDER BY c.ts_sec
'''

t1_rows = ana.execute(t1_query).fetchall()
t2_rows = ana.execute(t2_query).fetchall()
t3_rows = ana.execute(t3_query).fetchall()
print(f'Pulled: T1={len(t1_rows)} pairs, T2={len(t2_rows)} singles, T3={len(t3_rows)} NO trades')

# ──────────────────────────────────────────────────────────────────
# Build unified signal list with metadata
# ──────────────────────────────────────────────────────────────────
class Trade:
    __slots__ = ('ts','close_ts','tier','desc','cost','qty','pnl_at_settle','markets')
    def __init__(self, ts, close_ts, tier, desc, cost, qty, pnl_at_settle, markets):
        self.ts = ts; self.close_ts = close_ts; self.tier = tier; self.desc = desc
        self.cost = cost; self.qty = qty; self.pnl_at_settle = pnl_at_settle
        self.markets = markets

signals = []
for ts, ev, close_ts, mkt_lo, mkt_hi, ask_lo, bid_hi, qty, r_lo, r_hi in t1_rows:
    cost_per_unit = ask_lo + (1.0 - bid_hi)
    cost = cost_per_unit * qty
    # PnL per pair: see report — min payoff = bid_hi - ask_lo - fees, may earn extra $1 in middle case
    leg_lo_pnl = (1.0 if r_lo == 'yes' else 0.0) - ask_lo - kalshi_fee(ask_lo)
    leg_hi_pnl = (1.0 if r_hi == 'no'  else 0.0) - (1.0 - bid_hi) - kalshi_fee(1.0 - bid_hi)
    pair_pnl = leg_lo_pnl + leg_hi_pnl
    signals.append(Trade(ts, close_ts, 1, f'T1 {mkt_lo[-12:]}↔{mkt_hi[-12:]}',
                          cost, qty, pair_pnl * qty, (mkt_lo, mkt_hi)))

for ts, ev, close_ts, mkt, side, price, qty, result in t2_rows:
    cost = price * qty
    if side == 'yes':
        per_c = (1.0 if result == 'yes' else 0.0) - price - kalshi_fee(price)
    else:
        per_c = (1.0 if result == 'no' else 0.0) - price - kalshi_fee(price)
    signals.append(Trade(ts, close_ts, 2, f'T2 {mkt[-16:]} {side}',
                          cost, qty, per_c * qty, (mkt,)))

for ts, ev, close_ts, mkt, yes_bid, qty, result in t3_rows:
    no_price = 1.0 - yes_bid
    cost = no_price * qty
    per_c = (1.0 if result == 'no' else 0.0) - no_price - kalshi_fee(no_price)
    signals.append(Trade(ts, close_ts, 3, f'T3 {mkt[-16:]} no',
                          cost, qty, per_c * qty, (mkt,)))

signals.sort(key=lambda x: x.ts)
print(f'Total signals: {len(signals)}')

# ──────────────────────────────────────────────────────────────────
# Simulate cash account
# ──────────────────────────────────────────────────────────────────
def simulate(start_cash, max_concurrent=4, max_exposure_frac=1.0):
    cash = start_cash
    locked_cash = 0.0
    # Heap of (settlement_ts, pnl_to_release, original_cost, tier, desc)
    pending = []
    open_markets = set()  # don't double up on a market
    trades_executed = []
    skipped_reasons = {'no_cash': 0, 'concurrent': 0, 'exposure': 0, 'dup_market': 0}
    peak_locked = 0.0
    cash_curve = []  # (ts, cash, locked, net_value)

    for sig in signals:
        # Settle any expired positions
        while pending and pending[0][0] <= sig.ts:
            settle_ts, pnl, cost, tier, desc, markets = heapq.heappop(pending)
            cash += cost + pnl  # return locked cost + add PnL
            locked_cash -= cost
            for m in markets:
                open_markets.discard(m)
            cash_curve.append((settle_ts, cash, locked_cash, cash + locked_cash))

        # Can we afford this signal?
        max_allowed_exposure = start_cash * max_exposure_frac
        if any(m in open_markets for m in sig.markets):
            skipped_reasons['dup_market'] += 1; continue
        if locked_cash + sig.cost > max_allowed_exposure:
            skipped_reasons['exposure'] += 1; continue
        if cash < sig.cost:
            skipped_reasons['no_cash'] += 1; continue
        n_open = len(pending)
        if n_open >= max_concurrent:
            skipped_reasons['concurrent'] += 1; continue

        # Execute trade
        cash -= sig.cost
        locked_cash += sig.cost
        peak_locked = max(peak_locked, locked_cash)
        for m in sig.markets:
            open_markets.add(m)
        heapq.heappush(pending, (sig.close_ts, sig.pnl_at_settle, sig.cost, sig.tier, sig.desc, sig.markets))
        trades_executed.append(sig)
        cash_curve.append((sig.ts, cash, locked_cash, cash + locked_cash))

    # Final settle of any remaining
    final_pnl = 0.0
    while pending:
        settle_ts, pnl, cost, tier, desc, markets = heapq.heappop(pending)
        cash += cost + pnl
        final_pnl += pnl
        locked_cash -= cost
        cash_curve.append((settle_ts, cash, locked_cash, cash + locked_cash))

    return {
        'start_cash': start_cash,
        'end_cash': cash,
        'pnl': cash - start_cash,
        'pnl_pct': (cash - start_cash) / start_cash * 100,
        'trades': len(trades_executed),
        'peak_locked': peak_locked,
        'skipped': skipped_reasons,
        'cash_curve': cash_curve,
        'tier_counts': {1: sum(1 for t in trades_executed if t.tier == 1),
                         2: sum(1 for t in trades_executed if t.tier == 2),
                         3: sum(1 for t in trades_executed if t.tier == 3)},
        'tier_pnl': {1: sum(t.pnl_at_settle for t in trades_executed if t.tier == 1),
                      2: sum(t.pnl_at_settle for t in trades_executed if t.tier == 2),
                      3: sum(t.pnl_at_settle for t in trades_executed if t.tier == 3)},
    }


def report(r):
    print(f'\nStart: ${r["start_cash"]:.2f}   End: ${r["end_cash"]:.2f}   '
          f'PnL: ${r["pnl"]:+.2f} ({r["pnl_pct"]:+.1f}%)')
    print(f'Trades executed: {r["trades"]}')
    print(f'  Tier 1: {r["tier_counts"][1]} (PnL ${r["tier_pnl"][1]:+.2f})')
    print(f'  Tier 2: {r["tier_counts"][2]} (PnL ${r["tier_pnl"][2]:+.2f})')
    print(f'  Tier 3: {r["tier_counts"][3]} (PnL ${r["tier_pnl"][3]:+.2f})')
    print(f'Peak locked cash: ${r["peak_locked"]:.2f} ({r["peak_locked"]/r["start_cash"]*100:.0f}% of start)')
    print(f'Skipped trades:')
    for k, v in r['skipped'].items():
        print(f'  {k}: {v}')

# ──────────────────────────────────────────────────────────────────
# Scenarios
# ──────────────────────────────────────────────────────────────────
print('\n' + '═' * 65)
print('  SCENARIO 1 — $100 starting, max 4 concurrent, 100% exposure cap')
print('═' * 65)
r = simulate(100, max_concurrent=4, max_exposure_frac=1.0)
report(r)
days = 7
print(f'\n  → Daily: ${r["pnl"]/days:+.2f}/day')
print(f'  → Annualized (×52): ${r["pnl"]*52:+.0f}/year')

print('\n' + '═' * 65)
print('  SCENARIO 2 — $100 starting, max 6 concurrent, 100% exposure')
print('═' * 65)
r = simulate(100, max_concurrent=6, max_exposure_frac=1.0)
report(r)
print(f'\n  → Daily: ${r["pnl"]/days:+.2f}/day')

print('\n' + '═' * 65)
print('  SCENARIO 3 — $100 starting, 50% exposure cap (more conservative)')
print('═' * 65)
r = simulate(100, max_concurrent=4, max_exposure_frac=0.50)
report(r)
print(f'\n  → Daily: ${r["pnl"]/days:+.2f}/day')

print('\n' + '═' * 65)
print('  SCENARIO 4 — $500 starting (to compare scaling)')
print('═' * 65)
r = simulate(500, max_concurrent=8, max_exposure_frac=1.0)
report(r)
print(f'\n  → Daily: ${r["pnl"]/days:+.2f}/day')

print('\n' + '═' * 65)
print('  SCENARIO 5 — Unlimited capital (the original backtest)')
print('═' * 65)
r = simulate(100000, max_concurrent=999, max_exposure_frac=1.0)
report(r)

ana.close()
