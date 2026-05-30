#!/usr/bin/env python3
"""Build a root-cause audit for BTC1H replay-vs-ledger failures.

This is a diagnostic for replay fidelity. It does not search thresholds or
relax promotion gates.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_RECON_DIR = BACKTEST_ROOT / "btc1h_replay_vs_ledger_reconciliation_latest_codex"
DEFAULT_PARITY = BACKTEST_ROOT / "btc1h_selected_signal_model_parity_latest_codex" / "btc1h_selected_signal_model_parity.csv"
DEFAULT_DECISION_CHAIN = (
    BACKTEST_ROOT / "btc1h_forward_snapshot_signal_audit_latest_codex" / "btc1h_selected_signal_decision_chain.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_replay_root_cause_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

REPLAY_MODE_DIRS = {
    "candidate_scan": "btc1h_replay_vs_ledger_reconciliation_latest_codex",
    "candidate_selected_market": "btc1h_replay_vs_ledger_reconciliation_candidate_selected_market_latest_codex",
    "selected_scan": "btc1h_replay_vs_ledger_reconciliation_selected_scans_latest_codex",
    "selected_market": "btc1h_replay_vs_ledger_reconciliation_selected_market_latest_codex",
    "ttl15_candidate_scan": "btc1h_replay_vs_ledger_reconciliation_ttl15_latest_codex",
    "fullscan_prefilter": "btc1h_replay_vs_ledger_reconciliation_fullscan_prefilter_highconf_latest_codex",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reconciliation-dir", type=Path, default=DEFAULT_RECON_DIR)
    p.add_argument("--selected-parity", type=Path, default=DEFAULT_PARITY)
    p.add_argument("--decision-chain", type=Path, default=DEFAULT_DECISION_CHAIN)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def parse_time(value: Any) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "" or str(value).lower() == "nan":
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def finite_max(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.max())


def finite_min(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.min())


def unique_text(values: pd.Series) -> str:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() == "nan" or text in seen:
            continue
        seen.append(text)
    return ";".join(seen)


def market_side_key(market: Any, side: Any) -> str:
    market_text = str(market or "").strip().upper()
    side_text = str(side or "").strip().lower()
    if not market_text or market_text == "NAN" or not side_text or side_text == "nan":
        return ""
    return f"{market_text}|{side_text}"


def summarize_parity(parity: pd.DataFrame) -> pd.DataFrame:
    if parity.empty:
        return pd.DataFrame()
    work = parity.copy()
    work["join_key"] = [market_side_key(m, s) for m, s in zip(work.get("market_ticker", ""), work.get("side", ""))]
    work = work[work["join_key"].ne("")].copy()
    if work.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for key, group in work.groupby("join_key", sort=False):
        parity_status = group.get("parity_status", pd.Series("", index=group.index)).astype(str)
        implied_status = group.get("implied_ttl_status", pd.Series("", index=group.index)).astype(str)
        selected_ns = pd.to_numeric(group.get("selected_received_at_ns", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "join_key": key,
                "parity_rows": int(len(group)),
                "parity_statuses": unique_text(parity_status),
                "scan_ttl_recomputed_signal_rows": int(parity_status.isin(["pass", "value_mismatch"]).sum()),
                "scan_ttl_no_signal_rows": int(parity_status.eq("recomputed_no_signal").sum()),
                "implied_ttl_pass_rows": int(implied_status.eq("pass").sum()),
                "selected_first_ns": finite_min(selected_ns),
                "selected_last_ns": finite_max(selected_ns),
                "max_model_p_yes_abs_diff": finite_max(group.get("model_p_yes_abs_diff", pd.Series(dtype=float))),
                "max_net_edge_cents_abs_diff": finite_max(group.get("net_edge_cents_abs_diff", pd.Series(dtype=float))),
                "max_top_entry_price_abs_diff": finite_max(group.get("top_entry_price_abs_diff", pd.Series(dtype=float))),
                "max_quote_age_ms": finite_max(group.get("quote_age_ms", pd.Series(dtype=float))),
                "min_implied_ttl_offset_sec": finite_min(group.get("implied_ttl_offset_sec", pd.Series(dtype=float))),
                "max_implied_ttl_offset_sec": finite_max(group.get("implied_ttl_offset_sec", pd.Series(dtype=float))),
            }
        )
    return pd.DataFrame(rows)


def summarize_decision_chain(chain: pd.DataFrame) -> pd.DataFrame:
    if chain.empty:
        return pd.DataFrame()
    work = chain.copy()
    work["join_key"] = [market_side_key(m, s) for m, s in zip(work.get("market_ticker", ""), work.get("side", ""))]
    work = work[work["join_key"].ne("")].copy()
    if work.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for key, group in work.groupby("join_key", sort=False):
        decision_action = group.get("decision_action", pd.Series("", index=group.index)).astype(str).str.lower()
        entry_diff = pd.to_numeric(group.get("entry_price_diff", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "join_key": key,
                "decision_chain_rows": int(len(group)),
                "decision_fill_rows": int(decision_action.eq("paper_fill").sum()),
                "decision_skip_rows": int(decision_action.eq("skip").sum()),
                "decision_actions": unique_text(decision_action),
                "decision_details": unique_text(group.get("decision_detail", pd.Series("", index=group.index))),
                "max_decision_minus_selected_sec": finite_max(
                    group.get("decision_minus_selected_sec", pd.Series(dtype=float))
                ),
                "max_decision_entry_abs_diff": finite_max(entry_diff.abs()),
                "min_decision_entry_diff": finite_min(entry_diff),
                "max_decision_entry_diff": finite_max(entry_diff),
            }
        )
    return pd.DataFrame(rows)


def choose_join_key(row: pd.Series) -> str:
    ledger = str(row.get("ledger_join_key", "") or "").strip()
    replay = str(row.get("replay_join_key", "") or "").strip()
    return ledger if ledger else replay


def replay_minus_selected_sec(row: pd.Series) -> float | None:
    replay_time = parse_time(row.get("replay_entry_time"))
    selected_ns = finite(row.get("selected_first_ns"))
    if replay_time is None or selected_ns is None:
        return None
    selected_time = pd.Timestamp(int(selected_ns), unit="ns", tz="UTC")
    return float((replay_time - selected_time).total_seconds())


def classify_root_cause(row: pd.Series) -> str:
    diagnosis = str(row.get("diagnosis", ""))
    decision_entry_abs = finite(row.get("max_decision_entry_abs_diff")) or 0.0
    replay_selected_gap = finite(row.get("replay_minus_selected_sec"))
    scan_ttl_no_signal = int(finite(row.get("scan_ttl_no_signal_rows")) or 0)
    scan_ttl_signal = int(finite(row.get("scan_ttl_recomputed_signal_rows")) or 0)

    if "skip_then_fill" in diagnosis:
        return "reprice_skip_then_fill_sequence_missing"
    if "replaced_by_replay_market" in diagnosis or "replacing_captured_live_fill" in diagnosis:
        return "same_event_market_selection_not_row_faithful"
    if diagnosis == "captured_live_fill_missing_from_replay" and scan_ttl_no_signal and not scan_ttl_signal:
        return "scan_time_model_does_not_recreate_captured_signal"
    if diagnosis == "captured_live_fill_missing_from_replay":
        return "captured_fill_missing_from_replay"
    if diagnosis == "matched_market_with_entry_or_pnl_drift":
        if decision_entry_abs >= 0.009:
            return "order_decision_reprice_fill_price_missing"
        if replay_selected_gap is not None and abs(replay_selected_gap) > 0.25:
            return "blocked_dedupe_or_post_selected_scan_used_as_replay_clock"
        return "entry_or_pnl_path_drift"
    return "unclassified_replay_fidelity_gap"


def next_required_evidence(root_cause: str) -> str:
    mapping = {
        "reprice_skip_then_fill_sequence_missing": "Replay must model captured order_decision skip/fill sequencing and allow later fill after failed reprice.",
        "same_event_market_selection_not_row_faithful": "Replay must honor captured selected_market/order_decision state and event-level dedupe semantics.",
        "scan_time_model_does_not_recreate_captured_signal": "Future rows need exact captured model inputs/TTL policy; scan-time recomputation cannot validate these old rows.",
        "captured_fill_missing_from_replay": "Replay must use captured order_decision fill state or expose the missing decision-time input field.",
        "order_decision_reprice_fill_price_missing": "Replay PnL must use the filled order_decision/ledger entry price, not just selected-signal top price.",
        "blocked_dedupe_or_post_selected_scan_used_as_replay_clock": "Candidate-scan replay must not trade from blocked dedupe scans as if they were selected scans.",
        "entry_or_pnl_path_drift": "Audit timestamp, settlement, and fee path for the matched market before promotion.",
    }
    return mapping.get(root_cause, "Inspect row manually before treating replay as evidence.")


def build_rows(diagnosis: pd.DataFrame, parity: pd.DataFrame, decision_chain: pd.DataFrame) -> pd.DataFrame:
    if diagnosis.empty:
        return pd.DataFrame()
    work = diagnosis.copy()
    work["join_key"] = work.apply(choose_join_key, axis=1)
    parity_summary = summarize_parity(parity)
    decision_summary = summarize_decision_chain(decision_chain)
    for summary in (parity_summary, decision_summary):
        if not summary.empty:
            work = work.merge(summary, on="join_key", how="left")
    work["replay_minus_selected_sec"] = work.apply(replay_minus_selected_sec, axis=1)
    work["root_cause"] = work.apply(classify_root_cause, axis=1)
    work["next_required_evidence"] = work["root_cause"].map(next_required_evidence)
    cols = [
        "root_cause",
        "next_required_evidence",
        "diagnosis",
        "status",
        "event_ticker",
        "ledger_join_key",
        "replay_join_key",
        "ledger_entry_price",
        "replay_entry_price",
        "entry_price_abs_diff",
        "ledger_pnl",
        "replay_pnl",
        "pnl_diff_replay_minus_ledger",
        "decision_actions",
        "decision_details",
        "decision_fill_rows",
        "decision_skip_rows",
        "max_decision_minus_selected_sec",
        "max_decision_entry_abs_diff",
        "replay_minus_selected_sec",
        "parity_statuses",
        "scan_ttl_recomputed_signal_rows",
        "scan_ttl_no_signal_rows",
        "implied_ttl_pass_rows",
        "max_model_p_yes_abs_diff",
        "max_net_edge_cents_abs_diff",
        "max_quote_age_ms",
        "min_implied_ttl_offset_sec",
        "max_implied_ttl_offset_sec",
    ]
    return work[[c for c in cols if c in work.columns]].reset_index(drop=True)


def build_summary(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame([{"root_cause_rows": 0, "promotion_usable_from_root_cause_audit": False}])
    counts = rows["root_cause"].value_counts().to_dict()
    out = {
        "root_cause_rows": int(len(rows)),
        "unique_root_causes": int(rows["root_cause"].nunique()),
        "promotion_usable_from_root_cause_audit": False,
        "dominant_root_cause": rows["root_cause"].value_counts().index[0],
        "root_cause_counts": ";".join(f"{k}={v}" for k, v in counts.items()),
        "requires_order_decision_reprice_model": bool(
            rows["root_cause"].astype(str).isin(
                ["reprice_skip_then_fill_sequence_missing", "order_decision_reprice_fill_price_missing"]
            ).any()
        ),
        "requires_blocked_dedupe_filter": bool(
            rows["root_cause"].astype(str).eq("blocked_dedupe_or_post_selected_scan_used_as_replay_clock").any()
        ),
        "requires_exact_model_inputs": bool(
            rows["root_cause"].astype(str).eq("scan_time_model_does_not_recreate_captured_signal").any()
        ),
    }
    return pd.DataFrame([out])


def replay_mode_comparison() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for mode, dirname in REPLAY_MODE_DIRS.items():
        path = BACKTEST_ROOT / dirname / "btc1h_replay_vs_ledger_reconciliation_summary.csv"
        df = read_csv(path)
        if df.empty:
            continue
        row = df.iloc[0].to_dict()
        row["mode"] = mode
        row["artifact"] = str(path)
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    cols = [
        "mode",
        "actual_rows",
        "replay_rows",
        "exact_market_side_matches",
        "entry_price_drift_rows",
        "pnl_drift_rows",
        "event_replacement_rows",
        "replay_minus_actual_pnl",
        "promotion_usable_replay",
        "blockers",
        "artifact",
    ]
    return pd.DataFrame(rows)[[c for c in cols if c in pd.DataFrame(rows).columns]]


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    diagnosis = read_csv(args.reconciliation_dir / "btc1h_replay_vs_ledger_mismatch_diagnosis.csv")
    parity = read_csv(args.selected_parity)
    decision_chain = read_csv(args.decision_chain)

    rows = build_rows(diagnosis, parity, decision_chain)
    summary = build_summary(rows)
    modes = replay_mode_comparison()

    rows.to_csv(args.out_dir / "btc1h_replay_root_cause_rows.csv", index=False)
    summary.to_csv(args.out_dir / "btc1h_replay_root_cause_summary.csv", index=False)
    modes.to_csv(args.out_dir / "btc1h_replay_mode_comparison.csv", index=False)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reconciliation_dir": str(args.reconciliation_dir),
        "selected_parity": str(args.selected_parity),
        "decision_chain": str(args.decision_chain),
        "out_dir": str(args.out_dir),
        "scope": "btc1h_replay_root_cause_diagnostic_only",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC1H Replay Root-Cause Audit",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        "",
        "## Summary",
        "",
        markdown_table(summary),
        "",
        "## Root-Cause Rows",
        "",
        markdown_table(
            rows[
                [
                    c
                    for c in [
                        "root_cause",
                        "diagnosis",
                        "event_ticker",
                        "ledger_join_key",
                        "replay_join_key",
                        "max_decision_entry_abs_diff",
                        "replay_minus_selected_sec",
                        "parity_statuses",
                        "next_required_evidence",
                    ]
                    if c in rows.columns
                ]
            ]
            if not rows.empty
            else rows
        ),
        "",
        "## Replay Mode Comparison",
        "",
        markdown_table(modes),
        "",
        "## Interpretation",
        "",
        "- This is diagnostic only; it does not make any BTC1H row deployable.",
        "- A replay mode that improves aggregate PnL still fails promotion unless row-level market, entry, and PnL parity pass.",
        "- Current root causes point to order-decision reprice/fill sequencing, same-event dedupe/market selection, and exact model-input capture.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
