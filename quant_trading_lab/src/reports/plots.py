"""Matplotlib-only plotting helpers. Keep plots simple and readable."""
from __future__ import annotations
import pandas as pd
import matplotlib.pyplot as plt
from ..backtest.metrics import rolling_sharpe, rolling_drawdown, monthly_returns


def plot_equity(equity: pd.Series, benchmark: pd.Series | None = None,
                 title: str = "Equity curve", figsize=(10, 4), ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    ax.plot(equity.index, equity.values, label="Strategy", linewidth=1.5)
    if benchmark is not None and not benchmark.empty:
        b = benchmark.reindex(equity.index).ffill()
        ax.plot(b.index, b.values, label="Benchmark", linewidth=1.0, alpha=0.7)
    ax.set_title(title)
    ax.set_ylabel("Equity ($)")
    ax.grid(alpha=0.3)
    ax.legend()
    return ax


def plot_drawdown(equity: pd.Series, title: str = "Drawdown", figsize=(10, 3), ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    dd = equity / equity.cummax() - 1
    ax.fill_between(dd.index, dd.values, 0, color="red", alpha=0.4)
    ax.set_title(title)
    ax.set_ylabel("Drawdown")
    ax.grid(alpha=0.3)
    return ax


def plot_rolling_sharpe(returns: pd.Series, window: int = 63, figsize=(10, 3), ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    rs = rolling_sharpe(returns, window=window)
    ax.plot(rs.index, rs.values, linewidth=1)
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.set_title(f"Rolling {window}d Sharpe")
    ax.grid(alpha=0.3)
    return ax


def plot_monthly_heatmap(equity: pd.Series, title: str = "Monthly returns", figsize=(9, 4)):
    mret = monthly_returns(equity)
    if mret.empty:
        return None
    tab = mret.copy()
    tab.index = pd.MultiIndex.from_arrays([tab.index.year, tab.index.month], names=["yr", "mo"])
    pivot = tab.unstack("mo")
    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-0.10, vmax=0.10)
    ax.set_xticks(range(pivot.shape[1]))
    ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(pivot.shape[0]))
    ax.set_yticklabels(pivot.index)
    ax.set_title(title)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v*100:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="Monthly return")
    return ax


def plot_weights(weights: pd.DataFrame, title: str = "Target weights", figsize=(10, 4), ax=None):
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    for col in weights.columns:
        ax.plot(weights.index, weights[col], label=col, linewidth=0.8)
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    return ax
