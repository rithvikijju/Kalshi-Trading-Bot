# Prediction-Arb Setup Guide

End-to-end setup for the Kalshi ↔ Polymarket cross-venue arbitrage engine.

## What you have right now

```
prediction-arb/
├── clients/      Kalshi + Polymarket async clients
├── core/         Matcher, arb detector, fee calc, exec engine, portfolio, risk
├── data/         Pydantic models + cache directories
├── notebooks/    01 explore, 02 match, 03 paper trade, 04 go-live
├── utils/        Logging + retry/rate limit
├── live/         run_bot.py for production
├── config.yaml   All tunable parameters
├── .env.example  Credentials template
└── requirements.txt
```

## Step 1 — Install dependencies (one-time)

```bash
cd prediction-arb
python3 -m pip install --break-system-packages -r requirements.txt
```

Required minimum: `httpx[http2]`, `pydantic`, `pandas`, `rapidfuzz`, `pyyaml`,
`rich`, `cryptography`, `jupyter`, `ipywidgets`, `plotly`. All in requirements.txt.

Optional for semantic matching: `pip install sentence-transformers` (~500MB
download).

Optional for live Polymarket: `pip install py-clob-client web3 eth-account`.

## Step 2 — Configure Kalshi credentials

You have RSA keys for Kalshi. Set them up:

```bash
# 1. Copy your private key somewhere safe (chmod 600!)
cp ~/path/to/kalshi_private_key.pem ~/.kalshi/private_key.pem
chmod 600 ~/.kalshi/private_key.pem

# 2. Copy the env template
cp .env.example .env
chmod 600 .env

# 3. Edit .env with your real values
nano .env
```

In `.env`:
```
KALSHI_API_KEY_ID=your-actual-key-id-from-kalshi
KALSHI_PRIVATE_KEY_PATH=/Users/rithvikijju/.kalshi/private_key.pem
KALSHI_ENV=production
```

Load `.env` before running notebooks/scripts:
```bash
set -a; source .env; set +a
```

Or use `python-dotenv` — the clients will pick them up automatically when
loaded via `os.environ`.

## Step 3 — Verify both APIs work

```bash
python3 -c "
import asyncio, sys; sys.path.insert(0,'.')
from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient
async def t():
    k = KalshiClient(); p = PolymarketClient()
    ks = await k.get_markets(limit=3)
    ps = await p.get_markets(limit=3)
    print('Kalshi:', len(ks), 'markets'); print('  ', ks[0].title[:60])
    print('Poly:  ', len(ps), 'markets'); print('  ', ps[0].title[:60])
    await k.close(); await p.close()
asyncio.run(t())"
```

Expected: 3 markets from each, no auth errors.

If Kalshi auth fails: check key path and KEY_ID. The client logs
`authed=True/False` at init — should say True.

## Step 4 — Run the notebooks

```bash
cd prediction-arb
jupyter notebook
```

In Jupyter, open `notebooks/01_market_exploration.ipynb` first:
- Verifies both clients connect
- Shows category distribution
- Side-by-side market samples

Then `notebooks/02_matching_analysis.ipynb`:
- Runs the matcher
- Shows candidate pairs
- Mark verified/rejected in the SQLite cache

Then **`notebooks/03_paper_trading.ipynb`** — this is the main one:
- Live scanner loop with color-coded table
- Auto-executes arbs in paper mode
- Portfolio dashboard with running PnL
- Analytics

## Step 5 — Run for 1-2 weeks in paper mode

The paper engine:
- Uses LIVE prices from both APIs
- Simulates fills (25% slippage assumed)
- Records every "trade" in `data/paper_trades.db`
- Updates portfolio + cash automatically

Check daily:
- How many arbs detected vs how many "filled"?
- What's average edge after simulated slippage?
- Are exit triggers firing correctly?

## Step 6 — Going live

When paper trading shows consistent positive net edge:

1. **Get Polymarket access**
   - Create a Polygon wallet (MetaMask / Rabby)
   - Bridge USDC to Polygon
   - For US users: only Polymarket's US beta (CFTC-licensed, invite-only)
     is technically legal — the global platform restricts US IPs

2. **Add Polymarket credentials to .env**
   ```
   POLYMARKET_PRIVATE_KEY=0x...
   POLYMARKET_FUNDER_ADDRESS=0x...
   ```

3. **Install live deps**
   ```bash
   pip install py-clob-client web3 eth-account
   ```

4. **Run notebook 04 (go-live checklist)** — all checks must pass

