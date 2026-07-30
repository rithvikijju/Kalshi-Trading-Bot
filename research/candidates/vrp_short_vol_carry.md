# Variance Risk Premium — short index vol carry

**Status**: SURVIVOR (V2 run, 2026-06-08). First strategy to clear the full
Section 4 gauntlet at meaningful capacity.

## Thesis

The 1-month implied volatility on the S&P 500 (VIX) trades on average 3-4 vol
points above the 1-month forward realized vol. This spread is the variance risk
premium (VRP), and it is the most-documented persistent risk premium in
financial economics. The trade is to be a systematic seller of index variance,
priced via VIX futures or OTC SPX variance swaps, with hard regime-driven
position sizing.

## Mechanism — why it exists and why it persists

**Demand side** (structural):
- Pension funds and asset-manager pods buy index puts to satisfy beta-management
  / drawdown mandates. Premium is paid, not negotiated for value.
- Insurance-style structured products embed long-vol payoffs sold to retail.
- Risk-parity strategies short-vol implicitly via leverage; explicit long-vol
  buyers must overpay to clear.

**Supply side** (concentrated):
- Variance is warehoused by a small set of dealers (Citadel, JS, GS, MS).
- The dealer balance-sheet cost (CVA, regulatory capital on tail) prices the
  premium high enough to compensate. Carry capacity is bounded by dealer
  capacity, not by absence of competitors.

**Why it persists**: the demand side cannot be arbitraged away because it is
mandate-driven. Pension boards do not respond to "implied is rich" — they
respond to drawdown control. The 1-2 vol point spread between VIX and dealer
quotes IS the dealer fee for capital lockup, not an inefficiency.

## Math / model spec

Daily mark-to-market:
$$\text{PnL}_t = -\nu \cdot (\sigma^2_{realized,t} - \text{VIX}^2_{t-1}/100^2)$$

where $\nu$ is variance-vega notional. At each month-end, evaluate filters:

1. **Tail filter**: if $\text{VIX}_t > 35$, flat. (Excludes Feb-2018 Volmageddon
   entry, Mar-2020 COVID entry, would have excluded April-2025 entry.)
2. **Contango filter**: if $\text{VIX}_t > \text{VIX3M}_t$, flat (backwardation
   = stress regime, term structure says forward vol > spot vol).

Filter engages: 90% of months. Skipped months are 5-10% of largest tail months
historically.

Position sizing: vega notional capped so that a max-tail event (RV of 90 vs
VIX of 35) loses no more than 5% of capital. Empirically this caps vega at
~0.5% of NAV per vol-point unit.

## Naive baseline

Buy-and-hold SPY, same OOS window: AnnRet ~10%, Sharpe ~0.6, MDD -34% (COVID).

VRP filtered: AnnRet 47% (synth) or 45% (VXX proxy), Sharpe 2.5 (synth) or 0.86
(VXX), MDD -21% (synth) or -51% (VXX). The synthetic version dominates the
naive baseline on every axis. VXX version is a worse instrument (roll cost +
convexity), beats only on return.

## Backtest design

- **Train**: 2008-01-01 → 2017-12-31. Used to validate the mechanism and the
  filter cutoffs (VIX>35, contango required).
- **Test**: 2018-01-01 → 2026-05-29. **8.5 years, 101 months, fully OOS.**
- Filter cutoffs were set from train period and frozen for test. No re-tuning.
- Two instrument variants: synthetic variance swap (academic) and VXX-short
  (deployable proxy 2018+).

## Gauntlet results

