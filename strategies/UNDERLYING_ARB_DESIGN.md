# Underlying / Options Arbitrage — Design Doc

**Goal:** Build the strategy of "shorting underlying when option is overvalued"
that powers Citadel-style options market making. Backtest rigorously with
public data, then ship a live notebook.

## The strategy in one sentence

When an option is priced too high (implied vol > realized vol), **sell the
option** and **delta-hedge by trading the underlying** in the opposite
direction of the option's delta. As the stock moves, you systematically
short more underlying when stock rises (your short call's delta grows
negative) and cover when stock falls. Profit = premium collected − hedge
cost. Works because options markets persistently overprice volatility
(Variance Risk Premium).

This is the *vanilla* version of what Citadel/Susquehanna/Jane Street run
at industrial scale via dispersion trades, vol surface arb, and dynamic
hedging across thousands of names. We'll deploy the SPY (S&P 500 ETF)
version because:

1. SPY options are the deepest in the world (>$1B/day notional traded)
2. VRP is biggest at the index level (institutional put hedging)
3. Public data is comprehensive (VIX = expected SPY IV, free historical)
4. Capacity scales to $100M+ before slippage matters

## Three formulations (we'll backtest all)

### V1. Variance Risk Premium (the main strategy)

**Mechanism:** Sell 1-month at-the-money SPY straddle (short call + short
put), delta-hedge daily. Hold to expiry or roll monthly.

**Edge:** SPY 30-day implied vol has averaged ~16% historically vs
realized vol ~14%. The 2pt difference is the "premium." Sellers of
volatility (us) capture this in exchange for taking tail risk.

**Risk:** Volmageddon (Feb 2018), COVID (March 2020), Aug 2024 carry
unwind. These events can wipe 6+ months of premium in days. We size to
survive worst-case at 3× historical max-loss.

**Citadel angle:** Their option market-making desk effectively is a
massive VRP harvester — they sell options to retail/institutional buyers
and dynamically hedge.

### V2. Put-Call Parity Synthetic Forward Arb

**Mechanism:** For European-style options: Synthetic forward = Call − Put
+ Strike. If `spot > synthetic + costs`, short stock + long synthetic
(buy call, sell put). The difference captures **implied borrow cost** vs
actual borrow cost.

**Edge:** Pure structural arb when parity breaks. Mostly closed in
liquid US equities but still exists in hard-to-borrow names, foreign
ADRs, and around dividend events.

**Risk:** Early exercise on American puts, dividend mispricing,
counterparty risk on synthetic leg.

**Citadel angle:** This was THE original Citadel conv bond arb mechanism
— buying the convertible (= stock + option) and shorting underlying to
isolate the option component's mispricing.

### V3. Cross-sectional Dispersion

**Mechanism:** Short index volatility (sell SPY straddle), long
single-stock volatility (buy straddles on top 30 holdings). Captures
"correlation premium" — the index is less volatile than the weighted
sum of components when correlation < 1, so index implied vol persistently
overprices correlation.

**Edge:** Real and persistent. Sharpe 1-2 historically. But requires
trading 30+ option positions = high friction at small size.

**Risk:** Correlation spikes during crashes (everything moves together),
which is exactly when you don't want it.

## Realistic expectations

| Strategy | Live Sharpe | Annual return | Tail loss | Capital req |
|----------|-------------|---------------|-----------|-------------|
| V1 VRP basic | 0.6-1.0 | 8-15% | -30% to -50% | $100K min |
| V1 VRP risk-managed | 0.8-1.2 | 6-10% | -10% to -20% | $250K min |
| V2 Parity arb | 1.0-2.0 (when found) | 3-6% on capital | low | $1M+ |
| V3 Dispersion | 1.0-1.5 | 8-12% | -20% to -40% | $500K+ |

Capacity scaling:
- V1 SPY VRP: $50-200M (depends on willingness to roll big positions)
- V2 Parity: $5-20M per opportunity
- V3 Dispersion: $20-100M

## Backtest plan

1. **Phase 1**: V1 VRP backtest using VIX + SPY (data we already have,
   2010-2026, free). This is the headline.
2. **Phase 2**: V2 Parity check using yfinance current options chain
   (snapshot data, can backtest forward from today).
3. **Phase 3**: V3 Dispersion — complex, will document but skip if time
   short.

## API keys needed (deployment)

| Service | Free tier | Paid | Purpose |
|---------|-----------|------|---------|
| yfinance | yes (no key) | n/a | Historical SPY/VIX/stocks |
| Polygon.io | 5 req/min | $99/mo unlimited | Real-time options chain |
| Alpha Vantage | 25 req/day | $50/mo | Historical IV time series |
| Tradier | free (with $) | $10/mo | Live options data + execution |
| IBKR Pro | $0 commish + market data fees | varies | Production execution |
| TastyTrade | free | n/a | Cheap options execution |

For backtest: yfinance is enough.
For paper trading: Polygon free tier.
For live: Tradier or TastyTrade for execution + Polygon for data.

## Notebook plan

Single Jupyter notebook `underlying_arb_live.ipynb` with sections:
1. Config + API keys
2. Live data fetcher (SPY price, VIX, options chain via yfinance)
3. Signal generator (compute IV-RV gap, parity violation check)
4. Position sizing + risk mgmt
5. Paper trading simulator
6. Backtest harness with replayed historical data
7. Live monitoring dashboard
