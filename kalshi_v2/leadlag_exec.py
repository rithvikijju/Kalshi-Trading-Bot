"""
Executable + settle-based test of the Coinbase->Kalshi lead-lag, EVENT-DRIVEN.

leadlag.py showed a real mid-level lag (~3.5c capturable 2s after a >=$75 jump).
That was a MID number. This script asks the make-or-break question: does it survive
EXECUTABLE prices (real spread) and a realistic reaction lag?

Trade: on a spot jump, take the favored side (up->buy YES, down->buy NO) ~1s later
(REACT lag), then either (A) hold to settlement, or (B) exit ~15s later (the perp-
analog "convergence scalp" on a binary). Reported under three cost models so we can
see exactly what the spread does:
  mid   : mid-to-mid  (raw convergence, no spread/fee)         -> is there signal?
  taker : enter at ask, exit at bid, pay 2 fees                -> realistic taker
  maker : enter at bid, exit at ask, 0 fee (best case)         -> upper bound

Also prints the entry spread distribution (the cost that decides taker viability).
Data caveat: binary book freezes :55 (see kalshi_capture_55_freeze) so 15s exits and
settlement use what's available; jumps in the final 5min are dropped.
"""
import duckdb, numpy as np, statistics
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'kalshi_k6'))
from config import kalshi_fee  # correct Kalshi fee

DB = str(Path(__file__).resolve().parent.parent / 'live_capture_gapless_20260512_paused.duckdb')
NEAR = 1000.0
MIN_SNAPS = 3000
JUMP = 75.0      # $ spot move in 1s defining an event
REACT = 1        # seconds after the jump we can actually act (reaction latency)
HOLD = 15        # seconds held for the convergence-exit variant


def log(m): print(m, flush=True)


