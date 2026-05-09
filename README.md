# Kalshi-Trading-Bot

This repo has three older research projects plus the current BTC/Kalshi hourly
trading work. The active production/research surface is the BTC 1-hour KXBTCD
strategy family.

## Setup

```powershell
pip install -r requirements.txt
```

Keep credentials out of commits. Live Kalshi scripts read `credentials.env`
from the repo root today; do not expose that file or any PEM key.

## Project Map

| Area | Path | Status |
|---|---|---|
| BTC/Kalshi hourly trading | `scripts/btc_1hr_research_live.py`, `scripts/btc_1hr_risk_adjusted_research_live.py`, `scripts/may8examine.py` | Active |
| BTC/Kalshi historical replay | `scripts/backtest_research_duckdb.py`, `scripts/backtest_risk_adjusted_research.py` | Active research |
| Multi-crypto Kalshi scout | `scripts/build_crypto_research_datamart.py`, `scripts/backtest_crypto_research.py` | Research only |
| GNN triangular arbitrage | `gnn_arbitrage/`, `scripts/run_gnn_arbitrage.py` | Separate experiment |
| GA/DMLP paper replica | `ga_dmlp/`, `scripts/run_ga_dmlp.py` | Separate experiment |
| Neufeld/Sester arbitrage | `neufeld_arb/`, `scripts/run_neufeld_*` | Separate experiment |

## BTC/Kalshi Current State

Current live-money executor:

```powershell
python scripts\btc_1hr_risk_adjusted_research_live.py
```

Current shadow executor:

```powershell
python scripts\btc_1hr_market_shrink_no_cautious_shadow.py
```

Multi-strategy paper shadow:

```powershell
python scripts\btc_1hr_multi_strategy_shadow.py
```

Default strategies are `research`, `market_shrink_no_cautious`,
`market_shrink_no_cautious_shape_adjacent`, and `js_guarded`. It uses one shared
Kalshi/Kraken websocket stream and separate SQLite ledgers per strategy under
`~/.btc_kalshi_bot/multi_strategy_shadow/`.

Watch logs:

```powershell
$log = (Get-ChildItem logs\risk_adjusted_research*.out.log,logs\research_live_*.out.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName; Get-Content $log -Wait -Tail 100
$log = (Get-ChildItem logs\market_shrink_no_cautious_shadow_*.out.log,logs\btc_1hr_market_shrink_no_cautious_shadow_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName; Get-Content $log -Wait -Tail 100
```

Check processes:

```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" | Where-Object { $_.CommandLine -like '*btc_1hr_risk_adjusted_research_live.py*' -or $_.CommandLine -like '*btc_1hr_market_shrink_no_cautious_shadow.py*' } | Select-Object ProcessId,CommandLine
```

Current model:

| Component | Detail |
|---|---|
| Market universe | Current-hour KXBTCD cumulative `-T` BTC above/below markets only. Wrong event hours, bucket/range markets, stale events, and next-day markets are rejected. |
| Fair value | Causal 7-day empirical BTC move cache frozen at event open, BRTI dampening `0.80`, blended `70%` empirical / `30%` lognormal. |
| Live spot | Kraken websocket is the preferred live tick feed; Coinbase candle/cache code remains for history/fallback. |
| Kalshi price | Real orderbook state. YES ask and NO ask come from the book; do not assume `NO = 1 - YES` except when reconstructing the opposite ask from the opposite bid as the executor does. |
| Entry filter | Taker-fee-aware net edge, max spread `2c`, entry `0.25..0.75`, strong side probability (`YES >= 0.65`, `NO <= 0.35`). |
| Execution | Kalshi V2 FOK event orders, pre-inserted local order row, event lock, balance/position checks, visible-depth checks, and 409/FOK no-fill reconciliation. |
| Live sizing | `scripts/risk_adjusted_research.py`: conservative probability, fractional Kelly, bankroll/exposure caps, visible-depth caps, and price-band caps. It can use up to 3 contracts but is not forced to. |

## Data Trust

