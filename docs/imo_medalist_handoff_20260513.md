# Kalshi BTC Quant Research Handoff for IMO Medalist

Generated: 2026-05-13 late evening MDT / 2026-05-14 UTC.

This is a self-contained research handoff for a mathematically strong collaborator.
The goal is not to defend our current bot. The goal is to determine whether
there is a real, execution-surviving edge in short-horizon Kalshi BTC markets,
and if so to formalize the probability model and sizing rule rigorously enough
that one bad trade cannot erase a cluster of wins.

Do not share `credentials.env`, PEM/private keys, API keys, or any file that
contains secrets. Trade ledgers and capture DBs contain order IDs and client
order IDs but not credentials.

## Executive Summary

We trade Kalshi crypto binary contracts, mainly:

- `KXBTCD`: BTC 1-hour above/below contracts.
- `KXBTC15M`: BTC 15-minute up/down contracts.

The current urgent problem is the `KXBTC15M` bot. It looked good on a small
websocket replay, was scaled with target-win sizing, and then lost heavily live.

The failure mode is now clear:

- The live 15-minute bot was sizing trades to win roughly `$3` net.
- At high entry prices such as `0.72` to `0.77`, that means risking about
  `$9` to `$11` to make about `$3`.
- A few high-entry losses erased many normal wins.
- The bot's `net_edge_cents` field is not calibrated expected value. It is
  mostly a market-move magnitude proxy. Do not treat it as true EV.

Recent BTC15M live ledger, settled with official Kalshi results:

| Window | Settled Fills | W/L | PnL | Premium |
|---|---:|---:|---:|---:|
| Full BTC15M live ledger | 44 | 28 / 16 | `-$29.21` | `$109.21` |
| Since scaled restart `2026-05-13T20:31Z` | 13 | 5 / 8 | `-$27.35` | `$70.35` |
| Same scaled window, only `entry <= 0.60` | 7 | 3 / 4 | `+$2.80` | `$13.20` |

The `entry <= 0.60` result is not proof of edge. It is a diagnostic showing
that high-price target-win sizing caused most of the damage.

## What We Need From You

Please attack this as a probability and risk-control problem.

Do not start by tuning thresholds. Start by formalizing:

1. What is the fair probability of settlement for each contract, conditional on
   the information available at decision time?
2. What uncertainty penalty should be applied to that fair probability?
3. What trade size is rational after fees, fill uncertainty, and model error?
4. Which apparent edges disappear when evaluated on the live websocket replay
   rather than historical candle data?

Your job is to kill weak edges, not rescue them.

## Market Mechanics

Each Kalshi binary contract settles at `$1.00` if the chosen side wins and
`$0.00` otherwise. A buy at entry price `q` has one-contract PnL:

```text
win:  +1 - q - fee(q)
lose: -q - fee(q)
EV:   p - q - fee(q)
```

So a long contract is positive EV only if:

```text
p_true > q + fee_per_contract
```

This simple inequality is the core test. Most of the current work has been
around estimating `p_true` and keeping the fill model faithful.

Official docs to check before changing execution assumptions:

- Kalshi fees: https://help.kalshi.com/en/articles/13823805-fees
- Kalshi Create Order V2 / FOK: https://docs.kalshi.com/api-reference/orders/create-order-v2
- Kalshi historical cutoff: https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps
- Kalshi historical candlesticks: https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks

Important details:

- We use FOK event orders through REST, not websocket order entry.
- Kalshi websockets provide market/order/fill updates, but documented order
  submission is REST unless we get FIX access.
- Do not assume `NO ask = 1 - YES ask`. Use the real book. In our event-order
  implementation, buying NO crosses the YES book on the opposite side.
- Crypto settlement is based on CF Benchmarks RTI/BRTI style index averages,
  not Coinbase/Kraken last trade. Coinbase/Kraken are live proxies.
- Near-strike outcomes are especially dangerous because the settlement index
  basis can differ from exchange spot feeds.

## Current Live BTC15M Bot

Script:

```text
scripts/btc15m_lowdd_live.py
```

Current running command:

