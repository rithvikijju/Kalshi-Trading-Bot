"""Risk manager. Hard limits + kill switch."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import ArbOpportunity
from core.portfolio import Portfolio
from utils.logger import setup_logger

log = setup_logger("risk")


class RiskManager:
    def __init__(self,
                 max_position_per_market_usd: float = 500,
                 max_total_exposure_usd: float = 5_000,
                 max_daily_loss_usd: float = 200,
                 min_net_edge_cents: float = 2.0,
                 min_match_confidence: float = 0.90,
                 min_liquidity_usd: float = 100):
        self.max_position = max_position_per_market_usd
        self.max_exposure = max_total_exposure_usd
        self.max_daily_loss = max_daily_loss_usd
        self.min_edge = min_net_edge_cents
        self.min_conf = min_match_confidence
        self.min_liq = min_liquidity_usd
        self.kill_switch = False
        self.kill_reason = ""

    def trigger_kill(self, reason: str):
        self.kill_switch = True
        self.kill_reason = reason
        log.error(f"KILL SWITCH activated: {reason}")

    def approve(self, op: ArbOpportunity, portfolio: Portfolio) -> tuple[bool, str]:
        if self.kill_switch:
            return False, f"kill_switch: {self.kill_reason}"
        if op.pair.confidence < self.min_conf:
            return False, f"low_match_confidence ({op.pair.confidence:.2f} < {self.min_conf})"
        if op.net_edge_cents < self.min_edge:
            return False, f"edge_too_small ({op.net_edge_cents:.2f}c < {self.min_edge}c)"
        if op.capital_required_usd > self.max_position:
            return False, f"position_too_large (${op.capital_required_usd:.0f} > ${self.max_position})"
        if op.capital_required_usd < self.min_liq:
            return False, f"liquidity_too_thin (${op.capital_required_usd:.0f} < ${self.min_liq})"

        # Aggregate exposure
        snap = portfolio.snapshot()
        if snap.locked_in_positions_usd + op.capital_required_usd > self.max_exposure:
            return False, f"exposure_cap_hit (would_be ${snap.locked_in_positions_usd + op.capital_required_usd:.0f})"
        if snap.realized_pnl_usd < -self.max_daily_loss:
            self.trigger_kill(f"daily_loss_cap (${snap.realized_pnl_usd:.0f})")
            return False, f"daily_loss_cap"
        return True, ""
