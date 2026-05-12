# BTC 1h Core Model Research Handoff

Generated: 2026-05-11 UTC

This document summarizes the BTC 1-hour Kalshi research work up to the
2026-05-10 core-model sprint. It is intended to let the next researcher pick
up without reconstructing the full chat history.

## Current Bottom Line

The deployed live BTC bot is still the baseline research fair-value model plus
the late-only loss guard and risk-adjusted sizing. The new core-model variants
found promising historical improvements, especially downside semivariance,
stronger BRTI dampening, and fat-tailed Student-t probability models. However,
the captured websocket decision holdout did not justify replacing the live core
model yet.

The best next research direction is not pure sizing. It is a better
event-time fair value model, validated against captured websocket data with no
lookahead. The leading candidate is:

1. Baseline empirical/lognormal fair value.
2. Replace or augment the 60-minute realized-volatility input with downside
   semivariance or fat-tail treatment.
3. Keep the existing late-window NO-loss guard until a larger websocket holdout
   proves a replacement is safer.
4. Replay against captured websocket top-of-book in chronological order before
   deploying.

Do not treat the historical DuckDB candle backtest alone as deployment-grade.
It is useful for model discovery, not final validation.

## Production State At Time Of Handoff

Live BTC process observed:

```text
scripts/btc_1hr_late_only_loss_guard_live.py
```

The wrapper starts the same websocket execution engine as
`scripts/btc_1hr_research_live.py` with these strategy settings:

```text
BTC_1HR_SIGNAL_STRATEGY=research
BTC_1HR_SIZING_POLICY=risk_adjusted
BTC_1HR_MIN_TTL_MIN=5
BTC_1HR_MAX_TTL_MIN=20
BTC_1HR_MAX_CONTRACTS_PER_TRADE=5
BTC_1HR_RISK_BASE_MAX_CONTRACTS=5
BTC_1HR_RISK_NO_SIDE_CONTRACT_CAP=5
BTC_1HR_RISK_BASE_NO_SIDE_CONTRACT_CAP=5
BTC_1HR_MIN_NO_SIDE_PROB=0.72
```

The live engine uses Kalshi websocket market data, private order events, and
live BTC spot/candle refresh. The BTC path currently uses Coinbase candles with
Kraken fallback/websocket support in the live infrastructure. The core-model
research did not modify the live bot.

The live data capture database is:

```text
C:/Users/ahmed/.btc_kalshi_bot/research_live_capture.duckdb
```

The merged gapless copy used for research snapshots is:

```text
data/live_capture_gapless/live_capture_gapless.duckdb
```

## Important Vocabulary

### Historical candle replay

This uses Kalshi historical/corrected bid/ask candles and BTC minute bars from
`data/research_datamart/research.duckdb`. It can scan many days efficiently,
but it does not exactly reproduce websocket timing, queue state, exchange
latency, top-of-book volume decay, or every mid-minute book change.

Use it for broad hypothesis discovery.

### Captured websocket replay

This uses the orderbook, signal, BTC, and execution capture tables written by
our live websocket scripts. This is the highest-quality data source because it
records the stream the bot actually saw.

Use it for final validation. It is still only valid during periods where the
capture was connected and not dropping data.

### Captured decision holdout

This is not a full replay over every possible websocket tick. It is a stricter
audit of already-captured bot decisions. The script recomputes a candidate
model's probability at decision timestamps that were actually observed by a
running live or shadow bot, then asks whether the candidate would still have
passed that same executable opportunity.

Use it as a fast sanity check after historical model selection.

### Live-only decision

`live-only decision` is not a separate raw dataset. It is a slice of the
captured decision holdout. In `scripts/core_model_decision_holdout.py`,
`live_only` means rows where:

```text
capture == "research_live_capture"
```

That excludes multi-strategy shadow and paper decisions. It is smaller than the
combined decision sample, but closer to what the production bot actually did.

The four slices in the decision holdout summary mean:

```text
combined_decisions  = all settled captured decisions from live + shadow inputs
live_only           = only production live-research captured decisions
passed_combined     = combined rows where the candidate model would pass
passed_live_only    = live-only rows where the candidate model would pass
```

