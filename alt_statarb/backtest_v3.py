"""V3 — find the real edge.

V2 showed pair MOMENTUM has positive gross PnL across 10 pairs ($7,081 / 623 trades
= $11.36/trade gross), but Coinbase taker fees ($14/trade) eat the profit.

V3: comprehensive sweep over ALL 190 pairs at 2 fee tiers (Coinbase + Hyperliquid).
Then select pairs that are profitable in-sample and check them out-of-sample.
"""
from __future__ import annotations
import math, json
from pathlib import Path
from itertools import combinations
import duckdb, pandas as pd, numpy as np

DATA = Path('alt_statarb/data')
DB_PATH = DATA / 'alts_1h.duckdb'


def load_aligned():
    db = duckdb.connect(str(DB_PATH), read_only=True)
    tables = [r[0] for r in db.execute("SHOW TABLES").fetchall() if r[0].startswith('c_')]
    data = {}
    for t in tables:
        df = db.execute(f"SELECT ts_ms, ts, open, close FROM {t} ORDER BY ts_ms").df()
        data[t[2:]] = df
    db.close()
    base = data[list(data.keys())[0]][['ts_ms','ts']].copy()
    for coin, df in data.items():
        base = base.merge(df[['ts_ms','close','open']].rename(
            columns={'close':f'c_{coin}','open':f'o_{coin}'}), on='ts_ms', how='inner')
    return base.sort_values('ts_ms').reset_index(drop=True), list(data.keys())


def backtest_pair_momentum(df, a, b, window=168, z_in=2.0, z_out=0.5, z_stop=3.5,
                            max_hold=96, pos_usd=5000, taker_bps=5, slip_bps=2):
    ca = df[f'c_{a}'].values; cb = df[f'c_{b}'].values
    oa = df[f'o_{a}'].values; ob = df[f'o_{b}'].values
    tss = df['ts'].values; n = len(df)
    s = pd.Series(np.log(ca) - np.log(cb))
    mu = s.shift(1).rolling(window).mean()
    sd = s.shift(1).rolling(window).std()
    z = ((s - mu) / sd).values
    fee = (taker_bps + slip_bps) / 10000 * pos_usd * 4
    trades, in_pos = [], None
    for t in range(n - 1):
        zt = z[t]
        if np.isnan(zt): continue
        if in_pos is not None:
            held = t - in_pos['ei']
            reason = None
            if in_pos['side'] == 'long_a':
                if zt < z_out: reason = 'tp'
                elif zt > z_stop: reason = 'stop'
            else:
                if zt > -z_out: reason = 'tp'
                elif zt < -z_stop: reason = 'stop'
            if reason is None and held >= max_hold: reason = 'time'
            if reason:
                ft = t + 1
                if ft >= n or np.isnan(oa[ft]) or np.isnan(ob[ft]): continue
                ar = oa[ft]/in_pos['a_e'] - 1
                br = ob[ft]/in_pos['b_e'] - 1
                pnl_pct = (ar - br) if in_pos['side'] == 'long_a' else (br - ar)
                gross = pnl_pct * pos_usd; net = gross - fee
                trades.append({'enter':in_pos['ts_e'],'exit':tss[ft],'side':in_pos['side'],
                              'held_h':held,'gross':gross,'cost':fee,'net':net,'reason':reason})
                in_pos = None; continue
        if in_pos is None and abs(zt) > z_in:
            if t+1 >= n or np.isnan(oa[t+1]) or np.isnan(ob[t+1]): continue
            in_pos = {'ei':t+1,'ts_e':tss[t+1],
                      'side':'long_a' if zt > 0 else 'short_a',
                      'a_e':oa[t+1],'b_e':ob[t+1],'z_in':zt}
    return trades


def stats(trades, label='', cap=100_000):
    if not trades: return {'label':label,'n':0,'sharpe':0,'net':0}
    nets = np.array([t['net'] for t in trades])
    gross = sum(t['gross'] for t in trades)
    cost = sum(t['cost'] for t in trades)
    wins = (nets > 0).sum()
    pcts = nets / cap
    span_d = max(((pd.Timestamp(trades[-1]['exit']) - pd.Timestamp(trades[0]['enter'])).total_seconds()/86400), 0.1)
    tpd = len(trades) / span_d
    sh = (pcts.mean()/pcts.std()) * math.sqrt(tpd * 365) if pcts.std()>0 else 0
    nav=cap;peak=nav;mdd=0
    for t in trades:
        nav+=t['net']; peak=max(peak,nav)
        if peak>0: mdd=max(mdd,(peak-nav)/peak)
    return {'label':label,'n':len(trades),'win_pct':100*wins/len(trades),
            'gross':gross,'cost':cost,'net':nets.sum(),'sharpe':sh,'max_dd':mdd,
            'tpd':tpd,'span_d':span_d}


def run_all_pairs(df, coins, fee_tier='coinbase', window=168, **kwargs):
    """Backtest all C(n,2) pairs and return list of (pair, stats, trades)."""
    if fee_tier == 'coinbase':
        taker_bps, slip_bps = 5, 2     # 28 bps RT
    elif fee_tier == 'hyperliquid':
        taker_bps, slip_bps = 2.5, 1.5 # 16 bps RT (HL maker rebate -0.1, but assume mostly taker)
    elif fee_tier == 'maker_zero':
        taker_bps, slip_bps = 0, 1     # 4 bps RT (best case)
    else: raise ValueError(fee_tier)
    out = []
    for a, b in combinations(coins, 2):
        trades = backtest_pair_momentum(df, a, b, window=window,
                                         taker_bps=taker_bps, slip_bps=slip_bps,
                                         **kwargs)
        if not trades: continue
        s = stats(trades, f'{a}/{b}')
        out.append((a, b, s, trades))
    return out