```powershell
python scripts\btc15m_lowdd_live.py
```

Latest known process snapshot at generation time:

| Process | PID | Notes |
|---|---:|---|
| BTC15M bot | `21536` | Running `scripts\btc15m_lowdd_live.py` |
| Weather bot | `66624` | Separate bot. Do not kill it while working on BTC. |


Core BTC15M rule in plain English:

| Component | Current Rule |
|---|---|
| Series | `KXBTC15M`, current event only |
| Time-to-close | Trade only at `4-5` minutes to close |
| Spread | `<= 2c` |
| Entry | `0.05..0.80` |
| Risk/reward gate | `RR >= 0.33` |
| Kalshi signal | 2-minute YES-midpoint move at least `12.5c` in trade direction |
| BTC confirmation | 3-minute BTC return must not oppose the trade direction |
| Exhaustion guard | Absolute 3-minute YES-midpoint move must be `<= 35c` |
| Event throttle | First qualifying trade per event |
| Execution | Real top-of-book, hot-book reprice, visible quantity check, FOK |
| Current bad sizing | Target about `$3` net win, max `$15` premium, max `20` contracts |

The bad part is the sizing, not just the signal. Target-win sizing creates
convex loss exposure at high entries:

```text
entry = 0.75, target win ~= $3
contracts needed ~= 12
profit if right ~= +$3
loss if wrong ~= -$9 plus fees
```

That requires a very high calibrated win probability. The current model does
not justify that.

## Recent BTC15M Live Loss Audit

The high-entry losses that caused the drawdown:

| Local Time | Market | Side | Contracts | Entry | Result | PnL |
|---|---|---:|---:|---:|---:|---:|
| 2026-05-13 15:55 | `KXBTC15M-26MAY131800-00` | YES | 14 | 0.77 | NO | `-$10.96` |
| 2026-05-13 17:25 | `KXBTC15M-26MAY131930-30` | YES | 11 | 0.69 | NO | `-$7.76` |
| 2026-05-13 19:10 | `KXBTC15M-26MAY132115-15` | YES | 12 | 0.73 | NO | `-$8.93` |
| 2026-05-13 19:25 | `KXBTC15M-26MAY132130-30` | YES | 12 | 0.72 | NO | `-$8.81` |

Recent settled filled trades from the live ledger, latest tail:

| Local Time | Market | Side | Contracts | Entry | Result | PnL | Model p_yes | Net Edge Cents |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 05-13 15:25 | `KXBTC15M-26MAY131730-30` | NO | 15 | 0.78 | NO | `+3.11` | 0.105 | 22.73 |
| 05-13 15:55 | `KXBTC15M-26MAY131800-00` | YES | 14 | 0.77 | NO | `-10.96` | 0.780 | 12.21 |
| 05-13 16:10 | `KXBTC15M-26MAY131815-15` | YES | 4 | 0.15 | NO | `-0.64` | 0.180 | 11.65 |
| 05-13 16:40 | `KXBTC15M-26MAY131845-45` | YES | 5 | 0.34 | YES | `+3.22` | 0.365 | 13.40 |
| 05-13 16:55 | `KXBTC15M-26MAY131900-00` | NO | 5 | 0.27 | YES | `-1.42` | 0.660 | 18.10 |
| 05-13 17:10 | `KXBTC15M-26MAY131915-15` | YES | 5 | 0.36 | NO | `-1.89` | 0.385 | 13.20 |
| 05-13 17:25 | `KXBTC15M-26MAY131930-30` | YES | 11 | 0.69 | NO | `-7.76` | 0.775 | 19.45 |
| 05-13 17:55 | `KXBTC15M-26MAY132000-00` | YES | 7 | 0.55 | YES | `+3.02` | 0.750 | 33.14 |
| 05-13 18:25 | `KXBTC15M-26MAY132030-30` | YES | 6 | 0.45 | NO | `-2.81` | 0.630 | 28.67 |
| 05-13 19:10 | `KXBTC15M-26MAY132115-15` | YES | 12 | 0.73 | NO | `-8.93` | 0.740 | 11.08 |
| 05-13 19:25 | `KXBTC15M-26MAY132130-30` | YES | 12 | 0.72 | NO | `-8.81` | 0.930 | 31.08 |
| 05-13 20:40 | `KXBTC15M-26MAY132245-45` | NO | 12 | 0.72 | NO | `+3.19` | 0.255 | 12.58 |
| 05-13 21:25 | `KXBTC15M-26MAY132330-30` | YES | 4 | 0.16 | YES | `+3.32` | 0.170 | 12.45 |

