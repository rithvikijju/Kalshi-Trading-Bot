#!/usr/bin/env python3
"""Conservative deployment-readiness summary for BTC strategy audits.

This script does not search for new alpha. It reads the latest BTC15M/BTC1H
gate artifacts and emits a single PASS/FAIL report so we do not promote a
strategy from a cherry-picked table.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"deployment_readiness_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def latest_dir(prefix: str) -> Path | None:
    matches = [p for p in BACKTEST_ROOT.glob(f"{prefix}*") if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def latest_materialized_grid_dir() -> Path | None:
    """Prefer materialized grids scored with REST-official historical results."""
    preferred: list[Path] = []
    for prefix in (
        "btc15m_materialized_filter_grid_pred_official_",
        "btc15m_materialized_filter_grid_rest_official_",
    ):
        match = latest_dir(prefix)
        if match is not None:
            preferred.append(match)
    if preferred:
        return max(preferred, key=lambda path: path.stat().st_mtime)
    return latest_dir("btc15m_materialized_filter_grid_")


def latest_dirs(prefixes: list[str]) -> list[Path]:
    out: list[Path] = []
    for prefix in prefixes:
        match = latest_dir(prefix)
        if match is not None:
            out.append(match)
    return out


def latest_named_or_prefixed_dirs(names: list[str], prefixes: list[str]) -> Path | None:
    """Return the freshest existing output dir across stable aliases and timestamped dirs."""
    candidates: list[Path] = []
    for name in names:
        path = BACKTEST_ROOT / name
        if path.is_dir():
            candidates.append(path)
    for prefix in prefixes:
        match = latest_dir(prefix)
        if match is not None:
            candidates.append(match)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def latest_shadow_official_dir() -> Path | None:
    for name in (
        "remote_btc_shadow_official_settlement_latest_codex",
        "btc_shadow_official_settlement_latest_codex",
    ):
        path = BACKTEST_ROOT / name
        if path.is_dir():
            return path
    for prefix in ("remote_btc_shadow_official_settlement_", "btc_shadow_official_settlement_"):
        match = latest_dir(prefix)
        if match is not None:
            return match
    return None


def latest_btc1h_multi_holdout_dir() -> Path | None:
    return latest_named_or_prefixed_dirs(
        ["btc1h_multi_holdout_research_latest_codex"],
        ["btc1h_multi_holdout_research_"],
    )


def latest_btc1h_replay_reconciliation_dir() -> Path | None:
    stable = BACKTEST_ROOT / "btc1h_replay_vs_ledger_reconciliation_latest_codex"
    if stable.is_dir():
        return stable
    candidates: list[Path] = []
    for path in BACKTEST_ROOT.glob("btc1h_replay_vs_ledger_reconciliation_*"):
        if path.is_dir() and "selected_scans" not in path.name and "fullscan" not in path.name:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def latest_btc1h_clean_evidence_clock_dir() -> Path | None:
    return latest_named_or_prefixed_dirs(
        ["btc1h_clean_evidence_clock_gate_latest_codex"],
        ["btc1h_clean_evidence_clock_gate_"],
    )


def latest_btc1h_basis_mismatch_dir() -> Path | None:
    return latest_named_or_prefixed_dirs(
        ["btc1h_official_basis_mismatch_audit_latest_codex"],
        ["btc1h_official_basis_mismatch_audit_"],
    )


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def has_cf_credentials(env_path: Path) -> bool:
    if not env_path.exists():
        return False
    text = env_path.read_text(encoding="utf-8", errors="ignore").upper()
    markers = ("CFB_", "CF_BENCHMARK", "BRTI", "CFBENCH")
    return any(marker in text for marker in markers)


def to_float(row: pd.Series, key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key, default)
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def to_int_value(value: Any, default: int = 0) -> int:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(out):
        return default
    return int(out)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def reason_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    return [token for token in text.split(";") if token]


def add_btc15m_broad(
    rows: list[dict[str, Any]],
    df: pd.DataFrame,
    rest_df: pd.DataFrame,
    min_proxy: int,
    min_official: int,
) -> None:
    rest_by_candidate: dict[str, pd.Series] = {}
    if not rest_df.empty and "candidate" in rest_df:
        for _, rest_row in rest_df.iterrows():
            rest_by_candidate[str(rest_row.get("candidate", ""))] = rest_row
    for _, row in df.iterrows():
        candidate = str(row.get("candidate", ""))
        rest = rest_by_candidate.get(candidate)
        live_proxy_trades = to_float(rest, "proxy_trades", to_float(row, "live_proxy_trades")) if rest is not None else to_float(row, "live_proxy_trades")
        live_proxy_pnl = to_float(rest, "proxy_pnl", to_float(row, "live_proxy_pnl")) if rest is not None else to_float(row, "live_proxy_pnl")
        live_official_trades = to_float(rest, "official_trades", to_float(row, "live_official_trades")) if rest is not None else to_float(row, "live_official_trades")
        live_official_pnl = to_float(rest, "official_pnl", to_float(row, "live_official_pnl")) if rest is not None else to_float(row, "live_official_pnl")
        live_official_win_rate = to_float(rest, "official_win_rate", 0.0) if rest is not None else ""
        reasons: list[str] = []
        if not to_bool(row.get("deploy_ready", False)):
            existing = reason_tokens(row.get("failure_reasons", ""))
            if existing:
                reasons.extend(existing)
            else:
                reasons.append("gate_not_ready")
        if live_proxy_trades < min_proxy:
            reasons.append("too_few_live_proxy_trades")
        if live_official_trades < min_official:
            reasons.append("too_few_live_official_trades")
        if live_official_pnl <= 0:
            reasons.append("live_official_not_positive")
        if to_float(row, "jan13_17_pnl") < 0:
            reasons.append("jan_source_window_negative")
        rows.append(
            {
                "family": "BTC15M",
                "candidate": candidate,
                "source": "broad_gate",
                "production_ready": len(set(reasons)) == 0,
                "failure_reasons": ";".join(sorted(set(reasons))),
                "pred_trades": "",
                "pred_pnl": "",
                "live_proxy_trades": live_proxy_trades,
                "live_proxy_pnl": live_proxy_pnl,
                "live_official_trades": live_official_trades,
                "live_official_pnl": live_official_pnl,
                "live_official_win_rate": live_official_win_rate,
            }
        )


def add_btc15m_side(
    rows: list[dict[str, Any]],
    df: pd.DataFrame,
    min_pred: int,
    min_proxy: int,
    min_official: int,
) -> None:
    for _, row in df.iterrows():
        reasons: list[str] = []
        if not to_bool(row.get("deploy_ready", False)):
            existing = reason_tokens(row.get("failure_reasons", ""))
            if existing:
                reasons.extend(existing)
            else:
                reasons.append("gate_not_ready")
        if to_float(row, "pred_trades") < min_pred:
            reasons.append("too_few_predexon_trades")
        if to_float(row, "pred_pnl") <= 0:
            reasons.append("predexon_not_positive")
        if to_float(row, "live_proxy_trades") < min_proxy:
            reasons.append("too_few_live_proxy_trades")
        if to_float(row, "live_official_trades") < min_official:
            reasons.append("too_few_live_official_trades")
        if to_float(row, "live_official_pnl") <= 0:
            reasons.append("live_official_not_positive")
        rows.append(
            {
                "family": "BTC15M",
                "candidate": str(row.get("candidate", "")),
                "source": "side_gate",
                "production_ready": len(set(reasons)) == 0,
                "failure_reasons": ";".join(sorted(set(reasons))),
                "pred_trades": to_float(row, "pred_trades"),
                "pred_pnl": to_float(row, "pred_pnl"),
                "live_proxy_trades": to_float(row, "live_proxy_trades"),
                "live_proxy_pnl": to_float(row, "live_proxy_pnl"),
                "live_official_trades": to_float(row, "live_official_trades"),
                "live_official_pnl": to_float(row, "live_official_pnl"),
                "live_official_win_rate": to_float(row, "live_official_win_rate"),
            }
        )


def add_btc15m_materialized(
    rows: list[dict[str, Any]],
    df: pd.DataFrame,
    min_pred: int,
    min_proxy: int,
    min_official: int,
) -> None:
    """Add first-signal materialized filter candidates to readiness output."""
    for _, row in df.iterrows():
        reasons: list[str] = []
        existing = reason_tokens(row.get("failure_reasons", ""))
        if existing:
            reasons.extend(existing)
        if not to_bool(row.get("deploy_ready", False)) and not existing:
            if to_bool(row.get("research_pass", False)):
                reasons.append("research_pass_not_deployment_ready")
            else:
                reasons.append("gate_not_ready")
        if to_float(row, "pred_trades") < min_pred:
            reasons.append("too_few_predexon_trades")
        if to_float(row, "pred_pnl") <= 0:
            reasons.append("predexon_not_positive")
        if to_float(row, "pred_bad_windows") > 0:
            reasons.append("predexon_bad_windows")
        if to_float(row, "live_proxy_trades") < min_proxy:
            reasons.append("too_few_live_proxy_trades")
        if to_float(row, "live_proxy_pnl") <= 0:
            reasons.append("live_proxy_not_positive")
        if to_float(row, "live_official_trades") < min_official:
            reasons.append("too_few_live_official_trades")
        if to_float(row, "live_official_pnl") <= 0:
            reasons.append("live_official_not_positive")
        name = str(row.get("name", ""))
        live_candidate = str(row.get("live_candidate", ""))
        pred_strategy = str(row.get("pred_strategy", ""))
        candidate = f"{live_candidate}:{name}" if live_candidate or name else pred_strategy
        rows.append(
            {
                "family": "BTC15M",
                "candidate": candidate,
                "source": "materialized_first_signal_grid",
                "production_ready": to_bool(row.get("deploy_ready", False)) and len(set(reasons)) == 0,
                "failure_reasons": ";".join(sorted(set(reasons))),
                "pred_trades": to_float(row, "pred_trades"),
                "pred_pnl": to_float(row, "pred_pnl"),
                "live_proxy_trades": to_float(row, "live_proxy_trades"),
                "live_proxy_pnl": to_float(row, "live_proxy_pnl"),
                "live_official_trades": to_float(row, "live_official_trades"),
                "live_official_pnl": to_float(row, "live_official_pnl"),
                "live_official_win_rate": to_float(row, "live_official_win_rate"),
            }
        )


def add_btc15m_latest_live_replay(
    rows: list[dict[str, Any]],
    replay_dirs: list[Path],
    min_official: int,
) -> None:
    """Add exact current live-replay REST-official summaries for focused candidates."""
    for replay_dir in replay_dirs:
        df = read_csv(replay_dir / "summary.csv")
        if df.empty:
            continue
        for _, row in df.iterrows():
            official_trades = to_float(row, "official_trades")
            official_pnl = to_float(row, "official_pnl")
            proxy_trades = to_float(row, "proxy_trades")
            proxy_pnl = to_float(row, "proxy_pnl")
            mismatches = to_float(row, "official_proxy_result_mismatches")
            pnl_delta = to_float(row, "official_minus_proxy_pnl_2c")
            reasons = ["latest_live_replay_only_not_full_promotion_gate"]
            if official_trades < min_official:
                reasons.append("too_few_live_official_trades")
            if official_pnl <= 0:
                reasons.append("live_official_not_positive")
            if mismatches > 0:
                reasons.append("proxy_official_settlement_mismatch")
            if abs(pnl_delta) > 1e-9:
                reasons.append("proxy_official_pnl_disagreement")
            rows.append(
                {
                    "family": "BTC15M",
                    "candidate": str(row.get("candidate", replay_dir.name)),
                    "source": "latest_live_replay_rest_official",
                    "production_ready": False,
                    "failure_reasons": ";".join(sorted(set(reasons))),
                    "pred_trades": "",
                    "pred_pnl": "",
                    "live_proxy_trades": proxy_trades,
                    "live_proxy_pnl": proxy_pnl,
                    "live_official_trades": official_trades,
                    "live_official_pnl": official_pnl,
                    "live_official_win_rate": to_float(row, "official_win_rate"),
                }
            )


def add_btc15m_lowdd_forward_gate(rows: list[dict[str, Any]], gate_dir: Path | None) -> None:
    """Add the active lowdd wrapper's paper-forward promotion gate."""
    if gate_dir is None:
        return
    df = read_csv(gate_dir / "lowdd_forward_promotion_gate_summary.csv")
    if df.empty:
        rows.append(
            {
                "family": "BTC15M",
                "candidate": "btc15m_lowdd_current_wrapper",
                "source": "lowdd_forward_promotion_gate",
                "production_ready": False,
                "failure_reasons": "missing_lowdd_forward_promotion_gate_summary",
                "pred_trades": "",
                "pred_pnl": "",
                "live_proxy_trades": "",
                "live_proxy_pnl": "",
                "live_official_trades": "",
                "live_official_pnl": "",
                "live_official_win_rate": "",
            }
        )
        return
    for _, row in df.iterrows():
        blockers = reason_tokens(row.get("blockers", ""))
        advisories = reason_tokens(row.get("advisories", ""))
        if not to_bool(row.get("production_ready", False)) and not blockers:
            blockers.append(str(row.get("research_status") or "gate_not_ready"))
        rows.append(
            {
                "family": "BTC15M",
                "candidate": str(row.get("candidate") or "btc15m_lowdd_current_wrapper"),
                "source": "lowdd_forward_promotion_gate",
                "production_ready": to_bool(row.get("production_ready", False)) and not blockers,
                "failure_reasons": ";".join(sorted(set(blockers))),
                "advisories": ";".join(sorted(set(advisories))),
                "pred_trades": "",
                "pred_pnl": "",
                "live_proxy_trades": to_float(row, "selected_rows"),
                "live_proxy_pnl": to_float(row, "signal_one_contract_pnl"),
                "live_official_trades": to_float(row, "paper_settled_rows"),
                "live_official_pnl": to_float(row, "paper_official_pnl"),
                "live_official_win_rate": "",
            }
        )


