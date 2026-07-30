"""Unified portfolio dashboard — aggregates all paper-trading sleeves.

Reads state files from:
  - funding_arb/data/paper_state.json
  - kalshi_k6/data/paper_state.json
  - hf_pairs_live/data/paper_state.json

Shows per-sleeve NAV, total NAV, allocation %, capacity headroom, runtime status.

Usage:
  python portfolio_dashboard.py             # one-shot snapshot
  python portfolio_dashboard.py --watch     # refresh every 30s
"""
from __future__ import annotations
import argparse, json, os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path


BASE = Path('/Users/rithvikijju/edge-bot')

# Each sleeve: (name, state_path, runtime_pid_path_or_None, capacity_ceiling, description)
SLEEVES = [
    {
        'name': 'funding_arb',
        'state': BASE / 'funding_arb' / 'data' / 'paper_state.json',
        'log':   BASE / 'funding_arb' / 'data' / 'paper_log.jsonl',
        'capacity_low': 5_000,
        'capacity_high': 50_000_000,
        'cmd_match': 'paper_runner.py --assets',
        'desc': 'ETH/BTC funding-rate arb (spot+perp). Capacity = perp OI.',
    },
    {
        'name': 'kalshi_t1',
        'state': BASE / 'kalshi_t1' / 'data' / 'paper_state.json',
        'log':   BASE / 'kalshi_t1' / 'data' / 'paper_log.jsonl',
        'capacity_low': 50,
        'capacity_high': 5_000,
        'cmd_match': 'kalshi_t1',
        'desc': 'BTC binary cross-strike monotonicity arb (risk-free). Capacity = Kalshi depth.',
    },
    {
        'name': 'kalshi_k6',
        'state': BASE / 'kalshi_k6' / 'data' / 'paper_state.json',
        'log':   BASE / 'kalshi_k6' / 'data' / 'paper_log.jsonl',
        'capacity_low': 100,
        'capacity_high': 5_000,
        'cmd_match': 'kalshi_k6',
        'desc': 'BTC binary spot-displacement staleness arb. Capacity = Kalshi depth.',
    },
    {
        'name': 'kalshi_kcf',
        'state': BASE / 'kalshi_kcf' / 'data' / 'paper_state.json',
        'log':   BASE / 'kalshi_kcf' / 'data' / 'paper_log.jsonl',
        'capacity_low': 50,
        'capacity_high': 5_000,
        'cmd_match': 'kalshi_kcf',
        'desc': 'BTC binary capitulation-fade (informed/uninformed). Capacity = Kalshi depth.',
    },
    {
        'name': 'kalshi_k6_eth',
        'state': BASE / 'kalshi_k6_eth' / 'data' / 'paper_state.json',
        'log':   BASE / 'kalshi_k6_eth' / 'data' / 'paper_log.jsonl',
        'capacity_low': 50,
        'capacity_high': 500,
        'cmd_match': 'kalshi_k6_eth',
        'desc': 'ETH binary spot-displacement (K6 ported to ETH hourly). Smaller capacity than BTC version.',
    },
    {
        'name': 'hf_pairs',
        'state': BASE / 'hf_pairs_live' / 'data' / 'paper_state.json',
        'log':   BASE / 'hf_pairs_live' / 'data' / 'paper_log.jsonl',
        'capacity_low': 1_000,
        'capacity_high': 30_000_000,
        'cmd_match': 'hf_pairs',
        'desc': 'BTC/ETH cointegration mean-reversion. Capacity = Coinbase depth.',
    },
]


def find_running_pid(cmd_match: str):
    """Returns (pid, etime_str) if a process matching cmd_match is running.
    Uses both `ps aux` (process args) and lsof of state file (cwd) to attribute."""
    try:
        out = subprocess.check_output(['ps', 'aux']).decode()
        for line in out.split('\n'):
            if cmd_match in line and 'grep' not in line and 'portfolio_dashboard' not in line:
                parts = line.split(None, 10)
                pid = parts[1]
                return pid, parts[9] if len(parts) > 9 else '?'
    except Exception:
        pass
    return None, None


def find_running_pid_by_cwd(state_path: Path):
    """Walk all paper_runner.py processes, find the one whose cwd matches
    the state file's parent.parent directory."""
    target_dir = str(state_path.parent.parent.resolve())
    try:
        out = subprocess.check_output(['ps', '-eo', 'pid,command']).decode()
        for line in out.split('\n'):
            if 'paper_runner.py' not in line: continue
            if 'grep' in line: continue
            pid = line.strip().split()[0]
            try:
                cwd_out = subprocess.check_output(
                    ['lsof', '-a', '-d', 'cwd', '-Fn', '-p', pid],
                    stderr=subprocess.DEVNULL).decode()
                for cwd_line in cwd_out.split('\n'):
                    if cwd_line.startswith('n') and cwd_line[1:] == target_dir:
                        return pid, ''
            except Exception:
                pass
    except Exception:
        pass
    return None, None


