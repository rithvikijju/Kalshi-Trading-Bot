# edge-bot — quant research lab & trading bots

A personal research repo spanning ~70 backtested strategy ideas across Kalshi prediction
markets, crypto perps/spot, equities/ETFs, and futures — from first prototype through
bias-audited honest verdict. Two strategies are live/paper-trading in production; most of
the rest are documented negative or marginal results, kept because **the negative results
are as much the point as the positives**: this repo is a track record of rigorous testing,
not just a pile of wins.

If you only read one file, read [`alpha_stack/STRATEGY_LEDGER.md`](alpha_stack/STRATEGY_LEDGER.md) —
it's the master status board for every strategy tested, with the post-bias-audit verdict on each.

## How to read this repo

Every strategy folder went through the same discipline: build it, backtest it, then **audit
the backtest for bias** (lookahead, fee omission, daily-aggregation hiding intraday cost,
multiple-testing) before trusting the number. The recurring pattern across this whole
project: initial backtests routinely showed spectacular Sharpes (14, 25, 30) that collapsed
to something modest once the bias was found. That collapse, repeated across a dozen
mechanisms, is itself the main finding — see the "honest meta-lesson" at the bottom of the
ledger.

Status legend used throughout: 🟢 real edge, survived audit · 🟡 marginal/conditional ·
🔴 dead, no edge · ⚪ candidate, not yet tested.

---

## 🟢 Deployed / live-paper-trading

| Strategy | Dir | What it is |
|---|---|---|
| **Crypto funding-rate carry (ETH-focused)** | [`funding_arb/`](funding_arb/) | Long spot ETH (Coinbase) / short perp (Hyperliquid), collect funding. The clearest real edge in the whole project. Honest Sharpe 3–6 (APR 11–22%, MDD 3–8%) after correcting an inflated Sharpe-14 backtest that hid intraday basis variance behind daily bars. Capacity $50M+. **Primary strategy.** |
| **BTC/ETH cointegration pairs** | [`hf_pairs_live/`](hf_pairs_live/), engine notes in [`hf_pairs/`](hf_pairs/) | Rolling 60-min log-spread z-score, dollar-neutral entries/exits on Coinbase. Non-overlapping liquidity pool with funding_arb — part of the multi-sleeve architecture below. |
| **kalshi_v2** — BTC fair-value mispricing engine | [`kalshi_v2/`](kalshi_v2/) | Production model for Kalshi BTC binaries. Empirical fair-value bank + HRDNN P-robust filter (16-measure bootstrap ambiguity set) + Lipschitz-clamped Kelly sizing. Full architecture doc below. |
| **K6 + K14** — Kalshi spot-displacement | [`kalshi_k6/`](kalshi_k6/), [`kalshi_k6_eth/`](kalshi_k6_eth/) | Only 2 of 15 systematically-tested Kalshi hypotheses ([`kalshi_hypothesis_status`](alpha_stack/STRATEGY_LEDGER.md)) survived. Small capacity (~$5K/yr) but fast turnover; treated as a satellite sleeve, not the core book. **Caveat:** not reproduced OOS on true settlement data — see `backtest_outputs/` reports — so it runs at reduced size pending re-validation. |
| **T1 monotonicity arb** | [`kalshi_t1/`](kalshi_t1/) | Cross-strike no-arb violations on Kalshi BTC ladders. Calm-regime filter (`|spot_move_30s| ≤ $30`) deployed after volatile regimes were found to eat the arb before fill; depth cap raised from 5→~15 contracts to use available book depth. |
| **Kalshi zero-fee perp maker-MM** | [`kalshi_perp_edge/`](kalshi_perp_edge/) | Fades transient uninformed flow on Kalshi's BTC/ETH perps while maker fees are 0. +0.71bp/fill in paper. Perp is otherwise efficient vs spot — the earlier "lead-lag" story was a 60s timestamp artifact, not a real edge (see `kalshi_perp_leadlag/`). **Blocked live**: account not yet onboarded to perps; dies if Kalshi turns on perp fees. |

## 🟡 Validated but marginal, shelved, or not yet built

