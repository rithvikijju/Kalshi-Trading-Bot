# Kalshi BTC Arb Strategy — Live + Paper Trading Bot

Backtested **+$43.76 over 7 days** vs the existing bot's **-$8.04** (using the same 7-day capture).

| Tier | Strategy | Edge | Risk |
|------|----------|------|------|
| 1 | Monotonicity arb — `yes_bid(K_hi) > yes_ask(K_lo)` | avg 14.9¢/pair | **Risk-free** |
| 2 | Deep-ITM convergence — `price ≥ 0.88, fair ≥ 0.94, ttc ∈ [15,60]min` | avg 2¢/contract | 98% win rate |

| § | Section | Purpose |
|---|---------|--------|
| 1 | Config & Auth | Kalshi REST client, credentials, trading params |
| 2 | Live Data | Coinbase spot poller, Kalshi websocket, event tracker, tick logger |
| 3 | Signal Detection | Tier 1 monotonicity scan + Tier 2 deep-ITM scan |
| 4 | Execution | Single + paired order placement, position tracking |
| 5 | Main Loop | Start/stop orchestrator |
| 6 | Controls | Status, diagnostics, kill switch, enable_live |
