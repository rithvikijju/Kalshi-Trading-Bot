"""
Kalshi Perp Lead-Lag Scalp  —  live signal + paper executor (deployable).

EDGE (validated on 13d of 1-min candles, BTC+ETH; see KALSHI_PERP_EDGE.md):
  Kalshi crypto perps are ZERO-FEE, tightly spread (~0.5bp BTC / ~1.7bp ETH), and
  lag Coinbase spot by <1 min (1-min lead-lag corr ~0.98). The weak/zero funding
  tether lets the perp-vs-spot basis run to +-8-11bp std. After a spot move the perp
  predictably catches up. A taker needs to capture only ~11-19% of that catch-up to
  clear the round-trip spread (=> ~50s break-even latency: very loose).

SIGNAL (live, no look-ahead — uses only Coinbase spot + current perp quote):
  basis_bp = perp_mid_implied/spot - 1  (in bp), perp_mid_implied = perp_mid * MULT
  basis <= -ENTER  -> perp cheap  -> BUY  perp (it will rise to catch spot)
  basis >= +ENTER  -> perp rich   -> SELL perp (it will fall)
  exit when |basis| <= EXIT (caught up) or after MAX_HOLD_S, on the opposite side.

EXECUTION: taker by default (cross the perp's tight spread). Guards: only trade when
  perp spread <= MAX_SPREAD_TICKS and data is fresh. Paper mode by default — set
  --live to arm real orders (NOT enabled here; place_order is a stub to fill in).

MAKER FALLBACK: if Kalshi turns on perp taker fees (currently 0) or spreads widen,
  flip POST_ONLY=True to quote passively and EARN the spread (skew toward the spot
  lead). Stub provided; needs queue-aware fills.
"""
import sys, time, json, argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import deque
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'kalshi_k6'))
import config

MULT = {'BTC': 1e4, 'ETH': 1e3}
TICK = 1e-4                       # perp price tick ($/contract)
PERP = {'BTC': 'KXBTCPERP', 'ETH': 'KXETHPERP'}
CB = {'BTC': 'BTC-USD', 'ETH': 'ETH-USD'}

DEFAULTS = dict(
    enter_bp=5.0,                # enter when |basis| >= this (bp)
    exit_bp=1.0,                 # exit when |basis| <= this
    max_hold_s=120,              # hard time stop
    max_spread_ticks=4,          # don't trade if perp spread wider than this
    size=1,                      # contracts per trade
    stale_ms=2500,               # treat data older than this as stale -> no trade
)


def cb_spot(prod):
    u = f'https://api.exchange.coinbase.com/products/{prod}/ticker'
    req = urllib.request.Request(u, headers={'User-Agent': 'res/1.0'})
    d = json.loads(urllib.request.urlopen(req, timeout=4).read())
    return float(d['price']), float(d.get('bid', d['price'])), float(d.get('ask', d['price']))


class PaperBook:
    """Tracks one open position + realized paper PnL in bp (zero fee)."""
    def __init__(self):
        self.pos = 0            # +size long / -size short / 0 flat
        self.entry_px = None    # perp $ price filled
        self.entry_spot = None
        self.entry_t = None
        self.realized_bp = 0.0
        self.ntrades = 0
        self.wins = 0

    def open(self, side, perp_px, spot, t):
        self.pos = side
        self.entry_px = perp_px
        self.entry_spot = spot
        self.entry_t = t

    def close(self, perp_px):
        # long: bought ask, sell bid now; short: sold bid, buy ask now. perp_px is the fill.
        gross = (perp_px - self.entry_px) * (1 if self.pos > 0 else -1)  # $/contract
        bp = gross * MULT_GLOBAL / self.entry_spot * 1e4                  # -> bp of underlying
        self.realized_bp += bp
        self.ntrades += 1
        self.wins += 1 if bp > 0 else 0
        self.pos = 0
        return bp


