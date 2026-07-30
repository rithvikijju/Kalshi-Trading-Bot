"""K6 paper trading runner.

Subscribes to Coinbase BTC spot + Kalshi market data via REST polling. Every
decision cycle:
  1. Scan for K6+K14 signals
  2. For each new signal we can afford, "fill" at the current ask (paper)
  3. Check open positions for settlement — if event closed and we have BTC at close, realize PnL
  4. Save state, log cycle

Usage:
  cd kalshi_k6
  python paper_runner.py
  python paper_runner.py --bankroll 100 --max-cycles 100 --verbose

JSONL log at data/paper_log.jsonl, state at data/paper_state.json.
"""
from __future__ import annotations
import argparse, json, signal, sys, time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from config import CFG, KalshiClient, _coinbase_spot
from spot import SpotFeed
from market_feed import MarketFeed
from strategy import scan_k6, K6Signal
from portfolio import Portfolio


# ─────────────────────────────────────────────────────────────────
# Settlement helper — gets BTC spot at the event's close time
# ─────────────────────────────────────────────────────────────────

def _settle_btc_at(close_time_iso: str) -> Optional[float]:
    """Returns BTC mid at close_time. For paper mode we use a single
    Coinbase spot pull at the moment we detect the event closed. Close enough
    for paper trading — production would use the hour-end print."""
    return _coinbase_spot()


# ─────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────

def _log_line(path, payload: dict):
    with open(path, 'a') as f:
        f.write(json.dumps(payload, default=str) + '\n')


# ─────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────

