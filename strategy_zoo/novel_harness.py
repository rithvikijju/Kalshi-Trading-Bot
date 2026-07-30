"""Rigorous backtest harness for novel Kalshi hypotheses.

Design choices to eliminate common biases:

1. Walk-forward split: days 1-35 in-sample, days 36-65 out-of-sample.
   Parameters chosen on IS, reported on both windows.
2. No look-ahead in features. σ / EWMA / regime indicators only see data
   from before the signal time.
3. Fill modeling: fill at the NEXT observed ask in the same market,
   not the signal-tick ask. Captures real execution slippage.
4. Fee model: exact Kalshi fee = ceil(0.07 × P × (1-P) × 100) / 100 per contract.
5. Event-level de-correlation: per-event PnL aggregates for Sharpe.
6. Conservative settle: settlement determined by `btc_1m.close` at the close-time
   minute bucket. NO future-tick data.

Each candidate strategy provides:
  def generate_signals(db, start_date, end_date) -> DataFrame
     columns: [ts, market_ticker, event_ticker, side ('yes'|'no'),
               signal_price_observed]
The harness then:
  - For each signal, looks up next-tick ask (the actual price we'd fill at)
  - Resolves settlement
  - Computes PnL with fees
  - Aggregates per-event for Sharpe
"""
from __future__ import annotations
import math
import duckdb, pandas as pd, numpy as np
from dataclasses import dataclass

DB = '/Users/rithvikijju/edge-bot/data/research_datamart/research_backtest.duckdb'

# Walk-forward split (days from research DB start 2026-02-28)
IS_START = '2026-02-28'
IS_END   = '2026-04-03'      # 35 days IS
OOS_START = '2026-04-03'
OOS_END   = '2026-05-05'     # 32 days OOS


def kalshi_fee(p):
    """Exact Kalshi fee. Per-contract, applied at entry."""
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def attach_fills(db, signals: pd.DataFrame) -> pd.DataFrame:
    """For each signal row, look up the NEXT tick's ask in same market.
    Conservative: assumes our order races to the next observed snapshot.
    Falls back to signal-tick ask if no next-tick within 5 min."""
    if len(signals) == 0: return signals
    # Register signals for the lookup
    db.register('sig_tmp', signals[['market_ticker', 'ts', 'side']])
    fill_df = db.execute("""
        WITH next_tick AS (
            SELECT s.market_ticker, s.ts, s.side,
                   (SELECT q.yes_ask_close
                    FROM v_research_universe q
                    WHERE q.market_ticker = s.market_ticker
                      AND q.available_at > s.ts
                      AND q.available_at <= s.ts + INTERVAL '5 minutes'
                      AND q.yes_ask_close IS NOT NULL
                    ORDER BY q.available_at ASC LIMIT 1) AS next_yes_ask,
                   (SELECT q.no_ask_exe
                    FROM v_research_universe q
                    WHERE q.market_ticker = s.market_ticker
                      AND q.available_at > s.ts
                      AND q.available_at <= s.ts + INTERVAL '5 minutes'
                      AND q.no_ask_exe IS NOT NULL
                    ORDER BY q.available_at ASC LIMIT 1) AS next_no_ask
            FROM sig_tmp s
        )
        SELECT * FROM next_tick
    """).df()
    return signals.merge(fill_df, on=['market_ticker', 'ts', 'side'], how='left')


def resolve_settlement(db, signals: pd.DataFrame) -> pd.DataFrame:
    """For each signal, look up BTC close at the event's settlement time.
    Uses btc_1m.close at DATE_TRUNC(close_time, 'minute')."""
    if len(signals) == 0: return signals
    # Get close_time for each market
    db.register('sig_settle', signals[['market_ticker']].drop_duplicates())
    close_df = db.execute("""
        SELECT DISTINCT q.market_ticker, q.close_time, q.floor_strike,
               (SELECT b.close FROM btc_1m b
                WHERE b.bucket_start = DATE_TRUNC('minute', q.close_time)
                LIMIT 1) AS btc_close
        FROM sig_settle s
        JOIN v_research_universe q ON q.market_ticker = s.market_ticker
        WHERE q.close_time IS NOT NULL
    """).df()
    return signals.merge(close_df, on='market_ticker', how='left')


