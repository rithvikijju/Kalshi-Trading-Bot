"""Informed-trader microstructure research on Kalshi BTC binaries.

Rigorous attempt at distinguishing informed flow from noise:

1. Extract a CLEAN trade tape by:
   - Taking only negative orderbook deltas at top-of-book (= consumption)
   - Excluding deltas at non-best prices (likely cancels)
   - Using seq number to anchor each delta to a known book state

2. Compute three independent informed-flow estimators:
   - Kyle's λ: regression slope of mid-change on signed order flow
   - VPIN: bulk-volume classification (with cleaner signed tape)
   - Glosten-Milgrom spread component: how much of spread is adverse selection

3. Backtest in walk-forward style:
   - Use first 3 days as IS, last 4 days as OOS
   - Predict next-30s mid move from each estimator
   - Compare direction match + economic significance (clears 1¢ fee)
"""
from __future__ import annotations
import duckdb
import math
import pandas as pd
import numpy as np
from datetime import datetime, timezone

DB = '/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb'


def extract_clean_trades():
    """Pull negative delta_qty events that occurred at top-of-book (= real trade).
    Memory-efficient: process market-by-market, merge_asof to attach prior book.
    """
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='2GB'")
    print('Extracting clean trades (top-of-book consumption only)...')
    print('  Step 1: pull all negative deltas...')
    deltas = db.execute("""
        SELECT received_at_utc::TIMESTAMP AS ts,
               market_ticker, seq, side, price, -delta_qty AS trade_qty
        FROM ws_orderbook_delta_all
        WHERE delta_qty < 0
          AND price > 0.02 AND price < 0.98
        ORDER BY market_ticker, seq
    """).df()
    print(f'    {len(deltas):,} negative deltas')

    # Active markets
    active_markets = deltas['market_ticker'].unique()
    print(f'  Step 2: pull top-of-book for {len(active_markets):,} active markets...')
    db.register('active_mkts', pd.DataFrame({'market_ticker': active_markets}))
    tops = db.execute("""
        SELECT received_at_utc::TIMESTAMP AS ts,
               market_ticker, seq, yes_bid, yes_ask, no_bid, no_ask
        FROM ws_orderbook_top_dedup
        WHERE market_ticker IN (SELECT market_ticker FROM active_mkts)
          AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
        ORDER BY market_ticker, seq
    """).df()
    db.close()
    print(f'    {len(tops):,} top-of-book observations')

    # Per-market merge_asof to attach prior book state to each delta
    print('  Step 3: classify each delta as trade or cancel...')
    deltas['prev_yes_bid'] = np.nan
    deltas['prev_yes_ask'] = np.nan
    deltas['prev_no_bid'] = np.nan
    deltas['prev_no_ask'] = np.nan

    results = []
    for mkt, d_g in deltas.groupby('market_ticker'):
        t_g = tops[tops['market_ticker'] == mkt].sort_values('seq')
        if len(t_g) == 0: continue
        d_g = d_g.sort_values('seq').copy()
        # For each delta, find the most-recent prior top entry (seq <= delta.seq - 1)
        # Use searchsorted for speed
        t_seqs = t_g['seq'].values
        idx = np.searchsorted(t_seqs, d_g['seq'].values - 0.5)
        idx = np.clip(idx - 1, 0, len(t_g) - 1)  # one before
        valid = idx >= 0
        if valid.sum() == 0: continue
        d_g.loc[d_g.index[valid], 'prev_yes_bid'] = t_g['yes_bid'].values[idx[valid]]
        d_g.loc[d_g.index[valid], 'prev_yes_ask'] = t_g['yes_ask'].values[idx[valid]]
        d_g.loc[d_g.index[valid], 'prev_no_bid']  = t_g['no_bid'].values[idx[valid]]
        d_g.loc[d_g.index[valid], 'prev_no_ask']  = t_g['no_ask'].values[idx[valid]]
        results.append(d_g)

    if not results:
        return pd.DataFrame()
    df = pd.concat(results, ignore_index=True)
    df = df.dropna(subset=['prev_yes_bid','prev_yes_ask','prev_no_bid','prev_no_ask'])

    # Classify
    tol = 0.005
    df['classify'] = 'cancel'
    df.loc[(df['side']=='yes') & (abs(df['price'] - df['prev_yes_ask']) < tol), 'classify'] = 'buy_yes_taker'
    df.loc[(df['side']=='yes') & (abs(df['price'] - df['prev_yes_bid']) < tol), 'classify'] = 'sell_yes_taker'
    df.loc[(df['side']=='no')  & (abs(df['price'] - df['prev_no_ask'])  < tol), 'classify'] = 'buy_no_taker'
    df.loc[(df['side']=='no')  & (abs(df['price'] - df['prev_no_bid'])  < tol), 'classify'] = 'sell_no_taker'

    cancels = (df['classify'] == 'cancel').sum()
    trades = df[df['classify'] != 'cancel'].copy()
    trades['event_ticker'] = trades['market_ticker'].str.replace(r'-T[0-9.]+$', '', regex=True)

    print(f'  {len(df):,} delta events classified')
    print(f'    cancels (non-top-of-book): {cancels:,} ({cancels/len(df)*100:.1f}%)')
    print(f'    real trades:               {len(trades):,} ({len(trades)/len(df)*100:.1f}%)')
    print(f'  Trade-side breakdown:')
    print(trades['classify'].value_counts().to_string())
    return trades


