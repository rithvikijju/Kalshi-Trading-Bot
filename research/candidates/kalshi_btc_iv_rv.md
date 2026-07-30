# Kalshi BTC binary vol-RV with Deribit IV overlay — NEW CANDIDATE (K6 EXTENSION)

## Thesis
Treat each Kalshi BTC hourly binary as a digital call on BTC. Compute its
implied risk-neutral probability `q_kalshi`. Compute the "model" probability under
Deribit at-the-money IV `q_deribit` and under realized vol `q_realized`. When
`q_deribit - q_kalshi > 3¢` AND `q_realized - q_kalshi > 3¢` (both gaps agree),
buy the favored side. Optionally hedge with a Deribit option.

## Why the mispricing exists
- Kalshi BTC binary MMs use a static Black-Scholes-like model with stale vol input.
- Deribit IV updates continuously off live order flow.
- Kalshi retail flow is dominated by directional traders chasing strikes, not by
  vol traders cross-referencing Deribit.

## Relation to existing K6 edge
- K6 fires only on spot-displacement (BTC > $50 past a strike).
- This trade fires when ANY of {spot-displacement, vol-cheap-on-Kalshi, vol-rich-on-
  Kalshi} triggers a meaningful (q_deribit - q_kalshi) gap.
- **It subsumes K6** (K6 special case: displacement → both sides agree the contract
  should price near 0 or 1) **and extends K6 to non-displacement regimes.**

## Mechanism
Same K6 mechanism (MM repricing lag) + vol-mismatch mechanism (no real-time Deribit
linkage in the Kalshi pricing function).

## Naive baseline (must beat)
K6 alone. Adding the Deribit overlay should produce signals K6 misses without
sacrificing K6 hit rate.

## Validation gauntlet
- [ ] **Mechanism**: ✓
- [ ] **Prior-art**: practitioner-aware (Substack write-ups exist), not productized
- [ ] **Why still available**: dual-API integration gap (Kalshi + Deribit)
- [ ] **Decay clock**: 12-24 months
- [ ] **Capacity**: ~$50K-$200K/yr at K6-style depth. **Does not break $5K K6 ceiling
  meaningfully — same Kalshi depth constraint applies.**
- [ ] **OOS**: required (Polymarket BTC binaries when available, or 50/50 split)
- [ ] **Costs**: Kalshi fee 3.5¢ round trip at p=0.5; 3¢ gap rule must rise to 5¢ net
- [ ] **Multiple testing**: low (1 additional layer over existing K6 surface)

## Capacity
**Same as K6.** $50K-$200K/yr is the realistic ceiling. **This is a small-account
amplifier, not a scaling edge.**

## Decay risk
- Deribit-Kalshi integration becomes table-stakes as Kalshi BTC volume grows.
- Kalshi MMs upgrade to live-IV pricing.

## Verdict
**STATUS: WORTH BUILDING (2-week dev sprint), but NOT a separate edge — a K6
deepening.** Builds on existing infrastructure. Expected outcome: 1.5-2× the K6
signal count without lowering hit rate.

## Next step
1. (2d) Pull Deribit ATM IV API integration; replay against K6 historical signals.
2. (3d) Compute the gap distribution; confirm 3¢+ persistent signals exist outside
   K6 displacement bucket.
3. (1 week) Paper-trade alongside K6 as a parallel runner.
