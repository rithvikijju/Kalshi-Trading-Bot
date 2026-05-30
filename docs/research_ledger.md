# Research Ledger

This ledger tracks strategy research paths so we do not repeatedly rediscover rejected ideas. It is intentionally terse: each entry should state data, method, result, status, and next action.

## Data Quality Hierarchy

| Dataset | Use | Fidelity |
|---|---|---|
| Live websocket capture / actual ledgers | Final holdout and promotion gate | Best available; respects observed top-of-book timing |
| Historical Kalshi bid/ask candle DuckDB | Discovery and train/validation | Useful but optimistic versus live websocket execution |
| CSV chart exports | Large-sample diagnostics only | Optimistic; not executable |

## Status Labels

| Status | Meaning |
|---|---|
| `rejected` | Failed pre-live selection or contradicted by holdout |
| `research-only` | Interesting but not execution-faithful enough |
| `shadow` | Worth forward testing without live capital |
| `deploy-candidate` | Passed enough evidence to consider implementation after review |

## Ledger Entries

### 2026-05-27 - Remote websocket snapshot backtest and shadow cleanup

Output:

- Remote snapshots:
  `C:\Users\ClawService\Kalshi-Trading-Bot\backtest_outputs\remote_tick_snapshots_20260527_212327\`.
- BTC15M replay:
  `C:\Users\ClawService\Kalshi-Trading-Bot\backtest_outputs\remote_backtest_btc15m_lowdd_20260527_212327\`.
- BTC1H local replay from downloaded remote snapshot:
  `backtest_outputs\remote_backtest_btc1h_core_ws_stride60_20260527_212327\`.

Data:

- BTC15M capture-only DB snapshot: `7,515,538` top rows, `85,188` BTC ticks,
  `361` finalized events, window `2026-05-24 09:10:48Z` to
  `2026-05-28 03:10:48Z`.
- BTC1H shadow-capture snapshot: `6,867,691` streamed top rows, `213` event
  windows, causal `received_at_ns` replay with `60s` scan stride.
- Raw BTC15M capture remained active after pause/resume: PID `14260`,
  `failed=false`, `dropped=0`, replay sidecar count matched DB status counts.

Result:

- BTC15M `current_lowdd_no_rv`: `161` official-settled trades, PnL `+3.320`,
  win rate `66.46%`, max drawdown `-4.870`, Sharpe `0.646`.
- BTC1H `baseline_emp70_logn_rv60`: `39` settled trades, PnL `+1.98`,
  win rate `69.23%`, max drawdown `-3.33`.
- BTC1H `high_conf_80_entry70_no_chase`: `12` settled trades, PnL `+0.89`,
  win rate `75.00%`, max drawdown `-2.00`.
- Full May 8 BTC1H loss-guard replay was stopped as impractical on the full
  tick stream; use the dedicated core websocket replayer for this capture
  format.

Status:

- BTC15M lowdd: `shadow`, but marginal; not a live deploy candidate.
- BTC1H: `research-only`; small positive replay does not override the negative
  official forward ledger and faithful-replay blockers.
- Stopped bad remote shadows and removed the watchdog/tasks that restarted
  them: `q250_qty500_firstskip`, `q250_qty500_firstskip_yes`, `q1000_yes`, and
  `btc1h_high_conf80_entry70_no_chase`.
- Started BTC15M lowdd paper forward shadow through Task Scheduler:
  child PID `13720`, launcher PID `13792`, trade DB
  `C:\Users\ClawService\.btc_kalshi_bot\btc15m_lowdd_forward_shadow_trades.db`.

Next:

- Keep raw BTC15M capture running. Count only future lowdd official-settled
  paper rows for promotion evidence.
- Do not restart the stopped shadows unless a new gate explicitly justifies a
  fresh paper clock.

### 2026-05-12 - Same-Hour Event Surface / Graph Features

Output:

- `backtest_outputs/mcts_contract_15m_20260512/event_surface_followup_20260512.md`
- `.codex_work/agent_event_surface_20260512/`
- `backtest_outputs/mcts_contract_15m_20260512/event_surface_graph_csv_20260512/`

Data:

- Historical bid/ask baseline: 44 trades.
- CSV diagnostic baseline: 1,243 trades.
- Live websocket executed-like decisions: 125 trades.

Result:

- 2,190 surface guards tested; 0 passed strict historical/CSV pre-live gate.
- Local neighbor cheapness, monotonicity, curvature, adjacent confirmation, and slope are rejected as standalone alpha.
- Useful risk overlay found: surface-safe/chase-state. Live all-mode baseline `+4.06`; surface-safe only `+8.37`; no-reprice + price-match + surface-safe `+7.60` with lower drawdown.

Status:

- Surface alpha: `rejected`.
- Surface-safe sizing/chase filter: `shadow`.

Next:

- Add surface-safe telemetry to shadow only.
- Do not deploy until new websocket data confirms the lift.

### 2026-05-12 - BTC15M Target-Free Market Momentum

Output:

- `.codex_work/agent_15m_price_only_20260512/`
- `backtest_outputs/mcts_contract_15m_20260512/research_loop_report_20260512.md`
- `backtest_outputs/mcts_contract_15m_20260512/btc15m_timeseries_combo_20260512/`

Data:

- Train: May 1-4 BTC15M historical candles.
- Validation: May 5-6.
- OOS: May 8-10 endpoint pull, 288 events and 4,320 quote rows.

Result:

- Target-dependent fair-value BTC15M strategies rejected on May 8-10 OOS.
- Target-free price momentum survived: TTL 2-5, spread <=2c, buy direction of >=10c YES midpoint move from prior minute.
- OOS: 132 trades, `+6.261`, 77.27% win, +6.54% return on cost.

Status:

- `research-only`.

Next:

- Requires BTC15M websocket capture/replay before deployment because minute-candle execution is optimistic.

### 2026-05-12 - BTC15M Market Momentum + BTC Price Time-Series

Output:

- `.codex_work/mcts_research_loop_20260512/btc15m_timeseries_combo.py`
- `backtest_outputs/mcts_contract_15m_20260512/btc15m_timeseries_combo_20260512/`

Data:

- Source feature file: `.codex_work/agent_15m_price_only_20260512/target_free_quote_features.csv`.
- 11,760 quote rows across 784 BTC15M events.
- Train/validation selection was done before scoring May 8-10 OOS.
- Tested 1,032 target-free strategies using Kalshi market price momentum, BTC short-horizon return, TTL, and spread filters.

Leakage controls:

- Uses only quote rows available at decision time.
- Takes the first qualifying quote per event.
- May 8-10 OOS was not used for strategy selection.
- Does not use target/floor/BRTI/resolution fields for signal generation.

Result:

- Best selected strategy: `align_mkt1_0.1_btc1_0_ttl2_5_spr2`.
- Logic: TTL 2-5 minutes, spread <=2c, Kalshi 1-minute side move >=10c, BTC 1-minute same-side move >=0 bps.
- Train: 182 trades, `+5.513`, 4.07% return on cost.
- Validation: 59 trades, `+7.219`, 17.28% return on cost.
- OOS May 8-10: 133 trades, `+7.427`, 7.61% return on cost, 78.95% win, max drawdown `-2.757`.
- This improved the prior pure market-momentum OOS (`+6.317`, 6.34% return on cost, max drawdown `-3.460`).

Status:

- `research-only`.

Next:

- Gather BTC15M websocket orderbook capture before any deployment discussion.
- Re-test with exact top-of-book volume, FOK feasibility, fees, and asynchronous BTC/Kalshi event timing.

### 2026-05-12 - BTC 1h BTC Price Path / Time-Series Guards

Output:

- `.codex_work/mcts_research_loop_20260512/btc_timeseries_research.py`
- `backtest_outputs/mcts_contract_15m_20260512/btc_1h_timeseries_20260512/`

Data:

- BTC source rows: 116,987 one-minute bars from research DuckDB and live cache.
- Trade rows scored: 1,461 across CSV exports, historical API DuckDB, and live websocket decision logs.
- Baseline live websocket decisions: 125 trades, `+4.06`, 5.02% return on premium, 68.00% win, max drawdown `-7.15`.
- Baseline live-only subset: 49 trades, `+3.29`, 10.71% return on premium, 69.39% win, max drawdown `-2.81`.

Method:

- Tested side-signed BTC returns over 1, 2, 3, 5, 10, 15, 30, 45, and 60 minutes.
- Tested absolute returns, realized volatility, RV ratios, hour-open move, trend consistency, and acceleration features.
- Strict promotion required historical and CSV train/validation success before live scoring.

Leakage controls:

- BTC bars are joined by `available_at <= entry_time`.
- Lookback returns use bars with `available_at <= entry_time - lookback`.
- Strict accepts are selected without live websocket performance.
- Live diagnostics are treated as diagnostics only, not deployment evidence.

Result:

- Strict accepted guards: 0.
- Live-positive diagnostics included `side_btc_ret_30m_bps >= -20` and `rv_15m <= 1`, but they were not robust on CSV or historical validation.
- Interpretation: simple BTC price-path features are useful telemetry but are not a standalone deployable 1h filter on the current selected-trade dataset.

Status:

- `rejected` as deployable alpha.
- Mild adverse-move telemetry: `research-only`.

Next:

- Do not add a BTC-price-path guard to live 1h trading yet.
- Revisit only as part of a broader model that jointly calibrates fair value, market microstructure, and asynchronous BTC/Kalshi timing.

### 2026-05-12 - BTC 1h Core Model Variants

Output:

- `.codex_work/agent_core_model_mcts_20260512/`

Result:

- `student_t3_rv60 + price_match + no_recent_reprice` is the best core-model shadow candidate.
- Heavy market shrink improves calibration but starves trades and is not a primary signal.

Status:

- `shadow`.

Next:

- Forward test against current model with identical websocket infrastructure.

### 2026-05-12 - BTC15M Live Websocket Capture

Output:

- `scripts/btc15m_live_capture.py`
- Capture DB: `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb`
- Logs: `logs\btc15m_capture_*.out.log` and `logs\btc15m_live_capture_*.log`

Design:

- Capture-only, no order submission.
- Separate process from the BTC 1h live bot.
- Subscribes to `KXBTC15M` orderbook websocket data and Kraken `XBT/USD` ticker.
- Stores `ws_orderbook_top`, `coinbase_ticker` (Kraken-labeled product), `ws_lifecycle`, `ws_control`, and `capture_health`.
- Uses a short open/flush/close DuckDB writer so the DB can be queried while the capture process keeps running.
- Raw websocket deltas are off by default to avoid storage blowup; top-of-book changes are captured.

Status:

- `active capture`.
- Smoke test captured top-of-book updates and Kraken ticks correctly.
- Live rollover test passed: the capture moved from `KXBTC15M-26MAY120700` to `KXBTC15M-26MAY120715` and continued with zero dropped rows.

Next:

- Let this run for many hours before promotion decisions.
- Treat any `capture_dropped > 0`, sequence gap, or long no-event window as a validation gap.

### 2026-05-12 - BTC15M Low-Drawdown Momentum Search

Output:

- `.codex_work/mcts_research_loop_20260512/btc15m_lowdd_research.py`
- `backtest_outputs/mcts_contract_15m_20260512/btc15m_lowdd_research_20260512/`
- Agent outputs:
  - `.codex_work/agent_btc15m_pathA_20260512/`
  - `.codex_work/agent_btc15m_pathB_20260512/`
  - `.codex_work/agent_btc15m_pathC_20260512/`

Data:

- Historical target-free BTC15M quote features: 11,760 rows, 784 events.
- Train: May 1-4.
- Validation: May 5-6.
- OOS: May 8-10, not used for train/validation selection.
- Fees included.

Result:

- Fair-value/target-aware overlays rejected for now. They improved validation optics but did not improve OOS PnL.
- Contrarian/reversion variants rejected.
- Robust historical family: 2-minute Kalshi midpoint momentum with same-direction BTC 1-minute confirmation.
- Path A candidate:
  - Logic: TTL 2-4 minutes, spread <=2c, entry 20-90c, buy side of a 2-minute YES-mid move, require BTC 1-minute return to agree by at least 1 bp.
  - `>=7.5c` 2-minute move: train 94 trades `+5.35`, validation 29 trades `+2.10`, OOS 59 trades `+6.22`, OOS 77.97% win, OOS max DD `-1.37`.
  - `>=10c` 2-minute move: train 88 trades `+5.77`, validation 25 trades `+2.21`, OOS 55 trades `+5.23`, OOS 80.00% win, OOS max DD `-1.68`.
  - `>=15c` 2-minute move: train 76 trades `+7.45`, validation 23 trades `+2.21`, OOS 53 trades `+5.44`, OOS 81.13% win, OOS max DD `-1.78`.
- Critic filter diagnostics:
  - `signed_btc_ret_15m_bps >= 0.5` on the earlier 1-minute momentum baseline had historical OOS 91 trades `+7.346`, 90.1% win, max DD `-1.436`.
  - But this longer BTC filter failed the tiny live websocket replay, so it is not promoted.

Corrected live websocket replay:

- Output: `backtest_outputs/mcts_contract_15m_20260512/btc15m_live_capture_validation_20260512/`
- Capture DB: `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb`
- Validation run: May 12, 2026, through `KXBTC15M-26MAY121515`.
- Data: 777,137 top-book rows, 10,870 Kraken ticks, 35 captured events, 34 finalized events.
- Important correction: unresolved/open markets are no longer scored as losses. The validator now only scores rows whose Kalshi settlement result is `yes` or `no`, and opens the DuckDB file read-only with retry so capture can keep running.

| Strategy | Trades | PnL | ROC | Win | Max DD | Trade Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| `lowdd_candidate_no_rv` | 15 | +3.70 | 39.78% | 86.67% | -0.61 | 3.02 |
| `lowdd_candidate_mkt2_btc3_rv` | 15 | +3.70 | 39.78% | 86.67% | -0.61 | 3.02 |
| `combined_btc15_align_mkt5_015` | 11 | +1.68 | 26.58% | 72.73% | -1.29 | 1.07 |
| `prior_market_mom_1m` | 24 | +1.22 | 7.75% | 70.83% | -2.25 | 0.57 |
| `combined_btc15_align` | 17 | +0.69 | 5.61% | 76.47% | -1.34 | 0.36 |
| `pathA_mkt2_0100_btc1_1` | 14 | -1.21 | -13.14% | 57.14% | -2.87 | -0.63 |

Best current websocket-validated 15-minute candidate:

- `lowdd_candidate_no_rv`.
- Rule: TTL 4-5 minutes, spread <=2c, entry 5-90c, 2-minute Kalshi YES-midpoint move at least 12.5c in the traded side direction, BTC 3-minute return not opposing the side, reject extreme 3-minute Kalshi moves over 35c, first qualifying quote per event.
- The RV variant currently gives identical trades, so the simpler no-RV rule is preferred unless more data shows the RV filter matters.

Status:

- `lowdd_candidate_no_rv`: `shadow` / continued websocket-validation candidate.
- Path A 2-minute momentum + BTC 1-minute alignment: `rejected on live websocket validation`.
- Combined 15-minute BTC alignment: `research-only`.
- Fair-value overlays: `rejected`.

Next:

- Continue collecting BTC15M websocket data.
- Re-run `btc15m_live_capture_validate.py` after each larger settled sample.
- Do not deploy BTC15M until the candidate survives materially more independent websocket-settled events. Current evidence is encouraging but still only 15 selected trades for the best rule.

Capture maintenance note:

- On May 12 around 13:09 local time, directly validating against the live BTC15M DuckDB file blocked the writer on Windows. The writer exited permanently and the process logged `writer_failed` drops while the DB stopped advancing at `2026-05-12T19:09:46Z`.
- Fix applied: `btc15m_live_capture.py` now treats temporary DuckDB file locks as retryable instead of killing the writer, and `btc15m_live_capture_validate.py` snapshots the DB first before reading.
- Capture restarted at May 12 14:09 local time, PID `82764`. New rows resumed at `2026-05-12T20:09Z`, event `KXBTC15M-26MAY121615`, with `capture_dropped=0`.
- Treat the missing interval between roughly `2026-05-12T19:09:46Z` and `2026-05-12T20:09Z` as a validation gap.

### 2026-05-12 - BTC 1h Main-Branch Sizing Calibration Check

Output:

- Script: `.codex_work/sizing_calibration_20260512/compare_lipschitz_sizing_ws.py`
- Report: `backtest_outputs/sizing_calibration_20260512/progress_20260512.md`
- Data: `data/live_capture_gapless/live_capture_gapless.duckdb`

Main-branch finding:

- `origin/main:kalshi_v2/robust.py` contains `LipschitzSizer`.
- `origin/main:kalshi_v2/risk.py` contains half-Kelly sizing.
- The overconfidence-specific piece is the Lipschitz size clamp: it smooths contract count as a function of entry price so nearby entry prices cannot cause large size jumps.

Replay setup:

- Current deployed model approximation: `research`, risk-adjusted sizing, TTL 5-20 minutes, NO side probability >= 0.72, cap 5 contracts, NO near-strike cap 1 inside $115.
- Fees included with the current Kalshi taker fee formula.
- Labels use official Kalshi settlement results.
- Active live capture DB was locked by the live bot, so this used the offline gapless websocket DB.

Actual captured fills only:

| Policy | Trades | Contracts | PnL | Return on $100 | Win | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| `risk_adjusted_current` | 10 | 24 | +9.92 | +9.92% | 100.00% | 0.00 |
| `risk_adjusted_plus_main_lipschitz` | 10 | 24 | +9.92 | +9.92% | 100.00% | 0.00 |

Selected websocket scans, earliest current-gated signal per event:

| Policy | Trades | Contracts | PnL | Return on $100 | Win | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| `risk_adjusted_current` | 23 | 48 | +7.75 | +7.75% | 82.61% | -5.11 |
| `risk_adjusted_plus_main_lipschitz` | 23 | 42 | +5.42 | +5.42% | 82.61% | -5.11 |

Conclusion:

- Do not add main-branch Lipschitz sizing to the live BTC 1h research bot based on this evidence.
- It reduced winners and did not reduce drawdown. The observed loss mechanism is directional model error in late high-edge trades, not adjacent-price sizing overconfidence.

### 2026-05-12 - Live Handoff Snapshot: BTC 1h + BTC15M

Snapshot time: May 12, 2026 14:40 MDT.

Active live processes observed:

| Strategy | Script | PID at snapshot | Mode | Notes |
|---|---|---:|---|---|
| BTC 1h late-only loss guard | `scripts/btc_1hr_late_only_loss_guard_live.py` | 85016 | live/prod | Existing 1h production bot. Leave running unless explicitly replacing it. |
| BTC15M low-drawdown momentum | `scripts/btc15m_lowdd_live.py` | 48324 | live/prod | New one-contract BTC15M forward test. |
| Weather bot | `.codex_work/high_hit_detector/kalshi_weather_ws_trader.py` | 66624 | live/prod | Unrelated; do not kill while managing BTC bots. |

BTC 1h current deployed strategy:

- Wrapper: `scripts/btc_1hr_late_only_loss_guard_live.py`.
- Engine: `scripts/btc_1hr_research_live.py`.
- Signal strategy: `research`.
- Sizing policy: `risk_adjusted`.
- Market data: websocket execution/capture path from the research engine.
- Deployment gates from wrapper:
  - TTL 5-20 minutes.
  - Max 5 contracts per trade.
  - NO side probability must be at least 72%.
  - NO side cap 3 contracts.
  - NO trades within $115 of strike capped at 1 contract.
  - 300 second event cooldown after failed websocket reprice/filter.
- Trade ledger: `C:\Users\ahmed\.btc_kalshi_bot\research_live_trades.db`.
- Capture DB: `C:\Users\ahmed\.btc_kalshi_bot\research_live_capture.duckdb`.
- Latest ledger check at snapshot:
  - `research_live_trades`: 92 rows.
  - `research_event_locks`: 66 rows.
  - Latest filled row: `KXBTCD-26MAY1213`, `KXBTCD-26MAY1213-T79999.99`, side `no`, 1 contract, entry `0.67`, created `2026-05-12T16:47:31Z`.
  - Previous rows: `KXBTCD-26MAY1210` YES 2 contracts at `0.72`, `KXBTCD-26MAY1207` NO 1 contract at `0.62`.
- Logging caveat:
  - The process is running and `research_live_capture.duckdb.wal` is active, but the visible `logs\btc_1hr_*` files checked at snapshot were empty/stale.
  - For continuation, use the SQLite trade ledger and capture DB first, then fix/verify stdout/file logging before relying on tails for this process.
  - Do not open the live DuckDB directly for heavy analysis on Windows; snapshot/copy first or use the gapless/offline DB.

BTC15M low-drawdown live strategy:

- Script: `scripts/btc15m_lowdd_live.py`.
- Strategy name: `btc15m_lowdd_no_rv`.
- Series: `KXBTC15M`.
- Sizing: exactly 1 contract per trade.
- Data feeds: Kalshi websocket orderbook + Kraken BTC websocket.
- Fill behavior: fill-or-kill, real YES/NO book mechanics; NO is executed via the YES-book ask side, not `NO = 1 - YES` shortcuts.
- Trade ledger: `C:\Users\ahmed\.btc_kalshi_bot\btc15m_lowdd_live_trades.db`.
- Capture DB: `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb`.
- Active stdout log at snapshot: `logs\btc15m_lowdd_live_20260512_142230.out.log`.
- Active internal log at snapshot: `logs\btc15m_lowdd_live_20260512_142231.log`.
- Error log at snapshot: `logs\btc15m_lowdd_live_20260512_142230.err.log`, length 0.
- Health at snapshot:
  - Kalshi websocket connected.
  - Kraken websocket connected.
  - `books=1/1`.
  - `capture_dropped=0`.
  - Crossed the 14:30 local 15-minute market boundary and subscribed to `KXBTC15M-26MAY121645`.
  - No BTC15M trades yet; the live ledger had 0 rows.
- Update after snapshot:
  - The BTC15M live ledger later showed 2 filled one-contract YES trades.
  - `KXBTC15M-26MAY121700-00`: filled at actual entry `0.57`, fee `0.02`, official result `yes`, realized PnL `+0.41`.
  - `KXBTC15M-26MAY121715-15`: filled at actual entry `0.82`, fee `0.02`, official result `yes`, realized PnL `+0.16`.
  - Total BTC15M live realized PnL after these two fills: `+0.57`.
- Known benign noise:
  - During market refresh, normal websocket closure can print `ConnectionClosedOK` / `Task exception was never retrieved`.
  - The process reconnected and resubscribed correctly. Patch later to suppress normal close tracebacks, but this was not operationally fatal.
- Rule currently deployed:
  - Current `KXBTC15M` event only.
  - TTL 4-5 minutes.
  - Spread <= 2c.
  - Entry 5-90c.
  - 2-minute YES-midpoint move at least 12.5c in the traded direction.
  - BTC 3-minute return must not oppose the traded direction.
  - Reject absolute 3-minute YES-midpoint move over 35c.
  - First qualifying trade per event.
- Expected trade frequency from websocket validation:
  - 15 trades over 35 finalized 15-minute events.
  - About 0.43 trades per event, or about 1.7 trades/hour.
  - No trade for 30-60 minutes is normal; no trade for 2+ hours should trigger a log/data-health check.

BTC15M validation result to keep in mind:

| Strategy | Trades | PnL | ROC | Win | Max DD | Trade Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| `lowdd_candidate_no_rv` | 15 | +3.70 | 39.78% | 86.67% | -0.61 | 3.02 |
| `lowdd_candidate_mkt2_btc3_rv` | 15 | +3.70 | 39.78% | 86.67% | -0.61 | 3.02 |
| `combined_btc15_align_mkt5_015` | 11 | +1.68 | 26.58% | 72.73% | -1.29 | 1.07 |
| `prior_market_mom_1m` | 24 | +1.22 | 7.75% | 70.83% | -2.25 | 0.57 |

Continuation commands:

```powershell
# Watch BTC15M live logs
$log = (Get-ChildItem logs\btc15m_lowdd_live_*.out.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName; Get-Content $log -Wait -Tail 100

# Check BTC bot processes without touching the weather bot
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" | Where-Object { $_.CommandLine -like '*btc15m_lowdd_live.py*' -or $_.CommandLine -like '*btc_1hr_late_only_loss_guard_live.py*' -or $_.CommandLine -like '*kalshi_weather_ws_trader.py*' } | Select-Object ProcessId, CommandLine | Format-List
```

Next steps:

- Let BTC15M run through many settled events before promotion; current evidence is promising but still small-sample.
- Re-run `btc15m_live_capture_validate.py` only via snapshot/copy of the live DuckDB.
- Fix 1h logging visibility so the running process has a reliable tailable log again.
- When comparing live vs replay, prefer official Kalshi settlements and actual captured top-of-book rows; ignore any intervals with capture gaps, writer failures, or websocket sequence gaps.

### 2026-05-12 - Implied-Volatility Surface Research: BTC 1h + BTC15M

Question:

- Can the equity-options idea of an implied volatility surface help our Kalshi crypto markets?
- For BTC 1h `KXBTCD`, the same-hour strike chain is a real discrete survival curve: each YES price is approximately `Q(S_T >= K)` under market-implied / risk-neutral probabilities.
- For BTC15M `KXBTC15M`, each event has one up/down threshold, so there is no same-expiry cross-strike surface. Treat BTC15M as a one-point digital market path, not a true IV surface.

Theory references used:

- Breeden-Litzenberger state prices / risk-neutral density from option prices: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2642349
- Gatheral-Jacquier arbitrage-free SVI volatility surfaces: https://arxiv.org/abs/1204.0646
- Kalshi orderbook docs for YES/NO book semantics: https://docs.kalshi.com/api-reference/market/get-multiple-market-orderbooks

Outputs:

- Main script: `.codex_work/mcts_research_loop_20260512/iv_surface_research.py`
- Main report: `backtest_outputs/mcts_contract_15m_20260512/iv_surface_research_20260512/report.md`
- BTC 1h IV features: `backtest_outputs/mcts_contract_15m_20260512/iv_surface_research_20260512/btc1h_iv_surface_trade_features.parquet`
- BTC 1h scores: `backtest_outputs/mcts_contract_15m_20260512/iv_surface_research_20260512/btc1h_iv_surface_filter_scores.csv`
- BTC15M single-point IV scores: `backtest_outputs/mcts_contract_15m_20260512/iv_surface_research_20260512/btc15m_single_point_iv_filter_scores.csv`
- BTC 1h second-round subagent: `.codex_work/agent_btc1h_surface_round2_20260512/`
- BTC15M second-round subagent: `.codex_work/agent_btc15m_surface_round2_20260512/`

Data:

- BTC 1h historical/csv/live surface feature base: `backtest_outputs/mcts_contract_15m_20260512/event_surface_graph_csv_20260512/event_surface_trade_features.csv`
- Corrected IV run rows:
  - BTC 1h total rows: 1,412.
  - Historical API: 44 trades.
  - CSV diagnostic: 1,243 trades.
  - Live websocket decisions: 125 rows.
  - Non-null fitted IV rows: CSV 987, historical 30, live 56.
- BTC15M historical lowdd candidate rows: 165 trades.
- BTC15M live websocket holdout rows for `lowdd_candidate_no_rv`: 15 trades.

Method:

- BTC 1h:
  - Use existing same-hour surface snapshots built at or before each trade entry.
  - Fit a simple digital-lognormal survival surface plus a small forward shift to each trade's contemporaneous strike chain.
  - Test fitted IV, fit RMSE, fitted side residual, IV/RV ratio, and existing surface-safe/chase filters.
- BTC15M:
  - Do not fabricate a cross-strike surface.
  - Test a one-point digital IV / RV diagnostic and path-state overlays against `lowdd_candidate_no_rv`.

Leakage/audit notes:

- First IV run accidentally dropped BTC 1h live rows because mixed timestamp formats parsed to `NaT`; fixed with mixed-format UTC parsing and reran.
- BTC 1h live IV fit is still not promotion-grade:
  - Some live sibling surface rows are stale / low-coverage.
  - BTC spot/RV in the IV script is joined from the research DuckDB, not reconstructed from the exact same live feed by `received_at_ns`.
  - Fitted residuals use the selected contract inside the fitted surface, so residuals are descriptors, not strict leave-one-out fair-value errors.
- BTC15M single-point IV is diagnostic only:
  - It is not a surface.
  - Live sample is only 15 trades.
  - Some metadata is fetched from Kalshi API during analysis and should be replaced with captured metadata for promotion-grade replay.

BTC 1h results:

| Policy | Slice | Trades | PnL | ROP | Win | Max DD | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| baseline | live all | 125 | +4.06 | 5.02% | 68.00% | -7.15 | reference |
| existing surface-safe | live all | 84 | +8.37 | 15.61% | 73.81% | -5.17 | promising shadow risk overlay |
| surface-safe + fit edge >= 0 | live all | 18 | +5.03 | 42.02% | 94.44% | -0.63 | reject: fails historical/CSV discipline, likely live-sample noise |
| fit side edge >= 0 | live all | 31 | +3.93 | 18.65% | 80.65% | -1.76 | reject: loses total PnL and weak pre-live support |
| fit RMSE <= 6 | live all | 55 | -0.39 | -1.04% | 67.27% | -5.97 | reject |
| surface-safe + price-match + no recent failed reprice 180s | live all | 66 | +7.60 | 17.92% | 75.76% | -3.27 | promising, but live-only; shadow only |
| surface-safe + price-match + no recent failed reprice 180s | live mode only | 26 | +4.98 | 31.09% | 80.77% | -1.29 | promising, but live-only; shadow only |

BTC 1h conclusion:

- The same-hour chain is theoretically meaningful, but fitted lognormal/IV residuals are not a deployable alpha here.
- The robust signal remains a risk state:
  - avoid/downsize recent chase-state trades where the selected side or whole event surface moved sharply in our favor.
  - Current `surface_safe` definition: `side_chg_1m_c <= 10` and `event_side_shift_1m_c <= 5`.
- Recommended next step is pre-registered shadow telemetry, not production replacement:
  - baseline
  - `surface_safe`
  - `surface_safe + price_match + no_recent_failed_reprice_180s/300s`
  - adaptive version: unexplained Kalshi surface chase after accounting for BTC move and local digital slope.

BTC15M results:

| Policy | Slice | Trades | PnL | ROP | Win | Max DD | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| `lowdd_candidate_no_rv` baseline | historical OOS | 59 | +7.64 | 19.92% | 77.97% | -2.70 | reference |
| `lowdd_candidate_no_rv` baseline | live holdout | 15 | +3.70 | 39.78% | 86.67% | -0.61 | reference |
| side RV edge >= -10 | live holdout | 8 | +0.95 | 15.70% | 87.50% | -0.61 | reject: loses PnL |
| side RV edge >= 0 | live holdout | 8 | +0.95 | 15.70% | 87.50% | -0.61 | reject |
| logit 3m change <= 2 | historical OOS | 58 | +8.06 | 21.24% | not primary | -2.28 | shadow telemetry only; unchanged live |
| side midpoint <= 0.60 | live holdout | 9 | +2.54 | 56.95% | not primary | -0.61 | possible sizing throttle, not replacement |
| acceleration 1m >= 0 | live holdout | 13 | +2.80 | 34.15% | not primary | -0.61 | weaker than baseline |

BTC15M conclusion:

- There is no true same-expiry surface in current BTC15M data; one-point IV did not improve the websocket holdout.
- `lowdd_candidate_no_rv` remains the best BTC15M candidate.
- Carry only these as shadow telemetry / possible sizing overlays:
  - `logit_chg3_le_2`
  - `side_mid_le_0.6`
  - `accel1_ge_0`
- Need more settled BTC15M websocket events before any promotion discussion.

Research roadmap:

1. Build a causal BTC 1h surface-state table from live capture using `capture_label`, `received_at_ns`, health exclusions, and no forward-fill through reconnects.
2. Test adaptive surface-safe sizing: `unexplained_chase_c = Kalshi side move - BTC-explained local digital move`.
3. Test event freshness breadth: percent of sibling markets updated in last 2s/5s/15s.
4. Test no-arb interval coherence with monotone survival repair inside bid/ask intervals.
5. Test local density/gamma throttle near the selected strike.
6. For BTC15M, test cross-horizon context only after simultaneous BTC15M and BTC 1h live surface capture exists; do not invent a 15m surface from neighboring events.

### 2026-05-12 - Cross-Horizon BTC 1h / BTC15M and External BTC Data Research

Question:

- Can the 1h BTC chain help the BTC15M up/down strategy?
- Can BTC15M microstructure help the BTC 1h strategy?
- Can external BTC data sources, especially perpetual funding/index history, add alpha or reduce losses?

Outputs:

- Main script: `.codex_work/mcts_research_loop_20260512/cross_horizon_external_research.py`
- Main report: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/report.md`
- BTC 1h context by minute: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc1h_context_by_minute.parquet`
- Deribit BTC perpetual funding/index pull: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/deribit_btc_perp_funding.csv`
- BTC15M cross-horizon trades: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc15m_lowdd_cross_horizon_trades.csv`
- BTC15M scored policies: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc15m_lowdd_cross_horizon_scores.csv`
- BTC15M mined rules: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc15m_cross_horizon_mined_rules.csv`
- BTC 1h cross-horizon trades: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc1h_cross_horizon_trades.csv`
- BTC 1h scored policies: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc1h_cross_horizon_scores.csv`
- BTC 1h mined live rules: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/btc1h_live_cross_horizon_mined_rules.csv`

External data sources checked:

- Deribit public BTC perpetual funding history worked and produced 309 hourly rows from 2026-04-30 01:00 UTC to 2026-05-12 21:00 UTC. Official endpoint: https://docs.deribit.com/api-reference/upcoming/market-data/public-get_funding_rate_history
- Bybit has an official funding-history endpoint, but direct API calls from this machine hit CloudFront 403. Docs: https://bybit-exchange.github.io/docs/v5/market/history-fund-rate
- Binance futures funding/open-interest endpoints are relevant candidates, but direct calls from this machine returned restricted-location 451.
- CME CF BRTI/BRR are relevant institutional BTC benchmarks. CME describes BRTI as once-per-second and BRR as a daily reference rate. Docs: https://www.cmegroup.com/education/courses/introduction-to-bitcoin/introduction-to-bitcoin-reference-rate
- The BRTI methodology is orderbook-based and would be useful as a benchmark if licensed/systematic access is available. Methodology: https://www.cmegroup.com/trading/files/bitcoin-real-time-index-methodology-version-2.pdf

Data used:

- BTC15M historical event-candlestick/bid-ask features:
  - File: `.codex_work/agent_15m_price_only_20260512/target_free_quote_features.csv`
  - Rows: 11,760 quote rows, 784 events.
  - Range: 2026-04-30 23:46 UTC to 2026-05-10 23:45 UTC.
  - Candidate trades: 165 `lowdd_candidate_no_rv` rows.
- BTC 1h trade-feature base:
  - File: `backtest_outputs/mcts_contract_15m_20260512/event_surface_graph_csv_20260512/event_surface_trade_features.csv`
  - Rows: 1,412 trades.
  - Sources: 1,243 CSV diagnostic, 44 historical API, 125 live websocket decisions.
- BTC 1h market context:
  - Built from `data/research_datamart/research.duckdb`.
  - Rows: 8,407 minute-level event context rows.
  - Range: 2026-04-30 to 2026-05-06 for KXBTCD context.
- Live websocket capture reference:
  - `data/live_capture_gapless/live_capture_gapless.duckdb` has 27,245,906 deduped BTC 1h top-book rows and 562 order decisions from 2026-05-06 01:07 UTC to 2026-05-10 18:42 UTC.
  - `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb` currently has 903,959 BTC15M top-book rows from 2026-05-12 10:42 UTC to 2026-05-12 21:27 UTC, but this does not materially overlap the May 6-10 BTC 1h gapless dataset yet.

Leakage guards:

- All cross-horizon joins are backward `asof` joins.
- BTC 1h context attached to BTC15M trades is only from KXBTCD rows available at or before the BTC15M candidate timestamp.
- BTC15M context attached to BTC 1h trades is only from KXBTC15M quote rows available at or before the BTC 1h trade timestamp.
- Deribit features use the latest hourly funding/index row at or before the trade timestamp.
- No live deployment recommendation is made from these features because long, simultaneous, clean websocket capture for both horizons is still limited.

BTC15M with BTC 1h / Deribit context:

| Policy | Split | Trades | PnL | ROP | Win | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline `lowdd_candidate_no_rv` | train | 82 | +1.88 | 3.47% | 68.29% | -4.27 | reference |
| baseline `lowdd_candidate_no_rv` | validation | 24 | +3.54 | 24.48% | 75.00% | -0.75 | reference |
| baseline `lowdd_candidate_no_rv` | OOS | 59 | +7.64 | 19.92% | 77.97% | -2.70 | reference |
| 1h side bias aligned >= 0c | train | 44 | +3.30 | 11.50% | 72.73% | -2.22 | improves train ROP/DD |
| 1h side bias aligned >= 0c | validation | 11 | +3.11 | 52.80% | 81.82% | -0.57 | lower total PnL |
| 1h side bias aligned >= 0c | OOS | 28 | +3.81 | 20.95% | 78.57% | -1.21 | cuts drawdown but loses half PnL |
| Deribit side return <= -10.4 bps | train | 25 | +1.18 | 7.02% | not primary | -1.42 | risk control only |
| Deribit side return <= -10.4 bps | validation | 6 | +1.96 | 48.51% | not primary | 0.00 | small sample |
| Deribit side return <= -10.4 bps | OOS | 19 | +5.63 | 45.51% | not primary | -0.20 | much lower DD, lower total PnL |

BTC15M conclusion:

- The 1h chain has some relationship to BTC15M risk: when the 1h market leans in the same direction, BTC15M ROP and drawdown can improve.
- It does not improve robust total PnL on the OOS set. The baseline makes +7.64 OOS; the aligned-1h version makes +3.81.
- Deribit side-return gates look like drawdown reducers, not alpha adders. They cut OOS DD from -2.70 to -0.20 but also cut PnL from +7.64 to +5.63.
- Do not change the BTC15M live strategy from this research. Carry `1h_side_bias`, `1h_side_chg`, and Deribit side-return only as shadow telemetry.

BTC 1h with BTC15M / Deribit context:

| Policy | Live holdout trades | PnL | ROP | Win | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---|
| baseline live websocket decisions | 125 | +4.06 | 5.02% | 68.00% | -7.15 | reference |
| BTC15M side mid bias >= 0c | 79 | +4.64 | 9.03% | 70.89% | -5.00 | weakly promising, live-only |
| BTC15M 5m side change >= 0c | 54 | +4.17 | 11.97% | 72.22% | -3.52 | improves DD, not enough PnL |
| BTC15M mid bias >= 0c and spread <= 1c | 48 | +4.24 | 13.35% | 75.00% | -3.01 | cleaner but live-only |
| Deribit side return >= -20 bps | 112 | +5.70 | 7.88% | 69.64% | -7.15 | adds PnL but keeps same DD |
| Deribit side return >= 10 bps | 29 | +4.64 | 26.73% | 75.86% | -1.52 | interesting, too few trades |
| TTL <= 13.35 minutes | 25 | +3.17 | 20.03% | 76.00% | -1.45 | time-in-hour effect, not cross-horizon |

Chronological live-holdout stress:

| Policy | Early PnL | Middle PnL | Late PnL | Notes |
|---|---:|---:|---:|---|
| baseline | +3.34 | +3.82 | -3.10 | last segment lost |
| BTC15M side mid bias >= 0c | -0.18 | +5.65 | -0.83 | unstable by segment |
| BTC15M 5m side change >= 0c | +2.31 | +4.03 | -2.17 | still fails late segment |
| BTC15M mid bias >= 0c and spread <= 1c | +3.12 | +2.37 | -1.25 | better, still not robust |
| Deribit side return >= 10 bps | +0.83 | +3.30 | +0.51 | only 29 trades total, 1 late trade |
| TTL <= 13.35 minutes | -0.25 | +1.27 | +2.15 | strongest late effect but not an external-data edge |

BTC 1h conclusion:

- The best external-data hint is Deribit index trend aligned with our trade side. `deribit_side_ret_1h_bps >= 10` produced +4.64 on only 29 live-holdout trades with -1.52 DD.
- That is not enough for production because the positive result is concentrated in a small live-only sample and lacks historical cross-check coverage.
- BTC15M context is directionally useful as a risk filter, especially `btc15_side_mid_bias >= 0` and `btc15_spread <= 1`, but the late chronological segment still loses.
- The stronger non-cross-horizon observation is time-in-hour: the last 13.35 minutes had better live-holdout behavior. That overlaps with our existing late-only direction and is not a new model.

Research verdict:

- No deployable alpha was found from cross-horizon or external BTC data today.
- Promising shadow features:
  - BTC 1h: `deribit_side_ret_1h_bps`, `btc15_side_mid_bias_c`, `btc15_side_chg_5m_c`, `btc15_spread_cents`.
  - BTC15M: `oneh_side_bias_c`, `oneh_side_chg_1m_c`, `deribit_side_ret_1h_bps`.
- Required next step before using these live:
  - Gather simultaneous BTC 1h and BTC15M websocket capture in the same replay clock.
  - Add Deribit live index/funding websocket or REST capture with `received_at_utc`.
  - Re-run the exact same rules on that truly simultaneous holdout.

Follow-up: Deribit BTC-PERPETUAL 1-minute candle research:

- Script: `.codex_work/mcts_research_loop_20260512/deribit_perp_cross_horizon_research.py`
- Data output: `backtest_outputs/mcts_contract_15m_20260512/cross_horizon_external_20260512/deribit_btc_perp_1m.csv`
- Rows: 101,779 one-minute candles.
- Official endpoint: https://docs.deribit.com/api-reference/market-data/public-get_tradingview_chart_data
- Important leakage fix:
  - First pass used the candle timestamp directly and was too optimistic because the candle close may not be known until the bucket completes.
  - Corrected pass treats a 1-minute candle as available only at `ts + 1 minute`.
  - All results below use the corrected no-current-minute-close version.

BTC15M corrected Deribit 1-minute features:

| Policy | Split | Trades | PnL | ROP | Win | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | train | 82 | +1.88 | 3.47% | 68.29% | -4.27 | reference |
| baseline | validation | 24 | +3.54 | 24.48% | 75.00% | -0.75 | reference |
| baseline | OOS | 59 | +7.64 | 19.92% | 77.97% | -2.70 | reference |
| Deribit side return 15m <= 5.625 bps | train | 64 | +3.81 | 9.25% | 70.31% | -2.59 | improves train |
| Deribit side return 15m <= 5.625 bps | validation | 18 | +2.26 | 21.04% | 72.22% | -0.93 | fails validation total PnL |
| Deribit side return 15m <= 5.625 bps | OOS | 50 | +8.49 | 26.94% | 80.00% | -2.00 | improves OOS, not robust enough |
| Deribit side return 60m <= 19.44 bps | train | 71 | +0.74 | 1.60% | 66.20% | -4.22 | fails train |
| Deribit side return 60m <= 19.44 bps | validation | 17 | +3.89 | 42.70% | 76.47% | -0.59 | improves validation |
| Deribit side return 60m <= 19.44 bps | OOS | 44 | +8.34 | 29.10% | 84.09% | -0.81 | improves OOS, not robust enough |

BTC15M Deribit 1-minute conclusion:

- After removing the current-minute candle leak, Deribit perp features are not yet deployable for BTC15M.
- The best OOS result, side return 15m <= 5.625 bps, improves OOS PnL from +7.64 to +8.49 and DD from -2.70 to -2.00, but validation PnL falls from +3.54 to +2.26.
- This is a useful shadow feature, not a live change.

BTC 1h corrected Deribit 1-minute features on live websocket decisions:

| Policy | Trades | PnL | ROP | Win | Max DD | Sharpe | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 125 | +4.06 | 5.02% | 68.00% | -7.15 | 0.80 | reference |
| Deribit side return 60m <= 0.1857 bps | 51 | +7.80 | 23.49% | 80.39% | -2.13 | 2.69 | strongest external-data hint, live-only |
| Deribit side basis >= 0 bps | 28 | +3.23 | 17.21% | 78.57% | -1.83 | 1.42 | lower total PnL, cleaner risk |
| Deribit side return 1m <= -1.278 bps | 25 | +2.96 | 17.37% | 80.00% | -1.76 | 1.54 | lower total PnL, cleaner risk |

Chronological stress for `Deribit side return 60m <= 0.1857 bps`:

| Segment | Baseline PnL | Filter PnL | Baseline trades | Filter trades | Notes |
|---|---:|---:|---:|---:|---|
| early | +3.34 | +1.71 | 62 | 24 | gives up early profit |
| middle | +3.82 | +4.99 | 31 | 15 | improves |
| late | -3.10 | +1.10 | 32 | 12 | fixes the late loss segment |

BTC 1h Deribit 1-minute conclusion:

- This is the most interesting new external-data result.
- Interpretation: the 1h bot appears to perform better when we are not chasing a strong Deribit perp move in the same direction over the last hour. This is consistent with the prior market-shrink / late-only / anti-chase research.
- It is still not production-ready because it is selected and evaluated on the same 125 live websocket decisions. It should be added to shadow telemetry with a pre-registered rule and validated on future live capture before deployment.

### 2026-05-12 - Deribit Anti-Chase Deep Loop

Question:

- The prior pass suggested Deribit BTC-PERPETUAL state might help the BTC 1h strategy avoid bad chase trades.
- Run a broader, stricter research loop to see whether the pattern is real, what form it takes, and whether it transfers beyond the live sample.

Outputs:

- Script: `.codex_work/mcts_research_loop_20260512/deribit_anti_chase_deep_loop.py`
- Report: `backtest_outputs/mcts_contract_15m_20260512/deribit_anti_chase_deep_20260512/report.md`
- Feature table: `backtest_outputs/mcts_contract_15m_20260512/deribit_anti_chase_deep_20260512/btc1h_deribit_deep_features.parquet`
- Rule scores: `backtest_outputs/mcts_contract_15m_20260512/deribit_anti_chase_deep_20260512/rule_scores.csv`
- ML gate scores: `backtest_outputs/mcts_contract_15m_20260512/deribit_anti_chase_deep_20260512/ml_gate_scores.csv`
- Random subset sanity tests: `backtest_outputs/mcts_contract_15m_20260512/deribit_anti_chase_deep_20260512/candidate_random_subset_tests.csv`
- Kept/skipped profile for the best candidate: `backtest_outputs/mcts_contract_15m_20260512/deribit_anti_chase_deep_20260512/candidate_A_kept_vs_skipped_profile.csv`

Data and method:

- Input trades: 1,412 BTC 1h candidate/live decision rows from `btc1h_deribit_perp_trades.csv`.
- Deribit candles: 101,779 BTC-PERPETUAL 1-minute candles.
- Features: 77 total features.
- Rules scored: 7,816 single/pair filters.
- ML gates scored: 3 shallow gates: logistic, random forest, histogram GBDT.
- Leakage guard:
  - Deribit candle close available only at `bucket_ts + 1 minute`.
  - Features backward-asof joined to the Kalshi decision timestamp.
  - No live bot changes.

Baseline slices:

| Slice | Trades | PnL | ROP | Win | Max DD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| dev train | 596 | +153.08 | 45.57% | 82.05% | -2.52 | 15.52 |
| dev validation | 453 | +136.74 | 55.53% | 84.55% | -1.97 | 16.52 |
| dev test | 238 | +75.19 | 57.92% | 86.13% | -1.86 | 13.53 |
| live websocket holdout | 125 | +4.06 | 5.02% | 68.00% | -7.15 | 0.80 |

Best live candidates:

| Candidate | Meaning | Trades | PnL | ROP | Win | Max DD | Sharpe | Skipped PnL | Random-subset P(PnL >= candidate) | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `perp_rv_3m_bps >= 0.6665 AND side_chg_1m_c <= 5` | Deribit short-horizon movement exists, but Kalshi selected side has not jumped >5c in the last minute | 58 | +11.33 | 30.90% | 82.76% | -1.26 | 4.18 | -7.27 | 0.00% | strongest exploratory lead |
| `event_side_shift_5m_c >= 0 AND side_chg_1m_c <= 5` | Pure Kalshi state: no 1m selected-side chase, broader 5m event support nonnegative | 28 | +6.88 | 37.97% | 89.29% | -1.12 | 4.59 | -2.82 | 0.10% | cleaner transfer, smaller sample |
| `event_side_shift_1m_c <= 0.974 AND event_side_shift_5m_c >= 0` | Smooth event support without recent event-wide jump | 21 | +5.88 | 44.82% | 90.48% | -1.02 | 4.78 | -1.82 | 0.13% | very selective |
| `side_perp_ret_60m_bps <= 0.1857` | Earlier Deribit 60m anti-chase rule | 51 | +7.80 | 23.49% | 80.39% | -2.13 | 2.69 | -3.74 | 0.72% | useful but weaker than short-RV + no-chase |
| `side_chg_1m_c <= 10` | Existing Kalshi no-chase state | 90 | +8.38 | 14.54% | 73.33% | -5.17 | 2.05 | -4.32 | 1.12% | broad but still allows drawdown |

Candidate A kept vs skipped profile:

| Feature | Kept median | Skipped median | Read |
|---|---:|---:|---|
| entry price | 0.60 | 0.65 | kept trades are cheaper / more convex |
| side 1m change | -8.0c | +9.0c | skipped trades are classic chase |
| side 5m change | -2.5c | +16.25c | skipped trades already ran |
| event 1m shift | -3.11c | +5.30c | skipped trades have event-wide chase |
| event 5m shift | -0.90c | +6.33c | skipped trades have broader price chase |
| Deribit side 60m return | +0.15 bps | +7.60 bps | skipped trades have more same-side perp extension |
| side basis | -2.73 bps | -3.96 bps | kept trades have less adverse basis |

Focused Candidate A variants:

| Variant | Trades | PnL | ROP | Win | Max DD | Random-subset P(PnL >= variant) | Read |
|---|---:|---:|---:|---:|---:|---:|---|
| A base: `perp_rv_3m_bps >= 0.6665 AND side_chg_1m_c <= 5` | 58 | +11.33 | 30.90% | 82.76% | -1.26 | 0.01% | best broad live pattern |
| A + `side_perp_ret_60m_bps <= 0.1857` | 31 | +9.92 | 49.40% | 96.77% | -0.54 | 0.00% | strongest/selective external-data pattern |
| A + `entry_price <= 0.70` | 50 | +9.44 | 30.89% | 80.00% | -1.44 | 0.03% | practical cap keeps most edge |
| A + `ttl_min <= 45` | 45 | +9.72 | 34.37% | 84.44% | -1.55 | not primary | TTL is live-only here; cannot use as pre-live transfer evidence |
| A + `entry_price <= 0.60` | 29 | +2.58 | 15.71% | 65.52% | -2.38 | not primary | too strict; cuts too many winners |

Candidate A + Deribit 60m read:

- The most selective version combines two ideas:
  - short-term Deribit movement exists (`perp_rv_3m_bps >= 0.6665`);
  - Kalshi has not chased our side in the last minute (`side_chg_1m_c <= 5`);
  - broader Deribit has not already moved far in our side's direction (`side_perp_ret_60m_bps <= 0.1857`).
- Live holdout result: 31 trades, +9.92 PnL, 96.77% wins, -0.54 max DD.
- This is excellent but too sample-selected for immediate production. It is the best candidate for pre-registered shadow testing.

Interpretation:

- The strongest pattern is not "follow Deribit momentum."
- It is: there is recent BTC perp activity, but Kalshi has not already repriced our selected side upward.
- That looks like stale/underreacting Kalshi book alpha.
- The bad trades are mostly the ones where both the selected side and the broader event surface have already moved in our favor. Those look like late chase entries where our model sees edge but the market has already moved.
- Short-horizon Deribit activity helps separate useful fresh information from stale quiet periods, but the Kalshi no-chase features are still the core explanatory variables.

ML gate result:

- The shallow ML gates did not transfer.
- Random forest live: 88 trades, +2.92, worse than baseline +4.06.
- Logistic live: 62 trades, +1.69, worse.
- Shallow GBDT live: 81 trades, +0.59, worse.
- Conclusion: simple interpretable state filters beat black-box gates on this dataset.

Research verdict:

- Do not deploy yet because Candidate A was discovered on the same 125 live decisions used for the strongest score.
- The result is strong enough to shadow immediately with a pre-registered rule:
  - `perp_rv_3m_bps >= 0.6665`
  - `side_chg_1m_c <= 5`
  - optional stricter external-data confirmation: `side_perp_ret_60m_bps <= 0.1857`
  - keep the existing late-only / loss-guard / FOK / book-reprice safety stack unchanged.
- The pure Kalshi candidate `event_side_shift_5m_c >= 0 AND side_chg_1m_c <= 5` is the cleaner transfer candidate if we want less dependency on external Deribit capture.

Next research steps:

1. Add Deribit 1m features to shadow telemetry only, not trading.
2. Pre-register Candidate A and Candidate B in the shadow logger.
3. Evaluate only on future websocket capture; do not re-tune thresholds until at least 100 additional settled live decisions.
4. Test whether Candidate A still wins after excluding all trades with entry price >70c.
5. Test sizing, not entry, only after Candidate A survives future holdout.

### 2026-05-13 - Execution Latency / Order Speed Note

Question:

- Can orders be faster without degrading signal quality?

Current view:

- The best low-risk path is not to loosen model filters. It is to reduce
  software latency between a qualifying websocket state and FOK submit.
- Useful engineering changes for the next execution iteration:
  - Keep websocket state hot and avoid REST polling in the decision path.
  - Maintain pre-filtered per-event candidate lists so each tick evaluates only
    affected strikes.
  - Separate capture writes from order submission with nonblocking queues.
  - Reuse authenticated Kalshi sessions and avoid unnecessary account calls
    before a FOK order when cached bankroll/exposure checks are fresh.
  - Track timing metrics for each stage: websocket receive, state update,
    model evaluation, book reprice, order submit, and Kalshi response.

Constraint:

- Speed changes must preserve the same executable-price and top-of-book
  fillability checks. If a faster path changes what price/side/market the bot
  trades, it is a new strategy and must be separately replay-tested.

### 2026-05-13 - BTC15M LowDD Latency Patch

Implemented in `scripts/btc15m_lowdd_live.py`:

- Final hot-book reprice immediately before sizing/order submit.
- Skip if executable entry worsens by more than 2c versus the signal snapshot
  (`BTC15M_MAX_REPRICE_WORSE_CENTS`, default `2.0`).
- Downsize against current visible top-of-book quantity instead of assuming the
  earlier displayed size is still fillable.
- Add a 60s event chase cooldown after FOK no-fill or failed hot-book reprice
  (`BTC15M_ORDER_CHASE_COOLDOWN_SEC`, default `60.0`).
- Keep a warm portfolio cache outside the hot websocket decision path
  (`BTC15M_PORTFOLIO_CACHE_TTL_SEC=30`, warm refresh every 15s by default), so
  account REST is usually not between signal and FOK.
- Move normal websocket-update scans before periodic REST event refreshes; only
  lifecycle-triggered refreshes scan after refresh.
- Remove pre-submit decision capture/logging from the hot path; submit first,
  then record the response/no-fill decision with `ready_ms` and `submit_ms`.

Validation:

- `python -m py_compile scripts\btc15m_lowdd_live.py`
- `python scripts\btc15m_lowdd_live.py --mode dry-run --duration-sec 8 --health-sec 5`

Capture note:

- The live trader still writes the same DuckDB capture/decision tables through
  the nonblocking writer. If we need to remove market capture from the trading
  process entirely, `scripts\btc15m_live_capture.py` can run as a separate
  capture-only process while the trader keeps only trade/order telemetry.

### 2026-05-13 - Order Entry and Capture Storage Roadmap

Kalshi order-entry facts:

- Documented Kalshi WebSockets are for market/order/fill updates, not for
  submitting new orders.
- Live order placement remains REST via `POST /portfolio/events/orders` unless
  we get Kalshi FIX access.
- Kalshi FIX is the lower-latency order-entry path. It supports New Order
  Single, cancel/replace, cancel, and execution reports, but likely requires
  separate approval/credentials.

Fastest practical stack without sacrificing signal quality:

1. Keep Kalshi orderbook and Kraken BTC spot on WebSockets.
2. Keep final decision based on in-memory websocket state.
3. Keep final 1-2c hot-book reprice and visible top-quantity check.
4. Use REST FOK order submit until FIX access exists.
5. Use WebSocket `user_orders` / fills for confirmation and reconciliation,
   avoiding extra hot-path REST polling.
6. Warm/cache portfolio and risk state outside the signal-to-submit path.
7. Preload/subscribe the next event before rollover and reduce `/markets` REST
   refreshes to avoid 429s.
8. Keep capture writes asynchronous; do not let storage decide whether a trade
   gets submitted.

Storage/capture plan:

- Hot path:
  - Trader keeps only in-memory websocket state plus minimal local safety state.
  - Capture rows are put onto a nonblocking/in-process queue.
  - If the queue backs up, low-priority raw rows should drop before trade logic
    is slowed.
- Warm store:
  - Current DuckDB capture is acceptable for one/few markets because queue depth
    is low and `capture_dropped=0`.
  - DuckDB can briefly reject readers on Windows while the writer is flushing;
    this is a live-inspection inconvenience, not a trading bottleneck.
- Cold store:
  - At day end, snapshot the live DuckDB and export completed partitions to
    Parquet with ZSTD compression.
  - Partition by `market_type/date/table`, for example
    `data/cold_capture/btc15m/date=2026-05-13/ws_orderbook_top.parquet`.
  - Keep the current day in DuckDB for simple appends/health checks; keep older
    days in compressed Parquet for research scans.
  - Never archive/delete a live DB segment until row counts, min/max timestamps,
    and capture-health rows have been verified.
- Scaling threshold:
  - No external message queue is needed for one BTC15M market with small queue
    depth.
  - If we run many Kalshi markets and multiple real-time consumers, add a
    durable bus such as Redis Streams or NATS, or move storage to ClickHouse.
  - ClickHouse is a better long-running analytics sink than DuckDB when we need
    continuous concurrent reads and writes across many markets.

### 2026-05-13 - Pause Take-Profit Exit Rules Until More Data

Decision:

- Put BTC15M take-profit/early-exit deployment on hold until we have more live
  websocket capture and more settled examples.
- The current TP replay is too sensitive to one bad trade/path dependency, so
  treat the 95c/97c/99c and fixed-profit exit results as exploratory only.
- Do not deploy TP exits from the current sample without re-running on a larger
  websocket holdout.

Next-session reminder:

- Revisit this after another overnight/day session of BTC15M websocket capture.
- Re-run executable exit replay with bid-side exits, visible quantity checks,
  entry and exit fees, and settlement audit.
- Compare against the currently deployed hold-to-settlement RR-gated strategy,
  not only against the no-RR baseline.

### 2026-05-14 - BTC15M Apr 1-7 Predexon Deep Dive

Scope:

- Fixed KXBTC15M slice only: events closing from 2026-04-01 00:00 UTC through
  2026-04-08 00:00 UTC.
- Study split: Apr 1-4 close times. Holdout split: Apr 5-7 close times.
- No expansion beyond the requested first seven April days.

Artifacts:

- Pre-registration: `docs/2026-05-14_btc15m_apr1_7_preregistration.md`.
- Script: `scripts/research_btc15m_apr1_7_deepdive.py`.
- Output folder:
  `backtest_outputs/btc15m_apr1_7_deepdive_20260514_185319/`.
- PDF report:
  `backtest_outputs/btc15m_apr1_7_deepdive_20260514_185319/btc15m_apr1_7_deepdive_report.pdf`.

Data audit:

- Raw prepared Predexon rows: 613,606.
- Deduped feature rows: 181,348 across 641 events/markets.
- Study events: 362. Holdout events: 279.
- Deduped 281 duplicate event/market/timestamp snapshots by keeping latest
  sequence.
- Quality-ok feature rows: 179,941.
- Integrity checks passed after dedupe: zero duplicate feature timestamps per
  event/market, zero duplicate strategy/event trades, zero trades at/after
  close, zero PnL formula mismatches, zero bad entry prices, zero low-quality
  trade rows.

Validation result:

- 32 pre-registered strategies/model variants were tested, including the
  currently running BTC15M low-drawdown logic, a 1h-style fair-value transfer,
  orderbook/micropressure rules, BTC-alignment rules, timing filters, liquidity
  filters, stale-quote/spread filters, the not-applicable cross-sectional graph
  hypothesis, and sklearn HGB/logistic/MLP models.
- No strategy passed the full numerical gates. Every candidate failed
  multiple-comparison significance after the permutation/Bonferroni gate.
- Current BTC15M lowdd rule: study -$7.71 over 24 trades, holdout +$1.35 over
  9 trades, all -$6.36 over 33 trades.
- 1h-style fair-value transfer: study +$2.34 over 105 trades, holdout +$2.55
  over 63 trades, all +$4.89 over 168 trades, but holdout Sharpe was below the
  pre-registered threshold and Bonferroni-adjusted p-value was 1.0.
- Best holdout-only exploratory candidates were `h22_tail_no_lottery`
  (+$5.21/85 trades), `h06_tail_btc5_align` (+$4.88/56 trades),
  `h03_liquid_cheap_tail` (+$4.08/129 trades), and `h19_ttl_6_8_tail`
  (+$3.88/95 trades). These are not deployable from this slice because study
  behavior was poor and statistical gates rejected them.

Conclusion:

- This run did not find production-ready BTC15M alpha on the requested Apr 1-7
  slice.
- The useful research direction is not a new live deployment; it is to
  investigate why cheap tail/liquidity/BTC-aligned candidates flip from weak
  study to positive holdout, then validate only on live websocket capture or on
  a separately pre-registered Predexon period.

Deployment note:

- On user instruction, `scripts/btc15m_lowdd_live.py` now supports
  `--strategy h02` / `--strategy h02_fair_value_transfer`.
- H02 uses the same Kalshi/Kraken websocket execution engine as the prior
  BTC15M lowdd bot, but swaps the signal to the pre-registered h02 lognormal
  fair-value transfer: TTL 2-8m, spread <=2c, causal Kraken spot, 60m
  close-to-close annualized vol, one-contract sizing, and a 10c post-fee edge
  threshold. Final hot-book reprice recomputes h02 edge before submit.
- This is a forward test, not a promoted production alpha, because the Apr 1-7
  validation gates still had 0/32 passing strategies.

### 2026-05-14 - BTC15M Apr 1-4 Alpha Loop, Fixed Apr 5-7 Validation

Scope:

- User explicitly rejected the first broad pass as insufficient. The follow-up
  research loop used only the Apr 1-4 `study` rows from:
  `backtest_outputs/btc15m_apr1_7_deepdive_20260514_185319/side_candidates.parquet`.
- No Apr 5-7 rows were used until candidate rules had been fixed from Apr 1-4
  study-only analysis.
- Fixed validation then used Apr 5-7 `holdout` rows from the same prepared
  slice. No thresholds were tuned on holdout.
- New artifacts:
  - `scripts/research_btc15m_apr1_4_alpha_loop.py`
  - `scripts/research_btc15m_apr1_4_walkforward.py`
  - `scripts/validate_btc15m_apr5_7_candidates.py`
  - `backtest_outputs/btc15m_apr1_4_alpha_loop_latest/`
  - `backtest_outputs/btc15m_apr1_4_walkforward_latest/`
  - `backtest_outputs/btc15m_apr1_4_walkforward_loop3/`
  - `backtest_outputs/btc15m_apr5_7_fixed_validation_latest/`

Background data:

- KXBTCD 1-hour Predexon March-April backfill was started as requested.
- Process PID at launch/check: `21196`.
- Command series: `scripts\download_predexon_kalshi_orderbooks.py --series KXBTCD --start 2026-03-01T00:00:00Z --end 2026-05-01T00:00:00Z --status closed --window late --late-minutes 25 --max-markets-per-event 24 --rps 1 --workers 1 --limit 200`.
- Output root: `data\predexon_kalshi_orderbooks\series=KXBTCD`.
- Log: `logs\predexon_kxbtcd_mar_apr_20260514_192703.out.log`.

Study-only findings that failed holdout:

- Early BTC5 continuation looked very strong in Apr 1-4 study:
  - `spread<=2`, `entry .05-.80`, `visible_qty>=1`, `8<=ttl<=15`,
    `side_btc_5m_bps>=5`, `side_mid_chg_1m>=-0.05`.
  - Study: 103 trades, +$14.19, win 84.47%, Sharpe 3.94.
  - Apr 1-4 leave-one-day-out selected variants were positive on every
    internal fold.
  - Fixed Apr 5-7 holdout failed: 58 trades, -$4.74, win 58.62%,
    max DD -$6.25.
- Late reversal also looked strong in Apr 1-4 study:
  - `spread<=2`, `entry .05-.80`, `0<=ttl<=6`,
    `side_mid_chg_3m<=-0.05`, `side_btc_1m_bps<=-2`.
  - Study: 88 trades, +$12.45, ROP 43.61%, Sharpe 3.15.
  - Fixed Apr 5-7 holdout failed: 48 trades, -$3.30, win 25.00%.
- High-entry 2m pullback from loop 3 looked modestly robust inside study:
  - `entry>=.75`, `side_mid_chg_2m<=-.01`, `3<=ttl<=10`.
  - Study: 80 trades, +$3.62, win 90.00%.
  - Fixed Apr 5-7 holdout failed: 38 trades, -$5.74, win 71.05%.
- Bounded calibration/distance/RV guard failed:
  - `spread<=2`, `entry .08-.80`, `side_fair_p .60-.95`,
    side-distance 5-40 bps, `rv_15m/rv_60m<=1`, `fair_edge_cents -10..10`.
  - Study with 2c adverse fill: 106 trades, +$4.05.
  - Holdout with 2c adverse fill: 79 trades, -$5.17.

Study-only findings that survived fixed Apr 5-7 holdout:

- Best current candidate is fee-aware fair probability:
  - `side_fair_p >= 0.60`
  - `side_fair_p - entry_price - entry_fee >= 0.10`
  - first qualifying row per event, fee-included, one contract.
  - Study: 102 trades, +$17.08; with 2c adverse entry stress +$15.04.
  - Holdout: 45 trades, +$5.85; with 2c adverse entry stress +$4.95.
  - Holdout by day under 2c stress:
    - Apr 5: 16 trades, +$1.37
    - Apr 6: 16 trades, +$2.36
    - Apr 7: 13 trades, +$1.22
  - This is the first Apr 1-4-derived candidate that produced a clean fixed
    Apr 5-7 result in the target range for a $100 one-contract bankroll.
- Conservative high-fair-probability sibling:
  - `side_fair_p >= 0.90`
  - `side_fair_p - entry_price - entry_fee >= 0.05`
  - Study with 2c stress: 41 trades, +$13.97.
  - Holdout with 2c stress: 20 trades, +$2.09.
  - Smaller and less smooth than the `.60/.10` gate, but it supports the same
    calibration story.
- Narrow high-entry/depth micropressure pocket:
  - `side_micropressure>=.007`, `side_depth_imbalance>=.75`,
    `entry .60-.95`, `spread<=2`, `quote_speed<=2`,
    `visible_qty>=25`, and recent quote gap <=2s on either side.
  - Study with 2c stress: 27 trades, +$1.69.
  - Holdout with 2c stress: 11 trades, +$0.71.
  - This is too small to deploy, but it may be useful as a confirmation feature.

Interpretation:

- Raw fair edge is not the signal. `fair_edge_cents >= 5` was strongly negative
  in study and should not be used as a standalone rule.
- RR-only, entry-only, distance-only, raw BTC5 continuation, and same-contract
  pullback rules were not stable enough.
- The most defensible structure found in this loop is calibration quality:
  trade only when the model's side probability clears the actual executable
  entry plus Kalshi fee by a large margin.
- This is promising research alpha, not production-ready alpha. It still needs
  a larger untouched Predexon period and live-websocket replay validation before
  replacing a live bot.

### 2026-05-14 - Apr 1-4 Restart, Exact Fair-Edge Result

This is the corrected continuation of the Apr1-4-only restart. The first
fixed validation above used a fee-edge formulation. A fair-value subagent then
identified a stronger raw-cushion formulation that had not been saved as the
canonical validation artifact yet.

New reproducible artifacts:

- Study-only search:
  `scripts/research_btc15m_apr1_4_deep_structure.py`
- Fixed Apr5-7 exact-candidate validation:
  `scripts/validate_btc15m_apr5_7_deep_candidates.py`
- Output:
  `backtest_outputs/btc15m_apr1_4_deep_structure_latest/`
- Output:
  `backtest_outputs/btc15m_apr5_7_deep_candidates_latest/`

Key data limitation discovered by the cross-surface agent:

- This prepared BTC15M side-candidate parquet has exactly one market per event
  in the Apr1-7 slice. It cannot support adjacent-strike surface, graph, or
  cross-strike monotonicity research. Those ideas need a different event-state
  dataset with all active strikes per timestamp.

Exact Apr1-4-derived fair-edge candidates:

- `F2_exact_fair_p60_edge12`:
  `side_fair_p >= 0.60` and `fair_edge_cents >= 12`.
- `F4_exact_no_fair_p65_edge8`:
  `side == "no"`, `side_fair_p >= 0.65`, and `fair_edge_cents >= 8`.
- First qualifying row per event, sorted by event, timestamp, then
  descending `fair_edge_cents`.
- One contract, hold to settlement, Kalshi taker entry fee included.

Fixed Apr5-7 validation, with +2c adverse-entry stress:

| Rule | Study trades | Study PnL | Holdout trades | Holdout PnL | Holdout win | Holdout max DD | Holdout Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| `F2_exact_fair_p60_edge12` | 94 | +16.13 | 33 | +5.71 | 66.7% | -1.54 | 1.76 |
| `F4_exact_no_fair_p65_edge8` | 83 | +12.35 | 27 | +5.68 | 77.8% | -1.09 | 2.00 |
| `F3_exact_fair_p90_edge5` | 41 | +13.97 | 20 | +2.09 | 80.0% | -1.50 | 0.78 |
| `F5_filtered_fair_p60_edge12_spread2` | 79 | +5.22 | 28 | +1.43 | 60.7% | -1.42 | 0.53 |
| `M3_A_or_B_composite` | 90 | +14.68 | 26 | -0.80 | 69.2% | -3.39 | -0.35 |
| `M4_broad_micropressure` | 116 | +10.39 | 62 | +0.06 | 79.0% | -2.23 | 0.02 |

Apr5-7 day breakdown for `F2_exact_fair_p60_edge12`, +2c stress:

- Apr 5: 9 trades, +3.15
- Apr 6: 13 trades, +2.38
- Apr 7: 11 trades, +0.18

Apr5-7 day breakdown for `F4_exact_no_fair_p65_edge8`, +2c stress:

- Apr 5: 11 trades, +2.20
- Apr 6: 9 trades, +2.06
- Apr 7: 7 trades, +1.42

FOK/reprice realism, Apr5-7, next quote within 2s and no worse than +2c:

| Rule | Signals | Filled | Fill rate | Next-quote PnL |
|---|---:|---:|---:|---:|
| `F2_exact_fair_p60_edge12` | 33 | 19 | 57.6% | +2.68 |
| `F4_exact_no_fair_p65_edge8` | 27 | 16 | 59.3% | +0.21 |
| `F5_filtered_fair_p60_edge12_spread2` | 28 | 23 | 82.1% | +1.83 |
| `M4_broad_micropressure` | 62 | 52 | 83.9% | +1.84 |
| `M3_A_or_B_composite` | 26 | 18 | 69.2% | +1.30 |

Statistical sanity checks for the exact fair-edge candidates:

- `F2_exact_fair_p60_edge12`, Apr5-7 +2c stress:
  selected side +5.71, same-timestamp opposite side -3.95,
  edge versus opposite +9.66.
- Day-matched random event-side bootstrap for `F2`, Apr5-7:
  null mean -1.61, null 95th percentile +2.70,
  null 99th percentile +4.20, empirical `p_ge = 0.0020`.
- `F4_exact_no_fair_p65_edge8`, Apr5-7 +2c stress:
  selected side +5.68, same-timestamp opposite side -3.40,
  bootstrap `p_ge = 0.0015`.

Interpretation:

- The best currently found BTC15M structure from this constrained process is
  not path momentum and not standalone micropressure. It is a calibrated
  fair-value edge: the fair probability must clear the quoted entry by a large
  raw cushion.
- The exact rule includes wide-spread quotes. This is not automatically
  non-executable because the simulated fill pays the visible ask, but it is a
  red flag for live realism. The spread-filtered version is still positive but
  materially weaker.
- Do not tune this rule on Apr5-7. The next valid action is to freeze the exact
  rule and evaluate on a separate, untouched live-websocket or later Predexon
  period.

### 2026-05-14 - F2 Exact Rule on Live Websocket Holdout

User asked to test the only meaningful Apr1-4 candidate, F2, on the live
websocket holdout through "right now".

Artifact:

- Script: `scripts/backtest_btc15m_f2_live_ws_holdout.py`
- Output: `backtest_outputs/btc15m_f2_live_ws_20260514_223601/`

Frozen rule:

- `F2_exact_fair_p60_edge12`
- `side_fair_p >= 0.60`
- `fair_edge_cents >= 12`
- First qualifying trade per event.
- One contract, hold to settlement, Kalshi taker entry fee included.

Data:

- Source: local live websocket capture
  `C:\Users\ahmed\.btc_kalshi_bot\btc15m_live_capture.duckdb`.
- Capture rows: 5,169,827 BTC15M top-of-book rows and 73,311 Coinbase ticker
  rows.
- Window: 2026-05-12 10:42:45 UTC through 2026-05-15 04:36:00 UTC.
- Metadata markets: 249.
- Official determined results captured for only 53 markets, so the broad
  replay uses Coinbase-at-close proxy results; official subset is reported
  separately.
- Proxy-vs-official check on F2 trades with both labels:
  23/24 matches, 1 mismatch, 95.83% match rate.

Results:

| Result mode | Trades | PnL | Win | Max DD | Sharpe |
|---|---:|---:|---:|---:|---:|
| Coinbase proxy, no slip | 128 | -9.07 | 51.6% | -11.11 | -1.61 |
| Coinbase proxy, +2c stress | 128 | -11.63 | 51.6% | -13.47 | -2.07 |
| Official subset, no slip | 24 | -0.53 | 58.3% | -2.96 | -0.22 |
| Official subset, +2c stress | 24 | -1.01 | 58.3% | -3.08 | -0.42 |
| Same-timestamp opposite side, proxy +2c | 128 | -0.68 | n/a | -5.52 | -0.12 |

Daily proxy +2c:

| Day | Trades | PnL | Win | Max DD |
|---|---:|---:|---:|---:|
| 2026-05-12 | 29 | -6.03 | 41.4% | -7.32 |
| 2026-05-13 | 47 | -2.32 | 57.4% | -3.70 |
| 2026-05-14 | 44 | -3.53 | 50.0% | -4.42 |
| 2026-05-15 | 8 | +0.25 | 62.5% | -1.10 |

Diagnostics:

- Removing near-strike proxy outcomes (`abs(close_spot - strike) <= $10`) did
  not fix it: 113 trades, -10.58 under +2c.
- Restricting the already-selected F2 trades to spread <=2c did not fix it:
  97 trades, -10.27 under +2c.
- YES trades: 42 trades, -5.64 under +2c.
- NO trades: 86 trades, -5.99 under +2c.
- Main loss bucket was 1-2c spread trades, not the very wide quotes.

Interpretation:

- F2 was promising on Apr1-7 Predexon but failed the later live websocket
  holdout. Treat F2 as a rejected standalone deployment candidate unless a
  new, pre-registered explanation is found and validated on future untouched
  websocket data.
- This is an important negative result because the live websocket replay is the
  most faithful dataset we have.
### 2026-05-15 - Resume, Older Predexon Backfill, and Live-Risk Diagnostics

Context:

- Machine had restarted; BTC1H and BTC15M live scripts were resumed before this
  entry.
- Active live processes at this checkpoint:
  - BTC1H: `.codex_work\run_btc_1hr_late_only_loss_guard_live.py`, PID 14396.
  - BTC15M: `.codex_work\run_btc15m_lowdd_live.py`, PID 23572.
- Both processes had established websocket/TLS connections and active capture
  writes under `C:\Users\ahmed\.btc_kalshi_bot`.

Data backfill:

- Stopped a duplicate KXBTCD Predexon March/April resume job because March and
  April already had useful Predexon KXBTCD coverage and it was mostly
  `skipped_exists`.
- Added `scripts/fetch_kalshi_market_manifest.py` for Kalshi market manifest
  pulls. This was useful for discovery, but direct paginated historical-series
  crawl caused temporary Kalshi 429s, so the high-rate crawl was stopped.
- Added `.codex_work\predexon_backfill_queue_20260515.py`:
  - Uses existing BTC15M Predexon market metadata covering Jan-Mar.
  - Builds missing KXBTCD Jan 7-Feb 28 event manifests by direct
    `/historical/markets?event_ticker=...` calls.
  - Then sequentially runs Predexon at 1 request/sec so the single free API slot
    is not contended.
- Predexon historical orderbooks start around 2026-01-07, so Dec 2025 is not a
  viable Predexon orderbook target.
- Queue log at launch:
  `logs\predexon_old_queue_event_20260515_051401.out.log`.

Subagent findings:

- BTC1H researcher:
  - Current 1h live wrapper still uses the original `research` fair-value model
    with risk guards.
  - Existing evidence suggests side-specific error: YES-only looked stronger
    than NO-only on prior Predexon 1h tests, while the live ledger is mostly NO.
  - Proposed tests: NO-side calibration, volatility prior changes, market
    shrink, BRTI dampening, TTL-bucket calibration, and event freshness gates.
- BTC15M researcher:
  - Live BTC15M is not simply low win-rate. The failure is payoff geometry:
    target-win sizing buys too many contracts at high entries.
  - Ledger sample: high-entry `70-90c` bucket had high win rate but strongly
    negative PnL because one miss erased several capped wins.
  - Also flagged same-event FOK chase behavior and late lower-TTL losses.
- Data engineer:
  - Predexon market discovery has no date filter; use Kalshi official market
    metadata for ticker discovery, then Predexon only for orderbook snapshots.
  - `--no-write-levels` saves disk/serialization but does not reduce request
    count. Keeping levels is useful for future depth research when storage is
    acceptable.
- ML researcher:
  - Added `scripts/prototype_btc_ml_candidates.py`.
  - Added `docs/2026-05-15_ml_researcher_d_btc_ml_candidates.md`.
  - Recommended queue: tabular GBDT, regularized logistic calibrator, then tiny
    MLP only if the first two survive clean validation. Sequence models wait
    until causally ordered state sequences are built.

Focused BTC15M risk replay:

- Script:
  `.codex_work\risk_control_20260513\backtest_btc15m_safety_overlays.py`
- Output:
  `backtest_outputs\btc15m_safety_latest_20260515_051727\`
- Data:
  Snapshot of current `btc15m_live_capture.duckdb`, settled events only, real
  fees, one event-level candidate stream, policies fixed before this run.
- Important rows:
  - Current target-win, entry<=80c, no stop: 87 trades, 52 wins, 35 losses,
    `-$12.54`, max DD `-$59.39`, avg 8.97 contracts, worst trade `-$12.98`.
  - `RR>=0.33`, entry<=60c, target-win: 55 trades, `+$39.99`, max DD
    `-$11.44`, but still large contract counts.
  - Loss-cap safer candidates:
    - `safe_loss2_entry65_rr33_daily5_2loss`: 24 trades, `+$20.68`, max DD
      `-$4.16`, worst trade `-$2.00`.
    - `safe_loss1_entry65_rr33_daily5_2loss`: 24 trades, `+$7.60`, max DD
      `-$2.27`, worst trade `-$0.89`.
- Interpretation:
  - The most robust immediate BTC15M improvement is not a new alpha signal; it
    is replacing target-win sizing with max-loss sizing plus entry/RR gates.
  - Do not deploy target-win sizing at higher bankroll. It is exactly the
    failure mode that created recent live drawdown.

Focused BTC1H core-model research:

- Script:
  `scripts\core_model_research.py`
- Output:
  `backtest_outputs\core_model_focus_20260515_051558\`
- Split:
  - Train before 2026-04-01 UTC.
  - Validation Apr 1-20.
  - Test Apr 21 onward.
  - Websocket capture was not used to rank variants.
- Best focused historical variants:
  - `brti_065`: 106 trades, all `+$8.08`, test `+$5.78`, validation `+$1.75`,
    all win rate 76.4%, max DD `-$2.68`.
  - `ewma60_logn_blend`: 21 trades, all `+$4.10`, test `+$2.82`, all win rate
    85.7%, max DD `-$0.84`.
  - Baseline current model: 38 trades, all `+$3.33`, test `+$2.24`, all win
    rate 76.3%, max DD `-$1.39`.
- Interpretation:
  - `brti_065` is a promising 1h fair-value candidate because it increases
    trade count and test PnL versus baseline, but it is still historical-candle
    evidence and must get a websocket counterfactual replay before deployment.
  - `ewma60_logn_blend` is lower-capacity but cleaner drawdown. It is worth
    replaying on future websocket data.

Current next steps:

1. Let the Jan-Feb KXBTCD and Jan-Mar BTC15M Predexon queue continue.
2. Once older data is available, rerun BTC1H `brti_065` and BTC15M loss-cap
   policies on the larger Predexon set.
3. Before changing the live 1h fair-value model, build a websocket
   counterfactual replay for `brti_065` and `ewma60_logn_blend`.
4. For BTC15M, the deployment candidate to test next is max-loss sizing
   (`$1-$2` risk cap), `entry<=0.60-0.65`, `RR>=0.33`, no target-win sizing.

### 2026-05-15 - BTC15M Rejection, Capture-Only Pause, and 1H Candidate Gate

State change:

- BTC1H live remained running:
  `.codex_work\run_btc_1hr_late_only_loss_guard_live.py`, PID 14396.
- BTC15M live trader was stopped after the target-win strategy failed broad
  validation. It was replaced with capture-only:
  `python -u scripts\btc15m_live_capture.py --refresh-sec 10 --health-sec 30`,
  PID 3768 at this checkpoint.
- This preserves the BTC15M websocket dataset without submitting more orders
  from the rejected lowdd target-win strategy.
- Predexon old-data queue remained running and was scoped correctly:
  KXBTCD Jan 7-Feb 28 only; KXBTC15M Jan 7-Mar 31.
- KXBTCD Jan-Feb manifest completed with 88,139 market rows across 1,188
  events:
  `data\predexon_kalshi_orderbooks\market_manifests\kxbtcd_20260107_20260301.parquet`.
- Patched `scripts\download_predexon_kalshi_orderbooks.py` so local manifests
  that already include `market_ticker` do not collide with raw Kalshi `ticker`
  during normalization.
- Restarted top-only old-data queue:
  `logs\predexon_old_queue_toponly_20260515_055016.out.log`.
  It is running at 1 request/sec with `--no-write-levels`.

BTC15M target-win postmortem:

- Latest settled websocket replay:
  `backtest_outputs\btc15m_safety_latest_20260515_051727\candidate_stream_settled.csv`
  had 42,192 settled candidate rows across 87 settled events, with capture
  health showing 5,689,160 top-book rows, 78,822 BTC ticks, and
  `capture_dropped=0`.
- Current target-win baseline:
  87 trades, 52 wins, 35 losses, `-$12.54`, max DD `-$59.39`.
- The high-entry tail is fatal:
  `70-80c` entries went roughly 22/33 wins in live replay but lost about
  `-$64` under target-win sizing because a single miss loses around `$12`
  while winners are capped near `$3`.
- Broad April Predexon check rejected the same structure:
  current-like target-win rules were materially negative across April, even when
  RR and entry caps improved the recent live slice.
- Conclusion:
  the lowdd BTC15M signal is not approved for live trading. Loss-cap sizing can
  make recent websocket replay look survivable, but that is risk control, not
  proven alpha. Keep collecting BTC15M data and do not redeploy target-win.

BTC15M Apr1-4 research candidates:

- Apr1-4 study-only candidates produced apparently strong patterns:
  `A_early_btc5_momentum_confirmed`, `B_mid_ttl_micropressure_depth`,
  `C_late_reversal_after_contract_drop`, and combined `ABC_priority`.
- Apr5-7 frozen validation narrowed the only still-interesting family to
  fair-value edge:
  - `F2_exact_fair_p60_edge12`: holdout 33 trades, `+$6.37`, 2c-stressed
    `+$5.71`, win 66.7%, max DD 2c `-$1.54`.
  - `F4_exact_no_fair_p65_edge8`: holdout 27 trades, `+$6.21`,
    2c-stressed `+$5.68`, win 77.8%, max DD 2c `-$1.09`.
- But F2 failed local websocket replay:
  `backtest_outputs\btc15m_f2_live_ws_20260514_223601\f2_live_ws_summary.csv`
  showed official subset 24 trades, `-$0.53` raw / `-$1.01` with 2c stress,
  and proxy full replay 128 trades, `-$9.07` raw / `-$11.63` with 2c stress.
- Conclusion:
  F2/F4 remain research-only; they are not deployable until a new variant
  survives live websocket replay.

BTC15M ML candidates:

- `backtest_outputs\btc15m_april_top_models_20260514_clean\` trained on the
  requested chronological April 75/12.5/12.5 split.
- XGBoost and LightGBM were positive but tiny on the final test:
  - LightGBM: test 6 trades, `+$0.92`, 2c-stressed `+$0.80`, win 83.3%.
  - XGBoost: test 8 trades, `+$1.14`, 2c-stressed `+$0.98`, win 87.5%.
- MLP failed final test:
  26 trades, `-$5.29`, 2c-stressed `-$5.81`, win 42.3%.
- Conclusion:
  XGBoost/LightGBM are eligible only for paper shadow/live websocket holdout.
  MLP is rejected.
- Added `scripts\backtest_btc15m_ml_live_ws_holdout.py` to score the frozen
  April XGBoost/LightGBM models on causal BTC15M websocket capture.
- Bounded May 15 live replay:
  `python -u scripts\backtest_btc15m_ml_live_ws_holdout.py --start 2026-05-15T00:00:00Z --end 2026-05-15T11:45:00Z --out backtest_outputs\btc15m_ml_live_ws_may15_smoke_20260515_0605`
  - XGBoost: 8 trades, raw `+$0.20`, 2c-stressed `+$0.04`, win 75%,
    max DD 2c `-$1.23`.
  - LightGBM: 0 trades under its frozen April validation gate.
- Interpretation:
  XGBoost is not rejected by this small live slice, but the edge is effectively
  flat after execution stress. It remains a paper-shadow candidate only.

BTC1H observed-decision gate:

- Ran:
  `python -u scripts\core_model_observed_decision_gate.py --scorecard backtest_outputs\core_model_focus_20260515_051558\core_model_scorecard.csv --output-dir backtest_outputs\core_model_observed_gate_20260515_0600`
- This is not a full counterfactual replay. It recomputes finalist model gates
  on observed captured/ledger decisions.
- Results weakened the historical `brti_065` story:
  `brti_065` had 24 capture-passed trades for `+$1.66`, but live-only passed
  was only 15 trades for `+$0.25`, and recent ledger-passed actual was
  `-$2.91`.
- Cleaner observed gates:
  - `ewma60_logn_blend`: capture-passed 12 trades, `+$2.70`; live-only passed
    7 trades, `+$0.92`; zero recent ledger-passed trades.
  - `blend_85_15_rv60`: capture-passed 14 trades, `+$3.23`; live-only passed
    8 trades, `+$1.21`; recent ledger-passed `-$0.12`.
  - `rv_down60_blend`: capture-passed 14 trades, `+$3.18`; live-only passed
    7 trades, `+$0.75`; recent ledger-passed `+$0.31` on one trade.
- Conclusion:
  do not deploy `brti_065` from historical results alone. The next 1h work item
  is a true websocket counterfactual replay for `ewma60_logn_blend`,
  `blend_85_15_rv60`, `rv_down60_blend`, and baseline.
- Added `scripts\replay_btc1h_core_ws_counterfactual.py` for a true KXBTCD
  websocket counterfactual replay. It streams top rows by `received_at_ns`,
  uses causal BTC as-of data, reconstructs close/strike from tickers, and
  evaluates baseline plus core model variants.
- Smoke compile and tiny replay passed, but a multi-day dense run over
  `live_capture_gapless_20260512_paused.duckdb` was too slow and hit the
  foreground timeout before output. Before relying on this routinely, optimize
  by event/window pruning and vectorizing the variant scan.

Data-fidelity notes:

- Existing Predexon/live BTC15M overlap:
  `backtest_outputs\btc15m_predexon_ws_overlap_20260514_181535`.
- Overlap window was 2026-05-12 10:42:45 UTC to 22:29:02 UTC, 11 common
  events/markets.
- Predexon quote alignment to local websocket was close at snapshot times
  (median age about 0.055s, p95 about 2.956s), but dense local websocket data
  had many more intervening states. Use Predexon for broad research, not final
  promotion.

2026-05-15 BTC15M April multisplit fair-value rejection:

- Added `scripts\research_btc15m_april_multisplit_search.py`.
- Data:
  `backtest_outputs\predexon_btc15m_april_execution_20260514_182852\features_snapshot_level.parquet`
  plus `data\btc15m_historical_datamart\spot_1m.parquet`.
- Pre-registered 1,237 causal rules before scoring:
  fair-value grids, microstructure pressure grids, path/BTC regimes, and
  fair+book-pressure combinations.
- Splits:
  Apr 1-4 study, Apr 5-7 validation 1, Apr 8-14 validation 2,
  Apr 15-30 final April holdout. All PnL below includes taker fees and 2c
  adverse-entry stress, first signal per event.
- Output:
  `backtest_outputs\btc15m_april_multisplit_search_20260515`.
- Top April-selected candidate:
  `fair_fp60_edge12_ttl5-12_sp2_e2-60`
  (`side_fair_p >= 0.60`, fee-adjusted fair edge `>= 12c`, TTL 5-12m,
  spread `<= 2c`, entry `2c-60c`).
  It passed April selection windows:
  48 trades `+$2.39` on Apr 1-4, 15 trades `+$2.61` on Apr 5-7,
  33 trades `+$4.48` on Apr 8-14, and remained only mildly positive on
  Apr 15-30 with 183 trades `+$2.54`.
- Parameterized `scripts\backtest_btc15m_f2_live_ws_holdout.py` so frozen
  fair-value rules can be replayed on websocket capture without duplicating
  replay logic.
- Live websocket promotion gate rejected the top candidate:
  `backtest_outputs\btc15m_fair_ttl5_12_live_ws_20260515`.
  On local websocket capture 2026-05-12 10:42:45 UTC to
  2026-05-15 22:28:25 UTC, it produced 86 proxy-settled trades for
  `-$9.27` at 2c stress, win rate 46.5%, max DD `-$11.24`.
  Official-result subset was 24 trades for `-$3.75`, win rate 41.7%.
- Replayed all 107 April-selected fair-value variants on the same websocket
  holdout:
  `backtest_outputs\btc15m_april_selected_fair_live_ws_20260515`.
  The best proxy variant was only `+$0.37` and was `-$3.44` on official
  subset; most variants were negative.
- Conclusion:
  the Predexon fair-value family is useful for generating hypotheses, but the
  live websocket holdout rejects it as deployable BTC15M alpha. Do not deploy
  `fair_fp60_edge12_ttl5-12_sp2_e2-60`, broad F2, or neighboring fair-value
  variants without a new causal explanation that also survives websocket
  capture.

2026-05-15 BTC15M F2 robust-sizing and transfer audit:

- Rechecked the April-selected F2/fair-value candidate
  `fair_fp60_edge12_ttl5-12_sp2_e2-60` on Apr 1-14 with robust sizing:
  `backtest_outputs\btc15m_f2_robust_apr1_14_20260515\summary.csv`.
  One contract was 96 trades, 62.5% win, `+$11.40`, max DD `-$2.54`.
  Loss-cap `$1` was `+$15.18`, max DD `-$4.00`. Loss-cap `$2` was
  `+$37.91`, max DD `-$9.62`. Daily-stop variants reduced drawdown but
  cut trade count roughly in half.
- Rechecked the current BTC15M lowdd/target-win signal stream on Apr 1-14
  with the same robust sizing:
  `backtest_outputs\btc15m_robust_sizing_apr1_14_20260515\summary.csv`.
  Baseline current one-contract was 54 trades, 29.6% win, `-$11.62`,
  max DD `-$11.62`. Baseline current target-win sizing was `-$88.04`,
  max DD `-$85.29`. The best robust filtered one-contract variant
  (`entry_le65_side_move_18_30c_btc_lt8`) was still slightly negative:
  14 trades, 42.9% win, `-$0.61`, max DD `-$1.96`.
- Independent audit conclusion:
  the F2 predicate is equivalent between April Predexon and live websocket
  replay. Same fair probability, fair-edge, TTL, spread, and entry gates; no
  direct signal lookahead found; fee, side mapping, visible top-ask entry, and
  first-signal-per-event behavior match for inspected artifacts.
- Important caveats:
  `scripts\research_btc15m_april_multisplit_search.py` ranked candidates with
  a score that includes a holdout penalty, so `top30_rules.csv` is not a pure
  pre-holdout ranking artifact. `passes_selection` itself still uses study +
  validation splits. Live F2 replay is mostly proxy-settled: only 24/86 trades
  had official result messages, and proxy-vs-official matched 22/24.
- Diagnostic summary:
  `backtest_outputs\btc15m_f2_diagnostics_20260515\`.
  Apr1-14 F2: 96 trades, 62.5% win, `+$9.48` with 2c stress.
  Apr15-30 F2: 183 trades, 55.2% win, `+$2.54` with 2c stress.
  May live-WS F2: 86 trades, 46.5% win, `-$9.27` with 2c stress.
  May live-WS selected trades were more marginal: average entry about 53.3c,
  lower visible top quantity, and average fair-edge margin about 13.6c versus
  about 18.4c on Apr1-14.
- Decision:
  F2 is not deployable. The current lowdd signal is also not rescued by simple
  robust sizing on Apr1-14. The next viable path is not more raw fair-value
  threshold grids; it is either a calibrated tabular model/regime detector that
  treats fair value as one feature, or a genuinely live-validated
  execution-quality filter collected after rules are frozen.
- Follow-up strict-gate test:
  a post-filter diagnostic suggested `fair_p>=65%`, `edge>=12c`,
  `entry<=55c`, `visible_qty>=50` might remove bad F2 rows, but exact replay
  rejected it.
  - Exact live websocket replay:
    `backtest_outputs\btc15m_fair_fp65_edge12_entry55_q50_live_ws_20260515`.

2026-05-15/16 BTC15M F2 refreshed robust transfer check:

- Reran the exact April-selected F2 rule on Apr 1-14 using the robust execution
  rules requested:
  `backtest_outputs\btc15m_f2_robust_apr1_14_20260515_204801`.
  Rule: `side_fair_p>=0.60`, `fair_edge_cents>=12`, TTL `5-12m`,
  spread `<=2c`, entry `2c-60c`, first signal per event, top-ask taker fill,
  visible top-quantity cap, Kalshi taker fee, official settlement labels.
  One-contract was 96 trades, `+$11.40`, 62.5% win, max DD `-$2.54`,
  Sharpe `2.43`; with `+2c` adverse-entry stress it was `+$9.48`, max DD
  `-$2.70`, Sharpe `2.02`.
- Refreshed exact F2 on live BTC15M websocket capture through
  `2026-05-16 02:51:55 UTC`:
  `backtest_outputs\btc15m_f2_exact_live_ws_refresh_20260515_205154`.
  The exact F2 still fails on live replay: 101 proxy-settled trades,
  `-$9.46` with `+2c` stress, 47.5% win, max DD `-$11.36`; official subset
  was 25 trades, `-$3.33`, 44.0% win, max DD `-$4.12`.
- Added reusable transfer diagnostic:
  `scripts\analyze_btc15m_f2_transfer.py`.
  Output: `backtest_outputs\btc15m_f2_transfer_diagnostics_20260515_205531`.
  It compares Apr1-14, Apr15-30, and refreshed WS holdout without tuning on
  WS. Base F2 deteriorates across samples:
  Apr1-14 `+$9.48`, Apr15-30 `+$2.54`, WS `-$9.46`.
- The most interesting fixed subset is
  `ttl_10_12_entry_le55_q50`: TTL `10-12m`, entry `<=55c`, visible top qty
  `>=50`. Results with `+2c` stress:
  Apr1-14: 16 trades, `+$6.02`, 87.5% win, max DD `-$0.53`.
  Apr15-30: 25 trades, `+$3.24`, 64.0% win, max DD `-$2.10`.
  Refreshed WS proxy: 16 trades, `+$3.65`, 75.0% win, max DD `-$0.59`.
  Refreshed WS official subset: 3 trades, `+$1.38`, 100% win.
- Streak analysis was not robust enough to use as a live rule:
  Apr1-14 improved after 2+ losses, Apr15-30 worsened after 2+ losses, and
  WS also worsened after 2+ losses. Loss/win streak itself is therefore a weak
  state variable. The real repeatable clue is time-to-close/execution quality:
  the 8-10m TTL bucket failed across Apr15-30 and WS, while 10-12m held up.
- Current decision:
  do not deploy broad F2. Freeze `ttl_10_12_entry_le55_q50` as a candidate for
  future websocket-forward validation only. It is promising because it survived
  Apr1-14, Apr15-30, and refreshed WS, but sample size is still too small for a
  production swap.

2026-05-15 operational note:

- Restarted the BTC1H `high_conf_80` paper shadow after the empty-event refresh
  patch so future forward-validation capture uses current code.
  Process: `scripts\btc_1hr_high_conf80_shadow.py`, PID `19736`.
  Log: `logs\btc_1hr_high_conf80_shadow_20260515_205645.out.log`.
  It started in paper mode, `signal_strategy=high_conf_80`, `contracts=1`,
  `shadow_bankroll=$100`, separate capture DB
  `C:\Users\ahmed\.btc_kalshi_bot\btc_1hr_high_conf80_shadow_capture.duckdb`.

2026-05-15 BTC15M forward-validation setup:

- Patched `scripts\btc15m_lowdd_live.py` so the H02/fair-value path supports
  exact frozen-candidate gates:
  `BTC15M_H02_MIN_SIDE_PROB` and `BTC15M_H02_MIN_VISIBLE_QTY`.
- Added `scripts\btc15m_f2_ttl10_12_shadow.py`, a dry-run-only wrapper for
  the frozen candidate:
  `side_fair_p>=0.60`, fair edge after fee `>=12c`, TTL `10-12m`,
  spread `<=2c`, entry `2c-55c`, visible top qty `>=50`, one max contract.
  It uses isolated DBs:
  `C:\Users\ahmed\.btc_kalshi_bot\btc15m_f2_ttl10_12_shadow_capture.duckdb`
  and
  `C:\Users\ahmed\.btc_kalshi_bot\btc15m_f2_ttl10_12_shadow_trades.db`.
- Started the BTC15M F2 TTL 10-12 shadow as PID `4196`.
  Log: `logs\btc15m_f2_ttl10_12_shadow_20260515_210157.out.log`.
  It is `mode=dry-run`; it cannot submit orders.
- Added `scripts\test_btc15m_shadow_config.py`; validation:
  `python -m pytest scripts\test_btc15m_shadow_config.py -q --basetemp .pytest-codex-tmp`
  passed `3 passed`. The test checks exact env gates, dry-run wrapper text,
  and that the live H02 signal builder rejects visible top quantity below 50.
- Patched `.codex_work\predexon_backfill_queue_20260515.py` so the future
  KXBTC15M Jan-Mar Predexon step uses `--write-levels`, because the candidate
  needs level-0 visible quantity. Restarted the Predexon queue as PID `15964`
  with child PID `24040`. It is still working through/resuming KXBTCD first.
    59 proxy-settled trades, `-$5.76` with 2c stress, 47.5% win, max DD
    `-$6.61`; official subset 14 trades, `-$3.03`.
  - Exact April replay:
    `backtest_outputs\btc15m_fair_fp65_edge12_entry55_q50_april_exact_20260515`.
    Apr1-4 `+$2.98`, Apr5-7 `+$2.21`, Apr8-14 `+$0.88`, but Apr15-30
    `-$1.14`.
  - Reason for rejection:
    filtering already-selected F2 first hits was a selection artifact. The
    actual stricter rule chooses different first qualifying states and fails
    both April holdout and live websocket.

2026-05-15 BTC15M live-compatible ML candidate audit:

- Replayed the existing April-trained models on daily websocket slices rather
  than one giant dense run, to avoid memory issues:
  `backtest_outputs\btc15m_ml_comparison_ws_agg_20260515\aggregate_summary.csv`.
- Original April XGBoost (`backtest_outputs\btc15m_april_top_models_20260514_clean`):
  - April validation/test evidence was small but positive: validation 21
    trades `+$2.01` under 2c stress, test 8 trades `+$0.98`.
  - May 12-15 websocket diagnostic: 31 trades, 80.6% win, `+$2.06` under
    2c stress; 3/4 days positive, worst day `-$0.20`.
  - Feature audit:
    `backtest_outputs\btc15m_ml_feature_audit_20260515\summary.csv`.
    Only 4/67 features were missing from live reconstruction; XGBoost placed
    about 2.6% of split importance on those missing features. This is much
    cleaner than LightGBM.
- Original April LightGBM:
  - May 12-15 websocket diagnostic: 10 trades, `+$1.39` under 2c stress.
  - Rejected as less trustworthy because it placed about 18% of split
    importance on live-missing features (`rv_15m`, `rv_ratio_15_60`, and full
    depth fields).
- Added live-compatible training mode to `scripts\train_btc15m_april_top_models.py`:
  - `--live-compatible-only` drops features not reconstructed in live websocket
    replay.
  - Added `logistic_calibrator` as a regularized baseline.
- Live-compatible model artifact:
  `backtest_outputs\btc15m_live_compatible_models_20260515`.
  - Live-compatible XGBoost validation: 12 trades, `+$2.71` under 2c stress.
  - Live-compatible XGBoost April final test: only 1 trade, `+$0.20` under
    2c stress. This is too low-capacity for promotion.
  - Logistic calibrator failed April test: 30 trades, `-$2.25` under 2c stress.
- Live-compatible May websocket diagnostic:
  `backtest_outputs\btc15m_ml_comparison_ws_agg_20260515\aggregate_summary.csv`.
  - XGBoost: 26 trades, 76.9% win, `+$0.80` under 2c stress, positive on all
    four May days.
  - Logistic: 192 trades, 56.3% win, `-$14.38` under 2c stress. Rejected.
- Decision:
  the only BTC15M candidate that did not immediately fail live websocket replay
  is the live-compatible XGBoost, but it is not deployable because April final
  test capacity was only one trade and the May websocket data has already been
  inspected. It is eligible for paper-shadow or a frozen future websocket
  holdout only. Do not tune thresholds on May 12-15.
- Holdout freeze:
  created `docs\2026-05-15_btc15m_future_holdout_freeze.md` at
  2026-05-15T23:36:03Z. From that timestamp forward, newly collected BTC15M
  websocket data may be used as a clean future holdout for the frozen
  live-compatible XGBoost candidate. Do not change model/gate/features/sizing
  before that evaluation.

2026-05-15 BTC1H core-model websocket counterfactual replay:

- Patched `scripts\replay_btc1h_core_ws_counterfactual.py` to make the replay
  usable on the dense live capture:
  - It now prunes each event to the causal decision window
    `close-20m <= receive_time <= close-5m` instead of scanning every KXBTCD row.
  - It seeds the event book from rows at or before the decision window start,
    bounded to the current hourly event, so the orderbook state remains causal.
  - It still applies every top-of-book update to state, but evaluates the model
    at an explicit `--scan-stride-sec` cadence. This avoids pretending we can
    recompute 188-strike fair values tens of thousands of times per event.
- Data:
  `data\live_capture_gapless\live_capture_gapless_20260512_paused.duckdb`,
  table `ws_orderbook_top_dedup`, KXBTCD events from
  2026-05-06T01:40:00Z through 2026-05-12T23:55:00Z.
  One missing captured settlement,
  `KXBTCD-26MAY0800-T79599.99`, was patched from public Kalshi as `NO`.
- Outputs:
  - 1s stride:
    `backtest_outputs\btc1h_core_ws_counterfactual_fast_20260515`.
  - 5s stride:
    `backtest_outputs\btc1h_core_ws_counterfactual_fast_stride5_20260515`.
  - 10s stride:
    `backtest_outputs\btc1h_core_ws_counterfactual_fast_stride10_20260515`.
  - 15s stride:
    `backtest_outputs\btc1h_core_ws_counterfactual_fast_stride15_20260515`.
  - 20s stride:
    `backtest_outputs\btc1h_core_ws_counterfactual_fast_stride20_20260515`.
  - 30s stride:
    `backtest_outputs\btc1h_core_ws_counterfactual_fast_stride30_20260515`.
  - Consolidated table:
    `backtest_outputs\btc1h_core_ws_counterfactual_stride_sweep_20260515.csv`.
- Public-patched results, one contract, all fees included:

```
stride  variant                  trades  pnl    win%   maxDD  sharpe
1s      baseline_emp70_logn_rv60 65      -1.08  58.5%  -4.50  -0.28
1s      ewma60_logn_blend        64      -3.44  54.7%  -5.90  -0.89
1s      blend_85_15_rv60         69      -3.35  58.0%  -6.09  -0.84
1s      rv_down60_blend          69      -2.74  56.5%  -5.88  -0.69
1s      brti_065                 81      -3.49  58.0%  -7.44  -0.83

5s      best was blend_85_15     59      -0.48  61.0%  -4.15  -0.13
10s     best was ewma60          38      -0.86  57.9%  -3.30  -0.30
15s     ewma60_logn_blend        39      +3.68  69.2%  -1.53  +1.37
15s     rv_down60_blend          41      +2.14  65.9%  -1.90  +0.74
20s     blend_85_15_rv60         38      +1.60  65.8%  -1.51  +0.56
30s     baseline_emp70_logn_rv60 24      +2.79  75.0%  -1.46  +1.28
30s     rv_down60_blend          26      +2.10  69.2%  -1.37  +0.92
```

- Interpretation:
  the original fast websocket-style core variants are rejected on this live
  capture. Slower scan cadence appears to act as a loss-avoidance/quote-stability
  filter, but it is not yet deployable alpha because the sign flips sharply
  between 10s and 15s and the capture window is only about one week. Treat
  `scan_stride_sec` as a pre-registered hypothesis for the next validation pass,
  not as a parameter to tune on this already-inspected data.
- Outside check on Predexon BTC1H Apr1-14:
  `scripts\backtest_predexon_orderbooks.py --series KXBTCD --start
  2026-04-01T00:00:00Z --end 2026-04-15T00:00:00Z --scan-stride-sec 30`
  wrote `backtest_outputs\predexon_btc1h_apr1_14_stride30_20260515`.
  This is not the exact core-model replay above, but it tests the current 1H
  variant family on an outside snapshot source with the same slower-cadence
  idea.
  - `current_1h_late_loss_guard`: 7 trades, `+$1.56`, 85.7% win, max DD
    `-$0.62`.
  - `high_conf_80`: 4 trades, `+$1.34`, 100% win.
  - `research_original_late`: 9 trades, `+$0.60`, 66.7% win.
  - `market_shrink_shape_adjacent`: 4 trades, `+$0.52`, 75.0% win.
  A matching 1-second Apr1-14 Predexon run was attempted but did not complete
  within 15 minutes and was stopped after only writing `data_report.json`; do
  not count it as evidence. The April 30-second result is directionally
  supportive of a quote-stability/cadence gate, but sample size is too small
  for deployment.

2026-05-15 BTC1H high-confidence core candidate:

- Added `high_conf_80` to `scripts\replay_btc1h_core_ws_counterfactual.py`:
  same current/core BTC1H fair value, but require side probability at least
  80% (`min_yes_p=0.80`, `max_no_p=0.20`). No sizing change; one contract.
- Rationale:
  prior losses were not primarily a sizing issue after robust caps. The model
  was taking too many marginal 65-70% side-probability trades whose edge was
  not reliable live. A stricter probability gate should reduce trades while
  preserving only large model/market disagreements.
- Predexon outside validation, 30-second scan cadence:
  - Apr1-14:
    `backtest_outputs\predexon_btc1h_apr1_14_stride30_20260515`.
    `high_conf_80`: 4 trades, `+$1.34`, 100.0% win, max DD `$0.00`.
  - Apr15-30:
    `backtest_outputs\predexon_btc1h_apr15_30_stride30_20260515`.
    `high_conf_80`: 18 trades, `+$1.33`, 72.2% win, max DD `-$1.51`.
  - Apr1-30 combined:
    `backtest_outputs\predexon_btc1h_apr1_30_stride30_combined_20260515.csv`.
    `high_conf_80`: 22 trades, `+$2.67`, 77.3% win, max DD `-$1.51`.
- May live websocket counterfactual:
  tested the same `high_conf_80` on
  `data\live_capture_gapless\live_capture_gapless_20260512_paused.duckdb`
  across scan cadences without changing the predicate:

```
stride  trades  pnl    win%   maxDD  sharpe
1s      39      +3.07  76.9%  -2.11  +1.15
5s      29      +1.95  75.9%  -1.72  +0.83
10s     26      +2.82  80.8%  -0.96  +1.36
15s     24      +3.25  83.3%  -1.00  +1.74
20s     18      +2.34  83.3%  -1.00  +1.44
30s     18      +2.59  83.3%  -1.00  +1.59
```

- May WS daily notes:
  at 1s stride, 5 of 6 days were positive, but 2026-05-07 was bad:
  4 trades, `-$1.80`, 25% win. At 15s/30s stride, 2026-05-07 remained
  slightly negative (`-$0.41`) and the rest of the days were positive.
- Decision:
  `high_conf_80` is the best BTC1H candidate found in this loop. It passes
  Predexon Apr1-14, Predexon Apr15-30, and May WS cadence sensitivity. It is
  still not final deployment-approved because May WS has now been inspected and
  Predexon is snapshot-provider data, not exact receive-time websocket replay.
  Promote it to paper-shadow or a frozen future websocket holdout. Do not tune
  the 80% threshold or scan stride on May 6-12.
- Holdout freeze:
  created `docs\2026-05-16_btc1h_high_conf80_holdout_freeze.md` at
  2026-05-16T01:37:07Z. Future BTC1H websocket data after that timestamp is the
  clean validation set for this exact rule.

2026-05-15/16 BTC1H older Predexon validation and no-chase variants:

- Added `--one-hour-variant-regex` to
  `scripts\backtest_predexon_orderbooks.py` so older Predexon validation can
  focus on frozen BTC1H candidates without recomputing every unrelated variant.
- Older Predexon outside validation:
  `backtest_outputs\predexon_btc1h_feb10_mar31_stride30_highconf_20260515`.
  This used downloaded KXBTCD Predexon snapshots from 2026-02-10 through
  2026-03-31 at 30s scan cadence.
  - `current_1h_late_loss_guard`: 14 trades, `+$3.36`, 85.7% win, max DD
    `-$0.56`.
  - `research_original_late`: 15 trades, `+$2.89`, 80.0% win, max DD `-$0.61`.
  - `high_conf_80`: 8 trades, `+$0.76`, 75.0% win, max DD `-$0.83`.
  - `high_conf_80_no_chase`: 8 trades, `+$1.57`, 87.5% win, max DD `-$0.43`.
  - `high_conf_80_entry70_no_chase`: 5 trades, `+$0.84`, 80.0% win, max DD
    `-$0.43`.
- Predexon Feb10-Apr30 combined at 30s:
  `backtest_outputs\predexon_btc1h_feb10_apr30_stride30_combined_20260515.csv`.

```
model                         trades  pnl    win%   maxDD
current_1h_late_loss_guard    46      +5.03  71.7%  -1.85
research_original_late        50      +4.07  68.0%  -1.85
high_conf_80_no_chase         26      +3.88  80.8%  -1.25
high_conf_80                  30      +3.43  76.7%  -1.51
high_conf_80_entry70_no_chase 21      +2.63  76.2%  -1.25
```

- Ported the pre-existing 10m no-chase guard into
  `scripts\replay_btc1h_core_ws_counterfactual.py`:
  - YES is blocked if BTC rose at least `$150` over the prior 10 minutes.
  - NO is blocked if BTC fell at least `$150` over the prior 10 minutes.
  - Also added `high_conf_80_entry70_no_chase`, which caps entry at 70c.
- May WS no-chase validation:
  `backtest_outputs\btc1h_high_conf80_no_chase_ws_stride_sweep_20260515.csv`.

```
variant                         stride  trades  pnl    win%   maxDD
high_conf_80_no_chase           5s      25      +0.73  72.0%  -1.77
high_conf_80_no_chase           15s     21      +3.35  85.7%  -0.74
high_conf_80_no_chase           30s     17      +2.40  82.4%  -0.92
high_conf_80_entry70_no_chase   5s      22      +2.12  77.3%  -1.00
high_conf_80_entry70_no_chase   15s     19      +4.15  89.5%  -0.71
high_conf_80_entry70_no_chase   30s     13      +1.37  76.9%  -0.92
```

- Decision:
  no-chase is useful but not strictly superior. It improves some May WS
  cadences and older Predexon drawdown, but plain `high_conf_80` remains the
  cleaner primary frozen candidate because it is simpler, positive at 1s through
  30s on May WS, and has stronger Predexon Apr1-30 total PnL than entry-capped
  no-chase. Keep no-chase as a secondary candidate for future holdout, not a
  replacement unless future WS data confirms it.

2026-05-15/16 BTC1H high-confidence paper-shadow implementation:

- Added high-confidence strategy support to `scripts\btc_1hr_research_live.py`:
  - `high_conf_80`: current/core BTC1H research signal, but requires side
    probability at least 80%.
  - `high_conf_80_no_chase`: same, plus blocks YES after BTC is up at least
    `$150` over the prior 10 minutes and blocks NO after BTC is down at least
    `$150` over the prior 10 minutes.
  - `high_conf_80_entry70_no_chase`: same no-chase guard, but caps entry at
    70c.
- Added paper-shadow wrappers:
  - `scripts\btc_1hr_high_conf80_shadow.py`
  - `scripts\btc_1hr_high_conf80_no_chase_shadow.py`
- The wrappers default to paper mode, one-contract flat sizing, `$100`
  shadow bankroll, 5-20 minute TTL, and separate local DB files under
  `~\.btc_kalshi_bot\` so they do not lock or write into the live bot's
  `research_live_capture.duckdb`.
- Validation:
  - `python -m py_compile scripts\btc_1hr_research_live.py
    scripts\btc_1hr_high_conf80_shadow.py
    scripts\btc_1hr_high_conf80_no_chase_shadow.py
    scripts\replay_btc1h_core_ws_counterfactual.py
    scripts\backtest_predexon_orderbooks.py`
  - `python -m pytest scripts\test_research_live_safety.py -q --basetemp
    .pytest-codex-tmp`
  - Result: 42 passed, 1 benign pandas rolling warning.
- Operational note:
  the current live 1H capture DB cannot be copied or opened read-only while the
  live writer owns it on Windows. Future post-freeze replay either needs a
  paused/copyable DB, a separately running paper-shadow DB, or a future capture
  export mechanism in the live process. The new paper-shadow wrappers avoid
  this by writing their own trade/capture databases from startup.
- Started the primary paper shadow:
  - PID at start/check: `3504`.
  - Command: `python -u scripts\btc_1hr_high_conf80_shadow.py`.
  - stdout: `logs\btc_1hr_high_conf80_shadow_20260515_203952.out.log`.
  - stderr: `logs\btc_1hr_high_conf80_shadow_20260515_203952.err.log`.
  - trade DB: `~\.btc_kalshi_bot\btc_1hr_high_conf80_shadow.db`.
  - capture DB: `~\.btc_kalshi_bot\btc_1hr_high_conf80_shadow_capture.duckdb`.
  - Startup log confirmed `mode=paper`, `signal_strategy=high_conf_80`,
    `contracts=1`, `ttl=5.0-20.0m`, and separate capture DB. It initially saw
    no eligible event because the active hour was just outside the 20-minute
    TTL window.

2026-05-15/16 BTC15M F2 robust replay and implementation correction:

- User-requested Apr1-14 robust replay:
  `backtest_outputs\btc15m_f2_robust_apr1_14_20260515_204801`.
  Rule: `fair_fp60_edge12_ttl5-12_sp2_e2-60`, causal Predexon snapshots,
  first qualifying signal per event, top-ask taker fill, visible top quantity
  cap, official settlement labels, Kalshi fees, and +2c adverse-entry stress.
  - 1 contract: 96 trades, `+$11.40`, 62.5% win, max DD `-$2.54`, Sharpe 2.43.
  - 1 contract +2c stress: 96 trades, `+$9.48`, 62.5% win, max DD `-$2.70`,
    Sharpe 2.02.
  - $1 loss-cap +2c stress: 96 signals, `+$12.33`, max DD `-$4.21`.
  - $2 loss-cap +2c stress: 96 signals, `+$31.73`, max DD `-$10.09`.
  - Daily-stop variants reduced drawdown/trades but did not dominate the
    one-contract baseline.
- Significance/overfit diagnostic:
  `scripts\validate_btc15m_f2_significance.py`, output
  `backtest_outputs\btc15m_f2_significance_20260515_2120`.
  Null model: for each trade, `P(win) = stressed_entry + stressed_fee`, so the
  taker buyer has zero expected PnL after fees under an efficient-entry null.
  With 50k Monte Carlo paths:
  - `base_f2` Apr1-14: 96 trades, `+$9.48`, p=0.0312, but Bonferroni over the
    1,237-rule April search is 1.0.
  - `base_f2` Apr15-30: 183 trades, `+$2.54`, p=0.3343.
  - `base_f2` Apr1-30: 279 trades, `+$12.02`, p=0.0795.
  - Exact live-style stricter subset `ttl10_12_entry55_q50` Apr1-30: 77
    trades, `+$8.51`, 63.6% win, max DD `-$2.23`, p=0.0308; still not
    multiple-comparison significant.
- Important implementation correction:
  Earlier transfer diagnostics post-filtered the already selected broad F2
  first signal per event. The live shadow instead selects the first signal that
  satisfies the stricter predicate. Those are not the same. The correct
  live-style rule is "filter first, then first-per-event."
- Correct live-style websocket replay for the stricter subset:
  `backtest_outputs\btc15m_f2_ttl10_12_entry55_q50_live_ws_exactfirst_20260515_2122`.
  Settings: fair probability >=60%, edge >=12c, TTL 10-12m, spread <=2c,
  entry 2-55c, visible top qty >=50, one-contract replay.
  - Websocket capture coverage: 2026-05-12 10:42:45Z through
    2026-05-16 03:17:41Z.
  - Proxy +2c: 34 trades, `+$2.57`, 61.8% win, premium `$17.75`, ROP 14.5%,
    max DD `-$1.73`, Sharpe 0.89.
  - Official-result subset +2c: 6 trades, `+$1.73`, 83.3% win, max DD
    `-$0.59`; official/proxy match 6/6.
  - Daily proxy +2c was positive on May 12, 13, and 15, negative on May 14
    and May 16 so far. This is a forward-test candidate, not production alpha.
- Operational/data note:
  patched `.codex_work\predexon_backfill_queue_20260515.py` so BTC15M Jan-Mar
  Predexon backfill pulls `--window late --late-minutes 12 --write-levels`
  instead of full 15-minute books. This keeps the F2 decision window and should
  materially reduce requests/storage. Restarted only the Predexon queue; live
  1H, BTC15M capture, and BTC15M F2 dry-run shadow were left running.

2026-05-15/16 BTC15M older-data validation infrastructure:

- Added `scripts\fetch_btc_spot_1m.py`.
  Purpose: safely fetch/repair BTC-USD Coinbase 1-minute spot cache for BTC15M
  Predexon validation. It normalizes mixed raw/feature caches, dedupes by
  `bucket_start`, sets causal `available_at = bucket_start + 1 minute`, and
  recomputes RV features before replacing the parquet.
- Repaired and extended:
  `data\btc15m_historical_datamart\spot_1m.parquet`.
  Current clean coverage after repair: 187,149 rows from
  2026-01-06 00:01:00Z through 2026-05-16 00:01:00Z, with zero missing
  `available_at` rows.
- Added `scripts\backtest_btc15m_f2_predexon_range.py`.
  Purpose: fixed-rule BTC15M F2 validation on arbitrary Predexon ranges. This
  is not a parameter search. It uses provider snapshot time as available time,
  first qualifying signal per event, top-ask taker fill, visible top qty, local
  metadata official results, causal BTC minute close/RV, Kalshi fees, and +2c
  adverse-entry stress.
- Validator cross-check:
  `backtest_outputs\btc15m_f2_predexon_apr1_14_crosscheck_20260515_2135`.
  It reproduces the known Apr1-14 robust base F2 result exactly:
  96 trades, `+$9.48` under +2c stress, 62.5% win, max DD `-$2.70`, Sharpe
  2.02. This confirms the range validator is aligned with the robust Apr
  replay after fixing metadata loading.
- Bug fixed in the new validator:
  the first version selected only one market metadata parquet from the metadata
  directory, which missed April rows and falsely produced zero trades. It now
  merges all metadata files overlapping the requested date range and dedupes by
  market/event/series.
- First outside-time smoke:
  `backtest_outputs\btc15m_f2_predexon_jan08_available_20260515_2138`.
  Available Jan 8 02:45Z-13:15Z Predexon late-window data had 141,296 top
  rows, 141,285 level-0 size rows, 42 events, and zero F2/strict-F2 signals.
  This is too small to validate or reject F2; it only proves the older-data
  path runs and that early Jan 8 did not offer qualifying F2 entries.
- Follow-up after the backfill advanced:
  `backtest_outputs\btc15m_f2_predexon_jan08_latest_20260515_2132`.
  Jan 8 02:45Z-16:45Z had 182,805 top rows, 182,794 level-0 size rows, 56
  events, and still zero `base_f2`, `ttl10_12_entry55_q50`, or `ttl6_12`
  signals. Keep collecting before making any inference about January.
- Correction to the Jan smoke:
  the zero-signal result was caused by missing historical BTC15M price-to-beat
  metadata (`Price to beat: TBD`), which made `floor_strike`,
  `side_fair_p`, and `fair_edge_cents` NaN. This was a data issue, not a real
  absence of signals.
- Patched `scripts\backtest_btc15m_f2_predexon_range.py` so older BTC15M rows
  with missing target are filled with a clearly flagged
  `coinbase_open_minute_proxy`: Coinbase event-open minute close, available at
  event open + 1 minute. Rows before that proxy is available remain unusable.
  This is research-grade, not final-promotion-grade, because the exact Kalshi
  price-to-beat may differ by a few dollars.
- Jan 8 proxy-target rerun:
  `backtest_outputs\btc15m_f2_predexon_jan08_proxy_target_20260515_2140`.
  Window 2026-01-08 02:45Z-16:45Z:
  - `base_f2`: 9 trades, `-$2.82` under +2c stress, 22.2% win, max DD
    `-$3.23`.
  - `ttl10_12_entry55_q50`: 2 trades, `-$1.07`, 0% win.
  - `ttl6_12`: 8 trades, `-$3.24`, 12.5% win.
  Interpretation: this is an early outside-time rejection warning for F2, not
  something to tune against. Keep Jan/Feb as validation evidence; do not adjust
  F2 thresholds to rescue Jan 8.

2026-05-15/16 BTC15M F2 validation expansion:

- Reran Apr1-14 through the fixed range validator with the same robust rules:
  `backtest_outputs\btc15m_f2_predexon_apr1_14_rerun_20260515_214054`.
  It again matched the known robust result:
  - `base_f2`: 96 trades, `+$9.48`, 62.5% win, max DD `-$2.70`, Sharpe 2.02.
  - `ttl10_12_entry55_q50`: 29 trades, `+$4.96`, 69.0% win, max DD
    `-$1.66`, Sharpe 1.88.
  - `ttl6_12`: 89 trades, `+$8.83`, 62.9% win, max DD `-$2.41`, Sharpe
    1.93.
- Ran the same frozen robust rules on Apr15-30:
  `backtest_outputs\btc15m_f2_predexon_apr15_30_rerun_20260515_214252`.
  This is weaker validation, especially late April:
  - `base_f2`: 184 trades, `+$3.11`, 55.4% win, max DD `-$7.34`, Sharpe
    0.46. First half `+$3.58`; second half `-$0.47`.
  - `ttl10_12_entry55_q50`: 49 trades, `+$4.12`, 61.2% win, max DD
    `-$2.23`, Sharpe 1.18. First half `+$1.73`; second half `+$2.39`.
  - `ttl6_12`: 154 trades, `+$3.30`, 56.5% win, max DD `-$6.56`,
    Sharpe 0.53.
- Reran the currently available Jan8 older-data slice after the backfill reached
  21:30Z:
  `backtest_outputs\btc15m_f2_predexon_jan08_available_rerun_20260515_214252`.
  This uses the proxy target with near-proxy-strike rows dropped because old
  BTC15M metadata still has `Price to beat: TBD`:
  - `base_f2`: 11 trades, `-$0.68`, 45.5% win, max DD `-$1.65`, Sharpe
    -0.41.
  - `ttl10_12_entry55_q50`: 3 trades, `-$0.54`, 33.3% win, max DD `-$0.59`,
    Sharpe -0.50.
  - `ttl6_12`: 10 trades, `-$1.10`, 40.0% win, max DD `-$2.07`, Sharpe
    -0.69.
- Updated conclusion: F2 is a useful research candidate and strict
  `ttl10_12_entry55_q50` is the best current F2 variant, but it is not proven
  deployable alpha. It passes Apr1-14, remains positive but weaker on Apr15-30,
  and has an early negative older-data warning on proxy-target Jan8. Do not tune
  thresholds on Jan8; wait for more Jan/Feb Predexon data and treat those slices
  as outside-time validation.

2026-05-15/16 BTC15M F2 streak/pattern diagnostic:

- Added `scripts\analyze_btc15m_f2_streak_patterns.py`; output
  `backtest_outputs\btc15m_f2_streak_patterns_20260515_214659`.
  Design: Apr1-14 is diagnostic/training, Apr15-30 is validation, Jan8 is an
  external proxy-target check. This is diagnostic only, not a production rule.
- Streak result: no strong evidence that raw win/loss streaks are exploitable.
  - `base_f2` Apr1-14: max loss streak 4, permutation p=0.73; P(win after win)
    60.0%, P(win after loss) 65.7%, Fisher p=0.66.
  - `base_f2` Apr15-30: max loss streak 6, permutation p=0.55; P(win after win)
    55.9%, P(win after loss) 54.3%, Fisher p=0.88.
  - Strict `ttl10_12_entry55_q50` Apr15-30: P(win after win) 56.7%,
    P(win after loss) 66.7%, Fisher p=0.55.
  Conclusion: do not add a "pause after loss streak" rule from current evidence.
- Train-selected median gates found one plausible state variable:
  higher recent realized BTC volatility (`rv_60m`).
  - `base_f2`, threshold learned from Apr1-14 median `rv_60m >= 0.3198`:
    Apr15-30 improves from 184 trades `+$3.11`, 55.4% win, max DD `-$7.34`
    to 55 trades `+$8.62`, 69.1% win, max DD `-$2.67`.
  - `ttl6_12`, threshold learned from Apr1-14 median `rv_60m >= 0.3126`:
    Apr15-30 improves from 154 trades `+$3.30`, 56.5% win, max DD `-$6.56`
    to 50 trades `+$9.25`, 72.0% win, max DD `-$2.18`.
  - Strict `ttl10_12_entry55_q50`, `rv_60m >= 0.2749`: Apr15-30 improves
    from 49 trades `+$4.12`, 61.2% win, max DD `-$2.23` to 20 trades
    `+$3.36`, 70.0% win, max DD `-$1.14`.
  - Combined Apr1-30 after the same train-selected RV gates:
    `base_f2` 103 trades `+$14.43`, 67.0% win, max DD `-$2.67`;
    `ttl6_12` 95 trades `+$14.46`, 68.4% win, max DD `-$2.18`;
    strict 35 trades `+$7.48`, 74.3% win, max DD `-$1.14`.
- External checks are mixed and prevent promotion:
  - Jan8 proxy-target with near-strike dropped:
    `base_f2 rv_60m>=0.3198` 10 trades `-$0.04`, 50.0% win;
    `ttl6_12 rv_60m>=0.3126` 9 trades `-$0.46`, 44.4% win;
    strict `rv_60m>=0.2749` 3 trades `-$0.54`, 33.3% win.
  - Live websocket broad/base-like TTL5-12 entry<=60: all 101 trades
    `-$9.46`; with `rv_60m>=0.3198` still 10 trades `-$1.74`.
  - Live websocket strict TTL10-12 entry<=55 qty>=50: all 34 trades `+$2.57`;
    `rv_60m>=0.2749` leaves only 2 trades `+$0.91`, too few to trust.
  Conclusion: high RV is a good hypothesis for the next research loop, but it
  is not yet a deployable filter because it does not rescue broad live replay
  and the strict live sample is too small.
- Live-compatible simple logistic diagnostic:
  `backtest_outputs\btc15m_f2_streak_patterns_20260515_214659\live_common_feature_logistic_checks.csv`.
  This model used only features also present or computable in websocket replay:
  entry, fair probability, fair edge, TTL, spread, visible qty, `rv_60m`,
  BTC spot age, distance to strike, and side. It was trained only on Apr1-14
  `base_f2`; threshold was the train median predicted probability.
  - Train Apr1-14: all 96 trades `+$9.48`; selected 48 trades `+$9.18`,
    75.0% win, max DD `-$1.17`.
  - Validation Apr15-30: all 184 trades `+$3.11`; selected 77 trades `+$3.75`,
    61.0% win, max DD `-$2.20`.
  - External Jan8 proxy-target: all 11 trades `-$0.68`; selected 4 trades
    `-$0.35`.
  - Live websocket base-like TTL5-12 entry<=60: all 101 trades `-$9.46`;
    selected 14 trades `+$0.64`, 64.3% win, max DD `-$0.92`.
  Coefficient directions: higher visible qty, higher `rv_60m`, higher entry
  price, and longer TTL were positive; wider spread and higher claimed fair
  edge were negative. This may be detecting "liquid, volatile, not-too-cute"
  conditions where the fair-value model is less stale. It is still too weak and
  too small-sample for deployment.
- Independent subagent checks:
  - High-RV check: structurally plausible but not externally validated. Apr15-30
    high-vs-low split is meaningful (`base_f2` Fisher p=0.0159, `ttl6_12`
    Fisher p=0.0091), but train split did not itself strongly separate high vs
    low RV, Jan checks were negative/flat, and broad live websocket replay still
    lost under high-RV-only gates.
  - Streak/lag check: do not deploy any win/loss streak, loss-streak,
    win-streak, or recent-outcome filter. Recent-outcome-only ML had validation
    AUC below or near random and selected subsets that failed to improve
    validation/live performance consistently. Streak-looking artifacts flip
    between Apr1-14, Apr15-30, Jan proxy, and live websocket.

2026-05-15/16 BTC15M May Predexon validation:

- Ran frozen robust F2 on May1-12 Predexon snapshots:
  `backtest_outputs\btc15m_f2_predexon_may1_12_rerun_20260515_215440`.
  This is outside the April study period and overlaps recent live behavior.
  - `base_f2`: 94 trades, `-$4.69`, 48.9% win, max DD `-$5.56`, Sharpe
    -0.94.
  - `ttl10_12_entry55_q50`: 17 trades, `-$0.93`, 47.1% win, max DD `-$3.15`,
    Sharpe -0.42.
  - `ttl6_12`: 79 trades, `-$3.63`, 50.6% win, max DD `-$4.68`, Sharpe
    -0.78.
  Conclusion: fixed F2 as currently defined does not transfer cleanly to May.
- May high-RV gates, using thresholds learned only from Apr1-14:
  - `base_f2 rv_60m>=0.3198`: 12 trades, `+$0.66`, 58.3% win, max DD
    `-$0.65`.
  - `ttl6_12 rv_60m>=0.3126`: 9 trades, `+$1.22`, 66.7% win, max DD
    `-$0.65`.
  - strict `rv_60m>=0.2749`: 1 trade, `+$0.45`.
  This supports "low-RV broad F2 is dangerous" more than it proves high-RV
  alpha.
- May common-feature logistic check, trained only on Apr1-14 `base_f2`, failed:
  all May `base_f2` was 94 trades `-$4.69`; selected 23 trades `-$5.03`,
  34.8% win, max DD `-$4.50`. Treat the logistic as unstable; do not promote.

2026-05-15/16 BTC15M fixed regime-rule family:

- Added `scripts\research_btc15m_regime_rule_family.py`; output
  `backtest_outputs\btc15m_regime_rule_family_20260515_215931`.
  This uses `side_candidates.parquet` from Predexon robust replays and applies
  a predeclared fixed family of fair-value, high-RV, liquidity, TTL,
  spot-momentum, side-price-history, and stability gates. It takes the first
  qualifying row per event and charges Kalshi taker fees plus +2c adverse entry.
  Train is Apr1-14 only; validation is Apr15-30 and May1-12; Jan8 is external.
- A coherent high-RV cluster passed Apr train and both Apr15/May validation:
  - `edge12_to_28_highrv32`: Apr1-14 44 trades `+$4.21`; Apr15-30 57
    trades `+$9.14`; May1-12 12 trades `+$0.66`.
  - `highrv320_q1_base`: Apr1-14 48 trades `+$5.81`; Apr15-30 58 trades
    `+$8.84`; May1-12 12 trades `+$0.66`.
  - `highrv320_q100_base`: Apr1-14 44 trades `+$3.71`; Apr15-30 54 trades
    `+$8.18`; May1-12 11 trades `+$1.22`.
  - `liquid_highrv32_not_against`: Apr1-14 28 trades `+$4.95`; Apr15-30
    41 trades `+$4.67`; May1-12 7 trades `+$1.25`.
- Jan8 external proxy-target still did not confirm the cluster:
  - `edge12_to_28_highrv32`: 11 trades `-$0.83`.
  - `highrv320_q1_base`: 12 trades `-$1.20`.
  - `highrv320_q100_base`: 8 trades `-$1.00`.
  - `liquid_highrv32_not_against`: 6 trades `-$0.12`, the least-bad Jan
    external result but still not positive.
- Added live websocket replay for selected regime rules:
  `scripts\backtest_btc15m_regime_live_ws_holdout.py`; output
  `backtest_outputs\btc15m_regime_live_ws_20260515_220909`.
  It uses only local websocket capture and materializes only rows passing each
  fixed predicate to avoid huge side-candidate memory blowups.
  - `base_f2`: 106 proxy trades `-$9.45`; official subset 25 trades `-$3.33`.
  - `edge12_to_28_highrv32`: 10 proxy trades `-$1.74`; official subset 1
    trade `-$0.52`.
  - `highrv320_q100_base`: 10 proxy trades `-$1.74`; official subset 1 trade
    `-$0.52`.
  - `liquid_highrv32_not_against`: 5 proxy trades `+$0.03`, 60% win, max DD
    `-$0.79`; zero official-settled trades.
  - `strict_plus_highrv20`: 10 proxy trades `-$0.53`; official subset 2
    trades `-$0.09`.
  Conclusion: the high-RV regime cluster is not promoted. The live websocket
  promotion gate is too weak/negative. The only nonnegative live rule has five
  proxy trades and no official subset, which is not evidence of deployable
  alpha.

2026-05-15/16 BTC15M latest live lowdd replay:

- Ran `scripts\backtest_btc15m_live_holdout.py` on the current local BTC15M
  websocket capture; output
  `backtest_outputs\btc15m_live_holdout_refresh_20260515_221228`.
  Window: 2026-05-15 20:12:30Z through 2026-05-16 04:12:30Z, 639,403 top
  rows, 6,682 BTC ticks, 33 events, 32 finalized settlement rows.
  - `current_lowdd_no_rv`: 14 trades, `-$1.19`, 64.3% win, max DD `-$1.89`,
    Sharpe -0.62.
  - `cheap_yes_rr_first`: 26 trades, `-$0.23`.
  - `cheap_no_rr_first`: 25 trades, `-$2.599`.
  - `cheap_tail_best_side_first`: 32 trades, `-$0.699`.
  - `cheap_tail_position_aware`: 32 events, `-$2.759`.
  - `cheap_pair_lock_rr`: 18 completed pairs, `+$0.72`, 100% win, but this is
    still not deployable by itself because it only scores completed pairs and
    ignores first-leg events that never lock. The position-aware deployable
    version is negative.
  Conclusion: the current lowdd/momentum path remains rejected by live replay.

2026-05-15/16 BTC15M larger Jan external update:

- Predexon late-window backfill reached Jan9 14:30Z. Ran robust F2 range on
  Jan8 02:45Z through Jan9 14:30Z:
  `backtest_outputs\btc15m_f2_predexon_jan08_09_partial_20260515_221357`.
  This is still proxy-target because old BTC15M metadata has `Price to beat:
  TBD`; near-proxy-strike rows are dropped.
  - `base_f2`: 28 trades, `-$0.54`, 50.0% win, max DD `-$2.92`.
  - `ttl10_12_entry55_q50`: 6 trades, `-$0.17`, 50.0% win, max DD `-$0.59`.
  - `ttl6_12`: 26 trades, `-$0.47`, 50.0% win, max DD `-$2.43`.
- Reran the regime-rule family with this larger Jan external:
  `backtest_outputs\btc15m_regime_rule_family_jan0809ext_20260515_221454`.
  The high-RV cluster remains positive on Apr/May validation but fails Jan
  external:
  - `edge12_to_28_highrv32`: Jan external 13 trades `-$1.00`.
  - `highrv320_q100_base`: Jan external 11 trades `-$1.20`.
  - `liquid_highrv32_edge_capped`: Jan external 8 trades `-$0.27`.
  - `liquid_highrv32_not_against`: Jan external 9 trades `-$0.32`.
  Conclusion: outside-time Jan proxy evidence still blocks promotion of the
  high-RV fair-value family.

2026-05-15/16 BTC15M simultaneous pair-lock structural test:

- Added `scripts\research_btc15m_simultaneous_pair_locks.py`; output
  `backtest_outputs\btc15m_pair_locks_20260515_221655`.
  This tests same-timestamp YES+NO ask locks: buy one YES and one NO only when
  both top asks are visible at the same book timestamp and all-in cost after
  Kalshi fees, per-leg adverse stress, and a required profit cushion is below
  `$1`.
- Result:
  - With no adverse stress and zero required profit, Predexon April/May has
    many zero-profit equality cases (`YES ask + NO ask + fees == $1`), but live
    and Jan have none in the tested windows.
  - With even 1c adverse stress per leg and 1c required lock profit, there are
    zero qualifying pairs across Apr1-14, Apr15-30, May1-12, Jan8-9, and the
    live websocket holdout.
  - With 2c stress, also zero.
- Conclusion: true simultaneous, executable pair-lock arbitrage is not present
  in the data under realistic fill/slippage assumptions. The previously
  positive `cheap_pair_lock_rr` remains invalid as a standalone strategy because
  it waited for a later second leg and ignored first-leg failures.

2026-05-15/16 BTC15M F2 Apr1-14 robust replay refresh:

- User asked to backtest the current F2 candidate with the robust rules on
  Apr1-14. Reran:
  `scripts\backtest_btc15m_f2_predexon_range.py --start
  2026-04-01T00:00:00Z --end 2026-04-15T00:00:00Z --stress-cents 2
  --drop-proxy-near-strike-usd 20`.
- Fresh output:
  `backtest_outputs\btc15m_f2_robust_apr1_14_rerun_20260515_222233`.
- Data/replay assumptions: 1,218,851 Predexon top snapshots, 303,181 feature
  rows, 546,736 side candidates, 1,283 events, official Kalshi metadata targets,
  causal provider timestamp as `available_at`, Coinbase BTC 1m backward as-of,
  first qualifying signal per event, separate YES/NO executable top asks,
  visible top size gate, one-contract replay, Kalshi taker fees, and +2c adverse
  entry stress.
- Results:
  - `base_f2`: 96 trades, `+$9.48`, 62.5% win, max DD `-$2.70`, Sharpe 2.02.
  - `ttl10_12_entry55_q50`: 29 trades, `+$4.96`, 68.97% win, max DD `-$1.66`,
    Sharpe 1.88.
  - `ttl6_12`: 89 trades, `+$8.83`, 62.92% win, max DD `-$2.41`, Sharpe 1.93.
- This reproduces the earlier Apr1-14 robust F2 result. It is a strong in-sample
  April slice, not promotion evidence by itself because later Apr/May/live checks
  weakened or rejected broad F2.

2026-05-15/16 BTC1H May Predexon high-confidence validation refresh:

- Reran the frozen BTC1H high-confidence family on the available May Predexon
  KXBTCD snapshots:
  `scripts\backtest_predexon_orderbooks.py --series KXBTCD --start
  2026-05-01T00:00:00Z --end 2026-05-06T00:00:00Z --scan-stride-sec 30
  --one-hour-variant-regex "high_conf_80|current_1h_late_loss_guard|research_original_late"`.
  Fresh output:
  `backtest_outputs\predexon_btc1h_may1_06_stride30_highconf_20260515_222544`.
- Coverage caveat: local Predexon KXBTCD May data is effectively May3-May5
  only, with sparse May1/May6 files. This is still useful as an outside-time
  check after the April candidate work.
- May3-May5 result at 30s scan cadence:
  - `high_conf_80`: 19 trades, `-$0.78`, 57.9% win, max DD `-$3.46`.
  - `high_conf_80_no_chase`: 15 trades, `+$1.82`, 80.0% win, max DD `-$1.73`.
  - `high_conf_80_entry70_no_chase`: 12 trades, `+$2.09`, 83.3% win,
    max DD `-$1.37`.
  - `current_1h_late_loss_guard`: 23 trades, `+$1.00`, 65.2% win,
    max DD `-$3.66`.
  - `research_original_late`: 24 trades, `+$1.52`, 66.7% win, max DD `-$3.14`.
- Trade-level Feb10-May5 combined split comparison written to
  `backtest_outputs\btc1h_highconf_predexon_tradelevel_feb10_may5_20260515_222544.csv`.
  Summary:
  - `current_1h_late_loss_guard`: 69 trades, `+$6.03`, 69.6% win,
    max DD `-$3.66`.
  - `high_conf_80_no_chase`: 41 trades, `+$5.70`, 80.5% win,
    max DD `-$1.73`.
  - `research_original_late`: 74 trades, `+$5.59`, 67.6% win,
    max DD `-$3.14`.
  - `high_conf_80_entry70_no_chase`: 33 trades, `+$4.72`, 78.8% win,
    max DD `-$1.37`.
  - `high_conf_80`: 49 trades, `+$2.65`, 69.4% win, max DD `-$3.46`.
- Updated interpretation: plain `high_conf_80` is no longer the clean primary
  candidate because it failed the May Predexon outside check. The no-chase
  variants are now the more interesting BTC1H candidates: lower trade count,
  materially better win rate, and lower drawdown across Feb/Mar/Apr/May
  Predexon. They still need true post-freeze websocket shadow validation before
  any live promotion.

2026-05-15/16 BTC1H replay audit fixes and corrected websocket sweep:

- Subagent audit found two BTC1H replay fidelity issues:
  - `scripts\replay_btc1h_core_ws_counterfactual.py` only added the last event
    in a same-`received_at_ns` batch to `changed_events`, so simultaneous quote
    batches could miss evaluation for earlier events in the batch.
  - The websocket replay did not enforce a hard maximum Coinbase BTC tick age,
    so some older May rows could use stale as-of BTC.
- Patched `scripts\replay_btc1h_core_ws_counterfactual.py`:
  - Add each row's event to `changed_events` inside the per-row loop.
  - Add `--max-btc-spot-age-sec` defaulting to `180` and skip evaluations with
    older captured Coinbase ticks.
  - Record this gate in replay metadata.
- Subagent audit also noted Predexon BTC1H had top-level sizes attached but the
  shared `may8examine.signals_for_variant` path did not enforce chosen-side
  visible quantity. Patched `scripts\may8examine.py` to reject chosen sides with
  `visible_qty < 1`, and patched `scripts\backtest_predexon_orderbooks.py` to
  write `visible_qty` into BTC1H trade rows.
- Validation:
  - `python -m py_compile scripts\replay_btc1h_core_ws_counterfactual.py
    scripts\may8examine.py scripts\backtest_predexon_orderbooks.py` passed.
  - Reran May Predexon after the visible-size patch:
    `backtest_outputs\predexon_btc1h_may1_06_stride30_highconf_visible_20260515_233413`.
    Results matched the pre-patch May run exactly, so the May no-chase evidence
    was not coming from zero-quantity top-book quotes.
- Corrected May6-May12 websocket cadence sweep on
  `data\live_capture_gapless\live_capture_gapless_20260512_paused.duckdb`,
  with captured settlement and `--max-btc-spot-age-sec 180`, written to:
  `backtest_outputs\btc1h_highconf_ws_fixed_stride_sweep_20260515.csv`.

```
variant                         stride  trades  pnl    win%   maxDD
high_conf_80                    1s      35      +1.59  74.3%  -1.81
high_conf_80                    5s      25      +3.70  84.0%  -0.77
high_conf_80                    10s     21      +2.18  81.0%  -0.86
high_conf_80                    15s     20      +3.95  90.0%  -0.74
high_conf_80                    20s     13      +1.89  84.6%  -0.74
high_conf_80                    30s     14      +2.30  85.7%  -0.70
high_conf_80_no_chase           1s      32      -0.29  68.8%  -2.44
high_conf_80_no_chase           5s      22      +2.75  81.8%  -1.06
high_conf_80_no_chase           10s     18      +2.33  83.3%  -0.86
high_conf_80_no_chase           15s     17      +4.13  94.1%  -0.74
high_conf_80_no_chase           20s     12      +1.68  83.3%  -0.74
high_conf_80_no_chase           30s     12      +1.85  83.3%  -0.70
high_conf_80_entry70_no_chase   1s      29      +1.33  72.4%  -1.74
high_conf_80_entry70_no_chase   5s      19      +4.16  89.5%  -0.71
high_conf_80_entry70_no_chase   10s     15      +1.76  80.0%  -0.77
high_conf_80_entry70_no_chase   15s     14      +4.51  100%   0.00
high_conf_80_entry70_no_chase   20s     10      +1.18  80.0%  -0.72
high_conf_80_entry70_no_chase   30s     10      +1.35  80.0%  -0.70
```

- Interpretation:
  - Corrected websocket replay still supports the BTC1H high-confidence family.
  - Plain `high_conf_80` is the most cadence-stable on May6-May12 websocket
    data, positive from 1s through 30s.
  - `high_conf_80_no_chase` is better on Predexon May and at 5s-30s websocket
    cadence, but it loses at 1s under the corrected replay, so it is promising
    but cadence-sensitive rather than deployment-proven.
  - `entry70_no_chase` remains research-only because it adds another tuned cap.
- Started `scripts\btc_1hr_high_conf80_no_chase_shadow.py` in paper mode as PID
  `21264` with output log
  `logs\btc_1hr_high_conf80_no_chase_shadow_20260515_234133.out.log`. This is
  for forward validation only; no live orders.

2026-05-16 BTC1H high-confidence robustness audit:

- Added `scripts\analyze_btc1h_highconf_robustness.py`, a fixed-artifact audit
  script. It does not search thresholds; it consolidates frozen BTC1H candidate
  trade files and recomputes PnL under extra adverse entry stress of 0c, 1c,
  2c, and 3c.
- Output:
  `backtest_outputs\btc1h_highconf_robustness_20260516_003059`.
- Important artifact hygiene note: an earlier analyzer run
  `btc1h_highconf_robustness_20260516_003026` mis-parsed websocket `win=1.0`
  as false and was deleted. Use only the `003059` output.
- Inputs:
  - Predexon Feb10-Mar31 legacy high-confidence trades. The patched Feb-Mar
    rerun timed out and only wrote `data_report.json`, so the legacy Feb-Mar
    section remains directional and should not be treated as fully refreshed
    visible-size evidence.
  - Patched visible-size Predexon Apr1-14, Apr15-30, and May3-5.
  - Corrected websocket May6-May12 cadence sweep with 180s BTC tick age gate.
- Predexon no-extra-stress summary:
  - `current_1h_late_loss_guard`: 66 trades, `+$6.09`, 69.7% win,
    max DD `-$3.66`.
  - `high_conf_80_no_chase`: 38 trades, `+$5.86`, 81.6% win,
    max DD `-$1.73`.
  - `research_original_late`: 71 trades, `+$5.65`, 67.6% win,
    max DD `-$3.14`.
  - `high_conf_80_entry70_no_chase`: 30 trades, `+$4.86`, 80.0% win,
    max DD `-$1.37`.
  - `high_conf_80`: 46 trades, `+$2.81`, 69.6% win, max DD `-$3.46`.
- Predexon with +2c additional adverse entry stress:
  - `high_conf_80_no_chase`: 38 trades, `+$5.10`, 81.6% win,
    max DD `-$1.81`.
  - `current_1h_late_loss_guard`: 66 trades, `+$4.77`, 69.7% win,
    max DD `-$3.78`.
  - `high_conf_80_entry70_no_chase`: 30 trades, `+$4.26`, 80.0% win,
    max DD `-$1.41`.
  - `research_original_late`: 71 trades, `+$4.23`, 67.6% win,
    max DD `-$3.28`.
  - `high_conf_80`: 46 trades, `+$1.89`, 69.6% win, max DD `-$3.58`.
- Corrected websocket with +2c additional adverse entry stress:
  - Plain `high_conf_80` remains positive at every tested cadence:
    1s `+$0.89`, 5s `+$3.20`, 10s `+$1.76`, 15s `+$3.55`,
    20s `+$1.63`, 30s `+$2.02`.
  - `high_conf_80_no_chase` remains positive from 5s through 30s but is
    negative at 1s: 1s `-$0.93`, 5s `+$2.31`, 10s `+$1.97`,
    15s `+$3.79`, 20s `+$1.44`, 30s `+$1.61`.
  - `entry70_no_chase` remains positive across cadences, but because it adds a
    further selected entry cap, keep it research-only until a future holdout.
- Updated interpretation:
  - For a conservative signal candidate, plain `high_conf_80` has the best
    websocket cadence stability.
  - For drawdown reduction, `high_conf_80_no_chase` remains the best
    hypothesis, but the 1s websocket loss means it cannot be called fully robust
    yet.
  - Do not promote any BTC1H variant from this alone; keep both paper shadows
    collecting post-freeze decisions and validate against the live ledger later.

2026-05-16 BTC1H high-confidence significance audit:

- Added `scripts\analyze_btc1h_highconf_significance.py`.
  It reads the fixed robustness audit input trades and performs:
  - A breakeven random-outcome Monte Carlo null: each trade wins with
    probability equal to its all-in premium, so expected PnL is zero.
  - Paired event-level comparisons between variants, filling skipped events
    with zero PnL.
  This is a sanity check only, not a strategy search.
- Output:
  `backtest_outputs\btc1h_highconf_significance_20260516_003359`.
- Predexon breakeven-null results:
  - `high_conf_80_no_chase`: 38 trades, `+$5.86`, 81.6% win,
    p(PnL >= observed) `0.0271`.
  - `high_conf_80_entry70_no_chase`: 30 trades, `+$4.86`, 80.0% win,
    p `0.0429`, but remains research-only because it adds a selected entry cap.
  - `current_1h_late_loss_guard`: 66 trades, `+$6.09`, 69.7% win,
    p `0.0736`.
  - `research_original_late`: 71 trades, `+$5.65`, 67.6% win,
    p `0.1014`.
  - `high_conf_80`: 46 trades, `+$2.81`, 69.6% win, p `0.2360`.
- Corrected websocket breakeven-null highlights:
  - 1s `high_conf_80`: 35 trades, `+$1.59`, p `0.3521`.
  - 5s `high_conf_80`: 25 trades, `+$3.70`, p `0.0763`.
  - 15s `high_conf_80`: 20 trades, `+$3.95`, p `0.0370`.
  - 1s `high_conf_80_no_chase`: 32 trades, `-$0.29`, p `0.6281`.
  - 15s `high_conf_80_no_chase`: 17 trades, `+$4.13`, p `0.0182`.
  Websocket cadence rows are overlapping replays of the same May6-May12 days,
  so these are stability checks, not independent validation samples.
- Paired event-level checks:
  - Predexon `high_conf_80_no_chase` versus plain `high_conf_80`:
    `+$3.05` incremental over 46 events, sign-flip p `0.1537`.
  - Predexon `high_conf_80_no_chase` versus current late loss guard:
    `-$0.23` incremental over 66 events, sign-flip p `0.9430`.
  - Websocket no-chase versus plain high-conf is mixed by cadence:
    worse at 1s and 5s, near-flat/slightly better at 10s/15s, worse again at
    20s/30s.
- Updated interpretation:
  - The most defensible current BTC1H statement is: high-confidence filtering
    appears useful; no-chase is a plausible drawdown/liquidity-regime filter,
    but it is not proven superior to plain high-conf.
  - Do not replace the live strategy based on this audit alone. The required
    next gate is post-freeze shadow/live-ledger validation from the two running
    paper shadows.

2026-05-16 BTC1H Feb-Mar visible-size rerun and refreshed audits:

- The all-at-once patched Feb10-Mar31 Predexon rerun had timed out after
  writing only `data_report.json`, so it was not counted. Reran the same
  patched visible-size BTC1H replay in weekly chunks:
  - `backtest_outputs\predexon_btc1h_20260210_20260217_stride30_highconf_visible_20260516_003528`
  - `backtest_outputs\predexon_btc1h_20260217_20260224_stride30_highconf_visible_20260516_003528`
  - `backtest_outputs\predexon_btc1h_20260224_20260303_stride30_highconf_visible_20260516_003528`
  - `backtest_outputs\predexon_btc1h_20260303_20260310_stride30_highconf_visible_20260516_003528`
  - `backtest_outputs\predexon_btc1h_20260310_20260317_stride30_highconf_visible_20260516_003528`
  - `backtest_outputs\predexon_btc1h_20260317_20260324_stride30_highconf_visible_20260516_003528`
  - `backtest_outputs\predexon_btc1h_20260324_20260401_stride30_highconf_visible_20260516_003528`
- Most early chunks generated no trades after the stricter visible-liquidity
  path. The patched Feb-Mar trade evidence comes from Mar17-Mar31:
  - Mar17-Mar24: `current_1h_late_loss_guard` 1 trade `+$0.42`;
    `high_conf_80` 1 trade `-$0.68`; `high_conf_80_no_chase` 1 trade `+$0.25`;
    `research_original_late` 2 trades `-$0.05`.
  - Mar24-Apr1: `current_1h_late_loss_guard` 12 trades `+$2.50`;
    `high_conf_80` 7 trades `+$1.44`; `high_conf_80_no_chase` 7 trades
    `+$1.32`; `high_conf_80_entry70_no_chase` 5 trades `+$0.84`;
    `research_original_late` 12 trades `+$2.50`.
- Updated `scripts\analyze_btc1h_highconf_robustness.py` to use the patched
  weekly Feb-Mar chunks instead of the legacy pre-visible-size Feb-Mar file.
  Fresh robustness output:
  `backtest_outputs\btc1h_highconf_robustness_20260516_012255`.
- Refreshed Predexon no-extra-stress summary:
  - `high_conf_80_no_chase`: 38 trades, `+$5.86`, 81.6% win,
    max DD `-$1.73`.
  - `current_1h_late_loss_guard`: 65 trades, `+$5.65`, 69.2% win,
    max DD `-$3.66`.
  - `research_original_late`: 70 trades, `+$5.21`, 67.1% win,
    max DD `-$3.14`.
  - `high_conf_80_entry70_no_chase`: 30 trades, `+$4.86`, 80.0% win,
    max DD `-$1.37`.
  - `high_conf_80`: 46 trades, `+$2.81`, 69.6% win, max DD `-$3.46`.
- Refreshed Predexon +2c extra adverse-entry stress:
  - `high_conf_80_no_chase`: 38 trades, `+$5.10`, 81.6% win,
    max DD `-$1.81`.
  - `current_1h_late_loss_guard`: 65 trades, `+$4.35`, 69.2% win,
    max DD `-$3.78`.
  - `high_conf_80_entry70_no_chase`: 30 trades, `+$4.26`, 80.0% win,
    max DD `-$1.41`.
  - `research_original_late`: 70 trades, `+$3.81`, 67.1% win,
    max DD `-$3.28`.
  - `high_conf_80`: 46 trades, `+$1.89`, 69.6% win, max DD `-$3.58`.
- Updated `scripts\analyze_btc1h_highconf_significance.py` to read the latest
  robustness audit automatically. Fresh significance output:
  `backtest_outputs\btc1h_highconf_significance_20260516_012312`.
  Predexon breakeven-null p-values:
  - `high_conf_80_no_chase`: p `0.0270`.
  - `high_conf_80_entry70_no_chase`: p `0.0426`.
  - `current_1h_late_loss_guard`: p `0.0892`.
  - `research_original_late`: p `0.1210`.
  - `high_conf_80`: p `0.2376`.
  Paired event-level comparison remains cautious:
  `high_conf_80_no_chase` beats plain `high_conf_80` by `+$3.05`, but
  sign-flip p is `0.1539`; versus current late loss guard it is `+$0.21`,
  p `0.9470`.
- Updated interpretation after full patched refresh:
  `high_conf_80_no_chase` is the strongest BTC1H research candidate on
  Predexon after visible-size enforcement and stress testing, but the paired
  comparison and websocket cadence sensitivity still block declaring it
  deployment-ready. Keep it in paper shadow and require post-freeze forward
  validation.

2026-05-16 BTC15M F2 Apr1-14 robust rerun:

- Reran the frozen F2 Predexon validator for Apr1-14 on request:
  `backtest_outputs\btc15m_f2_robust_apr1_14_fresh_20260516_012635`.
- Command:
  `python scripts\backtest_btc15m_f2_predexon_range.py --start 2026-04-01T00:00:00Z --end 2026-04-15T00:00:00Z --stress-cents 2 --drop-proxy-near-strike-usd 20 --threads 8`.
- Input scale: 1,218,851 Predexon top rows, 1,218,851 level-0 size rows,
  303,181 feature rows, 546,736 side candidates, 1,283 events.
- Replay rules: first qualifying signal per event, provider timestamp as
  available time, separate YES/NO executable top asks, visible top quantity
  gate/cap, Coinbase BTC 1m backward-asof spot/60m realized vol, Kalshi
  metadata settlement, Kalshi taker fee, and +2c adverse entry stress.
- Results reproduced the previous robust Apr1-14 output:
  - `base_f2`: 96 trades, `+$9.48`, 62.50% win, max DD `-$2.70`,
    Sharpe `2.02`.
  - `ttl10_12_entry55_q50`: 29 trades, `+$4.96`, 68.97% win,
    max DD `-$1.66`, Sharpe `1.88`.
  - `ttl6_12`: 89 trades, `+$8.83`, 62.92% win, max DD `-$2.41`,
    Sharpe `1.93`.
- Interpretation unchanged: Apr1-14 looks good for F2, but this is not enough
  for deployment because later April, May Predexon, and live websocket replay
  have been much weaker or negative.

2026-05-16 BTC15M transfer-gate audit:

- Added `scripts\audit_btc15m_transfer_gate.py`.
  This is an audit script, not a search script: it reads frozen artifacts and
  applies a conservative promotion gate across Apr1-14 train, Apr15-30
  validation, May1-12 validation, Jan proxy external, and live websocket replay.
- Fresh output:
  `backtest_outputs\btc15m_transfer_gate_audit_20260516_013152`.
- Promotion result: 0 BTC15M F2/regime candidates passed.
- Key rows:
  - `base_f2`: Apr1-14 96 trades `+$9.48`; Apr15-30 184 trades `+$3.11`;
    May1-12 94 trades `-$4.69`; Jan proxy 28 trades `-$0.54`;
    live websocket proxy 101 trades `-$9.46`; official live subset 25 trades
    `-$3.33`.
  - `ttl6_12`: Apr1-14 89 trades `+$8.83`; Apr15-30 154 trades `+$3.30`;
    May1-12 79 trades `-$3.63`; Jan proxy 26 trades `-$0.47`; no dedicated
    live replay row in the frozen audit.
  - `strict_ttl10_12_entry55_q50`: Apr1-14 29 trades `+$4.96`; Apr15-30
    49 trades `+$4.12`; May1-12 17 trades `-$0.93`; Jan proxy 6 trades
    `-$0.17`; no dedicated live replay row in the frozen audit.
  - `liquid_highrv32_not_against`: Apr1-14 28 trades `+$4.95`; Apr15-30
    41 trades `+$4.67`; May1-12 7 trades `+$1.25`; Jan proxy 9 trades
    `-$0.32`; live websocket proxy 5 trades `+$0.03`, with 0 official subset
    trades. This is too small and Jan-negative, so it is not promotable.
- Independent read-only subagent checks agreed:
  - High-RV/volatility regimes reduce broad F2 damage and look plausible, but
    fail Jan/live or shrink to too few trades.
  - Loss streaks, win streaks, and recent outcome filters are not predictive:
    Apr1-14 `base_f2` max loss streak permutation p `0.7311`; Apr15-30 p
    `0.5459`.
  - Contract price-history features such as `yes_mid_chg` and `side_mid_chg`
    look good on some April slices but fail Jan/live transfer.
  - Microstructure/liquidity gates such as visible quantity, quote speed, TTL,
    entry, and distance-to-strike do not pass all validation slices.
  - Conservative ML: live-compatible XGBoost remains the only BTC15M ML
    candidate that did not immediately fail live websocket diagnostics, but it
    had only 1 April final-test trade, so it is paper-shadow/future-holdout
    only. Logistic/MLP/naive tree paths are rejected.
- Decision:
  do not deploy any BTC15M F2/regime/streak/price-history candidate from this
  batch. The valid research takeaway is that low realized-volatility broad F2
  is dangerous; it is not yet a positive trading signal.

2026-05-16 BTC1H promotion-gate audit:

- Added `scripts\audit_btc1h_promotion_gate.py`.
  This is an audit script, not a search script. It reads the frozen BTC1H
  robustness/significance artifacts and post-freeze paper-shadow ledgers.
- Fresh outputs:
  `backtest_outputs\btc1h_promotion_gate_audit_20260516_013738` and, after
  adding the entry70 shadow DB/log reader,
  `backtest_outputs\btc1h_promotion_gate_audit_20260516_013956`.
- Gate:
  - Predexon +2c adverse-entry stress PnL must be positive with at least
    25 trades.
  - Breakeven-null p-value must be <= 0.10.
  - All websocket cadence replays must be positive under +2c stress.
  - Post-freeze shadow must have at least 20 settled trades and positive
    realized PnL.
- Result: 0 BTC1H candidates passed.
- Candidate rows:
  - `high_conf_80`: Predexon 46 trades `+$1.89`, win 69.57%,
    max DD `-$3.58`; breakeven-null p `0.2376`; websocket +2c stress positive
    at all 6 cadences with minimum cadence PnL `+$0.89`; post-freeze shadow
    currently 1 settled trade `+$0.24`. Blocked by weak null p-value and too
    little forward shadow evidence.
  - `high_conf_80_no_chase`: Predexon 38 trades `+$5.10`, win 81.58%,
    max DD `-$1.81`; breakeven-null p `0.0270`; websocket +2c stress positive
    at 5/6 cadences but 1-second cadence was `-$0.93`; post-freeze shadow
    currently 1 settled trade `+$0.27`. Blocked by websocket cadence
    instability and too little forward shadow evidence.
  - `high_conf_80_entry70_no_chase`: Predexon 30 trades `+$4.26`, win 80.0%,
    max DD `-$1.41`; breakeven-null p `0.0426`; websocket +2c stress positive
    at all 6 cadences with minimum cadence PnL `+$0.75`; it adds an extra
    entry cap and had 0 settled post-freeze shadow trades at audit time, so it
    remains research-only.
- Decision:
  keep `high_conf_80` and `high_conf_80_no_chase` as paper shadows. Do not
  promote either solely from current backtests. If looking for the next BTC1H
  candidate, the most natural frozen shadow to add is `high_conf_80_entry70_no_chase`,
  but it must be treated as a new candidate requiring forward evidence.

2026-05-16 BTC1H entry70 no-chase paper shadow:

- Added `scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`.
  It mirrors the existing BTC1H websocket paper-shadow wrappers, but forces:
  `--paper`, `--signal-strategy high_conf_80_entry70_no_chase`,
  `--sizing-policy flat_max`, one-contract sizing, `$100` shadow bankroll, and
  separate DB/capture paths.
- Smoke validation:
  - `python -m py_compile scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`
  - `python scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py --dry-run --once --paper-report-sec 15`
  - Dry-run connected to Kalshi and Kraken websockets and exited cleanly.
- Started paper-only forward shadow:
  - PID at start/check: `7744`
  - Command: `python -u scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`
  - Stdout log:
    `logs\btc_1hr_high_conf80_entry70_no_chase_shadow_20260516_013914.out.log`
  - DB:
    `%USERPROFILE%\.btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow.db`
  - Capture DB:
    `%USERPROFILE%\.btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb`
- This process is paper-only and should be used strictly for forward validation.
  It should not be promoted until it has enough settled post-freeze decisions
  and still passes the BTC1H promotion-gate audit.

2026-05-16 BTC15M live-compatible ML post-freeze check:

- Refreshed the frozen live-compatible XGBoost model on post-freeze BTC15M
  websocket capture only:
  `backtest_outputs\btc15m_livecompat_xgb_postfreeze_ws_20260516_014159`.
- Command:
  `python scripts\backtest_btc15m_ml_live_ws_holdout.py --model-dir backtest_outputs\btc15m_live_compatible_models_20260515 --models xgboost_tabular --start 2026-05-15T23:30:00Z`.
- Replay window:
  `2026-05-15 23:30:00 UTC` through
  `2026-05-16 07:42:03 UTC`.
- Data scale:
  623,090 top-of-book websocket rows, 8,465 BTC rows, 3,586 lifecycle rows,
  953,241 reconstructed side candidates, 32 events.
- Result:
  - `xgboost_tabular`, frozen gate `pred_win_prob>=0.84` and
    `pred_ev>=0.14`: 5 trades, raw `-$1.25`, +2c-stressed `-$1.35`,
    40.0% win, max DD `-$1.50`.
  - The 5 selected events were 2 wins / 3 losses; no official result subset was
    present in this replay, so this is proxy-settled.
- Added `scripts\audit_btc15m_ml_promotion_gate.py`.
  Fresh output:
  `backtest_outputs\btc15m_ml_promotion_gate_audit_20260516_014511`.
- ML promotion-gate result:
  - `xgboost_tabular`: blocked by too few April final-test trades
    (1 trade) and negative post-freeze websocket replay.
  - `logistic_calibrator`: blocked by negative April final test and negative
    inspected websocket replay.
 - Decision:
  do not build or run a BTC15M live-compatible XGBoost paper shadow from this
  model version. The first clean post-freeze replay is already negative, so the
  model returns to research-only status.

2026-05-16 BTC15M F2 expanded January external check:

- Aggregated the Jan 8 through Jan 14 partial Predexon BTC15M F2 validation
  chunks from:
  `backtest_outputs\btc15m_f2_predexon_jan08_partial_20260516_015239`
  through
  `backtest_outputs\btc15m_f2_predexon_jan14_partial_partial_20260516_015239`.
- This aggregation uses the actual trade-level CSVs sorted by decision time,
  not hand-added daily summaries, so max drawdown and Sharpe are computed on
  the real external trade sequence.
- Same frozen robust rules as the April rerun:
  first qualifying signal per event, separate YES/NO executable top asks,
  visible top quantity gate/cap, as-of BTC minute close and 60m realized vol,
  Kalshi metadata settlement, Kalshi taker fees, and +2c adverse-entry stress.
- Combined result:

  | strategy | trades | +2c-stressed PnL | return on $100 | ROP | win rate | max DD | Sharpe |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | `base_f2` | 113 | `-$7.30` | `-7.30%` | `-12.31%` | `46.02%` | `-$10.03` | `-1.43` |
  | `ttl10_12_entry55_q50` | 32 | `-$1.75` | `-1.75%` | `-11.11%` | `43.75%` | `-$3.97` | `-0.64` |
  | `ttl6_12` | 102 | `-$4.57` | `-4.57%` | `-8.53%` | `48.04%` | `-$8.38` | `-0.94` |

- Day-level pattern:
  Jan 9 and Jan 10 were positive, but Jan 11 through Jan 14 erased the edge.
  This is a regime-transfer failure, not one isolated bad fill.
- Decision:
  BTC15M F2 remains rejected for deployment. The April 1-14 run is positive,
  but it does not survive a separate January external validation window.

2026-05-16 BTC15M F2 streak and single-feature transfer refresh:

- Patched `scripts\analyze_btc15m_f2_streak_patterns.py` to emit
  `external_gate_checks.csv`, which applies every Apr1-14 train-selected
  median gate to an untouched external trade file.
- Fresh diagnostic outputs:
  - Expanded January external:
    `backtest_outputs\btc15m_f2_streak_patterns_extcheck_20260516_020408`
  - May1-12 external:
    `backtest_outputs\btc15m_f2_streak_patterns_may_extcheck_20260516_020454`
- Streak result:
  no exploitable win/loss streak pattern. Loss-streak permutation p-values
  stayed high:
  - Apr1-14 `base_f2`: `0.7311`
  - Apr15-30 `base_f2`: `0.5459`
  - Jan8-14 `base_f2`: `0.7665`
  Transition probabilities also did not transfer; for example Jan8-14
  `base_f2` had `p_win_after_win=46.15%` and
  `p_win_after_loss=46.67%`.
- The most tempting transfer gate was `ttl6_12` with
  `fair_edge_cents < 14.7678`:
  - Apr1-14 train: 44 trades, `+$5.55`
  - Apr15-30 validation: 94 trades, `+$3.71`
  - Jan8-14 external: 50 trades, `+$1.12`
  - May1-12 external: 60 trades, `-$0.56`
  This fails full transfer.
- Strict filter:
  requiring train PnL > 0, Apr15-30 PnL > 0, Jan8-14 PnL > 0,
  May1-12 PnL > 0, and at least 10 trades in each external set leaves
  0 candidates.
- Decision:
  F2 streak/recent-outcome gates and simple single-feature gates are rejected.
  Do not revive them unless a materially new model family or data source is
  introduced.

2026-05-16 BTC1H simple gate audit:

- Ran a constrained one-feature gate audit from
  `backtest_outputs\btc1h_highconf_robustness_20260516_012255\all_input_trades.csv`.
- Split discipline:
  - Train/selection: Predexon BTC1H through Apr30, excluding `pred_may3_5_visible`.
  - Validation: Predexon `pred_may3_5_visible`.
  - Test: May6-12 websocket replay, with 1-second cadence used as the harshest
    latency/replay cadence.
  - PnL uses one contract, Kalshi taker fee, and +2c adverse-entry stress.
- Baselines under +2c stress:

  | variant | train trades | train PnL | May3-5 trades | May3-5 PnL | WS 1s trades | WS 1s PnL | WS 1s win | WS 1s max DD |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | `high_conf_80` | 27 | `+$3.05` | 19 | `-$1.16` | 35 | `+$0.89` | 74.3% | `-$1.61` |
  | `high_conf_80_no_chase` | 23 | `+$3.58` | 15 | `+$1.52` | 32 | `-$0.93` | 68.8% | `-$1.88` |
  | `high_conf_80_entry70_no_chase` | 18 | `+$2.41` | 12 | `+$1.85` | 29 | `+$0.75` | 72.4% | `-$1.82` |

- Eight simple gates were positive on train, May3-5, and WS 1s. The best
  coherent ones were all variants of `high_conf_80_entry70_no_chase`:

  | gate | train PnL | May3-5 PnL | WS 1s PnL | WS 1s trades | WS 1s max DD |
  |---|---:|---:|---:|---:|---:|
  | baseline | `+$2.41` | `+$1.85` | `+$0.75` | 29 | `-$1.82` |
  | `entry_price >= 0.59` | `+$1.55` | `+$1.04` | `+$0.93` | 27 | `-$1.82` |
  | `visible_qty < 1500.25` | `+$3.79` | `+$0.82` | `+$0.77` | 22 | `-$0.97` |

- Stress check for `high_conf_80_entry70_no_chase`:
  - Baseline WS 1s: +2c `+$0.75`, +3c `+$0.46`, +4c `+$0.17`.
  - `entry_price >= 0.59` WS 1s: +2c `+$0.93`, +3c `+$0.66`, +4c `+$0.39`.
  - `visible_qty < 1500.25` WS 1s: +2c `+$0.77`, +3c `+$0.55`, +4c `+$0.33`.
- Decision:
  `high_conf_80_entry70_no_chase` is still the best BTC1H paper-forward
  candidate. The `entry_price >= 0.59` subset is a possible future refinement,
  but it is not live-promotable until the currently running entry70 shadow has
  enough settled post-freeze trades and still passes the promotion gate.

2026-05-16 BTC1H entry59-70 no-chase paper shadow:

- Implemented paper-only candidate `high_conf_80_entry59_70_no_chase`.
- Live-engine changes:
  - Added the strategy name to `SUPPORTED_SIGNAL_STRATEGIES` in
    `scripts\btc_1hr_research_live.py`.
  - It reuses the high-confidence `side_probability >= 80%` logic and the
    existing 10-minute no-chase guard.
  - It adds a frozen entry band: `0.59 <= entry_price <= 0.70`.
- Paper wrapper:
  `scripts\btc_1hr_high_conf80_entry59_70_no_chase_shadow.py`.
  It forces `--paper`, `flat_max`, one-contract sizing, `$100` shadow bankroll,
  and separate DB/capture files:
  - `%USERPROFILE%\.btc_kalshi_bot\btc_1hr_high_conf80_entry59_70_no_chase_shadow.db`
  - `%USERPROFILE%\.btc_kalshi_bot\btc_1hr_high_conf80_entry59_70_no_chase_shadow_capture.duckdb`
- Reproducibility wiring:
  - Added the same variant to `scripts\replay_btc1h_core_ws_counterfactual.py`.
  - Added the same variant to `scripts\backtest_predexon_orderbooks.py`.
  - Added the variant to the BTC1H robustness/audit candidate lists.
- Validation:
  - `python -m py_compile` passed for the touched live/backtest/audit/test files.
  - Unit test passed:
    `test_high_conf_80_entry59_70_no_chase_requires_entry_floor_and_cap`.
  - Wrapper dry-run passed; it connected to Kalshi and Kraken websockets and
    exited cleanly without live order submission.
  - Direct Predexon smoke:
    `backtest_outputs\predexon_btc1h_entry59_70_smoke_20260516_022554`.
    Window Mar24-Apr1, 4 trades, `+$1.27`, 100% win. This smoke only proves
    wiring; it is too small to validate edge.
  - Websocket replay smoke:
    `backtest_outputs\btc1h_entry59_70_ws_smoke_20260516_023258`.
    It found 0 trades in the first 3 replay events, but successfully produced
    summary/trades artifacts for the new variant.
- Started paper-only forward shadow:
  - PID at start/check: `7828`
  - stdout:
    `logs\btc_1hr_high_conf80_entry59_70_no_chase_shadow_20260516_021231.out.log`
  - stderr file exists and was empty at startup.
  - Startup log confirmed mode `paper`, strategy
    `high_conf_80_entry59_70_no_chase`, capture enabled, and Kalshi/Kraken
    websockets connected.
- Decision:
  this is a paper-forward candidate only. It is not live-promotable until it has
  enough settled post-freeze paper trades and the promotion audit still passes.

2026-05-16 BTC1H entry59-70 derived promotion-audit wiring:

- Added `scripts\analyze_btc1h_entry59_70_derived.py`.
  It creates a conservative derived audit by filtering already-generated
  `high_conf_80_entry70_no_chase` trade rows to `0.59 <= entry_price <= 0.70`.
  This is not a full causal replay because it does not search for a later
  in-band signal when a cheap first signal was filtered out.
- Fresh output:
  `backtest_outputs\btc1h_entry59_70_derived_20260516_0241`.
- Conservative +2c-stressed result:

  | source | trades | PnL | win rate | max DD | Sharpe |
  |---|---:|---:|---:|---:|---:|
  | Predexon prior replay rows | 24 | `+$2.59` | 79.17% | `-$1.44` | 1.29 |
  | May6-12 websocket replay rows | 90 | `+$13.51` | 85.56% | `-$2.95` | 4.00 |

- Harsh 1-second websocket cadence subset:

  | cadence | trades | PnL | win rate | max DD | Sharpe |
  |---:|---:|---:|---:|---:|---:|
  | 1s | 27 | `+$0.93` | 74.07% | `-$1.82` | 0.41 |
  | 5s | 17 | `+$3.94` | 94.12% | `-$0.73` | 3.84 |
  | 10s | 15 | `+$1.46` | 80.00% | `-$0.85` | 0.89 |
  | 15s | 13 | `+$3.82` | 100.00% | `$0.00` | 46.21 |
  | 20s | 9 | `+$1.59` | 88.89% | `-$0.74` | 1.54 |
  | 30s | 9 | `+$1.77` | 88.89% | `-$0.72` | 1.71 |

- Patched `scripts\audit_btc1h_promotion_gate.py` to read the latest
  `btc1h_entry59_70_derived_*` output as a labelled non-promotable evidence
  source.
- Fresh promotion gate:
  `backtest_outputs\btc1h_promotion_gate_audit_20260516_023854`.
- Gate status:
  `high_conf_80_entry59_70_no_chase` does not pass. Failure reasons:
  `predexon_stress_not_positive_or_too_few`,
  `breakeven_null_not_strong`, `too_few_postfreeze_shadow_settled`,
  `research_only_extra_entry_gate_requires_forward_shadow`, and
  `derived_entry_band_audit_not_full_causal_replay`.
- Also added the variant to `scripts\analyze_btc1h_highconf_significance.py`
  so future full-causal robustness/significance reruns remain aligned.

2026-05-16 BTC15M F2 robust Apr1-14 rerun on request:

- Fresh command:
  `python -u scripts\backtest_btc15m_f2_predexon_range.py --start 2026-04-01 --end 2026-04-15 --out-dir backtest_outputs\btc15m_f2_robust_apr1_14_fresh_20260516_025621 --threads 12 --stress-cents 2`.
- Output:
  `backtest_outputs\btc15m_f2_robust_apr1_14_fresh_20260516_025621`.
- Data/rules:
  Predexon BTC15M top/level-0 snapshots from Apr 1 00:00 UTC through Apr 15
  00:00 UTC, 1,218,851 raw top rows, 303,181 feature rows, 1,283 events,
  first qualifying signal per event, one-contract top-of-book executable fill,
  separate YES/NO book construction, causal BTC minute close only, Kalshi taker
  fees, and +2c adverse entry stress.
- Results:

  | strategy | trades | PnL | return on $100 | ROP | win rate | max DD | Sharpe |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | `base_f2` | 96 | `+$9.48` | `+9.48%` | 18.76% | 62.50% | `-$2.70` | 2.02 |
  | `ttl10_12_entry55_q50` | 29 | `+$4.96` | `+4.96%` | 32.98% | 68.97% | `-$1.66` | 1.88 |
  | `ttl6_12` | 89 | `+$8.83` | `+8.83%` | 18.72% | 62.92% | `-$2.41` | 1.93 |

- Interpretation:
  this reproduces the positive Apr1-14 in-sample/training result. It should not
  be read as deployable by itself because later external checks, especially Jan
  partial and live websocket transfer, were weaker or negative for broad F2.

2026-05-16 BTC1H entry59-70 direct full-causal replay aggregate:

- Completed the pending direct Predexon replay chunks for
  `high_conf_80_entry59_70_no_chase`:
  - `backtest_outputs\predexon_btc1h_entry59_70_direct_mar24_apr01_20260516_024401`
  - `backtest_outputs\predexon_btc1h_entry59_70_direct_apr01_apr08_20260516_024401`
  - `backtest_outputs\predexon_btc1h_entry59_70_direct_apr08_apr15_20260516_024401`
  - `backtest_outputs\predexon_btc1h_entry59_70_direct_may03_may06_20260516_024401`
- Patched `scripts\aggregate_btc1h_entry59_70_direct.py`:
  - fixed mixed-format UTC parsing so fractional-second rows are not silently
    dropped;
  - distinguishes zero-trade chunks from truly missing trade files.
- Correct aggregate output:
  `backtest_outputs\btc1h_entry59_70_direct_aggregate_20260516_0309`.
- +2c stress direct replay summary:

  | split | trades | PnL | win rate | max DD | Sharpe |
  |---|---:|---:|---:|---:|---:|
  | all | 16 | `+$2.80` | 87.50% | `-$1.41` | 2.03 |
  | Mar24-Apr1 train | 4 | `+$1.19` | 100.00% | `$0.00` | 11.47 |
  | Apr1-Apr8 train | 0 | `$0.00` | n/a | `$0.00` | n/a |
  | Apr8-Apr15 validation | 2 | `+$0.57` | 100.00% | `$0.00` | 19.00 |
  | May3-May6 external | 10 | `+$1.04` | 80.00% | `-$1.41` | 0.77 |

- Interpretation:
  the full-causal direct replay is positive, but the evidence is thin and uneven:
  Apr1-Apr8 produced no trades, Apr8-Apr15 has only 2 validation trades, and May
  external weakens the Sharpe materially. Keep this as a paper-shadow candidate,
  not a promoted live strategy, until it accumulates settled forward trades.

2026-05-16 BTC1H entry59-70 safety review and first paper fill:

- Subagent review found a real safety gap: although intended as paper-only,
  `high_conf_80_entry59_70_no_chase` was selectable through the generic live
  executor.
- Patched `scripts\btc_1hr_research_live.py` with
  `PAPER_ONLY_SIGNAL_STRATEGIES` and a `validate_args` live-mode block.
- Patched `scripts\btc_1hr_high_conf80_entry59_70_no_chase_shadow.py` so the
  wrapper rejects overrides for `--signal-strategy`, `--sizing-policy`,
  `--db-path`, and `--capture-db-path`.
- Added targeted tests in `scripts\test_research_live_safety.py` and ran:
  `python -m py_compile scripts\btc_1hr_research_live.py scripts\btc_1hr_high_conf80_entry59_70_no_chase_shadow.py scripts\aggregate_btc1h_entry59_70_direct.py scripts\test_research_live_safety.py`
  plus:
  `python -m unittest scripts.test_research_live_safety.ResearchLiveSafetyTests.test_high_conf_80_entry59_70_no_chase_requires_entry_floor_and_cap scripts.test_research_live_safety.ResearchLiveSafetyTests.test_entry59_70_strategy_is_blocked_in_live_mode scripts.test_research_live_safety.ResearchLiveSafetyTests.test_entry59_70_shadow_wrapper_rejects_identity_overrides`.
  Result: 3 tests passed.
- Wrapper smoke with `--dry-run --once --paper-report-sec 15 --no-capture`
  started cleanly and connected/disconnected Kalshi + Kraken websockets. A smoke
  without `--no-capture` collided with the already-running shadow DuckDB writer,
  which is expected single-writer behavior.
- First post-freeze paper-shadow trade:
  `KXBTCD-26MAY1605-T78399.99`, side NO, entry 70c, model p_yes 0.1427,
  net edge 13.73c, 1 contract, paper filled at `2026-05-16T08:52:09Z`.
  Hourly paper report at 03:00 local showed settled win, realized PnL `+$0.28`,
  bankroll `$100.28`, trades 1, wins 1.
- Patched `scripts\analyze_btc1h_highconf_significance.py` to honor
  `--out-dir`, parse mixed UTC timestamps correctly, and explicitly derive
  `high_conf_80_entry59_70_no_chase` rows from `entry70_no_chase` rows with
  `0.59 <= entry_price <= 0.70` for significance sanity checks.
- Fresh significance output:
  `backtest_outputs\btc1h_highconf_significance_20260516_0312`.
  Derived entry59-70 Predexon null p-value: `0.1310` on 24 trades, weaker than
  entry70 (`0.0428`) and no-chase (`0.0277`).
- Fresh promotion gate:
  `backtest_outputs\btc1h_promotion_gate_audit_20260516_0312`.
  Result: still not promoted. It has 1 settled post-freeze shadow trade, but
  fails on weak breakeven-null p-value, too few shadow trades, research-only
  forward-gate requirements, and the derived-audit caveat.

2026-05-16 BTC1H high-confidence direct comparison refresh:

- Ran like-for-like direct Predexon causal replays for:
  `high_conf_80_no_chase`, `high_conf_80_entry70_no_chase`, and
  `high_conf_80_entry59_70_no_chase`.
- Replay chunks:
  - `backtest_outputs\predexon_btc1h_highconf_direct_mar24_apr01_20260516_0320`
  - `backtest_outputs\predexon_btc1h_highconf_direct_apr01_apr08_20260516_0320`
  - `backtest_outputs\predexon_btc1h_highconf_direct_apr08_apr15_20260516_0320`
  - `backtest_outputs\predexon_btc1h_highconf_direct_apr15_apr23_20260516_0340`
  - `backtest_outputs\predexon_btc1h_highconf_direct_apr23_may01_20260516_0340`
  - `backtest_outputs\predexon_btc1h_highconf_direct_may03_may06_20260516_0320`
- Added `scripts\aggregate_btc1h_highconf_direct.py` and aggregate output:
  `backtest_outputs\btc1h_highconf_direct_aggregate_20260516_0349`.
- +2c stress all-window direct replay:

  | variant | trades | PnL | win rate | max DD | Sharpe | breakeven-null p |
  |---|---:|---:|---:|---:|---:|---:|
  | `high_conf_80_no_chase` | 39 | `+$5.37` | 82.05% | `-$1.81` | 2.24 | 0.0399 |
  | `high_conf_80_entry70_no_chase` | 32 | `+$4.78` | 81.25% | `-$1.41` | 2.17 | 0.0479 |
  | `high_conf_80_entry59_70_no_chase` | 27 | `+$3.41` | 81.48% | `-$1.44` | 1.68 | 0.1100 |

- Split findings:
  - Apr23-May1 is the weakest April slice: no-chase and entry70 both barely
    positive at `+$0.06`; entry59-70 is negative at `-$0.37`.
  - May3-May6 external: entry70 is strongest (`+$1.85`, 12 trades), no-chase is
    next (`+$1.52`, 15 trades), entry59-70 is weaker (`+$1.04`, 10 trades).
- Paper-shadow status from logs/SQLite:
  - `high_conf_80_no_chase`: 2 settled paper wins, realized `+$0.55`.
  - `high_conf_80_entry70_no_chase`: 1 settled paper win, realized `+$0.28`.
  - `high_conf_80_entry59_70_no_chase`: 1 settled paper win, realized `+$0.28`.
- Interpretation:
  entry59-70 is not the best candidate after direct comparison; it filters out
  too much and has weaker null evidence. The live-forward candidates to keep
  shadowing are no-chase and entry70. Entry70 has the cleaner drawdown/cadence
  profile; no-chase has higher total PnL but previously showed 1-second
  websocket cadence instability.

2026-05-16 BTC1H high-confidence older Predexon validation:

- Checked local KXBTCD Predexon coverage and found BTC1H data from Feb9 through
  May6. Added older direct replay validation for Feb9-Mar24.
- Initial Feb9-Feb21 12-day chunk OOMed during Pandas merge, so it was split
  into smaller chunks:
  - `predexon_btc1h_highconf_direct_feb09_feb13_20260516_0420`: no trades.
  - `predexon_btc1h_highconf_direct_feb13_feb17_20260516_0420`: no trades.
  - `predexon_btc1h_highconf_direct_feb17_feb19_20260516_0425`: no trades.
  - `predexon_btc1h_highconf_direct_feb19_feb21_20260516_0425`: no trades.
  - `predexon_btc1h_highconf_direct_feb21_mar05_20260516_0355`: no trades.
  - `predexon_btc1h_highconf_direct_mar05_mar16_20260516_0355`: no trades.
  - `predexon_btc1h_highconf_direct_mar16_mar24_20260516_0355`: one
    no-chase trade, win.
- Corrected `scripts\aggregate_btc1h_highconf_direct.py` split labels so
  pre-Mar24 rows are labelled `feb09_mar24_old_val`.
- Full aggregate output:
  `backtest_outputs\btc1h_highconf_direct_aggregate_feb09_may06_20260516_0433`.
- +2c stress full direct replay, Feb9-May6 available windows:

  | variant | trades | PnL | win rate | max DD | Sharpe | breakeven-null p |
  |---|---:|---:|---:|---:|---:|---:|
  | `high_conf_80_no_chase` | 40 | `+$5.60` | 82.50% | `-$1.81` | 2.33 | 0.0344 |
  | `high_conf_80_entry70_no_chase` | 32 | `+$4.78` | 81.25% | `-$1.41` | 2.17 | 0.0479 |
  | `high_conf_80_entry59_70_no_chase` | 27 | `+$3.41` | 81.48% | `-$1.44` | 1.68 | 0.1100 |

- Interpretation:
  the older Feb9-Mar24 data does not falsify the high-conf candidates; it mostly
  has no qualifying trades. The best current candidates remain no-chase and
  entry70. No-chase has better total/statistical evidence; entry70 has lower
  drawdown and avoids the expensive >70c rows. Neither is ready for live
  promotion without more settled forward shadow trades and websocket-cadence
  agreement.

2026-05-16 BTC1H high-confidence promotion safety hardening:

- Tightened `scripts\btc_1hr_research_live.py` so all high-confidence research
  candidates are blocked in generic `live` mode until promotion:
  `high_conf_80`, `high_conf_80_no_chase`, `high_conf_80_entry70_no_chase`,
  and `high_conf_80_entry59_70_no_chase`.
- Locked the shadow wrappers for `high_conf_80`, `high_conf_80_no_chase`, and
  `high_conf_80_entry70_no_chase` the same way the entry59-70 wrapper was
  locked: callers cannot override `--signal-strategy`, `--sizing-policy`,
  `--db-path`, or `--capture-db-path`.
- Added targeted safety tests for all high-conf live-mode blocks and shadow
  wrapper override rejection.
- Validation:
  `python -m py_compile scripts\btc_1hr_research_live.py scripts\btc_1hr_high_conf80_shadow.py scripts\btc_1hr_high_conf80_no_chase_shadow.py scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py scripts\btc_1hr_high_conf80_entry59_70_no_chase_shadow.py scripts\test_research_live_safety.py`
  and targeted unittest for the high-confidence guards/wrappers passed:
  5 tests OK.
- Smoke-tested all four wrappers with `--dry-run --once --paper-report-sec 15
  --no-capture`; each started with the correct strategy and connected to
  Kalshi/Kraken websockets. Capture was disabled for the smoke to avoid
  colliding with the already-running shadow DuckDB writers.
- Refreshed promotion gate:
  `backtest_outputs\btc1h_promotion_gate_audit_20260516_0410`.
  Result: 0 high-confidence candidates are promoted.

2026-05-16 BTC1H no-chase 1s websocket instability diagnostic:

- Added `scripts\analyze_btc1h_no_chase_ws_instability.py`.
  This is a fixed diagnostic, not a threshold search. It reads the frozen
  high-confidence robustness artifact and asks whether the existing 70c entry
  cap explains `high_conf_80_no_chase` failing the 1-second websocket cadence.
- Output:
  `backtest_outputs\btc1h_no_chase_ws_instability_20260516_0415`.
- +2c stress diagnostic on websocket rows:
  - 1s `high_conf_80_no_chase` rows with entry `>70c`:
    11 trades, combined `-$1.41`.
  - 1s `high_conf_80_no_chase` rows with entry `<=70c`:
    21 trades, `+$0.48`.
  - At 5s, `>70c` no-chase rows also lose `-$1.61`, while `<=70c` rows make
    `+$3.92`.
- Interpretation:
  the 70c cap is not arbitrary curve-fitting after this diagnostic; it directly
  removes the rows responsible for the harsh websocket cadence instability.
  `high_conf_80_entry70_no_chase` is now the cleaner forward candidate than
  broad no-chase, even though broad no-chase has slightly higher aggregate
  Predexon PnL.

2026-05-16 BTC1H promotion gate updated with direct aggregate:

- Patched `scripts\audit_btc1h_promotion_gate.py` so when the Feb9-May6 direct
  aggregate exists it is used for the Predexon trade-count/PnL/null gate. The
  audit also records the no-chase 1s `>70c` and `<=70c` diagnostic PnL columns.
- Fresh output:
  `backtest_outputs\btc1h_promotion_gate_audit_20260516_0418`.
- Result:
  0 candidates pass.
- Updated gate table highlights:
  - `high_conf_80_no_chase`: direct 40 trades, `+$5.60`, p `0.0344`, but fails
    websocket cadence because 1s replay is negative. Diagnostic: 1s `>70c`
    no-chase rows `-$1.41`, 1s `<=70c` rows `+$0.48`.
  - `high_conf_80_entry70_no_chase`: direct 32 trades, `+$4.78`, p `0.0479`,
    all websocket cadences positive, but only 1 settled post-freeze paper trade.
  - `high_conf_80_entry59_70_no_chase`: direct 27 trades, `+$3.41`, p `0.1100`,
    not significant enough and only 1 settled post-freeze paper trade.
- Current decision:
  `entry70_no_chase` is the best forward-test candidate, not a deployment. It
  needs more settled paper-shadow trades before live promotion.

2026-05-16 BTC15M F2 robust Apr1-14 and transfer validation:

- Refreshed the frozen BTC15M F2 validator on Apr1-14 with robust rules:
  one-contract taker fills, first qualifying signal per event, separate YES/NO
  executable top asks, visible top quantity gate, causal BTC as-of joins, no
  candle high/low decision data, +2c adverse entry stress, and proxy
  near-strike drops when exact settlement metadata is missing.
- Output:
  `backtest_outputs\btc15m_f2_robust_apr1_14_fresh_20260516_025621`.
- Apr1-14 +2c stress:
  - `base_f2`: 96 trades, `+$9.48`, 62.50% win, max DD `-$2.70`, Sharpe 2.02.
  - `ttl6_12`: 89 trades, `+$8.83`, 62.92% win, max DD `-$2.41`, Sharpe 1.93.
  - `ttl10_12_entry55_q50`: 29 trades, `+$4.96`, 68.97% win, max DD `-$1.66`,
    Sharpe 1.88.
- Refreshed live websocket transfer check on the full current BTC15M capture:
  `2026-05-12 10:42:45 UTC` through about `2026-05-16 10:18 UTC`,
  7.7M top-book rows and 363 events. Output:
  `backtest_outputs\btc15m_f2_live_ws_transfer_20260516_041505`.
- Live websocket proxy +2c stress:
  - `base_f2`: 113 trades, `-$7.63`, 50.44% win, max DD `-$11.36`, Sharpe -1.44.
  - `ttl6_12`: 104 trades, `-$5.29`, 51.92% win, max DD `-$9.66`, Sharpe -1.04.
  - `ttl10_12_entry55_q50`: 39 trades, `+$3.76`, 64.10% win, max DD `-$1.73`,
    Sharpe 1.23.
- Refreshed Jan7-17 Predexon in smaller chunks after the full 10-day materialize
  hit a memory ceiling. Outputs:
  `backtest_outputs\btc15m_f2_predexon_jan07_10_fresh_20260516_042521`,
  `backtest_outputs\btc15m_f2_predexon_jan10_13_fresh_20260516_042521`, and
  `backtest_outputs\btc15m_f2_predexon_jan13_17_fresh_20260516_042521`.
- Jan7-17 chunk aggregate +2c stress:
  - `base_f2`: 196 trades, `-$12.96`, 45.41% win.
  - `ttl6_12`: 179 trades, `-$10.22`, 46.37% win.
  - `ttl10_12_entry55_q50`: 54 trades, `-$3.59`, 42.59% win.
- Existing exact Predexon checks:
  - Apr15-30 strict `ttl10_12_entry55_q50`: 49 trades, `+$4.12`, 61.22% win.
  - May1-12 strict `ttl10_12_entry55_q50`: 17 trades, `-$0.93`, 47.06% win.
- Interpretation:
  broad F2 and `ttl6_12` are rejected because they lose on live websocket
  transfer. The strict `ttl10_12_entry55_q50` subset is the only F2-style
  candidate still worth shadowing: it wins Apr1-14, Apr15-30, and May12-16
  live capture, but it loses Jan7-17 and May1-12. January is weaker evidence
  because those rows use `coinbase_open_minute_proxy` target metadata, while
  April/May are mostly Kalshi metadata, but May1-12 is still a real negative
  transfer check. Do not deploy strict F2 yet; keep it as paper shadow only
  and require more future websocket holdout before promotion.

2026-05-16 BTC15M live-compatible XGBoost future holdout check:

- Freeze protocol:
  `docs\2026-05-15_btc15m_future_holdout_freeze.md`.
  Candidate was frozen before future evaluation:
  `backtest_outputs\btc15m_live_compatible_models_20260515`,
  model `xgboost_tabular`, gate `pred_win_prob >= 0.84` and
  `pred_ev >= 0.14`, live-compatible features only.
- Ran the frozen model on post-freeze local websocket capture only:
  `2026-05-15 23:36:03 UTC` through `2026-05-16 10:32:53 UTC`.
  Output:
  `backtest_outputs\btc15m_livecompat_xgb_future_ws_20260516_043252`.
- Post-freeze websocket result:
  - `xgboost_tabular`: 5 trades, raw `-$1.25`, +2c stress `-$1.35`,
    40.00% win, max DD `-$1.50`, Sharpe -1.18.
  - `logistic_calibrator`: 29 trades, raw `-$0.82`, +2c stress `-$1.40`,
    58.62% win, max DD `-$5.28`, Sharpe -0.52.
- Diagnostic:
  the XGBoost failures included high-entry 68-72c rows where the lognormal
  fair-value model often disagreed or showed weak edge. Simple sanity overlays
  tested read-only on the already-selected model trades did not rescue the
  frozen candidate without killing nearly all trade count; e.g. `entry<=60c`
  reduced post-freeze XGBoost to 1 losing trade, and fair-value agreement rules
  often produced zero post-freeze trades.
- Decision:
  demote the live-compatible XGBoost candidate. It is no longer a leading
  BTC15M promotion candidate because its first clean future websocket holdout
  failed. Keep the artifact for research, not deployment.

2026-05-16 BTC15M RV/fair-value calibration sanity check:

- Tested whether the F2 fair-value model failure is just a realized-volatility
  scale problem. Recomputed lognormal probabilities from causal `btc_spot`,
  `floor_strike`, `ttl_min`, and `rv_60m * scale` for scales
  `0.50, 0.75, 1.00, 1.25, 1.50, 2.00`.
- Output:
  `backtest_outputs\btc15m_rv_scale_sanity_20260516_0445\rv_scale_summary.csv`.
- Protocol:
  no websocket tuning; select the best scale by Apr1-14 only, then inspect
  Apr15-30, May1-12, and Jan7-17 chunks. Rules used the same first-event,
  top-ask, visible-quantity, taker-fee, and +2c stress assumptions as the F2
  validators.
- Result:
  Apr1-14 selects scale `1.00` for both broad F2 and strict
  `ttl10_12_entry55_q50`.
  - Strict F2 scale `1.00`: Apr1-14 `+$4.96`, Apr15-30 `+$4.12`,
    May1-12 `-$0.93`, Jan7-17 `-$9.65`.
  - Strict F2 scale `2.00`: Jan7-17 improves to `+$2.11`, but Apr15-30 falls
    to `-$0.14`, May1-12 remains `-$1.09`, and trade count collapses.
  - Broad F2 scale `1.00`: Apr1-14 `+$9.48`, Apr15-30 `+$3.11`,
    May1-12 `-$4.69`, Jan7-17 `-$30.37`.
- Interpretation:
  there is no single RV multiplier that fixes transfer. The bad windows are not
  explained by a stable volatility under/over-scaling issue. More complex fair
  value work needs a regime-aware model and must validate on future websocket
  capture; do not patch production with a volatility multiplier.

2026-05-16 BTC15M orderbook/history feature pass:

- Read-only subagent review of same-contract price-history and orderbook
  features found no deployable additive rule.
- Summary:
  `book_imbalance` and `micropressure` filters made small Apr1-14 gains on
  strict F2, but did not repair May/Jan transfer. `yes_mid`/`side_mid` history
  features were too sparse in the selected strict-F2 artifacts outside April,
  and momentum/no-chase history filters failed train/validation or lacked
  coverage.
- Decision:
  keep same-contract price-history features as research-only. They are not a
  robust deployment gate until the websocket replay artifact stores the same
  feature columns and a frozen rule passes future capture.

2026-05-16 candidate readiness and data-fidelity audit:

- Read-only subagent audit compared the current tracked candidates:
  BTC1H `high_conf_80_entry70_no_chase`, BTC15M strict
  `ttl10_12_entry55_q50`, and BTC15M live-compatible XGBoost.
- Current ranking:
  1. BTC1H `high_conf_80_entry70_no_chase`: closest to deployable, but still
     paper-only. Evidence: Feb9-May6 direct aggregate 32 trades, `+$4.78`,
     81.25% win, max DD `-$1.41`, p `0.0479`; websocket cadence checks
     positive. Blocker: only 1 settled post-freeze paper trade.
  2. BTC15M strict `ttl10_12_entry55_q50`: keep as lower-confidence shadow.
     Evidence: wins Apr1-14, Apr15-30, and May12-16 local websocket proxy
     replay. Blockers: loses May1-12 and Jan7-17; official-settlement subset
     in live replay is only 6 trades.
  3. BTC15M live-compatible XGBoost/logistic: demoted/rejected for promotion.
     Evidence: first clean post-freeze websocket holdout lost, and April-only
     repair rules either fail test, vanish on websocket, or have too little
     sample size.
- Data-fidelity hierarchy:
  - Promotion-grade: local live websocket capture with official Kalshi
    lifecycle/private result, plus exact first-signal-per-event FOK/reprice
    evidence.
  - Useful but not promotion-grade: Predexon snapshots. They are valuable for
    broad rejection and rough transfer, but provider-time snapshots cannot prove
    live FOK fillability, queue position, or websocket gap behavior.
  - Discount heavily: proxy settlement and January BTC15M proxy-target metadata.
    January is currently useful as a warning sign, not a definitive promotion
    gate.
- Operational note:
  current BTC15M shadow log is dry-run style and can print repeated
  `would FOK` messages for one event. Treat it as opportunity telemetry, not a
  settled PnL ledger, unless the wrapper records first-event paper fills with
  official settlement.

2026-05-16 BTC15M strict F2 paper-shadow fix:

- Patched `scripts\btc15m_lowdd_live.py` to support a real `--mode paper`
  path for BTC15M with a separate mock bankroll. Paper mode now writes
  `paper_filled` rows under `paper_<live_mode>` and can report realized/open
  paper PnL from the local trade DB without depending on the real Kalshi
  portfolio.
- Patched `scripts\btc15m_f2_ttl10_12_shadow.py` from dry-run to paper mode
  with `$100` mock bankroll and the frozen strict F2 gates:
  TTL `10-12m`, spread `<=2c`, entry `2-55c`, side fair probability `>=60%`,
  fair edge `>=12c`, visible top ask quantity `>=50`, max one contract, and
  max final reprice worsening `2c`.
- Verification:
  `python -m py_compile scripts\btc15m_lowdd_live.py scripts\btc15m_f2_ttl10_12_shadow.py scripts\test_btc15m_shadow_config.py`
  passed. `python -m unittest scripts.test_btc15m_shadow_config` passed. A
  20-second paper-mode smoke run connected to Kalshi and Kraken websockets,
  used the `$100` mock bankroll, logged paper PnL reports, and exited cleanly.
- Restarted only the old BTC15M strict shadow process. New log:
  `logs\btc15m_f2_ttl10_12_shadow_20260516_045857.out.log`. It is running in
  paper mode and writing to
  `%USERPROFILE%\.btc_kalshi_bot\btc15m_f2_ttl10_12_shadow_trades.db`.

2026-05-16 BTC15M F2 win/loss streak and pattern detectability:

- Script:
  `scripts\analyze_btc15m_f2_streak_patterns.py`.
- Main output:
  `backtest_outputs\btc15m_f2_streak_patterns_20260516_050137`.
  External checks:
  `backtest_outputs\btc15m_f2_streak_patterns_may_external_20260516_0503`
  and
  `backtest_outputs\btc15m_f2_streak_patterns_jan_external_20260516_0503`.
- Protocol:
  Apr1-14 was diagnostic/training, Apr15-30 validation, May1-12 and Jan13-17
  external checks. This pass was intentionally read-only and did not use future
  websocket data for fitting.
- Result:
  there is no convincing Markov/streak signal. For strict
  `ttl10_12_entry55_q50`, Apr1-14 `p(win after win)=0.684`,
  `p(win after loss)=0.667`, Fisher p `1.000`; Apr15-30
  `p(win after win)=0.567`, `p(win after loss)=0.667`, Fisher p `0.554`.
  Max loss streak permutation p-values were high, so observed loss streaks look
  compatible with random clustering.
- Several median gates looked tempting on Apr15-30 but failed transfer:
  `base_f2 rv_60m >= 0.3198` was Apr15-30 `+$8.62`, May1-12 `+$0.66`, but
  Jan13-17 `-$8.98`; `base_f2 ttl_min >= 9.11` was Apr15-30 `+$3.37`,
  May1-12 `+$2.11`, but Jan13-17 `-$3.92`; `base_f2 distance_bps >= -4.43`
  was Apr15-30 `+$1.56`, May1-12 `+$2.54`, but Jan13-17 `-$8.05`.
- ML sanity on the same train/validation split did not produce a deployable
  BTC15M streak/pattern model. Logistic gates improved broad F2 validation
  (`base_f2` selected 81/184 Apr15-30 trades for `+$5.29` vs all `+$3.11`),
  but this is only a hypothesis and needs external/live transfer. HGB overfit:
  train AUC high, validation PnL weak/negative.
- Decision:
  do not add a streak, prior-win, RV, TTL, or distance gate to production. The
  strict F2 paper shadow remains useful for future websocket evidence, but
  streak structure is not currently an exploitable edge.

2026-05-16 BTC15M F2 conservative entry/liquidity gate rerun:

- A read-only failure-window review suggested strict F2 plus `entry <= 0.50`
  and `visible_qty >= 250` might rescue the bad windows. I added this as a
  named fixed rule in `scripts\backtest_btc15m_f2_predexon_range.py`:
  `ttl10_12_entry50_q250`.
- Important correction:
  a hand-filtered estimate looked too good because it filtered already-selected
  trades. The proper validator applies the gate before first-event selection,
  preserving first qualifying signal semantics.
- Clean Predexon reruns with +2c stress:
  - Apr1-14:
    `ttl10_12_entry55_q50` 28 trades, `+$4.48`, 67.86% win, max DD `-$1.66`;
    `ttl10_12_entry50_q250` 21 trades, `+$3.70`, 66.67% win, max DD `-$1.03`.
  - Apr15-30:
    `ttl10_12_entry55_q50` 47 trades, `+$4.11`, 61.70% win, max DD `-$2.23`;
    `ttl10_12_entry50_q250` 35 trades, `+$4.41`, 62.86% win, max DD `-$1.49`.
  - May1-12:
    `ttl10_12_entry55_q50` 17 trades, `-$0.93`, 47.06% win, max DD `-$3.15`;
    `ttl10_12_entry50_q250` 10 trades, `+$1.06`, 60.00% win, max DD `-$1.03`.
  - Jan13-17:
    `ttl10_12_entry55_q50` 29 trades, `-$4.44`, 34.48% win, max DD `-$4.48`;
    `ttl10_12_entry50_q250` 15 trades, `-$2.33`, 33.33% win, max DD `-$2.38`.
- Live websocket replay through `2026-05-16 11:13:47 UTC`:
  `backtest_outputs\btc15m_f2_live_ws_entry50_q250_20260516_0520`.
  Proxy +2c: 33 trades, `+$4.05`, 63.64% win, max DD `-$1.57`,
  Sharpe `1.45`. Official subset: 6 trades, `+$1.86`, 83.33% win.
- Decision:
  `ttl10_12_entry50_q250` is better than strict F2 on May1-12 and live
  websocket replay, and has lower drawdown in April, but it still fails
  Jan13-17. Keep it as a research/paper-shadow candidate only. It is not
  deployable until the January failure is explained or enough future websocket
  evidence demonstrates the older Predexon/proxy regime is not representative.

2026-05-16 BTC15M source-of-truth and liquidity/regime follow-up:

- Expanded `scripts\backtest_btc15m_f2_predexon_range.py` with causal rule
  variants, applied before first-event selection:
  `ttl10_12_entry50_q500`, `ttl10_12_entry50_q1000`,
  `ttl10_12_entry50_q500_rvmax040`, and
  `ttl10_12_entry50_q250_qspeed05`.
- New Predexon outputs:
  `backtest_outputs\btc15m_f2_liq_regime_apr01_14_20260516_052801`,
  `backtest_outputs\btc15m_f2_liq_regime_apr15_30_20260516_052801`,
  `backtest_outputs\btc15m_f2_liq_regime_may01_12_20260516_052801`, and
  `backtest_outputs\btc15m_f2_liq_regime_jan13_17_20260516_052801`.
- +2c stress results:
  - Apr1-14: q250 `22 / +$4.18 / 68.18% / DD -$1.03`;
    q500 `21 / +$3.72 / 66.67% / DD -$1.03`; q1000
    `18 / +$3.31 / 66.67% / DD -$1.02`; qspeed05
    `20 / +$5.21 / 75.00% / DD -$0.97`.
  - Apr15-30: q250 `37 / +$4.42 / 62.16% / DD -$1.49`;
    q500 `37 / +$4.46 / 62.16% / DD -$1.49`; q1000
    `28 / +$3.92 / 64.29% / DD -$1.52`; qspeed05
    `34 / +$4.08 / 61.76% / DD -$1.89`.
  - May1-12: q250 `10 / +$1.06 / 60.00% / DD -$1.03`;
    q500 `9 / +$1.64 / 66.67% / DD -$1.03`; q1000
    `6 / +$0.93 / 66.67% / DD -$1.03`; qspeed05
    `9 / +$1.59 / 66.67% / DD -$0.54`.
  - Jan13-17: q250 `15 / -$2.33 / 33.33% / DD -$2.38`;
    q500 `11 / -$2.04 / 27.27% / DD -$2.09`; q1000
    `1 / -$0.34 / 0.00% / DD -$0.34`; qspeed05
    `10 / -$2.69 / 20.00% / DD -$2.69`.
- Live websocket replay outputs:
  `backtest_outputs\btc15m_f2_live_ws_liq_q250_20260516_053202`,
  `backtest_outputs\btc15m_f2_live_ws_liq_q500_20260516_053202`, and
  `backtest_outputs\btc15m_f2_live_ws_liq_q1000_20260516_053202`.
  Spot-model +2c proxy results were q250 `34 / +$3.52 / 61.76% /
  DD -$1.64`, q500 `31 / +$3.06 / 61.29% / DD -$1.65`, q1000
  `29 / +$3.01 / 62.07% / DD -$1.76`. Official subset was only
  6 trades, all proxy/official results matched.
- Patched `scripts\backtest_btc15m_f2_live_ws_holdout.py` to support
  `--btc-model rolling60`, a Coinbase/Kraken proxy for the 60-second averaging
  used by Kalshi crypto settlements. This was a research-only replay option,
  not a live bot change.
- Rolling-60 live replay outputs:
  `backtest_outputs\btc15m_f2_live_ws_roll60_q250_20260516_053920`,
  `backtest_outputs\btc15m_f2_live_ws_roll60_q500_20260516_053920`, and
  `backtest_outputs\btc15m_f2_live_ws_roll60_q1000_20260516_053920`.
  Rolling60 worsened live replay: q250 `60 / -$4.17 / 45.00% /
  DD -$7.29`, q500 `58 / -$5.09 / 43.10% / DD -$7.88`, q1000
  `57 / -$5.42 / 42.11% / DD -$8.20`.
- Source-of-truth audit:
  Kalshi crypto markets settle from CF Benchmarks RTI 60-second averages, not
  Coinbase/Kraken last trade. CF Benchmarks says BRTI is the price input for
  Kalshi Bitcoin event-contract settlement, and Kalshi's help page says crypto
  expirations average 60 one-second RTI values before expiration.
- In our January Predexon BTC15M data, `floor_strike` is missing and the fair
  value model uses a Coinbase open-minute proxy as the price-to-beat. The
  Kalshi settlement result is official, but the model input is not. Across
  Jan13-17 events, official result vs Coinbase-proxy result mismatched 48/331
  events (`14.5%`). For q250 selected trades, official PnL was `-$2.33`, but
  diagnostic Coinbase-proxy settlement PnL would have been `+$0.67`; for q500,
  official `-$2.04` vs proxy `+$0.96`.
- Decision:
  the January failure should not be ignored, but it is primarily evidence that
  Coinbase/Kraken proxy fair value is not the correct core model for BTC15M.
  Do not deploy new BTC15M fair-value variants until we either obtain CF BRTI
  live/history or prove on a large official/live websocket holdout that the
  exchange-spot proxy remains profitable after source-mismatch risk.

2026-05-16 BTC1H replay-fidelity correction:

- Patched `scripts\replay_btc1h_core_ws_counterfactual.py` with a
  `--use-signal-scan-log` mode. This replays top-of-book state forward but
  only evaluates at captured `signal_scan` timestamps. Ordinary delta scans
  only evaluate markets changed since the previous scan; full-chain scans are
  allowed only when the captured scan reason/counts indicate a full scan
  (`initial_full_book`, `event_refresh`, `btc_candle_refresh`, `kraken`, etc.).
- Also added `--live-scan-semantics` for quote-group replays and changed the
  default BTC spot freshness gate from `180s` to the live bot's `15s`.
- Found and fixed a second replay mismatch source: the script was recomputing
  `rv_60m` from close-to-close returns, while the live bot's BTC cache uses
  its normalized live volatility column. Replay now preserves `rv_15m`,
  `rv_60m`, `rv_1d`, and `rkurt_60m` from the live BTC cache unless
  `--recompute-btc-rv` is explicitly supplied.
- Evidence:
  - Old replay `backtest_outputs\btc1h_entry70_shadow_ws_replay_stride1_20260516_0525`
    invented an extra `KXBTCD-26MAY1606-T78099.99` winner because it evaluated
    a stale market inside the full event surface after a different ticker
    changed.
  - Corrected signal-log replay
    `backtest_outputs\btc1h_entry70_livefaithful_onevariant_preserverv_20260516_060429`
    produced one trade, matching the shadow ledger's one filled trade:
    `KXBTCD-26MAY1605-T78399.99` NO at `0.70`.
- Remaining caveat:
  the corrected replay matches the trade set, but the reconstructed model
  probability for that trade (`p_yes=0.117`) still differs from the live
  `signal_scan` / shadow ledger value (`p_yes=0.142`). The most likely cause is
  live event TTL staleness between event refreshes; the live bot stores
  `ttl_hours` at event refresh time and reuses it until the next refresh. This
  did not change pass/fail for the matched trade, but future BTC1H promotion
  tests should either reconstruct event-refresh TTL exactly or import the live
  signal path directly.

2026-05-16 BTC15M F2 deployment-gate audit:

- Added `scripts\audit_btc15m_f2_deployment_gate.py`, an audit-only script
  that freezes four F2 candidates before reading live data:
  `q250`, `q250_qspeed05`, `q500`, and `q1000`. It loads the live websocket
  capture once, evaluates every candidate with the same causal execution
  assumptions, joins the existing historical Predexon windows, and writes a
  gate report.
- Output:
  `backtest_outputs\btc15m_f2_deployment_gate_20260516_135343`.
- Live capture covered `2026-05-12 10:42:45 UTC` through
  `2026-05-16 19:53:44 UTC`, with `8,567,091` top-of-book rows,
  `114,356` BTC rows, `37,237` lifecycle rows, `404` meta markets, and `111`
  official-result markets.
- Gate results:
  - `q250`: Apr1-14 `22 / +$4.18`, Apr15-30 `37 / +$4.42`,
    May1-12 `10 / +$1.06`, Jan13-17 `15 / -$2.33`,
    live proxy `37 / +$3.90 / DD -$1.57`, official subset
    `6 / +$1.86`.
  - `q250_qspeed05`: Apr1-14 `20 / +$5.21`, Apr15-30
    `34 / +$4.08`, May1-12 `9 / +$1.59`, Jan13-17
    `10 / -$2.69`, live proxy `32 / +$2.53 / DD -$2.12`,
    official subset `5 / +$1.40`.
  - `q500`: Apr1-14 `21 / +$3.72`, Apr15-30 `37 / +$4.46`,
    May1-12 `9 / +$1.64`, Jan13-17 `11 / -$2.04`,
    live proxy `35 / +$2.92 / DD -$2.14`, official subset
    `6 / +$1.86`.
  - `q1000`: Apr1-14 `18 / +$3.31`, Apr15-30 `28 / +$3.92`,
    May1-12 `6 / +$0.93`, Jan13-17 `1 / -$0.34`,
    live proxy `33 / +$2.75 / DD -$2.26`, official subset
    `6 / +$1.88`.
- Verdict:
  none are deployment-ready under the conservative gate. All have positive
  live proxy PnL and positive official subset PnL, but all fail because:
  `jan_negative_or_source_mismatch`, `too_few_live_proxy_trades`, and
  `too_few_live_official_trades`.
- Interpretation:
 these remain promising paper/research candidates, not production candidates.
 The next proof requirement is more official/live websocket data and a clean
 BTC15M settlement-source solution; do not promote based on proxy PnL alone.

2026-05-16 BTC15M expanded Predexon/live replay refresh:

- Used all completed BTC15M Predexon files currently available without stopping
  the ongoing backfill. Inventory at refresh time:
  - January: `2026-01-08 02:45 UTC` through `2026-01-27 12:30 UTC`
    (`~11.43M` snapshots in manifest).
  - April: `2026-04-01` through `2026-04-30`.
  - May: through `2026-05-12 22:30 UTC`.
  - Latest live websocket holdout: `2026-05-12 10:42:45 UTC` through
    `2026-05-16 20:14:38 UTC`.
- The full one-shot Predexon range run wrote a prepared cache but failed in a
  pandas merge due memory pressure. Re-ran January in smaller non-overlapping
  chunks and combined with the existing April/May outputs.
- Combined output:
  `backtest_outputs\btc15m_f2_combined_predexon_live_20260516_142944`.
- Combined Predexon, one contract, 2c adverse-entry stress:
  - `q1000`: `63` trades, `+$8.95`, return on $100 `+8.95%`, ROP `29.78%`,
    win rate `61.90%`, path DD `-$2.24`, Sharpe `2.29`.
  - `q500`: `101` trades, `+$8.65`, return on $100 `+8.65%`, ROP `17.89%`,
    win rate `56.44%`, path DD `-$5.77`, Sharpe `1.72`.
  - `q250_qspeed05`: `103` trades, `+$7.98`, return on $100 `+7.98%`,
    ROP `16.28%`, win rate `55.34%`, path DD `-$6.93`, Sharpe `1.59`.
  - `q250`: `130` trades, `+$7.71`, return on $100 `+7.71%`, ROP `12.18%`,
    win rate `54.62%`, path DD `-$7.44`, Sharpe `1.35`.
  - Broad `base_f2` remained negative: `816` trades, `-$2.12`.
- Latest live websocket evidence, same frozen candidates:
  - `q250`: `37` trades, `+$3.90`, DD `-$1.57`, Sharpe `1.31`.
  - `q500`: `35` trades, `+$2.92`, DD `-$2.14`, Sharpe `1.00`.
  - `q1000`: `33` trades, `+$2.75`, DD `-$2.26`, Sharpe `0.97`.
  - Official-result subset is still tiny (`5-6` trades depending on variant),
    so this is not yet deployment proof.
- New candidate worth tracking, not promoted:
  `q1000_yes` (same `q1000` filters but YES side only) had `30` Predexon
  trades for `+$7.97`, DD `-$0.98`, Sharpe `3.31`, and `7` live proxy trades
  for `+$1.39`, DD `-$0.52`. This was discovered during expanded analysis, so
  it needs fresh untouched/live validation before any production use.
- Interpretation:
  strict liquidity helps, and the q1000/high-liquidity subset looks better than
  q250 on Predexon, but the Jan13-23 proxy-settlement regime is a real warning.
  Treat January negatives as either source-mismatch risk or regime failure until
  BTC15M official strike/settlement source is solved.

2026-05-16 BTC15M/BTC1H promotion status refresh:

- Refreshed the BTC15M deployment replay on the live websocket capture:
  `backtest_outputs\btc15m_f2_deployment_gate_latest_20260516_154453`.
  The capture now runs through `2026-05-16 21:45 UTC` and still fails every
  broad F2 candidate under the conservative gate.
- Latest BTC15M broad-candidate live proxy results:
  - `q250`: `38` trades, `+$3.37`, DD `-$1.64`, official subset
    `6 / +$1.86`.
  - `q500`: `36` trades, `+$2.39`, DD `-$2.14`, official subset
    `6 / +$1.86`.
  - `q1000`: `34` trades, `+$2.22`, DD `-$2.33`, official subset
    `6 / +$1.88`.
  - All fail due too few live proxy trades, too few official live settlements,
    and the January negative/source-mismatch blocker.
- Refreshed the side-filter gate:
  `backtest_outputs\btc15m_f2_side_gate_20260516_154703`.
  `q1000_yes` remains the cleanest BTC15M research candidate but still fails
  promotion: `32` Predexon trades for `+$7.23`, `7` live proxy trades for
  `+$1.39`, and only `2` official-settled live trades.
- Independent read-only subagent audit agreed:
  no BTC15M or BTC1H candidate is real-money deployable today under the
  current conservative gate. The best BTC15M forward candidate is
  `q1000_yes`; the best BTC1H forward candidate is
  `high_conf_80_entry70_no_chase`, blocked mainly by insufficient
  post-freeze shadow settlements.
- Operational follow-up started:
  - BTC15M q1000 paper shadow:
    `logs\btc15m_f2_q1000_shadow_20260516_154320.out.log`.
  - BTC15M q1000 YES-only paper shadow:
    `logs\btc15m_f2_q1000_yes_shadow_20260516_154320.out.log`.
  - BTC1H high-confidence multi-shadow:
    `logs\btc_1hr_highconf_multi_shadow_20260516_154813.out.log`.
- These are paper-only forward validators. Do not promote them to real-money
  execution until the live/official-settled evidence threshold is met.

2026-05-16 BTC15M Jan28 tail refresh:

- New Predexon late-window data arrived while the backfill continued. Ran:
  `backtest_outputs\btc15m_f2_predexon_jan28_tail_20260516_155406`
  for `2026-01-28 02:00 UTC` through `2026-01-29 00:00 UTC`.
- The fresh tail weakens low-liquidity variants:
  - `q250`: `5` trades, `-$1.31`.
  - `q500`: `3` trades, `-$0.22`.
  - `q1000`: `1` trade, `+$0.75`.
- Folded this into:
  `backtest_outputs\btc15m_f2_combined_predexon_live_refresh2_20260516_155638`
  and refreshed the side gate:
  `backtest_outputs\btc15m_f2_side_gate_refresh2_20260516_155645`.
- Updated combined Predexon, one contract, 2c adverse-entry stress:
  - `q1000`: `66` trades, `+$8.96`, win `60.61%`, DD `-$2.24`,
    Sharpe `2.23`.
  - `q500`: `108` trades, `+$7.67`, win `54.63%`, DD `-$5.77`,
    Sharpe `1.47`.
  - `q250_qspeed05`: `109` trades, `+$7.67`, win `54.13%`,
    DD `-$6.93`, Sharpe `1.49`.
  - `q250`: `140` trades, `+$5.14`, win `52.14%`, DD `-$7.44`,
    Sharpe `0.86`.
  - `base_f2`: `860` trades, `-$3.62`.
- Side result after the refresh:
  `q1000_yes` is now `33` Predexon trades for `+$7.98`, win `69.70%`,
  DD `-$0.98`, Sharpe `3.04`; live evidence is unchanged and still too thin
  (`7` proxy trades, `2` official-settled trades). It remains the best BTC15M
  shadow candidate, not a deployable live strategy.

2026-05-16 BTC15M official settlement proxy audit:

- Added audit-only script:
  `scripts\audit_btc15m_official_proxy_settlement.py`.
- Output:
  `backtest_outputs\btc15m_official_proxy_audit_20260516_161842`.
- Compared all captured BTC15M official Kalshi `determined` lifecycle results
  against the local BTC-close proxy used by websocket replay.
- Results:
  - Official markets: `111`.
  - Proxy-computable markets: `111`.
  - Matches: `107`.
  - Mismatches: `4`.
  - Overall match rate: `96.40%`.
  - Farther than `$10` from strike: `102` markets, `100` matches
    (`98.04%`).
- Mismatches:
  - `KXBTC15M-26MAY121700-00`: official YES, proxy NO, proxy distance
    `-$7.75`.
  - `KXBTC15M-26MAY150645-45`: official NO, proxy YES, proxy distance
    `+$12.22`.
  - `KXBTC15M-26MAY151345-45`: official YES, proxy NO, proxy distance
    `-$15.66`.
  - `KXBTC15M-26MAY151445-45`: official NO, proxy YES, proxy distance
    `+$0.68`.
- Interpretation:
  proxy settlement is good enough for research triage but not good enough to
  replace official-settled validation for deployment. Candidate-specific
  `6/6` proxy/official agreement is encouraging, but the all-market mismatch
  rate keeps official-settled sample size as a real blocker.

2026-05-16 BTC15M REST official fill for replay trades:

- Added audit-only script:
  `scripts\fill_btc15m_live_ws_official_results.py`.
- Output:
  `backtest_outputs\btc15m_live_ws_rest_official_20260516_162103`.
- This filled finalized Kalshi REST `result` for all unique closed market
  tickers appearing in the latest live-replay candidate trades, then recomputed
  official PnL with the same 2c adverse-entry stress.
- This materially changed the BTC15M deployment view:
  - `q250`: `38` official-filled trades, `-$1.63`, win `47.37%`.
  - `q500`: `36` official-filled trades, `-$1.61`, win `47.22%`.
  - `q1000`: `34` official-filled trades, `-$1.78`, win `47.06%`.
  - `q250_qspeed05`: `33` official-filled trades, `-$2.00`, win `45.45%`.
- Refreshed side gate with REST-filled official results:
  `backtest_outputs\btc15m_f2_side_gate_rest_official_20260516_162134`.
- Side-gate result:
  - Broad `q1000/q500/q250` are rejected because official-filled live PnL is
    negative despite positive proxy PnL.
  - `q1000_yes` remains positive on the REST-filled live subset:
    `7` live official trades for `+$1.39`, win `71.43%`, but it still fails
    promotion because the sample is too small (`33` Predexon trades and only
    `7` live official trades).
- Operational decision:
  stopped the broad BTC15M `q1000` paper shadow. Keep only `q1000_yes` for
  BTC15M forward validation.
- Also paused the exact BTC1H real-money
  `.codex_work\run_btc_1hr_late_only_loss_guard_live.py` process after checking
  the promotion audit still had zero passing BTC1H candidates. The BTC1H
  high-confidence multi-strategy paper shadow remains running, and the BTC15M
  capture/backfill processes remain running.

2026-05-16 BTC15M q1000 official live side split:

- Audited the REST-filled live replay rows directly.
- `q1000` broad split on live official REST results:
  - YES side: `7` trades, `+$1.39`, win `71.43%`.
  - NO side: `27` trades, `-$3.17`, win `40.74%`.
  - Combined: `34` trades, `-$1.78`.
- Interpretation:
  the broad q1000 signal was only positive under proxy settlement because the
  NO side was being mis-scored by the Coinbase-close proxy for several markets.
  On actual Kalshi outcomes, the NO side is not viable. Any future BTC15M F2
  promotion must be YES-only unless a new official-settlement backtest proves
  otherwise.

2026-05-16 BTC15M q1000_yes live staging patch:

- Added disabled-by-default rolling risk controls to
  `scripts\btc15m_lowdd_live.py`:
  - `BTC15M_RISK_WINDOW_HOURS`.
  - `BTC15M_ROLLING_TRADE_CAP`.
  - `BTC15M_ROLLING_PREMIUM_CAP_DOLLARS`.
- The risk gate counts `submitted`, `filled`, and `partial_filled` trades in
  the selected strategy mode. It blocks before order submission if either the
  rolling trade count is already at cap or the new trade would push rolling
  premium-at-risk over cap. This is intentionally premium-at-risk, not a
  fragile settlement-PnL estimate, because official settlement can lag and
  proxy settlement has already been proven deployment-unsafe.
- Added staged live wrapper:
  `scripts\btc15m_f2_q1000_yes_live.py`.
- Wrapper freezes:
  - strategy `h02`.
  - TTL `10..12` minutes.
  - spread `<= 2c`.
  - edge `>= 12c`.
  - entry `2c..50c`.
  - side probability `>= 60%`.
  - top visible qty `>= 1000`.
  - YES-only.
  - one contract max.
  - rolling 24h cap: max `4` at-risk trades and max `$2.00` at-risk premium.
- Validation:
  - `python -m py_compile scripts\btc15m_lowdd_live.py scripts\btc15m_f2_q1000_yes_live.py scripts\btc15m_f2_q1000_yes_shadow.py scripts\test_btc15m_shadow_config.py`
    passed.
  - `python -m unittest scripts.test_btc15m_shadow_config -v` passed
    (`5` tests).
  - Dry-run smoke with the staged live gates connected to Kalshi and Kraken WS,
    displayed the expected q1000 YES-only config and risk caps, and exited
    cleanly after `20s`.
- Current deployment decision:
  this wrapper is staged but not auto-started. Strict promotion still has not
  passed; `q1000_yes` is the only BTC15M forward candidate, but it remains
  sample-size limited on official-settled live data.

2026-05-16 BTC15M refreshed official gate after staging:

- Refreshed current live replay gate:
  `backtest_outputs\btc15m_f2_deployment_gate_refresh_20260516_163841`.
- Filled REST official Kalshi outcomes for the refreshed live replay trades:
  `backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_163841`.
- Re-ran side gate:
  `backtest_outputs\btc15m_f2_side_gate_rest_official_refresh_20260516_163841`.
- Key correction:
  broad F2 variants are officially negative on live replay even though proxy
  replay is positive.
  - `q250`: `37` official trades, `-$1.10`, win `48.65%`.
  - `q500`: `35` official trades, `-$1.08`, win `48.57%`.
  - `q1000`: `33` official trades, `-$1.25`, win `48.48%`.
  - `q250_qspeed05`: `32` official trades, `-$1.47`, win `46.88%`.
- Side-gate result:
  - `q1000_yes`: Predexon `33` trades, `+$7.98`, win `69.70%`,
    Sharpe `3.04`; live official `7` trades, `+$1.39`, win `71.43%`.
  - Still fails strict promotion due `too_few_predexon_trades`,
    `too_few_live_proxy_trades`, and `too_few_live_official_trades`.
  - `q1000_yes_edge15`: even better Predexon stats (`27` trades,
    `+$8.09`, win `74.07%`, Sharpe `3.69`) but only `3` live official
    trades, so it is less deployable, not more.
- Added Jan29 partial Predexon slice from the ongoing backfill:
  `backtest_outputs\btc15m_f2_predexon_jan29_partial_20260516_164316`.
  It added no `q1000` trades, only tiny q250/q500 evidence.
- Combined Jan29 partial with prior Predexon:
  `backtest_outputs\btc15m_f2_combined_predexon_plus_jan29_20260516_164544`.
- Re-ran side gate with Jan29 included:
  `backtest_outputs\btc15m_f2_side_gate_plus_jan29_20260516_164544`.
  The deploy decision did not change.
- REST check note:
  direct `/markets/{ticker}` REST lookup for some old January tickers returns
  `404`, likely because those markets are outside the current endpoint's
  availability window. Do not use current REST 404 as evidence that old
  metadata results are wrong; use the local market manifest result column for
  historical scoring unless a historical endpoint-specific verifier is added.
- Current decision remains:
  no strategy passes strict production promotion. The only real-money path that
  is technically staged is `q1000_yes` micro-live with hard caps, but that would
  be a bounded forward test, not a fully proven deployment.

2026-05-16 BTC15M YES-only rescue search:

- Added audit/search bridge:
  `scripts\audit_btc15m_yes_rescue_search.py`.
- Purpose:
  search only historical Predexon side-candidate rows for broader YES-only
  filters, then evaluate screened rules once on live websocket capture with
  REST-filled official Kalshi outcomes. This was designed to avoid using live
  official outcomes as the search objective.
- Output:
  `backtest_outputs\btc15m_yes_rescue_search_20260516_1723`.
- Search universe:
  - Predexon rows: `260,610`.
  - Rules tested: `1,944`.
  - Screened historical rules carried to live: `18`.
  - Live YES candidate rows: `44,470`.
  - Live unique tickers with REST official outcomes: `116`.
- Result:
  no deploy-ready strategy.
- Best live-official rows:
  - `yes_rescue_00475`: Predexon `58` trades, `+$7.55`, Sharpe `2.07`,
    one bad window; live official only `2` trades, `+$0.93`.
  - `yes_rescue_00421`: Predexon `51` trades, `+$6.04`, Sharpe `1.74`,
    one bad window; live official only `2` trades, `+$0.93`.
  - `yes_rescue_00134`: Predexon `52` trades, `+$8.23`, Sharpe `2.37`,
    one bad window; live official `7` trades, `+$0.39`, but much weaker than
    proxy (`+$1.39`).
  - `yes_rescue_00152`: Predexon `66` trades, `+$9.06`, Sharpe `2.32`,
    no bad historical windows, but live official `8` trades, `-$0.27`.
- Interpretation:
  the rescue search confirms the blocker is not merely the original q1000_yes
  threshold. When we broaden enough to get more historical trades, live
  official performance either stays too sparse or turns negative. Do not
  promote a BTC15M YES-only variant from this run.

2026-05-16 BTC15M distance-gated YES rescue search:

- Output:
  `backtest_outputs\btc15m_yes_rescue_search_distance_20260516_1730`.
- Search universe:
  - Predexon rows: `260,610`.
  - Rules tested: `1,080`.
  - Historical rules carried to live: `34`.
  - Live YES candidate rows: `44,348`.
  - Live unique tickers with REST official outcomes: `116`.
- Result:
  no deploy-ready strategy.
- Best live-official positives were still too sparse:
  - `yes_rescue_00341` / `yes_rescue_00342`: Predexon `58` trades,
    `+$7.55`, Sharpe `2.07`; live official only `2` trades, `+$0.93`.
  - `yes_rescue_00458`: Predexon `50` trades, `+$8.03`, Sharpe `2.46`;
    live official only `4` trades, `+$0.81`.
  - `yes_rescue_00048`: Predexon `51` trades, `+$6.84`, Sharpe `1.97`;
    live official only `4` trades, `+$0.81`.
  - `yes_rescue_00099`: Predexon `51` trades, `+$8.73`, Sharpe `2.62`;
    live official only `1` trade, `+$0.44`.
- Rules with more live-official trades did not hold up strongly:
  - `yes_rescue_00076` / `yes_rescue_00077`: live official `7` trades,
    `+$0.39`, win `57.14%`, Sharpe `0.28`.
  - `yes_rescue_00104`: Predexon `54` trades, `+$10.25`, Sharpe `3.01`,
    but live official `2` trades, `-$0.15`.
- Interpretation:
  distance-from-strike filtering improves some historical optics, but it does
  not create enough confirmed live-official evidence. The same production
  blocker remains: the live-replay official gate is too sparse and too fragile
  to justify real-money promotion.

2026-05-16 BTC15M refreshed live capture gate at 17:38 local:

- Refreshed standard F2 deployment gate:
  `backtest_outputs\btc15m_f2_deployment_gate_refresh_20260516_173512`.
- Capture covered `2026-05-12 10:42:45 UTC` through
  `2026-05-16 23:35:13 UTC`, with `8,742,972` quote rows after metadata,
  `8,821,875` top rows, `419` markets, and `113` captured official-result
  markets.
- Standard gate still failed every broad BTC15M candidate due
  `jan_negative_or_source_mismatch`, `too_few_live_proxy_trades`, and
  `too_few_live_official_trades`.
- Refilled the new replay trades with REST official Kalshi results:
  `backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_173816`.
- REST official result was negative for every broad both-side candidate even
  though proxy PnL was positive:
  - `q250`: `38` trades, official `-$0.64`, proxy `+$4.36`.
  - `q500`: `36` trades, official `-$0.62`, proxy `+$3.38`.
  - `q1000`: `34` trades, official `-$0.79`, proxy `+$3.21`.
  - `q250_qspeed05`: `33` trades, official `-$1.01`, proxy `+$2.99`.
- Re-ran side gate with the REST-official live file:
  `backtest_outputs\btc15m_f2_side_gate_rest_official_refresh_20260516_173852`.
- Side gate again failed all candidates. Closest remains:
  - `q1000_yes`: Predexon `33` trades, `+$7.98`, Sharpe `3.04`;
    live proxy `7` trades, `+$1.39`; live official `7` trades, `+$1.39`.
    It fails only on sample-size gates: too few Predexon, live proxy, and
    live official trades.
- Proxy-vs-official diagnostic:
  - The latest broad replay had `38` unique markets and `5` proxy/REST-official
    result mismatches.
  - `4/5` mismatches were NO-side proxy wins that became official YES losses.
  - The flipped markets had small Coinbase-close proxy distance from strike
    (mean absolute distance about `$12`, median about `$15.38`) versus about
    `$55` for non-flipped broad candidate rows.
- Interpretation:
  the current broad BTC15M F2 signal is not deployable because proxy settlement
  is overstating edge, especially on near-strike NO-side outcomes. The YES-only
  split is directionally interesting because it avoids most of that failure
  mode, but it remains a forward-test candidate, not a production strategy.

2026-05-16 BTC15M compact refinement and new q250/qty500 shadow:

- Output:
  `backtest_outputs\btc15m_compact_refine_20260516_1820`.
- Method:
  refined only the already-materialized candidate-trade family from
  `backtest_outputs\btc15m_f2_combined_predexon_plus_jan29_20260516_164544`
  and evaluated on the latest REST-official live replay
  `backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_173816`.
  This was intentionally much faster than rebuilding full side-candidate
  matrices from raw websocket rows.
- Best compact row:
  `ttl10_12_entry50_q250` with both sides allowed, side probability `>= 0.60`,
  net edge `>= 12c`, entry `<= 50c`, spread `<= 2c`, and visible top-book
  quantity `>= 500`.
- Historical candidate-trade evidence:
  - Total: `63` trades, `+$8.31`, win `61.90%`, max DD `-$1.97`,
    Sharpe `2.09`.
  - `pred_apr01_14`: `19` trades, `+$4.67`, win `73.68%`.
  - `pred_apr15_30`: `32` trades, `+$3.89`, win `62.50%`.
  - `pred_may01_12`: `6` trades, `+$0.16`, win `50.00%`.
  - Small January slices were mixed: `pred_jan18_23` `2` trades `-$0.95`,
    `pred_jan25_27p` `2` trades `+$1.28`,
    `pred_jan27_1230_jan28_0200` `2` trades `-$0.74`.
- Latest REST-official live replay:
  - `17` trades, `+$4.30`, win `76.47%`, max DD `-$1.58`, Sharpe `2.34`.
  - Daily split: May 12 `+$1.89`, May 13 `+$1.58`, May 14 `-$0.52`,
    May 15 `+$1.50`, May 16 `-$0.15`.
- Interpretation:
  this is the strongest new forward-test candidate because it has more
  REST-official live trades than `q1000_yes` and keeps a high top-book visible
  quantity gate. It is not production-ready yet because it was selected during
  a live-result refinement pass, has weak/mixed small-sample January and May
  subwindows, and still needs forward shadow trades after freeze.
- Temporarily added and started a paper-only wrapper:
  `scripts\btc15m_f2_q250_qty500_shadow.py`, with log
  `logs\btc15m_f2_q250_qty500_shadow_20260516_181629.out.log`.
  Startup confirmed paper mode, H02, TTL `10..12`, edge `12c`, min side
  probability `0.60`, visible qty `500`, both sides allowed, max `1` contract,
  and live Kalshi/Kraken websocket connections.
- Important correction:
  this wrapper did not exactly reproduce the compact rule. The compact rule was
  "take the first q250-style signal only if that first signal has visible qty
  `>= 500`"; setting live min visible qty to `500` instead can wait for a later
  different signal in the same event. That is a different, more optimistic
  behavior.
- Stopped the q250/qty500 paper process and removed the wrapper to avoid
  collecting misleading evidence.
- Newly backfilled Jan30 partial stress:
  `backtest_outputs\btc15m_f2_predexon_jan30_partial_20260516_181836`.
  - Broad `base_f2`: `31` trades, `-$2.11`.
  - `ttl10_12_entry50_q250`: `5` trades, `+$0.45`.
  - Exact compact q250 trade rows with first-signal visible qty `>= 500`:
    `0` trades.
  - Simple live-style min-qty-500 (`ttl10_12_entry50_q500`): `3` trades,
    `-$0.45`.
- Updated interpretation:
  the compact q250/qty500 idea remains an interesting diagnostic from the
  existing live replay, but it is not directly deployable through the current
  live scanner and has no positive new Jan30 evidence. Do not promote it.

2026-05-16 BTC15M settlement-source audit:

- Primary source:
  Kalshi's crypto-market help page says crypto contracts settle using CF
  Benchmarks Real-Time Indexes, with the expiration value equal to the average
  of the relevant RTI over the final 60 seconds before expiration.
- Local confirmation:
  Kalshi REST market metadata exposes `expiration_value` for finalized BTC15M
  markets. In
  `backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_173816\market_results.csv`,
  all `38/38` replay markets had non-null `expiration_value`.
- Implication:
  Coinbase/Kraken close is a proxy, not settlement truth. Any live strategy
  whose edge is concentrated near the strike can look good under proxy PnL and
  lose under REST-official settlement.
- Decision-distance guard scan on materialized candidate trades:
  - For q1000, YES-only remains the only useful filter:
    `33` Predexon trades, `+$7.98`; `7` live REST-official trades, `+$1.39`.
  - Broad q250/q500/q1000 remain REST-official negative on live replay when no
    decision-distance filter is used.
  - Adding a causal aligned-distance guard (`>= 5bps`, `>= 10bps`, etc.) often
    removes nearly all live trades. Example: q1000 YES with `>= 5bps` has
    `20` Predexon trades and `+$6.05`, but `0` live REST-official trades.
- Interpretation:
  the right next model improvement is not "use Kraken/Coinbase harder"; it is
  either obtain a live CF RTI/BRTI feed or explicitly model/guard the
  RTI-versus-exchange basis. With current data, simple decision-distance guards
  are too sparse to deploy.

2026-05-16 BTC15M expiration-value basis quant:

- Data:
  `backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_173816`
  market REST results and replay trades.
- All `38/38` finalized markets had Kalshi `expiration_value`.
- Basis definition:
  `expiration_value - proxy_close_btc_spot`, where proxy close was the replay's
  exchange close sample.
- Basis stats on the 38 markets:
  - mean `+$3.36`
  - median `+$2.40`
  - std `$12.02`
  - min `-$18.89`
  - max `+$31.16`
  - 90th percentile `+$19.46`
  - 95th percentile `+$23.02`
- Proxy/official result mismatches:
  `5/38` markets.
  - Mismatches had mean basis `+$16.24`, median `+$22.34`.
  - Matches had mean basis `+$1.41`, median `+$0.69`.
- If one could know close-distance at close, rejecting proxy-close distance
  within `$25` of strike would remove all 5 observed mismatches, but this is
  not a causal decision-time filter. It is only valid as a label-quality rule
  for historical proxy-labeled research.
- The practical live lesson:
  because the RTI/exchange basis can be `20-30` dollars near expiry, strategies
  betting within that distance of the strike are exposed to settlement-index
  noise unless we have a live CF RTI feed or a very conservative causal
  distance/edge margin.

2026-05-16 CF Benchmarks access check:

- CF Benchmarks API docs expose the correct WebSocket endpoint:
  `wss://www.cfbenchmarks.com/ws/v4`.
- The docs state that the WebSocket API requires an API key obtained by
  contacting CF Benchmarks for a license. Auth can be sent as the websocket
  protocol array `['cfb', username, password]` or as HTTP Basic auth.
- Local `credentials.env` currently has Kalshi and Predexon credentials only;
  there is no CF Benchmarks/BRTI credential.
- Deployment implication:
  without CF RTI/BRTI access, BTC15M fair-value strategies are trading against
  an imperfect exchange-spot proxy. That can be acceptable only with a wide
  causal edge/distance guard and enough REST-official live proof. Current
  q1000/q1000_yes evidence is still too sparse for that.

2026-05-16 18:34-19:07 MST refreshed BTC15M official gate and readiness check:

- Refreshed causal replay through the latest BTC15M live capture:
  `backtest_outputs\btc15m_f2_deployment_gate_refresh_20260516_183441`.
  Broad variants remained proxy-positive but not deployable:
  - `q250`: live proxy `39` trades, `+$3.84`; gate official subset `7`,
    `+$2.32`; still fails January/source mismatch and sample-size gates.
  - `q500`: live proxy `37`, `+$2.86`; gate official subset `7`, `+$2.32`.
  - `q1000`: live proxy `35`, `+$2.69`; gate official subset `7`, `+$2.34`.
- REST-filled official settlement for that replay:
  `backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_183818`.
  This is the stronger audit than captured lifecycle-only official rows.
  Official +2c PnL was negative for all broad variants:
  - `q250`: `39` trades, `-$1.16`, win `48.72%`.
  - `q500`: `37` trades, `-$1.14`, win `48.65%`.
  - `q1000`: `35` trades, `-$1.31`, win `48.57%`.
  - `q250_qspeed05`: `34` trades, `-$1.50`, win `47.06%`.
- Re-ran side gate with REST-official live rows:
  `backtest_outputs\btc15m_f2_side_gate_rest_official_refresh_20260516_183849`.
  Best BTC15M side candidate remains `q1000_yes`, but it is not deployable:
  Predexon `33` trades, `+$7.98`, Sharpe `3.04`; live official `8` trades,
  `+$0.87`, win `62.5%`. Failure reasons are too few Predexon, live proxy,
  and live official trades.
- Added conservative readiness checker:
  `scripts\check_btc_deployment_readiness.py`.
  Latest output:
  `backtest_outputs\deployment_readiness_20260516_184321`.
  It correctly prefers REST-official settlement over lifecycle-only official
  rows and reports `production_ready_count = 0`. No BTC15M or BTC1H strategy is
  production-ready under the current gate.
- Added fast materialized first-signal filter grid:
  `scripts\audit_btc15m_materialized_filter_grid.py`.
  Latest output:
  `backtest_outputs\btc15m_materialized_filter_grid_20260516_190636`.
  This avoids the optimistic error of waiting for a later better signal in the
  same event. A rule means: accept the frozen strategy's first event-level
  signal only if that exact row also passes the extra causal filter.
- Strongest materialized research candidate:
  `q250` first signal with visible quantity `>= 500`, spread `<= 2c`, TTL
  `10-12m`, entry `<= 50c`, p `>= 0.60`, edge `>= 12c`.
  - Historical materialized evidence: `63` trades, `+$8.31`, win `61.90%`,
    max DD `-$1.97`, Sharpe `2.09`.
  - Latest live REST-official replay: `17` trades, `+$4.30`, win `76.47%`,
    max DD `-$1.58`, Sharpe `2.34`.
  - This is only a research pass, not deployment-ready, because it has only
    `17` official live trades and was selected after seeing this live replay.
- Subagent diagnostics:
  - Broad q250 official/proxy degradation is fully explained by 5 settlement
    flips: non-flipped rows are `+$1.51` proxy and official, while flipped
    rows are `+$2.33` proxy but `-$2.67` official.
  - NO side is the main source of flips. q250 YES has `9` official trades,
    `+$0.33`; q1000 YES has `8`, `+$0.87`.
  - Decision-time spread, entry, edge, TTL, RV, quote speed, and distance did
    not cleanly separate flips from non-flips in this small sample.
  - Visible quantity `>= 500` is directionally helpful for q250 but must be
    tested exactly as a first-signal skip rule; the live q500 reselection
    behavior is a different rule and loses under REST-official settlement.
  - BTC1H is not closer to deployability: current multi-shadow still has
    `0` trades and the prior BTC1H promotion audit has `0` promoted candidates.
- Predexon data-quality update:
  late12 BTC15M backfill was around `2303/7835` windows (`~29.4%`) and
  `data\predexon_kalshi_orderbooks` was about `1.824 GB`.
  Jan30 is patchy: `89/96` ok windows, `7` empty, and many edge-missing/thin
  intervals; the partial Jan30 stress weakens deployment confidence but is not
  by itself a clean failure proof.
- Current research direction:
  do not deploy broad q250/q500/q1000; do not deploy q1000_yes yet; do not
  deploy the q250 qty>=500 research candidate yet. The next honest promotion
  path is either more forward REST-official live evidence or a causal
  settlement-index improvement, ideally a CF Benchmarks RTI/BRTI feed.

2026-05-16 exact q250 qty>=500 first-signal shadow:

- Added a default-off executor gate:
  `BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY`.
  When set above `BTC15M_H02_MIN_VISIBLE_QTY`, the live scanner builds the base
  H02 signal first, then rejects and locks the event if that first signal has
  too little visible top-book quantity. This tests the materialized rule
  exactly and avoids the optimistic behavior of waiting for a later higher-qty
  tick in the same event.
- Added paper-only wrapper:
  `scripts\btc15m_f2_q250_qty500_firstskip_shadow.py`.
  Configuration:
  H02, TTL `10..12m`, spread `<=2c`, edge `>=12c`, entry `0.02..0.50`,
  side probability `>=0.60`, base visible qty `>=250`, first-signal visible
  qty `>=500`, both sides allowed, max `1` contract, paper mode only.
- Tests:
  `python -m unittest scripts.test_btc15m_shadow_config -v` now has 7 tests
  passing, including distinct base-min-qty vs first-signal-skip behavior and
  wrapper paper-only checks.
- Started paper shadow:
  `logs\btc15m_f2_q250_qty500_firstskip_shadow_20260516_191138.out.log`.
  Startup confirmed paper mode, Kraken/Kalshi websockets, first-signal
  min-qty `500`, and no real order submission.

2026-05-16 Boston-flight shutdown handoff:

- User needed to shut down quickly. Logged current state before stopping
  processes.
- Running Kalshi Python PIDs before shutdown:
  `3768` BTC15M live capture, `11640` BTC1H multi-strategy shadow, `11652`
  BTC15M q1000 YES shadow, `15596` BTC15M q250 qty500 first-skip shadow,
  `23204` Predexon BTC15M backfill, `19348` Predexon helper/low-memory process.
- Latest q250 qty500 first-skip shadow status:
  `logs\btc15m_f2_q250_qty500_firstskip_shadow_20260516_191138.out.log`.
  It was healthy, connected to Kalshi and Kraken websockets, and had `0`
  trades / `0` settled / `$0.00` PnL. It had only seen an event outside the
  `10..12m` TTL window at the tail.
- Latest Predexon BTC15M late12 backfill status:
  `logs\predexon_btc15m_late12_levels_{20260515_211347}.out.log`.
  It had reached about `[2330/7835]`, around
  `KXBTC15M-26JAN310800-00` (`2026-01-31 12:48 -> 13:00 UTC`).
- Added but not fully run before shutdown:
  `scripts\fill_btc15m_predexon_official_results.py`, which REST-fills
  materialized Predexon trade rows with official Kalshi results. Also patched
  `scripts\audit_btc15m_materialized_filter_grid.py` to accept
  `--pred-pnl-col` and `--pred-win-col`. Next resume step should be:
  1. Run `python -m py_compile scripts\fill_btc15m_predexon_official_results.py scripts\audit_btc15m_materialized_filter_grid.py`.
  2. Run official-fill on
     `backtest_outputs\btc15m_f2_combined_predexon_plus_jan29_20260516_164544\combined_predexon_trades.parquet`.
  3. Re-run the materialized grid with
     `--pred-pnl-col pnl_official_rest_2c --pred-win-col win_pnl_official_rest_2c`.
  4. Compare whether the q250 first-signal qty>=500 candidate still works when
     both historical Predexon and live websocket rows use official settlement.
- No production-ready strategy existed at shutdown. Latest readiness output:
  `backtest_outputs\deployment_readiness_20260516_184321`,
  `production_ready_count = 0`.

2026-05-17 MDT / 2026-05-18 UTC Codex resume: official Predexon fill and grid
refresh:

- Re-checked running Python processes with the Kalshi/BTC/Predexon filters:
  no matching process was running.
- `python -m py_compile scripts\fill_btc15m_predexon_official_results.py
  scripts\audit_btc15m_materialized_filter_grid.py` passed.
- First REST-fill attempt hit a Kalshi public REST `404` for an old Predexon
  ticker (`KXBTC15M-26JAN072300-00`). Patched
  `scripts\fill_btc15m_predexon_official_results.py` to keep missing markets
  unfilled and record `fetch_error=404_not_found` instead of aborting.
- Official fill output:
  `backtest_outputs\btc15m_predexon_rest_official_20260517_codex`.
  Coverage was only `374/880` unique market tickers; `506` returned
  `404_not_found`. This means January stress windows are still not
  REST-official-filled in this artifact.
- Strategy-level official fill results:
  - `ttl10_12_entry50_q250`: `69/142` official-filled rows, `+$9.66`,
    win `63.77%`, max DD `-$1.49`, Sharpe `2.34`.
  - `ttl10_12_entry50_q500`: `67/109`, `+$9.82`, win `64.18%`.
  - `ttl10_12_entry50_q1000`: `52/66`, `+$8.16`, win `65.38%`.
  - Broad `base_f2`: `374/880`, `+$7.90`, but Sharpe only `0.82` and the
    missing January coverage makes this incomplete.
- Window-level warning for `ttl10_12_entry50_q250`:
  April and May rows filled and were positive (`pred_apr01_14 +$4.18`,
  `pred_apr15_30 +$4.42`, `pred_may01_12 +$1.06`), but every January window
  had `0` official-filled rows. The old proxy January negatives therefore
  still matter as stress evidence.
- Official-Predexon materialized grid output:
  `backtest_outputs\btc15m_materialized_filter_grid_pred_official_20260517_codex`.
  Top row remained the q250 first-signal skip rule:
  first `q250` signal only, visible qty `>=500`, spread `<=2c`, TTL `10-12m`,
  entry `<=50c`, side fair probability `>=0.60`, edge `>=12c`.
  It had `57` Predexon official-filled trades, `+$8.72`, win `64.91%`,
  max DD `-$1.97`, Sharpe `2.33`, plus the same live REST-official `17`
  trades, `+$4.30`, win `76.47%`, max DD `-$1.58`.
- Interpretation:
  q250 qty>=500 first-signal survives the official-filled April/May Predexon
  subset and the existing live REST-official replay, but it is still research
  only. It was selected after seeing live replay, has only `17` official live
  trades, lacks REST-official January stress coverage, and fails deployment
  sample-size discipline.
- Refreshed readiness:
  `backtest_outputs\deployment_readiness_20260517_codex`; verdict remains
  `production_ready_count = 0`. Do not deploy broad q250/q500/q1000,
  q1000_yes, q250 qty>=500 first-signal, or any BTC1H candidate from this
  evidence.

2026-05-17 MDT / 2026-05-18 UTC GPT Pro loop and stricter current-state
artifacts:

- Built and submitted a GPT Pro strategy-advisor packet:
  `gpt_pro_packets\strategy_advisor_20260517_203137`.
  Saved the full Pro response at
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260517_203833.md`.
- Pro verdict, after reviewing the packet: nothing is deployable now. It
  ranked the most plausible paths as:
  1. BTC15M q250 first-signal qty>=500, spread <=2c, TTL 10-12m, entry <=50c,
     side fair p >=0.60, edge >=12c.
  2. BTC15M q1000 YES-only.
  3. BTC1H high_conf_80_entry70_no_chase as a slow-burn paper shadow.
  It explicitly treated these as forward-validation paths, not live-trading
  approvals.
- Added the Chrome/GPT Pro resilience notes to the local skill/workflow:
  Chrome extension access may appear via the Browser runtime as an extension
  browser rather than a dedicated `chrome` namespace; long pasted packets may
  become a ChatGPT pasted-text attachment and should be submitted with the
  `data-testid="send-button"` path. `scripts\save_gpt_pro_review.ps1` saves
  the Windows clipboard, so browser-extracted responses must be written
  directly if the clipboard still contains the original prompt.
- Metadata-settlement fill output:
  `backtest_outputs\btc15m_predexon_metadata_settlement_20260517_codex`.
  REST results still cover only `374/880` unique historical tickers, but local
  Predexon market metadata has official-looking results for `880/880`.
  Treat this as historical provider metadata settlement evidence, not a fresh
  Kalshi REST final gate.
  - Broad `base_f2` on metadata labels is weak: `880` trades, `-$1.40`, win
    `52.16%`, max DD `-$18.05`.
  - Broad `ttl10_12_entry50_q250` on metadata labels is only `+$5.17` across
    `142` trades, win `52.11%`, max DD `-$7.44`; the broad q strategy remains
    unattractive.
- Metadata-settlement materialized grid output:
  `backtest_outputs\btc15m_materialized_filter_grid_metadata_settlement_20260517_codex`.
  Top row remains `mat_grid_00019`, the q250 first-signal qty>=500 skip rule:
  `63` historical materialized trades, `+$8.31`, win `61.90%`, max DD
  `-$1.97`, Sharpe `2.09`; live REST-official replay still `17` trades,
  `+$4.30`, win `76.47%`, max DD `-$1.58`, Sharpe `2.34`.
  This is a research pass only. It has too few live official trades and was
  selected after seeing the live replay.
- Settlement-flip attribution output:
  `backtest_outputs\btc15m_settlement_flip_attribution_20260517_codex`.
  Broad q strategies still degrade mainly through NO-side settlement flips:
  broad q250 NO had `30` rows, `4` flips, proxy `+$2.51`, official `-$1.49`;
  broad q1000 NO had `27` rows, `4` flips, proxy `+$1.82`, official `-$2.18`.
  The materialized q250 top rule still had `2/10` NO flips, but remained
  positive because the filtered live sample also had stronger non-flip rows.
- Patched `scripts\check_btc_deployment_readiness.py` so the current
  materialized first-signal grid is included in readiness. New output:
  `backtest_outputs\deployment_readiness_20260517_materialized_codex`.
  Verdict remains `FAIL`, `production_ready_count = 0`. The q250 materialized
  row is now visible and explicitly blocked by
  `research_pass_not_deployment_ready`, `too_few_live_official_trades`, and
  `too_few_live_proxy_trades`.
- Added `scripts\check_btc_forward_shadow_status.py` for repeatable read-only
  liveness/ledger checks. Latest status output:
  `backtest_outputs\btc_forward_shadow_status_20260517_codex`.
  As of `2026-05-18T03:01:27Z`, all four intended forward processes were
  running:
  BTC15M live capture PID `1724`, BTC15M q250 qty500 first-skip paper PID
  `7052`, BTC15M q1000 YES paper PID `24840`, BTC1H high_conf_80_entry70
  paper PID `16216`.
  BTC15M q250 and q1000 paper ledgers still had `0` fills. BTC1H had `1`
  post-restart paper fill since `2026-05-18T02:42:00Z`:
  `KXBTCD-26MAY1723-T76899.99`, NO, `1` contract, entry `0.66`, fee estimate
  `0.02`, close `2026-05-18T03:00:00Z`. The `21:00` MDT hourly shadow report
  showed the trade settled as a win: total BTC1H shadow moved from `-$0.10`
  realized over 3 settled trades to `+$0.22` realized over 4 settled trades.
- Current conclusion: continue forward evidence collection and official
  settlement refreshes. Do not deploy BTC15M q250 first-signal, q1000 YES,
  broad q strategies, or BTC1H high-conf variants from this evidence.

2026-05-17 MDT / 2026-05-18 UTC fragility and shadow-ledger official audit:

- Added `scripts\analyze_btc15m_materialized_fragility.py`.
  Output:
  `backtest_outputs\btc15m_materialized_fragility_20260517_codex`.
  This is not a new search. It audits the already-known q250/q1000
  first-signal rules by side, causal decision-distance guard, historical
  metadata labels, historical REST subset, and live REST-official replay.
- q250 first-signal qty>=500 side split:
  - BOTH: Predexon metadata `63` trades, `+$8.31`, win `61.90%`; live
    REST-official `17`, `+$4.30`, win `76.47%`.
  - YES-only: Predexon metadata `28`, `+$4.44`, win `64.29%`; live
    REST-official `7`, `+$1.37`, win `71.43%`. No live proxy/official flips
    in this tiny sample.
  - NO-only: Predexon metadata `35`, `+$3.87`, win `60.00%`; live
    REST-official `10`, `+$2.93`, win `80.00%`, but live proxy PnL was
    `+$4.93` with `100%` proxy wins. The NO side is where the settlement-basis
    false confidence lives.
- q250 live flip detail:
  the top q250 BOTH rule had 2 live NO-side proxy/official flips:
  `KXBTC15M-26MAY152330-30` and `KXBTC15M-26MAY160130-30`, with official
  minus proxy basis `+$22.34` and `+$31.16`. Both were causal decision-time
  aligned distance only about `2.0-2.8 bps`, so a `>=5 bps` distance guard
  removes them but also leaves only `1` live trade. This is not enough to
  rescue deployment.
- Window fragility:
  q250 BOTH is strong in April (`pred_apr01_14 +$4.67`, `pred_apr15_30
  +$3.89`) but only `+$0.16` over 6 trades in `pred_may01_12` and has small
  negative two-trade January pockets. The bad-window gate did not fire because
  those windows were below the minimum `3`-trade window threshold, but the
  shape is still a warning.
- Added `scripts\check_btc_shadow_official_settlement.py`.
  Output:
  `backtest_outputs\btc_shadow_official_settlement_20260517_codex`.
  This read-only audit fetches Kalshi REST market results for paper-shadow
  ledgers and recomputes hold-to-settlement PnL using recorded entry/fee.
  It was then upgraded to also compute proxy settlement from
  `data\btc_1m_research_live_cache.parquet`, so proxy/official result and PnL
  disagreement are machine-readable.
- BTC1H shadow official audit:
  BTC15M q250 and q1000 YES paper ledgers still had `0` fills. BTC1H
  high_conf80 entry70 no-chase had `4` paper fills, all official REST-finalized
  as NO wins, `+$1.22` official PnL on `$2.78` premium. The one post-restart
  trade since `2026-05-18T02:42:00Z` was also an official NO win, `+$0.32`.
  Latest refreshed output:
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`.
  The all-history BTC1H shadow proxy-vs-official comparison is now explicit:
  proxy PnL `+$0.22`, official PnL `+$1.22`, delta `+$1.00`, with `1`
  proxy/official result mismatch. The mismatched trade was
  `KXBTCD-26MAY1613-T78199.99`: Coinbase proxy close `78202.65` would mark it
  YES, but Kalshi official expiration value `78148.24` finalized NO.
- Found a BTC1H paper-accounting bug/risk:
  the running BTC1H shadow's periodic `SHADOW report` used local Coinbase
  candle close as the settlement proxy, not Kalshi official result. It reported
  the first 3 settled trades as `2` wins / `1` loss and `-$0.10`, while REST
  official settlement says all 3 were wins and `+$0.90`.
- Patched `scripts\btc_1hr_research_live.py` for future runs:
  paper summaries now use Kalshi public REST official result by default
  (`BTC_1HR_PAPER_SETTLEMENT_SOURCE=official`) and keep trades open until the
  market is finalized. `BTC_1HR_PAPER_SETTLEMENT_SOURCE=proxy` is available
  only as an explicit fallback. Startup logs now include `paper_settlement=...`.
  The currently running BTC1H paper process was not restarted, so it still has
  the old in-memory proxy-summary behavior until a future restart.
- Verification:
  `python -m py_compile scripts\btc_1hr_research_live.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\analyze_btc15m_materialized_fragility.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config -v` passed `7/7`
  with the pre-existing logging ResourceWarnings.
- Deployment interpretation:
  this strengthens the no-deploy verdict. BTC15M still needs many more
  post-freeze live official fills; BTC1H has a promising tiny official shadow
  ledger but also just exposed a paper-summary/official-settlement mismatch,
  which must be fixed in a restarted shadow and observed forward before any
  promotion discussion. Refreshed readiness output
  `backtest_outputs\deployment_readiness_latest_codex` remains `FAIL` with
  `production_ready_count = 0`. The readiness summary now includes
  `shadow_official_ledger` rows. BTC1H all-history shadow is blocked by
  `proxy_official_settlement_mismatch`, `proxy_official_pnl_disagreement`,
  `shadow_ledger_only_not_full_promotion_gate`, and
  `too_few_shadow_official_trades`. The since-restart BTC1H row is also not
  deployable: `1` official trade, `+$0.32`, blocked by
  `too_few_shadow_official_trades` and `shadow_ledger_only_not_full_promotion_gate`.

## 2026-05-17/18 GPT Pro review loop and forward-basis reporting

- Refreshed live process state:
  `btc15m_live_capture.py`, `btc15m_f2_q250_qty500_firstskip_shadow.py`,
  `btc15m_f2_q1000_yes_shadow.py`, and
  `btc_1hr_high_conf80_entry70_no_chase_shadow.py` were all running. The two
  BTC15M paper shadow ledgers still had `0` fills. BTC1H high-conf entry70
  no-chase had `4` paper fills, with `1` since `2026-05-18T02:42:00Z`.
- Built and submitted a fresh GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260517_211908`. Saved Pro's response at
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_032726_utc.md`.
- GPT Pro agreed with the local gate:
  no BTC15M or BTC1H strategy is deployable. It ranked the research paths as
  `q250` first-signal qty>=500, `q1000_yes`, then BTC1H
  `high_conf80_entry70_no_chase`, all still blocked by sample size,
  official-settlement/ledger hygiene, and/or selection leakage.
- Pro recommended restarting the BTC1H shadow with official settlement enabled,
  but this was not done because the standing rule is not to kill/restart running
  live/capture/shadow processes unless explicitly asked. The running BTC1H
  process therefore still has old in-memory proxy-summary behavior, even though
  the code has been patched for future starts.
- Patched `scripts\check_btc_shadow_official_settlement.py` to make the daily
  forward ledger report more useful without touching live processes. It now
  includes model edge/spread/entry spot plus settlement-basis fields:
  `official_minus_proxy_spot`, `official_minus_proxy_bps`,
  `entry_spot_minus_strike`, `proxy_close_minus_strike`,
  `official_expiration_minus_strike`, and distance-in-bps columns. It leaves
  `quote_age_ms` and `top_visible_qty` blank when the existing ledger schema did
  not record them.
- Latest refreshed official-shadow output:
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`.
  BTC15M q250/q1000 shadow fills remain `0`. BTC1H all-history official PnL
  remains `+$1.22` on `4` official settled rows, proxy PnL `+$0.22`, with `1`
  proxy/official result mismatch. The new basis report shows the since row
  `KXBTCD-26MAY1723-T76899.99` had official-minus-proxy spot `-$111.67`; it
  still settled as a NO win, but the magnitude reinforces that proxy close is
  not a safe settlement substitute.
- Refreshed readiness:
  `backtest_outputs\deployment_readiness_latest_codex` remains `FAIL` with
  `production_ready_count = 0`. No live deployment.
- Added reusable local skill:
  `C:\Users\ahmed\.codex\skills\kalshi-btc-strategy-research`, validated with
  `quick_validate.py`. It captures the repeatable process/status/readiness/GPT
  Pro/no-deploy workflow for future Kalshi BTC research sessions.

## 2026-05-17/18 Forward evidence report consolidation

- Refreshed process/status again:
  all four expected Python processes were still running. The latest status
  output is `backtest_outputs\btc_forward_shadow_status_latest_codex`.
  BTC15M q250 first-signal and q1000 YES shadow ledgers still had `0` paper
  fills. BTC1H high-conf entry70 no-chase still had `4` paper fills, `1` since
  `2026-05-18T02:42:00Z`.
- Refreshed official settlement:
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`. BTC1H remains
  `+$1.22` official PnL over `4` official-settled rows, with proxy PnL `+$0.22`
  and `1` proxy/official result mismatch. The basis watch remains large:
  latest since-row basis `official_minus_proxy_spot = -$111.67`.
- Refreshed readiness:
  `backtest_outputs\deployment_readiness_latest_codex` remains `FAIL` with
  `production_ready_count = 0`.
- Added `scripts\build_btc_forward_evidence_report.py`, a read-only daily
  report builder that combines:
  process/capture status, REST-official shadow settlement, focused readiness
  blockers, and a basis watchlist. Latest output:
  `backtest_outputs\btc_forward_evidence_report_latest_codex`.
- The latest consolidated report verdict is still:
  `NO DEPLOY: readiness production_ready_count is 0.` It highlights the current
  hard blockers:
  BTC15M shadow fills are still absent; BTC1H evidence is still tiny and has an
  old proxy-summary mismatch; near-strike settlement-basis rows are too large to
  ignore.
- Updated the `Kalshi BTC forward evidence refresh` heartbeat to run the new
  consolidated report builder after the status, official-settlement, and
  readiness refreshes. It still must not deploy or restart processes unless
  explicitly asked.

## 2026-05-17/18 Official materialized readiness and ledger-realism patch

- Confirmed the Predexon REST-official fill path had already run:
  `backtest_outputs\btc15m_predexon_rest_official_20260517_codex`.
  It filled `374` unique market REST results across `2,424` combined
  Predexon trade rows. The official-filled materialized grid is:
  `backtest_outputs\btc15m_materialized_filter_grid_pred_official_20260517_codex`.
- Patched `scripts\check_btc_deployment_readiness.py` so the readiness gate
  prefers `btc15m_materialized_filter_grid_pred_official_*` or
  `btc15m_materialized_filter_grid_rest_official_*` before falling back to
  older metadata-scored materialized grids. This prevents the latest readiness
  report from silently using the stale metadata-settlement materialized table.
- Refreshed readiness after that patch:
  `backtest_outputs\deployment_readiness_latest_codex`.
  It now points to
  `backtest_outputs\btc15m_materialized_filter_grid_pred_official_20260517_codex`
  as `materialized_grid_dir` and still reports
  `production_ready_count = 0`.
- With REST-official historical settlement, the top q250 materialized row is
  still research-interesting but not deployable:
  `q250:mat_grid_00019` has `57` historical official-filled Predexon trades,
  `+$8.72` Predexon official PnL, `17` live official trades, and `+$4.30`
  live official PnL. It remains blocked by
  `research_pass_not_deployment_ready`, `too_few_live_official_trades`, and
  `too_few_live_proxy_trades`.
- Refreshed forward status:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`.
  All four expected processes were running:
  BTC15M capture PID `1724`, BTC15M q250 qty500 first-skip shadow PID `7052`,
  BTC15M q1000 YES shadow PID `24840`, and BTC1H high-conf entry70 no-chase
  shadow PID `16216`. BTC15M q250 and q1000 shadow ledgers still had `0`
  paper fills. BTC1H had `5` paper fills total and `2` since
  `2026-05-18T02:42:00Z`; the newest paper row was
  `KXBTCD-26MAY1800-T76999.99`, NO, entry `0.68`, created
  `2026-05-18T03:44:22.567875+00:00`.
- Refreshed official shadow settlement:
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`.
  BTC1H had `5` paper fills but only `4` official-finalized rows so far:
  all-history official PnL `+$1.22`, proxy PnL `+$0.22`, with `1`
  proxy/official result mismatch. Since `2026-05-18T02:42:00Z`, BTC1H had
  `2` paper fills, but only `1` official-finalized row, `+$0.32`; the
  `KXBTCD-26MAY1800-T76999.99` row was still active/unfinalized at refresh.
- Patched future ledger realism fields:
  `scripts\btc_1hr_research_live.py` now persists `available_qty`,
  `top_visible_qty`, `quote_received_at_ns`, `signal_received_at_ns`,
  `quote_age_ms`, `edge_threshold_cents`, `strike`, `ttl_min`, and the
  decision-time YES/NO book prices into `research_live_trades`. The BTC15M
  H02/F2 signal/reprice path in `scripts\btc15m_lowdd_live.py` now populates
  those fields before calling the shared recorder. Existing running shadows
  were not restarted, so the new columns only apply after a future restart or
  new process start.
- Patched `scripts\check_btc_shadow_official_settlement.py` and
  `scripts\build_btc_forward_evidence_report.py` to surface those fields when
  present and leave them blank for old ledger rows.
- Validation:
  `python -m py_compile scripts\btc_1hr_research_live.py
  scripts\btc15m_lowdd_live.py scripts\check_btc_shadow_official_settlement.py
  scripts\build_btc_forward_evidence_report.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_deployment_readiness.py
  scripts\fill_btc15m_predexon_official_results.py
  scripts\audit_btc15m_materialized_filter_grid.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53` tests, with only the
  existing logging ResourceWarnings and pandas all-NaN warning.
- Refreshed consolidated report:
  `backtest_outputs\btc_forward_evidence_report_latest_codex`.
  Verdict remains:
  `NO DEPLOY: readiness production_ready_count is 0.`
- Snapshot-inspected the live BTC15M q250/q1000 shadow capture DuckDBs instead
  of opening the live writers directly:
  `.codex_work\shadow_capture_snapshots_20260517_215450`.
  The zero paper fills appear to be genuine no-signal behavior, not dead
  processes. q250 snapshot had `270` capture-health rows, `251,915`
  `ws_orderbook_top` rows, `2,766` Kraken ticker rows, and `140,903`
  `signal_scan` rows; q1000 snapshot had `684` capture-health rows, `466,503`
  `ws_orderbook_top` rows, `5,295` Kraken ticker rows, and `275,065`
  `signal_scan` rows. Both had `order_decision = 0` and `candidate_count = 0`
  for every scan. Latest q250/q1000 scans were rejecting the current event
  because TTL was around `5.17m`, outside the frozen `10-12m` window, with
  earlier rejects mostly `h02_no_edge`, stale BTC spot, and spread filters.

## 2026-05-17/18 Current live-replay expansion and q250 official degradation

- Refreshed forward status at `2026-05-18T03:58:39Z`:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`.
  All four expected processes were still running. The BTC15M q250/q1000 shadow
  capture DBs were readable and actively updating:
  q250 had `145,216` `signal_scan` rows, q1000 YES had `279,363`, and both
  had `0` nonzero-candidate scans and `0` order-decision rows. Latest rejects
  were `h02_ttl_outside_1.40` / `h02_ttl_outside_1.39`, so the zero-fill state
  still looks like no qualifying signals rather than a dead shadow.
- Patched `scripts\check_btc_forward_shadow_status.py` and
  `scripts\build_btc_forward_evidence_report.py` so the normal forward report
  now includes signal-scan diagnostics: signal rows, nonzero-candidate rows,
  latest signal detail, and order-decision rows. Latest report:
  `backtest_outputs\btc_forward_evidence_report_latest_codex`.
- Patched `scripts\backtest_btc15m_f2_live_ws_holdout.py` with two replay
  controls needed for exact frozen candidates:
  `--first-signal-visible-qty-min` implements the q250 first-signal skip
  semantics, and `--side yes|no|both` implements side-specific candidates such
  as q1000 YES. This avoids the earlier optimistic mistake of filtering to
  higher visible quantity before choosing the first signal.
- Patched `scripts\fill_btc15m_live_ws_official_results.py` so it can REST-fill
  single-rule replay files without a pre-existing `candidate` column and so
  summary rows include proxy/official mismatch counts plus official-minus-proxy
  PnL delta.
- Ran exact q250 first-skip replay on the full current live websocket capture:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_latest_codex`.
  Command shape:
  F2/H02, side both, TTL `10-12m`, spread `<=2c`, entry `0.02..0.50`,
  base visible qty `>=250`, then first-signal visible qty `>=500`.
  Capture window was `2026-05-12T10:42:45Z` through
  `2026-05-18T04:00:53Z`, with `9,189,119` orderbook top rows.
  Replay found `39` base first signals, rejected `15` because first-signal
  visible qty was below `500`, and kept `24` closed proxy-settled trades.
- REST-filled those `24` q250 first-skip trades:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_rest_official_latest_codex`.
  All `24` markets had REST official results. Official +2c-stress PnL:
  `+$3.74`, win `66.67%`, max DD `-$1.64`, Sharpe `1.58`. Proxy +2c PnL:
  `+$6.74`, win `79.17%`. There were `3` proxy/official result mismatches
  and official-minus-proxy PnL was `-$3.00`.
- The new May 16 q250 first-skip slice is the key warning:
  `11` official-filled trades, official PnL `-$0.67`, win `45.45%`, while
  proxy PnL was `+$2.33`. This is the same settlement-basis false-positive
  pattern that harmed broad q strategies. Overall q250 first-skip remains
  positive on the full current live capture, but it is materially less robust
  than the earlier `17`-trade official snapshot and is still not deployable.
- Ran q1000 YES on the same current capture:
  `backtest_outputs\btc15m_f2_live_ws_q1000_yes_latest_codex`, then REST-filled
  at `backtest_outputs\btc15m_f2_live_ws_q1000_yes_rest_official_latest_codex`.
  Exact rule: YES-only, TTL `10-12m`, spread `<=2c`, entry `0.02..0.50`,
  visible qty `>=1000`, side fair probability `>=0.60`, edge `>=12c`.
  It had only `8` REST-official trades, `+$0.90`, win `62.5%`, max DD `-$1.00`;
  proxy and official agreed on all `8`. This remains far too small to promote.
- Patched `scripts\check_btc_deployment_readiness.py` so focused latest live
  REST-official replays are added to readiness as
  `latest_live_replay_rest_official` rows. Latest readiness now includes:
  - `q250_firstskip_qty500`: `24` official trades, `+$3.74`, blocked by
    `latest_live_replay_only_not_full_promotion_gate`,
    `too_few_live_official_trades`, `proxy_official_settlement_mismatch`, and
    `proxy_official_pnl_disagreement`.
  - `q1000_yes`: `8` official trades, `+$0.90`, blocked by
    `latest_live_replay_only_not_full_promotion_gate` and
    `too_few_live_official_trades`.
  Readiness remains `production_ready_count = 0`.
- Validation:
  `python -m py_compile scripts\check_btc_forward_shadow_status.py
  scripts\build_btc_forward_evidence_report.py
  scripts\check_btc_deployment_readiness.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\fill_btc15m_live_ws_official_results.py
  scripts\check_btc_shadow_official_settlement.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53` tests, with only the
  pre-existing logging ResourceWarnings and pandas all-NaN warning.
- Current interpretation:
  no deploy. q250 first-skip is still the most interesting BTC15M path, but
  the expanded REST-official live replay makes it less convincing, not more.
  q1000 YES is cleaner on proxy/official agreement but has only `8` official
  trades. Continue forward shadow collection and official settlement refreshes;
  do not tune thresholds on these expanded live rows.

## 2026-05-17/18 Latest official diagnostics and forward report refresh

- Added diagnostics-only audit:
  `scripts\analyze_btc15m_latest_live_replay_diagnostics.py`, output
  `backtest_outputs\btc15m_latest_live_replay_diagnostics_latest_codex`.
  This does not search for new thresholds; it only checks pre-existing
  side/distance/near-strike cuts on the latest q250 first-skip and q1000 YES
  REST-official live replay rows.
- Diagnostic result: no deployable rescue cut. For `q250_firstskip_qty500`,
  all `24` official rows remain `+$3.74` with `3` proxy/official result
  mismatches and `-$3.00` official-minus-proxy PnL delta. All mismatches were
  NO-side trades. YES-only removes the mismatches but leaves just `8` trades,
  `+$0.88`, win `62.5%`, Sharpe `0.61`. Requiring aligned distance `>=5bps`
  leaves only `1` trade. Excluding proxy-near-strike rows leaves `22` trades,
  `+$3.78`, but still `2` proxy/official mismatches and a `-$2.00`
  official-minus-proxy delta. The q250 issue is therefore settlement-basis
  fragility, not one obvious bad near-strike row.
- `q1000_yes` still has clean proxy/official agreement on the current exact
  replay, but only `8` official rows, `+$0.90`, win `62.5%`, max DD `-$1.00`.
  It remains research-only.
- Refreshed BTC1H official shadow settlement:
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`.
  The previously active `KXBTCD-26MAY1800-T76999.99` row finalized as a NO win.
  BTC1H high-conf80 entry70 no-chase shadow now has `5/5`
  official-finalized paper rows, official PnL `+$1.52`, official win `100%`,
  proxy PnL `+$0.52`, and `1` proxy/official mismatch. Since
  `2026-05-18T02:42:00Z`, it has `2` official rows, `+$0.62`, no mismatches.
  This is encouraging but still far too small and shadow-ledger-only.
- Re-checked processes with the required `Get-CimInstance` filter at the end
  of the run. All four target Python processes were running:
  BTC15M live capture PID `1724`, BTC15M q250 qty500 first-skip shadow PID
  `7052`, BTC15M q1000 YES shadow PID `24840`, and BTC1H high-conf80 entry70
  no-chase shadow PID `16216`. Latest status artifact:
  `backtest_outputs\btc_forward_shadow_status_latest_codex` at
  `2026-05-18T04:17:37Z`. q250 was readable and still had `0` paper fills,
  `168,245` signal scans, `0` nonzero-candidate scans, and latest reject
  `h02_ttl_outside_12.39`. q1000/BTC1H capture DB reads hit live DuckDB
  writer locks on this status refresh, but process checks and trade-ledger
  reads still showed q1000 `0` paper fills and BTC1H `5` paper fills.
- Refreshed readiness:
  `backtest_outputs\deployment_readiness_latest_codex`, created
  `2026-05-18T04:16:37Z`. `production_ready_count = 0`. The focused blockers
  remain:
  `q250_firstskip_qty500` blocked by
  `latest_live_replay_only_not_full_promotion_gate`,
  `too_few_live_official_trades`, `proxy_official_settlement_mismatch`, and
  `proxy_official_pnl_disagreement`; `q1000_yes` blocked by
  `latest_live_replay_only_not_full_promotion_gate` and
  `too_few_live_official_trades`; BTC1H shadow rows blocked by
  shadow-ledger-only, too-few-shadow-official-trades, and the older
  proxy/official mismatch.
- Refreshed consolidated report:
  `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
  `2026-05-18T04:17:44Z`. Verdict remains:
  `NO DEPLOY: readiness production_ready_count is 0.`
- Validation:
  `python -m py_compile scripts\check_btc_forward_shadow_status.py
  scripts\build_btc_forward_evidence_report.py
  scripts\check_btc_deployment_readiness.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\fill_btc15m_live_ws_official_results.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\analyze_btc15m_latest_live_replay_diagnostics.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53` tests, with the known
  logging ResourceWarnings and pandas all-NaN warning.
- Current interpretation:
  do not deploy. q250 first-skip is still interesting but materially weakened
  by official settlement. q1000 YES is clean but too small. BTC1H forward
  shadow has a nice tiny official streak, but tiny streaks are not evidence
  of deployability. The honest next loop is either more forward official
  shadow collection or a GPT Pro re-review using this updated q250/BTC1H
  evidence before designing the next frozen validation plan.

## 2026-05-17/18 GPT Pro re-review and post-freeze forward baseline

- Patched `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  explicitly include the consolidated forward report, exact q250/q1000
  REST-official replay summaries, and the latest live-replay diagnostics:
  `btc_forward_evidence_report_`,
  `btc15m_f2_live_ws_q250_firstskip_rest_official_`,
  `btc15m_f2_live_ws_q1000_yes_rest_official_`, and
  `btc15m_latest_live_replay_diagnostics_`.
- Built and submitted GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260517_222023`.
  Chrome extension automation worked through the `Sami` profile with a Pro
  composer; ChatGPT converted the long bundle into a pasted markdown
  attachment. The response was saved at
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260517_222556_response.md`.
  The initial `save_gpt_pro_review.ps1` run captured the Windows clipboard
  prompt rather than the Chrome response, so
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260517_222556.md` is only
  the prompt echo and should not be treated as the review.
- GPT Pro agreed with the local gate:
  no deploy, not even a small canary. It ranked the most plausible paths as
  (1) BTC15M q250 first-signal qty>=500, weakened by official settlement;
  (2) BTC15M q1000 YES, cleaner but too sparse; and
  (3) BTC1H high_conf80_entry70_no_chase, encouraging but microscopic and
  shadow-ledger-only. It recommended freezing those tracks, tracking
  post-freeze live websocket evidence from `2026-05-18T04:17:44Z` onward,
  prioritizing settlement-basis modeling / CF Benchmarks approximation, and
  requiring at least `100` BTC15M or `50` BTC1H post-freeze official-settled
  rows across at least `7` calendar days before promotion.
- Ran frozen post-freeze q250 replay from `2026-05-18T04:17:44Z`:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_20260518_041744_codex`.
  Rule was exact q250 first-signal qty>=500, TTL `10-12m`, spread `<=2c`,
  entry `0.02..0.50`, fair probability `>=0.60`, edge `>=12c`. Capture end
  was `2026-05-18T04:29:06.966391Z`; `13,525` top rows were scanned but there
  were `0` closed proxy-settled signals and `0` official rows.
- Ran frozen post-freeze q1000 YES replay from the same freeze point:
  `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_20260518_041744_codex`.
  It also had `0` closed proxy-settled signals and `0` official rows. This is
  expected this soon after the freeze point; it establishes a clean forward
  baseline rather than evidence of edge.
- Patched `scripts\backtest_btc15m_f2_live_ws_holdout.py` so empty
  post-freeze windows write clean empty artifacts instead of crashing on
  missing `proxy_result`, and so missing proxy close prices remain unresolved
  instead of becoming accidental NO labels. Also cleaned the datetime fallback
  assignment in the proxy-settlement path.
- Refreshed status and official settlement with
  `--since-utc 2026-05-18T04:17:44Z`:
  `backtest_outputs\btc_forward_shadow_status_latest_codex` and
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created at
  `2026-05-18T04:29:33Z`. All four target processes were running. Since the
  freeze point, BTC15M q250/q1000 and BTC1H all had `0` paper fills. q250 had
  `176,422` signal scans, `0` nonzero-candidate scans, latest reject
  `h02_ttl_outside_0.48`; q1000 had `310,796` signal scans, `0`
  nonzero-candidate scans, latest reject `h02_ttl_outside_0.46`.
- Refreshed readiness and report after the post-freeze baseline:
  `backtest_outputs\deployment_readiness_latest_codex`, created
  `2026-05-18T04:29:42Z`, and
  `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
  `2026-05-18T04:29:48Z`. Readiness remains
  `production_ready_count = 0`; the consolidated report remains
  `NO DEPLOY`.
- Added diagnostic-only settlement-basis watch:
  `scripts\build_btc15m_settlement_basis_watch.py`, output
  `backtest_outputs\btc15m_settlement_basis_watch_latest_codex`, created
  `2026-05-18T04:32:42Z`. It combines the broad BTC15M REST-official live
  replay plus exact q250 first-skip and q1000 YES REST-official rows into one
  row-level basis table. It is not a threshold search.
- Basis watch result:
  q250 first-skip NO has `16` official/proxy rows, `3` mismatches, mismatch
  rate `18.75%`, official PnL `+$2.86`, proxy PnL `+$5.86`,
  official-minus-proxy PnL delta `-$3.00`, mean official-minus-proxy basis
  `+$8.52`, and max absolute basis `$31.16`. q250 first-skip YES has `8`
  rows, `0` mismatches, and no PnL delta. q1000 YES has `8` rows,
  `0` mismatches, and no PnL delta. This makes the current research fork very
  explicit: q250 has more signal but a NO-side settlement-basis problem;
  q1000 YES is cleaner but extremely sparse.
- Future GPT Pro packets now include `btc15m_settlement_basis_watch_` artifacts
  and prioritize `settlement_basis_summary.csv` when present.

## 2026-05-17/18 Forward refresh and kill-or-continue control table

- Refreshed process state again with the GPT Pro freeze timestamp
  `--since-utc 2026-05-18T04:17:44Z`. All four expected runners were still
  present: BTC15M capture PID `1724`, BTC15M q250 first-skip shadow PID
  `7052`, BTC15M q1000 YES shadow PID `24840`, and BTC1H high-conf80 entry70
  no-chase shadow PID `16216`.
- Refreshed post-freeze q250/q1000 live websocket replays:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_20260518_041744_codex`
  and
  `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_20260518_041744_codex`.
  Capture end advanced to `2026-05-18T04:35:21.909619Z`; each replay saw
  `23,975` top rows, `13,499` quote rows after metadata, and `0` raw F2 hits,
  `0` first signals, `0` closed proxy trades, and `0` official rows.
- Refreshed shadow status:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
  `2026-05-18T04:35:30Z`. Since the freeze point, q250/q1000/BTC1H still had
  `0` paper fills. q250 had `182,401` signal scans, `0`
  nonzero-candidate scans, latest detail `stale_btc_spot`; q1000 had
  `316,822` signal scans, `0` nonzero-candidate scans, latest detail
  `h02_ttl_outside_9.52`. The live capture DB read hit a transient writer
  lock, but process state and q250/q1000 shadow DBs were live.
- Refreshed official shadow settlement:
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
  `2026-05-18T04:35:28Z`. BTC15M q250/q1000 still had `0` fills. BTC1H remained
  `5` official-filled paper rows all-time, official PnL `+$1.52`, proxy PnL
  `+$0.52`, and `1` proxy/official mismatch; since the freeze timestamp it had
  `0` official rows.
- Refreshed readiness and consolidated report:
  `backtest_outputs\deployment_readiness_latest_codex`, created
  `2026-05-18T04:35:51Z`, and
  `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
  `2026-05-18T04:36:03Z`. Readiness remains
  `production_ready_count = 0`; the consolidated verdict remains
  `NO DEPLOY`.
- Added `scripts\build_btc_kill_continue_report.py`, a deployment-control
  summary that combines readiness, forward shadows, post-freeze replay, and
  settlement-basis artifacts without searching thresholds. Latest output:
  `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T04:38:24Z`.
- Kill-or-continue actions:
  - `broad_q_families`: `KILL_FOR_DEPLOYMENT`; broad q strategies are
    REST-official negative and settlement-fragile.
  - `q250_firstskip_qty500`: `CONTINUE_FORWARD_ONLY`; it has `24` latest
    official replay rows, `+$3.74`, but `3` basis mismatches and `-$3.00`
    official-minus-proxy PnL delta, with `0` post-freeze fills so far.
  - `q1000_yes`: `CONTINUE_FORWARD_ONLY_SPARSE`; it has `8` official replay
    rows, `+$0.90`, `0` basis mismatches, and `0` post-freeze fills.
  - `btc1h_high_conf80_entry70_no_chase`:
    `OBSERVE_ONLY_RESTART_WITH_PERMISSION`; `5` official all-time shadow rows,
    `+$1.52`, `1` old proxy/official mismatch, and `0` post-freeze rows. A
    clean official-settlement shadow restart would require explicit permission.
- Future GPT Pro packets now include `btc_kill_continue_` artifacts and
  prioritize `kill_continue_summary.csv` when present.
- Validation:
  `python -m py_compile scripts\build_btc_kill_continue_report.py
  scripts\build_btc15m_settlement_basis_watch.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53` tests, with only the
  known logging ResourceWarnings and pandas all-NaN warning.
- Final process check after validation again showed only the four expected
  runners: BTC15M capture PID `1724`, q250 shadow PID `7052`, q1000 YES shadow
  PID `24840`, and BTC1H high-conf80 entry70 no-chase shadow PID `16216`.

## 2026-05-17/18 Signal-health refresh and GPT Pro packet v2

- Added `scripts\analyze_btc15m_shadow_signal_health.py`, a diagnostic-only
  audit for the BTC15M q250 first-skip and q1000 YES paper-shadow capture
  DuckDBs. It does not search thresholds or promote strategies; it checks
  whether zero post-freeze fills look like genuine no-signal behavior or a
  broken/stale capture path.
- Refreshed BTC15M signal health:
  `backtest_outputs\btc15m_shadow_signal_health_latest_codex`, created
  `2026-05-18T04:46:19Z`, using the GPT Pro freeze timestamp
  `2026-05-18T04:17:44Z`.
  Both BTC15M shadow capture DBs were readable. Since the freeze:
  q250 had `21,885` signal-scan rows, `0` nonzero-candidate rows, `0`
  selected rows, top detail family `h02_ttl_outside` at `76.13%`, and stale
  BTC spot detail families at about `11.13%`. q1000 YES had `21,774`
  signal-scan rows, `0` nonzero-candidate rows, `0` selected rows, top detail
  family `h02_ttl_outside` at `76.06%`, and stale BTC spot detail families at
  about `11.07%`.
- Interpretation: zero BTC15M post-freeze fills are mostly frozen-rule
  no-signal behavior, not a dead shadow. Stale BTC spot remains worth
  monitoring but is secondary to TTL/no-edge rejection in this window.
- Refreshed process/status/official/readiness/consolidated reports with
  `--since-utc 2026-05-18T04:17:44Z`. Latest artifacts:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`
  (`2026-05-18T04:46:40Z`),
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`
  (`2026-05-18T04:46:46Z`),
  `backtest_outputs\deployment_readiness_latest_codex`
  (`2026-05-18T04:46:49Z`), and
  `backtest_outputs\btc_forward_evidence_report_latest_codex`
  (`2026-05-18T04:46:51Z`).
- Status refresh found all four expected runners still alive. BTC15M q250 and
  q1000 had `0` paper fills since the freeze. BTC1H high-conf80 entry70
  no-chase had `1` new paper fill since the freeze, but it was still active
  and had `0` official-settled post-freeze rows. BTC1H all-time official
  finalized rows remain `5`, official PnL `+$1.52`, proxy PnL `+$0.52`, with
  `1` proxy/official mismatch.
- Readiness still reports `production_ready_count = 0`. Consolidated report
  still says `NO DEPLOY: readiness production_ready_count is 0.`
- Regenerated `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T04:47:04Z`, after the status refresh. It now includes
  signal-health columns and the new BTC1H post-freeze paper row:
  `broad_q_families = KILL_FOR_DEPLOYMENT`,
  `q250_firstskip_qty500 = CONTINUE_FORWARD_ONLY`,
  `q1000_yes = CONTINUE_FORWARD_ONLY_SPARSE`, and
  `btc1h_high_conf80_entry70_no_chase =
  OBSERVE_ONLY_RESTART_WITH_PERMISSION`.
- Patched `scripts\build_btc_kill_continue_report.py` and
  `scripts\build_gpt_pro_strategy_packet.py` so future control tables and GPT
  Pro packets include BTC15M shadow signal-health evidence.
- Built updated GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260517_224808`, created
  `2026-05-18T04:48:10Z`. It includes the latest forward evidence,
  kill-or-continue control table, settlement-basis watch, and BTC15M
  signal-health diagnostic. Chrome extension automation was available on the
  `Sami` profile, and the packet was submitted to ChatGPT Pro as pasted
  markdown attachment in conversation
  `https://chatgpt.com/c/6a0a9a67-6588-8326-97bd-ab32cbf91597`.
- Saved the actual GPT Pro response at
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260517_225620.md`. The
  attempted save immediately before it,
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260517_225525.md`, is a
  prompt echo caused by the Chrome/browser clipboard being separate from the
  Windows clipboard; do not treat `225525` as a review.
- GPT Pro v2 again agreed with the local gate: no deploy and no one-contract
  live canary. It ranked q250 first-skip as the best research path, q1000 YES
  as the cleanest sparse path, and BTC1H high-conf80 entry70 no-chase as an
  observe-only slow-burn. It emphasized settlement-basis modeling, q250 NO-side
  fragility, official-settlement-only scoring, and clean post-freeze collection
  over threshold search.
- Followed the GPT Pro v2 action plan by rerunning the exact frozen BTC15M
  post-freeze replays without parameter changes:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_20260518_041744_codex`
  and
  `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_20260518_041744_codex`.
  Capture end advanced to about `2026-05-18T04:57Z`. q250 scanned `62,900`
  top rows and q1000 scanned `63,293` top rows; both still had `0` raw F2
  hits, `0` first signals, `0` closed proxy trades, and `0` official rows.
- Regenerated `backtest_outputs\btc_kill_continue_latest_codex` again after
  those post-freeze replays, created `2026-05-18T04:57:41Z`. The research
  control actions did not change: broad q families killed for deployment,
  q250 forward-only, q1000 YES forward-only sparse, BTC1H observe-only unless
  explicitly restarted under clean official-settlement paper rules.
- Final process check still showed only the four expected Python runners:
  BTC15M capture PID `1724`, q250 first-skip shadow PID `7052`, q1000 YES
  shadow PID `24840`, and BTC1H high-conf80 entry70 no-chase shadow PID
  `16216`.
- Validation:
  `python -m py_compile scripts\analyze_btc15m_shadow_signal_health.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc15m_settlement_basis_watch.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.

## 2026-05-17/18 Settlement-basis risk audit and 05:00 UTC refresh

- Refreshed the canonical forward artifacts with the GPT Pro freeze timestamp
  `2026-05-18T04:17:44Z`:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`
  (`2026-05-18T04:59:33Z`),
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`
  (`2026-05-18T04:59:36Z`),
  `backtest_outputs\deployment_readiness_latest_codex`
  (`2026-05-18T04:59:39Z`), and
  `backtest_outputs\btc_forward_evidence_report_latest_codex`
  (`2026-05-18T04:59:41Z`).
- Process/status refresh: all four expected runners were still present. BTC15M
  q250/q1000 shadows were readable and had `0` paper fills since the freeze.
  q250 had `207,515` signal scans and `0` nonzero-candidate rows; q1000 had
  `341,662` signal scans and `0` nonzero-candidate rows. BTC1H still had `1`
  post-freeze paper fill, but `0` official-settled post-freeze rows.
- Readiness remained `production_ready_count = 0`; the consolidated report
  remained `NO DEPLOY`.
- Reran exact frozen BTC15M post-freeze replays without parameter changes:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_20260518_041744_codex`
  and
  `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_20260518_041744_codex`.
  Capture end advanced to `2026-05-18T05:00:00.779996Z`; each replay scanned
  `67,754` top rows and `67,623` quote rows after metadata, with `0` raw F2
  hits, `0` first signals, `0` closed proxy trades, and `0` official rows.
- Reran BTC15M shadow signal health:
  `backtest_outputs\btc15m_shadow_signal_health_latest_codex`, created
  `2026-05-18T05:00:15Z`. Since the freeze, q250 had `39,194` signal scans,
  `0` nonzero-candidate rows, and `0` selected rows; q1000 had `39,056`
  signal scans, `0` nonzero-candidate rows, and `0` selected rows. Top detail
  family was still `h02_ttl_outside` for both at about `77.3%`; stale BTC spot
  detail families were about `10.19%` for q250 and `10.16%` for q1000.
- Added `scripts\build_btc_settlement_basis_risk_audit.py`, a diagnostic-only
  deployment-control artifact that combines BTC15M settlement-basis rows and
  BTC1H official-shadow rows. It reports side-specific basis risk gates using
  current Pro/local thresholds: enough official rows, mismatch rate `<=2%`,
  and official-minus-proxy PnL drift no worse than `-$0.02/trade`.
- Ran the new audit:
  `backtest_outputs\btc_settlement_basis_risk_audit_latest_codex`, created
  `2026-05-18T05:02:15Z`. It produced `182` rows and `21` proxy/official
  mismatch rows. Every candidate-side failed at least one basis gate.
  Key blocker rows:
  - `q250_firstskip_qty500` NO: `16` official rows, `3` adverse mismatches,
    mismatch rate `18.75%`, official-minus-proxy PnL delta `-$0.1875/trade`,
    abs basis p95 `$24.545`, max abs basis `$31.16`.
  - `q1000_yes` YES: `8` official rows, `0` mismatches, but fails sample size.
  - `btc1h_high_conf80_entry70_no_chase_shadow` NO: `5` official rows,
    mismatch rate `20%`, abs basis p95 about `$100.218`, max abs basis
    `$111.67`; still too small and not deployment evidence.
- Patched `scripts\build_btc_kill_continue_report.py` so the kill/continue
  control table reads `settlement_basis_risk_gates.csv`. Latest output:
  `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T05:03:04Z`. q250 now explicitly shows failed basis gate reasons:
  `adverse_proxy_official_mismatches`,
  `official_minus_proxy_pnl_delta_bad`,
  `proxy_official_mismatch_rate_high`,
  `proxy_win_official_loss_flip`, and `too_few_official_rows`.
  q1000 YES shows `too_few_official_rows`. BTC1H shows
  `proxy_official_mismatch_rate_high` and `too_few_official_rows`.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc_settlement_basis_risk_audit_` artifacts and prioritize
  `settlement_basis_risk_gates.csv` /
  `settlement_basis_risk_by_candidate.csv`.
- Built a fresh GPT Pro packet after the basis-risk wiring:
  `gpt_pro_packets\strategy_advisor_20260517_230421`, created
  `2026-05-18T05:04:21Z`. It was not submitted, because the most recent GPT
  Pro review already agrees with the current local next step and the new audit
  confirms the same no-deploy/basis-risk picture rather than changing the
  high-level research plan.
- Validation:
  `python -m py_compile scripts\build_btc_settlement_basis_risk_audit.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. This turn improved the settlement-basis
  control surface, but the full objective is not complete because no candidate
  has enough post-freeze official-settled rows, q250 has adverse settlement
  mismatches, q1000 YES is sparse, and BTC1H remains shadow-only/tiny with a
  proxy/official mismatch history.

## 2026-05-17/18 Forward consistency audit

- Added `scripts\build_btc_forward_consistency_audit.py`, a deployment-control
  artifact that checks whether each frozen candidate agrees across live/paper
  shadow state, official settlement, frozen post-freeze replay, readiness,
  settlement-basis risk, and signal-health evidence. It does not search
  thresholds or authorize deployment.
- Ran:
  `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
  `2026-05-18T05:12:02Z`. Result: `0/3` candidates were consistent enough for
  promotion.
  - `q250_firstskip_qty500`: status
    `consistent_no_signal_no_promotion`; shadow paper fills since freeze `0`,
    post-freeze replay raw hits `0`, first signals `0`, official replay rows
    `0`. This confirms runner/replay consistency, but only because there were
    no post-freeze signals. It is not edge evidence. It also fails readiness,
    basis gate, and sample-size gates.
  - `q1000_yes`: status `consistent_no_signal_no_promotion`; same post-freeze
    `0` raw hits / `0` first signals / `0` official replay rows. It remains a
    sparse sentinel, not a deployable strategy.
  - `btc1h_high_conf80_entry70_no_chase`: status
    `shadow_official_proxy_mismatch`; since-freeze official rows `1`,
    official PnL `-$0.70`, official-minus-proxy PnL `-$1.00`, and one
    proxy/official mismatch. This makes the BTC1H runner-up category weaker,
    not closer.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so GPT Pro packets include
  `btc_forward_consistency_audit_` artifacts and prioritize
  `forward_consistency_summary.csv`.
- Built a fresh GPT Pro packet with the new audit included:
  `gpt_pro_packets\strategy_advisor_20260517_231223`, created
  `2026-05-18T05:12:25Z`. It was later submitted through the Chrome extension
  after confirming ChatGPT was logged in on a Pro account.
- Validation:
  `python -m py_compile scripts\build_btc_forward_consistency_audit.py
  scripts\build_btc_settlement_basis_risk_audit.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. BTC15M q250/q1000 are live and
  replay-consistent only in the "no post-freeze signal" sense; BTC1H has a new
  post-freeze official loss/mismatch. The objective remains open.

## 2026-05-17/18 GPT Pro v3 and basis-danger table

- Confirmed the Chrome extension automation path works on the `Sami` Chrome
  profile and that ChatGPT is logged in with Pro. Submitted the sanitized
  packet `gpt_pro_packets\strategy_advisor_20260517_231223` to a new ChatGPT
  Pro conversation. The paste was accepted as `Pasted markdown(5).md`.
- Saved the Pro response to
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260517_232103.md`.
  Pro's verdict matched the local gate: no BTC15M/BTC1H deployment and no
  one-contract canary. It ranked the paths as q250 first-skip best research
  lead, q1000 YES cleaner but sparse, and BTC1H observe-only. Local correction:
  its BTC1H streak text was stale versus the newest official audit; current
  local truth is `6` official rows all-time and `1` since-freeze row with
  official PnL `-$0.70` and one proxy/official mismatch.
- Followed Pro's highest-value next step by adding
  `scripts\build_btc_basis_danger_table.py`, a diagnostic-only settlement-basis
  danger table builder. It bins normalized official/proxy rows by side,
  proxy-distance bucket, decision-distance bucket, TTL, entry price, visible
  quantity, quote age, BTC spot age, and near-strike status. It does not create
  thresholds or trading rules.
- Ran:
  `backtest_outputs\btc_basis_danger_table_latest_codex`, created
  `2026-05-18T05:23:25Z`. It used `183` normalized basis-risk rows and found:
  - `q250_firstskip_qty500` NO: `16` both-result rows, `3` adverse mismatches,
    mismatch rate `18.75%`, official PnL `+$2.86`, proxy PnL `+$5.86`, and
    official-minus-proxy drift `-$0.1875/trade`.
  - `q250_firstskip_qty500` YES: `8` rows, `0` mismatches, but still too sparse.
  - `q1000_yes` YES: `8` rows, `0` mismatches, official PnL `+$0.90`, still too
    sparse.
  - BTC1H NO: `6` rows, `2` proxy/official mismatches, `1` adverse mismatch,
    mismatch rate `33.33%`; too few rows and not deployment evidence.
  - High-signal bins again concentrate adverse flips in NO-side rows. This is
    diagnostic evidence for settlement-basis modeling, not a new filter.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc_basis_danger_table_` artifacts and prioritize
  `basis_danger_by_candidate.csv` / `basis_danger_bins.csv`.
- Built another packet with the basis-danger artifact included:
  `gpt_pro_packets\strategy_advisor_20260517_232339`, created
  `2026-05-18T05:23:39Z`.
- Validation:
  `python -m py_compile scripts\build_btc_basis_danger_table.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_btc_settlement_basis_risk_audit.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The new work improves the audit and
  review loop, but the objective remains open until fresh post-freeze,
  official-settled, execution-realistic live rows exist.

## 2026-05-17/18 Current refresh and frozen opportunity-rate audit

- Refreshed the canonical forward artifacts from the freeze timestamp
  `2026-05-18T04:17:44Z`:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`
  (`2026-05-18T05:26:34Z`),
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`
  (`2026-05-18T05:26:42Z`),
  `backtest_outputs\deployment_readiness_latest_codex`
  (`2026-05-18T05:26:48Z`), and
  `backtest_outputs\btc_forward_evidence_report_latest_codex`
  (`2026-05-18T05:26:56Z`).
- Process/status: all four expected runners were still present:
  BTC15M capture, q250 first-skip shadow, q1000 YES shadow, and BTC1H
  high-conf80 entry70 no-chase shadow. No processes were killed or restarted.
- Readiness remained `production_ready_count = 0`. The forward evidence report
  remained `NO DEPLOY`.
- BTC1H official-settlement state remained worse than a deployable runner:
  `6` official rows all-time, official PnL `+$0.82`, but `2`
  proxy/official mismatches. Since the freeze there is `1` official row with
  official PnL `-$0.70`, proxy PnL `+$0.30`, and one proxy/official mismatch.
- Reran the exact frozen BTC15M post-freeze replays without changing
  thresholds:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_20260518_041744_codex`
  and
  `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_20260518_041744_codex`.
  Capture end advanced to about `2026-05-18T05:27Z`. q250 scanned
  `122,641` top rows and q1000 scanned `123,269` top rows; both had `0` raw
  F2 hits, `0` first signals, `0` closed proxy trades, and `0` official rows.
- Reran BTC15M shadow signal health:
  `backtest_outputs\btc15m_shadow_signal_health_latest_codex`, created
  `2026-05-18T05:27:46Z`. Since the freeze, q250 had `71,891` signal scans,
  `0` nonzero-candidate rows, and `0` selected rows; q1000 had `71,416`
  signal scans, `0` nonzero-candidate rows, and `0` selected rows. Top detail
  family was `h02_ttl_outside` for both at about `78.5%`; stale BTC spot
  detail families were about `10.59%` for q250 and `10.61%` for q1000.
- Rebuilt the settlement-basis and control artifacts:
  `backtest_outputs\btc_settlement_basis_risk_audit_latest_codex`
  (`2026-05-18T05:27:56Z`),
  `backtest_outputs\btc_kill_continue_latest_codex`
  (`2026-05-18T05:28:05Z`),
  `backtest_outputs\btc_forward_consistency_audit_latest_codex`
  (`2026-05-18T05:28:13Z`), and
  `backtest_outputs\btc_basis_danger_table_latest_codex`
  (`2026-05-18T05:28:15Z`). Conclusions did not improve: no candidate passed
  forward consistency, q250 remains blocked by adverse settlement-basis
  mismatches, q1000 YES remains too sparse, and BTC1H remains observe-only.
- Added `scripts\build_btc15m_frozen_opportunity_rate_report.py`, a
  diagnostic-only sample-accumulation report. It does not tune or promote; it
  compares the older exact live replay rate against the post-freeze no-signal
  window.
- Ran:
  `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`, created
  `2026-05-18T05:31:00Z`. Key rows:
  - `q250_firstskip_qty500`: old exact replay window `5.7209` days,
    `24` REST-official rows, about `4.1951` official rows/day. Post-freeze:
    `0` raw hits, `0` first signals, `0` official rows. At the old replay
    rate, reaching `100` post-freeze official rows would take about `23.84`
    days.
  - `q1000_yes`: old exact replay window `5.7248` days, `8` REST-official
    rows, about `1.3974` official rows/day. Post-freeze: `0` raw hits, `0`
    first signals, `0` official rows. At the old replay rate, reaching `100`
    post-freeze official rows would take about `71.56` days.
  These projections are only collection-planning diagnostics, not validation
  evidence.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc15m_frozen_opportunity_rate_` artifacts and prioritize
  `frozen_opportunity_rate_summary.csv`.
- Built a fresh packet with the refreshed audits and opportunity-rate report:
  `gpt_pro_packets\strategy_advisor_20260517_233115`, created
  `2026-05-18T05:31:15Z`. It was not submitted because the new artifact does
  not create a strategic fork; it reinforces the latest Pro/local conclusion.
- Validation:
  `python -m py_compile scripts\build_btc15m_frozen_opportunity_rate_report.py
  scripts\build_btc_basis_danger_table.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_btc_settlement_basis_risk_audit.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The best honest direction is continued
  forward collection for q250/q1000, settlement-basis modeling, and BTC1H
  observe-only unless explicitly restarted under clean official-settlement
  paper rules.

## 2026-05-17/18 Settlement-basis model feasibility audit

- Followed the GPT Pro recommendation to test whether a decision-time
  settlement-basis danger model is even supportable from the current official
  rows, without turning the result into a trading threshold.
- Added `scripts\build_btc_settlement_basis_model_feasibility.py`. The script
  normalizes the existing basis-risk rows, de-duplicates overlapping candidate
  rows by market/side/entry, excludes post-event fields from model features,
  and runs grouped cross-validation by `market_ticker` for three diagnostic
  targets: proxy/official mismatch, adverse proxy/official mismatch, and proxy
  win / official loss.
- Ran:
  `backtest_outputs\btc_settlement_basis_model_feasibility_latest_codex`,
  created `2026-05-18T05:36:23Z`. It used `183` normalized rows and `129`
  de-duplicated model rows.
- Decision-time features used were:
  `family_btc15m`, `side_yes`, `entry_price`, `visible_qty_log1p`,
  `spread_cents`, `abs_decision_distance_bps`,
  `signed_decision_distance_bps`, `btc_spot_age_sec`, and `rv_60m`.
  `ttl_min` and `quote_age_ms` were present in the feature contract but
  missing for all current rows, so they were reported as unavailable rather
  than silently imputed as real information. Post-event fields
  `proxy_distance_usd`, `official_distance_usd`, `basis_usd`, and
  `abs_basis_usd` were excluded from model features and retained only for
  diagnostics.
- Grouped-CV diagnostics were weak:
  - `proxy_official_mismatch`: `129` samples, `16` positives, OOF AUC
    `0.5668`, average precision `0.1868`, Brier `0.2285`.
  - `adverse_proxy_official_mismatch`: `129` samples, `15` positives, OOF AUC
    `0.4856`, average precision `0.1401`, Brier `0.2423`.
  - `proxy_win_official_loss`: same sample and positive counts as adverse
    mismatch, OOF AUC `0.4856`, average precision `0.1401`, Brier `0.2423`.
- Interpretation: this is a useful negative result. Current data does not
  support a deployable decision-time settlement-basis guard. Any future basis
  guard must be pre-registered and validated on fresh post-freeze
  official-settled live rows before it can affect q250, q1000 YES, or BTC1H.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc_settlement_basis_model_feasibility_` artifacts and prioritize
  `basis_model_feasibility_summary.csv` /
  `basis_model_group_summary.csv`.
- Built a fresh packet with the feasibility audit included:
  `gpt_pro_packets\strategy_advisor_20260517_233833`, created after the audit.
  It was not resubmitted because the new evidence reinforces the current
  local/Pro no-deploy conclusion rather than creating a new strategic fork.
- Validation:
  `python -m py_compile scripts\build_btc_settlement_basis_model_feasibility.py
  scripts\build_btc15m_frozen_opportunity_rate_report.py
  scripts\build_btc_basis_danger_table.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_btc_settlement_basis_risk_audit.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\backtest_btc15m_f2_live_ws_holdout.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. q250 remains the best research path but
  is blocked by official-settlement drift, adverse NO-side basis fragility,
  insufficient post-freeze official rows, and no post-freeze signals. q1000
  YES remains cleaner but too sparse. BTC1H remains observe-only and weaker
  after the since-freeze official loss/mismatch.

## 2026-05-17/18 Predexon REST-official coverage audit

- Refreshed live/readiness state from freeze `2026-05-18T04:17:44Z`.
  All four expected runners were still present: BTC15M capture, BTC15M q250
  first-skip shadow, BTC15M q1000 YES shadow, and BTC1H high-conf80 entry70
  no-chase shadow. No process was killed or restarted.
- Refreshed:
  `backtest_outputs\btc_forward_shadow_status_latest_codex`
  (`2026-05-18T05:41:37Z`),
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`
  (`2026-05-18T05:41:37Z`),
  `backtest_outputs\deployment_readiness_latest_codex`
  (`2026-05-18T05:41:47Z`), and
  `backtest_outputs\btc_forward_evidence_report_latest_codex`
  (`2026-05-18T05:41:47Z`). Readiness still had
  `production_ready_count = 0`; the forward evidence report remained
  `NO DEPLOY`.
- Current refreshed shadow facts:
  - BTC15M q250 and q1000 shadows still have `0` paper fills and `0`
    official-settled rows.
  - BTC1H has `6` official rows all-time, official PnL `+$0.82`, but `2`
    proxy/official mismatches. Since freeze there is `1` official row with
    official PnL `-$0.70`, proxy PnL `+$0.30`, and one proxy/official
    mismatch.
- Added `scripts\audit_btc15m_predexon_official_coverage.py`, a
  diagnostic-only audit that makes REST-official historical coverage explicit
  for the active BTC15M materialized candidates. It treats REST-official rows
  as research evidence and uncovered rows as proxy-only stress/coverage
  warnings, not promotion support.
- Ran:
  `backtest_outputs\btc15m_predexon_official_coverage_latest_codex`, created
  `2026-05-18T05:45:01Z`.
- Market-result coverage from the prior REST fill:
  `374` finalized REST markets with results, `506` REST `404_not_found`
  markets, `191` YES results, and `183` NO results. The missing REST coverage
  is concentrated in January windows; April/May materialized rows have REST
  official coverage.
- Focused candidate coverage:
  - `q250_firstskip_qty500`: `63` selected historical rows, `57`
    REST-official rows, coverage `90.48%`. REST-official covered PnL `+$8.72`,
    proxy-all PnL `+$8.31`. The `6` uncovered proxy-only rows have PnL
    `-$0.41`, win rate `33.33%`, max DD `-$0.74`, and `2` bad uncovered
    windows. This does not kill the research lead, but it prevents treating the
    historical result as fully official-settled evidence.
  - `q1000_yes`: `33` selected historical rows, `23` REST-official rows,
    coverage `69.70%`. REST-official covered PnL `+$6.45`, proxy-all PnL
    `+$7.98`. The `10` uncovered proxy-only rows have PnL `+$1.53`, win rate
    `50.00%`, max DD `-$0.98`, and `1` bad uncovered window. It remains clean
    but too sparse and too partially covered for promotion.
  - Broader q families have worse uncovered proxy stress. For example,
    `q250_both` has only `48.59%` REST-official coverage and `73` uncovered
    proxy-only rows with PnL `-$4.49`, reinforcing the kill-for-deployment
    decision for broad q strategies.
- Patched `scripts\build_btc_kill_continue_report.py` so the q250/q1000 rows
  include Predexon REST-official coverage fields:
  REST-official historical rows, coverage rate, uncovered rows, uncovered
  proxy PnL, and bad uncovered-window count. Rebuilt
  `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T05:46:17Z`. It still says:
  - broad q families: `KILL_FOR_DEPLOYMENT`.
  - q250 firstskip qty500: `CONTINUE_FORWARD_ONLY`, not deployable.
  - q1000 YES: `CONTINUE_FORWARD_ONLY_SPARSE`, not deployable.
  - BTC1H high-conf80 entry70 no-chase: `OBSERVE_ONLY_RESTART_WITH_PERMISSION`.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future Pro packets
  include `btc15m_predexon_official_coverage_` artifacts and prioritize
  `predexon_official_coverage_summary.csv`. Built
  `gpt_pro_packets\strategy_advisor_20260517_234627`; the packet includes the
  new coverage audit and the updated kill/continue report. It was not
  resubmitted because this follows Pro's existing January-stress recommendation
  and reinforces the no-deploy conclusion rather than changing the strategic
  branch.
- Validation:
  `python -m py_compile scripts\audit_btc15m_predexon_official_coverage.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\build_btc_settlement_basis_model_feasibility.py
  scripts\build_btc15m_frozen_opportunity_rate_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. This audit narrows the honest evidence
  for q250/q1000: historical Predexon support is partially REST-official, not
  fully official-settled, and missing January official rows remain stress
  caveats. The next useful evidence still has to come from fresh post-freeze
  live websocket rows with official settlement and execution-realistic
  paper/live agreement.

## 2026-05-17/18 Execution-realism audit

- Refreshed current process and forward state again from freeze
  `2026-05-18T04:17:44Z`.
  - All four expected processes were still running: BTC15M capture, BTC15M
    q250 first-skip shadow, BTC15M q1000 YES shadow, and BTC1H high-conf80
    entry70 no-chase shadow. No process was killed or restarted.
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T05:49:21Z`, still had `production_ready_count = 0`.
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T05:49:22Z`, still reported `NO DEPLOY`.
  - BTC15M q250/q1000 shadows still have `0` paper fills and `0`
    official-settled shadow rows.
  - BTC1H now has `7` paper-filled rows all-time and `2` paper-filled rows
    since freeze, but only `6` official-settled rows all-time and `1`
    official-settled row since freeze. Since-freeze official PnL remains
    `-$0.70`, with proxy PnL `+$0.30` and one proxy/official mismatch. The
    newest BTC1H row is active/unfinalized, so it is not promotion evidence.
- Added `scripts\build_btc_execution_realism_audit.py`, a promotion-control
  diagnostic that audits whether current evidence rows carry the execution
  fields required for eventual deployment:
  - live websocket replay rows: received-time top-book asks/quantities,
    side-entry consistency, visible quantity, spread, BTC spot freshness,
    official-settlement fields, and one-trade-per-event behavior.
  - paper shadow ledger rows: quote age, top visible quantity, quote/signal
    timestamps, decision-time yes/no book prices, contracts, fees, and
    official-settlement status.
- Ran:
  `backtest_outputs\btc_execution_realism_audit_latest_codex`, created
  `2026-05-18T05:52:46Z`.
- BTC15M exact live replay execution-field results:
  - `q250_firstskip_qty500_live_replay`: `24` rows, `24` official rows,
    official PnL `+$3.74`, replay field-complete rate `100%`,
    entry-side-ask match rate `100%`, visible-quantity-side-ask-qty match rate
    `100%`, visible quantity `>=500` on `100%` of rows, spread `<=2c` on
    `100%`, BTC spot age within `120s` on `100%`, p95 BTC spot age `13.68s`,
    max BTC spot age `16.88s`, `0` duplicate event rows. Audit status:
    `PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION`.
  - `q1000_yes_live_replay`: `8` rows, `8` official rows, official PnL
    `+$0.90`, replay field-complete rate `100%`, entry/visible/spread/BTC-age
    checks all `100%`, min visible quantity `1003.72`, `0` duplicate event
    rows. Audit status: `PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION`.
  These are necessary execution-field passes for the old live replay window,
  not promotion passes, because sample size, post-freeze collection,
  settlement-basis risk, and live/paper agreement still fail.
- BTC15M shadow ledger execution-field results:
  - q250 and q1000 YES paper shadows have `0` filled ledger rows, so ledger
    execution realism is still unassessed for forward paper fills. Audit status
    for both: `NO_FILLED_LEDGER_ROWS`.
- BTC1H shadow ledger execution-field result:
  - `btc1h_high_conf80_entry70_no_chase_shadow`: `7` paper rows, `6`
    official rows, `1` pending official row. Required ledger field-complete
    rate `0%` for the execution-only fields needed for promotion. Missing
    fields: `quote_age_ms`, `top_visible_qty`, `quote_received_at_ns`,
    `signal_received_at_ns`, `yes_bid`, `yes_ask`, `no_bid`, and `no_ask`.
    Audit blockers:
    `missing_ledger_execution_fields`,
    `quote_age_missing_or_above_limit`,
    `top_visible_qty_missing_or_below_contracts`,
    `entry_not_reconciled_to_side_ask`, and
    `pending_official_settlement_rows`. Audit status:
    `FAIL_LEDGER_EXECUTION_FIELDS`.
  This makes BTC1H strictly not deployable from the current running shadow,
  independent of PnL.
- Patched `scripts\build_btc_kill_continue_report.py` so the q250/q1000/BTC1H
  rows include replay and/or ledger execution-realism status. Latest
  `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T05:53:59Z`, still says:
  - broad q families: `KILL_FOR_DEPLOYMENT`.
  - q250 firstskip qty500: `CONTINUE_FORWARD_ONLY`, with replay execution
    fields passing but no forward shadow ledger fills.
  - q1000 YES: `CONTINUE_FORWARD_ONLY_SPARSE`, with replay execution fields
    passing but no forward shadow ledger fills.
  - BTC1H high-conf80 entry70 no-chase:
    `OBSERVE_ONLY_RESTART_WITH_PERMISSION`, with
    `FAIL_LEDGER_EXECUTION_FIELDS`.
- Reran `scripts\build_btc_forward_consistency_audit.py` after the refreshed
  BTC1H shadow state:
  `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
  `2026-05-18T05:54:49Z`. Result stayed `0/3` candidates consistent enough
  for promotion. BTC1H now shows `2` shadow paper fills since freeze, `1`
  official-filled row since freeze, official PnL `-$0.70`, and the same
  proxy/official mismatch blocker.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future Pro packets
  include `btc_execution_realism_audit_` artifacts and prioritize
  `execution_realism_summary.csv`. Built
  `gpt_pro_packets\strategy_advisor_20260517_235458`, which includes the new
  execution-realism audit plus refreshed kill/continue and forward-consistency
  artifacts. It was not submitted because this is a local gate audit that
  reinforces the existing Pro/local no-deploy conclusion rather than creating a
  strategic fork.
- Validation:
  `python -m py_compile scripts\build_btc_execution_realism_audit.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\check_btc_forward_shadow_status.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. BTC15M old live replay rows look
  execution-field-consistent, but the frozen paper shadows have no post-freeze
  fills. BTC1H is weaker: it has a tiny official sample, a since-freeze
  official loss/mismatch, one pending row, and missing ledger execution fields.
  No strategy satisfies the deployment objective.

## 2026-05-18 Shadow ledger schema preflight

- Investigated the BTC1H execution-realism blocker from the prior audit. The
  current source code in `scripts\btc_1hr_research_live.py` already defines
  `TRADE_REALISM_COLUMNS`, calls `ensure_trade_realism_columns(conn)` from
  `db_connect`, and `record_trade` writes `available_qty`, `top_visible_qty`,
  quote/signal timestamps, quote age, strike, TTL, and decision-time yes/no
  book prices.
- Read the active SQLite schemas directly, without mutating them. All three
  current shadow ledgers still have the old `25`-column
  `research_live_trades` schema:
  - `.codex_work\btc15m_f2_q250_qty500_firstskip_shadow\btc15m_f2_q250_qty500_firstskip_shadow_trades.db`
  - `.codex_work\btc15m_f2_q1000_yes_shadow\btc15m_f2_q1000_yes_shadow_trades.db`
  - `~\.btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow.db`
- Added `scripts\build_btc_ledger_schema_preflight.py`, a read-only preflight
  that compares active shadow ledger DB schemas against the required
  deployment ledger column contract. It does not migrate, kill, or restart
  processes.
- Ran:
  `backtest_outputs\btc_ledger_schema_preflight_latest_codex`, created
  `2026-05-18T05:59:30Z`.
- Result:
  - BTC15M q250 first-skip shadow: DB exists, table exists, `0` rows, schema
    columns `25`, required base columns present `19/19`, execution-realism
    columns present `0/12`, missing `12/12`, status
    `FAIL_REALISM_SCHEMA_RESTART_REQUIRED`.
  - BTC15M q1000 YES shadow: same `25`-column legacy schema and
    `FAIL_REALISM_SCHEMA_RESTART_REQUIRED`.
  - BTC1H high-conf80 entry70 no-chase shadow: DB exists, table exists, `7`
    rows, `7` paper-filled rows, schema columns `25`, required base columns
    present `19/19`, execution-realism columns present `0/12`, missing `12/12`,
    rows with any realism field `0`, status
    `FAIL_REALISM_SCHEMA_RESTART_REQUIRED`.
- Missing execution-realism columns for all active shadow ledgers:
  `available_qty`, `top_visible_qty`, `quote_received_at_ns`,
  `signal_received_at_ns`, `quote_age_ms`, `edge_threshold_cents`, `strike`,
  `ttl_min`, `yes_bid`, `yes_ask`, `no_bid`, and `no_ask`.
- Interpretation: the running shadow processes were started before the current
  schema migration/write path was active. Until the user explicitly authorizes
  a controlled restart or migration, future fills from these still-running
  processes are useful as signal-count diagnostics only; they must not be
  counted as deployable paper-ledger evidence.
- Patched `scripts\build_btc_kill_continue_report.py` so q250, q1000 YES, and
  BTC1H rows include ledger schema status, realism-column counts, and whether a
  restart/migration is required before fills can count as deployable evidence.
  Latest `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T06:00:16Z`, shows all three active shadow ledger schemas as
  `FAIL_REALISM_SCHEMA_RESTART_REQUIRED`.
- Patched `scripts\build_gpt_pro_strategy_packet.py` so future Pro packets
  include `btc_ledger_schema_preflight_` artifacts and prioritize
  `ledger_schema_preflight_summary.csv`. Built
  `gpt_pro_packets\strategy_advisor_20260518_000024`, which includes the
  preflight and the updated kill/continue report. It was not submitted because
  this is a local operational gate clarification, not a new strategic fork.
- Validation:
  `python -m py_compile scripts\build_btc_ledger_schema_preflight.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\build_btc_execution_realism_audit.py
  scripts\build_btc_forward_consistency_audit.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. This preflight raises the standard for
  the next collection phase: before any fresh paper fills can be promotion
  evidence, the shadow processes need an explicitly authorized clean
  restart/migration so ledger rows contain execution-realism fields.

## 2026-05-18 Readiness gate hardening for execution realism and ledger schema

- Patched `scripts\check_btc_deployment_readiness.py` so the main conservative
  readiness artifact now consumes:
  - `backtest_outputs\btc_execution_realism_audit_latest_codex\execution_realism_summary.csv`
  - `backtest_outputs\btc_ledger_schema_preflight_latest_codex\ledger_schema_preflight_summary.csv`
- The readiness summary now carries execution/schema status columns for the
  focused frozen candidates and adds hard blockers where appropriate:
  `replay_execution_realism_not_passing`,
  `no_filled_ledger_rows_for_execution_realism`,
  `shadow_ledger_execution_fields_missing`,
  `shadow_ledger_schema_restart_required`, and missing-audit variants.
- Reran:
  `python scripts\check_btc_deployment_readiness.py --out-dir backtest_outputs\deployment_readiness_latest_codex`.
  Exit code `1` is expected for a no-ready verdict. Latest readiness created
  `2026-05-18T06:08:06Z` with `production_ready_count = 0`.
- Focused readiness blockers now include:
  - BTC15M q250 firstskip qty500:
    `latest_live_replay_only_not_full_promotion_gate`,
    `no_filled_ledger_rows_for_execution_realism`,
    `proxy_official_pnl_disagreement`,
    `proxy_official_settlement_mismatch`,
    `shadow_ledger_schema_restart_required`, and
    `too_few_live_official_trades`.
  - BTC15M q1000 YES:
    `latest_live_replay_only_not_full_promotion_gate`,
    `no_filled_ledger_rows_for_execution_realism`,
    `shadow_ledger_schema_restart_required`, and
    `too_few_live_official_trades`.
  - BTC1H high-conf80 entry70 no-chase:
    `research_only_extra_entry_gate_requires_forward_shadow`,
    `shadow_ledger_execution_fields_missing`,
    `shadow_ledger_schema_restart_required`, and
    `too_few_postfreeze_shadow_settled`.
- Reran downstream control artifacts:
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T06:08:24Z`, verdict `NO DEPLOY`.
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T06:08:24Z`, still says broad q families
    `KILL_FOR_DEPLOYMENT`, q250 firstskip qty500
    `CONTINUE_FORWARD_ONLY`, q1000 YES `CONTINUE_FORWARD_ONLY_SPARSE`, and
    BTC1H `OBSERVE_ONLY_RESTART_WITH_PERMISSION`.
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T06:08:31Z`, still reports `0/3` candidates consistent enough
    for promotion.
- Built a fresh GPT Pro packet with the hardened readiness artifact:
  `gpt_pro_packets\strategy_advisor_20260518_000905`. It was not submitted in
  this pass because the new evidence is a local gate-control hardening that
  reinforces the previous Pro/local no-deploy conclusion rather than opening a
  new strategic fork.
- Updated the reusable Codex skill
  `C:\Users\ahmed\.codex\skills\kalshi-btc-strategy-research` so future BTC
  research loops run execution-realism, ledger-schema preflight,
  kill/continue, and forward-consistency checks before treating readiness as
  current. `quick_validate.py` reports `Skill is valid!`.
- Validation:
  `python -m py_compile scripts\check_btc_deployment_readiness.py
  scripts\build_btc_forward_evidence_report.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_gpt_pro_strategy_packet.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. A candidate can no longer sneak through
  the main readiness artifact on positive replay/official PnL while its active
  shadow ledger has no filled rows, no execution-realism columns, or a stale
  schema requiring restart/migration.

## 2026-05-18 Shadow restart/migration preflight

- Refreshed live process and evidence state without killing or restarting any
  process. All four expected Python runners were still present:
  BTC15M capture, BTC15M q250 firstskip shadow, BTC15M q1000 YES shadow, and
  BTC1H high-conf80 entry70 no-chase shadow.
- Refreshed artifacts:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T06:13:33Z`.
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T06:13:36Z`.
  - `backtest_outputs\btc_execution_realism_audit_latest_codex`, created
    `2026-05-18T06:13:38Z`.
  - `backtest_outputs\btc_ledger_schema_preflight_latest_codex`, refreshed in
    the same loop.
  - `backtest_outputs\deployment_readiness_latest_codex` still reports
    `production_ready_count = 0`.
- Refreshed state:
  - BTC15M q250 and q1000 shadows still have `0` paper-filled rows and `0`
    official-settled shadow rows.
  - BTC1H now has `7` paper-filled rows and `7` official-settled rows all-time,
    official PnL `+$1.11`, win rate `85.71%`, but `2` proxy/official result
    mismatches.
  - BTC1H since `2026-05-18T02:42:00Z` now has `4` official rows, official
    PnL `+$0.21`, proxy PnL `+$1.21`, official-minus-proxy `-$1.00`, and `1`
    proxy/official result mismatch. This is still too small and too
    settlement-fragile for promotion.
- Added `scripts\build_btc_shadow_restart_preflight.py`, a non-destructive
  restart/migration preflight. It does not touch live DBs or processes. For
  each active shadow ledger, it:
  - creates a fresh SQLite DB through the current `db_connect` code path,
  - backs up the active DB into an output-only copy,
  - opens the copy through `db_connect` to exercise schema migration,
  - inserts one smoke `paper_filled` row into the fresh DB and the copied DB,
  - verifies all execution-realism fields are present and populated on the
    inserted row.
- Ran:
  `backtest_outputs\btc_shadow_restart_preflight_latest_codex`, created
  `2026-05-18T06:16:17Z`.
- Result:
  - BTC15M q250 firstskip shadow: fresh schema `PASS_SCHEMA_READY`, copied
    active DB before migration `FAIL_REALISM_SCHEMA`, copied DB after
    migration `PASS_SCHEMA_READY`, insert `PASS_INSERT_REALISM_FIELDS`,
    restart path `PASS_RESTART_PATH_READY`.
  - BTC15M q1000 YES shadow: same pass pattern.
  - BTC1H high-conf80 entry70 no-chase shadow: active copied DB had `7`
    existing rows and `0` rows with realism fields before migration; copied DB
    after migration `PASS_SCHEMA_READY`, insert `PASS_INSERT_REALISM_FIELDS`,
    restart path `PASS_RESTART_PATH_READY`.
- Interpretation: current code is ready to create/migrate deployable ledger
  schemas after explicit user authorization, but the currently running shadows
  still are not deployable evidence because their active DB handles are stale.
  Rows written before a clean restart/migration remain diagnostics only.
- Patched `scripts\build_btc_kill_continue_report.py` so q250, q1000 YES, and
  BTC1H rows include restart-path status. Rebuilt
  `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T06:18:05Z`; all three focused rows show
  `restart_path_status = PASS_RESTART_PATH_READY` but
  `restart_deployable_without_live_restart = False`.
- Patched `scripts\build_btc_forward_consistency_audit.py` to remove stale
  wording that described BTC1H since-freeze official PnL as negative. Latest
  `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
  `2026-05-18T06:18:32Z`, reports `0/3` consistent enough for promotion and
  correctly describes BTC1H as blocked by tiny official sample,
  proxy/official mismatch, and failed readiness.
- Patched `scripts\build_gpt_pro_strategy_packet.py` and the reusable
  `kalshi-btc-strategy-research` skill so future Pro packets and default
  research loops include the restart preflight. Built
  `gpt_pro_packets\strategy_advisor_20260518_001858`; not submitted because
  the next required action is operational permission for a clean paper-shadow
  restart/migration, not a new strategic plan.
- Validation:
  `python -m py_compile scripts\build_btc_shadow_restart_preflight.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_ledger_schema_preflight.py
  scripts\build_btc_execution_realism_audit.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning. Skill validation also passed.
- Current conclusion: still no deploy. The immediate blocker is now narrower
  and operational: with explicit permission, the next collection phase should
  restart/migrate the paper shadows so future official-settled fills include
  execution-realism fields. Without that, q250/q1000 BTC15M cannot accumulate
  deployable ledger evidence, and BTC1H remains observe-only.

## 2026-05-18 Guarded paper-shadow restart workflow

- Added `scripts\restart_btc_paper_shadows.ps1`, a guarded PowerShell workflow
  for the eventual user-authorized paper-shadow restart. It is inert by
  default: running it without `-Execute` writes a restart plan and stops/starts
  nothing.
- Scope is intentionally narrow:
  - targets only `scripts\btc15m_f2_q250_qty500_firstskip_shadow.py`,
    `scripts\btc15m_f2_q1000_yes_shadow.py`, and
    `scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`;
  - leaves `scripts\btc15m_live_capture.py` untouched;
  - runs `scripts\build_btc_shadow_restart_preflight.py` before touching
    processes in execute mode;
  - archives stale trade DBs by default before restart, so post-restart rows
    come from a clean deployability ledger;
  - runs post-restart ledger-schema and shadow-status checks.
- Execute mode requires both flags:
  `-Execute -IUnderstandThisRestartsPaperShadows`.
  This was deliberately not run, because the user has not explicitly
  authorized stopping/restarting the live paper-shadow processes.
- Dry-run validation:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1`
  created
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_002306\restart_plan.json`.
  The dry run listed the current target PIDs and printed the explicit execute
  command. It did not stop or start anything.
- Re-checked process state after the dry run: the same four Python processes
  remained running:
  BTC15M capture, BTC15M q250 shadow, BTC15M q1000 YES shadow, and BTC1H
  high-conf80 entry70 no-chase shadow.
- Updated the reusable `kalshi-btc-strategy-research` skill and its
  deployability reference so future sessions use this guarded script instead
  of ad hoc process management when the user explicitly approves a restart.
- Validation:
  `python -m py_compile scripts\build_btc_shadow_restart_preflight.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\check_btc_deployment_readiness.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning. Skill validation also passed.
- Current conclusion: still no deploy. The restart workflow reduces operational
  risk for the next collection phase but does not itself create deployable
  evidence. The next deployability-relevant evidence must be future
  official-settled paper rows written after an authorized clean restart.

## 2026-05-18 Post-restart collection gate

- Added `scripts\build_btc_post_restart_collection_gate.py`, a promotion-control
  verifier for the future post-restart collection phase. It does not restart
  anything and does not search thresholds.
- Purpose: after an explicit controlled paper-shadow restart, count only rows
  written after the restart completion timestamp. It requires:
  - official Kalshi REST settlement (`official_result` present);
  - enough post-restart official rows (`100` for BTC15M, `50` for BTC1H by
    default);
  - positive official PnL;
  - no pending official-settlement rows in the counted post-restart sample;
  - populated execution-realism fields (`quote_age_ms`, `top_visible_qty`,
    quote/signal receive timestamps, and decision-time YES/NO book prices);
  - quote age within the configured limit (`250ms` default);
  - top visible quantity covering contracts;
  - entry matching the decision-time side ask;
  - one trade per event;
  - no proxy/official result mismatches.
- Ran:
  `backtest_outputs\btc_post_restart_collection_gate_latest_codex`, created
  `2026-05-18T06:27:46Z`.
- Current result is intentionally not ready because the controlled restart has
  not been executed:
  - BTC15M q250 firstskip: `PENDING_CONTROLLED_RESTART`, `0/100`
    post-restart official rows.
  - BTC15M q1000 YES: `PENDING_CONTROLLED_RESTART`, `0/100`
    post-restart official rows.
  - BTC1H high-conf80 entry70 no-chase: `PENDING_CONTROLLED_RESTART`, `0/50`
    post-restart official rows.
- Patched `scripts\check_btc_deployment_readiness.py` so the main readiness
  artifact now consumes
  `btc_post_restart_collection_gate_latest_codex\post_restart_collection_gate_summary.csv`.
  Focused q250/q1000/BTC1H rows now include
  `post_restart_collection_gate_status = PENDING_CONTROLLED_RESTART`,
  `post_restart_official_rows = 0`, and failure reason
  `post_restart_collection_gate_not_ready`.
- Patched `scripts\build_btc_kill_continue_report.py` so the focused rows
  include post-restart gate status and post-restart official row counts.
  Latest `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T06:29:42Z`, still reports no deploy; q250 and q1000 remain
  forward-only/sparse, BTC1H remains observe-only/restart-with-permission.
- Patched `scripts\build_gpt_pro_strategy_packet.py` and the reusable
  `kalshi-btc-strategy-research` skill so future GPT Pro packets/default loops
  include the post-restart gate. Built
  `gpt_pro_packets\strategy_advisor_20260518_002943`; not submitted because
  the blocker is still operational collection, not a new strategic fork.
- Validation:
  `python -m py_compile scripts\build_btc_post_restart_collection_gate.py
  scripts\check_btc_deployment_readiness.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_gpt_pro_strategy_packet.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning. Skill validation also passed.
- Current conclusion: still no deploy. The readiness gate now has an explicit
  future-evidence latch: it cannot pass until there is an authorized restart
  plus enough official-settled post-restart paper rows with execution-realism
  fields.

## 2026-05-18 BTC15M frozen signal starvation audit

- Added `scripts\build_btc15m_signal_starvation_report.py`, a forward-control
  artifact for the frozen BTC15M q250/q1000 shadows. It does not search
  thresholds. It asks whether the currently frozen rules are producing any
  post-freeze candidates/order decisions that could eventually become
  official-settled promotion evidence.
- Ran:
  `backtest_outputs\btc15m_signal_starvation_latest_codex`, created
  `2026-05-18T06:39:10Z`, using `since_utc = 2026-05-18T04:17:44Z`.
- Result:
  - q250 firstskip qty>=500 shadow: `144,194` signal rows across `11` events,
    `0` nonzero-candidate rows, `0` selected rows, `0` order-decision rows.
    Status: `STARVED_NO_POSTFREEZE_CANDIDATES`.
  - q1000 YES shadow: `143,265` signal rows across `11` events, `0`
    nonzero-candidate rows, `0` selected rows, `0` order-decision rows.
    Status: `STARVED_NO_POSTFREEZE_CANDIDATES`.
  - Both targets are mostly blocked by `h02_ttl_outside` (`~79.2%` of signal
    scans), then `h02_no_edge` (`~11.4%`), then stale BTC spot (`~9.0%`
    combined stale skip/none families). TTL-outside rows are mostly below the
    frozen `10-12m` window, with the rest above it.
- Interpretation: this is a collection-feasibility blocker, not a deployment
  blocker by itself and not permission to retune on the same live window. The
  runners are scanning, but the frozen q250/q1000 policies are not generating
  any post-freeze candidates to settle. If this persists after an explicitly
  authorized clean restart/migration, the honest next step is to preregister a
  new candidate before evaluating future holdout rows.
- Patched `scripts\build_btc_kill_continue_report.py` so q250/q1000 rows now
  include signal-starvation status, signal rows, zero-candidate counts, top
  blocker family, and promotion implication.
- Patched `scripts\build_gpt_pro_strategy_packet.py` and the reusable
  `kalshi-btc-strategy-research` skill so future GPT Pro packets/default loops
  include this starvation artifact.
- Current conclusion: still no deploy. q250/q1000 remain research-only and
  currently cannot even accumulate official post-freeze BTC15M promotion rows
  under the frozen policy.

## 2026-05-18 GPT Pro strategy review and non-disruptive follow-up

- Submitted the sanitized strategy-advisor packet through the user's Chrome
  ChatGPT Pro session:
  `gpt_pro_packets\strategy_advisor_20260518_004120`.
  The prompt/evidence excluded credentials, raw DuckDB files, raw Parquet
  orderbook data, and full logs.
- Saved the GPT Pro response:
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_065453Z.md`.
- GPT Pro agreed with the local readiness verdict:
  - no BTC15M or BTC1H strategy is deployable now;
  - no one-contract live canary is justified;
  - q250 firstskip qty>=500 remains the best BTC15M research lead, but is
    blocked by NO-side proxy/official basis fragility, no post-freeze fills,
    stale active ledger schema, and signal starvation;
  - q1000 YES is cleaner but too sparse;
  - BTC1H high-conf80 entry70 no-chase is observe-only because sample size is
    tiny, proxy/official mismatches exist, and current ledger rows lack
    execution-realism fields.
- GPT Pro's recommended next disruptive step is the guarded paper-shadow
  restart:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1 -Execute -IUnderstandThisRestartsPaperShadows`.
  This was **not run** because stopping/restarting paper-shadow processes still
  requires explicit user authorization. BTC15M capture was left untouched.
- Ran the non-disruptive diagnostics GPT Pro highlighted:
  - `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`, created
    `2026-05-18T06:55:50Z`. q250 and q1000 are both
    `frozen_rule_inactive_postfreeze`: zero post-freeze raw hits, first
    signals, and official rows. Old replay-rate projection is roughly `23.84`
    days for q250 and `71.56` days for q1000 YES to reach `100` official rows,
    before any settlement-basis gates.
  - `backtest_outputs\btc_basis_danger_table_latest_codex`, created
    `2026-05-18T06:55:53Z`. Confirms adverse proxy/official flips concentrate
    in BTC15M NO-side rows. q250 firstskip qty>=500 NO has `3/16` adverse
    mismatches (`18.75%`) and official-minus-proxy drift `-$3.00` total
    (`-$0.1875/trade`). q250 firstskip YES has `0/8` mismatches but is sparse.
  - `backtest_outputs\btc_settlement_basis_model_feasibility_latest_codex`,
    created `2026-05-18T06:55:55Z`. The decision-time basis model remains
    diagnostic-only: `129` de-duplicated rows, OOF ROC AUC `0.5668` for
    proxy/official mismatch and `0.4856` for adverse/proxy-win-official-loss.
  - `backtest_outputs\btc15m_predexon_official_coverage_latest_codex`,
    refreshed after Pro review. q250 firstskip qty>=500 has `63` selected
    historical rows, `57` REST-official rows (`90.48%` coverage), REST-official
    PnL `+8.72`, but `6` uncovered proxy-only rows with `-0.41` proxy PnL and
    `2` bad uncovered windows. q1000 YES has only `23` REST-official historical
    rows (`69.70%` coverage), so it remains too sparse/partial.
- Updated the reusable `kalshi-btc-strategy-research` skill and deployability
  reference so future default loops include signal starvation, frozen
  opportunity rate, basis danger/model feasibility, and Predexon official
  coverage diagnostics.
- Validation:
  `python -m py_compile scripts\fill_btc15m_predexon_official_results.py
  scripts\audit_btc15m_materialized_filter_grid.py
  scripts\check_btc_deployment_readiness.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\build_btc_forward_evidence_report.py
  scripts\build_btc_execution_realism_audit.py
  scripts\build_btc_ledger_schema_preflight.py
  scripts\build_btc_shadow_restart_preflight.py
  scripts\build_btc_post_restart_collection_gate.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\build_btc15m_signal_starvation_report.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_gpt_pro_strategy_packet.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The next deployability-relevant phase is
  explicit user-authorized paper-shadow restart/migration followed by fresh
  post-restart official-settled rows with complete execution-realism fields.

## 2026-05-18 Current refresh and restart authorization packet

- Re-checked current Python processes. The same four relevant processes are
  running:
  - BTC15M capture PID `1724`;
  - BTC15M q250 firstskip shadow PID `7052`;
  - BTC15M q1000 YES shadow PID `24840`;
  - BTC1H high-conf80 entry70 no-chase shadow PID `16216`.
  No process was killed or restarted.
- Refreshed the conservative control stack at approximately `2026-05-18T07:00Z`:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`:
    `4/4` targets running. BTC15M q250/q1000 still have `0` paper-filled rows
    and `0` order-decision rows. BTC1H still has `7` paper-filled rows all-time
    and `4` since `2026-05-18T02:42:00Z`.
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`:
    BTC1H all-time official PnL remains `+1.11` over `7` rows, but has `2`
    proxy/official result mismatches. Since `2026-05-18T02:42:00Z`, BTC1H has
    `4` official rows, official PnL `+0.21`, proxy PnL `+1.21`, and `1`
    proxy/official mismatch.
  - `backtest_outputs\btc_ledger_schema_preflight_latest_codex`:
    all three shadow ledgers remain `FAIL_REALISM_SCHEMA_RESTART_REQUIRED`
    with `25` active columns and `0/12` execution-realism columns.
  - `backtest_outputs\btc_shadow_restart_preflight_latest_codex`:
    all three restart paths remain `PASS_RESTART_PATH_READY`, but
    `deployable_without_live_restart = False`.
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex`:
    all focused rows remain `PENDING_CONTROLLED_RESTART`, with `0/100` BTC15M
    post-restart official rows and `0/50` BTC1H post-restart official rows.
  - `backtest_outputs\deployment_readiness_latest_codex`:
    `production_ready_count = 0`. Exit code `1` is expected for this
    conservative no-ready result.
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`:
    `0/3` candidates consistent enough for promotion.
  - `backtest_outputs\btc_kill_continue_latest_codex`:
    broad q families remain `KILL_FOR_DEPLOYMENT`; q250 remains
    `CONTINUE_FORWARD_ONLY`; q1000 YES remains
    `CONTINUE_FORWARD_ONLY_SPARSE`; BTC1H remains
    `OBSERVE_ONLY_RESTART_WITH_PERMISSION`.
- Refreshed BTC15M starvation evidence:
  - q250 firstskip qty>=500: `158,462` signal rows across `12` events,
    `0` nonzero-candidate rows, `0` selected rows, `0` order-decision rows.
    Status: `STARVED_NO_POSTFREEZE_CANDIDATES`.
  - q1000 YES: `157,424` signal rows across `12` events, `0`
    nonzero-candidate rows, `0` selected rows, `0` order-decision rows.
    Status: `STARVED_NO_POSTFREEZE_CANDIDATES`.
  - Latest frozen opportunity report still shows zero post-freeze raw hits,
    first signals, and official rows for both q250 and q1000.
- Added `scripts\build_btc_restart_authorization_packet.py`. It is
  non-disruptive: it reads processes and artifacts, writes an authorization
  packet, and does not stop, start, archive, migrate, or deploy anything.
- Ran:
  `backtest_outputs\btc_restart_authorization_packet_latest_codex`, created
  `2026-05-18T07:02:34Z`.
- Result:
  - all three paper-shadow restart targets are
    `READY_FOR_USER_AUTHORIZATION`;
  - targeted PIDs are q250 `7052`, q1000 `24840`, BTC1H `16216`;
  - BTC15M capture PID `1724` is explicitly recorded as untouched;
  - all three restart paths are `PASS_RESTART_PATH_READY`;
  - all three active ledgers still require restart/migration for deployable
    evidence;
  - all three post-restart gates remain `PENDING_CONTROLLED_RESTART`.
- The packet repeats the dry-run command:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1`
  and the execute command that still requires explicit user authorization:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1 -Execute -IUnderstandThisRestartsPaperShadows`.
- Updated `scripts\build_gpt_pro_strategy_packet.py` and the reusable
  `kalshi-btc-strategy-research` skill/reference so future loops include the
  restart authorization packet.
- Validation:
  `python -m py_compile scripts\build_btc_restart_authorization_packet.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\build_btc_kill_continue_report.py
  scripts\check_btc_deployment_readiness.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The evidence pipeline is ready for an
  explicit paper-shadow restart authorization, but deployment remains blocked
  until future post-restart official-settled rows with complete execution
  realism actually exist and pass the full readiness/consistency stack.

## 2026-05-18 GPT Pro follow-up: canonical official settlement feature table

- Followed the GPT Pro recommendation to build a canonical official-settlement
  feature table before changing any strategy logic or restarting paper shadows.
  Added `scripts\build_btc_official_settlement_feature_table.py`.
- The artifact is intentionally diagnostic, not a strategy search. It
  normalizes live websocket replay, paper-shadow official settlement, and
  Predexon selected historical rows into one source-fidelity table with
  explicit promotion blockers.
- Ran:
  `backtest_outputs\btc_official_settlement_feature_table_latest_codex`,
  created `2026-05-18T07:15:24Z`.
- Result:
  - `786` canonical rows and `0` promotion-usable rows.
  - Broad BTC15M live replay rows: `145` official rows, official PnL `-5.11`
    vs proxy PnL `+11.89`, `17` proxy/official mismatches, all adverse.
  - q250 firstskip exact live replay: `24` official rows, official PnL
    `+3.74` vs proxy PnL `+6.74`, `3` adverse mismatches, `12.5%` mismatch
    rate. This keeps q250 firstskip interesting but blocked.
  - q1000 YES exact live replay: `8` official rows, official PnL `+0.90`,
    `0` mismatches. Cleaner, but still too sparse and not post-restart paper
    evidence.
  - BTC1H shadow official rows: `7` official rows, official PnL `+1.11`,
    `2` proxy/official mismatches, `1` adverse/proxy-win-official-loss flip,
    and stale-schema blocker remains.
  - Predexon selected historical rows: `602` selected rows, `362` with REST
    official results and `240` missing official results. These remain
    historical provider-time diagnostics only, not live replay/promotion rows.
  - All source groups have `0` rows with complete requested decision fields and
    `0` rows with complete execution-realism fields in this canonical table.
    The largest blockers are `missing_execution_realism_fields` and
    `missing_requested_decision_fields` on all `786` rows.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include the canonical table. Rebuilt the packet:
  `gpt_pro_packets\strategy_advisor_20260518_011625`. It was not resubmitted
  because the table confirms the existing no-deploy blocker rather than
  creating a new strategic fork.
- Updated the reusable `kalshi-btc-strategy-research` skill and deployability
  reference so future default loops and packets include the canonical
  official-settlement feature table.
- Validation:
  `python -m py_compile scripts\fill_btc15m_predexon_official_results.py
  scripts\audit_btc15m_materialized_filter_grid.py
  scripts\check_btc_deployment_readiness.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\build_btc_forward_evidence_report.py
  scripts\build_btc_execution_realism_audit.py
  scripts\build_btc_ledger_schema_preflight.py
  scripts\build_btc_shadow_restart_preflight.py
  scripts\build_btc_restart_authorization_packet.py
  scripts\build_btc_post_restart_collection_gate.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\build_btc15m_signal_starvation_report.py
  scripts\build_btc15m_frozen_opportunity_rate_report.py
  scripts\build_btc_basis_danger_table.py
  scripts\build_btc_settlement_basis_model_feasibility.py
  scripts\audit_btc15m_predexon_official_coverage.py
  scripts\build_btc_official_settlement_feature_table.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_gpt_pro_strategy_packet.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `53/53`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Re-ran `scripts\check_btc_deployment_readiness.py` into
  `backtest_outputs\deployment_readiness_latest_codex`; expected exit code
  `1`, `production_ready_count = 0`.
- Current conclusion: still no deploy and no canary. The canonical table makes
  the next blocker sharper: no existing row is both official-settled and
  promotion-usable with complete decision/execution realism. The next
  deployability-relevant action remains an explicitly user-authorized clean
  restart/migration of the paper shadows, then fresh official-settled
  post-restart rows.

## 2026-05-18 BTC15M q250 YES-only next-candidate preregistration

- Continued the GPT Pro direction without restarting anything. The purpose was
  to turn the side-specific settlement lesson into a frozen future paper-only
  candidate, not to promote it.
- Added `scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py`, a standby
  paper-only wrapper for:
  - H02/F2 q250 first-signal policy;
  - TTL `10-12m`;
  - spread `<=2c`;
  - entry `0.02-0.50`;
  - side fair probability `>=0.60`;
  - edge `>=12c`;
  - base visible quantity `>=250`;
  - first qualifying signal visible quantity `>=500`;
  - `BTC15M_H02_ALLOWED_SIDE=yes`;
  - one contract max;
  - paper mode only.
- Added `scripts\build_btc15m_next_forward_candidate_packet.py`, which creates
  a preregistration/diagnostic packet from the canonical official-settlement
  feature table. It starts no processes and counts no old row as promotion
  evidence.
- Ran:
  `backtest_outputs\btc15m_next_forward_candidate_packet_latest_codex`.
- Packet result:
  - `btc15m_q250_firstskip_qty500_yes_forward` is
    `PREREGISTER_FOR_FUTURE_PAPER_COLLECTION`;
  - q250 YES diagnostic support:
    - Predexon selected rows: `28` diagnostic rows, `24` REST-official rows,
      official PnL `+4.89`, `0` mismatches;
    - old exact live websocket replay: `8` official rows, official PnL
      `+0.88`, `0` mismatches;
  - q250 NO is explicitly `DO_NOT_START_AS_NEW_FORWARD_CANDIDATE` because old
    exact live replay has `16` official rows, official PnL `+2.86`, but `3`
    adverse official/proxy mismatches;
  - q1000 YES remains `KEEP_EXISTING_FORWARD_ONLY_SPARSE_CONTROL`, with `8`
    old exact live official rows and `23` Predexon REST-official rows.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include this preregistration packet. Rebuilt
  `gpt_pro_packets\strategy_advisor_20260518_012348`; not submitted because
  GPT Pro had already recommended this side-control family and the local
  packet only made the freeze operationally explicit.
- Updated the reusable `kalshi-btc-strategy-research` skill/reference so future
  loops include the next-candidate packet.
- Validation:
  `python -m py_compile scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py
  scripts\build_btc15m_next_forward_candidate_packet.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\test_btc15m_shadow_config.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `54/54`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The q250 YES-only wrapper is now ready
  for a future explicitly authorized paper-only start/restart, but zero future
  promotion rows exist for it. Old replay/Predexon rows are diagnostic support
  only.

## 2026-05-18 guarded paper-shadow start/restart controls and GPT Pro packet

- Re-checked live Python processes before touching the control stack. Active
  repo-related processes were:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- Did not stop, start, or restart any live/capture/paper process.
- Wired the preregistered `q250_firstskip_qty500_yes` paper-only candidate into
  the guarded evidence/control stack:
  - `scripts\restart_btc_paper_shadows.ps1`;
  - `scripts\build_btc_ledger_schema_preflight.py`;
  - `scripts\build_btc_shadow_restart_preflight.py`;
  - `scripts\build_btc_restart_authorization_packet.py`;
  - `scripts\check_btc_forward_shadow_status.py`;
  - `scripts\check_btc_shadow_official_settlement.py`;
  - `scripts\build_btc_post_restart_collection_gate.py`;
  - `scripts\build_btc_execution_realism_audit.py`;
  - `scripts\check_btc_deployment_readiness.py`;
  - `scripts\build_btc_kill_continue_report.py`;
  - `scripts\build_btc_forward_consistency_audit.py`.
- Ran the paper shadow control script without `-Execute`; dry run only:
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_013039`.
  It reported the existing q250, q1000 YES, and BTC1H shadow PIDs and the
  q250 YES-only target as a new start candidate. No process action was taken.
- Regenerated conservative control artifacts:
  - `backtest_outputs\deployment_readiness_latest_codex`: expected exit code
    `1`, `production_ready_count = 0`;
  - `backtest_outputs\btc_kill_continue_latest_codex`: broad q families remain
    `KILL_FOR_DEPLOYMENT`; q250 firstskip remains `CONTINUE_FORWARD_ONLY`;
    q250 YES-only is `PREREGISTERED_PAPER_START_WITH_PERMISSION`;
    q1000 YES is `CONTINUE_FORWARD_ONLY_SPARSE`; BTC1H remains
    `OBSERVE_ONLY_RESTART_WITH_PERMISSION`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`:
    `0 / 4` candidates are consistent enough for promotion. The q250 YES-only
    row is not running, has no post-freeze replay, and has zero official
    future rows.
- Restart authorization packet:
  `backtest_outputs\btc_restart_authorization_packet_latest_codex`:
  - q250, q1000 YES, and BTC1H: `READY_FOR_USER_AUTHORIZATION`;
  - q250 YES-only: `READY_FOR_USER_AUTHORIZATION_TO_START`;
  - all targets still require explicit user permission;
  - BTC15M capture remains explicitly untouched.
- Validation:
  `python -m py_compile scripts\fill_btc15m_predexon_official_results.py
  scripts\audit_btc15m_materialized_filter_grid.py
  scripts\check_btc_deployment_readiness.py
  scripts\check_btc_shadow_official_settlement.py
  scripts\build_btc_forward_evidence_report.py
  scripts\build_btc_execution_realism_audit.py
  scripts\build_btc_ledger_schema_preflight.py
  scripts\build_btc_shadow_restart_preflight.py
  scripts\build_btc_restart_authorization_packet.py
  scripts\build_btc_post_restart_collection_gate.py
  scripts\analyze_btc15m_shadow_signal_health.py
  scripts\build_btc15m_signal_starvation_report.py
  scripts\build_btc15m_frozen_opportunity_rate_report.py
  scripts\build_btc15m_next_forward_candidate_packet.py
  scripts\build_btc_basis_danger_table.py
  scripts\build_btc_settlement_basis_model_feasibility.py
  scripts\audit_btc15m_predexon_official_coverage.py
  scripts\build_btc_official_settlement_feature_table.py
  scripts\build_btc_kill_continue_report.py
  scripts\build_btc_forward_consistency_audit.py
  scripts\build_gpt_pro_strategy_packet.py
  scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py` passed.
  `python -m unittest scripts.test_btc15m_shadow_config
  scripts.test_research_live_safety -v` passed `54/54`, with the known logging
  ResourceWarnings and pandas all-NaN warning.
- Built and submitted a fresh GPT Pro strategy packet through the Chrome
  extension using the Pro composer:
  `gpt_pro_packets\strategy_advisor_20260518_013715`. The packet pasted inline
  with the generated evidence bundle, excluding credentials, raw DuckDB,
  raw Parquet, and bulky logs.
- Saved GPT Pro's response to
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_014532.md`.
  Pro agreed with the no-deploy verdict:
  - deployable now: zero;
  - no one-contract canary is justified;
  - q250 YES-only is the best near-term BTC15M research path, but has zero
    future promotion rows;
  - q1000 YES remains a clean but sparse control;
  - BTC1H remains observe-only;
  - the active stale-schema ledgers cannot generate promotion evidence until a
    clean paper-shadow restart/migration writes complete execution-realism
    fields.
- Pro's concrete 24-hour plan is to execute the controlled paper-shadow
  start/restart workflow for paper shadows only, then collect future
  official-settled rows without retuning thresholds. This was not executed in
  this run because explicit user authorization is required before starting or
  restarting paper shadows.
- Verified that `scripts\build_btc_post_restart_collection_gate.py` already
  encodes Pro's main numeric gate shape: BTC15M `100` official post-restart
  rows, BTC1H `50`, official PnL positive, no pending official rows, no
  proxy/official mismatches, complete execution-realism fields, `quote_age_ms
  <= 250`, top visible quantity covering contracts, entry matching the
  decision-time side ask, and no duplicate event rows.
- Current conclusion: still no deploy and no canary. The only honest next
  deployability-relevant data collection step is an explicitly user-authorized
  paper-only start/restart of the guarded shadows, followed by future
  official-settled post-restart rows with complete execution-realism fields.

## 2026-05-18 no-restart starvation diagnostics hardening

- Continued without treating the goal-continuation wrapper as authorization to
  start or restart paper shadows. No process was stopped, started, or
  restarted.
- Refreshed active repo-related processes:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- Hardened future BTC15M H02 diagnostics in `scripts\btc15m_lowdd_live.py`.
  Future `h02_no_edge` scan details now include:
  - checked side count;
  - entry reject count;
  - quantity reject count;
  - edge reject count;
  - allowed side;
  - side entry/quantity snapshots where available.
  This is diagnostic metadata only and does not change thresholds. The running
  shadows will not emit the enhanced details until an explicitly authorized
  paper restart/start loads the new code.
- Updated `scripts\build_btc15m_signal_starvation_report.py`:
  - added the preregistered `q250_firstskip_qty500_yes` target;
  - reports `NOT_STARTED_MISSING_CAPTURE_DB` for that target until it is
    explicitly started;
  - parses future entry/quantity/edge reject units;
  - parses first-signal visible-quantity skip rows.
- Updated `scripts\analyze_btc15m_shadow_signal_health.py` to include the
  q250 YES-only target.
- Hardened `scripts\check_btc_forward_shadow_status.py` with DuckDB read
  retries. This fixed transient live DB lock failures for the q250 capture DB
  in the top-level forward evidence report.
- Regenerated:
  - `backtest_outputs\btc15m_shadow_signal_health_latest_codex`, created
    `2026-05-18T07:52:35Z`;
  - `backtest_outputs\btc15m_signal_starvation_latest_codex`, final refreshed
    at `2026-05-18T07:53:22Z`;
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T07:55:25Z`;
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T07:54:12Z`;
  - `backtest_outputs\btc_execution_realism_audit_latest_codex`, created
    `2026-05-18T07:54:30Z`;
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex`, created
    `2026-05-18T07:54:30Z`;
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T07:54:30Z`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T07:55:38Z`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T07:55:38Z`;
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T07:54:44Z`.
- Current BTC15M forward starvation result:
  - q250 firstskip qty500: `203,088` signal scans since freeze, `0` nonzero
    candidate rows, `0` selected rows, `0` order decisions,
    `STARVED_NO_POSTFREEZE_CANDIDATES`;
  - q1000 YES: `202,327` signal scans since freeze, `0` nonzero candidate
    rows, `0` selected rows, `0` order decisions,
    `STARVED_NO_POSTFREEZE_CANDIDATES`;
  - q250 YES-only: `NOT_STARTED_MISSING_CAPTURE_DB`, as expected without
    explicit paper-only start authorization.
- Top blocker remains TTL outside the frozen `10-12m` window:
  - q250 firstskip qty500: `79.699%` of scans, with `122,077` below-window and
    `39,686` above-window TTL rejects;
  - q1000 YES: `79.625%` of scans, with `121,703` below-window and `39,303`
    above-window TTL rejects.
- Existing rows are from the pre-enhanced-detail logger, so entry/quantity/edge
  reject units are blank/zero in the current starvation report. Future
  post-restart rows will carry those sub-reasons.
- Latest BTC1H official-settlement refresh found `8` paper-filled rows all
  time, but only `7` official-settled rows. The newest BTC1H row at
  `2026-05-18T07:45:11Z` is still active/pending official settlement, so it
  cannot count toward promotion. BTC1H still has stale-schema/missing
  execution-realism blockers and proxy/official mismatches.
- Latest conservative status remains:
  - readiness `production_ready_count = 0`;
  - forward consistency `0 / 4`;
  - post-restart collection gate `PENDING_CONTROLLED_RESTART` for all four
    candidate ledgers;
  - no deploy and no one-contract canary.
- Validation:
  - `python -m py_compile scripts\btc15m_lowdd_live.py
    scripts\build_btc15m_signal_starvation_report.py
    scripts\analyze_btc15m_shadow_signal_health.py
    scripts\check_btc_forward_shadow_status.py
    scripts\test_btc15m_shadow_config.py` passed.
  - `python -m unittest scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety -v` passed `54/54`, with the known
    logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The work improves the evidence pipeline
  so that, after explicit user-authorized paper-only restart/start, future
  no-candidate windows can distinguish TTL starvation from entry-band,
  quantity, and edge starvation without tuning on the same holdout window.

## 2026-05-18 decision-time settlement-basis guard hardening

- Followed the GPT Pro recommendation to treat official-settlement mismatch as
  a separate deployment-control problem, not as another threshold-search
  opportunity.
- Hardened `scripts\build_btc_settlement_basis_model_feasibility.py`:
  - added a fail-fast decision-feature leakage check. Any future
    `DECISION_FEATURES` entry containing post-event/settlement tokens such as
    `basis`, `official`, `result`, `pnl`, or `settlement` now raises before the
    artifact is produced;
  - added `basis_model_feature_safety_audit.csv`, which explicitly marks
    decision-time model features as allowed and official/proxy outcome fields
    as `post_event_diagnostic_only`;
  - added `basis_guard_deployment_verdict.csv`, which keeps
    `deployable_guard_now = False` unless a pre-registered guard survives fresh
    forward official-settled evaluation;
  - added OOF average-precision lift versus the positive-rate baseline, so a
    weak classifier cannot hide behind raw AP on an imbalanced target.
- Regenerated
  `backtest_outputs\btc_settlement_basis_model_feasibility_latest_codex` at
  `2026-05-18T08:02:47Z`.
- Latest decision-time-safe basis model verdict:
  - de-duplicated model rows: `129`;
  - proxy/official mismatch target: `16` positives, OOF ROC AUC `0.5668`, AP
    lift `1.5063`;
  - adverse mismatch / proxy-win-official-loss targets: `15` positives, OOF ROC
    AUC `0.4856`, AP lift `1.2045`;
  - all targets fail the guard precheck on sample size, positive count, OOF AUC,
    AP lift, and missing pre-registered fresh forward evaluation.
- Deployment state did not improve:
  - no settlement-basis guard is deployable;
  - no BTC15M or BTC1H strategy is production-ready;
  - the official conclusion remains no deploy and no canary.
- Validation:
  - `python -m py_compile scripts\build_btc_settlement_basis_model_feasibility.py
    scripts\test_btc_settlement_basis_model_feasibility.py` passed;
  - `python -m unittest scripts.test_btc_settlement_basis_model_feasibility -v`
    passed `3/3`;
  - `python -m unittest scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `57/57`,
    with the known logging ResourceWarnings and pandas all-NaN warning;
  - `python scripts\check_btc_deployment_readiness.py --out-dir
    backtest_outputs\deployment_readiness_latest_codex` regenerated readiness
    at `2026-05-18T08:03:14Z` with `production_ready_count = 0`. The command
    returned nonzero because the conservative readiness gate found no
    deployable strategies.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  surface `basis_guard_deployment_verdict.csv` from the settlement-basis
  feasibility artifact. Rebuilt
  `gpt_pro_packets\strategy_advisor_20260518_020431`; it was not submitted
  because the new local verdict confirms the existing no-deploy posture rather
  than creating a new strategic fork.

## 2026-05-18 forward evidence refresh and BTC1H capture-lock visibility

- Refreshed the read-only forward evidence stack without stopping, starting, or
  restarting any process.
- Active repo-related Python processes remained:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- Current BTC15M shadow status:
  - q250 firstskip qty500 is running but still has `0` nonzero candidate rows
    and `0` order decisions post-freeze;
  - q1000 YES is running but still has `0` nonzero candidate rows and `0`
    order decisions post-freeze;
  - q250 YES-only remains not started and has no capture DB.
- Current BTC1H official-settlement status:
  - `8` paper-filled rows all-time, all `8` official-settled;
  - all-time official PnL `+0.44`, win rate `75%`;
  - since `2026-05-18T02:42:00Z`: `5` official rows, official PnL `-0.46`,
    win rate `60%`;
  - `2` all-time official/proxy result mismatches, including `1` since the
    freeze; this keeps BTC1H observe-only.
- Regenerated artifacts:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T08:11:03Z`;
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T08:07:32Z`;
  - `backtest_outputs\btc_execution_realism_audit_latest_codex`, created
    `2026-05-18T08:08:04Z`;
  - `backtest_outputs\btc_ledger_schema_preflight_latest_codex`, created
    `2026-05-18T08:08:02Z`;
  - `backtest_outputs\btc_shadow_restart_preflight_latest_codex`, created
    `2026-05-18T08:08:06Z`;
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`, created
    `2026-05-18T08:08:21Z`;
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex`, created
    `2026-05-18T08:08:20Z`;
  - `backtest_outputs\btc_settlement_basis_risk_audit_latest_codex`, created
    `2026-05-18T08:08:05Z`;
  - `backtest_outputs\btc_basis_danger_table_latest_codex`, created
    `2026-05-18T08:08:23Z`;
  - `backtest_outputs\btc_settlement_basis_model_feasibility_latest_codex`,
    created `2026-05-18T08:08:24Z`;
  - `backtest_outputs\btc_official_settlement_feature_table_latest_codex`;
  - `backtest_outputs\btc15m_next_forward_candidate_packet_latest_codex`;
  - `backtest_outputs\btc15m_predexon_official_coverage_latest_codex`;
  - `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`;
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T08:08:49Z`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, final
    refreshed at `2026-05-18T08:11:50Z`;
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T08:09:00Z`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T08:11:16Z`.
- Current conservative deployment evidence:
  - readiness `production_ready_count = 0`;
  - forward consistency `0 / 4`;
  - post-restart collection gate remains `PENDING_CONTROLLED_RESTART` for all
    four candidate ledgers;
  - q250/q1000 BTC15M forward shadows are runner-health evidence only because
    they have no post-freeze candidate/fill rows;
  - BTC1H is worse on the current since-freeze official slice and remains
    blocked by official/proxy mismatch, stale ledger schema, missing
    execution-realism fields, and too few rows.
- Hardened `scripts\check_btc_forward_shadow_status.py`:
  - if a DuckDB live read fails, it now attempts a snapshot-copy fallback;
  - status rows now include `capture_read_source`, DB file mtime, DB file size,
    and snapshot-copy error text.
- Hardened `scripts\build_btc_forward_evidence_report.py` so the signal
  diagnostics table surfaces the new capture read-source, mtime, and snapshot
  failure fields.
- The BTC1H capture DB remained unreadable during this refresh:
  - live read failed because PID `16216` held the file;
  - snapshot copy also failed with Windows `PermissionError(13)`;
  - the report now records this explicitly with DB mtime
    `2026-05-18T07:48:16.533247Z` and size `85,471,232` bytes.
- Updated the GPT Pro packet builder output after the refresh:
  `gpt_pro_packets\strategy_advisor_20260518_021201`. It was not submitted
  because the current evidence did not create a new strategic fork; it sharpened
  the existing no-deploy/no-canary conclusion.
- Validation:
  - `python -m py_compile scripts\check_btc_forward_shadow_status.py
    scripts\build_btc_forward_evidence_report.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `57/57`,
    with the known logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy. The most deployability-relevant next
  action remains an explicitly user-authorized controlled paper-only
  restart/start, because active ledgers cannot produce promotion evidence until
  they write complete execution-realism fields after a clean restart/migration.

## 2026-05-18 restart-controller safety hardening and GPT Pro packet

- Re-checked repo-related Python processes before acting. Active processes were
  unchanged:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- No process was stopped, started, archived, migrated, or restarted. All restart
  work in this entry is dry-run / preflight only.
- Hardened `scripts\restart_btc_paper_shadows.ps1` before any future user-
  authorized restart:
  - added `Test-TargetScriptSafety`;
  - each target script must exist, end in `_shadow.py`, avoid `*_live.py`,
    lock paper mode via the expected BTC15M `--mode paper` or BTC1H `--paper`
    wrapper, and avoid any `--mode live` argv;
  - dry-run `restart_plan.json` now records `script_safety_pass` and
    `script_safety_reasons`;
  - execute mode refuses to proceed if any target fails script safety, before
    any destructive action.
- Added `scripts\test_btc_paper_restart_safety.py` to assert:
  - destructive actions remain behind both `-Execute` and
    `-IUnderstandThisRestartsPaperShadows`;
  - targets are shadow wrappers only;
  - `Start-Process` remains hidden;
  - dry runs emit safety fields without executing.
- Updated `scripts\build_btc_restart_authorization_packet.py` so the
  authorization packet consumes the latest dry-run restart plan and exposes:
  - `latest_restart_plan_script_safety_pass`;
  - `latest_restart_dry_run_plan_exists`;
  - `latest_restart_dry_run_did_not_execute`;
  - `latest_restart_dry_run_script_safety_passed`.
- Latest dry-run plan:
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_021641`.
  It has `execute = False` and all four target rows have
  `script_safety_pass = True`.
- Regenerated
  `backtest_outputs\btc_restart_authorization_packet_latest_codex`, created
  `2026-05-18T08:17:44Z`. It says all four targets are ready for explicit user
  authorization, while still requiring explicit approval before execution.
- Refreshed dependent control artifacts after the safety packet:
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex`, created
    `2026-05-18T08:19:44Z`: all candidates remain
    `PENDING_CONTROLLED_RESTART`, with `0` post-restart official rows;
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T08:19:44Z`: `production_ready_count = 0`; exit code `1`
    remains expected because no strategy is deployable;
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T08:19:44Z`: broad q families remain
    `KILL_FOR_DEPLOYMENT`; q250 firstskip remains `CONTINUE_FORWARD_ONLY`;
    q250 YES-only remains `PREREGISTERED_PAPER_START_WITH_PERMISSION`;
    q1000 YES remains `CONTINUE_FORWARD_ONLY_SPARSE`; BTC1H remains
    `OBSERVE_ONLY_RESTART_WITH_PERMISSION`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T08:19:58Z`: `0 / 4` candidates are consistent enough for
    promotion;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T08:19:58Z`: verdict remains
    `NO DEPLOY: readiness production_ready_count is 0`.
- Built and submitted a fresh GPT Pro strategy-advisor packet through the Chrome
  extension / ChatGPT Pro temporary-chat path:
  `gpt_pro_packets\strategy_advisor_20260518_021956`. The manifest excluded
  `credentials.env`, raw DuckDB files, raw Parquet orderbook data, and full
  logs; a credential-pattern scan of `prompt.md` and `evidence_bundle.md` found
  no key/secret/token/password/private-key style strings.
- Validation:
  - `python -m py_compile scripts\build_btc_restart_authorization_packet.py
    scripts\test_btc_paper_restart_safety.py
    scripts\check_btc_forward_shadow_status.py
    scripts\build_btc_forward_evidence_report.py
    scripts\build_gpt_pro_strategy_packet.py
    scripts\build_btc_post_restart_collection_gate.py
    scripts\build_btc_kill_continue_report.py
    scripts\build_btc_forward_consistency_audit.py` passed;
  - `python -m unittest scripts.test_btc_paper_restart_safety
    scripts.test_btc15m_shadow_config scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `61/61`,
    with the known logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion remains unchanged while GPT Pro is running: no deployment,
  no canary, and no restart/start without explicit user authorization.

## 2026-05-18 GPT Pro response and pre-restart baseline

- Saved the exact GPT Pro response from the Chrome/ChatGPT Pro temporary chat:
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_023800_exact.md`.
  A stale/transformed clipboard save was created first by
  `scripts\save_gpt_pro_review.ps1`; it was removed so the review folder keeps
  the exact extracted response for this packet.
- GPT Pro agreed with the local gate:
  - nothing is deployable now;
  - no live trading, no one-contract canary, and no small-size production test;
  - readiness remains `production_ready_count = 0`;
  - forward consistency remains `0 / 4`;
  - the official-settlement feature table has `787` rows and `0`
    promotion-usable rows;
  - all candidates have `0` post-restart official rows because the controlled
    restart/start has not been executed.
- GPT Pro ranked the near-term paths:
  1. `BTC15M q250_firstskip_qty500_yes_forward`: best next research path
     because it directly avoids the observed q250 NO-side official/proxy flip
     pattern, but it has `0` promotion-usable rows and is not deployable.
  2. `BTC15M q250_firstskip_qty500`: continue only as a side/basis
     decomposition and control path; do not promote raw q250 because the
     NO-side settlement-basis fragility remains a deployment blocker.
  3. `BTC15M q1000_yes`: cleaner but sparse control; keep collecting, but do
     not let it dominate unless q250 YES also fails settlement/execution gates.
  BTC1H remains observe-only until it has clean-schema forward evidence and no
  settlement-basis surprises.
- GPT Pro's 24-hour plan says the next disruptive step is the guarded
  paper-only restart/start:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1 -Execute -IUnderstandThisRestartsPaperShadows`.
  This remains blocked pending explicit user authorization.
- Followed the non-disruptive part of GPT Pro's plan by preserving the current
  pre-restart evidence baseline:
  `backtest_outputs\btc_pre_restart_baseline_20260518_023900`.
  The bundle copied current report/CSV/JSON files from the latest readiness,
  kill/continue, consistency, signal-health, starvation, settlement-basis,
  execution-realism, post-restart, authorization, forward-evidence, shadow
  status, shadow-official, and basis-model artifacts.
- Baseline bundle details:
  - `manifest.json` contains `69` copied artifact files;
  - `README.md` states the bundle is non-disruptive and that no process was
    stopped, started, archived, migrated, or deployed.
- Current conclusion after applying GPT Pro's advice: still no deploy. The
  evidence system is now better preserved and the restart path is more safely
  gated, but the strategy goal is not complete until fresh post-restart
  official-settled, execution-realistic rows accumulate and pass the numeric
  promotion gates.

## 2026-05-18 refreshed Pro-action checklist and current gate state

- Refreshed current process/evidence state without stopping, starting,
  restarting, archiving, migrating, or deploying anything.
- Active Python processes remained unchanged:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- Regenerated the current control stack:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T08:42:12Z`;
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T08:42:06Z`;
  - `backtest_outputs\btc_execution_realism_audit_latest_codex`, created
    `2026-05-18T08:42:03Z`;
  - `backtest_outputs\btc_ledger_schema_preflight_latest_codex`, created
    `2026-05-18T08:41:59Z`;
  - `backtest_outputs\btc_shadow_restart_preflight_latest_codex`, created
    `2026-05-18T08:42:27Z`;
  - dry-run restart plan
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_024244`,
    with `execute = False` and all four target scripts passing safety;
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`, created
    `2026-05-18T08:43:09Z`;
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex`, created
    `2026-05-18T08:42:25Z`;
  - `backtest_outputs\btc15m_shadow_signal_health_latest_codex`, created
    `2026-05-18T08:42:30Z`;
  - `backtest_outputs\btc15m_signal_starvation_latest_codex`, created
    `2026-05-18T08:42:53Z`;
  - `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`, created
    `2026-05-18T08:42:47Z`;
  - `backtest_outputs\btc15m_next_forward_candidate_packet_latest_codex`;
  - `backtest_outputs\btc_basis_danger_table_latest_codex`, created
    `2026-05-18T08:43:10Z`;
  - `backtest_outputs\btc_settlement_basis_model_feasibility_latest_codex`,
    created `2026-05-18T08:43:13Z`;
  - `backtest_outputs\btc15m_predexon_official_coverage_latest_codex`;
  - `backtest_outputs\btc_official_settlement_feature_table_latest_codex`;
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T08:43:25Z`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T08:43:25Z`;
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T08:43:25Z`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T08:43:44Z`.
- Current BTC15M live-shadow state:
  - q250 firstskip: running, `408,914` total signal scans, `0` candidates,
    `0` order decisions, `0` fills; post-freeze starvation report has
    `241,039` signal rows, `0` nonzero candidate rows, and
    `STARVED_NO_POSTFREEZE_CANDIDATES`;
  - q1000 YES: running, `542,328` total signal scans, `0` candidates,
    `0` order decisions, `0` fills; post-freeze starvation report has
    `240,179` signal rows, `0` nonzero candidate rows, and
    `STARVED_NO_POSTFREEZE_CANDIDATES`;
  - q250 YES-only: still not started, missing capture DB, status
    `NOT_STARTED_MISSING_CAPTURE_DB`.
- Current BTC1H state:
  - still `8` all-time official paper rows, official PnL `+0.44`, win rate
    `75%`;
  - since `2026-05-18T02:42:00Z`: `5` official rows, official PnL `-0.46`,
    win rate `60%`, and `1` proxy/official result mismatch;
  - the capture DB is still unreadable/snapshot-locked while PID `16216` holds
    it, and the ledger schema is still missing the execution-realism fields.
- Current deployment-control verdict:
  - readiness `production_ready_count = 0`;
  - forward consistency `0 / 4`;
  - canonical official-settlement feature table has `0` promotion-usable rows;
  - post-restart collection gate remains `PENDING_CONTROLLED_RESTART` with
    `0` post-restart official rows for every candidate;
  - settlement-basis model feasibility still has `0` deployable guards
    (`model_rows = 131`).
- Added `scripts\build_btc_gpt_pro_action_status.py`.
  This control artifact binds GPT Pro's latest recommendations to local
  evidence and writes:
  - `gpt_pro_action_checklist.csv`;
  - `gpt_pro_candidate_status.csv`;
  - `report.md`;
  - `run_info.json`.
- Generated
  `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
  `2026-05-18T08:46:11Z`. It says:
  - current deployment verdict: `BLOCKS_DEPLOYMENT`;
  - forward-layer agreement: `BLOCKS_DEPLOYMENT`;
  - promotion-usable official rows: `BLOCKS_DEPLOYMENT`;
  - post-restart collection gate: `BLOCKS_DEPLOYMENT`;
  - settlement-basis guard deployability: `BLOCKS_DEPLOYMENT`;
  - paper restart authorization path:
    `READY_FOR_EXPLICIT_USER_AUTHORIZATION`, but `passes_for_deployment =
    False`.
- Candidate action statuses from the new artifact:
  - `q250_firstskip_qty500_yes`: top GPT Pro path, not running, `0`
    promotion-usable rows, waiting for explicit paper restart/start
    authorization;
  - `q250_firstskip_qty500`: running but stale ledger schema, `0`
    post-restart rows, use only as side/basis decomposition control;
  - `q1000_yes`: running but stale ledger schema, sparse control only;
  - `btc1h_high_conf80_entry70_no_chase`: observe-only, stale schema, negative
    since-freeze official PnL, and proxy/official mismatch.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc_gpt_pro_action_status_` artifacts and prioritize
  `gpt_pro_action_checklist.csv` / `gpt_pro_candidate_status.csv`. Rebuilt
  `gpt_pro_packets\strategy_advisor_20260518_024639`; it was not resubmitted
  because the new artifact confirms the same strategic fork rather than
  creating a new one.
- Validation:
  - `python -m py_compile scripts\build_btc_gpt_pro_action_status.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc_paper_restart_safety
    scripts.test_btc15m_shadow_config scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `61/61`,
    with the known logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy and no canary. The exact next
  deployability-relevant action remains an explicitly user-authorized guarded
  paper-only restart/start so q250 YES-only, q250 control, q1000 YES, and BTC1H
  can begin producing countable post-restart official-settled rows with
  execution-realism fields.

## 2026-05-18 post-restart evidence-clock verifier

- Added `scripts\build_btc_post_restart_verification.py`.
  This is a read-only post-restart verifier for the immediate period after a
  user-authorized guarded paper-only restart/start. It does not stop, start,
  restart, archive, migrate, tune, or deploy anything.
- The verifier checks whether the evidence machine has actually started:
  - controlled `restart_result.json` exists and has a completion timestamp;
  - the restart plan was safety-checked;
  - `btc15m_live_capture.py` was declared untouched and remains running;
  - all four target paper shadows are running, including q250 YES-only;
  - active ledger schemas no longer require restart and have execution-realism
    columns;
  - post-restart collection gate rows exist and are official-settled enough for
    later collection review.
- Initial run:
  `backtest_outputs\btc_post_restart_verification_latest_codex`, created
  `2026-05-18T08:53:03Z`.
- Current verifier verdict:
  - `restart_executed = False`;
  - `evidence_clock_ready = False`;
  - `restart_result_exists = PENDING_CONTROLLED_RESTART`;
  - restart plan safety and BTC15M capture checks pass for the current dry-run
    plan;
  - all target shadows are not running because q250 YES-only is still not
    started;
  - all active ledgers still fail or miss execution-realism schema;
  - post-restart collection gate remains `NOT_READY`.
- Fixed an important verifier edge case: PowerShell-generated `restart_plan.json`
  can carry a UTF-8 BOM, so the verifier now reads JSON with `utf-8-sig`.
  It also prefers the authorization packet's `latest_restart_plan_dir` instead
  of picking an arbitrary newest dry-run/test restart directory by mtime.
- Added `scripts\test_btc_post_restart_verification.py` covering:
  - BOM-tolerant JSON reads;
  - authorization-packet restart-plan preference;
  - explicit `--restart-dir` precedence.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc_post_restart_verification_` artifacts and prioritize
  `post_restart_verification_checklist.csv` /
  `post_restart_target_status.csv`.
- Rebuilt GPT Pro packet
  `gpt_pro_packets\strategy_advisor_20260518_025341`; it now includes the
  post-restart verifier artifact. It was not submitted because no new strategic
  fork emerged: the verifier reinforces the known explicit-authorization
  blocker.
- Validation:
  - `python -m py_compile scripts\build_btc_post_restart_verification.py
    scripts\test_btc_post_restart_verification.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc_post_restart_verification -v` passed
    `3/3`;
  - `python -m unittest scripts.test_btc_post_restart_verification
    scripts.test_btc_paper_restart_safety scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `64/64`,
    with the known logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion remains unchanged: no deploy, no canary, and no
  post-restart evidence clock until the user explicitly authorizes the guarded
  paper-only restart/start command.

## 2026-05-18 q250 post-freeze official loss and replay-realism correction

- Continued the forward validation without stopping, starting, restarting,
  archiving, migrating, or deploying anything.
- Refreshed the live evidence stack. Active processes remained:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- New q250 raw firstskip forward evidence appeared:
  - q250 firstskip paper shadow logged `1` post-freeze paper fill on
    `KXBTC15M-26MAY180500-00`, side `no`, entry `0.46`, fee `0.02`;
  - Kalshi REST official result is `yes`, so the paper ledger official PnL is
    `-0.48`;
  - this row still cannot count as deployable evidence because the active
    ledger schema is stale and lacks execution-realism fields.
- Replayed the frozen q250 post-freeze live websocket window and found a replay
  realism bug:
  - the replay script had allowed BTC spot ticks up to `120s` old;
  - the live H02 engine rejects BTC spot older than
    `BTC15M_H02_BTC_MAX_AGE_SEC`, default `10s`;
  - the extra replay-only q250 signal at `2026-05-18T05:49:47Z` was rejected
    by the live shadow as `h02_stale_btc_spot`.
- Updated `scripts\backtest_btc15m_f2_live_ws_holdout.py`:
  - added `--max-btc-spot-age-sec`, default `10.0`;
  - filters replay candidates to causal BTC spot age `0..10s` by default,
    matching the live H02 engine.
- Re-ran post-freeze replay with the corrected stale-spot filter:
  - q250 firstskip: `6` raw NO hits, `1` first signal, `1` closed proxy row,
    proxy `-0.50` under 2c stress;
  - REST official fill for that replay row: `1` official row, official PnL
    `-0.50`, no proxy/official result mismatch;
  - q1000 YES: `0` raw hits, `0` first signals.
- Updated `scripts\build_btc_forward_consistency_audit.py`:
  - compares BTC15M paper-fill count to replay-selected/closed rows separately
    from official-settled rows;
  - uses REST-filled replay `summary.csv` when present;
  - reports q250 as
    `paper_replay_count_agree_but_not_promotable`, not a count mismatch.
- Updated `scripts\build_btc_forward_evidence_report.py` so the interpretation
  no longer says BTC15M has zero post-freeze fills when q250 has a settled
  diagnostic loss.
- Added `scripts\test_btc_forward_replay_realism.py` covering:
  - stale BTC spot ticks are excluded by default in live-WS replay;
  - forward consistency replay metrics prefer REST-official replay summaries
    when available.
- Current refreshed artifacts:
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T09:11:06Z`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T09:13:30Z`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T09:11:13Z`;
  - `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_20260518_041744_codex`;
  - `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_20260518_041744_codex`.
- Current verdict remains `NO DEPLOY`:
  - readiness `production_ready_count = 0`;
  - forward consistency `0 / 4`;
  - q250 raw now has one official post-freeze diagnostic loss;
  - q250 YES-only remains the top preregistered future paper-only candidate but
    is not running and has `0` promotion-usable rows;
  - q1000 YES remains sparse/zero-signal post-freeze;
  - BTC1H remains observe-only with tiny official sample, negative since-freeze
    official PnL, proxy/official mismatch, and stale schema.
- Rebuilt latest GPT Pro packet
  `gpt_pro_packets\strategy_advisor_20260518_031625` with the updated local
  evidence and copied its prompt to the Windows clipboard. The Codex in-app
  Browser is not logged into ChatGPT (`/auth/login`), and no callable logged-in
  Chrome/Pro surface was available in this session, so this packet was not
  submitted to GPT Pro.
- Validation:
  - `python -m py_compile scripts\backtest_btc15m_f2_live_ws_holdout.py
    scripts\build_btc_forward_consistency_audit.py
    scripts\build_btc_forward_evidence_report.py
    scripts\test_btc_forward_replay_realism.py` passed;
  - `python -m unittest scripts.test_btc_forward_replay_realism -v` passed
    `2/2`.
  - `python -m unittest scripts.test_btc_forward_replay_realism
    scripts.test_btc_post_restart_verification
    scripts.test_btc_paper_restart_safety scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `66/66`,
    with the known logging ResourceWarnings and pandas all-NaN warning.

## 2026-05-18 q250 YES-only post-freeze replay check

- Continued the forward validation without stopping, starting, restarting,
  archiving, migrating, or deploying anything.
- Active processes were re-checked and remained:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`.
- Confirmed the preregistered q250 YES-only shadow wrapper is paper-only and
  side-specific:
  - YES only;
  - TTL `10..12m`;
  - spread `<= 2c`;
  - entry `2..50c`;
  - side probability `>= 0.60`;
  - edge `>= 12c`;
  - base visible quantity `>= 250`;
  - first-signal visible quantity `>= 500`;
  - max contracts `1`.
- Replayed the q250 YES-only frozen post-freeze live websocket window:
  - artifact:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_20260518_041744_codex`;
  - capture start `2026-05-18T04:17:44Z`;
  - capture end `2026-05-18T09:18:51.245645Z`;
  - `435377` top rows and `25` metadata markets;
  - `0` YES raw hits, `0` NO raw hits, `0` first signals, `0` closed proxy
    trades.
- Updated `scripts\build_btc_forward_consistency_audit.py` so q250 YES-only
  has an explicit candidate row and reads the q250 YES post-freeze replay
  directory. It now reports q250 YES as
  `not_running_or_status_missing` with `0` post-freeze replay rows, rather
  than leaving the replay path missing.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include q250 YES-only post-freeze live replay artifacts and prioritize
  `f2_live_ws_summary.csv`.
- Refreshed control artifacts:
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T09:22:24Z`: `0 / 4` consistent enough for promotion;
  - q250 raw is
    `paper_replay_count_agree_but_not_promotable`, with `1` post-freeze
    official diagnostic loss;
  - q250 YES-only is not running, has `0` raw hits / first signals / closed
    replay rows, and is not deployable;
  - q1000 YES remains zero-signal post-freeze;
  - BTC1H remains blocked by proxy/official mismatch, negative since-freeze
    official PnL, tiny sample, and stale execution schema.
- Refreshed `backtest_outputs\btc_kill_continue_latest_codex`:
  - q250 YES-only action remains
    `PREREGISTERED_PAPER_START_WITH_PERMISSION`;
  - q250 raw remains `CONTINUE_FORWARD_ONLY`;
  - q1000 YES remains `CONTINUE_FORWARD_ONLY_SPARSE`;
  - BTC1H remains `OBSERVE_ONLY_RESTART_WITH_PERMISSION`.
- Refreshed `backtest_outputs\btc_gpt_pro_action_status_latest_codex`:
  - verdict remains `NO DEPLOY`;
  - the only operational next path is still explicit user authorization for the
    guarded paper-only restart/start workflow;
  - q250 YES-only remains GPT Pro's top research path but has `0`
    promotion-usable rows and is not running.
- Important workflow update: the Codex Chrome extension surface is now visible
  for profile `Sami`, so a fresh GPT Pro packet can be submitted through Chrome
  instead of relying on the logged-out in-app Browser. The packet still must
  exclude credentials and raw databases.
- Validation:
  - `python -m py_compile scripts\build_btc_forward_consistency_audit.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc_forward_replay_realism
    scripts.test_btc_post_restart_verification
    scripts.test_btc_paper_restart_safety scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `66/66`,
    with the known logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion remains unchanged: no deploy and no canary. q250 YES-only
  is cleaner conceptually than the NO-heavy q250 raw path, but it is
  collection-starved. It needs an explicit paper-only start/restart before any
  future rows can count.

## 2026-05-18 fresh Chrome GPT Pro review loop

- Successfully used the Codex Chrome extension surface for profile `Sami`.
  ChatGPT was logged in and the composer showed `Pro` selected.
- Built and submitted packet
  `gpt_pro_packets\strategy_advisor_20260518_032401` through a new ChatGPT
  conversation:
  `https://chatgpt.com/c/6a0adb27-0a90-8325-b861-3b62bc22c7e0`.
- Packet safety check:
  - manifest excludes `credentials.env`, raw DuckDB files, raw Parquet
    orderbook data, and full logs;
  - evidence bundle is `634049` bytes;
  - keyword scan found only benign references to credentials/secrets being
    excluded or absent.
- Saved the actual Pro response to
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_fresh_chrome_20260518_033054.md`.
- Fresh GPT Pro verdict:
  - `NO DEPLOY / NO CANARY`;
  - q250 YES-only is the highest-probability research path, but still low
    probability because it is not running, has `0` promotion-usable rows, and
    produced `0` post-freeze replay hits;
  - q1000 YES is cleaner but too sparse;
  - q250 raw should be kept only as side/basis decomposition because the latest
    post-freeze official row was a `-0.48` NO-side diagnostic loss;
  - BTC1H is not top-three and remains observe-only because of tiny sample,
    negative since-freeze official PnL, proxy/official mismatch, stale schema,
    and missing execution-realism fields.
- Fresh GPT Pro 24-hour plan agrees with local gates:
  - preserve the current pre-restart baseline;
  - execute the guarded paper-only restart/start only after explicit user
    authorization;
  - leave `btc15m_live_capture.py` running and untouched;
  - immediately verify the post-restart evidence clock if a restart is ever
    authorized;
  - do not retune thresholds on the first post-restart window.
- Created non-disruptive fresh baseline bundle
  `backtest_outputs\btc_pre_restart_baseline_20260518_033100`.
  It copied `24` current report/review/packet artifacts and did not stop,
  start, restart, archive, migrate, tune, or deploy anything.
- Updated `scripts\build_btc_gpt_pro_action_status.py` so its default GPT Pro
  review is the newest `docs\gpt_pro_reviews\gpt_pro_strategy_advisor*.md`
  file instead of a hardcoded older review.
- Refreshed deployment readiness at `2026-05-18T09:33:54Z`; it still reports
  `production_ready_count = 0`.
- Refreshed
  `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
  `2026-05-18T09:34:06Z`. It now references the fresh Chrome Pro review and
  still says:
  - current deployment verdict blocks deployment;
  - forward-layer agreement blocks deployment;
  - promotion-usable official rows block deployment;
  - post-restart collection gate blocks deployment;
  - basis guard deployability blocks deployment;
  - paper restart path is
    `READY_FOR_EXPLICIT_USER_AUTHORIZATION`, which authorizes nothing by
    itself.
- Validation:
  - `python -m py_compile scripts\build_btc_gpt_pro_action_status.py` passed;
  - `python scripts\build_btc_gpt_pro_action_status.py --out-dir
    backtest_outputs\btc_gpt_pro_action_status_latest_codex` passed.
  - `python scripts\check_btc_deployment_readiness.py --out-dir
    backtest_outputs\deployment_readiness_latest_codex` correctly returned
    the no-production-ready-candidates state.
- Current conclusion remains unchanged and is now independently reaffirmed:
  no deploy, no canary, and no process restart without explicit user
  authorization. The deployability goal is not complete; the next meaningful
  evidence step is future official-settled, execution-realistic paper rows
  collected after a controlled paper-only restart/start.

## 2026-05-18 live-WS starvation decomposition and tiny YES forward fills

- Continued the forward validation without stopping, starting, restarting,
  archiving, migrating, tuning, or deploying anything.
- Active process check remained unchanged:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`;
  - q250 YES-only shadow still not running.
- Added read-only diagnostic
  `scripts\analyze_btc15m_f2_live_ws_starvation.py`.
  It applies the frozen q250/q250-YES/q1000-YES rule settings to live
  websocket capture and decomposes the gates in a fixed order:
  valid quote, BTC spot freshness, TTL, spread, entry band, visible quantity,
  side probability, then edge. It does not search or tune thresholds.
- Generated
  `backtest_outputs\btc15m_f2_live_ws_starvation_latest_codex`, created
  `2026-05-18T09:38:27Z`, on capture window
  `2026-05-18T04:17:44Z` to `2026-05-18T09:38:14.941765Z`.
- Live-WS starvation decomposition:
  - q250 raw: `893226` side rows, `66` raw hits, `2` first-signal events,
    `2` post-first-signal trade events;
  - q250 YES-only: `446613` side rows, `60` raw hits, `1` first-signal event,
    `1` post-first-signal trade event;
  - q1000 YES: `446613` side rows, `60` raw hits, `1` first-signal event,
    `1` post-first-signal trade event;
  - top incremental blocker for all three was the TTL window, blocking about
    `71%` of side rows after quote/BTC-age checks;
  - after executable-looking gates before probability/edge, q250 YES had
    `15559` rows across `15` events, but only `1` event reached both
    probability and edge.
- Re-ran exact frozen live websocket replays through the fresher capture:
  - q250 raw:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_latest_codex`;
  - q250 YES-only:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_latest_codex`;
  - q1000 YES:
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_latest_codex`.
- REST-filled official Kalshi settlement for those new replay rows:
  - q250 raw REST artifact:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex`;
  - q250 YES-only REST artifact:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex`;
  - q1000 YES REST artifact:
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex`.
- New replay/REST official results:
  - q250 raw: `2` official rows, official PnL `0.00` under 2c stress,
    official win rate `50%`, `0` proxy/official mismatches;
  - q250 YES-only: `1` official row, official PnL `+0.50` under 2c stress,
    official win rate `100%`, `0` proxy/official mismatches;
  - q1000 YES: `1` official row, official PnL `+0.50` under 2c stress,
    official win rate `100%`, `0` proxy/official mismatches.
- The new q250 YES/q1000 YES official row is the same market:
  `KXBTC15M-26MAY180530-30`, event `KXBTC15M-26MAY180530`, side `yes`,
  signal `2026-05-18T09:19:48.604537Z`, entry `0.46`, visible quantity
  `1135`, side fair probability about `0.6057`, fair edge about `12.57c`,
  official result `yes`, official PnL `+0.50` under 2c stress.
- Refreshed live shadow status and official settlement:
  - q250 raw running shadow now has `2` post-freeze paper fills, `2`
    official-settled rows, official PnL `+0.04`;
  - q1000 YES running shadow now has `1` post-freeze paper fill, `1`
    official-settled row, official PnL `+0.52`;
  - q250 YES-only remains not running and has `0` ledger rows;
  - BTC1H since-freeze official rows dropped to `3`, official PnL `-1.08`,
    with `1` proxy/official mismatch.
- Updated `scripts\build_btc_forward_consistency_audit.py` so a REST official
  replay-fill directory can recover raw hit / first-signal / proxy counts from
  the source replay directory recorded in its `run_info.json`.
- Refreshed
  `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
  `2026-05-18T09:42:49Z`, using the latest REST-filled replay directories:
  - consistency still `0 / 4`;
  - q250 raw and q1000 YES are now
    `paper_replay_count_agree_but_not_promotable`;
  - q250 YES-only remains `not_running_or_status_missing`;
  - BTC1H remains blocked by negative since-freeze official PnL,
    proxy/official mismatch, stale schema, and too few official rows.
- Refreshed
  `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
  `2026-05-18T09:42:40Z`;
  `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T09:43:16Z`;
  `backtest_outputs\deployment_readiness_latest_codex`, created
  `2026-05-18T09:43:02Z`;
  and `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
  `2026-05-18T09:43:36Z`.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc15m_f2_live_ws_starvation_` artifacts and prioritize
  `f2_live_ws_starvation_summary.csv`,
  `f2_live_ws_starvation_gate_counts.csv`, and
  `f2_live_ws_starvation_near_misses.csv`.
- Validation:
  - `python -m py_compile scripts\analyze_btc15m_f2_live_ws_starvation.py
    scripts\build_btc_forward_consistency_audit.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc_forward_replay_realism
    scripts.test_btc_post_restart_verification
    scripts.test_btc_paper_restart_safety scripts.test_btc15m_shadow_config
    scripts.test_research_live_safety
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `66/66`,
    with the known logging ResourceWarnings and pandas all-NaN warning.
- Current conclusion: still no deploy and no canary. The new YES rows are
  encouraging diagnostics because replay, REST official settlement, and the
  currently running q1000 YES / q250 raw ledgers agree. They are still far too
  small, are from stale-schema active ledgers, and are not post-restart
  promotion evidence. q250 YES-only remains the preferred clean hypothesis but
  has not started collecting ledger evidence.

## 2026-05-18 frozen opportunity-rate refresh with q250 YES-only

- Filled the missing full-window q250 YES-only exact live-WS replay artifact:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_latest_codex`.
  Frozen settings were unchanged: YES only, visible quantity `>=250`, first
  signal visible quantity `>=500`, spread `<=2c`, TTL `10-12m`, entry
  `0.02-0.50`, fair probability `>=0.60`, fair edge `>=12c`, BTC spot age
  `<=10s`.
- Full-window q250 YES-only replay from
  `2026-05-12T10:42:45.158640Z` to `2026-05-18T04:17:44Z` produced:
  - `756` raw hits;
  - `8` first signals;
  - `2` first-signal quantity rejects;
  - `6` closed proxy rows;
  - proxy 2c PnL `+0.90`, win rate `66.67%`.
- REST-filled official settlement for that full-window q250 YES-only replay:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_rest_official_latest_codex`.
  It has `6 / 6` official-filled rows, official 2c PnL `+0.90`,
  official win rate `66.67%`, and `0` proxy/official mismatches.
- Updated `scripts\build_btc15m_frozen_opportunity_rate_report.py` so the
  opportunity-rate report:
  - accepts replay directories or REST official-fill directories for
    post-freeze artifacts;
  - recovers the source replay directory from REST `run_info.json`
    `input_trades`;
  - includes q250 YES-only alongside q250 raw and q1000 YES;
  - reports actual post-freeze official rows/PnL/rates instead of hardcoding
    post-freeze official rows to `0`;
  - labels q250 YES-only as simulated replay only when its preregistered paper
    shadow is not running;
  - labels running q250 raw and q1000 YES shadows as blocked by stale ledger
    schemas.
- Refreshed
  `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`, created
  `2026-05-18T09:56:16Z`.
- Refreshed frozen opportunity-rate summary:
  - q250 raw: prior official rows `24`, prior official PnL `+3.74`;
    post-freeze official replay rows `2`, post-freeze official PnL `0.00`;
    running shadow exists but `FAIL_REALISM_SCHEMA_RESTART_REQUIRED` blocks
    deployable ledger evidence.
  - q250 YES-only: prior official rows `6`, prior official PnL `+0.90`;
    post-freeze official replay rows `1`, post-freeze official PnL `+0.50`;
    preregistered paper shadow is not running and ledger DB is missing, so
    these rows are not promotion evidence.
  - q1000 YES: prior official rows `8`, prior official PnL `+0.90`;
    post-freeze official replay rows `1`, post-freeze official PnL `+0.50`;
    running shadow exists but `FAIL_REALISM_SCHEMA_RESTART_REQUIRED` blocks
    deployable ledger evidence.
- The report's projected days to `100` post-freeze official rows are only
  collection-planning diagnostics from very small windows. They are not a
  reason to retune thresholds, promote a candidate, or deploy.
- Validation:
  - `python scripts\build_btc15m_frozen_opportunity_rate_report.py --out-dir
    backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex` passed;
  - `python -m py_compile scripts\build_btc15m_frozen_opportunity_rate_report.py
    scripts\backtest_btc15m_f2_live_ws_holdout.py
    scripts\fill_btc15m_live_ws_official_results.py` passed.
- Current conclusion remains no deploy and no canary. q250 YES-only is now
  better represented in the evidence packet, but it still needs explicit
  paper-only start/restart authorization before any future official-settled
  ledger rows can count toward promotion.

## 2026-05-18 Predexon REST-official refresh and materialized grid

- Refreshed Predexon materialized trade official settlement:
  `backtest_outputs\btc15m_predexon_rest_official_latest_codex`, created
  `2026-05-18T09:59:43Z`.
- Input was
  `backtest_outputs\btc15m_f2_combined_predexon_plus_jan29_20260516_164544\combined_predexon_trades.parquet`.
  It contained `2424` trade rows and `880` unique market tickers.
- Kalshi REST official coverage remains partial: `374 / 880` unique tickers
  returned REST official `yes/no` results. Predexon metadata settlement
  covered all `880` unique tickers and is useful as a sensitivity check, but
  the grid below intentionally used the requested REST official columns.
- REST-official strategy-level summary highlights:
  - `ttl10_12_entry50_q250`: `69` official rows, PnL `+9.66`, win rate
    `63.77%`, max DD `-1.49`, Sharpe `2.34`;
  - `ttl10_12_entry50_q500`: `67` official rows, PnL `+9.82`, win rate
    `64.18%`, max DD `-1.49`, Sharpe `2.41`;
  - `ttl10_12_entry50_q1000`: `52` official rows, PnL `+8.16`, win rate
    `65.38%`, max DD `-1.52`, Sharpe `2.31`;
  - `ttl10_12_entry50_q250_qspeed05`: `63` official rows, PnL `+10.88`,
    win rate `66.67%`, max DD `-1.89`, Sharpe `2.84`.
- Reran materialized first-signal grid with REST-official Predexon PnL:
  `backtest_outputs\btc15m_materialized_filter_grid_pred_official_latest_codex`.
  Command used
  `--pred-pnl-col pnl_official_rest_2c --pred-win-col win_pnl_official_rest_2c`.
- Default-grid top row remains the q250 first-signal visible-quantity branch:
  `mat_grid_00019`, q250, both sides, fair probability `>=0.60`, edge
  `>=12c`, entry `<=50c`, visible quantity `>=500`, spread `<=2c`, TTL
  `10-12m`.
  It has `57` REST-official Predexon trades, PnL `+8.72`, win rate
  `64.91%`, max DD `-1.97`, Sharpe `2.33`; live REST-official replay remains
  `17` trades, PnL `+4.30`, win rate `76.47%`, max DD `-1.58`, Sharpe
  `2.34`.
- Reran a lower-sample diagnostic grid:
  `backtest_outputs\btc15m_materialized_filter_grid_pred_official_min20_latest_codex`.
  This was not a promotion gate; it exists so the q250 YES + qty>=500 branch
  is visible rather than hidden by the default `min_pred_trades=30` screen.
- Exact q250 YES + qty>=500 direct check:
  - REST-official Predexon: `24` official rows, PnL `+4.89`, win rate
    `70.83%`, max DD `-1.03`, Sharpe `2.12`;
  - metadata-settlement sensitivity: `28` rows, PnL `+4.44`, win rate
    `64.29%`;
  - older live REST-official replay: `7` official rows, PnL `+1.37`, win
    rate `71.43%`;
  - live proxy on the same `7` rows also PnL `+1.37`, win rate `71.43%`.
- Exact q250 YES + qty>=500 did not pass the normal materialized research gate
  because it has too few live official trades (`7`, below the default `8` for
  the grid and far below any deployability threshold). It is directionally
  positive, not deployable.
- Built a refreshed sanitized GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260518_040403`. It includes the latest
  frozen opportunity-rate report, Predexon REST-official fill, min20
  materialized grid, live-WS starvation report, and current readiness outputs.
- Refreshed
  `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
  `2026-05-18T10:05:34Z`. It still says `NO DEPLOY` and
  `WAITING_FOR_EXPLICIT_PAPER_RESTART_AUTHORIZATION`; this authorizes nothing
  by itself.
- Refreshed deployment readiness:
  `backtest_outputs\deployment_readiness_latest_codex`, created
  `2026-05-18T10:04:28Z`.
  It still reports `production_ready_count = 0`.
- Validation:
  - `python scripts\fill_btc15m_predexon_official_results.py --trades
    backtest_outputs\btc15m_f2_combined_predexon_plus_jan29_20260516_164544\combined_predexon_trades.parquet
    --out-dir backtest_outputs\btc15m_predexon_rest_official_latest_codex
    --sleep 0.02` passed;
  - `python scripts\audit_btc15m_materialized_filter_grid.py --predexon-trades
    backtest_outputs\btc15m_predexon_rest_official_latest_codex\predexon_trades_rest_official.parquet
    --live-trades
    backtest_outputs\btc15m_live_ws_rest_official_refresh_20260516_183818\live_ws_trades_rest_official.parquet
    --out-dir
    backtest_outputs\btc15m_materialized_filter_grid_pred_official_latest_codex
    --pred-pnl-col pnl_official_rest_2c --pred-win-col
    win_pnl_official_rest_2c` passed;
  - same grid with `--min-pred-trades 20 --top-n 200` passed;
  - `python -m py_compile scripts\fill_btc15m_predexon_official_results.py
    scripts\audit_btc15m_materialized_filter_grid.py` passed;
  - `python scripts\check_btc_deployment_readiness.py --out-dir
    backtest_outputs\deployment_readiness_latest_codex` returned the expected
    no-production-ready state.
- Current conclusion: BTC15M q250/YES evidence improved, but deployability did
  not. The strongest honest path remains future official-settled,
  execution-realistic, post-restart paper rows under frozen rules. BTC1H
  remains a runner-up/observe-only category, not closer to deployment.

## 2026-05-18 control-artifact refresh and stale post-freeze input fix

- Continued read-only validation. No live deployment, canary, process restart,
  archive, migration, threshold tuning, or paper-shadow start was executed.
- Process refresh still found `4 / 5` expected Python targets running:
  - `btc15m_live_capture.py`, PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py`, PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py`, PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID `16216`;
  - q250 YES-only remains not running.
- Refreshed shadow official settlement at `2026-05-18T10:07:15Z`:
  - q250 raw shadow: `2` official-filled BTC15M rows since freeze, official
    PnL `+0.04`, win rate `50%`;
  - q1000 YES shadow: `1` official-filled BTC15M row since freeze, official
    PnL `+0.52`, win rate `100%`;
  - q250 YES-only shadow: `0` ledger rows because the shadow is not running;
  - BTC1H high-conf80 entry70 no-chase shadow: `8` official-filled rows all
    time, official PnL `+0.44`, but `5` since-window official rows are
    negative at `-0.46` with `1` since-window proxy/official mismatch.
- Refreshed ledger schema preflight at `2026-05-18T10:07:10Z`.
  Running q250 raw, q1000 YES, and BTC1H ledgers still have old 25-column
  schemas with `0 / 12` required execution-realism columns populated.
  q250 YES-only ledger DB is still missing.
- Refreshed BTC15M signal health/starvation at `2026-05-18T10:09Z`:
  - q250 raw: `303117` post-freeze signal rows, `2` selected rows/order
    decisions, top blocker `h02_ttl_outside`, TTL-outside share `75.47%`;
  - q1000 YES: `302504` post-freeze signal rows, `1` selected row/order
    decision, top blocker `h02_ttl_outside`, TTL-outside share `77.76%`;
  - q250 YES-only: `NOT_STARTED_MISSING_CAPTURE_DB`.
- Fixed stale default inputs in
  `scripts\build_btc_kill_continue_report.py` and
  `scripts\build_btc_forward_consistency_audit.py`.
  Their default post-freeze directories now point to the REST-official
  `*_latest_codex` artifacts instead of the older replay-only
  `20260518_041744` artifacts.
- Updated `scripts\build_btc_kill_continue_report.py` so post-freeze metrics
  can read either replay directories or REST official-fill directories. It now
  recovers source replay counts from REST `run_info.json` `input_trades` and
  uses REST `summary.csv` for official trades/PnL when present.
- Refreshed `backtest_outputs\btc_kill_continue_latest_codex`, created
  `2026-05-18T10:10:58Z`. It now correctly reports:
  - q250 raw: `2` post-freeze official replay rows;
  - q250 YES-only: `1` post-freeze official replay row, but not running and
    not promotion evidence;
  - q1000 YES: `1` post-freeze official replay row;
  - BTC1H: observe-only restart-with-permission, still blocked.
- Refreshed `backtest_outputs\btc_forward_consistency_audit_latest_codex`,
  created `2026-05-18T10:10:58Z`.
  Consistency remains `0 / 4`:
  - q250 raw and q1000 YES: replay/paper counts agree, but not promotable;
  - q250 YES-only: not running/status missing;
  - BTC1H: official/proxy mismatch and negative since-window official PnL.
- Refreshed official-settlement feature table at `2026-05-18T10:10:19Z`.
  Promotion-usable rows remain `0` across the canonical feature table because
  decision/execution fields and live/paper promotion gates are still missing.
- Refreshed next-forward candidate packet at
  `backtest_outputs\btc15m_next_forward_candidate_packet_latest_codex`.
  q250 YES-only remains
  `PREREGISTER_FOR_FUTURE_PAPER_COLLECTION`, with `0` promotion-usable rows.
- Refreshed post-restart collection gate and restart authorization packet at
  `2026-05-18T10:10Z`.
  Restart path is ready for explicit user authorization, but
  `restart_executed = False`; this authorizes nothing by itself.
- Refreshed deployment readiness at `2026-05-18T10:10:34Z`.
  `production_ready_count = 0`.
- Refreshed `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
  `2026-05-18T10:10:58Z`.
  It still says `NO DEPLOY`; the only operational path it marks ready is
  explicit user authorization for a guarded paper-only restart/start.
- Rebuilt sanitized GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260518_041110`.
  This was packaged locally only; no new GPT Pro submission was necessary for
  this artifact-refresh step because the blocker is missing forward ledger
  evidence, not an unresolved planning question.
- Validation:
  - `python -m py_compile scripts\build_btc_kill_continue_report.py
    scripts\build_btc_forward_consistency_audit.py
    scripts\build_btc_forward_evidence_report.py
    scripts\build_btc_gpt_pro_action_status.py
    scripts\build_btc15m_next_forward_candidate_packet.py
    scripts\build_btc_official_settlement_feature_table.py
    scripts\check_btc_deployment_readiness.py` passed;
  - `python scripts\check_btc_deployment_readiness.py --out-dir
    backtest_outputs\deployment_readiness_latest_codex` returned the expected
    no-production-ready state.
- Current conclusion remains unchanged: no deploy, no canary, no live strategy.
  BTC15M q250 YES-only is still the best clean research direction, but it has
  not started collecting paper-ledger evidence. BTC1H moved further away from
  deployability because the refreshed since-window official ledger sample is
  negative and still proxy/official-fragile.

## 2026-05-18 BTC15M shadow/replay config-equivalence audit

- Added read-only audit
  `scripts\build_btc15m_shadow_replay_config_audit.py`.
  It parses the BTC15M paper-shadow wrapper `os.environ.setdefault(...)`
  values, compares them to frozen live-WS replay `run_info.json` fields, and
  checks paper-mode/restart-script invariants. It does not start processes,
  tune thresholds, or authorize deployment.
- Generated
  `backtest_outputs\btc15m_shadow_replay_config_audit_latest_codex`, created
  `2026-05-18T10:15:26Z`.
- Config-equivalence results:
  - q250 raw first-signal qty>=500:
    `19 / 19` checks passed, wrapper policy matches replay, paper mode locked,
    restart script includes target;
  - q250 YES-only first-signal qty>=500:
    `19 / 19` checks passed, wrapper policy matches replay, paper mode locked,
    restart script includes target;
  - q1000 YES:
    `19 / 19` checks passed, wrapper policy matches replay, paper mode locked,
    restart script includes target.
- Important interpretation: this only proves a future explicitly authorized
  paper start/restart would collect the intended frozen policies. It does not
  make old replay rows, stale-schema ledger rows, or non-running q250 YES-only
  rows deployable.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include `btc15m_shadow_replay_config_audit_` artifacts and prioritize
  `shadow_replay_config_summary.csv` / `shadow_replay_config_checks.csv`.
- Rebuilt sanitized GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260518_041545`.
- Validation:
  - `python scripts\build_btc15m_shadow_replay_config_audit.py --out-dir
    backtest_outputs\btc15m_shadow_replay_config_audit_latest_codex` passed;
  - `python -m py_compile scripts\build_btc15m_shadow_replay_config_audit.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc15m_shadow_config
    scripts.test_btc_paper_restart_safety -v` passed `12/12`, with the known
    logging ResourceWarnings.
- Current conclusion remains no deploy. The q250 YES-only forward path is
  now better operationally specified, but it still has `0` promotion-usable
  rows until an explicit paper-only start/restart is authorized and future
  official-settled rows accumulate with execution-realism fields.

## 2026-05-18 extended post-freeze replay and fresh GPT Pro review

- Continued read-only validation. No live deployment, canary, live trading,
  threshold retuning, process restart, archive, migration, or paper-shadow
  start was executed.
- Extended the frozen BTC15M live-WS post-freeze replays from freeze
  `2026-05-18T04:17:44Z` through the latest available capture at about
  `2026-05-18T10:18Z`:
  - q250 raw first-signal qty>=500:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_latest_codex`
    and REST fill
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex`;
  - q250 YES-only first-signal qty>=500:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_latest_codex`
    and REST fill
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex`;
  - q1000 YES:
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_latest_codex`
    and REST fill
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex`.
- Extended post-freeze REST-official results remained tiny:
  - q250 raw: `2` official rows, official PnL `0.00`, win rate `50%`,
    `0` proxy/official result mismatches;
  - q250 YES-only: `1` official row, official PnL `+0.50`, win rate `100%`,
    `0` proxy/official result mismatches;
  - q1000 YES: `1` official row, official PnL `+0.50`, win rate `100%`,
    `0` proxy/official result mismatches.
  This adds no deployable evidence; it mainly confirms sparse candidate
  collection under the frozen rules.
- Refreshed process state at `2026-05-18T10:22:21Z`: `4 / 5` targets were
  running. `btc15m_live_capture.py`, q250 raw, q1000 YES, and BTC1H
  high-conf80 entry70 no-chase were running. q250 YES-only was still not
  running.
- Refreshed shadow official settlement at `2026-05-18T10:22:18Z`:
  - q250 raw shadow: `2` official-filled BTC15M rows since freeze, official
    PnL `+0.04`;
  - q1000 YES shadow: `1` official-filled BTC15M row since freeze, official
    PnL `+0.52`;
  - q250 YES-only shadow: `0` rows because the DB is missing/not running;
  - BTC1H high-conf80 entry70 no-chase: `8` official-filled rows all time,
    official PnL `+0.44`; `5` since-window official rows have official PnL
    `-0.46` with `1` since-window proxy/official mismatch.
- Refreshed deployment readiness at `2026-05-18T10:22:35Z`.
  `production_ready_count = 0`. The readiness failure remains expected and
  correct.
- Refreshed forward consistency at `2026-05-18T10:22:35Z`.
  Consistency remains `0 / 4`:
  - q250 raw and q1000 YES have paper/replay count agreement, but not
    promotion evidence;
  - q250 YES-only is not running/status missing;
  - BTC1H has since-window negative official PnL and proxy/official mismatch.
- Submitted the rebuilt sanitized GPT Pro packet through Chrome/ChatGPT Pro:
  `gpt_pro_packets\strategy_advisor_20260518_042259`.
  Saved the response as
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_043034.md`.
- Fresh GPT Pro verdict matched the local gate:
  no live deployment, no canary, and no small-size exception. It ranked the
  research paths as:
  1. q250 YES-only first-signal qty>=500 as the best next experiment, but only
     after controlled paper-only start/restart and only future rows count;
  2. q1000 YES as the cleaner sparse control;
  3. q250 raw as side/basis decomposition only, not deployable;
  4. BTC1H as observe-only until clean-schema official forward rows exist.
- Refreshed restart-preflight and authorization artifacts at
  `2026-05-18T10:30Z`:
  - `backtest_outputs\btc_ledger_schema_preflight_latest_codex` still shows
    active q250 raw, q1000 YES, and BTC1H ledgers have old 25-column schemas
    with `0 / 12` execution-realism columns; q250 YES-only DB is missing;
  - `backtest_outputs\btc_shadow_restart_preflight_latest_codex` says all
    four paper-shadow restart/start paths pass schema/insert preflight;
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex` says the
    guarded paper-only restart/start path is ready for explicit user
    authorization, but did not execute it;
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex` remains
    `PENDING_CONTROLLED_RESTART` with `0` post-restart official rows.
- Refreshed
  `backtest_outputs\btc_gpt_pro_action_status_latest_codex` at
  `2026-05-18T10:31:20Z`. It points to the fresh GPT Pro review and still
  says `NO DEPLOY`. The only ready operational path is explicit user
  authorization for the guarded paper-only restart/start; that would collect
  evidence, not authorize live trading.
- After recording this ledger entry, rebuilt the sanitized GPT Pro packet again
  so the latest local handoff includes the new ledger state:
  `gpt_pro_packets\strategy_advisor_20260518_043234`.
- Validation:
  - `python -m py_compile` passed for the relied-on BTC15M/BTC1H official
    settlement, readiness, restart, action-status, replay/config-audit, and
    GPT packet scripts;
  - `python scripts\check_btc_deployment_readiness.py --out-dir
    backtest_outputs\deployment_readiness_latest_codex` returned the expected
    no-production-ready state.
- Current conclusion remains unchanged but sharper: no BTC15M or BTC1H strategy
  is deployable. The next useful step is not another threshold search; it is a
  user-authorized paper-only start/restart so q250 YES-only, q1000 YES, q250
  raw control, and BTC1H observe-only can begin collecting clean-schema,
  official-settled, execution-realistic forward rows under frozen rules.

## 2026-05-18 short refresh after GPT Pro review

- Continued read-only validation toward the same deployability goal. No live
  deployment, canary, paper execution, process stop/start/restart, archive, or
  threshold retuning was executed.
- Current local time check was `2026-05-18T04:35:40-06:00`
  (`2026-05-18T10:35:40Z`). Process refresh still found `4 / 5` expected
  Python targets running:
  - `btc15m_live_capture.py`, PID `1724`;
  - q250 raw paper shadow, PID `7052`;
  - q1000 YES paper shadow, PID `24840`;
  - BTC1H high-conf80 entry70 no-chase shadow, PID `16216`;
  - q250 YES-only remains not running.
- Refreshed shadow official settlement at `2026-05-18T10:35:46Z`.
  No new official-filled rows appeared since the prior refresh:
  - q250 raw shadow remains `2` official rows since freeze, official PnL
    `+0.04`;
  - q1000 YES remains `1` official row since freeze, official PnL `+0.52`;
  - q250 YES-only remains `0` rows / missing DB;
  - BTC1H remains `8` official rows all time, `5` since-window rows with
    official PnL `-0.46` and `1` since-window proxy/official mismatch.
- Refreshed active ledger schema preflight at `2026-05-18T10:35:40Z`.
  q250 raw, q1000 YES, and BTC1H ledgers still have stale 25-column schemas
  with `0 / 12` required execution-realism columns. q250 YES-only DB is still
  missing.
- Refreshed BTC15M signal starvation at `2026-05-18T10:36:17Z`:
  - q250 raw: `323437` post-freeze signal rows, `2` selected/order rows,
    top blocker `h02_ttl_outside`, TTL-outside share `75.71%`;
  - q1000 YES: `322618` post-freeze signal rows, `1` selected/order row,
    top blocker `h02_ttl_outside`, TTL-outside share `77.84%`;
  - q250 YES-only: `NOT_STARTED_MISSING_CAPTURE_DB`.
  This is still collection diagnostics, not promotion evidence.
- Refreshed basis and settlement diagnostics at `2026-05-18T10:36Z`.
  The pattern remains: q250/q1000 NO-side rows concentrate adverse
  proxy/official flips, YES-only rows are cleaner but sparse, and no
  deployable settlement-basis guard exists (`model_rows = 131`, too few
  samples/positives, no fresh preregistered guard evaluation).
- Refreshed deployment readiness at `2026-05-18T10:37:09Z`.
  `production_ready_count = 0` again, as expected.
- Refreshed forward evidence and consistency at `2026-05-18T10:37:09Z`.
  Forward consistency remains `0 / 4`:
  - q250 raw and q1000 YES have paper/replay count agreement but are not
    promotable because readiness, basis, sample-size, and stale-schema gates
    fail;
  - q250 YES-only is still not running/status missing;
  - BTC1H remains blocked by negative since-window official PnL and
    proxy/official mismatch.
- Ran the guarded paper-shadow restart script in dry-run mode only:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1`.
  It wrote
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_043744`
  and explicitly reported: "Dry run only. No processes were stopped or
  started." All four target scripts passed the dry-run script-safety check.
- Refreshed restart authorization and GPT Pro action-status artifacts after
  the dry run:
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`, created
    `2026-05-18T10:37:53Z`, says the paper-shadow restart path is ready for
    explicit user authorization but did not execute it;
  - `backtest_outputs\btc_post_restart_collection_gate_latest_codex`, created
    `2026-05-18T10:37:52Z`, remains `PENDING_CONTROLLED_RESTART` with `0`
    post-restart official rows;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T10:38:04Z`, still says `NO DEPLOY` and points at the latest
    dry-run plan.
- No new GPT Pro submission was warranted in this refresh. The fresh GPT Pro
  review from `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_043034.md`
  already answered the high-level strategy question, and the new evidence did
  not change the blocker: the system still needs explicit paper-only
  start/restart authorization before future rows can become promotion-usable.
- Validation:
  - `python -m py_compile` passed for the BTC15M/BTC1H official-settlement,
    readiness, restart, action-status, replay/config-audit, and GPT packet
    scripts relied on in this refresh;
  - readiness returned the expected no-production-ready state.
- Current conclusion remains no deploy. The shortest honest path toward a
  deployable strategy is still to begin a clean paper-only forward collection
  window, especially for q250 YES-only, while keeping q250 raw as basis-risk
  control, q1000 YES as sparse control, and BTC1H observe-only.

## 2026-05-18 resilient live-DuckDB read audit patch

- Continued read-only evidence-quality work. No live deployment, canary,
  process stop/start/restart, archive, migration, threshold retuning, or paper
  trade execution was performed.
- Problem found: `scripts\analyze_btc15m_shadow_signal_health.py` could mark a
  running BTC15M capture DB unreadable when Windows/duckdb denied a direct
  read-only open while the live writer had the file handle. This already made
  q250 raw health weaker than it should be in one refresh, even though
  `scripts\check_btc_forward_shadow_status.py` could read the same DB via a
  safe temporary snapshot-copy fallback.
- Patched `scripts\analyze_btc15m_shadow_signal_health.py`:
  - try direct `duckdb.connect(..., read_only=True)` first;
  - if that fails, copy the DB to a temporary directory and open the snapshot;
  - record `capture_read_source`, `read_error`, and
    `capture_snapshot_error` so future reports distinguish direct reads from
    recovered snapshot reads;
  - clean up temporary copies after the read.
- Applied the same resilience pattern to
  `scripts\build_btc15m_signal_starvation_report.py`, because it reads the same
  live paper-shadow DuckDB captures and should not turn transient file-lock
  timing into false evidence starvation/unreadability.
- Validation after patch:
  - `python -m py_compile scripts\analyze_btc15m_shadow_signal_health.py`
    passed;
  - `python -m py_compile scripts\build_btc15m_signal_starvation_report.py`
    passed;
  - `python scripts\analyze_btc15m_shadow_signal_health.py --out-dir
    backtest_outputs\btc15m_shadow_signal_health_latest_codex --since-utc
    2026-05-18T04:17:44Z` passed and now reads both running BTC15M shadows.
    q250 raw shows `327109` post-freeze signal rows, `2` selected rows,
    `2` order decisions, top detail family `h02_ttl_outside`, and
    q250 YES-only remains `missing_capture_db`;
  - `python scripts\build_btc15m_signal_starvation_report.py --out-dir
    backtest_outputs\btc15m_signal_starvation_latest_codex --since-utc
    2026-05-18T04:17:44Z` passed. q250 raw shows `328073` signal rows,
    `2` selected/order rows, TTL-outside share `75.9938%`; q1000 YES shows
    `327314` signal rows, `1` selected/order row, TTL-outside share
    `78.0987%`; q250 YES-only remains not started.
- Refreshed dependent artifacts after the patch:
  - `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`, created
    `2026-05-18T10:42:14Z`;
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T10:42:14Z`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T10:42:15Z`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T10:42:15Z`.
- Substantive verdict did not improve:
  - forward consistency remains `0 / 4`;
  - q250 raw and q1000 YES still have only `2` and `1` shadow official rows
    respectively since freeze, and both are stale-schema diagnostics only;
  - q250 YES-only is still the top next experiment but not running;
  - BTC1H remains observe-only with negative since-window official PnL and
    proxy/official mismatch;
  - action status remains `NO DEPLOY`.
- Current conclusion: this patch made the evidence layer more trustworthy but
  did not make any strategy deployable. The next actual deployability step is
  still explicit user authorization for the guarded paper-only restart/start,
  after which only future official-settled rows with complete execution-realism
  fields can count.

## 2026-05-18 extended post-freeze replay through 10:46 UTC

- Continued read-only BTC15M/BTC1H deployability work. No live deployment,
  canary, process stop/start/restart, archive, migration, threshold retuning, or
  paper-shadow execution was performed.
- Extended frozen BTC15M post-freeze live-websocket replays from freeze
  `2026-05-18T04:17:44Z` through the latest available BTC15M capture around
  `2026-05-18T10:46Z`, using official REST settlement fills after replay:
  - `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_latest_codex`
    and
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex`:
    q250 raw first-signal qty>=500 had `66` raw hits, `2` first signals, `2`
    official rows, official PnL `0.00`, win rate `50%`, and `0`
    proxy/official result mismatches;
  - `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_latest_codex`
    and
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex`:
    q250 YES-only first-signal qty>=500 had `60` raw hits, `1` first signal,
    `1` official row, official PnL `+0.50`, win rate `100%`, and `0`
    proxy/official result mismatches;
  - `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_latest_codex`
    and
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex`:
    q1000 YES had `60` raw hits, `1` first signal, `1` official row,
    official PnL `+0.50`, win rate `100%`, and `0` proxy/official result
    mismatches.
- Important interpretation: the replay extension from roughly `10:18Z` to
  `10:46Z` added no new candidate trades for the frozen policies. The tiny
  positive YES-only rows remain collection diagnostics only.
- Refreshed forward status and official-settlement artifacts after the replay
  extension:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T10:46:48Z`, found `4 / 5` monitored targets running. q250
    raw and q1000 YES remained running; q250 YES-only was still not running.
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T10:46:44Z`, found no new official-filled ledger rows. q250
    raw still had `2` official rows since freeze with official PnL `+0.04`;
    q1000 YES still had `1` official row with official PnL `+0.52`; BTC1H
    still had `5` since-window official rows with official PnL `-0.46` and a
    proxy/official mismatch.
  - `backtest_outputs\btc15m_frozen_opportunity_rate_latest_codex`, created
    `2026-05-18T10:46:40Z`, projected about `13.18` days to 100 rows at the
    q250 raw post-freeze replay rate and about `26.7` days for q250 YES-only
    or q1000 YES at the observed post-freeze replay rate.
  - `backtest_outputs\btc_official_settlement_feature_table_latest_codex`
    still had `0` promotion-usable rows.
- Refreshed conservative gate artifacts again at `2026-05-18T10:49Z`:
  - `backtest_outputs\deployment_readiness_latest_codex`:
    `production_ready_count = 0` with the expected no-production-ready exit
    state;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`: `NO DEPLOY`;
  - `backtest_outputs\btc_kill_continue_latest_codex`: broad q-families stay
    killed for deployment, q250 raw is continue-forward-only as a basis-risk
    control, q250 YES-only is still preregistered but needs explicit
    paper-start permission, q1000 YES is a sparse control, and BTC1H remains
    observe-only;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`: `0 / 4`
    candidates are consistent enough for promotion;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`: GPT Pro's
    `NO DEPLOY` recommendation remains blocked locally by readiness,
    post-restart collection, official-feature, basis, and stale-schema gates.
- Rebuilt the GPT Pro packet with the refreshed evidence at
  `gpt_pro_packets\strategy_advisor_20260518_044932`. No immediate second
  GPT Pro submission was warranted because the latest evidence did not change
  the strategic blocker already identified by
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_043034.md`: before
  promotion discussion, we need explicit paper-only restart/start authorization
  and future official-settled rows with complete execution-realism fields.
- Validation:
  - `python -m py_compile` passed for the BTC official-settlement, readiness,
    restart/preflight, signal-health/starvation, basis, consistency, and GPT
    packet scripts relied on in this refresh;
  - readiness returned the expected no-production-ready state.
- Current conclusion remains no deploy. The shortest honest path is not more
  threshold search on this same window; it is a controlled paper-only forward
  collection window, especially q250 YES-only, with q250 raw retained as a
  basis-risk control, q1000 YES as a sparse control, and BTC1H observe-only.

## 2026-05-18 10:55 UTC no-new-row continuation refresh

- Continued toward BTC15M/BTC1H deployability under the active goal. No live
  deployment, canary, live trading, paper-shadow execution, process
  stop/start/restart, archive, migration, or threshold retuning was performed.
- Re-checked process state at local `2026-05-18T04:52:29-06:00`.
  The same `4 / 5` Python targets were running:
  - `btc15m_live_capture.py`, PID `1724`;
  - q250 raw paper shadow, PID `7052`;
  - q1000 YES paper shadow, PID `24840`;
  - BTC1H high-conf80 entry70 no-chase shadow, PID `16216`;
  - q250 YES-only remained not running / missing capture DB.
- Refreshed shadow status and official-settlement artifacts at
  `2026-05-18T10:52Z`. No new official-filled shadow rows appeared:
  - q250 raw shadow remains `2` official rows since freeze, official PnL
    `+0.04`;
  - q1000 YES remains `1` official row since freeze, official PnL `+0.52`;
  - q250 YES-only remains `0` rows because it is not running;
  - BTC1H remains `8` official rows all time and `5` since-window official
    rows with official PnL `-0.46` and `1` since-window proxy/official
    mismatch.
- Refreshed active ledger schema, execution-realism, and post-restart gates:
  - active q250 raw, q1000 YES, and BTC1H ledgers still have old 25-column
    schemas with `0 / 12` required execution-realism fields;
  - q250 YES-only DB is still missing;
  - post-restart collection remains `PENDING_CONTROLLED_RESTART`, restart
    executed `False`, and `0` post-restart official rows.
- Refreshed restart preflight and authorization packet at
  `2026-05-18T10:53Z`. The guarded paper-only restart/start path still passes
  preflight for all four target shadows and is ready for explicit user
  authorization, but the packet did not execute anything.
- Refreshed BTC15M shadow signal-health/starvation diagnostics at
  `2026-05-18T10:53Z`:
  - q250 raw: `337811` post-freeze signal rows, `2` nonzero/selected rows,
    TTL-outside share `76.0629%`, stale BTC signal share `8.3251%`;
  - q1000 YES: `336956` post-freeze signal rows, `1` nonzero/selected row,
    TTL-outside share `78.0876%`, stale BTC signal share `8.4156%`;
  - q250 YES-only: `NOT_STARTED_MISSING_CAPTURE_DB`.
  This remains a collection feasibility diagnostic, not a search result and
  not permission to retune on this same live window.
- Extended frozen post-freeze BTC15M live-WS replay from freeze
  `2026-05-18T04:17:44Z` through capture end
  `2026-05-18T10:53:52.585420Z`, then REST-filled official settlement:
  - q250 raw first-signal qty>=500:
    `66` raw hits, `2` first signals, `2` official rows, official PnL `0.00`,
    win rate `50%`, `0` proxy/official mismatches;
  - q250 YES-only first-signal qty>=500:
    `60` raw hits, `1` first signal, `1` official row, official PnL `+0.50`,
    win rate `100%`, `0` proxy/official mismatches;
  - q1000 YES:
    `60` raw hits, `1` first signal, `1` official row, official PnL `+0.50`,
    win rate `100%`, `0` proxy/official mismatches.
  The replay extension from `10:46Z` to `10:53:52Z` added no new selected
  candidate rows.
- Refreshed opportunity-rate diagnostics at `2026-05-18T10:54Z`:
  - q250 raw projected about `13.48` days to reach 100 official post-freeze
    replay rows at the current post-freeze rate;
  - q250 YES-only and q1000 YES projected about `27.23` days to 100 official
    post-freeze replay rows at the current post-freeze rate.
  These projections are collection-planning diagnostics only.
- Refreshed settlement-basis model feasibility at `2026-05-18T10:54Z`.
  The basis guard remains diagnostic only: `model_rows = 131`, too few samples
  and positives, weak/insufficient OOF metrics, and no preregistered fresh
  forward guard evaluation.
- Refreshed Predexon official coverage at `2026-05-18T10:54Z`.
  Historical q250/q500/q1000-style rows remain partially REST-official covered,
  with uncovered proxy-only rows and bad uncovered windows blocking deployment
  use.
- Refreshed conservative gates at `2026-05-18T10:55Z`:
  - `backtest_outputs\deployment_readiness_latest_codex`:
    `production_ready_count = 0`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`: `NO DEPLOY`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`: `0 / 4`
    candidates consistent enough for promotion;
  - `backtest_outputs\btc_kill_continue_latest_codex`: broad q-families killed
    for deployment, q250 raw continue-forward-only as basis-risk control,
    q250 YES-only preregistered paper start pending explicit permission,
    q1000 YES sparse control, BTC1H observe-only;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`: GPT Pro's
    `NO DEPLOY` and explicit paper-only restart/start recommendation remains
    blocked locally until future official-settled, execution-realistic rows
    exist.
- Rebuilt the GPT Pro packet at
  `gpt_pro_packets\strategy_advisor_20260518_045510`. A new GPT Pro submission
  is not warranted yet because the fresh local evidence still matches the
  saved GPT Pro answer in
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_20260518_043034.md`: no
  deployment; collect clean post-restart paper-only evidence first.
- Validation:
  - `python -m py_compile` passed for the relied-on BTC official-settlement,
    readiness, restart/preflight, signal-health/starvation, basis, consistency,
    and GPT packet scripts;
  - readiness returned the expected no-production-ready state.
- Current conclusion remains no deploy. The only evidence-changing operational
  path now is explicit user authorization for the guarded paper-only
  restart/start. Without that, q250 YES-only cannot collect ledger evidence,
  and running q250 raw/q1000 YES/BTC1H shadows remain stale-schema diagnostics.

## 2026-05-18 BTC1H capture auditability sidecar patch

- Continued toward BTC15M/BTC1H deployability without live deployment,
  canary, live trading, paper-shadow execution, process stop/start/restart,
  archive, migration, or threshold retuning.
- Problem investigated: the latest forward-status/evidence reports still could
  not inspect the running BTC1H high-conf80 entry70 no-chase capture DuckDB
  while PID `16216` held the file. `check_btc_forward_shadow_status.py` already
  had a snapshot-copy fallback, but both direct DuckDB read and ordinary/shared
  file copy failed on Windows for
  `~\.btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb`.
  The capture also had a live `.duckdb.wal` sidecar.
- Implemented a future-facing lock-free audit sidecar in
  `scripts\btc_1hr_research_live.py`:
  - `LiveCaptureWriter` now writes
    `<capture_db>.status.json` with capture DB path, PID, queue health, dropped
    counts, rows-by-table, latest received timestamps by table, and
    signal-scan summary fields;
  - the sidecar is seeded from the capture DB when the writer starts and is
    refreshed after flushes and on writer close/failure;
  - this does not alter strategy thresholds or trading behavior. It only makes
    future restarted BTC1H capture writers auditable when DuckDB itself is
    locked to other processes.
- Updated `scripts\check_btc_forward_shadow_status.py`:
  - if direct read and snapshot copy both fail, it now falls back to
    `<capture_db>.status.json` when present;
  - the fallback populates capture-health rows/latest, top-book rows/latest,
    signal-scan rows/latest/action/detail/nonzero-candidate rows, and order
    decision rows;
  - JSON sidecar loading accepts `utf-8-sig` so PowerShell-created test
    artifacts with a BOM do not break diagnostics.
- Added regression coverage in
  `scripts\test_btc_forward_shadow_status.py` for sidecar parsing and the
  missing/unreadable DB fallback path.
- Validation:
  - `python -m py_compile scripts\btc_1hr_research_live.py
    scripts\check_btc_forward_shadow_status.py
    scripts\test_btc_forward_shadow_status.py` passed;
  - a manual sidecar smoke test printed
    `sidecar_after_live_lock 7 1 ttl err=`;
  - `python -m unittest scripts.test_btc_forward_shadow_status
    scripts.test_btc_paper_restart_safety
    scripts.test_btc_post_restart_verification -v` passed `8 / 8`;
  - `python scripts\build_btc_shadow_restart_preflight.py --out-dir
    backtest_outputs\btc_shadow_restart_preflight_latest_codex` still passed
    all four target restart paths.
- Important limitation: the currently running BTC1H shadow was started before
  this patch, so it still has no sidecar and remains unreadable while its live
  DuckDB is exclusively locked. The improvement becomes active only after an
  explicit user-authorized paper-only restart/start.
- Refreshed conservative artifacts after the patch:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T11:01:16Z`, still found `4 / 5` targets running and q250
    YES-only not running;
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T11:02:17Z`, still had `production_ready_count = 0`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T11:02:17Z`, remained `NO DEPLOY`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T11:02:17Z`, remained `0 / 4`;
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`, created
    `2026-05-18T11:02:19Z`, says the guarded paper-only restart/start path is
    ready for explicit user authorization and points at dry-run plan
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_050154`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, rerun
    sequentially at `2026-05-18T11:02:44Z`, now points at that same latest
    dry-run plan and remains `NO DEPLOY`.
- Rebuilt the GPT Pro packet after the corrected action-status refresh:
  `gpt_pro_packets\strategy_advisor_20260518_050250`.
- No new GPT Pro submission was warranted. This was an audit-infrastructure
  patch and did not change the strategy evidence or local blocker state.
- Current conclusion remains no deploy. The repo is now better prepared for a
  clean BTC1H paper evidence clock after explicit restart authorization, but no
  BTC15M or BTC1H strategy has deployable evidence yet.

## 2026-05-18 post-restart verifier sidecar gate

- Continued evidence-infrastructure work toward BTC15M/BTC1H deployability.
  No live deployment, canary, live trading, paper-shadow execution, process
  stop/start/restart, archive, migration, or threshold retuning was performed.
- Extended the BTC1H sidecar auditability patch into the post-restart verifier:
  - `scripts\check_btc_forward_shadow_status.py` now exposes
    `capture_sidecar_path`, `capture_sidecar_mtime_utc`,
    `capture_sidecar_updated_at_utc`, and `capture_sidecar_error` whenever a
    sidecar is present, even if the capture DB itself is directly readable;
  - `scripts\build_btc_post_restart_verification.py` now marks the BTC1H
    high-conf80 entry70 no-chase shadow as requiring a capture sidecar after
    restart;
  - the post-restart verifier now emits a
    `required_capture_sidecars_ready` checklist row and per-target fields
    `capture_sidecar_required`, `capture_sidecar_ready`,
    `capture_sidecar_updated_at_utc`, and `capture_sidecar_error`;
  - `evidence_clock_ready` now requires the BTC1H sidecar gate in addition to
    restart execution, BTC15M capture continuity, target process liveness, and
    active ledger schema readiness.
- Refreshed `backtest_outputs\btc_post_restart_verification_latest_codex` after
  rerunning `check_btc_forward_shadow_status.py`:
  - restart executed remains `False`;
  - evidence clock ready remains `False`;
  - q250 YES-only is still not running;
  - BTC1H sidecar required is `True` and sidecar ready is `False`, expected
    because the current BTC1H process predates the sidecar writer patch;
  - active ledger schemas still fail with `0 / 12` execution-realism fields.
- Refreshed conservative artifacts:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T11:06:14Z`, still found `4 / 5` targets running;
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T11:06:08Z`, still had `production_ready_count = 0`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`, created
    `2026-05-18T11:06:24Z`, remained `NO DEPLOY`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T11:06:38Z`, still says the only ready operational path is
    explicit user authorization for the guarded paper-only restart/start.
- Validation:
  - `python -m py_compile scripts\btc_1hr_research_live.py
    scripts\check_btc_forward_shadow_status.py
    scripts\build_btc_post_restart_verification.py
    scripts\test_btc_forward_shadow_status.py
    scripts\test_btc_post_restart_verification.py` passed;
  - `python -m unittest scripts.test_btc_forward_shadow_status
    scripts.test_btc_post_restart_verification
    scripts.test_btc_paper_restart_safety -v` passed `8 / 8`;
  - readiness returned the expected no-production-ready state.
- Rebuilt the GPT Pro packet at
  `gpt_pro_packets\strategy_advisor_20260518_050646`.
- No new GPT Pro submission was warranted. This patch clarifies the future
  evidence-clock gate but does not change the strategy ranking or deployment
  evidence: no BTC15M or BTC1H strategy is deployable yet.

## 2026-05-18 paper-vs-replay row reconciliation gate

- Added a row-level forward reconciliation control artifact:
  `scripts\build_btc_forward_row_reconciliation.py`.
  This is a deployment-control check, not a strategy search. It reconciles
  paper-shadow official rows against causal live-WS replay rows by market,
  side, occurrence, entry price, official result, actual-fee PnL, and
  decision-time proximity. It also reports the 2c stressed replay PnL delta
  separately so stressed replay accounting is not confused with paper ledger
  actual-fee accounting.
- Added regression coverage in
  `scripts\test_btc_forward_row_reconciliation.py` for:
  - a matching paper/replay row where actual-fee PnL agrees but the extra 2c
    stress layer differs as expected;
  - a replay-only q250 YES row with no fresh paper-shadow ledger row, which
    must block promotion evidence.
- Refreshed
  `backtest_outputs\btc_forward_row_reconciliation_latest_codex`, created
  `2026-05-18T11:16:58Z`:
  - `q250_firstskip_qty500`: 2 paper rows, 2 replay rows, 2 matched rows,
    actual-fee official PnL reconciles at `+0.04`, but promotion evidence is
    blocked by missing paper ledger execution fields and only 2 official rows;
  - `q1000_yes`: 1 paper row, 1 replay row, 1 matched row, actual-fee official
    PnL reconciles at `+0.52`, but promotion evidence is blocked by missing
    paper ledger execution fields and only 1 official row;
  - `q250_firstskip_qty500_yes`: 0 paper rows, 1 replay row, so the fresh
    YES-only candidate remains a preregistered paper-start path only, not
    evidence;
  - `btc1h_high_conf80_entry70_no_chase`: no causal replay comparator is
    available for the current since-freeze paper rows.
- Validation:
  - `python -m py_compile scripts\build_btc_forward_row_reconciliation.py
    scripts\test_btc_forward_row_reconciliation.py
    scripts\build_gpt_pro_strategy_packet.py` passed using a temporary
    repo-local `PYTHONPYCACHEPREFIX` because the default Windows pycache path
    hit a transient permission error;
  - `python -m unittest scripts.test_btc_forward_row_reconciliation -v`
    passed `2 / 2`.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future Pro packets
  include the row-reconciliation artifact and summary/details CSVs.
- Rebuilt the current GPT Pro packet after this update:
  `gpt_pro_packets\strategy_advisor_20260518_051901`.
- No live deployment, canary, restart/start/stop, paper-shadow authorization,
  threshold retuning, or new GPT Pro submission was performed. This closes one
  auditability gap but does not change the deployment verdict: no BTC15M or
  BTC1H strategy is deployable yet.

## 2026-05-18 row-reconciliation gate wiring

- Promoted the new paper-vs-live-replay row reconciliation from a standalone
  diagnostic into the conservative gate stack. No live deployment, canary,
  process stop/start/restart, paper-shadow authorization, migration, or
  threshold retuning was performed.
- Updated `scripts\check_btc_deployment_readiness.py`:
  - discovers latest `btc_forward_row_reconciliation_*`;
  - adds row-reconciliation status/pass/promotion-usable/matched-row fields to
    focused candidate readiness rows;
  - forces production readiness false when per-row reconciliation is missing,
    not passing, or not promotion-usable.
- Updated `scripts\build_btc_forward_consistency_audit.py`:
  - reads `btc_forward_row_reconciliation_latest_codex`;
  - includes row-reconciliation fields in `forward_consistency_summary.csv`;
  - adds explicit blockers such as
    `paper_replay_row_reconciliation_not_promotion_usable` and
    `row_reconciliation_no_paper_shadow_rows_since_freeze`.
- Updated `scripts\build_btc_gpt_pro_action_status.py`:
  - adds a `paper_replay_row_reconciliation` checklist row;
  - exposes candidate-level row reconciliation status/blockers;
  - keeps `deployable_now` false unless the row reconciliation is
    promotion-usable.
- Added regression coverage in
  `scripts\test_btc_readiness_row_reconciliation.py` proving that a focused
  candidate with matching paper/replay rows is still blocked when the row is
  not promotion-usable.
- Validation:
  - `python -m py_compile scripts\check_btc_deployment_readiness.py
    scripts\build_btc_gpt_pro_action_status.py
    scripts\build_btc_forward_consistency_audit.py
    scripts\test_btc_readiness_row_reconciliation.py
    scripts\test_btc_forward_row_reconciliation.py
    scripts\test_btc_forward_replay_realism.py` passed using a temporary
    repo-local `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_forward_replay_realism -v` passed `5 / 5`.
- Refreshed artifacts after wiring:
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T11:24:18Z`, still has `production_ready_count = 0` and now
    points at `row_reconciliation_dir =
    backtest_outputs\btc_forward_row_reconciliation_latest_codex`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T11:24:52Z`, remains `0 / 4` consistent enough for promotion
    and now lists row-reconciliation blockers for every focused candidate;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T11:24:54Z`, includes
    `paper_replay_row_reconciliation = BLOCKS_DEPLOYMENT` with
    `row_reconciliation_pass=2` but `promotion_usable=0`;
  - `backtest_outputs\btc_kill_continue_latest_codex`, created
    `2026-05-18T11:24:50Z`, remains `NO DEPLOY` / research-control only;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_052708`.
- Current evidence:
  - q250 raw and q1000 YES have row-level paper/replay matches, but neither is
    promotion-usable because active paper ledgers still lack execution-realism
    fields and samples are only 2 and 1 official rows respectively;
  - q250 YES-only still has replay-only evidence but no paper-shadow row;
  - BTC1H still has no causal replay comparator and remains observe-only.
- Final read-only refresh after the gate wiring:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T11:26:34Z`, still shows `4 / 5` targets running and q250
    YES-only not running;
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T11:26:37Z`, still has q250 raw 2 official rows `+0.04`,
    q1000 YES 1 official row `+0.52`, q250 YES-only 0 rows, and BTC1H
    since-window official PnL `-0.46` with 1 proxy/official mismatch;
  - `backtest_outputs\btc_forward_row_reconciliation_latest_codex`, created
    `2026-05-18T11:26:38Z`, still has `row_reconciliation_pass=2` but
    `promotion_usable=0`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T11:26:45Z`, keeps `NO DEPLOY`.
- Conclusion remains unchanged and sharper: aggregate replay/paper count
  agreement is no longer enough anywhere in the gate stack. No BTC15M or BTC1H
  strategy is deployable.

## 2026-05-18 BTC1H replay coverage audit

- Added `scripts\build_btc1h_replay_coverage_audit.py`, a read-only coverage
  diagnostic for BTC1H paper-shadow rows. It does not replay, tune, trade, or
  promote. It checks whether readable local capture DBs contain both
  `ws_orderbook_top` and `signal_scan` rows around each BTC1H paper fill, which
  is the prerequisite for a causal BTC1H paper-vs-replay comparator.
- Added `scripts\test_btc1h_replay_coverage_audit.py` covering:
  - a row with readable top-of-book and signal-scan coverage;
  - a missing-capture row that must not be considered replayable.
- Ran the audit at
  `backtest_outputs\btc1h_replay_coverage_audit_latest_codex`, created
  `2026-05-18T11:35:57Z`:
  - all BTC1H official paper rows: 8 total, 3 replayable from readable capture,
    5 blocked by the locked active BTC1H shadow capture DB;
  - since-freeze rows: 3 total, 0 replayable, 3 blocked by the locked active
    BTC1H shadow capture DB;
  - replayable older rows come from `research_live_capture`;
  - since-freeze rows require a readable sidecar, snapshot, or explicit
    user-authorized restart/start before row-level BTC1H replay reconciliation
    can become promotion-grade.
- Updated `scripts\build_btc_gpt_pro_action_status.py`:
  - added a `btc1h_replay_coverage` checklist row;
  - latest action status now reports
    `since_replayable_rows=0/3`, `locked_blocked_rows=3`, and
    `coverage_gate_pass=False`.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future GPT Pro packets
  include the BTC1H replay coverage report and CSVs.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_coverage_audit.py
    scripts\test_btc1h_replay_coverage_audit.py
    scripts\build_btc_gpt_pro_action_status.py
    scripts\build_gpt_pro_strategy_packet.py` passed using temporary
    repo-local `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc1h_replay_coverage_audit -v` passed
    `2 / 2`.
- Refreshed `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
  `2026-05-18T11:37:06Z`, and rebuilt GPT Pro packet
  `gpt_pro_packets\strategy_advisor_20260518_053742`.
- No live deployment, canary, process stop/start/restart, migration,
  paper-shadow authorization, or threshold retuning was performed. BTC1H
  remains observe-only and is now blocked on a precise replay-coverage gate in
  addition to negative since-window official PnL, proxy/official mismatch, and
  stale/missing ledger execution fields.

## 2026-05-18 fresh GPT Pro loop and drawdown sequence gate

- Built and submitted a fresh GPT Pro strategy packet through the Chrome
  ChatGPT Pro session:
  `gpt_pro_packets\strategy_advisor_20260518_054359`.
  The response was saved at
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_fresh_chrome_20260518_054359.md`.
- GPT Pro's refreshed verdict matched the local gates:
  - nothing is deployable now: not BTC15M, not BTC1H, not a canary, and not a
    small-size exception;
  - top path remains BTC15M q250 YES-only first-signal qty>=500, but it has
    0 paper rows and is not running;
  - q1000 YES remains a sparse cleaner YES control;
  - q250 raw remains a side/basis decomposition control, not a deployable
    strategy;
  - BTC1H remains observe-only because since-window official PnL is negative,
    proxy/official settlement disagrees, and since-freeze replay coverage is
    blocked.
- Refreshed local state after the Pro packet:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`, created
    `2026-05-18T11:43:03Z`, still shows 4/5 targets running:
    BTC15M capture, q250 raw shadow, q1000 YES shadow, and BTC1H shadow.
    q250 YES-only is still not running.
  - `backtest_outputs\btc_shadow_official_settlement_latest_codex`, created
    `2026-05-18T11:43:00Z`, has q250 raw 2 official rows `+0.04`,
    q1000 YES 1 official row `+0.52`, q250 YES-only 0 rows, and BTC1H
    since-window official PnL `-0.46` with 1 proxy/official mismatch. A newer
    BTC1H row was active/unsettled and did not improve deployability.
  - `backtest_outputs\deployment_readiness_latest_codex`, created
    `2026-05-18T11:43:47Z`, still has `production_ready_count = 0`.
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`, created
    `2026-05-18T11:43:47Z`, remains `0 / 4` consistent enough for promotion.
- Added `scripts\build_btc_drawdown_sequence_audit.py`, a read-only official
  PnL path-quality artifact. It emits:
  - `drawdown_sequence_summary.csv`;
  - `drawdown_sequence_details.csv`;
  - row-level official PnL, cumulative PnL, running peak, and drawdown.
  This closes the Pro-identified gap where summary PnL could hide an ugly
  return path.
- Added `scripts\test_btc_drawdown_sequence_audit.py` covering:
  - cumulative/peak/drawdown sequencing in timestamp order;
  - blocking the promotion window when a controlled restart has not executed.
- Refreshed
  `backtest_outputs\btc_drawdown_sequence_audit_latest_codex`, created
  `2026-05-18T11:59:31Z`:
  - all post-restart promotion windows are `PENDING_CONTROLLED_RESTART`;
  - q250 raw diagnostic rows remain only 2 official rows, `+0.04` PnL;
  - q1000 YES diagnostic rows remain only 1 official row, `+0.52` PnL;
  - q250 YES-only has 0 official rows;
  - BTC1H all-current diagnostic official PnL is `+0.44`, but max drawdown is
    `-1.08` with 2 proxy/official mismatches, so summary PnL is misleading;
  - BTC1H since-freeze diagnostic official PnL is `-1.08`, max drawdown
    `-0.67`, with 1 proxy/official mismatch.
- Updated `scripts\build_btc_gpt_pro_action_status.py`:
  - added `official_drawdown_sequence_gate = BLOCKS_DEPLOYMENT`;
  - latest action status
    `backtest_outputs\btc_gpt_pro_action_status_latest_codex`, created
    `2026-05-18T11:59:41Z`, now reports
    `post_restart_drawdown_gate_pass=0` and all post-restart drawdown scopes as
    `PENDING_CONTROLLED_RESTART`.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future Pro packets
  include drawdown sequence summary/details; rebuilt packets at
  `gpt_pro_packets\strategy_advisor_20260518_055940` and, after this ledger
  entry, `gpt_pro_packets\strategy_advisor_20260518_060032`.
- Updated the reusable `kalshi-btc-strategy-research` skill and its
  deployability reference so future runs include the drawdown sequence audit by
  default.
- Validation:
  - Python compile passed for the new drawdown audit/test plus updated
    action-status and packet scripts using repo-local `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc_paper_restart_safety -v` passed `11 / 11`.
- No live deployment, canary, live trading, process stop/start/restart,
  archive, migration, paper-shadow authorization, or threshold retuning was
  performed. The only operational path now ready is still explicit user
  authorization for the guarded paper-only restart/start:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1 -Execute -IUnderstandThisRestartsPaperShadows`.

## 2026-05-18 sequential evidence-stack refresh controller

- Current-process audit before this work still found exactly four matching
  Python processes:
  - BTC15M capture: `scripts\btc15m_live_capture.py`, PID 1724;
  - BTC15M q250 raw paper shadow:
    `scripts\btc15m_f2_q250_qty500_firstskip_shadow.py`, PID 7052;
  - BTC15M q1000 YES paper shadow:
    `scripts\btc15m_f2_q1000_yes_shadow.py`, PID 24840;
  - BTC1H high-conf80 entry70 no-chase paper shadow:
    `scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID 16216.
  q250 YES-only is still not running.
- A parallel manual refresh exposed an evidence-hygiene race: the BTC1H row
  `KXBTCD-26MAY1808-T77399.99` became REST-official/determined, but
  `build_btc_execution_realism_audit.py` read
  `shadow_official_trades.csv` before
  `check_btc_shadow_official_settlement.py` finished rewriting it. That made
  execution-realism show the row as pending while the official-settlement
  summary counted it as official.
- Added `scripts\refresh_btc_evidence_stack.py`, a read-only sequential
  controller that runs dependent deployability artifacts in dependency order.
  It explicitly runs `check_btc_shadow_official_settlement.py` before
  execution-realism, post-restart collection, drawdown sequence,
  row-reconciliation, official feature table, readiness, consistency, and GPT
  Pro action-status builders.
- Added `scripts\test_btc_evidence_stack_refresh.py` covering:
  - official settlement runs before every dependent artifact;
  - readiness exit code `1` is expected only for the readiness step;
  - the controller uses the current Python executable and can skip packet
    rebuilds when requested.
- Ran the sequential controller:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - 18/18 steps passed;
  - readiness returned exit code `1`, accepted as the expected
    no-production-ready state;
  - the run info says this was a sequential read-only refresh and did not
    start, stop, restart, archive, migrate, trade, deploy, or tune thresholds.
- The race is now gone in latest artifacts:
  - `backtest_outputs\btc_execution_realism_audit_latest_codex` now shows
    BTC1H rows `9`, official rows `9`, official PnL `+0.72`, and pending
    official rows `0`;
  - BTC1H still fails execution-realism for the real blocker:
    missing ledger execution fields, quote age/top visible quantity, and
    side-ask reconciliation fields.
- Latest official settlement after ordered refresh:
  - q250 raw: 2 official rows, official PnL `+0.04`;
  - q1000 YES: 1 official row, official PnL `+0.52`;
  - q250 YES-only: 0 official rows and still not running;
  - BTC1H: 9 official rows all-time, official PnL `+0.72`; since window has
    6 official rows, official PnL `-0.18`, and still includes a proxy/official
    mismatch.
- Latest forward consistency remains `0 / 4`:
  - q250 raw and q1000 YES have paper/replay count agreement but are not
    promotion-usable;
  - q250 YES-only is not running and has no paper-shadow rows;
  - BTC1H is blocked by proxy/official mismatch and no causal replay
    comparator for since-freeze rows.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so future Pro packets
  include the sequential refresh artifact.
  Latest packet:
  `gpt_pro_packets\strategy_advisor_20260518_060724`.
- Updated the reusable `kalshi-btc-strategy-research` skill and its
  deployability reference to prefer the sequential refresh controller and to
  warn against parallel refreshes of artifacts that depend on REST-official
  settlement output.
- Validation:
  - Python compile passed for `scripts\refresh_btc_evidence_stack.py`,
    `scripts\test_btc_evidence_stack_refresh.py`, and
    `scripts\build_gpt_pro_strategy_packet.py`;
  - `python -m unittest scripts.test_btc_evidence_stack_refresh
    scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc_paper_restart_safety -v` passed `14 / 14`.
- Deployment conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable; no live deployment, canary, process stop/start/restart, archive,
  migration, paper-shadow authorization, or threshold retuning was performed.

## 2026-05-18 expanded evidence-stack refresh and current BTC15M/BTC1H state

- Extended `scripts\refresh_btc_evidence_stack.py` from the original core
  refresh into the full ordered evidence refresh. It now also refreshes BTC15M
  shadow signal health, signal starvation, frozen opportunity rate, the next
  forward candidate packet, Predexon official-coverage diagnostics, BTC1H replay
  coverage, and post-restart verification before rebuilding the GPT Pro packet.
- Added a narrow output-directory cleanup to the controller so reused
  `*_latest_codex` folders do not retain stale numbered logs from older,
  shorter step lists. The cleanup removes only this controller's prior
  `NN_*.log`, `refresh_steps.json`, `run_info.json`, and `report.md` files.
- Added/updated regression coverage in `scripts\test_btc_evidence_stack_refresh.py`:
  - collection diagnostics run before summaries and packet generation;
  - dependent artifacts still run after REST-official settlement;
  - reused output directories keep unrelated evidence files while removing old
    controller metadata/logs.
- Updated the reusable `kalshi-btc-strategy-research` skill and its
  deployability reference so future sessions prefer the expanded controller,
  preserve the 25-step manual dependency order when needed, and remember the
  current q250 YES-only / q250 raw / q1000 YES / BTC1H roles.
- Reran the expanded controller:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T12:25:26Z` to `2026-05-18T12:30:24Z`;
  - 25/25 steps passed;
  - readiness returned exit code `1`, accepted as the expected
    no-production-ready state;
  - latest packet rebuilt at `gpt_pro_packets\strategy_advisor_20260518_063021`;
  - the output folder now contains only the current 25 numbered step logs plus
    current metadata/report files.
- Latest process audit still found exactly four matching Python processes and
  no q250 YES-only process:
  - BTC15M capture `scripts\btc15m_live_capture.py`, PID 1724;
  - BTC15M q250 raw paper shadow
    `scripts\btc15m_f2_q250_qty500_firstskip_shadow.py`, PID 7052;
  - BTC15M q1000 YES paper shadow
    `scripts\btc15m_f2_q1000_yes_shadow.py`, PID 24840;
  - BTC1H high-conf80 entry70 no-chase paper shadow
    `scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`, PID 16216.
- Latest GPT Pro action-status gates remain hard-blocked:
  - readiness production-ready count: `0`;
  - forward consistency count: `0`;
  - row reconciliation has `2` passing matches but `0` promotion-usable rows;
  - canonical official feature table has `0` promotion-usable rows;
  - post-restart collection and drawdown gates are
    `PENDING_CONTROLLED_RESTART`;
  - deployable settlement-basis guards: `0`;
  - BTC1H since-freeze replay coverage: `0 / 4` replayable rows, all 4 blocked
    by locked capture coverage.
- BTC15M collection diagnostics:
  - q250 raw had `405,051` signal rows since the freeze but only `2` selected
    rows; it is running, but its active ledger schema still lacks execution
    realism fields, so evidence is not promotion-usable.
  - q1000 YES had `405,174` signal rows since the freeze but only `1` selected
    row; it is also running with stale execution-realism schema.
  - q250 YES-only remains the top next forward candidate, but its capture DB is
    missing, it is not running, and it has `0` paper-shadow rows.
  - The dominant BTC15M non-trade reason is still TTL outside the 10-12 minute
    window, around `76.8%` for q250 raw and `78.4%` for q1000 YES.
- BTC15M frozen opportunity diagnostics:
  - q250 raw post-freeze replay has `2` official rows, `0.0` 2c-stressed PnL,
    and `50%` win rate, but remains `not_usable_ledger_schema_stale`;
  - q1000 YES post-freeze replay has `1` official row, `+0.50` 2c-stressed PnL,
    and `100%` win rate, but remains `not_usable_ledger_schema_stale`;
  - q250 YES-only post-freeze replay has `1` diagnostic official row with
    `+0.50`, but because the shadow is not running this row is replay-only and
    cannot count.
- Predexon official-coverage diagnostics remain research-only:
  - q250 firstskip qty>=500 has `57 / 63` REST-official rows and official PnL
    `+8.72`, but is blocked by partial official coverage, uncovered proxy-only
    rows, negative uncovered proxy PnL, and bad uncovered windows;
  - q1000 YES has `23 / 33` REST-official rows and official PnL `+6.45`, but is
    blocked by too few REST-official Predexon rows, partial coverage, proxy-only
    uncovered rows, and bad uncovered windows.
- BTC1H remains observe-only:
  - official settlement summary has `9` official rows all-time with PnL `+0.72`;
  - since-window BTC1H has `6` official rows with PnL `-0.18` and one
    proxy/official mismatch;
  - replay coverage has `3 / 9` replayable rows all-time and `0 / 4` replayable
    rows since-freeze because the relevant active capture rows are locked.
- Post-restart verification remains pending:
  - no authorized controlled restart/start has executed;
  - q250 raw, q1000 YES, and BTC1H active ledgers still fail the deployable
    schema check for missing execution-realism columns;
  - q250 YES-only is missing its DB and is not running;
  - the guarded restart/start path is ready for explicit user authorization, but
    this ledger entry and the current goal continuation do not authorize it.
- Validation:
  - Python compile passed for `scripts\refresh_btc_evidence_stack.py`,
    `scripts\test_btc_evidence_stack_refresh.py`,
    `scripts\build_gpt_pro_strategy_packet.py`,
    `scripts\build_btc_gpt_pro_action_status.py`,
    `scripts\build_btc_drawdown_sequence_audit.py`, and
    `scripts\test_btc_drawdown_sequence_audit.py` using repo-local
    `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc_evidence_stack_refresh
    scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc_paper_restart_safety -v` passed `16 / 16`.
- Deployment conclusion: still no deployable BTC15M or BTC1H strategy. GPT Pro
  does not need another submission for this refresh because the expanded
  evidence stack confirms the same high-level plan: prepare q250 YES-only as the
  top paper-only forward path only after explicit controlled start/restart
  authorization, keep q1000 YES as a sparse control, keep q250 raw as a
  basis/side diagnostic, and leave BTC1H observe-only until replay coverage and
  official-settlement behavior improve.

## 2026-05-18 wrapper hardening for frozen BTC15M forward paths

- Re-checked process state before this work. The same four Python processes were
  running: BTC15M capture, q250 raw paper shadow, q1000 YES paper shadow, and
  BTC1H high-conf80 entry70 no-chase paper shadow. q250 YES-only was still not
  running. No process was stopped, started, restarted, archived, migrated, or
  deployed.
- Reviewed the REST-official Predexon fill/grid artifacts:
  - `backtest_outputs\btc15m_predexon_rest_official_latest_codex` already
    contains a REST fill for the combined Predexon materialized rows, with
    `374` REST-official tickers from `880` unique tickers.
  - `backtest_outputs\btc15m_materialized_filter_grid_pred_official_latest_codex`
    still ranks q250 first-signal qty>=500 as research-positive on the
    REST-official subset: `57` Predexon official rows, `+8.72` PnL, `64.9%`
    win rate, max drawdown `-1.97`, Sharpe `2.33`; old live official replay
    has `17` rows, `+4.30`, `76.5%` win rate.
  - This does not change deployability because official coverage is partial,
    old live replay was seen before the current freeze, and no future
    promotion-grade q250 YES-only paper rows exist.
- Found and fixed a wrapper exactness risk: focused BTC15M frozen wrappers used
  `os.environ.setdefault(...)`, so a polluted parent shell could silently
  override preregistered settings. This is unacceptable for future paper-shadow
  evidence clocks because the row could be collected under a different policy.
- Hardened the focused paper wrappers to hard-set their frozen environment
  values before launching `btc15m_lowdd_live.py`:
  - `scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py`
  - `scripts\btc15m_f2_q250_qty500_firstskip_shadow.py`
  - `scripts\btc15m_f2_q1000_yes_shadow.py`
- Added regression coverage in `scripts\test_btc15m_shadow_config.py` proving
  the q250 YES-only wrapper overrides polluted environment variables and still
  launches `btc15m_lowdd_live.py` in paper mode with strategy `h02`, allowed
  side `yes`, base visible quantity `250`, first-signal visible quantity `500`,
  entry max `0.50`, and one-contract sizing.
- Reran the full ordered evidence refresh after the wrapper hardening:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T12:40:22Z` to `2026-05-18T12:47:40Z`;
  - 25/25 steps passed;
  - readiness returned exit code `1`, still expected for no production-ready
    candidates;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_064736`.
- Latest gate state after hardening remains:
  - readiness production-ready count `0`;
  - forward consistency count `0`;
  - row reconciliation `2` passing matches but `0` promotion-usable rows;
  - canonical official feature table `0` promotion-usable rows;
  - post-restart collection and drawdown gates pending controlled restart;
  - settlement-basis deployable guards `0`;
  - BTC1H since-freeze replay coverage `0 / 4`.
- Latest restart authorization packet still says all four paper-shadow targets
  are ready for explicit user authorization, including q250 YES-only as
  `READY_FOR_USER_AUTHORIZATION_TO_START`, but it did not execute anything and
  still requires explicit user permission.
- Validation:
  - Python compile passed for the hardened wrappers, wrapper tests, evidence
    refresh controller, evidence refresh tests, GPT Pro packet builder, and GPT
    Pro action-status builder using repo-local `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc15m_shadow_config
    scripts.test_btc_evidence_stack_refresh
    scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc_paper_restart_safety -v` passed `25 / 25`.
- Deployment conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable. This work only makes the future q250 YES-only / q250 raw / q1000
  YES paper-shadow evidence clock harder to contaminate once an explicit
  controlled paper-only start/restart is authorized.

## 2026-05-18 BTC1H replay coverage snapshot fallback audit

- Re-checked process state before this work. The same four Python processes were
  running: BTC15M capture, q250 raw paper shadow, q1000 YES paper shadow, and
  BTC1H high-conf80 entry70 no-chase paper shadow. q250 YES-only was still not
  running. No process was stopped, started, restarted, archived, migrated, or
  deployed.
- The current GPT Pro action checklist still had BTC1H blocked by replay
  coverage: since-freeze replayable rows `0 / 4`, all blocked by locked active
  capture coverage. After a new BTC1H paper row settled during this work, the
  blocker became `0 / 5`.
- Added a conservative snapshot fallback to
  `scripts\build_btc1h_replay_coverage_audit.py`:
  - first try live DuckDB read-only access;
  - if read-only access fails, copy the `.duckdb` file and matching `.wal`
    into a temporary directory and probe the copied snapshot;
  - annotate successful snapshot probes with
    `best_capture_read_source = snapshot_copy_after_live_read_failure`;
  - keep failed live/snapshot errors in `btc1h_replay_coverage_sources.csv`.
- Added regression coverage in `scripts\test_btc1h_replay_coverage_audit.py`
  proving a simulated Windows-style locked capture can become replayable when a
  snapshot copy is possible. Existing missing-capture and readable-capture tests
  still pass.
- Ran the BTC1H coverage audit directly and then through the full evidence
  refresh. The real active BTC1H capture still did not become usable:
  - all BTC1H paper rows: `10` rows, `3` replayable, `7` locked-blocked;
  - since-freeze rows: `5` rows, `0` replayable, `5` locked-blocked;
  - the active capture error is still the live Python process lock, and the
    snapshot fallback also fails with Windows `PermissionError 32` while PID
    `16216` owns the file.
- Reran the full ordered evidence refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T12:58:38Z` to `2026-05-18T13:04:59Z`;
  - 25/25 steps passed;
  - readiness returned exit code `1`, still expected for no production-ready
    candidates;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_070456`.
- Latest gates remain blocked:
  - readiness production-ready count `0`;
  - forward consistency count `0`;
  - row reconciliation `2` passing matches but `0` promotion-usable rows;
  - canonical official feature table `0` promotion-usable rows;
  - post-restart collection and drawdown gates pending controlled restart;
  - deployable settlement-basis guards `0`;
  - BTC1H since-freeze replay coverage `0 / 5`.
- Validation:
  - Python compile passed for the BTC1H replay coverage audit/test, refresh
    controller, GPT Pro packet builder, and GPT Pro action-status builder using
    repo-local `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc15m_shadow_config
    scripts.test_btc_evidence_stack_refresh
    scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc_paper_restart_safety -v` passed `26 / 26`.
- Deployment conclusion remains unchanged: BTC1H is not closer to promotion from
  current live state. The snapshot fallback is useful for future readable
  snapshots, but this active process still requires a status sidecar or an
  explicit controlled paper-only restart/start before row-level BTC1H replay
  reconciliation can count.

## 2026-05-18 BTC15M q250 YES-only replay refresh and first-signal semantics audit

- Re-checked process state before and after this work. The same four Python
  processes were running: BTC15M live capture PID `1724`, q250 first-skip paper
  shadow PID `7052`, q1000 YES paper shadow PID `24840`, and BTC1H
  high-conf80 entry70 no-chase paper shadow PID `16216`. q250 YES-only is still
  not running. No process was stopped, started, restarted, archived, migrated,
  deployed, or traded.
- Refreshed the exact frozen BTC15M post-freeze live websocket replays against
  the active BTC15M capture from freeze `2026-05-18T04:17:44Z` through roughly
  `2026-05-18T13:17Z`, then REST-filled official Kalshi settlements:
  - q250 raw first-signal qty>=500:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_latest_codex`
    and REST fill
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_postfreeze_rest_official_latest_codex`;
    `66` raw hits, `2` first signals, `2` official rows, official 2c PnL
    `$0.00`, win rate `50%`, `0` proxy/official mismatches.
  - q250 YES-only first-signal qty>=500:
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_latest_codex`
    and REST fill
    `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex`;
    `60` raw hits, `1` first signal, `1` official row, official 2c PnL
    `+$0.50`, win rate `100%`, `0` proxy/official mismatches.
  - q1000 YES:
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_latest_codex`
    and REST fill
    `backtest_outputs\btc15m_f2_live_ws_q1000_yes_postfreeze_rest_official_latest_codex`;
    `60` raw hits, `1` first signal, `1` official row, official 2c PnL
    `+$0.50`, win rate `100%`, `0` proxy/official mismatches.
- Added `scripts\audit_btc15m_first_signal_side_semantics.py` plus
  `scripts\test_btc15m_first_signal_side_semantics.py`. This diagnostic checks
  whether a side-filtered first-signal replay is merely a subset of the global
  both-side first-signal replay, or whether it creates a newer policy by
  waiting past an earlier opposite-side global first signal.
- Current semantics audit artifact:
  `backtest_outputs\btc15m_first_signal_side_semantics_latest_codex`.
  Result:
  - `audit_status = SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST`;
  - global q250 post-freeze first-signal rows: `2`;
  - side-filtered q250 YES rows: `1`;
  - side events agreeing with global first signal: `1`;
  - side-first extra events: `0`;
  - global-first opposite-side events: `1`.
  Interpretation: in the current post-freeze artifact, the q250 YES-only row is
  not produced by waiting past an earlier NO/global first signal. This removes
  one policy-continuity worry for this tiny window, but it does not make old
  replay rows promotion evidence.
- Wired the semantics audit into:
  - `scripts\refresh_btc_evidence_stack.py` as step `12 / 26`;
  - `scripts\build_gpt_pro_strategy_packet.py`, including the summary/detail
    CSVs in future Pro packets;
  - the reusable Kalshi BTC skill reference.
- Reran the full ordered evidence refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T13:21:49Z` to `2026-05-18T13:26:47Z`;
  - `26 / 26` steps passed;
  - readiness returned exit code `1`, still expected for
    `production_ready_count = 0`;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_072645`.
- Latest action-status blockers remain unchanged in substance:
  - readiness production-ready count `0`;
  - forward consistency count `0`;
  - row reconciliation `2` passing matches but `0` promotion-usable rows;
  - canonical official feature table `0` promotion-usable rows;
  - post-restart collection and drawdown gates pending controlled restart;
  - deployable settlement-basis guards `0`;
  - BTC1H since-freeze replay coverage `0 / 5`, with `5` locked-blocked rows.
- Validation:
  - Python compile passed for the new semantics audit/test, evidence refresh
    controller/test, and GPT Pro packet builder using repo-local
    `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc15m_first_signal_side_semantics
    scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc15m_shadow_config
    scripts.test_btc_evidence_stack_refresh
    scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc_paper_restart_safety
    scripts.test_btc_post_restart_verification -v` passed `32 / 32`.
- Deployment conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable. q250 YES-only remains the best next BTC15M forward paper path,
  but it is not running and has only `1` post-freeze official replay row in this
  refreshed window. q250 raw and q1000 YES remain stale-schema controls only.

## 2026-05-18 BTC15M post-freeze replay refresh wired into evidence stack

- Re-checked process state at the start of this work. The same four Python
  processes were running: BTC15M live capture PID `1724`, q250 first-skip paper
  shadow PID `7052`, q1000 YES paper shadow PID `24840`, and BTC1H
  high-conf80 entry70 no-chase paper shadow PID `16216`. q250 YES-only was
  still not running. No process was stopped, started, restarted, archived,
  migrated, deployed, or traded.
- Found and fixed an evidence-stack freshness gap: the full refresh consumed
  BTC15M post-freeze replay artifacts, but did not regenerate those replay and
  REST-official artifacts from the current live capture. This made downstream
  opportunity-rate, side-semantics, kill/continue, and GPT Pro packets capable
  of lagging the actual BTC15M capture horizon.
- Added `scripts\refresh_btc15m_postfreeze_replays.py`, a read-only controller
  that refreshes the three frozen BTC15M post-freeze replay paths and REST-fills
  official Kalshi settlement:
  - q250 raw first-signal qty>=500;
  - q250 YES-only first-signal qty>=500;
  - q1000 YES.
  The controller writes
  `backtest_outputs\btc15m_postfreeze_replay_refresh_latest_codex` and records
  command lines plus candidate-level replay/REST summaries. It does not start,
  stop, restart, trade, deploy, or tune thresholds.
- Hardened `scripts\fill_btc15m_live_ws_official_results.py` so empty replay
  outputs produce a zero-row summary instead of failing while sorting an empty
  DataFrame. This matters because sparse frozen BTC15M windows can legitimately
  produce no trades.
- Wired the new replay refresh into `scripts\refresh_btc_evidence_stack.py`
  before BTC15M signal health, starvation, opportunity-rate, side-semantics, and
  downstream packet/report generation. The evidence-stack refresh now has
  `27` steps.
- Reran the full ordered evidence refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T13:40:43Z` to `2026-05-18T13:50:08Z`;
  - `27 / 27` steps passed;
  - readiness returned exit code `1`, still expected for
    `production_ready_count = 0`;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_075004`.
- Fresh post-freeze replay/REST official refresh results:
  - q250 raw first-signal qty>=500: capture end
    `2026-05-18T13:41:17.639985Z`, `66` raw hits, `2` first signals, `2` REST
    official rows, official 2c PnL `$0.00`, official win rate `50%`, `0`
    proxy/official mismatches.
  - q250 YES-only: capture end `2026-05-18T13:41:41.061677Z`, `60` raw hits,
    `1` first signal, `1` REST official row, official 2c PnL `+$0.50`,
    official win rate `100%`, `0` proxy/official mismatches.
  - q1000 YES: capture end `2026-05-18T13:42:01.189250Z`, `60` raw hits, `1`
    first signal, `1` REST official row, official 2c PnL `+$0.50`, official
    win rate `100%`, `0` proxy/official mismatches.
- Updated downstream opportunity-rate output now uses the fresh replay horizon:
  - q250 raw projected days to `100` post-freeze official rows at current
    post-freeze official rate: `19.18`;
  - q250 YES-only: `38.77`;
  - q1000 YES: `38.80`.
  These projections are collection-planning diagnostics only and remain far too
  sparse for deployment.
- Current side-semantics audit remains
  `SIDE_FILTER_SUBSET_OF_GLOBAL_FIRST`: global first-signal rows `2`,
  side-filtered q250 YES rows `1`, side-first extra events `0`. This still
  does not make old replay rows promotion evidence.
- Current action-status blockers remain deployment-blocking:
  - readiness production-ready count `0`;
  - forward consistency count `0`;
  - row reconciliation `2` passing matches but `0` promotion-usable rows;
  - canonical official feature table `0` promotion-usable rows;
  - post-restart collection and drawdown gates pending controlled restart;
  - deployable settlement-basis guards `0`;
  - BTC1H since-freeze replay coverage `0 / 5`, with `5` locked-blocked rows.
- Validation:
  - Python compile passed for the new replay-refresh controller/test,
    REST-official fill script, evidence refresh controller/test, first-signal
    semantics audit/test, and GPT Pro packet builder using repo-local
    `PYTHONPYCACHEPREFIX`;
  - `python -m unittest scripts.test_btc15m_postfreeze_replay_refresh
    scripts.test_btc15m_first_signal_side_semantics
    scripts.test_btc_evidence_stack_refresh
    scripts.test_btc1h_replay_coverage_audit
    scripts.test_btc15m_shadow_config
    scripts.test_btc_drawdown_sequence_audit
    scripts.test_btc_forward_row_reconciliation
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc_paper_restart_safety
    scripts.test_btc_post_restart_verification -v` passed `37 / 37`.
- Deployment conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable. This work improves evidence freshness and reduces stale-artifact
  risk, but the actual blockers are still clean forward collection, official
  settlement, execution-realism fields, post-restart ledger evidence, and BTC1H
  causal replay coverage.

## 2026-05-18 GPT Pro review plus Predexon official-coverage tightening

- Re-verified that Chrome automation was available through the Sami Chrome
  profile and that ChatGPT was logged into the Pro account. Submitted the
  latest strategy-advisor packet through ChatGPT Pro and saved the response at
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_fresh_chrome_20260518_082010.md`.
  The review again said no deploy, no canary, and no small-size exception:
  `production_ready_count = 0`, forward consistency `0 / 4`,
  row-reconciliation `0` promotion-usable rows, post-restart collection and
  drawdown gates pending, BTC1H replay coverage blocked. Pro ranked the next
  path as BTC15M q250 YES-only paper collection, q1000 YES as a sparse cleaner
  control, and q250 raw as settlement-basis research only.
- Re-ran the full ordered evidence refresh just before the Pro packet:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T13:54:31Z` to `2026-05-18T14:04:33Z`;
  - `27 / 27` steps passed;
  - readiness returned exit code `1`, still expected for
    `production_ready_count = 0`;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_080429`.
- Fresh post-freeze replay/REST official refresh from that run:
  - q250 raw first-signal qty>=500: capture end
    `2026-05-18T13:55:07.920273Z`, `66` raw hits, `2` first signals, `2`
    REST official rows, official 2c PnL `$0.00`, official win rate `50%`,
    `0` proxy/official mismatches.
  - q250 YES-only: capture end `2026-05-18T13:55:35.105412Z`, `60` raw hits,
    `1` first signal, `1` REST official row, official 2c PnL `+$0.50`,
    official win rate `100%`, `0` proxy/official mismatches.
  - q1000 YES: capture end `2026-05-18T13:55:58.100082Z`, `60` raw hits, `1`
    first signal, `1` REST official row, official 2c PnL `+$0.50`, official
    win rate `100%`, `0` proxy/official mismatches.
- Followed the Pro recommendation without touching live processes: no process
  was stopped, started, restarted, archived, migrated, deployed, or traded.
  The q250 YES-only paper shadow remains not running until explicit user
  authorization.
- REST-filled official Kalshi settlement for the materialized Predexon BTC15M
  trade rows:
  `python scripts\fill_btc15m_predexon_official_results.py --trades backtest_outputs\btc15m_f2_combined_predexon_plus_jan29_20260516_164544\combined_predexon_trades.parquet --out-dir backtest_outputs\btc15m_predexon_rest_official_latest_codex --sleep 0.02`
  - input rows: `2,424`;
  - unique markets: `880`;
  - REST official results filled: `374`;
  - metadata results filled: `100,202` market metadata entries available.
  Official strategy summaries were positive on the filled subset, including
  q250 (`69` official rows, `+$9.66`, win `63.77%`), q500 (`67`,
  `+$9.82`, win `64.18%`), q1000 (`52`, `+$8.16`, win `65.38%`), and
  q250_qspeed05 (`63`, `+$10.88`, win `66.67%`). This is still partial
  historical coverage, not deployment evidence.
- Tightened `scripts\audit_btc15m_materialized_filter_grid.py` so official-PnL
  grid runs now report selected rows, scored rows, missing official rows,
  official coverage, uncovered proxy PnL, and uncovered proxy bad windows.
  Incomplete official coverage is now a failure reason by default for official
  PnL columns. The reusable Kalshi BTC skill reference was updated with this
  rule.
- Re-ran the materialized grid on REST-official Predexon columns:
  `python scripts\audit_btc15m_materialized_filter_grid.py --predexon-trades backtest_outputs\btc15m_predexon_rest_official_latest_codex\predexon_trades_rest_official.parquet --out-dir backtest_outputs\btc15m_materialized_filter_grid_rest_official_latest_codex --pred-pnl-col pnl_official_rest_2c --pred-win-col win_pnl_official_rest_2c --min-pred-trades 30 --min-live-official-trades 8 --top-n 100`
  - screened rows: `100`;
  - research_pass: `0`;
  - deploy_ready: `0`.
  Key rows:
  - q250 raw qty>=500: `63` selected Predexon rows, `57` official-scored,
    `6` missing official, official coverage `90.48%`, official Predexon PnL
    `+$8.72`, live official rows `17`, live official PnL `+$4.30`, but
    failure reason `pred_official_coverage_incomplete`.
  - q250 YES-only: `79` selected, `31` official-scored, `48` missing official,
    coverage `39.24%`, uncovered proxy PnL `-$2.24`, `2` uncovered proxy bad
    windows, official Predexon PnL `+$5.36`, live official rows `8`, live
    official PnL `+$0.85`; failure reasons
    `pred_official_coverage_incomplete;pred_uncovered_proxy_bad_windows`.
  - q1000 YES: `33` selected, `23` official-scored, `10` missing official,
    coverage `69.70%`, uncovered proxy PnL `+$1.53`, official Predexon PnL
    `+$6.45`, live official rows `7`, live official PnL `+$1.39`; failure
    reasons `too_few_live_official_trades;pred_official_coverage_incomplete`.
- Refreshed the canonical Predexon official-coverage audit on the same
  REST-filled files:
  `python scripts\audit_btc15m_predexon_official_coverage.py --predexon-trades backtest_outputs\btc15m_predexon_rest_official_latest_codex\predexon_trades_rest_official.parquet --market-results backtest_outputs\btc15m_predexon_rest_official_latest_codex\market_results.csv --out-dir backtest_outputs\btc15m_predexon_official_coverage_latest_codex --min-official-rows 50`
  The audit agrees that every monitored BTC15M Predexon path is not
  deployment-usable. q250 firstskip qty>=500 has `63` selected rows, `57`
  REST-official rows, coverage `90.48%`, official PnL `+$8.72`, but
  uncovered proxy PnL `-$0.41` and `2` bad uncovered windows. q250 YES has
  only `31` REST-official rows out of `79`, coverage `39.24%`, uncovered
  proxy PnL `-$2.24`, and `4` bad uncovered windows. q1000 YES has `23`
  REST-official rows out of `33`, official PnL `+$6.45`, but is still below
  the official-row threshold and has partial coverage.
- Interpretation after this loop: q250 raw still has the strongest combined
  official-subset/live replay numbers, but it remains settlement-basis research
  because it includes the historically fragile side mix and lacks complete
  official coverage. q250 YES-only remains operationally attractive as the next
  paper-only forward path, but the historical official-coverage audit is now
  less flattering: most selected Predexon rows are still unfilled by REST
  official settlement, and the uncovered proxy subset contains bad windows.
  q1000 YES looks cleaner on the uncovered proxy subset, but it is too sparse.
- Validation:
  - `python -m py_compile scripts\audit_btc15m_materialized_filter_grid.py`
    passed;
  - both REST official fill and tightened materialized-grid reruns completed
    successfully.
- Deployment conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable. The correct next operational move is still an explicitly
  authorized paper-only controlled start/restart for q250 YES-only/q1000/q250
  raw/BTC1H followed by fresh clean-schema official-settled row collection and
  row-level reconciliation. Without that authorization, the highest-value safe
  work is settlement-basis guard development and continued read-only evidence
  auditing.

## 2026-05-18 settlement-basis guard candidate diagnostics

- Continued read-only research toward the BTC15M/BTC1H deployment objective.
  Re-checked process state before acting. The same four Python processes were
  running: BTC15M live capture PID `1724`, q250 first-skip paper shadow PID
  `7052`, q1000 YES paper shadow PID `24840`, and BTC1H high-conf80 entry70
  no-chase paper shadow PID `16216`. q250 YES-only remains not running. No
  process was stopped, started, restarted, archived, migrated, deployed, or
  traded.
- Added `scripts\build_btc_settlement_basis_guard_candidates.py`, a
  research-only diagnostic that evaluates simple preregisterable
  settlement-basis veto candidates on already official-scored basis-risk rows.
  Guard inputs are decision-time-safe side and distance-to-strike fields only:
  YES-only, minimum side-aligned decision distance in USD/bps, and
  YES-or-NO-distance guards. Official/proxy outcomes are used only for
  after-the-fact scoring. The script hard-codes `deployable_guard_now=False`
  because no guard has fresh preregistered forward evaluation.
- While testing the guard output, found and fixed a source-labeling bug in
  `scripts\build_btc_settlement_basis_risk_audit.py`: BTC15M rows coming from
  `shadow_official_trades.csv` were previously labeled as family `BTC1H`.
  The loader now infers BTC15M/BTC1H from ledger/event/market tickers. Added
  `scripts\test_btc_settlement_basis_risk_audit.py` to prevent this regression.
- Wired the basis-risk audit and new guard-candidate diagnostic into
  `scripts\refresh_btc_evidence_stack.py` in dependency order:
  settlement-basis risk audit -> basis danger table -> guard candidates ->
  basis model feasibility. Also updated the GPT Pro packet builder so future
  packets include `settlement_basis_guard_summary.csv` and
  `settlement_basis_guard_promising.csv`.
- Fixed `scripts\check_btc_deployment_readiness.py` so the materialized grid
  directory is chosen by latest mtime across the REST-official/pred-official
  grid prefixes. The readiness artifact now points to the stricter
  `backtest_outputs\btc15m_materialized_filter_grid_rest_official_latest_codex`
  grid, preserving failure reasons such as
  `pred_official_coverage_incomplete`.
- Reran the ordered evidence stack:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T14:38:15Z` to `2026-05-18T14:49:31Z`;
  - `29 / 29` steps passed;
  - readiness returned exit code `1`, still expected for
    `production_ready_count = 0`.
- Refreshed readiness and downstream reports after the materialized-grid
  selection fix. Latest readiness:
  - `production_ready_count = 0`;
  - materialized grid input:
    `backtest_outputs\btc15m_materialized_filter_grid_rest_official_latest_codex`;
  - focused q250 materialized rows now fail with
    `pred_official_coverage_incomplete;too_few_live_official_trades;too_few_live_proxy_trades`.
- Latest post-freeze BTC15M replay refresh:
  - q250 raw first-signal qty>=500: capture end
    `2026-05-18T14:38:45.855347Z`, `66` raw hits, `2` first signals, `2`
    official rows, official 2c PnL `$0.00`, win `50%`, `0` mismatches.
  - q250 YES-only: capture end `2026-05-18T14:39:10.656854Z`, `60` raw hits,
    `1` first signal, `1` official row, official 2c PnL `+$0.50`, win
    `100%`, `0` mismatches.
  - q1000 YES: capture end `2026-05-18T14:39:30.240521Z`, `60` raw hits, `1`
    first signal, `1` official row, official 2c PnL `+$0.50`, win `100%`,
    `0` mismatches.
- Latest settlement-basis guard candidate artifact:
  `backtest_outputs\btc_settlement_basis_guard_candidates_latest_codex`.
  It produced `154` guard/candidate summary rows and `37` research-promising
  rows, all non-deployable. Useful diagnostic examples:
  - q250 firstskip qty>=500 `no_guard`: `24` kept rows, official PnL `+$3.74`,
    but `3` adverse proxy/official flips, so it remains basis-fragile.
  - q250 firstskip qty>=500 `yes_only`: `8` kept rows, official PnL `+$0.88`,
    `0` mismatches, but only `8` official rows and no fresh preregistered
    forward guard evaluation.
  - q1000 YES `no_guard` / `yes_only`: `8` kept rows, official PnL `+$0.90`,
    `0` mismatches, but far too sparse.
  - BTC1H high-conf80 entry70 no-chase has no promising guard in this table;
    its `no_guard` row still has `10` rows, `2` mismatches, and `1` adverse
    mismatch.
- Latest kill/continue and forward-consistency reports remain conservative:
  q250 firstskip qty>=500 is `CONTINUE_FORWARD_ONLY`, q250 YES-only is
  `PREREGISTERED_PAPER_START_WITH_PERMISSION`, q1000 YES is
  `CONTINUE_FORWARD_ONLY_SPARSE`, and BTC1H is
  `OBSERVE_ONLY_RESTART_WITH_PERMISSION`. Forward consistency remains
  `0 / 4` promotion-ready.
- Latest GPT Pro packet rebuilt after the refreshed artifacts:
  `gpt_pro_packets\strategy_advisor_20260518_085204`.
- Validation:
  - `python -m py_compile scripts\build_btc_settlement_basis_risk_audit.py
    scripts\build_btc_settlement_basis_guard_candidates.py
    scripts\refresh_btc_evidence_stack.py
    scripts\build_gpt_pro_strategy_packet.py
    scripts\check_btc_deployment_readiness.py
    scripts\test_btc_settlement_basis_risk_audit.py` passed;
  - `python -m unittest scripts.test_btc_settlement_basis_risk_audit
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `4 / 4`.
- Deployment conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable. The basis guard diagnostics make q250 raw's NO-side settlement
  fragility more explicit and make q250/q1000 YES-side guards plausible
  preregistration ideas, but they do not solve the real blocker: fresh
  official-settled, clean-schema, row-reconciled forward paper evidence after
  explicit user-authorized paper-only start/restart.

## 2026-05-18 - GPT Pro loop 2 and basis-guard preregistration packet

- Submitted a refreshed sanitized GPT Pro packet through the Chrome extension
  after verifying Chrome could open `https://example.com/` and ChatGPT was
  logged in with `Pro` visible. The packet was submitted as a long pasted-text
  attachment and saved to
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_chrome_20260518_092045.md`.
- GPT Pro again gave a hard no-deploy verdict: BTC15M and BTC1H are not
  deployable, not even as a canary. It ranked the next research paths as:
  1. q250 firstskip qty>=500 YES-only controlled paper-forward collection;
  2. q1000 YES as a sparse cleaner control;
  3. q250 raw as basis/side decomposition only. BTC1H stays observe-only.
- Updated `scripts\build_btc15m_next_forward_candidate_packet.py` to emit
  explicit basis-guard freeze specs and diagnostic evidence:
  - `basis_guard_freeze_specs.csv`
  - `basis_guard_diagnostic_evidence.csv`
  The primary future policy is q250 firstskip qty>=500 YES-only. q1000 YES
  remains an existing sparse control. q1000 aligned-distance and q250
  YES-or-NO-distance guards are sidecar metrics only, not trading policies.
- Fixed `scripts\build_gpt_pro_strategy_packet.py` so multi-CSV artifacts from
  the next-forward packet and settlement-basis guard-candidate packet actually
  make it into the GPT Pro packet by reference. Latest packet after the full
  refresh is `gpt_pro_packets\strategy_advisor_20260518_092001`.
- Ran the ordered evidence stack again:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`
  - latest run: `2026-05-18T15:13:00Z` to `2026-05-18T15:20:04Z`;
  - `29 / 29` steps passed;
  - readiness returned exit code `1`, still expected because
    `production_ready_count = 0`.
- Ran a dry-run only restart plan:
  `powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1`
  It wrote
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_092129\restart_plan.json`.
  No processes were stopped or started. All four target wrapper safety checks
  passed, and the plan still requires explicit user authorization before
  execution.
- Refreshed the restart authorization and Pro action-status artifacts after
  the dry run:
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`
  - `backtest_outputs\btc_post_restart_verification_latest_codex`
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`
  Current status remains:
  - `READY_FOR_EXPLICIT_USER_AUTHORIZATION` for the paper-only restart/start
    path;
  - `restart_executed=False`;
  - q250 YES-only DB missing and not running;
  - q250 raw, q1000 YES, and BTC1H active ledgers require restart for complete
    execution-realism schema;
  - row reconciliation promotion-usable count remains `0`;
  - BTC1H since-freeze replay coverage remains blocked (`0/7` replayable,
    `7` locked-blocked rows).
- Validation:
  - `python -m py_compile scripts\build_btc15m_next_forward_candidate_packet.py
    scripts\build_gpt_pro_strategy_packet.py
    scripts\build_btc_settlement_basis_risk_audit.py
    scripts\build_btc_settlement_basis_guard_candidates.py
    scripts\refresh_btc_evidence_stack.py
    scripts\build_btc_gpt_pro_action_status.py` passed;
  - `python -m unittest scripts.test_btc_settlement_basis_risk_audit
    scripts.test_btc_settlement_basis_model_feasibility -v` passed `4 / 4`.
- Current conclusion: still no deployable strategy. The next honest operational
  move is explicit user-authorized paper-only restart/start of the four shadow
  targets so the evidence clock can start with clean execution-realism schemas.
  Until that happens, q250 YES-only remains the top candidate on paper, not a
  deployable strategy.

## 2026-05-18 - Fresh capture-sidecar preflight and GPT Pro re-loop

- Tightened the paper-shadow restart preflight so it now proves more than a
  fresh ledger schema. `scripts\build_btc_shadow_restart_preflight.py` creates
  a fresh capture DuckDB for each target, writes smoke rows to `capture_health`,
  `ws_orderbook_top`, and `signal_scan`, and verifies the lock-free
  `.status.json` sidecar reports nonzero rows and timestamps. Restart readiness
  now requires `fresh_capture_sidecar_status = PASS_CAPTURE_SIDECAR`.
- Updated `scripts\build_btc_restart_authorization_packet.py` so authorization
  summaries include the fresh capture-sidecar requirement, and added
  `test_restart_preflight_proves_capture_status_sidecar` to
  `scripts\test_btc_paper_restart_safety.py`.
- Validation:
  - `python -m py_compile scripts\build_btc_shadow_restart_preflight.py
    scripts\build_btc_restart_authorization_packet.py
    scripts\test_btc_paper_restart_safety.py` passed;
  - `python -m unittest scripts.test_btc_paper_restart_safety -v` passed
    `5 / 5`;
  - the broader compile command for the active BTC evidence stack scripts
    passed.
- Refreshed the ordered evidence stack:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`.
  Latest run: `2026-05-18T15:31:30Z` to `2026-05-18T15:40:24Z`;
  `29 / 29` steps passed. Readiness returned exit code `1`, accepted by the
  controller because `production_ready_count = 0`.
- Current refreshed state remains no-deploy:
  - readiness `production_ready_count = 0`;
  - GPT Pro action status says `promotion_usable_rows = 0`,
    `row_reconciliation_pass_count = 2`, and
    `row_reconciliation_promotion_usable_count = 0`;
  - post-restart verification still has `restart_executed=False`;
  - q250 YES-only is still not running and has no DB;
  - q250 raw, q1000 YES, and BTC1H active ledgers still have stale
    25-column schemas missing the 12 execution-realism fields;
  - BTC1H since-freeze replay coverage remains blocked at `0 / 7` replayable
    rows with `7` locked-blocked rows.
- The stricter restart preflight now passes for all four paper targets:
  q250 raw, q250 YES-only start, q1000 YES, and BTC1H all show
  `PASS_SCHEMA_READY`, `PASS_INSERT_REALISM_FIELDS`,
  `PASS_CAPTURE_SIDECAR`, and `PASS_RESTART_PATH_READY`. This reduces restart
  risk but does not make old/current rows deployable evidence.
- Latest restart authorization remains paper-only and gated on explicit user
  authorization:
  `backtest_outputs\btc_restart_authorization_packet_latest_codex`.
  The latest dry-run restart plan is
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_092857`;
  it did not execute.
- Latest GPT Pro packet rebuilt after the refresh:
  `gpt_pro_packets\strategy_advisor_20260518_094021`. Chrome extension access
  was available and ChatGPT showed the `Pro` account. The packet was submitted
  to a new ChatGPT conversation URL via the normal Chrome window after the
  extension input channel failed on clipboard/typing. The response was
  recovered from the Chrome page copy and saved to
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_refresh_sidecar_20260518_095721.md`.
- GPT Pro's refreshed verdict matched the local gates:
  - no BTC15M or BTC1H deployment, not even canary;
  - q250 firstskip qty>=500 YES-only is the top path, but only after explicit
    controlled paper-only start/restart;
  - q1000 YES should remain a sparse cleaner control;
  - q250 raw is basis/side decomposition only because of NO-side
    proxy/official fragility;
  - BTC1H is a plumbing/observability path until clean schema, lock-free
    capture sidecars, and causal replay coverage exist.
- Current conclusion is unchanged: no BTC15M or BTC1H strategy is deployable.
  The next honest blocker is not model search; it is explicit paper-only
  restart/start authorization so the forward evidence clock can start with
  clean execution-realism schemas and readable capture sidecars.

## 2026-05-18 - Post-restart verifier now requires all target sidecars

- Re-checked Python processes before making changes. Current matching jobs:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PID `16216`.
  q250 YES-only is still not running.
- Closed a verifier loophole: the fresh restart preflight already proved all
  four wrappers can emit a lock-free capture `.status.json`, but
  `scripts\build_btc_post_restart_verification.py` only required the BTC1H
  sidecar after restart. It now requires every restarted paper target to have a
  fresh sidecar with `updated_at_utc >= restart_utc`, no sidecar error, and
  nonzero `capture_health`, `ws_orderbook_top`, and `signal_scan` row counts
  before the evidence clock can be marked ready.
- Added tests in `scripts\test_btc_post_restart_verification.py` for:
  - sidecar freshness after restart;
  - rejection of sidecars stale before restart;
  - rejection of sidecars missing basic table row counts.
- Validation:
  - `python -m py_compile scripts\build_btc_post_restart_verification.py
    scripts\test_btc_post_restart_verification.py` passed;
  - `python -m unittest scripts.test_btc_post_restart_verification -v` passed
    `5 / 5`;
  - `python -m py_compile scripts\build_btc_gpt_pro_action_status.py
    scripts\build_btc_post_restart_verification.py
    scripts\test_btc_post_restart_verification.py` passed.
- Regenerated:
  - `backtest_outputs\btc_post_restart_verification_latest_codex`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex` using
    `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_refresh_sidecar_20260518_095721.md`.
- Current regenerated post-restart verification remains correctly blocked:
  - `restart_executed=False`;
  - `evidence_clock_ready=False`;
  - `required_capture_sidecars_ready=PENDING_OR_FAIL`;
  - all four targets now show `capture_sidecar_required=True` and
    `capture_sidecar_ready=False` because no controlled restart/start has
    executed.
- Current regenerated GPT Pro action status remains unchanged in substance:
  `NO DEPLOY`; `production_ready_count=0`; `promotion_usable=0`;
  q250 YES-only is still waiting for explicit paper restart/start; q250 raw and
  q1000 YES remain stale-schema controls; BTC1H remains observe-only with
  `0/7` since-freeze replayable rows.

## 2026-05-18 - Restart process-identity gate added

- Ran the full ordered evidence refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex`.
  Latest run: `2026-05-18T16:05:38Z` to `2026-05-18T16:16:37Z`;
  `29 / 29` steps passed. Readiness returned exit code `1`, still expected
  because `production_ready_count = 0`.
- Latest compact state after the refresh:
  - readiness `production_ready_count = 0`;
  - GPT Pro action status `promotion_usable_rows = 0`;
  - row reconciliation still has two matched-but-not-promotable BTC15M rows:
    q250 raw `2` matched rows and q1000 YES `1` matched row, both blocked by
    stale paper-ledger execution fields and too few official rows;
  - q250 YES-only still has `0` paper rows and `1` replay-only row, so it is
    still `REPLAY_ROWS_WITHOUT_PAPER_SHADOW`;
  - BTC1H remains `NO_CAUSAL_REPLAY_COMPARATOR`.
- Added process creation timestamps to
  `scripts\check_btc_forward_shadow_status.py`. The status CSV now includes
  `process_created_at_utc` alongside each matching PID.
- Tightened `scripts\build_btc_post_restart_verification.py` again. After an
  authorized controlled restart, it now requires every target's current PID to
  match the PID recorded in `restart_result.json` and requires that process to
  have been created after the restart plan was built. This prevents stale
  survivor processes from starting the evidence clock.
- Added tests in `scripts\test_btc_post_restart_verification.py` for the
  process-identity gate:
  - pass when current PID matches `restart_result.json`;
  - fail when the PID differs;
  - fail when process creation time predates the restart plan.
- Validation:
  - `python -m py_compile scripts\check_btc_forward_shadow_status.py
    scripts\build_btc_post_restart_verification.py
    scripts\test_btc_post_restart_verification.py
    scripts\test_btc_forward_shadow_status.py` passed;
  - `python -m unittest scripts.test_btc_post_restart_verification
    scripts.test_btc_forward_shadow_status -v` passed `8 / 8`.
- Regenerated:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`;
  - `backtest_outputs\btc_post_restart_verification_latest_codex`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_101938`.
- Current regenerated post-restart verification remains correctly blocked:
  - `restart_executed=False`;
  - `evidence_clock_ready=False`;
  - `target_process_identity=PENDING_OR_FAIL`;
  - current stale PIDs were created around `2026-05-18T02:41:59Z`, before the
    dry-run restart plan, so they cannot be confused with future controlled
    restart evidence.
- Current conclusion remains unchanged: no BTC15M or BTC1H strategy is
  deployable. The best next research path is still explicit paper-only
  controlled start/restart of q250 YES-only plus controls, then future
  official-settled, execution-realistic, row-reconciled collection.

## 2026-05-18 - Evidence-clock verifier folded into readiness

- Tightened deployment readiness so the stricter post-restart verifier is now
  a hard blocker, not just a side report. `scripts\check_btc_deployment_readiness.py`
  now reads `backtest_outputs\btc_post_restart_verification_latest_codex` and
  adds blockers such as:
  - `post_restart_evidence_clock_not_ready`;
  - `post_restart_controlled_restart_not_executed`;
  - `post_restart_process_identity_not_ready`;
  - `post_restart_capture_sidecars_not_ready`;
  - `post_restart_active_ledger_schemas_not_ready`.
- Candidate-specific readiness rows also inherit target-level verifier fields
  from `post_restart_target_status.csv`, including process identity status,
  capture-sidecar status, active schema status, and evidence-clock status. This
  prevents stale survivor PIDs, locked/missing sidecars, or stale schemas from
  being hidden behind favorable replay/settlement summaries.
- Updated `scripts\build_btc_gpt_pro_action_status.py` so GPT Pro status has an
  explicit `post_restart_evidence_clock` checklist row and candidate
  `deployable_now` also requires `post_restart_evidence_clock_ready=True`.
- Moved `post_restart_verification` earlier in
  `scripts\refresh_btc_evidence_stack.py`, before `deployment_readiness` and
  `gpt_pro_action_status`, so the full refresh uses a consistent dependency
  order.
- Added regression coverage in
  `scripts\test_btc_readiness_row_reconciliation.py`: a candidate with good
  official rows/schema is still blocked if the post-restart evidence clock
  fails process identity or capture-sidecar freshness.
- Validation:
  - `python -m py_compile scripts\check_btc_deployment_readiness.py
    scripts\build_btc_gpt_pro_action_status.py
    scripts\refresh_btc_evidence_stack.py
    scripts\test_btc_readiness_row_reconciliation.py` passed;
  - `python -m unittest scripts.test_btc_readiness_row_reconciliation -v`
    passed `2 / 2`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir
    backtest_outputs\btc_evidence_stack_refresh_latest_codex --skip-packet`
    passed `28 / 28` from `2026-05-18T16:27:25Z` to
    `2026-05-18T16:37:56Z`. Readiness return code `1` was expected and counted
    as pass because no strategy is production-ready.
- Latest refreshed state:
  - `deployment_readiness_latest_codex` has `production_ready_count=0` and now
    records `post_restart_verification_dir`;
  - `btc_gpt_pro_action_status_latest_codex` has
    `post_restart_evidence_clock_ready=False`,
    `post_restart_verification_restart_executed=False`,
    `promotion_usable_rows=0`, and `row_reconciliation_promotion_usable_count=0`;
  - process audit still shows only the pre-existing BTC15M capture, q250 raw,
    q1000 YES, and BTC1H high-conf paper shadows; q250 YES-only is still not
    running.
- Conclusion unchanged: no deployment or canary. The repo is now better guarded
  against an accidental "ready" interpretation before an explicit controlled
  paper restart/start creates a fresh evidence clock and later official-settled
  rows.

## 2026-05-18 - Frozen wrapper policy parity gate added

- Added `scripts\audit_btc_frozen_policy_parity.py`, a read-only audit that
  verifies the active paper-shadow wrapper files still encode the preregistered
  policy thresholds before future rows can count. It checks:
  - q250 firstskip qty>=500 both-side control;
  - q250 firstskip qty>=500 YES-only primary forward policy;
  - q1000 YES sparse control;
  - BTC1H high-conf80 entry70 no-chase observe-only control.
- The audit currently passes all four wrappers:
  `policy_parity_pass_count=4/4` and `all_policy_parity_pass=True`.
- Wired the new artifact into the normal evidence stack:
  - `scripts\refresh_btc_evidence_stack.py` now runs
    `frozen_policy_parity` after the candidate freeze packet and before
    deployment readiness;
  - `scripts\check_btc_deployment_readiness.py` consumes
    `btc_frozen_policy_parity_latest_codex` and records the parity artifact in
    `run_info.json`;
  - `scripts\build_btc_gpt_pro_action_status.py` now has a
    `frozen_policy_parity` checklist row and requires candidate policy parity
    for `deployable_now`;
  - `scripts\build_gpt_pro_strategy_packet.py` now includes the parity audit in
    GPT Pro evidence packets.
- Added `scripts\test_btc_frozen_policy_parity.py` with:
  - a real-wrapper pass test;
  - a synthetic mismatch test proving a changed threshold is blocked.
- Validation:
  - `python -m py_compile scripts\audit_btc_frozen_policy_parity.py
    scripts\test_btc_frozen_policy_parity.py
    scripts\check_btc_deployment_readiness.py
    scripts\build_btc_gpt_pro_action_status.py
    scripts\refresh_btc_evidence_stack.py
    scripts\test_btc_evidence_stack_refresh.py
    scripts\build_gpt_pro_strategy_packet.py` passed;
  - `python -m unittest scripts.test_btc_frozen_policy_parity
    scripts.test_btc_readiness_row_reconciliation
    scripts.test_btc_evidence_stack_refresh -v` passed `9 / 9`;
  - full refresh
    `python scripts\refresh_btc_evidence_stack.py --out-dir
    backtest_outputs\btc_evidence_stack_refresh_latest_codex` passed `30 / 30`
    from `2026-05-18T16:46:52Z` to `2026-05-18T17:01:56Z`.
- Latest refreshed state:
  - deployment readiness still has `production_ready_count=0`;
  - GPT Pro action status has `all_frozen_policy_parity_pass=True`,
    `post_restart_evidence_clock_ready=False`, `promotion_usable_rows=0`,
    and `row_reconciliation_promotion_usable_count=0`;
  - q250 YES-only remains not running; q250 raw, q1000 YES, and BTC1H remain
    running but not deployable evidence because the controlled restart/start
    and post-restart evidence clock have not happened.
- Latest GPT Pro packet was rebuilt at
  `gpt_pro_packets\strategy_advisor_20260518_110152` and now includes
  `backtest_outputs\btc_frozen_policy_parity_latest_codex`.
- Conclusion unchanged: no BTC15M or BTC1H deployment. This change only closes
  another future-evidence loophole: a clean post-restart ledger cannot count if
  the wrapper drifted from the frozen policy.

## 2026-05-18 - q250 YES-only focused replay added to readiness

- Found a visibility gap in deployment readiness: the current top GPT Pro path,
  `q250_firstskip_qty500_yes`, had its own focused REST-official live replay
  artifact but was not included as a `latest_live_replay_rest_official` row in
  `deployment_readiness_latest_codex`.
- The focused q250 YES-only artifact is:
  `backtest_outputs\btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_rest_official_latest_codex`.
  Current summary:
  - `official_trades = 1`;
  - `official_pnl = +0.50`;
  - `official_win_rate = 100%`;
  - `official_proxy_result_mismatches = 0`.
- Updated `scripts\build_btc_execution_realism_audit.py` so q250 YES-only
  replay rows are audited separately as
  `q250_firstskip_qty500_yes_live_replay`. Current execution audit:
  - `rows = 1`;
  - `official_rows = 1`;
  - `required_field_complete_rate = 1.0`;
  - `min_recorded_visible_qty = 1135`;
  - `audit_status = PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION`.
- Updated `scripts\check_btc_deployment_readiness.py` so the q250 YES-only
  focused replay directory is included in `latest_live_replay_dirs` and receives
  replay-execution, row-reconciliation, frozen-policy, and post-restart
  evidence-clock blockers.
- Added a regression test in
  `scripts\test_btc_readiness_row_reconciliation.py`: a q250 YES-only focused
  replay row remains not production-ready when there is no matching paper shadow
  row.
- Validation:
  - `python -m py_compile scripts\build_btc_execution_realism_audit.py
    scripts\check_btc_deployment_readiness.py
    scripts\test_btc_readiness_row_reconciliation.py` passed;
  - `python -m unittest scripts.test_btc_readiness_row_reconciliation -v`
    passed `3 / 3`;
  - regenerated `btc_execution_realism_audit_latest_codex`,
    `deployment_readiness_latest_codex`,
    `btc_gpt_pro_action_status_latest_codex`, and GPT Pro packet
    `gpt_pro_packets\strategy_advisor_20260518_110702`.
- Latest readiness now has an explicit q250 YES-only focused row:
  - candidate `q250_firstskip_qty500_yes`;
  - source `latest_live_replay_rest_official`;
  - `production_ready = False`;
  - `live_official_trades = 1`;
  - `live_official_pnl = +0.50`;
  - `replay_execution_status = PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION`;
  - `row_reconciliation_status = REPLAY_ROWS_WITHOUT_PAPER_SHADOW`;
  - `row_reconciliation_promotion_usable = False`;
  - `post_restart_verification_evidence_clock_ready = False`.
- Conclusion unchanged: q250 YES-only remains the best next paper-forward
  candidate, but its single positive focused replay row is not deployable
  evidence. It needs explicit controlled paper start/restart, fresh matching
  paper rows, official settlement, row reconciliation, and enough sample size.

## 2026-05-18 - Refresh dependency order fixed for q250 YES-only replay

- Found and fixed a dependency-order issue introduced by making q250 YES-only a
  focused readiness row. `scripts\build_btc_execution_realism_audit.py` now
  consumes the q250 YES-only post-freeze replay artifact, so the sequential
  refresh must regenerate `btc15m_postfreeze_replay_refresh_latest_codex` before
  execution-realism/readiness consume replay rows.
- Updated `scripts\refresh_btc_evidence_stack.py` so the full refresh order is
  now:
  - shadow/process status;
  - REST-official shadow settlement;
  - BTC15M post-freeze replay/REST-official refresh;
  - execution-realism audit;
  - downstream schema, restart, collection, readiness, consistency, action
    status, and GPT Pro packet artifacts.
- Updated `scripts\test_btc_evidence_stack_refresh.py` to assert
  `btc15m_postfreeze_replay_refresh` runs before `execution_realism_audit`.
- Updated the reusable Kalshi BTC skill and deployability reference so the
  manual fallback command order matches the controller.
- Validation:
  - `python -m py_compile scripts\refresh_btc_evidence_stack.py
    scripts\test_btc_evidence_stack_refresh.py
    scripts\build_btc_execution_realism_audit.py
    scripts\check_btc_deployment_readiness.py
    scripts\test_btc_readiness_row_reconciliation.py` passed;
  - `python -m unittest scripts.test_btc_evidence_stack_refresh
    scripts.test_btc_readiness_row_reconciliation -v` passed `8 / 8`;
  - full refresh
    `python scripts\refresh_btc_evidence_stack.py --out-dir
    backtest_outputs\btc_evidence_stack_refresh_latest_codex` passed `30 / 30`
    from `2026-05-18T17:10:09Z` to `2026-05-18T17:20:19Z`.
- Latest refreshed artifacts under the corrected order:
  - `deployment_readiness_latest_codex` has `production_ready_count=0`;
  - q250 YES-only focused readiness row remains
    `production_ready=False`, `live_official_trades=1`,
    `live_official_pnl=+0.50`,
    `replay_execution_status=PASS_REPLAY_EXECUTION_FIELDS_NOT_PROMOTION`,
    `row_reconciliation_status=REPLAY_ROWS_WITHOUT_PAPER_SHADOW`, and
    `post_restart_verification_evidence_clock_ready=False`;
  - `btc_gpt_pro_action_status_latest_codex` remains no-deploy with
    `post_restart_evidence_clock_ready=False`, `promotion_usable_rows=0`, and
    `row_reconciliation_promotion_usable_count=0`;
  - latest GPT Pro packet rebuilt at
    `gpt_pro_packets\strategy_advisor_20260518_112016`.
- Conclusion unchanged: this was an evidence-integrity fix, not new alpha.
  q250 YES-only is still the top paper-forward path but remains undeployable
  until explicit controlled paper start/restart and enough clean future rows.

## 2026-05-18 - Settlement-basis gates folded into deployment readiness

- Ran a fresh read-only evidence refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir
  backtest_outputs\btc_evidence_stack_refresh_latest_codex`.
  It passed `30 / 30` from `2026-05-18T17:25:00Z` to
  `2026-05-18T17:36:09Z`; readiness still correctly returned no deploy with
  `production_ready_count=0`.
- Added settlement-basis gate fields to
  `scripts\check_btc_deployment_readiness.py`. The main readiness table now
  carries per-row `settlement_basis_gate_status`,
  `settlement_basis_gate_reasons`, official row count, mismatch rate, adverse
  mismatches, PnL delta per trade, and basis-size diagnostics from
  `btc_settlement_basis_risk_audit_latest_codex`.
- Added a regression test in
  `scripts\test_btc_readiness_row_reconciliation.py` proving q250
  first-signal raw replay is blocked when the NO-side official/proxy basis
  gate fails.
- Validation:
  - `python -m py_compile scripts\check_btc_deployment_readiness.py
    scripts\test_btc_readiness_row_reconciliation.py` passed;
  - `python -m unittest scripts.test_btc_readiness_row_reconciliation -v`
    passed `4 / 4`;
  - regenerated `deployment_readiness_latest_codex`,
    `btc_forward_evidence_report_latest_codex`,
    `btc_gpt_pro_action_status_latest_codex`, and GPT Pro packet
    `gpt_pro_packets\strategy_advisor_20260518_114055`.
- Current focused readiness facts:
  - raw `q250_firstskip_qty500`: `24` official replay rows, official PnL
    `+3.74`, but settlement-basis gate `FAIL` with mismatch rate `18.75%`,
    `3` adverse mismatches, and PnL delta `-0.1875` per both-result trade;
  - `q250_firstskip_qty500_yes`: `1` focused replay row, official PnL
    `+0.50`, basis-clean so far on the broader YES slice but only `8` basis
    rows and still `REPLAY_ROWS_WITHOUT_PAPER_SHADOW`;
  - `q1000_yes`: `8` official replay rows, official PnL `+0.90`, basis gate
    only fails sample size (`too_few_official_rows`);
  - BTC1H high-conf80 entry70 shadow: `13` all-time official rows, official
    PnL `+1.92`, but settlement-basis gate `FAIL` with `16.67%` mismatch,
    no causal replay comparator, and stale ledger execution fields.
- Conclusion unchanged but sharper: raw q250 remains a basis-risk control, not
  a deployment path. q250 YES-only is still the top paper-forward candidate,
  but the next real evidence step is explicit guarded paper-only
  start/restart, then fresh official-settled row-reconciled rows.

## 2026-05-18 - BTC1H replay coverage audit made sidecar-aware

- Investigated the BTC1H runner-up blocker. Current process state still has
  `scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py` running, but its
  active capture DB is locked by the live writer and the already-running
  process predates lock-free replay sidecars.
- Updated `scripts\btc_1hr_research_live.py` so future capture writers emit a
  lock-free `.replay.jsonl` sidecar alongside the DuckDB. The sidecar records
  replay-relevant `ws_orderbook_top`, `signal_scan`, `order_decision`, and
  `ws_lifecycle` rows as they are flushed.
- Updated `scripts\build_btc1h_replay_coverage_audit.py` to:
  - use the replay sidecar if the active DuckDB is locked;
  - batch coverage counts by source/table instead of issuing repeated
    per-trade count queries over large capture DBs;
  - avoid repeatedly retrying an already-known locked/no-sidecar source.
- Updated `scripts\check_btc_forward_shadow_status.py` so future status reports
  expose replay sidecar path, mtime, and row counts from the status sidecar.
- Validation:
  - `python -m py_compile scripts\btc_1hr_research_live.py
    scripts\build_btc1h_replay_coverage_audit.py
    scripts\check_btc_forward_shadow_status.py
    scripts\test_btc1h_replay_coverage_audit.py` passed;
  - `python -m unittest scripts.test_btc1h_replay_coverage_audit -v` passed
    `4 / 4`;
  - regenerated `btc1h_replay_coverage_audit_latest_codex`,
    `btc_forward_shadow_status_latest_codex`,
    `btc_forward_consistency_audit_latest_codex`,
    `btc_gpt_pro_action_status_latest_codex`, and GPT Pro packet
    `gpt_pro_packets\strategy_advisor_20260518_120236`.
- Current BTC1H evidence remains not deployable:
  - all rows: `13` paper rows, `3` replayable rows, `10` locked-blocked rows;
  - since freeze: `8` paper rows, `0` replayable rows, `8` locked-blocked rows;
  - action status still reports `btc1h_replay_coverage =
    BLOCKS_BTC1H_PROMOTION`.
- Conclusion: this is an infrastructure fix for future BTC1H validation, not
  new alpha. BTC1H remains observe-only until an explicitly authorized
  clean-schema restart produces a fresh replay sidecar and official-settled
  paper rows that can be row-reconciled.

## 2026-05-18 - Full refresh verified after BTC1H sidecar-aware audit

- Ran the full read-only controller after the BTC1H replay coverage changes:
  `python scripts\refresh_btc_evidence_stack.py --out-dir
  backtest_outputs\btc_evidence_stack_refresh_latest_codex`.
- Result: passed `30 / 30` from `2026-05-18T18:04:53Z` to
  `2026-05-18T18:08:09Z`. Readiness exit code `1` remained expected because
  `production_ready_count=0`.
- The current q250 YES-only path is structurally ready but not authorized or
  running:
  - restart authorization status
    `READY_FOR_USER_AUTHORIZATION_TO_START`;
  - restart path `PASS_RESTART_PATH_READY`;
  - fresh ledger schema `PASS_SCHEMA_READY`;
  - execution-realism insert preflight `PASS_INSERT_REALISM_FIELDS`;
  - fresh capture sidecar preflight `PASS_CAPTURE_SIDECAR`;
  - active ledger status remains `DB_MISSING` because it has not been started.
- Updated reusable workflow notes in the local Kalshi BTC skill so manual
  refresh order includes settlement-basis risk and guard-candidate steps, and
  documented the new BTC1H `.replay.jsonl` sidecar expectation.
- Conclusion unchanged: no deploy/canary. The honest next evidence step is
  still explicit guarded paper-only start/restart, after which future q250
  YES-only rows can be tested for official settlement, execution realism,
  row reconciliation, drawdown path quality, and sample size.

## 2026-05-18 - GPT Pro Chrome loop and guarded restart dry-run refresh

- Verified the Chrome extension path for the GPT Pro workflow using the `Sami`
  Chrome profile. ChatGPT was logged in, the composer was available, and the
  visible model control showed `Pro`.
- Submitted the latest strategy packet from
  `gpt_pro_packets\strategy_advisor_20260518_120807` to ChatGPT Pro by pasting
  the prompt plus inline `evidence_bundle.md`. ChatGPT converted the long paste
  into a pasted-text attachment and returned a review after `4m 32s`.
- Saved the exact browser-copied response to
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_chrome_exact_20260518_1222.md`.
  The review agrees with the local gates:
  - nothing is deployable now, including canary deployment;
  - `q250_firstskip_qty500_yes` is the top paper-forward path but has zero
    promotion-usable rows and is not running;
  - `q1000_yes` remains only a sparse cleaner control;
  - raw `q250_firstskip_qty500` is basis/side decomposition research, not a
    deployable both-side strategy;
  - BTC1H remains observe-only until clean schema, official rows, and causal
    replay coverage exist.
- Ran the guarded paper-shadow restart controller without `-Execute`:
  `powershell -NoProfile -ExecutionPolicy Bypass -File
  scripts\restart_btc_paper_shadows.ps1 -StartupWaitSec 1`.
  It wrote
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_121230\restart_plan.json`
  and explicitly reported dry run only; no processes were stopped or started.
- Re-checked matching Python processes after the dry run. The state remained:
  - `btc15m_live_capture.py` PID `1724`;
  - q250 raw shadow PID `7052`;
  - q1000 YES shadow PID `24840`;
  - BTC1H high-conf80 shadow PID `16216`;
  - q250 YES-only still not running.
- Rebuilt
  `backtest_outputs\btc_restart_authorization_packet_latest_codex` and
  `backtest_outputs\btc_gpt_pro_action_status_latest_codex`; they now point at
  the latest dry-run directory and still require explicit user authorization
  before any paper restart/start command may run.
- Validation:
  - `python -m unittest scripts.test_btc15m_shadow_config
    scripts.test_btc_paper_restart_safety -v` passed `14 / 14`;
  - `python -m unittest scripts.test_btc_settlement_basis_model_feasibility
    scripts.test_btc_settlement_basis_risk_audit -v` passed `4 / 4`.
- Conclusion unchanged: no deploy and no paper restart was executed. The only
  operational unlock is still explicit authorization for the guarded paper-only
  restart/start. Without that, the remaining honest work is read-only official
  settlement, basis-risk, replay, and ledger-validation research.

## 2026-05-18 - Predexon metadata-vs-REST source-fidelity audit added

- Ran the full read-only evidence refresh after adding a Predexon metadata
  source-fidelity check:
  `python scripts\refresh_btc_evidence_stack.py --out-dir
  backtest_outputs\btc_evidence_stack_refresh_latest_codex`.
  It passed `31 / 31` from `2026-05-18T18:32:40Z` to
  `2026-05-18T18:36:17Z`; readiness still returned the expected no-deploy
  exit code with `production_ready_count=0`.
- Added `scripts\audit_btc15m_predexon_metadata_vs_rest.py` and wired it into
  `scripts\refresh_btc_evidence_stack.py` plus the GPT Pro packet builder.
  The artifact is:
  `backtest_outputs\btc15m_predexon_metadata_vs_rest_latest_codex`.
- Result: Predexon metadata labels match Kalshi REST official labels on every
  overlapping row in the current materialized BTC15M Predexon set:
  - all rows: `1100` REST/metadata overlaps, `0` mismatches;
  - q250 first-skip qty>=500: `57` overlaps, `0` mismatches;
  - q250 YES: `31` overlaps, `0` mismatches;
  - q1000 YES: `23` overlaps, `0` mismatches.
- Interpretation: full-coverage Predexon metadata can be used as a historical
  research label when REST 404 leaves old rows uncovered, but metadata-only
  rows remain research diagnostics. They cannot replace live/paper
  REST-official settlement, row reconciliation, execution-realism fields, or
  the post-restart evidence clock for promotion.
- Validation:
  - `python -m py_compile scripts\audit_btc15m_predexon_metadata_vs_rest.py
    scripts\test_btc15m_predexon_metadata_vs_rest.py
    scripts\refresh_btc_evidence_stack.py scripts\build_gpt_pro_strategy_packet.py`
    passed;
  - `python -m unittest scripts.test_btc15m_predexon_metadata_vs_rest -v`
    passed `2 / 2`.
- Conclusion unchanged for deployment: no live/canary/paper promotion. This
  only strengthens the historical-research lane for future frozen candidate
  screening while preserving the live websocket and official REST gates.

## 2026-05-18 - Metadata-labeled grid and GPT Pro triage artifact

- Re-ran the BTC15M materialized first-signal grid on the REST-filled Predexon
  parquet with full-coverage Predexon metadata labels:
  `python scripts\audit_btc15m_materialized_filter_grid.py --predexon-trades
  backtest_outputs\btc15m_predexon_rest_official_latest_codex\predexon_trades_rest_official.parquet
  --pred-pnl-col pnl_predexon_metadata_2c --pred-win-col
  win_pnl_predexon_metadata_2c --min-pred-official-coverage 1.0 --out-dir
  backtest_outputs\btc15m_materialized_filter_grid_metadata_latest_codex`.
- Result: metadata labels preserve the same research-only shape as before:
  - `q250` first-signal qty>=500 both-side remains the strongest historical
    materialized row: `63` metadata-labeled Predexon trades, PnL `+8.31`,
    win `61.90%`, max DD `-1.97`, Sharpe `2.09`; live REST-official replay
    still has only `17` rows in the materialized grid table, PnL `+4.30`,
    win `76.47%`;
  - `q1000_yes` remains cleaner but too sparse: `33` metadata-labeled
    Predexon trades, PnL `+7.98`, win `69.70%`; live REST-official replay
    has only `7-8` rows depending on artifact cut and cannot pass sample
    gates;
  - higher-edge rows are even sparser and remain research-only.
- Added `scripts\audit_btc_strategy_triage.py`, a read-only GPT-Pro-facing
  triage builder. It merges the latest readiness, opportunity-rate,
  metadata-vs-REST, Predexon historical labels, and live REST-official replay
  into `backtest_outputs\btc_strategy_triage_latest_codex`.
- Full refresh after wiring the triage builder into
  `scripts\refresh_btc_evidence_stack.py` passed `32 / 32` from
  `2026-05-18T18:51:24Z` to `2026-05-18T18:55:44Z`. Readiness still returned
  the expected no-deploy code with `production_ready_count = 0`.
- Current triage verdict:
  - `q250_firstskip_qty500_yes` is the top paper-forward path, but is not
    running as a clean paper shadow. Evidence: `28` metadata-labeled
    historical rows, PnL `+4.44`, win `64.29%`; `24` REST-official historical
    overlap rows, PnL `+4.89`; live official replay `6` rows, PnL `+0.90`;
    post-freeze official rows `1`, PnL `+0.50`; projected about `60` days to
    `100` post-freeze official rows at current rate.
  - `q250_firstskip_qty500` is research-only/basis-decomposition: `63`
    metadata-labeled historical rows, PnL `+8.31`; `57` REST-overlap rows,
    PnL `+8.72`; live official replay `24` rows, PnL `+3.74`; but it has
    `3` official/proxy mismatches, stale ledger schema, no clean
    post-restart ledger evidence, settlement-basis/NO-side fragility, and was
    selected after prior live replay.
  - `q1000_yes` is a sparse control: `33` metadata-labeled historical rows,
    PnL `+7.98`; `23` REST-overlap rows, PnL `+6.45`; live official replay
    `8` rows, PnL `+0.90`; post-freeze official rows `1`, PnL `+0.50`; stale
    ledger schema and sample-size blockers remain.
  - Broad `q250`/`q500`/`q1000` remain rejected because official REST replay
    is negative: `-1.16`, `-1.14`, and `-1.31`, respectively.
  - BTC1H `high_conf_80_entry70_no_chase` remains observe-only: causal replay
    coverage and clean official-settled ledger evidence are still missing.
- The refreshed GPT Pro packet is
  `gpt_pro_packets\strategy_advisor_20260518_125541` and now includes the
  triage artifact plus metadata-vs-REST source-fidelity evidence.
- Validation:
  - `python -m py_compile scripts\audit_btc_strategy_triage.py
    scripts\refresh_btc_evidence_stack.py scripts\build_gpt_pro_strategy_packet.py`
    passed;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir
    backtest_outputs\btc_evidence_stack_refresh_latest_codex` passed `32 / 32`.
- Conclusion unchanged: no deployment, no canary, no broad q-strategy
  revival, and no BTC1H promotion. The only honest operational unlock remains
  explicit guarded paper-only start/restart so future rows can be counted with
  clean execution-realism fields and official settlement.

## 2026-05-18 - q250 YES first-signal side-semantics audit strengthened

- Updated `scripts\audit_btc15m_first_signal_side_semantics.py` so it no
  longer checks only the tiny post-freeze replay. It now compares both:
  - full old q250 first-skip replay vs q250 YES-only replay;
  - post-freeze q250 first-skip replay vs post-freeze q250 YES-only replay.
- Added explicit fields for:
  - side-filter extra events;
  - target-side global-first rows missing from the side replay;
  - replay config comparability and missing/mismatched config keys.
- Result from
  `backtest_outputs\btc15m_first_signal_side_semantics_latest_codex`:
  - full old replay: side-filtered q250 YES did not add events or wait past an
    earlier NO, but it is a strict subset: `24` global first rows, `8` global
    YES rows, `6` side-filtered YES rows, `2` global YES rows missing from the
    side replay. The old full comparison is not fully config-comparable because
    the old global q250 replay run info lacks `max_btc_spot_age_sec`
    provenance.
  - post-freeze replay: config-comparable and clean subset: `2` global rows,
    `1` side-filtered YES row, `0` side-filter extra events, `0` target-side
    missing rows.
- The two missing full-old global YES rows are:
  - `KXBTC15M-26MAY120815`, official/proxy PnL `+0.47`;
  - `KXBTC15M-26MAY161945`, official/proxy PnL `-0.49`.
  Net effect is approximately `-0.02`, so this does not rescue or kill q250
  YES, but it does make the full-old comparison diagnostic rather than a clean
  apples-to-apples proof.
- Full read-only refresh after this change passed `32 / 32` from
  `2026-05-18T19:05:36Z` to `2026-05-18T19:08:18Z`. Readiness still returned
  the expected no-deploy code with `production_ready_count = 0`.
- New GPT Pro packet:
  `gpt_pro_packets\strategy_advisor_20260518_130815`; it includes the
  strengthened side-semantics report.
- Validation:
  - `python -m py_compile scripts\audit_btc15m_first_signal_side_semantics.py
    scripts\test_btc15m_first_signal_side_semantics.py` passed;
  - `python -m unittest scripts.test_btc15m_first_signal_side_semantics -v`
    passed `4 / 4`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir
    backtest_outputs\btc_evidence_stack_refresh_latest_codex` passed `32 / 32`.
- Conclusion unchanged: q250 YES-only remains the top paper-forward BTC15M
  path only after explicit guarded paper-only start/restart. Old full-replay
  rows remain diagnostic, especially where config provenance is incomplete.

## 2026-05-18 - Full causal replay artifacts replaced stale full-window defaults

- Added and ran `scripts\refresh_btc15m_full_causal_replays.py`, a read-only
  full-window companion to the post-freeze replay refresher. It rebuilds the
  q250 first-skip, q250 first-skip YES-only, and q1000 YES live websocket
  replays with explicit `max_btc_spot_age_sec=10`, then REST-fills official
  Kalshi settlement.
- Latest artifact:
  `backtest_outputs\btc15m_full_causal_replay_refresh_latest_codex`.
  It completed successfully and produced:
  - `q250_firstskip_qty500`: `25` official rows, official PnL `+3.18`,
    win `64.00%`, proxy PnL `+7.18`, `4` proxy/official mismatches, and
    official-minus-proxy PnL `-4.00`;
  - `q250_firstskip_qty500_yes`: `7` official rows, official PnL `+1.40`,
    win `71.43%`, proxy PnL `+1.40`, and `0` proxy/official mismatches;
  - `q1000_yes`: `7` official rows, official PnL `+1.42`, win `71.43%`,
    proxy PnL `+1.42`, and `0` proxy/official mismatches.
- Updated downstream defaults so the full-window reports prefer the new causal
  full replay artifacts instead of the older full replay with incomplete run
  provenance:
  - `build_btc15m_frozen_opportunity_rate_report.py`;
  - `build_btc15m_shadow_replay_config_audit.py`;
  - `audit_btc15m_first_signal_side_semantics.py`;
  - `build_btc_execution_realism_audit.py`;
  - `analyze_btc15m_latest_live_replay_diagnostics.py`;
  - `build_btc15m_settlement_basis_watch.py`;
  - `audit_btc_strategy_triage.py`;
  - `check_btc_deployment_readiness.py`;
  - `build_gpt_pro_strategy_packet.py`;
  - `refresh_btc_evidence_stack.py`.
- Fixed a config-audit parser blind spot in
  `scripts\build_btc15m_shadow_replay_config_audit.py`: the audit now reads
  both `os.environ[...] = ...` assignments and `os.environ.setdefault(...)`.
  The previous failure was not actual wrapper drift; after the fix, all three
  BTC15M wrappers match their causal replay configs and the restart script
  includes the targets.
- Rebuilt dependent reports without executing any process restart:
  - `btc15m_shadow_replay_config_audit_latest_codex`: `0` failed config checks
    for q250 raw, q250 YES, and q1000 YES;
  - `btc15m_first_signal_side_semantics_latest_codex`: full causal q250 YES is
    a clean subset of full causal q250 raw (`25` global rows, `7` YES rows,
    `0` side-filter extra events, `0` missing target-side rows,
    config-comparable);
  - `deployment_readiness_latest_codex`: `production_ready_count=0`, with the
    latest live replay dirs now pointing at the causal REST-official artifacts;
  - `btc_strategy_triage_latest_codex`: q250 YES remains the top
    paper-forward path but is not running; q250 raw is still basis
    decomposition; q1000 YES remains sparse control; BTC1H remains observe-only.
- Tried to submit the refreshed
  `gpt_pro_packets\strategy_advisor_20260518_133949` packet through the Chrome
  extension. Chrome was reachable, logged into the `samisai Pro` account, and
  the composer showed `Pro`, but the extension pipe broke while pasting the
  full 1 MB bundle. The in-app browser fallback reached ChatGPT's login page,
  so no password/account flow was attempted. The packet remains ready for
  manual Pro paste/upload.
- Re-read the saved GPT Pro review
  `docs\gpt_pro_reviews\gpt_pro_strategy_advisor_chrome_exact_20260518_1222.md`.
  Its core recommendation still agrees with local evidence: no deploy/canary;
  the next operational unlock is explicit user authorization for the guarded
  paper-only restart/start so future q250 YES rows can count.
- Ran the guarded restart controller without `-Execute` only. It wrote
  `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_134328\restart_plan.json`
  and reported dry run only; no processes were stopped or started. The dry run
  includes q250 raw, q250 YES-only, q1000 YES, and BTC1H high-conf80 entry70
  no-chase, and all script safety checks pass.
- Current matching Python processes after the dry run are still:
  `btc15m_live_capture.py` PID `1724`, q250 raw shadow PID `7052`,
  q1000 YES shadow PID `24840`, and BTC1H high-conf80 shadow PID `16216`.
  q250 YES-only is still not running.
- Conclusion unchanged: no deploy and no paper restart was executed. The
  strongest honest next step is still explicit guarded paper-only
  start/restart, followed by official-settled row reconciliation on future
  clean-schema rows. Without that authorization, all current q250 YES and
  q1000 YES positives remain small-sample research evidence only.
- Full evidence-stack validation after wiring the causal full-window step into
  the controller passed `36 / 36` from `2026-05-18T19:45:55Z` to
  `2026-05-18T20:03:47Z`. The readiness step still returned the expected
  no-deploy code with `production_ready_count=0`. The refreshed GPT Pro packet
  is `gpt_pro_packets\strategy_advisor_20260518_140345`.

## 2026-05-18 - Browser-safe GPT Pro packet fallback added

- The full GPT Pro evidence bundle is now about 1 MB, which previously broke
  the Chrome extension paste pipe. Added a compact inline fallback to
  `scripts\build_gpt_pro_strategy_packet.py`:
  - `evidence_bundle_compact.md`: highest-signal artifacts only;
  - `browser_safe_prompt.md`: the normal review prompt plus compact evidence
    inline, intended for direct ChatGPT Pro paste when upload automation is
    unavailable.
- Added `-CopyBrowserSafe` to `scripts\ask_gpt_pro_strategy.ps1` so the
  fallback workflow is:
  `.\scripts\ask_gpt_pro_strategy.ps1 -CopyBrowserSafe -OpenChatGPT`.
- Updated `docs\gpt_pro_workflow.md` plus local reusable GPT Pro/Kalshi skill
  notes with the compact fallback.
- Generated and validated packet
  `gpt_pro_packets\strategy_advisor_20260518_140901`. Current size check:
  `browser_safe_prompt.md` is approximately `119k` characters, versus about
  `1.0M` for the full `evidence_bundle.md`.
- Also validated the real fallback command
  `powershell -NoProfile -ExecutionPolicy Bypass -File
  scripts\ask_gpt_pro_strategy.ps1 -CopyBrowserSafe`, which generated
  `gpt_pro_packets\strategy_advisor_20260518_140951` and copied the compact
  prompt without opening or submitting ChatGPT.
- Retried the Chrome extension after the prior oversized paste failure; the
  extension still returned a closed native pipe, so no GPT Pro submission was
  performed from Codex. The in-app browser is not logged into ChatGPT, and no
  password/account flow was attempted.
- Validation:
  - `python -m py_compile scripts\build_gpt_pro_strategy_packet.py` passed;
  - `powershell -NoProfile -ExecutionPolicy Bypass -File
    scripts\ask_gpt_pro_strategy.ps1` passed and printed the new
    browser-safe/compact paths;
  - `powershell -NoProfile -ExecutionPolicy Bypass -File
    scripts\ask_gpt_pro_strategy.ps1 -CopyBrowserSafe` passed.
- Conclusion unchanged: this improves the GPT Pro review loop, not strategy
  deployability. The current deployment blocker remains explicit paper-only
  restart/start plus future official-settled, execution-realistic row
  reconciliation.

## 2026-05-18 - Running-source freshness added to forward status

- Refreshed lightweight forward status and official settlement without
  restarting any process:
  - `python scripts\check_btc_forward_shadow_status.py --out-dir
    backtest_outputs\btc_forward_shadow_status_latest_codex --since-utc
    2026-05-18T02:42:00+00:00`;
  - `python scripts\check_btc_shadow_official_settlement.py --out-dir
    backtest_outputs\btc_shadow_official_settlement_latest_codex --since-utc
    2026-05-18T02:42:00+00:00`.
- Current process state remains `4/5` targets running:
  - BTC15M capture PID `1724`;
  - q250 raw shadow PID `7052`;
  - q1000 YES shadow PID `24840`;
  - BTC1H high-conf80 entry70 shadow PID `16216`;
  - q250 YES-only still not running.
- No new BTC15M paper fills appeared in this lightweight refresh. Current
  stale-schema paper official rows remain:
  - q250 raw shadow: `2` official rows since freeze, official PnL `+0.04`;
  - q1000 YES shadow: `1` official row since freeze, official PnL `+0.52`;
  - q250 YES-only: `0` rows because it is not running.
- BTC1H official paper rows remain superficially positive but not promotion
  usable:
  - all rows: `13` official rows, official PnL `+1.92`, `2` proxy/official
    mismatches;
  - since freeze: `10` official rows, official PnL `+1.02`, `1`
    proxy/official mismatch.
- Added running-source freshness fields to
  `scripts\check_btc_forward_shadow_status.py`:
  - `source_freshness_status`;
  - `source_latest_path`;
  - `source_latest_mtime_utc`;
  - `process_predates_latest_source`.
- Important result from
  `backtest_outputs\btc_forward_shadow_status_latest_codex`:
  - q250 raw shadow is `RUNNING_SOURCE_STALE_RESTART_REQUIRED`;
  - q1000 YES shadow is `RUNNING_SOURCE_STALE_RESTART_REQUIRED`;
  - BTC1H high-conf80 shadow is `RUNNING_SOURCE_STALE_RESTART_REQUIRED`,
    with latest source `scripts\btc_1hr_research_live.py`;
  - BTC15M capture is `RUNNING_SOURCE_CURRENT`;
  - q250 YES-only is `NOT_RUNNING_OR_PROCESS_TIME_MISSING`.
- This clarifies the BTC1H replay-sidecar blocker: the source code now has
  replay sidecar support, but the active BTC1H process started at
  `2026-05-18T02:41:59Z`, before the current
  `scripts\btc_1hr_research_live.py` mtime
  `2026-05-18T17:45:48Z`. Therefore the currently running BTC1H process cannot
  be assumed to emit the new sidecar until an explicitly authorized controlled
  restart.
- Rebuilt dependent read-only reports:
  - `btc_post_restart_collection_gate_latest_codex`;
  - `btc_post_restart_verification_latest_codex`;
  - `btc1h_replay_coverage_audit_latest_codex`;
  - `deployment_readiness_latest_codex`;
  - `btc_forward_evidence_report_latest_codex`;
  - `btc_forward_consistency_audit_latest_codex`;
  - `btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_141617`.
- Latest forward evidence report now exposes `source_freshness_status` in the
  forward shadow summary. It still says no deploy, with readiness
  `production_ready_count=0`.
- Validation:
  - `python -m py_compile scripts\check_btc_forward_shadow_status.py
    scripts\test_btc_forward_shadow_status.py
    scripts\build_btc_forward_evidence_report.py` passed;
  - `python -m unittest scripts.test_btc_forward_shadow_status -v` passed
    `3 / 3`.
- Conclusion unchanged: existing running processes are collection diagnostics
  only. The fact that they are running does not satisfy the current-code,
  sidecar, clean-schema, post-restart, row-reconciliation, official-settlement,
  or sample-size gates.

## 2026-05-18 - Source freshness promoted into readiness/action gates

- Tightened the deployment-control artifacts so running-source freshness is no
  longer just a status-table diagnostic. It is now a hard blocker in:
  - `scripts\check_btc_deployment_readiness.py`;
  - `scripts\build_btc_gpt_pro_action_status.py`;
  - `scripts\build_btc_forward_consistency_audit.py`;
  - `scripts\build_btc_kill_continue_report.py`.
- Refreshed forward status with the stable freeze timestamp:
  - `python scripts\check_btc_forward_shadow_status.py --out-dir
    backtest_outputs\btc_forward_shadow_status_latest_codex --since-utc
    2026-05-18T04:17:44Z`.
- Latest source-freshness gate outcome:
  - target shadows source-current: `0 / 4`;
  - stale or predating latest source: `3`;
  - not running or missing: `1`;
  - `running_source_freshness = BLOCKS_DEPLOYMENT`.
- Current promotion state remains unchanged:
  - `deployment_readiness_latest_codex` has `production_ready_count = 0`;
  - `btc_forward_consistency_audit_latest_codex` has
    `consistent_enough_for_promotion_count = 0 / 4`;
  - `btc_gpt_pro_action_status_latest_codex` still says `NO DEPLOY`.
- Ran the guarded paper-shadow restart script without `-Execute` only:
  - `powershell -ExecutionPolicy Bypass -File
    scripts\restart_btc_paper_shadows.ps1`;
  - it wrote
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_142812`
    and explicitly reported `Dry run only. No processes were stopped or
    started.`
- Rebuilt the restart authorization packet:
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`;
  - all four targets are ready for explicit paper-only authorization, but this
    still authorizes nothing by itself.
- Latest GPT Pro packet:
  - `gpt_pro_packets\strategy_advisor_20260518_142849`;
  - browser-safe prompt was prepared for fallback/manual ChatGPT Pro submission
    because callable Chrome automation is not currently available in this
    session.
- Conclusion unchanged and stricter: rows from the currently running q250 raw,
  q1000 YES, and BTC1H shadows cannot count toward promotion because those
  processes predate their latest source files. q250 YES-only remains the top
  paper-forward research path, but it is not running and has zero
  promotion-usable rows. No live deployment and no one-contract canary.

## 2026-05-18 - Freeze timestamp aligned for promotion-facing since counts

- Found and fixed a mixed-window evidence issue: the refresh controller and
  skill workflow still defaulted `--since-utc` to the paper process-start time
  `2026-05-18T02:42:00+00:00`, while the frozen promotion window is
  `2026-05-18T04:17:44Z`.
- Updated `scripts\refresh_btc_evidence_stack.py` so `DEFAULT_SINCE_UTC`
  equals `DEFAULT_FREEZE_UTC`, and updated the reusable
  `kalshi-btc-strategy-research` skill workflow to use
  `2026-05-18T04:17:44Z` for both forward-shadow status and shadow official
  settlement refreshes.
- Refreshed current read-only evidence with the corrected freeze timestamp:
  - `btc_forward_shadow_status_latest_codex`;
  - `btc_shadow_official_settlement_latest_codex`;
  - `btc_post_restart_collection_gate_latest_codex`;
  - `btc_drawdown_sequence_audit_latest_codex`;
  - `btc_forward_row_reconciliation_latest_codex`;
  - `btc_official_settlement_feature_table_latest_codex`;
  - `btc_settlement_basis_risk_audit_latest_codex`;
  - `btc1h_replay_coverage_audit_latest_codex`;
  - `deployment_readiness_latest_codex`;
  - `btc_forward_evidence_report_latest_codex`;
  - `btc_strategy_triage_latest_codex`;
  - `btc_kill_continue_latest_codex`;
  - `btc_forward_consistency_audit_latest_codex`;
  - `btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_143556`.
- Important corrected BTC1H result:
  - since-freeze BTC1H official rows are now `8`, not the older mixed-window
    `10`;
  - official PnL is `+0.40`, proxy PnL is `+1.40`;
  - there is `1` proxy/official mismatch;
  - drawdown/PnL ratio is `1.675`, so even the diagnostic path quality is
    weak.
- Added a readiness regression test proving that a candidate with otherwise
  passing execution/schema/reconciliation inputs is still blocked when the
  running process predates its source file:
  - `scripts\test_btc_readiness_row_reconciliation.py::test_running_shadow_predating_source_blocks_readiness`.
- Validated the refresh controller dry-run now emits the freeze timestamp for
  the first two dependency-order steps:
  - forward status uses `--since-utc 2026-05-18T04:17:44Z`;
  - shadow official settlement uses `--since-utc 2026-05-18T04:17:44Z`.
- Current conclusion remains no deploy:
  - `production_ready_count = 0`;
  - `consistent_enough_for_promotion_count = 0 / 4`;
  - source-current target shadows remain `0 / 4`;
  - q250 YES-only remains not running;
  - any paper-only restart/start still requires explicit user authorization.

## 2026-05-18 - Fee realism made explicit and latest Pro packet rebuilt

- Ran the full read-only evidence refresh:
  - `python scripts\refresh_btc_evidence_stack.py --out-dir
    backtest_outputs\btc_evidence_stack_refresh_latest_codex`;
  - all `36 / 36` steps completed, with deployment readiness return code `1`
    accepted as the expected no-deploy verdict.
- Re-checked processes before acting. Current matching Python processes remain:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PID `16216`.
- Strengthened fee/execution evidence:
  - `scripts\build_btc_execution_realism_audit.py` now reports
    `fee_present_rate`, `fee_nonnegative_rate`, `fee_mean`, `fee_max`,
    `actual_fee_official_pnl`, and `stressed_2c_official_pnl`;
  - negative or missing fees now add a direct `fee_missing_or_negative`
    blocker;
  - `scripts\check_btc_deployment_readiness.py` carries fee-realism fields
    into readiness and labels fee failures separately from missing schema
    fields;
  - `scripts\build_btc_kill_continue_report.py` surfaces fee-present and
    fee-nonnegative rates in the compact handoff table.
- Added regression coverage:
  - `scripts\test_btc_execution_realism_audit.py`;
  - verifies live replay fee fields and actual-fee versus 2c-stressed PnL;
  - verifies a negative ledger fee fails even when all other execution fields
    are present;
  - verifies readiness emits `shadow_ledger_fee_realism_not_passing` rather
    than a misleading missing-field reason for fee-only failures.
- Refreshed affected artifacts:
  - `btc_execution_realism_audit_latest_codex`;
  - `deployment_readiness_latest_codex`;
  - `btc_kill_continue_latest_codex`;
  - `btc_forward_evidence_report_latest_codex`;
  - `btc_strategy_triage_latest_codex`;
  - `btc_forward_consistency_audit_latest_codex`;
  - `btc_restart_authorization_packet_latest_codex`;
  - `btc_gpt_pro_action_status_latest_codex`.
- Latest execution-realism result:
  - causal live replay rows have fee-present and fee-nonnegative rates `1.0`;
  - q250 firstskip live replay: actual-fee official PnL `+3.68`,
    stressed-2c official PnL `+3.18`;
  - q250 YES-only live replay: actual-fee official PnL `+1.54`,
    stressed-2c official PnL `+1.40`;
  - q1000 YES live replay: actual-fee official PnL `+1.56`,
    stressed-2c official PnL `+1.42`;
  - active paper ledgers still fail because old schemas lack quote age, top
    visible quantity, quote/signal timestamps, and decision-time book columns.
- Ran the guarded paper-shadow restart workflow as a dry run only:
  - `powershell -ExecutionPolicy Bypass -File
    scripts\restart_btc_paper_shadows.ps1`;
  - wrote
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_145334`;
  - confirmed `execute = false`, capture process `btc15m_live_capture.py`
    would remain untouched, and all four target scripts pass safety checks.
- Latest action status:
  - `production_ready_count = 0`;
  - forward consistency promotion count `0 / 4`;
  - source-current target shadows `0 / 4`;
  - post-restart evidence clock is not started;
  - q250 YES-only remains the top Pro-ranked path, but it is not running and
    needs explicit user authorization before future rows can count.
- GPT Pro workflow status:
  - Chrome extension was discoverable, but tab control failed again with the
    same native-pipe failure pattern;
  - the in-app browser opens ChatGPT at `https://chatgpt.com/auth/login`, so
    it is not logged in;
  - fallback packet was built and copied with
    `scripts\ask_gpt_pro_strategy.ps1 -CopyBrowserSafe -OpenChatGPT`;
  - latest packet:
    `gpt_pro_packets\strategy_advisor_20260518_145903`.
- Conclusion unchanged: no BTC15M or BTC1H strategy is deployable. The only
  operational next step recommended by the current local gates and GPT Pro
  review is an explicitly authorized paper-only restart/start; without that,
  continue non-disruptive audit hardening and do not count stale active-ledger
  rows toward promotion.

## 2026-05-18 - GPT Pro action checklist now exposes fee/FOK realism gate

- Re-checked matching Python processes before this pass. Process state was
  unchanged:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PID `16216`.
- Added a standalone deployment-control check to
  `scripts\build_btc_gpt_pro_action_status.py`:
  - `fee_top_book_fok_execution_realism`;
  - consumes `btc_execution_realism_audit_latest_codex`;
  - reports replay-ready count, ledger-ready count, fee issue rows,
    top-book/FOK issue rows, and no-filled-ledger rows;
  - requires fee-present, nonnegative-fee, executable side ask,
    visible-size/FOK, quote-freshness, and one-trade-per-event evidence in
    both replay and ledger rows.
- Added regression coverage in `scripts\test_btc_gpt_pro_action_status.py`:
  - clean replay plus clean ledger passes the helper;
  - missing ledger top-visible/FOK fields block the helper;
  - empty ledger rows are counted as `no_filled_ledger_rows`, not mislabeled
    as fee failures.
- Rebuilt:
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_150444`.
- Latest action checklist now makes the blocker explicit:
  - `fee_top_book_fok_execution_realism = BLOCKS_DEPLOYMENT`;
  - replay-ready rows `3 / 3`;
  - ledger-ready rows `0 / 4`;
  - fee issue rows `0`;
  - top-book/FOK issue rows `3`;
  - no-filled-ledger rows `1`.
- Interpretation: live replay evidence currently proves fee and executable
  top-book realism for the replayed BTC15M rows, but no active paper ledger is
  deployable evidence. Three running old-schema ledgers lack quote age,
  visible quantity, and decision-time book fields; q250 YES-only has no filled
  ledger rows because it has not been started. This keeps the restart/start
  authorization path as the only honest next operational step, and still
  authorizes no live deployment.

## 2026-05-18 - q250 YES first-signal side semantics promoted to action gate

- Re-checked process state before this pass. It remained unchanged:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PID `16216`.
- Inspected `btc15m_first_signal_side_semantics_latest_codex`:
  - full replay: q250 YES-only side-filtered replay was a subset of global
    first-signal replay;
  - post-freeze replay: q250 YES-only side-filtered replay was also a subset;
  - side-filter extra events `0`;
  - global target-side events missing from side replay `0`;
  - global first-signal opposite-side events `19` across the two comparisons.
- Added a standalone q250 YES side-semantics check to
  `scripts\build_btc_gpt_pro_action_status.py`:
  - `q250_yes_first_signal_side_semantics`;
  - passes only when required q250 YES comparisons are present, config is
    comparable, and side-filtered first-signal replay adds no events beyond
    global first-signal replay.
- Added regression coverage in `scripts\test_btc_gpt_pro_action_status.py`:
  - clean YES subset semantics pass;
  - a side-filtered replay that waits past global first signal and adds events
    blocks q250 YES promotion.
- Rebuilt:
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_150855`.
- Latest action checklist:
  - `q250_yes_first_signal_side_semantics = REVIEW_REQUIRED`;
  - `subset_rows_passed = 2 / 2`;
  - `side_first_extra_events = 0`;
  - `global_target_side_missing = 0`;
  - `global_first_opposite_side_events = 19`.
- Interpretation: this supports q250 YES-only as the top paper-forward path
  because current side-filtered replay does not create extra hindsight-selected
  events. It still does not make q250 YES deployable: the shadow is not
  running, there are no promotion-usable ledger rows, and the paper-only
  restart/start path still requires explicit user authorization.

## 2026-05-18 - Post-restart verifier aligned with latest dry-run plan

- Re-checked process state before this pass. It remained unchanged:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PID `7052`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PID `16216`.
- Found an artifact-chain inconsistency:
  - `btc_restart_authorization_packet_latest_codex` and
    `btc_gpt_pro_action_status_latest_codex` referenced the latest dry-run
    restart plan
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_145334`;
  - `btc_post_restart_verification_latest_codex` still referenced the older
    dry-run plan
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_142812`.
- Strengthened `scripts\build_btc_gpt_pro_action_status.py` with a new
  `post_restart_verifier_alignment` checklist row:
  - compares the verifier's `restart_dir` to the restart authorization
    packet's `latest_restart_plan_dir`;
  - tells future runs to regenerate post-restart verification whenever the dry
    run or authorization packet changes.
- Added regression coverage in `scripts\test_btc_gpt_pro_action_status.py`:
  - `same_path_text` normalizes equivalent restart paths and rejects
    genuinely different restart plan directories.
- Rebuilt:
  - `backtest_outputs\btc_post_restart_verification_latest_codex`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_151225`.
- Latest alignment result:
  - `post_restart_verifier_alignment = REVIEW_REQUIRED`;
  - verifier restart dir and authorization latest restart plan dir both point
    to `btc_paper_shadow_controlled_restart_20260518_145334`;
  - `post_restart_evidence_clock` remains `BLOCKS_DEPLOYMENT` because no
    controlled restart/start was executed.
- Conclusion unchanged: this only fixes evidence coherence. There is still no
  deployable BTC15M or BTC1H strategy, and no stale active paper-ledger row can
  count toward promotion.

## 2026-05-18 - Process-hygiene blocker added after duplicate/unmanaged shadows appeared

- Re-checked matching Python processes after the latest packet refresh and the
  process state had changed:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PIDs `7052`, `10524`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PIDs `16216`, `4624`;
  - unmanaged matching BTC process
    `btc_1hr_high_conf80_no_chase_shadow.py` PID `10536`.
- No processes were killed or restarted in this pass.
- Strengthened `scripts\check_btc_forward_shadow_status.py`:
  - adds per-target `process_count`, `duplicate_process_count`, and
    `process_hygiene_status`;
  - writes `unmanaged_processes.csv`;
  - records `duplicate_target_process_count`,
    `duplicate_target_names`, and `unmanaged_matching_process_count` in
    `run_info.json`.
- Strengthened `scripts\build_btc_gpt_pro_action_status.py`:
  - adds a `process_hygiene` checklist row;
  - blocks deployment evidence when duplicate target PIDs or unmanaged BTC
    processes are present;
  - candidate status now prefers current shadow-status PIDs over older restart
    packet PID strings, so duplicate PIDs are visible in the handoff table.
- Rebuilt:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - `gpt_pro_packets\strategy_advisor_20260518_152356`.
- GPT Pro workflow follow-up:
  - Chrome extension discovery found the `Sami` Chrome profile, but tab access
    failed twice with the same closed native-pipe pattern;
  - used the resilient browser-safe fallback:
    `powershell -ExecutionPolicy Bypass -File scripts\ask_gpt_pro_strategy.ps1 -CopyBrowserSafe -OpenChatGPT`;
  - latest browser-safe packet copied/opened for manual submit:
    `gpt_pro_packets\strategy_advisor_20260518_152647`.
- Latest action checklist:
  - `process_hygiene = BLOCKS_DEPLOYMENT`;
  - duplicate target processes `2`;
  - unmanaged matching processes `1`;
  - duplicate targets
    `btc15m_q250_qty500_firstskip_shadow;btc1h_high_conf80_entry70_no_chase_shadow`.
- Validation:
  - `python -m py_compile scripts\check_btc_forward_shadow_status.py scripts\build_btc_gpt_pro_action_status.py scripts\test_btc_forward_shadow_status.py scripts\test_btc_gpt_pro_action_status.py`;
  - `python -m unittest scripts.test_btc_forward_shadow_status scripts.test_btc_gpt_pro_action_status scripts.test_btc_execution_realism_audit scripts.test_btc_readiness_row_reconciliation scripts.test_btc_forward_row_reconciliation -v`
    passed `24 / 24`.
- Conclusion unchanged but sharper: no BTC15M or BTC1H strategy is deployable.
  Future rows from the currently messy process set cannot count toward
  promotion until duplicate/unmanaged processes are resolved under explicit
  user authorization and the post-restart evidence clock starts cleanly.

## 2026-05-18 - Process hygiene promoted into readiness and consistency gates

- Re-checked matching Python processes. The messy process state persisted:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PIDs `7052`, `10524`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PIDs `16216`, `4624`;
  - unmanaged matching BTC process
    `btc_1hr_high_conf80_no_chase_shadow.py` PID `10536`.
- No processes were killed or restarted.
- Strengthened `scripts\check_btc_deployment_readiness.py`:
  - consumes `btc_forward_shadow_status_latest_codex\run_info.json`;
  - adds global readiness blockers
    `process_hygiene_duplicate_target_processes` and
    `process_hygiene_unmanaged_matching_processes`;
  - adds per-target readiness blocker
    `target_duplicate_processes_running`;
  - writes process-hygiene counts into `deployment_readiness_latest_codex`.
- Strengthened `scripts\build_btc_forward_consistency_audit.py`:
  - merges process-hygiene fields from authoritative `shadow_status.csv` even
    when using the forward-evidence summary;
  - marks q250 raw and BTC1H entry70 agreement as
    `duplicate_target_processes_running`;
  - carries unmanaged-process blockers into every candidate row.
- Strengthened `scripts\build_btc_forward_evidence_report.py`:
  - shows `pids`, `process_count`, `duplicate_process_count`, and
    `process_hygiene_status` in the human forward-shadow summary;
  - includes duplicate/unmanaged counts in the artifact inputs section.
- Regenerated:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`;
  - `backtest_outputs\deployment_readiness_latest_codex`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - browser-safe GPT Pro packet
    `gpt_pro_packets\strategy_advisor_20260518_153615`.
- Latest gate state:
  - readiness `production_ready_count = 0`;
  - readiness process-hygiene counts: duplicate target processes `2`,
    unmanaged matching processes `1`;
  - forward consistency `consistent_enough_for_promotion_count = 0 / 4`;
  - q250 raw and BTC1H entry70 are explicitly
    `duplicate_target_processes_running`;
  - q1000 YES is one process, but still fails stale-source, unmanaged-process,
    schema/execution, row-reconciliation, sample-size, and post-restart gates;
  - q250 YES-only is still not running and remains future paper-only
    collection, not promotion evidence.
- Validation:
  - `python -m py_compile scripts\check_btc_forward_shadow_status.py scripts\check_btc_deployment_readiness.py scripts\build_btc_forward_consistency_audit.py scripts\build_btc_forward_evidence_report.py scripts\build_btc_gpt_pro_action_status.py scripts\test_btc_forward_shadow_status.py scripts\test_btc_readiness_row_reconciliation.py scripts\test_btc_forward_consistency_audit.py scripts\test_btc_gpt_pro_action_status.py`;
  - `python -m unittest scripts.test_btc_forward_shadow_status scripts.test_btc_readiness_row_reconciliation scripts.test_btc_forward_consistency_audit scripts.test_btc_gpt_pro_action_status scripts.test_btc_execution_realism_audit scripts.test_btc_forward_row_reconciliation -v`
    passed `26 / 26`.
- Conclusion unchanged: no deploy, no canary. The current process set is not a
  clean evidence source. The only operational path remains an explicitly
  authorized guarded paper-only cleanup/restart/start; otherwise continue
  read-only analysis and wait for a saved GPT Pro review response.

## 2026-05-18 - Restart authorization packet hardened against stale/messy process state

- Re-confirmed the messy process state at 2026-05-18 21:46 UTC:
  - `btc15m_live_capture.py` PID `1724`;
  - `btc15m_f2_q250_qty500_firstskip_shadow.py` PIDs `7052`, `10524`;
  - `btc15m_f2_q1000_yes_shadow.py` PID `24840`;
  - `btc_1hr_high_conf80_entry70_no_chase_shadow.py` PIDs `16216`, `4624`;
  - unmanaged matching BTC process
    `btc_1hr_high_conf80_no_chase_shadow.py` PID `10536`.
- No processes were killed, restarted, archived, or migrated.
- Found that the older restart authorization packet was stale relative to the
  new process state: it still showed one PID for q250 raw and BTC1H entry70
  and reported `all_ready_for_user_authorization = true`.
- Hardened `scripts\build_btc_restart_authorization_packet.py`:
  - reads authoritative `btc_forward_shadow_status_latest_codex\shadow_status.csv`
    and `run_info.json` instead of relying only on a local process snapshot;
  - writes per-target `process_count`, `duplicate_process_count`,
    `process_hygiene_status`, duplicate cleanup flags, and unmanaged process
    counts;
  - copies `unmanaged_processes.csv` into the authorization packet;
  - marks every target
    `BLOCKED_UNMANAGED_PROCESS_DECISION_REQUIRED` while unmanaged matching
    BTC processes exist;
  - sets `all_ready_for_user_authorization = false`,
    `all_ready_for_clean_restart_authorization = false`,
    `ready_for_user_authorization_count = 0`, and
    `unmanaged_process_decision_required = true`.
- Hardened `scripts\restart_btc_paper_shadows.ps1`:
  - dry-run plan now records target `process_count`,
    `duplicate_process_count`, `process_hygiene_status`, and unmanaged
    matching BTC/Predexon Python processes;
  - execute path refuses to proceed while unmanaged matching processes remain
    unless the extra explicit acknowledgement
    `-IUnderstandUnmanagedBtcProcessesRemain` is supplied;
  - duplicate target processes are disclosed as cleanup by the guarded restart,
    not hidden in stale PID strings.
- Regenerated:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`;
  - dry-run plan
    `backtest_outputs\btc_paper_shadow_controlled_restart_20260518_154651`;
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`;
  - `backtest_outputs\btc_post_restart_verification_latest_codex`;
  - `backtest_outputs\deployment_readiness_latest_codex`;
  - `backtest_outputs\btc_forward_evidence_report_latest_codex`;
  - `backtest_outputs\btc_forward_consistency_audit_latest_codex`;
  - `backtest_outputs\btc_gpt_pro_action_status_latest_codex`;
  - browser-safe GPT Pro packet
    `gpt_pro_packets\strategy_advisor_20260518_154833`.
- Latest restart authorization state:
  - duplicate target process count `2`;
  - unmanaged matching process count `1`;
  - all four restart/start targets have
    `authorization_packet_status =
    BLOCKED_UNMANAGED_PROCESS_DECISION_REQUIRED`;
  - the guarded execute command is not ready while PID `10536` remains
    unmanaged.
- GPT Pro workflow status:
  - Chrome extension is discoverable on the `Sami` profile, but opening a new
    ChatGPT tab failed twice with `native pipe is closed`;
  - Codex in-app browser opened ChatGPT but was not logged in
    (`https://chatgpt.com/auth/login`);
  - latest browser-safe packet was copied/opened for manual Pro paste.
- Validation:
  - `python -m py_compile scripts\build_btc_restart_authorization_packet.py scripts\test_btc_paper_restart_safety.py`;
  - `python -m unittest scripts.test_btc_paper_restart_safety -v` passed
    `7 / 7`;
  - readiness exit code `1` remains expected because
    `production_ready_count = 0`.
- Conclusion unchanged but safer: no deploy, no canary, and not even a clean
  paper evidence restart/start packet until the unmanaged BTC1H process is
  explicitly handled. Future rows from the current process set cannot count
  toward promotion.

## 2026-05-18 - Refreshed replay evidence and added BTC15M candidate-overlap audit

- Ran the sequential evidence refresh. The first attempt exceeded the outer
  command timeout after completing through the BTC15M shadow replay config
  audit; the controller process continued briefly and then exited without
  writing final refresh metadata.
- Hardened `scripts\refresh_btc_evidence_stack.py` with resume controls:
  - `--start-at` accepts a 1-based step index or step name;
  - `--stop-after` bounds a refresh chunk;
  - resumed logs keep original step numbers and only selected old logs are
    cleaned.
- Resumed the refresh from `btc15m_candidate_overlap` through the GPT Pro
  packet. Latest resumed refresh:
  `backtest_outputs\btc_evidence_stack_refresh_latest_codex`, created
  `2026-05-18T22:30:17Z`, `all_ok = true`, steps `33-37` completed in the
  final tail refresh after triage was updated.
- Added `scripts\audit_btc15m_candidate_overlap.py` and artifact
  `backtest_outputs\btc15m_candidate_overlap_latest_codex`.
- Key overlap result:
  - full-causal q250 YES-only: `7` official events, official PnL `+1.40`,
    win rate `71.43%`, `0` proxy/official mismatches;
  - full-causal q1000 YES: `7` official events, official PnL `+1.42`, win
    rate `71.43%`, `0` proxy/official mismatches;
  - q250 YES-only vs q1000 YES official event overlap is `7 / 7` both ways,
    `overlap_status = identical_official_event_set`;
  - post-freeze q250 YES-only vs q1000 YES also overlaps `1 / 1` both ways.
- Interpretation: q1000 YES remains a useful stricter control, but it is not
  independent confirmation of q250 YES-only on the current live official replay
  sample. Do not double-count these rows as two separate evidence streams.
- Updated `scripts\audit_btc_strategy_triage.py`:
  - consumes `btc15m_candidate_overlap_latest_codex`;
  - adds overlap fields to `candidate_triage_summary.csv`;
  - marks q1000 YES with blocker
    `not_independent_from_q250_yes_current_replay_sample` when current
    full-causal official rows are identical to q250 YES-only.
- Updated `scripts\build_gpt_pro_strategy_packet.py` so GPT Pro packets include
  the overlap summary, pairwise overlap, event-membership table, and compact
  overlap evidence.
- Latest live/replay state after refresh:
  - full-causal q250 firstskip qty>=500: `25` official rows, official PnL
    `+3.18`, win rate `64%`, but includes `18` NO-side rows and `4`
    proxy/official mismatches;
  - full-causal q250 YES-only and q1000 YES remain tiny and non-independent;
  - post-freeze q250 raw has only `2` official rows and official PnL `0.00`;
  - post-freeze q250 YES-only and q1000 YES have only `1` official row each,
    both the same event.
- Re-checked the existing REST-official Predexon/materialized grid artifacts:
  - `btc15m_predexon_rest_official_latest_codex` filled `374 / 880` unique
    tickers from the combined Predexon trade file;
  - the q250 first-signal qty>=500 materialized row remains positive on scored
    REST-official Predexon rows (`57 / 63`, official PnL `+8.72`) and live
    official replay (`17` rows, `+4.30`), but fails because official coverage
    is incomplete (`6` selected rows missing official REST scores);
  - q1000 YES also remains positive but incomplete (`23 / 33` scored rows)
    and now overlaps the same current live official event set as q250 YES-only.
- Latest gates remain blocked:
  - `production_ready_count = 0`;
  - `promotion_usable_rows = 0`;
  - `process_hygiene_gate_pass = false`;
  - duplicate target processes `2`;
  - unmanaged matching process `1`;
  - `post_restart_evidence_clock_ready = false`;
  - BTC1H replay coverage still fails with `0 / 8` since-freeze replayable
    rows.
- Latest GPT Pro packet with overlap evidence:
  `gpt_pro_packets\strategy_advisor_20260518_163027`.
- GPT Pro workflow state:
  - Chrome extension path can open pages and Chrome ChatGPT is logged in;
  - in-app Browser ChatGPT is not logged in;
  - the browser-safe packet pasted as a long text attachment, but ChatGPT
    reported the Pro model is out of messages until `10:42 PM`, so the prompt
    was cleared and not submitted to the fallback `Thinking` model;
  - no new GPT Pro response exists for this overlap-enhanced packet yet.
- Validation:
  - `python -m py_compile` passed with `PYTHONPYCACHEPREFIX` redirected to
    `.codex_work\pycache_compile` after the normal pycache path hit a Windows
    access-denied rename;
  - focused unit tests passed `11 / 11`;
  - broader validation passed `44 / 44` across triage, evidence refresh,
    overlap, paper-restart safety, forward-shadow status, readiness row
    reconciliation, consistency/action-status, execution-realism, and row
    reconciliation tests.
- Conclusion unchanged: no deploy and no canary. The new evidence makes the
  ranking more honest: q250 YES-only remains the best paper-forward BTC15M
  direction, q1000 YES is currently the same official event set rather than
  independent validation, q250 raw is basis-decomposition research only, and
  BTC1H remains observe-only.

## 2026-05-18 - Cleaned BTC paper-shadow process set and started post-restart evidence clock

- User explicitly authorized auditing Python processes and stopping unneeded
  ones while preserving BTC15M/BTC1H websocket replay collection and any needed
  paper shadows.
- Process audit before cleanup:
  - kept BTC15M capture PID `1724`;
  - duplicate/stale BTC15M q250 firstskip PIDs `7052` and `10524`;
  - BTC15M q1000 YES PID `24840`;
  - duplicate/stale BTC1H entry70 no-chase PIDs `16216` and `4624`;
  - unmanaged secondary BTC1H no-chase PID `10536`.
- Stopped unmanaged secondary BTC1H no-chase PID `10536` because it was not the
  frozen runner-up target and blocked clean process hygiene.
- Ran the guarded paper-only restart workflow:
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1 -Execute -IUnderstandThisRestartsPaperShadows`.
  The workflow intentionally left BTC15M capture running and restarted/started
  only the needed paper shadows.
- Clean post-restart process set:
  - BTC15M capture: PID `1724`;
  - BTC15M q250 firstskip qty>=500: PID `6748`;
  - BTC15M q250 firstskip qty>=500 YES-only: PID `3284`;
  - BTC15M q1000 YES: PID `17620`;
  - BTC1H high_conf80 entry70 no-chase: PID `14968`.
- Latest process hygiene:
  - running targets `5 / 5`;
  - duplicate target processes `0`;
  - unmanaged matching processes `0`;
  - all target shadows `RUNNING_SOURCE_CURRENT`;
  - BTC1H replay sidecar exists and is updating.
- Latest post-restart verification:
  - `restart_executed = true`;
  - `evidence_clock_ready = true`;
  - BTC15M capture untouched and running;
  - all target process identities match `restart_result.json`;
  - required capture sidecars ready;
  - active ledger schemas ready with all execution-realism columns.
- Latest post-restart collection gate:
  - `NO_POST_RESTART_ROWS` for all four paper candidates;
  - BTC15M candidates still need `100` official-settled post-restart rows each;
  - BTC1H needs `50` official-settled post-restart rows;
  - deployment readiness remains failing with `production_ready_count = 0`.
- Fixed verifier edge cases discovered immediately after the clean restart:
  - `check_btc_shadow_official_settlement.py` now writes headers for empty
    `shadow_official_trades.csv`;
  - `build_btc_post_restart_collection_gate.py` now accepts empty trade CSVs
    and PowerShell UTF-8-BOM restart JSON, and handles empty post-restart
    trade tables without timezone comparison crashes;
  - `build_btc_post_restart_verification.py` no longer labels an executed,
    safety-checked restart plan as missing.
- Validation:
  - `python -m py_compile` passed for the touched verifier scripts with
    `PYTHONPYCACHEPREFIX=.codex_work\pycache_compile`;
  - `python -m unittest scripts.test_btc_shadow_official_empty_outputs scripts.test_btc_post_restart_verification -v`
    passed `11 / 11`.
- Conclusion: collection plumbing is finally in the right state, but there are
  zero promotion-valid post-restart fills so far. Let the shadows run; only
  rows after `2026-05-18T16:54:17.2841564-06:00` can count toward future
  promotion evidence.

## 2026-05-18 - Moved BTC collectors to always-on laptop and fixed capture writer throughput

- User authorized moving the BTC15M/BTC1H websocket capture plus required
  paper shadows from this laptop to `ClawService@100.92.9.80`, while leaving
  OpenClaw alone and not deploying live trading.
- Remote host:
  - machine/user observed as `DESKTOP-STI60DH\ClawService`;
  - repo deployed to `C:\Users\ClawService\Kalshi-Trading-Bot`;
  - Python used for collectors: `C:\Python310\python.exe` with the repo venv
    site-packages on `PYTHONPATH`;
  - C: drive checked with `.NET DriveInfo`: `930.91 GB` total, `785.77 GB`
    free, `NTFS`, so storage is currently adequate for collection.
- Performance issue found:
  - the old shared `LiveCaptureWriter` used row-by-row DuckDB `executemany`;
  - local microbench for 10,000 `ws_orderbook_top` rows took about `54.7s`
    before the rewrite;
  - vectorized DuckDB insert through a pandas DataFrame plus batched replay
    sidecar writes reduced the same local benchmark to about `1.7s`;
  - this preserves the existing DuckDB/replay-sidecar schema and falls back to
    `executemany` if the vectorized path fails.
- Added/updated remote management tooling:
  - `scripts\deploy_btc_collectors_remote.py`;
  - `scripts\start_btc_remote_collectors.ps1`;
  - `scripts\stop_btc_remote_collectors.ps1`;
  - `scripts\status_btc_remote_collectors.ps1`;
  - `scripts\audit_btc_runner_performance.py`.
- Remote clean evidence clock:
  - stopped the trial remote collectors;
  - archived trial remote DBs/sidecars under
    `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\archives\pre_clean_vectorized_20260518_181901`;
  - confirmed no remaining remote Python collectors before the final clean
    start;
  - final clean remote manifest created at `2026-05-19T00:19:19.3575434Z`.
- Final remote process set after clean start:
  - BTC15M capture PID `5840`;
  - BTC15M q250 firstskip qty>=500 PID `5148`;
  - BTC15M q250 firstskip qty>=500 YES-only PID `7008`;
  - BTC15M q1000 YES PID `7888`;
  - BTC1H high_conf80 entry70 no-chase PID `8964`.
- Final remote performance audit
  `backtest_outputs\btc_runner_performance_remote_latest_codex`:
  - running targets `5 / 5`;
  - total CPU load `64.88%` of one core;
  - total working set `568.8 MB`;
  - no dropped capture rows;
  - sidecar queues were small: BTC15M capture `5`, q250 `33`, q250 YES `3`,
    q1000 YES `2`, BTC1H `0`;
  - max queue depths after clean start were all low: `59`, `57`, `50`, `67`,
    and `11`;
  - last flush times were roughly `93-250 ms`;
  - fresh remote stderr logs were all `0` bytes.
- Final local process cleanup:
  - stopped local PIDs `1724`, `6748`, `3284`, `17620`, and `14968`;
  - rechecked local matching Kalshi/BTC/Predexon Python processes and found
    none remaining.
- Validation:
  - `python -m py_compile scripts\btc_1hr_research_live.py scripts\btc15m_live_capture.py scripts\btc15m_lowdd_live.py scripts\audit_btc_runner_performance.py` passed;
  - `python -m unittest scripts.test_btc15m_shadow_config scripts.test_research_live_safety -v`
    passed `57 / 57`.
- Deployment conclusion unchanged:
  - no strategy is production-ready;
  - this was an infrastructure/performance migration for paper-only forward
    evidence collection;
  - only future official-settled rows from the clean remote run should count
    toward promotion evidence, and the existing promotion gates still require
    enough official-settled post-restart BTC15M/BTC1H rows.

## 2026-05-21 - Remote DuckDB snapshot parity audit

- User authorized a brief pause/resume of the remote BTC collectors to copy
  promotion-grade DuckDB snapshots instead of relying on lossy `.replay.jsonl`
  sidecars.
- Remote maintenance window:
  - stopped only the five `KalshiBTC_*` BTC collection/shadow tasks on
    `ClawService@100.92.9.80`;
  - copied active DuckDBs and WALs to
    `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\duckdb_snapshots\snapshot_20260521_145951`;
  - restarted the same five tasks; new PIDs were BTC15M capture `3852`,
    q250 firstskip `10324`, q250 YES `5532`, q1000 YES `6992`, and BTC1H
    high_conf80 entry70 no-chase `6408`;
  - pause duration was `106.505s`;
  - post-restart health check showed all five processes alive, fresh status
    timestamps around `2026-05-21T15:18Z`, zero dropped rows, and low queues.
- Snapshot contents were readable with raw `coinbase_ticker` tables, so this
  removed the earlier sidecar limitation. Example row counts:
  - q250 capture: `5,529,871` `ws_orderbook_top`, `60,581`
    `coinbase_ticker`, `2,938,325` `signal_scan`, `9` `order_decision`;
  - q250 YES capture: `5,542,730` top, `60,579` Coinbase, `2,954,660`
    signal scans, `2` order decisions;
  - q1000 YES capture: `5,540,396` top, `60,578` Coinbase, `2,954,732`
    signal scans, `3` order decisions;
  - BTC1H capture: `2,273,693` top, `60,569` Coinbase, `480,791` signal
    scans, `13` order decisions.
- Re-ran the independent BTC15M vector replay on the real snapshots:
  - artifacts:
    `backtest_outputs\remote_snapshot_q250_firstskip_qty500_rest_20260521_145951`,
    `backtest_outputs\remote_snapshot_q250_firstskip_qty500_yes_rest_20260521_145951`,
    `backtest_outputs\remote_snapshot_q1000_yes_rest_20260521_145951`,
    and
    `backtest_outputs\remote_snapshot_row_reconciliation_20260521_145951`;
  - q250 all-side paper `9` rows vs replay `4` rows, matched `4`,
    paper official PnL `-$0.34`, replay official actual-fee PnL `+$1.19`;
  - q1000 YES paper `3` rows vs replay `2`, matched `2`, paper official PnL
    `+$0.50`, replay actual-fee PnL `+$0.01`;
  - q250 YES-only paper `2` rows vs replay `0`;
  - conclusion: the independent replay implementation is still not faithful
    enough for promotion-grade backtesting.
- Checked captured live-decision parity directly against the paper ledgers:
  artifact
  `backtest_outputs\remote_snapshot_orderdecision_parity_20260521_145951`.
  Captured `order_decision` rows matched ledger rows `1:1` by market/side and
  entry price for q250 (`9/9`), q250 YES (`2/2`), q1000 YES (`3/3`), and BTC1H
  (`11/11`). This means the gathered DuckDB/ledger data is internally
  consistent; the current failure is the offline replay model/scan semantics,
  not bad raw collection.
- Current official-settled forward evidence since the clean remote start:
  - q250 all-side: `9` official rows, official PnL `-$0.34`, win `44.44%`;
  - q250 YES-only: `2` official rows, official PnL `+$0.01`, win `50.00%`;
  - q1000 YES: `3` official rows, official PnL `+$0.50`, win `66.67%`;
  - BTC1H high_conf80 entry70 no-chase: `11` official rows, official PnL
    `+$0.50`, win `72.73%`, with `1` proxy/official mismatch.
- Deployment conclusion unchanged: no BTC15M or BTC1H strategy is production
  ready. Do not run broad historical promotion claims from the current
  vectorized BTC15M replay until it reproduces captured `signal_scan` /
  `order_decision` semantics row-for-row.

## 2026-05-21 late / 2026-05-22 UTC - Remote reboot recovery and safe parity guard

- The always-on laptop rebooted, so the remote BTC collectors/shadows had to be
  rechecked and restarted on `ClawService@100.92.9.80`.
- Root causes found and fixed:
  - stale PID/status files and Windows PID reuse made old stop/status logic
    unsafe;
  - venv launcher PIDs were being confused with the actual long-running
    `C:\Python310\python.exe` workers;
  - capture writer startup could block on DB-wide status seeding scans over
    multi-GB DuckDBs before fresh heartbeats were written;
  - the BTC15M capture-only DuckDB WAL was corrupt and was quarantined under
    `C:\Users\ClawService\.btc_kalshi_bot\corrupt_archive_20260521_203951`.
- Operational fixes deployed to the remote:
  - `scripts\btc_1hr_research_live.py` now seeds lock-free status from the
    previous sidecar by default, only scans DuckDB status counters when
    `BTC_CAPTURE_SEED_STATUS_FROM_DB=1`, and writes heartbeat/status sidecars
    even when no rows are flushed;
  - remote start/status/stop scripts now prefer direct `C:\Python310\python.exe`
    workers with the venv site on `PYTHONPATH`, trust fresh capture-status PIDs,
    validate process type before stopping, and avoid scanning old timestamped
    PID files that can point at reused system PIDs;
  - added `scripts\ensure_btc_remote_collectors.ps1` for idempotent remote
    health/restart checks;
  - added `scripts\build_btc_shadow_decision_log_parity.py`, but with sidecar
    fallback disabled by default so it cannot accidentally scan multi-GB replay
    JSONL files.
- Current remote health check at `2026-05-22T02:51:07Z`:
  - status script returned `ALL_RUNNING`;
  - running workers: BTC15M capture PID `9104`, q250 all-side PID `6748`,
    q250 YES PID `10076`, q1000 YES PID `10236`, BTC1H high-conf PID `5132`;
  - fresh sidecar timestamps around `2026-05-22T02:51Z`, zero dropped rows;
  - queues were small: BTC15M capture `3`, q250 all-side `13`, q250 YES `6`,
    q1000 YES `17`, BTC1H `61`.
- Latest remote official-settled paper evidence from
  `backtest_outputs\btc_shadow_official_settlement_latest_codex`:
  - BTC15M q250 first-signal qty>=500 all-side: `10` official rows, official
    PnL `+$0.15`, win `50.00%`, trade Sharpe `0.0886`, max DD `-$1.96`;
  - BTC15M q250 YES-only: `2` official rows, official PnL `+$0.01`, win
    `50.00%`, trade Sharpe `0.0099`, max DD `-$0.50`;
  - BTC15M q1000 YES: `3` official rows, official PnL `+$0.50`, win
    `66.67%`, trade Sharpe `0.5074`, max DD `-$0.49`;
  - BTC1H high_conf80 entry70 no-chase: `11` official rows, official PnL
    `+$0.50`, win `72.73%`, trade Sharpe `0.3155`, max DD `-$1.76`, with
    `1` proxy/official result mismatch.
- Interpretation:
  - the remote collection/shadows are back up, but no strategy is production
    ready;
  - q250 all-side is basically flat after fees on the official forward sample,
    with drawdown much larger than total PnL;
  - q1000 YES and BTC1H are positive but far too small to trust;
  - active DuckDB files are locked by live writers, so row-for-row decision
    parity still requires the authorized pause/snapshot workflow. The safe
    active-run parity artifact at
    `backtest_outputs\btc_shadow_decision_log_parity_latest_codex` reports the
    lock clearly and attaches official ledger PnL without scanning huge
    sidecars.

## 2026-05-22 - Pivot active research loop to BTC1H

- Decision: BTC15M remains passive shadow/collection only for now; active
  research focus moves to BTC1H.
- Remote status at `2026-05-22T03:02:44Z`:
  - all five collectors/shadows running on `ClawService@100.92.9.80`;
  - BTC15M shadows remain running;
  - BTC1H `btc1h_high_conf80_entry70_no_chase_shadow` running with PID `5132`;
  - BTC1H sidecar fresh at `2026-05-22T03:02:43Z`, queue `0`, dropped `0`;
  - BTC1H capture counts: `2,628,391` `ws_orderbook_top`, `571,922`
    `signal_scan`, `14` `order_decision`.
- Refreshed remote official settlement at `2026-05-22T03:06:38Z`.
  BTC1H official-settled evidence:
  - `11` official rows;
  - official PnL `+$0.50`;
  - proxy PnL `+$1.50`;
  - official-minus-proxy PnL `-$1.00`;
  - official win `72.73%`;
  - trade Sharpe `0.3155`;
  - max drawdown `-$1.76`;
  - `1` proxy/official result mismatch.
- Re-ran BTC1H robustness audit:
  `backtest_outputs\btc1h_highconf_robustness_20260521_210410`.
  At +2c adverse stress:
  - `high_conf_80_entry70_no_chase`: Predexon `30` trades, `+$4.26`,
    win `80.0%`, max DD `-$1.41`, Sharpe `1.9384`;
  - all six May websocket cadences remained positive, worst `+$0.75`.
- Latest promotion gate artifact:
  `backtest_outputs\btc1h_promotion_gate_audit_latest_codex`.
  No BTC1H candidate passes. The current best candidate still fails due to too
  few post-freeze settled rows and because `entry70_no_chase` was a research
  extra gate that requires forward shadow evidence.
- Settlement-basis diagnostics with remote official rows:
  - `backtest_outputs\btc1h_settlement_basis_risk_remote_latest_codex`;
  - `backtest_outputs\btc1h_basis_danger_remote_latest_codex`;
  - `backtest_outputs\btc1h_basis_guard_candidates_remote_latest_codex`.
  BTC1H has too few official rows for a deployable guard. The current warning is
  one NO-side proxy-win / official-loss row and large basis magnitude:
  p95 absolute basis about `$79.77` on NO-side rows.
- Created `docs\2026-05-22_btc1h_research_loop.md`.
- Current BTC1H research posture:
  - primary candidate stays `high_conf_80_entry70_no_chase`;
  - do not retune or launch a new variant yet;
  - collect to at least `50` official-settled rows;
  - investigate settlement-distance/basis risk before changing model features;
  - use pause/snapshot DuckDB parity for promotion-grade replay evidence.

## 2026-05-22 - BTC1H fixed multi-holdout research artifact

- Goal set in Codex: research BTC1H Kalshi strategies through faithful
  backtesting on available data, preserve multiple holdout sets, and identify
  deployable or near-deployable candidates without relaxing official
  settlement, execution, and live-replay gates.
- Added `scripts\build_btc1h_multi_holdout_research.py` to consolidate
  already-frozen BTC1H variants across fixed evidence buckets. This is not a
  threshold search and does not use the latest forward rows to tune a new
  rule.
- Artifact:
  `backtest_outputs\btc1h_multi_holdout_research_latest_codex`.
  Inputs:
  - `backtest_outputs\btc1h_highconf_robustness_20260521_210410\all_input_trades.csv`;
  - `backtest_outputs\btc1h_highconf_direct_aggregate_feb09_may06_20260516_0433\trades.csv`;
  - `backtest_outputs\btc1h_entry59_70_derived_20260516_0241\derived_trades.csv`;
  - `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex\shadow_official_trades.csv`.
- Historical/proxy rows were restressed with a `+2c` adverse entry assumption.
  Forward rows use actual REST-official settlement and actual ledger premium.
- Candidate summary:
  - `high_conf_80_entry70_no_chase` is the only active forward BTC1H
    candidate. It was positive in `14 / 14` fixed holdout buckets, positive in
    all `6 / 6` May live-websocket cadence buckets, and has `11`
    official-settled forward rows with official PnL `+$0.50`, win `72.73%`,
    max DD `-$1.76`, and official/proxy mismatch rate `9.09%`.
  - `high_conf_80_entry59_70_no_chase` is historical-only promising, with
    `12 / 13` positive buckets and all `6 / 6` websocket cadences positive, but
    it has no forward official rows and one negative direct holdout
    (`H2b_direct_apr23_may01_holdout`).
  - `high_conf_80` is historical-only promising but less stable, with `8 / 9`
    positive buckets and a negative `H3_predexon_may03_06_external` bucket.
  - `high_conf_80_no_chase` should stay watch/reject for now despite strong
    historical totals because it failed the `H4_live_ws_may06_12_stride1s`
    live-websocket cadence bucket.
- Deployability conclusion:
  - `deployable_now = False` for every BTC1H variant;
  - `production_ready_count` remains `0`;
  - the best BTC1H candidate is near-deployable research only, not a live
    strategy.
- Current blockers for `high_conf_80_entry70_no_chase`:
  - only `11` official-settled forward rows versus the minimum `50` row gate;
  - official/proxy mismatch rate `9.09%`, above the `2%` gate;
  - settlement basis has already reduced the BTC1H forward sample by `-$1.00`;
  - promotion still needs snapshot row parity and full execution-realism checks
    on executable ask, spread, visible size, FOK/no-fill behavior, fees, and
    live/paper ledger agreement.
- Next honest loop:
  - keep the current BTC1H shadow and BTC15M passive shadows running;
  - refresh official settlement daily or on manual review;
  - do not retune a basis/distance guard from the current `11` rows;
  - after enough new official rows, use a fresh pause/snapshot DuckDB parity
    window before counting replay evidence toward promotion.
- Remote status recheck at `2026-05-22T03:28:02Z`: `ALL_RUNNING`.
  BTC1H `btc1h_high_conf80_entry70_no_chase_shadow` PID `5132` was alive, and
  BTC15M capture plus the three BTC15M paper shadows were also alive.

## 2026-05-22 - BTC1H forward snapshot signal/decision parity

- Refreshed remote REST-official shadow settlement at
  `2026-05-22T03:42:10Z` and copied the small output artifacts back to
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`.
  BTC1H official-settled rows remained unchanged:
  - `11` rows;
  - official PnL `+$0.50`;
  - proxy PnL `+$1.50`;
  - official-minus-proxy PnL `-$1.00`;
  - official win `72.73%`;
  - max DD `-$1.76`;
  - `1` proxy/official mismatch.
- The active remote BTC1H DuckDB was correctly locked by the running writer, so
  no active DB copy/read was treated as promotion evidence. Used the already
  paused May 21 snapshot instead:
  `runtime\remote_snapshots\snapshot_20260521_145951`.
- Copied BTC1H snapshot files locally:
  - `btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb`
    (`284,962,816` bytes);
  - matching `.wal` (`14,025,116` bytes);
  - paper ledger SQLite DB (`20,480` bytes);
  - `snapshot_manifest.json`.
- Added/updated audit tooling:
  - `scripts\build_btc_shadow_decision_log_parity.py` now accepts explicit
    `--target LEDGER=CAPTURE_DB` overrides so paused remote snapshots can be
    audited without replacing local live paths;
  - `scripts\build_btc1h_forward_snapshot_signal_audit.py` verifies the actual
    BTC1H forward chain from captured selected `signal_scan` rows to captured
    `order_decision` rows to official-settled paper fills;
  - `scripts\build_btc1h_multi_holdout_research.py` now consumes the forward
    snapshot fidelity summary and no longer labels the current 11 BTC1H rows as
    missing snapshot parity when that parity has passed.
- Snapshot parity artifacts:
  - `backtest_outputs\btc1h_snapshot_decision_log_parity_latest_codex`:
    `11 / 11` BTC1H paper-fill decisions matched official ledger rows, with
    zero missing and zero extra fill rows; official PnL `+$0.50`, win
    `72.73%`, max DD `-$1.76`, mismatch count `1`;
  - `backtest_outputs\btc1h_snapshot_replay_coverage_latest_codex`:
    `11 / 11` official BTC1H rows were replayable from readable snapshot
    capture, with top-of-book and signal-scan rows around every paper fill;
  - `backtest_outputs\btc1h_forward_snapshot_signal_audit_latest_codex`:
    `13` selected signals matched `13` decisions, `11` were paper fills and
    `2` were websocket-reprice skips, and `11 / 11` fills matched official
    settlement rows.
- Forward snapshot daily official holdouts:
  - `2026-05-19`: `4` rows, official PnL `+$0.22`, mismatch rate `0%`;
  - `2026-05-20`: `4` rows, official PnL `-$0.63`, mismatch rate `25%`;
  - `2026-05-21`: `3` rows, official PnL `+$0.91`, mismatch rate `0%`.
- Updated multi-holdout artifact:
  `backtest_outputs\btc1h_multi_holdout_research_latest_codex`.
  The primary BTC1H candidate now shows
  `forward_snapshot_parity_status = pass_actual_shadow_snapshot_parity`, but
  `deployable_now = False` with blockers:
  `too_few_forward_official_rows`,
  `official_proxy_mismatch_gate_failed`, and
  `needs_full_counterfactual_replay_or_live_execution_gate`.
- Attempted the broad BTC1H signal-scan-clock counterfactual replay on the
  snapshot with `scripts\replay_btc1h_core_ws_counterfactual.py`. It remained
  running for more than ten minutes and was stopped locally. Do not treat that
  replay path as promotion-usable until it is optimized and can complete with a
  row-reconciliation report.
- Deployment conclusion unchanged:
  - no BTC1H or BTC15M strategy is production ready;
  - `high_conf_80_entry70_no_chase` remains the only BTC1H active
    near-deployable research candidate;
  - the current positive forward PnL is too small and too basis-sensitive to
    deploy.
- Remote worker status rechecked at `2026-05-22T04:11:06Z`: `ALL_RUNNING`.
  BTC1H PID `5132` and all BTC15M capture/shadow workers remained alive.

## 2026-05-22 - BTC1H selected-signal model parity blocker

- Added `scripts\build_btc1h_selected_signal_model_parity.py`.
  It recomputes the active BTC1H live strategy only at captured `selected`
  `signal_scan` rows using the selected market's as-of websocket top book and
  an as-of BTC candle cache. This is an actual-selected-row diagnostic, not a
  full all-window counterfactual replay.
- Pulled the remote BTC candle cache from
  `C:\Users\ClawService\Kalshi-Trading-Bot\data\btc_1m_research_live_cache.parquet`
  into
  `runtime\remote_snapshots\snapshot_20260521_145951\btc_1m_research_live_cache.remote.parquet`.
  The local cache was stale for this window, ending at
  `2026-05-19T00:21:00Z`.
- Artifact:
  `backtest_outputs\btc1h_selected_signal_model_parity_latest_codex`.
  Result:
  - `13` selected signal rows;
  - `0` selected model parity passes;
  - max model probability absolute drift `0.032348`;
  - max net-edge absolute drift `3.234823c`;
  - `4` selected rows did not recompute as signals.
- Follow-up diagnosis:
  - top-book side/entry parity passed, so the selected trade prices are
    supported by captured websocket top-of-book rows;
  - exact model replay using scan-time TTL still fails;
  - implied cached-TTL reconciliation passes on `13 / 13` selected rows, with
    max probability residual `0.000239` and implied TTL offset range `-178` to
    `+162` seconds;
  - official impact split:
    - `9` official fills still reproduce as scan-time-TTL signals, official PnL
      `+$0.93`;
    - `2` official fills are cached-TTL-only under this diagnostic, official
      PnL `-$0.43`;
  - the likely missing field is the exact cached `ttl_min` from the live event
    object, not the top-of-book row or broad BTC cache.
- Interpretation:
  - actual signal/decision/ledger parity still supports the 11 filled rows;
  - independent model-recomputed replay is diagnosable but not promotion-usable
    when it must infer cached TTL after the fact;
  - the current forward PnL cannot be treated as clean evidence for the exact
    scan-time-TTL policy, even though the cached-TTL-only official rows were net
    negative in this tiny sample.
- Updated `scripts\build_btc1h_multi_holdout_research.py` to consume the new
  selected-signal model parity summary. The active BTC1H candidate now carries
  the additional blocker
  `selected_signal_model_parity_failed_or_exact_btc_cache_missing`.
- Updated `scripts\btc_1hr_research_live.py` for future evidence collection:
  `signal_scan` rows can now include selected threshold, spread, visible
  quantity, quote timestamp/age, BTC candle time, BTC candle age, RV60, and
  10-minute BTC return, selected TTL, and close time. Existing remote processes
  were not restarted, so this code change only affects future explicitly
  authorized starts/restarts.
- Patched the live BTC1H signal model path for future runs so
  `model_probability` uses `close_time - scan_start_time` when a scan timestamp
  is available, instead of the event object's cached refresh-time TTL. This is
  a future-evidence fix, not a retroactive validation of old rows. The current
  remote process was not restarted, so pre-restart rows remain cached-TTL-policy
  rows and any post-restart scan-time-TTL rows must use a separate evidence
  clock.
- Re-ran:
  - `scripts\build_btc1h_selected_signal_model_parity.py`;
  - `scripts\build_btc1h_multi_holdout_research.py`;
  - `scripts\check_btc_deployment_readiness.py`.
  Selected-signal exact scan-time-TTL parity remains `0 / 13`, implied cached
  TTL parity remains `13 / 13`, and readiness still reports
  `production_ready_count = 0`.
- Patched `scripts\replay_btc1h_core_ws_counterfactual.py` so the BTC cache
  loader falls back to DuckDB when PyArrow fails on the remote parquet, and
  added `--candidate-scan-only` for active-policy parity diagnostics.
- Ran active-policy candidate-scan snapshot replay:
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_candidate_scans_latest_codex`.
  Command mode used captured `signal_scan` timestamps, live scan semantics,
  `--candidate-scan-only`, no public settlement fallback, and the paused May 21
  BTC1H snapshot. It completed in about `77s` and produced `10` settled replay
  trades, captured-lifecycle PnL `+$1.11`, win `80.0%`, max DD `-$1.38`, and
  Sharpe `0.8305`.
- Replay-vs-actual ledger comparison:
  - actual BTC1H REST-official shadow ledger remained `11` rows, `+$0.50`;
  - replay matched `9` actual market/side keys;
  - actual row missing from replay:
    `KXBTCD-26MAY1911-T76299.99|no` (`-$0.71`);
  - replay replaced actual `KXBTCD-26MAY2010-T77199.99|no` with
    `KXBTCD-26MAY2010-T77299.99|no`;
  - replay therefore overstated the actual ledger by about `$0.61`.
  This is useful diagnostic progress, not promotion evidence: the replay is
  still not row-for-row faithful to the old cached-TTL live process, and
  `--candidate-scan-only` cannot discover new variants because it relies on the
  active policy's captured candidate rows.
- Added `scripts\build_btc1h_replay_vs_ledger_reconciliation.py` and artifact
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_latest_codex`.
  The candidate-scan replay reconciliation reports:
  - actual official rows `11`, replay rows `10`;
  - exact market/side matches `9 / 11`;
  - actual official PnL `+$0.50`, replay PnL `+$1.11`;
  - replay-minus-actual PnL `+$0.61`;
  - `promotion_usable_replay = false`;
  - blockers:
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.
- Added stricter replay flags to `scripts\replay_btc1h_core_ws_counterfactual.py`:
  `--selected-scan-only` and `--selected-market-only`. Artifact
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_selected_scans_latest_codex`
  replayed only captured selected-scan rows and the captured selected market.
  It produced `6` settled replay rows, PnL `-$0.14`, win `66.67%`, max DD
  `-$1.38`, and Sharpe `-0.1106`.
- Selected-scan reconciliation artifact
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_selected_scans_latest_codex`
  matched only `6 / 11` actual market/side rows and is also
  `promotion_usable_replay = false`. This stricter mode avoids adjacent
  replacement trades but misses more cached-TTL live rows under scan-time model
  recomputation, reinforcing that old cached-TTL rows cannot validate the new
  scan-time-TTL policy.
- Updated `scripts\build_btc1h_multi_holdout_research.py` to consume the replay
  reconciliation summary. The primary candidate now carries
  `counterfactual_replay_not_row_faithful` in addition to sample-size,
  official/proxy mismatch, cached-TTL, and full execution-gate blockers.
- Refreshed remote official settlement at `2026-05-22T05:13:17Z` and copied the
  small artifacts back locally. BTC1H remains unchanged at `11` official rows,
  official PnL `+$0.50`, official win `72.73%`, proxy PnL `+$1.50`, and `1`
  proxy/official mismatch.
- Local process audit found and stopped one stale local
  `replay_btc1h_core_ws_counterfactual.py` process from the earlier slow
  all-scan attempt. It was not a collector or paper shadow.
- Remote status at `2026-05-22T05:08:39Z` was `ALL_RUNNING` with BTC15M capture,
  three BTC15M paper shadows, and BTC1H high-conf shadow alive on
  `ClawService@100.92.9.80`.
- Added explicit BTC1H policy-epoch fields for future clean evidence:
  - `scripts\btc_1hr_research_live.py` now writes `signal_strategy`,
    `model_ttl_policy`, and `model_policy_version` into future DuckDB
    `signal_scan` / `order_decision` rows and future SQLite
    `research_live_trades` rows;
  - default `model_ttl_policy` is `scan_time_close_minus_now_v1`;
  - default `model_policy_version` is
    `btc1h_live_model_20260522_scan_ttl_v1`.
- Updated `scripts\check_btc_shadow_official_settlement.py` to preserve those
  policy fields in `shadow_official_trades.csv` and emit
  `shadow_official_policy_summary.csv`, so future official-settled rows can be
  grouped by evidence clock instead of pooled with old rows.
- Deployed the corrected local BTC1H live/replay/audit/settlement scripts to
  the always-on laptop without restarting any process. Remote backups:
  - `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_scan_ttl_deploy_20260522_051842`;
  - `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_policy_settlement_deploy_20260522_052004`.
  Remote `py_compile` passed after upload.
- Remote status after script upload at `2026-05-22T05:18:55Z` was still
  `ALL_RUNNING`. Existing BTC1H PID `5132` is still the old in-memory process
  started at `2026-05-22T02:42:06Z`, so it cannot produce the new
  scan-time-TTL policy rows until an explicitly authorized controlled restart.
- Re-ran remote official settlement after deploying the policy-aware settlement
  script. Artifact:
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex\shadow_official_policy_summary.csv`.
  All current BTC1H rows have blank policy fields because they predate the new
  schema. BTC1H remains unchanged at `11` official rows, official PnL `+$0.50`,
  win `72.73%`, proxy PnL `+$1.50`, and `1` proxy/official mismatch.
- Validation:
  - `python -m pytest scripts\test_research_live_safety.py -q --basetemp .pytest-codex-tmp`
    passed (`46` tests, `8` subtests; one existing pandas warning);
  - `scripts\build_btc1h_multi_holdout_research.py` reran with the current
    official settlement and replay reconciliation artifacts;
  - `scripts\check_btc_deployment_readiness.py` reran with expected no-deploy
    exit and `production_ready_count = 0`.
- Deployment conclusion unchanged:
  - `production_ready_count = 0`;
  - `high_conf_80_entry70_no_chase` is the best BTC1H research candidate, but
    not deployable;
  - the honest next step is more forward official rows plus a future snapshot
    generated by code that captures exact BTC model inputs.

## 2026-05-22 - BTC1H readiness now gates model-policy evidence clocks

- Patched `scripts\check_btc_deployment_readiness.py` so the conservative
  readiness report now consumes the current BTC1H evidence stack instead of
  relying only on older promotion artifacts:
  - prefers `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`
    for shadow official settlement when present;
  - includes
    `backtest_outputs\btc1h_multi_holdout_research_latest_codex\btc1h_candidate_gate_summary.csv`;
  - records the primary BTC1H replay-vs-ledger reconciliation artifact in
    readiness `run_info.json`;
  - adds an explicit BTC1H model-policy epoch gate requiring future official
    rows under `model_policy_version =
    btc1h_live_model_20260522_scan_ttl_v1` and `model_ttl_policy =
    scan_time_close_minus_now_v1`.
- Patched `scripts\refresh_btc_evidence_stack.py` to regenerate
  `btc1h_multi_holdout_research_latest_codex` before readiness, so full refresh
  runs do not leave the BTC1H gate stale.
- Added a regression test proving BTC1H rows with blank model-policy fields
  keep readiness false:
  `scripts\test_btc_readiness_row_reconciliation.py::test_btc1h_blank_model_policy_epoch_blocks_readiness`.
- Re-ran:
  - `python -m pytest scripts\test_btc_readiness_row_reconciliation.py scripts\test_btc_execution_realism_audit.py -q --basetemp .pytest-codex-tmp`
    -> `9 passed`;
  - `python scripts\build_btc1h_multi_holdout_research.py --out-dir backtest_outputs\btc1h_multi_holdout_research_latest_codex`;
  - `python scripts\check_btc_deployment_readiness.py --out-dir backtest_outputs\deployment_readiness_latest_codex`
    -> expected no-deploy exit, `production_ready_count = 0`.
- Current BTC1H readiness row remains:
  - active candidate `high_conf_80_entry70_no_chase`;
  - forward official rows `11`, official PnL `+$0.50`, mismatch rate `9.09%`;
  - snapshot parity passes for actual old shadow rows;
  - selected-signal model parity fails;
  - replay-vs-ledger is not row-faithful;
  - policy epoch status is `EXPECTED_POLICY_MISSING` with `11` blank-policy
    official rows.
- Interpretation: old cached-TTL BTC1H rows are still useful diagnostics, but
  readiness now has a machine-readable blocker preventing them from counting as
  evidence for the future scan-time-TTL policy. No deployment.
- Also patched `scripts\build_btc_restart_authorization_packet.py` after a
  dry authorization packet exposed a stale local process snapshot from
  `2026-05-18T22:54:40Z`. The packet now fails
  `forward_process_hygiene_snapshot_fresh` when the status artifact is older
  than `--max-forward-status-age-minutes` and adds
  `forward_process_hygiene_snapshot_stale` to target blockers. Do not use stale
  local PIDs from that packet as remote runtime evidence.

## 2026-05-22 - BTC1H candidate-scan replay performance fix

- Patched `scripts\replay_btc1h_core_ws_counterfactual.py` so signal-scan
  replay pruning happens in DuckDB instead of Python:
  - `--candidate-scan-only` now loads only `candidate_count > 0` scans;
  - `--selected-scan-only` now loads only selected scans;
  - non-`--full-stream` signal-scan replay joins against `replay_windows`;
  - candidate/selected diagnostic modes prune replay event windows down to the
    events that actually have retained signal scans.
- Added regression coverage:
  `scripts\test_btc1h_replay_signal_scan_pruning.py`.
- On the May 21 paused BTC1H snapshot, the candidate-scan diagnostic now loads
  `465` signal scans and streams `663,391` top rows across `11` event windows,
  versus the previous equivalent run's `465` signal scans over `2,270,209` top
  rows across `59` windows. Local runtime dropped from about `62s` in the
  SQL-scan-pruned run to about `21s` after event-window pruning.
- Fast replay artifact:
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_candidate_scans_fast_latest_codex`.
  It produced the same active-candidate result as the prior candidate-scan
  replay:
  - `10` settled replay rows;
  - replay PnL `+$1.11`;
  - win `80.0%`;
  - max DD `-$1.38`;
  - Sharpe `0.830455`.
- Reconciled the fast replay into
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_latest_codex`.
  Result remains not promotion-usable:
  - actual official rows `11`, replay rows `10`;
  - exact market/side matches `9 / 11`;
  - replay-minus-actual PnL `+$0.61`;
  - blockers:
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.
- Re-ran:
  - `python scripts\build_btc1h_multi_holdout_research.py --out-dir backtest_outputs\btc1h_multi_holdout_research_latest_codex`;
  - `python scripts\check_btc_deployment_readiness.py --out-dir backtest_outputs\deployment_readiness_latest_codex`
    -> expected no-deploy exit, `production_ready_count = 0`;
  - `python -m pytest scripts\test_btc1h_replay_signal_scan_pruning.py scripts\test_btc_readiness_row_reconciliation.py scripts\test_btc_execution_realism_audit.py -q --basetemp .pytest-codex-tmp`
    -> `10 passed`.
- Interpretation: active-policy parity diagnostics are now much faster and more
  repeatable, but the result is deliberately unchanged: old cached-TTL rows
  still fail row-faithful replay and cannot promote the strategy.
- Uploaded the replay-pruning script/test to the remote laptop without
  restarting collectors. Backup:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_btc1h_replay_pruning_20260522_070008`.
  Remote `py_compile` passed. Remote status at `2026-05-22T07:00:21Z` was
  `ALL_RUNNING`; BTC1H PID `5132` remains the old in-memory process, so this
  code still affects offline replay tooling and future starts only.

## 2026-05-22 - BTC1H full scan-clock replay diagnostic and holdout refresh

- Goal: continue the BTC1H research loop with faithful live-websocket replay
  where possible, while keeping the current active shadow as research-only
  until official settlement, execution realism, and row-faithful replay agree.
- Further optimized `scripts\replay_btc1h_core_ws_counterfactual.py`:
  - maintains per-event markets that currently pass static executable-book
    gates, so dead signal scans can be rejected before building quote
    DataFrames or calling the model;
  - fixes `--max-rows` smoke tests so they do not drain all remaining
    `signal_scan` rows after the top-book stream is intentionally stopped;
  - caches parsed event close times, parsed strikes, and BTC timestamp lookup
    arrays.
- Added/updated regression coverage:
  - `scripts\test_btc1h_replay_signal_scan_pruning.py`;
  - `scripts\test_btc_readiness_row_reconciliation.py` now proves the
    readiness gate prefers
    `btc1h_replay_vs_ledger_reconciliation_latest_codex` over newer full-scan
    diagnostic directories.
- Validation:
  - `python -m pytest scripts\test_btc1h_replay_signal_scan_pruning.py scripts\test_btc_readiness_row_reconciliation.py scripts\test_btc_execution_realism_audit.py -q --basetemp .pytest-codex-tmp`
    -> `13 passed`;
  - remote `py_compile` passed after uploading the replay/readiness scripts.
- Active-policy candidate-scan replay remains unchanged and is still not
  promotion-usable:
  - artifact:
    `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_candidate_scans_fast_latest_codex`;
  - `10` settled replay rows, PnL `+$1.11`, win `80.0%`, max DD `-$1.38`,
    Sharpe `0.830455`;
  - reconciliation artifact:
    `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_latest_codex`;
  - actual official rows `11`, replay rows `10`, exact market/side matches
    `9 / 11`, replay-minus-actual PnL `+$0.61`;
  - blockers remain
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.
- Full scan-clock replay over the paused May 21 remote BTC1H snapshot:
  - artifact:
    `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_fullscan_prefilter_highconf_latest_codex`;
  - event windows `59`, streamed top rows `2,270,209`, signal scans `436,265`;
  - `14` signals, `13` settled, PnL `+$1.99`, win `84.62%`, max DD `-$1.40`,
    Sharpe `1.455433`;
  - stats show `353,167` event evaluations, `212,575` state-level static-book
    skips, and `105,355` model calls.
  - This is a useful research diagnostic, not promotion evidence. Reconciliation
    against the actual paper ledger has actual rows `11`, replay rows `14`,
    exact market/side matches `9 / 11`, replay-minus-actual PnL `+$1.49`, and
    the same non-row-faithful blocker family.
- Selected-scan + selected-market replay diagnostic:
  - artifact:
    `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_selected_market_latest_codex`;
  - only `6` replay rows, PnL `-$0.14`, exact matches `6 / 11`;
  - confirms the blocker is deeper than broad scan selection. Exact selected
    model parity still fails because current rows were generated with old
    cached-TTL/live inputs that were not fully captured; implied-TTL diagnostics
    can reconcile probabilities, but that is not a deployable replay.
- Refreshed remote official settlement using the remote `.venv` interpreter
  because remote system `python` lacked `requests`. Pulled:
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`.
  BTC1H remains `11` official rows, official PnL `+$0.50`, official win
  `72.73%`, proxy PnL `+$1.50`, and `1` proxy/official mismatch
  (`9.09%`).
- Re-ran:
  - `python scripts\build_btc1h_multi_holdout_research.py --out-dir backtest_outputs\btc1h_multi_holdout_research_latest_codex`;
  - `python scripts\check_btc_deployment_readiness.py --out-dir backtest_outputs\deployment_readiness_latest_codex`
    -> expected no-deploy exit, `production_ready_count = 0`.
- Current quantitative conclusion:
  - `high_conf_80_entry70_no_chase` remains the best BTC1H candidate: all
    `14 / 14` fixed holdout buckets positive, all `6 / 6` May live-WS cadences
    positive, historical/proxy trades `159`, stressed historical/proxy PnL
    `+$21.39`, and current forward REST-official PnL `+$0.50`.
  - It is not deployable. Main blockers are too few forward official rows
    (`11`, target `50`), official/proxy mismatch rate `9.09%`, selected-signal
    exact parity failure, and non-row-faithful replay vs the paper ledger.
- Remote status after upload at `2026-05-22T07:38:05Z`: `ALL_RUNNING`.
  No collectors or shadows were restarted. Backup:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_btc1h_fullscan_prefilter_20260522_073800`.

## 2026-05-22 - BTC1H model-input capture and holdout gate refresh

- Goal: keep the BTC1H research loop focused on faithful replay and multiple
  holdout sets, while leaving BTC15M as passive shadow/capture evidence.
- Local process audit: no matching Kalshi/BTC Python processes were running on
  this laptop.
- Remote status at `2026-05-22T07:51:15Z`: `ALL_RUNNING`.
  - BTC15M capture and all three BTC15M paper shadows were alive.
  - BTC1H `btc1h_high_conf80_entry70_no_chase_shadow` PID `5132` remained
    alive, started `2026-05-22T02:42:06Z`, with about `101.9 MB` working set.
  - No collectors or shadows were restarted.
- Refreshed remote official settlement using the remote `.venv` interpreter and
  pulled `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`.
  BTC1H remains unchanged:
  - `11` official-settled rows;
  - official PnL `+$0.50`;
  - official win `72.73%`;
  - proxy PnL `+$1.50`;
  - `1` proxy/official mismatch (`9.09%`).
- Patched replay/materialization/parity tooling so future sidecars preserve
  exact BTC1H model inputs needed for deployable replay evidence:
  - `scripts\materialize_btc_replay_sidecar.py` now materializes BTC1H
    `signal_scan` fields for selected strategy, model TTL policy/version,
    threshold/spread/top-visible size, quote timestamp/age, selected TTL,
    close time, BTC candle time/age, RV60, and 10-minute BTC return. It also
    preserves policy fields on `order_decision`.
  - `scripts\build_btc1h_selected_signal_model_parity.py` can read old and new
    `signal_scan` schemas, carries optional captured fields through parity
    rows, and separately reports captured-TTL parity when those fields exist.
  - `scripts\build_btc1h_multi_holdout_research.py` now exposes captured-TTL
    model parity counts and keeps cached-TTL-only forward rows blocked for
    promotion.
- Added regression coverage:
  - `scripts\test_btc_replay_sidecar_materialization.py`;
  - `scripts\test_btc1h_replay_signal_scan_pruning.py` now also verifies the
    selected-signal loader preserves optional model-input fields.
- Validation:
  - `python -m py_compile scripts\materialize_btc_replay_sidecar.py scripts\build_btc1h_selected_signal_model_parity.py scripts\build_btc1h_multi_holdout_research.py scripts\test_btc_replay_sidecar_materialization.py scripts\test_btc1h_replay_signal_scan_pruning.py scripts\replay_btc1h_core_ws_counterfactual.py scripts\check_btc_deployment_readiness.py scripts\test_btc_readiness_row_reconciliation.py`
    -> passed;
  - `python -m pytest scripts\test_btc_replay_sidecar_materialization.py scripts\test_btc1h_replay_signal_scan_pruning.py scripts\test_btc_readiness_row_reconciliation.py scripts\test_btc_execution_realism_audit.py -q --basetemp .pytest-codex-tmp`
    -> `15 passed`;
  - remote `.venv` `py_compile` passed after upload.
- Uploaded the replay/materializer/parity/readiness tooling to the remote laptop
  without restarting collectors. Backup:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_btc1h_model_input_capture_20260522_075110`.
- Refreshed:
  - `backtest_outputs\btc1h_selected_signal_model_parity_latest_codex`;
  - `backtest_outputs\btc1h_multi_holdout_research_latest_codex`;
  - `backtest_outputs\deployment_readiness_latest_codex`.
- Current BTC1H quantitative status:
  - best candidate remains `high_conf_80_entry70_no_chase`;
  - `14 / 14` fixed historical/proxy/live-WS holdout buckets positive;
  - `6 / 6` May live-WS cadences positive;
  - historical/proxy rows `159`, stressed historical/proxy PnL `+$21.39`;
  - forward REST-official rows `11`, official PnL `+$0.50`, mismatch rate
    `9.09%`;
  - selected-signal exact model parity still fails `0 / 13`, while implied
    cached-TTL parity passes `13 / 13`;
  - replay-vs-ledger remains not promotion-usable (`9 / 11` exact matches,
    replay-minus-actual PnL `+$0.61`).
- Current remote sidecar schema check: the running BTC1H process is still
  writing the older sidecar schema. It has live rows
  (`signal_scan=133,450`, `ws_orderbook_top=456,290`, `order_decision=1` in
  the replay sidecar at the check), but `signal_scan` lacks the newly required
  exact model-input fields such as `ttl_min`, model policy fields, quote
  age/timestamp, top-visible quantity, BTC RV60, and BTC 10-minute return.
- Deployment/readiness:
  - `production_ready_count = 0`;
  - no BTC1H or BTC15M strategy is deployable;
  - BTC1H is the current active research path, but future deployable evidence
    needs a clean, explicitly authorized BTC1H evidence-clock restart so new
    rows contain exact scan-time model inputs and can be reconciled against
    official settlement and row-faithful replay.

## 2026-05-22 - BTC1H clean evidence-clock gate added

- Goal: make the next BTC1H deployability blocker machine-readable instead of
  relying on prose scattered across parity/readiness artifacts.
- Re-checked local process state: no matching Kalshi/BTC Python processes were
  running on this laptop.
- Re-checked remote process state at `2026-05-22T08:05:11Z`: `ALL_RUNNING`.
  BTC1H PID `5132` was still alive; no collectors or shadows were restarted.
- Refreshed remote official settlement and pulled
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`.
  BTC1H remains unchanged:
  - `11` official-settled rows;
  - official PnL `+$0.50`;
  - official win `72.73%`;
  - proxy PnL `+$1.50`;
  - `1` proxy/official mismatch (`9.09%`).
- Pulled current remote BTC1H status/schema snapshots to:
  - `runtime\remote_status\btc1h_status_latest.json`;
  - `runtime\remote_status\btc1h_replay_sidecar_schema_latest.json`.
- Added `scripts\build_btc1h_clean_evidence_clock_gate.py`.
  It checks whether active BTC1H forward rows belong to a clean promotion
  evidence clock by requiring:
  - fresh capture status;
  - sidecar signal/order policy fields;
  - expected model TTL/policy rows in official-settled ledger output;
  - captured selected TTL/model inputs;
  - replay-vs-ledger promotion usability;
  - enough official rows, positive official PnL, and no proxy/official
    mismatches.
- Current artifact:
  `backtest_outputs\btc1h_clean_evidence_clock_gate_latest_codex`.
  Verdict:
  - `gate_status = BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - `clean_evidence_clock_ready = False`;
  - next action:
    `controlled_btc1h_shadow_restart_to_start_clean_evidence_clock`;
  - official rows `11 / 50`, official PnL `+$0.50`, mismatch rate `9.09%`.
- Main clean-clock blockers:
  - current BTC1H replay sidecar is missing exact model-input fields:
    `ttl_min`, `model_ttl_policy`, `model_policy_version`,
    `signal_strategy`, quote timestamp/age, top-visible quantity, BTC RV60,
    BTC 10-minute return, close time, and BTC candle timing fields;
  - order-decision sidecar rows are missing policy fields;
  - expected scan-time model policy has `0` official rows;
  - existing `11` BTC1H official rows are blank-policy cached-TTL rows;
  - captured TTL rows are `0 / 13`;
  - replay-vs-ledger remains not promotion-usable;
  - official row count and proxy/official mismatch gates both fail.
- Wired the clean evidence-clock gate into
  `scripts\check_btc_deployment_readiness.py`; refreshed
  `backtest_outputs\deployment_readiness_latest_codex`.
  `production_ready_count` remains `0`, now with explicit BTC1H clean-clock
  blocker columns/reasons.
- Added regression coverage:
  `scripts\test_btc1h_clean_evidence_clock_gate.py`.
- Also patched `scripts\analyze_btc1h_highconf_robustness.py` so `--help`
  uses `argparse` and no longer writes a timestamped robustness artifact.
- Validation:
  - `python -m py_compile scripts\analyze_btc1h_highconf_robustness.py scripts\check_btc_deployment_readiness.py scripts\build_btc1h_clean_evidence_clock_gate.py`;
  - `python scripts\analyze_btc1h_highconf_robustness.py --help` did not
    create a new robustness directory;
  - `python -m pytest scripts\test_btc1h_clean_evidence_clock_gate.py scripts\test_btc_replay_sidecar_materialization.py scripts\test_btc1h_replay_signal_scan_pruning.py scripts\test_btc_readiness_row_reconciliation.py scripts\test_btc_execution_realism_audit.py -q --basetemp .pytest-codex-tmp`
    -> `18 passed`.
- Uploaded the clean-clock/readiness/robustness tooling to the remote laptop
  without restarting collectors. Remote backup:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_btc1h_clean_clock_gate_20260522_080506`.
- Interpretation: `high_conf_80_entry70_no_chase` remains near-deployable
  research only. The strategy is historically interesting, but the current
  forward rows cannot be promoted. The next deployability step is not another
  threshold search; it is a controlled BTC1H evidence-clock restart, if and
  when explicitly authorized, followed by fresh official-settled rows with
  exact model-input sidecar fields and row-faithful replay.

## 2026-05-22 - BTC1H process-status and restart authorization refresh

- Goal: continue the BTC1H research loop while keeping BTC15M running only as
  shadow/control evidence, and make sure a future clean evidence-clock restart
  is guarded by real preflight checks rather than brittle SSH/WMI process
  inspection.
- Re-checked local process state: no matching Kalshi/BTC Python processes were
  running on this laptop.
- Remote status after sidecar-PID fallback refresh:
  - `running_targets = 5 / 5`;
  - duplicate target processes `0`;
  - unmanaged matching processes `0` by available status artifacts;
  - BTC1H `btc1h_high_conf80_entry70_no_chase_shadow` PID `5132` is alive, but
    its start time `2026-05-22T02:42:06Z` predates the latest
    `scripts\btc_1hr_research_live.py` source mtime
    `2026-05-22T05:18:44Z`, so current rows remain stale for promotion.
- Current BTC1H quantitative state is unchanged:
  - best candidate: `high_conf_80_entry70_no_chase`;
  - multi-holdout: `14 / 14` positive buckets and `6 / 6` May WS cadences;
  - historical/proxy rows `159`, stressed historical/proxy PnL `+$21.39`;
  - forward REST-official rows `11`, official PnL `+$0.50`, official win
    `72.73%`, proxy PnL `+$1.50`, mismatch rate `9.09%`;
  - replay-vs-ledger is not promotion-usable: `9 / 11` exact rows and
    replay-minus-actual PnL `+$0.61`.
- Patched guardrail tooling:
  - `scripts\check_btc_forward_shadow_status.py` now falls back to capture
    status sidecar PIDs when WMI command-line process inspection is denied, and
    parses Windows 7-digit fractional process start timestamps correctly;
  - `scripts\restart_btc_paper_shadows.ps1` now uses capture-status PID
    fallback in dry runs and records process-inspection warnings rather than
    failing immediately under SSH;
  - `scripts\build_btc_restart_authorization_packet.py` now treats
    `PASS_SCHEMA_READY` active ledgers as acceptable for a guarded restart and
    recognizes the q250 YES shadow as already running;
  - `scripts\test_btc_paper_restart_safety.py` covers these guardrail changes.
- Validation:
  - local `py_compile` passed for the touched status/restart/auth/test scripts;
  - local `powershell -NoProfile -ExecutionPolicy Bypass -File
    scripts\restart_btc_paper_shadows.ps1 -StartupWaitSec 1` produced a dry-run
    plan only and did not stop/start processes;
  - local targeted pytest suite passed: `26 passed`;
  - remote `.venv` `py_compile` passed after upload.
- Remote backup before the final upload:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_q250_yes_running_status_fix_20260522_082520`.
- Refreshed artifacts:
  - `backtest_outputs\btc_forward_shadow_status_latest_codex`;
  - `backtest_outputs\btc_restart_authorization_packet_latest_codex`;
  - `backtest_outputs\deployment_readiness_latest_codex`.
- Restart authorization packet now reports:
  - `all_ready_for_user_authorization = true`;
  - `all_ready_for_clean_restart_authorization = true`;
  - `ready_for_user_authorization_count = 4 / 4`.
- No restart or deployment was executed. `production_ready_count` remains `0`.
  The honest next step, if explicitly authorized, is a controlled paper-shadow
  restart to start a clean BTC1H scan-time-TTL evidence clock. After that,
  BTC1H needs at least `50` official-settled post-restart rows with populated
  execution-realism/model-input fields, low official/proxy mismatch, and
  row-faithful replay before any deployment discussion.

## 2026-05-22 - BTC1H clean-clock gate fallback and latest refresh

- Goal: keep the BTC1H evidence-clock gate faithful on both the local laptop
  and the remote collector, even when SSH/WMI access or pulled
  `runtime\remote_status` snapshots are unavailable/stale.
- Refreshed remote state without stopping or restarting anything:
  - remote status: `5 / 5` expected targets running;
  - BTC1H PID `5132` alive;
  - duplicate target processes `0`;
  - unmanaged matching processes `0`;
  - BTC1H remains `RUNNING_SOURCE_STALE_RESTART_REQUIRED` because the process
    predates the source that records exact scan-time model inputs.
- Refreshed remote official settlement and pulled
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`.
  Current BTC1H official evidence is unchanged:
  - `11` official rows;
  - official PnL `+$0.50`;
  - official win `72.73%`;
  - proxy/official mismatches `1 / 11 = 9.09%`.
- Patched `scripts\build_btc1h_clean_evidence_clock_gate.py`:
  - added `--forward-status-summary`;
  - uses the fresher forward-status row when pulled runtime status JSON is
    stale or missing;
  - can derive replay-sidecar table schema directly from the JSONL sidecar on
    the remote collector when a separate schema snapshot is missing;
  - parses Windows timestamps with 7 fractional digits.
- Added regression coverage in
  `scripts\test_btc1h_clean_evidence_clock_gate.py` for the forward-status and
  replay-sidecar fallback path.
- Refreshed local clean-clock artifact:
  `backtest_outputs\btc1h_clean_evidence_clock_gate_latest_codex`.
  Current summary:
  - `gate_status = BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - `status_source = forward_status_summary`;
  - `sidecar_schema_source = sidecar_schema_json`;
  - official rows `11 / 50`;
  - official PnL `+$0.50`;
  - multi-holdout promising `True`;
  - captured TTL rows `0 / 13`;
  - old sidecar still missing exact model-input fields.
- Refreshed `backtest_outputs\deployment_readiness_latest_codex`:
  `production_ready_count = 0`.
- Uploaded the clean-clock fallback script/test to the remote laptop after
  backup:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_clean_clock_forward_status_fallback_20260522_083820`.
  Remote `.venv` compile passed. Remote smoke run now blocks for real
  evidence-clock reasons rather than missing status JSON.
- Validation:
  - local `py_compile` passed for the clean-clock script and test;
  - local targeted pytest suite passed: `27 passed`;
  - no restart, deployment, or process kill was executed.
- Interpretation: the active BTC1H candidate remains the strongest research
  candidate and near-deployable only in the research sense. The deployability
  bottleneck is now very specific and machine-readable: a clean scan-time-TTL
  forward evidence clock must be started before future rows can count.

## 2026-05-22 - BTC1H official basis/mismatch audit

- Goal: make the current BTC1H official/proxy settlement risk row-level and
  machine-readable without fitting a hindsight guard on the tiny sample.
- Added `scripts\build_btc1h_official_basis_mismatch_audit.py`.
  It reads
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex\shadow_official_trades.csv`
  and writes:
  - `backtest_outputs\btc1h_official_basis_mismatch_audit_latest_codex\btc1h_basis_mismatch_summary.csv`;
  - `btc1h_basis_mismatch_watchlist.csv`;
  - `btc1h_basis_mismatch_by_day.csv`;
  - `btc1h_basis_mismatch_rows.csv`;
  - `report.md`.
- Current audit verdict:
  `TOO_FEW_OFFICIAL_ROWS;OBSERVED_PROXY_OFFICIAL_MISMATCH;PROXY_WIN_OFFICIAL_LOSS_FLIP`.
- Current BTC1H basis/mismatch numbers:
  - official rows `11 / 50`;
  - official PnL `+$0.50`;
  - proxy PnL `+$1.50`;
  - official-minus-proxy PnL `-$1.00`;
  - official/proxy mismatches `1 / 11 = 9.09%`;
  - proxy-win/official-loss flips `1`;
  - max absolute official/proxy basis `$87.42`;
  - p95 absolute official/proxy basis `$78.915`;
  - near-proxy-boundary rows within `$50`: `4`;
  - near-official-boundary rows within `$50`: `4`.
- The mismatch row is
  `KXBTCD-26MAY2008-T77299.99`, side `no`:
  - proxy result `no`, official result `yes`;
  - proxy margin was only about `$8.37` in favor of NO;
  - official margin ended about `$33.78` against NO;
  - official-minus-proxy basis was `$42.15`;
  - proxy PnL `+$0.30`, official PnL `-$0.70`.
- Daily path:
  - `2026-05-19`: `4` rows, official PnL `+$0.22`, no mismatches;
  - `2026-05-20`: `4` rows, official PnL `-$0.63`, `1` mismatch;
  - `2026-05-21`: `3` rows, official PnL `+$0.91`, no mismatches.
- Wired the audit into `scripts\check_btc_deployment_readiness.py`; refreshed
  `backtest_outputs\deployment_readiness_latest_codex`.
  Readiness now carries BTC1H basis fields and still has
  `production_ready_count = 0`.
- Added the audit to `scripts\refresh_btc_evidence_stack.py` immediately before
  deployment readiness. Bounded smoke refresh passed for:
  - `btc1h_official_basis_mismatch_audit`;
  - `deployment_readiness` with expected no-deploy exit `1`.
- Added regression coverage:
  - `scripts\test_btc1h_official_basis_mismatch_audit.py`;
  - readiness coverage in `scripts\test_btc_readiness_row_reconciliation.py`.
- Validation:
  - local `py_compile` passed for the new audit, readiness, refresh controller,
    and tests;
  - local targeted pytest suite passed: `29 passed`;
  - remote `.venv` compile passed after upload;
  - remote audit run passed and produced the same BTC1H basis verdict.
- Remote backup before upload:
  `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_btc1h_basis_mismatch_audit_20260522_084500`.
- Interpretation: this does not make a guard deployable. It tells us exactly
  what to monitor after a clean restart: near-strike NO trades can be erased by
  Kalshi official basis versus proxy close. Any distance/basis guard must be
  preregistered for future clean-clock rows, not fitted to these `11` rows.

## 2026-05-22 - BTC1H next forward candidate packet

- Goal: freeze the next BTC1H forward-evidence spec so future clean-clock rows
  have an explicit candidate definition, promotion gate, and basis-watch
  checklist. This is not deployment approval.
- Added `scripts\build_btc1h_next_forward_candidate_packet.py`.
  It writes:
  - `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex\btc1h_candidate_freeze_specs.csv`;
  - `btc1h_promotion_gate_specs.csv`;
  - `btc1h_basis_watch_specs.csv`;
  - `btc1h_current_evidence_snapshot.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_next_forward_candidate_packet.py`.
- Frozen candidate:
  - `variant = high_conf_80_entry70_no_chase`;
  - `ledger = btc1h_high_conf80_entry70_no_chase_shadow`;
  - wrapper `scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py`;
  - policy `btc1h_live_model_20260522_scan_ttl_v1` /
    `scan_time_close_minus_now_v1`;
  - paper-only, flat-max sizing, `1` contract;
  - TTL `5` to `20` minutes;
  - entry `25c` to `70c`;
  - YES probability `>= 0.80`, NO-side `p_yes <= 0.20`;
  - min edge `12c`, max spread `2c`;
  - no-chase 10-minute move threshold `$150`.
- Current evidence snapshot in the packet:
  - `14 / 14` holdouts positive;
  - `6 / 6` May websocket cadences positive;
  - historical/proxy rows `159`, PnL `+$21.39`;
  - forward official rows `11`, official PnL `+$0.50`;
  - forward official mismatch rate `9.09%`;
  - clean clock `BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - basis audit
    `TOO_FEW_OFFICIAL_ROWS;OBSERVED_PROXY_OFFICIAL_MISMATCH;PROXY_WIN_OFFICIAL_LOSS_FLIP`.
- Promotion gate frozen for future clean-clock rows:
  - at least `50` clean post-restart official rows;
  - official PnL positive after fees;
  - proxy/official mismatch rate `<= 2%`;
  - proxy-win/official-loss flips `0`;
  - row-for-row replay/ledger match required;
  - execution realism fields required;
  - expected model policy rows required;
  - blank policy rows allowed `0`;
  - source freshness must be current after controlled restart.
- Basis watch is prospective only:
  - watch boundary `$50`;
  - watch fields include side, market/strike, entry spot, proxy close,
    official expiration value, official-minus-proxy spot, side margins,
    quote age, top visible quantity, and entry price;
  - do not fit or apply a guard from current stale rows.
- Wired the packet into `scripts\refresh_btc_evidence_stack.py` immediately
  before deployment readiness. Bounded smoke refresh passed for:
  - `btc1h_next_forward_candidate_packet`;
  - `deployment_readiness` with expected no-deploy exit `1`.
- Validation:
  - local `py_compile` passed for the packet builder/test and refresh
    controller;
  - local packet test passed;
  - local packet generated from full local artifact set;
  - remote `.venv` compile and packet run passed after upload;
  - remote backup:
    `C:\Users\ClawService\Kalshi-Trading-Bot\runtime\script_backups\pre_btc1h_next_forward_packet_20260522_085030`.
- Interpretation: the next forward path is now explicit. We still need an
  explicitly authorized controlled paper-shadow restart before any new rows can
  count toward the frozen promotion gate.

## 2026-05-22 - BTC1H decision-time distance guard audit

- Goal: test whether the current BTC1H official/proxy flip is likely solved by
  a simple decision-time side-distance guard, without fitting a deployable guard
  from the tiny current official sample.
- Added `scripts\build_btc1h_decision_distance_guard_audit.py`.
  It writes:
  - `backtest_outputs\btc1h_decision_distance_guard_audit_latest_codex\btc1h_decision_distance_guard_summary.csv`;
  - `btc1h_decision_distance_guard_holdouts.csv`;
  - `btc1h_decision_distance_guard_forward_rows.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_decision_distance_guard_audit.py`.
- Feature tested: decision-time side margin in USD:
  - YES margin = `spot - strike`;
  - NO margin = `strike - spot`.
- Active candidate audited:
  `high_conf_80_entry70_no_chase`, with historical/proxy rows stressed by
  `+2c` adverse entry.
- Key result:
  - no guard / `$25` guard: `127` historical rows, `15 / 15` positive
    historical holdouts, `6 / 6` positive WS cadences, current official sample
    `11` rows for `+$0.50` with `1` proxy-win/official-loss flip;
  - `$50` guard: removes the current official mismatch and raises current
    diagnostic official PnL to `+$1.88` on `9` rows, but historical evidence
    weakens to `13 / 15` positive holdouts and only `4 / 6` positive WS
    cadences;
  - `$75+` guards reduce sample size and damage WS stability further.
- Interpretation: a simple `$50+` decision-distance guard is not ready to
  freeze as the next BTC1H strategy. It addresses the current observed flip,
  but it breaks the fixed live-websocket cadence robustness that made the
  candidate interesting. The current stale-clock official rows remain
  diagnostic only; do not promote or tune a guard from them.
- Wired the audit into `scripts\refresh_btc_evidence_stack.py` before the
  BTC1H next-forward packet and readiness check.
- Bounded refresh smoke passed for:
  - `btc1h_decision_distance_guard_audit`;
  - `btc1h_next_forward_candidate_packet`;
  - `deployment_readiness` with expected no-deploy exit `1`.
- Validation:
  - local `py_compile` passed for the new audit, test, and refresh controller;
  - local targeted pytest passed: `8 passed`.

## 2026-05-22 - BTC1H side/entry profile audit

- Goal: check whether the active BTC1H candidate's fragility is better
  explained by side exposure or entry-price band than by distance alone, without
  treating slices as deployable policies.
- Added `scripts\build_btc1h_side_entry_profile_audit.py`.
  It writes:
  - `backtest_outputs\btc1h_side_entry_profile_audit_latest_codex\btc1h_side_entry_profile_summary.csv`;
  - `btc1h_side_entry_profile_holdouts.csv`;
  - `btc1h_side_entry_profile_forward_rows.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_side_entry_profile_audit.py`.
- Profiles tested are fixed diagnostics:
  `all_active`, `yes_only`, `no_only`, `entry_le_50`,
  `entry_50_60`, `entry_60_70`, `yes_entry_le_60`,
  `yes_entry_60_70`, `no_entry_le_60`, `no_entry_60_70`.
- Key result:
  - `all_active`: `159` historical/proxy rows, `+$21.39`, `15 / 15`
    positive holdouts, `6 / 6` WS cadences, current diagnostic official sample
    `11` rows, `+$0.50`, `1` mismatch/flip;
  - `entry_60_70`: still `15 / 15` holdouts and `6 / 6` WS cadences, but
    current official sample has only `+$0.09` on `10` rows and still has the
    mismatch/flip;
  - `yes_only`: `59` historical/proxy rows, `+$8.74`, but only `14 / 15`
    holdouts and `5 / 6` WS cadences positive; current official evidence is
    just `1` row for `+$0.32`;
  - `no_only`: `100` historical/proxy rows, `+$12.65`, `6 / 6` WS cadences,
    but current official sample is `10` rows for only `+$0.18` and still has
    the mismatch/flip;
  - `no_entry_60_70`: current official sample is negative, `-$0.23`, with the
    mismatch/flip still present.
- Interpretation: no side-only or entry-band slice improves the deployability
  case. YES-only is too sparse and less stable; NO-side/entry-60-70 retains the
  exact official-settlement blocker. Keep the frozen active BTC1H candidate as
  the sole next forward candidate for now, and do not preregister a side/entry
  retune from these stale rows.
- Wired the audit into `scripts\refresh_btc_evidence_stack.py` between the
  BTC1H distance-guard audit and the next-forward packet.
- Bounded refresh smoke passed for:
  - `btc1h_decision_distance_guard_audit`;
  - `btc1h_side_entry_profile_audit`;
  - `btc1h_next_forward_candidate_packet`;
  - `deployment_readiness` with expected no-deploy exit `1`.
- Validation:
  - local `py_compile` passed for the new audit/test and refresh controller;
  - local targeted pytest passed: `8 passed`.

## 2026-05-22 - BTC1H promotion gap matrix

- Goal: collapse the large readiness CSV into a compact BTC1H-only promotion
  matrix that separates research support, operational passes, stale diagnostic
  evidence, hard blockers, and rejected retune diagnostics.
- Added `scripts\build_btc1h_promotion_gap_matrix.py`.
  It writes:
  - `backtest_outputs\btc1h_promotion_gap_matrix_latest_codex\btc1h_promotion_gap_matrix.csv`;
  - `btc1h_promotion_gap_summary.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_promotion_gap_matrix.py`.
- Current summary for `high_conf_80_entry70_no_chase`:
  - `deployable_now = False`;
  - `near_deployable_research_candidate = True`;
  - recommended policy for the next clean clock:
    `high_conf_80_entry70_no_chase`;
  - recommended policy change: `none`;
  - recommended next action:
    `explicitly_authorized_controlled_restart_then_collect_50_clean_official_rows`;
  - blocked gates: `8`;
  - rejected diagnostic retunes: `2`;
  - diagnostic-only gates: `5`;
  - research-only pass: `1`;
  - operational pass: `1`.
- Gate interpretation:
  - PASS research-only:
    historical multi-holdout support (`14 / 14` holdouts, `6 / 6` WS
    cadences, historical PnL `+$21.39`);
  - PASS operational:
    frozen policy parity;
  - DIAGNOSTIC only:
    official PnL is positive (`+$0.50`) only on stale pre-clean-clock rows,
    basis-stressed proxy labels are fragile at observed p95/max basis,
    runner-up variants look more basis-robust but lack forward validation,
    holdout independence is duplicate-heavy, and statistical confidence is
    fragile after de-duplicating market/side rows;
  - BLOCKED:
    clean evidence clock, clean forward sample size, execution-realism fields,
    proxy/official agreement, overall readiness, policy identity fields,
    process/source freshness, and row-for-row replay;
  - REJECTED diagnostic retunes:
    distance guard and side/entry retunes.
- Wired the matrix into `scripts\refresh_btc_evidence_stack.py` immediately
  after deployment readiness, so it summarizes the current BTC1H gate state
  after readiness has been rebuilt.
- Bounded refresh smoke passed for:
  - `deployment_readiness` with expected no-deploy exit `1`;
  - `btc1h_promotion_gap_matrix`.
- Validation:
  - local `py_compile` passed for the new matrix script/test and refresh
    controller;
  - local targeted pytest passed: `8 passed`.

## 2026-05-22 - BTC1H statistical confidence audit

- Goal: stress the active BTC1H candidate's statistical story without changing
  policy thresholds, and without allowing statistics to bypass official
  settlement, clean-clock, execution, or replay gates.
- Added `scripts\build_btc1h_statistical_confidence_audit.py`.
  It writes:
  - `backtest_outputs\btc1h_statistical_confidence_audit_latest_codex\btc1h_statistical_confidence_summary.csv`;
  - `btc1h_statistical_input_rows.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_statistical_confidence_audit.py`.
- Key result:
  - naive pooled historical rows: `159` rows, `+$21.39`, win `81.76%`,
    max DD `-$1.82`, Sharpe `4.45`, event-cluster bootstrap p2.5
    `+$5.7395`, breakeven-null p `0.00025`;
  - unique market/side historical rows: `69` rows, `+$5.82`, win `76.81%`,
    max DD `-$1.95`, Sharpe `1.67`, event-cluster bootstrap p2.5
    `-$1.28`, breakeven-null p `0.043898`;
  - robustness rows alone: `127` rows, `+$16.61`, event-cluster bootstrap p2.5
    `+$2.77`, breakeven-null p `0.00065`;
  - direct rows alone: `32` rows, `+$4.78`, event-cluster bootstrap p2.5
    `+$0.24975`, breakeven-null p `0.049448`;
  - stale forward official rows: `11` rows, `+$0.50`, win `72.73%`, max DD
    `-$1.76`, Sharpe `0.315`, event-cluster bootstrap p2.5 `-$2.61`,
    breakeven-null p `0.514474`.
- Interpretation: pooled historical evidence remains worth researching, but it
  is partly inflated by duplicate/cadence-overlapping market-side rows. The
  de-duplicated panel has a negative lower bootstrap bound, and the stale
  official panel is statistically useless for promotion. The active BTC1H
  candidate remains a near-deployable research candidate only.
- Wired the audit into `scripts\refresh_btc_evidence_stack.py` before the
  BTC1H next-forward packet, deployment readiness, and promotion gap matrix.
- Wired the statistical summary into
  `scripts\build_btc1h_promotion_gap_matrix.py` as a `DIAGNOSTIC_ONLY`
  `statistical_confidence` gate.

## 2026-05-22 - BTC1H holdout independence audit

- Goal: audit whether the active BTC1H candidate's `14 / 14` positive
  multi-holdout result is inflated by duplicate market/side rows or by one
  event/holdout cluster, without changing the frozen policy.
- Added `scripts\build_btc1h_holdout_independence_audit.py`.
  It writes:
  - `backtest_outputs\btc1h_holdout_independence_audit_latest_codex\btc1h_holdout_independence_summary.csv`;
  - `btc1h_holdout_independence_by_holdout.csv`;
  - `btc1h_holdout_independence_leave_one_holdout.csv`;
  - `btc1h_holdout_independence_top_clusters.csv`;
  - `btc1h_holdout_independence_input_rows.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_holdout_independence_audit.py`.
- Key result:
  - pooled historical rows: `159` rows, `+$21.39`, `90` duplicate
    market/side rows, `63` event clusters, `15` holdout keys,
    leave-one-event minimum PnL `+$19.41`;
  - de-duplicated market/side rows: `69` rows, `+$5.82`, `63` event clusters,
    `10` holdout keys, leave-one-event minimum PnL `+$5.26`;
  - stale forward official rows: `11` rows, `+$0.50`, leave-one-event minimum
    PnL `+$0.09`, diagnostic only;
  - weak holdout buckets:
    `direct_predexon_trade_logs|H2b_direct_apr23_may01_holdout` has only
    `+$0.06` and flips negative under one-event removal; and
    `robustness_trade_logs|D1_predexon_mar24_apr01_dev` has only `+$0.22` and
    also flips negative under one-event removal.
- Interpretation: the active BTC1H candidate is not purely a one-event mirage;
  the de-duplicated panel remains positive and leave-one-holdout remains
  positive. But the pooled edge is much less impressive after removing
  duplicate market/side rows, and several small buckets are fragile. This is
  research support, not deployment evidence.
- Wired the audit into `scripts\refresh_btc_evidence_stack.py` before the
  statistical confidence audit and BTC1H promotion gap matrix.
- Wired the summary into `scripts\build_btc1h_promotion_gap_matrix.py` as a
  `DIAGNOSTIC_ONLY` `holdout_independence` gate.
- Validation:
  - local `py_compile` passed for the new audit/test, promotion matrix, and
    refresh controller;
  - local targeted pytest passed: `10 passed`;
  - bounded refresh smoke passed from `btc1h_holdout_independence_audit`
    through `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as
    the expected no-deploy verdict.

## 2026-05-22 - BTC1H official-basis stress audit

- Goal: stress active BTC1H historical/proxy labels against plausible Kalshi
  official settlement basis without fitting a guard or changing the frozen
  policy. The stress moves proxy settlement against each trade side: YES rows
  lower, NO rows higher.
- Added `scripts\build_btc1h_basis_stress_audit.py`.
  It writes:
  - `backtest_outputs\btc1h_basis_stress_audit_latest_codex\btc1h_basis_stress_summary.csv`;
  - `btc1h_basis_stress_by_holdout.csv`;
  - `btc1h_basis_stress_trade_rows.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_basis_stress_audit.py`.
- Stressable coverage:
  - `62` active-candidate historical/proxy rows with usable settlement spot;
  - `32` unique market/side rows;
  - `9` historical holdout buckets;
  - this excludes live-WS rows without a usable proxy settlement spot for this
    particular stress calculation.
- Key result under `+2c` adverse entry stress:
  - `$0`, `$25`, `$50` side-adverse basis: `0` flips, PnL `+$9.04`, unique
    market/side PnL `+$4.78`, `9 / 9` positive stressable holdouts;
  - `$75` side-adverse basis: `7` flips, PnL `+$2.04`, unique PnL `+$0.78`,
    `6 / 9` positive holdouts;
  - observed p95 absolute official/proxy basis `$78.915`: `9` flips, PnL
    `+$0.04`, unique PnL `-$0.22`, only `4 / 9` positive holdouts;
  - observed max basis `$87.42`: `11` flips, PnL `-$1.96`, unique PnL
    `-$1.22`, only `2 / 9` positive holdouts.
- Interpretation: the active BTC1H candidate is robust to moderate `$50`
  side-adverse basis in the stressable historical panel, but it is not robust
  to p95/max basis shocks observed in the tiny stale official sample. This
  reinforces that proxy/captured labels remain research-only and that future
  clean official rows are mandatory before any promotion or basis guard.
- Wired the audit into `scripts\refresh_btc_evidence_stack.py` after the
  official basis mismatch audit and before distance/side/statistical audits.
- Wired the summary into `scripts\build_btc1h_promotion_gap_matrix.py` as a
  `DIAGNOSTIC_ONLY` `basis_stress` gate.
- Validation:
  - local `py_compile` passed for the new audit/test, promotion matrix, and
    refresh controller;
  - local targeted pytest passed: `11 passed`;
  - bounded refresh smoke passed from `btc1h_basis_stress_audit` through
    `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as the
    expected no-deploy verdict.

## 2026-05-22 - BTC1H variant basis-stress ranking

- Goal: compare already-frozen BTC1H variants under the same side-adverse
  official-basis shocks, so research priority is not based only on the active
  forward candidate. This is not a threshold search and does not switch the
  active policy.
- Added `scripts\build_btc1h_variant_basis_stress_ranking.py`.
  It writes:
  - `backtest_outputs\btc1h_variant_basis_stress_ranking_latest_codex\btc1h_variant_basis_stress_ranking.csv`;
  - `btc1h_variant_basis_stress_summary.csv`;
  - `btc1h_variant_basis_stress_by_holdout.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_variant_basis_stress_ranking.py`.
- Ranking by maximum side-adverse basis shock with positive unique
  market/side PnL:
  - `high_conf_80_no_chase`: survives through `$87.42`; at p95 basis
    `$78.915`, PnL `+$2.70`, unique PnL `+$1.60`, `8 / 12` holdouts positive;
  - `high_conf_80_entry59_70_no_chase`: survives through `$87.42`; at p95
    basis, PnL `+$3.00`, unique PnL `+$1.41`, `8 / 9` holdouts positive;
  - `high_conf_80_entry70_no_chase`: survives only through `$75`; at p95
    basis, PnL `+$0.04`, unique PnL `-$0.22`, `4 / 9` holdouts positive;
  - `high_conf_80`: survives only through `$50`; at p95 basis, PnL `-$2.11`,
    unique PnL `-$2.11`, `2 / 5` holdouts positive.
- Interpretation:
  - The active forward candidate is still the only BTC1H near-forward
    candidate because it has current official rows and frozen packet coverage.
  - But it is not the strongest basis-stress historical variant.
  - `high_conf_80_no_chase` is basis-robust historically, yet prior evidence
    still rejected it for websocket cadence instability and expensive-entry
    fragility.
  - `entry59_70_no_chase` is basis-robust historically, yet it remains
    historical-only and derived from filtered rows; it needs causal replay and
    fresh forward shadow evidence before promotion discussion.
- Wired the ranking into `scripts\refresh_btc_evidence_stack.py` after the
  active-candidate basis stress audit.
- Wired the ranking into `scripts\build_btc1h_promotion_gap_matrix.py` as a
  `DIAGNOSTIC_ONLY` `variant_basis_stress_ranking` gate.
- Validation:
  - local `py_compile` passed for the new ranking/test, promotion matrix, and
    refresh controller;
  - local targeted pytest passed: `10 passed`;
  - bounded refresh smoke passed from `btc1h_variant_basis_stress_ranking`
    through `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as
    the expected no-deploy verdict.

## 2026-05-22 - BTC1H research priority matrix

- Goal: separate the current forward-control policy from the best historical
  runner-up, so BTC1H research does not silently switch policies based on a
  diagnostic basis-stress table.
- Added `scripts\build_btc1h_research_priority_matrix.py`.
  It writes:
  - `backtest_outputs\btc1h_research_priority_matrix_latest_codex\btc1h_research_priority_matrix.csv`;
  - `btc1h_research_priority_summary.csv`;
  - `report.md`;
  - `run_info.json`.
- Added regression coverage:
  `scripts\test_btc1h_research_priority_matrix.py`.
- Current matrix:
  - active forward-control policy remains `high_conf_80_entry70_no_chase`;
  - top causal-replay runner-up is
    `high_conf_80_entry59_70_no_chase`, but current live-WS replay overlap is
    exact with the active policy on the paused snapshot;
  - top basis-stress-only variant is `high_conf_80_no_chase`;
  - recommended forward policy change is `none`;
  - `deployable_now = False` and `production_ready_count = 0`.
- Interpretation:
  - `entry70_no_chase` is still the right frozen clean-clock control because it
    is the only BTC1H policy with current official forward rows and packet
    coverage, even though those rows are stale/diagnostic.
  - `entry59_70_no_chase` deserves broader causal replay/model-input parity
    before any paper-shadow discussion because the first paused-snapshot replay
    added no independent rows versus `entry70_no_chase`.
  - `high_conf_80_no_chase` is a basis-robust watchlist policy, but the failed
    `1s` live-WS cadence and extra-row damage must be explained first.
  - `high_conf_80` is low priority under current evidence.
- Wired the priority matrix into `scripts\refresh_btc_evidence_stack.py` after
  statistical confidence and before the BTC1H next-forward packet/readiness.
- Validation:
  - local `py_compile` passed for the new matrix/test and refresh controller;
  - local targeted pytest passed: `8 passed`;
  - bounded refresh smoke passed from `btc1h_research_priority_matrix` through
    `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as the
    expected no-deploy verdict.

## 2026-05-22 - BTC1H entry59 replay overlap audit

- Goal: test whether the `high_conf_80_entry59_70_no_chase` runner-up is
  independent on the available paused live-websocket replay, rather than only a
  historical filtered-row variant.
- Ran faithful scan-clock replay on the paused May 21 remote BTC1H snapshot:
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_fullscan_entry59_latest_codex`.
  Command used captured `signal_scan` timestamps, live scan semantics, the
  remote BTC research cache, and `--no-public-fallback`.
- Result for `high_conf_80_entry59_70_no_chase`:
  - `14` signals;
  - `13` settled rows;
  - PnL `+$1.99`;
  - win rate `84.6154%`;
  - max DD `-$1.40`;
  - Sharpe `1.455433`.
- Added `scripts\build_btc1h_replay_variant_overlap_audit.py`.
  It compares the active `entry70_no_chase` full-scan replay against the new
  `entry59_70_no_chase` replay.
- Added regression coverage:
  `scripts\test_btc1h_replay_variant_overlap_audit.py`.
- Key overlap result:
  - `entry70_no_chase` replay rows: `14`, PnL `+$1.99`;
  - `entry59_70_no_chase` replay rows: `14`, PnL `+$1.99`;
  - shared rows: `14`;
  - base-only rows: `0`;
  - challenger-only rows: `0`;
  - independent challenger rows: `0`;
  - status: `EXACT_ROW_SET_MATCH`.
- Interpretation: on the paused May 21 live-WS snapshot, `entry59_70_no_chase`
  adds no independent causal replay evidence versus `entry70_no_chase`. It is
  still an interesting historical/basis-stress runner-up, but it should not get
  a separate paper shadow or be double-counted until a broader causal replay or
  future clean forward rows produce a genuinely different row set.
- Wired the overlap audit into `scripts\refresh_btc_evidence_stack.py` before
  the BTC1H research priority matrix. The matrix now records
  `entry59_replay_overlap_status = EXACT_ROW_SET_MATCH` and
  `entry59_independent_replay_rows_vs_active = 0`.
- Validation:
  - local `py_compile` passed for the overlap audit/test, priority matrix/test,
    and refresh controller;
  - local targeted pytest passed: `27 passed`;
  - bounded refresh smoke passed from `btc1h_replay_variant_overlap_audit`
    through `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as
    the expected no-deploy verdict.

## 2026-05-22 - BTC1H entry59 floor-filter audit

- Goal: broaden the entry59-vs-entry70 independence check beyond the paused
  live-WS snapshot without treating a filtered historical variant as a new
  deployable policy.
- Added `scripts\build_btc1h_entry59_floor_filter_audit.py`.
  It compares `high_conf_80_entry59_70_no_chase` against active
  `high_conf_80_entry70_no_chase` across paired direct Predexon aggregate rows
  and the paused live-WS full-scan replay. It skips artifacts where either
  variant is absent; the latest robustness log currently lacks the entry59
  variant, so it is not counted as entry59-deleted evidence.
- Added regression coverage:
  `scripts\test_btc1h_entry59_floor_filter_audit.py`.
- Output:
  `backtest_outputs\btc1h_entry59_floor_filter_audit_latest_codex`.
- Key result:
  - comparable evidence labels: `6`;
  - direct/live comparable rows: active `46`, entry59 `41`;
  - shared exact rows: `40`;
  - active-only exact rows: `6`, PnL `+$1.79`;
  - entry59-only exact rows: `1`, PnL `+$0.32`;
  - entry59-only independent market/side rows: `1`;
  - paused live-WS snapshot entry59-only market/side rows: `0`;
  - paused live-WS snapshot status: `EXACT_ROW_SET_MATCH`;
  - status: `ENTRY59_HAS_INDEPENDENT_MARKET_SIDE_ROWS`, but only from one
    historical direct Predexon row.
- Updated `scripts\build_btc1h_research_priority_matrix.py` to ingest the new
  floor-filter summary. Current priority summary records:
  - `entry59_floor_filter_status = ENTRY59_HAS_INDEPENDENT_MARKET_SIDE_ROWS`;
  - `entry59_independent_market_side_rows_vs_active = 1`;
  - `entry59_independent_replay_rows_vs_active = 0`;
  - `entry59_deleted_low_entry_base_rows = 6`;
  - `entry59_deleted_low_entry_base_pnl = 1.79`;
  - recommended forward policy change remains `none`.
- Interpretation:
  the one historical direct independent row is useful for broader replay
  targeting, but it is not clean forward evidence. Do not start a separate
  entry59 shadow yet; broaden causal replay or wait for future clean official
  rows that actually differ from active entry70.
- Wired the floor-filter audit into `scripts\refresh_btc_evidence_stack.py`
  after replay-overlap and before the BTC1H priority matrix.
- Validation:
  - local `py_compile` passed for the new audit/test, priority matrix/test, and
    refresh controller;
  - local targeted pytest passed: `17 passed`;
  - bounded refresh smoke passed from `btc1h_entry59_floor_filter_audit`
    through `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as
    the expected no-deploy verdict.

## 2026-05-22 - BTC1H broad no-chase extra-row audit

- Goal: make the `high_conf_80_no_chase` watchlist blocker current and
  row-level, instead of relying on an older summary that said the 1s cadence was
  bad.
- Added `scripts\build_btc1h_no_chase_extra_row_audit.py`.
  It compares broad `high_conf_80_no_chase` against active
  `high_conf_80_entry70_no_chase` only on artifacts where both variants are
  present. It recomputes every compared row with fixed `+2c` adverse entry
  stress and taker fee.
- Added regression coverage:
  `scripts\test_btc1h_no_chase_extra_row_audit.py`.
- Output:
  `backtest_outputs\btc1h_no_chase_extra_row_audit_latest_codex`.
- Also ran paused May 21 scan-clock replay for broad no-chase:
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_fullscan_no_chase_latest_codex`.
  It used captured `signal_scan` timestamps, live scan semantics, the remote
  BTC cache, and `--no-public-fallback`.
- Key result:
  - broad no-chase full-scan replay: `17` signals, `16` settled rows, PnL
    `+$1.68`, win `81.25%`, max DD `-$1.41`;
  - full-scan overlap versus active entry70:
    `14` active rows, `17` no-chase rows, `12` shared rows, `2` active-only
    rows, `5` no-chase-only rows, no-chase PnL diff `-$0.31`;
  - full-scan `+2c` extra-row stress:
    `5` extra no-chase rows, `3` independent market/side rows, extra-row PnL
    `-$0.84`;
  - comparable evidence labels: `19`;
  - no-chase rows: `208`;
  - entry70 rows: `173`;
  - exact extra broad no-chase rows: `55`;
  - all exact extra broad no-chase rows were `>70c`;
  - total extra no-chase PnL: `-$2.24`;
  - total extra `>70c` PnL: `-$2.24`;
  - live-WS extra no-chase PnL: `-$3.32`;
  - live-WS labels with negative extra-row PnL: `4 / 6`;
  - 1s live-WS extra `>70c` rows: `11`, PnL `-$1.41`.
- Updated `scripts\build_btc1h_research_priority_matrix.py` to ingest the new
  no-chase summary. Current priority summary records:
  - `no_chase_extra_row_status = NO_CHASE_EXTRA_ROWS_LIVE_WS_DAMAGING`;
  - `no_chase_total_extra_rows = 55`;
  - `no_chase_total_extra_pnl = -2.24`;
  - `no_chase_live_ws_negative_extra_label_count = 4`;
  - `no_chase_stride1_extra_gt70_pnl = -1.41`;
  - `no_chase_fullscan_status = EXTRA_GT70_ROWS_NEGATIVE`;
  - `no_chase_fullscan_extra_rows = 5`;
  - `no_chase_fullscan_extra_pnl = -0.84`;
  - `no_chase_fullscan_pnl_diff = -0.37`.
- Interpretation:
  broad no-chase remains basis-robust historically, but the extra rows admitted
  by removing the 70c cap are damaging in live-WS cadence checks. Do not
  restart or promote broad no-chase from current evidence; any revival needs
  fresh causal replay and official forward rows.
- Wired the audit into `scripts\refresh_btc_evidence_stack.py` before the BTC1H
  research priority matrix.
- Validation:
  - local `py_compile` passed for the new audit/test, priority matrix/test, and
    refresh controller;
  - local targeted pytest passed: `10 passed`;
  - bounded refresh smoke passed from `btc1h_no_chase_extra_row_audit` through
    `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as the
    expected no-deploy verdict.

## 2026-05-22 - BTC1H candidate decision packet update

- Goal: freeze the current candidate decisions inside the BTC1H next-forward
  packet, so basis-stress runner-ups cannot be mistaken for restart or
  deployment candidates.
- Updated `scripts\build_btc1h_next_forward_candidate_packet.py` to ingest:
  - `backtest_outputs\btc1h_research_priority_matrix_latest_codex\btc1h_research_priority_matrix.csv`;
  - `backtest_outputs\btc1h_research_priority_matrix_latest_codex\btc1h_research_priority_summary.csv`.
- New packet output:
  `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex\btc1h_candidate_decision_packet.csv`.
- Current decisions:
  - `high_conf_80_entry70_no_chase`:
    `keep_as_only_frozen_clean_clock_control`, eligible only for an explicitly
    authorized controlled paper-shadow restart and still not deployable;
  - `high_conf_80_entry59_70_no_chase`:
    `defer_shadow_until_independent_causal_rows`, because the paused live-WS
    snapshot was an exact row-set match with active entry70 and the broader
    floor audit has only one diagnostic historical independent market-side row;
  - `high_conf_80_no_chase`: `basis_watchlist_only_no_restart`, because the
    extra-row audit shows `55` exact extra rows for `-$2.24` and full-scan
    extra rows for `-$0.84`;
  - `high_conf_80`: `deprioritize`.
- `run_info.json` now records:
  - `candidate_decision_count = 4`;
  - `recommended_forward_policy_change = none`;
  - `top_causal_replay_runner_up = high_conf_80_entry59_70_no_chase`;
  - `top_basis_stress_variant = high_conf_80_no_chase`.
- Validation:
  - local `py_compile` passed for the packet builder/test;
  - local targeted pytest passed for the packet, priority matrix, promotion
    gap, and refresh-controller tests: `10 passed`;
  - bounded refresh passed from `btc1h_research_priority_matrix` through
    `btc1h_promotion_gap_matrix`, with readiness exit `1` accepted as the
    expected no-deploy verdict;
  - local packet regenerated from the current artifact stack.

## 2026-05-22 - BTC1H clean-clock gate wired into refresh

- Goal: prevent fresh BTC1H readiness/packet artifacts from depending on a
  stale `btc1h_clean_evidence_clock_gate_latest_codex` output.
- Full read-only refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --skip-packet`.
  It completed all `50` then-wired steps at `2026-05-22T12:43:32Z`, with
  deployment readiness exit `1` accepted as the expected no-deploy verdict.
- The refresh exposed a source-boundary problem:
  - local `btc_forward_shadow_status_latest_codex` reports the local BTC1H
    paper shadow as `NOT_RUNNING`;
  - BTC1H strategy/readiness artifacts still correctly use the pulled remote
    official-settlement artifact with `11` BTC1H rows;
  - SSH to `ClawService@100.92.9.80` failed from this session with
    `Permission denied`, so remote liveness could not be refreshed live.
- Patched `scripts\refresh_btc_evidence_stack.py` to add
  `btc1h_clean_evidence_clock_gate` after BTC1H multi-holdout research and
  before the next-forward packet, deployment readiness, and promotion-gap
  matrix.
- The clean-clock step accepts return code `1` as an expected blocked-gate
  result, like deployment readiness, instead of failing the whole read-only
  refresh when the correct conclusion is no deploy.
- Updated `scripts\test_btc_evidence_stack_refresh.py` so the refresh-order test
  enforces the new dependency and allows only the clean-clock gate and
  readiness to return `(0, 1)`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_clean_clock_current_latest_codex --start-at btc1h_multi_holdout_research --stop-after btc1h_promotion_gap_matrix --skip-packet`.
  It completed all `16` selected steps, with planned total now `51`.
- Current clean-clock summary:
  - `gate_status = BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - `clean_evidence_clock_ready = False`;
  - `blocker_count = 9`;
  - `status_source = status_json`;
  - remote BTC1H status snapshot updated at
    `2026-05-22T08:01:20.103367Z`, about `284.9` minutes old at audit time;
  - `official_rows = 11`, `official_pnl = +$0.50`,
    `official_proxy_mismatches = 1`;
  - `blank_policy_official_rows = 11`;
  - sidecar schema is missing the scan-time model-input and policy fields.
- Validation:
  - local `py_compile` passed for the refresh controller, clean-clock gate, and
    tests;
  - local targeted pytest passed:
    `scripts\test_btc_evidence_stack_refresh.py`
    `scripts\test_btc1h_clean_evidence_clock_gate.py` -> `11 passed`.
- Interpretation:
  active BTC1H remains near-deployable research only. The refreshed gate now
  makes the stale remote-status boundary explicit; no new official rows count
  toward promotion without an explicitly authorized clean-clock restart and
  current source/status verification.

## 2026-05-22 - BTC1H remote provenance in forward report

- Goal: make the normal forward evidence report distinguish local BTC process
  status from pulled remote BTC1H official-settlement evidence.
- Updated `scripts\build_btc_forward_evidence_report.py` to accept and report:
  - `--remote-official-dir`, default
    `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex`;
  - `--btc1h-remote-status-json`, default
    `runtime\remote_status\btc1h_status_latest.json`;
  - `--btc1h-clean-clock-summary`, default
    `backtest_outputs\btc1h_clean_evidence_clock_gate_latest_codex\btc1h_clean_evidence_clock_summary.csv`.
- New output:
  `backtest_outputs\btc_forward_evidence_report_latest_codex\btc1h_remote_provenance.csv`.
- Added report section `BTC1H Remote Provenance`, with fields for:
  - local shadow running/source freshness;
  - pulled remote status timestamp and age;
  - remote official rows/PnL/proxy mismatch count;
  - clean-clock gate status and blank-policy rows.
- Added regression coverage:
  `scripts\test_btc_forward_evidence_report.py`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_forward_provenance_latest_codex --start-at forward_evidence_report --stop-after gpt_pro_action_status --skip-packet`.
  It completed all `5` selected downstream steps.
- Current BTC1H provenance result:
  - `provenance_verdict = REMOTE_STATUS_STALE_OR_UNAVAILABLE`;
  - local BTC1H shadow status: `local_shadow_running = False`;
  - remote BTC1H official rows: `11`;
  - remote official PnL: `+$0.50`;
  - remote proxy/official mismatches: `1`;
  - remote status last pulled at `2026-05-22T08:01:20.103367Z`, about
    `289.4` minutes old at the report run;
  - clean-clock status remains `BLOCKED_CONTROLLED_RESTART_REQUIRED`.
- Validation:
  - local `py_compile` passed for the forward report and new test;
  - local targeted pytest passed:
    `scripts\test_btc_forward_evidence_report.py` -> `1 passed`.
- Interpretation:
  this does not add deployment evidence; it prevents a false read that local
  `NOT_RUNNING` rows erased the remote official sample, or that the stale
  remote official sample proves current remote liveness. BTC1H remains
  near-deployable research only until a fresh remote status plus clean evidence
  clock exists.

## 2026-05-22 - BTC1H remote provenance in consistency audit

- Goal: make the downstream consistency/control layer carry the same BTC1H
  remote provenance instead of reducing BTC1H to a generic local
  `not_running_or_status_missing` row.
- Updated `scripts\build_btc_forward_consistency_audit.py` to read
  `backtest_outputs\btc_forward_evidence_report_latest_codex\btc1h_remote_provenance.csv`.
- New BTC1H consistency fields include:
  - `btc1h_remote_provenance_verdict`;
  - `btc1h_remote_status_age_minutes`;
  - `btc1h_remote_official_rows`;
  - `btc1h_remote_official_pnl`;
  - `btc1h_remote_proxy_official_mismatches`;
  - `btc1h_clean_clock_status`;
  - `btc1h_clean_clock_ready`.
- Current consistency result:
  - `consistent_enough_for_promotion_count = 0 / 4`;
  - BTC1H `agreement_status =
    btc1h_remote_status_stale_or_unavailable`;
  - BTC1H remote official rows remain visible as `11`, with official PnL
    `+$0.50` and `1` remote proxy/official mismatch;
  - BTC1H blockers now explicitly include
    `btc1h_remote_status_stale_or_unavailable`,
    `btc1h_clean_evidence_clock_not_ready`,
    `btc1h_remote_proxy_official_mismatch`, and
    `btc1h_too_few_remote_official_rows`, alongside the local
    `not_running_or_status_missing` blocker.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_consistency_provenance_latest_codex --start-at forward_consistency_audit --stop-after gpt_pro_action_status --skip-packet`.
  It completed both selected downstream steps.
- Validation:
  - local `py_compile` passed for the consistency audit, forward report,
    refresh controller, and focused tests;
  - local targeted pytest passed:
    `scripts\test_btc_forward_consistency_audit.py`
    `scripts\test_btc_forward_evidence_report.py`
    `scripts\test_btc_evidence_stack_refresh.py` -> `11 passed`.
- Interpretation:
  BTC1H remains near-deployable research only. The control artifacts now show
  the useful remote official sample and the reason it still cannot count as
  fresh promotion evidence in the same row.

## 2026-05-22 - BTC1H remote provenance in GPT action status

- Goal: make the final GPT Pro/local-action control artifact preserve BTC1H
  remote-provenance blockers, not just generic replay coverage or local
  source-freshness blockers.
- Updated `scripts\build_btc_gpt_pro_action_status.py` to consume the BTC1H row
  from
  `backtest_outputs\btc_forward_consistency_audit_latest_codex\forward_consistency_summary.csv`.
- Added checklist row `btc1h_remote_provenance`.
  Current result:
  - `status = BLOCKS_BTC1H_PROMOTION`;
  - `passes_for_deployment = False`;
  - `verdict = REMOTE_STATUS_STALE_OR_UNAVAILABLE`;
  - `remote_status_age_minutes = 289.4126`;
  - `remote_official_rows = 11`;
  - `remote_official_pnl = +$0.50`;
  - `remote_proxy_official_mismatches = 1`;
  - `clean_clock_status = BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - `clean_clock_ready = False`.
- The BTC1H row in
  `backtest_outputs\btc_gpt_pro_action_status_latest_codex\gpt_pro_candidate_status.csv`
  now carries:
  - `current_status = BTC1H_REMOTE_STATUS_STALE_OR_UNAVAILABLE`;
  - `forward_consistency_status =
    btc1h_remote_status_stale_or_unavailable`;
  - the same remote official row/PnL/mismatch and clean-clock fields.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_gpt_action_provenance_latest_codex --start-at gpt_pro_action_status --stop-after gpt_pro_action_status --skip-packet`.
  It completed the selected step successfully.
- Validation:
  - local `py_compile` passed for the GPT action status, forward consistency,
    forward report, refresh controller, and focused tests;
  - local targeted pytest passed:
    `scripts\test_btc_gpt_pro_action_status.py`
    `scripts\test_btc_forward_consistency_audit.py`
    `scripts\test_btc_forward_evidence_report.py`
    `scripts\test_btc_evidence_stack_refresh.py` -> `22 passed`.
- Interpretation:
  BTC1H remains observe-only / near-deployable research. The final action
  artifact now makes it hard to confuse `11` pulled remote official rows with
  fresh, clean-clock, promotion-usable evidence.

## 2026-05-22 - BTC1H near-deployable label tightened

- Goal: prevent the fixed multi-holdout research artifact from labeling a BTC1H
  candidate as `near_deployable_candidate = True` when positive forward PnL is
  still accompanied by official/proxy mismatch, row-unfaithful replay, missing
  exact live model inputs, or cached-TTL-only fills.
- Updated `scripts\build_btc1h_multi_holdout_research.py`:
  - added `promotion_readiness_status`;
  - added `near_deployable_disqualifying_blockers`;
  - changed `near_deployable_candidate` so only sample-size plus a final
    counterfactual/live-execution validation blocker can still be called near
    deployable.
- Current active BTC1H result in
  `backtest_outputs\btc1h_multi_holdout_research_latest_codex\btc1h_candidate_gate_summary.csv`:
  - `research_promising = True`;
  - `near_deployable_candidate = False`;
  - `promotion_readiness_status =
    promising_but_blocked_by_official_or_fidelity_gates`;
  - forward official rows/PnL: `11` / `+$0.50`;
  - forward official mismatch rate: `9.09%`;
  - disqualifying blockers:
    `official_proxy_mismatch_gate_failed`,
    `counterfactual_replay_not_row_faithful`,
    `selected_signal_exact_cached_ttl_missing_for_promotion`,
    `forward_rows_include_cached_ttl_only_fills`.
- Updated `scripts\build_btc1h_next_forward_candidate_packet.py` so
  `btc1h_current_evidence_snapshot.csv` no longer hardcodes
  `deployability_state = near_deployable_research_only`. It now carries the
  multi-holdout `promotion_readiness_status`.
- Current next-forward packet evidence now says:
  - `near_deployable_candidate = False`;
  - `deployability_state =
    promising_but_blocked_by_official_or_fidelity_gates`;
  - clean clock remains `BLOCKED_CONTROLLED_RESTART_REQUIRED`;
  - restart authorization remains not ready in the current generated packet.
- Bounded refreshes:
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_multi_holdout_gate_latest_codex --start-at btc1h_multi_holdout_research --stop-after gpt_pro_action_status --skip-packet`
    completed all `21` selected downstream steps;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_candidate_packet_readiness_state_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
    completed all `8` selected downstream steps.
- Validation:
  - local `py_compile` passed for the multi-holdout, next-forward packet,
    research priority matrix, promotion-gap matrix, refresh controller, and
    their focused tests;
  - local targeted pytest passed:
    `scripts\test_btc1h_multi_holdout_research.py`
    `scripts\test_btc1h_next_forward_candidate_packet.py`
    `scripts\test_btc1h_research_priority_matrix.py`
    `scripts\test_btc1h_promotion_gap_matrix.py`
    `scripts\test_btc_evidence_stack_refresh.py` -> `13 passed`.
- Interpretation:
  the active BTC1H `high_conf_80_entry70_no_chase` path is still the best
  forward research control, but it is no longer described as near-deployable.
  It is promising but blocked by official-settlement disagreement and
  replay/model-input fidelity gaps, before even reaching the clean-clock sample
  size gate.

## 2026-05-22 - BTC1H replay mismatch diagnosis made row-level

- Tightened `scripts\build_btc1h_replay_vs_ledger_reconciliation.py` so
  `promotion_usable_replay` now requires row-for-row market/side, entry-price,
  and PnL parity. Exact market/side agreement alone is no longer enough if the
  replay drifts by even one cent on entry or PnL.
- Added
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_latest_codex\btc1h_replay_vs_ledger_mismatch_diagnosis.csv`.
  It joins reconciliation failures to the captured
  `btc1h_selected_signal_decision_chain.csv` and
  `btc1h_fill_decision_official_chain.csv` artifacts.
- Current candidate-scan replay remains not promotion usable:
  - actual official rows: `11`;
  - replay rows: `10`;
  - exact market/side matches: `9 / 11`;
  - entry-price drift rows: `2`;
  - PnL drift rows: `2`;
  - actual official PnL: `+$0.50`;
  - replay PnL: `+$1.11`;
  - replay-minus-actual PnL: `+$0.61`;
  - blockers:
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;entry_price_not_row_for_row_equal;pnl_not_row_for_row_equal`.
- The five row-level diagnosis rows are:
  - `KXBTCD-26MAY1911-T76299.99|no`:
    `captured_live_fill_missing_after_skip_then_fill`. The captured chain had
    two selected scans for this market: one `skip` from
    `failed_ws_reprice_filter`, then one `paper_fill`. Replay missed the later
    actual fill, which was the `-$0.71` official row.
  - `KXBTCD-26MAY2010-T77199.99|no`:
    `captured_live_fill_replaced_by_replay_market`. Actual paper filled the
    `T77199.99` NO market at `0.57`, but replay chose the same event's
    `T77299.99` NO market instead.
  - `KXBTCD-26MAY2010-T77299.99|no`:
    `replay_extra_market_replacing_captured_live_fill`.
  - `KXBTCD-26MAY2107-T77299.99|no`:
    `matched_market_with_entry_or_pnl_drift`. Market/side matched, but replay
    entry was `0.70` versus ledger `0.69`.
  - `KXBTCD-26MAY2110-T76699.99|yes`:
    `matched_market_with_entry_or_pnl_drift`. Market/side matched, but replay
    entry was `0.65` versus ledger `0.66`.
- Refreshed downstream artifacts from `btc1h_multi_holdout_research` through
  `gpt_pro_action_status`; all `21` selected read-only steps completed. The
  clean-clock blocker now carries the stricter entry/PnL parity blocker string.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_vs_ledger_reconciliation.py scripts\build_btc1h_multi_holdout_research.py scripts\build_btc1h_clean_evidence_clock_gate.py scripts\build_btc1h_promotion_gap_matrix.py`;
  - `python -m pytest scripts\test_btc1h_replay_vs_ledger_reconciliation.py scripts\test_btc1h_multi_holdout_research.py scripts\test_btc1h_clean_evidence_clock_gate.py scripts\test_btc1h_promotion_gap_matrix.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-replay-diagnosis-final`
    -> `18 passed`.
- Interpretation:
  the active BTC1H candidate is still a useful research control, but the
  remaining replay blocker is now sharper: the offline replay is not merely
  short by count, it fails specific live-chain behaviors around skip-then-fill,
  event-market replacement, and one-cent entry/PnL drift. No process changes or
  deployment actions followed from this diagnostic.

## 2026-05-22 - BTC1H cached-TTL replay diagnostic was negative

- Added diagnostic-only `--model-ttl-override-min` support to
  `scripts\replay_btc1h_core_ws_counterfactual.py`. It lets replay use a fixed
  model probability horizon while still enforcing the real event TTL entry
  window. This is intentionally not the default and is not forward
  scan-time-TTL evidence.
- Ran a bounded candidate-scan replay with `--model-ttl-override-min 15` on the
  May 21 paused BTC1H snapshot:
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_candidate_scans_ttl15_latest_codex`.
- Reconciliation artifact:
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_ttl15_latest_codex`.
  Result:
  - actual official rows: `11`;
  - replay rows: `7`;
  - exact market/side matches: `7 / 11`;
  - entry-price drift rows: `3`;
  - PnL drift rows: `3`;
  - actual official PnL: `+$0.50`;
  - replay PnL: `+$1.21`;
  - replay-minus-actual PnL: `+$0.71`;
  - `promotion_usable_replay = false`;
  - blockers:
    `missing_actual_rows;replay_row_count_differs;entry_price_not_row_for_row_equal;pnl_not_row_for_row_equal`.
- Interpretation:
  a simple fixed `15` minute cached-horizon replay does not explain the old
  live rows. It removes the same-event replacement problem but misses more
  actual fills and increases entry/PnL drift. The remaining faithful-replay
  problem therefore likely requires exact captured model-input state and
  reprice/fill sequencing, not a single global TTL override.
- Validation:
  - `python -m py_compile scripts\replay_btc1h_core_ws_counterfactual.py scripts\test_btc1h_replay_signal_scan_pruning.py`;
  - `python -m pytest scripts\test_btc1h_replay_signal_scan_pruning.py scripts\test_btc1h_replay_vs_ledger_reconciliation.py -q --basetemp .pytest-codex-tmp-btc1h-ttl-replay-final`
    -> `8 passed`.

## 2026-05-22 - BTC1H replay root-cause audit added to refresh stack

- Added `scripts\build_btc1h_replay_root_cause_audit.py` and artifact
  `backtest_outputs\btc1h_replay_root_cause_audit_latest_codex`.
  It joins replay-vs-ledger mismatch rows with:
  - selected-signal model parity;
  - selected-signal to order-decision chain timing;
  - replay-mode reconciliation summaries.
- Current root-cause summary:
  - root-cause rows: `5`;
  - unique root causes: `4`;
  - dominant cause:
    `same_event_market_selection_not_row_faithful`;
  - counts:
    `same_event_market_selection_not_row_faithful=2`,
    `reprice_skip_then_fill_sequence_missing=1`,
    `order_decision_reprice_fill_price_missing=1`,
    `blocked_dedupe_or_post_selected_scan_used_as_replay_clock=1`;
  - requires order-decision reprice/fill modeling: `true`;
  - requires blocked-dedupe filtering: `true`;
  - promotion usable from this audit: `false`.
- Replay mode comparison now shows no attempted replay mode is promotion usable:
  - candidate-scan: `9 / 11` exact market/side matches, replay PnL `+$1.11`
    vs actual `+$0.50`, blockers include event replacement and entry/PnL
    drift;
  - candidate-selected-market: same `9 / 11` and same blockers;
  - selected-scan / selected-market: only `6 / 11` exact matches;
  - fixed `15` minute cached-TTL candidate scan: `7 / 11` exact matches;
  - fullscan prefilter: `14` replay rows but still only `9 / 11` exact
    matches, with extra rows and event replacement.
- Patched `scripts\refresh_btc_evidence_stack.py` so the root-cause audit runs
  after `btc1h_clean_evidence_clock_gate` and before downstream BTC1H summary,
  readiness, and action-status artifacts.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_root_cause_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `20` selected read-only steps. Readiness returned `1` as the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_root_cause_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc1h_replay_root_cause_audit.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_root_cause_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-root-cause-final`
    -> `10 passed`.
- Current candidate verdict remains unchanged:
  `high_conf_80_entry70_no_chase` is `research_promising = true` but
  `near_deployable_candidate = false`, `deployable_now = false`, and
  `promotion_readiness_status =
  promising_but_blocked_by_official_or_fidelity_gates`.

## 2026-05-22 - BTC1H root causes surfaced in decision artifacts

- Patched `scripts\build_btc1h_promotion_gap_matrix.py` so the
  `row_for_row_replay` gate now consumes
  `backtest_outputs\btc1h_replay_root_cause_audit_latest_codex` in addition to
  the generic replay reconciliation blockers.
- Patched `scripts\build_btc1h_next_forward_candidate_packet.py` so
  `btc1h_current_evidence_snapshot.csv`, `btc1h_promotion_gate_specs.csv`, and
  `run_info.json` carry:
  - replay root-cause counts;
  - dominant root cause;
  - whether replay requires order-decision reprice/fill modeling;
  - whether replay requires blocked-dedupe filtering;
  - promotion-usable replay modes versus modes compared.
- Current promotion gap summary now records:
  - `near_deployable_research_candidate = false`;
  - replay root causes:
    `same_event_market_selection_not_row_faithful=2;reprice_skip_then_fill_sequence_missing=1;order_decision_reprice_fill_price_missing=1;blocked_dedupe_or_post_selected_scan_used_as_replay_clock=1`;
  - dominant replay root cause:
    `same_event_market_selection_not_row_faithful`;
  - promotion-usable replay modes: `0 / 6`.
- Current next-forward evidence snapshot now carries:
  - `replay_requires_order_decision_reprice_model = True`;
  - `replay_requires_blocked_dedupe_filter = True`;
  - `replay_requires_exact_model_inputs = False` for the current diagnosed
    mismatch rows, while clean future rows still require exact model-input
    capture through the separate clean-clock gate;
  - `replay_promotion_usable_modes = 0`;
  - `replay_modes_compared = 6`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_root_cause_surfaces_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
  completed all `8` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_promotion_gap_matrix.py scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_promotion_gap_matrix.py scripts\test_btc1h_next_forward_candidate_packet.py`;
  - `python -m pytest scripts\test_btc1h_promotion_gap_matrix.py scripts\test_btc1h_next_forward_candidate_packet.py -q --basetemp .pytest-codex-tmp-btc1h-root-cause-surfaces`
    -> `2 passed`.
- Interpretation:
  the decision surface is now harder to misread. The BTC1H active candidate
  remains the frozen research control, but the action artifacts explicitly say
  no replay mode is promotion usable and list the exact replay semantics that
  must be fixed before any replay evidence can count.

## 2026-05-22 - BTC1H replay repair feasibility split added

- Added `scripts\build_btc1h_replay_repair_feasibility.py` and artifact
  `backtest_outputs\btc1h_replay_repair_feasibility_latest_codex`.
- The checklist separates replay repairs testable on the existing paused
  snapshot from promotion gates that require future clean-clock official rows.
- Current summary:
  - `deployable_now = false`;
  - `near_deployable_after_current_replay_repairs = false`;
  - `replay_repair_can_make_deployable_now = false`;
  - replay promotion-usable modes: `0 / 6`;
  - current official rows: `11` versus the `50` row promotion floor;
  - official/proxy mismatch rate: `9.09%` versus the `2%` max;
  - clean clock remains `BLOCKED_CONTROLLED_RESTART_REQUIRED`.
- Existing paused-snapshot replay repairs:
  - `order_decision_reprice_fill_model`: required and testable now; current
    blockers include two root-cause rows across skip-then-fill sequencing and
    order-decision entry-price/PnL drift.
  - `blocked_dedupe_filter`: required and testable now; one root-cause row
    shows replay trading from a blocked/dedupe or post-selected scan clock.
  - `same_event_selected_market_dedupe`: required and partially testable now;
    two root-cause rows replace the captured live market with another market in
    the same event.
  - `row_for_row_reconciliation_after_repairs`: required and testable now, but
    currently all six compared replay modes fail promotion usability.
- Future clean-clock requirements:
  - `exact_model_input_capture`: old selected rows have
    `captured_ttl_available_rows = 0` and selected-signal model parity fails;
  - `official_proxy_basis_gate`: the current stale official sample has one
    proxy-win/official-loss flip and too high a mismatch rate;
  - `sample_size_gate`: current rows are old/not-clean-clock and short by `39`
    rows against the `50` row floor;
  - `clean_policy_identity`: old rows have blank policy identity and missing
    sidecar fields, so they cannot count toward the next evidence clock.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_replay_repair_feasibility` runs immediately after
  `btc1h_replay_root_cause_audit` and before downstream BTC1H summaries.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_replay_repair_feasibility_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `21` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_repair_feasibility.py scripts\test_btc1h_replay_repair_feasibility.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_repair_feasibility.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-replay-repair-feasibility`
    -> `8 passed`;
  - after wording cleanup:
    `python -m pytest scripts\test_btc1h_replay_repair_feasibility.py -q --basetemp .pytest-codex-tmp-btc1h-repair-feasibility-single`
    -> `1 passed`.
- Interpretation:
  the next best replay engineering target is order-decision/reprice modeling
  plus blocked-dedupe/same-event replay semantics on the paused snapshot. Even
  if that succeeds, the candidate remains blocked until future clean-clock rows
  satisfy exact model-input capture, official/proxy basis, sample size, and
  policy-identity gates.

## 2026-05-22 - BTC1H captured order-decision replay baseline

- Added `scripts\build_btc1h_order_decision_replay_baseline.py` and artifact
  `backtest_outputs\btc1h_order_decision_replay_baseline_latest_codex`.
  The builder materializes captured `order_decision` paper fills from the
  paused BTC1H snapshot into replay-shaped trade rows.
- This is deliberately diagnostic only:
  - it is an exact captured decision-log baseline;
  - it is not an independent counterfactual replay;
  - it cannot make replay evidence promotion usable by itself.
- Patched `scripts\build_btc1h_replay_vs_ledger_reconciliation.py` with
  `--replay-evidence-kind`. Default independent counterfactual behavior is
  unchanged, but diagnostic baselines now report row fidelity separately from
  promotion usability.
- Captured order-decision baseline result:
  - baseline rows: `11`;
  - settled rows: `11`;
  - official PnL: `+$0.50`;
  - official premium: `$7.50`;
  - official ROP: `6.6667%`;
  - official win rate: `72.7273%`;
  - max drawdown: `-$1.76`;
  - `promotion_usable_as_counterfactual = false`.
- Baseline reconciliation artifact:
  `backtest_outputs\btc1h_order_decision_replay_baseline_reconciliation_latest_codex`.
  It proves the captured decision/settlement/reconciliation path can be exact:
  - `replay_evidence_kind = captured_order_decision_log`;
  - actual rows: `11`;
  - replay rows: `11`;
  - exact market/side matches: `11 / 11`;
  - entry-price drift rows: `0`;
  - PnL drift rows: `0`;
  - row-fidelity exact: `true`;
  - `promotion_usable_replay = false`;
  - blocker:
    `diagnostic_replay_not_independent_counterfactual`.
- Updated `scripts\build_btc1h_replay_repair_feasibility.py` so its summary
  carries the captured order-decision baseline fields:
  `order_decision_baseline_rows = 11`,
  `order_decision_baseline_row_fidelity_exact = True`,
  `order_decision_baseline_promotion_usable_replay = False`.
- Patched `scripts\refresh_btc_evidence_stack.py` so the order-decision
  baseline and baseline reconciliation run after
  `btc1h_replay_root_cause_audit` and before
  `btc1h_replay_repair_feasibility`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_order_decision_baseline_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `23` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_order_decision_replay_baseline.py scripts\test_btc1h_order_decision_replay_baseline.py scripts\build_btc1h_replay_vs_ledger_reconciliation.py scripts\test_btc1h_replay_vs_ledger_reconciliation.py scripts\build_btc1h_replay_repair_feasibility.py scripts\test_btc1h_replay_repair_feasibility.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_order_decision_replay_baseline.py scripts\test_btc1h_replay_vs_ledger_reconciliation.py scripts\test_btc1h_replay_repair_feasibility.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-order-decision-baseline-final`
    -> `13 passed`.
- Interpretation:
  the data and official-settlement path are not the reason independent replay is
  failing. When anchored to captured order decisions, row fidelity is exact.
  The remaining BTC1H blocker is specifically the independent replay engine's
  scan/model/market-selection semantics, plus future clean-clock official
  evidence gates.

## 2026-05-22 - BTC1H independent replay repair target matrix

- Added `scripts\build_btc1h_replay_repair_target_matrix.py` and artifact
  `backtest_outputs\btc1h_replay_repair_target_matrix_latest_codex`.
- The matrix compares:
  - current independent counterfactual replay reconciliation;
  - captured order-decision baseline reconciliation;
  - replay root-cause rows;
  - replay repair feasibility summary.
- Current summary:
  - current independent row fidelity exact: `false`;
  - current independent promotion usable: `false`;
  - captured order-decision baseline row fidelity exact: `true`;
  - captured order-decision baseline promotion usable: `false`;
  - `independent_replay_gap_is_not_data_pipeline = true`;
  - current independent replay exact market/side matches: `9 / 11`;
  - current independent replay rows: `10` vs actual `11`;
  - current independent replay-minus-actual PnL: `+$0.61`;
  - deployable now: `false`.
- The next independent replay repair targets are now machine-readable:
  - `skip_then_fill_sequence`: `1` root-cause row in `KXBTCD-26MAY1911`;
    addresses a missing actual fill after captured skip-then-fill sequencing.
  - `same_event_market_selection`: `2` root-cause rows in
    `KXBTCD-26MAY2010`; addresses one ledger-only actual fill, one replay-only
    extra row, and event-level market replacement.
  - `order_decision_reprice_fill_price`: `1` root-cause row in
    `KXBTCD-26MAY2107`; addresses one-cent entry/PnL drift.
  - `blocked_dedupe_scan_clock`: `1` root-cause row in
    `KXBTCD-26MAY2110`; addresses a one-cent drift caused by trading from the
    wrong scan clock.
- Future-row blockers remain unchanged and are carried through the summary:
  `exact_model_input_capture;official_proxy_basis_gate;sample_size_gate;clean_policy_identity`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_replay_repair_target_matrix` runs after repair feasibility and before
  downstream BTC1H decision/status artifacts.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_replay_repair_target_matrix_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `24` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_repair_target_matrix.py scripts\test_btc1h_replay_repair_target_matrix.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_repair_target_matrix.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-repair-target-refresh`
    -> `8 passed`.
- Interpretation:
  this does not make BTC1H deployable, but it makes the next independent replay
  repair measurable. The captured decision baseline proves the raw
  decision/settlement path can be exact; independent replay must now eliminate
  the four named row-fidelity target classes without using captured decisions
  as the trade source.

## 2026-05-22 - BTC1H replay repair prerequisite audit

- Added `scripts\build_btc1h_replay_repair_prerequisite_audit.py` and artifact
  `backtest_outputs\btc1h_replay_repair_prerequisite_audit_latest_codex`.
  This audit tightens the repair target matrix by asking which targets are
  actually independently repairable from the current paused snapshot versus
  blocked by missing exact model-input or TTL capture.
- Current summary:
  - repair target count: `4`;
  - existing-snapshot repairable targets: `2`;
  - partial targets: `1`;
  - false targets: `1`;
  - repairable from current snapshot:
    `order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
  - partial from current snapshot: `same_event_market_selection`;
  - not independently repairable from current snapshot:
    `skip_then_fill_sequence`;
  - future exact-input blocked targets:
    `same_event_market_selection;skip_then_fill_sequence`;
  - `all_targets_repairable_from_existing_snapshot = false`;
  - `near_deployable_after_current_replay_repairs = false`;
  - deployable now: `false`.
- Row-level split:
  - `order_decision_reprice_fill_price` can be tested from the current
    snapshot on `KXBTCD-26MAY2107`: market/side already match, selected-chain
    fill exists, and the captured decision fill price explains the one-cent
    entry/PnL drift.
  - `blocked_dedupe_scan_clock` can be tested from the current snapshot on
    `KXBTCD-26MAY2110`: selected-chain fill exists and replay is `0.727796`
    seconds away from the selected timing.
  - `same_event_market_selection` is only partial on `KXBTCD-26MAY2010`:
    selected-chain fill exists for the actual market, but the replay-only
    replacement market has no captured selected-chain row and model/edge parity
    still drifts by `0.00343705697461` probability / `0.343705697461` cents.
  - `skip_then_fill_sequence` is false as an independent repair from this
    snapshot on `KXBTCD-26MAY1911`: the chain has skip/fill rows, but scan-TTL
    parity has `0` recomputed signal rows and `2` no-signal rows for the target
    fill market.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_replay_repair_prerequisite_audit` runs after
  `btc1h_replay_repair_target_matrix` and before downstream BTC1H
  official-basis, promotion-gap, and GPT-status artifacts.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_repair_prereq_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `25` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_repair_prerequisite_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc1h_replay_repair_prerequisite_audit.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_repair_prerequisite_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-repair-prereq`
    -> `8 passed`.
- Interpretation:
  the previous repair target matrix should not be read as "all four targets are
  pure replay-code fixes." The current snapshot is enough to test the two
  matched-drift repairs, but same-event market choice and skip-then-fill
  sequencing still require exact model-input/TTL capture or future clean-clock
  evidence. BTC1H remains observe-only and not near-deployable.

## 2026-05-22 - BTC1H replay repair attempt audit

- Refreshed the strict selected-scan / selected-market replay into
  `backtest_outputs\btc1h_core_ws_counterfactual_snapshot_selected_scans_latest_codex`
  using the paused May 21 BTC1H snapshot, captured `signal_scan` clock,
  `--selected-scan-only`, `--selected-market-only`, and `--no-public-fallback`.
  Result: `6` replay rows, PnL `-$0.14`, win rate `66.6667%`, with all rows
  settled from captured lifecycle data.
- Refreshed reconciliation artifact
  `backtest_outputs\btc1h_replay_vs_ledger_reconciliation_selected_scans_latest_codex`.
  Strict selected-scan replay is not a repair:
  - actual official rows: `11`;
  - replay rows: `6`;
  - exact market/side matches: `6`;
  - ledger-only rows: `5`;
  - replay-only rows: `0`;
  - event replacement rows: `0`;
  - entry/PnL drift rows: `1 / 1`;
  - replay-minus-actual PnL: `-$0.64`;
  - blockers:
    `missing_actual_rows;replay_row_count_differs;entry_price_not_row_for_row_equal;pnl_not_row_for_row_equal`.
- Added `scripts\build_btc1h_replay_repair_attempt_audit.py` and artifact
  `backtest_outputs\btc1h_replay_repair_attempt_audit_latest_codex`.
  The audit compares:
  - a diagnostic matched-drift patch simulation for the two targets that the
    prerequisite audit marked `True`;
  - the real strict selected-scan / selected-market replay attempt.
- Current attempt summary:
  - repairable targets from prerequisite audit:
    `order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
  - matched-drift diagnostic patch rows: `2`;
  - matched-drift patch removes entry drift: `true`;
  - strict selected-scan attempt verdict: `REGRESSES_ROW_FIDELITY`;
  - best current-snapshot attempt:
    `matched_drift_decision_fill_price_patch_simulation`;
  - remaining blockers after best attempt:
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`;
  - current snapshot repairs make replay promotion usable: `false`;
  - near-deployable after current replay repairs: `false`;
  - deployable now: `false`.
- Attempt rows:
  - `matched_drift_decision_fill_price_patch_simulation` removes the two
    entry/PnL drift rows on `KXBTCD-26MAY2107` and `KXBTCD-26MAY2110`, but
    replay still has `10` rows vs `11` actual, only `9` market/side matches,
    `2` ledger-only rows, `1` replay-only row, `2` event-replacement rows, and
    replay-minus-actual PnL `+$0.61`.
  - `strict_selected_scan_selected_market_replay` is worse for row fidelity:
    `6` replay rows vs `11` actual, `5` ledger-only rows, and replay-minus-
    actual PnL `-$0.64`. It removes event replacements by dropping too many
    actual fills, not by faithfully repairing blocked/dedupe semantics.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_replay_repair_attempt_audit` runs after the prerequisite audit and
  before downstream BTC1H official-basis, promotion-gap, and GPT-status
  artifacts.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_repair_attempt_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `26` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_repair_attempt_audit.py scripts\test_btc1h_replay_repair_attempt_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_repair_attempt_audit.py scripts\test_btc1h_replay_repair_prerequisite_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-repair-attempt-final`
    -> `9 passed`.
- Interpretation:
  the next replay-engineering step is not simply "turn on selected-scan-only."
  Current snapshot repairs can remove the two matched drift rows, but faithful
  independent replay still needs exact model-input/TTL capture plus row-for-row
  market selection, skip/fill sequence, entry price, and PnL parity. BTC1H
  remains observe-only and not near-deployable.

## 2026-05-22 - BTC1H replay repair attempts surfaced in top-level gates

- Patched `scripts\build_btc1h_promotion_gap_matrix.py` so the promotion
  matrix has an explicit `replay_repair_attempts` gate instead of leaving the
  repair-attempt verdict buried in the lower-level audit. Current status:
  `BLOCKED`.
- Current top-level replay-repair gate evidence:
  - attempts: `2`;
  - best current-snapshot attempt:
    `matched_drift_decision_fill_price_patch_simulation`;
  - matched-drift patch rows: `2`;
  - strict selected-scan verdict: `REGRESSES_ROW_FIDELITY`;
  - current snapshot repairs promotion usable: `false`;
  - blockers:
    `current_snapshot_repairs_not_promotion_usable;selected_scan_attempt_regresses_row_fidelity`;
  - remaining blockers after best attempt:
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.
- Patched `scripts\build_btc1h_research_priority_matrix.py` to read
  `backtest_outputs\btc1h_replay_repair_attempt_audit_latest_codex\btc1h_replay_repair_attempt_summary.csv`
  directly. The priority summary now carries:
  - `promotion_gap_blocked_gate_count = 9`;
  - `replay_repair_active_blockers =
    current_snapshot_repairs_not_promotion_usable;selected_scan_attempt_regresses_row_fidelity`;
  - `replay_repair_strict_selected_scan_attempt_verdict =
    REGRESSES_ROW_FIDELITY`;
  - `replay_repair_current_snapshot_repairs_promotion_usable = false`.
- Patched `scripts\build_btc1h_next_forward_candidate_packet.py` so the next
  forward packet's current-evidence snapshot and promotion spec also surface
  the repair-attempt audit. It now states that replay repair attempts must be
  promotion usable and that the current attempts are not.
- Regenerated:
  - `backtest_outputs\btc1h_promotion_gap_matrix_latest_codex`;
  - `backtest_outputs\btc1h_research_priority_matrix_latest_codex`;
  - `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_repair_attempt_topline_latest_codex --start-at btc1h_replay_root_cause_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `26` selected read-only steps. Readiness returned `1`, the
  expected no-deploy result.
- Validation:
  - `python -m py_compile scripts\build_btc1h_promotion_gap_matrix.py scripts\test_btc1h_promotion_gap_matrix.py scripts\build_btc1h_research_priority_matrix.py scripts\test_btc1h_research_priority_matrix.py scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_promotion_gap_matrix.py scripts\test_btc1h_research_priority_matrix.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-repair-attempt-topline`
    -> `10 passed`.
- Interpretation:
  BTC1H remains observe-only, not deployable, and not near-deployable. The
  current snapshot is useful for replay unit tests, but the top-level
  promotion path now explicitly requires independent replay repairs that are
  row-for-row promotion usable before old rows can count.

## 2026-05-22 - BTC1H objective completion audit

- Added `scripts\build_btc1h_objective_completion_audit.py` and artifact
  `backtest_outputs\btc1h_objective_completion_audit_latest_codex`.
  This is a requirement-level audit for the active objective: BTC1H strategy
  research via faithful backtesting on available data, multiple holdout sets,
  and deployable/near-deployable identification without relaxing official,
  execution, or live-replay gates.
- The audit emits:
  - `btc1h_candidate_objective_status.csv`;
  - `btc1h_objective_requirements.csv`;
  - `btc1h_objective_summary.csv`;
  - `report.md`;
  - `run_info.json`.
- Current objective summary:
  - `objective_complete = false`;
  - current verdict:
    `objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - deployable candidates: `0`;
  - near-deployable candidates: `0`;
  - promising research candidates: `3`;
  - promotion gap blocked gate count: `9`;
  - replay promotion-usable modes: `0 / 6`;
  - replay repair current-snapshot promotion usable: `false`.
- Candidate statuses:
  - `high_conf_80_entry70_no_chase`:
    `promising_research_control_blocked`, with `14 / 14` historical holdouts,
    `6 / 6` WS cadences, `11` forward official rows, official PnL `+$0.50`,
    and replay promotion usable `false`;
  - `high_conf_80_entry59_70_no_chase`:
    `promising_research_runner_up_not_independent`, with `12 / 13`
    historical holdouts, `6 / 6` WS cadences, and no forward official rows;
  - `high_conf_80_no_chase`: `basis_watchlist_not_forward_validated`, with
    `13 / 14` historical holdouts, `5 / 6` WS cadences, and no forward
    official rows;
  - `high_conf_80`: `historical_promising_blocked`, with `8 / 9` historical
    holdouts, `6 / 6` WS cadences, and no forward official rows.
- Requirement statuses:
  - `candidate_universe_and_holdouts = PASS_RESEARCH_EVIDENCE`;
  - `candidate_ranking_and_identification = PASS_IDENTIFIED`;
  - `deployment_or_near_deployment_verdict =
    PASS_NO_DEPLOYABLE_OR_NEAR_DEPLOYABLE_FOUND`;
  - `official_settlement_gate = BLOCKED`;
  - `execution_realism_gate = BLOCKED`;
  - `faithful_live_replay_gate = BLOCKED`;
  - `clean_evidence_clock_gate = BLOCKED`;
  - `statistical_independence_and_basis_caveats = DIAGNOSTIC_ONLY`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_objective_completion_audit` runs after
  `btc1h_promotion_gap_matrix` and before `forward_evidence_report` /
  `gpt_pro_action_status`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_objective_audit_latest_codex --start-at btc1h_promotion_gap_matrix --stop-after gpt_pro_action_status --skip-packet`
  completed all `7` selected read-only steps. The refresh controller now has
  `59` total planned steps, and the new objective audit is step `54`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-objective-audit-rerun`
    -> `8 passed`.
- Interpretation:
  the objective is not complete because faithful deployment-grade BTC1H
  backtesting is still blocked by official settlement, execution-realism,
  live-replay, and clean-clock requirements. The current honest result is
  stronger than "maybe promising": the candidate universe is ranked, but there
  are zero deployable or near-deployable BTC1H candidates under the preserved
  gates.

## 2026-05-22 - BTC1H paused-snapshot execution realism diagnostic

- Added `scripts\build_btc1h_snapshot_execution_realism_audit.py` and artifact
  `backtest_outputs\btc1h_snapshot_execution_realism_latest_codex`.
  This reads the paused remote BTC1H SQLite shadow ledger at
  `runtime\remote_snapshots\snapshot_20260521_145951\btc_1hr_high_conf80_entry70_no_chase_shadow.db`
  and joins the already pulled REST-official settlement rows from
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex\shadow_official_trades.csv`.
- Current diagnostic summary:
  - rows: `11`;
  - official rows: `11`;
  - official PnL: `+$0.50`;
  - official/proxy mismatches: `1`;
  - blank policy rows: `11`;
  - required execution field complete rate: `1.0`;
  - entry matches side ask rate: `1.0`;
  - actual entry matches entry rate: `1.0`;
  - fee present/nonnegative rate: `1.0`;
  - top visible quantity >= contracts rate: `1.0`;
  - quote age <= `250ms` rate: `0.9090909090909091`;
  - stale quote rows: `1`;
  - promotion usable: `false`.
- The stale quote row is:
  - event `KXBTCD-26MAY2106`;
  - market `KXBTCD-26MAY2106-T77699.99`;
  - side `no`;
  - entry `0.68`;
  - top visible quantity `75`;
  - quote age `260.9622ms`.
- Current blockers:
  `quote_age_above_limit;official_proxy_mismatch_present;pre_clean_clock_blank_policy_rows;old_snapshot_not_clean_clock_promotion_evidence`.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the
  execution-realism requirement now includes this paused-snapshot diagnostic.
  The requirement remains `BLOCKED`; this only narrows the reason from "maybe
  missing fields" to "old rows have fields, but one stale quote plus blank
  policy/proxy mismatch/old-clock status prevent promotion."
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_snapshot_execution_realism_audit` runs after
  `btc1h_promotion_gap_matrix` and before
  `btc1h_objective_completion_audit`. The refresh controller now has `60`
  total planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_snapshot_execution_latest_codex --start-at btc1h_promotion_gap_matrix --stop-after gpt_pro_action_status --skip-packet`
  completed all `8` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_snapshot_execution_realism_audit.py scripts\test_btc1h_snapshot_execution_realism_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_snapshot_execution_realism_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-snapshot-execution`
    -> `9 passed`.
- Interpretation:
  this improves the faithful-backtesting evidence package by proving the paused
  BTC1H ledger did capture executable side asks, fees, visible size, and quote
  timestamps. It does not make the current rows near-deployable because the
  rows are pre-clean-clock, have blank policy identity, include one stale quote
  by the `250ms` gate, retain one proxy/official mismatch, and still lack
  independent row-for-row replay parity.

## 2026-05-22 - BTC1H execution-filter impact audit

- Added `scripts\build_btc1h_execution_filter_impact_audit.py` and artifact
  `backtest_outputs\btc1h_execution_filter_impact_latest_codex`.
  This applies the strict execution-realism row flags from
  `backtest_outputs\btc1h_snapshot_execution_realism_latest_codex` to the
  current pulled REST-official BTC1H rows. It is a sensitivity audit only, not
  a new candidate or a gate relaxation.
- Current profile comparison:
  - all paused snapshot rows: `11` rows, official PnL `+$0.50`, proxy PnL
    `+$1.50`, official/proxy mismatches `1`, mismatch rate `9.09%`;
  - strict execution-filtered rows: `10` rows, official PnL `+$0.20`, proxy
    PnL `+$1.20`, official/proxy mismatches `1`, mismatch rate `10.00%`.
- Removed row:
  - `KXBTCD-26MAY2106` / `KXBTCD-26MAY2106-T77699.99`;
  - side `no`;
  - entry `0.68`;
  - quote age `260.9622ms`;
  - official result `no`;
  - proxy result `no`;
  - official PnL `+$0.30`;
  - removal reason `quote_age_above_limit`.
- The strict filtered slice remains blocked by:
  `too_few_execution_filtered_official_rows;official_proxy_mismatch_remaining;old_snapshot_not_clean_clock_promotion_evidence;replay_parity_still_required`.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the
  execution-realism requirement now carries both the old-snapshot execution
  field diagnostic and the strict execution-filter official-result impact.
  Current objective summary includes:
  - `execution_filter_strict_official_rows = 10`;
  - `execution_filter_strict_official_pnl = 0.2`;
  - `execution_filter_strict_official_proxy_mismatches = 1`;
  - `execution_filter_strict_removed_markets =
    KXBTCD-26MAY2106-T77699.99`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_execution_filter_impact_audit` runs after
  `btc1h_snapshot_execution_realism_audit` and before
  `btc1h_objective_completion_audit`. At this point the refresh controller had
  `61` total planned steps; the basis-mismatch audit below extends it to `62`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_execution_filter_latest_codex --start-at btc1h_promotion_gap_matrix --stop-after gpt_pro_action_status --skip-packet`
  completed all `9` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_execution_filter_impact_audit.py scripts\test_btc1h_execution_filter_impact_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_execution_filter_impact_audit.py scripts\test_btc1h_snapshot_execution_realism_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-execution-filter`
    -> `10 passed`.
- Interpretation:
  strict execution filtering makes the already-small positive BTC1H official
  sample weaker (`+$0.50` -> `+$0.20`) and does not remove the proxy/official
  mismatch. This strengthens the no-near-deployable conclusion under preserved
  execution, official-settlement, clean-clock, and replay-fidelity gates.

## 2026-05-22 - BTC1H execution-filtered basis mismatch audit

- Added `scripts\build_btc1h_execution_filtered_basis_mismatch_audit.py` and
  artifact
  `backtest_outputs\btc1h_execution_filtered_basis_mismatch_latest_codex`.
  This narrows the strict execution-filtered BTC1H slice to any surviving
  official/proxy settlement mismatches and computes the strike-distance /
  basis math. It is diagnostic only and does not fit a guard.
- Current summary:
  - strict execution-filtered official rows: `10`;
  - strict official PnL: `+$0.20`;
  - strict proxy PnL: `+$1.20`;
  - strict official/proxy mismatches: `1`;
  - mismatch removed by execution filter: `false`;
  - basis guard deployable now: `false`;
  - near-deployable candidate: `false`.
- Surviving mismatch row:
  - event `KXBTCD-26MAY2008`;
  - market `KXBTCD-26MAY2008-T77299.99`;
  - side `no`;
  - entry `0.68`;
  - official/proxy result `yes/no`;
  - official PnL `-$0.70`;
  - proxy PnL `+$0.30`;
  - official-minus-proxy spot `+$42.15`;
  - proxy close minus strike `-$8.37`;
  - official expiration minus strike `+$33.78`;
  - proxy side margin `+$8.37`;
  - official side margin `-$33.78`;
  - quote age `72.4969ms`;
  - top visible quantity `881`.
- Current blockers:
  `single_mismatch_after_execution_filter;too_few_strict_official_rows;old_snapshot_not_clean_clock_promotion_evidence;guard_not_fit_from_current_rows;replay_parity_still_required`.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the
  official-settlement requirement and objective summary now surface the
  strict-filtered basis mismatch. Current objective fields include:
  - `execution_filtered_basis_mismatch_rows = 1`;
  - `execution_filtered_basis_mismatch_market =
    KXBTCD-26MAY2008-T77299.99`;
  - `execution_filtered_basis_mismatch_removed_by_execution_filter = False`;
  - `execution_filtered_basis_mismatch_official_minus_proxy_spot = 42.15`;
  - `execution_filtered_basis_mismatch_proxy_close_minus_strike = -8.37`;
  - `execution_filtered_basis_mismatch_official_expiration_minus_strike =
    33.78`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_execution_filtered_basis_mismatch_audit` runs after
  `btc1h_execution_filter_impact_audit` and before
  `btc1h_objective_completion_audit`. The refresh controller now has `62`
  total planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_exec_filter_basis_latest_codex --start-at btc1h_promotion_gap_matrix --stop-after gpt_pro_action_status --skip-packet`
  completed all `10` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_execution_filtered_basis_mismatch_audit.py scripts\test_btc1h_execution_filtered_basis_mismatch_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_execution_filtered_basis_mismatch_audit.py scripts\test_btc1h_execution_filter_impact_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-exec-filter-basis`
    -> `11 passed`.
- Interpretation:
  the strict execution filter did not remove the only BTC1H official/proxy
  mismatch; it isolated it as a near-strike basis flip. That makes the blocker
  more concrete, not weaker: any distance/basis guard must be preregistered and
  tested on future clean-clock official rows, not fitted to this old `10`-row
  filtered slice.

## 2026-05-22 - BTC1H remaining evidence manifest

- Added `scripts\build_btc1h_remaining_evidence_manifest.py` and artifact
  `backtest_outputs\btc1h_remaining_evidence_manifest_latest_codex`.
  This consumes the objective-completion audit, promotion gap matrix,
  clean-clock gate, execution realism, strict execution-filter impact,
  strict-filtered basis mismatch, and replay repair/root-cause summaries. It
  turns the remaining BTC1H blockers into explicit missing-evidence rows
  without starting, stopping, restarting, migrating, deploying, or tuning.
- Current summary:
  - manifest status: `BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - objective complete: `false`;
  - deployable now: `false`;
  - near-deployable now: `false`;
  - requirements total: `7`;
  - requirements current artifacts can satisfy: `0`;
  - requirements current artifacts cannot satisfy: `7`;
  - requires explicit authorization count: `6`;
  - requires process control count: `6`;
  - preregistration required count: `6`;
  - critical missing evidence count: `5`;
  - current artifacts can make near-deployable: `false`;
  - process control authorized: `false`;
  - no process action taken: `true`.
- Critical missing evidence IDs:
  - `clean_evidence_clock_start`;
  - `official_settled_clean_sample`;
  - `execution_realism_clean_rows`;
  - `faithful_row_for_row_replay_parity`;
  - `basis_mismatch_prospective_watch`.
- The manifest rows make the current blocker contract machine-readable:
  - old blank-policy rows cannot become clean-clock promotion evidence;
  - stale official rows and the `10`-row strict execution-filtered slice cannot
    satisfy the official-settlement sample gate;
  - old-snapshot execution realism can define future row requirements but
    cannot promote the candidate;
  - current replay repairs remain diagnostics because `0 / 6` replay modes are
    promotion usable;
  - the strict-filtered basis mismatch is only a prospective watch condition,
    not a fitted trading guard.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_remaining_evidence_manifest` runs after
  `btc1h_objective_completion_audit` and before `forward_evidence_report`. The
  refresh controller now has `63` total planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_remaining_evidence_latest_codex --start-at btc1h_promotion_gap_matrix --stop-after gpt_pro_action_status --skip-packet`
  completed all `11` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-remaining-evidence`
    -> `9 passed`.
- Interpretation:
  this does not make BTC1H closer to deployment by weakening gates. It makes
  the opposite explicit: every promotion-relevant remaining requirement still
  needs clean future evidence, and six of the seven manifest rows require
  explicit authorization/process control before that evidence can even start
  being collected.

## 2026-05-22 - BTC1H faithful replay data contract

- Added `scripts\build_btc1h_faithful_replay_data_contract.py` and artifact
  `backtest_outputs\btc1h_faithful_replay_data_contract_latest_codex`.
  This consolidates the exact sidecar fields needed for future row-for-row
  BTC1H replay parity. Inputs are the current BTC1H remote status/schema JSON,
  clean-clock gate, replay-vs-ledger reconciliation, repair target matrix,
  repair prerequisite audit, and repair attempt audit. It is a read-only
  contract, not a process action or a deployment gate relaxation.
- Current data-contract summary:
  - contract status: `BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
  - required field count: `56`;
  - present required field count: `39`;
  - missing required field count: `17`;
  - required clean-clock missing field count: `17`;
  - required tables: `order_decision;signal_scan;ws_lifecycle;ws_orderbook_top`;
  - missing required tables: `order_decision;signal_scan`;
  - sidecar signal-scan rows: `134648`;
  - sidecar order-decision rows: `1`;
  - sidecar top-of-book rows: `458458`;
  - sidecar lifecycle rows: `68782`;
  - clean evidence clock ready: `false`;
  - expected-policy official rows: `0`;
  - blank-policy official rows: `11`;
  - current field contract ready: `false`;
  - clean policy identity ready: `false`;
  - current artifacts can support faithful replay: `false`;
  - current replay promotion usable: `false`;
  - deployable now: `false`;
  - near-deployable now: `false`;
  - requires explicit authorization: `true`;
  - requires process control: `true`;
  - no process action taken: `true`.
- Missing required sidecar fields:
  - `signal_scan.signal_strategy`;
  - `signal_scan.model_ttl_policy`;
  - `signal_scan.model_policy_version`;
  - `signal_scan.edge_threshold_cents`;
  - `signal_scan.spread_cents`;
  - `signal_scan.top_visible_qty`;
  - `signal_scan.quote_received_at_ns`;
  - `signal_scan.quote_age_ms`;
  - `signal_scan.ttl_min`;
  - `signal_scan.close_time`;
  - `signal_scan.btc_candle_time`;
  - `signal_scan.btc_candle_age_sec`;
  - `signal_scan.btc_rv60`;
  - `signal_scan.btc_ret_10m_usd`;
  - `order_decision.signal_strategy`;
  - `order_decision.model_ttl_policy`;
  - `order_decision.model_policy_version`.
- The contract also carries the current replay blockers:
  `missing_required_sidecar_fields;clean_policy_identity_not_ready;future_exact_model_input_capture_required;current_replay_not_promotion_usable;current_snapshot_repairs_not_promotion_usable`.
  The prerequisite audit still says future exact-input capture is required for
  `same_event_market_selection;skip_then_fill_sequence`.
- Updated `scripts\build_btc1h_remaining_evidence_manifest.py` so the
  faithful-replay missing-evidence row now includes this data-contract status,
  missing field count, and blockers. The refreshed manifest now records:
  - `faithful_replay_data_contract_status =
    BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
  - `faithful_replay_missing_required_field_count = 17`;
  - `faithful_replay_current_artifacts_can_support = False`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_faithful_replay_data_contract` runs after
  `btc1h_replay_repair_attempt_audit` and before
  `btc1h_official_basis_mismatch_audit`. The refresh controller now has `64`
  total planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_replay_data_contract_latest_codex --start-at btc1h_replay_repair_attempt_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `26` selected read-only steps. `deployment_readiness` returned
  code `1`, accepted by the controller because it correctly means no
  production-ready candidates.
- Validation:
  - `python -m py_compile scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-replay-data-contract`
    -> `11 passed`.
- Interpretation:
  the blocker is now field-level rather than vague. Current BTC1H sidecar data
  has plenty of rows, but it is missing the scan-time policy identity, TTL,
  quote-age, top-visible-quantity, BTC feature, and order-decision policy
  fields needed to make an independent replay promotion-usable. Existing rows
  remain research/control diagnostics only.

## 2026-05-22 - BTC1H replay source-contract readiness

- Patched `scripts\materialize_btc_replay_sidecar.py` so materialized replay
  DuckDBs preserve:
  - `ws_lifecycle.open_ts`;
  - `ws_lifecycle.close_ts`;
  - `order_decision.side`.
  The source writer already had these fields in `CAPTURE_SCHEMAS` /
  replay-sidecar rows, but the materializer had not been preserving all of
  them.
- Added `scripts\build_btc1h_replay_source_contract_readiness.py` and artifact
  `backtest_outputs\btc1h_replay_source_contract_readiness_latest_codex`.
  This statically checks the current BTC1H source against the `56`-field
  faithful-replay contract:
  - `CAPTURE_SCHEMAS` contains every contract field;
  - `_record_scan` / `_record_decision` emit every required
    `signal_scan` / `order_decision` payload field;
  - `materialize_btc_replay_sidecar.py` can parse every raw sidecar field;
  - the materialized table definitions preserve every contract field.
- Current source-contract summary:
  - source contract status:
    `SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`;
  - required field count: `56`;
  - source-ready field count: `56`;
  - source missing field count: `0`;
  - capture schema missing count: `0`;
  - recorder payload missing count: `0`;
  - materializer sidecar column missing count: `0`;
  - materializer table column missing count: `0`;
  - current source contract ready: `true`;
  - current data contract status:
    `BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
  - current artifacts can support faithful replay: `false`;
  - current rows remain blocked until clean clock: `true`;
  - deployable now: `false`;
  - near-deployable now: `false`;
  - requires explicit authorization to collect: `true`;
  - requires process control to collect: `true`;
  - no process action taken: `true`.
- Updated `scripts\build_btc1h_remaining_evidence_manifest.py` so the
  faithful-replay missing-evidence row now distinguishes source readiness from
  current-row evidence. The refreshed manifest records:
  - `replay_source_contract_status =
    SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`;
  - `replay_source_contract_ready = True`;
  - `replay_source_missing_field_count = 0`;
  - `faithful_replay_current_artifacts_can_support = False`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_replay_source_contract_readiness` runs after
  `btc1h_faithful_replay_data_contract` and before
  `btc1h_official_basis_mismatch_audit`. The refresh controller now has `65`
  total planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_source_contract_latest_codex --start-at btc1h_faithful_replay_data_contract --stop-after gpt_pro_action_status --skip-packet`
  completed all `26` selected read-only steps. `deployment_readiness` returned
  code `1`, accepted by the controller because it still means no
  production-ready candidates.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_source_contract_readiness.py scripts\test_btc1h_replay_source_contract_readiness.py scripts\materialize_btc_replay_sidecar.py scripts\test_btc_replay_sidecar_materialization.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_source_contract_readiness.py scripts\test_btc_replay_sidecar_materialization.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-source-contract`
    -> `13 passed`.
- Interpretation:
  the current checked-out BTC1H code path is now source-ready to emit and
  materialize every field in the faithful-replay contract, but that is not
  evidence for the old running rows. The official BTC1H status remains blocked:
  current artifacts cannot support faithful replay, and future rows only become
  eligible after explicit authorization starts a clean evidence clock and the
  refreshed sidecar schema proves these fields are populated.

## 2026-05-22 - BTC1H clean-clock collection preflight

- Added `scripts\build_btc1h_clean_clock_collection_preflight.py` and artifact
  `backtest_outputs\btc1h_clean_clock_collection_preflight_latest_codex`.
  This is a read-only handoff/preflight that separates:
  - source/materializer preparation readiness;
  - current-row replay/clean-clock blockers;
  - explicit user authorization requirements;
  - the missing post-restart official sample.
- Current preflight summary:
  - `preflight_status = READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
  - `ready_for_authorization = True`;
  - `collection_evidence_ready = False`;
  - `source_contract_ready = True`;
  - `current_artifacts_can_support_faithful_replay = False`;
  - `clean_evidence_clock_ready = False`;
  - `restart_authorization_status = NEEDS_REVIEW_BEFORE_START_RESTART`;
  - `user_permission_required = True`;
  - `restart_path_status = PASS_RESTART_PATH_READY`;
  - `fresh_capture_replay_schema_status = PASS_REPLAY_SIDECAR_SCHEMA`;
  - `post_restart_gate_status = PENDING_CONTROLLED_RESTART`;
  - `post_restart_official_rows = 0`;
  - `min_post_restart_official_rows = 50`;
  - `promotion_collection_ready = False`;
  - `deployable_now = False`;
  - `near_deployable_now = False`;
  - `process_control_authorized = False`;
  - `no_process_action_taken = True`.
- Checklist verdicts:
  - `source_contract_ready`: `PASS_SOURCE_READY`;
  - `current_rows_faithful_replay_contract`:
    `BLOCKED_CURRENT_ROWS_NOT_FAITHFUL`;
  - `clean_evidence_clock_state`: `BLOCKED_CLEAN_CLOCK_NOT_READY`;
  - `restart_path_preflight`: `PASS_PREP_READY`;
  - `explicit_authorization`: `BLOCKED_AUTHORIZATION_REQUIRED`;
  - `post_restart_official_sample`: `BLOCKED_NO_POST_RESTART_ROWS`;
  - `deployability_verdict`: `BLOCKED_NO_DEPLOY`.
- Updated `scripts\build_btc1h_remaining_evidence_manifest.py` so the manifest
  consumes the preflight and now records:
  - `clean_clock_collection_preflight_status =
    READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
  - `clean_clock_collection_preflight_ready_for_authorization = True`;
  - `clean_clock_collection_preflight_collection_evidence_ready = False`;
  - `clean_clock_collection_preflight_process_control_authorized = False`.
- Patched `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_clean_clock_collection_preflight` runs after
  `btc1h_replay_source_contract_readiness` and before
  `btc1h_official_basis_mismatch_audit`. The refresh controller now has `66`
  total planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_clean_preflight_latest_codex --start-at btc1h_faithful_replay_data_contract --stop-after gpt_pro_action_status --skip-packet`
  completed all `27` selected read-only steps. `deployment_readiness` returned
  code `1`, accepted by the controller because it still means no
  production-ready candidates.
- Validation:
  - `python -m py_compile scripts\build_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_clean_clock_collection_preflight.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-clean-preflight`
    -> `11 passed`.
- Interpretation:
  BTC1H is prepared enough to ask for explicit guarded paper-shadow
  authorization, but it has zero clean collection evidence today. No process
  control was taken, and the candidate remains neither deployable nor
  near-deployable.

## 2026-05-22 - BTC1H forward packet aligned with clean-clock preflight

- Patched `scripts\build_btc1h_next_forward_candidate_packet.py` so the
  next-forward packet consumes
  `backtest_outputs\btc1h_clean_clock_collection_preflight_latest_codex\btc1h_clean_clock_collection_preflight_summary.csv`.
  The packet now separates:
  - restart authorization packet global readiness;
  - BTC1H-specific readiness for authorization review;
  - promotion/collection evidence readiness;
  - whether process control has actually been authorized.
- Refreshed artifact:
  `backtest_outputs\btc1h_next_forward_candidate_packet_latest_codex`.
  Current `run_info.json` now records:
  - `packet_status =
    READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
  - `clean_clock_collection_preflight_status =
    READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
  - `clean_clock_collection_ready_for_authorization = true`;
  - `clean_clock_collection_evidence_ready = false`;
  - `clean_clock_collection_process_control_authorized = false`;
  - `restart_authorization_ready = false`;
  - `deployable_now = false`;
  - `replay_promotion_usable_modes = 0 / 6`.
- The refreshed `btc1h_current_evidence_snapshot.csv` carries the same
  preflight fields next to the existing objective facts:
  - all fixed holdouts positive: `14 / 14`;
  - live-WS cadences positive: `6 / 6`;
  - stale forward official rows: `11`;
  - stale official PnL: `+$0.50`;
  - forward proxy/official mismatch rate: `0.0909`;
  - replay repair attempt: `REGRESSES_ROW_FIDELITY`;
  - clean-clock status: `BLOCKED_CONTROLLED_RESTART_REQUIRED`.
- Downstream objective audit now reports:
  - `packet_status =
    READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `critical_blocked_requirements =
    official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_packet_preflight_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
  completed all `13` selected read-only steps. `deployment_readiness` returned
  code `1`, accepted by the controller because no strategy is production-ready.
- Validation:
  - `python -m py_compile scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-packet-preflight`
    -> `8 passed`.
- Interpretation:
  the active BTC1H control remains the only frozen next-clean-clock candidate,
  but the correct current state is authorization-review readiness, not
  collection evidence or near-deployability. No process control was taken.

## 2026-05-22 - BTC1H holdout provenance audit

- Patched `scripts\build_btc1h_multi_holdout_research.py` so the multi-holdout
  report now writes:
  - `btc1h_holdout_provenance.csv`;
  - `btc1h_holdout_provenance_summary.csv`.
  These files classify every fixed holdout row as one of:
  - `historical_predexon_research`;
  - `live_ws_replay_research`;
  - `official_forward_shadow`.
- Current provenance summary:
  - `high_conf_80_entry70_no_chase`:
    - holdout rows: `16`;
    - research-countable rows: `16`;
    - live-WS stability rows: `6`;
    - official-forward diagnostic rows: `1`;
    - official-forward trades: `11`;
    - official/proxy mismatch rows: `1`;
    - near-deployable countable rows: `0`;
    - deployable countable rows: `0`;
    - status: `RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY`.
  - `high_conf_80_entry59_70_no_chase`:
    - holdout rows: `15`;
    - live-WS stability rows: `6`;
    - official-forward diagnostic rows: `0`;
    - near/deployable countable rows: `0 / 0`;
    - status: `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.
  - `high_conf_80`:
    - holdout rows: `11`;
    - live-WS stability rows: `6`;
    - official-forward diagnostic rows: `0`;
    - negative research holdouts:
      `D0_predexon_mar17_24_old_context;H3_predexon_may03_06_external`;
    - status: `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.
  - `high_conf_80_no_chase`:
    - holdout rows: `18`;
    - live-WS stability rows: `6`;
    - official-forward diagnostic rows: `0`;
    - negative research holdout: `H4_live_ws_may06_12_stride1s`;
    - status: `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.
- The active-control official row is explicitly labeled
  `official_forward_diagnostic_only`, not promotion evidence, with blockers:
  `too_few_official_rows_for_promotion;official_proxy_mismatch_rate_above_limit;official_proxy_mismatches_present;too_few_forward_official_rows;official_proxy_mismatch_gate_failed;needs_full_counterfactual_replay_or_live_execution_gate;counterfactual_replay_not_row_faithful;selected_signal_exact_cached_ttl_missing_for_promotion;forward_rows_include_cached_ttl_only_fills;candidate_not_deployable_or_near_deployable`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_holdout_provenance_latest_codex --start-at btc1h_multi_holdout_research --stop-after gpt_pro_action_status --skip-packet`
  completed all `36` selected read-only steps. `btc1h_clean_evidence_clock_gate`
  returned code `1` and `deployment_readiness` returned code `1`, both accepted
  because they represent expected blocked gates/no production-ready candidates.
- Validation:
  - `python -m py_compile scripts\build_btc1h_multi_holdout_research.py scripts\test_btc1h_multi_holdout_research.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_multi_holdout_research.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-holdout-provenance`
    -> `12 passed`.
- Interpretation:
  the research-positive BTC1H story is now explicitly separated from
  promotion-countable evidence. The active control remains promising research
  with official diagnostics, while every current holdout bucket still has `0`
  deployable and `0` near-deployable countable rows.

## 2026-05-22 - BTC1H official PnL path audit

- Added `scripts\build_btc1h_official_pnl_path_audit.py` and test
  `scripts\test_btc1h_official_pnl_path_audit.py`.
- New artifact:
  `backtest_outputs\btc1h_official_pnl_path_latest_codex`.
  It writes:
  - `btc1h_official_pnl_path_sequence.csv`;
  - `btc1h_official_pnl_path_details.csv`;
  - `btc1h_official_pnl_path_summary.csv`;
  - `report.md`;
  - `run_info.json`.
- The audit sequences the current REST-official BTC1H paper rows from
  `backtest_outputs\remote_btc_shadow_official_settlement_latest_codex\shadow_official_trades.csv`.
  Current official path facts:
  - official rows: `11`;
  - official PnL after fees: `+$0.50`;
  - proxy PnL over the same rows: `+$1.50`;
  - official-minus-proxy PnL: `-$1.00`;
  - official win rate: `0.727273`;
  - proxy/official mismatches: `1`;
  - proxy/official mismatch rate: `0.090909`;
  - max drawdown: `-$1.76`;
  - drawdown/PnL ratio: `3.52`;
  - max drawdown market: `KXBTCD-26MAY2009-T77499.99`;
  - max single loss: `-$0.71`;
  - rows with quote age `>90ms`: `3`;
  - max quote age: `260.9622ms`.
- Path status:
  `DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE`.
  `current_rows_count_for_promotion = false` because the sample is below `50`
  clean official rows, has a `9.09%` proxy/official mismatch rate, predates a
  clean evidence clock, and still lacks replay-parity/execution-realism gates.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the objective
  audit consumes the path summary. Current objective summary now includes:
  - `official_pnl_path_audit_status =
    DIAGNOSTIC_PRE_CLEAN_CLOCK_OFFICIAL_PATH_NOT_PROMOTION_USABLE`;
  - `official_pnl_path_rows = 11`;
  - `official_pnl_path_official_pnl = 0.5`;
  - `official_pnl_path_max_drawdown = -1.76`;
  - `official_pnl_path_drawdown_to_pnl_ratio = 3.52`;
  - `official_pnl_path_proxy_official_mismatches = 1`;
  - `official_pnl_path_current_rows_count_for_promotion = false`.
- Updated `scripts\refresh_btc_evidence_stack.py` so
  `btc1h_official_pnl_path_audit` runs after
  `btc1h_execution_filtered_basis_mismatch_audit` and before
  `btc1h_objective_completion_audit`. The refresh controller now has `67`
  planned steps.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_pnl_path_latest_codex --start-at btc1h_execution_filtered_basis_mismatch_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `9 / 9` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_official_pnl_path_audit.py scripts\test_btc1h_official_pnl_path_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_official_pnl_path_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-pnl-path`
    -> `9 passed`.
- Interpretation:
  the current BTC1H official path is now visible row-by-row instead of being
  hidden behind a positive summary PnL. The path is still diagnostic only and
  strengthens the no-deploy verdict: `0` deployable and `0` near-deployable
  BTC1H candidates, with official settlement, execution realism, faithful
  replay, and clean evidence clock gates still blocked. No process control was
  taken.

## 2026-05-22 - BTC1H strict execution-filtered official path

- Updated `scripts\build_btc1h_official_pnl_path_audit.py` so the official PnL
  path audit also consumes
  `backtest_outputs\btc1h_execution_filtered_basis_mismatch_latest_codex\btc1h_execution_filtered_basis_strict_rows.csv`.
- New strict-path artifact:
  `backtest_outputs\btc1h_official_pnl_path_latest_codex\btc1h_official_pnl_path_strict_execution_sequence.csv`.
- Current strict execution-filtered path facts:
  - strict rows: `10`;
  - strict official PnL after fees: `+$0.20`;
  - strict proxy PnL on the same rows: `+$1.20`;
  - strict official-minus-proxy PnL: `-$1.00`;
  - strict max drawdown: `-$1.76`;
  - strict drawdown/PnL ratio: `8.8`;
  - strict proxy/official mismatches: `1`;
  - strict rows with quote age `>90ms`: `2`;
  - row removed by the strict execution filter:
    `KXBTCD-26MAY2106-T77699.99`.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the objective
  summary now carries the strict-path fields:
  - `official_pnl_path_strict_execution_rows = 10`;
  - `official_pnl_path_strict_execution_official_pnl = 0.2`;
  - `official_pnl_path_strict_execution_max_drawdown = -1.76`;
  - `official_pnl_path_strict_execution_proxy_official_mismatches = 1`;
  - `official_pnl_path_strict_execution_current_rows_count_for_promotion =
    false`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_strict_pnl_path_latest_codex --start-at btc1h_execution_filtered_basis_mismatch_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `9 / 9` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_official_pnl_path_audit.py scripts\test_btc1h_official_pnl_path_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
  - `python -m pytest scripts\test_btc1h_official_pnl_path_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-strict-pnl-path`
    -> `9 passed`.
- Interpretation:
  enforcing the strict old-snapshot execution filter weakens, rather than
  rescues, the current BTC1H official path. It removes one positive row and
  leaves the same max drawdown and the same official/proxy mismatch. The active
  BTC1H control remains diagnostic-only, with `0` deployable and `0`
  near-deployable candidates. No process control was taken.

## 2026-05-22 - BTC1H holdout independence now feeds priority blockers

- Updated `scripts\build_btc1h_research_priority_matrix.py` so the priority
  matrix consumes
  `backtest_outputs\btc1h_holdout_independence_audit_latest_codex\btc1h_holdout_independence_summary.csv`.
- Current active-control independence facts:
  - pooled historical/proxy rows: `159`;
  - unique market-side rows: `69`;
  - duplicate market-side rows: `90`;
  - pooled historical/proxy stressed PnL: `+$21.39`;
  - unique market-side stressed PnL: `+$5.82`;
  - historical leave-one-event minimum PnL: `+$19.41`;
  - forward official stale leave-one-event minimum PnL: `+$0.09`;
  - independence status: `WEAK_OR_CONCENTRATED_RESEARCH`;
  - independence blocker: `duplicate_market_side_rows`.
- The active BTC1H priority row now carries:
  - `holdout_independence_status = WEAK_OR_CONCENTRATED_RESEARCH`;
  - `historical_unique_market_side_rows = 69`;
  - `historical_duplicate_market_side_rows = 90`;
  - `historical_unique_market_side_pnl = 5.82`;
  - deployment blockers
    `active_historical_holdouts_have_duplicate_market_side_rows` and
    `active_historical_independence_weak_or_concentrated`.
- The objective candidate status now propagates those blockers into
  `backtest_outputs\btc1h_objective_completion_audit_latest_codex\btc1h_candidate_objective_status.csv`
  for `high_conf_80_entry70_no_chase`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_independence_priority_latest_codex --start-at btc1h_holdout_independence_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `20 / 20` selected read-only steps. `deployment_readiness`
  returned code `1`, accepted because no strategy is production-ready.
- Validation:
  - `python -m py_compile scripts\build_btc1h_research_priority_matrix.py scripts\test_btc1h_research_priority_matrix.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_research_priority_matrix.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-independence-priority`
    -> `8 passed`.
- Interpretation:
  the active BTC1H control remains the best frozen forward control, but its
  historical support is now explicitly discounted for duplicate market-side
  concentration. This does not make the strategy deployable or
  near-deployable; it strengthens the blocked-research conclusion. No process
  control was taken.

## 2026-05-22 - BTC1H promising-candidate sets split in objective audit

- Updated `scripts\build_btc1h_objective_completion_audit.py` so the objective
  summary distinguishes raw multi-holdout promising variants from stricter
  priority-ranked research candidates.
- Current objective summary now reports:
  - `promising_research_candidates = 3`;
  - `priority_research_candidates = 3`;
  - `multi_holdout_promising_candidates = 3`;
  - `priority_research_candidate_variants =
    high_conf_80_entry70_no_chase;high_conf_80_entry59_70_no_chase;high_conf_80_no_chase`;
  - `multi_holdout_promising_variants =
    high_conf_80_entry70_no_chase;high_conf_80_entry59_70_no_chase;high_conf_80`;
  - `multi_holdout_promising_but_low_priority_variants = high_conf_80`;
  - `priority_watchlist_not_multi_holdout_promising_variants =
    high_conf_80_no_chase`.
- Candidate status now marks:
  - `high_conf_80_entry70_no_chase` as
    `promising_research_control_blocked`;
  - `high_conf_80_entry59_70_no_chase` as
    `promising_research_runner_up_not_independent`;
  - `high_conf_80_no_chase` as
    `basis_watchlist_not_forward_validated`;
  - `high_conf_80` as `low_priority_or_rejected`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_promising_sets_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `7 / 7` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-objective-promising-sets`
    -> `1 passed`.
- Interpretation:
  the headline count remains `3`, but it no longer hides that the two
  three-candidate sets are different. Broad `high_conf_80` is still raw
  multi-holdout-promising but low priority/rejected after stricter ranking,
  while `high_conf_80_no_chase` is a basis watchlist path rather than a
  multi-holdout pass. No BTC1H candidate is deployable or near-deployable.

## 2026-05-22 - BTC1H available-data coverage surfaced in remaining manifest

- Updated `scripts\build_btc1h_remaining_evidence_manifest.py` so the
  remaining-evidence manifest now writes a companion machine-readable coverage
  table:
  `backtest_outputs\btc1h_remaining_evidence_manifest_latest_codex\btc1h_available_data_coverage.csv`.
- The coverage table pulls in:
  - the BTC1H holdout provenance summary;
  - replay coverage audit status;
  - faithful-replay data-contract status;
  - replay source-contract readiness.
- Current coverage summary:
  - known available data classes:
    `historical_proxy_research_only;live_ws_stability_research_only;official_forward_diagnostic_only;current_btc1h_replay_coverage_audit;current_sidecar_and_snapshot_replay_artifacts;checked_out_source_and_materializer_for_future_rows`;
  - known data statuses:
    `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE;RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY;BLOCKED_REPLAY_COVERAGE;BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS;SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`;
  - promotion-countable available data classes: none;
  - known available near-deployable-countable rows: `0`;
  - known available deployable-countable rows: `0`;
  - `known_available_data_can_make_near_deployable = false`;
  - `no_known_available_data_class_can_make_near_deployable = true`.
- The coverage rows make the data-class distinction explicit:
  - historical/proxy rows and live-WS replay rows are research-only;
  - old official-forward BTC1H rows are diagnostic-only;
  - current replay coverage is blocked and cannot replace faithful row-for-row
    replay;
  - current sidecar/snapshot artifacts still miss faithful-replay contract
    fields;
  - checked-out source/materializer readiness is future-collection readiness,
    not current promotion evidence.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_available_data_manifest_latest_codex --start-at btc1h_remaining_evidence_manifest --stop-after gpt_pro_action_status --skip-packet`
  completed all `6 / 6` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py`;
  - `python -m pytest scripts\test_btc1h_remaining_evidence_manifest.py -q --basetemp .pytest-codex-tmp-btc1h-available-data-manifest`
    -> `2 passed`.
- Interpretation:
  this closes a handoff ambiguity around "available data." The current local
  evidence package has useful BTC1H research data, but no known available data
  class can make the active control deployable or near-deployable without new
  clean-clock official rows, execution-realism fields, and independent
  row-for-row replay parity. No process control was taken.

## 2026-05-22 - BTC1H available-data scope added to objective audit

- Updated `scripts\build_btc1h_objective_completion_audit.py` so the objective
  audit now consumes the same upstream available-data evidence directly:
  - `btc1h_holdout_provenance_summary.csv`;
  - `btc1h_replay_coverage_summary.csv`;
  - `btc1h_faithful_replay_data_contract_summary.csv`;
  - `btc1h_replay_source_contract_summary.csv`.
- Added an explicit requirement row:
  `available_data_scope_and_countability`.
  Current status:
  `PASS_SCOPED_NO_PROMOTION_USABLE_AVAILABLE_DATA`.
- Current objective summary now carries:
  - known available data classes:
    `historical_proxy_research_only;live_ws_stability_research_only;official_forward_diagnostic_only;current_btc1h_replay_coverage_audit;current_sidecar_and_snapshot_replay_artifacts;checked_out_source_and_materializer_for_future_rows`;
  - known data statuses:
    `RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE;RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY;BLOCKED_REPLAY_COVERAGE;BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS;SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`;
  - promotion-countable available data classes: none;
  - known available near-deployable-countable rows: `0`;
  - known available deployable-countable rows: `0`;
  - `known_available_data_can_make_near_deployable = false`;
  - `known_available_data_can_make_deployable = false`;
  - `faithful_replay_data_contract_status =
    BLOCKED_MISSING_FAITHFUL_REPLAY_CAPTURE_FIELDS`;
  - `replay_source_contract_status =
    SOURCE_READY_RESTART_REQUIRED_CURRENT_ROWS_BLOCKED`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_objective_available_data_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `7 / 7` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-objective-available-data-final`
    -> `10 passed`.
- Interpretation:
  the top-level objective audit now carries the same available-data conclusion
  as the remaining-evidence manifest. A reader no longer has to inspect a
  separate manifest to see that current BTC1H available data supports research
  diagnostics only and cannot make any candidate deployable or near-deployable
  under the official-settlement, execution-realism, faithful-replay, and
  clean-clock gates. No process control was taken.

## 2026-05-22 - BTC1H candidate-level promotion gate columns

- Updated `scripts\build_btc1h_objective_completion_audit.py` so
  `btc1h_candidate_objective_status.csv` now carries candidate-level
  promotion evidence columns instead of only a long blocker string:
  - `available_data_status`;
  - `available_data_uses`;
  - `promotion_countable_available_data`;
  - `candidate_promotion_evidence_status`;
  - `official_settlement_gate_blocked`;
  - `execution_realism_gate_blocked`;
  - `faithful_replay_gate_blocked`;
  - `clean_evidence_clock_gate_blocked`;
  - `statistical_or_basis_caveat_active`.
- Current candidate-level result:
  - `high_conf_80_entry70_no_chase`:
    `candidate_promotion_evidence_status =
    NO_PROMOTION_COUNTABLE_AVAILABLE_DATA`,
    `available_data_status = RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY`;
  - `high_conf_80_entry59_70_no_chase`:
    `candidate_promotion_evidence_status =
    NO_PROMOTION_COUNTABLE_AVAILABLE_DATA`,
    `available_data_status = RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`;
  - `high_conf_80_no_chase`:
    `candidate_promotion_evidence_status =
    NO_PROMOTION_COUNTABLE_AVAILABLE_DATA`,
    `available_data_status = RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`;
  - `high_conf_80`:
    `candidate_promotion_evidence_status =
    NO_PROMOTION_COUNTABLE_AVAILABLE_DATA`,
    `available_data_status = RESEARCH_ONLY_NO_OFFICIAL_FORWARD_EVIDENCE`.
- For the active control, the candidate row now also exposes the hard gate
  blockers directly:
  - `official_settlement_gate_blocked = true`;
  - `execution_realism_gate_blocked = true`;
  - `faithful_replay_gate_blocked = true`;
  - `clean_evidence_clock_gate_blocked = true`;
  - `statistical_or_basis_caveat_active = true`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_candidate_gate_columns_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `7 / 7` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-candidate-gate-columns-final`
    -> `10 passed`.
- Interpretation:
  the candidate table now makes the no-near-deployable conclusion auditable at
  the row level. Each promising or watchlist BTC1H variant is still useful as
  research evidence, but none has promotion-countable available data, and the
  active control remains blocked by every hard promotion gate. No process
  control was taken.

## 2026-05-22 - BTC1H candidate rows now name failing holdout buckets

- Updated `scripts\build_btc1h_objective_completion_audit.py` so
  `btc1h_candidate_objective_status.csv` now carries the multi-holdout details
  that were previously only visible in `btc1h_candidate_gate_summary.csv`:
  - `promotion_readiness_status`;
  - `negative_holdouts`;
  - `near_deployable_disqualifying_blockers`.
- Current candidate-level holdout facts:
  - `high_conf_80_entry70_no_chase`: `14 / 14` historical holdouts positive,
    `6 / 6` WS cadences positive, no negative holdout name, but still
    `promising_but_blocked_by_official_or_fidelity_gates`;
  - `high_conf_80_entry59_70_no_chase`: `12 / 13` historical holdouts
    positive, negative holdout `H2b_direct_apr23_may01_holdout`, `6 / 6` WS
    cadences positive, and `historical_promising_needs_forward_official_evidence`;
  - `high_conf_80_no_chase`: `13 / 14` historical holdouts positive, negative
    live-WS cadence `H4_live_ws_may06_12_stride1s`, `5 / 6` WS cadences
    positive, and `research_watch_or_reject`;
  - `high_conf_80`: `8 / 9` historical holdouts positive, negative holdout
    `H3_predexon_may03_06_external`, `6 / 6` WS cadences positive, and
    `historical_promising_needs_forward_official_evidence`.
- Bounded refresh:
  `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_holdout_detail_columns_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status --skip-packet`
  completed all `7 / 7` selected read-only steps.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-holdout-detail-final`
    -> `10 passed`.
- Interpretation:
  the objective candidate table now preserves both sides of the multi-holdout
  picture. The active control is the only current candidate with clean
  historical/WS bucket positivity, but it is still blocked by official,
  execution, replay, and clean-clock gates. The runner-up and watchlist
  variants now show exactly which holdout or cadence prevents treating them as
  broad robust passes. No process control was taken.

## 2026-05-22 - BTC1H replay repair feasibility surfaced in objective audit

- Updated `scripts\build_btc1h_objective_completion_audit.py` so the top-level
  objective audit consumes:
  - `btc1h_replay_repair_prerequisite_summary.csv`;
  - `btc1h_replay_repair_target_summary.csv`;
  - `btc1h_replay_repair_attempt_summary.csv`.
- The objective summary now exposes the replay-repair blocker directly:
  - `replay_repair_blocker_status =
    BLOCKED_FUTURE_EXACT_INPUT_REQUIRED`;
  - `replay_repair_target_count = 4`;
  - `replay_repair_existing_snapshot_true_target_count = 2`;
  - `replay_repair_existing_snapshot_partial_target_count = 1`;
  - `replay_repair_existing_snapshot_false_target_count = 1`;
  - `replay_repair_future_exact_input_blocked_targets =
    same_event_market_selection;skip_then_fill_sequence`.
- The `faithful_live_replay_gate` requirement now includes the row-for-row
  replay repair detail:
  - current independent replay exact market-side matches are `9 / 11`;
  - replay row count is `10` versus `11` actual rows;
  - replay-minus-actual PnL drift is `+0.61`;
  - two targets are repairable from the current snapshot:
    `order_decision_reprice_fill_price` and `blocked_dedupe_scan_clock`;
  - `same_event_market_selection` is only partially repairable and
    `skip_then_fill_sequence` is not repairable from the current snapshot;
  - the best current snapshot attempt is
    `matched_drift_decision_fill_price_patch_simulation`, but remaining
    blockers are still
    `missing_actual_rows;replay_row_count_differs;extra_replay_rows;event_level_market_replacements;pnl_not_row_for_row_equal`.
- Current objective verdict remains unchanged:
  - `objective_complete = false`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - critical blockers:
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-replay-repair-objective`
    -> `8 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_replay_repair_objective_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `7 / 7` steps.
- Interpretation:
  the objective audit now makes the faithful-backtesting blocker explicit at
  the same level as deployability. Existing paused-snapshot data can test two
  replay-engine fixes diagnostically, but it cannot make the active BTC1H
  candidate promotion-usable because same-event selection and skip/fill
  sequencing still need exact model-input/TTL capture or future clean-clock
  evidence. No process control was taken.

## 2026-05-22 - BTC1H faithful replay root-cause field gaps

- Extended `scripts\build_btc1h_faithful_replay_data_contract.py` so the
  contract now writes
  `btc1h_faithful_replay_root_cause_field_gaps.csv`, mapping each replay
  failure target to the exact required capture fields that are present or
  missing.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so
  `btc1h_objective_summary.csv` carries the field-gap result:
  - `faithful_replay_root_cause_field_gap_rows = 4`;
  - `faithful_replay_root_cause_future_exact_input_target_count = 2`;
  - `faithful_replay_root_cause_target_missing_required_field_count = 16`;
  - `faithful_replay_root_cause_target_missing_required_fields =
    signal_scan.model_ttl_policy;signal_scan.model_policy_version;signal_scan.ttl_min;signal_scan.close_time;signal_scan.btc_candle_time;signal_scan.btc_candle_age_sec;signal_scan.btc_rv60;signal_scan.btc_ret_10m_usd`;
  - `faithful_replay_root_cause_target_repairs_can_make_promotion_usable_now =
    False`.
- Current target-level field gap result:
  - `skip_then_fill_sequence`: snapshot repair status `False`, `8` missing
    target fields, future exact-input required, promotion-usable now `False`;
  - `same_event_market_selection`: snapshot repair status `Partial`, `8`
    missing target fields, future exact-input required, promotion-usable now
    `False`;
  - `order_decision_reprice_fill_price`: snapshot repair status `True`, `0`
    missing target fields, but promotion-usable now still `False`;
  - `blocked_dedupe_scan_clock`: snapshot repair status `True`, `0` missing
    target fields, but promotion-usable now still `False`.
- Objective/manifest verdict after refresh:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `known_available_data_can_make_near_deployable = False`;
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-field-gap-contract`
    -> `10 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_field_gap_contract_latest_codex --start-at btc1h_faithful_replay_data_contract --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `28 / 28` steps. The
    `deployment_readiness` step returned `1` and was correctly treated as PASS
    by the controller because it means no production-ready candidates.
- Interpretation:
  current paused-snapshot data can still support two diagnostic replay-engine
  repairs, but it cannot rescue the target-selection or skip/fill sequence
  failures because the exact scan-time model-input and TTL fields were not
  captured. This is stricter faithful-backtesting evidence, not a deployment
  shortcut. No process control was taken.

## 2026-05-22 - BTC1H residual repair target ledger

- Extended `scripts\build_btc1h_replay_repair_attempt_audit.py` so it now
  writes `btc1h_replay_repair_residual_targets.csv`, a target-level ledger that
  separates diagnostic current-snapshot patches from unresolved future-input
  replay blockers.
- Current residual target statuses:
  - `skip_then_fill_sequence`:
    `UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED`, current snapshot repair attempted
    `False`, future exact input required `True`, promotion usable `False`;
  - `same_event_market_selection`:
    `UNRESOLVED_FUTURE_EXACT_INPUT_REQUIRED`, current snapshot repair attempted
    `False`, future exact input required `True`, promotion usable `False`;
  - `order_decision_reprice_fill_price`:
    `DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE`, current snapshot repair
    attempted `True`, future exact input required `False`, promotion usable
    `False`;
  - `blocked_dedupe_scan_clock`:
    `DIAGNOSTIC_PATCH_APPLIED_NOT_PROMOTION_USABLE`, current snapshot repair
    attempted `True`, future exact input required `False`, promotion usable
    `False`.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the objective
  summary now carries:
  - `replay_repair_residual_target_count = 4`;
  - `replay_repair_diagnostic_patch_applied_target_count = 2`;
  - `replay_repair_unresolved_future_exact_input_target_count = 2`;
  - `replay_repair_diagnostic_patch_applied_targets =
    order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
  - `replay_repair_unresolved_future_exact_input_targets =
    skip_then_fill_sequence;same_event_market_selection`;
  - `replay_repair_targets_repaired_to_promotion_usable_count = 0`;
  - `replay_repair_all_field_ready_repairs_remain_diagnostic_only = True`.
- Current objective and manifest verdict remain unchanged:
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - `known_available_data_can_make_near_deployable = False`;
  - hard blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_replay_repair_attempt_audit.py scripts\test_btc1h_replay_repair_attempt_audit.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_replay_repair_attempt_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-residual-repair`
    -> `11 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_residual_repair_latest_codex --start-at btc1h_replay_repair_attempt_audit --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `29 / 29` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the two field-ready replay repairs can remove matched entry/PnL drift only as
  diagnostic replay-engineering evidence. They still leave missing actual
  rows, row-count drift, extra replay rows, event-level replacements, and PnL
  drift, and they repair `0` targets to promotion usability. No process control
  was taken.

## 2026-05-22 - BTC1H priority matrix carries residual replay blockers

- Updated `scripts\build_btc1h_research_priority_matrix.py` so
  `btc1h_research_priority_matrix.csv` and
  `btc1h_research_priority_summary.csv` include the active-control residual
  replay repair fields from `btc1h_replay_repair_attempt_summary.csv`.
- Current ranking-layer replay blocker string:
  `current_snapshot_repairs_not_promotion_usable;selected_scan_attempt_regresses_row_fidelity;unresolved_future_exact_input_replay_targets;field_ready_replay_repairs_diagnostic_only;zero_replay_targets_repaired_to_promotion_usable`.
- Current active-control priority row now carries:
  - `replay_repair_residual_target_count = 4`;
  - `replay_repair_diagnostic_patch_applied_target_count = 2`;
  - `replay_repair_unresolved_future_exact_input_target_count = 2`;
  - `replay_repair_diagnostic_patch_applied_targets =
    order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
  - `replay_repair_unresolved_future_exact_input_targets =
    skip_then_fill_sequence;same_event_market_selection`;
  - `replay_repair_targets_repaired_to_promotion_usable_count = 0`;
  - `replay_repair_all_field_ready_repairs_remain_diagnostic_only = True`.
- Current research ranking remains:
  - active forward control: `high_conf_80_entry70_no_chase`;
  - top causal replay runner-up:
    `high_conf_80_entry59_70_no_chase`;
  - top basis-stress variant: `high_conf_80_no_chase`.
- Current objective/manifest verdict remains unchanged:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - `known_available_data_can_make_near_deployable = False`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_research_priority_matrix.py scripts\test_btc1h_research_priority_matrix.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_research_priority_matrix.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-priority-residual`
    -> `9 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_priority_residual_latest_codex --start-at btc1h_research_priority_matrix --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `15 / 15` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the strategy ranking now reflects the stricter residual replay evidence at
  the same layer that names the active control and runner-up candidates. The
  active control remains the best BTC1H forward-control research candidate, but
  the ranking artifact now makes clear that `0` replay targets were repaired to
  promotion usability. No process control was taken.

## 2026-05-22 - BTC1H candidate packet carries residual replay evidence

- Updated `scripts\build_btc1h_next_forward_candidate_packet.py` so the
  next-forward candidate packet carries residual replay repair fields in:
  - `btc1h_current_evidence_snapshot.csv`;
  - `btc1h_candidate_decision_packet.csv`;
  - `run_info.json`;
  - `report.md`.
- Current packet replay fields:
  - `replay_repair_residual_target_count = 4`;
  - `replay_repair_diagnostic_patch_applied_target_count = 2`;
  - `replay_repair_unresolved_future_exact_input_target_count = 2`;
  - `replay_repair_diagnostic_patch_applied_targets =
    order_decision_reprice_fill_price;blocked_dedupe_scan_clock`;
  - `replay_repair_unresolved_future_exact_input_targets =
    skip_then_fill_sequence;same_event_market_selection`;
  - `replay_repair_targets_repaired_to_promotion_usable_count = 0`;
  - `replay_repair_all_field_ready_repairs_remain_diagnostic_only = True`.
- Current packet status remains conservative:
  - `packet_status = READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
  - `deployable_now = False`;
  - `current_rows_count_for_promotion = False`;
  - `collection_evidence_ready = False`;
  - `restart_authorization_ready = False`.
- Current objective/manifest verdict remains unchanged:
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-packet-residual`
    -> `8 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_packet_residual_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `14 / 14` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the forward candidate packet can still freeze the active BTC1H policy for a
  future clean evidence clock, but it now names the strict replay-residual
  blocker directly: current data repaired `0` replay targets to promotion
  usability. No process control was taken.

## 2026-05-22 - BTC1H clean-clock preflight defers to restart authorization

- Updated `scripts\build_btc1h_clean_clock_collection_preflight.py` so the
  BTC1H clean-clock collection preflight no longer claims authorization
  readiness when the guarded restart authorization packet itself is not ready.
- Current refreshed preflight:
  - `preflight_status = BLOCKED_PREPARATION_NOT_READY`;
  - `ready_for_authorization = False`;
  - `collection_evidence_ready = False`;
  - `restart_authorization_status = NEEDS_REVIEW_BEFORE_START_RESTART`;
  - `restart_authorization_packet_ready = False`;
  - `pre_authorization_blockers = target_process_state_expected`;
  - `target_process_count = 0`;
  - `target_process_running = False`;
  - `target_process_hygiene_status = NOT_RUNNING`.
- Downstream status now reflects the stricter current process-state blocker:
  - remaining-evidence manifest:
    `clean_clock_collection_preflight_status =
    BLOCKED_PREPARATION_NOT_READY`;
  - next-forward candidate packet:
    `clean_clock_collection_ready_for_authorization = False`,
    `shadow_running = False`,
    `restart_authorization_ready = False`;
  - objective audit:
    `packet_status = BLOCKED_RESTART_AUTHORIZATION_NOT_READY`.
- Current objective verdict remains unchanged:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_clean_clock_collection_preflight.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\build_btc1h_objective_completion_audit.py scripts\build_btc1h_next_forward_candidate_packet.py scripts\check_btc_deployment_readiness.py scripts\test_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-clean-preflight-auth-slice`
    -> `14 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_clean_preflight_auth_latest_codex --start-at btc1h_clean_clock_collection_preflight --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `26 / 26` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the source/schema path can still be reviewed, but the current BTC1H target is
  not running and the restart authorization packet reports
  `target_process_state_expected`, so the preflight is now blocked before
  authorization. No process control was taken.

## 2026-05-22 - BTC restart packet separates expected state from observed action

- Updated `scripts\build_btc_restart_authorization_packet.py` so the guarded
  restart/start authorization packet reports current observed process action
  from `process_count`, separate from the configured expected process state.
- Current BTC1H authorization row:
  - `authorization_packet_status = NEEDS_REVIEW_BEFORE_START_RESTART`;
  - `pre_authorization_blockers = target_process_state_expected`;
  - `process_count = 0`;
  - `process_hygiene_status = NOT_RUNNING`;
  - `expected_process_state = existing_process_required`;
  - `observed_process_action = start_absent_target`;
  - `will_stop_existing_processes = False`;
  - `will_start_process = True`;
  - `will_restart_process = False`;
  - `will_start_new_process = True`.
- The BTC1H clean-clock preflight now carries:
  - `preflight_status = BLOCKED_PREPARATION_NOT_READY`;
  - `restart_authorization_packet_ready = False`;
  - `expected_process_state = existing_process_required`;
  - `observed_process_action = start_absent_target`;
  - `will_restart_process = False`;
  - `will_start_new_process = True`.
- Current objective verdict remains unchanged:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `packet_status = BLOCKED_RESTART_AUTHORIZATION_NOT_READY`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc_restart_authorization_packet.py scripts\test_btc_paper_restart_safety.py scripts\build_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_clean_clock_collection_preflight.py`;
  - `python -m pytest scripts\test_btc_paper_restart_safety.py scripts\test_btc1h_clean_clock_collection_preflight.py -q --basetemp .pytest-codex-tmp-btc1h-start-action-semantics`
    -> `12 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_start_action_semantics_latest_codex --start-at restart_authorization_packet --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `56 / 56` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the evidence clock is still blocked and no process action was taken, but the
  packet now states the operational reality precisely: the absent BTC1H target
  would be a guarded start, not a restart of an existing process, and this is
  not authorization-ready under the current expected-process-state check.

## 2026-05-22 - BTC start/restart authorization lane is ready but not evidence

- Updated `scripts\build_btc_restart_authorization_packet.py` so the guarded
  workflow treats absent paper shadows as startable only after explicit user
  authorization, instead of blocking on the absence of an existing process.
- The packet now checks that BTC15M capture is not targeted by the workflow
  rather than requiring a BTC15M capture PID to be running.
- Current target rows:
  - all four paper-shadow targets have
    `authorization_packet_status = READY_FOR_USER_AUTHORIZATION_TO_START`;
  - all four have `process_count = 0`;
  - all four have `expected_process_state = start_or_restart_allowed`;
  - all four have `observed_process_action = start_absent_target`;
  - all four have `will_start_new_process = True`;
  - all four have `will_restart_process = False`.
- Current authorization packet `run_info.json`:
  - `all_ready_for_user_authorization = True`;
  - `all_ready_for_clean_restart_authorization = True`;
  - `ready_for_user_authorization_count = 4`;
  - `latest_restart_plan_execute = False`;
  - `unmanaged_matching_process_count = 0`.
- Current BTC1H clean-clock preflight:
  - `preflight_status = READY_FOR_AUTHORIZATION_NOT_COLLECTION_EVIDENCE`;
  - `ready_for_authorization = True`;
  - `collection_evidence_ready = False`;
  - `restart_authorization_status = READY_FOR_USER_AUTHORIZATION_TO_START`;
  - `restart_authorization_packet_ready = True`;
  - `observed_process_action = start_absent_target`;
  - `will_start_new_process = True`.
- Current objective/manifest verdict remains unchanged:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `packet_status = READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - `current_artifacts_can_make_near_deployable = False`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc_restart_authorization_packet.py scripts\test_btc_paper_restart_safety.py scripts\build_btc1h_clean_clock_collection_preflight.py scripts\test_btc1h_clean_clock_collection_preflight.py`;
  - `python -m pytest scripts\test_btc_paper_restart_safety.py scripts\test_btc1h_clean_clock_collection_preflight.py -q --basetemp .pytest-codex-tmp-btc1h-start-ready-auth-final`
    -> `13 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_start_ready_auth_latest_codex --start-at restart_authorization_packet --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `56 / 56` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  this supersedes the immediately prior start/restart authorization blocker:
  the safe authorization review lane is now ready, but no process control was
  taken and no clean evidence rows exist yet. Explicit user authorization,
  future official-settled rows, execution-realism fields, and row-for-row replay
  parity are still required before BTC1H can become near-deployable.

## 2026-05-22 - BTC1H packet/objective expose start-ready but no-evidence state

- Updated `scripts\build_btc1h_next_forward_candidate_packet.py` so the BTC1H
  next-forward packet carries the clean-clock preflight's corrected process
  state fields in `btc1h_current_evidence_snapshot.csv`, `run_info.json`, and
  `report.md`.
- Updated `scripts\build_btc1h_objective_completion_audit.py` so the objective
  summary and clean-evidence-clock requirement show the same authorization
  state instead of only the high-level packet status.
- Current BTC1H candidate packet state:
  - `packet_status = READY_FOR_AUTHORIZATION_REVIEW_NOT_COLLECTION_EVIDENCE`;
  - `restart_authorization_status =
    READY_FOR_USER_AUTHORIZATION_TO_START`;
  - `restart_authorization_packet_ready = True`;
  - `expected_process_state = start_or_restart_allowed`;
  - `observed_process_action = start_absent_target`;
  - `will_start_new_process = True`;
  - `will_restart_process = False`;
  - `authorization_ready_but_collection_evidence_false = True`;
  - `clean_clock_collection_evidence_ready = False`;
  - `no_process_action_taken = True`.
- Current BTC1H objective summary remains unchanged on deployability:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_next_forward_candidate_packet.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
  - `python -m pytest scripts\test_btc1h_next_forward_candidate_packet.py scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-packet-objective-start-state`
    -> `2 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_packet_objective_start_state_latest_codex --start-at btc1h_next_forward_candidate_packet --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `14 / 14` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the handoff packet and objective audit now agree with the start-ready
  authorization lane: absent BTC1H paper shadow targets could be started only
  after explicit user authorization, but no start/restart occurred and there is
  still no clean collection evidence. This is not near-deployable evidence.

## 2026-05-22 - BTC1H faithful replay missing-field counts clarified

- Updated `scripts\build_btc1h_faithful_replay_data_contract.py` so the
  faithful replay contract distinguishes:
  - overall missing required sidecar fields;
  - summed target-level missing-field occurrences across replay root causes;
  - unique target-level missing fields that block the unresolved exact-input
    replay targets.
- Updated `scripts\build_btc1h_objective_completion_audit.py` and
  `scripts\build_btc1h_remaining_evidence_manifest.py` so downstream summaries
  and reports carry the same counts.
- Current regenerated faithful replay contract:
  - `missing_required_field_count = 17`;
  - `root_cause_target_missing_required_field_count = 16`;
  - `root_cause_target_missing_required_field_occurrence_count = 16`;
  - `root_cause_target_unique_missing_required_field_count = 8`;
  - target missing field ids are
    `signal_scan.model_ttl_policy;signal_scan.model_policy_version;signal_scan.ttl_min;signal_scan.close_time;signal_scan.btc_candle_time;signal_scan.btc_candle_age_sec;signal_scan.btc_rv60;signal_scan.btc_ret_10m_usd`;
  - `current_artifacts_can_support_faithful_replay = False`.
- Current objective/manifest verdict remains unchanged:
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_faithful_replay_data_contract.py scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py`;
  - `python -m pytest scripts\test_btc1h_faithful_replay_data_contract.py scripts\test_btc1h_objective_completion_audit.py scripts\test_btc1h_remaining_evidence_manifest.py -q --basetemp .pytest-codex-tmp-btc1h-faithful-count-clarity`
    -> `5 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_faithful_count_clarity_latest_codex --start-at btc1h_faithful_replay_data_contract --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `28 / 28` steps. The
    `deployment_readiness` return code `1` was correctly treated as PASS by the
    controller because it means no production-ready candidates.
- Interpretation:
  the apparent `17` versus `16` mismatch was not a deployability improvement.
  It is the difference between all missing sidecar contract fields and the
  repeated target-level fields needed for the two unresolved exact-input replay
  failures. Current artifacts still cannot produce faithful row-for-row BTC1H
  replay evidence.

## 2026-05-22 - BTC1H available-data coverage rows now carry blockers

- Updated `scripts\build_btc1h_remaining_evidence_manifest.py` so
  `btc1h_available_data_coverage.csv` writes a `blockers` column in addition
  to the existing `blocker_summary`, and the manifest report includes that
  column in the available-data coverage table.
- Current available-data coverage now says every data class has
  `current_rows_count_for_promotion = False` and
  `can_make_near_deployable_now = False`, with row-level blockers:
  - historical/proxy and live-WS rows are research-only unless clean-clock,
    official-settlement, execution-realism, and replay gates pass;
  - replay coverage audits are plumbing only and do not replace official clean
    rows or row-for-row faithful replay;
  - current sidecar/snapshot replay artifacts are blocked by
    `missing_required_sidecar_fields;clean_policy_identity_not_ready;future_exact_model_input_capture_required;current_replay_not_promotion_usable;current_snapshot_repairs_not_promotion_usable`;
  - source readiness is future collection capability after authorization, not
    current collection evidence.
- Current summary remains unchanged:
  - `manifest_status = BLOCKED_MISSING_CLEAN_FORWARD_EVIDENCE`;
  - `known_available_data_can_make_near_deployable = False`;
  - `known_available_data_deployable_countable_rows = 0`;
  - `current_artifacts_can_make_near_deployable = False`;
  - `no_process_action_taken = True`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_remaining_evidence_manifest.py scripts\test_btc1h_remaining_evidence_manifest.py`;
  - `python -m pytest scripts\test_btc1h_remaining_evidence_manifest.py -q --basetemp .pytest-codex-tmp-btc1h-coverage-blockers`
    -> `2 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_btc1h_available_data_blockers_latest_codex --start-at btc1h_remaining_evidence_manifest --stop-after gpt_pro_action_status --skip-packet`
    -> selected read-only refresh passed `6 / 6` steps.
- Interpretation:
  this does not improve deployability, but it makes the current no-near-
  deployable conclusion easier to audit: every available data source now states
  why it cannot satisfy the original BTC1H objective today.

## 2026-05-22 - BTC1H candidate rows now explain research-only status

- Updated `scripts\build_btc1h_objective_completion_audit.py` so
  `btc1h_candidate_objective_status.csv` now carries candidate-level evidence
  classification:
  - `candidate_research_class`;
  - `candidate_is_research_only`;
  - `current_available_data_class`;
  - `promotion_gate_failures`;
  - `why_not_near_deployable`;
  - `next_evidence_to_reconsider`.
- Current regenerated candidate classes:
  - `high_conf_80_entry70_no_chase` =
    `active_forward_control_research_only`, with only
    `research_plus_official_diagnostic_only` available data;
  - `high_conf_80_entry59_70_no_chase` =
    `causal_replay_runner_up_research_only`, with the runner-up still not
    independent from the active snapshot;
  - `high_conf_80_no_chase` =
    `basis_robust_watchlist_research_only`, with no clean forward official
    validation;
  - `high_conf_80` =
    `multi_holdout_historical_promising_research_only`, but still
    low-priority/rejected despite historical positives.
- Every candidate row still has:
  - `promotion_countable_available_data = False`;
  - `candidate_promotion_evidence_status =
    NO_PROMOTION_COUNTABLE_AVAILABLE_DATA`;
  - hard promotion failures across official settlement, execution realism,
    faithful replay, and clean evidence-clock gates.
- Current summary remains unchanged:
  - `objective_complete = False`;
  - `current_verdict =
    objective_incomplete_no_deployable_or_near_deployable_btc1h_candidate`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `promising_research_candidates = 3`;
  - critical blockers remain
    `official_settlement_gate;execution_realism_gate;faithful_live_replay_gate;clean_evidence_clock_gate`.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-candidate-research-only`
    -> `1 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status`
    -> selected read-only refresh passed `7 / 7` steps.
- Interpretation:
  this is a reporting and guardrail improvement, not a deployment improvement.
  The top BTC1H policies are still research-only until future clean-clock rows
  satisfy official settlement, execution realism, and row-for-row replay gates.

## 2026-05-22 - BTC1H candidate blocker proof now links to source artifacts

- Extended `scripts\build_btc1h_objective_completion_audit.py` again so each
  `btc1h_candidate_objective_status.csv` row now includes:
  - `promotion_blocker_evidence_snapshot`, a compact candidate-level proof with
    available-data status, research rows, official diagnostic rows,
    near/deployable-countable row counts, forward official rows/PnL,
    proxy-mismatch rate, replay promotion usability, replay exact-match rate,
    and hard gate failures;
  - `promotion_blocker_evidence_sources`, the artifact paths that support that
    row's blocker verdict.
- Current active-control proof snapshot:
  - `available_data_status = RESEARCH_PLUS_OFFICIAL_DIAGNOSTIC_ONLY`;
  - `near_deployable_countable_rows = 0`;
  - `deployable_countable_rows = 0`;
  - `forward_official_rows = 11`;
  - `forward_official_pnl = 0.5`;
  - `forward_proxy_mismatch_rate = 0.0909`;
  - `replay_ledger_promotion_usable = False`;
  - `replay_ledger_exact_match_rate = 0.818182`;
  - promotion failures remain official settlement, execution realism, faithful
    replay, clean evidence clock, and statistical/basis caveats.
- Source artifacts now listed per row include the multi-holdout candidate gate
  summary, research-priority matrix, holdout provenance, promotion-gap matrix,
  official PnL path, execution filter/basis audits, snapshot execution audit,
  replay coverage, faithful replay data contract, replay repair target/attempt
  audits, and the next-forward candidate packet run info.
- Current summary remains unchanged:
  - `objective_complete = False`;
  - `deployable_candidates = 0`;
  - `near_deployable_candidates = 0`;
  - `promising_research_candidates = 3`;
  - no current row is promotion-countable or near-deployable-countable.
- Validation:
  - `python -m py_compile scripts\build_btc1h_objective_completion_audit.py scripts\test_btc1h_objective_completion_audit.py`;
  - `python -m pytest scripts\test_btc1h_objective_completion_audit.py -q --basetemp .pytest-codex-tmp-btc1h-source-backed-candidate-blockers`
    -> `1 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status`
    -> selected read-only refresh passed `7 / 7` steps.
- Interpretation:
  this makes the negative BTC1H conclusion easier to audit from a single CSV
  row per candidate. It still does not create deployable or near-deployable
  evidence, and no process/start/restart/trading action was taken.

## 2026-05-22 - BTC1H candidate promotion deficits quantified

- Added `scripts\build_btc1h_candidate_promotion_deficit_audit.py` and
  `scripts\test_btc1h_candidate_promotion_deficit_audit.py`, then wired the
  new audit into `scripts\refresh_btc_evidence_stack.py` immediately after the
  objective completion audit.
- New artifact:
  `backtest_outputs\btc1h_candidate_promotion_deficit_latest_codex\btc1h_candidate_promotion_deficits.csv`.
  It quantifies, per candidate:
  - clean official row deficit versus the `50` row minimum;
  - whether current data is promotion-countable;
  - proxy/official mismatch excess versus the `2%` maximum;
  - execution-field completeness deficit;
  - replay exact-match deficit and promotion-usable replay modes;
  - faithful replay missing field counts;
  - clean policy-identity row deficit;
  - whether current artifacts can make the candidate near-deployable.
- Current regenerated summary:
  - `candidates_current_artifacts_can_make_near_deployable = 0`;
  - `candidates_with_no_promotion_countable_data = 4`;
  - `current_clean_post_restart_official_rows = 0`;
  - `clean_official_row_deficit = 50`;
  - active `high_conf_80_entry70_no_chase`
    `active_proxy_official_mismatch_rate_excess = 0.0709`;
  - active replay exact-match deficit is `0.181818`;
  - active execution-field completeness deficit under the promotion gate is
    `1.0`;
  - faithful replay still has `17` overall missing required fields and `8`
    unique target-level missing fields.
- Current candidate deficit blockers:
  - active `entry70_no_chase` is blocked by no promotion-countable available
    data, too few clean official rows, mismatch rate above limit, incomplete
    execution fields, non-promotion-usable row-for-row replay, missing faithful
    replay capture fields, and missing clean policy identity rows;
  - the runner-up and watchlist variants additionally have no forward official
    rows.
- Validation:
  - `python -m py_compile scripts\build_btc1h_candidate_promotion_deficit_audit.py scripts\test_btc1h_candidate_promotion_deficit_audit.py scripts\refresh_btc_evidence_stack.py scripts\test_btc_evidence_stack_refresh.py`;
  - `python -m pytest scripts\test_btc1h_candidate_promotion_deficit_audit.py scripts\test_btc_evidence_stack_refresh.py -q --basetemp .pytest-codex-tmp-btc1h-promotion-deficit`
    -> `8 passed`;
  - `python scripts\refresh_btc_evidence_stack.py --out-dir backtest_outputs\btc_evidence_stack_refresh_latest_codex --start-at btc1h_objective_completion_audit --stop-after gpt_pro_action_status`
    -> selected read-only refresh passed `8 / 8` steps. The refresh now plans
    `69` total steps and includes `btc1h_candidate_promotion_deficit_audit` as
    step `62`.
- Interpretation:
  this makes the gap to near-deployable explicit and numeric. It does not
  create promotion evidence: all BTC1H candidates remain research-only under
  the original official-settlement, execution-realism, faithful-replay, and
  clean-clock gates.
### 2026-05-30 - Branch/data research checkpoint

- Audited the current laptop server collection state:
  - BTC15M raw capture is live with `dropped = 0`,
    `ws_orderbook_top = 16,755,021`, and replay sidecar counts matching status.
  - BTC15M lowdd shadow is live with `dropped = 0`,
    `ws_orderbook_top = 4,463,363`, `signal_scan = 2,381,496`, and
    `order_decision = 5,716`.
  - BTC1H high-conf capture is stale at `2026-05-28T05:15:56Z` and is not
    current forward evidence.
- Ran two collection-fidelity checks:
  - recent BTC15M raw-vs-lowdd sidecar overlap had `26,900` common
    `(market_ticker, seq, source)` top-book keys with zero Kalshi
    price/quantity mismatches;
  - latest captured `KXBTC15M-26MAY300045-45` top book matched Kalshi REST
    NO bid `0.9990` qty `4920.20`.
- Important limitation:
  current BTC15M capture has `capture_raw_ws = false`; it is faithful
  top-of-book replay evidence, not full raw websocket level/delta capture.
- Built a local ignored BTC1H snapshot dataset from the remote capture:
  `runtime/branch_backtests/btc1h_20260530_gapless.duckdb`.
  It contains `7,029,636` deduped top-book rows, `1,423,192` signal scans,
  and `62` order decisions, but also `45,428` market gaps over `120s`, so it
  is research-only.
- Backtested runnable v2 branch harnesses on that snapshot after adapting only
  isolated runtime worktrees to consume the captured `KRAKEN:XBT/USD` ticker:
  - `origin/arb-v3`: `170` trades, net PnL `-$16.09`, fees `$6.69`,
    positive net trades `0 / 170`;
  - `origin/claude/v2-backtest-harness`: same result on the snapshot;
  - coarse combined v2 sweep completed first five variants before timeout,
    and all were negative after fees (`-$12.35`, `-$10.62`, `-$10.62`,
    `-$12.35`, and `-$0.32`).
- Refreshed the current BTC15M lowdd official paper result from Kalshi REST:
  `12` settled fills, `7 / 5` wins/losses, official PnL `-$3.84`, premium
  `$49.84`, return on premium about `-7.7%`, and no new fills since
  `2026-05-28T19:55:35Z`.
- Interpretation:
  no current branch or live forward candidate is validated. Keep top-of-book
  capture running; treat v2 and lowdd as rejected/negative for now; next loop
  should materialize BTC15M sidecar slices and search preregistered filters on
  fresh official-settled rows without using `btc_spot` as a canonical
  per-message label.

### 2026-05-30 - BTC15M sidecar lowdd research and paper-accounting fix

- Added bounded materialization support to
  `scripts/materialize_btc_replay_sidecar.py`:
  `--start-utc`, `--end-utc`, repeated `--table`, `--event-prefix`,
  `--market-prefix`, raw `coinbase_ticker` preservation when present, manifest
  filter metadata, and active-append-tolerant JSON parsing.
- Materialized the remote raw BTC15M replay sidecar for
  `2026-05-29T16:00:00Z..2026-05-30T06:00:00Z` without pausing collection or
  copying the raw sidecar. Local ignored artifact:
  `runtime/remote_snapshots/sidecar_slices/btc15m_raw_20260529_1600_20260530_0600.duckdb`.
  Counts: `1,453,022` top-book rows, `90,076` lifecycle rows, and
  `1,300,087` synthetic BTC rows from top-book `btc_spot`.
- Frozen F2 causal replay on `2026-05-29T18:00:00Z..2026-05-30T06:00:00Z`:
  - `q250_firstskip_qty500`: `1` trade, REST-official PnL `-$0.54`;
  - `q250_firstskip_qty500_yes`: `0` trades;
  - `q1000_yes`: `0` trades.
  These remain unvalidated/signal-starved.
- Broader live-holdout rule pack on the same official-settled window found
  `current_lowdd_no_rv`: `16` trades, PnL `+$2.49`, return on premium
  `+23.69%`, win rate `81.25%`, max drawdown `-$0.46`. Subwindows:
  `16:00..18:00Z` was `-$0.33` on `4` trades; `18:00..00:00Z` was `+$1.31`
  on `9`; `00:00..06:00Z` was `+$1.18` on `7`. This is promising research
  evidence only; the old May 28 live ledger remained negative, and this slice
  uses synthetic BTC ticks from top-book rows.
- Diagnosed why the live lowdd paper shadow stopped filling:
  the lowdd shadow replay sidecar had `760,173` scans, `2,939` selected
  signals, and `2,939` order decisions in the same window, but every order
  decision was `skip`; `2,907` were `sizing_budget_or_liquidity_zero`.
  The paper portfolio showed `portfolio_available = 50.16` but
  `estimated_cost = 0.00` because old May 28 paper fills were still counted as
  active exposure after local BTC minute history no longer covered their close
  times.
- Patched `scripts/btc15m_lowdd_live.py` so paper shadow accounting settles
  closed paper rows from Kalshi REST official market results before falling
  back to local BTC history. Added regression coverage in
  `scripts/test_btc15m_shadow_config.py`.
- Validation:
  `python -m py_compile scripts\btc15m_lowdd_live.py scripts\test_btc15m_shadow_config.py scripts\materialize_btc_replay_sidecar.py scripts\test_btc_replay_sidecar_materialization.py`
  and
  `python -m pytest scripts\test_btc15m_shadow_config.py scripts\test_btc_replay_sidecar_materialization.py -q --basetemp .pytest-codex-tmp-btc15m-lowdd-paper`
  passed with `15 passed`.
- Deployed the paper-accounting fix to the remote deployment copy and restarted
  only the paper lowdd scheduled task
  `\KalshiBTC_btc15m_lowdd_forward_shadow`. A stale May 28 child PID `13720`
  survived the first task restart and was stopped; clean child PID `12796`
  then matched the status sidecar and resumed row/scanning growth with
  `failed=false`, `dropped=0`.
- Raw BTC15M capture was not restarted or paused. It remained healthy after the
  lowdd restart: PID `14260`, `failed=false`, `dropped=0`,
  `ws_orderbook_top=16,933,760`, latest top-book row
  `2026-05-30T06:35:26.235940Z`.
- Next evidence gate:
  count only post-restart lowdd paper rows as forward evidence for the fix,
  verify sizing no longer skips on the next selected signal, and compare any
  new paper fills to the raw-sidecar replay candidates before considering any
  deployment discussion.

### 2026-05-30 - Remote post-restart lowdd and git hygiene check

- Pulled and pushed branch `sami`; it was already aligned with `origin/sami`
  and `git push` reported `Everything up-to-date`.
- Remote branch scan after fetch showed `origin/sami` at commit `226b7f2`
  from `2026-05-30`; the newest non-`sami` branch head remained
  `origin/claude/v2-backtest-harness` at `4b62b23` from `2026-05-13`.
- Remote raw BTC15M capture remained live and was not paused or restarted.
  Status at `2026-05-30T06:46:16Z`: PID `14260`, `failed=false`,
  `dropped=0`, queue depth `0`, `ws_orderbook_top=16,948,827`,
  `ws_lifecycle=1,283,375`, `coinbase_ticker=201,301`, latest top-book row
  `2026-05-30T06:46:16.691831Z`. `capture_raw_ws=false`, so this is faithful
  top-of-book/lifecycle capture, not a full raw websocket frame archive.
- Remote lowdd paper shadow remained live under scheduled task
  `\KalshiBTC_btc15m_lowdd_forward_shadow`. Status at
  `2026-05-30T06:46:19Z`: PID `12796`, `failed=false`, `dropped=0`,
  queue depth `9`, latest `signal_scan=2026-05-30T06:46:18.773534Z`;
  the latest `order_decision` was still pre-restart at
  `2026-05-30T03:55:56.634518Z`.
- Bounded replay-sidecar audit for
  `received_at_utc >= 2026-05-30T06:33:25Z` found `18,746`
  `ws_orderbook_top` rows, `1,022` lifecycle rows, and `9,487` signal scans.
  Signal actions were `9,441 none` and `46 skip`; there were `0 selected`
  rows, `0 order_decision` rows, and `0` post-restart ledger fills.
- Interpretation:
  the lowdd paper-accounting fix is running and no longer visibly stuck on the
  old `sizing_budget_or_liquidity_zero` blocker, but no qualifying post-restart
  signal has occurred yet. Leave the shadow running; do not retune thresholds
  on this window.

### 2026-05-30 - BTC15M fidelity audit and latest settled sidecar replay

- Refreshed remote status at `2026-05-30T06:50:53Z`. Raw BTC15M capture was
  still live with PID `14260`, `failed=false`, `dropped=0`,
  `ws_orderbook_top=16,957,370`, `ws_lifecycle=1,283,597`, and latest
  top-book row `2026-05-30T06:50:47.726414Z`. Lowdd paper shadow was still
  live with PID `12796`, `failed=false`, `dropped=0`, latest
  `signal_scan=2026-05-30T06:50:50.431545Z`, and still `0` post-restart
  ledger fills.
- Tried the preferred direct-DuckDB holdout replay on
  `2026-05-30T06:00:00Z..06:45:00Z`, but Windows denied copying the active raw
  capture DB (`PermissionError(13)`). Collection was not paused. Fell back to
  bounded replay-sidecar materialization.
- Materialized the raw capture replay sidecar for
  `2026-05-30T06:00:00Z..06:45:00Z` into remote ignored runtime storage:
  `72,566` `ws_orderbook_top` rows, `4,222` lifecycle rows, and `68,671`
  synthetic Coinbase rows from `ws_orderbook_top.btc_spot`. This is
  research-grade for top-book replay only, not promotion-grade BTC tick-age
  evidence.
- Official-settled holdout replay on that latest slice found:
  - `cheap_yes_rr_first`: `1` trade, PnL `-$0.180`;
  - `cheap_no_rr_first`: `2` trades, PnL `-$0.135`;
  - `cheap_tail_best_side_first`: `3` trades, PnL `-$0.315`;
  - `cheap_tail_position_aware`: `3` trades, PnL `-$0.315`;
  - `cheap_pair_lock_rr`: `0` trades;
  - `current_lowdd_no_rv`: `0` trades.
  All `4` events in the replay window had REST-official settlement rows.
- Added `scripts/audit_btc15m_sidecar_fidelity.py` to make sidecar data-quality
  checks reusable. It reports table counts, timestamp/quote invariants, top-book
  gaps, Coinbase source quality, and optional independent-capture bucket parity.
- Materialized the matching lowdd sidecar slice for the same window:
  `62,941` top-book rows, `3,903` lifecycle rows, `27,652` signal scans,
  `0` order decisions, and `59,523` synthetic Coinbase rows.
- Fidelity/parity audit comparing raw vs lowdd captures in `5s` buckets found:
  raw top-book rows had `0` timestamp/null ticker errors, `0` price out-of-range
  rows, `0` negative quantities, and `0` gaps over `120s` (`p99` top-book gap
  about `0.405s`, max gap about `32.43s`). It also found `129` rows with null
  book fields, `254` crossed YES-book rows, and `241` crossed NO-book rows.
  Independent-capture parity matched `448 / 511` raw buckets and `448 / 451`
  lowdd buckets; `93.63%` of matched buckets were within `1c` YES-mid, with
  p95 difference `1c`. Worst mismatches clustered around fast-moving near-close
  markets.
- Patched `scripts/backtest_btc15m_live_holdout.py` so replay drops invalid
  top-book rows before feature construction: null/crossed/out-of-range prices
  and negative visible quantities can no longer be scored as executable
  zero-spread opportunities. Added regression coverage in
  `scripts/test_btc15m_live_holdout.py`.
- Reran the `06:00Z..06:45Z` replay after that filter; the strategy summary was
  unchanged, so the losing cheap-tail rows in this slice were not caused by the
  crossed-book rows. Future positive slices still need this filter for
  execution realism.
- Validation:
  `python -m py_compile scripts\backtest_btc15m_live_holdout.py scripts\audit_btc15m_sidecar_fidelity.py scripts\test_btc15m_live_holdout.py`
  and
  `python -m pytest scripts\test_btc15m_live_holdout.py -q --basetemp .pytest-codex-tmp-btc15m-live-holdout`
  passed with `1 passed`.
- Interpretation:
  the data is usable for filtered top-of-book research replay, but not a full
  raw websocket archive and not promotion-grade BTC tick-age replay while the
  sidecar path relies on synthetic Coinbase ticks. The newest official-settled
  slice is negative for cheap-tail variants and has no lowdd trades.

### 2026-05-30 - Full raw DuckDB snapshot, real Coinbase replay, and first clean lowdd fill

- Briefly paused only the raw BTC15M capture to obtain a full DuckDB snapshot;
  lowdd remained running. Snapshot:
  `runtime\duckdb_snapshots\raw_btc15m_full_20260530_071126\btc15m_live_capture.duckdb`
  plus `.wal`, stored only under remote ignored `runtime/`. The pause/copy
  sequence copied `2,074,095,616` bytes of main DB plus `14,184,717` bytes of
  WAL. Raw capture was restarted under scheduled task
  `\KalshiBTC_btc15m_live_capture` after the direct SSH child launch proved
  non-persistent.
- Verified raw capture recovery at `2026-05-30T07:32:04Z`: PID `6084`,
  launcher PID `3752`, `failed=false`, `dropped=0`, queue depth `0`,
  `ws_orderbook_top=17,007,662`, `ws_lifecycle=1,287,103`,
  `coinbase_ticker=201,804`, latest top-book row
  `2026-05-30T07:32:02.482783Z`.
- Patched `scripts/backtest_btc15m_live_holdout.py` so snapshot copying also
  copies a sibling `.duckdb.wal` when present. This prevents stopped/full
  snapshots from silently losing the most recent uncheckpointed rows.
- Added bounded-window support to `scripts/audit_btc15m_sidecar_fidelity.py`
  via `--start-utc` / `--end-utc` and separated raw-clean verdicts from
  filtered-top-book replay verdicts.
- Full raw DuckDB fidelity audit on
  `2026-05-30T06:00:00Z..07:00:00Z`, compared against the independent lowdd
  capture in `5s` buckets:
  - `92,718` raw top-book rows, `4,513` lifecycle rows, `968` real
    `coinbase_ticker` rows, `117` capture-health rows;
  - `0` timestamp/null ticker errors, `0` price out-of-range rows, `0`
    negative quantities, and `0` top-book gaps over `120s`;
  - `195` rows with null book fields, `340` crossed YES-book rows, and `322`
    crossed NO-book rows, leaving `92,166 / 92,718` valid top-book rows
    (`99.4046%`);
  - audit verdict: raw table is not perfectly clean, but
    `filtered_top_book_research_grade=true` and
    `promotion_grade_coinbase_ticks=true`;
  - independent-capture parity matched `586 / 654` raw buckets and
    `586 / 608` lowdd buckets; `94.01%` of matched buckets were within `1c`
    YES-mid, p95 difference `1c`.
- Official-settled replay on the full raw DuckDB snapshot with real Coinbase
  ticks for `2026-05-30T06:00:00Z..07:00:00Z`:
  - `cheap_yes_rr_first`: `2` trades, PnL `-$0.340`;
  - `cheap_no_rr_first`: `2` trades, PnL `-$0.135`;
  - `cheap_tail_best_side_first`: `4` trades, PnL `-$0.475`;
  - `cheap_tail_position_aware`: `4` trades, PnL `-$0.475`;
  - `cheap_pair_lock_rr`: `0` trades;
  - `current_lowdd_no_rv`: `0` trades.
  The sidecar-derived replay for the same window produced the same strategy
  summary, so synthetic BTC ticks did not change this window's decisions.
- Lowdd forward shadow produced the first clean post-restart paper fill:
  `KXBTC15M-26MAY300330-30`, `yes`, `7` contracts at `0.55`, quote age
  `3.0025ms`, visible quantity `154`, created
  `2026-05-30T07:25:01.391998Z`. The sidecar showed the selected signal at
  `2026-05-30T07:24:59.995431Z` and the order decision
  `paper_fill/filled` at `2026-05-30T07:25:01.599934Z` with
  `portfolio_available=96.16`; the old `sizing_budget_or_liquidity_zero`
  blocker did not recur.
- Kalshi REST official settlement for that lowdd row was `result=yes`,
  `expiration_value=73474.24`, finalized. Official paper PnL for the row:
  win, `+$3.02` after the recorded `$0.13` fee on `$3.98` premium.
- Validation:
  `python -m py_compile scripts\audit_btc15m_sidecar_fidelity.py scripts\test_btc15m_sidecar_fidelity.py scripts\backtest_btc15m_live_holdout.py scripts\test_btc15m_live_holdout.py`
  and
  `python -m pytest scripts\test_btc15m_live_holdout.py scripts\test_btc15m_sidecar_fidelity.py -q --basetemp .pytest-codex-tmp-btc15m-fidelity`
  passed with `4 passed`.
- Interpretation:
  the raw DuckDB now supports promotion-grade Coinbase tick-age replay for
  paused snapshots, after filtering invalid/crossed top-book rows. The latest
  full-snapshot strategy window is negative for cheap-tail variants. Lowdd has
  one clean post-restart official-settled win; keep collecting, but do not
  promote from a single row.

### 2026-05-30 - Lowdd post-restart report gate and replay parity row

- Added `scripts/build_btc15m_lowdd_postrestart_report.py`, a reusable
  post-restart evidence gate for the BTC15M lowdd paper shadow. It reads only
  ledger rows at or after a configured clean-restart timestamp, fetches official
  Kalshi settlement, computes PnL using the lowdd ledger's recorded fee as a
  total fill fee, audits optional sidecar signal/order counts, and writes
  CSV/JSON/Markdown reports.
- Added `scripts/test_btc15m_lowdd_postrestart_report.py` covering:
  total-fee PnL semantics, post-restart SQLite filtering, and row-order max
  drawdown summary.
- Ran the report on the remote lowdd ledger with
  `--since-utc 2026-05-30T06:33:25+00:00`. Remote artifact:
  `runtime\remote_backtests\btc15m_lowdd_postrestart_latest`.
  Summary:
  `1` trade, `1` settled, `1` win, `0` losses, official PnL `+$3.02`,
  premium `$3.98`, return on premium `75.8794%`, max drawdown `$0.00`.
- Sidecar context since the clean restart:
  `93,560` top-book rows, `5,139` lifecycle rows, `42,693` signal scans,
  and exactly `1` order decision. Signal actions:
  `38,486 none`, `2,998 skip`, `1,209 blocked`, `1 selected`.
  The only order action was `paper_fill / filled` at
  `2026-05-30T07:25:01.599934Z`.
- Materialized the lowdd sidecar window
  `2026-05-30T07:00:00Z..07:30:00Z`:
  `40,555` top-book rows, `3,454` lifecycle rows, `18,762` signal scans,
  `1` order decision, and `38,885` synthetic Coinbase rows.
- Offline replay on that same lowdd capture window reproduced the live paper
  lowdd decision:
  `current_lowdd_no_rv` had `1` trade, event
  `KXBTC15M-26MAY300330`, market `KXBTC15M-26MAY300330-30`, side `yes`,
  entry `0.55`, first entry `2026-05-30T07:25:00.023952Z`, official result
  `yes`, single-contract PnL `+$0.43`. The paper ledger scaled the same
  decision to `7` contracts, recorded total fee `$0.13`, and official PnL
  `+$3.02`.
- Other diagnostic rules on the same tiny two-event window:
  `cheap_yes_rr_first` and `cheap_tail_best_side_first` were `+$0.527` on
  `2` trades, `cheap_no_rr_first` was `-$0.620` on `1` trade,
  `cheap_pair_lock_rr` was `+$0.010` on `1`, and
  `cheap_tail_position_aware` was `-$0.093` on `2`. These are exploratory only.
- Interpretation:
  the lowdd post-restart accounting/execution path now has one official-settled
  row with replay parity. This validates that the previous stale-exposure
  blocker is fixed for at least one selected signal. It does not validate a
  deployable strategy; continue counting only future post-restart official
  rows and inspect row-level drawdown before promotion discussion.

### 2026-05-30 - Full-snapshot rolling-window replay and second lowdd post-restart row

- Added `scripts/build_btc15m_live_holdout_window_grid.py`, a rolling-window
  replay driver for already-snapshotted BTC15M DuckDB captures. It reuses the
  filtered live-holdout scoring path without repeatedly copying the same
  multi-GB snapshot. Added `scripts/test_btc15m_live_holdout_window_grid.py`
  for window clipping and bad-bound validation.
- Ran the grid on the remote full raw snapshot
  `runtime\duckdb_snapshots\raw_btc15m_full_20260530_071126\btc15m_live_capture.duckdb`
  for `2026-05-29T16:00:00Z..2026-05-30T07:00:00Z`, using one-hour windows and
  real Coinbase ticks. Remote artifact:
  `runtime\remote_backtests\btc15m_full_snapshot_window_grid_20260529_1600_20260530_0700`.
- Aggregate official-settled replay results:
  - `current_lowdd_no_rv`: `21` trades across `21` events, official PnL
    `+$2.40`, premium `$13.60`, return on premium `+17.647%`, win rate
    `76.19%`, max drawdown `-$0.90`, positive/negative/flat windows
    `10 / 2 / 3`.
  - `cheap_yes_rr_first`: `42` trades, official PnL `+$2.566`, premium
    `$13.434`, return on premium `+19.10%`, win rate `38.10%`, max drawdown
    `-$1.434`.
  - `cheap_no_rr_first`: `41` trades, official PnL `-$7.347`, return on
    premium `-51.21%`.
  - `cheap_tail_best_side_first`: `60` trades, official PnL `-$4.451`, return
    on premium `-30.80%`.
  - `cheap_tail_position_aware`: `60` trades, official PnL `-$5.061`, return
    on premium `-19.42%`.
  - `cheap_pair_lock_rr`: `21` diagnostic pair-lock rows, official PnL
    `+$1.11`, return on premium `+5.58%`; this remains diagnostic only until
    pair timing and executable two-leg behavior are audited.
- `current_lowdd_no_rv` window path:
  `+0.57`, `-0.90`, `+0.29`, `+0.11`, `+0.24`, `+0.09`, `+0.39`, `+0.43`,
  `-0.04`, `+0.25`, `+0.11`, `+0.86`, then three flat no-trade hours from
  `04:00Z..07:00Z`. This is promising as a replay stability check, but it
  still includes pre-restart replay rows and is not live-paper promotion
  evidence.
- Refreshed the remote lowdd post-restart report after the `07:55Z` decision.
  Artifact:
  `runtime\remote_backtests\btc15m_lowdd_postrestart_latest`.
  Since the clean restart at `2026-05-30T06:33:25Z`, the sidecar has `117,397`
  top-book rows, `54,126` signal scans, `2` selected signals, and `2`
  `paper_fill / filled` order decisions.
- Post-restart lowdd official-settled rows:
  - id `13`: `KXBTC15M-26MAY300330-30`, YES, `7` contracts at `0.55`, total
    fee `$0.13`, quote age `3.0025ms`, top visible qty `154`, Kalshi result
    YES, official PnL `+$3.02`.
  - id `14`: `KXBTC15M-26MAY300400-00`, NO, `4` contracts at `0.23`, total
    fee `$0.05`, quote age `56.1282ms`, top visible qty `30`, Kalshi result
    YES, official PnL `-$0.97`.
- Post-restart lowdd summary is now `2` trades, `2` settled, `1` win, `1` loss,
  official PnL `+$2.05`, premium `$4.95`, return on premium `41.4141%`, win
  rate `50%`, max drawdown `-$0.97`.
- Remote live health from sidecar tails at `2026-05-30T08:04Z`: raw BTC15M
  capture latest top-book row was `2026-05-30T08:03:57.561998Z`; lowdd latest
  top-book row was `2026-05-30T08:03:59.833492Z`, latest signal scan was
  `2026-05-30T08:03:59.834478Z`, and the latest order decision was the
  `07:55:28.491029Z` filled NO paper row. Scheduled-task/process enumeration
  through CIM was denied over SSH, so sidecar freshness is the operational
  health evidence for this check.
- Interpretation:
  the 15-hour full-snapshot replay makes lowdd worth continued forward testing,
  but the fresh official paper ledger is still too small and already has one
  loss. Keep the raw collector and lowdd paper shadow running; do not promote
  or size up without substantially more post-restart official-settled rows and
  row-level drawdown stability.

### 2026-05-30 - Pair-lock selection-bias audit rejects pair-only candidate

- Added `scripts/audit_btc15m_pair_lock_selection_bias.py` and
  `scripts/test_btc15m_pair_lock_selection_bias.py`.
- Purpose:
  `cheap_pair_lock_rr` only reports events where a later opposite-side leg
  appeared cheaply enough to lock payout. That is future-conditioned unless the
  strategy also counts first-leg exposure for events where no later lock
  appears. The causal comparison is `cheap_tail_position_aware`, which keeps
  those unpaired first legs.
- Fetched the small CSV outputs from the remote 15-hour full raw replay into
  ignored `runtime\remote_backtests\btc15m_full_snapshot_window_grid_20260529_1600_20260530_0700`
  and audited them plus the local sidecar replay windows. Output:
  `backtest_outputs\btc15m_pair_lock_selection_bias_20260530_latest`.
- Results:
  - Full raw `2026-05-29T16:00Z..2026-05-30T07:00Z`:
    `cheap_pair_lock_rr` had `21` pair rows, pair-only PnL `+$1.11`; the
    position-aware comparison had the same `21` paired rows but also `39`
    unpaired first-leg exposures with PnL `-$6.171`, making total
    position-aware PnL `-$5.061`, max drawdown `-$5.376`.
  - Sidecar `2026-05-29T18:00Z..2026-05-30T06:00Z`:
    pair-only PnL `+$0.71`; `32` unpaired exposures lost `-$5.534`, making
    position-aware PnL `-$4.824`.
  - Sidecar `18:00Z..00:00Z`:
    pair-only PnL `+$0.49`; `17` unpaired exposures lost `-$2.857`, making
    position-aware PnL `-$2.367`.
  - Sidecar `00:00Z..06:00Z`:
    pair-only PnL `+$0.22`; `15` unpaired exposures lost `-$2.677`, making
    position-aware PnL `-$2.457`.
- Verdict:
  all audited windows are `reject_pair_only_future_conditioned`. Pair-lock
  remains a useful diagnostic for book structure, but it is not a standalone
  strategy and should not be deployed or ranked as a candidate unless a future
  runner can enter both legs causally without carrying unbounded first-leg
  selection risk.

### 2026-05-30 - BTC15M replay robustness audit and third lowdd paper fill

- Added `scripts/audit_btc15m_replay_candidate_robustness.py` and
  `scripts/test_btc15m_replay_candidate_robustness.py`.
- Purpose:
  turn positive replay summaries into a stricter scientific gate by checking
  trade count, path drawdown, trade-bootstrap PnL, profit probability, and
  rolling-window stability when window summaries are available. Pair-lock rows
  are explicitly excluded because the selection-bias audit above already
  rejected them as future-conditioned.
- Ran the audit on:
  - full raw replay
    `runtime\remote_backtests\btc15m_full_snapshot_window_grid_20260529_1600_20260530_0700`;
  - sidecar slices
    `runtime\branch_backtests\btc15m_live_holdout_sidecar_20260529_1600_1800`,
    `runtime\branch_backtests\btc15m_live_holdout_sidecar_20260529_1800_20260530_0000`,
    `runtime\branch_backtests\btc15m_live_holdout_sidecar_20260530_0000_0600`,
    and `runtime\branch_backtests\btc15m_live_holdout_sidecar_20260529_1800_20260530_0600`.
  Output:
  `backtest_outputs\btc15m_replay_candidate_robustness_20260530_latest`.
- Full raw `2026-05-29T16:00Z..2026-05-30T07:00Z` robustness results:
  - `current_lowdd_no_rv`: `21` trades, official PnL `+$2.40`, return on
    premium `17.65%`, win rate `76.19%`, max drawdown `-$0.90`, positive /
    negative / flat windows `10 / 2 / 3`, trade-bootstrap p05 `-$0.14`, profit
    probability `93.86%`. Verdict:
    `research_promising_insufficient_sample_bootstrap_fragile`.
  - `cheap_yes_rr_first`: `42` trades, official PnL `+$2.566`, return on
    premium `19.10%`, win rate `38.10%`, max drawdown `-$1.434`, windows
    `9 / 5 / 1`, trade-bootstrap p05 `-$1.921`, profit probability `82.42%`.
    Verdict: `research_promising_insufficient_sample_bootstrap_fragile`.
  - `cheap_no_rr_first`, `cheap_tail_best_side_first`, and
    `cheap_tail_position_aware` remain rejected on non-positive official PnL.
  - `cheap_pair_lock_rr` is excluded by the prior selection-bias audit even
    though its pair-only bootstrap is positive.
- Sidecar-slice robustness was directionally consistent:
  `current_lowdd_no_rv` was positive on `18:00Z..00:00Z`, `00:00Z..06:00Z`,
  and `18:00Z..06:00Z`, but negative on `16:00Z..18:00Z`; every positive
  sidecar slice still failed the sample-size gate.
- Refreshed the remote lowdd post-restart report after a new paper fill at
  `2026-05-30T08:10:01.945911Z`.
  Since the clean restart, the lowdd sidecar now has `140,665` top-book rows,
  `64,850` signal scans, `3` selected signals, and `3` `paper_fill / filled`
  order decisions through `2026-05-30T08:19:56Z`.
- New post-restart row:
  id `15`, `KXBTC15M-26MAY300415-15`, YES, `9` contracts at `0.65`, total fee
  `$0.15`, quote age `1.9963ms`, top visible qty `21.37`, Kalshi result YES,
  official PnL `+$3.00`.
- Post-restart lowdd summary is now `3` trades, `3` settled, `2` wins,
  `1` loss, official PnL `+$5.05`, premium `$10.95`, return on premium
  `46.1187%`, win rate `66.6667%`, max drawdown `-$0.97`.
- Remote sidecar freshness at `2026-05-30T08:19Z`: raw BTC15M latest top-book
  row was `2026-05-30T08:19:32.719186Z`; lowdd latest top-book row was
  `2026-05-30T08:19:33.991232Z` and latest signal scan was
  `2026-05-30T08:19:33.995225Z`.
- Interpretation:
  `current_lowdd_no_rv` is still the lead BTC15M research path because it has
  replay positivity, sidecar consistency after `18:00Z`, live paper parity, and
  now `3` official-settled post-restart paper rows. It remains strictly
  research-only: the full replay bootstrap lower tail crosses zero, the profit
  probability is below a `95%` robustness gate, and the forward sample is far
  below a reasonable promotion count.

### 2026-05-30 - Lowdd paper fill replay parity audit

- Added `scripts/audit_btc15m_lowdd_paper_replay_parity.py` and
  `scripts/test_btc15m_lowdd_paper_replay_parity.py`.
- Materialized the lowdd replay sidecar for
  `2026-05-30T07:00:00Z..2026-05-30T08:20:00Z` into ignored remote runtime:
  `runtime\sidecar_slices\btc15m_lowdd_20260530_0700_0820.duckdb`.
  Counts: `106,109` top-book rows, `6,638` lifecycle rows, `49,590` signal
  scans, `3` order decisions, and `101,985` synthetic Coinbase rows from
  top-book `btc_spot`. This is wrapper-parity evidence, not promotion-grade
  raw Coinbase tick-age replay.
- Offline generic replay on that slice:
  `current_lowdd_no_rv` had `3` trades across the three post-restart events,
  single-contract PnL `+$0.42`, return on premium `26.58%`, win rate
  `66.67%`, max drawdown `-$0.25`.
- The row-level paper/sidecar/replay parity audit output is:
  `runtime\remote_backtests\btc15m_lowdd_paper_replay_parity_20260530_0700_0820`.
- Paper ledger versus live sidecar:
  all `3 / 3` post-restart paper fills had matching `signal_scan` selected rows
  and `order_decision` paper-fill rows with exact entry-price parity:
  - id `13`: paper `0.55`, signal `0.55`, order `0.55`;
  - id `14`: paper `0.23`, signal `0.23`, order `0.23`;
  - id `15`: paper `0.65`, signal `0.65`, order `0.65`.
- Paper ledger versus generic replay:
  ids `13` and `14` matched the generic replay price exactly, but id `15`
  did not: paper/live sidecar entry was `0.65`, while the generic offline
  `current_lowdd_no_rv` replay row for the same event/market/side used `0.74`.
  Verdict counts:
  `2` `full_live_and_generic_replay_parity_pass`, `1`
  `live_sidecar_parity_pass_generic_replay_price_mismatch`.
- Interpretation:
  the running paper wrapper is producing faithful sidecar signal/order rows, so
  fresh paper fills can count for the post-restart forward-evidence clock.
  However, the generic `backtest_btc15m_live_holdout.py` lowdd rule is not
  perfectly policy-equivalent to the live wrapper on every row. Use the live
  sidecar selected/order rows as the authoritative replay-parity check for
  post-restart paper fills; treat generic replay summaries as conservative
  diagnostics unless row-level price parity passes.

### 2026-05-30 - Sidecar-selected lowdd official replay

- Added `scripts/backtest_btc15m_lowdd_sidecar_selected.py` and
  `scripts/test_btc15m_lowdd_sidecar_selected.py`.
- Purpose:
  create a policy-equivalent replay path for the running lowdd paper wrapper by
  using `signal_scan` rows where `action = selected`, rather than rescoring all
  top-book rows in the generic replay harness. This preserves the live wrapper's
  exact selected market, side, entry price, and scan cadence.
- Ran it on the materialized lowdd sidecar slice
  `runtime\sidecar_slices\btc15m_lowdd_20260530_0700_0820.duckdb`.
  Output:
  `runtime\remote_backtests\btc15m_lowdd_sidecar_selected_20260530_0700_0820`.
- Official-settled sidecar-selected results:
  - selected rows: `3`;
  - settled rows: `3`;
  - one-contract signal PnL: `+$0.51` on `$1.49` premium, return on premium
    `34.2282%`, win rate `66.6667%`, max drawdown `-$0.25`;
  - paper-order-scaled PnL: `+$5.05` on `$10.95` premium, return on premium
    `46.1187%`, win rate `66.6667%`, max drawdown `-$0.97`;
  - order price mismatch rows: `0`.
- Row-level sidecar-selected official results:
  - `KXBTC15M-26MAY300330-30`, YES at `0.55`, official YES, one-contract PnL
    `+$0.43`, paper scaled PnL `+$3.02`;
  - `KXBTC15M-26MAY300400-00`, NO at `0.23`, official YES, one-contract PnL
    `-$0.25`, paper scaled PnL `-$0.97`;
  - `KXBTC15M-26MAY300415-15`, YES at `0.65`, official YES, one-contract PnL
    `+$0.33`, paper scaled PnL `+$3.00`.
- Interpretation:
  the sidecar-selected replay should be the authoritative wrapper-parity
  backtest for fresh lowdd paper evidence. The generic top-book replay remains
  useful for broader research-window diagnostics, but it is not allowed to
  prove wrapper policy parity unless its row-level selected prices match the
  sidecar-selected rows.

### 2026-05-30 - Lowdd forward promotion gate

- Added `scripts/build_btc15m_lowdd_forward_promotion_gate.py` and
  `scripts/test_btc15m_lowdd_forward_promotion_gate.py`.
- Purpose:
  convert the current post-restart lowdd evidence stack into a machine-readable
  promotion decision. Inputs are the sidecar-selected official replay, the
  paper/live-sidecar parity audit, and the post-restart official paper summary.
  The gate does not rescore data or tune thresholds.
- Ran it on the latest lowdd artifacts:
  - selected replay:
    `runtime\remote_backtests\btc15m_lowdd_sidecar_selected_20260530_0700_0820`;
  - parity audit:
    `runtime\remote_backtests\btc15m_lowdd_paper_replay_parity_20260530_0700_0820`;
  - post-restart paper report:
    `runtime\remote_backtests\btc15m_lowdd_postrestart_latest`.
- Output:
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_latest`.
- Gate result:
  `production_ready = false`,
  `research_status = research_promising_insufficient_forward_sample`.
- Passing evidence:
  live sidecar parity is `3 / 3`, order price mismatch rows are `0`,
  one-contract signal PnL is `+$0.51`, paper-order-scaled PnL is `+$5.05`,
  and order-scaled max drawdown is `-$0.97`.
- Blockers:
  `selected_rows_below_min`, `settled_rows_below_min`, `order_rows_below_min`,
  and `paper_settled_rows_below_min`; all are caused by only `3` settled
  forward rows versus the default `50` row minimum.
- Advisory:
  generic replay still has `1` price mismatch, so generic top-book replay
  remains diagnostic. The sidecar-selected replay is the authoritative
  wrapper-parity path for future lowdd paper fills.

### 2026-05-30 - Lowdd four-row refresh and raw-vs-shadow fidelity

- Remote status at about `2026-05-30T08:51Z`:
  - raw BTC15M capture was live with `failed = false`, `last_error = ""`,
    `ws_orderbook_top = 17,112,049`, and latest top-book
    `2026-05-30T08:51:22.784375Z`;
  - lowdd sidecar was live with `failed = false`, `last_error = ""`,
    `signal_scan = 2,542,645`, `order_decision = 5,720`, and latest top-book
    `2026-05-30T08:51:28.242123Z`.
- New post-restart lowdd paper fill:
  id `16`, `KXBTC15M-26MAY300430-30`, YES, `9` contracts at `0.65`,
  official YES, paper-order PnL `+$3.00`.
- Materialized only the bounded lowdd sidecar decision slice
  `2026-05-30T07:00:00Z..2026-05-30T08:40:00Z` into ignored runtime:
  `runtime\sidecar_slices\btc15m_lowdd_20260530_0700_0840.duckdb`.
  Counts: `62,540` signal-scan rows and `4` order-decision rows.
- Refreshed official post-restart paper report:
  `runtime\remote_backtests\btc15m_lowdd_postrestart_20260530_0700_0840`.
  Results: `4` trades, `4` settled, `3` wins, official PnL `+$8.05`,
  premium `$16.95`, return on premium `47.4926%`, win rate `75%`, max
  drawdown `-$0.97`.
- Refreshed sidecar-selected official replay:
  `runtime\remote_backtests\btc15m_lowdd_sidecar_selected_20260530_0700_0840`.
  Results: `4` selected rows, `4` settled rows, one-contract PnL `+$0.84`,
  paper-order-scaled PnL `+$8.05`, order price mismatch rows `0`.
- Refreshed paper/live-sidecar parity:
  `runtime\remote_backtests\btc15m_lowdd_paper_replay_parity_20260530_0700_0840`.
  All `4 / 4` paper fills matched live sidecar selected/order rows. The generic
  replay comparison was not supplied for this quick refresh, so all four
  verdicts are `live_sidecar_parity_pass_generic_replay_missing`.
- Refreshed the lowdd forward gate:
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_0900`.
  Verdict remains `production_ready = false`,
  `research_status = research_promising_insufficient_forward_sample`. The only
  blockers are still sample-size blockers: `selected_rows_below_min`,
  `settled_rows_below_min`, `order_rows_below_min`, and
  `paper_settled_rows_below_min`, because `4` rows is far below the default
  `50` row threshold.
- Raw-vs-lowdd capture fidelity check:
  materialized `2026-05-30T08:00:00Z..2026-05-30T08:40:00Z` top-book slices
  from the raw capture and lowdd sidecar into ignored runtime. Raw slice counts:
  `58,806` top-book rows, `3,217` lifecycle rows, and `56,189` synthetic
  Coinbase rows. Lowdd slice counts: `57,831` top-book rows, `3,172`
  lifecycle rows, and `55,403` synthetic Coinbase rows.
- Fidelity output:
  `backtest_outputs\btc15m_raw_lowdd_fidelity_20260530_0800_0840`.
  The raw top-book slice has no hard failures, `58,684 / 58,806` valid
  filterable top-book rows (`99.7925%`), and no top-book gaps over `120s`.
  Filterable failures are `27` null-book-field rows, `86` crossed YES-book
  rows, and `79` crossed NO-book rows.
- Raw-vs-lowdd parity:
  `442 / 450` raw 5-second buckets matched the lowdd sidecar (`98.22%` raw
  match rate, `99.33%` lowdd match rate). YES mid-price p95 difference was
  `0c`; `97.49%` of matched buckets were within `1c`; maximum matched-bucket
  difference was `9c`.
- Patched `scripts/audit_btc15m_sidecar_fidelity.py` so copied materialized
  DuckDBs without their manifest do not accidentally mark synthetic Coinbase
  rows as promotion-grade. The audit now inspects the Coinbase table schema and
  marks synthetic-or-unknown schema as not promotion-grade BTC tick-age
  evidence. Regression coverage was added in
  `scripts/test_btc15m_sidecar_fidelity.py`.
- Interpretation:
  raw capture remains faithful enough for filtered top-book research on this
  fresh window and agrees closely with the lowdd sidecar. It is not
  promotion-grade Coinbase tick-age evidence because these materialized
  Coinbase rows are synthetic from top-book `btc_spot`.

### 2026-05-30 - Readable raw-capture probe and supervision caveat

- Goal:
  get fresh real `coinbase_ticker` evidence without relying on synthetic
  Coinbase rows from the replay sidecar.
- First attempted a non-invasive same-machine copy of the active raw
  `btc15m_live_capture.duckdb` plus `.wal`. Windows denied reading the active
  main DB while persistent capture PID `6084` held the file.
- Then attempted a controlled raw-capture pause using scheduled task
  `\KalshiBTC_btc15m_live_capture`. `schtasks /End` succeeded, but the child
  Python writer PID `6084` remained alive and `taskkill /PID 6084 /F` returned
  `Access denied`, so the DB remained locked and no full fresh snapshot was
  obtained.
- Important supervision state after that attempt:
  the raw child process continued collecting, but the scheduled task wrapper
  showed `Ready` rather than `Running`. Final status at about
  `2026-05-30T09:20Z`: raw capture `failed=false`, `last_error=""`,
  `dropped=0`, `queue_depth=0`, latest top-book
  `2026-05-30T09:20:10.341083Z`, latest Coinbase
  `2026-05-30T09:20:06.201371Z`, `17,134,854` top-book rows, and `202,731`
  Coinbase rows. Lowdd remained live with latest top-book
  `2026-05-30T09:20:12.341856Z` and no new order decision after
  `2026-05-30T08:25:09.739962Z`.
- Safer workaround:
  ran a separate finite capture-only probe using the readable writer, without
  touching the live collector:
  `scripts\btc15m_live_capture.py --capture-db-path
  C:\Users\ClawService\.btc_kalshi_bot\codex_btc15m_readable_probe_20260530_0915.duckdb
  --capture-writer readable --duration-sec 180 --refresh-sec 10 --health-sec 30`.
  It exited cleanly with `failed=false`, `last_error=""`, `dropped=0`, and a
  small readable DB copied locally under ignored `runtime\remote_snapshots`.
- Fidelity audit output:
  `backtest_outputs\btc15m_readable_probe_fidelity_20260530_0915`.
  Window: `2026-05-30T09:14:20Z..2026-05-30T09:17:40Z`.
  Probe counts: `1,493` top-book rows, `43` lifecycle rows, `24` real
  Coinbase ticker rows, `8` capture-health rows. Coinbase schema included raw
  fields, so `promotion_grade_coinbase_ticks=true`.
- Probe top-book quality:
  no timestamp/ticker errors, no out-of-range prices, no crossed books, no
  negative quantities, and no gaps over `120s`. The only filterable issue was
  `27` null-book-field rows, leaving `1,466 / 1,493` valid top-book rows
  (`98.1916%`). Verdict:
  `filtered_top_book_research_grade=true`.
- Probe versus active raw replay sidecar:
  materialized active raw sidecar window
  `runtime\sidecar_slices\btc15m_raw_sidecar_20260530_0914_0918.duckdb`
  had `1,741` top-book rows, `30` lifecycle rows, and `1,604` synthetic
  Coinbase rows. Bucket parity against the readable probe matched all `34 / 34`
  probe buckets and `34 / 37` sidecar buckets. YES-mid mean, p95, and max
  difference were all `0c`; all matched buckets were within `1c`.
- Interpretation:
  the readable writer path can produce fresh, promotion-grade real Coinbase
  tick evidence while agreeing exactly with the active raw sidecar on matched
  top-book buckets in this short probe. The existing persistent raw process is
  still collecting, but its task-wrapper supervision state needs care before
  any future planned pause/restart; do not start a duplicate persistent writer
  against the same DB.

### 2026-05-30 - Collector status supervision patch

- Patched `scripts/status_btc_remote_collectors.ps1` to report scheduled-task
  supervision separately from process liveness. The script now:
  - derives the expected task name as `KalshiBTC_<collector_name>` when the
    runtime manifest has a blank `task_name`;
  - reports `manifest_task_name`, resolved `task_name`, `task_exists`,
    `task_runtime_status`, `task_status_source`, `task_status_error`, and
    `task_supervision_status` per row;
  - emits a top-level `supervision_status` while preserving the existing
    process-level `status` field, so watchdog logic that keys off process
    liveness is not silently changed.
- Local validation:
  parsed the PowerShell script with `[scriptblock]::Create(...)` and ran it
  against a fake runtime manifest. The fake missing-task case now produces
  top-level `supervision_status = TASK_MISSING`.
- Deployed only the patched status script to the remote working copy and ran it
  against the real manifest. Output confirmed the raw capture supervision
  issue directly:
  - top-level `supervision_status = PROCESS_RUNNING_TASK_NOT_RUNNING`;
  - `btc15m_live_capture` process PID `6084` is alive and read from
    `capture_status_sidecar`;
  - resolved task name is `KalshiBTC_btc15m_live_capture`;
  - `task_exists = true`, `task_runtime_status = Ready`;
  - row-level `task_supervision_status =
    PROCESS_RUNNING_TASK_NOT_RUNNING`.
- Interpretation:
  status tooling now makes the current laptop state explicit: raw capture is
  alive, but not task-supervised. Do not use `schtasks /Run` or
  `ensure_btc_remote_collectors.ps1` casually while PID `6084` is still holding
  the raw DB, because that could attempt to start a duplicate writer.

### 2026-05-30 - Collector ensure unsafe-restart guard

- Patched `scripts/ensure_btc_remote_collectors.ps1` so it checks the new
  `task_supervision_status` rows before deciding to restart collectors.
- New behavior:
  if any row reports `PROCESS_RUNNING_TASK_NOT_RUNNING`, `ensure` returns
  `action = blocked_process_running_task_not_running`, includes
  `unsafe_supervision_rows`, and does not call
  `start_btc_remote_collectors.ps1`.
- Local validation:
  both `ensure_btc_remote_collectors.ps1` and `status_btc_remote_collectors.ps1`
  parsed with `[scriptblock]::Create(...)`.
- Remote validation:
  deployed only the patched `ensure` and `status` scripts to the laptop working
  copy, then ran:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  C:\Users\ClawService\Kalshi-Trading-Bot\scripts\ensure_btc_remote_collectors.ps1
  -RepoRoot C:\Users\ClawService\Kalshi-Trading-Bot`.
- Remote output:
  `action = blocked_process_running_task_not_running`,
  `restart_blocked_reason` explicitly says a live collector has a non-running
  task and restart could create duplicate locked-DB writers, and
  `start_result = null`.
- The blocked row is the raw BTC15M capture:
  PID `6084`, process source `capture_status_sidecar`, task
  `KalshiBTC_btc15m_live_capture`, `task_runtime_status = Ready`, row-level
  `task_supervision_status = PROCESS_RUNNING_TASK_NOT_RUNNING`.
- The raw sidecar remained fresh during the `ensure` check:
  status age about `4s`, `dropped = 0`, `queue_depth = 0`.
- Interpretation:
  the watchdog/ensure path is now safer in the current laptop state. It will
  not automatically start a duplicate raw capture while the unsupervised PID is
  alive and holding the active DuckDB.

### 2026-05-30 - BTC15M post-restart lowdd forward evidence window

- Remote status checked at about `2026-05-30T09:35Z`:
  raw `btc15m_live_capture` was still fresh and live with PID `6084`,
  `failed=false`, `dropped=0`, replay sidecar size about `12.63GB`, and
  `task_supervision_status = PROCESS_RUNNING_TASK_NOT_RUNNING`.
  Lowdd was separately task-supervised by
  `\KalshiBTC_btc15m_lowdd_forward_shadow`, with fresh
  `btc15m_lowdd_forward_shadow_capture.duckdb.status.json` and replay sidecar
  writes.
- Materialized a bounded post-restart window from remote replay sidecars
  without pausing collection:
  `2026-05-30T06:40:00Z..2026-05-30T09:15:00Z`.
  Local ignored slices:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_0915\btc15m_raw_20260530_0640_0915.duckdb`
  and
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_0915\btc15m_lowdd_20260530_0640_0915.duckdb`.
- Raw slice counts:
  `189,370` top-book rows, `844` lifecycle rows, `180,520` synthetic Coinbase
  rows, `11` BTC15M events. Fidelity artifact:
  `backtest_outputs\btc15m_raw_sidecar_fidelity_20260530_0640_0915`.
  Verdict remained research-only:
  `promotion_grade_coinbase_ticks=false` because Coinbase rows are synthetic
  from top-book `btc_spot`; top-book hard failure was
  `top_book_gaps_over_threshold`; valid-book rate was `99.5849%`.
- Official live-holdout replay artifact:
  `backtest_outputs\btc15m_live_holdout_20260530_0640_0915`.
  All `11` events had finalized Kalshi settlement. Results:
  `current_lowdd_no_rv` had `4` trades, `+$0.75` one-contract PnL,
  `+33.33%` return on premium, `75%` win rate, max drawdown `-$0.25`.
  Other rule-pack rows were mixed/negative except small diagnostic positives:
  `cheap_yes_rr_first` `+$0.619`, `cheap_pair_lock_rr` `+$0.160`.
- Lowdd sidecar-selected replay artifact:
  `backtest_outputs\btc15m_lowdd_sidecar_selected_20260530_0640_0915`.
  The lowdd wrapper emitted `4` selected/paper-fill decisions in this bounded
  window. All `4` officially settled. Sidecar-selected one-contract PnL was
  `+$0.84`; scaled order PnL was `+$8.05` on `$16.95` premium, `75%` win rate,
  max drawdown `-$0.97`, and `order_price_mismatch_rows=0`.
- Copied only the small remote paper ledger DB to ignored runtime and generated
  a bounded paper-ledger report with a new `--end-utc` option:
  `backtest_outputs\btc15m_lowdd_postrestart_20260530_0640_0915_bounded`.
  Same-window paper ledger matched sidecar-selected economics:
  `4` trades, `4` settled, official PnL `+$8.05`, premium `$16.95`,
  return on premium `47.4926%`, max drawdown `-$0.97`.
- Paper/live-sidecar parity artifact:
  `backtest_outputs\btc15m_lowdd_paper_replay_parity_20260530_0640_0915`.
  All `4 / 4` paper rows passed live sidecar parity. Generic raw replay had
  one price mismatch, recorded as advisory because the live sidecar-selected
  order stream is the policy-equivalent source for wrapper parity.
- Promotion gate artifact:
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_0640_0915_rerun`.
  Verdict:
  `production_ready=false`,
  `research_status=research_promising_insufficient_forward_sample`.
  Blockers are only sample-size gates:
  `selected_rows_below_min`, `settled_rows_below_min`, `order_rows_below_min`,
  and `paper_settled_rows_below_min` (`4` rows vs `50` required).
- Interpretation:
  the accounting fix worked on the first bounded post-restart evidence window,
  and lowdd is now the most promising active paper-forward candidate. It is
  still not deployable: the sample is tiny, the raw materialized slice uses
  synthetic Coinbase rows, and raw capture supervision remains unsafe for
  casual restarts. Continue collecting lowdd paper rows and periodically rerun
  this bounded evidence packet; do not promote until the gate passes on a much
  larger official-settled forward sample.

### 2026-05-30 - BTC collector status active-target cleanup and six-row lowdd refresh

- Patched `scripts/status_btc_remote_collectors.ps1` so the current active
  target set is explicit:
  `btc15m_live_capture` plus `btc15m_lowdd_forward_shadow`. The script still
  reports stale manifest rows as `manifest_legacy`, but now adds lowdd as
  `current_active_observed` when the old manifest does not include it.
- Patched `scripts/ensure_btc_remote_collectors.ps1` so freshness checks cover
  only the current active sidecars: raw BTC15M capture and lowdd forward.
  If current active rows are unhealthy, `ensure` now blocks with
  `blocked_current_active_manual_restart_required` instead of calling the
  legacy `start_btc_remote_collectors.ps1`, which would launch stale
  q250/q1000/BTC1H targets.
- Validation:
  local parse passed for both PowerShell scripts, and a fake manifest test
  produced a `btc15m_lowdd_forward_shadow` row with
  `target_set = current_active_observed`.
- Deployed only the patched `status` and `ensure` scripts to the laptop and
  validated against live state at about `2026-05-30T09:53Z`:
  `current_active_status = ALL_CURRENT_ACTIVE_RUNNING`.
  Current active rows:
  raw PID `6084`, `task_runtime_status = Ready`,
  `task_supervision_status = PROCESS_RUNNING_TASK_NOT_RUNNING`;
  lowdd PID `12796`, `task_runtime_status = Running`,
  `task_supervision_status = TASK_PROCESS_ALIGNED`.
  `ensure` remained safe with
  `action = blocked_process_running_task_not_running`, and both current active
  sidecars were fresh with `dropped = 0`.
- Refreshed the post-restart lowdd paper ledger through
  `2026-05-30T09:53Z`:
  artifact `backtest_outputs\btc15m_lowdd_postrestart_20260530_0640_0953`.
  Result: `6` trades, all `6` officially settled, `5` wins, official PnL
  `+$14.27`, premium `$27.73`, return on premium `51.4605%`, max drawdown
  `-$0.97`.
- Materialized matching raw and lowdd replay sidecar windows
  `2026-05-30T06:40:00Z..2026-05-30T09:53:00Z` without pausing collection.
  Raw slice counts:
  `225,976` top-book rows, `1,063` lifecycle rows, `214,115` synthetic
  Coinbase rows, `14` BTC15M events. Fidelity artifact:
  `backtest_outputs\btc15m_raw_sidecar_fidelity_20260530_0640_0953`.
  Verdict remained research-only:
  `promotion_grade_coinbase_ticks=false`,
  `top_book_gaps_over_threshold`, valid-book rate `99.5699%`.
- Official live-holdout replay artifact:
  `backtest_outputs\btc15m_live_holdout_20260530_0640_0953`.
  `current_lowdd_no_rv` produced `6` trades, `+$1.49` one-contract PnL,
  `42.4501%` return on premium, `83.3333%` win rate, max drawdown `-$0.25`.
  `cheap_pair_lock_rr` stayed diagnostic-positive with `6` trades, `+$0.24`,
  but it is still not separately execution-audited.
- Lowdd sidecar-selected replay artifact:
  `backtest_outputs\btc15m_lowdd_sidecar_selected_20260530_0640_0953`.
  It found the same `6` selected/order rows, all settled, scaled order PnL
  `+$14.27`, premium `$27.73`, win rate `83.3333%`, max drawdown `-$0.97`.
  It also surfaced `order_price_mismatch_rows = 1`: the
  `KXBTC15M-26MAY300545-45` NO row was selected at `57c` and paper-filled at
  `58c` about `0.56s` later.
- Parity artifact:
  `backtest_outputs\btc15m_lowdd_paper_replay_parity_20260530_0640_0953`.
  `5 / 6` rows passed live sidecar parity; one failed live sidecar price
  parity due to the `57c -> 58c` selected/order reprice. Generic raw replay had
  one additional price mismatch.
- Promotion gate artifact:
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_0640_0953_rerun`.
  Verdict:
  `production_ready=false`,
  `research_status=research_blocked_or_rejected`.
  Blockers:
  sample-size gates, `order_price_mismatch_rows_nonzero`, and
  `live_sidecar_parity_not_all_rows`.
- Interpretation:
  lowdd economics improved from `4` to `6` settled post-fix rows, but the new
  6-row packet found a live selected-price-to-order-price mismatch. Treat this
  as a real execution/parity blocker until the wrapper/gate distinguishes
  acceptable quote-movement repricing from policy drift. Do not promote; keep
  collecting and next inspect whether the 1c reprice is expected and logged
  well enough to count order-price PnL without weakening execution realism.

### 2026-05-30 - BTC15M lowdd selected-to-order reprice parity audit

- Inspected the active wrapper path in `scripts\btc15m_lowdd_live.py`.
  The sidecar `signal_scan` row is recorded with `action = selected` before
  the hot-book reprice. The order/paper-fill path then reprices from the
  current book with `BTC15M_MAX_REPRICE_WORSE_CENTS` defaulting to `2.0c`.
- Verified the only six-row mismatch directly from the materialized lowdd
  sidecar slice:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_0953\btc15m_lowdd_20260530_0640_0953.duckdb`.
  For `KXBTC15M-26MAY300545-45`, the selected NO signal was at
  `2026-05-30T09:40:10.207556Z` with entry `57c`; the nearest prior book was
  age `2.012ms` with `no_ask = 57c`. The paper order was at
  `2026-05-30T09:40:10.769246Z` with entry `58c`; the nearest prior book was
  age `0.998ms` with `no_ask = 58c`. The order used `8` contracts, YES limit
  `42c`, estimated cost `$4.78`, and net edge `14.25c`.
- Patched `scripts\audit_btc15m_lowdd_paper_replay_parity.py` so paper/order
  parity remains mandatory, but bounded selected-signal-to-order reprices are
  classified separately. The audit now emits
  `live_sidecar_price_mismatch_rows`, `signal_order_reprice_rows`,
  `signal_order_reprice_over_limit_rows`, and
  `max_signal_order_worse_reprice_cents`.
- Patched `scripts\build_btc15m_lowdd_forward_promotion_gate.py` so a selected
  price mismatch is not a blocker when the audit proves order/paper parity and
  the selected-to-order reprice stayed inside the configured cap. True
  live-sidecar order price mismatches and over-limit reprices remain blockers.
- Validation:
  `python -m pytest scripts\test_btc15m_lowdd_paper_replay_parity.py scripts\test_btc15m_lowdd_forward_promotion_gate.py -q --basetemp .pytest-codex-tmp-lowdd-reprice`
  passed with `11` tests. `python -m py_compile` also passed for the touched
  audit, gate, and test scripts.
- Rerun parity artifact:
  `backtest_outputs\btc15m_lowdd_paper_replay_parity_20260530_0640_0953_reprice_audit`.
  Result: `6 / 6` live sidecar parity pass rows,
  `live_sidecar_price_mismatch_rows = 0`,
  `signal_order_reprice_rows = 1`,
  `signal_order_reprice_over_limit_rows = 0`,
  `max_signal_order_worse_reprice_cents = 1.0`, and
  `generic_replay_price_mismatch_rows = 2`.
- Rerun promotion gate artifact:
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_0640_0953_reprice_audit`.
  Verdict changed to
  `production_ready=false`,
  `research_status=research_promising_insufficient_forward_sample`.
  Remaining blockers are only the sample-size gates:
  `selected_rows_below_min`, `settled_rows_below_min`,
  `order_rows_below_min`, and `paper_settled_rows_below_min`.
  Advisories are
  `generic_replay_price_mismatch_sidecar_selected_is_authoritative` and
  `selected_order_reprice_within_config_limit`.
- Interpretation:
  the 57c-to-58c row is expected live quote movement during the wrapper's
  guarded reprice path, not a policy/parity drift. The order/paper official
  economics remain matched at `+$14.27` on `$27.73` premium across `6` settled
  rows. This improves the lowdd status from rejected to promising, but it is
  still not deployable because the forward sample is too small and the raw
  materialized slice still uses synthetic Coinbase rows.

### 2026-05-30 - BTC15M 10:12Z lowdd refresh and branch applicability check

- Checked the laptop over SSH at about `2026-05-30T10:11Z`. The heavier
  collector status script did not return within `60s`, so direct status-sidecar
  and scheduled-task checks were used instead.
- Remote raw BTC15M capture remained fresh:
  `btc15m_live_capture.duckdb.status.json` had `failed=false`, `dropped=0`,
  PID `6084`, queue depth `0`, latest `ws_orderbook_top` at
  `2026-05-30T10:11:31.091425Z`, and replay sidecar rows
  `17,183,243` for `ws_orderbook_top`.
- Remote lowdd forward shadow remained fresh:
  `btc15m_lowdd_forward_shadow_capture.duckdb.status.json` had
  `failed=false`, `dropped=0`, PID `12796`, queue depth `2`, latest
  `signal_scan` at `2026-05-30T10:11:30.427463Z`, latest
  `ws_orderbook_top` at `2026-05-30T10:11:30.426401Z`, task
  `\KalshiBTC_btc15m_lowdd_forward_shadow` status `Running`, and replay rows
  `4,879,999` top-book, `2,580,080` signal-scan, and `5,722`
  order-decision rows. The raw task still showed `Ready` while the writer PID
  was alive, so do not manually restart raw capture while PID `6084` is still
  writing.
- Materialized fresh bounded sidecar slices on the laptop using the repo
  `.venv` because system Python lacked DuckDB:
  `2026-05-30T06:40:00Z..2026-05-30T10:12:00Z`.
  Local ignored copies:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_1012\btc15m_raw_20260530_0640_1012.duckdb`
  and
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_1012\btc15m_lowdd_20260530_0640_1012.duckdb`.
- Raw materialized counts:
  `242,963` top-book rows, `1,172` lifecycle rows, `229,577` synthetic
  Coinbase rows, `15` BTC15M markets. Lowdd materialized counts:
  `240,936` top-book rows, `1,187` lifecycle rows, `115,773` signal-scan
  rows, and `6` paper-fill order-decision rows.
- Data fidelity artifact:
  `backtest_outputs\btc15m_raw_sidecar_fidelity_20260530_0640_1012`.
  Verdict remained research-only:
  `promotion_grade_coinbase_ticks=false` because Coinbase ticks are synthetic
  from top-book `btc_spot`; `hard_failures=top_book_gaps_over_threshold`.
  Valid-book rate was `99.5407%` with `1,116` filterable bad top-book rows.
  Raw-vs-lowdd independent bucket parity was usable as a diagnostic:
  `main_match_rate=97.2198%`, `compare_match_rate=95.8243%`,
  YES-mid mean absolute difference `0.0589c`, p95 `0.10c`, max `9.0c`,
  within-1c rate `98.3075%`.
- Generic official live-holdout replay artifact:
  `backtest_outputs\btc15m_live_holdout_20260530_0640_1012`.
  All `15` BTC15M events in the window had finalized Kalshi settlements.
  `current_lowdd_no_rv` remained positive with `6` trades, `+$1.49`
  one-contract PnL, `42.4501%` return on premium, `83.3333%` win rate,
  and max drawdown `-$0.25`. `cheap_yes_rr_first` was diagnostic-positive:
  `11` trades, `+$1.019`, `25.5966%` return on premium, but only
  `45.4545%` win rate. `cheap_pair_lock_rr` was `6` trades, `+$0.24`,
  `4.1667%` return on premium, `100%` win rate, but it remains a diagnostic
  rule without the lowdd wrapper's paper/order parity audit.
- Lowdd sidecar-selected artifact:
  `backtest_outputs\btc15m_lowdd_sidecar_selected_20260530_0640_1012`.
  No new paper fills arrived after the 09:40Z fill: still `6` selected rows,
  all settled, signal one-contract PnL `+$1.58`, scaled order PnL `+$14.27`,
  premium `$27.73`, win rate `83.3333%`, max drawdown `-$0.97`, and one
  selected-to-order price reprice row.
- Paper ledger artifact:
  `backtest_outputs\btc15m_lowdd_postrestart_20260530_0640_1012`.
  Same `6` official-settled paper trades, `5` wins, `+$14.27` official PnL,
  `$27.73` premium, `51.4605%` return on premium, max drawdown `-$0.97`.
- Parity artifact:
  `backtest_outputs\btc15m_lowdd_paper_replay_parity_20260530_0640_1012`.
  Result stayed clean after the reprice-classification patch:
  `6 / 6` live sidecar parity pass rows,
  `live_sidecar_price_mismatch_rows = 0`,
  `signal_order_reprice_rows = 1`,
  `signal_order_reprice_over_limit_rows = 0`,
  `max_signal_order_worse_reprice_cents = 1.0`, and
  `generic_replay_price_mismatch_rows = 2`.
- Promotion gate artifact:
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_0640_1012`.
  Verdict:
  `production_ready=false`,
  `research_status=research_promising_insufficient_forward_sample`.
  Standalone blockers are only sample-size gates:
  `selected_rows_below_min`, `settled_rows_below_min`,
  `order_rows_below_min`, and `paper_settled_rows_below_min`.
- Patched `scripts\check_btc_deployment_readiness.py` so the active lowdd
  forward gate is included in the global deployment-readiness table. Validation:
  `python -m py_compile scripts\check_btc_deployment_readiness.py` passed.
  Fresh readiness artifact:
  `backtest_outputs\deployment_readiness_20260530_1012_lowdd_gate`.
  Global verdict remains `production_ready_count = 0`.
- Branch applicability check:
  after `git fetch`, no non-`sami` branch had commits newer than May 13, 2026.
  The recent non-`sami` branches are all `kalshi_v2` / arb branches that expect
  cumulative strike markets (`-T` tickers), `ws_orderbook_top_dedup`,
  `coinbase_ticker_all`, and lifecycle `determined` rows. The fresh BTC15M
  slice has `15` `KXBTC15M` binary up/down markets, `0` `-T` markets,
  `0` `KXBTCD` markets, `0` lifecycle `determined` rows, and `4`
  lifecycle `settled` rows. Those branches are not directly replayable on this
  BTC15M slice without an adapter and an official-settlement path; the current
  BTC15M experiments remain the valid branch-current comparison for this data.
- Interpretation:
  lowdd remains the best active paper-forward candidate, but still has only
  `6` settled post-fix rows. The data is suitable for research-grade top-book
  replay after filters, but not promotion-grade BTC tick-age evidence because
  Coinbase ticks are synthetic in the materialized sidecar. Continue collecting
  lowdd and raw BTC15M; the next useful check is another bounded refresh once
  new paper fills arrive, plus a separate adapter experiment if we want to test
  `kalshi_v2` branches against BTC15M binary markets.

### 2026-05-30 - Machine-readable branch replayability inventory

- Added `scripts\audit_btc_branch_replayability.py` to make branch coverage
  auditable instead of narrative-only. The script:
  - lists remote branches by commit date;
  - inspects relevant branch files from Git without checking them out;
  - detects current BTC15M replay tools, `kalshi_v2` model/harness branches,
    and alternate arbitrage/backtester branches;
  - inspects the chosen DuckDB capture schema and market/lifecycle contents;
  - writes CSV, JSON, and Markdown artifacts explaining whether each branch is
    directly replayable on the capture or needs an adapter/harness.
- Validation:
  `python -m py_compile scripts\audit_btc_branch_replayability.py` passed.
  Initial execution found and fixed a Windows Git-output decode issue by
  forcing UTF-8 with replacement in the subprocess wrapper. A second run also
  corrected the classification so `kalshi_v2` branches without a captured
  DuckDB backtest harness are not mislabeled as directly replayable.
- Replayability artifact:
  `backtest_outputs\btc_branch_replayability_20260530_1012`.
  Input capture:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_1012\btc15m_raw_20260530_0640_1012.duckdb`.
  Capture market/lifecycle facts:
  `15` total markets, `15` BTC15M binary markets, `0` cumulative `-T`
  markets, `0` lifecycle `determined` rows, and `4` lifecycle `settled` rows.
- Branch inventory result across `19` remote branches:
  - `1` directly replayable current BTC15M branch:
    `origin/sami`.
  - `3` `kalshi_v2` branches with a captured-DuckDB backtest harness but not
    directly replayable on this BTC15M slice:
    `origin/claude/v2-backtest-harness`, `origin/arb-v3`, and
    `origin/claude/v2-sami-strategy-backtest`. Blockers were
    `missing_ws_orderbook_top_dedup`, `missing_coinbase_ticker_all`,
    `missing_ws_lifecycle_all`, `no_cumulative_T_markets`, and
    `no_determined_lifecycle_rows`.
  - `14` `kalshi_v2` model/strategy branches without a branch-local captured
    DuckDB backtest harness, classified as
    `not_directly_replayable_needs_harness`.
  - `1` GNN/arbitrage branch classified as `not_btc_capture_replay_ready`
    because no BTC15M captured-DuckDB replay contract was detected.
- Interpretation:
  for the currently collected BTC15M binary DuckDB slice, `origin/sami` is the
  only branch that can be honestly replayed directly. The `kalshi_v2` branch
  family may still contain useful modeling ideas, but testing it on this data
  requires an explicit adapter: map current `ws_orderbook_top` /
  `coinbase_ticker` / `ws_lifecycle` to the old `_dedup` / `_all` names,
  replace lifecycle `determined` parsing with official REST settlement or
  settled-event parsing, and redesign the market model for BTC15M up/down
  binaries rather than cumulative strike (`-T`) markets.

### 2026-05-30 - BTC15M diagnostic candidate quality and robustness refresh

- Tightened `scripts\audit_btc15m_replay_candidate_robustness.py` so candidate
  summaries now include row-quality gates before any "promising" verdict:
  official result rows, executable quote rows, non-executable quote rows,
  duplicate event rows, observed spread/visible quantity, and
  `row_quality_blockers`.
- Row-quality semantics:
  a candidate is rejected before bootstrap if it lacks official settlement,
  has duplicate event exposure, lacks executable quote fields, has visible
  quantity below `1`, has entry outside `[1c, 99c]`, or has spread above
  `3.0c`. The spread/entry comparisons include a tiny float tolerance so exact
  `3.0c` and `99c` rows are not rejected due to representation noise.
- Validation:
  `python -m pytest scripts\test_btc15m_replay_candidate_robustness.py -q --basetemp .pytest-codex-tmp-btc15m-robustness-quality`
  passed with `6` tests. `python -m py_compile
  scripts\audit_btc15m_replay_candidate_robustness.py
  scripts\test_btc15m_replay_candidate_robustness.py` also passed before the
  final report-text-only tweak; the post-tweak test rerun passed again.
- Built a rolling window grid for the latest materialized raw capture:
  `backtest_outputs\btc15m_live_holdout_window_grid_20260530_0640_1012`.
  Input:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_1012\btc15m_raw_20260530_0640_1012.duckdb`,
  window `2026-05-30T06:40:00Z..2026-05-30T10:12:00Z`,
  `1h` windows, `1h` step. This produced `4` windows. Because each window is
  scored independently, boundary-sensitive features can make the aggregate
  trade count differ slightly from the whole-window replay; use it as
  stability evidence, not as the canonical PnL ledger.
- Fresh strict robustness artifact:
  `backtest_outputs\btc15m_replay_candidate_robustness_20260530_1012_quality`.
  All replay rows had official settlement, executable quote rows, and no
  duplicate event exposure under the `<=3c` spread / `>=1` visible quantity
  gate.
- Results:
  - `current_lowdd_no_rv`: `6` trades, official replay PnL `+$1.49`,
    return on premium `42.4501%`, win rate `83.3333%`, max drawdown `-$0.25`,
    bootstrap p05 `+$0.35`, bootstrap profit probability `99.16%`,
    `3 / 4` positive rolling windows, verdict
    `research_promising_insufficient_sample`.
  - `cheap_yes_rr_first`: `11` trades, PnL `+$1.019`, return on premium
    `25.5966%`, win rate `45.4545%`, max drawdown `-$1.10`, bootstrap p05
    `-$1.64905`, bootstrap profit probability `73.36%`, `2 / 4` positive
    windows, verdict
    `research_promising_insufficient_sample_bootstrap_fragile`.
  - `cheap_pair_lock_rr`: `6` rows, `+$0.24`, `100%` win rate, bootstrap p05
    `+$0.08`, but verdict remains
    `excluded_by_prior_selection_bias_audit`.
  - `cheap_tail_best_side_first`, `cheap_tail_position_aware`, and
    `cheap_no_rr_first` were official-PnL negative and rejected.
- Interpretation:
  this stricter pass removes `cheap_yes_rr_first` from near-term forward-test
  consideration despite positive headline PnL; it is sample-small, bootstrap
  fragile, and window-unstable. Lowdd remains the only quality-clean positive
  BTC15M replay candidate in the latest slice, but it is still not deployable
  because its official/paper-forward sample is only `6` rows.

### 2026-05-30 - BTC15M 10:41Z remote refresh, lowdd duplicate-selected fix

- Git/branch refresh:
  local `sami` fast-forward pulled cleanly and `git push` reported
  `Everything up-to-date`. Remote branch scan showed no newer non-`sami`
  strategy commits: `origin/sami` was latest at `2026-05-30 06:35:30 -0400`;
  the next most recent strategy branches were still from `2026-05-13`.
- Remote collector status at approximately `2026-05-30T10:40:34Z`:
  raw `btc15m_live_capture` sidecar was alive with `failed=false`,
  `dropped=0`, PID `6084`, queue depth `6`, and latest
  `ws_orderbook_top=2026-05-30T10:40:32.390930Z`.
  Lowdd forward shadow was alive with `failed=false`, `dropped=0`, PID
  `12796`, queue depth `19`, latest
  `signal_scan=2026-05-30T10:40:32.947033Z`, and `20` rows in
  `research_live_trades`.
- Fresh bounded materialization:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_1041`.
  Window:
  `2026-05-30T06:40:00Z..2026-05-30T10:41:00Z`.
  Raw slice counts were `279,881` `ws_orderbook_top`, `1,339`
  `ws_lifecycle`, and `263,129` synthetic `coinbase_ticker` rows.
  Lowdd slice counts were `277,822` `ws_orderbook_top`, `135,043`
  `signal_scan`, `9` `order_decision`, and `262,405` synthetic
  `coinbase_ticker` rows.
- Data-fidelity artifact:
  `backtest_outputs\btc15m_raw_sidecar_fidelity_20260530_0640_1041`.
  Verdict stayed research-only: valid top-book rate was `99.5076%`, but
  `top_book_gaps_over_threshold` remained a hard failure and Coinbase ticks
  were synthetic from top-book `btc_spot`, so the slice is not
  promotion-grade BTC tick-age evidence.
- Fresh whole-window official replay:
  `backtest_outputs\btc15m_live_holdout_20260530_0640_1041`.
  All `17` events in the window were finalized by Kalshi REST. Results:
  `current_lowdd_no_rv` had `8` trades, PnL `+$1.74`, return on premium
  `33.0798%`, win rate `87.5%`, max drawdown `-$0.25`.
  `cheap_yes_rr_first` had `13` trades, PnL `+$0.999`, return on premium
  `19.9760%`, win rate `46.1538%`, max drawdown `-$1.10`.
  `cheap_pair_lock_rr` stayed positive at `+$0.30` with `8` rows, but remains
  excluded by the prior selection-bias audit. The other cheap-tail variants
  were official-PnL negative.
- Rolling-window robustness:
  `backtest_outputs\btc15m_live_holdout_window_grid_20260530_0640_1041` and
  `backtest_outputs\btc15m_replay_candidate_robustness_20260530_1041_quality`.
  `current_lowdd_no_rv` remained quality-clean with `8` official-settled,
  executable rows, no duplicate event exposure, bootstrap p05 `+$0.71`,
  bootstrap profit probability `99.62%`, and `4 / 5` nonnegative windows
  (`4` positive, `1` flat). Verdict:
  `research_promising_insufficient_sample`.
  `cheap_yes_rr_first` stayed positive on the whole-window ledger but was still
  bootstrap/window fragile: bootstrap p05 `-$1.86205`, profit probability
  `71.16%`, and `2 / 5` positive windows. Verdict:
  `research_promising_insufficient_sample_bootstrap_fragile`.
- Lowdd sidecar/paper evidence:
  `backtest_outputs\btc15m_lowdd_sidecar_selected_20260530_0640_1041`,
  `backtest_outputs\btc15m_lowdd_postrestart_20260530_0640_1041`,
  `backtest_outputs\btc15m_lowdd_paper_replay_parity_20260530_0640_1041`, and
  `backtest_outputs\btc15m_lowdd_forward_promotion_gate_20260530_0640_1041`.
  The raw lowdd sidecar contained `9` selected rows, but one was a duplicate
  selected scan for event `KXBTC15M-26MAY300630`. The selected-sidecar replay
  now dedupes repeated selected scans by event and writes
  `duplicate_selected_signals.csv`; evidence rows are `8` selected, `8`
  settled, `8` order rows, and `8` paper rows. Sidecar order-scaled official
  PnL exactly matched the paper official ledger after dedupe:
  `+$20.43` on `$42.57` premium, `87.5%` win rate, max drawdown `-$0.97`.
  Live sidecar parity passed for all `8` paper rows; selected-to-order reprice
  rows were `2`, with `0` over the configured limit and max worse reprice
  `1.0c`.
- Lowdd promotion gate:
  production-ready remained `false`. Blockers were only sample-size gates:
  `selected_rows_below_min`, `settled_rows_below_min`,
  `order_rows_below_min`, and `paper_settled_rows_below_min`. Advisories:
  duplicate selected signals were deduped, generic replay price mismatches are
  advisory because live sidecar parity is authoritative, and selected-to-order
  reprices were within config.
- Branch replayability refresh:
  `backtest_outputs\btc_branch_replayability_20260530_1041`. Results were
  unchanged across `19` remote branches: `1` directly replayable current BTC15M
  branch (`origin/sami`), `3` branches needing an adapter, `14` needing a
  harness, and `1` not BTC capture replay-ready. Fresh capture facts:
  `17` BTC15M binary markets, `0` cumulative `-T` markets, `1`
  lifecycle `determined` row, and `5` lifecycle `settled` rows.
- Readiness:
  `backtest_outputs\deployment_readiness_20260530_1041_lowdd_gate` still had
  `production_ready_count=0`. The standalone lowdd gate is promising but
  sample-small; broad global readiness still includes conservative historical
  blockers from older verifier artifacts.
- Validation:
  `python -m pytest scripts\test_btc15m_lowdd_sidecar_selected.py
  scripts\test_btc15m_lowdd_forward_promotion_gate.py -q --basetemp
  .pytest-codex-tmp-lowdd-dedupe-gate` passed with `10` tests.
  `python -m py_compile scripts\backtest_btc15m_lowdd_sidecar_selected.py
  scripts\build_btc15m_lowdd_forward_promotion_gate.py
  scripts\test_btc15m_lowdd_sidecar_selected.py
  scripts\test_btc15m_lowdd_forward_promotion_gate.py` passed.

### 2026-05-30 - BTC15M kalshi_v2 adapter feasibility audit

- Added `scripts\audit_btc15m_kalshi_v2_adapter_feasibility.py` to move the
  branch audit beyond "needs adapter" and answer whether the `kalshi_v2`
  branch family can be adapted scientifically to the current BTC15M binary
  websocket capture.
- The audit separates:
  - direct branch backtest feasibility;
  - table-alias feasibility for current capture tables;
  - REST metadata coverage for mapping BTC15M binary markets to floor strikes
    and official outcomes;
  - hold-to-settlement research-adapter feasibility; and
  - original v2 TP/SL/time-exit deployability risk.
- Artifact:
  `backtest_outputs\btc15m_kalshi_v2_adapter_feasibility_20260530_1041`.
  Input capture:
  `runtime\remote_snapshots\sidecar_slices_20260530_0640_1041\btc15m_raw_20260530_0640_1041.duckdb`.
- Capture/REST preflight:
  the slice has `17` BTC15M binary markets, `0` cumulative `-T` markets, and
  synthetic/provenance-weak Coinbase ticks (`coinbase_raw_book_field_rate=0`).
  Kalshi REST returned `17 / 17` captured markets matched, `17 / 17`
  floor-strike rows, and `17 / 17` official result rows.
- Branch results:
  all `17` `kalshi_v2` refs were classified
  `research_adapter_feasible_not_original_v2_deployable`.
  For the `3` branches with `kalshi_v2/backtest.py`
  (`origin/claude/v2-backtest-harness`, `origin/arb-v3`, and
  `origin/claude/v2-sami-strategy-backtest`), direct execution is blocked by
  missing `_dedup`/`_all` table names and, more importantly, by the absence of
  cumulative `-T` markets because the branch backtest filters/parses `-T`
  tickers. The other `kalshi_v2` refs still need a captured-DuckDB harness.
- Scientific interpretation:
  a branch-original replay is not honest on the current BTC15M slice. A new
  preregistered research adapter is feasible: map each BTC15M binary market to
  its REST `floor_strike`, use decision-time `ws_orderbook_top` rows, use
  official REST settlement, and hold to settlement first. TP/SL/time-exit logic
  from v2 remains excluded from deployment evidence until live exit-fill
  behavior is validated independently.
- Validation:
  `python -m pytest scripts\test_btc15m_kalshi_v2_adapter_feasibility.py -q
  --basetemp .pytest-codex-tmp-v2-adapter-2` passed with `3` tests.
  `python -m py_compile
  scripts\audit_btc15m_kalshi_v2_adapter_feasibility.py
  scripts\test_btc15m_kalshi_v2_adapter_feasibility.py` passed.
