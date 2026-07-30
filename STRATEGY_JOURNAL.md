# Strategy Journal — Interview Spiel

*This is meant to be read out loud. Each section is the story I'd tell about that strategy, with a brief math anchor and the numbers, then a list of likely follow-up questions with quick answers.*

---

## The arc, in one paragraph

I started by building a **mispricing model on Kalshi BTC hourly binaries** — basically a Black-Scholes fair-value calculator that watches Coinbase spot and flags when Kalshi's market price drifts away from fair. That worked, so I added a **second signal on top** when I realized Kalshi's cross-strike quotes weren't always internally consistent — there's a free monotonicity arb sitting there. Together those became the "v2 bot." Then I hit two walls: Kalshi's fee structure kills most directional ideas, and the total capacity is maybe ten grand a year. So I **pivoted outward** to find something scalable, and after a 50-strategy bake-off the only survivor was **crypto funding-rate arbitrage** — long spot, short perpetuals, collect the funding payments. After getting that into paper trading, I went **back to Kalshi** with a more systematic hypothesis sweep and found one new edge — **K6, a spot-displacement staleness signal** — that fits as a fourth signal on the existing bot. That's the four-strategy stack.

---

## Strategy 1 — Kalshi mispricing model (this is where I started)

### The story I'd tell

"The first thing I built was a fair-value model for Kalshi's BTC hourly binaries. The idea is simple: each contract pays a dollar if BTC's hourly close beats a strike, zero otherwise. That's literally a binary option, and binary options have a closed-form Black-Scholes price. So I built a live system that polls Coinbase for BTC spot every two seconds, estimates volatility with an EWMA (the standard RiskMetrics one, lambda 0.94), runs Black-Scholes with a drift correction, and then mixes in a Merton jump-diffusion term to handle BTC's fat tails — because BS alone underestimates the chance of a big move.

The output is a 'fair' probability for every strike on the active hour. Then I compare that to Kalshi's actual ask. When fair is 94% but the market's selling YES at 88%, that's a six-cent gap. After Kalshi's fee at that price it's roughly five cents of edge, and I'd Kelly-size into it. I use quarter-Kelly because full Kelly has a fifty-percent drawdown probability even with a perfect edge estimate, and my edge estimate isn't perfect.

I also wrapped each signal type in a Wald sequential probability ratio test — basically, after every settled trade, the bot updates a log-likelihood ratio of 'this signal still has edge' versus 'this signal is now breakeven.' If that ratio drops below a confidence threshold, the bot auto-disables that signal. It's a circuit breaker for silent regime breaks."

### The math, just enough to defend it

- **Vol estimator:** EWMA, `σ²_t = 0.94·σ²_{t−1} + 0.06·r_t²`. Standard RiskMetrics 1996, ~75-period half-life.
- **Fair value:** Black-Scholes binary `P = N(d)` where `d = (ln(S/K) + (μ − σ²/2)t) / (σ√t)`. Drift μ estimated from a 20-min rolling window, capped at ±50%/yr because anything more extreme is noise on this horizon.
- **Jump correction:** Merton mixture, `fair = Σ_{k=0..5} P(k jumps)·BS(σ_k)` with λ=12 jumps/yr, 2% per jump. Taking the min of BS and jump fair values for conservatism.
- **Sizing:** quarter-Kelly. `f* = (p·b − q) / b`, where `b = (1 − price − fee) / (price + fee)`.
- **Outlier rejection on spot:** Hampel filter, k=3, with a 60-second watchdog so it doesn't get permanently stuck.

### The numbers

Seven-day backtest against captured live data: **+$43.76 on v2 vs −$8.04 on the prior bot**. Live, this tier prints a handful of fills a day with edges of one to five cents. Annualized at current sizing: somewhere in the five-to-fifteen-grand range depending on regime.

### Likely interview questions

