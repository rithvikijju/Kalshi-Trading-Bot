"""Kalshi BTC late-hour fade backtest.

Strategy: In a BTC hourly market, when spot is FAR from a strike (>$300) with
significant time remaining, that strike's probability is near 0 or 1, but
Kalshi quotes may not have fully repriced. Buy whichever side will settle.

Honest fee model: Kalshi fee per contract = ceil(0.07 × P × (1-P) × 100) / 100
For 5+ contracts at $0.85: fee = ceil(0.07 × 0.85 × 0.15 × 100)/100 ≈ $0.01/ct

Settlement determination: BTC spot at top-of-hour UTC for the event's hour.
Event KXBTCD-26MAYDDHH closes at HH:00 ET = HH+4:00 UTC (DST).
"""
from __future__ import annotations
import math, re
from pathlib import Path
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb'


def kalshi_fee(p):
    """Per-contract fee: ceil(0.07 * P * (1-P) * 100) / 100"""
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def parse_event_close(event_ticker):
    """KXBTCD-26MAYDDHH → UTC close timestamp.
    HH is in ET. May 2026 is DST → UTC = ET + 4."""
    m = re.match(r'KXBTCD-(\d{2})([A-Z]{3})(\d{2})(\d{2})', event_ticker)
    if not m: return None
    yr, mon_str, day, hr_et = m.groups()
    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
              'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    mon = months[mon_str]
    yr_full = 2000 + int(yr)
    hr_utc = (int(hr_et) + 4) % 24
    day_utc = int(day) + (1 if int(hr_et) + 4 >= 24 else 0)
    return pd.Timestamp(f'{yr_full}-{mon:02d}-{day_utc:02d} {hr_utc:02d}:00:00', tz='UTC')


def backtest(min_dist=300, min_time_remaining_min=10, max_ask=0.85,
              min_depth=10, max_position_per_event=5, max_total_open=20):
    """Walk through all signals, look up settlement, compute P&L."""
    db = duckdb.connect(DB, read_only=True)
    print(f"  Loading signals: min_dist={min_dist}, min_t={min_time_remaining_min}min, max_ask={max_ask}, min_depth={min_depth}")

    # Pull entries: one per (market, second). Add buy signal flag.
    # Filter to first signal per market only (to avoid 100k entries on the same market)
    sigs = db.execute(f"""
        WITH parsed AS (
            SELECT received_at_utc::TIMESTAMP AS ts,
                   event_ticker, market_ticker, btc_spot,
                   yes_ask, yes_bid, no_ask, no_bid,
                   yes_ask_qty, no_ask_qty,
                   CAST(REGEXP_EXTRACT(market_ticker, 'T(\d+\.?\d*)', 1) AS DOUBLE) AS strike
            FROM ws_orderbook_top_dedup
            WHERE market_ticker LIKE '%-T%' AND yes_ask IS NOT NULL
              AND btc_spot IS NOT NULL AND btc_spot > 0
              AND yes_ask > 0 AND no_ask > 0
        ),
        signals AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts) AS rn_per_market,
                   CASE
                     WHEN btc_spot - strike > {min_dist} AND yes_ask < {max_ask}
                       AND yes_ask_qty >= {min_depth} THEN 'BUY_YES'
                     WHEN strike - btc_spot > {min_dist} AND no_ask < {max_ask}
                       AND no_ask_qty >= {min_depth} THEN 'BUY_NO'
                     ELSE NULL
                   END AS sig
            FROM parsed
        )
        SELECT * FROM signals WHERE sig IS NOT NULL AND rn_per_market = 1
        ORDER BY ts
    """).df()

    print(f"  {len(sigs)} first-signal entries (one per market-ever)")
    if len(sigs) == 0: return None

    # Get BTC settlement price per event = btc_spot at the event's close time (or last available before)
    settle = {}
    for evt in sigs['event_ticker'].unique():
        close_ts = parse_event_close(evt)
        if close_ts is None: continue
        # Pull last spot tick within 5 min of close from coinbase ticker
        r = db.execute("""
            SELECT price FROM coinbase_ticker_all
            WHERE product_id = 'BTC-USD'
              AND received_at_utc::TIMESTAMP BETWEEN ? AND ?
            ORDER BY received_at_utc DESC LIMIT 1
        """, [close_ts - pd.Timedelta(minutes=10), close_ts]).fetchone()
        if r: settle[evt] = r[0]

    print(f"  settled {len(settle)} of {sigs['event_ticker'].nunique()} events")

    # Compute P&L per signal
    trades = []
    for _, row in sigs.iterrows():
        evt = row['event_ticker']
        if evt not in settle: continue
        btc_final = settle[evt]
        strike = row['strike']
        if row['sig'] == 'BUY_YES':
            # Pay yes_ask, win $1 if BTC > strike at close
            paid = row['yes_ask']
            fee_in = kalshi_fee(row['yes_ask'])
            settle_value = 1.0 if btc_final > strike else 0.0
            # Sell on close (or hold to settlement, no fee if held)
            pnl = settle_value - paid - fee_in
        else:  # BUY_NO
            paid = row['no_ask']
            fee_in = kalshi_fee(row['no_ask'])
            settle_value = 1.0 if btc_final < strike else 0.0
            pnl = settle_value - paid - fee_in
        trades.append({
            'ts':row['ts'], 'event':evt, 'market':row['market_ticker'],
            'side':row['sig'], 'paid':paid, 'fee':fee_in,
            'settle_value':settle_value, 'pnl':pnl,
            'btc_at_entry':row['btc_spot'], 'btc_at_settle':btc_final, 'strike':strike,
            'dist_at_entry': row['btc_spot'] - strike,
        })
    return pd.DataFrame(trades)


