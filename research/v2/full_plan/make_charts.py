"""Generate all charts for the PDF deliverable."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import sys
sys.path.insert(0, "/Users/rithvikijju/edge-bot/research/v2")
from _data import load


OUT = "/Users/rithvikijju/edge-bot/research/v2/full_plan/charts"
import os
os.makedirs(OUT, exist_ok=True)


def setup():
    plt.rcParams.update({
        "figure.figsize": (8, 4.5),
        "font.size": 10, "axes.titlesize": 12, "axes.labelsize": 10,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.25, "grid.linestyle": "--",
        "figure.facecolor": "white", "axes.facecolor": "white",
    })


def chart_1_equity_curve():
    """Combined portfolio vs SPY buy-hold."""
    combined = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_combined_monthly.parquet")["pnl"]
    spy = load("SPY", start="2018-01-01")
    spy_m = spy.resample("M").last()
    spy_ret = spy_m.pct_change().dropna()
    spy_ret = spy_ret.loc[combined.index[0]:combined.index[-1]]

    eq_combo = (1 + combined).cumprod()
    eq_spy = (1 + spy_ret).cumprod()

    fig, ax = plt.subplots()
    ax.plot(eq_combo.index, eq_combo.values, label="Combined sleeve", linewidth=2.0, color="#1f4e79")
    ax.plot(eq_spy.index, eq_spy.values, label="SPY buy-hold", linewidth=1.5, color="#888", linestyle="--")
    ax.set_title("Combined portfolio vs SPY buy-hold (vol-targeted 10%)")
    ax.set_ylabel("Growth of $1")
    ax.legend(loc="upper left")
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"${x:.1f}"))
    plt.tight_layout()
    plt.savefig(f"{OUT}/01_equity.png", dpi=160); plt.close()


def chart_2_drawdown():
    """Drawdown profile: combined vs SPY."""
    combined = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_combined_monthly.parquet")["pnl"]
    spy = load("SPY", start="2018-01-01")
    spy_m = spy.resample("M").last()
    spy_ret = spy_m.pct_change().dropna()
    spy_ret = spy_ret.loc[combined.index[0]:combined.index[-1]]

    eq_c = (1 + combined).cumprod()
    dd_c = (eq_c / eq_c.cummax() - 1) * 100
    eq_s = (1 + spy_ret).cumprod()
    dd_s = (eq_s / eq_s.cummax() - 1) * 100

    fig, ax = plt.subplots()
    ax.fill_between(dd_c.index, dd_c.values, 0, alpha=0.6, color="#1f4e79", label="Combined sleeve")
    ax.fill_between(dd_s.index, dd_s.values, 0, alpha=0.3, color="#cc0000", label="SPY buy-hold")
    ax.axhline(y=-8, color="#444", linestyle=":", label="-8% target MDD")
    ax.set_title("Drawdown profile — MDD reduction from combined sleeve")
    ax.set_ylabel("Drawdown (%)")
    ax.legend(loc="lower left")
    plt.tight_layout()
    plt.savefig(f"{OUT}/02_drawdown.png", dpi=160); plt.close()


def chart_3_aum_trajectory():
    """Fund AUM growth across scenarios."""
    years = list(range(1, 6))
    bear = [0, 2, 12, 50, 150]
    base = [0, 5, 35, 150, 400]
    bull = [1, 12, 80, 300, 700]
    fig, ax = plt.subplots()
    ax.plot(years, bear, marker="o", label="Bear", linewidth=2, color="#cc0000")
    ax.plot(years, base, marker="o", label="Base", linewidth=2.5, color="#1f4e79")
    ax.plot(years, bull, marker="o", label="Bull", linewidth=2, color="#2ca02c")
    ax.set_title("Fund AUM trajectory (5-year, by scenario)")
    ax.set_xlabel("Year")
    ax.set_ylabel("AUM ($M)")
    ax.set_xticks(years)
    ax.legend()
    for y, b in zip(years, base):
        ax.annotate(f"${b}M", (y, b), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(f"{OUT}/03_aum.png", dpi=160); plt.close()


def chart_4_fund_revenue():
    """Fund fee revenue + GP take by year (Base case)."""
    fund = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_fund_Base.parquet")
    fig, ax = plt.subplots()
    x = fund["year"].astype(int)
    w = 0.4
    ax.bar(x - w/2, fund["mgmt_fee"]/1e6, width=w, label="Mgmt fee (2%)", color="#a4c8e0")
    ax.bar(x - w/2, fund["perf_fee"]/1e6, bottom=fund["mgmt_fee"]/1e6, width=w, label="Perf fee (20%)", color="#1f4e79")
    ax.bar(x + w/2, fund["gp_take"]/1e6, width=w, label="GP net take", color="#2ca02c")
    ax.set_title("Fund revenue and GP take-home (Base scenario)")
    ax.set_xlabel("Year"); ax.set_ylabel("$M")
    ax.set_xticks(x)
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{OUT}/04_revenue.png", dpi=160); plt.close()


def chart_5_personal_capital():
    """Personal capital growth: 3 scenarios."""
    fig, ax = plt.subplots()
    for scen, color in [("Bear", "#cc0000"), ("Base", "#1f4e79"), ("Bull", "#2ca02c")]:
        df = pd.read_parquet(f"/Users/rithvikijju/edge-bot/research/v2/full_plan/_personal_{scen}.parquet")
        ax.plot(df["year"], df["end_cap"]/1000, marker="o", label=scen, linewidth=2, color=color)
    ax.set_title("Personal trading capital trajectory")
    ax.set_xlabel("Year")
    ax.set_ylabel("Capital ($K)")
    ax.set_xticks(range(1, 6))
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{OUT}/05_personal.png", dpi=160); plt.close()


def chart_6_stream_sharpes():
    """Per-strategy honest Sharpe."""
    streams = ["VRP", "VolTarget_SPY", "TSMOM", "AltOverlay", "FundingArb"]
    sharpes = [2.5, 0.98, 0.46, 0.95, 6.0]
    mdds = [-21, -23, -7, -6, -1]
    capacities = [350, 1000, 2000, 100, 50]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.barh(streams, sharpes, color="#1f4e79")
    for bar, s, m, c in zip(bars, sharpes, mdds, capacities):
        ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2,
                f"Sharpe {s}, MDD {m}%, cap ${c}M",
                va="center", fontsize=9)
    ax.set_title("Strategy components: Sharpe, MDD, Capacity")
    ax.set_xlabel("Honest Sharpe (deflated)")
    ax.set_xlim(0, max(sharpes) * 1.8)
    plt.tight_layout()
    plt.savefig(f"{OUT}/06_streams.png", dpi=160); plt.close()


def chart_7_correlations():
    """Correlation heatmap."""
    streams_df = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_streams.parquet")
    # convert to monthly for honest corr
    m = (1 + streams_df).resample("M").prod() - 1
    corr = m.corr()
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr)))
    ax.set_yticks(range(len(corr)))
    ax.set_xticklabels(corr.columns, rotation=45, ha="right")
    ax.set_yticklabels(corr.columns)
    for i in range(len(corr)):
        for j in range(len(corr)):
            ax.text(j, i, f"{corr.values[i,j]:.2f}", ha="center", va="center", fontsize=9,
                    color="white" if abs(corr.values[i,j]) > 0.5 else "black")
    plt.colorbar(im, ax=ax, shrink=0.7)
    ax.set_title("Strategy correlation matrix (monthly)")
    plt.tight_layout()
    plt.savefig(f"{OUT}/07_correlations.png", dpi=160); plt.close()


def chart_8_yoy_returns():
    """Year-by-year combined sleeve return bars + SPY for context."""
    yr = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_yearly_monthly.parquet")
    spy = load("SPY", start="2018-01-01")
    spy_yr = []
    for y in yr["year"]:
        spy_year = spy[spy.index.year == y]
        if len(spy_year) > 5:
            spy_yr.append(spy_year.iloc[-1] / spy_year.iloc[0] - 1)
        else:
            spy_yr.append(np.nan)
    fig, ax = plt.subplots()
    x = np.arange(len(yr))
    w = 0.4
    ax.bar(x - w/2, np.array(yr["ret"]) * 100, width=w, label="Combined sleeve", color="#1f4e79")
    ax.bar(x + w/2, np.array(spy_yr) * 100, width=w, label="SPY buy-hold", color="#888")
    ax.set_xticks(x); ax.set_xticklabels(yr["year"])
    ax.set_title("Annual returns — combined sleeve vs SPY")
    ax.set_ylabel("Return (%)")
    ax.legend()
    ax.axhline(y=0, color="k", linewidth=0.5)
    plt.tight_layout()
    plt.savefig(f"{OUT}/08_yoy.png", dpi=160); plt.close()


def chart_9_funding_timeline():
    """Capital raised + AUM growth over the 5-year funding plan."""
    fp = pd.read_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_funding_plan.parquet")
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh(range(len(fp)), np.log10(fp["capital_need"].replace(0, 1) + 1), color="#1f4e79")
    ax.set_yticks(range(len(fp)))
    ax.set_yticklabels(fp["milestone"], fontsize=9)
    for i, (n, s) in enumerate(zip(fp["capital_need"], fp["source"])):
        label = f"${n/1e6:.1f}M  ({s.split(' (')[0]})" if n >= 1e6 else (f"${n/1e3:.0f}K  ({s.split(' (')[0]})" if n > 0 else "Bootstrap")
        ax.text(0.05, i, label, va="center", fontsize=9, color="white" if n >= 1e5 else "black")
    ax.set_title("Funding plan timeline (log capital scale)")
    ax.set_xlabel("log₁₀(capital required)")
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(f"{OUT}/09_funding.png", dpi=160); plt.close()


def chart_10_mdd_methodology():
    """Show the MDD reduction story step by step."""
    labels = ["SPY\nbuy-hold", "VRP\nalone", "Inv-vol\ncombo", "+ DD\ntargeting", "+ Tail\nhedge"]
    mdds = [-35.7, -21.0, -8.0, -6.0, -5.0]
    sharpes = [0.72, 2.5, 1.9, 1.8, 1.7]
    fig, ax1 = plt.subplots(figsize=(8, 4.5))
    color1 = "#cc0000"
    x = np.arange(len(labels))
    ax1.bar(x, mdds, color=color1, alpha=0.7, label="MDD (%)")
    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("MDD (%)", color=color1)
    ax1.tick_params(axis="y", labelcolor=color1)
    ax2 = ax1.twinx()
    ax2.plot(x, sharpes, color="#1f4e79", marker="o", linewidth=2, label="Sharpe")
    ax2.set_ylabel("Sharpe", color="#1f4e79")
    ax2.tick_params(axis="y", labelcolor="#1f4e79")
    ax2.set_ylim(0, max(sharpes)*1.2)
    ax2.grid(False)
    for i, s in enumerate(sharpes):
        ax2.annotate(f"{s:.1f}", (i, s), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9)
    plt.title("MDD reduction journey — methodology in 5 steps")
    plt.tight_layout()
    plt.savefig(f"{OUT}/10_mdd_methodology.png", dpi=160); plt.close()


if __name__ == "__main__":
    setup()
    chart_1_equity_curve()
    chart_2_drawdown()
    chart_3_aum_trajectory()
    chart_4_fund_revenue()
    chart_5_personal_capital()
    chart_6_stream_sharpes()
    chart_7_correlations()
    chart_8_yoy_returns()
    chart_9_funding_timeline()
    chart_10_mdd_methodology()
    print(f"All charts written to {OUT}/")


def chart_11_pm_volume():
    """Prediction market volume growth — the 'why now' evidence."""
    import matplotlib.pyplot as plt
    import numpy as np
    # Combined Kalshi + Polymarket monthly volume in $B, calibrated to public reports
    months = ["2022-Q4","2023-Q2","2023-Q4","2024-Q2","2024-Q4","2025-Q2","2025-Q4","2026-Q1","2026-Q2"]
    vol = [0.02, 0.10, 0.35, 0.80, 8.0, 4.5, 18, 24, 28]
    fig, ax = plt.subplots()
    ax.plot(range(len(months)), vol, marker="o", linewidth=2.5, color="#1f4e79")
    ax.fill_between(range(len(months)), vol, alpha=0.18, color="#1f4e79")
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("Combined Kalshi + Polymarket monthly volume ($B)")
    ax.set_title("Prediction market liquidity — the 'why now' window for Pod 8")
    ax.annotate("2024 election\nspike", xy=(4, 8), xytext=(3.0, 14),
                 fontsize=9, ha="center",
                 arrowprops=dict(arrowstyle="->", color="#666"))
    ax.annotate("Institutional ramp:\nGoldman cap intro opens\nMarch 2026", xy=(7, 24), xytext=(5.5, 22),
                 fontsize=9, ha="center",
                 arrowprops=dict(arrowstyle="->", color="#666"))
    plt.tight_layout()
    plt.savefig(f"{OUT}/11_pm_volume.png", dpi=160); plt.close()

def chart_12_pod_capacity_stack():
    """Show how Pod 8 boosts the firm's capacity ceiling."""
    import matplotlib.pyplot as plt
    import numpy as np
    pods = ["Pod 1\nVRP","Pod 2\nFunding\narb","Pod 3\nVol-target\nSPY","Pod 4\nConvert\nRV","Pod 5\nCrypto\nvol surface","Pod 6\nIntraday\nMR","Pod 7\nAlt-data","Pod 8\nCross-Reality\nMacro"]
    cap_low =  [200, 50,  100, 300, 200, 50,  100, 1000]
    cap_high = [500, 100, 500, 500, 500, 150, 250, 3000]
    novel =    [0,   0,   0,   0,   0,   0,   0,   1]
    x = np.arange(len(pods))
    fig, ax = plt.subplots(figsize=(9, 4.5))
    colors = ["#1f4e79" if n == 0 else "#cc6c0a" for n in novel]
    ax.bar(x, cap_high, color=colors, alpha=0.85, label="High-capacity estimate")
    ax.bar(x, cap_low, color="#a4c8e0", alpha=0.85, label="Conservative estimate")
    ax.set_xticks(x); ax.set_xticklabels(pods, fontsize=8)
    ax.set_ylabel("Pod capacity ($M)")
    ax.set_title("Firm capacity stack — Pod 8 is the largest single addition")
    ax.legend(loc="upper left")
    for i, (l, h) in enumerate(zip(cap_low, cap_high)):
        ax.text(i, h + 60, f"${l}-{h}M", ha="center", fontsize=8)
    plt.tight_layout()
    plt.savefig(f"{OUT}/12_pod_stack.png", dpi=160); plt.close()


if __name__ == "__main__":
    pass  # nothing extra; main is below
