"""OOS validation of "fade sell_yes_taker" finding.

Split 7-day sample chronologically: first 3.5 days = train (parameter calibration),
last 3.5 days = OOS test. If the edge holds OOS, it's real.

Also test: does the edge depend on the BTC regime during the capture window?
"""
from __future__ import annotations
import duckdb
import math
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, '/Users/rithvikijju/edge-bot')
from informed_flow_research import extract_clean_trades


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def get_settles():
    db = duckdb.connect('/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb',
                         read_only=True)
    db.execute("PRAGMA memory_limit='2GB'")
    settle = db.execute("""
        WITH last_obs AS (
            SELECT market_ticker, MAX(received_at_utc::TIMESTAMP) AS last_ts
            FROM ws_orderbook_top_dedup GROUP BY market_ticker
        )
        SELECT m.market_ticker, m.last_ts,
               CAST(REGEXP_EXTRACT(m.market_ticker, '-T(\\d+\\.?\\d*)', 1) AS DOUBLE) AS strike,
               (SELECT AVG(price) FROM coinbase_ticker_all c
                WHERE c.product_id = 'BTC-USD'
                  AND c.received_at_utc::TIMESTAMP BETWEEN m.last_ts - INTERVAL '60 seconds'
                                                      AND m.last_ts + INTERVAL '60 seconds')
                AS settle_btc
        FROM last_obs m
    """).df()
    db.close()
    settle = settle[settle['settle_btc'].notna()]
    settle['yes_settles'] = (settle['settle_btc'] > settle['strike']).astype(int)
    return settle


def get_btc_trend(start_ts, end_ts):
    db = duckdb.connect('/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb',
                         read_only=True)
    db.execute("PRAGMA memory_limit='2GB'")
    df = db.execute(f"""
        SELECT MIN(price) AS lo, MAX(price) AS hi, AVG(price) AS mean,
               (SELECT price FROM coinbase_ticker_all
                WHERE product_id='BTC-USD' AND received_at_utc::TIMESTAMP >= '{start_ts}'
                ORDER BY received_at_utc ASC LIMIT 1) AS open,
               (SELECT price FROM coinbase_ticker_all
                WHERE product_id='BTC-USD' AND received_at_utc::TIMESTAMP <= '{end_ts}'
                ORDER BY received_at_utc DESC LIMIT 1) AS close
        FROM coinbase_ticker_all
        WHERE product_id = 'BTC-USD'
          AND received_at_utc::TIMESTAMP BETWEEN '{start_ts}' AND '{end_ts}'
    """).df()
    db.close()
    return df.iloc[0]


def evaluate_window(trades, settle, threshold_pct=80, classify='sell_yes_taker'):
    """For a slice of trades, isolate the target classify type, take top X% by qty,
    fade-to-settle, compute PnL. Returns dict of stats."""
    sub = trades[trades['classify'] == classify].copy()
    if len(sub) == 0: return None
    thr_qty = sub['trade_qty'].quantile(threshold_pct/100.0)
    sub = sub[sub['trade_qty'] >= thr_qty]
    # Cap to first 3 per market to avoid concentration
    sub = sub.sort_values(['market_ticker', 'ts']).groupby('market_ticker').head(3)
    # Fade direction
    if classify == 'sell_yes_taker':
        sub['our_side'] = 'yes'
        sub['entry_price'] = sub['prev_yes_ask']
    elif classify == 'sell_no_taker':
        sub['our_side'] = 'no'
        sub['entry_price'] = 1 - sub['prev_yes_bid']
    elif classify == 'buy_yes_taker':
        sub['our_side'] = 'no'
        sub['entry_price'] = 1 - sub['prev_yes_bid']
    elif classify == 'buy_no_taker':
        sub['our_side'] = 'yes'
        sub['entry_price'] = sub['prev_yes_ask']
    sub = sub.merge(settle[['market_ticker','yes_settles']], on='market_ticker', how='inner')
    sub['settles_our_side'] = np.where(sub['our_side'] == 'yes',
                                         sub['yes_settles'], 1 - sub['yes_settles'])
    sub = sub.dropna(subset=['entry_price'])
    sub = sub[(sub['entry_price'] > 0.005) & (sub['entry_price'] < 0.995)]
    sub['fee'] = sub['entry_price'].apply(kalshi_fee)
    sub['pnl'] = sub['settles_our_side'] - sub['entry_price'] - sub['fee']
    if len(sub) == 0: return None
    return {
        'n': len(sub),
        'win_rate': (sub['pnl'] > 0).mean(),
        'avg_pnl': sub['pnl'].mean(),
        'total': sub['pnl'].sum(),
        'avg_entry': sub['entry_price'].mean(),
        'std_pnl': sub['pnl'].std(),
    }