**"Why Black-Scholes? Aren't crypto returns non-Gaussian?"**
Right — and that's exactly why I added the Merton jump-diffusion overlay. BS alone is too tight in the tails. Jump-diffusion mixes BS values across a Poisson-counted number of jumps, which inflates the implied variance and pulls deep-ITM fair values away from 1.0. I take the min of the two estimates so I never get tricked into thinking something's more certain than it is.

**"How do you know your sigma estimate is right?"**
I don't know it's right — I know how it can be wrong. If my σ is too high, my fair value gets less extreme (pulled toward 0.5), so I trade less. That's the safe direction. If my σ is too low, I'd think contracts are deep-ITM when they aren't — that's the dangerous direction. So I pad σ by 50% as an uncertainty buffer and floor it at 35% annualized. Both lean the same way: miss trades rather than buy bad ones.

**"What's the Kelly fraction and why a quarter?"**
Full Kelly maximizes long-run growth rate but has a known 50% drawdown probability with perfect edge — and my edge estimates aren't perfect. Quarter Kelly cuts variance by sixteen-x while only giving up about 25% of growth. Standard practical compromise.

**"What if a signal stops working?"**
That's what the SPRT is for. After every settled trade I update a log-likelihood ratio comparing "tier has real edge" versus "tier is breakeven." When the LLR drops below a threshold — Wald's bounds at the 5% false-disable rate — the tier auto-shuts off until I look at it. Won't trigger before twenty trades per tier, so a few unlucky early outcomes don't kill a real strategy.

---

## Strategy 2 — Cross-strike monotonicity arbitrage (added on top)

### The story I'd tell

"Once the mispricing model was running, I noticed something. Kalshi lists about 188 strikes per hourly BTC event. The probability of 'BTC exceeds strike X' has to be greater than the probability of 'BTC exceeds strike Y' if Y is bigger than X — that's just basic probability, no model needed. Which means the YES prices have to be monotone in the strike. And they weren't always! Sometimes the market maker on the higher strike would quote a YES bid that was higher than the lower strike's YES ask. That's a pure arbitrage. Buy YES at the low strike, buy NO at the high strike, and you're guaranteed at least a dollar payout against the sum of your two costs.

So that became a second signal on top of the fair-value model. It's risk-free by definition — the math literally guarantees a positive payout — but execution is hard because these arbs can disappear in under a second when HFT bots eat them. I added a calm-regime filter that only allows it to trade when BTC spot hasn't moved more than thirty dollars in the past thirty seconds, because that's the regime where the arbs actually persist long enough for me to fill both legs."

### The math anchor

- Monotonicity: `P(BTC > K_lo) ≥ P(BTC > K_hi)` for `K_lo < K_hi`. So `yes_ask(K_lo) ≥ yes_bid(K_hi)` should hold, and a violation is a risk-free arb.
- Trade structure: BUY YES at K_lo for `ask_lo`, BUY NO at K_hi for `(1 − bid_hi)`. Min payout is one dollar minus those two costs minus fees. Arb if `bid_hi − ask_lo > fees`.
- Calm filter: skip if `|spot(t) − spot(t−30s)| > $30`.

### The numbers

