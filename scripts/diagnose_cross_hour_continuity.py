#!/usr/bin/env python3
"""Explore same-strike cross-hour carryover around KXBTCD event boundaries.

This is a research diagnostic, not a trading rule. It checks whether the same
strike's final quote in one hourly event and early quote in the next hourly
event contain a systematic transition effect.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "research_datamart" / "research.duckdb"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose KXBTCD cross-hour quote continuity.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "backtest_outputs" / "cross_hour_continuity")
    parser.add_argument("--next-minute", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    con = duckdb.connect(str(args.db), read_only=True)
    rows = con.execute(
        """
        WITH hourly AS (
            SELECT event_ticker, min(open_time) AS open_time, max(close_time) AS close_time
            FROM kalshi_markets
            WHERE is_hourly_kxbtcd AND is_cumulative
            GROUP BY 1
        ),
        pairs AS (
            SELECT h1.event_ticker AS prev_event,
                   h2.event_ticker AS next_event,
                   h1.close_time AS boundary_time
            FROM hourly h1
            JOIN hourly h2 ON h2.open_time = h1.close_time
        ),
        prev_q AS (
            SELECT q.event_ticker, q.market_ticker, m.floor_strike,
                   q.ts_end, q.yes_bid_close, q.yes_ask_close,
                   0.5 * (q.yes_bid_close + q.yes_ask_close) AS prev_mid
            FROM kalshi_quotes q
            JOIN kalshi_markets m USING (market_ticker)
            WHERE m.is_hourly_kxbtcd AND m.is_cumulative
        ),
        next_q AS (
            SELECT q.event_ticker, q.market_ticker, m.floor_strike,
                   q.ts_end, q.yes_bid_close, q.yes_ask_close,
                   0.5 * (q.yes_bid_close + q.yes_ask_close) AS next_mid
            FROM kalshi_quotes q
            JOIN kalshi_markets m USING (market_ticker)
            WHERE m.is_hourly_kxbtcd AND m.is_cumulative
        )
        SELECT p.prev_event, p.next_event, p.boundary_time,
               prev_q.floor_strike,
               prev_q.market_ticker AS prev_market,
               next_q.market_ticker AS next_market,
               prev_q.prev_mid,
               next_q.next_mid,
               100.0 * (next_q.next_mid - prev_q.prev_mid) AS mid_change_cents,
               prev_q.yes_bid_close AS prev_yes_bid,
               prev_q.yes_ask_close AS prev_yes_ask,
               next_q.yes_bid_close AS next_yes_bid,
               next_q.yes_ask_close AS next_yes_ask
        FROM pairs p
        JOIN prev_q
          ON prev_q.event_ticker = p.prev_event
         AND prev_q.ts_end = p.boundary_time
        JOIN next_q
          ON next_q.event_ticker = p.next_event
         AND next_q.floor_strike = prev_q.floor_strike
         AND next_q.ts_end = p.boundary_time + (? * INTERVAL 1 MINUTE)
        ORDER BY p.boundary_time, prev_q.floor_strike
        """,
        [args.next_minute],
    ).fetchdf()
    con.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = args.output_dir / "cross_hour_same_strike_detail.csv"
    summary_path = args.output_dir / "cross_hour_same_strike_summary.csv"
    rows.to_csv(detail_path, index=False)

    if rows.empty:
        pd.DataFrame([{"pairs": 0}]).to_csv(summary_path, index=False)
        print(f"no matched cross-hour rows; wrote {summary_path}")
        return 0

    rows["abs_mid_change_cents"] = rows["mid_change_cents"].abs()
    rows["prev_mid_bucket"] = pd.cut(
        rows["prev_mid"],
        [0.0, 0.1, 0.25, 0.4, 0.6, 0.75, 0.9, 1.0],
        include_lowest=True,
    ).astype(str)
    summary = rows.groupby("prev_mid_bucket", observed=True).agg(
        pairs=("mid_change_cents", "size"),
        mean_change_cents=("mid_change_cents", "mean"),
        median_change_cents=("mid_change_cents", "median"),
        mean_abs_change_cents=("abs_mid_change_cents", "mean"),
        p90_abs_change_cents=("abs_mid_change_cents", lambda x: float(np.nanquantile(x, 0.90))),
    )
    summary.loc["ALL"] = {
        "pairs": len(rows),
        "mean_change_cents": rows["mid_change_cents"].mean(),
        "median_change_cents": rows["mid_change_cents"].median(),
        "mean_abs_change_cents": rows["abs_mid_change_cents"].mean(),
        "p90_abs_change_cents": rows["abs_mid_change_cents"].quantile(0.90),
    }
    summary.reset_index().rename(columns={"index": "prev_mid_bucket"}).to_csv(summary_path, index=False)
    print(f"wrote {detail_path} rows={len(rows)}")
    print(f"wrote {summary_path}")
    print(summary.tail(8).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
