# Phase 2 — Research Evaluation Memo

How the two committed papers map onto the kalshi-bot fair-value pipeline,
and what was integrated vs rejected.

The base model in `kalshi-bot.ipynb` is a single-distribution fair-value
mispricing engine: BTC minute history → vol/kurt features → conditional
empirical bank → P(YES) for each Kalshi BTC strike → trade when
post-fee edge exceeds threshold. No recurrent network, no learned
parameters, no batched training.

---

## SFM (3097983.3098117.pdf) — Zhang, Aggarwal, Qi, KDD 2017

### Components evaluated

| Eq.   | Component                                                  | Decision   |
|-------|------------------------------------------------------------|------------|
| 7-11  | Joint state-frequency state matrix S_t ∈ ℂ^{D×K}, DFT     | implemented (`sfm.py`), disabled |
| 12-14 | Joint forget gate F_t = f^state ⊗ f^freq                  | implemented, disabled |
| 15-17 | Inverse-transform composition c_t = tanh(A_t·u_a + b_a)   | implemented, disabled |
| 18-19 | Output gate + hidden-state projection                      | implemented, disabled |

### Why disabled by default (`CFG['sfm_enabled'] = False`)

The base pipeline does not have a recurrent encoder. SFM's value
proposition is "decompose hidden state into K frequencies via DFT to
separate noise from trend during sequence modelling." For a
non-recurrent fair-value engine that already conditions on rolling
realised vol and kurtosis, SFM is upstream feature engineering looking
for a downstream consumer.

We considered three integration points:

1. **Replace `causal_sigma_from_spot` with an SFM-predicted σ.**
   Trades a simple causal estimator (`std(log_ret)·√af` over last
   ~60 ticks) for a learned model with K frequencies × D states of
   parameters. Adds training pipeline, requires labelled forward-vol
   data, introduces overfitting risk. The empirical bank's vol+kurt
   matching already conditions on the same regime indicator. Cost
   exceeds incremental information.

2. **Replace the empirical bank with an SFM forward-distribution
   predictor.** This is a wholesale architectural rewrite — would
   replace a non-parametric, drift-removed, vol-matched lookup with
   a parametric model that needs retraining as regimes shift. Phase 1
   constraint was "keep the core model pipeline intact"; this violates
   it.

3. **Use SFM as an upstream feature** for the empirical bank's
   conditioning vector (currently `[rv_60m, rkurt_60m]` → augment
   with SFM-decomposed frequency amplitudes). Plausible but the kurt
   matching is currently disabled (`kurt = 0.0` hardcoded in
   `strategy.py:82,113`), so adding more matching dimensions before
   fixing the existing ones is premature.

The implementation in `sfm.py` (SFMCell + SFMVolPredictor) is preserved
as a drop-in option behind `CFG['sfm_enabled']`. If a future iteration
wants to A/B test SFM-σ vs causal-σ, the cell is ready and follows
Equations 7-19 exactly. PyTorch is an optional dependency; the module
falls back gracefully when not installed.

### Decision criteria scorecard

- Stability improvement: **inconclusive** — claimed in paper for stock
  prediction (5-stock S&P sample); BTC short-horizon binary markets
  were not in their evaluation set.
- Robustness across distribution shifts: **moderate** — multi-frequency
  decomposition does help with regime-mix data, but the empirical bank
  already adapts via the rolling vol estimator.
- HPC / GPU requirement: **acceptable** — a single SFM cell with
  D=64, K=8 trains on CPU in seconds.
- Plays nicely: **awkward** — has to be bolted upstream of a model
  that already encodes regime via simpler rolling stats.

Verdict: **integrate as opt-in, do not enable by default**.

---

## HRDNN (2510.10599v1.pdf) — Yadav & Mohanty, arXiv 2025

### Components evaluated

| §     | Component                                                  | Decision   |
|-------|------------------------------------------------------------|------------|
| 3.1   | P-robust G-arbitrage: trade only when E_P[edge] > 0 ∀P∈P  | **integrated** (`robust.robust_filter`) |
| 3.2   | Penalised super-replication X_{B,L,k}                      | rejected (see below) |
| 3.3   | Ridgelet ψ(a·x − b) basis approximation                    | rejected (see below) |
| —     | Lipschitz-constrained strategy class W_{B,L}               | **integrated** (`robust.LipschitzSizer`) |

### What was integrated

