#!/usr/bin/env python3
"""Audit whether kalshi_v2 branches can be adapted to BTC15M binary capture.

The branch replayability audit answers whether a branch can run directly.  This
script asks the next question for the kalshi_v2 family: if it cannot run
directly on the current BTC15M websocket DuckDB, what adapter would be needed,
and is that adapter scientifically valid as research-only evidence?
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "remote_snapshots"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_kalshi_v2_adapter_feasibility_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_live_holdout import fetch_results  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--branch-ref", action="append", default=[])
    parser.add_argument("--branch-replayability-csv", type=Path, default=None)
    parser.add_argument("--kalshi-sleep-sec", type=float, default=0.0)
    parser.add_argument("--skip-rest", action="store_true", help="Skip Kalshi REST metadata coverage probe.")
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
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def newest_branch_replayability_csv() -> Path | None:
    matches = list(BACKTEST_ROOT.glob("btc_branch_replayability_*/branch_replayability.csv"))
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def branch_refs_from_replayability(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    if "ref" not in df.columns:
        return []
    if "has_kalshi_v2" in df.columns:
        mask = df["has_kalshi_v2"].astype(str).str.lower().eq("true")
        refs = df.loc[mask, "ref"].astype(str).tolist()
    else:
        refs = df["ref"].astype(str).tolist()
    return [ref for ref in refs if ref and ref.startswith("origin/")]


def branch_files(ref: str) -> list[str]:
    raw = run_git(["ls-tree", "-r", "--name-only", ref], check=False)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def show_file(ref: str, path: str) -> str:
    return run_git(["show", f"{ref}:{path}"], check=False)


def all_kalshi_v2_refs() -> list[str]:
    raw = run_git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)",
            "refs/remotes/origin",
        ]
    )
    refs: list[str] = []
    for ref in raw.splitlines():
        ref = ref.strip()
        if not ref or ref in {"origin", "origin/HEAD"}:
            continue
        files = branch_files(ref)
        if any(path.startswith("kalshi_v2/") for path in files):
            refs.append(ref)
    return refs


def resolve_branch_refs(args: argparse.Namespace) -> list[str]:
    refs = list(args.branch_ref or [])
    if not refs:
        replayability = args.branch_replayability_csv or newest_branch_replayability_csv()
        refs = branch_refs_from_replayability(replayability)
    if not refs:
        refs = all_kalshi_v2_refs()
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        con.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except duckdb.Error:
        return False


def count_or_zero(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    try:
        return int(con.execute(sql).fetchone()[0] or 0)
    except Exception:
        return 0


def inspect_capture(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"capture_db": "", "exists": False, "error": "no_capture_db_found"}
    if not path.exists():
        return {"capture_db": str(path), "exists": False, "error": "capture_db_missing"}
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = con.execute("SHOW TABLES").fetchdf()["name"].astype(str).tolist()
        info: dict[str, Any] = {
            "capture_db": str(path),
            "exists": True,
            "tables": tables,
            "has_ws_orderbook_top": "ws_orderbook_top" in tables,
            "has_coinbase_ticker": "coinbase_ticker" in tables,
            "has_ws_lifecycle": "ws_lifecycle" in tables,
            "has_ws_orderbook_top_dedup": "ws_orderbook_top_dedup" in tables,
            "has_coinbase_ticker_all": "coinbase_ticker_all" in tables,
            "has_ws_lifecycle_all": "ws_lifecycle_all" in tables,
        }
        if table_exists(con, "ws_orderbook_top"):
            row = con.execute(
                """
                SELECT COUNT(*)::BIGINT AS top_rows,
                       COUNT(DISTINCT market_ticker)::BIGINT AS market_count,
                       COUNT(DISTINCT event_ticker)::BIGINT AS event_count,
                       COUNT(DISTINCT CASE WHEN market_ticker LIKE '%-T%' THEN market_ticker END)::BIGINT AS dash_t_markets,
                       COUNT(DISTINCT CASE WHEN market_ticker LIKE '%-B%' THEN market_ticker END)::BIGINT AS dash_b_markets,
                       COUNT(DISTINCT CASE WHEN event_ticker LIKE 'KXBTC15M-%' THEN market_ticker END)::BIGINT AS btc15m_binary_markets,
                       COUNT(CASE WHEN yes_bid IS NOT NULL AND yes_ask IS NOT NULL THEN 1 END)::BIGINT AS rows_with_yes_book,
                       COUNT(CASE WHEN no_bid IS NOT NULL AND no_ask IS NOT NULL THEN 1 END)::BIGINT AS rows_with_no_book,
                       MIN(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS min_utc,
                       MAX(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS max_utc
                FROM ws_orderbook_top
                """
            ).fetchdf().iloc[0].to_dict()
            info.update({key: clean_value(value) for key, value in row.items()})
            events = con.execute(
                """
                SELECT DISTINCT event_ticker
                FROM ws_orderbook_top
                WHERE event_ticker LIKE 'KXBTC15M-%'
                ORDER BY event_ticker
                """
            ).fetchdf()["event_ticker"].astype(str).tolist()
            markets = con.execute(
                """
                SELECT DISTINCT market_ticker
                FROM ws_orderbook_top
                WHERE event_ticker LIKE 'KXBTC15M-%'
                ORDER BY market_ticker
                """
            ).fetchdf()["market_ticker"].astype(str).tolist()
            info["events"] = events
            info["markets"] = markets
        else:
            info.update({"events": [], "markets": []})
        if table_exists(con, "coinbase_ticker"):
            info["coinbase_rows"] = count_or_zero(con, "SELECT COUNT(*) FROM coinbase_ticker")
            info["coinbase_best_bid_null_rows"] = count_or_zero(
                con,
                "SELECT COUNT(*) FROM coinbase_ticker WHERE best_bid IS NULL",
            )
            info["coinbase_best_ask_null_rows"] = count_or_zero(
                con,
                "SELECT COUNT(*) FROM coinbase_ticker WHERE best_ask IS NULL",
            )
            info["coinbase_raw_book_field_rate"] = round(
                1.0
                - (
                    max(info["coinbase_best_bid_null_rows"], info["coinbase_best_ask_null_rows"])
                    / info["coinbase_rows"]
                ),
                6,
            ) if info["coinbase_rows"] else 0.0
        else:
            info["coinbase_rows"] = 0
            info["coinbase_raw_book_field_rate"] = 0.0
        if table_exists(con, "ws_lifecycle"):
            lifecycle = con.execute(
                """
                SELECT COALESCE(event_type, '') AS event_type, COUNT(*)::BIGINT AS row_count
                FROM ws_lifecycle
                GROUP BY 1
                ORDER BY row_count DESC, event_type
                """
            ).fetchdf()
            info["lifecycle_counts"] = lifecycle.to_dict(orient="records")
            info["lifecycle_determined_rows"] = int(
                lifecycle[lifecycle["event_type"].astype(str).eq("determined")]["row_count"].sum()
            )
            info["lifecycle_settled_rows"] = int(
                lifecycle[lifecycle["event_type"].astype(str).eq("settled")]["row_count"].sum()
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


def inspect_rest_metadata(capture: dict[str, Any], sleep_sec: float, skip_rest: bool) -> dict[str, Any]:
    events = [event for event in capture.get("events", []) if event]
    markets = set(str(market) for market in capture.get("markets", []))
    if skip_rest:
        return {
            "rest_skipped": True,
            "events": len(events),
            "captured_markets": len(markets),
            "matched_markets": 0,
            "floor_strike_markets": 0,
            "finalized_markets": 0,
            "result_markets": 0,
        }
    if not events:
        return {"rest_skipped": False, "events": 0, "captured_markets": len(markets)}
    df = fetch_results(events, sleep_sec=sleep_sec)
    if df.empty:
        return {
            "rest_skipped": False,
            "events": len(events),
            "captured_markets": len(markets),
            "rest_markets": 0,
            "matched_markets": 0,
            "floor_strike_markets": 0,
            "finalized_markets": 0,
            "result_markets": 0,
        }
    matched = df[df["market_ticker"].astype(str).isin(markets)].copy()
    return {
        "rest_skipped": False,
        "events": len(events),
        "captured_markets": len(markets),
        "rest_markets": int(len(df)),
        "matched_markets": int(matched["market_ticker"].nunique()),
        "floor_strike_markets": int(matched["floor_strike"].notna().sum()) if "floor_strike" in matched.columns else 0,
        "close_time_markets": int(matched["close_time"].notna().sum()) if "close_time" in matched.columns else 0,
        "finalized_markets": int(matched["status"].astype(str).str.lower().eq("finalized").sum()) if "status" in matched.columns else 0,
        "result_markets": int(matched["result"].astype(str).str.lower().isin(["yes", "no"]).sum()) if "result" in matched.columns else 0,
        "matched_market_sample": matched.head(10).to_dict(orient="records"),
    }


def inspect_branch(ref: str) -> dict[str, Any]:
    files = branch_files(ref)
    file_set = set(files)
    backtest = show_file(ref, "kalshi_v2/backtest.py") if "kalshi_v2/backtest.py" in file_set else ""
    sweep = show_file(ref, "kalshi_v2/backtest_sweep.py") if "kalshi_v2/backtest_sweep.py" in file_set else ""
    model = show_file(ref, "kalshi_v2/model.py") if "kalshi_v2/model.py" in file_set else ""
    strategy = show_file(ref, "kalshi_v2/strategy.py") if "kalshi_v2/strategy.py" in file_set else ""
    config = show_file(ref, "kalshi_v2/config.py") if "kalshi_v2/config.py" in file_set else ""
    data = show_file(ref, "kalshi_v2/data.py") if "kalshi_v2/data.py" in file_set else ""
    all_text = "\n".join([backtest, sweep, model, strategy, config, data])
    commit_date = run_git(["show", "-s", "--format=%ci", ref], check=False).strip()
    subject = run_git(["show", "-s", "--format=%s", ref], check=False).strip()
    return {
        "ref": ref,
        "commit": run_git(["rev-parse", "--short", ref], check=False).strip(),
        "commit_date": commit_date,
        "subject": subject,
        "has_kalshi_v2": any(path.startswith("kalshi_v2/") for path in files),
        "has_backtest": bool(backtest),
        "has_backtest_sweep": bool(sweep),
        "has_model": bool(model),
        "has_strategy": bool(strategy),
        "backtest_filters_dash_t": "str.contains(\"-T\")" in backtest or "str.contains('-T')" in backtest,
        "backtest_parses_dash_t": "split(\"-T\")" in backtest or "split('-T')" in backtest,
        "backtest_uses_dedup_tables": "ws_orderbook_top_dedup" in backtest,
        "backtest_uses_all_tables": "coinbase_ticker_all" in backtest or "ws_lifecycle_all" in backtest,
        "backtest_uses_determined_lifecycle": "event_type = 'determined'" in backtest or 'event_type = "determined"' in backtest,
        "uses_tp_sl_time_exit": any(token in all_text for token in ["take_profit_cents", "stop_loss_pct", "time_exit_min_ttl_m"]),
        "uses_empirical_bank": "build_empirical_bank" in all_text,
        "uses_robust_filter": "robust_filter" in all_text,
        "uses_rest_floor_fields": "parse_market_fields" in data or 'b.get("floor")' in strategy,
        "fair_value_can_accept_unknown_as_cumulative": 'return "unknown"' in model and "# Cumulative" in model,
    }


def evaluate_branch_adapter(branch: dict[str, Any], capture: dict[str, Any], rest: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    advisories: list[str] = []
    adapter_requirements: list[str] = []

    has_top = bool(capture.get("has_ws_orderbook_top"))
    has_btc = bool(capture.get("has_coinbase_ticker"))
    has_lifecycle = bool(capture.get("has_ws_lifecycle"))
    dash_t_markets = int(capture.get("dash_t_markets") or 0)
    btc15m_binary_markets = int(capture.get("btc15m_binary_markets") or 0)
    matched_markets = int(rest.get("matched_markets") or 0)
    floor_markets = int(rest.get("floor_strike_markets") or 0)
    result_markets = int(rest.get("result_markets") or 0)
    captured_markets = int(rest.get("captured_markets") or capture.get("market_count") or 0)
    coinbase_raw_rate = float(capture.get("coinbase_raw_book_field_rate") or 0.0)

    if not branch.get("has_kalshi_v2"):
        blockers.append("branch_has_no_kalshi_v2")
    if not branch.get("has_model"):
        blockers.append("missing_kalshi_v2_model")
    if not branch.get("has_strategy"):
        blockers.append("missing_kalshi_v2_strategy")
    if not branch.get("has_backtest"):
        advisories.append("branch_missing_capture_backtest_harness")
        adapter_requirements.append("reuse_or_build_btc15m_binary_harness")
    if branch.get("backtest_filters_dash_t") or branch.get("backtest_parses_dash_t"):
        blockers.append("direct_v2_backtest_filters_cumulative_dash_t_markets")
    if branch.get("backtest_uses_dedup_tables") and not capture.get("has_ws_orderbook_top_dedup"):
        blockers.append("direct_v2_backtest_expects_ws_orderbook_top_dedup")
    if branch.get("backtest_uses_all_tables") and not capture.get("has_coinbase_ticker_all"):
        blockers.append("direct_v2_backtest_expects_coinbase_ticker_all")
    if branch.get("backtest_uses_all_tables") and not capture.get("has_ws_lifecycle_all"):
        blockers.append("direct_v2_backtest_expects_ws_lifecycle_all")
    if dash_t_markets == 0:
        blockers.append("capture_has_no_cumulative_dash_t_markets")
    if not has_top:
        adapter_requirements.append("materialize_ws_orderbook_top")
    if not has_btc:
        adapter_requirements.append("materialize_coinbase_ticker")
    if not has_lifecycle:
        adapter_requirements.append("materialize_ws_lifecycle")
    if btc15m_binary_markets > 0:
        adapter_requirements.append("map_btc15m_binary_market_to_floor_strike_from_rest")
    if captured_markets and floor_markets < captured_markets:
        adapter_requirements.append("complete_rest_floor_strike_metadata")
    if captured_markets and result_markets < captured_markets:
        adapter_requirements.append("complete_official_rest_results")
    if branch.get("uses_tp_sl_time_exit"):
        advisories.append("tp_sl_time_exit_not_deployment_ready_without_live_exit_validation")
    if coinbase_raw_rate < 0.5:
        advisories.append("coinbase_ticks_not_promotion_grade_or_raw_provenance_missing")

    table_alias_adapter_possible = has_top and has_btc and has_lifecycle
    binary_metadata_adapter_possible = (
        btc15m_binary_markets > 0
        and captured_markets > 0
        and matched_markets >= captured_markets
        and floor_markets >= captured_markets
        and result_markets >= captured_markets
    )
    hold_to_settlement_adapter_possible = (
        bool(branch.get("has_model"))
        and bool(branch.get("has_strategy"))
        and table_alias_adapter_possible
        and binary_metadata_adapter_possible
        and bool(branch.get("fair_value_can_accept_unknown_as_cumulative") or branch.get("uses_rest_floor_fields"))
    )
    original_v2_direct_backtest_possible = (
        bool(branch.get("has_backtest"))
        and capture.get("has_ws_orderbook_top_dedup")
        and capture.get("has_coinbase_ticker_all")
        and capture.get("has_ws_lifecycle_all")
        and dash_t_markets > 0
    )

    if original_v2_direct_backtest_possible:
        verdict = "direct_v2_backtest_possible"
        next_action = "run_branch_backtest_then_apply_current_deployability_gates"
    elif hold_to_settlement_adapter_possible:
        verdict = "research_adapter_feasible_not_original_v2_deployable"
        next_action = "build_preregistered_hold_to_settlement_btc15m_binary_adapter_without_tp_sl"
    elif table_alias_adapter_possible and not binary_metadata_adapter_possible:
        verdict = "adapter_blocked_by_rest_metadata_coverage"
        next_action = "complete_rest_metadata_mapping_before_model_replay"
    else:
        verdict = "adapter_not_ready"
        next_action = "build_missing_harness_or_capture_schema_adapter_first"

    return {
        "ref": branch.get("ref", ""),
        "commit_date": branch.get("commit_date", ""),
        "commit": branch.get("commit", ""),
        "subject": branch.get("subject", ""),
        "has_backtest": bool(branch.get("has_backtest")),
        "has_backtest_sweep": bool(branch.get("has_backtest_sweep")),
        "uses_empirical_bank": bool(branch.get("uses_empirical_bank")),
        "uses_robust_filter": bool(branch.get("uses_robust_filter")),
        "uses_tp_sl_time_exit": bool(branch.get("uses_tp_sl_time_exit")),
        "original_v2_direct_backtest_possible": bool(original_v2_direct_backtest_possible),
        "table_alias_adapter_possible": bool(table_alias_adapter_possible),
        "binary_metadata_adapter_possible": bool(binary_metadata_adapter_possible),
        "hold_to_settlement_adapter_possible": bool(hold_to_settlement_adapter_possible),
        "verdict": verdict,
        "next_action": next_action,
        "blockers": ";".join(sorted(set(blockers))),
        "adapter_requirements": ";".join(sorted(set(adapter_requirements))),
        "advisories": ";".join(sorted(set(advisories))),
        "capture_btc15m_binary_markets": btc15m_binary_markets,
        "capture_dash_t_markets": dash_t_markets,
        "rest_matched_markets": matched_markets,
        "rest_floor_strike_markets": floor_markets,
        "rest_result_markets": result_markets,
        "coinbase_raw_book_field_rate": coinbase_raw_rate,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    capture_db = args.capture_db or latest_capture_db()
    capture = inspect_capture(capture_db)
    rest = inspect_rest_metadata(capture, sleep_sec=args.kalshi_sleep_sec, skip_rest=args.skip_rest)
    refs = resolve_branch_refs(args)
    rows = [evaluate_branch_adapter(inspect_branch(ref), capture, rest) for ref in refs]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "kalshi_v2_adapter_feasibility.csv", rows)
    verdict_counts = {}
    if rows:
        verdict_counts = {
            str(key): int(value)
            for key, value in pd.Series([row["verdict"] for row in rows]).value_counts().items()
        }
    preflight = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture": capture,
        "rest_metadata": rest,
        "branch_refs": refs,
        "verdict_counts": verdict_counts,
    }
    (args.out_dir / "capture_adapter_preflight.json").write_text(
        json.dumps(preflight, indent=2, default=str),
        encoding="utf-8",
    )
    report_df = pd.DataFrame(rows)
    report_cols = [
        "ref",
        "has_backtest",
        "original_v2_direct_backtest_possible",
        "hold_to_settlement_adapter_possible",
        "verdict",
        "blockers",
        "adapter_requirements",
        "advisories",
    ]
    report = [
        "# BTC15M kalshi_v2 Adapter Feasibility",
        "",
        f"Generated UTC: `{preflight['created_at_utc']}`",
        f"Capture DB: `{capture.get('capture_db', '')}`",
        "",
        "## Capture / Metadata Preflight",
        "",
        "```json",
        json.dumps(
            {
                "btc15m_binary_markets": capture.get("btc15m_binary_markets"),
                "dash_t_markets": capture.get("dash_t_markets"),
                "coinbase_raw_book_field_rate": capture.get("coinbase_raw_book_field_rate"),
                "rest_matched_markets": rest.get("matched_markets"),
                "rest_floor_strike_markets": rest.get("floor_strike_markets"),
                "rest_result_markets": rest.get("result_markets"),
            },
            indent=2,
            default=str,
        ),
        "```",
        "",
        "## Branch Results",
        "",
        markdown_table(report_df[[col for col in report_cols if col in report_df.columns]]) if not report_df.empty else "_empty_",
        "",
        "## Interpretation",
        "",
        "- `original_v2_direct_backtest_possible=false` means the branch backtest cannot be run as-is on the BTC15M binary capture.",
        "- `hold_to_settlement_adapter_possible=true` means the model ideas can be tested as a new research adapter using official REST metadata and settlement.",
        "- TP/SL/time-exit logic remains excluded from deployment evidence until it has independent live exit-fill validation.",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {"out_dir": str(args.out_dir), "rows": len(rows), "verdict_counts": preflight["verdict_counts"]}


def main() -> int:
    args = parse_args()
    print(json.dumps(run(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
