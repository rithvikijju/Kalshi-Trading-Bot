"""SQLite-backed portfolio + trade log.

Single source of truth for: open positions, settled arb pairs, capital state.
"""
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import (Position, ArbPair, Platform, Side, ArbOpportunity,
                          PortfolioSnapshot)


class Portfolio:
    def __init__(self, db_path: str = "data/paper_trades.db",
                 starting_capital_usd: float = 5_000.0):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.starting_capital = starting_capital_usd
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        c = self._conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            market_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            avg_entry_price REAL NOT NULL,
            arb_pair_id TEXT,
            opened_at TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            realized_pnl REAL DEFAULT 0,
            closed_at TEXT,
            close_price REAL,
            raw TEXT
        );
        CREATE TABLE IF NOT EXISTS arb_pairs (
            arb_pair_id TEXT PRIMARY KEY,
            kalshi_market_id TEXT NOT NULL,
            polymarket_market_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            size REAL NOT NULL,
            capital_deployed_usd REAL NOT NULL,
            expected_payout_usd REAL NOT NULL,
            expected_net_edge_usd REAL NOT NULL,
            detected_at TEXT NOT NULL,
            resolution_date TEXT,
            status TEXT DEFAULT 'open',
            actual_pnl_usd REAL,
            settled_at TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS portfolio_state (
            key TEXT PRIMARY KEY,
            value REAL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event_type TEXT NOT NULL,
            details TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status);
        CREATE INDEX IF NOT EXISTS idx_arb_status ON arb_pairs(status);
        """)
        # seed cash if first run
        cur = c.execute("SELECT value FROM portfolio_state WHERE key='cash_usd'")
        if not cur.fetchone():
            c.execute("INSERT INTO portfolio_state(key, value) VALUES (?, ?)",
                      ("cash_usd", self.starting_capital))
            c.execute("INSERT INTO portfolio_state(key, value) VALUES (?, ?)",
                      ("starting_capital", self.starting_capital))
        c.commit(); c.close()

    # ─── State accessors ──────────────────────────────────────────
    def cash_usd(self) -> float:
        c = self._conn()
        r = c.execute("SELECT value FROM portfolio_state WHERE key='cash_usd'").fetchone()
        c.close()
        return float(r[0]) if r else 0.0

    def _set_cash(self, new_cash: float):
        c = self._conn()
        c.execute("UPDATE portfolio_state SET value=? WHERE key='cash_usd'", (new_cash,))
        c.commit(); c.close()

    def log_event(self, event_type: str, details: dict):
        c = self._conn()
        c.execute("INSERT INTO events(ts, event_type, details) VALUES (?, ?, ?)",
                  (datetime.now(timezone.utc).isoformat(), event_type,
                   json.dumps(details, default=str)))
        c.commit(); c.close()

    # ─── Open / settle arb pairs ──────────────────────────────────
    def open_arb_pair(self, op: ArbOpportunity, size: float, arb_pair_id: str):
        cap = (op.kalshi_price + op.polymarket_price) * size
        # Persist both positions + the arb_pair record
        c = self._conn()
        c.execute("""INSERT INTO positions(platform, market_id, side, size,
            avg_entry_price, arb_pair_id, opened_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')""",
            (Platform.KALSHI.value, op.pair.kalshi_market.market_id,
             op.kalshi_side.value, size, op.kalshi_price,
             arb_pair_id, datetime.now(timezone.utc).isoformat()))
        c.execute("""INSERT INTO positions(platform, market_id, side, size,
            avg_entry_price, arb_pair_id, opened_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')""",
            (Platform.POLYMARKET.value, op.pair.polymarket_market.market_id,
             op.polymarket_side.value, size, op.polymarket_price,
             arb_pair_id, datetime.now(timezone.utc).isoformat()))
        res_iso = op.pair.kalshi_market.resolution_date.isoformat() if op.pair.kalshi_market.resolution_date else None
        c.execute("""INSERT INTO arb_pairs(arb_pair_id, kalshi_market_id,
            polymarket_market_id, direction, size, capital_deployed_usd,
            expected_payout_usd, expected_net_edge_usd, detected_at,
            resolution_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (arb_pair_id, op.pair.kalshi_market.market_id,
             op.pair.polymarket_market.market_id, op.direction.value, size,
             cap, op.estimated_payout_usd, op.net_edge_cents / 100 * size,
             op.detected_at.isoformat(), res_iso))
        c.commit(); c.close()
        self._set_cash(self.cash_usd() - cap)
        self.log_event("open_arb", {"arb_pair_id": arb_pair_id, "size": size,
                                     "capital": cap, "expected_edge_usd": op.net_edge_cents / 100 * size})

    def settle_arb_pair(self, arb_pair_id: str, kalshi_won: bool):
        """Settle. kalshi_won = True means the Kalshi leg paid out, etc.
        Both legs are linked — only one of (yes_k+no_p) or (no_k+yes_p) wins on each."""
        c = self._conn()
        r = c.execute("SELECT * FROM arb_pairs WHERE arb_pair_id=?", (arb_pair_id,)).fetchone()
        if not r:
            c.close(); return
        positions = c.execute("SELECT * FROM positions WHERE arb_pair_id=? AND status='open'",
                              (arb_pair_id,)).fetchall()
        size = float(r["size"])
        total_payout = 0.0
        for p in positions:
            is_kalshi = p["platform"] == Platform.KALSHI.value
            # An arb that's properly constructed: exactly ONE of the two sides pays $1
            won = (is_kalshi and kalshi_won) or ((not is_kalshi) and (not kalshi_won))
            payout = float(p["size"]) if won else 0.0
            total_payout += payout
            close_price = 1.0 if won else 0.0
            realized = (close_price - float(p["avg_entry_price"])) * float(p["size"])
            c.execute("""UPDATE positions SET status='closed', closed_at=?,
                close_price=?, realized_pnl=? WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), close_price, realized, p["id"]))
        actual_pnl = total_payout - float(r["capital_deployed_usd"])
        c.execute("""UPDATE arb_pairs SET status='settled', actual_pnl_usd=?,
            settled_at=? WHERE arb_pair_id=?""",
            (actual_pnl, datetime.now(timezone.utc).isoformat(), arb_pair_id))
        c.commit(); c.close()
        # Credit cash: original capital back + actual pnl
        self._set_cash(self.cash_usd() + float(r["capital_deployed_usd"]) + actual_pnl)
        self.log_event("settle_arb", {"arb_pair_id": arb_pair_id,
                                       "actual_pnl_usd": actual_pnl})

    # ─── Snapshot ────────────────────────────────────────────────
    def snapshot(self) -> PortfolioSnapshot:
        c = self._conn()
        open_arbs = c.execute("SELECT COUNT(*), COALESCE(SUM(capital_deployed_usd),0) "
                              "FROM arb_pairs WHERE status='open'").fetchone()
        n_open = int(open_arbs[0])
        locked = float(open_arbs[1])
        settled = c.execute("SELECT COUNT(*), COALESCE(SUM(actual_pnl_usd),0),"
                            " COUNT(CASE WHEN actual_pnl_usd > 0 THEN 1 END),"
                            " COUNT(CASE WHEN actual_pnl_usd <= 0 THEN 1 END)"
                            " FROM arb_pairs WHERE status='settled'").fetchone()
        n_settled = int(settled[0])
        realized = float(settled[1])
        wins = int(settled[2]); losses = int(settled[3])
        c.close()
        return PortfolioSnapshot(
            starting_capital_usd=self.starting_capital,
            cash_usd=self.cash_usd(),
            locked_in_positions_usd=locked,
            realized_pnl_usd=realized,
            unrealized_pnl_usd=0.0,  # filled in by caller with current mids
            open_arb_pairs=n_open,
            settled_arb_pairs=n_settled,
            wins=wins, losses=losses,
        )

    def open_arb_pairs_df(self):
        import pandas as pd
        c = self._conn()
        df = pd.read_sql_query("SELECT * FROM arb_pairs WHERE status='open' ORDER BY detected_at DESC", c)
        c.close()
        return df

    def all_events_df(self):
        import pandas as pd
        c = self._conn()
        df = pd.read_sql_query("SELECT * FROM events ORDER BY id DESC LIMIT 100", c)
        c.close()
        return df

    def settled_arbs_df(self):
        import pandas as pd
        c = self._conn()
        df = pd.read_sql_query("SELECT * FROM arb_pairs WHERE status='settled' ORDER BY settled_at DESC", c)
        c.close()
        return df

    def reset(self):
        """Wipe everything and restart with starting_capital."""
        Path(self.db_path).unlink(missing_ok=True)
        self._init()
