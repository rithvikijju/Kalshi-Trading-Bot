"""Paper runner for cross-exchange HL × OKX funding carry.

Simulates a delta-neutral perp pair per coin in UNIVERSE:
  - HL leg: sign × notional perp position
  - OKX leg: opposite

Funding accrues every 8h (OKX cycle) and 1h (HL cycle). We mark every
POLL_INTERVAL_SEC, collect funding when OKX funding times trigger.

State persisted in data/paper_state.json. Log in data/paper_log.jsonl.
"""
from __future__ import annotations
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import UNIVERSE, NOTIONAL_PER_COIN, FEE_BP_PER_LEG, POLL_INTERVAL_SEC, PAPER_BANKROLL
from feed import aligned_spread

DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
STATE_PATH = DATA_DIR / 'paper_state.json'
LOG_PATH = DATA_DIR / 'paper_log.jsonl'


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_state():
    if STATE_PATH.exists():
        return json.load(open(STATE_PATH))
    state = {
        'started_at': now_iso(),
        'bankroll': PAPER_BANKROLL,
        'positions': {},
        'cum_funding_usd': 0.0,
        'cum_fees_usd': 0.0,
        'cum_mtm_pnl_usd': 0.0,
        'cycles': 0,
        'last_okx_funding_times': {},
    }
    for c, cfg in UNIVERSE.items():
        state['positions'][c] = {
            'sign': cfg['sign'],
            'notional_usd': NOTIONAL_PER_COIN,
            'in_position': False,
            'hl_entry_mark': None,
            'okx_entry_mark': None,
            'opened_at': None,
            'cum_funding_this_pos': 0.0,
        }
    return state


def open_position(state, coin, snap_c):
    p = state['positions'][coin]
    if p['in_position']: return
    p['in_position'] = True
    p['hl_entry_mark'] = snap_c['hl_mark']
    p['okx_entry_mark'] = snap_c['okx_mark']
    p['opened_at'] = now_iso()
    p['cum_funding_this_pos'] = 0.0
    # Fees: HL maker + OKX maker per leg (1 bp each side)
    fee_usd = p['notional_usd'] * FEE_BP_PER_LEG / 10000
    state['cum_fees_usd'] += fee_usd
    state['bankroll'] -= fee_usd
    log({'event': 'OPEN', 'coin': coin, 'sign': p['sign'],
         'hl_mark': p['hl_entry_mark'], 'okx_mark': p['okx_entry_mark'],
         'notional_usd': p['notional_usd'], 'fee_usd': round(fee_usd, 4)})


def close_position(state, coin, snap_c, reason='manual'):
    p = state['positions'][coin]
    if not p['in_position']: return
    # MTM: HL short collects when price drops; OKX long opposite. Net = 0 in theory.
    # sign=+1 → SHORT HL (gains when HL drops), LONG OKX (gains when OKX rises).
    hl_pnl  = -p['sign'] * (snap_c['hl_mark']  - p['hl_entry_mark'])  / p['hl_entry_mark']  * p['notional_usd']
    okx_pnl = +p['sign'] * (snap_c['okx_mark'] - p['okx_entry_mark']) / p['okx_entry_mark'] * p['notional_usd']
    mtm = hl_pnl + okx_pnl
    fee_usd = p['notional_usd'] * FEE_BP_PER_LEG / 10000
    state['bankroll'] += mtm - fee_usd
    state['cum_mtm_pnl_usd'] += mtm
    state['cum_fees_usd'] += fee_usd
    p['in_position'] = False
    log({'event': 'CLOSE', 'coin': coin, 'reason': reason,
         'mtm_usd': round(mtm, 4), 'fee_usd': round(fee_usd, 4),
         'cum_funding_this_pos_usd': round(p['cum_funding_this_pos'], 4),
         'hl_mark': snap_c['hl_mark'], 'okx_mark': snap_c['okx_mark']})