| Source | Path | Trust / Use |
|---|---|---|
| Historical bid/ask DuckDB | `data/research_datamart/research.duckdb` | Best historical source. KXBTCD hourly bid/ask minute candles, no forward fill. Local only because it is too large for GitHub. |
| Compact DuckDB | `data/research_datamart/research_backtest.duckdb` | Commit-safe reproducibility source; slightly smaller/older than the full local DB. |
| Live trade ledger | `~/.btc_kalshi_bot/research_live_trades.db` | Source of truth for actual live attempts/fills. |
| Shadow trade ledger | `~/.btc_kalshi_bot/market_shrink_no_cautious_shadow_trades.db` | Paper fills for the current market-shrink shadow; use official Kalshi results for audit, not only bot-reported proxy PnL. |
| Live websocket capture | `~/.btc_kalshi_bot/research_live_capture.duckdb` | Best future replay source, but locked while the bot runs. Snapshot/checkpoint before analysis. |
| Raw CSV exports | `data/kalshi-price-history-kxbtcd-*.csv` | Reference only. These are chart/export prices with assumed spread, not executable bid/ask. |
| Backtest outputs | `backtest_outputs/` | Derived artifacts. Useful for provenance, not source data. |

Live-capture replays must follow [docs/live_capture_backtest_rules.md](docs/live_capture_backtest_rules.md).

Known fidelity limits:

| Issue | Impact |
|---|---|
| Historical DuckDB uses 1-minute bid/ask candles | Better than CSV, but still not second-level live orderbook replay. |
| Historical settlement mostly uses BTC minute-close proxy | Official Kalshi `expiration_value` is not stored for every old historical row. Kalshi crypto settlement uses the CF Benchmarks RTI simple average over the 60 seconds before expiry, so Coinbase/Kraken minute close is only a proxy. |
| Live capture DB is locked during trading | Do not stop live bots just to inspect it unless intentionally pausing trading. |
| CSV backtests look much stronger | Treat CSV numbers as diagnostics only. They repeatedly overstate edge. |

Settlement/index notes:

| Contract | Official underlying | Research implication |
|---|---|---|
| BTC hourly/15m | CF Bitcoin Real-Time Index (`BRTI`) 60-second pre-expiry average | Coinbase/Kraken are fast live spot feeds, not final settlement truth. Near-strike trades need an index-basis guard. |
| ETH hourly/15m | CF Ether-Dollar Real-Time Index (`ETHUSD_RTI` / ERTI) 60-second pre-expiry average | ETH work must use ETH-specific basis and volatility scaling; do not port BTC thresholds blindly. |

## Strategy Scorecard

Decision-grade historical replay uses `data/research_datamart/research.duckdb`
with taker-fee estimates and chronological train/validation/test splits:
train `< 2026-04-01`, validation `2026-04-01` to `2026-04-21`, test
`>= 2026-04-21`.

Canonical corrected bid/ask results:

| Strategy | Trades | PnL | Premium | Return on Premium | Win Rate | Max DD | Read |
|---|---:|---:|---:|---:|---:|---:|---|
| `paper` | 2,532 | -83.39 | 1,362.39 | -6.12% | 50.51% | -84.78 | Reject |
| `research` / current signal | 173 | +6.25 | 118.75 | +5.26% | 72.25% | -5.93 | Modest edge, validation was negative |
| `js_robust` | 160 | +5.79 | 114.21 | +5.07% | 75.00% | -4.40 | Similar to research |
| `js_guarded` | 120 | +7.56 | 85.44 | +8.85% | 77.50% | -1.98 | Best old replay, still paper-only |

Risk sizing overlay on the same 173 `research` signals with `$20` start:

| Policy | Contracts | PnL | Ending Bankroll | Return on Start | Max DD | Read |
|---|---:|---:|---:|---:|---:|---|
| `fixed_1` | 173 | +6.25 | 26.25 | +31.25% | -5.93 | Base signal |
| `flat_3` | 519 | +20.31 | 40.31 | +101.55% | -17.58 | Higher return, too much drawdown for small bankroll |
| `risk_adjusted` | 206 | +9.81 | 29.81 | +49.05% | -7.80 | Current live sizing idea |

## May 8 Research Sprint

New replay script:

```powershell
python scripts\may8examine.py --output-dir backtest_outputs\research_sprint\may8_selected_parallel\test --start 2026-04-21T00:00:00Z --variant js_time_guard --variant market_shrink_no_cautious
```

The sprint tested fair-value blends, BRTI dampening, market-mid shrinkage,
YES-only/NO-cautious side filters, live-loss guards, and JS-style time guards.
Cross-hour/intermarket/exit overlays were reviewed separately; none produced a
return-improving train/validation/test result.

