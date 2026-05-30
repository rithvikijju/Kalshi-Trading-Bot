#!/usr/bin/env python3
"""Materialize a BTC capture replay sidecar into a DuckDB replay snapshot.

Live BTC collectors keep their DuckDB files locked on Windows.  The runners also
append a newline-delimited JSON sidecar containing the replay-critical capture
rows.  This helper converts that sidecar into the table names expected by the
offline replay scripts without touching the running process.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--out-db", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--start-utc",
        help="Inclusive UTC lower bound on received_at_utc, e.g. 2026-05-29T00:00:00Z.",
    )
    parser.add_argument(
        "--end-utc",
        help="Exclusive UTC upper bound on received_at_utc, e.g. 2026-05-30T00:00:00Z.",
    )
    parser.add_argument(
        "--table",
        dest="tables",
        action="append",
        choices=["ws_orderbook_top", "ws_lifecycle", "signal_scan", "order_decision", "coinbase_ticker"],
        help="Sidecar table to materialize. May be repeated. Defaults to all replay tables.",
    )
    parser.add_argument(
        "--event-prefix",
        dest="event_prefixes",
        action="append",
        help="Keep rows whose event_ticker, market_ticker, or selected_market starts with this prefix. May be repeated.",
    )
    parser.add_argument(
        "--market-prefix",
        dest="market_prefixes",
        action="append",
        help="Keep rows whose market_ticker or selected_market starts with this prefix. May be repeated.",
    )
    parser.add_argument(
        "--synthetic-coinbase-from-top",
        action="store_true",
        help=(
            "Build coinbase_ticker from ws_orderbook_top.btc_spot. This is useful "
            "for old sidecars that did not include raw Coinbase ticks, but it is "
            "not equivalent to a raw Coinbase-tick replay."
        ),
    )
    return parser.parse_args()


def q(path: Path) -> str:
    return str(path).replace("'", "''")


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        con.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except duckdb.Error:
        return False


NUMERIC_CASTS = {
    "received_at_ns": "BIGINT",
    "sid": "BIGINT",
    "seq": "BIGINT",
    "yes_bid": "DOUBLE",
    "yes_bid_qty": "DOUBLE",
    "yes_ask": "DOUBLE",
    "yes_ask_qty": "DOUBLE",
    "no_bid": "DOUBLE",
    "no_bid_qty": "DOUBLE",
    "no_ask": "DOUBLE",
    "no_ask_qty": "DOUBLE",
    "btc_spot": "DOUBLE",
    "changed_markets": "BIGINT",
    "evaluated_markets": "BIGINT",
    "candidate_count": "BIGINT",
    "entry_price": "DOUBLE",
    "yes_limit_price": "DOUBLE",
    "net_edge_cents": "DOUBLE",
    "model_p_yes": "DOUBLE",
    "edge_threshold_cents": "DOUBLE",
    "spread_cents": "DOUBLE",
    "top_visible_qty": "DOUBLE",
    "quote_received_at_ns": "BIGINT",
    "quote_age_ms": "DOUBLE",
    "ttl_min": "DOUBLE",
    "btc_candle_age_sec": "DOUBLE",
    "btc_rv60": "DOUBLE",
    "btc_ret_10m_usd": "DOUBLE",
    "latency_ms": "DOUBLE",
    "blocked_events": "BIGINT",
    "contracts": "DOUBLE",
    "estimated_cost": "DOUBLE",
    "portfolio_available": "DOUBLE",
    "portfolio_value": "DOUBLE",
    "price": "DOUBLE",
    "best_bid": "DOUBLE",
    "best_ask": "DOUBLE",
    "sequence": "BIGINT",
}


SIDECAR_COLUMNS = [
    "table",
    "received_at_ns",
    "received_at_utc",
    "market_ticker",
    "event_ticker",
    "sid",
    "seq",
    "yes_bid",
    "yes_bid_qty",
    "yes_ask",
    "yes_ask_qty",
    "no_bid",
    "no_bid_qty",
    "no_ask",
    "no_ask_qty",
    "btc_spot",
    "source",
    "product_id",
    "price",
    "best_bid",
    "best_ask",
    "sequence",
    "exchange_time",
    "message_type",
    "event_type",
    "open_ts",
    "close_ts",
    "payload_json",
    "reason",
    "mode",
    "signal_strategy",
    "model_ttl_policy",
    "model_policy_version",
    "changed_markets",
    "evaluated_markets",
    "candidate_count",
    "selected_market",
    "selected_side",
    "side",
    "entry_price",
    "yes_limit_price",
    "net_edge_cents",
    "model_p_yes",
    "edge_threshold_cents",
    "spread_cents",
    "top_visible_qty",
    "quote_received_at_ns",
    "quote_age_ms",
    "ttl_min",
    "close_time",
    "btc_candle_time",
    "btc_candle_age_sec",
    "btc_rv60",
    "btc_ret_10m_usd",
    "latency_ms",
    "blocked_events",
    "action",
    "contracts",
    "estimated_cost",
    "portfolio_available",
    "portfolio_value",
    "client_order_id",
    "detail",
]


DEFAULT_MATERIALIZED_TABLES = [
    "ws_orderbook_top",
    "ws_lifecycle",
    "signal_scan",
    "order_decision",
    "coinbase_ticker",
]


def json_columns_sql() -> str:
    return "{" + ", ".join(f"'{col}':'VARCHAR'" for col in SIDECAR_COLUMNS) + "}"


def create_if_present(con: duckdb.DuckDBPyConnection, table: str, columns: list[str]) -> int:
    present = {
        str(row[1])
        for row in con.execute("PRAGMA table_info(__raw_sidecar)").fetchall()
    }
    select_parts = []
    for col in columns:
        cast_type = NUMERIC_CASTS.get(col)
        if col in present:
            if cast_type:
                select_parts.append(f"TRY_CAST({col} AS {cast_type}) AS {col}")
            else:
                select_parts.append(col)
        else:
            select_parts.append(f"NULL::{cast_type or 'VARCHAR'} AS {col}")
    con.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT {", ".join(select_parts)}
        FROM __raw_sidecar
        WHERE "table" = ?
        """,
        [table],
    )
    return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def selected_tables(args: argparse.Namespace) -> list[str]:
    if not args.tables:
        return list(DEFAULT_MATERIALIZED_TABLES)
    seen: set[str] = set()
    ordered = []
    for table in args.tables:
        if table not in seen:
            seen.add(table)
            ordered.append(table)
    return ordered


