# Kalshi Perpetual Futures — Edge Research

**Asset:** KXBTCPERP / KXETHPERP (Kalshi crypto perps, launched 2026-06-03).
**Data:** full product life, 2026-06-03 → 06-16 (~13 days). 1-min OHLC+bid/ask+vol+OI
candles, 8h funding history (w/ mark price), and microsecond trade tape — all from the
**margin** REST API (`/trade-api/v2/margin/...`). Spot: Coinbase 1-min + Binance 1-second
(`data-api.binance.vision`; `api.binance.com` is geo-blocked 451).
**Code:** `pull_data.py · analyze.py · backtest.py · align_check.py · backtest_aligned.py ·
sublag.py · maker_edge.py · maker_conditioned.py · capture_ticks.py · maker_mm.py`

---

## TL;DR

Two structural facts make Kalshi perps unusual:
1. **Fees are currently ZERO** for *both* maker and taker on every perp (verified live via
   `/margin/fee_tiers` — all rates `0`). The quadratic fee that kills binary strategies does
   **not** apply to perps yet. This is a launch promo; treat as time-limited.
2. **Funding is mostly zero and capped ±2%/8h** (BTC nonzero only 12% of periods, mean
   4.3%/yr; ETH 42%, −7.2%/yr). A weak tether vs crypto-native venues.

What is **NOT** an edge (rigorously killed below): predicting BTC, and any **lead-lag scalp**.
The perp tracks spot to within ~1 second at every observable timescale — it is efficiently
arbitraged.

What **IS** a viable edge:
- **(A) Zero-fee, mean-reversion-tilted market-making** — NEW, the headline result. The
  perp's taker flow is *uninformed and mean-reverting*; a passive maker harvests the
  ~0.5bp spread with ≈neutral adverse selection. +EV only because fees are 0.
- **(B) Cross-venue funding carry** — robust, slow, delta-neutral; the known fallback,
  with a Kalshi-specific structural twist (its funding is the odd one out).

---

## The bias autopsy (why the "obvious" edge is fake)

A naive merge of Kalshi perp candles vs Coinbase candles produced a *spectacular* fake
signal: 1-min lead-lag corr **0.976**, basis→next-min-return corr **−0.97**, backtest
"Sharpe 30." It was **100% artifact**, from two bugs the user explicitly warned about:

1. **Timestamp convention mismatch.** Coinbase candles are labelled by bucket **start**
   (close = price at ts+60); Kalshi `end_period_ts` is bucket **end** (close = price at ts).
   Merging on equal ts compared the perp at T against spot at **T+60** → a manufactured 60s
   "lag." Proof (`align_check.py`): contemporaneous return corr peaks at exactly **shift
   +60s (0.982)** and the k=±1 lead-lag collapses to ~0.02. After the fix, basis std drops
   8.3bp → **1.9bp** and the signal corr −0.97 → **−0.08**.
2. **Taker-side sign error.** Crossing the spread to *sell* hits the **bid**, not the ask.
   The first backtest accidentally earned the spread on short legs.

After fixing both, the 1-min basis-reversion strategy is **net-negative at every
threshold/hold** for BTC and ETH — the ~0.5bp round-trip spread eats the entire residual.

**Sub-second check** (`sublag.py`, perp trade tape vs Binance 1s): cross-correlation peaks
at **k=0s** (contemporaneous, 0.70); ±1s are small symmetric discreteness shoulders;
everything ≥2s is zero. There is **no exploitable lead-lag at 1s, 1min, or 8h.** (The
older "10–15s lag" finding was the *binary* market — wide-spread, illiquid; the perp is
fast.)

---

## (A) Zero-fee mean-reversion market-making  — the new edge

Measured from the public trade tape: `taker_side` tells us exactly what the resting
**maker** did (`bid` → maker bought; `ask` → maker sold). Maker forward edge per fill =
`s·(perp_mid_{t+D} − price)`, `s=+1` if maker bought (zero fee). (`maker_edge.py`,
`maker_conditioned.py`, ~107k fills / 3h window.)

| horizon | maker edge/fill |
|--------:|----------------:|
| 0s  | +0.13 bp (≈ half-spread captured) |
| 1–15s | +0.23 … +0.25 bp |
| 30–120s | +0.17 … +0.21 bp (stays positive — mild adverse selection) |
| round-trip (both sides) | **+0.45 … +0.50 bp ≈ full spread** |

**The key conditional finding (counterintuitive):**