Best faithful historical candidates:

| Variant | Train PnL | Val PnL | Test PnL | Overall PnL | Overall Return on Premium | Trades | Read |
|---|---:|---:|---:|---:|---:|---:|---|
| `js_time_guard` | +1.76 | +2.23 | +3.91 | +7.90 | +8.97% | 123 | Strongest split-consistent result; essentially the guarded JS family. Keep paper-shadowing, do not live-promote until official/live-capture audit is fixed. |
| `market_shrink_no_cautious` | +1.01 | +1.11 | +1.09 | +3.21 | +5.86% | 77 | Best new conservative candidate. It directly reduces the NO-side problem and passed all splits. |
| `market_shrink_25` | +0.09 | +1.14 | +1.12 | +2.35 | +18.58% | 20 | Clean but too sparse to deploy. |
| `baseline_current` | +4.49 | -0.65 | +2.41 | +6.25 | +5.26% | 173 | Current signal still viable but validation and live fills show risk. |

Rejected or not ready:

| Variant | Reason |
|---|---|
| `brti_dampen_60` | Overtrades and loses overall: -1.73, -0.53% return on premium. |
| `yes_only` | Live YES has been good, but historical validation failed and overall PnL was slightly negative. |
| `no_cautious` / `live_loss_guard` | Good train/test, failed validation. |
| `blend_balanced_50_50` | Good train/test, failed validation. |
| Cross-hour reset/overlay | Executable validation failed. |
| Intermarket overlap | Slight train log-loss improvement, worse validation. |
| Routine TP/SL/trailing exits | Underperformed hold. |
| Model emergency exits | Sometimes reduce drawdown, but reduced held-out test return. Keep as research only. |

Lower-trust CSV diagnostic with assumed `2c` spread:

| Variant | Train PnL | Val PnL | Test PnL | Read |
|---|---:|---:|---:|---|
| `market_shrink_no_cautious` | +118.29 | +105.55 | +65.89 | Confirms it is not obviously broken, but CSV overstates edge. |
| `js_time_guard` | +66.25 | +49.22 | +25.58 | Same caveat. Do not deploy from CSV evidence. |

Outputs from this sprint:

| Path | Contents |
|---|---|
| `backtest_outputs/research_sprint/strategy_variant_scorecard.csv` | Ranked historical DuckDB scorecard. |
| `backtest_outputs/research_sprint/may8_selected_parallel/` | Train/validation/test split replays for selected variants. |
| `backtest_outputs/research_sprint/may8_candidates_csv_2c/` | Low-trust CSV diagnostic for top candidates. |
| `backtest_outputs/research_sprint/live_research_trade_analysis.csv` | Official-result live fill audit. |
| `backtest_outputs/research_sprint/shadow_js_guarded_trade_analysis.csv` | Official-result JS shadow audit. |
| `backtest_outputs/research_sprint/calibration_*.csv` | Probability/entry/side calibration diagnostics. |

## May 9 MCTS / Literature Sprint

Subagents reviewed orderbook microstructure, crypto jump/volatility, cross-market
lead-lag, Kelly/risk sizing, and MCTS search design. The useful conclusion was
not a black-box model; it was a set of stricter, interpretable guards:

| Branch | Read |
|---|---|
| Jump/exhaustion guards | Target high-entry reversal losses after sharp BTC moves. Most variants were too sparse or failed validation. |
| Low-risk sizing | For `$20-$100` bankrolls, cap forward-test shadows at 1 contract until live evidence justifies more. |
| Shape/orderbook guards | Cross-strike monotonicity and adjacent-strike confirmation are testable on corrected historical DuckDB. |
| Microprice/OFI | Promising but live-capture-only; historical DuckDB lacks queue/depth pressure. |
| MCTS search | Blind large MCTS was too slow and generated sparse/no-trade candidates. Use pre-registered branches plus smaller recoverable MCTS batches. |

Decision-grade pre-registered replay, same corrected DuckDB train/validation/test
split and fee model:

