"""Cross-platform arb detector.

For a pair (Kalshi, Polymarket):
  buy YES on side A + buy NO on side B  → guaranteed $1 payout per contract.
  Edge per contract = 1 - (price_A_yes + price_B_no)  (minus fees)

Two directions to check:
  1. yes_kalshi + no_poly  < 1 - total_fees
  2. yes_poly  + no_kalshi < 1 - total_fees

Score by net edge × size (capital-efficient annualized return).
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import (MatchedMarketPair, ArbOpportunity, ArbDirection, Side,
                          NormalizedPrice, OrderBook, Platform)
from core.fee_calculator import kalshi_fee_per_contract, polymarket_fee_per_contract
from utils.logger import setup_logger

log = setup_logger("arb")


def _walk_book(asks: list, size_needed: float, max_slippage_pct: float = 0.02,
               start_price: float | None = None) -> tuple[float, float]:
    """Walk an asks list to estimate average fill price and max fillable size.

    Returns (avg_price, total_size_filled)."""
    if not asks: return 0.0, 0.0
    start = start_price or asks[0].price
    walk_cap = start * (1 + max_slippage_pct)
    total_size, total_cost = 0.0, 0.0
    for lvl in asks:
        if lvl.price > walk_cap: break
        take = min(lvl.size, size_needed - total_size)
        if take <= 0: break
        total_size += take
        total_cost += take * lvl.price
        if total_size >= size_needed: break
    avg = total_cost / total_size if total_size > 0 else 0.0
    return avg, total_size


def detect_arb(pair: MatchedMarketPair,
               kalshi_price: NormalizedPrice,
               poly_price: NormalizedPrice,
               kalshi_yes_book: OrderBook | None = None,
               poly_yes_book: OrderBook | None = None,
               kalshi_no_book: OrderBook | None = None,
               poly_no_book: OrderBook | None = None,
               poly_is_maker: bool = True,
               min_net_edge_cents: float = 2.0,
               min_liquidity_usd: float = 100.0,
               max_slippage_pct: float = 0.02,
               max_capital_usd: float = 500.0) -> ArbOpportunity | None:
    """Returns the best (most-profitable) arb opportunity for this pair, or None."""
    poly_cat = pair.polymarket_market.category

    candidates: list[ArbOpportunity] = []

    # Direction 1: BUY YES on Kalshi (at yes_ask) + BUY NO on Poly (at no_ask)
    k_yes = kalshi_price.yes_ask
    p_no  = poly_price.no_ask
    if k_yes is not None and p_no is not None:
        gross_cents = 100 * (1.0 - k_yes - p_no)
        k_fee = kalshi_fee_per_contract(k_yes) * 100
        p_fee = polymarket_fee_per_contract(p_no, poly_cat, is_maker=poly_is_maker) * 100
        net_cents = gross_cents - k_fee - p_fee
        if net_cents >= min_net_edge_cents:
            # Liquidity / capacity
            max_size = min(
                kalshi_price.yes_ask_size or 0,
                poly_price.no_ask_size or 0,
            )
            # Walk books if available
            if kalshi_yes_book and poly_no_book:
                _, kalshi_walkable = _walk_book(kalshi_yes_book.asks, max_size or 1000, max_slippage_pct)
                _, poly_walkable = _walk_book(poly_no_book.asks, max_size or 1000, max_slippage_pct)
                max_size = min(max_size or 1000, kalshi_walkable, poly_walkable)
            capital_usd = (k_yes + p_no) * max_size if max_size > 0 else 0
            if capital_usd >= min_liquidity_usd and capital_usd <= max_capital_usd:
                size_capped = max_size
                # Or cap by max_capital_usd if exceeded
                if (k_yes + p_no) * max_size > max_capital_usd:
                    size_capped = max_capital_usd / (k_yes + p_no)
                payout = size_capped * 1.0
                ann = _annualized(net_cents / 100 * size_capped, capital_usd, pair)
                candidates.append(ArbOpportunity(
                    pair=pair,
                    direction=ArbDirection.YES_KALSHI_NO_POLY,
                    kalshi_price=k_yes, polymarket_price=p_no,
                    kalshi_side=Side.YES, polymarket_side=Side.NO,
                    gross_edge_cents=gross_cents,
                    kalshi_fee_cents=k_fee, polymarket_fee_cents=p_fee,
                    net_edge_cents=net_cents,
                    max_size_contracts=size_capped,
                    capital_required_usd=capital_usd,
                    estimated_payout_usd=payout,
                    annualized_return_pct=ann,
                ))

    # Direction 2: BUY YES on Poly + BUY NO on Kalshi
    p_yes = poly_price.yes_ask
    k_no  = kalshi_price.no_ask
    if p_yes is not None and k_no is not None:
        gross_cents = 100 * (1.0 - p_yes - k_no)
        p_fee = polymarket_fee_per_contract(p_yes, poly_cat, is_maker=poly_is_maker) * 100
        k_fee = kalshi_fee_per_contract(k_no) * 100
        net_cents = gross_cents - k_fee - p_fee
        if net_cents >= min_net_edge_cents:
            max_size = min(
                poly_price.yes_ask_size or 0,
                kalshi_price.no_ask_size or 0,
            )
            if poly_yes_book and kalshi_no_book:
                _, poly_walkable = _walk_book(poly_yes_book.asks, max_size or 1000, max_slippage_pct)
                _, k_walkable = _walk_book(kalshi_no_book.asks, max_size or 1000, max_slippage_pct)
                max_size = min(max_size or 1000, poly_walkable, k_walkable)
            capital_usd = (p_yes + k_no) * max_size if max_size > 0 else 0
            if capital_usd >= min_liquidity_usd and capital_usd <= max_capital_usd:
                size_capped = max_size
                if (p_yes + k_no) * max_size > max_capital_usd:
                    size_capped = max_capital_usd / (p_yes + k_no)
                payout = size_capped * 1.0
                ann = _annualized(net_cents / 100 * size_capped, capital_usd, pair)
                candidates.append(ArbOpportunity(
                    pair=pair,
                    direction=ArbDirection.YES_POLY_NO_KALSHI,
                    kalshi_price=k_no, polymarket_price=p_yes,
                    kalshi_side=Side.NO, polymarket_side=Side.YES,
                    gross_edge_cents=gross_cents,
                    kalshi_fee_cents=k_fee, polymarket_fee_cents=p_fee,
                    net_edge_cents=net_cents,
                    max_size_contracts=size_capped,
                    capital_required_usd=capital_usd,
                    estimated_payout_usd=payout,
                    annualized_return_pct=ann,
                ))

    if not candidates: return None
    candidates.sort(key=lambda c: -c.net_edge_cents * c.max_size_contracts)
    return candidates[0]


def _annualized(profit_usd: float, capital_usd: float,
                pair: MatchedMarketPair) -> float | None:
    """Annualized return given resolution date."""
    if capital_usd <= 0: return None
    res = pair.kalshi_market.resolution_date or pair.polymarket_market.resolution_date
    if not res: return None
    days = max((res - datetime.now(timezone.utc)).total_seconds() / 86400, 0.5)
    return (profit_usd / capital_usd) * (365 / days) * 100


def detect_all(pairs: list[MatchedMarketPair],
               prices: dict[tuple[Platform, str], NormalizedPrice],
               **kwargs) -> list[ArbOpportunity]:
    """Run detect_arb across many pairs, return only positive opportunities."""
    out: list[ArbOpportunity] = []
    for pair in pairs:
        kp = prices.get((Platform.KALSHI, pair.kalshi_market.market_id))
        pp = prices.get((Platform.POLYMARKET, pair.polymarket_market.market_id))
        if kp is None or pp is None: continue
        op = detect_arb(pair, kp, pp, **kwargs)
        if op: out.append(op)
    out.sort(key=lambda o: -o.net_edge_cents)
    return out