Again: `model_p_yes` and `net_edge_cents` are not calibrated fair probability
or true expected value. They are ledger proxies.

## BTC 1-Hour Strategy Context

The 1-hour strategy is older and better studied than the 15-minute strategy.
Primary scripts:

```text
scripts/btc_1hr_research_live.py
scripts/btc_1hr_risk_adjusted_research_live.py
scripts/btc_1hr_late_only_loss_guard_live.py
scripts/may8examine.py
```

Current 1-hour model family:

| Component | Detail |
|---|---|
| Market universe | Current-hour `KXBTCD` BTC above/below markets |
| Fair value | 7-day empirical BTC move cache plus lognormal/BRTI dampening |
| Live spot | Kraken websocket preferred; Coinbase cache remains for history/fallback |
| Execution | Kalshi V2 FOK event orders, real orderbook, event locks, visible-depth checks |
| Main concern | Backtests were often optimistic versus actual live fills/settlements |

Historical corrected bid/ask backtest from `README.md`:

| Strategy | Trades | PnL | Premium | Return on Premium | Win Rate | Max DD | Read |
|---|---:|---:|---:|---:|---:|---:|---|
| `paper` | 2,532 | -83.39 | 1,362.39 | -6.12% | 50.51% | -84.78 | Reject |
| `research` | 173 | +6.25 | 118.75 | +5.26% | 72.25% | -5.93 | Modest, validation negative |
| `js_robust` | 160 | +5.79 | 114.21 | +5.07% | 75.00% | -4.40 | Similar |
| `js_guarded` | 120 | +7.56 | 85.44 | +8.85% | 77.50% | -1.98 | Best old replay, paper-only |

Important read: do not promote a 1-hour variant from historical results alone.
The live websocket capture and actual trade ledger are the final gate.

## Data Inventory and Trust Levels

### Highest Trust: Live Websocket Capture

BTC15M live capture:

```text
~/.btc_kalshi_bot/btc15m_live_capture.duckdb
```

Snapshot at generation time:

| Table | Rows | Time Range |
|---|---:|---|
| `capture_health` | 5,002 | `2026-05-12T10:42:44Z` -> `2026-05-14T05:34:17Z` |
| `coinbase_ticker` | 45,891 | `2026-05-12T10:42:45Z` -> `2026-05-14T05:34:35Z` |
| `order_decision` | 177 | `2026-05-12T20:55:51Z` -> `2026-05-14T03:25:09Z` |
| `signal_scan` | 1,782,628 | `2026-05-12T20:22:34Z` -> `2026-05-14T05:34:38Z` |
| `ws_orderbook_top` | 3,362,163 | `2026-05-12T10:42:45Z` -> `2026-05-14T05:34:38Z` |

BTC 1-hour live capture:

```text
~/.btc_kalshi_bot/research_live_capture.duckdb
```

Snapshot at generation time:

| Table | Rows | Time Range |
|---|---:|---|
| `capture_health` | 21,198 | `2026-05-06T01:38:56Z` -> `2026-05-13T11:09:43Z` |
| `coinbase_ticker` | 1,427,206 | `2026-05-06T01:07:09Z` -> `2026-05-13T11:09:52Z` |
| `order_decision` | 763 | `2026-05-06T04:51:57Z` -> `2026-05-13T10:50:23Z` |
| `signal_scan` | 5,642,660 | `2026-05-06T01:07:08Z` -> `2026-05-13T11:09:22Z` |
| `ws_orderbook_top` | 16,418,617 | `2026-05-06T01:07:08Z` -> `2026-05-13T10:55:00Z` |

