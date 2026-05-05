# Kalshi-Trading-Bot

Two strategies sit alongside the original Kalshi notebooks:

## 1. `gnn_arbitrage/` — GNN triangular arbitrage strategy + backtester + paper trader

Currencies are nodes, executable rates are directed edges, and a small
message-passing GNN scores each edge for the probability that it sits on a
profitable cycle. Training labels come from exhaustive Bellman-Ford-style
cycle search on synthetic FX snapshots; at test time the strategy picks the
highest-net-profit triangle whose edges all clear a confidence threshold and
trades it with configurable fees and slippage.

### Offline backtest (synthetic FX with injected arbs)

```
python scripts/run_gnn_arbitrage.py
```

### Live paper trading (Binance / Kraken / Coinbase / Bybit via ccxt)

`scripts/paper_trade.py` polls a public ticker endpoint, builds the rate
graph, ranks cycles, and writes simulated fills to `paper_trades.jsonl`.
Nothing is sent to the exchange. Three modes:

| `--mode`   | What it does |
|------------|--------------|
| `oracle`   | Bellman-Ford ground truth: trade any post-fee profitable triangle |
| `gnn`      | Pretrain an `EdgeArbitrageGNN` on synthetic data, then score live edges |
| `baseline` | Trade any cycle whose **gross** edge clears `--min-gross-bps` |

```
# Smoke check — pull two snapshots from Kraken
python scripts/paper_trade.py --venue kraken --mode oracle --max-iters 2

# Continuous run on Binance, GNN strategy, $200 notional, 2-second cadence
python scripts/paper_trade.py --venue binance --mode gnn \
    --currencies USDT BTC ETH SOL XRP --poll 2 --notional 200

# Tail the log
tail -f paper_trades.jsonl
```

**Reality check.** Public-quote triangular arbitrage on liquid spot crypto
markets is essentially extinct after taker fees (Kraken ≈ 26 bps × 3 legs
= 78 bps; Binance ≈ 10 bps × 3 = 30 bps). Expect the `oracle` and `gnn`
modes to be silent most or all of the time. Use `baseline --min-gross-bps 0`
to confirm the plumbing fires; use `oracle` to discover whether your venue
ever shows real edges.

**What's faked vs. real.** The data feed is real top-of-book (REST tickers).
The "execution" is purely a JSONL log of intended fills priced at the bid
quoted at signal time, minus the venue's taker fee — no order-book depth,
no latency, no partial fills. To go from this to actual paper accounts you
swap `PaperBroker.execute_cycle` for venue paper-API calls (Binance Spot
Testnet, etc.).

### Modules

| File | Role |
|------|------|
| `data.py` | Synthetic FX feed with injected arbs |
| `graph.py` | Rate-graph construction with cycle-aware edge features |
| `arbitrage.py` | Bellman-Ford-style ground-truth cycle search |
| `gnn_model.py` | Plain-PyTorch edge-classifying GNN with raw-feature skip |
| `backtester.py` | Offline backtester + oracle baseline |
| `live.py` | `CCXTFeed` adapter — emits `FXSnapshot`s from a live venue |
| `paper.py` | `PaperBroker` — fee-aware simulated execution, JSONL log, kill-switch |

## 2. `ga_dmlp/` — Sezer/Ozbayoglu/Dogdu 2017 GA + Deep MLP

Faithful replica of the six-phase pipeline from
`1-s2.0-S1877050917318252-main.pdf` (Procedia CS 114 (2017) 473–480):

| Phase | What it does |
|------:|--------------|
|  0 | Adjust OHLC by `Close / Adj Close` ratio |
|  1 | Compute RSI for intervals 1..20, SMA‑50/200 trend direction |
| GA | 8‑gene chromosome `(RSIbuy, intBuy, RSIsell, intSell)` × {downtrend, uptrend}; pop=50, crossover=0.7, mutation=0.001 |
|  2 | Build labeled MLP training set (buy/sell from chromosome, hold from in‑between RSI) |
|  3 | Deep MLP `(3, 20, 10, 8, 6, 5, 3)`, 200 epochs, input = `(RSI value, RSI interval, trend dir)` |
|  4 | Voting across all 20 RSI intervals; class with count > 14 wins, else hold |
|  5 | Backtest: $10k start, all‑in, $1 commission, 10% stop‑loss, ignore repeated labels |

```
python scripts/run_ga_dmlp.py AAPL
```

Falls back to a synthetic GBM price series when yfinance can't reach the
network.

## 3. `neufeld_arb/` — Neufeld & Sester 2024 model-free static arbitrage

Faithful implementation of the algorithm in
[`2306.16422v2.pdf`](2306.16422v2.pdf): *Neural Networks Can Detect
Model-Free Static Arbitrage Strategies* (Neufeld & Sester, NTU/NUS,
Aug 2024). Different problem from the GNN cycle search above:

