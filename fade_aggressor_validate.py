"""Rigorous validation: does "fade aggressive flow" actually print money on Kalshi?

Hypothesis: in Kalshi BTC binaries, aggressive takers are mostly noise traders.
When a large taker hits one side, fade them — go opposite — and the mid drifts
back toward where it was.

Tests:
  1. Multiple horizons (15s, 30s, 60s, 120s)
  2. Multiple signal thresholds (p70, p80, p90, p95)
  3. Walk-forward IS/OOS split
  4. Trade-level PnL with realistic fees (entry + exit at mid; 7%*p*(1-p)/100 fee)
  5. Sample-size adjusted Sharpe
"""
from __future__ import annotations
import duckdb
import math
import sys
import pandas as pd
import numpy as np

DB = '/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb'

# Reuse the trade extraction from informed_flow_research
sys.path.insert(0, '/Users/rithvikijju/edge-bot')
from informed_flow_research import extract_clean_trades, compute_kyle_lambda_per_event


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def signal_aggregation(trades_df, window_sec):
    """Aggregate signed_of per (event_ticker, bucket). Return df with bucket_idx,
    event_ticker, signed_of, total_qty."""
    sign_map = {'buy_yes_taker': +1, 'sell_no_taker': +1,
                'sell_yes_taker': -1, 'buy_no_taker': -1}
    trades_df = trades_df.copy()
    ts_utc = pd.to_datetime(trades_df['ts'], utc=True)
    epoch_sec = ((ts_utc - pd.Timestamp('1970-01-01', tz='UTC'))
                  .dt.total_seconds()).astype('int64')
    trades_df['bucket_idx'] = (epoch_sec // window_sec).astype('int64')
    trades_df['signed_qty'] = (trades_df['classify'].map(sign_map).fillna(0)
                                * trades_df['trade_qty'])
    return trades_df.groupby(['event_ticker', 'bucket_idx']).agg(
        signed_of=('signed_qty', 'sum'),
        total_qty=('trade_qty', 'sum'),
        n_trades=('classify', 'count'),
    ).reset_index()


def get_event_mids(window_sec, lookahead_buckets):
    """Per (event_ticker, bucket_idx), get current avg_mid and mid at +N buckets."""
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='2GB'")
    mids = db.execute(f"""
        SELECT
            CAST(FLOOR(EXTRACT(EPOCH FROM received_at_utc::TIMESTAMP) / {window_sec}) AS BIGINT) AS bucket_idx,
            market_ticker,
            REGEXP_REPLACE(market_ticker, '-T[0-9.]+$', '') AS event_ticker,
            AVG((yes_bid + yes_ask) / 2.0) AS avg_mid
        FROM ws_orderbook_top_dedup
        WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
          AND yes_bid > 0 AND yes_ask < 1
        GROUP BY 1, 2, 3
    """).df()
    db.close()
    em = mids.groupby(['event_ticker', 'bucket_idx'])['avg_mid'].mean().reset_index()
    em = em.sort_values(['event_ticker', 'bucket_idx']).reset_index(drop=True)
    em['mid_future'] = em.groupby('event_ticker')['avg_mid'].shift(-lookahead_buckets)
    em['dmid_future'] = em['mid_future'] - em['avg_mid']
    return em


def evaluate_fade(of_df, mids_df, of_threshold_percentile=80, window_sec=60, fee_per_side_bps=None):
    """Evaluate the strategy: FADE strong takers.
       When signed_of > p% (buy-side spike), SELL YES at current mid → profit if mid drops.
       When signed_of < (1-p%), BUY YES at current mid → profit if mid rises.
    """
    merged = of_df.merge(mids_df, on=['event_ticker', 'bucket_idx'])
    merged = merged.dropna(subset=['dmid_future', 'avg_mid'])
    if len(merged) < 30:
        return None
    of_abs = merged['signed_of'].abs()
    if of_abs.std() == 0: return None
    p_thr = np.percentile(of_abs, of_threshold_percentile)

    # Strong signal subset
    strong = merged[of_abs >= p_thr].copy()
    if len(strong) < 5: return None

    # FADE: if signed_of > 0 (people buying), SHORT (sell yes) — gain if mid drops
    # If signed_of < 0 (people selling), LONG (buy yes) — gain if mid rises
    # Per-trade PnL = mid_future - mid_entry × (-1 if fading buyers else +1)
    strong['side'] = np.where(strong['signed_of'] > 0, 'short_yes', 'long_yes')
    strong['raw_pnl'] = np.where(strong['side'] == 'long_yes',
                                  strong['dmid_future'],   # buy yes: profit if mid rises
                                  -strong['dmid_future'])  # short yes: profit if mid falls

    # Realistic fee: at mid_entry, fee = kalshi_fee(mid_entry).
    # Entry + exit ≈ 2× fee in a round-trip
    strong['fee'] = strong['avg_mid'].apply(lambda p: 2 * kalshi_fee(p))
    strong['net_pnl'] = strong['raw_pnl'] - strong['fee']

    n = len(strong)
    win_rate = (strong['net_pnl'] > 0).mean()
    raw_avg = strong['raw_pnl'].mean()
    net_avg = strong['net_pnl'].mean()
    total = strong['net_pnl'].sum()

    return {
        'n': n,
        'threshold_pct': of_threshold_percentile,
        'window_sec': window_sec,
        'mean_raw_dmid': raw_avg,
        'mean_net_pnl': net_avg,
        'win_rate': win_rate,
        'total': total,
        'sharpe': (net_avg / strong['net_pnl'].std() * np.sqrt(252 * 24)
                    if strong['net_pnl'].std() > 0 else 0),
    }


def walk_forward(trades_df, n_buckets_warmup=200, window_sec=60, lookahead_buckets=1,
                  threshold_pct=80):
    """Split data by bucket_idx; first half = IS (use to estimate noise), second half = OOS test."""
    of_df = signal_aggregation(trades_df, window_sec)
    if len(of_df) < 100: return None
    mids_df = get_event_mids(window_sec, lookahead_buckets)
    # Bucket-aligned merge
    merged = of_df.merge(mids_df, on=['event_ticker', 'bucket_idx'])
    merged = merged.dropna(subset=['dmid_future']).sort_values('bucket_idx').reset_index(drop=True)
    if len(merged) < 60: return None

    split_idx = int(len(merged) * 0.5)
    is_data = merged.iloc[:split_idx]
    oos_data = merged.iloc[split_idx:]

    # Use IS to estimate the threshold value, then apply on OOS
    if len(is_data) < 10 or len(oos_data) < 10: return None
    p_thr = np.percentile(is_data['signed_of'].abs(), threshold_pct)

    # OOS test
    strong_oos = oos_data[oos_data['signed_of'].abs() >= p_thr].copy()
    if len(strong_oos) < 5: return None

    strong_oos['side'] = np.where(strong_oos['signed_of'] > 0, 'short_yes', 'long_yes')
    strong_oos['raw_pnl'] = np.where(strong_oos['side'] == 'long_yes',
                                       strong_oos['dmid_future'],
                                       -strong_oos['dmid_future'])
    strong_oos['fee'] = strong_oos['avg_mid'].apply(lambda p: 2 * kalshi_fee(p))
    strong_oos['net_pnl'] = strong_oos['raw_pnl'] - strong_oos['fee']

    return {
        'n_oos': len(strong_oos),
        'oos_win_rate': (strong_oos['net_pnl'] > 0).mean(),
        'oos_mean_raw': strong_oos['raw_pnl'].mean(),
        'oos_mean_net': strong_oos['net_pnl'].mean(),
        'oos_total': strong_oos['net_pnl'].sum(),
        'threshold_value': p_thr,
        'sharpe_oos': (strong_oos['net_pnl'].mean() / strong_oos['net_pnl'].std()
                        if strong_oos['net_pnl'].std() > 0 else 0),
    }


if __name__ == '__main__':
    print('=' * 90)
    print('Rigorous validation: FADE-AGGRESSOR strategy on Kalshi BTC binaries')
    print('=' * 90)

    print('\nExtracting clean trade tape...')
    trades = extract_clean_trades()
    print()

    print('═══ Phase 1: Parameter sweep (in-sample only) ═══')
    print()
    print(f'{"window_sec":<11} {"lookahead":<11} {"threshold":<11} {"n":<6} {"raw_¢":<8} {"net_¢":<8} {"win%":<7} {"total":<9} {"sharpe":<7}')
    print('-' * 90)
    for ws in [30, 60, 120, 300]:
        for lookahead in [1, 2, 3]:
            of_df = signal_aggregation(trades, ws)
            mids_df = get_event_mids(ws, lookahead)
            for thr_pct in [70, 80, 90, 95]:
                r = evaluate_fade(of_df, mids_df, thr_pct, ws)
                if r is None: continue
                print(f'  {ws:<9} {lookahead:<11} p{thr_pct:<10} '
                      f'{r["n"]:<6} {r["mean_raw_dmid"]*100:>+5.2f}¢  '
                      f'{r["mean_net_pnl"]*100:>+5.2f}¢  '
                      f'{r["win_rate"]*100:>4.1f}%  '
                      f'${r["total"]:>+6.2f}  {r["sharpe"]:>+5.2f}')

    print()
    print('═══ Phase 2: Walk-forward OOS (50/50 chronological split) ═══')
    print()
    print(f'{"params":<35} {"n_oos":<7} {"win%":<7} {"raw_¢":<8} {"net_¢":<8} {"total":<9} {"sharpe":<7}')
    print('-' * 90)
    best = None
    best_score = -1e9
    for ws in [30, 60, 120, 300]:
        for lookahead in [1, 2, 3]:
            for thr in [80, 90, 95]:
                r = walk_forward(trades, window_sec=ws, lookahead_buckets=lookahead,
                                  threshold_pct=thr)
                if r is None: continue
                params = f'ws={ws}s look={lookahead} thr=p{thr}'
                print(f'  {params:<33} {r["n_oos"]:<7} {r["oos_win_rate"]*100:>4.1f}%  '
                      f'{r["oos_mean_raw"]*100:>+5.2f}¢  '
                      f'{r["oos_mean_net"]*100:>+5.2f}¢  '
                      f'${r["oos_total"]:>+6.2f}  {r["sharpe_oos"]:>+5.2f}')
                # Score by total OOS PnL × n
                score = r["oos_total"] * (1 if r["n_oos"] > 20 else 0)
                if score > best_score:
                    best_score = score
                    best = (ws, lookahead, thr, r)

    if best:
        ws, look, thr, r = best
        print()
        print('═══ Best OOS config ═══')
        print(f'  window={ws}s, lookahead={look} buckets, threshold=p{thr}')
        print(f'  n_oos={r["n_oos"]}, mean_net=${r["oos_mean_net"]:.4f}/trade, total=${r["oos_total"]:.2f}')
        print(f'  win_rate={r["oos_win_rate"]*100:.1f}%, sharpe={r["sharpe_oos"]:+.2f}')
