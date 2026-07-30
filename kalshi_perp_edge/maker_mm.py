"""
Kalshi Perp zero-fee MARKET-MAKER — paper simulator + deployable skeleton.

Edge (see KALSHI_PERP_EDGE.md): perp is efficient vs spot (no lead-lag), fees are 0, and
taker flow is uninformed/mean-reverting. So a passive maker harvests the ~0.5bp spread,
and earns MOST by fading transient spot moves (fills against the move: +0.71bp/fill).

Design:
  fair  = spot (perp tracks spot 1:1; implied = spot/MULT in perp price units)
  quote a bid below fair and ask above fair at a half-width >= min edge; SKEW toward the
  FADE side of the recent spot move (spot just dropped -> lean the bid, buy the dip).
  Inventory-aware: shade quotes against current inventory; stop adding past a cap.
  Kill-switch: if spot drifts > DRIFT_BP over DRIFT_S (sustained trend = informed risk),
  pull quotes and flatten.

Paper fills (APPROXIMATE, front-of-queue => optimistic fill rate, honest upper bound):
  poll new public trades; a taker SELL (taker_side='bid') at px<=my_bid fills my bid;
  a taker BUY (taker_side='ask') at px>=my_ask fills my ask. PnL marked to perp mid.

NOT wired to real orders. This measures whether the quoting logic is inventory-stable and
roughly +EV before committing to a live queue-aware build.
"""
import sys, json, time, urllib.request, argparse
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'kalshi_k6'))
import config

MULT = {'BTC': 1e4, 'ETH': 1e3}
PERP = {'BTC': 'KXBTCPERP', 'ETH': 'KXETHPERP'}
SPOT = {'BTC': 'BTC-USD', 'ETH': 'ETH-USD'}
TICK = 1e-4


def cb_spot(prod):
    u = f'https://api.exchange.coinbase.com/products/{prod}/ticker'
    d = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={'User-Agent': 'r/1.0'}), timeout=4).read())
    return float(d['price'])


def new_trades(c, tk, last_id):
    """public trades newer than last_id (first seen)."""
    d = c._get('/margin/trades', {'ticker': tk, 'limit': 100})
    tr = d.get('trades', [])
    out = []
    for t in tr:
        if t['trade_id'] == last_id:
            break
        out.append(t)
    return out, (tr[0]['trade_id'] if tr else last_id)


def run(coin, a):
    mult = MULT[coin]; tk = PERP[coin]; prod = SPOT[coin]
    c = config.KalshiClient(); c.base = 'https://external-api.kalshi.com/trade-api/v2'
    log = Path(__file__).resolve().parent / 'data' / \
        f'mm_{coin}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl'
    f = open(log, 'a')
    print(f'[{coin}] paper MM half={a.half_bp}bp skew={a.skew} invcap={a.inv_cap} -> {log.name}',
          flush=True)

    inv = 0.0; cash = 0.0; fills = 0; realized_marks = []
    last_id = None
    spot_hist = deque(maxlen=600)        # (t, spot)
    i = 0
    while True:
        t0 = time.time()
        try:
            m = c._get(f'/margin/markets/{tk}')['market']
            bid, ask = float(m['bid']), float(m['ask'])
            spot = cb_spot(prod)
        except Exception as ex:
            print(' feed err', str(ex)[:60], flush=True); time.sleep(1); continue
        mid = (bid + ask) / 2
        now = time.time()
        spot_hist.append((now, spot))
        fair = spot / mult                # fair perp price

        # recent spot move (bp) over ~3s for fade skew + kill-switch drift over DRIFT_S
        def move_bp(win):
            ref = next((s for (tt, s) in spot_hist if tt >= now - win), spot_hist[0][1])
            return (spot / ref - 1) * 1e4
        mv3 = move_bp(3)
        drift = move_bp(a.drift_s)

        half = a.half_bp * 1e-4 * fair
        # fade skew: spot dropped (mv3<0) -> push our quotes DOWN a touch (lean bid to buy dip)
        skew = -a.skew * (mv3 * 1e-4) * fair
        # inventory skew: long inventory -> lower quotes to sell; short -> raise to buy
        inv_skew = -a.inv_kp * (inv / max(a.inv_cap, 1)) * half
        # PASSIVE: rest at-or-behind the touch, never cross the book (we are a maker)
        my_bid = min(fair - half + skew + inv_skew, bid)
        my_ask = max(fair + half + skew + inv_skew, ask)
        killed = abs(drift) >= a.drift_bp

        # paper fills from public trades (front-of-queue => optimistic). All units $/contract.
        trades = []
        try:
            if last_id is None:                 # first loop: seed id, no fills
                d0 = c._get('/margin/trades', {'ticker': tk, 'limit': 1}).get('trades', [])
                last_id = d0[0]['trade_id'] if d0 else 'seed'
            else:
                trades, last_id = new_trades(c, tk, last_id)
        except Exception as ex:
            print(' trades err', str(ex)[:50], flush=True)
        for t in trades:
            px = float(t['price']); sz = float(t['count'])
            taker = t.get('taker_side')
            if (not killed) and inv < a.inv_cap and taker == 'bid' and px <= my_bid + 1e-9:
                fz = min(sz, a.inv_cap - inv)   # cap enforced per fill
                inv += fz; cash -= my_bid * fz; fills += 1
            elif (not killed) and inv > -a.inv_cap and taker == 'ask' and px >= my_ask - 1e-9:
                fz = min(sz, a.inv_cap + inv)
                inv -= fz; cash += my_ask * fz; fills += 1

        equity = cash + inv * mid               # $/contract units (perp price = $/contract)
        i += 1
        if i % 30 == 0:
            rec = dict(t=now, inv=round(inv, 1), equity=round(equity, 2), fills=fills,
                       mv3=round(mv3, 2), spot=round(spot, 1), killed=killed)
            f.write(json.dumps(rec) + '\n'); f.flush()
            print(f'  inv {inv:+.0f} fills {fills} equity ${equity:+.2f} '
                  f'mv3 {mv3:+.1f}bp{" KILL" if killed else ""}', flush=True)
        time.sleep(max(0, a.poll - (now - t0)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('coin', nargs='?', default='BTC', choices=['BTC', 'ETH'])
    p.add_argument('--half_bp', type=float, default=0.3, help='quote half-width (bp)')
    p.add_argument('--skew', type=float, default=0.5, help='fade-skew gain on 3s spot move')
    p.add_argument('--inv_cap', type=float, default=50, help='max |inventory| (contracts)')
    p.add_argument('--inv_kp', type=float, default=1.0, help='inventory mean-revert gain')
    p.add_argument('--drift_bp', type=float, default=15, help='kill-switch drift threshold')
    p.add_argument('--drift_s', type=float, default=20, help='kill-switch drift window (s)')
    p.add_argument('--poll', type=float, default=1.0)
    a = p.parse_args()
    run(a.coin, a)


if __name__ == '__main__':
    main()
