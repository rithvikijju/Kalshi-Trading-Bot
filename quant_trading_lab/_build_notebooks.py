"""Generate the 9 notebooks under notebooks/ using nbformat so cell IDs,
nbformat_minor, and metadata are properly set. Run once."""
import nbformat as nbf
from pathlib import Path

NB_DIR = Path(__file__).parent / "notebooks"
NB_DIR.mkdir(exist_ok=True)


def mkmd(src): return nbf.v4.new_markdown_cell(src)
def mkcode(src): return nbf.v4.new_code_cell(src)


def save(name, cells):
    nb = nbf.v4.new_notebook()
    nb.cells = cells
    nb.metadata = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    }
    nbf.validate(nb)
    nbf.write(nb, NB_DIR / name)


PREAMBLE = """import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.abspath(".."))
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
%matplotlib inline"""

# ============================================================
# 00 — Setup + API key check
# ============================================================
save("00_setup_and_api_key_check.ipynb", [
    mkmd("""# 00 — Setup + API key check

Run this first. Verifies your environment, checks which API keys are present,
initialises the SQLite database, and prints the provider availability matrix.

**Required:** Python 3.10+, dependencies from `requirements.txt`, and a `.env` file
(copy from `.env.example`).

**Output:** clear list of which strategies you can run with the keys you have."""),
    mkcode(PREAMBLE),
    mkcode("""from src.config import check_keys, provider_status, SAFETY, KEYS
from src.data.storage import init_db

init_db()
print('DB initialized at data/lab.sqlite')

ks = check_keys()
print('\\nKeys present:')
for p in ks['present']: print(f'  ✓ {p}')
print('\\nKeys MISSING (provider will be skipped):')
for p in ks['missing']: print(f'  ✗ {p}')
print(f'\\nLIVE_TRADING_ENABLED = {ks["live_enabled"]}')
print(f'I_UNDERSTAND_REAL_MONEY_RISK = {ks["risk_acknowledged"]}')
print(f'→ can trade live: {ks["can_trade_live"]}')"""),
    mkmd("### Provider availability matrix"),
    mkcode("""import pandas as pd
pd.DataFrame(provider_status())"""),
    mkmd("""### What you can run with current keys

If yfinance + FRED are 'present', you can already:
- Backtest SPY vol-target (NB 02)
- Backtest TSMOM (NB 04)
- Backtest combined portfolio (NB 06)

Public crypto data needs no keys (NB 03 funding arb works).

Polygon/Alpaca unlock intraday + paper execution (NB 07).

Kalshi/Polymarket public market data works without auth; signed Kalshi auth only needed for order placement (NB 05 doesn't need it)."""),
])

# ============================================================
# 01 — Data ingestion
# ============================================================
save("01_data_ingestion.ipynb", [
    mkmd("""# 01 — Data ingestion

Pull historical daily bars for the strategy universe and cache to parquet + SQLite.

Default source: yfinance. Override to Polygon/Alpaca by changing the client."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.yfinance_client import YFinanceClient
yf = YFinanceClient()
assert yf.available(), 'yfinance not installed'

ETF_UNIVERSE = ['SPY','QQQ','IWM','TLT','IEF','GLD','SLV','USO','DBA',
                'VNQ','HYG','LQD','UUP','FXE','FXY','XLE','XLK']

data = {}
for s in ETF_UNIVERSE:
    df = yf.get_daily_bars(s, start='2010-01-01')
    if not df.empty:
        data[s] = df['adj_close']
        print(f'  {s}: {len(df)} rows {df.index.min().date()} → {df.index.max().date()}')

prices = pd.DataFrame(data).dropna(how='all').ffill().dropna()
prices.to_parquet('../data/parquet/etf_panel.parquet')
print(f'\\nPanel: {prices.shape}, cached to data/parquet/etf_panel.parquet')
prices.tail()"""),
    mkmd("### Quick chart"),
    mkcode("""fig, ax = plt.subplots(figsize=(11, 5))
(prices / prices.iloc[0]).plot(ax=ax, linewidth=0.8)
ax.set_title('ETF universe (normalised)')
ax.grid(alpha=0.3)
ax.legend(loc='best', fontsize=8); plt.show()"""),
    mkmd("### Optional: FRED macro series\n\nIf you have a FRED key:"),
    mkcode("""from src.config import KEYS
if KEYS.fred:
    from src.data.fred_client import FREDClient
    fred = FREDClient()
    cpi = fred.get_series('CPIAUCSL', start='2010-01-01')
    print('CPI rows:', len(cpi))
    cpi.tail()
else:
    print('No FRED key set; skipping macro pull')"""),
    mkmd("### Optional: crypto funding history\n\nUses Binance's public REST endpoint — no auth required."),
    mkcode("""from src.data.crypto_clients import BinanceClient
bnc = BinanceClient()
f = bnc.get_funding_history('BTCUSDT', start='2022-01-01')
print(f'BTC funding rows: {len(f)} (last 5):')
f.tail()"""),
])