## Primary Data Inventory

### Historical research datamart

Path:

```text
data/research_datamart/research.duckdb
```

Observed table counts:

```text
btc_1m                 107,737 rows
kalshi_csv_prices    2,193,773 rows
kalshi_markets         235,515 rows
kalshi_quotes        4,667,431 rows
v_btc_available        107,737 rows
v_research_universe  4,538,666 rows
```

Important tables:

```text
btc_1m
  bucket_start, available_at, open, high, low, close, volume,
  log_ret, rv_15m, rv_60m, rv_1d, rkurt_60m

kalshi_quotes
  market_ticker, event_ticker, ts_end, available_at,
  yes_bid/ask OHLC, yes_ask_exe, no_ask_exe, spread_cents,
  price_mean, volume, open_interest, source, fidelity

kalshi_markets
  market_ticker, series_ticker, event_ticker, open_time, close_time,
  floor_strike, cap_strike, status, event_open_time,
  is_cumulative, is_hourly_kxbtcd
```

The core-model sprint only loaded the relevant late-window candidate universe:

```text
quotes loaded: 15,492
events loaded: 1,459
BTC bars:      107,737
variants:      60
```

The candidate quote filter was:

```text
market is hourly KXBTCD
market is cumulative
5 <= TTL minutes <= 20
spread_cents <= 2
yes_ask_exe or no_ask_exe in [0.25, 0.75]
ordered by close_time, available_at, market_ticker
```

### Captured websocket datamart

Path:

```text
data/live_capture_gapless/live_capture_gapless.duckdb
```

Observed table counts:

```text
capture_health_all             26,834 rows
coinbase_ticker_all         2,732,451 rows
event_coverage                    221 rows
order_decision_all                562 rows
order_decision_dedup              562 rows
signal_scan_all             9,460,993 rows
signal_scan_dedup           9,455,041 rows
source_manifest                     4 rows
ws_control_all                  1,554 rows
ws_lifecycle_all            1,350,901 rows
ws_orderbook_delta_all        913,574 rows
ws_orderbook_snapshot_level_all 34,031 rows
ws_orderbook_top_all       28,509,269 rows
ws_orderbook_top_dedup     27,245,906 rows
ws_orderbook_top_gaps          40,569 rows
ws_private_event_all              666 rows
```

This is the best source for future full websocket replay. The important
research constraint is that replay must respect the actual `received_at_utc`
order. Coinbase/Kraken spot updates and Kalshi book deltas must not be merged
into a single simultaneous bar that gives the model information it did not have
yet.

## Current Baseline Fair-Value Model

The current baseline probability path lives in:

```text
scripts/btc_1hr_research_live.py
scripts/backtest_research_duckdb.py
scripts/core_model_research.py
```

Conceptually it estimates:

```text
p_yes = P(BRTI expiration value > strike | current spot, TTL, recent BTC state)
```

The current deployed research model is:

```text
70% empirical probability + 30% lognormal probability
BRTI dampening = 0.80
volatility input = rv_60m
empirical lookback = 7 days before event open
```

The empirical component:

1. Uses recent BTC log returns from the lookback window.
2. Demeans historical moves.
3. Finds historical horizons close to the current TTL.
4. Conditions on nearby realized volatility samples.
5. Freezes the event empirical cache at event open to avoid using returns from
   inside the market being traded.

The lognormal component:

1. Uses the current spot.
2. Uses the selected realized-volatility estimate.
3. Scales by the remaining TTL.
4. Computes a parametric probability of finishing above the strike.

Execution filters then require enough net edge after fee estimate, sensible
price bands, spread limits, and side-probability gates.

## Fee And PnL Convention

For one contract:

```text
premium paid = entry_price
fee          = kalshi_fee_dollars(entry_price, contracts=1, liquidity="taker")
win pnl      = 1 - entry_price - fee
loss pnl     = -entry_price - fee
```

All reported PnL in the core-model research is one-contract PnL unless
otherwise stated. `premium` is total entry premium, not bankroll. `ROP` is:

```text
pnl / premium
```

