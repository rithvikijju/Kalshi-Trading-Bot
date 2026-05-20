#!/usr/bin/env python3
"""Build a frozen-candidate BTC kill-or-continue report.

This is a deployment-control artifact. It summarizes the current frozen BTC15M
and BTC1H paths using readiness, post-freeze replay, shadow-ledger, and
settlement-basis artifacts. It does not search thresholds or promote anything.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc_kill_continue_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_FREEZE_UTC = "2026-05-18T04:17:44Z"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC frozen-candidate kill-or-continue report.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--freeze-utc", default=DEFAULT_FREEZE_UTC)
    p.add_argument("--readiness-dir", type=Path, default=BACKTEST_ROOT / "deployment_readiness_latest_codex")
    p.add_argument("--forward-report-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_evidence_report_latest_codex")
    p.add_argument("--basis-watch-dir", type=Path, default=BACKTEST_ROOT / "btc15m_settlement_basis_watch_latest_codex")
    p.add_argument("--basis-risk-dir", type=Path, default=BACKTEST_ROOT / "btc_settlement_basis_risk_audit_latest_codex")
    p.add_argument("--predexon-coverage-dir", type=Path, default=BACKTEST_ROOT / "btc15m_predexon_official_coverage_latest_codex")
    p.add_argument("--execution-realism-dir", type=Path, default=BACKTEST_ROOT / "btc_execution_realism_audit_latest_codex")
    p.add_argument("--ledger-schema-dir", type=Path, default=BACKTEST_ROOT / "btc_ledger_schema_preflight_latest_codex")
    p.add_argument("--restart-preflight-dir", type=Path, default=BACKTEST_ROOT / "btc_shadow_restart_preflight_latest_codex")
    p.add_argument("--post-restart-gate-dir", type=Path, default=BACKTEST_ROOT / "btc_post_restart_collection_gate_latest_codex")
    p.add_argument("--signal-health-dir", type=Path, default=BACKTEST_ROOT / "btc15m_shadow_signal_health_latest_codex")
    p.add_argument("--signal-starvation-dir", type=Path, default=BACKTEST_ROOT / "btc15m_signal_starvation_latest_codex")
    p.add_argument(
        "--q250-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex",
        help="Post-freeze q250 raw replay dir or REST official-fill dir.",
    )
    p.add_argument(
        "--q250-yes-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex",
        help="Post-freeze q250 YES-only replay dir or REST official-fill dir.",
    )
    p.add_argument(
        "--q1000-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex",
        help="Post-freeze q1000 YES replay dir or REST official-fill dir.",
    )
    return p.parse_args()


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_csv(path: Path) -> pd.DataFrame:
    path = project_path(path)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    path = project_path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def scalar(df: pd.DataFrame, col: str, default: Any = "") -> Any:
    if df.empty or col not in df.columns:
        return default
    value = df.iloc[0].get(col, default)
    if pd.isna(value):
        return default
    return value


def first_row(df: pd.DataFrame, **filters: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    mask = pd.Series(True, index=df.index)
    for col, value in filters.items():
        if col not in df.columns:
            return pd.DataFrame()
        mask &= df[col].astype(str).eq(str(value))
    return df.loc[mask].head(1).copy()


def postfreeze_metrics(path: Path) -> dict[str, Any]:
    output_dir = project_path(path)
    info = read_json(output_dir / "run_info.json")
    summary = read_csv(output_dir / "f2_live_ws_summary.csv")
    if summary.empty and info.get("input_trades"):
        source_trades = Path(str(info.get("input_trades")))
        source_trades = source_trades if source_trades.is_absolute() else PROJECT_ROOT / source_trades
        source_dir = source_trades.parent
        source_info = read_json(source_dir / "run_info.json")
        source_summary = read_csv(source_dir / "f2_live_ws_summary.csv")
        if source_info:
            info = {**info, **source_info}
        if not source_summary.empty:
            summary = source_summary
    official = first_row(summary, result_mode="official_2c_subset")
    proxy = first_row(summary, result_mode="proxy_2c")
    official_source = "captured_lifecycle"
    rest_summary = read_csv(output_dir / "summary.csv")
    if not rest_summary.empty and {"official_trades", "official_pnl"}.issubset(rest_summary.columns):
        official = rest_summary.head(1).rename(columns={"official_trades": "trades", "official_pnl": "pnl"})
        official_source = "rest_fill"
    return {
        "postfreeze_official_source": official_source,
        "postfreeze_capture_end_utc": info.get("capture_end_utc", ""),
        "postfreeze_quote_rows": info.get("quote_rows_after_meta", ""),
        "postfreeze_raw_hits": info.get("f2_raw_hits", ""),
        "postfreeze_first_signals": info.get("f2_first_signals", ""),
        "postfreeze_first_signal_qty_rejects": info.get("f2_first_signal_qty_rejects", ""),
        "postfreeze_closed_proxy_trades": info.get("f2_signals_closed_with_proxy", ""),
        "postfreeze_official_trades": int(scalar(official, "trades", 0) or 0),
        "postfreeze_official_pnl": float(scalar(official, "pnl", 0.0) or 0.0),
        "postfreeze_proxy_trades": int(scalar(proxy, "trades", 0) or 0),
        "postfreeze_proxy_pnl": float(scalar(proxy, "pnl", 0.0) or 0.0),
    }


def basis_metrics(basis: pd.DataFrame, candidate: str, side: str | None = None) -> dict[str, Any]:
    if basis.empty:
        return {}
    work = basis[basis["candidate"].astype(str).eq(candidate)].copy()
    if side is not None and "side" in work.columns:
        work = work[work["side"].astype(str).str.lower().eq(side)]
    if work.empty:
        return {}
    # For focused candidates there is a single row per side. For both-side
    # candidates aggregate conservatively across sides.
    return {
        "basis_rows": int(pd.to_numeric(work.get("rows"), errors="coerce").fillna(0).sum()),
        "basis_official_rows": int(pd.to_numeric(work.get("official_rows"), errors="coerce").fillna(0).sum()),
        "basis_mismatches": int(pd.to_numeric(work.get("mismatches"), errors="coerce").fillna(0).sum()),
        "basis_mismatch_rate": round(
            float(pd.to_numeric(work.get("mismatches"), errors="coerce").fillna(0).sum())
            / float(max(pd.to_numeric(work.get("both_result_rows"), errors="coerce").fillna(0).sum(), 1)),
            4,
        ),
        "basis_official_pnl_2c": round(float(pd.to_numeric(work.get("official_pnl_2c"), errors="coerce").fillna(0).sum()), 4),
        "basis_proxy_pnl_2c": round(float(pd.to_numeric(work.get("proxy_pnl_2c"), errors="coerce").fillna(0).sum()), 4),
        "basis_pnl_delta_official_minus_proxy_2c": round(
            float(pd.to_numeric(work.get("pnl_delta_official_minus_proxy_2c"), errors="coerce").fillna(0).sum()),
            4,
        ),
        "basis_max_abs_usd": round(float(pd.to_numeric(work.get("max_abs_basis_usd"), errors="coerce").fillna(0).max()), 4),
    }


def basis_risk_metrics(gates: pd.DataFrame, candidate: str, side: str | None = None) -> dict[str, Any]:
    if gates.empty:
        return {}
    work = gates[gates["candidate"].astype(str).eq(candidate)].copy()
    if side is not None and "side" in work.columns:
        work = work[work["side"].astype(str).str.lower().eq(side)]
    if work.empty:
        return {}
    return {
        "basis_gate_pass": bool(work["basis_gate_pass"].astype(str).str.lower().eq("true").all()),
        "basis_gate_reasons": ";".join(
            sorted(
                {
                    reason
                    for text in work.get("basis_gate_reasons", pd.Series(dtype=str)).fillna("").astype(str)
                    for reason in text.split(";")
                    if reason
                }
            )
        ),
        "basis_gate_official_rows": int(pd.to_numeric(work.get("official_rows"), errors="coerce").fillna(0).sum()),
        "basis_gate_max_mismatch_rate": round(float(pd.to_numeric(work.get("mismatch_rate"), errors="coerce").fillna(0).max()), 4),
        "basis_gate_adverse_mismatches": int(pd.to_numeric(work.get("adverse_mismatches"), errors="coerce").fillna(0).sum()),
        "basis_gate_min_pnl_delta_per_trade_2c": round(
            float(pd.to_numeric(work.get("pnl_delta_per_both_trade_2c"), errors="coerce").fillna(0).min()),
            4,
        ),
        "basis_gate_max_abs_basis_usd": round(float(pd.to_numeric(work.get("max_abs_basis_usd"), errors="coerce").fillna(0).max()), 4),
    }


def readiness_metrics(readiness: pd.DataFrame, candidate: str, source: str | None = None) -> dict[str, Any]:
    if readiness.empty:
        return {}
    work = readiness[readiness["candidate"].astype(str).eq(candidate)].copy()
    if source is not None and "source" in work.columns:
        work = work[work["source"].astype(str).eq(source)]
    if work.empty:
        return {}
    row = work.iloc[0]
    return {
        "readiness_source": row.get("source", ""),
        "production_ready": bool(row.get("production_ready", False)),
        "failure_reasons": row.get("failure_reasons", ""),
        "live_official_trades": row.get("live_official_trades", ""),
        "live_official_pnl": row.get("live_official_pnl", ""),
        "live_official_win_rate": row.get("live_official_win_rate", ""),
        "live_proxy_trades": row.get("live_proxy_trades", ""),
        "live_proxy_pnl": row.get("live_proxy_pnl", ""),
        "pred_trades": row.get("pred_trades", ""),
        "pred_pnl": row.get("pred_pnl", ""),
    }


def predexon_coverage_metrics(coverage: pd.DataFrame, candidate: str) -> dict[str, Any]:
    row = first_row(coverage, name=candidate)
    if row.empty:
        return {}
    return {
        "predexon_selected_rows": scalar(row, "selected_rows", ""),
        "predexon_rest_official_rows": scalar(row, "rest_official_rows", ""),
        "predexon_rest_official_coverage_rate": scalar(row, "rest_official_coverage_rate", ""),
        "predexon_rest_official_pnl_2c": scalar(row, "rest_official_pnl_2c", ""),
        "predexon_proxy_all_pnl_2c": scalar(row, "proxy_all_pnl_2c", ""),
        "predexon_uncovered_rows": scalar(row, "uncovered_rows", ""),
        "predexon_uncovered_proxy_pnl_2c": scalar(row, "uncovered_proxy_pnl_2c", ""),
        "predexon_bad_uncovered_windows": scalar(row, "bad_uncovered_windows", ""),
        "predexon_coverage_blockers": scalar(row, "deployment_blockers", ""),
    }


def execution_realism_metrics(execution: pd.DataFrame, source: str, prefix: str) -> dict[str, Any]:
    row = first_row(execution, source=source)
    if row.empty:
        return {}
    return {
        f"{prefix}_execution_status": scalar(row, "audit_status", ""),
        f"{prefix}_execution_rows": scalar(row, "rows", ""),
        f"{prefix}_execution_official_rows": scalar(row, "official_rows", ""),
        f"{prefix}_execution_actual_fee_official_pnl": scalar(row, "actual_fee_official_pnl", ""),
        f"{prefix}_execution_stressed_2c_official_pnl": scalar(row, "stressed_2c_official_pnl", ""),
        f"{prefix}_execution_fee_present_rate": scalar(row, "fee_present_rate", ""),
        f"{prefix}_execution_fee_nonnegative_rate": scalar(row, "fee_nonnegative_rate", ""),
        f"{prefix}_execution_fee_mean": scalar(row, "fee_mean", ""),
        f"{prefix}_execution_fee_max": scalar(row, "fee_max", ""),
        f"{prefix}_execution_field_complete_rate": scalar(row, "required_field_complete_rate", ""),
        f"{prefix}_execution_missing_fields": scalar(row, "missing_or_empty_fields", ""),
        f"{prefix}_execution_blockers": scalar(row, "blockers", ""),
        f"{prefix}_execution_entry_match_rate": scalar(row, "entry_matches_side_ask_rate", ""),
        f"{prefix}_execution_visible_qty_match_rate": scalar(row, "visible_qty_matches_side_ask_qty_rate", ""),
        f"{prefix}_execution_quote_age_present_rate": scalar(row, "quote_age_present_rate", ""),
        f"{prefix}_execution_top_visible_present_rate": scalar(row, "top_visible_qty_present_rate", ""),
    }


def ledger_schema_metrics(schema: pd.DataFrame, ledger: str) -> dict[str, Any]:
    row = first_row(schema, ledger=ledger)
    if row.empty:
        return {}
    return {
        "ledger_schema_status": scalar(row, "preflight_status", ""),
        "ledger_schema_columns": scalar(row, "schema_column_count", ""),
        "ledger_realism_columns_present": scalar(row, "realism_columns_present", ""),
        "ledger_realism_columns_missing": scalar(row, "realism_columns_missing", ""),
        "ledger_restart_required_for_deployable_evidence": scalar(row, "restart_required_for_deployable_ledger", ""),
        "ledger_schema_reason": scalar(row, "reason", ""),
    }


def restart_preflight_metrics(preflight: pd.DataFrame, ledger: str) -> dict[str, Any]:
    row = first_row(preflight, ledger=ledger)
    if row.empty:
        return {}
    return {
        "restart_path_status": scalar(row, "restart_path_status", ""),
        "restart_fresh_schema_status": scalar(row, "fresh_schema_status", ""),
        "restart_fresh_insert_status": scalar(row, "fresh_insert_status", ""),
        "restart_copy_before_schema_status": scalar(row, "copy_before_schema_status", ""),
        "restart_copy_after_schema_status": scalar(row, "copy_after_schema_status", ""),
        "restart_copy_insert_status": scalar(row, "copy_insert_status", ""),
        "restart_deployable_without_live_restart": scalar(row, "deployable_without_live_restart", ""),
    }


def post_restart_gate_metrics(gate: pd.DataFrame, ledger: str) -> dict[str, Any]:
    row = first_row(gate, ledger=ledger)
    if row.empty:
        return {}
    return {
        "post_restart_gate_status": scalar(row, "gate_status", ""),
        "post_restart_collection_ready": scalar(row, "promotion_collection_ready", ""),
        "post_restart_official_rows": scalar(row, "post_restart_official_rows", ""),
        "post_restart_min_official_rows": scalar(row, "min_post_restart_official_rows", ""),
        "post_restart_official_pnl": scalar(row, "official_pnl", ""),
        "post_restart_realism_complete_rows": scalar(row, "realism_complete_rows", ""),
        "post_restart_failure_reasons": scalar(row, "failure_reasons", ""),
    }


def shadow_metrics(shadows: pd.DataFrame, name: str) -> dict[str, Any]:
    row = first_row(shadows, name=name)
    if row.empty:
        return {}
    return {
        "running": scalar(row, "running", ""),
        "source_freshness_status": scalar(row, "source_freshness_status", ""),
        "source_latest_path": scalar(row, "source_latest_path", ""),
        "source_latest_mtime_utc": scalar(row, "source_latest_mtime_utc", ""),
        "process_predates_latest_source": scalar(row, "process_predates_latest_source", ""),
        "paper_filled_rows": scalar(row, "paper_filled_rows", ""),
        "paper_filled_rows_since": scalar(row, "paper_filled_rows_since", ""),
        "official_filled_all": scalar(row, "official_filled_all", ""),
        "official_pnl_all": scalar(row, "official_pnl_all", ""),
        "official_filled_since": scalar(row, "official_filled_since", ""),
        "official_pnl_since": scalar(row, "official_pnl_since", ""),
        "proxy_official_mismatches_all": scalar(row, "proxy_official_mismatches_all", ""),
        "signal_scan_rows": scalar(row, "signal_scan_rows", ""),
        "signal_scan_nonzero_candidate_rows": scalar(row, "signal_scan_nonzero_candidate_rows", ""),
        "signal_scan_latest_detail": scalar(row, "signal_scan_latest_detail", ""),
        "order_decision_rows": scalar(row, "order_decision_rows", ""),
    }


def signal_health_metrics(health: pd.DataFrame, detail_counts: pd.DataFrame, name: str) -> dict[str, Any]:
    row = first_row(health, name=name)
    if row.empty:
        return {}
    details = detail_counts[detail_counts.get("name", pd.Series(dtype=str)).astype(str).eq(name)].copy() if not detail_counts.empty else pd.DataFrame()
    stale = details[details.get("detail_family", pd.Series(dtype=str)).astype(str).isin(["stale_btc_spot", "h02_stale_btc_spot"])]
    return {
        "health_readable": scalar(row, "readable", ""),
        "health_signal_rows_since": scalar(row, "signal_scan_rows_since", ""),
        "health_signal_nonzero_rows_since": scalar(row, "signal_nonzero_candidate_rows_since", ""),
        "health_signal_selected_rows_since": scalar(row, "signal_selected_rows_since", ""),
        "health_top_detail_family": scalar(row, "signal_top_detail_family", ""),
        "health_top_detail_family_rows": scalar(row, "signal_top_detail_family_rows", ""),
        "health_btc_spot_age_p95_sec_since": scalar(row, "btc_spot_age_p95_sec_since", ""),
        "health_btc_spot_age_gt10_share_since": scalar(row, "btc_spot_age_gt10_share_since", ""),
        "health_stale_btc_signal_rows_since": int(pd.to_numeric(stale.get("rows"), errors="coerce").fillna(0).sum()) if not stale.empty else 0,
        "health_stale_btc_signal_share_since": round(
            float(pd.to_numeric(stale.get("rows"), errors="coerce").fillna(0).sum())
            / float(max(pd.to_numeric(row.get("signal_scan_rows_since", pd.Series([0])), errors="coerce").fillna(0).iloc[0], 1)),
            4,
        )
        if not row.empty
        else 0.0,
    }


def signal_starvation_metrics(starvation: pd.DataFrame, candidate: str) -> dict[str, Any]:
    row = first_row(starvation, candidate=candidate)
    if row.empty:
        return {}
    return {
        "starvation_status": scalar(row, "collection_status", ""),
        "starvation_signal_rows_since": scalar(row, "signal_rows_since", ""),
        "starvation_distinct_events_since": scalar(row, "distinct_events_since", ""),
        "starvation_nonzero_candidate_rows_since": scalar(row, "nonzero_candidate_rows_since", ""),
        "starvation_selected_rows_since": scalar(row, "selected_rows_since", ""),
        "starvation_order_decision_rows_since": scalar(row, "order_decision_rows_since", ""),
        "starvation_top_detail_family": scalar(row, "top_detail_family", ""),
        "starvation_top_detail_family_share": scalar(row, "top_detail_family_share", ""),
        "starvation_ttl_outside_share": scalar(row, "ttl_ttl_outside_share", ""),
        "starvation_ttl_below_window_rows": scalar(row, "ttl_ttl_below_window_rows", ""),
        "starvation_ttl_above_window_rows": scalar(row, "ttl_ttl_above_window_rows", ""),
        "starvation_no_edge_rows": scalar(row, "no_edge_rows", ""),
        "starvation_no_edge_p_yes_p95": scalar(row, "no_edge_p_yes_p95", ""),
        "starvation_stale_btc_signal_share": scalar(row, "stale_btc_signal_share_since", ""),
        "starvation_promotion_implication": scalar(row, "promotion_implication", ""),
        "starvation_research_implication": scalar(row, "research_implication", ""),
    }


def action_for(row: dict[str, Any]) -> tuple[str, str]:
    if row["candidate"] == "broad_q_families":
        return (
            "KILL_FOR_DEPLOYMENT",
            "Broad q strategies are REST-official negative and settlement-fragile; keep only as cautionary diagnostics.",
        )
    if row["candidate"] == "q250_firstskip_qty500":
        return (
            "CONTINUE_FORWARD_ONLY",
            "Most interesting BTC15M path, but q250 NO-side basis mismatch blocks deployment; collect post-freeze official rows without retuning.",
        )
    if row["candidate"] == "q250_firstskip_qty500_yes":
        return (
            "PREREGISTERED_PAPER_START_WITH_PERMISSION",
            "Prepared as a fresh YES-only paper shadow because q250 NO-side official/proxy flips are dangerous; old rows are diagnostic only and future rows need explicit paper-only start/restart.",
        )
    if row["candidate"] == "q1000_yes":
        return (
            "CONTINUE_FORWARD_ONLY_SPARSE",
            "Cleaner proxy/official agreement than q250, but the post-freeze official sample is still far too small.",
        )
    if row["candidate"] == "btc1h_high_conf80_entry70_no_chase":
        return (
            "OBSERVE_ONLY_RESTART_WITH_PERMISSION",
            "Tiny official shadow sample, proxy/official mismatch, missing execution fields, and too few post-freeze official rows; clean official-settlement restart needs explicit permission.",
        )
    return ("RESEARCH_ONLY", "No deployment gate evidence.")


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    readiness = read_csv(args.readiness_dir / "readiness_summary.csv")
    shadows = read_csv(args.forward_report_dir / "forward_shadow_summary.csv")
    basis = read_csv(args.basis_watch_dir / "settlement_basis_summary.csv")
    basis_risk = read_csv(args.basis_risk_dir / "settlement_basis_risk_gates.csv")
    predexon_coverage = read_csv(args.predexon_coverage_dir / "predexon_official_coverage_summary.csv")
    execution_realism = read_csv(args.execution_realism_dir / "execution_realism_summary.csv")
    ledger_schema = read_csv(args.ledger_schema_dir / "ledger_schema_preflight_summary.csv")
    restart_preflight = read_csv(args.restart_preflight_dir / "shadow_restart_preflight_summary.csv")
    post_restart_gate = read_csv(args.post_restart_gate_dir / "post_restart_collection_gate_summary.csv")
    health = read_csv(args.signal_health_dir / "shadow_signal_health_summary.csv")
    health_details = read_csv(args.signal_health_dir / "shadow_signal_detail_counts.csv")
    starvation = read_csv(args.signal_starvation_dir / "signal_starvation_summary.csv")
    readiness_info = read_json(args.readiness_dir / "run_info.json")

    rows: list[dict[str, Any]] = []
    broad = {
        "family": "BTC15M",
        "candidate": "broad_q_families",
        "gate": "killed_deploy",
        "freeze_utc": args.freeze_utc,
        "promotion_min_official_trades": 100,
    }
    broad_failures = readiness[readiness["source"].astype(str).eq("broad_gate")].copy() if not readiness.empty else pd.DataFrame()
    broad["readiness_rows"] = int(len(broad_failures))
    broad["worst_live_official_pnl"] = (
        float(pd.to_numeric(broad_failures.get("live_official_pnl"), errors="coerce").min())
        if not broad_failures.empty
        else ""
    )
    broad["failure_reasons"] = "official_negative_broad_q_family;proxy_official_settlement_false_positive"
    broad["action"], broad["next_step"] = action_for(broad)
    rows.append(broad)

    q250 = {
        "family": "BTC15M",
        "candidate": "q250_firstskip_qty500",
        "gate": "frozen_forward_collection",
        "freeze_utc": args.freeze_utc,
        "promotion_min_official_trades": 100,
    }
    q250.update(readiness_metrics(readiness, "q250_firstskip_qty500", "latest_live_replay_rest_official"))
    q250.update(shadow_metrics(shadows, "btc15m_q250_qty500_firstskip_shadow"))
    q250.update(signal_health_metrics(health, health_details, "btc15m_q250_qty500_firstskip_shadow"))
    q250.update(basis_metrics(basis, "q250_firstskip_qty500"))
    q250.update(basis_risk_metrics(basis_risk, "q250_firstskip_qty500"))
    q250.update(predexon_coverage_metrics(predexon_coverage, "q250_firstskip_qty500"))
    q250.update(execution_realism_metrics(execution_realism, "q250_firstskip_qty500_live_replay", "replay"))
    q250.update(execution_realism_metrics(execution_realism, "btc15m_q250_qty500_firstskip_shadow", "ledger"))
    q250.update(ledger_schema_metrics(ledger_schema, "btc15m_q250_qty500_firstskip_shadow"))
    q250.update(restart_preflight_metrics(restart_preflight, "btc15m_q250_qty500_firstskip_shadow"))
    q250.update(post_restart_gate_metrics(post_restart_gate, "btc15m_q250_qty500_firstskip_shadow"))
    q250.update(signal_starvation_metrics(starvation, "q250_firstskip_qty500"))
    q250.update(postfreeze_metrics(args.q250_postfreeze_dir))
    q250["action"], q250["next_step"] = action_for(q250)
    rows.append(q250)

    q250_yes = {
        "family": "BTC15M",
        "candidate": "q250_firstskip_qty500_yes",
        "gate": "preregistered_forward_collection",
        "freeze_utc": args.freeze_utc,
        "promotion_min_official_trades": 100,
    }
    q250_yes.update(shadow_metrics(shadows, "btc15m_q250_qty500_firstskip_yes_shadow"))
    q250_yes.update(basis_metrics(basis, "q250_firstskip_qty500", "yes"))
    q250_yes.update(basis_risk_metrics(basis_risk, "q250_firstskip_qty500", "yes"))
    q250_yes.update(execution_realism_metrics(execution_realism, "btc15m_q250_qty500_firstskip_yes_shadow", "ledger"))
    q250_yes.update(ledger_schema_metrics(ledger_schema, "btc15m_q250_qty500_firstskip_yes_shadow"))
    q250_yes.update(restart_preflight_metrics(restart_preflight, "btc15m_q250_qty500_firstskip_yes_shadow"))
    q250_yes.update(post_restart_gate_metrics(post_restart_gate, "btc15m_q250_qty500_firstskip_yes_shadow"))
    q250_yes.update(postfreeze_metrics(args.q250_yes_postfreeze_dir))
    q250_yes["action"], q250_yes["next_step"] = action_for(q250_yes)
    rows.append(q250_yes)

    q1000 = {
        "family": "BTC15M",
        "candidate": "q1000_yes",
        "gate": "frozen_forward_collection",
        "freeze_utc": args.freeze_utc,
        "promotion_min_official_trades": 100,
    }
    q1000.update(readiness_metrics(readiness, "q1000_yes", "latest_live_replay_rest_official"))
    q1000.update(shadow_metrics(shadows, "btc15m_q1000_yes_shadow"))
    q1000.update(signal_health_metrics(health, health_details, "btc15m_q1000_yes_shadow"))
    q1000.update(basis_metrics(basis, "q1000_yes", "yes"))
    q1000.update(basis_risk_metrics(basis_risk, "q1000_yes", "yes"))
    q1000.update(predexon_coverage_metrics(predexon_coverage, "q1000_yes"))
    q1000.update(execution_realism_metrics(execution_realism, "q1000_yes_live_replay", "replay"))
    q1000.update(execution_realism_metrics(execution_realism, "btc15m_q1000_yes_shadow", "ledger"))
    q1000.update(ledger_schema_metrics(ledger_schema, "btc15m_q1000_yes_shadow"))
    q1000.update(restart_preflight_metrics(restart_preflight, "btc15m_q1000_yes_shadow"))
    q1000.update(post_restart_gate_metrics(post_restart_gate, "btc15m_q1000_yes_shadow"))
    q1000.update(signal_starvation_metrics(starvation, "q1000_yes"))
    q1000.update(postfreeze_metrics(args.q1000_postfreeze_dir))
    q1000["action"], q1000["next_step"] = action_for(q1000)
    rows.append(q1000)

    btc1h = {
        "family": "BTC1H",
        "candidate": "btc1h_high_conf80_entry70_no_chase",
        "gate": "paper_shadow_official_collection",
        "freeze_utc": args.freeze_utc,
        "promotion_min_official_trades": 50,
    }
    btc1h.update(shadow_metrics(shadows, "btc1h_high_conf80_entry70_no_chase_shadow"))
    btc1h_readiness = readiness[readiness["candidate"].astype(str).str.contains("btc1h_high_conf80_entry70_no_chase_shadow:all", regex=False)].head(1)
    if not btc1h_readiness.empty:
        btc1h.update(
            {
                "readiness_source": scalar(btc1h_readiness, "source", ""),
                "production_ready": bool(scalar(btc1h_readiness, "production_ready", False)),
                "failure_reasons": scalar(btc1h_readiness, "failure_reasons", ""),
                "live_official_trades": scalar(btc1h_readiness, "live_official_trades", ""),
                "live_official_pnl": scalar(btc1h_readiness, "live_official_pnl", ""),
                "live_official_win_rate": scalar(btc1h_readiness, "live_official_win_rate", ""),
            }
        )
    btc1h.update(basis_risk_metrics(basis_risk, "btc1h_high_conf80_entry70_no_chase_shadow", "no"))
    btc1h.update(execution_realism_metrics(execution_realism, "btc1h_high_conf80_entry70_no_chase_shadow", "ledger"))
    btc1h.update(ledger_schema_metrics(ledger_schema, "btc1h_high_conf80_entry70_no_chase_shadow"))
    btc1h.update(restart_preflight_metrics(restart_preflight, "btc1h_high_conf80_entry70_no_chase_shadow"))
    btc1h.update(post_restart_gate_metrics(post_restart_gate, "btc1h_high_conf80_entry70_no_chase_shadow"))
    btc1h["action"], btc1h["next_step"] = action_for(btc1h)
    rows.append(btc1h)

    out = pd.DataFrame(rows)
    preferred_cols = [
        "family",
        "candidate",
        "action",
        "gate",
        "production_ready",
        "freeze_utc",
        "promotion_min_official_trades",
        "live_official_trades",
        "live_official_pnl",
        "source_freshness_status",
        "process_predates_latest_source",
        "source_latest_path",
        "source_latest_mtime_utc",
        "paper_filled_rows_since",
        "official_filled_since",
        "postfreeze_official_trades",
        "postfreeze_official_pnl",
        "postfreeze_raw_hits",
        "postfreeze_first_signals",
        "basis_mismatches",
        "basis_gate_pass",
        "basis_gate_reasons",
        "basis_gate_max_mismatch_rate",
        "basis_gate_adverse_mismatches",
        "basis_mismatch_rate",
        "basis_pnl_delta_official_minus_proxy_2c",
        "predexon_selected_rows",
        "predexon_rest_official_rows",
        "predexon_rest_official_coverage_rate",
        "predexon_uncovered_rows",
        "predexon_uncovered_proxy_pnl_2c",
        "predexon_bad_uncovered_windows",
        "predexon_coverage_blockers",
        "replay_execution_status",
        "replay_execution_fee_present_rate",
        "replay_execution_fee_nonnegative_rate",
        "replay_execution_actual_fee_official_pnl",
        "replay_execution_stressed_2c_official_pnl",
        "replay_execution_field_complete_rate",
        "replay_execution_entry_match_rate",
        "replay_execution_visible_qty_match_rate",
        "ledger_execution_status",
        "ledger_execution_fee_present_rate",
        "ledger_execution_fee_nonnegative_rate",
        "ledger_execution_actual_fee_official_pnl",
        "ledger_schema_status",
        "ledger_realism_columns_present",
        "ledger_realism_columns_missing",
        "ledger_restart_required_for_deployable_evidence",
        "restart_path_status",
        "restart_fresh_schema_status",
        "restart_copy_before_schema_status",
        "restart_copy_after_schema_status",
        "restart_copy_insert_status",
        "restart_deployable_without_live_restart",
        "post_restart_gate_status",
        "post_restart_collection_ready",
        "post_restart_official_rows",
        "post_restart_min_official_rows",
        "post_restart_official_pnl",
        "post_restart_realism_complete_rows",
        "starvation_status",
        "starvation_signal_rows_since",
        "starvation_distinct_events_since",
        "starvation_nonzero_candidate_rows_since",
        "starvation_order_decision_rows_since",
        "starvation_top_detail_family",
        "starvation_top_detail_family_share",
        "starvation_ttl_outside_share",
        "starvation_no_edge_p_yes_p95",
        "starvation_stale_btc_signal_share",
        "starvation_promotion_implication",
        "ledger_execution_field_complete_rate",
        "ledger_execution_missing_fields",
        "ledger_execution_blockers",
        "signal_scan_nonzero_candidate_rows",
        "signal_scan_latest_detail",
        "health_top_detail_family",
        "health_stale_btc_signal_share_since",
        "health_btc_spot_age_p95_sec_since",
        "failure_reasons",
        "next_step",
    ]
    ordered_cols = [col for col in preferred_cols if col in out.columns] + [col for col in out.columns if col not in preferred_cols]
    out = out[ordered_cols]
    out.to_csv(args.out_dir / "kill_continue_summary.csv", index=False)
    compact_cols = [
        "family",
        "candidate",
        "action",
        "production_ready",
        "live_official_trades",
        "live_official_pnl",
        "source_freshness_status",
        "process_predates_latest_source",
        "paper_filled_rows_since",
        "postfreeze_official_trades",
        "basis_mismatches",
        "basis_gate_pass",
        "basis_gate_reasons",
        "basis_pnl_delta_official_minus_proxy_2c",
        "predexon_rest_official_rows",
        "predexon_rest_official_coverage_rate",
        "predexon_uncovered_rows",
        "predexon_uncovered_proxy_pnl_2c",
        "replay_execution_status",
        "replay_execution_fee_present_rate",
        "replay_execution_fee_nonnegative_rate",
        "ledger_execution_status",
        "ledger_execution_fee_present_rate",
        "ledger_execution_fee_nonnegative_rate",
        "ledger_schema_status",
        "ledger_restart_required_for_deployable_evidence",
        "restart_path_status",
        "restart_deployable_without_live_restart",
        "post_restart_gate_status",
        "post_restart_official_rows",
        "starvation_status",
        "starvation_signal_rows_since",
        "starvation_nonzero_candidate_rows_since",
        "starvation_order_decision_rows_since",
        "starvation_top_detail_family",
        "starvation_ttl_outside_share",
        "ledger_execution_missing_fields",
        "signal_scan_nonzero_candidate_rows",
        "health_top_detail_family",
        "health_stale_btc_signal_share_since",
        "next_step",
    ]
    compact = out[[col for col in compact_cols if col in out.columns]].copy()

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_utc": args.freeze_utc,
        "production_ready_count": readiness_info.get("production_ready_count", ""),
        "readiness_dir": str(args.readiness_dir),
        "forward_report_dir": str(args.forward_report_dir),
        "basis_watch_dir": str(args.basis_watch_dir),
        "basis_risk_dir": str(args.basis_risk_dir),
        "predexon_coverage_dir": str(args.predexon_coverage_dir),
        "execution_realism_dir": str(args.execution_realism_dir),
        "ledger_schema_dir": str(args.ledger_schema_dir),
        "restart_preflight_dir": str(args.restart_preflight_dir),
        "post_restart_gate_dir": str(args.post_restart_gate_dir),
        "signal_health_dir": str(args.signal_health_dir),
        "signal_starvation_dir": str(args.signal_starvation_dir),
        "q250_postfreeze_dir": str(args.q250_postfreeze_dir),
        "q250_yes_postfreeze_dir": str(args.q250_yes_postfreeze_dir),
        "q1000_postfreeze_dir": str(args.q1000_postfreeze_dir),
        "note": "Control artifact only. Actions are research-control labels, not deployment approvals.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# BTC Kill-Or-Continue Report",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Freeze UTC: `{args.freeze_utc}`",
        f"Readiness production_ready_count: `{info['production_ready_count']}`",
        "",
        "## Summary",
        "",
        compact.fillna("").to_string(index=False),
        "",
        "Full row-level decision fields are in `kill_continue_summary.csv`.",
        "",
        "## Interpretation",
        "",
        "- No row is deployable; actions are research-control labels only.",
        "- q250 first-skip remains the highest-information BTC15M path, but settlement-basis mismatch blocks promotion.",
        "- The BTC15M q250/q1000 forward shadows are also collection-starved when `starvation_status` is `STARVED_NO_POSTFREEZE_CANDIDATES`; that is a reason to keep collecting only as a control, not to retune on this window.",
        "- Predexon REST-official historical support is partial where January markets return REST 404; uncovered rows are stress warnings, not promotion evidence.",
        "- Execution-realism fields are now audited separately; BTC1H shadow rows currently fail ledger field coverage.",
        "- Active shadow ledger schemas are preflighted separately; current 25-column schemas require restart/migration before fills can count as deployable ledger evidence.",
        "- Running-source freshness is preflighted separately; rows from a process that predates its wrapper or engine source cannot count.",
        "- Restart preflight is separate from active-ledger readiness: passing the fresh/copy schema check only means a user-authorized restart or migration should create countable future rows.",
        "- Post-restart collection gate is separate again: it will only count official-settled rows written after a controlled restart with populated execution-realism fields.",
        "- q1000 YES remains cleaner but sparse.",
        "- BTC1H needs more official paper rows and a clean official-settlement shadow restart before any serious promotion discussion.",
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