| | GNN triangular arbitrage | Neufeld–Sester static arbitrage |
|---|---|---|
| Domain | FX / crypto cross rates | Vanilla call options on stock baskets |
| Strategy | One trade through a 3-leg cycle | `(a, h+, h-)`: cash + long/short option positions |
| Holding | Instant round-trip | Hold to expiry, no rebalancing |
| Edge | Cycle product > 1 after fees | `payoff ≥ 0 ∀ S ∈ S` AND `price < 0` |
| Detector | GNN scoring directed edges | Deep MLP `R^{3N} → R^{1+2N}` |

### Pipeline

```
┌──────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────┐   ┌──────────────┐
│ run_neufeld_ │ → │ run_neufeld_ │ → │ run_neufeld_│   │ run_neufeld_ │   │ replay /     │
│ fetch.py     │   │ train.py     │   │ backtest.py │   │ paper.py     │ → │ confusion +  │
│ (yfinance)   │   │ (Alg 1)      │   │ (sec 3.1.3) │   │ (live chain) │   │ profit dist  │
└──────────────┘   └──────────────┘   └─────────────┘   └──────────────┘   └──────────────┘
```

#### Algorithm 1 (paper Section 3.1)

For each iteration:

1. Sample a batch of `B` markets `(K_b, π_b)` — paper draws 5 random S&P
   constituents per market and stacks 11 calls each (`N = 55` securities).
2. Pre-compute LSIP target `Y_b = V(K_b, π_b) = inf f(π_b, a, h)` over
   feasible strategies, set `Y~_b = -1` if `Y_b < 0` else `0`.
