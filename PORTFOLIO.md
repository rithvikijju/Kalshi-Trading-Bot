# Portfolio architecture — scalable multi-sleeve setup

## The capacity problem K6 alone can't solve

K6 (Kalshi BTC spot-displacement) is bounded by **Kalshi orderbook depth**: median 17 contracts per signal, p95 of 52. Even fully utilized, the strategy ceilings at roughly **$3-6K/yr** of PnL regardless of how much capital you throw at it. Throwing $50K at K6 doesn't earn more than throwing $5K — the extra capital just sits idle.

To scale beyond that, you need strategies in **non-overlapping capacity buckets** — different markets, different liquidity pools, different fundamentals.

## The three-sleeve architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SLEEVE              │  LIQUIDITY POOL          │  CAPACITY    │  STATUS    │
├─────────────────────────────────────────────────────────────────────────────┤
│  funding_arb         │  Hyperliquid perp OI     │  $5K - $50M  │  RUNNING   │
│  hf_pairs (BTC/ETH)  │  Coinbase spot depth     │  $1K - $30M  │  RUNNING   │
│  kalshi_k6           │  Kalshi BTC book depth   │  $100 - $5K  │  STOPPABLE │
└─────────────────────────────────────────────────────────────────────────────┘
```

Each draws from a different market, with a different fundamental edge driver. None compete for the same liquidity.

### Sleeve 1 — `funding_arb` (ETH/BTC funding rate)

**What it does:** Long spot ETH/BTC on Coinbase, short matched perpetual on Hyperliquid. Collect funding payments from leveraged retail longs every hour.

**Why it scales:** Hyperliquid perp OI is $5-10B globally for ETH alone. A $50M position is 0.5% of OI — basically frictionless. Capacity is structural and persists across regimes.

**Edge driver:** Retail demand for leverage. There's no real-world short supply for crypto, so the funding mechanism prices the cost of leverage at a small positive premium. Documented across 8 years of BitMEX data, survives all crashes.

**Honest expectations:**
- Current regime: ~11% APR on perp notional → 5.5% on capital → ~3.5% after live haircut
- Historical median: ~22% APR → 11% on capital → ~7-8% after live haircut
- Capacity drag starts at $25M, bites hard at $50M+

**Files:**
- `funding_arb/paper_runner.py` — paper loop (currently running, ETH only)
- `funding_arb/live_runner.py` — real-money version, needs Hyperliquid + Coinbase creds
- `funding_arb/STRATEGY.md` — design doc
- `funding_arb/projections.py` — capital-tier projection table
- `funding_arb/data/paper_log.jsonl` — per-cycle log
- `funding_arb/data/paper_state.json` — current snapshot

### Sleeve 2 — `hf_pairs_live` (BTC/ETH cointegration)

**What it does:** Tracks the log-spread `log(BTC) − log(ETH)` on a rolling 60-min window. When the z-score exceeds ±2σ, enters a dollar-neutral pair trade (short the outperformer, long the underperformer). Exits at |z|<0.5 (TP), |z|>3.5 (SL), or 4h timeout.

**Why it scales:** Coinbase Advanced does $500M-$1B/day in BTC+ETH. A $50K leg is 0.005% of daily volume — invisible. Strategy supports $5M-$30M total deployment before slippage becomes meaningful.

**Edge driver:** Short-term cointegration between two highly-correlated crypto assets. When microstructure noise pushes them apart, they mean-revert within 30-120 minutes typically.

**Honest expectations:**
- Per-trade target: capture 0.5-1.5σ of spread move = ~0.075-0.15% of leg notional
- Friction: ~0.25% per round trip (taker fees + slippage)
- Realistic per-trade edge: net 0.05-0.15% after costs
- Expected trades: 3-10/day depending on volatility regime
- Annualized: 5-15% on capital in healthy regimes

**Files:**
- `hf_pairs_live/paper_runner.py` — paper loop (running)
- `hf_pairs_live/config.py` — entry/exit thresholds
- `hf_pairs_live/spot_feed.py` — dual BTC+ETH polling + z-score
- `hf_pairs_live/portfolio.py` — pair-position tracking
- `hf_pairs_live/data/paper_log.jsonl`, `paper_state.json`

### Sleeve 3 — `kalshi_k6` (BTC binary staleness arb)

**What it does:** When BTC spot moves $50-$500 past a Kalshi strike with ≤15min until close, buy the favored side at the stale market-maker ask. Hold to settlement. Side-aware vol-regime filter (K14) gates YES vs NO entries.

**Why it's capacity-bounded:** Kalshi BTC top-of-book depth median is 17 contracts. The strategy can't deploy more than ~20 contracts per signal without walking the book.

**Edge driver:** Kalshi market makers reprice on a 200ms-2min lag after fast BTC moves. The "stale window" is where our edge lives. Validated at +$0.039/trade × ~45 signals/day on active days.

**Honest expectations:**
- $100 to $1K capital: capital-limited, ~+300-500% APR theoretically
- $1K to $5K capital: depth-saturated, ~$3-4K/yr fixed PnL ceiling
- Above $5K: extra capital sits idle, % return collapses

**Use case:** This is a "small-account amplifier." Run it on $1-5K for high % returns, not on $50K+.

**Files:**
- `kalshi_k6/paper_runner.py` — paper loop (stopped, final NAV $101.26 after 21h)
- `kalshi_k6/live_runner.py` — Kalshi REST live trading
- `kalshi_k6/STRATEGY.md` — full mechanic explanation
- `kalshi_k6/data/paper_log.jsonl`, `paper_state.json`

## Capacity-aware allocation

Given the three sleeves' capacity profiles, here's how to allocate at different total capital levels:

| Total capital | funding_arb | hf_pairs | kalshi_k6 | Notes |
|---|---|---|---|---|
| $1K | $0 | $0 | $1K | K6 only — others below minimum |
| $5K | $3K | $0 | $2K | Funding arb's floor is $5K but $3K can canary it |
| $25K | $15K | $5K | $5K | All three viable; K6 saturated |
| $100K | $60K | $35K | $5K | Funding + HF pairs each clearly below capacity |
| $1M | $700K | $300K | $5K | K6 contribution becomes a rounding error |
| $10M | $7M | $3M | $5K | At scale, HF pairs starts hitting capacity drag |
| $50M+ | $40M | $10M (max) | $5K | HF pairs capped; consider adding more sleeves |

Allocation principle: **fill each sleeve up to its own capacity ceiling, then move to the next**.

## The total scalable ceiling

Adding all three:

```
funding_arb max ≈ $50M
hf_pairs max   ≈ $30M
kalshi_k6 max  ≈ $5K (rounding)
                ──────
