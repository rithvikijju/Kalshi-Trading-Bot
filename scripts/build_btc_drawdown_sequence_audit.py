#!/usr/bin/env python3
"""Build BTC official-PnL drawdown sequence diagnostics.

This is a read-only promotion-control artifact. It does not trade, tune, start
processes, or change ledgers. The main promotion window is post-controlled
restart, but stale/current scopes are emitted as diagnostics so historical rows
do not get confused with deployment evidence.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_drawdown_sequence_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_SHADOW_OFFICIAL = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex"

FREEZE_UTC = "2026-05-18T04:17:44Z"

LEDGER_SPECS = [
    {
        "ledger": "btc15m_q250_qty500_firstskip_shadow",
        "family": "BTC15M",
        "candidate": "q250_firstskip_qty500",
        "min_official_rows": 100,
    },
    {
        "ledger": "btc15m_q250_qty500_firstskip_yes_shadow",
        "family": "BTC15M",
        "candidate": "q250_firstskip_qty500_yes",
        "min_official_rows": 100,
    },
    {
        "ledger": "btc15m_q1000_yes_shadow",
        "family": "BTC15M",
        "candidate": "q1000_yes",
        "min_official_rows": 100,
    },
    {
        "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
        "family": "BTC1H",
        "candidate": "btc1h_high_conf80_entry70_no_chase",
        "min_official_rows": 50,
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BTC drawdown sequence audit.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--shadow-official-dir", type=Path, default=DEFAULT_SHADOW_OFFICIAL)
    parser.add_argument("--restart-dir", type=Path, default=None)
    parser.add_argument("--restart-utc", default="", help="Override controlled restart completion UTC.")
    parser.add_argument("--freeze-utc", default=FREEZE_UTC)
    parser.add_argument("--max-drawdown-one-contract", type=float, default=10.0)
    parser.add_argument("--max-dd-to-pnl-ratio", type=float, default=0.50)
    return parser.parse_args()


def latest_dir(prefix: str) -> Path | None:
    matches = [p for p in BACKTEST_ROOT.glob(f"{prefix}*") if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def numeric(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(float("nan"), index=index, dtype=float)
    return pd.to_numeric(series, errors="coerce")


def resolve_restart(args: argparse.Namespace) -> tuple[Path | None, bool, str, str]:
    restart_dir = args.restart_dir or latest_dir("btc_paper_shadow_controlled_restart_")
    if args.restart_utc:
        return restart_dir, True, args.restart_utc, "cli_restart_utc"
    if restart_dir is None:
        return None, False, "", "missing_restart_dir"
    result = read_json(restart_dir / "restart_result.json")
    if result.get("completed_at"):
        return restart_dir, True, str(result["completed_at"]), "restart_result"
    plan = read_json(restart_dir / "restart_plan.json")
    if plan:
        return restart_dir, False, "", "restart_plan_not_executed"
    return restart_dir, False, "", "missing_restart_result"


def max_drawdown_from_cumulative(cumulative: pd.Series) -> pd.Series:
    if cumulative.empty:
        return pd.Series(dtype=float)
    return cumulative - cumulative.cummax()


def official_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "official_result" not in df.columns:
        return df.iloc[0:0].copy()
    result = df["official_result"].astype(str).str.lower()
    return df[result.isin(["yes", "no"])].copy()


def make_sequence(
    rows: pd.DataFrame,
    *,
    family: str,
    candidate: str,
    ledger: str,
    scope: str,
    promotion_window: bool,
) -> pd.DataFrame:
    official = official_rows(rows).copy()
    if official.empty:
        return pd.DataFrame(
            columns=[
                "family",
                "candidate",
                "ledger",
                "scope",
                "promotion_window",
                "sequence_index",
                "created_at",
                "market_ticker",
                "event_ticker",
                "side",
                "official_result",
                "proxy_result",
                "official_proxy_result_mismatch",
                "official_pnl",
                "cumulative_official_pnl",
                "running_peak_official_pnl",
                "drawdown",
            ]
        )
    official = official.sort_values(["created_at_ts", "market_ticker"], kind="mergesort").reset_index(drop=True)
    pnl = numeric(official.get("official_pnl"), official.index).fillna(0.0)
    cumulative = pnl.cumsum()
    peak = cumulative.cummax()
    drawdown = max_drawdown_from_cumulative(cumulative)
    out = pd.DataFrame(
        {
            "family": family,
            "candidate": candidate,
            "ledger": ledger,
            "scope": scope,
            "promotion_window": promotion_window,
            "sequence_index": range(1, len(official) + 1),
            "created_at": official.get("created_at", pd.Series("", index=official.index)),
            "market_ticker": official.get("market_ticker", pd.Series("", index=official.index)),
            "event_ticker": official.get("event_ticker", pd.Series("", index=official.index)),
            "side": official.get("side", pd.Series("", index=official.index)),
            "official_result": official.get("official_result", pd.Series("", index=official.index)),
            "proxy_result": official.get("proxy_result", pd.Series("", index=official.index)),
            "official_proxy_result_mismatch": official.get(
                "official_proxy_result_mismatch", pd.Series(False, index=official.index)
            ).map(boolish),
            "official_pnl": pnl.round(4),
            "cumulative_official_pnl": cumulative.round(4),
            "running_peak_official_pnl": peak.round(4),
            "drawdown": drawdown.round(4),
        }
    )
    return out


def summarize_sequence(
    seq: pd.DataFrame,
    *,
    family: str,
    candidate: str,
    ledger: str,
    scope: str,
    min_official_rows: int,
    restart_executed: bool,
    max_drawdown_one_contract: float,
    max_dd_to_pnl_ratio: float,
    promotion_window: bool,
) -> dict[str, Any]:
    official_rows_count = int(len(seq))
    official_pnl = float(pd.to_numeric(seq.get("official_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    max_dd = float(pd.to_numeric(seq.get("drawdown", pd.Series(dtype=float)), errors="coerce").fillna(0).min()) if official_rows_count else 0.0
    max_dd_abs = abs(max_dd)
    dd_to_pnl = max_dd_abs / official_pnl if official_pnl > 0 else float("inf") if max_dd_abs > 0 else 0.0
    mismatches = int(seq.get("official_proxy_result_mismatch", pd.Series(dtype=bool)).map(boolish).sum()) if official_rows_count else 0
    reasons: list[str] = []
    if promotion_window and not restart_executed:
        reasons.append("controlled_restart_not_executed")
    if official_rows_count < min_official_rows:
        reasons.append("too_few_official_rows_for_drawdown_gate")
    if official_pnl <= 0:
        reasons.append("official_pnl_not_positive")
    if max_dd_abs > max_drawdown_one_contract:
        reasons.append("max_drawdown_above_one_contract_limit")
    if official_pnl > 0 and dd_to_pnl > max_dd_to_pnl_ratio:
        reasons.append("max_drawdown_too_large_vs_profit")
    if mismatches:
        reasons.append("proxy_official_settlement_mismatch")
    if promotion_window:
        if not restart_executed:
            status = "PENDING_CONTROLLED_RESTART"
        elif reasons:
            status = "FAIL_DRAWDOWN_SEQUENCE_GATE"
        else:
            status = "PASS_DRAWDOWN_SEQUENCE_GATE_NOT_DEPLOYMENT"
    else:
        status = "DIAGNOSTIC_ONLY_NOT_PROMOTION"
    return {
        "family": family,
        "candidate": candidate,
        "ledger": ledger,
        "scope": scope,
        "promotion_window": promotion_window,
        "drawdown_gate_status": status,
        "drawdown_gate_pass": status == "PASS_DRAWDOWN_SEQUENCE_GATE_NOT_DEPLOYMENT",
        "official_rows": official_rows_count,
        "min_official_rows": min_official_rows,
        "official_pnl": round(official_pnl, 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_abs": round(max_dd_abs, 4),
        "max_drawdown_one_contract_limit": max_drawdown_one_contract,
        "drawdown_to_pnl_ratio": round(dd_to_pnl, 4) if dd_to_pnl != float("inf") else "inf",
        "max_dd_to_pnl_ratio_limit": max_dd_to_pnl_ratio,
        "proxy_official_mismatches": mismatches,
        "failure_reasons": ";".join(sorted(set(reasons))),
    }


def ledger_rows(trades: pd.DataFrame, ledger: str) -> pd.DataFrame:
    if trades.empty or "ledger" not in trades.columns:
        return pd.DataFrame()
    work = trades[trades["ledger"].astype(str).eq(ledger)].copy()
    if "created_at" in work.columns:
        work["created_at_ts"] = pd.to_datetime(work["created_at"], utc=True, errors="coerce")
    else:
        work["created_at_ts"] = pd.NaT
    return work


def markdown_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "(empty)"
    keep = [col for col in cols if col in df.columns]
    view = df[keep].fillna("").astype(str)
    widths = {col: max(len(col), *(len(value) for value in view[col].tolist())) for col in keep}
    header = " | ".join(col.ljust(widths[col]) for col in keep)
    sep = "-+-".join("-" * widths[col] for col in keep)
    body = [" | ".join(str(row[col]).ljust(widths[col]) for col in keep) for _, row in view.iterrows()]
    return "\n".join([header, sep, *body])


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    restart_dir, restart_executed, restart_utc, restart_source = resolve_restart(args)
    restart_ts = pd.to_datetime(restart_utc, utc=True, errors="coerce") if restart_utc else pd.NaT
    freeze_ts = pd.to_datetime(args.freeze_utc, utc=True, errors="coerce") if args.freeze_utc else pd.NaT
    trades = read_csv(args.shadow_official_dir / "shadow_official_trades.csv")

    all_sequences: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []
    for spec in LEDGER_SPECS:
        ledger = str(spec["ledger"])
        family = str(spec["family"])
        candidate = str(spec["candidate"])
        min_rows = int(spec["min_official_rows"])
        rows = ledger_rows(trades, ledger)
        scopes = [
            ("all_current_diagnostic", rows, False),
            (
                "since_freeze_current_diagnostic",
                rows[rows["created_at_ts"].ge(freeze_ts)].copy() if not rows.empty and not pd.isna(freeze_ts) else rows.iloc[0:0].copy(),
                False,
            ),
            (
                "post_restart_promotion_window",
                rows[rows["created_at_ts"].ge(restart_ts)].copy()
                if restart_executed and not rows.empty and not pd.isna(restart_ts)
                else rows.iloc[0:0].copy(),
                True,
            ),
        ]
        for scope, scope_rows, promotion_window in scopes:
            seq = make_sequence(
                scope_rows,
                family=family,
                candidate=candidate,
                ledger=ledger,
                scope=scope,
                promotion_window=promotion_window,
            )
            all_sequences.append(seq)
            summary_rows.append(
                summarize_sequence(
                    seq,
                    family=family,
                    candidate=candidate,
                    ledger=ledger,
                    scope=scope,
                    min_official_rows=min_rows,
                    restart_executed=restart_executed,
                    max_drawdown_one_contract=args.max_drawdown_one_contract,
                    max_dd_to_pnl_ratio=args.max_dd_to_pnl_ratio,
                    promotion_window=promotion_window,
                )
            )

    summary = pd.DataFrame(summary_rows)
    non_empty_sequences = [seq for seq in all_sequences if not seq.empty]
    if non_empty_sequences:
        details = pd.concat(non_empty_sequences, ignore_index=True)
    elif all_sequences:
        details = all_sequences[0].copy()
    else:
        details = pd.DataFrame()
    summary.to_csv(args.out_dir / "drawdown_sequence_summary.csv", index=False)
    details.to_csv(args.out_dir / "drawdown_sequence_details.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "shadow_official_dir": str(args.shadow_official_dir),
        "restart_dir": str(restart_dir or ""),
        "restart_executed": restart_executed,
        "restart_utc": restart_utc,
        "restart_source": restart_source,
        "freeze_utc": args.freeze_utc,
        "max_drawdown_one_contract": args.max_drawdown_one_contract,
        "max_dd_to_pnl_ratio": args.max_dd_to_pnl_ratio,
        "note": "Read-only drawdown sequence audit. Diagnostic scopes are not promotion evidence; only post_restart_promotion_window can become promotion-relevant after a controlled restart.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "family",
        "candidate",
        "scope",
        "drawdown_gate_status",
        "official_rows",
        "min_official_rows",
        "official_pnl",
        "max_drawdown",
        "drawdown_to_pnl_ratio",
        "proxy_official_mismatches",
        "failure_reasons",
    ]
    report = [
        "# BTC Drawdown Sequence Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Restart executed: `{restart_executed}`",
        f"Restart UTC: `{restart_utc}`",
        "",
        "## Summary",
        "",
        markdown_table(summary, compact_cols),
        "",
        "## Interpretation",
        "",
        "- This is a drawdown-path audit, not a deployment approval.",
        "- `all_current_diagnostic` and `since_freeze_current_diagnostic` include stale-schema/current rows and are diagnostic only.",
        "- `post_restart_promotion_window` is the only scope that can become promotion-relevant, and it is empty until an explicit controlled paper restart/start is executed.",
        "- The sequence details CSV contains row-level official PnL, cumulative PnL, running peak, and drawdown.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
