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

## BTC 1-Hour Current Handoff

This repo contains several experiments, but the active BTC/Kalshi work is now
centered on the KXBTCD 1-hour cumulative markets. Current state as of May 8,
2026:

| Track | Script | Mode | Strategy | Status / Purpose |
|---|---|---|---|---|
| Production live | `scripts/btc_1hr_risk_adjusted_research_live.py` | Real Kalshi prod orders | `research` signal with risk-adjusted sizing | Current live-money executor. This wraps the websocket research executor and keeps the signal logic aligned with `btc_1hr_research_live.py`. |
| Core executor/model | `scripts/btc_1hr_research_live.py` | Live or paper, websocket or polling | `research`, `js_guarded` selectable | Shared engine: Kalshi websocket books, Kraken/Coinbase BTC data, event guards, order placement, capture DB, and scan loop. |
| Paper shadow | `scripts/btc_1hr_js_guarded_shadow.py` | Simulated fills, no Kalshi orders | `js_guarded` with `$1000` mock bankroll | Watches the same websocket-style path and reports bankroll/PnL for the guarded Jane-Street-style candidate. |
| Risk model helper | `scripts/risk_adjusted_research.py` | Imported helper | Conservative Kelly sizing | Caps live size by bankroll, visible depth, entry risk, and model confidence. |
| May 8 research sweep | `scripts/may8examine.py` | Offline replay | Current model plus guardrail variants | Tests calibration shrinkage, basis/near-strike guards, momentum-exhaustion guards, and no-dump-chase guards across historical DuckDB, CSV diagnostics, and optional live capture snapshots. |
| Corrected replay | `scripts/backtest_research_duckdb.py` | Offline DuckDB replay | `paper`, `research`, `js_robust`, `js_guarded` | Earlier causal bid/ask-candle benchmark used for model comparison. |

The production bot is no longer the raw fixed-size research script. Use the
risk-adjusted wrapper for live money unless deliberately reproducing an old
run. The live signal is still the successful `research` model, but final
contract size is risk-adjusted. The shadow bot is still paper-only.

Quick commands from the project root:

```powershell
# Start live production risk-adjusted research bot and watch its log.
$ts = Get-Date -Format "yyyyMMdd_HHmmss"; Start-Process -WindowStyle Hidden -FilePath python -WorkingDirectory (Get-Location) -ArgumentList "scripts\btc_1hr_risk_adjusted_research_live.py" -RedirectStandardOutput "logs\risk_adjusted_research_$ts.out.log" -RedirectStandardError "logs\risk_adjusted_research_$ts.err.log"; Start-Sleep -Seconds 5; Get-Content "logs\risk_adjusted_research_$ts.out.log" -Wait -Tail 100

# Start js_guarded paper shadow and watch its log.
$ts = Get-Date -Format "yyyyMMdd_HHmmss"; Start-Process -WindowStyle Hidden -FilePath python -WorkingDirectory (Get-Location) -ArgumentList "scripts\btc_1hr_js_guarded_shadow.py" -RedirectStandardOutput "logs\js_guarded_shadow_$ts.out.log" -RedirectStandardError "logs\js_guarded_shadow_$ts.err.log"; Start-Sleep -Seconds 5; Get-Content "logs\js_guarded_shadow_$ts.out.log" -Wait -Tail 100

# Watch latest live or shadow log without starting a new process.
$log = (Get-ChildItem logs\risk_adjusted_research_*.out.log,logs\research_live_*.out.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName; Get-Content $log -Wait -Tail 100
$log = (Get-ChildItem logs\js_guarded_shadow_*.out.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName; Get-Content $log -Wait -Tail 100

# Check whether the live and shadow Python processes are still running.
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" | Where-Object { $_.CommandLine -like '*btc_1hr_risk_adjusted_research_live.py*' -or $_.CommandLine -like '*btc_1hr_research_live.py*' -or $_.CommandLine -like '*btc_1hr_js_guarded_shadow.py*' } | Select-Object ProcessId,CommandLine
```

Current model details:

| Component | Detail |
|---|---|
| Market universe | Current-hour KXBTCD cumulative `-T` BTC above/below markets only. Bucket markets, next-day markets, wrong close hour, and stale events are rejected. |
| Price feed | Kraken websocket is the preferred live BTC feed; Coinbase candle/cache support remains in the engine for history and fallback. Kalshi pricing comes from real websocket orderbook state, not `NO = 1 - YES` assumptions. |
| Fair value | 7-day causal empirical BTC move cache frozen at event open, blended with lognormal fair value and dampened using `BRTI_DAMPENING = 0.80`. |
| Entry filter | Fee-aware net edge must clear the base 12c threshold plus uncertainty. Spread must be <= 2c, entry between 25c and 75c, and side probability must be strong enough (`YES >= 0.65` or `NO <= 0.35`). |
| Execution | Kalshi V2 FOK event orders with real visible depth, pre-inserted local order rows, event locks, position checks, balance checks, and 409/FOK no-fill reconciliation. |
| Sizing | Risk-adjusted helper uses conservative probability, fractional Kelly, bankroll and exposure caps, visible-depth caps, and entry-risk caps. It is allowed to use up to 3 contracts but is not forced to use 3. |
| Capture | Live top-of-book, lifecycle, signal scans, order decisions, private events, and health rows are written to `~/.btc_kalshi_bot/research_live_capture.duckdb`; raw websocket deltas are intentionally off by default. |

Local runtime databases:

| Path | Meaning | Commit? |
|---|---|---|
| `~/.btc_kalshi_bot/research_live_trades.db` | SQLite ledger for live research orders and event locks | No |
| `~/.btc_kalshi_bot/research_live_capture.duckdb` | Websocket/live-capture dataset for exact future replay | No |
| `~/.btc_kalshi_bot/js_guarded_shadow_trades.db` | Paper-shadow fills for `js_guarded` | No |
| `~/.btc_kalshi_bot/js_guarded_shadow_capture.duckdb` | Paper-shadow websocket/live-capture dataset | No |

Research/backtest data map:

| Path | Purpose | Commit? |
|---|---|
| `data/research_datamart/research.duckdb` | Full local historical bid/ask DuckDB; too large for GitHub, ignored. |
| `data/research_datamart/research_backtest.duckdb` | Compact commit-safe DuckDB used by other people to reproduce core replay work. | Yes |
| `data/research_datamart/kalshi_quotes.parquet` | Historical observed bid/ask minute candles; ignored because generated. |
| `data/research_datamart/kalshi_markets.parquet` | Market/event metadata and settlement proxy fields; ignored because generated. |
| `data/research_datamart/kalshi_bidask_events/` | Raw per-event historical bid/ask pulls; ignored because generated. |
| `data/btc_1m_research_live_cache.parquet` | BTC minute cache used by live/research scripts; generated. |
| `data/*.csv` | Original manual Kalshi chart-price exports; retained for coverage/reference but not deployment evidence. |
| `backtest_outputs/may8examine_historical/` | May 8 high-quality historical DuckDB backtest outputs. Ignored. |
| `backtest_outputs/may8examine_csv_2c/` | May 8 CSV diagnostic outputs with assumed 2c spread. Ignored. |
| `backtest_outputs/may8examine_combined/` | Combined summary/trade CSV from May 8 runs. Ignored. |