def summarize(df, label=''):
    if df is None or len(df) == 0: return
    wins = (df['pnl'] > 0).sum()
    total = len(df)
    avg_pnl = df['pnl'].mean()
    sum_pnl = df['pnl'].sum()
    win_pnl_avg = df[df['pnl']>0]['pnl'].mean() if wins > 0 else 0
    loss_pnl_avg = df[df['pnl']<=0]['pnl'].mean() if total-wins > 0 else 0
    span_h = (df['ts'].max() - df['ts'].min()).total_seconds() / 3600
    print(f"  [{label}]")
    print(f"    trades:   {total}")
    print(f"    win rate: {wins/total*100:.1f}%  ({wins}/{total})")
    print(f"    avg P&L per contract: ${avg_pnl:+.4f}")
    print(f"    win avg: ${win_pnl_avg:+.3f}  loss avg: ${loss_pnl_avg:+.3f}")
    print(f"    total P&L (1 contract each): ${sum_pnl:+.2f}")
    print(f"    span: {span_h:.0f}h  → ${sum_pnl/max(span_h/24,1):.2f}/day per contract")
    # Sharpe
    if df['pnl'].std() > 0:
        sh = df['pnl'].mean() / df['pnl'].std() * math.sqrt(total / max(span_h/24/365, 0.001))
        print(f"    Sharpe (annualized, 1 trade per contract): {sh:.2f}")


if __name__ == '__main__':
    print("=== Kalshi BTC late-hour fade backtest ===\n")
    print("Sweep over signal stringency:")
    for params in [
        {'min_dist':200, 'max_ask':0.85, 'min_depth':10},
        {'min_dist':300, 'max_ask':0.85, 'min_depth':10},
        {'min_dist':500, 'max_ask':0.85, 'min_depth':10},
        {'min_dist':300, 'max_ask':0.75, 'min_depth':10},
        {'min_dist':500, 'max_ask':0.75, 'min_depth':10},
        {'min_dist':300, 'max_ask':0.65, 'min_depth':10},
        {'min_dist':500, 'max_ask':0.65, 'min_depth':10},
        {'min_dist':500, 'max_ask':0.55, 'min_depth':10},
    ]:
        df = backtest(**params)
        label = f"d>{params['min_dist']} ask<{params['max_ask']} depth>={params['min_depth']}"
        summarize(df, label)
        print()
