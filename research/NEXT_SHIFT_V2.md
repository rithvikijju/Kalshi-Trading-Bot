# NEXT_SHIFT_V2 — firm-scale market-structure inflections

*Written June 2026. Mandate: where is large flow migrating that a mathematically-
sophisticated entrant could intermediate at $50M–$500M capacity, ideally compounding
toward $1B+? Citadel-grade decomposition: new flow, incumbent gap, choke point,
capture mechanism, capacity, kill case.*

V1 of this memo focused on Polymarket/Kalshi and AI-agent flow — correctly scoped for
a small-capacity sleeve. Those candidates are now OUT of scope (see redirect note at
end). This memo only entertains shifts whose realistic capture envelope is
≥ $50M of deployed capital before slippage eats edge.

---

## Executive ranking

| # | Shift | Capacity | Math/tech edge | Verdict |
|---|---|---|---|---|
| **1** | **Crypto-derivatives institutionalization: IBIT-options ↔ CME ↔ Deribit cross-venue vol-surface** | **$200M–$500M** | High | **BUILD** |
| 2 | High-yield credit electronification (algorithmic PT pricing + HY/EM long-tail MM) | $150M–$1B+ | Medium-High | Build if you can hire the credit DNA |
| 3 | T+1/T+2 FX ↔ ETF creation-redemption plumbing arb | $100M–$300M | Medium | Build with prime broker / AP partnership |
| 4 | Dispersion 2.0: post-March-2026 single-stock vol vs SPX | $100M–$500M | High | Crowded but real; needs differentiated risk model |
| 5 | Crypto-tradFi convergence (regulated perps, 24/7 CME) basis | $50M–$200M | Medium | Already partially captured by Jane/Jump |
| 6 | RWA / tokenized-treasury cross-issuer + cross-chain | $20M–$80M | Low | Below mandate; skip |
| 7 | 0DTE pure liquidity provision | <$50M effective net of competition | Low-Medium | Captured; skip |

The #1 pick is crypto-derivatives vol-surface arbitrage. Reasoning below — the
short version is that the IBIT/CME/Deribit triad is the only place I can find where
a $200M+ vol book can be deployed against a real, structurally-persistent
inefficiency that the biggest incumbents are either regulatorily blocked from
fully arbing or have institutional reasons to under-allocate to.

---

## 1. Crypto-derivatives vol-surface arbitrage (IBIT options ↔ CME ↔ Deribit) — **TOP PICK**

### New flow

