# Kalshi-Trading-Bot

Multiple strategies live in this repo. The active one is `kalshi_v2/`;
the rest are earlier research iterations (GNN arb, GA-DMLP, Neufeld static
arb) kept for reference.

## 0. `kalshi_v2/` — BTC fair-value mispricing engine (live)

The current production model. Trades Kalshi BTC binary markets (KXBTC
hourly buckets, KXBTCD daily cumulatives) by:

1. Computing a fair P(YES) for each strike from a drift-removed,
   vol-conditioned empirical sample bank built on 90 days of Coinbase
   BTC minute history.
2. Comparing P(YES) to the live order book and computing post-fee edge
   on both YES and NO sides.
3. Running every candidate signal through a **HRDNN P-robust filter**
   (`robust.py`) that re-evaluates the edge under 16 bootstrapped
   probability measures; trades only when ≥85% of measures agree.
4. Sizing via a **Lipschitz-clamped Kelly** (`robust.LipschitzSizer`) so
   small price differences don't produce wildly different position sizes.
5. Sending a 30-second-expiration limit order at `current_ask + 2c`,
   with an SL/TP/time/age exit policy on every cycle.

### Architecture

| Module | Role |
|---|---|
| `state.py` | Single source of truth for shared mutable state (LOCK, SPOT, BOOKS, TRACKED, WS_STATE, BOT_STATE). Edit-free; immune to autoreload identity drift. |
| `config.py` | All knobs (mode, thresholds, sizing, WS URL, robust-filter params). One CFG dict, no hidden globals. |
| `client.py` | Bare Kalshi REST + WebSocket auth (RSA-PSS over SHA256). No wrappers. |
| `data.py` | Coinbase BTC poller, async WS listener (with REST fallback on 401/403), event tracker. Uses gnn-arb-live's WS subscription pattern. |
| `model.py` | Empirical sample bank (drift-removed, vol+kurt matched) + lognormal closed-form fallback. Single `fair_value()` dispatcher. |
| `robust.py` | HRDNN P-robust filter (moving-block bootstrap of log returns → 16 fair-value measures → unanimity-style decision rule) + LipschitzSizer. |
| `sfm.py` | SFM cell (Equations 7–19 of Zhang/Aggarwal/Qi KDD 2017). Implemented but disabled by default — base model has no recurrent encoder; available behind `CFG['sfm_enabled']`. See `RESEARCH_MEMO.md`. |
| `paper_db.py` | SQLite trades / robust-decisions / spot-tick / book-tick log. Idempotent schema. |
| `risk.py` | Mode-aware preflight: hard ticker dedup (always), plus balance / exposure / concurrent / daily-loss / entry-band gates in live mode. |
| `execution.py` | Smart-limit order placement, position-management exit policy, settlement booking from Kalshi's `result` field. |
| `strategy.py` | The signal scanner. Walks BOOKS, computes edge per strike, runs robust filter, returns sorted DataFrame. |
| `portfolio.py` | Per-mode (paper / live / shadow) portfolio views with mark-to-market. |
| `main.py` | Lifecycle: `start_bot`, `stop_bot`, `status`, `dashboard`, `tail_log`, `enable_live`, `disable_live`, `kill_switch`. Spins 4 daemon threads. |

### How to run

`kalshi-bot-v2.ipynb` is the thin notebook driver. Cells:

| § | Purpose |
|---|---|
| 1 | Imports + autoreload |
| 2 | Build `kalshi_md` + `kalshi_live` clients (RSA key from `~/.kalshi/credentials.env`) |
| 3 | Display CFG |
| 4 | Fetch 90 d BTC minute bars from Coinbase |
| 5 | Preview the empirical bank |
| 6 | Preview the ambiguity set (verifies bootstrap actually varies measures) |
| 7 | `start_bot()` — spawns spot poller, WS listener, event tracker, decision worker |
| 8 | `status()` — quick snapshot, run any time |
| 8b | `dashboard()` — full live diagnostic: connectivity, tracking, per-market edge breakdown with rejection reasons, robust filter outcomes, risk blocks, trades, log |
| 8c | `tail_log()` — raw log lines |
| 9 | Manual `scan_signals()` diagnostic |
| 10–14 | Open positions, robust decisions, portfolio, settled trades, risk blocks |
| 13b | **Full trade log** (entries + robust decisions + settlements with PnL/win-rate) |
| 15 | Controls (stop / kill / enable_live / cancel_all) |

Modes are paper / `live_shadow` / `live`. Default is paper. `enable_live()`
refuses if Kalshi balance is `$0` or unknown.

### Key tunables (`config.py`)

| Knob | Default | What it does |
|---|---|---|
| `min_edge_cents` | 2.5 | reject signals with post-fee edge below this |
| `max_spread_cents` | 3 | reject markets with bid-ask spread above this |
| `min_entry_price` / `max_entry_price` | 0.20 / 0.80 | reject extreme entries |
| `robust_min_pass_rate` | 1.0 | fraction of ambiguity-set measures that must show positive edge (0.85 = 14/16) |
| `robust_n_bootstrap` | 16 | size of the ambiguity set |
| `stop_loss_pct` | 0.20 | exit if mid drops 20% from entry |
| `take_profit_cents` | 5.0 | exit when mid moves +5c |
| `time_exit_min_ttl_m` | 3 | flat the position when ≤3 min to expiry |
| `max_position_age_min` | 90 | hard close after 90 min held |
| `lipschitz_position_L` | 200 | max Δsize per cent of entry-price change |

### Research memo

`kalshi_v2/RESEARCH_MEMO.md` documents which components of the SFM
(Zhang/Aggarwal/Qi KDD 2017) and HRDNN (Yadav/Mohanty arXiv 2025) papers
were integrated, which were rejected, and why.

### Data leakage review

`DATA_LEAKAGE_REVIEW.md` (on `claude/review-data-leakage-d7aG4`) audits
the v2 pipeline. Live operation is clean; the only flagged risks were
backtest-related (since deferred).

---

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