This is not the same as return on full account cash. A strategy that makes
`+2.0` one-contract PnL on `20.0` premium has `10% ROP`, but on a `$100`
account it is `+2%` if only one contract was traded each time.

## Leakage Rules Used

The backtest rules that matter most:

1. BTC bars are joined by `available_at`, not by close time alone.
2. The empirical cache is built using data before the traded event opens.
3. Historical model selection uses train/validation/test by event time.
4. Websocket capture is held out from historical model ranking.
5. Official Kalshi settlement should be preferred for final scoring.
6. Historical settlement from BTC minute bars is a proxy and must be treated as
   weaker than official settlement.
7. Captured websocket replay must process rows in `received_at_utc` order.
8. Do not use quote updates, BTC ticks, or official results that arrived after
   the simulated decision time.
9. Do not tune on websocket holdout and then call it holdout again.

Relevant rules doc:

```text
docs/live_capture_backtest_rules.md
```

## Literature And Hypothesis Inputs

The research desk reviewed these modeling directions:

1. HAR-RV volatility forecasting.
2. Log-realized-volatility regressions.
3. HARQ/noise-aware realized volatility.
4. Two-scale realized volatility and microstructure-noise correction.
5. Intraday volatility seasonality.
6. Jump-robust realized measures.
7. Upside/downside semivariance.
8. Jump clustering and tail risk.
9. GARCH/EGARCH/GJR volatility.
10. Realized GARCH.
11. Regime-conditioned volatility.
12. Range-based volatility.
13. Unexpected volume/liquidity state.
14. Kalshi orderbook shape and market-implied probability.
15. Cross-market spillovers.
16. Option-implied distribution where available.
17. Probability calibration.
18. Fat-tailed distributions.
19. Momentum/reversal drift.
20. BRTI/reference-index dampening.

Useful references:

```text
Corsi HAR-RV:
https://academic.oup.com/jfec/article-pdf/7/2/174/2543795/nbp001.pdf

Andersen et al. realized volatility:
https://www.nber.org/papers/w8160

Zhang, Mykland, Ait-Sahalia two-scale RV:
https://www.nber.org/papers/w11380

Barndorff-Nielsen and Shephard realized power/jumps:
https://public.econ.duke.edu/~get/browse/courses/883/Spr16/COURSE-MATERIALS/Z_Papers/BNSJFEC2004.pdf

Katsiampa Bitcoin volatility:
https://shura.shu.ac.uk/16526/1/Katsiampa-VolatilityEstimationforBitcoin%28AM%29.pdf

Chu et al. crypto GARCH family comparisons:
https://www.mdpi.com/1911-8074/10/4/17
```

## Research Harness

Main script:

```text
scripts/core_model_research.py
```

Decision holdout script:

```text
scripts/core_model_decision_holdout.py
```

Primary output directory:

```text
backtest_outputs/core_model_research_20260510_calibrated
```

Important outputs:

```text
core_model_report.md
core_model_scorecard.csv
core_model_summary.csv
core_model_trades.csv
core_model_variants.json
core_model_ws_decision_holdout_rows.csv
core_model_ws_decision_holdout_summary.csv
core_model_captured_holdout_note.csv
```

The calibrated run printed:

```text
loaded quotes=15,492 events=1,459 btc=107,737 variants=60
calibrators=['base_no_market', 'base_with_market']
trades=2,825
```

The historical split was:

```text
train:      entry before 2026-04-01 00:00:00 UTC
validation: 2026-04-01 00:00:00 UTC through before 2026-04-21 00:00:00 UTC
test:       2026-04-21 00:00:00 UTC onward in research.duckdb
```

## Feature Engineering In Core Model Sprint

Additional BTC features created in `scripts/core_model_research.py`:

```text
rolling RV:       5m, 10m, 15m, 30m, 60m, 120m, 240m, 720m, 1440m
EWMA RV:          15m, 30m, 60m, 120m, 240m, 720m
semivariance:     upside/downside 30m, 60m, 240m
momentum:         log_ret_5m, log_ret_10m, log_ret_30m, log_ret_60m
HAR static:       sqrt(0.50 * rv_60m^2 + 0.30 * rv_240m^2 + 0.20 * rv_1d^2)
short/long ratio: rv_60m / rv_1d
HAR fit:          train-only linear regression on log future one-hour variance
```

