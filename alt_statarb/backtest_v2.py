"""Variant strategies to find an edge.

V1 (cointegration mean-reversion) LOST gross. So either:
  - Direction is wrong (momentum exists where I expected reversion)
  - Or the mechanism needs different framing

Testing 4 new variants:
  C. Pair MOMENTUM (reverse of V1): enter in direction of the spread move
  D. CROSS-SECTIONAL momentum: long top-N coins by trailing return, short bottom-N
  E. CROSS-SECTIONAL reversal: short top, long bottom (1-day reversal premium)
  F. BTC dominance signal: when BTC outperforms, expect altseason next
"""
from __future__ import annotations
import math, sys
from pathlib import Path
from itertools import combinations
import duckdb, pandas as pd, numpy as np

DATA = Path('alt_statarb/data')
DB_PATH = DATA / 'alts_1h.duckdb'

# Costs
TAKER_FEE_BPS = 5
SLIP_BPS = 2


def load_all() -> dict[str, pd.DataFrame]:
    db = duckdb.connect(str(DB_PATH), read_only=True)
    tables = [r[0] for r in db.execute("SHOW TABLES").fetchall() if r[0].startswith('c_')]
    out = {}
    for t in tables:
        coin = t[2:]
        df = db.execute(f"SELECT ts_ms, ts, open, high, low, close, volume FROM {t} ORDER BY ts_ms").df()
        out[coin] = df
    db.close()
    return out


def align_universe(data: dict) -> pd.DataFrame:
    if not data: return pd.DataFrame()
    base = data[list(data.keys())[0]][['ts_ms', 'ts']].copy()
    for coin, df in data.items():
        base = base.merge(df[['ts_ms', 'close', 'open']].rename(
            columns={'close': f'c_{coin}', 'open': f'o_{coin}'}),
            on='ts_ms', how='inner')
    return base.sort_values('ts_ms').reset_index(drop=True)


# ─── C. Pair MOMENTUM (reverse V1) ─────────────────────────────────
def backtest_pair_momentum(df, a, b, window=168, z_in=2.0, z_out=0.5,
                            z_stop=3.5, max_hold=96, pos_usd=5000):
    ca = df[f'c_{a}'].values; cb = df[f'c_{b}'].values
    oa = df[f'o_{a}'].values; ob = df[f'o_{b}'].values
    tss = df['ts'].values; n = len(df)
    s = pd.Series(np.log(ca) - np.log(cb))
    mu = s.shift(1).rolling(window).mean()
    sd = s.shift(1).rolling(window).std()
    z = ((s - mu) / sd).values
    fee = (TAKER_FEE_BPS + SLIP_BPS) / 10000 * pos_usd * 4
    trades, in_pos = [], None
    for t in range(n - 1):
        zt = z[t]
        if np.isnan(zt): continue
        if in_pos is not None:
            held = t - in_pos['ei']
            # Exit: spread reverts toward 0 (we're long the trend) OR stop OR time
            reason = None
            if in_pos['side'] == 'long_a':   # entered at z>z_in
                if zt < z_out: reason = 'reverted'  # back to neutral
                elif zt > z_stop: reason = 'stop_break_up'
            else:  # short_a, entered at z < -z_in
                if zt > -z_out: reason = 'reverted'
                elif zt < -z_stop: reason = 'stop_break_down'
            if reason is None and held >= max_hold: reason = 'time'
            if reason:
                ft = t + 1
                if np.isnan(oa[ft]) or np.isnan(ob[ft]): continue
                ar = oa[ft]/in_pos['a_e'] - 1
                br = ob[ft]/in_pos['b_e'] - 1
                # MOMENTUM: when z>0 we EXPECT z to keep going up → A keeps outperforming → long A
                pnl_pct = (ar - br) if in_pos['side'] == 'long_a' else (br - ar)
                gross = pnl_pct * pos_usd; net = gross - fee
                trades.append({'a':a,'b':b,'enter':in_pos['ts_e'],'exit':tss[ft],
                              'side':in_pos['side'],'held_h':held,
                              'z_in':in_pos['z_in'],'gross':gross,'cost':fee,'net':net,
                              'reason':reason})
                in_pos = None; continue
        if in_pos is None and abs(zt) > z_in:
            if np.isnan(oa[t+1]) or np.isnan(ob[t+1]): continue
            # MOMENTUM: z>0 means A is winning → LONG A
            in_pos = {'ei':t+1,'ts_e':tss[t+1],
                      'side':'long_a' if zt > 0 else 'short_a',
                      'a_e':oa[t+1],'b_e':ob[t+1],'z_in':zt}
    return trades


