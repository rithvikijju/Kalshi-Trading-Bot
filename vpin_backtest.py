"""Backtest VPIN on real Kalshi tick data.

Extract trades from ws_orderbook_delta_all (negative delta_qty at top-of-book
= trade happened). Build VPIN per event-hour, test whether high VPIN predicts
subsequent mid move direction.

The question: does VPIN, computed in real-time, give us a tradable signal?
If yes, the PIN framework adds genuine edge. If no, we honestly say so.
"""
from __future__ import annotations
import duckdb
import math
import pandas as pd
import numpy as np
from datetime import datetime, timezone

DB = '/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb'


def extract_trades_from_deltas():
    """Negative delta_qty at non-zero price → likely a trade.
    A trade on YES_ASK reduces ask qty (= BUY YES occurred).
    A trade on YES_BID reduces bid qty (= SELL YES occurred).
    """
    db = duckdb.connect(DB, read_only=True)
    print('Pulling trade-deltas from orderbook delta stream...')
    df = db.execute("""
        SELECT received_at_utc::TIMESTAMP AS ts,
               market_ticker,
               -- Extract event from market: KXBTCD-26MAY0522-T80999.99 → KXBTCD-26MAY0522
               REGEXP_REPLACE(market_ticker, '-T[0-9.]+$', '') AS event_ticker,
               side,
               price,
               -delta_qty AS trade_qty,   -- positive = volume of trade
               CASE WHEN side = 'yes' THEN 'buy_yes'
                    WHEN side = 'no'  THEN 'buy_no'
                    ELSE 'unknown' END AS trade_side
        FROM ws_orderbook_delta_all
        WHERE delta_qty < 0          -- consumption = trade or cancel
          AND price > 0.02 AND price < 0.98
        ORDER BY received_at_utc
    """).df()
    db.close()
    print(f'  {len(df):,} negative-delta events (trades + cancels mixed)')
    return df


def build_vpin_per_event(trades_df, bucket_size_usd=200, window=30):
    """For each event_ticker, build VPIN bucket-by-bucket.
    Return: per-event time series of (ts, vpin, signed_imbalance, mid)."""
    out = []
    for event, ev_df in trades_df.groupby('event_ticker'):
        ev_df = ev_df.sort_values('ts').reset_index(drop=True)
        ev_df['trade_usd'] = ev_df['price'] * ev_df['trade_qty']
        # Bucket accumulation
        buckets = []   # list of (V_b, V_s)
        cur_b = 0.0; cur_s = 0.0; cur_tot = 0.0
        for _, row in ev_df.iterrows():
            # Trade attribution: "buy_yes" (side='yes', someone bought YES) = bullish on this strike
            #                    "buy_no"  (side='no',  someone bought NO)  = bearish on this strike
            # For event-level VPIN, we need a "directional" signal — but since each strike
            # is independent, we'll just count buy_yes vs buy_no across the event.
            usd = row['trade_usd']
            remaining = usd
            while remaining > 0 and len(buckets) < 10000:
                room = bucket_size_usd - cur_tot
                chunk = min(remaining, room)
                if row['trade_side'] == 'buy_yes':
                    cur_b += chunk
                elif row['trade_side'] == 'buy_no':
                    cur_s += chunk
                cur_tot += chunk
                remaining -= chunk
                if cur_tot >= bucket_size_usd - 1e-9:
                    buckets.append({
                        'ts': row['ts'], 'V_b': cur_b, 'V_s': cur_s,
                    })
                    cur_b = 0; cur_s = 0; cur_tot = 0
        # Compute rolling VPIN over `window` buckets
        for i in range(window, len(buckets)):
            wnd = buckets[i - window:i]
            sum_imb = sum(abs(b['V_b'] - b['V_s']) for b in wnd)
            sum_signed = sum(b['V_b'] - b['V_s'] for b in wnd)
            total = window * bucket_size_usd
            vpin = sum_imb / total
            signed = sum_signed / total   # -1 (sell toxic) to +1 (buy toxic)
            out.append({
                'event_ticker': event,
                'ts': buckets[i]['ts'],
                'vpin': vpin,
                'signed_imb': signed,
                'bucket_n': i,
            })
    return pd.DataFrame(out)


