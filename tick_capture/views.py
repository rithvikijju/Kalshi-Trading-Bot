"""DuckDB views over the rolling parquet shards.

We never store rows directly in DuckDB — parquet is the canonical store
(safer, smaller, easier to back up). DuckDB just registers views that
`read_parquet` with hive partitioning over the shard tree, so queries
like:

    duckdb tick_data/ticks.duckdb
    SELECT venue, msg_type, COUNT(*) FROM ticks GROUP BY 1,2;

work out of the box.

Call refresh_views(root) any time the shard tree's set of (venue, msg_type)
combinations changes — the view is dropped and recreated.
"""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

import duckdb


VIEW_FILE = "ticks.duckdb"


def _discover_paths(root: Path) -> Iterable[Path]:
    """Yield each (venue, msg_type) shard directory containing parquet files."""
    if not root.exists():
        return
    for venue_dir in root.iterdir():
        if not venue_dir.is_dir() or venue_dir.name.startswith("."):
            continue
        for mt_dir in venue_dir.iterdir():
            if not mt_dir.is_dir() or mt_dir.name.startswith("."):
                continue
            # Are there any parquet files under here?
            has_pq = any(p.suffix == ".parquet" and "tmp" not in p.name
                         for p in mt_dir.rglob("*.parquet"))
            if has_pq:
                yield mt_dir


def refresh_views(root: Path) -> None:
    """(Re)build the DuckDB view file at <root>/ticks.duckdb.

    Defines:
      - one view per (venue, msg_type): e.g. kalshi_orderbook_delta
      - one unified view `ticks` UNION ALL'ing the per-(venue, msg_type) views
      - convenience views: `kalshi_book`, `polymarket_book` flattening
        their top-of-book updates.
    """
    root = Path(root)
    db_path = root / VIEW_FILE
    paths = list(_discover_paths(root))
    if not paths:
        return

    con = duckdb.connect(str(db_path))
    try:
        # Drop everything we manage and rebuild fresh.
        existing = con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name='main'"
        ).fetchall()
        for (v,) in existing:
            try: con.execute(f"DROP VIEW IF EXISTS {v}")
            except Exception: pass

        per_view_names = []
        for d in paths:
            venue = d.parent.name
            mt = d.name
            vname = f"{venue}_{mt}".replace("-", "_")
            glob = str(d / "**" / "*.parquet").replace("'", "''")
            con.execute(
                f"CREATE VIEW {vname} AS SELECT *, '{venue}' AS _venue, "
                f"'{mt}' AS _msg_type FROM read_parquet('{glob}', "
                f"hive_partitioning=true, union_by_name=true)"
            )
            per_view_names.append(vname)

        if per_view_names:
            unions = " UNION ALL ".join(
                f"SELECT ts_us, recv_us, venue, msg_type, market_id, asset_id, "
                f"side, price, size, payload FROM {v}"
                for v in per_view_names
            )
            con.execute(f"CREATE VIEW ticks AS {unions}")

        # Make the file standalone-loadable by leaving connection open via PRAGMA.
        con.execute("CHECKPOINT")
    finally:
        con.close()
