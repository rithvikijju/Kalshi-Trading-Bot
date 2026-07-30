"""Monthly performance report — what an LP would see at month end.

Outputs markdown + chart PNGs to data/reports/<run_id>/.
"""
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import matplotlib.pyplot as plt
from ..backtest.metrics import summary, monthly_returns
from ..config import REPORTS_DIR
from . import plots


def write_monthly_report(result: dict, run_id: str, strategy_name: str = "strategy") -> Path:
    out_dir = REPORTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    eq = result["equity"]
    rets = result["returns"]
    bench = result.get("benchmark")

    # charts
    fig, ax = plt.subplots(figsize=(10, 4))
    plots.plot_equity(eq, bench, ax=ax)
    fig.savefig(out_dir / "equity.png", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3))
    plots.plot_drawdown(eq, ax=ax)
    fig.savefig(out_dir / "drawdown.png", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3))
    plots.plot_rolling_sharpe(rets, ax=ax)
    fig.savefig(out_dir / "rolling_sharpe.png", bbox_inches="tight")
    plt.close(fig)

    plots.plot_monthly_heatmap(eq)
    plt.savefig(out_dir / "monthly_heatmap.png", bbox_inches="tight")
    plt.close()

    m = summary(rets, eq)
    mret = monthly_returns(eq)

    md = [f"# Monthly performance report — {strategy_name}",
          f"\n**run_id:** `{run_id}`  \n**period:** {eq.index.min().date()} → {eq.index.max().date()}",
          "\n## Summary",
          "\n| metric | value |\n|---|---|"]
    for k, v in m.items():
        if isinstance(v, float):
            md.append(f"| {k} | {v:.4f} |")
        else:
            md.append(f"| {k} | {v} |")

    md.append("\n## Monthly returns")
    md.append("| month | return |\n|---|---|")
    for ts, v in mret.tail(24).items():
        md.append(f"| {ts.strftime('%Y-%m')} | {v*100:+.2f}% |")

    md.extend(["\n## Charts", "![equity](equity.png)", "![drawdown](drawdown.png)",
               "![rolling Sharpe](rolling_sharpe.png)", "![monthly heatmap](monthly_heatmap.png)"])

    (out_dir / "report.md").write_text("\n".join(md))
    (out_dir / "metrics.json").write_text(json.dumps(m, indent=2))
    return out_dir
