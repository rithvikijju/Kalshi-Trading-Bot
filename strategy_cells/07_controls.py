# § 6 — Controls: status, diagnostics, live mode, kill switch

def status():
    print(f'Running: {BOT_STATE["running"]}  Mode: {CFG["mode"]}  Iter: {BOT_STATE["iter"]}')
    ws_age = ''
    if _WS_STATE.get('last_msg_ts'):
        ws_age = f' (last msg {(datetime.now(timezone.utc) - _WS_STATE["last_msg_ts"]).total_seconds():.0f}s ago)'
    print(f'WS: {"connected" if _WS_STATE["connected"] else "DISCONNECTED"}  '
          f'msgs={_WS_STATE["msg_count"]}  sub={_WS_STATE["subscribed_event"]}{ws_age}')
    with _LOCK:
        spot = SPOT.get('price'); spot_ts = SPOT.get('ts')
        event = TRACKED.get('event'); close = TRACKED.get('close_time')
        refreshed = TRACKED.get('refreshed_at')
        n_books = len(BOOKS)
    if spot:
        age = (datetime.now(timezone.utc) - spot_ts).total_seconds() if spot_ts else None
        if age is not None:
            print(f'Spot: ${spot:,.2f} ({age:.0f}s ago)')
        else:
            print(f'Spot: ${spot:,.2f}')
    else:
        print('Spot: unavailable')
    if event:
        ttl = (close - datetime.now(timezone.utc)).total_seconds() / 60
        print(f'Event: {event}  TTL: {ttl:.1f}min  Books: {n_books}')
    else:
        ref_age = ''
        if refreshed:
            ref_age = f' (tracker last ran {(datetime.now(timezone.utc) - refreshed).total_seconds():.0f}s ago)'
        print(f'Event: NONE TRACKED{ref_age}  — event_tracker has not found an event in the 2min–2hr window')
    sigma = _causal_sigma()
    if sigma:
        print(f'Sigma: {sigma*100:.1f}%')
    else:
        with _LOCK:
            n_hist = len(SPOT.get('history', []))
        print(f'Sigma: insufficient data ({n_hist}/{CFG["sigma_min_points"]} points)')

    sigs = scan_all_signals()
    print(f'\nCurrent signals: T1={len(sigs["tier1"])}  T2={len(sigs["tier2"])}')
    for s in sigs['tier1'][:3]:
        print(f'  T1 {s.mkt_lo} | {s.mkt_hi}  edge={s.net_edge_cents:.1f}c  qty={s.qty}')
    for s in sigs['tier2'][:3]:
        print(f'  T2 {s.ticker} {s.side} @${s.price:.2f}  fair={s.fair_value:.3f}  edge={s.edge_cents:.1f}c')

    pos = _open_positions()
    print(f'\nOpen positions: {len(pos)}')
    for p in pos:
        tag = 'T1' if p['tier'] == 1 else 'T2'
        print(f'  {tag} {p["market_ticker"]} {p["side"]} x{p["contracts"]} @${p["entry_price"]:.2f}')
    print(f'\nToday PnL: ${_today_pnl():+.2f}  Exposure: ${_open_exposure():.2f}')
    print(f'Trades this session: {BOT_STATE["trades_this_session"]}')
    for entry in BOT_STATE['log'][-5:]:
        print(f'  {entry}')