May 8 guardrail research:

```powershell
python scripts\may8examine.py --output-dir backtest_outputs\may8examine_historical --progress-every-events 50
python scripts\may8examine.py --skip-historical --include-csv --assumed-spread-cents 2 --output-dir backtest_outputs\may8examine_csv_2c --progress-every-events 100
```

`scripts/may8examine.py` reuses the live research feature logic, applies the
same Kalshi taker-fee estimate, and enforces causal training windows. It
does not model early exits. Historical and CSV settlements use BTC minute
close proxies because official Kalshi `expiration_value` is not stored for all
older datasets. The current live-capture DB was locked by the running bot, so
the May 8 run did not stop the bot to snapshot it.

High-quality historical DuckDB result (`data/research_datamart/research.duckdb`,
1,488 events, 4,538,666 quote rows):

| Variant | Train Trades | Train PnL | Val Trades | Val PnL | Test Trades | Test PnL | Test Return on Premium | Test Max DD | Read |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `baseline_current` | 66 | +4.49 | 54 | -0.65 | 53 | +2.41 | 6.77% | -3.27 | Current signal still has edge on held-out test, but validation was negative. |
| `market_shrink_25` | 7 | +0.09 | 6 | +1.14 | 7 | +1.12 | 28.87% | -0.64 | Most stable split profile, but the sample is too small to deploy. |
| `vol_dist_075` | 46 | +3.38 | 38 | -0.26 | 37 | +1.24 | 4.63% | -2.45 | Reduces trade count and drawdown, but does not fix validation. |
| `momentum_exhaustion_guard` | 39 | +1.14 | 42 | -1.82 | 35 | +4.04 | 17.60% | -2.42 | Directly attacks the live failure mode, but validation failure blocks deployment. |
| `no_dump_chase_guard` | 52 | +2.12 | 47 | -2.05 | 41 | +3.82 | 14.05% | -2.16 | Helps held-out test and aligns with live diagnosis, but validation was bad. |
| `basis_shrink_75` | 4 | -0.81 | 1 | -0.54 | 0 | 0.00 | n/a | n/a | Too sparse and negative. |
| `may8_guarded` | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | n/a | n/a | Over-filtered on faithful bid/ask data. |
| `may8_conservative` | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | n/a | n/a | Over-filtered on faithful bid/ask data. |

CSV diagnostic result with assumed 2c spread (`data/*.csv`, 1,366 events,
2,020,851 quote rows) looked much better, for example `may8_guarded` produced
+94.38 train, +105.98 validation, and +61.61 test. Do not treat those numbers
as deployable evidence. They use exported chart prices plus an assumed spread,
which is exactly the data source that previously made the strategy look
unrealistically strong.

Live reality check from settled fills before May 8: the current research family
had a good hit rate but weak realized economics. Across 20 settled live fills
the bot won 14 and lost 6, but realized PnL was about `-$0.87` on `$26.87` of
premium (`-3.23%` return on premium). YES-side fills were positive; NO-side
fills lost money, especially after sharp selloffs where the model over-trusted
continued downside. FOK rejections were useful: missed/not-filled attempts
would have lost materially if forced through at stale assumptions. This is why
risk-adjusted sizing and exact live capture matter more than adding larger
fixed size.

What to trust:

| Use | Trust level |
|---|---|
| Corrected DuckDB bid/ask replay | Best historical evidence currently in repo, but still minute-candle replay with BTC proxy settlement. |
| Exact websocket capture DBs | Best future dataset for live-faithful replay once enough duration is collected. |
| Live SQLite trade ledger | Source of truth for what we actually attempted/filled. |
| Raw Kalshi CSV exports | Coverage/reference only, not executable fills. |
| Old assumed-spread CSV backtests | Historical diagnostics only; do not use for deployment decisions. |

Current research conclusion: keep the risk-adjusted research live bot as the
production candidate, keep `js_guarded` paper-shadowed, and do not deploy the
May 8 guardrail variants yet. `market_shrink_25` is the cleanest-looking new
idea, while momentum/no-dump guards match the live loss diagnosis, but none
passed train/validation/test strongly enough for live promotion.

## BTC 1-Hour Kalshi Backtest Report

Backtest script:

```
python scripts/backtest_1hr_collected_data.py
```

Live execution script:

```
python scripts/btc_1hr_risk_adjusted_research_live.py
```

The deployed live script is pinned to the successful research signal:
KXBTCD hourly BTC markets only, cumulative "$X or above" markets only,
one selected signal per event, 7-day empirical training window frozen at
event open, official Kalshi taker fee estimate, and real orderbook execution
checks. The signal filter remains the one-contract research filter; final live
size is then risk-adjusted from bankroll, entry risk, model confidence, and
visible depth. Running the wrapper with no flags starts live prod execution.
Use the core executor's dry-run flags only when deliberately doing diagnostics.

### Data Verification

| Month | Files | Time Coverage (UTC) | Rows/File | Markets/File | Errors | Missing Calendar Candidates |
|---|---:|---|---:|---:|---:|---:|
| March 2026 | 696 | 2026-03-01 04:01 -> 2026-04-01 03:00 | 60 | 75-188 | 0 | 48 |
| April 2026 | 670 | 2026-04-01 03:01 -> 2026-05-01 03:00 | 60 | 188 | 0 | 50 |

The missing candidates are mostly recurring unavailable hours in the Kalshi
history export set, not malformed files. The verifier refused to backtest on
structural errors; both March and April reports had `errors: []`.

### Faithful DuckDB/Parquet Datamart

Build the corrected research store:

```
python scripts/download_kalshi_bidask_history.py --from-csv-dir data --out-dir data/research_datamart/kalshi_bidask_events --workers 2 --chunk-minutes 15 --overwrite
python scripts/build_research_datamart.py --out-dir data/research_datamart --fill-internal-btc-gaps
```

Run the strict replay:

```
python scripts/backtest_research_duckdb.py --strategy research --db data/research_datamart/research.duckdb --output-dir backtest_outputs/research_duckdb
python scripts/backtest_research_duckdb.py --strategy paper --db data/research_datamart/research.duckdb --output-dir backtest_outputs/paper_duckdb
```

Outputs:

| Path | Role |
|---|---|
| `data/research_datamart/research_backtest.duckdb` | Compact queryable DuckDB copy, safe to commit without LFS |
| `data/research_datamart/research.duckdb` | Full local DuckDB copy; ignored because it exceeds GitHub's 100 MB file limit |
| `data/research_datamart/kalshi_quotes.parquet` | Observed historical Kalshi bid/ask candles; no forward fill |
| `data/research_datamart/kalshi_bidask_events/` | Per-event bid/ask Parquet parts from Kalshi's event candlestick API |
| `data/research_datamart/kalshi_csv_prices.parquet` | Raw CSV exports copied for reference only |
| `data/research_datamart/btc_1m.parquet` | Coinbase candles with `available_at = bucket_start + 1 minute` |
| `backtest_outputs/research_duckdb/` | Corrected research backtest report |
| `backtest_outputs/paper_duckdb/` | Corrected original paper-signal backtest report |

