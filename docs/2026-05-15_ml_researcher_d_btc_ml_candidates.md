# ML Researcher D BTC Model Candidates

Created: 2026-05-15

Scope: BTC15M and BTC1H research/training data only. No live script was modified
or restarted. Websocket capture stays the final holdout. BTC15M Apr 5-7 stays
preserved unless a candidate is frozen before scoring it there.

## Prototype Artifact

Research-only script:

```text
scripts/prototype_btc_ml_candidates.py
```

Smoke run:

```text
python scripts\prototype_btc_ml_candidates.py --market both --max-rows 40000 --out-dir backtest_outputs\btc_ml_candidate_prototype_20260515_smoke --min-validation-trades 6
```

Outputs:

```text
backtest_outputs\btc_ml_candidate_prototype_20260515_smoke\report.md
backtest_outputs\btc_ml_candidate_prototype_20260515_smoke\btc15m_summary.csv
backtest_outputs\btc_ml_candidate_prototype_20260515_smoke\btc1h_summary.csv
backtest_outputs\btc_ml_candidate_prototype_20260515_smoke\manifest.json
```

The smoke run is not deployment evidence. It is a schema/runtime prototype and
candidate triage pass.

## Recommended Candidate Queue

### 1. Tabular GBDT Probability Model

Use for both BTC15M and BTC1H.

Architecture:

- Histogram GBDT for the local prototype; LightGBM/XGBoost if installed and
  pinned.
- Binary target is side settlement win/loss.
- Output is calibrated `p(win | current executable side candidate)`.
- Live decision rule converts probability to fee-adjusted EV and then applies a
  fixed threshold gate.

Why it is first:

- Strong with limited event count.
- Handles nonlinear interactions between TTL, distance, spread, BTC impulse,
  and book pressure.
- Easy to replay causally from a single state vector.

Feature families:

- Executable quote state: side, entry price, fee, spread, visible size, visible
  ratio, depth, top bid/ask, microprice, imbalance.
- Time state: TTL, close quarter/hour, available-time sin/cos, close-time
  sin/cos.
- BTC state: stale-checked spot age, 1/3/5m returns for BTC15M, 5/15/60m
  returns for BTC1H, realized vol windows.
- Fair-value prior: lognormal/fair probability, distance to strike, side edge,
  fee edge.
- BTC15M-only structure: recent side-mid changes, quote speed, spread changes,
  depth imbalance, side micropressure.

Minimum data:

- BTC15M: at least 7-14 trading days with full side-candidate rows and official
  or audited settlement; Apr 1-4 is too small for final ranking.
- BTC1H: at least 1,000 settled events and enough late-window quote rows to
  maintain 50+ validation trades after gating.
- For promotion: websocket capture rows with executable top-of-book size,
  health windows, BTC feed age, official settlement, and replay manifest.

Smoke result:

- BTC15M HGB was positive on Apr 4 internal validation but high train lift
  suggests overfit risk.
- BTC1H HGB was the best quick candidate on historical proxy labels.

### 2. Regularized Logistic Calibration Layer

Use for both BTC15M and BTC1H as the calibration baseline.

Architecture:

- Median imputer, standard scaler, L2 logistic regression.
- Optional isotonic/Platt calibration only inside the training window.
- Same EV thresholding as the tree model.

Why it stays in:

- It is the simplest live-safe model that can absorb fair-value, microstructure,
  and time-of-day features.
- If a NN/tree cannot beat this under the same replay rules, the extra
  complexity is not justified.

Minimum data:

- Same event counts as GBDT, but logistic can be used earlier because variance
  is lower.
- Calibration must be rechecked by price bucket and TTL bucket.

Smoke result:

- BTC15M logistic was cleaner than the MLP on Apr 4 internal validation.
- BTC1H logistic was competitive with HGB and more stable than MLP.

### 3. Tiny Tabular MLP

Use only after the logistic/GBDT baselines are frozen.

Architecture:

