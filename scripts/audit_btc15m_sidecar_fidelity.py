#!/usr/bin/env python3
"""Audit BTC15M materialized websocket replay data for fidelity.

This script is intentionally about data quality, not strategy selection.  It
checks the replay-critical properties of a materialized sidecar DuckDB:
timestamp validity, quote sanity, top-book coverage gaps, Coinbase ticker
source, and optional parity against an independently collected capture.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


DEFAULT_OUT_DIR = Path("backtest_outputs") / f"btc15m_sidecar_fidelity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", type=Path, required=True, help="Materialized DuckDB to audit.")
    parser.add_argument("--compare-db", type=Path, help="Optional independent capture DuckDB to compare.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--start-utc", help="Optional inclusive UTC lower bound for received_at_utc.")
    parser.add_argument("--end-utc", help="Optional exclusive UTC upper bound for received_at_utc.")
    parser.add_argument("--bucket-sec", type=int, default=5, help="Bucket size for optional parity comparison.")
    parser.add_argument("--gap-sec", type=float, default=120.0, help="Gap threshold for top-book coverage checks.")
    parser.add_argument("--price-tolerance-cents", type=float, default=1.0)
    return parser.parse_args()


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        con.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except duckdb.Error:
        return False


def create_audit_tables(con: duckdb.DuckDBPyConnection, start_utc: str | None, end_utc: str | None) -> dict[str, str]:
    tables: dict[str, str] = {}
    for table in ["ws_orderbook_top", "ws_lifecycle", "coinbase_ticker", "signal_scan", "order_decision", "capture_health"]:
        if not table_exists(con, table):
            tables[table] = table
            continue
        cols = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if "received_at_utc" not in cols or (not start_utc and not end_utc):
            tables[table] = table
            continue
        dest = f"__audit_{table}"
        clauses = []
        params: list[Any] = []
        if start_utc:
            clauses.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= TRY_CAST(? AS TIMESTAMPTZ)")
            params.append(start_utc)
        if end_utc:
            clauses.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) < TRY_CAST(? AS TIMESTAMPTZ)")
            params.append(end_utc)
        con.execute(f"CREATE TEMP TABLE {dest} AS SELECT * FROM {table} WHERE {' AND '.join(clauses)}", params)
        tables[table] = dest
    return tables


def manifest_for(db_path: Path) -> dict[str, Any]:
    path = db_path.with_suffix(db_path.suffix + ".manifest.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - diagnostic only
        return {"manifest_read_error": repr(exc)}


def write_df(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_empty_"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return "```\n" + df.to_string(index=False) + "\n```"


def scalar_row(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(names, row)) if row is not None else {}


def table_counts(con: duckdb.DuckDBPyConnection, tables: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table in ["ws_orderbook_top", "ws_lifecycle", "coinbase_ticker", "signal_scan", "order_decision", "capture_health"]:
        actual = tables.get(table, table)
        if not table_exists(con, actual):
            rows.append({"table": table, "exists": False, "rows": 0, "min_utc": None, "max_utc": None})
            continue
        cols = {str(row[1]) for row in con.execute(f"PRAGMA table_info({actual})").fetchall()}
        if "received_at_utc" in cols:
            info = scalar_row(
                con,
                f"""
                SELECT COUNT(*)::BIGINT AS rows,
                       MIN(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS min_utc,
                       MAX(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS max_utc,
                       SUM(CASE WHEN TRY_CAST(received_at_utc AS TIMESTAMPTZ) IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_utc
                FROM {actual}
                """,
            )
        else:
            info = scalar_row(con, f"SELECT COUNT(*)::BIGINT AS rows FROM {actual}")
            info.update({"min_utc": None, "max_utc": None, "null_utc": None})
        info.update({"table": table, "exists": True})
        rows.append(info)
    return pd.DataFrame(rows)[["table", "exists", "rows", "min_utc", "max_utc", "null_utc"]]


def quote_invariants(con: duckdb.DuckDBPyConnection, top_table: str) -> pd.DataFrame:
    if not table_exists(con, top_table):
        return pd.DataFrame([{"metric": "missing_ws_orderbook_top", "value": 1}])
    row = scalar_row(
        con,
        f"""
        SELECT COUNT(*)::BIGINT AS rows,
               SUM(CASE WHEN TRY_CAST(received_at_utc AS TIMESTAMPTZ) IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_received_at_utc,
               SUM(CASE WHEN received_at_ns IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_received_at_ns,
               SUM(CASE WHEN market_ticker IS NULL OR market_ticker = '' THEN 1 ELSE 0 END)::BIGINT AS null_market_ticker,
               SUM(CASE WHEN event_ticker IS NULL OR event_ticker = '' THEN 1 ELSE 0 END)::BIGINT AS null_event_ticker,
               SUM(CASE WHEN yes_bid IS NULL OR yes_ask IS NULL OR no_bid IS NULL OR no_ask IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_book_fields,
               SUM(CASE WHEN yes_bid < 0 OR yes_bid > 1 OR yes_ask < 0 OR yes_ask > 1 OR no_bid < 0 OR no_bid > 1 OR no_ask < 0 OR no_ask > 1 THEN 1 ELSE 0 END)::BIGINT AS price_out_of_range,
               SUM(CASE WHEN yes_bid > yes_ask THEN 1 ELSE 0 END)::BIGINT AS crossed_yes_book,
               SUM(CASE WHEN no_bid > no_ask THEN 1 ELSE 0 END)::BIGINT AS crossed_no_book,
               SUM(CASE WHEN yes_bid_qty < 0 OR yes_ask_qty < 0 OR no_bid_qty < 0 OR no_ask_qty < 0 THEN 1 ELSE 0 END)::BIGINT AS negative_qty,
               SUM(CASE WHEN btc_spot IS NOT NULL AND (btc_spot < 1000 OR btc_spot > 1000000) THEN 1 ELSE 0 END)::BIGINT AS implausible_btc_spot,
               SUM(CASE WHEN TRY_CAST(received_at_utc AS TIMESTAMPTZ) IS NOT NULL
                         AND received_at_ns IS NOT NULL
                         AND market_ticker IS NOT NULL AND market_ticker != ''
                         AND event_ticker IS NOT NULL AND event_ticker != ''
                         AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL AND no_bid IS NOT NULL AND no_ask IS NOT NULL
                         AND yes_bid BETWEEN 0 AND 1 AND yes_ask BETWEEN 0 AND 1
                         AND no_bid BETWEEN 0 AND 1 AND no_ask BETWEEN 0 AND 1
                         AND yes_bid <= yes_ask AND no_bid <= no_ask
                         AND COALESCE(yes_bid_qty, 0) >= 0
                         AND COALESCE(yes_ask_qty, 0) >= 0
                         AND COALESCE(no_bid_qty, 0) >= 0
                         AND COALESCE(no_ask_qty, 0) >= 0
                         AND (btc_spot IS NULL OR btc_spot BETWEEN 1000 AND 1000000)
                        THEN 1 ELSE 0 END)::BIGINT AS valid_book_rows,
               COUNT(DISTINCT event_ticker)::BIGINT AS distinct_events,
               COUNT(DISTINCT market_ticker)::BIGINT AS distinct_markets
        FROM {top_table}
        """,
    )
    rows = int(row.get("rows") or 0)
    valid = int(row.get("valid_book_rows") or 0)
    row["filterable_bad_rows"] = rows - valid
    row["valid_book_rate"] = (valid / rows) if rows else 0.0
    return pd.DataFrame([{"metric": key, "value": value} for key, value in row.items()])


def gap_summary(con: duckdb.DuckDBPyConnection, top_table: str, gap_sec: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not table_exists(con, top_table):
        empty = pd.DataFrame()
        return empty, empty
    summary = con.execute(
        f"""
        WITH ordered AS (
            SELECT market_ticker,
                   event_ticker,
                   received_at_ns,
                   (received_at_ns - LAG(received_at_ns) OVER (
                       PARTITION BY market_ticker ORDER BY received_at_ns
                   )) / 1000000000.0 AS gap_sec
            FROM {top_table}
            WHERE received_at_ns IS NOT NULL
        )
        SELECT COUNT(DISTINCT market_ticker)::BIGINT AS markets,
               COUNT(*)::BIGINT AS rows_with_clock,
               SUM(CASE WHEN gap_sec > ? THEN 1 ELSE 0 END)::BIGINT AS gaps_over_threshold,
               MAX(gap_sec) AS max_gap_sec,
               AVG(gap_sec) AS avg_gap_sec,
               quantile_cont(gap_sec, 0.99) AS p99_gap_sec
        FROM ordered
        """,
        [gap_sec],
    ).fetchdf()
    worst = con.execute(
        f"""
        WITH ordered AS (
            SELECT market_ticker,
                   event_ticker,
                   received_at_ns,
                   TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS received_at_utc,
                   (received_at_ns - LAG(received_at_ns) OVER (
                       PARTITION BY market_ticker ORDER BY received_at_ns
                   )) / 1000000000.0 AS gap_sec
            FROM {top_table}
            WHERE received_at_ns IS NOT NULL
        )
        SELECT market_ticker, event_ticker, received_at_utc, gap_sec
        FROM ordered
        WHERE gap_sec IS NOT NULL
        ORDER BY gap_sec DESC
        LIMIT 25
        """
    ).fetchdf()
    return summary, worst


def lifecycle_counts(con: duckdb.DuckDBPyConnection, lifecycle_table: str) -> pd.DataFrame:
    if not table_exists(con, lifecycle_table):
        return pd.DataFrame()
    cols = {str(row[1]) for row in con.execute(f"PRAGMA table_info({lifecycle_table})").fetchall()}
    candidates = [col for col in ["event_type", "message_type", "reason"] if col in cols]
    if candidates:
        lifecycle_expr = "COALESCE(" + ", ".join(candidates + ["'unknown'"]) + ")"
    else:
        lifecycle_expr = "'unknown'"
    return con.execute(
        f"""
        SELECT {lifecycle_expr} AS lifecycle_type,
               COUNT(*)::BIGINT AS rows,
               MIN(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS min_utc,
               MAX(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) AS max_utc
        FROM {lifecycle_table}
        GROUP BY 1
        ORDER BY rows DESC, lifecycle_type
        """
    ).fetchdf()


def coinbase_summary(con: duckdb.DuckDBPyConnection, manifest: dict[str, Any], coinbase_table: str) -> pd.DataFrame:
    if not table_exists(con, coinbase_table):
        return pd.DataFrame(
            [
                {
                    "metric": "coinbase_ticker_exists",
                    "value": False,
                    "note": "missing coinbase_ticker table; BTC tick-age replay is not available",
                }
            ]
        )
    cols = {str(row[1]) for row in con.execute(f"PRAGMA table_info({coinbase_table})").fetchall()}
    raw_schema_fields_present = {"product_id", "exchange_time"}.issubset(cols)
    row = scalar_row(
        con,
        f"""
        WITH ordered AS (
            SELECT received_at_ns,
                   TRY_CAST(received_at_utc AS TIMESTAMPTZ) AS ts,
                   price,
                   (received_at_ns - LAG(received_at_ns) OVER (ORDER BY received_at_ns)) / 1000000000.0 AS gap_sec
            FROM {coinbase_table}
            WHERE received_at_ns IS NOT NULL
        )
        SELECT COUNT(*)::BIGINT AS rows,
               SUM(CASE WHEN ts IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_received_at_utc,
               SUM(CASE WHEN price IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_price,
               MIN(ts) AS min_utc,
               MAX(ts) AS max_utc,
               MIN(price) AS min_price,
               MAX(price) AS max_price,
               MAX(gap_sec) AS max_gap_sec,
               quantile_cont(gap_sec, 0.99) AS p99_gap_sec
        FROM ordered
        """,
    )
    manifest_source = str(manifest.get("coinbase_ticker_source") or "")
    if manifest_source:
        source = manifest_source
    elif raw_schema_fields_present:
        source = "raw_sidecar_schema"
    else:
        source = "synthetic_or_unknown_schema"
    row["coinbase_ticker_source"] = source
    row["raw_coinbase_schema_fields_present"] = raw_schema_fields_present
    row["synthetic_coinbase_from_top"] = bool(manifest.get("synthetic_coinbase_from_top", False)) or source.startswith(
        "synthetic"
    )
    return pd.DataFrame([{"metric": key, "value": value, "note": ""} for key, value in row.items()])


def bucketed_top(con: duckdb.DuckDBPyConnection, top_table: str, bucket_sec: int) -> pd.DataFrame:
    if not table_exists(con, top_table):
        return pd.DataFrame()
    return con.execute(
        f"""
        WITH base AS (
            SELECT market_ticker,
                   event_ticker,
                   FLOOR(epoch(TRY_CAST(received_at_utc AS TIMESTAMPTZ)) / ?)::BIGINT * ? AS bucket_epoch,
                   received_at_ns,
                   (yes_bid + yes_ask) / 2.0 AS yes_mid,
                   (no_bid + no_ask) / 2.0 AS no_mid,
                   (yes_ask - yes_bid) * 100.0 AS spread_cents,
                   btc_spot
            FROM {top_table}
            WHERE TRY_CAST(received_at_utc AS TIMESTAMPTZ) IS NOT NULL
              AND market_ticker IS NOT NULL
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY market_ticker, bucket_epoch
                       ORDER BY received_at_ns DESC
                   ) AS rn
            FROM base
        )
        SELECT market_ticker, event_ticker, bucket_epoch, received_at_ns,
               yes_mid, no_mid, spread_cents, btc_spot
        FROM ranked
        WHERE rn = 1
        """,
        [bucket_sec, bucket_sec],
    ).fetchdf()


def parity_summary(
    main_con: duckdb.DuckDBPyConnection,
    compare_con: duckdb.DuckDBPyConnection,
    main_tables: dict[str, str],
    compare_tables: dict[str, str],
    bucket_sec: int,
    tolerance_cents: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    left = bucketed_top(main_con, main_tables.get("ws_orderbook_top", "ws_orderbook_top"), bucket_sec)
    right = bucketed_top(compare_con, compare_tables.get("ws_orderbook_top", "ws_orderbook_top"), bucket_sec)
    if left.empty or right.empty:
        return pd.DataFrame([{"metric": "parity_available", "value": False}]), pd.DataFrame()
    merged = left.merge(
        right,
        on=["market_ticker", "bucket_epoch"],
        how="inner",
        suffixes=("_main", "_compare"),
    )
    if not merged.empty:
        merged["yes_mid_abs_diff_cents"] = (merged["yes_mid_main"] - merged["yes_mid_compare"]).abs() * 100.0
        merged["spread_abs_diff_cents"] = (merged["spread_cents_main"] - merged["spread_cents_compare"]).abs()
        merged["btc_abs_diff"] = (merged["btc_spot_main"] - merged["btc_spot_compare"]).abs()
    rows = [
        {"metric": "parity_available", "value": True},
        {"metric": "bucket_sec", "value": bucket_sec},
        {"metric": "main_buckets", "value": int(len(left))},
        {"metric": "compare_buckets", "value": int(len(right))},
        {"metric": "matched_buckets", "value": int(len(merged))},
        {"metric": "main_match_rate", "value": float(len(merged) / len(left)) if len(left) else 0.0},
        {"metric": "compare_match_rate", "value": float(len(merged) / len(right)) if len(right) else 0.0},
    ]
    if not merged.empty:
        diff = merged["yes_mid_abs_diff_cents"].dropna()
        rows.extend(
            [
                {"metric": "yes_mid_abs_diff_cents_mean", "value": float(diff.mean()) if not diff.empty else None},
                {"metric": "yes_mid_abs_diff_cents_p95", "value": float(diff.quantile(0.95)) if not diff.empty else None},
                {"metric": "yes_mid_abs_diff_cents_max", "value": float(diff.max()) if not diff.empty else None},
                {
                    "metric": "yes_mid_within_tolerance_rate",
                    "value": float((diff <= tolerance_cents).mean()) if not diff.empty else None,
                },
            ]
        )
    worst = (
        merged.sort_values("yes_mid_abs_diff_cents", ascending=False)
        .head(25)
        if not merged.empty and "yes_mid_abs_diff_cents" in merged.columns
        else pd.DataFrame()
    )
    return pd.DataFrame(rows), worst


def verdict(
    quote_df: pd.DataFrame,
    gap_df: pd.DataFrame,
    coinbase_df: pd.DataFrame,
    manifest: dict[str, Any],
    compare_df: pd.DataFrame,
) -> pd.DataFrame:
    q = dict(zip(quote_df["metric"].astype(str), quote_df["value"])) if not quote_df.empty else {}
    gaps = gap_df.iloc[0].to_dict() if not gap_df.empty else {}
    cb = dict(zip(coinbase_df["metric"].astype(str), coinbase_df["value"])) if not coinbase_df.empty else {}
    compare = dict(zip(compare_df["metric"].astype(str), compare_df["value"])) if not compare_df.empty else {}
    hard_failures = []
    filterable_failures = []
    for metric in [
        "null_received_at_utc",
        "null_received_at_ns",
        "null_market_ticker",
        "null_event_ticker",
    ]:
        if float(q.get(metric) or 0) > 0:
            hard_failures.append(metric)
    for metric in [
        "null_book_fields",
        "price_out_of_range",
        "crossed_yes_book",
        "crossed_no_book",
        "negative_qty",
        "implausible_btc_spot",
    ]:
        if float(q.get(metric) or 0) > 0:
            filterable_failures.append(metric)
    if float(gaps.get("gaps_over_threshold") or 0) > 0:
        hard_failures.append("top_book_gaps_over_threshold")
    notes = []
    synthetic_coinbase = bool(manifest.get("synthetic_coinbase_from_top", False)) or table_bool(
        cb.get("synthetic_coinbase_from_top", False)
    )
    raw_coinbase_schema = table_bool(cb.get("raw_coinbase_schema_fields_present", False))
    coinbase_source = str(cb.get("coinbase_ticker_source") or "")
    promotion_grade_coinbase = (
        table_bool(cb.get("coinbase_ticker_exists", True))
        and not synthetic_coinbase
        and (coinbase_source in {"raw_sidecar", "raw_sidecar_schema"} or raw_coinbase_schema)
    )
    if synthetic_coinbase:
        notes.append("coinbase_ticker is synthetic from top-book btc_spot; not promotion-grade BTC tick-age evidence")
    elif not promotion_grade_coinbase:
        notes.append("coinbase_ticker source is not proven raw; not promotion-grade BTC tick-age evidence")
    if compare and compare.get("parity_available") is True:
        if float(compare.get("main_match_rate") or 0.0) < 0.8:
            notes.append("independent capture bucket match rate below 80%")
    valid_book_rows = int(float(q.get("valid_book_rows") or 0))
    filtered_top_book_research_grade = len(hard_failures) == 0 and valid_book_rows > 0
    raw_top_book_clean = (
        len(hard_failures) == 0
        and len(filterable_failures) == 0
        and int(q.get("rows") or 0) > 0
    )
    return pd.DataFrame(
        [
            {
                "top_book_research_grade": raw_top_book_clean,
                "filtered_top_book_research_grade": filtered_top_book_research_grade,
                "promotion_grade_coinbase_ticks": promotion_grade_coinbase,
                "hard_failures": ";".join(hard_failures),
                "filterable_failures": ";".join(filterable_failures),
                "valid_book_rows": valid_book_rows,
                "valid_book_rate": float(q.get("valid_book_rate") or 0.0),
                "notes": "; ".join(notes),
            }
        ]
    )


def table_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return bool(value)


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_for(args.capture_db)
    con = connect(args.capture_db)
    try:
        audit_tables = create_audit_tables(con, args.start_utc, args.end_utc)
        counts = table_counts(con, audit_tables)
        quote = quote_invariants(con, audit_tables.get("ws_orderbook_top", "ws_orderbook_top"))
        gaps, worst_gaps = gap_summary(con, audit_tables.get("ws_orderbook_top", "ws_orderbook_top"), args.gap_sec)
        lifecycle = lifecycle_counts(con, audit_tables.get("ws_lifecycle", "ws_lifecycle"))
        coinbase = coinbase_summary(con, manifest, audit_tables.get("coinbase_ticker", "coinbase_ticker"))
        parity = pd.DataFrame()
        parity_worst = pd.DataFrame()
        compare_manifest: dict[str, Any] = {}
        if args.compare_db:
            compare_manifest = manifest_for(args.compare_db)
            compare_con = connect(args.compare_db)
            try:
                compare_tables = create_audit_tables(compare_con, args.start_utc, args.end_utc)
                parity, parity_worst = parity_summary(
                    con,
                    compare_con,
                    audit_tables,
                    compare_tables,
                    args.bucket_sec,
                    args.price_tolerance_cents,
                )
            finally:
                compare_con.close()
        verdict_df = verdict(quote, gaps, coinbase, manifest, parity)
    finally:
        con.close()

    write_df(counts, args.out_dir / "table_counts.csv")
    write_df(quote, args.out_dir / "quote_invariants.csv")
    write_df(gaps, args.out_dir / "gap_summary.csv")
    write_df(worst_gaps, args.out_dir / "worst_gaps.csv")
    write_df(lifecycle, args.out_dir / "lifecycle_counts.csv")
    write_df(coinbase, args.out_dir / "coinbase_summary.csv")
    write_df(verdict_df, args.out_dir / "fidelity_verdict.csv")
    if not parity.empty:
        write_df(parity, args.out_dir / "parity_summary.csv")
    if not parity_worst.empty:
        write_df(parity_worst, args.out_dir / "parity_worst_buckets.csv")

    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_db": str(args.capture_db),
        "compare_db": str(args.compare_db) if args.compare_db else "",
        "start_utc": args.start_utc or "",
        "end_utc": args.end_utc or "",
        "bucket_sec": args.bucket_sec,
        "gap_sec": args.gap_sec,
        "price_tolerance_cents": args.price_tolerance_cents,
        "manifest": manifest,
        "compare_manifest": compare_manifest,
    }
    (args.out_dir / "audit_manifest.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")

    report = [
        "# BTC15M Sidecar Fidelity Audit",
        "",
        f"Generated: `{info['created_at_utc']}`",
        f"Capture DB: `{args.capture_db}`",
        f"Compare DB: `{args.compare_db or ''}`",
        f"Window: `{args.start_utc or ''}` -> `{args.end_utc or ''}`",
        "",
        "## Verdict",
        "",
        df_to_markdown(verdict_df),
        "",
        "## Table Counts",
        "",
        df_to_markdown(counts),
        "",
        "## Quote Invariants",
        "",
        df_to_markdown(quote),
        "",
        "## Gap Summary",
        "",
        df_to_markdown(gaps),
        "",
        "## Coinbase Summary",
        "",
        df_to_markdown(coinbase),
    ]
    if not parity.empty:
        report.extend(["", "## Independent Capture Parity", "", df_to_markdown(parity)])
    (args.out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return {
        "out_dir": str(args.out_dir),
        "verdict": verdict_df.to_dict(orient="records"),
        "table_counts": counts.to_dict(orient="records"),
    }


def main() -> int:
    args = parse_args()
    result = run(args)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
