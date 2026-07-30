# RV_DERIVATIVES — Relative-value derivatives & the platform-expansion method

*Why a modeling-heavy convertible / RV niche is the right "boring durable" workstream
to build a real firm around — and the explicit tail risks that have killed every prior
generation that ignored them.*

## 0. TL;DR

- **Convertible market is structurally back in 2026:** $300–500B outstanding, $120B
  issuance in 2025 (record), 2026 issuance up >550% YoY (AI / utilities refinancing).
- **The RV gap is empirically real:** academic + practitioner evidence converges on a
  **10–15% implied-vol discount** in converts vs listed options, with **26% smaller
  offering discount** for issuers whose stocks also have listed options.
- **The "boring" version of the trade — buy cheap-vol converts, delta-hedge, optionally
  hedge credit with CDS — is well-known.** It's not a closet-novel edge; it's a
  modeling-discipline edge that you have to be willing to build the machinery for, and
  most retail+small-fund operators won't. That's the gap.
- **Tail risk is the entire game.** Convert arb has blown up twice in modern memory
  (2005 GM, 2008 crisis: -34% Sep–Nov 2008 alone). The right framing is *carry with
  embedded tail* — model that explicitly or you'll be the next data point.
- **The portable asset is the Step-2 risk infrastructure** (pricing + Greeks + hedging
  + risk attribution). That same machinery unlocks dispersion, capital-structure arb,
  SPAC warrants, and (per `NOVELTY.md`) prediction-market RV. Owning the
  *infrastructure*, not any single trade, is what makes this a firm.

## 1. The convertible relative-value mispricing

### Mechanism (Section 1 mandatory)

A convertible bond is a corporate bond plus an embedded equity call (the conversion
option). Theoretical value:

```
V_convert = PV(coupons + principal under credit risk)  +  C(equity call, kappa shares, strike S0/kappa)
```

The embedded call has an implied vol you can solve for. **It tends to trade at a
discount to listed-option vol on the same name** — 10–15% IV discount is documented;
the gap narrows as the bond seasons.

### Why does this gap exist and persist?

- **Dealer-intermediated, low-liquidity market.** Round-lots are $1M-$5M par.
  Bid-ask wide. Many holders cannot delta-hedge themselves and accept the bond as a
  pure cash instrument.
- **Forced / non-economic sellers.** Issuers raising capital (convert as cheap-funding
  vehicle), funds redeeming, downgrades forcing investment-grade managers to sell.
- **Modeling burden.** Properly pricing a convert requires (i) credit-adjusted bond
  floor, (ii) call/put/forced-conversion logic, (iii) coupling between equity and
  credit, (iv) free boundaries. Most participants don't model it correctly and rely
  on dealer quotes.
- **Capital lock-up.** Holding to capture the gap requires posting and holding capital
  for months-to-quarters. Funds with monthly liquidity terms can't.

The **persistence story is structural and behavioral.** Big funds already work this
space (Citadel, Holocene, JS Asset Management, Linden, Calamos). The gap small
operators can target is the **mid-cap and small-cap convert universe** — too small
($50M–$300M deal size) for the big convert-arb funds to bother with, modeling burden
identical to large deals.

### The RV cheapness signal — concrete spec

For each convert in the universe each day:

1. Pull bond terms (face, coupon, maturity, conversion ratio, call/put schedule).
2. Pull current stock price S, dividend yield q.
3. Pull risk-free curve r and credit spread lambda (from CDS if available, else from
   bond-floor calibration on senior straight bonds).
4. Solve `implied_vol(market_price)` using `convert_model.py:implied_vol()` — this is
   the **convert-embedded equity vol**.
5. Compute the **vol gap**:
   ```
   vol_gap = listed_atm_iv  -  convert_implied_vol
   ```
6. **Long candidate:** vol_gap > 5% (convert cheap on vol). **Short candidate:**
   vol_gap < -5% (convert rich — rare, usually means credit assumption is wrong).
7. Construct the hedge:
   - **Delta hedge:** short `delta * kappa` shares per bond.
   - **Credit hedge (optional):** buy CDS on the issuer in matching notional × credit_delta.
   - **Funding:** repo the convert; net carry includes coupons, dividends paid on the
     hedge short, and CDS premium.

### Expected economics (sanity-check numbers, not a backtest)

For a textbook "10% vol gap" position:
- Vega per 1% on a 4-yr at-money convert ≈ $0.47 per bond per vol point (from our
  model demo).
- Hold for vol-gap normalization over 6–12 months → capture ~5pts of vol → +$2.35 per
  bond per round trip ≈ 2.4% per round trip on the bond.
- Net of round-trip cost (50–150bp dealer markup + financing + hedge slippage):
  **expected +1.0% to +2.0% per position before any tail event**.
- 4–8 positions a year (constrained by universe + capital lockup) → **+5% to +12% gross
  on bond-equivalent notional** in benign regimes.
- Sharpe in benign regimes: 1.5–2.5 (literature range for the strategy).

**This is a "modest carry + tail" payoff distribution.** The same shape as funding arb,
but with much higher capital requirements and a *known historical -34% tail*.

