"""Live funding-rate feeds for HL + OKX."""
import json
import subprocess
import time


def hl_funding_snapshot():
    """Returns dict: {coin: {mark, funding_hr, oi_usd}}."""
    cmd = ['curl', '-s', '-X', 'POST', 'https://api.hyperliquid.xyz/info',
           '-H', 'Content-Type: application/json',
           '-d', '{"type":"metaAndAssetCtxs"}']
    raw = subprocess.check_output(cmd, timeout=10).decode()
    m = json.loads(raw)
    uni, ctxs = m[0]['universe'], m[1]
    out = {}
    for i, u in enumerate(uni):
        c = ctxs[i]
        try:
            mark = float(c.get('markPx', 0))
            out[u['name']] = {
                'mark': mark,
                'funding_hr': float(c.get('funding', 0)),
                'premium': float(c.get('premium', 0)),
                'oi_usd': float(c.get('openInterest', 0)) * mark,
            }
        except Exception:
            pass
    return out


def okx_funding_snapshot(coins):
    """Returns dict: {coin: {funding_8h, ts_ms, mark, instId}}."""
    out = {}
    for c in coins:
        inst = f'{c}-USDT-SWAP'
        try:
            # Current realized funding
            r = subprocess.check_output(
                ['curl', '-s', f'https://www.okx.com/api/v5/public/funding-rate?instId={inst}'],
                timeout=10).decode()
            d = json.loads(r)
            if d.get('code') == '0' and d.get('data'):
                row = d['data'][0]
                # Also get mark via mark-price endpoint
                mr = json.loads(subprocess.check_output(
                    ['curl', '-s', f'https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId={inst}'],
                    timeout=10).decode())
                mark = float(mr['data'][0]['markPx']) if mr.get('data') else 0
                nfr = row.get('nextFundingRate') or '0'
                nft = row.get('nextFundingTime') or '0'
                out[c] = {
                    'funding_8h': float(row['fundingRate']),
                    'next_funding_8h': float(nfr) if nfr else 0.0,
                    'next_funding_time': int(nft) if nft else 0,
                    'mark': mark,
                    'instId': inst,
                }
        except Exception as e:
            import sys
            print(f'[okx_feed] {c}: {type(e).__name__}: {e}', file=sys.stderr)
    return out


def aligned_spread(coins):
    """Returns dict: {coin: {hl_apr, okx_apr, spread_apr, hl_mark, okx_mark}}."""
    hl = hl_funding_snapshot()
    ok = okx_funding_snapshot(coins)
    out = {}
    for c in coins:
        if c not in hl or c not in ok:
            continue
        # Convert all rates to APR (%)
        hl_apr = hl[c]['funding_hr'] * 24 * 365 * 100
        okx_apr = ok[c]['funding_8h'] / 8 * 24 * 365 * 100  # 8h rate → hourly equiv
        out[c] = {
            'hl_apr': hl_apr,
            'okx_apr': okx_apr,
            'spread_apr': hl_apr - okx_apr,
            'hl_mark': hl[c]['mark'],
            'okx_mark': ok[c]['mark'],
            'hl_oi_usd': hl[c]['oi_usd'],
            'okx_next_funding_time': ok[c].get('next_funding_time', 0),
        }
    return out


if __name__ == '__main__':
    from config import UNIVERSE
    coins = list(UNIVERSE.keys())
    snap = aligned_spread(coins)
    print(f"{'coin':6s} {'HL_APR':>8s} {'OKX_APR':>8s} {'spread':>8s} {'sign':>5s} {'HL_OI':>10s}")
    print('-' * 55)
    for c in coins:
        if c not in snap: continue
        s = snap[c]
        sig = UNIVERSE[c]['sign']
        print(f"{c:6s} {s['hl_apr']:>+7.2f}% {s['okx_apr']:>+7.2f}% {s['spread_apr']:>+7.2f}% "
              f"{sig:>+5d} {s['hl_oi_usd']/1e6:>8.1f}M")
