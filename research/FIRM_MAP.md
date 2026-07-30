# FIRM_MAP — V2 platform & pod-expansion plan

*From `/goal` V2 run, 2026-06-08. The platform method (Griffin-style) applied to
the strategies that survived the V2 gauntlet.*

## The platform method, restated for this operator

1. **Niche where modeling/tech is the edge** — V2 found two: VRP (variance risk
   premium short-vol carry) and crypto funding-rate arb (carried over from V1).
2. **Build the portable research + risk + execution infrastructure** — this is the
   moat. NOT the individual strategies.
3. **Add specialists in adjacent liquid markets on the same plumbing.**
4. **Allocate capital and control aggregate leverage / drawdown centrally.**
5. **Add uncorrelated strategies that smooth the aggregate return stream.**

The wrong reading of this map is "build five strategies." The right reading is:
the strategies are training data for the infrastructure. The infrastructure is
the firm. The strategies decay; the infrastructure compounds.

## V2 starting position

What this operator has, today (June 2026):

| Asset                                                | Capacity        | Status |
|------------------------------------------------------|-----------------|--------|
| ETH funding-rate arb (deployed, V1)                  | $50M            | Live   |
| VRP short-vol carry (V2 winner)                      | $200-500M       | Not yet deployed |
| TSMOM diversifier (V2 borderline)                    | $1B+            | Sleeve only |
| Convertible TF pricer (`convert_model.py`)           | infra           | Reference impl |
| Funding-arb risk auditing methodology                | infra           | Documented |
| HRP / inverse-vol allocator (`hrp_monthly.py`)       | infra           | Working code |

The strategies above represent ~$250-550M of combined capacity. The
infrastructure assets (the pricer, the audit methodology, the allocator) are
portable across asset classes and across future strategy candidates.

## Pod 1 — Volatility & derivatives RV (the build-first pod)

**Why first**: VRP is the highest-Sharpe survivor (~1.5 honest), the deepest
market (SPX variance + VIX futures), and the most documented risk premium. It
is also the place where math sophistication is most directly the edge.

**Components**:
- VRP systematic short-vol with regime filters
- Tail hedge overlay (long deep-OTM put or vol-of-vol component) to bound MDD
- Once running: dispersion (index vs single-name vol), term-structure RV,
  rough-vol model calibration for shorter-dated structures

**Infrastructure required**:
- Vol surface fitting (SVI, Heston, rough Bergomi) — not yet built
- Real-time IV data feed (Deribit for crypto, OptionMetrics or DDH for SPX)
- OTC variance swap broker relationship (prime broker, $50M minimum)

**Headcount**: 1 PM + 1 vol quant + 1 execution + risk shared.

**Time to live**: 8-12 weeks (broker onboarding is the gate, not the model).

## Pod 2 — Cross-asset systematic (the diversifier pod)

**Why second**: TSMOM is the cheap-to-build complement to Pod 1. Sharpe alone
is unimpressive (0.5), but the zero-correlation to Pod 1 means the combined
sleeve Sharpe is materially higher than either alone. AQR / Man-AHL / Winton
all run this exact pod; it's a solved problem from an implementation standpoint.

**Components**:
- TSMOM across 17 ETF asset classes (vol-targeted, monthly)
- Risk-parity / equal-vol weighting overlay
- Cross-sectional momentum (XSMOM) at country level — currently dead at this
  universe but worth re-testing at higher frequency

**Infrastructure required**:
- Liquid futures execution (CME, ICE, EUREX)
- Daily vol targeting at the sleeve level

**Headcount**: 1 PM + 0.5 quant. Pod 2 is the lowest-headcount pod because the
strategies are mostly mechanical.

**Time to live**: 4-6 weeks. Half the time of Pod 1.

## Pod 3 — Crypto carry & basis (the existing pod)

**Why preserved**: This is the V1 winner (ETH funding arb), already deployed.
Capacity is exactly at the $50M floor — fragile to the new mandate but real.

**Components**:
- ETH spot/perp funding capture
- Cross-exchange basis (Binance vs HL vs OKX) — tested as audit-failing
  Sharpe 1-5 in V1, redeploy at honest sizing
- ETH/BTC futures basis (CME vs Deribit vs HL)

**Infrastructure required**: API connectivity to Binance, HL, Deribit, OKX,
CME. Hot-wallet management. Already operational.

**Headcount**: 0.5 PM (already running). No expansion needed.

**Capacity ceiling**: $50-100M before slippage degrades materially. Pod stays
as a sleeve, doesn't grow into a center of mass.

## Pod 4 (future) — Convertible RV + capital-structure arb

**Why next-build**: V1 produced a working TF pricer (`convert_model.py`). The
data unlock (FINRA TRACE, Markit credit curves, OptionMetrics IV surface) is
$20-50K/year — affordable once Pods 1-3 are revenue-generating.

