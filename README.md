# Kalshi-Trading-Bot

Two strategies sit alongside the original Kalshi notebooks:

## 1. `gnn_arbitrage/` — GNN triangular arbitrage backtester

Currencies are nodes, executable rates are directed edges, and a small
message-passing GNN scores each edge for the probability that it sits on a
profitable cycle. Training labels come from exhaustive Bellman-Ford-style
cycle search on synthetic FX snapshots; at test time the backtester picks the
highest-net-profit triangle whose edges all clear a confidence threshold and
trades it with configurable fees and slippage.

```
python scripts/run_gnn_arbitrage.py
```

Modules: `data.py` (synthetic feed with injected arbs), `graph.py`,
`arbitrage.py` (ground-truth cycle search), `gnn_model.py` (plain-PyTorch
edge-classifying GNN), `backtester.py` (GNN + oracle baseline).

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
