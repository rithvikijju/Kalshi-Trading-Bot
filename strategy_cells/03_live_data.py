# § 2 — Live Data Pipeline
#
# Workers (background threads):
#   1. Coinbase BTC spot poller (every 2s)
#   2. Kalshi websocket listener (ticker channel) → updates BOOKS in-memory
#   3. Event tracker — finds nearest open hourly KXBTCD event
#   4. Tick flusher — persists every tick to SQLite

_LOCK = threading.Lock()

SPOT = {'price': None, 'ts': None, 'history': []}
BOOKS: Dict[str, Dict[str, Any]] = {}
TRACKED = {'event': None, 'close_time': None, 'refreshed_at': None}
BOT_STATE = {'running': False, 'threads': [], 'log': [],
             'trades_this_session': 0, 'iter': 0}

_WS_STATE = {'connected': False, 'subscribed_event': None,
             'reconnect_count': 0, 'last_msg_ts': None, 'msg_count': 0,
             'needs_resubscribe': False, 'mode': 'websocket'}


def _log(msg):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    entry = f'[{ts}] {msg}'
    BOT_STATE['log'].append(entry)
    if len(BOT_STATE['log']) > 500:
        BOT_STATE['log'] = BOT_STATE['log'][-200:]


# ── Tick logger ──────────────────────────────────────────────────

TICK_INIT_SQL = (
    "CREATE TABLE IF NOT EXISTS ticks ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
    " event_ticker TEXT, market_ticker TEXT NOT NULL,"
    " yes_bid REAL, yes_bid_qty REAL, yes_ask REAL, yes_ask_qty REAL,"
    " no_bid REAL, no_ask REAL, volume REAL, source TEXT DEFAULT 'ws',"
    " btc_spot REAL, secs_to_close REAL);"
    "CREATE TABLE IF NOT EXISTS spot_ticks ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
    " price REAL NOT NULL, source TEXT);"
    "CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(ts);"
    "CREATE INDEX IF NOT EXISTS idx_ticks_event ON ticks(event_ticker, ts);"
    "CREATE INDEX IF NOT EXISTS idx_ticks_market ON ticks(market_ticker, ts);"
    "CREATE INDEX IF NOT EXISTS idx_spot_ts ON spot_ticks(ts);"
)


