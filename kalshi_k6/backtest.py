"""
Conservative historical backtest for the K6 spot-displacement strategy.

Replays the EXACT scan_k6() decision rule (kalshi_k6/strategy.py) against the
7-day live capture in live_capture_gapless_20260512_paused.duckdb.

Design choices for an HONEST (anti-optimistic) result:
  * Outcome = real BTC spot at the true settlement instant (next :00 boundary),
    pulled from coinbase_ticker_all via ASOF join -- NOT the frozen :55 book,
    which never shows 0/1 convergence.
  * One position per market (mirrors live: skip markets already held), taken at
    the EARLIEST qualifying snapshot.
  * Fill realism: a signal only "fills" if the same-side ask actually persisted
    (present, qty>=min_depth, not worse by >1c) for >=PERSIST_SEC after the
    signal. Arbs that evaporate sub-second are counted as no-fills.
  * Correct Kalshi fee (kalshi_fee from config: peaks 1.75c at p=0.5).
  * qty = 1 everywhere, so win-rate / edge-per-contract are undistorted by Kelly.

Reports atomic-fill vs realistic-fill side by side to explain the 88% vs 77% gap.
"""
import sys, math
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CFG, kalshi_fee  # noqa: E402

DB = str(Path(__file__).resolve().parent.parent / 'live_capture_gapless_20260512_paused.duckdb')
PERSIST_SEC = 1.5          # ask must survive this long to count as a fill
MIN_DEPTH = 5              # _book_passes min_depth
MAX_SPREAD = 0.05          # _book_passes max_spread
WORSEN_TOL = 0.01          # ask may drift up by at most this and still "persist"
P_WIN = CFG['expected_win_prob']   # 0.88, used in scan_k6 edge gate
NEAR_STRIKE = 50.0         # |settle_spot - strike| below this => fragile outcome label

# K6 buckets: (t_lo, t_hi, d_lo, d_hi, side, label)
BUCKETS = [
    (0,   300,   50,   200,  'yes', 'A1'),
    (0,   300,   200,  500,  'yes', 'A2'),
    (0,   300,  -200,  -50,  'no',  'A1n'),
    (0,   300,  -500, -200,  'no',  'A2n'),
    (300, 900,   50,   200,  'yes', 'B1'),
    (300, 900,   200,  500,  'yes', 'B2'),
    (300, 900,  -200,  -50,  'no',  'B1n'),
    (300, 900,  -500, -200,  'no',  'B2n'),
]


def log(m): print(m, flush=True)


