"""KXBTCY (year-end BTC bucket) risk-neutral distribution analysis.

Strategy concept:
  - 28 mutually-exclusive bucket markets cover BTC's Jan-1-2027 close.
  - Sum of true probabilities MUST = 1.
  - We fit a smooth implied distribution (lognormal + tail adjustments) to the
    market's bid/ask midpoints.
  - Buckets whose mid deviates >threshold from the smooth fit are mispriced.
  - Trade them: BUY underpriced YES, SELL overpriced YES (= BUY NO).

This is a real, scalable strategy because:
  - Per-market OI is $100K-$250K (vs $0-770 on hourly KXBTCD).
  - Total chain OI is ~$3.7M.
  - We can deploy $50K+ at this scale without moving the book.
  - Hold-to-settlement is ~7 months from now (long-duration, low turnover).
"""
from __future__ import annotations
import math, sys
sys.path.insert(0, '/Users/rithvikijju/edge-bot/kalshi_k6')
from config import KalshiClient

import numpy as np


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def pull_kxbtcy():
    c = KalshiClient()
    ev = c.get_events(series_ticker='KXBTCY', status='open', limit=5)
    evt = ev['events'][0]['event_ticker']
    mk = c.get_markets(event_ticker=evt, status='open', limit=200)
    rows = []
    for m in mk.get('markets', []):
        sk_lo = m.get('floor_strike') or 0
        # Bucket markets are named B22500 etc; thresholds are T20000.00 etc
        # Bucket = [floor_strike, floor_strike + 5000]
        ticker = m['ticker']
        is_threshold = '-T' in ticker
        sk_hi = float(sk_lo) + 5000 if not is_threshold else None
        if is_threshold:
            # Two tail markets: ≤$20K and ≥$150K
            if 'T20000' in ticker:
                sk_lo, sk_hi = 0, 20000
                btype = 'lower_tail'
            else:  # T149999.99
                sk_lo, sk_hi = 150000, 1e7
                btype = 'upper_tail'
        else:
            btype = 'bucket'
        rows.append({
            'ticker': ticker, 'type': btype,
            'strike_lo': float(sk_lo), 'strike_hi': float(sk_hi),
            'yes_bid': float(m.get('yes_bid_dollars') or 0),
            'yes_ask': float(m.get('yes_ask_dollars') or 0),
            'no_bid': float(m.get('no_bid_dollars') or 0),
            'no_ask': float(m.get('no_ask_dollars') or 0),
            'oi': float(m.get('open_interest_fp') or 0),
            'v24': float(m.get('volume_24h_fp') or 0),
        })
    rows.sort(key=lambda r: r['strike_lo'])
    return evt, rows


def fit_lognormal_to_chain(rows, spot, T_years):
    """Find (mu, sigma) of a lognormal distribution that best matches the
    market-implied bucket probabilities."""
    mids = []
    bucket_centers = []
    for r in rows:
        if r['type'] != 'bucket':
            continue
        mid = (r['yes_bid'] + r['yes_ask']) / 2.0
        if mid <= 0 or mid >= 1:
            continue
        center = (r['strike_lo'] + r['strike_hi']) / 2.0
        mids.append(mid)
        bucket_centers.append(center)
    if not mids:
        return None
    # Normalize the bucket probabilities so they sum to (1 - tail mass)
    # For now, fit lognormal directly. Lognormal CDF: Φ((ln(K) - mu)/sigma)
    # P(K_lo < S_T < K_hi) = Φ((ln(K_hi)-mu)/sigma) - Φ((ln(K_lo)-mu)/sigma)
    from scipy.stats import norm

    def implied_p(K_lo, K_hi, mu, sigma):
        d_hi = (math.log(K_hi) - mu) / sigma if K_hi > 0 else -1e9
        d_lo = (math.log(K_lo) - mu) / sigma if K_lo > 0 else -1e9
        return norm.cdf(d_hi) - norm.cdf(d_lo)

    def loss(params):
        mu, log_sigma = params
        sigma = math.exp(log_sigma)
        sse = 0
        for r in rows:
            mid = (r['yes_bid'] + r['yes_ask']) / 2.0
            if mid <= 0 or mid >= 1: continue
            p_model = implied_p(r['strike_lo'], r['strike_hi'], mu, sigma)
            sse += (p_model - mid) ** 2
        return sse

    from scipy.optimize import minimize
    # Initial guess: BS-style spot drift to expiration
    # mu = ln(spot) + (r - σ²/2)·T; sigma_T = σ·√T
    initial_mu = math.log(spot)
    initial_sigma = 0.55 * math.sqrt(T_years)
    res = minimize(loss, [initial_mu, math.log(initial_sigma)], method='Nelder-Mead')
    mu, log_sigma = res.x
    sigma = math.exp(log_sigma)
    return mu, sigma