5. **Edit `config.yaml`** → `mode: live`

6. **Start the bot**
   ```bash
   cd prediction-arb
   python3 -m live.run_bot
   ```

   First run with TINY position sizes (set `max_position_per_market_usd: 50`
   in config) for 24-48h. Then scale up.

## Step 7 — Low-latency tuning

The code is already optimized for low latency:
- `httpx.AsyncClient` with HTTP/2 + keep-alive + connection pooling
- All API calls run via `asyncio.gather` for parallelism
- In-memory price cache (no redundant fetches per cycle)
- Rate limiter per client (Kalshi 18/sec reads, Poly 10/sec)

To go further:
- **WebSockets**: replace polling with WS subscriptions
  - Kalshi: `wss://api.elections.kalshi.com/trade-api/ws/v2`
  - Polymarket: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
  - Cuts latency from ~30s polling to <1s push
- **Pre-resolve token_ids**: cache poly market metadata so price lookups
  don't hit `/markets` first
- **Colocate**: deploy on AWS us-east-1 (closest to most exchanges)
- **HTTP/3** via httpx when stable

## Step 8 — Operational checklist

Daily:
- [ ] Check kill switch hasn't tripped
- [ ] Reconcile open arb pairs vs broker positions
- [ ] Review skip reasons (most common = low confidence?)

Weekly:
- [ ] Backup `data/cache/matches_cache.db` and `data/paper_trades.db`
- [ ] Mark new manual matches as verified
- [ ] Tax: every settled trade is income (1099 in US)

Monthly:
- [ ] Review fee assumptions vs actual fills (Coinbase/Kalshi may update)
- [ ] Resize position limits as capital grows
- [ ] Audit match cache for stale entries

## Architecture notes / extension points

### Adding a new venue (e.g., Manifold Markets)
1. Create `clients/manifold_client.py` inheriting `PredictionMarketClient`
2. Implement the 7 abstract methods
3. Add normalizer to your market normalization step
4. Extend the matcher to handle N-way matches (currently 2-way)
5. Update arb detector to look across N legs

### Enabling semantic matching
1. `pip install sentence-transformers`
2. Set `matching.use_semantic_matching: true` in config.yaml
3. First match call will lazy-load `all-MiniLM-L6-v2` (~80MB)
4. Improves recall on differently-worded markets (e.g., "Fed cuts" vs
   "rate decrease")

### WebSocket streaming (lower latency)
1. Add a `_ws_listener` thread in each client like the Kalshi bot already has
2. Maintain in-memory book state per market
3. Replace the polling loop in `live/run_bot.py` with event-driven scan

### Risk extensions
- Add Kelly sizing in `risk_manager.py` (formula: f = edge / variance)
- Add per-category exposure limits
- Add correlation penalties (multiple Fed-related arbs are not independent)

## Troubleshooting

**Kalshi auth fails**: Check key path is absolute, key file has correct
permissions, KEY_ID matches the one shown in Kalshi dashboard.

**Polymarket returns empty**: Their Gamma API rate-limits aggressively.
Slow down (`rate_limit_reads_per_sec: 5` in client init).

**0 matched pairs**: Your Kalshi sample may be all sports props. Use
`get_markets_by_series([...])` not `get_markets()`. See KALSHI_SERIES in
notebooks for the right list.

**Notebook 03 hangs**: The scanner cell has `RUN_CYCLES = 60`. Reduce or
interrupt the kernel.

**SQLite locked**: Don't run the live bot and the notebook simultaneously
against the same `paper_trades.db`. Use different paths or stop one.

**Polymarket trade fails**: Polymarket trading requires `py-clob-client`
which we DON'T install by default. Add it when going live.

## Files reference

| Path | Purpose |
|------|---------|
| `config.yaml` | All knobs in one place |
| `.env` | Secrets (gitignored!) |
| `clients/kalshi_client.py` | RSA-PSS-signed REST client + WS hook |
| `clients/polymarket_client.py` | Gamma read + CLOB public + auth stubs |
| `core/market_matcher.py` | Fuzzy + semantic + manual + SQLite cache |
| `core/arb_detector.py` | Spread analysis with book walk |
| `core/fee_calculator.py` | Per-platform fee tables |
| `core/execution_engine.py` | Paper sim + live exec stub |
| `core/portfolio.py` | SQLite trade log + arb pair tracking |
| `core/risk_manager.py` | Hard limits + kill switch |
| `live/run_bot.py` | Production entrypoint |
| `notebooks/03_paper_trading.ipynb` | **Main interactive notebook** |
