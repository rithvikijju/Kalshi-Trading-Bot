"""Per-contract Kalshi fee models for the mean-reversion strategy.

The fee floor is the single biggest threat to a directional intraday binary
(memory: kalshi_fee_floor.md). A round trip pays the trading fee TWICE (entry
and exit), so we always size the entry gate against `round_trip_fee_cents`.

Two models:
  - kalshi_pct : Kalshi's published taker fee, ceil(0.07 * P * (1-P)) per
                 contract, in cents. Worst near P=0.5 (1.75c), ->0 at the tails.
  - flat3      : the repo's conservative 3% of (payout - price), capped at 7c.
                 Matches core/fee_calculator.kalshi_fee_per_contract.
"""
from __future__ import annotations
import math


def trade_fee_cents(price: float, model: str = "kalshi_pct") -> float:
    """One-way taker fee for a single contract at `price` (0..1), in cents."""
    if price is None or price <= 0.0 or price >= 1.0:
        return 0.0
    if model == "flat3":
        raw = 0.03 * (1.0 - price)
        return max(0.0, min(raw, 0.07)) * 100.0
    # kalshi_pct (default): ceil to the cent, as Kalshi rounds fees up
    raw_dollars = 0.07 * price * (1.0 - price)
    return math.ceil(raw_dollars * 100.0)


def round_trip_fee_cents(entry_price: float, exit_price: float,
                         model: str = "kalshi_pct") -> float:
    """Fee paid buying at entry AND selling at exit (both taker)."""
    return trade_fee_cents(entry_price, model) + trade_fee_cents(exit_price, model)