def diagnostics():
    print('=== DIAGNOSTICS ===')
    print(f'Mode: {CFG["mode"]}  Live: {CFG["live_enabled"]}  Iter: {BOT_STATE["iter"]}')
    alive = [t.name for t in BOT_STATE.get('threads', []) if t.is_alive()]
    dead  = [t.name for t in BOT_STATE.get('threads', []) if not t.is_alive()]
    print(f'Threads alive: {alive}')
    if dead: print(f'Threads DEAD: {dead}')
    print(f'\nWS: connected={_WS_STATE["connected"]}  msgs={_WS_STATE["msg_count"]}  '
          f'reconnects={_WS_STATE["reconnect_count"]}')
    ws_last = _WS_STATE.get('last_msg_ts')
    if ws_last:
        age = (datetime.now(timezone.utc) - ws_last).total_seconds()
        print(f'  Last WS msg: {age:.1f}s ago')

    now = datetime.now(timezone.utc)
    with _LOCK:
        spot_age = (now - SPOT['ts']).total_seconds() if SPOT.get('ts') else None
        n_hist = len(SPOT.get('history', []))
        n_books = len(BOOKS)
    spot_age_str = f'{spot_age:.0f}s' if spot_age is not None else '— (no spot yet)'
    print(f'\nSpot age: {spot_age_str}   history: {n_hist} points')
    print(f'Books loaded: {n_books}')

    sigma = _causal_sigma()
    if sigma:
        print(f'Sigma (annual): {sigma*100:.2f}%')
    else:
        print('Sigma: None')

    sigs = scan_all_signals()
    print(f'\nSignals now:  T1={len(sigs["tier1"])}   T2={len(sigs["tier2"])}')
    for s in sigs['tier1'][:5]:
        print(f'  T1 lo={s.mkt_lo} hi={s.mkt_hi}  ask_lo={s.ask_lo:.2f} bid_hi={s.bid_hi:.2f}  '
              f'qty={s.qty} edge={s.net_edge_cents:.1f}c')
    for s in sigs['tier2'][:5]:
        print(f'  T2 {s.ticker} {s.side} @${s.price:.2f}  fair={s.fair_value:.3f}  '
              f'edge={s.edge_cents:.1f}c ttc={s.secs_remaining:.0f}s')

    conn = _trades_conn()
    total = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    settled = conn.execute('SELECT COUNT(*) FROM trades WHERE settled=1').fetchone()[0]
    pnl = conn.execute('SELECT COALESCE(SUM(pnl),0) FROM trades WHERE settled=1').fetchone()[0]
    wins = conn.execute('SELECT COUNT(*) FROM trades WHERE settled=1 AND pnl>0').fetchone()[0]
    by_tier = conn.execute('SELECT tier, COUNT(*), COALESCE(SUM(pnl),0) FROM trades WHERE settled=1 GROUP BY tier').fetchall()
    conn.close()
    print(f'\nAll-time: total={total} settled={settled}')
    if settled:
        print(f'  PnL=${pnl:+.2f}  Win rate={wins/settled*100:.0f}%')
    for r in by_tier:
        print(f'  Tier {r[0]}: n={r[1]} pnl=${r[2]:+.2f}')

    print(f'\nRecent log:')
    for entry in BOT_STATE['log'][-10:]:
        print(f'  {entry}')


def trade_history(n=20):
    conn = _trades_conn()
    rows = conn.execute('SELECT * FROM trades ORDER BY id DESC LIMIT ?', (n,)).fetchall()
    conn.close()
    if not rows:
        print('No trades yet.'); return
    df = pd.DataFrame([dict(r) for r in rows])
    cols = ['id', 'ts', 'tier', 'pair_id', 'market_ticker', 'side', 'contracts',
            'entry_price', 'edge_cents', 'mode', 'settled', 'settlement_result', 'pnl']
    cols = [c for c in cols if c in df.columns]
    print(df[cols].to_string(index=False))


def enable_live():
    if kalshi_live is None:
        print('No prod credentials — cannot go live.'); return
    try:
        bal = kalshi_live.get_balance()
        balance = float(bal.get('balance', 0)) / 100.0
    except Exception as e:
        print(f'Balance check failed: {e}'); return
    if balance <= 0:
        print('Balance $0 — refusing.'); return
    CFG['mode'] = 'live'; CFG['live_enabled'] = True
    print(f'LIVE TRADING ENABLED.  Balance ${balance:.2f}')
    print(f'  Max exposure: ${CFG["max_total_exposure"]}  Daily loss cap: ${CFG["daily_loss_limit"]}')


def disable_live():
    CFG['mode'] = 'paper'; CFG['live_enabled'] = False
    print('Switched to paper mode.')


def kill_switch():
    print('KILL SWITCH activated')
    CFG['mode'] = 'paper'; CFG['live_enabled'] = False
    stop_bot()
    if kalshi_live:
        try:
            orders = kalshi_live._get('/portfolio/orders', {'status': 'resting'}).get('orders', []) or []
            for o in orders:
                oid = o.get('order_id')
                if oid:
                    kalshi_live.cancel_order(oid)
            print(f'  Cancelled {len(orders)} resting orders')
        except Exception as e:
            print(f'  cancel err: {e}')


print('Controls ready.')
print('  status()         — quick state')
print('  diagnostics()    — detailed')
print('  trade_history()  — recent trades')
print('  tick_stats()     — data collection stats')
print('  enable_live()    — switch paper -> live')
print('  disable_live()   — back to paper')
print('  kill_switch()    — halt + cancel all')
