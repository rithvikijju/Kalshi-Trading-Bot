# 10 Novel Hypotheses — V3 (microstructure & regime focus)

Designed 2026-05-15 after reviewing STRATEGY_REPORT.md, HYPOTHESIS_REPORT_2.md,
REPORT_NOVEL_STRATEGIES.md. Goal: find a real scalable edge.

## What's been ruled out (do NOT retest)
- Parity arb (Kalshi enforces)
- Crossed books (Kalshi prevents)
- Butterfly / convexity arb on binary payoffs (-$185 in prior test — wrong math)
- ATM directional (no edge)
- OTM YES persistence (only NO works → already deployed as T3)
- Settlement-final-minute strategies (data truncated 6min before close)
- Tier 2 below price 0.88 (fee floor crushes edge)
- TTC < 15 min (tail risk dominates)

## Core constraints
- Fee = min(7¢, 7% × entry_price) — entry only, no exit fee on settle
- Sweet spot TTC = [30, 60] min (proven by H7/H20)
- KXBTCD hourly markets only
- Capital constraint = $200 working, max 4 concurrent positions
- "Scalable" requires either (a) >$5/day at fixed size **and** depth to absorb larger size, or (b) >2x current frequency

## The 10 hypotheses

### H31 — Quote-Life Gated Monotonicity Arb
**Intuition:** Tier 1 already prints $27/wk in backtest but the live bot rarely
fills — quotes evaporate before round-trip. Filter to pairs where both quoted
sides have been stable ≥ 5s.
**Entry:** All current T1 conditions + `min(quote_age_lo_ask, quote_age_hi_bid) ≥ 5s`
**Exit:** Hold to settlement (no early exit — both legs converge to $1)
**Size:** Same as T1 (depth-limited)
**Why untested:** Prior backtests assume instant fill regardless of quote age.
**Failure mode:** Filtering too tightly → no trades. Need to verify N ≥ 20.

### H32 — Inverted Lead/Lag: Kalshi → Spot
**Intuition:** HFTs trade Kalshi after spot moves. But sometimes informed flow
hits Kalshi first (someone with conviction). If Kalshi's implied probability
moves >2σ in 30s with no corresponding spot move, the spot tends to follow.
**Entry:** For each event, compute weighted-avg-implied-spot from the strike
chain at t and t-30s. If `|d(implied_spot)/dt|` > 2× recent stddev AND
`|d(actual_spot)| < 0.5 × |d(implied_spot)|`, buy the most-marked-up strike's
YES at ask.
**Exit:** TP at +3¢ or hold 5 min, whichever first. SL at -4¢.
**Size:** $5/trade.
**Why untested:** Prior reports tested spot→Kalshi; the reverse needs the
strike-chain implied-spot construction.
**Failure mode:** Coinbase spot is the same data that's driving Kalshi quotes,
so "lead" may be illusory.

### H33 — Stale-Quote MM Error Sniper
**Intuition:** When an MM's quote on a strike hasn't refreshed in >60s but
spot has moved meaningfully toward (or away from) that strike, the quote is
stale. Hit it before the MM corrects.
**Entry:** For each strike where yes_ask hasn't updated in ≥ 90s AND
spot has moved ≥ $80 toward making YES better-priced (relative to BS fair),
buy YES if `BS_fair - ya - fee ≥ 1.5¢`.
**Exit:** Hold to settlement or TP +5¢, SL -5¢.
**Size:** $3/trade (small — adverse selection risk).
**Why untested:** Requires per-quote timestamp tracking; prior backtests used
1s aggregates.
**Failure mode:** "Stale" usually means "no flow" which usually means
"correctly priced and no one cares."

### H34 — Cross-Strike Coordinated Markup (Order Flow Signal)
**Intuition:** If ≥3 adjacent strikes simultaneously mark up bids by ≥0.5¢
within 10s, that's coordinated order flow, not noise. The direction (up vs
down the chain) signals where spot is heading.
**Entry:** For each event, scan strikes sorted by K. If in last 10s, ≥3
consecutive strikes had yes_bid increase by ≥ 0.5¢, and these are all on the
spot-side (above spot for downward-trending impl. prob, below for upward),
buy YES at the strike one step beyond the markup band.
**Exit:** TP +4¢ or 10 min timeout, SL -4¢.
**Size:** $4/trade.
**Why untested:** Requires cross-strike feature engineering across multiple
markets per timestamp — prior reports treated strikes independently.
**Failure mode:** Order flow signals fade fast; by the time we identify it,
the next strike is already marked.

### H35 — Calm-Regime Tier-2 Boost
**Intuition:** T2 wins 98% in backtest but breaks during vol spikes. Bucket
events by realized vol over last 60min; T2 should be especially profitable in
the bottom tercile.
**Entry:** All T2 conditions + `realized_60min_sigma < 60min_sigma_p33` for the
event's history.
**Exit:** Same as current T2 (hold to settle).
**Size:** Bump from current $5 to $10 in calm regime.
**Why untested:** Prior backtests applied uniform sizing across regimes.
**Failure mode:** "Calm" predicts "future calm" only weakly.