| Variant | Train PnL | Val PnL | Test PnL | Trades | Read |
|---|---:|---:|---:|---:|---|
| `js_time_guard` | +1.76 | +2.23 | +3.91 | 123 | Still strongest historical replay, but shadow/live risk reporting remains a blocker. |
| `market_shrink_no_cautious_shape_adjacent` | +0.47 | +1.21 | +1.66 | 30 | Best new branch. Sparse, but passed all splits with low drawdown. Paper-only candidate. |
| `market_shrink_no_cautious` | +1.01 | +1.11 | +1.09 | 77 | Current market-shrink shadow baseline. More trades, less strict. |
| `jump_guard_moderate` | +0.42 | +0.21 | +1.22 | 17 | Positive but too sparse. |
| `market_shrink_no_cautious_shape_loose` | -0.03 | +0.76 | +1.66 | 33 | Failed train by a small amount. |
| `lowrisk_no_extra` | -0.59 | -0.07 | -0.36 | 66 | Reject. |
| `lowrisk_high_entry_surcharge` | -2.82 | -2.12 | +1.00 | 25 | Reject. |

Outputs:

| Path | Contents |
|---|---|
| `backtest_outputs/mcts_research_sprint/combined_scorecard.csv` | Combined train/validation/test scorecard for the pre-registered branches. |
| `backtest_outputs/mcts_strategy_research_core_20260509/mcts_train_summary.partial.csv` | Exploratory MCTS train-only partial; not decision-grade. |

Do not promote `shape_adjacent` directly to live. The right next step is a
paper shadow with 1-contract max and exact live-capture reconciliation. The
highest-priority research extension is `shape_adjacent_plus_basis`: keep the
cross-strike shape/adjacent confirmation, then add a causal guard for official
RTI settlement basis near the strike.

## Live Trade Audit

Official Kalshi-result audit through the May 8 run:

| Ledger | Fills | Wins | Losses | PnL | Premium | Return on Premium | Max DD | Read |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Live research | 28 | 18 | 10 | -2.56 | 32.56 | -7.86% | -3.62 | Good hit rate, bad payoff distribution. |
| JS shadow | 23 | 16 | 7 | -10.95 | 772.95 | -1.42% | -91.86 | Sizing dominates risk; do not trust bot proxy PnL without official settlement. |

Live research by side:

| Side | Fills | PnL | Return on Premium | Win Rate | Read |
|---|---:|---:|---:|---:|---|
| YES | 6 | +1.88 | +20.62% | 83.33% | Small sample, currently good. |
| NO | 22 | -4.44 | -18.94% | 59.09% | Main loss source. NO after sharp moves/reversals is the failure mode. |

Important execution finding: the 20 live `not_filled` FOK attempts would have
lost about `-$6.33` if forced through at logged entry prices. FOK rejection has
been economically useful; do not replace it with blind retries.

Shadow issue: the JS shadow log reported worse PnL than the official-result
audit because at least one settled market was marked differently by the BTC
proxy. Shadow PnL must be reconciled to official Kalshi results before using it
as a promotion gate.

## Current Recommendation

Do not change live production logic from this sprint alone.

Current live should remain `btc_1hr_risk_adjusted_research_live.py` while we
collect more exact live capture and official-result fills. The current paper
shadow is `market_shrink_no_cautious`; the best new May 9 research branch is
`market_shrink_no_cautious_shape_adjacent`, but it is sparse and needs its own
paper shadow plus live-capture replay before any live promotion.

Priority next steps:

1. Fix/reconcile shadow reporting to official Kalshi settlement.
2. Forward-test `market_shrink_no_cautious` as a paper-only shadow, not live.
3. Continue collecting live websocket capture; use snapshots for exact replay.
4. Only promote a strategy after it passes historical DuckDB, official-result
   live paper, and exact live-capture replay.

## Multi-Crypto Scout

The multi-crypto datamart exists at `data/crypto_research_datamart/`. The
primary 24-hour scout covered `KXBTC15M`, `KXETH15M`, and `KXETHD`. ETH 1-hour
looked most interesting, but trade counts were too small. BTC/ETH 15-minute
up/down did not produce a deployment-grade edge after fees. Treat all
multi-crypto results as research only until multi-week bid/ask data is built.

## Historical Notes

Old polling scripts (`btc_1hr_paper.py`, `btc_1hr_hardened.py`,
`btc_1hr_neohardened.py`, `btc_1hr_live.py`) are useful for archaeology but are
not the current production path. The old CSV-based March/April reports are
retained in `backtest_outputs/`, but corrected DuckDB bid/ask replay supersedes
them for decisions.
