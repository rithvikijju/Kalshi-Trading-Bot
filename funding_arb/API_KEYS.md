# API Keys & Credentials

## Where keys go

```
~/.config/funding_arb/credentials.env
```

Setup:
```bash
mkdir -p ~/.config/funding_arb
chmod 700 ~/.config/funding_arb
touch ~/.config/funding_arb/credentials.env
chmod 600 ~/.config/funding_arb/credentials.env
nano ~/.config/funding_arb/credentials.env
```

## File format

```
# === COINBASE ADVANCED ===
# https://www.coinbase.com/settings/api → New Key
# Scopes: view, trade  (NOT transfer for safety)
# Restrict by your home IP if static
COINBASE_API_KEY=organizations/abc12345-.../apiKeys/...
COINBASE_API_SECRET=-----BEGIN EC PRIVATE KEY-----\nMHc...==\n-----END EC PRIVATE KEY-----

# === HYPERLIQUID ===
# Generated from app: https://app.hyperliquid.xyz/API
# This is a delegated wallet — can trade, withdrawals only to your main wallet
HYPERLIQUID_API_PRIVATE_KEY=0xabcdef...
# Your main wallet address (where funds live; API can't move them elsewhere)
HYPERLIQUID_VAULT_ADDRESS=0xYourMainWallet

# === OPTIONAL: ALERTS ===
PUSHOVER_USER=...           # https://pushover.net/ for phone alerts
PUSHOVER_TOKEN=...
# Or:
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## To run BACKTEST or PAPER mode

**No keys needed.** The notebook uses public APIs:
- Coinbase public endpoint for spot prices (no key)
- Hyperliquid public `/info` endpoint for funding + marks (no key)

You can run the entire notebook end-to-end with zero credentials.
Paper trading uses live market data but simulated execution.

## To run LIVE mode

You need BOTH:
- Coinbase Advanced keys (to buy/sell spot)
- Hyperliquid API wallet (to short perps)

The notebook detects whether credentials are present and offers live
execution only when both are configured.

## Security tips

1. **Never commit credentials.env to git.** Add `.config/funding_arb/` to
   your global `.gitignore`.
2. **Coinbase: use IP restriction.** If you trade from one home computer,
   lock the key to that IP.
3. **Hyperliquid: API wallet is "delegated."** Even if leaked, attacker
   can only trade — they can't withdraw beyond your main wallet's
   approval.
4. **Don't enable `transfer` permission on Coinbase.** The bot only needs
   to buy and sell; it should never need to move USD off-platform.
5. **Phone alerts for: liquidation buffer < 3×, basis > 30 bps.**

## How to get each credential

### Coinbase Advanced API key

1. Log in at https://www.coinbase.com
2. Click your profile → Settings → APIs (or directly:
   https://www.coinbase.com/settings/api)
3. Create New Key:
   - Name: `funding-arb-bot`
   - Permissions: ✓ View, ✓ Trade (NOT transfer)
   - IP whitelist: your home/server IP
4. Save the JSON file Coinbase gives you. Extract:
   - `name` → `COINBASE_API_KEY`
   - `privateKey` → `COINBASE_API_SECRET`
5. The private key includes newlines — keep them as `\n` literal in the
   env file if you escape them, OR use a triple-quoted heredoc when
   loading.

### Hyperliquid API wallet

1. Open https://app.hyperliquid.xyz in your wallet-connected browser
2. Click your address (top right) → API
3. Click "Generate API Wallet"
4. Sign the transaction with your main wallet
5. Hyperliquid will show you a private key — COPY IT NOW (only shown once)
6. Save it as `HYPERLIQUID_API_PRIVATE_KEY`
7. Also save your main wallet address as `HYPERLIQUID_VAULT_ADDRESS`

This API wallet can trade with your funds but cannot withdraw to a new
address — withdrawals always go back to your main wallet first.

### Pushover (for phone alerts)

1. Sign up at https://pushover.net (free for personal use)
2. Install the Pushover app on your phone
3. Get your User Key from the dashboard
4. Create an Application Token for "funding-arb"
5. Save both as `PUSHOVER_USER` and `PUSHOVER_TOKEN`

Test it with `curl`:
```bash
curl -s --form-string "token=$PUSHOVER_TOKEN" \
     --form-string "user=$PUSHOVER_USER" \
     --form-string "message=test" \
     https://api.pushover.net/1/messages.json
```
