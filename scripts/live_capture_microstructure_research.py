#!/usr/bin/env python3
"""Microstructure research on observed live-capture order decisions.

This script is read-only. It evaluates hypotheses that require websocket
order-decision rows rather than historical candles: failed reprice toxicity,
FOK no-fill toxicity, visible top liquidity, and same-event cooldowns.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts.live_capture_backtest_audit import OfficialResults


DEFAULT_DB = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless.duckdb"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / "live_capture_microstructure_research_20260511"
DEFAULT_CACHE_SEED = PROJECT_ROOT / "backtest_outputs" / "iterative_signal_research_desk_20260511_run3" / "official_results_cache.json"

NY_TZ = ZoneInfo("America/New_York")
EVENT_RE = re.compile(r"^KXBTCD-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<hour>\d{2})$")
STRIKE_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)", re.IGNORECASE)
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class Hypothesis:
    name: str
    description: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def close_from_event(event_ticker: str) -> pd.Timestamp | None:
    match = EVENT_RE.match(str(event_ticker).upper())
    if not match:
        return None
    month = MONTHS.get(match.group("mon"))
    if month is None:
        return None
    return pd.Timestamp(
        year=2000 + int(match.group("yy")),
        month=month,
        day=int(match.group("day")),
        hour=int(match.group("hour")),
        tz=NY_TZ,
    ).tz_convert("UTC")


def strike_from_ticker(ticker: str) -> float:
    match = STRIKE_RE.search(str(ticker).upper())
    return float(match.group("strike")) + 0.01 if match else float("nan")


def safe_mask(mask: pd.Series | np.ndarray | bool, index: pd.Index) -> pd.Series:
    if isinstance(mask, (bool, np.bool_)):
        return pd.Series(bool(mask), index=index)
    out = mask if isinstance(mask, pd.Series) else pd.Series(mask, index=index)
    return out.reindex(index).fillna(False).astype(bool)


def max_drawdown(values: pd.Series) -> float:
    clean = values.dropna().astype(float)
    if clean.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), clean.cumsum()], ignore_index=True)
    return float((equity - equity.cummax()).min())


def seed_cache(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if DEFAULT_CACHE_SEED.exists():
        path.write_text(DEFAULT_CACHE_SEED.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text("{}", encoding="utf-8")


def load_decisions(db_path: Path, cache_path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    tables = {row[0] for row in con.execute("show tables").fetchall()}
    if "order_decision_dedup" not in tables:
        con.close()
        return pd.DataFrame()
    df = con.execute(
        """
        WITH d AS (
            SELECT row_number() OVER () AS decision_id, *
            FROM order_decision_dedup
            WHERE market_ticker IS NOT NULL
              AND side IS NOT NULL
              AND entry_price IS NOT NULL
            ORDER BY received_at_ns
        )
        SELECT d.*,
               top.received_at_ns AS top_received_at_ns,
               top.yes_ask,
               top.yes_ask_qty,
               top.no_ask,
               top.no_ask_qty,
               top.yes_bid,
               top.yes_bid_qty,
               top.no_bid,
               top.no_bid_qty,
               top5.yes_bid AS plus5_yes_bid,
               top5.no_bid AS plus5_no_bid,
               top30.yes_bid AS plus30_yes_bid,
               top30.no_bid AS plus30_no_bid
        FROM d
        LEFT JOIN LATERAL (
            SELECT *
            FROM ws_orderbook_top_dedup t
            WHERE t.capture_label = d.capture_label
              AND t.market_ticker = d.market_ticker
              AND t.received_at_ns <= d.received_at_ns
            ORDER BY t.received_at_ns DESC
            LIMIT 1
        ) top ON TRUE
        LEFT JOIN LATERAL (
            SELECT *
            FROM ws_orderbook_top_dedup t
            WHERE t.capture_label = d.capture_label
              AND t.market_ticker = d.market_ticker
              AND t.received_at_ns >= d.received_at_ns + 5000000000
            ORDER BY t.received_at_ns ASC
            LIMIT 1
        ) top5 ON TRUE
        LEFT JOIN LATERAL (
            SELECT *
            FROM ws_orderbook_top_dedup t
            WHERE t.capture_label = d.capture_label
              AND t.market_ticker = d.market_ticker
              AND t.received_at_ns >= d.received_at_ns + 30000000000
            ORDER BY t.received_at_ns ASC
            LIMIT 1
        ) top30 ON TRUE
        ORDER BY d.received_at_ns
        """
    ).fetchdf()
    con.close()
    if df.empty:
        return df
    df["received_at"] = pd.to_datetime(df["received_at_utc"], utc=True, errors="coerce")
    df["close_time"] = df["event_ticker"].map(close_from_event)
    df["ttl_min"] = (df["close_time"] - df["received_at"]).dt.total_seconds() / 60.0
    df["mode_clean"] = df["mode"].astype(str).str.replace("paper:", "", regex=False)
    df["side"] = df["side"].astype(str).str.lower()
    df["strike"] = df["market_ticker"].map(strike_from_ticker)
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["net_edge_cents"] = pd.to_numeric(df["net_edge_cents"], errors="coerce")
    df["btc_spot"] = pd.to_numeric(df["btc_spot"], errors="coerce")
    df["contracts"] = pd.to_numeric(df["contracts"], errors="coerce").fillna(1).astype(int)
    df["side_distance_usd"] = np.where(df["side"].eq("yes"), df["btc_spot"] - df["strike"], df["strike"] - df["btc_spot"])
    df["visible_side_ask"] = np.where(df["side"].eq("yes"), df["yes_ask"], df["no_ask"])
    df["visible_side_ask_qty"] = np.where(df["side"].eq("yes"), df["yes_ask_qty"], df["no_ask_qty"])
    df["price_matches_entry"] = np.isclose(df["visible_side_ask"], df["entry_price"], atol=0.001, equal_nan=False)
    df["plus5_side_bid"] = np.where(df["side"].eq("yes"), df["plus5_yes_bid"], df["plus5_no_bid"])
    df["plus30_side_bid"] = np.where(df["side"].eq("yes"), df["plus30_yes_bid"], df["plus30_no_bid"])
    df["markout_5s_cents"] = 100.0 * (pd.to_numeric(df["plus5_side_bid"], errors="coerce") - df["entry_price"])
    df["markout_30s_cents"] = 100.0 * (pd.to_numeric(df["plus30_side_bid"], errors="coerce") - df["entry_price"])

    seed_cache(cache_path)
    official = OfficialResults(cache_path)
    results: list[str | None] = []
    statuses: list[str | None] = []
    values: list[float | None] = []
    for ticker in df["market_ticker"].astype(str):
        row = official.get(ticker)
        results.append(row.get("result"))
        statuses.append(row.get("status"))
        try:
            values.append(float(row.get("expiration_value")) if row.get("expiration_value") is not None else None)
        except Exception:
            values.append(None)
    official.write()
    df["official_result"] = results
    df["official_status"] = statuses
    df["official_expiration_value"] = values
    df["settled"] = df["official_result"].isin(["yes", "no"])
    fee_1c = df["entry_price"].map(lambda p: kalshi_fee_dollars(float(p), contracts=1, liquidity="taker") if pd.notna(p) else np.nan)
    df["premium_1c"] = df["entry_price"] + fee_1c
    df["pnl_1c_if_filled"] = np.where(
        df["settled"],
        np.where(df["side"].eq(df["official_result"]), 1.0, 0.0) - df["premium_1c"],
        np.nan,
    )
    # `submit/before_order` rows are pre-order audit records, not fills. Counting
    # them here would duplicate live order decisions.
    df["executed_like"] = df["action"].isin(["filled", "paper_fill"]) | df["detail"].isin(["filled", "order_response"])
    df["failed_reprice"] = df["detail"].eq("failed_ws_reprice_filter")
    df["fok_no_fill"] = df["detail"].astype(str).str.contains("fok|409|no_fill|insufficient", case=False, regex=True)
    return df


def add_cooldown_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values("received_at_ns").copy()
    out["seconds_since_event_failed_reprice"] = np.nan
    out["seconds_since_market_failed_reprice"] = np.nan
    failed_by_event: dict[tuple[str, str], int] = {}
    failed_by_market: dict[tuple[str, str, str], int] = {}
    event_values: list[float] = []
    market_values: list[float] = []
    for row in out.itertuples(index=False):
        mode = str(row.mode_clean)
        event = str(row.event_ticker)
        market = str(row.market_ticker)
        ns = int(row.received_at_ns)
        event_key = (mode, event)
        market_key = (mode, event, market)
        event_values.append((ns - failed_by_event[event_key]) / 1e9 if event_key in failed_by_event else math.nan)
        market_values.append((ns - failed_by_market[market_key]) / 1e9 if market_key in failed_by_market else math.nan)
        if bool(row.failed_reprice):
            failed_by_event[event_key] = ns
            failed_by_market[market_key] = ns
    out["seconds_since_event_failed_reprice"] = event_values
    out["seconds_since_market_failed_reprice"] = market_values
    return out


def summarize(df: pd.DataFrame, name: str, description: str) -> dict[str, object]:
    part = df[df["settled"].eq(True)].dropna(subset=["pnl_1c_if_filled", "premium_1c"]).copy()
    if part.empty:
        return {
            "hypothesis": name,
            "description": description,
            "rows": 0,
            "events": 0,
            "pnl_1c_if_filled": 0.0,
            "premium_1c": 0.0,
            "rop": 0.0,
            "win_rate": 0.0,
            "max_dd": 0.0,
            "avg_entry": 0.0,
            "avg_edge": 0.0,
            "avg_visible_qty": 0.0,
            "avg_markout_30s_cents": 0.0,
        }
    pnl = part["pnl_1c_if_filled"].astype(float)
    premium = part["premium_1c"].astype(float)
    return {
        "hypothesis": name,
        "description": description,
        "rows": int(len(part)),
        "events": int(part["event_ticker"].nunique()),
        "pnl_1c_if_filled": float(pnl.sum()),
        "premium_1c": float(premium.sum()),
        "rop": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "max_dd": max_drawdown(pnl),
        "avg_entry": float(part["entry_price"].mean()),
        "avg_edge": float(part["net_edge_cents"].mean()),
        "avg_visible_qty": float(part["visible_side_ask_qty"].mean()),
        "avg_markout_30s_cents": float(part["markout_30s_cents"].mean()),
    }


def hypotheses() -> list[Hypothesis]:
    c = lambda name: (lambda d: pd.to_numeric(d[name], errors="coerce"))
    return [
        Hypothesis("executed", "Filled/paper/submit decisions only", lambda d: d["executed_like"]),
        Hypothesis("failed_reprice_forced", "Rows skipped because websocket reprice/filter failed, if forced", lambda d: d["failed_reprice"]),
        Hypothesis("fok_no_fill_forced", "FOK/no-fill/not-filled decisions, if forced", lambda d: d["fok_no_fill"] | d["action"].eq("not_filled")),
        Hypothesis("executed_no_recent_event_reprice_60s", "Executed rows with no event failed-reprice in prior 60s", lambda d: d["executed_like"] & (c("seconds_since_event_failed_reprice")(d).isna() | (c("seconds_since_event_failed_reprice")(d) > 60))),
        Hypothesis("executed_no_recent_event_reprice_180s", "Executed rows with no event failed-reprice in prior 180s", lambda d: d["executed_like"] & (c("seconds_since_event_failed_reprice")(d).isna() | (c("seconds_since_event_failed_reprice")(d) > 180))),
        Hypothesis("executed_after_event_reprice_180s", "Executed rows within 180s after same-event failed reprice", lambda d: d["executed_like"] & c("seconds_since_event_failed_reprice")(d).between(0, 180)),
        Hypothesis("executed_price_match", "Executed with top ask matching entry", lambda d: d["executed_like"] & d["price_matches_entry"]),
        Hypothesis("executed_price_mismatch", "Executed with top ask not matching entry", lambda d: d["executed_like"] & ~d["price_matches_entry"]),
        Hypothesis("executed_qty_ge3", "Executed with visible side ask qty >=3", lambda d: d["executed_like"] & (c("visible_side_ask_qty")(d) >= 3)),
        Hypothesis("executed_qty_ge10", "Executed with visible side ask qty >=10", lambda d: d["executed_like"] & (c("visible_side_ask_qty")(d) >= 10)),
        Hypothesis("executed_qty_lt10", "Executed with visible side ask qty <10", lambda d: d["executed_like"] & (c("visible_side_ask_qty")(d) < 10)),
        Hypothesis("executed_no_only", "Executed NO decisions", lambda d: d["executed_like"] & d["side"].eq("no")),
        Hypothesis("executed_yes_only", "Executed YES decisions", lambda d: d["executed_like"] & d["side"].eq("yes")),
        Hypothesis("executed_edge_gte16", "Executed with net edge >=16c", lambda d: d["executed_like"] & (c("net_edge_cents")(d) >= 16)),
        Hypothesis("executed_edge_lt16", "Executed with net edge <16c", lambda d: d["executed_like"] & (c("net_edge_cents")(d) < 16)),
        Hypothesis("executed_markout30_pos", "Executed rows with positive 30s markout", lambda d: d["executed_like"] & (c("markout_30s_cents")(d) > 0)),
        Hypothesis("executed_markout30_neg", "Executed rows with non-positive 30s markout", lambda d: d["executed_like"] & (c("markout_30s_cents")(d) <= 0)),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.output_dir / "official_results_cache.json"
    decisions = add_cooldown_features(load_decisions(args.capture_db, cache_path))
    if decisions.empty:
        print("no decisions loaded")
        return 1
    rows = []
    for hyp in hypotheses():
        mask = safe_mask(hyp.predicate(decisions), decisions.index)
        rows.append(summarize(decisions[mask].copy(), hyp.name, hyp.description))
        for mode, part in decisions[mask].groupby("mode_clean", dropna=False):
            row = summarize(part.copy(), f"{hyp.name}:{mode}", hyp.description + f" / mode={mode}")
            rows.append(row)
    summary = pd.DataFrame(rows).sort_values(["pnl_1c_if_filled", "rows"], ascending=[False, False])
    decisions.to_csv(args.output_dir / "order_decisions_with_microstructure.csv", index=False)
    summary.to_csv(args.output_dir / "microstructure_hypothesis_summary.csv", index=False)
    manifest = {
        "capture_db": str(args.capture_db),
        "rows": int(len(decisions)),
        "settled_rows": int(decisions["settled"].sum()),
        "first": str(decisions["received_at"].min()),
        "last": str(decisions["received_at"].max()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "PnL is one-contract if filled at observed entry price with Kalshi taker entry fee.",
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"output_dir={args.output_dir}")
    print(pd.DataFrame([manifest]).to_string(index=False))
    print("\nTOP MICROSTRUCTURE HYPOTHESES")
    cols = ["hypothesis", "rows", "events", "pnl_1c_if_filled", "rop", "win_rate", "max_dd", "avg_entry", "avg_edge", "avg_visible_qty", "avg_markout_30s_cents"]
    print(summary[cols].head(30).to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