def load_state(path: Path):
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def count_log_lines(path: Path):
    if not path.exists(): return 0
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def fmt_money(x):
    if x is None: return 'n/a'
    if abs(x) >= 1e6: return f'${x/1e6:>7.2f}M'
    if abs(x) >= 1e3: return f'${x/1e3:>7.2f}K'
    return f'${x:>8.2f}'


def fmt_pct(x, base):
    if x is None or base is None or base == 0: return 'n/a'
    return f'{(x/base)*100:+.2f}%'


def render():
    print()
    print('=' * 100)
    print(f'PORTFOLIO DASHBOARD — {datetime.now(timezone.utc).isoformat()[:19]} UTC')
    print('=' * 100)

    total_starting = 0.0
    total_nav = 0.0
    total_realized = 0.0
    rows = []

    for s in SLEEVES:
        state = load_state(s['state'])
        # First try by-cwd which is more reliable for multiple paper_runners
        pid, etime = find_running_pid_by_cwd(s['state'])
        if pid is None:
            pid, etime = find_running_pid(s['cmd_match'])
        log_count = count_log_lines(s['log'])

        if state is None:
            rows.append({
                'name': s['name'], 'starting': 0, 'nav': 0, 'realized': 0,
                'open': 0, 'closed': 0, 'started_at': None,
                'pid': pid, 'log_cycles': log_count, 'desc': s['desc'],
                'capacity': f"${s['capacity_low']:,} - ${s['capacity_high']:,}",
                'note': 'no state file',
            })
            continue

        # Funding-arb state has different schema (positions per asset)
        if s['name'] == 'funding_arb':
            starting = state.get('notional_per_asset', 0) * len(state.get('positions', {}))
            nav = state.get('total_nav', starting)
            # realized = sum of cum_funding - cum_fees + cum_spread across positions
            realized = 0.0
            for asset_state in state.get('positions', {}).values():
                realized += (asset_state.get('cum_funding_collected_usd', 0)
                              - asset_state.get('cum_fees_paid_usd', 0)
                              + asset_state.get('cum_spread_pnl_usd', 0))
            open_count = sum(1 for a in state.get('positions', {}).values()
                              if a.get('in_position'))
            closed = 0  # funding arb doesn't close cycles in this sense
            started_at = state.get('started_at')
        else:
            starting = state.get('starting_bankroll', 0)
            nav = state.get('nav', state.get('cash', 0))
            realized = state.get('realized_pnl', 0)
            open_count = state.get('open_positions_count', 0)
            closed = state.get('closed_trades_count', 0)
            started_at = state.get('started_at')

        total_starting += starting
        total_nav += nav
        total_realized += realized
        rows.append({
            'name': s['name'], 'starting': starting, 'nav': nav,
            'realized': realized, 'open': open_count, 'closed': closed,
            'started_at': started_at, 'pid': pid, 'log_cycles': log_count,
            'desc': s['desc'],
            'capacity': f"${s['capacity_low']:,} - ${s['capacity_high']:,}",
        })

    # Sleeve table
    print(f"{'sleeve':<14} {'status':<10} {'started':<12} {'starting':<12} {'NAV':<12} "
          f"{'realized':<12} {'%':<8} {'open':<5} {'closed':<7} {'cycles':<7}")
    print('-' * 100)
    for r in rows:
        status = f'PID {r["pid"]}' if r['pid'] else 'STOPPED'
        started = r['started_at'][:10] if r['started_at'] else '–'
        starting = r.get('starting', 0)
        nav = r.get('nav', 0)
        pct = (nav - starting) / starting * 100 if starting else 0
        print(f"{r['name']:<14} {status:<10} {started:<12} "
              f"{fmt_money(starting):<12} {fmt_money(nav):<12} "
              f"{fmt_money(r['realized']):<12} {pct:>+5.2f}%   "
              f"{r['open']:<5} {r['closed']:<7} {r['log_cycles']:<7}")

    print('-' * 100)
    total_pct = (total_nav - total_starting) / total_starting * 100 if total_starting else 0
    print(f"{'TOTAL':<14} {'':<10} {'':<12} {fmt_money(total_starting):<12} "
          f"{fmt_money(total_nav):<12} {fmt_money(total_realized):<12} {total_pct:>+5.2f}%")

    # Scaling map
    print()
    print('SLEEVE DESCRIPTIONS & CAPACITY RANGES')
    print('-' * 100)
    for r in rows:
        print(f"  {r['name']:<14} capacity {r['capacity']}")
        print(f"  {'':14} {r['desc']}")

    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--watch', action='store_true', help='refresh every 30s')
    ap.add_argument('--interval', type=int, default=30)
    args = ap.parse_args()
    if args.watch:
        while True:
            os.system('clear')
            render()
            time.sleep(args.interval)
    else:
        render()


if __name__ == '__main__':
    main()
