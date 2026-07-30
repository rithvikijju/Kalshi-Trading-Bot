"""Entry point: capture BTC short-horizon ticks from Kalshi + Polymarket.

Run:
    python -m tick_capture.main --root /Users/rithvikijju/edge-bot/tick_data

Writes parquet shards to <root>/{venue}/{msg_type}/dt=YYYY-MM-DD/hr=HH/part.parquet
and refreshes the DuckDB view file <root>/ticks.duckdb on a 60s cadence.
"""
from __future__ import annotations
import argparse, asyncio, signal, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tick_capture.writer import ParquetWriter
from tick_capture.discover import (discover_kalshi_btc_markets,
                                   discover_polymarket_btc_5m)
from tick_capture.kalshi import KalshiListener
from tick_capture.polymarket import PolymarketListener
from tick_capture import views


# ──── Discovery threads ──────────────────────────────────────────────────
class UniverseCache:
    def __init__(self):
        self.kalshi: list = []
        self.polymarket: list = []
        self.last_kalshi = 0.0
        self.last_poly = 0.0


async def discovery_loop(cache: UniverseCache):
    """Re-discovers BTC markets every 30s. Logs whenever the set changes."""
    while True:
        # Run blocking REST calls in a thread to keep the loop free.
        try:
            ks = await asyncio.to_thread(discover_kalshi_btc_markets)
            tickers = [m["ticker"] for m in ks]
            if set(tickers) != set(m["ticker"] for m in cache.kalshi):
                print(f"[discover] kalshi universe → {len(tickers)} tickers: "
                      f"{tickers[:5]}{'…' if len(tickers)>5 else ''}", flush=True)
            cache.kalshi = ks
            cache.last_kalshi = time.time()
        except Exception as e:
            print(f"[discover] kalshi err: {e}", flush=True)
        try:
            pm = await asyncio.to_thread(discover_polymarket_btc_5m)
            slugs = [m["slug"] for m in pm]
            if set(slugs) != set(m["slug"] for m in cache.polymarket):
                print(f"[discover] polymarket universe → {len(slugs)} markets: "
                      f"{slugs[:3]}{'…' if len(slugs)>3 else ''}", flush=True)
            cache.polymarket = pm
            cache.last_poly = time.time()
        except Exception as e:
            print(f"[discover] polymarket err: {e}", flush=True)
        await asyncio.sleep(30)


async def stats_loop(writer: ParquetWriter, cache: UniverseCache):
    """Periodic heartbeat: total rows per msg_type, current universe sizes."""
    last = {}
    while True:
        await asyncio.sleep(30)
        cur = writer.counters()
        diff = {k: cur[k] - last.get(k, 0) for k in cur}
        last = cur
        per = " ".join(f"{k}={cur[k]}(+{diff[k]})" for k in sorted(cur))
        print(f"[stats] kalshi_u={len(cache.kalshi)} poly_u={len(cache.polymarket)}  {per}",
              flush=True)


async def views_loop(root: Path):
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(views.refresh_views, root)
        except Exception as e:
            print(f"[views] refresh err: {e}", flush=True)


async def main_async(root: Path):
    writer = ParquetWriter(str(root))
    cache = UniverseCache()
    print(f"[main] writing to {root}", flush=True)

    # Prime universes before listeners start.
    try:
        cache.kalshi = await asyncio.to_thread(discover_kalshi_btc_markets)
    except Exception as e:
        print(f"[main] initial kalshi discover err: {e}", flush=True)
    try:
        cache.polymarket = await asyncio.to_thread(discover_polymarket_btc_5m)
    except Exception as e:
        print(f"[main] initial polymarket discover err: {e}", flush=True)
    print(f"[main] kalshi={len(cache.kalshi)} polymarket={len(cache.polymarket)}",
          flush=True)

    kalshi = KalshiListener(writer)
    poly = PolymarketListener(writer)

    # Initial view file.
    try:
        await asyncio.to_thread(views.refresh_views, root)
    except Exception as e:
        print(f"[main] initial views err: {e}", flush=True)

    tasks = [
        asyncio.create_task(discovery_loop(cache)),
        asyncio.create_task(stats_loop(writer, cache)),
        asyncio.create_task(views_loop(root)),
        asyncio.create_task(writer.periodic_flusher()),
        asyncio.create_task(kalshi.run(lambda: [m["ticker"] for m in cache.kalshi])),
        asyncio.create_task(poly.run(lambda: list(cache.polymarket))),
    ]

    stop_event = asyncio.Event()
    def _sigterm(*_):
        print("[main] signal received, shutting down", flush=True)
        stop_event.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try: loop.add_signal_handler(sig, _sigterm)
        except NotImplementedError: pass

    await stop_event.wait()
    for t in tasks: t.cancel()
    writer.stop()
    try:
        await asyncio.to_thread(views.refresh_views, root)
    except Exception:
        pass
    print("[main] done", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="/Users/rithvikijju/edge-bot/tick_data",
                   help="Parquet root directory")
    args = p.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    asyncio.run(main_async(root))


if __name__ == "__main__":
    main()
