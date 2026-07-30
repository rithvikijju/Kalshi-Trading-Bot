# USER_ACTION_REQUIRED — what you need to provide

This is a personal trading framework, so it deliberately doesn't ship with
keys, accounts, or capital. Here's the full list of what to acquire and where.

## Tier 0 — free, no signup (works out of the box)
| You get | How |
|---|---|
| ETF/stock daily bars | `yfinance` already in `requirements.txt` |
| Crypto funding history | Binance public REST (no auth) — works via `BinanceClient` |
| Kalshi market browsing | Public `api.elections.kalshi.com` |
| Polymarket market browsing | Public Gamma + CLOB APIs |

**Strategies runnable at Tier 0:** SPY vol-target backtest, TSMOM backtest, combined portfolio, crypto funding-arb backtest, PM signal research (browsing only).

## Tier 1 — free signups, recommended
| Provider | What it unlocks | Get a key |
|---|---|---|
| **FRED** | CPI / FOMC / Fed funds / yields | https://fred.stlouisfed.org/docs/api/api_key.html → `FRED_API_KEY` |
| **Alpaca paper** | Real paper-trading equities + intraday bars | https://alpaca.markets/ → `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` |
| **Kalshi signed-auth** | Required only for **placing** orders, not for reading | https://kalshi.com/developers → PEM key file at `KALSHI_PRIVATE_KEY_PATH` |

## Tier 2 — paid, only if you need them
| Provider | What it unlocks | Cost |
|---|---|---|
| **Polygon.io** | 1-minute equities/ETF intraday + websockets | $29–$199/mo |
| **Databento** | Futures + options institutional data | usage-based |
| **CCXT pro / exchange direct** | Live crypto orderbook + private auth | exchange-specific (Binance/OKX/Kraken/Hyperliquid) |

## Broker accounts (only when going live)
| | Use | Requires |
|---|---|---|
| **Alpaca live** | US equities/ETFs | account funded with ≥$0 (no minimum) |
| **Interactive Brokers** | Multi-asset incl. futures | $10k account suggested |
| **Hyperliquid** | Crypto perp, low fees, on-chain | EOA wallet + USDC bridge |
| **Kraken / Coinbase / Binance** | Crypto spot + perp | KYC |

## Minimum capital per sleeve
| Sleeve | Min capital | Why |
|---|---|---|
| SPY vol-target paper | $0 | paper broker is in-process |
| SPY vol-target live | $1k | enough to size at 10% vol target with whole shares |
| TSMOM paper | $0 | paper |
| TSMOM live | $25k | 17-ETF universe with minimum-trade sanity |
| Funding arb paper | $0 | simulated funding cashflows |
| Funding arb live | $10k per venue | 1:1 spot/perp; below this fees dominate |
| PM signal research | $0 | research-only by default |

## Recommended deployment order
1. Tier 0 only: NB 00 → 01 → 02 → 04 → 06 → 08 (full backtest pipeline)
2. Add FRED key, re-run NB 01.
3. Add Alpaca paper, run NB 07.
4. Paper trade for **30–60 days**. Compare paper PnL to backtest projection.
5. Only after paper looks healthy: add live broker, set `LIVE_TRADING_ENABLED=true`.
6. Add Polygon if you want intraday vol forecasts (not required for daily strategies).

## Safety warnings
- **Do not** trade real money based on a backtest alone. The 30-day paper-trading gate is non-negotiable.
- **Do not** put more than the per-sleeve capital cap into a single venue (see `RISK_POLICY.md`).
- **Do not** override the drawdown stops without a written reason in the run log.
- **Do not** trade prediction markets directly from this framework yet — they are a signal layer only.
- **Do not** commit your `.env` file.
- **Do not** share your Kalshi PEM or exchange API secrets in notebooks; load them via `KEYS`.

## When in doubt
Re-read **BIAS_AND_VALIDATION.md** before adding any new feature, and
**RISK_POLICY.md** before sizing up.
