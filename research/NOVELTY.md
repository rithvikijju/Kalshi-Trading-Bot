# NOVELTY — cross-domain transfer candidates

*Where genuine novelty (not "no one has thought of this", but "structural reason it's
still available") shows up in capacity-constrained markets a small operator can
actually trade.*

## 0. The default prior

> An idea that feels novel is usually one of three things: already known under a
> different name; an artifact of overfitting; or genuinely novel and therefore
> decaying the moment it's used at size.

I'm raising the bar on excitement, not lowering it. Every candidate below has been
prior-art-checked. Where prior art exists in informal/practitioner form (blog posts,
hedge-fund desk lore) but not in productized form, I say so explicitly.

## 1. Candidate: convert/RV vol-modeling discipline → prediction markets

### The cross-domain claim

The same modeling stack that prices a convertible's embedded equity call
(`research/convert_model.py` — TF coupled-PDE, vol calibration, Greek decomposition,
free-boundary handling) is **mathematically applicable to a Kalshi/Polymarket
binary**, which is also an embedded option on an underlying state variable:

- Kalshi BTC hourly strike at $X: contract pays $1 iff `BTC_close > X`. This is a
  **digital call** on BTC at strike X. Its risk-neutral price = `N(d2)` under
  Black-Scholes; under realistic distributions it requires a model that respects skew
  + jump risk + the time-to-expiration kink.
- Kalshi political-bucket contract: `P(candidate wins) = expectation over a
  distribution implicitly defined by polling + news`. Replace "vol" with "news
  arrival intensity", "delta" with "sensitivity to polling shock", "credit risk" with
  "resolution-mechanism risk" (i.e., what if the event doesn't resolve cleanly).
- Polymarket binary on macro outcomes: identical structure.

### Why this is plausibly novel (with caveats)

**Where prior art clearly exists:**
- "Cross-platform arbitrage" between Kalshi and Polymarket — well-known, multiple
  practitioner write-ups (Moontower, Privy blog, Trade-Ideas).
- "Use option chains as a cross-reference" — known. Substack posts discuss using BTC
  option IVs to sanity-check Kalshi BTC binary prices.
- Black-Scholes pricing of binary options — textbook.

**Where the gap is:**
- **No productized RV signal that systematically computes (Kalshi-implied vol)
  vs (Deribit listed BTC option vol) vs (realized vol)** across the Kalshi BTC
  binary surface and trades the gap. The Substack-level discussion is "look at this
  one strike," not "build the full surface daily."
- **The TF coupled-PDE framework specifically does not appear in the prediction-market
  literature.** That's because prediction-market binaries don't have a credit / cash
  component the way converts do. **But political/event contracts DO have an analogue
  — "resolution risk" (the contract might be voided or settled ambiguously) plays the
  role of credit spread.** This is a genuine modeling gap I haven't seen filled.

### The concrete RV trade construction

**Setup:** Daily, for each Kalshi BTC binary with at least 1h to settlement and at
least $5K of YES + NO open interest:

1. Pull Kalshi YES-mid → implied risk-neutral probability `q_kalshi`.
2. Pull spot BTC, Deribit at-the-money implied vol for the matching expiry `iv_deribit`.
3. Pull realized vol over matching horizon `iv_realized`.
4. Compute the **fair binary price** under Deribit-vol BS:
   `q_deribit = N( (ln(F/K) - 0.5*sigma^2*T) / (sigma*sqrt(T)) )`.
5. Compute under realized-vol BS: `q_realized = same formula with sigma = realized`.
6. **The RV signal:**
   ```
   gap_deribit  = q_deribit  - q_kalshi    (positive → Kalshi YES is cheap vs Deribit IV)
   gap_realized = q_realized - q_kalshi    (positive → Kalshi YES is cheap vs realized vol)
   ```
7. **Trade rule:** when both gaps exceed 3¢ AND have the same sign, buy the favored
   side. Hold to settlement. Hedge optionally with the corresponding Deribit option.

This **subsumes the K6 spot-displacement edge** (which is a special case: when BTC
spot has just moved past a strike, all three pricing methods agree that the contract
should price near 1.0 or 0.0, but the Kalshi MM hasn't repriced). It also extends K6
to non-displacement regimes where K6 currently doesn't trade.

### Validation gauntlet (Section 4)

- [ ] **Mechanism:** the gap exists because Kalshi MMs use a static
  Black-Scholes-like model with stale vol, while Deribit IV updates continuously.
  Persistent because Kalshi flow is dominated by retail directional traders and the
  DMM rebate structure rewards quoting tight on volume, not on edge.
- [ ] **Prior-art check:** ✓ done above. Practitioner-level discussion exists; full
  surface arbitrage system does not.
- [ ] **Why still available:** Kalshi's API + crypto-derivatives API integration is
  the entry barrier. Most practitioner traders don't have both. Most quant funds
  don't bother with $5K-OI markets.
- [ ] **Decay clock:** 12–24 months. Once Kalshi crypto volume crosses $1B/mo
  (currently ~$200M/mo), HFTs will close the surface.
- [ ] **Capacity:** Kalshi BTC binary depth median 17 contracts → ~$5K-$10K per signal
  → ~$50K-$200K/yr PnL ceiling at fully-utilized depth. **Same K6 capacity ceiling
  applies.** This is a small-operator-only trade. Cannot be funded externally.
- [ ] **Out-of-sample:** must validate on data the user hasn't already used to design
  K6. Use Polymarket BTC binaries (when available) as the OOS holdout.
- [ ] **Walk-forward:** required.
- [ ] **Net of costs:** Kalshi fee + Deribit hedge fee + spreads. At binary prices
  near 0.5, Kalshi fee is 1.75¢ per side → 3.5¢ round trip. The 3¢ gap rule is
  before-fees and likely needs to be 4–5¢ for net positive.
- [ ] **Multiple testing:** N=8 K6 buckets already tested; adding the Deribit-IV
  overlay is 1 additional test, low MT burden.

### Verdict on candidate 1

**STATUS: PROMISING, BUT IS A K6 EXTENSION, NOT A SEPARATE EDGE.**

The trade *is* novel as a productized system. But it's a *deepening* of the K6 edge
(already known and deployed), not a new capacity ceiling. **It's the right next 1-2
weeks of dev work on the Kalshi sleeve.** It does NOT change the scaling story.

The truly novel piece — applying the **resolution-risk component** of the TF model to
event contracts (political, macro) — needs a separate test on Polymarket / Kalshi
political markets. Worth a dedicated 2-week prototype after the BTC-IV-RV trade is
running.

## 2. Candidate: sports-pricing methodology → mid-tail Kalshi sports contracts

### The cross-domain claim

Sports betting markets have **40+ years of academic + practitioner pricing
methodology** that has not yet been imported into Kalshi sports contracts in any
systematic way:

- **Closing-line value (CLV) as the gold-standard edge metric** (Pinnacle's closing
  line is widely considered the sharpest publicly observable price).
- **Power-rating models** (Massey, Sagarin, FiveThirtyEight ELO derivatives, RAPM for
  basketball, EPV-style continuous-state models).
- **Microstructure of in-game lines** (steam moves, reverse-line movement).

Kalshi sports contracts on NBA, NFL, etc. are **binary options on outcomes whose
true probability is computable from the same models that price sportsbook lines.**

### Why this is plausibly novel

**Where prior art clearly exists:**
- Sportsbook-vs-sportsbook arbitrage (cross-book).
- CLV-based edge measurement (universal in sharp circles).
- Power-rating modeling.

**Where the gap is:**
- **Kalshi as a venue is too new for the sharp sports community to be properly set
  up on.** Most sharps trade with sportsbooks, where structural advantage (no vig,
  exchange-style order book) didn't exist until ~2024-2025.
- The natural arb: **Pinnacle closing line implies probability `p_pin`. Kalshi
  market mid implies `p_kalshi`. Trade the gap.** This is conceptually identical to
  cross-venue Kalshi/Polymarket arb but with a sharper reference price.
- **No academic literature has framed Kalshi as a binary-option surface on sports
  outcomes** (to my knowledge — and I'd suspect it because Kalshi sports was approved
  late 2024).

### Concrete construction

For each Kalshi NBA / NFL contract:
1. Pull Pinnacle closing line at corresponding moment.
2. Convert to fair probability (de-vig: `p_fair = p_yes / (p_yes + p_no)`).
3. Compare to Kalshi YES mid.
4. **Trade rule:** when |Kalshi_mid - Pinnacle_devig| > 3% AND volume > $5K, trade
   the cheap side.
5. Hold to event resolution.

### Validation gauntlet

- [ ] **Mechanism:** Pinnacle is the sharpest price (40-year track record);
  divergence from Pinnacle is divergence from truth. Kalshi-Pinnacle gaps persist
  because sharps haven't fully migrated to Kalshi and the Kalshi DMM layer (Citadel,
  Jump) prices off internal models, not necessarily Pinnacle.
- [ ] **Prior-art check:** Sportsbook-vs-Kalshi arb is **lightly discussed** on
  r/sportsbook and gambling Twitter. I have **not found a productized system**.
  Verdict: practitioner-aware but not productized.
- [ ] **Why still available:** Pinnacle blocks US IPs (legal). Most US Kalshi traders
  don't have a Pinnacle account. The set of operators with both Pinnacle access AND
  Kalshi access AND quant infrastructure is small.
- [ ] **Decay clock:** 18-36 months. Driven by (a) how fast US-friendly Pinnacle
  alternatives appear (Circa, BetMGM in some states), and (b) whether the Kalshi
  DMM layer starts pricing off Pinnacle directly.
- [ ] **Capacity:** Kalshi NBA team-futures markets have **$12M-$17M OI per market**
  (from `SESSION_HIGHLIGHTS`). This is ~4-5 orders of magnitude deeper than Kalshi
  BTC binaries. Capacity per game: $50K-$500K. Annual: **$1M-$5M of edge per year
  if 3% gap captured 200 games/yr.**
- [ ] **Legal:** Pinnacle access for US persons is a regulatory gray zone. Worth a
  lawyer-call before deploying. Alternative reference prices: Circa Sports (Nevada,
  US-legal, sharp).
- [ ] **Net of costs:** Kalshi fees + Pinnacle vig already de-vigged out.

### Verdict on candidate 2

**STATUS: HIGH-PRIORITY, RUN PRIOR-ART CHECK + LEGAL CHECK FIRST.**

This is the **single most interesting candidate** from this run. It:
- Reuses your existing Kalshi infrastructure (the K6 sleeve infra).
- Has **dramatically higher capacity** than Kalshi crypto binaries ($1M-$5M/yr vs
  $5K/yr).
- Sits squarely in the "small player has the work-edge, big firms ignore" thesis
  (Citadel doesn't have sportsbook accounts because they don't need to).
- Has a clear decay clock (12-36 months).
- Has a **clear hard gate: do you legally have access to Pinnacle or Circa odds at
  the closing moment?** If no, you cannot do this. If yes, this could be the next
  K6.

**Recommended next step:** spend 2 hours researching legal status of Pinnacle data
access for US persons, and Circa Sports data API availability. If green, this is a
2-week prototype.

## 3. Candidate: combining two known-thin edges into a fat one

### The cross-domain claim

Two of your existing edges are individually capacity-constrained: K6 (~$5K/yr) and
KCF (validated, blocked on infrastructure). Can they be combined?

Specifically:
- K6 trades on spot-displacement signals (BTC moves past a strike).
- KCF trades on capitulation-fade signals (aggressive sell flow predicts opposite).

**Hypothesis:** when K6 and KCF agree (both say "buy YES"), the win rate is materially
higher than either alone, allowing higher conviction sizing → more PnL per signal
even if signal count stays constant.

### Prior-art check

This is **strategy-stacking**, a standard practitioner technique. Not novel in itself.
The novel claim would be that the two signals are *empirically* uncorrelated.

### Quick math

- K6 win rate: 77%–99% depending on bucket.
- KCF win rate at p85 threshold: 84%.
- If independent: combined hit rate = 0.85 × 0.85 = 72% — *lower* than either alone
  because both must fire.
- BUT: combined-signal trades would have *higher conviction* → can size 2-3× larger
  per Kelly → more PnL per trade.
- AND: the combined-signal trades that BOTH fire on are a subset → lower expected
  count.

### Verdict on candidate 3

**STATUS: LOW-NOVELTY, MEDIUM-VALUE.**

Worth a 1-day backtest: do K6 trades that ALSO have a KCF signal in the same hour
show higher per-trade PnL? Answer determines whether to bother with the stacking
layer. Not a separate edge, just a sizing refinement.

## 4. Candidate: tokenized-treasury weekend funding arb

### The cross-domain claim

BUIDL/Ondo/Franklin tokens accrue Treasury yield 24/7 but the underlying T-bill
market is closed Saturday-Sunday. **The tokens should accrue weekend interest at the
risk-free rate; if they don't, that's a yield gap. If they do, the financing side
(USDC) is overpaying.**

### Prior-art check

This is being actively discussed in DeFi circles (Anchorage, Coinbase Custody) and
several DeFi protocols (Aave, Morpho) are building tokenized-treasury collateral
markets. Verdict: **known and competed.**

### Verdict on candidate 4

**STATUS: DEAD — TOO COMPETED.**

The tokenized-treasury yield-arb is being captured by DeFi yield aggregators
(Pendle, Morpho) already. A small operator would be late and capital-constrained
(KYC barriers for BUIDL access, ~$5M minimum for institutional issuance).

## 5. Summary table

| # | Candidate | Status | Why available | Decay clock | Capacity | Next step |
|---|---|---|---|---|---|---|
| 1 | Vol-RV → Kalshi BTC binaries | **PROMISING (K6 extension)** | API integration gap | 12–24mo | $50K-$200K/yr | 2-week dev sprint |
| 2 | Pinnacle/Circa odds → Kalshi sports | **HIGH-PRIORITY** | Legal + infra gap | 18–36mo | **$1M-$5M/yr** | Legal check first |
| 3 | K6 × KCF signal-stacking | LOW-NOVELTY | — | — | Marginal | 1-day backtest |
| 4 | Tokenized-treasury weekend arb | DEAD | Too competed | — | — | Skip |

## 6. The honest meta-finding

Genuine novelty for you in 2026 looks like **cross-domain transfer of well-understood
methods into capacity-constrained venues that haven't seen those methods yet**:

- **RV-model → prediction markets**: real but a K6 extension at best (small capacity).
- **Sports-pricing → Kalshi sports**: real, materially higher capacity, infrastructure
  gap is the wedge. **This is the run's most interesting standalone candidate.**

Both reuse infrastructure you already have (Kalshi WS consumer, BPP Bayesian update
framework, K6 trade-execution stack). Both have clear decay clocks. Both fail
gracefully if wrong (you find out fast that the gap doesn't exist or is too thin).

The Step-1/Step-2 platform from `RV_DERIVATIVES.md` and the cross-domain candidates
here **stack in one direction:** modeling discipline + capacity-aware venue selection +
infrastructure reuse. That stacking *is* the firm-building story.

---

*Refs:*
- [Cross-platform arb between Kalshi/Polymarket](https://www.kucoin.com/blog/kalshi-surpasses-polymarket-in-global-trading-volume-with-22b-valuation-what-does-it-mean)
- [Substack: math of prediction markets](https://navnoorbawa.substack.com/p/the-math-of-prediction-markets-binary)
- [Moontower: prediction-market arb using option chains](https://moontowermeta.com/prediction-market-arbitrage-using-option-chains-to-find-mispriced-bets/)
- [Kalshi macro contracts forecast crypto vol — arxiv](https://arxiv.org/pdf/2604.01431)
- [Prediction markets go institutional](https://cryptodaily.co.uk/2026/05/hedge-funds-kalshi-polymarket-prediction-markets)
