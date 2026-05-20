#!/usr/bin/env python3
"""Audit BTC15M official Kalshi settlement vs local BTC-close proxy.

This is a data-quality audit, not a strategy backtest.  It uses captured
Kalshi lifecycle `determined` messages plus captured BTC ticks to test whether
the replay proxy result agrees with official Kalshi results for all settled
BTC15M markets available in the local websocket capture.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_btc15m_f2_live_ws_holdout import (  # noqa: E402
    DEFAULT_CAPTURE_DB,
    add_proxy_results,
    metadata_from_lifecycle,
)


DEFAULT_OUT = PROJECT_ROOT / "backtest_outputs" / f"btc15m_official_proxy_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BTC15M official settlements against BTC close proxy.")
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE_DB)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--btc-model", choices=["spot", "rolling60"], default="spot")
    parser.add_argument("--near-strike-usd", type=float, default=10.0)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def load_capture_tables(db_path: Path, start: str | None, end: str | None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    con = duckdb.connect(str(db_path), read_only=True)
    where = ["event_ticker LIKE 'KXBTC15M-%'"]
    params: list[object] = []
    if start:
        where.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= ?")
        params.append(pd.Timestamp(start, tz="UTC").to_pydatetime())
    if end:
        where.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) <= ?")
        params.append(pd.Timestamp(end, tz="UTC").to_pydatetime())
    where_sql = " AND ".join(where)
    lifecycle = con.execute(
        f"""
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               message_type, event_type, event_ticker, market_ticker, payload_json
        FROM ws_lifecycle
        WHERE {where_sql}
        ORDER BY received_at_ns
        """,
        params,
    ).fetchdf()
    btc = con.execute(
        """
        SELECT received_at_ns,
               TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
               price, best_bid, best_ask, sequence
        FROM coinbase_ticker
        ORDER BY received_at_ns
        """
    ).fetchdf()
    info = con.execute(
        """
        SELECT
          min(TRY_CAST(received_at_utc AS TIMESTAMPTZ)),
          max(TRY_CAST(received_at_utc AS TIMESTAMPTZ)),
          count(*)
        FROM ws_lifecycle
        WHERE event_ticker LIKE 'KXBTC15M-%'
        """
    ).fetchone()
    con.close()
    for df in (lifecycle, btc):
        if "received_at_utc" in df:
            df["received_at_utc"] = pd.to_datetime(df["received_at_utc"], utc=True, errors="coerce")
    return lifecycle, btc, {
        "capture_db": str(db_path),
        "lifecycle_min": str(info[0]),
        "lifecycle_max": str(info[1]),
        "lifecycle_rows": int(info[2] or 0),
    }


def summarize(df: pd.DataFrame, near_strike_usd: float) -> dict:
    both = df[
        df["official_result"].astype(str).str.lower().isin(["yes", "no"])
        & df["proxy_result"].astype(str).str.lower().isin(["yes", "no"])
    ].copy()
    both["match"] = both["official_result"].astype(str).str.lower().eq(both["proxy_result"].astype(str).str.lower())
    near = both[pd.to_numeric(both["proxy_distance_usd"], errors="coerce").abs().le(near_strike_usd)]
    far = both[pd.to_numeric(both["proxy_distance_usd"], errors="coerce").abs().gt(near_strike_usd)]
    return {
        "official_markets": int(df["official_result"].astype(str).str.lower().isin(["yes", "no"]).sum()),
        "proxy_computable_markets": int(both["proxy_result"].astype(str).str.lower().isin(["yes", "no"]).sum()),
        "matches": int(both["match"].sum()),
        "mismatches": int((~both["match"]).sum()),
        "match_rate": float(both["match"].mean()) if len(both) else 0.0,
        "near_strike_usd": near_strike_usd,
        "near_strike_markets": int(len(near)),
        "near_strike_matches": int(near["match"].sum()) if len(near) else 0,
        "far_from_strike_markets": int(len(far)),
        "far_from_strike_matches": int(far["match"].sum()) if len(far) else 0,
        "far_from_strike_match_rate": float(far["match"].mean()) if len(far) else 0.0,
        "min_abs_proxy_distance_usd": float(pd.to_numeric(both["proxy_distance_usd"], errors="coerce").abs().min()) if len(both) else None,
        "median_abs_proxy_distance_usd": float(pd.to_numeric(both["proxy_distance_usd"], errors="coerce").abs().median()) if len(both) else None,
    }


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    lifecycle, btc, info = load_capture_tables(args.capture_db, args.start, args.end)
    meta, official = metadata_from_lifecycle(lifecycle)
    if meta.empty or official.empty:
        raise RuntimeError("missing BTC15M metadata or official results in capture")
    q = meta.merge(official[["event_ticker", "market_ticker", "official_result", "determined_at_utc"]], on=["event_ticker", "market_ticker"], how="inner")
    q = q[["event_ticker", "market_ticker", "floor_strike", "open_time", "close_time", "official_result", "determined_at_utc"]].drop_duplicates()
    q = add_proxy_results(q, btc, args.btc_model)
    q["official_proxy_match"] = q["official_result"].astype(str).str.lower().eq(q["proxy_result"].astype(str).str.lower())
    q["abs_proxy_distance_usd"] = pd.to_numeric(q["proxy_distance_usd"], errors="coerce").abs()
    summary = summarize(q, args.near_strike_usd)
    summary.update(
        {
            **info,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "btc_model": args.btc_model,
            "start_arg": args.start or "",
            "end_arg": args.end or "",
            "metadata_markets": int(len(meta)),
            "official_rows": int(len(official)),
        }
    )
    q.sort_values(["close_time", "market_ticker"]).to_csv(args.out_dir / "official_proxy_markets.csv", index=False)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = [
        "# BTC15M Official vs Proxy Settlement Audit",
        "",
        f"Created UTC: `{summary['created_at_utc']}`",
        f"Capture DB: `{summary['capture_db']}`",
        "",
        "## Summary",
        "",
        f"- Official markets: `{summary['official_markets']}`",
        f"- Proxy-computable markets: `{summary['proxy_computable_markets']}`",
        f"- Matches: `{summary['matches']}`",
        f"- Mismatches: `{summary['mismatches']}`",
        f"- Match rate: `{summary['match_rate']:.4f}`",
        f"- Near-strike markets within ${args.near_strike_usd:g}: `{summary['near_strike_markets']}`",
        f"- Far-from-strike match rate: `{summary['far_from_strike_match_rate']:.4f}`",
        "",
        "## Interpretation",
        "",
        "This audit tests settlement proxy quality only. It does not prove any strategy edge or fillability.",
    ]
    mismatches = q[~q["official_proxy_match"]]
    if not mismatches.empty:
        report.extend(["", "## Mismatches", "", mismatches[["event_ticker", "market_ticker", "close_time", "floor_strike", "close_btc_spot", "proxy_distance_usd", "official_result", "proxy_result"]].to_string(index=False)])
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