def main():
    con = duckdb.connect(DB, read_only=True)

    log('[1] liquid near-the-money markets + settlement spot ...')
    con.execute(f"""
    create temp table mkt as
    select market_ticker,
           cast(regexp_extract(market_ticker,'T([0-9.]+)$',1) as double) strike,
           date_trunc('hour', max(received_at_utc::timestamp)) + interval 1 hour as close_ts
    from ws_orderbook_top_dedup group by 1
    """)
    con.execute(f"""
    create temp table liq as
    select o.market_ticker
    from ws_orderbook_top_dedup o join mkt m using(market_ticker)
    where o.yes_bid is not null and o.yes_ask is not null and o.yes_ask>0 and o.yes_ask<1
      and abs(o.btc_spot - m.strike) < {NEAR}
    group by 1 having count(*) >= {MIN_SNAPS}
    """)
    con.execute("""
    create temp table settle as
    select m.market_ticker, m.strike, m.close_ts, epoch_ns(m.close_ts) close_ns,
           c.price settle_spot
    from mkt m asof join coinbase_ticker_all c on c.received_at_ns <= epoch_ns(m.close_ts)
    where m.market_ticker in (select market_ticker from liq)
    """)
    log(f'    liquid markets: {con.execute("select count(*) from liq").fetchone()[0]}')

    log('[2] 1-second bars (last quote per second) ...')
    df = con.execute("""
    with base as (
      select o.market_ticker, (o.received_at_ns/1000000000)::bigint sec, o.received_at_ns,
             o.btc_spot spot, o.yes_bid, o.yes_ask, o.no_bid, o.no_ask
      from ws_orderbook_top_dedup o join liq using(market_ticker)
      where o.yes_bid is not null and o.yes_ask is not null and o.yes_ask>0 and o.yes_ask<1
        and o.no_bid is not null and o.no_ask is not null
    )
    select b.market_ticker, b.sec,
           arg_max(spot, received_at_ns) spot,
           arg_max(yes_bid, received_at_ns) yes_bid, arg_max(yes_ask, received_at_ns) yes_ask,
           arg_max(no_bid, received_at_ns) no_bid,   arg_max(no_ask, received_at_ns) no_ask,
           s.strike, s.settle_spot
    from base b join settle s using(market_ticker)
    group by b.market_ticker, b.sec, s.strike, s.settle_spot
    order by b.market_ticker, b.sec
    """).df()
    log(f'    bars: {len(df):,}')

    trades = []  # each: dict of pnls per variant + meta
    for mt, g in df.groupby('market_ticker', sort=False):
        g = g.set_index('sec')
        full = np.arange(g.index.min(), g.index.max() + 1)
        spot = g['spot'].reindex(full).ffill().to_numpy()
        yb = g['yes_bid'].reindex(full).ffill().to_numpy()
        ya = g['yes_ask'].reindex(full).ffill().to_numpy()
        nb = g['no_bid'].reindex(full).ffill().to_numpy()
        na = g['no_ask'].reindex(full).ffill().to_numpy()
        strike = g['strike'].iloc[0]; settle_spot = g['settle_spot'].iloc[0]
        yes_won = 1.0 if settle_spot >= strike else 0.0
        dspot = np.diff(spot, prepend=spot[0])
        jumps = np.where(np.abs(dspot) >= JUMP)[0]
        for i in jumps:
            e = i + REACT                 # entry bar
            x = e + HOLD                  # convergence-exit bar
            if x >= len(spot):
                continue
            up = dspot[i] > 0
            # favored side: up-jump -> buy YES ; down-jump -> buy NO
            if up:
                e_ask, e_bid = ya[e], yb[e]; x_ask, x_bid = ya[x], yb[x]
                win = yes_won
            else:
                e_ask, e_bid = na[e], nb[e]; x_ask, x_bid = na[x], nb[x]
                win = 1.0 - yes_won
            if not (0 < e_ask < 1 and 0 < e_bid < 1):
                continue
            e_mid = (e_ask + e_bid) / 2; x_mid = (x_ask + x_bid) / 2
            spread = e_ask - e_bid
            fee_e, fee_x = kalshi_fee(e_ask), kalshi_fee(x_bid)
            trades.append(dict(
                up=up, spread=spread,
                # convergence-exit (perp-analog scalp)
                B_mid=x_mid - e_mid,
                B_taker=(x_bid - e_ask) - fee_e - fee_x,
                B_maker=(x_ask - e_bid),
                # hold-to-settlement
                A_taker=(win - e_ask) - kalshi_fee(e_ask),
                A_mid=(win - e_mid),
            ))

    n = len(trades)
    log(f'\n{"="*60}\nEXECUTABLE LEAD-LAG TEST  (n={n} jump-trades, REACT={REACT}s, HOLD={HOLD}s)\n{"="*60}')
    if not n:
        log('no trades'); return

    spreads = sorted(t['spread'] for t in trades)
    log('Entry spread (yes/no ask-bid), cents:  p25 %.2f  median %.2f  p75 %.2f  mean %.2f' % (
        spreads[n//4]*100, spreads[n//2]*100, spreads[3*n//4]*100,
        statistics.fmean(spreads)*100))

    def stat(key):
        v = [t[key] for t in trades]
        return statistics.fmean(v)*100, (statistics.fmean([1 for x in v if x>0])/1 if False else sum(1 for x in v if x>0)/len(v))*100

    log('\nConvergence-exit scalp (enter on jump, exit %ds later):' % HOLD)
    for k, lbl in [('B_mid','mid->mid (raw signal)'), ('B_taker','TAKER (pay spread+2 fees)'), ('B_maker','MAKER (earn spread, 0 fee)')]:
        m, w = stat(k); log('  %-26s mean %+6.3fc/ct   win %4.1f%%' % (lbl, m, w))
    log('\nHold-to-settlement:')
    for k, lbl in [('A_mid','at mid (no spread/fee)'), ('A_taker','TAKER (ask + fee)')]:
        m, w = stat(k); log('  %-26s mean %+6.3fc/ct   win %4.1f%%' % (lbl, m, w))
    log('='*60)


if __name__ == '__main__':
    main()
