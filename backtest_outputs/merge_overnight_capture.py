"""Convert an overnight tick-capture SQLite snapshot into a backtest-ready
DuckDB file matching the existing analysis.duckdb schema.

Usage:
    # In Jupyter after the overnight run:
    >>> path = snapshot_ticks_db('night1')
    # Then in shell:
    $ python backtest_outputs/merge_overnight_capture.py output/captures/ticks_capture_night1.db

This produces:
    backtest_outputs/captures/{label}.duckdb
        ├── strike_snaps_1s  (same schema as analysis.duckdb)
        └── spot_ticks_1s

You can then ATTACH multiple captures alongside analysis.duckdb in any
backtest script to extend the dataset:

    ana.execute("ATTACH 'backtest_outputs/captures/night1.duckdb' AS night1 (READ_ONLY)")
    ana.execute("CREATE VIEW combined_snaps AS "
                "SELECT * FROM strike_snaps_1s_clean "
                "UNION ALL SELECT * FROM night1.strike_snaps_1s")

Settlements are NOT included — markets need to actually close + Kalshi must
publish results before the bot can fetch them. Run with `--fetch-settles`
after the markets close to backfill that table.
"""
import argparse, sqlite3, re, sys, os
from pathlib import Path
import duckdb

STRIKE_RE = re.compile(r'-T(\d+(?:\.\d+)?)$')

def parse_strike(market_ticker: str):
    """KXBTCD-26MAY1208-T80999.99 → 80999.99"""
    m = STRIKE_RE.search(market_ticker or '')
    return float(m.group(1)) if m else None

def main():
    p = argparse.ArgumentParser()
    p.add_argument('source', help='SQLite capture path (from snapshot_ticks_db)')
    p.add_argument('--out-dir', default='backtest_outputs/captures')
    p.add_argument('--label', default=None,
                   help='Output filename label; defaults to source basename')
    args = p.parse_args()

    src = Path(args.source)
    if not src.exists():
        sys.exit(f'No such file: {src}')

    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    label = args.label or src.stem  # e.g. ticks_capture_night1
    out_path = out_dir / f'{label}.duckdb'

    if out_path.exists():
        # Refuse to overwrite an existing capture — they should be immutable
        sys.exit(f'Output already exists: {out_path}. Delete it first if you mean to.')

    print(f'Source:      {src}  ({src.stat().st_size/1e6:.1f} MB)')
    print(f'Destination: {out_path}')

    # Read SQLite via duckdb's sqlite_scanner
    db = duckdb.connect(str(out_path))
    db.execute("INSTALL sqlite; LOAD sqlite;")
    db.execute(f"ATTACH '{src}' AS src (TYPE SQLITE, READ_ONLY)")

    n_ticks = db.execute("SELECT COUNT(*) FROM src.ticks").fetchone()[0]
    n_spots = db.execute("SELECT COUNT(*) FROM src.spot_ticks").fetchone()[0]
    n_evt   = db.execute("SELECT COUNT(DISTINCT event_ticker) FROM src.ticks").fetchone()[0]
    print(f'  src ticks: {n_ticks:,}  spot_ticks: {n_spots:,}  events: {n_evt}')
    if n_ticks == 0:
        sys.exit('No ticks to merge — nothing to do.')

    # Inline the strike-parsing in SQL (DuckDB regex) instead of a UDF.
    # KXBTCD-26MAY1208-T80999.99  →  80999.99
    STRIKE_SQL = "TRY_CAST(regexp_extract(market_ticker, '-T(\\d+(?:\\.\\d+)?)$', 1) AS DOUBLE)"

    # Build canonical strike_snaps_1s — one row per (market_ticker, ts_sec)
    # using the LAST tick within each second (state at end of second).
    print('Building strike_snaps_1s…')
    db.execute('''
    CREATE TABLE strike_snaps_1s AS
    WITH parsed AS (
      SELECT
        event_ticker,
        market_ticker,
        parse_strike(market_ticker) AS strike,
        CAST(EXTRACT(EPOCH FROM CAST(ts AS TIMESTAMP)) AS BIGINT) AS ts_sec,
        yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
        btc_spot, secs_to_close,
        ROW_NUMBER() OVER (
          PARTITION BY market_ticker,
                       CAST(EXTRACT(EPOCH FROM CAST(ts AS TIMESTAMP)) AS BIGINT)
          ORDER BY ts DESC
        ) AS rn
      FROM src.ticks
      WHERE market_ticker IS NOT NULL
        AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
    )
    SELECT event_ticker, market_ticker, strike, ts_sec,
           yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
           btc_spot, secs_to_close
    FROM parsed
    WHERE rn = 1
    ''')
    n_clean = db.execute("SELECT COUNT(*) FROM strike_snaps_1s").fetchone()[0]
    print(f'  strike_snaps_1s: {n_clean:,} rows')

    # 1-sec spot aggregates
    db.execute('''
    CREATE TABLE spot_ticks_1s AS
    WITH parsed AS (
      SELECT
        CAST(EXTRACT(EPOCH FROM CAST(ts AS TIMESTAMP)) AS BIGINT) AS ts_sec,
        price,
        ROW_NUMBER() OVER (
          PARTITION BY CAST(EXTRACT(EPOCH FROM CAST(ts AS TIMESTAMP)) AS BIGINT)
          ORDER BY ts DESC
        ) AS rn
      FROM src.spot_ticks
    )
    SELECT ts_sec, price AS btc_spot FROM parsed WHERE rn = 1
    ''')
    n_spot1s = db.execute("SELECT COUNT(*) FROM spot_ticks_1s").fetchone()[0]
    print(f'  spot_ticks_1s:   {n_spot1s:,} rows')

    # Indexes for fast joins
    db.execute('CREATE INDEX idx_snap_mkt_ts ON strike_snaps_1s(market_ticker, ts_sec)')
    db.execute('CREATE INDEX idx_snap_evt_ts ON strike_snaps_1s(event_ticker, ts_sec)')
    db.execute('CREATE INDEX idx_spot_ts ON spot_ticks_1s(ts_sec)')

    # Coverage summary
    t_min, t_max = db.execute('SELECT MIN(ts_sec), MAX(ts_sec) FROM strike_snaps_1s').fetchone()
    duration_min = (t_max - t_min) / 60 if t_min else 0
    print(f'\nCoverage: {duration_min:.1f} min, '
          f'avg {n_clean/max(duration_min,1):.0f} snaps/min across {n_evt} events')

    # Where settlements come from: when markets close, Kalshi publishes them.
    # If you re-run this script later with --fetch-settles you can backfill.
    print('\nDone. To use in a backtest:')
    print(f"  ATTACH '{out_path}' AS cap (READ_ONLY);")
    print(f"  SELECT * FROM cap.strike_snaps_1s LIMIT 5;")
    print()
    print('NOTE: settlements are NOT included. They need to be fetched from')
    print('Kalshi after the markets close. The existing analysis.duckdb has')
    print('the historical settlements table you can join against.')

    db.close()

if __name__ == '__main__':
    main()
