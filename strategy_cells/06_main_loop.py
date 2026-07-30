# § 5 — Main Loop

def _decision_loop():
    while BOT_STATE['running']:
        BOT_STATE['iter'] += 1
        SIGNAL_FLOW['cycles'] += 1
        # Reset per-cycle stats
        for k in SIGNAL_FLOW['last_cycle']:
            SIGNAL_FLOW['last_cycle'][k] = 0
        try:
            if BOT_STATE['iter'] % 12 == 0:
                check_settlements()

            sigs = scan_all_signals()
            n1, n2, n3 = len(sigs['tier1']), len(sigs['tier2']), len(sigs['tier3'])
            SIGNAL_FLOW['last_cycle']['t1_seen'] = n1
            SIGNAL_FLOW['last_cycle']['t2_seen'] = n2
            SIGNAL_FLOW['last_cycle']['t3_seen'] = n3
            SIGNAL_FLOW['totals']['t1_seen'] += n1
            SIGNAL_FLOW['totals']['t2_seen'] += n2
            SIGNAL_FLOW['totals']['t3_seen'] += n3

            # Tier 1 first (risk-free) — execute up to 1 per cycle
            for s in sigs['tier1']:
                if execute_t1(s):
                    break

            # Tier 2 — best remaining
            for s in sigs['tier2']:
                if execute_t2(s):
                    break

            # Tier 3 — best remaining
            for s in sigs['tier3']:
                if execute_t3(s):
                    break

        except Exception as e:
            _log(f'decision err: {e}')
        _sleep(CFG['decision_interval_sec'])


def start_bot():
    if BOT_STATE['running']:
        print('Bot already running.'); return
    BOT_STATE['running'] = True
    BOT_STATE['iter'] = 0
    BOT_STATE['log'] = []
    BOT_STATE['trades_this_session'] = 0
    _WS_STATE['msg_count'] = 0
    _WS_STATE['reconnect_count'] = 0
    _WS_STATE['subscribed_event'] = None
    _WS_STATE['last_msg_ts'] = None

    # Sync-bootstrap: find an event NOW so WS subscribes on first connect
    print('Bootstrapping: looking for active event...')
    try:
        be, bc, n = _scan_for_event()
        if be:
            _apply_tracked_event(be, bc)
            print(f'  → tracking {be} closes {bc} (from {n} candidates)')
        else:
            print(f'  → no events in 2min–2hr window (checked {n} candidates). '
                  f'event_tracker will retry every 60s.')
    except Exception as e:
        print(f'  bootstrap err: {e}')

    workers = [
        ('spot_poller', _spot_poller),
        ('ws_listener', _ws_listener),
        ('event_tracker', _event_tracker),
        ('tick_flusher', _tick_flusher),
        ('decision_loop', _decision_loop),
    ]
    threads = []
    for name, fn in workers:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        threads.append(t)
    BOT_STATE['threads'] = threads

    print(f'Bot started.  Mode={CFG["mode"]}  T1={CFG["t1_enabled"]}  T2={CFG["t2_enabled"]}')
    print(f'  Tier1: min_edge={CFG["t1_min_net_edge_cents"]}c max_qty={CFG["t1_max_qty_per_leg"]}')
    print(f'  Tier2: price>={CFG["t2_min_price"]} fair>={CFG["t2_min_fair"]} '
          f'ttc=[{CFG["t2_min_secs_to_close"]},{CFG["t2_max_secs_to_close"]}]s')
    print(f'  Risk: max_pos={CFG["max_concurrent_positions"]}  '
          f'exp_cap=${CFG["max_total_exposure"]}  daily_loss=${CFG["daily_loss_limit"]}')
    print(f'  status()  diagnostics()  tick_stats()  stop_bot()')


def stop_bot():
    BOT_STATE['running'] = False
    for t in BOT_STATE.get('threads', []):
        t.join(timeout=5)
    BOT_STATE['threads'] = []
    _WS_STATE['connected'] = False
    print(f'Bot stopped. Trades this session: {BOT_STATE["trades_this_session"]}')


print('Main loop ready.')
