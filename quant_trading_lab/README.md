# quant_trading_lab

Personal systematic-trading research + paper-trading framework.

Built around real APIs, no-lookahead discipline, strict risk limits, and a
default of **paper trading only**. Live execution is gated behind two
explicit safety locks.

## What this is
- **Notebook-driven** research and live monitoring; clean Python modules underneath.
- **Multiple sleeves:** SPY vol-target, cross-asset TSMOM, crypto spot-perp funding arb, prediction-market lead-lag research.
- **Real backtest engine** with costs, slippage, drawdown de-risking, kill switches, leakage pre-flight checks.
- **Paper broker** built in; Alpaca paper supported via REST.
- **Storage:** SQLite by default (drop-in Postgres via `DATABASE_URL`).
- **Reports:** tearsheet + monthly markdown report with charts.

## What this is not
- **Not a turnkey live bot.** Live execution paths are intentionally minimal and gated.
- **Not a backtest framework that hides bugs.** It runs leakage probes before each backtest and aborts if they fail.
- **Not provider-locked.** Free tiers (yfinance, public crypto, public PM data) get you a working pipeline; paid feeds (Polygon, Alpaca, Databento) plug in via the same `Client` interface.

## Quick start
1. Install: `pip install -r requirements.txt`
2. Copy `.env.example` → `.env` and fill in keys you have.
3. Open `notebooks/00_setup_and_api_key_check.ipynb` and run it.
4. Then walk the numbered notebooks in order — they build on each other.

## Project layout
- `notebooks/00..08*.ipynb` — the user-facing surface
- `src/config.py` — env + provider registry + safety locks
- `src/data/*` — data clients (yfinance/Polygon/Alpaca/FRED/Kalshi/Polymarket/crypto/Databento), schemas, SQLite + parquet I/O
- `src/strategies/*` — SPY vol-target, TSMOM, funding arb, PM lead-lag
- `src/backtest/*` — engine, portfolio, execution, costs, metrics, validation
- `src/live/*` — paper + Alpaca broker, risk monitor, scheduler, alerts
- `src/reports/*` — tearsheet, monthly report, plots
- `src/utils/*` — time/log/math/leakage helpers
- `tests/` — pytest suite (lookahead, engine, signals, metrics)
- `data/` — local parquet cache + SQLite DB + report output

See also: **START_HERE.md**, **USER_ACTION_REQUIRED.md**, **BIAS_AND_VALIDATION.md**, **RISK_POLICY.md**.