def raw_table_filter_tables(tables: list[str], synthetic_coinbase_from_top: bool) -> list[str]:
    needed = list(tables)
    if synthetic_coinbase_from_top and "coinbase_ticker" in tables and "ws_orderbook_top" not in needed:
        needed.append("ws_orderbook_top")
    return needed


def build_raw_where(args: argparse.Namespace, tables: list[str]) -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    if tables:
        clauses.append('"table" IN (' + ", ".join("?" for _ in tables) + ")")
        params.extend(tables)
    if args.start_utc:
        clauses.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) >= TRY_CAST(? AS TIMESTAMPTZ)")
        params.append(args.start_utc)
    if args.end_utc:
        clauses.append("TRY_CAST(received_at_utc AS TIMESTAMPTZ) < TRY_CAST(? AS TIMESTAMPTZ)")
        params.append(args.end_utc)
    if args.event_prefixes:
        prefix_clauses = []
        for prefix in args.event_prefixes:
            like = f"{prefix}%"
            prefix_clauses.append("(event_ticker LIKE ? OR market_ticker LIKE ? OR selected_market LIKE ?)")
            params.extend([like, like, like])
        clauses.append("(" + " OR ".join(prefix_clauses) + ")")
    if args.market_prefixes:
        prefix_clauses = []
        for prefix in args.market_prefixes:
            like = f"{prefix}%"
            prefix_clauses.append("(market_ticker LIKE ? OR selected_market LIKE ?)")
            params.extend([like, like])
        clauses.append("(" + " OR ".join(prefix_clauses) + ")")
    if not clauses:
        return "", params
    return "WHERE " + "\n          AND ".join(clauses), params