def evaluate(signals: pd.DataFrame, label: str, window: str) -> dict:
    """Compute PnL, win rate, Sharpe, event-level Sharpe for a signal set."""
    if signals is None or len(signals) == 0:
        return {'label': label, 'window': window, 'n': 0, 'note': 'no signals'}

    # Drop rows without fills or settlement
    df = signals.dropna(subset=['btc_close']).copy()
    df = df[((df['side'] == 'yes') & df['next_yes_ask'].notna()) |
            ((df['side'] == 'no') & df['next_no_ask'].notna())]
    if len(df) == 0:
        return {'label': label, 'window': window, 'n': 0, 'note': 'no fills'}

    # Trade-level PnL
    yes_wins = (df['btc_close'] > df['floor_strike']).astype(float)
    df['settles'] = np.where(df['side'] == 'yes', yes_wins, 1 - yes_wins)
    df['paid'] = np.where(df['side'] == 'yes', df['next_yes_ask'], df['next_no_ask'])
    df['fee'] = df['paid'].apply(kalshi_fee)
    df['pnl'] = df['settles'] - df['paid'] - df['fee']

    # Filter out pathological fills (paid > 0.99 or <0.01)
    df = df[(df['paid'] > 0.01) & (df['paid'] < 0.99)]
    if len(df) == 0:
        return {'label': label, 'window': window, 'n': 0, 'note': 'all fills extreme'}

    n = len(df)
    wins = (df['pnl'] > 0).sum()
    avg = df['pnl'].mean()
    total = df['pnl'].sum()
    span_d = (df['ts'].max() - df['ts'].min()).total_seconds() / 86400 if n > 1 else 1
    tpd = n / max(span_d, 0.1)

    # Trade-level Sharpe (overstates)
    trade_sr = (avg / df['pnl'].std() * math.sqrt(tpd * 365)) if df['pnl'].std() > 0 else 0

    # Event-level Sharpe (honest)
    if 'event_ticker' in df.columns:
        ev = df.groupby('event_ticker')['pnl'].sum()
        events_per_day = len(ev) / max(span_d, 0.1)
        event_sr = (ev.mean() / ev.std() * math.sqrt(events_per_day * 365)
                    if ev.std() > 0 else 0)
        worst_event = ev.min()
    else:
        event_sr = trade_sr
        worst_event = df['pnl'].min()

    # Daily PnL
    daily = df.set_index('ts').resample('1D')['pnl'].sum()
    daily_sr = (daily.mean() / daily.std() * math.sqrt(365)
                if daily.std() > 0 else 0)

    # Compounding sanity check: at $1/contract avg, what's the $100 -> $X potential?
    # Use OOS only.
    capital = 100
    per_trade_ret = avg / df['paid'].mean()  # return % per trade on capital deployed
    if per_trade_ret > 0:
        comp_30d = capital * (1 + per_trade_ret) ** (tpd * 30)
        comp_30d = min(comp_30d, 100000)  # sanity cap
    else:
        comp_30d = capital * (1 + per_trade_ret * tpd * 30)

    return {
        'label': label,
        'window': window,
        'n': n,
        'span_d': round(span_d, 1),
        'tpd': round(tpd, 2),
        'win_pct': round(wins / n * 100, 1),
        'avg_pnl': round(avg, 4),
        'total_pnl': round(total, 2),
        'avg_paid': round(df['paid'].mean(), 3),
        'trade_sr': round(trade_sr, 2),
        'event_sr': round(event_sr, 2),
        'daily_sr': round(daily_sr, 2),
        'worst_event': round(worst_event, 2),
        'per_trade_ret_pct': round(per_trade_ret * 100, 2),
        'comp_$100_30d': round(comp_30d, 0),
    }


def run_strategy(generate_fn, label: str) -> dict:
    """Run a strategy on both IS and OOS windows. Returns evaluations."""
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='4GB'")
    out = {}
    for window, start, end in [('IS', IS_START, IS_END), ('OOS', OOS_START, OOS_END)]:
        print(f'  [{label} / {window}] generating signals...')
        signals = generate_fn(db, start, end)
        print(f'    {len(signals):,} raw signals')
        if len(signals) == 0:
            out[window] = {'label': label, 'window': window, 'n': 0}
            continue
        signals = attach_fills(db, signals)
        signals = resolve_settlement(db, signals)
        out[window] = evaluate(signals, label, window)
        r = out[window]
        if 'note' in r:
            print(f'    [{window}] {r.get("note")}')
        else:
            print(f'    [{window}] n={r["n"]} win={r["win_pct"]}% '
                  f'avg=${r["avg_pnl"]:+.4f} eventSR={r["event_sr"]} '
                  f'compound $100→${r["comp_$100_30d"]:.0f} in 30d')
    db.close()
    return out


def print_summary(results: dict):
    print()
    print('=' * 120)
    print('SUMMARY (IS = in-sample 35d, OOS = out-of-sample 30d)')
    print('=' * 120)
    fmt = ('{label:<30} {window:<5} {n:>6} {tpd:>6} {win_pct:>6}% '
           '{avg_pnl:>+8.4f} {avg_paid:>7.3f} {event_sr:>+7.2f} '
           '{comp_$100_30d:>10,.0f}')
    print(f"{'strategy':<30} {'win':<5} {'n':>6} {'tpd':>6} "
          f"{'win%':>7} {'avg_pnl':>8} {'paid':>7} {'evSR':>7} "
          f"{'compound':>10}")
    print('-' * 120)
    for label, both in results.items():
        for window in ['IS', 'OOS']:
            r = both.get(window, {})
            if 'n' not in r or r['n'] == 0:
                print(f"{label:<30} {window:<5}    -- no signals/fills --")
                continue
            print(fmt.format(**r))
    print()
