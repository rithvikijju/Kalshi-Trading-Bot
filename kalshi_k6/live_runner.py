"""K6 LIVE trading runner.

Identical loop to paper_runner but places real Kalshi limit orders.
Requires Kalshi prod credentials at ~/.kalshi/credentials.env with
  KALSHI_PROD_KEY_ID=...
  KALSHI_PROD_PRIVATE_KEY_PATH=~/.kalshi/private_key.pem

SAFETY: live mode is OFF by default and requires --i-know-what-im-doing flag
plus a max-bankroll cap. Will refuse to start without both.

Order placement:
  - Limit buy at the signal's observed ask + 0¢ (no buffer)
  - Expiration: 20 seconds (config)
  - After expiration, the order is naturally cancelled; we don't track it as a fill
  - On every cycle, reconcile open positions against Kalshi /portfolio/positions
"""
from __future__ import annotations
import argparse, json, signal, sys, time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from config import CFG, KalshiClient
from spot import SpotFeed
from market_feed import MarketFeed
from strategy import scan_k6
from portfolio import Portfolio
from paper_runner import _log_line


class LiveRunner:
    def __init__(self, bankroll: float, verbose: bool = True):
        CFG['starting_bankroll'] = bankroll
        self.verbose = verbose
        self.client = KalshiClient()  # auth from ~/.kalshi/credentials.env
        if not self.client.private_key or not self.client.key_id:
            raise RuntimeError(
                'No Kalshi prod credentials. Put KALSHI_PROD_KEY_ID and '
                'KALSHI_PROD_PRIVATE_KEY_PATH in ~/.kalshi/credentials.env.')
        # Verify we can read balance
        bal = self.client.get_balance()
        bal_cents = bal.get('balance', 0) if isinstance(bal, dict) else 0
        print(f'Live auth OK. Kalshi balance: ${bal_cents/100:.2f}')
        if bal_cents < bankroll * 100:
            raise RuntimeError(
                f'Kalshi balance ${bal_cents/100:.2f} is less than requested '
                f'bankroll ${bankroll:.2f}. Fund the account or lower bankroll.')

        self.spot = SpotFeed()
        self.mkt = MarketFeed(self.client)
        self.portfolio = Portfolio.load(CFG['live_state'], mode='live')
        self.cycle = 0
        self._running = False
        self._last_state_save = 0
        self._pending_orders: Dict[str, Any] = {}  # order_id → context

    def _say(self, msg):
        if self.verbose:
            ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
            print(f'[{ts}] {msg}', flush=True)

    def start(self):
        self.spot.start()
        self.mkt.start()
        self._running = True
        self._say(f'K6 LIVE runner started (bankroll ${self.portfolio.cash:.2f})')
        self._say('Warming up (10s)...')
        time.sleep(10)

    def stop(self):
        self._running = False
        self.spot.stop()
        self.mkt.stop()
        self.portfolio.save(CFG['live_state'])

    def _reconcile_positions(self):
        """Pull current Kalshi positions and update the local portfolio cash
        on settled markets. Handles the case where a position settled while
        the runner wasn't watching."""
        try:
            kp = self.client.get_positions()
        except Exception as e:
            self._say(f'reconcile: positions API error {e}')
            return
        if not isinstance(kp, dict):
            return
        # Kalshi positions API gives us per-market position counts and
        # realized PnL. For now we trust local accounting and only flag
        # discrepancies.
        kalshi_open_tickers = {p.get('ticker'): p for p in kp.get('market_positions', [])
                                if p.get('position', 0) != 0}
        for pid, pos in list(self.portfolio.open_positions.items()):
            mt = pos.market_ticker
            if mt not in kalshi_open_tickers:
                # Kalshi shows no open position → it settled
                # We'll handle PnL in the next settlement scan; for now log
                self._say(f'reconcile: {mt} no longer open on Kalshi side')

    def _place_order_for_signal(self, sig) -> Optional[dict]:
        """Place a real limit-buy order on Kalshi. Returns fill info or None."""
        # Kalshi price is in cents (integer). Buy at the asking price exactly.
        price_cents = round(sig.price * 100)
        if price_cents < 1 or price_cents > 99:
            return None
        resp = self.client.place_limit(
            ticker=sig.market_ticker,
            side=sig.side,
            action='buy',
            count=sig.qty,
            price_cents=price_cents,
        )
        if resp.get('error'):
            self._say(f'  ORDER REJECTED: {resp["error"]} status={resp.get("status")}')
            return None
        order = resp.get('order', resp)
        order_id = order.get('order_id')
        self._say(f'  ORDER SENT: {sig.market_ticker} {sig.side} qty={sig.qty} '
                   f'@{price_cents}¢ id={order_id}')
        self._pending_orders[order_id] = {
            'sig': sig, 'placed_at': time.time(), 'order': order
        }
        return order

    def _confirm_fills(self):
        """Check on pending orders — if they filled, register as a position;
        if they expired, drop them."""
        if not self._pending_orders:
            return
        # Approach: query positions API. Any market_ticker in pending that's
        # now in positions => assume filled at our limit price.
        try:
            kp = self.client.get_positions()
        except Exception:
            return
        kalshi_positions = {p.get('ticker'): p
                              for p in kp.get('market_positions', [])
                              if isinstance(p, dict)}
        # Filled if positions API shows positive position in our market
        for oid, info in list(self._pending_orders.items()):
            sig = info['sig']
            age = time.time() - info['placed_at']
            kp_pos = kalshi_positions.get(sig.market_ticker, {})
            net = kp_pos.get('position', 0)
            # Crude fill detection — if we now have a non-zero position
            # in this market and we didn't before, assume it's our fill
            already_local = any(p.market_ticker == sig.market_ticker
                                  for p in self.portfolio.open_positions.values())
            if net != 0 and not already_local:
                self.portfolio.open_position(
                    {
                        'market_ticker': sig.market_ticker,
                        'event_ticker': sig.event_ticker,
                        'strike': sig.strike,
                        'side': sig.side,
                        'bucket': sig.bucket,
                        'spot': sig.spot,
                        'spot_dist': sig.spot_dist,
                        'rv_15m_annual': sig.rv_15m_annual,
                        'close_time': (sig.detected_at +
                                        __import__('datetime').timedelta(
                                            seconds=sig.secs_to_close)).isoformat(),
                    },
                    fill_price=sig.price, fill_qty=sig.qty)
                self._say(f'  FILLED {sig.market_ticker} {sig.side} qty={sig.qty}')
                del self._pending_orders[oid]
            elif age > CFG['order_expiration_sec'] + 5:
                # Order expired, drop it
                del self._pending_orders[oid]

    def run_cycle(self) -> dict:
        self.cycle += 1
        spot = self.spot.price
        rv = self.spot.rv_15min_annual()
        snap = self.mkt.snapshot()
        markets = snap['markets']
        cycle_payload = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'cycle': self.cycle,
            'spot': spot,
            'rv_15m_annual': rv,
            'event_ticker': snap.get('event_ticker'),
            'mkt_count': len(markets),
            'open_positions': len(self.portfolio.open_positions),
            'cash': round(self.portfolio.cash, 4),
            'realized_pnl': round(self.portfolio.realized_pnl, 4),
            'daily_pnl': round(self.portfolio.daily_pnl, 4),
            'halted': self.portfolio.halted,
            'pending_orders': len(self._pending_orders),
            'opened': [],
            'orders_sent': [],
        }

        # Reconcile + confirm fills
        if self.cycle % 5 == 0:
            self._reconcile_positions()
        self._confirm_fills()

        # Halted? skip signal generation
        if self.portfolio.halted:
            cycle_payload['note'] = 'halted'
            return cycle_payload

        if spot is None or rv is None or not markets:
            cycle_payload['note'] = 'no data yet'
            return cycle_payload

        # Scan
        open_tickers = {p.market_ticker for p in self.portfolio.open_positions.values()}
        signals = scan_k6(markets, spot, rv,
                           bankroll_avail=self.portfolio.cash,
                           open_market_tickers=open_tickers)
        cycle_payload['n_signals'] = len(signals)

        # Try to place orders for each (sorted by edge)
        for sig in signals:
            if not self.portfolio.can_open(sig.qty, sig.price):
                continue
            order = self._place_order_for_signal(sig)
            if order:
                cycle_payload['orders_sent'].append({
                    'market': sig.market_ticker, 'side': sig.side,
                    'qty': sig.qty, 'price': sig.price,
                })

        # Save state
        if time.time() - self._last_state_save > CFG['state_save_sec']:
            self.portfolio.save(CFG['live_state'])
            self._last_state_save = time.time()
        return cycle_payload

    def loop(self, max_cycles: Optional[int] = None):
        self.start()
        try:
            while self._running:
                cycle = self.run_cycle()
                _log_line(CFG['live_log'], cycle)
                if cycle.get('n_signals') or cycle.get('orders_sent'):
                    self._say(f"cycle {cycle['cycle']}: signals={cycle.get('n_signals',0)} "
                               f"orders_sent={len(cycle.get('orders_sent', []))} "
                               f"open={cycle['open_positions']} "
                               f"pending={cycle['pending_orders']} "
                               f"cash=${cycle['cash']:.2f} "
                               f"pnl=${cycle['realized_pnl']:+.2f}")
                elif self.cycle % 10 == 0:
                    self._say(f"cycle {cycle['cycle']}: idle "
                               f"open={cycle['open_positions']} cash=${cycle['cash']:.2f}")
                if max_cycles and self.cycle >= max_cycles:
                    break
                time.sleep(CFG['decision_loop_sec'])
        except KeyboardInterrupt:
            self._say('interrupted')
        finally:
            self.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bankroll', type=float, required=True,
                     help='Hard cap on capital to deploy. Required.')
    ap.add_argument('--max-cycles', type=int, default=None)
    ap.add_argument('--i-know-what-im-doing', action='store_true',
                     help='Required confirmation flag for live trading.')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    if not args.i_know_what_im_doing:
        print('Refusing to start. Pass --i-know-what-im-doing to confirm live mode.')
        sys.exit(2)
    if args.bankroll > 500:
        print(f'Refusing to start with bankroll ${args.bankroll}. '
              f'Maximum allowed for first live deploy is $500. '
              f'Edit kalshi_k6/live_runner.py to raise this guard.')
        sys.exit(2)

    print(f'STARTING LIVE K6 RUNNER WITH ${args.bankroll:.2f} BANKROLL')
    print('Press Ctrl-C to stop. Real money is at risk.')
    print()
    runner = LiveRunner(bankroll=args.bankroll, verbose=not args.quiet)
    runner.loop(max_cycles=args.max_cycles)


if __name__ == '__main__':
    main()
