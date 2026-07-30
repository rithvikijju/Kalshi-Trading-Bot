# § 4 — Execution: Orders, Positions, Settlement
#
# Tracks live signal-flow telemetry so the dashboard can show what's being
# scanned vs. executed vs. skipped per cycle.

from collections import deque

SIGNAL_FLOW = {
    'cycles': 0,
    'totals': {'t1_seen': 0, 't2_seen': 0, 't3_seen': 0,
               't1_exec': 0, 't2_exec': 0, 't3_exec': 0},
    'last_cycle': {'t1_seen': 0, 't2_seen': 0, 't3_seen': 0,
                   't1_exec': 0, 't2_exec': 0, 't3_exec': 0},
    'skip_reasons': {},   # reason -> count
    'recent': deque(maxlen=20),  # tuples of (ts_str, tier, ticker, status, detail)
}


def _flow_record(tier: int, ticker: str, status: str, detail: str = ''):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    SIGNAL_FLOW['recent'].append((ts, tier, ticker, status, detail))
    if status.startswith('skip:'):
        reason = status[5:]
        SIGNAL_FLOW['skip_reasons'][reason] = SIGNAL_FLOW['skip_reasons'].get(reason, 0) + 1

TRADES_INIT_SQL = (
    "CREATE TABLE IF NOT EXISTS trades ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
    " tier INTEGER NOT NULL, pair_id TEXT,"
    " event_ticker TEXT, market_ticker TEXT NOT NULL,"
    " side TEXT NOT NULL, action TEXT NOT NULL,"
    " contracts INTEGER NOT NULL, entry_price REAL NOT NULL,"
    " edge_cents REAL, fair_value REAL, spot REAL, sigma REAL,"
    " secs_remaining REAL, mode TEXT DEFAULT 'paper',"
    " order_id TEXT, client_order_id TEXT,"
    " settled INTEGER DEFAULT 0, settlement_result TEXT,"
    " pnl REAL, settled_at TEXT);"
    "CREATE INDEX IF NOT EXISTS idx_trades_settled ON trades(settled);"
    "CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_ticker, settled);"
    "CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair_id);"
)


def _trades_conn():
    conn = sqlite3.connect(CFG['trades_db_path'])
    conn.row_factory = sqlite3.Row
    return conn


def _init_trades_db():
    conn = _trades_conn()
    conn.executescript(TRADES_INIT_SQL)
    conn.commit(); conn.close()

_init_trades_db()


def _record_trade(tier, market_ticker, side, contracts, entry_price,
                  edge_cents=None, fair_value=None, spot=None, sigma=None,
                  secs_remaining=None, pair_id=None, order_id=None,
                  client_order_id=None, event_ticker=None):
    conn = _trades_conn()
    cur = conn.execute(
        'INSERT INTO trades(ts, tier, pair_id, event_ticker, market_ticker, side, action,'
        ' contracts, entry_price, edge_cents, fair_value, spot, sigma, secs_remaining,'
        ' mode, order_id, client_order_id)'
        ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (datetime.now(timezone.utc).isoformat(), tier, pair_id,
         event_ticker or TRACKED.get('event'), market_ticker, side, 'buy',
         contracts, entry_price, edge_cents, fair_value, spot, sigma, secs_remaining,
         CFG['mode'], order_id, client_order_id))
    trade_id = cur.lastrowid
    conn.commit(); conn.close()
    return trade_id


def _open_positions():
    conn = _trades_conn()
    rows = [dict(r) for r in conn.execute('SELECT * FROM trades WHERE settled = 0').fetchall()]
    conn.close()
    return rows


def _market_is_open(ticker):
    conn = _trades_conn()
    n = conn.execute('SELECT COUNT(*) FROM trades WHERE settled=0 AND market_ticker=?',
                     (ticker,)).fetchone()[0]
    conn.close()
    return n > 0


def _open_exposure():
    conn = _trades_conn()
    row = conn.execute('SELECT COALESCE(SUM(entry_price * contracts), 0) FROM trades WHERE settled=0').fetchone()
    conn.close()
    return float(row[0])


def _today_pnl():
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = _trades_conn()
    row = conn.execute('SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE settled=1 AND settled_at >= ?',
                       (today,)).fetchone()
    conn.close()
    return float(row[0])


