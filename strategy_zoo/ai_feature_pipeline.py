"""Feature pipeline for AI strategies. ZERO look-ahead.

Outputs a parquet with one row per (market_ticker, snapshot_minute), with:
  - 23 causal features computed from data available at snapshot time
  - settle outcome (YES wins / NO wins / BTC at close)
  - For both YES- and NO-side trade options, the realized PnL if we'd bought
    at that snapshot's ask

The OOS period in the research DB only contains snapshots with mtc 34-59,
so we restrict the WHOLE pipeline to that range. Both train and test get the
same data shape.
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np
from pathlib import Path

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'
OUT = Path('/Users/rithvikijju/edge-bot/strategy_zoo/ai_features.parquet')

# Restrict to a slice that has full data in IS AND OOS
MTC_RANGE = (34, 59)   # 34-59 minutes to close


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def build():
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='6GB'")

    print('Pulling raw rows + spot + vol...')
    df = db.execute(f"""
        WITH q AS (
            SELECT
                event_ticker, market_ticker, available_at, close_time,
                floor_strike,
                yes_ask_close, yes_bid_close, no_ask_exe, spread_cents,
                DATE_DIFF('minute', available_at, close_time) AS mtc,
                DATE_TRUNC('minute', available_at) AS qmin,
                DATE_TRUNC('minute', close_time) AS cmin
            FROM v_research_universe
            WHERE close_time IS NOT NULL
              AND yes_ask_close IS NOT NULL AND yes_bid_close IS NOT NULL
              AND no_ask_exe IS NOT NULL
              AND yes_ask_close BETWEEN 0.02 AND 0.98
              AND no_ask_exe   BETWEEN 0.02 AND 0.98
              AND DATE_DIFF('minute', available_at, close_time) BETWEEN {MTC_RANGE[0]} AND {MTC_RANGE[1]}
        ),
        with_btc AS (
            SELECT
                q.*,
                b.close AS spot,
                b.rv_15m AS rv15,
                b.rv_60m AS rv60,
                b.log_ret AS log_ret_now,
                bc.close AS btc_close
            FROM q
            LEFT JOIN btc_1m b  ON b.bucket_start = q.qmin
            LEFT JOIN btc_1m bc ON bc.bucket_start = q.cmin
            WHERE b.close IS NOT NULL AND bc.close IS NOT NULL
        ),
        with_lags AS (
            SELECT
                w.*,
                -- log-return over last 5 min (causal — uses only past minute bars)
                LN(w.spot / (SELECT close FROM btc_1m WHERE bucket_start = w.qmin - INTERVAL '5 minutes' LIMIT 1)) AS lret_5m,
                -- spot 15 min ago for direction context
                (SELECT close FROM btc_1m WHERE bucket_start = w.qmin - INTERVAL '15 minutes' LIMIT 1) AS spot_15m_ago,
                -- min/max over last 10 min (window stats)
                (SELECT MIN(low)  FROM btc_1m WHERE bucket_start BETWEEN w.qmin - INTERVAL '10 minutes' AND w.qmin) AS spot_min_10m,
                (SELECT MAX(high) FROM btc_1m WHERE bucket_start BETWEEN w.qmin - INTERVAL '10 minutes' AND w.qmin) AS spot_max_10m
            FROM with_btc w
        ),
        first_per_market AS (
            -- One row per market (first snapshot we'd encounter), so trade counts
            -- are realistic — you can't enter the same market multiple times.
            SELECT *, ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY available_at) AS rn
            FROM with_lags
        )
        SELECT * FROM first_per_market WHERE rn = 1
    """).df()
    db.close()
    print(f'  {len(df):,} rows pulled')

    # ── Engineered features ──────────────────────────────────────────
    df['spot_dist'] = df['spot'] - df['floor_strike']
    df['mid_yes']   = (df['yes_ask_close'] + df['yes_bid_close']) / 2.0
    df['mid_no']    = 1 - df['mid_yes']
    df['spread']    = df['yes_ask_close'] - df['yes_bid_close']
    df['ask_yes']   = df['yes_ask_close']
    df['ask_no']    = df['no_ask_exe']

    # Drift over last 15 min
    df['drift_15m']     = np.log(df['spot'] / df['spot_15m_ago'])
    df['drift_5m']      = df['lret_5m']
    # 10-min range (proxy for short-term vol)
    df['range_10m']     = (df['spot_max_10m'] - df['spot_min_10m']) / df['spot']
    # Direction-aware moments
    df['spot_dist_pct'] = df['spot_dist'] / df['spot']
    df['abs_dist']      = np.abs(df['spot_dist'])
    df['dist_sq']       = df['spot_dist'] ** 2

    # Standardized distance using rv60 (annualized) and time-to-close
    seconds_to_close = df['mtc'] * 60.0
    sigma_per_sec    = df['rv60'] / np.sqrt(365.25 * 24 * 3600)
    expected_move    = sigma_per_sec * df['spot'] * np.sqrt(seconds_to_close.clip(lower=1))
    df['z_dist']     = df['spot_dist'] / expected_move.clip(lower=1)

    # Time / calendar
    ts = pd.to_datetime(df['available_at'], utc=True)
    df['hour_utc']    = ts.dt.hour
    df['dow']         = ts.dt.dayofweek
    df['hour_sin']    = np.sin(2 * np.pi * df['hour_utc'] / 24)
    df['hour_cos']    = np.cos(2 * np.pi * df['hour_utc'] / 24)
    df['mtc']         = df['mtc'].astype(int)
    df['log_mtc']     = np.log(df['mtc'].clip(lower=1))

    # ── Targets ────────────────────────────────────────────────────────
    df['yes_wins'] = (df['btc_close'] > df['floor_strike']).astype(int)
    df['no_wins']  = 1 - df['yes_wins']

    df['fee_yes'] = df['ask_yes'].apply(kalshi_fee)
    df['fee_no']  = df['ask_no'].apply(kalshi_fee)
    df['pnl_buy_yes'] = df['yes_wins'] - df['ask_yes'] - df['fee_yes']
    df['pnl_buy_no']  = df['no_wins']  - df['ask_no']  - df['fee_no']

    # Drop rows with any NaN in features
    feature_cols = [
        'spot_dist','spot_dist_pct','abs_dist','dist_sq','z_dist',
        'mid_yes','spread','ask_yes','ask_no',
        'mtc','log_mtc',
        'rv15','rv60',
        'drift_5m','drift_15m','range_10m',
        'hour_utc','hour_sin','hour_cos','dow',
        'spot',
    ]
    before = len(df)
    df = df.dropna(subset=feature_cols + ['yes_wins','pnl_buy_yes','pnl_buy_no'])
    print(f'  Dropped {before - len(df):,} NaN rows; {len(df):,} clean rows')

    # Save (dedupe — `spot` appears in both metadata and features)
    keep = ['available_at','event_ticker','market_ticker','floor_strike','close_time',
            'btc_close','yes_wins','pnl_buy_yes','pnl_buy_no'] + feature_cols
    keep = list(dict.fromkeys(keep))  # preserve order, remove dupes
    df[keep].to_parquet(OUT)
    print(f'  Saved to {OUT}')

    # Summary
    print()
    print('Date range:', df['available_at'].min(), 'to', df['available_at'].max())
    print('Mean yes_wins:', df['yes_wins'].mean())
    print('Mean ask_yes:', df['ask_yes'].mean())
    print('Mean PnL buy_yes (random):', df['pnl_buy_yes'].mean())
    print('Mean PnL buy_no  (random):', df['pnl_buy_no'].mean())
    print('Note: random trades should lose money (= -fee). Mean PnL ≈ -1¢ confirms.')


if __name__ == '__main__':
    build()
