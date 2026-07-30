"""
Coinbase -> Kalshi BTC lead-lag measurement.

Question: when Coinbase spot moves, how many SECONDS does the Kalshi book take to
reprice, and is there an exploitable window? Decides whether a faster fair-value
pipeline can pay (and as a taker vs a maker).

Data: live_capture_gapless_20260512_paused.duckdb, ws_orderbook_top_dedup (carries
btc_spot + Kalshi top-of-book per snapshot, ns timestamps). 7 days, KXBTCD hourly.

Two analyses, pooled over liquid near-the-money market-hours:
  1. Cross-correlation: 1s spot returns vs 1s Kalshi yes-mid changes, lags -3..30s.
     Peak lag = how long Kalshi trails spot.
  2. Event study: after a spot jump (>=$75 in <=2s), what fraction of the eventual
     (60s) Kalshi mid response is realized at 1s / 5s / 15s / 30s?
"""
import duckdb, numpy as np
from pathlib import Path

DB = str(Path(__file__).resolve().parent.parent / 'live_capture_gapless_20260512_paused.duckdb')
NEAR = 1000.0      # |spot-strike| band defining a responsive near-the-money book ($)
MIN_SNAPS = 3000   # min two-sided snapshots for a market to count as "liquid"
JUMP = 75.0        # spot move (1s) that defines an "event"


def log(m): print(m, flush=True)


