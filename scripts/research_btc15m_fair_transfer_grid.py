#!/usr/bin/env python3
"""Transfer-test BTC15M fair-value gates across Predexon April and live WS data.

This is intentionally narrow: it does not search arbitrary model forms.  It
checks whether the fair-value family that looked strong on Apr 1-14 survives
later April and independent websocket replay windows.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import kalshi_fee_dollars
from scripts import backtest_btc15m_f2_live_ws_holdout as ws_replay


DEFAULT_APRIL_CANDIDATES = PROJECT_ROOT / "backtest_outputs" / "btc15m_april_multisplit_search_20260515" / "side_candidates_april.parquet"
DEFAULT_CAPTURE_DB = Path.home() / ".btc_kalshi_bot" / "btc15m_live_capture.duckdb"
DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_fair_transfer_grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@dataclass(frozen=True)
class Rule:
    name: str
    fair_p_min: float
    edge_cents_min: float
    ttl_min: float
    ttl_max: float
    spread_max_cents: float
    entry_min: float
    entry_max: float
    visible_qty_min: float


def fee(price: float) -> float:
    return kalshi_fee_dollars(float(price), contracts=1, liquidity="taker")


def max_drawdown(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").fillna(0.0).cumsum()
    if x.empty:
        return 0.0
    return float((x - x.cummax()).min())


def sharpe(pnl: pd.Series) -> float:
    x = pd.to_numeric(pnl, errors="coerce").dropna()
    if len(x) < 2:
        return 0.0
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(x.mean() / sd * math.sqrt(len(x)))


def pnl_at_entry(side: pd.Series, result: pd.Series, entry: pd.Series) -> pd.Series:
    entry = pd.to_numeric(entry, errors="coerce").clip(0.01, 0.99)
    fees = entry.map(fee)
    win = side.astype(str).str.lower().eq(result.astype(str).str.lower())
    return pd.Series(np.where(win, 1.0 - entry - fees, -(entry + fees)), index=entry.index, dtype=float)


def build_rules() -> list[Rule]:
    rules: list[Rule] = []
    for fair_p in [0.60, 0.65, 0.70, 0.75, 0.80]:
        for edge in [12, 16, 20, 24]:
            for ttl_lo, ttl_hi in [(5, 12), (6, 12), (7, 11), (8, 12)]:
                for entry_lo, entry_hi in [(0.02, 0.60), (0.35, 0.60), (0.45, 0.60), (0.50, 0.60)]:
                    for qty in [1, 10, 50, 100]:
                        name = (
                            f"fp{int(fair_p*100)}_e{edge}_ttl{ttl_lo}-{ttl_hi}_"
                            f"px{int(entry_lo*100)}-{int(entry_hi*100)}_q{qty}"
                        )
                        rules.append(Rule(name, fair_p, edge, ttl_lo, ttl_hi, 2.0, entry_lo, entry_hi, qty))
    return rules


def first_trades(c: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    if c.empty:
        return c.copy()
    m = (
        pd.to_numeric(c["side_fair_p"], errors="coerce").ge(rule.fair_p_min)
        & pd.to_numeric(c["fair_edge_cents"], errors="coerce").ge(rule.edge_cents_min)
        & pd.to_numeric(c["ttl_min"], errors="coerce").between(rule.ttl_min, rule.ttl_max)
        & pd.to_numeric(c["spread_cents"], errors="coerce").le(rule.spread_max_cents)
        & pd.to_numeric(c["entry_price"], errors="coerce").between(rule.entry_min, rule.entry_max)
        & pd.to_numeric(c["visible_qty"], errors="coerce").ge(rule.visible_qty_min)
    )
    if not m.any():
        return c.iloc[0:0].copy()
    time_col = "timestamp_utc" if "timestamp_utc" in c.columns else "received_at_utc"
    sort_cols = ["event_ticker", time_col, "fair_edge_cents"]
    return (
        c.loc[m]
        .sort_values(sort_cols, ascending=[True, True, False])
        .drop_duplicates("event_ticker", keep="first")
        .sort_values(time_col)
        .copy()
    )


def metrics(trades: pd.DataFrame, split: str, pnl_col: str) -> dict[str, object]:
    pnl = pd.to_numeric(trades[pnl_col], errors="coerce").dropna() if pnl_col in trades else pd.Series(dtype=float)
    wins = pd.to_numeric(trades.loc[pnl.index, "win"], errors="coerce") if len(pnl) and "win" in trades else pd.Series(dtype=float)
    return {
        "split": split,
        "trades": int(len(pnl)),
        "pnl": round(float(pnl.sum()), 4) if len(pnl) else 0.0,
        "win_rate": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "max_dd": round(max_drawdown(pnl), 4),
        "sharpe": round(sharpe(pnl), 4),
        "avg_entry": round(float(pd.to_numeric(trades.loc[pnl.index, "entry_price"], errors="coerce").mean()), 4) if len(pnl) else 0.0,
        "avg_edge": round(float(pd.to_numeric(trades.loc[pnl.index, "fair_edge_cents"], errors="coerce").mean()), 4) if len(pnl) else 0.0,
        "avg_qty": round(float(pd.to_numeric(trades.loc[pnl.index, "visible_qty"], errors="coerce").mean()), 4) if len(pnl) else 0.0,
    }


def load_april(path: Path) -> pd.DataFrame:
    cols = [
        "timestamp_utc",
        "event_ticker",
        "market_ticker",
        "side",
        "entry_price",
        "visible_qty",
        "entry_fee",
        "side_fair_p",
        "fair_edge_cents",
        "ttl_min",
        "spread_cents",
        "result",
        "win",
    ]
    c = pd.read_parquet(path, columns=cols)
    c["timestamp_utc"] = pd.to_datetime(c["timestamp_utc"], utc=True, format="mixed")
    c["entry_price"] = pd.to_numeric(c["entry_price"], errors="coerce")
    c["entry_fee"] = pd.to_numeric(c["entry_fee"], errors="coerce")
    c["pnl_2c"] = pnl_at_entry(c["side"], c["result"], c["entry_price"] + 0.02)
    return c


def load_ws_candidates(capture_db: Path, start: str | None, end: str | None) -> tuple[pd.DataFrame, dict[str, object]]:
    top, btc, lifecycle, _decisions, info = ws_replay.load_capture(capture_db, start, end)
    meta, official_results = ws_replay.metadata_from_lifecycle(lifecycle)
    q = ws_replay.prepare_quotes(top, btc, meta, official_results)
    q = ws_replay.add_proxy_results(q, btc)
    capture_end = pd.Timestamp(info["capture_end_utc"])
    q = q[q["close_time"].le(capture_end) & q["proxy_result"].astype(str).str.lower().isin(["yes", "no"])].copy()
    # Avoid materializing both sides for every dense websocket row.  The grid
    # below only considers fair_p >= 0.60, edge >= 12c, TTL 5-12, spread <=2c,
    # entry 2-60c, so prefilter against that superset first.
    q = q[
        pd.to_numeric(q["ttl_min"], errors="coerce").between(5, 12)
        & pd.to_numeric(q["spread_cents"], errors="coerce").le(2)
    ].copy()
    if q.empty:
        return pd.DataFrame(), info

    p_yes = pd.to_numeric(q["lognormal_p_yes"], errors="coerce").to_numpy(dtype=float)
    yes_entry = pd.to_numeric(q["yes_ask"], errors="coerce").to_numpy(dtype=float)
    no_entry = pd.to_numeric(q["no_ask"], errors="coerce").to_numpy(dtype=float)
    yes_qty = pd.to_numeric(q["yes_ask_qty"], errors="coerce").to_numpy(dtype=float)
    no_qty = pd.to_numeric(q["no_ask_qty"], errors="coerce").to_numpy(dtype=float)
    yes_fee = ws_replay.fee_array(yes_entry)
    no_fee = ws_replay.fee_array(no_entry)
    yes_edge = (p_yes - yes_entry) * 100.0 - yes_fee * 100.0
    no_p = 1.0 - p_yes
    no_edge = (no_p - no_entry) * 100.0 - no_fee * 100.0

    base_cols = [
        "received_at_ns",
        "received_at_utc",
        "event_ticker",
        "market_ticker",
        "seq",
        "spread_cents",
        "close_time",
        "proxy_result",
    ]
    frames: list[pd.DataFrame] = []
    yes_mask = (
        np.isfinite(p_yes)
        & (p_yes >= 0.60)
        & np.isfinite(yes_edge)
        & (yes_edge >= 12)
        & (yes_entry >= 0.02)
        & (yes_entry <= 0.60)
        & (yes_qty >= 1)
    )
    if yes_mask.any():
        y = q.loc[yes_mask, base_cols + ["ttl_min"]].copy()
        y["side"] = "yes"
        y["entry_price"] = yes_entry[yes_mask]
        y["visible_qty"] = yes_qty[yes_mask]
        y["side_fair_p"] = p_yes[yes_mask]
        y["entry_fee"] = yes_fee[yes_mask]
        y["fair_edge_cents"] = yes_edge[yes_mask]
        frames.append(y)
    no_mask = (
        np.isfinite(no_p)
        & (no_p >= 0.60)
        & np.isfinite(no_edge)
        & (no_edge >= 12)
        & (no_entry >= 0.02)
        & (no_entry <= 0.60)
        & (no_qty >= 1)
    )
    if no_mask.any():
        n = q.loc[no_mask, base_cols + ["ttl_min"]].copy()
        n["side"] = "no"
        n["entry_price"] = no_entry[no_mask]
        n["visible_qty"] = no_qty[no_mask]
        n["side_fair_p"] = no_p[no_mask]
        n["entry_fee"] = no_fee[no_mask]
        n["fair_edge_cents"] = no_edge[no_mask]
        frames.append(n)
    if not frames:
        return pd.DataFrame(), info
    c = pd.concat(frames, ignore_index=True)
    c["received_at_utc"] = pd.to_datetime(c["received_at_utc"], utc=True, format="mixed")
    c["timestamp_utc"] = c["received_at_utc"]
    c["result"] = c["proxy_result"]
    c["win"] = c["side"].astype(str).str.lower().eq(c["result"].astype(str).str.lower())
    c["pnl_2c"] = pnl_at_entry(c["side"], c["result"], c["entry_price"] + 0.02)
    return c, info


def add_split(c: pd.DataFrame, split_col: str, splits: list[tuple[str, str, str]]) -> pd.DataFrame:
    out = c.copy()
    out[split_col] = "outside"
    t = pd.to_datetime(out["timestamp_utc"], utc=True, format="mixed")
    for name, start, end in splits:
        out.loc[t.ge(pd.Timestamp(start, tz="UTC")) & t.lt(pd.Timestamp(end, tz="UTC")), split_col] = name
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--april-candidates", type=Path, default=DEFAULT_APRIL_CANDIDATES)
    ap.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    ap.add_argument("--ws-start", default=None)
    ap.add_argument("--ws-end", default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    april = load_april(args.april_candidates)
    april = add_split(
        april,
        "split",
        [
            ("apr01_14", "2026-04-01", "2026-04-15"),
            ("apr15_30", "2026-04-15", "2026-05-01"),
        ],
    )
    ws, info = load_ws_candidates(args.capture_db, args.ws_start, args.ws_end)
    ws = add_split(
        ws,
        "split",
        [
            ("ws_may12_13", "2026-05-12", "2026-05-14"),
            ("ws_may14_16", "2026-05-14", "2026-05-16"),
        ],
    )

    rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    rules = build_rules()
    for i, rule in enumerate(rules, start=1):
        if i % 250 == 0:
            print(f"evaluated {i:,}/{len(rules):,}", flush=True)
        for dataset, c in [("predexon_april", april), ("live_ws", ws)]:
            for split in sorted(c["split"].unique()):
                if split == "outside":
                    continue
                trades = first_trades(c[c["split"].eq(split)], rule)
                row = {"rule": rule.name, "dataset": dataset}
                row.update(metrics(trades, split, "pnl_2c"))
                row.update(
                    {
                        "fair_p_min": rule.fair_p_min,
                        "edge_cents_min": rule.edge_cents_min,
                        "ttl_min": rule.ttl_min,
                        "ttl_max": rule.ttl_max,
                        "entry_min": rule.entry_min,
                        "entry_max": rule.entry_max,
                        "visible_qty_min": rule.visible_qty_min,
                    }
                )
                rows.append(row)
                if not trades.empty:
                    t = trades[
                        [
                            "timestamp_utc",
                            "event_ticker",
                            "market_ticker",
                            "side",
                            "entry_price",
                            "visible_qty",
                            "side_fair_p",
                            "fair_edge_cents",
                            "ttl_min",
                            "spread_cents",
                            "result",
                            "win",
                            "pnl_2c",
                        ]
                    ].copy()
                    t["rule"] = rule.name
                    t["dataset"] = dataset
                    t["split"] = split
                    trade_frames.append(t)

    summary = pd.DataFrame(rows)
    pivot = summary.pivot_table(
        index=["rule", "fair_p_min", "edge_cents_min", "ttl_min", "ttl_max", "entry_min", "entry_max", "visible_qty_min"],
        columns=["dataset", "split"],
        values=["trades", "pnl", "win_rate", "max_dd", "sharpe"],
        aggfunc="first",
    )
    pivot.columns = ["_".join(map(str, col)).strip() for col in pivot.columns.to_flat_index()]
    pivot = pivot.reset_index()
    for col in [
        "pnl_predexon_april_apr01_14",
        "pnl_predexon_april_apr15_30",
        "pnl_live_ws_ws_may12_13",
        "pnl_live_ws_ws_may14_16",
    ]:
        if col not in pivot:
            pivot[col] = 0.0
    pivot["passes_transfer"] = (
        pivot["pnl_predexon_april_apr01_14"].gt(0)
        & pivot["pnl_predexon_april_apr15_30"].gt(0)
        & pivot["pnl_live_ws_ws_may12_13"].gt(0)
        & pivot["pnl_live_ws_ws_may14_16"].gt(0)
    )
    pivot["score"] = (
        pivot["pnl_predexon_april_apr01_14"].clip(upper=20)
        + pivot["pnl_predexon_april_apr15_30"].clip(upper=20)
        + 3.0 * pivot["pnl_live_ws_ws_may12_13"]
        + 3.0 * pivot["pnl_live_ws_ws_may14_16"]
    )
    ranked = pivot.sort_values(["passes_transfer", "score"], ascending=[False, False])
    summary.to_csv(args.out / "split_summary.csv", index=False)
    ranked.to_csv(args.out / "ranked_rules.csv", index=False)
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_parquet(args.out / "trade_rows.parquet", index=False, compression="zstd")
    (args.out / "run_info.json").write_text(pd.Series(info).to_json(indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(ranked.head(25).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