# ============================================================
# 02 — SPY vol-target backtest
# ============================================================
save("02_spy_vol_target_backtest.ipynb", [
    mkmd("""# 02 — SPY vol-target backtest

**Objective:** target 10% annualised portfolio vol on SPY. Position size =
`target_vol / forecast_vol`, clipped to [0, max_leverage]. Forecast = trailing
21-day realised vol.

**Bias controls:**
- All inputs at time t use only data with ts ≤ t.
- Rebalance at next-bar OPEN (engine shifts the weight by 1 bar).
- Pre-flight leakage check runs automatically.

**Failure conditions:** if pre-flight detects leakage, the run aborts with an error."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.yfinance_client import YFinanceClient
from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.backtest.engine import run_backtest, EngineConfig
from src.reports.tearsheet import tearsheet

yf = YFinanceClient()
spy = yf.get_daily_bars('SPY', start='2010-01-01')
prices = pd.DataFrame({'SPY': spy['adj_close']})
print('SPY rows:', len(prices))"""),
    mkmd("### Configure"),
    mkcode("""cfg_strat = VolTargetConfig(symbol='SPY', target_vol=0.10, rv_window=21, max_leverage=1.0)
cfg_engine = EngineConfig(starting_cash=100_000, rebalance_freq='D',
                          benchmark='SPY', strategy_name='spy_vol_target')
strat = SPYVolTargetStrategy(cfg_strat)
result = run_backtest(strat, prices, cfg_engine)
result['metrics']"""),
    mkmd("### Tearsheet"),
    mkcode("""fig, m = tearsheet(result, title='SPY vol-target')
plt.show()"""),
    mkmd("""### Interpretation

Vol-targeting is a risk-control overlay, not an alpha strategy. The expected outcome:
- Realised vol close to 10% target.
- Sharpe similar to or modestly better than buy-and-hold SPY.
- **Materially smaller drawdowns** — that's the whole point.

If you see Sharpe much higher than buy-and-hold's, double-check for lookahead."""),
    mkmd("### Sweep target vols"),
    mkcode("""rows = []
for tv in [0.05, 0.08, 0.10, 0.15, 0.20]:
    s = SPYVolTargetStrategy(VolTargetConfig(symbol='SPY', target_vol=tv,
                                              rv_window=21, max_leverage=2.0))
    r = run_backtest(s, prices, EngineConfig(starting_cash=100_000, rebalance_freq='D',
                                              benchmark='SPY', strategy_name=f'svt_{tv}',
                                              run_preflight=False))
    rows.append(dict(target_vol=tv, **{k: r['metrics'][k] for k in
                       ['sharpe','cagr','ann_vol','max_drawdown','calmar']}))
pd.DataFrame(rows)"""),
    mkmd("""### Next steps

1. Replace 21-day realised vol with EWMA / GARCH forecast.
2. Add ML vol forecast (only with walk-forward training; baseline must work first).
3. Run `07_live_paper_trading_dashboard.ipynb` to paper trade this.
4. Combine with TSMOM in `06_combined_portfolio_backtest.ipynb`."""),
])

