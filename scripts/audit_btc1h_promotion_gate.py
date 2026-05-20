#!/usr/bin/env python3
"""Promotion-gate audit for frozen BTC1H candidate strategies.

This script reads already-generated BTC1H robustness/significance artifacts and
post-freeze paper-shadow ledgers.  It does not search thresholds or optimize
parameters.  The gate is deliberately conservative because the objective is to
avoid promoting a candidate that only looks good on one convenient slice.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
LOG_ROOT = PROJECT_ROOT / "logs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_promotion_gate_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

EVALUATED_VARIANTS = [
    "high_conf_80",
    "high_conf_80_no_chase",
    "high_conf_80_entry70_no_chase",
    "high_conf_80_entry59_70_no_chase",
]

SHADOW_DBS = {
    "high_conf_80": Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_shadow.db",
    "high_conf_80_no_chase": Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_no_chase_shadow.db",
    "high_conf_80_entry70_no_chase": Path.home()
    / ".btc_kalshi_bot"
    / "btc_1hr_high_conf80_entry70_no_chase_shadow.db",
    "high_conf_80_entry59_70_no_chase": Path.home()
    / ".btc_kalshi_bot"
    / "btc_1hr_high_conf80_entry59_70_no_chase_shadow.db",
}

SHADOW_LOG_PATTERNS = {
    "high_conf_80": "btc_1hr_high_conf80_shadow_*.out.log",
    "high_conf_80_no_chase": "btc_1hr_high_conf80_no_chase_shadow_*.out.log",
    "high_conf_80_entry70_no_chase": "btc_1hr_high_conf80_entry70_no_chase_shadow_*.out.log",
    "high_conf_80_entry59_70_no_chase": "btc_1hr_high_conf80_entry59_70_no_chase_shadow_*.out.log",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--min-shadow-settled", type=int, default=20)
    parser.add_argument("--max-null-p", type=float, default=0.10)
    return parser.parse_args()


def latest_dir(pattern: str) -> Path:
    matches = [p for p in BACKTEST_ROOT.glob(pattern) if p.is_dir()]
    if not matches:
        raise FileNotFoundError(f"no output dir matching {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def optional_latest_dir(pattern: str) -> Path | None:
    matches = [p for p in BACKTEST_ROOT.glob(pattern) if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def latest_log(pattern: str) -> Path | None:
    matches = [p for p in LOG_ROOT.glob(pattern) if p.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_shadow_db(strategy: str, db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "strategy": strategy,
            "shadow_db": str(db_path),
            "shadow_db_exists": False,
            "shadow_trades_recorded": 0,
        }
    con = sqlite3.connect(db_path)
    try:
        trades = pd.read_sql_query("SELECT * FROM research_live_trades ORDER BY created_at", con)
    finally:
        con.close()
    if trades.empty:
        return {
            "strategy": strategy,
            "shadow_db": str(db_path),
            "shadow_db_exists": True,
            "shadow_trades_recorded": 0,
        }
    first = pd.to_datetime(trades["created_at"], utc=True, errors="coerce").min()
    last = pd.to_datetime(trades["created_at"], utc=True, errors="coerce").max()
    return {
        "strategy": strategy,
        "shadow_db": str(db_path),
        "shadow_db_exists": True,
        "shadow_trades_recorded": int(len(trades)),
        "shadow_first_trade": "" if pd.isna(first) else first.isoformat(),
        "shadow_last_trade": "" if pd.isna(last) else last.isoformat(),
        "shadow_avg_entry": float(pd.to_numeric(trades["entry_price"], errors="coerce").mean()),
        "shadow_avg_edge_cents": float(pd.to_numeric(trades["net_edge_cents"], errors="coerce").mean()),
    }


REPORT_RE = re.compile(
    r"SHADOW (?:hourly )?report strategy=(?P<strategy>\S+) .*?equity=\$(?P<equity>-?[0-9.]+) "
    r"realized_pnl=\$(?P<pnl>-?[0-9.]+) .*?trades=(?P<trades>\d+) settled=(?P<settled>\d+) "
    r"open=(?P<open>\d+) wins=(?P<wins>\d+) losses=(?P<losses>\d+)"
)


def read_latest_shadow_report(strategy: str) -> dict[str, Any]:
    path = latest_log(SHADOW_LOG_PATTERNS[strategy])
    if path is None:
        return {"strategy": strategy, "shadow_log": "", "shadow_log_report_found": False}
    last_match: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = REPORT_RE.search(line)
        if not match:
            continue
        data = match.groupdict()
        if data["strategy"] != strategy:
            continue
        last_match = {
            "strategy": strategy,
            "shadow_log": str(path.relative_to(PROJECT_ROOT)),
            "shadow_log_report_found": True,
            "shadow_equity": float(data["equity"]),
            "shadow_realized_pnl": float(data["pnl"]),
            "shadow_report_trades": int(data["trades"]),
            "shadow_settled": int(data["settled"]),
            "shadow_open": int(data["open"]),
            "shadow_wins": int(data["wins"]),
            "shadow_losses": int(data["losses"]),
        }
    if last_match is None:
        return {
            "strategy": strategy,
            "shadow_log": str(path.relative_to(PROJECT_ROOT)),
            "shadow_log_report_found": False,
        }
    return last_match


def build_gate(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, str]]:
    robustness_dir = latest_dir("btc1h_highconf_robustness_*")
    significance_dir = latest_dir("btc1h_highconf_significance_*")
    direct_dir = optional_latest_dir("btc1h_highconf_direct_aggregate_feb09_may06_*")
    no_chase_diag_dir = optional_latest_dir("btc1h_no_chase_ws_instability_*")
    source = pd.read_csv(robustness_dir / "source_stress_summary.csv")
    ws = pd.read_csv(robustness_dir / "ws_cadence_stress_summary.csv")
    null = pd.read_csv(significance_dir / "breakeven_null_tests.csv")
    direct = pd.DataFrame()
    direct_null = pd.DataFrame()
    if direct_dir is not None and (direct_dir / "summary.csv").exists():
        direct = pd.read_csv(direct_dir / "summary.csv")
    if direct_dir is not None and (direct_dir / "breakeven_null_2c.csv").exists():
        direct_null = pd.read_csv(direct_dir / "breakeven_null_2c.csv")
    no_chase_diag = pd.DataFrame()
    if no_chase_diag_dir is not None and (no_chase_diag_dir / "no_chase_bucket_summary.csv").exists():
        no_chase_diag = pd.read_csv(no_chase_diag_dir / "no_chase_bucket_summary.csv")
    derived_dir = optional_latest_dir("btc1h_entry59_70_derived_*")
    if derived_dir is not None:
        derived_source = derived_dir / "source_stress_summary.csv"
        derived_ws = derived_dir / "ws_cadence_stress_summary.csv"
        if derived_source.exists():
            source = pd.concat([source, pd.read_csv(derived_source)], ignore_index=True, sort=False)
        if derived_ws.exists():
            ws = pd.concat([ws, pd.read_csv(derived_ws)], ignore_index=True, sort=False)

    source = source[
        source["variant"].isin(EVALUATED_VARIANTS)
        & source["source"].eq("predexon")
        & pd.to_numeric(source["extra_stress_cents"], errors="coerce").eq(args.stress_cents)
    ].copy()
    ws = ws[
        ws["variant"].isin(EVALUATED_VARIANTS)
        & pd.to_numeric(ws["extra_stress_cents"], errors="coerce").eq(args.stress_cents)
    ].copy()
    null = null[null["variant"].isin(EVALUATED_VARIANTS) & null["dataset"].eq("predexon_all")].copy()
    if not direct.empty:
        direct = direct[
            direct["variant"].isin(EVALUATED_VARIANTS)
            & direct["split"].eq("all")
            & pd.to_numeric(direct["extra_stress_cents"], errors="coerce").eq(args.stress_cents)
        ].copy()
    if not direct_null.empty:
        direct_null = direct_null[direct_null["variant"].isin(EVALUATED_VARIANTS)].copy()

    shadow_rows = []
    for variant in EVALUATED_VARIANTS:
        row = {"strategy": variant}
        if variant in SHADOW_DBS:
            row.update(read_shadow_db(variant, SHADOW_DBS[variant]))
            row.update(read_latest_shadow_report(variant))
        else:
            row.update(
                {
                    "shadow_db_exists": False,
                    "shadow_trades_recorded": 0,
                    "shadow_log_report_found": False,
                    "shadow_report_trades": 0,
                    "shadow_settled": 0,
                    "shadow_realized_pnl": 0.0,
                }
            )
        shadow_rows.append(row)
    shadows = pd.DataFrame(shadow_rows)

    rows = []
    for variant in EVALUATED_VARIANTS:
        s = source[source["variant"].eq(variant)]
        d = direct[direct["variant"].eq(variant)] if not direct.empty else pd.DataFrame()
        w = ws[ws["variant"].eq(variant)]
        n = null[null["variant"].eq(variant)]
        dn = direct_null[direct_null["variant"].eq(variant)] if not direct_null.empty else pd.DataFrame()
        sh = shadows[shadows["strategy"].eq(variant)]

        pred_pnl = float(s["pnl"].iloc[0]) if not s.empty else float("nan")
        pred_trades = int(s["trades"].iloc[0]) if not s.empty else 0
        pred_dd = float(s["max_dd"].iloc[0]) if not s.empty else float("nan")
        pred_wr = float(s["win_rate"].iloc[0]) if not s.empty else float("nan")
        p_value = float(n["sim_p_ge_pnl"].iloc[0]) if not n.empty else float("nan")
        direct_pnl = float(d["pnl"].iloc[0]) if not d.empty else float("nan")
        direct_trades = int(d["trades"].iloc[0]) if not d.empty else 0
        direct_dd = float(d["max_dd"].iloc[0]) if not d.empty else float("nan")
        direct_wr = float(d["win_rate"].iloc[0]) if not d.empty else float("nan")
        direct_p_value = float(dn["p_ge"].iloc[0]) if not dn.empty else float("nan")
        gate_pnl = direct_pnl if direct_trades > 0 else pred_pnl
        gate_trades = direct_trades if direct_trades > 0 else pred_trades
        gate_p_value = direct_p_value if pd.notna(direct_p_value) else p_value
        ws_min_pnl = float(pd.to_numeric(w["pnl"], errors="coerce").min()) if not w.empty else float("nan")
        ws_positive_cadences = int((pd.to_numeric(w["pnl"], errors="coerce") > 0).sum()) if not w.empty else 0
        ws_cadences = int(w["cadence_sec"].nunique()) if not w.empty else 0
        settled = int(sh["shadow_settled"].fillna(0).iloc[0]) if not sh.empty and "shadow_settled" in sh else 0
        shadow_pnl = float(sh["shadow_realized_pnl"].fillna(0.0).iloc[0]) if not sh.empty and "shadow_realized_pnl" in sh else 0.0
        shadow_trades = int(sh["shadow_report_trades"].fillna(0).iloc[0]) if not sh.empty and "shadow_report_trades" in sh else 0
        no_chase_1s_gt70_pnl = float("nan")
        no_chase_1s_le70_pnl = float("nan")
        if variant == "high_conf_80_no_chase" and not no_chase_diag.empty:
            diag = no_chase_diag[pd.to_numeric(no_chase_diag["cadence_sec"], errors="coerce").eq(1.0)].copy()
            no_chase_1s_gt70_pnl = float(
                pd.to_numeric(diag[diag["entry_bucket"].eq(">70c")]["pnl"], errors="coerce").sum()
            )
            no_chase_1s_le70_pnl = float(
                pd.to_numeric(diag[diag["entry_bucket"].eq("<=70c")]["pnl"], errors="coerce").sum()
            )

        failures = []
        if not (gate_trades >= 25 and gate_pnl > 0):
            failures.append("predexon_stress_not_positive_or_too_few")
        if not (gate_p_value <= args.max_null_p):
            failures.append("breakeven_null_not_strong")
        if not (ws_cadences >= 6 and ws_positive_cadences == ws_cadences and ws_min_pnl > 0):
            failures.append("websocket_cadence_instability")
        if settled < args.min_shadow_settled:
            failures.append("too_few_postfreeze_shadow_settled")
        elif shadow_pnl <= 0:
            failures.append("postfreeze_shadow_not_positive")
        if variant in {"high_conf_80_entry70_no_chase", "high_conf_80_entry59_70_no_chase"}:
            failures.append("research_only_extra_entry_gate_requires_forward_shadow")
        if variant == "high_conf_80_entry59_70_no_chase":
            failures.append("derived_entry_band_audit_not_full_causal_replay")

        rows.append(
            {
                "candidate": variant,
                "predexon_trades": pred_trades,
                "predexon_pnl_2c": pred_pnl,
                "predexon_win_rate": pred_wr,
                "predexon_max_dd": pred_dd,
                "breakeven_null_p": p_value,
                "direct_trades": direct_trades,
                "direct_pnl_2c": direct_pnl,
                "direct_win_rate": direct_wr,
                "direct_max_dd": direct_dd,
                "direct_breakeven_null_p": direct_p_value,
                "ws_cadences": ws_cadences,
                "ws_positive_cadences": ws_positive_cadences,
                "ws_min_pnl_2c": ws_min_pnl,
                "no_chase_1s_gt70_pnl": no_chase_1s_gt70_pnl,
                "no_chase_1s_le70_pnl": no_chase_1s_le70_pnl,
                "shadow_trades": shadow_trades,
                "shadow_settled": settled,
                "shadow_pnl": shadow_pnl,
                "passes_promotion_gate": len(failures) == 0,
                "failure_reasons": ";".join(failures),
            }
        )

    paths = {
        "robustness_dir": str(robustness_dir.relative_to(PROJECT_ROOT)),
        "significance_dir": str(significance_dir.relative_to(PROJECT_ROOT)),
    }
    if derived_dir is not None:
        paths["derived_entry59_70_dir"] = str(derived_dir.relative_to(PROJECT_ROOT))
    if direct_dir is not None:
        paths["direct_highconf_dir"] = str(direct_dir.relative_to(PROJECT_ROOT))
    if no_chase_diag_dir is not None:
        paths["no_chase_instability_dir"] = str(no_chase_diag_dir.relative_to(PROJECT_ROOT))
    return pd.DataFrame(rows), paths


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    work = df.copy()
    for col in work.columns:
        if pd.api.types.is_float_dtype(work[col]):
            work[col] = work[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
        else:
            work[col] = work[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), int(work[col].astype(str).map(len).max())) for col in work.columns}
    lines = ["| " + " | ".join(col.ljust(widths[col]) for col in work.columns) + " |"]
    lines.append("| " + " | ".join("-" * widths[col] for col in work.columns) + " |")
    for _, row in work.iterrows():
        lines.append("| " + " | ".join(str(row[col]).ljust(widths[col]) for col in work.columns) + " |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    table, paths = build_gate(args)
    table.to_csv(args.out_dir / "promotion_gate.csv", index=False)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stress_cents": args.stress_cents,
        "min_shadow_settled": args.min_shadow_settled,
        "max_null_p": args.max_null_p,
        "paths": paths,
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    passed = table[table["passes_promotion_gate"].astype(bool)]
    report = [
        "# BTC1H Promotion Gate Audit",
        "",
        "This audit reads frozen BTC1H candidate artifacts and post-freeze paper-shadow evidence. It does not search thresholds.",
        "",
        "## Gate",
        "",
        f"- Predexon +{args.stress_cents:.0f}c stress PnL must be positive with at least 25 trades.",
        f"- Breakeven-null p-value must be <= {args.max_null_p:.2f}.",
        "- All websocket cadence replays must be positive under the same stress.",
        f"- Post-freeze shadow must have at least {args.min_shadow_settled} settled trades and positive realized PnL.",
        "- When the Feb9-May6 direct aggregate exists, it is used for the Predexon trade-count/PnL/null gate.",
        "",
        "## Verdict",
        "",
        f"Candidates passing promotion gate: `{len(passed)}`.",
    ]
    if passed.empty:
        report.append("")
        report.append("No BTC1H candidate is fully promotable yet. The blockers are currently forward-shadow sample size, and for no-chase specifically 1-second websocket cadence instability.")
    report.extend(["", "## Candidate Table", "", markdown_table(table), "", "## Input Artifacts", ""])
    for key, path in paths.items():
        report.append(f"- `{key}`: `{path}`")
    (args.out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(markdown_table(table))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
