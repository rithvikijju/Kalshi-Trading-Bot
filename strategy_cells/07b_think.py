# § 6b — think() — verbose signal explainer
#
# Walks every market in BOOKS and prints WHY each is or isn't a signal.
# Used to debug "the bot isn't trading" — shows you exactly what it sees.


def think(max_rows=15):
    """Detailed dump of what the strategy is seeing right now."""
    with _LOCK:
        spot = SPOT.get('price')
        spot_ts = SPOT.get('ts')
        event = TRACKED.get('event')
        close_time = TRACKED.get('close_time')
        snap = {k: dict(v) for k, v in BOOKS.items()}

    print('═══════════════════════════════════════════════════════════════════════')
    print(' BOT REASONING — what the strategy sees right now')
    print('═══════════════════════════════════════════════════════════════════════')

    # ── Market state ─────────────────────────────────────────────
    print(f'\n[ MARKET STATE ]')
    print(f'  Spot: ${spot:,.2f}' if spot else '  Spot: ─ (no data)')
    if spot_ts:
        print(f'  Spot age: {(datetime.now(timezone.utc) - spot_ts).total_seconds():.1f}s')
    print(f'  Event:    {event or "─ (none tracked)"}')
    if close_time:
        ttc = (close_time - datetime.now(timezone.utc)).total_seconds()
        print(f'  Close in: {ttc:.0f}s ({ttc/60:.1f}min)')
    print(f'  Books loaded: {len(snap)}')
    sigma = _causal_sigma()
    if sigma:
        sig_s = sigma / math.sqrt(365.25 * 24 * 3600)
        sig_rem = sig_s * spot * math.sqrt(max(1, (close_time - datetime.now(timezone.utc)).total_seconds())) if (spot and close_time) else None
        print(f'  Sigma (annual): {sigma*100:.2f}%   1-σ move to expiry: ${sig_rem:.0f}' if sig_rem else f'  Sigma (annual): {sigma*100:.2f}%')
    else:
        with _LOCK:
            n_hist = len(SPOT.get('history', []))
        print(f'  Sigma: insufficient ({n_hist}/{CFG["sigma_min_points"]} spot points)')

    # ── Universe: active markets sorted by strike ────────────────
    # Apply the SAME filter the scanner uses, so what you see matches reality
    active = []
    skipped_low_bid = 0
    for tk, b in snap.items():
        if b.get('status') != 'active' or b.get('floor') is None: continue
        ya = b.get('yes_ask'); yb = b.get('yes_bid')
        if ya is None or yb is None: continue
        if yb <= 0.01 or ya >= 0.99:
            skipped_low_bid += 1
            continue
        active.append({'tk': tk, 'K': float(b['floor']),
                       'ya': ya, 'yb': yb,
                       'ya_qty': b.get('yes_ask_qty') or 0,
                       'yb_qty': b.get('yes_bid_qty') or 0})
    active.sort(key=lambda r: r['K'])

    # Contract-type sanity check: threshold contracts have monotone yes_ask in strike
    # (decreasing as K increases). Bell-curve = range/bucket → strategy doesn't apply.
    contract_type = 'threshold'
    if event and not event.startswith('KXBTCD'):
        contract_type = 'NOT-KXBTCD'
    elif len(active) >= 10:
        atm_K = spot if spot else (active[len(active)//2]['K'])
        below = [r for r in active if r['K'] < atm_K]
        above = [r for r in active if r['K'] >= atm_K]
        if below and above:
            avg_below_ask = sum(r['ya'] for r in below) / len(below)
            avg_above_ask = sum(r['ya'] for r in above) / len(above)
            if avg_below_ask < avg_above_ask:
                contract_type = 'BELL (range/bucket)'

    print(f'\n[ UNIVERSE ]  {len(active)} active markets with real bids '
          f'(skipped {skipped_low_bid} with yes_bid≤0.01 or yes_ask≥0.99)')
    if contract_type != 'threshold':
        print(f'  ⚠️  CONTRACT TYPE: {contract_type}')
        print(f'  ⚠️  Strategy assumes THRESHOLD ("BTC ≥ $K"). This event prices like a')
        print(f'  ⚠️  range/bucket contract — Tier 1 and Tier 2 should NOT trade here.')
        print(f'  ⚠️  Make sure event_series = ("KXBTCD",) — current = {CFG["event_series"]}')
    if active and spot:
        atm_idx = min(range(len(active)), key=lambda i: abs(active[i]['K'] - spot))
        lo = max(0, atm_idx - 5); hi = min(len(active), atm_idx + 6)
        print(f'  Strikes near spot (${spot:,.0f}):')
        print(f'    {"strike":>10s} {"yes_bid":>10s} {"yes_ask":>10s} {"spread":>8s} {"bid_qty":>8s} {"ask_qty":>8s}')
        for i in range(lo, hi):
            r = active[i]
            arrow = ' ←ATM' if i == atm_idx else ''
            spr = (r['ya'] - r['yb']) * 100
            print(f'    {r["K"]:>10,.0f} {r["yb"]:>10.2f} {r["ya"]:>10.2f} {spr:>7.1f}¢ '
                  f'{int(r["ya_qty"]):>8} {int(r["yb_qty"]):>8}{arrow}')

    # ── Tier 1: monotonicity — show ALL pairs where bid_hi > ask_lo ──
    print(f'\n[ TIER 1 — Monotonicity Arb ]')
    print(f'  Rule: yes_bid(K_hi) > yes_ask(K_lo) + fees → risk-free pair trade')
    print(f'  Threshold: min_net_edge ≥ {CFG["t1_min_net_edge_cents"]}¢')

    t1_all = []  # all pairs with gross > 0
    for i, lo in enumerate(active):
        for hi in active[i+1:]:
            if hi['yb'] > lo['ya']:
                gross = hi['yb'] - lo['ya']
                fees = kalshi_fee(lo['ya']) + kalshi_fee(1.0 - hi['yb'])
                net = gross - fees
                t1_all.append({
                    'lo': lo['tk'], 'hi': hi['tk'],
                    'K_lo': lo['K'], 'K_hi': hi['K'],
                    'ask_lo': lo['ya'], 'bid_hi': hi['yb'],
                    'gross_c': gross * 100, 'fees_c': fees * 100, 'net_c': net * 100,
                    'qty': min(lo['ya_qty'] or 1, hi['yb_qty'] or 1, CFG['t1_max_qty_per_leg']),
                    'passes': net >= CFG['t1_min_net_edge_cents'] / 100,
                })

    if not t1_all:
        print('  → No monotonicity violations in the book (this is normal — markets are usually consistent)')
    else:
        t1_all.sort(key=lambda r: r['net_c'], reverse=True)
        passing = [r for r in t1_all if r['passes']]
        print(f'  Pairs with bid_hi > ask_lo: {len(t1_all)}  |  passing fee threshold: {len(passing)}')
        print(f'  Top {min(max_rows, len(t1_all))}:')
        print(f'    {"K_lo":>8s} {"K_hi":>8s} {"ask_lo":>7s} {"bid_hi":>7s} {"gross":>6s} {"fees":>5s} {"net":>6s} {"qty":>4s}  status')
        for r in t1_all[:max_rows]:
            tag = '✓ TRADE' if r['passes'] else f'✗ net {r["net_c"]:+.1f}¢ < {CFG["t1_min_net_edge_cents"]}¢'
            print(f'    {r["K_lo"]:>8,.0f} {r["K_hi"]:>8,.0f} {r["ask_lo"]:>7.2f} {r["bid_hi"]:>7.2f} '
                  f'{r["gross_c"]:>5.1f}¢ {r["fees_c"]:>4.1f}¢ {r["net_c"]:>+5.1f}¢ {r["qty"]:>4}  {tag}')

    # ── Tier 2: deep-ITM — show fair value vs market price per strike ──
    print(f'\n[ TIER 2 — Deep-ITM Convergence ]')
    print(f'  Rule: price ≥ {CFG["t2_min_price"]}, fair ≥ {CFG["t2_min_fair"]}, edge ≥ {CFG["t2_min_edge_cents"]}¢, '
          f'TTC ∈ [{CFG["t2_min_secs_to_close"]}, {CFG["t2_max_secs_to_close"]}]s')

    if not spot or not close_time:
        print('  → No spot or close_time — skipping')
    elif not sigma:
        print('  → No sigma yet — skipping (need ≥15 spot points)')
    else:
        secs = (close_time - datetime.now(timezone.utc)).total_seconds()
        if not (CFG['t2_min_secs_to_close'] <= secs <= CFG['t2_max_secs_to_close']):
            print(f'  → TTC {secs:.0f}s is outside [{CFG["t2_min_secs_to_close"]}, {CFG["t2_max_secs_to_close"]}]s — Tier 2 idle')
        else:
            sigma_f = max(sigma, CFG['t2_sigma_floor_annual'])
            sigma_c = sigma_f * (1.0 + CFG['sigma_uncertainty_discount'])
            sig_s = sigma_c / math.sqrt(365.25 * 24 * 3600)
            sig_rem = sig_s * spot * math.sqrt(secs)
            min_dist = max(CFG['t2_min_dollar_distance_from_strike'],
                           spot * CFG['t2_min_pct_distance_from_strike'])
            print(f'  σ_rem (1-σ move to expiry): ${sig_rem:.0f}   (floored σ {sigma_f*100:.1f}%, +disc {CFG["sigma_uncertainty_discount"]*100:.0f}%)')
            print(f'  Min |spot − K|: ${min_dist:.0f} (max ${CFG["t2_min_dollar_distance_from_strike"]:.0f} or {CFG["t2_min_pct_distance_from_strike"]*100:.1f}%)')
            print(f'  Per-strike fair vs market (sorted by |edge|):')
            rows = []
            for r in active:
                K = r['K']
                dist = abs(spot - K)
                d = dist / sig_rem
                fair_yes = _norm_cdf(d) if spot > K else 1.0 - _norm_cdf(d)
                fair_no = 1.0 - fair_yes
                yes_edge = fair_yes - r['ya'] - kalshi_fee(r['ya'])
                no_price = 1.0 - r['yb']
                no_edge = fair_no - no_price - kalshi_fee(no_price)
                for side, price, fair, edge in [('yes', r['ya'], fair_yes, yes_edge),
                                                 ('no',  no_price, fair_no, no_edge)]:
                    rows.append({
                        'K': K, 'side': side, 'price': price, 'fair': fair,
                        'edge_c': edge * 100, 'dist': dist,
                        'passes': (price >= CFG['t2_min_price'] and price <= 0.97
                                   and fair >= CFG['t2_min_fair']
                                   and edge >= CFG['t2_min_edge_cents'] / 100
                                   and dist >= min_dist),
                    })
            rows.sort(key=lambda x: x['edge_c'], reverse=True)
            passing = [r for r in rows if r['passes']]
            print(f'  Total candidates: {len(rows)}  |  passing all filters: {len(passing)}')
            print(f'    {"strike":>8s} {"side":>4s} {"price":>6s} {"fair":>6s} {"edge":>7s} {"dist":>6s}  status')
            for r in rows[:max_rows]:
                reasons = []
                if r['price'] < CFG['t2_min_price']: reasons.append(f'price<{CFG["t2_min_price"]}')
                if r['price'] > 0.97: reasons.append('price>0.97')
                if r['fair'] < CFG['t2_min_fair']: reasons.append(f'fair<{CFG["t2_min_fair"]}')
                if r['edge_c'] < CFG['t2_min_edge_cents']: reasons.append(f'edge<{CFG["t2_min_edge_cents"]}¢')
                if r['dist'] < min_dist: reasons.append(f'dist<${min_dist:.0f}')
                tag = '✓ TRADE' if r['passes'] else ('✗ ' + ', '.join(reasons) if reasons else '✗')
                print(f'    {r["K"]:>8,.0f} {r["side"]:>4s} {r["price"]:>6.2f} {r["fair"]:>6.3f} '
                      f'{r["edge_c"]:>+6.1f}¢ ${r["dist"]:>5.0f}  {tag}')

    # ── Risk gate state ──────────────────────────────────────────
    print(f'\n[ RISK GATES ]')
    open_pos = _open_positions()
    exp = _open_exposure()
    pnl = _today_pnl()
    print(f'  Open positions: {len(open_pos)} / {CFG["max_concurrent_positions"]}')
    print(f'  Exposure:       ${exp:.2f} / ${CFG["max_total_exposure"]:.0f}')
    print(f'  Today PnL:      ${pnl:+.2f}  (daily loss limit ${CFG["daily_loss_limit"]:.0f})')
    for p in open_pos:
        tag = 'T1' if p['tier'] == 1 else 'T2'
        print(f'    {tag} {p["market_ticker"]} {p["side"]} x{p["contracts"]} @${p["entry_price"]:.2f}')

    # ── What the scanners ACTUALLY return (after filters) ────────
    sigs = scan_all_signals()
    print(f'\n[ EXECUTIONABLE SIGNALS NOW ]  T1={len(sigs["tier1"])}  T2={len(sigs["tier2"])}')
    for s in sigs['tier1'][:5]:
        print(f'  T1 BUY {s.mkt_lo} yes @{s.ask_lo:.2f} + BUY {s.mkt_hi} no @{1-s.bid_hi:.2f}  '
              f'edge={s.net_edge_cents:.1f}¢ qty={s.qty}')
    for s in sigs['tier2'][:5]:
        print(f'  T2 BUY {s.ticker} {s.side} x{s.qty} @${s.price:.2f}  '
              f'fair={s.fair_value:.3f} edge={s.edge_cents:.1f}¢')

    print('═══════════════════════════════════════════════════════════════════════')


print('think() ready — call to see exactly what each tier is evaluating.')
