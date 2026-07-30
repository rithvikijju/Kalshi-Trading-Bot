"""Build funding_arb_live.ipynb — end-to-end paper + live engine.

Sections:
  §1 Config + credentials
  §2 Live data fetchers (Hyperliquid funding, Coinbase spot, HL marks)
  §3 Position state (persisted to JSON)
  §4 Paper trading simulator
  §5 Risk monitors (margin, basis, exit gates)
  §6 Live execution stubs (Coinbase + Hyperliquid orders)
  §7 Daily run wrapper
  §8 Monitoring dashboard
  §9 Quick-look historicals (last N days summary)
"""
import json
from pathlib import Path

CELLS = [
    ('md', '''# Funding Rate Arbitrage — Paper + Live Engine

End-to-end trading engine for the long-spot / short-perpetual basis trade.

**What it does:** Maintains a delta-neutral position on ETH and BTC by
holding spot long on Coinbase and shorting perpetuals on Hyperliquid.
Every hour, Hyperliquid pays you the funding rate × position size. No
direction-taking, no prediction — just sit on the trade and collect.

**Modes:**
- `backtest`: replay historical data, no orders
- `paper`: live data, simulated orders (default for first 2 weeks)
- `live`: real money — only after paper trading shows positive PnL

**Realistic expectation at $5K capital (May 2026 regime):** $15-30/month.
Real value is the system itself, scalable when capital grows.

Read `STRATEGY.md` for mechanics, `SETUP_GUIDE.md` for exchange setup,
`API_KEYS.md` for credentials.
'''),

    ('md', '## §1 — Configuration\n'),

    ('code', '''import os, json, math, time, warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Any
import numpy as np
import pandas as pd
import requests

warnings.filterwarnings('ignore')

CRED_PATH = Path.home() / '.config' / 'funding_arb' / 'credentials.env'
CRED = {}
if CRED_PATH.exists():
    for line in CRED_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, v = line.split('=', 1)
        CRED[k.strip()] = v.strip()
print(f'{len(CRED)} credentials loaded' if CRED else f'No creds at {CRED_PATH} — paper-only mode')

CFG = dict(
    MODE              = 'paper',          # 'backtest' | 'paper' | 'live'

    # Universe + sizing for $5K starting capital
    COINS             = ['ETH', 'BTC'],
    SPOT_USD_PER_COIN = 1_250,            # long spot per coin
    PERP_USD_PER_COIN = 1_250,            # short perp per coin (matched)

    # Risk
    MAX_BASIS_BPS     = 50,               # close if basis > 50 bps
    MIN_MARGIN_RATIO  = 3.0,              # close if margin ratio < 3x
    MIN_7D_FUNDING_BPS_PER_HR = 0.005,    # close if 7d MA below 0.005 bp/hr (~ 0.04 bp/day, near zero)

    # Operational
    PAPER_LOG_PATH    = 'funding_arb/data/paper_log.jsonl',
    STATE_PATH        = 'funding_arb/data/state.json',
    POLL_SEC          = 60,
)
Path('funding_arb/data').mkdir(parents=True, exist_ok=True)
print(f"Mode: {CFG['MODE']}   Coins: {CFG['COINS']}   "
      f"$/coin: spot ${CFG['SPOT_USD_PER_COIN']}, perp ${CFG['PERP_USD_PER_COIN']}")
'''),

    ('md', '''## §2 — Live data fetchers

Three sources, all public (no API key required for reads):
- Coinbase spot prices via ccxt
- Hyperliquid mark prices via `/info` endpoint
- Hyperliquid funding rates via `/info` endpoint (1h interval)
'''),

    ('code', '''import ccxt
_cb = ccxt.coinbase({'enableRateLimit': True})
HL_URL = 'https://api.hyperliquid.xyz/info'

def fetch_spot(coin):
    """Coinbase spot ticker. Returns dict with bid, ask, last."""
    t = _cb.fetch_ticker(f'{coin}/USD')
    return {'coin': coin, 'last': float(t['last']),
            'bid': float(t['bid']), 'ask': float(t['ask']),
            'ts': datetime.now(timezone.utc)}

def fetch_perp_mark(coin):
    """Hyperliquid mark price for the coin."""
    r = requests.post(HL_URL, json={'type': 'allMids'}, timeout=8)
    return float(r.json().get(coin, 0))

def fetch_funding_history(coin, hours_back=24*7):
    """Hyperliquid funding rate history for last N hours."""
    start = int((time.time() - hours_back * 3600) * 1000)
    r = requests.post(HL_URL, json={'type': 'fundingHistory', 'coin': coin,
                                      'startTime': start}, timeout=8)
    rows = r.json()
    df = pd.DataFrame(rows)
    if df.empty: return df
    df['ts'] = pd.to_datetime(df['time'], unit='ms', utc=True)
    df['rate'] = df['fundingRate'].astype(float)
    return df[['ts', 'rate']].sort_values('ts').reset_index(drop=True)

def current_basis_bps(coin):
    """(perp_mark - spot_last) / spot_last × 10000."""
    spot = fetch_spot(coin)
    perp = fetch_perp_mark(coin)
    return {'coin': coin, 'spot': spot['last'], 'perp': perp,
            'basis_bps': (perp - spot['last']) / spot['last'] * 10000,
            'ts': spot['ts']}

# Smoke test
print('Spot:')
for c in CFG['COINS']:
    s = fetch_spot(c)
    print(f'  {c}: bid ${s["bid"]:,.2f}  ask ${s["ask"]:,.2f}')
print('\\nFunding (24h):')
for c in CFG['COINS']:
    h = fetch_funding_history(c, hours_back=24)
    if not h.empty:
        rate_mean = h['rate'].mean()
        annualized = rate_mean * 24 * 365 * 100
        print(f'  {c}: mean rate {rate_mean*10000:.3f} bp/hr → {annualized:.2f}% annualized')
print('\\nBasis:')
for c in CFG['COINS']:
    b = current_basis_bps(c)
    print(f'  {c}: spot ${b["spot"]:,.2f}  perp ${b["perp"]:,.2f}  basis {b["basis_bps"]:+.2f} bps')
'''),

    ('md', '''## §3 — Position state

Persisted to `data/state.json` so the bot survives restarts. Each coin has:
- spot_qty, spot_entry_price, spot_entry_ts (long spot, on Coinbase)
- perp_size, perp_entry_price, perp_entry_ts (short perp, on Hyperliquid)
- funding_accrued (running tally of funding income, in USD)
'''),

    ('code', '''def load_state():
    p = Path(CFG['STATE_PATH'])
    if not p.exists():
        return {'positions': {}, 'last_funding_check_ts': None,
                'paper_starting_usd': 5_000, 'started_at': None}
    return json.loads(p.read_text())

def save_state(state):
    Path(CFG['STATE_PATH']).parent.mkdir(parents=True, exist_ok=True)
    Path(CFG['STATE_PATH']).write_text(json.dumps(state, indent=2, default=str))

def log_paper(entry):
    """Append a JSON event to the paper log."""
    p = Path(CFG['PAPER_LOG_PATH'])
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a') as f:
        f.write(json.dumps(entry, default=str) + '\\n')

state = load_state()
print(f"State: {len(state.get('positions', {}))} positions tracked")
print(f"Started at: {state.get('started_at') or 'not yet'}")
for coin, p in state.get('positions', {}).items():
    print(f"  {coin}: spot {p.get('spot_qty', 0):.4f} @ ${p.get('spot_entry_price', 0):,.2f}  "
          f"perp {p.get('perp_size', 0):.4f} @ ${p.get('perp_entry_price', 0):,.2f}  "
          f"funding accrued ${p.get('funding_accrued', 0):.4f}")
'''),

    ('md', '''## §4 — Paper trading: open positions

In paper mode, "opening" means recording an entry at current market prices.
No real orders. Funding accrual will happen in §5.
'''),

    ('code', '''def paper_open(coin):
    """Record opening a delta-neutral position on COIN at current prices."""
    spot = fetch_spot(coin)
    perp = fetch_perp_mark(coin)
    # We BUY spot at the ask and SHORT perp at the perp mark
    spot_price = spot['ask']
    perp_price = perp
    spot_qty = CFG['SPOT_USD_PER_COIN'] / spot_price
    perp_size = CFG['PERP_USD_PER_COIN'] / perp_price
    now = datetime.now(timezone.utc)
    pos = {
        'coin': coin,
        'spot_qty': spot_qty, 'spot_entry_price': spot_price, 'spot_entry_ts': str(now),
        'perp_size': perp_size, 'perp_entry_price': perp_price, 'perp_entry_ts': str(now),
        'funding_accrued': 0.0,
        'last_funding_check_ts': str(now),
    }
    state = load_state()
    state['positions'][coin] = pos
    if state.get('started_at') is None:
        state['started_at'] = str(now)
    save_state(state)
    log_paper({'event': 'open', 'coin': coin, 'spot_price': spot_price,
               'perp_price': perp_price, 'spot_qty': spot_qty,
               'perp_size': perp_size, 'ts': now})
    print(f'  PAPER OPEN {coin}: '
          f'long {spot_qty:.5f} spot @ ${spot_price:,.2f}  '
          f'short {perp_size:.5f} perp @ ${perp_price:,.2f}')
    return pos

def paper_open_all():
    """Open both ETH and BTC positions (call this once when starting paper)."""
    for c in CFG['COINS']:
        if c in load_state().get('positions', {}):
            print(f'  {c} already open, skipping')
            continue
        paper_open(c)

# Don't auto-call yet; user runs paper_open_all() when ready
print("Ready. Call paper_open_all() to open positions in paper mode.")
print("(Skip for now if you want to inspect signals first.)")
'''),

    ('md', '''## §5 — Funding accrual + MtM (the heart of paper mode)

Every time you call `update_positions()`, the bot:
1. Fetches latest funding rates since last check
2. Credits each position with `rate × hours × position_size`
3. Marks the spot leg to current spot price
4. Marks the perp leg to current perp mark
5. Logs everything

In real life, funding payments are credited to your Hyperliquid account
balance automatically every hour. Paper mode just simulates this.
'''),

    ('code', '''def update_positions():
    """Tick the paper-trading state forward: accrue funding, update marks."""
    state = load_state()
    positions = state.get('positions', {})
    if not positions:
        print('No positions open. Run paper_open_all() first.')
        return
    now = datetime.now(timezone.utc)
    for coin, pos in positions.items():
        # 1. Fetch funding events since last check
        last_check = pd.Timestamp(pos.get('last_funding_check_ts', pos['spot_entry_ts']))
        if last_check.tzinfo is None:
            last_check = last_check.tz_localize('UTC')
        hours_since = (now - last_check).total_seconds() / 3600
        if hours_since < 0.5:
            continue  # don't bother updating more than 2x/hr
        funding_hist = fetch_funding_history(coin, hours_back=int(hours_since)+2)
        if not funding_hist.empty:
            # Sum rates for funding events strictly AFTER last_check
            new_funds = funding_hist[funding_hist['ts'] > last_check]
            # Funding income = rate × notional × per_event
            notional = pos['perp_size'] * pos['perp_entry_price']
            funding_income = (new_funds['rate'].sum()) * notional
            pos['funding_accrued'] = float(pos.get('funding_accrued', 0)) + float(funding_income)
            pos['last_funding_check_ts'] = str(now)
            if len(new_funds) > 0:
                print(f"  {coin}: {len(new_funds)} new funding events, "
                      f"+${funding_income:+.4f} ({new_funds['rate'].mean()*10000:.3f} bp/hr avg)")
        # 2. Mark to market: gather current quotes
        spot_now = fetch_spot(coin)['last']
        perp_now = fetch_perp_mark(coin)
        spot_pnl = (spot_now - pos['spot_entry_price']) * pos['spot_qty']
        perp_pnl = (pos['perp_entry_price'] - perp_now) * pos['perp_size']  # SHORT perp
        pos['unrealized_spot_pnl'] = float(spot_pnl)
        pos['unrealized_perp_pnl'] = float(perp_pnl)
        pos['mark_spot'] = float(spot_now)
        pos['mark_perp'] = float(perp_now)
        pos['current_basis_bps'] = (perp_now - spot_now) / spot_now * 10000
    state['positions'] = positions
    save_state(state)
    log_paper({'event': 'update', 'positions_snapshot': positions, 'ts': now})

# Run once now to see what happens
update_positions()
'''),

    ('md', '''## §6 — Risk monitors / auto-exit conditions

Three exit triggers:
1. Basis > MAX_BASIS_BPS (default 50) — basis blowout, close to limit damage
2. 7-day funding average ≤ MIN_7D_FUNDING_BPS — edge has died
3. Margin ratio < MIN_MARGIN_RATIO — get out before forced liquidation

Each is checked per-coin. Closing one coin's position doesn't close the
other.
'''),

    ('code', '''def check_exit_triggers():
    """Returns dict {coin: reason} for coins that should be closed."""
    state = load_state()
    triggers = {}
    for coin, pos in state.get('positions', {}).items():
        # 1. Basis check
        basis = pos.get('current_basis_bps')
        if basis is None:
            update_positions(); state = load_state()
            basis = state['positions'][coin].get('current_basis_bps')
        if basis is not None and abs(basis) > CFG['MAX_BASIS_BPS']:
            triggers[coin] = f'basis_blowout (basis={basis:+.1f}bp > {CFG["MAX_BASIS_BPS"]})'
            continue
        # 2. 7-day funding check
        h = fetch_funding_history(coin, hours_back=24*7)
        if not h.empty:
            mean_bphr = h['rate'].mean() * 10000
            if mean_bphr <= CFG['MIN_7D_FUNDING_BPS_PER_HR']:
                triggers[coin] = f'funding_dead (7d_mean={mean_bphr:.4f}bp/hr <= {CFG["MIN_7D_FUNDING_BPS_PER_HR"]})'
                continue
        # 3. Margin ratio check (paper: assume 5x always until live)
        if CFG['MODE'] == 'live':
            # TODO: query Hyperliquid for actual margin ratio
            pass
    return triggers

def paper_close(coin, reason='manual'):
    """Close a paper position. Realizes the funding accrued + MtM."""
    state = load_state()
    if coin not in state.get('positions', {}):
        print(f'  No open position on {coin}')
        return
    pos = state['positions'][coin]
    update_positions()  # final mark
    state = load_state()
    pos = state['positions'][coin]
    realized_pnl = pos.get('funding_accrued', 0) + pos.get('unrealized_spot_pnl', 0) + pos.get('unrealized_perp_pnl', 0)
    now = datetime.now(timezone.utc)
    log_paper({'event': 'close', 'coin': coin, 'reason': reason,
               'realized_pnl': realized_pnl,
               'funding_accrued': pos.get('funding_accrued', 0),
               'spot_pnl': pos.get('unrealized_spot_pnl', 0),
               'perp_pnl': pos.get('unrealized_perp_pnl', 0),
               'ts': now})
    del state['positions'][coin]
    save_state(state)
    print(f'  PAPER CLOSE {coin}: realized ${realized_pnl:+.4f}  ({reason})')

# Check triggers now
trigs = check_exit_triggers()
print(f'Active exit triggers: {len(trigs)}')
for coin, reason in trigs.items():
    print(f'  {coin}: {reason}')
'''),

    ('md', '''## §7 — Daily run wrapper

One function you can wire to a cron job (`* * * * * python -c ...`) or
just call manually in the notebook. It:
1. Updates all positions (accrue funding, refresh marks)
2. Checks exit triggers
3. Closes any coin that should be closed
4. Logs everything

In live mode it would also place real orders. For paper, it just simulates.
'''),

    ('code', '''def daily_run(auto_close=True):
    """Main loop tick. Call this every 1-60 minutes (cron-able)."""
    now = datetime.now(timezone.utc)
    print(f'\\n─── daily_run  {now.strftime("%Y-%m-%d %H:%M:%S UTC")} ───')
    update_positions()
    trigs = check_exit_triggers()
    if trigs and auto_close:
        for coin, reason in trigs.items():
            print(f'  AUTO-CLOSE {coin}: {reason}')
            paper_close(coin, reason)
    elif trigs:
        print(f'  Exit triggers active but auto_close=False:')
        for coin, reason in trigs.items():
            print(f'    {coin}: {reason}')
    print(f'  Open positions: {len(load_state().get("positions", {}))}')

daily_run()
'''),

    ('md', '''## §8 — Monitoring dashboard

Re-run this cell anytime to see current state. After 1-2 weeks of paper
trading, this is your "is the strategy working?" view.
'''),

    ('code', '''def dashboard():
    state = load_state()
    positions = state.get('positions', {})
    started = state.get('started_at')
    starting_capital = state.get('paper_starting_usd', 5_000)
    print('═' * 78)
    print(f' FUNDING ARB — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}'
          f'   Mode: {CFG["MODE"]}')
    print('═' * 78)
    if started:
        days = (datetime.now(timezone.utc) - pd.Timestamp(started).tz_convert("UTC")).days
        print(f' Started: {started}  ({days} days ago)')
    print(f' Capital: ${starting_capital}')
    print(f' Open positions: {len(positions)}')
    if not positions:
        print(' (no positions — run paper_open_all() to start)')
    total_funding = 0.0
    total_spot_pnl = 0.0
    total_perp_pnl = 0.0
    for coin, pos in positions.items():
        print(f'\\n ── {coin} ──')
        print(f'   spot:  long {pos["spot_qty"]:.5f}  '
              f'@ ${pos["spot_entry_price"]:,.2f}  '
              f'now ${pos.get("mark_spot", pos["spot_entry_price"]):,.2f}')
        print(f'   perp:  short {pos["perp_size"]:.5f}  '
              f'@ ${pos["perp_entry_price"]:,.2f}  '
              f'now ${pos.get("mark_perp", pos["perp_entry_price"]):,.2f}')
        print(f'   basis: {pos.get("current_basis_bps", 0):+.2f} bps')
        funding = pos.get('funding_accrued', 0)
        spot_pnl = pos.get('unrealized_spot_pnl', 0)
        perp_pnl = pos.get('unrealized_perp_pnl', 0)
        net = funding + spot_pnl + perp_pnl
        print(f'   PnL:   funding=${funding:+.4f}  spot_mtm=${spot_pnl:+.4f}  '
              f'perp_mtm=${perp_pnl:+.4f}  net=${net:+.4f}')
        total_funding += funding; total_spot_pnl += spot_pnl; total_perp_pnl += perp_pnl
    total_net = total_funding + total_spot_pnl + total_perp_pnl
    if positions:
        print(f'\\n PORTFOLIO NET: ${total_net:+.4f}'
              f'   (funding ${total_funding:+.4f}  spot_mtm ${total_spot_pnl:+.4f}  perp_mtm ${total_perp_pnl:+.4f})')
        if starting_capital > 0 and started:
            days_open = max((datetime.now(timezone.utc) - pd.Timestamp(started).tz_convert("UTC")).total_seconds() / 86400, 0.01)
            ann_pct = total_net / starting_capital * 365 / days_open * 100
            print(f' Annualized:    {ann_pct:+.2f}% on $5K capital  (linear extrapolation, {days_open:.1f} days)')

    # Show last 5 paper log events
    log_path = Path(CFG['PAPER_LOG_PATH'])
    if log_path.exists():
        lines = log_path.read_text().strip().splitlines()[-5:]
        if lines:
            print(f'\\n Recent log events:')
            for line in lines:
                e = json.loads(line)
                ts = e.get("ts", "")[:19]
                ev = e.get('event', '?')
                coin = e.get('coin', '')
                detail = ''
                if ev == 'close':  detail = f"realized=${e.get('realized_pnl', 0):+.4f} reason={e.get('reason','')}"
                elif ev == 'open': detail = f"spot=${e.get('spot_price', 0):.2f} perp=${e.get('perp_price', 0):.2f}"
                print(f'   [{ts}] {ev:6s} {coin:5s} {detail}')

dashboard()
'''),

    ('md', '''## §9 — Historical quick-look

Pull last N days of funding rates and show what the strategy WOULD have
earned. This is a 1-week sanity check before deploying paper.
'''),

    ('code', '''def historical_quicklook(days=7):
    """How much funding would each coin have paid over the last N days?"""
    print(f'\\n══ Last {days} days hypothetical income on $1,250 notional/coin ══')
    print(f'{"coin":<6} {"events":>7} {"mean_bp/hr":>11} {"sum_pct":>9} {"$_income":>10}')
    for c in CFG['COINS']:
        h = fetch_funding_history(c, hours_back=days*24)
        if h.empty: continue
        mean_bphr = h['rate'].mean() * 10000
        sum_pct = h['rate'].sum() * 100
        income_usd = h['rate'].sum() * CFG['SPOT_USD_PER_COIN']
        print(f'{c:<6} {len(h):>7} {mean_bphr:>10.3f}  {sum_pct:>+7.4f}%  ${income_usd:>+8.4f}')

    # Same but annualized
    print(f'\\nAnnualized (extrapolating last {days}d):')
    total_per_yr = 0.0
    for c in CFG['COINS']:
        h = fetch_funding_history(c, hours_back=days*24)
        if h.empty: continue
        per_hour_rate = h['rate'].mean()
        annual_pct_on_notional = per_hour_rate * 24 * 365 * 100
        annual_usd = per_hour_rate * 24 * 365 * CFG['SPOT_USD_PER_COIN']
        total_per_yr += annual_usd
        print(f'  {c}:  {annual_pct_on_notional:+.2f}%/yr on notional  →  ${annual_usd:+.0f}/yr at $1,250 notional')
    print(f'\\n Total funding income at $5K capital (2 coins, $1,250 notional each):')
    print(f'   ${total_per_yr:.2f}/yr from funding')
    print(f'   ${0.045 * 2_500:.2f}/yr from idle USDC @ 4.5% (T-bill proxy)')
    print(f'   ─────────────────────')
    print(f'   ${total_per_yr + 0.045 * 2_500:.2f}/yr combined  ({(total_per_yr + 112.5)/5000*100:.2f}% on $5K capital)')

historical_quicklook(days=7)
'''),

    ('md', '''## §10 — Going live (when paper has 2+ weeks of positive PnL)

DO NOT run the cells below until paper trading has confirmed the strategy
is working. When ready:

1. Set `CFG['MODE'] = 'live'`
2. Restart the kernel (so `_cb` re-initializes with auth keys)
3. Run `live_open_all()` — this will place REAL orders
'''),

    ('code', '''def _live_open_coinbase_spot(coin):
    """Place a market buy on Coinbase for SPOT_USD_PER_COIN of {coin}."""
    if CFG['MODE'] != 'live':
        print(f'  [DRY] Coinbase BUY {coin}: ${CFG["SPOT_USD_PER_COIN"]}'); return None
    if not (CRED.get('COINBASE_API_KEY') and CRED.get('COINBASE_API_SECRET')):
        print('  ! Missing COINBASE_API_KEY/SECRET — cannot go live'); return None
    cb = ccxt.coinbase({
        'apiKey': CRED['COINBASE_API_KEY'],
        'secret': CRED['COINBASE_API_SECRET'].replace('\\\\n', '\\n'),
        'enableRateLimit': True,
    })
    order = cb.create_market_buy_order_with_cost(f'{coin}/USD', CFG['SPOT_USD_PER_COIN'])
    print(f'  LIVE Coinbase BUY {coin}: order id {order.get("id")}'); return order

def _live_open_hyperliquid_short(coin):
    """Short PERP_USD_PER_COIN of {coin}-PERP on Hyperliquid."""
    if CFG['MODE'] != 'live':
        print(f'  [DRY] Hyperliquid SHORT {coin}: ${CFG["PERP_USD_PER_COIN"]}'); return None
    if not (CRED.get('HYPERLIQUID_API_PRIVATE_KEY') and CRED.get('HYPERLIQUID_VAULT_ADDRESS')):
        print('  ! Missing Hyperliquid creds'); return None
    # TODO: implement using hyperliquid-python-sdk. Install:
    #   pip install hyperliquid-python-sdk
    print('  [STUB] Hyperliquid SDK integration goes here.')
    return None

def live_open_all():
    """Open both legs on both coins, LIVE money."""
    if CFG['MODE'] != 'live':
        print('Not in live mode. Set CFG[\\'MODE\\'] = \\'live\\' first.'); return
    for c in CFG['COINS']:
        _live_open_coinbase_spot(c)
        _live_open_hyperliquid_short(c)
        # Record in state same as paper
        paper_open(c)  # for state tracking purposes

print('Live execution stubs loaded (dry by default).')
print('Live mode requires both Coinbase + Hyperliquid creds in credentials.env')
'''),

    ('md', '''## §11 — Cron deployment template

Once paper trading works, automate via cron. Create `funding_arb/cron_run.py`:

```python
#!/usr/bin/env python3
"""Cron entrypoint: pulls latest funding, ticks state, alerts if needed."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
# Re-execute the notebook code blocks as a module here, or split into
# importable modules. Simplest:
from notebook_runtime import daily_run, dashboard
daily_run()  # accrue funding, check triggers, auto-close if needed
```

Then in your crontab:
```
# Run every 15 minutes
*/15 * * * * /usr/bin/python3 /path/to/edge-bot/funding_arb/cron_run.py >> /var/log/funding_arb.log 2>&1
```

Phone alerts (set `PUSHOVER_USER`, `PUSHOVER_TOKEN` in credentials):
```python
def push(msg):
    requests.post('https://api.pushover.net/1/messages.json', data={
        'token': CRED['PUSHOVER_TOKEN'], 'user': CRED['PUSHOVER_USER'],
        'message': msg,
    })
push(f'Funding arb: closed {coin} for {reason}')
```
'''),
]

def build():
    cells = []
    for kind, src in CELLS:
        if kind == 'md':
            cells.append({'cell_type': 'markdown', 'metadata': {},
                          'source': src.splitlines(keepends=True)})
        else:
            cells.append({'cell_type': 'code', 'metadata': {},
                          'execution_count': None, 'outputs': [],
                          'source': src.splitlines(keepends=True)})
    nb = {'cells': cells, 'metadata': {
        'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
        'language_info': {'name': 'python', 'version': '3.11'}},
        'nbformat': 4, 'nbformat_minor': 5}
    out = Path('funding_arb/funding_arb_live.ipynb')
    out.write_text(json.dumps(nb, indent=1))
    print(f'wrote {out}  ({out.stat().st_size:,} bytes, {len(cells)} cells)')

if __name__ == '__main__':
    build()