| Strategy | Dir | Verdict |
|---|---|---|
| VRP short-vol carry | [`research/`](research/) (SHORTLIST_V2), [`strategies/`](strategies/) | Honest Sharpe ~1.5, $200–500M capacity, regime filters (VIX<35 and VIX<VIX3M) cut MDD −54%→−21%. Solo-buildable via VIX futures/SVXY. Not yet deployed. |
| HL×OKX cross-exchange alt-perp carry | [`xchg_carry/`](xchg_carry/) | Initial Sharpe 25 collapsed to honest Sharpe 1–5 / APR 1–3% after realistic frictions (25bp round-trip, basis MTM, selection bias). Implemented, not deployed. |
| Daily multi-factor stack | [`alpha_stack/`](alpha_stack/) | Reusable bias-hardened backtest harness (deflated Sharpe + Benjamini-Hochberg FDR + no-lookahead + costs). 15 daily factors stack to honest Sharpe ~0.42 alone; becomes interesting combined with funding carry + VRP (~2.9 combined). |
| Trend-filtered cross-section momentum (crypto) | [`dispersion_research/`](dispersion_research/), [`multi_asset_research/`](multi_asset_research/) | Sharpe ~2 OOS, best of the 2026-06-07 audit batch. |
| Coinbase→Kalshi BTC lead-lag | [`kalshi_perp_leadlag/`](kalshi_perp_leadlag/) | Real but maker-only: big spot jumps (≥$75/1s) leave Kalshi trailing ~10–15s with ~3.5¢ still capturable 2s post-jump. Spread kills the taker version. Not proven on executable/settle PnL. |
| Kalshi late-hour mid underprice | [`backtest_outputs/`](backtest_outputs/) | +$0.044/ct after fees in the 60–70¢ mid, 5–15min-to-close window — subsumed by K6/K14. |
| In-game mean-reversion (buy-dip/fade-spike) | [`prediction-arb/`](prediction-arb/) `strategies/mean_reversion/` | Mechanics validated; needs a tape-backtest before trusting live. |
| Pinnacle/Circa → Kalshi sports | *(candidate, code not yet started)* | Highest-EV **new** candidate identified: $1–5M/yr capacity (100× K6). Gated on legal/data access to Pinnacle closing lines. |
| Crypto vol-surface arb (IBIT/CME/Deribit) | [`research/`](research/) `convert_model.py` (Tsiveriotis-Fernandes) | 2026 regulatory-inflection candidate (CME 24/7 + CFTC perps). Needs options data to test. |

## 🔴 Dead ends (kept for the record, not because they work)

| Strategy | Dir | Why it died |
|---|---|---|
| ICT / market-structure futures bot | [`topstep_bot/`](topstep_bot/), [`topstep_strategy/`](topstep_strategy/) | CNN confidence-model selector genuinely works (OOS AUC 0.74), but the strategy is robustly **-EV** (-$2.41/trade): the payoff structure (tight targets vs micro fees), not the model, is the problem. Walk-forward correctly refused to ship it. |
| Pure order-flow / VPIN / informed-flow models | `bayesian_price_predictor.py`, `informed_flow_research.py`, `pin_vpin_strategy.py`, `vpin_backtest.py`, `fade_aggressor_*.py` | OOS ~50% accuracy, net-negative after spread — Kalshi perp/binary flow is efficiently arbed to sub-second horizons. |
| ETF cointegration pairs, Avellaneda-Lee residual reversion, cross-sectional momentum (daily, ETFs/large-caps) | [`alt_statarb/`](alt_statarb/) | Dead 2018–2026 OOS at daily frequency. Would need an intraday data unlock to revisit. |
| Kalshi sentiment alpha | — | Short-horizon hourly binaries are driven by spot vol, not news; sentiment only holds up as a risk-regime filter. |
| Kalshi K1/K3/K9/K10/K13 | [`backtest_outputs/`](backtest_outputs/), [`strategy_zoo/`](strategy_zoo/) | Dead in the systematic 15-hypothesis test (K7/K11/K12/K15 deferred pending tick/maker models). |
| BTC funding arb, alone, modern era | [`funding_arb/`](funding_arb/) | Died post-2022 (post-Luna); ETH is what carries the funding-carry portfolio now. |
| Any "Sharpe 25/30" claim | (multiple dirs) | Every one was a bias artifact — timestamp leakage, omitted fees, or daily-bar aggregation hiding intraday variance. No Sharpe-25 edge exists in retail-accessible public crypto data. |

