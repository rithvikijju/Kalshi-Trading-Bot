#!/usr/bin/env python3
"""Replay selected BTC15M regime rules on locally captured websocket data.

This is the live-capture promotion gate for rules first studied on Predexon.
It uses the same local websocket capture functions as the F2 replay script,
then applies fixed predicates before taking the first qualifying signal per
event.  No Kalshi requests are made.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    add_pnl,
    add_proxy_results,
    compare_official_proxy,
    daily,
    fee_array,
    load_capture,
    metrics,
    prepare_quotes,
)


DEFAULT_CAPTURE_DB = Path(os.path.expanduser("~/.btc_kalshi_bot/btc15m_live_capture.duckdb"))
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_regime_live_ws_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Rule:
    name: str
    fair_p_min: float = 0.60
    edge_min: float = 12.0
    ttl_min: float = 5.0
    ttl_max: float = 12.0
    spread_max: float = 2.0
    entry_min: float = 0.02
    entry_max: float = 0.60
    qty_min: float = 1.0
    rv_min: float | None = None
    fair_edge_max: float | None = None
    side_btc_ret_min: float | None = None
    rationale: str = ""


RULES = [
    Rule("base_f2", rationale="baseline frozen F2"),
    Rule("edge12_to_28_highrv32", rv_min=0.32, fair_edge_max=28.0, rationale="best fixed-family validation rule"),
    Rule("highrv320_q1_base", rv_min=0.32, rationale="minimal high-RV gate"),
    Rule("highrv320_q100_base", rv_min=0.32, qty_min=100.0, rationale="high-RV plus visible liquidity"),
    Rule("liquid_highrv32_not_against", rv_min=0.32, qty_min=100.0, side_btc_ret_min=0.0, rationale="least-bad Jan external rule"),
    Rule("strict_plus_highrv20", ttl_min=10.0, ttl_max=12.0, entry_max=0.55, qty_min=50.0, rv_min=0.20, rationale="strict shadow plus RV"),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def add_btc_ret_to_quotes(q: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    out = q.copy()
    b = btc.dropna(subset=["received_at_utc", "price"]).copy()
    b["received_at_utc"] = pd.to_datetime(b["received_at_utc"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    b["price"] = pd.to_numeric(b["price"], errors="coerce")
    b = b.dropna(subset=["received_at_utc", "price"]).sort_values("received_at_utc")
    if b.empty or out.empty:
        out["btc_ret_3m_bps"] = np.nan
        return out
    look = out[["received_at_utc", "btc_spot_model"]].copy().reset_index(drop=True)
    look["_orig_idx"] = np.arange(len(look))
    look["received_at_utc"] = pd.to_datetime(look["received_at_utc"], utc=True, errors="coerce").astype("datetime64[ns, UTC]")
    look["lookback_time"] = (look["received_at_utc"] - pd.Timedelta(minutes=3)).astype("datetime64[ns, UTC]")
    merged = pd.merge_asof(
        look.sort_values("lookback_time"),
        b[["received_at_utc", "price"]].rename(columns={"received_at_utc": "btc_lookback_time", "price": "btc_lookback_price"}),
        left_on="lookback_time",
        right_on="btc_lookback_time",
        direction="backward",
        tolerance=pd.Timedelta(minutes=2),
    ).sort_values("_orig_idx")
    ret = np.log(
        pd.to_numeric(look["btc_spot_model"], errors="coerce").to_numpy(dtype=float)
        / pd.to_numeric(merged["btc_lookback_price"], errors="coerce").to_numpy(dtype=float)
    ) * 10000.0
    out["btc_ret_3m_bps"] = ret
    return out


def select_rule_trades(q: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    if q.empty:
        return pd.DataFrame()
    q = q.copy()
    common = (
        q["ttl_min"].between(rule.ttl_min, rule.ttl_max, inclusive="both")
        & q["spread_cents"].le(rule.spread_max)
        & q["btc_spot_age_sec"].between(0, 120)
        & q["floor_strike"].notna()
        & q["close_time"].notna()
    )
    if rule.rv_min is not None:
        common &= q["rv_60m"].ge(rule.rv_min)

    p_yes = pd.to_numeric(q["lognormal_p_yes"], errors="coerce")
    yes_entry = pd.to_numeric(q["yes_ask"], errors="coerce")
    no_entry = pd.to_numeric(q["no_ask"], errors="coerce")
    yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce")
    no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce")
    yes_fee = pd.Series(fee_array(yes_entry), index=q.index)
    no_fee = pd.Series(fee_array(no_entry), index=q.index)
    yes_edge = (p_yes - yes_entry) * 100.0 - yes_fee * 100.0
    no_p = 1.0 - p_yes
    no_edge = (no_p - no_entry) * 100.0 - no_fee * 100.0

    yes_side_btc = pd.to_numeric(q["btc_ret_3m_bps"], errors="coerce")
    no_side_btc = -yes_side_btc

    yes_mask = (
        common
        & p_yes.ge(rule.fair_p_min)
        & yes_edge.ge(rule.edge_min)
        & yes_entry.between(rule.entry_min, rule.entry_max, inclusive="both")
        & yes_qty.ge(rule.qty_min)
    )
    no_mask = (
        common
        & no_p.ge(rule.fair_p_min)
        & no_edge.ge(rule.edge_min)
        & no_entry.between(rule.entry_min, rule.entry_max, inclusive="both")
        & no_qty.ge(rule.qty_min)
    )
    if rule.fair_edge_max is not None:
        yes_mask &= yes_edge.le(rule.fair_edge_max)
        no_mask &= no_edge.le(rule.fair_edge_max)
    if rule.side_btc_ret_min is not None:
        yes_mask &= yes_side_btc.ge(rule.side_btc_ret_min)
        no_mask &= no_side_btc.ge(rule.side_btc_ret_min)

    base_cols = [
        "received_at_ns",
        "received_at_utc",
        "event_ticker",
        "market_ticker",
        "seq",
        "yes_ask",
        "yes_ask_qty",
        "no_ask",
        "no_ask_qty",
        "spread_cents",
        "btc_spot_model",
        "btc_spot_age_sec",
        "btc_ret_3m_bps",
        "rv_60m",
        "floor_strike",
        "close_time",
        "official_result",
        "close_btc_spot",
        "close_btc_time",
        "proxy_result",
        "proxy_distance_usd",
        "near_strike_proxy",
        "ttl_min",
    ]
    frames = []
    if bool(yes_mask.any()):
        y = q.loc[yes_mask, base_cols].copy()
        y["side"] = "yes"
        y["entry_price"] = yes_entry.loc[yes_mask].to_numpy()
        y["visible_qty"] = yes_qty.loc[yes_mask].to_numpy()
        y["side_fair_p"] = p_yes.loc[yes_mask].to_numpy()
        y["entry_fee"] = yes_fee.loc[yes_mask].to_numpy()
        y["fair_edge_cents"] = yes_edge.loc[yes_mask].to_numpy()
        y["opp_entry"] = no_entry.loc[yes_mask].to_numpy()
        y["side_btc_ret_3m_bps"] = yes_side_btc.loc[yes_mask].to_numpy()
        frames.append(y)
    if bool(no_mask.any()):
        n = q.loc[no_mask, base_cols].copy()
        n["side"] = "no"
        n["entry_price"] = no_entry.loc[no_mask].to_numpy()
        n["visible_qty"] = no_qty.loc[no_mask].to_numpy()
        n["side_fair_p"] = no_p.loc[no_mask].to_numpy()
        n["entry_fee"] = no_fee.loc[no_mask].to_numpy()
        n["fair_edge_cents"] = no_edge.loc[no_mask].to_numpy()
        n["opp_entry"] = yes_entry.loc[no_mask].to_numpy()
        n["side_btc_ret_3m_bps"] = no_side_btc.loc[no_mask].to_numpy()
        frames.append(n)
    if not frames:
        return pd.DataFrame()
    hits = pd.concat(frames, ignore_index=True)
    hits["premium"] = hits["entry_price"] + hits["entry_fee"]
    return (
        hits.sort_values(["event_ticker", "received_at_ns", "fair_edge_cents", "seq", "side"], ascending=[True, True, False, True, True])
        .drop_duplicates("event_ticker", keep="first")
        .reset_index(drop=True)
    )


def run_rule(c: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    t = select_rule_trades(c, rule)
    if t.empty:
        return t
    for result_col, out_col, slip in [
        ("proxy_result", "pnl_proxy_0c", 0),
        ("proxy_result", "pnl_proxy_2c", 2),
        ("official_result", "pnl_official_0c", 0),
        ("official_result", "pnl_official_2c", 2),
    ]:
        t = add_pnl(t, result_col, out_col, slip)
    t["rule"] = rule.name
    return t


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    top, btc, lifecycle, decisions, capture_info = load_capture(args.capture_db, args.start, args.end)
    meta, results = prepare_meta(lifecycle)
    q = prepare_quotes(top, btc, meta, results)
    q = add_proxy_results(q, btc)
    c = add_btc_ret_to_quotes(q, btc)

    frames = []
    rows = []
    for rule in RULES:
        t = run_rule(c, rule)
        if not t.empty:
            frames.append(t)
        for mode, pnl_col in [
            ("proxy_2c", "pnl_proxy_2c"),
            ("official_2c_subset", "pnl_official_2c"),
        ]:
            row = {"rule": rule.name, "result_mode": mode}
            if not t.empty:
                row.update(metrics(t, pnl_col))
            else:
                row.update({"trades": 0, "pnl": 0.0, "return_on_100": 0.0, "premium": 0.0, "rop": 0.0, "win_rate": 0.0, "max_dd": 0.0, "sharpe": 0.0})
            rows.append(row)

    summary = pd.DataFrame(rows)
    all_trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    summary.to_csv(args.out_dir / "regime_live_ws_summary.csv", index=False)
    if not all_trades.empty:
        all_trades.to_parquet(args.out_dir / "regime_live_ws_trades.parquet", index=False)
        for rule, g in all_trades.groupby("rule"):
            daily(g, "pnl_proxy_2c").to_csv(args.out_dir / f"{rule}_daily_proxy_2c.csv", index=False)
    rule_defs = pd.DataFrame([asdict(r) for r in RULES])
    rule_defs.to_csv(args.out_dir / "rule_definitions.csv", index=False)
    info = {
        **capture_info,
        "candidate_rows": int(len(c)),
        "rules": len(RULES),
        "official_proxy": compare_official_proxy(all_trades) if not all_trades.empty else {},
    }
    (args.out_dir / "run_info.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
    (args.out_dir / "report.md").write_text(
        "# BTC15M Regime Live WS Replay\n\n"
        + summary.to_string(index=False)
        + "\n\nRules are fixed from Predexon diagnostics; this run uses only local websocket capture.\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.out_dir}")
    print(summary.to_string(index=False))
    return 0


def prepare_meta(lifecycle: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    from scripts.backtest_btc15m_f2_live_ws_holdout import metadata_from_lifecycle

    return metadata_from_lifecycle(lifecycle)


if __name__ == "__main__":
    raise SystemExit(main())