## 2. Tail and crowding risk — do not skip

The two case studies every convert arb operator must internalize:

### 2005 GM downgrade

- By 2004–05, hedge funds **owned 80–85% of new convertible issuance.**
- May 2005 GM downgrade → forced credit sellers → convert spreads widened → arb funds
  lost ~8% in Q1+Q2 2005 (worst since 1994).
- Investor redemptions forced funds to sell **up to 40% of holdings over the next year**.
- Multiple funds shut.

### 2008 financial crisis

- Convert arb funds **lost ~34% in 2008**, nearly all of it Sep–Nov.
- Distinct cause from 2005: **prime brokers cut convert-arb lending lines** as Lehman /
  Bear Stearns / AIG forced firmwide deleveraging.
- The position was fundamentally fine; the *financing* was broken.
- Short-sale bans on financials briefly broke the equity-hedge side.

### What this means for you (small operator)

- **Don't lever the position aggressively.** The 2008 blow-up was a leverage-and-
  financing event, not a model event. At 1× leverage the position would have drawn
  down but recovered. At 4–6× (typical fund leverage), it killed firms.
- **Don't be the marginal holder in a crowded vintage.** The 2026 AI-utility issuance
  wave is exactly the kind of single-sector cluster that produced 2005.
- **Model crowding explicitly:** track aggregate convert-arb AUM (Eurekahedge /
  HFR publish), prime-broker financing terms, and concentration of new issuance by
  sector. When all three are elevated, **cut size pre-emptively** — don't wait for the
  drawdown.
- **Build the position as carry + cheap convexity, not as carry + sold-tail.** The
  trade is supposed to be long convex (you own the embedded call). Don't unwittingly
  turn it short-convex by stripping the vol hedge.

This explicit tail discipline is **what allocators want to see in a fundability story.**
"Convert RV with a measured-tail-and-crowding overlay" is a sellable narrative;
"convert RV that just happened to make 12% last year" is not.

## 3. Generalization — same modeling engine, multiple markets

The Tsiveriotis-Fernandes-style coupled-PDE framework + Greeks engine + credit-aware
pricing engine is portable. Adjacent niches the **same machinery** unlocks:

### 3a. Capital-structure arbitrage (priority: HIGH)

One issuer trades across (i) equity, (ii) senior bonds, (iii) subordinated bonds,
(iv) CDS, (v) converts, (vi) listed options. The Merton structural model implies
relationships between equity vol, asset vol, credit spread, and bond price. When they
disagree, trade the cheaper instrument against the rich one. **Same model already in
`convert_model.py` (the credit_delta Greek IS the link).**

**Universe:** all US-listed names with both CDS and listed options (~200 names).
Capacity: medium-small ($10M-$100M/name). Big funds run this but the mid-cap end is
neglected.

### 3b. Vol relative value — dispersion, skew, term structure (priority: MEDIUM)

- **Dispersion:** index vol vs basket of single-name vols. Standard SPX / SPY vs
  basket. Capacity is large; competition fierce. Where a small op can win: sector
  ETFs (XLF, XLE) vs constituent baskets — less crowded.
- **Skew RV:** 25-delta put vs 25-delta call vol vs realized higher moments. Modeling
  identical to convert vol modeling; data infrastructure overlaps.
- **Term-structure RV:** front-month vs 3-month implied vol vs realized. The
  systematic version of "sell rich short-dated vol".

### 3c. SPAC warrants & redemption mechanics (priority: MEDIUM-LOW)

SPAC warrants are call options on the de-SPAC equity. Mispricing emerges during:
- The 30-day redemption window (forced cash flow).
- Post-de-SPAC initial trading (liquidity-constrained price discovery).
The TF engine prices these directly with a credit spread = 0 and short maturity.

### 3d. CoCos / preferreds / hybrids (priority: MEDIUM)

European bank CoCos (contingent convertibles) trigger at a Tier-1 capital ratio
threshold. The trigger-prob estimate is the modeling gap. TF engine extension: replace
"conversion ratio kappa" with a state-dependent trigger function.

### 3e. Basis trades — CDS-bond, cash-futures (priority: HIGH for diversification)

CDS-bond basis: spread on a bond vs spread on a same-issuer same-maturity CDS should
equal zero in arbitrage. It rarely does — historically -200 to +200bp gaps. Lower
expected return than convert RV but **uncorrelated**.

### Ranking by "same infrastructure unlocks new capacity"

| Niche | New capital deployable | Reuses model | Effort |
|---|---|---|---|
| Convert RV (core) | $5M-$50M | — | Have already |
| Capital-structure arb | $10M-$100M | 90% | 2-4 weeks port |
| Dispersion (sector ETF) | $5M-$25M | 70% | 4-8 weeks port |
| Skew / term-structure RV | $10M-$50M | 60% | 4-8 weeks |
| SPAC warrants | $1M-$10M | 80% | 1-2 weeks |
| CoCos | $10M-$50M | 60% | 6-8 weeks |
| CDS-bond basis | $20M-$200M | 50% | 4-6 weeks |