### What The BTC/Kalshi Data Actually Is

There are three distinct data layers. They should not be treated as
interchangeable.

| Data | Location | What one row means | Used for faithful fills? |
|---|---|---|---|
| Raw Kalshi UI CSV exports | `data/kalshi-price-history-kxbtcd-*.csv` and `kalshi_csv_prices` | One timestamp for one hourly event, with many strike columns like `$65000 or above`; values are Kalshi chart/export prices in cents | No |
| Corrected Kalshi bid/ask replay | `kalshi_quotes` / `kalshi_quotes.parquet` / `research_backtest.duckdb` | One market/strike/minute with observed historical `yes_bid_close` and `yes_ask_close` | Yes |
| Coinbase BTC candles | `btc_1m` / `btc_1m.parquet` | One Coinbase BTC-USD 1-minute OHLCV candle plus rolling features | Used for signals and settlement proxy |

The raw CSV files are the manual downloads from pages like
`kxbtcd-26mar0704`, `kxbtcd-26mar0705`, etc. Each file is one hourly KXBTCD
event, and each column is a cumulative threshold contract. Those files are
good for coverage checks and rough diagnostics, but they are not enough for a
production-quality backtest because the exported chart price is not a real
executable bid/ask pair.

The corrected replay uses Kalshi historical bid/ask candle data instead:

```
yes entry price = yes_ask_close
no entry price  = 1 - yes_bid_close
spread_cents    = (yes_ask_close - yes_bid_close) * 100
```

This matters because a NO contract is not safely priced as `1 - yes_ask`.
To buy NO, the backtest has to cross the NO ask, which is equivalent to
`1 - yes_bid` when only the YES bid/ask pair is available.

Kalshi's event candlestick endpoint can truncate dense responses by returning
an `adjusted_end_ts` earlier than the requested hour close. The downloader now
fetches each hourly event in 15-minute chunks, then dedupes by
`market_ticker, ts_end`. This keeps the data faithful because every retained
row is still an observed Kalshi bid/ask candle; it just avoids silently losing
later minutes in a dense event.

The Coinbase BTC data is a market-data proxy for the signal model and
settlement. The replay treats a Coinbase candle as unavailable until the
minute closes:

```
available_at = bucket_start + 1 minute
```

That removes the major candle lookahead bug. The remaining limitation is that
Kalshi's actual settlement source may not exactly equal Coinbase BTC-USD, so
settlement PnL is still a proxy unless official Kalshi settlement/index data
is collected.

The compact committed DB is:

| Table | Rows | Range / Meaning |
|---|---:|---|
| `kalshi_markets` | 235,515 | Hourly cumulative KXBTCD market metadata from roughly Mar 1, 2026 through May 5, 2026 |
| `kalshi_quotes` | 4,667,431 | Observed historical bid/ask minute candles; no forward fill |
| `btc_1m` | 107,737 | Coinbase BTC-USD minute candles from Feb 20, 2026 through May 5, 2026 |

Datamart build on 2026-05-05:

| Table | Rows | Distinct Events | Notes |
|---|---:|---:|---|
| `kalshi_markets` | 235,515 | n/a | SQLite markets plus bid/ask/CSV-inferred market metadata |
| `kalshi_quotes` | 4,667,431 | 1,492 | Bid/ask candle data from SQLite plus chunked event candlestick API |
| `kalshi_csv_prices` | 2,193,773 | 1,470 | Raw downloaded midpoint-style CSV history; not used for fills |
| `btc_1m` | 107,737 | n/a | Coinbase minute OHLCV from 2026-02-20 to 2026-05-05 |

Coverage after chunking:

| Measure | Corrected Bid/Ask | Raw CSV Price | Notes |
|---|---:|---:|---|
| Rows | 4,667,431 | 2,193,773 | Bid/ask has more market/strike observations because it keeps actual quote rows |
| Events | 1,492 | 1,470 | Extra bid/ask events come from SQLite history around the edge of the range |
| Event-minutes | 95,334 | 88,188 | Bid/ask includes some extra SQLite minutes |
| Raw CSV event-minutes covered by bid/ask | 88,160 / 88,188 | n/a | 99.97% coverage of the raw CSV minute grid |

Corrected faithful replay:

Sources:
`backtest_outputs/research_duckdb_chunked_bidask/backtest_research_duckdb_summary.csv`
and
`backtest_outputs/paper_duckdb_chunked_bidask/backtest_paper_duckdb_summary.csv`,
plus the later JS-family runs in
`backtest_outputs/js_robust_chunked_bidask_full/` and
`backtest_outputs/js_guarded_chunked_bidask_full/`.

| Strategy | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Win Rate | Profit Factor | Max Active Premium ($) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| paper_duckdb | 2,532 | -83.39 | 1,362.39 | -6.12% | -84.78 | 50.51% | 0.87 | 7.78 |
| research_duckdb | 173 | 6.25 | 118.75 | 5.26% | -5.93 | 72.25% | 1.20 | 0.77 |
| js_robust_duckdb | 160 | 5.79 | 114.21 | 5.07% | -4.40 | 75.00% | 1.21 | 0.80 |
| js_guarded_duckdb | 120 | 7.56 | 85.44 | 8.85% | -1.98 | 77.50% | 1.40 | 0.82 |

Equity/drawdown chart:
`backtest_outputs/plots/research_equity_corrected_bidask.png`.
Research/JS overlay chart:
`backtest_outputs/plots/js_guarded_vs_research_corrected_bidask.png`.

### Third Strategy: `js_robust`

Implemented in `scripts/backtest_research_duckdb.py` as `--strategy js_robust`.
This is a conservative research variant, not a production live bot yet. It was
designed from train-period diagnostics and market-microstructure constraints:

| Component | Rule |
|---|---|
| Probability model | Same empirical/lognormal model as `research` |
| Market prior | Shrink model probability 25% toward Kalshi bid/ask mid |
| Edge | Taker-fee-adjusted edge must beat an uncertainty-adjusted threshold |
| Execution | Observed historical bid/ask candle only; no forward fill |
| Positioning | One active market per hourly event |
| Entry | Favorite-priced contracts only, `0.55 <= entry <= 0.80` |
| Spread | `spread_cents <= 2` |

Chronological split by event close time:

| Split | Event Close Range (UTC) |
|---|---|
| Train | `< 2026-04-01 00:00` |
| Validation | `2026-04-01 00:00` to `< 2026-04-21 00:00` |
| Test | `>= 2026-04-21 00:00` |

Source: `backtest_outputs/strategy_split_comparison.csv`.
Latest combined source with guarded variants:
`backtest_outputs/js_guarded_strategy_comparison.csv`.

