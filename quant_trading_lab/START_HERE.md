# START_HERE — how to run quant_trading_lab

## 1. One-time setup
```bash
cd quant_trading_lab
python3 -m pip install -r requirements.txt
cp .env.example .env
# edit .env to add whatever keys you have. Anything you skip → that
# provider is disabled, others keep working.
```
Required Python: 3.10+.

## 2. Verify the install
```bash
python3 -m pytest tests/ -q
```
You should see `10 passed`.

## 3. First notebook run
```bash
jupyter lab
```
Open and run **in order**:

| # | Notebook | What it does | Needs |
|---|---|---|---|
| 00 | `00_setup_and_api_key_check.ipynb` | Inits SQLite, prints which providers are usable | nothing |
| 01 | `01_data_ingestion.ipynb` | Pulls ETF panel + (optional) FRED + crypto funding history to cache | yfinance (free) |
| 02 | `02_spy_vol_target_backtest.ipynb` | Backtests SPY vol-target with leakage pre-flight + tearsheet | yfinance (free) |
| 03 | `03_crypto_funding_arb_backtest.ipynb` | BTC/ETH spot-perp funding-arb simulation | public Binance API (free) |
| 04 | `04_tsmom_backtest.ipynb` | 17-ETF cross-asset TSMOM monthly | yfinance (free) |
| 05 | `05_prediction_market_signal_research.ipynb` | Browse Kalshi/Polymarket markets, lead-lag analysis skeleton | none (auth needed only for orders) |
| 06 | `06_combined_portfolio_backtest.ipynb` | Inverse-vol combination of SPY-VT + TSMOM | yfinance (free) |
| 07 | `07_live_paper_trading_dashboard.ipynb` | Paper-trading loop, risk monitor, dashboard | nothing for the in-memory broker; Alpaca optional |
| 08 | `08_monthly_performance_report.ipynb` | Writes markdown + PNGs to `data/reports/<run_id>/` | nothing |

## 4. Run the full pipeline end-to-end (no notebook)
```bash
# from project root
python3 -c "
from src.data.yfinance_client import YFinanceClient
from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.backtest.engine import run_backtest, EngineConfig
from src.reports.monthly_report import write_monthly_report
import pandas as pd
yf = YFinanceClient()
spy = yf.get_daily_bars('SPY', start='2015-01-01')
prices = pd.DataFrame({'SPY': spy['adj_close']})
res = run_backtest(SPYVolTargetStrategy(VolTargetConfig()), prices,
                   EngineConfig(strategy_name='spy_vt'))
print(res['metrics'])
out = write_monthly_report(res, res['run_id'], 'spy_vt')
print('Report:', out)
"
```
This is the deliverable smoke test — it pulls real SPY data, runs the
backtest with the pre-flight leakage check, writes a tearsheet + report.

## 5. Recommended deployment order
1. Setup → API key check (NB 00)
2. Pull ETF history (NB 01)
3. Backtest SPY vol-target (NB 02)
4. Backtest TSMOM (NB 04)
5. Pull crypto funding (NB 01 last cell)
6. Backtest funding arb (NB 03)
7. Build PM snapshots + run lead-lag (NB 05)
8. Combine portfolio (NB 06)
9. Paper trade for **30–60 days minimum** (NB 07) before any real money.
10. Monthly performance report (NB 08).

## 6. Switching to a different data provider
Each client implements the same interface (`BarsClient`, `FundingClient`, etc.).
To swap from yfinance to Polygon for ETFs:
```python
# in any notebook
from src.data.polygon_client import PolygonClient
yf = PolygonClient()   # same call signature; needs POLYGON_API_KEY
```

## 7. Going live (deliberately hard)
Live trading requires **two independent** env vars set to `true`:
```bash
LIVE_TRADING_ENABLED=true
I_UNDERSTAND_REAL_MONEY_RISK=true
```
…AND working credentials for the venue. Even with both, the system enforces
`RiskLimits` (drawdown stops, position caps). See **RISK_POLICY.md**.