def _risk_preflight(market_tickers, total_cost):
    if _today_pnl() < CFG['daily_loss_limit']:
        return False, f'daily loss limit hit (${_today_pnl():.2f})'
    for tk in market_tickers:
        if _market_is_open(tk):
            return False, f'already open on {tk}'
    open_pos = _open_positions()
    if len(open_pos) >= CFG['max_concurrent_positions']:
        return False, f'open positions ({len(open_pos)}) >= cap'
    exp = _open_exposure()
    if exp + total_cost > CFG['max_total_exposure']:
        return False, f'exposure ${exp+total_cost:.0f} > cap ${CFG["max_total_exposure"]}'
    # Paper-account cash check (if paper mode + paper module loaded)
    try:
        if CFG['mode'] == 'paper' and not paper_has_cash(total_cost):
            return False, f'paper cash ${PAPER_ACCOUNT["cash"]:.2f} < cost ${total_cost:.2f}'
    except NameError:
        pass
    return True, ''


def _place_one(market_ticker, side, qty, limit_price):
    # Returns (order_id, client_order_id) or (None, None)
    if CFG['mode'] != 'live' or kalshi_live is None:
        return 'paper', f'paper-{uuid.uuid4().hex[:12]}'
    buf = CFG['order_buffer_cents'] / 100.0
    p = min(0.99, limit_price + buf)
    cents = int(round(p * 100))
    resp = kalshi_live.place_order(
        ticker=market_ticker, side=side, action='buy',
        count=qty, yes_price_cents=cents,
        expiration_sec=CFG['order_expiration_sec'])
    if 'error' in resp:
        _log(f'ORDER FAIL {resp.get("status","")}: {resp["error"][:120]}')
        return None, None
    o = resp.get('order', {})
    return o.get('order_id'), o.get('client_order_id')


def execute_t1(sig):
    cost_per_unit = sig.ask_lo + (1.0 - sig.bid_hi)
    total_cost = cost_per_unit * sig.qty
    ok, why = _risk_preflight([sig.mkt_lo, sig.mkt_hi], total_cost)
    if not ok:
        _log(f'T1 SKIP {sig.mkt_lo}/{sig.mkt_hi}: {why}')
        _flow_record(1, f'{sig.mkt_lo[-12:]}↔{sig.mkt_hi[-12:]}', f'skip:{why}',
                     f'edge={sig.net_edge_cents:.1f}c qty={sig.qty}')
        return False

    oid_a, cid_a = _place_one(sig.mkt_lo, 'yes', sig.qty, sig.ask_lo)
    if oid_a is None:
        _flow_record(1, sig.mkt_lo[-12:], 'skip:order_failed_leg1', '')
        return False
    _record_trade(tier=1, market_ticker=sig.mkt_lo, side='yes',
                  contracts=sig.qty, entry_price=sig.ask_lo,
                  edge_cents=sig.net_edge_cents, pair_id=sig.pair_id,
                  order_id=oid_a, client_order_id=cid_a)

    oid_b, cid_b = _place_one(sig.mkt_hi, 'no', sig.qty, 1.0 - sig.bid_hi)
    if oid_b is None:
        _log(f'T1 PARTIAL: leg1 filled, leg2 failed. MANUAL UNWIND on {sig.mkt_lo}')
        _flow_record(1, sig.mkt_hi[-12:], 'skip:order_failed_leg2',
                     'MANUAL UNWIND REQUIRED')
        return False
    _record_trade(tier=1, market_ticker=sig.mkt_hi, side='no',
                  contracts=sig.qty, entry_price=1.0 - sig.bid_hi,
                  edge_cents=sig.net_edge_cents, pair_id=sig.pair_id,
                  order_id=oid_b, client_order_id=cid_b)

    try: paper_open_trade(total_cost)
    except NameError: pass
    BOT_STATE['trades_this_session'] += 1
    SIGNAL_FLOW['totals']['t1_exec'] += 1
    SIGNAL_FLOW['last_cycle']['t1_exec'] += 1
    _flow_record(1, f'{sig.mkt_lo[-12:]}+{sig.mkt_hi[-12:]}', 'EXEC',
                 f'edge={sig.net_edge_cents:.1f}c qty={sig.qty}')
    _log(f'T1 PAIR {sig.pair_id[-6:]}: '
         f'BUY {sig.mkt_lo} yes x{sig.qty}@{sig.ask_lo:.2f} + '
         f'BUY {sig.mkt_hi} no x{sig.qty}@{(1-sig.bid_hi):.2f}  edge={sig.net_edge_cents:.1f}c')
    return True


