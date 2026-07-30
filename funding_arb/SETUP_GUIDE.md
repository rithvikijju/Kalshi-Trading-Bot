# Setup Guide — Funding Rate Arb, $5K Start

Step-by-step from zero to paper trading to live trading. Plan for ~1 week
of evening work to get fully set up.

## Phase 0 — What you need before starting

- [ ] $5,000 in a bank account
- [ ] A computer that can stay on (or cron-able cloud VM later)
- [ ] About 1 hour/week ongoing for monitoring
- [ ] Email + phone (for KYC, alerts)
- [ ] A crypto wallet (MetaMask or Rabby — for Hyperliquid)

If you don't have a wallet yet: install [Rabby](https://rabby.io/) or
[MetaMask](https://metamask.io/), back up your seed phrase to a piece of
paper (not the cloud), and you're ready.

## Phase 1 — Open accounts (Days 1-3)

### Coinbase Advanced (spot leg, US-regulated)

1. Go to https://www.coinbase.com → Sign up
2. Complete identity verification (passport or driver's license + selfie)
3. Link a US bank account
4. Wait for ACH approval (1-3 business days)
5. Once approved, go to https://www.coinbase.com/advanced-trade and enable
   Advanced Trade if not already on
6. Verify you can see BTC-USD and ETH-USD markets

**Why Coinbase Advanced (not Coinbase regular):** lower fees (0.05% taker
vs 1.49% on the consumer app), real maker/taker order book.

### Hyperliquid (perpetual short leg)

Hyperliquid is a DEX — there's no signup, just wallet connection.

1. Bridge USDC to Arbitrum first:
   - On Coinbase, when buying USDC, choose network = Arbitrum
   - Or transfer USDC from any wallet to Arbitrum L2
2. Connect your wallet at https://app.hyperliquid.xyz
3. Click "Deposit" and follow the bridge flow (USDC from Arbitrum →
   Hyperliquid USDC)
4. Bridge takes ~30 sec, fee is ~$0.10
5. Approve trading on the app (one-time signature)

**Why Hyperliquid:** DEX so no KYC blocks for US users, lowest fees
(0.025% taker, -0.001% maker rebate), full self-custody, instant
deposits.

### Optional: API keys (for automation later)

For now you can trade manually through the UIs. Once paper trading shows
the strategy works, automate via API:

**Coinbase Advanced API key:**
1. https://www.coinbase.com/settings/api → New Key
2. Permissions: `view`, `trade` (NOT `transfer` — security)
3. Restrict by your home IP
4. Save the key as `~/.config/funding_arb/credentials.env` (see
   API_KEYS.md format)

**Hyperliquid API:**
1. From the Hyperliquid app: Settings → API → Create API Wallet
2. Save the private key — DO NOT share, this CAN withdraw your funds
3. Funded via your main wallet's USDC; you can trade but not withdraw
   beyond the API wallet's balance
4. Add to credentials.env

## Phase 2 — Capital allocation (Day 4)

Goal split for $5K:

| Bucket | Where | Amount | Why |
|--------|-------|--------|-----|
| ETH spot | Coinbase | $1,250 | Long leg #1 |
| BTC spot | Coinbase | $1,250 | Long leg #2 |
| USDC on Hyperliquid | Hyperliquid | $2,500 | Perp margin + reserve |

Step-by-step:
1. Transfer $2,500 USD to Coinbase via ACH (free, 1-2 days)
2. Transfer $2,500 USD to Coinbase too (we'll bridge to USDC)
3. Wait for funds to settle
4. Buy $1,250 ETH-USD using Advanced Trade (market or limit, your choice)
5. Buy $1,250 BTC-USD using Advanced Trade
6. Convert $2,500 USD → USDC on Coinbase (free)
7. Withdraw $2,500 USDC to your Arbitrum address
   - Network: Arbitrum One
   - Cost: ~$2-5
8. Bridge USDC from Arbitrum to Hyperliquid (in-app)
9. Verify $2,500 USDC shows in Hyperliquid

Total time: ~30 minutes of clicking spread over 2 days waiting for ACH.

## Phase 3 — Paper trading (Days 5-21)

For 2-3 weeks, **don't open the perp shorts yet**. Just paper trade.

1. Run `funding_arb_live.ipynb` in paper mode
2. The notebook will:
   - Pull live funding rates from Hyperliquid every hour
   - Pull live spot prices from Coinbase every minute
   - Simulate the positions you'd hold ($1,250 spot ETH + $1,250 short
     ETH-PERP, same for BTC)
   - Track simulated PnL hour-by-hour
   - Log everything to `data/paper_log.jsonl`
3. After 1 week, review `data/paper_log.jsonl` to see:
   - How much would you have earned in funding?
   - How much did the basis move?
   - Were there any near-misses on margin?
4. **Decision point at end of week 2:**
   - If paper PnL is positive and consistent: graduate to live
   - If paper PnL is negative: stay in paper, debug, or skip (regime may
     be unfavorable right now)

## Phase 4 — Live entry (Day ~21)

Once paper trading has shown 2+ weeks of consistent positive funding income:

1. Set the notebook to live mode (`CFG['mode'] = 'live'`)
2. Manually verify the first trade:
   - Notebook will prompt: "Open ETH position? $1,250 spot + $1,250 perp short"
   - Confirm yes
   - Open BOTH legs within 60 seconds (simultaneity matters for delta
     neutrality)
3. Do the same for BTC
4. Run the daily monitor cell — verify positions match expectations

## Phase 5 — Ongoing operations (Day 21+)

Daily (5 minutes):
- Open the notebook, run the dashboard cell
- Verify positions are still delta-neutral
- Verify margin ratio > 3×
- Verify funding accrued matches expectations

Weekly (30 minutes):
- Check 7-day funding rolling average — still positive?
- Check for basis blowout — was peak basis ever > 30 bps?
- Reconcile actual PnL against simulated

Monthly (1 hour):
- Tax accounting: every funding payment is taxable income
- Export trade history to CSV
- Review max drawdown vs expectations

## Phase 6 — Scaling up

Once you've run for 3+ months without issues, increase capital:

| Month | Capital | Year-1 expected return | Hours/week |
|-------|---------|------------------------|------------|
| 0 | $5K | $200-350 | 1 |
| 3 | $25K | $1,000-1,750 | 1-2 |
| 6 | $50K | $2,000-3,500 | 2 |
| 12 | $100K | $4,000-7,000 | 3-4 |
| 18 | $250K | $10,000-17,500 | 5-7 |

At $250K+ this becomes worth your time as a real income source.
Below $25K it's mostly "infrastructure that proves the system works."

## What can go wrong (read before going live)

| Issue | Likelihood | Mitigation |
|-------|------------|------------|
| Funding flips persistently negative | Medium | Auto-close, switch to T-bills only |
| Basis blows out >50 bps for hours | Low | Force-close at 50 bps, take small loss |
| Hyperliquid liquidation | Very low if margin is 5× | Margin alerts, manual top-up |
| Coinbase outage / freeze | Medium (rare but happens) | Diversify (later, for now monitor) |
| You forget to monitor for a week | High at $5K size | Set up phone alerts (Pushover/Telegram) |
| Tax surprise at year end | High | Track every funding payment as ordinary income |
| US regulation change | Low but real | Have a USD-out plan; Coinbase part is easier |
| HYPERLIQUID-specific risk (smart-contract bug) | Very low | Don't keep >$50K there until 1yr of clean ops |

## Specific failure modes for $5K size

**Position too small to be worth fees:**
At $1,250 per spot leg, Coinbase's 0.05% taker = $0.63 per fill. Over a
year with no rebalancing, that's $1.25 total. Negligible. ✓

At Hyperliquid 0.025% taker on $1,250 perp short = $0.31 per fill.
Over a year you'll open and close maybe 4-8 times = $5-10 total fees. ✓

So fees don't kill this at $5K size as long as you don't churn.

**Funding payment too small to bother:**
At 3 bps per hour on $1,250 notional = $0.0004/hour = $0.0094/day per coin.
$5.59/year per coin. Two coins = $11.18/year from funding alone.

That's depressing in absolute terms but it's THE EDGE working as designed.
Your real return at $5K comes from T-bill carry on the idle $2,000 (~$100/year)
plus this small funding edge. Total ~$110-200/year on $5K.

**Margin call you didn't watch:**
Hyperliquid sends in-app notifications and you can set phone alerts via
their app. Set alerts at: 3× margin (heads up), 2× margin (action needed).

## Roadmap to "this is a real business"

| Phase | Capital | What you're proving |
|-------|---------|---------------------|
| **Now** | **$5K** | **Plumbing works, you can execute** |
| 3 mo | $25K | Strategy works in your hands across regimes |
| 6 mo | $50K | Returns stable; tax/ops sustainable |
| 12 mo | $100K | Track record exists; talk to first LPs |
| 18 mo | $250K-1M | Real fund seed |
| 36 mo | $1M-10M | Multi-strategy fund |

Each phase, you've already DONE the operational work — capital just makes
the absolute dollars bigger. The hard work is at $5K. The reward is at $250K+.
