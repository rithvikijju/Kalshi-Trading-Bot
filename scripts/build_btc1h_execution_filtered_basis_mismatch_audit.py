#!/usr/bin/env python3
"""Audit BTC1H basis mismatches that survive strict execution filtering.

The execution-filter impact audit asks what rows remain after enforcing
decision-time quote/fee/size checks on the old paused BTC1H snapshot. This
script narrows in on any official/proxy settlement mismatch still present in
that strict subset. It is diagnostic only: a basis guard cannot be fit or
promoted from these old, tiny rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / "btc1h_execution_filtered_basis_mismatch_latest_codex"
DEFAULT_ROW_FLAGS = (
    BACKTEST_ROOT
    / "btc1h_snapshot_execution_realism_latest_codex"
    / "btc1h_snapshot_execution_realism_rows.csv"
)
DEFAULT_OFFICIAL_TRADES = (
    BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
)
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"
STRICT_CHECKS = [
    "required_fields_present",
    "entry_matches_side_ask",
    "actual_entry_matches_entry",
    "fee_present_nonnegative",
    "top_visible_qty_ge_contracts",
    "quote_age_le_limit",
    "spread_le_limit",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--row-flags", type=Path, default=DEFAULT_ROW_FLAGS)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--min-clean-official-rows", type=int, default=50)
    parser.add_argument("--watch-boundary-usd", type=float, default=50.0)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
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


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("event_ticker", "")).strip(),
        str(row.get("market_ticker", "")).strip(),
        str(row.get("side", "")).strip().lower(),
    )


def official_by_key(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    out: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        if row.get("ledger") != LEDGER:
            continue
        out[key(row)] = row
    return out


def row_passes_strict_execution(row: dict[str, str]) -> bool:
    return all(to_bool(row.get(check, "")) for check in STRICT_CHECKS)


def side_sign(side: str) -> int:
    return 1 if str(side).strip().lower() == "yes" else -1


def pctile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def enrich_official_row(
    *,
    flag_row: dict[str, str],
    official: dict[str, str],
    watch_boundary_usd: float,
) -> dict[str, Any]:
    side = str(official.get("side", flag_row.get("side", ""))).strip().lower()
    sign = side_sign(side)
    proxy_close_minus_strike = to_float(official.get("proxy_close_minus_strike"))
    official_expiration_minus_strike = to_float(official.get("official_expiration_minus_strike"))
    official_minus_proxy_spot = to_float(official.get("official_minus_proxy_spot"))
    proxy_side_margin = sign * proxy_close_minus_strike
    official_side_margin = sign * official_expiration_minus_strike
    adverse_basis = -sign * official_minus_proxy_spot
    basis_consumed_proxy_margin: float | str = ""
    if proxy_side_margin > 0:
        basis_consumed_proxy_margin = adverse_basis / proxy_side_margin
    proxy_boundary_abs = abs(proxy_close_minus_strike)
    official_boundary_abs = abs(official_expiration_minus_strike)
    return {
        "variant": VARIANT,
        "event_ticker": official.get("event_ticker", flag_row.get("event_ticker", "")),
        "market_ticker": official.get("market_ticker", flag_row.get("market_ticker", "")),
        "side": side,
        "created_at": official.get("created_at", flag_row.get("created_at", "")),
        "entry_price": official.get("entry_price", flag_row.get("entry_price", "")),
        "contracts": official.get("contracts", flag_row.get("contracts", "")),
        "fee": official.get("fee", flag_row.get("actual_fee_paid", "")),
        "model_p_yes": official.get("model_p_yes", ""),
        "net_edge_cents": official.get("net_edge_cents", ""),
        "entry_btc_spot": official.get("entry_btc_spot", ""),
        "entry_spot_minus_strike": official.get("entry_spot_minus_strike", ""),
        "entry_spot_distance_bps": official.get("entry_spot_distance_bps", ""),
        "quote_age_ms": flag_row.get("quote_age_ms", official.get("quote_age_ms", "")),
        "top_visible_qty": flag_row.get("top_visible_qty", official.get("top_visible_qty", "")),
        "spread_cents": flag_row.get("spread_cents", official.get("spread_cents", "")),
        "official_result": official.get("official_result", ""),
        "proxy_result": official.get("proxy_result", ""),
        "official_pnl": round(to_float(official.get("official_pnl")), 10),
        "proxy_pnl": round(to_float(official.get("proxy_pnl")), 10),
        "official_minus_proxy_pnl": round(
            to_float(official.get("official_pnl")) - to_float(official.get("proxy_pnl")),
            10,
        ),
        "official_minus_proxy_spot": round(official_minus_proxy_spot, 10),
        "official_minus_proxy_bps": official.get("official_minus_proxy_bps", ""),
        "proxy_close_spot": official.get("proxy_close_spot", ""),
        "expiration_value": official.get("expiration_value", ""),
        "floor_strike": official.get("floor_strike", ""),
        "ticker_strike": official.get("ticker_strike", ""),
        "proxy_close_minus_strike": round(proxy_close_minus_strike, 10),
        "official_expiration_minus_strike": round(official_expiration_minus_strike, 10),
        "proxy_side_margin_usd": round(proxy_side_margin, 10),
        "official_side_margin_usd": round(official_side_margin, 10),
        "adverse_basis_usd": round(adverse_basis, 10),
        "basis_consumed_proxy_margin": round(basis_consumed_proxy_margin, 10)
        if isinstance(basis_consumed_proxy_margin, float)
        else "",
        "proxy_boundary_abs_usd": round(proxy_boundary_abs, 10),
        "official_boundary_abs_usd": round(official_boundary_abs, 10),
        "near_proxy_boundary": proxy_boundary_abs <= watch_boundary_usd,
        "near_official_boundary": official_boundary_abs <= watch_boundary_usd,
        "near_either_boundary": proxy_boundary_abs <= watch_boundary_usd
        or official_boundary_abs <= watch_boundary_usd,
        "proxy_side_win": proxy_side_margin > 0,
        "official_side_win": official_side_margin > 0,
        "official_proxy_result_mismatch": to_bool(official.get("official_proxy_result_mismatch", "")),
        "strict_execution_pass": row_passes_strict_execution(flag_row),
        "pre_clean_clock_row": flag_row.get("pre_clean_clock_row", ""),
        "promotion_usable": False,
    }


def build_audit(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    row_flags = read_csv(args.row_flags)
    official_lookup = official_by_key(read_csv(args.official_trades))
    strict_flags = [row for row in row_flags if row_passes_strict_execution(row)]
    removed_flags = [row for row in row_flags if not row_passes_strict_execution(row)]

    all_pairs = [(row, official_lookup[key(row)]) for row in row_flags if key(row) in official_lookup]
    strict_pairs = [(row, official_lookup[key(row)]) for row in strict_flags if key(row) in official_lookup]
    removed_keys = {key(row) for row in removed_flags}

    all_mismatch_keys = {
        key(official)
        for _row, official in all_pairs
        if to_bool(official.get("official_proxy_result_mismatch", ""))
    }
    strict_mismatch_pairs = [
        (row, official)
        for row, official in strict_pairs
        if to_bool(official.get("official_proxy_result_mismatch", ""))
    ]
    removed_mismatch_markets = sorted(
        official_lookup[mismatch_key].get("market_ticker", "")
        for mismatch_key in all_mismatch_keys
        if mismatch_key in removed_keys and mismatch_key in official_lookup
    )
    mismatch_rows = [
        enrich_official_row(flag_row=row, official=official, watch_boundary_usd=args.watch_boundary_usd)
        for row, official in strict_mismatch_pairs
    ]
    strict_official_rows = [
        enrich_official_row(flag_row=row, official=official, watch_boundary_usd=args.watch_boundary_usd)
        for row, official in strict_pairs
    ]

    strict_official_pnl = sum(to_float(row.get("official_pnl")) for row in strict_official_rows)
    strict_proxy_pnl = sum(to_float(row.get("proxy_pnl")) for row in strict_official_rows)
    strict_mismatch_count = len(mismatch_rows)
    mismatch_removed_by_execution_filter = bool(all_mismatch_keys) and strict_mismatch_count == 0
    basis_values = [abs(to_float(row.get("official_minus_proxy_spot"))) for row in mismatch_rows]
    adverse_values = [to_float(row.get("adverse_basis_usd")) for row in mismatch_rows]
    proxy_margins = [abs(to_float(row.get("proxy_side_margin_usd"))) for row in mismatch_rows]

    blockers: list[str] = []
    if strict_mismatch_count == 1:
        blockers.append("single_mismatch_after_execution_filter")
    elif strict_mismatch_count > 1:
        blockers.append("basis_mismatches_after_execution_filter")
    if len(strict_official_rows) < args.min_clean_official_rows:
        blockers.append("too_few_strict_official_rows")
    blockers.extend(
        [
            "old_snapshot_not_clean_clock_promotion_evidence",
            "guard_not_fit_from_current_rows",
            "replay_parity_still_required",
        ]
    )
    audit_status = (
        "DIAGNOSTIC_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH_REMAINS"
        if strict_mismatch_count
        else "DIAGNOSTIC_NO_STRICT_EXECUTION_FILTERED_BASIS_MISMATCH"
    )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": LEDGER,
        "variant": VARIANT,
        "row_flags": str(args.row_flags),
        "official_trades": str(args.official_trades),
        "audit_status": audit_status,
        "strict_rows": len(strict_flags),
        "strict_official_rows": len(strict_official_rows),
        "strict_official_pnl": round(strict_official_pnl, 10),
        "strict_proxy_pnl": round(strict_proxy_pnl, 10),
        "strict_official_minus_proxy_pnl": round(strict_official_pnl - strict_proxy_pnl, 10),
        "strict_official_proxy_mismatches": strict_mismatch_count,
        "strict_official_proxy_mismatch_rate": round(strict_mismatch_count / len(strict_official_rows), 10)
        if strict_official_rows
        else 0.0,
        "all_snapshot_official_rows": len(all_pairs),
        "all_snapshot_official_proxy_mismatches": len(all_mismatch_keys),
        "mismatch_removed_by_execution_filter": mismatch_removed_by_execution_filter,
        "removed_mismatch_markets": ";".join(removed_mismatch_markets),
        "strict_mismatch_markets": ";".join(str(row.get("market_ticker", "")) for row in mismatch_rows),
        "strict_mismatch_sides": ";".join(str(row.get("side", "")) for row in mismatch_rows),
        "strict_mismatch_official_minus_proxy_spot": ";".join(
            str(row.get("official_minus_proxy_spot", "")) for row in mismatch_rows
        ),
        "strict_mismatch_proxy_close_minus_strike": ";".join(
            str(row.get("proxy_close_minus_strike", "")) for row in mismatch_rows
        ),
        "strict_mismatch_official_expiration_minus_strike": ";".join(
            str(row.get("official_expiration_minus_strike", "")) for row in mismatch_rows
        ),
        "near_boundary_mismatch_rows": sum(1 for row in mismatch_rows if row.get("near_either_boundary")),
        "watch_boundary_usd": args.watch_boundary_usd,
        "max_abs_mismatch_basis_usd": round(max(basis_values), 10) if basis_values else 0.0,
        "p95_abs_mismatch_basis_usd": round(pctile(basis_values, 0.95), 10),
        "max_adverse_mismatch_basis_usd": round(max(adverse_values), 10) if adverse_values else 0.0,
        "min_proxy_side_abs_margin_usd": round(min(proxy_margins), 10) if proxy_margins else 0.0,
        "basis_guard_deployable_now": False,
        "near_deployable_candidate": False,
        "blockers": ";".join(dict.fromkeys(blockers)),
        "next_action": (
            "Track the mismatch as a prospective basis watch condition only; require clean-clock official rows, "
            "pre-registered guard criteria, and row-for-row replay parity before any promotion discussion."
        ),
    }
    return mismatch_rows, strict_official_rows, summary


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    mismatch_rows, strict_official_rows, summary = build_audit(args)
    write_csv(args.out_dir / "btc1h_execution_filtered_basis_mismatch_summary.csv", [summary])
    write_csv(args.out_dir / "btc1h_execution_filtered_basis_mismatch_rows.csv", mismatch_rows)
    write_csv(args.out_dir / "btc1h_execution_filtered_basis_strict_rows.csv", strict_official_rows)
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC1H Execution-Filtered Basis Mismatch Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Audit status: `{summary['audit_status']}`",
        f"Strict official rows: `{summary['strict_official_rows']}`",
        f"Strict official/proxy mismatches: `{summary['strict_official_proxy_mismatches']}`",
        f"Mismatch removed by execution filter: `{summary['mismatch_removed_by_execution_filter']}`",
        f"Basis guard deployable now: `{summary['basis_guard_deployable_now']}`",
        f"Blockers: `{summary['blockers']}`",
        "",
        "## Mismatch Rows",
        "",
        markdown_table(
            mismatch_rows,
            [
                "event_ticker",
                "market_ticker",
                "side",
                "entry_price",
                "official_result",
                "proxy_result",
                "official_pnl",
                "proxy_pnl",
                "official_minus_proxy_spot",
                "proxy_close_minus_strike",
                "official_expiration_minus_strike",
                "proxy_side_margin_usd",
                "official_side_margin_usd",
                "near_either_boundary",
                "quote_age_ms",
                "top_visible_qty",
            ],
        ),
        "",
        "## Summary",
        "",
        markdown_table([summary], list(summary.keys())),
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
