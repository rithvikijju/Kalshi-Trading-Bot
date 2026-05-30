#!/usr/bin/env python3
"""Reconcile BTC1H counterfactual replay trades against the paper ledger.

This is a replay-fidelity audit, not a strategy search.  A replay can be useful
for research while still failing promotion if it cannot reproduce the actual
shadow ledger row for row.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
ACTIVE_LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
DEFAULT_REPLAY_TRADES = (
    BACKTEST_ROOT
    / "btc1h_core_ws_counterfactual_snapshot_candidate_scans_latest_codex"
    / "btc1h_core_ws_counterfactual_trades.csv"
)
DEFAULT_OFFICIAL_TRADES = (
    BACKTEST_ROOT
    / "remote_btc_shadow_official_settlement_latest_codex"
    / "shadow_official_trades.csv"
)
DEFAULT_SELECTED_DECISION_CHAIN = (
    BACKTEST_ROOT
    / "btc1h_forward_snapshot_signal_audit_latest_codex"
    / "btc1h_selected_signal_decision_chain.csv"
)
DEFAULT_FILL_OFFICIAL_CHAIN = (
    BACKTEST_ROOT
    / "btc1h_forward_snapshot_signal_audit_latest_codex"
    / "btc1h_fill_decision_official_chain.csv"
)
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_replay_vs_ledger_reconciliation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-trades", type=Path, default=DEFAULT_REPLAY_TRADES)
    parser.add_argument("--official-trades", type=Path, default=DEFAULT_OFFICIAL_TRADES)
    parser.add_argument("--selected-decision-chain", type=Path, default=DEFAULT_SELECTED_DECISION_CHAIN)
    parser.add_argument("--fill-official-chain", type=Path, default=DEFAULT_FILL_OFFICIAL_CHAIN)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger", default=ACTIVE_LEDGER)
    parser.add_argument(
        "--replay-evidence-kind",
        default="independent_counterfactual",
        help=(
            "Evidence kind for the replay rows. Only independent_counterfactual "
            "can be promotion-usable; captured decision-log baselines are "
            "diagnostic even when row fidelity is exact."
        ),
    )
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def read_optional_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def parse_time(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return series.map(lambda x: pd.to_datetime(x, utc=True, errors="coerce"))


def as_bool(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        return numeric.fillna(0.0) > 0.5
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def max_drawdown(values: pd.Series) -> float:
    arr = pd.to_numeric(values, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if arr.size == 0:
        return 0.0
    equity = np.concatenate([[0.0], np.cumsum(arr)])
    return float(np.min(equity - np.maximum.accumulate(equity)))


def sharpe(values: pd.Series) -> float:
    arr = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if arr.size < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    if sd <= 1e-12 or not math.isfinite(sd):
        return 0.0
    return float(np.mean(arr) / sd * math.sqrt(arr.size))


def normalize_official(df: pd.DataFrame, ledger: str) -> pd.DataFrame:
    if "ledger" not in df.columns:
        return pd.DataFrame()
    work = df[df["ledger"].astype(str).eq(ledger)].copy()
    if work.empty:
        return work
    valid = work["official_result"].astype(str).str.lower().isin({"yes", "no"})
    work = work[valid].copy()
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    work["market_ticker"] = work["market_ticker"].astype(str).str.upper()
    work["side"] = work["side"].astype(str).str.lower()
    work["key"] = work["market_ticker"] + "|" + work["side"]
    work["event_key"] = work["event_ticker"]
    work["ledger_entry_time"] = parse_time(work.get("created_at", pd.Series(index=work.index, dtype=str)))
    work["ledger_entry_price"] = pd.to_numeric(work.get("entry_price"), errors="coerce")
    work["ledger_pnl"] = pd.to_numeric(work.get("official_pnl"), errors="coerce")
    work["ledger_win"] = as_bool(work.get("official_win", pd.Series(False, index=work.index)))
    return work.sort_values(["ledger_entry_time", "market_ticker", "side"]).reset_index(drop=True)


def normalize_replay(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if work.empty:
        for col in (
            "event_ticker",
            "market_ticker",
            "side",
            "key",
            "event_key",
            "replay_entry_time",
            "replay_entry_price",
            "replay_pnl",
            "replay_win",
        ):
            if col not in work.columns:
                work[col] = pd.Series(dtype=object)
        return work
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    work["market_ticker"] = work["market_ticker"].astype(str).str.upper()
    work["side"] = work["side"].astype(str).str.lower()
    work["key"] = work["market_ticker"] + "|" + work["side"]
    work["event_key"] = work["event_ticker"]
    work["replay_entry_time"] = parse_time(work.get("entry_time", pd.Series(index=work.index, dtype=str)))
    work["replay_entry_price"] = pd.to_numeric(work.get("entry_price"), errors="coerce")
    work["replay_pnl"] = pd.to_numeric(work.get("pnl"), errors="coerce")
    work["replay_win"] = as_bool(work.get("win", pd.Series(False, index=work.index)))
    return work.sort_values(["replay_entry_time", "market_ticker", "side"]).reset_index(drop=True)


def unique_text(values: pd.Series) -> str:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() == "nan" or text in seen:
            continue
        seen.append(text)
    return ";".join(seen)


def market_side_key(market: object, side: object) -> str:
    market_text = str(market or "").upper()
    side_text = str(side or "").lower()
    if not market_text or market_text == "NAN" or not side_text or side_text == "nan":
        return ""
    return f"{market_text}|{side_text}"


def add_join_keys(df: pd.DataFrame, market_col: str = "market_ticker", side_col: str = "side") -> pd.DataFrame:
    work = df.copy()
    if market_col not in work.columns or side_col not in work.columns:
        work["join_key"] = ""
        return work
    work[market_col] = work[market_col].astype(str).str.upper()
    work[side_col] = work[side_col].astype(str).str.lower()
    work["join_key"] = [market_side_key(market, side) for market, side in zip(work[market_col], work[side_col])]
    return work


def summarize_selected_chain(chain: pd.DataFrame) -> pd.DataFrame:
    if chain.empty or "market_ticker" not in chain.columns or "side" not in chain.columns:
        return pd.DataFrame()
    work = add_join_keys(chain)
    work = work[work["join_key"].ne("")].copy()
    if work.empty:
        return pd.DataFrame()
    action = work.get("decision_action", pd.Series("", index=work.index)).astype(str).str.lower()
    work["_is_fill"] = action.eq("paper_fill")
    work["_is_skip"] = action.eq("skip")
    for col in ("selected_received_at_utc", "decision_received_at_utc"):
        if col in work.columns:
            work[col] = parse_time(work[col])
    rows: list[dict[str, object]] = []
    for key, group in work.groupby("join_key", sort=False):
        rows.append(
            {
                "join_key": key,
                "captured_selected_scan_rows": int(len(group)),
                "captured_selected_fill_rows": int(group["_is_fill"].sum()),
                "captured_selected_skip_rows": int(group["_is_skip"].sum()),
                "captured_selected_actions": unique_text(group.get("selected_action", pd.Series("", index=group.index))),
                "captured_decision_actions": unique_text(group.get("decision_action", pd.Series("", index=group.index))),
                "captured_decision_details": unique_text(group.get("decision_detail", pd.Series("", index=group.index))),
                "captured_selected_first_utc": group.get("selected_received_at_utc", pd.Series(index=group.index, dtype=str)).min(),
                "captured_selected_last_utc": group.get("selected_received_at_utc", pd.Series(index=group.index, dtype=str)).max(),
                "captured_selected_min_entry_price": pd.to_numeric(
                    group.get("selected_entry_price", pd.Series(index=group.index, dtype=float)),
                    errors="coerce",
                ).min(),
                "captured_selected_max_entry_price": pd.to_numeric(
                    group.get("selected_entry_price", pd.Series(index=group.index, dtype=float)),
                    errors="coerce",
                ).max(),
                "captured_selected_max_net_edge_cents": pd.to_numeric(
                    group.get("selected_net_edge_cents", pd.Series(index=group.index, dtype=float)),
                    errors="coerce",
                ).max(),
            }
        )
    return pd.DataFrame(rows)


def summarize_fill_chain(chain: pd.DataFrame) -> pd.DataFrame:
    if chain.empty or "market_ticker" not in chain.columns or "side" not in chain.columns:
        return pd.DataFrame()
    work = add_join_keys(chain)
    work = work[work["join_key"].ne("")].copy()
    if work.empty:
        return pd.DataFrame()
    if "decision_received_at_utc" in work.columns:
        work["decision_received_at_utc"] = parse_time(work["decision_received_at_utc"])
    rows: list[dict[str, object]] = []
    for key, group in work.groupby("join_key", sort=False):
        first = group.sort_values("decision_received_at_utc") if "decision_received_at_utc" in group.columns else group
        first_row = first.iloc[0]
        rows.append(
            {
                "join_key": key,
                "captured_fill_decision_rows": int(len(group)),
                "captured_fill_decision_action": str(first_row.get("decision_action", "")),
                "captured_fill_decision_utc": first_row.get("decision_received_at_utc", ""),
                "captured_fill_decision_entry_price": first_row.get("decision_entry_price", np.nan),
                "captured_fill_decision_detail": str(first_row.get("decision_detail", "")),
                "captured_fill_quote_age_ms": first_row.get("quote_age_ms", np.nan),
                "captured_fill_top_visible_qty": first_row.get("top_visible_qty", np.nan),
                "captured_official_created_at": str(first_row.get("created_at", "")),
            }
        )
    return pd.DataFrame(rows)


def summarize_event_context(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if df.empty or "event_ticker" not in df.columns or "market_ticker" not in df.columns or "side" not in df.columns:
        return pd.DataFrame()
    work = add_join_keys(df)
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    rows: list[dict[str, object]] = []
    for event, group in work.groupby("event_ticker", sort=False):
        rows.append(
            {
                "event_ticker": event,
                f"{prefix}_same_event_rows": int(len(group)),
                f"{prefix}_same_event_keys": unique_text(group["join_key"]),
            }
        )
    return pd.DataFrame(rows)


def summarize_selected_event_context(chain: pd.DataFrame) -> pd.DataFrame:
    if chain.empty or "event_ticker" not in chain.columns:
        return pd.DataFrame()
    work = add_join_keys(chain)
    work["event_ticker"] = work["event_ticker"].astype(str).str.upper()
    action = work.get("decision_action", pd.Series("", index=work.index)).astype(str).str.lower()
    work["_is_fill"] = action.eq("paper_fill")
    work["_is_skip"] = action.eq("skip")
    rows: list[dict[str, object]] = []
    for event, group in work.groupby("event_ticker", sort=False):
        rows.append(
            {
                "event_ticker": event,
                "captured_event_selected_scan_rows": int(len(group)),
                "captured_event_fill_rows": int(group["_is_fill"].sum()),
                "captured_event_skip_rows": int(group["_is_skip"].sum()),
                "captured_event_selected_keys": unique_text(group["join_key"]),
                "captured_event_decision_actions": unique_text(group.get("decision_action", pd.Series("", index=group.index))),
            }
        )
    return pd.DataFrame(rows)


def build_detail(ledger: pd.DataFrame, replay: pd.DataFrame) -> pd.DataFrame:
    ledger_cols = [
        "event_ticker",
        "market_ticker",
        "side",
        "key",
        "ledger_entry_time",
        "ledger_entry_price",
        "ledger_pnl",
        "official_result",
        "proxy_result",
        "official_proxy_result_mismatch",
    ]
    replay_cols = [
        "event_ticker",
        "market_ticker",
        "side",
        "key",
        "replay_entry_time",
        "replay_entry_price",
        "replay_pnl",
        "official_result",
        "result_source",
    ]
    left = ledger[[c for c in ledger_cols if c in ledger.columns]].copy()
    right = replay[[c for c in replay_cols if c in replay.columns]].copy()
    merged = left.merge(right, on="key", how="outer", suffixes=("_ledger", "_replay"), indicator=True)
    merged["status"] = merged["_merge"].map(
        {
            "both": "exact_market_side_match",
            "left_only": "ledger_only_missing_from_replay",
            "right_only": "replay_only_extra_vs_ledger",
        }
    ).astype(str)
    merged["event_ticker"] = merged["event_ticker_ledger"].fillna(merged["event_ticker_replay"])
    merged["market_ticker_ledger"] = merged["market_ticker_ledger"].fillna("")
    merged["market_ticker_replay"] = merged["market_ticker_replay"].fillna("")
    merged["side_ledger"] = merged["side_ledger"].fillna("")
    merged["side_replay"] = merged["side_replay"].fillna("")
    merged["entry_price_abs_diff"] = (
        pd.to_numeric(merged.get("ledger_entry_price"), errors="coerce")
        - pd.to_numeric(merged.get("replay_entry_price"), errors="coerce")
    ).abs()
    merged["pnl_diff_replay_minus_ledger"] = (
        pd.to_numeric(merged.get("replay_pnl"), errors="coerce")
        - pd.to_numeric(merged.get("ledger_pnl"), errors="coerce")
    )
    merged["entry_time_diff_sec"] = (
        parse_time(merged.get("replay_entry_time", pd.Series(index=merged.index, dtype=str)))
        - parse_time(merged.get("ledger_entry_time", pd.Series(index=merged.index, dtype=str)))
    ).dt.total_seconds()

    ledger_by_event = ledger.groupby("event_key")["key"].apply(set).to_dict()
    replay_by_event = replay.groupby("event_key")["key"].apply(set).to_dict()
    replacement_events = {
        event
        for event in set(ledger_by_event).intersection(replay_by_event)
        if ledger_by_event[event].isdisjoint(replay_by_event[event])
    }
    merged["event_replacement"] = merged["event_ticker"].isin(replacement_events)
    merged.loc[
        merged["event_replacement"] & merged["status"].ne("exact_market_side_match"),
        "status",
    ] = merged["status"] + "_event_replacement"
    keep = [
        "status",
        "event_ticker",
        "key",
        "market_ticker_ledger",
        "side_ledger",
        "ledger_entry_time",
        "ledger_entry_price",
        "ledger_pnl",
        "official_result_ledger",
        "proxy_result",
        "official_proxy_result_mismatch",
        "market_ticker_replay",
        "side_replay",
        "replay_entry_time",
        "replay_entry_price",
        "replay_pnl",
        "official_result_replay",
        "result_source",
        "entry_price_abs_diff",
        "pnl_diff_replay_minus_ledger",
        "entry_time_diff_sec",
    ]
    return merged[[c for c in keep if c in merged.columns]].sort_values(["event_ticker", "status", "key"]).reset_index(drop=True)


def build_mismatch_diagnosis(
    detail: pd.DataFrame,
    ledger: pd.DataFrame,
    replay: pd.DataFrame,
    selected_chain: pd.DataFrame,
    fill_chain: pd.DataFrame,
) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    status = detail["status"].astype(str)
    entry_diff = pd.to_numeric(detail.get("entry_price_abs_diff"), errors="coerce").fillna(0.0).abs()
    pnl_diff = pd.to_numeric(detail.get("pnl_diff_replay_minus_ledger"), errors="coerce").fillna(0.0).abs()
    work = detail[(status.ne("exact_market_side_match")) | (entry_diff > 1e-9) | (pnl_diff > 1e-9)].copy()
    if work.empty:
        return work

    work["ledger_join_key"] = [
        market_side_key(market, side)
        for market, side in zip(work.get("market_ticker_ledger", ""), work.get("side_ledger", ""))
    ]
    work["replay_join_key"] = [
        market_side_key(market, side)
        for market, side in zip(work.get("market_ticker_replay", ""), work.get("side_replay", ""))
    ]
    work["diagnosis_join_key"] = work["ledger_join_key"].where(work["ledger_join_key"].ne(""), work["replay_join_key"])

    selected_summary = summarize_selected_chain(selected_chain)
    fill_summary = summarize_fill_chain(fill_chain)
    for summary_df in (selected_summary, fill_summary):
        if not summary_df.empty:
            work = work.merge(summary_df, left_on="diagnosis_join_key", right_on="join_key", how="left").drop(
                columns=["join_key"], errors="ignore"
            )

    ledger_event = summarize_event_context(ledger, "ledger")
    replay_event = summarize_event_context(replay, "replay")
    selected_event = summarize_selected_event_context(selected_chain)
    for event_df in (ledger_event, replay_event, selected_event):
        if not event_df.empty:
            work = work.merge(event_df, on="event_ticker", how="left")

    def numeric(row: pd.Series, col: str) -> float:
        try:
            value = float(row.get(col, 0.0))
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    def classify(row: pd.Series) -> str:
        row_status = str(row.get("status", ""))
        same_event_replay_rows = numeric(row, "replay_same_event_rows")
        same_event_ledger_rows = numeric(row, "ledger_same_event_rows")
        captured_fill_rows = numeric(row, "captured_fill_decision_rows")
        selected_rows = numeric(row, "captured_selected_scan_rows")
        skip_rows = numeric(row, "captured_selected_skip_rows")
        if row_status.startswith("ledger_only") and same_event_replay_rows > 0:
            return "captured_live_fill_replaced_by_replay_market"
        if row_status.startswith("ledger_only") and captured_fill_rows > 0 and selected_rows > 1 and skip_rows > 0:
            return "captured_live_fill_missing_after_skip_then_fill"
        if row_status.startswith("ledger_only") and captured_fill_rows > 0:
            return "captured_live_fill_missing_from_replay"
        if row_status.startswith("ledger_only"):
            return "ledger_row_missing_from_replay"
        if row_status.startswith("replay_only") and same_event_ledger_rows > 0:
            return "replay_extra_market_replacing_captured_live_fill"
        if row_status.startswith("replay_only"):
            return "replay_extra_row_without_ledger_fill"
        if row_status == "exact_market_side_match" and (
            numeric(row, "entry_price_abs_diff") > 1e-9 or abs(numeric(row, "pnl_diff_replay_minus_ledger")) > 1e-9
        ):
            return "matched_market_with_entry_or_pnl_drift"
        return "row_fidelity_issue"

    def note(row: pd.Series) -> str:
        label = str(row.get("diagnosis", ""))
        if label == "captured_live_fill_replaced_by_replay_market":
            return "actual paper fill exists, but replay chose a different market in the same event"
        if label == "captured_live_fill_missing_after_skip_then_fill":
            return "actual paper fill followed an earlier captured skip for the same market; replay missed the later fill"
        if label == "captured_live_fill_missing_from_replay":
            return "actual paper fill is present in captured decision chain but absent from replay"
        if label == "replay_extra_market_replacing_captured_live_fill":
            return "replay emitted an extra market where the actual ledger filled another market in the same event"
        if label == "matched_market_with_entry_or_pnl_drift":
            return "market and side match, but replay entry price or PnL is not row-for-row equal"
        return "row-level replay fidelity mismatch"

    work["diagnosis"] = work.apply(classify, axis=1)
    work["diagnosis_note"] = work.apply(note, axis=1)
    preferred = [
        "diagnosis",
        "diagnosis_note",
        "status",
        "event_ticker",
        "ledger_join_key",
        "replay_join_key",
        "ledger_entry_time",
        "replay_entry_time",
        "entry_time_diff_sec",
        "ledger_entry_price",
        "replay_entry_price",
        "entry_price_abs_diff",
        "ledger_pnl",
        "replay_pnl",
        "pnl_diff_replay_minus_ledger",
        "captured_selected_scan_rows",
        "captured_selected_fill_rows",
        "captured_selected_skip_rows",
        "captured_decision_actions",
        "captured_decision_details",
        "captured_fill_decision_rows",
        "captured_fill_decision_utc",
        "captured_fill_decision_entry_price",
        "captured_fill_quote_age_ms",
        "captured_fill_top_visible_qty",
        "ledger_same_event_rows",
        "ledger_same_event_keys",
        "replay_same_event_rows",
        "replay_same_event_keys",
        "captured_event_selected_scan_rows",
        "captured_event_fill_rows",
        "captured_event_skip_rows",
        "captured_event_selected_keys",
    ]
    return work[[c for c in preferred if c in work.columns]].reset_index(drop=True)


def build_summary(
    ledger: pd.DataFrame,
    replay: pd.DataFrame,
    detail: pd.DataFrame,
    replay_evidence_kind: str = "independent_counterfactual",
) -> pd.DataFrame:
    exact_matches = int(detail["status"].astype(str).eq("exact_market_side_match").sum()) if not detail.empty else 0
    ledger_only = int(detail["status"].astype(str).str.startswith("ledger_only").sum()) if not detail.empty else 0
    replay_only = int(detail["status"].astype(str).str.startswith("replay_only").sum()) if not detail.empty else 0
    event_replacements = int(detail["status"].astype(str).str.contains("event_replacement").sum()) if not detail.empty else 0
    actual_rows = int(len(ledger))
    replay_rows = int(len(replay))
    actual_pnl = float(ledger["ledger_pnl"].sum()) if actual_rows else 0.0
    replay_pnl = float(replay["replay_pnl"].sum()) if replay_rows else 0.0
    actual_win_rate = float(ledger["ledger_win"].mean()) if actual_rows else 0.0
    replay_win_rate = float(replay["replay_win"].mean()) if replay_rows else 0.0
    exact_rate = exact_matches / actual_rows if actual_rows else 0.0
    entry_price_drift_rows = (
        int(pd.to_numeric(detail.get("entry_price_abs_diff"), errors="coerce").fillna(0.0).abs().gt(1e-9).sum())
        if not detail.empty
        else 0
    )
    pnl_drift_rows = (
        int(pd.to_numeric(detail.get("pnl_diff_replay_minus_ledger"), errors="coerce").fillna(0.0).abs().gt(1e-9).sum())
        if not detail.empty
        else 0
    )
    row_fidelity_blockers = []
    if actual_rows == 0:
        row_fidelity_blockers.append("no_actual_official_ledger_rows")
    if exact_matches != actual_rows:
        row_fidelity_blockers.append("missing_actual_rows")
    if replay_rows != actual_rows:
        row_fidelity_blockers.append("replay_row_count_differs")
    if replay_only:
        row_fidelity_blockers.append("extra_replay_rows")
    if event_replacements:
        row_fidelity_blockers.append("event_level_market_replacements")
    if entry_price_drift_rows:
        row_fidelity_blockers.append("entry_price_not_row_for_row_equal")
    if abs(replay_pnl - actual_pnl) > 1e-9:
        row_fidelity_blockers.append("pnl_not_row_for_row_equal")
    elif pnl_drift_rows:
        row_fidelity_blockers.append("pnl_path_not_row_for_row_equal")
    evidence_kind = str(replay_evidence_kind or "independent_counterfactual").strip()
    row_fidelity_exact = bool(actual_rows > 0 and not row_fidelity_blockers)
    blockers = list(row_fidelity_blockers)
    if row_fidelity_exact and evidence_kind != "independent_counterfactual":
        blockers.append("diagnostic_replay_not_independent_counterfactual")
    promotion_usable = bool(row_fidelity_exact and evidence_kind == "independent_counterfactual")
    return pd.DataFrame(
        [
            {
                "replay_evidence_kind": evidence_kind,
                "actual_rows": actual_rows,
                "replay_rows": replay_rows,
                "exact_market_side_matches": exact_matches,
                "ledger_only_rows": ledger_only,
                "replay_only_rows": replay_only,
                "event_replacement_rows": event_replacements,
                "entry_price_drift_rows": entry_price_drift_rows,
                "pnl_drift_rows": pnl_drift_rows,
                "exact_match_rate_vs_actual": round(exact_rate, 6),
                "actual_official_pnl": round(actual_pnl, 6),
                "replay_pnl": round(replay_pnl, 6),
                "replay_minus_actual_pnl": round(replay_pnl - actual_pnl, 6),
                "actual_win_rate": round(actual_win_rate, 6),
                "replay_win_rate": round(replay_win_rate, 6),
                "actual_max_drawdown": round(max_drawdown(ledger["ledger_pnl"]), 6) if actual_rows else 0.0,
                "replay_max_drawdown": round(max_drawdown(replay["replay_pnl"]), 6) if replay_rows else 0.0,
                "actual_sharpe": round(sharpe(ledger["ledger_pnl"]), 6) if actual_rows else 0.0,
                "replay_sharpe": round(sharpe(replay["replay_pnl"]), 6) if replay_rows else 0.0,
                "row_fidelity_exact": row_fidelity_exact,
                "row_fidelity_blockers": ";".join(row_fidelity_blockers),
                "promotion_usable_replay": promotion_usable,
                "blockers": ";".join(blockers),
            }
        ]
    )


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

    ledger = normalize_official(read_csv(args.official_trades), args.ledger)
    replay = normalize_replay(read_csv(args.replay_trades))
    selected_chain = read_optional_csv(args.selected_decision_chain)
    fill_chain = read_optional_csv(args.fill_official_chain)
    detail = build_detail(ledger, replay)
    diagnosis = build_mismatch_diagnosis(detail, ledger, replay, selected_chain, fill_chain)
    summary = build_summary(ledger, replay, detail, args.replay_evidence_kind)

    summary_path = args.out_dir / "btc1h_replay_vs_ledger_reconciliation_summary.csv"
    detail_path = args.out_dir / "btc1h_replay_vs_ledger_reconciliation_rows.csv"
    diagnosis_path = args.out_dir / "btc1h_replay_vs_ledger_mismatch_diagnosis.csv"
    summary.to_csv(summary_path, index=False)
    detail.to_csv(detail_path, index=False)
    diagnosis.to_csv(diagnosis_path, index=False)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": args.ledger,
        "official_trades": str(args.official_trades),
        "replay_trades": str(args.replay_trades),
        "replay_evidence_kind": args.replay_evidence_kind,
        "selected_decision_chain": str(args.selected_decision_chain),
        "fill_official_chain": str(args.fill_official_chain),
        "out_dir": str(args.out_dir),
        "scope": "btc1h_active_policy_replay_vs_actual_shadow_ledger",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    interesting = detail[detail["status"].astype(str).ne("exact_market_side_match")].copy()
    report = [
        "# BTC1H Replay vs Ledger Reconciliation",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Ledger: `{args.ledger}`",
        "",
        "## Summary",
        "",
        markdown_table(summary),
        "",
        "## Non-Matching Rows",
        "",
        markdown_table(
            interesting[
                [
                    c
                    for c in [
                        "status",
                        "event_ticker",
                        "market_ticker_ledger",
                        "side_ledger",
                        "ledger_entry_price",
                        "ledger_pnl",
                        "market_ticker_replay",
                        "side_replay",
                        "replay_entry_price",
                        "replay_pnl",
                    ]
                    if c in interesting.columns
                ]
            ]
        ),
        "",
        "## Mismatch Diagnosis",
        "",
        markdown_table(
            diagnosis[
                [
                    c
                    for c in [
                        "diagnosis",
                        "status",
                        "event_ticker",
                        "ledger_join_key",
                        "replay_join_key",
                        "entry_time_diff_sec",
                        "entry_price_abs_diff",
                        "pnl_diff_replay_minus_ledger",
                        "captured_selected_scan_rows",
                        "captured_selected_fill_rows",
                        "captured_selected_skip_rows",
                        "captured_decision_actions",
                        "ledger_same_event_keys",
                        "replay_same_event_keys",
                    ]
                    if c in diagnosis.columns
                ]
            ]
            if not diagnosis.empty
            else diagnosis
        ),
        "",
        "## Interpretation",
        "",
        "- This audit requires exact market+side, entry-price, and PnL parity between replay and actual paper ledger rows.",
        "- A positive replay PnL is not promotion evidence unless `promotion_usable_replay` is true.",
        "- Event-level replacements are especially important because they can change strikes while preserving a similar-looking event count.",
        "- Mismatch diagnosis joins against the captured selected-signal and decision chains when those artifacts are available.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
