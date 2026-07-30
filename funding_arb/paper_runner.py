"""Paper-trade runner for ETH/BTC funding arb. ZERO setup required.

Pulls live data from Hyperliquid (public API, no auth) + Coinbase (public spot),
simulates what your positions WOULD do, logs to data/paper_log.jsonl.

Run this TODAY: `python paper_runner.py`. Leave it running. Check `data/paper_log.jsonl`
in a week to see the strategy working with REAL live data.

USAGE:
  python paper_runner.py                    # default: $5k per asset, ETH+BTC
  python paper_runner.py --notional 10000   # bigger sim position
  python paper_runner.py --assets ETH       # just ETH (recommended — BTC funding is weak)
"""
from __future__ import annotations
import argparse, json, time, signal, sys
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import urllib.error

DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
LOG_PATH = DATA_DIR / 'paper_log.jsonl'
STATE_PATH = DATA_DIR / 'paper_state.json'


# ─── Public APIs (no auth) ────────────────────────────────────────
HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
CB_PRICE_URL = 'https://api.coinbase.com/v2/prices/{pair}/spot'


def http_post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def http_get_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def fetch_hl_funding_and_mark(coins):
    """Returns {coin: {'mark': float, 'funding_bps_8h': float}}."""
    meta = http_post_json(HL_INFO_URL, {'type': 'metaAndAssetCtxs'})
    universe = meta[0]['universe']
    ctxs = meta[1]
    out = {}
    for i, u in enumerate(universe):
        if u['name'] not in coins: continue
        ctx = ctxs[i]
        # Hyperliquid funding rate is HOURLY (1h cycle on HL)
        f_hr = float(ctx.get('funding', 0.0))
        mark = float(ctx.get('markPx', 0.0))
        out[u['name']] = {
            'mark': mark,
            'funding_bps_hr': f_hr * 10000,
            'funding_bps_8h': f_hr * 10000 * 8,
            'open_interest_usd': float(ctx.get('openInterest', 0)) * mark,
        }
    return out


def fetch_coinbase_spot(pair):
    """pair = 'ETH-USD' or 'BTC-USD'."""
    data = http_get_json(CB_PRICE_URL.format(pair=pair))
    return float(data['data']['amount'])


# ─── Simulator ────────────────────────────────────────────────────
SPOT_FEE_BPS = 5    # Coinbase Advanced taker
PERP_FEE_BPS = 2.5  # Hyperliquid taker
SLIP_BPS = 1.5      # per leg

def init_or_load_state(notional_per_asset, assets):
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    state = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'notional_per_asset': notional_per_asset,
        'positions': {a: {
            'in_position': False,
            'spot_entry': None,
            'perp_entry': None,
            'opened_at': None,
            'cum_funding_collected_usd': 0.0,
            'cum_fees_paid_usd': 0.0,
            'cum_spread_pnl_usd': 0.0,
            'last_spot': None,
            'last_perp': None,
            'n_funding_payments': 0,
        } for a in assets},
        'cycles': 0,
        'total_nav': notional_per_asset * len(assets),
    }
    return state


def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def log_cycle(record):
    with open(LOG_PATH, 'a') as f:
        f.write(json.dumps(record) + '\n')


