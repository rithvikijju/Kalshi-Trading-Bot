#!/usr/bin/env python3
"""Explain why the live BTC research strategy is not producing signals."""

from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import btc_1hr_research_live as live


def quote_from_market_fields(market: dict) -> live.BookQuote:
    return live.BookQuote(
        ticker=str(market.get("ticker") or ""),
        yes_bid=live.normalize_price(market.get("yes_bid_dollars")),
        yes_bid_qty=live.optional_float(market.get("yes_bid_size_fp")) or 0.0,
        yes_ask=live.normalize_price(market.get("yes_ask_dollars")),
        yes_ask_qty=live.optional_float(market.get("yes_ask_size_fp")) or 0.0,
        no_bid=live.normalize_price(market.get("no_bid_dollars")),
        no_bid_qty=live.optional_float(market.get("no_bid_size_fp")) or 0.0,
        no_ask=live.normalize_price(market.get("no_ask_dollars")),
        no_ask_qty=live.optional_float(market.get("no_ask_size_fp")) or 0.0,
    )


def quote_candidates(quote: live.BookQuote, p_yes: float) -> list[dict]:
    candidates = []
    if quote.yes_ask is not None and quote.yes_spread_cents is not None:
        candidates.append(
            {
                "side": "yes",
                "entry_price": quote.yes_ask,
                "edge": p_yes - quote.yes_ask,
                "spread": quote.yes_spread_cents,
                "available_qty": quote.yes_ask_qty,
            }
        )
    if quote.no_ask is not None and quote.no_spread_cents is not None:
        candidates.append(
            {
                "side": "no",
                "entry_price": quote.no_ask,
                "edge": (1.0 - p_yes) - quote.no_ask,
                "spread": quote.no_spread_cents,
                "available_qty": quote.no_ask_qty,
            }
        )
    return candidates


def candidate_reason(best: dict, p_yes: float, emp_cache: dict, contracts: int) -> tuple[str, float, float]:
    entry_price = float(best["entry_price"])
    entry_fee = live.kalshi_fee_dollars(entry_price, contracts=contracts, liquidity="taker")
    net_edge_cents = float(best["edge"]) * 100.0 - entry_fee * 100.0
    threshold = live.RESEARCH_MIN_EDGE_CENTS + live.edge_uncertainty_cents(p_yes, emp_cache)
    side = best["side"]
    strong_prob = (side == "yes" and p_yes >= live.RESEARCH_MIN_YES_P) or (
        side == "no" and p_yes <= live.RESEARCH_MAX_NO_P
    )
    if not strong_prob:
        return "strong_prob", net_edge_cents, threshold
    if net_edge_cents < threshold:
        return "edge", net_edge_cents, threshold
    if best["spread"] > live.RESEARCH_MAX_SPREAD_CENTS + live.FLOAT_EPSILON:
        return "spread", net_edge_cents, threshold
    if entry_price < live.RESEARCH_MIN_ENTRY - live.FLOAT_EPSILON or entry_price > live.RESEARCH_MAX_ENTRY + live.FLOAT_EPSILON:
        return "entry_range", net_edge_cents, threshold
    if best["available_qty"] < contracts:
        return "qty", net_edge_cents, threshold
    return "pass", net_edge_cents, threshold


