#!/usr/bin/env python3
"""Build a fixed multi-holdout BTC1H strategy research report.

This script does not search thresholds.  It consolidates already-produced
faithful-enough BTC1H trade artifacts:

- Predexon orderbook snapshot replays for historical research.
- Captured live-websocket replays for cadence/stability checks.
- Official-settled paper-shadow ledgers for forward evidence.

Historical/proxy rows are useful for research only.  A strategy remains
undeployable unless forward official settlement, live/paper ledger parity, and
execution-realism gates pass.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars  # noqa: E402


BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc1h_multi_holdout_research_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
PRIMARY_VARIANTS = [
    "high_conf_80",
    "high_conf_80_no_chase",
    "high_conf_80_entry70_no_chase",
    "high_conf_80_entry59_70_no_chase",
]
ACTIVE_SHADOW_LEDGER = "btc1h_high_conf80_entry70_no_chase_shadow"
LEDGER_TO_VARIANT = {
    ACTIVE_SHADOW_LEDGER: "high_conf_80_entry70_no_chase",
}
NEAR_DEPLOYABLE_ALLOWED_BLOCKERS = {
    "too_few_forward_official_rows",
    "needs_full_counterfactual_replay_or_live_execution_gate",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stress-cents", type=float, default=2.0)
    parser.add_argument("--min-forward-official-rows", type=int, default=50)
    parser.add_argument("--max-forward-mismatch-rate", type=float, default=0.02)
    parser.add_argument("--robustness-trades", type=Path, default=None)
    parser.add_argument("--direct-trades", type=Path, default=None)
    parser.add_argument("--derived-entry59-trades", type=Path, default=None)
    parser.add_argument("--shadow-official-trades", type=Path, default=None)
    parser.add_argument("--forward-fidelity-summary", type=Path, default=None)
    parser.add_argument("--selected-model-parity-summary", type=Path, default=None)
    parser.add_argument("--replay-ledger-reconciliation-summary", type=Path, default=None)
    return parser.parse_args()


def latest_file(pattern: str, filename: str) -> Path | None:
    matches = [p / filename for p in BACKTEST_ROOT.glob(pattern) if (p / filename).exists()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def default_paths(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        "robustness_trades": args.robustness_trades
        or latest_file("btc1h_highconf_robustness_*", "all_input_trades.csv"),
        "direct_trades": args.direct_trades
        or latest_file("btc1h_highconf_direct_aggregate_feb09_may06_*", "trades.csv"),
        "derived_entry59_trades": args.derived_entry59_trades
        or latest_file("btc1h_entry59_70_derived_*", "derived_trades.csv"),
        "shadow_official_trades": args.shadow_official_trades
        or latest_file("remote_btc_shadow_official_settlement_latest_codex", "shadow_official_trades.csv")
        or latest_file("btc_shadow_official_settlement_latest_codex", "shadow_official_trades.csv"),
        "forward_fidelity_summary": args.forward_fidelity_summary
        or latest_file("btc1h_forward_snapshot_signal_audit_latest_codex", "btc1h_forward_snapshot_fidelity_summary.csv"),
        "selected_model_parity_summary": args.selected_model_parity_summary
        or latest_file("btc1h_selected_signal_model_parity_latest_codex", "btc1h_selected_signal_model_parity_summary.csv"),
        "replay_ledger_reconciliation_summary": args.replay_ledger_reconciliation_summary
        or latest_file("btc1h_replay_vs_ledger_reconciliation_latest_codex", "btc1h_replay_vs_ledger_reconciliation_summary.csv"),
    }


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def parse_time(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return series.map(lambda x: pd.to_datetime(x, utc=True, errors="coerce"))


def max_drawdown(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0
    equity = np.cumsum(arr)
    return float(np.min(equity - np.maximum.accumulate(equity)))


def sharpe(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12 or not math.isfinite(std):
        return 0.0
    return float(np.mean(arr) / std * math.sqrt(arr.size))


def as_bool(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        return numeric.fillna(0.0) > 0.5
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def historical_pnl_with_stress(df: pd.DataFrame, stress_cents: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    entry = (pd.to_numeric(df["entry_price"], errors="coerce") + stress_cents / 100.0).clip(upper=0.99)
    fee = entry.map(lambda x: kalshi_fee_dollars(float(x), contracts=1, liquidity="taker"))
    premium = entry + fee
    if "win_bool" in df.columns:
        win = as_bool(df["win_bool"])
    elif "win" in df.columns:
        win = as_bool(df["win"])
    else:
        win = pd.to_numeric(df.get("pnl", 0.0), errors="coerce").fillna(0.0) > 0
    pnl = pd.Series(np.where(win.to_numpy(), 1.0 - premium.to_numpy(), -premium.to_numpy()), index=df.index)
    return pnl.astype(float), premium.astype(float), win.astype(bool)


def summarize_group(group: pd.DataFrame, pnl: pd.Series, premium: pd.Series, win: pd.Series) -> dict[str, object]:
    if group.empty:
        return {
            "trades": 0,
            "pnl": 0.0,
            "premium": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "sharpe": 0.0,
        }
    gpnl = pnl.loc[group.index].astype(float)
    gprem = premium.loc[group.index].astype(float)
    gwin = win.loc[group.index].astype(bool)
    total_premium = float(gprem.sum())
    return {
        "trades": int(len(group)),
        "pnl": round(float(gpnl.sum()), 4),
        "premium": round(total_premium, 4),
        "rop": round(float(gpnl.sum()) / total_premium, 4) if total_premium > 0 else 0.0,
        "win_rate": round(float(gwin.mean()), 4) if len(gwin) else 0.0,
        "max_dd": round(max_drawdown(gpnl), 4),
        "sharpe": round(sharpe(gpnl), 4),
        "avg_entry": round(float(pd.to_numeric(group["entry_price"], errors="coerce").mean()), 4)
        if "entry_price" in group
        else "",
        "first_entry": str(group["entry_time"].min()) if "entry_time" in group else "",
        "last_entry": str(group["entry_time"].max()) if "entry_time" in group else "",
    }


def historical_holdout_label(row: pd.Series) -> str:
    source = str(row.get("source", ""))
    dataset = str(row.get("dataset", ""))
    cadence = row.get("cadence_sec", "")
    if source == "websocket":
        try:
            return f"H4_live_ws_may06_12_stride{int(float(cadence))}s"
        except Exception:
            return f"H4_live_ws_may06_12_stride{cadence}s"
    if "20260317_20260324" in dataset:
        return "D0_predexon_mar17_24_old_context"
    if "20260324_20260401" in dataset:
        return "D1_predexon_mar24_apr01_dev"
    if "apr1_14" in dataset:
        return "H1_predexon_apr01_14_holdout"
    if "apr15_30" in dataset:
        return "H2_predexon_apr15_30_holdout"
    if "may3_5" in dataset:
        return "H3_predexon_may03_06_external"
    return f"other_{dataset or source}"


def direct_holdout_label(row: pd.Series) -> str:
    split = str(row.get("split", ""))
    mapping = {
        "feb09_mar24_old_val": "D0_direct_feb09_mar24_old_context",
        "mar24_apr01_train": "D1_direct_mar24_apr01_dev",
        "apr01_apr08_train": "H1a_direct_apr01_08_holdout",
        "apr08_apr15_val": "H1b_direct_apr08_15_holdout",
        "apr15_apr23_val": "H2a_direct_apr15_23_holdout",
        "apr23_may01_val": "H2b_direct_apr23_may01_holdout",
        "may03_may06_external": "H3_direct_may03_06_external",
    }
    return mapping.get(split, f"direct_{split}")


def build_historical_summary(df: pd.DataFrame, source_name: str, stress_cents: float, label_func) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "variant" not in work and "model" in work:
        work["variant"] = work["model"]
    work = work[work["variant"].astype(str).isin(PRIMARY_VARIANTS)].copy()
    if work.empty:
        return pd.DataFrame()
    work["entry_time"] = parse_time(work["entry_time"])
    work["holdout"] = work.apply(label_func, axis=1)
    pnl, premium, win = historical_pnl_with_stress(work, stress_cents)
    rows = []
    for (variant, holdout), group in work.groupby(["variant", "holdout"], dropna=False, sort=True):
        row = {
            "evidence_source": source_name,
            "variant": variant,
            "holdout": holdout,
            "stress_cents": stress_cents,
            "settlement_label": "proxy_or_captured_result",
        }
        row.update(summarize_group(group, pnl, premium, win))
        rows.append(row)
    return pd.DataFrame(rows)


def build_forward_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "ledger" not in df:
        return pd.DataFrame()
    work = df.copy()
    work = work[work["ledger"].astype(str).isin(LEDGER_TO_VARIANT)].copy()
    if work.empty:
        return pd.DataFrame()
    work["variant"] = work["ledger"].map(LEDGER_TO_VARIANT)
    work["created_at"] = parse_time(work["created_at"])
    rows = []
    for variant, group in work.groupby("variant", dropna=False, sort=True):
        official = group[group["official_result"].astype(str).str.lower().isin({"yes", "no"})].copy()
        if official.empty:
            continue
        pnl = pd.to_numeric(official["official_pnl"], errors="coerce").fillna(0.0)
        premium = pd.to_numeric(official["official_premium"], errors="coerce").fillna(0.0)
        win = as_bool(official["official_win"])
        proxy_pnl = pd.to_numeric(official.get("proxy_pnl", pd.Series(dtype=float)), errors="coerce")
        proxy_rows = int(proxy_pnl.notna().sum())
        mismatches = as_bool(official.get("official_proxy_result_mismatch", pd.Series(False, index=official.index))).sum()
        total_premium = float(premium.sum())
        rows.append(
            {
                "evidence_source": "forward_shadow_official",
                "variant": variant,
                "holdout": "H5_forward_remote_official_may18_plus",
                "stress_cents": "actual_fee",
                "settlement_label": "kalshi_rest_official",
                "trades": int(len(official)),
                "pnl": round(float(pnl.sum()), 4),
                "premium": round(total_premium, 4),
                "rop": round(float(pnl.sum()) / total_premium, 4) if total_premium > 0 else 0.0,
                "win_rate": round(float(win.mean()), 4) if len(win) else 0.0,
                "max_dd": round(max_drawdown(pnl), 4),
                "sharpe": round(sharpe(pnl), 4),
                "avg_entry": round(float(pd.to_numeric(official["entry_price"], errors="coerce").mean()), 4),
                "first_entry": str(official["created_at"].min()),
                "last_entry": str(official["created_at"].max()),
                "proxy_rows": proxy_rows,
                "proxy_pnl": round(float(proxy_pnl.dropna().sum()), 4) if proxy_rows else "",
                "official_proxy_mismatches": int(mismatches),
                "official_proxy_mismatch_rate": round(float(mismatches) / len(official), 4) if len(official) else 0.0,
                "mean_official_minus_proxy_spot": round(
                    float(pd.to_numeric(official.get("official_minus_proxy_spot", pd.Series(dtype=float)), errors="coerce").mean()),
                    4,
                ),
                "max_abs_official_minus_proxy_spot": round(
                    float(
                        pd.to_numeric(official.get("official_minus_proxy_spot", pd.Series(dtype=float)), errors="coerce")
                        .abs()
                        .max()
                    ),
                    4,
                ),
            }
        )
    return pd.DataFrame(rows)


def positive_holdout_count(summary: pd.DataFrame, variant: str, source_prefix: str | None = None) -> tuple[int, int]:
    rows = summary[summary["variant"].astype(str).eq(variant)].copy()
    if source_prefix:
        rows = rows[rows["evidence_source"].astype(str).str.startswith(source_prefix)].copy()
    holdout_rows = rows[rows["holdout"].astype(str).str.startswith("H")].copy()
    if holdout_rows.empty:
        return 0, 0
    pnl = pd.to_numeric(holdout_rows["pnl"], errors="coerce")
    return int((pnl > 0).sum()), int(len(holdout_rows))


def fidelity_for_variant(fidelity: pd.DataFrame, variant: str) -> dict[str, object]:
    if fidelity.empty or variant != LEDGER_TO_VARIANT.get(ACTIVE_SHADOW_LEDGER):
        return {
            "forward_snapshot_parity_status": "not_checked",
            "forward_snapshot_selected_to_decision_matched": "",
            "forward_snapshot_fill_official_matched": "",
        }
    row = fidelity.iloc[0]
    selected_missing = int(pd.to_numeric(pd.Series([row.get("selected_without_decision", 0)]), errors="coerce").fillna(0).iloc[0])
    decision_extra = int(pd.to_numeric(pd.Series([row.get("decision_without_selected", 0)]), errors="coerce").fillna(0).iloc[0])
    fill_missing = int(pd.to_numeric(pd.Series([row.get("fill_without_official", 0)]), errors="coerce").fillna(0).iloc[0])
    official_extra = int(pd.to_numeric(pd.Series([row.get("official_without_fill", 0)]), errors="coerce").fillna(0).iloc[0])
    status = (
        "pass_actual_shadow_snapshot_parity"
        if selected_missing == 0 and decision_extra == 0 and fill_missing == 0 and official_extra == 0
        else "fail_actual_shadow_snapshot_parity"
    )
    return {
        "forward_snapshot_parity_status": status,
        "forward_snapshot_selected_to_decision_matched": int(
            pd.to_numeric(pd.Series([row.get("selected_to_decision_matched", 0)]), errors="coerce").fillna(0).iloc[0]
        ),
        "forward_snapshot_fill_official_matched": int(
            pd.to_numeric(pd.Series([row.get("fill_decision_to_official_matched", 0)]), errors="coerce").fillna(0).iloc[0]
        ),
    }


def model_parity_for_variant(parity: pd.DataFrame, variant: str) -> dict[str, object]:
    if parity.empty or variant != LEDGER_TO_VARIANT.get(ACTIVE_SHADOW_LEDGER):
        return {
            "selected_signal_model_parity_status": "not_checked",
            "selected_signal_model_parity_pass_rows": "",
            "selected_signal_model_parity_fail_rows": "",
            "selected_signal_model_parity_max_p_abs_diff": "",
            "selected_signal_model_parity_max_edge_abs_diff": "",
            "selected_signal_implied_ttl_pass_rows": "",
            "selected_signal_implied_ttl_max_p_abs_diff": "",
            "selected_signal_implied_ttl_offset_sec_range": "",
            "selected_signal_captured_ttl_available_rows": "",
            "selected_signal_captured_ttl_pass_rows": "",
            "selected_signal_captured_ttl_max_p_abs_diff": "",
            "scan_ttl_recomputed_official_rows": "",
            "scan_ttl_recomputed_official_pnl": "",
            "scan_ttl_no_signal_official_rows": "",
            "scan_ttl_no_signal_official_pnl": "",
        }
    row = parity.iloc[0]
    pass_rows = int(pd.to_numeric(pd.Series([row.get("parity_pass_rows", 0)]), errors="coerce").fillna(0).iloc[0])
    fail_rows = int(pd.to_numeric(pd.Series([row.get("parity_fail_rows", 0)]), errors="coerce").fillna(0).iloc[0])
    implied_pass_rows = int(pd.to_numeric(pd.Series([row.get("implied_ttl_pass_rows", 0)]), errors="coerce").fillna(0).iloc[0])
    captured_available_rows = int(
        pd.to_numeric(pd.Series([row.get("captured_ttl_available_rows", 0)]), errors="coerce").fillna(0).iloc[0]
    )
    captured_pass_rows = int(
        pd.to_numeric(pd.Series([row.get("captured_ttl_pass_rows", 0)]), errors="coerce").fillna(0).iloc[0]
    )
    implied_max_diff = float(
        pd.to_numeric(pd.Series([row.get("max_implied_ttl_model_p_yes_abs_diff", 0)]), errors="coerce").fillna(0).iloc[0]
    )
    captured_max_diff = float(
        pd.to_numeric(pd.Series([row.get("max_captured_ttl_model_p_yes_abs_diff", 0)]), errors="coerce").fillna(0).iloc[0]
    )
    implied_min_offset = pd.to_numeric(pd.Series([row.get("min_implied_ttl_offset_sec", np.nan)]), errors="coerce").iloc[0]
    implied_max_offset = pd.to_numeric(pd.Series([row.get("max_implied_ttl_offset_sec", np.nan)]), errors="coerce").iloc[0]
    scan_ttl_rows = int(pd.to_numeric(pd.Series([row.get("scan_ttl_recomputed_official_rows", 0)]), errors="coerce").fillna(0).iloc[0])
    scan_ttl_pnl = float(pd.to_numeric(pd.Series([row.get("scan_ttl_recomputed_official_pnl", 0)]), errors="coerce").fillna(0).iloc[0])
    no_signal_rows = int(pd.to_numeric(pd.Series([row.get("scan_ttl_no_signal_official_rows", 0)]), errors="coerce").fillna(0).iloc[0])
    no_signal_pnl = float(pd.to_numeric(pd.Series([row.get("scan_ttl_no_signal_official_pnl", 0)]), errors="coerce").fillna(0).iloc[0])
    status = "pass_selected_signal_model_parity" if fail_rows == 0 and pass_rows > 0 else "fail_selected_signal_model_parity"
    offset_range = (
        ""
        if pd.isna(implied_min_offset) or pd.isna(implied_max_offset)
        else f"{int(implied_min_offset)}..{int(implied_max_offset)}"
    )
    return {
        "selected_signal_model_parity_status": status,
        "selected_signal_model_parity_pass_rows": pass_rows,
        "selected_signal_model_parity_fail_rows": fail_rows,
        "selected_signal_model_parity_max_p_abs_diff": round(
            float(pd.to_numeric(pd.Series([row.get("max_model_p_yes_abs_diff", 0)]), errors="coerce").fillna(0).iloc[0]),
            6,
        ),
        "selected_signal_model_parity_max_edge_abs_diff": round(
            float(pd.to_numeric(pd.Series([row.get("max_net_edge_cents_abs_diff", 0)]), errors="coerce").fillna(0).iloc[0]),
            6,
        ),
        "selected_signal_implied_ttl_pass_rows": implied_pass_rows,
        "selected_signal_implied_ttl_max_p_abs_diff": round(implied_max_diff, 6),
        "selected_signal_implied_ttl_offset_sec_range": offset_range,
        "selected_signal_captured_ttl_available_rows": captured_available_rows,
        "selected_signal_captured_ttl_pass_rows": captured_pass_rows,
        "selected_signal_captured_ttl_max_p_abs_diff": round(captured_max_diff, 6),
        "scan_ttl_recomputed_official_rows": scan_ttl_rows,
        "scan_ttl_recomputed_official_pnl": round(scan_ttl_pnl, 6),
        "scan_ttl_no_signal_official_rows": no_signal_rows,
        "scan_ttl_no_signal_official_pnl": round(no_signal_pnl, 6),
    }


def replay_reconciliation_for_variant(reconciliation: pd.DataFrame, variant: str) -> dict[str, object]:
    if reconciliation.empty or variant != LEDGER_TO_VARIANT.get(ACTIVE_SHADOW_LEDGER):
        return {
            "replay_ledger_promotion_usable": "",
            "replay_ledger_actual_rows": "",
            "replay_ledger_replay_rows": "",
            "replay_ledger_exact_matches": "",
            "replay_ledger_exact_match_rate": "",
            "replay_ledger_replay_minus_actual_pnl": "",
            "replay_ledger_blockers": "",
        }
    row = reconciliation.iloc[0]
    promotion_usable = bool(
        str(row.get("promotion_usable_replay", "")).strip().lower() in {"true", "1", "yes"}
        or pd.to_numeric(pd.Series([row.get("promotion_usable_replay", np.nan)]), errors="coerce").fillna(0).iloc[0] > 0.5
    )
    return {
        "replay_ledger_promotion_usable": promotion_usable,
        "replay_ledger_actual_rows": int(pd.to_numeric(pd.Series([row.get("actual_rows", 0)]), errors="coerce").fillna(0).iloc[0]),
        "replay_ledger_replay_rows": int(pd.to_numeric(pd.Series([row.get("replay_rows", 0)]), errors="coerce").fillna(0).iloc[0]),
        "replay_ledger_exact_matches": int(
            pd.to_numeric(pd.Series([row.get("exact_market_side_matches", 0)]), errors="coerce").fillna(0).iloc[0]
        ),
        "replay_ledger_exact_match_rate": round(
            float(pd.to_numeric(pd.Series([row.get("exact_match_rate_vs_actual", 0)]), errors="coerce").fillna(0).iloc[0]),
            6,
        ),
        "replay_ledger_replay_minus_actual_pnl": round(
            float(pd.to_numeric(pd.Series([row.get("replay_minus_actual_pnl", 0)]), errors="coerce").fillna(0).iloc[0]),
            6,
        ),
        "replay_ledger_blockers": str(row.get("blockers", "")),
    }


def promotion_readiness_status(
    *,
    deployable_now: bool,
    near_deployable_candidate: bool,
    research_promising: bool,
    forward_rows: int,
    disqualifying_blockers: list[str],
) -> str:
    if deployable_now:
        return "deployable"
    if near_deployable_candidate:
        return "near_deployable_pending_sample_and_final_execution_gate"
    if research_promising and forward_rows > 0:
        if disqualifying_blockers:
            return "promising_but_blocked_by_official_or_fidelity_gates"
        return "promising_forward_control_not_deployable"
    if research_promising:
        return "historical_promising_needs_forward_official_evidence"
    return "research_watch_or_reject"


def aggregate_by_variant(
    summary: pd.DataFrame,
    args: argparse.Namespace,
    fidelity: pd.DataFrame,
    selected_model_parity: pd.DataFrame,
    replay_reconciliation: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    historical_sources = {
        "robustness_trade_logs",
        "direct_predexon_trade_logs",
        "derived_entry59_trade_logs",
    }
    for variant in PRIMARY_VARIANTS:
        v = summary[summary["variant"].astype(str).eq(variant)].copy()
        hist = v[v["evidence_source"].isin(historical_sources)].copy()
        forward = v[v["evidence_source"].eq("forward_shadow_official")].copy()
        ws = v[v["holdout"].astype(str).str.startswith("H4_live_ws")].copy()
        pred = v[v["holdout"].astype(str).str.contains("predexon|direct", case=False, regex=True)].copy()
        holdout_rows = v[v["holdout"].astype(str).str.startswith("H")].copy()
        negative_holdouts = holdout_rows[pd.to_numeric(holdout_rows["pnl"], errors="coerce").fillna(0.0) <= 0][
            "holdout"
        ].astype(str).tolist()
        hist_pos, hist_total = positive_holdout_count(summary, variant)
        ws_pos = int((pd.to_numeric(ws["pnl"], errors="coerce") > 0).sum()) if not ws.empty else 0
        ws_total = int(len(ws))
        forward_rows = int(pd.to_numeric(forward["trades"], errors="coerce").fillna(0).sum()) if not forward.empty else 0
        forward_pnl = float(pd.to_numeric(forward["pnl"], errors="coerce").fillna(0).sum()) if not forward.empty else 0.0
        mismatch_rate = (
            float(pd.to_numeric(forward["official_proxy_mismatch_rate"], errors="coerce").max())
            if not forward.empty and "official_proxy_mismatch_rate" in forward
            else float("nan")
        )
        deploy_blockers = []
        if forward_rows < args.min_forward_official_rows:
            deploy_blockers.append("too_few_forward_official_rows")
        if forward_pnl <= 0:
            deploy_blockers.append("forward_official_pnl_not_positive")
        if math.isnan(mismatch_rate) or mismatch_rate > args.max_forward_mismatch_rate:
            deploy_blockers.append("official_proxy_mismatch_gate_failed")
        fidelity_row = fidelity_for_variant(fidelity, variant)
        model_parity_row = model_parity_for_variant(selected_model_parity, variant)
        replay_reconciliation_row = replay_reconciliation_for_variant(replay_reconciliation, variant)
        if forward_rows > 0 and fidelity_row["forward_snapshot_parity_status"] == "pass_actual_shadow_snapshot_parity":
            deploy_blockers.append("needs_full_counterfactual_replay_or_live_execution_gate")
        else:
            deploy_blockers.append("needs_snapshot_row_parity_and_full_execution_gate")
        if forward_rows > 0 and replay_reconciliation_row.get("replay_ledger_promotion_usable") is False:
            deploy_blockers.append("counterfactual_replay_not_row_faithful")
        if (
            forward_rows > 0
            and model_parity_row["selected_signal_model_parity_status"] == "fail_selected_signal_model_parity"
        ):
            captured_available = int(model_parity_row.get("selected_signal_captured_ttl_available_rows") or 0)
            captured_pass = int(model_parity_row.get("selected_signal_captured_ttl_pass_rows") or 0)
            fail_rows = int(model_parity_row.get("selected_signal_model_parity_fail_rows") or 0)
            if (
                captured_available > 0
                and captured_available == fail_rows
                and captured_pass == captured_available
            ):
                deploy_blockers.append("selected_signal_exact_recompute_failed_despite_captured_ttl")
            elif (
                str(model_parity_row.get("selected_signal_implied_ttl_pass_rows", ""))
                == str(fail_rows)
                and str(model_parity_row.get("selected_signal_implied_ttl_pass_rows", "")) not in {"", "0"}
            ):
                deploy_blockers.append("selected_signal_exact_cached_ttl_missing_for_promotion")
            else:
                deploy_blockers.append("selected_signal_model_parity_failed_or_exact_model_inputs_missing")
        if int(model_parity_row.get("scan_ttl_no_signal_official_rows") or 0) > 0:
            deploy_blockers.append("forward_rows_include_cached_ttl_only_fills")
        deploy_blockers = list(dict.fromkeys(deploy_blockers))
        near_disqualifying_blockers = [
            blocker for blocker in deploy_blockers if blocker not in NEAR_DEPLOYABLE_ALLOWED_BLOCKERS
        ]
        research_promising = (
            hist_total > 0
            and hist_pos >= max(1, hist_total - 1)
            and (ws_total == 0 or ws_pos == ws_total)
            and (forward_rows == 0 or forward_pnl > 0)
        )
        near_deployable_candidate = bool(
            research_promising
            and forward_rows > 0
            and forward_pnl > 0
            and forward_rows < args.min_forward_official_rows
            and not near_disqualifying_blockers
        )
        readiness_status = promotion_readiness_status(
            deployable_now=False,
            near_deployable_candidate=near_deployable_candidate,
            research_promising=bool(research_promising),
            forward_rows=forward_rows,
            disqualifying_blockers=near_disqualifying_blockers,
        )
        if forward_rows > 0:
            research_status = "active_forward_candidate"
        elif research_promising:
            research_status = "historical_only_promising"
        else:
            research_status = "research_watch_or_reject"
        rows.append(
            {
                "variant": variant,
                "research_status": research_status,
                "research_promising": bool(research_promising),
                "near_deployable_candidate": near_deployable_candidate,
                "promotion_readiness_status": readiness_status,
                "deployable_now": False,
                "deploy_blockers": ";".join(deploy_blockers),
                "near_deployable_disqualifying_blockers": ";".join(near_disqualifying_blockers),
                "all_positive_holdouts": hist_pos,
                "all_holdouts": hist_total,
                "negative_holdouts": ";".join(dict.fromkeys(negative_holdouts)),
                "ws_positive_cadences": ws_pos,
                "ws_cadences": ws_total,
                "forward_official_rows": forward_rows,
                "forward_official_pnl": round(forward_pnl, 4),
                "forward_official_mismatch_rate": "" if math.isnan(mismatch_rate) else round(mismatch_rate, 4),
                **fidelity_row,
                **model_parity_row,
                **replay_reconciliation_row,
                "historical_trades": int(pd.to_numeric(hist["trades"], errors="coerce").fillna(0).sum())
                if not hist.empty
                else 0,
                "historical_pnl_sum": round(float(pd.to_numeric(hist["pnl"], errors="coerce").fillna(0).sum()), 4)
                if not hist.empty
                else 0.0,
                "predexon_like_rows": int(pd.to_numeric(pred["trades"], errors="coerce").fillna(0).sum())
                if not pred.empty
                else 0,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["research_promising", "forward_official_rows", "historical_pnl_sum"],
        ascending=[False, False, False],
    )


def boolish(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def variant_candidate_row(candidates: pd.DataFrame, variant: str) -> dict[str, object]:
    if candidates.empty:
        return {}
    rows = candidates[candidates["variant"].astype(str).eq(variant)]
    if rows.empty:
        return {}
    return rows.iloc[0].to_dict()


def evidence_family(row: pd.Series) -> tuple[str, str]:
    source = str(row.get("evidence_source", ""))
    holdout = str(row.get("holdout", ""))
    settlement = str(row.get("settlement_label", ""))
    if source == "forward_shadow_official" or settlement == "kalshi_rest_official":
        return "official_forward_shadow", "official REST-settled paper shadow"
    if holdout.startswith("H4_live_ws"):
        return "live_ws_replay_research", "captured live-websocket replay with proxy/captured result"
    if source in {"robustness_trade_logs", "direct_predexon_trade_logs", "derived_entry59_trade_logs"}:
        return "historical_predexon_research", "historical Predexon/provider-time snapshot research"
    return "other_research", "research-only source"


def build_holdout_provenance(
    summary: pd.DataFrame,
    candidates: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Classify each holdout row by what it can and cannot prove now."""

    rows: list[dict[str, object]] = []
    for _, row in summary.iterrows():
        variant = str(row.get("variant", ""))
        candidate = variant_candidate_row(candidates, variant)
        family, family_desc = evidence_family(row)
        trades = int(pd.to_numeric(pd.Series([row.get("trades", 0)]), errors="coerce").fillna(0).iloc[0])
        pnl = float(pd.to_numeric(pd.Series([row.get("pnl", 0.0)]), errors="coerce").fillna(0.0).iloc[0])
        mismatch_rate = pd.to_numeric(
            pd.Series([row.get("official_proxy_mismatch_rate", np.nan)]),
            errors="coerce",
        ).iloc[0]
        mismatches = int(
            pd.to_numeric(pd.Series([row.get("official_proxy_mismatches", 0)]), errors="coerce")
            .fillna(0)
            .iloc[0]
        )
        official = family == "official_forward_shadow"
        live_ws = family == "live_ws_replay_research"
        deployable_now = boolish(candidate.get("deployable_now", False))
        near_deployable_now = boolish(candidate.get("near_deployable_candidate", False))
        candidate_blockers = str(candidate.get("deploy_blockers", "")).strip()
        blockers: list[str] = []
        if not official:
            blockers.append("not_kalshi_rest_official_settlement")
        else:
            if trades < args.min_forward_official_rows:
                blockers.append("too_few_official_rows_for_promotion")
            if pd.isna(mismatch_rate):
                blockers.append("official_proxy_mismatch_rate_missing")
            elif float(mismatch_rate) > args.max_forward_mismatch_rate:
                blockers.append("official_proxy_mismatch_rate_above_limit")
            if mismatches > 0:
                blockers.append("official_proxy_mismatches_present")
        if candidate_blockers:
            blockers.extend([part for part in candidate_blockers.split(";") if part])
        if not deployable_now and not near_deployable_now:
            blockers.append("candidate_not_deployable_or_near_deployable")
        blockers = list(dict.fromkeys(blockers))
        near_deployable_countable = official and near_deployable_now
        deployable_countable = official and deployable_now and not blockers
        if deployable_countable:
            current_use = "deployable_evidence"
        elif near_deployable_countable:
            current_use = "near_deployable_evidence"
        elif official:
            current_use = "official_forward_diagnostic_only"
        elif live_ws:
            current_use = "live_ws_stability_research_only"
        else:
            current_use = "historical_proxy_research_only"

        rows.append(
            {
                "variant": variant,
                "evidence_source": row.get("evidence_source", ""),
                "holdout": row.get("holdout", ""),
                "evidence_family": family,
                "evidence_family_description": family_desc,
                "settlement_label": row.get("settlement_label", ""),
                "trades": trades,
                "pnl": round(pnl, 4),
                "win_rate": row.get("win_rate", ""),
                "first_entry": row.get("first_entry", ""),
                "last_entry": row.get("last_entry", ""),
                "official_proxy_mismatches": mismatches if official else "",
                "official_proxy_mismatch_rate": "" if pd.isna(mismatch_rate) else round(float(mismatch_rate), 4),
                "counts_for_research": trades > 0,
                "counts_for_live_ws_stability": live_ws and trades > 0,
                "counts_for_official_forward_diagnostic": official and trades > 0,
                "counts_for_near_deployable_now": near_deployable_countable,
                "counts_for_deployable_now": deployable_countable,
                "current_use": current_use,
                "promotion_blockers": ";".join(blockers),
            }
        )

    provenance = pd.DataFrame(rows)
    summary_rows: list[dict[str, object]] = []
    for variant, group in provenance.groupby("variant", dropna=False, sort=True):
        official_group = group[group["evidence_family"].eq("official_forward_shadow")]
        negative_rows = group[
            group["counts_for_research"].astype(bool)
            & pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0).le(0)
        ]
        current_use_values = list(dict.fromkeys(group["current_use"].astype(str).tolist()))
        deployable_rows = int(group["counts_for_deployable_now"].astype(bool).sum())
        near_rows = int(group["counts_for_near_deployable_now"].astype(bool).sum())
        if deployable_rows:
            status = "HAS_DEPLOYABLE_HOLDOUT_EVIDENCE"
        elif near_rows:
            status = "HAS_NEAR_DEPLOYABLE_HOLDOUT_EVIDENCE"
        elif not official_group.empty:
            status = "RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY"
        else:
            status = "RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE"
        summary_rows.append(
            {
                "variant": variant,
                "holdout_rows": int(len(group)),
                "research_countable_rows": int(group["counts_for_research"].astype(bool).sum()),
                "live_ws_stability_rows": int(group["counts_for_live_ws_stability"].astype(bool).sum()),
                "official_forward_diagnostic_rows": int(
                    group["counts_for_official_forward_diagnostic"].astype(bool).sum()
                ),
                "near_deployable_countable_rows": near_rows,
                "deployable_countable_rows": deployable_rows,
                "official_forward_trades": int(
                    pd.to_numeric(official_group["trades"], errors="coerce").fillna(0).sum()
                )
                if not official_group.empty
                else 0,
                "official_proxy_mismatch_rows": int(
                    pd.to_numeric(official_group["official_proxy_mismatches"], errors="coerce").fillna(0).sum()
                )
                if not official_group.empty
                else 0,
                "negative_research_holdouts": ";".join(
                    dict.fromkeys(negative_rows["holdout"].astype(str).tolist())
                ),
                "current_uses": ";".join(current_use_values),
                "holdout_evidence_status": status,
            }
        )
    provenance_summary = pd.DataFrame(summary_rows)
    return provenance, provenance_summary


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
    paths = default_paths(args)

    robustness = read_csv(paths["robustness_trades"])
    direct = read_csv(paths["direct_trades"])
    derived = read_csv(paths["derived_entry59_trades"])
    shadow = read_csv(paths["shadow_official_trades"])
    fidelity = read_csv(paths["forward_fidelity_summary"])
    selected_model_parity = read_csv(paths["selected_model_parity_summary"])
    replay_reconciliation = read_csv(paths["replay_ledger_reconciliation_summary"])

    summaries = [
        build_historical_summary(robustness, "robustness_trade_logs", args.stress_cents, historical_holdout_label),
        build_historical_summary(direct, "direct_predexon_trade_logs", args.stress_cents, direct_holdout_label),
        build_historical_summary(derived, "derived_entry59_trade_logs", args.stress_cents, historical_holdout_label),
        build_forward_summary(shadow),
    ]
    summary = pd.concat([s for s in summaries if not s.empty], ignore_index=True, sort=False)
    summary = summary.sort_values(["variant", "evidence_source", "holdout"]).reset_index(drop=True)
    candidates = aggregate_by_variant(summary, args, fidelity, selected_model_parity, replay_reconciliation)
    provenance, provenance_summary = build_holdout_provenance(summary, candidates, args)

    summary.to_csv(args.out_dir / "btc1h_multi_holdout_summary.csv", index=False)
    candidates.to_csv(args.out_dir / "btc1h_candidate_gate_summary.csv", index=False)
    provenance.to_csv(args.out_dir / "btc1h_holdout_provenance.csv", index=False)
    provenance_summary.to_csv(args.out_dir / "btc1h_holdout_provenance_summary.csv", index=False)
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stress_cents": args.stress_cents,
        "min_forward_official_rows": args.min_forward_official_rows,
        "max_forward_mismatch_rate": args.max_forward_mismatch_rate,
        "paths": {key: str(value) if value is not None else "" for key, value in paths.items()},
        "note": "Research artifact only. Historical rows are proxy/captured-result evidence; forward official rows are required for deployment gates.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    interesting = summary[
        summary["variant"].isin(["high_conf_80_entry70_no_chase", "high_conf_80", "high_conf_80_no_chase"])
    ].copy()
    report = [
        "# BTC1H Multi-Holdout Research",
        "",
        f"Created UTC: `{meta['created_at_utc']}`",
        f"Stress on historical/proxy rows: `+{args.stress_cents:.1f}c` adverse entry.",
        "",
        "## Inputs",
        "",
        *[f"- `{key}`: `{value}`" for key, value in meta["paths"].items()],
        "",
        "## Candidate Gate Summary",
        "",
        markdown_table(candidates),
        "",
        "## Holdout Provenance Summary",
        "",
        markdown_table(provenance_summary),
        "",
        "## Key Holdout Rows",
        "",
        markdown_table(
            interesting[
                [
                    "evidence_source",
                    "variant",
                    "holdout",
                    "settlement_label",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                    "official_proxy_mismatch_rate",
                ]
                if "official_proxy_mismatch_rate" in interesting.columns
                else [
                    "evidence_source",
                    "variant",
                    "holdout",
                    "settlement_label",
                    "trades",
                    "pnl",
                    "win_rate",
                    "max_dd",
                    "sharpe",
                ]
            ].fillna("")
        ),
        "",
        "## Interpretation",
        "",
        "- This is not a threshold search; it compares already-frozen BTC1H variants across fixed holdout buckets.",
        "- `deployable_now` is false for every candidate until forward official sample size, official/proxy mismatch, and execution gates pass.",
        "- `near_deployable_candidate` is also gated: positive forward PnL is not enough when official/proxy mismatches, row-unfaithful replay, missing exact live model inputs, or cached-TTL-only fills remain.",
        "- Current BTC1H snapshot parity can pass for actual shadow rows while full all-window counterfactual replay remains a separate blocker.",
        "- Replay-vs-ledger reconciliation is promotion-usable only when it reproduces actual paper rows exactly by market and side.",
        "- Selected-signal model parity is a stricter replay check: exact parity can fail when cached live model inputs such as event TTL were not captured, even if implied-TTL diagnostics reconcile the logged signal.",
        "- Historical Predexon rows are provider-time snapshots and are research/training evidence, not final live replay.",
        "- Live websocket rows are stronger, but pre-freeze/backtest rows still do not replace fresh forward official rows.",
        "",
    ]
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