The train-only HAR fit used 55,898 train rows and these features:

```text
rv_15m
rv_60m
rv_240m
rv_1d
rv_down_60m
rv_up_60m
rv_short_long_ratio
```

Fitted coefficients:

```text
intercept: -0.2588963003
rv_15m:     0.3804286403
rv_60m:    -0.1932266709
rv_240m:    0.2746455244
rv_1d:      0.0475265867
rv_down:    0.6832272603
rv_up:      0.5078354501
ratio:     -0.2407532576
```

## Variant Families Tested

The sprint tested 60 variants across three hypothesis rounds:

1. Baseline 70/30 empirical/lognormal.
2. Pure empirical and pure lognormal baselines.
3. Blend weights 85/15, 50/50, 30/70.
4. Different realized-volatility windows: 15m, 30m, 60m, 120m, 240m, 1d.
5. EWMA realized volatility.
6. HAR static and train-fit HAR volatility.
7. Volatility scale factors: 0.85, 1.15, 1.30.
8. BRTI dampening values: 0.65 and 1.00 against the baseline 0.80.
9. Fat-tailed Student-t innovations, df=3, 5, 8.
10. Momentum and reversal drift from 5m, 10m, 30m, 60m returns.
11. Event-implied volatility from Kalshi surface shape.
12. Market-implied probability shrink.
13. Upside and downside semivariance.
14. Empirical lookback windows of 3 and 14 days.
15. Higher and lower uncertainty surcharges.
16. Train-only Platt/logistic calibrators, with and without market price.
17. Side-bias adjustments.
18. Adaptive high/low-volatility variants.
19. Short empirical plus market shrink variants.
20. Calibration plus uncertainty variants.

## Historical Core-Model Results

Historical results are one-contract, fee-included, late-window results from the
historical DuckDB candidate universe. They are sorted by the internal score,
which rewards validation/test PnL and penalizes drawdown.

```text
variant                    train           validation      test           all            test win%  test maxDD  score
rv_down60_blend            28 / +0.81      13 / +1.50     24 / +5.40     65 / +7.71     87.5%      -0.65       5.1975
brti_065                   36 / +0.55      29 / +1.75     41 / +5.78    106 / +8.08     80.5%      -1.34       5.1525
student_t3_rv60            25 / +2.38      20 / +2.10     35 / +4.92     80 / +9.40     80.0%      -1.69       4.8800
blend_85_15_rv60           36 / +3.45      21 / +2.15     31 / +3.61     88 / +9.21     77.4%      -1.63       4.1975
cal_no_market_low_uncert   14 / +1.11      16 / +2.41     31 / +3.28     61 / +6.80     74.2%      -1.34       4.1325
vol_scale_085              29 / +1.58      21 / +0.74     36 / +4.79     86 / +7.11     80.6%      -1.27       3.6175
rv_up60_blend              30 / +2.47      23 / +1.91     28 / +3.25     81 / +7.63     78.6%      -1.79       3.6100
lower_uncertainty          19 / +0.55      10 / +1.92     21 / +2.62     50 / +5.09     76.2%      -1.47       3.3400
ewma60_logn_blend           7 / +0.01       4 / +1.27     10 / +2.82     21 / +4.10     90.0%      -0.53       3.2525
higher_uncertainty         12 / -0.59       5 / +1.50     11 / +2.51     28 / +3.42     81.8%      -0.53       3.2500
baseline_emp70_logn_rv60   13 / -0.27       8 / +1.36     17 / +2.24     38 / +3.33     76.5%      -1.19       2.5650
```

Interpretation:

1. Downside semivariance was the cleanest historical improvement. It performed
   in train, validation, and test, and had the best test drawdown.
2. Stronger BRTI dampening increased trade count and total PnL but with larger
   drawdown.
3. Fat-tailed Student-t was more balanced than the baseline and had consistent
   win rate across splits.
4. The baseline was not broken historically, but it was weaker and more sparse.
5. Several variants looked excellent only because they were sparse. Sparse
   variants cannot be trusted without more websocket data.

## By-Side Notes

