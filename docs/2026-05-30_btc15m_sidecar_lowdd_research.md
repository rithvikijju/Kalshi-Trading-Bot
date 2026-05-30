# 2026-05-30 BTC15M Sidecar / Lowdd Research Checkpoint

This checkpoint records a bounded live-websocket sidecar experiment, replay
results, and a paper-shadow accounting fix. It is research evidence only. It
does not authorize live trading.

## Materialized Sidecar Slice

Source:

`C:\Users\ClawService\.btc_kalshi_bot\btc15m_live_capture.duckdb.replay.jsonl`

Output copied locally under ignored `runtime/`:

`runtime/remote_snapshots/sidecar_slices/btc15m_raw_20260529_1600_20260530_0600.duckdb`

No raw multi-GB sidecar was copied into the repo.

Materializer contract:

- `start_utc_inclusive = 2026-05-29T16:00:00Z`
- `end_utc_exclusive = 2026-05-30T06:00:00Z`
- `synthetic_coinbase_from_top = true`
- `coinbase_ticker_source = synthetic_from_ws_orderbook_top_btc_spot`
- `raw_sidecar_rows_loaded = 1,543,098`
- `ws_orderbook_top = 1,453,022`
- `ws_lifecycle = 90,076`
- `signal_scan = 0`
- `order_decision = 0`
- `coinbase_ticker = 1,300,087`

The synthetic `coinbase_ticker` table is a practical research bridge because
the raw replay sidecar contains top-book and lifecycle rows, not raw Coinbase
ticks. It is not promotion-grade Coinbase tick-age evidence.

Local sanity query found `57` distinct BTC15M events in the slice. The replay
window used for strategy scoring was `2026-05-29T18:00:00Z` to
`2026-05-30T06:00:00Z`.

## Frozen F2 Replay Results

Commands used the frozen causal F2 policy family with
`fair_p_min = 0.60`, `edge_cents_min = 12`, `ttl = 10..12m`,
`spread <= 2c`, `entry = 2..50c`, and `max_btc_spot_age_sec = 10`.

| candidate | trades | official filled | official PnL | proxy PnL | note |
| --- | ---: | ---: | ---: | ---: | --- |
| `q250_firstskip_qty500` | 1 | 1 | `-$0.54` | `-$0.54` | reject/no evidence |
| `q250_firstskip_qty500_yes` | 0 | 0 | `$0.00` | `$0.00` | signal-starved |
| `q1000_yes` | 0 | 0 | `$0.00` | `$0.00` | signal-starved |

REST official fill for the one `q250_firstskip_qty500` row matched the proxy
result. This branch is not validated by the new slice.

## Broader Live-Holdout Rule Pack

`scripts/backtest_btc15m_live_holdout.py` was run on the same sidecar slice
with Kalshi REST official settlement for every event in each subwindow.

For `2026-05-29T18:00:00Z` to `2026-05-30T06:00:00Z`:

| strategy | trades | PnL | return on premium | win rate | max DD |
| --- | ---: | ---: | ---: | ---: | ---: |
| `cheap_yes_rr_first` | 33 | `+$1.128` | `+10.38%` | `36.36%` | `-$1.396` |
| `cheap_no_rr_first` | 33 | `-$5.542` | `-48.02%` | `18.18%` | `-$5.596` |
| `cheap_tail_best_side_first` | 48 | `-$5.004` | `-41.69%` | `14.58%` | `-$5.004` |
| `cheap_pair_lock_rr` | 16 | `+$0.710` | `+4.64%` | `100.00%` | `$0.000` |
| `cheap_tail_position_aware` | 48 | `-$4.824` | `-23.17%` | `33.33%` | `-$4.834` |
| `current_lowdd_no_rv` | 16 | `+$2.490` | `+23.69%` | `81.25%` | `-$0.460` |

Subwindow check for `current_lowdd_no_rv`:

