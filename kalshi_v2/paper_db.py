"""SQLite persistence layer.

Single source of truth for all paper / live / shadow trades. Schema is
idempotent — safe to import or call init() multiple times.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import CFG


def _conn():
    db = Path(CFG["db_path"]).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c


def init_db():
    conn = _conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_utc   TEXT NOT NULL,
        event_ticker    TEXT,
        market_ticker   TEXT NOT NULL,
        side            TEXT NOT NULL,                -- 'yes' / 'no'
        entry_price     REAL NOT NULL,
        contracts       INTEGER NOT NULL,
        entry_edge_cents REAL,
        model_p_yes     REAL,
        market_yes_mid  REAL,
        btc_spot_entry  REAL,
        ttl_hours_entry REAL,
        confidence      REAL,
        trade_type      TEXT DEFAULT 'v2',
        robust_pass_rate REAL,
        robust_mean_edge_c REAL,
        -- Settlement
        settled         INTEGER DEFAULT 0,
        settle_price    REAL,
        pnl_dollars     REAL,
        exit_reason     TEXT,
        settled_at      TEXT,
        -- Live order tracking
        kalshi_order_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_trades_ts        ON trades(timestamp_utc);
    CREATE INDEX IF NOT EXISTS idx_trades_settled   ON trades(settled);
    CREATE INDEX IF NOT EXISTS idx_trades_type      ON trades(trade_type);
    CREATE INDEX IF NOT EXISTS idx_trades_market    ON trades(market_ticker);

    CREATE TABLE IF NOT EXISTS spot_ticks (
        ts          TEXT NOT NULL,
        price       REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_spot_ticks_ts ON spot_ticks(ts);

    CREATE TABLE IF NOT EXISTS book_ticks (
        ts          TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        yes_bid     REAL,
        yes_ask     REAL,
        last_price  REAL,
        volume      INTEGER,
        status      TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_book_ticks_ts ON book_ticks(ts);
    CREATE INDEX IF NOT EXISTS idx_book_ticks_tk ON book_ticks(ticker, ts);

    CREATE TABLE IF NOT EXISTS robust_decisions (
        ts            TEXT NOT NULL,
        ticker        TEXT,
        side          TEXT,
        entry_price   REAL,
        n_measures    INTEGER,
        pass_rate     REAL,
        mean_edge_c   REAL,
        min_edge_c    REAL,
        max_edge_c    REAL,
        passed        INTEGER,
        reason        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_robust_ts ON robust_decisions(ts);
    """)
    conn.commit(); conn.close()


def record_trade(trade: dict) -> int:
    """Insert a new trade row; returns the new id."""
    conn = _conn()
    keys = ["timestamp_utc", "event_ticker", "market_ticker", "side",
            "entry_price", "contracts", "entry_edge_cents", "model_p_yes",
            "market_yes_mid", "btc_spot_entry", "ttl_hours_entry",
            "confidence", "trade_type", "robust_pass_rate",
            "robust_mean_edge_c", "kalshi_order_id"]
    placeholders = ",".join("?" * len(keys))
    values = tuple(trade.get(k) for k in keys)
    conn.execute(
        f"INSERT INTO trades({','.join(keys)}) VALUES ({placeholders})", values)
    tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit(); conn.close()
    return int(tid)


def settle_trade(trade_id: int, settle_price: float, pnl_dollars: float,
                  exit_reason: str):
    conn = _conn()
    conn.execute(
        "UPDATE trades SET settled=1, settle_price=?, pnl_dollars=?, "
        "exit_reason=?, settled_at=? WHERE id=?",
        (float(settle_price), float(pnl_dollars), exit_reason,
         datetime.now(timezone.utc).isoformat(), int(trade_id)))
    conn.commit(); conn.close()


def open_trades(tags=None) -> pd.DataFrame:
    where = "settled=0"
    params: tuple = ()
    if tags is not None:
        real = tuple(t for t in tags if t is not None)
        ph = ",".join("?" * len(real))
        null_clause = " OR trade_type IS NULL" if None in tags else ""
        where += f" AND (trade_type IN ({ph}){null_clause})"
        params = real
    conn = _conn()
    df = pd.read_sql_query(
        f"SELECT * FROM trades WHERE {where} ORDER BY timestamp_utc", conn, params=params)
    conn.close()
    return df


def settled_trades(tags=None) -> pd.DataFrame:
    where = "settled=1"
    params: tuple = ()
    if tags is not None:
        real = tuple(t for t in tags if t is not None)
        ph = ",".join("?" * len(real))
        null_clause = " OR trade_type IS NULL" if None in tags else ""
        where += f" AND (trade_type IN ({ph}){null_clause})"
        params = real
    conn = _conn()
    df = pd.read_sql_query(
        f"SELECT * FROM trades WHERE {where} ORDER BY COALESCE(settled_at, timestamp_utc)",
        conn, params=params)
    conn.close()
    return df


def ticker_already_open(ticker: str, tags) -> bool:
    """Hard ticker dedup. Returns True if any unsettled trade exists on
    this market with one of the given tags."""
    real = tuple(t for t in tags if t is not None)
    if not real: return False
    ph = ",".join("?" * len(real))
    null_clause = " OR trade_type IS NULL" if None in tags else ""
    conn = _conn()
    n = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE settled=0 AND market_ticker=? "
        f"AND (trade_type IN ({ph}){null_clause})",
        (ticker, *real)).fetchone()[0]
    conn.close()
    return n > 0


def log_robust_decision(ticker, side, entry_price, diag, passed, reason=""):
    conn = _conn()
    conn.execute(
        "INSERT INTO robust_decisions(ts, ticker, side, entry_price, n_measures, "
        "pass_rate, mean_edge_c, min_edge_c, max_edge_c, passed, reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), ticker, side, float(entry_price),
         diag.get("n_measures"), diag.get("pass_rate"), diag.get("mean_edge_c"),
         diag.get("min_edge_c"), diag.get("max_edge_c"), int(passed), reason))
    conn.commit(); conn.close()


# Initialize on import
init_db()
