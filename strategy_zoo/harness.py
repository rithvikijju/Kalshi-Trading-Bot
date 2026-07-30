"""Unified backtest harness for the 50-strategy zoo.

Loaders, cost model, stats. Each strategy is a function that takes (data, **kwargs)
and returns a list of trade dicts with at least {enter, exit, pnl_pct, side}.

Pnl is in PCT of $1 notional per leg, so net_dollar = pnl_pct * pos_usd - cost_dollar.
"""
from __future__ import annotations
import math, json, time
from pathlib import Path
import duckdb, pandas as pd, numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_ALTS = ROOT / 'alt_statarb/data/alts_1h.duckdb'
DATA_BTCETH_1M = ROOT / 'hf_pairs/data/btc_eth_1min.duckdb'
DATA_FUNDING = ROOT / 'strategies/data/crypto_long.duckdb'
DATA_RESEARCH = ROOT / 'data/research_datamart/research_backtest.duckdb'
RESULTS_DB = ROOT / 'strategy_zoo/results.duckdb'


# ─── Cost tiers (RT in basis points) ────────────────────────────────
COSTS = {
    'hyperliquid':  {'taker_bps': 2.5, 'slip_bps': 1.5, 'maker_bps': -0.4},
    'coinbase':     {'taker_bps': 5.0, 'slip_bps': 2.0, 'maker_bps': 0.0},
    'kraken_perp':  {'taker_bps': 3.5, 'slip_bps': 1.5, 'maker_bps': 0.0},
    'bybit':        {'taker_bps': 5.5, 'slip_bps': 2.0, 'maker_bps': 1.0},
    'binance':      {'taker_bps': 4.0, 'slip_bps': 1.5, 'maker_bps': 1.5},
}


def rt_cost_bps(tier: str, legs: int = 2, taker_frac: float = 1.0) -> float:
    """Round-trip cost in bps for `legs` legs (e.g. 2 for spread). taker_frac=1 → all taker."""
    c = COSTS[tier]
    per_leg = taker_frac * (c['taker_bps'] + c['slip_bps']) + (1-taker_frac) * (c['maker_bps'] + c['slip_bps'])
    return per_leg * legs * 2   # entry + exit


# ─── Loaders (cached on first call) ─────────────────────────────────
_CACHE = {}

def load_alts_1h():
    if 'alts' in _CACHE: return _CACHE['alts']
    db = duckdb.connect(str(DATA_ALTS), read_only=True)
    tables = sorted([r[0] for r in db.execute('SHOW TABLES').fetchall() if r[0].startswith('c_')])
    data = {}
    for t in tables:
        df = db.execute(f'SELECT ts_ms, ts, open, high, low, close, volume FROM {t} ORDER BY ts_ms').df()
        data[t[2:]] = df
    db.close()
    base = data[list(data.keys())[0]][['ts_ms','ts']].copy()
    for coin, df in data.items():
        base = base.merge(df[['ts_ms','open','close','volume']].rename(
            columns={'open':f'o_{coin}','close':f'c_{coin}','volume':f'v_{coin}'}),
            on='ts_ms', how='inner')
    base = base.sort_values('ts_ms').reset_index(drop=True)
    _CACHE['alts'] = (base, list(data.keys()))
    return _CACHE['alts']


def load_btc_eth_1m():
    if 'btceth_1m' in _CACHE: return _CACHE['btceth_1m']
    db = duckdb.connect(str(DATA_BTCETH_1M), read_only=True)
    btc = db.execute('SELECT ts_ms, ts, open, high, low, close, volume FROM BTC_USD ORDER BY ts_ms').df()
    eth = db.execute('SELECT ts_ms, ts, open, high, low, close, volume FROM ETH_USD ORDER BY ts_ms').df()
    db.close()
    btc.columns = ['ts_ms','ts','b_o','b_h','b_l','b_c','b_v']
    eth.columns = ['ts_ms','ts2','e_o','e_h','e_l','e_c','e_v']
    df = btc.merge(eth[['ts_ms','e_o','e_h','e_l','e_c','e_v']], on='ts_ms').sort_values('ts_ms').reset_index(drop=True)
    _CACHE['btceth_1m'] = df
    return df


def load_funding():
    """Funding (8h) + perp + spot (daily). merge_asof forward-fills daily prices
    to each funding tick so every funding obs has the most recent perp/spot close."""
    if 'funding' in _CACHE: return _CACHE['funding']
    db = duckdb.connect(str(DATA_FUNDING), read_only=True)
    out = {}
    for asset, sym in [('BTC', 'XBTUSD'), ('ETH', 'ETHUSD')]:
        f = db.execute(f"SELECT timestamp, fundingRate FROM funding_{sym} ORDER BY timestamp").df()
        p = db.execute(f"SELECT timestamp, close as perp_close FROM perp_{sym} ORDER BY timestamp").df()
        spot_tbl = 'spot_XBT_index' if asset == 'BTC' else 'spot_ETH_index'
        s = db.execute(f"SELECT timestamp, close as spot_close FROM {spot_tbl} ORDER BY timestamp").df()
        # Normalize tz to UTC then drop tz for merge_asof (which requires monotonic naive or tz-aware)
        for d in (f, p, s):
            d['timestamp'] = pd.to_datetime(d['timestamp'], utc=True)
            d.sort_values('timestamp', inplace=True)
        m = pd.merge_asof(f, p, on='timestamp', direction='backward')
        m = pd.merge_asof(m, s, on='timestamp', direction='backward')
        m['basis_bps'] = (m['perp_close']/m['spot_close'] - 1) * 10000
        out[asset] = m.reset_index(drop=True)
    db.close()
    _CACHE['funding'] = out
    return out