def add_btc1h(rows: list[dict[str, Any]], audit_dir: Path | None) -> None:
    if audit_dir is None:
        return
    summary_path = audit_dir / "promotion_summary.csv"
    if not summary_path.exists():
        summary_path = audit_dir / "summary.csv"
    if not summary_path.exists():
        summary_path = audit_dir / "promotion_gate.csv"
    df = read_csv(summary_path)
    if df.empty:
        rows.append(
            {
                "family": "BTC1H",
                "candidate": "(unknown)",
                "source": "promotion_gate",
                "production_ready": False,
                "failure_reasons": "missing_btc1h_machine_readable_summary",
                "pred_trades": "",
                "pred_pnl": "",
                "live_proxy_trades": "",
                "live_proxy_pnl": "",
                "live_official_trades": "",
                "live_official_pnl": "",
                "live_official_win_rate": "",
            }
        )
        return
    for _, row in df.iterrows():
        ready_col = "deploy_ready" if "deploy_ready" in row else "promoted" if "promoted" in row else None
        if ready_col is None and "passes_promotion_gate" in row:
            ready_col = "passes_promotion_gate"
        reasons = ";".join(reason_tokens(row.get("failure_reasons", row.get("reasons", ""))))
        if ready_col is None or not to_bool(row.get(ready_col, False)):
            reasons = reasons or "gate_not_ready"
        pred_trades = row.get("direct_trades", "")
        if to_float(row, "direct_trades", 0.0) <= 0 and "predexon_trades" in row:
            pred_trades = row.get("predexon_trades", "")
        pred_pnl = row.get("direct_pnl", row.get("direct_pnl_2c", ""))
        if to_float(row, "direct_trades", 0.0) <= 0 and "predexon_pnl_2c" in row:
            pred_pnl = row.get("predexon_pnl_2c", "")
        rows.append(
            {
                "family": "BTC1H",
                "candidate": str(row.get("candidate", row.get("strategy", ""))),
                "source": "promotion_gate",
                "production_ready": ready_col is not None and to_bool(row.get(ready_col, False)) and not reasons,
                "failure_reasons": reasons,
                "pred_trades": row.get("pred_trades", pred_trades),
                "pred_pnl": row.get("pred_pnl", pred_pnl),
                "live_proxy_trades": row.get("shadow_trades", ""),
                "live_proxy_pnl": row.get("shadow_pnl", ""),
                "live_official_trades": row.get("shadow_settled", ""),
                "live_official_pnl": row.get("shadow_pnl", ""),
                "live_official_win_rate": row.get("shadow_win_rate", ""),
            }
        )


def add_btc1h_multi_holdout(rows: list[dict[str, Any]], df: pd.DataFrame, min_official: int) -> None:
    """Add the current BTC1H multi-holdout research gate rows to readiness."""
    if df.empty:
        return
    for _, row in df.iterrows():
        candidate = str(row.get("variant", row.get("candidate", "")))
        blockers = reason_tokens(row.get("deploy_blockers", ""))
        official_rows = to_float(row, "forward_official_rows")
        official_pnl = to_float(row, "forward_official_pnl")
        mismatch_rate = to_float(row, "forward_official_mismatch_rate", default=float("nan"))
        reasons: list[str] = []
        reasons.extend(blockers)
        if not to_bool(row.get("deployable_now", False)) and not blockers:
            reasons.append("btc1h_multi_holdout_not_deployable")
        if official_rows < min_official:
            reasons.append("too_few_forward_official_rows")
        if official_pnl <= 0:
            reasons.append("forward_official_pnl_not_positive")
        if pd.isna(mismatch_rate) or mismatch_rate > 0:
            reasons.append("forward_official_proxy_mismatch_not_zero")
        rows.append(
            {
                "family": "BTC1H",
                "candidate": candidate,
                "source": "btc1h_multi_holdout_research",
                "production_ready": to_bool(row.get("deployable_now", False)) and len(set(reasons)) == 0,
                "failure_reasons": ";".join(sorted(set(reasons))),
                "pred_trades": row.get("historical_trades", ""),
                "pred_pnl": row.get("historical_pnl_sum", ""),
                "live_proxy_trades": "",
                "live_proxy_pnl": "",
                "live_official_trades": official_rows,
                "live_official_pnl": official_pnl,
                "live_official_win_rate": "",
                "btc1h_research_status": row.get("research_status", ""),
                "btc1h_research_promising": to_bool(row.get("research_promising", False)),
                "btc1h_near_deployable_candidate": to_bool(row.get("near_deployable_candidate", False)),
                "btc1h_all_positive_holdouts": to_float(row, "all_positive_holdouts"),
                "btc1h_all_holdouts": to_float(row, "all_holdouts"),
                "btc1h_ws_positive_cadences": to_float(row, "ws_positive_cadences"),
                "btc1h_ws_cadences": to_float(row, "ws_cadences"),
                "btc1h_forward_snapshot_parity_status": row.get("forward_snapshot_parity_status", ""),
                "btc1h_selected_signal_model_parity_status": row.get("selected_signal_model_parity_status", ""),
                "btc1h_replay_ledger_promotion_usable": to_bool(row.get("replay_ledger_promotion_usable", False)),
                "btc1h_replay_ledger_exact_match_rate": to_float(row, "replay_ledger_exact_match_rate"),
                "btc1h_replay_ledger_blockers": row.get("replay_ledger_blockers", ""),
            }
        )


