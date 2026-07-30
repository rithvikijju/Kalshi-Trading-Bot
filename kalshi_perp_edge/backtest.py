"""
Execution-realistic backtest of the perp lead-lag / basis-reversion scalp.

Signal (info available at close of minute t, no look-ahead):
    basis_bp(t) = (perp_mid(t)*MULT - spot(t)) / spot(t) * 1e4
    perp cheap (basis <= -thr) -> BUY perp ; perp rich (basis >= +thr) -> SELL perp.

Fills are TAKER, crossing the real candle bid/ask (so the full spread is paid),
fee = 0 (current Kalshi perp fee schedule). entry_lag bars models latency:
    lag=0 : fill at bar t      (fast; signal & quote contemporaneous)
    lag=1 : fill at bar t+1    (a full MINUTE late — pessimistic floor)
Exit is taker on the opposite side after H bars.

Reports event-study mean NET bp/trade across a thr x H x lag grid, plus a
non-overlapping portfolio sim (Sharpe, daily bp) at a chosen config.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COIN = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
MULT = {'BTC': 1e4, 'ETH': 1e3}[COIN]


def load():
    df = pd.read_parquet(DATA / f'merged_{COIN}.parquet')  # produced by analyze.py
    df = df.reset_index(drop=True)
    return df


def event_study(df, thr, H, lag):
    """Every qualifying bar -> one hypothetical taker round-trip. Returns net bp array."""
    bid = df.bid.values * MULT
    ask = df.ask.values * MULT
    spot = df.spot.values
    basis = df.basis_bp.values
    n = len(df)
    pnl = []
    for t in range(n - lag - H):
        s = basis[t]
        if abs(s) < thr:
            continue
        ei = t + lag                 # entry bar
        xi = t + lag + H             # exit bar
        if s <= -thr:                # cheap -> long: buy ask, sell bid later
            entry = ask[ei]; exit_ = bid[xi]; ret = (exit_ - entry)
        else:                        # rich -> short: sell bid, buy ask later
            entry = bid[ei]; exit_ = ask[xi]; ret = (entry - exit_)
        pnl.append(ret / spot[ei] * 1e4)
    return np.array(pnl)


def portfolio(df, thr, H, lag):
    """Non-overlapping trades: enter on signal, block until exit. Realistic count."""
    bid = df.bid.values * MULT
    ask = df.ask.values * MULT
    spot = df.spot.values
    basis = df.basis_bp.values
    ts = df.ts.values
    n = len(df)
    trades = []
    t = 0
    while t < n - lag - H:
        s = basis[t]
        if abs(s) < thr:
            t += 1; continue
        ei, xi = t + lag, t + lag + H
        if s <= -thr:
            ret = (bid[xi] - ask[ei])
        else:
            ret = (ask[ei] - bid[xi])
        trades.append((ts[ei], ret / spot[ei] * 1e4))
        t = xi                       # no overlap
    return pd.DataFrame(trades, columns=['ts', 'bp'])


def main():
    df = load()
    days = (df.ts.iloc[-1] - df.ts.iloc[0]) / 86400
    print(f'{COIN}: {len(df)} bars, {days:.1f} days, zero-fee perp\n')

    print('EVENT STUDY — mean NET bp/trade  (after full spread, fee=0)')
    print(f'{"thr":>5} {"H":>3} | {"lag=0 net":>10} {"n":>6} | {"lag=1 net":>10} {"n":>6}')
    for thr in (3, 5, 8, 12):
        for H in (1, 2, 3):
            r0 = event_study(df, thr, H, 0)
            r1 = event_study(df, thr, H, 1)
            m0 = r0.mean() if len(r0) else float('nan')
            m1 = r1.mean() if len(r1) else float('nan')
            print(f'{thr:>5} {H:>3} | {m0:>10.3f} {len(r0):>6} | {m1:>10.3f} {len(r1):>6}')

    print('\nPORTFOLIO SIM (non-overlapping)')
    print(f'{"thr":>5} {"H":>3} {"lag":>4} | {"trades":>7} {"net bp/tr":>9} {"bp/day":>8} '
          f'{"win%":>6} {"Sharpe(d)":>9}')
    for (thr, H, lag) in [(5, 1, 0), (8, 1, 0), (8, 2, 0), (5, 1, 1), (8, 1, 1), (12, 1, 1)]:
        tr = portfolio(df, thr, H, lag)
        if len(tr) < 5:
            print(f'{thr:>5} {H:>3} {lag:>4} | {len(tr):>7}  (too few)'); continue
        tr['day'] = (tr.ts // 86400)
        daily = tr.groupby('day').bp.sum()
        sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else float('nan')
        print(f'{thr:>5} {H:>3} {lag:>4} | {len(tr):>7} {tr.bp.mean():>9.3f} '
              f'{daily.mean():>8.1f} {(tr.bp>0).mean()*100:>5.0f}% {sharpe:>9.2f}')


if __name__ == '__main__':
    main()
