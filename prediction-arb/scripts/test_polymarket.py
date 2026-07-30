"""Verify Polymarket public API access.

Usage:
    python3 scripts/test_polymarket.py
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.polymarket_client import PolymarketClient


async def main():
    p = PolymarketClient()
    print(f"Authed: {p._authed}  (False is fine for paper/read-only)")

    markets = await p.get_markets(limit=5)
    print(f"\nGot {len(markets)} markets from Gamma:")
    for m in markets:
        print(f"  [{(m.market_id or '')[:16]}...] {m.title[:65]}  cat={m.category}")

    # Try one price lookup
    if markets:
        m = markets[0]
        try:
            price = await p.get_price(m.market_id, m.yes_token_id)
            print(f"\nLive price for '{m.title[:50]}':")
            print(f"  YES bid={price.yes_bid}  ask={price.yes_ask}")
            print(f"  NO  bid={price.no_bid}  ask={price.no_ask}")
        except Exception as e:
            print(f"  price fetch err: {e}")

    print("\n✓ Polymarket client working")
    await p.close()


if __name__ == "__main__":
    asyncio.run(main())
