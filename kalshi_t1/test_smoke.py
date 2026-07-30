"""Smoke test: scan_t1() finds arb opportunities; portfolio settles all 3 BTC outcomes."""
from datetime import datetime, timezone, timedelta
from strategy import scan_t1
from portfolio import T1Portfolio


def mkt(ticker, strike, yes_bid, yes_ask, bid_qty=20, ask_qty=20):
    return {
        'ticker': ticker, 'strike': float(strike),
        'event_ticker': 'TEST', 'status': 'active',
        'yes_bid': yes_bid, 'yes_ask': yes_ask,
        'no_bid': 1 - yes_ask, 'no_ask': 1 - yes_bid,
        'yes_bid_qty': bid_qty, 'yes_ask_qty': ask_qty,
        'close_time': datetime.now(timezone.utc) + timedelta(minutes=30),
    }


def test_arb_fires():
    print('=== T1: monotonicity violation (K_lo=80000 K_hi=80100, edge 5¢) ===')
    # Market should have higher YES bid at LOWER strike normally.
    # Violation: lower strike ask=0.30, higher strike bid=0.40 — arb gap
    markets = {
        'A-T80000': mkt('A-T80000', 80000, 0.28, 0.30),   # cheap to buy YES here
        'A-T80100': mkt('A-T80100', 80100, 0.40, 0.42),   # we'd want to "sell YES" → buy NO at 1-0.40=0.60
    }
    sigs = scan_t1(markets, spot=80050, spot_move_30s=10, bankroll_avail=100)
    print(f'  signals: {len(sigs)}')
    for s in sigs:
        print(f'    lo={s.mkt_lo} ask={s.ask_lo}  hi={s.mkt_hi} bid={s.bid_hi}  '
              f'edge={s.net_edge*100:+.2f}¢  qty={s.qty}  cost=${s.pair_cost*s.qty:.2f}')
    assert len(sigs) == 1
    assert sigs[0].mkt_lo == 'A-T80000'
    assert sigs[0].mkt_hi == 'A-T80100'
    print('  PASS\n')


def test_no_arb_when_monotone():
    print('=== T1: monotone (no arb expected) ===')
    markets = {
        'A-T80000': mkt('A-T80000', 80000, 0.55, 0.60),   # YES bid at lower strike is HIGHER
        'A-T80100': mkt('A-T80100', 80100, 0.45, 0.50),   # YES bid at higher strike is LOWER
    }
    sigs = scan_t1(markets, spot=80050, spot_move_30s=10, bankroll_avail=100)
    print(f'  signals: {len(sigs)} (expected 0)')
    assert len(sigs) == 0
    print('  PASS\n')


def test_calm_filter_blocks_volatile():
    print('=== T1: calm filter blocks when spot moved >$30 in 30s ===')
    markets = {
        'A-T80000': mkt('A-T80000', 80000, 0.28, 0.30),
        'A-T80100': mkt('A-T80100', 80100, 0.40, 0.42),
    }
    sigs = scan_t1(markets, spot=80050, spot_move_30s=60, bankroll_avail=100)
    print(f'  signals: {len(sigs)} (expected 0 — spot moved $60)')
    assert len(sigs) == 0
    print('  PASS\n')


def test_settlement_outcomes():
    print('=== T1: settlement payouts for all 3 BTC outcomes ===')
    base_sig = {
        'pair_id': 'p1', 'event_ticker': 'TEST',
        'mkt_lo': 'A-T80000', 'mkt_hi': 'A-T80100',
        'strike_lo': 80000.0, 'strike_hi': 80100.0,
        'qty': 5, 'ask_lo': 0.30, 'bid_hi': 0.40,
        'net_edge': 0.08,   # gross 10¢ - fees ~2¢
        'close_time': datetime.now(timezone.utc).isoformat(),
    }
    # Cost: qty=5 × (0.30 + fee + 0.60 + fee) = 5 × (0.90 + 2×fee)
    # ≈ 5 × 0.92 = $4.60 total

    # Case 1: BTC < strike_lo (= 79900) — only leg_hi wins
    pf = T1Portfolio(); pf.cash = 100
    pos = pf.open_position(base_sig)
    cost = pos.total_cost
    print(f'  cost = ${cost:.3f}')
    settled = pf.settle_position(pos.position_id, btc_at_close=79900)
    expected = 5 * 1.0 - cost   # leg_hi wins 5×$1, leg_lo loses
    print(f'  BTC=79900 (below low): pnl=${settled.realized_pnl:+.3f}  expected=${expected:+.3f}')
    assert abs(settled.realized_pnl - expected) < 0.01

    # Case 2: BTC between strikes (=80050) — BOTH win
    pf = T1Portfolio(); pf.cash = 100
    pos = pf.open_position(base_sig)
    settled = pf.settle_position(pos.position_id, btc_at_close=80050)
    expected = 5 * 2.0 - cost   # both legs win $1 each × 5
    print(f'  BTC=80050 (between):   pnl=${settled.realized_pnl:+.3f}  expected=${expected:+.3f}')
    assert abs(settled.realized_pnl - expected) < 0.01

    # Case 3: BTC > strike_hi (=80200) — only leg_lo wins
    pf = T1Portfolio(); pf.cash = 100
    pos = pf.open_position(base_sig)
    settled = pf.settle_position(pos.position_id, btc_at_close=80200)
    expected = 5 * 1.0 - cost
    print(f'  BTC=80200 (above high): pnl=${settled.realized_pnl:+.3f}  expected=${expected:+.3f}')
    assert abs(settled.realized_pnl - expected) < 0.01
    print('  PASS\n')


def test_risk_free_min_payout():
    print('=== T1: min payout is always positive (risk-free invariant) ===')
    sig = {
        'pair_id': 'p2', 'event_ticker': 'TEST',
        'mkt_lo': 'A-T80000', 'mkt_hi': 'A-T80100',
        'strike_lo': 80000.0, 'strike_hi': 80100.0,
        'qty': 5, 'ask_lo': 0.30, 'bid_hi': 0.40,
        'net_edge': 0.08,
        'close_time': datetime.now(timezone.utc).isoformat(),
    }
    pf = T1Portfolio(); pf.cash = 100
    pos = pf.open_position(sig)
    # The MIN payout across the 3 outcomes is qty × $1 (cases 1 and 3)
    # If qty × 1 > total_cost, the trade is risk-free
    min_payout = pos.qty * 1.0
    print(f'  qty={pos.qty}  total_cost=${pos.total_cost:.3f}  min_payout=${min_payout:.2f}  '
          f'min_pnl=${min_payout - pos.total_cost:+.3f}')
    assert min_payout > pos.total_cost   # risk-free check
    print('  PASS\n')


if __name__ == '__main__':
    test_arb_fires()
    test_no_arb_when_monotone()
    test_calm_filter_blocks_volatile()
    test_settlement_outcomes()
    test_risk_free_min_payout()
    print('All T1 smoke tests passed.')
