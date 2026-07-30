# § 3 — Signal Detection
#
# Tier 1 — Monotonicity arbitrage (RISK-FREE)
#   Pairs (mkt_lo, mkt_hi) on the same event where strike_hi > strike_lo
#   AND yes_bid_hi > yes_ask_lo + total_fees.
#   Trade: BUY YES(K_lo) at ask_lo, BUY NO(K_hi) at (1 - bid_hi).
#
# Tier 2 — Deep-ITM convergence
#   Black-Scholes binary fair value vs market price, deep-ITM only.
#   Filter: price >= 0.88, fair >= 0.94, edge >= 0.5c, TTC in [15min, 60min].


@dataclass
class T1Signal:
    pair_id: str
    mkt_lo: str
    mkt_hi: str
    strike_lo: float
    strike_hi: float
    ask_lo: float
    bid_hi: float
    qty: int
    net_edge_cents: float


@dataclass
class T2Signal:
    ticker: str
    side: str            # 'yes' or 'no'
    strike: float
    price: float
    fair_value: float
    edge_cents: float
    qty: int
    secs_remaining: float
    sigma: float


@dataclass
class T3Signal:
    """OTM persistence NO buy — buy NO when spot has been < strike for ≥5min."""
    ticker: str
    strike: float
    no_price: float
    yes_bid: float
    qty: int
    secs_remaining: float
    persistence_min: float
    spot_dist: float


def _spot_was_below(strike: float, secs_ago: float) -> Optional[bool]:
    """Was spot < strike `secs_ago` seconds ago? None if no history."""
    target = datetime.now(timezone.utc) - timedelta(seconds=secs_ago)
    with _LOCK:
        hist = list(SPOT['history'])
    if not hist:
        return None
    for ts, price in reversed(hist):
        if ts <= target:
            return price < strike
    return None  # not enough history


def _spot_move_over(secs: float) -> Optional[float]:
    """|spot(now) - spot(now - secs)|. None if not enough history."""
    target = datetime.now(timezone.utc) - timedelta(seconds=secs)
    with _LOCK:
        hist = list(SPOT['history'])
        now_price = SPOT.get('price')
    if not hist or now_price is None:
        return None
    for ts, price in reversed(hist):
        if ts <= target:
            return abs(now_price - price)
    return None  # not enough history yet


def scan_t1_monotonicity():
    if not CFG['t1_enabled']:
        return []
    try:
        if not sprt_active(1):
            return []
    except NameError:
        pass
    # V5 calm-regime filter — only trade T1 when spot has been stable.
    # Volatile spot ⇒ HFT activity ⇒ arbs evaporate before we can fill both legs.
    if CFG.get('t1_calm_filter_enabled', False):
        move30 = _spot_move_over(30.0)
        if move30 is None or move30 > CFG['t1_calm_max_spot_move_30s']:
            return []
    with _LOCK:
        snap = {k: dict(v) for k, v in BOOKS.items()}

    rows = []
    for tk, b in snap.items():
        if b.get('status') != 'active' or b.get('floor') is None:
            continue
        ya = b.get('yes_ask'); yb = b.get('yes_bid')
        if ya is None or yb is None or yb <= 0.01 or ya >= 0.99:
            continue
        ya_qty = b.get('yes_ask_qty') or 0
        yb_qty = b.get('yes_bid_qty') or 0
        rows.append({'tk': tk, 'K': float(b['floor']),
                     'ya': ya, 'yb': yb, 'ya_qty': ya_qty, 'yb_qty': yb_qty})
    rows.sort(key=lambda r: r['K'])

    opps = []
    for i, lo in enumerate(rows):
        for hi in rows[i+1:]:
            if hi['yb'] <= lo['ya']:
                continue
            gross = hi['yb'] - lo['ya']
            fees = kalshi_fee(lo['ya']) + kalshi_fee(1.0 - hi['yb'])
            net = gross - fees
            if net < CFG['t1_min_net_edge_cents'] / 100:
                continue
            qty = min(lo['ya_qty'] or 1, hi['yb_qty'] or 1,
                      CFG['t1_max_qty_per_leg'])
            cost_per_unit = lo['ya'] + (1.0 - hi['yb'])
            if cost_per_unit > 0:
                qty = min(qty, max(1, int(CFG['t1_max_dollars_per_pair'] / cost_per_unit)))
            if qty < 1:
                continue
            opps.append(T1Signal(
                pair_id=f't1-{uuid.uuid4().hex[:10]}',
                mkt_lo=lo['tk'], mkt_hi=hi['tk'],
                strike_lo=lo['K'], strike_hi=hi['K'],
                ask_lo=lo['ya'], bid_hi=hi['yb'],
                qty=int(qty), net_edge_cents=net * 100,
            ))
    opps.sort(key=lambda o: o.net_edge_cents, reverse=True)
    return opps


