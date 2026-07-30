# API Keys & Deployment Setup

## 0. Where keys live

The notebook reads credentials from:
```
~/.config/underlying_arb/credentials.env
```

Format (one per line, `KEY=value`, comments start with `#`):
```
# Data
POLYGON_API_KEY=...
ALPHA_VANTAGE_KEY=...

# Brokers (pick one)
TRADIER_TOKEN=...
TRADIER_ACCOUNT_ID=VA12345678
# OR
TASTYTRADE_USERNAME=...
TASTYTRADE_PASSWORD=...
# OR
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
```

Create the file:
```bash
mkdir -p ~/.config/underlying_arb
chmod 700 ~/.config/underlying_arb
touch ~/.config/underlying_arb/credentials.env
chmod 600 ~/.config/underlying_arb/credentials.env
nano ~/.config/underlying_arb/credentials.env
```

## 1. To run BACKTEST only

**Required:** nothing. yfinance works without keys.
```bash
pip install yfinance pandas numpy duckdb matplotlib
jupyter notebook underlying_arb_live.ipynb
```

The backtest uses 16 years of SPY + VIX history. No API keys needed.

## 2. To run PAPER TRADING (recommended for first month)

**Required:** one data source for real-time options chain.

### Option A: Polygon.io (cheapest paid option)
- **Sign up:** https://polygon.io
- **Free tier:** 5 calls/min, 15-min delayed data, all US options
- **Paid:** "Options Starter" $79/mo for real-time, or "Options Advanced"
  $199/mo for real-time + Greeks
- For our daily-cadence strategy, free tier is sufficient
- Add to credentials: `POLYGON_API_KEY=your_key_here`

### Option B: Tradier sandbox (free + paper trading built in)
- **Sign up:** https://tradier.com/products/brokerage → sandbox account
- **Free tier:** delayed options data, paper account with simulated fills
- Add to credentials: `TRADIER_TOKEN=your_sandbox_token`
- Bonus: you can ALSO test order submission against their sandbox

### Option C: TastyTrade (also free for paper)
- **Sign up:** https://developer.tastytrade.com/
- Real-time data when you have a funded account
- Best Python library: https://github.com/tastyware/tastytrade

## 3. To go LIVE

**Required:** brokerage account + data source.

### Brokerage options ranked

| Broker | Min deposit | Commish | Margin | Options data | Best for |
|--------|-------------|---------|--------|--------------|----------|
| **Tradier** | $0 | $0.35/contract | competitive | included | **Smallest accounts** ($5K+) |
| **TastyTrade** | $0 | $1.00 open / $0 close | competitive | included | **Mid-size** ($25K+) |
| **IBKR Pro** | $0 (was $10K) | $0.15-0.65/contract | best | $1.50/mo SPX | **$100K+, deep margin** |
| **Schwab** (TOS) | $0 | $0.65/contract | reasonable | included | TOS platform fans |

**For this strategy specifically:** TastyTrade or Tradier. They both have:
- Strong multi-leg order types (you'll be selling straddles)
- Good defined-risk options margin (no Reg-T penalty if you size right)
- Python APIs you can automate against

### Capital + margin needs

A SPY straddle has undefined risk (technically infinite on the put side
because there's no upside cap on SPY price). Brokers require:
- **Reg-T cash account:** can't sell naked options — won't work for this
- **Reg-T margin:** ~20-25% of underlying value per contract × 100
- **Portfolio margin** (PM): typically 10-15%, requires $100K+ NAV

For $100K capital running the strategy as backtested:
- ~$25K vega notional per trade
- That's ~3 SPY straddles when SPY = $740
- Reg-T margin requirement: ~$45K per cycle (3 × $740 × 100 × 20%)
- → you need a $100K account in Reg-T margin to run 3 contracts safely

Smaller? Cap the strategy to 1 contract until you have $50K+.

## 4. The actual deployment ladder

### Week 0: paper account
- Open Tradier sandbox account
- Run notebook daily, log every signal to a CSV
- Compare logged "would-have-traded" PnL to backtest expectation

### Week 1-2: small live ($5-10K)
- Open small funded account
- Trade 1 contract at a time
- Each trade: enter on signal day, hold to expiry, no early exits
- Verify fills match the notebook's "recommended size" output

### Month 1-3: scale to $25-50K
- Add Greek monitoring (vega, theta, gamma daily)
- Add the **delta-hedge** loop: every 2-3 days, check position delta and
  trade SPY shares to neutralize
- Move from "buy-and-hold to expiry" → "active hedge"

### Month 3+: $100K+
- Portfolio margin upgrade
- Multi-position (overlapping straddles, different expiries)
- Add VRP signal to filter by IV term structure

## 5. Specific Python libraries to install for live

```bash
pip install yfinance pandas numpy duckdb matplotlib jupyter
# data
pip install polygon-api-client     # if using Polygon
pip install tradier-python         # if using Tradier
pip install tastytrade             # if using TastyTrade
pip install ib_insync              # if using IBKR
# for real-time iv calculations
pip install py_vollib              # Black-Scholes greeks, IV calc
pip install QuantLib-Python        # advanced options pricing
```

## 6. Costs at each tier

| Setup | Monthly cost | Capacity |
|-------|--------------|----------|
| Backtest only | $0 | n/a |
| Paper (Polygon free) | $0 | n/a |
| Live small (Tradier + free Polygon) | ~$10-30 trading fees | $5-25K |
| Live mid ($25K+, TastyTrade + Polygon Options Starter $79/mo) | ~$100-200/mo | $25-100K |
| Live large (IBKR + Polygon Advanced $199/mo) | ~$300-500/mo | $100K-10M |
| Production fund (FIX feed + colocation) | $5-10K/mo | $10M+ |

## 7. What can go wrong (read before going live)

1. **Volatility regime change**: Feb 2018 (XIV blowup), March 2020 (COVID),
   Aug 2024 (yen carry unwind). Strategy lost 5-30% in these. The -5% stop
   loss helps but doesn't fully protect against gap moves overnight.
2. **Pin risk at expiry**: if SPY closes within $0.01 of your strike at
   expiry, settlement assignment is uncertain. Close positions ≥2 days
   before expiry to avoid this.
3. **Margin call**: a large overnight gap can take you below maintenance
   margin. Always keep ≥50% cushion above broker's minimum.
4. **Liquidity in stress**: SPY options bid-ask widens 5-10× during crashes.
   You may exit at much worse prices than backtest assumes.
5. **Tax**: short options held <60 days are short-term capital gains
   (ordinary income rate). Consult a tax pro before scaling.

## 8. Monitoring

Once live, monitor daily:
- **Position MTM vs entry**: alert if any position down >3%
- **Portfolio delta**: should be near zero after hedging
- **Portfolio vega**: should be negative (we're short vol)
- **VIX**: if it spikes >30 mid-trade, manually evaluate closing
- **Realized vol**: if RV starts approaching IV, your edge is gone for that cycle

Set up alerts via simple cron job that pipes to your phone (Pushover,
Telegram bot, etc.). The notebook's `daily_run()` cell is your starting
point — wrap it in a scheduler.
