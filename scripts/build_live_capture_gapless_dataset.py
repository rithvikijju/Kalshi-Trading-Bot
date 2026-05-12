#!/usr/bin/env python3
"""Build a consolidated, gap-audited live websocket capture dataset.

This does not touch the running bots. It reads snapshot/archived DuckDB files
and writes a separate DuckDB with:

* raw unioned capture tables with source labels
* exact-deduped top-of-book rows
* coverage/gap diagnostics by source/event/market

The script does not fabricate missing websocket data. "Gapless" here means the
output is sorted, deduped, and explicitly annotated so downstream tests can use
only contiguous market segments that have no large capture gaps.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless.duckdb"
DEFAULT_REPORT = PROJECT_ROOT / "data" / "live_capture_gapless" / "live_capture_gapless_report.json"
DEFAULT_SOURCES = [
    (
        "research_snapshot",
        PROJECT_ROOT
        / "backtest_outputs"
        / "live_capture_replay_20260510"
        / "capture_snapshots_manual"
        / "research_live_capture_20260510_124019.duckdb",
    ),
    (
        "multi_shadow_snapshot",
        PROJECT_ROOT
        / "backtest_outputs"
        / "live_capture_replay_20260510"
        / "capture_snapshots_manual"
        / "multi_strategy_shadow_capture_20260510_124250.duckdb",
    ),
    ("js_guarded_archive", Path.home() / ".btc_kalshi_bot" / "js_guarded_shadow_capture.duckdb"),
    (
        "market_shrink_archive",
        Path.home() / ".btc_kalshi_bot" / "market_shrink_no_cautious_shadow_capture.duckdb",
    ),
]
TABLES = [
    "ws_orderbook_top",
    "signal_scan",
    "order_decision",
    "coinbase_ticker",
    "ws_lifecycle",
    "ws_private_event",
    "capture_health",
    "ws_control",
    "ws_orderbook_delta",
    "ws_orderbook_snapshot_level",
]


@dataclass(frozen=True)
class Source:
    label: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--gap-seconds",
        type=float,
        default=120.0,
        help="Gap threshold used for coverage diagnostics.",
    )
    parser.add_argument(
        "--source",
        action="append",
        help="Additional/override source in label=path form. If supplied, defaults are not used.",
    )
    return parser.parse_args()


def configured_sources(args: argparse.Namespace) -> list[Source]:
    if not args.source:
        return [Source(label, path) for label, path in DEFAULT_SOURCES if path.exists()]
    out: list[Source] = []
    for raw in args.source:
        label, sep, path = raw.partition("=")
        if not sep:
            raise ValueError(f"--source must be label=path, got {raw!r}")
        out.append(Source(label.strip(), Path(path).expanduser()))
    return [src for src in out if src.path.exists()]


def table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    try:
        con.execute(f"SELECT * FROM {schema}.{table} LIMIT 0")
    except duckdb.Error:
        return False
    return True


def create_or_insert(
    out: duckdb.DuckDBPyConnection,
    src_schema: str,
    src: Source,
    table: str,
    created_tables: set[str],
) -> int:
    target = f"{table}_all"
    if table not in created_tables:
        out.execute(
            f"""
            CREATE TABLE {target} AS
            SELECT *, ?::VARCHAR AS capture_label, ?::VARCHAR AS capture_path
            FROM {src_schema}.{table}
            """,
            [src.label, str(src.path)],
        )
        created_tables.add(table)
    else:
        out.execute(
            f"""
            INSERT INTO {target}
            SELECT *, ?::VARCHAR AS capture_label, ?::VARCHAR AS capture_path
            FROM {src_schema}.{table}
            """,
            [src.label, str(src.path)],
        )
    return int(out.execute(f"SELECT count(*) FROM {src_schema}.{table}").fetchone()[0])


def build_dataset(args: argparse.Namespace) -> dict:
    sources = configured_sources(args)
    if not sources:
        raise FileNotFoundError("No source DuckDB files found.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()

    out = duckdb.connect(str(args.output))
    out.execute("PRAGMA threads=8")
    out.execute(
        """
        CREATE TABLE source_manifest(
            capture_label VARCHAR,
            capture_path VARCHAR,
            size_bytes UBIGINT,
            wal_size_bytes UBIGINT
        )
        """
    )

    source_table_rows: list[dict] = []
    created_tables: set[str] = set()
    for idx, src in enumerate(sources):
        schema = f"src_{idx}"
        quoted_path = str(src.path).replace("'", "''")
        out.execute(f"ATTACH '{quoted_path}' AS {schema} (READ_ONLY)")
        wal = Path(str(src.path) + ".wal")
        out.execute(
            "INSERT INTO source_manifest VALUES (?, ?, ?, ?)",
            [
                src.label,
                str(src.path),
                src.path.stat().st_size,
                wal.stat().st_size if wal.exists() else 0,
            ],
        )
        for table in TABLES:
            if table_exists(out, schema, table):
                rows = create_or_insert(out, schema, src, table, created_tables)
                source_table_rows.append({"source": src.label, "table": table, "rows": rows})
        out.execute(f"DETACH {schema}")

    if "ws_orderbook_top" in created_tables:
        # Exact dedupe only. Near-time overlap from different bots is preserved
        # because those rows represent different receive times.
        out.execute(
            """
            CREATE TABLE ws_orderbook_top_dedup AS
            SELECT *
            FROM ws_orderbook_top_all
            QUALIFY row_number() OVER (
                PARTITION BY received_at_ns, market_ticker, yes_bid, yes_bid_qty,
                             yes_ask, yes_ask_qty, no_bid, no_bid_qty,
                             no_ask, no_ask_qty, btc_spot, source
                ORDER BY capture_label
            ) = 1
            """
        )
        out.execute(
            """
            CREATE TABLE ws_orderbook_top_gaps AS
            WITH ordered AS (
                SELECT capture_label, event_ticker, market_ticker,
                       received_at_ns,
                       CAST(received_at_utc AS TIMESTAMPTZ) AS received_at,
                       lag(CAST(received_at_utc AS TIMESTAMPTZ)) OVER (
                           PARTITION BY capture_label, market_ticker
                           ORDER BY received_at_ns
                       ) AS prev_received_at
                FROM ws_orderbook_top_dedup
            ),
            gaps AS (
                SELECT *, date_diff('millisecond', prev_received_at, received_at) / 1000.0 AS gap_seconds
                FROM ordered
                WHERE prev_received_at IS NOT NULL
            )
            SELECT capture_label, event_ticker, market_ticker,
                   min(received_at) AS first_seen,
                   max(received_at) AS last_seen,
                   count(*) + 1 AS rows,
                   max(gap_seconds) AS max_gap_seconds,
                   sum(CASE WHEN gap_seconds > ? THEN 1 ELSE 0 END) AS large_gaps
            FROM gaps
            GROUP BY capture_label, event_ticker, market_ticker
            """,
            [args.gap_seconds],
        )
        out.execute(
            """
            CREATE TABLE event_coverage AS
            SELECT capture_label, event_ticker,
                   min(CAST(received_at_utc AS TIMESTAMPTZ)) AS first_seen,
                   max(CAST(received_at_utc AS TIMESTAMPTZ)) AS last_seen,
                   count(*) AS top_rows,
                   count(DISTINCT market_ticker) AS markets,
                   count(DISTINCT source) AS row_sources
            FROM ws_orderbook_top_dedup
            GROUP BY capture_label, event_ticker
            """
        )
    if "order_decision" in created_tables:
        out.execute(
            """
            CREATE TABLE order_decision_dedup AS
            SELECT *
            FROM order_decision_all
            QUALIFY row_number() OVER (
                PARTITION BY received_at_ns, mode, action, event_ticker, market_ticker,
                             side, entry_price, net_edge_cents, detail, capture_label
                ORDER BY capture_label
            ) = 1
            """
        )
    if "signal_scan" in created_tables:
        out.execute(
            """
            CREATE TABLE signal_scan_dedup AS
            SELECT *
            FROM signal_scan_all
            QUALIFY row_number() OVER (
                PARTITION BY received_at_ns, mode, event_ticker, selected_market,
                             selected_side, entry_price, net_edge_cents, action, detail,
                             capture_label
                ORDER BY capture_label
            ) = 1
            """
        )

    summary: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output": str(args.output),
        "sources": [{"label": src.label, "path": str(src.path)} for src in sources],
        "source_table_rows": source_table_rows,
        "notes": [
            "Active running DuckDB files are not read directly on Windows because they are locked.",
            "Rows come from snapshot/archived capture DBs supplied to this script.",
            "ws_orderbook_top_dedup only removes exact duplicate top-of-book rows.",
            "Use ws_orderbook_top_gaps to restrict validation to contiguous segments.",
        ],
    }
    for table in ("ws_orderbook_top_all", "ws_orderbook_top_dedup", "signal_scan_all", "order_decision_all"):
        if table_exists(out, "main", table):
            row = out.execute(
                f"""
                SELECT count(*) AS rows,
                       min(received_at_utc) AS first_seen,
                       max(received_at_utc) AS last_seen
                FROM {table}
                """
            ).fetchone()
            summary[table] = {"rows": int(row[0]), "first_seen": row[1], "last_seen": row[2]}
    if table_exists(out, "main", "ws_orderbook_top_gaps"):
        row = out.execute(
            """
            SELECT count(*) AS market_segments,
                   sum(large_gaps) AS large_gaps,
                   max(max_gap_seconds) AS max_gap_seconds
            FROM ws_orderbook_top_gaps
            """
        ).fetchone()
        summary["gap_audit"] = {
            "market_segments": int(row[0] or 0),
            "large_gaps": int(row[1] or 0),
            "max_gap_seconds": float(row[2] or 0.0),
            "gap_threshold_seconds": args.gap_seconds,
        }

    out.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    summary = build_dataset(args)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
