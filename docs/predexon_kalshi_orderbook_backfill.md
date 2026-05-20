# Predexon Kalshi Orderbook Backfill

Predexon is useful as a cheap historical snapshot source for Kalshi BTC15M and
KXBTCD research. It is not a replacement for our own live websocket capture
when we need promotion-grade replay against the live ledger.

## API Facts Verified

- Base URL: `https://api.predexon.com`
- Auth: `x-api-key` header.
- Market discovery: `GET /v2/kalshi/markets`
- Orderbook history: `GET /v2/kalshi/orderbooks`
- Orderbook required params: `ticker`, `start_time`, `end_time`.
- Orderbook timestamps are Unix milliseconds.
- Orderbook prices are cents, `1` to `99`.
- Orderbook page size is `limit=1..200`.
- Pagination uses `pagination_key`.
- Docs state Kalshi market/orderbook/trade endpoints are free and unlimited, but
  the free plan still has a 1 request/second rate limit.
- Predexon changelog notes a Kalshi orderbook data gap from
  `2026-03-12 08:00 UTC` to `2026-03-14 18:10 UTC`.

## Fidelity Classification

Predexon rows should be labeled as:

```text
provider = predexon
fidelity = historical_snapshot_provider_time
```

They are suitable for:

- broad model research,
- validating whether an idea survives beyond our small websocket sample,
- training microstructure features that only need sampled depth,
- estimating spread/liquidity regimes.

They are not sufficient for:

- exact live-ledger replay,
- local receive-time latency modeling,
- websocket gap/drop modeling,
- queue-position claims,
- proving FOK fillability at sub-second precision.

## Downloader

Script:

```powershell
python scripts\download_predexon_kalshi_orderbooks.py --help
```

Put the key in `credentials.env`:

```text
PREDEXON_API_KEY=...
```

High-value first BTC15M late-window pull:

```powershell
python scripts\download_predexon_kalshi_orderbooks.py --start 2026-01-07T00:00:00Z --end 2026-05-14T00:00:00Z --series KXBTC15M --window late --late-minutes 20 --max-tickers 500 --max-markets-per-event 1 --rps 1 --workers 1
```

KXBTCD hourly late-window pull, capped to the most liquid markets per event:

```powershell
python scripts\download_predexon_kalshi_orderbooks.py --start 2026-01-07T00:00:00Z --end 2026-05-14T00:00:00Z --series KXBTCD --window late --late-minutes 20 --max-tickers 1000 --max-markets-per-event 12 --rps 1 --workers 1
```

Smoke test one tiny slice:

```powershell
python scripts\download_predexon_kalshi_orderbooks.py --tickers KXBTC15M-26MAY122145-45 --start 2026-05-13T01:25:00Z --end 2026-05-13T01:45:00Z --window full --rps 1 --workers 1
```

The downloader writes partitioned Parquet under:

```text
data/predexon_kalshi_orderbooks/
```

Each pull also appends `manifest.jsonl` with request windows, snapshot counts,
paths, and errors. The Parquet data is intentionally ignored by git.

## Query Priority

Because even free endpoints can still be slow at 1 request/second, use this
order:

1. BTC15M, late 20 minutes, all available closed markets.
2. BTC15M, full 15-minute windows.
3. KXBTCD, late 20 minutes, top 12 markets per event by volume/OI.
4. KXBTCD, full hour windows for only events/markets used in promising research.

For promotion, rerun final candidates on our own websocket capture and require
agreement with actual live/paper ledger behavior.
