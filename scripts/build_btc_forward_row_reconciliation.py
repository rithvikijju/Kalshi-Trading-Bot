#!/usr/bin/env python3
"""Reconcile BTC paper-shadow fills against causal live-WS replay rows.

This is a deployment-control artifact, not a strategy search. Aggregate paper
and replay counts can agree while the rows still differ by side, market,
decision time, entry, settlement result, or PnL convention. This script makes
that agreement explicit and keeps stale-schema ledger rows from being mistaken
for deployable evidence.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_forward_row_reconciliation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_FREEZE_UTC = "2026-05-18T04:17:44Z"
DEFAULT_SHADOW_TRADES = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"

LEDGER_EXECUTION_FIELDS = [
    "quote_age_ms",
    "top_visible_qty",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "yes_ask",
    "no_ask",
]

PAPER_COLUMNS = [
    "candidate",
    "source",
    "ledger",
    "event_ticker",
    "market_ticker",
    "side",
    "paper_created_at_utc",
    "paper_entry_price",
    "paper_fee",
    "paper_official_result",
    "paper_official_pnl",
    "paper_proxy_result",
    "paper_proxy_pnl",
    "paper_expiration_value",
    "paper_contracts",
    "paper_execution_fields_complete",
    "recon_key",
    "_occurrence",
]

REPLAY_COLUMNS = [
    "candidate",
    "source",
    "ledger",
    "event_ticker",
    "market_ticker",
    "side",
    "replay_received_at_utc",
    "replay_received_at_ns",
    "replay_entry_price",
    "replay_entry_fee",
    "replay_premium",
    "replay_official_result",
    "replay_official_pnl_actual_fee",
    "replay_official_pnl_stressed_2c",
    "replay_proxy_result",
    "replay_proxy_pnl_stressed_2c",
    "replay_visible_qty",
    "replay_yes_ask",
    "replay_no_ask",
    "recon_key",
    "_occurrence",
]


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    candidate: str
    ledger: str
    replay_path: Path | None
    min_official_rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BTC forward paper-vs-replay row reconciliation.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--freeze-utc", default=DEFAULT_FREEZE_UTC)
    parser.add_argument("--shadow-trades", type=Path, default=DEFAULT_SHADOW_TRADES)
    parser.add_argument(
        "--q250-replay",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex" / "live_ws_trades_rest_official.parquet",
    )
    parser.add_argument(
        "--q1000-replay",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex" / "live_ws_trades_rest_official.parquet",
    )
    parser.add_argument(
        "--q250-yes-replay",
        type=Path,
        default=BACKTEST_ROOT / "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex" / "live_ws_trades_rest_official.parquet",
    )
    parser.add_argument("--entry-tolerance", type=float, default=1e-9)
    parser.add_argument("--pnl-tolerance", type=float, default=1e-9)
    parser.add_argument("--time-tolerance-ms", type=float, default=1000.0)
    return parser.parse_args()


def read_table(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def text(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series("", index=index, dtype=str)
    return series.fillna("").astype(str)


def numeric(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(np.nan, index=index, dtype=float)
    return pd.to_numeric(series, errors="coerce")


def is_present(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(False, index=index, dtype=bool)
    if pd.api.types.is_numeric_dtype(series):
        return series.notna()
    values = series.fillna("").astype(str).str.strip().str.lower()
    return ~values.isin(["", "nan", "none", "nat", "<na>"])


def scalar_sum(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(values.sum()), 10) if len(values) else 0.0


def max_abs(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().abs()
    return round(float(values.max()), 10) if len(values) else 0.0


def normalize_result(value: Any) -> str:
    result = str(value or "").strip().lower()
    return result if result in {"yes", "no"} else ""


def empty_paper() -> pd.DataFrame:
    return pd.DataFrame(columns=PAPER_COLUMNS + [f"paper_has_{field}" for field in LEDGER_EXECUTION_FIELDS])


def empty_replay() -> pd.DataFrame:
    return pd.DataFrame(columns=REPLAY_COLUMNS)


def normalize_paper(raw: pd.DataFrame, spec: CandidateSpec, freeze_utc: str) -> pd.DataFrame:
    if raw.empty or "ledger" not in raw.columns:
        return empty_paper()
    work = raw[raw["ledger"].astype(str).eq(spec.ledger)].copy()
    if work.empty:
        return empty_paper()
    created = pd.to_datetime(work.get("created_at"), utc=True, errors="coerce")
    freeze = pd.Timestamp(freeze_utc)
    if freeze.tzinfo is None:
        freeze = freeze.tz_localize("UTC")
    work = work[created.ge(freeze)].copy()
    if work.empty:
        return empty_paper()
    work["_created_at_utc"] = pd.to_datetime(work.get("created_at"), utc=True, errors="coerce")
    out = pd.DataFrame(index=work.index)
    out["candidate"] = spec.candidate
    out["source"] = "paper_shadow_ledger"
    out["ledger"] = spec.ledger
    out["event_ticker"] = text(work.get("event_ticker"), work.index)
    out["market_ticker"] = text(work.get("market_ticker"), work.index)
    out["side"] = text(work.get("side"), work.index).str.lower()
    out["paper_created_at_utc"] = work["_created_at_utc"]
    out["paper_entry_price"] = numeric(work.get("entry_price"), work.index)
    out["paper_fee"] = numeric(work.get("fee"), work.index)
    out["paper_official_result"] = text(work.get("official_result"), work.index).map(normalize_result)
    out["paper_official_pnl"] = numeric(work.get("official_pnl"), work.index)
    out["paper_proxy_result"] = text(work.get("proxy_result"), work.index).map(normalize_result)
    out["paper_proxy_pnl"] = numeric(work.get("proxy_pnl"), work.index)
    out["paper_expiration_value"] = numeric(work.get("expiration_value"), work.index)
    out["paper_contracts"] = numeric(work.get("contracts"), work.index)
    for field in LEDGER_EXECUTION_FIELDS:
        out[f"paper_has_{field}"] = is_present(work.get(field), work.index)
    out["paper_execution_fields_complete"] = out[[f"paper_has_{field}" for field in LEDGER_EXECUTION_FIELDS]].all(axis=1)
    out["recon_key"] = out["market_ticker"] + "|" + out["side"]
    out["_occurrence"] = out.groupby("recon_key", dropna=False).cumcount()
    return out.reset_index(drop=True)


def normalize_replay(raw: pd.DataFrame, spec: CandidateSpec) -> pd.DataFrame:
    if raw.empty:
        return empty_replay()
    work = raw.copy()
    out = pd.DataFrame(index=work.index)
    out["candidate"] = spec.candidate
    out["source"] = "live_ws_replay"
    out["ledger"] = spec.ledger
    out["event_ticker"] = text(work.get("event_ticker"), work.index)
    out["market_ticker"] = text(work.get("market_ticker"), work.index)
    out["side"] = text(work.get("side"), work.index).str.lower()
    out["replay_received_at_utc"] = pd.to_datetime(work.get("received_at_utc"), utc=True, errors="coerce")
    out["replay_received_at_ns"] = numeric(work.get("received_at_ns"), work.index)
    out["replay_entry_price"] = numeric(work.get("entry_price"), work.index)
    out["replay_entry_fee"] = numeric(work.get("entry_fee"), work.index)
    out["replay_premium"] = numeric(work.get("premium"), work.index)
    out["replay_official_result"] = text(
        work.get("official_result_filled", work.get("official_result_rest", work.get("official_result"))),
        work.index,
    ).map(normalize_result)
    out["replay_official_pnl_actual_fee"] = numeric(
        work.get("pnl_official_rest_0c", work.get("pnl_official_0c")),
        work.index,
    )
    out["replay_official_pnl_stressed_2c"] = numeric(
        work.get("pnl_official_rest_2c", work.get("pnl_official_2c")),
        work.index,
    )
    out["replay_proxy_result"] = text(work.get("proxy_result"), work.index).map(normalize_result)
    out["replay_proxy_pnl_stressed_2c"] = numeric(work.get("pnl_proxy_2c"), work.index)
    out["replay_visible_qty"] = numeric(work.get("visible_qty"), work.index)
    out["replay_yes_ask"] = numeric(work.get("yes_ask"), work.index)
    out["replay_no_ask"] = numeric(work.get("no_ask"), work.index)
    out["recon_key"] = out["market_ticker"] + "|" + out["side"]
    out["_occurrence"] = out.groupby("recon_key", dropna=False).cumcount()
    return out.reset_index(drop=True)


def reconcile_candidate(
    *,
    shadow: pd.DataFrame,
    replay_raw: pd.DataFrame,
    spec: CandidateSpec,
    freeze_utc: str,
    entry_tolerance: float,
    pnl_tolerance: float,
    time_tolerance_ms: float,
) -> tuple[dict[str, Any], pd.DataFrame]:
    paper = normalize_paper(shadow, spec, freeze_utc)
    replay = normalize_replay(replay_raw, spec)
    if spec.replay_path is None:
        details = paper.copy()
        if details.empty:
            details = pd.DataFrame(columns=["candidate", "ledger", "market_ticker", "side"])
        details["reconciliation_status"] = "no_causal_replay_comparator"
        return {
            "family": spec.family,
            "candidate": spec.candidate,
            "ledger": spec.ledger,
            "paper_rows": int(len(paper)),
            "replay_rows": 0,
            "matched_rows": 0,
            "paper_without_replay_rows": int(len(paper)),
            "replay_without_paper_rows": 0,
            "row_reconciliation_pass": False,
            "promotion_usable": False,
            "reconciliation_status": "NO_CAUSAL_REPLAY_COMPARATOR",
            "blocking_reasons": "no_causal_replay_comparator",
        }, details

    merged = paper.merge(
        replay,
        on=["candidate", "ledger", "recon_key", "_occurrence"],
        how="outer",
        suffixes=("_paper", "_replay"),
        indicator=True,
    )
    for col in ["event_ticker", "market_ticker", "side"]:
        left = f"{col}_paper"
        right = f"{col}_replay"
        if left in merged.columns or right in merged.columns:
            merged[col] = merged.get(left, pd.Series("", index=merged.index)).combine_first(
                merged.get(right, pd.Series("", index=merged.index))
            )

    matched = merged["_merge"].eq("both")
    entry_delta = merged["paper_entry_price"] - merged["replay_entry_price"]
    pnl_delta_actual = merged["paper_official_pnl"] - merged["replay_official_pnl_actual_fee"]
    pnl_delta_stressed = merged["paper_official_pnl"] - merged["replay_official_pnl_stressed_2c"]
    if "paper_created_at_utc" in merged.columns and "replay_received_at_utc" in merged.columns:
        paper_time = pd.to_datetime(merged["paper_created_at_utc"], utc=True, errors="coerce")
        replay_time = pd.to_datetime(merged["replay_received_at_utc"], utc=True, errors="coerce")
        time_delta_ms = (paper_time - replay_time).dt.total_seconds() * 1000.0
        merged["paper_created_at_utc"] = paper_time
        merged["replay_received_at_utc"] = replay_time
    else:
        time_delta_ms = pd.Series(np.nan, index=merged.index)
    result_match = merged["paper_official_result"].eq(merged["replay_official_result"])

    merged["entry_delta"] = entry_delta
    merged["official_pnl_delta_actual_fee"] = pnl_delta_actual
    merged["official_pnl_delta_vs_replay_stressed_2c"] = pnl_delta_stressed
    merged["decision_time_delta_ms"] = time_delta_ms
    merged["entry_matches"] = matched & entry_delta.abs().le(entry_tolerance)
    merged["official_result_matches"] = matched & result_match
    merged["official_pnl_actual_fee_matches"] = matched & pnl_delta_actual.abs().le(pnl_tolerance)
    merged["decision_time_within_tolerance"] = matched & time_delta_ms.abs().le(time_tolerance_ms)
    paper_execution = merged.get("paper_execution_fields_complete")
    if paper_execution is None:
        merged["paper_execution_fields_complete_row"] = False
    else:
        merged["paper_execution_fields_complete_row"] = paper_execution.where(paper_execution.notna(), False).astype(bool)
    merged["reconciliation_status"] = np.select(
        [
            merged["_merge"].eq("left_only"),
            merged["_merge"].eq("right_only"),
            ~merged["entry_matches"],
            ~merged["official_result_matches"],
            ~merged["official_pnl_actual_fee_matches"],
            ~merged["decision_time_within_tolerance"],
        ],
        [
            "paper_without_replay",
            "replay_without_paper",
            "entry_mismatch",
            "official_result_mismatch",
            "official_pnl_actual_fee_mismatch",
            "decision_time_delta_large",
        ],
        default="matched",
    )

    paper_rows = int(len(paper))
    replay_rows = int(len(replay))
    matched_rows = int(matched.sum())
    paper_without = int(merged["_merge"].eq("left_only").sum())
    replay_without = int(merged["_merge"].eq("right_only").sum())
    entry_mismatch = int((matched & ~merged["entry_matches"]).sum())
    result_mismatch = int((matched & ~merged["official_result_matches"]).sum())
    actual_pnl_mismatch = int((matched & ~merged["official_pnl_actual_fee_matches"]).sum())
    time_mismatch = int((matched & ~merged["decision_time_within_tolerance"]).sum())
    paper_execution_complete = bool(paper["paper_execution_fields_complete"].all()) if paper_rows else False
    official_rows = int(
        paper["paper_official_result"].isin(["yes", "no"]).sum()
        if paper_rows
        else replay["replay_official_result"].isin(["yes", "no"]).sum()
    )

    blockers: list[str] = []
    if paper_rows == 0:
        blockers.append("no_paper_shadow_rows_since_freeze")
    if replay_rows == 0:
        blockers.append("no_replay_rows")
    if paper_without:
        blockers.append("paper_rows_without_replay_match")
    if replay_without:
        blockers.append("replay_rows_without_paper_match")
    if entry_mismatch:
        blockers.append("entry_mismatch")
    if result_mismatch:
        blockers.append("official_result_mismatch")
    if actual_pnl_mismatch:
        blockers.append("actual_fee_pnl_mismatch")
    if time_mismatch:
        blockers.append("decision_time_delta_large")
    if paper_rows and not paper_execution_complete:
        blockers.append("paper_ledger_execution_fields_missing")
    if official_rows < spec.min_official_rows:
        blockers.append("too_few_official_rows_for_promotion")

    row_reconciliation_pass = not any(
        reason
        for reason in blockers
        if reason
        not in {
            "paper_ledger_execution_fields_missing",
            "too_few_official_rows_for_promotion",
        }
    )
    if not blockers:
        status = "PASS_ROW_RECONCILIATION_NOT_DEPLOYMENT"
    elif row_reconciliation_pass:
        status = "MATCHED_BUT_NOT_PROMOTION_USABLE"
    elif paper_rows == 0 and replay_rows > 0:
        status = "REPLAY_ROWS_WITHOUT_PAPER_SHADOW"
    elif spec.replay_path is not None and replay_rows == 0:
        status = "NO_REPLAY_ROWS"
    else:
        status = "FAIL_ROW_RECONCILIATION"

    summary = {
        "family": spec.family,
        "candidate": spec.candidate,
        "ledger": spec.ledger,
        "paper_rows": paper_rows,
        "replay_rows": replay_rows,
        "matched_rows": matched_rows,
        "paper_without_replay_rows": paper_without,
        "replay_without_paper_rows": replay_without,
        "entry_mismatch_rows": entry_mismatch,
        "official_result_mismatch_rows": result_mismatch,
        "actual_fee_pnl_mismatch_rows": actual_pnl_mismatch,
        "decision_time_delta_large_rows": time_mismatch,
        "paper_execution_fields_complete": paper_execution_complete,
        "paper_official_pnl_sum": scalar_sum(paper.get("paper_official_pnl", pd.Series(dtype=float))),
        "replay_actual_fee_official_pnl_sum": scalar_sum(replay.get("replay_official_pnl_actual_fee", pd.Series(dtype=float))),
        "replay_stressed_2c_official_pnl_sum": scalar_sum(replay.get("replay_official_pnl_stressed_2c", pd.Series(dtype=float))),
        "paper_minus_replay_actual_fee_pnl": scalar_sum(pnl_delta_actual[matched]),
        "paper_minus_replay_stressed_2c_pnl": scalar_sum(pnl_delta_stressed[matched]),
        "max_abs_entry_delta": max_abs(entry_delta[matched]),
        "max_abs_actual_fee_pnl_delta": max_abs(pnl_delta_actual[matched]),
        "max_abs_decision_time_delta_ms": max_abs(time_delta_ms[matched]),
        "official_rows": official_rows,
        "promotion_min_official_rows": spec.min_official_rows,
        "row_reconciliation_pass": row_reconciliation_pass,
        "promotion_usable": False,
        "reconciliation_status": status,
        "blocking_reasons": ";".join(dict.fromkeys(blockers)),
    }
    return summary, merged


def preferred_detail_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "candidate",
        "ledger",
        "reconciliation_status",
        "_merge",
        "event_ticker",
        "market_ticker",
        "side",
        "paper_created_at_utc",
        "replay_received_at_utc",
        "decision_time_delta_ms",
        "paper_entry_price",
        "replay_entry_price",
        "entry_delta",
        "paper_official_result",
        "replay_official_result",
        "paper_official_pnl",
        "replay_official_pnl_actual_fee",
        "replay_official_pnl_stressed_2c",
        "official_pnl_delta_actual_fee",
        "official_pnl_delta_vs_replay_stressed_2c",
        "paper_execution_fields_complete_row",
        "replay_visible_qty",
        "replay_yes_ask",
        "replay_no_ask",
    ]
    ordered = [col for col in cols if col in df.columns] + [col for col in df.columns if col not in cols]
    return df[ordered]


def json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if math.isnan(float(value)):
            return None
        return float(value)
    return value


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    shadow = read_table(args.shadow_trades)
    specs = [
        CandidateSpec(
            family="BTC15M",
            candidate="q250_firstskip_qty500",
            ledger="btc15m_q250_qty500_firstskip_shadow",
            replay_path=args.q250_replay,
            min_official_rows=100,
        ),
        CandidateSpec(
            family="BTC15M",
            candidate="q1000_yes",
            ledger="btc15m_q1000_yes_shadow",
            replay_path=args.q1000_replay,
            min_official_rows=100,
        ),
        CandidateSpec(
            family="BTC15M",
            candidate="q250_firstskip_qty500_yes",
            ledger="btc15m_q250_qty500_firstskip_yes_shadow",
            replay_path=args.q250_yes_replay,
            min_official_rows=100,
        ),
        CandidateSpec(
            family="BTC1H",
            candidate="btc1h_high_conf80_entry70_no_chase",
            ledger="btc1h_high_conf80_entry70_no_chase_shadow",
            replay_path=None,
            min_official_rows=50,
        ),
    ]

    summary_rows: list[dict[str, Any]] = []
    detail_frames: list[pd.DataFrame] = []
    for spec in specs:
        replay = read_table(spec.replay_path)
        summary, details = reconcile_candidate(
            shadow=shadow,
            replay_raw=replay,
            spec=spec,
            freeze_utc=args.freeze_utc,
            entry_tolerance=args.entry_tolerance,
            pnl_tolerance=args.pnl_tolerance,
            time_tolerance_ms=args.time_tolerance_ms,
        )
        summary_rows.append(summary)
        if not details.empty:
            detail_frames.append(preferred_detail_columns(details))

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(args.out_dir / "row_reconciliation_summary.csv", index=False)
    if detail_frames:
        cleaned_details = [frame.dropna(axis=1, how="all") for frame in detail_frames if len(frame)]
        pd.concat(cleaned_details, ignore_index=True, sort=False).to_csv(
            args.out_dir / "row_reconciliation_details.csv",
            index=False,
        )

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_utc": args.freeze_utc,
        "shadow_trades": str(args.shadow_trades),
        "q250_replay": str(args.q250_replay),
        "q1000_replay": str(args.q1000_replay),
        "q250_yes_replay": str(args.q250_yes_replay),
        "entry_tolerance": args.entry_tolerance,
        "pnl_tolerance": args.pnl_tolerance,
        "time_tolerance_ms": args.time_tolerance_ms,
        "note": "Row reconciliation is necessary but not sufficient. promotion_usable is always false here unless separate readiness/sample/execution gates are satisfied elsewhere.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Forward Row Reconciliation",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        f"Freeze UTC: `{args.freeze_utc}`",
        "",
        "## Summary",
        "",
        summary_df.round(6).fillna("").to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- This checks paper-shadow rows against causal live-WS replay rows by market, side, occurrence, entry, official result, actual-fee PnL, and decision-time proximity.",
        "- `paper_minus_replay_stressed_2c_pnl` is expected to differ when replay applies the additional 2c stress layer; actual-fee PnL must still reconcile.",
        "- Missing paper ledger execution fields or tiny official samples keep rows out of promotion evidence even when the row keys match.",
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
