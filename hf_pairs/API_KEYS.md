# HF Pairs Engine — API Keys & Deployment

## Where keys live

```
~/.config/hf_pairs/credentials.env
```

Format (one per line):
```
COINBASE_API_KEY=...
COINBASE_SECRET=...
KRAKEN_API_KEY=...
KRAKEN_SECRET=...
```

Setup:
```bash
mkdir -p ~/.config/hf_pairs
chmod 700 ~/.config/hf_pairs
nano ~/.config/hf_pairs/credentials.env
chmod 600 ~/.config/hf_pairs/credentials.env
```

## To run backtest only

Nothing needed. Just:
```bash
python3 hf_pairs/fetch_data.py 6      # one-time, gets 6 months of 1-min bars
jupyter notebook hf_pairs/hf_pairs_engine.ipynb
```

## To run paper / live (after reading the verdict)

**Honest disclaimer up front:** the rigorous backtest in §6b of the notebook
shows BTC/ETH at 1-min on Coinbase is NOT a deployable strategy at retail
fees. The signal is real but too small to overcome cost. Don't put real
money on this exact configuration. Use the engine as a TEMPLATE for other
pair/timeframe/venue combinations that DO clear the cost hurdle.

If you want to do paper trading anyway (for execution-quality testing,
not because it's profitable):

### Option A: Coinbase Advanced (US-friendly, you already have an account)

1. Get API keys: https://www.coinbase.com/settings/api → New Key
   - Scopes: `view` + `trade` (NOT `transfer`)
   - Restrict by IP
2. Add to `credentials.env`:
   ```
   COINBASE_API_KEY=organizations/abc-.../apiKeys/...
   COINBASE_SECRET=-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----
   ```
3. Coinbase Advanced uses CDP-style keys; ccxt v4+ handles them automatically.
4. Maker fee is 0%, taker 0.05% (improves at $10K+ monthly volume).

### Option B: Kraken Pro (lower fees, US-friendly)

1. Get keys: https://www.kraken.com/u/security/api → Generate New Key
   - Permissions: Query Funds, Modify Orders, Cancel Orders (NOT Deposit/Withdraw)
2. Add:
   ```
   KRAKEN_API_KEY=...
   KRAKEN_SECRET=...
   ```
3. Replace `coinbase` with `kraken` in §3's `cb = ccxt.coinbase(...)` line and
   the data fetcher.
4. Maker 0%, taker 0.10% (worse than Coinbase for taker but better borrow rates).

### Option C: Hyperliquid (DEX, cheapest fees)

1. Different setup — uses on-chain wallet, not API key
2. Best fee structure: 0.025% taker, -0.001% maker (REBATE for makers)
3. Won't help BTC/ETH pairs much because they trade as perps not spot
4. Useful for perpetual basis trading instead

## Cost-tier cheat sheet (round-trip on $5K position)

| Venue | Maker | Taker | Slip | RT Cost (taker) | RT Cost (maker) |
|-------|-------|-------|------|-----------------|------------------|
| Coinbase Advanced (0 vol) | 0% | 0.05% | 0.02% | 28bp | 8bp |
| Coinbase Advanced ($10M vol) | 0% | 0.04% | 0.02% | 24bp | 8bp |
| Kraken Pro | 0% | 0.10% | 0.02% | 48bp | 8bp |
| Hyperliquid (perps) | -0.001% | 0.025% | 0.02% | 18bp | 7.6bp |

You need round-trip cost < typical-per-trade-gross-profit for the strategy to
net positive. For BTC/ETH 1-min that's ~8 bps gross, so even the BEST maker
execution is just at breakeven.

## What this notebook is actually useful for

1. **Studying execution quality.** Compare live fills against the simulated
   "fill at next bar's open" assumption. If your fills are consistently
   worse than the backtest assumes, you have a real execution problem.

2. **Finding venues / timeframes that DO work.** Plug different exchanges
   into §3 and different timeframes into §7b. The engine machinery is
   correct — only the BTC/ETH 1-min instantiation doesn't have edge.

3. **Adapting to other pairs.** Replace `BTC/USD` and `ETH/USD` with any
   cointegrated pair. Crypto candidates: ETH/SOL, BTC/SOL, BTC/AVAX.
   Equity candidates (need different fetcher): KO/PEP, MA/V, GS/MS.

4. **As a learning artifact.** This is what a real, honest backtest looks
   like — including the case where the answer is "nope, don't deploy."

## Cron deployment (when you have a viable config)

```bash
# /etc/crontab — run signal every 5 minutes
*/5 * * * * /usr/bin/python3 /path/to/edge-bot/hf_pairs/run_live.py >> /var/log/hf_pairs.log 2>&1
```

`run_live.py` would be a thin wrapper:
```python
import sys; sys.path.insert(0, 'hf_pairs')
from notebook_runtime import daily_run  # extract daily_run() into a module
daily_run()
```

(For now, manually run §9's `daily_run()` cell in Jupyter.)