def main() -> int:
    args = parse_args()
    tables = selected_tables(args)
    raw_tables = raw_table_filter_tables(tables, args.synthetic_coinbase_from_top)
    if not args.sidecar.exists():
        raise FileNotFoundError(args.sidecar)
    if args.out_db.exists():
        if not args.overwrite:
            raise FileExistsError(f"{args.out_db} exists; pass --overwrite")
        args.out_db.unlink()
    wal = Path(str(args.out_db) + ".wal")
    if wal.exists() and args.overwrite:
        wal.unlink()
    args.out_db.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(args.out_db))
    con.execute("PRAGMA threads=8")
    where_sql, where_params = build_raw_where(args, raw_tables)
    con.execute(
        f"""
        CREATE TABLE __raw_sidecar AS
        SELECT *
        FROM read_json(
            '{q(args.sidecar)}',
            format='newline_delimited',
            columns={json_columns_sql()},
            union_by_name=true,
            ignore_errors=true,
            maximum_object_size=16777216
        )
        {where_sql}
        """,
        where_params,
    )

    counts: dict[str, int] = {}
    if "ws_orderbook_top" in tables:
        counts["ws_orderbook_top"] = create_if_present(
            con,
            "ws_orderbook_top",
            [
                "received_at_ns",
                "received_at_utc",
                "market_ticker",
                "event_ticker",
                "sid",
                "seq",
                "yes_bid",
                "yes_bid_qty",
                "yes_ask",
                "yes_ask_qty",
                "no_bid",
                "no_bid_qty",
                "no_ask",
                "no_ask_qty",
                "btc_spot",
                "source",
            ],
        )
    if "ws_lifecycle" in tables:
        counts["ws_lifecycle"] = create_if_present(
            con,
            "ws_lifecycle",
            [
                "received_at_ns",
                "received_at_utc",
                "message_type",
                "event_type",
                "event_ticker",
                "market_ticker",
                "open_ts",
                "close_ts",
                "payload_json",
            ],
        )
    if "signal_scan" in tables:
        counts["signal_scan"] = create_if_present(
            con,
            "signal_scan",
            [
                "received_at_ns",
                "received_at_utc",
                "reason",
                "mode",
                "event_ticker",
                "changed_markets",
                "evaluated_markets",
                "candidate_count",
                "selected_market",
                "selected_side",
                "signal_strategy",
                "model_ttl_policy",
                "model_policy_version",
                "entry_price",
                "net_edge_cents",
                "model_p_yes",
                "edge_threshold_cents",
                "spread_cents",
                "top_visible_qty",
                "quote_received_at_ns",
                "quote_age_ms",
                "ttl_min",
                "close_time",
                "btc_spot",
                "btc_candle_time",
                "btc_candle_age_sec",
                "btc_rv60",
                "btc_ret_10m_usd",
                "latency_ms",
                "blocked_events",
                "action",
                "detail",
            ],
        )
    if "order_decision" in tables:
        counts["order_decision"] = create_if_present(
            con,
            "order_decision",
            [
                "received_at_ns",
                "received_at_utc",
                "mode",
                "action",
                "signal_strategy",
                "model_ttl_policy",
                "model_policy_version",
                "event_ticker",
                "market_ticker",
                "side",
                "contracts",
                "entry_price",
                "yes_limit_price",
                "net_edge_cents",
                "btc_spot",
                "estimated_cost",
                "portfolio_available",
                "portfolio_value",
                "client_order_id",
                "detail",
            ],
        )
    if "coinbase_ticker" in tables and not args.synthetic_coinbase_from_top:
        counts["coinbase_ticker"] = create_if_present(
            con,
            "coinbase_ticker",
            [
                "received_at_ns",
                "received_at_utc",
                "product_id",
                "price",
                "best_bid",
                "best_ask",
                "sequence",
                "exchange_time",
            ],
        )

    coinbase_ticker_source = "raw_sidecar" if "coinbase_ticker" in counts else ""
    if args.synthetic_coinbase_from_top and "coinbase_ticker" in tables:
        con.execute(
            """
            CREATE TABLE coinbase_ticker AS
            SELECT received_at_ns,
                   received_at_utc,
                   btc_spot AS price,
                   NULL::DOUBLE AS best_bid,
                   NULL::DOUBLE AS best_ask,
                   NULL::BIGINT AS sequence
            FROM ws_orderbook_top
            WHERE btc_spot IS NOT NULL
            QUALIFY row_number() OVER (
                PARTITION BY received_at_ns, received_at_utc, btc_spot
                ORDER BY market_ticker
            ) = 1
            """
        )
        counts["coinbase_ticker"] = int(con.execute("SELECT count(*) FROM coinbase_ticker").fetchone()[0])
        coinbase_ticker_source = "synthetic_from_ws_orderbook_top_btc_spot"

    raw_rows = int(con.execute("SELECT count(*) FROM __raw_sidecar").fetchone()[0])
    con.execute("DROP TABLE __raw_sidecar")
    info = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sidecar": str(args.sidecar),
        "out_db": str(args.out_db),
        "filters": {
            "start_utc_inclusive": args.start_utc,
            "end_utc_exclusive": args.end_utc,
            "tables": tables,
            "raw_table_filter_tables": raw_tables,
            "event_prefixes": args.event_prefixes or [],
            "market_prefixes": args.market_prefixes or [],
        },
        "raw_sidecar_rows_loaded": raw_rows,
        "synthetic_coinbase_from_top": bool(args.synthetic_coinbase_from_top),
        "coinbase_ticker_source": coinbase_ticker_source,
        "counts": counts,
        "warning": (
            "coinbase_ticker is synthetic when synthetic_coinbase_from_top=true; "
            "use a directly readable DuckDB for promotion-grade Coinbase tick-age replay."
        )
        if args.synthetic_coinbase_from_top
        else "",
    }
    (args.out_db.with_suffix(args.out_db.suffix + ".manifest.json")).write_text(
        json.dumps(info, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    con.close()
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
