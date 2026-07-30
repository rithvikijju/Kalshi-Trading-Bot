"""
QUEUE-AWARE paper market-maker on the REAL prod tape (no account/onboarding needed).

The honest "does it work" test. Unlike maker_mm.py (front-of-queue = optimistic), this
models your position in the FIFO queue: when you join a price level you sit BEHIND the
size already resting there (read from the live book); you only fill after enough volume
trades through to clear the queue ahead of you. That haircut is the difference between a
paper fantasy and a realistic fill rate.

Loop (~1Hz, all read-only prod endpoints):
  - top-of-book + book depth (/margin/markets/{tk}, /margin/markets/{tk}/orderbook)
  - new public trades (/margin/trades) with price + taker_side + count
  - Coinbase spot (fair value)
Quoting: passive GTC at/behind the touch, fade-skew on 3s spot move, inventory cap +
mean-revert skew, drift kill-switch. Fills are queue-gated. PnL in $/contract, fee=0.
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


def cb_spot(prod):
    u = f'https://api.exchange.coinbase.com/products/{prod}/ticker'
    d = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={'User-Agent': 'r/1.0'}), timeout=4).read())
    return float(d['price'])


class Quote:
    """One resting order: price, my remaining size, and size queued AHEAD of me."""
    __slots__ = ('px', 'size', 'ahead')

    def __init__(self, px, size, ahead):
        self.px, self.size, self.ahead = px, size, ahead


def depth_at(levels, px, side):
    """summed resting size at prices >= px (bids) or <= px (asks) i.e. ahead of a join at px."""
    tot = 0.0
    for lv in levels:
        p, s = float(lv[0]), float(lv[1])
        if (side == 'bid' and p >= px - 1e-9) or (side == 'ask' and p <= px + 1e-9):
            tot += s
    return tot


def run(coin, a):
    mult = MULT[coin]; tk = PERP[coin]; prod = SPOT[coin]
    c = config.KalshiClient(); c.base = 'https://external-api.kalshi.com/trade-api/v2'
    log = Path(__file__).resolve().parent / 'data' / \
        f'mmq_{coin}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl'
    f = open(log, 'a')
    print(f'[{coin}] QUEUE-AWARE paper MM half={a.half_bp}bp size={a.size} cap={a.inv_cap} '
          f'-> {log.name}', flush=True)

    inv = 0.0; cash = 0.0; fills = 0; gross_quoted = 0
    bidq = askq = None                    # resting Quote or None
    last_id = None
    spot_hist = deque(maxlen=600)
    i = 0
    while True:
        t0 = time.time()
        try:
            m = c._get(f'/margin/markets/{tk}')['market']
            mbid, mask = float(m['bid']), float(m['ask'])
            ob = c._get(f'/margin/markets/{tk}/orderbook').get('orderbook', {})
            bids = ob.get('bids') or []; asks = ob.get('asks') or []
            spot = cb_spot(prod)
        except Exception as ex:
            print(' feed', str(ex)[:50], flush=True); time.sleep(1); continue
        now = time.time(); mid = (mbid + mask) / 2
        spot_hist.append((now, spot))
        fair = spot / mult

        def move_bp(win):
            ref = next((s for (tt, s) in spot_hist if tt >= now - win), spot_hist[0][1])
            return (spot / ref - 1) * 1e4
        mv3, drift = move_bp(3), move_bp(a.drift_s)
        killed = abs(drift) >= a.drift_bp

        half = a.half_bp * 1e-4 * fair
        skew = -a.skew * (mv3 * 1e-4) * fair
        inv_skew = -a.inv_kp * (inv / max(a.inv_cap, 1)) * half
        want_bid = min(fair - half + skew + inv_skew, mbid)     # passive
        want_ask = max(fair + half + skew + inv_skew, mask)

        # (re)place quotes: if our target moved >= 1 tick or we're flat on a side, requote
        # (requote resets queue position to the depth currently ahead at the new level)
        def requote(side, want, cur, can):
            if not can:
                return None
            if cur is None or abs(cur.px - want) >= 1e-4:
                ahead = depth_at(bids if side == 'bid' else asks, want, side)
                return Quote(want, a.size, ahead)
            return cur
        bidq = requote('bid', want_bid, bidq, (not killed) and inv < a.inv_cap)
        askq = requote('ask', want_ask, askq, (not killed) and inv > -a.inv_cap)
        if killed:                      # kill-switch: pull both quotes
            bidq = askq = None

        # new trades, queue-gated fills
        trades = []
        try:
            if last_id is None:
                d0 = c._get('/margin/trades', {'ticker': tk, 'limit': 1}).get('trades', [])
                last_id = d0[0]['trade_id'] if d0 else 'seed'
            else:
                d = c._get('/margin/trades', {'ticker': tk, 'limit': 200}).get('trades', [])
                for t in d:
                    if t['trade_id'] == last_id:
                        break
                    trades.append(t)
                last_id = d[0]['trade_id'] if d else last_id
        except Exception as ex:
            print(' trades', str(ex)[:40], flush=True)

        for t in reversed(trades):            # oldest first
            px = float(t['price']); sz = float(t['count']); side = t.get('taker_side')
            if side == 'bid' and bidq and px <= bidq.px + 1e-9 and inv < a.inv_cap:
                eat = sz
                if bidq.ahead > 0:            # clear queue ahead first
                    d = min(bidq.ahead, eat); bidq.ahead -= d; eat -= d
                if eat > 0:                   # now we fill
                    fz = min(eat, bidq.size, a.inv_cap - inv)
                    if fz > 0:
                        inv += fz; cash -= bidq.px * fz; fills += 1; bidq.size -= fz
                        if bidq.size <= 1e-9:
                            bidq = None
            elif side == 'ask' and askq and px >= askq.px - 1e-9 and inv > -a.inv_cap:
                eat = sz
                if askq.ahead > 0:
                    d = min(askq.ahead, eat); askq.ahead -= d; eat -= d
                if eat > 0:
                    fz = min(eat, askq.size, a.inv_cap + inv)
                    if fz > 0:
                        inv -= fz; cash += askq.px * fz; fills += 1; askq.size -= fz
                        if askq.size <= 1e-9:
                            askq = None

        equity = cash + inv * mid
        i += 1
        if i % 30 == 0:
            rec = dict(t=round(now, 1), inv=round(inv, 1), equity=round(equity, 4),
                       fills=fills, mv3=round(mv3, 2), killed=killed,
                       per_fill_bp=round(equity / max(fills, 1) / mid * 1e4, 3))
            f.write(json.dumps(rec) + '\n'); f.flush()
            print(f'  inv {inv:+.0f} fills {fills} equity ${equity:+.4f} '
                  f'({rec["per_fill_bp"]:+.2f}bp/fill) mv3 {mv3:+.1f}{" KILL" if killed else ""}',
                  flush=True)
        time.sleep(max(0, a.poll - (now - t0)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('coin', nargs='?', default='BTC', choices=['BTC', 'ETH'])
    p.add_argument('--half_bp', type=float, default=0.3)
    p.add_argument('--skew', type=float, default=0.5)
    p.add_argument('--size', type=float, default=5, help='contracts per quote')
    p.add_argument('--inv_cap', type=float, default=50)
    p.add_argument('--inv_kp', type=float, default=1.0)
    p.add_argument('--drift_bp', type=float, default=15)
    p.add_argument('--drift_s', type=float, default=20)
    p.add_argument('--poll', type=float, default=1.0)
    a = p.parse_args()
    run(a.coin, a)


if __name__ == '__main__':
    main()