### H36 — Adjacent-Strike Binary Strangle Pin
**Intuition:** When spot is straddling two adjacent strikes (within $100 of
each, TTC ∈ [3, 10]min), exactly one of (YES on K_below, YES on K_above) will
settle to $1. If `ya(K_below) + ya(K_above) < $1.00 - 2×fee`, we have a
strangle long that pays exactly $1 minus our entry, guaranteed.
**Entry:** spot ∈ [K_below + $20, K_above - $20], TTC ∈ [180, 600]s,
`ya_below + ya_above + fees_total < 0.97`.
**Exit:** Hold to settlement. One leg pays $1, other pays $0. Net = $1 - cost.
**Size:** Limited by min(ya_below_qty, ya_above_qty).
**Why untested:** Requires multi-leg simultaneous order; binary strangle isn't
in any prior hypothesis list.
**Failure mode:** Both legs fail to fill simultaneously → exposed to spot move.

### H37 — Spot-Distance Adaptive T2
**Intuition:** T2 wins more when spot is "very far" ITM vs "barely" ITM. Edge
should scale with `|spot - strike| / (σ × √TTC)` (i.e., d_sigma).
**Entry:** All T2 conditions + `dist_sigma ≥ 3.0` (currently effectively ≥ 1).
**Exit:** Hold to settle.
**Size:** Bump to $10/trade.
**Why untested:** Prior T2 used dollar distance, not sigma-normalized.
**Failure mode:** Tightening to ≥3σ may yield <10 trades → no signal.

### H38 — Same-Side Depth Imbalance
**Intuition:** When yes_bid_qty >> yes_ask_qty (e.g., 5x), there's standing
demand at the bid; market makers will rotate up. Buy YES at ask, sell at
inflated bid 60s later.
**Entry:** `yes_bid_qty ≥ 5 × yes_ask_qty`, `yes_bid_qty ≥ 50`,
TTC ∈ [600, 3600], price ∈ [0.20, 0.80] (mid-book).
**Exit:** 60s timer OR if yes_bid rises ≥ 2¢ (take profit). SL -3¢.
**Size:** $5/trade.
**Why untested:** Prior tests didn't use depth-imbalance features.
**Failure mode:** Imbalance often signals impending opposite move (large bid
about to get hit, MM steps away).

### H39 — Pre-Open Persistence Continuation
**Intuition:** When a NEW event opens, the first 5 minutes set a "ranging"
band. Strikes well outside this band (≥$200) at minute 5 typically remain
outside until close. Buy NO on those.
**Entry:** Event age ∈ [300, 600]s, `(strike - spot) > $200` (above-spot),
spot has not crossed strike since open, yes_bid ∈ [0.10, 0.30].
**Exit:** Hold to settle or 30 min, whichever first.
**Size:** $3/trade.
**Why untested:** Prior tests treated all event ages uniformly.
**Failure mode:** Sample size — only one "minute 5" per hourly event.

### H40 — Implied Probability Surface Smoothing
**Intuition:** The strike chain implies a probability density. If one strike's
implied prob is >1.5¢ off the smoothed (cubic spline) value of its neighbors,
that strike is mispriced. Buy/sell the side that brings it toward smoothed.
**Entry:** Fit smoothed implied curve across all active strikes. Pick the
strike with largest absolute deviation if `|deviation| ≥ 1.5¢ AND distance
from edge of chain ≥ 2 strikes`. If actual > smoothed, buy NO; if actual <
smoothed, buy YES. Require TTC ≥ 30min, price ∈ [0.10, 0.90].
**Exit:** Hold to settle or 20 min timeout.
**Size:** $4/trade.
**Why untested:** Requires per-timestamp curve fit; prior tests used pairwise
strike comparisons.
**Failure mode:** "Deviations" may reflect genuine information (e.g., the
strike contains a round number, MMs are aware).

---

## Backtester requirements
- Tick-level replay using `ws_orderbook_top_dedup` (16M rows) ordered by `received_at_ns`
- Spot from `coinbase_ticker_all` joined by nearest-prior timestamp
- Decisions use only data with `ts < t_now` (no peeking)
- Fee = min(7¢, 7% × entry_price), entry only
- Latency model: 200ms from decision-tick to order-on-exchange
- Fill model: marketable order against the inside, up to displayed qty.
  If size > qty available within 200ms after order placement → partial fill at displayed depth, reject the rest
- Position limits: max 4 concurrent across all strategies
- Settle using `event_settlement_spot` for ground truth at close
- Output per-strategy: N, win%, total PnL, mean PnL, stddev PnL, Sharpe, max-DD, t-stat
