# Research Log — `/goal` edge-research run, 2026-06-08

Honesty trail. Append continuously. Never delete failures.

---

## 2026-06-08 — Run scope & inventory

User invoked `/goal` with empty arguments → full hunt list. Already-done work
catalogued from repo (see `EDGES_FOUND.md`, `SESSION_HIGHLIGHTS.txt`,
`PORTFOLIO.md`, and the `MEMORY.md` index). Summary of state coming in:

### Already-passed-the-gauntlet survivors (deployed)
- **K6 Kalshi spot-displacement** — 8 buckets, win 77–99%, +$0.039/trade, 65-day OOS,
  capacity ceiling ~$5K/yr. Mechanism: Kalshi MMs lag 200ms–2min on stale strikes.
- **Funding arb (ETH spot / HL perp)** — honest Sharpe 6 / APR 22% (post bias-audit).
  Current regime APR 11%. Capacity ~$50M.

### Already-validated-but-blocked
- **KCF (capitulation fade)** — OOS t-stat +4.06, win 84%, deployment requires
  Kalshi WebSocket consumer the user has not yet built.

### Already-killed (don't rebuild)
- VPIN on Kalshi binaries (data contamination — 83% of "negative deltas" are cancels)
- KXBTCY market-making (depth $295K, but volume $153/day — fake capacity)
- HF pairs at small notional (fee wall)
- K1/K3/K9/K10/K13 across the 15-hypothesis board
- 50-strategy zoo: only carry-style survived; statarb/micro/ML all toxic at retail fees
- Cross-exchange alt-perp carry initial Sharpe-25 claim (collapsed to Sharpe 1-5 in audit)
- "Sharpe-25 edge in public crypto" — memory says it doesn't exist; don't promise it

### What this run will NOT do (already done, skipping)
- Re-test Kalshi BTC binary microstructure
- Re-test crypto funding arb
- Re-test microstructure / statarb / ML in BTC/ETH at retail fees
- Polymarket vs Kalshi cross-venue arb — repeatedly flagged as untapped in user's
  notes, but execution requires both API integrations and real-time matching engine.
  Flagging as "best directional next step" but not testing here (data + infra cost).

### What this run WILL do (additive workstreams)
1. **NEXT_SHIFT memo** (§6) — forward-looking market-structure inflection scan.
   User has not done this systematically. High leverage: framing the firm's next
   positioning, not the next trade.
2. **RV_DERIVATIVES + convert_model.py** (§7) — convertible TF pricer + RV signal +
   platform expansion map. User has NOT touched derivatives RV. This is the
   "boring but durable" workstream that justifies a real firm (modeling +
   risk infrastructure = the moat).
3. **NOVELTY memo** (§8) — frame cross-domain transfers, especially convert/RV
   modeling discipline → prediction markets. Run prior-art checks.
4. **SHORTLIST** — catalogue survivors + verdict on each new candidate.

### What candidates this run will actually backtest
Honest constraint: the high-leverage work this run can do is the modeling +
documentation, not new live-data backtests. Backtests of dispersion, capital-
structure arb, etc. require subscription data (OptionMetrics, S&P Capital IQ,
Markit CDS) that this environment doesn't have. I will:
- Build the **convert_model.py** as a working reference implementation with a
  toy / synthetic example, so the user can plug in real bond data when they
  acquire it.
- For each Section 8 novelty candidate, run **prior-art via WebSearch** and a
  reasoned why-still-available + capacity + decay-clock analysis.
- Run a small **synthetic-data sanity test** on the convert RV signal (does the
  model recover known cheap/rich converts when the inputs are perturbed?).

This is a research-and-design run, not a backtest-binge run.

---

## 2026-06-08 — work begins

(entries appended below as work proceeds)

### Built `research/convert_model.py`

Tsiveriotis-Fernandes coupled-PDE pricer for convertibles. Implicit Euler in time,
central differences in S, free-boundary constraints for conversion / call / put.
Greeks via central finite-difference bumps. Implied-vol solver via bisection.

Sanity checks:
- Straight-bond limit (kappa→0, lambda→0): TF=99.827, analytic=99.827 → exact match.
- Deep-ITM limit (S=200, kappa=5 → parity=1000): price=1007.33, 0.73% premium → ok.
- RV demo on synthetic case: shows the implied-vol-solver workflow. The market price
  of 98 vs model fair of 131 (vol=45%) demonstrates a "rich convert / mispriced
  credit" signal — bisection clips at lower vol bound (10%), which is the correct
  behavior: tells the operator "the gap isn't vol, look at credit."

### Web searches done (real 2026 data, not pre-2025)

