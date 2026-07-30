"""Smoke test for K6 strategy core — drives scan_k6() with synthetic market data
to verify the signal logic + portfolio plumbing works end-to-end.

Run: python test_smoke.py
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from strategy import scan_k6
from portfolio import Portfolio
from config import CFG


def build_market(ticker, strike, yes_bid, yes_ask, no_bid, no_ask, close_in_sec):
    return {
        'ticker': ticker,
        'event_ticker': 'TEST-EVENT',
        'strike': float(strike),
        'close_time': datetime.now(timezone.utc) + timedelta(seconds=close_in_sec),
        'status': 'active',
        'yes_bid': yes_bid, 'yes_ask': yes_ask,
        'no_bid':  no_bid,  'no_ask':  no_ask,
        'yes_ask_qty': 20, 'no_ask_qty': 20,
        'yes_bid_qty': 20, 'no_bid_qty': 20,
    }


def test_yes_side_fires():
    print('=== YES-side bucket A1 (0-5min, spot +$100 above strike, high vol) ===')
    spot = 80100
    markets = {
        'K-T80000': build_market('K-T80000', 80000, 0.82, 0.85, 0.14, 0.18, 200),
    }
    rv = 0.50  # mid-high vol → YES fires
    sigs = scan_k6(markets, spot, rv, bankroll_avail=100.0)
    print(f'  signals: {len(sigs)}')
    for s in sigs:
        print(f'    {s.bucket} {s.market_ticker} {s.side} qty={s.qty} '
              f'@${s.price:.3f} edge=${s.expected_edge*100:+.2f}¢ '
              f'dist=${s.spot_dist:+.0f}')
    assert len(sigs) == 1, f'expected 1 signal, got {len(sigs)}'
    s = sigs[0]
    assert s.side == 'yes'
    assert s.bucket == 'A1'
    assert s.qty >= 1
    print('  PASS\n')


def test_no_side_fires():
    print('=== NO-side bucket A1n (0-5min, spot $100 BELOW strike, low vol) ===')
    spot = 79900
    markets = {
        'K-T80000': build_market('K-T80000', 80000, 0.14, 0.18, 0.82, 0.85, 200),
    }
    rv = 0.20  # low vol → NO fires
    sigs = scan_k6(markets, spot, rv, bankroll_avail=100.0)
    print(f'  signals: {len(sigs)}')
    for s in sigs:
        print(f'    {s.bucket} {s.market_ticker} {s.side} qty={s.qty} '
              f'@${s.price:.3f} edge=${s.expected_edge*100:+.2f}¢ '
              f'dist=${s.spot_dist:+.0f}')
    assert len(sigs) == 1, f'expected 1 signal, got {len(sigs)}'
    assert sigs[0].side == 'no'
    assert sigs[0].bucket == 'A1n'
    print('  PASS\n')


def test_yes_blocked_by_low_vol():
    print('=== YES side blocked by low vol (rv below k14_yes_min_rv) ===')
    spot = 80100
    markets = {
        'K-T80000': build_market('K-T80000', 80000, 0.82, 0.85, 0.14, 0.18, 200),
    }
    rv = 0.20  # below 0.33
    sigs = scan_k6(markets, spot, rv, bankroll_avail=100.0)
    print(f'  signals: {len(sigs)} (expected 0)')
    assert len(sigs) == 0
    print('  PASS\n')


def test_distance_out_of_band():
    print('=== Distance > $500 (out of K6 band) ===')
    spot = 80600
    markets = {
        'K-T80000': build_market('K-T80000', 80000, 0.95, 0.98, 0.02, 0.05, 200),
    }
    sigs = scan_k6(markets, spot, 0.50, bankroll_avail=100.0)
    print(f'  signals: {len(sigs)} (expected 0)')
    assert len(sigs) == 0
    print('  PASS\n')


def test_portfolio_open_settle():
    print('=== Portfolio: open, then settle a winning YES position ===')
    pf = Portfolio(mode='paper')
    pf.cash = 100.0
    sig = {
        'market_ticker': 'K-T80000', 'event_ticker': 'TEST',
        'strike': 80000.0, 'side': 'yes', 'bucket': 'A1',
        'spot': 80100.0, 'spot_dist': 100.0, 'rv_15m_annual': 0.50,
        'close_time': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    }
    pos = pf.open_position(sig, fill_price=0.85, fill_qty=5)
    assert pos is not None
    print(f'  opened: qty={pos.qty} @${pos.entry_price:.3f} fee=${pos.entry_fee:.3f}')
    print(f'  cash after open: ${pf.cash:.2f}')
    assert pf.cash < 100.0
    # Now settle (BTC at 80100 — above 80000 strike → YES wins)
    settled = pf.settle_position(pos.position_id, btc_at_close=80100)
    print(f'  settled: settle_value={settled.settle_value} realized=${settled.realized_pnl:+.2f}')
    print(f'  cash after settle: ${pf.cash:.2f}  realized: ${pf.realized_pnl:+.2f}')
    assert settled.settle_value == 1.0
    assert pf.realized_pnl > 0
    print('  PASS\n')


def test_portfolio_open_settle_loss():
    print('=== Portfolio: open and lose a YES position ===')
    pf = Portfolio(mode='paper')
    pf.cash = 100.0
    sig = {
        'market_ticker': 'K-T80000', 'event_ticker': 'TEST',
        'strike': 80000.0, 'side': 'yes', 'bucket': 'A1',
        'spot': 80100.0, 'spot_dist': 100.0, 'rv_15m_annual': 0.50,
        'close_time': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    }
    pos = pf.open_position(sig, fill_price=0.85, fill_qty=5)
    # BTC ends BELOW strike (loss)
    settled = pf.settle_position(pos.position_id, btc_at_close=79500)
    assert settled.settle_value == 0.0
    assert pf.realized_pnl < 0
    print(f'  Loss: ${settled.realized_pnl:+.2f}')
    print(f'  Final cash: ${pf.cash:.2f}  (started 100, lost ~$4.30)')
    print('  PASS\n')


def test_portfolio_caps():
    print('=== Portfolio caps: refuses to open when cash insufficient ===')
    pf = Portfolio(mode='paper')
    pf.cash = 1.0  # not enough for 5 contracts at $0.85
    sig = {'market_ticker': 'K-T80000', 'event_ticker': 'T', 'strike': 80000.0,
           'side': 'yes', 'bucket': 'A1', 'spot': 80100, 'spot_dist': 100,
           'rv_15m_annual': 0.50,
           'close_time': datetime.now(timezone.utc).isoformat()}
    pos = pf.open_position(sig, fill_price=0.85, fill_qty=5)
    assert pos is None
    print(f'  refused as expected (cash {pf.cash})')
    print('  PASS\n')


if __name__ == '__main__':
    test_yes_side_fires()
    test_no_side_fires()
    test_yes_blocked_by_low_vol()
    test_distance_out_of_band()
    test_portfolio_open_settle()
    test_portfolio_open_settle_loss()
    test_portfolio_caps()
    print('All smoke tests passed.')
