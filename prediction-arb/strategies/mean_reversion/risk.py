"""Risk gate for the mean-reversion strategy. Hard caps + daily-loss kill switch.

Mirrors the philosophy of core/risk_manager.py but for single-leg positions.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.mean_reversion.models import Signal
from strategies.mean_reversion.portfolio import MRPortfolio
from utils.logger import setup_logger

log = setup_logger("mr.risk")


class MRRiskManager:
    def __init__(self, cfg: dict):
        r = cfg["risk"]
        self.max_position_per_market = r["max_position_per_market_usd"]
        self.max_total_exposure = r["max_total_exposure_usd"]
        self.max_concurrent = r["max_concurrent_positions"]
        self.max_daily_loss = r["max_daily_loss_usd"]
        self.min_edge_cents = r["min_edge_cents"]
        self.kill_switch = False
        self.kill_reason = ""

    def trigger_kill(self, reason: str):
        self.kill_switch = True
        self.kill_reason = reason
        log.error(f"KILL SWITCH: {reason}")

    @staticmethod
    def _today_start_iso() -> str:
        now = datetime.now(timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    def check_daily_loss(self, pf: MRPortfolio):
        day_pnl = pf.realized_pnl_usd(since_iso=self._today_start_iso())
        if day_pnl <= -self.max_daily_loss:
            self.trigger_kill(f"daily_loss_cap (${day_pnl:.0f})")

    def approve_entry(self, sig: Signal, size: float, fill_price: float,
                      pf: MRPortfolio) -> tuple[bool, str]:
        if self.kill_switch:
            return False, f"kill_switch: {self.kill_reason}"
        if sig.expected_edge_cents < self.min_edge_cents:
            return False, f"edge_too_small ({sig.expected_edge_cents:.1f}c)"
        if size < 1:
            return False, "size<1"
        cost = fill_price * size
        if pf.market_exposure_usd(sig.market_id) + cost > self.max_position_per_market:
            return False, "per_market_cap"
        if pf.exposure_usd() + cost > self.max_total_exposure:
            return False, "total_exposure_cap"
        if pf.n_open() >= self.max_concurrent:
            return False, "max_concurrent"
        if pf.cash_usd() < cost:
            return False, "insufficient_cash"
        return True, ""
