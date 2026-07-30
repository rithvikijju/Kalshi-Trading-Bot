"""Focused backtest of the only profitable bucket from the price-calibration scan:

  5-15min to close, yes_mid in [0.60, 0.70] band → BUY YES at ask.

Calibration showed implied=65.1%, empirical=74.1%, ask~0.672 → +5.3¢ edge / trade.

We tighten with:
  - Liquidity filter (spread ≤ 5¢, yes_ask_qty ≥ 10)
  - Convert to time-series: respect signal first-per-market dedup
  - Compute actual realized P&L per trade with proper Kalshi fee
  - Compute time-aware sharpe + max drawdown."""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def backtest(mid_lo=0.60, mid_hi=0.70, tmin=5, tmax=15,
             max_spread=0.05, min_depth=10, side='yes'):
    db = duckdb.connect(DB, read_only=True)
    px_col_ask = 'yes_ask_close' if side == 'yes' else 'no_ask_exe'
    px_col_bid = 'yes_bid_close' if side == 'yes' else 'no_bid_exe'

    sql = f"""
        WITH quotes AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.{px_col_ask} AS ask, q.{px_col_bid} AS bid,
                   (q.{px_col_ask}+q.{px_col_bid})/2.0 AS mid,
                   q.{px_col_ask}-q.{px_col_bid} AS spread,
                   q.floor_strike,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close
            FROM v_research_universe q
            WHERE q.close_time IS NOT NULL
              AND q.{px_col_ask} IS NOT NULL AND q.{px_col_bid} IS NOT NULL
              AND q.{px_col_ask} > 0 AND q.{px_col_ask} < 1
        ),
        in_band AS (
            SELECT * FROM quotes
            WHERE mid BETWEEN {mid_lo} AND {mid_hi}
              AND mins_to_close BETWEEN {tmin} AND {tmax}
              AND spread <= {max_spread}
        ),
        first_sig AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM in_band
        ),
        with_settle AS (
            SELECT q.*,
                   (SELECT close FROM btc_1m b
                    WHERE b.bucket_start <= q.close_time
                    ORDER BY b.bucket_start DESC LIMIT 1) AS btc_at_close
            FROM first_sig q
            WHERE rn = 1
        )
        SELECT * FROM with_settle WHERE btc_at_close IS NOT NULL
        ORDER BY available_at
    """
    df = db.execute(sql).df()
    db.close()
    if len(df) == 0: return None

    yes_settles = (df['btc_at_close'] > df['floor_strike']).astype(float)
    if side == 'yes':
        df['settles'] = yes_settles
    else:
        df['settles'] = 1.0 - yes_settles

    df['fee'] = df['ask'].apply(kalshi_fee)
    df['pnl'] = df['settles'] - df['ask'] - df['fee']
    return df


def summarize(df, label):
    if df is None or len(df) == 0:
        print(f"  [{label}] NO DATA"); return
    n = len(df); wins = (df['pnl'] > 0).sum()
    avg_pnl = df['pnl'].mean(); sum_pnl = df['pnl'].sum()
    span = (df['available_at'].max() - df['available_at'].min()).total_seconds() / 86400
    tpd = n / max(span, 0.1)
    avg_cap = df['ask'].mean()
    apr = avg_pnl / avg_cap * tpd * 365 if avg_cap > 0 else 0
    daily = df.set_index('available_at').resample('1D')['pnl'].sum()
    daily_sr = (daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0
    cum = df['pnl'].cumsum()
    dd = (cum - cum.cummax()).min()
    print(f"  [{label}]  span={span:.0f}d  n={n:>5}  tpd={tpd:.1f}")
    print(f"    win={wins/n*100:.1f}%  avg_pnl=${avg_pnl:+.4f}  total=${sum_pnl:+.2f}")
    print(f"    cap=${avg_cap:.2f}  ann_return={apr*100:+.1f}%  daily_sharpe={daily_sr:.2f}  max_dd=${dd:.2f}")


if __name__ == '__main__':
    print("=" * 95)
    print("5-15min, yes-mid 0.60-0.70 — focused backtest of the only ★ from calibration scan")
    print("=" * 95)

    # baseline (from calibration: ★+5.3¢)
    df = backtest()
    summarize(df, 'baseline: yes 60-70, 5-15m, spread<=5c, depth>=10')

    print("\n--- Robustness: vary mid band ---")
    for lo, hi in [(0.55, 0.65), (0.60, 0.70), (0.65, 0.75), (0.60, 0.75), (0.55, 0.70)]:
        df = backtest(mid_lo=lo, mid_hi=hi)
        summarize(df, f'mid {lo}-{hi}')

    print("\n--- Robustness: vary time-to-close ---")
    for tlo, thi in [(2, 10), (5, 15), (5, 30), (10, 30), (15, 45), (30, 60)]:
        df = backtest(tmin=tlo, tmax=thi)
        summarize(df, f't {tlo}-{thi}m')

    print("\n--- Robustness: tighter spread filter ---")
    for sp in [0.02, 0.03, 0.05, 0.10, 0.20]:
        df = backtest(max_spread=sp)
        summarize(df, f'spread<={sp}')

    print("\n--- Symmetry: same idea but BUY NO in NO's 60-70 band ---")
    df = backtest(side='no')
    summarize(df, 'BUY NO at no-mid 60-70')
