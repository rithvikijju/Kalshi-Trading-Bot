#!/usr/bin/env python3
"""Build a promotion gate for BTC15M lowdd forward paper evidence.

This script is intentionally narrow. It consumes the lowdd wrapper's
sidecar-selected official replay, the paper/live-sidecar parity audit, and the
post-restart paper official summary. It does not tune thresholds or rescore
market data.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
RUNTIME_BACKTEST_ROOT = PROJECT_ROOT / "runtime" / "remote_backtests"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_lowdd_forward_promotion_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--selected-dir", type=Path, help="Directory containing selected_signal_summary.csv/trades.csv.")
    parser.add_argument("--parity-dir", type=Path, help="Directory containing paper_replay_parity_summary.json/rows.csv.")
    parser.add_argument("--paper-dir", type=Path, help="Directory containing lowdd_postrestart_summary.csv/trades.csv.")
    parser.add_argument("--min-selected-rows", type=int, default=50)
    parser.add_argument("--min-settled-rows", type=int, default=50)
    parser.add_argument("--min-order-rows", type=int, default=50)
    parser.add_argument("--min-paper-rows", type=int, default=50)
    parser.add_argument("--min-live-sidecar-parity-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-order-price-mismatch-rows", type=int, default=0)
    parser.add_argument("--min-order-max-drawdown", type=float, default=-5.0)
    parser.add_argument("--candidate", default="btc15m_lowdd_current_wrapper")
    return parser.parse_args()


def newest_dir(prefix: str) -> Path | None:
    matches: list[Path] = []
    for root in (BACKTEST_ROOT, RUNTIME_BACKTEST_ROOT):
        if root.exists():
            matches.extend([path for path in root.glob(f"{prefix}*") if path.is_dir()])
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def resolve_dirs(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        "selected": args.selected_dir or newest_dir("btc15m_lowdd_sidecar_selected_"),
        "parity": args.parity_dir or newest_dir("btc15m_lowdd_paper_replay_parity_"),
        "paper": args.paper_dir or newest_dir("btc15m_lowdd_postrestart"),
    }


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def read_one_csv(path: Path | None) -> dict[str, Any]:
    df = read_csv(path)
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "" or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "" or pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default


def approx_equal(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def build_drawdown_rows(trades: pd.DataFrame) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    work = trades.copy()
    if "selected_at_utc" in work.columns:
        work["selected_at_utc_ts"] = pd.to_datetime(work["selected_at_utc"], utc=True, errors="coerce")
        work = work.sort_values(["selected_at_utc_ts", "market_ticker"], na_position="last")
    rows: list[dict[str, Any]] = []
    order_equity = 0.0
    order_peak = 0.0
    signal_equity = 0.0
    signal_peak = 0.0
    for index, row in work.reset_index(drop=True).iterrows():
        order_pnl = to_float(row.get("order_scaled_pnl"), 0.0)
        signal_pnl = to_float(row.get("signal_one_contract_pnl"), 0.0)
        order_equity += order_pnl
        signal_equity += signal_pnl
        order_peak = max(order_peak, order_equity)
        signal_peak = max(signal_peak, signal_equity)
        rows.append(
            {
                "sequence": index + 1,
                "selected_at_utc": row.get("selected_at_utc", ""),
                "event_ticker": row.get("event_ticker", ""),
                "market_ticker": row.get("market_ticker", ""),
                "side": row.get("side", ""),
                "official_result": row.get("official_result", ""),
                "signal_one_contract_pnl": round(signal_pnl, 6),
                "signal_cumulative_pnl": round(signal_equity, 6),
                "signal_drawdown": round(signal_equity - signal_peak, 6),
                "order_scaled_pnl": round(order_pnl, 6),
                "order_cumulative_pnl": round(order_equity, 6),
                "order_drawdown": round(order_equity - order_peak, 6),
            }
        )
    return rows


def evaluate_gate(
    *,
    candidate: str,
    selected_summary: dict[str, Any],
    parity_summary: dict[str, Any],
    paper_summary: dict[str, Any],
    args: argparse.Namespace,
    missing_inputs: list[str] | None = None,
) -> dict[str, Any]:
    missing_inputs = missing_inputs or []
    selected_rows = to_int(selected_summary.get("selected_rows"))
    settled_rows = to_int(selected_summary.get("settled_rows"))
    order_rows = to_int(selected_summary.get("order_rows"))
    paper_rows = to_int(paper_summary.get("trades"))
    paper_settled_rows = to_int(paper_summary.get("settled"))
    paper_parity_rows = to_int(parity_summary.get("paper_rows"))
    live_pass_rows = to_int(parity_summary.get("live_sidecar_parity_pass_rows"))
    generic_mismatch_rows = to_int(parity_summary.get("generic_replay_price_mismatch_rows"))
    order_price_mismatch_rows = to_int(selected_summary.get("order_price_mismatch_rows"))
    live_sidecar_price_mismatch_rows = to_int(parity_summary.get("live_sidecar_price_mismatch_rows"))
    signal_order_reprice_rows = to_int(parity_summary.get("signal_order_reprice_rows"))
    signal_order_reprice_over_limit_rows = to_int(parity_summary.get("signal_order_reprice_over_limit_rows"))
    max_signal_order_worse_reprice_cents = to_float(parity_summary.get("max_signal_order_worse_reprice_cents"))
    signal_pnl = to_float(selected_summary.get("signal_one_contract_pnl"))
    order_pnl = to_float(selected_summary.get("order_scaled_pnl"))
    paper_pnl = to_float(paper_summary.get("official_pnl"))
    signal_premium = to_float(selected_summary.get("signal_one_contract_premium"))
    order_premium = to_float(selected_summary.get("order_scaled_premium"))
    paper_premium = to_float(paper_summary.get("official_premium"))
    signal_max_drawdown = to_float(selected_summary.get("signal_max_drawdown"))
    order_max_drawdown = to_float(selected_summary.get("order_max_drawdown"))

    blockers: list[str] = [f"missing_{name}_input" for name in missing_inputs]
    advisories: list[str] = []
    has_reprice_classification = "signal_order_reprice_rows" in parity_summary
    selected_order_reprice_classified = (
        has_reprice_classification
        and order_price_mismatch_rows <= signal_order_reprice_rows
        and signal_order_reprice_over_limit_rows == 0
        and live_sidecar_price_mismatch_rows == 0
    )

    if selected_rows < args.min_selected_rows:
        blockers.append("selected_rows_below_min")
    if settled_rows < args.min_settled_rows:
        blockers.append("settled_rows_below_min")
    if order_rows < args.min_order_rows:
        blockers.append("order_rows_below_min")
    if paper_settled_rows < args.min_paper_rows:
        blockers.append("paper_settled_rows_below_min")
    if selected_rows != settled_rows:
        blockers.append("unsettled_selected_rows")
    if paper_rows != paper_settled_rows:
        blockers.append("unsettled_paper_rows")
    if live_sidecar_price_mismatch_rows > 0:
        blockers.append("live_sidecar_price_mismatch_rows_nonzero")
    if signal_order_reprice_over_limit_rows > 0:
        blockers.append("signal_order_reprice_over_limit_rows_nonzero")
    if order_price_mismatch_rows > args.max_order_price_mismatch_rows and not selected_order_reprice_classified:
        blockers.append("order_price_mismatch_rows_nonzero")
    if order_price_mismatch_rows > 0 and selected_order_reprice_classified:
        advisories.append("selected_order_reprice_within_config_limit")
    if order_pnl <= 0:
        blockers.append("order_scaled_pnl_not_positive")
    if signal_pnl <= 0:
        blockers.append("signal_one_contract_pnl_not_positive")
    if order_max_drawdown < args.min_order_max_drawdown:
        blockers.append("order_max_drawdown_below_limit")

    live_pass_rate = live_pass_rows / paper_parity_rows if paper_parity_rows else 0.0
    if paper_parity_rows != paper_rows:
        blockers.append("paper_parity_row_count_mismatch")
    if live_pass_rate < args.min_live_sidecar_parity_pass_rate:
        blockers.append("live_sidecar_parity_not_all_rows")
    if order_rows != paper_settled_rows:
        blockers.append("order_paper_row_count_mismatch")
    if not approx_equal(order_pnl, paper_pnl):
        blockers.append("order_paper_pnl_mismatch")
    if paper_premium and not approx_equal(order_premium, paper_premium):
        blockers.append("order_paper_premium_mismatch")
    if generic_mismatch_rows > 0:
        advisories.append("generic_replay_price_mismatch_sidecar_selected_is_authoritative")

    price_parity_ok = (
        live_sidecar_price_mismatch_rows == 0
        and signal_order_reprice_over_limit_rows == 0
        and (order_price_mismatch_rows == 0 or selected_order_reprice_classified)
    )
    unique_blockers = sorted(set(blockers))
    production_ready = len(unique_blockers) == 0
    if production_ready:
        research_status = "production_ready_pending_human_approval"
    elif (
        order_pnl > 0
        and signal_pnl > 0
        and price_parity_ok
        and live_pass_rate >= args.min_live_sidecar_parity_pass_rate
    ):
        research_status = "research_promising_insufficient_forward_sample"
    else:
        research_status = "research_blocked_or_rejected"

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate": candidate,
        "production_ready": production_ready,
        "research_status": research_status,
        "blockers": ";".join(unique_blockers),
        "advisories": ";".join(sorted(set(advisories))),
        "selected_rows": selected_rows,
        "settled_rows": settled_rows,
        "order_rows": order_rows,
        "paper_rows": paper_rows,
        "paper_settled_rows": paper_settled_rows,
        "paper_parity_rows": paper_parity_rows,
        "live_sidecar_parity_pass_rows": live_pass_rows,
        "live_sidecar_parity_pass_rate": round(live_pass_rate, 6),
        "generic_replay_price_mismatch_rows": generic_mismatch_rows,
        "order_price_mismatch_rows": order_price_mismatch_rows,
        "live_sidecar_price_mismatch_rows": live_sidecar_price_mismatch_rows,
        "signal_order_reprice_rows": signal_order_reprice_rows,
        "signal_order_reprice_over_limit_rows": signal_order_reprice_over_limit_rows,
        "max_signal_order_worse_reprice_cents": round(max_signal_order_worse_reprice_cents, 6),
        "signal_one_contract_pnl": round(signal_pnl, 6),
        "signal_one_contract_premium": round(signal_premium, 6),
        "signal_max_drawdown": round(signal_max_drawdown, 6),
        "order_scaled_pnl": round(order_pnl, 6),
        "order_scaled_premium": round(order_premium, 6),
        "order_max_drawdown": round(order_max_drawdown, 6),
        "paper_official_pnl": round(paper_pnl, 6),
        "paper_official_premium": round(paper_premium, 6),
        "min_selected_rows": args.min_selected_rows,
        "min_settled_rows": args.min_settled_rows,
        "min_order_rows": args.min_order_rows,
        "min_paper_rows": args.min_paper_rows,
        "min_order_max_drawdown": args.min_order_max_drawdown,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dirs = resolve_dirs(args)
    selected_dir = dirs["selected"]
    parity_dir = dirs["parity"]
    paper_dir = dirs["paper"]
    missing_inputs = [name for name, path in dirs.items() if path is None]

    selected_summary = read_one_csv(None if selected_dir is None else selected_dir / "selected_signal_summary.csv")
    selected_trades = read_csv(None if selected_dir is None else selected_dir / "selected_signal_trades.csv")
    parity_summary = read_json(None if parity_dir is None else parity_dir / "paper_replay_parity_summary.json")
    paper_summary = read_one_csv(None if paper_dir is None else paper_dir / "lowdd_postrestart_summary.csv")

    if not selected_summary:
        missing_inputs.append("selected_summary")
    if not parity_summary:
        missing_inputs.append("parity_summary")
    if not paper_summary:
        missing_inputs.append("paper_summary")

    summary = evaluate_gate(
        candidate=args.candidate,
        selected_summary=selected_summary,
        parity_summary=parity_summary,
        paper_summary=paper_summary,
        args=args,
        missing_inputs=missing_inputs,
    )
    drawdown_rows = build_drawdown_rows(selected_trades)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "lowdd_forward_promotion_gate_summary.csv", [summary])
    write_csv(args.out_dir / "lowdd_forward_drawdown_sequence.csv", drawdown_rows)
    (args.out_dir / "lowdd_forward_promotion_gate_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    manifest = {
        "created_at_utc": summary["created_at_utc"],
        "selected_dir": str(selected_dir) if selected_dir else "",
        "parity_dir": str(parity_dir) if parity_dir else "",
        "paper_dir": str(paper_dir) if paper_dir else "",
        "notes": [
            "Gate consumes sidecar-selected replay, paper/live-sidecar parity, and post-restart official paper rows.",
            "Generic replay price mismatches are advisory when live sidecar-selected parity passes.",
            "Selected-signal to order-entry reprices are advisory only when the paper order matches the live sidecar order and the reprice stayed within the audit limit.",
            "Passing this gate still requires explicit human approval before any live deployment.",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report_df = pd.DataFrame([summary])
    drawdown_df = pd.DataFrame(drawdown_rows)
    report_cols = [
        "candidate",
        "production_ready",
        "research_status",
        "blockers",
        "advisories",
        "selected_rows",
        "settled_rows",
        "order_rows",
        "paper_settled_rows",
        "order_scaled_pnl",
        "order_max_drawdown",
        "order_price_mismatch_rows",
        "signal_order_reprice_rows",
        "max_signal_order_worse_reprice_cents",
    ]
    drawdown_cols = [
        "sequence",
        "selected_at_utc",
        "market_ticker",
        "side",
        "official_result",
        "order_scaled_pnl",
        "order_cumulative_pnl",
        "order_drawdown",
    ]
    report = [
        "# BTC15M Lowdd Forward Promotion Gate",
        "",
        f"Generated: `{summary['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table(report_df[[col for col in report_cols if col in report_df.columns]]),
        "",
        "## Drawdown Sequence",
        "",
        markdown_table(drawdown_df[[col for col in drawdown_cols if col in drawdown_df.columns]]) if not drawdown_df.empty else "_empty_",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), **summary}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
