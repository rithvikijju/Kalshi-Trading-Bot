"""
Live risk manager — the gate every order must pass.

This WRAPS the audited rule model in ../../topstep_strategy/risk_engine.py rather than
re-deriving TopStep's trailing-drawdown math. That file models the rules on *closed*
trade PnL; live trading also needs INTRADAY equity (with open MTM) checked against the
trailing floor every tick, because the account blows on intraday equity touching the
floor, not on closed PnL. So we add:

  * real-time intraday equity = realized + unrealized, checked vs the floor each quote
  * a hard pre-trade gate (can_enter) honoring daily-loss lock, streak halt, max contracts,
    distance-to-floor, and concurrent-position cap
  * stop-distance sizing that never risks more than the distance to the trailing floor

The ML confidence multiplier (ml/sizing.py) only ever scales DOWN within the cap this
returns — it can never increase risk past the deterministic ceiling.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Import the existing, audited rule engine without assuming it's an installed package.
_RISK_SRC = Path(__file__).resolve().parents[2] / "topstep_strategy" / "risk_engine.py"
_spec = importlib.util.spec_from_file_location("ts_risk_engine", _RISK_SRC)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ts_risk_engine"] = _mod
_spec.loader.exec_module(_mod)
RiskEngine = _mod.RiskEngine          # the dataclass with trailing-DD / daily-loss logic


class LiveRiskManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.eng = RiskEngine(
            account=cfg.account_size,
            per_trade_risk=cfg.per_trade_risk,
            loss_streak_halt=cfg.loss_streak_halt,
            giveback_frac=cfg.giveback_frac,
        )
        self.reason_blocked: str = ""

    # ---- equity views ----
    @property
    def equity(self) -> float:
        return self.eng.equity

    @property
    def floor(self) -> float:
        return self.eng.floor

    def intraday_equity(self, unrealized: float) -> float:
        return self.eng.equity + unrealized

    def floor_room(self, unrealized: float = 0.0) -> float:
        """Dollars between current intraday equity and the trailing-DD floor."""
        return self.intraday_equity(unrealized) - self.eng.floor

    # ---- the gate ----
    def can_enter(self, n_open_positions: int, unrealized: float = 0.0) -> bool:
        self.reason_blocked = ""
        if self.eng.blown:
            self.reason_blocked = "account blown (hit trailing drawdown floor)"
            return False
        if self.eng.passed:
            self.reason_blocked = "combine target already reached — stop trading"
            return False
        if self.eng.locked_today:
            self.reason_blocked = "locked for the day (daily loss / streak / give-back)"
            return False
        if n_open_positions >= self.cfg.max_concurrent_positions:
            self.reason_blocked = f"max concurrent positions ({self.cfg.max_concurrent_positions})"
            return False
        if self.floor_room(unrealized) <= self.cfg.per_trade_risk:
            self.reason_blocked = "too close to trailing-DD floor to risk another trade"
            return False
        return True

    def hard_floor_breached(self, unrealized: float) -> bool:
        """Intraday equity has touched the trailing floor — flatten everything NOW."""
        return self.intraday_equity(unrealized) <= self.eng.floor

    # ---- sizing ----
    def max_contracts(self, stop_ticks: float, tick_value: float,
                      unrealized: float = 0.0) -> int:
        """Deterministic ceiling: full stop ~= per_trade_risk, also capped by distance
        to floor and the account's max contracts. This is the hard cap the ML score
        scales *down* from — never up."""
        risk_per_ct = max(stop_ticks * tick_value, 1e-9)
        by_budget = int(self.cfg.per_trade_risk // risk_per_ct)
        room = self.floor_room(unrealized)
        by_room = int(room // risk_per_ct)
        by_acct = self.eng.p["max_ct"]
        return max(0, min(by_budget, by_room, by_acct))

    # ---- state updates ----
    def on_closed_trade(self, pnl: float) -> bool:
        """Apply a realized trade PnL. Returns False if it blew the account."""
        return self.eng.on_trade(pnl)

    def end_of_day(self):
        self.eng.end_of_day()

    def snapshot(self) -> dict:
        e = self.eng
        return dict(
            account=self.cfg.account_size, equity=round(e.equity, 2),
            floor=round(e.floor, 2), day_pnl=round(e.day_pnl, 2),
            streak=e.streak, locked_today=e.locked_today, blown=e.blown,
            passed=e.passed, target_remaining=round(e.p["target"] - (e.equity - e.p["start"]), 2),
        )