| Strategy | Split | Trades | PnL ($) | Premium ($) | Return on Premium | Win Rate | Max DD ($) |
|---|---|---:|---:|---:|---:|---:|---:|
| paper | train | 1,051 | -41.90 | 560.90 | -7.47% | 49.38% | -43.78 |
| paper | validation | 837 | -17.53 | 453.53 | -3.87% | 52.09% | -24.29 |
| paper | test | 644 | -23.96 | 347.96 | -6.89% | 50.31% | -28.19 |
| research | train | 66 | 4.49 | 46.51 | 9.65% | 77.27% | -1.89 |
| research | validation | 54 | -0.65 | 36.65 | -1.77% | 66.67% | -5.93 |
| research | test | 53 | 2.41 | 35.59 | 6.77% | 71.70% | -3.27 |
| js_robust | train | 64 | 1.69 | 46.31 | 3.65% | 75.00% | -1.99 |
| js_robust | validation | 51 | 0.97 | 36.03 | 2.69% | 72.55% | -4.40 |
| js_robust | test | 45 | 3.13 | 31.87 | 9.82% | 77.78% | -1.69 |
| js_guarded | train | 44 | 1.42 | 31.58 | 4.50% | 75.00% | -1.81 |
| js_guarded | validation | 42 | 2.23 | 29.77 | 7.49% | 76.19% | -1.98 |
| js_guarded | test | 34 | 3.91 | 24.09 | 16.23% | 82.35% | -1.42 |

Interpretation: `js_robust` is less explosive than the original research
strategy on train, but it is positive on train, validation, and test.
`js_guarded` is the current best replay candidate because it improves full
sample PnL, return on premium, held-out test PnL, and max drawdown at once. The
trade count is still small, so this is a stronger candidate for live-capture
paper validation, not proof of a production edge.

### Fourth Strategy: `js_guarded`

Implemented in `scripts/backtest_research_duckdb.py` as
`--strategy js_guarded`. This was selected from train/validation diagnostics,
then checked on the held-out `>= 2026-04-21` test split.

Rules:

| Component | Rule |
|---|---|
| Base model | Same as `js_robust` |
| UTC-hour guard | Do not enter during UTC hours `17` through `23` |
| Moneyness guard | Require `abs(entry_spot - strike) / entry_spot <= 60 bps` |
| Entry/order rules | Same favorite-priced, fee-adjusted, one-position-per-event JS robust rules |

Full corrected replay:

| Strategy | Trades | PnL ($) | Premium ($) | Return on Premium | Win Rate | Max DD ($) |
|---|---:|---:|---:|---:|---:|---:|
| research | 173 | 6.25 | 118.75 | 5.26% | 72.25% | -5.93 |
| js_robust | 160 | 5.79 | 114.21 | 5.07% | 75.00% | -4.40 |
| js_guarded | 120 | 7.56 | 85.44 | 8.85% | 77.50% | -1.98 |

Rejected guarded variants:

| Variant | Train PnL ($) | Validation PnL ($) | Test PnL ($) | Decision |
|---|---:|---:|---:|---|
| `js_guarded_skipmid` | -0.12 | 2.37 | 3.61 | Rejected: train turned negative when replayed as a real strategy |
| `js_guarded_late` | 2.51 | 0.13 | 1.39 | Rejected for primary use: too little validation capacity |

Event-block bootstrap over hourly opportunities estimated
`P(js_guarded PnL > js_robust PnL) ~= 0.70`; the 95% interval for the PnL
difference still crossed zero. Treat the result as materially better but not
statistically locked. The next gate is exact websocket live-capture replay.

Side research: `scripts/diagnose_cross_hour_continuity.py` explores whether
same-strike prices jump when one hourly event closes and the next opens.
The first diagnostic found 161,188 same-strike cross-hour pairs. Extreme
contracts usually had zero median transition, but near-the-money buckets had
larger absolute one-minute transition moves. This is a promising separate
hypothesis, but it is not included in `js_robust` and needs its own
leakage-safe strategy test.

Leakage-safe cross-hour reset test: `scripts/backtest_cross_hour_reset.py`
turns the same-strike continuity idea into an executable strategy that waits
for the next event's first observed quote, uses only bid/ask candle data, and
holds to settlement. It was tested only on train and validation; the test split
was left unused because the idea failed validation.

| Split | Trades | PnL ($) | Premium ($) | Return on Premium | Win Rate | Max DD ($) |
|---|---:|---:|---:|---:|---:|---:|
| train | 63 | -1.21 | 46.21 | -2.62% | 71.43% | -3.97 |
| validation | 51 | -1.81 | 36.81 | -4.92% | 68.63% | -5.35 |

Cross-hour overlay research: `scripts/research_cross_hour_overlay.py` fits a
regularized train-only model using first-minutes current quotes plus previous
adjacent event same-strike boundary information. The cross-hour features did
slightly improve train probability diagnostics, and the validation AUC nudged
higher, but executable trades failed validation after bid/ask and fees.

First observed minute only, train-fitted model:

| Split | Threshold | Trades | PnL ($) | Premium ($) | Return on Premium | Win Rate | Max DD ($) |
|---|---:|---:|---:|---:|---:|---:|---:|
| train | 8c | 108 | 5.01 | 67.99 | 7.37% | 67.59% | -8.19 |
| validation | 8c | 97 | -16.23 | 61.23 | -26.51% | 46.39% | -18.63 |

Interpretation: there is cross-hour statistical structure, but this version is
not a tradable overlay. Do not deploy it unless a new frozen rule passes
validation first.

Inter-market overlap test: `scripts/research_intermarket_overlap.py` trains a
train-only current-market classifier and a train-only inter-market classifier,
then uses the inter-market edge as a filter on existing `research` and
`js_robust` trades. The inter-market model slightly improved train fit, but it
did not beat the current-only model on validation:

| Split | Model | Rows | Log Loss | Brier | AUC |
|---|---|---:|---:|---:|---:|
| train | current_only | 1,195,058 | 0.081834 | 0.024193 | 0.995901 |
| train | intermarket | 1,195,058 | 0.081526 | 0.024082 | 0.995927 |
| validation | current_only | 1,101,798 | 0.065651 | 0.019557 | 0.997353 |
| validation | intermarket | 1,101,798 | 0.066119 | 0.019716 | 0.997318 |

Validation overlap examples:

| Strategy | Rule | Trades | PnL ($) | Return on Premium |
|---|---|---:|---:|---:|
| js_robust | baseline_all | 51 | 0.97 | 2.69% |
| js_robust | intermarket_edge_ge_-5c | 36 | -0.24 | -0.95% |
| research | baseline_all | 54 | -0.65 | -1.77% |
| research | intermarket_edge_ge_-5c | 38 | 0.23 | 0.89% |

Interpretation: the overlap helped `research` validation at a loose threshold,
but hurt `js_robust` and did not show clean model-level validation improvement.
This is not robust enough to deploy.

Dynamic exit research: `scripts/research_dynamic_exits.py` replays take-profit,
stop-loss, trailing, and panic-exit rules on existing faithful trade logs.
Early exits sell at the executable bid side (`yes_bid_close` for long YES,
`1 - yes_ask_close` for long NO) and subtract a second Kalshi taker fee at the
exit price. Holding to settlement has no exit order fee. The script defaults to
`--splits train validation` to preserve the unseen test set.

