#!/usr/bin/env python3
"""Freeze a non-trading BTC15M ML sidecar experiment.

This script preregisters the only current non-lowdd ML lead as a diagnostic
metric. It deliberately does not submit orders, start processes, tune gates, or
promote old rows. The output is a frozen experiment contract for future scoring
from the raw websocket capture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_MODEL_DIR = BACKTEST_ROOT / "btc15m_april_top_models_20260514_clean"
DEFAULT_OUT = BACKTEST_ROOT / f"btc15m_ml_sidecar_preregistration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BTC15M LightGBM non-trading sidecar preregistration packet.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--backtest-root", type=Path, default=BACKTEST_ROOT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--model-name", default="lightgbm_tabular")
    parser.add_argument("--candidate-screen-dir", type=Path, default=None)
    parser.add_argument("--ml-grid-dir", type=Path, default=None)
    parser.add_argument("--min-future-official-rows", type=int, default=50)
    parser.add_argument("--min-future-clean-proxy-rows", type=int, default=100)
    return parser.parse_args()


def latest_dir(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_model_metadata(model_path: Path) -> dict[str, Any]:
    if not model_path.exists():
        return {
            "model_file_exists": False,
            "model_file_sha256": "",
            "feature_count": 0,
            "features": [],
            "model_payload_keys": "",
            "model_load_error": "model_file_missing",
        }
    try:
        with model_path.open("rb") as f:
            payload = pickle.load(f)
        features = list(payload.get("features", [])) if isinstance(payload, dict) else []
        keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        return {
            "model_file_exists": True,
            "model_file_sha256": sha256_file(model_path),
            "feature_count": len(features),
            "features": features,
            "model_payload_keys": ";".join(str(k) for k in keys),
            "model_load_error": "",
        }
    except Exception as exc:
        return {
            "model_file_exists": True,
            "model_file_sha256": sha256_file(model_path),
            "feature_count": 0,
            "features": [],
            "model_payload_keys": "",
            "model_load_error": repr(exc),
        }


def gate_from_model_summary(model_dir: Path, model_name: str) -> dict[str, Any]:
    summary = read_csv(model_dir / "model_summary.csv")
    if summary.empty:
        return {"gate_min_p": 0.0, "gate_min_ev": 0.0, "model_summary_blocker": "model_summary_missing"}
    gate = summary[
        summary["model"].astype(str).eq(model_name)
        & summary["split"].astype(str).eq("validation")
        & summary["pnl_model"].astype(str).eq("pnl")
    ]
    if gate.empty:
        return {"gate_min_p": 0.0, "gate_min_ev": 0.0, "model_summary_blocker": "validation_gate_missing"}
    row = gate.iloc[0]
    return {
        "gate_min_p": as_float(row.get("gate_min_p")),
        "gate_min_ev": as_float(row.get("gate_min_ev")),
        "validation_trades": as_int(row.get("trades")),
        "validation_pnl": as_float(row.get("pnl")),
        "validation_max_dd": as_float(row.get("max_dd")),
        "validation_win_rate_pct": as_float(row.get("win_rate_pct")),
        "model_summary_blocker": "",
    }


def ml_grid_evidence(ml_grid_dir: Path | None, model_name: str) -> dict[str, Any]:
    table = read_csv(ml_grid_dir / "ml_window_grid_official_proxy_summary.csv" if ml_grid_dir else None)
    if table.empty:
        return {"ml_grid_blocker": "ml_grid_summary_missing"}
    proxy = table[(table["model"].astype(str).eq(model_name)) & (table["subset"].astype(str).eq("proxy_all"))]
    official = table[(table["model"].astype(str).eq(model_name)) & (table["subset"].astype(str).eq("official_subset"))]
    if proxy.empty:
        return {"ml_grid_blocker": "model_proxy_row_missing"}
    src = proxy.iloc[0]
    off = official.iloc[0] if not official.empty else src
    return {
        "ml_grid_blocker": "",
        "full_raw_clean_rows": as_int(src.get("rows")),
        "full_raw_events": as_int(src.get("events")),
        "full_raw_official_rows": as_int(src.get("official_rows")),
        "full_raw_proxy_pnl_2c": as_float(src.get("proxy_pnl_2c")),
        "full_raw_official_pnl_2c": as_float(src.get("official_pnl_2c")),
        "full_raw_premium": as_float(src.get("premium")),
        "full_raw_proxy_win_rate": as_float(src.get("proxy_win_rate")),
        "full_raw_official_win_rate": as_float(src.get("official_win_rate")),
        "full_raw_max_dd_proxy_2c": as_float(src.get("max_dd_proxy_2c")),
        "full_raw_max_dd_official_2c": as_float(off.get("max_dd_official_2c")),
        "full_raw_row_quality_blockers": clean_text(src.get("row_quality_blockers")),
    }


def candidate_screen_evidence(candidate_screen_dir: Path | None, model_name: str) -> dict[str, Any]:
    table = read_csv(candidate_screen_dir / "candidate_screen.csv" if candidate_screen_dir else None)
    candidate_id = f"ml_{model_name}"
    if table.empty:
        return {"candidate_screen_blocker": "candidate_screen_missing", "candidate_screen_candidate_id": candidate_id}
    rows = table[table["candidate_id"].astype(str).eq(candidate_id)]
    if rows.empty:
        return {"candidate_screen_blocker": "candidate_screen_model_row_missing", "candidate_screen_candidate_id": candidate_id}
    src = rows.iloc[0]
    return {
        "candidate_screen_blocker": "",
        "candidate_screen_candidate_id": candidate_id,
        "candidate_screen_status": clean_text(src.get("status")),
        "candidate_screen_forward_action": clean_text(src.get("forward_action")),
        "candidate_screen_deployable_now": clean_text(src.get("deployable_now")),
        "candidate_screen_blockers": clean_text(src.get("blockers")),
        "candidate_screen_notes": clean_text(src.get("notes")),
    }


def build_freeze_spec(
    *,
    args: argparse.Namespace,
    model_path: Path,
    model_meta: dict[str, Any],
    gate: dict[str, Any],
    evidence: dict[str, Any],
    screen: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze_blockers: list[str] = []
    deployment_blockers: list[str] = []
    if not model_meta.get("model_file_exists"):
        freeze_blockers.append("model_file_missing")
    if model_meta.get("model_load_error"):
        freeze_blockers.append("model_payload_unreadable")
    if model_meta.get("feature_count", 0) <= 0:
        freeze_blockers.append("model_features_missing")
    if gate.get("model_summary_blocker"):
        freeze_blockers.append(str(gate["model_summary_blocker"]))
    if evidence.get("ml_grid_blocker"):
        freeze_blockers.append(str(evidence["ml_grid_blocker"]))
    if screen.get("candidate_screen_blocker"):
        freeze_blockers.append(str(screen["candidate_screen_blocker"]))

    row_quality = clean_text(evidence.get("full_raw_row_quality_blockers"))
    if row_quality:
        deployment_blockers.append(row_quality)
    if as_float(evidence.get("full_raw_proxy_pnl_2c")) <= 0:
        deployment_blockers.append("full_raw_proxy_pnl_2c_not_positive")
    if as_float(evidence.get("full_raw_official_pnl_2c")) <= 0:
        deployment_blockers.append("full_raw_official_pnl_2c_not_positive")
    if as_int(evidence.get("full_raw_official_rows")) < int(args.min_future_official_rows):
        deployment_blockers.append("official_rows_below_forward_review_min")
    if as_int(evidence.get("full_raw_clean_rows")) < int(args.min_future_clean_proxy_rows):
        deployment_blockers.append("clean_proxy_rows_below_forward_review_min")

    can_freeze = not freeze_blockers
    spec = {
        "candidate_id": "btc15m_lightgbm_tabular_nontrading_sidecar",
        "status": "FROZEN_NON_TRADING_FORWARD_METRIC" if can_freeze else "FREEZE_BLOCKED",
        "model_name": args.model_name,
        "family": "BTC15M",
        "evaluation_mode": "non_trading_sidecar_metric_only_from_raw_capture",
        "order_submission_allowed": False,
        "paper_order_allowed": False,
        "starts_or_restarts_processes": False,
        "model_dir": str(args.model_dir),
        "model_file": str(model_path),
        "model_file_sha256": model_meta.get("model_file_sha256", ""),
        "feature_count": int(model_meta.get("feature_count") or 0),
        "gate_min_p": gate.get("gate_min_p", 0.0),
        "gate_min_ev": gate.get("gate_min_ev", 0.0),
        "selection_policy": "score all executable side candidates; keep pred_win_prob >= gate_min_p and pred_ev >= gate_min_ev; first selected row per event",
        "decision_time_basis": "received_at_ns from live websocket top-of-book plus causal Coinbase ticker as-of joins",
        "entry_price_basis": "executable side ask only; no NO=1-YES shortcut",
        "max_spread_cents": 2.0,
        "min_visible_qty": 1.0,
        "ttl_min": 0.0,
        "ttl_max": 15.0,
        "min_entry": 0.01,
        "max_entry": 0.99,
        "max_btc_spot_age_sec": 90.0,
        "one_trade_per_event": True,
        "settlement_basis": "Kalshi REST official settlement for counted rows; proxy labels are diagnostic only",
        "min_future_official_rows_for_review": int(args.min_future_official_rows),
        "min_future_clean_proxy_rows_for_review": int(args.min_future_clean_proxy_rows),
        "min_future_official_rows_for_promotion_discussion": 100,
        "deployment_blockers": ";".join(dict.fromkeys(deployment_blockers)),
        "freeze_blockers": ";".join(dict.fromkeys(freeze_blockers)),
        "deployable_now": False,
        "freeze_rationale": (
            "Only non-lowdd candidate with clean full-raw replay rows and positive proxy plus tiny official-subset PnL; "
            "frozen as a metric only so future raw-capture rows can be judged without tuning or order flow."
        ),
    }
    gate_row = {
        "candidate_id": spec["candidate_id"],
        "can_freeze_non_trading_metric": can_freeze,
        "deployable_now": False,
        "recommended_next_action": (
            "score_future_raw_capture_snapshots_no_orders"
            if can_freeze
            else "repair_freeze_blockers_before_forward_tracking"
        ),
        "why_not_deployable": spec["deployment_blockers"] or "non_trading_metric_only_requires_future_official_rows",
        "why_not_start_ordering_shadow": "ML evidence is under-sampled and old rows are diagnostic only; use raw-capture scoring before any paper-ordering process.",
    }
    return spec, gate_row


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    work = df.copy()
    for col in work.columns:
        work[col] = work[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), int(work[col].map(len).max())) for col in work.columns}
    lines = ["| " + " | ".join(col.ljust(widths[col]) for col in work.columns) + " |"]
    lines.append("| " + " | ".join("-" * widths[col] for col in work.columns) + " |")
    for _, src in work.iterrows():
        lines.append("| " + " | ".join(str(src[col]).ljust(widths[col]) for col in work.columns) + " |")
    return "\n".join(lines)


def build_report(
    spec: dict[str, Any],
    gate_row: dict[str, Any],
    evidence: dict[str, Any],
    screen: dict[str, Any],
    info: dict[str, Any],
) -> str:
    spec_cols = [
        "candidate_id",
        "status",
        "model_name",
        "gate_min_p",
        "gate_min_ev",
        "feature_count",
        "deployable_now",
        "deployment_blockers",
        "freeze_blockers",
    ]
    evidence_cols = [
        "full_raw_clean_rows",
        "full_raw_official_rows",
        "full_raw_proxy_pnl_2c",
        "full_raw_official_pnl_2c",
        "full_raw_proxy_win_rate",
        "full_raw_official_win_rate",
        "candidate_screen_status",
        "candidate_screen_forward_action",
    ]
    spec_table = pd.DataFrame([{col: spec.get(col, "") for col in spec_cols}])
    evidence_row = {col: evidence.get(col, screen.get(col, "")) for col in evidence_cols}
    evidence_table = pd.DataFrame([evidence_row])
    lines = [
        "# BTC15M ML Non-Trading Sidecar Preregistration",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Freeze status: `{spec['status']}`.",
        "- This is not deployable and not a paper-ordering shadow.",
        "- Future use is offline/raw-capture scoring only until enough official-settled forward rows exist.",
        "",
        "## Freeze Spec",
        "",
        markdown_table(spec_table),
        "",
        "## Evidence",
        "",
        markdown_table(evidence_table),
        "",
        "## Gate Decision",
        "",
        markdown_table(pd.DataFrame([gate_row])),
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    candidate_screen_dir = args.candidate_screen_dir or latest_dir(args.backtest_root, "btc15m_research_candidate_screen_*")
    ml_grid_dir = args.ml_grid_dir or latest_dir(args.backtest_root, "btc15m_ml_live_ws_full_raw_window_grid_*")
    model_path = args.model_dir / f"{args.model_name}.pkl"

    model_meta = load_model_metadata(model_path)
    gate = gate_from_model_summary(args.model_dir, args.model_name)
    evidence = ml_grid_evidence(ml_grid_dir, args.model_name)
    screen = candidate_screen_evidence(candidate_screen_dir, args.model_name)
    spec, gate_row = build_freeze_spec(
        args=args,
        model_path=model_path,
        model_meta=model_meta,
        gate=gate,
        evidence=evidence,
        screen=screen,
    )

    freeze_specs = pd.DataFrame([spec])
    evidence_summary = pd.DataFrame([{**evidence, **screen}])
    gate_summary = pd.DataFrame([gate_row])
    freeze_specs.to_csv(args.out_dir / "ml_sidecar_freeze_specs.csv", index=False)
    evidence_summary.to_csv(args.out_dir / "ml_sidecar_evidence_summary.csv", index=False)
    gate_summary.to_csv(args.out_dir / "ml_sidecar_gate_summary.csv", index=False)
    features = pd.DataFrame({"feature": model_meta.get("features", [])})
    features.to_csv(args.out_dir / "ml_sidecar_feature_list.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_screen_dir": str(candidate_screen_dir) if candidate_screen_dir else "",
        "ml_grid_dir": str(ml_grid_dir) if ml_grid_dir else "",
        "model_dir": str(args.model_dir),
        "model_name": args.model_name,
        "model_file_sha256": model_meta.get("model_file_sha256", ""),
        "started_or_restarted_processes": False,
        "submitted_orders": False,
        "paper_orders": False,
        "deployed_live": False,
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    (args.out_dir / "report.md").write_text(build_report(spec, gate_row, evidence, screen, info), encoding="utf-8")

    print(freeze_specs.to_string(index=False))
    print(gate_summary.to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
