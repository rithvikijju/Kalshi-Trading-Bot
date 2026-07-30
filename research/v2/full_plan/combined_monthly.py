"""Honest combined portfolio at MONTHLY frequency — fixes the daily-broadcast bug."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from combined_portfolio import load_streams, stats_daily, tail_hedge_overlay, hrp_weights, drawdown_targeting
import sys
sys.path.insert(0, "/Users/rithvikijju/edge-bot/research/v2")
from _data import load


def monthly_compound(daily_pnl: pd.Series) -> pd.Series:
    return (1 + daily_pnl).resample("M").prod() - 1


def stats_monthly(p):
    if p.std() == 0:
        return dict(sharpe=0, ann_ret=0, ann_vol=0, mdd=0)
    return dict(sharpe=float(p.mean()/p.std()*np.sqrt(12)),
                ann_ret=float(p.mean()*12),
                ann_vol=float(p.std()*np.sqrt(12)),
                mdd=float(((1+p).cumprod() / (1+p).cumprod().cummax() - 1).min()))


def main():
    df_d = load_streams()

    # convert each to monthly compound returns
    df_m = pd.DataFrame({c: monthly_compound(df_d[c]) for c in df_d.columns})
    df_m = df_m.dropna(how="all").fillna(0)

    print(f"Monthly panel: {df_m.shape[1]} streams × {df_m.shape[0]} months")

    print("\n=== Per-stream stats (monthly) ===")
    for c in df_m.columns:
        s = stats_monthly(df_m[c])
        print(f"  {c:18s}: Sharpe={s['sharpe']:+.2f}  Ret={s['ann_ret']*100:+.1f}%  Vol={s['ann_vol']*100:.1f}%  MDD={s['mdd']*100:+.1f}%")

    print("\n=== Correlations (monthly) ===")
    print(df_m.corr().round(2).to_string())

    # Inverse-vol weights
    cov = df_m.cov().values * 12
    w = hrp_weights(cov)
    print("\n=== Weights (inverse-vol) ===")
    for c, wi in zip(df_m.columns, w):
        print(f"  {c:18s}: {wi:.3f}")

    combo = (df_m * w).sum(axis=1)
    # vol target to 10%
    if combo.std() > 0:
        combo_vt = combo / combo.std() / np.sqrt(12) * 0.10

    s_plain = stats_monthly(combo_vt)
    print(f"\n=== Combined (vol-targeted 10% ann) ===")
    print(f"  Plain:        Sharpe={s_plain['sharpe']:+.2f}  Ret={s_plain['ann_ret']*100:+.1f}%  MDD={s_plain['mdd']*100:+.1f}%")

    # Tail hedge — apply at daily, then aggregate to monthly
    spy = load("SPY", start="2018-01-01")
    spy_ret = np.log(spy / spy.shift(1)).dropna()
    spy_ret = spy_ret.reindex(df_d.index, method="ffill").fillna(0)
    hedge_d = tail_hedge_overlay(spy_ret)
    hedge_m = monthly_compound(hedge_d).reindex(combo_vt.index).fillna(0)
    combo_hedged = combo_vt + 0.20 * hedge_m
    s_h = stats_monthly(combo_hedged)
    print(f"  + Tail hedge: Sharpe={s_h['sharpe']:+.2f}  Ret={s_h['ann_ret']*100:+.1f}%  MDD={s_h['mdd']*100:+.1f}%")

    combo_full = drawdown_targeting(combo_hedged, dd_trigger=-0.05, leverage_cut=0.5)
    s_f = stats_monthly(combo_full)
    print(f"  + DD-target:  Sharpe={s_f['sharpe']:+.2f}  Ret={s_f['ann_ret']*100:+.1f}%  MDD={s_f['mdd']*100:+.1f}%")

    # year-by-year
    print("\n=== Year-by-year (final, monthly) ===")
    yr_rows = []
    for y in sorted(combo_full.index.year.unique()):
        sub = combo_full[combo_full.index.year == y]
        if len(sub) >= 2:
            ann_ret = (1 + sub).prod() - 1
            s = stats_monthly(sub)
            yr_rows.append(dict(year=y, ret=ann_ret, sharpe=s['sharpe'], vol=s['ann_vol'], mdd=s['mdd']))
    yr_df = pd.DataFrame(yr_rows)
    print(yr_df.to_string(index=False))

    combo_full.to_frame("pnl").to_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_combined_monthly.parquet")
    yr_df.to_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_yearly_monthly.parquet")
    pd.Series(w, index=df_m.columns).to_frame("weight").to_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_weights_monthly.parquet")
    print("\nSaved monthly artifacts.")

    return s_plain, s_h, s_f


if __name__ == "__main__":
    main()