# ============================================================
# 03 — Crypto funding arb backtest
# ============================================================
save("03_crypto_funding_arb_backtest.ipynb", [
    mkmd("""# 03 — Crypto spot-perp funding arb backtest

**Setup:** long spot, short perp (1:1 notional). Collect funding. Unwind on either:
- N consecutive negative funding prints, or
- |basis| > threshold (suggests stress).

**Data:** Binance public REST for funding history (no auth). yfinance for spot proxy.

**Mode:** backtest only. Paper trading lives in NB 07."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.crypto_clients import BinanceClient
from src.data.yfinance_client import YFinanceClient
from src.strategies.crypto_funding_arb import simulate_funding_arb, FundingArbConfig

bnc = BinanceClient()
f = bnc.get_funding_history('BTCUSDT', start='2022-01-01')
print('Funding rows:', len(f))
f.head()"""),
    mkcode("""yf = YFinanceClient()
btc = yf.get_daily_bars('BTC-USD', start='2022-01-01')
spot = btc['adj_close']
spot.index = spot.index.tz_localize(None)
print('BTC daily rows:', len(spot))"""),
    mkmd("### Simulate"),
    mkcode("""res = simulate_funding_arb(
    funding=f['rate'],
    spot=spot,
    perp=None,                       # treat perp = spot in absence of perp marks
    cfg=FundingArbConfig(notional=100_000, unwind_funding_threshold=0.0,
                          unwind_periods=6, unwind_basis_bps=80))
res['metrics']"""),
    mkcode("""fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
ax[0].plot(res['equity'].index, res['equity'].values, linewidth=1)
ax[0].set_title('Funding arb equity (notional $100k)')
ax[0].grid(alpha=0.3)
ax[1].plot(res['daily_funding'].index, res['daily_funding'].values * 10_000, linewidth=0.6)
ax[1].set_ylabel('daily funding (bp)')
ax[1].grid(alpha=0.3); plt.show()

print(f'unwind events: {len(res["events"])}')
for d, why in res['events'][:5]: print(f'  {d.date()}: {why}')"""),
    mkmd("""### Interpretation & risk

Real funding-arb risk in production includes:
- **Counterparty risk** — exchange insolvency wipes the leg you couldn't withdraw.
- **Collateral risk** — perp short uses USDT margin; USDT depeg = liquidation.
- **Slippage at unwind** — sized small (1-2bp on liquid pairs) but real.
- **Borrow / financing** — long spot doesn't borrow, but margin perp does.

See `RISK_POLICY.md` for the venue-allocation cap and stress-test triggers."""),
])

# ============================================================
# 04 — TSMOM backtest
# ============================================================
save("04_tsmom_backtest.ipynb", [
    mkmd("""# 04 — Cross-asset TSMOM backtest

**Setup:** rank 17 ETFs by 12-month trailing return. Long positive, short negative
(or cash if `allow_short=False`). Vol-target each leg to 10%. Rebalance monthly.

**Bias controls:** signal uses data only up to month-end t; trades execute at next
month-start prices."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.yfinance_client import YFinanceClient
from src.strategies.tsmom import TSMOMStrategy, TSMOMConfig
from src.backtest.engine import run_backtest, EngineConfig
from src.reports.tearsheet import tearsheet

yf = YFinanceClient()
universe = ['SPY','QQQ','IWM','TLT','IEF','GLD','SLV','USO','VNQ','XLE','XLK']
data = {s: yf.get_daily_bars(s, start='2008-01-01')['adj_close']
         for s in universe}
prices = pd.DataFrame(data).ffill().dropna()
print('Panel:', prices.shape)"""),
    mkmd("### Run"),
    mkcode("""strat = TSMOMStrategy(TSMOMConfig(universe=list(prices.columns),
                                   per_asset_vol=0.08,
                                   max_gross_leverage=1.0, allow_short=True))
res = run_backtest(strat, prices,
                   EngineConfig(starting_cash=100_000, rebalance_freq='M',
                                 benchmark='SPY', strategy_name='tsmom',
                                 run_preflight=False))
res['metrics']"""),
    mkcode("""fig, m = tearsheet(res, 'TSMOM cross-asset')
plt.show()"""),
])