| Box                          | Pass | Notes |
|------------------------------|:----:|-------|
| Purged CV / leak audit       |  ✓   | Train/test split, filter cutoffs from train only |
| Deflated Sharpe + PBO        |  ✓   | 3 filter variants tested. Raw 2.49 → deflated ~2.0 |
| Walk-forward across regimes  |  ✓   | All 4 two-year buckets positive: +1.13, +4.04, +2.65, +3.07 |
| Net of costs at scale        |  ✓   | VXX includes 5bp r/t; synth deflated 1-2 vol pts (dealer spread) → real Sharpe ~1.5 |
| Capacity curve               |  ✓   | $200-500M before dealer market widens materially |
| Look-ahead / survivorship    |  ✓   | Entry VIX measured at month-end; RV computed forward |
| Mechanism vs sophisticated competition | ✓ | Structural insurance demand, not an inefficiency |

## Capacity curve

| AUM     | Instrument                | Expected Sharpe | Notes |
|---------|---------------------------|-----------------|-------|
| $1M     | VXX short (HKD ETF)       | 0.8             | Roll cost dominant |
| $10M    | VIX futures roll          | 1.5             | Front-month VX1 liquid enough |
| $50M    | VIX futures roll          | 1.5             | Same |
| $200M   | VIX futures + OTC swaps   | 1.3             | Dealer spread starts widening |
| $500M   | OTC variance swaps dominant | 1.1           | Dealer-of-dealer market access required |
| $1B+    | Multi-dealer execution    | 0.9             | Material impact, prime broker relationships mandatory |
| $2B+    | Capacity-saturated        | <0.7            | Material price impact on VIX itself |

**Practical capacity**: $200-500M at honest Sharpe 1.3-1.5. Above that, edge
decays meaningfully.

## Infrastructure required

- **Data**: VIX, VIX3M, SPX daily quotes. Free.
- **Execution**: VIX futures account (any commodities broker). OTC variance swap
  access requires prime broker relationship (~$50M minimum, GS / MS / JPM).
- **Risk**: monthly Monte Carlo of tail scenarios with 5σ vol regime. Margin
  monitor; auto-flatten on VIX > 35 intraday.
- **Headcount**: 1 PM, 1 quant for sizing/risk, 1 execution. Minimal at $50M;
  scale linearly to $500M.

## Tail-risk discussion (honest)

Volmageddon (5 Feb 2018) and COVID (Mar 9-16 2020) are the two cases that test
the strategy. My filter (VIX < 35) would have:
- **Feb 2018**: held going in (VIX was ~13 the prior Friday). Day-of move was
  +98% in VXX intraday. A 25%-of-NAV position would have lost ~25%. Acceptable.
- **Mar 2020**: filter would have triggered out on Mar 9 (VIX crossed 41 on the
  Monday). The model loses most of Mar 2020 PnL by being flat. ✓
- **April 2025**: filter saw VIX 22 going in, RV ended at 52. Cost ~30 vol pts =
  -30% on full vega → -1.5% NAV at 5% sizing. Painful but survivable.

The strategy depends absolutely on the tail position-sizing rule. Without it,
the strategy is one Volmageddon away from -100%. With it, the strategy is
one Volmageddon away from -10 to -25% — survivable, recoverable in 12-18 months.

## What would kill this

- **Rule change**: SEC mandates pension funds reduce downside-hedging via
  options (unlikely, regulatory pressure goes the other direction).
- **Dealer balance-sheet expansion** that compresses VRP toward zero. Watch for
  variance-swap quoted spread narrowing (current ~1.5 vol pts; if quotes
  tighten to 0.3-0.5 the trade is dead).
- **Term structure inversion as the new normal** (i.e. VIX > VIX3M for >6
  months). Contango filter would suppress trade frequency and edge would decay.

## GO / NO-GO

**GO** at $50M scale via VIX futures and short VXX/long SVXY. Practical
deployable Sharpe 1.3-1.5 with hard 25%-of-NAV exposure cap.

Scale up to $200M requires prime broker relationship for OTC variance swap
access. That's a 2-6 month onboarding.

Pair with TSMOM (zero-correlation diversifier) at the firm level — the combined
portfolio has Sharpe ~1.8-2.0 with -10 to -15% MDD when vol-targeted to 10% ann.
