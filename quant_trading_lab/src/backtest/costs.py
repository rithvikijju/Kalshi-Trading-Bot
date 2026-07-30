"""Transaction cost model. Conservative defaults; override per strategy.

Cost components:
  - commission: per-share or bp-of-notional (broker dependent)
  - slippage: bp-of-notional (market impact)
  - spread: half-spread paid when crossing the book
  - funding: paid/received hourly or 8h on perp positions
  - borrow: paid on shorts (equities)
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CostModel:
    commission_bps: float = 0.0       # eg Alpaca commission-free for stocks
    slippage_bps: float = 1.0         # 1bp default for liquid ETFs
    half_spread_bps: float = 0.5      # cross the book
    short_borrow_apy: float = 0.005   # 50bp / yr for typical ETFs
    perp_taker_bps: float = 4.0       # 4bp typical
    perp_maker_bps: float = -1.0      # rebate on some venues
    spot_taker_bps: float = 10.0      # 10bp typical crypto spot taker

    def equity_trade_cost(self, notional: float) -> float:
        """Cost in $ for trading `notional` (absolute) of an equity."""
        return abs(notional) * (self.commission_bps + self.slippage_bps + self.half_spread_bps) / 10_000

    def crypto_spot_trade_cost(self, notional: float) -> float:
        return abs(notional) * (self.spot_taker_bps + self.slippage_bps) / 10_000

    def crypto_perp_trade_cost(self, notional: float, taker: bool = True) -> float:
        bps = self.perp_taker_bps if taker else self.perp_maker_bps
        return abs(notional) * (bps + self.slippage_bps) / 10_000

    def daily_borrow_cost(self, short_notional: float) -> float:
        return abs(short_notional) * self.short_borrow_apy / 252