def main() -> int:
    data_client = live.KalshiApi(env="prod", require_auth=True)
    btc_1m = live.load_btc_history_cached(live.RESEARCH_BOOTSTRAP_BTC_DAYS)
    btc_1m = live.refresh_btc_data_cached(btc_1m)
    events = live.scan_research_events(data_client)[:1]
    print(f"events={len(events)}")
    if not events:
        return 0

    event = events[0]
    print(
        f"event={event['event_ticker']} ttl_min={event['ttl_hours'] * 60:.2f} "
        f"close={live.utc_dt(event['close_time']).isoformat()} markets={len(event['markets'])}"
    )
    emp_cache = live.build_event_emp_cache(btc_1m, event, verbose=True)
    print("emp_cache=" + ",".join(f"{h}:{v.get('n', 0)}" for h, v in sorted(emp_cache.items())))
    spot = live.base_strategy.get_btc_spot()
    print(f"spot={spot:.2f}")

    markets = [m for m in event["markets"] if m.get("ticker")]
    books = data_client.get_orderbooks([m["ticker"] for m in markets], depth=1)
    counts: Counter[str] = Counter()
    market_quote_counts: Counter[str] = Counter()
    last_price_counts: Counter[str] = Counter()
    rows = []
    market_quote_rows = []
    last_price_rows = []
    both_side_rows = []
    sample_market_keys = sorted(markets[0].keys()) if markets else []
    print("sample_market_keys=" + ",".join(sample_market_keys[:80]))

    for market in markets:
        ticker = market["ticker"]
        payload = books.get(ticker)
        if not payload:
            counts["missing_book"] += 1
            continue
        quote = live.quote_from_orderbook(ticker, payload)
        parsed = live.base_strategy.parse_market(market)
        p_yes, ttl_min = live.model_probability(event, market, btc_1m, emp_cache, spot)
        if not math.isfinite(p_yes):
            counts["nonfinite_p"] += 1
            continue

        candidates = quote_candidates(quote, p_yes)
        if not candidates:
            counts["no_two_sided_quote"] += 1
        else:
            best = max(candidates, key=lambda item: item["edge"])
            reason, net_edge, threshold = candidate_reason(best, p_yes, emp_cache, live.RESEARCH_SIGNAL_CONTRACTS)
            counts[reason] += 1
            rows.append(
                {
                    "ticker": ticker,
                    "strike": live.optional_float(parsed.get("floor")),
                    "side": best["side"],
                    "entry": float(best["entry_price"]),
                    "p_yes": p_yes,
                    "net_edge": net_edge,
                    "threshold": threshold,
                    "spread": float(best["spread"]),
                    "qty": float(best["available_qty"]),
                    "reason": reason,
                    "yes_bid": quote.yes_bid,
                    "yes_ask": quote.yes_ask,
                    "no_bid": quote.no_bid,
                    "no_ask": quote.no_ask,
                }
            )
            for cand in candidates:
                r2, ne2, th2 = candidate_reason(cand, p_yes, emp_cache, live.RESEARCH_SIGNAL_CONTRACTS)
                both_side_rows.append(
                    {
                        "ticker": ticker,
                        "side": cand["side"],
                        "entry": float(cand["entry_price"]),
                        "p_yes": p_yes,
                        "net_edge": ne2,
                        "threshold": th2,
                        "spread": float(cand["spread"]),
                        "reason": r2,
                    }
                )

        market_quote = quote_from_market_fields(market)
        market_candidates = quote_candidates(market_quote, p_yes)
        if not market_candidates:
            market_quote_counts["no_two_sided_quote"] += 1
        else:
            market_best = max(market_candidates, key=lambda item: item["edge"])
            market_reason, market_net_edge, market_threshold = candidate_reason(
                market_best,
                p_yes,
                emp_cache,
                live.RESEARCH_SIGNAL_CONTRACTS,
            )
            market_quote_counts[market_reason] += 1
            market_quote_rows.append(
                {
                    "ticker": ticker,
                    "strike": live.optional_float(parsed.get("floor")),
                    "side": market_best["side"],
                    "entry": float(market_best["entry_price"]),
                    "p_yes": p_yes,
                    "net_edge": market_net_edge,
                    "threshold": market_threshold,
                    "spread": float(market_best["spread"]),
                    "qty": float(market_best["available_qty"]),
                    "reason": market_reason,
                    "yes_bid": market_quote.yes_bid,
                    "yes_ask": market_quote.yes_ask,
                    "no_bid": market_quote.no_bid,
                    "no_ask": market_quote.no_ask,
                }
            )

        last_price = live.normalize_price(market.get("last_price_dollars"))
        if last_price is None:
            last_price_counts["missing_last_price"] += 1
        else:
            half_spread = live.RESEARCH_MAX_SPREAD_CENTS / 200.0
            synthetic_candidates = [
                {
                    "side": "yes",
                    "entry_price": min(1.0, last_price + half_spread),
                    "edge": p_yes - min(1.0, last_price + half_spread),
                    "spread": live.RESEARCH_MAX_SPREAD_CENTS,
                    "available_qty": 1_000_000.0,
                },
                {
                    "side": "no",
                    "entry_price": min(1.0, 1.0 - last_price + half_spread),
                    "edge": (1.0 - p_yes) - min(1.0, 1.0 - last_price + half_spread),
                    "spread": live.RESEARCH_MAX_SPREAD_CENTS,
                    "available_qty": 1_000_000.0,
                },
            ]
            synthetic_best = max(synthetic_candidates, key=lambda item: item["edge"])
            synthetic_reason, synthetic_net_edge, synthetic_threshold = candidate_reason(
                synthetic_best,
                p_yes,
                emp_cache,
                live.RESEARCH_SIGNAL_CONTRACTS,
            )
            last_price_counts[synthetic_reason] += 1
            last_price_rows.append(
                {
                    "ticker": ticker,
                    "side": synthetic_best["side"],
                    "last_price": last_price,
                    "entry": float(synthetic_best["entry_price"]),
                    "p_yes": p_yes,
                    "net_edge": synthetic_net_edge,
                    "threshold": synthetic_threshold,
                    "spread": float(synthetic_best["spread"]),
                    "reason": synthetic_reason,
                }
            )

    print("best_side_rejection_counts=" + ",".join(f"{k}:{v}" for k, v in counts.most_common()))
    print("market_quote_rejection_counts=" + ",".join(f"{k}:{v}" for k, v in market_quote_counts.most_common()))
    print("last_price_synthetic_rejection_counts=" + ",".join(f"{k}:{v}" for k, v in last_price_counts.most_common()))
    print("\ntop_by_net_edge:")
    for row in sorted(rows, key=lambda r: r["net_edge"], reverse=True)[:12]:
        print(
            f"{row['ticker']} {row['side'].upper()} strike={row['strike']} entry={row['entry']:.4f} "
            f"p_yes={row['p_yes']:.4f} net={row['net_edge']:.2f}c thresh={row['threshold']:.2f}c "
            f"spread={row['spread']:.2f} qty={row['qty']:.0f} reason={row['reason']} "
            f"book=yes {row['yes_bid']}/{row['yes_ask']} no {row['no_bid']}/{row['no_ask']}"
        )
    print("\ntop_by_edge_minus_threshold:")
    for row in sorted(rows, key=lambda r: r["net_edge"] - r["threshold"], reverse=True)[:12]:
        print(
            f"{row['ticker']} {row['side'].upper()} entry={row['entry']:.4f} p_yes={row['p_yes']:.4f} "
            f"margin={row['net_edge'] - row['threshold']:.2f}c net={row['net_edge']:.2f}c "
            f"thresh={row['threshold']:.2f}c spread={row['spread']:.2f} reason={row['reason']}"
        )
    print("\npasses_if_both_sides_independent=" + str(sum(1 for r in both_side_rows if r["reason"] == "pass")))
    print("\ntop_market_quote_by_edge_minus_threshold:")
    for row in sorted(market_quote_rows, key=lambda r: r["net_edge"] - r["threshold"], reverse=True)[:12]:
        print(
            f"{row['ticker']} {row['side'].upper()} entry={row['entry']:.4f} p_yes={row['p_yes']:.4f} "
            f"margin={row['net_edge'] - row['threshold']:.2f}c net={row['net_edge']:.2f}c "
            f"thresh={row['threshold']:.2f}c spread={row['spread']:.2f} reason={row['reason']} "
            f"market=yes {row['yes_bid']}/{row['yes_ask']} no {row['no_bid']}/{row['no_ask']}"
        )
    print("\ntop_last_price_synthetic_by_edge_minus_threshold:")
    for row in sorted(last_price_rows, key=lambda r: r["net_edge"] - r["threshold"], reverse=True)[:12]:
        print(
            f"{row['ticker']} {row['side'].upper()} last={row['last_price']:.4f} entry={row['entry']:.4f} "
            f"p_yes={row['p_yes']:.4f} margin={row['net_edge'] - row['threshold']:.2f}c "
            f"net={row['net_edge']:.2f}c thresh={row['threshold']:.2f}c reason={row['reason']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
