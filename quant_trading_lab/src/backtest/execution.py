"""Order routing inside the backtest. Computes the trades needed to move
positions toward target weights, applies costs, and books fills.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd
from .portfolio import Portfolio
from .costs import CostModel


@dataclass
class Fill:
    ts: pd.Timestamp
    symbol: str
    qty: float
    price: float
    fee: float
    slippage_bps: float


def rebalance_to_targets(
    ts: pd.Timestamp,
    portfolio: Portfolio,
    target_weights: dict[str, float],
    exec_prices: dict[str, float],
    costs: CostModel,
    asset_class_by_symbol: Optional[dict[str, str]] = None,
) -> list[Fill]:
    """Move portfolio to target weights using execution prices at this bar.

    target_weights: weight of portfolio equity per symbol. e.g. {"SPY": 0.5}
    exec_prices: price at which orders fill (usually next-bar open).
    """
    asset_class_by_symbol = asset_class_by_symbol or {}
    equity = portfolio.equity(exec_prices)
    fills = []
    symbols = set(target_weights.keys()) | set(portfolio.positions.keys())
    for s in symbols:
        px = exec_prices.get(s)
        if px is None or px <= 0:
            continue
        tgt_w = target_weights.get(s, 0.0)
        tgt_qty = (tgt_w * equity) / px
        cur_qty = portfolio.positions.get(s, type("X", (), {"qty": 0.0})()).qty
        dq = tgt_qty - cur_qty
        if abs(dq * px) < 1.0:        # don't rebalance < $1 of notional
            continue
        notional = dq * px
        ac = asset_class_by_symbol.get(s, "equity")
        if ac == "crypto_spot":
            fee = costs.crypto_spot_trade_cost(notional)
        elif ac == "crypto_perp":
            fee = costs.crypto_perp_trade_cost(notional)
        else:
            fee = costs.equity_trade_cost(notional)
        portfolio.apply_fill(s, dq, px, fee, asset_class=ac)
        fills.append(Fill(ts=ts, symbol=s, qty=dq, price=px, fee=fee,
                          slippage_bps=costs.slippage_bps))
    return fills