Use this data for final validation. It has real websocket orderbook states and
real decision timestamps. It is limited in duration and may be locked while
bots are running.

### Medium Trust: Historical Kalshi API Candles

BTC 1-hour historical datamart:

```text
data/research_datamart/research.duckdb
data/research_datamart/research_backtest.duckdb
```

Report:

| Item | Value |
|---|---:|
| markets | 235,515 |
| quote rows | 4,667,431 |
| quote events | 1,492 |
| BTC rows | 107,737 |
| BTC availability | `2026-02-20T04:01Z` -> `2026-05-06T00:02Z` |
| Notes | Observed bid/ask candles only, no forward fill; Coinbase 1m `available_at = bucket_start + 1m` |

BTC15M historical datamart:

```text
data/btc15m_historical_datamart/crypto_research.duckdb
```

Report:

| Item | Value |
|---|---:|
| series | `KXBTC15M` |
| events | 6,896 |
| markets | 6,896 |
| quote rows | 103,397 |
| spot rows | 115,285 |
| Kalshi cutoff market_settled_ts | `2026-03-13T00:00:00Z` |
| Notes | Observed 1-minute bid/ask candles, no forward fill; recent events use event candlesticks; older events route to historical market candlesticks |

Historical API data is useful for model research and pre-registration. It is
not fully faithful to live trading because:

- Candles are minute-level, not tick/orderbook-delta-level.
- No exact FOK queue position.
- No true live quote-change speed unless reconstructed coarsely.
- Settlement often needs proxying unless official result is fetched and cached.
- It can overstate fills and understate adverse selection.

### Low Trust: CSV Chart Exports

Raw CSVs:

```text
data/kalshi-price-history-kxbtcd-*.csv
```

Use only for rough feature discovery. They are chart/export prices, not real
executable bid/ask orderbook states. Many early optimistic results came from
CSV assumptions and should not be trusted for deployment.

## Backtest Rules That Must Not Be Violated

Use:

```text
docs/live_capture_backtest_rules.md
```

Core rules:

1. Every feature must be computed as-of the decision timestamp.
2. Never use candle high/low for decisions. For historical candles, use candle
   close only when `available_at = candle_end`.
3. Use official Kalshi settlements where possible.
4. Drop or flag near-strike outcomes if only exchange proxy settlement is
   available.
5. Include entry fees and exit fees.
6. For historical candles, cap fill size conservatively unless a live-fitted
   liquidity/fill model says otherwise.
7. Reject isolated candles with missing previous/next minute.
8. Reject wide/unstable spreads.
9. Fit no-fill/FOK failure probability from live data using spread, visible
   quantity, price, TTL, side, and quote-change speed.
10. Promote a strategy only after it survives the live websocket holdout.

## Known Failed or Paused Ideas

| Idea | Current Read |
|---|---|
| Fixed target-win sizing | Caused large live drawdown; do not scale this as-is. |
| TP at 95c/97c/99c or fixed-profit exits | Paused. Too path-dependent on one bad trade in small sample. Needs more websocket data. |
| CSV-only strategies | Overstated edge repeatedly. Research only. |
| Black-box ML gates on small live sample | Did not transfer well; interpretable filters beat shallow ML so far. |
| Removing one-trade-per-event rule | Increased drawdown materially in replay. |
| Assuming `NO = 1 - YES` naively | Wrong for execution; use actual book/opposite side mechanics. |

## Mathematical Problems to Work On

### 1. Fair Probability Model

Formalize the conditional probability:

```text
p_t = P(contract settles YES | information available at time t)
```

Candidate information sets:

- Kalshi top-of-book bid/ask path for this contract.
- Kalshi path for all contracts in the same event.
- BTC spot/perp path from Kraken/Coinbase/Deribit.
- Time-to-close.
- Recent realized volatility.
- Distance to settlement threshold.
- Spread, top-book quantity, quote-change speed.
- Whether the market has already moved in our side's direction.

The desired output is a calibrated `p_t`, not just a directional signal.

### 2. Model Uncertainty Penalty

For small samples, raw `p_hat` is dangerous. We need a shrinkage rule:

