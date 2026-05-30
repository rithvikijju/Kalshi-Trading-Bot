# 2026-05-30 Branch/Data Research Checkpoint

This checkpoint records current evidence from the branch sweep and remote
collection audit. It is research evidence only. It does not authorize live
deployment.

## Remote Collection State

Checked around `2026-05-30T04:42Z` to `2026-05-30T05:42Z`.

- BTC15M raw capture status:
  - `failed = false`;
  - `dropped = 0`;
  - `ws_orderbook_top = 16,755,021`;
  - `ws_lifecycle = 1,274,109`;
  - latest top-book row at `2026-05-30T04:42:14.258787+00:00`;
  - replay sidecar rows match status counts for replay-critical tables;
  - `capture_raw_ws = false`, so this is faithful top-of-book capture, not
    full raw websocket level/delta capture.
- BTC15M lowdd forward shadow status:
  - `failed = false`;
  - `dropped = 0`;
  - `ws_orderbook_top = 4,463,363`;
  - `signal_scan = 2,381,496`;
  - `order_decision = 5,716`;
  - latest scan at `2026-05-30T04:42:11.569170+00:00`;
  - latest paper fill remains `2026-05-28T19:55:35.133281+00:00`.
- BTC1H high-conf status is stale:
  - status updated at `2026-05-28T05:15:56.553695+00:00`;
  - sidecar/DB are not current forward collection evidence;
  - copied readable snapshot to
    `runtime/remote_snapshots/snapshot_20260530_0030_btc1h`.

## Fidelity Experiments

### Cross-process top-book concordance

Compared recent overlapping BTC15M raw-capture and lowdd-shadow replay sidecar
rows by `(market_ticker, seq, source)` over tail samples.

- Common top-book keys: `26,900`.
- Kalshi top-book price/quantity field mismatches: `0`.
- `btc_spot` mismatches: `307`.
- Interpretation:
  - Kalshi top-of-book state agrees across independent processes for matched
    websocket keys.
  - `btc_spot` is process-local asynchronous context and should not be treated
    as a canonical per-Kalshi-message label.

### Live REST spot check

Compared latest sidecar top book for `KXBTC15M-26MAY300045-45` to Kalshi REST.

- REST: NO bid `0.9990`, qty `4920.20`.
- Sidecar: NO bid `0.999`, qty `4920.20`, complementary YES ask `0.001`.
- Interpretation: latest live captured top book matched the independent REST
  top-of-book check.

### DB accessibility

- Active BTC15M DuckDB files are locked on Windows while collectors run.
- Replay JSONL sidecars are the correct lock-free surface for current
  read-only research unless an experiment specifically requires pausing.
- No pause was needed for this checkpoint.

## Branch Inventory

Remote branch activity relevant to strategy/backtest work:

- `origin/arb-v3` and `origin/claude/v2-sami-strategy-backtest` contain
  `kalshi_v2/backtest.py`.
- `origin/claude/v2-backtest-harness` contains a distinct
  `kalshi_v2/backtest.py` with close-to-strike hard rejection.
- `origin/claude/v2-backtest-sweep` contains `kalshi_v2/backtest_sweep.py`,
  but not `kalshi_v2/backtest.py`, so it is not standalone runnable without
  combining it with a harness branch.
- Other `origin/claude/v2-*` branches mostly change one component of
  `kalshi_v2` (`model.py`, `strategy.py`, `execution.py`, `portfolio.py`, or
  config). They require a combined harness or config sweep to evaluate against
  captured data.

## BTC1H Snapshot Dataset

Built local gap-audited snapshot:

- Input:
  `runtime/remote_snapshots/snapshot_20260530_0030_btc1h/btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb`
- Output:
  `runtime/branch_backtests/btc1h_20260530_gapless.duckdb`
- Rows:
  - `ws_orderbook_top_all = 7,037,294`;
  - `ws_orderbook_top_dedup = 7,029,636`;
  - `signal_scan_all = 1,423,192`;
  - `order_decision_all = 62`;
  - `coinbase_ticker_all = 212,237`.