def execute_t2(sig):
    cost = sig.price * sig.qty
    ok, why = _risk_preflight([sig.ticker], cost)
    if not ok:
        _log(f'T2 SKIP {sig.ticker}: {why}')
        _flow_record(2, sig.ticker[-16:], f'skip:{why}',
                     f'{sig.side} @${sig.price:.2f} edge={sig.edge_cents:.1f}c')
        return False
    oid, cid = _place_one(sig.ticker, sig.side, sig.qty, sig.price)
    if oid is None:
        _flow_record(2, sig.ticker[-16:], 'skip:order_failed', '')
        return False
    _record_trade(tier=2, market_ticker=sig.ticker, side=sig.side,
                  contracts=sig.qty, entry_price=sig.price,
                  edge_cents=sig.edge_cents, fair_value=sig.fair_value,
                  spot=SPOT.get('price'), sigma=sig.sigma,
                  secs_remaining=sig.secs_remaining,
                  order_id=oid, client_order_id=cid)
    try: paper_open_trade(cost)
    except NameError: pass
    BOT_STATE['trades_this_session'] += 1
    SIGNAL_FLOW['totals']['t2_exec'] += 1
    SIGNAL_FLOW['last_cycle']['t2_exec'] += 1
    _flow_record(2, sig.ticker[-16:], 'EXEC',
                 f'{sig.side} x{sig.qty} @${sig.price:.2f} edge={sig.edge_cents:.1f}c')
    _log(f'T2 ENTRY: {sig.ticker} {sig.side} x{sig.qty} @ ${sig.price:.2f}  '
         f'fair={sig.fair_value:.3f}  edge={sig.edge_cents:.1f}c')
    return True


def execute_t3(sig):
    cost = sig.no_price * sig.qty
    ok, why = _risk_preflight([sig.ticker], cost)
    if not ok:
        _log(f'T3 SKIP {sig.ticker}: {why}')
        _flow_record(3, sig.ticker[-16:], f'skip:{why}',
                     f'no @${sig.no_price:.2f} dist=${sig.spot_dist:.0f}')
        return False
    oid, cid = _place_one(sig.ticker, 'no', sig.qty, sig.no_price)
    if oid is None:
        _flow_record(3, sig.ticker[-16:], 'skip:order_failed', '')
        return False
    _record_trade(tier=3, market_ticker=sig.ticker, side='no',
                  contracts=sig.qty, entry_price=sig.no_price,
                  spot=SPOT.get('price'),
                  secs_remaining=sig.secs_remaining,
                  order_id=oid, client_order_id=cid)
    try: paper_open_trade(cost)
    except NameError: pass
    BOT_STATE['trades_this_session'] += 1
    SIGNAL_FLOW['totals']['t3_exec'] += 1
    SIGNAL_FLOW['last_cycle']['t3_exec'] += 1
    _flow_record(3, sig.ticker[-16:], 'EXEC',
                 f'no x{sig.qty} @${sig.no_price:.2f} dist=${sig.spot_dist:.0f}')
    _log(f'T3 ENTRY: {sig.ticker} no x{sig.qty} @ ${sig.no_price:.2f}  '
         f'dist=${sig.spot_dist:.0f}  persist={sig.persistence_min:.0f}min')
    return True


def check_settlements():
    for pos in _open_positions():
        tk = pos['market_ticker']
        try:
            m = kalshi_prod.get_market(tk).get('market', {})
            status = (m.get('status') or '').lower()
            if status not in ('settled', 'finalized', 'closed', 'determined'):
                continue
            result = (m.get('result') or '').lower()
            if result not in ('yes', 'no'):
                continue
            payout = 1.0 if pos['side'] == result else 0.0
            pnl_per_c = payout - pos['entry_price'] - kalshi_fee(pos['entry_price'])
            pnl = pnl_per_c * pos['contracts']
            conn = _trades_conn()
            conn.execute('UPDATE trades SET settled=1, settlement_result=?, pnl=?, settled_at=? WHERE id=?',
                         (result, pnl, datetime.now(timezone.utc).isoformat(), pos['id']))
            conn.commit(); conn.close()
            tag = f'T{pos["tier"]}'
            won = pnl > 0
            # Feed SPRT (if loaded)
            try: sprt_record_outcome(pos['tier'], won)
            except NameError: pass
            # Update paper account (if loaded + we're in paper mode)
            try:
                if CFG['mode'] == 'paper':
                    paper_settle_trade(pos['contracts'], pos['entry_price'], pnl)
            except NameError: pass
            _log(f'{tag} SETTLE: {tk} {pos["side"]} -> {result}  pnl=${pnl:+.2f}')
        except Exception as e:
            _log(f'settle err {tk}: {e}')


print('Execution engine ready.')