def main():
    con = duckdb.connect(DB, read_only=True)

    log('[1] selecting liquid near-the-money markets ...')
    con.execute(f"""
    create temp table mkt as
    select market_ticker,
           cast(regexp_extract(market_ticker,'T([0-9.]+)$',1) as double) strike
    from ws_orderbook_top_dedup group by 1
    """)
    con.execute(f"""
    create temp table liq as
    select o.market_ticker
    from ws_orderbook_top_dedup o join mkt m using(market_ticker)
    where o.yes_bid is not null and o.yes_ask is not null
      and o.yes_ask > 0 and o.yes_ask < 1
      and abs(o.btc_spot - m.strike) < {NEAR}
    group by 1 having count(*) >= {MIN_SNAPS}
    """)
    nliq = con.execute('select count(*) from liq').fetchone()[0]
    log(f'    liquid market-hours: {nliq}')

    log('[2] building 1-second bars (last snapshot per second) ...')
    df = con.execute(f"""
    with base as (
      select o.market_ticker,
             (o.received_at_ns / 1000000000)::bigint as sec,
             o.received_at_ns,
             o.btc_spot as spot,
             (o.yes_bid + o.yes_ask)/2.0 as ymid
      from ws_orderbook_top_dedup o
      join liq using(market_ticker)
      where o.yes_bid is not null and o.yes_ask is not null
        and o.yes_ask > 0 and o.yes_ask < 1
    )
    select market_ticker, sec,
           arg_max(spot, received_at_ns) as spot,
           arg_max(ymid, received_at_ns) as ymid
    from base group by 1, 2 order by 1, 2
    """).df()
    log(f'    1s bars: {len(df):,} across {df.market_ticker.nunique()} markets')

    # ---- per-market aligned diffs (reindex to contiguous seconds, ffill) ----
    MAXLAG = 30
    lags = range(-3, MAXLAG + 1)
    # accumulate paired samples per lag for pooled correlation
    sx = {k: [] for k in lags}   # spot returns
    sy = {k: [] for k in lags}   # mid changes at t+k
    # event study accumulators
    horizons = [1, 2, 3, 5, 10, 15, 30, 60]
    ev_curves = []   # each: dict h-> normalized mid move
    n_events = 0

    for mt, g in df.groupby('market_ticker', sort=False):
        g = g.set_index('sec')
        full = np.arange(g.index.min(), g.index.max() + 1)
        spot = g['spot'].reindex(full).ffill().to_numpy()
        ymid = g['ymid'].reindex(full).ffill().to_numpy()
        if len(spot) < 120:
            continue
        dspot = np.diff(spot, prepend=spot[0])     # $ change per second
        dmid = np.diff(ymid, prepend=ymid[0])      # prob change per second
        # pooled CCF samples
        for k in lags:
            if k >= 0:
                a = dspot[:len(dspot) - k] if k else dspot
                b = dmid[k:]
            else:
                a = dspot[-k:]
                b = dmid[:len(dmid) + k]
            n = min(len(a), len(b))
            if n > 0:
                sx[k].append(a[:n]); sy[k].append(b[:n])
        # event study: spot jumps
        ev_idx = np.where(np.abs(dspot) >= JUMP)[0]
        for i in ev_idx:
            if i + 60 >= len(ymid):
                continue
            sign = np.sign(dspot[i])
            base_mid = ymid[i - 1] if i > 0 else ymid[i]
            eventual = (ymid[i + 60] - base_mid) * sign   # signed eventual response
            if abs(eventual) < 0.01:      # need a real response to normalize against
                continue
            curve = {h: ((ymid[i + h] - base_mid) * sign) / eventual for h in horizons}
            curve['_eventual_abs'] = abs(eventual)                 # total move, prob units
            curve['_residual_after2s'] = abs(ymid[i + 60] - ymid[i + 2])  # capturable by a t+2s reactor
            ev_curves.append(curve); n_events += 1

    # ---- report CCF ----
    log('\n' + '=' * 60)
    log('CROSS-CORRELATION: 1s spot return (t) vs Kalshi yes-mid change (t+lag)')
    log('=' * 60)
    log(f'{"lag(s)":>7} {"corr":>8}')
    best = (None, -1)
    for k in lags:
        x = np.concatenate(sx[k]); y = np.concatenate(sy[k])
        if x.std() == 0 or y.std() == 0:
            c = float('nan')
        else:
            c = float(np.corrcoef(x, y)[0, 1])
        if not np.isnan(c) and c > best[1]:
            best = (k, c)
        bar = '#' * int(max(0, c) * 60)
        log(f'{k:>7} {c:>8.3f}  {bar}')
    log(f'\n>> peak correlation at lag = {best[0]}s (corr {best[1]:.3f})')
    log('   lag 0 dominant => Kalshi tracks spot within the same second (no taker window)')
    log('   peak at lag>=1 => Kalshi trails spot by that many seconds')

    # ---- report event study ----
    log('\n' + '=' * 60)
    log(f'EVENT STUDY: spot jumps >= ${JUMP:.0f} in 1s  (n={n_events})')
    log('Fraction of the eventual (60s) Kalshi mid response realized by horizon h:')
    log('=' * 60)
    if n_events:
        import statistics
        for h in horizons:
            vals = [c[h] for c in ev_curves]
            med = statistics.median(vals)
            mean = statistics.fmean(vals)
            log(f'  t+{h:>2}s : median {med:6.1%}   mean {mean:6.1%}')
        log('\n  If most of the response is realized by t+1s, the taker window is ~gone.')
        log('  If it builds over 10-30s, that is the exploitable lag (maker-side safest).')

        # magnitude vs fee (the make-or-break)
        ev_abs = sorted(c['_eventual_abs'] for c in ev_curves)
        res2 = sorted(c['_residual_after2s'] for c in ev_curves)
        med_ev = ev_abs[len(ev_abs)//2]; med_res = res2[len(res2)//2]
        mean_res = statistics.fmean(res2)
        log('\n  MAGNITUDE (cents per contract):')
        log(f'    total eventual mid move per jump:  median {med_ev*100:5.2f}c')
        log(f'    capturable by a t+2s reactor:      median {med_res*100:5.2f}c   mean {mean_res*100:5.2f}c')
        log(f'    taker fee at p=0.5: 1.75c  |  maker fee: ~0c')
        log(f'    >> taker net (median): {med_res*100 - 1.75:+.2f}c   maker net (median): {med_res*100:+.2f}c')
    else:
        log('  no qualifying events')
    log('=' * 60)


if __name__ == '__main__':
    main()