class PaperRunner:
    def __init__(self, bankroll: float = None, verbose: bool = True):
        if bankroll is not None:
            CFG['starting_bankroll'] = bankroll
        self.verbose = verbose
        # Anonymous client — we only need read access for paper mode
        self.client = KalshiClient()
        self.spot = SpotFeed()
        self.mkt = MarketFeed(self.client)
        # Resume from prior state if present
        self.portfolio = Portfolio.load(CFG['paper_state'], mode='paper')
        self.cycle = 0
        self._running = False
        self._last_state_save = 0

    def _say(self, msg):
        if self.verbose:
            ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
            print(f'[{ts}] {msg}', flush=True)

    def start(self):
        self.spot.start()
        self.mkt.start()
        self._running = True
        self._say('K6 paper runner started')
        self._say(f'  Bankroll: ${self.portfolio.cash:.2f}  '
                  f'Open: {len(self.portfolio.open_positions)}  '
                  f'Realized: ${self.portfolio.realized_pnl:+.2f}')
        # warm-up
        self._say('Warming up (10s wait for spot + market data)...')
        time.sleep(10)

    def stop(self):
        self._running = False
        self.spot.stop()
        self.mkt.stop()
        self.portfolio.save(CFG['paper_state'])
        self._say('K6 paper runner stopped')

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
            'opened': [],
            'settled': [],
        }
        if spot is None or rv is None or not markets:
            cycle_payload['note'] = 'waiting for data'
            return cycle_payload

        # 1. Check for settled positions (events that have closed)
        now = datetime.now(timezone.utc)
        to_settle = []
        for pid, pos in list(self.portfolio.open_positions.items()):
            close_ts = pos.close_time
            if isinstance(close_ts, str) and close_ts:
                try:
                    close_dt = datetime.fromisoformat(close_ts.replace('Z', '+00:00'))
                except Exception:
                    continue
                if now >= close_dt:
                    to_settle.append((pid, pos, close_dt))
        for pid, pos, close_dt in to_settle:
            btc_at_close = _settle_btc_at(pos.close_time)
            if btc_at_close is None:
                # Fall back to current spot
                btc_at_close = spot
            settled = self.portfolio.settle_position(pid, btc_at_close)
            if settled:
                cycle_payload['settled'].append({
                    'market': settled.market_ticker,
                    'side': settled.side,
                    'qty': settled.qty,
                    'entry_price': settled.entry_price,
                    'settle_value': settled.settle_value,
                    'realized_pnl': round(settled.realized_pnl, 4),
                })
                self._say(f'  SETTLED {settled.market_ticker} {settled.side} '
                           f'qty={settled.qty} pnl=${settled.realized_pnl:+.2f}')

        # 2. Halted? skip signal generation
        if self.portfolio.halted:
            cycle_payload['note'] = 'halted — daily loss limit hit'
            return cycle_payload

        # 3. Scan for K6 signals
        open_tickers = {p.market_ticker for p in self.portfolio.open_positions.values()}
        signals = scan_k6(markets, spot, rv,
                           bankroll_avail=self.portfolio.cash,
                           open_market_tickers=open_tickers)
        cycle_payload['n_signals'] = len(signals)

        # 4. Open each signal we can afford (sorted by edge)
        for sig in signals:
            if not self.portfolio.can_open(sig.qty, sig.price):
                continue
            # Paper fill at observed ask
            pos = self.portfolio.open_position(
                {
                    'market_ticker': sig.market_ticker,
                    'event_ticker': sig.event_ticker,
                    'strike': sig.strike,
                    'side': sig.side,
                    'bucket': sig.bucket,
                    'spot': sig.spot,
                    'spot_dist': sig.spot_dist,
                    'rv_15m_annual': sig.rv_15m_annual,
                    'close_time': self._format_close_time(sig.secs_to_close, sig.detected_at),
                },
                fill_price=sig.price, fill_qty=sig.qty)
            if pos:
                cycle_payload['opened'].append({
                    'market': sig.market_ticker, 'side': sig.side,
                    'bucket': sig.bucket, 'qty': sig.qty,
                    'price': sig.price, 'spot_dist': round(sig.spot_dist, 1),
                    'expected_edge': round(sig.expected_edge, 4),
                    'rv_annual': round(sig.rv_15m_annual, 3),
                })
                self._say(f'  OPEN {sig.bucket} {sig.market_ticker} {sig.side} '
                           f'qty={sig.qty} @${sig.price:.3f} dist=${sig.spot_dist:+.0f} '
                           f'edge={sig.expected_edge*100:+.1f}¢')

        # 5. Save state periodically
        if time.time() - self._last_state_save > CFG['state_save_sec']:
            self.portfolio.save(CFG['paper_state'])
            self._last_state_save = time.time()

        return cycle_payload

    def _format_close_time(self, secs_to_close: float, detected_at: datetime) -> str:
        from datetime import timedelta
        ct = detected_at + timedelta(seconds=secs_to_close)
        return ct.isoformat()

    def loop(self, max_cycles: Optional[int] = None):
        self.start()
        try:
            while self._running:
                cycle = self.run_cycle()
                _log_line(CFG['paper_log'], cycle)
                if cycle.get('n_signals'):
                    self._say(f'cycle {cycle["cycle"]}: spot=${cycle["spot"]:.0f} '
                               f'rv={cycle["rv_15m_annual"]:.3f} mkts={cycle["mkt_count"]} '
                               f'signals={cycle["n_signals"]} '
                               f'open={cycle["open_positions"]} '
                               f'cash=${cycle["cash"]:.2f} '
                               f'pnl=${cycle["realized_pnl"]:+.2f}')
                elif self.cycle % 10 == 0:
                    self._say(f'cycle {cycle["cycle"]}: idle. '
                               f'spot=${cycle["spot"] or 0:.0f} '
                               f'rv={cycle["rv_15m_annual"] or 0:.3f} '
                               f'mkts={cycle["mkt_count"]} '
                               f'open={cycle["open_positions"]} '
                               f'cash=${cycle["cash"]:.2f}')
                if max_cycles and self.cycle >= max_cycles:
                    break
                time.sleep(CFG['decision_loop_sec'])
        except KeyboardInterrupt:
            self._say('interrupted by user')
        finally:
            self.stop()


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bankroll', type=float, default=None,
                     help='Starting bankroll (only takes effect on first run; resumed state wins after that)')
    ap.add_argument('--max-cycles', type=int, default=None)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--reset', action='store_true', help='wipe state and start fresh')
    args = ap.parse_args()

    if args.reset:
        for p in [CFG['paper_state'], CFG['paper_log']]:
            if p.exists(): p.unlink()
        print('Reset paper state.')

    runner = PaperRunner(bankroll=args.bankroll, verbose=not args.quiet)
    runner.loop(max_cycles=args.max_cycles)
    print('\nFinal state:')
    print(json.dumps(runner.portfolio.to_state(), indent=2, default=str))


if __name__ == '__main__':
    main()
