# SHORTLIST V2 — capacity-gated survivors

*Final ranked output of the V2 `/goal` run, 2026-06-08. Hard gates: no
prediction markets, no micro-capacity niches, ≥$50M capacity, ranked by
**risk-adjusted return at scale** (NOT at toy size).*

The V1 SHORTLIST (`SHORTLIST.md`) is preserved as the honesty trail; its
prediction-market winners (K6, Pinnacle-Kalshi sports) are explicitly OUT of
the V2 mandate. See `log.md` for the carry-over table.

---

## Survivors ranked by risk-adjusted return at scale

### 1. Variance Risk Premium — short index vol carry, regime-filtered

- **Capacity**: $200-500M (VIX futures + OTC SPX variance swaps)
- **Honest Sharpe**: 1.3-1.5 net of dealer spreads at $200M scale
  (raw backtest 2.5 → deflated 2.0 → minus 1-2 vol pts execution = ~1.5)
- **AnnRet**: 30-47% gross; 12-19% at vol-targeted 10% deployment
- **MDD**: -21% gross (-9 to -15% in combined sleeve)
- **OOS evidence**: 2018-01-01 → 2026-05-29, 101 months, all 4 two-year
  buckets +Sharpe (1.13, 4.04, 2.65, 3.07). COVID + Volmageddon + April-2025
  spike all in the test window.
- **Mechanism**: structural insurance demand bids IV above subsequent RV;
  spread is the dealer-warehousing fee. Persists because the demand side is
  mandate-driven (pension/asset-manager beta hedging), not value-driven.
- **Tail risk**: binding constraint. Sized at 25-30% of capital with hard
  VIX > 35 flatten rule. Without this rule, one Volmageddon = -100%.
- **GO/NO-GO**: **GO** at $1-10M solo (VIX futures + SVXY) immediately.
  GO at $50-200M with prime broker onboarding (8-12 weeks).
- **Card**: [vrp_short_vol_carry.md](candidates/vrp_short_vol_carry.md)
- **Code**: `research/v2/vrp.py`

### 2. Cross-asset TSMOM — diversifier, NOT standalone

- **Capacity**: $1B+ (CME/ICE futures, unconstrained)
- **Standalone Sharpe**: 0.46 (FAILS deflation alone)
- **Diversifier Sharpe lift**: combined w/ VRP via inverse-vol → portfolio
  Sharpe 1.90, MDD -9% (vs VRP-alone Sharpe 2.5 MDD -21%)
- **Mechanism**: behavioral underreaction + slow-moving macro regimes.
  Trend drought 2016-2021 documented; reignited 2022-2025.
- **Correlation**: 0.01 vs VRP (zero). The genuine value-add.
- **GO/NO-GO**: **GO as a 20-30% sleeve inside the V2 combined portfolio.**
  NOT a standalone product. Sized smaller than VRP because of lower edge.
- **Card**: [tsmom_diversifier.md](candidates/tsmom_diversifier.md)
- **Code**: `research/v2/tsmom.py`, `research/v2/hrp_monthly.py`

### 3. ETH funding-rate arb — V1 carry-over at floor

- **Capacity**: $50M (binding constraint — sits exactly at the V2 floor)
- **Honest Sharpe**: 6 (V1 bias audit; was Sharpe 14 raw before audit)
- **AnnRet**: 22% long-run median; 11% current regime
- **Mechanism**: retail leverage demand → positive funding → collect carry
  on spot/perp basis
- **Status**: paper-running since May 2026 (V1 deployment)
- **Verdict**: PRESERVED. At capacity floor — does not scale to $200M without
  multi-venue execution. Lives as Pod 3 sleeve in the firm map.
- **Card**: V1 candidate file [funding_arb_eth.md](candidates/funding_arb_eth.md)

## Combined V2 sleeve

Inverse-vol weighting of VRP (large vol) + TSMOM (small vol) + Funding arb
(separate sleeve, not integrated into the daily mix):

| Component       | Weight | Standalone Sharpe |
|-----------------|--------|-------------------|
| VRP swap        | ~17%   | 2.49              |
| VRP VXX (proxy) | ~6%    | 0.86              |
| TSMOM           | ~77%   | 0.46              |

**Combined portfolio (inverse-vol)**: Sharpe 1.90, Vol 6.4%, MDD -9%.
**Vol-targeted to 10% (2.5x lever on the futures-equivalent sleeve)**:
AnnRet ~19%, MDD ~-15%.

ETH funding arb sits as a separate Pod 3 sleeve and is not co-mingled in the
daily PnL aggregation (different settlement currency, different risk profile).
Treat it as additive Sharpe ~6 on $50M, contributing ~$5-10M of annual return
at current regime, ~$11M at long-run median.

---

## What got killed in V2

