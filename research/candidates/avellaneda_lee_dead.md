# Avellaneda-Lee residual reversion — DEAD at daily frequency

**Status**: KILLED in V2 OOS testing 2018-2026.

## What was tested

41 large-cap S&P names spanning all 11 sectors. Rolling 252d window, PCA to
extract top 5 factors (eigenportfolios), OLS each name's return on factors,
fit OU to cumulative residual (the "s-score"), trade s-score reversion ±1.25.

Sector-neutral by construction (PCs absorb sector beta).

## Result

| Period      | Sharpe | AnnRet  |
|-------------|--------|---------|
| 2018-2019   | -0.73  | -3.9%   |
| 2020-2021   | -0.18  | -1.4%   |
| 2022-2023   | -1.24  | -7.3%   |
| 2024-2025   | -0.39  | -2.4%   |
| **Full**    | **-0.70** | **-4.5%** |

Negative in every 2-year bucket.

## What killed it

Residual reversion is implicitly short single-name momentum. The 2018-2025
mega-cap-tech grind (NVDA +18x, AAPL +5x, MSFT +6x, LLY +12x) presented as
"this name is rich, fade it" residual signals — which lost money continuously
as the trend persisted.

Daily-frequency single-name mean-reversion has been competed away by HFT
shops over the last decade. What's left at this horizon is structural
short-momentum exposure, which is the wrong side of the recent regime.

## What would unlock this

- **Intraday (5-30min) frequency** where mean-reversion still exists post-
  inventory/news-driven dislocation.
- **Wider universe** (Russell 1000) with proper Barra-style industry + style
  factor controls in addition to PCs.
- **News-event meta-labels** — trade reversion only after non-fundamental
  moves (López de Prado triple-barrier with event labeling).

None of these are buildable here without paid data.

## Family D verdict

At daily frequency in the large-cap-eligible universe: DEAD. The natural unlock
is intraday HFT-adjacent, which requires capital and infrastructure outside
this run's scope. NEEDS-DATA.
