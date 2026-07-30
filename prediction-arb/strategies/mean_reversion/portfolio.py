"""SQLite-backed single-leg portfolio for the mean-reversion strategy.

Distinct from core/portfolio.py (which models two-leg arb pairs). Here each row
is one directional position: buy a side, exit by selling it back (or letting it
settle). Tracks cash, fees, realized PnL, and a daily-PnL view for the kill switch.
"""
from __future__ import annotations
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from data.models import Side
from strategies.mean_reversion.models import MRPosition, PosStatus, Signal, SignalType
from strategies.mean_reversion.fees import trade_fee_cents


class MRPortfolio:
    def __init__(self, db_path: str, starting_capital_usd: float,
                 fee_model: str = "kalshi_pct"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.starting_capital = starting_capital_usd
        self.fee_model = fee_model
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        c = self._conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mr_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            size REAL NOT NULL,
            entry_price REAL NOT NULL,
            target_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            fair_yes_prob REAL,
            opened_at TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            exit_price REAL,
            exit_reason TEXT,
            entry_fee_usd REAL DEFAULT 0,
            exit_fee_usd REAL DEFAULT 0,
            realized_pnl REAL,
            closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS mr_state (key TEXT PRIMARY KEY, value REAL);
        CREATE TABLE IF NOT EXISTS mr_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, event_type TEXT NOT NULL, details TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mr_status ON mr_positions(status);
        """)
        if not c.execute("SELECT 1 FROM mr_state WHERE key='cash_usd'").fetchone():
            c.execute("INSERT INTO mr_state(key,value) VALUES('cash_usd',?)",
                      (self.starting_capital,))
        c.commit(); c.close()

    # ─── cash ──────────────────────────────────────────────────────
    def cash_usd(self) -> float:
        c = self._conn()
        r = c.execute("SELECT value FROM mr_state WHERE key='cash_usd'").fetchone()
        c.close()
        return float(r[0]) if r else 0.0

    def _set_cash(self, v: float):
        c = self._conn()
        c.execute("UPDATE mr_state SET value=? WHERE key='cash_usd'", (v,))
        c.commit(); c.close()

    def log_event(self, event_type: str, details: dict):
        c = self._conn()
        c.execute("INSERT INTO mr_events(ts,event_type,details) VALUES(?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), event_type,
                   json.dumps(details, default=str)))
        c.commit(); c.close()

    # ─── open / close ──────────────────────────────────────────────
    def open_position(self, sig: Signal, size: float, fill_price: float) -> MRPosition:
        entry_fee = trade_fee_cents(fill_price, self.fee_model) / 100.0 * size
        cost = fill_price * size + entry_fee
        c = self._conn()
        cur = c.execute("""INSERT INTO mr_positions(market_id,side,signal_type,size,
            entry_price,target_price,stop_price,fair_yes_prob,opened_at,status,entry_fee_usd)
            VALUES(?,?,?,?,?,?,?,?,?, 'open', ?)""",
            (sig.market_id, sig.side.value, sig.type.value, size, fill_price,
             sig.target_price, sig.stop_price, sig.fair_yes_prob,
             datetime.now(timezone.utc).isoformat(), entry_fee))
        pid = cur.lastrowid
        c.commit(); c.close()
        self._set_cash(self.cash_usd() - cost)
        self.log_event("open", {"pos_id": pid, "market": sig.market_id,
                                "side": sig.side.value, "size": size,
                                "entry": fill_price, "edge_c": sig.expected_edge_cents})
        return MRPosition(market_id=sig.market_id, side=sig.side, size=size,
                          entry_price=fill_price, target_price=sig.target_price,
                          stop_price=sig.stop_price, signal_type=sig.type,
                          pos_id=pid, fair_yes_prob=sig.fair_yes_prob)

    def close_position(self, pos: MRPosition, exit_price: float, reason: str) -> float:
        exit_fee = trade_fee_cents(exit_price, self.fee_model) / 100.0 * pos.size
        c = self._conn()
        row = c.execute("SELECT entry_fee_usd FROM mr_positions WHERE id=?",
                        (pos.pos_id,)).fetchone()
        entry_fee = float(row[0]) if row else 0.0
        proceeds = exit_price * pos.size - exit_fee
        gross = (exit_price - pos.entry_price) * pos.size
        realized = gross - entry_fee - exit_fee
        c.execute("""UPDATE mr_positions SET status='closed', exit_price=?,
            exit_reason=?, exit_fee_usd=?, realized_pnl=?, closed_at=? WHERE id=?""",
            (exit_price, reason, exit_fee, realized,
             datetime.now(timezone.utc).isoformat(), pos.pos_id))
        c.commit(); c.close()
        self._set_cash(self.cash_usd() + proceeds)
        self.log_event("close", {"pos_id": pos.pos_id, "exit": exit_price,
                                 "reason": reason, "pnl": round(realized, 2)})
        pos.status = PosStatus.CLOSED
        pos.exit_price = exit_price
        pos.exit_reason = reason
        pos.realized_pnl = realized
        return realized

    # ─── views ─────────────────────────────────────────────────────
    def open_positions(self) -> list[MRPosition]:
        c = self._conn()
        rows = c.execute("SELECT * FROM mr_positions WHERE status='open'").fetchall()
        c.close()
        out = []
        for r in rows:
            out.append(MRPosition(
                market_id=r["market_id"], side=Side(r["side"]), size=r["size"],
                entry_price=r["entry_price"], target_price=r["target_price"],
                stop_price=r["stop_price"], signal_type=SignalType(r["signal_type"]),
                opened_at=datetime.fromisoformat(r["opened_at"]), pos_id=r["id"],
                status=PosStatus.OPEN, fair_yes_prob=r["fair_yes_prob"],
            ))
        return out

    def exposure_usd(self) -> float:
        c = self._conn()
        r = c.execute("SELECT COALESCE(SUM(entry_price*size),0) FROM mr_positions "
                      "WHERE status='open'").fetchone()
        c.close()
        return float(r[0])

    def market_exposure_usd(self, market_id: str) -> float:
        c = self._conn()
        r = c.execute("SELECT COALESCE(SUM(entry_price*size),0) FROM mr_positions "
                      "WHERE status='open' AND market_id=?", (market_id,)).fetchone()
        c.close()
        return float(r[0])

    def n_open(self) -> int:
        c = self._conn()
        r = c.execute("SELECT COUNT(*) FROM mr_positions WHERE status='open'").fetchone()
        c.close()
        return int(r[0])

    def realized_pnl_usd(self, since_iso: Optional[str] = None) -> float:
        c = self._conn()
        if since_iso:
            r = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM mr_positions "
                          "WHERE status='closed' AND closed_at>=?", (since_iso,)).fetchone()
        else:
            r = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM mr_positions "
                          "WHERE status='closed'").fetchone()
        c.close()
        return float(r[0])

    def stats(self) -> dict:
        c = self._conn()
        row = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(realized_pnl),0) pnl,
            COUNT(CASE WHEN realized_pnl>0 THEN 1 END) wins,
            COUNT(CASE WHEN realized_pnl<=0 THEN 1 END) losses
            FROM mr_positions WHERE status='closed'""").fetchone()
        c.close()
        n = int(row["n"])
        return {"closed": n, "pnl_usd": float(row["pnl"]),
                "wins": int(row["wins"]), "losses": int(row["losses"]),
                "win_rate": (float(row["wins"]) / n) if n else 0.0,
                "cash_usd": self.cash_usd(), "open": self.n_open()}

    def reset(self):
        Path(self.db_path).unlink(missing_ok=True)
        self._init()