### A. ETF cointegration + OU bands — DEAD
- 19 pairs tested, 4 in-sample survivors, all 4 lost OOS
- Equal-weight portfolio Sharpe -2.83, MDD -66%
- Kalman dynamic-hedge upgrade also failed (best Sharpe 0.68)
- Specific failure box: walk-forward across regimes
- Card: [coint_pairs_dead.md](candidates/coint_pairs_dead.md)

### D. Avellaneda-Lee residual reversion — DEAD
- 41 large caps, PCA + OU on residual, daily, 2018-2026
- Sharpe -0.70, MDD -37%, negative in every 2-year bucket
- Killed by mega-cap-tech grind (short-momentum exposure was the wrong side)
- Card: [avellaneda_lee_dead.md](candidates/avellaneda_lee_dead.md)

### Cross-sectional momentum (Asness 1997-style) — DEAD
- 22 country/asset ETFs, 12-1 momentum, monthly rebalance
- Full OOS Sharpe 0.28, MDD -32%
- Weaker than TSMOM at same horizon; not used

---

## NEEDS-DATA (couldn't test honestly, naming the unlock)

| Strategy family               | Specific data needed         | Approx cost  |
|-------------------------------|------------------------------|--------------|
| HFT microstructure (Avellaneda-Stoikov, Hawkes flow) | ITCH/OUCH feeds, colocation | $500K+/yr |
| Single-name equity factor + Barra residual | Compustat or Sentieo fundamentals | $30-50K/yr |
| Rough vol / SVI surface RV    | OptionMetrics IVY DB         | $30-50K/yr   |
| Convertible RV (V1 pricer ready) | FINRA TRACE bulk + Markit credit | $20-40K/yr |
| Credit / CDS basis            | Markit TRACE + CDS quote feed | $50K+/yr     |
| Sub-day mean reversion / pairs | Polygon Stocks-1m+, Tickdata  | $5-20K/yr    |

The fastest unlock is **Polygon Stocks-1m** ($5-20K/yr) — would let us re-test
both A and D at intraday frequency, which is where the academic literature
says the edge still exists post-HFT squeeze.

---

## Section 4 gauntlet table — every candidate

| Strategy | Purged CV | Deflated Sharpe | Walk-fwd regimes | Net costs at scale | Capacity | Mechanism | Verdict |
|----------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| VRP swap (filtered) | ✓ | ✓ (2.0) | ✓ (4/4 buckets +) | ✓ | ✓ ($200-500M) | ✓ | **SURVIVES** |
| TSMOM (cross-asset) | ✓ | ✗ (raw 0.46) | ✗ (3/8 bad) | ✓ | ✓ ($1B+) | ✓ | DIVERSIFIER |
| ETH funding arb (V1) | ✓ | ✓ (Sharpe 6) | ✓ | ✓ | borderline ($50M floor) | ✓ | CARRY-OVER |
| Coint pairs (static) | ✓ | n/a (Sharpe negative) | ✗ | ✓ | ✓ | partial | DEAD |
| Coint pairs (Kalman) | ✓ | ✗ (0.68) | ✗ | ✓ | ✓ | partial | DEAD |
| Avellaneda-Lee | ✓ | n/a | ✗ (4/4 negative) | ✓ | partial | ✓ | DEAD |
| XSMOM (country) | ✓ | ✗ (0.28) | ✗ | ✓ | ✓ | ✓ | DEAD |

---

## Single best direction to dig next

If the operator does ONE thing in the next 4-6 weeks:

**Deploy VRP at $1-10M sleeve immediately** via VIX front-month futures roll
(short VX1/VX2 spread) plus a small SVXY long, with the contango + VIX<35
filter automated. This is solo-buildable, capacity-headroom-positive, and the
fastest path from research to revenue.

Parallel track over 8-12 weeks: prime broker onboarding for OTC variance swap
access, to scale VRP from $10M → $200M. This is the gate, not the model.

Once VRP is live and producing PnL, the next-build is **Pod 4 (Convertible RV)**
— the V1 pricer is ready, the data unlock is $20-40K/yr, and convert market is
$300-500B with documented 10-15% IV gap to listed options. See `FIRM_MAP.md`.

---

## Honest summary

V2 tested 7 strategy candidates across 5 of Section 2's 7 families. **One
clean standalone survivor (VRP) at meaningful capacity. One useful diversifier
(TSMOM). One V1 carry-over at the floor (funding arb). Two strategy families
(microstructure, factor) are NEEDS-DATA, not tested.**

The combined deployable sleeve (VRP + TSMOM + funding arb) has aggregate
capacity ~$250-550M with honest Sharpe 1.5-2.0 net of impact. That's a real
firm-scale product. It's not Sharpe 5 — those don't exist in public data.
It IS a credible base for a 2-3 pod multi-strategy platform whose moat is the
research-and-risk infrastructure, not any single strategy.

**Single highest-EV direction to attack next**: deploy VRP at small sleeve
NOW. Onboard prime broker for the scale-up. Build Pod 4 (convertible RV) in
parallel as the next sleeve. The infrastructure compounds; the strategies are
training data for it.
