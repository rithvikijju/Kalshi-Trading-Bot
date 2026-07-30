# § 4b — $100 Paper Trading Account
#
# Simulates a real broker account in paper mode:
#   - Tracks cash, locked (in open positions), realized PnL
#   - Risk preflight checks `paper_has_cash(cost)` before approving trades
#   - On settlement: deduct fees, credit payout, release locked cash
#   - Persists state in output/paper_account.db so restart doesn't reset
#
# Live mode is unaffected — paper account is only consulted when CFG['mode'] == 'paper'

PAPER_DB_PATH = 'output/paper_account.db'
PAPER_STARTING_CASH = 100.00  # ← change here for different account size

PAPER_ACCOUNT = {
    'starting_cash': PAPER_STARTING_CASH,
    'cash': PAPER_STARTING_CASH,
    'locked': 0.0,
    'realized_pnl': 0.0,
    'trades_opened': 0,
    'trades_settled': 0,
    'wins': 0,
    'losses': 0,
    'peak_equity': PAPER_STARTING_CASH,
    'max_drawdown': 0.0,  # max % from peak
    'session_started_at': None,
}


def _paper_db():
    conn = sqlite3.connect(PAPER_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_paper_db():
    conn = _paper_db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS paper_state (
        key TEXT PRIMARY KEY, value REAL
    );
    CREATE TABLE IF NOT EXISTS paper_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        event_type TEXT NOT NULL,
        amount REAL,
        cash_after REAL,
        locked_after REAL,
        note TEXT
    );
    ''')
    conn.commit(); conn.close()


def _save_paper_state():
    conn = _paper_db()
    for k, v in PAPER_ACCOUNT.items():
        if isinstance(v, (int, float)):
            conn.execute('INSERT OR REPLACE INTO paper_state(key, value) VALUES(?, ?)',
                         (k, float(v)))
    conn.commit(); conn.close()


def _load_paper_state():
    conn = _paper_db()
    rows = conn.execute('SELECT key, value FROM paper_state').fetchall()
    conn.close()
    if not rows:
        return False
    for k, v in rows:
        if k in PAPER_ACCOUNT and isinstance(PAPER_ACCOUNT[k], (int, float)):
            PAPER_ACCOUNT[k] = v
    return True


def _log_paper_event(event_type: str, amount: float, note: str = ''):
    conn = _paper_db()
    conn.execute(
        'INSERT INTO paper_events(ts, event_type, amount, cash_after, locked_after, note) '
        'VALUES(?,?,?,?,?,?)',
        (datetime.now(timezone.utc).isoformat(), event_type, amount,
         PAPER_ACCOUNT['cash'], PAPER_ACCOUNT['locked'], note))
    conn.commit(); conn.close()


def paper_reset(starting_cash: float = None):
    """Reset paper account to fresh state. Use when you want a clean start."""
    if starting_cash is None:
        starting_cash = PAPER_STARTING_CASH
    PAPER_ACCOUNT.update({
        'starting_cash': starting_cash,
        'cash': starting_cash,
        'locked': 0.0,
        'realized_pnl': 0.0,
        'trades_opened': 0,
        'trades_settled': 0,
        'wins': 0,
        'losses': 0,
        'peak_equity': starting_cash,
        'max_drawdown': 0.0,
        'session_started_at': datetime.now(timezone.utc).isoformat(),
    })
    # Wipe history
    conn = _paper_db()
    conn.execute('DELETE FROM paper_state')
    conn.execute('DELETE FROM paper_events')
    conn.commit(); conn.close()
    _save_paper_state()
    _log_paper_event('reset', starting_cash, f'Reset to ${starting_cash}')
    print(f'Paper account reset: ${starting_cash:.2f} starting cash')


def paper_has_cash(cost: float) -> bool:
    """Risk preflight: can we afford this trade?"""
    return PAPER_ACCOUNT['cash'] >= cost


def paper_open_trade(cost: float):
    """Deduct cost from cash, add to locked. Called when a trade is opened."""
    PAPER_ACCOUNT['cash'] -= cost
    PAPER_ACCOUNT['locked'] += cost
    PAPER_ACCOUNT['trades_opened'] += 1
    _log_paper_event('open', -cost, f'cost ${cost:.2f}')
    _save_paper_state()


def paper_settle_trade(contracts: int, entry_price: float, pnl: float):
    """Settle a trade. pnl is the net PnL (already includes fees).
    The original cost was entry_price × contracts."""
    cost = entry_price * contracts
    PAPER_ACCOUNT['locked'] -= cost
    PAPER_ACCOUNT['cash'] += cost + pnl  # release cost + add PnL
    PAPER_ACCOUNT['realized_pnl'] += pnl
    PAPER_ACCOUNT['trades_settled'] += 1
    if pnl > 0:
        PAPER_ACCOUNT['wins'] += 1
    else:
        PAPER_ACCOUNT['losses'] += 1
    # Track peak equity + max drawdown
    equity = paper_equity_value()
    if equity > PAPER_ACCOUNT['peak_equity']:
        PAPER_ACCOUNT['peak_equity'] = equity
    dd = (PAPER_ACCOUNT['peak_equity'] - equity) / PAPER_ACCOUNT['peak_equity']
    if dd > PAPER_ACCOUNT['max_drawdown']:
        PAPER_ACCOUNT['max_drawdown'] = dd
    _log_paper_event('settle', pnl, f'pnl ${pnl:+.2f}')
    _save_paper_state()


def paper_equity_value() -> float:
    """Total account value = cash + locked (cost basis of open positions)."""
    return PAPER_ACCOUNT['cash'] + PAPER_ACCOUNT['locked']


def paper_mark_to_market() -> float:
    """Equity including unrealized PnL from open positions."""
    unrealized = 0.0
    for p in _open_positions():
        try:
            _, u_per_c, _ = _market_to_market(p)
            if u_per_c is not None:
                unrealized += u_per_c * p['contracts']
        except Exception:
            pass
    return paper_equity_value() + unrealized


def paper_stats():
    """One-shot snapshot of paper account performance."""
    starting = PAPER_ACCOUNT['starting_cash']
    equity = paper_equity_value()
    mtm = paper_mark_to_market()
    realized = PAPER_ACCOUNT['realized_pnl']
    unrealized = mtm - equity
    settled = PAPER_ACCOUNT['trades_settled']
    win_pct = (PAPER_ACCOUNT['wins'] / settled * 100) if settled else 0
    pct_return = (mtm - starting) / starting * 100
    print(f'╔══════════════════════════════════════════════════════════════════╗')
    print(f'║  PAPER ACCOUNT  —  starting ${starting:.2f}                                  ║')
    print(f'╠══════════════════════════════════════════════════════════════════╣')
    print(f'║  Cash available:       ${PAPER_ACCOUNT["cash"]:>10.2f}                          ║')
    print(f'║  Locked in positions:  ${PAPER_ACCOUNT["locked"]:>10.2f}                          ║')
    print(f'║  Cost-basis equity:    ${equity:>10.2f}                          ║')
    print(f'║  Mark-to-market:       ${mtm:>10.2f}    (unreal ${unrealized:+.2f})  ║')
    print(f'║  Realized PnL:         ${realized:>+10.2f}                          ║')
    print(f'║  Total return:         {pct_return:>+9.2f}%                           ║')
    print(f'║  Trades: opened={PAPER_ACCOUNT["trades_opened"]:>3}  settled={settled:>3}  '
          f'wins={PAPER_ACCOUNT["wins"]:>3}  losses={PAPER_ACCOUNT["losses"]:>3}  ║')
    if settled:
        print(f'║  Win rate:             {win_pct:>9.1f}%                            ║')
    print(f'║  Peak equity:          ${PAPER_ACCOUNT["peak_equity"]:>10.2f}                          ║')
    print(f'║  Max drawdown:         {PAPER_ACCOUNT["max_drawdown"]*100:>9.2f}%                           ║')
    print(f'╚══════════════════════════════════════════════════════════════════╝')


# Initialize on cell run
_init_paper_db()
if not _load_paper_state():
    paper_reset(PAPER_STARTING_CASH)
    print(f'Paper account initialized with ${PAPER_STARTING_CASH:.2f}.')
else:
    print(f'Paper account loaded: ${PAPER_ACCOUNT["cash"]:.2f} cash, '
          f'${PAPER_ACCOUNT["locked"]:.2f} locked, '
          f'realized PnL ${PAPER_ACCOUNT["realized_pnl"]:+.2f}')
    print(f'  → paper_reset() to start over.  paper_stats() for full snapshot.')