| Strategy | Split | Best Rule | PnL ($) | Return on Premium | Max DD ($) | Interpretation |
|---|---|---|---:|---:|---:|---|
| js_robust | train | hold | 1.69 | 3.65% | -1.99 | All tested exits underperformed after exit spread/fees |
| js_robust | validation | hold | 0.97 | 2.69% | -4.40 | No routine exit should be added |
| research | train | hold | 4.49 | 9.65% | -1.89 | Early exits clipped winners |
| research | validation | panic_bid_le_40 | -0.03 | -0.08% | -2.99 | Defensive only; it hurt train and is not robust |

Model emergency exit research: `scripts/research_model_emergency_exits.py`
tests a rarer rule: exit only when the updated held-side model win probability
is low and the executable bid, after a second taker fee, is richer than the
model's hold value. The most stable frozen candidate so far is
`pwin_le_30_mkt_plus_5`: exit when held-side `p_win <= 30%` and
`exit_bid - exit_fee >= p_win + 5c`.

| Strategy | Split | Rule | Early Exits | PnL ($) | Return on Premium | Max DD ($) |
|---|---|---|---:|---:|---:|---:|
| js_robust | train | hold | 0 | 1.69 | 3.65% | -1.99 |
| js_robust | train | pwin_le_30_mkt_plus_5 | 11 | 1.93 | 4.17% | -1.72 |
| js_robust | validation | hold | 0 | 0.97 | 2.69% | -4.40 |
| js_robust | validation | pwin_le_30_mkt_plus_5 | 7 | 1.10 | 3.05% | -4.62 |
| js_robust | test | hold | 0 | 3.13 | 9.82% | -1.69 |
| js_robust | test | pwin_le_30_mkt_plus_5 | 2 | 2.86 | 8.97% | -1.84 |
| research | train | hold | 0 | 4.49 | 9.65% | -1.89 |
| research | train | pwin_le_30_mkt_plus_5 | 14 | 4.90 | 10.54% | -1.19 |
| research | validation | hold | 0 | -0.65 | -1.77% | -5.93 |
| research | validation | pwin_le_30_mkt_plus_5 | 11 | 1.68 | 4.58% | -3.88 |
| research | test | hold | 0 | 2.41 | 6.77% | -3.27 |
| research | test | pwin_le_30_mkt_plus_5 | 8 | 1.89 | 5.31% | -2.61 |

Interpretation: unlike blunt stops, this is a plausible emergency-only exit
candidate, but it did not improve held-out test returns after being frozen.
It may be useful only as a drawdown-control paper experiment, not as a
production alpha improvement.

Event-prefix splits:

| Strategy | Event Prefix | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Win Rate | Profit Factor |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| paper_duckdb | `KXBTCD-26MAR` | 874 | -50.29 | 465.29 | -10.81% | -51.68 | 47.48% | 0.79 |
| paper_duckdb | `KXBTCD-26APR` | 913 | -24.64 | 496.64 | -4.96% | -27.77 | 51.70% | 0.90 |
| paper_duckdb | `KXBTCD-26MAY` | 144 | -8.92 | 79.92 | -11.16% | -11.67 | 49.31% | 0.78 |
| research_duckdb | `KXBTCD-26MAR` | 57 | 4.04 | 39.96 | 10.11% | -2.22 | 77.19% | 1.45 |
| research_duckdb | `KXBTCD-26APR` | 64 | 1.55 | 43.45 | 3.57% | -4.44 | 70.31% | 1.13 |
| research_duckdb | `KXBTCD-26MAY` | 7 | 1.45 | 4.55 | 31.87% | -0.66 | 85.71% | 3.20 |

Looser replay allowing multiple active markets in the same hourly event:

Source: `backtest_outputs/research_duckdb_allow_multi_event/backtest_research_duckdb_summary.csv`

| Strategy | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Win Rate | Profit Factor | Max Active Premium ($) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| research_duckdb | 242 | 2.87 | 167.13 | 1.72% | -14.29 | 70.25% | 1.06 | 7.22 |

Interpretation: this is the only execution-faithful historical replay in the
repo right now. It removes the Coinbase candle timestamp lookahead, does not
forward-fill stale Kalshi prices, uses observed bid/ask candles for entries,
charges Kalshi taker fees, and defaults to one active position per event. It
still uses historical minute candle closes rather than full orderbook-depth
snapshots, and the BTC cache reported 15 internal minute gaps on the full
build. Those gaps are not forward-filled by this replay.

What we learned:

| Finding | Practical meaning |
|---|---|
| The old CSV assumed-spread backtests were too optimistic | They used chart/export prices plus an assumed spread, so their large March/April returns are not production-grade evidence |
| The first corrected replay was still sparse | The old bid/ask downloader ignored Kalshi `adjusted_end_ts`, so dense event hours were often truncated |
| Chunking fixed most of that incompleteness | Corrected bid/ask now covers 99.97% of raw CSV event-minutes while remaining observed bid/ask data only |
| Fees materially matter | The corrected replay includes Kalshi taker fees in both edge filtering and realized PnL |
| The original paper strategy does not survive the corrected replay | It took 2,532 trades and lost $83.39, or -6.12% on premium |
| The research strategy survives, but the edge is smaller | It took 173 trades and made $6.25, or +5.26% on premium |
| More trades was worse for research | Allowing multiple active markets per hourly event dropped return on premium to +1.72% and increased drawdown |
| The live bot should stay pinned to the corrected research constraints | One selected signal per event, real bid/ask pricing, taker fees, duplicate protection, and bankroll sizing are part of the tested setup |
| There is still live-execution risk | Minute candles do not prove fillability, queue priority, exact depth, or sub-minute latency |
| There is still settlement-index risk | Coinbase BTC is used as the settlement proxy; exact Kalshi settlement/index data would be better |

### May 5 Live Replay Audit

Source data: `data/research_datamart/research.duckdb`, rebuilt
`2026-05-06T00:05:18Z` from corrected Kalshi bid/ask event candles plus the
Coinbase BTC minute cache. The recent corrected bid/ask window now runs from
`KXBTCD-26APR2519` through `KXBTCD-26MAY0520`. Four synthetic hourly slots
inside that range, `KXBTCD-26APR3002` through `KXBTCD-26APR3005`, returned
Kalshi API 404s and have no local raw or corrected data, so they are marked as
unavailable rather than filled.

Replay window: `2026-05-05T05:00:00Z` through `2026-05-06T00:05:18Z`
(`May 4 11:00 PM MDT` through the latest rebuilt data).

| Run | Path | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) |
|---|---|---:|---:|---:|---:|---:|
| strict research replay | `backtest_outputs/may5_0500_to_now_research_final` | 1 | 0.28 | 0.72 | 38.89% | 0.00 |
| research replay allowing multiple same-event markets | `backtest_outputs/may5_0500_to_now_research_final_multi` | 4 | 1.17 | 2.83 | 41.34% | 0.00 |
| original paper thresholds on corrected data | `backtest_outputs/may5_0500_to_now_paper_final` | 30 | -1.88 | 16.88 | -11.14% | -4.71 |

Live reconciliation from `~/.btc_kalshi_bot/research_live_trades.db` over the
same window found 9 filled research trades, each sized to 3 contracts, with
estimated realized PnL `-$2.0088` on `$17.0088` of premium plus fees. Only 1 of
those live fills matched the strict replay; only 2 matched the looser
same-event replay. Reconciliation output:
`backtest_outputs/may5_0500_to_now_live_reconciliation.csv`.