def scan_t2_deep_itm():
    if not CFG['t2_enabled']:
        return []
    # SPRT auto-disable check (if math models are loaded)
    try:
        if not sprt_active(2):
            return []
    except NameError:
        pass
    with _LOCK:
        spot = SPOT.get('price')
        close_time = TRACKED.get('close_time')
        snap = {k: dict(v) for k, v in BOOKS.items()}
    if spot is None or close_time is None:
        return []
    now = datetime.now(timezone.utc)
    secs = (close_time - now).total_seconds()
    if not (CFG['t2_min_secs_to_close'] <= secs <= CFG['t2_max_secs_to_close']):
        return []

    # Hard minimum distance from strike — defense against the "spot blew through"
    # failure mode where model sigma underestimates a regime shift.
    min_dist = max(CFG['t2_min_dollar_distance_from_strike'],
                   spot * CFG['t2_min_pct_distance_from_strike'])

    max_spread = CFG.get('t2_max_spread_at_entry', 0.05)

    signals = []
    for tk, b in snap.items():
        if b.get('status') != 'active' or b.get('floor') is None:
            continue
        K = float(b['floor'])
        if abs(spot - K) < min_dist:
            continue
        ya = b.get('yes_ask'); yb = b.get('yes_bid')
        ya_qty = b.get('yes_ask_qty') or 1
        yb_qty = b.get('yes_bid_qty') or 1
        if ya is not None and yb is not None and (ya - yb) > max_spread:
            continue

        # Use robust fair value (EWMA σ + drift + jump-diffusion consensus)
        # Falls back to plain Black-Scholes if math models not loaded.
        try:
            fv = fair_value_robust(spot, K, secs)
            if fv is None:
                continue
            fair_yes = fv['fair']
            sigma = fv['sigma_used']
        except NameError:
            sigma = max(_causal_sigma() or 0, CFG['t2_sigma_floor_annual'])
            sigma_c = sigma * (1.0 + CFG['sigma_uncertainty_discount'])
            sig_s = sigma_c / math.sqrt(365.25 * 24 * 3600)
            sig_rem = sig_s * spot * math.sqrt(secs)
            if sig_rem <= 0:
                continue
            d = abs(spot - K) / sig_rem
            fair_yes = _norm_cdf(d) if spot > K else 1.0 - _norm_cdf(d)
        fair_no = 1.0 - fair_yes

        # YES-side buy
        if ya is not None and CFG['t2_min_price'] <= ya <= 0.97 and fair_yes >= CFG['t2_min_fair']:
            edge = fair_yes - ya - kalshi_fee(ya)
            if edge >= CFG['t2_min_edge_cents'] / 100:
                qty = min(ya_qty, CFG['t2_max_qty_per_strike'],
                          max(1, int(CFG['t2_max_dollars_per_trade'] / max(0.01, ya))))
                signals.append(T2Signal(
                    ticker=tk, side='yes', strike=K, price=ya, fair_value=fair_yes,
                    edge_cents=edge * 100, qty=int(qty),
                    secs_remaining=secs, sigma=sigma))

        # NO-side buy
        if yb is not None and CFG['t2_min_price'] <= (1.0 - yb) <= 0.97 and fair_no >= CFG['t2_min_fair']:
            no_price = 1.0 - yb
            edge = fair_no - no_price - kalshi_fee(no_price)
            if edge >= CFG['t2_min_edge_cents'] / 100:
                qty = min(yb_qty, CFG['t2_max_qty_per_strike'],
                          max(1, int(CFG['t2_max_dollars_per_trade'] / max(0.01, no_price))))
                signals.append(T2Signal(
                    ticker=tk, side='no', strike=K, price=no_price, fair_value=fair_no,
                    edge_cents=edge * 100, qty=int(qty),
                    secs_remaining=secs, sigma=sigma))

    signals.sort(key=lambda s: s.edge_cents, reverse=True)
    return signals