def _init_tick_db():
    conn = sqlite3.connect(CFG['ticks_db_path'])
    # WAL = better concurrent reads + faster writes + crash safety for long runs.
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(TICK_INIT_SQL)
    # Best-effort migration for older DBs that pre-date btc_spot/secs_to_close.
    for col in ('btc_spot REAL', 'secs_to_close REAL'):
        try:
            conn.execute(f'ALTER TABLE ticks ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass  # already exists
    conn.commit(); conn.close()

_init_tick_db()

_TICK_QUEUE: list = []
_SPOT_TICK_QUEUE: list = []
_TICK_LOCK = threading.Lock()


def _queue_tick(event_ticker, market_ticker, yb, ya, yb_qty, ya_qty, no_bid, no_ask, volume, source='ws'):
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    # Snapshot spot + secs_to_close at tick time so future backtests have a
    # self-contained row. Read WITHOUT _LOCK — _queue_tick is called from
    # inside _LOCK-holding contexts (e.g. _seed_books_rest) so acquiring _LOCK
    # here would deadlock. Dict reads are safe under the GIL; slight staleness
    # on btc_spot/secs_to_close is fine for a tick log.
    spot = SPOT.get('price')
    ct = TRACKED.get('close_time')
    secs = (ct - now).total_seconds() if ct else None
    with _TICK_LOCK:
        _TICK_QUEUE.append((ts, event_ticker, market_ticker, yb, yb_qty, ya, ya_qty,
                            no_bid, no_ask, volume, source, spot, secs))


def _queue_spot_tick(price, source='coinbase'):
    ts = datetime.now(timezone.utc).isoformat()
    with _TICK_LOCK:
        _SPOT_TICK_QUEUE.append((ts, price, source))


def _tick_flusher():
    while BOT_STATE['running']:
        _flush_ticks()
        _sleep(5.0)
    _flush_ticks()


def _flush_ticks():
    with _TICK_LOCK:
        ticks = list(_TICK_QUEUE); _TICK_QUEUE.clear()
        spots = list(_SPOT_TICK_QUEUE); _SPOT_TICK_QUEUE.clear()
    if not ticks and not spots:
        return
    try:
        conn = sqlite3.connect(CFG['ticks_db_path'])
        if ticks:
            conn.executemany(
                'INSERT INTO ticks(ts,event_ticker,market_ticker,yes_bid,yes_bid_qty,'
                'yes_ask,yes_ask_qty,no_bid,no_ask,volume,source,btc_spot,secs_to_close) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', ticks)
        if spots:
            conn.executemany(
                'INSERT INTO spot_ticks(ts,price,source) VALUES(?,?,?)', spots)
        conn.commit(); conn.close()
    except Exception as e:
        _log(f'tick flush err: {e}')


def tick_stats():
    conn = sqlite3.connect(CFG['ticks_db_path'])
    n_ticks = conn.execute('SELECT COUNT(*) FROM ticks').fetchone()[0]
    n_spots = conn.execute('SELECT COUNT(*) FROM spot_ticks').fetchone()[0]
    first = conn.execute('SELECT MIN(ts) FROM ticks').fetchone()[0]
    last = conn.execute('SELECT MAX(ts) FROM ticks').fetchone()[0]
    n_events = conn.execute('SELECT COUNT(DISTINCT event_ticker) FROM ticks').fetchone()[0]
    n_markets = conn.execute('SELECT COUNT(DISTINCT market_ticker) FROM ticks').fetchone()[0]
    # Coverage of the new backtest-critical columns (NULL if pre-upgrade rows)
    n_spot_col = conn.execute('SELECT COUNT(*) FROM ticks WHERE btc_spot IS NOT NULL').fetchone()[0]
    n_ttc_col  = conn.execute('SELECT COUNT(*) FROM ticks WHERE secs_to_close IS NOT NULL').fetchone()[0]
    # Last-hour throughput (rough health check)
    last_hour = conn.execute(
        "SELECT COUNT(*) FROM ticks WHERE ts > datetime('now','-1 hour')"
    ).fetchone()[0]
    size_mb = os.path.getsize(CFG['ticks_db_path']) / (1024 ** 2)
    conn.close()
    print(f'Ticks: {n_ticks:,}  spot ticks: {n_spots:,}')
    print(f'Events: {n_events}  Markets: {n_markets}')
    print(f'Range: {first or "—"} → {last or "—"}')
    print(f'DB size: {size_mb:.1f} MB')
    print(f'Last hour throughput: {last_hour:,} ticks')
    if n_ticks:
        pct_spot = 100 * n_spot_col / n_ticks
        pct_ttc  = 100 * n_ttc_col  / n_ticks
        print(f'Backtest-ready rows: btc_spot {pct_spot:.0f}%  secs_to_close {pct_ttc:.0f}%')


def snapshot_ticks_db(label: str = None) -> str:
    """Copy the live ticks DB to a timestamped immutable snapshot.

    Call this at the end of an overnight run to lock in a backtestable file.
    Uses SQLite VACUUM INTO so the snapshot is consistent even while the
    bot keeps writing.
    """
    if label is None:
        label = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out_dir = Path('output/captures'); out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'ticks_capture_{label}.db'
    src = sqlite3.connect(CFG['ticks_db_path'])
    try:
        src.execute(f"VACUUM INTO '{out_path}'")
    finally:
        src.close()
    size_mb = out_path.stat().st_size / (1024 ** 2)
    print(f'Snapshot → {out_path}  ({size_mb:.1f} MB)')
    return str(out_path)


# ── Spot poller ───────────────────────────────────────────────────

def _coinbase_spot():
    try:
        r = requests.get('https://api.coinbase.com/v2/prices/BTC-USD/spot', timeout=5)
        r.raise_for_status()
        return float(r.json()['data']['amount']), 'coinbase'
    except Exception:
        r = requests.get('https://api.coingecko.com/api/v3/simple/price',
                         params={'ids': 'bitcoin', 'vs_currencies': 'usd'}, timeout=5)
        r.raise_for_status()
        return float(r.json()['bitcoin']['usd']), 'coingecko'


def _spot_poller():
    while BOT_STATE['running']:
        try:
            price, source = _coinbase_spot()
            # Hampel outlier filter (defined in §2b — only invoke if available)
            try:
                if 'spot_is_outlier' in globals() and spot_is_outlier(price):
                    _log(f'spot outlier rejected: ${price:.2f} (Hampel filter)')
                    _sleep(CFG['spot_poll_sec'])
                    continue
            except NameError:
                pass
            now = datetime.now(timezone.utc)
            with _LOCK:
                SPOT['price'] = price; SPOT['ts'] = now
                SPOT['history'].append((now, price))
                cutoff = now - timedelta(minutes=CFG['sigma_window_min'] + 10)
                SPOT['history'] = [(t, p) for t, p in SPOT['history'] if t > cutoff]
            _queue_spot_tick(price, source)
            # Update online math models if loaded
            try:
                ewma_update(price); drift_update(price)
            except NameError:
                pass
        except Exception as e:
            _log(f'spot err: {e}')
        _sleep(CFG['spot_poll_sec'])


# ── Kalshi websocket ──────────────────────────────────────────────

def _parse_ticker_msg(msg_data):
    tk = msg_data.get('market_ticker')
    if not tk:
        return
    def _d(key):
        v = msg_data.get(key)
        if v is None or v == '':
            return None
        try: return float(v)
        except (ValueError, TypeError): return None

    ya = _d('yes_ask_dollars'); yb = _d('yes_bid_dollars')
    yb_qty = _d('yes_bid_qty') or _d('volume_fp')
    ya_qty = _d('yes_ask_qty')
    na = (1.0 - yb) if yb is not None else None
    nb = (1.0 - ya) if ya is not None else None
    now = datetime.now(timezone.utc)

    with _LOCK:
        existing = BOOKS.get(tk, {})
        BOOKS[tk] = {
            'yes_bid': yb if yb is not None else existing.get('yes_bid'),
            'yes_ask': ya if ya is not None else existing.get('yes_ask'),
            'yes_bid_qty': yb_qty if yb_qty is not None else existing.get('yes_bid_qty'),
            'yes_ask_qty': ya_qty if ya_qty is not None else existing.get('yes_ask_qty'),
            'no_bid': nb if nb is not None else existing.get('no_bid'),
            'no_ask': na if na is not None else existing.get('no_ask'),
            'floor': existing.get('floor'),
            'close_time': existing.get('close_time'),
            'status': existing.get('status', 'active'),
            'ts': now,
            'volume': msg_data.get('volume_fp') or existing.get('volume'),
        }
        event = TRACKED.get('event')
        _WS_STATE['last_msg_ts'] = now
        _WS_STATE['msg_count'] += 1

    _queue_tick(event, tk, yb, ya, yb_qty, ya_qty, nb, na,
                msg_data.get('volume_fp'), 'ws')


def _seed_books_rest(event_ticker):
    # REST hydrate BOOKS with floor strikes + initial quotes
    try:
        mkts = kalshi_prod.get_markets(event_ticker=event_ticker, limit=200).get('markets', [])
        now = datetime.now(timezone.utc)
        with _LOCK:
            for m in mkts:
                tk = m.get('ticker')
                if not tk:
                    continue
                yb = m.get('yes_bid'); ya = m.get('yes_ask')
                yb = float(yb) / 100 if yb is not None else None
                ya = float(ya) / 100 if ya is not None else None
                na = (1.0 - yb) if yb is not None else None
                nb = (1.0 - ya) if ya is not None else None
                BOOKS[tk] = {
                    'yes_bid': yb, 'yes_ask': ya,
                    'yes_bid_qty': None, 'yes_ask_qty': None,
                    'no_bid': nb, 'no_ask': na,
                    'floor': m.get('floor_strike'),
                    'close_time': m.get('close_time'),
                    'status': (m.get('status') or '').lower(),
                    'ts': now, 'volume': m.get('volume'),
                }
                _queue_tick(event_ticker, tk, yb, ya, None, None, nb, na,
                            m.get('volume'), 'rest')
        _log(f'REST seed: {len(mkts)} markets for {event_ticker}')
    except Exception as e:
        _log(f'REST seed err: {e}')


def _get_event_tickers(event_ticker):
    try:
        mkts = kalshi_prod.get_markets(event_ticker=event_ticker, limit=200).get('markets', [])
        return [m['ticker'] for m in mkts if m.get('ticker')]
    except Exception:
        return []


async def _try_subscribe(ws, reason='', sub_id=1):
    """Try to send a subscribe message for the currently-tracked event.
    Idempotent: only sends if we have an event AND it's not already our sub.
    Returns True if subscribed."""
    with _LOCK:
        event = TRACKED.get('event')
    if not event:
        return False
    if event == _WS_STATE.get('subscribed_event'):
        return True
    tickers = _get_event_tickers(event)
    if not tickers:
        _log(f'WS subscribe skipped ({reason}): no tickers for {event}')
        return False
    try:
        await ws.send(json.dumps({
            'id': sub_id, 'cmd': 'subscribe',
            'params': {'channels': ['ticker'], 'market_tickers': tickers}}))
    except Exception as e:
        _log(f'WS subscribe send err ({reason}): {e}')
        return False
    _WS_STATE['subscribed_event'] = event
    _log(f'WS subscribed ({reason}): {len(tickers)} tickers on {event}')
    # Hydrate via REST in parallel so we have book data immediately
    _seed_books_rest(event)
    return True


async def _ws_connect():
    while BOT_STATE['running']:
        if kalshi_live is None:
            _log('WS: no auth client — REST fallback')
            _WS_STATE['mode'] = 'rest_fallback'
            return
        auth_headers = kalshi_live.ws_auth_headers()
        if not auth_headers:
            _log('WS: no auth headers — REST fallback')
            _WS_STATE['mode'] = 'rest_fallback'
            return
        try:
            async with websockets.connect(CFG['ws_url'], **{_WS_HEADER_PARAM: auth_headers}) as ws:
                _WS_STATE['connected'] = True
                _WS_STATE['reconnect_count'] = 0
                _WS_STATE['mode'] = 'websocket'
                _log('WS connected')

                # Initial subscription attempt
                await _try_subscribe(ws, reason='initial')

                sub_id_counter = [10]
                last_sub_attempt = time.time()

                while BOT_STATE['running']:
                    # Resubscribe edge — event changed
                    if _WS_STATE['needs_resubscribe']:
                        _WS_STATE['needs_resubscribe'] = False
                        await _try_subscribe(ws, reason='event-changed', sub_id=sub_id_counter[0])
                        sub_id_counter[0] += 1
                    # Retry subscription every 8s if we still have no sub yet
                    elif _WS_STATE.get('subscribed_event') is None and (time.time() - last_sub_attempt) > 8.0:
                        await _try_subscribe(ws, reason='retry', sub_id=sub_id_counter[0])
                        sub_id_counter[0] += 1
                        last_sub_attempt = time.time()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mt = data.get('type', '')
                    if mt == 'ticker':
                        _parse_ticker_msg(data.get('msg', {}))
                    elif mt == 'subscribed':
                        sid = data.get('msg', {}).get('sid')
                        _log(f'WS sub confirmed, sid={sid}')
                    elif mt == 'error':
                        _log(f'WS error: {data.get("msg", {})}')

        except (websockets.exceptions.InvalidStatusCode, websockets.exceptions.InvalidStatus) as e:
            code_attr = getattr(e, 'status_code', None) or getattr(getattr(e, 'response', None), 'status_code', None)
            _log(f'WS rejected: HTTP {code_attr}')
            if code_attr in (401, 403):
                _log('WS auth failed — REST fallback')
                _WS_STATE['mode'] = 'rest_fallback'
                _WS_STATE['connected'] = False
                return
        except Exception as e:
            _log(f'WS err: {e}')

        _WS_STATE['connected'] = False
        _WS_STATE['subscribed_event'] = None
        if not BOT_STATE['running']:
            break
        _WS_STATE['reconnect_count'] += 1
        delay = min(CFG['ws_reconnect_base_sec'] * (2 ** _WS_STATE['reconnect_count']),
                    CFG['ws_reconnect_max_sec'])
        _log(f'WS reconnecting in {delay:.0f}s')
        await asyncio.sleep(delay)


def _ws_listener():
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_ws_connect())
    except Exception as e:
        _log(f'WS loop err: {e}')
    finally:
        loop.close()
    if _WS_STATE['mode'] == 'rest_fallback':
        _rest_poller()