## Infra & tooling (not strategies themselves)

| Dir | Purpose |
|---|---|
| [`alpha_stack/`](alpha_stack/) | Reusable bias-hardened backtest harness — deflated Sharpe, Benjamini-Hochberg FDR, no-lookahead checks, realistic costs. Validated by correctly rejecting a synthetic noise strategy claiming Sharpe 1.0. |
| [`quant_trading_lab/`](quant_trading_lab/) | General-purpose research framework: data clients (Alpaca/Polygon/Databento/crypto), backtest engine with `RiskLimits`, live `RiskState` monitor. |
| [`tick_capture/`](tick_capture/) | Kalshi + Polymarket tick/orderbook capture daemon feeding the DuckDB backtests. |
| [`agentic-dashboard/`](agentic-dashboard/) | Next.js "Bloomberg terminal"-style console for the agentic research/paper-trading system. |
| `portfolio_dashboard.py` | Aggregates paper-trading state across every sleeve into one view. |
| [`backtest_outputs/`](backtest_outputs/) | Reports (`*.md`) and result JSON from the 20-hypothesis and novel-strategy sweep runs. Large `analysis.duckdb` backing these is gitignored — regenerate via `merge_overnight_capture.py`. |
| [`PORTFOLIO.md`](PORTFOLIO.md) | The 3-sleeve capacity architecture (funding_arb / hf_pairs_live / kalshi_k6) and why they don't compete for the same liquidity. |
| [`STRATEGY_JOURNAL.md`](STRATEGY_JOURNAL.md), [`EDGES_FOUND.md`](EDGES_FOUND.md), [`SESSION_HIGHLIGHTS.txt`](SESSION_HIGHLIGHTS.txt) | Running research log across the whole project. |

## Early prototypes (reference implementations, not deployed)

Kept because each faithfully implements a published paper end-to-end — useful as reference
code even though none survived as a live strategy.

### `kalshi_v2/` — BTC fair-value mispricing engine (live)

The current production model. Trades Kalshi BTC binary markets (KXBTC hourly buckets,
KXBTCD daily cumulatives) by:

1. Computing a fair P(YES) for each strike from a drift-removed, vol-conditioned empirical
   sample bank built on 90 days of Coinbase BTC minute history.
2. Comparing P(YES) to the live order book and computing post-fee edge on both YES and NO sides.
3. Running every candidate signal through a **HRDNN P-robust filter** (`robust.py`) that
   re-evaluates the edge under 16 bootstrapped probability measures; trades only when ≥85%
   of measures agree.
4. Sizing via a **Lipschitz-clamped Kelly** (`robust.LipschitzSizer`) so small price
   differences don't produce wildly different position sizes.
5. Sending a 30-second-expiration limit order at `current_ask + 2c`, with an SL/TP/time/age
   exit policy on every cycle.

| Module | Role |
|---|---|
| `state.py` | Single source of truth for shared mutable state. |
| `config.py` | All knobs — one `CFG` dict, no hidden globals. |
| `client.py` | Bare Kalshi REST + WebSocket auth (RSA-PSS over SHA256). |
| `data.py` | Coinbase BTC poller, async WS listener with REST fallback. |
| `model.py` | Empirical sample bank + lognormal closed-form fallback. |
| `robust.py` | HRDNN P-robust filter + `LipschitzSizer`. |
| `sfm.py` | SFM cell (Zhang/Aggarwal/Qi KDD 2017), implemented but disabled by default. |
| `paper_db.py` | SQLite trades / robust-decisions / tick log. |
| `risk.py` | Preflight gates: ticker dedup, balance/exposure/concurrent/daily-loss/entry-band. |
| `execution.py` | Smart-limit order placement, exit policy, settlement booking. |
| `strategy.py` | Signal scanner — walks the book, computes edge, runs the robust filter. |
| `portfolio.py` | Per-mode (paper/live/shadow) portfolio views with mark-to-market. |
| `main.py` | Lifecycle: `start_bot`, `stop_bot`, `status`, `dashboard`, `kill_switch`. |