# ─── D/E. Cross-sectional rank strategy ────────────────────────────
def backtest_xs_rank(df, coins, lookback_h=24, hold_h=24, top_n=3,
                     direction='mom', pos_per_leg_usd=1000):
    """At each rebalance: rank coins by trailing return.
    direction='mom': long top, short bottom
    direction='rev': short top, long bottom (reversal premium)
    """
    closes = df[[f'c_{c}' for c in coins]].values
    opens  = df[[f'o_{c}' for c in coins]].values
    tss = df['ts'].values; n = len(df)
    fee_per_leg = (TAKER_FEE_BPS + SLIP_BPS) / 10000 * pos_per_leg_usd * 2  # entry+exit

    trades = []
    t = lookback_h + 1
    while t + hold_h < n - 1:
        # Trailing return ending at t-1 (no peek)
        prev = closes[t - lookback_h]
        now = closes[t - 1]
        rets = (now / prev) - 1
        # Filter NaN
        ok = ~np.isnan(rets)
        if ok.sum() < 2 * top_n:
            t += hold_h; continue
        idx = np.argsort(rets)
        # top: highest returns; bottom: lowest
        top_idx = [i for i in idx[::-1] if ok[i]][:top_n]
        bot_idx = [i for i in idx if ok[i]][:top_n]
        # Enter at t open, exit at t+hold_h open
        if t+hold_h+1 >= n: break
        en = t  # use bar t's OPEN as entry (signal computed from data through t-1)
        ex = t + hold_h
        en_p = opens[en]; ex_p = opens[ex]
        for i in top_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i] / en_p[i] - 1
            side = 'long' if direction == 'mom' else 'short'
            gross = (ret if side == 'long' else -ret) * pos_per_leg_usd
            net = gross - fee_per_leg
            trades.append({'enter':tss[en],'exit':tss[ex],'coin':coins[i],'side':side,
                           'gross':gross,'cost':fee_per_leg,'net':net,'group':'top'})
        for i in bot_idx:
            if np.isnan(en_p[i]) or np.isnan(ex_p[i]): continue
            ret = ex_p[i] / en_p[i] - 1
            side = 'short' if direction == 'mom' else 'long'
            gross = (ret if side == 'long' else -ret) * pos_per_leg_usd
            net = gross - fee_per_leg
            trades.append({'enter':tss[en],'exit':tss[ex],'coin':coins[i],'side':side,
                           'gross':gross,'cost':fee_per_leg,'net':net,'group':'bottom'})
        t += hold_h
    return trades


def summarize(trades, label, starting_cap=100_000):
    if not trades: return {'label':label,'n':0}
    nets = np.array([t['net'] for t in trades])
    gross = sum(t['gross'] for t in trades); cost = sum(t['cost'] for t in trades)
    wins = (nets > 0).sum()
    span_d = (pd.Timestamp(trades[-1]['exit']) - pd.Timestamp(trades[0]['enter'])).total_seconds() / 86400
    tpd = len(trades) / max(span_d, 1)
    pcts = nets / starting_cap
    sharpe = pcts.mean() / pcts.std() * math.sqrt(tpd * 365) if pcts.std() > 0 else 0
    nav=starting_cap;peak=nav;mdd=0
    for t in trades:
        nav += t['net']; peak = max(peak, nav)
        if peak>0: mdd = max(mdd, (peak-nav)/peak)
    return {'label':label,'n':len(trades),'win_pct':wins/len(trades)*100,
            'gross':gross,'cost':cost,'net':nets.sum(),'sharpe':sharpe,
            'max_dd':mdd,'tpd':tpd,'span_d':span_d}

def fmt(s):
    if s.get('n',0)==0: return f"  [{s['label']}] no trades"
    return (f"  [{s['label']:38}] n={s['n']:>4}  win={s['win_pct']:>4.0f}%  "
            f"tpd={s['tpd']:>5.1f}  gross=${s['gross']:>+9.0f}  cost=${s['cost']:>7.0f}  "
            f"net=${s['net']:>+9.0f}  sharpe={s['sharpe']:>+5.2f}  DD={s['max_dd']*100:>4.1f}%")


def main():
    print("Loading…")
    data = load_all()
    coins = list(data.keys())
    df = align_universe(data)
    print(f"  {len(coins)} coins, {len(df):,} bars  {df['ts'].iloc[0]} → {df['ts'].iloc[-1]}")

    # ─── C. Pair MOMENTUM (reverse of mean reversion) ──────────────
    print("\n=== STRATEGY C: pair MOMENTUM (follow the spread, not fade it) ===")
    top_pairs = [('APT','BCH'),('APT','POL'),('AAVE','BCH'),('APT','BTC'),
                 ('APT','ETH'),('APT','XRP'),('APT','LINK'),('ADA','BCH'),
                 ('APT','XLM'),('AAVE','POL')]
    all_c = []
    for a, b in top_pairs:
        if f'c_{a}' not in df.columns or f'c_{b}' not in df.columns: continue
        trades = backtest_pair_momentum(df, a, b)
        all_c.extend(trades)
        print(fmt(summarize(trades, f'{a}/{b}')))
    print(fmt(summarize(all_c, 'STRATEGY C TOTAL (10 pairs)')))

    # ─── D. Cross-sectional MOMENTUM ───────────────────────────────
    print("\n=== STRATEGY D: cross-sectional MOMENTUM (long top, short bottom) ===")
    for lookback in [12, 24, 48, 168]:
        for hold in [12, 24, 48]:
            t = backtest_xs_rank(df, coins, lookback_h=lookback, hold_h=hold,
                                  top_n=3, direction='mom', pos_per_leg_usd=500)
            print(fmt(summarize(t, f'XS-MOM lookback={lookback}h hold={hold}h')))

    # ─── E. Cross-sectional REVERSAL ───────────────────────────────
    print("\n=== STRATEGY E: cross-sectional REVERSAL (long bottom, short top) ===")
    for lookback in [12, 24, 48, 168]:
        for hold in [12, 24, 48]:
            t = backtest_xs_rank(df, coins, lookback_h=lookback, hold_h=hold,
                                  top_n=3, direction='rev', pos_per_leg_usd=500)
            print(fmt(summarize(t, f'XS-REV lookback={lookback}h hold={hold}h')))

if __name__ == '__main__':
    main()