def decide_and_simulate(state, hl_data, spot_prices):
    """One cycle: update funding, decide enter/exit, log."""
    notional = state['notional_per_asset']
    ts = datetime.now(timezone.utc).isoformat()
    for asset, pos in state['positions'].items():
        if asset not in hl_data: continue
        spot_pair = f'{asset}-USD'
        if spot_pair not in spot_prices: continue
        f_bps_hr = hl_data[asset]['funding_bps_hr']
        mark = hl_data[asset]['mark']
        spot = spot_prices[spot_pair]
        basis_bps = (mark/spot - 1) * 10000

        # Decision logic
        ENTER_THRESHOLD_BPS_HR = 0.1       # only enter if funding > 0.1 bps/hr (~1% APR)
        EXIT_THRESHOLD_BPS_HR  = -1.0      # exit if funding flips strongly negative
        BASIS_SAFETY_BPS       = 15.0      # don't enter if basis > 15 bps richer than funding APR
        action = 'hold'

        if not pos['in_position']:
            # Enter when funding positive AND basis not blown out
            if f_bps_hr > ENTER_THRESHOLD_BPS_HR and basis_bps < BASIS_SAFETY_BPS:
                # Simulate opening: pay 1 leg of spot fee + 1 leg of perp fee
                fee = ((SPOT_FEE_BPS + SLIP_BPS) + (PERP_FEE_BPS + SLIP_BPS))/10000 * notional
                pos['in_position'] = True
                pos['spot_entry'] = spot
                pos['perp_entry'] = mark
                pos['last_spot'] = spot
                pos['last_perp'] = mark
                pos['opened_at'] = ts
                pos['cum_fees_paid_usd'] += fee
                state['total_nav'] -= fee
                action = 'OPEN'
        else:
            # Collect funding: short perp earns funding when funding rate is positive
            funding_payment = f_bps_hr/10000 * notional
            pos['cum_funding_collected_usd'] += funding_payment
            state['total_nav'] += funding_payment
            pos['n_funding_payments'] += 1
            # Realize spread change since last cycle
            d_spot = spot/pos['last_spot'] - 1
            d_perp = mark/pos['last_perp'] - 1
            spread_pnl = (d_spot - d_perp) * notional
            pos['cum_spread_pnl_usd'] += spread_pnl
            state['total_nav'] += spread_pnl
            pos['last_spot'] = spot
            pos['last_perp'] = mark
            # Exit when funding turns negative
            if f_bps_hr < EXIT_THRESHOLD_BPS_HR:
                fee = ((SPOT_FEE_BPS + SLIP_BPS) + (PERP_FEE_BPS + SLIP_BPS))/10000 * notional
                pos['in_position'] = False
                pos['cum_fees_paid_usd'] += fee
                state['total_nav'] -= fee
                action = 'CLOSE'

        # Log this asset's cycle
        record = {
            'ts': ts, 'cycle': state['cycles'], 'asset': asset,
            'mark': mark, 'spot': spot,
            'funding_bps_hr': round(f_bps_hr, 4),
            'funding_bps_8h': round(f_bps_hr * 8, 3),
            'funding_apr_pct': round(f_bps_hr/10000 * 24 * 365 * 100, 2),
            'basis_bps': round(basis_bps, 2),
            'in_position': pos['in_position'],
            'action': action,
            'cum_funding_usd': round(pos['cum_funding_collected_usd'], 4),
            'cum_spread_usd': round(pos['cum_spread_pnl_usd'], 4),
            'cum_fees_usd': round(pos['cum_fees_paid_usd'], 4),
            'asset_pnl_usd': round(pos['cum_funding_collected_usd']
                                    + pos['cum_spread_pnl_usd']
                                    - pos['cum_fees_paid_usd'], 4),
        }
        log_cycle(record)
        # Console
        print(f"  [{asset}] f={f_bps_hr:+.2f}bps/hr ({record['funding_apr_pct']:+.1f}% APR)  "
              f"basis={basis_bps:+.1f}bps  pos={'YES' if pos['in_position'] else 'no '}  "
              f"action={action:>5}  asset_pnl=${record['asset_pnl_usd']:+.4f}")

    state['cycles'] += 1


def print_dashboard(state):
    print(f"\n=== PAPER STATE  cycles={state['cycles']}  started={state['started_at'][:19]} ===")
    total_funding = sum(p['cum_funding_collected_usd'] for p in state['positions'].values())
    total_spread = sum(p['cum_spread_pnl_usd'] for p in state['positions'].values())
    total_fees = sum(p['cum_fees_paid_usd'] for p in state['positions'].values())
    total_pnl = total_funding + total_spread - total_fees
    cap = state['notional_per_asset'] * len(state['positions'])
    print(f"  Total funding collected: ${total_funding:+.4f}")
    print(f"  Total spread P&L:        ${total_spread:+.4f}")
    print(f"  Total fees paid:         ${total_fees:.4f}")
    print(f"  Net P&L:                 ${total_pnl:+.4f}  ({total_pnl/cap*100:+.4f}% of ${cap})")
    print(f"  Total NAV simulated:     ${state['total_nav']:.2f}")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--notional', type=float, default=5000,
                     help='Simulated notional per asset (default $5k)')
    ap.add_argument('--assets', nargs='+', default=['ETH'],
                     help='Assets to simulate (default ETH only — BTC funding is weak)')
    ap.add_argument('--interval-sec', type=int, default=600,
                     help='Cycle interval in seconds (default 600 = 10min)')
    args = ap.parse_args()

    state = init_or_load_state(args.notional, args.assets)

    # Graceful shutdown
    stop = False
    def sigint(*_):
        nonlocal stop
        stop = True
        print('\n  Received Ctrl-C, finishing cycle and saving state...')
    signal.signal(signal.SIGINT, sigint)

    print(f"=== Funding-arb PAPER runner ===")
    print(f"  assets: {args.assets}")
    print(f"  notional/asset: ${args.notional:,.0f}")
    print(f"  cycle interval: {args.interval_sec}s")
    print(f"  log: {LOG_PATH}")
    print(f"  state: {STATE_PATH}")
    print("  press Ctrl-C anytime to stop.\n")

    while not stop:
        try:
            hl_data = fetch_hl_funding_and_mark(args.assets)
            spot_prices = {f'{a}-USD': fetch_coinbase_spot(f'{a}-USD') for a in args.assets}
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC] cycle {state['cycles']}")
            decide_and_simulate(state, hl_data, spot_prices)
            save_state(state)
            if state['cycles'] % 6 == 0:
                print_dashboard(state)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  network err: {e}, retrying in 30s")
            time.sleep(30); continue
        except Exception as e:
            print(f"  unexpected err: {e}, retrying in 30s")
            time.sleep(30); continue
        # Sleep until next cycle (or stop)
        for _ in range(args.interval_sec):
            if stop: break
            time.sleep(1)

    print_dashboard(state)
    save_state(state)
    print(f"\n  state saved to {STATE_PATH}")
    print(f"  log appended to {LOG_PATH}  (view with: tail -f {LOG_PATH} | jq .)")


if __name__ == '__main__':
    main()
