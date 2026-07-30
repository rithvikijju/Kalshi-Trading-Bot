"""T1 monotonicity arbitrage — paper runner.

Loop:
  1. Spot feed → spot_move_30s for calm-regime filter
  2. Market feed → 188-strike orderbook snapshot
  3. scan_t1() returns T1Signal candidates (sorted by edge × qty)
  4. Open each fillable pair, save state
  5. Settle pairs whose event has closed (BTC at close_time minute)

Usage:
  cd kalshi_t1
  python paper_runner.py --bankroll 100
  python paper_runner.py --reset
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import CFG, KalshiClient, _coinbase_spot
from spot import SpotFeed
from market_feed import MarketFeed
from strategy import scan_t1
from portfolio import T1Portfolio


def _settle_btc_at(close_time_iso: str) -> Optional[float]:
    """For paper mode, use a live Coinbase pull at the moment we detect close.
    Production would use Kalshi's official BRTI 60-tick average."""
    return _coinbase_spot()


def _log_line(path, payload: dict):
    with open(path, 'a') as f:
        f.write(json.dumps(payload, default=str) + '\n')


class T1PaperRunner:
    def __init__(self, bankroll=None, verbose=True):
        if bankroll is not None:
            CFG['starting_bankroll'] = bankroll
        self.verbose = verbose
        self.client = KalshiClient()
        self.spot = SpotFeed()
        self.mkt = MarketFeed(self.client)
        self.portfolio = T1Portfolio.load(CFG['paper_state'])
        self.cycle = 0
        self._running = False
        self._last_save = 0

    def _say(self, msg):
        if self.verbose:
            ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
            print(f'[{ts}] {msg}', flush=True)

    def start(self):
        self.spot.start()
        self.mkt.start()
        self._running = True
        self._say('T1 monotonicity arb paper runner started')
        self._say(f'  Bankroll: ${self.portfolio.cash:.2f}  '
                  f'Open: {len(self.portfolio.open_positions)}  '
                  f'Realized: ${self.portfolio.realized_pnl:+.2f}')
        self._say('Warming up (10s)...')
        time.sleep(10)

    def stop(self):
        self._running = False
        self.spot.stop()
        self.mkt.stop()
        self.portfolio.save(CFG['paper_state'])
        self._say('T1 paper runner stopped')

    def run_cycle(self) -> dict:
        self.cycle += 1
        spot = self.spot.price
        snap = self.mkt.snapshot()
        markets = snap.get('markets') or {}
        spot_move_30s = self.spot.move_over(30.0)

        payload = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'cycle': self.cycle,
            'spot': spot,
            'spot_move_30s': spot_move_30s,
            'event_ticker': snap.get('event_ticker'),
            'mkt_count': len(markets),
            'open_positions': len(self.portfolio.open_positions),
            'cash': round(self.portfolio.cash, 4),
            'realized_pnl': round(self.portfolio.realized_pnl, 4),
            'daily_pnl': round(self.portfolio.daily_pnl, 4),
            'halted': self.portfolio.halted,
            'opened': [],
            'settled': [],
        }

        if spot is None or not markets:
            payload['note'] = 'waiting for data'
            if time.time() - self._last_save > CFG['state_save_sec']:
                self.portfolio.save(CFG['paper_state'])
                self._last_save = time.time()
            return payload

        # 1. Settle any positions whose event has closed
        now = datetime.now(timezone.utc)
        to_settle = []
        for pid, pos in list(self.portfolio.open_positions.items()):
            ct = pos.close_time
            if isinstance(ct, str) and ct:
                try:
                    close_dt = datetime.fromisoformat(ct.replace('Z', '+00:00'))
                except Exception:
                    continue
                if now >= close_dt:
                    to_settle.append((pid, pos))
        for pid, pos in to_settle:
            btc_at_close = _settle_btc_at(pos.close_time) or spot
            settled = self.portfolio.settle_position(pid, btc_at_close)
            if settled:
                payload['settled'].append({
                    'pid': pid, 'lo': settled.mkt_lo, 'hi': settled.mkt_hi,
                    'btc_at_close': btc_at_close,
                    'realized_pnl': round(settled.realized_pnl, 4),
                })
                self._say(f'  SETTLE {pid} BTC=${btc_at_close:.0f} '
                          f'lo={settled.strike_lo} hi={settled.strike_hi} '
                          f'pnl=${settled.realized_pnl:+.3f}')

        # 2. Halt check
        if self.portfolio.halted:
            payload['note'] = 'halted'
            return payload

        # 3. Scan for new T1 signals
        open_tickers = set()
        for p in self.portfolio.open_positions.values():
            open_tickers.add(p.mkt_lo)
            open_tickers.add(p.mkt_hi)
        sigs = scan_t1(markets, spot, spot_move_30s,
                        bankroll_avail=self.portfolio.cash,
                        open_market_tickers=open_tickers)
        payload['n_signals'] = len(sigs)

        # 4. Open positions for top signals (sorted by edge already)
        for sig in sigs:
            sig_dict = {
                'pair_id': sig.pair_id,
                'event_ticker': sig.event_ticker,
                'mkt_lo': sig.mkt_lo, 'mkt_hi': sig.mkt_hi,
                'strike_lo': sig.strike_lo, 'strike_hi': sig.strike_hi,
                'qty': sig.qty,
                'ask_lo': sig.ask_lo, 'bid_hi': sig.bid_hi,
                'net_edge': sig.net_edge,
                'close_time': (sig.detected_at + timedelta(
                    seconds=sig.secs_to_close)).isoformat(),
            }
            pos = self.portfolio.open_position(sig_dict)
            if pos:
                payload['opened'].append({
                    'pid': pos.position_id, 'lo': sig.mkt_lo, 'hi': sig.mkt_hi,
                    'qty': sig.qty,
                    'ask_lo': sig.ask_lo, 'bid_hi': sig.bid_hi,
                    'net_edge_per_pair': round(sig.net_edge, 4),
                    'total_cost': round(pos.total_cost, 3),
                    'min_payout': round(pos.qty * 1.0 - pos.total_cost, 3),
                })
                self._say(f'  OPEN T1 lo={sig.mkt_lo} hi={sig.mkt_hi} '
                          f'qty={sig.qty} edge={sig.net_edge*100:+.2f}¢/pair '
                          f'cost=${pos.total_cost:.2f} min_pnl=${pos.qty - pos.total_cost:+.2f}')
                # Each market can only host one position per leg — refresh blocked set
                open_tickers.add(sig.mkt_lo)
                open_tickers.add(sig.mkt_hi)

        # 5. Save state periodically
        if time.time() - self._last_save > CFG['state_save_sec']:
            self.portfolio.save(CFG['paper_state'])
            self._last_save = time.time()
        return payload

    def loop(self, max_cycles: Optional[int] = None):
        self.start()
        try:
            while self._running:
                cycle = self.run_cycle()
                _log_line(CFG['paper_log'], cycle)
                if cycle.get('opened') or cycle.get('settled') or self.cycle % 20 == 0:
                    move_str = (f'{cycle["spot_move_30s"]:.0f}'
                                if cycle.get('spot_move_30s') is not None else 'n/a')
                    self._say(f'cycle {cycle["cycle"]}: '
                              f'spot=${cycle.get("spot",0) or 0:.0f} '
                              f'move30=${move_str} '
                              f'mkts={cycle.get("mkt_count",0)} '
                              f'sigs={cycle.get("n_signals","--")} '
                              f'open={cycle["open_positions"]} '
                              f'cash=${cycle["cash"]:.2f} '
                              f'pnl=${cycle["realized_pnl"]:+.2f}')
                if max_cycles and self.cycle >= max_cycles:
                    break
                time.sleep(CFG['decision_loop_sec'])
        except KeyboardInterrupt:
            self._say('interrupted')
        finally:
            self.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bankroll', type=float, default=None)
    ap.add_argument('--max-cycles', type=int, default=None)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--reset', action='store_true')
    args = ap.parse_args()
    if args.reset:
        for p in [CFG['paper_state'], CFG['paper_log']]:
            if p.exists(): p.unlink()
        print('Reset T1 paper state.')
    r = T1PaperRunner(bankroll=args.bankroll, verbose=not args.quiet)
    r.loop(max_cycles=args.max_cycles)
    print('\nFinal state:')
    print(json.dumps(r.portfolio.to_state(), indent=2, default=str))


if __name__ == '__main__':
    main()