What failed:

| Issue | Meaning |
|---|---|
| Live duplicate protection was ticker-level, not event-level | The strict replay assumes one active position per hourly event, but live could buy a second market in the same `KXBTCD-...HH` event on a later scan. This was patched in `scripts/btc_1hr_research_live.py`. |
| Historical Kalshi replay is minute-candle bid/ask, not second-level book replay | Live saw orderbook prices inside a minute that the replay cannot know until the candle closes; using the minute low/high would be lookahead. |
| Live spot is Coinbase spot, replay spot is Coinbase completed-minute candle close | In the audit the live-vs-replay BTC spot difference reached about `$56`, enough to move edge filters on near-the-money contracts. |

Current implication: the corrected DuckDB replay is still the best historical
replay in the repo, but it is not an exact live execution simulator. Exact
matching requires recording decision-time Coinbase spot snapshots and
decision-time Kalshi orderbook snapshots.

No-leakage replay rule: valid replays must use only causal Kalshi quotes. In
`scripts/backtest_research_live_approx.py`, that means `--quote-timing causal`.
The `--quote-timing containing` mode is blocked unless
`--allow-lookahead-diagnostic` is explicitly passed, because it uses the
current minute's completed candle and therefore includes information from after
the bot's decision second.

No-leakage May 5 rerun from the actual overnight bot start
(`2026-05-05T06:29:27Z`) through `2026-05-06T00:20:25Z`:

| Replay | Path | Trades | PnL ($) | Live Trades | Live PnL ($) | Matched Live Setups |
|---|---|---:|---:|---:|---:|---:|
| Logged scans, old ticker dedupe, causal quotes | `backtest_outputs/may5_no_leak_live_replay/replay_causal_close_ticker.csv` | 3 | 2.70 | 9 | -2.0088 | 2/9 |
| Logged scans, fixed event dedupe, causal quotes | `backtest_outputs/may5_no_leak_live_replay/replay_causal_close_event.csv` | 1 | 0.85 | 9 | -2.0088 | 2/9 |
| Canonical research replay, causal quotes | `backtest_outputs/may5_no_leak_canonical_research` | 1 | 0.28 per contract | n/a | n/a | n/a |

Interpretation: without leakage, the replay misses most of the live fills. That
is the honest result from the current historical candle dataset.

The paper row uses the original `scripts/btc_1hr_paper.py` signal thresholds
and simple rolling BTC volatility model, but applies the official taker fee
formula in both edge filtering and PnL. That fee correction makes it stricter
than the old paper script's fixed `0.70c` fee assumption.

The older reports below are retained for comparison only. They used CSV prices
with an assumed spread, so they are useful diagnostics but not production-grade
evidence of edge.

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

If running live money, use `scripts/btc_1hr_risk_adjusted_research_live.py`
rather than the raw research, paper, or hardened scripts. The wrapper uses the
websocket research executor but applies the risk-adjusted sizing policy. Use
`scripts/btc_1hr_research_live.py` directly only for controlled diagnostics,
paper runs, or strategy-engine work. The old March/April selection was based
on assumed-spread CSV replay; after corrected DuckDB replay and real fills, the
edge should be treated as modest and sizing-sensitive, not proof of unlimited
live profitability.

Default live command:

```powershell
python scripts\btc_1hr_risk_adjusted_research_live.py
```

The websocket executor writes a live replay database at
`~/.btc_kalshi_bot/research_live_capture.duckdb`. That capture is intentionally
outside the repo and should not be committed. It stores Kalshi orderbook
reconstructed top-of-book, lifecycle/private messages, BTC websocket
ticks, signal scans, and order decisions. Raw orderbook deltas and raw snapshot
levels are suppressed by default to avoid overwhelming DuckDB during live
trading; enable `--capture-raw-ws` only when intentionally running a short
diagnostic capture. This is the high-quality dataset we need for future
no-leakage replay testing.

`scripts/btc_1hr_js_guarded_shadow.py` mirrors this websocket path for the
`js_guarded` candidate but defaults to `--paper`, `--shadow-bankroll 1000`,
`~/.btc_kalshi_bot/js_guarded_shadow_trades.db`, and
`~/.btc_kalshi_bot/js_guarded_shadow_capture.duckdb`. It prints a shadow PnL
report every minute and near the top of each hour:

```
SHADOW report strategy=js_guarded start=$1000.00 equity=$1000.00 realized_pnl=$0.00 bankroll_return=0.000% ...
```

Live execution safeguards:

| Guard | Behavior |
|---|---|
| Websocket market data | Maintains an in-memory Kalshi orderbook from authenticated `orderbook_delta` snapshots and deltas, plus live BTC spot from Kraken websocket with Coinbase/cache support elsewhere in the engine. |
| Event subscription control | Uses Kalshi lifecycle messages plus a low-rate metadata refresh to subscribe only to the current eligible KXBTCD event and remove old event markets. |
| Stale-event refresh failure | If event metadata refresh fails while the prior event is no longer eligible, the bot clears the event set and refuses to scan until a fresh eligible event is loaded. |
| Full initial book gate | Does not scan/trade a new hourly event until every market in that event has received its initial websocket orderbook snapshot. |
| Reconnect/gap safety | Clears in-memory books on reconnect, ignores duplicate/out-of-order websocket messages, and reconnects on any Kalshi sequence gap before trading again. |
| Fresh BTC spot gate | Refuses to scan/trade unless the BTC websocket feed is connected and the spot tick is recent. |
| Correct event | Trades only the current KXBTCD hourly event whose API close time matches the event ticker's New York close hour. |
| Correct market | Trades only cumulative `-T` markets inside that exact event; bucket markets and next-day events are rejected. |
| Duplicate protection | Blocks existing DB `submitted`, `filled`, or `partial_filled` rows, all nonzero live Kalshi positions, and an atomic per-event SQLite lock before any paper/live order attempt. |
| Bankroll control | Reads Kalshi `balance` and `portfolio_value` every live cycle, blocks orders that exceed available cash, per-market exposure, or total active exposure. |
| Risk-adjusted sizing | Keeps the backtested one-contract signal filter, then sizes the final live order with conservative Kelly, bankroll, exposure, entry-risk, and visible-depth caps. |
| Order pricing | Uses real YES and NO orderbook bids from the maintained websocket book. YES ask is `1 - best NO bid`; NO ask is `1 - best YES bid`. |
| Order type | Uses Kalshi V2 FOK orders with `cancel_order_on_pause` and self-trade prevention. |
| 409 / FOK no-fill sync | If Kalshi returns HTTP 409, the bot queries `/portfolio/orders` by `client_order_id`; canceled zero-fill orders become `not_filled`, partials stay protected, and the websocket loop continues. |
| Unknown order state | Inserts a local `submitted` row before POSTing; timeout/unknown state blocks re-entry instead of retrying blindly. |
| Capture load control | Captures top-of-book and decisions by default, coalesces scan triggers, and disables raw websocket delta capture unless explicitly requested. |

### Multi-Crypto Scout

Two scripts extend the corrected DuckDB replay method beyond BTC hourly
KXBTCD:

```powershell
python scripts\build_crypto_research_datamart.py --out-dir data\crypto_research_datamart --max-hourly-events 12 --max-15m-events 48 --workers 2 --chunk-minutes 15
python scripts\backtest_crypto_research.py --db data\crypto_research_datamart\crypto_research.duckdb --output-dir backtest_outputs\crypto_research
```

The datamart builder uses Kalshi's public cutoff endpoint and recorded
`market_settled_ts=2026-03-06T00:00:00Z` on this run. Events before that cutoff
route through historical market endpoints; recent events use event
candlesticks. It keeps only observed `yes_bid`/`yes_ask` minute candles and
uses `NO ask = 1 - YES bid`, the same corrected execution convention as the
BTC replay. Coinbase spot data is stored with `available_at = bucket_start + 1
minute`.

Primary 24-hour crypto focus run:

```powershell
python scripts\build_crypto_research_datamart.py --out-dir data\crypto_research_datamart --series KXBTC15M KXETH15M KXETHD --start 2026-05-05T04:00:00Z --end 2026-05-06T04:00:00Z --max-hourly-events 40 --max-15m-events 120 --workers 1 --chunk-minutes 15
python scripts\backtest_crypto_research.py --db data\crypto_research_datamart\crypto_research.duckdb --output-dir backtest_outputs\crypto_research_primary_24h_final_20260505_230624 --series KXBTC15M --series KXETH15M --series KXETHD
```

Primary 24-hour datamart, built `2026-05-06T04:42:43Z`:

| Item | Count / Range |
|---|---:|
| Kalshi events | 216 |
| Kalshi markets | 1,957 |
| Kalshi bid/ask rows | 36,450 |
| Coinbase spot rows | 32,606 |
| Series | `KXBTC15M`, `KXETH15M`, `KXETHD` |
| Event close span | `2026-05-05T04:00:00Z` through `2026-05-06T03:45:00Z` |
| Fetch failures | 0 |

The primary split is chronological 60/20/20 by unique event close timestamp,
so simultaneous BTC/ETH close-time buckets stay in the same split:

| Split | Events | Close Range UTC |
|---|---:|---|
| Train | 129 | `2026-05-05T04:00:00Z` to `2026-05-05T18:00:00Z` |
| Validation | 42 | `2026-05-05T18:15:00Z` to `2026-05-05T22:45:00Z` |
| Test | 45 | `2026-05-05T23:00:00Z` to `2026-05-06T03:45:00Z` |

Primary 24-hour backtest summary:

| Strategy | Split | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Read |
|---|---|---:|---:|---:|---:|---:|---|
| `ported_research` | train | 1 | 0.28 | 0.72 | 38.89% | 0.00 | Sparse ETHD-style hit |
| `ported_research` | test | 2 | 0.94 | 1.06 | 88.68% | 0.00 | Too few trades to trust |
| `ported_js_guarded` | train | 1 | 0.28 | 0.72 | 38.89% | 0.00 | No validation/test trades |
| `crypto_fair_value` | train | 13 | -0.58 | 8.58 | -6.76% | -2.00 | Fails train |
| `crypto_fair_value` | validation | 2 | -0.04 | 1.04 | -3.85% | -0.44 | Fails validation |
| `crypto_fair_value` | test | 3 | 1.29 | 1.71 | 75.44% | 0.00 | Positive only after failing train/validation |
| `crypto_fair_value_loose` | train | 22 | -0.19 | 14.19 | -1.34% | -1.51 | Too weak |
| `crypto_fair_value_loose` | validation | 2 | -0.04 | 1.04 | -3.85% | -0.44 | Fails validation |
| `crypto_fair_value_loose` | test | 9 | 0.08 | 5.92 | 1.35% | -1.56 | Not enough edge |
| `micro_updown_guarded` | train | 11 | -0.59 | 6.59 | -8.95% | -1.51 | Fails train |
| `micro_updown_guarded` | validation | 1 | 0.40 | 0.60 | 66.67% | 0.00 | One trade only |
| `micro_updown_guarded` | test | 2 | -0.32 | 1.32 | -24.24% | -0.67 | Fails test |
| `updown15_fast` | train | 22 | 0.07 | 13.93 | 0.50% | -1.39 | No real edge |
| `updown15_fast` | validation | 3 | 0.35 | 1.65 | 21.21% | -0.39 | Tiny positive |
| `updown15_fast` | test | 7 | -1.57 | 4.57 | -34.35% | -2.27 | Reject |
| `eth_1h_fair_value` | train | 4 | 0.35 | 2.65 | 13.21% | -0.36 | Interesting but sparse |
| `eth_1h_fair_value` | test | 2 | 0.94 | 1.06 | 88.68% | 0.00 | Too few trades to deploy |

Read: the 15-minute up/down ideas are not deployment candidates from this
primary run. ETH 1-hour above/below remains the most interesting non-BTC path,
but the 24-hour sample has only six total `eth_1h_fair_value` trades and needs
multi-week data before promotion.

Longer-term data note: a 7-day or 30-day version of this primary dataset is
the next research gate, but it should run with a slow Kalshi throttle or
off-hours because it competes with the live bot for `/markets` and candlestick
rate limits. The 24-hour run above is useful for plumbing and early rejection,
not enough to declare a new production edge.

Earlier broad scout dataset:

| Item | Count / Range |
|---|---:|
| Kalshi events | 336 |
| Kalshi markets | 6,878 |
| Kalshi bid/ask rows | 110,499 |
| Coinbase spot rows | 80,808 |
| Families | 48 hourly above/below, 48 hourly range, 240 15-minute up/down events |
| Series | `KXETHD`, `KXSOLD`, `KXDOGED`, `KXXRPD`, `KXETH`, `KXSOLE`, `KXDOGE`, `KXXRP`, `KXBTC15M`, `KXETH15M`, `KXSOL15M`, `KXDOGE15M`, `KXXRP15M` |
| Event close span | `2026-05-05T15:00:00Z` through `2026-05-06T02:45:00Z` |

Backtest split is chronological 60/20/20 by event close inside that collected
sample:

| Split | Events | Close Range UTC |
|---|---:|---|
| Train | 201 | `2026-05-05T15:00:00Z` to `2026-05-05T22:00:00Z` |
| Validation | 67 | `2026-05-05T22:00:00Z` to `2026-05-06T00:15:00Z` |
| Test | 68 | `2026-05-06T00:15:00Z` to `2026-05-06T02:45:00Z` |

Results are fee-adjusted and settle against Kalshi's market result when
available. The first run was a combined replay grouped by series. After that,
each series was run again by itself with `--series <ticker>` so the per-market
results are not hidden by the combined split:

```powershell
python scripts\backtest_crypto_research.py --db data\crypto_research_datamart\crypto_research.duckdb --series KXDOGE --output-dir backtest_outputs\crypto_research_by_series\KXDOGE
```

Full standalone grids are written here:

| File | Contents |
|---|---|
| `backtest_outputs/crypto_research_by_series/all_series_strategy_grid.csv` | One row for every series and strategy, including zero-trade rows |
| `backtest_outputs/crypto_research_by_series/all_series_strategy_grid_by_split.csv` | One row for every series, strategy, and train/validation/test split |