Driven from `kalshi-bot-v2.ipynb`. Modes are paper / `live_shadow` / `live`; `enable_live()`
refuses if Kalshi balance is `$0` or unknown. See `kalshi_v2/RESEARCH_MEMO.md` for which
parts of the SFM/HRDNN papers were integrated vs rejected.

### `gnn_arbitrage/` — GNN triangular arbitrage

Currencies are nodes, executable rates are directed edges; a small message-passing GNN
scores each edge for sitting on a profitable cycle. Trained on synthetic FX with injected
arbs via exhaustive Bellman-Ford cycle search; picks the highest-net-profit triangle at test
time. **Reality check:** public-quote triangular arbitrage on liquid spot crypto is
essentially extinct after taker fees (Kraken ≈78bps round-trip, Binance ≈30bps) — expect the
`oracle`/`gnn` paper-trading modes to be silent most of the time.

```bash
python scripts/run_gnn_arbitrage.py                       # offline backtest
python scripts/paper_trade.py --venue kraken --mode oracle --max-iters 2   # smoke check
```

### `ga_dmlp/` — Sezer/Ozbayoglu/Dogdu 2017 GA + Deep MLP

Faithful replica of the six-phase pipeline from `1-s2.0-S1877050917318252-main.pdf`: RSI
features across 20 intervals → genetic-algorithm-tuned buy/sell chromosome → deep MLP
`(3,20,10,8,6,5,3)` trained on GA-labeled data → interval voting → backtest.

```bash
python scripts/run_ga_dmlp.py AAPL
```

### `neufeld_arb/` — Neufeld & Sester 2024 model-free static arbitrage

Faithful implementation of `2306.16422v2.pdf` (*Neural Networks Can Detect Model-Free
Static Arbitrage Strategies*): a deep MLP proposes a static cash + long/short vanilla-option
position `(a, h+, h-)` per basket of stocks, trained against an LP-computed ground-truth
target (`lsip.py`). Backtest on real S&P option chains reproduces the paper's ~89% sign
accuracy vs LSIP target — but is inflated by stale Yahoo quotes and has no multi-leg
execution layer, so it's kept as a reference implementation, not a strategy to run.

```bash
python scripts/run_neufeld_fetch.py --out data/sp500_options.parquet --tickers AAPL MSFT ...
python scripts/run_neufeld_train.py --data data/sp500_options.parquet --out models/neufeld.pt
python scripts/run_neufeld_backtest.py --data data/sp500_options.parquet --model models/neufeld.pt
```

---

## Setup

```bash
pip install -r requirements.txt
```

Each strategy directory that needs exchange/API credentials ships a `.env.example` —
copy it to `.env`, fill in your own keys, and never commit the real file (`.gitignore`
blocks `.env*` repo-wide). Large capture/backtest data (DuckDB, Parquet, JSONL tick logs,
raw price-history CSVs) is intentionally gitignored — it's multi-GB, regenerable from each
strategy's own `capture.py`/`fetch.py`, and doesn't belong in git history. See the "what's
excluded" note below if you're rebuilding from a fresh clone.

## What's excluded from this repo, and why

This repo holds source, research docs, and small result artifacts (equity curves, trade
CSVs, summary JSON) — not raw data. Gitignored:

- **Secrets**: `.env`, `*.env.sh` (real API keys for Kalshi, Hyperliquid, Alpaca, Databento,
  Binance, OKX, Kraken, Discord/Slack webhooks, etc. — only the `.env.example` templates
  are tracked).
- **Data caches**: `*.duckdb`, `*.sqlite`, `*.parquet`, `*.pkl`, `*.db`, `*.jsonl`, root
  `data/`, `tick_data/`, `output/` — multi-GB raw captures and backtest databases,
  regenerable via each strategy's capture scripts.
- **Build artifacts**: `node_modules/`, `.next/`, `__pycache__/`, `.venv/`.
- **Personal/off-topic**: resume files — out of scope for a trading-strategy repo.
