#!/usr/bin/env python3
"""Risk-adjusted sizing overlay for the BTC 1-hour research strategy.

This module does not change the research signal. It only changes how many
contracts a passed signal is allowed to buy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from config.btc_1hr_config import CFG, kalshi_fee_dollars


@dataclass(frozen=True)
class RiskSizingConfig:
    starting_bankroll: float = 20.0
    max_contracts: int = 3
    max_per_market_fraction: float = float(CFG.get("max_per_market", 0.20))
    max_total_exposure_fraction: float = float(CFG.get("max_total_risk", 0.50))
    kelly_fraction: float = 0.25
    edge_confidence: float = 0.50
    medium_entry_cap: float = 0.55
    high_entry_cap: float = 0.65
    no_side_contract_cap: int = 3
    scale_min_edge_cents: float | None = None
    scale_side: str | None = None
    base_max_contracts: int | None = None
    base_medium_entry_cap: float | None = None
    base_high_entry_cap: float | None = None
    base_no_side_contract_cap: int | None = None
    no_side_near_distance_usd: float | None = None
    no_side_near_distance_contract_cap: int | None = None
    no_side_low_prob_threshold: float | None = None
    no_side_low_prob_contract_cap: int | None = None


@dataclass(frozen=True)
class SizingDecision:
    contracts: int
    fee: float
    cost: float
    side_probability: float
    conservative_probability: float
    cost_per_contract: float
    full_kelly_fraction: float
    applied_kelly_fraction: float
    risk_budget: float
    price_band_cap: int
    budget: float
    reason: str


def _finite_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def side_probability(model_p_yes: float, side: str) -> float:
    p_yes = min(1.0, max(0.0, _finite_float(model_p_yes, 0.5)))
    return p_yes if str(side).lower() == "yes" else 1.0 - p_yes


def price_band_contract_cap(entry_price: float, config: RiskSizingConfig) -> int:
    """Prevent favorite-priced contracts from mechanically scaling to 3x."""
    if entry_price >= config.high_entry_cap:
        return 1
    if entry_price >= config.medium_entry_cap:
        return min(2, config.max_contracts)
    return config.max_contracts


def cost_for_contracts(entry_price: float, contracts: int, liquidity: str = "taker") -> tuple[float, float]:
    fee = kalshi_fee_dollars(entry_price, contracts=contracts, liquidity=liquidity)
    return entry_price * contracts + fee, fee


def conservative_probability(
    p_side: float,
    cost_per_contract: float,
    edge_confidence: float,
) -> float:
    """Shrink estimated edge toward breakeven for sizing only."""
    if p_side <= cost_per_contract:
        return p_side
    confidence = min(1.0, max(0.0, edge_confidence))
    return cost_per_contract + confidence * (p_side - cost_per_contract)


def kelly_risk_fraction(p_side: float, cost_per_contract: float) -> float:
    """Kelly fraction of bankroll to risk for a binary $1 payout contract."""
    if cost_per_contract <= 0.0 or cost_per_contract >= 1.0:
        return 0.0
    return max(0.0, (p_side - cost_per_contract) / (1.0 - cost_per_contract))


def effective_risk_config(config: RiskSizingConfig, side: str, net_edge_cents: float | None = None) -> RiskSizingConfig:
    """Apply edge/side gating for scale-up configs while preserving current base sizing."""
    if config.scale_min_edge_cents is None and not config.scale_side:
        return config

    edge = _finite_float(net_edge_cents, 0.0)
    passes_edge = config.scale_min_edge_cents is None or edge >= float(config.scale_min_edge_cents)
    passes_side = not config.scale_side or str(side).lower() == str(config.scale_side).lower()
    if passes_edge and passes_side:
        return config

    return replace(
        config,
        max_contracts=config.base_max_contracts if config.base_max_contracts is not None else min(config.max_contracts, 3),
        medium_entry_cap=config.base_medium_entry_cap if config.base_medium_entry_cap is not None else 0.55,
        high_entry_cap=config.base_high_entry_cap if config.base_high_entry_cap is not None else 0.65,
        no_side_contract_cap=config.base_no_side_contract_cap if config.base_no_side_contract_cap is not None else 3,
    )


def choose_risk_adjusted_contracts(
    *,
    entry_price: float,
    model_p_yes: float,
    side: str,
    bankroll: float,
    available_cash: float,
    active_exposure: float,
    config: RiskSizingConfig,
    available_qty: float | None = None,
    liquidity: str = "taker",
    net_edge_cents: float | None = None,
    side_distance_usd: float | None = None,
) -> SizingDecision:
    config = effective_risk_config(config, side, net_edge_cents)
    entry_price = min(1.0, max(0.0, _finite_float(entry_price, 0.0)))
    bankroll = max(0.0, _finite_float(bankroll, 0.0))
    available_cash = max(0.0, _finite_float(available_cash, 0.0))
    active_exposure = max(0.0, _finite_float(active_exposure, 0.0))
    side_name = str(side).lower()
    p_side = side_probability(model_p_yes, side_name)
    max_contracts = max(0, int(config.max_contracts))
    if available_qty is not None and math.isfinite(float(available_qty)):
        max_contracts = min(max_contracts, max(0, int(math.floor(float(available_qty)))))
    conditional_no_cap_applied = False
    if side_name == "no":
        max_contracts = min(max_contracts, max(0, int(config.no_side_contract_cap)))
        if config.no_side_low_prob_threshold is not None and p_side < float(config.no_side_low_prob_threshold):
            cap = max(0, int(config.no_side_low_prob_contract_cap or 0))
            if cap < max_contracts:
                conditional_no_cap_applied = True
            max_contracts = min(max_contracts, cap)
        distance = _finite_float(side_distance_usd, float("nan"))
        if (
            config.no_side_near_distance_usd is not None
            and math.isfinite(distance)
            and distance < float(config.no_side_near_distance_usd)
        ):
            cap = max(0, int(config.no_side_near_distance_contract_cap or 0))
            if cap < max_contracts:
                conditional_no_cap_applied = True
            max_contracts = min(max_contracts, cap)
    price_cap = price_band_contract_cap(entry_price, config)
    max_contracts = min(max_contracts, price_cap)
    if max_contracts <= 0:
        return SizingDecision(0, 0.0, 0.0, 0.0, 0.0, entry_price, 0.0, 0.0, 0.0, price_cap, 0.0, "no_contract_capacity")

    one_cost, one_fee = cost_for_contracts(entry_price, 1, liquidity=liquidity)
    cost_per_contract = one_cost
    p_conservative = conservative_probability(p_side, cost_per_contract, config.edge_confidence)
    full_kelly = kelly_risk_fraction(p_conservative, cost_per_contract)
    applied_kelly = min(1.0, max(0.0, config.kelly_fraction)) * full_kelly
    kelly_budget = bankroll * applied_kelly
    per_market_budget = bankroll * max(0.0, config.max_per_market_fraction)
    total_budget = bankroll * max(0.0, config.max_total_exposure_fraction) - active_exposure
    budget = min(available_cash, per_market_budget, total_budget, kelly_budget)

    if one_cost > min(available_cash, per_market_budget, total_budget):
        return SizingDecision(0, one_fee, one_cost, p_side, p_conservative, cost_per_contract, full_kelly, applied_kelly, kelly_budget, price_cap, budget, "bankroll_budget")
    if budget < one_cost:
        return SizingDecision(0, one_fee, one_cost, p_side, p_conservative, cost_per_contract, full_kelly, applied_kelly, kelly_budget, price_cap, budget, "kelly_budget")

    chosen = 0
    chosen_cost = 0.0
    chosen_fee = 0.0
    for contracts in range(1, max_contracts + 1):
        cost, fee = cost_for_contracts(entry_price, contracts, liquidity=liquidity)
        if cost <= budget:
            chosen = contracts
            chosen_cost = cost
            chosen_fee = fee
        else:
            break

    reason = "risk_adjusted"
    if chosen <= 0:
        reason = "budget"
    elif chosen < max_contracts:
        reason = "risk_budget_cap"
    elif conditional_no_cap_applied:
        reason = "conditional_no_cap"
    elif chosen == price_cap and price_cap < config.max_contracts:
        reason = "price_band_cap"
    return SizingDecision(
        contracts=chosen,
        fee=chosen_fee,
        cost=chosen_cost,
        side_probability=p_side,
        conservative_probability=p_conservative,
        cost_per_contract=cost_per_contract,
        full_kelly_fraction=full_kelly,
        applied_kelly_fraction=applied_kelly,
        risk_budget=kelly_budget,
        price_band_cap=price_cap,
        budget=budget,
        reason=reason,
    )


def choose_flat_contracts(
    *,
    entry_price: float,
    bankroll: float,
    available_cash: float,
    active_exposure: float,
    max_contracts: int,
    max_per_market_fraction: float,
    max_total_exposure_fraction: float,
    available_qty: float | None = None,
    liquidity: str = "taker",
) -> SizingDecision:
    entry_price = min(1.0, max(0.0, _finite_float(entry_price, 0.0)))
    bankroll = max(0.0, _finite_float(bankroll, 0.0))
    available_cash = max(0.0, _finite_float(available_cash, 0.0))
    active_exposure = max(0.0, _finite_float(active_exposure, 0.0))
    capacity = max(0, int(max_contracts))
    if available_qty is not None and math.isfinite(float(available_qty)):
        capacity = min(capacity, max(0, int(math.floor(float(available_qty)))))
    budget = min(
        available_cash,
        bankroll * max(0.0, max_per_market_fraction),
        bankroll * max(0.0, max_total_exposure_fraction) - active_exposure,
    )
    chosen = 0
    chosen_cost = 0.0
    chosen_fee = 0.0
    for contracts in range(1, capacity + 1):
        cost, fee = cost_for_contracts(entry_price, contracts, liquidity=liquidity)
        if cost <= budget:
            chosen = contracts
            chosen_cost = cost
            chosen_fee = fee
        else:
            break
    one_cost, _ = cost_for_contracts(entry_price, 1, liquidity=liquidity)
    return SizingDecision(
        contracts=chosen,
        fee=chosen_fee,
        cost=chosen_cost,
        side_probability=0.0,
        conservative_probability=0.0,
        cost_per_contract=one_cost,
        full_kelly_fraction=0.0,
        applied_kelly_fraction=0.0,
        risk_budget=budget,
        price_band_cap=capacity,
        budget=budget,
        reason="flat_max" if chosen else "budget",
    )


def premium_deployed(row: pd.Series | dict[str, Any]) -> float:
    return _finite_float(row.get("entry_price", 0.0), 0.0) * _finite_float(row.get("contracts", 1), 1.0) + _finite_float(row.get("entry_fee", 0.0), 0.0)


def unit_payout_from_trade(row: pd.Series | dict[str, Any]) -> float:
    contracts = max(1.0, _finite_float(row.get("contracts", 1), 1.0))
    if "payout" in row and pd.notna(row.get("payout")):
        return 1.0 if _finite_float(row.get("payout"), 0.0) / contracts > 0.5 else 0.0
    if "pnl" in row and pd.notna(row.get("pnl")):
        return 1.0 if _finite_float(row.get("pnl"), 0.0) > 0.0 else 0.0
    return 0.0


def compute_sized_stats(trades: pd.DataFrame, starting_bankroll: float, skipped: dict[str, int] | None = None) -> dict[str, Any]:
    skipped = skipped or {}
    if trades.empty:
        return {
            "trades": 0,
            "contracts": 0,
            "starting_bankroll": float(starting_bankroll),
            "ending_bankroll": float(starting_bankroll),
            "total_pnl": 0.0,
            "return_on_start": 0.0,
            "premium_deployed": 0.0,
            "return_on_premium": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_contracts": 0.0,
            "max_contracts": 0,
            "avg_entry_price": 0.0,
            "skipped_budget": int(skipped.get("budget", 0)),
            "skipped_event_lock": int(skipped.get("event_lock", 0)),
        }

    ordered = trades.sort_values(["settle_time", "entry_time", "market_ticker"]).reset_index(drop=True)
    pnl = ordered["pnl"].astype(float)
    premium = ordered["premium_deployed"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    equity = starting_bankroll + pnl.cumsum()
    drawdown = equity - equity.cummax()
    drawdown_pct = drawdown / equity.cummax().replace(0, pd.NA)
    ending = starting_bankroll + float(pnl.sum())
    return {
        "trades": int(len(ordered)),
        "contracts": int(ordered["contracts"].sum()),
        "starting_bankroll": float(starting_bankroll),
        "ending_bankroll": float(ending),
        "total_pnl": float(pnl.sum()),
        "return_on_start": float(ending / starting_bankroll - 1.0) if starting_bankroll else 0.0,
        "premium_deployed": float(premium.sum()),
        "return_on_premium": float(pnl.sum() / premium.sum()) if premium.sum() else 0.0,
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else (math.inf if len(wins) else 0.0),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "max_drawdown_pct": float(drawdown_pct.min()) if len(drawdown_pct.dropna()) else 0.0,
        "avg_contracts": float(ordered["contracts"].mean()),
        "max_contracts": int(ordered["contracts"].max()),
        "avg_entry_price": float(ordered["entry_price"].mean()),
        "yes_trades": int((ordered["side"].str.lower() == "yes").sum()) if "side" in ordered else 0,
        "no_trades": int((ordered["side"].str.lower() == "no").sum()) if "side" in ordered else 0,
        "skipped_budget": int(skipped.get("budget", 0)),
        "skipped_event_lock": int(skipped.get("event_lock", 0)),
    }