def main():
    df, coins = load_aligned()
    print(f"{len(coins)} coins, {len(df):,} bars")
    n = len(df)
    train = df.iloc[:int(n*0.6)].reset_index(drop=True)
    test  = df.iloc[int(n*0.6):].reset_index(drop=True)
    print(f"Train: {len(train)} bars | Test: {len(test)} bars")

    # ──────────────────────────────────────────────────────────────
    # Step 1: ALL pairs, both fee tiers, full sample
    # ──────────────────────────────────────────────────────────────
    print("\n=== STEP 1: backtest ALL 190 pairs ===")
    for tier in ['coinbase', 'hyperliquid', 'maker_zero']:
        res = run_all_pairs(df, coins, fee_tier=tier)
        nets = [r[2]['net'] for r in res]
        positive = sum(1 for n in nets if n > 0)
        agg_net = sum(nets); agg_gross = sum(r[2]['gross'] for r in res)
        agg_cost = sum(r[2]['cost'] for r in res); agg_n = sum(r[2]['n'] for r in res)
        span = res[0][2]['span_d'] if res else 0
        tpd_agg = agg_n / max(span, 1)
        print(f"  [{tier:>12}]  pairs_tested={len(res)} positive_pairs={positive:>3}  "
              f"agg_trades={agg_n:>5} tpd={tpd_agg:>5.1f}  "
              f"agg_gross=${agg_gross:>+8.0f}  agg_cost=${agg_cost:>7.0f}  "
              f"agg_net=${agg_net:>+8.0f}")

    # ──────────────────────────────────────────────────────────────
    # Step 2: SELECT pairs on TRAIN, evaluate on TEST (no leakage)
    # ──────────────────────────────────────────────────────────────
    print("\n=== STEP 2: select profitable pairs on TRAIN, evaluate on TEST ===")
    print("(Hyperliquid fee tier)")
    train_res = run_all_pairs(train, coins, fee_tier='hyperliquid')
    # Pick top by sharpe AND profitable
    profitable_train = [(a,b,s,t) for (a,b,s,t) in train_res
                         if s['net'] > 0 and s['n'] >= 10 and s['sharpe'] > 0.5]
    profitable_train.sort(key=lambda x: -x[2]['sharpe'])
    print(f"  Train: {len(train_res)} pairs tested, {len(profitable_train)} qualify "
          f"(net>0, n≥10, sharpe>0.5)")
    top20 = profitable_train[:20]
    for a,b,s,_ in top20:
        print(f"    TRAIN {a:>5}/{b:>5}  n={s['n']:>3} win={s['win_pct']:>4.0f}%  "
              f"net=${s['net']:>+7.0f}  sharpe={s['sharpe']:>+5.2f}")

    # Apply same pairs to TEST
    print(f"\n  Same pairs on TEST (OOS):")
    test_trades_all = []
    for a, b, _, _ in top20:
        t_trades = backtest_pair_momentum(test, a, b, window=168, taker_bps=2.5, slip_bps=1.5)
        s = stats(t_trades, f'{a}/{b}')
        if s['n'] == 0:
            print(f"    OOS   {a:>5}/{b:>5}  no trades")
            continue
        test_trades_all.extend(t_trades)
        print(f"    OOS   {a:>5}/{b:>5}  n={s['n']:>3} win={s['win_pct']:>4.0f}%  "
              f"net=${s['net']:>+7.0f}  sharpe={s['sharpe']:>+5.2f}")
    print()
    s_train = stats([t for _,_,_,trs in top20 for t in trs], 'TRAIN portfolio')
    s_test  = stats(test_trades_all, 'TEST portfolio')
    print(f"  ═══════════════════════════════════════════════════════════")
    print(f"  TRAIN portfolio (top20): n={s_train['n']} tpd={s_train['tpd']:.1f} "
          f"net=${s_train['net']:+,.0f} sharpe={s_train['sharpe']:+.2f}")
    print(f"  TEST  portfolio (same pairs OOS): n={s_test['n']} tpd={s_test['tpd']:.1f} "
          f"net=${s_test['net']:+,.0f} sharpe={s_test['sharpe']:+.2f}")

    # ──────────────────────────────────────────────────────────────
    # Step 3: parameter sweep on best-candidate pair
    # ──────────────────────────────────────────────────────────────
    if top20:
        best = top20[0]
        a, b = best[0], best[1]
        print(f"\n=== STEP 3: parameter sweep for top pair {a}/{b} ===")
        for window in [48, 96, 168, 240]:
            for z_in in [1.5, 2.0, 2.5, 3.0]:
                t = backtest_pair_momentum(df, a, b, window=window, z_in=z_in,
                                            taker_bps=2.5, slip_bps=1.5)
                s = stats(t, f'win={window}h z_in={z_in}')
                if s['n'] == 0: continue
                print(f"    w={window:>3} z_in={z_in:>3}  n={s['n']:>3} win={s['win_pct']:>4.0f}%  "
                      f"net=${s['net']:>+6.0f}  sharpe={s['sharpe']:>+5.2f}")

if __name__ == '__main__':
    main()