def compute_kyle_lambda_per_event(trades_df, window_sec=60):
    """Kyle's lambda = Cov(Δmid, signed_OF) / Var(signed_OF).

    For each event_ticker, bucket trades into `window_sec` windows.
    Signed order flow OF in window:
      OF = (buy_yes + sell_no) - (sell_yes + buy_no)
    Mid change Δmid: change in (yes_bid+yes_ask)/2 across the window.
    """
    # First pull mid quotes per minute per market for change calculations
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='2GB'")
    print(f'  Pulling mid quotes for windowing (window={window_sec}s)...')
    mids = db.execute(f"""
        SELECT
            CAST(FLOOR(EXTRACT(EPOCH FROM received_at_utc::TIMESTAMP) / {window_sec}) AS BIGINT) AS bucket_idx,
            market_ticker,
            AVG((yes_bid + yes_ask) / 2.0) AS avg_mid,
            COUNT(*) AS n_obs
        FROM ws_orderbook_top_dedup
        WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
          AND yes_bid > 0 AND yes_ask < 1
        GROUP BY 1, 2
    """).df()
    db.close()
    mids = mids.sort_values(['market_ticker', 'bucket_idx']).reset_index(drop=True)
    mids['next_mid'] = mids.groupby('market_ticker')['avg_mid'].shift(-1)
    mids['dmid_next'] = mids['next_mid'] - mids['avg_mid']

    # Sign trades — match DuckDB's EPOCH calc exactly
    trades_df = trades_df.copy()
    ts_utc = pd.to_datetime(trades_df['ts'], utc=True)
    epoch_sec = ((ts_utc - pd.Timestamp('1970-01-01', tz='UTC'))
                  .dt.total_seconds()).astype('int64')
    trades_df['bucket_idx'] = (epoch_sec // window_sec).astype('int64')
    sign_map = {
        'buy_yes_taker':  +1,
        'sell_no_taker':  +1,
        'sell_yes_taker': -1,
        'buy_no_taker':   -1,
    }
    trades_df['signed_qty'] = trades_df['classify'].map(sign_map).fillna(0) * trades_df['trade_qty']
    trades_df['signed_usd'] = trades_df['signed_qty'] * trades_df['price']

    # Aggregate per (event_ticker × bucket)
    of = trades_df.groupby(['event_ticker', 'bucket_idx']).agg(
        signed_of=('signed_qty', 'sum'),
        signed_usd=('signed_usd', 'sum'),
        n_trades=('classify', 'count'),
        total_qty=('trade_qty', 'sum'),
    ).reset_index()

    # Join to mid changes — average dmid across all event markets in same bucket
    mids['event_ticker'] = mids['market_ticker'].str.replace(r'-T[0-9.]+$', '', regex=True)
    event_mid_chg = mids.groupby(['event_ticker', 'bucket_idx']).agg(
        avg_dmid=('dmid_next', 'mean'),
        n_markets=('market_ticker', 'count'),
    ).reset_index()

    print(f'  trades bucket range: {of["bucket_idx"].min()} - {of["bucket_idx"].max()} (n_buckets={of["bucket_idx"].nunique()})')
    print(f'  mids   bucket range: {event_mid_chg["bucket_idx"].min()} - {event_mid_chg["bucket_idx"].max()} (n_buckets={event_mid_chg["bucket_idx"].nunique()})')
    merged = of.merge(event_mid_chg, on=['event_ticker', 'bucket_idx'])
    merged = merged.dropna(subset=['avg_dmid'])
    return merged


def kyle_lambda(merged):
    """Compute per-event Kyle's lambda + overall."""
    print('\n  Computing Kyle lambda per event + overall...')
    overall_lambda = None
    if len(merged) > 30:
        of = merged['signed_of'].values
        dmid = merged['avg_dmid'].values
        if of.std() > 0:
            overall_lambda = np.cov(dmid, of)[0,1] / np.var(of)
    print(f'  Overall Kyle lambda (Δmid per unit signed flow): {overall_lambda}')

    per_event = []
    for ev, g in merged.groupby('event_ticker'):
        if len(g) < 10: continue
        of = g['signed_of'].values
        dmid = g['avg_dmid'].values
        if of.std() == 0 or np.isnan(of.std()): continue
        lam = np.cov(dmid, of)[0,1] / np.var(of)
        # Spearman/Pearson correlation for sanity
        corr = np.corrcoef(of, dmid)[0,1] if of.std() > 0 and dmid.std() > 0 else 0
        per_event.append({'event': ev, 'lambda': lam, 'corr': corr, 'n': len(g),
                          'of_std': of.std(), 'dmid_std': dmid.std()})
    return pd.DataFrame(per_event), overall_lambda


def test_predictive_power(merged):
    """Test whether signed_of in window t predicts dmid in window t+1.
    Two cuts:
      1. Linear correlation across all data
      2. Sign-match: when |signed_of| is large, does dmid match?
    """
    print('\n  Testing predictive power...')
    print(f'  Total bucket observations: {len(merged):,}')

    # Add lagged feature: signed_of in current bucket predicts dmid in CURRENT bucket
    # (because avg_dmid is the change from THIS to NEXT bucket — flow in this bucket → next move)
    corr = np.corrcoef(merged['signed_of'], merged['avg_dmid'])[0,1] if len(merged) > 10 else 0
    print(f'  Corr(signed_of_t, dmid_t+1):  {corr:+.4f}')

    # USD-weighted
    corr_usd = np.corrcoef(merged['signed_usd'], merged['avg_dmid'])[0,1] if len(merged) > 10 else 0
    print(f'  Corr(signed_usd_t, dmid_t+1): {corr_usd:+.4f}')

    # Sign-match for large signals
    threshold = merged['signed_of'].abs().quantile(0.80)
    strong = merged[merged['signed_of'].abs() > threshold]
    if len(strong) > 0:
        # Predict dmid sign from signed_of sign
        match = ((strong['signed_of'] > 0) == (strong['avg_dmid'] > 0)).mean()
        print(f'\n  Sign-match when |signed_of| > p80 ({threshold:.0f}):')
        print(f'    n={len(strong):,}   match_rate={match*100:.1f}%   (50% = random)')
        # Mean dmid by direction
        positive = strong[strong['signed_of'] > 0]['avg_dmid']
        negative = strong[strong['signed_of'] < 0]['avg_dmid']
        print(f'    Mean dmid when signed_of > 0:  {positive.mean()*100:+.3f}¢  (n={len(positive)})')
        print(f'    Mean dmid when signed_of < 0:  {negative.mean()*100:+.3f}¢  (n={len(negative)})')

        # Tradeable? Mean |dmid| should exceed fee floor (~1.5¢) for it to be tradeable
        mean_abs = strong['avg_dmid'].abs().mean()
        print(f'    Mean |dmid| in strong buckets: {mean_abs*100:.3f}¢  (need > ~1.5¢ for fee)')


if __name__ == '__main__':
    print('=' * 90)
    print('INFORMED-TRADER MICROSTRUCTURE RESEARCH — Kalshi BTC binaries')
    print('=' * 90)

    trades = extract_clean_trades()
    if len(trades) == 0:
        print('NO TRADES EXTRACTED — aborting')
    else:
        print('\n--- Test 1: 60-second bucket Kyle lambda ---')
        merged = compute_kyle_lambda_per_event(trades, window_sec=60)
        print(f'  Bucket observations after merge: {len(merged):,}')
        per_event, overall = kyle_lambda(merged)
        if len(per_event) > 0:
            print(f'  Per-event lambdas computed for {len(per_event)} events')
            print(f'  Median lambda: {per_event["lambda"].median():+.6e}')
            print(f'  Median corr:   {per_event["corr"].median():+.4f}')
        test_predictive_power(merged)

        print('\n--- Test 2: 30-second bucket (higher frequency) ---')
        merged30 = compute_kyle_lambda_per_event(trades, window_sec=30)
        test_predictive_power(merged30)
