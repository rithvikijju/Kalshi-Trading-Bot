from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402

DEFAULT_GRID = PROJECT_ROOT / "backtest_outputs" / "btc15m_ml_live_ws_full_raw_window_grid_20260522_0530_1300"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize BTC15M ML live-WS window grid outputs with official/proxy separation.")
    parser.add_argument("--grid-dir", type=Path, default=DEFAULT_GRID)
    return parser.parse_args()


def read_grid(grid_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[pd.DataFrame] = []
    trades: list[pd.DataFrame] = []
    for window_dir in sorted(path for path in grid_dir.iterdir() if path.is_dir()):
        summary_path = window_dir / "ml_live_ws_summary.csv"
        trades_path = window_dir / "ml_live_ws_trades.parquet"
        if summary_path.exists():
            summary = pd.read_csv(summary_path)
            summary["window"] = window_dir.name
            summaries.append(summary)
        if trades_path.exists():
            trade = pd.read_parquet(trades_path)
            trade["window"] = window_dir.name
            trades.append(trade)
    summary_df = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    trades_df = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    return summary_df, trades_df


def max_drawdown(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    cumulative = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    drawdown = cumulative - cumulative.cummax()
    return float(drawdown.min())


def add_official_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    if out.empty:
        return out
    if "official_result" not in out.columns:
        out["official_result"] = np.nan
    has_official = out["official_result"].notna()
    official_win = out["official_result"].astype(str).str.lower().eq(out["side"].astype(str).str.lower())
    entry = pd.to_numeric(out["entry_price"], errors="coerce")
    fee = pd.to_numeric(out["entry_fee"], errors="coerce")
    out["official_win"] = np.where(has_official, official_win, np.nan)
    out["pnl_official_0c"] = np.where(has_official, np.where(official_win, 1.0 - entry - fee, -entry - fee), np.nan)
    entry_2c = (entry + 0.02).clip(upper=0.99)
    fee_2c = np.asarray([kalshi_fee_dollars(float(price), contracts=1, liquidity="taker") for price in entry_2c])
    out["pnl_official_2c"] = np.where(
        has_official,
        np.where(official_win, 1.0 - entry_2c - fee_2c, -entry_2c - fee_2c),
        np.nan,
    )
    return out


def summarize_subset(model: str, subset: str, trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "model": model,
            "subset": subset,
            "rows": 0,
            "events": 0,
            "proxy_pnl_0c": 0.0,
            "proxy_pnl_2c": 0.0,
            "official_pnl_0c": 0.0,
            "official_pnl_2c": 0.0,
            "premium": 0.0,
            "proxy_win_rate": 0.0,
            "official_win_rate": 0.0,
            "max_dd_proxy_2c": 0.0,
            "max_dd_official_2c": 0.0,
            "min_visible_qty": 0.0,
            "max_spread_cents": 0.0,
            "row_quality_blockers": "",
        }
    ordered = trades.sort_values("received_at_utc").copy()
    official_rows = ordered[ordered["official_result"].notna()].copy()
    blockers: list[str] = []
    duplicate_event_rows = int(len(ordered) - ordered["event_ticker"].nunique())
    nonexec = int(((ordered["visible_qty"] <= 0) | (ordered["spread_cents"] > 2)).sum())
    if duplicate_event_rows:
        blockers.append("duplicate_event_rows")
    if nonexec:
        blockers.append("non_executable_or_wide_quote_rows")
    official_pnl = pd.to_numeric(ordered["pnl_official_2c"], errors="coerce").dropna()
    return {
        "model": model,
        "subset": subset,
        "rows": int(len(ordered)),
        "events": int(ordered["event_ticker"].nunique()),
        "official_rows": int(len(official_rows)),
        "duplicate_event_rows": duplicate_event_rows,
        "non_executable_or_wide_quote_rows": nonexec,
        "proxy_pnl_0c": float(ordered["pnl"].sum()),
        "proxy_pnl_2c": float(ordered["pnl_2c"].sum()),
        "official_pnl_0c": float(pd.to_numeric(ordered["pnl_official_0c"], errors="coerce").sum(skipna=True)),
        "official_pnl_2c": float(pd.to_numeric(ordered["pnl_official_2c"], errors="coerce").sum(skipna=True)),
        "premium": float(ordered["premium"].sum()),
        "proxy_win_rate": float(ordered["win"].mean()),
        "official_win_rate": float(official_rows["official_win"].astype(float).mean()) if not official_rows.empty else 0.0,
        "max_dd_proxy_2c": max_drawdown(ordered["pnl_2c"]),
        "max_dd_official_2c": max_drawdown(official_pnl),
        "min_visible_qty": float(ordered["visible_qty"].min()),
        "max_spread_cents": float(ordered["spread_cents"].max()),
        "row_quality_blockers": ";".join(blockers),
    }


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        values = []
        for col in columns:
            value = row.get(col, "")
            if isinstance(value, float):
                value = f"{value:.6g}"
            values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_report(grid_dir: Path, aggregate: pd.DataFrame, by_window: pd.DataFrame, manifest: dict[str, Any]) -> None:
    columns = [
        "model",
        "subset",
        "rows",
        "events",
        "official_rows",
        "proxy_pnl_2c",
        "official_pnl_2c",
        "proxy_win_rate",
        "official_win_rate",
        "max_dd_proxy_2c",
        "row_quality_blockers",
    ]
    positive_proxy = aggregate[(aggregate["subset"].eq("proxy_all")) & (aggregate["proxy_pnl_2c"] > 0)]
    positive_official = aggregate[(aggregate["subset"].eq("official_subset")) & (aggregate["official_pnl_2c"] > 0)]
    lines = [
        "# BTC15M ML Live-WS Window Grid Summary",
        "",
        f"Grid: `{grid_dir}`",
        "",
        f"Windows with summaries: `{manifest['summary_windows']}`; windows with trade files: `{manifest['trade_windows']}`.",
        "",
        "## Aggregate",
        "",
        markdown_table(aggregate.to_dict("records"), columns),
        "",
        "## Verdict",
        "",
        "No ML model is promotion-ready.",
        "",
    ]
    if not positive_proxy.empty:
        lines.append("- Positive proxy rows remain proxy diagnostics unless official settlement coverage is large enough and aligned.")
    if not positive_official.empty:
        lines.append("- Positive official-subset rows are too few to support deployment.")
    if by_window.empty or by_window["official_rows"].sum() < 50:
        lines.append("- Official-settled ML coverage is too small for promotion.")
    lines.append("- Continue treating April-trained ML models as research diagnostics only.")
    (grid_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    grid_dir = args.grid_dir.resolve()
    summary, trades = read_grid(grid_dir)
    trades = add_official_pnl(trades)

    aggregate_rows: list[dict[str, Any]] = []
    by_window_rows: list[dict[str, Any]] = []
    if not trades.empty:
        for model, group in trades.groupby("model", sort=True):
            aggregate_rows.append(summarize_subset(str(model), "proxy_all", group))
            aggregate_rows.append(summarize_subset(str(model), "official_subset", group[group["official_result"].notna()]))
        for (model, window), group in trades.groupby(["model", "window"], sort=True):
            row = summarize_subset(str(model), str(window), group)
            by_window_rows.append(row)

    aggregate = pd.DataFrame(aggregate_rows)
    by_window = pd.DataFrame(by_window_rows)
    aggregate.to_csv(grid_dir / "ml_window_grid_official_proxy_summary.csv", index=False)
    by_window.to_csv(grid_dir / "ml_window_grid_by_window.csv", index=False)
    manifest = {
        "grid_dir": str(grid_dir),
        "summary_windows": int(summary["window"].nunique()) if "window" in summary.columns else 0,
        "trade_windows": int(trades["window"].nunique()) if "window" in trades.columns else 0,
        "trade_rows": int(len(trades)),
        "models": sorted(trades["model"].astype(str).unique().tolist()) if "model" in trades.columns and not trades.empty else [],
        "source_summary_rows": int(len(summary)),
    }
    (grid_dir / "summary_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(grid_dir, aggregate, by_window, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if not aggregate.empty:
        print(aggregate.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
