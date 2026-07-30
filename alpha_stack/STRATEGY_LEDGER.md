# Complete Strategy Ledger — everything we've tested, with honest verdicts

Every trading strategy and investment idea explored across our entire history, with the
post-bias-audit verdict. **Winners (deployable, real edge after honest testing) are 🟢.**
Marginal/conditional are 🟡. Dead/no-edge are 🔴. Candidates not yet tested are ⚪.

The recurring theme: initial backtests routinely showed spectacular Sharpes (14, 25, 30)
that **collapsed under bias audit** — almost always from (a) timestamp/lookahead leakage,
(b) omitted fees/spread, (c) daily aggregation hiding intraday cost, or (d) multiple-testing.
The honest survivors are few, and their Sharpes are modest. That pattern is itself the most
important finding.

---

## 🟢 WINNERS — real edge, survived bias audit

| # | Strategy | Honest verdict | Capacity | Notes |
|---|----------|----------------|----------|-------|
| 1 | **Crypto funding-rate carry (ETH-focused)** | **Sharpe 3–6** (APR 11–22%, MDD 3–8%) | $50M+ | The clearest real edge. Long spot / short perp, collect funding. Initial Sharpe 14 was inflated by daily aggregation; honest MC = 3–6. ETH funding ~11% APR currently (half historical). **Primary strategy.** |
| 2 | **VRP short-vol carry** | **Sharpe ~1.5** honest | $200–500M | Sell index vol, harvest variance risk premium. Regime filters (VIX<35 AND VIX<VIX3M) cut MDD −54%→−21%. Solo-buildable via VIX futures / SVXY. |
| 3 | **Kalshi K6 + K14 (binary BTC hourly)** | 🟡→🟢 small | ~$5K/yr | Only 2 of 15 Kalshi hypotheses survived. Tiny capacity; fast turnover sleeve. K6 NOT reproduced OOS on true settlement — treat as marginal. |
| 4 | **Kalshi zero-fee perp maker-MM (fade flow)** | +0.71bp/fill paper | small | Real while perp fees = 0; uninformed mean-reverting flow. **Blocked**: account not onboarded to perps. Paper-only. Dies if fees turn on. |

## 🟡 MARGINAL / conditional — real but small or unvalidated

| Strategy | Verdict |
|----------|---------|
| HL×OKX cross-exchange alt-perp carry | Sharpe 1–5 after honest frictions (was "25"). Real but small/capacity-limited. |
| Trend-filtered cross-section momentum (crypto) | Sharpe ~2 OOS — best of the 2026-06-07 audit batch. |
| **Daily multi-factor stack (this run, `alpha_stack/`)** | **Stack Sharpe ~0.4**, OOS +0.30, 15 strategies, low corr. Small but honest; needs more orthogonal sleeves to get big. |
| Kalshi late-hour mid underprice | +$0.044/ct after fees — subsumed by K6. |
| Coinbase→Kalshi BTC lead-lag | Real but maker-only, seconds-after-jump window. Not proven on executable PnL. |

## 🔴 DEAD — no edge after honest testing

| Strategy | Why it died |
|----------|-------------|
| ICT / market-structure futures bot (TopStep) | 4 OOS strikes, all ~coin-flip after fees. Bar patterns carry no directional info. |
| Pure-ML intraday direction (futures) | OOS accuracy ~44–49.5%. Efficient at this horizon. |
| Kalshi perp/binary **taker** order-flow model | OOS ~50%, net-negative after spread. Perp efficiently arbed to <1s. |
| ETF cointegration pairs | Dead 2018–2026 OOS (daily). |
| Avellaneda-Lee residual reversion | Dead 2018–2026 OOS (daily). |
| Cross-sectional momentum on ETFs/large-caps (daily) | Dead OOS; negative this run too (s02). |
| "Sharpe 25/30" claims (multiple) | All bias artifacts (timestamp, fee, daily-agg). No Sharpe-25 edge in public crypto. |
| Kalshi sentiment alpha | Short-horizon binaries driven by spot vol, not news. |
| Kalshi lead-lag scalp (taker) | The "10–15s lag" was a timestamp artifact + illiquid binary. |
| BTC funding arb (modern era, alone) | Died post-2022; ETH carries the portfolio. |
| Kalshi K1/K3/K9/K10/K13 | Dead in systematic hypothesis test. |

## ⚪ CANDIDATES — promising, not yet tested

| Idea | Why interesting | Blocker |
|------|-----------------|---------|
| **Pinnacle/Circa → Kalshi sports** | Closing-line value, $1–5M/yr capacity (100× K6) | Legal/data access for Pinnacle |
| Crypto vol-surface arb (IBIT/CME/Deribit) | 2026 regulatory inflection (CME 24/7, CFTC perps) | Needs options data |
| Kalshi macro-event scalp (CPI/FOMC/NFP) | Event surprise vs priced distribution | Needs event-market history + index intraday |

---

## The honest meta-lesson

Across ~70+ backtests and a dozen distinct mechanisms, **two strategies are robustly real
and deployable** (funding carry, VRP), a handful are marginal, and the large majority died
under honest scrutiny — almost all after an initial "amazing" Sharpe collapsed on bias audit.
The path to a *big* combined Sharpe is **not** a single deep edge (none exists in the public
data we can access); it is **stacking the few genuine low-correlation edges** — funding carry
(Sharpe ~4) + VRP (~1.5) + the daily factor stack (~0.4) — which, being largely uncorrelated,
combine to a materially higher portfolio Sharpe than any component. That is the realistic
"Renaissance-lite" thesis given retail data access.
