"""Bayesian Price Predictor (BPP) — Glosten-Milgrom-style on Kalshi BTC binaries.

Framework (Glosten-Milgrom 1985 + Easley-O'Hara 1987):
  - True value V ∈ {0, 1} (NO or YES wins at settlement)
  - At each tick, a trade arrives. The trade is from an INFORMED trader with
    prob α_type (depends on trade direction), or UNINFORMED with prob 1-α_type.
  - Informed traders' actions correlate with V:
      Informed bullish-on-YES → buy_yes_taker  (or sell_no_taker)
      Informed bearish-on-YES → sell_yes_taker (or buy_no_taker)
  - Uninformed are 50/50.

After observing a sequence of trades, the posterior P(V=1 | history) updates
via Bayes. If this posterior diverges from the market mid by > fee, trade.

Bayesian update for one trade observation:

  P(V=1 | observed trade type T) = P(T | V=1) × P(V=1) / P(T)

  P(buy_yes_taker | V=1):   α × 1.0 + (1-α) × 0.5   (informed buys, unif 50/50)
  P(buy_yes_taker | V=0):   α × 0.0 + (1-α) × 0.5   (informed wouldn't buy YES if V=0)

  P(sell_yes_taker | V=1):  α × 0.0 + (1-α) × 0.5
  P(sell_yes_taker | V=0):  α × 1.0 + (1-α) × 0.5

  (analogous for buy_no / sell_no with informed bearish-on-YES = sell_yes)

Empirical calibration of α per trade type:
  Use realized win-rate of YES given each trade type as the per-type α.
"""
from __future__ import annotations
import math
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, '/Users/rithvikijju/edge-bot')
from informed_flow_research import extract_clean_trades
from fade_aggressor_settle import get_market_close_and_settle


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


# Map each trade type to its "true value indicator" direction
# +1 means trade type is more likely when V=YES wins
# -1 means trade type is more likely when V=NO wins
SIGN_BY_TYPE = {
    'buy_yes_taker':  +1,     # informed bull on YES
    'sell_no_taker':  +1,     # informed bull on YES (sold NO short)
    'sell_yes_taker': -1,     # informed bear on YES
    'buy_no_taker':   -1,     # informed bear on YES
}


def calibrate_alpha_per_type(train_trades, train_settle):
    """For each trade type, compute empirical P(YES wins | trade type).
    Return per-type (effective_sign, α) where:
      effective_sign = +1 if trade predicts YES, -1 if predicts NO
      α              = |2 × win_rate - 1| (strength of prediction)

    Anti-informed types (where the trade's "implied direction" is actually
    backward) get a flipped effective_sign.
    """
    merged = train_trades.merge(
        train_settle[['market_ticker', 'yes_settles']],
        on='market_ticker', how='inner')
    print('  Per-trade-type calibration (training data):')
    print(f'  {"type":<18} {"n":<8} {"P(YES|T)":<10} {"naive_sign":<11} {"eff_sign":<10} {"α":<8}  {"interpretation":<30}')
    print('  ' + '-' * 100)
    calibration = {}
    for t, naive_sign in SIGN_BY_TYPE.items():
        sub = merged[merged['classify'] == t]
        if len(sub) < 5:
            print(f'  {t:<18} {len(sub):<8} {"too few":<15}')
            continue
        p_yes = sub['yes_settles'].mean()
        # Effective predictor: if P(YES|T) > 0.5, this trade predicts YES (eff_sign=+1).
        # Otherwise predicts NO (eff_sign=-1).
        if p_yes > 0.5:
            eff_sign = +1
            strength = 2 * p_yes - 1
        else:
            eff_sign = -1
            strength = 2 * (1 - p_yes) - 1
        # Flag anti-informed if naive_sign != eff_sign
        anti = '(anti-informed)' if naive_sign != eff_sign else '(informed)'
        calibration[t] = (eff_sign, strength)
        print(f'  {t:<18} {len(sub):<8} {p_yes*100:>5.1f}%    {naive_sign:>+5}       '
              f'{eff_sign:>+5}      {strength:>5.3f}  {anti}')
    return calibration


def bayes_update(p_yes_prior, trade_type, calibration):
    """Update P(YES wins) given an observed trade of trade_type.

    calibration[trade_type] = (effective_sign, strength)
    """
    if trade_type not in calibration:
        return p_yes_prior
    eff_sign, a = calibration[trade_type]
    # P(trade_type | YES=1) and P(trade_type | YES=0)
    if eff_sign == +1:
        p_t_v1 = a * 1.0 + (1 - a) * 0.5
        p_t_v0 = a * 0.0 + (1 - a) * 0.5
    else:
        p_t_v1 = a * 0.0 + (1 - a) * 0.5
        p_t_v0 = a * 1.0 + (1 - a) * 0.5
    num = p_t_v1 * p_yes_prior
    den = p_t_v1 * p_yes_prior + p_t_v0 * (1 - p_yes_prior)
    if den < 1e-9:
        return p_yes_prior
    return num / den