<details open>
<summary><strong>Combined replay: strategy summary</strong></summary>

| Strategy | Split | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| `crypto_fair_value` | train | 11 | 2.54 | 7.46 | 34.05% | -0.67 | Broad cross-crypto above/up/down fair-value rule |
| `crypto_fair_value` | validation | 3 | 0.22 | 1.78 | 12.36% | -0.44 | Positive but tiny sample |
| `crypto_fair_value` | test | 2 | 0.83 | 1.17 | 70.94% | 0.00 | Two winners only |
| `micro_updown_guarded` | train | 8 | 1.79 | 5.21 | 34.36% | -0.67 | 15-minute up/down only |
| `micro_updown_guarded` | validation | 1 | -0.67 | 0.67 | -100.00% | -0.67 | Failed validation on one trade |
| `micro_updown_guarded` | test | 2 | 0.83 | 1.17 | 70.94% | 0.00 | Same two test trades as `crypto_fair_value` |
| `ported_research` | train | 2 | 0.68 | 1.32 | 51.52% | 0.00 | Existing BTC research logic ported directly |
| `ported_research` | validation | 1 | 0.46 | 0.54 | 85.19% | 0.00 | Too sparse |
| `ported_js_guarded` | train | 2 | 0.68 | 1.32 | 51.52% | 0.00 | BTC UTC guard leaves almost no trades |
| `range_fair_value` | train | 3 | 0.05 | 1.95 | 2.56% | -0.81 | Range markets look weak after fees |
| `range_fair_value` | validation | 1 | 0.39 | 0.61 | 63.93% | 0.00 | Too sparse |

</details>

<details>
<summary><strong>Standalone per-series runs: nonzero trades</strong></summary>

| Series | Market | Strategy | Split | Trades | PnL ($) | Premium ($) | Return on Premium | Max DD ($) |
|---|---|---|---|---:|---:|---:|---:|---:|
| `KXDOGE15M` | DOGE 15m up/down | `crypto_fair_value` | train | 1 | 0.24 | 0.76 | 31.58% | 0.00 |
| `KXDOGE15M` | DOGE 15m up/down | `micro_updown_guarded` | train | 1 | 0.24 | 0.76 | 31.58% | 0.00 |
| `KXETH` | ETH 1h range | `range_fair_value` | train | 3 | 0.05 | 1.95 | 2.56% | -0.81 |
| `KXETH15M` | ETH 15m up/down | `crypto_fair_value` | train | 2 | 0.72 | 1.28 | 56.25% | 0.00 |
| `KXETH15M` | ETH 15m up/down | `crypto_fair_value` | test | 1 | 0.35 | 0.65 | 53.85% | 0.00 |
| `KXETH15M` | ETH 15m up/down | `micro_updown_guarded` | train | 2 | 0.72 | 1.28 | 56.25% | 0.00 |
| `KXETH15M` | ETH 15m up/down | `micro_updown_guarded` | validation | 1 | -0.67 | 0.67 | -100.00% | -0.67 |
| `KXETH15M` | ETH 15m up/down | `micro_updown_guarded` | test | 1 | 0.35 | 0.65 | 53.85% | 0.00 |
| `KXETHD` | ETH 1h above/below | `crypto_fair_value` | train | 1 | 0.26 | 0.74 | 35.14% | 0.00 |
| `KXETHD` | ETH 1h above/below | `crypto_fair_value` | validation | 2 | 0.02 | 0.98 | 2.04% | -0.44 |
| `KXETHD` | ETH 1h above/below | `ported_research` | validation | 1 | 0.46 | 0.54 | 85.19% | 0.00 |
| `KXSOL15M` | SOL 15m up/down | `crypto_fair_value` | train | 1 | 0.34 | 0.66 | 51.52% | 0.00 |
| `KXSOL15M` | SOL 15m up/down | `crypto_fair_value` | test | 1 | 0.48 | 0.52 | 92.31% | 0.00 |
| `KXSOL15M` | SOL 15m up/down | `micro_updown_guarded` | train | 1 | 0.34 | 0.66 | 51.52% | 0.00 |
| `KXSOL15M` | SOL 15m up/down | `micro_updown_guarded` | test | 1 | 0.48 | 0.52 | 92.31% | 0.00 |
| `KXSOL15M` | SOL 15m up/down | `ported_js_guarded` | train | 1 | 0.34 | 0.66 | 51.52% | 0.00 |
| `KXSOL15M` | SOL 15m up/down | `ported_research` | train | 1 | 0.34 | 0.66 | 51.52% | 0.00 |
| `KXSOLD` | SOL 1h above/below | `crypto_fair_value` | train | 2 | 0.58 | 1.42 | 40.85% | 0.00 |
| `KXSOLD` | SOL 1h above/below | `ported_js_guarded` | train | 1 | 0.34 | 0.66 | 51.52% | 0.00 |
| `KXSOLD` | SOL 1h above/below | `ported_research` | train | 1 | 0.34 | 0.66 | 51.52% | 0.00 |
| `KXXRP` | XRP 1h range | `range_fair_value` | validation | 1 | 0.39 | 0.61 | 63.93% | 0.00 |
| `KXXRP15M` | XRP 15m up/down | `crypto_fair_value` | train | 4 | 0.40 | 2.60 | 15.38% | -0.67 |
| `KXXRP15M` | XRP 15m up/down | `crypto_fair_value` | validation | 1 | 0.20 | 0.80 | 25.00% | 0.00 |
| `KXXRP15M` | XRP 15m up/down | `micro_updown_guarded` | train | 4 | 0.49 | 2.51 | 19.52% | -0.67 |

</details>

<details>
<summary><strong>Standalone per-series runs: no trades</strong></summary>

| Series | Market | Events | Markets | Quote Rows | Result |
|---|---|---:|---:|---:|---|
| `KXBTC15M` | BTC 15m up/down | 48 | 48 | 720 | No trades passed filters |
| `KXDOGE` | DOGE 1h range | 12 | 689 | 8,879 | No trades passed filters |
| `KXDOGED` | DOGE 1h above/below | 12 | 689 | 10,073 | No trades passed filters |
| `KXSOLE` | SOL 1h range | 12 | 900 | 11,738 | No trades passed filters |
| `KXXRPD` | XRP 1h above/below | 12 | 865 | 14,573 | No trades passed filters |

</details>

Interpretation: this scout found a possible 15-minute/up-down and non-BTC
above/below signal worth collecting more data for, but the sample is only
about 12 hours and the trade counts are far too small for production. DOGE
hourly was included as both `KXDOGE` and `KXDOGED`; neither standalone run
found a fee-adjusted trade. The BTC `js_guarded` time-of-day guard should not
be blindly ported to these markets. Range buckets did not show a robust enough
edge after bid/ask and fees.




## Historical Runs

### Base Paper Bot (15 Hour Run)
- **Total Trades:** 61 (Settled: 54)
- **Net PnL:** +0.532 USD
- **Win Rate:** 53.7%

### Hardened Bot (15 Hour Run)
- **Total Trades:** 64 (Settled: 56)
- **Net PnL:** +1.498 USD
- **Win Rate:** 50.0%