- **IBIT options open interest: $27.6B in April 2026, briefly overtook Deribit's
  $26.9B for the first time. IBIT now ~52% of total Bitcoin options OI** — an
  all-time high US-regulated share ([Coindesk](https://www.coindesk.com/markets/2026/01/13/bitcoin-options-open-interest-extends-dominance-over-futures-damping-btc-volatility), [CoinLaw](https://coinlaw.io/options-market-in-crypto-statistics/)).
- **CME crypto ADV: 407,200 contracts YTD 2026, +46% YoY. $3T notional in
  2025**. CME went 24/7 on May 29, 2026 — the first time a regulated US futures
  exchange has been continuously open on a major asset class
  ([CME](https://www.cmegroup.com/media-room/press-releases/2026/2/19/cme_group_to_launch247cryptocurrencyfuturesandoptionstradingonma.html)).
- **Deribit Feb 2026 BTC options: $79.5B / 1.12M contracts** — third-highest
  monthly print on record. **Deribit still owns >90% of ETH options share**
  ([CoinLaw](https://coinlaw.io/options-market-in-crypto-statistics/)).
- **CFTC approved regulated Bitcoin perpetuals on May 29, 2026** — first US-listed
  perps from a regulated venue. New surface, new basis term, new participants
  forced in by mandate ([Phemex](https://phemex.com/blogs/cftc-approves-regulated-bitcoin-perpetuals)).

That gives four anchor surfaces that should be one consistent risk-neutral
density but are not:

| Surface | Vol DNA | Participant base |
|---|---|---|
| IBIT options (NYSE Arca / CBOE) | TradFi: long-dated, call-skewed, RIA hedging flow | US institutions, RIAs, options-permissioned retail |
| Deribit options | Crypto-native: short-tenor, put-skewed, gamma-driven | Offshore prop, market makers, crypto hedge funds |
| CME options on BTC futures | Hybrid, CME-CF benchmark | US institutions (regulated mandates only) |
| Regulated perps (post-May 2026) | New | TBD — early days |

### Incumbent gap

Why isn't this already fully arbed by Citadel/JS/Jump?

1. **Mandate fragmentation.** A US-regulated 40-Act or pension manager cannot
   hold Deribit positions. A Deribit-native prop shop cannot easily get IBIT
   options permissioning at scale (PB constraints, FINRA, beneficial-ownership
   reporting). The *natural* arber is squeezed between regulatory regimes.
   Citadel Securities and Jane Street have both regimes but are
   capacity-allocated to higher-RoC equities flow — vol books at those firms
   are dominated by SPX/single-name and the crypto silo is still a fraction of
   group capital ([Hedgeweek](https://www.hedgeweek.com/non-bank-trading-firms-surge-as-jane-street-and-citadel-securities-drive-record-114bn-revenue-pool/)).
2. **Pricing-model gap.** IBIT options trade on a Bitcoin-ETF underlying which
   has overnight gaps; Deribit trades 24/7 on a perp-anchored index;
   CME-options expire to the CME CF Bitcoin Reference Rate. **The three
   "spots" are different reference series.** A naive vol-surface comparator
   sees fake arbs; a correct one prices the basis between the references and
   only fades the *residual.* This is a Tsiveriotis-Fernandes-grade modeling
   problem (convert with discrete dividend = funding leg = cash settlement
   rule = different underlying). Many shops have not bothered to build it
   properly because the asset class was, until 2025, too small to warrant
   the desk.
3. **TradFi-options-DNA vs crypto-native DNA mismatch.** Per Falcon X and
   Glassnode commentary, IBIT imports "TradFi options DNA" — longer horizons,
   call-centric, RIA-hedging-flow — while Deribit "sets the near-term rhythm"
   with put-heavy gamma ([FalconX](https://www.falconx.io/newsroom/inside-the-crypto-options-boom-three-significant-shifts-shaping-this-market)). The IV-difference between the
   two venues is regularly 2-5 vol-points on matched tenor/strike — that's
   not noise, that's structural client-base segmentation that closes only
   when a sophisticated middleman intermediates.

### Choke point

Three:

1. **Cross-venue risk-aggregation tech.** A book that simultaneously prices
   IBIT, Deribit, CME options and the four reference rates is non-trivial.
   Whoever builds it first owns the spread.
2. **Funding/borrow on the IBIT leg.** IBIT options PM uses share borrow; if
   you can borrow IBIT cheaply (via a prime broker relationship) and short it
   against a long Deribit call, you collect the implied-vol differential. The
   constraint is the borrow desk relationship, not the model.
3. **24/7 risk management.** Now that CME runs continuously, the
   firm-scale player needs a 24/7 risk engine. Most $50M crypto vol funds run
   business-hours-staffed desks — they shut off Deribit hedges on weekends
   and accept basis blowups. A continuous risk engine is a moat for the
   next 18 months until tooling commoditizes.

### Capture mechanism (what you'd build)

- **A joint SPX-VIX-style arbitrage-free surface across the four reference
  rates** — exactly what the November 2025 arXiv "risk-neutral neural
  operator" paper does for SPX/VIX, ported to BTC ([arXiv](https://arxiv.org/pdf/2511.06451)).
  Calibrate one joint risk-neutral density; price every listed option as a
  projection of that density; trade the residual.
- **Three sub-strategies on the same surface:**
  - **(a) Cross-venue skew arb.** Sell IBIT 25-delta puts when IBIT put-skew
    is rich vs Deribit-implied densities at matched tenor.
  - **(b) Calendar arb.** IBIT longer-dated tenors are systematically
    over-bid by retail / RIA hedging flow; sell IBIT 60-180d, buy
    Deribit 60-180d, hedge delta with CME futures.
  - **(c) Cross-asset BTC↔ETH dispersion.** Deribit owns ETH options
    (>90% share). Short BTC vol (where TradFi entry has compressed IV)
    against long ETH vol (where the surface is still crypto-native and
    less crowded). Honest BTC-ETH dispersion is a real risk-premium —
    not the same as crowded SPX-single-name dispersion that just blew up
    in March 2026.
- **Hedging stack:** CME BTC and ETH futures (regulated, deep), regulated
  perps (post-May-2026), spot ETF for delta-cleaning where short side allows.

### Capacity

This is the load-bearing number; I'll be specific.

- IBIT options OI is $27.6B. A vol-arb book taking 1% of OI on rolling
  positions = ~$275M notional gross. Net vega exposure at typical 30-day
  tenor: ~$15-20M vega — large but absorbable by a $150M-equity book.
- CME options ADV translates to ~$30M premium/day institutional capacity
  for a single book without market impact.
- Deribit Feb 2026 saw $79.5B BTC monthly volume; a $300M book turning
  weekly = $1.2B/month notional, ~1.5% of Deribit's flow. Below impact
  threshold.
- **Practical capacity: $200M equity / $500M gross notional before
  alpha decay.** Could push to $500M equity with multi-tenor diversification
  and the ETH leg.

### Kill case

- **IBIT options institutionalize fully** — Citadel/Optiver build the same
  joint surface and IV-differentials close to noise. Decay clock: 18-30
  months from June 2026.
- **Regulated perps cannibalize Deribit** in a way that collapses the
  surface segmentation. Plausible but slow; offshore demand for >100×
  leverage and exotic strikes won't migrate.
- **CME pulls back the 24/7 launch** (operational issues, low weekend
  volume). Plausible mild risk; doesn't kill the trade, just narrows the
  weekend-gap variant.
- **Regulatory shock**: SEC restricts IBIT borrow availability or
  PM-margining for the ETF options leg.

### Where math/tech is the differentiator

Joint-surface calibration with no-arbitrage constraints across four
reference series and three different settlement conventions is genuinely
hard. The November 2025 neural-operator paper is one approach; classical
SVI/SSVI extended to multiple roots is another. Either way, **this is one
of the few crypto strategies where a small advanced team beats a large
legacy quant shop**, because the large shops have legacy SPX vol-surface
infra that isn't designed for four-reference-rate crypto.

---

## 2. High-yield credit electronification — algorithmic PT pricing & HY/EM long-tail MM

### New flow

- IG corporate bond e-trading: **50% in 2025, up from 46% in Sep 2023**
  ([Tradeweb](https://www.tradeweb.com/newsroom/media-center/insights/commentary/electronic-credit-trading-approaching-inflection-point-in-ig/)). HY at 32%, flat YoY — **the gap is the opportunity.**
- **MarketAxess US-credit portfolio-trading ADV +48% in 2025 to $1.4B;
  +126% YoY in Jan 2026 to $2B/day. PT market share 20.6% in Q4 2025
  vs 16.2% the year before** ([MarketAxess](https://investor.marketaxess.com/news/news-details/2026/MarketAxess-Reports-Fourth-Quarter-and-Full-Year-2025-Financial-Results/default.aspx), [Investing](https://www.investing.com/news/company-news/marketaxess-q4-2025-slides-portfolio-trading-surge-drives-record-annual-revenue-93CH-4491376)).
- **Jane Street Q1 2026: $16.1B revenue, +40% YoY** — and the
  commentary explicitly calls out fixed-income ETFs and EM ETFs as a
  primary driver ([Hedgeweek](https://www.hedgeweek.com/jane-street-outpaces-rivals-with-record-40bn-trading-haul/)). Non-banks now own a $114B/yr
  revenue pool that used to belong to bulge brackets.
- **HY distressed pile: $46.9B in tech alone as of Q1 2026** ([Morningstar](https://www.morningstar.com/markets/private-credit-defaults-accelerating-led-by-distressed-exchanges)) — credit cycle is turning, dispersion is exploding.

### Incumbent gap

Jane Street and Citadel Securities are already eating the IG ETF arb.
But:

- **HY is 32% e-traded, not 50%** — the same arc that took IG from 30%
  to 50% over 2018-2025 will take HY from 32% to 50% over 2026-2030.
  That migration is the inflection.
- The PT growth on MarketAxess is so fast (126% YoY) that **the pricing
  layer is the bottleneck.** SOLVE, Bloomberg IBVAL Front Office, and
  Overbond all launched ML-based intraday HY pricers in 2024-2025 but
  none of them is the obvious winner ([A-Team](https://a-teaminsight.com/blog/solve-launches-ai-powered-pricing-tool-for-corporate-bond-market/), [Institutional Investor](https://www.institutionalinvestor.com/article/2cc01z896579jxju9f08w/portfolio/bloomberg-says-its-using-machine-learning-to-deliver-near-real-time-bond-prices)). A buyside firm that prices PTs 2-3 bps better than
  the dealer panel captures persistent edge.
- **EM corp credit** is the long-tail. Tradeweb runs EM hard- and
  local-currency e-trading but the **liquidity is fragmented across 35+
  countries**. Bulge-bracket EM desks are shrinking. The opportunity is
  to be the e-MM that quotes 500-1000 mid-cap EM corp credits that
  Jane/Citadel don't bother with.

### Choke point

- **The PT-pricing model.** Whoever can price a 200-line HY portfolio
  in 30 seconds with <2bp error to next-day TRACE beats the dealer
  bid.
- **The dealer-axes / RFQ inventory layer.** PT executes through dealer
  panels — having the model AND the dealer relationships is the moat.
- **HY ETF creation-redemption** (HYG, JNK, USHY) — the basket-vs-NAV
  gap during stress is real and has widened in the 2026 credit cycle.

### Capture mechanism

Build a vertically-integrated HY/EM PT pricer + RFQ engine + ETF AP
operation. Three legs:
1. ML pricer on TRACE + dealer-runs + ETF-flow features, calibrated to
   beat IBVAL by 1-2 bps RMS.
2. Sit on MarketAxess + Tradeweb + ICE as a non-dealer MM (the new
   regulatory regime allows this).
3. Use HYG/JNK/USHY (and EMB, EMHY) creation/redemption as the relief
   valve when dispersion creates basket-NAV gaps.

### Capacity

- US HY outstanding: ~$1.4T. PT market $638.9B traded May-23 to
  May-25 ([BondWave](https://bondwave.com/portfolio-trading-pricing-dynamics/)) → ~$25B/month flow.
- A 1% MM share = ~$3B/month notional; at 5bp average capture, that's
  ~$150M/yr revenue. That implies an equity capacity of $200M-$1B
  depending on inventory velocity.

### Kill case

- Jane Street and Citadel decide HY is worth full attention and
  out-spend the model. The credit-DNA gap they have today closes in
  18 months.
- HY default cycle runs hot enough that PT volumes seize up.
- TRACE-tape delays narrow to seconds, removing the information edge.

### Math/tech edge

ML pricing of illiquid HY/EM credits is genuinely a hard supervised-
learning problem with severe label noise (TRACE prints are sparse,
delayed, and have minimum-size biases). A small team with credit-DNA + ML
can outprice legacy dealers by 2-3 bps. **But you need at least one
former HY trader on the team — pure-ML without credit context will
lose to noise.**

---

## 3. T+1/T+2 FX ↔ ETF creation-redemption plumbing arb

### New flow

- US moved to T+1 in May 2024. **FX still settles T+2 for most pairs**;
  CLS cutoff doesn't accommodate same-day FX for T+1 securities
  ([Clearstream](https://www.clearstream.com/clearstream-en/newsroom/250430-4421984)).
- **ETF APs now post 105-110% collateral** to bridge the T+1/T+2 gap
  ("Thursday Effect" — ESMA's term) ([Euroclear](https://www.euroclear.com/newsandinsights/en/Format/Articles/the-challenges-of-t1-for-etfs.html)).
- **EU moves to T+1 on October 11, 2027** — pre-positioning window is
  *now*. SWIFT Institute estimates banks have 80% less time to manage
  cross-border settlement under T+1 ([BNP Paribas](https://securities.cib.bnpparibas/t1-in-europe-whats-next-for-the-eu-the-uk-and-switzerland/)).

### Incumbent gap

This is operational arb, not pure pricing arb. **Citadel and Jane don't
do operational arb at sub-$5M per opportunity well** — they prefer
volume games. But the *funding-cost gap* between APs that solved T+1
plumbing and those that didn't is a real, persistent 5-15 bps spread on
international ETF creation/redemption that the existing AP panel just
swallows as cost.

### Choke point

The collateral/funding stack. Whoever can post collateral cheaper +
hedge FX intra-day cheaper than the bulge-bracket AP wins.

### Capture mechanism

Become an AP on cross-border ETFs (VEU, IEFA, EEM, EMB) and run an
intra-day FX hedge book that exploits the CLS-cutoff dislocation
between US T+1 securities settlement and T+2 FX. Position for EU T+1 in
Oct 2027.

### Capacity

International ETF AUM = ~$1.5T. AP-flow is a fraction; a single AP
operation typically runs $500M-$5B notional per week. Realistic
revenue capture: $30-50M/yr at $100-300M deployed equity. Below #1 and
#2 but durable.

### Kill case

- CLS extends settlement window to T+1 (under discussion; could happen
  by 2027).
- US moves to T+0 (proposed for 2028+).
- FX swap costs spike enough to eat the spread.

### Math/tech edge

Modest. This is mostly an operational + funding cost game. Math edge
is in the FX-hedging-optimization layer (Almgren-Chriss style intraday
hedging schedule that minimizes CLS-cutoff penalty).

---

## 4. Dispersion 2.0 — post-March-2026 single-stock vol vs SPX

### New flow

- Dispersion started 2026 at the 99th percentile (extreme), compressed
  to 12th percentile in the geopolitical shock, now ~58th percentile
  ([Citadel Securities](https://www.citadelsecurities.com/news-and-insights/flows-and-fundamentals/)).
- **JPMorgan dispersion-strategy index −4.9% in March 2026**, one of the
  worst months in a decade ([Resonanz Capital](https://resonanzcapital.com/insights/after-the-correlation-shock-how-march-2026-broke-and-reshaped-a-popular-vol-trade)).
- Retail single-stock options ADV +25% YoY in 2025 → +50% above
  2020-2025 average in 2026 ([Citadel](https://www.citadelsecurities.com/news-and-insights/april-update/)). New supply of single-name vol.

### Incumbent gap

The crowd just blew up in March 2026. **Survivors will re-enter with
better risk models**; new entrants with a fresh risk model (one that
prices the correlation-shock tail correctly) can take the post-blowup
share.

### Choke point

The correlation-tail model. A standard short-correlation book is
crowded. A short-correlation book that systematically hedges the
correlation-tail with a defensible model is rare.

### Capture mechanism

Run dispersion with three tweaks the crowd doesn't: (a) dynamic
correlation-tail hedge using SPX skew, (b) cross-asset
correlation-shock indicators (credit, rates, FX) as kill switches,
(c) capacity-aware position sizing keyed to dispersion-index quantile.

### Capacity

$100M-$500M before crowding eats edge in the post-blowup regime.

### Kill case

Crowd re-aggregates within 6-12 months. Or correlation regime stays
shocky and the tail-hedge cost exceeds the carry.

### Math/tech edge

Correlation-tail modeling is non-trivial. But this is contested
territory (Capstone, Susquehanna, IMC, Optiver) — math edge alone
won't win, you need execution and capital.

---

## 5. Crypto basis at institutional scale — CME spot ETF / regulated perps

Briefly: **gross APR 38% pre-fees Q1 2026 on HL↔Binance equity-perp
funding; realistic net 3-12% on major pairs** ([NeuralArb](https://www.neuralarb.com/2026/04/24/hyperliquid-vs-cexs-perp-arbitrage-after-fees-funding-slippage/)). CFTC
approved regulated US perps in May 2026 — new arb leg.

This is real and $50-200M capacity. **But largely already captured** by
the existing crypto basis funds (Galaxy, Wintermute, Cumberland) and the
HL↔CEX leg is the noisy retail-targeted variant of what these incumbents
already do at scale. Build only if you have an unfair execution edge
(co-located, custom matching engine).

---

## 6. RWA / tokenized treasuries — **BELOW MANDATE**

Total tokenized treasury market: $12.78B in March 2026 ([Yellow.com](https://yellow.com/research/real-world-asset-tokenization-20-billion-record)).
Yield differential BUIDL 3.45% vs USDY 3.55% — 10 bps "access premium"
([NeuralArb](https://www.neuralarb.com/2026/04/01/rwa-arbitrage-tokenized-treasury-price-gaps/)). After gas, mint friction, KYC, you're looking at
20-50bp opportunities with $50-200M capacity each. **Total addressable
arb book ~$20-80M.** Below the $50M+ mandate floor. Skip.

---

## 7. 0DTE pure liquidity provision — **CAPTURED**

0DTE = >50% of SPX volume; ~$80B gross gamma on the tape ([SpotGamma](https://spotgamma.com/gamma-exposure-gex/)).
Retail = 53-54% of flow ([Cboe](https://www.cboe.com/insights/posts/spx-0-dte-options-jump-to-record-62-share-in-august/)). **The MM layer is dominated by
Citadel, Susquehanna, Optiver, IMC, Jane.** Net liquidity-provision
edge for a new entrant: near zero on the central strikes. The
*spillover* trades (VIX1D, term structure pinning, single-stock
gamma sympathy) are interesting but each is sub-$50M capacity.

---

## Why no "obvious $1B" pick

The honest answer: **the $1B+ capacity slots are all already owned by
the firms that built the moat ten years ago.** Citadel Securities did
$12.2B in 2025; Jane Street did $40B and is on pace for >$60B in 2026.
They own SPX 0DTE MM, treasury ETF arb, IG credit ETF arb, equity
single-name MM, and increasingly the IBIT options book.

The places left for a math-sophisticated entrant to deploy $100-500M
are:

1. **Where regulatory mandate fragmentation forces incumbent retreat**
   (= crypto vol-surface, IBIT/Deribit/CME).
2. **Where the asset class is undergoing electronification right now**
   so the incumbent's existing tech moat hasn't ported yet
   (= HY/EM credit PT pricing).
3. **Where operational complexity dominates and incumbents prefer
   pure-pricing games** (= T+1/T+2 FX-ETF plumbing).

#1 is the cleanest. It is also the only one of the three where math
and tech are the primary differentiator, not credit-DNA / operations
relationships. **That is the pick.**

---

## Redirect from V1

V1 NEXT_SHIFT.md ranked Polymarket↔Kalshi cross-venue routing and
AI-agent flow classification as #1 and #2. Both remain valid for a
small-capacity sleeve ($50K-$2M deployed) but:

- **Polymarket↔Kalshi** total monthly volume ~$28B (May 2026), of which
  the cross-venue gap is single-cent-wide on most contracts. Realistic
  capacity: $2-10M before the gap closes. Below the V2 mandate floor.
- **AI-agent flow classification** is real but the *capturable revenue*
  is small-cap (Robinhood Agentic still <100K accounts at end of Q2
  2026 per the implicit signal in the announcement; even at 1M agent
  accounts the trading revenue arb is order-of-magnitude $20-50M/yr
  fragmented across many counterparties).

Both remain on the small-cap board. Neither qualifies as a firm-scale
($50M+) deployment vehicle. The user already runs the funding-arb
strategy at $50M; V2 needs candidates that *match or exceed* that
capacity, not just yield faster turnover.

---

*Refs cited inline. Date-stamped June 8, 2026.*
