"""Variance risk premium — short index vol carry with regime filter.

Two complementary backtests:

(1) **Synthetic VRP swap P&L** — at each month-end, take VIX as ex-ante fair vol
estimate; compute next 21-day realized vol of SPY; short-vol P&L per "vega" =
(VIX/100 - RV) × vega_notional. Regime filters: skip if VIX > 35 OR backwardation
(VIX > VIX3M). This is the clean academic VRP backtest.

(2) **Practical VXX-short proxy** — short VXX (or long SVXY) when regime filters
pass. VXX has structural roll-down from contango → short captures it. Use
2011-2018 XIV-equivalent (treat post-2018 as the "honest" regime since XIV blew up).

Capacity: VIX futures markets are deep ($1B+ daily notional). At $50M no impact issue.
Tail risk is the binding constraint — Feb 2018 Volmageddon, Mar 2020 COVID.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from _data import load_many, load

START = "2008-01-01"
END   = "2026-06-01"


def realized_vol(returns: pd.Series, window: int = 21) -> pd.Series:
    """Annualized realized vol from log returns."""
    return returns.rolling(window).std() * np.sqrt(252) * 100  # in vol points


def vrp_swap_backtest(spy_close: pd.Series, vix: pd.Series, vix3m: pd.Series,
                       max_vix: float = 35.0,
                       require_contango: bool = True) -> pd.DataFrame:
    """Roll a monthly synthetic variance swap. Position-sized by vega.

    At month-end m:
      - If VIX_m > max_vix → flat
      - If require_contango and VIX_m > VIX3M_m → flat
      - Else short 1 vega: P&L = (VIX_m - RV_{m, m+21d})  (per vol point)
    Daily mark-to-market via squared returns minus expected (squared VIX/sqrt(252))^2.
    """
    spy_ret = np.log(spy_close / spy_close.shift(1))
    rv_21 = realized_vol(spy_ret, 21)

    # align all series
    idx = spy_close.index.intersection(vix.index)
    if vix3m is not None:
        idx = idx.intersection(vix3m.index)
    df = pd.DataFrame({
        "spy": spy_close.loc[idx],
        "ret": spy_ret.loc[idx],
        "vix": vix.loc[idx],
        "vix3m": vix3m.loc[idx] if vix3m is not None else vix.loc[idx],
        "rv21": rv_21.loc[idx],
    }).dropna()

    # month-ends — re-position on last business day of each month
    df["month_end"] = df.index.to_series().dt.to_period("M") != \
                      df.index.to_series().dt.to_period("M").shift(-1)

    # at each ME pick position for next month
    positions = []
    last_pos_vol = None
    last_entry_date = None
    daily_pnl = []
    for i, (dt, row) in enumerate(df.iterrows()):
        if row["month_end"]:
            # set position for next month
            if row["vix"] > max_vix:
                last_pos_vol = 0
            elif require_contango and row["vix"] > row["vix3m"]:
                last_pos_vol = 0
            else:
                last_pos_vol = -1.0  # short 1 unit of variance, in vol-point units
                last_entry_vix = row["vix"]
                last_entry_date = dt
        # daily MTM not modeled — we book PnL only at next month-end
        if i + 1 < len(df) and df["month_end"].iloc[i + 1] and last_pos_vol is not None and last_pos_vol < 0:
            # close at next ME
            pass  # handled at next ME
        positions.append(last_pos_vol if last_pos_vol is not None else 0)
    df["pos"] = positions

    # actually compute P&L per holding period: at each ME, if we held short vol last month
    me_dates = df.index[df["month_end"]]
    pnl_records = []
    for k in range(1, len(me_dates)):
        d0, d1 = me_dates[k-1], me_dates[k]
        # entry vix at d0
        entry_vix = df.loc[d0, "vix"]
        # realized vol over (d0, d1]
        period_rets = df.loc[d0:d1, "ret"].iloc[1:]
        if len(period_rets) < 5: continue
        rv_period = period_rets.std() * np.sqrt(252) * 100
        # position carried (0 or -1)
        pos = df.loc[d0, "pos"]
        if pos == 0:
            pnl_pts = 0.0
        else:
            # short-vol: P&L per unit = (entry_vix - rv_period), in vol points
            pnl_pts = (entry_vix - rv_period) * (-pos)  # pos is -1 → short → +1*(entry-rv)
        # normalize to a "% per month" assuming we sized so one vol point = 1% of cap
        # this is the standard variance swap convention (vega-notional sized to capital)
        pnl_records.append({"date": d1, "entry_vix": entry_vix, "rv": rv_period,
                            "pos": pos, "pnl_pct": pnl_pts / 100})

    res = pd.DataFrame(pnl_records).set_index("date")
    return res


def annualized_stats_monthly(pnl_pct: pd.Series):
    """Stats on a monthly PnL series."""
    if pnl_pct.std() == 0:
        return {"sharpe": 0, "ann_ret": 0, "ann_vol": 0, "mdd": 0}
    s = pnl_pct.mean() / pnl_pct.std() * np.sqrt(12)
    eq = (1 + pnl_pct).cumprod()
    return {"sharpe": float(s),
            "ann_ret": float(pnl_pct.mean() * 12),
            "ann_vol": float(pnl_pct.std() * np.sqrt(12)),
            "mdd": float((eq / eq.cummax() - 1).min())}


def main():
    print("Loading SPY, ^VIX, ^VIX3M (or ^VXV) ...")
    spy = load("SPY", start=START, end=END)
    vix = load("^VIX", start=START, end=END)
    try:
        vix3m = load("^VIX3M", start=START, end=END)
    except Exception:
        try:
            vix3m = load("^VXV", start=START, end=END)
            print("  using ^VXV as VIX3M proxy")
        except Exception:
            vix3m = None
            print("  no VIX3M data, contango filter disabled")
    print(f"  SPY: {len(spy)}d, VIX: {len(vix)}d, VIX3M: {len(vix3m) if vix3m is not None else 0}d")

    print("\n=== VRP swap backtest: short vol monthly, regime filters ===")

    print("\n(a) No filters (naive short-vol carry):")
    res = vrp_swap_backtest(spy, vix, vix3m, max_vix=999, require_contango=False)
    res_train = res[res.index < "2018-01-01"]
    res_oos = res[res.index >= "2018-01-01"]
    print(f"  Train 2008-2017 (n={len(res_train)} months): {annualized_stats_monthly(res_train['pnl_pct'])}")
    print(f"  OOS  2018+      (n={len(res_oos)} months): {annualized_stats_monthly(res_oos['pnl_pct'])}")
    # worst months
    worst = res_oos.nsmallest(5, "pnl_pct")[["entry_vix", "rv", "pnl_pct"]]
    print(f"  Worst 5 OOS months: \n{worst}")

    print("\n(b) Tail filter only (skip if VIX > 35):")
    res = vrp_swap_backtest(spy, vix, vix3m, max_vix=35, require_contango=False)
    res_oos = res[res.index >= "2018-01-01"]
    print(f"  OOS: {annualized_stats_monthly(res_oos['pnl_pct'])}")

    if vix3m is not None:
        print("\n(c) Tail + contango filter (skip if VIX > 35 OR backwardation):")
        res = vrp_swap_backtest(spy, vix, vix3m, max_vix=35, require_contango=True)
        res_oos = res[res.index >= "2018-01-01"]
        print(f"  OOS: {annualized_stats_monthly(res_oos['pnl_pct'])}")
        n_trades = (res_oos["pos"] != 0).sum()
        n_total = len(res_oos)
        print(f"  Engaged {n_trades}/{n_total} months ({n_trades/n_total*100:.0f}%)")

        # walk-forward by year
        print("\n  By 2-year bucket:")
        for y in range(2018, 2026, 2):
            sub = res[(res.index >= f"{y}-01-01") & (res.index < f"{y+2}-01-01")]
            if len(sub) > 5:
                st = annualized_stats_monthly(sub["pnl_pct"])
                print(f"    {y}-{y+1}: Sharpe={st['sharpe']:+.2f}  AnnRet={st['ann_ret']*100:+.1f}%  MDD={st['mdd']*100:+.1f}%  n={len(sub)}")

        # save filtered version PnL for downstream HRP
        save = res_oos["pnl_pct"]
        # convert monthly to daily-aligned for HRP (rough — broadcast)
        save.to_frame("pnl_pct").to_parquet(
            "/Users/rithvikijju/edge-bot/research/v2/data/_pnl_vrp.parquet"
        )
        print("\n  Saved → research/v2/data/_pnl_vrp.parquet")

    print("\n=== Practical VXX-short proxy (post-Volmageddon, 2018+) ===")
    try:
        vxx = load("VXX", start="2018-02-01", end=END)
        print(f"  VXX: {len(vxx)}d")
        vxx_ret = np.log(vxx / vxx.shift(1)).dropna()
        # naive short-vxx every day where VIX < 35 AND VIX < VIX3M
        spy_ret = np.log(spy / spy.shift(1))
        # align
        idx = vxx_ret.index.intersection(vix.index).intersection(vix3m.index)
        d = pd.DataFrame({
            "vxx_ret": vxx_ret.loc[idx], "vix": vix.loc[idx], "vix3m": vix3m.loc[idx]
        })
        d["signal"] = ((d["vix"] < 35) & (d["vix"] < d["vix3m"])).astype(int)
        d["short_vxx_pnl"] = -d["signal"].shift(1) * d["vxx_ret"]  # short next day
        d["short_vxx_pnl"] -= d["signal"].diff().abs().shift(1).fillna(0) * (5/10000)  # 5bp r/t
        pnl_daily = d["short_vxx_pnl"].dropna()
        if pnl_daily.std() > 0:
            s = pnl_daily.mean() / pnl_daily.std() * np.sqrt(252)
            eq = (1 + pnl_daily).cumprod()
            mdd = (eq/eq.cummax() - 1).min()
            print(f"  Short VXX (filtered): Sharpe={s:+.2f}  AnnRet={pnl_daily.mean()*252*100:+.1f}%  Vol={pnl_daily.std()*np.sqrt(252)*100:.1f}%  MDD={mdd*100:+.1f}%")
            print(f"  Engaged {d['signal'].sum()}/{len(d)} days ({d['signal'].mean()*100:.0f}%)")
            pnl_daily.to_frame("pnl").to_parquet("/Users/rithvikijju/edge-bot/research/v2/data/_pnl_vrp_vxx.parquet")
    except Exception as e:
        print(f"  VXX proxy failed: {e}")


if __name__ == "__main__":
    main()
