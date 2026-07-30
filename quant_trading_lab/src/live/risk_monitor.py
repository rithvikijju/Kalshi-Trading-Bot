"""Hard risk monitor. Wraps a broker to enforce limits.

The monitor runs BEFORE every order is sent. Any breach blocks the order,
emits a risk_event row, and (if configured) sends an alert. The same
RiskLimits dataclass is used by the backtest engine — so paper/live
behavior matches what was simulated.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from .broker_base import Broker, Order
from ..backtest.engine import RiskLimits
from ..data.storage import log_risk_event
from ..utils.logging import get_logger

log = get_logger("risk")


@dataclass
class RiskState:
    run_id: str
    limits: RiskLimits = field(default_factory=RiskLimits)
    starting_equity: float = 100_000.0
    hwm: float = field(init=False)
    halted: bool = False
    derisk_factor: float = 1.0
    daily_pnl_pct: float = 0.0
    last_equity: float = field(init=False)
    stale_data_max_age_sec: int = 60 * 15

    def __post_init__(self):
        self.hwm = self.starting_equity
        self.last_equity = self.starting_equity


class RiskMonitor:
    def __init__(self, broker: Broker, state: RiskState):
        self.broker = broker
        self.state = state

    def update_equity(self, equity: float) -> None:
        if equity > self.state.hwm:
            self.state.hwm = equity
        self.state.daily_pnl_pct = equity / self.state.last_equity - 1 if self.state.last_equity > 0 else 0
        dd = equity / self.state.hwm - 1
        if dd <= self.state.limits.drawdown_warn:
            self._event("dd_warn", "warn", f"DD {dd:.2%}")
        if dd <= self.state.limits.drawdown_derisk and self.state.derisk_factor > 0.5:
            self.state.derisk_factor = 0.5
            self._event("dd_derisk", "warn", f"DD {dd:.2%} → leverage halved")
        if dd <= self.state.limits.drawdown_stop:
            self.state.halted = True
            self._event("dd_stop", "critical", f"DD {dd:.2%} → halt")
        if self.state.daily_pnl_pct <= self.state.limits.max_daily_loss_pct:
            self.state.halted = True
            self._event("daily_loss_halt", "critical",
                        f"day PnL {self.state.daily_pnl_pct:.2%} → halt")
        self.state.last_equity = equity

    def check_data_freshness(self, last_data_ts: pd.Timestamp, now: pd.Timestamp) -> bool:
        age = (now - last_data_ts).total_seconds()
        if age > self.state.stale_data_max_age_sec:
            self._event("stale_data", "warn", f"data age {age:.0f}s > limit")
            return False
        return True

    def submit(self, order: Order) -> dict:
        if self.state.halted:
            self._event("blocked", "critical", f"halted, refusing {order.symbol} {order.side} {order.qty}")
            return dict(status="blocked", reason="halted")
        acct = self.broker.account()
        eq = acct.get("equity", self.state.last_equity)
        px = self.broker.last_price(order.symbol)
        notional = order.qty * px
        if eq > 0 and abs(notional) / eq > self.state.limits.max_single_asset_weight:
            self._event("blocked", "warn", f"position cap on {order.symbol}")
            return dict(status="blocked", reason="position_cap")
        return self.broker.submit(order)

    def _event(self, kind: str, severity: str, message: str) -> None:
        log.warning(f"[risk] {kind}: {message}")
        log_risk_event(self.state.run_id, pd.Timestamp.utcnow(), kind, severity, message)