**Total Step-3 expansion capacity: $60M–$485M** if all pods exist and are filled. This
is the fundability number — and it's larger than the funding-arb sleeve.

## 4. The platform-expansion map (Steps 3–5)

What it actually takes to turn the Step-1/2 convert RV niche into a multi-pod firm:

### Step 3 — hire specialists (12–24 months out)

Skills you need to add, in priority order:

1. **A CDS / credit specialist** — to run capital-structure arb (3a). Reuses the
   convert pricing model; needs CDS data (Markit, Bloomberg) and prime-broker CDS
   lines. Compensation: 25-50% of pod PnL, common structure.
2. **A vol / dispersion specialist** — to run 3b. Needs OptionMetrics / live option
   feed.
3. **An equity-derivatives sales person (not a quant)** — to source forced sellers
   and dealer flow. *This person matters as much as the quant talent.*

### Step 4 — central risk function (immediately on first hire)

The infrastructure to build BEFORE hiring anyone:

- **VaR / stressed VaR aggregator** across all pods. Daily.
- **Position concentration limits** — per issuer, sector, vintage.
- **Counterparty / prime-broker exposure dashboard** (the 2008 lesson).
- **Liquidity / unwind-time estimator** per position (the 2005 lesson — knowing how
  fast you could exit if forced to).
- **Stress scenarios:** GM 2005 replay, Lehman 2008 replay, COVID March 2020 replay.

### Step 5 — keep expanding

Pods to add once 3a-3e are running:

- Tax-equity / municipal arb (small, esoteric).
- ETF basket arb (XLF vs constituents at the close).
- Bond-future / cash-bond basis (CME deliverable basket).
- **The cross-domain bet from `NOVELTY.md`:** RV-modeling discipline applied to
  prediction markets (where capacity is currently small but growing — see
  `NEXT_SHIFT.md` rank #1).

**The pitch to allocators in 24 months:** "We started in convert RV with $5M of
proprietary capital, built portable pricing + risk infrastructure, expanded into [3
pods], aggregate Sharpe ~3 with explicit tail risk modeling, and have a clear roadmap
into [3 more pods] including a novel application of our RV machinery to prediction
markets. Total deployable AUM at scale: ~$200M–$500M before capacity drag."

That is a credible mid-9-figure multi-strategy firm narrative built on a single
modeling-discipline edge. It's also the path that has the lowest survivorship-bias
problem: every step is incremental and grounded in the prior step's infrastructure.

## 5. What's actually executable in the next 90 days

Honest scope. You cannot become a convert-arb firm in 90 days. What you CAN do:

1. **Acquire one convert + listed-option + bond pair** as a working test case
   (~$1K-$2K of data: FINRA TRACE for the bond price, public 10-K for terms, free
   listed-option chains from Yahoo/CBOE). Run `convert_model.py` end-to-end.
2. **Compute the vol gap on ~10 names** across mid-cap converts. Identify the 2 with
   the biggest gap. **Don't trade them yet** — just confirm the signal is real and
   model is calibrated.
3. **Write the 2-pager pitch deck** for what a convert-RV + capital-structure-arb
   firm looks like. Use the numbers in §3 of this document.
4. **Track convert-arb crowding indicators** monthly (HFR convert-arb index AUM,
   issuance by sector, prime-broker convert-financing rates).
5. **Compare against your existing edges' opportunity cost.** Funding-arb alone earns
   ~10-22% APR with much lower modeling burden. The convert-RV thesis only wins if
   it credibly scales to $50M+ — which it does, but only over 12–24 months.

## 6. The honest verdict

- **As a *trade* for the next 6 months: skip.** Funding arb + K6 + the Section-6
  prediction-market positioning yield more $/effort.
- **As a *firm-building infrastructure investment*: build it.** This is the
  workstream that makes you fundable beyond personal capital, and the convert engine
  + risk framework is a 6–12 month build that pays for itself in optionality.
- **The trap to avoid:** lever the convert sleeve to chase yield. Every blow-up in this
  strategy's 30-year history is a leverage-and-financing story, not a model-failure
  story. Run it unlevered and small, or don't run it.

---

*Refs:*
- [Tsiveriotis-Fernandes 1998 background](https://arxiv.org/pdf/1111.2683)
- [Convert market size 2026](https://markwideresearch.com/convertible-bond-market)
- [2026 issuance +556% YoY](https://www.indexbox.io/blog/ai-companies-fuel-record-us-convertible-bond-issuance-in-2026/)
- [2005 GM convert-arb event + 2008 -34%](https://www.stern.nyu.edu/sites/default/files/assets/documents/con_043324.pdf)
- [Arbitrage crashes and the speed of capital (Mitchell-Pulvino)](https://conference.nber.org/confer/2010/Risks10/Mitchell_Pulvino.pdf)
- [Empirical convert IV vs listed IV gap](https://onlinelibrary.wiley.com/doi/10.1002/jcaf.22429)
- [Review of Finance: options add value via convert pricing](https://academic.oup.com/rof/article/27/1/189/6510954)
- [Convert arbitrage 2023-2025 comeback](https://resonanzcapital.com/insights/convertible-arbitrage-the-2023-2025-comeback)