def add_shadow_official(
    rows: list[dict[str, Any]],
    df: pd.DataFrame,
    min_official: int,
) -> None:
    """Add current paper-shadow ledger audits filled with REST-official results."""
    if df.empty:
        return
    for _, row in df.iterrows():
        ledger = str(row.get("ledger", ""))
        family = "BTC1H" if ledger.startswith("btc1h") else "BTC15M" if ledger.startswith("btc15m") else "BTC"
        scope = str(row.get("scope", ""))
        official_trades = to_float(row, "official_filled_rows")
        official_pnl = to_float(row, "official_pnl")
        proxy_trades = to_float(row, "proxy_filled_rows")
        proxy_pnl = to_float(row, "proxy_pnl")
        proxy_delta = to_float(row, "official_minus_proxy_pnl")
        proxy_mismatches = to_float(row, "official_proxy_result_mismatches")
        reasons = ["shadow_ledger_only_not_full_promotion_gate"]
        if official_trades < min_official:
            reasons.append("too_few_shadow_official_trades")
        if official_pnl <= 0:
            reasons.append("shadow_official_not_positive")
        if proxy_mismatches > 0:
            reasons.append("proxy_official_settlement_mismatch")
        if abs(proxy_delta) > 1e-9:
            reasons.append("proxy_official_pnl_disagreement")
        rows.append(
            {
                "family": family,
                "candidate": f"{ledger}:{scope}" if scope else ledger,
                "source": "shadow_official_ledger",
                "production_ready": False,
                "failure_reasons": ";".join(sorted(set(reasons))),
                "pred_trades": "",
                "pred_pnl": "",
                "live_proxy_trades": proxy_trades,
                "live_proxy_pnl": proxy_pnl,
                "live_official_trades": official_trades,
                "live_official_pnl": official_pnl,
                "live_official_win_rate": to_float(row, "official_win_rate"),
            }
        )


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return default
    return text


def first_row(df: pd.DataFrame, column: str, value: str) -> pd.Series | None:
    if df.empty or column not in df.columns:
        return None
    matches = df[df[column].astype(str).eq(value)]
    if matches.empty:
        return None
    return matches.iloc[0]


def add_reason_tokens(existing: Any, extra: list[str]) -> str:
    return ";".join(sorted(set(reason_tokens(existing) + [token for token in extra if token])))


def apply_reason_blockers(
    summary: pd.DataFrame,
    mask: pd.Series,
    reasons: list[str],
    values: dict[str, Any] | None = None,
) -> None:
    if summary.empty or not mask.any():
        return
    values = values or {}
    for col in values:
        if col not in summary.columns:
            summary[col] = ""
    for idx in summary.index[mask]:
        if reasons:
            summary.at[idx, "failure_reasons"] = add_reason_tokens(summary.at[idx, "failure_reasons"], reasons)
            summary.at[idx, "production_ready"] = False
        for col, value in values.items():
            summary.at[idx, col] = value


def candidate_mask(summary: pd.DataFrame, exact: str | None = None, prefix: str | None = None) -> pd.Series:
    if summary.empty or "candidate" not in summary.columns:
        return pd.Series(False, index=summary.index)
    candidates = summary["candidate"].astype(str)
    if exact is not None:
        return candidates.eq(exact)
    if prefix is not None:
        return candidates.str.startswith(prefix)
    return pd.Series(False, index=summary.index)


def execution_reasons(row: pd.Series | None, kind: str) -> tuple[list[str], dict[str, Any]]:
    if row is None:
        return (
            [f"{kind}_execution_realism_audit_missing"],
            {},
        )
    status = as_text(row.get("audit_status", ""))
    blockers = as_text(row.get("blockers", ""))
    missing_fields = as_text(row.get("missing_or_empty_fields", ""))
    values = {
        f"{kind}_execution_status": status,
        f"{kind}_execution_rows": to_float(row, "rows"),
        f"{kind}_execution_official_rows": to_float(row, "official_rows"),
        f"{kind}_execution_actual_fee_official_pnl": to_float(row, "actual_fee_official_pnl"),
        f"{kind}_execution_stressed_2c_official_pnl": to_float(row, "stressed_2c_official_pnl"),
        f"{kind}_execution_fee_present_rate": to_float(row, "fee_present_rate"),
        f"{kind}_execution_fee_nonnegative_rate": to_float(row, "fee_nonnegative_rate"),
        f"{kind}_execution_fee_mean": to_float(row, "fee_mean"),
        f"{kind}_execution_fee_max": to_float(row, "fee_max"),
        f"{kind}_execution_field_complete_rate": to_float(row, "required_field_complete_rate"),
        f"{kind}_execution_missing_fields": missing_fields,
        f"{kind}_execution_blockers": blockers,
    }
    reasons: list[str] = []
    if kind == "replay":
        if not status.startswith("PASS_REPLAY_EXECUTION_FIELDS"):
            reasons.append("replay_execution_realism_not_passing")
    else:
        if status == "NO_FILLED_LEDGER_ROWS" or to_float(row, "rows") <= 0:
            reasons.append("no_filled_ledger_rows_for_execution_realism")
        if "fee_missing_or_negative" in reason_tokens(blockers):
            reasons.append("shadow_ledger_fee_realism_not_passing")
        if missing_fields:
            reasons.append("shadow_ledger_execution_fields_missing")
        elif status == "FAIL_LEDGER_EXECUTION_FIELDS":
            reasons.append("shadow_ledger_execution_realism_not_passing")
    return reasons, values


def schema_reasons(row: pd.Series | None) -> tuple[list[str], dict[str, Any]]:
    if row is None:
        return ["shadow_ledger_schema_preflight_missing"], {}
    status = as_text(row.get("preflight_status", ""))
    restart_required = to_bool(row.get("restart_required_for_deployable_ledger", False))
    values = {
        "shadow_ledger_schema_status": status,
        "shadow_ledger_schema_columns": to_float(row, "schema_column_count"),
        "shadow_ledger_realism_columns_present": to_float(row, "realism_columns_present"),
        "shadow_ledger_realism_columns_missing": to_float(row, "realism_columns_missing"),
        "shadow_ledger_restart_required_for_deployable_evidence": restart_required,
    }
    reasons: list[str] = []
    if restart_required or (status and status != "PASS"):
        reasons.append("shadow_ledger_schema_restart_required")
    return reasons, values


def post_restart_collection_reasons(row: pd.Series | None) -> tuple[list[str], dict[str, Any]]:
    if row is None:
        return ["post_restart_collection_gate_missing"], {}
    status = as_text(row.get("gate_status", ""))
    reasons = reason_tokens(row.get("failure_reasons", ""))
    values = {
        "post_restart_collection_gate_status": status,
        "post_restart_collection_ready": to_bool(row.get("promotion_collection_ready", False)),
        "post_restart_official_rows": to_float(row, "post_restart_official_rows"),
        "post_restart_min_official_rows": to_float(row, "min_post_restart_official_rows"),
        "post_restart_official_pnl": to_float(row, "official_pnl"),
        "post_restart_proxy_official_mismatches": to_float(row, "proxy_official_mismatches"),
        "post_restart_realism_complete_rows": to_float(row, "realism_complete_rows"),
    }
    if status != "PASS_POST_RESTART_COLLECTION_GATE_NOT_DEPLOYMENT":
        reasons.append("post_restart_collection_gate_not_ready")
    return reasons, values


def row_reconciliation_reasons(row: pd.Series | None) -> tuple[list[str], dict[str, Any]]:
    if row is None:
        return ["paper_replay_row_reconciliation_missing"], {}
    status = as_text(row.get("reconciliation_status", ""))
    blockers = as_text(row.get("blocking_reasons", ""))
    row_pass = to_bool(row.get("row_reconciliation_pass", False))
    promotion_usable = to_bool(row.get("promotion_usable", False))
    values = {
        "row_reconciliation_status": status,
        "row_reconciliation_pass": row_pass,
        "row_reconciliation_promotion_usable": promotion_usable,
        "row_reconciliation_matched_rows": to_float(row, "matched_rows"),
        "row_reconciliation_paper_rows": to_float(row, "paper_rows"),
        "row_reconciliation_replay_rows": to_float(row, "replay_rows"),
        "row_reconciliation_official_rows": to_float(row, "official_rows"),
        "row_reconciliation_blockers": blockers,
    }
    reasons: list[str] = []
    if not row_pass:
        reasons.append("paper_replay_row_reconciliation_not_passing")
    if not promotion_usable:
        reasons.append("paper_replay_row_reconciliation_not_promotion_usable")
    if blockers:
        reasons.extend(f"row_reconciliation_{token}" for token in reason_tokens(blockers))
    return reasons, values


