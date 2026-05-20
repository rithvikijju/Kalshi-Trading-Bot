# BTC1H High-Confidence Candidate Holdout Freeze

Freeze timestamp: `2026-05-16T01:37:07Z`

This document freezes the BTC1H `high_conf_80` candidate for future websocket
holdout evaluation. Any websocket data collected after the freeze timestamp may
be used as clean forward validation only if the rule below is not changed.

## Frozen Rule

- Market family: `KXBTCD` BTC hourly above/below.
- Data source for final evaluation: self-captured live websocket top-of-book
  plus as-of BTC spot/candle data.
- Fair value: current/core BTC1H research fair value used by
  `scripts/replay_btc1h_core_ws_counterfactual.py`.
- Side probability gate:
  - YES allowed only when `model_p_yes >= 0.80`.
  - NO allowed only when `model_p_yes <= 0.20`.
- TTL window: 5 to 20 minutes before event close.
- Entry bounds: inherited current core settings, `0.25 <= entry <= 0.75`.
- Spread: inherited current core settings, spread <= 2c.
- Edge: inherited current core settings, net edge >= threshold after taker fee.
- Fill model: one visible top-of-book contract, taker fee included, hold to
  settlement.
- Replay cadence for evaluation: report 1s, 5s, 10s, 15s, 20s, and 30s. Do not
  select one cadence after seeing future results; the candidate must remain
  profitable across nearby cadences or it is unstable.
- Sizing: one contract only for validation. Sizing can be studied later only
  after the one-contract signal passes future holdout.

## Evidence Before Freeze

- Predexon Apr1-14 at 30s cadence:
  `backtest_outputs\predexon_btc1h_apr1_14_stride30_20260515`.
  `high_conf_80`: 4 trades, `+$1.34`, 100.0% win.
- Predexon Apr15-30 at 30s cadence:
  `backtest_outputs\predexon_btc1h_apr15_30_stride30_20260515`.
  `high_conf_80`: 18 trades, `+$1.33`, 72.2% win.
- May live websocket capture, scan-cadence sweep:
  `backtest_outputs\btc1h_high_conf80_ws_stride_sweep_20260515.csv`.
  The candidate was positive from 1s through 30s cadence.

## Promotion Gate

To move beyond paper-shadow, the frozen rule should pass a future websocket
holdout collected after `2026-05-16T01:37:07Z` with:

- Positive one-contract PnL after fees.
- No single-day drawdown cluster resembling 2026-05-07 unless a pre-registered
  regime filter explains it.
- Similar decisions between replay and any live/paper ledger.
- No threshold tuning on future holdout.