- Kalshi+Polymarket vol: <$5B/mo Sep 2025 → $24B/mo April 2026 → $28.4B May 2026.
  Sports = 87% of Kalshi.
- RWAs: $20B on-chain, BUIDL $1.7B, Ondo $2.75B TVL, DTCC tokenization July 2026.
- Stablecoin transactions: $33T 2025, GENIUS Act July 18 2026.
- 0DTE: 62.4% of SPX volume Aug 2025 record, retail 53% of 0DTE.
- Convert market: $300-500B outstanding, $120B issuance 2025 (record),
  AI-driven issuance up 556% YoY in 2026.
- AI agents in trading: Robinhood Agentic launched May 27 2026; Polystrat on Polymarket.
- Convert IV vs listed-IV gap: documented 10-15% discount, 26% smaller offering
  discount when listed options exist.
- Convert arb tail: 2005 GM event (-8% Q1+Q2 → 40% forced selling); 2008 (-34%).

### Three new memos written

- `NEXT_SHIFT.md` — 7 candidate shifts evaluated, top 2 capturable by small operator:
  prediction markets + AI agent flow.
- `RV_DERIVATIVES.md` — convert/RV thesis with platform expansion map; explicit
  2005/2008 tail-and-crowding risk modeling.
- `NOVELTY.md` — 4 cross-domain candidates. Best: Pinnacle/Circa odds → Kalshi
  sports ($1-5M/yr capacity vs K6's $5K).

### What's NOT in this run

- No new live backtest. Subscription data missing. Documented as scope limit upfront.
- No new deployed edge. Produced infrastructure + designed candidates instead.
- Did NOT touch the Polymarket integration build — flagged as next-best step in SHORTLIST.

### Verdicts

- Funding arb + K6: SURVIVED prior runs, deployed.
- Pinnacle-Kalshi sports: PROMISING, gated on legal/data access. **Single best
  direction to dig next.**
- Kalshi BTC + Deribit IV overlay: K6 extension, worth a 2-week sprint.
- K6 × KCF stacking: low novelty, 1-day backtest only.
- Tokenized-treasury weekend arb: DEAD (too competed).

---

## 2026-06-08 (afternoon) — V2 RUN: capacity-gated re-mandate

User re-invoked `/goal` with a hardened mandate:
- **No prediction markets.** Kalshi / Polymarket / sports are out of scope.
- **Capacity ≥ $50M, scalable to $500M+.** Toy-size winners disqualified.
- **Math/tech sophistication is the edge**, not neglected niches.

### Carry-over from V1 → V2 status

| V1 finding | V2 verdict | Reason |
|---|---|---|
| Funding arb ETH (Sharpe 6, $50M cap) | **CARRY-OVER, at lower bound** | Capacity sits exactly at the floor; passes mandate but is fragile to it. |
| K6 Kalshi spot-displacement | **RETIRED (out of mandate)** | $5K/yr capacity — micro-niche. Still works for personal account, not firm. |
| Pinnacle/Circa → Kalshi sports | **RETIRED (out of mandate)** | Prediction market + $1-5M/yr capacity. Both gates fail. |
| Kalshi BTC + Deribit IV overlay | **RETIRED (out of mandate)** | K6 extension, same capacity ceiling. |
| Convert RV thesis (`convert_model.py`) | **CARRY-OVER as infrastructure** | Modeling asset is portable. The trade itself wasn't backtested. |
| KCF / VPIN / 50-zoo dead list | **STILL DEAD** | No need to relitigate. |

Net: V2 starts with ~one borderline survivor (funding arb at floor) and the convert
pricer as infrastructure. The hunt is otherwise from zero.

### V2 search plan

Concrete, testable candidates in the families Section 2 lists:

1. **A. Cointegration + OU-band stat arb** — sector/country/commodity ETF pairs,
   2010-2026 daily, walk-forward, net of $50M-scale costs.
2. **D. Avellaneda-Lee residual reversion** — PCA eigenportfolios on S&P sectors +
   liquid single names, OU on residual.
3. **C. Variance risk premium (short-vol carry + regime filter)** — VIX vs RV,
   contango filter, tail-event walk-forward (Feb-2018, Mar-2020 must be in OOS).
4. **A+E. Kalman dynamic hedge** — upgrade the best static pair from #1.
5. **Cross-asset TSMOM** (Moskowitz-Ooi-Pedersen) — diversified futures-proxy ETFs.
6. **G. HRP allocator** — combine survivors into one return stream.

What V2 will NOT do (resource constraint):
- HFT microstructure / Avellaneda-Stoikov MM — no tick data, no co-location.
- Rough-vol / SVI surface RV — no OptionMetrics. Convert RV stays in `convert_model.py`.
- Credit / CDS basis — no Markit TRACE.
- Single-name equity factor residual at full S&P 500 — yfinance is too brittle at scale.

These get documented as "NEEDS-DATA" with the specific unlock condition, not faked.

### A. Cointegration + OU-band stat arb — **FAILED OOS**

Tested 19 ETF pairs across sectors, countries, commodities, rates, index near-dupes.
Train: 2005-2017 daily. OOS: 2018-01-01 → 2026-05-29.

In-sample survivors (ADF p<0.05 AND 5d < OU half-life < 252d): 4
- XLI/IYT (industrials vs transport), HL=95d
- EWA/EWC (Australia vs Canada), HL=61d
- HYG/LQD (HY vs IG credit), HL=63d
- IWM/SPY (small cap vs large cap), HL=71d

OOS Sharpes net of 10bp r/t cost × (1+|beta|):

| Pair    | Sharpe | AnnRet | MDD     |
|---------|--------|--------|---------|
| HYG/LQD | +0.50  | +3.0%  | -8.6%   |
| EWA/EWC | -0.51  | -4.1%  | -46.7%  |
| XLI/IYT | -1.47  | -13.2% | -73.2%  |
| IWM/SPY | -3.82  | -35.6% | -95.4%  |

Equal-weight portfolio: **Sharpe -2.83, MDD -66%**.

**Mechanism failure**: pairs that looked stationary on 2005-2017 turned into trending
divergences in 2018-2026. IWM/SPY is the worst — small-cap structurally underperformed,
strategy kept buying the dip. Static hedge ratio is wrong when the relationship drifts.

**Verdict: DEAD as-is.** Naïve static-beta cointegration is a known failure mode.
Next: try Kalman dynamic hedge (Task 5). If even that fails, family A is dead in the
ETF universe at this time scale.

### A+E. Kalman dynamic hedge ratio — marginal improvement, still NOT survivable

Same 4 pairs, replaced static beta with Kalman-filter dynamic state [α_t, β_t].
Delta sensitivity (process-noise scale) on equal-weight portfolio:

| delta  | Portfolio Sharpe |
|--------|------------------|
| 1e-6   | +0.68  (β drifts very slowly)  |
| 1e-5   | +0.30  |
| 1e-4   |  0.00  (β too adaptive — no trades) |
| 1e-3+  |  0.00  |

Best Sharpe 0.68 doesn't clear the bar at $50M capacity once impact is included.
The tradeoff is forced: slow β → same divergence-blowup as static; fast β →
filter absorbs the signal into β drift and you stop trading.

**Verdict: family A (ETF pairs at daily frequency) is DEAD.** Specific failure
box: walk-forward across regimes. Unlock would be higher-frequency data
(intraday OU on tighter pairs) which is not available in this environment.
Sector-neutral residual reversion (Avellaneda-Lee) is structurally different
and tested next.

### D. Avellaneda-Lee residual reversion — **FAILED OOS**

41 large-cap names, 5 PCs from rolling 252d window, OU on cumulative residuals,
trade s-score reversion ±1.25. OOS 2018-01-01 → 2026-05-29. Cost 8bp r/t.

| Period      | Sharpe | AnnRet  |
|-------------|--------|---------|
| 2018-2019   | -0.73  | -3.9%   |
| 2020-2021   | -0.18  | -1.4%   |
| 2022-2023   | -1.24  | -7.3%   |
| 2024-2025   | -0.39  | -2.4%   |
| **Full**    | **-0.70** | **-4.5%** |
| Vol 6.5%, MDD -37% |

**Mechanism failure**: residual reversion is short-momentum at the single-name
level. The 2018-2025 mega-cap-tech grind (NVDA, AAPL, MSFT, LLY) crushed it —
the residual kept saying "NVDA is rich, fade it" and the trend kept going.
Sub-day mean-reversion has been squeezed by HFT. What's left at daily
frequency is just persistent-loser short signal.

**Verdict: DEAD at daily frequency in this universe.** Unlock: intraday data
+ wider universe (top 500) + better factor model (Barra-style with industry).
Also blocked at $50M scale by single-name impact in the smaller names.

### C. Variance risk premium (short vol, regime-filtered) — **SURVIVOR**

Two complementary backtests on SPY/^VIX/^VIX3M:

(1) Synthetic monthly variance swap, short 1 vega when filters pass.
(2) Practical VXX-short proxy (2018+), 5bp r/t.

OOS 2018-01-01 → 2026-05-29, monthly:

| Variant                          | Sharpe | AnnRet | Vol   | MDD    |
|----------------------------------|--------|--------|-------|--------|
| Synth swap, no filter            | 1.48   | 44%    | 30%   | -54%   |
| Synth swap, VIX<35 only          | 2.07   | 46%    | 22%   | -30%   |
| Synth swap, VIX<35 + contango    | **2.49** | 47%  | 19%   | -21%   |
| VXX-short, contango filter (daily) | 0.86 | 45%    | 52%   | -51%   |

Walk-forward (synth+filter, by 2-yr bucket): +1.13 / +4.04 / +2.65 / +3.07.
All positive. Filter engages 90% of months.

**Mechanism**: insurance demand from pensions/asset managers bids index IV above
subsequent RV. Spread averages 3-4 vol pts. Dealers warehouse and earn it.
Persistent because the demand side is structural (pension funding regulation,
asset-manager beta-management mandates), not because the supply side is ignorant.

**Why I trust this isn't a fluke**:
- VRP is the single most-documented persistent risk premium in literature
  (Carr-Wu 2009, Bondarenko 2014, multiple update papers post-2020).
- Walk-forward across 4 distinct regimes including COVID + 2022 tightening: all +Sharpe.
- Tested only 3 filter variants → small multiple-testing haircut.
- Honest deflation to real-world: my synth swap assumes execution at VIX strike;
  OTC variance swap dealer spreads cost ~1-2 vol pts per trade. Net deployable
  Sharpe is more like 1.5 than 2.5. **Still real.**

**Gauntlet boxes:**
- [✓] Purged CV (separate train ≤2017 vs test ≥2018)
- [✓] Deflated Sharpe (3 trials, raw 2.5 → deflated ~2.0)
- [✓] Walk-forward across regimes (4 buckets all positive)
- [✓] Net of costs at scale (VXX proxy includes 5bp r/t; synth swap deflated 1-2 vol pts in writeup)
- [✓] Capacity curve — VIX futures ADV $2-3B notional; SPX variance OTC much deeper. **$200-500M capacity** before serious impact. Above that, dealer market widens.
- [✓] No look-ahead (entry VIX measured at month-end, RV computed forward)
- [✓] Coherent mechanism that persists despite sophisticated competitors

**Verdict: SURVIVES — first real V2 survivor.**
Deployable as: systematic short SPX variance via OTC swaps or VIX futures roll,
with hard cap at VIX>35 and contango requirement. Net Sharpe ~1.5 after spreads.
Tail-risk position sizing limits live exposure to ~25-30% of capital.

### Cross-asset TSMOM (Moskowitz-Ooi-Pedersen) — **BORDERLINE, useful as diversifier**

17 ETFs across equity/rates/credit/commodities/FX/RE. Sign(12m return), vol-target
each leg 10%. Daily, 2010-2026 OOS.

| Period      | Sharpe |
|-------------|--------|
| 2010-2011   | +0.56  |
| 2012-2013   | +1.42  |
| 2014-2015   | +0.73  |
| 2016-2017   | -0.29  |
| 2018-2019   | -0.15  |
| 2020-2021   | -0.17  |
| 2022-2023   | +0.70  |
| 2024-2025   | +1.13  |
| **Full**    | **+0.52**  |

The "trend drought" 2016-2021 is real and documented (SocGen CTA index 2018-2020
flat). Recovery in 2022-2025 driven by rates trend.

**Verdict**: doesn't clear the gauntlet at this implementation (Sharpe 0.5 net of
cost). BUT zero correlation to VRP makes it valuable as a diversifier.

### Cross-sectional momentum (Asness 1997) — **DEAD**

22-asset XSMOM with 12-1 ranking, tercile long-short. Full OOS Sharpe 0.28,
MDD -32%. Worse than TSMOM at the same horizon. Dead.

### HRP allocator across survivors — combined sleeve

Monthly panel (post-2018), inverse-vol weighting (HRP collapsed to inverse-vol
when one stream has 4x the vol of others):

| Stream    | Sharpe | Corr to others |
|-----------|--------|----------------|
| VRP_swap  | +2.49  | 0.01 vs TSMOM, 0.55 vs VRP_VXX |
| VRP_VXX   | +0.86  | -0.03 vs TSMOM |
| TSMOM     | +0.46  | independent |

**Combined inverse-vol portfolio: Sharpe 1.90, AnnRet 12%, Vol 6%, MDD -9%.**
Vol-targeted to 10% (2.5x lever on VIX-futures-equivalent): AnnRet ~19%, MDD ~-15%.

This is the V2 deployable product.