# ─── Stats ──────────────────────────────────────────────────────────
def stats(trades, label='', starting_cap=100_000, freq='auto'):
    if not trades or len(trades) == 0:
        return {'label':label,'n':0,'sharpe':0,'net':0,'win_pct':0,'tpd':0,'span_d':0,'gross':0,'cost':0,'max_dd':0}
    nets = np.array([t.get('net', t.get('pnl_dollar', 0)) for t in trades])
    grosses = np.array([t.get('gross', 0) for t in trades])
    costs = np.array([t.get('cost', 0) for t in trades])
    wins = (nets > 0).sum()
    try:
        t_first = pd.Timestamp(trades[0].get('enter', trades[0].get('ts_enter')))
        t_last  = pd.Timestamp(trades[-1].get('exit',  trades[-1].get('ts_exit', trades[-1].get('enter'))))
        span_d = max((t_last - t_first).total_seconds() / 86400, 0.1)
    except Exception:
        span_d = max(len(trades) / 24, 0.1)
    tpd = len(trades) / span_d
    pcts = nets / starting_cap
    sharpe = pcts.mean()/pcts.std() * math.sqrt(tpd * 365) if pcts.std() > 0 else 0
    nav, peak, mdd = starting_cap, starting_cap, 0
    for n in nets:
        nav += n; peak = max(peak, nav)
        if peak > 0: mdd = max(mdd, (peak-nav)/peak)
    return {'label':label,'n':len(trades),'win_pct':100*wins/len(trades),
            'gross':grosses.sum(),'cost':costs.sum(),'net':nets.sum(),
            'sharpe':sharpe,'max_dd':mdd,'tpd':tpd,'span_d':span_d,
            'avg_trade':nets.mean(),'cap':starting_cap}


def fmt(s):
    if s.get('n',0) == 0: return f"  [{s['label']}] no trades"
    return (f"  [{s['label']:42}] n={s['n']:>5}  win={s['win_pct']:>4.0f}%  "
            f"tpd={s['tpd']:>5.1f}  net=${s['net']:>+9.0f}  "
            f"sharpe={s['sharpe']:>+5.2f}  DD={s['max_dd']*100:>4.1f}%")


# ─── Results table ──────────────────────────────────────────────────
def save_result(strategy_id: str, label: str, fee_tier: str, stats_dict: dict, notes: str = ''):
    db = duckdb.connect(str(RESULTS_DB))
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        ts TIMESTAMP, strategy_id VARCHAR, label VARCHAR, fee_tier VARCHAR,
        n INT, win_pct DOUBLE, tpd DOUBLE, gross DOUBLE, cost DOUBLE, net DOUBLE,
        sharpe DOUBLE, max_dd DOUBLE, span_d DOUBLE, cap DOUBLE, notes VARCHAR
    )""")
    db.execute("""INSERT INTO runs VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [strategy_id, label, fee_tier,
         int(stats_dict.get('n',0)), float(stats_dict.get('win_pct',0)),
         float(stats_dict.get('tpd',0)), float(stats_dict.get('gross',0)),
         float(stats_dict.get('cost',0)), float(stats_dict.get('net',0)),
         float(stats_dict.get('sharpe',0)), float(stats_dict.get('max_dd',0)),
         float(stats_dict.get('span_d',0)), float(stats_dict.get('cap',100000)), notes])
    db.close()


def top_results(min_sharpe=1.0, min_trades=20):
    db = duckdb.connect(str(RESULTS_DB), read_only=True)
    try:
        df = db.execute(f"""
            SELECT strategy_id, label, fee_tier, n, win_pct, tpd, net, sharpe, max_dd, span_d, notes
            FROM runs WHERE sharpe >= {min_sharpe} AND n >= {min_trades}
            ORDER BY sharpe DESC
        """).df()
    except Exception as e:
        df = pd.DataFrame()
    db.close()
    return df


if __name__ == '__main__':
    # smoke test
    print('alts loader...')
    df, coins = load_alts_1h()
    print(f'  {len(df)} bars, {len(coins)} coins')
    print('btc/eth 1m loader...')
    df = load_btc_eth_1m()
    print(f'  {len(df)} bars')
    print('funding loader...')
    fd = load_funding()
    print(f'  BTC funding rows: {len(fd["BTC"])}, ETH funding rows: {len(fd["ETH"])}')
    print('costs:')
    for t in ['hyperliquid', 'coinbase']:
        print(f'  {t}: RT 2-leg taker = {rt_cost_bps(t, legs=2):.1f} bps')
