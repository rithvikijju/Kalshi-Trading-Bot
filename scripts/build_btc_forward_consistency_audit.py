#!/usr/bin/env python3
"""Audit whether frozen BTC candidates agree across forward evidence layers.

This is a deployment-control artifact, not a strategy search. It checks the
current frozen BTC15M/BTC1H paths against the agreement standard required for
promotion: live/paper shadow behavior, official settlement, frozen replay,
readiness, and settlement-basis risk must all point the same way.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_forward_consistency_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_FREEZE_UTC = "2026-05-18T04:17:44Z"


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    candidate: str
    shadow_name: str
    readiness_candidate: str
    readiness_source: str | None
    basis_candidate: str
    basis_side: str | None
    min_official_rows: int
    replay_dir_arg: str | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BTC frozen-candidate forward consistency audit.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--freeze-utc", default=DEFAULT_FREEZE_UTC)
    p.add_argument("--readiness-dir", type=Path, default=BACKTEST_ROOT / "deployment_readiness_latest_codex")
    p.add_argument("--shadow-status-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_shadow_status_latest_codex")
    p.add_argument("--forward-report-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_evidence_report_latest_codex")
    p.add_argument("--shadow-official-dir", type=Path, default=BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex")
    p.add_argument("--row-reconciliation-dir", type=Path, default=BACKTEST_ROOT / "btc_forward_row_reconciliation_latest_codex")
    p.add_argument("--basis-risk-dir", type=Path, default=BACKTEST_ROOT / "btc_settlement_basis_risk_audit_latest_codex")
    p.add_argument("--signal-health-dir", type=Path, default=BACKTEST_ROOT / "btc15m_shadow_signal_health_latest_codex")
    p.add_argument("--kill-continue-dir", type=Path, default=BACKTEST_ROOT / "btc_kill_continue_latest_codex")
    p.add_argument(
        "--q250-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex",
    )
    p.add_argument(
        "--q1000-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex",
    )
    p.add_argument(
        "--q250-yes-postfreeze-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex",
    )
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    text = str(value)
    if text.lower() in {"nan", "none", "<na>"}:
        return default
    return text


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return as_text(value).strip().lower() in {"true", "1", "yes", "y"}


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(out):
        return default
    return out


def as_int(value: Any, default: int = 0) -> int:
    return int(round(as_float(value, float(default))))


def append_blocker(row: dict[str, Any], blocker: str) -> None:
    blockers = {
        token
        for token in as_text(row.get("blocking_reasons", "")).split(";")
        if token
    }
    blockers.add(blocker)
    row["blocking_reasons"] = ";".join(sorted(blockers))
    row["consistent_enough_for_promotion"] = False


def scalar(df: pd.DataFrame, col: str, default: Any = "") -> Any:
    if df.empty or col not in df.columns:
        return default
    value = df.iloc[0].get(col, default)
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
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


def shadow_rows(args: argparse.Namespace) -> pd.DataFrame:
    rows = read_csv(args.forward_report_dir / "forward_shadow_summary.csv")
    status = read_csv(args.shadow_status_dir / "shadow_status.csv")
    if rows.empty:
        return status
    if status.empty or "name" not in rows.columns or "name" not in status.columns:
        return rows
    status_cols = [
        "name",
        "process_count",
        "duplicate_process_count",
        "process_hygiene_status",
        "pids",
        "process_created_at_utc",
        "replay_sidecar_exists",
        "replay_sidecar_mtime_utc",
        "replay_sidecar_rows_by_table",
    ]
    status_cols = [
        col
        for col in status_cols
        if col in status.columns and (col not in rows.columns or col == "name")
    ]
    if status_cols == ["name"]:
        return rows
    return rows.merge(status[status_cols], on="name", how="left")


def shadow_metrics(shadows: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    row = first_row(shadows, name=spec.shadow_name)
    if row.empty:
        return {
            "ready_running": False,
            "shadow_status_missing": True,
            "source_freshness_status": "MISSING",
            "source_freshness_current": False,
        }
    source_freshness_status = as_text(scalar(row, "source_freshness_status", ""))
    process_predates_latest_source = as_bool(scalar(row, "process_predates_latest_source", False))
    duplicate_process_count = as_int(scalar(row, "duplicate_process_count", 0))
    process_hygiene_status = as_text(scalar(row, "process_hygiene_status", ""))
    running = as_bool(scalar(row, "running", False))
    return {
        "ready_running": running,
        "shadow_status_missing": False,
        "shadow_process_count": as_int(scalar(row, "process_count", 0)),
        "shadow_duplicate_process_count": duplicate_process_count,
        "shadow_process_hygiene_status": process_hygiene_status,
        "source_freshness_status": source_freshness_status,
        "source_freshness_current": running and source_freshness_status == "RUNNING_SOURCE_CURRENT",
        "source_latest_path": as_text(scalar(row, "source_latest_path", "")),
        "source_latest_mtime_utc": as_text(scalar(row, "source_latest_mtime_utc", "")),
        "process_predates_latest_source": process_predates_latest_source,
        "shadow_signal_scan_rows": as_int(scalar(row, "signal_scan_rows", 0)),
        "shadow_signal_nonzero_candidate_rows_total": as_int(
            scalar(row, "signal_scan_nonzero_candidate_rows", 0)
        ),
        "shadow_order_decision_rows": as_int(scalar(row, "order_decision_rows", 0)),
        "shadow_paper_fills_all": as_int(scalar(row, "paper_filled_rows", 0)),
        "shadow_paper_fills_since": as_int(scalar(row, "paper_filled_rows_since", 0)),
        "shadow_official_filled_all_status": as_int(scalar(row, "official_filled_all", 0)),
        "shadow_official_filled_since_status": as_int(scalar(row, "official_filled_since", 0)),
        "shadow_official_pnl_since_status": as_float(scalar(row, "official_pnl_since", 0.0)),
        "shadow_proxy_official_mismatches_since_status": as_int(
            scalar(row, "proxy_official_mismatches_since", 0)
        ),
        "shadow_latest_detail": as_text(scalar(row, "signal_scan_latest_detail", "")),
        "shadow_capture_error": as_text(scalar(row, "capture_error", "")),
        "shadow_trade_error": as_text(scalar(row, "trade_error", "")),
    }


def official_metrics(official_summary: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    all_row = first_row(official_summary, ledger=spec.shadow_name, scope="all")
    since_row = first_row(official_summary, ledger=spec.shadow_name, scope="since")
    return {
        "shadow_official_filled_all": as_int(scalar(all_row, "official_filled_rows", 0)),
        "shadow_official_pnl_all": as_float(scalar(all_row, "official_pnl", 0.0)),
        "shadow_official_win_rate_all": as_float(scalar(all_row, "official_win_rate", 0.0)),
        "shadow_official_filled_since": as_int(scalar(since_row, "official_filled_rows", 0)),
        "shadow_official_pnl_since": as_float(scalar(since_row, "official_pnl", 0.0)),
        "shadow_official_win_rate_since": as_float(scalar(since_row, "official_win_rate", 0.0)),
        "shadow_proxy_pnl_since": as_float(scalar(since_row, "proxy_pnl", 0.0)),
        "shadow_official_minus_proxy_pnl_since": as_float(
            scalar(since_row, "official_minus_proxy_pnl", 0.0)
        ),
        "shadow_proxy_official_mismatches_since": as_int(
            scalar(since_row, "official_proxy_result_mismatches", 0)
        ),
        "shadow_mean_official_minus_proxy_spot_since": as_float(
            scalar(since_row, "mean_official_minus_proxy_spot", 0.0)
        ),
        "shadow_max_abs_official_minus_proxy_spot_since": as_float(
            scalar(since_row, "max_abs_official_minus_proxy_spot", 0.0)
        ),
    }


def readiness_metrics(readiness: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    if readiness.empty:
        return {
            "readiness_production_ready": False,
            "readiness_failure_reasons": "readiness_missing",
        }
    work = readiness[readiness["candidate"].astype(str).eq(spec.readiness_candidate)].copy()
    if spec.readiness_source is not None and "source" in work.columns:
        work = work[work["source"].astype(str).eq(spec.readiness_source)]
    if work.empty:
        return {
            "readiness_production_ready": False,
            "readiness_failure_reasons": "readiness_candidate_missing",
        }
    row = work.iloc[0]
    return {
        "readiness_source": as_text(row.get("source", "")),
        "readiness_production_ready": as_bool(row.get("production_ready", False)),
        "readiness_failure_reasons": as_text(row.get("failure_reasons", "")),
        "readiness_live_official_trades": as_int(row.get("live_official_trades", 0)),
        "readiness_live_official_pnl": as_float(row.get("live_official_pnl", 0.0)),
        "readiness_live_official_win_rate": as_float(row.get("live_official_win_rate", 0.0)),
        "readiness_live_proxy_trades": as_int(row.get("live_proxy_trades", 0)),
        "readiness_live_proxy_pnl": as_float(row.get("live_proxy_pnl", 0.0)),
    }


def basis_metrics(basis_gates: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    if basis_gates.empty:
        return {
            "basis_gate_pass": False,
            "basis_gate_reasons": "basis_risk_missing",
        }
    work = basis_gates[basis_gates["candidate"].astype(str).eq(spec.basis_candidate)].copy()
    if spec.basis_side is not None and "side" in work.columns:
        work = work[work["side"].astype(str).str.lower().eq(spec.basis_side.lower())]
    if work.empty:
        return {
            "basis_gate_pass": False,
            "basis_gate_reasons": "basis_candidate_missing",
        }
    reasons = sorted(
        {
            reason
            for text in work.get("basis_gate_reasons", pd.Series(dtype=str)).fillna("").astype(str)
            for reason in text.split(";")
            if reason
        }
    )
    return {
        "basis_gate_pass": bool(work["basis_gate_pass"].astype(str).str.lower().eq("true").all()),
        "basis_gate_reasons": ";".join(reasons),
        "basis_gate_official_rows": as_int(pd.to_numeric(work.get("official_rows"), errors="coerce").fillna(0).sum()),
        "basis_gate_max_mismatch_rate": round(
            float(pd.to_numeric(work.get("mismatch_rate"), errors="coerce").fillna(0).max()),
            4,
        ),
        "basis_gate_adverse_mismatches": as_int(
            pd.to_numeric(work.get("adverse_mismatches"), errors="coerce").fillna(0).sum()
        ),
        "basis_gate_min_pnl_delta_per_trade_2c": round(
            float(pd.to_numeric(work.get("pnl_delta_per_both_trade_2c"), errors="coerce").fillna(0).min()),
            4,
        ),
        "basis_gate_max_abs_basis_usd": round(
            float(pd.to_numeric(work.get("max_abs_basis_usd"), errors="coerce").fillna(0).max()),
            4,
        ),
    }


def health_metrics(health: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    row = first_row(health, name=spec.shadow_name)
    if row.empty:
        return {
            "health_readable": False,
            "health_signal_nonzero_rows_since": 0,
            "health_signal_selected_rows_since": 0,
        }
    return {
        "health_readable": as_bool(scalar(row, "readable", False)),
        "health_signal_rows_since": as_int(scalar(row, "signal_scan_rows_since", 0)),
        "health_signal_nonzero_rows_since": as_int(scalar(row, "signal_nonzero_candidate_rows_since", 0)),
        "health_signal_selected_rows_since": as_int(scalar(row, "signal_selected_rows_since", 0)),
        "health_top_detail_family": as_text(scalar(row, "signal_top_detail_family", "")),
        "health_top_detail_family_rows": as_int(scalar(row, "signal_top_detail_family_rows", 0)),
        "health_btc_spot_age_p95_sec_since": round(as_float(scalar(row, "btc_spot_age_p95_sec_since", 0.0)), 4),
        "health_btc_spot_age_gt10_share_since": round(
            as_float(scalar(row, "btc_spot_age_gt10_share_since", 0.0)),
            4,
        ),
    }


def replay_metrics(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "has_postfreeze_replay": False,
        }
    info = read_json(path / "run_info.json")
    summary = read_csv(path / "f2_live_ws_summary.csv")
    if summary.empty and info.get("input_trades"):
        source_trades = Path(str(info.get("input_trades")))
        if not source_trades.is_absolute():
            source_trades = PROJECT_ROOT / source_trades
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
    rest_summary = read_csv(path / "summary.csv")
    if not rest_summary.empty and {"official_trades", "official_pnl"}.issubset(rest_summary.columns):
        official = rest_summary.head(1).rename(
            columns={
                "official_trades": "trades",
                "official_pnl": "pnl",
            }
        )
        official_source = "rest_fill"
    return {
        "has_postfreeze_replay": bool(info or not summary.empty),
        "postfreeze_replay_capture_end_utc": as_text(info.get("capture_end_utc", "")),
        "postfreeze_replay_quote_rows": as_int(info.get("quote_rows_after_meta", 0)),
        "postfreeze_replay_raw_hits": as_int(info.get("f2_raw_hits", 0)),
        "postfreeze_replay_first_signals": as_int(info.get("f2_first_signals", 0)),
        "postfreeze_replay_closed_proxy_trades": as_int(info.get("f2_signals_closed_with_proxy", 0)),
        "postfreeze_replay_official_source": official_source,
        "postfreeze_replay_official_trades": as_int(scalar(official, "trades", 0)),
        "postfreeze_replay_official_pnl": as_float(scalar(official, "pnl", 0.0)),
        "postfreeze_replay_proxy_trades": as_int(scalar(proxy, "trades", 0)),
        "postfreeze_replay_proxy_pnl": as_float(scalar(proxy, "pnl", 0.0)),
    }


def row_reconciliation_metrics(reconciliation: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    row = first_row(reconciliation, candidate=spec.candidate)
    if row.empty:
        return {
            "row_reconciliation_status": "missing",
            "row_reconciliation_pass": False,
            "row_reconciliation_promotion_usable": False,
            "row_reconciliation_blockers": "row_reconciliation_missing",
        }
    return {
        "row_reconciliation_status": as_text(scalar(row, "reconciliation_status", "")),
        "row_reconciliation_pass": as_bool(scalar(row, "row_reconciliation_pass", False)),
        "row_reconciliation_promotion_usable": as_bool(scalar(row, "promotion_usable", False)),
        "row_reconciliation_matched_rows": as_int(scalar(row, "matched_rows", 0)),
        "row_reconciliation_paper_rows": as_int(scalar(row, "paper_rows", 0)),
        "row_reconciliation_replay_rows": as_int(scalar(row, "replay_rows", 0)),
        "row_reconciliation_official_rows": as_int(scalar(row, "official_rows", 0)),
        "row_reconciliation_blockers": as_text(scalar(row, "blocking_reasons", "")),
    }


def kill_continue_metrics(kill_continue: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    row = first_row(kill_continue, candidate=spec.candidate)
    if row.empty:
        return {}
    return {
        "control_action": as_text(scalar(row, "action", "")),
        "control_next_step": as_text(scalar(row, "next_step", "")),
    }


def agreement_status(row: dict[str, Any], spec: CandidateSpec) -> str:
    if not row.get("ready_running", False):
        return "not_running_or_status_missing"
    if as_int(row.get("shadow_duplicate_process_count", 0)) > 0 or row.get("shadow_process_hygiene_status") == "DUPLICATE_TARGET_PROCESSES":
        return "duplicate_target_processes_running"
    if row.get("process_predates_latest_source", False) or row.get("source_freshness_status") == "RUNNING_SOURCE_STALE_RESTART_REQUIRED":
        return "running_source_stale_restart_required"
    if spec.family == "BTC15M":
        shadow_since = as_int(row.get("shadow_paper_fills_since", 0))
        replay_selected = as_int(row.get("postfreeze_replay_closed_proxy_trades", 0))
        replay_official = as_int(row.get("postfreeze_replay_official_trades", 0))
        raw_hits = as_int(row.get("postfreeze_replay_raw_hits", 0))
        first_signals = as_int(row.get("postfreeze_replay_first_signals", 0))
        if shadow_since == 0 and replay_selected == 0 and raw_hits == 0 and first_signals == 0:
            return "consistent_no_signal_no_promotion"
        if shadow_since != replay_selected:
            return "ledger_replay_count_mismatch"
        if replay_selected > replay_official:
            return "paper_replay_count_agree_pending_official"
        return "paper_replay_count_agree_but_not_promotable"
    if as_int(row.get("shadow_official_filled_since", 0)) == 0:
        return "shadow_no_official_since_no_promotion"
    if as_int(row.get("shadow_proxy_official_mismatches_since", 0)) > 0:
        return "shadow_official_proxy_mismatch"
    if as_float(row.get("shadow_official_pnl_since", 0.0)) <= 0:
        return "shadow_official_nonpositive_since"
    return "shadow_positive_but_sparse"


def next_step(row: dict[str, Any], spec: CandidateSpec) -> str:
    status = row.get("agreement_status", "")
    if spec.candidate == "q250_firstskip_qty500":
        if status == "consistent_no_signal_no_promotion":
            return "Keep exact frozen shadow/replay running; collect official rows without retuning. Focus new work on settlement-basis modeling before any promotion."
        if status == "paper_replay_count_agree_pending_official":
            return "Replay and paper fill counts now agree, but settlement is still pending; wait for official REST settlement and keep this as a forward-only control."
        if status == "paper_replay_count_agree_but_not_promotable":
            return "Replay and paper fill counts agree, but the official sample is tiny and currently negative; keep raw q250 as a forward-only side/basis control."
        return "Investigate any paper/replay mismatch, then continue forward-only collection if the frozen rule remains executable."
    if spec.candidate == "q250_firstskip_qty500_yes":
        return "Preregistered paper-only start candidate. Count only future official-settled rows after explicit user authorization; old q250 rows are diagnostic only."
    if spec.candidate == "q1000_yes":
        return "Keep as sparse cleaner-side sentinel; do not promote until it accumulates enough official forward trades."
    if spec.family == "BTC1H":
        return "Observe only. Proxy/official mismatch, too few official rows, and stale ledger execution fields block promotion; restart or extend only with explicit permission."
    return "Research only."


def build_row(
    spec: CandidateSpec,
    args: argparse.Namespace,
    readiness: pd.DataFrame,
    shadows: pd.DataFrame,
    official_summary: pd.DataFrame,
    basis_gates: pd.DataFrame,
    health: pd.DataFrame,
    kill_continue: pd.DataFrame,
    reconciliation: pd.DataFrame,
) -> dict[str, Any]:
    replay_dir = getattr(args, spec.replay_dir_arg) if spec.replay_dir_arg else None
    row: dict[str, Any] = {
        "family": spec.family,
        "candidate": spec.candidate,
        "shadow_name": spec.shadow_name,
        "freeze_utc": args.freeze_utc,
        "promotion_min_official_rows": spec.min_official_rows,
    }
    row.update(shadow_metrics(shadows, spec))
    row.update(official_metrics(official_summary, spec))
    row.update(readiness_metrics(readiness, spec))
    row.update(basis_metrics(basis_gates, spec))
    row.update(health_metrics(health, spec))
    row.update(replay_metrics(replay_dir))
    row.update(row_reconciliation_metrics(reconciliation, spec))
    row.update(kill_continue_metrics(kill_continue, spec))
    row["agreement_status"] = agreement_status(row, spec)

    blockers: list[str] = []
    if not row.get("ready_running", False):
        blockers.append("not_running_or_status_missing")
    if row.get("shadow_status_missing", False):
        blockers.append("shadow_status_missing")
    if as_int(row.get("shadow_duplicate_process_count", 0)) > 0 or row.get("shadow_process_hygiene_status") == "DUPLICATE_TARGET_PROCESSES":
        blockers.append("target_duplicate_processes_running")
        blockers.append("process_hygiene_duplicate_target_processes")
    if row.get("process_predates_latest_source", False) or row.get("source_freshness_status") == "RUNNING_SOURCE_STALE_RESTART_REQUIRED":
        blockers.append("running_source_stale_restart_required")
        blockers.append("current_process_predates_latest_source")
    elif not row.get("source_freshness_current", False):
        blockers.append("shadow_source_freshness_not_current")
    if not row.get("readiness_production_ready", False):
        blockers.append("readiness_not_production_ready")
    if not row.get("basis_gate_pass", False):
        blockers.append("settlement_basis_gate_failed")
    if as_int(row.get("shadow_official_filled_since", 0)) < spec.min_official_rows:
        blockers.append("too_few_shadow_official_rows_since")
    if as_float(row.get("shadow_official_pnl_since", 0.0)) < 0:
        blockers.append("shadow_official_pnl_since_negative")
    if as_int(row.get("shadow_proxy_official_mismatches_since", 0)) > 0:
        blockers.append("shadow_proxy_official_mismatch_since")
    if spec.family == "BTC15M":
        if not row.get("has_postfreeze_replay", False):
            blockers.append("postfreeze_replay_missing")
        if as_int(row.get("postfreeze_replay_official_trades", 0)) < spec.min_official_rows:
            blockers.append("too_few_postfreeze_replay_official_rows")
        if as_int(row.get("postfreeze_replay_closed_proxy_trades", 0)) > as_int(row.get("postfreeze_replay_official_trades", 0)):
            blockers.append("postfreeze_replay_official_settlement_pending")
        if row.get("ready_running", False) and as_int(row.get("shadow_paper_fills_since", 0)) != as_int(row.get("postfreeze_replay_closed_proxy_trades", 0)):
            blockers.append("shadow_paper_vs_replay_count_mismatch")
    if not row.get("row_reconciliation_pass", False):
        blockers.append("paper_replay_row_reconciliation_not_passing")
    if not row.get("row_reconciliation_promotion_usable", False):
        blockers.append("paper_replay_row_reconciliation_not_promotion_usable")
    row_reconciliation_blockers = as_text(row.get("row_reconciliation_blockers", ""))
    if row_reconciliation_blockers:
        blockers.extend(
            f"row_reconciliation_{token}"
            for token in row_reconciliation_blockers.split(";")
            if token
        )
    if row["agreement_status"] == "consistent_no_signal_no_promotion":
        blockers.append("consistent_no_signal_is_not_promotion_evidence")

    row["blocking_reasons"] = ";".join(sorted(set(blockers)))
    row["consistent_enough_for_promotion"] = not blockers
    row["next_step"] = next_step(row, spec)
    return row


def preferred_columns() -> list[str]:
    return [
        "family",
        "candidate",
        "consistent_enough_for_promotion",
        "agreement_status",
        "ready_running",
        "shadow_process_count",
        "shadow_duplicate_process_count",
        "shadow_process_hygiene_status",
        "duplicate_target_process_count",
        "unmanaged_matching_process_count",
        "duplicate_target_names",
        "source_freshness_status",
        "source_freshness_current",
        "process_predates_latest_source",
        "source_latest_path",
        "source_latest_mtime_utc",
        "readiness_production_ready",
        "readiness_failure_reasons",
        "basis_gate_pass",
        "basis_gate_reasons",
        "basis_gate_official_rows",
        "basis_gate_max_mismatch_rate",
        "basis_gate_adverse_mismatches",
        "shadow_paper_fills_since",
        "shadow_official_filled_since",
        "shadow_official_pnl_since",
        "shadow_official_minus_proxy_pnl_since",
        "shadow_proxy_official_mismatches_since",
        "postfreeze_replay_raw_hits",
        "postfreeze_replay_first_signals",
        "postfreeze_replay_closed_proxy_trades",
        "postfreeze_replay_official_source",
        "postfreeze_replay_official_trades",
        "postfreeze_replay_official_pnl",
        "row_reconciliation_status",
        "row_reconciliation_pass",
        "row_reconciliation_promotion_usable",
        "row_reconciliation_matched_rows",
        "row_reconciliation_blockers",
        "health_signal_rows_since",
        "health_signal_nonzero_rows_since",
        "health_signal_selected_rows_since",
        "health_top_detail_family",
        "health_btc_spot_age_p95_sec_since",
        "control_action",
        "blocking_reasons",
        "next_step",
    ]


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        CandidateSpec(
            family="BTC15M",
            candidate="q250_firstskip_qty500",
            shadow_name="btc15m_q250_qty500_firstskip_shadow",
            readiness_candidate="q250_firstskip_qty500",
            readiness_source="latest_live_replay_rest_official",
            basis_candidate="q250_firstskip_qty500",
            basis_side=None,
            min_official_rows=100,
            replay_dir_arg="q250_postfreeze_dir",
        ),
        CandidateSpec(
            family="BTC15M",
            candidate="q1000_yes",
            shadow_name="btc15m_q1000_yes_shadow",
            readiness_candidate="q1000_yes",
            readiness_source="latest_live_replay_rest_official",
            basis_candidate="q1000_yes",
            basis_side="yes",
            min_official_rows=100,
            replay_dir_arg="q1000_postfreeze_dir",
        ),
        CandidateSpec(
            family="BTC15M",
            candidate="q250_firstskip_qty500_yes",
            shadow_name="btc15m_q250_qty500_firstskip_yes_shadow",
            readiness_candidate="btc15m_q250_qty500_firstskip_yes_shadow:since",
            readiness_source="shadow_official_ledger",
            basis_candidate="q250_firstskip_qty500",
            basis_side="yes",
            min_official_rows=100,
            replay_dir_arg="q250_yes_postfreeze_dir",
        ),
        CandidateSpec(
            family="BTC1H",
            candidate="btc1h_high_conf80_entry70_no_chase",
            shadow_name="btc1h_high_conf80_entry70_no_chase_shadow",
            readiness_candidate="btc1h_high_conf80_entry70_no_chase_shadow:since",
            readiness_source="shadow_official_ledger",
            basis_candidate="btc1h_high_conf80_entry70_no_chase_shadow",
            basis_side="no",
            min_official_rows=50,
        ),
    ]

    readiness = read_csv(args.readiness_dir / "readiness_summary.csv")
    shadows = shadow_rows(args)
    shadow_info = read_json(args.shadow_status_dir / "run_info.json")
    official_summary = read_csv(args.shadow_official_dir / "shadow_official_summary.csv")
    reconciliation = read_csv(args.row_reconciliation_dir / "row_reconciliation_summary.csv")
    basis_gates = read_csv(args.basis_risk_dir / "settlement_basis_risk_gates.csv")
    health = read_csv(args.signal_health_dir / "shadow_signal_health_summary.csv")
    kill_continue = read_csv(args.kill_continue_dir / "kill_continue_summary.csv")

    rows = [
        build_row(
            spec=spec,
            args=args,
            readiness=readiness,
            shadows=shadows,
            official_summary=official_summary,
            basis_gates=basis_gates,
            health=health,
            kill_continue=kill_continue,
            reconciliation=reconciliation,
        )
        for spec in specs
    ]
    duplicate_target_process_count = as_int(shadow_info.get("duplicate_target_process_count", 0))
    unmanaged_matching_process_count = as_int(shadow_info.get("unmanaged_matching_process_count", 0))
    duplicate_target_names = as_text(shadow_info.get("duplicate_target_names", ""))
    for row in rows:
        row["duplicate_target_process_count"] = duplicate_target_process_count
        row["unmanaged_matching_process_count"] = unmanaged_matching_process_count
        row["duplicate_target_names"] = duplicate_target_names
        if unmanaged_matching_process_count > 0:
            append_blocker(row, "process_hygiene_unmanaged_matching_processes")
    out = pd.DataFrame(rows)
    ordered = [col for col in preferred_columns() if col in out.columns] + [
        col for col in out.columns if col not in preferred_columns()
    ]
    out = out[ordered]
    out.to_csv(args.out_dir / "forward_consistency_summary.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_utc": args.freeze_utc,
        "candidate_count": int(len(out)),
        "consistent_enough_for_promotion_count": int(out["consistent_enough_for_promotion"].sum()),
        "duplicate_target_process_count": duplicate_target_process_count,
        "unmanaged_matching_process_count": unmanaged_matching_process_count,
        "duplicate_target_names": duplicate_target_names,
        "readiness_dir": str(args.readiness_dir),
        "shadow_status_dir": str(args.shadow_status_dir),
        "forward_report_dir": str(args.forward_report_dir),
        "shadow_official_dir": str(args.shadow_official_dir),
        "row_reconciliation_dir": str(args.row_reconciliation_dir),
        "basis_risk_dir": str(args.basis_risk_dir),
        "signal_health_dir": str(args.signal_health_dir),
        "kill_continue_dir": str(args.kill_continue_dir),
        "q250_postfreeze_dir": str(args.q250_postfreeze_dir),
        "q250_yes_postfreeze_dir": str(args.q250_yes_postfreeze_dir),
        "q1000_postfreeze_dir": str(args.q1000_postfreeze_dir),
        "note": "Deployment-control audit only. It does not search thresholds or authorize live deployment.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    compact_cols = [
        "family",
        "candidate",
        "consistent_enough_for_promotion",
        "agreement_status",
        "ready_running",
        "source_freshness_status",
        "shadow_process_hygiene_status",
        "shadow_duplicate_process_count",
        "unmanaged_matching_process_count",
        "source_freshness_current",
        "process_predates_latest_source",
        "readiness_production_ready",
        "basis_gate_pass",
        "shadow_paper_fills_since",
        "shadow_official_filled_since",
        "shadow_official_pnl_since",
        "postfreeze_replay_raw_hits",
        "postfreeze_replay_first_signals",
        "postfreeze_replay_closed_proxy_trades",
        "postfreeze_replay_official_source",
        "postfreeze_replay_official_trades",
        "health_signal_nonzero_rows_since",
        "blocking_reasons",
    ]
    compact = out[[col for col in compact_cols if col in out.columns]].copy()
    btc15m_activity = out[
        out["family"].astype(str).eq("BTC15M")
        & (
            (pd.to_numeric(out.get("shadow_paper_fills_since", 0), errors="coerce").fillna(0) > 0)
            | (pd.to_numeric(out.get("postfreeze_replay_first_signals", 0), errors="coerce").fillna(0) > 0)
        )
    ]
    if btc15m_activity.empty:
        btc15m_interpretation = (
            "- BTC15M q250/q1000 paper shadows and frozen replay currently agree only because there were zero "
            "post-freeze signals/fills; that is runner health evidence, not edge evidence."
        )
    else:
        btc15m_interpretation = (
            "- BTC15M now has post-freeze activity, but any replay-selected or paper-filled rows remain "
            "collection diagnostics until enough official-settled rows accumulate and source, schema, and post-restart execution gates pass."
        )
    report = [
        "# BTC Forward Consistency Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Freeze UTC: `{args.freeze_utc}`",
        f"Consistent enough for promotion: `{info['consistent_enough_for_promotion_count']}` / `{info['candidate_count']}`",
        "",
        "## Summary",
        "",
        compact.fillna("").to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- No candidate has enough agreement evidence for promotion.",
        btc15m_interpretation,
        "- BTC1H remains blocked even if recent official PnL is positive: the official sample is tiny, proxy settlement disagrees on at least one row, source freshness is stale, and readiness still fails.",
        "- Any future promotion discussion must start from official-settlement rows gathered after the freeze, without retuning thresholds on the same window.",
        "",
        "Full fields are in `forward_consistency_summary.csv`.",
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