def _rest_poller():
    _log('REST poller active (6s)')
    while BOT_STATE['running']:
        with _LOCK:
            event = TRACKED.get('event')
        if not event:
            _sleep(2); continue
        try:
            mkts = kalshi_prod.get_markets(event_ticker=event, limit=200).get('markets', [])
            now = datetime.now(timezone.utc)
            with _LOCK:
                for m in mkts:
                    tk = m.get('ticker')
                    if not tk: continue
                    yb = m.get('yes_bid'); ya = m.get('yes_ask')
                    yb = float(yb)/100 if yb is not None else None
                    ya = float(ya)/100 if ya is not None else None
                    na = (1.0 - yb) if yb is not None else None
                    nb = (1.0 - ya) if ya is not None else None
                    BOOKS[tk] = {
                        'yes_bid': yb, 'yes_ask': ya,
                        'yes_bid_qty': None, 'yes_ask_qty': None,
                        'no_bid': nb, 'no_ask': na,
                        'floor': m.get('floor_strike'),
                        'close_time': m.get('close_time'),
                        'status': (m.get('status') or '').lower(),
                        'ts': now, 'volume': m.get('volume')}
                    _queue_tick(event, tk, yb, ya, None, None, nb, na, m.get('volume'), 'rest')
                _WS_STATE['last_msg_ts'] = now
                _WS_STATE['msg_count'] += len(mkts)
        except Exception as e:
            _log(f'REST poll err: {e}')
        _sleep(6.0)