```text
p_trade = shrink(p_hat, sample_size, regime_uncertainty, calibration_error)
```

Question: what is the correct conservative transform from estimated probability
to tradable probability?

### 3. Rational Sizing

For contract price `q`, fee `f`, bankroll `B`, and conservative probability
`p_trade`, define size `n`.

Naive Kelly for a binary long with cost `q + f` and win payoff `1 - q - f`
is not enough because model error dominates. We need something like:

```text
n = min(
    visible_top_qty,
    bankroll_risk_cap,
    drawdown_cap,
    fractional_kelly_after_uncertainty,
    max_contracts
)
```

Hard constraints should include:

- Max loss per trade, probably `$1` to `$2` while the model is unproven.
- Max daily loss, e.g. stop trading after `-$5`.
- Max exposure across active positions.
- No target-win sizing at high entries unless probability is calibrated and
  uncertainty-shrunk.

### 4. No-Arbitrage / Cross-Market Structure

Explore mathematical constraints between:

- Adjacent 15-minute contracts.
- 15-minute and 1-hour BTC contracts.
- Same-event contract surface shape.
- YES/NO books and bid/ask consistency.
- Sequential markets immediately after previous resolution.

We suspect there may be structural inefficiencies where Kalshi updates slowly
relative to BTC/perp moves, but we need to prove it out-of-sample.

### 5. Contract Price History

Question: does the Kalshi contract's own price path contain predictive
information beyond BTC price?

Promising direction from previous research:

- The market may underreact when external BTC activity increases but Kalshi has
  not yet repriced the selected side.
- Bad trades often look like late chase entries, where Kalshi already moved in
  our favor before we entered.

Potential variables:

```text
side_chg_1m_c
side_chg_2m_c
event_surface_shift_5m_c
spread_change
top_qty_change
microprice / order imbalance if depth is available
quote update rate
```

### 6. Prove or Disprove the 15-Minute Edge

The 15-minute bot is currently not production-worthy at scale. The minimum
standard for reviving it:

1. Define a probability model.
2. Define a risk rule before looking at the holdout.
3. Fit on historical/API data plus only the allowed training slice of live data.
4. Validate on later live websocket data.
5. Reconcile predicted trades with actual live fills.
6. Show that PnL is not dominated by one or two trades.
7. Show that max drawdown is acceptable under `$100` bankroll.

## Suggested Work Plan

### Phase 1: Reproduce the Current Failure

Start with the live ledger:

```text
~/.btc_kalshi_bot/btc15m_lowdd_live_trades.db
```

Recompute:

- Official result per market from Kalshi API.
- Per-trade fee-adjusted PnL.
- PnL by entry bucket.
- PnL by contracts.
- PnL by `net_edge_cents`.
- PnL by side.
- PnL by time-to-close.
- PnL if capped at 1, 2, 3 contracts.
- PnL if `entry <= 0.60`, `0.65`, `0.70`.

The already-observed critical fact:

```text
Since 2026-05-13T20:31Z:
actual target-win sizing: 13 trades, 5/8, -$27.35
cap 3 contracts:          same 13 trades, approx -$5.63
entry <= 0.60:            7 trades, 3/4, +$2.80
entry <= 0.60 and cap 3:  7 trades, 3/4, +$1.85
```

Do not overfit to this; use it to understand the failure.

### Phase 2: Build a Causal Replay

For the BTC15M capture DB, replay only data available at each timestamp:

```text
~/.btc_kalshi_bot/btc15m_live_capture.duckdb
```

Use tables:

- `ws_orderbook_top`: executable top-of-book states.
- `coinbase_ticker`: live BTC proxy ticks currently written by the capture
  schema; despite the name, the 15m live trader uses Kraken websocket spot in
  the running script.
- `signal_scan`: what the bot evaluated and why it rejected/accepted.
- `order_decision`: attempted fills/not-fills.
- `capture_health`: stale feed/gaps/queue status.

The replay should simulate:

- Per-event current subscription.
- As-of BTC spot and Kalshi book states.
- FOK fill at visible top of book.
- No-fill probability or strict visible-quantity cap.
- One-trade-per-event unless explicitly testing otherwise.
- Official settlement.

