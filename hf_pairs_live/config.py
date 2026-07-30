"""HF pairs trading config — BTC/ETH cointegrated mean reversion.

Independent strategy from Kalshi sleeves. Uses Coinbase spot only.
Capacity: $5-30M (Coinbase Advanced supports this size with minor slippage).

The trade:
  spread_t = log(BTC_t) - β·log(ETH_t)      with β ≈ 1 (simple version)
  z_t = (spread_t - rolling_60min_mean) / rolling_60min_std

  ENTRY  |z| > 2.0   (short BTC + long ETH if z>2, opposite if z<-2)
  EXIT   |z| < 0.5   (take profit)
         |z| > 3.0   (stop loss)
         held > 4h   (time stop)

Realistic costs (Coinbase Advanced):
  - 0.05% taker per leg → 0.20% round-trip per pair (2 legs in, 2 legs out)
  - 0.05% slippage estimated round-trip
  - Total friction: ~0.25% per round trip

Per-trade breakeven: need to capture > 0.25% of spread movement.
At z=2 entry, exit at z=0.5: 1.5σ × spread_std of capture.
"""
from __future__ import annotations
from pathlib import Path

CFG = {
    # ─ Strategy params ─
    'beta':              1.0,              # log-spread coefficient (β=1 simplest)
    'lookback_minutes':  60,               # rolling window for mean/std
    'entry_z':           2.0,
    'exit_z':            0.5,
    'stop_z':            3.5,
    'time_stop_hours':   4.0,

    # ─ Portfolio ─
    'starting_bankroll':       1000.0,
    'notional_per_leg':        100.0,      # $100 long + $100 short
    'max_concurrent_positions': 2,
    'daily_loss_limit':       -50.0,

    # ─ Costs ─
    'taker_fee':       0.0005,             # 5 bps Coinbase Advanced
    'slippage_bps':    5.0,                # 5 bps slippage per leg

    # ─ Polling ─
    'spot_poll_sec':       5.0,            # 5s polling (HF but not tick-level)
    'decision_loop_sec':   5.0,
    'state_save_sec':     30.0,

    # ─ Endpoints ─
    'coinbase_url': 'https://api.exchange.coinbase.com/products/{pair}/ticker',
    'btc_pair':     'BTC-USD',
    'eth_pair':     'ETH-USD',

    # ─ Persistence ─
    'data_dir':     Path(__file__).parent / 'data',
    'paper_log':    Path(__file__).parent / 'data' / 'paper_log.jsonl',
    'paper_state':  Path(__file__).parent / 'data' / 'paper_state.json',

    # ─ Misc ─
    'verbose':              True,
    'min_history_required': 60,            # min ticks before we trade
}

CFG['data_dir'].mkdir(parents=True, exist_ok=True)
