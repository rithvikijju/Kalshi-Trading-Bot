# Funding Rate Arbitrage — Strategy Spec

## The trade

For each of {ETH, BTC}:
- **Long** spot on Coinbase (US-regulated, deepest USD on-ramp)
- **Short** perpetual future on Hyperliquid (DEX, no KYC, lowest US-accessible fees)
- Delta-neutral by construction; PnL = funding payments collected

**Hold continuously.** Every funding interval (1h on Hyperliquid), the
perpetual short receives `funding_rate × position_size` from leveraged
longs. We just sit there and collect.

## Why it works

Crypto perpetual exchanges set the funding rate dynamically to keep perp
price near spot. When more traders want to be long with leverage than
short, funding turns positive — longs pay shorts. This happens persistently
because:

1. Retail traders bias toward long bets on volatile assets
2. Leverage is cheaper than buying spot (you tie up less capital)
3. There's no equivalent way for "natural" short interest to clear

Our position is the natural short — we're providing leverage to the longs
without taking directional risk.

## Live regime (as of May 2026)

```
Hyperliquid ETH funding 24h trailing: ~0.03 bp/hr  → ~2-7%/yr annualized
Hyperliquid BTC funding 24h trailing: ~0.01 bp/hr  → ~1-3%/yr annualized
ETH perp basis vs Coinbase spot:      +5-10 bps
BTC perp basis vs Coinbase spot:      +5-10 bps
```

These rates are MUCH lower than BitMEX historical (2016-2022 averaged 8-15%
annualized). The compression is because:
- Spot ETFs reduced retail leverage demand
- Hyperliquid has efficient price discovery (lower funding spread)
- Sophisticated basis arbitrageurs already capture most of the spread

**Realistic 2026 expectation:** 3-6% on notional on top of ~5% T-bill carry
on idle capital. Total combined return on capital: 5-10%/yr.

## Position sizing for $5K

| Bucket | Where | Amount |
|--------|-------|--------|
| Spot long ETH | Coinbase Advanced | $1,250 |
| Spot long BTC | Coinbase Advanced | $1,250 |
| USDC on Hyperliquid (perp margin + reserve) | Hyperliquid | $2,500 |
| **Total** | | **$5,000** |

On Hyperliquid, $2,500 USDC supports:
- Short $1,250 ETH-PERP at 5× cross-margin = $250 margin
- Short $1,250 BTC-PERP at 5× cross-margin = $250 margin
- Reserve: $2,000 (well above liquidation buffer)

Effective notional: $2,500 (sum of ETH + BTC positions, delta-neutral).

## Entry / exit rules

**Entry (initial deployment):**
- Buy ETH spot on Coinbase, market order, $1,250
- Buy BTC spot on Coinbase, market order, $1,250
- Bridge $2,500 USDC to Hyperliquid (via Arbitrum bridge)
- On Hyperliquid: short ETH-PERP at $1,250 size, short BTC-PERP at $1,250 size

**Hold:** indefinitely, while these are all true:
- 7-day rolling-mean funding > 0 on each coin
- Basis on each coin < 50 bps (perp not >0.5% above spot — sign of stress)
- Margin ratio on Hyperliquid > 3× minimum
- Bot is up and monitoring

**Partial exit (per coin) if any becomes true:**
- 7-day funding ≤ 0 for 3 consecutive days → close that coin's position
- Basis exceeds 50 bps for 1 hour → close that coin (basis-blowout protection)
- Margin ratio drops below 2× → close & rebalance margin upward

**Full exit (emergency):**
- Either exchange shows ≥30-min outage → close everything, wait for restoration
- Any individual position MtM is down >5% → kill switch

## Risk controls

**Liquidation protection:**
- Margin ratio target: 5× (i.e., maintain 20%+ margin)
- Alert at 3×, force-reduce at 2×, force-close at 1.5×
- Hyperliquid auto-liquidates at 1.0× — we want significant cushion

**Basis blowout:**
- During crypto stress events (COVID Mar 2020, FTX Nov 2022), basis can
  spike 50-200 bps in hours
- We close BEFORE liquidation hits us, take the small loss
- Historical worst single-day basis move (basis-aware backtest): -4.9% on
  COVID March 13, 2020

**Counterparty:**
- Coinbase has FDIC insurance on USD held (up to $250K)
- Hyperliquid has on-chain reserves visible (audit yourself)
- Don't keep >$50K on Hyperliquid until you've verified everything

**Operational:**
- Daily check that positions match expectations (reconciliation)
- Weekly backup of position state
- Phone alerts on margin ratio < 3× or basis > 30 bps

## Expected month-by-month at $5K

Conservative case (3% on notional + T-bill on idle):
- Funding income: 3% × $2,500 notional = $75/yr = $6.25/mo
- T-bill income: 5% × $2,000 idle = $100/yr = $8.33/mo
- **Total: ~$15/mo, ~$180/yr = 3.6%**

Base case (5% on notional + T-bill):
- Funding income: 5% × $2,500 = $125/yr = $10.40/mo
- T-bill income: 5% × $2,000 = $100/yr = $8.33/mo
- **Total: ~$19/mo, ~$225/yr = 4.5%**

Optimistic (10% on notional + T-bill, requires funding regime improvement):
- Funding income: 10% × $2,500 = $250/yr = $20.80/mo
- T-bill income: $100/yr = $8.33/mo
- **Total: ~$29/mo, ~$350/yr = 7%**

**At $5K, this is a "prove-the-system-works" deployment.** Real money is
made when you scale to $25K+ where the absolute dollar returns justify the
operational overhead. See `SETUP_GUIDE.md` for the scaling roadmap.

## What this is NOT

- Not high-frequency. You touch this maybe once a week.
- Not directional. We don't predict BTC or ETH price.
- Not a money printer. 4-7% returns above risk-free, with real tail risk.
- Not totally passive. You need to monitor for basis blowout + margin level.

## Comparable strategies (and why this one)

| Strategy | Annual return | Capital needed | Tail risk |
|----------|---------------|----------------|-----------|
| T-bills | ~5% | any | none |
| **Funding arb (this)** | **~5-10%** | **$5K min, $50K useful** | **basis blowout, exchange risk** |
| Kalshi T1 (your bot) | ~10-30% on $1-3K | $1-3K cap | Kalshi exchange risk |
| VRP options | ~6-9% | $10K+ | Vol spike (Volmageddon) |
| Index momentum | ~6-8% | $50K+ | Trend break |
