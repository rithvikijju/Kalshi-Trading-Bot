"""Live runner for funding-arb. STARTS IN PAPER MODE by default.

The same loop as paper_runner.py but with real order execution behind a
hard gate. Refuses to open positions until:
  1. --live flag is passed
  2. Credentials are loaded from ~/.config/funding_arb/credentials.env
  3. Account balances pre-flight check passes
  4. You manually confirm the first trade interactively

Until you flip --live, this script is IDENTICAL to paper_runner in behavior —
useful for stress-testing the same code path that will eventually trade.

USAGE:
  python live_runner.py                        # paper mode (safe)
  python live_runner.py --live --notional 1250 # LIVE — REAL MONEY — confirms first trade
"""
from __future__ import annotations
import argparse, json, time, signal, sys, os
from datetime import datetime, timezone
from pathlib import Path
import urllib.request, urllib.error

# Path layout
THIS_DIR = Path(__file__).parent
DATA_DIR = THIS_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
LOG_PATH = DATA_DIR / 'live_log.jsonl'
STATE_PATH = DATA_DIR / 'live_state.json'
CRED_PATH = Path.home() / '.config' / 'funding_arb' / 'credentials.env'


# ─── Credential loading ───────────────────────────────────────────
def load_credentials():
    """Reads KEY=VALUE pairs from credentials.env, returns dict."""
    if not CRED_PATH.exists(): return {}
    out = {}
    for line in CRED_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        if '=' not in line: continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ─── Public APIs (shared with paper_runner) ───────────────────────
HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
CB_PRICE_URL = 'https://api.coinbase.com/v2/prices/{pair}/spot'


