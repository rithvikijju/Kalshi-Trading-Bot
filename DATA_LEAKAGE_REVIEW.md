# Data Leakage Review — `kalshi_v2/`

**Scope.** All modules under `kalshi_v2/` (config, client, data, model,
robust, sfm, paper_db, risk, execution, strategy, portfolio, main).
**Goal.** Identify any place where future information enters a decision
that was supposed to use only the past, and any related contamination of
state across modes / runs / regimes.

For each finding: severity, file:line, what leaks (or appears to), and
whether it bites in **live** operation, in **backtest**, or both.

---

## TL;DR

In **live** operation the pipeline is mostly clean: the empirical bank
and ambiguity set are built from history once at `start_bot`, frozen,
and then trades happen forward. The runtime estimators
(`causal_sigma_from_spot`, `add_rv_features`) are right-aligned and
genuinely causal. Settlement reads Kalshi's `result` field, which is the
actual outcome.

The real exposure is in **backtest** (Phase 4, not yet built): the
bank-construction code has the right `max_idx` knob but **no caller uses
it**, drift removal subtracts the **full-window** mean, and the
"ambiguity set" advertised by the robust filter is **not actually a
bootstrap**. If a backtest harness is wired up against the current
`build_empirical_bank` / `build_ambiguity_set` as-is, results will be
silently inflated.

There is also one operational leak in live: `LipschitzSizer` shares
clamp history across paper / shadow / live because `main.py` keys it on
the literal `"v2"`.

---

## CRITICAL findings

### 1. `build_ambiguity_set` does not bootstrap — only re-seeds
**File:** `kalshi_v2/robust.py:47-81`
**Severity:** Critical (methodology, not strictly leakage)

The docstring promises a moving-block bootstrap of `btc_1m` to vary
both vol and kurt across measures:

> "Each measure is an empirical bank built from a moving-block bootstrap
> of recent btc_1m. Block sampling preserves vol clustering."

The code computes `block_starts` at line 70-71 and then throws them
away. The actual loop (line 76-78) just calls `build_empirical_bank`
with a different `seed`:

```python
bank = build_empirical_bank(btc_1m, horizon_min,
                              n_samples=2000, seed=k,
                              demean=True)
```

`seed` only varies the `rng.choice(len(R), n_samples, replace=False)`
sub-sampling of the same R/V/K arrays from the same `btc_1m`. The
underlying distribution is identical across all K measures.

**Consequence.** The HRDNN-inspired robust filter
(`robust.robust_filter`) is not testing robustness against ambiguity in
P; it is testing whether the edge survives sampling noise of a 2,000-row
sub-bank. Pass rate will be artificially high. The "min_edge" /
"max_edge" diag fields will collapse onto each other.

**Fix.** Either implement the moving-block bootstrap on `btc_1m` rows
and call `build_empirical_bank` on each resampled DataFrame, OR
explicitly perturb (vol, drift) parameters per measure. The current code
should not be advertised as an ambiguity set.

---