- Coverage:
  - first top-book row `2026-05-19T00:40:07.148535+00:00`;
  - last top-book row `2026-05-28T04:55:00.827342+00:00`;
  - `45,428` market gaps over `120s`;
  - max gap `895.936s`.
- Interpretation: usable for research backtests, but not promotion evidence
  without contiguous-segment filtering and official/execution gates.

## Branch Backtest Results

The v2 branch harnesses assumed `product_id = BTC-USD`, while collected BTC
ticks use `KRAKEN:XBT/USD`. Isolated runtime worktrees were patched only to
consume all non-null BTC prices and handle mixed ISO timestamps. Those patches
were not applied to the main branch.

### `origin/arb-v3`

Command shape:

```powershell
run_backtest(
  duckdb_path=runtime/branch_backtests/btc1h_20260530_gapless.duckdb,
  output_dir=runtime/branch_backtests/arb_v3_btc1h_20260530_patched,
  decision_interval_sec=60,
  decision_window_min=30,
  build_full_bank=False
)
```

Result:

- trades: `170`;
- net PnL: `-$16.09`;
- gross PnL: `-$9.40`;
- fees: `$6.69`;
- positive net trades: `0 / 170`;
- mean net per trade: `-$0.095`;
- worst trade: `-$0.36`;
- throttled trades: `67`.

### `origin/claude/v2-backtest-harness`

Same adapted data contract and 60-second scan cadence.

- trades: `170`;
- net PnL: `-$16.09`;
- gross PnL: `-$9.40`;
- fees: `$6.69`;
- positive net trades: `0 / 170`;
- same practical result as `arb-v3` on this snapshot.

### Coarse v2 variant sweep

Combined `origin/claude/v2-backtest-sweep` with the patched `arb-v3` harness.
Used 300-second scan cadence as a coarse screen. The full 13-variant sweep did
not finish inside a 30-minute cap; the orphaned local sweep process was stopped.
Completed variants:

| variant | trades | net PnL | win rate | max DD | note |
| --- | ---: | ---: | ---: | ---: | --- |
| `01_baseline_pre_sami` | 128 | `-$12.35` | `0.0%` | `$12.29` | reject |
| `02_v2_current_defaults` | 111 | `-$10.62` | `0.0%` | `$10.40` | reject |
| `03_awareness_no_throttle` | 111 | `-$10.62` | `0.0%` | `$10.41` | reject |
| `04_throttle_no_awareness` | 128 | `-$12.35` | `0.0%` | `$12.34` | reject |
| `05_sami_tight_gates` | 6 | `-$0.32` | `0.0%` | `$0.30` | low-trade reject |

Interpretation: no completed v2 branch/sweep candidate showed edge on this
captured BTC1H snapshot after fees.

## Current Forward Lowdd Result

Remote BTC15M lowdd paper ledger:

- paper fills: `12`;
- all `12` officially settled via Kalshi REST;
- wins/losses: `7 / 5`;
- official PnL: `-$3.84`;
- premium at risk including fees: `$49.84`;
- return on premium: about `-7.7%`;
- worst trade: `-$8.93`;
- best trade: `+$3.43`;
- first fill: `2026-05-28T05:55:04.244301+00:00`;
- latest fill: `2026-05-28T19:55:35.133281+00:00`;
- no new fills since then.

Interpretation: lowdd is not validated. It can keep collecting paper evidence,
but it should not be promoted and is currently a negative forward test.

## Research Ranking After This Checkpoint

1. Keep BTC15M top-of-book capture running. It is the best current data source,
   and the top-book fidelity checks passed.
2. Treat current v2 branch family as rejected on the May 19-28 BTC1H snapshot
   unless a later combined branch produces a materially different harness result
   under official/execution-realistic settlement.
3. Treat BTC15M lowdd as negative forward evidence, not a candidate.
4. Next research loop should focus on:
   - materializing BTC15M sidecar slices into a replay DB without stopping
     capture;
   - running causal BTC15M sidecar backtests on post-May-28 data;
   - searching for small, preregisterable filters that improve official PnL
     without using `btc_spot` as a canonical per-message label;
   - optionally starting a separate full raw websocket capture if we need
     level/delta replay, because current `capture_raw_ws=false` only proves
     top-of-book fidelity.