def run(coin, args):
    global MULT_GLOBAL
    MULT_GLOBAL = MULT[coin]
    c = config.KalshiClient()
    c.base = 'https://external-api.kalshi.com/trade-api/v2'
    tk, prod = PERP[coin], CB[coin]
    book = PaperBook()
    log = Path(__file__).resolve().parent / 'data' / \
        f'paper_{coin}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl'
    print(f'[{coin}] paper lead-lag scalp  enter={args.enter_bp}bp exit={args.exit_bp}bp '
          f'hold<={args.max_hold_s}s  -> {log.name}', flush=True)
    f = open(log, 'a')
    i = 0
    while True:
        t0 = time.time()
        try:
            m = c._get(f'/margin/markets/{tk}')['market']
            bid, ask = float(m['bid']), float(m['ask'])
            spot, _, _ = cb_spot(prod)
        except Exception as ex:
            print('  feed err', str(ex)[:70], flush=True)
            time.sleep(1.0); continue
        mid = (bid + ask) / 2
        spread_ticks = round((ask - bid) / TICK)
        perp_impl = mid * MULT_GLOBAL
        basis_bp = (perp_impl / spot - 1) * 1e4
        now = time.time()

        # ---- manage open position ----
        if book.pos != 0:
            held = now - book.entry_t
            caught_up = abs(basis_bp) <= args.exit_bp
            timed_out = held >= args.max_hold_s
            # also exit if basis flipped past zero (overshoot)
            flipped = (book.pos > 0 and basis_bp >= args.exit_bp) or \
                      (book.pos < 0 and basis_bp <= -args.exit_bp)
            if caught_up or timed_out or flipped:
                fill = bid if book.pos > 0 else ask          # taker exit, opposite side
                bp = book.close(fill)
                rec = dict(t=now, ev='exit', basis_bp=round(basis_bp, 2), bp=round(bp, 2),
                           held=round(held, 1), cum_bp=round(book.realized_bp, 1),
                           n=book.ntrades, win=book.wins)
                f.write(json.dumps(rec) + '\n'); f.flush()
                print(f'  EXIT {bp:+.2f}bp held {held:.0f}s | cum {book.realized_bp:+.1f}bp '
                      f'{book.wins}/{book.ntrades}', flush=True)
        # ---- look for entry (flat only) ----
        elif spread_ticks <= args.max_spread_ticks and abs(basis_bp) >= args.enter_bp:
            if basis_bp <= -args.enter_bp:
                book.open(+args.size, ask, spot, now)        # buy perp at ask
                side = 'BUY'
            else:
                book.open(-args.size, bid, spot, now)        # sell perp at bid
                side = 'SELL'
            print(f'  {side} @ {ask if side=="BUY" else bid:.4f} basis {basis_bp:+.2f}bp '
                  f'spot {spot:.1f} spr {spread_ticks}t', flush=True)

        i += 1
        if i % 300 == 0:
            print(f'  .. {i} polls, basis {basis_bp:+.2f}bp, cum {book.realized_bp:+.1f}bp '
                  f'over {book.ntrades} trades', flush=True)
        time.sleep(max(0, args.poll - (now - t0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('coin', nargs='?', default='BTC', choices=['BTC', 'ETH'])
    ap.add_argument('--enter_bp', type=float, default=DEFAULTS['enter_bp'])
    ap.add_argument('--exit_bp', type=float, default=DEFAULTS['exit_bp'])
    ap.add_argument('--max_hold_s', type=float, default=DEFAULTS['max_hold_s'])
    ap.add_argument('--max_spread_ticks', type=int, default=DEFAULTS['max_spread_ticks'])
    ap.add_argument('--size', type=int, default=DEFAULTS['size'])
    ap.add_argument('--poll', type=float, default=0.5, help='seconds between polls')
    ap.add_argument('--live', action='store_true', help='(stub) arm real orders')
    args = ap.parse_args()
    if args.live:
        print('LIVE order placement is a stub — refusing to send real orders.'); return
    run(args.coin, args)


if __name__ == '__main__':
    main()