### 2. Backtest leakage — `build_empirical_bank` walk-forward is unenforced
**File:** `kalshi_v2/model.py:25-81`, callers in `main.py:172`,
`robust.py:76`
**Severity:** Critical (backtest only — Phase 4 hasn't been built)

The function exposes `max_idx`:

```python
def build_empirical_bank(btc_1m, horizon_min, max_idx=None, ...):
    if max_idx is None:
        max_idx = len(btc_1m)
    ...
    for i in range(60 * 24, max_idx - horizon_min):
```

**Three things to verify before any backtest runs:**

1. **`max_idx` is never passed by any caller in v2.** Both `main.py:172`
   and `robust.py:76` call `build_empirical_bank(btc_1m, horizon_min,
   ...)` with no `max_idx`, so the bank always sees the full 90 days.
   For a live bot built once and frozen, this is fine. For a backtest
   that simulates decisions at time `t`, this directly leaks the
   `[t, end_of_history]` slice into every predicted P(YES).

2. **Drift removal uses the full-window mean.** Line 68-69:
   `R = R - R.mean()` subtracts the average forward log-return of the
   entire bank, including future returns relative to early simulated
   trades. This is a textbook look-ahead.

3. **`v_mean`, `v_std`, `k_mean`, `k_std`** (lines 76-79) are also
   computed over the full window and then used at inference for
   z-score matching (`empirical_p_above`, lines 95-99). Same leak.

**Fix (required before Phase 4):** when called from a backtest harness,
pass `max_idx = i_now`, and demean using only `R[: max_idx_local]`.
Consider adding an explicit `WALK_FORWARD=True` config flag that asserts
`max_idx is not None` and refuses to run otherwise.

---

## HIGH

### 3. `LipschitzSizer` history shared across paper / shadow / live
**File:** `kalshi_v2/main.py:94`, `kalshi_v2/robust.py:161-204`
**Severity:** High (operational state leakage, not classical data
leakage)

`main.py:94` calls:

```python
contracts = SIZER.size("v2", raw_size, entry)
```

The strategy key is the literal `"v2"` regardless of `CFG["mode"]`. The
`SIZER.history` dict (`robust.py:176`) therefore mixes paper, shadow,
and live entries. When the user flips `enable_live()`, the first live
entry's clamp reference is whatever the most recent paper entry near the
same price was, which can be 3–4 orders of magnitude larger (paper
bankroll = $100k, live bal often a few hundred dollars).

Concretely: a paper trade at $0.55 sized to ~3,600 contracts becomes
the nearest-price reference for the next live trade at $0.56, which the
clamp then permits up to ~3,600 ± 200·0.01 = 3,598. The live `cap` from
`effective_risk_limits` would still trim the size, but the Lipschitz
clamp is no longer providing the smoothness it claims because the
neighbour comparison is meaningless across modes.

**Fix.** Either key on `f"v2_{mode}"` in `main.py:94`, or maintain three
separate sizers. Reset history on `enable_live()` / `disable_live()`.

---

## MEDIUM

### 4. `kurt` is hardcoded to `0.0` at inference
**Files:** `kalshi_v2/strategy.py:82`, `kalshi_v2/strategy.py:113`,
`kalshi_v2/robust.py:120`

The bank stores `starting_kurts` and computes `k_mean`, `k_std`. The
matching logic in `model.py:98` computes:

```python
tk = (kurt - bank["k_mean"]) / bank["k_std"]
```

But `strategy.scan_signals` always passes `kurt=0.0`
(`strategy.py:82`), and the same value gets stored into the signal dict
(`strategy.py:113`) and re-used by `robust_filter`
(`robust.py:120`).

Result: `tk` is the constant `-k_mean/k_std` on every call. The kurt
axis of the joint vol+kurt match is effectively zero-information; the
matching collapses to a vol-only nearest-neighbour with a fixed kurt
offset.

This is not data leakage. It is a feature-disabled-without-comment
issue: docstrings advertise "vol+kurt joint matching" (model.py:6,
model.py:23) and the bank carries extra state that the live path
doesn't exercise.

**Fix.** Either compute live kurt from spot tape (analogue of
`causal_sigma_from_spot`) and thread it through, or remove kurt from
the bank and the docstrings.

---

### 5. Drift demean is global, not regime-conditional
**File:** `kalshi_v2/model.py:68-69`

`R = R - R.mean()` over the entire 90-day bank assumes the bank's mean
log-return matches the regime active during a live trade. With a
2-hour-or-less trading horizon and a 90-day bank, a strong directional
month inside the bank window can leave a residual demean that biases
short-horizon predictions for the opposite direction.

In **live** this is not leakage (bank was built from past data only),
but it is a model-design risk that compounds in backtest unless the
demean is also walk-forward (see Finding 2).

**Suggestion.** Compute the demean over a shorter trailing window
(e.g., last 7 days) or over windows matched to current realized vol.

---

## LOW / verified clean

### 6. `causal_sigma_from_spot` is causal
**File:** `kalshi_v2/data.py:440-450`

Uses only `SPOT["history"]`, which is appended to by `spot_poller`.
`SPOT["history"]` is trimmed to the last 70 minutes (line 145-146).
Returns None if fewer than 15 ticks. Genuinely causal — no leakage.

### 7. `add_rv_features` is causal
**File:** `kalshi_v2/data.py:63-70`

Pandas rolling defaults to right-aligned with `min_periods=window`. At
row `i`, `rv_w` uses `log_ret[i-w+1 .. i]`, each of which uses
`close[j-1 .. j]`. All inputs at row `i` come from rows `≤ i`. Clean.

### 8. Settlement reads Kalshi's `result` field, not predictions
**File:** `kalshi_v2/execution.py:190-222`

`check_settlements` only acts when `status` is in `TERMINAL_STATUSES`
(line 26) AND `result` is populated. It uses the actual outcome (1.00
or 0.00) for PnL booking. Not leakage.

### 9. `_decide_exit`, `_mark_to_market` use current quotes only
**File:** `kalshi_v2/execution.py:78-103`,
`kalshi_v2/portfolio.py:95-116`

Read-only of `BOOKS[ticker]` (WS state) and current REST quotes for
exit decisions. By definition not leakage in live.

### 10. `event_tracker` clears `BOOKS` on event change
**File:** `kalshi_v2/data.py:204-213`

`BOOKS.clear()` is called before re-seeding (line 208). No prior-event
quote can persist into post-change scans.

### 11. `paper_db` schema uses `settled_at` for PnL window queries
**File:** `kalshi_v2/risk.py:102-114`

`_live_pnl_today` uses `COALESCE(settled_at, timestamp_utc)` for the
day-bucket cutoff. Settlement time, not entry time, anchors the daily
loss limit. Correct.

---

## Specific guardrails to add before backtest (Phase 4)

1. Add `WALK_FORWARD=True` config flag. When set, **fail loudly** if
   `build_empirical_bank` is called with `max_idx is None` — refuse to
   produce a bank that has visibility into the simulated future.
2. Make `build_empirical_bank` accept `demean_window=None` and demean
   over `R[: max_idx_local]` only.
3. Wire `build_ambiguity_set` to a real moving-block resample of
   `btc_1m[:max_idx]` (Finding 1) before reporting any "robust"
   metrics.
4. In the backtest harness, advance `SPOT["history"]` and the bank's
   `max_idx` together, in lockstep with simulated wall-clock.
5. Reset `SIZER.history` between simulated runs (and key the strategy
   string per run, per Finding 3).

---

## What was NOT changed

This review is read-only. No code was modified. Recommended fixes are
called out per finding above; they should be applied as a follow-up
patch.
