#!/usr/bin/env python3
"""Quantify BTC1H broad no-chase extra-row damage versus entry70.

This is a fixed diagnostic, not a threshold search.  It compares the broader
`high_conf_80_no_chase` policy against the active
`high_conf_80_entry70_no_chase` policy on artifacts where both variants are
present.  The key question is whether the rows admitted by removing the 70c
entry cap are helpful or whether they explain the live-websocket cadence
failure.

All PnL is recomputed with a fixed adverse entry stress and taker fee.  Outputs
are research-only and cannot promote broad no-chase without official forward
rows, execution realism, and live replay/ledger agreement.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_no_chase_extra_row_audit_latest_codex"
NO_CHASE = "high_conf_80_no_chase"
ENTRY70 = "high_conf_80_entry70_no_chase"
LIVE_FULLSCAN_ENTRY70 = (
    BACKTEST_ROOT
    / "btc1h_core_ws_counterfactual_snapshot_fullscan_prefilter_highconf_latest_codex"
    / "btc1h_core_ws_counterfactual_trades.csv"
)


@dataclass(frozen=True)
class SourceSpec:
    label: str
    path: Path | None
    label_func: Callable[[dict[str, str]], str]
    no_chase_path: Path | None = None
    entry70_path: Path | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-chase-variant", default=NO_CHASE)
    parser.add_argument("--entry70-variant", default=ENTRY70)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--live-fullscan-trades", type=Path, default=LIVE_FULLSCAN_ENTRY70)
    parser.add_argument(
        "--live-fullscan-no-chase-trades",
        type=Path,
        default=BACKTEST_ROOT
        / "btc1h_core_ws_counterfactual_snapshot_fullscan_no_chase_latest_codex"
        / "btc1h_core_ws_counterfactual_trades.csv",
    )
    parser.add_argument(
        "--live-fullscan-entry70-trades",
        type=Path,
        default=LIVE_FULLSCAN_ENTRY70,
    )
    return parser.parse_args()


def latest_file(pattern: str, filename: str) -> Path | None:
    matches = [p / filename for p in BACKTEST_ROOT.glob(pattern) if (p / filename).exists()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return to_float(value) > 0.5


def slug(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = text.strip("_")
    return text or "unknown"


def direct_label(row: dict[str, str]) -> str:
    return f"direct_{slug(row.get('split', 'unknown'))}"


def robustness_label(row: dict[str, str]) -> str:
    source = str(row.get("source", "")).strip().lower()
    if source == "websocket":
        cadence = str(row.get("cadence_sec", "")).strip()
        cadence = str(int(float(cadence))) if cadence else "unknown"
        return f"live_ws_stride{cadence}s"
    return f"robustness_{slug(row.get('dataset') or row.get('split') or source)}"


def live_fullscan_label(_row: dict[str, str]) -> str:
    return "live_ws_fullscan_snapshot"


def default_sources(args: argparse.Namespace) -> list[SourceSpec]:
    return [
        SourceSpec(
            "direct_predexon_aggregate",
            args.direct_trades or latest_file("btc1h_highconf_direct_aggregate_feb09_may06_*", "trades.csv"),
            direct_label,
        ),
        SourceSpec(
            "robustness_trade_logs",
            args.robustness_trades or latest_file("btc1h_highconf_robustness_*", "all_input_trades.csv"),
            robustness_label,
        ),
        SourceSpec(
            "live_fullscan_snapshot",
            args.live_fullscan_trades,
            live_fullscan_label,
            no_chase_path=args.live_fullscan_no_chase_trades,
            entry70_path=args.live_fullscan_entry70_trades,
        ),
    ]


def entry_id(row: dict[str, str]) -> str:
    for field in ("entry_received_at_ns", "entry_time", "quote_ts_end"):
        value = str(row.get(field, "")).strip()
        if value:
            return value
    return ""


def price_key(row: dict[str, str]) -> str:
    raw = str(row.get("entry_price", "")).strip()
    if raw == "":
        return ""
    return f"{to_float(raw):.6f}"


def exact_key(label: str, row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (
        label,
        str(row.get("event_ticker", "")).strip().upper(),
        str(row.get("market_ticker", "")).strip().upper(),
        str(row.get("side", "")).strip().lower(),
        entry_id(row),
        price_key(row),
    )


def market_side_key(label: str, row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        label,
        str(row.get("event_ticker", "")).strip().upper(),
        str(row.get("market_ticker", "")).strip().upper(),
        str(row.get("side", "")).strip().lower(),
    )


def row_win(row: dict[str, str]) -> bool:
    if str(row.get("win_bool", "")).strip() != "":
        return to_bool(row.get("win_bool"))
    if str(row.get("win", "")).strip() != "":
        return to_bool(row.get("win"))
    if str(row.get("payout", "")).strip() != "":
        return to_float(row.get("payout")) > 0.5
    return to_float(row.get("pnl")) > 0.0


def stressed_pnl(row: dict[str, str], stress_cents: float) -> float:
    entry = min(0.99, max(0.0, to_float(row.get("entry_price")) + stress_cents / 100.0))
    fee = kalshi_fee_dollars(entry, contracts=1, liquidity="taker")
    premium = entry + fee
    return (1.0 - premium) if row_win(row) else -premium


def max_drawdown(values: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def group_rows(spec: SourceSpec, args: argparse.Namespace) -> tuple[dict[str, dict[str, list[dict[str, str]]]], list[str]]:
    grouped: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: {"no_chase": [], "entry70": []})
    missing: list[str] = []
    if spec.no_chase_path is not None or spec.entry70_path is not None:
        if spec.no_chase_path is None or not spec.no_chase_path.exists():
            missing.append(f"{spec.label}:missing_no_chase_path:{spec.no_chase_path or ''}")
        else:
            no_rows = read_csv(spec.no_chase_path)
            if args.no_chase_variant not in {str(row.get("variant", "")).strip() for row in no_rows}:
                missing.append(f"{spec.label}:variant_not_present:{args.no_chase_variant}")
            for row in no_rows:
                if str(row.get("variant", "")).strip() == args.no_chase_variant:
                    grouped[spec.label_func(row)]["no_chase"].append(row)
        if spec.entry70_path is None or not spec.entry70_path.exists():
            missing.append(f"{spec.label}:missing_entry70_path:{spec.entry70_path or ''}")
        else:
            entry70_rows = read_csv(spec.entry70_path)
            if args.entry70_variant not in {str(row.get("variant", "")).strip() for row in entry70_rows}:
                missing.append(f"{spec.label}:variant_not_present:{args.entry70_variant}")
            for row in entry70_rows:
                if str(row.get("variant", "")).strip() == args.entry70_variant:
                    grouped[spec.label_func(row)]["entry70"].append(row)
        return grouped, missing

    rows = read_csv(spec.path)
    if spec.path is None or not spec.path.exists():
        missing.append(str(spec.path or ""))
        return grouped, missing
    variants = {str(row.get("variant", "")).strip() for row in rows}
    absent = [variant for variant in (args.no_chase_variant, args.entry70_variant) if variant not in variants]
    if absent:
        missing.append(f"{spec.label}:variant_not_present:{','.join(absent)}")
        return grouped, missing
    for row in rows:
        variant = str(row.get("variant", "")).strip()
        label = spec.label_func(row)
        if variant == args.no_chase_variant:
            grouped[label]["no_chase"].append(row)
        elif variant == args.entry70_variant:
            grouped[label]["entry70"].append(row)
    return grouped, missing


def exact_buckets(label: str, rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str, str, str], list[dict[str, str]]]:
    buckets: dict[tuple[str, str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = exact_key(label, row)
        if all(key[1:]):
            buckets[key].append(row)
    return buckets


def market_keys(label: str, rows: list[dict[str, str]]) -> set[tuple[str, str, str, str]]:
    return {market_side_key(label, row) for row in rows if all(market_side_key(label, row)[1:])}


def summarize_rows(rows: list[dict[str, str]], stress_cents: float) -> dict[str, Any]:
    pnls = [stressed_pnl(row, stress_cents) for row in rows]
    wins = [row_win(row) for row in rows]
    entries = [to_float(row.get("entry_price"), float("nan")) for row in rows]
    clean_entries = [entry for entry in entries if math.isfinite(entry)]
    return {
        "rows": len(rows),
        "pnl": round(sum(pnls), 6),
        "win_rate": round(sum(1 for win in wins if win) / len(wins), 6) if wins else 0.0,
        "max_dd": round(max_drawdown(pnls), 6),
        "avg_entry": round(sum(clean_entries) / len(clean_entries), 6) if clean_entries else "",
    }


def compare_label(
    evidence_label: str,
    spec: SourceSpec,
    no_chase_rows: list[dict[str, str]],
    entry70_rows: list[dict[str, str]],
    stress_cents: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    no_exact = exact_buckets(evidence_label, no_chase_rows)
    entry70_exact = exact_buckets(evidence_label, entry70_rows)
    no_keys = set(no_exact)
    entry70_keys = set(entry70_exact)
    shared_keys = sorted(no_keys & entry70_keys)

    details: list[dict[str, Any]] = []
    shared_exact_rows = 0
    extra_no_chase: list[dict[str, str]] = []
    entry70_only: list[dict[str, str]] = []

    for key in sorted(no_keys | entry70_keys):
        no_bucket = no_exact.get(key, [])
        entry70_bucket = entry70_exact.get(key, [])
        shared = min(len(no_bucket), len(entry70_bucket))
        shared_exact_rows += shared
        for row in no_bucket[shared:]:
            extra_no_chase.append(row)
            details.append(detail_row(evidence_label, "extra_no_chase_exact", row, stress_cents, no_chase=True))
        for row in entry70_bucket[shared:]:
            entry70_only.append(row)
            details.append(detail_row(evidence_label, "entry70_only_exact", row, stress_cents, no_chase=False))

    entry70_market = market_keys(evidence_label, entry70_rows)
    no_chase_market = market_keys(evidence_label, no_chase_rows)
    extra_no_chase_market = [
        row for row in no_chase_rows if market_side_key(evidence_label, row) not in entry70_market
    ]
    entry70_market_only = [row for row in entry70_rows if market_side_key(evidence_label, row) not in no_chase_market]
    extra_gt70 = [row for row in extra_no_chase if to_float(row.get("entry_price")) > 0.70]
    extra_le70 = [row for row in extra_no_chase if to_float(row.get("entry_price")) <= 0.70]
    extra_market_gt70 = [row for row in extra_no_chase_market if to_float(row.get("entry_price")) > 0.70]

    no_summary = summarize_rows(no_chase_rows, stress_cents)
    entry70_summary = summarize_rows(entry70_rows, stress_cents)
    extra_summary = summarize_rows(extra_no_chase, stress_cents)
    extra_gt70_summary = summarize_rows(extra_gt70, stress_cents)
    extra_market_summary = summarize_rows(extra_no_chase_market, stress_cents)
    entry70_only_summary = summarize_rows(entry70_only, stress_cents)

    if not no_chase_rows and not entry70_rows:
        status = "NO_ROWS"
    elif extra_gt70 and extra_gt70_summary["pnl"] < 0:
        status = "EXTRA_GT70_ROWS_NEGATIVE"
    elif extra_no_chase and extra_summary["pnl"] < 0:
        status = "EXTRA_ROWS_NEGATIVE"
    elif extra_no_chase:
        status = "EXTRA_ROWS_POSITIVE"
    else:
        status = "EXACT_ROW_SET_MATCH"

    summary = {
        "evidence_label": evidence_label,
        "source_label": spec.label,
        "status": status,
        "stress_cents": stress_cents,
        "no_chase_rows": no_summary["rows"],
        "entry70_rows": entry70_summary["rows"],
        "shared_exact_rows": shared_exact_rows,
        "extra_no_chase_exact_rows": extra_summary["rows"],
        "entry70_only_exact_rows": entry70_only_summary["rows"],
        "extra_no_chase_market_side_rows": extra_market_summary["rows"],
        "entry70_market_side_only_rows": len(entry70_market_only),
        "no_chase_pnl": no_summary["pnl"],
        "entry70_pnl": entry70_summary["pnl"],
        "pnl_diff_no_chase_minus_entry70": round(float(no_summary["pnl"]) - float(entry70_summary["pnl"]), 6),
        "extra_no_chase_pnl": extra_summary["pnl"],
        "extra_no_chase_win_rate": extra_summary["win_rate"],
        "extra_no_chase_avg_entry": extra_summary["avg_entry"],
        "extra_gt70_rows": extra_gt70_summary["rows"],
        "extra_gt70_pnl": extra_gt70_summary["pnl"],
        "extra_gt70_win_rate": extra_gt70_summary["win_rate"],
        "extra_le70_rows": len(extra_le70),
        "extra_le70_pnl": round(sum(stressed_pnl(row, stress_cents) for row in extra_le70), 6),
        "extra_market_side_pnl": extra_market_summary["pnl"],
        "extra_market_side_gt70_rows": len(extra_market_gt70),
        "entry70_only_pnl": entry70_only_summary["pnl"],
    }
    return details, summary


def detail_row(
    evidence_label: str,
    status: str,
    row: dict[str, str],
    stress_cents: float,
    *,
    no_chase: bool,
) -> dict[str, Any]:
    key = exact_key(evidence_label, row)
    return {
        "evidence_label": evidence_label,
        "match_status": status,
        "event_ticker": key[1],
        "market_ticker": key[2],
        "side": key[3],
        "entry_id": key[4],
        "entry_price": key[5],
        "is_gt70_entry": to_float(row.get("entry_price")) > 0.70,
        "stressed_pnl": round(stressed_pnl(row, stress_cents), 6),
        "win": row_win(row),
        "no_chase_row": no_chase,
        "side_probability": row.get("side_probability", row.get("model_p_side", "")),
        "net_edge_cents": row.get("net_edge_cents", ""),
        "visible_qty": row.get("visible_qty", ""),
        "btc_ret_10m_usd": row.get("btc_ret_10m_usd", ""),
        "source_dir": row.get("source_dir", ""),
    }


def total(rows: list[dict[str, Any]], field: str) -> float:
    return sum(to_float(row.get(field, "")) for row in rows)


def build_overall_summary(
    args: argparse.Namespace,
    source_summaries: list[dict[str, Any]],
    missing_sources: list[str],
) -> dict[str, Any]:
    compared = [row for row in source_summaries if row["no_chase_rows"] or row["entry70_rows"]]
    live_ws = [row for row in compared if str(row.get("evidence_label", "")).startswith("live_ws_stride")]
    live_negative = [row for row in live_ws if to_float(row.get("extra_no_chase_pnl")) < 0.0]
    stride1 = next((row for row in live_ws if row.get("evidence_label") == "live_ws_stride1s"), {})
    fullscan = next((row for row in compared if row.get("evidence_label") == "live_ws_fullscan_snapshot"), {})
    total_extra_pnl = round(total(compared, "extra_no_chase_pnl"), 6)
    total_extra_gt70_pnl = round(total(compared, "extra_gt70_pnl"), 6)

    if not compared:
        status = "MISSING_OR_EMPTY_INPUTS"
    elif live_negative:
        status = "NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING"
    elif total_extra_gt70_pnl < 0:
        status = "NO_CHASE_EXTRA_GT70_ROWS_NEGATIVE"
    elif total_extra_pnl <= 0:
        status = "NO_CHASE_EXTRA_ROWS_NONPOSITIVE"
    else:
        status = "NO_CHASE_EXTRA_ROWS_DIAGNOSTIC_POSITIVE"

    recommendation = (
        "Do not promote or restart broad no-chase. The extra rows admitted by removing the 70c cap remain "
        "diagnostic only; the live-WS cadence damage must be solved on fresh causal replay and official rows."
    )
    if status == "NO_CHASE_EXTRA_ROWS_DIAGNOSTIC_POSITIVE":
        recommendation = (
            "Keep broad no-chase on the research watchlist only. Extra rows are positive in the compared artifacts, "
            "but the policy still lacks clean official forward rows and row-for-row live replay/ledger promotion evidence."
        )

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "no_chase_variant": args.no_chase_variant,
        "entry70_variant": args.entry70_variant,
        "stress_cents": args.stress_cents,
        "status": status,
        "deployable_now": False,
        "compared_evidence_labels": len(compared),
        "missing_source_count": len([src for src in missing_sources if src]),
        "missing_sources": ";".join(src for src in missing_sources if src),
        "total_no_chase_rows": int(total(compared, "no_chase_rows")),
        "total_entry70_rows": int(total(compared, "entry70_rows")),
        "total_extra_no_chase_exact_rows": int(total(compared, "extra_no_chase_exact_rows")),
        "total_extra_no_chase_market_side_rows": int(total(compared, "extra_no_chase_market_side_rows")),
        "total_extra_no_chase_pnl": total_extra_pnl,
        "total_extra_gt70_rows": int(total(compared, "extra_gt70_rows")),
        "total_extra_gt70_pnl": total_extra_gt70_pnl,
        "total_extra_le70_rows": int(total(compared, "extra_le70_rows")),
        "total_extra_le70_pnl": round(total(compared, "extra_le70_pnl"), 6),
        "live_ws_label_count": len(live_ws),
        "live_ws_negative_extra_label_count": len(live_negative),
        "live_ws_extra_no_chase_pnl": round(total(live_ws, "extra_no_chase_pnl"), 6),
        "live_ws_extra_gt70_pnl": round(total(live_ws, "extra_gt70_pnl"), 6),
        "live_ws_stride1_extra_no_chase_rows": stride1.get("extra_no_chase_exact_rows", ""),
        "live_ws_stride1_extra_no_chase_pnl": stride1.get("extra_no_chase_pnl", ""),
        "live_ws_stride1_extra_gt70_rows": stride1.get("extra_gt70_rows", ""),
        "live_ws_stride1_extra_gt70_pnl": stride1.get("extra_gt70_pnl", ""),
        "fullscan_status": fullscan.get("status", ""),
        "fullscan_no_chase_rows": fullscan.get("no_chase_rows", ""),
        "fullscan_entry70_rows": fullscan.get("entry70_rows", ""),
        "fullscan_extra_no_chase_rows": fullscan.get("extra_no_chase_exact_rows", ""),
        "fullscan_extra_no_chase_market_side_rows": fullscan.get("extra_no_chase_market_side_rows", ""),
        "fullscan_extra_no_chase_pnl": fullscan.get("extra_no_chase_pnl", ""),
        "fullscan_pnl_diff_no_chase_minus_entry70": fullscan.get("pnl_diff_no_chase_minus_entry70", ""),
        "recommended_next_action": recommendation,
        "note": (
            "All rows are diagnostic and may overlap across cadence artifacts. Negative live-WS extra-row labels "
            "are blockers for broad no-chase; they are not permission to tune a new cap on the same window."
        ),
    }


def build_audit(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    details: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    missing_sources: list[str] = []
    for spec in default_sources(args):
        grouped, missing = group_rows(spec, args)
        missing_sources.extend(missing)
        for evidence_label in sorted(grouped):
            label_details, label_summary = compare_label(
                evidence_label,
                spec,
                grouped[evidence_label]["no_chase"],
                grouped[evidence_label]["entry70"],
                args.stress_cents,
            )
            details.extend(label_details)
            source_summaries.append(label_summary)
    overall = build_overall_summary(args, source_summaries, missing_sources)
    return details, source_summaries, overall


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join(lines)


def build_report(source_summaries: list[dict[str, Any]], overall: dict[str, Any]) -> str:
    rows = sorted(source_summaries, key=lambda row: str(row.get("evidence_label", "")))
    cols = [
        "evidence_label",
        "status",
        "no_chase_rows",
        "entry70_rows",
        "extra_no_chase_exact_rows",
        "extra_gt70_rows",
        "extra_no_chase_pnl",
        "extra_gt70_pnl",
        "pnl_diff_no_chase_minus_entry70",
    ]
    return (
        "# BTC1H Broad No-Chase Extra-Row Audit\n\n"
        f"Created UTC: `{overall['created_at_utc']}`\n"
        f"Status: `{overall['status']}`\n"
        f"Stress: `+{overall['stress_cents']}c`\n"
        f"No-chase variant: `{overall['no_chase_variant']}`\n"
        f"Entry70 variant: `{overall['entry70_variant']}`\n"
        f"Deployable now: `{overall['deployable_now']}`\n\n"
        "## Evidence Labels\n\n"
        + markdown_table(rows, cols)
        + "\n\n## Recommendation\n\n"
        f"{overall['recommended_next_action']}\n\n"
        "## Summary\n\n```json\n"
        + json.dumps(overall, indent=2, sort_keys=True)
        + "\n```\n"
    )


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    details, source_summaries, overall = build_audit(args)
    write_csv(args.out_dir / "btc1h_no_chase_extra_row_details.csv", details)
    write_csv(args.out_dir / "btc1h_no_chase_extra_row_by_source.csv", source_summaries)
    write_csv(args.out_dir / "btc1h_no_chase_extra_row_summary.csv", [overall])
    (args.out_dir / "run_info.json").write_text(json.dumps(overall, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(source_summaries, overall), encoding="utf-8")
    print(json.dumps(overall, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