def frozen_policy_reasons(row: pd.Series | None) -> tuple[list[str], dict[str, Any]]:
    if row is None:
        return ["frozen_policy_parity_missing"], {}
    status = as_text(row.get("policy_parity_status", ""))
    blockers = as_text(row.get("policy_parity_blockers", ""))
    passed = to_bool(row.get("policy_parity_pass", False))
    values = {
        "frozen_policy_parity_status": status,
        "frozen_policy_parity_pass": passed,
        "frozen_policy_parity_blockers": blockers,
    }
    reasons: list[str] = []
    if not passed:
        reasons.append("frozen_policy_parity_not_passing")
    if blockers:
        reasons.extend(f"frozen_policy_{token}" for token in reason_tokens(blockers))
    return reasons, values


def policy_epoch_reasons(
    policy_df: pd.DataFrame,
    ledger: str,
    *,
    expected_ttl_policy: str,
    expected_policy_version: str,
    min_official_rows: int,
) -> tuple[list[str], dict[str, Any]]:
    """Require forward official rows to belong to the expected model-policy epoch."""
    if policy_df.empty:
        return ["shadow_official_policy_summary_missing"], {}
    required = {"ledger", "scope", "model_ttl_policy", "model_policy_version", "official_filled_rows"}
    if not required.issubset(set(policy_df.columns)):
        missing = sorted(required - set(policy_df.columns))
        return ["shadow_official_policy_summary_missing_fields"], {
            "shadow_official_policy_missing_fields": ";".join(missing),
        }

    ledger_rows = policy_df[policy_df["ledger"].astype(str).eq(ledger)].copy()
    if ledger_rows.empty:
        return ["shadow_official_policy_ledger_missing"], {
            "shadow_official_policy_status": "MISSING_LEDGER",
        }

    scoped = ledger_rows[ledger_rows["scope"].astype(str).str.lower().eq("since")].copy()
    if scoped.empty:
        scoped = ledger_rows

    expected = scoped[
        scoped["model_ttl_policy"].fillna("").astype(str).eq(expected_ttl_policy)
        & scoped["model_policy_version"].fillna("").astype(str).eq(expected_policy_version)
    ].copy()
    blank_rows = scoped[
        scoped["model_ttl_policy"].fillna("").astype(str).str.strip().eq("")
        | scoped["model_policy_version"].fillna("").astype(str).str.strip().eq("")
    ].copy()

    reasons: list[str] = []
    values: dict[str, Any] = {
        "shadow_official_expected_model_ttl_policy": expected_ttl_policy,
        "shadow_official_expected_model_policy_version": expected_policy_version,
        "shadow_official_policy_blank_rows": float(
            pd.to_numeric(blank_rows.get("official_filled_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
        ),
    }
    if expected.empty:
        values.update(
            {
                "shadow_official_policy_status": "EXPECTED_POLICY_MISSING",
                "shadow_official_policy_official_rows": 0.0,
                "shadow_official_policy_official_pnl": 0.0,
                "shadow_official_policy_proxy_mismatches": 0.0,
            }
        )
        reasons.append("shadow_official_expected_policy_missing")
    else:
        official_rows = float(pd.to_numeric(expected["official_filled_rows"], errors="coerce").fillna(0).sum())
        official_pnl = float(pd.to_numeric(expected.get("official_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        mismatches = float(
            pd.to_numeric(expected.get("official_proxy_result_mismatches", pd.Series(dtype=float)), errors="coerce")
            .fillna(0)
            .sum()
        )
        values.update(
            {
                "shadow_official_policy_status": "EXPECTED_POLICY_PRESENT",
                "shadow_official_policy_official_rows": official_rows,
                "shadow_official_policy_official_pnl": official_pnl,
                "shadow_official_policy_proxy_mismatches": mismatches,
            }
        )
        if official_rows < min_official_rows:
            reasons.append("shadow_official_expected_policy_too_few_official_rows")
        if official_pnl <= 0:
            reasons.append("shadow_official_expected_policy_pnl_not_positive")
        if mismatches > 0:
            reasons.append("shadow_official_expected_policy_proxy_official_mismatch")
    if values["shadow_official_policy_blank_rows"] > 0:
        reasons.append("shadow_official_blank_policy_rows_present")
    return reasons, values


def source_freshness_reasons(row: pd.Series | None) -> tuple[list[str], dict[str, Any]]:
    if row is None:
        return ["shadow_source_freshness_status_missing"], {}
    status = as_text(row.get("source_freshness_status", ""))
    running = to_bool(row.get("running", False))
    process_predates_latest_source = to_bool(row.get("process_predates_latest_source", False))
    duplicate_process_count = int(to_float(row, "duplicate_process_count"))
    process_hygiene_status = as_text(row.get("process_hygiene_status", ""))
    values = {
        "shadow_source_freshness_status": status,
        "shadow_source_latest_path": as_text(row.get("source_latest_path", "")),
        "shadow_source_latest_mtime_utc": as_text(row.get("source_latest_mtime_utc", "")),
        "shadow_process_created_at_utc": as_text(row.get("process_created_at_utc", "")),
        "shadow_process_predates_latest_source": process_predates_latest_source,
        "shadow_process_count": int(to_float(row, "process_count")),
        "shadow_duplicate_process_count": duplicate_process_count,
        "shadow_process_hygiene_status": process_hygiene_status,
    }
    reasons: list[str] = []
    if duplicate_process_count > 0 or process_hygiene_status == "DUPLICATE_TARGET_PROCESSES":
        reasons.append("target_duplicate_processes_running")
        reasons.append("process_hygiene_duplicate_target_processes")
    if status == "RUNNING_SOURCE_STALE_RESTART_REQUIRED" or process_predates_latest_source:
        reasons.append("running_source_stale_restart_required")
        reasons.append("current_process_predates_latest_source")
    elif not running or status in {"", "NOT_RUNNING_OR_PROCESS_TIME_MISSING"}:
        reasons.append("shadow_source_freshness_not_running_or_unknown")
    elif status != "RUNNING_SOURCE_CURRENT":
        reasons.append("shadow_source_freshness_not_current")
    return reasons, values


def process_hygiene_global_reasons(info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    if not info:
        return [], {}
    duplicate_count = to_int_value(info.get("duplicate_target_process_count", 0))
    unmanaged_count = to_int_value(info.get("unmanaged_matching_process_count", 0))
    duplicate_names = as_text(info.get("duplicate_target_names", ""))
    values = {
        "process_hygiene_duplicate_target_process_count": duplicate_count,
        "process_hygiene_unmanaged_matching_process_count": unmanaged_count,
        "process_hygiene_duplicate_target_names": duplicate_names,
    }
    reasons: list[str] = []
    if duplicate_count > 0:
        reasons.append("process_hygiene_duplicate_target_processes")
    if unmanaged_count > 0:
        reasons.append("process_hygiene_unmanaged_matching_processes")
    return reasons, values


def basis_specs_for_row(row: pd.Series) -> list[tuple[str, str | None]]:
    """Map readiness rows to settlement-basis gate rows.

    A side of None means the candidate needs all available side rows for that
    basis candidate to pass. This is conservative for BOTH-side strategies and
    for shadow-ledger rows whose summary aggregates both sides.
    """
    candidate = as_text(row.get("candidate", ""))
    source = as_text(row.get("source", ""))

    if candidate.startswith("btc15m_q250_qty500_firstskip_shadow:"):
        return [("btc15m_q250_qty500_firstskip_shadow", None)]
    if candidate.startswith("btc15m_q250_qty500_firstskip_yes_shadow:"):
        return [("btc15m_q250_qty500_firstskip_yes_shadow", None)]
    if candidate.startswith("btc15m_q1000_yes_shadow:"):
        return [("btc15m_q1000_yes_shadow", None)]
    if candidate.startswith("btc1h_high_conf80_entry70_no_chase_shadow:"):
        return [("btc1h_high_conf80_entry70_no_chase_shadow", None)]

    exact: dict[str, tuple[str, str | None]] = {
        "q250": ("q250", None),
        "q500": ("q500", None),
        "q1000": ("q1000", None),
        "q250_qspeed05": ("q250_qspeed05", None),
        "q250_both": ("q250", None),
        "q500_both": ("q500", None),
        "q1000_both": ("q1000", None),
        "q250_qspeed05_both": ("q250_qspeed05", None),
        "q250_yes": ("q250", "yes"),
        "q500_yes": ("q500", "yes"),
        "q1000_yes": ("q1000_yes", "yes"),
        "q250_firstskip_qty500": ("q250_firstskip_qty500", None),
        "q250_firstskip_qty500_yes": ("q250_firstskip_qty500", "yes"),
        "high_conf_80_entry70_no_chase": ("btc1h_high_conf80_entry70_no_chase_shadow", None),
        "btc1h_high_conf80_entry70_no_chase": ("btc1h_high_conf80_entry70_no_chase_shadow", None),
    }
    if candidate in exact:
        return [exact[candidate]]

    # Materialized-grid rows are emitted as e.g. q250:mat_grid_00019.
    if source == "materialized_first_signal_grid" and ":" in candidate:
        base = candidate.split(":", 1)[0]
        if base in {"q250", "q500", "q1000", "q250_qspeed05"}:
            return [(base, None)]

    return []


def settlement_basis_reasons(
    row: pd.Series,
    basis_gates: pd.DataFrame,
) -> tuple[list[str], dict[str, Any]]:
    specs = basis_specs_for_row(row)
    missing_reason = "settlement_basis_risk_audit_missing" if basis_gates.empty else "settlement_basis_candidate_missing"
    if not specs:
        return (
            ["settlement_basis_mapping_missing"],
            {
                "settlement_basis_gate_status": "MISSING",
                "settlement_basis_gate_pass": False,
                "settlement_basis_gate_reasons": "settlement_basis_mapping_missing",
            },
        )
    if basis_gates.empty or "candidate" not in basis_gates.columns:
        return (
            [missing_reason],
            {
                "settlement_basis_gate_status": "MISSING",
                "settlement_basis_gate_pass": False,
                "settlement_basis_gate_reasons": missing_reason,
            },
        )

    matched: list[pd.DataFrame] = []
    missing: list[str] = []
    for basis_candidate, side in specs:
        work = basis_gates[basis_gates["candidate"].astype(str).eq(basis_candidate)].copy()
        if side is not None and "side" in work.columns:
            work = work[work["side"].astype(str).str.lower().eq(side.lower())]
        if work.empty:
            missing.append(f"{basis_candidate}:{side or 'all'}")
        else:
            matched.append(work)

    if not matched:
        return (
            ["settlement_basis_candidate_missing"],
            {
                "settlement_basis_gate_status": "MISSING",
                "settlement_basis_gate_pass": False,
                "settlement_basis_gate_reasons": "settlement_basis_candidate_missing",
                "settlement_basis_missing_specs": ";".join(missing),
            },
        )

    work = pd.concat(matched, ignore_index=True)
    pass_all = bool(work["basis_gate_pass"].map(to_bool).all()) if "basis_gate_pass" in work.columns else False
    gate_reasons: list[str] = []
    for text in work.get("basis_gate_reasons", pd.Series(dtype=str)).fillna("").astype(str):
        gate_reasons.extend(reason_tokens(text))
    gate_reasons = sorted(set(gate_reasons))

    reasons: list[str] = []
    if missing:
        reasons.append("settlement_basis_candidate_missing")
    if not pass_all:
        reasons.append("settlement_basis_gate_failed")
    reasons.extend(f"settlement_basis_{token}" for token in gate_reasons)

    numeric = lambda col: pd.to_numeric(work.get(col, pd.Series(dtype=float)), errors="coerce")
    sides = sorted(set(work.get("side", pd.Series(dtype=str)).fillna("").astype(str)))
    values = {
        "settlement_basis_gate_status": "PASS" if pass_all and not missing else "FAIL",
        "settlement_basis_gate_pass": pass_all and not missing,
        "settlement_basis_gate_reasons": ";".join(gate_reasons),
        "settlement_basis_gate_sides": ",".join(side for side in sides if side),
        "settlement_basis_official_rows": float(numeric("official_rows").fillna(0).sum()),
        "settlement_basis_promotion_min_official_rows": float(numeric("promotion_min_official_rows").fillna(0).max()),
        "settlement_basis_mismatch_rate": float(numeric("mismatch_rate").fillna(0).max()),
        "settlement_basis_adverse_mismatches": float(numeric("adverse_mismatches").fillna(0).sum()),
        "settlement_basis_pnl_delta_per_trade_2c": float(numeric("pnl_delta_per_both_trade_2c").dropna().min())
        if not numeric("pnl_delta_per_both_trade_2c").dropna().empty
        else 0.0,
        "settlement_basis_abs_basis_p95_usd": float(numeric("abs_basis_p95_usd").fillna(0).max()),
        "settlement_basis_max_abs_basis_usd": float(numeric("max_abs_basis_usd").fillna(0).max()),
    }
    if missing:
        values["settlement_basis_missing_specs"] = ";".join(missing)
    return reasons, values


def apply_settlement_basis_gates(summary: pd.DataFrame, basis_gates: pd.DataFrame | None) -> pd.DataFrame:
    """Fold official/proxy settlement-basis gates into readiness rows."""
    if summary.empty or basis_gates is None:
        return summary
    value_columns = [
        "settlement_basis_gate_status",
        "settlement_basis_gate_pass",
        "settlement_basis_gate_reasons",
        "settlement_basis_gate_sides",
        "settlement_basis_official_rows",
        "settlement_basis_promotion_min_official_rows",
        "settlement_basis_mismatch_rate",
        "settlement_basis_adverse_mismatches",
        "settlement_basis_pnl_delta_per_trade_2c",
        "settlement_basis_abs_basis_p95_usd",
        "settlement_basis_max_abs_basis_usd",
        "settlement_basis_missing_specs",
    ]
    for col in value_columns:
        if col not in summary.columns:
            summary[col] = ""

    for idx, row in summary.iterrows():
        if as_text(row.get("source", "")) == "lowdd_forward_promotion_gate":
            summary.at[idx, "settlement_basis_gate_status"] = "NOT_APPLICABLE_LOWDD_OFFICIAL_PAPER_GATE"
            summary.at[idx, "settlement_basis_gate_pass"] = ""
            summary.at[idx, "settlement_basis_gate_reasons"] = ""
            continue
        reasons, values = settlement_basis_reasons(row, basis_gates)
        if reasons:
            summary.at[idx, "failure_reasons"] = add_reason_tokens(summary.at[idx, "failure_reasons"], reasons)
            summary.at[idx, "production_ready"] = False
        for col, value in values.items():
            summary.at[idx, col] = value
    return summary


def btc1h_clean_evidence_clock_reasons(
    df: pd.DataFrame,
    *,
    min_official_rows: int,
) -> tuple[list[str], dict[str, Any]]:
    if df.empty:
        return ["btc1h_clean_evidence_clock_gate_missing"], {}
    row = df.iloc[0]
    ready = to_bool(row.get("clean_evidence_clock_ready", False))
    gate_status = as_text(row.get("gate_status", ""))
    values = {
        "btc1h_clean_evidence_clock_status": gate_status,
        "btc1h_clean_evidence_clock_ready": ready,
        "btc1h_clean_evidence_clock_next_action": as_text(row.get("next_action", "")),
        "btc1h_clean_evidence_clock_blocker_count": to_float(row, "blocker_count"),
        "btc1h_clean_clock_sidecar_signal_missing_fields": as_text(row.get("sidecar_signal_missing_fields", "")),
        "btc1h_clean_clock_sidecar_order_missing_fields": as_text(
            row.get("sidecar_order_decision_missing_fields", "")
        ),
        "btc1h_clean_clock_expected_policy_official_rows": to_float(row, "expected_policy_official_rows"),
        "btc1h_clean_clock_blank_policy_official_rows": to_float(row, "blank_policy_official_rows"),
        "btc1h_clean_clock_official_rows": to_float(row, "official_rows"),
        "btc1h_clean_clock_official_pnl": to_float(row, "official_pnl"),
        "btc1h_clean_clock_proxy_official_mismatches": to_float(row, "official_proxy_mismatches"),
        "btc1h_clean_clock_captured_ttl_available_rows": to_float(row, "captured_ttl_available_rows"),
        "btc1h_clean_clock_selected_signal_rows": to_float(row, "selected_signal_rows"),
        "btc1h_clean_clock_replay_ledger_promotion_usable": to_bool(row.get("replay_ledger_promotion_usable", False)),
    }
    reasons: list[str] = []
    if not ready:
        reasons.append("btc1h_clean_evidence_clock_not_ready")
    if gate_status == "BLOCKED_CONTROLLED_RESTART_REQUIRED":
        reasons.append("btc1h_controlled_restart_required_for_clean_evidence_clock")
    if values["btc1h_clean_clock_sidecar_signal_missing_fields"]:
        reasons.append("btc1h_clean_clock_sidecar_model_inputs_missing")
    if values["btc1h_clean_clock_sidecar_order_missing_fields"]:
        reasons.append("btc1h_clean_clock_sidecar_order_policy_fields_missing")
    if values["btc1h_clean_clock_expected_policy_official_rows"] <= 0:
        reasons.append("btc1h_clean_clock_expected_policy_rows_missing")
    if values["btc1h_clean_clock_blank_policy_official_rows"] > 0:
        reasons.append("btc1h_clean_clock_blank_policy_rows_present")
    if values["btc1h_clean_clock_captured_ttl_available_rows"] < values["btc1h_clean_clock_selected_signal_rows"]:
        reasons.append("btc1h_clean_clock_captured_ttl_missing")
    if not values["btc1h_clean_clock_replay_ledger_promotion_usable"]:
        reasons.append("btc1h_clean_clock_replay_not_promotion_usable")
    if values["btc1h_clean_clock_official_rows"] < min_official_rows:
        reasons.append("btc1h_clean_clock_too_few_official_rows")
    if values["btc1h_clean_clock_official_pnl"] <= 0:
        reasons.append("btc1h_clean_clock_official_pnl_not_positive")
    if values["btc1h_clean_clock_proxy_official_mismatches"] > 0:
        reasons.append("btc1h_clean_clock_proxy_official_mismatch_present")
    return reasons, values


def apply_btc1h_clean_evidence_clock_gate(
    summary: pd.DataFrame,
    clean_clock_df: pd.DataFrame,
    *,
    min_official_rows: int,
) -> pd.DataFrame:
    if summary.empty:
        return summary
    reasons, values = btc1h_clean_evidence_clock_reasons(clean_clock_df, min_official_rows=min_official_rows)
    mask = candidate_mask(summary, exact="high_conf_80_entry70_no_chase") | candidate_mask(
        summary, prefix="btc1h_high_conf80_entry70_no_chase_shadow:"
    )
    apply_reason_blockers(summary, mask, reasons, values)
    return summary


def btc1h_basis_mismatch_reasons(
    df: pd.DataFrame,
    *,
    min_official_rows: int,
) -> tuple[list[str], dict[str, Any]]:
    if df.empty:
        return ["btc1h_basis_mismatch_audit_missing"], {}
    row = df.iloc[0]
    audit_status = as_text(row.get("audit_status", ""))
    deployable_guard = to_bool(row.get("deployable_guard_now", False))
    values = {
        "btc1h_basis_audit_status": audit_status,
        "btc1h_basis_deployable_guard_now": deployable_guard,
        "btc1h_basis_guard_recommendation": as_text(row.get("guard_recommendation", "")),
        "btc1h_basis_official_rows": to_float(row, "official_rows"),
        "btc1h_basis_official_pnl": to_float(row, "official_pnl"),
        "btc1h_basis_proxy_pnl": to_float(row, "proxy_pnl"),
        "btc1h_basis_official_minus_proxy_pnl": to_float(row, "official_minus_proxy_pnl"),
        "btc1h_basis_official_proxy_mismatches": to_float(row, "official_proxy_mismatches"),
        "btc1h_basis_official_proxy_mismatch_rate": to_float(row, "official_proxy_mismatch_rate"),
        "btc1h_basis_proxy_win_official_loss_flips": to_float(row, "proxy_win_official_loss_flips"),
        "btc1h_basis_near_proxy_boundary_rows": to_float(row, "near_proxy_boundary_rows"),
        "btc1h_basis_near_official_boundary_rows": to_float(row, "near_official_boundary_rows"),
        "btc1h_basis_p95_abs_basis_usd": to_float(row, "p95_abs_official_minus_proxy_spot"),
        "btc1h_basis_max_abs_basis_usd": to_float(row, "max_abs_official_minus_proxy_spot"),
        "btc1h_basis_max_adverse_basis_usd": to_float(row, "max_adverse_basis_usd"),
    }
    reasons: list[str] = []
    if values["btc1h_basis_official_rows"] < min_official_rows:
        reasons.append("btc1h_basis_too_few_official_rows")
    if values["btc1h_basis_official_proxy_mismatches"] > 0:
        reasons.append("btc1h_basis_proxy_official_mismatch_present")
    if values["btc1h_basis_proxy_win_official_loss_flips"] > 0:
        reasons.append("btc1h_basis_proxy_win_official_loss_flip")
    if not deployable_guard:
        reasons.append("btc1h_basis_guard_not_deployable")
    if "TOO_FEW_OFFICIAL_ROWS" in audit_status:
        reasons.append("btc1h_basis_audit_too_few_official_rows")
    return reasons, values


def apply_btc1h_basis_mismatch_audit(
    summary: pd.DataFrame,
    basis_df: pd.DataFrame,
    *,
    min_official_rows: int,
) -> pd.DataFrame:
    if summary.empty:
        return summary
    reasons, values = btc1h_basis_mismatch_reasons(basis_df, min_official_rows=min_official_rows)
    mask = candidate_mask(summary, exact="high_conf_80_entry70_no_chase") | candidate_mask(
        summary, prefix="btc1h_high_conf80_entry70_no_chase_shadow:"
    )
    apply_reason_blockers(summary, mask, reasons, values)
    return summary


def checklist_passes(df: pd.DataFrame, check: str, column: str = "passes_for_evidence_clock") -> bool:
    row = first_row(df, "check", check)
    if row is None:
        return False
    return to_bool(row.get(column, False))


def post_restart_verification_reasons(
    info: dict[str, Any],
    checklist_df: pd.DataFrame,
    target_df: pd.DataFrame,
    *,
    candidate: str | None = None,
    ledger: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Return blockers from the stricter post-restart evidence-clock verifier."""
    if not info:
        return ["post_restart_verification_missing"], {}

    restart_executed = to_bool(info.get("restart_executed", False))
    evidence_clock_ready = to_bool(info.get("evidence_clock_ready", False))
    collection_gate_ready = to_bool(info.get("collection_gate_ready", False))
    values: dict[str, Any] = {
        "post_restart_verification_restart_executed": restart_executed,
        "post_restart_verification_evidence_clock_ready": evidence_clock_ready,
        "post_restart_verification_collection_gate_ready": collection_gate_ready,
    }
    reasons: list[str] = []
    if not restart_executed:
        reasons.append("post_restart_controlled_restart_not_executed")
    if not evidence_clock_ready:
        reasons.append("post_restart_evidence_clock_not_ready")
    if not checklist_passes(checklist_df, "target_process_identity"):
        reasons.append("post_restart_process_identity_not_ready")
    if not checklist_passes(checklist_df, "required_capture_sidecars_ready"):
        reasons.append("post_restart_capture_sidecars_not_ready")
    if not checklist_passes(checklist_df, "all_target_shadows_running"):
        reasons.append("post_restart_target_shadows_not_running")
    if not checklist_passes(checklist_df, "active_ledger_schemas_ready"):
        reasons.append("post_restart_active_ledger_schemas_not_ready")

    target_row: pd.Series | None = None
    if ledger:
        target_row = first_row(target_df, "ledger", ledger)
    if target_row is None and candidate:
        target_row = first_row(target_df, "candidate", candidate)
    if target_row is not None:
        target_values = {
            "post_restart_target_evidence_clock_status": as_text(target_row.get("evidence_clock_status", "")),
            "post_restart_target_running": to_bool(target_row.get("running", False)),
            "post_restart_target_process_identity_ready": to_bool(target_row.get("process_identity_ready", False)),
            "post_restart_target_process_identity_status": as_text(target_row.get("process_identity_status", "")),
            "post_restart_target_capture_sidecar_ready": to_bool(target_row.get("capture_sidecar_ready", False)),
            "post_restart_target_capture_sidecar_status": as_text(target_row.get("capture_sidecar_status", "")),
            "post_restart_target_active_schema_status": as_text(target_row.get("active_schema_status", "")),
        }
        values.update(target_values)
        if not target_values["post_restart_target_running"]:
            reasons.append("post_restart_target_not_running")
        if not target_values["post_restart_target_process_identity_ready"]:
            reasons.append("post_restart_target_process_identity_not_ready")
        if not target_values["post_restart_target_capture_sidecar_ready"]:
            reasons.append("post_restart_target_capture_sidecar_not_ready")
        if as_text(target_row.get("evidence_clock_status", "")) not in {
            "EVIDENCE_CLOCK_STARTED_WAIT_FOR_OFFICIAL_ROWS",
        }:
            reasons.append("post_restart_target_evidence_clock_not_ready")
    return reasons, values


def apply_execution_and_schema_gates(
    summary: pd.DataFrame,
    execution_df: pd.DataFrame,
    schema_df: pd.DataFrame,
    post_restart_df: pd.DataFrame,
    row_reconciliation_df: pd.DataFrame,
    shadow_status_df: pd.DataFrame | None = None,
    frozen_policy_df: pd.DataFrame | None = None,
    basis_gates_df: pd.DataFrame | None = None,
    shadow_official_policy_df: pd.DataFrame | None = None,
    post_restart_verification_info: dict[str, Any] | None = None,
    post_restart_verification_checklist_df: pd.DataFrame | None = None,
    post_restart_target_df: pd.DataFrame | None = None,
    shadow_status_info: dict[str, Any] | None = None,
    btc1h_expected_ttl_policy: str = "",
    btc1h_expected_policy_version: str = "",
    min_live_official_trades: int = 30,
) -> pd.DataFrame:
    """Fold execution realism and live ledger schema audits into readiness rows.

    These checks are intentionally conservative. A profitable live-replay row
    cannot become production-ready unless the matching paper ledger also has
    deployable execution fields and a current schema.
    """
    if summary.empty:
        return summary
    post_restart_verification_info = post_restart_verification_info or {}
    if shadow_status_df is None:
        shadow_status_df = pd.DataFrame()
    if frozen_policy_df is None:
        frozen_policy_df = pd.DataFrame()
    if shadow_official_policy_df is None:
        shadow_official_policy_df = pd.DataFrame()
    if post_restart_verification_checklist_df is None:
        post_restart_verification_checklist_df = pd.DataFrame()
    if post_restart_target_df is None:
        post_restart_target_df = pd.DataFrame()
    shadow_status_info = shadow_status_info or {}

    global_restart_reasons, global_restart_values = post_restart_verification_reasons(
        post_restart_verification_info,
        post_restart_verification_checklist_df,
        post_restart_target_df,
    )
    if global_restart_reasons:
        global_restart_mask = summary["source"].astype(str).ne("lowdd_forward_promotion_gate")
        apply_reason_blockers(
            summary,
            global_restart_mask,
            global_restart_reasons,
            global_restart_values,
        )
    global_process_reasons, global_process_values = process_hygiene_global_reasons(shadow_status_info)
    if global_process_reasons:
        apply_reason_blockers(
            summary,
            pd.Series(True, index=summary.index),
            global_process_reasons,
            global_process_values,
        )
    if frozen_policy_df.empty:
        frozen_policy_mask = summary["source"].astype(str).ne("lowdd_forward_promotion_gate")
        apply_reason_blockers(
            summary,
            frozen_policy_mask,
            ["frozen_policy_parity_audit_missing"],
            {},
        )

    replay_specs = [
        ("q250_firstskip_qty500", "q250_firstskip_qty500_live_replay"),
        ("q250_firstskip_qty500_yes", "q250_firstskip_qty500_yes_live_replay"),
        ("q1000_yes", "q1000_yes_live_replay"),
    ]
    for candidate, source in replay_specs:
        row = first_row(execution_df, "source", source)
        reasons, values = execution_reasons(row, "replay")
        mask = candidate_mask(summary, exact=candidate) & summary["source"].astype(str).eq("latest_live_replay_rest_official")
        apply_reason_blockers(summary, mask, reasons, values)

    ledger_specs = [
        (
            "btc15m_q250_qty500_firstskip_shadow",
            [
                candidate_mask(summary, exact="q250_firstskip_qty500"),
                candidate_mask(summary, prefix="btc15m_q250_qty500_firstskip_shadow:"),
            ],
        ),
        (
            "btc15m_q250_qty500_firstskip_yes_shadow",
            [
                candidate_mask(summary, exact="q250_firstskip_qty500_yes"),
                candidate_mask(summary, prefix="btc15m_q250_qty500_firstskip_yes_shadow:"),
            ],
        ),
        (
            "btc15m_q1000_yes_shadow",
            [
                candidate_mask(summary, exact="q1000_yes"),
                candidate_mask(summary, prefix="btc15m_q1000_yes_shadow:"),
            ],
        ),
        (
            "btc1h_high_conf80_entry70_no_chase_shadow",
            [
                candidate_mask(summary, exact="high_conf_80_entry70_no_chase"),
                candidate_mask(summary, prefix="btc1h_high_conf80_entry70_no_chase_shadow:"),
            ],
        ),
    ]
    for ledger, masks in ledger_specs:
        ledger_execution = first_row(execution_df, "source", ledger)
        exec_reasons, exec_values = execution_reasons(ledger_execution, "shadow_ledger")
        schema = first_row(schema_df, "ledger", ledger)
        schema_blockers, schema_values = schema_reasons(schema)
        post_restart = first_row(post_restart_df, "ledger", ledger)
        post_restart_blockers, post_restart_values = post_restart_collection_reasons(post_restart)
        frozen_policy = first_row(frozen_policy_df, "ledger", ledger)
        frozen_policy_blockers, frozen_policy_values = frozen_policy_reasons(frozen_policy)
        source_freshness = first_row(shadow_status_df, "name", ledger)
        source_freshness_blockers, source_freshness_values = source_freshness_reasons(source_freshness)
        verifier_blockers, verifier_values = post_restart_verification_reasons(
            post_restart_verification_info,
            post_restart_verification_checklist_df,
            post_restart_target_df,
            ledger=ledger,
        )
        policy_blockers: list[str] = []
        policy_values: dict[str, Any] = {}
        if ledger == "btc1h_high_conf80_entry70_no_chase_shadow":
            policy_blockers, policy_values = policy_epoch_reasons(
                shadow_official_policy_df,
                ledger,
                expected_ttl_policy=btc1h_expected_ttl_policy,
                expected_policy_version=btc1h_expected_policy_version,
                min_official_rows=min_live_official_trades,
            )
        mask = masks[0].copy()
        for next_mask in masks[1:]:
            mask |= next_mask
        apply_reason_blockers(
            summary,
            mask,
            exec_reasons
            + schema_blockers
            + post_restart_blockers
            + frozen_policy_blockers
            + source_freshness_blockers
            + verifier_blockers
            + policy_blockers,
            exec_values
            | schema_values
            | post_restart_values
            | frozen_policy_values
            | source_freshness_values
            | verifier_values
            | policy_values,
        )

    reconciliation_specs = [
        (
            "q250_firstskip_qty500",
            [
                candidate_mask(summary, exact="q250_firstskip_qty500"),
                candidate_mask(summary, prefix="btc15m_q250_qty500_firstskip_shadow:"),
            ],
        ),
        (
            "q250_firstskip_qty500_yes",
            [
                candidate_mask(summary, exact="q250_firstskip_qty500_yes"),
                candidate_mask(summary, prefix="btc15m_q250_qty500_firstskip_yes_shadow:"),
            ],
        ),
        (
            "q1000_yes",
            [
                candidate_mask(summary, exact="q1000_yes"),
                candidate_mask(summary, prefix="btc15m_q1000_yes_shadow:"),
            ],
        ),
        (
            "btc1h_high_conf80_entry70_no_chase",
            [
                candidate_mask(summary, exact="high_conf_80_entry70_no_chase"),
                candidate_mask(summary, prefix="btc1h_high_conf80_entry70_no_chase_shadow:"),
            ],
        ),
    ]
    for candidate, masks in reconciliation_specs:
        recon = first_row(row_reconciliation_df, "candidate", candidate)
        recon_reasons, recon_values = row_reconciliation_reasons(recon)
        mask = masks[0].copy()
        for next_mask in masks[1:]:
            mask |= next_mask
        apply_reason_blockers(summary, mask, recon_reasons, recon_values)

    summary = apply_settlement_basis_gates(summary, basis_gates_df)
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Summarize BTC deployment readiness gates.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-pred-trades", type=int, default=50)
    p.add_argument("--min-live-proxy-trades", type=int, default=50)
    p.add_argument("--min-live-official-trades", type=int, default=30)
    p.add_argument("--btc1h-expected-model-ttl-policy", default="scan_time_close_minus_now_v1")
    p.add_argument("--btc1h-expected-model-policy-version", default="btc1h_live_model_20260522_scan_ttl_v1")
    p.add_argument("--credentials-env", type=Path, default=PROJECT_ROOT / "credentials.env")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    broad_dir = latest_dir("btc15m_f2_deployment_gate_refresh_")
    side_dir = latest_dir("btc15m_f2_side_gate_rest_official_refresh_")
    materialized_dir = latest_materialized_grid_dir()
    rest_dir = latest_dir("btc15m_live_ws_rest_official_refresh_")
    latest_live_replay_dirs = latest_dirs(
        [
            "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_",
            "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_",
            "btc15m_f2_live_ws_q1000_yes_causal_rest_official_",
        ]
    )
    btc1h_dir = latest_dir("btc1h_promotion_gate_audit_")
    btc1h_multi_holdout_dir = latest_btc1h_multi_holdout_dir()
    btc1h_replay_reconciliation_dir = latest_btc1h_replay_reconciliation_dir()
    btc1h_clean_evidence_clock_dir = latest_btc1h_clean_evidence_clock_dir()
    btc1h_basis_mismatch_dir = latest_btc1h_basis_mismatch_dir()
    shadow_official_dir = latest_shadow_official_dir()
    execution_realism_dir = latest_dir("btc_execution_realism_audit_")
    ledger_schema_dir = latest_dir("btc_ledger_schema_preflight_")
    post_restart_dir = latest_dir("btc_post_restart_collection_gate_")
    post_restart_verification_dir = latest_dir("btc_post_restart_verification_")
    row_reconciliation_dir = latest_dir("btc_forward_row_reconciliation_")
    shadow_status_dir = latest_dir("btc_forward_shadow_status_")
    shadow_status_info = read_json(shadow_status_dir / "run_info.json" if shadow_status_dir else None)
    frozen_policy_dir = latest_dir("btc_frozen_policy_parity_")
    basis_risk_dir = latest_dir("btc_settlement_basis_risk_audit_")
    lowdd_forward_gate_dir = latest_dir("btc15m_lowdd_forward_promotion_gate_")

    rows: list[dict[str, Any]] = []
    add_btc15m_broad(
        rows,
        read_csv(broad_dir / "gate_summary.csv" if broad_dir else None),
        read_csv(rest_dir / "summary.csv" if rest_dir else None),
        args.min_live_proxy_trades,
        args.min_live_official_trades,
    )
    add_btc15m_side(
        rows,
        read_csv(side_dir / "side_gate_summary.csv" if side_dir else None),
        args.min_pred_trades,
        args.min_live_proxy_trades,
        args.min_live_official_trades,
    )
    add_btc15m_materialized(
        rows,
        read_csv(materialized_dir / "materialized_filter_summary.csv" if materialized_dir else None),
        args.min_pred_trades,
        args.min_live_proxy_trades,
        args.min_live_official_trades,
    )
    add_btc15m_latest_live_replay(rows, latest_live_replay_dirs, args.min_live_official_trades)
    add_btc15m_lowdd_forward_gate(rows, lowdd_forward_gate_dir)
    add_btc1h(rows, btc1h_dir)
    add_btc1h_multi_holdout(
        rows,
        read_csv(btc1h_multi_holdout_dir / "btc1h_candidate_gate_summary.csv" if btc1h_multi_holdout_dir else None),
        args.min_live_official_trades,
    )
    add_shadow_official(
        rows,
        read_csv(shadow_official_dir / "shadow_official_summary.csv" if shadow_official_dir else None),
        args.min_live_official_trades,
    )

    summary = pd.DataFrame(rows)
    if summary.empty:
        summary = pd.DataFrame(
            [
                {
                    "family": "ALL",
                    "candidate": "(none)",
                    "source": "all_gates",
                    "production_ready": False,
                    "failure_reasons": "no_gate_artifacts_found",
                }
            ]
        )
    summary["production_ready"] = summary["production_ready"].astype(bool)
    summary = apply_execution_and_schema_gates(
        summary,
        read_csv(execution_realism_dir / "execution_realism_summary.csv" if execution_realism_dir else None),
        read_csv(ledger_schema_dir / "ledger_schema_preflight_summary.csv" if ledger_schema_dir else None),
        read_csv(post_restart_dir / "post_restart_collection_gate_summary.csv" if post_restart_dir else None),
        read_csv(row_reconciliation_dir / "row_reconciliation_summary.csv" if row_reconciliation_dir else None),
        read_csv(shadow_status_dir / "shadow_status.csv" if shadow_status_dir else None),
        read_csv(frozen_policy_dir / "frozen_policy_parity_summary.csv" if frozen_policy_dir else None),
        read_csv(basis_risk_dir / "settlement_basis_risk_gates.csv" if basis_risk_dir else None),
        read_csv(shadow_official_dir / "shadow_official_policy_summary.csv" if shadow_official_dir else None),
        read_json(post_restart_verification_dir / "run_info.json" if post_restart_verification_dir else None),
        read_csv(post_restart_verification_dir / "post_restart_verification_checklist.csv" if post_restart_verification_dir else None),
        read_csv(post_restart_verification_dir / "post_restart_target_status.csv" if post_restart_verification_dir else None),
        shadow_status_info,
        args.btc1h_expected_model_ttl_policy,
        args.btc1h_expected_model_policy_version,
        args.min_live_official_trades,
    )
    summary = apply_btc1h_clean_evidence_clock_gate(
        summary,
        read_csv(
            btc1h_clean_evidence_clock_dir / "btc1h_clean_evidence_clock_summary.csv"
            if btc1h_clean_evidence_clock_dir
            else None
        ),
        min_official_rows=args.min_live_official_trades,
    )
    summary = apply_btc1h_basis_mismatch_audit(
        summary,
        read_csv(
            btc1h_basis_mismatch_dir / "btc1h_basis_mismatch_summary.csv"
            if btc1h_basis_mismatch_dir
            else None
        ),
        min_official_rows=args.min_live_official_trades,
    )
    summary["production_ready"] = summary["production_ready"].astype(bool)
    summary = summary.sort_values(
        ["production_ready", "family", "source", "live_official_pnl"],
        ascending=[False, True, True, False],
        na_position="last",
    )

    cf_present = has_cf_credentials(args.credentials_env)
    out_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "broad_gate_dir": str(broad_dir.relative_to(PROJECT_ROOT)) if broad_dir else "",
        "side_gate_dir": str(side_dir.relative_to(PROJECT_ROOT)) if side_dir else "",
        "materialized_grid_dir": str(materialized_dir.relative_to(PROJECT_ROOT)) if materialized_dir else "",
        "rest_official_dir": str(rest_dir.relative_to(PROJECT_ROOT)) if rest_dir else "",
        "latest_live_replay_dirs": [str(path.relative_to(PROJECT_ROOT)) for path in latest_live_replay_dirs],
        "lowdd_forward_gate_dir": str(lowdd_forward_gate_dir.relative_to(PROJECT_ROOT)) if lowdd_forward_gate_dir else "",
        "btc1h_gate_dir": str(btc1h_dir.relative_to(PROJECT_ROOT)) if btc1h_dir else "",
        "btc1h_multi_holdout_dir": str(btc1h_multi_holdout_dir.relative_to(PROJECT_ROOT)) if btc1h_multi_holdout_dir else "",
        "btc1h_replay_reconciliation_dir": str(btc1h_replay_reconciliation_dir.relative_to(PROJECT_ROOT))
        if btc1h_replay_reconciliation_dir
        else "",
        "btc1h_clean_evidence_clock_dir": str(btc1h_clean_evidence_clock_dir.relative_to(PROJECT_ROOT))
        if btc1h_clean_evidence_clock_dir
        else "",
        "btc1h_basis_mismatch_audit_dir": str(btc1h_basis_mismatch_dir.relative_to(PROJECT_ROOT))
        if btc1h_basis_mismatch_dir
        else "",
        "shadow_official_dir": str(shadow_official_dir.relative_to(PROJECT_ROOT)) if shadow_official_dir else "",
        "execution_realism_dir": str(execution_realism_dir.relative_to(PROJECT_ROOT)) if execution_realism_dir else "",
        "ledger_schema_dir": str(ledger_schema_dir.relative_to(PROJECT_ROOT)) if ledger_schema_dir else "",
        "post_restart_collection_gate_dir": str(post_restart_dir.relative_to(PROJECT_ROOT)) if post_restart_dir else "",
        "post_restart_verification_dir": str(post_restart_verification_dir.relative_to(PROJECT_ROOT)) if post_restart_verification_dir else "",
        "row_reconciliation_dir": str(row_reconciliation_dir.relative_to(PROJECT_ROOT)) if row_reconciliation_dir else "",
        "shadow_status_dir": str(shadow_status_dir.relative_to(PROJECT_ROOT)) if shadow_status_dir else "",
        "process_hygiene_duplicate_target_process_count": to_int_value(
            shadow_status_info.get("duplicate_target_process_count", 0)
        ),
        "process_hygiene_unmanaged_matching_process_count": to_int_value(
            shadow_status_info.get("unmanaged_matching_process_count", 0)
        ),
        "frozen_policy_parity_dir": str(frozen_policy_dir.relative_to(PROJECT_ROOT)) if frozen_policy_dir else "",
        "settlement_basis_risk_dir": str(basis_risk_dir.relative_to(PROJECT_ROOT)) if basis_risk_dir else "",
        "min_pred_trades": args.min_pred_trades,
        "min_live_proxy_trades": args.min_live_proxy_trades,
        "min_live_official_trades": args.min_live_official_trades,
        "btc1h_expected_model_ttl_policy": args.btc1h_expected_model_ttl_policy,
        "btc1h_expected_model_policy_version": args.btc1h_expected_model_policy_version,
        "cf_benchmarks_credentials_present": cf_present,
        "production_ready_count": int(summary["production_ready"].sum()),
    }
    summary.to_csv(args.out_dir / "readiness_summary.csv", index=False)
    (args.out_dir / "run_info.json").write_text(json.dumps(out_info, indent=2, sort_keys=True), encoding="utf-8")

    verdict = "PASS" if out_info["production_ready_count"] > 0 else "FAIL"
    report = [
        "# BTC Deployment Readiness",
        "",
        f"Created UTC: `{out_info['created_at_utc']}`",
        f"Verdict: `{verdict}`",
        "",
        "## Inputs",
        "",
        f"- Broad BTC15M gate: `{out_info['broad_gate_dir']}`",
        f"- Side BTC15M gate: `{out_info['side_gate_dir']}`",
        f"- BTC15M materialized first-signal grid: `{out_info['materialized_grid_dir']}`",
        f"- REST official fill: `{out_info['rest_official_dir']}`",
        f"- BTC15M lowdd forward gate: `{out_info['lowdd_forward_gate_dir']}`",
        f"- BTC1H gate: `{out_info['btc1h_gate_dir']}`",
        f"- BTC1H multi-holdout gate: `{out_info['btc1h_multi_holdout_dir']}`",
        f"- BTC1H replay-vs-ledger reconciliation: `{out_info['btc1h_replay_reconciliation_dir']}`",
        f"- BTC1H clean evidence clock gate: `{out_info['btc1h_clean_evidence_clock_dir']}`",
        f"- BTC1H official basis/mismatch audit: `{out_info['btc1h_basis_mismatch_audit_dir']}`",
        f"- Shadow official ledger audit: `{out_info['shadow_official_dir']}`",
        f"- Execution-realism audit: `{out_info['execution_realism_dir']}`",
        f"- Shadow ledger schema preflight: `{out_info['ledger_schema_dir']}`",
        f"- Post-restart collection gate: `{out_info['post_restart_collection_gate_dir']}`",
        f"- Post-restart evidence-clock verifier: `{out_info['post_restart_verification_dir']}`",
        f"- Paper-vs-replay row reconciliation: `{out_info['row_reconciliation_dir']}`",
        f"- Running source/process hygiene: `{out_info['shadow_status_dir']}`",
        f"- Duplicate target processes: `{out_info['process_hygiene_duplicate_target_process_count']}`",
        f"- Unmanaged matching BTC processes: `{out_info['process_hygiene_unmanaged_matching_process_count']}`",
        f"- Frozen wrapper policy parity: `{out_info['frozen_policy_parity_dir']}`",
        f"- Settlement-basis risk gate: `{out_info['settlement_basis_risk_dir']}`",
        f"- BTC1H expected model policy: `{args.btc1h_expected_model_policy_version}` / `{args.btc1h_expected_model_ttl_policy}`",
        f"- CF Benchmarks credentials present: `{cf_present}`",
        "",
        "## Summary",
        "",
        summary.round(4).to_string(index=False),
        "",
        "## Rule",
        "",
        (
            "Production readiness requires positive official-settled live PnL, "
            f"at least {args.min_live_official_trades} official live trades, "
            f"at least {args.min_live_proxy_trades} live proxy trades, and enough "
            "historical Predexon evidence where applicable. It also requires "
            "passing execution-realism, live paper-ledger schema checks, "
            "running-source freshness, paper-vs-replay reconciliation, "
            "BTC1H model-policy and clean evidence-clock gates, and settlement-basis gates. "
            "Proxy-only profitability is not sufficient."
        ),
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.round(4).to_string(index=False))
    print(json.dumps(out_info, indent=2, sort_keys=True))
    print(f"Wrote {args.out_dir}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