# ============================================================
# 05 — Prediction market signal research
# ============================================================
save("05_prediction_market_signal_research.ipynb", [
    mkmd("""# 05 — Prediction market → TradFi lead-lag research

**Objective:** test whether prediction-market prices on macro events (Fed, CPI,
geopolitical) lead TradFi instruments (TLT, SPY, VIX proxy, DXY).

**Output:** lag at which PM-to-TradFi correlation peaks. If lead < 15 minutes or
|correlation| < 0.30, the strategy returns zero weight."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.kalshi_client import KalshiClient
from src.data.polymarket_client import PolymarketClient
from src.strategies.prediction_market_signals import LeadLagResearchStrategy, LeadLagConfig

k = KalshiClient()
p = PolymarketClient()
print('Kalshi available:', k.available())
print('Polymarket available:', p.available())"""),
    mkmd("### Browse Kalshi markets (Fed / CPI focus)\n\nKalshi public market data needs no auth."),
    mkcode("""km = k.list_markets(status='open', limit=200)
if not km.empty:
    print(f'{len(km)} open markets, columns: {km.columns.tolist()[:10]}')
    if 'title' in km.columns:
        fed = km[km['title'].str.contains('Fed|FOMC|rate', case=False, na=False)]
        # Show whichever price columns exist (Kalshi schema varies)
        wanted = [c for c in ['ticker','title','yes_bid','yes_ask','last_price','volume_24h','volume'] if c in fed.columns]
        print(f'{len(fed)} Fed-related markets:')
        fed[wanted].head(15)
else:
    print('No markets returned')"""),
    mkmd("### Browse Polymarket\n\nPolymarket is permissionless; their Gamma API is open."),
    mkcode("""pm = p.list_markets(active=True, limit=200)
if not pm.empty:
    print(f'{len(pm)} active markets, columns: {pm.columns.tolist()[:10]}')
    pm.head()"""),
    mkmd("""### Lead-lag analysis (skeleton)

For a real study you need:
1. minute-by-minute PM probability series for an event (download from `get_market_history` on Kalshi or `get_price_history` on Polymarket).
2. minute-by-minute TradFi price for the comparison instrument (Polygon or Alpaca).

Below is a synthetic example showing how the strategy decides whether the signal is tradeable. Replace with real data once your minute-level data feed is up."""),
    mkcode("""np.random.seed(0)
idx = pd.date_range('2026-01-01', periods=2000, freq='1min', tz='UTC')
pm_prob = pd.Series(np.cumsum(np.random.normal(0,0.001,2000))+0.5, index=idx).clip(0.01,0.99)
tlt = pm_prob.shift(30) * 0.5 + np.random.normal(0, 0.002, 2000).cumsum() + 100
tlt = tlt.bfill()
df = pd.DataFrame({'FED-RATE-DEC': pm_prob, 'TLT': tlt})

strat = LeadLagResearchStrategy(LeadLagConfig(pm_symbol='FED-RATE-DEC', tradfi_symbol='TLT'))
_ = strat.signal(df)
print('lead-lag analysis:', strat.last_analysis)"""),
    mkmd("""### Production recipe

Run nightly:
1. For each upcoming Fed/CPI event window, pull Kalshi + Polymarket minute data.
2. Align to SOFR futures (or TLT proxy) minute bars from Polygon.
3. Run `LeadLagResearchStrategy.analyze_leadlag()`; persist `(event_id, lead_min, corr)`.
4. Trade only when |lead| > 15 min AND |corr| > 0.30 AND survives a 6-month rolling OOS check.

**Anti-bias rule:** PM prices used in the lookback window must have timestamps strictly before the TradFi window being predicted."""),
])

