"""Full pipeline smoke test: markets → matcher → prices → arb detector → paper exec.

Usage:
    python3 scripts/test_full_pipeline.py
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.kalshi_client import KalshiClient
from clients.polymarket_client import PolymarketClient
from core.market_matcher import MarketMatcher
from core.arb_detector import detect_all
from core.portfolio import Portfolio
from core.risk_manager import RiskManager
from core.execution_engine import ExecutionEngine
from data.models import Platform


KALSHI_SERIES = [
    "KXPRES", "KXSEN", "KXIMPEACH",
    "KXBTCD", "KXETHD", "KXBTC", "KXETH",
    "KXFEDDECISION", "KXCPI", "KXJOBS",
    "KXSPX", "KXNASDAQ",
    "KXWORLDCUP", "KXNBA", "KXWAR",
]


async def main():
    k = KalshiClient(environment="production")
    p = PolymarketClient()

    print("[1/6] Pulling markets…")
    k_markets, p_markets = await asyncio.gather(
        k.get_markets_by_series(KALSHI_SERIES, limit_per_series=50),
        p.get_markets(limit=400),
    )
    print(f"      Kalshi: {len(k_markets)}  Polymarket: {len(p_markets)}")

    print("\n[2/6] Matching cross-platform…")
    matcher = MarketMatcher(min_score=0.55, auto_match_threshold=0.85,
                            cache_path="data/cache/matches_cache.db")
    pairs = matcher.match(k_markets, p_markets)
    print(f"      {len(pairs)} candidate pairs found")
    for pp in pairs[:5]:
        print(f"      conf={pp.confidence:.2f}  {pp.kalshi_market.title[:50]}")
        print(f"                  vs {pp.polymarket_market.title[:50]}")

    if not pairs:
        print("      No pairs — try a wider set of Kalshi series or lower min_score")
        await k.close(); await p.close()
        return

    print(f"\n[3/6] Fetching live prices for top {min(10, len(pairs))} pairs…")
    top = pairs[:10]
    tasks = []
    for pp in top:
        tasks.append(k.get_price(pp.kalshi_market.market_id))
        tasks.append(p.get_price(pp.polymarket_market.market_id))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    prices = {}
    for i, pp in enumerate(top):
        kr, pr = results[2*i], results[2*i+1]
        if not isinstance(kr, Exception):
            prices[(Platform.KALSHI, pp.kalshi_market.market_id)] = kr
        if not isinstance(pr, Exception):
            prices[(Platform.POLYMARKET, pp.polymarket_market.market_id)] = pr
    print(f"      got prices for {len(prices)//2} pairs")

    print("\n[4/6] Running arb detector (showing all candidates, even negative-edge)…")
    ops = detect_all(top, prices, min_net_edge_cents=-50,
                     min_liquidity_usd=0, max_capital_usd=500)
    print(f"      {len(ops)} arb candidates")
    for op in ops[:3]:
        print(f"      {op.direction.value}  gross={op.gross_edge_cents:+.1f}c  "
              f"net={op.net_edge_cents:+.1f}c  size={op.max_size_contracts:.0f}")

    print("\n[5/6] Setting up paper portfolio + risk + execution engine…")
    portfolio = Portfolio(db_path="data/paper_trades.db", starting_capital_usd=5000)
    risk = RiskManager(min_match_confidence=0.55,
                       min_net_edge_cents=0.5, min_liquidity_usd=20)
    engine = ExecutionEngine(k, p, portfolio, risk, mode="paper")
    print(f"      Starting capital: ${portfolio.starting_capital:.2f}")
    print(f"      Current cash:     ${portfolio.cash_usd():.2f}")

    print("\n[6/6] Attempting paper execution on best opportunity…")
    if ops:
        success, reason = await engine.execute(ops[0])
        print(f"      result={success}  reason='{reason}'")
    else:
        print("      no actionable opportunity right now (markets are efficient)")

    snap = portfolio.snapshot()
    print(f"\nFinal portfolio:")
    print(f"  cash=${snap.cash_usd:.2f}  locked=${snap.locked_in_positions_usd:.2f}")
    print(f"  open_arbs={snap.open_arb_pairs}  realized=${snap.realized_pnl_usd:+.2f}")

    await k.close(); await p.close()
    print("\n✓ Full pipeline working")


if __name__ == "__main__":
    asyncio.run(main())