3. Sample `SB` terminal scenarios `S_{b,j}` from the prediction set
   `[0, 2]^d` (paper's choice).
4. Forward the NN, minimize per-batch:

   ```
   loss = f(π_b, NN_a, NN_h)                                                   # price
        + γ · (1/SB) · Σ_j max(0, -I_S(NN))^2                                  # feasibility
        + γ · max(0, -(Y~_b + 0.5) · f(π_b, NN_a, NN_h))                       # sign-correctness
   ```

   `γ` ramps from 1 → 10 000 across iterations.

#### Real-data run (no synthetic fallback)

```bash
# 1. Snapshot real S&P 500 option chains via yfinance (single REST call per ticker).
python scripts/run_neufeld_fetch.py --out data/sp500_options.parquet \
    --tickers AAPL MSFT GOOGL AMZN META NVDA TSLA AVGO JPM V JNJ WMT MA PG UNH

# 2. Train Algorithm 1 on the snapshot (5x1024 ReLU MLP, ~5k iters on CPU).
python scripts/run_neufeld_train.py \
    --data data/sp500_options.parquet \
    --out  models/neufeld.pt \
    --pool 5000 --iters 5000

# 3. Backtest on held-out 5-stock combinations from the same snapshot
#    (mirrors paper Sections 3.1.1–3.1.2). Reports profit distribution,
#    feasibility-violation rate, and confusion vs LSIP target.
python scripts/run_neufeld_backtest.py \
    --data data/sp500_options.parquet \
    --model models/neufeld.pt \
    --n 500

# 4. Live paper trading: poll the same chain every minute, run NN, log
#    the proposed (a, h+, h-) strategy plus its predicted price.
#    Nothing is sent to a broker.
python scripts/run_neufeld_paper.py \
    --model models/neufeld.pt \
    --tickers AAPL MSFT GOOGL AMZN META \
    --poll 60 --log neufeld_paper.jsonl

# Watch fills accumulate
tail -f neufeld_paper.jsonl
```

#### What the backtest output means

```
=== net profit (I_S - f) over eval grid, mean per market ===
  count: 500
  mean: 0.46    # paper Table 1: mean ≈ 0.478
  std:  0.32
  ...
NN proposes arbitrage on 412/500 markets (82.40%)   # paper: ~75%
feasibility violation rate (any S in grid with payoff < -1e-6): 7.20%
                                                                 # paper: "violations happen
                                                                 #  frequently but are
                                                                 #  typically only marginal"
vs LSIP target sign:
  precision: 0.95
  recall:    0.91
  accuracy:  0.89    # paper Table 3 5-layer no-reg: 0.892
```

Numbers will obviously differ on your snapshot (different day, different
expiry, different basket) — what matters is the relative shape: most
market combinations admit arbitrage on stale Yahoo bid/ask quotes, and
the NN reproduces the LSIP target sign with ~89% accuracy.

#### Caveats

- **Stale quotes inflate the result.** Yahoo Finance bid/ask on illiquid
  strikes can lag minutes. Real exploitable arbitrage requires fresh
  market data and atomic multi-leg execution.
- **No execution layer.** Logging a proposed `(a, h+, h-)` is not a
  fill — actually trading 11 options simultaneously requires a multi-leg
  options API (IBKR, Tastytrade) and is out of scope here.
- **Held-to-maturity.** A static strategy is by definition path-independent
  but takes the full time to expiry to realize. You eat all the dividend,
  borrow, and assignment risk on the short legs.

### Modules

| File | Role |
|------|------|
| `payoff.py`       | Vanilla call payoffs `Ψ_i` and the static-strategy `I_S`, `f` (NumPy + Torch) |
| `market.py`       | `OptionsBundle`, `Market`, `sample_market`, `scale_to_unit_spot` |
| `lsip.py`         | LP-based `V(K, π)` target via `scipy.linprog` |
| `data.py`         | yfinance options-chain fetcher (no synthetic fallback) |
| `model.py`        | `StaticArbDetector` MLP with bounded output layer |
| `training.py`     | Algorithm 1 trainer with γ-ramp |
| `backtester.py`   | Replay backtester per paper Section 3.1.3 |
| `paper.py`        | Live paper trading: real chain → NN → JSONL log |

---

## Setup

```
pip install -r requirements.txt
```

## BTC 1-Hour Kalshi Backtest Report

Backtest script:

```
python scripts/backtest_1hr_collected_data.py
```

Live execution script:

```
python scripts/btc_1hr_research_live.py
```

The deployed live script is pinned to the successful research backtest:
KXBTCD hourly BTC markets only, cumulative "$X or above" markets only,
one selected signal per minute, one contract per signal, 7-day empirical
training window frozen at event open, official Kalshi taker fee estimate,
and real orderbook execution checks. Running it with no flags starts live
prod execution. Use `--dry-run --once` for a one-cycle validation scan.

### Data Verification

| Month | Files | Time Coverage (UTC) | Rows/File | Markets/File | Errors | Missing Calendar Candidates |
|---|---:|---|---:|---:|---:|---:|
| March 2026 | 696 | 2026-03-01 04:01 -> 2026-04-01 03:00 | 60 | 75-188 | 0 | 48 |
| April 2026 | 670 | 2026-04-01 03:01 -> 2026-05-01 03:00 | 60 | 188 | 0 | 50 |

The missing candidates are mostly recurring unavailable hours in the Kalshi
history export set, not malformed files. The verifier refused to backtest on
structural errors; both March and April reports had `errors: []`.

### March 2026, Official Fee + 2c Assumed Spread

Source: `backtest_outputs/march_official_fee_2c/backtest_1hr_summary.csv`

| Strategy | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Win Rate | Profit Factor | Avg Edge (c) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| paper | 2,108 | 164.47 | 1,089.53 | 15.10% | -8.74 | 59.49% | 1.38 | 12.12 |
| neohardened | 3,488 | 336.55 | 2,196.45 | 15.32% | -7.11 | 72.62% | 1.60 | 19.22 |
| research | 2,081 | 364.67 | 1,184.33 | 30.79% | -3.12 | 74.44% | 2.24 | 23.49 |

### April 2026, Official Fee + 2c Assumed Spread

Source: `backtest_outputs/april_official_fee_2c/backtest_1hr_summary.csv`

| Strategy | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Win Rate | Profit Factor | Avg Edge (c) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| paper | 2,187 | 204.93 | 1,140.07 | 17.98% | -11.32 | 61.50% | 1.48 | 13.70 |
| neohardened | 3,623 | 390.87 | 2,254.13 | 17.34% | -14.01 | 73.01% | 1.67 | 21.28 |
| research | 2,203 | 413.93 | 1,247.07 | 33.19% | -5.97 | 75.40% | 2.33 | 25.78 |

### April 2026, Research Rerun With Full BTC Cache

Source:
`backtest_outputs/april_official_fee_2c_fullbtc_research/backtest_1hr_summary.csv`

| Strategy | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Win Rate | Profit Factor | Avg Edge (c) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| research | 2,276 | 418.71 | 1,296.29 | 32.30% | -6.91 | 75.35% | 2.31 | 25.61 |

### Production Selection

Deploy `scripts/btc_1hr_research_live.py`. Across March and April it had
the best PnL, best return on premium, best profit factor, and materially
lower drawdown than the other two BTC 1-hour scripts.

Live execution safeguards:

| Guard | Behavior |
|---|---|
| Correct event | Trades only the current KXBTCD hourly event whose API close time matches the event ticker's New York close hour. |
| Correct market | Trades only cumulative `-T` markets inside that exact event; bucket markets and next-day events are rejected. |
| Duplicate protection | Blocks existing DB `submitted`, `filled`, or `partial_filled` rows and all nonzero live Kalshi positions by ticker. |
| Bankroll control | Reads Kalshi `balance` and `portfolio_value` every live cycle, blocks orders that exceed available cash, per-market exposure, or total active exposure. |
| Order pricing | Uses real YES and NO orderbook bids. YES ask is `1 - best NO bid`; NO ask is `1 - best YES bid`. |
| Order type | Uses Kalshi V2 FOK orders with `cancel_order_on_pause` and self-trade prevention. |
| Unknown order state | Inserts a local `submitted` row before POSTing; timeout/unknown state blocks re-entry instead of retrying blindly. |




## Historical Runs

### Base Paper Bot (15 Hour Run)
- **Total Trades:** 61 (Settled: 54)
- **Net PnL:** +0.532 USD
- **Win Rate:** 53.7%

### Hardened Bot (15 Hour Run)
- **Total Trades:** 64 (Settled: 56)
- **Net PnL:** +1.498 USD
- **Win Rate:** 50.0%