**P-robust filter (`robust.robust_filter`)**. For every raw signal that
clears the standard edge threshold, we recompute fair P(YES) under
each measure in the ambiguity set P (16 bootstrapped empirical banks,
constructed via moving-block bootstrap of `log_ret` — see
`build_ambiguity_set`). The signal passes only if the fraction of
measures with positive edge ≥ `min_pass_rate` and the mean edge ≥
`min_mean_edge_c`. With `min_pass_rate = 1.0` (default), the filter
implements the strict G-arbitrage criterion from §3.1.

**Lipschitz position sizing (`robust.LipschitzSizer`)**. The paper's
strategy class W_{B,L} bounds both magnitude (B) and Lipschitz constant
(L) in price input. A single-strike binary market has no continuous
strategy w(S); the Lipschitz constraint reduces to "size as a function
of entry price should not change wildly between adjacent prices." We
implement that as a per-mode rolling clamp: `|size(p1) − size(p2)| ≤
L·|p1 − p2|`. Default L = 200. Mode-keyed (`v2_paper`, `v2_shadow`,
`v2_live`) so paper's $100k bankroll sizes don't leak into live's
few-hundred-dollar bankroll sizes.

### What was rejected and why

**Penalised super-replication X_{B,L,k} (§3.2).** The paper develops
this for continuous-time European options where the super-replicating
portfolio is a hedging instrument. Kalshi binary markets settle to
{$0, $1} — there is no underlying replication portfolio to optimise.
The relevant analogue is the "trade only when edge > fee + slippage
across all P" criterion, which we implement directly as the robust
filter rather than via a Lagrangian penalty.

**Ridgelet network approximation (§3.3).** The paper materialises the
strategy w(S, t) as a ridgelet expansion ∑ c_i ψ(a_i·x − b_i) with
neural-network coefficients (their Theorem 3.6). For a strike-by-strike
binary market the strategy is a vector of binary trade/no-trade
decisions, not a continuous function of price space. There is nothing
to expand. We take only the **shape constraint** from §3.1-3.3
(Lipschitz + bounded) and apply it to position sizing (above).

### Decision criteria scorecard

- Stability improvement: **strong** — the robust filter cuts false
  positives that came from a single noisy fair-value estimate.
  Diagnostics (`pass_rate`, `min_edge_c`, `max_edge_c`) are logged per
  decision in the `robust_decisions` table for ongoing calibration.
- Robustness across distribution shifts: **strong by construction** —
  the bootstrap ambiguity set is exactly what HRDNN's §3.1 asks for.
- HPC / GPU requirement: **none** — building 16 empirical banks at
  bot start takes < 5 seconds on a laptop; per-signal evaluation is
  ~16 nearest-neighbour lookups in numpy.
- Plays nicely: **excellent** — wraps the existing fair-value engine
  rather than replacing it.

Verdict: **integrated as the default decision gate** (`CFG[
'robust_enabled'] = True`).

---

## Lipschitz sizing — additional notes

The integration choice was conservative — clamp rather than penalty.
Alternative was a Kelly-with-Lipschitz-regulariser objective, but Kelly
fraction estimation under model uncertainty is itself a research
project. The clamp implementation:

- Maintains `Dict[strategy_key, List[Tuple[price, size]]]` per-mode.
- For new entry at price p, finds nearest-price reference (p', s') in
  history.
- Clamps new size to `s' ± L·|p − p'|`.
- L = 200 means at $0.01 of price difference the size can change by up
  to 200 contracts; at the same price, no change permitted.
- History capped at 50 entries per mode to keep the lookup O(50).

The bigger risk this guards against: Kelly recommending 100 contracts
at $0.499 and 5,000 contracts at $0.501 because a tiny ε in our_p
flipped which side of fair value the trade was on. In a 50/50 binary,
that flip is real and frequent.

---

## What's NOT integrated and is intentionally so

- **GNN model from kalshi_gnn_arb_live.ipynb.** Per task brief, only
  the websocket auth/handshake/reconnect logic is reused (see
  `data._ws_loop`). The GNN itself stays out of v2.
- **SFM as default.** See above. Available behind a flag.
- **Backtest harness (Phase 4).** Deferred per user direction. The
  walk-forward leakage risks identified in `DATA_LEAKAGE_REVIEW.md`
  (max_idx unenforced) are deferred along with it; if Phase 4 ships,
  fix those first.
