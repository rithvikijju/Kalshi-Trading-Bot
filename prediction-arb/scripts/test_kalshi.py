"""Run this first to verify Kalshi auth works.

Usage from prediction-arb/ directory:
    python3 scripts/test_kalshi.py
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.kalshi_client import KalshiClient


async def main():
    k = KalshiClient(environment="production")
    print(f"Authed:  {k._authed}")
    print(f"Key ID:  {'set' if k.api_key_id else 'MISSING'}")
    if not k._authed:
        print("\n!! Kalshi credentials not found.")
        print("   Expected one of:")
        print("   - ~/.kalshi/credentials.env with KALSHI_PROD_KEY_ID + KALSHI_PROD_PRIVATE_KEY_PATH")
        print("   - prediction-arb/.env with KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH")
        await k.close()
        return

    balance = await k.get_balance_usd()
    print(f"Balance: ${balance:.2f}")

    markets = await k.get_markets(limit=3)
    print(f"\nSample markets ({len(markets)}):")
    for m in markets:
        print(f"  [{m.market_id[:32]}] {m.title[:60]}")

    print("\n✓ Kalshi client working")
    await k.close()


if __name__ == "__main__":
    asyncio.run(main())
