# Kalshi-only Strategy Hypotheses

Constraints from prior memory:
- Fee floor: min(7¢, 7%) on YES/NO → ~57% win rate needed on directional at $0.50
- T1 monotonicity arb already deployed (depth-capped at 5)
- Calm-regime filter: skip when |spot_move_30s| > $30
- Sentiment is not where edge is

So I need strategies that are EITHER:
(a) **Maker-only** (no fees, just rebate / spread capture)
(b) **Multi-leg arb** (low net fee, hedged)
(c) **High-edge directional** where expected EV >> 7%

## A. Pure-arb (multi-leg) hypotheses

K1. **YES+NO cross arb** — when yes_ask_exe + no_ask_exe < 1.00, instant guaranteed.
K2. **Cross-strike monotonicity** — P(BTC > $80,500) must be ≤ P(BTC > $80,400). Find violations.
K3. **Cross-strike bid-ask consistency** — buying NO @ strike X and YES @ strike X+100 hedges to nothing if spot ends between. Find mispricings.
K4. **Within-event arb portfolio** — sum of all single-bucket probabilities should be 1.0.
K5. **Cross-event consistency** — adjacent hourly events at the same strike should be related (cumulative drift).

## B. Spot-Kalshi latency / freshness

K6. **Stale-quote post-spot-jump** — when BTC moves >$20 in 10s and a Kalshi market hasn't repriced yet, fade.
K7. **Coinbase-Kalshi lag arb** — Coinbase ticker leads Kalshi orderbook by ~50-200ms; exploit.

## C. Timing / decay

K8. **Last-N-minute fade** — markets that are still 20-80% with <5 min to close, where spot strongly favors one side. Take the right side.
K9. **First-N-minute drift** — early in the hour, markets are wider; fade extreme prints to mid.
K10. **Settlement-pin convergence** — at strikes nearest spot, price drifts to 50%. Sell vol around the pin.

## D. Microstructure

K11. **Order-flow imbalance** — when yes_bid_qty / yes_ask_qty diverges from "fair" for current spot, signal.
K12. **Spread-tightening trade** — when spread widens out of regime, expect it to tighten.
K13. **Quote-cross detection** — yes_bid + no_bid > 1.0 means both sides paying premium — short the rich side.

## E. Vol & regime

K14. **Realized-vol regime filter on K1** — only do YES+NO arb in calm regimes (per my memory).
K15. **Quiet-hour spread harvest** — post passive maker orders on low-vol hours.