def collect_hl_funding(state, coin, snap_c, dt_hours):
    """HL pays funding every hour. Approximate continuous accrual.
    Position sign=+1 (SHORT HL): receives hl_rate × notional per hour when rate > 0.
    """
    p = state['positions'][coin]
    if not p['in_position']: return
    hl_rate_per_hr = snap_c['hl_apr'] / (24 * 365 * 100)
    inc = -p['sign'] * hl_rate_per_hr * p['notional_usd'] * dt_hours
    # Sign convention: position_hl = -sign × notional (sign=+1 means SHORT HL)
    # Income on HL = -position_hl × rate × notional = +sign × rate × notional. Wait let me redo.
    # SHORT HL: position_hl = -1. Funding income = -position_hl × rate × $notional = +rate × notional.
    # If sign=+1 (SHORT HL), inc = +rate × notional. Correct: inc = sign × rate × notional.
    inc = p['sign'] * hl_rate_per_hr * p['notional_usd'] * dt_hours
    state['bankroll'] += inc
    state['cum_funding_usd'] += inc
    p['cum_funding_this_pos'] += inc


def collect_okx_funding(state, coin, snap_c):
    """Triggered every 8h at OKX funding time. Position sign=+1 means LONG OKX,
    so pays funding when rate > 0."""
    p = state['positions'][coin]
    if not p['in_position']: return
    # We use the realized OKX 8h rate from the last snapshot
    okx_rate_8h = snap_c['okx_apr'] / (24 * 365 * 100) * 8
    # LONG OKX: position_okx = +1. Funding paid = +rate. We RECEIVE = -rate.
    # If sign=+1 → LONG OKX → income = -rate × notional (lose if rate>0, gain if rate<0)
    # General: income = -sign × rate × notional
    inc = -p['sign'] * okx_rate_8h * p['notional_usd']
    state['bankroll'] += inc
    state['cum_funding_usd'] += inc
    p['cum_funding_this_pos'] += inc
    log({'event': 'OKX_FUND', 'coin': coin, 'okx_rate_8h_pct': round(okx_rate_8h*100, 4),
         'inc_usd': round(inc, 4)})


def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2, default=str)


def log(rec):
    rec['ts'] = now_iso()
    with open(LOG_PATH, 'a') as f:
        f.write(json.dumps(rec) + '\n')


def run():
    state = init_state()
    log({'event': 'STARTUP', 'bankroll': state['bankroll']})
    last_poll_ts = time.time()

    def sigterm(sig, frame):
        log({'event': 'SHUTDOWN'})
        save_state(state)
        sys.exit(0)
    signal.signal(signal.SIGTERM, sigterm)
    signal.signal(signal.SIGINT, sigterm)

    while True:
        try:
            snap = aligned_spread(list(UNIVERSE.keys()))
            now = time.time()
            dt_hours = (now - last_poll_ts) / 3600.0
            last_poll_ts = now

            # Open any position that's not yet open
            for c, cfg in UNIVERSE.items():
                if c not in snap: continue
                p = state['positions'][c]
                if not p['in_position']:
                    open_position(state, c, snap[c])
                # HL funding accrual (per hour)
                collect_hl_funding(state, c, snap[c], dt_hours)
                # OKX funding settled when funding time passes
                last_t = state['last_okx_funding_times'].get(c, 0)
                next_t = snap[c].get('okx_next_funding_time', 0)
                # If next funding time is in the past, fund just happened
                if next_t and last_t < next_t and (next_t/1000) < now:
                    collect_okx_funding(state, c, snap[c])
                    state['last_okx_funding_times'][c] = next_t

            state['cycles'] += 1
            # Compute NAV
            nav = state['bankroll']
            for c in UNIVERSE:
                if state['positions'][c]['in_position']:
                    nav += 0  # delta-neutral, MTM ~0 expected
            state['nav'] = nav

            # Snapshot log every 100 cycles (~ every 100 min)
            if state['cycles'] % 100 == 0:
                snap_summary = {c: round(snap[c]['spread_apr'], 2) for c in snap}
                log({'event': 'NAV', 'cycle': state['cycles'], 'nav': round(nav, 4),
                     'cum_fund': round(state['cum_funding_usd'], 4),
                     'cum_fees': round(state['cum_fees_usd'], 4),
                     'spreads_apr': snap_summary})
                save_state(state)

        except KeyboardInterrupt:
            sigterm(None, None)
        except Exception as e:
            log({'event': 'ERROR', 'error': f'{type(e).__name__}: {e}'})

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == '__main__':
    run()