def join_with_future_mid(vpin_df, lookahead_min=5):
    """For each VPIN observation, find the mid at t + lookahead_min and
    test whether signed_imb predicts the mid change.

    Since VPIN was computed across all strikes of an event, we proxy
    "event mid" as the AVERAGE yes_mid across all active strikes."""
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='6GB'")
    # Pre-compute event-level avg mid per minute from top-of-book
    print('Computing event-level avg yes_mid per minute...')
    avg_mid = db.execute("""
        WITH q AS (
            SELECT DATE_TRUNC('minute', received_at_utc::TIMESTAMP) AS minute,
                   REGEXP_REPLACE(market_ticker, '-T[0-9.]+$', '') AS event_ticker,
                   AVG((yes_bid + yes_ask)/2.0) AS avg_mid
            FROM ws_orderbook_top_dedup
            WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
              AND yes_bid > 0 AND yes_ask < 1
            GROUP BY 1, 2
        )
        SELECT * FROM q
    """).df()
    db.close()
    avg_mid['minute'] = pd.to_datetime(avg_mid['minute'], utc=True)

    # For each VPIN row, look up mid now and mid at +lookahead
    results = []
    avg_mid_by_event = {ev: g.set_index('minute')['avg_mid'].sort_index()
                         for ev, g in avg_mid.groupby('event_ticker')}
    for _, row in vpin_df.iterrows():
        ev = row['event_ticker']
        if ev not in avg_mid_by_event: continue
        series = avg_mid_by_event[ev]
        # Convert row ts to UTC pd.Timestamp
        ts = pd.Timestamp(row['ts'], tz='UTC') if row['ts'].tzinfo is None else pd.Timestamp(row['ts'])
        try:
            mid_now = series.asof(ts.floor('min'))
            mid_fut = series.asof(ts.floor('min') + pd.Timedelta(minutes=lookahead_min))
            if pd.isna(mid_now) or pd.isna(mid_fut): continue
        except Exception:
            continue
        results.append({
            'vpin': row['vpin'], 'signed_imb': row['signed_imb'],
            'mid_now': mid_now, 'mid_fut': mid_fut,
            'dmid': mid_fut - mid_now,
        })
    return pd.DataFrame(results)


def evaluate(results):
    if len(results) == 0:
        print('No results to evaluate.')
        return
    print(f'\nN observations: {len(results):,}')
    # Bucket by VPIN level
    results['vpin_band'] = pd.cut(results['vpin'],
        bins=[-0.001, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=['<0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '>0.8'])
    g = results.groupby('vpin_band', observed=True).agg(
        n=('dmid', 'count'),
        mean_dmid=('dmid', 'mean'),
        std_dmid=('dmid', 'std'),
        mean_signed=('signed_imb', 'mean'),
    ).round(5)
    print('\nVPIN level → future 5-min mid change:')
    print(g.to_string())

    # Correlation between signed_imb and future dmid
    corr = results[['signed_imb', 'dmid']].corr().iloc[0, 1]
    print(f'\nCorrelation(signed_imb, future_dmid): {corr:+.4f}')

    # Test directional prediction: when |signed_imb| > 0.5, does dmid match sign?
    strong = results[abs(results['signed_imb']) > 0.5].copy()
    if len(strong) > 0:
        # Sign match: signed_imb > 0 should predict dmid > 0
        hit = ((strong['signed_imb'] > 0) == (strong['dmid'] > 0)).mean()
        print(f'\nWhen |signed_imb| > 0.5 (n={len(strong)}):')
        print(f'  Direction-match rate: {hit*100:.1f}%  (50% = random)')
        print(f'  Mean |dmid|:          ${abs(strong["dmid"]).mean()*100:.2f}¢')


if __name__ == '__main__':
    print('=' * 80)
    print('VPIN backtest on real Kalshi tick data (live capture, 7 days)')
    print('=' * 80)
    trades = extract_trades_from_deltas()
    # Take a subset for tractable compute
    print(f'\nSample size: using first 200K negative-delta events for VPIN build...')
    trades_sample = trades.head(200_000)
    vpin_df = build_vpin_per_event(trades_sample, bucket_size_usd=200, window=30)
    print(f'  {len(vpin_df):,} VPIN observations computed across '
          f'{vpin_df["event_ticker"].nunique() if len(vpin_df) else 0} events')
    if len(vpin_df) == 0:
        print('Not enough bucket completions; try larger sample or smaller bucket_size.')
    else:
        results = join_with_future_mid(vpin_df, lookahead_min=5)
        evaluate(results)
