#!/usr/bin/env python3
"""Plot equity and drawdown from faithful strategy trade CSVs."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRADES = PROJECT_ROOT / "backtest_outputs" / "research_duckdb_chunked_bidask" / "backtest_research_duckdb_trades.csv"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "plots" / "research_equity_corrected_bidask.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot cumulative PnL and drawdown for a strategy trade CSV.")
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument(
        "--overlay",
        action="append",
        default=[],
        help="Optional overlay in label=path form. Can be passed multiple times.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--title", default="Research Strategy on Corrected Kalshi Bid/Ask Replay")
    parser.add_argument("--highlight-start")
    parser.add_argument("--highlight-end")
    return parser.parse_args()


def load_curve(path: Path, label: str) -> tuple[str, pd.DataFrame, dict]:
    trades = pd.read_csv(path, parse_dates=["entry_time", "settle_time"])
    if trades.empty:
        raise SystemExit(f"No trades found in {path}")
    trades = trades.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
    trades["cum_pnl"] = trades["pnl"].astype(float).cumsum()
    trades["running_max"] = trades["cum_pnl"].cummax()
    trades["drawdown"] = trades["cum_pnl"] - trades["running_max"]
    premium = trades["entry_price"].astype(float) + trades["entry_fee"].astype(float)
    stats = {
        "Trades": len(trades),
        "PnL": trades["pnl"].sum(),
        "Premium": premium.sum(),
        "Return/Premium": trades["pnl"].sum() / premium.sum(),
        "Max DD": trades["drawdown"].min(),
        "Win Rate": (trades["pnl"] > 0).mean(),
    }
    return label, trades, stats


def parse_overlay(value: str) -> tuple[str, Path]:
    label, sep, path = value.partition("=")
    if not sep:
        raise SystemExit("--overlay must be in label=path form")
    return label, Path(path)


def parse_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def main() -> int:
    args = parse_args()
    curves: list[tuple[str, pd.DataFrame, dict]] = [load_curve(args.trades, "research")]
    for overlay in args.overlay:
        label, path = parse_overlay(overlay)
        curves.append(load_curve(path, label))

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(13, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
        constrained_layout=True,
    )
    ax, dd_ax = axes
    colors = ["#0f766e", "#2563eb", "#7c3aed", "#ea580c"]
    highlight_start = parse_ts(args.highlight_start)
    highlight_end = parse_ts(args.highlight_end)
    if highlight_start is not None and highlight_end is not None:
        for axis in axes:
            axis.axvspan(highlight_start, highlight_end, color="#f59e0b", alpha=0.12, label="highlight window" if axis is ax else None)
    for idx, (label, trades, stats) in enumerate(curves):
        color = colors[idx % len(colors)]
        x = trades["settle_time"]
        ax.plot(x, trades["cum_pnl"], color=color, linewidth=2.0, label=f"{label} cumulative PnL")
        dd_ax.plot(x, trades["drawdown"], color=color, linewidth=1.2, label=f"{label} DD")
    ax.axhline(0, color="#525252", linewidth=0.8)
    ax.axvline(pd.Timestamp("2026-04-01T00:00:00Z"), color="#6b7280", linestyle="--", linewidth=1.0, label="Train/validation split")
    ax.axvline(pd.Timestamp("2026-04-21T00:00:00Z"), color="#9ca3af", linestyle="--", linewidth=1.0, label="Validation/test split")
    ax.set_title(args.title, fontsize=14, loc="left")
    ax.set_ylabel("Cumulative PnL ($)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left")

    summary = "\n".join(
        f"{label}: trades={stats['Trades']} pnl={stats['PnL']:.2f} USD "
        f"ret={stats['Return/Premium']:.2%} max_dd={stats['Max DD']:.2f} USD"
        for label, _, stats in curves
    )
    ax.text(
        0.01,
        0.98,
        summary,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=10,
        bbox={"facecolor": "white", "edgecolor": "#d4d4d4", "alpha": 0.9},
    )

    dd_ax.axhline(0, color="#525252", linewidth=0.8)
    dd_ax.set_ylabel("DD ($)")
    dd_ax.set_xlabel("Settlement time")
    dd_ax.grid(True, alpha=0.25)
    dd_ax.legend(loc="lower left")
    dd_ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    for label in dd_ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=180)
    plt.close(fig)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