| fill context | fwd-15s maker edge |
|---|---:|
| WITH the recent spot move | +0.02 bp (~nothing) |
| **AGAINST** the recent spot move (fade) | **+0.71 bp** |
| during calm (|Δspot 3s| < 1bp) | −0.04 bp |
| during a move (|Δspot 3s| 1–8bp) | +0.1 … +0.5 bp |

The toxic fills are **not** the ones during moves — the opposite. Taker flow is panic /
noise that **mean-reverts**: sellers dump into the bid, the perp bounces, the patient
maker who *faded* them profits. So the edge is to **provide liquidity into transient spot
moves**, not pull away from them.

**Why this is +EV only here:** at retail, fees normally exceed a 0.5bp spread, so passive
MM is dead. Kalshi's zero-fee launch on liquid (~8 trades/s, $1–3.6M OI), non-toxic,
retail flow makes pure spread-capture profitable for a small player. This is the
"think-outside-the-box" edge: not prediction, not speed — **be the zero-fee liquidity
provider to noise traders, tilted to fade.**

**Honest caveats.** (i) Magnitudes use 1s-last as a mid proxy (noisy); the *sign* and the
*conditional structure* are robust, the absolute bp is approximate — the forward 1Hz
bid/ask capture (`capture_ticks.py`, running) will pin it down. (ii) Fill rate / queue
position is **not** modeled — real PnL depends on getting to the front of a 0.5bp queue;
needs a live paper test. (iii) Zero fee is a promo — if Kalshi turns on perp **taker**
fees, inventory-flattening gets taxed; the strategy needs maker-fee ≈ 0 to persist
(monitor `/margin/fee_tiers`). (iv) 13 days, one regime — small sample.

**Deployable design** (`maker_mm.py`, paper): quote both sides near the touch; **skew/size
toward the fade side** of any recent spot move; cap inventory and delta-hedge or
taker-flatten when over limit; kill-switch on *sustained* one-directional drift (trend =
informed risk, distinct from the transient moves we want to fade).

---

## (B) Cross-venue funding carry — robust fallback

Delta-neutral: long the perp on the low/negative-funding venue, short on the high-funding
venue; collect the funding differential every 8h. Kalshi's funding is structurally the odd
one out (capped, mostly zero), so it is almost always a cheap leg. From
`funding_kalshi_arb/` (HL hourly, OKX 8h, Kalshi 8h, 3.8d overlap):

| pair | mean spread (ann) | sign-stable | persistence ac1 |
|---|---:|---:|---:|
| ETH hyperliquid−kalshi | **+18.2%** | 88% | 0.80 |
| BTC kalshi−okx | +10.3% | 60% | 0.87 |

Real but small-sample and capacity-limited; this is the known "funding arb" family
(see memory `crypto_funding_arb_finding`, `xchg_carry_finding`) extended to Kalshi as a
venue. Use as the delta-neutral complement to (A), not the headline.

---

## Access / deployment reality (checked 2026-06-16)

Perps roll out **member-by-member**; there is **no self-serve API to enable them**
(`/margin/exchange/get-enabled-status` is read-only). With valid demo keys, auth + read
endpoints work (`/margin/orders`, `/margin/positions`, `/margin/fills` return empty), but
**order placement returns `user_not_found`** and `/margin/balance` returns 403 — the
account is not onboarded to the margin exchange. The order *body* was structurally
accepted (the 400 was user_not_found, not a schema error), so the execution code is
correct; it just needs an onboarded account. **The DEMO perp book is also empty (no flow)**
— demo can validate order plumbing but NOT the edge. The real edge test with zero access
is the **queue-aware paper sim on the prod tape** (`maker_mm_queue.py`).

Order schema (margin): `POST /margin/orders` {ticker, client_order_id, side: `bid|ask`,
count (str), price (str fixed-point $), time_in_force: `good_till_canceled|immediate_or_cancel|
fill_or_kill`, post_only (bool), self_trade_prevention_type: `taker_at_cross|maker`}.

## Status / next

- [x] De-biased lead-lag (dead), sub-second lag (none), 1-min taker (net-neg).
- [x] Zero-fee MM edge identified + conditional structure characterized.
- [x] Demo/prod access checked — account not onboarded to perps (blocker, on Kalshi).
- [~] `maker_mm_queue.py` — QUEUE-AWARE paper MM on prod tape (realistic fills) running.
- [ ] 1Hz bid/ask capture → clean adverse-selection + true-spread numbers.
- [ ] Live order bot — build once perp access is onboarded (execution code path verified).
- [ ] Watch `/margin/fee_tiers` for the end of the zero-fee promo (kills the edge).
