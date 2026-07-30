"""
Position sizing = deterministic risk cap × ML confidence scale.

The order of operations is the safety contract:
  1. risk engine computes the HARD cap (contracts s.t. a full stop ≈ per_trade_risk,
     also bounded by distance-to-floor and the account max).
  2. the confidence model maps setup quality → a fraction in [0, 1].
  3. final size = floor(cap × fraction), clamped to >= 0.

Confidence can only shrink size. If the cap is 0 (no room / locked), size is 0 regardless
of how good the setup looks.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..broker.base import ContractMeta
from .engine import LiveRiskManager


@dataclass
class SizingDecision:
    size: int
    cap: int
    confidence: float
    scale: float
    stop_ticks: float
    risk_dollars: float
    reason: str


def decide_size(setup, meta: ContractMeta, risk: LiveRiskManager, confidence: float,
                min_confidence: float, n_open: int, unrealized: float = 0.0) -> SizingDecision:
    tick = meta.tick_size or 1.0
    stop_ticks = setup.risk / tick if tick else 0.0
    cap = risk.max_contracts(stop_ticks, meta.tick_value, unrealized)

    if cap <= 0:
        return SizingDecision(0, 0, confidence, 0.0, stop_ticks, 0.0,
                              f"risk cap 0 ({risk.reason_blocked or 'no room'})")
    if confidence < min_confidence:
        return SizingDecision(0, cap, confidence, 0.0, stop_ticks, 0.0,
                              f"confidence {confidence:.2f} < floor {min_confidence:.2f}")

    from ..ml.confidence import ConfidenceModel
    frac = ConfidenceModel.scale(confidence, lo=min_confidence)
    size = max(0, int(cap * frac))
    # never let a valid, confident setup round to 0 if there's room for at least 1
    if size == 0 and frac > 0 and cap >= 1:
        size = 1
    risk_dollars = size * stop_ticks * meta.tick_value
    return SizingDecision(size, cap, confidence, frac, stop_ticks, risk_dollars,
                          f"cap {cap} × scale {frac:.2f} → {size} (~${risk_dollars:.0f} risk)")