def simulate_predictor(test_trades, test_settle, calibration, edge_threshold=0.02):
    """Simulate the BPP strategy on test data:
      For each market, walk trades chronologically.
      Maintain rolling posterior P(YES) starting from market mid at first trade.
      At each new trade, compare posterior to current market mid (use prev_yes_ask
      as a proxy if we had to estimate mid pre-trade). If |posterior - mid| > threshold + fee,
      trade in posterior direction.
    """
    merged = test_trades.merge(
        test_settle[['market_ticker', 'yes_settles']],
        on='market_ticker', how='inner')
    # Sort
    merged = merged.sort_values(['market_ticker', 'ts']).reset_index(drop=True)

    trades = []
    posteriors = {}  # market_ticker -> current posterior P(YES)
    traded_markets = set()

    for _, row in merged.iterrows():
        mkt = row['market_ticker']
        mid_now = (row['prev_yes_ask'] + row['prev_yes_bid']) / 2.0
        if pd.isna(mid_now) or mid_now <= 0 or mid_now >= 1: continue

        # Initialize posterior at first trade
        if mkt not in posteriors:
            posteriors[mkt] = mid_now

        # Update posterior with this new trade
        p_new = bayes_update(posteriors[mkt], row["classify"], calibration)
        posteriors[mkt] = p_new

        # Decision: do we trade?
        if mkt in traded_markets: continue  # only one bet per market

        # Posterior says YES is more likely than market is pricing
        edge_buy_yes = p_new - row['prev_yes_ask'] - kalshi_fee(row['prev_yes_ask'])
        edge_buy_no  = (1 - p_new) - (1 - row['prev_yes_bid']) - kalshi_fee(1 - row['prev_yes_bid'])

        if max(edge_buy_yes, edge_buy_no) < edge_threshold:
            continue

        if edge_buy_yes >= edge_buy_no:
            # Buy YES
            entry = row['prev_yes_ask']
            fee = kalshi_fee(entry)
            pnl = row['yes_settles'] - entry - fee
            side = 'yes'
        else:
            entry = 1 - row['prev_yes_bid']
            fee = kalshi_fee(entry)
            pnl = (1 - row['yes_settles']) - entry - fee
            side = 'no'

        trades.append({
            'ts': row['ts'], 'market': mkt, 'side': side,
            'entry': entry, 'fee': fee, 'posterior': p_new,
            'mid_at_signal': mid_now, 'edge_pred': max(edge_buy_yes, edge_buy_no),
            'pnl': pnl, 'yes_settles': row['yes_settles'],
            'trigger_type': row['classify'],
        })
        traded_markets.add(mkt)

    if not trades:
        return None
    return pd.DataFrame(trades)


if __name__ == '__main__':
    print('=' * 90)
    print('BAYESIAN PRICE PREDICTOR (BPP) — Glosten-Milgrom on Kalshi BTC binaries')
    print('=' * 90)
    print('\nExtracting clean trade tape + settlements...')
    trades = extract_clean_trades()
    settle = get_market_close_and_settle()
    print(f'  trades: {len(trades):,}, settled markets: {len(settle):,}')

    # Chronological 50/50 split
    trades['ts'] = pd.to_datetime(trades['ts'], utc=True)
    trades_sorted = trades.sort_values('ts').reset_index(drop=True)
    mid_ts = trades_sorted['ts'].quantile(0.5)
    train = trades_sorted[trades_sorted['ts'] < mid_ts]
    test  = trades_sorted[trades_sorted['ts'] >= mid_ts]
    print(f'\nTrain: {len(train):,}  Test: {len(test):,}')

    # Calibrate alpha
    print('\n─── Step 1: Calibrate α per trade type (training data) ───')
    calibration = calibrate_alpha_per_type(train, settle)
    if not calibration:
        print('Calibration failed.')
        sys.exit(0)

    # Run on test
    print(f'\n─── Step 2: BPP simulation on OOS test data ───')
    for thr in [0.005, 0.01, 0.02, 0.05, 0.10]:
        result = simulate_predictor(test, settle, calibration, edge_threshold=thr)
        if result is None or len(result) == 0:
            print(f'  threshold={thr}: 0 trades')
            continue
        n = len(result)
        wins = (result['pnl'] > 0).sum()
        avg = result['pnl'].mean()
        total = result['pnl'].sum()
        sd = result['pnl'].std()
        t_stat = avg / (sd / math.sqrt(n)) if sd > 0 else 0
        # Calibration check: did predicted edge predict realized PnL?
        cal_corr = result[['edge_pred', 'pnl']].corr().iloc[0,1]
        print(f'  threshold={thr:>5.3f}: n={n:>4}  win={wins/n*100:>4.1f}%  '
              f'avg=${avg:>+7.4f}  total=${total:>+6.2f}  '
              f't-stat={t_stat:>+5.2f}  pred-real_corr={cal_corr:+.3f}')

    print()
    print('─── Step 3: Breakdown of OOS triggers by type ───')
    result = simulate_predictor(test, settle, calibration, edge_threshold=0.02)
    if result is not None and len(result) > 0:
        for t in result['trigger_type'].unique():
            sub = result[result['trigger_type'] == t]
            if len(sub) < 5: continue
            print(f'  {t:<20}  n={len(sub):>4}  win={(sub["pnl"]>0).mean()*100:>5.1f}%  '
                  f'avg=${sub["pnl"].mean():+.4f}  total=${sub["pnl"].sum():+.2f}')
