## Run Book

### Paper trading (default)
1. Run every code cell above (§1 → §6)
2. `start_bot()` — fires up websocket, polls spot, scans signals, places paper trades
3. `status()` — quick snapshot (signals, positions, PnL)
4. `diagnostics()` — full detail (threads, sigma, recent log)
5. `stop_bot()` — when done

### Live trading
1. Verify with paper for ≥1 hour first
2. `enable_live()` — flips mode + sanity-checks balance
3. `status()` — monitor
4. `kill_switch()` — instant stop (cancels all resting orders)

### Data collection
The bot logs every websocket tick to `output/arb_strategy_ticks.db`. After collecting a
few weeks of data, recalibrate Tier 2 thresholds against fresh ticks.

### How the strategy works

**Tier 1** is mathematically risk-free. When the books show `yes_bid(K_hi) > yes_ask(K_lo)`,
buy YES at the low strike and (equivalently) sell YES at the high = buy NO at the high.
For ANY settlement outcome the net is at least `bid_hi - ask_lo - 2×fees`, which is positive
by construction.

**Tier 2** is statistical. Buy deep-ITM contracts where Black-Scholes binary fair value
exceeds the market price by enough to overcome Kalshi's 7% fee. Strict filters
(`price ≥ 0.88, fair ≥ 0.94, TTC ∈ [15min, 60min]`) ensure positive expected value.
