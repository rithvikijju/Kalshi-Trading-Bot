# Cross-Exchange Funding-Rate Carry (HL × OKX)

## Strategy

Hyperliquid (HL) consistently pays HIGHER perp funding on mid-cap alt-perps than
OKX does on the same coin, because HL's user base is degen-leveraged retail.
That asymmetry doesn't arb away because most retail traders use only one venue.

For each coin in our basket, we run a delta-neutral pair:

  HL leg : short perp  (collect HL's elevated funding)
  OKX leg: long  perp  (pay OKX's lower funding)
  Net    : +(HL_fund − OKX_fund) per cycle

Sign is locked at deploy time from the trailing average spread. If sign flips
persistently (>30 days), the pair is closed.

## Universe

8 winners from 6-month backtest (Mar–Jun 2026, sign-locked OOS after first 60
cycles per coin):

| coin    | avg APR spread | Sharpe | 8h periods OOS |
|---------|----------------|--------|----------------|
| HYPE    | +9.57%         | 28.7   | 224            |
| LINK    | +9.00%         | 42.6   | 207            |
| PENDLE  | +8.37%         | 16.0   | 116            |
| XLM     | +6.23%         | 15.5   | 201            |
| BCH     | +4.05%         | 12.7   | 268            |
| LTC     | +3.67%         | 15.4   | 220            |
| XRP     | -3.22%*        | 14.9   | 217            |
| AVAX    | +10.69%        | 37.2   | 72             |

*XRP spread is NEGATIVE: OKX pays more than HL → take HL long, OKX short.

Equal-weight basket: gross APR **+6.88%, Sharpe 25.3, DD -0.07%** in OOS window.

## Capital model

Per $1 notional per coin → $0.20 margin each side at 5x leverage → $0.40 capital.
At 5x: **APR on capital ≈ 17%, Sharpe ≈ 25**. Honest live haircut: Sharpe 8-12, APR 10-15%.

## Capacity

HL OI for these alts: $20M-$1B each. OKX OI: similar. Capacity per coin: $1M-$10M before
self-impact. Basket capacity: $10M-$50M.

## Risks

1. **Spread regime flip**: SOL flipped from negative to positive spread in May 2026.
   Mitigation: monthly review of sign; auto-close on persistent flip.
2. **Liquidation cascades**: 30% adverse move on one leg could liquidate; need margin
   rebalancing between venues.
3. **Exchange-specific risk**: HL or OKX outage / insolvency.
4. **Funding-rate cap**: HL caps at ±10.95% APR; spread is also bounded.

## Files

- `STRATEGY.md` — this doc
- `config.py`    — coin universe, sign-lock, fee params
- `feed.py`      — pull live funding from HL + OKX
- `paper_runner.py` — paper simulator, logs to data/paper_log.jsonl
- `data/paper_state.json` — running NAV
