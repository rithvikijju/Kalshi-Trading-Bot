# § 6c — Live-updating portfolio dashboard
#
# Mark-to-market every open position using current order book, show realized
# + unrealized PnL, refresh every N seconds without re-running the cell.
# Stop with Jupyter's ■ (interrupt kernel).


def _market_to_market(pos):
    """Return (current_mark, unrealized_pnl_per_contract, source) for one position.

    Conservative: uses the price we could exit at right now (bid for our side).
    For a YES long, exit = yes_bid. For a NO long, exit = no_bid = 1 - yes_ask.
    Returns (None, None, 'stale') if no quote available."""
    with _LOCK:
        b = dict(BOOKS.get(pos['market_ticker'], {}))
    if not b:
        return None, None, 'no book'
    side = pos['side']
    entry = pos['entry_price']
    if side == 'yes':
        exit_p = b.get('yes_bid')
    else:
        ya = b.get('yes_ask')
        exit_p = (1.0 - ya) if ya is not None else None
    if exit_p is None:
        return None, None, 'no quote'
    # PnL if we closed now: payout (= exit_p) - cost (= entry + fee)
    # No exit fee assumed for now (Kalshi sometimes waives close-side fee for makers)
    unrealized = exit_p - entry - kalshi_fee(entry)
    return exit_p, unrealized, 'live'


def _pair_unrealized(pos_a, pos_b):
    """For a Tier 1 pair, compute combined unrealized PnL."""
    m_a, u_a, src_a = _market_to_market(pos_a)
    m_b, u_b, src_b = _market_to_market(pos_b)
    if u_a is None or u_b is None:
        return None, src_a if u_a is None else src_b
    total = (u_a + u_b) * pos_a['contracts']  # both legs have same qty
    return total, 'live'