| window | trades | official PnL | return on premium | win rate | max DD |
| --- | ---: | ---: | ---: | ---: | ---: |
| `2026-05-29T16:00Z..18:00Z` | 4 | `-$0.330` | `-14.16%` | `50.00%` | `-$0.900` |
| `2026-05-29T18:00Z..2026-05-30T00:00Z` | 9 | `+$1.310` | `+23.02%` | `77.78%` | `-$0.330` |
| `2026-05-30T00:00Z..06:00Z` | 7 | `+$1.180` | `+24.48%` | `85.71%` | `-$0.460` |

Interpretation:

- `current_lowdd_no_rv` is research-promising on this fresh official-settled
  slice, but it is not deployable. The positive sample is only `16` trades,
  and the raw slice uses synthetic BTC ticks from top-book rows.
- The earlier live paper ledger for this same lowdd family remained negative
  through May 28 (`12` fills, official PnL `-$3.84`), so the correct next step
  is more clean forward paper evidence, not live trading.
- The `cheap_pair_lock_rr` result is also diagnostic only. It should be audited
  separately for executable pair-lock timing before being considered a forward
  candidate.

## Lowdd Shadow Failure Mode

The lowdd paper shadow was not writing new paper fills after May 28. A bounded
materialization of the lowdd shadow replay sidecar for
`2026-05-29T16:00:00Z` to `2026-05-30T06:00:00Z` showed:

- `ws_orderbook_top = 1,451,556`
- `ws_lifecycle = 90,067`
- `signal_scan = 760,173`
- `order_decision = 2,939`
- signal actions: `none = 724,979`, `selected = 2,939`, `skip = 32,255`
- order actions: all `2,939` were `skip`
- dominant order detail: `sizing_budget_or_liquidity_zero` on `2,907` rows

Sizing diagnostics:

- `portfolio_available = 50.16`
- `portfolio_value = 100.00`
- `estimated_cost = 0.00`
- root cause: the paper shadow considered old May 28 paper fills still active
  because `paper_shadow_summary()` could only settle closed rows from the
  in-memory BTC minute history; after restart/time passage, that history no
  longer covered the old close times.

Fix:

- `scripts/btc15m_lowdd_live.py` now uses Kalshi REST official market results
  to settle closed paper rows before falling back to local BTC history.
- Regression test added:
  `test_lowdd_paper_summary_releases_closed_trade_with_official_result`.
- Validation:
  `python -m pytest scripts\test_btc15m_shadow_config.py scripts\test_btc_replay_sidecar_materialization.py -q --basetemp .pytest-codex-tmp-btc15m-lowdd-paper`
  passed with `15 passed`.

## Remote Cleanup / Forward Collection

Deployed only the paper-accounting fix to the remote deployment copy:

`C:\Users\ClawService\Kalshi-Trading-Bot\scripts\btc15m_lowdd_live.py`

Restarted only the paper lowdd scheduled task:

`\KalshiBTC_btc15m_lowdd_forward_shadow`

Process cleanup:

- The first task restart left an old May 28 child process alive.
- Old stale child PID `13720` was stopped.
- Clean restart produced new child PID `12796`.
- Status sidecar then reported the same PID `12796`.

Post-restart status:

- lowdd shadow `failed = false`
- lowdd shadow `dropped = 0`
- rows advanced over a 65-second check:
  - `ws_orderbook_top`: `4,629,919 -> 4,631,865`
  - `signal_scan`: `2,459,329 -> 2,460,347`
  - `ws_lifecycle`: `303,107 -> 303,120`
- no new selected signal/order decision after the clean restart yet.

Raw BTC15M capture was not restarted or paused. It remained healthy:

- raw capture PID `14260`
- `failed = false`
- `dropped = 0`
- `ws_orderbook_top = 16,933,760`
- `ws_lifecycle = 1,282,401`
- latest top-book row `2026-05-30T06:35:26.235940Z`

## Next Research Step

Keep the fixed lowdd paper shadow running and treat only post-restart rows as
fresh forward evidence for the accounting fix. Do not promote `current_lowdd`
unless future official-settled paper fills confirm the sidecar backtest under
the same execution-realism fields.

The most useful next experiment is to re-check the lowdd paper ledger after
the next BTC15M selected signal, verify that sizing no longer skips due to
stale exposure, and compare the new paper fill sequence against the replay
candidate rows from the raw sidecar.