def scan_t3_otm_persistence():
    """Tier 3 — buy NO when spot has been < strike for ≥5 min."""
    if not CFG.get('t3_enabled', False):
        return []
    try:
        if not sprt_active(3):
            return []
    except NameError:
        pass
    with _LOCK:
        spot = SPOT.get('price')
        close_time = TRACKED.get('close_time')
        snap = {k: dict(v) for k, v in BOOKS.items()}
    if spot is None or close_time is None:
        return []
    now = datetime.now(timezone.utc)
    secs = (close_time - now).total_seconds()
    if not (CFG['t3_min_secs_to_close'] <= secs <= CFG['t3_max_secs_to_close']):
        return []

    persistence_s = CFG['t3_persistence_seconds']
    # Need at least 5 min of history to verify
    with _LOCK:
        hist = list(SPOT['history'])
    if len(hist) < 30:  # ~60s with 2s polling
        return []

    signals = []
    for tk, b in snap.items():
        if b.get('status') != 'active' or b.get('floor') is None:
            continue
        K = float(b['floor'])
        if spot >= K:
            continue  # need OTM for YES (spot < K)
        dist = K - spot
        if dist < CFG['t3_min_strike_distance']:
            continue
        yb = b.get('yes_bid')
        if yb is None:
            continue
        if not (CFG['t3_min_yes_bid'] <= yb <= CFG['t3_max_yes_bid']):
            continue

        # Persistence check: spot below K for full persistence window
        was_below_300 = _spot_was_below(K, persistence_s)
        was_below_180 = _spot_was_below(K, persistence_s * 0.6)
        was_below_60 = _spot_was_below(K, persistence_s * 0.2)
        if was_below_300 is None or not (was_below_60 and was_below_180 and was_below_300):
            continue

        no_price = 1.0 - yb
        yb_qty = b.get('yes_bid_qty') or 1
        qty = min(yb_qty, CFG['t3_max_qty_per_strike'],
                  max(1, int(CFG['t3_max_dollars_per_trade'] / max(0.01, no_price))))
        signals.append(T3Signal(
            ticker=tk, strike=K, no_price=no_price, yes_bid=yb,
            qty=int(qty), secs_remaining=secs,
            persistence_min=persistence_s / 60.0, spot_dist=dist))

    signals.sort(key=lambda s: s.spot_dist, reverse=True)  # prefer far OTM
    return signals


def scan_all_signals():
    t1 = scan_t1_monotonicity()
    t2 = scan_t2_deep_itm()
    t3 = scan_t3_otm_persistence()
    blocked = set()
    for o in t1:
        blocked.add(o.mkt_lo); blocked.add(o.mkt_hi)
    t2_clean = [s for s in t2 if s.ticker not in blocked]
    t3_clean = [s for s in t3 if s.ticker not in blocked]
    return {'tier1': t1, 'tier2': t2_clean, 'tier3': t3_clean}


print('Signal detection ready (Tier 1 + Tier 2 + Tier 3).')
