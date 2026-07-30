"""Tearsheet: one-pager performance summary for a single backtest run."""
from __future__ import annotations
import matplotlib.pyplot as plt
import pandas as pd
from . import plots
from ..backtest.metrics import summary, monthly_returns


def tearsheet(result: dict, title: str = ""):
    """`result` is the dict returned by engine.run_backtest()."""
    eq = result["equity"]
    rets = result["returns"]
    bench = result.get("benchmark")
    weights = result.get("weights")

    fig = plt.figure(figsize=(12, 12))
    gs = fig.add_gridspec(5, 2, hspace=0.5, wspace=0.25)
    ax1 = fig.add_subplot(gs[0, :])
    plots.plot_equity(eq, bench, title=f"{title} equity", ax=ax1)
    ax2 = fig.add_subplot(gs[1, :])
    plots.plot_drawdown(eq, ax=ax2)
    ax3 = fig.add_subplot(gs[2, :])
    plots.plot_rolling_sharpe(rets, ax=ax3)
    if weights is not None and not weights.empty:
        ax4 = fig.add_subplot(gs[3, :])
        plots.plot_weights(weights, ax=ax4)

    metrics = summary(rets, eq)
    metrics_lines = "\n".join(
        f"{k:>16s}: {v:.3f}" if isinstance(v, float) and abs(v) < 100
        else f"{k:>16s}: {v}" for k, v in metrics.items())
    ax_text = fig.add_subplot(gs[4, 0])
    ax_text.axis("off")
    ax_text.text(0, 1, "Summary\n" + metrics_lines, family="monospace",
                  verticalalignment="top", fontsize=10)

    mret = monthly_returns(eq)
    ax_mh = fig.add_subplot(gs[4, 1])
    if not mret.empty:
        plots.plot_monthly_heatmap(eq, title="Monthly returns")
    return fig, metrics
