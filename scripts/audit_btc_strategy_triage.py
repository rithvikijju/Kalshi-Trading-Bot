#!/usr/bin/env python3
"""Conservative BTC strategy triage across latest deployability artifacts.

This script does not search for new alpha or tune thresholds. It gathers the
latest BTC15M/BTC1H evidence into a small table that is useful after a GPT Pro
review: which candidates are still worth collecting evidence for, which are
research-only controls, and which are rejected for deployment.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_strategy_triage_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    candidate: str
    role: str
    status: str
    readiness_candidate: str
    readiness_source: str | None = None
    pred_strategy: str | None = None
    side_mode: str | None = None
    min_visible_qty: float | None = None
    live_summary_prefix: str | None = None
    opportunity_candidate: str | None = None
    fixed_blockers: tuple[str, ...] = ()
    next_action: str = ""


CANDIDATES: tuple[CandidateSpec, ...] = (
    CandidateSpec(
        family="BTC15M",
        candidate="btc15m_lowdd_current_wrapper",
        role="active paper-forward BTC15M path",
        status="active_forward_research_insufficient_sample",
        readiness_candidate="btc15m_lowdd_current_wrapper",
        readiness_source="lowdd_forward_promotion_gate",
        next_action="Keep the active lowdd paper-forward shadow and raw collector running; rerun the remote evidence refresh after new fills and do not deploy before the 50-row official/parity gate passes.",
    ),
    CandidateSpec(
        family="BTC15M",
        candidate="q250_firstskip_qty500_yes",
        role="stale preregistered paper-forward control",
        status="paper_forward_not_running",
        readiness_candidate="q250_firstskip_qty500_yes",
        pred_strategy="ttl10_12_entry50_q250",
        side_mode="yes",
        min_visible_qty=500.0,
        live_summary_prefix="btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_",
        opportunity_candidate="q250_firstskip_qty500_yes",
        fixed_blockers=(
            "selected_after_prior_live_replay_not_promotion_holdout",
            "preregistered_shadow_not_running",
            "needs_fresh_post_restart_official_ledger_rows",
        ),
        next_action="If explicitly authorized, start/restart paper shadows with the guarded script; count only future clean official-settled ledger rows.",
    ),
    CandidateSpec(
        family="BTC15M",
        candidate="q250_firstskip_qty500",
        role="best historical/live both-side research candidate",
        status="research_only_basis_decomposition",
        readiness_candidate="q250_firstskip_qty500",
        pred_strategy="ttl10_12_entry50_q250",
        side_mode="both",
        min_visible_qty=500.0,
        live_summary_prefix="btc15m_f2_live_ws_q250_firstskip_causal_rest_official_",
        opportunity_candidate="q250_firstskip_qty500",
        fixed_blockers=(
            "selected_after_prior_live_replay_not_promotion_holdout",
            "no_side_settlement_basis_fragility",
            "needs_fresh_post_restart_official_ledger_rows",
        ),
        next_action="Do not deploy; use as a decomposition/control while evaluating YES-only and basis-risk guards on future official-settled rows.",
    ),
    CandidateSpec(
        family="BTC15M",
        candidate="q1000_yes",
        role="sparser cleaner YES-only control",
        status="research_control_sparse",
        readiness_candidate="q1000_yes",
        pred_strategy="ttl10_12_entry50_q1000",
        side_mode="yes",
        min_visible_qty=1000.0,
        live_summary_prefix="btc15m_f2_live_ws_q1000_yes_causal_rest_official_",
        opportunity_candidate="q1000_yes",
        fixed_blockers=(
            "too_sparse_for_fast_promotion",
            "needs_fresh_post_restart_official_ledger_rows",
        ),
        next_action="Keep as a paper-only control after clean restart; do not let sparse positive PnL override sample-size gates.",
    ),
    CandidateSpec(
        family="BTC15M",
        candidate="q250",
        role="broad both-side F2 gate",
        status="rejected_for_deployment",
        readiness_candidate="q250",
        readiness_source="broad_gate",
        fixed_blockers=("official_rest_replay_negative", "basis_side_fragility"),
        next_action="Do not deploy or tune forward thresholds from this failed broad gate.",
    ),
    CandidateSpec(
        family="BTC15M",
        candidate="q500",
        role="broad both-side F2 gate",
        status="rejected_for_deployment",
        readiness_candidate="q500",
        readiness_source="broad_gate",
        fixed_blockers=("official_rest_replay_negative", "basis_side_fragility"),
        next_action="Do not deploy or tune forward thresholds from this failed broad gate.",
    ),
    CandidateSpec(
        family="BTC15M",
        candidate="q1000",
        role="broad both-side F2 gate",
        status="rejected_for_deployment",
        readiness_candidate="q1000",
        readiness_source="broad_gate",
        fixed_blockers=("official_rest_replay_negative", "basis_side_fragility"),
        next_action="Do not deploy or tune forward thresholds from this failed broad gate.",
    ),
    CandidateSpec(
        family="BTC1H",
        candidate="high_conf_80_entry70_no_chase",
        role="runner-up category observe-only strategy",
        status="observe_only_replay_blocked",
        readiness_candidate="high_conf_80_entry70_no_chase",
        readiness_source="promotion_gate",
        fixed_blockers=(
            "no_causal_replay_comparator",
            "needs_fresh_post_restart_official_ledger_rows",
        ),
        next_action="Keep BTC1H observe-only until a clean-schema shadow produces replayable official-settled rows.",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a conservative BTC strategy triage report.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--predexon-trades",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_predexon_rest_official_latest_codex" / "predexon_trades_rest_official.parquet",
    )
    parser.add_argument(
        "--readiness-dir",
        type=Path,
        default=BACKTEST_ROOT / "deployment_readiness_latest_codex",
    )
    parser.add_argument(
        "--opportunity-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_frozen_opportunity_rate_latest_codex",
    )
    parser.add_argument(
        "--metadata-vs-rest-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_predexon_metadata_vs_rest_latest_codex",
    )
    parser.add_argument(
        "--candidate-overlap-dir",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_candidate_overlap_latest_codex",
    )
    return parser.parse_args()


def latest_dir(prefix: str) -> Path | None:
    matches = [p for p in BACKTEST_ROOT.glob(f"{prefix}*") if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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
    return [part.strip() for part in text.split(";") if part.strip()]


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def max_drawdown(pnl: pd.Series) -> float:
    clean = pd.to_numeric(pnl, errors="coerce").dropna().reset_index(drop=True)
    if clean.empty:
        return 0.0
    curve = clean.cumsum()
    return float((curve - curve.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    clean = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(clean) < 2:
        return 0.0
    sd = float(clean.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(clean.mean() / sd * math.sqrt(len(clean)))


def metrics(df: pd.DataFrame, pnl_col: str, win_col: str, premium_col: str) -> dict[str, Any]:
    if df.empty or pnl_col not in df:
        return {
            "trades": 0,
            "pnl_2c": 0.0,
            "premium_2c": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd_2c": 0.0,
            "sharpe": 0.0,
        }
    work = df[pd.to_numeric(df[pnl_col], errors="coerce").notna()].copy()
    if work.empty:
        return {
            "trades": 0,
            "pnl_2c": 0.0,
            "premium_2c": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd_2c": 0.0,
            "sharpe": 0.0,
        }
    pnl = pd.to_numeric(work[pnl_col], errors="coerce")
    premium = pd.to_numeric(work.get(premium_col, 0.0), errors="coerce").fillna(0.0)
    wins = pd.to_numeric(work.get(win_col, 0.0), errors="coerce").fillna(0.0)
    total_pnl = float(pnl.sum())
    total_premium = float(premium.sum())
    return {
        "trades": int(len(work)),
        "pnl_2c": round(total_pnl, 4),
        "premium_2c": round(total_premium, 4),
        "rop": round(total_pnl / total_premium, 4) if total_premium > 0 else 0.0,
        "win_rate": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "max_dd_2c": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
    }


def filter_predexon(pred: pd.DataFrame, spec: CandidateSpec) -> pd.DataFrame:
    if pred.empty or not spec.pred_strategy:
        return pd.DataFrame()
    out = pred[pred["strategy"].astype(str).eq(spec.pred_strategy)].copy()
    if out.empty:
        return out
    side = out["side"].astype(str).str.lower()
    if spec.side_mode == "yes":
        mask = side.eq("yes")
    elif spec.side_mode == "no":
        mask = side.eq("no")
    else:
        mask = side.isin(["yes", "no"])
    mask &= pd.to_numeric(out["side_fair_p"], errors="coerce").ge(0.60)
    mask &= pd.to_numeric(out["fair_edge_cents"], errors="coerce").ge(12.0)
    mask &= pd.to_numeric(out["entry_price"], errors="coerce").le(0.50)
    mask &= pd.to_numeric(out["spread_cents"], errors="coerce").le(2.0)
    mask &= pd.to_numeric(out["ttl_min"], errors="coerce").between(10.0, 12.0)
    if spec.min_visible_qty is not None:
        mask &= pd.to_numeric(out["visible_qty"], errors="coerce").ge(spec.min_visible_qty)
    sort_cols = [c for c in ("available_at", "timestamp_utc", "event_ticker") if c in out.columns]
    return out.loc[mask].sort_values(sort_cols).reset_index(drop=True)


def select_readiness(readiness: pd.DataFrame, spec: CandidateSpec) -> pd.Series | None:
    if readiness.empty or "candidate" not in readiness:
        return None
    rows = readiness[readiness["candidate"].astype(str).eq(spec.readiness_candidate)]
    if spec.readiness_source and "source" in rows:
        rows = rows[rows["source"].astype(str).eq(spec.readiness_source)]
    if rows.empty:
        return None
    return rows.iloc[0]


def select_opportunity(opportunity: pd.DataFrame, spec: CandidateSpec) -> pd.Series | None:
    if opportunity.empty or not spec.opportunity_candidate or "candidate" not in opportunity:
        return None
    rows = opportunity[opportunity["candidate"].astype(str).eq(spec.opportunity_candidate)]
    if rows.empty:
        return None
    return rows.iloc[0]


def select_live_summary(spec: CandidateSpec) -> pd.Series | None:
    if not spec.live_summary_prefix:
        return None
    directory = latest_dir(spec.live_summary_prefix)
    if directory is None:
        return None
    df = read_csv(directory / "summary.csv")
    if df.empty or "candidate" not in df:
        return None
    rows = df[df["candidate"].astype(str).eq(spec.candidate)]
    if rows.empty:
        return None
    row = rows.iloc[0].copy()
    row["_artifact_dir"] = str(directory)
    return row


def overlap_context(pairwise: pd.DataFrame, spec: CandidateSpec) -> dict[str, Any]:
    empty = {
        "overlap_reference_candidate": "",
        "full_causal_official_overlap_status": "",
        "full_causal_shared_official_events": 0,
        "full_causal_official_event_overlap_rate": 0.0,
        "independent_evidence_note": "",
        "overlap_blocker": "",
    }
    if pairwise.empty:
        return empty
    reference = ""
    if spec.candidate == "q250_firstskip_qty500_yes":
        reference = "q1000_yes"
    elif spec.candidate == "q1000_yes":
        reference = "q250_firstskip_qty500_yes"
    elif spec.candidate == "q250_firstskip_qty500":
        reference = "q250_firstskip_qty500_yes"
    else:
        return empty
    rows = pairwise[
        pairwise.get("source", pd.Series("", index=pairwise.index)).astype(str).eq("full_causal")
        & pairwise.get("left_candidate", pd.Series("", index=pairwise.index)).astype(str).eq(spec.candidate)
        & pairwise.get("right_candidate", pd.Series("", index=pairwise.index)).astype(str).eq(reference)
    ]
    if rows.empty:
        return {**empty, "overlap_reference_candidate": reference}
    row = rows.iloc[0]
    status = str(row.get("overlap_status", ""))
    shared = to_float(row.get("shared_official_events"), 0.0)
    rate = to_float(row.get("official_event_overlap_rate"), 0.0)
    note = (
        f"Current full-causal official event set is identical to {reference}; do not count as independent confirmation."
        if status == "identical_official_event_set"
        else f"Current full-causal official overlap vs {reference}: {status}, shared_events={shared:g}, overlap_rate={rate:.2f}."
        if status
        else ""
    )
    blocker = (
        "not_independent_from_q250_yes_current_replay_sample"
        if spec.candidate == "q1000_yes" and status == "identical_official_event_set"
        else ""
    )
    return {
        "overlap_reference_candidate": reference,
        "full_causal_official_overlap_status": status,
        "full_causal_shared_official_events": int(shared),
        "full_causal_official_event_overlap_rate": round(rate, 4),
        "independent_evidence_note": note,
        "overlap_blocker": blocker,
    }


def blocker_categories(reasons: list[str]) -> list[str]:
    cats: set[str] = set()
    joined = ";".join(reasons)
    if any(token in joined for token in ("settlement_basis", "proxy_official", "basis_side")):
        cats.add("settlement_basis_or_proxy_mismatch")
    if any(token in joined for token in ("post_restart", "controlled_restart", "schema", "execution_realism", "ledger")):
        cats.add("clean_paper_ledger_missing")
    if any(token in joined for token in ("too_few", "sparse", "sample")):
        cats.add("sample_size")
    if any(token in joined for token in ("not_running", "DB_MISSING", "shadow_not_running")):
        cats.add("shadow_not_running")
    if any(token in joined for token in ("no_causal_replay", "replay_blocked")):
        cats.add("causal_replay_missing")
    if "live_official_not_positive" in joined or "official_rest_replay_negative" in joined:
        cats.add("official_live_pnl_failed")
    if "bad_window" in joined:
        cats.add("window_instability")
    if "selected_after_prior_live_replay" in joined:
        cats.add("not_clean_holdout")
    if "not_independent_from" in joined:
        cats.add("not_independent_evidence")
    return sorted(cats)


def build_row(
    spec: CandidateSpec,
    pred: pd.DataFrame,
    readiness: pd.DataFrame,
    opportunity: pd.DataFrame,
    candidate_overlap: pd.DataFrame,
    metadata_status: str,
) -> dict[str, Any]:
    pred_rows = filter_predexon(pred, spec)
    metadata_metrics = metrics(
        pred_rows,
        "pnl_predexon_metadata_2c",
        "win_pnl_predexon_metadata_2c",
        "premium_predexon_metadata_2c",
    )
    rest_metrics = metrics(
        pred_rows,
        "pnl_official_rest_2c",
        "win_pnl_official_rest_2c",
        "premium_official_rest_2c",
    )
    readiness_row = select_readiness(readiness, spec)
    opportunity_row = select_opportunity(opportunity, spec)
    live_row = select_live_summary(spec)
    overlap = overlap_context(candidate_overlap, spec)

    readiness_reasons = reason_tokens(readiness_row.get("failure_reasons", "")) if readiness_row is not None else ["readiness_row_missing"]
    overlap_blockers = [overlap["overlap_blocker"]] if overlap.get("overlap_blocker") else []
    reasons = sorted(set([*readiness_reasons, *spec.fixed_blockers, *overlap_blockers]))
    categories = blocker_categories(reasons)

    production_ready = bool(readiness_row is not None and to_bool(readiness_row.get("production_ready", False)))
    deployable_now = production_ready and not reasons

    return {
        "family": spec.family,
        "candidate": spec.candidate,
        "role": spec.role,
        "status": spec.status,
        "deployable_now": deployable_now,
        "production_ready_gate": production_ready,
        "historical_label_status": metadata_status if spec.pred_strategy else "",
        "historical_metadata_trades": metadata_metrics["trades"],
        "historical_metadata_pnl_2c": metadata_metrics["pnl_2c"],
        "historical_metadata_win_rate": metadata_metrics["win_rate"],
        "historical_metadata_max_dd_2c": metadata_metrics["max_dd_2c"],
        "historical_metadata_sharpe": metadata_metrics["sharpe"],
        "historical_rest_trades": rest_metrics["trades"],
        "historical_rest_pnl_2c": rest_metrics["pnl_2c"],
        "historical_rest_win_rate": rest_metrics["win_rate"],
        "historical_rest_coverage": round(rest_metrics["trades"] / metadata_metrics["trades"], 4) if metadata_metrics["trades"] else 0.0,
        "live_official_trades": to_float(live_row.get("official_trades")) if live_row is not None else to_float(readiness_row.get("live_official_trades")) if readiness_row is not None else 0.0,
        "live_official_pnl_2c": to_float(live_row.get("official_pnl")) if live_row is not None else to_float(readiness_row.get("live_official_pnl")) if readiness_row is not None else 0.0,
        "live_official_win_rate": to_float(live_row.get("official_win_rate")) if live_row is not None else to_float(readiness_row.get("live_official_win_rate")) if readiness_row is not None else 0.0,
        "live_proxy_trades": to_float(live_row.get("proxy_trades")) if live_row is not None else to_float(readiness_row.get("live_proxy_trades")) if readiness_row is not None else 0.0,
        "live_proxy_pnl_2c": to_float(live_row.get("proxy_pnl")) if live_row is not None else to_float(readiness_row.get("live_proxy_pnl")) if readiness_row is not None else 0.0,
        "official_proxy_mismatches": to_float(live_row.get("official_proxy_result_mismatches")) if live_row is not None else 0.0,
        "overlap_reference_candidate": overlap["overlap_reference_candidate"],
        "full_causal_official_overlap_status": overlap["full_causal_official_overlap_status"],
        "full_causal_shared_official_events": overlap["full_causal_shared_official_events"],
        "full_causal_official_event_overlap_rate": overlap["full_causal_official_event_overlap_rate"],
        "independent_evidence_note": overlap["independent_evidence_note"],
        "postfreeze_official_rows": to_float(opportunity_row.get("postfreeze_official_rows")) if opportunity_row is not None else 0.0,
        "postfreeze_official_pnl_2c": to_float(opportunity_row.get("postfreeze_official_pnl_2c")) if opportunity_row is not None else 0.0,
        "postfreeze_rows_per_day": to_float(opportunity_row.get("postfreeze_official_rows_per_day")) if opportunity_row is not None else 0.0,
        "projected_days_to_100_postfreeze_rows": to_float(opportunity_row.get("projected_days_to_100_postfreeze_rows_at_postfreeze_official_rate")) if opportunity_row is not None else 0.0,
        "ledger_running": to_bool(opportunity_row.get("ledger_running")) if opportunity_row is not None else "",
        "ledger_preflight_status": str(opportunity_row.get("ledger_preflight_status", "")) if opportunity_row is not None else "",
        "activity_status": str(opportunity_row.get("activity_status", "")) if opportunity_row is not None else "",
        "promotion_evidence_status": str(opportunity_row.get("promotion_evidence_status", "")) if opportunity_row is not None else "",
        "blocker_categories": ";".join(categories),
        "blockers": ";".join(reasons),
        "next_action": spec.next_action,
    }


def metadata_status(metadata_dir: Path) -> str:
    df = read_csv(metadata_dir / "predexon_metadata_vs_rest_summary.csv")
    if df.empty:
        return "UNKNOWN_METADATA_REST_AUDIT_MISSING"
    if "metadata_usable_as_historical_research_label" in df and df["metadata_usable_as_historical_research_label"].astype(bool).all():
        if "rest_metadata_mismatches" in df and pd.to_numeric(df["rest_metadata_mismatches"], errors="coerce").fillna(1).sum() == 0:
            return "PASS_RESEARCH_LABEL_REST_OVERLAP"
    return "FAIL_OR_INCOMPLETE_METADATA_REST_OVERLAP"


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    display = df.copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(lambda x: "" if pd.isna(x) else f"{float(x):.4g}")
        else:
            display[col] = display[col].map(lambda x: "" if pd.isna(x) else str(x))
    columns = list(display.columns)
    widths = {
        col: max(
            len(col),
            *(len(str(value)) for value in display[col].tolist()),
        )
        for col in columns
    }
    header = "| " + " | ".join(col.ljust(widths[col]) for col in columns) + " |"
    sep = "| " + " | ".join("-" * widths[col] for col in columns) + " |"
    body = [
        "| " + " | ".join(str(row[col]).ljust(widths[col]) for col in columns) + " |"
        for _, row in display.iterrows()
    ]
    return "\n".join([header, sep, *body])


def write_report(out_dir: Path, rows: pd.DataFrame, run_info: dict[str, Any]) -> None:
    top_cols = [
        "family",
        "candidate",
        "status",
        "deployable_now",
        "historical_metadata_trades",
        "historical_metadata_pnl_2c",
        "historical_rest_trades",
        "live_official_trades",
        "live_official_pnl_2c",
        "postfreeze_official_rows",
        "projected_days_to_100_postfreeze_rows",
        "blocker_categories",
    ]
    report = [
        "# BTC Strategy Triage",
        "",
        f"Created UTC: `{run_info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        "NO DEPLOY: every tracked BTC15M/BTC1H path remains below promotion gates.",
        "",
        "The best next evidence path is the active `btc15m_lowdd_current_wrapper`, but it still has only a small official-settled forward sample. `q250_firstskip_qty500_yes` and `q1000_yes` are stale preregistered controls, not active deployment candidates. `q250_firstskip_qty500` remains research-only because the both-side path is exposed to settlement-basis/NO-side fragility and was selected after prior live replay. BTC1H remains observe-only until causal replay and clean official ledger evidence exist.",
        "",
        "## Summary",
        "",
        markdown_table(rows[top_cols]),
        "",
        "## Candidate Details",
        "",
    ]
    detail_cols = [
        "candidate",
        "role",
        "historical_label_status",
        "historical_metadata_win_rate",
        "historical_metadata_max_dd_2c",
        "historical_metadata_sharpe",
        "historical_rest_coverage",
        "live_official_win_rate",
        "official_proxy_mismatches",
        "full_causal_official_overlap_status",
        "full_causal_official_event_overlap_rate",
        "ledger_running",
        "ledger_preflight_status",
        "activity_status",
        "promotion_evidence_status",
        "next_action",
    ]
    report.extend([markdown_table(rows[detail_cols]), ""])
    report.extend(
        [
            "## Inputs",
            "",
            f"- Predexon trades: `{run_info['predexon_trades']}`",
            f"- Readiness dir: `{run_info['readiness_dir']}`",
            f"- Opportunity dir: `{run_info['opportunity_dir']}`",
            f"- Metadata-vs-REST dir: `{run_info['metadata_vs_rest_dir']}`",
            f"- Candidate overlap dir: `{run_info['candidate_overlap_dir']}`",
            "",
            "## Rule",
            "",
            "This artifact is diagnostic only. A candidate can be research-interesting while `deployable_now` remains false. Promotion still requires official settlement, fees, top-of-book/FOK execution realism, live websocket replay, and clean live/paper ledger behavior to agree on fresh forward rows.",
            "",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pred = pd.read_parquet(args.predexon_trades) if args.predexon_trades.exists() else pd.DataFrame()
    readiness = read_csv(args.readiness_dir / "readiness_summary.csv")
    opportunity = read_csv(args.opportunity_dir / "frozen_opportunity_rate_summary.csv")
    candidate_overlap = read_csv(args.candidate_overlap_dir / "candidate_pairwise_overlap.csv")
    meta_status = metadata_status(args.metadata_vs_rest_dir)

    rows = pd.DataFrame(
        [build_row(spec, pred, readiness, opportunity, candidate_overlap, meta_status) for spec in CANDIDATES]
    )
    rows.to_csv(args.out_dir / "candidate_triage_summary.csv", index=False)

    run_info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predexon_trades": str(args.predexon_trades),
        "readiness_dir": str(args.readiness_dir),
        "opportunity_dir": str(args.opportunity_dir),
        "metadata_vs_rest_dir": str(args.metadata_vs_rest_dir),
        "candidate_overlap_dir": str(args.candidate_overlap_dir),
        "rows": int(len(rows)),
        "deployable_count": int(rows["deployable_now"].sum()),
        "note": "Read-only triage. Does not tune thresholds, start/restart processes, or authorize deployment.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(run_info, indent=2, sort_keys=True), encoding="utf-8")
    write_report(args.out_dir, rows, run_info)
    print(rows.to_string(index=False))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
