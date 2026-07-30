# BIAS_AND_VALIDATION

How this framework prevents lookahead and validates strategies. Read this
before adding a new strategy.

## 1. Lookahead bias — what we prevent and how

### Rule 1: signal at t depends only on data with timestamp ≤ t
**Where enforced:**
- `src/strategies/spy_vol_target.py` — realized vol uses `rolling(window).std()` which by construction looks backward.
- `src/strategies/tsmom.py` — `pct_change(252)` is `(p_t - p_{t-252}) / p_{t-252}`, purely backward.
- `src/strategies/prediction_market_signals.py` — lead-lag uses `.shift(lag).corr(...)` so the lagging series cannot peek forward.

### Rule 2: orders execute on bar t+1, not t
**Where enforced:** `src/backtest/engine.py:run_backtest()`:
```python
weights = weights.shift(1).fillna(0)        # signal at t → trade at t+1
```
This is the most common bug source in retail backtests. The engine shifts the entire weight matrix by 1, then trades at the next bar's open price (`prices.iloc[i + 1].to_dict()` in the rebalance block).

### Rule 3: macro / event data is filtered by available_ts not event_ts
**Where enforced:** `src/data/fred_client.py:_release_lag()` — every FRED series stores both `event_ts` (the date the print refers to) and `available_ts` (when it was knowable). Strategies must filter on `available_ts`.

### Rule 4: ML training uses walk-forward only
**Where enforced:** `src/backtest/validation.py:purged_walk_forward_splits()` — expanding window train + fixed-width forward test block + embargo days at the seam (López de Prado 2018).

### Rule 5: no full-sample normalisation
**Where enforced:** `src/utils/leakage_tests.py:check_no_future_normalization()` — heuristic that flags columns whose head and tail have ~equal unit std and zero mean (signature of full-sample z-scoring).

## 2. Pre-flight leakage checks

`src/utils/leakage_tests.py` provides:
- `check_signal_uses_only_past(signal_fn, data)` — perturbs random future windows and verifies the signal at t is unchanged. **Default ON in `engine.run_backtest`.** Aborts the run if any probe leaks.
- `check_no_future_normalization(df)` — heuristic for full-sample scaling.
- `stress_test_with_oracle(backtest_fn, data, target_col)` — INTENTIONALLY injects next-day return as a feature; if Sharpe doesn't explode, the harness is over-eagerly shifting and swallowing real signal.

Run with:
```python
from src.backtest.validation import preflight, assert_preflight_passes
reports = preflight(my_strategy, data)
assert_preflight_passes(reports)        # raises if anything failed
```

## 3. Backtest engine invariants

- **Deterministic.** No `Date.now()`, no `random.random()` without a seed.
- **No partial fills, no rejections** in the vector engine — order assumed filled at `next_open`. This is a known simplification; live execution has slippage + rejection handling in `src/live/`.
- **Costs always applied.** Commissions, slippage, half-spread, funding, borrow — see `src/backtest/costs.py:CostModel`.
- **Risk monitor lives in the engine loop**, identical to live: drawdown warn/de-risk/stop, daily loss cap, position cap.
- **Benchmark always plotted alongside** the equity curve.

## 4. ML validation harness

When you wire an ML vol forecaster (or any model) into a strategy:
1. Use `purged_walk_forward_splits` to generate `(train_idx, test_idx)` pairs.
2. Embargo at least 5 days between train and test (more if features overlap).
3. Compute Sharpe on the **concatenated test set**, NOT in-sample.
4. Run the oracle stress test to confirm the harness is leakage-sensitive.
5. Report **deflated Sharpe** (Bailey-López de Prado 2014) — naive Sharpe inflates when you've tried many configs.

## 5. Survivor / selection bias

- **Asset universe.** TSMOM uses 17 large liquid ETFs that have all existed since ~2008. We acknowledge this is an ex-post universe; backtested CAGR is overstated for any universe assembled after the fact.
- **Time window.** Earliest data point is 2010 by default. 2015+ for SPY vol-target. Anything pre-2008 is at the user's risk (Lehman, regime shifts).
- **Strategy parameter selection.** Default params are reasonable guesses, not optimised. The vol-target sweep in NB 02 is meant for *understanding*, not for picking the highest-Sharpe row.

## 6. What we DO NOT claim to prevent
- **Regime change.** A strategy with strong 2018-2024 OOS may still lose money 2026-2030.
- **Capacity.** Backtests assume zero market impact at the sizes shown. Funding arb on Binance at $10k is a different universe from $10M.
- **Counterparty risk.** Modelled as a kill switch (`risk_monitor`), but exchange insolvency isn't priced into the cost model.
- **Cherry-picking after the fact.** If you keep tuning until paper PnL looks good, you've leaked through your own eyes.

## 7. The 30-day paper-trading gate
No live capital until 30+ days of paper PnL has been compared to backtest projections within ±1 standard deviation. This is enforced socially, not by code, but the framework records every paper-trading run in `strategy_runs` so the comparison is always available.
