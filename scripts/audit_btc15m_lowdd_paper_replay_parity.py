#!/usr/bin/env python3
"""Audit BTC15M lowdd paper fills against sidecar and replay evidence.

The post-restart paper ledger is the forward evidence clock. This check verifies
that each ledger row has a matching live sidecar selected signal and paper-fill
order decision, then compares the same event against the generic offline replay
rows. A generic replay price mismatch is reported separately because it can mean
the replay harness is not policy-equivalent to the live wrapper.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_lowdd_paper_replay_parity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-trades", type=Path, required=True)
    parser.add_argument("--replay-trades", type=Path)
    parser.add_argument("--materialized-db", type=Path, help="DuckDB materialized from the lowdd replay sidecar.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--strategy", default="current_lowdd_no_rv")
    parser.add_argument("--price-tolerance", type=float, default=1e-9)
    parser.add_argument("--time-tolerance-sec", type=float, default=10.0)
    parser.add_argument(
        "--max-signal-order-reprice-cents",
        type=float,
        default=2.0,
        help="Maximum allowed selected-signal to order-entry worse reprice in cents.",
    )
    return parser.parse_args()


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ["created_at", "received_at_utc", "close_time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    for col in ["entry_price", "contracts", "fee", "official_pnl", "premium", "pnl"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["event_ticker", "market_ticker", "side", "strategy"]:
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


def load_sidecar_tables(materialized_db: Path | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if materialized_db is None:
        return pd.DataFrame(), pd.DataFrame()
    import duckdb

    con = duckdb.connect(str(materialized_db), read_only=True)
    try:
        selected = con.execute(
            """
            SELECT TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
                   event_ticker,
                   selected_market AS market_ticker,
                   selected_side AS side,
                   entry_price,
                   net_edge_cents,
                   model_p_yes,
                   action,
                   detail
            FROM signal_scan
            WHERE action = 'selected'
            ORDER BY TRY_CAST(received_at_utc AS TIMESTAMPTZ)
            """
        ).fetchdf()
        orders = con.execute(
            """
            SELECT TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
                   event_ticker,
                   market_ticker,
                   side,
                   contracts,
                   entry_price,
                   estimated_cost,
                   action,
                   detail
            FROM order_decision
            WHERE action = 'paper_fill'
            ORDER BY TRY_CAST(received_at_utc AS TIMESTAMPTZ)
            """
        ).fetchdf()
    finally:
        con.close()
    for df in (selected, orders):
        if not df.empty:
            df["received_at_utc"] = pd.to_datetime(df["received_at_utc"], utc=True, errors="coerce")
            for col in ["entry_price", "contracts", "estimated_cost", "net_edge_cents", "model_p_yes"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            for col in ["event_ticker", "market_ticker", "side", "action", "detail"]:
                if col in df.columns:
                    df[col] = df[col].astype(str)
    return selected, orders


def strategy_replay_rows(path: Path | None, strategy: str) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    replay = load_csv(path)
    if "strategy" not in replay.columns:
        return pd.DataFrame()
    replay = replay[replay["strategy"].eq(strategy)].copy()
    if "received_at_utc" not in replay.columns:
        replay["received_at_utc"] = pd.NaT
    return replay


def nearest_match(
    rows: pd.DataFrame,
    paper: pd.Series,
    time_col: str,
    price_col: str = "entry_price",
) -> pd.Series | None:
    if rows.empty:
        return None
    mask = (
        rows["event_ticker"].astype(str).eq(str(paper.get("event_ticker")))
        & rows["market_ticker"].astype(str).eq(str(paper.get("market_ticker")))
        & rows["side"].astype(str).str.lower().eq(str(paper.get("side")).lower())
    )
    subset = rows[mask].copy()
    if subset.empty:
        return None
    paper_ts = paper.get("created_at")
    if pd.notna(paper_ts) and time_col in subset.columns:
        subset["_abs_dt_sec"] = (subset[time_col] - paper_ts).dt.total_seconds().abs()
        subset = subset.sort_values(["_abs_dt_sec", time_col])
    elif price_col in subset.columns:
        subset["_abs_price"] = (pd.to_numeric(subset[price_col], errors="coerce") - float(paper.get("entry_price"))).abs()
        subset = subset.sort_values("_abs_price")
    return subset.iloc[0]


def price_diff(match: pd.Series | None, paper: pd.Series) -> float | None:
    if match is None:
        return None
    try:
        return float(match.get("entry_price")) - float(paper.get("entry_price"))
    except Exception:
        return None


def time_diff_sec(match: pd.Series | None, paper: pd.Series, time_col: str = "received_at_utc") -> float | None:
    if match is None:
        return None
    paper_ts = paper.get("created_at")
    match_ts = match.get(time_col)
    if pd.isna(paper_ts) or pd.isna(match_ts):
        return None
    return float((match_ts - paper_ts).total_seconds())


def diff_ok(value: float | None, tolerance: float) -> bool:
    return value is not None and abs(value) <= tolerance


def time_ok(value: float | None, tolerance: float) -> bool:
    return value is not None and abs(value) <= tolerance


def row_verdict(
    signal_diff: float | None,
    order_diff: float | None,
    signal_order_reprice_cents: float | None,
    signal_dt: float | None,
    order_dt: float | None,
    replay_match: pd.Series | None,
    replay_diff: float | None,
    price_tolerance: float,
    time_tolerance: float,
    max_signal_order_reprice_cents: float,
) -> str:
    if signal_diff is None or order_diff is None:
        return "fail_live_sidecar_missing"
    order_matches_paper = diff_ok(order_diff, price_tolerance)
    signal_matches_paper = diff_ok(signal_diff, price_tolerance)
    signal_order_reprice_ok = (
        signal_order_reprice_cents is not None
        and signal_order_reprice_cents <= max_signal_order_reprice_cents + 1e-9
    )
    if not order_matches_paper:
        return "fail_live_sidecar_price_mismatch"
    if not time_ok(signal_dt, time_tolerance) or not time_ok(order_dt, time_tolerance):
        return "fail_live_sidecar_time_mismatch"
    if not signal_matches_paper:
        if not signal_order_reprice_ok:
            return "fail_signal_order_reprice_over_limit"
        if replay_match is None:
            return "live_sidecar_order_parity_pass_signal_reprice_generic_replay_missing"
        if not diff_ok(replay_diff, price_tolerance):
            return "live_sidecar_order_parity_pass_signal_reprice_generic_replay_price_mismatch"
        return "live_sidecar_order_parity_pass_signal_reprice"
    if replay_match is None:
        return "live_sidecar_parity_pass_generic_replay_missing"
    if not diff_ok(replay_diff, price_tolerance):
        return "live_sidecar_parity_pass_generic_replay_price_mismatch"
    return "full_live_and_generic_replay_parity_pass"


def audit_rows(
    paper: pd.DataFrame,
    selected: pd.DataFrame,
    orders: pd.DataFrame,
    replay: pd.DataFrame,
    price_tolerance: float,
    time_tolerance: float,
    max_signal_order_reprice_cents: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, paper_row in paper.sort_values("created_at").iterrows():
        signal = nearest_match(selected, paper_row, "received_at_utc")
        order = nearest_match(orders, paper_row, "received_at_utc")
        replay_match = nearest_match(replay, paper_row, "received_at_utc")
        signal_diff = price_diff(signal, paper_row)
        order_diff = price_diff(order, paper_row)
        replay_diff = price_diff(replay_match, paper_row)
        signal_dt = time_diff_sec(signal, paper_row)
        order_dt = time_diff_sec(order, paper_row)
        replay_dt = time_diff_sec(replay_match, paper_row)
        signal_order_reprice_cents = None
        if signal is not None and order is not None:
            try:
                signal_order_reprice_cents = (float(order.get("entry_price")) - float(signal.get("entry_price"))) * 100.0
            except Exception:
                signal_order_reprice_cents = None
        rows.append(
            {
                "paper_id": paper_row.get("id"),
                "event_ticker": paper_row.get("event_ticker"),
                "market_ticker": paper_row.get("market_ticker"),
                "side": paper_row.get("side"),
                "paper_created_at": paper_row.get("created_at"),
                "paper_contracts": paper_row.get("contracts"),
                "paper_entry_price": paper_row.get("entry_price"),
                "paper_official_pnl": paper_row.get("official_pnl"),
                "signal_time": None if signal is None else signal.get("received_at_utc"),
                "signal_entry_price": None if signal is None else signal.get("entry_price"),
                "signal_price_diff": signal_diff,
                "signal_time_diff_sec": signal_dt,
                "order_time": None if order is None else order.get("received_at_utc"),
                "order_entry_price": None if order is None else order.get("entry_price"),
                "order_price_diff": order_diff,
                "order_time_diff_sec": order_dt,
                "signal_order_reprice_cents": signal_order_reprice_cents,
                "replay_time": None if replay_match is None else replay_match.get("received_at_utc"),
                "replay_entry_price": None if replay_match is None else replay_match.get("entry_price"),
                "replay_price_diff": replay_diff,
                "replay_time_diff_sec": replay_dt,
                "replay_single_contract_pnl": None if replay_match is None else replay_match.get("pnl"),
                "verdict": row_verdict(
                    signal_diff,
                    order_diff,
                    signal_order_reprice_cents,
                    signal_dt,
                    order_dt,
                    replay_match,
                    replay_diff,
                    price_tolerance,
                    time_tolerance,
                    max_signal_order_reprice_cents,
                ),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict_counts[str(row["verdict"])] = verdict_counts.get(str(row["verdict"]), 0) + 1
    live_pass = sum(
        count
        for verdict, count in verdict_counts.items()
        if (
            verdict.startswith("full_live")
            or verdict.startswith("live_sidecar_parity_pass")
            or verdict.startswith("live_sidecar_order_parity_pass")
        )
    )
    reprice_values = [
        float(row["signal_order_reprice_cents"])
        for row in rows
        if row.get("signal_order_reprice_cents") is not None and abs(float(row["signal_order_reprice_cents"])) > 1e-9
    ]
    signal_reprice_rows = len(reprice_values)
    generic_mismatch = (
        verdict_counts.get("live_sidecar_parity_pass_generic_replay_price_mismatch", 0)
        + verdict_counts.get("live_sidecar_order_parity_pass_signal_reprice_generic_replay_price_mismatch", 0)
    )
    live_sidecar_price_mismatch = verdict_counts.get("fail_live_sidecar_price_mismatch", 0)
    signal_reprice_over_limit = verdict_counts.get("fail_signal_order_reprice_over_limit", 0)
    return {
        "paper_rows": len(rows),
        "live_sidecar_parity_pass_rows": live_pass,
        "live_sidecar_price_mismatch_rows": live_sidecar_price_mismatch,
        "signal_order_reprice_rows": signal_reprice_rows,
        "signal_order_reprice_over_limit_rows": signal_reprice_over_limit,
        "max_signal_order_reprice_cents": max(reprice_values) if reprice_values else 0.0,
        "max_signal_order_worse_reprice_cents": max([value for value in reprice_values if value > 0.0], default=0.0),
        "generic_replay_price_mismatch_rows": generic_mismatch,
        "verdict_counts": verdict_counts,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    paper = load_csv(args.paper_trades)
    selected, orders = load_sidecar_tables(args.materialized_db)
    replay = strategy_replay_rows(args.replay_trades, args.strategy)
    rows = audit_rows(
        paper,
        selected,
        orders,
        replay,
        args.price_tolerance,
        args.time_tolerance_sec,
        args.max_signal_order_reprice_cents,
    )
    summary = summarize(rows)
    write_csv(args.out_dir / "paper_replay_parity_rows.csv", rows)
    (args.out_dir / "paper_replay_parity_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "paper_trades": str(args.paper_trades),
        "replay_trades": str(args.replay_trades) if args.replay_trades else "",
        "materialized_db": str(args.materialized_db) if args.materialized_db else "",
        "strategy": args.strategy,
        "price_tolerance": args.price_tolerance,
        "time_tolerance_sec": args.time_tolerance_sec,
        "max_signal_order_reprice_cents": args.max_signal_order_reprice_cents,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report_df = pd.DataFrame(rows)
    report_cols = [
        "paper_id",
        "event_ticker",
        "side",
        "paper_entry_price",
        "signal_entry_price",
        "order_entry_price",
        "signal_order_reprice_cents",
        "replay_entry_price",
        "signal_time_diff_sec",
        "order_time_diff_sec",
        "replay_time_diff_sec",
        "verdict",
    ]
    report = [
        "# BTC15M Lowdd Paper/Replay Parity Audit",
        "",
        f"Generated: `{manifest['created_at_utc']}`",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(summary, indent=2, default=str),
        "```",
        "",
        "## Rows",
        "",
        markdown_table(report_df[[c for c in report_cols if c in report_df.columns]]),
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), **summary}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
