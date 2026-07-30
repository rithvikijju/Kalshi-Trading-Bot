"""TEST 2 — Fade aggressors and HOLD TO SETTLEMENT.

Round-tripping pays fees twice (~4¢ at mid prices). But Kalshi binaries settle
to $0 or $1, so we can ENTER a position and hold to settlement — paying fees
only ONCE.

Hypothesis: after a strong taker event (someone aggressively buying YES or
hitting NO bid), fade them with a position held to settlement. The mid-revert
finding suggests they're uninformed; settlement should follow the FADE.

Setup:
  - For each market, for each trade in trade tape, identify "strong taker events"
    (signed_qty exceeds the per-market p80)
  - At that moment, compute the FADE position:
      strong buy YES (someone hitting yes_ask)   → BUY NO at no_ask  (= short yes)
      strong sell YES (someone hitting yes_bid)  → BUY YES at yes_ask (= long yes; fade the dumper)
  - Hold to event settlement
  - Compute realized PnL with 1× fee + entry price + settlement
"""
from __future__ import annotations
import duckdb
import math
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, '/Users/rithvikijju/edge-bot')
from informed_flow_research import extract_clean_trades

DB = '/Users/rithvikijju/edge-bot/live_capture_gapless_20260512_paused.duckdb'


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def get_market_close_and_settle():
    """For each market we have trades in, find the event's close time + BTC at close
    so we can determine YES_settles."""
    db = duckdb.connect(DB, read_only=True)
    db.execute("PRAGMA memory_limit='2GB'")
    # Extract event close from the last observed event_ticker — use signal_scan_dedup
    # which has event_ticker + close_time. Approximate: use the latest top-of-book
    # observation for each market as a proxy for close time.
    print('  Getting settlement BTC for each event...')
    settle = db.execute("""
        WITH last_obs AS (
            SELECT market_ticker,
                   MAX(received_at_utc::TIMESTAMP) AS last_ts,
                   AVG(btc_spot) AS avg_btc
            FROM ws_orderbook_top_dedup
            WHERE btc_spot IS NOT NULL AND btc_spot > 0
            GROUP BY market_ticker
        ),
        with_coinbase_at_close AS (
            SELECT m.market_ticker,
                   m.last_ts,
                   CAST(REGEXP_EXTRACT(m.market_ticker, '-T(\\d+\\.?\\d*)', 1) AS DOUBLE) AS strike,
                   (SELECT AVG(price) FROM coinbase_ticker_all c
                    WHERE c.product_id = 'BTC-USD'
                      AND c.received_at_utc::TIMESTAMP BETWEEN m.last_ts - INTERVAL '60 seconds'
                                                          AND m.last_ts + INTERVAL '60 seconds')
                   AS settle_btc
            FROM last_obs m
        )
        SELECT * FROM with_coinbase_at_close WHERE settle_btc IS NOT NULL
    """).df()
    db.close()
    settle['yes_settles'] = (settle['settle_btc'] > settle['strike']).astype(int)
    return settle


def per_market_signal(trades_df, percentile_threshold=80):
    """For each market, find trades where trade_qty exceeds the market's p_threshold
    of trade quantities. Those are 'strong taker events'."""
    # Per-market threshold
    thresholds = trades_df.groupby('market_ticker')['trade_qty'].quantile(percentile_threshold/100.0)
    trades_df = trades_df.copy()
    trades_df['mkt_threshold'] = trades_df['market_ticker'].map(thresholds)
    return trades_df[trades_df['trade_qty'] > trades_df['mkt_threshold']]


def backtest_fade_to_settle(trades_df, percentile=80, max_trades_per_market=3):
    """For each strong taker event, FADE and hold to settle.
    Limit max_trades_per_market so we don't over-trade a single market.
    """
    print(f'\n--- Fade strategy: percentile={percentile}, max trades/market={max_trades_per_market} ---')
    strong = per_market_signal(trades_df, percentile)
    print(f'  Strong taker events: {len(strong):,}')

    # Limit per market
    strong = strong.sort_values(['market_ticker', 'ts']).groupby('market_ticker').head(max_trades_per_market)
    print(f'  After per-market cap: {len(strong):,}')

    # For each, define our FADE trade
    # buy_yes_taker (rare!): they bought YES → we SELL YES = buy NO at no_ask
    # sell_yes_taker:        they sold YES  → we BUY YES = fade their pessimism
    # buy_no_taker (rare!):  they bought NO → we BUY YES (= sell NO)
    # sell_no_taker:         they sold NO   → we BUY NO  (= sell YES)
    fade_side = {
        'buy_yes_taker': 'no',
        'sell_yes_taker': 'yes',
        'buy_no_taker': 'yes',
        'sell_no_taker': 'no',
    }
    strong = strong.copy()
    strong['our_side'] = strong['classify'].map(fade_side)

    # Entry price: when we BUY YES we pay yes_ask (prev_yes_ask is the best ask
    # right before the consume — approximately what we'd see at signal time).
    # When we BUY NO we pay (1 - yes_bid_post) ≈ (1 - prev_yes_bid) for simplicity.
    strong['entry_price'] = np.where(strong['our_side'] == 'yes',
                                      strong['prev_yes_ask'],
                                      1 - strong['prev_yes_bid'])

    # Get settle info
    settle = get_market_close_and_settle()
    strong = strong.merge(settle[['market_ticker', 'strike', 'settle_btc', 'yes_settles']],
                            on='market_ticker', how='inner')

    # PnL: settles=1 if BTC > strike, else 0
    strong['settles_our_side'] = np.where(strong['our_side'] == 'yes',
                                            strong['yes_settles'],
                                            1 - strong['yes_settles'])
    strong['fee'] = strong['entry_price'].apply(kalshi_fee)
    strong['pnl_per_contract'] = strong['settles_our_side'] - strong['entry_price'] - strong['fee']

    # Drop bad rows
    strong = strong.dropna(subset=['entry_price', 'pnl_per_contract'])
    strong = strong[(strong['entry_price'] > 0) & (strong['entry_price'] < 1)]
    if len(strong) == 0:
        print('  No tradeable signals after filters')
        return None

    n = len(strong)
    wins = (strong['pnl_per_contract'] > 0).sum()
    win_rate = wins / n
    avg_pnl = strong['pnl_per_contract'].mean()
    total = strong['pnl_per_contract'].sum()
    sharpe = (avg_pnl / strong['pnl_per_contract'].std() * np.sqrt(n)
                if strong['pnl_per_contract'].std() > 0 else 0)
    print(f'  Trades: {n}')
    print(f'  Win rate: {win_rate*100:.1f}%')
    print(f'  Avg PnL/contract: ${avg_pnl:+.4f}')
    print(f'  Total PnL: ${total:+.2f}')
    print(f'  Sharpe (sample): {sharpe:+.2f}')

    # Breakdown by fade direction
    print('\n  Breakdown by trade type:')
    for c in strong['classify'].unique():
        sub = strong[strong['classify'] == c]
        print(f'    {c:<20} n={len(sub):>4}  win={((sub["pnl_per_contract"]>0).mean()*100):>5.1f}%  '
              f'avg=${sub["pnl_per_contract"].mean():+.4f}  total=${sub["pnl_per_contract"].sum():+.2f}')

    return strong


if __name__ == '__main__':
    print('=' * 90)
    print('FADE-AGGRESSOR TO SETTLEMENT — 1× fee strategy on real Kalshi data')
    print('=' * 90)
    trades = extract_clean_trades()
    for pct in [50, 70, 80, 90, 95]:
        backtest_fade_to_settle(trades, percentile=pct, max_trades_per_market=5)
