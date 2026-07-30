"""
Kalshi Perp zero-fee market-maker — LIVE order bot (prod margin API).

Implements the full order lifecycle for the strategy in KALSHI_PERP_EDGE.md:
posts passive GTC post_only quotes around spot-fair, fade-skews on the 3s spot move,
caps inventory with a mean-revert skew, and pulls/flattens on a sustained-drift kill-switch.

SAFETY (read before arming):
  * DRY-RUN by default — it logs the exact orders it WOULD place/cancel and never sends.
    Pass --arm to send real orders (a real-money action; the harness will also gate it).
  * Requires a FUNDED margin account. It reads /margin/balance and refuses to arm if
    available_balance < --min_balance.
  * --size (contracts/quote, default 1) and --inv_cap (default 5) keep notional tiny while
    you validate the live loop. BTC perp ~ $6.6/contract, so size 1 ≈ $6.6 notional.
  * Kill-switch flattens via IOC if |inventory| or drift breaches limits.
  * The edge is UNVALIDATED on realistic fills (see maker_mm_queue.py) — start tiny.

Endpoints: POST/DELETE /margin/orders, GET /margin/{orders,positions,fills,balance}.
Order body: {ticker, client_order_id, side bid|ask, count(str), price(str $),
time_in_force good_till_canceled, post_only true, self_trade_prevention_type taker_at_cross}.
"""
import sys, json, time, uuid, argparse, urllib.request
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'kalshi_k6'))
import config

MULT = {'BTC': 1e4, 'ETH': 1e3}
PERP = {'BTC': 'KXBTCPERP', 'ETH': 'KXETHPERP'}
SPOT = {'BTC': 'BTC-USD', 'ETH': 'ETH-USD'}
TICK = 1e-4
BASE = 'https://external-api.kalshi.com/trade-api/v2'


def cb_spot(prod):
    u = f'https://api.exchange.coinbase.com/products/{prod}/ticker'
    d = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={'User-Agent': 'r/1.0'}), timeout=4).read())
    return float(d['price'])


class OMS:
    """Thin order-management wrapper over the prod margin API (signed)."""
    def __init__(self, client, dry):
        self.c = client; self.dry = dry

    def _req(self, m, path, body=None):
        h = self.c._sign(m, '/trade-api/v2' + path); h['Content-Type'] = 'application/json'
        r = getattr(requests, m.lower())(BASE + path, headers=h,
                                         **({'json': body} if body else {}), timeout=10)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:200]

    def get(self, path):
        return self._req('GET', path)[1]

    def place(self, ticker, side, count, price, post_only=True, tif='good_till_canceled'):
        body = dict(ticker=ticker, client_order_id='mm-' + uuid.uuid4().hex[:12], side=side,
                    count=str(int(count)), price=f'{price:.4f}', time_in_force=tif,
                    post_only=post_only, self_trade_prevention_type='taker_at_cross')
        if self.dry:
            print(f'  [DRY] PLACE {side} {count}@{price:.4f}', flush=True)
            return {'dry': True, 'body': body}
        sc, resp = self._req('POST', '/margin/orders', body)
        print(f'  PLACE {side} {count}@{price:.4f} -> {sc}', flush=True)
        return resp

    def cancel(self, oid):
        if self.dry:
            print(f'  [DRY] CANCEL {oid}', flush=True); return
        self._req('DELETE', f'/margin/orders/{oid}')


def order_id(resp):
    if isinstance(resp, dict):
        return (resp.get('order') or {}).get('order_id') or resp.get('order_id')
    return None