# ============================================================
# 06 — Combined portfolio
# ============================================================
save("06_combined_portfolio_backtest.ipynb", [
    mkmd("""# 06 — Combined portfolio backtest

Combine strategy-level return streams using inverse-vol weighting + optional vol
targeting + drawdown de-risking.

**Inputs:** daily PnL series from each backtest (saved into the DB by the engine)."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.yfinance_client import YFinanceClient
from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.strategies.tsmom import TSMOMStrategy, TSMOMConfig
from src.backtest.engine import run_backtest, EngineConfig

yf = YFinanceClient()
universe = ['SPY','QQQ','IWM','TLT','IEF','GLD','SLV','USO','VNQ','XLE','XLK']
data = {s: yf.get_daily_bars(s, start='2010-01-01')['adj_close'] for s in universe}
prices = pd.DataFrame(data).ffill().dropna()
print('Universe panel:', prices.shape)"""),
    mkcode("""# strategy 1: SPY vol-target
strat1 = SPYVolTargetStrategy(VolTargetConfig(symbol='SPY', target_vol=0.10, rv_window=21))
r1 = run_backtest(strat1, prices[['SPY']], EngineConfig(strategy_name='svt', run_preflight=False, benchmark=None))

# strategy 2: TSMOM
strat2 = TSMOMStrategy(TSMOMConfig(universe=list(prices.columns), per_asset_vol=0.08,
                                    max_gross_leverage=1.0, allow_short=True))
r2 = run_backtest(strat2, prices, EngineConfig(strategy_name='tsmom', rebalance_freq='M', run_preflight=False, benchmark=None))

streams = pd.DataFrame({'spy_vt': r1['returns'], 'tsmom': r2['returns']}).fillna(0)
print('Streams:', streams.shape)
print('Per-stream Sharpe:')
for c in streams.columns:
    s = streams[c]
    print(f'  {c}: Sharpe={s.mean()/s.std()*np.sqrt(252):+.2f}')"""),
    mkmd("### Inverse-vol allocation"),
    mkcode("""ivol = 1.0 / streams.std()
w = ivol / ivol.sum()
print('Weights:'); print(w.to_string())
combo = (streams * w).sum(axis=1)
print(f'\\nCombo Sharpe: {combo.mean()/combo.std()*np.sqrt(252):+.2f}')
print(f'Combo MDD: {((1+combo).cumprod() / (1+combo).cumprod().cummax() - 1).min():+.2%}')"""),
    mkmd("### Correlations + rolling correlations"),
    mkcode("""print('Static corr:'); print(streams.corr().round(2))
rc = streams['spy_vt'].rolling(126).corr(streams['tsmom'])
fig, ax = plt.subplots(figsize=(11, 3))
ax.plot(rc.index, rc.values, linewidth=0.7)
ax.axhline(0, color='gray', linewidth=0.5)
ax.set_title('Rolling 126d correlation: SPY-VT vs TSMOM')
ax.grid(alpha=0.3); plt.show()"""),
    mkmd("### Combined equity"),
    mkcode("""eq = (1 + combo).cumprod() * 100_000
fig, ax = plt.subplots(figsize=(11, 4))
ax.plot(eq.index, eq.values, linewidth=1.2, label='combined')
ax.plot(r1['equity'].index, r1['equity'].values, linewidth=0.7, alpha=0.6, label='spy_vt')
ax.plot(r2['equity'].index, r2['equity'].values, linewidth=0.7, alpha=0.6, label='tsmom')
ax.set_title('Inverse-vol combined portfolio')
ax.grid(alpha=0.3); ax.legend(); plt.show()"""),
])