# ── Event tracker ─────────────────────────────────────────────────

def _scan_for_event():
    """One pass over all configured series; returns (event_ticker, close_time)
    or (None, None). Logs candidate counts."""
    from dateutil import parser as dtparser
    now = datetime.now(timezone.utc)
    best_event = None; best_close = None; total_candidates = 0
    for series in CFG['event_series']:
        try:
            resp = kalshi_prod.get_events(series_ticker=series, status='open', limit=50)
        except Exception as e:
            _log(f'tracker fetch err ({series}): {e}')
            continue
        events = resp.get('events', [])
        total_candidates += len(events)
        for ev in events:
            et = ev.get('event_ticker', '')
            ct_str = None
            for m in ev.get('markets', []) or []:
                ct_str = m.get('close_time') or m.get('expected_expiration_time')
                if ct_str: break
            if not ct_str:
                try:
                    mr = kalshi_prod.get_markets(event_ticker=et, status='open', limit=5)
                    for m in mr.get('markets', []):
                        ct_str = m.get('close_time') or m.get('expected_expiration_time')
                        if ct_str: break
                except Exception:
                    pass
            if not ct_str: continue
            ct = dtparser.isoparse(ct_str)
            if ct.tzinfo is None: ct = ct.replace(tzinfo=timezone.utc)
            ttl = (ct - now).total_seconds()
            if ttl < 120 or ttl > 7200: continue
            if best_close is None or ct < best_close:
                best_event = et; best_close = ct
    return best_event, best_close, total_candidates


