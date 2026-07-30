# ETF cointegration + OU-band stat-arb — DEAD

**Status**: KILLED in V2 OOS testing 2018-2026.

## What was tested

19 candidate ETF pairs across sectors (XLE/XOP, XLF/KRE, XLK/SMH, XLY/XRT,
XLV/IBB, XLI/IYT, XLU/VPU), countries (EWA/EWC, EWG/EWQ, EWJ/EWT, EWZ/EWW),
commodities (GLD/SLV, GDX/GLD, USO/XLE), rates (TLT/IEF, HYG/LQD), and index
near-duplicates (SPY/IVV, QQQ/SPY, IWM/SPY).

Pipeline:
1. Engle-Granger on log prices, 2005-2017 training
2. OU process fit to spread
3. Keep pairs with ADF p<0.05 AND 5d < half-life < 252d → 4 survivors
4. Static-beta OU-band trading 2018-2026 OOS

## Result

| Pair    | Sharpe | AnnRet | MDD     |
|---------|--------|--------|---------|
| HYG/LQD | +0.50  | +3.0%  | -8.6%   |
| EWA/EWC | -0.51  | -4.1%  | -46.7%  |
| XLI/IYT | -1.47  | -13.2% | -73.2%  |
| IWM/SPY | -3.82  | -35.6% | -95.4%  |
| **Portfolio** | **-2.83** | **-12.5%** | **-66.3%** |

## What killed it

Static cointegration assumes the hedge ratio is constant. In 2018-2026:
- IWM/SPY: small caps structurally underperformed (5+ years of mega-cap concentration).
  Strategy kept buying the dip; the dip kept dipping. -95% MDD.
- EWA/EWC: 2014 oil crash and 2020 COVID broke the resource-linked spread.
- XLI/IYT: transport diverged from broader industrials post-COVID supply chain.

These are not "the model didn't fit" — they are regime changes the model
cannot accommodate.

## Kalman dynamic hedge — also DEAD

Replaced static β with Kalman filter state β_t. Tradeoff is forced:
- Slow β-drift (delta=1e-6): same divergence-blowup as static. Sharpe 0.68.
- Fast β-drift (delta=1e-4+): filter absorbs the spread signal into β,
  innovations stay small, no trades fire. Sharpe 0.

Best portfolio Sharpe achievable was 0.68 — below the bar at $50M after impact.

## What would unlock this

- **Intraday data** (5-15 min bars) — OU on shorter horizons where regime
  changes are smaller. Not available here.
- **Sector-neutral pairs at higher frequency** — e.g. intraday XLE/XLF, where
  the macro-driven divergence is averaged out.
- **More pairs in basket form** — 50+ pairs equal-weighted, where idiosyncratic
  pair blowups are diversified away. Would need a tighter ADF threshold (p<0.01)
  to control multiple-testing.

**None of these are accessible without subscription tick data + a real low-
latency execution stack. NEEDS-DATA verdict.**

## Family A verdict

Static-beta cointegration stat-arb at daily frequency on ETFs is DEAD in the
2018-2026 era. The HFT shops have squeezed out everything that was tradeable
at this latency.
