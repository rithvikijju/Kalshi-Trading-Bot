#!/usr/bin/env python3
"""Build a canonical BTC official-settlement feature table.

This is a diagnostic control artifact, not a strategy search. It normalizes the
current official-vs-proxy settlement rows across live websocket replay, paper
shadow ledger settlement, and Predexon historical snapshots into one table with
explicit source fidelity and promotion blockers.
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
DEFAULT_OUT = BACKTEST_ROOT / f"btc_official_settlement_feature_table_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DEFAULT_BASIS_RISK_ROWS = BACKTEST_ROOT / "btc_settlement_basis_risk_audit_latest_codex" / "settlement_basis_risk_rows.csv"
DEFAULT_SHADOW_OFFICIAL_ROWS = BACKTEST_ROOT / "btc_shadow_official_settlement_latest_codex" / "shadow_official_trades.csv"
DEFAULT_PREDEXON_SELECTED = (
    BACKTEST_ROOT / "btc15m_predexon_official_coverage_latest_codex" / "predexon_coverage_selected_trades.parquet"
)
DEFAULT_PREDEXON_MARKET_RESULTS = (
    BACKTEST_ROOT / "btc15m_predexon_rest_official_20260517_codex" / "market_results.csv"
)

REQUESTED_DECISION_FIELDS = ["quote_age_ms", "top_visible_qty", "yes_bid", "yes_ask", "no_bid", "no_ask"]
EXECUTION_REALISM_FIELDS = [
    "quote_age_ms",
    "top_visible_qty",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "strike",
    "ttl_min",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
]

OUTPUT_COLUMNS = [
    "source",
    "fidelity_label",
    "strategy_family",
    "candidate",
    "event_ticker",
    "market_ticker",
    "close_time",
    "decision_time",
    "strike",
    "side",
    "entry_price",
    "contracts",
    "fee",
    "ttl_min",
    "btc_spot",
    "btc_spot_age_sec",
    "proxy_close_spot",
    "proxy_result",
    "proxy_win",
    "proxy_pnl_2c",
    "official_result",
    "official_win",
    "official_pnl_2c",
    "official_expiration_value",
    "proxy_distance_usd",
    "official_distance_usd",
    "basis_usd",
    "abs_basis_usd",
    "proxy_official_result_mismatch",
    "adverse_proxy_official_mismatch",
    "proxy_win_official_loss",
    "quote_age_ms",
    "top_visible_qty",
    "visible_qty",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_ask_qty",
    "no_ask_qty",
    "spread_cents",
    "quote_received_at_ns",
    "signal_received_at_ns",
    "rv_60m",
    "fair_edge_cents",
    "side_fair_p",
    "window",
    "source_path",
    "has_official_result",
    "has_proxy_result",
    "decision_fields_complete",
    "execution_realism_fields_complete",
    "missing_decision_fields",
    "missing_execution_realism_fields",
    "promotion_usable",
    "promotion_blockers",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical BTC official-settlement feature table.")
    parser.add_argument("--basis-risk-rows", type=Path, default=DEFAULT_BASIS_RISK_ROWS)
    parser.add_argument("--shadow-official-rows", type=Path, default=DEFAULT_SHADOW_OFFICIAL_ROWS)
    parser.add_argument("--predexon-selected-trades", type=Path, default=DEFAULT_PREDEXON_SELECTED)
    parser.add_argument("--predexon-market-results", type=Path, default=DEFAULT_PREDEXON_MARKET_RESULTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def series(df: pd.DataFrame, names: str | list[str], default: Any = pd.NA) -> pd.Series:
    candidates = [names] if isinstance(names, str) else names
    for name in candidates:
        if name in df.columns:
            return df[name]
    return pd.Series([default] * len(df), index=df.index)


def coalesce(df: pd.DataFrame, names: list[str], default: Any = pd.NA) -> pd.Series:
    out = pd.Series([default] * len(df), index=df.index, dtype="object")
    for name in names:
        if name not in df.columns:
            continue
        s = df[name]
        mask = is_missing(out) & ~is_missing(s)
        out.loc[mask] = s.loc[mask]
    return out


def is_missing(s: pd.Series) -> pd.Series:
    if s.dtype == "object":
        text = s.astype(str).str.strip().str.lower()
        return s.isna() | text.isin(["", "nan", "none", "<na>", "nat"])
    return s.isna()


def text_clean(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})


def result_clean(s: pd.Series) -> pd.Series:
    out = text_clean(s).str.lower()
    return out.where(out.isin(["yes", "no"]), "")


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def parse_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True)


def side_win(side: pd.Series, result: pd.Series) -> pd.Series:
    side_n = text_clean(side).str.lower()
    result_n = result_clean(result)
    win = pd.Series(pd.NA, index=side.index, dtype="object")
    both = side_n.isin(["yes", "no"]) & result_n.isin(["yes", "no"])
    win.loc[both] = side_n.loc[both].eq(result_n.loc[both])
    return win


def bool_from_any(s: pd.Series) -> pd.Series:
    text = text_clean(s).str.lower()
    out = pd.Series(pd.NA, index=s.index, dtype="object")
    out.loc[text.isin(["true", "1", "yes", "y"])] = True
    out.loc[text.isin(["false", "0", "no", "n"])] = False
    return out


def load_predexon_market_results(path: Path) -> pd.DataFrame:
    market_results = read_csv(path)
    if market_results.empty:
        return pd.DataFrame(columns=["market_ticker", "official_result_market", "official_expiration_value_market", "settlement_ts"])
    out = market_results.copy()
    out = out.rename(
        columns={
            "result": "official_result_market",
            "expiration_value": "official_expiration_value_market",
        }
    )
    keep = [
        col
        for col in [
            "market_ticker",
            "official_result_market",
            "official_expiration_value_market",
            "settlement_ts",
        ]
        if col in out.columns
    ]
    return out[keep].drop_duplicates("market_ticker")


def normalize_basis_risk(path: Path) -> pd.DataFrame:
    raw = read_csv(path)
    if raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=raw.index)
    source_raw = text_clean(series(raw, "source"))
    family = text_clean(series(raw, "family"))
    shadow = source_raw.eq("shadow_official_ledger") | family.eq("BTC1H")
    raw = raw.loc[~shadow].copy()
    if raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=raw.index)
    source_raw = text_clean(series(raw, "source"))
    family = text_clean(series(raw, "family"))
    shadow = source_raw.eq("shadow_official_ledger") | family.eq("BTC1H")
    live_ws = ~shadow

    out["source"] = source_raw.where(source_raw.ne(""), "basis_risk_audit")
    out.loc[shadow, "fidelity_label"] = "paper_shadow_stale_schema_diagnostic"
    out.loc[live_ws, "fidelity_label"] = "live_ws_replay_old_not_promotion"
    out["strategy_family"] = family.where(family.ne(""), "BTC15M")
    out["candidate"] = coalesce(raw, ["candidate", "ledger"])
    out["event_ticker"] = series(raw, "event_ticker")
    out["market_ticker"] = series(raw, "market_ticker")
    out["close_time"] = coalesce(raw, ["close_time"])
    out["decision_time"] = coalesce(raw, ["received_at_utc", "created_at"])
    out["strike"] = coalesce(raw, ["floor_strike", "ticker_strike"])
    out["side"] = text_clean(series(raw, "side")).str.lower()
    out["entry_price"] = coalesce(raw, ["entry_price"])
    out["contracts"] = coalesce(raw, ["contracts"], 1)
    out["fee"] = coalesce(raw, ["fee", "entry_fee"])
    out["ttl_min"] = series(raw, "ttl_min")
    out["btc_spot"] = coalesce(raw, ["btc_spot_model", "entry_btc_spot", "btc_spot"])
    out["btc_spot_age_sec"] = series(raw, "btc_spot_age_sec")
    out["proxy_close_spot"] = coalesce(raw, ["close_btc_spot", "proxy_close_spot", "btc_close_spot"])
    out["proxy_result"] = coalesce(raw, ["proxy_result", "result"])
    out["proxy_win"] = coalesce(raw, ["proxy_win", "win_pnl_proxy_2c", "win"])
    out["proxy_pnl_2c"] = coalesce(raw, ["proxy_pnl_2c", "pnl_proxy_2c", "proxy_pnl", "pnl"])
    out["official_result"] = coalesce(raw, ["official_result_filled", "rest_result", "official_result"])
    out["official_win"] = coalesce(raw, ["official_win", "win_pnl_official_rest_2c", "win_pnl_official_2c"])
    out["official_pnl_2c"] = coalesce(raw, ["official_pnl_2c", "pnl_official_rest_2c", "pnl_official_2c", "official_pnl"])
    out["official_expiration_value"] = coalesce(raw, ["expiration_value", "rest_expiration_value"])
    out["proxy_distance_usd"] = series(raw, "proxy_distance_usd")
    out["official_distance_usd"] = coalesce(raw, ["official_distance_usd", "official_expiration_minus_strike"])
    out["basis_usd"] = coalesce(raw, ["basis_usd", "basis_official_minus_proxy_usd", "official_minus_proxy_spot"])
    out["quote_age_ms"] = series(raw, "quote_age_ms")
    out["top_visible_qty"] = coalesce(raw, ["top_visible_qty", "visible_qty"])
    out["visible_qty"] = series(raw, "visible_qty")
    out["yes_bid"] = series(raw, "yes_bid")
    out["yes_ask"] = series(raw, "yes_ask")
    out["no_bid"] = series(raw, "no_bid")
    out["no_ask"] = series(raw, "no_ask")
    out["yes_ask_qty"] = series(raw, "yes_ask_qty")
    out["no_ask_qty"] = series(raw, "no_ask_qty")
    out["spread_cents"] = series(raw, "spread_cents")
    out["quote_received_at_ns"] = series(raw, "quote_received_at_ns")
    out["signal_received_at_ns"] = series(raw, "signal_received_at_ns")
    out["rv_60m"] = series(raw, "rv_60m")
    out["fair_edge_cents"] = series(raw, "fair_edge_cents")
    out["side_fair_p"] = series(raw, "side_fair_p")
    out["window"] = series(raw, "window")
    out["source_path"] = str(path.relative_to(PROJECT_ROOT))
    return out


def normalize_shadow_official(path: Path) -> pd.DataFrame:
    raw = read_csv(path)
    if raw.empty:
        return pd.DataFrame()
    finalized = result_clean(series(raw, "official_result")).isin(["yes", "no"])
    raw = raw.loc[finalized].copy()
    if raw.empty:
        return pd.DataFrame()

    out = pd.DataFrame(index=raw.index)
    out["source"] = "shadow_official_ledger"
    out["fidelity_label"] = "paper_shadow_stale_schema_diagnostic"
    out["strategy_family"] = "BTC1H"
    out["candidate"] = series(raw, "ledger")
    out["event_ticker"] = series(raw, "event_ticker")
    out["market_ticker"] = series(raw, "market_ticker")
    out["close_time"] = series(raw, "settlement_ts")
    out["decision_time"] = series(raw, "created_at")
    out["strike"] = coalesce(raw, ["floor_strike", "ticker_strike"])
    out["side"] = text_clean(series(raw, "side")).str.lower()
    out["entry_price"] = series(raw, "entry_price")
    out["contracts"] = series(raw, "contracts")
    out["fee"] = series(raw, "fee")
    out["ttl_min"] = pd.NA
    out["btc_spot"] = series(raw, "entry_btc_spot")
    out["btc_spot_age_sec"] = pd.NA
    out["proxy_close_spot"] = series(raw, "proxy_close_spot")
    out["proxy_result"] = series(raw, "proxy_result")
    out["proxy_win"] = series(raw, "proxy_win")
    out["proxy_pnl_2c"] = series(raw, "proxy_pnl")
    out["official_result"] = series(raw, "official_result")
    out["official_win"] = series(raw, "official_win")
    out["official_pnl_2c"] = series(raw, "official_pnl")
    out["official_expiration_value"] = series(raw, "expiration_value")
    out["proxy_distance_usd"] = series(raw, "proxy_close_minus_strike")
    out["official_distance_usd"] = series(raw, "official_expiration_minus_strike")
    out["basis_usd"] = series(raw, "official_minus_proxy_spot")
    out["quote_age_ms"] = series(raw, "quote_age_ms")
    out["top_visible_qty"] = series(raw, "top_visible_qty")
    out["visible_qty"] = series(raw, "top_visible_qty")
    out["yes_bid"] = series(raw, "yes_bid")
    out["yes_ask"] = series(raw, "yes_ask")
    out["no_bid"] = series(raw, "no_bid")
    out["no_ask"] = series(raw, "no_ask")
    out["yes_ask_qty"] = pd.NA
    out["no_ask_qty"] = pd.NA
    out["spread_cents"] = series(raw, "spread_cents")
    out["quote_received_at_ns"] = series(raw, "quote_received_at_ns")
    out["signal_received_at_ns"] = series(raw, "signal_received_at_ns")
    out["rv_60m"] = pd.NA
    out["fair_edge_cents"] = series(raw, "net_edge_cents")
    out["side_fair_p"] = pd.NA
    out["window"] = pd.NA
    out["source_path"] = str(path.relative_to(PROJECT_ROOT))
    return out


def normalize_predexon(selected_path: Path, market_results_path: Path) -> pd.DataFrame:
    raw = read_parquet(selected_path)
    if raw.empty:
        return pd.DataFrame()
    market_results = load_predexon_market_results(market_results_path)
    if not market_results.empty and "market_ticker" in raw.columns:
        raw = raw.merge(market_results, on="market_ticker", how="left")

    out = pd.DataFrame(index=raw.index)
    out["source"] = "predexon_selected_coverage"
    out["fidelity_label"] = "historical_snapshot_provider_time_diagnostic"
    out["strategy_family"] = "BTC15M"
    out["candidate"] = series(raw, "candidate")
    out["event_ticker"] = series(raw, "event_ticker")
    out["market_ticker"] = series(raw, "market_ticker")
    out["close_time"] = series(raw, "close_time")
    out["decision_time"] = coalesce(raw, ["available_at", "timestamp_utc"])
    out["strike"] = series(raw, "floor_strike")
    out["side"] = text_clean(series(raw, "side")).str.lower()
    out["entry_price"] = series(raw, "entry_price")
    out["contracts"] = 1
    out["fee"] = series(raw, "entry_fee")
    out["ttl_min"] = series(raw, "ttl_min")
    out["btc_spot"] = series(raw, "btc_spot")
    out["btc_spot_age_sec"] = series(raw, "btc_spot_age_sec")
    out["proxy_close_spot"] = series(raw, "btc_close_spot")
    out["proxy_result"] = series(raw, "result")
    out["proxy_win"] = series(raw, "win")
    out["proxy_pnl_2c"] = coalesce(raw, ["pnl_stress", "pnl"])
    out["official_result"] = coalesce(raw, ["official_result_rest", "official_result_market"])
    out["official_win"] = series(raw, "win_pnl_official_rest_2c")
    out["official_pnl_2c"] = series(raw, "pnl_official_rest_2c")
    out["official_expiration_value"] = series(raw, "official_expiration_value_market")
    out["proxy_distance_usd"] = coalesce(raw, ["proxy_close_margin_usd"])
    out["official_distance_usd"] = pd.NA
    out["basis_usd"] = pd.NA
    out["quote_age_ms"] = pd.NA
    out["top_visible_qty"] = series(raw, "visible_qty")
    out["visible_qty"] = series(raw, "visible_qty")
    out["yes_bid"] = series(raw, "yes_bid")
    out["yes_ask"] = series(raw, "yes_ask")
    out["no_bid"] = series(raw, "no_bid")
    out["no_ask"] = series(raw, "no_ask")
    out["yes_ask_qty"] = series(raw, "yes_ask_qty")
    out["no_ask_qty"] = series(raw, "no_ask_qty")
    out["spread_cents"] = series(raw, "spread_cents")
    out["quote_received_at_ns"] = pd.NA
    out["signal_received_at_ns"] = pd.NA
    out["rv_60m"] = series(raw, "rv_60m")
    out["fair_edge_cents"] = series(raw, "fair_edge_cents")
    out["side_fair_p"] = series(raw, "side_fair_p")
    out["window"] = series(raw, "window")
    out["source_path"] = str(selected_path.relative_to(PROJECT_ROOT))
    return out


def fill_derived(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    out = rows.copy()
    for col in [
        "strike",
        "entry_price",
        "contracts",
        "fee",
        "ttl_min",
        "btc_spot",
        "btc_spot_age_sec",
        "proxy_close_spot",
        "proxy_pnl_2c",
        "official_pnl_2c",
        "official_expiration_value",
        "proxy_distance_usd",
        "official_distance_usd",
        "basis_usd",
        "quote_age_ms",
        "top_visible_qty",
        "visible_qty",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "yes_ask_qty",
        "no_ask_qty",
        "spread_cents",
        "quote_received_at_ns",
        "signal_received_at_ns",
        "rv_60m",
        "fair_edge_cents",
        "side_fair_p",
    ]:
        if col in out.columns:
            out[col] = num(out[col])

    out["side"] = text_clean(out["side"]).str.lower()
    out["proxy_result"] = result_clean(out["proxy_result"])
    out["official_result"] = result_clean(out["official_result"])

    close_dt = parse_utc(out["close_time"])
    decision_dt = parse_utc(out["decision_time"])
    computed_ttl = (close_dt - decision_dt).dt.total_seconds() / 60.0
    out["ttl_min"] = out["ttl_min"].fillna(computed_ttl)
    out["close_time"] = close_dt.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").str.replace(".000000Z", "Z", regex=False)
    out["decision_time"] = decision_dt.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").str.replace(".000000Z", "Z", regex=False)

    computed_proxy_distance = out["proxy_close_spot"] - out["strike"]
    out["proxy_distance_usd"] = out["proxy_distance_usd"].fillna(computed_proxy_distance)
    computed_official_distance = out["official_expiration_value"] - out["strike"]
    out["official_distance_usd"] = out["official_distance_usd"].fillna(computed_official_distance)
    computed_basis = out["official_expiration_value"] - out["proxy_close_spot"]
    out["basis_usd"] = out["basis_usd"].fillna(computed_basis)
    out["abs_basis_usd"] = out["basis_usd"].abs()

    proxy_win_calc = side_win(out["side"], out["proxy_result"])
    official_win_calc = side_win(out["side"], out["official_result"])
    proxy_win_existing = bool_from_any(out["proxy_win"])
    official_win_existing = bool_from_any(out["official_win"])
    out["proxy_win"] = proxy_win_existing.where(~is_missing(proxy_win_existing), proxy_win_calc)
    out["official_win"] = official_win_existing.where(~is_missing(official_win_existing), official_win_calc)

    out["has_official_result"] = out["official_result"].isin(["yes", "no"])
    out["has_proxy_result"] = out["proxy_result"].isin(["yes", "no"])
    both_results = out["has_official_result"] & out["has_proxy_result"]
    out["proxy_official_result_mismatch"] = both_results & out["official_result"].ne(out["proxy_result"])

    pnl_delta = out["official_pnl_2c"] - out["proxy_pnl_2c"]
    official_win_bool = out["official_win"].eq(True)
    proxy_win_bool = out["proxy_win"].eq(True)
    out["proxy_win_official_loss"] = both_results & proxy_win_bool & ~official_win_bool
    out["adverse_proxy_official_mismatch"] = out["proxy_official_result_mismatch"] & (
        out["proxy_win_official_loss"] | pnl_delta.lt(0).fillna(False)
    )

    out["decision_fields_complete"] = field_complete(out, REQUESTED_DECISION_FIELDS)
    out["execution_realism_fields_complete"] = field_complete(out, EXECUTION_REALISM_FIELDS)
    out["missing_decision_fields"] = missing_fields(out, REQUESTED_DECISION_FIELDS)
    out["missing_execution_realism_fields"] = missing_fields(out, EXECUTION_REALISM_FIELDS)
    out["promotion_blockers"] = out.apply(promotion_blockers, axis=1)
    out["promotion_usable"] = out["promotion_blockers"].eq("")

    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[OUTPUT_COLUMNS].sort_values(["strategy_family", "candidate", "decision_time", "market_ticker"])


def field_complete(df: pd.DataFrame, fields: list[str]) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    complete = pd.Series([True] * len(df), index=df.index)
    for field in fields:
        if field not in df.columns:
            complete &= False
        else:
            complete &= ~is_missing(df[field])
    return complete


def missing_fields(df: pd.DataFrame, fields: list[str]) -> pd.Series:
    values: list[str] = []
    for idx in df.index:
        missing: list[str] = []
        for field in fields:
            if field not in df.columns:
                missing.append(field)
                continue
            value = df.at[idx, field]
            if pd.isna(value) or str(value).strip().lower() in ["", "nan", "none", "<na>", "nat"]:
                missing.append(field)
        values.append(";".join(missing))
    return pd.Series(values, index=df.index)


def promotion_blockers(row: pd.Series) -> str:
    blockers: list[str] = []
    if not bool(row.get("has_official_result", False)):
        blockers.append("missing_official_result")
    if pd.isna(row.get("official_expiration_value")):
        blockers.append("missing_official_expiration_value")
    if not bool(row.get("decision_fields_complete", False)):
        blockers.append("missing_requested_decision_fields")
    if not bool(row.get("execution_realism_fields_complete", False)):
        blockers.append("missing_execution_realism_fields")

    fidelity = str(row.get("fidelity_label", ""))
    source = str(row.get("source", ""))
    if fidelity == "live_ws_replay_old_not_promotion":
        blockers.append("old_live_ws_replay_not_postrestart_paper")
        if source == "broad_live_ws_rest_official":
            blockers.append("broad_family_overlap_not_frozen_policy")
    elif fidelity == "paper_shadow_stale_schema_diagnostic":
        blockers.append("stale_shadow_ledger_schema_diagnostic")
    elif fidelity == "historical_snapshot_provider_time_diagnostic":
        blockers.append("predexon_historical_not_live_receive_time_replay")
        if not bool(row.get("has_official_result", False)):
            blockers.append("predexon_uncovered_rest_official")
    return ";".join(dict.fromkeys(blockers))


def summarize(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    out_rows: list[dict[str, Any]] = []
    for key, group in rows.groupby(group_cols, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        row = dict(zip(group_cols, key_tuple, strict=False))
        official = group[group["has_official_result"]]
        both = group[group["has_official_result"] & group["has_proxy_result"]]
        basis = pd.to_numeric(group["basis_usd"], errors="coerce").dropna()
        row.update(
            {
                "rows": int(len(group)),
                "official_result_rows": int(group["has_official_result"].sum()),
                "proxy_result_rows": int(group["has_proxy_result"].sum()),
                "both_result_rows": int(len(both)),
                "official_pnl_2c": round(float(pd.to_numeric(official["official_pnl_2c"], errors="coerce").sum()), 4)
                if len(official)
                else 0.0,
                "proxy_pnl_2c": round(float(pd.to_numeric(group["proxy_pnl_2c"], errors="coerce").sum()), 4),
                "mismatches": int(group["proxy_official_result_mismatch"].sum()),
                "adverse_mismatches": int(group["adverse_proxy_official_mismatch"].sum()),
                "proxy_win_official_loss_rows": int(group["proxy_win_official_loss"].sum()),
                "mismatch_rate": round(float(group["proxy_official_result_mismatch"].sum() / len(both)), 4)
                if len(both)
                else 0.0,
                "mean_basis_usd": round(float(basis.mean()), 4) if len(basis) else math.nan,
                "p95_abs_basis_usd": round(float(basis.abs().quantile(0.95)), 4) if len(basis) else math.nan,
                "decision_fields_complete_rows": int(group["decision_fields_complete"].sum()),
                "execution_realism_fields_complete_rows": int(group["execution_realism_fields_complete"].sum()),
                "promotion_usable_rows": int(group["promotion_usable"].sum()),
            }
        )
        out_rows.append(row)
    return pd.DataFrame(out_rows).sort_values(group_cols).reset_index(drop=True)


def missing_summary(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    fields = list(dict.fromkeys(EXECUTION_REALISM_FIELDS + ["official_result", "official_expiration_value", "proxy_result"]))
    for (source, fidelity, candidate), group in rows.groupby(["source", "fidelity_label", "candidate"], dropna=False):
        for field in fields:
            missing = is_missing(group[field]) if field in group.columns else pd.Series([True] * len(group), index=group.index)
            out.append(
                {
                    "source": source,
                    "fidelity_label": fidelity,
                    "candidate": candidate,
                    "field": field,
                    "rows": int(len(group)),
                    "missing_rows": int(missing.sum()),
                    "missing_rate": round(float(missing.mean()), 4) if len(group) else 0.0,
                }
            )
    return pd.DataFrame(out).sort_values(["source", "candidate", "missing_rate", "field"], ascending=[True, True, False, True])


def blocker_summary(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    counts: dict[str, int] = {}
    for blockers in rows["promotion_blockers"].fillna("").astype(str):
        if not blockers:
            counts["none"] = counts.get("none", 0) + 1
            continue
        for blocker in blockers.split(";"):
            counts[blocker] = counts.get(blocker, 0) + 1
    return pd.DataFrame(
        [{"promotion_blocker": key, "rows": value} for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    )


def write_report(out_dir: Path, rows: pd.DataFrame, source_summary: pd.DataFrame, candidate_summary: pd.DataFrame, info: dict[str, Any]) -> None:
    lines = [
        "# BTC Official Settlement Feature Table",
        "",
        f"Created UTC: `{info['created_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Canonical rows: `{len(rows)}`.",
        f"- Promotion-usable rows: `{int(rows['promotion_usable'].sum()) if not rows.empty else 0}`.",
        "- This artifact is diagnostic only; source fidelity and missing execution-realism fields remain explicit blockers.",
        "",
        "## By Source",
        "",
        source_summary.round(4).to_string(index=False) if not source_summary.empty else "No source rows.",
        "",
        "## By Candidate",
        "",
        candidate_summary.round(4).to_string(index=False) if not candidate_summary.empty else "No candidate rows.",
        "",
        "## Interpretation",
        "",
        "- `live_ws_replay_old_not_promotion` rows are useful replay diagnostics, but not post-restart paper evidence.",
        "- `paper_shadow_stale_schema_diagnostic` rows need a clean restart/migration before future rows can count.",
        "- `historical_snapshot_provider_time_diagnostic` rows are Predexon research rows, not live receive-time replay.",
        "- Rows missing official settlement, official expiration value, quote age, top visible size, or YES/NO book fields stay blocked.",
        "",
        "## Run Info",
        "",
        "```json",
        json.dumps(info, indent=2, sort_keys=True),
        "```",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    frames = [
        normalize_basis_risk(args.basis_risk_rows),
        normalize_shadow_official(args.shadow_official_rows),
        normalize_predexon(args.predexon_selected_trades, args.predexon_market_results),
    ]
    nonempty = [frame for frame in frames if not frame.empty]
    concat_ready = [frame.dropna(axis=1, how="all") for frame in nonempty]
    rows = fill_derived(pd.concat(concat_ready, ignore_index=True, sort=False)) if concat_ready else pd.DataFrame(columns=OUTPUT_COLUMNS)

    source_summary = summarize(rows, ["source", "fidelity_label"])
    candidate_summary = summarize(rows, ["strategy_family", "candidate", "side", "fidelity_label"])
    missing = missing_summary(rows)
    blockers = blocker_summary(rows)

    rows.to_csv(args.out_dir / "official_settlement_feature_table.csv", index=False)
    rows.to_parquet(args.out_dir / "official_settlement_feature_table.parquet", index=False, compression="zstd")
    source_summary.to_csv(args.out_dir / "official_settlement_feature_summary.csv", index=False)
    candidate_summary.to_csv(args.out_dir / "official_settlement_feature_by_candidate.csv", index=False)
    missing.to_csv(args.out_dir / "missing_field_summary.csv", index=False)
    blockers.to_csv(args.out_dir / "promotion_blocker_summary.csv", index=False)

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis_risk_rows": str(args.basis_risk_rows),
        "shadow_official_rows": str(args.shadow_official_rows),
        "predexon_selected_trades": str(args.predexon_selected_trades),
        "predexon_market_results": str(args.predexon_market_results),
        "rows": int(len(rows)),
        "promotion_usable_rows": int(rows["promotion_usable"].sum()) if not rows.empty else 0,
        "requested_decision_fields": REQUESTED_DECISION_FIELDS,
        "execution_realism_fields": EXECUTION_REALISM_FIELDS,
        "note": "Diagnostic only. This table standardizes settlement features and blockers; it does not promote any strategy.",
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    write_report(args.out_dir, rows, source_summary, candidate_summary, info)

    print(source_summary.round(4).to_string(index=False) if not source_summary.empty else "No rows")
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
