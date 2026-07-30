"""Kalshi BTC deep-in-the-money harvest backtest.

Strategy: when yes_ask or no_ask is at 0.92-0.99 and we're near close, the
market is saying "near certain". Asymmetric: pay 0.95, win $1 if right
(5¢ gain), lose 0.95 if wrong. Kalshi fee at 0.95 = 0.07 × 0.95 × 0.05 = 0.33¢
per contract, almost free.

Win rate needed for breakeven at 0.95 ask: 95%+ (just slightly above ask price).

Backtest: 2 months of curated quotes (Feb 28 → May 5 2026). Join with
btc_1m for settlement. Compute true win rate at each price band.
"""
from __future__ import annotations
import math, sys
from pathlib import Path
import duckdb, pandas as pd, numpy as np

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1-p) * 100) / 100


def backtest(min_ask, max_ask, max_mins_to_close, side='yes', min_data_count=10):
    """Backtest deep-ITM harvest.
    Buy `side` when ask price is in [min_ask, max_ask] AND time-to-close ≤ max_mins.
    Settle at close. Return trade list."""
    db = duckdb.connect(DB, read_only=True)
    px_col = 'yes_ask_close' if side == 'yes' else 'no_ask_exe'
    df = db.execute(f"""
        WITH quotes AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.{px_col} AS ask, q.floor_strike, q.yes_bid_close,
                   DATE_DIFF('minute', q.available_at, q.close_time) AS mins_to_close
            FROM v_research_universe q
            WHERE q.{px_col} BETWEEN {min_ask} AND {max_ask}
              AND q.close_time IS NOT NULL
              AND DATE_DIFF('minute', q.available_at, q.close_time) BETWEEN 0 AND {max_mins_to_close}
        ),
        -- Get one entry per (market, hour) — first time signal fires
        first_signal AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM quotes
        ),
        -- BTC at close time
        btc_close AS (
            SELECT q.market_ticker, q.event_ticker, q.available_at, q.close_time,
                   q.ask, q.floor_strike, q.mins_to_close,
                   (SELECT close FROM btc_1m b
                    WHERE b.bucket_start <= q.close_time
                    ORDER BY b.bucket_start DESC LIMIT 1) AS btc_at_close
            FROM first_signal q
            WHERE rn = 1
        )
        SELECT *,
            CASE WHEN btc_at_close > floor_strike THEN 1.0 ELSE 0.0 END AS yes_settles
        FROM btc_close
        WHERE btc_at_close IS NOT NULL
    """).df()
    if len(df) == 0: return None
    # Compute settlement value for the side we bought
    if side == 'yes':
        df['settles'] = df['yes_settles']
    else:
        df['settles'] = 1.0 - df['yes_settles']
    df['fee'] = df['ask'].apply(kalshi_fee)
    df['pnl'] = df['settles'] - df['ask'] - df['fee']
    return df


def summarize(df, label):
    if df is None or len(df) == 0:
        print(f"  [{label}] no data"); return
    n = len(df); wins = (df['pnl'] > 0).sum()
    avg_pnl = df['pnl'].mean()
    sum_pnl = df['pnl'].sum()
    span_d = (df['available_at'].max() - df['available_at'].min()).total_seconds() / 86400
    tpd = n / max(span_d, 0.1)
    # Annualized return on $1 deployed per contract = avg_pnl/ask × tpd × 365
    avg_capital = df['ask'].mean()
    annualized_return = avg_pnl / avg_capital * tpd * 365
    if df['pnl'].std() > 0:
        sh = df['pnl'].mean() / df['pnl'].std() * math.sqrt(tpd * 365)
    else:
        sh = 0
    print(f"  [{label}]")
    print(f"    n={n:>5}  win_rate={wins/n*100:>5.1f}%  tpd={tpd:>5.1f}")
    print(f"    avg_capital_per_contract=${avg_capital:.3f}  avg_pnl=${avg_pnl:+.4f}")
    print(f"    total_pnl=${sum_pnl:+.2f}  annualized_return={annualized_return*100:+.1f}%  Sharpe≈{sh:.2f}")


if __name__ == '__main__':
    print("=" * 90)
    print("Kalshi BTC deep-ITM harvest — 2 months curated data (Feb 28 → May 5 2026)")
    print("=" * 90)

    print("\n--- BUY YES at deep prices, near close ---")
    for ask_lo, ask_hi, max_mins in [
        (0.95, 0.99, 5),
        (0.95, 0.99, 15),
        (0.95, 0.99, 30),
        (0.97, 0.99, 5),
        (0.97, 0.99, 15),
        (0.97, 0.99, 60),
        (0.92, 0.96, 5),
        (0.92, 0.96, 15),
        (0.85, 0.94, 5),
    ]:
        df = backtest(ask_lo, ask_hi, max_mins, side='yes')
        summarize(df, f'YES {ask_lo}-{ask_hi} mins<={max_mins}')

    print("\n--- BUY NO at deep prices, near close ---")
    for ask_lo, ask_hi, max_mins in [
        (0.95, 0.99, 5),
        (0.95, 0.99, 15),
        (0.95, 0.99, 30),
        (0.97, 0.99, 5),
        (0.92, 0.96, 5),
        (0.85, 0.94, 5),
    ]:
        df = backtest(ask_lo, ask_hi, max_mins, side='no')
        summarize(df, f'NO {ask_lo}-{ask_hi} mins<={max_mins}')
