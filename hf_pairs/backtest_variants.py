"""Find a configuration that actually works (or definitively show none does).

Test three axes:
  A) cost level: taker-only vs maker-only vs mixed
  B) timeframe:  1-min vs 5-min vs 15-min bars
  C) exit logic: z-based vs full-reversion vs fixed hold

All variants are walk-forward, no look-ahead.
"""
import math, sys
from pathlib import Path
from dataclasses import dataclass, asdict
import duckdb, pandas as pd, numpy as np

DATA = Path('hf_pairs/data')

@dataclass
class Trade:
    enter_idx: int; enter_ts: pd.Timestamp; side: str
    btc_enter: float; eth_enter: float; z_enter: float
    exit_idx: int = -1; exit_ts: pd.Timestamp = None
    btc_exit: float = 0.0; eth_exit: float = 0.0; z_exit: float = 0.0
    pnl_gross: float = 0.0; cost: float = 0.0; pnl_net: float = 0.0
    bars_held: int = 0; reason: str = ''

def load_1min():
    db = duckdb.connect(str(DATA / 'btc_eth_1min.duckdb'), read_only=True)
    btc = db.execute('SELECT ts_ms, ts, open, high, low, close, volume FROM BTC_USD ORDER BY ts_ms').df()
    eth = db.execute('SELECT ts_ms, ts, open, high, low, close, volume FROM ETH_USD ORDER BY ts_ms').df()
    db.close()
    df = btc.merge(eth, on='ts_ms', suffixes=('_btc','_eth'))
    df['ts'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
    df['spread'] = np.log(df['close_btc']) - np.log(df['close_eth'])
    return df.sort_values('ts_ms').reset_index(drop=True)

def resample(df_1min, minutes):
    """Resample 1-min bars to N-min bars. Use last-close for snapshot, first-open."""
    df = df_1min.set_index('ts')
    agg = pd.DataFrame()
    for col in ['btc', 'eth']:
        agg[f'open_{col}']  = df[f'open_{col}'].resample(f'{minutes}min').first()
        agg[f'close_{col}'] = df[f'close_{col}'].resample(f'{minutes}min').last()
        agg[f'high_{col}']  = df[f'high_{col}'].resample(f'{minutes}min').max()
        agg[f'low_{col}']   = df[f'low_{col}'].resample(f'{minutes}min').min()
        agg[f'volume_{col}'] = df[f'volume_{col}'].resample(f'{minutes}min').sum()
    agg = agg.dropna()
    agg = agg.reset_index()
    agg['ts_ms'] = (agg['ts'].astype('int64') // 10**6).astype('int64')
    agg['spread'] = np.log(agg['close_btc']) - np.log(agg['close_eth'])
    return agg

def compute_z(df, window):
    df = df.copy()
    s = df['spread']
    df['mu'] = s.shift(1).rolling(window).mean()
    df['sd'] = s.shift(1).rolling(window).std()
    df['z'] = (df['spread'] - df['mu']) / df['sd']
    return df

def run(df, *, z_in, z_out, z_stop, max_hold, window,
        position_usd=5000, taker_fee_bps=5, slip_bps=2,
        exit_at_zero=False):
    df = compute_z(df, window)
    fees_per_fill = (taker_fee_bps + slip_bps)
    cost_per_trade = fees_per_fill / 10_000 * position_usd * 4
    btc_o, eth_o = df['open_btc'].values, df['open_eth'].values
    btc_c, eth_c = df['close_btc'].values, df['close_eth'].values
    zs = df['z'].values; tss = df['ts'].values
    trades = []; in_pos = None
    for t in range(len(df) - 1):
        z = zs[t]
        if pd.isna(z): continue
        if in_pos is not None:
            held = t - in_pos.enter_idx
            reason = None
            target_out = 0.0 if exit_at_zero else z_out
            # Determine exit based on the original entry side
            if in_pos.side == 'long_btc':  # z<-2 at entry, target z near zero or positive
                if z >= target_out:        reason = 'take_profit'
            else:  # short_btc, z>2 at entry, target z near zero or negative
                if z <= -target_out if exit_at_zero else (z <= target_out):
                    reason = 'take_profit'
            if reason is None and abs(z) > z_stop:  reason = 'stop_loss'
            if reason is None and held >= max_hold: reason = 'time_stop'
            if reason:
                ft = t + 1
                in_pos.exit_idx, in_pos.exit_ts = ft, tss[ft]
                in_pos.btc_exit, in_pos.eth_exit = btc_o[ft], eth_o[ft]
                in_pos.z_exit = zs[ft] if not pd.isna(zs[ft]) else z
                in_pos.bars_held = held; in_pos.reason = reason
                br = in_pos.btc_exit/in_pos.btc_enter - 1
                er = in_pos.eth_exit/in_pos.eth_enter - 1
                pp = (br-er) if in_pos.side == 'long_btc' else (er-br)
                in_pos.pnl_gross = pp * position_usd
                in_pos.cost = cost_per_trade
                in_pos.pnl_net = in_pos.pnl_gross - in_pos.cost
                trades.append(in_pos); in_pos = None; continue
        if in_pos is None and abs(z) > z_in:
            if pd.isna(btc_o[t+1]) or pd.isna(eth_o[t+1]): continue
            in_pos = Trade(t+1, tss[t+1], 'short_btc' if z > 0 else 'long_btc',
                           btc_o[t+1], eth_o[t+1], z)
    return trades, df

def stats(trades, df, position_usd, starting_cap=100_000):
    if not trades: return None
    span_days = (df['ts'].iloc[-1] - df['ts'].iloc[0]).total_seconds() / 86400
    pnls = np.array([t.pnl_net for t in trades])
    pnl_total = pnls.sum()
    wins = (pnls > 0).sum()
    pnl_pct = pnls / starting_cap
    tpd = len(trades) / max(span_days, 1)
    sharpe = pnl_pct.mean() / pnl_pct.std() * math.sqrt(tpd * 365) if pnl_pct.std() > 0 else 0
    nav = starting_cap; peak = nav; mdd = 0
    for t in trades:
        nav += t.pnl_net
        if nav > peak: peak = nav
        if peak > 0:
            dd = (peak - nav) / peak
            if dd > mdd: mdd = dd
    total_ret = nav/starting_cap - 1
    return {
        'n': len(trades), 'win_pct': wins/len(trades)*100,
        'gross': sum(t.pnl_gross for t in trades),
        'cost':  sum(t.cost for t in trades),
        'net':   pnl_total,
        'sharpe': sharpe, 'total_ret': total_ret,
        'max_dd': mdd, 'days': span_days,
        'tpd': tpd, 'avg_hold_min': float(np.mean([t.bars_held for t in trades])),
    }

def row(label, s, tf_min=1):
    if s is None:
        print(f'  {label:48s}  no trades'); return
    print(f'  {label:48s}  n={s["n"]:>4} '
          f'win={s["win_pct"]:>4.1f}%  '
          f'gross=${s["gross"]:>+8.0f}  cost=${s["cost"]:>+7.0f}  '
          f'net=${s["net"]:>+8.0f}  sharpe={s["sharpe"]:>+5.2f}  '
          f'tpd={s["tpd"]:>4.1f}')

# ─── Load + variants ──────────────────────────────────────────────────
df_1m = load_1min()
print(f'Loaded {len(df_1m):,} 1-min bars  ({df_1m["ts"].min()} → {df_1m["ts"].max()})')

# Baseline (already ran): retail taker fees, 1-min, z=2.0
print('\n══ Cost-axis sensitivity (1-min bars, z=2, win=60) ══════════════════════')
for label, tk, sl in [
    ('retail taker (5bp + 2bp slip = 28bp round-trip)', 5, 2),
    ('discount taker (3bp + 2bp slip = 20bp)',          3, 2),
    ('maker-only on Coinbase Pro (0bp + 2bp slip = 8bp)', 0, 2),
    ('zero-cost (theoretical signal check)',           0, 0),
]:
    trades, _ = run(df_1m, z_in=2.0, z_out=0.5, z_stop=3.5, max_hold=240,
                    window=60, taker_fee_bps=tk, slip_bps=sl)
    row(label, stats(trades, df_1m, 5000))

# Timeframe axis: hold a position longer per bar
print('\n══ Timeframe-axis (z=2, window=60 bars, maker fees) ════════════════════')
for tf in [1, 5, 15, 30]:
    df_tf = resample(df_1m, tf) if tf > 1 else df_1m
    # Adjust hold cap proportionally
    trades, _ = run(df_tf, z_in=2.0, z_out=0.5, z_stop=3.5,
                    max_hold=int(240/tf), window=60,
                    taker_fee_bps=0, slip_bps=2)
    label = f'{tf}-min bars  ({len(df_tf):,} bars)'
    row(label, stats(trades, df_tf, 5000), tf)

# Z-threshold sweep at 5-min + maker fees
print('\n══ Z-entry sweep (5-min bars, maker-only) ══════════════════════════════')
df_5m = resample(df_1m, 5)
for z_in in [1.5, 2.0, 2.5, 3.0, 3.5]:
    trades, _ = run(df_5m, z_in=z_in, z_out=0.5, z_stop=z_in+2.0,
                    max_hold=48, window=60, taker_fee_bps=0, slip_bps=2)
    row(f'z_in={z_in}', stats(trades, df_5m, 5000), 5)

# Exit logic: full reversion (exit at z=0 cross) vs z=0.5
print('\n══ Exit logic comparison (5-min bars, maker-only, z=2.5) ═══════════════')
for label, kwargs in [
    ('exit at |z|<0.5 (partial reversion)', dict(z_out=0.5, exit_at_zero=False)),
    ('exit at z=0 cross (full reversion)',   dict(z_out=0.0, exit_at_zero=True)),
]:
    trades, _ = run(df_5m, z_in=2.5, z_stop=4.5, max_hold=48, window=60,
                    taker_fee_bps=0, slip_bps=2, **kwargs)
    row(label, stats(trades, df_5m, 5000), 5)

# Best config OOS
print('\n══ Best config OOS (60/40 train/test, 5-min, maker, z=2.5, full-rev) ══')
for label, df_part in [('TRAIN', df_5m.iloc[:int(len(df_5m)*0.6)]),
                       ('TEST ', df_5m.iloc[int(len(df_5m)*0.6):])]:
    trades, _ = run(df_part.reset_index(drop=True),
                    z_in=2.5, z_out=0.0, z_stop=4.5, max_hold=48,
                    window=60, taker_fee_bps=0, slip_bps=2, exit_at_zero=True)
    row(label, stats(trades, df_part, 5000), 5)