Historical by-side behavior for the key variants:

```text
rv_down60_blend:
  train      NO +1.67, YES -0.86
  validation NO +0.19, YES +1.31
  test       NO +4.02, YES +1.38

brti_065:
  train      NO +0.72, YES -0.17
  validation NO +1.10, YES +0.65
  test       NO +4.01, YES +1.77

student_t3_rv60:
  train      NO +1.73, YES +0.65
  validation NO +1.56, YES +0.54
  test       NO +3.66, YES +1.26

baseline_emp70_logn_rv60:
  train      NO +0.07, YES -0.34
  validation NO +1.07, YES +0.29
  test       NO +2.63, YES -0.39
```

The baseline's historical YES side was not robust. Better core-model variants
mostly improved NO trades and made YES less harmful, but this still needs
websocket replay confirmation.

## Captured Websocket Decision Holdout

Decision-holdout input:

```text
backtest_outputs/loss_prevention_research_20260510/holdout_late_only_features.csv
```

The decision holdout is a settled captured-decision audit, not a full
counterfactual replay. It uses decisions the bots actually saw and recomputes
candidate probabilities at those decision timestamps.

Current explicit NO-loss guard result on this holdout:

```text
captured current no_p72 guard:
  combined decisions: 17 trades, +6.13 PnL, 56.4% ROP, 100.0% win rate
  live-only decisions: 10 trades, +3.70 PnL, 58.7% ROP, 100.0% win rate
```

Finalist core-model decision re-score:

```text
variant                    slice             trades  pnl    ROP       win%    maxDD
rv_down60_blend            passed_combined      21   +2.48  +19.8%    71.4%   -1.67
rv_down60_blend            passed_live_only     13   +0.53   +7.1%    61.5%   -1.19
brti_065                   passed_combined      25   +2.10  +14.1%    68.0%   -1.67
brti_065                   passed_live_only     16   +0.69   +7.4%    62.5%   -1.19
student_t3_rv60            passed_combined      25   +2.10  +14.1%    68.0%   -1.67
student_t3_rv60            passed_live_only     16   +0.69   +7.4%    62.5%   -1.19
blend_85_15_rv60           passed_combined      19   +1.68  +14.8%    68.4%   -2.06
blend_85_15_rv60           passed_live_only     12   +0.14   +2.0%    58.3%   -1.58
baseline_emp70_logn_rv60   passed_combined      17   +0.73   +7.1%    64.7%   -2.50
baseline_emp70_logn_rv60   passed_live_only     10   -0.76  -13.2%    50.0%   -2.02
```

Conclusion from this holdout:

The core-model variants are promising research candidates, but none beat the
current explicit NO-loss guard on the captured decision holdout. The correct
next action is full websocket replay or forward shadow testing, not immediate
live replacement.

## May 10 Live Loss After Guard Deployment

The live late-only loss-guard bot took this trade:

```text
local log time: 2026-05-10 18:54:26 America/Denver
UTC time:       2026-05-11 00:54:26 UTC
event:          KXBTCD-26MAY1021
market:         KXBTCD-26MAY1021-T81399.99
side:           NO
contracts:      1
entry:          0.70
fee:            0.02
model_p_yes:    0.1318776642
model_p_no:     0.8681223358
net_edge:       14.812c
spread:         1.0c
btc_spot:       81305.40
close:          2026-05-11 01:00:00 UTC
```

Kalshi official result:

```text
expiration_value: 81454.94
result:           yes
status:           finalized
```

PnL:

```text
loss = -0.70 - 0.02 = -0.72
```

Why it lost:

1. The market was a `YES if above 81399.99` market.
2. The bot bought `NO`, so it needed the official BRTI average to finish below
   81400.
3. At decision time BTC spot used by the bot was about 81305.40, roughly 94.6
   dollars below the strike.
4. In the final minutes Coinbase BTC moved from the low 81300s to above 81400.
5. Kalshi settles on the 60-second CF Benchmarks BRTI average before the hour,
   not Coinbase. The official settlement value was 81454.94, so `YES` won.