**Components**:
- Convert IV vs listed IV gap (documented 10-15% structural)
- Convert vs synthetic put (call + cash) RV
- Credit hedge (CDS or HY ETF short) for delta-neutral convert positioning

**Infrastructure required**: FINRA TRACE bond data, Markit credit curves,
listed options vol surface fitter. Convert pricer already exists.

**Headcount**: 1 PM + 1 fixed-income quant. Higher-skill hire, longer to staff.

**Time to live**: 6-9 months. This is the post-fundraise pod.

## What's intentionally NOT a pod

- **Cointegration / Avellaneda-Lee stat arb** — DEAD at this frequency. The
  HFT shops own the unlock. Not pursuing without $5-10M of infra investment
  in colocation + tick data.
- **HFT market making (Avellaneda-Stoikov)** — needs colocation + ITCH/OUCH
  feeds + dedicated network. $5M+ infra + 12-month build. Not in scope until
  AUM justifies it.
- **Single-name equity factor (Barra residual)** — needs Compustat / Sentieo /
  internal fundamentals data. $30-50K/yr data alone, plus 6-month model build.
- **Prediction-market sleeves** — V1 candidates explicitly out of V2 mandate
  due to capacity. Personal-account-only (K6 remains a private amp, separate
  from firm).

## The infrastructure that IS the moat

In priority order, what to build that compounds:

### Tier 1 — already exists, refine
- **`hrp_monthly.py`** — multi-strategy allocator. Generalize to N strategies,
  with rolling window and stress-scenario overrides.
- **`convert_model.py`** — TF pricer, generalize to LSM Monte Carlo for path-
  dependent embedded options (resets, contingent conversion).
- **`funding_arb_bias_audit.md`** — the auditing methodology (honest backtest
  → Sharpe deflation → realistic-impact accounting) is itself the moat. Make
  it a template applied to every new candidate before deployment.

### Tier 2 — to build over the next 6 months
- **Vol surface fitter** (SVI no-arb + Heston calibration) — required for Pod 1
  and Pod 4. Buy or build in Python; not novel, but required.
- **Real-time risk monitor** — daily MTM of all positions, scenario shocks
  (VIX +20, BTC -50%, rates +200bp), trip-wires on aggregate leverage.
- **Execution router** — abstract broker connectivity so strategies don't care
  whether they're hitting IB, Hyperliquid, or a prime broker desk.

### Tier 3 — to build pre-Pod-4
- **FINRA TRACE ingestion + corp bond pricing** — last-trade + composite quote.
- **Credit curve fitter** — bootstrap from CDS or bond spreads.
- **Convert universe screener** — issuer fundamentals + cap-structure scan.

## Solo-buildable vs needs-raise

**Solo-buildable today** (current operator, current capital):
- Pod 1 (VRP) at $1-10M sleeve via VIX futures and short VXX. No broker
  relationship needed at that size.
- Pod 2 (TSMOM) at $500K-5M sleeve via liquid ETF futures.
- Pod 3 (Crypto carry) at $50-100M, already running.
- Tier 1 infrastructure refinement.
- Tier 2 vol surface fitter (build it as a research artifact, not yet
  production-grade).

**Needs raise** ($10-50M check from family office or seed allocator):
- Pod 1 at $200-500M scale (requires prime broker relationship).
- Tier 2 production-grade risk monitor + execution router.
- Pod 4 build-out (convert RV).
- First quant hire (Pod 1 vol quant).

**Needs institutional raise** ($100M+):
- Tier 3 fixed-income infrastructure.
- HFT-adjacent infrastructure if/when colocation makes sense (post-$200M AUM).
- Hire a full second pod team.

## The fundability narrative

Three points, in this order, when pitching:

1. **Demonstrated honest research discipline.** "We killed cointegration,
   Avellaneda-Lee, XSMOM, and showed the trend drought in TSMOM, before
   deploying anything. Our gauntlet (purged CV, deflated Sharpe, capacity
   curves, mechanism check) caught the V1 funding-arb Sharpe-14 inflation
   before it shipped to LPs." → This is the trust signal.

2. **Two uncorrelated production sleeves.** VRP + funding arb, $250-550M
   combined capacity, Sharpe ~1.5-2.0 combined, MDD bounded by tail-risk rules.
   → This is the product.

3. **Portable infrastructure.** Convert pricer, audit methodology, allocator,
   risk monitor. → This is the firm. Pod 4 (convert RV) is what justifies the
   next $200M of AUM growth without re-tooling.

The mistake to NOT make: pitch any single strategy as the thesis. The thesis
is the infrastructure that makes 5+ strategies survive their natural decay.