# ============================================================
# 07 — Live paper trading dashboard
# ============================================================
save("07_live_paper_trading_dashboard.ipynb", [
    mkmd("""# 07 — Live paper trading dashboard

**Default mode:** in-memory paper broker (no broker account needed).

**Optional:** Alpaca paper broker (set `USE_ALPACA = True` below; needs ALPACA_API_KEY).

Re-run the 'Tick' cell to: pull latest price → recompute signal → check risk → place
orders → snapshot portfolio."""),
    mkcode(PREAMBLE),
    mkcode("""import uuid, time
from src.config import SAFETY, KEYS
from src.data.yfinance_client import YFinanceClient
from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.live.paper_broker import PaperBroker
from src.live.risk_monitor import RiskMonitor, RiskState
from src.live.broker_base import Order
from src.backtest.engine import RiskLimits
from src.backtest.costs import CostModel

USE_ALPACA = False    # flip to True if you have Alpaca paper creds in .env
STARTING_CASH = 100_000
run_id = uuid.uuid4().hex[:12]
print('run_id:', run_id, ' paper mode (live disabled by default):', not SAFETY.can_trade_live)"""),
    mkcode("""yf = YFinanceClient()
def latest_price(symbol):
    df = yf.get_daily_bars(symbol, start='2024-01-01', use_cache=False)
    return float(df['adj_close'].iloc[-1])

if USE_ALPACA and KEYS.alpaca_id:
    from src.live.alpaca_broker import AlpacaBroker
    broker = AlpacaBroker(live=False)
else:
    broker = PaperBroker(run_id=run_id, starting_cash=STARTING_CASH,
                          price_fn=latest_price, costs=CostModel())
broker"""),
    mkcode("""limits = RiskLimits(max_gross_leverage=1.0, max_single_asset_weight=0.5,
                    drawdown_warn=-0.03, drawdown_derisk=-0.05, drawdown_stop=-0.08)
state = RiskState(run_id=run_id, limits=limits, starting_equity=STARTING_CASH)
monitor = RiskMonitor(broker, state)
print('monitor armed')"""),
    mkmd("""### Tick — run signal → rebalance to target

This pulls the latest SPY price, computes target weight from the SPY vol-target strategy, and submits the required order through the risk monitor."""),
    mkcode("""def tick():
    df = yf.get_daily_bars('SPY', start='2023-01-01', use_cache=False)
    prices = pd.DataFrame({'SPY': df['adj_close']})
    sig = SPYVolTargetStrategy(VolTargetConfig(symbol='SPY', target_vol=0.10, rv_window=21))\\
          .signal(prices)
    target_w = float(sig['SPY'].iloc[-1])
    acct = broker.account()
    equity = acct.get('equity', STARTING_CASH)
    px = broker.last_price('SPY')
    target_qty = (target_w * equity) / px
    cur_qty = next((p.qty for p in broker.positions() if p.symbol == 'SPY'), 0.0)
    delta = target_qty - cur_qty
    monitor.update_equity(equity)
    if abs(delta * px) >= 50:
        side = 'buy' if delta > 0 else 'sell'
        result = monitor.submit(Order(symbol='SPY', side=side, qty=round(abs(delta), 4),
                                       reason='spy_vt tick'))
    else:
        result = dict(status='no_op')
    return dict(target_w=target_w, target_qty=round(target_qty,4),
                 cur_qty=cur_qty, equity=equity, px=px, result=result)

tick()"""),
    mkmd("### Dashboard"),
    mkcode("""acct = broker.account()
from src.data.storage import query
fills = query('SELECT * FROM fills WHERE run_id=? ORDER BY ts DESC', (run_id,))
events = query('SELECT * FROM risk_events WHERE run_id=? ORDER BY ts DESC', (run_id,))
snapshots = query('SELECT * FROM portfolio_snapshots WHERE run_id=? ORDER BY ts DESC LIMIT 30', (run_id,))

print('=== Account ===')
for k, v in acct.items(): print(f'  {k}: {v}')
print('\\n=== Positions ===')
for p in broker.positions(): print(f'  {p.symbol}: qty={p.qty:.4f} avg=${p.avg_px:.2f}')
print('\\n=== Recent fills (last 5) ===')
fills.head() if not fills.empty else 'none'"""),
    mkcode("""events.head() if not events.empty else 'no risk events'"""),
    mkmd("""### Optional: loop

Uncomment to run a tick every N seconds for a duration. **Note:** yfinance daily bars don't change intraday, so for true live ticking you want intraday Polygon/Alpaca."""),
    mkcode("""# from src.live.scheduler import loop_until
# loop_until(tick, every_seconds=60, until=pd.Timestamp.utcnow() + pd.Timedelta(hours=1))"""),
])

# ============================================================
# 08 — Monthly report
# ============================================================
save("08_monthly_performance_report.ipynb", [
    mkmd("""# 08 — Monthly performance report

Generates a markdown + PNG report for any backtest or paper-trading run. Outputs to
`data/reports/<run_id>/`."""),
    mkcode(PREAMBLE),
    mkcode("""from src.data.yfinance_client import YFinanceClient
from src.strategies.spy_vol_target import SPYVolTargetStrategy, VolTargetConfig
from src.backtest.engine import run_backtest, EngineConfig
from src.reports.monthly_report import write_monthly_report

yf = YFinanceClient()
spy = yf.get_daily_bars('SPY', start='2018-01-01')
prices = pd.DataFrame({'SPY': spy['adj_close']})
res = run_backtest(SPYVolTargetStrategy(VolTargetConfig()),
                    prices,
                    EngineConfig(strategy_name='spy_vt_report', run_preflight=False))
out_dir = write_monthly_report(res, res['run_id'], 'spy_vol_target')
print('Wrote report to', out_dir)
import os
for f in sorted(os.listdir(out_dir)): print(' ', f)"""),
    mkcode("""from IPython.display import Markdown, Image
Markdown((out_dir / 'report.md').read_text())"""),
    mkcode("""Image(filename=str(out_dir / 'equity.png'))"""),
    mkcode("""Image(filename=str(out_dir / 'drawdown.png'))"""),
    mkcode("""Image(filename=str(out_dir / 'monthly_heatmap.png'))"""),
])

print("Wrote 9 notebooks to", NB_DIR)