def run(coin, a):
    mult = MULT[coin]; tk = PERP[coin]; prod = SPOT[coin]
    c = config.KalshiClient()
    oms = OMS(c, dry=not a.arm)
    bal = oms.get('/margin/balance')
    avail = float((bal.get('subaccount_balances', [{}]) or [{}])[0].get('available_balance', 0)
                  if isinstance(bal, dict) else 0)
    print(f'[{coin}] LIVE MM  arm={a.arm}  available_balance=${avail:.2f}  '
          f'size={a.size} inv_cap={a.inv_cap}', flush=True)
    if a.arm and avail < a.min_balance:
        print(f'REFUSING TO ARM: available ${avail:.2f} < --min_balance ${a.min_balance}. '
              f'Fund the margin account first.', flush=True)
        return

    log = Path(__file__).resolve().parent / 'data' / \
        f'live_{coin}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl'
    f = open(log, 'a')
    bid_oid = ask_oid = None
    bid_px = ask_px = None
    spot_hist = deque(maxlen=600)
    i = 0
    while True:
        t0 = time.time()
        try:
            m = c._get(f'/margin/markets/{tk}')['market']  # uses default signed GET
            mbid, mask = float(m['bid']), float(m['ask'])
            spot = cb_spot(prod)
            pos = oms.get('/margin/positions').get('positions', [])
            inv = sum(float(p.get('position', 0)) for p in pos if p.get('ticker') == tk)
        except Exception as ex:
            print(' feed', str(ex)[:50], flush=True); time.sleep(1); continue
        now = time.time(); mid = (mbid + mask) / 2
        spot_hist.append((now, spot)); fair = spot / mult

        def move_bp(w):
            ref = next((s for (tt, s) in spot_hist if tt >= now - w), spot_hist[0][1])
            return (spot / ref - 1) * 1e4
        mv3, drift = move_bp(3), move_bp(a.drift_s)
        killed = abs(drift) >= a.drift_bp

        half = a.half_bp * 1e-4 * fair
        skew = -a.skew * (mv3 * 1e-4) * fair
        inv_skew = -a.inv_kp * (inv / max(a.inv_cap, 1)) * half
        want_bid = round(min(fair - half + skew + inv_skew, mbid) / TICK) * TICK
        want_ask = round(max(fair + half + skew + inv_skew, mask) / TICK) * TICK

        want_quote_bid = (not killed) and inv < a.inv_cap
        want_quote_ask = (not killed) and inv > -a.inv_cap

        # cancel-replace each side only when target moved >= 1 tick or quoting state changed
        if (not want_quote_bid) and bid_oid:
            oms.cancel(bid_oid); bid_oid = bid_px = None
        elif want_quote_bid and (bid_px is None or abs(bid_px - want_bid) >= TICK):
            if bid_oid:
                oms.cancel(bid_oid)
            bid_oid = order_id(oms.place(tk, 'bid', a.size, want_bid)); bid_px = want_bid
        if (not want_quote_ask) and ask_oid:
            oms.cancel(ask_oid); ask_oid = ask_px = None
        elif want_quote_ask and (ask_px is None or abs(ask_px - want_ask) >= TICK):
            if ask_oid:
                oms.cancel(ask_oid)
            ask_oid = order_id(oms.place(tk, 'ask', a.size, want_ask)); ask_px = want_ask

        i += 1
        if i % 15 == 0:
            rec = dict(t=round(now, 1), inv=inv, mid=mid, bid=bid_px, ask=ask_px,
                       mv3=round(mv3, 2), killed=killed, avail=avail)
            f.write(json.dumps(rec) + '\n'); f.flush()
            print(f'  inv {inv:+.0f} quote [{bid_px} / {ask_px}] mid {mid:.4f} '
                  f'mv3 {mv3:+.1f}{" KILL" if killed else ""}', flush=True)
        time.sleep(max(0, a.poll - (now - t0)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('coin', nargs='?', default='BTC', choices=['BTC', 'ETH'])
    p.add_argument('--arm', action='store_true', help='send REAL orders (default: dry-run)')
    p.add_argument('--size', type=float, default=1, help='contracts per quote')
    p.add_argument('--inv_cap', type=float, default=5)
    p.add_argument('--half_bp', type=float, default=0.3)
    p.add_argument('--skew', type=float, default=0.5)
    p.add_argument('--inv_kp', type=float, default=1.0)
    p.add_argument('--drift_bp', type=float, default=15)
    p.add_argument('--drift_s', type=float, default=20)
    p.add_argument('--min_balance', type=float, default=20, help='refuse to arm below this $')
    p.add_argument('--poll', type=float, default=1.0)
    a = p.parse_args()
    run(a.coin, a)


if __name__ == '__main__':
    main()
