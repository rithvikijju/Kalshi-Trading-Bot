#!/usr/bin/env python3
"""Audit execution-realism field coverage for BTC candidate evidence.

This is a promotion-control artifact, not a strategy search. A candidate cannot
be deployable unless its live replay and paper/live ledger rows carry enough
decision-time evidence to prove executable side asks, visible quantity, quote
freshness, one-trade-per-event behavior, fees, and official settlement.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT = BACKTEST_ROOT / f"btc_execution_realism_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_Q250_REPLAY = (
    BACKTEST_ROOT
    / "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_latest_codex"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_Q250_YES_REPLAY = (
    BACKTEST_ROOT
    / "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_latest_codex"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_Q1000_REPLAY = (
    BACKTEST_ROOT
    / "btc15m_f2_live_ws_q1000_yes_causal_rest_official_latest_codex"
    / "live_ws_trades_rest_official.parquet"
)
DEFAULT_SHADOW_TRADES = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"

REPLAY_REQUIRED_FIELDS = [
    "received_at_ns",
    "received_at_utc",
    "event_ticker",
    "market_ticker",
    "side",
    "entry_price",
    "entry_fee",
    "visible_qty",
    "yes_ask",
    "yes_ask_qty",
    "no_ask",
    "no_ask_qty",
    "spread_cents",
    "btc_spot_model",
    "btc_spot_age_sec",
    "close_time",
]

LEDGER_REQUIRED_FIELDS = [
    "created_at",
    "event_ticker",
    "market_ticker",
    "side",
    "contracts",
    "entry_price",
    "fee",
    "spread_cents",
    "entry_btc_spot",
    "quote_age_ms",
    "top_visible_qty",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BTC execution-realism evidence coverage.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--q250-replay", type=Path, default=DEFAULT_Q250_REPLAY)
    parser.add_argument("--q250-yes-replay", type=Path, default=DEFAULT_Q250_YES_REPLAY)
    parser.add_argument("--q1000-replay", type=Path, default=DEFAULT_Q1000_REPLAY)
    parser.add_argument("--shadow-trades", type=Path, default=DEFAULT_SHADOW_TRADES)
    parser.add_argument("--max-quote-age-ms", type=float, default=250.0)
    parser.add_argument("--max-btc-spot-age-sec", type=float, default=120.0)
    return parser.parse_args()


def load_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def is_present(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype=bool)
    if pd.api.types.is_numeric_dtype(series):
        return series.notna()
    text = series.astype(str)
    return series.notna() & ~text.str.lower().isin(["", "nan", "none", "nat"])


def field_presence(df: pd.DataFrame, fields: list[str]) -> tuple[dict[str, float], list[str]]:
    rates: dict[str, float] = {}
    missing_or_empty: list[str] = []
    rows = len(df)
    for field in fields:
        if field not in df.columns:
            rates[field] = 0.0
            missing_or_empty.append(field)
            continue
        rate = float(is_present(df[field]).mean()) if rows else 0.0
        rates[field] = rate
        if rate < 1.0:
            missing_or_empty.append(field)
    return rates, missing_or_empty


def safe_numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def side_ask(df: pd.DataFrame) -> pd.Series:
    side = df.get("side", pd.Series("", index=df.index)).astype(str).str.lower()
    yes = safe_numeric(df, "yes_ask")
    no = safe_numeric(df, "no_ask")
    return yes.where(side.eq("yes"), no.where(side.eq("no"), np.nan))


def side_ask_qty(df: pd.DataFrame) -> pd.Series:
    side = df.get("side", pd.Series("", index=df.index)).astype(str).str.lower()
    yes = safe_numeric(df, "yes_ask_qty")
    no = safe_numeric(df, "no_ask_qty")
    return yes.where(side.eq("yes"), no.where(side.eq("no"), np.nan))


def rate(mask: pd.Series) -> float:
    if mask.empty:
        return 0.0
    return float(mask.fillna(False).mean())


def fee_quality(df: pd.DataFrame, column: str) -> dict[str, float]:
    fees = safe_numeric(df, column)
    present = is_present(df[column]) if column in df.columns else pd.Series(False, index=df.index)
    nonnegative = fees.ge(0.0) & present
    valid = fees[present & fees.ge(0.0)]
    return {
        "fee_present_rate": rate(present),
        "fee_nonnegative_rate": rate(nonnegative),
        "fee_mean": float(valid.mean()) if len(valid) else np.nan,
        "fee_max": float(valid.max()) if len(valid) else np.nan,
    }


def pnl_sum(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns:
        return np.nan
    values = safe_numeric(df, column).dropna()
    return float(values.sum()) if len(values) else np.nan


def max_drawdown(pnl: pd.Series) -> float:
    cs = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if cs.empty:
        return 0.0
    return float((cs - cs.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def official_metrics(df: pd.DataFrame, pnl_col: str, result_col: str) -> dict[str, Any]:
    if df.empty or result_col not in df.columns:
        return {"official_rows": 0, "official_pnl": 0.0, "official_max_dd": 0.0, "official_sharpe": 0.0}
    official = df[df[result_col].astype(str).str.lower().isin(["yes", "no"])].copy()
    pnl = safe_numeric(official, pnl_col) if pnl_col in official.columns else pd.Series(dtype=float)
    return {
        "official_rows": int(len(official)),
        "official_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "official_max_dd": max_drawdown(pnl.reset_index(drop=True)),
        "official_sharpe": sharpe(pnl),
    }


def audit_replay(
    df: pd.DataFrame,
    *,
    candidate: str,
    path: Path,
    min_visible_qty: float,
    max_btc_spot_age_sec: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], pd.DataFrame]:
    rows = len(df)
    presence, missing = field_presence(df, REPLAY_REQUIRED_FIELDS)
    if rows == 0:
        missing = []
    entry = safe_numeric(df, "entry_price")
    fee_stats = fee_quality(df, "entry_fee")
    expected_entry = side_ask(df)
    visible = safe_numeric(df, "visible_qty")
    expected_visible = side_ask_qty(df)
    spread = safe_numeric(df, "spread_cents")
    btc_age = safe_numeric(df, "btc_spot_age_sec")
    events = df.get("event_ticker", pd.Series(dtype=str)).astype(str) if rows else pd.Series(dtype=str)

    entry_matches = (entry - expected_entry).abs().le(1e-9) & expected_entry.notna()
    visible_matches = (visible - expected_visible).abs().le(1e-9) & expected_visible.notna()
    visible_ge_min = visible.ge(min_visible_qty)
    visible_ge_one = visible.ge(1.0)
    spread_ok = spread.le(2.0)
    btc_age_ok = btc_age.between(0.0, max_btc_spot_age_sec)
    duplicate_events = int(events.duplicated().sum()) if rows else 0
    one_trade_per_event = rows == int(events.nunique()) if rows else True

    field_rows = [
        {"source": candidate, "profile": "live_ws_replay", "field": field, "presence_rate": presence.get(field, 0.0)}
        for field in REPLAY_REQUIRED_FIELDS
    ]
    row_flags = df.copy()
    if rows:
        row_flags["audit_candidate"] = candidate
        row_flags["audit_profile"] = "live_ws_replay"
        row_flags["audit_entry_matches_side_ask"] = entry_matches
        row_flags["audit_visible_qty_matches_side_ask_qty"] = visible_matches
        row_flags["audit_visible_qty_ge_candidate_min"] = visible_ge_min
        row_flags["audit_btc_spot_age_ok"] = btc_age_ok

    blockers: list[str] = []
    if rows == 0:
        blockers.append("no_replay_rows")
    if missing:
        blockers.append("missing_replay_decision_fields")
    if rows and (fee_stats["fee_present_rate"] < 1.0 or fee_stats["fee_nonnegative_rate"] < 1.0):
        blockers.append("fee_missing_or_negative")
    if rows and not bool(entry_matches.all()):
        blockers.append("entry_not_side_ask")
    if rows and not bool(visible_matches.all()):
        blockers.append("visible_qty_not_side_ask_qty")
    if rows and not bool(visible_ge_min.all()):
        blockers.append("visible_qty_below_candidate_min")
    if rows and not bool(spread_ok.all()):
        blockers.append("spread_above_2c")
    if rows and not bool(btc_age_ok.all()):
        blockers.append("stale_btc_spot_in_replay")
    if not one_trade_per_event:
        blockers.append("duplicate_event_rows")

    official = official_metrics(df, "pnl_official_rest_2c", "official_result_filled")
    status = "PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION"
    if rows == 0:
        status = "NO_REPLAY_ROWS"
    elif blockers:
        status = "FAIL_REPLAY_EXECUTION_FIELDS"
    elif official["official_rows"] < rows:
        status = "PARTIAL_OFFICIAL_SETTLEMENT"

    summary = {
        "source": candidate,
        "profile": "live_ws_replay",
        "path": str(path),
        "rows": rows,
        **official,
        "actual_fee_official_pnl": pnl_sum(df, "pnl_official_rest_0c"),
        "stressed_2c_official_pnl": pnl_sum(df, "pnl_official_rest_2c"),
        "required_field_complete_rate": min(presence.values()) if presence else 0.0,
        "missing_or_empty_fields": ";".join(missing),
        **fee_stats,
        "entry_matches_side_ask_rate": rate(entry_matches),
        "visible_qty_matches_side_ask_qty_rate": rate(visible_matches),
        "visible_qty_ge_one_rate": rate(visible_ge_one),
        "visible_qty_ge_candidate_min_rate": rate(visible_ge_min),
        "min_visible_qty": min_visible_qty,
        "min_recorded_visible_qty": float(visible.min()) if visible.notna().any() else np.nan,
        "spread_le_2c_rate": rate(spread_ok),
        "max_spread_cents": float(spread.max()) if spread.notna().any() else np.nan,
        "btc_spot_age_ok_rate": rate(btc_age_ok),
        "btc_spot_age_p95_sec": float(btc_age.quantile(0.95)) if btc_age.notna().any() else np.nan,
        "btc_spot_age_max_sec": float(btc_age.max()) if btc_age.notna().any() else np.nan,
        "event_count": int(events.nunique()) if rows else 0,
        "duplicate_event_rows": duplicate_events,
        "one_trade_per_event": one_trade_per_event,
        "blockers": ";".join(blockers),
        "audit_status": status,
    }
    return summary, field_rows, row_flags


def audit_ledger(
    df: pd.DataFrame,
    *,
    ledger: str,
    path: Path,
    max_quote_age_ms: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], pd.DataFrame]:
    work = df[df.get("ledger", pd.Series(dtype=str)).astype(str).eq(ledger)].copy() if not df.empty else pd.DataFrame()
    rows = len(work)
    presence, missing = field_presence(work, LEDGER_REQUIRED_FIELDS)
    if rows == 0:
        missing = []
    contracts = safe_numeric(work, "contracts")
    top_visible = safe_numeric(work, "top_visible_qty")
    quote_age = safe_numeric(work, "quote_age_ms")
    entry = safe_numeric(work, "entry_price")
    fee_stats = fee_quality(work, "fee")
    expected_entry = side_ask(work)
    events = work.get("event_ticker", pd.Series(dtype=str)).astype(str) if rows else pd.Series(dtype=str)

    top_visible_ge_contracts = top_visible.ge(contracts).where(contracts.notna(), False)
    quote_age_ok = quote_age.between(0.0, max_quote_age_ms)
    entry_matches = (entry - expected_entry).abs().le(1e-9) & expected_entry.notna()
    duplicate_events = int(events.duplicated().sum()) if rows else 0
    one_trade_per_event = rows == int(events.nunique()) if rows else True

    field_rows = [
        {"source": ledger, "profile": "paper_shadow_ledger", "field": field, "presence_rate": presence.get(field, 0.0)}
        for field in LEDGER_REQUIRED_FIELDS
    ]
    row_flags = work.copy()
    if rows:
        row_flags["audit_candidate"] = ledger
        row_flags["audit_profile"] = "paper_shadow_ledger"
        row_flags["audit_quote_age_ok"] = quote_age_ok
        row_flags["audit_top_visible_qty_ge_contracts"] = top_visible_ge_contracts
        row_flags["audit_entry_matches_side_ask"] = entry_matches

    blockers: list[str] = []
    if rows == 0:
        blockers.append("no_filled_ledger_rows")
    if missing:
        blockers.append("missing_ledger_execution_fields")
    if rows and (fee_stats["fee_present_rate"] < 1.0 or fee_stats["fee_nonnegative_rate"] < 1.0):
        blockers.append("fee_missing_or_negative")
    if rows and not bool(quote_age_ok.all()):
        blockers.append("quote_age_missing_or_above_limit")
    if rows and not bool(top_visible_ge_contracts.all()):
        blockers.append("top_visible_qty_missing_or_below_contracts")
    if rows and not bool(entry_matches.all()):
        blockers.append("entry_not_reconciled_to_side_ask")
    if not one_trade_per_event:
        blockers.append("duplicate_event_rows")

    official = official_metrics(work, "official_pnl", "official_result")
    pending_rows = int(rows - official["official_rows"]) if rows else 0
    if pending_rows > 0:
        blockers.append("pending_official_settlement_rows")

    status = "PASS_LEDGER_EXECUTION_FIELDS_NOT_PROMOTION"
    if rows == 0:
        status = "NO_FILLED_LEDGER_ROWS"
    elif blockers:
        status = "FAIL_LEDGER_EXECUTION_FIELDS"

    summary = {
        "source": ledger,
        "profile": "paper_shadow_ledger",
        "path": str(path),
        "rows": rows,
        **official,
        "actual_fee_official_pnl": official["official_pnl"],
        "stressed_2c_official_pnl": np.nan,
        "pending_official_rows": pending_rows,
        "required_field_complete_rate": min(presence.values()) if presence else 0.0,
        "missing_or_empty_fields": ";".join(missing),
        **fee_stats,
        "quote_age_present_rate": presence.get("quote_age_ms", 0.0),
        "quote_age_le_limit_rate": rate(quote_age_ok),
        "quote_age_p95_ms": float(quote_age.quantile(0.95)) if quote_age.notna().any() else np.nan,
        "top_visible_qty_present_rate": presence.get("top_visible_qty", 0.0),
        "top_visible_qty_ge_contracts_rate": rate(top_visible_ge_contracts),
        "min_top_visible_qty": float(top_visible.min()) if top_visible.notna().any() else np.nan,
        "entry_matches_side_ask_rate": rate(entry_matches),
        "event_count": int(events.nunique()) if rows else 0,
        "duplicate_event_rows": duplicate_events,
        "one_trade_per_event": one_trade_per_event,
        "blockers": ";".join(dict.fromkeys(blockers)),
        "audit_status": status,
    }
    return summary, field_rows, row_flags


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    row_flag_frames: list[pd.DataFrame] = []

    replay_specs = [
        ("q250_firstskip_qty500_live_replay", args.q250_replay, 500.0),
        ("q250_firstskip_qty500_yes_live_replay", args.q250_yes_replay, 500.0),
        ("q1000_yes_live_replay", args.q1000_replay, 1000.0),
    ]
    for candidate, path, min_visible_qty in replay_specs:
        df = load_table(path)
        summary, fields, row_flags = audit_replay(
            df,
            candidate=candidate,
            path=path,
            min_visible_qty=min_visible_qty,
            max_btc_spot_age_sec=args.max_btc_spot_age_sec,
        )
        summaries.append(summary)
        field_rows.extend(fields)
        if not row_flags.empty:
            row_flag_frames.append(row_flags)

    shadow = load_table(args.shadow_trades)
    ledgers = [
        "btc15m_q250_qty500_firstskip_shadow",
        "btc15m_q250_qty500_firstskip_yes_shadow",
        "btc15m_q1000_yes_shadow",
        "btc1h_high_conf80_entry70_no_chase_shadow",
    ]
    for ledger in ledgers:
        summary, fields, row_flags = audit_ledger(
            shadow,
            ledger=ledger,
            path=args.shadow_trades,
            max_quote_age_ms=args.max_quote_age_ms,
        )
        summaries.append(summary)
        field_rows.extend(fields)
        if not row_flags.empty:
            row_flag_frames.append(row_flags)

    summary_df = pd.DataFrame(summaries)
    fields_df = pd.DataFrame(field_rows)
    summary_df.to_csv(args.out_dir / "execution_realism_summary.csv", index=False)
    fields_df.to_csv(args.out_dir / "execution_realism_field_presence.csv", index=False)
    if row_flag_frames:
        pd.concat(row_flag_frames, ignore_index=True, sort=False).to_csv(args.out_dir / "execution_realism_row_flags.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "q250_replay": str(args.q250_replay),
        "q250_yes_replay": str(args.q250_yes_replay),
        "q1000_replay": str(args.q1000_replay),
        "shadow_trades": str(args.shadow_trades),
        "max_quote_age_ms": args.max_quote_age_ms,
        "max_btc_spot_age_sec": args.max_btc_spot_age_sec,
        "note": "Promotion-control diagnostic only. Passing execution fields is necessary but not sufficient for deployment.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")

    report = [
        "# BTC Execution-Realism Audit",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Summary",
        "",
        summary_df.round(4).fillna("").to_string(index=False),
        "",
        "## Interpretation",
        "",
        "- Live websocket replay rows must prove received-time top-book executable asks and visible quantity.",
        "- Paper shadow ledger rows must prove quote age, top visible quantity, quote/signal timestamps, and decision-time book fields.",
        "- Fee columns must be present and nonnegative; replay summaries expose both actual-fee and 2c-stressed official PnL where available.",
        "- This audit is necessary-but-not-sufficient; official settlement, sample size, basis risk, and live/paper agreement gates still apply.",
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
