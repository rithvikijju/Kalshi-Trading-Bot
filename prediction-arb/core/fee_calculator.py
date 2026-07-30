"""Platform-specific fee math.

Kalshi (2026):
- Per-contract fee on expected payout. Roughly: fee_rate × (payout - price) per contract.
- We use a conservative 3% blended rate unless we discover a per-market schedule.

Polymarket (March 2026):
- Maker: 0%   (post-only orders rest on the book)
- Taker: varies by category (see POLY_TAKER_FEES)
- Fees apply on OUTPUT (proceeds), not on entry.
"""
from __future__ import annotations
import math
from data.models import Platform, Side  # noqa


# Polymarket category → taker fee rate
POLY_TAKER_FEES: dict[str, float] = {
    "crypto":      0.0180,
    "sports":      0.0075,
    "politics":    0.0100,
    "geopolitics": 0.0000,   # free
    "finance":     0.0100,
    "economics":   0.0150,
    "culture":     0.0125,
    "weather":     0.0125,
    "tech":        0.0100,
    "other":       0.0125,
}


def kalshi_fee_per_contract(price: float, payout: float = 1.0,
                             fee_rate: float = 0.03) -> float:
    """Conservative Kalshi taker fee. Returns USD fee per contract."""
    if price is None or price <= 0 or price >= 1:
        return 0.0
    # Capped (industry convention) — never higher than 7 cents per contract
    raw = fee_rate * (payout - price)
    return max(0.0, min(raw, 0.07))


def polymarket_fee_per_contract(price: float, category: str = "other",
                                 is_maker: bool = False) -> float:
    """Polymarket fee per contract (which settles at $1)."""
    if is_maker: return 0.0  # MASSIVE advantage; prefer maker orders
    cat = (category or "other").lower()
    rate = POLY_TAKER_FEES.get(cat, POLY_TAKER_FEES["other"])
    # Fee on output: proceeds = 1.0 if it wins, fee applied on that
    return rate * 1.0   # ≈ rate per contract


def total_arb_fees_cents(kalshi_price: float, poly_price: float,
                          poly_category: str = "other",
                          poly_is_maker: bool = True) -> dict:
    """Compute total per-contract fees for a 2-leg arb position.

    Returns:
        {kalshi_fee_cents, poly_fee_cents, total_cents}
    """
    k_fee = kalshi_fee_per_contract(kalshi_price)
    p_fee = polymarket_fee_per_contract(poly_price, poly_category, is_maker=poly_is_maker)
    return {
        "kalshi_fee_cents": k_fee * 100,
        "poly_fee_cents":   p_fee * 100,
        "total_cents":      (k_fee + p_fee) * 100,
    }


def min_profitable_edge_cents(poly_category: str = "other",
                               poly_is_maker: bool = True,
                               kalshi_avg_price: float = 0.5,
                               poly_avg_price: float = 0.5) -> float:
    """Smallest gross edge (cents) that still nets >0 after fees."""
    fees = total_arb_fees_cents(kalshi_avg_price, poly_avg_price,
                                  poly_category, poly_is_maker)
    # +1 bps buffer
    return fees["total_cents"] + 0.5
