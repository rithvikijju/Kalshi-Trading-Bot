# BTC15M Future Holdout Freeze - 2026-05-15

Created: 2026-05-15T23:36:03Z

Purpose:

- Freeze the current BTC15M model-research state before evaluating future
  websocket capture.
- Prevent tuning on the same May 12-15 websocket data that has already been
  inspected repeatedly.

Frozen candidate:

- Model artifact: `backtest_outputs/btc15m_live_compatible_models_20260515/`
- Model: `xgboost_tabular`
- Gate from April validation only:
  - `pred_win_prob >= 0.84`
  - `pred_ev >= 0.14`
- Feature set:
  - Live-compatible only; excludes `bid_depth`, `ask_depth`, `rv_15m`,
    `rv_ratio_15_60`.
- Execution assumption:
  - One contract.
  - First qualifying side candidate per event.
  - Hold to settlement.
  - Kalshi taker entry fee included.
  - Report both raw and +2c adverse-entry-stressed PnL.

Evidence before freeze:

- April validation: 12 trades, +$2.71 under +2c stress.
- April final test: 1 trade, +$0.20 under +2c stress.
- May 12-15 websocket diagnostic: 26 trades, +$0.80 under +2c stress,
  positive on all four inspected days.

Decision:

- This is not deployable alpha because April final-test capacity is too low and
  May 12-15 websocket data is already burned as development/diagnostic data.
- From this timestamp forward, newly collected BTC15M websocket data may be
  used as a fresh holdout for this frozen candidate.
- Do not change the model, feature set, gate, sizing, or settlement logic before
  evaluating that future holdout. If any of those change, create a new freeze
  document with a new future-only holdout boundary.
