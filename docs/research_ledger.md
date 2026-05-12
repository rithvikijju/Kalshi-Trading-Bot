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