- 2 hidden layers, roughly 32 -> 16 units, ReLU, L2/weight decay, early
  stopping inside train only.
- Inputs are the same tabular feature vector as logistic/GBDT.
- Export path must be fixed: scaler, imputer, weight file, feature order, and
  deterministic CPU inference.

Why it is not first:

- It can memorize event-specific structure in the side-candidate table.
- The BTC15M smoke run showed exactly that pattern: strong train lift, poor Apr
  4 internal validation PnL.

Minimum data:

- BTC15M: more than Apr 1-4; use rolling folds with multiple weeks or do not
  promote.
- BTC1H: enough rows for an untouched test after internal early stopping.
- Require calibration and threshold stability across folds, not just AUC.

### 4. Sequence Model: Causal TCN/GRU Over Last K States

Do not run until websocket or high-frequency historical snapshots are clean
enough.

Architecture:

- Per-event sequence of last K causally available states ending at decision
  time.
- Small TCN or GRU encoder plus tabular head.
- Strict masking for missing/stale book and BTC updates.
- Output remains side `p(win)` and uses the same fee-adjusted EV gate.

Feature families:

- BTC tick/candle deltas through time.
- Top-of-book changes, spread changes, visible quantity changes.
- Side-mid path, quote speed, imbalance path.
- Current tabular state appended at the head.

Minimum data:

- Thousands of events with dense pre-close sequences, not just sparse candles.
- Raw or reconstructed orderbook updates with local receive timestamps.
- Capture health metadata so gaps and reconnects can be masked.

Reason to wait:

- A sequence model is only live-replayable if every time step is exactly what
  the bot would have known. Minute bars and backfilled snapshots can easily
  create lookahead.

## Leakage Risks

- Using websocket capture for model or gate selection before final replay.
- Touching BTC15M Apr 5-7 repeatedly during architecture search.
- Joining BTC and Kalshi rows by rounded minute instead of `available_at` or
  local receive order.
- Using quote high/low, future-best entry, future-best RR, or post-decision
  book changes.
- Treating `NO = 1 - YES mid` as executable when the live book had no NO ask.
- Reusing event-close settlement spot or official result in features.
- Choosing thresholds on the same validation window after inspecting PnL.
- Allowing multiple side candidates per event after the decision policy is
  supposed to take the first qualifying signal.
- Ignoring top-of-book quantity, stale BTC age, dropped websocket windows, or
  reconnect resnapshot gaps.

## Evaluation Plan

1. Freeze feature builders.
   BTC15M features must be generated from Apr 1-4 only during research. BTC1H
   features use historical DuckDB with BTC bars joined causally by availability.

2. Internal model selection.
   BTC15M: train on Apr 1-3 and select model/gate on Apr 4 only. BTC1H: train
   before Apr 1 and select on Apr 1-20 historical validation.

3. Freeze candidate package.
   Record model type, hyperparameters, feature list, imputer/scaler, probability
   calibration, threshold grid, min trade count, entry filters, fee mode, and
   first-per-event policy.

4. One untouched validation pass.
   BTC15M: score Apr 5-7 once only after freeze. BTC1H: score the historical
   test split once after freeze. Reject train-positive/test-negative candidates.

5. Robustness checks.
   Report raw PnL, 2c adverse-entry stress, win rate by entry bucket, TTL bucket,
   spread bucket, side, and day. Require positive after fees and drawdown smaller
   than total PnL magnitude.

6. Final websocket replay.
   Replay in local receive order, exclude unhealthy windows, use only executable
   top-of-book prices and visible size, include taker fees, prefer official
   settlement, and emit a replay manifest. This is the only promotion-grade
   check.

## Current Triage From Smoke Run

Keep:

- BTC15M logistic calibrator.
- BTC15M HGB as a non-frozen exploratory candidate.
- BTC1H HGB.
- BTC1H logistic calibrator.

Do not promote yet:

- BTC15M MLP, because the smoke run showed overfit behavior.
- Any sequence model, until the replay dataset is built as a causal state
  sequence rather than as backfilled bars.
