#!/usr/bin/env python3
"""Audit remote BTC research branches for replayability on a captured DuckDB.

This is a branch inventory, not a strategy optimizer. It answers a narrower
question needed before honest backtests: which branch candidates can be replayed
directly on the current collected schema, and which need an adapter or different
data before their results would be meaningful?
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "remote_snapshots"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_branch_replayability_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

RELEVANT_DIFF_PATHS = [
    "scripts",
    "config",
    "kalshi_v2",
    "gnn_arbitrage",
    "neufeld_arb",
    "ga_dmlp",
]

CURRENT_BTC15M_SCRIPTS = {
    "scripts/backtest_btc15m_live_holdout.py",
    "scripts/backtest_btc15m_lowdd_sidecar_selected.py",
    "scripts/audit_btc15m_lowdd_paper_replay_parity.py",
    "scripts/build_btc15m_lowdd_forward_promotion_gate.py",
    "scripts/audit_btc15m_sidecar_fidelity.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=None, help="Materialized BTC capture DuckDB to audit against.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--max-branches", type=int, default=80)
    return parser.parse_args()


def run_git(args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout or ""


def latest_capture_db() -> Path | None:
    candidates: list[Path] = []
    for root in [RUNTIME_ROOT, PROJECT_ROOT / "runtime"]:
        if root.exists():
            candidates.extend(root.glob("**/btc15m_raw_*.duckdb"))
            candidates.extend(root.glob("**/btc15m_live_capture*.duckdb"))
    candidates = [path for path in candidates if path.is_file() and not str(path).endswith(".wal")]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        con.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except duckdb.Error:
        return False


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return con.execute(sql).fetchone()[0]


def inspect_capture_db(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"capture_db": "", "exists": False, "error": "no_capture_db_found"}
    if not path.exists():
        return {"capture_db": str(path), "exists": False, "error": "capture_db_missing"}
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = con.execute("SHOW TABLES").fetchdf()["name"].astype(str).tolist()
        table_set = set(tables)
        info: dict[str, Any] = {
            "capture_db": str(path),
            "exists": True,
            "tables": tables,
            "has_ws_orderbook_top": "ws_orderbook_top" in table_set,
            "has_ws_orderbook_top_dedup": "ws_orderbook_top_dedup" in table_set,
            "has_coinbase_ticker": "coinbase_ticker" in table_set,
            "has_coinbase_ticker_all": "coinbase_ticker_all" in table_set,
            "has_ws_lifecycle": "ws_lifecycle" in table_set,
            "has_ws_lifecycle_all": "ws_lifecycle_all" in table_set,
        }
        top_table = "ws_orderbook_top" if table_exists(con, "ws_orderbook_top") else "ws_orderbook_top_dedup"
        if table_exists(con, top_table):
            market_row = con.execute(
                f"""
                SELECT COUNT(*)::BIGINT AS top_rows,
                       COUNT(DISTINCT market_ticker)::BIGINT AS total_markets,
                       COUNT(DISTINCT CASE WHEN market_ticker LIKE '%-T%' THEN market_ticker END)::BIGINT AS dash_t_markets,
                       COUNT(DISTINCT CASE WHEN market_ticker LIKE '%-B%' THEN market_ticker END)::BIGINT AS dash_b_markets,
                       COUNT(DISTINCT CASE WHEN market_ticker LIKE 'KXBTC15M-%' THEN market_ticker END)::BIGINT AS btc15m_binary_markets,
                       COUNT(DISTINCT CASE WHEN market_ticker LIKE 'KXBTCD-%' THEN market_ticker END)::BIGINT AS btcd_markets,
                       MIN(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS min_utc,
                       MAX(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS max_utc
                FROM {top_table}
                """
            ).fetchdf().iloc[0].to_dict()
            info.update({key: clean_value(value) for key, value in market_row.items()})
        lifecycle_table = "ws_lifecycle" if table_exists(con, "ws_lifecycle") else "ws_lifecycle_all"
        if table_exists(con, lifecycle_table):
            lifecycle_rows = con.execute(
                f"""
                SELECT COALESCE(event_type, '') AS event_type,
                       COALESCE(message_type, '') AS message_type,
                       COUNT(*)::BIGINT AS row_count
                FROM {lifecycle_table}
                GROUP BY 1, 2
                ORDER BY row_count DESC, event_type, message_type
                """
            ).fetchdf()
            info["lifecycle_counts"] = lifecycle_rows.to_dict(orient="records")
            info["lifecycle_determined_rows"] = int(
                lifecycle_rows[lifecycle_rows["event_type"].astype(str).eq("determined")]["row_count"].sum()
            )
            info["lifecycle_settled_rows"] = int(
                lifecycle_rows[lifecycle_rows["event_type"].astype(str).eq("settled")]["row_count"].sum()
            )
        else:
            info["lifecycle_counts"] = []
            info["lifecycle_determined_rows"] = 0
            info["lifecycle_settled_rows"] = 0
        return info
    finally:
        con.close()


def clean_value(value: Any) -> Any:
    if pd.isna(value):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def remote_refs(max_branches: int) -> list[dict[str, str]]:
    raw = run_git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(committerdate:iso8601)|%(refname:short)|%(objectname:short)|%(subject)",
            "refs/remotes/origin",
        ]
    )
    refs: list[dict[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        date, ref, commit, subject = parts
        if ref in {"origin", "origin/HEAD"}:
            continue
        refs.append({"commit_date": date, "ref": ref, "commit": commit, "subject": subject})
        if len(refs) >= max_branches:
            break
    return refs


def branch_files(ref: str) -> list[str]:
    raw = run_git(["ls-tree", "-r", "--name-only", ref], check=False)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def relevant_changed_files(base_ref: str, ref: str) -> list[str]:
    raw = run_git(["diff", "--name-only", f"{base_ref}...{ref}", "--", *RELEVANT_DIFF_PATHS], check=False)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def show_file(ref: str, path: str) -> str:
    return run_git(["show", f"{ref}:{path}"], check=False)


def branch_features(ref: str, files: list[str], changed: list[str]) -> dict[str, Any]:
    file_set = set(files)
    has_kalshi_v2 = any(path.startswith("kalshi_v2/") for path in file_set)
    has_current_btc15m = any(path in file_set for path in CURRENT_BTC15M_SCRIPTS)
    has_gnn = any(path.startswith("gnn_arbitrage/") for path in file_set)
    has_neufeld = any(path.startswith("neufeld_arb/") for path in file_set)
    has_ga = any(path.startswith("ga_dmlp/") for path in file_set)
    kalshi_backtest = show_file(ref, "kalshi_v2/backtest.py") if "kalshi_v2/backtest.py" in file_set else ""
    return {
        "has_kalshi_v2": has_kalshi_v2,
        "has_kalshi_v2_backtest": "kalshi_v2/backtest.py" in file_set,
        "has_kalshi_v2_backtest_sweep": "kalshi_v2/backtest_sweep.py" in file_set,
        "has_current_btc15m_replay_tools": has_current_btc15m,
        "has_gnn_arbitrage": has_gnn,
        "has_neufeld_arb": has_neufeld,
        "has_ga_dmlp": has_ga,
        "changed_relevant_files": changed,
        "kalshi_v2_requires_dedup_tables": "ws_orderbook_top_dedup" in kalshi_backtest,
        "kalshi_v2_requires_all_tables": "coinbase_ticker_all" in kalshi_backtest or "ws_lifecycle_all" in kalshi_backtest,
        "kalshi_v2_requires_t_markets": "-T" in kalshi_backtest,
        "kalshi_v2_requires_determined_lifecycle": "determined" in kalshi_backtest,
        "kalshi_v2_mentions_take_profit": "take_profit" in kalshi_backtest or "TP" in kalshi_backtest,
    }


def classify_replayability(features: dict[str, Any], capture: dict[str, Any]) -> tuple[str, list[str], str]:
    if not capture.get("exists"):
        return "unknown_no_capture_db", ["capture_db_missing"], "Need a materialized capture DB before branch replayability can be checked."

    blockers: list[str] = []
    notes: list[str] = []
    if features["has_current_btc15m_replay_tools"]:
        if not capture.get("has_ws_orderbook_top"):
            blockers.append("missing_ws_orderbook_top")
        if not capture.get("has_coinbase_ticker"):
            blockers.append("missing_coinbase_ticker")
        if int(capture.get("btc15m_binary_markets") or 0) <= 0:
            blockers.append("no_btc15m_binary_markets")
        if blockers:
            return "needs_capture_adapter", blockers, "Current BTC15M replay tools are present but the capture schema is not directly compatible."
        return "directly_replayable_current_btc15m", [], "Current BTC15M replay/fidelity tools match this capture schema."

    if features["has_kalshi_v2"]:
        if not features["has_kalshi_v2_backtest"]:
            return (
                "not_directly_replayable_needs_harness",
                ["missing_branch_capture_backtest_harness"],
                "Branch has kalshi_v2 model/strategy code but no branch-local captured-DuckDB replay harness.",
            )
        if features["kalshi_v2_requires_dedup_tables"] and not capture.get("has_ws_orderbook_top_dedup"):
            blockers.append("missing_ws_orderbook_top_dedup")
        if features["kalshi_v2_requires_all_tables"]:
            if not capture.get("has_coinbase_ticker_all"):
                blockers.append("missing_coinbase_ticker_all")
            if not capture.get("has_ws_lifecycle_all"):
                blockers.append("missing_ws_lifecycle_all")
        if features["kalshi_v2_requires_t_markets"] and int(capture.get("dash_t_markets") or 0) <= 0:
            blockers.append("no_cumulative_T_markets")
        if features["kalshi_v2_requires_determined_lifecycle"] and int(capture.get("lifecycle_determined_rows") or 0) <= 0:
            blockers.append("no_determined_lifecycle_rows")
        if features["kalshi_v2_mentions_take_profit"]:
            notes.append("contains_tp_sl_time_exit_logic_not_deployment_ready_without_live_exit_validation")
        if blockers:
            return "not_directly_replayable_needs_adapter", blockers, "; ".join(notes)
        return "directly_replayable_kalshi_v2", [], "; ".join(notes)

    if features["has_gnn_arbitrage"] or features["has_neufeld_arb"] or features["has_ga_dmlp"]:
        return (
            "not_btc_capture_replay_ready",
            ["non_btc15m_capture_backtester"],
            "Branch contains alternative arbitrage/backtester code, but no BTC15M captured-DuckDB replay contract was detected.",
        )

    return "no_btc_replay_candidate_detected", ["no_relevant_btc_replay_code"], ""


def build_rows(args: argparse.Namespace, capture: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ref_info in remote_refs(args.max_branches):
        ref = ref_info["ref"]
        files = branch_files(ref)
        changed = relevant_changed_files(args.base_ref, ref)
        features = branch_features(ref, files, changed)
        verdict, blockers, note = classify_replayability(features, capture)
        rows.append(
            {
                **ref_info,
                "replayability": verdict,
                "blockers": ";".join(blockers),
                "note": note,
                "has_kalshi_v2": features["has_kalshi_v2"],
                "has_kalshi_v2_backtest": features["has_kalshi_v2_backtest"],
                "has_kalshi_v2_backtest_sweep": features["has_kalshi_v2_backtest_sweep"],
                "has_current_btc15m_replay_tools": features["has_current_btc15m_replay_tools"],
                "has_gnn_arbitrage": features["has_gnn_arbitrage"],
                "has_neufeld_arb": features["has_neufeld_arb"],
                "has_ga_dmlp": features["has_ga_dmlp"],
                "changed_relevant_file_count": len(changed),
                "changed_relevant_files": ";".join(changed[:20]),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_empty_"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def run(args: argparse.Namespace) -> dict[str, Any]:
    capture_db = args.capture_db or latest_capture_db()
    capture = inspect_capture_db(capture_db)
    rows = build_rows(args, capture)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "branch_replayability.csv", rows)
    (args.out_dir / "capture_schema_summary.json").write_text(json.dumps(capture, indent=2, default=str), encoding="utf-8")
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_db": str(capture_db or ""),
        "base_ref": args.base_ref,
        "branch_count": len(rows),
        "replayability_counts": pd.Series([row["replayability"] for row in rows]).value_counts().to_dict() if rows else {},
        "capture_market_counts": {
            "total_markets": capture.get("total_markets", 0),
            "btc15m_binary_markets": capture.get("btc15m_binary_markets", 0),
            "dash_t_markets": capture.get("dash_t_markets", 0),
            "lifecycle_determined_rows": capture.get("lifecycle_determined_rows", 0),
            "lifecycle_settled_rows": capture.get("lifecycle_settled_rows", 0),
        },
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    df = pd.DataFrame(rows)
    report_cols = [
        "ref",
        "commit_date",
        "commit",
        "subject",
        "replayability",
        "blockers",
        "note",
        "changed_relevant_file_count",
    ]
    report = [
        "# BTC Branch Replayability Audit",
        "",
        f"Generated UTC: `{summary['created_at_utc']}`",
        f"Capture DB: `{summary['capture_db']}`",
        f"Base ref: `{args.base_ref}`",
        "",
        "## Capture Schema",
        "",
        "```json",
        json.dumps(summary["capture_market_counts"], indent=2, default=str),
        "```",
        "",
        "## Branches",
        "",
        markdown_table(df[[col for col in report_cols if col in df.columns]]),
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), **summary}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