Live: about thirty-five fills a week, average edge a few cents per fill, win rate ~100% on settled trades (it's risk-free; the only loss path is one-leg-fill). Backtest showed the depth cap was too tight — book median is seventeen contracts, ninety-fifth percentile fifty-two — so I raised the cap from five to fifteen, which roughly tripled per-trade size. The calm filter alone took ten candidates from "evaporate before I can fill" to "100% capture, $31 across the sample."

### Likely interview questions

**"You said risk-free. What's the actual risk?"**
The math is risk-free if both legs fill. The real risk is one-leg-fill — I send both orders, one fills, the other misses because the price moved. Then I'm sitting with an unhedged binary. Mitigations: small qty per leg, the calm-regime filter (which is when arbs persist), and an aggressive timeout that closes any single-leg position quickly.

**"Why doesn't this get arbed away?"**
It mostly does, by faster bots. My RTT to Kalshi is 200-400 milliseconds. Bots with colocation are sub-100ms and they eat most of these. What I'm catching is the residual — arbs that show up during quieter spot regimes when HFTs aren't aggressively poking. The calm-regime filter explicitly only trades in that subset.

**"How much can this scale?"**
Capacity is set by Kalshi's book depth, not strategy logic. Median candidate has 17 contracts of depth, p95 is 52. At my current qty cap of 15 per leg I'm capturing most of the meat. Going higher hits one-leg-fill risk before it hits real PnL gains.

---

## Strategy 3 — Funding rate arbitrage (the pivot to capacity)

### The story I'd tell

"After living with the Kalshi bot for a while, two facts became impossible to ignore. One, Kalshi's fee structure — seven cents per contract or seven percent of P times one-minus-P, whichever is smaller — kills almost any directional play. You'd need a seven percent win-rate edge to clear fees, and real microstructure edges are one to three percent. Two, the total capacity is maybe ten thousand dollars a year. That's pocket money relative to the operational overhead of running a 24/7 bot.

So I went looking for something with the same risk profile but ten to a hundred times the capacity. I wrote out ten production-grade strategy designs, then expanded that into a fifty-strategy bake-off — microstructure, statarb, cross-sectional, info-theoretic, ML, calendar, basis carry — and backtested all of them through a uniform harness with realistic costs. Sixty-eight backtests total. Everything died at retail fees except one category: **crypto funding-rate arbitrage**, specifically on ETH.

The trade is mechanical. Crypto perpetual futures pay a funding rate every hour. When the perp price is above spot — which it usually is, because retail wants leverage to go long — longs pay shorts. So I go long ETH spot on Coinbase and short ETH perpetual on Hyperliquid in matched size. Delta-neutral by construction. Every funding interval I collect a payment from the leveraged longs.

The reason this works structurally is that there's no easy way for retail to short crypto directly — they can't borrow real BTC or ETH. So the funding mechanism is the only thing keeping perp price near spot, and it stays positive on average because long demand exceeds short supply. That dynamic has held for eight-plus years and through multiple crashes."

### The math anchor

Daily PnL has two parts:

```
PnL = funding_payment + (Δspot − Δperp) × notional
```

The first term is the income. The second is **basis mark-to-market** — and that's the silent killer. Perp and spot don't trade at exactly the same price; the gap fluctuates. Variance decomp from our data: basis MTM standard deviation is **1.3× larger than funding income standard deviation**. Most retail traders look at funding APR and ignore this. They get blown up on basis blowout days.

### The bias audit — why our headline Sharpe was lying

"The first backtest I ran gave me Sharpe 14 on ETH funding arb. Beautiful number. I didn't trust it. I did a bias audit and found the problem: funding rates are sampled eight-hourly, but my perp and spot prices were daily closes. When I did the as-of merge, three funding ticks per day saw the same perp-spot snapshot, which artificially smoothed the basis MTM variance. Intraday basis vol of ten to thirty bps was completely invisible to a daily-resolution backtest.

I re-ran with Monte Carlo corrections — injected intraday Gaussian noise calibrated to known intraday basis vol, and added stochastic tail events at the historical frequency. Honest Sharpe came out at **six**, with a 5th-to-95th-percentile band of 5.3 to 7.3. APR around 22% on a four-year ETH window. Still a real edge, just not the Sharpe-14 fantasy."

### The numbers

Eight-year backtest, honest version after bias audit:

- ETH continuous basis carry: **Sharpe 6, APR ~22%, MDD ~−2.4%**
- BTC funding arb in the modern era: **Sharpe −0.1, MDD −16%** (post-2024 spot ETF approval killed it; we don't trade BTC funding right now)
- Worst single day in the sample: **COVID March 2020, basis blew out 4.9% in a few hours**

Current live deployment is paper-only. Five thousand dollars notional total, ETH leg only, started a day ago. As of last cycle: $4,994 NAV — basically just the unamortized entry fees, will turn positive in six to twelve hours at current 10.95% APR ETH funding.

### Likely interview questions

**"Why ETH and not BTC?"**
BTC funding died after spot ETFs got approved in early 2024. Institutions could finally get clean spot exposure without using leveraged perps, which drained the retail premium that powered funding. My 8-year backtest shows BTC modern-era Sharpe at minus zero point one with sixteen percent drawdown. ETH didn't have a spot ETF until much later, so the leverage premium survived longer. The 50/50 portfolio is still positive Sharpe because of diversification, but BTC is a near-zero contributor.

**"Why does this work? Why hasn't it been arbed away?"**
It HAS been partially arbed away — that's exactly what the funding rate IS. It's the market's clearing price for "be the short side of the perp." What I'm being paid is the price of leverage that retail demands. As long as retail demand for leverage exceeds the supply of natural shorts, funding stays positive. It compresses over time — we've seen post-ETF compression — but it doesn't flip negative permanently.

**"What's the worst thing that could happen?"**
A basis blowout event. COVID March 2020 had basis move 5% in hours on BitMEX. LUNA collapse and FTX each had 2-3% basis days. On a 5k position that's $250 in a day. On 5 million it's $250k in a day. Mitigations: auto-close if basis exceeds 50bps for an hour, keep margin ratio above 3x, consider tail hedges (OTM puts or vol ETFs) at scale.

**"How do you know your real Sharpe?"**
I trust the post-bias-audit number, which is Sharpe 6 with a 5.3 to 7.3 95% band. Realistic live haircut for execution slippage and operational frictions is 30-40%, so expected live Sharpe is **3 to 5**. APR 11-22% depending on regime. Current ETH funding regime is half the historical median, so near-term expected is at the lower end of that band.

**"What about counterparty risk on Hyperliquid?"**
Real concern. Hyperliquid is a DEX with on-chain reserves you can audit, but it's not an insured US exchange. I'm keeping position size under 50k there until trust is built, and the plan is to diversify into Kraken Pro (regulated, higher fees but lower exchange risk). Spot leg is on Coinbase which is FDIC-insured for the USD side.

**"What if regulators kill US perp access?"**
The strategy degrades to spot-only. I lose the funding carry but I'm not stuck with a derivative I can't unwind. The spot ETH/BTC just sits there at Coinbase. Pause-able indefinitely if needed.

---

## Strategy 4 — K6 spot-displacement (the new Kalshi finding)

### The story I'd tell

"After getting the funding arb into paper trading, I went back to Kalshi with a more systematic frame. The thinking was: funding arb is the primary, slow, scalable strategy. Kalshi can be a parallel sleeve for fast turnover — different time horizon, different capital pocket, doesn't compete with funding arb for capacity. So small-capacity finds still count.

I wrote out fifteen named hypotheses for Kalshi-only edges — pure arbitrage variants, latency plays, calendar effects, microstructure signals, vol-regime filters. Then I tested them one by one. Most died. The within-market YES-plus-NO ask arbitrage had zero hits in two and a half million quotes — market makers eliminate it. The bid-side version had one hit in sixteen million live ticks. The strike-sandwich synthetic was richly priced. Order-flow imbalance had a real but tiny signal — point-seven cents per move, can't clear the three-and-a-half-cent round-trip fee.

But **one hypothesis worked**: when BTC spot moves past a strike fast — fifty to five hundred dollars past — with fifteen minutes or less to close, the favored side of that strike is statistically underpriced by two to five cents. The market maker hasn't repriced fast enough after the move. I'm just paying the gap between where their quote sits and where the conditional probability actually is.

It validated cleanly across eight buckets — four YES-side, four NO-side — all positive. Stacked portfolio: 88% win rate, 4¢ average edge per trade after fees. Then I added a vol-regime filter and it improved to 90% win rate, 4¢ edge, smaller drawdown."

### The math anchor

Not really a model — more of a conditional empirical edge. For each `(time_to_close, spot − strike)` bucket, the trade is:

- Spot is past the strike → BUY YES (or BUY NO if spot is below strike)
- Pay the ask, hold to settlement
- Edge per trade = empirical_win_rate − ask − fee

The K14 vol-regime filter on top: YES-side trades only when realized 15-min vol is mid-to-high (fast moves create staleness), NO-side trades only when vol is low-to-mid (high vol risks spot reversing across the strike).

### The numbers

After all filters applied:

- 1,991 trades over 65 days
- 90% win rate
- +$0.04 avg PnL per trade after fees
- Total: +$77 per contract over 65 days, **about $430 per year per contract**
- Daily Sharpe ~22 in-sample, honest realized expected **5 to 8** after correcting for within-event correlation
- Max drawdown: −$6

Important caveat: only fires on 18 of 65 days (~28%). It needs BTC to be moving. On those active days mean PnL is $4.27, 89% positive days.

### Likely interview questions

**"Why does this work?"**
Latency. When BTC moves $200 in a minute, the conditional probability of "YES" on a strike below the new spot price jumps fast — should be 85-90% range. The Kalshi market maker is busy and doesn't reprice instantly; their ask sits at 75-80% for tens of seconds to minutes. I'm catching the gap during the lag.

**"How do you know it's not curve-fit?"**
Three things. One, eight buckets all positive independently — that's not one lucky overfit. Two, the buckets that LOOK like they should work but don't have an explainable reason — calibration average ask is lower than first-quote-per-market ask, so the edge dissolves at execution. Three, the vol-regime split tells a clean causal story: edge requires fast spot movement to create staleness, so it vanishes in calm regimes. What I don't have yet is out-of-sample on a different time window. Sixty-five days, single regime.

**"What's the realistic live Sharpe?"**
In-sample daily Sharpe is 22. That's overstated because within-event trades correlate — one BTC move triggers 5-10 simultaneous strike signals that share the same underlying bet. Honest de-correlated estimate: **5 to 8**. Still very good, but be ready to defend the haircut.

**"How does this differ from the existing tiers?"**
T2 catches static mispricings — "this contract should be worth more than the market is paying given my vol estimate." K6 catches dynamic mispricings — "spot just moved past this strike and the market hasn't caught up yet." Different mechanism, different trigger condition, different time signature. They cross-block each other so I don't double-bet the same market.

**"What's the deployment plan?"**
Add it as a fourth tier in the existing bot — same infrastructure, same SPRT auto-disable, same Kelly sizing. Maybe two to three hours of wiring. Cap exposure at 20 contracts per signal for the first couple weeks while I watch the live capture rate. Need to do a tick-level persistence check first — my backtest assumes the ask I see is fillable, but real fills depend on whether it sits long enough at my 200-400ms RTT.

---

## How I'd close the interview

"So the four-strategy stack: the Kalshi mispricing model is the original, monotonicity arb is the free-money layer I bolted on after I saw the inconsistencies, funding arb is the scalable primary I pivoted to once I hit the Kalshi capacity ceiling, and K6 is the new mispricing variant I found going back to Kalshi systematically. The funding arb is the real engine — Sharpe 3-5 live, scales to seven figures — and the Kalshi work is a fast-turnover sleeve on top.

The two big lessons from this project: one, **fee structure is destiny** — almost every microstructure or ML idea I tested died at retail fees, and the only survivors are low-turnover carry trades and structural arbs where the fee is dwarfed by the certain outcome. Two, **back-test Sharpe lies**, specifically when there's a data-resolution mismatch hiding intraday variance. Always honest-up the numbers with Monte Carlo before sizing into anything."

---

*Source code paths if asked: Kalshi bot in `strategy_cells/` (notebook build at `kalshi-bot-v2.ipynb`). Funding arb in `funding_arb/paper_runner.py` and `live_runner.py`. K6 backtest in `strategy_zoo/kalshi_k6_v2.py`. The 50-strategy zoo in `strategy_zoo/batch_*.py`. Bias audit in `strategy_zoo/bias_audit.py`.*
