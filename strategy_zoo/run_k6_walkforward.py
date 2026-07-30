"""Walk-forward K6 + K14 (the existing winning Kalshi strategy) through
the same rigorous harness as the novel hypotheses. This is the baseline
that any new strategy must beat — and the candidate to compound $100.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np
from novel_harness import run_strategy, print_summary

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'


def gen_k6_yes(db, start, end) -> pd.DataFrame:
    """K6 YES-side: spot $50-$500 above strike, 0-15 min to close, mid+high vol only."""
    df = db.execute(f"""
        WITH q AS (
            SELECT event_ticker, market_ticker, available_at, close_time,
                   floor_strike, yes_ask_close, yes_bid_close,
                   DATE_DIFF('minute', available_at, close_time) AS mtc,
                   DATE_TRUNC('minute', available_at) AS qmin
            FROM v_research_universe
            WHERE close_time IS NOT NULL
              AND yes_ask_close IS NOT NULL AND yes_ask_close > 0 AND yes_ask_close < 1
              AND CAST(available_at AS DATE) BETWEEN '{start}' AND '{end}'
              AND DATE_DIFF('minute', available_at, close_time) BETWEEN 0 AND 15
        ),
        with_spot AS (
            SELECT q.*, b.close AS spot, b.rv_15m AS rv15
            FROM q LEFT JOIN btc_1m b ON b.bucket_start = q.qmin
            WHERE b.close IS NOT NULL AND b.rv_15m IS NOT NULL
        ),
        in_band AS (
            SELECT * FROM with_spot
            WHERE spot - floor_strike BETWEEN 50 AND 500
              AND rv15 >= 0.0003  -- mid+high vol cutoff (rough K14 threshold)
        ),
        dedup AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM in_band
        )
        SELECT available_at AS ts, market_ticker, event_ticker, 'yes' AS side
        FROM dedup WHERE rn = 1
    """).df()
    return df


def gen_k6_no(db, start, end) -> pd.DataFrame:
    """K6 NO-side: spot $50-$500 below strike, 0-15 min to close, low+mid vol only."""
    df = db.execute(f"""
        WITH q AS (
            SELECT event_ticker, market_ticker, available_at, close_time,
                   floor_strike, yes_ask_close, yes_bid_close, no_ask_exe,
                   DATE_DIFF('minute', available_at, close_time) AS mtc,
                   DATE_TRUNC('minute', available_at) AS qmin
            FROM v_research_universe
            WHERE close_time IS NOT NULL
              AND no_ask_exe IS NOT NULL AND no_ask_exe > 0 AND no_ask_exe < 1
              AND CAST(available_at AS DATE) BETWEEN '{start}' AND '{end}'
              AND DATE_DIFF('minute', available_at, close_time) BETWEEN 0 AND 15
        ),
        with_spot AS (
            SELECT q.*, b.close AS spot, b.rv_15m AS rv15
            FROM q LEFT JOIN btc_1m b ON b.bucket_start = q.qmin
            WHERE b.close IS NOT NULL AND b.rv_15m IS NOT NULL
        ),
        in_band AS (
            SELECT * FROM with_spot
            WHERE floor_strike - spot BETWEEN 50 AND 500
              AND rv15 < 0.0008  -- low+mid vol cutoff (K14 threshold for NO side)
        ),
        dedup AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM in_band
        )
        SELECT available_at AS ts, market_ticker, event_ticker, 'no' AS side
        FROM dedup WHERE rn = 1
    """).df()
    return df


def gen_k6_combined(db, start, end) -> pd.DataFrame:
    y = gen_k6_yes(db, start, end)
    n = gen_k6_no(db, start, end)
    return pd.concat([y, n], ignore_index=True).sort_values('ts')


if __name__ == '__main__':
    print('=' * 100)
    print('K6 + K14 walk-forward (the existing Kalshi winner, baseline for novel)')
    print('=' * 100)
    results = {'K6+K14 combined': run_strategy(gen_k6_combined, 'K6+K14 combined')}
    print_summary(results)

    # Compute $100 compounding scenarios for the OOS window
    oos = results['K6+K14 combined'].get('OOS', {})
    if oos.get('n', 0) > 0:
        print()
        print('=== $100 compounding scenarios on OOS ===')
        avg_pnl = oos['avg_pnl']
        avg_paid = oos['avg_paid']
        tpd = oos['tpd']
        per_trade_ret = avg_pnl / avg_paid

        for size_contracts in [1, 5, 10, 20, 50]:
            position_cost = size_contracts * avg_paid
            if position_cost > 100:
                print(f"  {size_contracts}-contract size: ${position_cost:.0f} cost > $100 — too big")
                continue
            daily_pnl = size_contracts * avg_pnl * tpd
            # Account for active days: ~28%
            monthly_pnl = daily_pnl * 30 * 0.28
            print(f"  size={size_contracts}ct  cost/trade=${position_cost:.1f}  "
                  f"daily_pnl=${daily_pnl:.2f}  monthly (28% active)=${monthly_pnl:.0f}  "
                  f"$100 → ${100 + monthly_pnl:.0f} after 30d")
