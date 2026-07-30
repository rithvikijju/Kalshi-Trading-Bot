# Top 10 Scalable Strategies — Design Doc

Designed 2026-05-15. Criteria: (a) plausible residual edge, (b) backtestable
with public data, (c) >$10M scalable capacity, (d) deployable within 30 days.

## Crypto (4)

### S1. Funding Rate Arbitrage (basis trade) — HIGHEST CONVICTION
- **Mechanism:** Short perpetual + long spot. Capture funding payments.
- **Why edge exists:** Leveraged longs persistently overpay because retail
  demand for leverage exceeds short interest. Funding can't go arbitrarily
  negative because shorts would close. Real, persistent, decades-likely.
- **Data:** Binance public funding API + spot/perp klines, ~2019-present
- **Capacity:** $10-100M/coin. BTC alone supports $50M+ at moderate slippage.
- **Sharpe expected:** 1.5-3.0
- **Friction:** ~3-5 bps per leg per rebalance + occasional liquidation risk

### S2. Cross-Exchange Cash-and-Carry
- **Mechanism:** Long spot on Coinbase + short quarterly future on Binance
  when futures trade at premium. Lock in basis.
- **Edge:** Same fundamental driver as S1 (leverage demand) but quarterly
  rather than perpetual.
- **Data:** Binance quarterly futures + spot
- **Capacity:** $5-20M/coin
- **Sharpe expected:** 1-2 (lower than S1 but lower vol)

### S3. Stablecoin Yield Aggregation (depeg arbitrage variant)
- **Mechanism:** When a stablecoin trades below $1 (e.g., USDC at $0.998),
  buy on DEX, redeem at issuer. Captures depeg.
- **Edge:** Real but episodic. SVB crisis 2023 had USDC at $0.88.
- **Data:** Historical USDC/USDT/DAI prices
- **Capacity:** $10-50M per event but events are rare
- **Sharpe expected:** Episodic but high (5-10 during events)

### S4. Cross-Coin Momentum
- **Mechanism:** Long top-3 cryptos by trailing 1mo return, short bottom-3.
  Weekly rebalance.
- **Edge:** Crypto markets show stronger momentum than equities (less
  efficient, more retail).
- **Data:** Top 20 coins by cap, 2018-present
- **Capacity:** $10-30M (depth-limited on smaller coins)
- **Sharpe expected:** 0.7-1.2

## Equity (4)

### S5. Sector Momentum (cross-sectional)
- **Mechanism:** 11 SPDR sector ETFs. Long top-3 by 12-1 month return,
  short bottom-3. Monthly rebalance.
- **Edge:** Time-series momentum is a robust academic anomaly. Sectors are
  large enough to avoid stock-specific noise.
- **Data:** yfinance, 2007-present
- **Capacity:** $500M+ (sector ETFs are very liquid)
- **Sharpe expected:** 0.5-0.8 (modest, but very scalable)

### S6. Volatility Risk Premium (VIX > realized)
- **Mechanism:** Approximate VRP via VIX minus realized 30d vol. When
  VRP > 5 (vol-points), short VIX exposure (e.g., long SVXY or short UVXY
  small notional). Hedge with tail puts.
- **Edge:** VIX persistently exceeds realized vol because of insurance demand.
  Real but DANGEROUS — 2018 Volmageddon and 2020 March wiped out SVXY/XIV.
- **Data:** VIX, SPY realized vol, yfinance
- **Capacity:** $5-20M (vol products are size-limited)
- **Sharpe expected:** 1.0-1.5 unhedged, then -10 in a tail event

### S7. Pairs Trading (cointegrated equities)
- **Mechanism:** Identify cointegrated pairs (KO/PEP, MA/V, GS/MS, BAC/JPM,
  CVX/XOM, etc.). When spread z-score > 2, fade. Exit at z=0 or stop at z=3.
- **Edge:** Statistical mean reversion. Real but small per pair; needs many
  pairs.
- **Data:** yfinance, daily 2010-present
- **Capacity:** $1-10M per pair, $50-100M aggregate
- **Sharpe expected:** 0.8-1.5

### S8. Low Volatility Anomaly
- **Mechanism:** Long low-vol stocks via SPLV, short SPY. Captures the
  consistent underperformance of high-beta names.
- **Edge:** Behavioral (leverage-constrained investors chase risk).
  Documented since the 1970s.
- **Data:** SPLV vs SPY since 2011
- **Capacity:** $100M+ (ETF liquidity)
- **Sharpe expected:** 0.4-0.6

## Cross-Asset / Macro (2)

### S9. Multi-Asset Trend Following
- **Mechanism:** 12-month momentum across diversified ETFs (SPY, EFA, EEM,
  GLD, TLT, DBC, RWX, IEF). Long positive momentum, short negative.
  Monthly rebalance.
- **Edge:** Time-series momentum, demonstrated across markets for decades.
  CTA strategies use this exact playbook.
- **Data:** yfinance, 2007-present
- **Capacity:** $100M+
- **Sharpe expected:** 0.5-0.9

### S10. Currency Carry
- **Mechanism:** Long high-yielding currencies, short low-yielding.
  Approximated via currency ETFs (FXA AUD, FXC CAD, FXE EUR, FXY JPY, FXB GBP).
  Use proxy rates from local sovereign yields.
- **Edge:** "Forward rate bias" — high-yield currencies don't depreciate
  enough to offset their yield. Real but blew up 2008 and Feb 2018.
- **Data:** yfinance, 2010-present
- **Capacity:** $100M+ via ETFs, $1B+ via FX directly
- **Sharpe expected:** 0.4-0.7

## Decision matrix

| # | Strategy | Sharpe | Capacity | Deploy difficulty |
|---|----------|--------|----------|-------------------|
| S1 | Crypto funding arb | 1.5-3.0 | $10-100M | Low (1 exchange) |
| S2 | Cash-and-carry | 1.0-2.0 | $5-20M | Med (2 venues) |
| S3 | Stablecoin depeg | episodic | $10-50M | High (monitor 24/7) |
| S4 | Crypto momentum | 0.7-1.2 | $10-30M | Low |
| S5 | Sector momentum | 0.5-0.8 | $500M+ | Low |
| S6 | VRP | 1.0-1.5 (tail risk!) | $5-20M | High |
| S7 | Pairs trading | 0.8-1.5 | $50-100M | Med |
| S8 | Low vol anomaly | 0.4-0.6 | $100M+ | Low |
| S9 | Multi-asset trend | 0.5-0.9 | $100M+ | Low |
| S10 | Currency carry | 0.4-0.7 (tail risk!) | $100M+ | Low |

**Top picks for backtest:** S1, S2, S5, S7, S9 (mix of high-Sharpe crypto +
proven equity/cross-asset factors).
