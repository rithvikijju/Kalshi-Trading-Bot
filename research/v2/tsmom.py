"""Time-series momentum (Moskowitz-Ooi-Pedersen 2012) across diversified asset ETFs.

Each month-end, for every asset:
  - Take sign of trailing 12m return → position
  - Scale to target vol (10% annualized) using trailing 60d realized vol
Portfolio = equal-weighted sum across assets.

Universe: diversified futures-proxy ETFs spanning equities (region), rates,
commodities, currencies. yfinance daily 2005-2026.

Known result: TSMOM ran Sharpe ~1.2 in MOP's 1985-2009 paper, ~0.7 in
Hurst-Ooi-Pedersen 2017 follow-up (added 110 years of OOS, the OOS Sharpe was
the same). Post-2017 has been a trend drought — academic prior suggests
Sharpe 0.3-0.6 in 2018-2026. We test honestly and let the data speak.
"""
import numpy as np
import pandas as pd
from _data import load_many

UNIVERSE = [
    # Equity regions
    "SPY", "EFA", "EEM",
    # Rates
    "TLT", "IEF", "SHY",
    # Credit
    "HYG", "LQD",
    # Commodities
    "GLD", "SLV", "USO", "DBC", "DBA",
    # FX (DXY-like)
    "UUP", "FXE", "FXY",
    # Real estate
    "VNQ",
]

LOOKBACK_M = 12
VOL_LOOKBACK = 60
VOL_TARGET = 0.10  # 10% per leg


def main():
    print(f"Loading {len(UNIVERSE)} assets...")
    px = load_many(UNIVERSE, start="2005-01-01", end="2026-06-01")
    px = px.dropna(axis=1, thresh=int(0.5 * len(px)))
    print(f"  got {px.shape[1]} assets, {px.shape[0]} days")

    rets = np.log(px / px.shift(1)).dropna(how="all")

    # build monthly rebalance signal: at each month-end, sign of trailing 252d log return
    px_me = px.resample("M").last()
    lookback_ret = np.log(px_me / px_me.shift(LOOKBACK_M))
    signal_me = np.sign(lookback_ret)
    signal_me = signal_me.shift(1)  # use previous ME signal for current month (no look-ahead)

    # vol target sizing — daily rolling realized vol
    realized_vol = rets.rolling(VOL_LOOKBACK).std() * np.sqrt(252)
    vol_target_weights = VOL_TARGET / realized_vol.replace(0, np.nan)
    vol_target_weights = vol_target_weights.clip(upper=4.0)  # cap leverage per leg at 4x

    # broadcast monthly signal to daily, hold for the month
    signal_daily = signal_me.reindex(rets.index, method="ffill")

    # position = sign × vol-target weight, equal-weighted across assets
    pos = signal_daily * vol_target_weights
    pos = pos.divide(pos.abs().sum(axis=1).replace(0, np.nan), axis=0)  # normalize gross to 1
    pos = pos.fillna(0)

    # P&L: hold position from previous bar
    daily_pnl = (pos.shift(1) * rets).sum(axis=1)

    # turnover cost: 5bp r/t × |dw|
    turnover = pos.diff().abs().sum(axis=1).fillna(0)
    cost = turnover * (5.0 / 10000)
    pnl_net = (daily_pnl - cost).dropna()

    pnl_oos = pnl_net[pnl_net.index >= "2010-01-01"]

    def stats(p):
        if p.std() == 0: return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0)
        s = p.mean() / p.std() * np.sqrt(252)
        eq = (1 + p).cumprod()
        return dict(sharpe=float(s), ann_ret=float(p.mean()*252),
                    ann_vol=float(p.std()*np.sqrt(252)),
                    mdd=float((eq/eq.cummax()-1).min()))

    print(f"\nFull post-2010 (OOS for MOP 2012 paper):")
    print(f"  {stats(pnl_oos)}")

    print("\nBy 2-year bucket:")
    for y in range(2010, 2026, 2):
        sub = pnl_oos[(pnl_oos.index >= f"{y}-01-01") & (pnl_oos.index < f"{y+2}-01-01")]
        if len(sub) > 10:
            s = stats(sub)
            print(f"  {y}-{y+1}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")

    # asset-class subportfolios
    asset_classes = {
        "Equity": ["SPY", "EFA", "EEM"],
        "Rates":  ["TLT", "IEF", "SHY"],
        "Credit": ["HYG", "LQD"],
        "Commodity": ["GLD", "SLV", "USO", "DBC", "DBA"],
        "FX":     ["UUP", "FXE", "FXY"],
        "RE":     ["VNQ"],
    }
    print("\nBy asset class (full OOS):")
    for cls, names in asset_classes.items():
        cols = [n for n in names if n in pos.columns]
        if not cols: continue
        p_cls = (pos.shift(1)[cols] * rets[cols]).sum(axis=1)
        # rescale to its own vol
        if p_cls.std() > 0:
            p_cls = p_cls / p_cls.std() / np.sqrt(252) * 0.10  # vol target 10%
            s = stats(p_cls[p_cls.index >= "2010-01-01"])
            print(f"  {cls}: Sharpe={s['sharpe']:+.2f}  AnnRet={s['ann_ret']*100:+.1f}%  MDD={s['mdd']*100:+.1f}%")

    # save for HRP
    pnl_oos.to_frame("pnl").to_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_tsmom.parquet")
    print("\nSaved PnL → research/v2/data/_pnl_tsmom.parquet")


if __name__ == "__main__":
    main()
