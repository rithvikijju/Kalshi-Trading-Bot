#!/usr/bin/env python3
"""Audit BTC1H official-vs-proxy settlement basis risk.

This is diagnostic only. It does not invent a guard or promote a strategy from
the current tiny forward sample. The goal is to make the observed BTC1H
official/proxy mismatch and near-strike exposure machine-readable for future
clean evidence-clock monitoring.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_official_basis_mismatch_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
VARIANT = "high_conf_80_entry70_no_chase"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--trades-csv",
        type=Path,
        default=BACKTEST_ROOT / "remote_btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv",
    )
    p.add_argument("--ledger", default=LEDGER)
    p.add_argument("--variant", default=VARIANT)
    p.add_argument("--min-promotion-official-rows", type=int, default=50)
    p.add_argument(
        "--watch-usd",
        type=float,
        default=50.0,
        help="Rows with proxy or official settlement within this many dollars of strike enter the watchlist.",
    )
    return p.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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


def day_key(value: str) -> str:
    return str(value or "")[:10]


def side_sign(side: str) -> int:
    return 1 if str(side).strip().lower() == "yes" else -1


def enrich_row(row: dict[str, str]) -> dict[str, Any]:
    side = str(row.get("side", "")).strip().lower()
    sign = side_sign(side)
    proxy_close_minus_strike = to_float(row.get("proxy_close_minus_strike"))
    official_exp_minus_strike = to_float(row.get("official_expiration_minus_strike"))
    basis = to_float(row.get("official_minus_proxy_spot"))
    proxy_side_margin = sign * proxy_close_minus_strike
    official_side_margin = sign * official_exp_minus_strike
    adverse_basis = -sign * basis
    official_pnl = to_float(row.get("official_pnl"))
    proxy_pnl = to_float(row.get("proxy_pnl"))
    basis_consumed_proxy_margin = ""
    if proxy_side_margin > 0:
        basis_consumed_proxy_margin = adverse_basis / proxy_side_margin
    return {
        **row,
        "variant": VARIANT,
        "trade_day_utc": day_key(row.get("created_at", "")),
        "side_sign": sign,
        "official_pnl": official_pnl,
        "proxy_pnl": proxy_pnl,
        "official_minus_proxy_pnl": official_pnl - proxy_pnl,
        "official_minus_proxy_spot": basis,
        "abs_official_minus_proxy_spot": abs(basis),
        "proxy_side_margin_usd": proxy_side_margin,
        "official_side_margin_usd": official_side_margin,
        "adverse_basis_usd": adverse_basis,
        "basis_consumed_proxy_margin": basis_consumed_proxy_margin,
        "proxy_boundary_abs_usd": abs(proxy_close_minus_strike),
        "official_boundary_abs_usd": abs(official_exp_minus_strike),
        "proxy_side_win": proxy_side_margin > 0,
        "official_side_win": official_side_margin > 0,
        "official_proxy_result_mismatch": to_bool(row.get("official_proxy_result_mismatch")),
        "proxy_win_official_loss": proxy_side_margin > 0 and official_side_margin <= 0,
        "adverse_basis_exceeded_proxy_margin": proxy_side_margin > 0 and adverse_basis >= proxy_side_margin,
    }


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    official_rows = len(rows)
    official_pnl = sum(to_float(row.get("official_pnl")) for row in rows)
    proxy_pnl = sum(to_float(row.get("proxy_pnl")) for row in rows)
    mismatches = [row for row in rows if row.get("official_proxy_result_mismatch")]
    proxy_win_official_loss = [row for row in rows if row.get("proxy_win_official_loss")]
    abs_basis = [to_float(row.get("abs_official_minus_proxy_spot")) for row in rows]
    adverse_basis = [to_float(row.get("adverse_basis_usd")) for row in rows]
    proxy_margins = [abs(to_float(row.get("proxy_side_margin_usd"))) for row in rows]
    near_proxy = [row for row in rows if to_float(row.get("proxy_boundary_abs_usd")) <= args.watch_usd]
    near_official = [row for row in rows if to_float(row.get("official_boundary_abs_usd")) <= args.watch_usd]
    official_wins = sum(1 for row in rows if row.get("official_side_win"))
    no_rows = sum(1 for row in rows if str(row.get("side", "")).lower() == "no")
    yes_rows = sum(1 for row in rows if str(row.get("side", "")).lower() == "yes")

    status_parts = []
    if official_rows < args.min_promotion_official_rows:
        status_parts.append("TOO_FEW_OFFICIAL_ROWS")
    if mismatches:
        status_parts.append("OBSERVED_PROXY_OFFICIAL_MISMATCH")
    if proxy_win_official_loss:
        status_parts.append("PROXY_WIN_OFFICIAL_LOSS_FLIP")
    if not status_parts:
        status_parts.append("NO_BASIS_MISMATCH_IN_CURRENT_SAMPLE")

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": args.ledger,
        "variant": args.variant,
        "audit_status": ";".join(status_parts),
        "deployable_guard_now": False,
        "guard_recommendation": "diagnostic_only_collect_clean_post_restart_rows",
        "official_rows": official_rows,
        "min_promotion_official_rows": args.min_promotion_official_rows,
        "official_pnl": round(official_pnl, 6),
        "proxy_pnl": round(proxy_pnl, 6),
        "official_minus_proxy_pnl": round(official_pnl - proxy_pnl, 6),
        "official_win_rate": round(official_wins / official_rows, 6) if official_rows else 0.0,
        "official_proxy_mismatches": len(mismatches),
        "official_proxy_mismatch_rate": round(len(mismatches) / official_rows, 6) if official_rows else 0.0,
        "proxy_win_official_loss_flips": len(proxy_win_official_loss),
        "no_side_rows": no_rows,
        "yes_side_rows": yes_rows,
        "near_proxy_boundary_rows": len(near_proxy),
        "near_official_boundary_rows": len(near_official),
        "watch_boundary_usd": args.watch_usd,
        "mean_official_minus_proxy_spot": round(
            sum(to_float(row.get("official_minus_proxy_spot")) for row in rows) / official_rows, 6
        )
        if official_rows
        else 0.0,
        "max_abs_official_minus_proxy_spot": round(max(abs_basis), 6) if abs_basis else 0.0,
        "p95_abs_official_minus_proxy_spot": round(pctile(abs_basis, 0.95), 6),
        "max_adverse_basis_usd": round(max(adverse_basis), 6) if adverse_basis else 0.0,
        "p05_proxy_side_abs_margin_usd": round(pctile(proxy_margins, 0.05), 6),
        "median_proxy_side_abs_margin_usd": round(pctile(proxy_margins, 0.50), 6),
    }


def by_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("trade_day_utc", ""))].append(row)
    out = []
    for day, day_rows in sorted(grouped.items()):
        official_rows = len(day_rows)
        official_pnl = sum(to_float(row.get("official_pnl")) for row in day_rows)
        proxy_pnl = sum(to_float(row.get("proxy_pnl")) for row in day_rows)
        out.append(
            {
                "trade_day_utc": day,
                "official_rows": official_rows,
                "official_pnl": round(official_pnl, 6),
                "proxy_pnl": round(proxy_pnl, 6),
                "official_minus_proxy_pnl": round(official_pnl - proxy_pnl, 6),
                "official_proxy_mismatches": sum(1 for row in day_rows if row.get("official_proxy_result_mismatch")),
                "proxy_win_official_loss_flips": sum(1 for row in day_rows if row.get("proxy_win_official_loss")),
                "max_abs_official_minus_proxy_spot": round(
                    max(to_float(row.get("abs_official_minus_proxy_spot")) for row in day_rows), 6
                ),
                "min_proxy_side_margin_usd": round(
                    min(to_float(row.get("proxy_side_margin_usd")) for row in day_rows), 6
                ),
                "min_official_side_margin_usd": round(
                    min(to_float(row.get("official_side_margin_usd")) for row in day_rows), 6
                ),
            }
        )
    return out


def watchlist(rows: list[dict[str, Any]], watch_usd: float) -> list[dict[str, Any]]:
    risky = [
        row
        for row in rows
        if row.get("official_proxy_result_mismatch")
        or row.get("proxy_win_official_loss")
        or to_float(row.get("proxy_boundary_abs_usd")) <= watch_usd
        or to_float(row.get("official_boundary_abs_usd")) <= watch_usd
    ]
    return sorted(
        risky,
        key=lambda row: (
            not bool(row.get("official_proxy_result_mismatch")),
            not bool(row.get("proxy_win_official_loss")),
            min(to_float(row.get("proxy_boundary_abs_usd")), to_float(row.get("official_boundary_abs_usd"))),
        ),
    )


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "created_at",
        "trade_day_utc",
        "market_ticker",
        "side",
        "entry_price",
        "model_p_yes",
        "net_edge_cents",
        "quote_age_ms",
        "top_visible_qty",
        "official_result",
        "proxy_result",
        "official_pnl",
        "proxy_pnl",
        "official_minus_proxy_pnl",
        "official_minus_proxy_spot",
        "proxy_close_minus_strike",
        "official_expiration_minus_strike",
        "proxy_side_margin_usd",
        "official_side_margin_usd",
        "adverse_basis_usd",
        "basis_consumed_proxy_margin",
        "official_proxy_result_mismatch",
        "proxy_win_official_loss",
    ]
    out = {field: row.get(field, "") for field in fields}
    for key, value in list(out.items()):
        if isinstance(value, float):
            out[key] = round(value, 6)
    return out


def report(summary: dict[str, Any], watch_rows: list[dict[str, Any]], day_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# BTC1H Official Basis/Mismatch Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Audit status: `{summary['audit_status']}`",
        f"- Deployable guard now: `{summary['deployable_guard_now']}`",
        f"- Recommendation: `{summary['guard_recommendation']}`",
        "",
        "## Summary",
        "",
        f"- Official rows: `{summary['official_rows']}` / min `{summary['min_promotion_official_rows']}`",
        f"- Official PnL: `{summary['official_pnl']}`",
        f"- Proxy PnL: `{summary['proxy_pnl']}`",
        f"- Official minus proxy PnL: `{summary['official_minus_proxy_pnl']}`",
        f"- Official/proxy mismatches: `{summary['official_proxy_mismatches']}` "
        f"({summary['official_proxy_mismatch_rate']})",
        f"- Proxy-win/official-loss flips: `{summary['proxy_win_official_loss_flips']}`",
        f"- Max abs official/proxy basis: `${summary['max_abs_official_minus_proxy_spot']}`",
        f"- P95 abs official/proxy basis: `${summary['p95_abs_official_minus_proxy_spot']}`",
        f"- Max side-adverse basis: `${summary['max_adverse_basis_usd']}`",
        f"- Near-proxy-boundary rows within `${summary['watch_boundary_usd']}`: `{summary['near_proxy_boundary_rows']}`",
        f"- Near-official-boundary rows within `${summary['watch_boundary_usd']}`: `{summary['near_official_boundary_rows']}`",
        "",
        "## Watchlist",
        "",
    ]
    if not watch_rows:
        lines.append("- No watchlist rows.")
    else:
        for row in watch_rows[:12]:
            lines.append(
                "- "
                f"`{row.get('market_ticker')}` {row.get('side')} "
                f"official/proxy `{row.get('official_result')}/{row.get('proxy_result')}`, "
                f"official PnL `{round(to_float(row.get('official_pnl')), 4)}`, "
                f"proxy margin `{round(to_float(row.get('proxy_side_margin_usd')), 2)}` USD, "
                f"official margin `{round(to_float(row.get('official_side_margin_usd')), 2)}` USD, "
                f"basis `{round(to_float(row.get('official_minus_proxy_spot')), 2)}` USD"
            )
    lines += [
        "",
        "## Daily Path",
        "",
    ]
    for row in day_rows:
        lines.append(
            f"- `{row['trade_day_utc']}`: rows `{row['official_rows']}`, "
            f"official PnL `{row['official_pnl']}`, proxy PnL `{row['proxy_pnl']}`, "
            f"mismatches `{row['official_proxy_mismatches']}`"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- This audit is intentionally diagnostic. The sample is too small to fit a guard.",
        "- The mismatch row should become a frozen watch condition after a clean restart, not a hindsight exclusion on these rows.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = [row for row in read_csv(args.trades_csv) if row.get("ledger") == args.ledger]
    rows = [enrich_row(row) for row in raw_rows]
    summary = summarize(rows, args)
    day_rows = by_day(rows)
    watch_rows = watchlist(rows, args.watch_usd)

    write_csv(args.out_dir / "btc1h_basis_mismatch_summary.csv", [summary])
    write_csv(args.out_dir / "btc1h_basis_mismatch_by_day.csv", day_rows)
    write_csv(args.out_dir / "btc1h_basis_mismatch_watchlist.csv", [compact_row(row) for row in watch_rows])
    write_csv(args.out_dir / "btc1h_basis_mismatch_rows.csv", [compact_row(row) for row in rows])
    run_info = {
        "created_at_utc": summary["created_at_utc"],
        "trades_csv": str(args.trades_csv),
        "ledger": args.ledger,
        "variant": args.variant,
        "note": "Diagnostic only. Do not promote or fit guards from this tiny official sample.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(report(summary, watch_rows, day_rows), encoding="utf-8")
    print(report(summary, watch_rows, day_rows))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
