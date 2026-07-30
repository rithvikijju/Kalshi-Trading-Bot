# Cross-asset TSMOM — diversifier, not standalone

**Status**: BORDERLINE (Sharpe 0.46 standalone). Survives the gauntlet ONLY as
an uncorrelated diversifier alongside the VRP sleeve.

## Thesis

Moskowitz-Ooi-Pedersen (2012) showed that the sign of the trailing 12-month
return is a positive predictor of the next month's return across nearly every
major asset class. Mechanism: behavioral underreaction to news, capital flow
inertia, and slow-moving central-bank/macro regimes. Hurst-Ooi-Pedersen (2017)
extended the result back to 1880 and confirmed the same Sharpe ~0.7 in true OOS.

The strategy lost money 2016-2021 (the "trend drought") but reignited in
2022-2025 driven by the rates / commodities regime shift.

## What survives, brutally

OOS 2010-2025, daily, 17-asset ETF universe, monthly rebalance, vol-target each
leg to 10%:

| Period      | Sharpe | AnnRet |
|-------------|--------|--------|
| 2010-2015   | +0.73 to +1.42 | 2-4% |
| 2016-2021   | -0.3 to +0.0   | flat/loss |
| 2022-2025   | +0.70 to +1.13 | 3-5% |
| **Full**    | **+0.46**  | **+1.9%** |

The trend drought was REAL — it's not a tuning artifact. The strategy genuinely
stopped working in the QE-suppressed regime when correlations went to one and
nothing trended. It came back when central banks unwound and rates trended for
24 straight months.

## Why include it at all

Two reasons:

1. **Zero correlation to VRP** (corr +0.01 monthly, ρ=-0.03 vs VXX). True
   diversifier — combining VRP (Sharpe ~2) + TSMOM (Sharpe 0.5) in
   inverse-vol mix gives Sharpe ~1.9 with -9% MDD. The MDD reduction is the
   product.

2. **Capacity is unlimited.** All instruments are CME/ICE deep futures. A $1B
   sleeve doesn't move the underlying. Crucially, in a stress scenario where
   VRP loses (vol spike), TSMOM typically wins (the spike triggers a trend
   regime — long bonds, short equities, the spike-correlated trades).

## Gauntlet result

| Box                          | Pass | Notes |
|------------------------------|:----:|-------|
| Purged CV                    |  ✓   | Daily rebalance, monthly signal lag |
| Deflated Sharpe              |  ✗   | Raw 0.46 → deflated ~0.2. Standalone doesn't clear. |
| Walk-forward regimes         |  Partial | 8 sub-periods, 5 positive, 3 negative. Drought is well-documented. |
| Net of costs                 |  ✓   | 5bp r/t included. Turnover ~10%/month. |
| Capacity                     |  ✓   | $1B+ unconstrained |
| Mechanism                    |  ✓   | Behavioral underreaction is well-evidenced |

**Standalone: FAILS deflated-Sharpe. As diversifier: PASSES.**

## What would make TSMOM standalone

Adding intra-asset cross-section (sector momentum within equities, country
momentum within EM, curve-position momentum within rates) — the Asness "MOM
everywhere" recipe. I tested cross-sectional momentum at the country/asset
level (`xsmom.py`) and got Sharpe 0.28, also too weak standalone.

Better: a higher-frequency trend component (weekly or daily signal with vol-
adjusted entry) combined with the monthly version. Skip for now; the diversifier
benefit is captured in the V2 sleeve.

## GO / NO-GO

**GO as a 20-30% allocation in the V2 combined sleeve**, not as a standalone
product. Pair with VRP. The math is in `research/v2/hrp_monthly.py`.