def _render_dashboard():
    out = []
    now = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
    mode_str = 'LIVE' if CFG['mode'] == 'live' else 'PAPER'
    out.append(f'═════════ KALSHI ARB BOT ═════════ {now} ═════════ Mode: {mode_str} ═════════')

    # ── Connection / data freshness ─────────────────────────────
    ws_age = ''
    if _WS_STATE.get('last_msg_ts'):
        ws_age = f' (last {(datetime.now(timezone.utc) - _WS_STATE["last_msg_ts"]).total_seconds():.0f}s ago)'
    ws_ok = '✓' if _WS_STATE.get('connected') else '✗'
    out.append(f'WS {ws_ok} msgs={_WS_STATE["msg_count"]}  sub={_WS_STATE.get("subscribed_event") or "—"}{ws_age}')

    with _LOCK:
        spot = SPOT.get('price'); spot_ts = SPOT.get('ts')
        event = TRACKED.get('event'); close = TRACKED.get('close_time')
        n_books = len(BOOKS)
    spot_line = 'Spot: ─'
    if spot is not None:
        age = (datetime.now(timezone.utc) - spot_ts).total_seconds() if spot_ts else None
        spot_line = f'Spot: ${spot:,.2f}' + (f' ({age:.0f}s ago)' if age is not None else '')
    out.append(spot_line)
    if event:
        ttc = (close - datetime.now(timezone.utc)).total_seconds() / 60 if close else None
        ttc_str = f'  TTL: {ttc:.1f}min' if ttc is not None else ''
        out.append(f'Event: {event}{ttc_str}  Books: {n_books}')
    else:
        out.append(f'Event: — (no active event in window)')
    sigma = _causal_sigma()
    if sigma:
        out.append(f'Sigma: {sigma*100:.1f}% annual')
    else:
        with _LOCK:
            n_hist = len(SPOT.get('history', []))
        out.append(f'Sigma: warming up ({n_hist}/{CFG["sigma_min_points"]} pts)')

    # ── Portfolio ───────────────────────────────────────────────
    open_pos = _open_positions()
    realized_today = _today_pnl()
    realized_all_time = 0.0
    conn = _trades_conn()
    r = conn.execute('SELECT COALESCE(SUM(pnl),0), COUNT(*) FILTER (WHERE settled=1) FROM trades').fetchone()
    realized_all_time = float(r[0])
    n_settled = int(r[1])
    conn.close()

    out.append('')
    out.append(f'┌─ PORTFOLIO ─────────────────────────────────────────────────────────────────')

    # Group T1 positions by pair_id; T2/T3 are single-leg
    t1_pairs = {}
    single_positions = []  # T2 + T3
    for p in open_pos:
        if p['tier'] == 1 and p.get('pair_id'):
            t1_pairs.setdefault(p['pair_id'], []).append(p)
        else:
            single_positions.append(p)

    n_t2 = sum(1 for p in single_positions if p['tier'] == 2)
    n_t3 = sum(1 for p in single_positions if p['tier'] == 3)
    unrealized_total = 0.0
    out.append(f'│ Open positions: {len(open_pos)} / {CFG["max_concurrent_positions"]}'
               f'  (T1 pairs: {len(t1_pairs)}, T2: {n_t2}, T3: {n_t3})')

    # Tier 1 pairs
    if t1_pairs:
        out.append(f'│')
        out.append(f'│ Tier 1 pairs (risk-free arbitrage):')
        for pid, legs in t1_pairs.items():
            if len(legs) != 2:
                out.append(f'│   ⚠ orphan pair {pid[-6:]}  ({len(legs)} legs)')
                continue
            u, src = _pair_unrealized(legs[0], legs[1])
            if u is not None:
                unrealized_total += u
            u_str = f'${u:+.2f}' if u is not None else '—'
            out.append(f'│   {pid[-8:]}  {legs[0]["market_ticker"][-18:]} {legs[0]["side"]:3s}'
                       f' / {legs[1]["market_ticker"][-18:]} {legs[1]["side"]:3s}'
                       f'  x{legs[0]["contracts"]}  unreal={u_str}')

    # T2/T3 single legs
    if single_positions:
        out.append(f'│')
        out.append(f'│ Single-leg positions (mark-to-market):')
        out.append(f'│   {"tier":>4s} {"market_ticker":<26s} {"side":>4s} {"qty":>3s} {"entry":>6s} '
                   f'{"mark":>6s} {"unreal":>8s}')
        for p in single_positions:
            mark, u_per_c, src = _market_to_market(p)
            u = (u_per_c * p['contracts']) if u_per_c is not None else None
            if u is not None:
                unrealized_total += u
            mark_str = f'${mark:.2f}' if mark is not None else '—'
            u_str = f'${u:+.2f}' if u is not None else '—'
            tk_short = p['market_ticker'][-26:]
            tier_str = f'T{p["tier"]}'
            out.append(f'│   {tier_str:>4s} {tk_short:<26s} {p["side"]:>4s} {p["contracts"]:>3} '
                       f'${p["entry_price"]:>5.2f} {mark_str:>6s} {u_str:>8s}')

    if not open_pos:
        out.append(f'│ (no open positions)')

    # Paper account snapshot (paper mode only)
    try:
        if CFG['mode'] == 'paper':
            pa = PAPER_ACCOUNT
            mtm = paper_mark_to_market()
            ret = (mtm - pa['starting_cash']) / pa['starting_cash'] * 100
            out.append(f'│')
            out.append(f'│ ── PAPER ACCOUNT (${pa["starting_cash"]:.2f} start) ──')
            out.append(f'│   Cash: ${pa["cash"]:.2f}  Locked: ${pa["locked"]:.2f}  '
                       f'MtM: ${mtm:.2f}  Return: {ret:+.2f}%')
            wpct = (pa["wins"] / pa["trades_settled"] * 100) if pa["trades_settled"] else 0
            out.append(f'│   Settled: {pa["trades_settled"]} (W{pa["wins"]}/L{pa["losses"]} = {wpct:.0f}%)  '
                       f'MaxDD: {pa["max_drawdown"]*100:.1f}%')
    except NameError:
        pass

    # PnL summary
    out.append(f'│')
    net_today = realized_today + unrealized_total
    pnl_color_today = '+' if net_today >= 0 else ''
    out.append(f'│ Realized today: ${realized_today:+8.2f}    '
               f'Unrealized: ${unrealized_total:+8.2f}    '
               f'Net today: ${pnl_color_today}{net_today:+8.2f}')
    out.append(f'│ All-time realized: ${realized_all_time:+8.2f} across {n_settled} settled trades')
    out.append(f'│ Exposure: ${_open_exposure():.2f} / ${CFG["max_total_exposure"]:.0f}')
    out.append(f'└─────────────────────────────────────────────────────────────────────────────')

    # ── Active signals (right now) ──────────────────────────────
    sigs = scan_all_signals()
    out.append('')
    out.append(f'┌─ LIVE SIGNALS (this scan) ──────────────────────────────────────────────────')
    out.append(f'│ T1={len(sigs["tier1"])} T2={len(sigs["tier2"])} T3={len(sigs["tier3"])}  '
               f'(actionable right now, after pair-dedup)')
    for s in sigs['tier1'][:2]:
        out.append(f'│   T1 {s.mkt_lo[-18:]:18s} yes@{s.ask_lo:.2f} + '
                   f'{s.mkt_hi[-18:]:18s} no@{1-s.bid_hi:.2f}  '
                   f'edge={s.net_edge_cents:.1f}¢ qty={s.qty}')
    for s in sigs['tier2'][:2]:
        out.append(f'│   T2 {s.ticker[-26:]:26s} {s.side:3s} x{s.qty} @${s.price:.2f}  '
                   f'fair={s.fair_value:.3f} edge={s.edge_cents:.1f}¢')
    for s in sigs['tier3'][:2]:
        out.append(f'│   T3 {s.ticker[-26:]:26s} no  x{s.qty} @${s.no_price:.2f}  '
                   f'dist=${s.spot_dist:.0f} persist={s.persistence_min:.0f}min')
    if not (sigs['tier1'] or sigs['tier2'] or sigs['tier3']):
        out.append('│   (no actionable signals right now)')
    out.append(f'└──────────────────────────────────────────────────────────────────────────────')

    # ── Signal flow telemetry (session totals + most-recent activity) ──────
    lc = SIGNAL_FLOW['last_cycle']
    tot = SIGNAL_FLOW['totals']
    cyc = SIGNAL_FLOW['cycles']
    out.append('')
    out.append(f'┌─ SIGNAL FLOW ({cyc} scan cycles this session) ───────────────────────────────')
    out.append(f'│ Last cycle:  T1 seen={lc["t1_seen"]} exec={lc["t1_exec"]}  '
               f'T2 seen={lc["t2_seen"]} exec={lc["t2_exec"]}  '
               f'T3 seen={lc["t3_seen"]} exec={lc["t3_exec"]}')
    out.append(f'│ Session sum: T1 seen={tot["t1_seen"]} exec={tot["t1_exec"]}  '
               f'T2 seen={tot["t2_seen"]} exec={tot["t2_exec"]}  '
               f'T3 seen={tot["t3_seen"]} exec={tot["t3_exec"]}')
    if SIGNAL_FLOW['skip_reasons']:
        top_reasons = sorted(SIGNAL_FLOW['skip_reasons'].items(),
                              key=lambda x: -x[1])[:5]
        out.append(f'│ Top skip reasons: ' + ', '.join(f'{r}={n}' for r, n in top_reasons))
    out.append(f'│')
    out.append(f'│ Recent signal decisions (latest 8):')
    if SIGNAL_FLOW['recent']:
        for entry in list(SIGNAL_FLOW['recent'])[-8:]:
            ts, tier, ticker, status, detail = entry
            mark = '✓' if status == 'EXEC' else '✗'
            out.append(f'│   [{ts}] {mark} T{tier} {ticker:<28s} {status:<25s} {detail}')
    else:
        out.append(f'│   (no signal activity yet)')
    out.append(f'└──────────────────────────────────────────────────────────────────────────────')

    # ── SPRT (auto-disable monitor) ────────────────────────────
    try:
        out.append('')
        out.append(f'┌─ SPRT MONITOR — auto-disable when win rate drops below target ──────────────')
        for tier in (1, 2, 3):
            st = sprt_state(tier)
            if not st:
                continue
            status = 'DISABLED' if st['disabled'] else 'active'
            verdict = ''
            if st['llr'] <= SPRT_LOWER:
                verdict = '← DISABLE'
            elif st['llr'] >= SPRT_UPPER:
                verdict = '← CONFIRMED'
            out.append(f'│   T{tier}: {status:>8s}  n={st["n"]:>3}  '
                       f'LLR={st["llr"]:+6.2f}  '
                       f'(target {st["p_target"]*100:.0f}% / breakeven {st["p_be"]*100:.0f}%) {verdict}')
        out.append(f'└──────────────────────────────────────────────────────────────────────────────')
    except NameError:
        pass

    # ── Recent activity ─────────────────────────────────────────
    out.append('')
    out.append('Recent activity:')
    for entry in BOT_STATE['log'][-5:]:
        out.append(f'  {entry}')

    return '\n'.join(out)


def live_status(refresh_sec=2):
    """Auto-refreshing dashboard. Press ■ (interrupt kernel) to stop."""
    try:
        from IPython.display import clear_output
        in_jupyter = True
    except ImportError:
        in_jupyter = False
        def clear_output(wait=True):
            os.system('clear' if os.name == 'posix' else 'cls')

    try:
        while True:
            clear_output(wait=True)
            print(_render_dashboard())
            print(f'\n[refreshing every {refresh_sec}s — press ■ stop to halt]')
            time.sleep(refresh_sec)
    except KeyboardInterrupt:
        clear_output(wait=True)
        print(_render_dashboard())
        print('\n[dashboard stopped]')


print('Live dashboard ready.')
print('  live_status()        — refreshes every 2s with mark-to-market PnL')
print('  live_status(5)       — refresh every 5s')
