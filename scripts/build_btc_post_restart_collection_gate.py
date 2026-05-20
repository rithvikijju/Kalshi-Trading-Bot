#!/usr/bin/env python3
"""Gate future post-restart BTC paper-shadow collection evidence.

This is a deployment-control artifact. It does not restart processes and it
does not search thresholds. It checks whether rows written after a controlled
paper-shadow restart are official-settled, execution-realistic, and numerous
enough to be considered promotion evidence.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc_post_restart_collection_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_SHADOW_OFFICIAL = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex"

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

REQUIRED_REALISM_COLUMNS = [
    "quote_age_ms",
    "top_visible_qty",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate post-restart BTC shadow collection evidence.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--shadow-official-dir", type=Path, default=DEFAULT_SHADOW_OFFICIAL)
    parser.add_argument("--restart-dir", type=Path, default=None)
    parser.add_argument("--restart-utc", default="", help="Override restart completion UTC for row filtering.")
    parser.add_argument("--max-quote-age-ms", type=float, default=250.0)
    parser.add_argument("--min-btc15m-official-rows", type=int, default=100)
    parser.add_argument("--min-btc1h-official-rows", type=int, default=50)
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
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "" or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def present_mask(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    series = df[col]
    text = series.astype(str).str.strip().str.lower()
    return series.notna() & ~text.isin(["", "nan", "none", "nat", "<na>"])


def numeric(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def rate(mask: pd.Series) -> float:
    if mask.empty:
        return 0.0
    return float(mask.fillna(False).mean())


def max_drawdown(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if x.empty:
        return 0.0
    return float((x - x.cummax()).min())


def side_ask(df: pd.DataFrame) -> pd.Series:
    side = df.get("side", pd.Series("", index=df.index)).astype(str).str.lower()
    yes = numeric(df, "yes_ask")
    no = numeric(df, "no_ask")
    return yes.where(side.eq("yes"), no.where(side.eq("no"), float("nan")))


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


def min_rows_for(spec: dict[str, Any], args: argparse.Namespace) -> int:
    if spec["family"] == "BTC15M":
        return int(args.min_btc15m_official_rows)
    if spec["family"] == "BTC1H":
        return int(args.min_btc1h_official_rows)
    return int(spec["min_official_rows"])


def gate_ledger(
    df: pd.DataFrame,
    spec: dict[str, Any],
    *,
    restart_executed: bool,
    restart_utc: str,
    restart_source: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    ledger = spec["ledger"]
    work = df[df.get("ledger", pd.Series(dtype=str)).astype(str).eq(ledger)].copy() if not df.empty else pd.DataFrame()
    if "created_at" in work.columns:
        work["created_at_ts"] = pd.to_datetime(work["created_at"], utc=True, errors="coerce")
    else:
        work["created_at_ts"] = pd.NaT
    restart_ts = pd.to_datetime(restart_utc, utc=True, errors="coerce") if restart_utc else pd.NaT
    if restart_executed and not pd.isna(restart_ts) and not work.empty:
        post = work[work["created_at_ts"].ge(restart_ts)].copy()
    else:
        post = work.iloc[0:0].copy()

    official = post[post.get("official_result", pd.Series(dtype=str)).astype(str).str.lower().isin(["yes", "no"])].copy()
    pending = post[~post.index.isin(official.index)].copy()
    pnl = numeric(official, "official_pnl")
    proxy_pnl = numeric(official, "proxy_pnl")
    official_win = official.get("official_win", pd.Series(dtype=bool)).map(as_bool) if not official.empty else pd.Series(dtype=bool)
    required_presence = {
        col: rate(present_mask(post, col))
        for col in REQUIRED_REALISM_COLUMNS
    }
    complete_realism = pd.Series(True, index=post.index)
    for col in REQUIRED_REALISM_COLUMNS:
        complete_realism &= present_mask(post, col)
    quote_age = numeric(post, "quote_age_ms")
    contracts = numeric(post, "contracts")
    top_visible = numeric(post, "top_visible_qty")
    entry = numeric(post, "entry_price")
    expected_entry = side_ask(post)
    quote_age_ok = quote_age.between(0.0, args.max_quote_age_ms)
    top_visible_ok = top_visible.ge(contracts).where(contracts.notna(), False)
    entry_match = (entry - expected_entry).abs().le(1e-9) & expected_entry.notna()
    event_count = int(post.get("event_ticker", pd.Series(dtype=str)).astype(str).nunique()) if not post.empty else 0
    duplicate_event_rows = int(post.get("event_ticker", pd.Series(dtype=str)).astype(str).duplicated().sum()) if not post.empty else 0
    mismatches = (
        official.get("official_proxy_result_mismatch", pd.Series(dtype=bool)).map(as_bool)
        if not official.empty
        else pd.Series(dtype=bool)
    )

    min_official_rows = min_rows_for(spec, args)
    reasons: list[str] = []
    if not restart_executed:
        reasons.append("controlled_restart_not_executed")
    if restart_executed and pd.isna(restart_ts):
        reasons.append("restart_utc_unparseable")
    if len(post) == 0:
        reasons.append("no_post_restart_paper_rows")
    if len(official) < min_official_rows:
        reasons.append("too_few_post_restart_official_rows")
    if len(pending) > 0:
        reasons.append("pending_official_settlement_rows")
    if len(official) and float(pnl.sum()) <= 0:
        reasons.append("post_restart_official_pnl_not_positive")
    if len(post) and not bool(complete_realism.all()):
        reasons.append("missing_execution_realism_fields")
    if len(post) and not bool(quote_age_ok.all()):
        reasons.append("quote_age_missing_or_above_limit")
    if len(post) and not bool(top_visible_ok.all()):
        reasons.append("top_visible_qty_missing_or_below_contracts")
    if len(post) and not bool(entry_match.all()):
        reasons.append("entry_not_reconciled_to_side_ask")
    if duplicate_event_rows > 0:
        reasons.append("duplicate_event_rows")
    if len(mismatches) and int(mismatches.sum()) > 0:
        reasons.append("proxy_official_settlement_mismatch")

    if not restart_executed:
        status = "PENDING_CONTROLLED_RESTART"
    elif len(post) == 0:
        status = "NO_POST_RESTART_ROWS"
    elif reasons:
        status = "FAIL_POST_RESTART_COLLECTION_GATE"
    else:
        status = "PASS_POST_RESTART_COLLECTION_GATE_NOT_DEPLOYMENT"

    return {
        "family": spec["family"],
        "candidate": spec["candidate"],
        "ledger": ledger,
        "restart_source": restart_source,
        "restart_executed": restart_executed,
        "restart_utc": restart_utc,
        "paper_rows_total": int(len(work)),
        "post_restart_paper_rows": int(len(post)),
        "post_restart_official_rows": int(len(official)),
        "min_post_restart_official_rows": min_official_rows,
        "pending_official_rows": int(len(pending)),
        "official_pnl": round(float(pnl.sum()) if len(pnl) else 0.0, 4),
        "official_max_dd": round(max_drawdown(pnl.reset_index(drop=True)), 4),
        "official_win_rate": round(float(official_win.mean()) if len(official_win) else 0.0, 4),
        "proxy_pnl": round(float(proxy_pnl.sum()) if len(proxy_pnl) else 0.0, 4),
        "official_minus_proxy_pnl": round(float(pnl.sum() - proxy_pnl.sum()) if len(official) else 0.0, 4),
        "proxy_official_mismatches": int(mismatches.sum()) if len(mismatches) else 0,
        "realism_complete_rows": int(complete_realism.sum()) if len(complete_realism) else 0,
        "min_required_realism_presence_rate": round(min(required_presence.values()) if required_presence else 0.0, 4),
        "quote_age_ok_rate": round(rate(quote_age_ok), 4),
        "quote_age_p95_ms": round(float(quote_age.quantile(0.95)) if quote_age.notna().any() else 0.0, 4),
        "top_visible_qty_ok_rate": round(rate(top_visible_ok), 4),
        "entry_matches_side_ask_rate": round(rate(entry_match), 4),
        "event_count": event_count,
        "duplicate_event_rows": duplicate_event_rows,
        "gate_status": status,
        "promotion_collection_ready": status == "PASS_POST_RESTART_COLLECTION_GATE_NOT_DEPLOYMENT",
        "failure_reasons": ";".join(sorted(set(reasons))),
    }


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
    trades = read_csv(args.shadow_official_dir / "shadow_official_trades.csv")
    rows = [
        gate_ledger(
            trades,
            spec,
            restart_executed=restart_executed,
            restart_utc=restart_utc,
            restart_source=restart_source,
            args=args,
        )
        for spec in LEDGER_SPECS
    ]
    summary = pd.DataFrame(rows)
    summary.to_csv(args.out_dir / "post_restart_collection_gate_summary.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "restart_dir": str(restart_dir) if restart_dir else "",
        "restart_executed": restart_executed,
        "restart_utc": restart_utc,
        "restart_source": restart_source,
        "shadow_official_dir": str(args.shadow_official_dir),
        "max_quote_age_ms": args.max_quote_age_ms,
        "note": "This gate checks future post-restart paper rows only. Passing it is not deployment approval; readiness, replay, basis, and GPT/local promotion gates still apply.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "family",
        "candidate",
        "gate_status",
        "promotion_collection_ready",
        "restart_executed",
        "post_restart_official_rows",
        "min_post_restart_official_rows",
        "official_pnl",
        "proxy_official_mismatches",
        "realism_complete_rows",
        "quote_age_ok_rate",
        "top_visible_qty_ok_rate",
        "failure_reasons",
    ]
    report = [
        "# BTC Post-Restart Collection Gate",
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
        "- This gate only counts rows written after a controlled paper-shadow restart.",
        "- Rows must be official-settled and carry execution-realism fields before they can count as promotion evidence.",
        "- Passing this gate would still not deploy a strategy; it only clears the post-restart collection layer.",
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
