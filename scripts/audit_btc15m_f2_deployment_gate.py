#!/usr/bin/env python3
"""Final-style BTC15M F2 deployment gate audit.

This is an audit script, not a search script.  Candidate parameters are frozen
before the live replay is loaded.  The script joins:

* Predexon historical windows already produced by backtest_btc15m_f2_predexon_range.py
* One causal replay over the local websocket capture
* Official-result subset checks where the capture has Kalshi determinations

The output is deliberately conservative: a strategy can be "promising" and
still fail deployment if official/live evidence is too thin or source mismatch
risk remains unresolved.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    DEFAULT_CAPTURE_DB,
    add_pnl,
    add_proxy_results,
    compare_official_proxy,
    load_capture,
    metadata_from_lifecycle,
    metrics,
    prepare_quotes,
    select_f2_trades,
)


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_f2_deployment_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Candidate:
    name: str
    historical_name: str
    ttl_min: float = 10.0
    ttl_max: float = 12.0
    entry_max: float = 0.50
    visible_qty_min: float = 250.0
    quote_speed_min: float | None = None


CANDIDATES = [
    Candidate("q250", "ttl10_12_entry50_q250", visible_qty_min=250.0),
    Candidate("q250_qspeed05", "ttl10_12_entry50_q250_qspeed05", visible_qty_min=250.0, quote_speed_min=0.5),
    Candidate("q500", "ttl10_12_entry50_q500", visible_qty_min=500.0),
    Candidate("q1000", "ttl10_12_entry50_q1000", visible_qty_min=1000.0),
]

HISTORICAL_WINDOWS = {
    "apr01_14": BACKTEST_ROOT / "btc15m_f2_liq_regime_apr01_14_20260516_052801" / "summary.csv",
    "apr15_30": BACKTEST_ROOT / "btc15m_f2_liq_regime_apr15_30_20260516_052801" / "summary.csv",
    "may01_12": BACKTEST_ROOT / "btc15m_f2_liq_regime_may01_12_20260516_052801" / "summary.csv",
    "jan13_17": BACKTEST_ROOT / "btc15m_f2_liq_regime_jan13_17_20260516_052801" / "summary.csv",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-live-proxy-trades", type=int, default=50)
    p.add_argument("--min-live-official-trades", type=int, default=30)
    return p.parse_args()


def read_historical() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    by_hist = {c.historical_name: c.name for c in CANDIDATES}
    for window, path in HISTORICAL_WINDOWS.items():
        if not path.exists():
            rows.append({"candidate": "ALL", "window": window, "missing": True, "path": str(path)})
            continue
        df = pd.read_csv(path)
        df = df[df["split"].eq("all") & df["strategy"].isin(by_hist)].copy()
        for _, row in df.iterrows():
            rows.append(
                {
                    "candidate": by_hist[str(row["strategy"])],
                    "window": window,
                    "trades": int(row["trades"]),
                    "pnl": float(row["pnl_stress"]),
                    "return_on_100": float(row["return_on_100"]),
                    "premium": float(row["premium_stress"]),
                    "rop": float(row["rop"]),
                    "win_rate": float(row["win_rate"]),
                    "max_dd": float(row["max_dd"]),
                    "sharpe": float(row["sharpe"]),
                    "path": str(path.relative_to(PROJECT_ROOT)),
                }
            )
    return pd.DataFrame(rows)


def daily(trades: pd.DataFrame, pnl_col: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    work = trades.copy()
    work["day"] = pd.to_datetime(work["close_time"], utc=True).dt.strftime("%Y-%m-%d")
    rows = []
    for (candidate, day), group in work.groupby(["candidate", "day"], dropna=False):
        row = {"candidate": candidate, "day": day}
        row.update(metrics(group, pnl_col))
        rows.append(row)
    return pd.DataFrame(rows)


def live_replay(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    top, btc, lifecycle, decisions, info = load_capture(args.capture_db, args.start, args.end)
    meta, official_results = metadata_from_lifecycle(lifecycle)
    info.update(
        {
            "meta_markets": int(len(meta)),
            "official_result_markets": int(len(official_results)),
            "meta_first_close": str(meta["close_time"].min()) if not meta.empty else "",
            "meta_last_close": str(meta["close_time"].max()) if not meta.empty else "",
        }
    )
    q = prepare_quotes(top, btc, meta, official_results, btc_model="spot")
    q = add_proxy_results(q, btc, btc_model="spot")
    capture_end = pd.Timestamp(info["capture_end_utc"])
    q = q[q["close_time"].le(capture_end) & q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
    info["quote_rows_after_meta"] = int(len(q))

    trade_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []
    comparisons: dict[str, Any] = {}
    for candidate in CANDIDATES:
        trades, hit_info = select_f2_trades(
            q,
            fair_p_min=0.60,
            edge_cents_min=12.0,
            ttl_min=candidate.ttl_min,
            ttl_max=candidate.ttl_max,
            spread_max_cents=999.0,
            entry_min=0.01,
            entry_max=candidate.entry_max,
            visible_qty_min=candidate.visible_qty_min,
            quote_speed_min=candidate.quote_speed_min,
        )
        trades = add_pnl(trades, "proxy_result", "pnl_proxy_2c", 2)
        trades = add_pnl(trades, "official_result", "pnl_official_2c", 2)
        trades["candidate"] = candidate.name
        trade_frames.append(trades)

        compare = compare_official_proxy(trades)
        comparisons[candidate.name] = compare
        for result_mode, pnl_col in [
            ("live_proxy_2c", "pnl_proxy_2c"),
            ("live_official_2c_subset", "pnl_official_2c"),
        ]:
            metric = metrics(trades, pnl_col)
            summary_rows.append(
                {
                    "candidate": candidate.name,
                    "window": result_mode,
                    **metric,
                    "official_proxy_events_with_both": compare["events_with_both"],
                    "official_proxy_matches": compare["matches"],
                    "official_proxy_mismatches": compare["mismatches"],
                    "official_proxy_match_rate": compare["match_rate"],
                    "raw_hits": hit_info["raw_hits"],
                    "yes_hits": hit_info["yes_hits"],
                    "no_hits": hit_info["no_hits"],
                }
            )
    live_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return pd.DataFrame(summary_rows), live_trades, daily(live_trades, "pnl_proxy_2c"), {**info, "official_proxy_by_candidate": comparisons}


def gate(historical: pd.DataFrame, live: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    combined = pd.concat([historical, live], ignore_index=True, sort=False)
    rows: list[dict[str, Any]] = []
    for candidate in [c.name for c in CANDIDATES]:
        g = combined[combined["candidate"].eq(candidate)].copy()
        by_window = {str(r["window"]): r for _, r in g.iterrows()}

        def value(window: str, field: str, default: Any = None) -> Any:
            row = by_window.get(window)
            return row[field] if row is not None and field in row.index and pd.notna(row[field]) else default

        reasons: list[str] = []
        for window in ["apr01_14", "apr15_30", "may01_12"]:
            if float(value(window, "pnl", -999.0)) <= 0:
                reasons.append(f"{window}_not_positive")
        if float(value("jan13_17", "pnl", -999.0)) <= 0:
            reasons.append("jan_negative_or_source_mismatch")
        if int(value("live_proxy_2c", "trades", 0)) < args.min_live_proxy_trades:
            reasons.append("too_few_live_proxy_trades")
        if float(value("live_proxy_2c", "pnl", -999.0)) <= 0:
            reasons.append("live_proxy_not_positive")
        if int(value("live_official_2c_subset", "trades", 0)) < args.min_live_official_trades:
            reasons.append("too_few_live_official_trades")
        if float(value("live_official_2c_subset", "pnl", -999.0)) <= 0:
            reasons.append("live_official_not_positive")

        rows.append(
            {
                "candidate": candidate,
                "deploy_ready": not reasons,
                "status": "PASS" if not reasons else "FAIL",
                "failure_reasons": ";".join(reasons),
                "apr01_14_trades": value("apr01_14", "trades", 0),
                "apr01_14_pnl": value("apr01_14", "pnl", 0.0),
                "apr15_30_trades": value("apr15_30", "trades", 0),
                "apr15_30_pnl": value("apr15_30", "pnl", 0.0),
                "may01_12_trades": value("may01_12", "trades", 0),
                "may01_12_pnl": value("may01_12", "pnl", 0.0),
                "jan13_17_trades": value("jan13_17", "trades", 0),
                "jan13_17_pnl": value("jan13_17", "pnl", 0.0),
                "live_proxy_trades": value("live_proxy_2c", "trades", 0),
                "live_proxy_pnl": value("live_proxy_2c", "pnl", 0.0),
                "live_proxy_max_dd": value("live_proxy_2c", "max_dd", 0.0),
                "live_official_trades": value("live_official_2c_subset", "trades", 0),
                "live_official_pnl": value("live_official_2c_subset", "pnl", 0.0),
            }
        )
    return pd.DataFrame(rows)


def write_report(out_dir: Path, gate_df: pd.DataFrame, combined: pd.DataFrame, info: dict[str, Any]) -> None:
    def md_table(df: pd.DataFrame) -> str:
        if df.empty:
            return "_empty_"
        work = df.copy()
        for col in work.columns:
            work[col] = work[col].map(lambda v: "" if pd.isna(v) else str(v))
        widths = {col: max(len(str(col)), int(work[col].map(len).max())) for col in work.columns}
        header = "| " + " | ".join(str(col).ljust(widths[col]) for col in work.columns) + " |"
        sep = "| " + " | ".join("-" * widths[col] for col in work.columns) + " |"
        rows = [
            "| " + " | ".join(str(row[col]).ljust(widths[col]) for col in work.columns) + " |"
            for _, row in work.iterrows()
        ]
        return "\n".join([header, sep, *rows])

    lines = [
        "# BTC15M F2 Deployment Gate Audit",
        "",
        f"Generated: `{datetime.now().isoformat()}`",
        "",
        "## Verdict",
        "",
    ]
    if gate_df["deploy_ready"].any():
        lines.append("At least one candidate passed the configured gate.")
    else:
        lines.append("No candidate is deployment-ready under this gate.")
    lines.extend(
        [
            "",
            "Reasons are intentionally conservative: January source mismatch and too few official live-settlement observations both block promotion.",
            "",
            "## Gate Summary",
            "",
            md_table(gate_df),
            "",
            "## All Evidence Rows",
            "",
            md_table(combined.sort_values(["candidate", "window"])),
            "",
            "## Capture Info",
            "",
            "```json",
            json.dumps(info, indent=2, sort_keys=True, default=str),
            "```",
            "",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    historical = read_historical()
    live_summary, live_trades, live_daily, info = live_replay(args)
    combined = pd.concat([historical, live_summary], ignore_index=True, sort=False)
    gate_df = gate(historical, live_summary, args)

    historical.to_csv(args.out_dir / "historical_evidence.csv", index=False)
    live_summary.to_csv(args.out_dir / "live_ws_evidence.csv", index=False)
    live_trades.to_parquet(args.out_dir / "live_ws_trades.parquet", index=False, compression="zstd")
    live_daily.to_csv(args.out_dir / "live_ws_daily_proxy_2c.csv", index=False)
    combined.to_csv(args.out_dir / "combined_evidence.csv", index=False)
    gate_df.to_csv(args.out_dir / "gate_summary.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_report(args.out_dir, gate_df, combined, info)

    print(gate_df.to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