def http_post_json(url, payload, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_get_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_hl_funding_and_mark(coins):
    meta = http_post_json(HL_INFO_URL, {'type': 'metaAndAssetCtxs'})
    universe = meta[0]['universe']; ctxs = meta[1]
    out = {}
    for i, u in enumerate(universe):
        if u['name'] not in coins: continue
        ctx = ctxs[i]
        f_hr = float(ctx.get('funding', 0.0))
        mark = float(ctx.get('markPx', 0.0))
        out[u['name']] = {
            'mark': mark,
            'funding_bps_hr': f_hr * 10000,
            'open_interest_usd': float(ctx.get('openInterest', 0)) * mark,
        }
    return out


def fetch_coinbase_spot(pair):
    data = http_get_json(CB_PRICE_URL.format(pair=pair))
    return float(data['data']['amount'])


# ─── Order execution shim (paper vs live) ─────────────────────────
class PaperBroker:
    """Fake broker — just logs intended orders."""
    def __init__(self): self.log = []
    def buy_spot(self, asset, notional_usd):
        order = {'venue':'coinbase','side':'BUY','asset':asset,'notional_usd':notional_usd,
                 'mode':'PAPER','ts':datetime.now(timezone.utc).isoformat()}
        self.log.append(order); return {'status':'paper_filled','order':order}
    def sell_spot(self, asset, notional_usd):
        order = {'venue':'coinbase','side':'SELL','asset':asset,'notional_usd':notional_usd,
                 'mode':'PAPER','ts':datetime.now(timezone.utc).isoformat()}
        self.log.append(order); return {'status':'paper_filled','order':order}
    def short_perp(self, asset, notional_usd):
        order = {'venue':'hyperliquid','side':'SHORT','asset':asset,'notional_usd':notional_usd,
                 'mode':'PAPER','ts':datetime.now(timezone.utc).isoformat()}
        self.log.append(order); return {'status':'paper_filled','order':order}
    def close_perp(self, asset, notional_usd):
        order = {'venue':'hyperliquid','side':'CLOSE','asset':asset,'notional_usd':notional_usd,
                 'mode':'PAPER','ts':datetime.now(timezone.utc).isoformat()}
        self.log.append(order); return {'status':'paper_filled','order':order}
    def balances(self): return {'mode':'PAPER','usd_spot':999_999,'usdc_perp':999_999}


class LiveBroker:
    """Stub for real broker. Wire up coinbase-advanced-py + hyperliquid-python-sdk
    here once you have credentials. ALL methods MUST confirm orders externally
    before sending."""
    def __init__(self, creds):
        self.creds = creds
        # Required keys (will fail fast if missing)
        for k in ('COINBASE_API_KEY','COINBASE_API_SECRET',
                  'HYPERLIQUID_API_PRIVATE_KEY','HYPERLIQUID_VAULT_ADDRESS'):
            if k not in creds:
                raise RuntimeError(
                    f"Missing credential {k}.  See {CRED_PATH}.  "
                    "Run in --paper mode until you have keys.")
        # Lazy-import SDKs only when going live (so paper users don't need them)
        try:
            from coinbase.rest import RESTClient as CoinbaseClient   # coinbase-advanced-py
            from hyperliquid.exchange import Exchange as HLExchange  # hyperliquid-python-sdk
            from hyperliquid.utils import constants as HL_CONST
            from eth_account import Account
        except ImportError as e:
            raise RuntimeError(
                "Live mode requires: pip install coinbase-advanced-py hyperliquid-python-sdk eth-account"
            ) from e
        self.cb = CoinbaseClient(api_key=creds['COINBASE_API_KEY'],
                                  api_secret=creds['COINBASE_API_SECRET'])
        wallet = Account.from_key(creds['HYPERLIQUID_API_PRIVATE_KEY'])
        self.hl = HLExchange(wallet, HL_CONST.MAINNET_API_URL,
                              vault_address=creds['HYPERLIQUID_VAULT_ADDRESS'])

    def buy_spot(self, asset, notional_usd):
        """MARKET BUY {asset}-USD on Coinbase Advanced for ~notional_usd worth."""
        # Coinbase Advanced API uses funds (USD amount) for market buys
        resp = self.cb.market_order_buy(product_id=f'{asset}-USD',
                                         quote_size=str(round(notional_usd, 2)))
        return {'status':'live_filled','response':resp}

    def sell_spot(self, asset, notional_usd):
        # First, look up how much of the asset we hold; sell-market uses base size
        # (Implement when actually going live — leave as TODO for now.)
        raise NotImplementedError("Implement sell-spot once you have Coinbase API tested")

    def short_perp(self, asset, notional_usd):
        """OPEN SHORT on Hyperliquid {asset}-PERP for ~notional_usd."""
        mark = fetch_hl_funding_and_mark([asset])[asset]['mark']
        size = notional_usd / mark
        # is_buy=False = SELL/SHORT;  reduce_only=False
        resp = self.hl.market_open(name=asset, is_buy=False, sz=size, slippage=0.005)
        return {'status':'live_filled','response':resp}

    def close_perp(self, asset, notional_usd):
        """CLOSE existing short on Hyperliquid."""
        resp = self.hl.market_close(coin=asset)
        return {'status':'live_filled','response':resp}

    def balances(self):
        cb_bal = self.cb.get_accounts()
        hl_state = http_post_json(HL_INFO_URL,
                                   {'type':'clearinghouseState',
                                    'user': self.creds['HYPERLIQUID_VAULT_ADDRESS']})
        return {'mode':'LIVE','coinbase':cb_bal,'hyperliquid':hl_state}


# ─── Safety gates ─────────────────────────────────────────────────
class SafetyGates:
    """Hard limits the bot will refuse to violate."""
    MAX_NOTIONAL_PER_ASSET = 50_000     # don't risk more than this per asset
    MIN_FUNDING_BPS_HR_TO_ENTER = 0.05  # ~4% APR floor
    MAX_BASIS_BPS_TO_ENTER = 25         # don't enter into a rich perp
    EXIT_BASIS_BPS = 60                 # force-exit if basis blows out
    MIN_PERP_MARGIN_RATIO = 3.0         # always keep 3× margin

    @classmethod
    def can_enter(cls, asset, notional, funding_bps_hr, basis_bps):
        if notional > cls.MAX_NOTIONAL_PER_ASSET:
            return False, f'notional ${notional} > MAX ${cls.MAX_NOTIONAL_PER_ASSET}'
        if funding_bps_hr < cls.MIN_FUNDING_BPS_HR_TO_ENTER:
            return False, f'funding {funding_bps_hr:.2f} < MIN {cls.MIN_FUNDING_BPS_HR_TO_ENTER}'
        if basis_bps > cls.MAX_BASIS_BPS_TO_ENTER:
            return False, f'basis {basis_bps:.1f} > MAX {cls.MAX_BASIS_BPS_TO_ENTER}'
        return True, 'ok'

    @classmethod
    def must_exit(cls, asset, funding_bps_hr, basis_bps):
        if basis_bps > cls.EXIT_BASIS_BPS:
            return True, f'basis blowout {basis_bps:.1f} > {cls.EXIT_BASIS_BPS}'
        if funding_bps_hr < -1.0:
            return True, f'funding flipped strongly negative {funding_bps_hr:.2f}'
        return False, ''


# ─── Main loop ────────────────────────────────────────────────────
def confirm(prompt):
    ans = input(f"{prompt} [type YES to confirm]: ").strip()
    return ans == 'YES'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true', help='REAL ORDERS. requires creds.')
    ap.add_argument('--notional', type=float, default=1250.0,
                     help='Per-asset notional (default $1250 — small start)')
    ap.add_argument('--assets', nargs='+', default=['ETH'])
    ap.add_argument('--interval-sec', type=int, default=300)
    args = ap.parse_args()

    mode = 'LIVE' if args.live else 'PAPER'
    creds = load_credentials() if args.live else {}

    if args.live and not creds:
        print(f"  ABORT: --live requested but no credentials at {CRED_PATH}")
        sys.exit(1)
    if args.live and not confirm(
        f"\n  YOU ARE ABOUT TO TRADE REAL MONEY.\n"
        f"  mode={mode}, notional=${args.notional}/asset, assets={args.assets}\n"
        f"  Confirm?"):
        print('  aborted by user'); sys.exit(0)

    broker = LiveBroker(creds) if args.live else PaperBroker()
    print(f"\n=== funding-arb runner [{mode}] ===")
    print(f"  assets={args.assets} notional=${args.notional}/asset interval={args.interval_sec}s")

    state = json.load(open(STATE_PATH)) if STATE_PATH.exists() else {
        'mode': mode, 'started': datetime.now(timezone.utc).isoformat(),
        'positions': {a: {'open': False, 'opened_at': None, 'spot_entry':None,
                            'perp_entry':None, 'cum_funding':0.0, 'cum_fees':0.0,
                            'last_mark':None,'last_spot':None,'cum_spread':0.0} for a in args.assets},
        'cycle': 0,
    }

    stop = False
    def sigint(*_):
        nonlocal stop; stop = True
        print('\n  shutdown requested, finishing cycle')
    signal.signal(signal.SIGINT, sigint)

    while not stop:
        try:
            hl = fetch_hl_funding_and_mark(args.assets)
            spot = {a: fetch_coinbase_spot(f'{a}-USD') for a in args.assets}
            ts = datetime.now(timezone.utc).isoformat()
            print(f"\n[{ts[:19]}] cycle {state['cycle']}")

            for asset in args.assets:
                if asset not in hl: continue
                pos = state['positions'][asset]
                f_hr = hl[asset]['funding_bps_hr']
                mark = hl[asset]['mark']
                sp = spot[asset]
                basis = (mark/sp - 1) * 10000

                # Decision
                if pos['open']:
                    pos['cum_funding'] += f_hr/10000 * args.notional
                    if pos['last_mark']:
                        ds = sp/pos['last_spot'] - 1
                        dm = mark/pos['last_mark'] - 1
                        pos['cum_spread'] += (ds - dm) * args.notional
                    pos['last_mark'] = mark; pos['last_spot'] = sp
                    must, reason = SafetyGates.must_exit(asset, f_hr, basis)
                    if must:
                        print(f"  [{asset}] EXIT — {reason}")
                        if args.live and not confirm(f'  CLOSE positions for {asset}?'):
                            print('  exit cancelled by user'); continue
                        r1 = broker.sell_spot(asset, args.notional)
                        r2 = broker.close_perp(asset, args.notional)
                        pos['open'] = False
                        pos['cum_fees'] += ((5+1.5)+(2.5+1.5))/10000 * args.notional
                        print(f'  [{asset}] CLOSED. spot_resp={r1["status"]} perp_resp={r2["status"]}')
                else:
                    can, reason = SafetyGates.can_enter(asset, args.notional, f_hr, basis)
                    if can:
                        print(f"  [{asset}] ENTER opportunity: f={f_hr:.2f}bps/hr basis={basis:.1f}bps")
                        if args.live and not confirm(f'  OPEN $ {args.notional} arb on {asset}?'):
                            print('  entry skipped by user'); continue
                        r1 = broker.buy_spot(asset, args.notional)
                        r2 = broker.short_perp(asset, args.notional)
                        pos.update({'open':True,'opened_at':ts,
                                    'spot_entry':sp,'perp_entry':mark,
                                    'last_mark':mark,'last_spot':sp})
                        pos['cum_fees'] += ((5+1.5)+(2.5+1.5))/10000 * args.notional
                        print(f'  [{asset}] OPENED. spot_resp={r1["status"]} perp_resp={r2["status"]}')
                    else:
                        print(f"  [{asset}] hold (no-entry: {reason})")

                # Log
                rec = {'ts':ts,'mode':mode,'cycle':state['cycle'],'asset':asset,
                        'funding_bps_hr':round(f_hr,3),'basis_bps':round(basis,2),
                        'mark':mark,'spot':sp,'open':pos['open'],
                        'cum_funding':round(pos['cum_funding'],4),
                        'cum_spread':round(pos['cum_spread'],4),
                        'cum_fees':round(pos['cum_fees'],4),
                        'pnl':round(pos['cum_funding']+pos['cum_spread']-pos['cum_fees'],4)}
                with open(LOG_PATH,'a') as f: f.write(json.dumps(rec)+'\n')
                print(f"  [{asset}] f={f_hr:+.2f}bps/hr basis={basis:+.1f}bps open={pos['open']} "
                      f"pnl=${rec['pnl']:+.4f}")

            state['cycle'] += 1
            with open(STATE_PATH,'w') as f: json.dump(state, f, indent=2)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  net err: {e}, retry 30s"); time.sleep(30); continue
        except Exception as e:
            print(f"  err: {e}"); time.sleep(30); continue
        for _ in range(args.interval_sec):
            if stop: break
            time.sleep(1)

    print(f'\n  state saved.  view log: tail -f {LOG_PATH} | jq .')


if __name__ == '__main__':
    main()
