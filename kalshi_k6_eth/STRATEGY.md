# K6 — Kalshi spot-displacement strategy

A self-contained paper-and-live trading module for the K6+K14 strategy
validated in `strategy_zoo/kalshi_k6_v2.py`.

## The trade

When BTC spot moves $50-$500 past a Kalshi strike with ≤15 minutes until the
hourly event closes, the favored side of that strike is statistically
underpriced by ~2-5¢. We buy the favored side at the market ask, hold to
settlement, collect ≈$0.04/contract average after fees.

Validated backtest numbers (65-day window):
- **88-90% win rate** depending on which buckets / regime filter
- **+$0.039 average PnL per trade** after Kalshi fees
- **45 trades per active day** (~28% of calendar days are active)
- **~$430/year per contract** at 1-contract sizing

## Side-aware vol filter (K14)

Same vol regime affects YES and NO sides asymmetrically:

- **YES side** (spot above strike): only fires when `rv_15m_annual >= 0.33`.
  Fast moves create the staleness we exploit; in calm regimes MMs reprice fast
  enough that no edge survives.
- **NO side** (spot below strike): only fires when `rv_15m_annual < 0.65`.
  High vol risks spot vol-bursting back across the strike and killing the NO
  bet.

## Module layout

```
kalshi_k6/
  config.py          — config dict, Kalshi REST client, fee math
  spot.py            — Coinbase BTC poller + EWMA vol estimator
  market_feed.py     — Kalshi event/market REST polling (background thread)
  strategy.py        — K6 signal detection logic (pure)
  portfolio.py       — position tracking + PnL accounting + state persistence
  paper_runner.py    — paper trading loop (no real orders)
  live_runner.py     — live trading loop (real orders, requires creds + flag)
  data/
    paper_log.jsonl  — every cycle's events
    paper_state.json — current portfolio snapshot (atomically written)
    live_log.jsonl   — live cycles
    live_state.json  — live portfolio snapshot
```

## Running it

### Paper mode (no money at risk)

```bash
cd kalshi_k6
python paper_runner.py
# or with custom bankroll:
python paper_runner.py --bankroll 100
# or fixed cycle count:
python paper_runner.py --max-cycles 100
# wipe state and restart:
python paper_runner.py --reset
```

Output goes to stdout. State written to `data/paper_state.json` every 15
seconds. Full per-cycle JSONL at `data/paper_log.jsonl`.

### Live mode (real money)

```bash
# 1. Set up creds at ~/.kalshi/credentials.env:
#    KALSHI_PROD_KEY_ID=...
#    KALSHI_PROD_PRIVATE_KEY_PATH=~/.kalshi/private_key.pem

# 2. Run with explicit bankroll cap and the safety flag:
cd kalshi_k6
python live_runner.py --bankroll 100 --i-know-what-im-doing
```

The live runner:
- Refuses to start without `--i-know-what-im-doing`
- Refuses to start with bankroll > $500 (edit code to raise this guard)
- Verifies your Kalshi balance ≥ requested bankroll before starting
- Places limit-buy orders at exactly the observed ask
- Reconciles open positions against Kalshi `/portfolio/positions` every 5 cycles

## Sizing math (quarter-Kelly)

At each signal:
```
fee   = ceil(0.07 × p × (1−p) × 100) / 100      # exact Kalshi fee
win   = 1 − ask − fee                            # payoff if we win
loss  = ask + fee                                # cost if we lose
b     = win / loss
p     = 0.88                                     # historical win rate
q     = 1 − p
f*    = (p·b − q) / b                            # full Kelly
qty   = floor(min(cash, $15) × f* × 0.25 / ask)  # quarter Kelly, capped
```

Then qty is further clamped by:
- `max_qty_per_signal = 20` contracts hard cap
- Available book depth at the ask
- `max_total_exposure = $80` portfolio-wide
- `max_concurrent_positions = 10`

## Kill switches

- **`daily_loss_limit = -$10`** → portfolio.halted = True for the rest of the
  UTC day. Resets at UTC midnight rollover.
- **Refuses to open** when `cash < cost` (won't go cash-negative).
- **Order rejection** logged but doesn't halt the bot — we keep trying.

## $100 → $X math (paper expected)

```
avg edge per trade   = +$0.04 (after fees)
avg cost per trade   = $0.85 (mostly 0.80-0.95 ask range)
% return per trade   ≈ 4.7% on capital deployed
trades per active day≈ 45
active days          ≈ 28% of calendar days

At 20-contract sizing on $100 bankroll:
  position cost  = 20 × $0.85 = $17 per signal
  PnL per signal = 20 × $0.04 = $0.80
  active-day PnL = 45 × $0.80 = $36/day on active days
  monthly PnL    ≈ 28% × 30 × $36 = $302
  $100 → ~$400 after 30 days (≈ 4× annualized)
```

This is the **paper-mode expectation under backtested edge**. Realistic live:
- Tick-level fill rate may be 50-70% of backtest assumption (some asks evaporate
  before our REST poll sees them)
- Execution slippage adds 0.5-1¢/trade on average
- Honest expected: $100 → $250-$350 over 30 days

## Risks

- **Single-side fill at settlement time.** If we open at 14:55 and the event
  closes at 15:00, the position is open for 5 minutes and BTC could move
  meaningfully against us. The win rate accounts for this empirically.
- **Sample regime.** All backtest data is from Feb-May 2026. Different vol
  regimes may invalidate the K14 thresholds. SPRT auto-disable would catch
  this — but it's not yet integrated (TODO).
- **Kalshi API outages.** Polling-based; if API is down, we miss signals.
- **REST latency.** We poll every 3 seconds; sub-second persistence finds may
  evaporate. The strategy's edge is in 5-15min staleness, so 3s polling is OK
  but tighter would be better.

## Wire-up status

- [x] config + Kalshi client
- [x] Spot feed + EWMA vol
- [x] Market feed (REST polling)
- [x] K6+K14 signal scanner
- [x] Portfolio + PnL tracker
- [x] Paper runner
- [x] Live runner with safety flags
- [ ] **SPRT auto-disable** — track win-rate vs target, halt on degradation
- [ ] **Tick-level persistence check** — confirm asks sit long enough at 200-400ms RTT
- [ ] **Websocket upgrade** — replace REST polling with the existing bot's WS infra
- [ ] **Backtest replay mode** — feed historical data into the runner for end-to-end validation

## Comparison vs the existing strategy_cells/ bot

The existing bot (`strategy_cells/`, notebook at `kalshi-bot-v2.ipynb`) runs
T1 monotonicity, T2 deep-ITM, T3 OTM persistence. This module is **K6 alone**
in a standalone process. Eventually K6 should be integrated as a 4th tier in
the main bot to share infrastructure — until then, this is a clean standalone
deploy that doesn't risk regressing the existing tiers.
