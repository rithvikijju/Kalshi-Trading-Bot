#!/usr/bin/env python3
"""Audit whether BTC1H entry59 adds independent evidence versus entry70.

This is a fixed diagnostic, not a threshold search.  The entry59-70 policy is
definitionally an entry-band filter on top of the active entry70 no-chase
candidate, but one-trade-per-event replay semantics can still make row-level
behavior non-obvious.  This script compares available paired artifacts and
keeps two questions separate:

- Does entry59 add independent market/side events versus entry70?
- When exact rows differ, are they just deleted/replaced entry70 rows?

All output is research-only.  It cannot make entry59 deployable without clean
forward official rows, execution realism, and live replay/ledger agreement.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_entry59_floor_filter_audit_latest_codex"
BASE_VARIANT = "high_conf_80_entry70_no_chase"
CHALLENGER_VARIANT = "high_conf_80_entry59_70_no_chase"
LIVE_FULLSCAN_BASE = (
    BACKTEST_ROOT
    / "btc1h_core_ws_counterfactual_snapshot_fullscan_prefilter_highconf_latest_codex"
    / "btc1h_core_ws_counterfactual_trades.csv"
)
LIVE_FULLSCAN_CHALLENGER = (
    BACKTEST_ROOT
    / "btc1h_core_ws_counterfactual_snapshot_fullscan_entry59_latest_codex"
    / "btc1h_core_ws_counterfactual_trades.csv"
)


@dataclass(frozen=True)
class SourceSpec:
    label: str
    profile: str
    base_path: Path | None
    challenger_path: Path | None
    label_func: Callable[[dict[str, str]], str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--base-variant", default=BASE_VARIANT)
    parser.add_argument("--challenger-variant", default=CHALLENGER_VARIANT)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--base-live-replay", type=Path, default=LIVE_FULLSCAN_BASE)
    parser.add_argument("--challenger-live-replay", type=Path, default=LIVE_FULLSCAN_CHALLENGER)
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
        return float(value)
    except (TypeError, ValueError):
        return default


def slug(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = text.strip("_")
    return text or "unknown"


def price_key(row: dict[str, str]) -> str:
    raw = str(row.get("entry_price", "")).strip()
    if raw == "":
        return ""
    return f"{to_float(raw):.6f}"


def entry_id(row: dict[str, str]) -> str:
    for field in ("entry_received_at_ns", "entry_time", "quote_ts_end"):
        value = str(row.get(field, "")).strip()
        if value:
            return value
    return ""


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


def pnl_value(row: dict[str, str]) -> float:
    if str(row.get("pnl", "")).strip() != "":
        return to_float(row.get("pnl"))
    return to_float(row.get("official_pnl"))


def average(values: Iterable[float]) -> float | str:
    clean = [v for v in values if v == v]
    if not clean:
        return ""
    return round(sum(clean) / len(clean), 6)


def variant_rows(rows: list[dict[str, str]], variant: str) -> list[dict[str, str]]:
    return [row for row in rows if str(row.get("variant", "")).strip() == variant]


def direct_label(row: dict[str, str]) -> str:
    return f"direct_{slug(row.get('split', 'unknown'))}"


def robustness_label(row: dict[str, str]) -> str:
    source = str(row.get("source", "")).strip().lower()
    if source == "websocket":
        cadence = str(row.get("cadence_sec", "")).strip()
        cadence = str(int(float(cadence))) if cadence else "unknown"
        return f"robustness_live_ws_stride{cadence}s"
    dataset = row.get("dataset") or row.get("split") or source
    return f"robustness_{slug(dataset)}"


def live_snapshot_label(_row: dict[str, str]) -> str:
    return "live_ws_fullscan_snapshot"


def default_sources(args: argparse.Namespace) -> list[SourceSpec]:
    direct_path = args.direct_trades or latest_file("btc1h_highconf_direct_aggregate_feb09_may06_*", "trades.csv")
    robustness_path = args.robustness_trades or latest_file("btc1h_highconf_robustness_*", "all_input_trades.csv")
    return [
        SourceSpec("direct_predexon_aggregate", "single_artifact", direct_path, None, direct_label),
        SourceSpec("robustness_trade_logs", "single_artifact", robustness_path, None, robustness_label),
        SourceSpec(
            "live_ws_fullscan_snapshot",
            "paired_replay_artifacts",
            args.base_live_replay,
            args.challenger_live_replay,
            live_snapshot_label,
        ),
    ]


def group_rows(
    spec: SourceSpec,
    args: argparse.Namespace,
) -> tuple[dict[str, dict[str, list[dict[str, str]]]], list[str]]:
    grouped: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: {"base": [], "challenger": []})
    missing: list[str] = []

    if spec.profile == "single_artifact":
        rows = read_csv(spec.base_path)
        if spec.base_path is None or not spec.base_path.exists():
            missing.append(str(spec.base_path or ""))
            return grouped, missing
        variants = {str(row.get("variant", "")).strip() for row in rows}
        missing_variants = [
            variant for variant in (args.base_variant, args.challenger_variant) if variant not in variants
        ]
        if missing_variants:
            missing.append(f"{spec.label}:variant_not_present:{','.join(missing_variants)}")
            return grouped, missing
        for row in rows:
            label = spec.label_func(row)
            variant = str(row.get("variant", "")).strip()
            if variant == args.base_variant:
                grouped[label]["base"].append(row)
            elif variant == args.challenger_variant:
                grouped[label]["challenger"].append(row)
        return grouped, missing

    base_rows = read_csv(spec.base_path)
    challenger_rows = read_csv(spec.challenger_path)
    if spec.base_path is None or not spec.base_path.exists():
        missing.append(str(spec.base_path or ""))
    if spec.challenger_path is None or not spec.challenger_path.exists():
        missing.append(str(spec.challenger_path or ""))
    for row in variant_rows(base_rows, args.base_variant):
        grouped[spec.label_func(row)]["base"].append(row)
    for row in variant_rows(challenger_rows, args.challenger_variant):
        grouped[spec.label_func(row)]["challenger"].append(row)
    return grouped, missing


def bucket_by_exact(label: str, rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str, str, str], list[dict[str, str]]]:
    buckets: dict[tuple[str, str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = exact_key(label, row)
        if all(key[1:]):
            buckets[key].append(row)
    return buckets


def market_keys(label: str, rows: list[dict[str, str]]) -> set[tuple[str, str, str, str]]:
    return {market_side_key(label, row) for row in rows if all(market_side_key(label, row)[1:])}


def rows_for_market_only(
    label: str,
    rows: list[dict[str, str]],
    other_keys: set[tuple[str, str, str, str]],
) -> list[dict[str, str]]:
    return [row for row in rows if market_side_key(label, row) not in other_keys]


def compare_label(
    evidence_label: str,
    spec: SourceSpec,
    base_rows: list[dict[str, str]],
    challenger_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_by_exact = bucket_by_exact(evidence_label, base_rows)
    challenger_by_exact = bucket_by_exact(evidence_label, challenger_rows)
    base_exact_keys = set(base_by_exact)
    challenger_exact_keys = set(challenger_by_exact)
    all_exact_keys = sorted(base_exact_keys | challenger_exact_keys)

    base_ms = market_keys(evidence_label, base_rows)
    challenger_ms = market_keys(evidence_label, challenger_rows)
    base_market_only = rows_for_market_only(evidence_label, base_rows, challenger_ms)
    challenger_market_only = rows_for_market_only(evidence_label, challenger_rows, base_ms)

    details: list[dict[str, Any]] = []
    shared_exact_rows = 0
    base_exact_only_rows: list[dict[str, str]] = []
    challenger_exact_only_rows: list[dict[str, str]] = []

    for key in all_exact_keys:
        base_bucket = base_by_exact.get(key, [])
        challenger_bucket = challenger_by_exact.get(key, [])
        shared = min(len(base_bucket), len(challenger_bucket))
        shared_exact_rows += shared
        for idx in range(shared):
            base = base_bucket[idx]
            challenger = challenger_bucket[idx]
            details.append(
                {
                    "evidence_label": evidence_label,
                    "match_status": "shared_exact",
                    "event_ticker": key[1],
                    "market_ticker": key[2],
                    "side": key[3],
                    "entry_id": key[4],
                    "entry_price": key[5],
                    "base_pnl": round(pnl_value(base), 6),
                    "challenger_pnl": round(pnl_value(challenger), 6),
                    "base_entry_price": round(to_float(base.get("entry_price", "")), 6),
                    "challenger_entry_price": round(to_float(challenger.get("entry_price", "")), 6),
                }
            )
        for row in base_bucket[shared:]:
            base_exact_only_rows.append(row)
            details.append(detail_row(evidence_label, "base_exact_only", row, base=True))
        for row in challenger_bucket[shared:]:
            challenger_exact_only_rows.append(row)
            details.append(detail_row(evidence_label, "challenger_exact_only", row, base=False))

    base_low_entry_rows = [row for row in base_exact_only_rows if to_float(row.get("entry_price", ""), 99.0) < 0.59]
    base_pnl = sum(pnl_value(row) for row in base_rows)
    challenger_pnl = sum(pnl_value(row) for row in challenger_rows)
    base_exact_only_pnl = sum(pnl_value(row) for row in base_exact_only_rows)
    challenger_exact_only_pnl = sum(pnl_value(row) for row in challenger_exact_only_rows)
    base_market_only_pnl = sum(pnl_value(row) for row in base_market_only)
    challenger_market_only_pnl = sum(pnl_value(row) for row in challenger_market_only)
    exact_match = bool(base_rows or challenger_rows) and not base_exact_only_rows and not challenger_exact_only_rows
    challenger_exact_subset = bool(challenger_rows) and not challenger_exact_only_rows
    challenger_market_subset = bool(challenger_rows) and not challenger_market_only

    if not base_rows and not challenger_rows:
        status = "NO_ROWS"
    elif exact_match:
        status = "EXACT_ROW_SET_MATCH"
    elif challenger_market_only:
        status = "ENTRY59_HAS_INDEPENDENT_MARKET_SIDE_ROWS"
    elif challenger_exact_only_rows:
        status = "SAME_MARKET_SIDE_DIFFERENT_EXECUTION_ROWS"
    elif base_exact_only_rows:
        status = "ENTRY59_STRICT_SUBSET_FILTER"
    else:
        status = "NON_EXACT_OVERLAP"

    summary = {
        "evidence_label": evidence_label,
        "source_label": spec.label,
        "source_profile": spec.profile,
        "status": status,
        "base_rows": len(base_rows),
        "challenger_rows": len(challenger_rows),
        "shared_exact_rows": shared_exact_rows,
        "base_exact_only_rows": len(base_exact_only_rows),
        "challenger_exact_only_rows": len(challenger_exact_only_rows),
        "shared_market_side_keys": len(base_ms & challenger_ms),
        "base_market_side_only_rows": len(base_market_only),
        "challenger_market_side_only_rows": len(challenger_market_only),
        "exact_row_set_match": exact_match,
        "challenger_exact_subset_of_base": challenger_exact_subset,
        "challenger_market_side_subset_of_base": challenger_market_subset,
        "base_pnl": round(base_pnl, 6),
        "challenger_pnl": round(challenger_pnl, 6),
        "pnl_diff": round(challenger_pnl - base_pnl, 6),
        "base_exact_only_pnl": round(base_exact_only_pnl, 6),
        "challenger_exact_only_pnl": round(challenger_exact_only_pnl, 6),
        "base_market_side_only_pnl": round(base_market_only_pnl, 6),
        "challenger_market_side_only_pnl": round(challenger_market_only_pnl, 6),
        "deleted_low_entry_base_rows": len(base_low_entry_rows),
        "deleted_low_entry_base_pnl": round(sum(pnl_value(row) for row in base_low_entry_rows), 6),
        "base_exact_only_avg_entry": average(to_float(row.get("entry_price", ""), float("nan")) for row in base_exact_only_rows),
        "challenger_exact_only_avg_entry": average(
            to_float(row.get("entry_price", ""), float("nan")) for row in challenger_exact_only_rows
        ),
        "base_market_side_only_avg_entry": average(
            to_float(row.get("entry_price", ""), float("nan")) for row in base_market_only
        ),
        "challenger_market_side_only_avg_entry": average(
            to_float(row.get("entry_price", ""), float("nan")) for row in challenger_market_only
        ),
    }
    return details, summary


def detail_row(evidence_label: str, status: str, row: dict[str, str], base: bool) -> dict[str, Any]:
    key = exact_key(evidence_label, row)
    prefix = "base" if base else "challenger"
    return {
        "evidence_label": evidence_label,
        "match_status": status,
        "event_ticker": key[1],
        "market_ticker": key[2],
        "side": key[3],
        "entry_id": key[4],
        "entry_price": key[5],
        "base_pnl": round(pnl_value(row), 6) if base else "",
        "challenger_pnl": round(pnl_value(row), 6) if not base else "",
        "base_entry_price": round(to_float(row.get("entry_price", "")), 6) if base else "",
        "challenger_entry_price": round(to_float(row.get("entry_price", "")), 6) if not base else "",
        "source_dir": row.get("source_dir", ""),
    }


def build_overall_summary(
    args: argparse.Namespace,
    source_summaries: list[dict[str, Any]],
    missing_sources: list[str],
) -> dict[str, Any]:
    compared = [row for row in source_summaries if row["base_rows"] or row["challenger_rows"]]

    def total(field: str) -> float:
        return sum(to_float(row.get(field, "")) for row in compared)

    live = next((row for row in source_summaries if row["evidence_label"] == "live_ws_fullscan_snapshot"), {})
    challenger_market_only = int(total("challenger_market_side_only_rows"))
    challenger_exact_only = int(total("challenger_exact_only_rows"))
    base_exact_only = int(total("base_exact_only_rows"))

    if not compared:
        status = "MISSING_OR_EMPTY_INPUTS"
    elif challenger_market_only > 0:
        status = "ENTRY59_HAS_INDEPENDENT_MARKET_SIDE_ROWS"
    elif challenger_exact_only > 0:
        status = "SAME_MARKET_SIDE_DIFFERENT_EXECUTION_ROWS"
    elif base_exact_only > 0:
        status = "ENTRY59_STRICT_SUBSET_FILTER"
    else:
        status = "EXACT_ROW_SET_MATCH_ON_COMPARED_ARTIFACTS"

    recommendation = (
        "Do not start a separate entry59 shadow from current evidence. Treat entry59 as a diagnostic entry-floor "
        "filter on active entry70 unless a future clean replay/forward window creates independent market-side rows."
    )
    if challenger_market_only > 0:
        recommendation = (
            "Entry59 creates independent market-side rows in at least one artifact, but this is diagnostic only; "
            "it still needs faithful live replay and clean official forward rows before any shadow discussion."
        )

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_variant": args.base_variant,
        "challenger_variant": args.challenger_variant,
        "status": status,
        "deployable_now": False,
        "compared_evidence_labels": len(compared),
        "missing_source_count": len([src for src in missing_sources if src]),
        "missing_sources": ";".join(src for src in missing_sources if src),
        "total_base_rows": int(total("base_rows")),
        "total_challenger_rows": int(total("challenger_rows")),
        "total_shared_exact_rows": int(total("shared_exact_rows")),
        "total_base_exact_only_rows": int(total("base_exact_only_rows")),
        "total_challenger_exact_only_rows": challenger_exact_only,
        "total_base_market_side_only_rows": int(total("base_market_side_only_rows")),
        "total_challenger_market_side_only_rows": challenger_market_only,
        "total_base_exact_only_pnl": round(total("base_exact_only_pnl"), 6),
        "total_challenger_exact_only_pnl": round(total("challenger_exact_only_pnl"), 6),
        "total_base_market_side_only_pnl": round(total("base_market_side_only_pnl"), 6),
        "total_challenger_market_side_only_pnl": round(total("challenger_market_side_only_pnl"), 6),
        "total_deleted_low_entry_base_rows": int(total("deleted_low_entry_base_rows")),
        "total_deleted_low_entry_base_pnl": round(total("deleted_low_entry_base_pnl"), 6),
        "live_snapshot_status": live.get("status", ""),
        "live_snapshot_base_rows": live.get("base_rows", ""),
        "live_snapshot_challenger_rows": live.get("challenger_rows", ""),
        "live_snapshot_challenger_market_side_only_rows": live.get("challenger_market_side_only_rows", ""),
        "live_snapshot_exact_row_set_match": live.get("exact_row_set_match", ""),
        "recommended_next_action": recommendation,
        "note": (
            "Totals are diagnostic and may double-count overlapping historical artifacts. Independent market-side "
            "rows are the key evidence-de-duplication field; deployability remains false without official forward "
            "settlement, execution realism, and replay/ledger agreement."
        ),
    }


def build_floor_audit(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    details: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    missing_sources: list[str] = []
    for spec in default_sources(args):
        grouped, missing = group_rows(spec, args)
        missing_sources.extend(missing)
        for evidence_label in sorted(grouped):
            row_groups = grouped[evidence_label]
            label_details, label_summary = compare_label(
                evidence_label,
                spec,
                row_groups["base"],
                row_groups["challenger"],
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


def build_report(
    source_summaries: list[dict[str, Any]],
    overall: dict[str, Any],
) -> str:
    cols = [
        "evidence_label",
        "status",
        "base_rows",
        "challenger_rows",
        "base_exact_only_rows",
        "challenger_exact_only_rows",
        "challenger_market_side_only_rows",
        "pnl_diff",
        "deleted_low_entry_base_rows",
        "deleted_low_entry_base_pnl",
    ]
    rows = sorted(source_summaries, key=lambda row: str(row.get("evidence_label", "")))
    return (
        "# BTC1H Entry59 Floor Filter Audit\n\n"
        f"Created UTC: `{overall['created_at_utc']}`\n"
        f"Status: `{overall['status']}`\n"
        f"Base: `{overall['base_variant']}`\n"
        f"Challenger: `{overall['challenger_variant']}`\n"
        f"Independent challenger market-side rows: `{overall['total_challenger_market_side_only_rows']}`\n"
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
    details, source_summaries, overall = build_floor_audit(args)
    write_csv(args.out_dir / "btc1h_entry59_floor_filter_details.csv", details)
    write_csv(args.out_dir / "btc1h_entry59_floor_filter_by_source.csv", source_summaries)
    write_csv(args.out_dir / "btc1h_entry59_floor_filter_summary.csv", [overall])
    (args.out_dir / "run_info.json").write_text(json.dumps(overall, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(source_summaries, overall), encoding="utf-8")
    print(json.dumps(overall, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
