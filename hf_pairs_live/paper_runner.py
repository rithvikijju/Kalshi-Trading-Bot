"""HF pairs (BTC/ETH cointegration) paper runner.

Decision loop:
  every decision_loop_sec:
    1. Get current spread + z-score from feed
    2. For each open position: check exit triggers (|z|<0.5, |z|>3.5, 4h timeout)
    3. If no open position and |z| >= 2.0: open new pair trade
    4. Save state, log cycle

Usage:
  cd hf_pairs_live
  python paper_runner.py --bankroll 1000
  python paper_runner.py --reset
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from typing import Optional

from config import CFG
from spot_feed import DualSpotFeed
from portfolio import Portfolio


def _log_line(path, payload: dict):
    with open(path, 'a') as f:
        f.write(json.dumps(payload, default=str) + '\n')


class HFPairsRunner:
    def __init__(self, bankroll=None, verbose=True):
        if bankroll is not None:
            CFG['starting_bankroll'] = bankroll
        self.verbose = verbose
        self.feed = DualSpotFeed()
        self.portfolio = Portfolio.load(CFG['paper_state'])
        self.cycle = 0
        self._running = False
        self._last_save = 0
        self._cycle_start = time.time()

    def _say(self, msg):
        if self.verbose:
            ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
            print(f'[{ts}] {msg}', flush=True)

    def start(self):
        self.feed.start()
        self._running = True
        self._say('HF pairs runner started')
        self._say(f'  Bankroll: ${self.portfolio.cash:.2f}  '
                  f'Open: {len(self.portfolio.open_positions)}  '
                  f'Realized: ${self.portfolio.realized_pnl:+.2f}')
        # Need lookback window worth of data first
        warmup_secs = CFG['lookback_minutes'] * 60
        self._say(f'Warming up ({warmup_secs}s = {CFG["lookback_minutes"]}min '
                  f'rolling window required before first trade)...')

    def stop(self):
        self._running = False
        self.feed.stop()
        self.portfolio.save(CFG['paper_state'])
        self._say('HF pairs runner stopped')

    def run_cycle(self) -> dict:
        self.cycle += 1
        snap = self.feed.snapshot()
        z, mean, std = self.feed.z_score()
        payload = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'cycle': self.cycle,
            'btc': snap.get('btc'), 'eth': snap.get('eth'),
            'spread': snap.get('spread'),
            'z': z, 'spread_mean': mean, 'spread_std': std,
            'n_polls': snap.get('n_polls'),
            'open_positions': len(self.portfolio.open_positions),
            'cash': round(self.portfolio.cash, 4),
            'realized_pnl': round(self.portfolio.realized_pnl, 4),
            'daily_pnl': round(self.portfolio.daily_pnl, 4),
            'halted': self.portfolio.halted,
            'actions': [],
        }

        btc, eth = snap.get('btc'), snap.get('eth')
        if btc is None or eth is None or z is None:
            payload['note'] = 'waiting for data / warmup'
            # Still save state so the dashboard can see we're running
            if time.time() - self._last_save > CFG['state_save_sec']:
                self.portfolio.save(CFG['paper_state'])
                self._last_save = time.time()
            return payload

        now = datetime.now(timezone.utc)

        # 1. Check exits on open positions
        for pid, pos in list(self.portfolio.open_positions.items()):
            entry_ts = datetime.fromisoformat(pos.entry_ts)
            held_hours = (now - entry_ts).total_seconds() / 3600
            reason = None
            if abs(z) < CFG['exit_z']:
                reason = f'z={z:.2f} → take_profit'
            elif abs(z) > CFG['stop_z']:
                reason = f'z={z:.2f} → stop_loss'
            elif held_hours > CFG['time_stop_hours']:
                reason = f'held {held_hours:.1f}h → time_stop'
            if reason:
                closed = self.portfolio.close_position(pid, btc, eth, z, reason)
                if closed:
                    payload['actions'].append({
                        'type': 'close', 'pid': pid, 'reason': reason,
                        'realized_pnl': round(closed.realized_pnl, 4),
                        'held_hours': round(held_hours, 2),
                    })
                    self._say(f'  CLOSE {pid} {reason} '
                               f'pnl=${closed.realized_pnl:+.4f} held={held_hours:.1f}h')

        # 2. Check entries
        if self.portfolio.halted:
            payload['note'] = 'halted'
        elif abs(z) >= CFG['entry_z'] and self.portfolio.can_open():
            # Don't open same direction as existing position
            wanted = 'short_btc_long_eth' if z > 0 else 'long_btc_short_eth'
            already_have = any(p.direction == wanted
                                 for p in self.portfolio.open_positions.values())
            if not already_have:
                pos = self.portfolio.open_position(wanted, btc, eth, snap['spread'], z)
                if pos:
                    payload['actions'].append({
                        'type': 'open', 'pid': pos.position_id,
                        'direction': wanted, 'z': round(z, 3),
                        'btc': btc, 'eth': eth,
                    })
                    self._say(f'  OPEN {wanted} at z={z:+.2f}  '
                               f'BTC=${btc:.0f} ETH=${eth:.0f} '
                               f'cost=${pos.entry_fee:.3f}')

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
                if cycle.get('actions') or self.cycle % 20 == 0:
                    z = cycle.get('z')
                    z_str = f'{z:+.2f}' if z is not None else 'n/a'
                    self._say(f'cycle {cycle["cycle"]}: z={z_str} '
                               f'btc=${cycle.get("btc",0) or 0:.0f} '
                               f'eth=${cycle.get("eth",0) or 0:.0f} '
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
        print('Reset HF pairs state.')
    r = HFPairsRunner(bankroll=args.bankroll, verbose=not args.quiet)
    r.loop(max_cycles=args.max_cycles)
    print('\nFinal state:')
    print(json.dumps(r.portfolio.to_state(), indent=2, default=str))


if __name__ == '__main__':
    main()
