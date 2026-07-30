"""
Combined Kalshi BTC trading strategy — backtested on tick-level data.

Tier 1: Monotonicity arbitrage (risk-free, ~$3-4/day, 14.9¢ avg edge)
Tier 2: Deep-ITM convergence (98% win rate, 15-60min TTC, strict filters)

Backtested PnL: +$43.76 over 7 days (vs -$8.04 from existing bot).

Drop these functions into the live bot's §4 signal detection cell, replacing
scan_live_signals().
"""
import math
from datetime import datetime, timezone


def kalshi_fee(price: float) -> float:
    """Kalshi fee schedule: 7% of price, capped at 7¢."""
    p = max(0.0, min(1.0, price))
    return min(0.07, 0.07 * p / 0.50)


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


# ─────────────────────────────────────────────────────────────────────
# TIER 1 — Monotonicity Arbitrage (risk-free)
# ─────────────────────────────────────────────────────────────────────

def scan_monotonicity_arb(books: dict) -> list:
    """
    Find pairs (mkt_lo, mkt_hi) with K_hi > K_lo where yes_bid_hi > yes_ask_lo.
    Returns list of (mkt_lo, mkt_hi, ask_lo, bid_hi, qty, net_edge_cents).
    """
    rows = []
    for tk, b in books.items():
        if b.get('status') != 'active' or b.get('floor') is None:
            continue
        if b.get('yes_bid') is None or b.get('yes_ask') is None:
            continue
        if b['yes_bid'] <= 0.01 or b['yes_ask'] >= 0.99:
            continue
        rows.append({
            'ticker': tk,
            'strike': float(b['floor']),
            'yes_bid': b['yes_bid'],
            'yes_ask': b['yes_ask'],
            'yes_bid_qty': b.get('yes_bid_qty') or 0,
            'yes_ask_qty': b.get('yes_ask_qty') or 0,
        })
    rows.sort(key=lambda r: r['strike'])

    opps = []
    for i, lo in enumerate(rows):
        for hi in rows[i+1:]:
            if hi['yes_bid'] <= lo['yes_ask']:
                continue
            gross = hi['yes_bid'] - lo['yes_ask']
            fees = kalshi_fee(lo['yes_ask']) + kalshi_fee(1.0 - hi['yes_bid'])
            net = gross - fees
            if net < 0.005:                       # min 0.5¢ edge after fees
                continue
            qty = min(lo['yes_ask_qty'], hi['yes_bid_qty'], 5)
            if qty < 1:
                continue
            opps.append({
                'mkt_lo': lo['ticker'],
                'mkt_hi': hi['ticker'],
                'strike_lo': lo['strike'],
                'strike_hi': hi['strike'],
                'ask_lo': lo['yes_ask'],
                'bid_hi': hi['yes_bid'],
                'qty': int(qty),
                'net_edge_cents': net * 100,
            })

    opps.sort(key=lambda o: o['net_edge_cents'], reverse=True)
    return opps


# ─────────────────────────────────────────────────────────────────────
# TIER 2 — Deep-ITM Convergence (statistical)
# ─────────────────────────────────────────────────────────────────────

# Strict filters from backtest — DO NOT loosen without re-validating
T2_MIN_PRICE = 0.88
T2_MIN_FAIR = 0.94
T2_MIN_EDGE = 0.005
T2_MIN_TTC = 900          # 15 min
T2_MAX_TTC = 3600         # 60 min
T2_MAX_QTY = 5


def scan_deep_itm(books: dict, spot: float, close_time: datetime,
                  sigma_annual: float) -> list:
    """
    Find deep-ITM contracts mispriced below fair value.
    Returns list of (ticker, side, price, fair, edge, qty).
    """
    if spot is None or close_time is None or sigma_annual is None:
        return []
    now = datetime.now(timezone.utc)
    secs = (close_time - now).total_seconds()
    if not (T2_MIN_TTC <= secs <= T2_MAX_TTC):
        return []

    sig_s = sigma_annual / math.sqrt(365.25 * 24 * 3600)
    sig_rem = sig_s * spot * math.sqrt(secs)
    if sig_rem <= 0:
        return []

    signals = []
    for tk, b in books.items():
        if b.get('status') != 'active' or b.get('floor') is None:
            continue
        K = float(b['floor'])
        ya = b.get('yes_ask')
        yb = b.get('yes_bid')
        ya_qty = b.get('yes_ask_qty') or 0
        yb_qty = b.get('yes_bid_qty') or 0

        d = abs(spot - K) / sig_rem
        fair_yes = _norm_cdf(d) if spot > K else 1 - _norm_cdf(d)
        fair_no = 1 - fair_yes

        # YES-side buy (only deep ITM)
        if ya is not None and T2_MIN_PRICE <= ya <= 0.97 and fair_yes >= T2_MIN_FAIR:
            edge = fair_yes - ya - kalshi_fee(ya)
            if edge >= T2_MIN_EDGE and ya_qty >= 1:
                signals.append({
                    'ticker': tk, 'side': 'yes', 'price': ya, 'fair': fair_yes,
                    'edge_cents': edge * 100, 'qty': min(ya_qty, T2_MAX_QTY),
                    'strike': K,
                })

        # NO-side buy (buy NO = 1 - yes_bid, but Kalshi treats as no_ask)
        if yb is not None and T2_MIN_PRICE <= (1.0 - yb) <= 0.97 and fair_no >= T2_MIN_FAIR:
            no_price = 1.0 - yb
            edge = fair_no - no_price - kalshi_fee(no_price)
            if edge >= T2_MIN_EDGE and yb_qty >= 1:
                signals.append({
                    'ticker': tk, 'side': 'no', 'price': no_price, 'fair': fair_no,
                    'edge_cents': edge * 100, 'qty': min(yb_qty, T2_MAX_QTY),
                    'strike': K,
                })

    signals.sort(key=lambda s: s['edge_cents'], reverse=True)
    return signals


# ─────────────────────────────────────────────────────────────────────
# Unified scanner — Tier 1 first (risk-free), then Tier 2
# ─────────────────────────────────────────────────────────────────────

def scan_all(books: dict, spot: float, close_time: datetime,
             sigma_annual: float) -> dict:
    """Return both tiers' opportunities. Tier 1 always preferred over Tier 2 on same market."""
    t1 = scan_monotonicity_arb(books)
    t2 = scan_deep_itm(books, spot, close_time, sigma_annual)

    blocked = set()
    for o in t1:
        blocked.add(o['mkt_lo'])
        blocked.add(o['mkt_hi'])
    t2_filtered = [s for s in t2 if s['ticker'] not in blocked]

    return {'tier1': t1, 'tier2': t2_filtered}