The previous same-hour shadow trade on `T81599.99 NO` won because 81454.94 is
below 81600. The live trade on `T81399.99 NO` lost because 81454.94 is above
81400. These are different strikes in the same event.

This loss does not prove the guard is broken, but it does prove the heldout
`100% win rate` was a small-sample validation result, not a guarantee. The
risk-adjusted sizing did its job here: because the entry was high-priced at
70c, the price-band cap limited the trade to one contract instead of five.

Potential follow-up hypothesis from this loss:

```text
Late high-priced NO trades near the strike still carry jump/crossing risk.
Require a minimum distance-to-strike in volatility units, or require stronger
market-implied confirmation when p_side is high but distance is small.
```

Do not add this as a production rule from one loss. Test it first on historical
data and then on captured websocket replay.

## Known Weaknesses In The Research Stack

1. Historical Kalshi candles are not the same as tick-level executable top of
   book.
2. Historical BTC minute close is not the same as CF Benchmarks BRTI.
3. The historical Kalshi endpoint/corrected data can have coverage boundaries
   and gaps.
4. Captured websocket data is only trustworthy during connected, non-dropping
   intervals.
5. Decision holdout is not full counterfactual replay.
6. Sparse strategies can look very good by chance.
7. It is easy to overfit the late-window rules because the sample is small.
8. YES and NO behavior differ; aggregate PnL can hide side-specific weakness.
9. Market-implied shrink/calibration can leak optimism if fit outside train.
10. Any model that uses final-hour BTC movement inside the event must use only
    data available at the decision timestamp.

## Recommended Future Research Plan

### Step 1: Build full websocket replay

Use `data/live_capture_gapless/live_capture_gapless.duckdb` and process:

```text
ws_orderbook_snapshot_level_all
ws_orderbook_delta_all
ws_orderbook_top_dedup
coinbase_ticker_all or kraken equivalent captures
signal_scan_dedup
order_decision_dedup
ws_private_event_all
```

Replay all rows in `received_at_utc` order. Maintain the exact book state and
BTC state the bot would have known at each moment. Only evaluate signals after
the relevant update has arrived. Do not align BTC and Kalshi by minute bars in
the final validation layer.

### Step 2: Validate core-model finalists

Finalists to replay first:

```text
baseline_emp70_logn_rv60 + current no_p72 guard
rv_down60_blend + current no_p72 guard
brti_065 + current no_p72 guard
student_t3_rv60 + current no_p72 guard
blend_85_15_rv60 + current no_p72 guard
```

### Step 3: Test loss-prevention hypotheses without touching production

Candidate filters:

```text
distance_to_strike / expected_move
distance_to_strike / downside_semivariance_move
side-specific NO guard above/below 65c entry
BRTI-reference divergence guard
late jump/momentum guard
book-implied reversal guard
spread/available-depth stability guard
do-not-chase if signal appears only after sudden book repricing
```

### Step 4: Use train/validation/test discipline

Recommended split:

```text
Historical train:      before 2026-04-01
Historical validation: 2026-04-01 to before 2026-04-21
Historical test:       2026-04-21 onward
Websocket holdout:     never use for initial feature selection
Forward shadow:        required before live deployment
```

For websocket data, use chronological blocked splits by day. Do not randomly
split ticks or decisions, because adjacent ticks in the same event are highly
dependent.

### Step 5: Report both account and premium returns

Always report:

```text
trades
contracts
gross premium
fees
net PnL
return on premium
return on $100 bankroll
win rate
max drawdown in dollars
max drawdown as percent of bankroll
YES/NO split
entry price buckets
TTL buckets
distance-to-strike buckets
```

## Reproduction Commands

Historical core-model sprint:

```powershell
python scripts\core_model_research.py --db data\research_datamart\research.duckdb --output-dir backtest_outputs\core_model_research_20260510_calibrated --rounds 3 --progress-every-events 50
```

Captured decision holdout:

```powershell
python scripts\core_model_decision_holdout.py --holdout backtest_outputs\loss_prevention_research_20260510\holdout_late_only_features.csv --btc data\btc_1m_research_live_cache.parquet --output-dir backtest_outputs\core_model_research_20260510_calibrated
```

Watch current live BTC bot log:

```powershell
Get-Content logs\late_loss_guard_live_20260510_162038.out.log -Wait -Tail 100
```

## Final Research Judgment

The alpha is not yet proven to scale simply by increasing contract count. The
best evidence so far is that the baseline research model plus strict late
NO-loss guard has a useful live-captured edge, but the sample is still small
and a May 10 out-of-sample loss showed remaining crossing risk.

The most promising core-model improvements are:

1. Downside semivariance volatility (`rv_down60_blend`).
2. Stronger BRTI/reference dampening (`brti_065`).
3. Fat-tailed Student-t fair value (`student_t3_rv60`).

The correct next step is to test these as overlays on the current guard using a
full captured-websocket replay, then run the best one in shadow before any live
replacement.

## May 11 Active Research Loop Update

This section records the active research iteration run on 2026-05-11 after the
May 10 core-model handoff.

### Data used

```text
Historical discovery:
backtest_outputs/live_shadow_accuracy_20260510/historical_may8_variants/may8examine_trades.csv

Captured websocket holdout:
backtest_outputs/loss_prevention_research_20260510/holdout_late_only_features.csv

Recent official live ledger:
C:/Users/ahmed/.btc_kalshi_bot/research_live_trades.db

BTC causal feature cache:
data/btc_1m_research_live_cache.parquet
```

All final checks used official Kalshi settlement labels where available. The
captured holdout was treated only as a final gate; thresholds were not selected
from it.

### Hard NO filters

Script:

```text
scripts/research_no_fair_value_loop.py
```

Output:

```text
backtest_outputs/no_fair_value_research_20260511/
```

Result: `922` hard NO filter candidates, `0` promotion-gate passes. Hard
filters that removed the May 11 NO losses also removed too much validated
historical edge. Do not deploy a hard NO block from this run.

### NO sizing throttles

Script:

```text
scripts/research_no_sizing_throttle_loop.py
```

Output:

```text
backtest_outputs/no_sizing_throttle_research_20260511_after_patch/
```

Best operational risk-control candidate:

```text
NO side_distance_usd < 115 => max 1 contract
```

Compared with current cap-5 sizing on the same entry stream:

```text
historical_validation delta: +0.32
historical_test delta:       +0.38
live_capture_holdout delta: +11.13
recent actual ledger delta:  +2.88
recent actual ledger PnL:    -4.21 -> -1.33
```

This is a risk-control improvement, not new alpha. It keeps the same signal and
reduces damage from close-to-strike NO sizing.

### Core model observed-decision gate

Script:

```text
scripts/core_model_observed_decision_gate.py
```

Output:

```text
backtest_outputs/core_model_observed_gate_20260511/
```

Key result after applying the live `p_no >= 0.72` guard:

```text
variant              historical test with cap5   strict captured holdout   recent ledger
rv_down60_blend      +5.09 on $100, DD -2.64     +3.75, 92.3% win, DD -0.54  passed 1/5, +0.31
brti_065             +7.64 on $100, DD -3.42     +5.35, 94.1% win, DD -0.54  passed 3/5, -0.12
lower_uncertainty    +2.67 on $100, DD -3.01     +3.75, 92.3% win, DD -0.54  passed 3/5, -0.12
```

Read: `rv_down60_blend` remains the best core-model research candidate, but it
is not promoted live from observed rows alone. It rejects too many recent ledger
rows and slightly worsens the broad captured holdout when evaluated as a pure
observed-decision gate. Keep it as a shadow/core-model research candidate until
a full captured-websocket counterfactual replay validates it.

### Deployment from this iteration

The live BTC wrapper now keeps the baseline research signal and adds only the
tested NO sizing throttle:

```text
scripts/btc_1hr_late_only_loss_guard_live.py
BTC_1HR_MIN_NO_SIDE_PROB=0.72
BTC_1HR_RISK_NO_NEAR_DISTANCE_USD=115
BTC_1HR_RISK_NO_NEAR_DISTANCE_CONTRACT_CAP=1
```

The startup log should show:

```text
no_min_side_prob=0.72 no_near_cap=$115/1
```

This change does not alter the fair-value model or entry signal; it caps size
on close-to-strike NO trades.