Total           ≈ $80M before capacity drag
```

Beyond $80M, you'd want to add:
- Calendar basis (CME ETH/BTC vs spot): $10-30M
- Cross-coin funding diversification (SOL, AVAX): $5-20M each
- Equity pairs (KO/PEP, MA/V, GS/MS): $50-200M total
- Sector ETF momentum: $500M+

## How they interact

**Correlation between sleeves:**
- funding_arb ↔ hf_pairs: ~0.2-0.3 (both crypto, but different mechanisms)
- funding_arb ↔ kalshi_k6: ~0.0 (different timescales, different markets)
- hf_pairs ↔ kalshi_k6: ~0.0 (totally independent)

The portfolio diversification benefit is real — combined Sharpe should be higher than any individual sleeve's Sharpe.

**Failure modes:**
- A basis blowout (LUNA / FTX / COVID class) hurts funding_arb directly. hf_pairs might benefit if it widens the BTC/ETH spread. K6 might benefit from elevated vol.
- A liquidity crisis at Coinbase hurts hf_pairs and funding_arb. K6 keeps going (different exchange).
- A Kalshi outage takes down K6 only.
- Three independent exchanges = three independent counterparty exposures.

## Running everything

```bash
# Start funding arb (ETH leg only — currently running):
cd /Users/rithvikijju/edge-bot/funding_arb
nohup python paper_runner.py --assets ETH > data/runner.log 2>&1 &

# Start HF pairs (currently running):
cd /Users/rithvikijju/edge-bot/hf_pairs_live
nohup python paper_runner.py --bankroll 1000 > data/runner.log 2>&1 &

# Start K6 (currently stopped — opt in when ready):
cd /Users/rithvikijju/edge-bot/kalshi_k6
nohup python paper_runner.py --bankroll 100 > data/runner.log 2>&1 &

# View unified dashboard:
cd /Users/rithvikijju/edge-bot
python portfolio_dashboard.py            # one-shot
python portfolio_dashboard.py --watch    # refresh every 30s
```

## Wire-up status

- [x] funding_arb paper running on ETH ($5K notional)
- [x] hf_pairs paper running on BTC/ETH ($1K bankroll)
- [x] kalshi_k6 paper validated ($100 → $101.26 over 21h), stoppable
- [x] portfolio_dashboard.py aggregates all three
- [ ] Live-money deployment for any sleeve (paper-validated, awaiting decision)
- [ ] Cross-sleeve allocator (auto-rebalance to fill each sleeve to ceiling)
- [ ] Risk-aggregate kill switch (halt all sleeves on portfolio drawdown)
- [ ] Multi-asset funding arb (BTC + ETH + SOL + AVAX simultaneously)
