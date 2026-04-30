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

## Setup

```
pip install -r requirements.txt
```
