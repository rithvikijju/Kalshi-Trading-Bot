# SHORTLIST — surviving edges & verdicts

*Final ranked output of the `/goal` run, 2026-06-08. Ranked by
mechanism × capacity × robustness of evidence (NOT by backtest Sharpe alone).*

## Surviving edges

### 1. Funding-rate arb (ETH spot / Hyperliquid perp) — DEPLOYED

- **Status**: Already validated, paper-running since May 18 2026.
- **Mechanism**: Retail leverage demand → structurally positive funding → collect.
- **Capacity**: $50M before slippage drag.
- **Honest expected return**: 11% APR current regime, 22% APR long-run median.
- **Tail risk**: Basis blow-out (LUNA / FTX class). Modeled in `funding_arb_bias_audit`.
- **Fundability**: ✓ A real LP-fundable sleeve. The narrative needs the bias audit
  attached (Sharpe 6, not 14).
- **Card**: [k6_spot_displacement.md](candidates/funding_arb_eth.md)

### 2. K6 Kalshi spot-displacement — DEPLOYED

- **Status**: Validated, deployed, running.
- **Mechanism**: Kalshi MMs lag 200ms-2min repricing after fast BTC moves.
- **Capacity**: ~$5K/yr **ceiling** (Kalshi book depth-bounded).
- **Honest expected return**: +1500-3000% APR on $100, drops to <100% by $5K.
- **Fundability**: ✗ Too small for any external capital. Personal-account amplifier.
- **Card**: [k6_spot_displacement.md](candidates/k6_spot_displacement.md)

## New candidates evaluated this run

### 3. Pinnacle/Circa odds → Kalshi sports — **HIGH-PRIORITY, GATED ON LEGAL CHECK**

- **Status**: Designed, prior-art-checked, NOT yet backtested.
- **Mechanism**: Pinnacle closing line is sharpest reference; Kalshi mid diverges by
  3-10¢ on mid-tail sports markets; sharps haven't migrated to Kalshi yet.
- **Capacity**: $1M-$5M/yr — **2-3 orders of magnitude larger than K6.**
- **Hard gate**: Legal access to Pinnacle (or Circa) data for the operator.
- **Verdict**: **The most interesting standalone candidate from this run.** Reuses
  Kalshi infrastructure, capacity is materially larger, decay clock is 18-36 months.
- **Next step**: 2-4h legal/data research, then 1-2 day prototype if green.
- **Card**: [pinnacle_kalshi_sports.md](candidates/pinnacle_kalshi_sports.md)

### 4. Kalshi BTC binary vol-RV with Deribit IV overlay — K6 EXTENSION

- **Status**: Designed.
- **Mechanism**: Same K6 mechanism + vol-mismatch with Deribit IV.
- **Capacity**: Same ~$5K/yr ceiling as K6 (Kalshi depth-bound).
- **Verdict**: Worth building (2-week sprint). Subsumes K6 + extends to non-
  displacement regimes. **Not a separate edge**, a K6 deepening.
- **Card**: [kalshi_btc_iv_rv.md](candidates/kalshi_btc_iv_rv.md)

## Edges that died or were skipped this run

- **Tokenized-treasury weekend funding arb** — too competed (Pendle, Morpho, DeFi
  yield aggregators already capturing).
- **K6 × KCF signal stacking** — low novelty, useful only as a sizing refinement.
- **Re-test of microstructure / statarb / ML in Kalshi crypto binaries** — skipped
  per prior `MEMORY.md` (50-strategy zoo already killed these).
- **Re-test of cross-exchange alt-perp carry** — skipped per `xchg_carry_audit`
  (Sharpe 1-5 honest, not Sharpe 25).
- **Tokenized-treasury cross-issuer yield arb** — needs $5M+ to be capital-efficient.

## Single most credible novel candidate + decay clock

> **Pinnacle/Circa → Kalshi sports.**
> **Decay clock: 18–36 months.**
> Driven by (a) Pinnacle alternatives emerging for US persons, (b) Kalshi DMMs
> wiring up to Pinnacle directly, (c) sharp community migration. *Build it before
> any of these happen.*

## Blockers hit this run

- **No subscription data:** OptionMetrics, Markit CDS, FINRA TRACE bulk all gated
  behind paid licenses. Convertible-RV backtesting requires these and cannot be done
  in this environment. → Built a reference implementation (`convert_model.py`) the
  user can plug paid data into.
- **No Pinnacle/Circa API access:** can't validate the cross-domain sports candidate
  empirically without it. → Documented the prerequisite explicitly as the first gate.
- **Polymarket API integration:** not present in this repo. → Documented as the
  "best directional next step" but not built.

## Single best direction to dig deeper on next

**Run the 2-4 hour legal/data check on Pinnacle and Circa access.** If green, run the
1-2 day historical-gap prototype on the 100 most recent Kalshi NBA games. Either:

- Green and gap exists → **2-week prototype → live paper → live money. Highest-EV
  path of anything in this report.**
- Green and gap is too thin → **Save the operator months of dev work; falls back to
  candidate 4 (K6+Deribit) as the next sprint.**
- Red on legal → **Pivot to building the Polymarket-Kalshi cross-venue router from
  `NEXT_SHIFT.md` rank 1.** Same flow-direction, smaller capacity (~$50-500K/yr) but
  no legal gate.

## Deliverables produced this run

- `research/log.md` — honesty trail.
- `research/SHORTLIST.md` — this file.
- `research/NEXT_SHIFT.md` — forward-looking market-structure inflection memo.
- `research/RV_DERIVATIVES.md` — convert/RV thesis + platform expansion map.
- `research/NOVELTY.md` — cross-domain transfer candidates.
- `research/convert_model.py` — Tsiveriotis-Fernandes pricer + Greeks + implied vol.
- `research/candidates/*.md` — per-candidate cards (4 candidates documented).

## What this run did NOT do (be honest)

- **Did not run new live-data backtests.** Convertible RV needs subscription data.
  Pinnacle/Kalshi sports needs API access. Polymarket cross-venue arb needs both APIs.
- **Did not produce a new deployed edge.** What it produced was: a forward strategy,
  a modeling infrastructure asset, two new candidate trades with clear next steps,
  and an honest assessment of which existing edges to keep running.
- **Did not rebuild what was already done.** Prior research (50-strategy zoo, VPIN,
  funding-arb bias audit, K6 deployment) is referenced but not re-litigated.

---

*Closing thought:* The two real surviving edges (funding arb + K6) account for ~$50M
of capacity in aggregate. The single new candidate with the best ratio of
expected-value to effort is the Pinnacle-Kalshi sports trade. The medium-term
firm-building work is the convertible-RV platform. Everything else in the broader
literature has either been killed already or doesn't survive contact with retail-
operator constraints (fees, depth, data access).