def main():
    con = duckdb.connect(DB, read_only=True)

    log('[1/5] per-market strike + settlement time (close = next :00 after book end)')
    con.execute("""
    create temp table mkt as
    select market_ticker,
           cast(regexp_extract(market_ticker, 'T([0-9.]+)$', 1) as double) as strike,
           date_trunc('hour', max(received_at_utc::timestamp)) + interval 1 hour as close_ts
    from ws_orderbook_top_dedup
    group by 1
    """)
    log('       markets: %d' % con.execute('select count(*) from mkt').fetchone()[0])

    log('[2/5] settlement spot via ASOF join to coinbase at close instant')
    con.execute("""
    create temp table settle as
    select m.market_ticker, m.strike, m.close_ts,
           epoch_ns(m.close_ts) as close_ns, c.price as settle_spot
    from mkt m
    asof join coinbase_ticker_all c
      on c.received_at_ns <= epoch_ns(m.close_ts)
    """)
    nset = con.execute('select count(*) from settle where settle_spot is not null').fetchone()[0]
    log('       markets with settlement spot: %d' % nset)

    log('[3/5] per-minute annualized realized vol from coinbase (rolling 15m)')
    con.execute("""
    create temp table volm as
    with m as (
      select date_trunc('minute', received_at_utc::timestamp) as mn,
             last(price order by received_at_ns) as px
      from coinbase_ticker_all group by 1),
    r as (select mn, ln(px / lag(px) over (order by mn)) as lr from m)
    select mn,
           stddev_samp(lr) over (order by mn rows between 14 preceding and current row)
             * sqrt(525600.0) as rv
    from r
    """)

    log('[4/5] coarse candidate snapshots (in time/dist window, vol known)')
    # Pull only snapshots that *could* match a bucket; exact bucket/book/edge in python.
    con.execute("""
    create temp table cand as
    select o.market_ticker, o.received_at_ns,
           s.strike, s.close_ns, s.settle_spot, (s.settle_spot >= s.strike) as yes_won,
           o.btc_spot, (o.btc_spot - s.strike) as spot_dist,
           (s.close_ns - o.received_at_ns)/1e9 as secs_to_close,
           o.yes_bid, o.yes_ask, o.yes_ask_qty, o.no_bid, o.no_ask, o.no_ask_qty,
           v.rv
    from ws_orderbook_top_dedup o
    join settle s using(market_ticker)
    left join volm v on v.mn = date_trunc('minute', o.received_at_utc::timestamp)
    where s.settle_spot is not null
      and v.rv is not null
      and (s.close_ns - o.received_at_ns)/1e9 between 0 and 900
      and abs(o.btc_spot - s.strike) between 50 and 500
    """)
    nc = con.execute('select count(*) from cand').fetchone()[0]
    log('       coarse candidate snapshots: %d' % nc)

    rows = con.execute("""
        select * from cand order by market_ticker, received_at_ns
    """).fetchnumpy()
    import numpy as np
    n = len(rows['market_ticker'])

    log('[5/5] apply exact scan_k6 gate, pick earliest per market, settle')

    def book_ok(side, i):
        if side == 'yes':
            ask, q, bid = rows['yes_ask'][i], rows['yes_ask_qty'][i], rows['yes_bid'][i]
        else:
            ask, q, bid = rows['no_ask'][i], rows['no_ask_qty'][i], rows['no_bid'][i]
        if ask is None or bid is None: return None
        if not (0 < ask < 1): return None
        if (q or 0) < MIN_DEPTH: return None
        if (ask - bid) > MAX_SPREAD: return None
        return float(ask)

    entries = {}   # market -> dict (earliest qualifying signal)
    for i in range(n):
        mt = rows['market_ticker'][i]
        if mt in entries:
            continue  # already have an (earlier) entry for this market
        secs = rows['secs_to_close'][i]; dist = rows['spot_dist'][i]; rv = rows['rv'][i]
        for tlo, thi, dlo, dhi, side, label in BUCKETS:
            if not (tlo <= secs <= thi): continue
            if not (dlo <= dist <= dhi): continue
            if side == 'yes' and rv < CFG['k14_yes_min_rv']: continue
            if side == 'no'  and rv >= CFG['k14_no_max_rv']: continue
            ask = book_ok(side, i)
            if ask is None: continue
            fee = kalshi_fee(ask)
            exp_edge = P_WIN * (1 - ask - fee) + (1 - P_WIN) * (-(ask + fee))
            if exp_edge < 0.005: continue
            won = bool(rows['yes_won'][i]) if side == 'yes' else (not bool(rows['yes_won'][i]))
            entries[mt] = dict(
                market=mt, entry_ns=int(rows['received_at_ns'][i]), side=side, bucket=label,
                ask=ask, fee=fee, secs=float(secs), dist=float(dist), rv=float(rv),
                won=won, settle_spot=float(rows['settle_spot'][i]), strike=float(rows['strike'][i]),
                near_strike=abs(rows['settle_spot'][i] - rows['strike'][i]) < NEAR_STRIKE,
            )
            break
    log('       distinct entry signals (atomic, pre-persistence): %d' % len(entries))

    # persistence check: same-side ask present & qty>=MIN_DEPTH & <= ask+tol within PERSIST_SEC
    log('       checking ask persistence (%.1fs) per entry ...' % PERSIST_SEC)
    for e in entries.values():
        side = e['side']; col_ask = f'{side}_ask'; col_q = f'{side}_ask_qty'
        r = con.execute(f"""
            select count(*) from ws_orderbook_top_dedup
            where market_ticker = ?
              and received_at_ns >  ?
              and received_at_ns <= ? + {int(PERSIST_SEC*1e9)}
              and {col_ask} is not null and {col_ask} <= ? + {WORSEN_TOL}
              and {col_ask} > 0 and {col_ask} < 1
              and coalesce({col_q},0) >= {MIN_DEPTH}
        """, [e['market'], e['entry_ns'], e['entry_ns'], e['ask']]).fetchone()[0]
        e['filled'] = r > 0

    # ---- report ----
    E = list(entries.values())
    def stats(items):
        if not items: return (0, 0.0, 0.0, 0.0)
        w = sum(1 for x in items if x['won'])
        pnl = [ (1 - x['ask'] - x['fee']) if x['won'] else -(x['ask'] + x['fee']) for x in items ]
        return (len(items), w/len(items), sum(pnl)/len(items), sum(pnl))

    atom = stats(E)
    filled = [e for e in E if e['filled']]
    real = stats(filled)
    nofill = len(E) - len(filled)

    log('\n' + '='*64)
    log('K6 BACKTEST  (capture %s)' % Path(DB).name)
    log('='*64)
    log('Settlement: BTC spot at next :00 vs strike. qty=1. Fee=Kalshi formula.')
    log('NOTE: books freeze at :55, so 0-5min "A" buckets are near-unobservable.')
    log('')
    log('ATOMIC FILL (assume every signal fills at observed ask):')
    log('  trades=%d  win=%.1f%%  edge/ct=%+.4f$  total=%+.2f$' %
        (atom[0], atom[1]*100, atom[2], atom[3]))
    log('REALISTIC FILL (ask survived >=%.1fs):' % PERSIST_SEC)
    log('  trades=%d  win=%.1f%%  edge/ct=%+.4f$  total=%+.2f$   (no-fill: %d, %.0f%%)' %
        (real[0], real[1]*100, real[2], real[3], nofill,
         100*nofill/max(1, atom[0])))
    near = sum(1 for e in filled if e['near_strike'])
    log('  fragile labels (|settle-strike|<$%d at close): %d (%.0f%%)' %
        (NEAR_STRIKE, near, 100*near/max(1, len(filled))))

    # bootstrap 95% CI on realistic edge/contract and win rate
    pnls = np.array([ (1 - x['ask'] - x['fee']) if x['won'] else -(x['ask'] + x['fee'])
                      for x in filled ])
    wins = np.array([1 if x['won'] else 0 for x in filled])
    rng = np.random.default_rng(7)
    bs_edge, bs_win = [], []
    for _ in range(5000):
        idx = rng.integers(0, len(pnls), len(pnls))
        bs_edge.append(pnls[idx].mean()); bs_win.append(wins[idx].mean())
    lo, hi = np.percentile(bs_edge, [2.5, 97.5])
    wlo, whi = np.percentile(bs_win, [2.5, 97.5])
    log('  bootstrap 95%% CI: edge/ct [%+.4f, %+.4f]$   win [%.1f%%, %.1f%%]' %
        (lo, hi, wlo*100, whi*100))
    log('  >> edge CI %s zero' % ('EXCLUDES' if lo > 0 else 'INCLUDES'))

    log('\nBy bucket (realistic fills):')
    log('  %-5s %6s %7s %10s' % ('bkt', 'n', 'win%', 'edge/ct$'))
    for _,_,_,_,_,label in BUCKETS:
        b = [e for e in filled if e['bucket'] == label]
        if not b:
            log('  %-5s %6d %7s %10s' % (label, 0, '-', '-')); continue
        s = stats(b)
        log('  %-5s %6d %6.1f%% %+10.4f' % (label, s[0], s[1]*100, s[2]))

    # A vs B coverage diagnostic
    a_ct = sum(1 for e in E if e['bucket'].startswith('A'))
    log('\nCoverage: A-bucket(0-5min) entries=%d  B-bucket(5-15min) entries=%d' %
        (a_ct, len(E)-a_ct))
    log('='*64)


if __name__ == '__main__':
    main()
