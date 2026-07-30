# Funding arb (ETH spot / HL perp) — DEPLOYED SURVIVOR

*Already passed the gauntlet during prior research. Catalogued here for completeness.*

## Thesis
Long ETH spot on Coinbase + short ETH perpetual on Hyperliquid in matched notional.
Delta-neutral. Collect funding payments from leveraged retail longs.

## Mechanism
Retail demand for leverage in crypto with no easy real-world short supply →
funding rate stays structurally positive. Documented across 8+ years of BitMEX
data, survives all crashes.

## Validation summary (from `MEMORY.md` + bias audit)
- Honest Sharpe 6 (post bias-audit; original Sharpe 14 was inflated by daily-resolution
  data hiding intraday basis variance)
- Honest APR 22%, current regime 11% (about half historical median)
- Max drawdown -2.4% median, -3.7% at 5th percentile

## Capacity
$50M before slippage drag (Hyperliquid ETH perp OI is $5-10B globally).

## Verdict
**SURVIVED** — deployed, running. Primary scalable sleeve.

## Decay clock
Indefinite if structural retail-long bias persists. The risk is regime change:
sustained negative funding (basis trade unwinds). Has happened historically post-Luna
2022 — required temporary suspension on BTC; ETH funding stayed positive.

## Extensions worth doing
- Multi-asset extension to BTC + SOL + AVAX. Per `SESSION_HIGHLIGHTS` recommendation:
  quadruples notional at same yield. **Tested in `xchg_carry_audit` — honest APR after
  realistic frictions is 1-3% / Sharpe 1-5, NOT a 4× free lunch.** Re-read the audit
  before scaling.
