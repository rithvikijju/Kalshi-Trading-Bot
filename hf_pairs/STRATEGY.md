# HF Crypto Pairs Strategy — BTC/ETH Intraday Mean Reversion

## Mechanism

BTC and ETH are deeply cointegrated on intraday horizons. When one moves
faster than the other for short-lived microstructure reasons (a single
large taker, a brief liquidity gap), the log-spread `log(BTC) - β·log(ETH)`
diverges from its mean. The spread reverts within 30-120 minutes most of
the time because the two are driven by the same beta (global crypto risk).

## Entry/exit rules

```
spread = log(BTC) - log(ETH)              # β = 1, simplest version
mean    = rolling 60-min mean of spread
std     = rolling 60-min std  of spread
z       = (spread - mean) / std

ENTRY: |z| > 2.0  AND no current position
  z > 2:  spread is too high → BTC overpriced relative to ETH → SHORT BTC, LONG ETH
  z < -2: spread is too low  → BTC underpriced relative to ETH → LONG BTC, SHORT ETH

EXIT:
  |z| < 0.5    → take profit
  |z| > 3.0    → stop loss
  held > 4h    → time stop (signal stale)

SIZING: dollar-neutral.  notional = position_size × portfolio_value / 2 per leg.
```

## Realistic costs (Coinbase Advanced "Pro" tier)

- Maker fee: 0.00% (zero), Taker: 0.05% per leg
- We use taker (market orders) for fast fills → 0.10% round trip × 2 legs = 0.20% per trade
- Slippage: ~0.01-0.05% per leg depending on size, call it 0.05% round trip
- **Total friction: ~0.25% per trade**

That means we need an average win > 0.30% per trade to net positive. At z=2
entry / z=0.5 exit, we're capturing ~1.5σ × spread_std of mean-reversion.
For BTC/ETH on 1-min bars, spread_std is roughly 0.0005-0.0010 (5-10 bps),
so 1.5σ ≈ 0.075-0.15%. **Marginal**. Strategy depends on getting most trades
to fill near z=0 rather than waiting for full reversion.

## Walk-forward / no look-ahead

The 60-min rolling mean/std at bar t uses only bars [t-60, t]. The entry
decision at bar t uses z(t). The trade fills at bar t+1 (next bar's open),
matching what a live execution at "current price" would actually look like.

Stop and exit conditions are evaluated at each subsequent bar's close.

## Capacity

Coinbase Advanced has daily volume of ~$500M-$1B in BTC + ETH. Our trades
are ~$5-50K each. Capacity is comfortably $5-30M total deployment before
slippage doubles.

## Why this is different from the daily basis trade

- Holding period: 30-120 minutes vs days/weeks
- Trade frequency: 5-15/day vs weekly
- Mechanism: pairs cointegration vs funding rate carry
- Risk profile: short-lived market-neutral exposure vs basis blowout risk