def analyze():
    print('=' * 100)
    print('KXBTCY Risk-Neutral Distribution Analysis')
    print('=' * 100)

    evt, rows = pull_kxbtcy()
    print(f'Event: {evt}')
    print(f'Total markets: {len(rows)}')

    # Days to expiration
    from datetime import datetime, timezone
    expiry = datetime(2027, 1, 1, 5, 0, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days_to_exp = (expiry - now).total_seconds() / 86400
    T_years = days_to_exp / 365.25
    print(f'Days to expiration: {days_to_exp:.0f}  (T = {T_years:.3f} years)')

    # Current BTC spot (use Coinbase)
    sys.path.insert(0, '/Users/rithvikijju/edge-bot/kalshi_k6')
    from config import _coinbase_spot
    spot = _coinbase_spot('BTC-USD')
    print(f'BTC spot now: ${spot:.0f}')
    print()

    # Sum check
    sum_yes_ask = sum(r['yes_ask'] for r in rows if r['yes_ask'] > 0)
    sum_yes_bid = sum(r['yes_bid'] for r in rows if r['yes_bid'] > 0)
    sum_mid = sum((r['yes_bid'] + r['yes_ask']) / 2 for r in rows
                   if r['yes_bid'] > 0 and r['yes_ask'] > 0)
    print(f'Sum of yes_ask: {sum_yes_ask:.4f}   (must be ≥ 1 for no arb)')
    print(f'Sum of yes_bid: {sum_yes_bid:.4f}   (must be ≤ 1 for no arb)')
    print(f'Sum of mid:     {sum_mid:.4f}   (closer to 1 = tighter market)')
    print()

    # Fit lognormal
    fit = fit_lognormal_to_chain(rows, spot, T_years)
    if fit is None:
        print('Lognormal fit failed (insufficient bucket data)')
        return
    mu, sigma = fit
    implied_vol_annual = sigma / math.sqrt(T_years)
    print(f'Lognormal fit: μ = {mu:.4f}, σ = {sigma:.4f}')
    print(f'Annualized implied vol from chain fit: {implied_vol_annual*100:.1f}%')
    print(f'  (for reference: BTC historical 1-yr vol ≈ 60-70%)')
    print()

    # Identify mispricings
    from scipy.stats import norm
    def implied_p(K_lo, K_hi):
        if K_hi <= 0 or K_lo < 0: return 0
        d_hi = (math.log(K_hi) - mu) / sigma if K_hi > 0 else -1e9
        d_lo = (math.log(max(K_lo, 1)) - mu) / sigma
        return max(0, norm.cdf(d_hi) - norm.cdf(d_lo))

    print(f'{"ticker":<35} {"strike range":<22} {"mid":<7} {"ask":<7} {"model":<7} {"gap":<8} {"edge after fee":<15} {"OI":<10}')
    print('-' * 115)
    tradeable_buy = []
    tradeable_sell = []
    for r in rows:
        mid = (r['yes_bid'] + r['yes_ask']) / 2
        if r['type'] == 'lower_tail':
            # ≤ $20K bucket
            d = (math.log(20000) - mu) / sigma
            model_p = norm.cdf(d)
            rng = f'≤${20000}'
        elif r['type'] == 'upper_tail':
            d = (math.log(150000) - mu) / sigma
            model_p = 1 - norm.cdf(d)
            rng = f'≥${150000}'
        else:
            model_p = implied_p(r['strike_lo'], r['strike_hi'])
            rng = f'${r["strike_lo"]:.0f}-${r["strike_hi"]:.0f}'
        gap = model_p - mid

        # Tradeable edge: model says higher than ask → BUY YES; model lower than bid → SELL YES
        ask_fee = kalshi_fee(r['yes_ask'])
        bid_fee = kalshi_fee(r['yes_bid']) if r['yes_bid'] > 0 else 0
        buy_edge = model_p - r['yes_ask'] - ask_fee
        sell_edge = r['yes_bid'] - model_p - bid_fee
        best_edge = max(buy_edge, sell_edge)
        side = 'BUY YES' if buy_edge > sell_edge else 'SELL YES'

        flag = '★' if best_edge > 0.01 else ''
        print(f'  {r["ticker"]:<33} {rng:<22} {mid:.4f}  {r["yes_ask"]:.4f}  {model_p:.4f}  {gap:+.4f}  '
              f'{side} {best_edge:+.4f}{flag}  ${r["oi"]:>8.0f}')

        if buy_edge > 0.01:
            tradeable_buy.append((r, buy_edge, model_p))
        elif sell_edge > 0.01:
            tradeable_sell.append((r, sell_edge, model_p))

    print()
    print(f'Tradeable BUY YES (model > ask + fee + 1¢): {len(tradeable_buy)}')
    for r, edge, mp in sorted(tradeable_buy, key=lambda x: -x[1])[:5]:
        print(f'  {r["ticker"]:<33} ask={r["yes_ask"]:.4f}  model={mp:.4f}  edge={edge*100:+.2f}¢  OI=${r["oi"]:.0f}')

    print(f'\nTradeable SELL YES (bid > model + fee + 1¢): {len(tradeable_sell)}')
    for r, edge, mp in sorted(tradeable_sell, key=lambda x: -x[1])[:5]:
        print(f'  {r["ticker"]:<33} bid={r["yes_bid"]:.4f}  model={mp:.4f}  edge={edge*100:+.2f}¢  OI=${r["oi"]:.0f}')


if __name__ == '__main__':
    analyze()
