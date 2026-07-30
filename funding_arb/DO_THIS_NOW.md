# DO THIS NOW — concrete next steps

Strategy validated: **ETH continuous basis carry** (long ETH spot + short ETH perp,
collect funding). 4-yr Sharpe 14, OOS Sharpe 16, every year +ve 2019-2025.

Right now ETH funding on Hyperliquid is **+0.125 bps/hr ≈ +10.9% APR**.
Lower than historical avg (which was +22% APR), but still positive, still tradeable.

---

## TONIGHT (5 minutes) — no accounts, no risk

Just run the paper simulator. It pulls live data from public APIs, no auth needed,
no money at risk. Lets you see the strategy "alive" without committing anything.

```bash
cd /Users/rithvikijju/edge-bot/funding_arb
python paper_runner.py --notional 5000 --assets ETH --interval-sec 600
```

Leave it running in a terminal window (or under `nohup` / `screen`). Every 10 min
it pulls the current funding rate, simulates what your position would be doing,
and appends a line to `data/paper_log.jsonl`.

**Tomorrow morning**, check what it captured:
```bash
tail -20 data/paper_log.jsonl | jq .
# OR for total simulated P&L so far:
cat data/paper_state.json
```

Expected: ~$0.06 of simulated funding income per day per $5k notional at current
~11% APR funding. Tiny in absolute terms, but it's the strategy WORKING.

---

## THIS WEEK (1-2 hours of clicking, ~3 days of waiting on ACH)

Open the accounts. You only need two:

### 1. Coinbase Advanced (US spot ETH)
- https://www.coinbase.com → Sign up → verify ID
- Link a US bank account → wait 1-3 days for ACH approval
- Go to https://www.coinbase.com/advanced-trade (NOT the consumer Coinbase app —
  fees are 30× higher on the consumer app)
- Confirm you can see the ETH-USD order book

### 2. Hyperliquid (ETH perp short)
- Install Rabby wallet or MetaMask (browser extension): https://rabby.io
- Back up your seed phrase to PAPER, never cloud
- Get ~$10 of ETH on Arbitrum L2 for gas (any centralized exchange withdrawal)
- Go to https://app.hyperliquid.xyz → connect wallet → deposit USDC
- Bridge USDC from Arbitrum to Hyperliquid in the app (~30 sec, ~$0.10 fee)

**That's it.** No KYC on Hyperliquid (it's a DEX). Coinbase handles the regulated
side. You don't need API keys yet — manual trading is fine until live trades are
working consistently.

---

## NEXT WEEK (15 minutes once accounts are funded)

Move $5k total into the system:
- $2,500 to Coinbase → buy $2,500 of ETH-USD market order
- $2,500 to Hyperliquid → leave as USDC for now

That's your "ready to trade" capital. Don't open the perp short yet.

---

## WEEK 3 (the real start)

Open the actual arb position **manually**, both legs within ~60 seconds:

1. Coinbase: buy $2,500 ETH-USD (you already did this in week 2 — done)
2. Hyperliquid: open SHORT ~$2,500 of ETH-PERP, market order, 1× leverage

Now you're delta-neutral. Every hour, Hyperliquid pays you funding (when funding
rate is positive). Walk away. Check once a day.

**Daily check (1 minute):**
- Open Hyperliquid app → verify the short position is still open
- Verify margin ratio > 3× (safe buffer)
- Look at the accrued funding payment count

**Weekly check (5 minutes):**
- Add up the funding payments — that's your gross return
- Subtract Coinbase + Hyperliquid fees (paid once at open)
- That's your real P&L

---

## EXIT TRIGGERS — close the trade if any of these happen

| Trigger | What to do |
|---|---|
| Funding rate flips negative for >24h | Close both legs, wait for it to turn positive again |
| Basis (perp/spot - 1) blows out > 0.5% | Close both legs immediately — perp could continue diverging |
| Hyperliquid margin ratio drops below 2× | Add USDC margin OR close perp partially |
| You don't trust the position | Just close it. Carry strategies should never feel risky. |

---

## WHEN TO AUTOMATE (after 4+ weeks of clean manual trading)

Once you've manually opened/closed at least one full cycle and feel confident:
1. Create API keys on Coinbase Advanced (`view + trade` only, NEVER `transfer`)
2. Create an API wallet on Hyperliquid (Settings → API)
3. Drop them into `~/.config/funding_arb/credentials.env` (see `API_KEYS.md`)
4. Run: `python live_runner.py --live --notional 1250 --assets ETH`
   - Defaults to PAPER mode unless `--live` is passed
   - Confirms each trade interactively the first few times
   - Has safety gates: max notional $50k, refuses to enter if basis is rich,
     auto-exits on basis blowout

---

## CAPITAL SCALING

| Phase | Capital | Expected yearly return | Time/week |
|---|---|---|---|
| Now (paper) | $0 | $0 (learning) | ~30 min total this week |
| Month 1 | $5k | ~$550 (at current 11% APR) | 5 min/day |
| Month 3 | $25k | ~$2,750 | 5 min/day |
| Month 6 | $50k | ~$5,500 | 10 min/day |
| Year 1 | $100k | ~$11,000-23,000 (depends on regime) | 10 min/day |
| Year 2+ | $250k-1M | $27k-230k | 30 min/day |

These are at the **current low-funding regime (11% APR)**. Historical avg is
**22% APR**, which would roughly double these numbers. The strategy gets MORE
profitable when shorts get crushed (bear regimes — 48% APR in stress test).

---

## TL;DR — TONIGHT

```bash
cd /Users/rithvikijju/edge-bot/funding_arb
python paper_runner.py --notional 5000 --assets ETH --interval-sec 600
```

Let it run. Check `data/paper_log.jsonl` tomorrow. While it runs in the background,
go open the Coinbase and Hyperliquid accounts so you're ready next week.