if __name__ == '__main__':
    print('=' * 90)
    print('OOS VALIDATION: fade sell_yes_taker — split chronologically')
    print('=' * 90)

    trades = extract_clean_trades()
    settle = get_settles()
    print(f'\nTotal clean trades: {len(trades):,}')
    print(f'Total settled markets: {len(settle):,}')

    # Sort chronologically and split
    trades['ts'] = pd.to_datetime(trades['ts'], utc=True)
    trades_sorted = trades.sort_values('ts').reset_index(drop=True)
    mid_ts = trades_sorted['ts'].quantile(0.5)
    train = trades_sorted[trades_sorted['ts'] < mid_ts]
    test  = trades_sorted[trades_sorted['ts'] >= mid_ts]
    print(f'\nIS (train): {len(train):,} trades, '
          f'{train["ts"].min()} to {train["ts"].max()}')
    print(f'OOS (test): {len(test):,} trades, '
          f'{test["ts"].min()} to {test["ts"].max()}')

    # BTC regime in each window
    is_btc = get_btc_trend(train['ts'].min(), train['ts'].max())
    oos_btc = get_btc_trend(test['ts'].min(), test['ts'].max())
    print(f'\nBTC regime:')
    print(f'  IS:   ${is_btc["open"]:.0f} → ${is_btc["close"]:.0f}  '
          f'(range ${is_btc["lo"]:.0f}-${is_btc["hi"]:.0f})')
    print(f'  OOS:  ${oos_btc["open"]:.0f} → ${oos_btc["close"]:.0f}  '
          f'(range ${oos_btc["lo"]:.0f}-${oos_btc["hi"]:.0f})')

    # Evaluate each classify on each window
    print('\n=== Per-classify backtest IS vs OOS ===')
    print(f'{"classify":<22} {"threshold":<11} {"IS_n":<6} {"IS_win":<8} {"IS_avg":<10} {"OOS_n":<6} {"OOS_win":<8} {"OOS_avg":<10}')
    print('-' * 100)
    for classify in ['sell_yes_taker', 'sell_no_taker', 'buy_yes_taker', 'buy_no_taker']:
        for pct in [50, 70, 80, 90, 95]:
            is_r = evaluate_window(train, settle, pct, classify)
            oos_r = evaluate_window(test, settle, pct, classify)
            if is_r is None and oos_r is None: continue
            is_str = (f'{is_r["n"]:<6} {is_r["win_rate"]*100:>5.1f}%  ${is_r["avg_pnl"]:>+7.4f}'
                      if is_r else f'{"-":<6} {"-":<7} {"-":<10}')
            oos_str = (f'{oos_r["n"]:<6} {oos_r["win_rate"]*100:>5.1f}%  ${oos_r["avg_pnl"]:>+7.4f}'
                       if oos_r else f'{"-":<6} {"-":<7} {"-":<10}')
            print(f'  {classify:<20} p{pct:<10} {is_str}  {oos_str}')

    print()
    print('=== Best surviving config — sell_yes_taker fade at high percentile ===')
    for pct in [80, 85, 90, 95]:
        oos_r = evaluate_window(test, settle, pct, 'sell_yes_taker')
        if oos_r is None: continue
        # Compute sample-adjusted Sharpe
        sharpe_t = (oos_r['avg_pnl'] / (oos_r['std_pnl'] or 1)) * math.sqrt(oos_r['n'])
        # Rough annualization assuming ~5 such trades per day from 3.5 day OOS span
        trades_per_day = oos_r['n'] / 3.5
        annual_n = trades_per_day * 365
        annual_pnl = oos_r['avg_pnl'] * annual_n
        print(f'  p{pct}: n={oos_r["n"]} win={oos_r["win_rate"]*100:.1f}% '
              f'avg=${oos_r["avg_pnl"]:+.4f}  total=${oos_r["total"]:+.2f}  '
              f't-stat={sharpe_t:+.2f}  annual≈${annual_pnl:+.0f} on $1/trade')