def _apply_tracked_event(best_event, best_close):
    """Update TRACKED + trigger resubscribe if event changed.

    If best_event is None (e.g. transient API failure), keep the current
    tracked event UNLESS its close_time has already passed."""
    with _LOCK:
        old = TRACKED.get('event')
        old_close = TRACKED.get('close_time')
        new_is_better = best_event and best_event != old
        # Keep current event if API gave us nothing but our event is still alive
        if best_event is None and old is not None and old_close is not None:
            if (old_close - datetime.now(timezone.utc)).total_seconds() > 0:
                TRACKED['refreshed_at'] = datetime.now(timezone.utc)
                return  # keep what we have
        if new_is_better:
            _log(f'Tracking: {best_event} closes {best_close}')
            BOOKS.clear()
            _WS_STATE['needs_resubscribe'] = True
        TRACKED['event'] = best_event
        TRACKED['close_time'] = best_close
        TRACKED['refreshed_at'] = datetime.now(timezone.utc)
    if new_is_better:
        _seed_books_rest(best_event)


def _event_tracker():
    while BOT_STATE['running']:
        try:
            best_event, best_close, n_cand = _scan_for_event()
            if best_event is None and n_cand > 0:
                _log(f'tracker: {n_cand} events found but none in 2min–2hr window')
            elif best_event is None:
                _log(f'tracker: no open events for {CFG["event_series"]}')
            _apply_tracked_event(best_event, best_close)
        except Exception as e:
            _log(f'tracker err: {e}')
        _sleep(60)


# ── Helpers ───────────────────────────────────────────────────────

def _sleep(secs):
    end = time.time() + secs
    while time.time() < end and BOT_STATE['running']:
        time.sleep(0.2)


def _causal_sigma():
    # Annualized BTC vol from recent spot history (causal — only past data)
    with _LOCK:
        hist = list(SPOT['history'])
    if len(hist) < CFG['sigma_min_points']:
        return None
    prices = np.array([p for _, p in hist], dtype=float)
    lr = np.diff(np.log(prices))
    if len(lr) < 5: return None
    s = float(np.std(lr) * np.sqrt(525960))
    if not np.isfinite(s) or s <= 0: return None
    return s


print('Data pipeline ready. tick_stats() to inspect collected data.')