### Phase 3: Fit a Calibrated Probability Model

Do not start with neural nets. Start with robust, interpretable models:

- Logistic regression with monotone/intuitive features.
- Isotonic or beta calibration.
- Bayesian/binomial shrinkage by regime.
- Separate calibration for high-entry and low-entry regimes.
- Separate calibration for YES and NO if needed.

Target variable:

```text
1 if side wins after fees enough to justify entry, else 0
```

But for EV, model the actual settlement side first, then apply price/fee.

### Phase 4: Sizing and Risk

A strategy should pass both:

```text
EV_after_fees > 0
drawdown_under_$100_bankroll_is_survivable
```

Recommended initial live sizing constraints:

```text
max_contracts <= 3
max_loss_per_trade <= $1-$2
max_daily_loss <= $5
entry <= 0.60 unless calibration proves high entries work
no target-win sizing until probability is calibrated
```

### Phase 5: Out-of-Sample Promotion Gate

A candidate is not deployable unless:

- It was specified before testing the final live holdout.
- It wins on historical/API data under conservative fill assumptions.
- It wins on live websocket capture.
- It does not depend on one trade.
- It has worst-case loss bounded by design.
- It matches actual live ledger behavior when replaying periods where the bot
  was running.

## Useful Commands

Check BTC/Kalshi Python processes:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'python' -and $_.CommandLine -match 'btc15m|btc_1hr|kalshi' } |
  Select-Object ProcessId, CreationDate, CommandLine |
  Format-List
```

Watch current BTC15M live log:

```powershell
Get-Content logs\btc15m_lowdd_live_20260513_221541.out.log -Wait -Tail 100
```

Inspect BTC15M trade ledger schema:

```powershell
@'
import sqlite3, pathlib
p = pathlib.Path.home() / ".btc_kalshi_bot" / "btc15m_lowdd_live_trades.db"
con = sqlite3.connect(p)
print(con.execute("select name from sqlite_master where type='table'").fetchall())
print(con.execute("pragma table_info(research_live_trades)").fetchall())
'@ | python -
```

List live capture tables:

```powershell
@'
import duckdb, pathlib
p = pathlib.Path.home() / ".btc_kalshi_bot" / "btc15m_live_capture.duckdb"
con = duckdb.connect(str(p), read_only=True)
print(con.execute("show tables").fetchall())
'@ | python -
```

If the DB is locked by a live writer, copy/snapshot it before analysis rather
than blocking the bot.

## Files to Read First

| Path | Why |
|---|---|
| `README.md` | Current repo map and older strategy scorecards |
| `docs/research_ledger.md` | Detailed research chronology and current warnings |
| `docs/live_capture_backtest_rules.md` | Rules for faithful replay |
| `scripts/btc15m_lowdd_live.py` | Current live 15m bot |
| `scripts/btc15m_live_capture.py` | Capture/event subscription helper |
| `scripts/btc_1hr_research_live.py` | Shared Kalshi/Kraken/API/order logic |
| `scripts/may8examine.py` | Main 1-hour historical research script |
| `backtest_outputs/btc15m_risk_reward_20260513/` | Recent 15m RR replay, exploratory |
| `backtest_outputs/btc15m_take_profit_20260513/` | TP replay, paused due path dependency |

## Research Standard

Do not accept:

- A strategy that only wins on CSV exports.
- A strategy that only wins because of one or two trades.
- A strategy that assumes fills that were not actually visible.
- A strategy that uses any future candle high/low.
- A strategy that scales losses faster than wins.
- A strategy with no explicit drawdown stop under a `$100` bankroll.

Accept only:

- Causal features.
- Official settlement where possible.
- Fees included.
- FOK/fill realism included.
- Live websocket capture as final holdout.
- Sizing that is robust to model error.

The current best high-level hypothesis is:

> There may be a stale-book/underreaction edge when external BTC activity moves
> but Kalshi has not yet repriced the selected contract side. The edge is not
> "chase any Kalshi momentum"; late chase entries are exactly where losses have
> appeared.

Please focus on proving or killing that hypothesis.
