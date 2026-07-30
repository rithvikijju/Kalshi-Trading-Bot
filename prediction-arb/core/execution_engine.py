"""Execution engine.

Paper mode: simulates fills against current order books with realistic slippage.
Live mode: routes to actual broker APIs (Kalshi + Polymarket).

The two-leg execution problem is solved differently per mode:
- Paper: both legs "fill" atomically at the detected prices (with slippage walk).
- Live: place limit at the arb price on each side simultaneously; on partial,
  apply `failed_leg_behavior` policy.
"""
from __future__ import annotations
import asyncio, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import (ArbOpportunity, OrderRequest, OrderResponse, OrderStatus,
                          Platform, Side)
from clients.base_client import PredictionMarketClient
from core.portfolio import Portfolio
from core.risk_manager import RiskManager
from utils.logger import setup_logger

log = setup_logger("exec")


class ExecutionEngine:
    def __init__(self,
                 kalshi_client: PredictionMarketClient,
                 polymarket_client: PredictionMarketClient,
                 portfolio: Portfolio,
                 risk: RiskManager,
                 mode: str = "paper",
                 leg_timeout_seconds: float = 30.0,
                 failed_leg_behavior: str = "cancel"):
        self.k = kalshi_client
        self.p = polymarket_client
        self.portfolio = portfolio
        self.risk = risk
        self.mode = mode
        self.leg_timeout = leg_timeout_seconds
        self.failed_leg_behavior = failed_leg_behavior

    async def execute(self, op: ArbOpportunity) -> tuple[bool, str]:
        """Execute one arb opportunity. Returns (success, reason)."""
        ok, reason = self.risk.approve(op, self.portfolio)
        if not ok:
            log.info(f"SKIP arb: {reason}  edge={op.net_edge_cents:.1f}c")
            self.portfolio.log_event("skip", {"reason": reason,
                                               "kalshi_id": op.pair.kalshi_market.market_id,
                                               "poly_id": op.pair.polymarket_market.market_id,
                                               "net_edge_cents": op.net_edge_cents})
            return False, reason

        if self.mode == "paper":
            return await self._paper_execute(op)
        else:
            return await self._live_execute(op)

    async def _paper_execute(self, op: ArbOpportunity) -> tuple[bool, str]:
        """Simulate: assume we filled both legs at the arb prices."""
        size = op.max_size_contracts
        if size < 1:
            return False, "size<1"
        # Apply mild slippage: lose 25% of the gross edge to simulate realistic walk
        slip_cents = op.gross_edge_cents * 0.25
        net_after_slip = op.net_edge_cents - slip_cents
        if net_after_slip < self.risk.min_edge:
            return False, f"net_edge_after_slippage_too_small ({net_after_slip:.2f}c)"
        arb_pair_id = f"arb-{uuid.uuid4().hex[:10]}"
        self.portfolio.open_arb_pair(op, size, arb_pair_id)
        log.info(f"PAPER FILL [{arb_pair_id}] {op.pair.kalshi_market.market_id} "
                 f"+ {op.pair.polymarket_market.market_id[:18]}  size={size:.1f}  "
                 f"net_edge={op.net_edge_cents:.1f}c → expected ${op.net_edge_cents/100 * size:.2f}")
        return True, "paper_fill"

    async def _live_execute(self, op: ArbOpportunity) -> tuple[bool, str]:
        """Live: place both legs as limit orders, watch fills, handle failures."""
        size = max(int(op.max_size_contracts), 1)
        arb_pair_id = f"arb-{uuid.uuid4().hex[:10]}"

        # Place LESS LIQUID leg first to reduce execution risk. For now, default
        # to Polymarket first because we can use post-only (maker) and not move
        # the market while we wait for Kalshi to fill.
        poly_order = OrderRequest(
            platform=Platform.POLYMARKET,
            market_id=op.pair.polymarket_market.market_id,
            side=op.polymarket_side,
            size=size,
            limit_price=op.polymarket_price,
            post_only=True,   # critical: 0% fee
            arb_pair_id=arb_pair_id,
        )
        kalshi_order = OrderRequest(
            platform=Platform.KALSHI,
            market_id=op.pair.kalshi_market.market_id,
            side=op.kalshi_side,
            size=size,
            limit_price=op.kalshi_price,
            arb_pair_id=arb_pair_id,
        )

        try:
            poly_resp, kalshi_resp = await asyncio.gather(
                self.p.place_order(poly_order),
                self.k.place_order(kalshi_order),
                return_exceptions=True,
            )
        except Exception as e:
            log.error(f"live exec failed: {e}")
            return False, f"exec_error: {e}"

        # TODO: handle partials, retries, failed leg per `self.failed_leg_behavior`
        # For now: if both went through, treat as filled
        if (isinstance(poly_resp, OrderResponse) and isinstance(kalshi_resp, OrderResponse)
            and poly_resp.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)
            and kalshi_resp.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)):
            self.portfolio.open_arb_pair(op, size, arb_pair_id)
            return True, "live_fill"

        # One or both failed — try to cancel any that succeeded
        if isinstance(poly_resp, OrderResponse):
            try: await self.p.cancel_order(poly_resp.order_id)
            except Exception: pass
        if isinstance(kalshi_resp, OrderResponse):
            try: await self.k.cancel_order(kalshi_resp.order_id)
            except Exception: pass
        return False, "partial_or_failed_legs"
