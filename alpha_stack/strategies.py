"""
15 small-alpha conditional strategies across DIFFERENT markets and mechanisms.

Design principles (so the stack works and the test stays honest):
  * each is an ECONOMICALLY-MOTIVATED hypothesis, not a fitted curve (no free parameters to
    over-optimize) — the Renaissance "if-these-conditions-then" spirit, kept simple & robust;
  * spanning diverse markets (equity, sector, factor, vol, rates, credit, FX, commodity, crypto)
    and mechanisms (trend, reversal, carry, regime, seasonality) so cross-correlations are LOW
    — diversification is what turns small edges into a big stacked Sharpe;
  * STRICT no-lookahead: every strategy returns target weights decided at close[t]; the runner
    shifts by 1 so the position earns the close[t]->close[t+1] return. No same-bar info leaks.

Each fn returns a weights DataFrame (index=dates, cols=instruments), pre-shift.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ret(prices):
    return prices.pct_change()


def _z(s, win):
    return (s - s.rolling(win).mean()) / s.rolling(win).std()


def _norm_rows(w):
    """L1-normalize each row so gross exposure ~1 (keeps cost/vol comparable across strats)."""
    g = w.abs().sum(axis=1).replace(0, np.nan)
    return w.div(g, axis=0).fillna(0.0)


# --------------------------------------------------------------- the 15 strategies
def s01_tsmom_equity(d):
    """Time-series momentum: long index ETFs with positive 12m-minus-1m return, else flat."""
    p = d["prices"][["SPY", "QQQ", "IWM", "DIA"]]
    mom = p.shift(21) / p.shift(252) - 1
    return _norm_rows((mom > 0).astype(float))


def s02_xs_momentum_sectors(d):
    """Cross-sectional momentum across 10 sector ETFs: long top-3 / short bottom-3 by 6m return."""
    p = d["prices"][d["groups"]["sectors"]]
    mom = p / p.shift(126) - 1
    rank = mom.rank(axis=1)
    n = rank.max(axis=1)
    w = pd.DataFrame(0.0, index=p.index, columns=p.columns)
    w[rank.le(3)] = -1.0
    w[rank.ge(n - 2, axis=0)] = 1.0
    return _norm_rows(w)


def s03_short_term_reversal(d):
    """5-day reversal: buy the worst-performing index ETFs of the last week (overreaction fade)."""
    p = d["prices"][["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM"]]
    r5 = p / p.shift(5) - 1
    rank = r5.rank(axis=1)
    w = -(rank - rank.mean(axis=1).values.reshape(-1, 1))   # long losers, short winners
    return _norm_rows(w)


def s04_vix_term_structure(d):
    """Vol carry: long SPY when VIX < VIX3M (contango = calm), flat when backwardated."""
    p = d["prices"]
    if "^VIX" not in p or "^VIX3M" not in p:
        return None
    sig = (p["^VIX"] < p["^VIX3M"]).astype(float)
    w = pd.DataFrame(0.0, index=p.index, columns=["SPY"])
    w["SPY"] = sig
    return w


def s05_trend_filter_200d(d):
    """Classic trend: hold SPY only when above its 200d MA (regime de-risking)."""
    p = d["prices"][["SPY"]]
    ma = p["SPY"].rolling(200).mean()
    w = pd.DataFrame(0.0, index=p.index, columns=["SPY"])
    w["SPY"] = (p["SPY"] > ma).astype(float)
    return w


def s06_bond_trend(d):
    """Rates trend: long TLT when its own 3m momentum is positive (duration trend)."""
    p = d["prices"][["TLT"]]
    mom = p["TLT"] / p["TLT"].shift(63) - 1
    w = pd.DataFrame(0.0, index=p.index, columns=["TLT"])
    w["TLT"] = (mom > 0).astype(float)
    return w


def s07_credit_spread_riskoff(d):
    """Macro regime: when HY credit spread (FRED) is rising (z>0 over 3m), de-risk equity to 0."""
    p = d["prices"][["SPY"]]
    fred = d["fred"]
    if "hy_oas" not in fred:
        return None
    oas = fred["hy_oas"].reindex(p.index).ffill()
    risk_on = (_z(oas, 63) < 0).astype(float)   # spread below its trend = risk on
    w = pd.DataFrame(0.0, index=p.index, columns=["SPY"])
    w["SPY"] = risk_on
    return w


def s08_yield_curve_regime(d):
    """Term spread (10y-2y): steepening regime favors small-caps/cyclicals (IWM) over flat."""
    p = d["prices"][["IWM"]]
    fred = d["fred"]
    if "term_spread" not in fred:
        return None
    ts = fred["term_spread"].reindex(p.index).ffill()
    w = pd.DataFrame(0.0, index=p.index, columns=["IWM"])
    w["IWM"] = (ts.diff(21) > 0).astype(float)   # steepening
    return w


def s09_usd_trend_commodity(d):
    """FX-commodity link: when USD (UUP) is in downtrend, tilt long gold (GLD)."""
    p = d["prices"][["UUP", "GLD"]]
    usd_dn = (p["UUP"] / p["UUP"].shift(63) - 1) < 0
    w = pd.DataFrame(0.0, index=p.index, columns=["GLD"])
    w["GLD"] = usd_dn.astype(float)
    return w


def s10_turn_of_month(d):
    """Seasonality: long SPY on the last trading day + first 3 of each month (cash-flow effect)."""
    p = d["prices"][["SPY"]]
    idx = p.index
    w = pd.DataFrame(0.0, index=idx, columns=["SPY"])
    dom = idx.day
    # first 3 business days: day-of-month <=4; last day: next row is a new month
    is_first = pd.Series(dom <= 4, index=idx)
    is_last = pd.Series(idx.to_period("M"), index=idx).ne(
        pd.Series(idx.to_period("M"), index=idx).shift(-1))
    w["SPY"] = (is_first | is_last).astype(float)
    return w


def s11_sector_rs_rotation(d):
    """Relative-strength rotation: hold the 3 sectors with best 3m return (long-only momentum)."""
    p = d["prices"][d["groups"]["sectors"]]
    mom = p / p.shift(63) - 1
    rank = mom.rank(axis=1, ascending=False)
    return _norm_rows((rank <= 3).astype(float))


def s12_crypto_tsmom(d):
    """Crypto trend (different market): long BTC/ETH when 50d>200d-ish (30/100d) trend up."""
    p = d["prices"][["BTC-USD", "ETH-USD"]]
    fast = p.rolling(30).mean(); slow = p.rolling(100).mean()
    return _norm_rows((fast > slow).astype(float))


def s13_rsi2_meanrev(d):
    """Connors RSI(2) mean reversion: buy index ETFs when 2-day RSI < 10 (deep oversold)."""
    p = d["prices"][["SPY", "QQQ", "IWM"]]
    delta = p.diff()
    up = delta.clip(lower=0).rolling(2).mean()
    dn = (-delta.clip(upper=0)).rolling(2).mean()
    rs = up / dn.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    w = (rsi < 10).astype(float)
    # only when above 200d MA (mean-revert within uptrends — the robust version)
    trend = (p > p.rolling(200).mean()).astype(float)
    return _norm_rows(w * trend)


def s14_vix_spike_fade(d):
    """Vol spike fade: when VIX z-score (1m) > 2, go long SPY next day (panic mean-reverts)."""
    p = d["prices"][["SPY"]]
    if "^VIX" not in d["prices"]:
        return None
    vix = d["prices"]["^VIX"]
    spike = (_z(vix, 21) > 2).astype(float)
    w = pd.DataFrame(0.0, index=p.index, columns=["SPY"])
    w["SPY"] = spike
    return w


def s15_defensive_breakout(d):
    """Risk-parity-lite barbell: equal long SPY+TLT+GLD only when each is above its 100d MA
    (own-trend gated), a diversified all-weather trend sleeve."""
    cols = ["SPY", "TLT", "GLD"]
    p = d["prices"][cols]
    above = (p > p.rolling(100).mean()).astype(float)
    return _norm_rows(above)


ALL = [s01_tsmom_equity, s02_xs_momentum_sectors, s03_short_term_reversal, s04_vix_term_structure,
       s05_trend_filter_200d, s06_bond_trend, s07_credit_spread_riskoff, s08_yield_curve_regime,
       s09_usd_trend_commodity, s10_turn_of_month, s11_sector_rs_rotation, s12_crypto_tsmom,
       s13_rsi2_meanrev, s14_vix_spike_fade, s15_defensive_breakout]
