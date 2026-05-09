# Live-Capture Backtest Rules

These rules apply whenever we backtest against DuckDB data collected by the live
websocket executors in `~/.btc_kalshi_bot/*capture*.duckdb`.

The goal is not to make a clean-looking historical dataset. The goal is to
replay exactly what a live bot could have known at each decision timestamp.

## Source Priority

| Source | Use |
|---|---|
| Live capture DuckDB | Best source for replaying what our bot saw live. |
| Live SQLite trade ledger | Source of actual paper/live decisions and fills. |
| Official Kalshi settlement/result | Source of final win/loss labels when available. |
| Historical Kalshi bid/ask DuckDB | Good for older research and rejecting weak variants, but not exact live execution. |
| CSV chart exports | Diagnostics only; never promotion-grade. |

## Mandatory Replay Rules

1. Use `received_at_ns` as the clock.
   Every Kalshi book update, Kraken/Coinbase spot update, scan, decision, health
   row, and private event must be ordered by local receive time. Do not align
   feeds by rounded minute, exchange timestamp, candle close, or wall-clock bins
   unless the replay explicitly proves the data was available before the
   decision.

2. Rebuild state incrementally.
   At decision time `T`, the model may only see rows with `received_at_ns <= T`.
   The replay state must be produced by applying snapshots and deltas in arrival
   order, then running the exact strategy on that state.

3. Respect asynchronous feed arrival.
   Kalshi orderbook deltas and BTC spot updates do not arrive at the same time.
   A replay may use the latest BTC spot only if it had actually arrived before
   the Kalshi-triggered scan. Never join a Kalshi row to a future BTC tick because
   both fall in the same minute.

4. Exclude unhealthy windows.
   Do not score windows where `capture_health.capture_dropped > 0`, the capture
   writer was unhealthy, BTC spot was stale, the Kalshi websocket was
   disconnected, orderbook snapshots were incomplete, or a sequence gap forced a
   resubscribe. Re-enter scoring only after a fresh snapshot is present.

5. Do not forward-fill market books across reconnects.
   After a Kalshi websocket reconnect, treat books as unknown until the new
   snapshot for that market has arrived. A stale best bid/ask is not executable.

6. Model what was actually captured.
   If `raw_ws_capture=False`, the replay can test top-of-book strategies and
   signal timing. It cannot honestly test full depth, queue position,
   order-flow-imbalance, or microprice features that require raw depth deltas.

7. Use executable prices.
   YES entries use the observed YES ask. NO entries use the observed NO ask from
   the reconstructed live book state. Do not assume `NO = 1 - YES` except through
   the same book reconstruction logic used live.

8. Include fees at entry.
   PnL and sizing must include Kalshi taker fees using the fee formula active in
   the executor. Any comparison table must state whether exit fees are modeled.

9. Simulate FOK conservatively.
   A replayed fill is valid only if the quoted top-of-book price and visible
   size at decision time could fill the order. If depth was not captured, cap the
   replay to the visible top quantity stored in `ws_orderbook_top` and do not
   assume deeper resting liquidity.

10. Label with official settlement where possible.
    Coinbase/Kraken spot is a live reference feed, not final settlement truth.
    Kalshi crypto markets settle on the relevant CF Benchmarks RTI 60-second
    pre-expiry average. When official settlement is missing, mark results as
    proxy-labeled and do not use them for promotion.

11. Keep strategy selection chronological.
    Tune on earlier capture/historical data, choose on validation, then score a
    later untouched capture window once. Do not keep changing strategy rules
    after seeing the same test-window trades.

12. Emit a manifest with every replay.
    A replay must write the capture DB path, snapshot time, table row counts,
    replay start/end, excluded unhealthy windows, strategy code version, fee
    mode, settlement-label source, and whether raw websocket data was available.

## Required Sanity Checks

Before trusting a live-capture backtest, verify:

| Check | Requirement |
|---|---|
| Capture coverage | Continuous `capture_health` rows across the replay window. |
| Drops | Zero scored rows after any nonzero capture drop until a clean resnapshot. |
| Book readiness | All scored markets have a current snapshot before deltas are applied. |
| Feed age | BTC spot used by a signal is not stale under executor rules. |
| Decision match | Replayed signals align with the live/paper ledger for the same strategy and settings. |
| Settlement | Official result used, or proxy result clearly labeled. |

## What Counts As Faithful

A faithful live-capture replay should be able to answer:

```text
Given only websocket messages that had arrived by this timestamp,
would this exact strategy have produced this exact signal, at this executable
price and size, after fees, without using later BTC or Kalshi information?
```

If the replay cannot answer that, the result is research-only.
